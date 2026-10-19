"""
Snowflake MIS — TNA REPORT (mounted at /sf-tna).

V2RETAIL.BRONZE.SRM_TNA_REPORT, the SRM portal's Time-and-Action sheet: one row per PENDING PO
LINE (po x article x store), carrying the PO's own attributes, where it has got to, and the
BUDGETED and ACTUAL date of each of the 37 production / DC stages behind it.

    PO_NUMBER | ARTICLE_NUMBER | STORE_CODE || CUTTING BUD  CUTTING ACT  CUTTING STATUS | ...

BUILT LIKE THE DAY WISE TREND, and deliberately: a grain you pick, a second axis you pick, value
filters per dimension, and the same PickList panels on the page. What changes is the second axis.
There it is DAYS; here it is STAGES, one block of columns each, in the order the goods actually
move through them - so the sheet reads left to right as the garment's own life.

THE EXTRACT CARRIES ITS OWN HIERARCHY, AND THE TWO MASTERS ARE JOINED BESIDE IT. SEGMENT /
DIVISION / SUB_DIVISION / MAJOR_CATEGORY, the vendor and the article description are all stamped
on the PO by SRM. The MIS store master (on STORE_CODE) and VW_PRODUCT_MASTER (on ARTICLE_NUMBER)
are joined in as well, so a line can be grouped by the store's zone or the article's MVGR - the
same two masters, joined the same 1:1 way, that every other MIS page enriches from.

THEY ARE NOT THE SAME COLUMNS, WHICH IS WHY THE MASTER ONES ARE PREFIXED. SRM's stamp is what the
PO was raised under; the master is what SAP says today, and they disagree - measured 18-Sep-2026:
DIVISION differs on 77 of 30,940 lines and the major category on 2,379 (7.7%). So the extract's own
columns keep their names and the joined ones carry SM_ / PM_, and a sheet showing DIVISION beside
PM_DIV shows the disagreement rather than hiding it behind one name.

STORE_CODE IS THE RECEIVING PLANT, NOT A SHOP, and the store master says so once it is joined:
measured 18-Sep-2026, 27,008 of 30,940 lines (87%) name one of FOUR DCs, and only 3,915 land on the
303 actual stores. So grouping by SM_ZONE or SM_STATE puts most of a TNA report in one 'NA' bucket
- correctly. SM_ST_TYP is in the list for exactly this reason.

BOTH JOINS ARE COMPLETE AND 1:1. Measured 18-Sep-2026: all 309 store codes are in the MIS store
master, and all 16,271 article numbers parse and are in the product master - so nothing here goes
blank for want of a master row. Both lookups QUALIFY to ONE row per key, so neither can fan a
PEND_PO_QTY out; they are LEFT joins so a future unmatched line loses its master columns and keeps
its own. One master column is worth naming: PM_VENDOR_CODE is blank on 30,303 of 30,940 lines, and
on the 637 where both exist it NEVER equals the PO's vendor - the article's vendor is not the
vendor this PO was placed with. Group by VENDOR_NAME, not PM_VENDOR_NAME.

THE DEFAULT GRAIN IS THE LINE ITSELF (PO_NUMBER x ARTICLE_NUMBER x STORE_CODE), which is what makes
this page a report and not only a pivot: every aggregate below collapses to the row's own value at
that grain, so the default sheet is `SELECT *` of the picked columns. Group by something above it -
vendor, category, current status - and the SAME columns become the aggregate named in the header:

    BUD      MIN of the stage's budgeted date      the earliest the group was due
    ACT      MAX of the stage's actual date        the last one to clear it
    STATUS   the value when the group agrees, else MIXED
    DELAY    AVG days from budgeted to actual, or to TODAY while it is still open
    DONE     lines with an actual date
    PEND     lines without one

Nothing is averaged silently: DELAY and the ageing measures are averages BY NAME, the money and
the quantity are sums, and MAX_STAGE_DELAY is a max - so a grouped row cannot be mistaken for a
line.

THE TEXT DATES ARE PARSED, NOT PRINTED. DOC_DT, DELIVERY_DATE, BUDGETED_DATE, READY_PLANNED and
CRNT_SAP_DEL_DT are TEXT in the extract, in DD.MM.YYYY. Sorting or ranging on that text is
alphabetical and wrong ('01.01.2027' sorts before '31.12.2025'), so every one of them is read
through TRY_TO_DATE(c,'DD.MM.YYYY') and published as YYYY-MM-DD. Measured 18-Sep-2026: all 30,940
rows parse, and DELIVERY_DATE / CRNT_SAP_DEL_DT are blank on 11,978 of them - blank in the source,
not lost here.

DELIVERY_DATE IS NOT ALWAYS THE ONE TO RANGE ON, which is why the date basis is picked too: a
sourcing question ranges on DOC_DT (when the PO was raised), a delivery question on DELIVERY_DATE
or BUDGETED_DATE. Leave the dates empty and nothing is ranged at all - the table is 30,940 rows,
so the whole book is a legitimate first screen here in a way it never is on the day-wise facts.
"""
import io
import logging
import re
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from . import sf_var_combined as base

logger = logging.getLogger(__name__)
router = APIRouter()

TBL = "V2RETAIL.BRONZE.SRM_TNA_REPORT"

# ── THE DIMENSIONS, grouped the way the picker draws them ─────────────────────────────────────
#
# Every one is a plain column of the extract. TEXT_DATES are the five DD.MM.YYYY columns; they are
# dimensions like any other, but their SQL goes through TRY_TO_DATE so they group, sort and filter
# as dates. DATE_DIMS are already DATE in the table and only need formatting.
TEXT_DATES = {"DOC_DT", "DELIVERY_DATE", "BUDGETED_DATE", "READY_PLANNED", "CRNT_SAP_DEL_DT"}
DATE_DIMS = {"C_STAT_DATE"}
BOOL_DIMS = {"PORTAL_CONTROLLED", "IS_STALE"}

EXTRACT_GROUPS = [
    ("PO", ["PO_NUMBER", "PO_TYPE", "DOC_DT", "ZPO_PROCESS", "ZPO_ROUTING",
            "PORTAL_CONTROLLED", "IS_STALE"]),
    ("Article", ["ARTICLE_NUMBER", "GEN_ART_DESC", "VENDOR_DESIGN_NO", "COLOR_LIST"]),
    ("Hierarchy (SRM)", ["SEGMENT", "DIVISION", "SUB_DIVISION", "MAJOR_CATEGORY"]),
    ("Vendor & site", ["VENDOR_CODE", "VENDOR_NAME", "STORE_CODE", "TA_NAME", "ZONE_ID"]),
    ("Status", ["CRNT_STAT", "PRODUCTION_STATUS", "PP_STATUS", "DELAYED_STAGE", "C_STAT_DATE"]),
    ("Dates", ["READY_PLANNED", "DELIVERY_DATE", "BUDGETED_DATE", "CRNT_SAP_DEL_DT"]),
    ("Logistics", ["LR_NO", "TRANSPORTER_NAME", "VEHICLE_NUMBER", "SAP_PACKING_LIST"]),
]
EXTRACT_DIMS = [c for _g, cols in EXTRACT_GROUPS for c in cols]

# ── THE TWO MASTERS, joined on STORE_CODE and ARTICLE_NUMBER ──────────────────────────────────
#
# The SAME catalogues every other MIS page offers, read from sf_var_combined so a column added to
# a master appears here without an edit - the store master's 24 attributes (sm) and the product
# master's 122, the colour / size rationalisation names included (pm).
#
# EVERY ONE IS PREFIXED, and that is not tidiness. The extract already has a SEGMENT, a DIVISION, a
# SUB_DIVISION, a MAJOR_CATEGORY, a VENDOR_CODE and a VENDOR_NAME of its own, stamped by SRM when
# the PO was raised; the master's are what SAP says today. Unprefixed, PROD_ATTRS's VENDOR_NAME
# would collide outright with the extract's, and the pair that do NOT collide by name (DIVISION vs
# DIV) would be worse - two columns a reader would read as the same fact. SM_ / PM_ says which
# side of the join a column came from, in the grid and in the exported header.
#
# ST_TYP IS ADDED TO THE STORE LIST, which the day-wise trend's own STORE_ATTRS does not carry
# (there the plant TYPE is a separate lookup, used to narrow stock columns). Here it is the first
# thing a reader needs: a PO's STORE_CODE is the plant that will RECEIVE the goods, and measured
# 18-Sep-2026 that is one of four DCs on 27,008 of 30,940 lines - 87%. So a TNA report grouped by
# SM_ZONE puts most of itself in one 'NA' bucket, not because the master is short but because a
# purchase order is not raised against a shop. SM_ST_TYP is what says so on the sheet.
STORE_SRC_COLS = list(base.STORE_ATTRS) + ["ST_TYP"]
STORE_DIM_MAP = {f"SM_{a}": a for a in STORE_SRC_COLS}
PROD_DIM_MAP = {f"PM_{a}": a for a in (list(base.PROD_ATTRS) + list(base.MAP_COLS))}
STORE_DIMS = list(STORE_DIM_MAP)
PROD_DIMS = list(PROD_DIM_MAP)

DIM_GROUPS = EXTRACT_GROUPS + [("Store master", STORE_DIMS), ("Product master", PROD_DIMS)]
ALL_DIMS = []
for _g, _cols in DIM_GROUPS:
    for _c in _cols:
        if _c not in ALL_DIMS:
            ALL_DIMS.append(_c)

# The PO LINE's own key. This is the default grain, and the one at which every aggregate below is
# the row's own value - see the module note.
DEFAULT_DIMS = ["PO_NUMBER", "ARTICLE_NUMBER", "STORE_CODE"]
# The grain is one row per PO line once any of these is picked. Not a refusal - the table is 30,940
# rows - but it is what /params publishes so the page can say what it is showing.
LINE_DIMS = {"PO_NUMBER", "ARTICLE_NUMBER", "VENDOR_DESIGN_NO", "COLOR_LIST", "LR_NO",
             "VEHICLE_NUMBER", "SAP_PACKING_LIST"}

# ── THE MEASURES ──────────────────────────────────────────────────────────────────────────────
#
# (SQL over the group, label). The AGGREGATE IS IN THE NAME wherever it is not a sum, because a
# grouped row that says AVG-DELAY cannot be read as a line's delay by accident.
MEASURES = {
    "LINES":       ("COUNT(*)", "LINES"),
    "POS":         ("COUNT(DISTINCT t.PO_NUMBER)", "POS"),
    "ARTICLES":    ("COUNT(DISTINCT t.ARTICLE_NUMBER)", "ARTICLES"),
    "PEND_PO_QTY": ("ROUND(SUM(t.PEND_PO_QTY),2)", "PEND-QTY"),
    "PEND_PO_VAL": ("ROUND(SUM(t.PEND_PO_VALUE),2)", "PEND-VAL"),
    # Positive = past its delivery date. The extract signs it that way (a line due in 27 days
    # reads -27), so it is carried through unchanged rather than re-signed here.
    "DEL_OVR_AGING":    ("ROUND(AVG(t.DEL_OVR_AGING),1)", "AVG-OVERDUE-DAYS"),
    "CRNT_STAT_AGEING": ("ROUND(AVG(t.CRNT_STAT_AGEING),1)", "AVG-STAT-AGE"),
    "MAX_STAGE_DELAY":  ("MAX(t.MAX_STAGE_DELAY)", "MAX-STAGE-DELAY"),
    "RISK_SCORE":       ("ROUND(AVG(t.RISK_SCORE),1)", "AVG-RISK"),
    "OVERDUE_LINES":    ("COUNT_IF(t.DEL_OVR_AGING > 0)", "OVERDUE-LINES"),
    "DELAYED_LINES":    ("COUNT_IF(t.MAX_STAGE_DELAY > 0)", "DELAYED-LINES"),
    "STALE_LINES":      ("COUNT_IF(t.IS_STALE)", "STALE-LINES"),
    "PORTAL_LINES":     ("COUNT_IF(t.PORTAL_CONTROLLED)", "PORTAL-LINES"),
}
MEASURE_KEYS = list(MEASURES)
MEASURE_LABEL = {k: v[1] for k, v in MEASURES.items()}
DEFAULT_MEASURES = ["PEND_PO_QTY", "PEND_PO_VAL", "DEL_OVR_AGING", "MAX_STAGE_DELAY", "RISK_SCORE"]

# ── THE STAGES, in the order the goods move through them ──────────────────────────────────────
#
# Each is three columns of the extract: STG_<KEY>_BUDGETED, STG_<KEY>, STG_<KEY>_STATUS. FIVE OF
# THEM HAVE NO BUDGETED DATE in the source - a hold, and the four receipt stamps at the far end -
# so their blocks carry no BUD and no DELAY rather than two columns that would always be empty.
# That is why a block's width is published per stage instead of assumed.
STAGE_KEYS = [
    "PO_ACKNOWLEDGED", "YARDAGE_RECEIVED", "PP_MAKING", "PP_SEND_TO_V2", "PP_APPROVED",
    "PP_PACKED_BY_V2", "PP_DISPATCH_BY_V2", "PP_RECEIVED_BY_VENDOR",
    "FABRIC_ORDERED", "FABRIC_RECEIVED", "CUTTING", "STITCHING", "PRINTING", "EMBROIDERY",
    "VALUE_ADDITION", "FINISHING", "BARCODING", "PO_HOLD_V2", "RFQC", "QC_DONE",
    "ASN_APPLIED", "ASN_APPROVED", "DISPATCH", "GRC",
    "DC_GATE_ENTRY", "DC_VEHICLE_UNLOADING", "DC_INTERNAL_QC", "DC_SORTING", "DC_BARCODING",
    "DC_STAGING", "DC_GRC_DONE", "DC_PUT01_SCANNING", "DC_TRANSFER_TO_MSA", "DC_GRT",
    "SBATING", "MRN", "QUALITY_DONE",
]
NO_BUDGET = {"PO_HOLD_V2", "GRC", "SBATING", "MRN", "QUALITY_DONE"}

_ACRONYMS = {"Po": "PO", "Pp": "PP", "V2": "V2", "Dc": "DC", "Qc": "QC", "Asn": "ASN",
             "Rfqc": "RFQC", "Grc": "GRC", "Grt": "GRT", "Msa": "MSA", "Mrn": "MRN",
             "Put01": "PUT01"}


def _stage_label(k):
    return " ".join(_ACRONYMS.get(w, w) for w in k.replace("_", " ").title().split())


STAGE_LABEL = {k: _stage_label(k) for k in STAGE_KEYS}
# The four stages a TNA reader opens on: fabric in, the two long making stages, and out of the
# door. Everything else is one tick away and rides the same query.
DEFAULT_STAGES = ["FABRIC_RECEIVED", "CUTTING", "STITCHING", "DISPATCH"]

# What one stage block holds. BUD / ACT / STATUS is the extract's own trio and the default; the
# other three are the aggregate questions a grouped grain asks of a stage.
STAGE_COLS = ["BUD", "ACT", "STATUS", "DELAY", "DONE", "PEND"]
DEFAULT_STAGE_COLS = ["BUD", "ACT", "STATUS"]
STAGE_COL_NOTE = {
    "BUD": "budgeted date (MIN over the group)",
    "ACT": "actual date (MAX over the group)",
    "STATUS": "the status when the group agrees, else MIXED",
    "DELAY": "avg days budgeted -> actual, or -> today while open",
    "DONE": "lines with an actual date",
    "PEND": "lines without one",
}

# The date column a range is applied to. Every one of these is a text date but C_STAT_DATE - and
# DELIVERY_DATE / CRNT_SAP_DEL_DT are blank on 11,978 of 30,940 rows, so a range on one of those
# DROPS the blanks. The page says so rather than letting a filter look like missing data.
DATE_BASES = ["DOC_DT", "DELIVERY_DATE", "BUDGETED_DATE", "READY_PLANNED", "CRNT_SAP_DEL_DT",
              "C_STAT_DATE"]
DEFAULT_DATE_BASIS = "DELIVERY_DATE"

SEARCH_COLS = ["PO_NUMBER", "ARTICLE_NUMBER", "GEN_ART_DESC", "VENDOR_NAME", "VENDOR_DESIGN_NO"]

MAX_LIMIT = 5_000
# The whole extract is ~31,000 rows, so this cap only ever catches a runaway grain - but it is
# checked the same way the day-wise trend checks its own, before a row is paged or written.
MAX_ROWS = 200_000
XLSX_MAX_ROWS = 1_048_574
MAX_VALUES = 2_000
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# /params counts and date-bounds the whole table. Cheap here, but it is called on every page load
# and the extract reloads once a day, so it is cached like every other params in this service.
_PARAMS_TTL_S = 10 * 60
_params_cache = {"at": 0.0, "data": None}


def _dim_expr(d):
    """(SQL expression, needs store master, needs product master) for one dimension.

    A date is read as a date, a boolean as a word, everything else as trimmed text - and the two
    prefixed families come off their joined lookup rather than the extract.

    THE TEXT DATES ARE THE POINT on the extract's side. 'YYYY-MM-DD' out of TRY_TO_DATE sorts,
    ranges and groups correctly; the extract's own 'DD.MM.YYYY' does none of the three.

    ::VARCHAR BEFORE TRIM on the master columns, always: several of them are DATE or NUMBER
    (ARTICLE_CREATION_DATE, ARTICLE_COST, COLOR_MAP_DATE), and TRIM of a DATE is a compile error in
    Snowflake rather than a silent cast."""
    if d in TEXT_DATES:
        return f"TO_VARCHAR(TRY_TO_DATE(t.{d}, 'DD.MM.YYYY'), 'YYYY-MM-DD')", False, False
    if d in DATE_DIMS:
        return f"TO_VARCHAR(t.{d}, 'YYYY-MM-DD')", False, False
    if d in BOOL_DIMS:
        return f"IFF(t.{d}, 'YES', 'NO')", False, False
    if d in STORE_DIM_MAP:
        return f"TRIM(sm.{STORE_DIM_MAP[d]}::VARCHAR)", True, False
    if d in PROD_DIM_MAP:
        return f"TRIM(pm.{PROD_DIM_MAP[d]}::VARCHAR)", False, True
    if d in EXTRACT_DIMS:
        return f"TRIM(t.{d}::VARCHAR)", False, False
    raise HTTPException(400, f"unknown dimension '{d}'")


def _lookups(need):
    """The master joins - and only the ones the grain or the filters actually named.

    BOTH QUALIFY DOWN TO ONE ROW PER KEY, so every join here is 1:1 and no dimension can fan a
    measure out. That is the property that matters most when a join is added to a query whose
    measures are sums: a store master with two rows for one ST_CD would double every PEND_PO_VALUE
    on that store's lines. Same subqueries the day-wise trend uses, for the same reason."""
    sql = ""
    if need["sm"]:
        sql += (f" LEFT JOIN (SELECT TRIM(ST_CD) ST_CD, {', '.join(STORE_SRC_COLS)} "
                f"FROM {base.STORE_TBL} QUALIFY ROW_NUMBER() OVER (PARTITION BY TRIM(ST_CD) "
                f"ORDER BY UPDATED_AT DESC NULLS LAST) = 1) sm ON sm.ST_CD = TRIM(t.STORE_CODE)")
    if need["pm"]:
        cols = ", ".join(f"v.{src} AS {alias}" for alias, src in
                         {**base.PROD_ATTR_MAP, **base.MAP_ATTR_MAP}.items())
        # TRY_TO_NUMBER on BOTH sides: the extract keeps the article as TEXT and the view's own
        # ARTICLE_NUMBER is text too, so matching them as numbers is what makes a leading zero or a
        # stray space on either side a non-event. Measured 18-Sep-2026: all 30,940 lines parse.
        sql += (f" LEFT JOIN (SELECT TRY_TO_NUMBER(v.ARTICLE_NUMBER) ARTICLE_NUMBER, {cols} "
                f"FROM {base.PROD_TBL} v WHERE TRY_TO_NUMBER(v.ARTICLE_NUMBER) IS NOT NULL "
                f"QUALIFY ROW_NUMBER() OVER (PARTITION BY TRY_TO_NUMBER(v.ARTICLE_NUMBER) "
                f"ORDER BY v.ARTICLE_CREATION_DATE DESC NULLS LAST) = 1) pm "
                f"ON pm.ARTICLE_NUMBER = TRY_TO_NUMBER(t.ARTICLE_NUMBER)")
    return sql


def _date_expr(c):
    """The DATE behind a date column, for a range test - never its text.

    Only the extract's own dates can be a range basis: the masters carry dates of their own
    (ARTICLE_CREATION_DATE, the two map stamps) but ranging a TNA report on when an article was
    created is a different question from when its PO is due, and DATE_BASES says so."""
    if c in TEXT_DATES:
        return f"TRY_TO_DATE(t.{c}, 'DD.MM.YYYY')"
    if c in DATE_DIMS:
        return f"t.{c}"
    raise HTTPException(400, f"'{c}' is not a date column here")


def _dims(requested):
    """The caller's grain, in CATALOGUE order - never request order, so two runs that tick the
    same dimensions in a different sequence produce the same file."""
    want = [str(d).strip().upper() for d in (requested or []) if str(d).strip()]
    if not want:
        return list(DEFAULT_DIMS)
    unknown = [d for d in want if d not in ALL_DIMS]
    if unknown:
        raise HTTPException(400, f"unknown dimension(s): {', '.join(unknown)}")
    return [d for d in ALL_DIMS if d in set(want)]


def _measures(requested):
    if requested is None:
        return list(DEFAULT_MEASURES)
    want = {str(m).strip().upper() for m in requested}
    return [m for m in MEASURE_KEYS if m in want]


def _stages(requested):
    if requested is None:
        return list(DEFAULT_STAGES)
    want = {str(s).strip().upper() for s in requested}
    unknown = want - set(STAGE_KEYS)
    if unknown:
        raise HTTPException(400, f"unknown stage(s): {', '.join(sorted(unknown))}")
    return [s for s in STAGE_KEYS if s in want]


def _stage_cols(requested):
    if requested is None:
        return list(DEFAULT_STAGE_COLS)
    want = {str(c).strip().upper() for c in requested}
    return [c for c in STAGE_COLS if c in want]


def stage_block(stage, cols):
    """The columns one stage contributes, in catalogue order and minus what it has no source for.

    A stage with no budgeted date carries no BUD and no DELAY - see NO_BUDGET."""
    if stage in NO_BUDGET:
        return [c for c in cols if c not in ("BUD", "DELAY")]
    return list(cols)


def stage_col(stage, col):
    """'CUTTING BUD' - one space, the same convention the day-wise trend uses, because both
    exports split on it to rebuild the two header rows."""
    return f"{stage} {col}"


def _stage_sql(stage, col):
    bud = f"t.STG_{stage}_BUDGETED"
    act = f"t.STG_{stage}"
    sts = f"t.STG_{stage}_STATUS"
    if col == "BUD":
        return f"TO_VARCHAR(MIN({bud}), 'YYYY-MM-DD')"
    if col == "ACT":
        return f"TO_VARCHAR(MAX({act}), 'YYYY-MM-DD')"
    if col == "STATUS":
        # The group's status when it HAS one. MIN = MAX over NVL'd text is the cheap way to ask
        # "does everything here agree", and it treats a blank as a value rather than dropping it -
        # COUNT(DISTINCT) would call one DONE and forty blanks agreement.
        return (f"IFF(MIN(NVL({sts},'')) = MAX(NVL({sts},'')), "
                f"NULLIF(MAX(NVL({sts},'')),''), 'MIXED')")
    if col == "DELAY":
        # Still open = measured to TODAY, which is what makes this the number a TNA meeting reads:
        # a stage that never cleared keeps growing instead of staying blank.
        return (f"ROUND(AVG(IFF({bud} IS NULL, NULL, "
                f"DATEDIFF('day', {bud}, NVL({act}, CURRENT_DATE())))),1)")
    if col == "DONE":
        return f"COUNT_IF({act} IS NOT NULL)"
    if col == "PEND":
        return f"COUNT_IF({act} IS NULL)"
    raise HTTPException(400, f"unknown stage column '{col}'")


def _filter_sql(filters, need):
    """The WHERE terms for {DIM: [values]}, and what they force into the FROM.

    ANDed across dimensions, ORed within one. '(blank)' is a real choice in the tick-lists and has
    to mean IS NULL / '' rather than match the literal text nobody has.

    A filter on a MASTER dimension pulls its join in exactly as a grain on one would - `need` is
    how, so a report grouped only by vendor but filtered on the store's zone still joins the store
    master and nothing else."""
    out = []
    for d, vals in (filters or {}).items():
        du = str(d).strip().upper()
        if du not in ALL_DIMS:
            continue
        clean = [str(v) for v in (vals or []) if str(v).strip() != ""]
        if not clean:
            continue
        expr, sm, pm = _dim_expr(du)
        need["sm"] = need["sm"] or sm
        need["pm"] = need["pm"] or pm
        lits = [v for v in clean if v != "(blank)"]
        parts = []
        if lits:
            parts.append(f"{expr} IN (" + ", ".join(base._q(v) for v in lits) + ")")
        if "(blank)" in clean:
            parts.append(f"NVL({expr},'') = ''")
        out.append("(" + " OR ".join(parts) + ")")
    return out


def _range_sql(basis, from_date, to_date):
    b = str(basis or DEFAULT_DATE_BASIS).strip().upper()
    if b not in DATE_BASES:
        raise HTTPException(400, f"date basis must be one of {DATE_BASES}")
    a = str(from_date or "").strip()
    z = str(to_date or "").strip()
    if a and z and a > z:
        raise HTTPException(400, f"fromDate ({a}) is after toDate ({z})")
    out = []
    for v, op in ((a, ">="), (z, "<=")):
        if not v:
            continue
        if not _DATE_RE.match(v):
            raise HTTPException(400, f"the {b} range must be dates in YYYY-MM-DD form")
        out.append(f"{_date_expr(b)} {op} {base._q(v)}::DATE")
    return out


def _search_sql(search):
    s = str(search or "").strip()
    if not s:
        return []
    like = base._q("%" + s + "%")
    return ["(" + " OR ".join(f"UPPER(NVL(t.{c}::VARCHAR,'')) LIKE UPPER({like})"
                              for c in SEARCH_COLS) + ")"]


_OPS = {">": ">", ">=": ">=", "<": "<", "<=": "<=", "=": "=", "!=": "<>"}


def _condition_expr(field):
    """The expression behind a condition's field when it is not on screen."""
    if field in MEASURES:
        return MEASURES[field][0]
    parts = field.rsplit(" ", 1)
    if len(parts) == 2 and parts[0] in STAGE_KEYS and parts[1] in ("DELAY", "DONE", "PEND"):
        return _stage_sql(parts[0], parts[1])
    raise HTTPException(
        400, f"'{field}' cannot be a condition - a condition tests a measure, or a stage's "
             f"DELAY / DONE / PEND, which are numbers. Dates and statuses are filtered in the "
             f"panels above.")


def _having_sql(conds, sel_by_name):
    """The numeric CONDITIONS, as HAVING over the group.

    A condition names a MEASURE or a stage column that is already in the projection, so the test
    is the same expression the column shows - there is no way for the grid to print 31 and the
    filter to have tested something else. A condition on a column that is NOT selected is still
    allowed (the expression is rebuilt), because 'show me the vendors, keep the ones over 7 days
    late' is a real question that does not need the column on screen."""
    out = []
    for c in (conds or []):
        field = str((c or {}).get("field") or "").strip().upper()
        op = str((c or {}).get("op") or ">").strip()
        raw = (c or {}).get("value")
        if not field or raw in (None, ""):
            continue
        if op not in _OPS:
            raise HTTPException(400, f"unknown operator '{op}'")
        try:
            val = float(raw)
        except (TypeError, ValueError):
            raise HTTPException(400, f"the condition on {field} needs a number, got '{raw}'")
        expr = sel_by_name.get(field) or _condition_expr(field)
        out.append(f"({expr}) {_OPS[op]} {val}")
    return out


class TnaReq(BaseModel):
    group_by: Optional[list] = None        # default PO_NUMBER + ARTICLE_NUMBER + STORE_CODE
    measures: Optional[list] = None        # default qty, value, overdue, max delay, risk
    stages: Optional[list] = None          # default fabric in, cutting, stitching, dispatch
    stage_cols: Optional[list] = None      # default BUD / ACT / STATUS
    # {DIM: [values]} - ANDed across dimensions, ORed within one, the same contract every other
    # MIS page's dimension filters carry.
    filters: Optional[dict] = None
    # [{field, op, value}] - HAVING over the group. See _having_sql.
    conditions: Optional[list] = None
    date_basis: Optional[str] = DEFAULT_DATE_BASIS
    from_date: Optional[str] = None        # both empty = the whole extract, which is legitimate
    to_date: Optional[str] = None
    search: Optional[str] = None
    page: int = 1
    limit: int = 200
    sort_col: Optional[str] = None
    sort_dir: Optional[str] = "asc"


class ValuesReq(BaseModel):
    # ONE dimension for a filter panel's tick-list, or SEVERAL for a folder dump, which needs the
    # combinations that actually OCCUR rather than the cross product - crossing two dimensions
    # blindly orders files that are mostly empty. The same shape sf_day_trend's /values carries.
    dim: Optional[str] = None
    columns: Optional[list] = None
    filters: Optional[dict] = None
    date_basis: Optional[str] = DEFAULT_DATE_BASIS
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    search: Optional[str] = None


def _build(req):
    """(sql, count_sql, columns, dims, measures, stages, stage_cols, blocks).

    One aggregate over one table: the grain in the GROUP BY, the measures and every picked stage's
    block in the projection, the value filters in the WHERE and the numeric conditions in the
    HAVING."""
    dims = _dims(req.group_by)
    meas = _measures(req.measures)
    stages = _stages(req.stages)
    scols = _stage_cols(req.stage_cols)
    if not meas and not (stages and scols):
        raise HTTPException(
            400, "Nothing to show beside the grain - pick at least one measure, or a stage and "
                 "the columns to show for it.")

    # Which masters this request actually needs - nothing is joined for a report that names
    # neither, which is most of them.
    need = {"sm": False, "pm": False}
    conds = (_filter_sql(req.filters, need)
             + _range_sql(req.date_basis, req.from_date, req.to_date)
             + _search_sql(req.search))
    where = (" WHERE " + " AND ".join(conds)) if conds else ""

    cols, proj = [], []
    sel_by_name = {}
    for d in dims:
        expr, sm, pm = _dim_expr(d)
        need["sm"] = need["sm"] or sm
        need["pm"] = need["pm"] or pm
        cols.append(d)
        proj.append(f'{expr} AS "{d}"')
    for m in meas:
        name = MEASURE_LABEL[m]
        cols.append(name)
        proj.append(f'{MEASURES[m][0]} AS "{name}"')
        sel_by_name[name] = MEASURES[m][0]
        sel_by_name[m] = MEASURES[m][0]
    blocks = {}
    for s in stages:
        blocks[s] = stage_block(s, scols)
        for c in blocks[s]:
            name = stage_col(s, c)
            cols.append(name)
            proj.append(f'{_stage_sql(s, c)} AS "{name}"')
            sel_by_name[name] = _stage_sql(s, c)

    grp = ", ".join(str(i + 1) for i in range(len(dims)))
    having = _having_sql(req.conditions, sel_by_name)
    having_sql = (" HAVING " + " AND ".join(having)) if having else ""

    sc = str(req.sort_col or "")
    direction = "DESC" if str(req.sort_dir or "").lower() == "desc" else "ASC"
    dim_order = ", ".join(f'"{d}"' for d in dims)
    if sc in cols:
        # NULLS LAST on every sort. A stage nothing has reached is all blanks, and Snowflake sorts
        # DESC nulls-first - so "the latest cutting date first" would open on a page of dashes.
        order = f'"{sc}" {direction} NULLS LAST, {dim_order}'
    else:
        order = dim_order

    body = f"FROM {TBL} t{_lookups(need)}{where} GROUP BY {grp}{having_sql}"
    sql = f"SELECT {', '.join(proj)} {body} ORDER BY {order}"
    # The count is of GROUPS, not of rows, so it has to wrap the grouped query - and it carries the
    # HAVING with it, or a condition would narrow the page and not the total.
    count_sql = f"SELECT COUNT(*) FROM (SELECT {', '.join(proj)} {body})"
    return sql, count_sql, cols, dims, meas, stages, scols, blocks


@router.get("/params")
def tna_params(refresh: int = 0):
    """The catalogues the panels draw from, and what is actually loaded.

    THE LOAD STAMP IS NOT DECORATION. This is a SNAPSHOT of the SRM portal - one row per PENDING
    line, replaced wholesale each load - so every number on the page is 'as at' that stamp, and a
    stale extract is the one failure mode a reader cannot see from the grid."""
    if not refresh and _params_cache["data"] is not None \
            and (time.time() - _params_cache["at"]) < _PARAMS_TTL_S:
        return {**_params_cache["data"], "cached": True}

    conn = base._conn()
    try:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT COUNT(*), COUNT(DISTINCT PO_NUMBER), COUNT(DISTINCT ARTICLE_NUMBER),
                   COUNT(DISTINCT VENDOR_CODE), COUNT(DISTINCT STORE_CODE),
                   ROUND(SUM(PEND_PO_QTY),2), ROUND(SUM(PEND_PO_VALUE),2),
                   COUNT_IF(DEL_OVR_AGING > 0), COUNT_IF(IS_STALE), MAX(LOADED_AT),
                   TO_VARCHAR(MIN(TRY_TO_DATE(DOC_DT,'DD.MM.YYYY')),'YYYY-MM-DD'),
                   TO_VARCHAR(MAX(TRY_TO_DATE(DOC_DT,'DD.MM.YYYY')),'YYYY-MM-DD'),
                   TO_VARCHAR(MIN(TRY_TO_DATE(DELIVERY_DATE,'DD.MM.YYYY')),'YYYY-MM-DD'),
                   TO_VARCHAR(MAX(TRY_TO_DATE(DELIVERY_DATE,'DD.MM.YYYY')),'YYYY-MM-DD'),
                   COUNT_IF(NVL(TRIM(DELIVERY_DATE),'') = '')
              FROM {TBL}""")
        r = cur.fetchone() or []
        stats = {"rows": int(r[0] or 0), "pos": int(r[1] or 0), "articles": int(r[2] or 0),
                 "vendors": int(r[3] or 0), "stores": int(r[4] or 0),
                 "pendQty": base._f(r[5]), "pendValue": base._f(r[6]),
                 "overdueLines": int(r[7] or 0), "staleLines": int(r[8] or 0),
                 "loadedAt": base._fmt_str(r[9]),
                 "docFrom": r[10], "docTo": r[11],
                 "deliveryFrom": r[12], "deliveryTo": r[13],
                 "noDeliveryDate": int(r[14] or 0)}

        # WHICH STAGES THIS EXTRACT ACTUALLY USES. All 37 are columns of the table, but a stage
        # nobody stamps is a block of blank columns waiting to be ticked, so the count rides along
        # and the picker can say which are empty instead of a reader finding out one at a time.
        sel = ", ".join(f'COUNT_IF(STG_{s} IS NOT NULL) "{s}"' for s in STAGE_KEYS)
        cur.execute(f"SELECT {sel} FROM {TBL}")
        names = [d[0] for d in cur.description]
        row = cur.fetchone() or []
        stage_use = {n: int(v or 0) for n, v in zip(names, row)}

        # WHAT A STORE_CODE ACTUALLY IS ON THIS EXTRACT, counted rather than asserted. A PO names
        # the plant that will RECEIVE it, and most of them are DCs - so the page can say what
        # grouping by a store-master column is about to do BEFORE a reader reads a sheet that is
        # 87% one 'NA' bucket. Published as measured numbers, never as prose with a figure baked in
        # that would go stale the first time the mix moved.
        cur.execute(f"""
            SELECT NVL(NULLIF(TRIM(sm.ST_TYP),''),'(unknown)'), COUNT(*),
                   COUNT(DISTINCT TRIM(t.STORE_CODE))
              FROM {TBL} t{_lookups({'sm': True, 'pm': False})}
             GROUP BY 1 ORDER BY 2 DESC""")
        plant_mix = [{"type": r[0], "lines": int(r[1] or 0), "plants": int(r[2] or 0)}
                     for r in cur.fetchall()]
    finally:
        conn.close()

    out = {"table": TBL, "stats": stats,
           "dims": [{"label": g, "cols": cols} for g, cols in DIM_GROUPS],
           "allDims": ALL_DIMS, "defaultDims": DEFAULT_DIMS, "lineDims": sorted(LINE_DIMS),
           "measures": MEASURE_KEYS, "measureLabels": MEASURE_LABEL,
           "defaultMeasures": DEFAULT_MEASURES,
           "stages": STAGE_KEYS, "stageLabels": STAGE_LABEL, "defaultStages": DEFAULT_STAGES,
           "noBudgetStages": sorted(NO_BUDGET), "stageUse": stage_use,
           # The two joined families, so the page can name them without a second copy of the lists,
           # and the plant mix behind STORE_CODE - see the note where it is counted.
           "storeDims": STORE_DIMS, "prodDims": PROD_DIMS, "plantMix": plant_mix,
           "stageCols": STAGE_COLS, "defaultStageCols": DEFAULT_STAGE_COLS,
           "stageColNotes": STAGE_COL_NOTE,
           "dateBases": DATE_BASES, "defaultDateBasis": DEFAULT_DATE_BASIS,
           "conditionFields": MEASURE_KEYS, "operators": list(_OPS),
           "maxRows": MAX_ROWS}
    _params_cache["at"] = time.time()
    _params_cache["data"] = out
    return {**out, "cached": False}


@router.post("/result")
def tna_result(req: TnaReq):
    page = max(1, int(req.page or 1))
    limit = max(1, min(int(req.limit or 200), MAX_LIMIT))
    sql, count_sql, cols, dims, meas, stages, scols, blocks = _build(req)
    conn = base._conn()
    try:
        cur = conn.cursor()
        cur.execute(count_sql)
        total = int(cur.fetchone()[0] or 0)
        if total > MAX_ROWS:
            raise HTTPException(
                400, f"{total:,} rows at this grain - the page caps at {MAX_ROWS:,}. Narrow the "
                     f"filters, or group by something above the PO line.")
        cur.execute(f"{sql} LIMIT {limit} OFFSET {(page - 1) * limit}")
        names = [c[0] for c in cur.description]
        rows = [[base._fmt_str(v) for v in r] for r in cur.fetchall()]
    finally:
        conn.close()
    return {"columns": names, "rows": rows, "total": total, "page": page, "limit": limit,
            "dims": dims, "measures": [MEASURE_LABEL[m] for m in meas],
            "measureKeys": meas, "stages": stages, "stageCols": scols,
            "stageLabels": {s: STAGE_LABEL[s] for s in stages},
            # A block's width varies - the five stages with no budgeted date carry no BUD and no
            # DELAY - so the page is told each one rather than multiplying.
            "blocks": {s: blocks[s] for s in stages},
            "lineGrain": bool(set(dims) & LINE_DIMS)}


def _combos(cols, filters, basis, from_date, to_date):
    """The distinct COMBINATIONS of the split columns that actually occur, with a row count each.

    A folder dump asks this before it writes anything, so it plans one file per combination that
    EXISTS rather than crossing every value of every level - two levels crossed blindly order
    hundreds of files that are nearly all empty. The count rides along in the same GROUP BY so a
    caller can report a slice with no lines instead of writing an empty sheet for it."""
    for c in cols:
        if c not in ALL_DIMS:
            raise HTTPException(400, f"cannot split on '{c}' - it is not a dimension here")
    need = {"sm": False, "pm": False}
    conds = _filter_sql(filters, need) + _range_sql(basis, from_date, to_date)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    exprs = []
    for c in cols:
        e, sm, pm = _dim_expr(c)
        need["sm"] = need["sm"] or sm
        need["pm"] = need["pm"] or pm
        exprs.append(f"NVL(NULLIF({e},''),'(blank)')")
    order = ", ".join(str(i + 1) for i in range(len(cols)))
    sql = (f"SELECT {', '.join(exprs)}, COUNT(*) FROM {TBL} t{_lookups(need)}{where} "
           f"GROUP BY {order} ORDER BY {order}")
    conn = base._conn()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
    finally:
        conn.close()
    return {"columns": cols,
            "combos": [[base._fmt_str(v) for v in r[:-1]] for r in rows],
            "counts": [int(r[-1] or 0) for r in rows]}


@router.post("/values")
def tna_values(req: ValuesReq):
    """The values a filter panel offers for one dimension, within the range and the OTHER filters.

    NARROWED BY THE FILTERS ALREADY SET, minus this dimension's own - which is what makes the
    panels usable in sequence: pick a division and the category list is that division's
    categories. Excluding its own filter is what lets a choice already made be widened without
    clearing it first."""
    cols = [str(c).strip().upper() for c in (req.columns or []) if str(c).strip()]
    if cols:
        return _combos(cols, req.filters, req.date_basis, req.from_date, req.to_date)
    d = str(req.dim or "").strip().upper()
    if d not in ALL_DIMS:
        raise HTTPException(400, f"unknown dimension '{d}'")
    others = {k: v for k, v in (req.filters or {}).items() if str(k).strip().upper() != d}
    need = {"sm": False, "pm": False}
    conds = _filter_sql(others, need) + _range_sql(req.date_basis, req.from_date, req.to_date)
    expr, sm, pm = _dim_expr(d)
    need["sm"] = need["sm"] or sm
    need["pm"] = need["pm"] or pm
    s = str(req.search or "").strip()
    if s:
        conds.append(f"UPPER(NVL({expr},'')) LIKE UPPER({base._q('%' + s + '%')})")
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    # THE EXTRACT'S OWN ROWS, not the master's: a filter panel offers the values that occur on a
    # pending PO line, so the store list is the 309 stores with POs open rather than all 795 in the
    # master - a tick nobody could use is worse than one that is missing.
    sql = (f"SELECT DISTINCT NVL(NULLIF({expr},''),'(blank)') V FROM {TBL} t{_lookups(need)}{where} "
           f"ORDER BY 1 LIMIT {MAX_VALUES + 1}")
    conn = base._conn()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        vals = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    return {"dim": d, "values": vals[:MAX_VALUES], "truncated": len(vals) > MAX_VALUES,
            "cap": MAX_VALUES}


@router.post("/export")
def tna_export(req: TnaReq, fmt: str = "xlsx"):
    """The whole scoped selection, in the page's own two-row header: the stage name banded over
    its block, the column names beneath.

    Same shape and same reasoning as the day-wise trend's export - xlsxwriter in constant-memory
    mode, every header row and merge written before the first data row, because a write back to an
    earlier row after that is silently dropped."""
    if fmt not in ("xlsx", "csv"):
        raise HTTPException(400, "fmt must be xlsx or csv")
    sql, count_sql, cols, dims, meas, stages, scols, blocks = _build(req)

    head = list(cols)
    band = [""] * (len(dims) + len(meas))
    bands = []
    at = len(dims) + len(meas)
    for s in stages:
        width = len(blocks[s])
        if not width:
            continue
        bands.append((at, width, STAGE_LABEL[s]))
        band += [STAGE_LABEL[s]] + [""] * (width - 1)
        at += width

    conn = base._conn()
    cur = conn.cursor()
    if fmt == "csv":
        # The connection stays open behind the generator, the same shape every streamed CSV in
        # this service has. Closing it here would end the cursor mid-stream.
        cur.execute(sql)
        return _stream_csv_two_row(cur, band, head, "TNA_REPORT.csv")

    import tempfile
    import os
    import xlsxwriter
    try:
        cur.execute(count_sql)
        total = int(cur.fetchone()[0] or 0)
        if total > XLSX_MAX_ROWS:
            raise HTTPException(
                400, f"{total:,} rows is past Excel's own limit of {XLSX_MAX_ROWS:,} - narrow the "
                     f"filters, or take it as CSV.")
        cur.execute(sql)
        fd, path = tempfile.mkstemp(suffix=".xlsx", prefix="tna_report_")
        os.close(fd)
        wb = xlsxwriter.Workbook(path, {"constant_memory": True, "use_zip64": True})
        ws = wb.add_worksheet("TNA")
        band_fmt = wb.add_format({"bold": True, "align": "center", "bg_color": "#DDEBF7",
                                  "font_size": 8})
        head_fmt = wb.add_format({"bold": True, "font_size": 8})
        # ROW 0 BEFORE ROW 1, and both before any data: constant memory flushes a row to disk the
        # moment the next one is touched, so a write back to an earlier row is silently DROPPED.
        for c0, width, text in bands:
            if width > 1:
                ws.merge_range(0, c0, 0, c0 + width - 1, text, band_fmt)
            else:
                ws.write(0, c0, text, band_fmt)
        for j, v in enumerate(head):
            ws.write(1, j, v, head_fmt)
        ws.set_column(0, max(0, len(head) - 1), 14)
        ws.freeze_panes(2, len(dims))
        r = 2
        while True:
            batch = cur.fetchmany(5000)
            if not batch:
                break
            for row in batch:
                for j, v in enumerate(row):
                    if v is not None:
                        ws.write(r, j, v)
                r += 1
        wb.close()
    finally:
        conn.close()

    def stream():
        try:
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    return StreamingResponse(
        stream(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="TNA_REPORT.xlsx"'})


def _stream_csv_two_row(cur, band, head, filename):
    """The service's _stream_csv, with the stage band written above the column names."""
    import csv
    from .sf_gandola import _na_row, _text_cols
    text = _text_cols(cur)

    def generate():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(band)
        w.writerow(head)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        while True:
            batch = cur.fetchmany(2000)
            if not batch:
                break
            for r in batch:
                w.writerow(_na_row(r, text))
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    return StreamingResponse(generate(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})

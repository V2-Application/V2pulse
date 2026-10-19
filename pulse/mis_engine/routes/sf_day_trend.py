"""
Snowflake MIS — DAY WISE SALE & STOCK TREND (mounted at /sf-day-trend).

The LAST-30-DAYS SALE-STOCK-TREND sheet, built from the same two day-wise facts every other MIS
page reads. One row per STORE x VARIANT ARTICLE, and one BLOCK OF SEVEN COLUMNS PER DAY:

    ST_CD | VAR_ART || 0001-STK-Q 0001-STK-V 0006-STK-Q 0006-STK-V SALE-Q SALE-V GM-V | (next day) ...

Newest day first, exactly as the workbook has it, so the two line up column for column.

WHY IT IS ITS OWN PAGE AND NOT A SAVED REPORT. The report builder's model is dimensions x sale
WINDOWS: the windows are overlapping lookbacks (MTD, QTR, YTD) anchored on one date, and every
measure is one column. This is the other shape - one column per DAY, the days side by side, with
stock and sale interleaved per day. Neither engine can express that without inventing a day
dimension that would multiply the rows instead of widening them.

THE GRAIN, THE STOCK TAGS AND THE MEASURES ARE PICKED, like the MIS report builder's. ST_CD x
VAR_ART with 0001 / 0006 stock and the day's sale is the DEFAULT - the workbook unchanged - but the
grain can be any set of fact, store-master or product-master dimensions, the stock tags are the
same catalogue the variant grid offers (composites, rule tags and picks included), and each
dimension carries a value filter. What does not move is the second axis: DAYS, one block of columns
each.

SCOPE IS ENFORCED BY THE GRAIN, NOT BY A RULE ABOUT FILTERS. Measured 17-Sep-2026, an article-deep
grain over 30 days is 1,52,27,699 rows across the estate - neither a grid nor a spreadsheet, since
Excel stops at 1,048,576. So an article-deep grain with nothing narrowing it is refused by name,
and every grain is capped at MAX_ROWS after the count. Grouping by MAJ_CAT alone is a few thousand
rows and needs no filter at all, which is why the old "always pick a store" rule was wrong.

STOCK IS WHAT THAT DAY REPORTED, NOT A CARRY-FORWARD. The variant grid carries each store's latest
position forward to the anchor, because it shows ONE date and a store that did not report that day
would read as empty. Thirty columns side by side is the opposite case: carrying forward paints a
flat line that looks like stock and is really the absence of a file. Measured over the last 30
days, 8 of them carry 1-3 stores (21-28 Aug), one is missing entirely (29 Aug) and 30-31 Aug carry
456 of 500 - so a carry-forward sheet would repeat 16-Aug positions across a third of its width.
Blank means "this store did not report that day", and /params returns the per-day store count so
the page can say which columns are thin BEFORE anyone reads a number off them.

SALE IS THE DAY'S OWN SALE - no windows, no anchoring. A day column is that calendar day, so the
columns add up across the sheet, unlike every window column on the variant grid.
"""
import io
import logging
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from . import sf_var_combined as base

logger = logging.getLogger(__name__)
router = APIRouter()

# ── WHAT A DAY BLOCK HOLDS, and it is now the caller's choice ────────────────────────────────
#
# The workbook's own block is two ET storage locations and the day's sale, and that stays the
# DEFAULT - a sheet taken with nothing picked is the format unchanged. Everything below is a
# vocabulary to pick from, the same way the MIS report builder picks dimensions, windows and
# measures. The difference is the second axis: DAYS, one column block each, rather than
# overlapping windows anchored on one date.
DEFAULT_STK_TAGS = ["0001", "0006"]
DEFAULT_MEASURES = ["SALE_Q", "SALE_V", "GM_V"]
# The sale measures, and the workbook's spelling for each. DISCOUNT_V is offered because the fact
# carries it and discount beside sale is the obvious next question; it is not in the default
# because it is not in the workbook.
MEAS_LABEL = {"SALE_Q": "SALE-Q", "SALE_V": "SALE-V", "GM_V": "GM-V", "DISCOUNT_V": "DISC-V"}
SALE_MEASURES = list(MEAS_LABEL)

# ── THE GRAIN, also the caller's choice ──────────────────────────────────────────────────────
#
# THREE FAMILIES, and which one a dimension belongs to decides what has to be joined:
#
#   fact     stamped on BOTH day-wise facts, so grouping by one costs nothing at all
#   store    the MIS store master, joined on ST_CD            (sm)
#   product  VW_PRODUCT_MASTER, joined on ARTICLE_NUMBER      (pm)
#
# Both lookups QUALIFY down to ONE row per key, so every join is 1:1 and no dimension here can fan
# a measure out. That is the property the month-wise predecessor lost when it joined two multi-row
# tables at once and inflated sale by 20.7%, and it is worth re-stating each time a join is added
# to an aggregate.
FACT_DIMS = {
    "ST_CD": "ST_CD",
    # VAR_ART is the workbook's name for the 13-digit article. The column keeps that name rather
    # than the fact's ARTICLE_NUMBER so a sheet still lines up with the file it replaces.
    "VAR_ART": "ARTICLE_NUMBER",
    "GEN_ART_NUMBER": "GEN_ART_NUMBER",
    "GEN_ART_DESC": "GEN_ART_DESC",
    "COLOR": "COLOR",
    "SIZE_1": "SIZE_1",
    "MAJ_CAT": "MAJ_CAT",
    "SEG": "SEG",
}
STORE_DIMS = list(base.STORE_ATTRS)
PROD_DIMS = list(base.PROD_ATTRS) + list(base.MAP_COLS)
ALL_DIMS = list(FACT_DIMS) + STORE_DIMS + PROD_DIMS
DIM_GROUPS = [("Fact", list(FACT_DIMS)), ("Store master", STORE_DIMS),
              ("Product master", PROD_DIMS)]
DEFAULT_DIMS = ["ST_CD", "VAR_ART"]

# THE DIMENSIONS THAT MAKE THE GRAIN ARTICLE-DEEP. Grouping by any of these puts one row per
# article (or finer) on the sheet: 1,52,27,699 rows across the estate over 30 days, measured
# 17-Sep-2026. Grouping by MAJ_CAT alone is a few thousand. So the scope rule below asks for a
# filter only when the grain is actually deep, instead of demanding one from a report that would
# have returned four hundred rows.
ARTICLE_DIMS = {"VAR_ART", "GEN_ART_NUMBER", "GEN_ART_DESC", "COLOR", "SIZE_1"}

KEY_COLS = ["ST_CD", "VAR_ART"]          # the default grain, for the page's first render

# 31, the same ceiling the variant grid carries and for the same reason: each day is its own set of
# conditional aggregates over both facts, so the width and the work grow together. The workbook
# wants 30.
MAX_DAYS = 31
DEFAULT_DAYS = 30
MAX_LIMIT = 5_000
# The result cap, applied to the COUNT before a row is paged or written. Well under Excel's 10.4
# lakh on purpose: a sheet this wide is unreadable long before then, and the message names the
# number so the reader knows what to narrow.
MAX_ROWS = 400_000
XLSX_MAX_ROWS = 1_048_574
# What a filter panel offers. Capped because MAJ_CAT alone has 2,901 values and a tick-list is not
# a search engine; the reply says when a list was cut.
MAX_VALUES = 2_000

_COL_RE = re.compile(r"^[A-Z0-9_]+$")


def _dim_expr(d):
    """(SQL expression, needs store master, needs product master) for one dimension."""
    if d in FACT_DIMS:
        return f"f.{FACT_DIMS[d]}", False, False
    if d in STORE_DIMS:
        return f"sm.{d}", True, False
    if d in PROD_DIMS:
        return f"pm.{d}", False, True
    raise HTTPException(400, f"unknown dimension '{d}'")


def _dims(requested):
    """The caller's grain, in CATALOGUE order - never request order.

    Column POSITION is what a spreadsheet built on this sheet depends on, so two runs that pick the
    same dimensions in a different sequence have to produce the same file."""
    want = [str(d).strip().upper() for d in (requested or []) if str(d).strip()]
    if not want:
        return list(DEFAULT_DIMS)
    unknown = [d for d in want if d not in ALL_DIMS]
    if unknown:
        raise HTTPException(400, f"unknown dimension(s): {', '.join(unknown)}")
    return [d for d in ALL_DIMS if d in set(want)]


def _tags(requested):
    """The stock tags, validated against the SAME catalogue the variant grid uses - so a composite
    (TOTAL_STORE_STOCK), a rule tag (LQ_V02) and a pick (MSA, GRT) all work here too and mean
    exactly what they mean there."""
    if not requested:
        return list(DEFAULT_STK_TAGS)
    return base._tags(requested)


def _measures(requested):
    if not requested:
        return list(DEFAULT_MEASURES)
    want = {str(m).strip().upper() for m in requested}
    picked = [m for m in SALE_MEASURES if m in want]
    if not picked:
        raise HTTPException(400, f"unknown sale measure(s); expected some of {SALE_MEASURES}")
    return picked


# The combined stock column's name. TOTAL rather than the tag list, because the tags are the
# caller's pick and a name built from them ("0001+0006-STK-Q") would change every time the pick
# does - which is exactly the sort of moving column name a spreadsheet pointed at this file cannot
# survive.
STK_TOTAL = "TOTAL"
# What the combined column does with the picked tags. 'add' keeps each tag AND totals them, 'only'
# is the total by itself, 'off' is the tags alone - the shape this page had before.
STOCK_TOTAL_MODES = ("add", "only", "off")


def day_cols(tags, meas, stock_total="add"):
    """The measure names inside one day's block, in the workbook's order: stock, then sale.

    THE COMBINED COLUMN IS THE PICKED TAGS ADDED UP - pick 0001 and 0006 and TOTAL-STK-Q is the
    shop floor plus the back room, per day, which is the one number most of this sheet's readers
    actually want. It goes at the END of the stock half so the per-tag columns keep their
    positions; 'only' drops them instead, for a sheet that wants the total and the sale and
    nothing else.

    IT IS AN ADDITION, SO THE PICK HAS TO BE ADDABLE. 0001 + 0006 are two ET storage locations and
    disjoint, so their sum is a real number. 0001 + LQ_V01 is NOT: the LQUA families regroup stock
    the ET tags already report, so adding them counts the same garment twice. This module cannot
    refuse the pick - a reader may have a reason - but /result reports the families behind the
    total so the page can say so above the numbers. See tag_family in sf_var_combined."""
    stk = [f"{t}-STK-{m}" for t in tags for m in ("Q", "V")]
    if stock_total == "only" and len(tags) > 1:
        stk = []
    if stock_total in ("add", "only") and len(tags) > 1:
        stk += [f"{STK_TOTAL}-STK-Q", f"{STK_TOTAL}-STK-V"]
    return stk + [MEAS_LABEL[m] for m in meas]


def _stock_total_mode(req):
    m = str(getattr(req, "stock_total", "add") or "add").strip().lower()
    return m if m in STOCK_TOTAL_MODES else "add"


def day_col(d, meas):
    """The column name for one (day, measure) pair - '2026-09-16 SALE-Q'.

    One space, because the grid and both exports split on it to rebuild the workbook's two header
    rows, and because that is what an Excel formula pointing at this sheet will read."""
    return f"{d} {meas}"


def _days(from_date, to_date):
    a, b = base._range(from_date, to_date)
    d0, d1 = datetime.fromisoformat(a).date(), datetime.fromisoformat(b).date()
    span = (d1 - d0).days + 1
    if span > MAX_DAYS:
        raise HTTPException(
            400, f"{span} days requested. Every day is its own block of columns and its own "
                 f"aggregates, so the range is capped at {MAX_DAYS} - narrow the dates.")
    # NEWEST FIRST, like the workbook. The sheet is read left to right as "today, yesterday, the
    # day before", so the SQL builds them in that order and nothing downstream re-sorts.
    return a, b, [(d1 - timedelta(days=i)).isoformat() for i in range(span)]


class TrendReq(BaseModel):
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    group_by: Optional[list] = None        # dimensions; default ST_CD + VAR_ART
    stock_tags: Optional[list] = None      # default 0001 + 0006
    measures: Optional[list] = None        # default SALE_Q, SALE_V, GM_V
    # {DIM: [values]} - ANDed across dimensions, ORed within one, which is the contract the report
    # builder's dimension filters already carry.
    filters: Optional[dict] = None
    # "V2R: the DCs only" - narrows the V2R_* stock COLUMNS to the trading DCs (DH24 / DW01 / DK04
    # today) and leaves every row, every other tag and all the sale alone. See DC_ONLY_PRED.
    # "ET stock: stores only" - the same switch the MIS report builder carries. The ET LGORT tags
    # (0001 ... PSA1) count plants typed STORE and nothing else, so a sheet whose ROWS span the
    # estate can still have shop-floor stock that means the shop floor. Columns only: no row is
    # dropped, and the LQUA / PRD / PTL / HUBPRD families are untouched, because that stock lives
    # at DCs and hubs by nature and restricting it to STORE would blank it rather than move it.
    # 'add' (default) = each picked tag AND their total; 'only' = the total alone; 'off' = the
    # tags alone, which is what this page did before the total existed.
    stock_total: str = "add"
    store_only: bool = False
    dc_only: bool = False
    # The same narrowing said explicitly: the plants to count for the V2R_* columns. A list wins
    # over dc_only, because a reader who names plants means those plants.
    v2r_plants: Optional[list] = None
    search: Optional[str] = None
    # CONDITIONS, the MIS Reports panel's shape ({col, op, v1, v2, vals}). dim_conds name any
    # dimension (grouped or not) and go into both facts' WHERE; meas_conds name a measure id from
    # meas_cond_ids() and filter the finished rows - see _meas_cond_sql.
    dim_conds: Optional[list] = None
    meas_conds: Optional[list] = None
    page: int = 1
    limit: int = 200
    sort_col: Optional[str] = None
    sort_dir: Optional[str] = "asc"


class ValuesReq(BaseModel):
    # ONE dimension for a filter panel's tick-list, or SEVERAL for a folder dump, which needs the
    # combinations that actually OCCUR rather than the cross product - crossing two dimensions
    # blindly orders files that are mostly empty. Exactly the shape Bin Wise's /values carries.
    dim: Optional[str] = None
    columns: Optional[list] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    filters: Optional[dict] = None
    search: Optional[str] = None
    dim_conds: Optional[list] = None


def _dim_cond_sql(conds, need):
    """The dimension CONDITIONS (contains / begins with / between / is none of ...) as WHERE terms,
    and what they force into the FROM. Same operators and SQL as the Inventory Reports engine."""
    from .sf_vert_inv_art import _cond
    out = []
    for c in conds or []:
        d = str((c or {}).get("col") or "").strip().upper()
        if d not in ALL_DIMS:
            raise HTTPException(400, f"A condition names '{d}', which is not a dimension here")
        expr, sm, pm = _dim_expr(d)
        need["sm"] = need["sm"] or sm
        need["pm"] = need["pm"] or pm
        sql = _cond(expr, c, False)
        if sql:
            out.append(f"({sql})")
    return out


# MEASURE CONDITIONS. A day block has thirty columns per measure, so a condition names one number
# per ROW instead: a sale measure's TOTAL over the window, or a stock tag's reading on the LAST day
# of it (stock does not add up across days). Ids: 'SALE_Q' ... and 'STK_Q:<tag>' / 'STK_V:<tag>'
# ('STK_Q:TOTAL' = the picked tags added up).
def meas_cond_ids(tags, meas):
    out = [(m, f"{MEAS_LABEL[m]} (window total)") for m in meas]
    for t in list(tags) + ([STK_TOTAL] if len(tags) > 1 else []):
        for m in ("Q", "V"):
            out.append((f"STK_{m}:{t}", f"{t}-STK-{m} (last day)"))
    return out


def _meas_cond_sql(conds, meas, tags):
    """(outer WHERE over the joined result, extra sale-side selects, extra stock-side selects).
    NVL(...,0): a row that did not sell at all has SALE-Q 0, so 'SALE-Q = 0' finds it."""
    from .sf_vert_inv_art import _cond
    ok = {i for i, _l in meas_cond_ids(tags, meas)}
    parts, need_sale, need_stk = [], [], []
    for c in conds or []:
        col = str((c or {}).get("col") or "").strip().upper()
        if col not in ok:
            raise HTTPException(400, f"A condition names '{col}', which is not one of this run's measures")
        alias = "__C_" + "".join(ch if ch.isalnum() else "_" for ch in col)
        ref = f'{"sa" if col in meas else "st"}."{alias}"'
        (need_sale if col in meas else need_stk).append((col, alias))
        op = str(c.get("op") or "").lower()
        sql = _cond(ref if op in ("blank", "nonblank") else f"NVL({ref},0)", c, True)
        if sql:
            parts.append(f"({sql})")
    return parts, list(dict.fromkeys(need_sale)), list(dict.fromkeys(need_stk))


def _filter_sql(filters, need):
    """The WHERE terms for {DIM: [values]}, and what they force into the FROM.

    '(blank)' is a real choice, exactly as it is in the Bin Wise tick-lists, and it has to mean
    IS NULL / '' rather than the literal text nobody has."""
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
            parts.append(f"TRIM({expr}::VARCHAR) IN (" + ", ".join(base._q(v) for v in lits) + ")")
        if "(blank)" in clean:
            parts.append(f"NVL(TRIM({expr}::VARCHAR),'') = ''")
        out.append("(" + " OR ".join(parts) + ")")
    return out


def _search_sql(search):
    s = str(search or "").strip()
    if not s:
        return []
    like = base._q("%" + s + "%")
    return [f"(f.ARTICLE_NUMBER::VARCHAR LIKE {like} "
            f"OR UPPER(NVL(f.GEN_ART_DESC,'')) LIKE UPPER({like}))"]


def _lookups(need):
    """The master joins - and only the ones the grain or the filters actually named.

    Both QUALIFY down to ONE row per key, so every join here is 1:1 and no dimension can fan a
    measure out. That is the property that matters most when a join is added to an aggregate."""
    sql = ""
    if need["sm"]:
        sql += (f" LEFT JOIN (SELECT TRIM(ST_CD) ST_CD, {', '.join(STORE_DIMS)} "
                f"FROM {base.STORE_TBL} QUALIFY ROW_NUMBER() OVER (PARTITION BY TRIM(ST_CD) "
                f"ORDER BY UPDATED_AT DESC NULLS LAST) = 1) sm ON sm.ST_CD = TRIM(f.ST_CD)")
    if need["pm"]:
        cols = ", ".join(f"v.{src} AS {alias}" for alias, src in
                         {**base.PROD_ATTR_MAP, **base.MAP_ATTR_MAP}.items())
        sql += (f" LEFT JOIN (SELECT TRY_TO_NUMBER(v.ARTICLE_NUMBER) ARTICLE_NUMBER, {cols} "
                f"FROM {base.PROD_TBL} v WHERE TRY_TO_NUMBER(v.ARTICLE_NUMBER) IS NOT NULL "
                f"QUALIFY ROW_NUMBER() OVER (PARTITION BY TRY_TO_NUMBER(v.ARTICLE_NUMBER) "
                f"ORDER BY v.ARTICLE_CREATION_DATE DESC NULLS LAST) = 1) pm "
                f"ON pm.ARTICLE_NUMBER = f.ARTICLE_NUMBER")
    return sql


def _build(req):
    """(sql, count_sql, columns, days, dims, tags, measures).

    ONE ROW PER GRAIN TUPLE, one block of columns per day. The two facts are aggregated to the
    grain separately and FULL OUTER JOINed on it: a row belongs on the sheet if it SOLD in the
    range OR was HELD in it, and an inner join would drop everything that sold out and everything
    sitting unsold - between them most of the sheet.

    EQUAL_NULL, not '=', on the join keys. A dimension can be NULL (an article the product master
    has no row for), and '=' on two NULLs is NULL - so the sale side and the stock side of one
    tuple would land on two half-blank rows and every column would read twice.
    """
    a, b, days = _days(req.from_date, req.to_date)
    dims = _dims(req.group_by)
    tags = _tags(req.stock_tags)
    meas = _measures(req.measures)

    need = {"sm": False, "pm": False}
    conds = (_filter_sql(req.filters, need) + _dim_cond_sql(req.dim_conds, need)
             + _search_sql(req.search))
    mc_where, mc_sale, mc_stk = _meas_cond_sql(req.meas_conds, meas, tags)
    for d in dims:
        _e, sm, pm = _dim_expr(d)
        need["sm"] = need["sm"] or sm
        need["pm"] = need["pm"] or pm

    # THE SCOPE RULE, and it is about the GRAIN rather than about having a filter at all. An
    # article-deep grain with nothing narrowing it is 1.52 crore rows; MAJ_CAT alone is a few
    # thousand and needs no filter to be readable.
    if (set(dims) & ARTICLE_DIMS) and not conds:
        raise HTTPException(
            400, "This grain is one row per article. Add a filter - a store, a category, a "
                 "division - or group by something above the article: unfiltered, the estate is "
                 "about 1.52 crore rows over 30 days, past what a sheet or a grid can hold.")

    dim_sel, _ = [], None
    for d in dims:
        expr, _sm, _pm = _dim_expr(d)
        dim_sel.append(f'{expr} AS "{d}"')
    dim_csv = ", ".join(dim_sel)
    grp = ", ".join(str(i + 1) for i in range(len(dims)))
    where = f"f.DT BETWEEN {base._q(a)}::DATE AND {base._q(b)}::DATE"
    cond_sql = (" AND " + " AND ".join(conds)) if conds else ""
    joins = _lookups(need)

    sale_sel = [f'ROUND(SUM(CASE WHEN f.DT = {base._q(d)}::DATE THEN f.{m} END),4) '
                f'AS "{day_col(d, MEAS_LABEL[m])}"'
                for d in days for m in meas]
    sale_sel += [f'SUM(f.{c}) AS "{al}"' for c, al in mc_sale]
    sale = (f"(SELECT {dim_csv}, {', '.join(sale_sel)} FROM {base.SALE_TBL} f{joins} "
            f"WHERE {where}{cond_sql} GROUP BY {grp})")

    # base.tag_measure_sql, so a composite / rule / pick tag means here exactly what it means on
    # the variant grid. multi_store=True because this grain can sit ABOVE the store - see the note
    # on PICK_TAGS in sf_var_combined.
    stk_sel = []
    mode = _stock_total_mode(req)
    for d in days:
        day_pred = f"f.DT = {base._q(d)}::DATE"
        for m, col in (("Q", "STOCK_Q"), ("V", "STOCK_V")):
            per_tag = []
            for t in tags:
                expr = base.tag_measure_sql(t, col, "f.KPI",
                                            bool(getattr(req, "store_only", False)),
                                            multi_store=True,
                                            dc_only=bool(getattr(req, "dc_only", False)),
                                            v2r_plants=getattr(req, "v2r_plants", None))
                # The day test goes INSIDE the tag's own CASE. Wrapping the finished expression in
                # one instead would let a pick tag choose across the whole range first and then
                # attribute that answer to a single day.
                expr = expr.replace("CASE WHEN ", f"CASE WHEN {day_pred} AND ")
                per_tag.append((t, expr))
            if mode != "only" or len(tags) == 1:
                for t, expr in per_tag:
                    stk_sel.append(f'{expr} AS "{day_col(d, f"{t}-STK-{m}")}"')
            if mode in ("add", "only") and len(tags) > 1:
                # NVL on each term, then NULL back when every one of them was NULL: a total of
                # nothing is blank, exactly like the per-tag cells beside it, rather than a 0 that
                # reads as "counted, and it was zero".
                body = " + ".join(f"NVL({e},0)" for _t, e in per_tag)
                any_ref = "COALESCE(" + ", ".join(e for _t, e in per_tag) + ")"
                stk_sel.append(f'IFF({any_ref} IS NULL, NULL, ROUND({body},4)) '
                               f'AS "{day_col(d, f"{STK_TOTAL}-STK-{m}")}"')
    # The measure conditions' hidden stock columns: the tag (or the total) on the LAST day.
    for cid, al in mc_stk:
        kind, t = cid.split(":", 1)
        col = "STOCK_Q" if kind == "STK_Q" else "STOCK_V"
        last = f"f.DT = {base._q(days[0])}::DATE"
        exprs = []
        for tg in (tags if t == STK_TOTAL else [t]):
            e = base.tag_measure_sql(tg, col, "f.KPI", bool(getattr(req, "store_only", False)),
                                     multi_store=True, dc_only=bool(getattr(req, "dc_only", False)),
                                     v2r_plants=getattr(req, "v2r_plants", None))
            exprs.append(e.replace("CASE WHEN ", f"CASE WHEN {last} AND "))
        body = " + ".join(f"NVL({e},0)" for e in exprs)
        stk_sel.append(f'IFF(COALESCE({", ".join(exprs)}) IS NULL, NULL, {body}) AS "{al}"')
    needs_plant = any(base.tag_needs_plant(t, bool(getattr(req, "store_only", False)),
                                           bool(getattr(req, "dc_only", False)),
                                           getattr(req, "v2r_plants", None))
                      for t in tags)
    pt_join = (f" LEFT JOIN {base.plant_lookup_sql()} pt ON pt.ST_CD = TRIM(f.ST_CD)"
               if needs_plant else "")
    kpi_scan = base.scan_filter(tags, "f.KPI")
    stk = (f"(SELECT {dim_csv}, {', '.join(stk_sel)} FROM {base.STOCK_TBL} f{joins}{pt_join} "
           f"WHERE {where}{kpi_scan}{cond_sql} GROUP BY {grp})")

    on = " AND ".join(f'EQUAL_NULL(sa."{d}", st."{d}")' for d in dims)
    cols = list(dims)
    proj = [f'COALESCE(sa."{d}", st."{d}") AS "{d}"' for d in dims]
    blocks = day_cols(tags, meas, mode)
    sale_set = {MEAS_LABEL[m] for m in meas}
    for d in days:
        for c in blocks:
            name = day_col(d, c)
            cols.append(name)
            proj.append(f'{"sa" if c in sale_set else "st"}."{name}"')

    joined = f"{sale} sa FULL OUTER JOIN {stk} st ON {on}"
    if mc_where:
        joined += " WHERE " + " AND ".join(mc_where)
    sc = str(req.sort_col or "")
    direction = "DESC" if str(req.sort_dir or "").lower() == "desc" else "ASC"
    dim_order = ", ".join(f'COALESCE(sa."{d}", st."{d}")' for d in dims)
    if sc in cols[len(dims):]:
        # NULLS LAST: most cells on this sheet are blank (a line sells on a few of its thirty
        # days), and Snowflake sorts DESC nulls-first - so "biggest day first" would open on a page
        # of dashes and read as an empty report.
        side = "sa" if sc.rsplit(" ", 1)[-1] in sale_set else "st"
        order = f'{side}."{sc}" {direction} NULLS LAST, {dim_order}'
    elif sc in dims:
        order = f'COALESCE(sa."{sc}", st."{sc}") {direction} NULLS LAST, {dim_order}'
    else:
        order = dim_order

    sql = f"SELECT {', '.join(proj)} FROM {joined} ORDER BY {order}"
    count_sql = f"SELECT COUNT(*) FROM {joined}"
    return sql, count_sql, cols, days, dims, tags, meas


@router.get("/params")
def trend_params(refresh: int = 0):
    """What is loaded, and how thin each day is.

    THE PER-DAY STORE COUNT IS THE POINT, not decoration. A day column can be blank because the
    store held nothing or because the extract did not run, and only this tells the two apart - so
    the page prints it above the grid rather than leaving a reader to infer it."""
    conn = base._conn()
    try:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT DT::VARCHAR, COUNT(DISTINCT ST_CD)
              FROM {base.STOCK_TBL}
             WHERE DT >= DATEADD(day, -60, CURRENT_DATE()) AND KPI IN ('0001','0006')
             GROUP BY 1 ORDER BY 1 DESC""")
        stock_days = [{"day": r[0], "stores": int(r[1] or 0)} for r in cur.fetchall()]
        cur.execute(f"""
            SELECT DT::VARCHAR, COUNT(DISTINCT ST_CD)
              FROM {base.SALE_TBL}
             WHERE DT >= DATEADD(day, -60, CURRENT_DATE())
             GROUP BY 1 ORDER BY 1 DESC""")
        sale_days = [{"day": r[0], "stores": int(r[1] or 0)} for r in cur.fetchall()]

        cur.execute(f"SELECT DISTINCT TRIM(ST_CD) FROM {base.STOCK_TBL} "
                    f"WHERE DT >= DATEADD(day, -30, CURRENT_DATE()) ORDER BY 1")
        stores = [r[0] for r in cur.fetchall()]
        cur.execute(f"SELECT DISTINCT TRIM(MAJ_CAT) FROM {base.STOCK_TBL} "
                    f"WHERE DT >= DATEADD(day, -30, CURRENT_DATE()) "
                    f"AND NVL(TRIM(MAJ_CAT),'') <> '' ORDER BY 1")
        maj_cats = [r[0] for r in cur.fetchall()]
        cur.execute(f"SELECT DISTINCT TRIM(SEG) FROM {base.STOCK_TBL} "
                    f"WHERE DT >= DATEADD(day, -30, CURRENT_DATE()) "
                    f"AND NVL(TRIM(SEG),'') <> '' ORDER BY 1")
        segs = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    latest = stock_days[0]["day"] if stock_days else None
    lo = None
    if latest:
        lo = (datetime.fromisoformat(latest).date() - timedelta(days=DEFAULT_DAYS - 1)).isoformat()
    return {"stockDays": stock_days, "saleDays": sale_days,
            "latest": latest, "defaultFrom": lo, "defaultTo": latest,
            "stores": stores, "majCats": maj_cats, "segs": segs,
            # THE PICKER VOCABULARIES, grouped exactly as the panels draw them. Published rather
            # than hard-coded on the page for the reason every other list here is: a dimension
            # added to the master shows up in the picker without a frontend change, and the two
            # cannot disagree about what a dimension is called.
            "dims": [{"label": g, "cols": cols} for g, cols in DIM_GROUPS],
            "allDims": ALL_DIMS, "defaultDims": DEFAULT_DIMS,
            "articleDims": sorted(ARTICLE_DIMS),
            "measures": SALE_MEASURES, "measureLabels": MEAS_LABEL,
            "defaultMeasures": DEFAULT_MEASURES,
            # The SAME stock-tag catalogue the variant grid offers, composites, rule tags and
            # picks included - so TOTAL_STORE_STOCK or MSA can be a column here too.
            "stockTags": (list(base.STOCK_TAGS) + list(base.COMPOSITE_TAGS)
                          + list(base.RULE_TAGS) + list(base.PICK_TAGS)),
            "defaultStockTags": DEFAULT_STK_TAGS,
            # The plants under V2R, each with what the master types it - so this page can offer
            # the same "which plants count" pick the report builder does, from the same list.
            "v2rPlants": base.v2r_plants_catalogue(),
            "dayCols": day_cols(DEFAULT_STK_TAGS, DEFAULT_MEASURES), "keyCols": KEY_COLS,
            "maxDays": MAX_DAYS, "defaultDays": DEFAULT_DAYS, "maxRows": MAX_ROWS}


@router.post("/result")
def trend_result(req: TrendReq):
    page = max(1, int(req.page or 1))
    limit = max(1, min(int(req.limit or 200), MAX_LIMIT))
    sql, count_sql, cols, days, dims, tags, meas = _build(req)
    conn = base._conn()
    try:
        cur = conn.cursor()
        cur.execute(count_sql)
        total = int(cur.fetchone()[0] or 0)
        # THE CAP IS CHECKED BEFORE THE PAGE IS READ, so a grain nobody meant to ask for costs one
        # aggregate rather than a page of a million-row sort. The number is in the message because
        # "too many rows" without it leaves the reader guessing what to narrow.
        if total > MAX_ROWS:
            raise HTTPException(
                400, f"{total:,} rows at this grain - the page caps at {MAX_ROWS:,}. Narrow the "
                     f"filters, or group by something above the article.")
        cur.execute(f"{sql} LIMIT {limit} OFFSET {(page - 1) * limit}")
        names = [c[0] for c in cur.description]
        rows = [[base._fmt_str(v) for v in r] for r in cur.fetchall()]
    finally:
        conn.close()
    return {"columns": names, "rows": rows, "total": total, "page": page, "limit": limit,
            "days": days, "dims": dims, "stockTags": tags, "measures": meas,
            "dayCols": day_cols(tags, meas, _stock_total_mode(req)), "keyCols": dims,
            "stockTotal": _stock_total_mode(req), "stockTotalCol": STK_TOTAL,
            # The families behind the total, so the page can warn when a pick is not addable -
            # 0001 + 0006 are two disjoint ET locations; 0001 + LQ_V01 counts the same stock twice.
            "tagFamilies": {t: base.tag_family(t) for t in tags}}


def _combos(a, b, cols, filters, dim_conds=None):
    """The distinct COMBINATIONS of the split columns that actually occur, with a row count each.

    A folder dump asks this before it writes anything, so it plans one file per combination that
    EXISTS rather than crossing every value of every level - two levels crossed blindly order
    hundreds of files that are nearly all empty. The count rides along in the same GROUP BY so a
    caller can refuse a slice it cannot deliver whole, instead of discovering it file by file.

    THE STOCK FACT, like the single-dimension list above: it is the wider of the two, so a
    combination that exists only on unsold stock still gets its file."""
    for c in cols:
        if c not in ALL_DIMS:
            raise HTTPException(400, f"cannot split on '{c}' - it is not a dimension here")
    need = {"sm": False, "pm": False}
    conds = _filter_sql(filters, need) + _dim_cond_sql(dim_conds, need)
    exprs = []
    for c in cols:
        e, sm, pm = _dim_expr(c)
        need["sm"] = need["sm"] or sm
        need["pm"] = need["pm"] or pm
        exprs.append(f"NVL(NULLIF(TRIM({e}::VARCHAR),''),'(blank)')")
    where = f"f.DT BETWEEN {base._q(a)}::DATE AND {base._q(b)}::DATE"
    cond_sql = (" AND " + " AND ".join(conds)) if conds else ""
    order = ", ".join(str(i + 1) for i in range(len(cols)))
    sql = (f"SELECT {', '.join(exprs)}, COUNT(*) FROM {base.STOCK_TBL} f{_lookups(need)} "
           f"WHERE {where}{cond_sql} GROUP BY {order} ORDER BY {order}")
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
def trend_values(req: ValuesReq):
    """The values a filter panel offers for one dimension, within the range and the OTHER filters.

    NARROWED BY THE FILTERS ALREADY SET, minus this dimension's own - which is what makes the
    panels usable in sequence: pick a division and the category list is that division's categories,
    not all 2,901. Excluding its own filter is what lets you widen a choice you have already made
    without clearing it first."""
    a, b, _days_ = _days(req.from_date, req.to_date)
    cols = [str(c).strip().upper() for c in (req.columns or []) if str(c).strip()]
    if cols:
        return _combos(a, b, cols, req.filters, req.dim_conds)
    d = str(req.dim or "").strip().upper()
    if d not in ALL_DIMS:
        raise HTTPException(400, f"unknown dimension '{d}'")
    others = {k: v for k, v in (req.filters or {}).items() if str(k).strip().upper() != d}
    need = {"sm": False, "pm": False}
    conds = _filter_sql(others, need)
    expr, sm, pm = _dim_expr(d)
    need["sm"] = need["sm"] or sm
    need["pm"] = need["pm"] or pm
    s = str(req.search or "").strip()
    if s:
        conds.append(f"UPPER({expr}::VARCHAR) LIKE UPPER({base._q('%' + s + '%')})")
    where = f"f.DT BETWEEN {base._q(a)}::DATE AND {base._q(b)}::DATE"
    cond_sql = (" AND " + " AND ".join(conds)) if conds else ""
    # THE STOCK FACT, not the sale one: it is the wider of the two (every line held, not only the
    # lines that sold), so a value that exists only on unsold stock still reaches the picker.
    sql = (f"SELECT DISTINCT NVL(NULLIF(TRIM({expr}::VARCHAR),''),'(blank)') V "
           f"FROM {base.STOCK_TBL} f{_lookups(need)} WHERE {where}{cond_sql} "
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


def _fmt_day(d):
    """'2026-09-16' -> '16-Sep-26', the spelling the workbook's date band uses."""
    return datetime.fromisoformat(d).strftime("%d-%b-%y")


@router.post("/export")
def trend_export(req: TrendReq, fmt: str = "xlsx"):
    """The whole scoped selection, in the workbook's own two-row header.

    ROW 1 IS THE DATE BAND - merged across its seven columns in xlsx - AND ROW 2 THE MEASURE NAMES,
    the same shape as LAST-30-DAYS SALE-STOCK-TREND FORMAT.xlsb, so a sheet taken here drops into
    the place that file came from. CSV writes the identical two rows unmerged, which is all a CSV
    can carry and is what Excel shows the same way.

    ROW 2 CARRIES EXCEL'S OWN AUTOFILTER across every column, grain and measure alike, so the
    sheet opens ready to be narrowed to a store, a category or a segment without anyone adding
    the filter by hand. It sits on the measure-name row rather than the date band, because a
    merged cell cannot be a filter header.

    XLSXWRITER IN CONSTANT-MEMORY MODE, not openpyxl, and that is a size decision rather than a
    preference: one store over thirty days is ~30,000 rows x 212 columns = 6.4M cells, which
    openpyxl holds in memory as objects. Constant memory flushes each row to disk as it is written
    - see df_gandola.py, which learned the same lesson. Rows must therefore be written strictly
    top to bottom, which is why both header rows and every merge are done before the first data
    row.
    """
    if fmt not in ("xlsx", "csv"):
        raise HTTPException(400, "fmt must be xlsx or csv")
    sql, count_sql, cols, days, dims, tags, meas = _build(req)
    blocks = day_cols(tags, meas, _stock_total_mode(req))
    band = [""] * len(dims)
    for d in days:
        band += [_fmt_day(d)] + [""] * (len(blocks) - 1)
    head = list(dims) + [c for _d in days for c in blocks]

    conn = base._conn()
    cur = conn.cursor()
    if fmt == "csv":
        # The connection stays open behind the generator - the same shape _stream_csv has
        # everywhere else in this service. Closing it here would end the cursor mid-stream.
        cur.execute(sql)
        return _stream_csv_two_row(cur, band, head, "DAY_WISE_SALE_STOCK_TREND.csv")

    import tempfile
    import os
    import xlsxwriter
    try:
        cur.execute(count_sql)
        total = int(cur.fetchone()[0] or 0)
        if total > XLSX_MAX_ROWS:
            raise HTTPException(
                400, f"{total:,} rows is past Excel's own limit of {XLSX_MAX_ROWS:,} - narrow the "
                     f"stores or the categories, or take it as CSV.")
        cur.execute(sql)
        fd, path = tempfile.mkstemp(suffix=".xlsx", prefix="day_trend_")
        os.close(fd)
        wb = xlsxwriter.Workbook(path, {"constant_memory": True, "use_zip64": True})
        ws = wb.add_worksheet("Sheet1")
        band_fmt = wb.add_format({"bold": True, "align": "center", "bg_color": "#DDEBF7",
                                  "font_size": 8})
        head_fmt = wb.add_format({"bold": True, "font_size": 8})
        # ROW 0 BEFORE ROW 1, and both before any data. Constant memory flushes a row to disk the
        # moment the next one is touched, so a write back to an earlier row is silently DROPPED -
        # which is exactly how the date band came out blank the first time this ran.
        bands = [(len(dims) + i * len(blocks), len(blocks), _fmt_day(d))
                 for i, d in enumerate(days)]
        for c0, width, text in bands:
            # merge_range needs at least two cells; a single-measure band is a plain write, which
            # is a real case now that the block is picked rather than fixed at seven.
            if width > 1:
                ws.merge_range(0, c0, 0, c0 + width - 1, text, band_fmt)
            else:
                ws.write(0, c0, text, band_fmt)
        for j, v in enumerate(head):
            ws.write(1, j, v, head_fmt)
        ws.set_column(0, len(head) - 1, 12)
        # Freeze below the two header rows and to the right of the grain columns, whatever the
        # grain turned out to be - "2, 2" was the two key columns of the fixed layout.
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
        # COLUMN-WISE FILTER DROPDOWNS, on ROW 1 - the measure-name row, not the merged date band
        # above it. It is set AFTER the data because the range needs the last row, and that is safe
        # in constant memory: autofilter records a range on the worksheet and is written into the
        # sheet XML at close, so unlike a cell write it does not reach back into a flushed row.
        # Row 1 to row 1 when nothing came back is a valid header-only filter, not an error.
        ws.autofilter(1, 0, max(r - 1, 1), len(head) - 1)
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
        headers={"Content-Disposition":
                 'attachment; filename="DAY_WISE_SALE_STOCK_TREND.xlsx"'})


def _stream_csv_two_row(cur, band, head, filename):
    """The service's _stream_csv, with the workbook's date band written above the column names."""
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

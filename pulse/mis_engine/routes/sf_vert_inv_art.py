"""
Snowflake MIS — VERTICAL INVENTORY REPORT, ARTICLE LEVEL (mounted at /sf-vert-inv-art).

The article-level duplicate of /sf-vert-inv: ONE ROW PER PLANT x ARTICLE x SLOC (with the article's
SEG / DIV / SUB_DIV / MJ / GEN_ART / CLR riding along) and the same opening, closing and
negative-closing stock in quantity and value. Every sourcing rule - which plant a source row lands
on, the SEG / DIV universe, positive-only OP / CL, MAP valuation for LQUA / PTL / pick - is the
parent's, built by the very same SQL (sf_vert_inv._build_sql(article=True)); see that module's
docstring. The positive / negative split is judged per article at plant x SLOC there as well, so
summing this table over ARTICLE gives the parent's MIS_VERT_INV to the unit.

ET_STOCK_DATA LGORTS DIFFER FROM THE PARENT (user rule, 2026-09-25): STORE plants take LGORT 0001-0099,
DC plants take LGORT 0032 / 0042 / 0044 / 0099 and HUB plants 0032 / 0033 / 0044 / 0099 (user rule, 2026-09-25;
the DC LQUA bins carry LGORT 0001 only, so none of these is counted twice) (the parent takes every DC / HUB LGORT but 0001). So the
ET_STOCK_DATA rows of DCs and hubs no longer tie to the MJ-level report; every other source does.
The article-level MAP used to value LQUA / PTL / pick still reads every ET LGORT.

OP / CL ARE NET here (user ask, 2026-09-25): an article below zero shows its negative in OP-STK / CL-STK
too, not only in NEG-CL (the parent counts positive articles only). NEG-CL still holds the negatives
alone, so CL - NEG = the parent's positive-only CL.

PTL SHOWS TWICE TOO (user ask 2026-09-26): at the destination store (DWERKS) and at the sending RDC
(SWERKS, the V06 ... V25 put-to-light staging types the LQUA rows leave out), SLOC PTL-<LGTYP> at both.

RDC-PRD SHOWS TWICE (user choice 2026-09-25): at the receiving store (SITE, the sample's layout) AND at
the sending RDC (VSTEL, where the packed HUs physically are) - so, like HUB-PRD, a grand total over
both plant types counts those pieces twice; filter ST_TYP to separate them.

UNIVERSE DIFFERS FROM THE PARENT TOO: SEG APP / GM / MKT **and NT** (hard tags, packing, carry bags -
61.7M EA / Rs 107 Cr at stores on 24-Sep, which the sample drops), DIV not SCRAP, pieces (EA) only.

IN / OUT (user ask, 2026-09-25) - quantity moved between the OP and the CL reading, per row:
  ET_STOCK_DATA rows  MATDOC, BUDAT in (ET OP day, ET CL day] - an ET date is the END-of-day stock
                      (23->24 Sep: 2,033 of 2,076 store x LGORT pairs tie OP + IN - OUT = CL). Real
                      LGORTs: RECORD_TYPE MDOC, BSTAUS_SG 'A' (unrestricted), own stock. LGORT 0099 is
                      NOT a storage location (no T001L row, never a MATDOC LGORT): ET_STOCK_DATA puts
                      STOCK IN TRANSIT there, which MATDOC posts with a BLANK LGORT and BSTAUS_SG H / F
                      (641 receipt into transit, GR out of it), on both MDOC and MDOC_CP records - so
                      those lines are the 0099 moves (ties at 460 / 467 stores, 23 / 24 hubs, 3 / 5
                      DCs at plant level, 23->24 Sep). S = IN, H = OUT. Same LGORT rule as the stock rows.
  LQUA bin rows (DC)  LTAP (the GOLD.V_INV_WM_MOVE_MAJCAT logic): NLTYP / NLPLA + NISTM = IN,
                      VLTYP / VLPLA + VISTM = OUT, confirmed (QDATU + QZEIT) after the OP LQUA run and
                      up to the CL run, mapped to MSA_ / PROCESS_GRT / SEASONAL like the stock rows.
                      This does NOT tie to the LQUA snapshots (23->24 Sep: 10 of 44 moving storage
                      types; no time shift fixes it) - kept at the user's choice as the real moves.
  PTL / PRD / HUB PRD / PICK are RFC snapshots with no movement document: IN / OUT stay 0.

MAP (user ask, 2026-09-25) is the article's moving average price averaged over its plants in
BRONZE.MBEW: AVG(VERPR / PEINH) per MATNR. MBEW is one row per MATNR x BWKEY (no BWTAR split),
PEINH is always 1 and VERPR never 0. It is stored per row at build time (averaging 77M MBEW rows
per page read would be too slow) and shown only while ARTICLE is a Group-by column.

ARTICLE is the variant (ARTICLE_NUMBER, ET_STOCK_DATA's MATNR); GEN_ART + CLR are the generic
article, so the Group-by chips can roll up to it. The slice is ~20M+ rows per as-of date (ET alone
is ~22M rows a day), stored in BRONZE.MIS_VERT_INV_ART and built on the first open of a date.
ARTICLE / GEN_ART are too many for filter pick-lists: the search box matches them instead.
"""
import csv
import io
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import sf_var_combined as base
from . import sf_vert_inv as vi
from .sf_gandola import _na_row, _stream_csv, _text_cols

logger = logging.getLogger(__name__)
router = APIRouter()

DB = vi.DB
OUT_TBL = f"{DB}.BRONZE.MIS_VERT_INV_ART"
LOG_TBL = f"{DB}.BRONZE.MIS_VERT_INV_ART_LOG"
STORE_TBL = vi.STORE_TBL
PROD_TBL = vi.PROD_TBL
T001L_TBL = vi.T001L_TBL

# Default Group-by, in default order. The page may send any subset IN ANY ORDER (the grid's column
# order), plus "PM:<column>" for any other VW_PRODUCT_MASTER column, joined on ART at read time.
DIMS = ["ST_CD", "ST_TYP", "SEG", "DIV", "SUB_DIV", "MJ", "ART_TYPE", "GEN_ART", "CLR", "ART", "SLOC", "SRC"]
PM_PREFIX = "PM:"
# Product-master columns already stored as a dim (or the key) - not offered again under PM:.
_PM_SKIP = {"ARTICLE_NUMBER", "SEGMENT", "DIVISION", "SUB_DIVISION", "MAJOR_CATEGORY_DESCRIPTION",
            "GEN_ARTICLE_NUMBER", "COLOR", "ARTICLE_DESCRIPTION", "ARTICLE_TYPE"}
_pm_cols = {"at": 0, "cols": []}
# STORE-MASTER columns, the same way: 'SM:<col>' joins SLV_MIS_STORE_MASTER on the plant at read time.
# The vocabulary and its order (the store hierarchy first) are the MIS reports' own STORE_ATTRS.
SM_PREFIX = "SM:"
_SM_FIRST = ["ZONE", "ZONE_RETAIL", "REG", "AREA", "STATE", "RDC_CD", "RDC_NM", "ST_STAT", "OLD_NEW"]
_SM_ALL = [c for c in (base.STORE_ATTRS or []) if c != "ST_NM"]
SM_COLS = [c for c in _SM_FIRST if c in _SM_ALL] + [c for c in _SM_ALL if c not in _SM_FIRST]
# The product-master columns that ARE the product hierarchy, offered first (the rest follow).
PM_HIERARCHY = ["MERCHANDISE_CATEGORY_CODE", "MERCHANDISE_CATEGORY_DESCRIPTION", "MAJOR_CATEGORY_CODE",
                "SUB_CATEGORY_CODE", "SUB_CATEGORY_DESCRIPTION", "GEN_ARTICLE_DESC", "RANGE_SEGMENT",
                "MRP", "SIZE1", "NEW_SIZE_DESC", "NEW_COLOR_DESC", "VENDOR_CODE", "VENDOR_NAME",
                "MERGE_VENDOR_CODE", "MERGE_VENDOR_NAME"]
# The stored order (the build SQL's column order) and the display order (IN / OUT sit between OP and CL).
STORED_MEASURES = vi.MEASURES + ["IN_Q", "OUT_Q"]
MEASURES = ["OP_Q", "OP_V", "IN_Q", "OUT_Q", "CL_Q", "CL_V", "NEG_Q", "NEG_V"]
LABELS = {**vi.LABELS, "GEN_ART": "GEN_ART", "CLR": "CLR", "ART": "ARTICLE", "ART_DESC": "ARTICLE_DESC",
          "MAP": "MAP", "IN_Q": "IN-Q", "OUT_Q": "OUT-Q"}
# Per-article attributes that ride along with ART: shown, sorted on, never grouped by.
ATTR_AGG = {"MAP": "MAX(v.MAP)"}
MBEW_TBL = f"{DB}.BRONZE.MBEW"
MATDOC_TBL = f"{DB}.BRONZE.MATDOC"
LTAP_TBL = f"{DB}.BRONZE.LTAP"
# ART_TYPE (the product master's ARTICLE_TYPE, stored per row since 2026-09-26) - the MIS "Art type" pick.
FILTER_COLS = ["ST_TYP", "ST_CD", "SEG", "DIV", "SUB_DIV", "MJ", "ART_TYPE", "CLR", "SLOC", "SRC"]
MAX_LIMIT = 50_000

# Stores: LGORT 1..99; DC (RDC): 0032 Refinishing, 0042 Winter GRT, 0044 DC short/excess and 0099
# (in transit); HUB: 0032 / 0033 / 0044 / 0099 (user rule 2026-09-26; hub 0001 stays out - it is the
# same pieces as ZWM_PRD_HUB's HUB_FRESH / HUB_GRT, 409K of 463K article rows equal on 25-Sep).
# See the docstring.
ET_LGORT_RULE = ("((sm.ST_TYP = 'STORE' AND TRY_TO_NUMBER(et.SLOC) BETWEEN 1 AND 99)"
                 " OR (sm.ST_TYP = 'DC' AND et.SLOC IN ('0032','0042','0044','0099'))"
                 " OR (sm.ST_TYP = 'HUB' AND et.SLOC IN ('0032','0033','0044','0099')))")

# SEG APP / GM / MKT + NT, DIV not SCRAP, and only articles counted in pieces (MARA MEINS 'EA') -
# user choice 2026-09-25. Left out: FAB / ACC (fabric, trims), blank-SEG trims (categories absent from
# ZMC_MASTER), SCRAP, and the few NT items in M / L / PAC / KG, so a Q total never mixes units.
UNIVERSE = ("TRIM(SEGMENT) IN ('APP','GM','MKT','NT') AND NVL(TRIM(DIVISION),'') <> 'SCRAP'"
            f" AND ARTICLE_NUMBER IN (SELECT LTRIM(TRIM(MATNR),'0') FROM {DB}.BRONZE.MARA WHERE TRIM(MEINS) = 'EA')")

_build_lock = threading.Lock()
_ddl_done = {"ok": False}
_opts_cache = {}
_OPTS_TTL_S = 15 * 60

_q = vi._q
_as_of = vi._as_of
_one = vi._one
_latest_et = vi._latest_et


def _ensure_tables(cur):
    if _ddl_done["ok"]:
        return
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {OUT_TBL} (
        AS_OF_DATE DATE NOT NULL, ST_CD VARCHAR(10) NOT NULL, SEG VARCHAR(20), "DIV" VARCHAR(40),
        SUB_DIV VARCHAR(60), MJ VARCHAR(80), GEN_ART VARCHAR(40), CLR VARCHAR(60), ART VARCHAR(40) NOT NULL,
        SLOC VARCHAR(40) NOT NULL, SRC VARCHAR(40),
        OP_Q NUMBER(18,3), OP_V NUMBER(20,2), CL_Q NUMBER(18,3), CL_V NUMBER(20,2),
        NEG_Q NUMBER(18,3), NEG_V NUMBER(20,2), BUILT_AT TIMESTAMP_NTZ)
        CLUSTER BY (AS_OF_DATE)""")
    cur.execute(f"ALTER TABLE {OUT_TBL} ADD COLUMN IF NOT EXISTS MAP NUMBER(18,2)")
    cur.execute(f"ALTER TABLE {OUT_TBL} ADD COLUMN IF NOT EXISTS IN_Q NUMBER(18,3)")
    cur.execute(f"ALTER TABLE {OUT_TBL} ADD COLUMN IF NOT EXISTS OUT_Q NUMBER(18,3)")
    cur.execute(f"ALTER TABLE {OUT_TBL} ADD COLUMN IF NOT EXISTS ART_TYPE VARCHAR(20)")
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {LOG_TBL} (
        AS_OF_DATE DATE, BUILT_AT TIMESTAMP_NTZ, BUILT_BY VARCHAR(100), SECS NUMBER(10,1),
        ROWS_ NUMBER, SOURCES VARIANT, TOTALS VARIANT)""")
    _ddl_done["ok"] = True


def _run_ts(run):
    """'dot24-202609241835-LQUA' -> '20260924183500' (the run id carries its IST start)."""
    m = re.search(r"(\d{12})", str(run or ""))
    return m.group(1) + "00" if m else None


def _moves_sql(src):
    """The IN / OUT legs added to the build's `rws` (see the docstring)."""
    legs = []
    et_cl = src.get("et_cl")
    if et_cl:
        cl = et_cl.strftime("%Y%m%d")
        op = src["et_op"].strftime("%Y%m%d") if src.get("et_op") else (et_cl - timedelta(days=1)).strftime("%Y%m%d")
        legs.append(f"""
  UNION ALL
  SELECT IFF(et.SHKZG = 'S', 'IN', 'OUT'), et.ST_CD, et.SLOC, 'ET_STOCK_DATA', et.ART, et.Q, NULL
    FROM (SELECT TRIM(m.WERKS) ST_CD, NVL(NULLIF(TRIM(m.LGORT),''), '0099') SLOC, TRIM(m.SHKZG) SHKZG,
                 LTRIM(TRIM(m.MATNR),'0') ART, SUM(m.MENGE) Q
            FROM {MATDOC_TBL} m
           WHERE m.BUDAT > '{op}' AND m.BUDAT <= '{cl}' AND NVL(TRIM(m.SOBKZ),'') = '' AND m.SHKZG IN ('S','H')
             AND ((m.RECORD_TYPE = 'MDOC' AND m.BSTAUS_SG = 'A' AND NVL(TRIM(m.LGORT),'') <> '')
               OR (m.RECORD_TYPE IN ('MDOC','MDOC_CP') AND m.BSTAUS_SG IN ('H','F') AND NVL(TRIM(m.LGORT),'') = ''))
           GROUP BY 1,2,3,4) et
    JOIN sm ON sm.ST_CD = et.ST_CD
   WHERE {ET_LGORT_RULE}""")
    b = _run_ts(src.get("lq_cl_run"))
    if b:
        a = _run_ts(src.get("lq_op_run")) or (datetime.strptime(b, "%Y%m%d%H%M%S") - timedelta(days=1)).strftime("%Y%m%d%H%M%S")
        days = ", ".join(_q(x) for x in sorted({a[:8], b[:8]} | {
            (datetime.strptime(a[:8], "%Y%m%d") + timedelta(days=i)).strftime("%Y%m%d")
            for i in range((datetime.strptime(b[:8], "%Y%m%d") - datetime.strptime(a[:8], "%Y%m%d")).days + 1)}))
        for k, typ, pla, qty in (("IN", "NLTYP", "NLPLA", "NISTM"), ("OUT", "VLTYP", "VLPLA", "VISTM")):
            legs.append(f"""
  UNION ALL
  SELECT '{k}', TRIM(t.WERKS), {vi._lq_slot('t.' + typ, 'g', v02_split=True)}, 'LQUA', LTRIM(TRIM(t.MATNR),'0'),
         TRY_TO_DECIMAL(t.{qty},18,3), NULL
    FROM {LTAP_TBL} t
    JOIN sm ON sm.ST_CD = TRIM(t.WERKS) AND sm.ST_TYP = 'DC'
    LEFT JOIN {vi.LAGP_TBL} g ON TRIM(g.LGNUM) = TRIM(t.LGNUM) AND TRIM(g.LGTYP) = TRIM(t.{typ}) AND TRIM(g.LGPLA) = TRIM(t.{pla})
   WHERE TRIM(t.LGNUM) = 'V2R' AND t.QDATU IN ({days}) AND t.QDATU || t.QZEIT > '{a}' AND t.QDATU || t.QZEIT <= '{b}'
     AND {vi._lq_types('t.' + typ)}""")
    return "".join(legs)


def _build(conn, d, user=None):
    t0 = time.time()
    cur = conn.cursor()
    _ensure_tables(cur)
    src = vi._source_days(cur, d)
    ds = d.isoformat()
    now = "CONVERT_TIMEZONE('Asia/Kolkata', CURRENT_TIMESTAMP())::TIMESTAMP_NTZ"
    cur.execute("BEGIN")
    try:
        cur.execute(f"DELETE FROM {OUT_TBL} WHERE AS_OF_DATE = '{ds}'")
        # ART is every source's MATNR (ET MATNR, LQUA MATNR, PTL MATNR, PRD MATNR, hub ARTICLE,
        # pick MATERIAL), leading zeros stripped - the product master's ARTICLE_NUMBER.
        cur.execute(f"""INSERT INTO {OUT_TBL} (AS_OF_DATE, ST_CD, SEG, "DIV", SUB_DIV, MJ, GEN_ART, CLR, ART,
                            SLOC, SRC, ART_TYPE, {', '.join(STORED_MEASURES)}, MAP, BUILT_AT)
                        WITH mp AS (SELECT LTRIM(TRIM(MATNR),'0') ART,
                                           AVG(TRY_TO_DECIMAL(VERPR,18,3) / NULLIF(TRY_TO_DECIMAL(PEINH,18,3),0)) MAP
                                      FROM {MBEW_TBL} WHERE TRY_TO_DECIMAL(VERPR,18,3) > 0 GROUP BY 1)
                        SELECT '{ds}'::DATE, x.*, ROUND(mp.MAP, 2), {now}
                          FROM ({vi._build_sql(src, article=True, et_rule=ET_LGORT_RULE, net=True, moves=_moves_sql(src),
                                         universe=UNIVERSE, v02_split=True, prd_at_rdc=True,
                                         ptl_at_rdc=True)}) x
                          LEFT JOIN mp ON mp.ART = x.ART""")
        cur.execute(f"SELECT COUNT(*), {', '.join(f'SUM({m})' for m in MEASURES)} FROM {OUT_TBL} WHERE AS_OF_DATE = '{ds}'")
        r = cur.fetchone()
        totals = {m: float(v or 0) for m, v in zip(MEASURES, r[1:])}
        sources = {k: base._fmt_str(v) for k, v in src.items()}
        secs = round(time.time() - t0, 1)
        cur.execute(f"""INSERT INTO {LOG_TBL} (AS_OF_DATE, BUILT_AT, BUILT_BY, SECS, ROWS_, SOURCES, TOTALS)
                        SELECT '{ds}'::DATE, {now}, {_q(user or 'system')}, {secs}, {int(r[0] or 0)},
                               PARSE_JSON({_q(json.dumps(sources))}), PARSE_JSON({_q(json.dumps(totals))})""")
        cur.execute("COMMIT")
    except Exception:
        cur.execute("ROLLBACK")
        raise
    _opts_cache.pop(ds, None)
    logger.info("[sf-vert-inv-art] built %s: %s rows in %ss", d, r[0], secs)


def _last_build(cur, d):
    cur.execute(f"""SELECT BUILT_AT, BUILT_BY, SECS, ROWS_, SOURCES, TOTALS FROM {LOG_TBL}
                     WHERE AS_OF_DATE = '{d.isoformat()}' ORDER BY BUILT_AT DESC LIMIT 1""")
    r = cur.fetchone()
    if not r:
        return None
    return {"asOf": d.isoformat(), "builtAt": base._fmt_str(r[0]), "builtBy": r[1],
            "secs": float(r[2] or 0), "rows": int(r[3] or 0),
            "sources": json.loads(r[4]) if r[4] else {}, "totals": json.loads(r[5]) if r[5] else {}}


def _resolve(conn, req_as_of, user=None):
    cur = conn.cursor()
    _ensure_tables(cur)
    d = _as_of(req_as_of) or _latest_et(cur)
    if not d:
        raise HTTPException(404, "ET_STOCK_DATA has no reading in the last 45 days")
    if isinstance(d, datetime):
        d = d.date()
    info = _last_build(cur, d)
    if info is None:
        with _build_lock:
            info = _last_build(cur, d)
            if info is None:
                _build(conn, d, user)
                info = _last_build(cur, d)
    return d, info


# ── read side ─────────────────────────────────────────────────────────────────────────────────
_BASE = (f" FROM {OUT_TBL} v"
         f" LEFT JOIN (SELECT TRIM(ST_CD) ST_CD, MAX(ST_NM) ST_NM, MAX(TRIM(ST_TYP)) ST_TYP FROM {STORE_TBL} GROUP BY 1) sm"
         f" ON sm.ST_CD = v.ST_CD"
         f" LEFT JOIN (SELECT TRIM(WERKS) WERKS, TRIM(LGORT) LGORT, MAX(LGOBE) LGOBE FROM {T001L_TBL} GROUP BY 1, 2) tl"
         f" ON tl.WERKS = v.ST_CD AND tl.LGORT = v.SLOC"
         f" LEFT JOIN (SELECT TRIM(LGORT) LGORT, MODE(LGOBE) LGOBE FROM {T001L_TBL} GROUP BY 1) tg"
         f" ON tg.LGORT = v.SLOC")
# ARTICLE_DESC is joined only when the ART column is shown - no point paying for it otherwise.
# Product-master columns (ART_DESC and any PM:<col> Group-by) come from ONE join that carries only
# the columns the grid asked for.
_EXPR = {"ST_CD": "v.ST_CD", "ST_NM": "sm.ST_NM", "ST_TYP": "sm.ST_TYP", "SEG": "v.SEG", "DIV": 'v."DIV"',
         "SUB_DIV": "v.SUB_DIV", "MJ": "v.MJ", "GEN_ART": "v.GEN_ART", "CLR": "v.CLR", "ART": "v.ART",
         "ART_TYPE": "v.ART_TYPE",
         "ART_DESC": "pd.ART_DESC", "SLOC": "v.SLOC", "SRC": "v.SRC",
         "SLOC_DESC": "COALESCE(tl.LGOBE, v.SLOC)"}


class ViReq(BaseModel):
    as_of: Optional[str] = None
    dims: Optional[list] = None
    col_filters: Optional[dict] = None
    search: Optional[str] = None
    page: int = 1
    limit: int = 200
    sort_col: Optional[str] = None
    sort_dir: Optional[str] = "asc"
    measures: Optional[list] = None      # a subset of MEASURES (a saved Inventory Report's pick); default all
    # Saved-report CONDITIONS (the MIS Reports panel's shape, misConds.condsToDef):
    #   dim_conds  [{col, op, v1, v2, vals}] on any dimension (DIMS / 'PM:<col>'), in the WHERE
    #   meas_conds [{col, op, v1, v2, vals}] on a measure's SUM, in the HAVING
    dim_conds: Optional[list] = None
    meas_conds: Optional[list] = None
    # FATHER / CHILD: the "grid" columns (any dim / PM: / SM: column not in `dims`). Each one adds a block
    # of child rows under every father row (the `dims` group) - see _grid_sql.
    grid: Optional[list] = None


_TXT_OPS = {"in", "not_in", "contains", "not_contains", "starts", "ends", "like", "not_like",
            "blank", "nonblank", "eq", "ne", "gt", "gte", "lt", "lte", "between"}
_NUM_OPS = {"gt", "gte", "lt", "lte", "eq", "ne", "between", "in", "not_in", "blank", "nonblank"}


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        raise HTTPException(400, f"'{v}' is not a number")


def _cond(expr, c, numeric):
    """One condition row as SQL over `expr` (a column, or SUM(...) for a measure)."""
    op = str(c.get("op") or "").lower()
    if op not in (_NUM_OPS if numeric else _TXT_OPS):
        raise HTTPException(400, f"operator '{op}' is not allowed on {c.get('col')}")
    lit = (lambda v: str(_num(v))) if numeric else _q
    txt = expr if numeric else f"NVL(TO_VARCHAR({expr}),'')"
    if op in ("blank", "nonblank"):
        blank = f"{expr} IS NULL" if numeric else f"{txt} = ''"
        return blank if op == "blank" else f"NOT ({blank})"
    if op in ("in", "not_in"):
        vals = [v for v in (c.get("vals") or []) if str(v).strip() != ""][:500]
        if not vals:
            return None
        sql = f"{txt} IN ({', '.join(lit(v) for v in vals)})"
        return sql if op == "in" else f"NOT ({sql})"
    v1 = c.get("v1")
    if op == "between":
        return f"{txt} BETWEEN {lit(v1)} AND {lit(c.get('v2'))}"
    if op in ("contains", "not_contains", "starts", "ends"):
        pat = {"contains": f"%{v1}%", "not_contains": f"%{v1}%", "starts": f"{v1}%", "ends": f"%{v1}"}[op]
        sql = f"UPPER({txt}) LIKE UPPER({_q(pat)})"
        return f"NOT ({sql})" if op == "not_contains" else sql
    if op in ("like", "not_like"):
        sql = f"UPPER({txt}) LIKE UPPER({_q(str(v1).replace('*', '%'))})"
        return f"NOT ({sql})" if op == "not_like" else sql
    sym = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
    return f"{txt} {sym} {lit(v1)}"


def _cond_cols(req):
    """The product-master columns the dimension conditions name - the pd join must carry them."""
    return [_norm_dim(c.get("col")) for c in (req.dim_conds or [])
            if _is_pm(_norm_dim(c.get("col") or "")) or _is_sm(_norm_dim(c.get("col") or ""))]


def _meas_filter(req, tmpl):
    """The measure conditions as one AND over `tmpl` ('SUM(v.{m})' in a HAVING, a column elsewhere)."""
    parts = []
    for c in req.meas_conds or []:
        m = str(c.get("col") or "").upper()
        if m not in MEASURES:
            raise HTTPException(400, f"'{m}' is not a measure")
        sql = _cond(tmpl.format(m=m), c, True)
        if sql:
            parts.append(sql)
    return " AND ".join(parts)


def _having(req):
    sql = _meas_filter(req, "SUM(v.{m})")
    return f" HAVING {sql}" if sql else ""


def _ms(req):
    """The measures to return, in display order. An empty / missing pick means all of them."""
    want = {str(m).upper() for m in (req.measures or [])}
    return [m for m in MEASURES if m in want] or list(MEASURES)


def _pm_columns(cur):
    """VW_PRODUCT_MASTER columns offered as extra Group-by (cached 1 h)."""
    if not _pm_cols["cols"] or time.time() - _pm_cols["at"] > 3600:
        db, sch, tbl = PROD_TBL.split(".")
        cur.execute(f"SELECT COLUMN_NAME FROM {db}.INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = '{sch}' "
                    f"AND TABLE_NAME = '{tbl}' ORDER BY ORDINAL_POSITION")
        cols = [r[0] for r in cur.fetchall() if r[0] not in _PM_SKIP]
        _pm_cols["cols"] = [c for c in PM_HIERARCHY if c in cols] + [c for c in cols if c not in PM_HIERARCHY]
        _pm_cols["at"] = time.time()
    return _pm_cols["cols"]


def _is_pm(c):
    return c.startswith(PM_PREFIX)


def _is_sm(c):
    return c.startswith(SM_PREFIX)


def _norm_dim(x):
    """'pm:size1' -> 'PM:SIZE1', 'sm:zone' -> 'SM:ZONE', 'mj' -> 'MJ'."""
    c = str(x).strip()
    for pre in (PM_PREFIX, SM_PREFIX):
        if c.upper().startswith(pre):
            return pre + c[len(pre):].upper()
    return c.upper()


def _dims(req, cur):
    pm_ok = set(_pm_columns(cur))
    ds = []
    for x in (req.dims or DIMS):
        c = _norm_dim(x)
        ok = (c in DIMS or (_is_pm(c) and c[len(PM_PREFIX):] in pm_ok)
              or (_is_sm(c) and c[len(SM_PREFIX):] in SM_COLS))
        if ok and c not in ds:
            ds.append(c)
    if not ds:
        raise HTTPException(400, "pick at least one dimension")
    return ds


def _out_cols(ds):
    cols = []
    for c in ds:
        cols.append(c)
        if c == "ST_CD":
            cols += ["ST_NM"]
        if c == "ART":
            cols += ["ART_DESC", "MAP"]
        if c == "SLOC":
            cols += ["SLOC_DESC"]
    return cols


def _from(cols, extra_pm=()):
    pm = [("ARTICLE_DESCRIPTION", "ART_DESC")] if "ART_DESC" in cols else []
    pm += [(c[len(PM_PREFIX):], c) for c in list(dict.fromkeys([*cols, *extra_pm])) if _is_pm(c)]
    sel = ", ".join(f'MAX("{src}") "{alias}"' for src, alias in pm)
    out = _BASE + (f" LEFT JOIN (SELECT ARTICLE_NUMBER ART, {sel} FROM {PROD_TBL} GROUP BY 1) pd ON pd.ART = v.ART"
                   if pm else "")
    sm = list(dict.fromkeys(c for c in [*cols, *extra_pm] if _is_sm(c)))
    if sm:
        ssel = ", ".join(f'MAX(TO_VARCHAR("{c[len(SM_PREFIX):]}")) "{c}"' for c in sm)
        out += f" LEFT JOIN (SELECT TRIM(ST_CD) ST_CD, {ssel} FROM {STORE_TBL} GROUP BY 1) sx ON sx.ST_CD = v.ST_CD"
    return out


def _where(req, d):
    conds = [f"v.AS_OF_DATE = '{d.isoformat()}'"]
    for c, vals in (req.col_filters or {}).items():
        cu = str(c).strip().upper()
        if cu not in FILTER_COLS:
            continue
        clean = [str(v) for v in (vals or []) if str(v).strip() != ""]
        if not clean:
            continue
        lits = [v for v in clean if v != "(blank)"]
        parts = []
        if lits:
            parts.append(f"{_EXPR[cu]} IN ({', '.join(_q(v) for v in lits)})")
        if "(blank)" in clean:
            parts.append(f"NVL({_EXPR[cu]},'') = ''")
        conds.append("(" + " OR ".join(parts) + ")")
    s = str(req.search or "").strip()
    if s:
        like = _q("%" + s + "%")
        pre = _q(s.lstrip("0") + "%")
        conds.append(f"(UPPER(v.ST_CD) LIKE UPPER({like}) OR UPPER(sm.ST_NM) LIKE UPPER({like}) "
                     f"OR UPPER(v.MJ) LIKE UPPER({like}) OR UPPER(v.SLOC) LIKE UPPER({like}) "
                     f"OR v.ART LIKE {pre} OR v.GEN_ART LIKE {pre})")
    for c in getattr(req, "dim_conds", None) or []:
        col = _norm_dim(c.get("col") or "")
        if _is_pm(col):
            expr = f'pd."{col}"'
        elif _is_sm(col) and col[len(SM_PREFIX):] in SM_COLS:
            expr = f'sx."{col}"'
        elif col in _EXPR and col not in ATTR_AGG:
            expr = _EXPR[col]
        else:
            raise HTTPException(400, f"'{col}' is not a dimension")
        sql = _cond(expr, c, False)
        if sql:
            conds.append(f"({sql})")
    return " WHERE " + " AND ".join(conds)


def _x(c, cols):
    if _is_pm(c):
        return f'pd."{c}"'
    if _is_sm(c):
        return f'sx."{c}"'
    if c == "SLOC_DESC" and "ST_CD" not in cols:
        return "COALESCE(tg.LGOBE, v.SLOC)"
    return _EXPR[c]


def _col_sql(c, cols):
    return f"{ATTR_AGG.get(c) or _x(c, cols)} AS \"{c}\""


def _group_by(cols):
    return ", ".join(str(i + 1) for i, c in enumerate(cols) if c not in ATTR_AGG)


def _select(cols, measures=MEASURES):
    grp = ", ".join(_col_sql(c, cols) for c in cols)
    ms = ", ".join(f"SUM(v.{m}) AS {m}" for m in measures)
    return f"SELECT {grp}, {ms}", _group_by(cols)


def _order(req, cols, measures=MEASURES):
    direction = "DESC" if str(req.sort_dir or "").lower() == "desc" else "ASC"
    sc = str(req.sort_col or "")
    if sc in cols or sc in measures:
        return f" ORDER BY \"{sc}\" {direction} NULLS LAST, " + ", ".join(f'"{c}"' for c in cols if c not in ATTR_AGG)
    return " ORDER BY " + ", ".join(f'"{c}"' for c in cols if c not in ATTR_AGG)


# -- FATHER / CHILD GRID (user ask 2026-09-26) ---------------------------------------------------
# "HA10 M_JEANS CLR WHT / HA10 M_JEANS FIT loose_fit": the father is the Group-by (ST_CD x MJ), the
# grid is a list of columns (CLR, PM:FIT ...), and every grid column adds its own block of child rows
# under each father - long form, GRID_COL = the column's name, GRID_VAL = its value. Every block sums
# to its father on its own (a blank value is kept as '(blank)', never dropped), so:
#   - each father gets a TOTAL row first (GRID_COL 'TOTAL'), and the footer totals are the TOTAL rows
#     only - summing every row would count the stock once per grid column;
#   - <m>_PCT is the child's share of its father's <m> (the TOTAL row reads 100).
# One scan: the stock rows CROSS JOIN a tiny VALUES list of the grid columns and a CASE picks each
# one's value. Measure conditions apply to the father AND to each child row (a child of a dropped
# father is dropped too); a GRID_COL condition applies to the TOTAL rows as well (TOTAL is a value on
# its tick list), a GRID_VAL one to the children only. The share is taken before either, so it stays
# a share of the whole father.
GRID_COL, GRID_VAL, GRID_TOTAL, PCT = "GRID_COL", "GRID_VAL", "TOTAL", "_PCT"
GRID_OUT = (GRID_COL, GRID_VAL)
GRID_MAX = 10
LABELS.update({GRID_COL: "GRID", GRID_VAL: "VALUE", **{m + PCT: f"{LABELS.get(m, m)} %" for m in MEASURES}})


def _grid(req, cur, dims):
    """The request's grid columns, validated like Group-by, minus any that are already a father column."""
    pm_ok = set(_pm_columns(cur))
    out = []
    for x in req.grid or []:
        c = _norm_dim(x)
        ok = (c in DIMS or (_is_pm(c) and c[len(PM_PREFIX):] in pm_ok)
              or (_is_sm(c) and c[len(SM_PREFIX):] in SM_COLS))
        if ok and c not in dims and c not in out:
            out.append(c)
    if len(out) > GRID_MAX:
        raise HTTPException(400, f"At most {GRID_MAX} grid columns")
    if not out:
        raise HTTPException(400, "The grid columns must differ from the Group by columns")
    return out


def _grid_label(c):
    return LABELS.get(c, c) if c in DIMS else c.split(":", 1)[1]


def _grid_val(grid, cols):
    whens = " ".join(f"WHEN {i + 1} THEN TO_VARCHAR({_x(c, cols)})" for i, c in enumerate(grid))
    return f"NVL(NULLIF(TRIM(CASE g.K {whens} END), ''), '(blank)')"


def _grid_join(grid):
    vals = ", ".join(f"({i + 1}, {_q(_grid_label(c))})" for i, c in enumerate(grid))
    return f" CROSS JOIN (SELECT column1 K, column2 N FROM VALUES {vals}) g"


def _split_grid_conds(req):
    """(req without the GRID_COL / GRID_VAL conditions, those conditions) - _where knows no GRID_*."""
    conds = list(getattr(req, "dim_conds", None) or [])
    mine = [c for c in conds if _norm_dim(c.get("col") or "") in GRID_OUT]
    rest = [c for c in conds if _norm_dim(c.get("col") or "") not in GRID_OUT]
    return req.model_copy(update={"dim_conds": rest}), mine


def _grid_sql(req, cur, d, measures, kl=False, where=None, with_total=False):
    """The father / child query: (SELECT of the visible columns, unordered; the visible columns; the
    ORDER BY over it). Inside, hidden _K (0 = TOTAL, then the grid column's pick order) and _FS (the
    father's value of a sort measure) keep each father's TOTAL row first and its blocks together.
    `where` overrides the as-of WHERE (the published view reads the latest date). The TOTAL rows are
    dropped from the output (user ask 2026-09-28: "total not required") unless `with_total` - the
    footer totals still read them."""
    base_req, gconds = _split_grid_conds(req)
    dims = _dims(req, cur)
    grid = _grid(req, cur, dims)
    cols = _out_cols(dims)
    keys = [c for c in cols if c not in ATTR_AGG]
    where = _where(base_req, d) if where is None else where
    frm = _from(cols, [*_cond_cols(base_req), *grid])
    n = len(cols)
    c_grp = ", ".join([*(str(i + 1) for i, c in enumerate(cols) if c not in ATTR_AGG),
                       str(n + 1), str(n + 2), str(n + 3)])
    c_sql = (f"SELECT {', '.join(_col_sql(c, cols) for c in cols)}, g.K AS _K, g.N AS {GRID_COL}, "
             f"{_grid_val(grid, cols)} AS {GRID_VAL}, {', '.join(f'SUM(v.{m}) AS {m}' for m in measures)}"
             f"{frm}{_grid_join(grid)}{where} GROUP BY {c_grp}")
    qk = ", ".join(f'"{c}"' for c in keys)
    w_sql = f"SELECT c.*, {', '.join(f'SUM({m}) OVER (PARTITION BY {qk}, _K) AS F_{m}' for m in measures)} FROM c"
    sc = str(req.sort_col or "")
    dr = "DESC" if str(req.sort_dir or "").lower() == "desc" else "ASC"
    fs_m = sc if sc in measures else None

    def scaled(x, m):
        return f"ROUND({x} / {1000 if m.endswith('_Q') else 100000}, 3)" if kl else x

    # TOTAL rows: the first block summed back to its father (every block sums to it).
    t_sel = [", ".join(f'MAX("{c}") AS "{c}"' if c in ATTR_AGG else f'"{c}"' for c in cols),
             "0 AS _K", f"'{GRID_TOTAL}' AS {GRID_COL}", f"'' AS {GRID_VAL}",
             *(f"{scaled(f'SUM({m})', m)} AS {m}" for m in measures),
             *(f"IFF(SUM({m}) = 0, NULL, 100) AS {m}{PCT}" for m in measures),
             f"{f'SUM({fs_m})' if fs_m else '0'} AS _FS"]
    ch_sel = [", ".join(f'"{c}"' for c in cols), "_K", GRID_COL, GRID_VAL,
              *(f"{scaled(m, m)} AS {m}" for m in measures),
              *(f"ROUND({m} * 100 / NULLIF(F_{m}, 0), 2) AS {m}{PCT}" for m in measures),
              f"{f'F_{fs_m}' if fs_m else '0'} AS _FS"]
    t_having = _meas_filter(req, "SUM({m})")
    ch_where = [_meas_filter(req, "F_{m}"), _meas_filter(req, "{m}")]
    t_where = []
    for g in gconds:
        col = _norm_dim(g.get("col") or "")
        ch_where.append(_cond(col, g, False))
        if col == GRID_COL:
            t_where.append(_cond(f"'{GRID_TOTAL}'", g, False))
    t_where = [x for x in t_where if x]
    ch_where = [x for x in ch_where if x]
    t_sql = (f"SELECT {', '.join(t_sel)} FROM c WHERE _K = 1{''.join(' AND ' + x for x in t_where)} "
             f"GROUP BY {qk}" + (f" HAVING {t_having}" if t_having else ""))
    ch_sql = f"SELECT {', '.join(ch_sel)} FROM w" + (f" WHERE {' AND '.join(ch_where)}" if ch_where else "")
    shown = [*cols, GRID_COL, GRID_VAL, *measures, *(m + PCT for m in measures)]
    sql = (f"WITH c AS ({c_sql}), w AS ({w_sql}) SELECT {', '.join(f'{chr(34)}{c}{chr(34)}' for c in shown)}, _K, _FS "
           f"FROM ({t_sql} UNION ALL {ch_sql})")
    if not with_total:
        sql = f"SELECT * FROM ({sql}) WHERE _K <> 0"
    lead, tail = [], f'"{GRID_VAL}" ASC'
    if fs_m:
        lead = [f"_FS {dr} NULLS LAST"]
        tail = f'"{fs_m}" {dr} NULLS LAST, {tail}'
    elif sc in keys:
        lead = [f'"{sc}" {dr} NULLS LAST']
    elif sc == GRID_VAL:
        tail = f'"{GRID_VAL}" {dr}'
    elif sc.endswith(PCT) and sc[:-len(PCT)] in measures:
        tail = f'"{sc}" {dr} NULLS LAST, {tail}'
    order = " ORDER BY " + ", ".join([*lead, *(f'"{c}"' for c in keys), "_K", tail])
    return sql, shown, order


@router.get("/params")
def via_params(as_of: Optional[str] = None):
    conn = base._conn()
    try:
        d, info = _resolve(conn, as_of)
        cur = conn.cursor()
        key = d.isoformat()
        hit = _opts_cache.get(key)
        if hit and time.time() - hit[0] < _OPTS_TTL_S:
            opts = hit[1]
        else:
            sel = ", ".join(f"ARRAY_AGG(DISTINCT NULLIF(TRIM({_EXPR[c]}),'')) \"{c}\"" for c in FILTER_COLS)
            cur.execute(f"SELECT {sel}{_BASE} WHERE v.AS_OF_DATE = '{key}'")
            row = cur.fetchone() or []
            opts = {n[0]: sorted(x for x in (json.loads(v) if v else []) if x is not None)
                    for n, v in zip(cur.description, row)}
            _opts_cache[key] = (time.time(), opts)
        latest = _latest_et(cur)
        pm_cols = _pm_columns(cur)
    finally:
        conn.close()
    return {"asOf": key, "latestEtDate": base._fmt_str(latest), "build": info, "dims": DIMS,
            "measures": MEASURES, "attrs": list(ATTR_AGG), "labels": LABELS,
            "pmPrefix": PM_PREFIX, "pmCols": pm_cols, "smPrefix": SM_PREFIX, "smCols": SM_COLS,
            "filterCols": FILTER_COLS, "options": opts,
            "table": OUT_TBL}


@router.post("/result")
def via_result(req: ViReq):
    page = max(1, int(req.page or 1))
    limit = max(1, min(int(req.limit or 200), MAX_LIMIT))
    conn = base._conn()
    try:
        d, info = _resolve(conn, req.as_of)
        cur = conn.cursor()
        ms = _ms(req)
        if req.grid:
            return _grid_result(req, cur, d, info, ms, page, limit)
        cols = _out_cols(_dims(req, cur))
        where = _where(req, d)
        frm = _from(cols, _cond_cols(req))
        sel, grp = _select(cols, ms)
        grp = grp + _having(req)
        cur.execute(f"SELECT COUNT(*), {', '.join(f'SUM({m})' for m in ms)} FROM ({sel}{frm}{where} GROUP BY {grp})")
        r = cur.fetchone()
        total = int(r[0] or 0)
        totals = {m: float(v or 0) for m, v in zip(ms, r[1:])}
        cur.execute(f"{sel}{frm}{where} GROUP BY {grp}{_order(req, cols, ms)} LIMIT {limit} OFFSET {(page - 1) * limit}")
        rows = [[float(v) if ((c in ms or c in ATTR_AGG) and v is not None) else base._fmt_str(v)
                 for c, v in zip(cols + ms, row)] for row in cur.fetchall()]
    finally:
        conn.close()
    return {"asOf": d.isoformat(), "build": info, "columns": cols + ms, "rows": rows,
            "total": total, "totals": totals, "page": page, "limit": limit}


def _grid_result(req, cur, d, info, ms, page, limit):
    sql, shown, order = _grid_sql(req, cur, d, ms, with_total=True)
    cur.execute(f"SELECT COUNT_IF(_K <> 0), {', '.join(f'SUM(IFF(_K = 0, {m}, 0))' for m in ms)} FROM ({sql})")
    r = cur.fetchone()
    total = int(r[0] or 0)
    totals = {m: float(v or 0) for m, v in zip(ms, r[1:])}
    num = set(ms) | {m + PCT for m in ms} | set(ATTR_AGG)
    cur.execute(f"SELECT {', '.join(chr(34) + c + chr(34) for c in shown)} FROM ({sql}) WHERE _K <> 0{order} "
                f"LIMIT {limit} OFFSET {(page - 1) * limit}")
    rows = [[float(v) if (c in num and v is not None) else base._fmt_str(v) for c, v in zip(shown, row)]
            for row in cur.fetchall()]
    return {"asOf": d.isoformat(), "build": info, "columns": shown, "rows": rows,
            "total": total, "totals": totals, "page": page, "limit": limit, "grid": True}


class ValuesReq(ViReq):
    col: str


VALUES_CAP = 5000


def _col_values(req):
    """The distinct values of one column under the page's filters (NOT its header conditions - a
    list that hid what you just unticked would leave no way to tick it back on). The Excel-style
    header menu's "is any of" tick-list. ARTICLE-sized columns are capped at VALUES_CAP."""
    conn = base._conn()
    try:
        d, _info = _resolve(conn, req.as_of)
        col = _norm_dim(req.col)
        if col in GRID_OUT and req.grid:
            return _grid_values(req, conn, d, col)
        if _is_pm(col):
            expr = f'pd."{col}"'
        elif _is_sm(col) and col[len(SM_PREFIX):] in SM_COLS:
            expr = f'sx."{col}"'
        elif col in _EXPR and col not in ATTR_AGG:
            expr = _EXPR[col]
        else:
            raise HTTPException(400, f"'{req.col}' has no value list")
        plain = req.model_copy(update={"dim_conds": None, "meas_conds": None})
        cur = conn.cursor()
        cur.execute(f"SELECT DISTINCT NVL(TO_VARCHAR({expr}),'') x{_from(['ART_DESC'] if col == 'ART_DESC' else [], [col] if (_is_pm(col) or _is_sm(col)) else [])}"
                    f"{_where(plain, d)} ORDER BY 1 LIMIT {VALUES_CAP + 1}")
        vals = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    return {"col": col, "values": vals[:VALUES_CAP], "capped": len(vals) > VALUES_CAP}


def _grid_values(req, conn, d, col):
    """GRID_COL's tick list is TOTAL + the grid columns; GRID_VAL's is every value of every grid column."""
    cur = conn.cursor()
    plain = req.model_copy(update={"dim_conds": None, "meas_conds": None})
    grid = _grid(plain, cur, _dims(plain, cur))
    if col == GRID_COL:
        return {"col": col, "values": [_grid_label(c) for c in grid], "capped": False}
    cur.execute(f"SELECT DISTINCT {_grid_val(grid, [])} x{_from([], grid)}{_grid_join(grid)}{_where(plain, d)}"
                f" ORDER BY 1 LIMIT {VALUES_CAP + 1}")
    vals = [r[0] for r in cur.fetchall()]
    return {"col": col, "values": vals[:VALUES_CAP], "capped": len(vals) > VALUES_CAP}


@router.post("/values")
def via_values(req: ValuesReq):
    return _col_values(req)


class BuildReq(BaseModel):
    as_of: Optional[str] = None
    user: Optional[str] = None


@router.post("/build")
def via_build(req: BuildReq):
    conn = base._conn()
    try:
        cur = conn.cursor()
        _ensure_tables(cur)
        d = _as_of(req.as_of) or _latest_et(cur)
        if isinstance(d, datetime):
            d = d.date()
        if not _build_lock.acquire(timeout=5):
            raise HTTPException(409, "A build is already running - try again in a few seconds")
        try:
            _build(conn, d, req.user)
        finally:
            _build_lock.release()
        info = _last_build(cur, d)
    finally:
        conn.close()
    return {"asOf": d.isoformat(), "build": info}


class ExportReq(ViReq):
    units: Optional[str] = "raw"
    file_stem: Optional[str] = None      # an Inventory Report's slug; default VERTICAL_INVENTORY_ARTICLE


@router.post("/export")
def via_export(req: ExportReq):
    conn = base._conn()
    d, _info = _resolve(conn, req.as_of)
    measures = _ms(req)
    kl = str(req.units or "").lower() == "kl"
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", req.file_stem or "") or "VERTICAL_INVENTORY_ARTICLE"
    fname = f"{stem}_{d.isoformat()}{'_K_LAKH' if kl else ''}.csv"
    if req.grid:
        cur = conn.cursor()
        sql, shown, order = _grid_sql(req, cur, d, measures, kl=kl)
        cur.execute(f"SELECT {', '.join(chr(34) + c + chr(34) for c in shown)} FROM ({sql}){order}")
        return _stream_csv(cur, [_hdr(c) for c in shown], fname)
    cols = _out_cols(_dims(req, conn.cursor()))
    grp = ", ".join(_col_sql(c, cols) for c in cols)
    ms = ", ".join(f"ROUND(SUM(v.{m}) / {1000 if m.endswith('_Q') else 100000}, 3) AS {m}" if kl
                   else f"SUM(v.{m}) AS {m}" for m in measures)
    cur = conn.cursor()
    cur.execute(f"SELECT {grp}, {ms}{_from(cols, _cond_cols(req))}{_where(req, d)} GROUP BY {_group_by(cols)}{_having(req)}"
                f"{_order(req, cols, measures)}")
    return _stream_csv(cur, [_hdr(c) for c in cols + measures], fname)


def _hdr(c):
    return LABELS.get(c, c[len(PM_PREFIX):] if (_is_pm(c) or _is_sm(c)) else c)


# -- ONE QUERY, SPLIT BY THE CALLER (dump, 2026-09-28) -------------------------------------------
# A level-split dump (one file per store) used to run the report once per value: 495 stores, each a
# scan of the whole day (the table is clustered on AS_OF_DATE only, so a store filter prunes
# nothing) plus 2-8 s of compile - ~13 s a store, ~1.8 h of Snowflake for the ALL-GRID report.
# This runs it ONCE, ordered by the level columns first and the report's own order after, so each
# value's rows arrive together and in the order its own run gives. The levels must be Group-by
# (father) columns: every sum, share % and measure condition is then over a group that lies inside
# one value, so a slice of the whole IS that value's own run.
# NDJSON: line 1 {asOf, columns, headers, header_csv}; then one line per row, [key_1..key_n, *cells]
# for mode 'rows' (/result's cells, raw units) or [key_1..key_n, csv_line] for mode 'csv' (/export's
# line: its NA rule and its units). A blank key reads '(blank)', as /values names it.
def via_split(req, levels, mode="rows"):
    conn = base._conn()
    try:
        d, _info = _resolve(conn, req.as_of)
        cur = conn.cursor()
        measures = _ms(req)
        kl = mode == "csv" and str(req.units or "").lower() == "kl"
        cols = _out_cols(_dims(req, cur))
        lv = [_norm_dim(c) for c in (levels or [])]
        bad = [c for c in lv if c not in cols or c in ATTR_AGG]
        if not lv or bad:
            raise HTTPException(400, f"Split levels must be Group by columns; not {', '.join(bad) or '(none)'}")
        if req.grid:
            sql, shown, order = _grid_sql(req, cur, d, measures, kl=kl)
            num = set(measures) | {m + PCT for m in measures} | set(ATTR_AGG)
        else:
            grp = ", ".join(_col_sql(c, cols) for c in cols)
            ms = ", ".join(f"ROUND(SUM(v.{m}) / {1000 if m.endswith('_Q') else 100000}, 3) AS {m}" if kl
                           else f"SUM(v.{m}) AS {m}" for m in measures)
            sql = (f"SELECT {grp}, {ms}{_from(cols, _cond_cols(req))}{_where(req, d)} "
                   f"GROUP BY {_group_by(cols)}{_having(req)}")
            shown, order = cols + measures, _order(req, cols, measures)
            num = set(measures) | set(ATTR_AGG)
        keys = ", ".join(f"NVL(TRIM(TO_VARCHAR(\"{c}\")),'') \"_L{i}\"" for i, c in enumerate(lv))
        by = ", ".join(f'"_L{i}"' for i in range(len(lv)))
        cur.execute(f"SELECT {keys}, {', '.join(chr(34) + c + chr(34) for c in shown)} FROM ({sql}) "
                    f"ORDER BY {by}, {order[len(' ORDER BY '):]}")
    except Exception:
        conn.close()
        raise
    n = len(lv)
    text = _text_cols(cur)[n:]
    heads = [_hdr(c) for c in shown]

    def line_of(w, buf, row):
        w.writerow(row)
        s = buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        return s.rstrip("\r\n")

    def generate():
        try:
            buf = io.StringIO()
            w = csv.writer(buf)
            yield json.dumps({"asOf": d.isoformat(), "levels": lv, "columns": shown, "headers": heads,
                              "header_csv": line_of(w, buf, heads)}) + "\n"
            total = 0
            while True:
                batch = cur.fetchmany(5000)
                if not batch:
                    # The trailer: a stream cut short (an error mid-read) has none, so the caller
                    # can tell a finished read from a broken one before writing a single file.
                    yield json.dumps({"end": True, "rows": total}) + "\n"
                    break
                total += len(batch)
                out = []
                for r in batch:
                    ks = [k if k != "" else "(blank)" for k in r[:n]]
                    cells = r[n:]
                    if mode == "csv":
                        out.append(json.dumps([*ks, line_of(w, buf, _na_row(cells, text))]))
                    else:
                        out.append(json.dumps([*ks, *(float(v) if (c in num and v is not None) else base._fmt_str(v)
                                                      for c, v in zip(shown, cells))]))
                yield "\n".join(out) + "\n"
        finally:
            conn.close()

    return StreamingResponse(generate(), media_type="application/x-ndjson")

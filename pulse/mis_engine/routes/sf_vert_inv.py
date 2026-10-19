"""
Snowflake MIS — VERTICAL INVENTORY ENGINE (no routes of its own).

The Vertical Inventory page (/sf-mis-vert-inventory, /sf-vert-inv, BRONZE.MIS_VERT_INV) was removed
on 2026-09-25 at the user's request. What remains is the shared engine the article-level page
(sf_vert_inv_art.py, /sf-mis-vert-inventory-article) builds on: the source-day resolution, the
source -> plant / SLOC rules and _build_sql. The rules below are still the rules that page applies.

ONE ROW PER PLANT x SEG / DIV / SUB_DIV / MAJ_CAT x SLOC, with opening, closing and negative-closing
stock in quantity and value. The column layout is the operator's VERTICAL-INVENTORY-REPORT-FORMAT
(ST_CD, ST_NM, ST_TYP, SEG, DIV, SUB_DIV, MJ, SLOC, OP-STK-Q/V, CL-STK-Q/V, NEG-CL-STK-Q/V); the SLOC
vocabulary and every rule below were reconciled against the sample
MR-ST-MJ-TTL-SCM & INVT-STK-MVT-REPORT-2026-09-23.xlsb, sheet 02-PLANT-ALL-LOC.

WHICH PLANT A SOURCE ROW BELONGS TO (the format's Sheet2, and the sample)
  ET_STOCK_DATA           WERKS          every LGORT at STORE plants; at DC and HUB plants every
                                         LGORT but 0001 - that stock is the bin / PRD rows below
  LQUA_SNAPSHOT (LQUA)    WERKS, DC only V2R bins -> MSA_<LGTYP>; E* bins -> MSA_V02 when fresh and
                                         PROCESS_GRT when LAGP marks them G1; CL bins -> SEASONAL
  ET_ZWM_PTL_STK_RFC      DWERKS (store) PTL-<LGTYP>
  ET_ZWM_PRD_BCG_REP_RFC  SITE (store)   <VSTEL>-PRD, one SLOC per sending RDC; VERPR is the line VALUE
  ZWM_PRD_HUB             STORE_WERKS    ST_HUB_PRD
                          RWERKS (hub)   HUB_FRESH / HUB_GRT by HU_TYPE F / G - the hub rows and the
                                         store rows are the same pieces, so the company HUB-PRD
                                         total counts them twice, as the sample does
  ZWM_PICKING_PEN_PRD_RFC PLANT (DC)     PICK_PENDING
  LQUA SDC bins are NOT read: they are the stores' LGORT 0002 stock, already a row.

THE UNIVERSE: SEG APP / GM / MKT, DIV not SCRAP. The sample drops NT (hard tags, packing: 105K of
HA10's 217K 0001 units on 23-Sep) and SCRAP (one article, SCRAP_RTL_PAPER, sat at -11,168 units
and Rs 0 - it alone is the gap between -11.566K and the sample's -0.398K negative).

OP / CL ARE POSITIVE ARTICLES ONLY and NEG-CL holds the negatives, judged per article at plant x
SLOC: HA10 0013 nets to -5.9K but the sample's CL is +52.398K with -38K apart - exactly the
positive articles. OP is the source's previous available day (ET 22-Sep for a 23-Sep report; PTL's
previous day is 21-Sep because 22-Sep never landed), LQUA's previous snapshot run.

VALUE: ET LABST_DMBTR; PRD_BCG VERPR; ZWM_PRD_HUB VALUE. LQUA, PTL and pick-pending carry none, so
they are valued at the article's MAP at that plant on the closing day (LABST_DMBTR / LABST over its
positive stock), falling back to the article's company-wide MAP.

Reconciled on 23-Sep: HA10 0002/0003/0005/0009/0017/0099, DW01-PRD (Q and value), PTL-V06, DB03
0099 / HUB_GRT / HUB_FRESH CL all match the sample to the unit. Not sourced and so absent: the
sample's PENDING STO, HUB_IN_TR, and DC HUB_FRESH rows.
"""
import logging
from datetime import datetime
from typing import Optional

from fastapi import HTTPException

from . import sf_var_combined as base

logger = logging.getLogger(__name__)

DB = "V2RETAIL"
STORE_TBL = f"{DB}.AKS_SILVER.SLV_MIS_STORE_MASTER"
PROD_TBL = f"{DB}.SILVER.VW_PRODUCT_MASTER"
T001L_TBL = f"{DB}.BRONZE.T001L"
ET_TBL = f"{DB}.BRONZE.ET_STOCK_DATA"
LQS_TBL = f"{DB}.BRONZE.LQUA_SNAPSHOT"
LAGP_TBL = f"{DB}.BRONZE.LAGP"
BCG_TBL = f"{DB}.BRONZE.ET_ZWM_PRD_BCG_REP_RFC"
HUB_TBL = f"{DB}.BRONZE.ZWM_PRD_HUB"
PTL_TBL = f"{DB}.BRONZE.ET_ZWM_PTL_STK_RFC"
PICK_TBL = f"{DB}.BRONZE.ZWM_PICKING_PEN_PRD_RFC"

MEASURES = ["OP_Q", "OP_V", "CL_Q", "CL_V", "NEG_Q", "NEG_V"]
# Format header names, for the grid and the CSV.
LABELS = {"ST_CD": "ST_CD", "ST_NM": "ST_NM", "ST_TYP": "ST_TYP", "SEG": "SEG", "DIV": "DIV",
          "SUB_DIV": "SUB_DIV", "MJ": "MJ", "SLOC": "SLOC", "SLOC_DESC": "SLOC_DESC", "SRC": "SRC",
          "OP_Q": "OP-STK-Q", "OP_V": "OP-STK-V", "CL_Q": "CL-STK-Q", "CL_V": "CL-STK-V",
          "NEG_Q": "NEG-CL-STK-Q", "NEG_V": "NEG-CL-STK-V"}

# The DC storage types that ARE stock-at-rest, besides the E* (V02) and CL* bins - the sample's MSA
# rows and the same list SILVER.V_ARS_MSA_STOCK_LQUA keeps. The other V2R types (V05-V07, V09,
# V10, V18, V19, V23-V26) are picking / PTL / putwall / hub-sort staging: that stock is already
# the PTL rows at the store, so adding it here would count one piece twice.
MSA_LGTYPS = "'G01','V01','V04','V08','V11','V15','V16','V21','V51'"

def _q(v):
    return base._q(v)


def _as_of(v):
    if not v:
        return None
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, f"as_of must be YYYY-MM-DD, got '{v}'")


def _one(cur, sql):
    cur.execute(sql)
    r = cur.fetchone()
    return r[0] if r else None


def _source_days(cur, d):
    """For each dated source: the closing day (latest on or before `d`) and the opening day (the
    one before it). LQUA and pick-pending are chosen by RUN, the newest run of the day."""
    ds = d.isoformat()
    src = {}
    src["et_cl"] = _one(cur, f"SELECT MAX(STOCK_DATE)::DATE FROM {ET_TBL} WHERE STOCK_DATE >= DATEADD(day,-45,'{ds}'::DATE) "
                             f"AND STOCK_DATE < DATEADD(day,1,'{ds}'::DATE)")
    if not src["et_cl"]:
        raise HTTPException(404, f"ET_STOCK_DATA has no reading in the 45 days up to {d}")
    src["et_op"] = _one(cur, f"SELECT MAX(STOCK_DATE)::DATE FROM {ET_TBL} WHERE STOCK_DATE >= DATEADD(day,-45,'{src['et_cl']}'::DATE) "
                             f"AND STOCK_DATE < '{src['et_cl']}'::DATE")
    for key, tbl, col in (("ptl", PTL_TBL, "PUR_DATE"), ("bcg", BCG_TBL, "PRD_DATE::DATE"),
                          ("hub", HUB_TBL, "PUR_DATE"), ("pick", PICK_TBL, "PUR_DATE")):
        cl = _one(cur, f"SELECT MAX({col}) FROM {tbl} WHERE {col} <= '{ds}'")
        op = _one(cur, f"SELECT MAX({col}) FROM {tbl} WHERE {col} < '{cl}'") if cl else None
        src[f"{key}_cl"], src[f"{key}_op"] = cl, op
    for k in ("cl", "op"):
        day = src[f"pick_{k}"]
        src[f"pick_{k}_run"] = _one(cur, f"SELECT MAX_BY(SRC_RUN_ID, LOADED_AT) FROM {PICK_TBL} WHERE PUR_DATE = '{day}'") if day else None
    lq_cl = _one(cur, f"SELECT MAX(SNAP_DATE) FROM {LQS_TBL} WHERE SNAP_DATE <= '{ds}'")
    src["lq_cl"] = lq_cl
    src["lq_cl_run"] = _one(cur, f"SELECT MAX_BY(SRC_RUN_ID, SNAP_TS) FROM {LQS_TBL} WHERE SNAP_DATE = '{lq_cl}'") if lq_cl else None
    lq_op = _one(cur, f"SELECT MAX(SNAP_DATE) FROM {LQS_TBL} WHERE SNAP_DATE < '{lq_cl}'") if lq_cl else None
    src["lq_op"] = lq_op
    src["lq_op_run"] = _one(cur, f"SELECT MAX_BY(SRC_RUN_ID, SNAP_TS) FROM {LQS_TBL} WHERE SNAP_DATE = '{lq_op}'") if lq_op else None
    return src


def _dl(v):
    return f"'{v}'::DATE" if v else "NULL::DATE"


def _sl(v):
    return _q(v) if v else "NULL"


# Which articles are in the report (over VW_PRODUCT_MASTER columns): the sample's SEG APP / GM / MKT,
# DIV not SCRAP. The article page passes its own.
UNIVERSE = "TRIM(SEGMENT) IN ('APP','GM','MKT') AND NVL(TRIM(DIVISION),'') <> 'SCRAP'"

# Which ET_STOCK_DATA storage locations become rows: every LGORT at stores; at DCs and hubs every
# LGORT but 0001 (that stock is the LQUA bin / PRD rows). The article page passes its own rule.
ET_LGORT_RULE = "NOT (sm.ST_TYP IN ('DC','HUB') AND et.SLOC = '0001')"


def _lq_slot(typ, g, v02_split=False):
    """The report SLOC of a V2R storage type (+ its LAGP bin row `g`), as the LQUA rows name it.
    v02_split (the article page, user ask 2026-09-25): the E-type (V02) bins split by the bin's LAGP
    status LPTYP - F1 fresh -> MSA_V02_FRESH, G1 GRT -> MSA_V02_GRT (what PROCESS_GRT held), blank
    stays MSA_V02. Without it, the sample's names: G1 -> PROCESS_GRT, the rest MSA_V02."""
    if v02_split:
        e = (f" WHEN TRIM({typ}) LIKE 'E%' AND NVL(TRIM({g}.LPTYP),'') = 'G1' THEN 'MSA_V02_GRT'"
             f" WHEN TRIM({typ}) LIKE 'E%' AND NVL(TRIM({g}.LPTYP),'') = 'F1' THEN 'MSA_V02_FRESH'")
    else:
        e = f" WHEN TRIM({typ}) LIKE 'E%' AND NVL(TRIM({g}.LPTYP),'') = 'G1' THEN 'PROCESS_GRT'"
    return (f"CASE WHEN TRIM({typ}) LIKE 'CL%' OR NVL(TRIM({g}.LPTYP),'') = 'CL' THEN 'SEASONAL'"
            f"{e} WHEN TRIM({typ}) LIKE 'E%' THEN 'MSA_V02'"
            f" ELSE 'MSA_' || TRIM({typ}) END")


def _lq_types(typ):
    return f"(TRIM({typ}) LIKE 'E%' OR TRIM({typ}) LIKE 'CL%' OR TRIM({typ}) IN ({MSA_LGTYPS}))"


def _build_sql(src, article=False, et_rule=ET_LGORT_RULE, net=False, moves=None, universe=UNIVERSE,
               v02_split=False, prd_at_rdc=False, ptl_at_rdc=False):
    """article=True keeps the article (and its GEN_ART / CLR) instead of summing it away - the
    article-level copy of this report (sf_vert_inv_art.py). et_rule is the SQL condition (over
    sm.ST_TYP and et.SLOC) for which ET_STOCK_DATA LGORTs become rows. net=True makes OP / CL the
    net stock (negative articles included); NEG-CL still holds the negatives alone. moves is extra
    UNION ALL legs for `rws` with K = 'IN' / 'OUT' (the article page's movement); when given, two
    more measures IN_Q / OUT_Q are appended after the six."""
    pos = "" if net else " AND a.Q > 0"
    # prd_at_rdc (the article page, user ask 2026-09-25): the RDC-PRD handling units are ALSO shown
    # at the sending RDC (VSTEL), where they physically sit packed, besides the receiving store -
    # the same pieces twice in a grand total, exactly as HUB-PRD shows at both hub and store.
    # ptl_at_rdc (the article page, user ask 2026-09-26): the PTL stock is ALSO shown at the sending
    # RDC (SWERKS), where it sits in the V06 / V07 / V09 / V10 / V18 / V19 / V23-V25 put-to-light
    # staging types - which the LQUA rows leave out (MSA_LGTYPS), so nothing is counted twice at the
    # DC; against the store rows it is the same pieces twice, as RDC-PRD and HUB-PRD are.
    ptl_rdc = (f"""
  UNION ALL
  SELECT IFF(p.PUR_DATE = {_dl(src['ptl_cl'])}, 'CL', 'OP'), TRIM(p.SWERKS), 'PTL-' || TRIM(p.LGTYP),
         'ET_ZWM_PTL_STK_RFC', LTRIM(TRIM(p.MATNR),'0'), p.MENGE, NULL
    FROM (SELECT * FROM {PTL_TBL} WHERE PUR_DATE IN ({_dl(src['ptl_cl'])}, {_dl(src['ptl_op'])})
          QUALIFY ROW_NUMBER() OVER (PARTITION BY PUR_DATE, LGTYP, MATNR, DWERKS, SWERKS, HUB ORDER BY MENGE DESC) = 1) p""" if ptl_at_rdc else "")
    prd_rdc = (f"""
  UNION ALL
  SELECT IFF(b.PRD_DATE::DATE = {_dl(src['bcg_cl'])}, 'CL', 'OP'), TRIM(b.VSTEL), TRIM(b.VSTEL) || '-PRD',
         'ET_ZWM_PRD_BCG_REP_RFC', LTRIM(TRIM(b.MATNR),'0'), b.VEMNG, b.VERPR
    FROM {BCG_TBL} b WHERE b.PRD_DATE::DATE IN ({_dl(src['bcg_cl'])}, {_dl(src['bcg_op'])})""" if prd_at_rdc else "")
    mv_cols = ", SUM(IFF(a.K = 'IN', a.Q, 0)), SUM(IFF(a.K = 'OUT', a.Q, 0))" if moves else ""
    keys = ('a.ST_CD, pm.SEG, pm."DIV", pm.SUB_DIV, pm.MJ, pm.GEN_ART, pm.CLR, a.ART, a.SLOC, a.SRC, pm.ART_TYPE' if article
            else 'a.ST_CD, pm.SEG, pm."DIV", pm.SUB_DIV, pm.MJ, a.SLOC, a.SRC')
    nkeys = 11 if article else 7
    return f"""
WITH
sm AS (SELECT TRIM(ST_CD) ST_CD, MAX(TRIM(ST_TYP)) ST_TYP FROM {STORE_TBL}
       WHERE TRIM(ST_TYP) IN ('STORE','HUB','DC') GROUP BY 1),
pm AS (SELECT ARTICLE_NUMBER ART, TRIM(SEGMENT) SEG, TRIM(DIVISION) "DIV", TRIM(SUB_DIVISION) SUB_DIV,
              TRIM(MAJOR_CATEGORY_DESCRIPTION) MJ, TRIM(GEN_ARTICLE_NUMBER) GEN_ART, TRIM(COLOR) CLR,
              TRIM(ARTICLE_TYPE) ART_TYPE
         FROM {PROD_TBL}
        WHERE {universe}),
et AS (
  SELECT IFF(e.STOCK_DATE::DATE = {_dl(src['et_cl'])}, 'CL', 'OP') K, TRIM(e.WERKS) ST_CD, TRIM(e.LGORT) SLOC,
         LTRIM(TRIM(e.MATNR),'0') ART, SUM(e.LABST) Q, SUM(e.LABST_DMBTR) V
    FROM {ET_TBL} e
    JOIN sm ON sm.ST_CD = TRIM(e.WERKS)
   WHERE (e.STOCK_DATE >= {_dl(src['et_cl'])} AND e.STOCK_DATE < DATEADD(day,1,{_dl(src['et_cl'])}))
      OR (e.STOCK_DATE >= {_dl(src['et_op'])} AND e.STOCK_DATE < DATEADD(day,1,{_dl(src['et_op'])}))
   GROUP BY 1,2,3,4),
rate  AS (SELECT ST_CD, ART, SUM(V)/NULLIF(SUM(Q),0) R FROM et WHERE K = 'CL' AND Q > 0 GROUP BY 1,2),
crate AS (SELECT ART, SUM(V)/NULLIF(SUM(Q),0) R FROM et WHERE K = 'CL' AND Q > 0 GROUP BY 1),
rws AS (
  SELECT et.K, et.ST_CD, et.SLOC, 'ET_STOCK_DATA' SRC, et.ART, et.Q, et.V
    FROM et JOIN sm ON sm.ST_CD = et.ST_CD
   WHERE {et_rule}
  UNION ALL
  SELECT IFF(s.SRC_RUN_ID = {_sl(src['lq_cl_run'])}, 'CL', 'OP'), TRIM(s.WERKS),
         {_lq_slot('s.LGTYP', 'g', v02_split)},
         'LQUA', LTRIM(TRIM(s.MATNR),'0'), TRY_TO_DECIMAL(s.GESME,18,3), NULL
    FROM {LQS_TBL} s
    JOIN sm ON sm.ST_CD = TRIM(s.WERKS) AND sm.ST_TYP = 'DC'
    LEFT JOIN {LAGP_TBL} g ON TRIM(g.LGNUM) = TRIM(s.LGNUM) AND TRIM(g.LGTYP) = TRIM(s.LGTYP) AND TRIM(g.LGPLA) = TRIM(s.LGPLA)
   WHERE s.SNAP_DATE IN ({_dl(src['lq_cl'])}, {_dl(src['lq_op'])})
     AND s.SRC_RUN_ID IN ({_sl(src['lq_cl_run'])}, {_sl(src['lq_op_run'])}) AND TRIM(s.LGNUM) = 'V2R'
     AND {_lq_types('s.LGTYP')}
  UNION ALL
  SELECT IFF(p.PUR_DATE = {_dl(src['ptl_cl'])}, 'CL', 'OP'), TRIM(p.DWERKS), 'PTL-' || TRIM(p.LGTYP),
         'ET_ZWM_PTL_STK_RFC', LTRIM(TRIM(p.MATNR),'0'), p.MENGE, NULL
    FROM (SELECT * FROM {PTL_TBL} WHERE PUR_DATE IN ({_dl(src['ptl_cl'])}, {_dl(src['ptl_op'])})
          QUALIFY ROW_NUMBER() OVER (PARTITION BY PUR_DATE, LGTYP, MATNR, DWERKS, SWERKS, HUB ORDER BY MENGE DESC) = 1) p{ptl_rdc}
  UNION ALL
  SELECT IFF(b.PRD_DATE::DATE = {_dl(src['bcg_cl'])}, 'CL', 'OP'), TRIM(b.SITE), TRIM(b.VSTEL) || '-PRD',
         'ET_ZWM_PRD_BCG_REP_RFC', LTRIM(TRIM(b.MATNR),'0'), b.VEMNG, b.VERPR
    FROM {BCG_TBL} b WHERE b.PRD_DATE::DATE IN ({_dl(src['bcg_cl'])}, {_dl(src['bcg_op'])}){prd_rdc}
  UNION ALL
  SELECT IFF(h.PUR_DATE = {_dl(src['hub_cl'])}, 'CL', 'OP'), TRIM(h.STORE_WERKS), 'ST_HUB_PRD',
         'ZWM_PRD_HUB', LTRIM(TRIM(h.ARTICLE),'0'), h.QTY, h.VALUE
    FROM {HUB_TBL} h WHERE h.PUR_DATE IN ({_dl(src['hub_cl'])}, {_dl(src['hub_op'])})
  UNION ALL
  SELECT IFF(h.PUR_DATE = {_dl(src['hub_cl'])}, 'CL', 'OP'), TRIM(h.RWERKS),
         CASE TRIM(h.HU_TYPE) WHEN 'F' THEN 'HUB_FRESH' WHEN 'G' THEN 'HUB_GRT'
              ELSE 'HUB_' || NVL(NULLIF(TRIM(h.HU_TYPE),''),'NA') END,
         'ZWM_PRD_HUB', LTRIM(TRIM(h.ARTICLE),'0'), h.QTY, h.VALUE
    FROM {HUB_TBL} h WHERE h.PUR_DATE IN ({_dl(src['hub_cl'])}, {_dl(src['hub_op'])})
  UNION ALL
  SELECT IFF(k.SRC_RUN_ID = {_sl(src['pick_cl_run'])}, 'CL', 'OP'), TRIM(k.PLANT), 'PICK_PENDING',
         'ZWM_PICKING_PEN_PRD_RFC', LTRIM(TRIM(k.MATERIAL),'0'), k.PEN_QTY, NULL
    FROM {PICK_TBL} k
   WHERE k.PUR_DATE IN ({_dl(src['pick_cl'])}, {_dl(src['pick_op'])})
     AND k.SRC_RUN_ID IN ({_sl(src['pick_cl_run'])}, {_sl(src['pick_op_run'])}){moves or ''}),
art AS (
  SELECT r.K, r.ST_CD, r.SLOC, r.SRC, r.ART, SUM(r.Q) Q,
         IFF(COUNT(r.V) > 0, SUM(r.V), SUM(r.Q) * COALESCE(MAX(rt.R), MAX(cr.R), 0)) V
    FROM rws r
    JOIN sm ON sm.ST_CD = r.ST_CD
    LEFT JOIN rate rt ON rt.ST_CD = r.ST_CD AND rt.ART = r.ART
    LEFT JOIN crate cr ON cr.ART = r.ART
   GROUP BY 1,2,3,4,5)
SELECT {keys},
       SUM(IFF(a.K = 'OP'{pos}, a.Q, 0)), SUM(IFF(a.K = 'OP'{pos}, a.V, 0)),
       SUM(IFF(a.K = 'CL'{pos}, a.Q, 0)), SUM(IFF(a.K = 'CL'{pos}, a.V, 0)),
       SUM(IFF(a.K = 'CL' AND a.Q < 0, a.Q, 0)), SUM(IFF(a.K = 'CL' AND a.Q < 0, a.V, 0)){mv_cols}
  FROM art a JOIN pm ON pm.ART = a.ART
 GROUP BY {', '.join(str(i + 1) for i in range(nkeys))}"""


def _latest_et(cur):
    return _one(cur, f"SELECT MAX(STOCK_DATE)::DATE FROM {ET_TBL} WHERE STOCK_DATE >= DATEADD(day,-45,CURRENT_DATE)")

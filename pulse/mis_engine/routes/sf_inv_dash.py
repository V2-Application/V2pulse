"""
Snowflake MIS — INVENTORY ANALYTICS DASHBOARD (mounted at /sf-inv-dash).

Stock alone says where the pieces are; the questions an inventory dashboard answers - how many days
does it last, what is not selling, where are we out of stock while the DC holds it, which categories
carry more stock than their sale share - need the sale next to it. So each as-of date gets its own
stored slice, ONE ROW PER PLANT x OPTION (generic article + colour - the grain fashion stock is
judged at: one size not selling at a store for 90 days is normal, the whole option not selling is not):

  stock  from BRONZE.MIS_VERT_INV_ART (the Vertical Inventory (Article) build, built first if absent),
         summed over SLOC and sizes and split by where it sits:
           SHELF  store plant, ET_STOCK_DATA, SLOC <> 0099          (sellable at the store)
           TR     any ET_STOCK_DATA SLOC 0099                       (in transit)
           PIPE   store plant, any other source (PTL, RDC-PRD, hub PRD) - bound for the store
           WH     DC / HUB plant, everything but 0099               (warehouse; ST_TYP tells DC from HUB)
         CL / OP / NEG / IN / OUT are the article build's own totals, so CL = SHELF + TR + PIPE + WH.
  sale   BRONZE.VBRP, FKART_ANA = 'FP' (the house rule, sf_mis_report / SP_AKS_DAY_REF_LOAD): quantity
         negated on SHKZG = 'X', NETWR / WAVWR already signed, GM = NETWR - WAVWR. Windows END ON the
         as-of day: S7 / S30 / S90 = the last 7 / 30 / 90 days inclusive, LAST_SALE = the last day in
         those 90 with a positive quantity. Articles are the article build's universe (SEG APP / GM /
         MKT / NT, DIV not SCRAP, EA only), so sale and stock line up article for article; a plant x
         option that sold but holds no stock is a row too (that is what OOS is made of). An article with
         no GEN_ART is its own option.

MERCHANDISE ONLY (the page's default): SEG NT - hard tags, packing, fixed assets (FA / CO: AC units,
carry bags) - never sells (24-Sep-2026: 146 Cr / 6.6 Cr pcs of store shelf, zero sale), so counting it
makes every cover / dead-stock figure meaningless. The switch drops SEG NT from every figure.

BRONZE.MIS_INV_DASH_SZ is the same at store x option x SIZE (product master SIZE1), with CLR_FAM - the
colour shade rolled up to a family (BLACK takes DEEP / JET / CARBON BLACK ...; _FAMILIES). The option
table is built as its roll-up. The page reads the size table for the Size & colour tab, whenever a size
is filtered on, and when the health grain is switched to size.

plus BRONZE.MIS_INV_DASH_WK - the same sale in 13 rolling 7-day weeks back from the as-of day, at
ST_CD x SEG / DIV / SUB_DIV / MJ, for the trend.

The read side (/data) applies the page's filters and sums the slice several ways in one GROUPING
SETS scan, then runs ABC, the action lists and the trends. Derived measures (row level, store plants):
  cover days  SHELF / (S30 / 30); health bucket per store x option with shelf stock:
              DEAD no sale in 90 d, SLOW none in 30 d, else by cover <15 / 15-30 / 30-60 / 60-90 / 90-180 / >180
  excess      shelf beyond `target` days of cover (all of it when SLOW / DEAD), valued at the row's rate
  OOS         sold in the last 30 days, shelf <= 0; LOST/day = its S30 value / 30
"""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import sf_var_combined as base
from . import sf_vert_inv as vi
from . import sf_vert_inv_art as art

logger = logging.getLogger(__name__)
router = APIRouter()

DB = vi.DB
OUT_TBL = f"{DB}.BRONZE.MIS_INV_DASH_OPT"        # store x option (GEN_ART + CLR)        - health grain
SZ_TBL = f"{DB}.BRONZE.MIS_INV_DASH_SZ"          # store x option x size (SIZE1)        - size / colour grain
WK_TBL = f"{DB}.BRONZE.MIS_INV_DASH_WK"
LOG_TBL = f"{DB}.BRONZE.MIS_INV_DASH_LOG"
VBRP_TBL = f"{DB}.BRONZE.VBRP"
STORE_TBL = vi.STORE_TBL
PROD_TBL = vi.PROD_TBL
WEEKS = 13

STK = ["CL_Q", "CL_V", "OP_Q", "OP_V", "NEG_Q", "NEG_V", "IN_Q", "OUT_Q",
       "SHELF_Q", "SHELF_V", "TR_Q", "TR_V", "PIPE_Q", "PIPE_V", "WH_Q", "WH_V"]
SALE = ["S7_Q", "S30_Q", "S30_V", "S30_GM", "S90_Q", "S90_V", "S90_GM"]
FILTER_COLS = ["ZONE", "STATE", "ST_TYP", "ST_CD", "SEG", "DIV", "SUB_DIV", "MJ", "CLR_FAM", "CLR", "SZ"]
_FEXPR = {"ZONE": "sm.ZONE", "STATE": "sm.STATE", "ST_TYP": "d.ST_TYP", "ST_CD": "d.ST_CD",
          "SEG": "d.SEG", "DIV": 'd."DIV"', "SUB_DIV": "d.SUB_DIV", "MJ": "d.MJ",
          "CLR_FAM": "d.CLR_FAM", "CLR": "d.CLR", "SZ": "d.SZ"}
# Which filter columns each table carries (the weekly sale has no colour / size).
_TBL_COLS = {"opt": set(FILTER_COLS) - {"SZ"}, "sz": set(FILTER_COLS),
             "wk": {"ZONE", "STATE", "ST_TYP", "ST_CD", "SEG", "DIV", "SUB_DIV", "MJ"}}
_SM = (f"(SELECT TRIM(ST_CD) ST_CD, MAX(ST_NM) ST_NM, MAX(TRIM(ZONE)) ZONE, MAX(TRIM(STATE)) STATE"
       f" FROM {STORE_TBL} GROUP BY 1)")

# COLOR is a shade (DEEP BLACK, JET BLACK, NAVY, OXFORD BLUE ...); the family rolls shades up so
# "black jeans" is one filter. First match wins, so the order matters (NAVY before BLUE is moot -
# both are BLUE; OFF WHITE must be BEIGE before WHITE).
_FAMILIES = [
    ("MULTI", ["ASSORT", "MULTI", "MIX", "PRINT", "CHECK", "STRIPE"]),
    ("BEIGE", ["OFF WHITE", "OFFWHITE", "CREAM", "BEIGE", "IVORY", "FAWN", "SAND", "STONE", "NUDE", "SKIN", "ECRU",
               "ALMND", "ALMOND"]),
    ("BLACK", ["BLACK", "BLK", "ONYX"]),
    ("WHITE", ["WHITE"]),
    ("GREY", ["GREY", "GRAY", "GRY", "CHARCOAL", "CHRCOL", "ASH", "SILVER", "SMOKE", "ANTHRA", "ANTRA", "SLATE"]),
    ("BLUE", ["BLUE", "NAVY", "DENIM", "INDIGO", "TEAL", "TURQ", "AQUA", "CYAN", "FIROZI", "SKY"]),
    ("RED", ["RED", "MAROON", "WINE", "RUST", "BURGUNDY", "CHERRY", "CORAL"]),
    ("PINK", ["PINK", "ROSE", "PEACH", "MAGENTA", "FUCHSIA", "ONION", "RANI"]),
    ("PURPLE", ["PURPLE", "VIOLET", "LAVENDER", "LILAC", "MAUVE", "PLUM"]),
    ("GREEN", ["GREEN", "GRN", "OLIVE", "MINT", "LIME", "PISTA", "PASTA", "MEHNDI", "SAGE", "BOTTLE"]),
    ("YELLOW", ["YELLOW", "MUSTARD", "LEMON", "GOLD"]),
    ("ORANGE", ["ORANGE", "SAFFRON", "TANGERINE"]),
    ("BROWN", ["BROWN", "BRW", "BRN", "KHAKI", "KHAKHI", "TAN", "COFFEE", "CHOCO", "CACAO", "CAMEL", "MOCHA", "COPPER"]),
]


def _fam_sql(col):
    whens = " ".join(f"WHEN {' OR '.join(f'UPPER({col}) LIKE {_q_lit(chr(37) + w + chr(37))}' for w in words)} THEN '{fam}'"
                     for fam, words in _FAMILIES)
    return f"CASE WHEN NVL({col},'') = '' THEN '(blank)' {whens} ELSE 'OTHER' END"


def _q_lit(s):
    return "'" + s.replace("'", "''") + "'"


_build_lock = threading.Lock()
_ddl_done = {"ok": False}
_cache = {}
_CACHE_TTL_S = 15 * 60
_q = vi._q


def _ensure_tables(cur):
    if _ddl_done["ok"]:
        return
    cols = ", ".join(f"{m} NUMBER(20,3)" for m in STK + SALE)
    for tbl, extra in ((OUT_TBL, ""), (SZ_TBL, "SZ VARCHAR(30),")):
        cur.execute(f"""CREATE TABLE IF NOT EXISTS {tbl} (
            AS_OF_DATE DATE NOT NULL, ST_CD VARCHAR(10) NOT NULL, GEN_ART VARCHAR(40) NOT NULL, CLR VARCHAR(60),
            {extra} CLR_FAM VARCHAR(20), N_VAR NUMBER(6), ST_TYP VARCHAR(10),
            SEG VARCHAR(20), "DIV" VARCHAR(40), SUB_DIV VARCHAR(60), MJ VARCHAR(80), MAP NUMBER(18,2),
            {cols}, LAST_SALE DATE, BUILT_AT TIMESTAMP_NTZ) CLUSTER BY (AS_OF_DATE)""")
    cur.execute(f"ALTER TABLE {OUT_TBL} ADD COLUMN IF NOT EXISTS CLR_FAM VARCHAR(20)")
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {WK_TBL} (
        AS_OF_DATE DATE NOT NULL, ST_CD VARCHAR(10), SEG VARCHAR(20), "DIV" VARCHAR(40), SUB_DIV VARCHAR(60),
        MJ VARCHAR(80), WK NUMBER(3), WK_END DATE, S_Q NUMBER(20,3), S_V NUMBER(20,2), S_GM NUMBER(20,2))
        CLUSTER BY (AS_OF_DATE)""")
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {LOG_TBL} (
        AS_OF_DATE DATE, BUILT_AT TIMESTAMP_NTZ, BUILT_BY VARCHAR(100), SECS NUMBER(10,1), ROWS_ NUMBER,
        SALE_TO DATE, TOTALS VARIANT)""")
    _ddl_done["ok"] = True


def _build(conn, d, user=None):
    t0 = time.time()
    cur = conn.cursor()
    _ensure_tables(cur)
    ds = d.isoformat()
    lo = (d - timedelta(days=90)).strftime("%Y%m%d")
    hi = d.strftime("%Y%m%d")
    dl = f"'{ds}'::DATE"
    now = "CONVERT_TIMEZONE('Asia/Kolkata', CURRENT_TIMESTAMP())::TIMESTAMP_NTZ"
    # The article build's own product join (sf_vert_inv._build_sql's pm), so GEN_ART / CLR match it; + SIZE1.
    pm = (f"SELECT ARTICLE_NUMBER ART, MAX(TRIM(SEGMENT)) SEG, MAX(TRIM(DIVISION)) \"DIV\","
          f" MAX(TRIM(SUB_DIVISION)) SUB_DIV, MAX(TRIM(MAJOR_CATEGORY_DESCRIPTION)) MJ,"
          f" NVL(NULLIF(MAX(TRIM(GEN_ARTICLE_NUMBER)),''), ARTICLE_NUMBER) G, NVL(MAX(TRIM(COLOR)),'') C,"
          f" NVL(MAX(TRIM(SIZE1)),'') SZ"
          f" FROM {PROD_TBL} WHERE {art.UNIVERSE} GROUP BY 1")
    # Daily sale per plant x article over the 91 days, universe articles only.
    cur.execute(f"""CREATE OR REPLACE TEMPORARY TABLE TMP_INV_DASH_SALE AS
        SELECT TRIM(v.WERKS) ST_CD, LTRIM(TRIM(v.MATNR),'0') ART, TRY_TO_DATE(v.FKDAT_ANA,'YYYYMMDD') D,
               SUM(IFF(NVL(v.SHKZG,'') = 'X', -1, 1) * TRY_TO_DOUBLE(TO_VARCHAR(v.FKIMG))) Q,
               SUM(TRY_TO_DOUBLE(TO_VARCHAR(v.NETWR))) V,
               SUM(TRY_TO_DOUBLE(TO_VARCHAR(v.NETWR)) - TRY_TO_DOUBLE(TO_VARCHAR(v.WAVWR))) GM
          FROM {VBRP_TBL} v
         WHERE v.FKART_ANA = 'FP' AND v.FKDAT_ANA BETWEEN '{lo}' AND '{hi}'
           AND LTRIM(TRIM(v.MATNR),'0') IN (SELECT ART FROM ({pm}))
         GROUP BY 1, 2, 3
        HAVING D IS NOT NULL""")
    # Store x option x size - the finest grain; the option table is its roll-up.
    et = "v.SRC = 'ET_STOCK_DATA'"
    cur.execute(f"""CREATE OR REPLACE TEMPORARY TABLE TMP_INV_DASH_SZ AS
        WITH sm AS (SELECT TRIM(ST_CD) ST_CD, MAX(TRIM(ST_TYP)) ST_TYP FROM {STORE_TBL} GROUP BY 1),
        pm AS ({pm}),
        po AS (SELECT G, C, MAX(SEG) SEG, MAX("DIV") "DIV", MAX(SUB_DIV) SUB_DIV, MAX(MJ) MJ FROM pm GROUP BY 1, 2),
        mp AS (SELECT LTRIM(TRIM(MATNR),'0') ART,
                      AVG(TRY_TO_DECIMAL(VERPR,18,3) / NULLIF(TRY_TO_DECIMAL(PEINH,18,3),0)) MAP
                 FROM {art.MBEW_TBL} WHERE TRY_TO_DECIMAL(VERPR,18,3) > 0 GROUP BY 1),
        mpo AS (SELECT pm.G, pm.C, pm.SZ, AVG(mp.MAP) MAP FROM pm JOIN mp ON mp.ART = pm.ART GROUP BY 1, 2, 3),
        stk AS (
          SELECT v.ST_CD, COALESCE(pm.G, NULLIF(v.GEN_ART,''), v.ART) G, COALESCE(pm.C, v.CLR, '') C, NVL(pm.SZ, '') SZ,
                 AVG(v.MAP) MAP, COUNT(DISTINCT v.ART) N_VAR,
                 SUM(v.CL_Q) CL_Q, SUM(v.CL_V) CL_V, SUM(v.OP_Q) OP_Q, SUM(v.OP_V) OP_V,
                 SUM(v.NEG_Q) NEG_Q, SUM(v.NEG_V) NEG_V, SUM(v.IN_Q) IN_Q, SUM(v.OUT_Q) OUT_Q,
                 SUM(IFF(sm.ST_TYP = 'STORE' AND {et} AND v.SLOC <> '0099', v.CL_Q, 0)) SHELF_Q,
                 SUM(IFF(sm.ST_TYP = 'STORE' AND {et} AND v.SLOC <> '0099', v.CL_V, 0)) SHELF_V,
                 SUM(IFF({et} AND v.SLOC = '0099', v.CL_Q, 0)) TR_Q,
                 SUM(IFF({et} AND v.SLOC = '0099', v.CL_V, 0)) TR_V,
                 SUM(IFF(sm.ST_TYP = 'STORE' AND NOT {et}, v.CL_Q, 0)) PIPE_Q,
                 SUM(IFF(sm.ST_TYP = 'STORE' AND NOT {et}, v.CL_V, 0)) PIPE_V,
                 SUM(IFF(NVL(sm.ST_TYP,'') <> 'STORE' AND NOT ({et} AND v.SLOC = '0099'), v.CL_Q, 0)) WH_Q,
                 SUM(IFF(NVL(sm.ST_TYP,'') <> 'STORE' AND NOT ({et} AND v.SLOC = '0099'), v.CL_V, 0)) WH_V
            FROM {art.OUT_TBL} v LEFT JOIN sm ON sm.ST_CD = v.ST_CD LEFT JOIN pm ON pm.ART = v.ART
           WHERE v.AS_OF_DATE = '{ds}'
           GROUP BY 1, 2, 3, 4),
        sl AS (
          SELECT s.ST_CD, pm.G, pm.C, pm.SZ,
                 SUM(IFF(D > DATEADD(day,-7,{dl}), Q, 0)) S7_Q,
                 SUM(IFF(D > DATEADD(day,-30,{dl}), Q, 0)) S30_Q,
                 SUM(IFF(D > DATEADD(day,-30,{dl}), V, 0)) S30_V,
                 SUM(IFF(D > DATEADD(day,-30,{dl}), GM, 0)) S30_GM,
                 SUM(IFF(D > DATEADD(day,-90,{dl}), Q, 0)) S90_Q,
                 SUM(IFF(D > DATEADD(day,-90,{dl}), V, 0)) S90_V,
                 SUM(IFF(D > DATEADD(day,-90,{dl}), GM, 0)) S90_GM,
                 MAX(IFF(Q > 0 AND D > DATEADD(day,-90,{dl}), D, NULL)) LAST_SALE
            FROM TMP_INV_DASH_SALE s JOIN pm ON pm.ART = s.ART GROUP BY 1, 2, 3, 4)
        SELECT COALESCE(stk.ST_CD, sl.ST_CD) ST_CD, COALESCE(stk.G, sl.G) GEN_ART, COALESCE(stk.C, sl.C) CLR,
               COALESCE(stk.SZ, sl.SZ) SZ, NVL(stk.N_VAR, 0) N_VAR, NVL(sm.ST_TYP, 'OTHER') ST_TYP,
               po.SEG, po."DIV", po.SUB_DIV, po.MJ, ROUND(COALESCE(stk.MAP, mpo.MAP), 2) MAP,
               {', '.join(f'NVL(stk.{m}, 0) {m}' for m in STK)}, {', '.join(f'NVL(sl.{m}, 0) {m}' for m in SALE)},
               sl.LAST_SALE
          FROM stk FULL OUTER JOIN sl ON sl.ST_CD = stk.ST_CD AND sl.G = stk.G AND sl.C = stk.C AND sl.SZ = stk.SZ
          LEFT JOIN sm ON sm.ST_CD = COALESCE(stk.ST_CD, sl.ST_CD)
          LEFT JOIN po ON po.G = COALESCE(stk.G, sl.G) AND po.C = COALESCE(stk.C, sl.C)
          LEFT JOIN mpo ON mpo.G = COALESCE(stk.G, sl.G) AND mpo.C = COALESCE(stk.C, sl.C) AND mpo.SZ = COALESCE(stk.SZ, sl.SZ)""")
    ms = STK + SALE
    cur.execute("BEGIN")
    try:
        for tbl in (OUT_TBL, SZ_TBL, WK_TBL):
            cur.execute(f"DELETE FROM {tbl} WHERE AS_OF_DATE = '{ds}'")
        cur.execute(f"""INSERT INTO {SZ_TBL} (AS_OF_DATE, ST_CD, GEN_ART, CLR, SZ, CLR_FAM, N_VAR, ST_TYP, SEG, "DIV", SUB_DIV,
                            MJ, MAP, {', '.join(ms)}, LAST_SALE, BUILT_AT)
            SELECT {dl}, ST_CD, GEN_ART, CLR, SZ, {_fam_sql('CLR')}, N_VAR, ST_TYP, SEG, "DIV", SUB_DIV, MJ, MAP,
                   {', '.join(ms)}, LAST_SALE, {now}
              FROM TMP_INV_DASH_SZ""")
        cur.execute(f"""INSERT INTO {OUT_TBL} (AS_OF_DATE, ST_CD, GEN_ART, CLR, CLR_FAM, N_VAR, ST_TYP, SEG, "DIV", SUB_DIV,
                            MJ, MAP, {', '.join(ms)}, LAST_SALE, BUILT_AT)
            SELECT {dl}, ST_CD, GEN_ART, CLR, {_fam_sql('CLR')}, SUM(N_VAR), MAX(ST_TYP), MAX(SEG), MAX("DIV"), MAX(SUB_DIV),
                   MAX(MJ), ROUND(AVG(MAP), 2), {', '.join(f'SUM({m})' for m in ms)}, MAX(LAST_SALE), {now}
              FROM TMP_INV_DASH_SZ GROUP BY ST_CD, GEN_ART, CLR""")
        cur.execute(f"""INSERT INTO {WK_TBL} (AS_OF_DATE, ST_CD, SEG, "DIV", SUB_DIV, MJ, WK, WK_END, S_Q, S_V, S_GM)
            SELECT {dl}, s.ST_CD, pm.SEG, pm."DIV", pm.SUB_DIV, pm.MJ, FLOOR(DATEDIFF(day, s.D, {dl}) / 7) WK,
                   DATEADD(day, -7 * FLOOR(DATEDIFF(day, s.D, {dl}) / 7), {dl}), SUM(s.Q), SUM(s.V), SUM(s.GM)
              FROM TMP_INV_DASH_SALE s JOIN ({pm}) pm ON pm.ART = s.ART
             WHERE DATEDIFF(day, s.D, {dl}) BETWEEN 0 AND {WEEKS * 7 - 1}
             GROUP BY 1, 2, 3, 4, 5, 6, 7, 8""")
        cur.execute(f"SELECT COUNT(*), SUM(CL_V), SUM(S30_V) FROM {OUT_TBL} WHERE AS_OF_DATE = '{ds}'")
        r = cur.fetchone()
        cur.execute("SELECT MAX(D) FROM TMP_INV_DASH_SALE")
        sale_to = cur.fetchone()[0]
        secs = round(time.time() - t0, 1)
        totals = {"CL_V": float(r[1] or 0), "S30_V": float(r[2] or 0)}
        cur.execute(f"""INSERT INTO {LOG_TBL} (AS_OF_DATE, BUILT_AT, BUILT_BY, SECS, ROWS_, SALE_TO, TOTALS)
                        SELECT {dl}, {now}, {_q(user or 'system')}, {secs}, {int(r[0] or 0)},
                               {_q(base._fmt_str(sale_to)) + '::DATE' if sale_to else 'NULL'},
                               PARSE_JSON({_q(json.dumps(totals))})""")
        cur.execute("COMMIT")
    except Exception:
        cur.execute("ROLLBACK")
        raise
    logger.info("[sf-inv-dash] built %s: %s option rows in %ss", d, r[0], secs)


def _last_build(cur, d):
    cur.execute(f"""SELECT BUILT_AT, BUILT_BY, SECS, ROWS_, SALE_TO FROM {LOG_TBL}
                     WHERE AS_OF_DATE = '{d.isoformat()}' ORDER BY BUILT_AT DESC LIMIT 1""")
    r = cur.fetchone()
    if not r:
        return None
    return {"asOf": d.isoformat(), "builtAt": base._fmt_str(r[0]), "builtBy": r[1], "secs": float(r[2] or 0),
            "rows": int(r[3] or 0), "saleTo": base._fmt_str(r[4])}


def _resolve(conn, req_as_of, user=None, force=False):
    """The as-of date and its build - the article slice first (its own first-open build), then ours."""
    d, _art_info = art._resolve(conn, req_as_of, user)
    cur = conn.cursor()
    _ensure_tables(cur)
    info = None if force else _last_build(cur, d)
    if info is None:
        if not _build_lock.acquire(timeout=5 if force else 900):
            raise HTTPException(409, "A dashboard build is already running - try again shortly")
        try:
            info = None if force else _last_build(cur, d)
            if info is None:
                _build(conn, d, user)
                info = _last_build(cur, d)
        finally:
            _build_lock.release()
    return d, info


# ── read side ─────────────────────────────────────────────────────────────────────────────────
class DashReq(BaseModel):
    as_of: Optional[str] = None
    col_filters: Optional[dict] = None
    search: Optional[str] = None
    target_days: int = 60
    merch_only: bool = True      # drop SEG NT (non-merchandise) - see the docstring
    grain: str = "option"        # health measured per store x option, or per store x option x SIZE


def _tkey(req):
    """Which stored grain answers: the size table when asked for, or when a size is filtered on."""
    return "sz" if (str(req.grain).lower() == "size" or (req.col_filters or {}).get("SZ")) else "opt"


def _tbl(tkey):
    return SZ_TBL if tkey == "sz" else OUT_TBL


def _where(req, d, tkey="opt"):
    """The page's filters over one table (`tkey`); a filter the table has no column for is skipped."""
    have = _TBL_COLS[tkey]
    tbl_has_opt = tkey != "wk"
    conds = [f"d.AS_OF_DATE = '{d.isoformat()}'"]
    if req.merch_only:
        conds.append("NVL(d.SEG,'') <> 'NT'")
    for c, vals in (req.col_filters or {}).items():
        cu = str(c).strip().upper()
        if cu not in _FEXPR or cu not in have:
            continue
        clean = [str(v) for v in (vals or []) if str(v).strip() != ""][:1000]
        if not clean:
            continue
        lits = [v for v in clean if v != "(blank)"]
        parts = [f"{_FEXPR[cu]} IN ({', '.join(_q(v) for v in lits)})"] if lits else []
        if "(blank)" in clean:
            parts.append(f"NVL({_FEXPR[cu]},'') = ''")
        conds.append("(" + " OR ".join(parts) + ")")
    s = str(req.search or "").strip()
    if s:
        like = _q("%" + s + "%")
        opt = f" OR d.GEN_ART LIKE {_q(s.lstrip('0') + '%')}" if tbl_has_opt else ""
        conds.append(f"(UPPER(d.ST_CD) LIKE UPPER({like}) OR UPPER(sm.ST_NM) LIKE UPPER({like}) "
                     f"OR UPPER(d.MJ) LIKE UPPER({like}){opt})")
    return " WHERE " + " AND ".join(conds)


# Health buckets (store x option with shelf stock) and MAP bands; the page labels them.
BUCKETS = ["C15", "C30", "C60", "C90", "C180", "C999", "SLOW", "DEAD"]
PRICE_BANDS = ["P0", "P200", "P400", "P700", "P1000", "P1500", "PNA"]


def _base_cte(req, d, tkey=None):
    tkey = tkey or _tkey(req)
    t = max(7, min(int(req.target_days or 60), 365))
    st = "d.ST_TYP = 'STORE'"
    cov = "d.SHELF_Q * 30 / d.S30_Q"
    return f"""b AS (
      SELECT d.*, sm.ST_NM, NVL(sm.ZONE, '(blank)') ZONE,
             IFF({st}, GREATEST(d.SHELF_Q, 0), 0) SQ, IFF({st} AND d.SHELF_Q > 0, GREATEST(d.SHELF_V, 0), 0) SV,
             IFF({st}, d.S30_Q, 0) ST30_Q,
             CASE WHEN NOT ({st}) OR d.SHELF_Q <= 0 THEN NULL
                  WHEN d.S90_Q <= 0 THEN 'DEAD' WHEN d.S30_Q <= 0 THEN 'SLOW'
                  WHEN {cov} < 15 THEN 'C15' WHEN {cov} < 30 THEN 'C30' WHEN {cov} < 60 THEN 'C60'
                  WHEN {cov} < 90 THEN 'C90' WHEN {cov} < 180 THEN 'C180' ELSE 'C999' END BKT,
             CASE WHEN d.MAP IS NULL OR d.MAP <= 0 THEN 'PNA' WHEN d.MAP < 200 THEN 'P0' WHEN d.MAP < 400 THEN 'P200'
                  WHEN d.MAP < 700 THEN 'P400' WHEN d.MAP < 1000 THEN 'P700' WHEN d.MAP < 1500 THEN 'P1000' ELSE 'P1500' END PB,
             IFF({st} AND d.SHELF_Q > 0, IFF(d.S30_Q <= 0, d.SHELF_Q, GREATEST(d.SHELF_Q - {t} * d.S30_Q / 30, 0)), 0) EXQ,
             IFF({st} AND d.SHELF_Q > 0 AND d.S90_Q <= 0, d.SHELF_V, 0) DEAD_V,
             IFF({st} AND d.SHELF_Q > 0 AND d.S30_Q <= 0 AND d.S90_Q > 0, d.SHELF_V, 0) SLOW_V,
             IFF({st} AND d.S30_Q > 0, 1, 0) SELL_N,
             IFF({st} AND d.S30_Q > 0 AND d.SHELF_Q <= 0, 1, 0) OOS_N,
             IFF({st} AND d.S30_Q > 0 AND d.SHELF_Q <= 0, d.S30_V / 30, 0) LOST_V,
             IFF({st} AND d.SHELF_Q > 0, 1, 0) LIVE_N
        FROM {_tbl(tkey)} d LEFT JOIN {_SM} sm ON sm.ST_CD = d.ST_CD
        {_where(req, d, tkey)})"""


AGG = STK + SALE + ["SQ", "SV", "ST30_Q", "EXQ", "EXV", "DEAD_V", "SLOW_V", "SELL_N", "OOS_N", "LOST_V", "LIVE_N",
                    "DC_V", "HUB_V", "DC_Q", "HUB_Q"]
_AGG_SQL = {"EXV": "SUM(IFF(SQ > 0, SV * EXQ / SQ, 0))", "DC_V": "SUM(IFF(ST_TYP = 'DC', WH_V, 0))",
            "HUB_V": "SUM(IFF(ST_TYP = 'HUB', WH_V, 0))", "DC_Q": "SUM(IFF(ST_TYP = 'DC', WH_Q, 0))",
            "HUB_Q": "SUM(IFF(ST_TYP = 'HUB', WH_Q, 0))"}
KEYS = ["ST_TYP", "ZONE", "SEG", "DIV", "MJ", "ST_CD", "BKT", "PB"]
SETS = {(): "total", ("ST_TYP",): "st_typ", ("ZONE",): "zone", ("SEG",): "seg", ("DIV",): "div",
        ("MJ",): "mj", ("ST_CD",): "store", ("BKT",): "bkt", ("DIV", "BKT"): "div_bkt", ("PB",): "pb"}
# The long lists (MJ ~1,600 rows, stores ~500) carry only what their tables show.
SLIM = ["CL_V", "SV", "SQ", "WH_V", "TR_V", "NEG_V", "S30_Q", "S30_V", "ST30_Q", "S90_V", "S90_GM",
        "EXV", "DEAD_V", "SLOW_V", "OOS_N", "SELL_N", "LIVE_N", "LOST_V"]


def _agg_select():
    return ", ".join(f"{_AGG_SQL.get(m, f'SUM({m})')} {m}" for m in AGG)


def _num(v):
    return round(float(v or 0), 2)


@router.get("/params")
def dash_params(as_of: Optional[str] = None):
    conn = base._conn()
    try:
        d, info = _resolve(conn, as_of)
        cur = conn.cursor()
        sel = ", ".join(f"ARRAY_AGG(DISTINCT NULLIF(TRIM({_FEXPR[c]}),'')) \"{c}\"" for c in FILTER_COLS)
        cur.execute(f"SELECT {sel} FROM {SZ_TBL} d LEFT JOIN {_SM} sm ON sm.ST_CD = d.ST_CD WHERE d.AS_OF_DATE = '{d.isoformat()}'")
        row = cur.fetchone() or []
        opts = {n[0]: sorted(x for x in (json.loads(v) if v else []) if x is not None) for n, v in zip(cur.description, row)}
        cur.execute(f"SELECT TRIM(ST_CD), MAX(ST_NM) FROM {STORE_TBL} GROUP BY 1")
        have = set(opts.get("ST_CD", []))
        names = {r[0]: r[1] for r in cur.fetchall() if r[0] in have}
        latest = art._latest_et(cur)
        cur.execute(f"SELECT DISTINCT AS_OF_DATE FROM {LOG_TBL} ORDER BY 1 DESC LIMIT 60")
        built = [base._fmt_str(r[0]) for r in cur.fetchall()]
    finally:
        conn.close()
    return {"asOf": d.isoformat(), "latestEtDate": base._fmt_str(latest), "build": info, "options": opts,
            "storeNames": names, "filterCols": FILTER_COLS, "builtDates": built}


def _q_cuts(cur, cte):
    """Every cut in one GROUPING SETS scan."""
    n = len(KEYS)
    sets = ", ".join("(" + ", ".join(s) + ")" for s in SETS)
    cur.execute(f"""WITH {cte}
        SELECT {', '.join(KEYS)}, {', '.join(f'GROUPING({k})' for k in KEYS)}, MAX(ST_NM), MAX(ZONE), {_agg_select()}
          FROM b GROUP BY GROUPING SETS ({sets})""")
    out = {v: [] for v in SETS.values()}
    for r in cur.fetchall():
        grouped = tuple(k for k, g in zip(KEYS, r[n:2 * n]) if not g)
        name = SETS.get(grouped)
        if name is None:
            continue
        row = {k: base._fmt_str(r[KEYS.index(k)]) for k in grouped}
        if name in ("bkt", "div_bkt") and not row.get("BKT"):
            continue
        if name == "store":
            row.update({"ST_NM": base._fmt_str(r[2 * n]), "ZONE": base._fmt_str(r[2 * n + 1])})
        vals = dict(zip(AGG, r[2 * n + 2:]))
        keep = SLIM if name in ("mj", "store") else AGG
        row.update({m: _num(vals[m]) for m in keep})
        out[name].append(row)
    out["store"] = [x for x in out["store"] if x["SV"] or x["S30_V"] or x["WH_V"]]
    return out


def _q_abc(cur, cte):
    """ABC on 90-day sale value at option level, and the Pareto curve in 20 bins of selling options."""
    cur.execute(f"""WITH {cte},
        a AS (SELECT GEN_ART, CLR, SUM(S90_V) S, SUM(CL_V) C, SUM(SV) SH FROM b GROUP BY 1, 2
              HAVING SUM(S90_V) <> 0 OR SUM(CL_V) <> 0),
        r AS (SELECT S, C, SH, SUM(GREATEST(S,0)) OVER (ORDER BY S DESC, GEN_ART, CLR ROWS UNBOUNDED PRECEDING) CS,
                     SUM(GREATEST(S,0)) OVER () TS, ROW_NUMBER() OVER (ORDER BY S DESC, GEN_ART, CLR) RN,
                     COUNT_IF(S > 0) OVER () NS FROM a)
        SELECT 'CLS', CASE WHEN S <= 0 THEN 'N' WHEN CS - S < 0.8 * TS THEN 'A' WHEN CS - S < 0.95 * TS THEN 'B' ELSE 'C' END,
               COUNT(*), SUM(S), SUM(C), SUM(SH) FROM r GROUP BY 1, 2
        UNION ALL
        SELECT 'BIN', TO_VARCHAR(CEIL(RN * 20 / NS)), COUNT(*), SUM(S), SUM(C), SUM(SH) FROM r WHERE S > 0 GROUP BY 1, 2""")
    abc, bins = {}, []
    for kind, k, cnt, s, c, sh in cur.fetchall():
        rec = {"n": int(cnt or 0), "S90_V": _num(s), "CL_V": _num(c), "SV": _num(sh)}
        if kind == "CLS":
            abc[k] = rec
        else:
            bins.append({"bin": int(k), **rec})
    bins.sort(key=lambda x: x["bin"])
    ts = sum(x["S90_V"] for x in bins) or 1
    tc = sum(v["CL_V"] for v in abc.values()) or 1   # every option with stock or sale = the whole stock
    cs = cc = 0
    pareto = [{"pctArt": 0, "pctSale": 0, "pctStock": 0}]
    for x in bins:
        cs += x["S90_V"]
        cc += x["CL_V"]
        pareto.append({"pctArt": x["bin"] * 5, "pctSale": round(cs / ts * 100, 1), "pctStock": round(cc / tc * 100, 1)})
    return abc, pareto


def _q_actions(cur, cte):
    """Option-level action lists: OOS at stores (with what the DCs / hubs hold), dead stock, excess."""
    cur.execute(f"""WITH {cte},
        a AS (SELECT GEN_ART, CLR, MAX(MJ) MJ, MAX("DIV") DV, SUM(OOS_N) OOS_N, SUM(SELL_N) SELL_N, SUM(LOST_V) LOST_V,
                     SUM(WH_Q) WH_Q, SUM(TR_Q) TR_Q, SUM(DEAD_V) DEAD_V, COUNT_IF(DEAD_V > 0) DEAD_ST,
                     SUM(IFF(DEAD_V > 0, SQ, 0)) DEAD_Q, SUM(S30_Q) S30_Q, SUM(S30_V) S30_V, SUM(SQ) SQ, SUM(EXQ) EXQ,
                     SUM(IFF(SQ > 0, SV * EXQ / SQ, 0)) EXV, AVG(MAP) MAP, MAX(LAST_SALE) LAST_SALE
                FROM b GROUP BY 1, 2),
        t AS (SELECT 'OOS' K, a.* FROM a WHERE OOS_N > 0 QUALIFY ROW_NUMBER() OVER (ORDER BY LOST_V DESC) <= 30
              UNION ALL
              SELECT 'DEAD', a.* FROM a WHERE DEAD_V > 0 QUALIFY ROW_NUMBER() OVER (ORDER BY DEAD_V DESC) <= 30
              UNION ALL
              SELECT 'EXCESS', a.* FROM a WHERE EXV > 0 AND S30_Q > 0 QUALIFY ROW_NUMBER() OVER (ORDER BY EXV DESC) <= 30)
        SELECT t.*, pd.DSC, pd.VENDOR, pd.MRP FROM t
          LEFT JOIN (SELECT NVL(NULLIF(TRIM(GEN_ARTICLE_NUMBER),''), ARTICLE_NUMBER) G,
                            COALESCE(MAX(NULLIF(TRIM(GEN_ARTICLE_DESC),'')), MAX(NULLIF(TRIM(ARTICLE_DESCRIPTION),''))) DSC,
                            MAX(NULLIF(TRIM(VENDOR_NAME),'')) VENDOR, MAX(TRY_TO_DOUBLE(TO_VARCHAR(MRP))) MRP
                       FROM {PROD_TBL} GROUP BY 1) pd ON pd.G = t.GEN_ART""")
    cols = [c[0] for c in cur.description]
    txt = {"K", "GEN_ART", "CLR", "MJ", "DV", "DSC", "VENDOR", "LAST_SALE"}
    actions = {"OOS": [], "DEAD": [], "EXCESS": []}
    for r in cur.fetchall():
        rec = {c: (base._fmt_str(v) if c in txt else _num(v)) for c, v in zip(cols, r)}
        actions[rec.pop("K")].append(rec)
    for k, m in (("OOS", "LOST_V"), ("DEAD", "DEAD_V"), ("EXCESS", "EXV")):
        actions[k].sort(key=lambda x: -x[m])
    return actions


def _q_trends(cur, req, d):
    """Weekly sale (13 rolling weeks) and stock over the built dates, both under the page's filters."""
    wf = _where(req, d, "wk").replace("d.ST_TYP", "NVL(sm2.ST_TYP,'OTHER')")
    cur.execute(f"""SELECT d.WK, MAX(d.WK_END), SUM(d.S_Q), SUM(d.S_V), SUM(d.S_GM)
                      FROM {WK_TBL} d LEFT JOIN {_SM} sm ON sm.ST_CD = d.ST_CD
                      LEFT JOIN (SELECT TRIM(ST_CD) ST_CD, MAX(TRIM(ST_TYP)) ST_TYP FROM {STORE_TBL} GROUP BY 1) sm2
                        ON sm2.ST_CD = d.ST_CD
                    {wf} GROUP BY 1 ORDER BY 1 DESC""")
    weekly = [{"wk": int(r[0]), "wkEnd": base._fmt_str(r[1]), "S_Q": _num(r[2]), "S_V": _num(r[3]), "S_GM": _num(r[4])}
              for r in cur.fetchall()]
    tk = _tkey(req)
    sw = _where(req, d, tk).replace(f"d.AS_OF_DATE = '{d.isoformat()}'",
                                f"d.AS_OF_DATE BETWEEN DATEADD(day, -120, '{d.isoformat()}'::DATE) AND '{d.isoformat()}'")
    cur.execute(f"""SELECT d.AS_OF_DATE, SUM(d.CL_V), SUM(IFF(d.ST_TYP = 'STORE' AND d.SHELF_Q > 0, d.SHELF_V, 0)),
                           SUM(d.WH_V), SUM(d.S30_V), SUM(IFF(d.ST_TYP = 'STORE', GREATEST(d.SHELF_Q,0), 0)),
                           SUM(IFF(d.ST_TYP = 'STORE', d.S30_Q, 0))
                      FROM {_tbl(tk)} d LEFT JOIN {_SM} sm ON sm.ST_CD = d.ST_CD
                    {sw} GROUP BY 1 ORDER BY 1""")
    stock = [{"date": base._fmt_str(r[0]), "CL_V": _num(r[1]), "SV": _num(r[2]), "WH_V": _num(r[3]), "S30_V": _num(r[4]),
              "COVER": round(float(r[5] or 0) * 30 / float(r[6]), 1) if r[6] else None} for r in cur.fetchall()]
    return weekly[::-1], stock


SZ_KEYS = ["CLR_FAM", "SZ", "CLR"]
SZ_SETS = {("CLR_FAM",): "clrFam", ("SZ",): "size", ("CLR_FAM", "SZ"): "famSize", ("CLR",): "clr"}


def _q_sizes(cur, req, d):
    """Colour and size, always at the size grain: the size curve, colour family / shade mix, the colour
    x size grid, and per store which sizes are short and which are in excess under the page's filters."""
    cte = _base_cte(req, d, "sz")
    n = len(SZ_KEYS)
    sets = ", ".join("(" + ", ".join(x) + ")" for x in SZ_SETS)
    cur.execute(f"""WITH {cte}
        SELECT {', '.join(SZ_KEYS)}, {', '.join(f'GROUPING({k})' for k in SZ_KEYS)}, {_agg_select()}
          FROM b GROUP BY GROUPING SETS ({sets})""")
    out = {v: [] for v in SZ_SETS.values()}
    for r in cur.fetchall():
        grouped = tuple(k for k, g in zip(SZ_KEYS, r[n:2 * n]) if not g)
        name = SZ_SETS.get(grouped)
        if name is None:
            continue
        vals = dict(zip(AGG, r[2 * n:]))
        row = {k: base._fmt_str(r[SZ_KEYS.index(k)]) or "(blank)" for k in grouped}
        row.update({m: _num(vals[m]) for m in SLIM + ["WH_Q", "SQ", "EXQ"]})
        out[name].append(row)
    out["clr"] = sorted(out["clr"], key=lambda x: -(x["SV"] + x["S30_V"]))[:60]
    # Judged per STORE x SIZE over everything the filters leave (e.g. M_JEANS + BLACK): a size is SHORT
    # at a store when it sold in 30 days and has under 15 days of shelf (none at all = out of stock);
    # EXCESS when its shelf is beyond the target cover, or it has shelf and no 30-day sale. Judging
    # per option instead makes every size both at once - some option is always short, another over.
    t = max(7, min(int(req.target_days or 60), 365))
    cur.execute(f"""WITH {cte},
        ss AS (SELECT ST_CD, MAX(ST_NM) ST_NM, MAX(ZONE) ZONE, NVL(NULLIF(SZ,''),'(blank)') SZ,
                      SUM(SQ) SQ, SUM(SV) SV, SUM(ST30_Q) SQ30, SUM(IFF(ST_TYP = 'STORE', S30_V, 0)) SV30
                 FROM b WHERE ST_TYP = 'STORE' GROUP BY 1, 4 HAVING SUM(SQ) > 0 OR SUM(ST30_Q) > 0),
        k AS (SELECT ss.*,
                     IFF(SQ30 > 0 AND SQ * 30 / SQ30 < 15, 1, 0) SHORT,
                     IFF(SQ30 > 0 AND SQ <= 0, 1, 0) OOS,
                     IFF(SQ > 0 AND (SQ30 <= 0 OR SQ * 30 / SQ30 > {t}), 1, 0) EXCESS,
                     IFF(SQ > 0, GREATEST(SQ - {t} * GREATEST(SQ30, 0) / 30, 0), 0) EXQ,
                     IFF(SQ30 > 0 AND SQ <= 0, SV30 / 30, 0) LOST_V FROM ss),
        top_sz AS (SELECT SZ FROM k GROUP BY 1 ORDER BY SUM(SQ30) DESC, SUM(SQ) DESC LIMIT 16)
        SELECT 'S', ST_CD, MAX(ST_NM), MAX(ZONE), NULL, SUM(SQ), SUM(SV), SUM(SQ30), SUM(SV30), SUM(OOS), SUM(SHORT), SUM(EXCESS),
               SUM(LOST_V), SUM(EXQ), SUM(IFF(SQ > 0, SV * EXQ / SQ, 0)),
               LISTAGG(IFF(SHORT = 1, SZ, NULL), ', ') WITHIN GROUP (ORDER BY SZ),
               LISTAGG(IFF(EXCESS = 1, SZ, NULL), ', ') WITHIN GROUP (ORDER BY SZ)
          FROM k GROUP BY 2
        UNION ALL
        SELECT 'C', ST_CD, NULL, NULL, SZ, SQ, SV, SQ30, SV30, OOS, SHORT, EXCESS, LOST_V, EXQ,
               IFF(SQ > 0, SV * EXQ / SQ, 0), NULL, NULL
          FROM k WHERE SZ IN (SELECT SZ FROM top_sz)""")
    cols = ["K", "ST_CD", "ST_NM", "ZONE", "SZ", "SQ", "SV", "SQ30", "SV30", "OOS_N", "SHORT_N", "EXCESS_N",
            "LOST_V", "EXQ", "EXV", "SHORT_SZ", "EXCESS_SZ"]
    txt = {"K", "ST_CD", "ST_NM", "ZONE", "SZ", "SHORT_SZ", "EXCESS_SZ"}
    finder = {"stores": [], "cells": []}
    for r in cur.fetchall():
        rec = {c: (base._fmt_str(v) if c in txt else _num(v)) for c, v in zip(cols, r)}
        kind = rec.pop("K")
        if kind == "S":
            finder["stores"].append(rec)
        else:
            finder["cells"].append({k: rec[k] for k in ("ST_CD", "SZ", "SQ", "SQ30", "OOS_N", "SHORT_N", "EXCESS_N", "LOST_V", "EXQ")})
    return out, finder


def _on_own_conn(fn, *a):
    conn = base._conn()
    try:
        return fn(conn.cursor(), *a)
    finally:
        conn.close()


@router.post("/data")
def dash_data(req: DashReq):
    conn = base._conn()
    try:
        d, info = _resolve(conn, req.as_of)
    finally:
        conn.close()
    key = json.dumps([d.isoformat(), (info or {}).get("builtAt"), req.col_filters or {}, req.search or "",
                      req.target_days, req.merch_only, _tkey(req)], sort_keys=True, default=str)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL_S:
        return hit[1]
    cte = _base_cte(req, d)
    # Independent statements on their own sessions - Snowflake runs them side by side.
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_sz = ex.submit(_on_own_conn, _q_sizes, req, d)
        f_cuts = ex.submit(_on_own_conn, _q_cuts, cte)
        f_act = ex.submit(_on_own_conn, _q_actions, cte)
        f_tr = ex.submit(_on_own_conn, _q_trends, req, d)
        f_abc = ex.submit(_on_own_conn, _q_abc, cte)
        out = f_cuts.result()
        total = (out.pop("total") or [{m: 0.0 for m in AGG}])[0]
        actions = f_act.result()
        weekly, stock_trend = f_tr.result()
        abc, pareto = f_abc.result()
        sizes, finder = f_sz.result()
    res = {"asOf": d.isoformat(), "build": info, "targetDays": req.target_days, "merchOnly": req.merch_only,
           "grain": _tkey(req), **sizes, "storeFinder": finder,
           "total": total, **out, "abc": abc, "pareto": pareto, "actions": actions, "weekly": weekly,
           "stockTrend": stock_trend}
    if len(_cache) > 64:
        _cache.clear()
    _cache[key] = (time.time(), res)
    return res


class BuildReq(BaseModel):
    as_of: Optional[str] = None
    user: Optional[str] = None


@router.post("/build")
def dash_build(req: BuildReq):
    conn = base._conn()
    try:
        d, info = _resolve(conn, req.as_of, req.user, force=True)
    finally:
        conn.close()
    _cache.clear()
    return {"asOf": d.isoformat(), "build": info}

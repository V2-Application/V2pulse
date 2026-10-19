"""
Snowflake MIS — PRODUCT MASTER (mounted at /sf-prod-master).

A READ-ONLY WINDOW onto V2RETAIL.SILVER.VW_PRODUCT_MASTER, the article master every MIS page
already enriches from: it is what sf_var_combined's PROD_TBL points at, what stamps MAJ_CAT and SEG
onto the day-wise facts, and what the report builder's product dimensions are read through. The
sidebar carried a Store Master and no Product Master, so the one attribute set an MIS reader could
not look up was the one under every article number on the screen.

READ-ONLY, AND NOT AS A POLICY — THERE IS NOTHING TO UPLOAD. The MIS store master is a workbook the
MIS team loads (AKS_SILVER.SLV_MIS_STORE_MASTER, its own Bronze/Silver pair). This is a VIEW over
SAP: BRONZE.MARA joined to MAKT (description), SILVER.VW_ZMC_MASTER (the SEG / DIV / SUB_DIV /
MAJ_CAT / SUB_CAT hierarchy, off MATKL), LFA1 + BUT000 (vendor and the merge parent it rolls up to,
resolved recursively), ET_ART_BROADER_MENU_DATA (the 86 M_* merchandising attributes, at GENERIC
article grain) and the two rationalisation maps ET_ZMM_COLOR_MAP_RFC / ET_ZMM_SIZE_MAPPING_GET_RFC.
A row appears here when SAP creates the article; there is no file behind it and so no uploader,
no template and no delete.

NOT AKS_SILVER.SLV_DIM_PRODUCT. That is the uploaded master at /aks/product-master and it is a
different object at a different GRAIN - one row per MAJ_CAT (~443 categories, ASP / ACP / density /
bookfold) driving the plan engines. This one is one row per 13-digit ARTICLE, 3.71M of them. The
two masters are as separate as the two store masters, and nothing here reaches that one.

Measured 17-Sep-2026: 3,711,835 articles, 1,194,463 generic parents, 4,234 major categories;
3,067,765 rows in SEG APP / GM, which is the universe the MIS loaders keep.

TWO COLUMNS ARE ALWAYS BLANK AND THE VIEW IS WHY. ART_STATUS and NATURE_OF_ARTICLE are selected as
literal NULL inside VW_PRODUCT_MASTER - measured 0 non-null rows of 3.71M. They are listed here
because they are columns of the master and a reader looking for them should find them and see they
are empty, rather than conclude the page dropped them; they are deliberately NOT filterable, since
a tick-list with nothing in it is a worse answer than no tick-list.

TWO MORE NAMES TO READ CAREFULLY:
  MRP        is MARA.FREE_CHAR, the article's price, as TEXT with two decimals ('295.00') since
             13-Oct-2026 (db-scripts/459). Until then the view mapped it to FASHGRD, the fashion
             GRADE letter (F / C / RF), so older exports show letters here.
  F_GRC_DATE / L_GRC_DATE  (DATE, since 13-Oct-2026, db-scripts/468) are the first / last GRC
             (movement type 101 only) of the GENERIC article, across all its variants, from
             SILVER.GRC_LIVE_HR. Blank = no 101 GRC since 25-Dec-2021, where MATDOC starts.
  MERCHANDISE_CATEGORY_DESCRIPTION and MAJOR_CATEGORY_DESCRIPTION are the SAME source column
             (MC_DESC) under two names - identical on every row, 4,234 values each. Both are kept
             because both names are in use downstream.
"""
import json
import logging
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from . import sf_var_combined as base
from .sf_gandola import _stream_csv

logger = logging.getLogger(__name__)
router = APIRouter()

TBL = "V2RETAIL.SILVER.VW_PRODUCT_MASTER"

# ── THE COLUMN CATALOGUE, in display order and grouped the way the picker shows it ────────────
#
# Grouped rather than flat because there are 124 of them and 86 are the M_* merchandising block:
# a flat list is a wall, and the four small groups are what anybody actually opens the page for.
# The GROUPS are published on /params, so the picker's headings and this order cannot drift.
ARTICLE_COLS = [
    "ARTICLE_NUMBER", "ARTICLE_DESCRIPTION", "GEN_ARTICLE_NUMBER", "GEN_ARTICLE_DESC",
    "ARTICLE_TYPE", "ART_STATUS", "ARTICLE_CREATION_DATE", "F_GRC_DATE", "L_GRC_DATE",
    "ARTICLE_COST", "MRP", "RANGE_SEGMENT", "NATURE_OF_ARTICLE", "VENDOR_DESIGN",
    # db-scripts/484: placeholders, NULL on every row until a source is given.
    "SSN", "PAK_SZ", "REF_ART", "ATT_TYP",
]
HIER_COLS = [
    "SEGMENT", "DIVISION", "SUB_DIVISION",
    "MAJOR_CATEGORY_CODE", "MAJOR_CATEGORY_DESCRIPTION",
    "SUB_CATEGORY_CODE", "SUB_CATEGORY_DESCRIPTION",
    "MERCHANDISE_CATEGORY_CODE", "MERCHANDISE_CATEGORY_DESCRIPTION",
]
# COLOR / SIZE1 are SAP's own stamps; the NEW_* pair is what merchandising has renamed them to,
# COALESCEd back onto the stamp inside the view so neither is ever blank where the stamp is not.
# Which means you cannot read "was this mapped?" off NEW_COLOR_DESC alone - compare it to COLOR.
CLR_SIZE_COLS = [
    "COLOR", "COLOR_CODE", "NEW_COLOR_DESC", "NEW_COLOR_CODE", "COLOR_MAP_DATE",
    "SIZE1", "NEW_SIZE_DESC", "SIZE_MAP_DATE", "SIZE_DIMENSION",
]
# The vendor the article was bought from, and the MERGE parent it rolls up to - the recursive
# LFA1/BUT000 walk inside the view, so a chain of renamed vendors reports its living head.
VENDOR_COLS = [
    "VENDOR_CODE", "VENDOR_NAME", "VENDOR_CITY",
    "MERGE_VENDOR_CODE", "MERGE_VENDOR_NAME", "MERGE_VENDOR_CITY",
]
# ET_ART_BROADER_MENU_DATA, all 86, in the view's own order. AT GENERIC ARTICLE GRAIN: the view
# joins them on LEFT(MATNR,10), so every colour and size of one style carries the same values.
MERCH_COLS = [
    "M_ACC_COL", "M_ADD_ACC", "M_ADD_ACC1", "M_BLT_MAIN_STYLE", "M_BLT_SIZE", "M_BTM_FOLD",
    "M_BTN_MAIN_MVGR", "M_BUYING_TYPE", "M_COLLAR", "M_COLLAR_SIZE", "M_COUNT", "M_DC_EDGE_LOOP",
    "M_DC_SUB_STYLE", "M_EMB_TYPE", "M_EMBROIDERY", "M_FAB", "M_FAB_WEIGHT", "M_FAB2", "M_FIT",
    "M_FO_BTN_STYLE", "M_GSM", "M_LENGTH", "M_LYCRA", "M_MACRO_MVGR", "M_MAIN_MVGR",
    "M_MAIN_STYLE", "M_NECK_BAND", "M_NECK_BAND_STYLE", "M_OUNZ", "M_PATCH_TYPE", "M_PATCHES",
    "M_PATTERN", "M_PD", "M_PLACEMENT", "M_PLACKET", "M_POCKET", "M_PRINT_PLACEMENT",
    "M_PRINT_STYLE", "M_PRINT_TYPE", "M_SET", "M_SHADE", "M_SLEEVES_MAIN_STYLE",
    "M_SUB_STYLE_BLT", "M_SUB_STYLE_BTN_CLR", "M_WASH", "M_WASH_COLOR", "M_WEAVE_1", "M_WEAVE_2",
    "M_WEAVE_3", "M_YARN", "M_ZIP", "M_ZIP_COL", "M_FAB_DIV", "M_YARN_02", "M_COMPOSITION",
    "M_CONSTRUCTION", "M_FINISH", "M_FAB_MAIN_MVGR_1", "M_FAB_MAIN_MVGR_2", "M_WEAVE_01",
    "M_WEAVE_02", "M_NECK_TYPE", "M_NECK_STYLE", "M_COLLAR_TYPE", "M_BLT_TYPE", "M_BLT_STYLE",
    "M_BODY_STYLE", "M_DC_STYLE", "M_ZIP_TYPE", "M_BTN_TYPE", "M_PATCH_STYLE", "M_PATCHE_TYPE",
    "M_EMBROIDERY_STYLE", "M_FAB_VDR", "M_IMP_ATBT", "M_WIDTH", "M_COLLAR_STYLE",
    "M_NO_OF_POCKET", "M_SLEEVE_FOLD", "M_HTRF_STYLE", "M_BTN_CLR", "M_DC_SHAPE", "M_HTRF_TYPE",
    "M_EMB_PLACEMENT", "M_AGE_GROUP", "M_EXTRA_POCKET",
]
GROUPS = [
    ("Article", ARTICLE_COLS),
    ("Hierarchy", HIER_COLS),
    ("Colour & size", CLR_SIZE_COLS),
    ("Vendor", VENDOR_COLS),
    ("Merchandising (M_*)", MERCH_COLS),
]
ALL_COLS = [c for _g, cols in GROUPS for c in cols]

# What the grid opens with. A 124-column grid opens as a horizontal scrollbar and nothing else, so
# the default is the identity, the hierarchy and the colour/size names - the columns somebody looks
# an article up BY. Everything else is one tick away in the picker and rides the same query.
DEFAULT_COLS = [
    "ARTICLE_NUMBER", "ARTICLE_DESCRIPTION", "GEN_ARTICLE_NUMBER", "GEN_ARTICLE_DESC",
    "ARTICLE_TYPE", "ARTICLE_CREATION_DATE", "F_GRC_DATE", "L_GRC_DATE", "MRP", "RANGE_SEGMENT",
    "SEGMENT", "DIVISION", "SUB_DIVISION", "MAJOR_CATEGORY_DESCRIPTION",
    "SUB_CATEGORY_DESCRIPTION", "COLOR", "NEW_COLOR_DESC", "SIZE1", "NEW_SIZE_DESC",
    "VENDOR_NAME", "M_MACRO_MVGR", "M_MAIN_MVGR",
]

# THE TICK-LISTS, and the cardinality is the whole selection rule. Measured 17-Sep-2026 over the
# full view: SEGMENT 6, RANGE_SEGMENT 6, MRP 4 (2,244 since db-scripts/459 made it the price), DIVISION 36, M_FIT 51, VENDOR_NAME 53,
# ARTICLE_TYPE 95, SUB_DIVISION 233, M_MAIN_MVGR 725, NEW_SIZE_DESC 915, M_MACRO_MVGR 1,017,
# MAJOR_CATEGORY_DESCRIPTION 4,234.
#
# COLOR (7,540) and NEW_COLOR_DESC (6,984) are deliberately NOT here: a tick-list of seven thousand
# colours is a scrolling wall, and the search box answers "which article is this colour" directly.
# MAJOR_CATEGORY_DESCRIPTION is kept in spite of its 4,234 because it is THE filter this page is
# opened for - and the picker has a search box, which is what makes a long list usable at all.
#
# ART_STATUS and NATURE_OF_ARTICLE are absent because the view selects NULL for both: their lists
# would come back empty and read as "no data loaded" rather than "this column is not sourced".
FILTER_COLS = [
    "SEGMENT", "DIVISION", "SUB_DIVISION", "MAJOR_CATEGORY_DESCRIPTION", "ARTICLE_TYPE",
    "RANGE_SEGMENT", "MRP", "VENDOR_NAME", "M_MACRO_MVGR", "M_MAIN_MVGR", "M_FIT",
    "NEW_SIZE_DESC",
]
# The columns the search box matches. Article number is TEXT in this view, so a 13-digit paste
# matches it directly; the two descriptions are what a word search is for.
SEARCH_COLS = ["ARTICLE_NUMBER", "ARTICLE_DESCRIPTION", "GEN_ARTICLE_NUMBER", "GEN_ARTICLE_DESC"]

# THE MIS UNIVERSE, as a switch rather than as a default. Every MIS loader keeps SEG APP / GM with
# a non-blank major category, so the 3.07M rows behind that filter are the articles that can ever
# appear in a report. The page opens on the WHOLE master anyway, because this is a master viewer
# and silently hiding 644,070 rows of it would be the page lying about what it is showing.
UNIVERSE_PRED = ("TRIM(NVL(SEGMENT,'')) IN ('APP','GM') "
                 "AND TRIM(NVL(MAJOR_CATEGORY_DESCRIPTION,'')) <> ''")

# The grid pages 200 at a time. The ceiling is for a caller asking for one big slab; the CSV export
# streams and is not capped by it.
MAX_LIMIT = 50_000

# /params is four scans of a 3.7M-row view for lists that change when SAP creates an article, so it
# is cached exactly like the variant page's. `refresh=1` forces a rebuild.
_PARAMS_TTL_S = 15 * 60
_params_cache = {"at": 0.0, "data": None}


def _col(c):
    """A quoted, validated column reference. Anything not in the catalogue never reaches the SQL."""
    if c not in ALL_COLS:
        raise HTTPException(400, f"unknown column '{c}'")
    return f'"{c}"'


def _cols(requested):
    """The projection for a request: the caller's picks in CATALOGUE order, never request order.

    Order is fixed here so a CSV taken today lines up with one taken after somebody re-ticked the
    columns in a different sequence, and so the grid's headers cannot arrive shuffled."""
    if not requested:
        return list(DEFAULT_COLS)
    want = {str(c).strip().upper() for c in requested}
    picked = [c for c in ALL_COLS if c in want]
    if not picked:
        raise HTTPException(400, "none of the requested columns exist on the product master")
    return picked


@router.get("/params")
def prod_params(refresh: int = 0):
    """The tick-list options and the headline counts, both read from the view, never assumed."""
    if not refresh and _params_cache["data"] is not None \
            and (time.time() - _params_cache["at"]) < _PARAMS_TTL_S:
        return {**_params_cache["data"], "cached": True}

    conn = base._conn()
    try:
        cur = conn.cursor()
        # ONE PASS for every option list. Twelve separate DISTINCTs is twelve scans of 3.7M rows to
        # fill twelve dropdowns - the same reason the bin report aggregates its lists together.
        sel = ", ".join(f"ARRAY_AGG(DISTINCT NULLIF(TRIM({_col(c)}),'')) \"{c}\""
                        for c in FILTER_COLS)
        cur.execute(f"SELECT {sel} FROM {TBL}")
        names = [d[0] for d in cur.description]
        row = cur.fetchone() or []
        opts = {}
        for n, v in zip(names, row):
            vals = [x for x in (json.loads(v) if v else []) if x is not None]
            opts[n] = sorted(vals)

        # The headline, in one statement. COLOR_MAP_DATE / SIZE_MAP_DATE hold ONE value across the
        # whole view each - they date the rationalisation MAP, not the article - so MAX is the map's
        # own stamp and the page prints it as such.
        cur.execute(f"""
            SELECT COUNT(*), COUNT(DISTINCT ARTICLE_NUMBER), COUNT(DISTINCT GEN_ARTICLE_NUMBER),
                   COUNT(DISTINCT MAJOR_CATEGORY_DESCRIPTION),
                   COUNT_IF({UNIVERSE_PRED}),
                   MAX(COLOR_MAP_DATE), MAX(SIZE_MAP_DATE), MAX(ARTICLE_CREATION_DATE)
              FROM {TBL}""")
        r = cur.fetchone() or []
        stats = {"rows": int(r[0] or 0), "articles": int(r[1] or 0), "genArticles": int(r[2] or 0),
                 "majCats": int(r[3] or 0), "universeRows": int(r[4] or 0),
                 "colorMapDate": base._fmt_str(r[5]), "sizeMapDate": base._fmt_str(r[6]),
                 "latestArticle": base._fmt_str(r[7])}
    finally:
        conn.close()

    out = {"table": TBL, "groups": [{"label": g, "cols": cols} for g, cols in GROUPS],
           "columns": ALL_COLS, "defaultCols": DEFAULT_COLS,
           "filterCols": FILTER_COLS, "searchCols": SEARCH_COLS,
           "options": opts, "stats": stats,
           # The two the view sources as literal NULL. Published rather than described in prose so
           # the page can grey them in the picker instead of a reader ticking one and seeing dashes.
           "emptyCols": ["ART_STATUS", "NATURE_OF_ARTICLE",
                         "SSN", "PAK_SZ", "REF_ART", "ATT_TYP"]}
    _params_cache["at"] = time.time()
    _params_cache["data"] = out
    return {**out, "cached": False}


class ProdReq(BaseModel):
    col_filters: Optional[dict] = None   # {COL: [values]}
    search: Optional[str] = None
    universe: bool = False               # MIS universe only - SEG APP/GM, non-blank MAJ_CAT
    columns: Optional[list] = None
    page: int = 1
    limit: int = 200
    sort_col: Optional[str] = None
    sort_dir: Optional[str] = "asc"


def _where(req):
    conds = []
    if bool(getattr(req, "universe", False)):
        conds.append(f"({UNIVERSE_PRED})")

    for c, vals in (req.col_filters or {}).items():
        cu = str(c).strip().upper()
        if cu not in FILTER_COLS:
            continue
        clean = [str(v) for v in (vals or []) if str(v).strip() != ""]
        if not clean:
            continue
        # '(blank)' is a real choice in the tick-list, so it has to mean IS NULL / '' rather than
        # match the literal text nobody has. Same token the options list uses.
        lits = [v for v in clean if v != "(blank)"]
        parts = []
        if lits:
            parts.append(f"TRIM({_col(cu)}) IN ({', '.join(base._q(v) for v in lits)})")
        if "(blank)" in clean:
            parts.append(f"NVL(TRIM({_col(cu)}),'') = ''")
        conds.append("(" + " OR ".join(parts) + ")")

    s = str(req.search or "").strip()
    if s:
        like = base._q("%" + s + "%")
        conds.append("(" + " OR ".join(f"UPPER({_col(c)}) LIKE UPPER({like})"
                                       for c in SEARCH_COLS) + ")")
    return (" WHERE " + " AND ".join(conds)) if conds else ""


def _order(req):
    direction = "DESC" if str(req.sort_dir or "").lower() == "desc" else "ASC"
    sc = str(req.sort_col or "")
    # NULLS LAST on every sort: this view COALESCEs some columns and leaves others blank on
    # millions of rows, and Snowflake orders DESC with NULLS FIRST - so "sort by vendor, biggest
    # first" opened on a page of dashes and read as an empty grid rather than as a sort.
    if sc in ALL_COLS:
        return f" ORDER BY {_col(sc)} {direction} NULLS LAST, \"ARTICLE_NUMBER\""
    return ' ORDER BY "ARTICLE_NUMBER"'


@router.post("/result")
def prod_result(req: ProdReq):
    page = max(1, int(req.page or 1))
    limit = max(1, min(int(req.limit or 200), MAX_LIMIT))
    cols = _cols(req.columns)
    where = _where(req)
    conn = base._conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {TBL}{where}")
        total = int(cur.fetchone()[0] or 0)
        cur.execute(f"SELECT {', '.join(_col(c) for c in cols)} FROM {TBL}{where}{_order(req)} "
                    f"LIMIT {limit} OFFSET {(page - 1) * limit}")
        names = [c[0] for c in cur.description]
        rows = [[base._fmt_str(v) for v in r] for r in cur.fetchall()]
    finally:
        conn.close()
    return {"table": TBL, "columns": names, "rows": rows, "total": total,
            "page": page, "limit": limit, "shown": len(rows),
            "universe": bool(getattr(req, "universe", False))}


@router.post("/export")
def prod_export(req: ProdReq):
    """The WHOLE filtered selection as CSV, streamed - not the page.

    RAW COLUMN NAMES in the header, the same rule the variant grid's export follows: a header in a
    file is the column you can query against, and a prettified one is how a file and a screen come
    to disagree about what a column is called."""
    cols = _cols(req.columns)
    conn = base._conn()
    cur = conn.cursor()
    cur.execute(f"SELECT {', '.join(_col(c) for c in cols)} FROM {TBL}{_where(req)}{_order(req)}")
    hdr = [c[0] for c in cur.description]
    return _stream_csv(cur, hdr, "PRODUCT_MASTER.csv")

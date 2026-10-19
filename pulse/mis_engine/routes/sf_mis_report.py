"""MIS report builder — saved report definitions over the day-wise variant facts.

A definition is {group_by, windows, measures, stock_tags, filters, name}. Nothing is stored per
report: every run re-aggregates from DAY_WISE_ST_VAR_SALE / _STOCK at the report's own grain.

WHY RE-AGGREGATE RATHER THAN ROLL UP THE FINISHED VARIANT REPORT
----------------------------------------------------------------
Grouping the 292-column variant output would break three column families at once:

  AVG_*        averages a group's DAILY total over the days it was active. Averaging the
               per-variant averages is an average of averages, which is not the average.
  *_DAYS       counts DAYS. Summing a variant's day count across a maj-cat multiplies the
               month by the number of variants in it.
  S_*          are CUMULATIVE per row - a 27-Aug and a 28-Aug row both contain 1-27 Aug - so
               adding them double-counts the overlap. This is the exact fault that once made an
               MTD column read 71 lakh where the truth was 5.5 lakh.

THE SHAPE THAT AVOIDS ALL THREE
-------------------------------
Aggregate to (dimensions, DT) FIRST, then apply the windows to those daily group totals:

    daily  = SELECT dims, DT, SUM(SALE_V) ... FROM fact JOIN masters GROUP BY dims, DT
    sale   = SELECT dims,
                    SUM  (CASE WHEN DT in window THEN SV END)                -- totals
                    AVG  (CASE WHEN DT in window AND SV > 0 THEN SV END)     -- true daily average
                    COUNT(CASE WHEN DT in window AND SQ > 0 THEN 1 END)      -- true day count
             FROM daily GROUP BY dims

At variant grain this reduces to exactly what the base report produces, so the two agree by
construction rather than by anyone remembering to keep them in step.

WHEN DT IS NOT A DIMENSION
--------------------------
The windows are anchored on ONE day - the To date - and the report is one row per group "as at"
that date. Anchoring every day in the range and unioning would emit a row per group per day while
claiming not to be grouped by day, and the duplicate rows would silently multiply any total taken
over the result.
"""
import json
import logging
import re
import time
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional

from .. import sf_cancel
from ..worker.auto_gandola_sf import get_sf_read_connection
from .sf_gandola import _stream_csv
from . import sf_var_combined as base

logger = logging.getLogger(__name__)
router = APIRouter()

DEF_TBL = "V2RETAIL.BRONZE.MIS_REPORT_DEF"

# Dimensions a report may group by: the fact's own keys, then everything the two masters carry.
# Derived from the base report's lists, so a column added there is groupable here with no edit.
FACT_DIMS = ["DT", "ST_CD", "ARTICLE_NUMBER", "GEN_ART_NUMBER", "ARTICLE_DESC",
             "COLOR", "SIZE_1", "MAJ_CAT", "SEG"]
# PLANT_COL is appended rather than folded into base.STORE_ATTRS because it comes from a
# DIFFERENT table: STORE_ATTRS is the planning view (666 stores), PLANT_TYPE is the full master
# (705, the 39 DC/hub/office plants included). Grouping a report by it is the row-wise way to ask
# the question the 0099_DC stock tag answers column-wise.
# EVERY product-master column this builder can group by - base's catalogue PLUS the two
# rationalisation names db-scripts/379 added to the view (NEW_COLOR_DESC, NEW_SIZE_DESC).
#
# base keeps those two OUT of PROD_ATTR_MAP deliberately, so they land at the END of the Variant
# Article grid instead of shifting its every sale, stock and ref column two places right. That is
# a rule about ONE grid's column ORDER, and it has nothing to say about which columns are
# GROUPABLE - a builder that inherited it would simply be missing two dimensions for a reason that
# does not apply here. So they are merged back in and are product-master columns like any other:
# same `pv` join, same source name, same filter and distinct-value paths.
PROD_DIM_MAP = {**base.PROD_ATTR_MAP, **base.MAP_ATTR_MAP}
PROD_DIMS = list(PROD_DIM_MAP)
DIM_COLS = (FACT_DIMS + ["GEN_ART_DESC"] + list(base.STORE_ATTRS) + PROD_DIMS
            + [base.PLANT_COL])

# Plain stock tags, then the plant-type splits of the tags that have one. A split is just a tag
# here - it picks like one, saves like one, and yields K_<tag>__<measure> like one - which is why
# nothing below this line has to know the difference except the CASE that reads it.
# Composites last, so a total sits to the right of the parts it sums, and rule tags after those.
# A RULE TAG is a tag here too, and deliberately needs no special case: it picks, saves and yields
# K_<tag>__<measure> like every other one. The single thing that differs is that its CASE reads the
# row's ST_CD as well as its KPI - which base.tag_predicate builds off the `f.KPI` this file
# already passes, and which _joins already covers by asking base.tag_needs_plant().
#
# base.PICK_TAGS (MSA, GRT) ARE HERE TOO, and the ONE thing this file must get right about them is
# that a report's grain is whatever it groups by. A pick is the FIRST NON-ZERO of its members -
# MSA is LQUA storage type E01 at DH24 and E03 at DW01, the same rack named per site - and on the
# variant grid, where a row carries one store, only one member can be filled. Here a report can
# group by MAJ_CAT alone, putting both DCs in one bucket, where first-non-zero would report DH24
# and silently drop DW01. So every call below passes multi_store=True and the tag is emitted as
# the UNION of its members, which IS "each store's pick, summed" - the members are disjoint by
# location, measured. Same number as the variant grid wherever a row carries one store.
ALL_STOCK_TAGS = (list(base.STOCK_TAGS) + base.split_tag_names()
                  + list(base.COMPOSITE_TAGS) + list(base.RULE_TAGS) + list(base.PICK_TAGS))

SALE_MEASURES = list(base.WINDOW_MEASURES)
STOCK_MEASURES = ["STOCK_V", "STOCK_Q"]
WINDOWS = list(base.SALE_WINDOWS) + ["RANGE"]

# -- LAYOUT: the same numbers, turned ninety degrees -------------------------------------------
#
# horizontal  one row per GROUP; every value gets its own COLUMN - S_<win>__<meas>, K_<tag>__<meas>,
#             R_<win>__<meas>, P_<win>__<meas>. Nine windows x five measures is forty-five columns
#             and a horizontal scroll nobody reads past the third screen.
# vertical    one row per (GROUP, MEASURE); the WINDOWS become the columns. The measure names run
#             DOWN the page and TD / MTD / QTR run across it, which is how a management sheet is
#             read and why this was asked for.
# period      one row per PERIOD - TD, MTD, QTR, then each stock tag, then each plan month - and
#             the measure KINDS across the top: QTY, VALUE, GM. This is how a management sheet is
#             written by hand, and it is the turn asked for after the one above: vertical puts the
#             measure down the page and the windows across, which is this one's mirror image.
#
# ONE QUERY, NOT THREE. Both turns are the horizontal one turned inside out IN SNOWFLAKE -
# the wide result becomes a derived table and a small VALUES row-set fans it out. So a cell cannot
# read differently depending on which way round it is displayed, because there is only one
# aggregate underneath all three. The alternative - pivoting in the browser - has to pull rows x
# measures first, which is exactly what put the D-Weekly custom pivot at ~90s before it moved
# server-side.
#
# WHERE STOCK GOES. A stock tag has no window: it is a POSITION as at the To date, which is why it
# is its own column in the wide report rather than one per window. Repeating that one number under
# all nine window columns would be a lie told nine times, so a stock row carries its value in a
# column of its own - AS_AT - and NULL across the windows. Sale, Day Ref and Sale Plan rows are
# NULL in AS_AT for the mirror-image reason. The column only exists when the report has tags.
# WHY IT MERGES. QTY holds the sale quantity on a window row and the stock quantity on a tag row,
# because on that sheet they are the same COLUMN - "how many pieces" - read at a different period.
# A stock position has no gross margin, so the GM cell of a tag row is simply blank. The merge is
# by KIND and only for the three kinds below; a discount, a day count and a percentage keep a
# column of their own, because "VALUE" over a discount would be a column heading that lies.
#
# WHEN IT CANNOT MERGE IT SPLITS, and that is the whole safety of it: two measures of one kind that
# sit on the SAME row family - Sale Q and Avg Sale Q are both quantities of a window - have one
# cell between them and no way to share it. So that kind's column breaks into one column per
# measure, named after the measure. Nothing is ever dropped or silently summed to fit.
LAYOUTS = ["horizontal", "vertical", "period"]
VERT_BLOCK_COL, VERT_MEAS_COL, VERT_AS_AT_COL = "BLOCK", "MEASURE", "AS_AT"
# The measure axis is a ROW set now, so a report with hundreds of stock tags would fan every group
# out by hundreds. Capped rather than left to surprise someone at export time.
MAX_VERT_ROWS = 400

# The one label column of the period layout. It needs no BLOCK beside it, unlike the vertical
# sheet: a window row carries sale, Day Ref and Sale Plan measures at once, so there is no single
# block to name, and the period itself is already unique - the nine window codes, the stock tag
# names and the month tokens share no spelling between them.
PERIOD_COL = "PERIOD"
# The four ROW FAMILIES, which is what decides whether two measures can share a column: measures
# of different families never meet in a cell, because a row belongs to exactly one family.
#   WINDOW  TD / MTD / ... - sale, Day Ref, Sale Plan and GRC measures, read over a window's days
#   STOCK   one row per stock tag - a POSITION as at the To date, so it has no window
#   MONTH   one row per plan month - the whole of that month from the Plan Live 360 snapshot
#   PO      ONE row - the open purchase-order book as it stands now. See the PO block: that view
#           holds only lines still pending, so there is no history to put on any other axis.
FAM_WINDOW, FAM_STOCK, FAM_MONTH, FAM_PO = "WINDOW", "STOCK", "MONTH", "PO"
# The kinds that merge, in the order the sheet wants them across the top. Everything not in here
# keeps its own column - see the note by LAYOUTS.
MERGE_KINDS = ["QTY", "VALUE", "GM"]
# WHICH KIND EACH MEASURE IS. Explicit rather than derived from the name, because the suffix does
# not carry it: GM_V and ACT_GM_VAL end in a value marker and are margins, ACT_AVG_STK_Q is a
# quantity of stock rather than of sale, and TRF_OP_STK ends in neither. A measure LEFT OUT of
# this map gets a column of its own, which is the safe default - DISCOUNT_V, the three day counts
# and LYSP_DAY_CONTRI are all deliberately absent.
_MEAS_KIND = {
    # Sale, over a window
    "SALE_Q": "QTY", "SALE_V": "VALUE", "GM_V": "GM",
    "AVG_SALE_Q": "QTY", "AVG_SALE_V": "VALUE", "AVG_GM_V": "GM",
    # Stock, as at the To date. The TAG is the row here, so the two measures are the whole block.
    "STOCK_Q": "QTY", "STOCK_V": "VALUE",
    # Day Ref - three families, each a sale pair, a margin and an average-stock pair
    "ACT_SALE_Q": "QTY", "ACT_SALE_V": "VALUE", "ACT_GM_VAL": "GM",
    "ACT_AVG_STK_Q": "QTY", "ACT_AVG_STK_V": "VALUE",
    "LYSP_SALE_Q": "QTY", "LYSP_SALE_V": "VALUE", "LYSP_GM_VAL": "GM",
    "LYSP_AVG_STK_Q": "QTY", "LYSP_AVG_STK_V": "VALUE",
    "LYSM_SALE_Q": "QTY", "LYSM_SALE_V": "VALUE", "LYSM_GM_VAL": "GM",
    "LYSM_AVG_STK_Q": "QTY", "LYSM_AVG_STK_V": "VALUE",
    # Sale Plan, over a window
    "SALE_PLAN_Q": "QTY", "SALE_PLAN_V": "VALUE",
    "SALE_WITH_STR_Q": "QTY", "SALE_WITH_STR_V": "VALUE",
    # GRC AND PO ARE DELIBERATELY ABSENT, and not by oversight - see the note by LAYOUTS for what
    # being absent buys. A kind SPLITS for the WHOLE SHEET the moment two of its slots share one
    # row family, so mapping GRC_Q, GRC_GRS_Q and GRC_RET_Q to QTY - three quantities of a WINDOW,
    # the same family sale measures sit in - would break the merged QTY column apart for sale and
    # stock too, and a report that had read QTY for a year would silently grow four columns the day
    # somebody ticked a GRC measure. The six PO quantities would do it again on their own.
    #
    # It is also the truer reading. These are not pieces of sale or pieces of stock: a GRC quantity
    # is pieces RECEIVED and a PO quantity is pieces ON ORDER, and a sheet that stacked all three
    # under one "QTY" heading would be inviting a column total that means nothing. Left out, each
    # keeps a column named after itself, which is the safe default this map already documents.
}
# The ONE month measure that is neither a quantity nor a value. Same single exception the grid's
# K / L divisor already carves out (MONTH_PLAIN in SfMisReports.jsx), and for the same reason:
# scaling 21 cover days would print them as 0.02.
_MONTH_DAY_MEAS = {"TRF_COVER_DAY"}

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

MAX_LIMIT = 5000
_SRC = {"SALE_V": "SV", "SALE_Q": "SQ", "GM_V": "GM", "DISCOUNT_V": "DV"}

# ── Day Ref, RECOMPUTED AT ARTICLE GRAIN ──────────────────────────────────────────────────────
#
# NOT a join to AKS_SILVER.SLV_FCT_DAY_REF. That table is the OUTPUT of SP_AKS_DAY_REF_LOAD, and
# its last step throws away the only thing this builder needs: the proc reads SAP_VBRP line by line
# at ARTICLE level and only collapses to MAJ_CAT in its final GROUP BY. So the article detail is
# not missing from the reference, it is discarded on the way out.
#
# This runs the proc's OWN logic and simply stops before that last collapse, which means the
# reference facts arrive at the same grain as the sale fact and can be aggregated to whatever the
# report groups by - DIV, COLOR, a store attribute, the article itself. Reading the finished
# category table instead could only ever repeat one number across a group, which is why an earlier
# version of this had to refuse every grouping finer than store x major category. That restriction
# is gone, and with it the whole class of "why can I not group this by division" question.
#
# TRANSCRIBED FROM db-scripts/250, SP_AKS_DAY_REF_LOAD. Held to it measurably:
#   * the calendar spine is SLV_LYSP_MASTER_PLANNING, so LYSP and LYSM read each store's OWN
#     LY date. That gap is 354-385 days and set per store - never a flat 364.
#   * sale is SAP_VBRP where FKART_ANA='FP'; quantity negates on SHKZG='X' while NETWR and WAVWR
#     already carry the credit sign; GM = NETWR - WAVWR.
#   * stock is ET_STOCK_DATA at LGORT 0001/0006, read on the exact date (the proc's default
#     P_LOCF_MAX_DAYS=0 - no carry-forward, so an unreported day is absent, not repeated).
#   * ACT stock alone also requires an active store in STORE_PLANT_MASTER; the LY branches do not.
#     That asymmetry is in the source and is kept.
#
# The article bridge is BRONZE.DIM_PRODUCT, not the proc's V_AKS_ART_MAJCAT, because only the
# former carries ARTICLE_NUMBER - and it has to be the same bridge the sale fact used or a report
# grouped by article would line two different vocabularies up against each other. Measured
# 12-Aug-2025 over all of VBRP: both bridges map Rs 6,19,30,068.29 of Rs 6,19,43,449.17, neither
# maps anything the other misses, and they disagree on MAJ_CAT for nothing at all. So the
# substitution costs no tie-out.
#
# IT TIES TO THE PENNY IN TOTAL, AND DELIBERATELY NOT PER CATEGORY. Measured, HB05, 01-Sep-2026:
# grand total LYSP Rs 11,28,908.04 here and Rs 11,28,908.04 in SLV_FCT_DAY_REF - exact. Per
# category it moves: M_JEANS reads Rs 36,264.52 against the table's Rs 38,738.78, and the
# Rs 2,474.26 is found intact under M_BLU_JEANS (Rs 1,475.22) and M_COL_JEANS (Rs 999.04).
#
# That is the LIVE master doing its job. The reference table froze each article's MAJ_CAT at the
# moment its loader ran; this report resolves the whole hierarchy live, from one row of one master
# (see PROD_KEY_MAP in sf_var_combined) - so a re-classified article files under the category it is
# in NOW. Matching the table per-category would mean reading the frozen stamp for Day Ref while
# reading the live one for sale, which would put the two halves of a single row on two different
# classifications. The total is what has to tie, and it does.
REF_CAL_TBL = "V2RETAIL.AKS_SILVER.SLV_LYSP_MASTER_PLANNING"
REF_VBRP = "V2RETAIL.BRONZE.SAP_VBRP"
REF_STOCK_TBL = "V2RETAIL.BRONZE.ET_STOCK_DATA"
REF_PLANT_TBL = "V2RETAIL.BRONZE.STORE_PLANT_MASTER"
REF_DIM_PROD = "V2RETAIL.BRONZE.DIM_PRODUCT"
# The proc's two storage locations. Not the report's stock tags - those are the day-wise stock
# fact's own, a different and much wider set. Narrowing here is what makes the number tie.
REF_STOCK_LGORT = ["0001", "0006"]
# VBRP stores the billing date as text in more than one shape; this is the proc's own coalesce.
REF_VBRP_DATE = ("COALESCE(TRY_TO_DATE(v.FKDAT_ANA, 'YYYYMMDD'), "
                 "TRY_TO_TIMESTAMP(v.FKDAT_ANA)::DATE)")

REF_MEASURES = list(base.REF_MEASURES) + ["LYSP_DAY_CONTRI"]
# HOW EACH ONE AGGREGATES ACROSS DAYS. The flows (sale, GM) SUM. The AVG_STK pair is a stock
# POSITION, so summing it across a window would give stock-days - the error the report's own stock
# block exists to avoid. It averages over the days that reported. Across the DIMENSION both sum;
# that is the inner step and it happens first.
_REF_AVG = {"ACT_AVG_STK_Q", "ACT_AVG_STK_V", "LYSP_AVG_STK_Q", "LYSP_AVG_STK_V",
            "LYSM_AVG_STK_Q", "LYSM_AVG_STK_V"}
# The three date families, and which column of the calendar spine each reads.
REF_KINDS = {"ACT": "CAL_DATE", "LYSP": "LYS_DATE", "LYSM": '"LYSM DATE"'}

# ── SALE PLAN: the Day Wise Plan's own distributed numbers, windowed like everything else ─────
#
# The AKS Day Wise Plan distributes a saved MONTH plan into calendar days, so it already has the
# shape this report wants: one row per day per store per category. WITH_STR_FINAL_V/_Q are the
# "with store" finals - the pair the Day Wise Plan grid itself shows and the pair weekly
# transfer-in reads (db-scripts/282, 342) - so a SALE_PLAN column here is the same number those
# already agree on, summed over a window instead of a fiscal week.
PLAN_TBL = "V2RETAIL.AKS_GOLD.GLD_MONTH_PLAN_DAY_WISE"
PLAN_LOG_TBL = "V2RETAIL.AKS_OPS.MONTH_PLAN_SAVE_LOG"
# The MONTH plan itself - the table the Month Plan Saved page reads. Only five of its ninety-odd
# measures were ever distributed into days, so anything else has to be landed on days here.
PLAN_MONTH_TBL = "V2RETAIL.AKS_GOLD.GLD_MONTH_PLAN_RESULT"
PLAN_MEASURES = ["SALE_PLAN_Q", "SALE_PLAN_V", "SALE_WITH_STR_Q", "SALE_WITH_STR_V"]

# TWO WAYS A PLAN MEASURE REACHES A DAY, and the difference is where the number already lives.
#
# DAY-NATIVE: the day-wise table carries the column already spread across the month's days
# (value x FINAL_DAY_CONTRI, done at Generate time), so the window just sums it. SALE_PLAN_Q/V
# keep their names rather than becoming WITH_STR_FINAL_* - nine saved reports and their published
# SILVER views name the column P_<WIN>__SALE_PLAN_V, and a rename would rewrite all of them.
PLAN_DAY_SRC = {"SALE_PLAN_Q": "WITH_STR_FINAL_Q", "SALE_PLAN_V": "WITH_STR_FINAL_V"}

# MONTH-SOURCED: the column exists only on the month plan, so it is joined on and multiplied by
# the day-wise row's own FINAL_DAY_CONTRI. That is the SAME curve the day-native pair was spread
# with, so a month-sourced column and SALE_PLAN_V are windowed identically and stay comparable.
#
# The join is 1:1 and cannot fan out: within one session GLD_MONTH_PLAN_RESULT is unique on
# PLAN_KEY (measured 12-Sep-2026: 2,298,366 rows = 2,298,366 distinct keys), and every one of the
# day-wise table's 303,030 Sep keys matches exactly one of them on plain equality - none of the
# seven key columns is ever NULL on either side.
#
# A key with FINAL_DAY_CONTRI NULL contributes NOTHING, exactly as it does for SALE_PLAN_V today.
# That is not a rounding matter: 195,373 of Sep-26's 328,338 store x category keys have no day
# contribution, so the day-wise total is 2,800,487,555.68 against the month plan's
# 3,253,589,075.60 - 13.9% of the plan has no day to sit on and is invisible to every window here.
# Spreading it evenly instead would invent a flat curve for those keys, so it is left out.
PLAN_MONTH_SRC = {"SALE_WITH_STR_Q": "SALE_WITH_STR_Q", "SALE_WITH_STR_V": "SALE_WITH_STR_V"}
PLAN_KEY = ["FY", "FY_MONTH", "ST_CD", "SEG", "DIV_CAT", "SUB_DIV", "MAJ_CAT"]

# THE PLAN STOPS AT THE CATEGORY. Its grain is ST_CD x SEG x DIV_CAT x SUB_DIV x MAJ_CAT x
# CAL_DATE - there is no article number in it, because nobody plans a colour in a size. So this
# block is emitted only when every grouped dimension is one the plan can actually resolve: its own
# four hierarchy levels, plus anything hanging off ST_CD (the store master and the plant type).
# Group the report by ARTICLE_NUMBER and the columns come back NULL rather than repeating the
# whole category against each of its articles or inventing a split - see PLAN_NOTE.
#
# Vocabulary verified 12-Sep-2026 against SILVER.VW_PRODUCT_MASTER: SEG 2/2, DIV_CAT 14/14,
# SUB_DIV 57/57 and MAJ_CAT 502/507 of the plan's distinct values match the master exactly, so
# these join the report's dimensions directly with no mapping table.
PLAN_OWN_DIMS = {"ST_CD": "ST_CD", "SEG": "SEG", "DIV": "DIV_CAT",
                 "SUB_DIV": "SUB_DIV", "MAJ_CAT": "MAJ_CAT"}
PLAN_DIMS = set(PLAN_OWN_DIMS) | set(base.STORE_ATTRS) | {base.PLANT_COL}
PLAN_NOTE = ("Sale Plan is planned at store x category. It is returned blank when the report "
             "groups or filters below that - pick ST_CD / SEG / DIV / SUB_DIV / MAJ_CAT or any "
             "store attribute.")

# WHICH SAVED PLAN. The db-scripts/342 rule, verbatim, because weekly transfer-in and the weekly
# purchase plan already read the plan this way and a third answer to "which plan is THE plan" is
# how two pages start quoting different numbers. STATUS NULL reads as ACCEPTED (pre-workflow
# sessions behave as before) and an explicit approval outranks every unreviewed session whenever
# it was saved. Without this the six sessions in the table would simply stack on top of each other.
PLAN_SESSION_SQL = f"""(SELECT d.SESSION_ID
      FROM (SELECT DISTINCT SESSION_ID FROM {PLAN_TBL}) d
      JOIN {PLAN_LOG_TBL} l ON l.SESSION_ID = d.SESSION_ID
     WHERE COALESCE(UPPER(TRIM(l.STATUS)), 'ACCEPTED') = 'ACCEPTED'
     ORDER BY IFF(UPPER(TRIM(l.STATUS)) = 'ACCEPTED', 0, 1) ASC, l.SAVED_AT DESC
     LIMIT 1)"""


def _plan_dim_expr(c):
    """Where a dimension comes from on the PLAN side - its own row, or the store masters."""
    if c in PLAN_OWN_DIMS:
        return f"f.{PLAN_OWN_DIMS[c]}"
    if c == base.PLANT_COL:
        return base.PLANT_FILT_EXPR
    return f"sm.{c}"


def _plan_joins(used):
    """The store masters, on the plan's ST_CD. Never the product view - the plan has no article."""
    sql = ""
    if any(c in base.STORE_ATTRS for c in used):
        sql += f" LEFT JOIN {base.STORE_TBL} sm ON TRIM(sm.ST_CD) = TRIM(f.ST_CD)"
    if base.PLANT_COL in used:
        sql += f" LEFT JOIN {base.plant_lookup_sql()} pt ON pt.ST_CD = TRIM(f.ST_CD)"
    return sql


# ── MONTH PLAN (360): the four monthly plans, read AS AT A PICKED MONTH ────────────────────────
#
# Everything above this line is a WINDOW over days. This block is not. The Space, Transfer In and
# Purchase plans are made once a MONTH and are never distributed into days - MTF_IN_PLAN's grain is
# (ST_CD, MAJ_CAT, MONTH_ID) and there is no day curve anywhere in it. So an "MTD" or "L7D" reading
# of a transfer-in quantity would be an invention, and the honest axis is the one the plan is
# actually written on: pick a MONTH and the column is that month's plan.
#
# That is why this has its own picker rather than joining the Sale Plan list. A Sale Plan column is
# one measure of one WINDOW; a Month Plan column is one measure of one MONTH, and the two axes are
# picked independently - a report can carry both.
#
# SOURCE: the Plan Live 360 SNAPSHOT, not the views behind it. db-scripts/357 measured one page of
# VW_PLAN_LIVE_360 at 4-5 s with the sort and the offset removed - the cost is BUILDING the view
# (a three-way join plus a UNION key spine over ~5M rows), paid on every read. A MIS report is also
# published as a SILVER view and pulled by the scheduled dumps, so that cost would be paid again on
# every one of them. The snapshot is the same numbers the Plan Live 360 page itself shows, which is
# the second reason to read it: a reader comparing the two pages must not find them disagreeing
# because one of them re-resolved a plan in between.
#
# The page owns the refresh button; nothing here rebuilds it. `monthSnapshot` in the catalogue
# carries the snapshot's timestamp so the builder can say which save these columns are from.
MPLAN_TBL = "V2RETAIL.AKS_GOLD.PLAN_LIVE_360_MAT"
MPLAN_RDC_TBL = "V2RETAIL.AKS_GOLD.PLAN_LIVE_360_RDC_MAT"
MPLAN_STATE_TBL = "V2RETAIL.AKS_OPS.PLAN_LIVE_360_STATE"

# The measures, in the Plan Live 360 grid's own order and under its own column names, so a number
# read here and a number read there are the same name as well as the same value.
#
# RAW UNITS. Every MTF quantity is stored in THOUSANDS and the 360 view multiplies it back by 1000
# (db-scripts/355 note (c)), so TRF_IN_STK_Q here is in pieces exactly as SPACE_DISP_Q and the sale
# plan are. Nothing is re-scaled in this file.
MPLAN_STORE_MEASURES = [
    "SALE_W_Q", "SALE_W_V", "SALE_WO_Q", "SALE_WO_V",
    "SPACE_DISP_Q",
    "TRF_SALE_Q", "TRF_COVER_SALE_Q", "TRF_DISP_CL_Q", "TRF_OP_STK", "TRF_IN_STK_Q",
    "TRF_CL_STK", "TRF_SHORT_Q", "TRF_EXCESS_Q", "TRF_COVER_DAY",
]
# The purchase plan. RDC x MAJ_CAT x MONTH and NOT spread across stores - 780 rows a month against
# transfer-in's 259,740. Allocating it by transfer-in share would produce a store-level "purchase
# quantity" nobody planned, so it keeps its own grain and blanks below it, exactly as the 360 page
# keeps it on a separate tab.
MPLAN_RDC_MEASURES = [
    "PP_BGT_PUR_Q", "PP_POS_PO_RAISED", "PP_NEG_PO_RAISED", "PP_DC_STK_Q", "PP_DC_CL_STK_Q",
    "PP_TTL_TRF_OUT_Q", "PP_CM_TRF_OUT_Q", "PP_DC_SHORT_Q", "PP_DC_EXCESS_Q", "PP_DEL_PEND_Q",
]
MONTH_MEASURES = MPLAN_STORE_MEASURES + MPLAN_RDC_MEASURES
# The picker's grouping. Four plans, named as the 360 page names them.
MONTH_MEASURE_GROUPS = {
    "Sale Plan": ["SALE_W_Q", "SALE_W_V", "SALE_WO_Q", "SALE_WO_V"],
    "Space Plan": ["SPACE_DISP_Q"],
    "Transfer In": ["TRF_SALE_Q", "TRF_COVER_SALE_Q", "TRF_DISP_CL_Q", "TRF_OP_STK",
                    "TRF_IN_STK_Q", "TRF_CL_STK", "TRF_SHORT_Q", "TRF_EXCESS_Q", "TRF_COVER_DAY"],
    "Purchase (RDC)": list(MPLAN_RDC_MEASURES),
}
# HOW EACH ONE AGGREGATES ACROSS THE DIMENSION. Every quantity and value SUMS - they are flows and
# stock positions at one month, and two categories' transfer-in adds up. COVER DAY does not: it is
# "how many days of cover this plan buys", a RATE, and summing it over 500 categories would produce
# a five-figure day count. It averages over the rows that carry one.
MPLAN_AVG = {"TRF_COVER_DAY"}
# COVER DAY IS EMPTY TODAY, and that is the source, not this block: MTF_IN_PLAN.COVER_DAY is NULL
# on all 2,337,660 of its rows across every month it holds (measured 14-Sep-2026), so the Plan Live
# 360 page's Cover Days column is blank as well. It is offered here for parity with that page - the
# day the transfer-in engine starts writing it, the column fills by itself.

# WHICH DIMENSIONS EACH HALF CAN RESOLVE, and the rule is the Sale Plan's rule for the same reason:
# the plan stops at the category and must not GUESS below it. Group by COLOR and the honest answer
# is a blank column, not the category's whole transfer-in stamped onto each of its colours.
#
# The store half knows ST_CD and MAJ_CAT of its own, and anything hanging off ST_CD through the
# store masters. It does NOT know SEG / DIV / SUB_DIV: unlike GLD_MONTH_PLAN_RESULT, the 360
# snapshot carries only MAJ_CAT, and deriving the parent levels here would mean a second, private
# answer to "which subdivision is this category in".
#
# Vocabulary measured 14-Sep-2026 against SILVER.VW_PRODUCT_MASTER: 512 of the snapshot's 520
# MAJ_CAT values match the live master; the eight that do not (HH_STEEL_CUTLERY, T_FUR_TOYS,
# CLN_FLOOR MOP, HF_QUILT, SI_LUDO, T_MT_PULL_ALONG_TOYS, CLN_P_DUSTPAN, CLN_NAPHTHALENE_BALLS)
# are categories the master no longer carries, and they simply find no row to join to.
MPLAN_OWN_DIMS = {"ST_CD": "ST_CD", "MAJ_CAT": "MAJ_CAT"}
MPLAN_DIMS = set(MPLAN_OWN_DIMS) | set(base.STORE_ATTRS) | {base.PLANT_COL}
# The purchase half has no store at all, so its resolvable set is its own three columns. RDC_NM is
# included because the snapshot carries the same two names the store master does (DH24 = FRKH,
# DW01 = KOL); the master's NULL-named stores simply find no purchase row, which is the truth.
MPLAN_RDC_OWN_DIMS = {"RDC_CD": "RDC_CD", "RDC_NM": "RDC_NM", "MAJ_CAT": "MAJ_CAT"}
MPLAN_RDC_DIMS = set(MPLAN_RDC_OWN_DIMS)
MONTH_NOTE = ("Month Plan is read at store x category from the Plan Live 360 snapshot. Sale, "
              "Space and Transfer In columns are returned blank when the report groups or filters "
              "below that - pick ST_CD / MAJ_CAT or any store attribute. Purchase has no store at "
              "all: it is planned at RDC x category and is blank unless the report groups only by "
              "RDC_CD / RDC_NM / MAJ_CAT.")

# ── WHICH MONTH. Relative to the report's anchor, or named outright ───────────────────────────
#
# RELATIVE tokens are the ones that survive being saved. A MIS report is published as a SILVER view
# and pulled by the scheduled dumps every month; a report pinned to 202609 would still be quoting
# September next March, and nobody would notice until someone reconciled it. CM is the calendar
# month of the report's anchor - the To date on screen, each grouped day when the report groups by
# DATE, and the previous month-end in a published view (base.MONTH_END_ANCHOR_SQL), which is the
# month-end close the business signs off.
#
# ABSOLUTE tokens - a plain YYYYMM - are for the report that genuinely is about one month: "the
# Oct-26 transfer-in plan". They freeze, and that is what they are for.
MONTH_OFFSETS = {"PM": -1, "CM": 0, "NM": 1, "M2": 2, "M3": 3}
MONTH_TOKENS = list(MONTH_OFFSETS)
_MONTH_ABS_RE = re.compile(r"^(?:19|20)\d{2}(?:0[1-9]|1[0-2])$")


def _month_id_expr(anchor, token, view_mode):
    """The MONTH_ID (YYYYMM) this token means for this anchor, as a literal or as SQL.

    An absolute token is already the answer. A relative one is a month offset from the anchor, and
    in view mode that has to stay an EXPRESSION - a published view takes no parameters, so the
    month has to re-resolve itself on every read exactly as the windows do.
    """
    if token not in MONTH_OFFSETS:
        return int(token)
    k = MONTH_OFFSETS[token]
    if view_mode:
        a = f"ADD_MONTHS({anchor}, {k})"
        return base.SqlExpr(f"(YEAR({a}) * 100 + MONTH({a}))")
    d = datetime.fromisoformat(str(anchor)).date()
    m = d.year * 12 + (d.month - 1) + k
    return (m // 12) * 100 + (m % 12) + 1


def _mplan_dim_expr(c, own):
    """Where a dimension comes from on a Month Plan row - the snapshot itself, or the store
    masters joined on its ST_CD. `own` is whichever half is being built."""
    if c in own:
        return f"f.{own[c]}"
    if c == base.PLANT_COL:
        return base.PLANT_FILT_EXPR
    return f"sm.{c}"


# ── WHICH MONTHS THE SNAPSHOT ACTUALLY HOLDS ──────────────────────────────────────────────────
#
# The picker lists the five relative tokens unconditionally - they are arithmetic on the anchor and
# need no data - and then the months the snapshot really has, so nobody ticks a month that could
# only ever come back blank. Measured 14-Sep-2026 that is 202607..202707, thirteen months.
#
# CACHED, because the catalogue is fetched on every page load of the builder and this is the only
# query in it. Two small reads (a 13-row GROUP BY and a one-row state table) against a snapshot
# that is rebuilt by hand every few days do not need to be re-asked every few seconds.
_MONTH_CACHE = {"at": 0.0, "months": [], "snapshot": None}
_MONTH_CACHE_TTL = 600


def _month_catalogue():
    """[{id, label}] for the months in the snapshot, plus when the snapshot was taken."""
    now = time.time()
    if _MONTH_CACHE["months"] and now - _MONTH_CACHE["at"] < _MONTH_CACHE_TTL:
        return _MONTH_CACHE["months"], _MONTH_CACHE["snapshot"]
    months, snap = [], None
    try:
        conn = base._conn()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT DISTINCT MONTH_ID FROM {MPLAN_TBL} ORDER BY 1")
            for (mid,) in cur.fetchall():
                mid = str(int(mid))
                months.append({"id": mid,
                               "label": f"{_MON_ABBR[int(mid[4:]) - 1]}-{mid[:4]}"})
            cur.execute(f"SELECT REFRESHED_AT, STATUS FROM {MPLAN_STATE_TBL} LIMIT 1")
            row = cur.fetchone()
            if row:
                snap = {"refreshedAt": base._fmt_str(row[0]), "status": row[1]}
        finally:
            conn.close()
    except Exception as e:                      # noqa: BLE001 - the picker degrades, the page does not
        # A catalogue that 500s would take the whole builder down with it, and the relative tokens
        # are still usable without this list. So the named months simply do not appear.
        logger.warning("month catalogue unavailable: %s", e)
        return [], None
    _MONTH_CACHE.update({"at": now, "months": months, "snapshot": snap})
    return months, snap


_MON_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ── GRC: goods received, a DAY-WISE FLOW and therefore a window block ──────────────────────────
#
# VW_GRC_REPORT_AKA is a movement ledger: one row per goods-receipt line, stamped with the day it
# posted (POSTING_DATE_DT, a real DATE - the only date column in either of these two views that is
# not DD.MM.YYYY text). Measured 22-Sep-2026: 2,498,610 rows, 2021-12-25 to today, 506 plants.
#
# That is the same shape the sale fact has, so GRC goes on the SAME AXIS as sale - one column is
# one measure over one window - and G_MTD__GRC_V is the receipt for precisely the days
# S_MTD__SALE_V sold in. It needs no picker of its own beyond the measures.
#
# THE SIGNS ARE ALREADY IN THE DATA. Five movement types are present - 101 receipt, 102 its
# reversal, 122 return to vendor, 123 that return's reversal, 161 returns PO - and the reversing
# ones carry NEGATIVE GRC_QTY and GRC_VALUE. So a plain SUM is the NET receipt and needs no
# movement list at all.
#
# GROSS AND RETURNS ARE SPLIT BY THE ROW'S OWN SIGN, not by a hard-coded set of movement types.
# GREATEST(x,0) and LEAST(x,0) cannot go stale when SAP starts stamping a sixth movement, they
# need no taxonomy anybody has to maintain here, and they are exact by construction: gross minus
# returns IS net, for every measure, at every grain. Returns are NEGATED so the column reads as
# "how much came back" rather than as a minus sign the eye has to carry across the sheet.
GRC_TBL = base.GRC_TBL
# THE SIX SHARED MEASURES PLUS TWO THIS PAGE CAN AFFORD. base.GRC_MEASURES is the set the variant
# grid offers, where the row is one variant at one store on one day and a line or day count says
# nothing. A REPORT groups - by category, by division, by store - so "how many receipt lines" and
# "how many receipt days" are real questions here, and they are added rather than duplicated.
GRC_MEASURES = list(base.GRC_MEASURES) + ["GRC_LINES", "GRC_DAYS"]
# How each one is built from a day's rows. The six shared ones go through base.grc_sum, so the
# rule about which way up a return points has exactly one definition (base.GRC_SRC) - the variant
# grid wraps the same summand in a window CASE instead, and the two cannot disagree about the sign.
# GRC_DAYS is absent because it is not a sum of anything: it counts DAYS, and the window layer
# builds it exactly as it builds the sale block's SAL_DAYS.
GRC_DAY_AGG = {m: base.grc_sum(m, col=lambda c: f"f.{c}") for m in base.GRC_MEASURES}
GRC_DAY_AGG["GRC_LINES"] = "COUNT(*)"
# GRC_DAYS counts a day in if ANY receipt line posted on it, whichever way it went. "Days with a
# movement", not "days with a net positive receipt" - a day whose only rows were returns is still
# a day the goods desk worked, and a day whose receipts and returns happen to cancel is not a day
# nothing happened. GRC_LINES is the per-day carrier, so it is pulled in whenever DAYS is picked.
GRC_DAYS_SRC = "GRC_LINES"
GRC_NOTE = ("GRC is the goods-receipt ledger, summed over the same windows as sale. Reversals and "
            "returns carry their own negative sign in the source, so GRC Qty / Val are NET; Gross "
            "and Returns split the same rows by sign and always add back to it. It resolves every "
            "dimension the sale fact does - its own store and article are joined to the same two "
            "masters, so a GRC column groups by division, cluster or colour like any other.")

# ── PO: the OPEN ORDER BOOK, a position and nothing else ───────────────────────────────────────
#
# VW_PO_PENDING_NEW IS PENDING-ONLY, and every column below is shaped by that one fact. Measured
# 22-Sep-2026: all 122,416 rows carry PENDING_PO_QUANTITY > 0. A PO line that has been received in
# full is not in the view - it has LEFT it. So the view is a POSITION AS IT STANDS NOW, in the same
# sense a stock tag is, and it has no history in it to put on any other axis.
#
# WHICH IS WHY THERE IS NO WINDOW HERE. The obvious-looking move is to window ACTUAL_PO_QUANTITY on
# DOC_DT and call the column "PO raised MTD". It would be wrong, and wrong in the direction nobody
# checks: the lines received during that very month have already dropped out of the view, so the
# column would shrink day by day while claiming to report a month that had finished. There is no
# way to read a flow out of a table that only keeps what has not happened yet. DOC_DT is offered
# as a FILTER on the block instead, where "raised since 1-Sep and still open" is exactly true.
#
# ONE COLUMN PER MEASURE, no axis token in the middle - but the wide name still spells
# O_PO__<measure>, with PO standing in for the period. That is not decoration: the vertical and
# period turns both build a cell as prefix + period + '__' + measure, uniformly across every
# family, and a block that spelled its columns differently would need a special case in each of
# them. See _period_slots.
PO_TBL = base.PO_TBL
# The one period this block has, and the label its row carries in the period layout. It cannot
# collide with a window code, a stock tag or a month token - none of them is spelled PO.
PO_PERIOD = "PO"
# The variant grid's ten, plus the distinct PO count - which only means something above the
# article, so that grid leaves it out. Same reasoning as GRC's two extras above.
PO_MEASURES = list(base.PO_MEASURES) + ["PO_COUNT"]
# WHAT EACH ONE IS, and every one of them is "over the lines that are still open" - that is the
# view's population and no measure here can escape it.
#
#   PEND   what is still to come                 PENDING_PO_QUANTITY / _VALUE
#   ORD    what those same lines were ordered at  ACTUAL_PO_QUANTITY / _VALUE. NOT "what was
#          ordered this year": a fully-received line is gone, so this is the ordered size of the
#          open book, and PEND / ORD is how much of it is still outstanding.
#   GR     what has already landed against them   GR_QTY. ORD - GR = PEND, by construction.
#   OVD    the part whose ex-factory date has passed. The overdue test is the block's one use of
#          a date, and EX_FAC_DT is DD.MM.YYYY TEXT - TRY_TO_DATE with the explicit format, never
#          the AUTO parse, which reads 01.02.2026 as a January date.
#   REL /  pending split by PO_STATS. Measured 22-Sep-2026 the book is 3,782 RELEASE lines against
#   UNREL  118,634 UNRELEASE, so the split is most of what anyone asks this table.
#   LINES  PO line count            COUNT(*)
#   COUNT  distinct purchase orders COUNT(DISTINCT PO_NUMBER). Safe here and nowhere else in this
#          file: this block is ONE aggregate straight off the view, with no daily rollup underneath
#          it for a distinct count to be taken twice over.
# base.po_sum, qualified onto this file's fact-shaped row - which aliases the view `f` so that
# _dim_expr, _joins and _filter_sql apply to it verbatim - plus the one measure that is this
# page's own.
PO_AGG = {m: base.po_sum(m, lambda c: f"f.{c}") for m in base.PO_SRC}
PO_AGG["PO_COUNT"] = "COUNT(DISTINCT f.PO_NUMBER)"
# The four that need a PREDICATE as well as a column: (source column, which test). The overdue
# test is completed at build time because it compares against the run's anchor, and in view mode
# that anchor is an EXPRESSION which has to re-resolve on every read of the published view.
#
# DD.MM.YYYY IS PASSED EXPLICITLY. Snowflake's AUTO date parse reads 01.02.2026 as 1-Feb by one
# convention and 2-Jan by another depending on what it has already seen in the column, and this
# column is text in exactly that ambiguous shape - which is the same trap the SRM TNA report walked
# into. TRY_TO_DATE with the format named leaves nothing to infer.
PO_DATE_FMT = base.PO_DATE_FMT
PO_COND_AGG = {m: (f"f.{src}", which) for m, (src, which) in base.PO_COND_AGG.items()}
PO_NOTE = ("PO is the OPEN order book as it stands now - that view holds only lines still pending, "
           "so a received line has left it. Every PO column is therefore a position, like a stock "
           "tag, and there is no window reading of it: 'PO raised MTD' cannot be read from a table "
           "that keeps only what has not arrived. It is also blank when the report groups by DATE, "
           "for the same reason - one live position stamped onto thirty days is the same number "
           "told thirty times. Filter DOC_DT for 'raised since X and still open'.")
# PO IS ALMOST ENTIRELY A DC BOOK. Measured 22-Sep-2026: DH24 61,794 lines and DW01 56,839 of
# 122,416, so 97% of it sits at the two big DCs and the stores carry a trickle. Grouping a PO
# report by store is legitimate and is not blocked - it simply returns what the book actually says,
# which is two large rows and a long tail. Said here so nobody reads the tail as a data fault.

# BOTH VIEWS RESOLVE EVERY DIMENSION IN THE CATALOGUE, and that is measured, not assumed.
#
# Each carries a plant code and an article number, which is all the two masters need - so rather
# than a private dimension list with its own blanking rules (the Sale Plan and Month Plan blocks
# each need one, because the plan genuinely stops at the category), both blocks SHAPE THEIR ROWS
# TO LOOK LIKE THE SALE FACT and then hand them to _dim_expr, _joins and _filter_sql verbatim. One
# definition of what MAJ_CAT means, one of how CLUSTER is reached, no second copy to drift.
#
# Coverage against SILVER.VW_PRODUCT_MASTER - the master `pv` joins, and the one that decides
# whether a grouping resolves - measured 22-Sep-2026:
#   GRC, FY-to-date   916,773 rows, 0 unmatched, 0.000% of value unresolved
#   PO, whole book    122,563 rows, 0 unmatched, 0.000% of value unresolved
# Against the MIS store master, both are 0 rows with no plant. So neither block needs the blanking
# machinery, and a PO or GRC column grouped by SUB_DIV or NEW_COLOR_DESC is complete.
#
# NOT the BRONZE.DIM_PRODUCT bridge the Day Ref block uses. That one is filtered to rows with a
# non-blank MAJ_CAT and a GEN_ART_NUMBER, and against IT the same two views lose 4.6% and 36.8% of
# their value respectively - the PO book is full of articles bought for a season the narrower
# bridge has not got to yet. Day Ref must use it, because it has to tie to a proc that uses it;
# these two must not, and the difference is the whole of that 36.8%.
#
# The four variant columns the sale fact carries physically - GEN_ART_NUMBER, COLOR, SIZE_1 and the
# article description - are read off the master here, since neither view has them. They are joined
# ONLY when the report actually names one; a GRC report grouped by store and category never pays
# for it.
GP_ART_DIMS = {"GEN_ART_NUMBER", "COLOR", "SIZE_1", "ARTICLE_DESC", "GEN_ART_DESC"}
GP_ART_CTE = f"""gp_art AS (
    SELECT TRY_TO_NUMBER(ARTICLE_NUMBER) AN, TRY_TO_NUMBER(GEN_ARTICLE_NUMBER) GEN_ART_NUMBER,
           ARTICLE_DESCRIPTION AS GEN_ART_DESC,
           NVL(NULLIF(TRIM(COLOR),''),'NA') COLOR, NVL(NULLIF(TRIM(SIZE1),''),'NA') SIZE_1
      FROM {base.PROD_TBL}
    QUALIFY ROW_NUMBER() OVER (PARTITION BY TRY_TO_NUMBER(ARTICLE_NUMBER)
                               ORDER BY ARTICLE_CREATION_DATE DESC NULLS LAST) = 1)"""


def _gp_fact_cols(art):
    """The five columns a GRC / PO row needs in order to be read as a sale-fact row.

    `art` says whether the gp_art bridge was joined. When it was not, the four variant columns are
    NULL rather than absent: _dim_expr names f.COLOR whether or not the report groups by it (a
    filter or a condition can name it too), so the column has to EXIST on every shape of the row.
    Nothing reads a NULL one, because the only thing that would have is the grouping that caused
    the bridge to be joined in the first place.
    """
    if art:
        return ("a.GEN_ART_NUMBER, a.GEN_ART_DESC, a.COLOR, a.SIZE_1")
    return ("CAST(NULL AS NUMBER) GEN_ART_NUMBER, CAST(NULL AS VARCHAR) GEN_ART_DESC, "
            "CAST(NULL AS VARCHAR) COLOR, CAST(NULL AS VARCHAR) SIZE_1")


def _gp_art_join(art, key):
    """The bridge join, or nothing. `key` is the block's article expression."""
    return f" LEFT JOIN gp_art a ON a.AN = {key}" if art else ""


# ══════════════════════════════════════════════════════════════════════════════
# definition
# ══════════════════════════════════════════════════════════════════════════════
# ── COLUMN RENAMES: a saved definition still speaking the old master's vocabulary ─────────────
#
# db-scripts/377 renamed two columns on the MIS store master - PLANT_TYPE -> ST_TYP and
# STATUS -> ST_STAT - and a saved report is a STRING, so every definition written before that day
# still names the old ones.
#
# THIS FAILED SILENTLY, AND THAT IS THE WHOLE REASON IT IS HERE. _clean_def drops any column not
# in DIM_COLS rather than escaping it, which is right for an injection surface and wrong for a
# rename: the two conditions on RDC-MJ - STATUS <> 'IN-ACT' and PLANT_TYPE = 'STORE' - were
# discarded on every run, so a report meant to cover 537 trading STORES silently covered all 795
# plants. The DCs alone put Rs 165 Cr of DH24 warehouse stock into a store report, and nothing
# anywhere said a filter had been thrown away. Measured 16-Sep-2026: 2 of 11 saved reports affected
# (rdc-mj, rdc-var-article-rpt), both carrying exactly this pair.
#
# Applied on READ as well as on save, so the editor shows a migrated condition instead of a blank
# row it cannot match to its catalogue - which is what leads someone to re-pick it by hand and get
# the operator wrong. The stored JSON is left alone until the next ordinary save rewrites it.
#
# ADD A LINE HERE WHENEVER A DIM COLUMN IS RENAMED. It is append-only and costs nothing: a name
# that no longer appears in any definition simply never matches.
RENAMED_DIM_COLS = {
    "PLANT_TYPE": "ST_TYP",       # db-scripts/377
    "STATUS":     "ST_STAT",      # db-scripts/377
}


def _migrate_col(c):
    """One column name, moved to its current spelling. Unknown names pass through untouched -
    this maps renames, it does not validate; DIM_COLS still decides what survives."""
    c = str(c or "").strip().upper()
    return RENAMED_DIM_COLS.get(c, c)


def _migrate_def(d):
    """A saved definition with every dim column moved to its current spelling.

    Touches the four places a column name can be stored - group_by, filters, col_filters and
    dim_filters - because a rename lands in all of them and half a migration is worse than none.
    Returns a NEW dict; the caller's copy (and the row in MIS_REPORT_DEF) is not modified."""
    if not isinstance(d, dict):
        return d
    out = dict(d)
    if isinstance(d.get("group_by"), list):
        out["group_by"] = [_migrate_col(c) for c in d["group_by"]]
    if isinstance(d.get("filters"), dict):
        out["filters"] = {_migrate_col(k): v for k, v in d["filters"].items()}
    for key in ("col_filters", "dim_filters"):
        if isinstance(d.get(key), list):
            out[key] = [dict(f, col=_migrate_col(f.get("col")))
                        if isinstance(f, dict) else f for f in d[key]]
    return out


def _clean_def(d):
    """Validate a definition and return it normalised.

    Every list is filtered against the catalogue rather than escaped, because these values are
    interpolated into SQL. A column name that is not in DIM_COLS never reaches the query - it is
    dropped here, so there is no injection surface to get wrong later.
    """
    # BEFORE anything is matched against the catalogue: a column that was renamed out from under a
    # saved report is migrated, not dropped. See RENAMED_DIM_COLS.
    d = _migrate_def(d or {})
    seen, group_by = set(), []
    for c in (d.get("group_by") or []):
        c = str(c).strip().upper()
        if c in DIM_COLS and c not in seen:
            seen.add(c)
            group_by.append(c)

    want_w = {str(x).strip().upper() for x in (d.get("windows") or [])}
    want_m = {str(x).strip().upper() for x in (d.get("measures") or [])}
    want_t = {str(x).strip().upper() for x in (d.get("stock_tags") or [])}
    # Order follows the catalogue, never the request, so two reports with the same columns come
    # out in the same order and an export stays comparable.
    # NO SILENT DEFAULTS. These used to fall back to ["TD"] and ["SALE_V","SALE_Q","GM_V"] when the
    # caller sent an empty list, which meant deselecting every window and saving handed the windows
    # straight back - the editor reopened showing TD and three measures nobody had chosen.
    #
    # An empty selection is a legitimate report: drop every window and measure and you get a pure
    # STOCK report, dimensions plus stock tags, which is exactly what a report called
    # ARS_STOCK_REPORT wants. build_sql emits the stock-only shape for it.
    #
    # What is NOT legitimate is a report with no measures of any kind, so that is rejected below
    # rather than quietly filled in.
    want_r = {str(x).strip().upper() for x in (d.get("ref_measures") or [])}
    wins = [w for w in WINDOWS if w in want_w]
    meas = [m for m in SALE_MEASURES if m in want_m]
    tags = [t for t in ALL_STOCK_TAGS if t in want_t]
    ref = [m for m in REF_MEASURES if m in want_r]
    want_p = {str(x).strip().upper() for x in (d.get("plan_measures") or [])}
    plan = [m for m in PLAN_MEASURES if m in want_p]
    # THE MONTH AXIS. Two lists, and a column is one of each - the same shape windows and measures
    # have, which is why they are validated the same way below.
    #
    # The token order is the CATALOGUE'S, never the request's: the five relative tokens in calendar
    # order, then the named months ascending. Two reports picking the same months therefore lay
    # their columns out identically, and a saved report's export stays comparable with itself after
    # someone re-ticks a month.
    want_mm = {str(x).strip().upper() for x in (d.get("month_measures") or [])}
    mmeas = [m for m in MONTH_MEASURES if m in want_mm]
    want_mo = {str(x).strip().upper() for x in (d.get("plan_months") or [])}
    months = ([t for t in MONTH_TOKENS if t in want_mo]
              + sorted(t for t in want_mo if _MONTH_ABS_RE.match(t)))
    # GRC rides the WINDOW axis, so it needs no list of its own beyond the measures. PO rides no
    # axis at all - one column per measure - so it needs no partner list either, and is the first
    # block in this builder that can stand entirely on its own.
    want_g = {str(x).strip().upper() for x in (d.get("grc_measures") or [])}
    grc = [m for m in GRC_MEASURES if m in want_g]
    want_o = {str(x).strip().upper() for x in (d.get("po_measures") or [])}
    po = [m for m in PO_MEASURES if m in want_o]

    # Ref measures need a window for the same reason sale measures do: a column is one measure of
    # one window. Unlike sale they need no partner measure list, so this is a one-way check.
    if ref and not wins:
        raise HTTPException(400, "Day Ref measures need at least one window - a Day Ref column is "
                                 "one measure summed over one window.")
    # Same rule for the plan, and for the same reason: a plan column is one measure summed over
    # one window's days.
    if plan and not wins:
        raise HTTPException(400, "Sale Plan measures need at least one window - a Sale Plan "
                                 "column is one measure summed over one window.")
    # Same rule again for GRC, and for the same reason: the receipt ledger is a day-wise flow, so
    # a GRC column is one measure summed over one window's days. PO deliberately has no such
    # check - it is a position and needs no axis.
    if grc and not wins:
        raise HTTPException(400, "GRC measures need at least one window - a GRC column is one "
                                 "measure summed over one window's days.")
    # The Month Plan's two lists go together the way windows and measures do - one without the
    # other yields no columns at all, so say which half is missing rather than saving a report that
    # silently has nothing in it.
    if mmeas and not months:
        raise HTTPException(400, "Month Plan measures need at least one month - a Month Plan "
                                 "column is one measure of one month. Pick a month, or clear the "
                                 "measures.")
    if months and not mmeas:
        raise HTTPException(400, "A month on its own produces no column. Pick at least one Month "
                                 "Plan measure, or clear the months.")

    # Sale columns are the CROSS PRODUCT of windows and measures, so one without the other yields
    # nothing - say so plainly instead of returning a report with only dimension columns.
    # Windows now serve TWO measure lists, so "windows but no measures" is only an error when the
    # Day Ref list is empty too - windows plus Day Ref measures alone is a perfectly good report.
    if (wins and not meas and not ref and not plan and not grc) or (meas and not wins):
        raise HTTPException(400, "Windows and Measures go together - a sale column is one measure "
                                 "of one window. Pick at least one of each, or clear both for a "
                                 "stock-only report.")
    if not wins and not meas and not tags and not ref and not plan and not mmeas and not po:
        raise HTTPException(400, "This report would have no measures at all. Pick some windows and "
                                 "measures, some stock tags, some Day Ref measures, some Sale "
                                 "Plan measures, some GRC measures, a Month Plan month and "
                                 "measure, or some PO measures.")

    # SAVED CONDITIONS, the report's own WHERE and HAVING.
    #
    # Two shapes, because they act at two different points in the query and always have:
    #   filters      {COL: [values]}  - dimension "is any of", applied INSIDE the scan (WHERE)
    #   col_filters  [{col,op,v1,v2}] - applied to the FINISHED columns, after the aggregation,
    #                                   which is what a HAVING clause is
    #
    # Only the shape is validated here. The COLUMN is deliberately not: col_filters name output
    # columns (S_TD__SALE_V, K_0001__STOCK_Q, ST_CD ...) whose existence depends on the windows,
    # measures and tags the same definition chooses, and those can change after a condition is
    # saved. _col_where re-validates against the run's ACTUAL columns and drops anything that no
    # longer exists - so a condition on a measure somebody later removed goes quiet instead of
    # failing the report.
    col_filters = []
    for f in (d.get("col_filters") or []):
        if not isinstance(f, dict):
            continue
        c = str(f.get("col") or "").strip()
        op = str(f.get("op") or "").strip().lower()
        if not c or op not in _SAVED_OPS:
            continue
        row = {"col": c, "op": op, "v1": f.get("v1"), "v2": f.get("v2")}
        # A ticked value list is carried through rather than flattened into v1: `in` accepts both
        # shapes, and dropping it here would turn a saved "is any of" into an empty condition.
        if isinstance(f.get("vals"), list):
            row["vals"] = [str(x) for x in f["vals"]][:MAX_IN_VALUES]
        col_filters.append(row)
        if len(col_filters) >= 50:      # a report with 50 conditions is a mistake, not a filter
            break

    filters = {}
    for k, v in (d.get("filters") or {}).items():
        k = str(k).strip().upper()
        if k in DIM_COLS and isinstance(v, list):
            vals = [str(x) for x in v if str(x).strip() != ""]
            if vals:
                filters[k] = vals[:500]

    # THE THIRD SHAPE: a condition on a dimension the report does NOT print.
    #
    # col_filters can only name an output column, so before this every condition had to sit on a
    # grouped column - the editor could offer nothing else without writing filters that would be
    # silently dropped at run time. These are applied inside the scan instead (_dim_where), so any
    # dimension in the catalogue can be filtered on whether or not the report groups by it.
    #
    # The COLUMN is validated here, unlike col_filters: a dimension is a fixed vocabulary that
    # cannot stop existing when the windows change, and this string is interpolated into the inner
    # scan. Anything outside DIM_COLS is dropped rather than escaped, the same rule as `filters`.
    dim_filters = []
    for f in (d.get("dim_filters") or []):
        if not isinstance(f, dict):
            continue
        c = str(f.get("col") or "").strip().upper()
        op = str(f.get("op") or "").strip().lower()
        if c not in DIM_COLS or op not in _SAVED_OPS:
            continue
        row = {"col": c, "op": op, "v1": f.get("v1"), "v2": f.get("v2")}
        if isinstance(f.get("vals"), list):
            row["vals"] = [str(x) for x in f["vals"]][:MAX_IN_VALUES]
        dim_filters.append(row)
        if len(dim_filters) >= 50:
            break

    # Anything not named in LAYOUTS is horizontal, so a definition saved before either turn
    # existed - and every report already in MIS_REPORT_DEF - reads back exactly as it always did.
    layout = _layout_of(d)

    # STORES ONLY, over the ET_STOCK_DATA family. See base.STORE_ONLY_PRED for what it restricts
    # and why it stops at that family.
    #
    # A REPORT PROPERTY, NOT A CONDITION, because it is not one: a condition removes ROWS, and this
    # report wants every plant's rows - its RDC totals and its sale windows are estate-wide - while
    # its shop-floor stock counts stores. Making it a dim_filter would have taken the DCs out of
    # the sale figures too, which is the thing that made the first three attempts at this wrong.
    #
    # Defaults FALSE, so every report saved before this reads back exactly as it did.
    store_only_stock = bool(d.get("store_only_stock"))
    # THE V2R TWIN OF IT, and a report property for the same reason: V2R is a warehouse NUMBER
    # spanning 4 DCs and 23 hubs, so V2R_* read plain is three times the DC stock somebody asking
    # for "the DCs" meant. It narrows the COLUMN, never the rows - the hubs keep their rows and
    # their sale. 'DC' and trading, from the master, which today is exactly DH24 / DW01 / DK04;
    # see DC_ONLY_PRED in sf_var_combined. Defaults FALSE, so every saved report reads back as it did.
    dc_only_stock = bool(d.get("dc_only_stock"))
    # THE PLANTS THE READER PICKED, which is the same narrowing said explicitly. A list WINS over
    # the rule above (see tag_predicate): the rule is a convenience the picker fills itself from,
    # and a reader who names DP01 means DP01. Codes only - they are keys, not text.
    v2r_plants = [str(x).strip().upper() for x in (d.get("v2r_plants") or [])
                  if str(x).strip()]
    # THE SAME PICK, PER TAG: {TAG: [ST_CD, ...]} - which plants count for THAT tag's columns.
    # v2r_plants above is the V2R family's version of this and stays, because every report saved
    # with it reads back unchanged; this is the general form, and the two AND together on a V2R
    # tag that carries both.
    #
    # The TAG is validated against the catalogue for the same reason a dimension is: it is
    # interpolated into the predicate. The CODES are not - they go through _q, and a plant that
    # does not exist simply matches nothing, which is the honest answer to picking it.
    tag_plants = {}
    for k, v in (d.get("tag_plants") or {}).items():
        k = str(k).strip().upper()
        if k not in ALL_STOCK_TAGS or not isinstance(v, list):
            continue
        codes = [str(x).strip().upper() for x in v if str(x).strip()]
        if codes:
            tag_plants[k] = codes[:2000]
        if len(tag_plants) >= 100:
            break
    # THE SAME PICK SAID ONCE FOR A WHOLE FAMILY - ET, SDC, V2R, V2S, LQ, LQL, LQB, PRD, HUBPRD
    # (base.plant_family). Somebody reading five V2S storage types means "the V2S stock at these
    # plants", not five identical lists, and a tag's own pick wins over its family's - see
    # tag_predicate. The FAMILY name is validated the same way a tag is: it is interpolated into
    # the predicate through plant_family, so anything the catalogue does not know is dropped.
    known_families = {f for f in (base.plant_family(t) for t in ALL_STOCK_TAGS) if f}
    family_plants = {}
    for k, v in (d.get("family_plants") or {}).items():
        k = str(k).strip().upper()
        if k not in known_families or not isinstance(v, list):
            continue
        codes = [str(x).strip().upper() for x in v if str(x).strip()]
        if codes:
            family_plants[k] = codes[:2000]
        if len(family_plants) >= 50:
            break

    return {"group_by": group_by, "windows": wins, "measures": meas,
            "stock_tags": tags, "ref_measures": ref, "plan_measures": plan,
            "plan_months": months, "month_measures": mmeas,
            "grc_measures": grc, "po_measures": po,
            "filters": filters, "col_filters": col_filters, "dim_filters": dim_filters,
            "layout": layout, "store_only_stock": store_only_stock,
            "dc_only_stock": dc_only_stock, "v2r_plants": v2r_plants,
            "tag_plants": tag_plants, "family_plants": family_plants}


def _cols_for(defn):
    """The report's columns, in display order."""
    cols = list(defn["group_by"])
    for w in defn["windows"]:
        for m in defn["measures"]:
            cols.append(f"S_{w}__{m}")
    for t in defn["stock_tags"]:
        for m in STOCK_MEASURES:
            cols.append(f"K_{t}__{m}")
    # Day Ref last, so a report that gains them keeps every existing column in its existing place.
    for w in defn["windows"]:
        for m in defn.get("ref_measures") or []:
            cols.append(f"R_{w}__{m}")
    # Sale Plan after Day Ref, same reasoning - a report that gains it keeps every existing column
    # where it already was.
    for w in defn["windows"]:
        for m in defn.get("plan_measures") or []:
            cols.append(f"P_{w}__{m}")
    # Month Plan last, same reasoning again. MONTH first and measure second, so one month's four
    # plans read together as a block rather than one measure's months chasing each other across
    # the sheet - which is how the Plan Live 360 grid is laid out and how it is read.
    for t in defn.get("plan_months") or []:
        for m in defn.get("month_measures") or []:
            cols.append(f"M_{t}__{m}")
    # GRC then PO at the very end, the same append-only rule every block before them followed: a
    # report that gains either keeps every column it already had exactly where it was, so an export
    # taken last month still lines up with one taken today.
    for w in defn["windows"]:
        for m in defn.get("grc_measures") or []:
            cols.append(f"G_{w}__{m}")
    # PO has no axis, so PO_PERIOD stands in for one - see the PO block for why the name is still
    # spelled with a period in the middle.
    for m in defn.get("po_measures") or []:
        cols.append(f"O_{PO_PERIOD}__{m}")
    return cols


# ══════════════════════════════════════════════════════════════════════════════
# SQL
# ══════════════════════════════════════════════════════════════════════════════
def _dim_expr(c):
    """Where a dimension comes from: the fact row, the store master, or the product master."""
    if c == "GEN_ART_DESC":
        return "gp.GEN_ART_DESC"
    if c == "ARTICLE_DESC":
        return "f.GEN_ART_DESC"        # the fact column still carries the old physical name
    # MAJ_CAT and SEG are ON the fact but OWNED by the product master - see PROD_KEY_MAP in
    # sf_var_combined. The fact's copy is a snapshot of its load day, so a report grouped on
    # MAJ_CAT + SUB_DIV read the frozen category beside the live subdivision and split one category
    # across two subdivisions the moment SAP re-classified an article. Same expression as the base
    # report, so the two agree; COALESCEd to the stamp for the handful of articles with no master
    # row. This is why _joins now pulls in the product view for these two.
    if c in base.PROD_KEY_MAP:
        return f"COALESCE(pv.{base.PROD_KEY_MAP[c]}, f.{c})"
    if c in FACT_DIMS:
        return f"f.{c}"
    # Off `pt`, the FULL master - not sm. A report grouped on plant type against the planning view
    # could only ever return STORE and NA, so the DC and HUB rows would simply not exist.
    if c == base.PLANT_COL:
        return base.PLANT_FILT_EXPR
    if c in base.STORE_ATTRS:
        return f"sm.{c}"
    return f"pv.{PROD_DIM_MAP[c]}"


def _joins(defn):
    """Only the masters this report's dimensions and filters actually need.

    A report grouped on ST_CD and DT alone then never touches the 3.6M-row product view, which is
    most of the difference between a fast report and a slow one.

    MAJ_CAT and SEG now COUNT as product-view columns even though they are physically on the fact:
    they are read from the master so the hierarchy cannot self-contradict (_dim_expr).

    THE PRODUCT SUBQUERY NAMES ITS COLUMNS - it used to be SELECT *, which dragged all 116 columns
    of a view that runs a RECURSIVE CTE over LFA1/BUT000 through a 3.7M-row window sort to reach the
    two or three anyone asked for. Measured on store-wise-var-article, 3 Sep, page 1: 277.6s with
    the star against 197.3s naming the columns - and 224.0s for the OLD build that did not join the
    product view at all. So naming them does not merely pay back the join MAJ_CAT now needs, it
    lands under where the report started.
    """
    # dim_filters count as USED even though they never reach the projection: a condition on
    # CLUSTER still needs the store master joined, or the clause names an alias nothing defined.
    # .get for the same reason stock_tags uses it - other pages call this with a synthetic
    # definition carrying only the two keys they are asking about.
    used = (set(defn["group_by"]) | set(defn["filters"])
            | {f["col"] for f in (defn.get("dim_filters") or [])})
    sql = ""
    if any(c in base.STORE_ATTRS for c in used):
        sql += f" LEFT JOIN {base.STORE_TBL} sm ON TRIM(sm.ST_CD) = TRIM(f.ST_CD)"
    # Also when a SPLIT TAG is picked: its CASE names pt, and a report grouped on nothing but
    # MAJ_CAT would otherwise reference an alias that was never joined. Both facts carry ST_CD, so
    # one join serves the sale block and the stock block - on a split-tag-only report the sale side
    # never reads it, which is a 777-row broadcast join against a 127M-row scan and not worth a
    # second, narrower _joins to avoid.
    #
    # .get, NOT ["stock_tags"]: this function is also called with a SYNTHETIC definition built by
    # another page - sf_mis_trend._joins narrows the dimensions before delegating, and its /values
    # probe passes {"group_by": [col], "filters": {}} - and those dicts carry only the two keys
    # this function is being asked about. A hard subscript here made every Sale Trend run 500 with
    # KeyError: 'stock_tags' the moment PLANT_TYPE was not among the grouped columns, which is
    # almost always (the `or` short-circuits when it IS, which is why it looked intermittent).
    # No tags means no rule tag, means no reason to join pt - so the empty default is also correct.
    # The switch puts `pt` into every ET tag's predicate, so it decides the join too - same .get
    # default as stock_tags, for the synthetic definitions described above.
    _so = bool(defn.get("store_only_stock"))
    _dc = bool(defn.get("dc_only_stock"))
    _vp = defn.get("v2r_plants") or []
    if base.PLANT_COL in used or any(base.tag_needs_plant(t, _so, _dc, _vp)
                                     for t in (defn.get("stock_tags") or [])):
        sql += f" LEFT JOIN {base.plant_lookup_sql()} pt ON pt.ST_CD = TRIM(f.ST_CD)"
    # Source names, because _dim_expr reads pv by the VIEW's column name, not the report's alias.
    # ARTICLE_CREATION_DATE is deliberately not forced in: QUALIFY orders on it before the
    # projection happens, so the window still sees it whether or not the report asked for it.
    prod_used = sorted({PROD_DIM_MAP[c] for c in used if c in PROD_DIM_MAP}
                       | {base.PROD_KEY_MAP[c] for c in used if c in base.PROD_KEY_MAP})
    if prod_used:
        sql += (f" LEFT JOIN (SELECT TRY_TO_NUMBER(ARTICLE_NUMBER) AN, {', '.join(prod_used)} "
                f"FROM {base.PROD_TBL} "
                f"QUALIFY ROW_NUMBER() OVER (PARTITION BY TRY_TO_NUMBER(ARTICLE_NUMBER) "
                f"ORDER BY ARTICLE_CREATION_DATE DESC NULLS LAST) = 1) pv "
                f"ON pv.AN = f.ARTICLE_NUMBER")
    if "GEN_ART_DESC" in used:
        # The GENERIC description lives on the parent article's OWN row; the view repeats the
        # variant text in its GEN_ARTICLE_DESC column, so that field returns the wrong one.
        sql += (f" LEFT JOIN (SELECT TRY_TO_NUMBER(ARTICLE_NUMBER) GEN_ART_NUMBER, "
                f"ARTICLE_DESCRIPTION AS GEN_ART_DESC FROM {base.PROD_TBL} "
                f"WHERE TRY_TO_NUMBER(ARTICLE_NUMBER) = TRY_TO_NUMBER(GEN_ARTICLE_NUMBER) "
                f"QUALIFY ROW_NUMBER() OVER (PARTITION BY TRY_TO_NUMBER(ARTICLE_NUMBER) "
                f"ORDER BY ARTICLE_CREATION_DATE DESC NULLS LAST) = 1) gp "
                f"ON gp.GEN_ART_NUMBER = f.GEN_ART_NUMBER")
    return sql


def _filter_sql(defn):
    """A clause per filtered dimension.

    An EMPTY value list is what _resolve leaves behind when a saved filter and a run filter on the
    same column do not overlap. The honest answer there is no rows - and it has to be spelled FALSE,
    because "IN ()" is a compilation error, which is how that combination used to fail.
    """
    out = []
    for k, vals in defn["filters"].items():
        if not vals:
            out.append("FALSE")
        elif k == "DT":
            # A date compares as a date: UPPER(TRIM(...)) around one would only turn it into text.
            out.append(f"{_dim_expr(k)} IN ({', '.join(base._q(v) for v in vals)})")
        else:
            # Matched case- and space-insensitively, the same as an Equals condition. Codes are
            # uppercase in the fact table and typed however the person typing them felt, and an
            # "is any of DB03" that returns nothing because it was typed "db03" reads exactly like
            # a store with no sale.
            lits = ", ".join(base._q(str(v).strip().upper()) for v in vals)
            out.append(f"UPPER(TRIM(TO_VARCHAR({_dim_expr(k)}))) IN ({lits})")
    # Everything the editor wrote on a dimension that is NOT a plain value list - Equals, Contains,
    # Between, Is none of, on a grouped column or on one the report never prints. Same scan, same
    # WHERE, so one filter mechanism covers both.
    out += _dim_where(defn.get("dim_filters"))
    return out


# Excel-style per-column conditions, chosen in the grid header rather than saved on the report.
#
# WHY THESE ARE NOT `filters`
# A dimension value list belongs in the INNER scan: it prunes the fact read and is also what an
# export split understands. A MEASURE does not exist until after the aggregation, so a condition on
# one can only be applied to the finished result. Text conditions ride the same outer WHERE because
# ILIKE is not a value list, and keeping one mechanism for both means one place to get the
# validation right.
#
# Nothing a caller sends is interpolated as text: the column must already be one THIS run produces,
# the operator comes from a fixed map, numbers go through float(), and patterns go through _q with
# the LIKE wildcards escaped.
# `eq` and `ne` are listed for _SAVED_OPS below, but _col_where handles them itself: equality is
# the one comparison that has to work on TEXT, and several `eq` on one column mean "any of".
_NUM_OPS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "=", "ne": "<>"}
# The LIKE escape character. '!' rather than the usual backslash: whether a backslash inside a
# Snowflake string literal is an escape of its own depends on the driver and the session, and this
# query is assembled as TEXT - so the safe choice is the one character that needs no quoting.
# Confirmed against Snowflake: '%A!_B%' ESCAPE '!' matches A_B and not AXB.
_LIKE_ESC_CH = "!"
_LIKE_ESC = f" ESCAPE '{_LIKE_ESC_CH}'"
_TXT_OPS = {"contains": ("ILIKE", "%{}%"), "not_contains": ("NOT ILIKE", "%{}%"),
            "starts": ("ILIKE", "{}%"), "ends": ("ILIKE", "%{}")}
# RAW LIKE - the pattern is the person's own, wildcards and all.
#
# The four ops above BUILD the pattern and escape whatever is inside it, which is right for
# "contains DM_01" but leaves no way to write a real one: HB% , DM_1 , %KURTA%ST. So `like` passes
# the typed pattern through untouched. It is still a quoted literal, never interpolated - the only
# difference is that % and _ keep their meaning instead of being escaped away.
#
# ILIKE rather than LIKE, for the same reason `contains` is: nobody types a store code in the case
# the warehouse happens to store it in. ESCAPE '!' still applies, so a LITERAL percent is '!%'.
_RAW_LIKE_OPS = {"like": "ILIKE", "not_like": "NOT ILIKE"}
# Value lists on the FINISHED columns. `filters` already does "is any of" for a group-by dimension,
# inside the scan - this is the same idea for the two cases it cannot serve: a MEASURE, which does
# not exist until after the aggregation, and "is NONE of", which an IN list cannot express.
_LIST_OPS = {"in", "not_in"}
# Everything _col_where can actually execute. A definition may only store one of these, so a
# saved report can never carry an operator the engine would silently ignore at run time.
_SAVED_OPS = (set(_NUM_OPS) | set(_TXT_OPS) | set(_RAW_LIKE_OPS) | _LIST_OPS
              | {"blank", "nonblank", "between"})
# The most values one list condition may carry, matching the cap on a `filters` entry.
MAX_IN_VALUES = 500


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _scalar(v):
    """A comparable SQL literal for a condition value: a number, or a DATE.

    Comparison and BETWEEN used to be float-only, so a date typed into a condition on DT parsed as
    None and the whole clause was dropped - a filter that silently did nothing, which is worse than
    one that refuses. A value that looks like YYYY-MM-DD becomes a real DATE literal instead.

    Returns None for anything that is neither, which still drops the clause - but now only for
    values that genuinely cannot be compared.
    """
    n = _num(v)
    if n is not None:
        return str(n)
    t = str(v or "").strip()
    if base._DATE_RE.match(t):
        return f"'{t}'::DATE"
    return None


def _like(v, shape):
    """A LIKE pattern for `v`, with the wildcards inside it made literal.

    Without this a store code containing an underscore would match any single character, so
    'contains DM_01' would quietly return rows that are not DM_01. Built character by character
    rather than by chained replaces, which have to be ordered right to avoid escaping an escape.
    """
    out = []
    for ch in str(v):
        if ch in (_LIKE_ESC_CH, "%", "_"):
            out.append(_LIKE_ESC_CH)
        out.append(ch)
    return shape.format("".join(out))


def _numsafe(e):
    """`e` as a NUMBER for an ordered comparison, with un-parseable text becoming NULL.

    A code column is TEXT even when every value in it reads as a number, and Snowflake answers
    `ART_TYPE BETWEEN 1110 AND 1518` by casting the WHOLE column to a number - so the six rows
    that still carry the SAP material type ('VERP', 'FERT') kill the query outright with
    "Numeric value 'VERP' is not recognized", and the report returns nothing at all. Those rows
    are not in the range the reader asked for; they should simply not match.

    TRY_TO_DECIMAL gives exactly that: the rows that parse compare as numbers, the rows that do
    not go NULL and drop out. 38,6 rather than a double because ARTICLE_NUMBER is longer than a
    double can hold exactly, and a rounded article number matches the wrong article. Wrapping a
    column that is ALREADY numeric costs a round trip through text and changes no answer, which is
    why this does not need to know which kind of column it was handed.
    """
    return f"TRY_TO_DECIMAL(TO_VARCHAR({e}), 38, 6)"


def _colref(c):
    """How a condition names a column AFTER the aggregation: the quoted output column.

    The other spelling is _dim_expr, which names the same dimension BEFORE it - `f.ST_CD`,
    `sm.CLUSTER`, `pv.SUB_DIVISION`. Both go through the same clause builders below, which is what
    lets one condition vocabulary serve a column the report prints and one it only filters on.
    """
    return f'"{c}"'


def _txt_eq(c, v, negate=False, ref=_colref):
    """A case- and space-insensitive equality test on a column holding TEXT.

    `eq` used to go through _scalar() alone, which returns None for anything that is not a number
    or a date - so "ST_CD Equals HB05" produced NO CLAUSE AT ALL and the report came back with
    every store in it, looking as though the condition had never been saved. A store code is the
    single most common thing anybody equates, so this is the path that has to work.

    Compared UPPER(TRIM(...)) on both sides for the same reason `contains` is an ILIKE: a code
    typed as "db03" means the DB03 in the data, and the fact table's own codes are padded in
    places. `ne` also has to admit NULL, which no comparison does on its own.
    """
    lit = base._q(str(v).strip().upper())
    e = ref(c)
    cl = f'UPPER(TRIM(TO_VARCHAR({e}))) {"<>" if negate else "="} {lit}'
    return f'({cl} OR {e} IS NULL)' if negate else cl


def _is_code(v):
    """True for a value that only LOOKS like a number: a storage tag 0001, a category code 0102.

    _scalar() parses those to 1.0 and 102.0, and an equality written that way is wrong twice over.
    It cannot match the text in the column ('0001' is not 1), and on a column that MIXES codes and
    words - KPI holds 0001 beside LQ_B05 - Snowflake coerces the whole column to a number to make
    the comparison and fails the query outright on the first non-numeric row.

    A LEADING ZERO is what says "code, not quantity": nobody writes a price as 0999. Everything
    else keeps the numeric comparison, which is what a condition on MRP or ARTICLE_NUMBER needs -
    matching those as text would miss 999 stored as 999.00.
    """
    t = str(v or "").strip()
    return len(t) > 1 and t[0] == "0" and not t.startswith("0.")


def _eq_clause(c, vals, ref=_colref):
    """One OR-ed clause for every `eq` condition saved on the SAME column.

    ANDing them is the only other reading and it is never the one anybody means: a column is one
    value per row, so "ST_CD Equals HB05 AND ST_CD Equals HD22" is a guaranteed empty report. Seven
    of those rows in the editor mean the seven stores, which is an IN list - so that is what they
    compile to. Conditions on DIFFERENT columns still AND, as they must.

    Numbers and dates keep the typed comparison; everything else goes through _txt_eq.
    """
    parts = []
    for v in vals:
        a = None if _is_code(v) else _scalar(v)
        if a is None:
            parts.append(_txt_eq(c, v, ref=ref))
        else:
            # _numsafe for the same reason BETWEEN needs it: ART_TYPE holds 1110 as TEXT beside a
            # handful of SAP material types, and a bare `= 1110` casts the column and fails the
            # whole query on them. A date literal compares against the column itself.
            e = _numsafe(ref(c)) if _num(v) is not None else ref(c)
            parts.append(f'{e} = {a}')
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"


def _in_values(f):
    """The value list behind an `is any of` / `is none of` condition.

    Two shapes arrive here and both mean the same list: the grid header TICKS values and sends
    `vals`, the report editor types a comma-separated line into `v1`. Accepting both is what lets
    one operator serve the header and the saved report, rather than the editor having to build an
    array the header would only take apart again.

    De-duplicated and capped at MAX_IN_VALUES, the same as a `filters` entry.
    """
    raw = f.get("vals")
    if not isinstance(raw, list):
        raw = str(f.get("v1") or "").split(",")
    out = []
    for v in raw:
        t = str(v).strip()
        if t != "" and t not in out:
            out.append(t)
    return out[:MAX_IN_VALUES]


def _col_where(cols, conds, ref=_colref):
    """WHERE clauses for the header conditions, validated against THIS run's own columns.

    A column the run does not produce is DROPPED rather than escaped - the same rule _clean_def
    uses - so an unknown name never reaches the query.

    `ref` says HOW a column is named in the clause. The default is the finished output column, so
    this is a HAVING on the aggregated result. _dim_where passes _dim_expr instead and gets the
    same conditions written against the raw dimension - which is what lets a condition be applied
    to a column the report filters on but does not print. `cols` is the allowed vocabulary either
    way: this run's output columns for the default, DIM_COLS for a dimension condition.
    """
    out = []
    eq_by_col = {}                      # col -> [values], so same-column Equals rows OR together
    for f in (conds or []):
        if not isinstance(f, dict):
            continue
        c = str(f.get("col") or "").strip()
        op = str(f.get("op") or "").strip().lower()
        if c not in cols:
            continue
        e = ref(c)
        if op == "eq":
            # Collected rather than emitted: every Equals on this column becomes ONE clause below.
            # A placeholder holds the spot so the finished WHERE reads in the order it was written.
            if c not in eq_by_col:
                out.append(("EQ", c))
            eq_by_col.setdefault(c, []).append(f.get("v1"))
        elif op == "ne":
            a = None if _is_code(f.get("v1")) else _scalar(f.get("v1"))
            if a is None:
                out.append(_txt_eq(c, f.get("v1"), True, ref))
            else:
                # Negated through COALESCE so the rows the comparison cannot judge - a NULL
                # column, or text that is not a number - are KEPT. "Does not equal 1110" has to
                # return the VERP row; NOT NULL on its own would silently drop it.
                out.append(f'(NOT COALESCE({_eq_clause(c, [f.get("v1")], ref)}, FALSE))')
        elif op in _LIST_OPS:
            # Built from the same _eq_clause as Equals, so a list of one and a single Equals
            # compile to exactly the same SQL and a text value is matched the same way.
            cl = _eq_clause(c, _in_values(f), ref)
            if cl:
                # Same COALESCE as `ne`, and for the same reason - it also covers the NULL
                # column the old `OR {e} IS NULL` spelling was there for.
                out.append(cl if op == "in" else f'(NOT COALESCE({cl}, FALSE))')
        elif op in _RAW_LIKE_OPS:
            v = str(f.get("v1") or "")
            if v != "":
                cl = f'TO_VARCHAR({e}) {_RAW_LIKE_OPS[op]} {base._q(v)}' + _LIKE_ESC
                out.append(f'({cl} OR {e} IS NULL)' if op == "not_like" else cl)
        elif op == "blank":
            out.append(f'{e} IS NULL')
        elif op == "nonblank":
            out.append(f'{e} IS NOT NULL')
        elif op == "between":
            # Numbers are ordered here so 'between 500 and 100' still means what the reader meant.
            # Date literals are left in the order given: they are already SQL text by this point,
            # and swapping them would mean parsing them back out again for no real gain.
            n1, n2 = _num(f.get("v1")), _num(f.get("v2"))
            if n1 is not None and n2 is not None:
                out.append(f'{_numsafe(e)} BETWEEN {min(n1, n2)} AND {max(n1, n2)}')
            else:
                a, b = _scalar(f.get("v1")), _scalar(f.get("v2"))
                if a is not None and b is not None:
                    out.append(f'{e} BETWEEN {a} AND {b}')
        elif op in _NUM_OPS:
            a = _scalar(f.get("v1"))
            if a is not None:
                # Same cast as BETWEEN, and for the same reason - but only when the comparison
                # really is numeric. A date literal has to compare against the column itself.
                lhs = _numsafe(e) if _num(f.get("v1")) is not None else e
                out.append(f'{lhs} {_NUM_OPS[op]} {a}')
        elif op in _TXT_OPS:
            v = str(f.get("v1") or "")
            if v != "":
                kw, shape = _TXT_OPS[op]
                cl = f'TO_VARCHAR({e}) {kw} {base._q(_like(v, shape))}' + _LIKE_ESC
                out.append(f'({cl} OR {e} IS NULL)' if op == "not_contains" else cl)

    # Swap each placeholder for the one OR-ed clause its column's Equals rows add up to. A column
    # whose values were all unusable drops out entirely, the same as any other empty condition.
    return [x for x in (_eq_clause(y[1], eq_by_col[y[1]], ref) if isinstance(y, tuple) else y
                        for y in out) if x]


def _merged_conds(defn, req_conds):
    """The report's SAVED conditions plus whatever this run added, in that order.

    Saved first so a report always carries its own conditions - including in the nightly dump,
    which sends none of its own. The run's conditions are ADDED, never substituted: they are
    ANDed together downstream, so opening a saved report and filtering a column further narrows
    it rather than quietly discarding the condition the report was built with.

    De-duplicated on (col, op, v1, v2) so re-applying the same condition from the header - which
    is exactly what happens when the page shows a saved condition as a chip - does not emit the
    clause twice.
    """
    out, seen = [], set()
    for f in list(defn.get("col_filters") or []) + list(req_conds or []):
        if not isinstance(f, dict):
            continue
        key = (str(f.get("col") or ""), str(f.get("op") or ""),
               str(f.get("v1")), str(f.get("v2")), str(f.get("vals")))
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def _outer_conds(defn, req_conds):
    """The conditions to apply to the FINISHED result, once build_sql has handed it back.

    HORIZONTAL is what it always was: the saved conditions plus this run's own.

    EITHER TURN is only the run's own. The saved ones went in BEFORE the turn (see _finish)
    because they name wide columns a turned result no longer has - leaving them here would mean
    _col_where silently dropped every one and the report came back wider than it was saved. The
    run's header conditions belong out here for the mirror reason: they name TD / MEASURE / AS_AT,
    or PERIOD / QTY / VALUE, which is what the person clicking the header can actually see.
    """
    if _is_turned(defn):
        return list(req_conds or [])
    return _merged_conds(defn, req_conds)


def _col_where_sql(cols, conds):
    """The same clauses as one ' WHERE ...' fragment, or '' when nothing survived validation."""
    w = _col_where(cols, conds)
    return (" WHERE " + " AND ".join(w)) if w else ""


def _dim_where(conds, expr=_dim_expr, allowed=None):
    """Conditions on a DIMENSION, written for the INNER scan.

    WHY THIS EXISTS. A col_filter is validated against the columns the run produces, so a
    condition could only ever name a column the report also printed - which made the editor's
    condition list a copy of its Group-by list. That is backwards: "only the north cluster",
    "brand not CLEARANCE" are exactly the narrowings you want WITHOUT the column in the output,
    and adding it to the grouping to filter on it splits every row.

    So a dimension condition is applied where a dimension filter has always been applied - inside
    the scan, against `f.ST_CD` / `sm.CLUSTER` / `pv.SUB_DIVISION` rather than against an output
    column. Same operators, same validation, same escaping: _col_where does the work, `ref` only
    changes how the column is spelled.

    Cheaper as well as more capable: this prunes the fact read instead of testing rows that have
    already been aggregated. `filters` ("is any of") keeps its own path because the export split
    reads that map directly; everything else arrives here.
    """
    return _col_where(DIM_COLS if allowed is None else allowed, conds, expr)


def _coal(aliases, col):
    """`a.COL` for one alias, `COALESCE(a.COL, b.COL, ...)` for several.

    The single-alias case is spelled without COALESCE so a one-block report emits exactly the SQL
    it emitted before there were three blocks - which is what lets the refactor be proved by
    comparing strings rather than by reading them.
    """
    if len(aliases) == 1:
        return f"{aliases[0]}.{col}"
    return "COALESCE(" + ", ".join(f"{a}.{col}" for a in aliases) + ")"


def _anchor_month(anc, view_mode):
    """The calendar month containing an anchor, as the pair the contribution divides by.

    Calendar, not fiscal: the Day Ref page's own contribution partitions on YEAR/MONTH of
    CAL_DATE, and this has to agree with it or the same cell reads two different percentages on
    two pages.
    """
    if view_mode:
        return (base.SqlExpr(f"DATE_TRUNC('MONTH', {anc})"), base.SqlExpr(f"LAST_DAY({anc})"))
    d = datetime.fromisoformat(str(anc)).date()
    nxt = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
    return (date(d.year, d.month, 1), nxt - timedelta(days=1))


def _layout_of(defn):
    """The layout this definition is set to, always one of LAYOUTS.

    Anything unrecognised reads as horizontal, which is what makes the two turns additive: a
    definition saved before either existed, and every report already in MIS_REPORT_DEF, comes
    back exactly as it always did.
    """
    v = str((defn or {}).get("layout") or "horizontal").strip().lower()
    return v if v in LAYOUTS else "horizontal"


def _is_period(defn):
    return _layout_of(defn) == "period"


def _is_turned(defn):
    """TRUE for either turn. The run, the export and the condition split care only about whether
    the result still carries the wide column names, and neither turn does - so they ask this
    rather than naming one layout and quietly leaving the other on the horizontal path."""
    return _layout_of(defn) != "horizontal"


def _vertical_rows(defn):
    """The measure AXIS - what becomes a row - in the same order _cols_for lays the wide columns
    out in. Sale, then stock, then Day Ref, then Sale Plan: turning the report on its side must not
    also reshuffle it, or the vertical and horizontal readings of one report stop looking alike.

    `prefix` is how the row finds its value for a given window - S_/R_/P_ + <window>__<measure>,
    the wide column name. `as_at` is the un-windowed alternative, and exactly one of the two is
    ever set.
    """
    rows = []
    for m in defn["measures"]:
        rows.append({"block": "SALE", "measure": m, "prefix": "S_", "as_at": None})
    for t in defn["stock_tags"]:
        for m in STOCK_MEASURES:
            # The measure name carries the TAG, because the tag is what distinguishes one stock row
            # from the next - <tag>__STOCK_Q is the wide column with its K_ stripped, so the screen
            # can label it with the same rule it labels the wide header with.
            rows.append({"block": "STOCK", "measure": f"{t}__{m}", "prefix": None,
                         "as_at": f"K_{t}__{m}"})
    for m in defn.get("ref_measures") or []:
        rows.append({"block": "DAY_REF", "measure": m, "prefix": "R_", "as_at": None})
    for m in defn.get("plan_measures") or []:
        rows.append({"block": "SALE_PLAN", "measure": m, "prefix": "P_", "as_at": None})
    # A MONTH PLAN row has neither a window prefix nor an as-at column: its values live under the
    # MONTH columns, which are a third kind of value column on the vertical sheet. `month` says so,
    # and _vertical_sql reads M_<token>__<measure> off it the same way the others read their own.
    for m in defn.get("month_measures") or []:
        rows.append({"block": "MONTH_PLAN", "measure": m, "prefix": None, "as_at": None,
                     "month": True})
    for m in defn.get("grc_measures") or []:
        rows.append({"block": "GRC", "measure": m, "prefix": "G_", "as_at": None})
    # A PO row is an AS-AT row, exactly like a stock one: it has a single value and no window to
    # spread it across, so it goes in the AS_AT column rather than repeating itself under TD, MTD
    # and the rest. That is why AS_AT is now emitted for a PO report with no stock tags in it.
    for m in defn.get("po_measures") or []:
        rows.append({"block": "PO", "measure": m, "prefix": None,
                     "as_at": f"O_{PO_PERIOD}__{m}"})
    return rows


def _vertical_cols(defn):
    """The vertical report's columns, in display order: the grouping, the two labels that say
    which measure this row is, then one column per window, then AS_AT if there are stock tags."""
    cols = list(defn["group_by"]) + [VERT_BLOCK_COL, VERT_MEAS_COL] + list(defn["windows"])
    # The months are value columns beside the windows - a month is to a Month Plan row what TD is
    # to a sale row. They can never collide: a window is one of ten fixed names and a month token
    # is one of five short codes or a six-digit YYYYMM.
    cols += list(defn.get("plan_months") or [])
    # PO shares the stock block's AS_AT column - both are positions, and neither has a window to
    # sit under. They can never collide in a cell: a row belongs to one block or the other.
    if defn["stock_tags"] or (defn.get("po_measures") or []):
        cols.append(VERT_AS_AT_COL)
    return cols


def _vertical_sql(wide_sql, defn):
    """Turn the finished wide result on its side, in Snowflake.

    The wide query becomes a derived table and is CROSS JOINed to a VALUES row-set holding one row
    per measure. That reads the aggregate ONCE and fans it out by the join, rather than UNIONing
    one SELECT per measure - which would re-read it once per measure and is how a ten-measure
    report would have paid for ten scans.

    "_ORD" rides along so the measures come back in catalogue order rather than alphabetically -
    GM V above Sale V is not the order anyone built the report in. It is deliberately NOT in
    _vertical_cols: the callers project the column list explicitly and ORDER BY it from outside,
    so it sorts the result without appearing in it.
    """
    rows = _vertical_rows(defn)
    if len(rows) > MAX_VERT_ROWS:
        raise HTTPException(400, f"This report has {len(rows)} measures, so the vertical layout "
                                 f"would give every group {len(rows)} rows - the cap is "
                                 f"{MAX_VERT_ROWS}. Drop some stock tags or measures, or read it "
                                 f"in the Horizontal layout.")
    # Every literal here is OURS - block names are fixed strings and measure/tag names came out of
    # the catalogue via _clean_def, never off the request - and _q quotes them anyway rather than
    # leaving that fact load-bearing.
    vals = ", ".join(f"({i}, {base._q(r['block'])}, {base._q(r['measure'])})"
                     for i, r in enumerate(rows))
    axis = (f'(SELECT * FROM (VALUES {vals}) AS v("_ORD", "{VERT_BLOCK_COL}", '
            f'"{VERT_MEAS_COL}")) r')

    sel = [f'w."{c}" AS "{c}"' for c in defn["group_by"]]
    sel += [f'r."{VERT_BLOCK_COL}"', f'r."{VERT_MEAS_COL}"']
    for w in defn["windows"]:
        br = " ".join(f'WHEN {i} THEN w."{r["prefix"]}{w}__{r["measure"]}"'
                      for i, r in enumerate(rows) if r["prefix"])
        # A report with windows but nothing windowed to put in them cannot get here - _clean_def
        # rejects it - but an all-NULL column is still a better answer than a SQL syntax error if
        # that ever stops being true.
        sel.append(f'CASE r."_ORD" {br} END AS "{w}"' if br
                   else f'CAST(NULL AS FLOAT) AS "{w}"')
    for t in defn.get("plan_months") or []:
        br = " ".join(f'WHEN {i} THEN w."M_{t}__{r["measure"]}"'
                      for i, r in enumerate(rows) if r.get("month"))
        sel.append(f'CASE r."_ORD" {br} END AS "{t}"' if br
                   else f'CAST(NULL AS FLOAT) AS "{t}"')
    if defn["stock_tags"] or (defn.get("po_measures") or []):
        br = " ".join(f'WHEN {i} THEN w."{r["as_at"]}"'
                      for i, r in enumerate(rows) if r["as_at"])
        sel.append(f'CASE r."_ORD" {br} END AS "{VERT_AS_AT_COL}"')
    sel.append('r."_ORD"')
    return f"SELECT {', '.join(sel)} FROM ({wide_sql}) w CROSS JOIN {axis}"


# ── THE PERIOD TURN: periods down the page, measure kinds across it ────────────────────
def _month_kind(m):
    """A Month Plan measure's kind. Derived rather than mapped because the four plans grow a
    measure at a time and a missing entry would silently give the new one a column of its own; the
    rule is the one the grid's K / L divisor already uses - _V is a value, everything else in the
    block is a quantity (TRF_OP_STK, TRF_CL_STK and PP_POS_PO_RAISED all are, despite the names),
    and cover days are the single exception."""
    if m in _MONTH_DAY_MEAS:
        return None
    return "VALUE" if m.endswith("_V") else "QTY"


def _period_slots(defn):
    """One slot per MEASURE the report picked, in the order _cols_for lays the wide columns out in:
    sale, stock, Day Ref, Sale Plan, Month Plan. Turning the report must not also reshuffle it.

    A slot is "a measure, and where to find it on a row of its family": `prefix` plus the row's own
    period plus the measure name IS the wide column, uniformly across all three families -
    S_TD__SALE_Q, K_0001__STOCK_Q, M_CM__SALE_W_Q - which is why this layout needs no per-family
    special case in the SQL below.

    THE STOCK BLOCK IS TWO SLOTS, NOT TWO PER TAG, because the tag is a ROW here. That is the whole
    difference from the vertical sheet, where the tag had to ride inside the measure name.
    """
    slots = []
    for m in defn["measures"]:
        slots.append({"family": FAM_WINDOW, "prefix": "S_", "measure": m,
                      "kind": _MEAS_KIND.get(m)})
    if defn["stock_tags"]:
        for m in STOCK_MEASURES:
            slots.append({"family": FAM_STOCK, "prefix": "K_", "measure": m,
                          "kind": _MEAS_KIND.get(m)})
    for m in defn.get("ref_measures") or []:
        slots.append({"family": FAM_WINDOW, "prefix": "R_", "measure": m,
                      "kind": _MEAS_KIND.get(m)})
    for m in defn.get("plan_measures") or []:
        slots.append({"family": FAM_WINDOW, "prefix": "P_", "measure": m,
                      "kind": _MEAS_KIND.get(m)})
    for m in defn.get("month_measures") or []:
        slots.append({"family": FAM_MONTH, "prefix": "M_", "measure": m,
                      "kind": _month_kind(m)})
    # GRC is a window family like sale; PO is its own, with one row. Both pass kind=None, which is
    # what keeps each of their measures in a column named after itself instead of splitting the
    # sheet's merged QTY column apart for everybody - see the note in _MEAS_KIND.
    for m in defn.get("grc_measures") or []:
        slots.append({"family": FAM_WINDOW, "prefix": "G_", "measure": m, "kind": None})
    for m in defn.get("po_measures") or []:
        slots.append({"family": FAM_PO, "prefix": "O_", "measure": m, "kind": None})
    return slots


def _period_plan(defn):
    """(slots, columns, rows) for the period layout - the whole shape of the sheet, decided once.

    THE MERGE AND THE SPLIT. A slot wants the column of its KIND (QTY / VALUE / GM). It gets it
    only if no OTHER slot of that kind shares its row family, because two slots on one row have one
    cell between them; when two do, that kind breaks into one column per measure, each named after
    its measure. So Sale Q and Stock Q merge into QTY - a window row and a tag row never meet -
    while Sale Q and Avg Sale Q, both quantities of a window, come back as SALE_Q and AVG_SALE_Q.
    Nothing is dropped and nothing is summed to make it fit.

    COLUMN ORDER is QTY, VALUE, GM first, because that is the order the sheet is read in, then
    whatever else the report picked in catalogue order. A kind that split keeps its slots in
    catalogue order too, so a column never moves for a reason the reader cannot see.

    ROW ORDER is the windows in catalogue order, then the stock tags, then the plan months - each
    family included only if some slot actually reads it, so a report with no windowed measure gets
    no blank TD row.
    """
    slots = _period_slots(defn)
    by_kind = {}
    for s in slots:
        if s["kind"] in MERGE_KINDS:
            by_kind.setdefault(s["kind"], []).append(s)
    # A kind splits when two of its slots share a family - i.e. when its families are fewer than
    # its slots.
    split = {k for k, ss in by_kind.items() if len({s["family"] for s in ss}) != len(ss)}
    for s in slots:
        s["col"] = (s["kind"] if s["kind"] in MERGE_KINDS and s["kind"] not in split
                    else s["measure"])

    cols, seen = [], set()
    for c in MERGE_KINDS + [s["col"] for s in slots]:
        if c not in seen and any(s["col"] == c for s in slots):
            seen.add(c)
            cols.append(c)

    fams = {s["family"] for s in slots}
    rows = []
    if FAM_WINDOW in fams:
        rows += [(w, FAM_WINDOW) for w in defn["windows"]]
    if FAM_STOCK in fams:
        rows += [(t, FAM_STOCK) for t in defn["stock_tags"]]
    if FAM_MONTH in fams:
        rows += [(t, FAM_MONTH) for t in defn.get("plan_months") or []]
    # The PO row, last and always exactly one - the block has a single period by construction.
    if FAM_PO in fams:
        rows.append((PO_PERIOD, FAM_PO))
    return slots, cols, rows


def _slot_at(slots, col, fam):
    """The one slot in this column on a row of this family, or None for a cell that has no value -
    the GM of a stock tag, a month measure on a window row. Unique by construction: that is exactly
    what the split in _period_plan guarantees."""
    for s in slots:
        if s["col"] == col and s["family"] == fam:
            return s
    return None


def _period_cols(defn):
    """The period report's columns, in display order: the grouping, the period label, then the
    measure columns."""
    _slots, cols, _rows = _period_plan(defn)
    return list(defn["group_by"]) + [PERIOD_COL] + cols


def _period_meta(defn):
    """What the GRID needs in order to format a period cell, computed here because the rule that
    built the columns lives here.

    A merged column holds different KINDS of number down the page - QTY is pieces of sale on a
    window row and pieces of stock on a tag row - so a cell can no more be formatted from its
    header than a vertical cell can. The vertical sheet solved that by having the browser rebuild
    the wide column name (vertRowKey); doing the same here would mean a second copy of _MEAS_KIND
    and of the split rule in JavaScript, which is two implementations of one decision and exactly
    how the two would drift. So the server sends the answer instead:

      rows  {period: family}            which family each row belongs to
      cols  {column: {family: wide}}    a REPRESENTATIVE wide column name per cell kind

    Representative is enough because no format rule reads the window, the tag or the month - only
    the prefix and the measure - which is the same reason vertRowKey can stand TD in for every
    window. A cell with no entry is a cell with no value.
    """
    slots, cols, rows = _period_plan(defn)
    rep = {}
    for period, fam in rows:
        rep.setdefault(fam, period)
    out = {}
    for c in cols:
        per_fam = {}
        for fam, period in rep.items():
            s = _slot_at(slots, c, fam)
            if s:
                per_fam[fam] = "%s%s__%s" % (s["prefix"], period, s["measure"])
        out[c] = per_fam
    return {"rows": {period: fam for period, fam in rows}, "cols": out}


def _period_sql(wide_sql, defn):
    """Turn the finished wide result on its side the other way, in Snowflake.

    Same mechanism as _vertical_sql and for the same reason: the wide query becomes a derived table
    and a small VALUES row-set - one row per PERIOD this time - fans it out by a CROSS JOIN, so the
    aggregate is read once however many rows it ends up spread over.

    "_ORD" rides along to keep the periods in catalogue order rather than alphabetical (MTD above TD
    is nobody's sheet) and is deliberately not in _period_cols, exactly as in the vertical turn.
    """
    slots, cols, rows = _period_plan(defn)
    # Not reachable through _clean_def, which rejects a report with no measures of any kind and a
    # measure family with no axis to read it over. Explicit anyway: the VALUES row-set below is not
    # valid SQL when empty, and a 400 that says what is missing beats a Snowflake syntax error.
    if not rows:
        raise HTTPException(400, "The period layout needs at least one period to put down the "
                                 "page - a window, a stock tag or a plan month. Pick one, or read "
                                 "this report in the Horizontal layout.")
    if len(rows) > MAX_VERT_ROWS:
        raise HTTPException(400, f"This report has {len(rows)} periods, so the period layout would "
                                 f"give every group {len(rows)} rows - the cap is {MAX_VERT_ROWS}. "
                                 f"Drop some stock tags, windows or months, or read it in the "
                                 f"Horizontal layout.")
    # Every literal here is OURS - the family names are fixed strings, and the periods and the
    # measures came out of the catalogue via _clean_def, never off the request - and _q quotes them
    # anyway rather than leaving that fact load-bearing.
    vals = ", ".join(f"({i}, {base._q(period)})" for i, (period, _fam) in enumerate(rows))
    axis = f'(SELECT * FROM (VALUES {vals}) AS v("_ORD", "{PERIOD_COL}")) r'

    sel = [f'w."{c}" AS "{c}"' for c in defn["group_by"]]
    sel.append(f'r."{PERIOD_COL}"')
    for c in cols:
        br = " ".join(f'WHEN {i} THEN w."{s["prefix"]}{period}__{s["measure"]}"'
                      for i, (period, fam) in enumerate(rows)
                      for s in [_slot_at(slots, c, fam)] if s)
        # A column that is blank on every row cannot arise - a column exists because some slot
        # wanted it, and that slot's family is in `rows` - but an all-NULL column still beats a
        # syntax error if that ever stops being true.
        sel.append(f'CASE r."_ORD" {br} END AS "{c}"' if br
                   else f'CAST(NULL AS FLOAT) AS "{c}"')
    sel.append('r."_ORD"')
    return f"SELECT {', '.join(sel)} FROM ({wide_sql}) w CROSS JOIN {axis}"


def _finish(wide_sql, keys, cols, defn, saved_conds):
    """The one exit both of build_sql's returns go through.

    HORIZONTAL is returned untouched, conditions and all - the callers apply those themselves and
    always have.

    EITHER TURN applies the report's SAVED conditions HERE, before the turn. They were written
    in the editor against wide column names (S_MTD__SALE_V), which neither turned result has, so
    applying them afterwards would mean _col_where dropped every one of them as unknown and a
    report narrowed to seven stores quietly came back with six hundred. Applying them first is also
    the right reading: ">= 1 lakh MTD" narrows the GROUPS, not the individual measure or period
    rows. The run's own header conditions stay outside, where they can name what is on screen.
    """
    if not _is_turned(defn):
        return wide_sql, keys, cols
    where = _col_where_sql(cols, saved_conds)
    # Wrapped, not appended: a WHERE on the wide SELECT cannot see that SELECT's own aliases.
    inner = f"SELECT * FROM ({wide_sql}){where}" if where else wide_sql
    if _is_period(defn):
        return _period_sql(inner, defn), keys, _period_cols(defn)
    return _vertical_sql(inner, defn), keys, _vertical_cols(defn)


def build_sql(defn, from_date, to_date, anchor_sql=None, saved_conds=None):
    """Return (sql, keys, cols) for one run of this definition.

    VIEW MODE (`anchor_sql` given, and it must be a base.SqlExpr): every anchor and window bound
    becomes a SQL EXPRESSION instead of a literal date, so the query re-resolves its own dates every
    time it is read. That is what makes a report publishable as a view - a view takes no parameters,
    and a literal date baked in would freeze it on the day it was created.

    Only the date arithmetic branches. The dimension handling, the sale and stock assembly and the
    outer join below are shared, because a second copy of them is how the view and the grid would
    quietly stop agreeing.

    In view mode DT is dropped from the grouping: there is exactly one anchor, so a per-day column
    would be one value repeated. from_date / to_date are then ignored.
    """
    view_mode = anchor_sql is not None
    if view_mode:
        # A published view is a CONTRACT: something downstream SELECTs its columns by name, and
        # flipping a saved report to vertical must not silently reshape it under them. So the view
        # is always the wide shape, whatever the report is set to display as.
        defn = {**defn, "group_by": [c for c in defn["group_by"] if c != "DT"],
                "layout": "horizontal"}
        d0 = d1 = None
    else:
        a, b = base._range(from_date, to_date)
        d0, d1 = datetime.fromisoformat(a).date(), datetime.fromisoformat(b).date()

    by_day = (not view_mode) and "DT" in defn["group_by"]
    if by_day:
        span = (d1 - d0).days + 1
        if span > base.MAX_ANCHOR_DAYS:
            raise HTTPException(400, f"{span} days requested. Grouping by DATE gives each day its "
                                     f"own window set, so the range is capped at "
                                     f"{base.MAX_ANCHOR_DAYS} days - drop DATE from the grouping, "
                                     f"or narrow the dates.")
        anchors = [(d0 + timedelta(days=i)).isoformat() for i in range(span)]
    elif view_mode:
        anchors = [anchor_sql]
    else:
        anchors = [b]          # one row per group, as at the To date

    wins_by_anchor = {}
    for anc in anchors:
        if view_mode:
            w = base._window_exprs(anc)
            # RANGE is the caller's own From/To, and a view has no caller - so it collapses to the
            # anchor day, which is what the grid shows when From = To = the anchor.
            w["RANGE"] = (anc, anc)
        else:
            w = base._window_ranges(anc)
            w["RANGE"] = (d0, d1)
        wins_by_anchor[anc] = w

    if view_mode:
        # min()/max() over SQL text would compare strings, not dates. LEAST/GREATEST make Snowflake
        # do the comparison at read time, which is the only place the dates exist.
        bounds = [str(v) for w in wins_by_anchor.values() for pair in w.values() for v in pair]
        scan_lo = base.SqlExpr("LEAST(" + ", ".join(bounds) + ")")
        scan_hi = base.SqlExpr("GREATEST(" + ", ".join(bounds) + ")")
    else:
        scan_lo = min(v[0] for w in wins_by_anchor.values() for v in w.values())
        scan_hi = max(v[1] for w in wins_by_anchor.values() for v in w.values())

    dims = [c for c in defn["group_by"] if c != "DT"]
    dim_sel = ", ".join(f"{_dim_expr(c)} AS {c}" for c in dims)
    dim_csv = ", ".join(dims)
    # GROUP BY the QUALIFIED expressions, not the output aliases. ST_CD exists on the fact, on the
    # store master and on the stock pick, so a bare "GROUP BY ST_CD" is ambiguous the moment a
    # store attribute is used - it compiled fine for product-only reports and failed for store ones.
    dim_grp = ", ".join(_dim_expr(c) for c in dims)
    joins, conds = _joins(defn), _filter_sql(defn)

    # ---- daily group totals -----------------------------------------------------------------
    daily_where = [f"f.DT BETWEEN {base._dt(scan_lo)} AND {base._dt(scan_hi)}"] + conds
    daily = (f"SELECT {dim_sel + ', ' if dims else ''}f.DT, "
             f"SUM(f.SALE_V) SV, SUM(f.SALE_Q) SQ, SUM(f.GM_V) GM, SUM(f.DISCOUNT_V) DV "
             f"FROM {base.SALE_TBL} f{joins} "
             f"WHERE {' AND '.join(daily_where)} "
             f"GROUP BY {dim_grp + ', ' if dims else ''}f.DT")

    # ---- windows over those daily totals -----------------------------------------------------
    branches = []
    for anc in anchors:
        sel = []
        for w in defn["windows"]:
            lo, hi = wins_by_anchor[anc][w]
            inw = f"DT BETWEEN {base._dt(lo)} AND {base._dt(hi)}"
            for meas in defn["measures"]:
                alias = f"S_{w}__{meas}"
                if meas.startswith("AVG_"):
                    col = _SRC[meas[4:]]
                    sel.append(f'ROUND(AVG(CASE WHEN {inw} AND {col} > 0 THEN {col} END),4) '
                               f'AS "{alias}"')
                elif meas == "SAL_DAYS":
                    sel.append(f'COUNT(CASE WHEN {inw} AND SQ > 0 THEN 1 END) AS "{alias}"')
                elif meas == "GM_DAYS":
                    sel.append(f'COUNT(CASE WHEN {inw} AND GM > 0 THEN 1 END) AS "{alias}"')
                elif meas == "DATA_DAYS":
                    sel.append(f'COUNT(CASE WHEN {inw} THEN 1 END) AS "{alias}"')
                else:
                    sel.append(f'ROUND(SUM(CASE WHEN {inw} THEN {_SRC[meas]} END),4) '
                               f'AS "{alias}"')
        head = (f"{base._dt(anc)} AS DT, " if by_day else "") + (dim_csv + ", " if dims else "")
        branches.append(f"SELECT {head}{', '.join(sel)} FROM daily"
                        + (f" GROUP BY {dim_csv}" if dims else ""))
    sale = " UNION ALL ".join(branches)

    keys = (["DT"] if by_day else []) + dims
    cols = _cols_for(defn)
    sale_cols = [c for c in cols if c.startswith("S_")]
    stock_cols = [c for c in cols if c.startswith("K_")]
    ref_cols = [c for c in cols if c.startswith("R_") and "__" in c]
    plan_cols = [c for c in cols if c.startswith("P_") and "__" in c]
    grc_cols = [c for c in cols if c.startswith("G_") and "__" in c]
    po_cols = [c for c in cols if c.startswith("O_") and "__" in c]

    # ---- Day Ref: SP_AKS_DAY_REF_LOAD's logic, stopped one step short of its final collapse ----
    #
    # Same two-step shape as the sale block - aggregate to (dimensions, CAL_DATE) first, then apply
    # the windows to those daily group totals - and for the same reason: AVG_STK has to be summed
    # ACROSS the group and averaged ACROSS the days, in that order, or it becomes an average of
    # averages.
    #
    # The article-grain rows are shaped to look exactly like the sale fact, aliased `f`, so
    # _dim_expr, _joins and _filter_sql apply to them VERBATIM. That is the point of the shape:
    # a dimension resolves identically on both sides of the row, and there is no second copy of
    # the dimension logic to drift.
    ref_daily = ref_block = None
    ref_pre = []
    if ref_cols:
        r_measures = defn["ref_measures"]
        # THE CONTRIBUTION NEEDS THE WHOLE MONTH. It is a day's LYSP value as a share of its own
        # calendar month, so the scan has to reach the month's edges even when the window does not
        # - a 3-day window would otherwise divide by those 3 days and read ~10x too big. Only
        # widened when the measure is actually asked for; it is a wider read than any window.
        r_bounds = [v for w in wins_by_anchor.values() for pair in w.values() for v in pair]
        if "LYSP_DAY_CONTRI" in r_measures:
            for anc in anchors:
                r_bounds += list(_anchor_month(anc, view_mode))
        if view_mode:
            r_lo = base.SqlExpr("LEAST(" + ", ".join(str(v) for v in r_bounds) + ")")
            r_hi = base.SqlExpr("GREATEST(" + ", ".join(str(v) for v in r_bounds) + ")")
        else:
            r_lo, r_hi = min(r_bounds), max(r_bounds)
        d_lo, d_hi = base._dt(r_lo), base._dt(r_hi)

        # WHAT THE PICKED MEASURES ACTUALLY NEED. Everything below is built from this and nothing
        # else - the block used to emit all three run types and both source branches no matter
        # what was ticked, so a report wanting two LYSP stock columns still scanned VBRP for a
        # sale it never showed and walked the spine three times over.
        #
        # Measured 12-Sep-2026 on rdc-var-article-rpt (ref_measures = LYSP_AVG_STK_Q +
        # LYSP_AVG_STK_V, nothing else): the profile pushed 1.52 BILLION rows through the joins
        # and aggregates to return 34,440 rows, with 27% of the runtime in the ref_daily
        # aggregate computing fifteen measures in order to use two.
        #
        # LYSP_DAY_CONTRI is DERIVED, not stored: the window branch divides two LYSP_SALE_V sums,
        # so asking for it pulls in the LYSP sale that nothing else may have asked for.
        r_need = {m for m in r_measures if m in base.REF_MEASURES}
        if "LYSP_DAY_CONTRI" in r_measures:
            r_need.add("LYSP_SALE_V")
        r_kinds = {m.split("_", 1)[0] for m in r_need}
        r_fams = {m.split("_", 1)[1] for m in r_need}
        need_sale = bool(r_fams & {"SALE_Q", "SALE_V", "GM_VAL"})
        need_stk = bool(r_fams & {"AVG_STK_Q", "AVG_STK_V"})

        # The calendar spine, exploded to one row per (store, report day, WHICH year it reads).
        # The proc picks a single SRC_DATE per run because it is invoked once per run type; here
        # every run type WANTED is a row and the branch becomes a conditional sum. An equi-join on
        # SRC_DATE either way. Only the wanted kinds: an unasked-for kind is a third of the spine
        # and, for LYSM, a whole extra year of dates for ref_days to drag out of the sources.
        kinds = " UNION ALL ".join(
            f"SELECT ST_CD, CAL_DATE, {col} AS SRC_DATE, {base._q(k)} AS KIND FROM {REF_CAL_TBL} "
            f"WHERE CAL_DATE BETWEEN {d_lo} AND {d_hi}"
            + ("" if k == "ACT" else f" AND {col} IS NOT NULL")
            for k, col in REF_KINDS.items() if k in r_kinds)
        ref_pre.append(f"ref_cal AS ({kinds})")
        # One scan of VBRP and one of the stock snapshot, restricted to THE DATES THE SPINE ASKS
        # FOR - the set, not its MIN..MAX envelope.
        #
        # THE ENVELOPE WAS THE WHOLE COST OF THIS REPORT. The spine's dates arrive in TIGHT
        # CLUSTERS with a year-long hole in the middle: a month-end report wants ~62 ACT days this
        # year and ~62 LYSP/LYSM days last year, so MIN..MAX spans 426 days of which 302 are
        # wanted by nothing. Measured 12-Sep-2026 on the rdc-var-article-rpt view: 124 distinct
        # SRC_DATEs inside a 426-day envelope, and ET_STOCK_DATA is clustered on STOCK_DATE at
        # depth 1.09 - so the envelope assigned 1242/1242 partitions / 26.0 GB where a 13-date
        # probe assigns 63 / 1.4 GB. The whole view was reading 46.4 GB on an X-Small.
        #
        # Snowflake plans this as a SemiJoin and pushes a JoinFilter onto the ET_STOCK_DATA and
        # VBRP table scans, so the pruning is real - but it happens at RUNTIME, so EXPLAIN still
        # prints the full partition count. Read the query profile, not the plan.
        #
        # Equivalent by construction: ref_fact inner-joins both sources back to ref_cal on
        # `d = c.SRC_DATE`, so a row on any other date was always discarded one step later. This
        # replaces the span CTE, and with it three more scans of the LYSP master that existed only
        # to compute the envelope.
        ref_pre.append("ref_days AS (SELECT DISTINCT SRC_DATE FROM ref_cal)")
        # The article bridge, identical to the day-wise sale loader's _prod_d (db-scripts/298) so
        # the two vocabularies are the same one.
        ref_pre.append(f"""ref_prod AS (
            SELECT mat_key, ARTICLE_NUMBER, GEN_ART_NUMBER, GEN_ART_DESC, COLOR, SIZE_1, MAJ_CAT, SEG
              FROM (SELECT LTRIM(MATNR,'0') mat_key, TO_NUMBER(ARTICLE_NUMBER) ARTICLE_NUMBER,
                           TO_NUMBER(GEN_ART_NUMBER) GEN_ART_NUMBER, GEN_ART_DESC,
                           NVL(NULLIF(TRIM(COLOR),''),'NA') COLOR,
                           NVL(NULLIF(TRIM(SIZE_1),''),'NA') SIZE_1, MAJ_CAT, SEG,
                           ROW_NUMBER() OVER (PARTITION BY LTRIM(MATNR,'0')
                                              ORDER BY MODIFY_DATE DESC NULLS LAST) rn
                      FROM {REF_DIM_PROD}
                     WHERE MATNR IS NOT NULL AND TRIM(NVL(MAJ_CAT,'')) <> ''
                       AND ARTICLE_NUMBER IS NOT NULL AND GEN_ART_NUMBER IS NOT NULL)
             WHERE rn = 1)""")
        # The report's ST_CD scope, pushed INTO both source scans. The outer WHERE applies it
        # anyway, so this changes no result - only how much of VBRP and the stock snapshot is read
        # before it is thrown away. A one-store report was reading the whole estate.
        _sv = [str(x) for x in (defn["filters"].get("ST_CD") or []) if str(x).strip()]
        st_v = (" AND v.WERKS IN (" + ", ".join(base._q(x) for x in _sv) + ")") if _sv else ""
        st_s = (" AND s.WERKS IN (" + ", ".join(base._q(x) for x in _sv) + ")") if _sv else ""
        if need_sale:
          ref_pre.append(f"""ref_sale AS (
            SELECT v.WERKS st, {REF_VBRP_DATE} d, LTRIM(v.MATNR,'0') mk,
                   SUM(CASE WHEN NVL(v.SHKZG,'') = 'X'
                            THEN -TRY_TO_DOUBLE(TO_VARCHAR(v.FKIMG))
                            ELSE  TRY_TO_DOUBLE(TO_VARCHAR(v.FKIMG)) END) q,
                   SUM(TRY_TO_DOUBLE(TO_VARCHAR(v.NETWR))) val,
                   SUM(TRY_TO_DOUBLE(TO_VARCHAR(v.NETWR))
                     - TRY_TO_DOUBLE(TO_VARCHAR(v.WAVWR))) gm
              FROM {REF_VBRP} v
             WHERE v.FKART_ANA = 'FP'
               AND {REF_VBRP_DATE} IN (SELECT SRC_DATE FROM ref_days){st_v}
             GROUP BY 1, 2, 3)""")
        # Exact date, no carry-forward: the proc's P_LOCF_MAX_DAYS default of 0. A day the snapshot
        # does not hold produces no row rather than a repeated one.
        lg = ", ".join(base._q(t) for t in REF_STOCK_LGORT)
        if need_stk:
          ref_pre.append(f"""ref_stk AS (
            SELECT s.WERKS st, s.STOCK_DATE::DATE d, LTRIM(s.MATNR,'0') mk,
                   SUM(s.LABST) q, SUM(s.LABST_DMBTR) val
              FROM {REF_STOCK_TBL} s
             WHERE s.LGORT IN ({lg})
               AND s.STOCK_DATE::DATE IN (SELECT SRC_DATE FROM ref_days){st_s}
             GROUP BY 1, 2, 3)""")
        # Sale and stock rows in ONE fact-shaped stream, so the whole thing aggregates in a single
        # pass and every dimension is resolved once - but only the branches a picked measure reads.
        # A branch is not a column that goes unselected: it is a full scan of VBRP or of
        # ET_STOCK_DATA, joined to the spine and to the product bridge, and unioned into the row
        # count that every operator above it then carries.
        rf = []
        if need_sale:
            rf.append("""
            SELECT c.CAL_DATE DT, c.KIND, c.ST_CD, p.ARTICLE_NUMBER, p.GEN_ART_NUMBER,
                   p.GEN_ART_DESC, p.COLOR, p.SIZE_1, p.MAJ_CAT, p.SEG,
                   a.q SQ, a.val SV, a.gm SG, 0 KQ, 0 KV
              FROM ref_cal c
              JOIN ref_sale a ON a.st = c.ST_CD AND a.d = c.SRC_DATE
              JOIN ref_prod p ON p.mat_key = a.mk""")
        if need_stk:
            # ACT stock alone is restricted to an active store, which is the proc's own asymmetry
            # (its LY stock branches skip that check). With no ACT on the spine that test is
            # vacuously true for every row, so the correlated EXISTS is dropped rather than run
            # against each of them.
            plant = (f"""
             WHERE c.KIND <> 'ACT'
                OR EXISTS (SELECT 1 FROM {REF_PLANT_TBL} d
                            WHERE TRIM(d.ST_CD) = TRIM(c.ST_CD)
                              AND TRIM(d.ST_TYP) = 'STORE'
                              AND TRIM(d.ST_STAT) IN ('ACT', 'UPC'))""" if "ACT" in r_kinds else "")
            rf.append(f"""
            SELECT c.CAL_DATE DT, c.KIND, c.ST_CD, p.ARTICLE_NUMBER, p.GEN_ART_NUMBER,
                   p.GEN_ART_DESC, p.COLOR, p.SIZE_1, p.MAJ_CAT, p.SEG,
                   0 SQ, 0 SV, 0 SG, k.q KQ, k.val KV
              FROM ref_cal c
              JOIN ref_stk k ON k.st = c.ST_CD AND k.d = c.SRC_DATE
              JOIN ref_prod p ON p.mat_key = k.mk{plant}""")
        ref_pre.append("ref_fact AS (" + " UNION ALL ".join(rf) + ")")

        _RSRC = {"SALE_Q": "SQ", "SALE_V": "SV", "GM_VAL": "SG",
                 "AVG_STK_Q": "KQ", "AVG_STK_V": "KV"}
        rsel = []
        for m in base.REF_MEASURES:                       # ACT_SALE_Q ... LYSM_AVG_STK_V
            if m not in r_need:                           # r_need already carries the CONTRI's
                continue                                  # LYSP_SALE_V, which nothing selects
            kind, meas = m.split("_", 1)
            rsel.append(f"SUM(IFF(f.KIND = {base._q(kind)}, f.{_RSRC[meas]}, 0)) AS {m}")
        ref_daily = (f"SELECT {dim_sel + ', ' if dims else ''}f.DT, {', '.join(rsel)} "
                     f"FROM ref_fact f{joins} "
                     + ((" WHERE " + " AND ".join(conds)) if conds else "")
                     + f" GROUP BY {dim_grp + ', ' if dims else ''}f.DT")

        rbranches = []
        for anc in anchors:
            m_lo, m_hi = _anchor_month(anc, view_mode)
            sel = []
            for w in defn["windows"]:
                lo, hi = wins_by_anchor[anc][w]
                inw = f"DT BETWEEN {base._dt(lo)} AND {base._dt(hi)}"
                for m in r_measures:
                    alias = f"R_{w}__{m}"
                    if m == "LYSP_DAY_CONTRI":
                        # Recomputed at the GROUP's grain, not averaged from the source rows: the
                        # window's LYSP value over the month's, both for this group. A share of a
                        # share has no meaning, so the only correct move is to divide the two
                        # totals the group actually has.
                        inm = f"DT BETWEEN {base._dt(m_lo)} AND {base._dt(m_hi)}"
                        sel.append(
                            f'ROUND(SUM(CASE WHEN {inw} THEN GREATEST(LYSP_SALE_V, 0) END) '
                            f'/ NULLIF(SUM(CASE WHEN {inm} THEN GREATEST(LYSP_SALE_V, 0) END), 0)'
                            f', 6) AS "{alias}"')
                    elif m in _REF_AVG:
                        # ZEROS COUNT HERE, unlike the sale block's AVG_ measures. That rule
                        # ("average over the days that sold") answers what a typical SELLING day
                        # looked like, and a zero-sale day is not one. A stock position is the
                        # opposite: a category that held stock for 10 days of a month and nothing
                        # for the other 20 really did average low, and skipping the empty days
                        # would report it as though it were stocked all month. Days with no ROW at
                        # all are still excluded - that is "not reported", not "zero".
                        sel.append(f'ROUND(AVG(CASE WHEN {inw} THEN {m} END),4) AS "{alias}"')
                    else:
                        sel.append(f'ROUND(SUM(CASE WHEN {inw} THEN {m} END),4) AS "{alias}"')
            head = (f"{base._dt(anc)} AS DT, " if by_day else "") + (dim_csv + ", " if dims else "")
            rbranches.append(f"SELECT {head}{', '.join(sel)} FROM ref_daily"
                             + (f" GROUP BY {dim_csv}" if dims else ""))
        ref_block = " UNION ALL ".join(rbranches)

    # ---- stock: the position on each anchor day, then summed to the grain --------------------
    # The pick stays per (store, tag) even when the report does not group by store: a position
    # belongs to the day that store reported it, and only after picking is it safe to add up.
    stock = None
    if stock_cols:
        # The UNDERLYING KPIs, deduped: a split is a slice of a tag that is already in the fact,
        # and there is no KPI row stamped '0099_DC' for the pick to find. A RULE tag has no name
        # list at all and narrows by a prefix test instead - scan_filter returns whichever each
        # tag needs, ORed, so the pick reads exactly the rows the CASEs below can use.
        pick = base._stock_pick(anchors, where=base.scan_filter(defn["stock_tags"], "KPI"))
        # A PLANT-TYPE SPLIT NARROWS THE CASE, it does not add a scan. '0099_DC' reads the same
        # KPI as '0099' and simply also tests the row's plant type, so picking both costs one extra
        # conditional aggregate over rows already being read.
        #
        # UNLIKE the variant grid, this cannot be an outer re-projection: there the row IS a store,
        # so the split was the plain column passed through an IFF. Here the grain is whatever the
        # report groups by - group on MAJ_CAT alone and STORE / DC / HUB become three real columns
        # on one row, which is the reading that makes the split worth having.
        st_sel = []
        for t in defn["stock_tags"]:
            for m in STOCK_MEASURES:
                # base.tag_measure_sql, the same call the variant grid makes, so the two engines
                # cannot drift about what a tag's column IS. For every tag but a PICK it returns
                # exactly the conditional SUM written here before.
                #
                # multi_store=True ALWAYS, and not because the grain is unknown - it is knowable,
                # `dims` is right here. It is because the two spellings agree wherever the grain
                # carries a store (the members are disjoint by location), so branching on the dims
                # would buy a second formulation of the same number and one more thing to keep in
                # step. The union is the one that is also right when the grain does not.
                st_sel.append(
                    f'{base.tag_measure_sql(t, m, "f.KPI", defn.get("store_only_stock"), multi_store=True, dc_only=defn.get("dc_only_stock"), v2r_plants=defn.get("v2r_plants"), tag_plants=defn.get("tag_plants"), family_plants=defn.get("family_plants"))}'
                    f' AS "K_{t}__{m}"')
        st_cond = (" AND " + " AND ".join(conds)) if conds else ""
        stock = (f"SELECT p.ANCHOR_DT AS DT{', ' + dim_sel if dims else ''}, {', '.join(st_sel)} "
                 f"FROM {base.STOCK_TBL} f{joins} "
                 f"JOIN {pick} p ON p.ST_CD = f.ST_CD AND p.KPI = f.KPI AND p.DT = f.DT{st_cond} "
                 f"GROUP BY p.ANCHOR_DT{', ' + dim_grp if dims else ''}")

    # ---- sale plan: the Day Wise Plan's days, summed over the same windows ------------------
    #
    # Shaped exactly like the sale block - aggregate to (dimensions, CAL_DATE) first, then window
    # those daily totals - so the two are comparable cell for cell: P_TD__SALE_PLAN_V is the plan
    # for precisely the days S_TD__SALE_V actually sold in.
    plan_block, plan_null = None, []
    if plan_cols:
        # The plan cannot see below its own grain, and it must not GUESS. A dimension or a filter
        # it cannot resolve means the honest answer is nothing, not the category's whole plan
        # stamped onto each of its articles.
        dim_conds = defn.get("dim_filters") or []
        unresolved = ([c for c in dims if c not in PLAN_DIMS]
                      + [c for c in defn["filters"] if c not in PLAN_DIMS]
                      # A dimension CONDITION scopes the report exactly as a filter does, so one
                      # the plan cannot resolve has to blank the plan for the same reason: the
                      # plan would otherwise be the UNFILTERED plan sitting beside filtered sale.
                      + [f["col"] for f in dim_conds if f["col"] not in PLAN_DIMS])
        if unresolved:
            plan_null = [f'CAST(NULL AS FLOAT) AS "{c}"' for c in plan_cols]
        else:
            p_sel = ", ".join(f"{_plan_dim_expr(c)} AS {c}" for c in dims)
            p_grp = ", ".join(_plan_dim_expr(c) for c in dims)
            p_used = set(dims) | set(defn["filters"]) | {f["col"] for f in dim_conds}
            p_where = [f"f.SESSION_ID = {PLAN_SESSION_SQL}",
                       f"f.CAL_DATE BETWEEN {base._dt(scan_lo)} AND {base._dt(scan_hi)}"]
            for k, vals in defn["filters"].items():
                if not vals:
                    p_where.append("FALSE")
                else:
                    lits = ", ".join(base._q(str(v).strip().upper()) for v in vals)
                    p_where.append(
                        f"UPPER(TRIM(TO_VARCHAR({_plan_dim_expr(k)}))) IN ({lits})")
            # The same conditions against the PLAN's own columns. Every one of them resolves -
            # anything that did not is in `unresolved` above and blanked the block already.
            p_where += _dim_where(dim_conds, _plan_dim_expr, PLAN_DIMS)
            # One aggregate per picked measure, ALIASED AS THE MEASURE - so the window layer
            # below names the alias it wants instead of deciding by a _Q / _V suffix, which broke
            # the moment two measures shared a suffix.
            p_meas = []
            for m in defn["plan_measures"]:
                if m in PLAN_DAY_SRC:
                    p_meas.append(f"SUM(COALESCE(f.{PLAN_DAY_SRC[m]},0)) AS {m}")
                else:
                    p_meas.append(f"SUM(COALESCE(mp.{PLAN_MONTH_SRC[m]},0) "
                                  f"* COALESCE(f.FINAL_DAY_CONTRI,0)) AS {m}")
            p_from = f"{PLAN_TBL} f{_plan_joins(p_used)}"
            # The month plan is joined ONLY when something actually needs it, so a report that
            # picks the day-native pair alone builds the exact query it built before.
            m_cols = sorted({PLAN_MONTH_SRC[m] for m in defn["plan_measures"]
                             if m in PLAN_MONTH_SRC})
            if m_cols:
                on = " AND ".join(f"mp.{k} = f.{k}" for k in PLAN_KEY)
                # LEFT, so a day-wise key with no month row keeps its day and reads 0 rather than
                # vanishing and quietly shrinking the window's other columns.
                #
                # The month plan is narrowed to its session and to the columns actually picked
                # BEFORE the join rather than in the ON clause - the session is a scalar subquery
                # and belongs in a WHERE, and a derived table that projects seven keys plus what
                # was asked for is a far smaller build side than all ninety-eight columns.
                p_from += (f" LEFT JOIN (SELECT {', '.join(PLAN_KEY + m_cols)} "
                           f"FROM {PLAN_MONTH_TBL} "
                           f"WHERE SESSION_ID = {PLAN_SESSION_SQL}) mp ON {on}")
            plan_daily = (f"SELECT {p_sel + ', ' if dims else ''}f.CAL_DATE DT, "
                          + ", ".join(p_meas)
                          + f" FROM {p_from} "
                          f"WHERE {' AND '.join(p_where)} "
                          f"GROUP BY {p_grp + ', ' if dims else ''}f.CAL_DATE")
            pbranches = []
            for anc in anchors:
                sel = []
                for w in defn["windows"]:
                    lo, hi = wins_by_anchor[anc][w]
                    inw = f"DT BETWEEN {base._dt(lo)} AND {base._dt(hi)}"
                    for m in defn["plan_measures"]:
                        sel.append(f'ROUND(SUM(CASE WHEN {inw} THEN {m} END),4) '
                                   f'AS "P_{w}__{m}"')
                head = ((f"{base._dt(anc)} AS DT, " if by_day else "")
                        + (dim_csv + ", " if dims else ""))
                pbranches.append(f"SELECT {head}{', '.join(sel)} FROM plan_daily"
                                 + (f" GROUP BY {dim_csv}" if dims else ""))
            plan_block = " UNION ALL ".join(pbranches)

    # ---- month plan: the Plan Live 360 snapshot, read at the months the report picks ----------
    #
    # NOT windowed, and that is the whole point of the block. The Space, Transfer In and Purchase
    # plans exist once per month and have no day curve to spread along, so each column is one
    # measure of one MONTH - resolved per anchor, exactly as a window is, which is what keeps a
    # report grouped by DATE honest and what lets a published view re-resolve its own months.
    #
    # TWO HALVES, TWO GRAINS. The store snapshot carries Sale, Space and Transfer In at
    # ST_CD x MAJ_CAT; the RDC snapshot carries the Purchase plan at RDC_CD x MAJ_CAT and has no
    # store in it at all. They are built independently and each blanks on its own rule, so a report
    # grouped by store gets its transfer-in columns and honest blanks where purchase would be.
    month_toks = defn.get("plan_months") or []
    month_meas = defn.get("month_measures") or []
    mplan_ctes, mplan_parts, mplan_null = [], [], []

    def _month_half(tbl, own, ok_dims, meas, cte, join_store):
        """One half of the Month Plan block: (cte_sql, branch_sql, cols, null_sel).

        `own` is the columns this snapshot carries itself, `ok_dims` everything it can resolve.
        Exactly one of branch_sql and null_sel is ever non-empty - the half either builds or blanks.

        The shape is the Sale Plan block's, deliberately: aggregate to (dimensions, MONTH_ID) once,
        then pick each picked month out of that with a CASE - so N months cost one scan, not N.
        """
        cols_h = [f"M_{t}__{m}" for t in month_toks for m in meas]
        if not meas:
            return None, None, [], []
        # The snapshot cannot see below its own grain and must not GUESS. A dimension, a filter or
        # a condition it cannot resolve blanks this half - the alternative is the UNFILTERED plan
        # sitting beside filtered sale, or one category's plan repeated against each of its
        # articles, and both read as fact.
        dconds = defn.get("dim_filters") or []
        unresolved = ([c for c in dims if c not in ok_dims]
                      + [c for c in defn["filters"] if c not in ok_dims]
                      + [f["col"] for f in dconds if f["col"] not in ok_dims])
        if unresolved:
            return None, None, cols_h, [f'CAST(NULL AS FLOAT) AS "{c}"' for c in cols_h]

        def expr(c):
            return _mplan_dim_expr(c, own)

        h_sel = ", ".join(f"{expr(c)} AS {c}" for c in dims)
        h_grp = ", ".join(expr(c) for c in dims)
        # EVERY month this run can ask for, deduped by its SQL text. In view mode these are
        # expressions rather than numbers, so the IN list is what narrows a 3.7M-row snapshot to
        # the two or three months the report is actually about - and it re-narrows itself on every
        # read of the published view.
        ids, seen_id = [], set()
        for anc in anchors:
            for t in month_toks:
                mid = str(_month_id_expr(anc, t, view_mode))
                if mid not in seen_id:
                    seen_id.add(mid)
                    ids.append(mid)
        h_where = [f"f.MONTH_ID IN ({', '.join(ids)})"]
        for k, vals in defn["filters"].items():
            if not vals:
                h_where.append("FALSE")
            else:
                lits = ", ".join(base._q(str(v).strip().upper()) for v in vals)
                h_where.append(f"UPPER(TRIM(TO_VARCHAR({expr(k)}))) IN ({lits})")
        h_where += _dim_where(dconds, expr, ok_dims)
        # SUM across the dimension, AVG for the one measure that is a rate - see MPLAN_AVG. No
        # COALESCE anywhere: db-scripts/355 keeps "this plan has no row here" as NULL rather than
        # 0 on purpose, and turning it into a zero here would undo that one layer further on.
        h_agg = ", ".join(f"{'AVG' if m in MPLAN_AVG else 'SUM'}(f.{m}) AS {m}" for m in meas)
        joins_h = _plan_joins(set(dims) | set(defn["filters"])
                              | {f["col"] for f in dconds}) if join_store else ""
        cte_sql = (f"SELECT {h_sel + ', ' if dims else ''}f.MONTH_ID MID, {h_agg} "
                   f"FROM {tbl} f{joins_h} "
                   f"WHERE {' AND '.join(h_where)} "
                   f"GROUP BY {h_grp + ', ' if dims else ''}f.MONTH_ID")
        brs = []
        for anc in anchors:
            sel_a = []
            for t in month_toks:
                mid = _month_id_expr(anc, t, view_mode)
                for m in meas:
                    # One row per month per group reaches this, so the outer aggregate is only
                    # picking it out - SUM and AVG both return that single row's value, and both
                    # return NULL when the month has no row at all.
                    fn = "AVG" if m in MPLAN_AVG else "SUM"
                    sel_a.append(f'ROUND({fn}(CASE WHEN MID = {mid} THEN {m} END),4) '
                                 f'AS "M_{t}__{m}"')
            head = ((f"{base._dt(anc)} AS DT, " if by_day else "")
                    + (dim_csv + ", " if dims else ""))
            brs.append(f"SELECT {head}{', '.join(sel_a)} FROM {cte}"
                       + (f" GROUP BY {dim_csv}" if dims else ""))
        return cte_sql, " UNION ALL ".join(brs), cols_h, []

    if month_toks and month_meas:
        for tbl, own, ok, ms, cte, alias, jn in (
                (MPLAN_TBL, MPLAN_OWN_DIMS, MPLAN_DIMS,
                 [m for m in month_meas if m in MPLAN_STORE_MEASURES], "mplan_store", "m", True),
                (MPLAN_RDC_TBL, MPLAN_RDC_OWN_DIMS, MPLAN_RDC_DIMS,
                 [m for m in month_meas if m in MPLAN_RDC_MEASURES], "mplan_rdc", "n", False)):
            cte_sql, branch, cols_h, nulls = _month_half(tbl, own, ok, ms, cte, jn)
            if branch:
                mplan_ctes.append(f"{cte} AS ({cte_sql})")
                mplan_parts.append((alias, branch, cols_h))
            mplan_null += nulls

    # ---- GRC: the goods-receipt ledger, windowed exactly like sale ----------------------------
    #
    # Same two-step shape as the sale block - aggregate to (dimensions, DT) first, then apply the
    # windows to those daily group totals - so G_MTD__GRC_V is the receipt for precisely the days
    # S_MTD__SALE_V sold in, and GRC_DAYS counts days rather than multiplying one by the number of
    # articles in a category.
    #
    # The inner SELECT shapes a GRC row to look like a sale-fact row and aliases it `f`, so
    # _dim_expr, _joins and _filter_sql apply to it VERBATIM - the same trick the Day Ref block
    # uses, and for the same reason: one definition of what MAJ_CAT means, with no second copy to
    # drift out of step.
    grc_meas = defn.get("grc_measures") or []
    grc_daily = grc_block = None
    gp_pre = []
    if grc_cols:
        # WHAT TO CARRY PER DAY. GRC_DAYS is built by the window layer out of GRC_LINES, so
        # picking it alone still has to land the line count underneath - and picking both must not
        # land it twice.
        g_need = [m for m in GRC_MEASURES if m in grc_meas and m in GRC_DAY_AGG]
        if "GRC_DAYS" in grc_meas and GRC_DAYS_SRC not in g_need:
            g_need.append(GRC_DAYS_SRC)
        g_used = (set(dims) | set(defn["filters"])
                  | {f["col"] for f in (defn.get("dim_filters") or [])})
        g_art = bool(g_used & GP_ART_DIMS)
        if g_art:
            gp_pre.append(GP_ART_CTE)
        # THE SCAN IS THE PICKED WINDOWS' OWN ENVELOPE, not scan_lo..scan_hi. Those two span EVERY
        # window the anchor defines whether or not the report picked it, so they always reach back
        # through LYSP_SSN - fifteen months for a report asking only for TD and MTD. The sale block
        # pays that because its `daily` CTE is shared with other readers of the same anchors; this
        # block has one reader and can ask for exactly the days it will use.
        g_bounds = [v for anc in anchors for w in defn["windows"]
                    for v in wins_by_anchor[anc][w]]
        if view_mode:
            g_lo = base.SqlExpr("LEAST(" + ", ".join(str(v) for v in g_bounds) + ")")
            g_hi = base.SqlExpr("GREATEST(" + ", ".join(str(v) for v in g_bounds) + ")")
        else:
            g_lo, g_hi = min(g_bounds), max(g_bounds)
        g_row = (f"SELECT g.POSTING_DATE_DT DT, TRIM(g.STORE_CODE) ST_CD, "
                 f"TRY_TO_NUMBER(g.ARTICLE_NUMBER) ARTICLE_NUMBER, "
                 f"{_gp_fact_cols(g_art)}, "
                 # The view's OWN category is the fallback only - _dim_expr COALESCEs the live
                 # master over it, exactly as it does for the sale fact's frozen stamp. SEG the
                 # view has not got at all, so it is NULL and the master is the whole answer.
                 f"g.MAJOR_CATEGORY MAJ_CAT, CAST(NULL AS VARCHAR) SEG, "
                 f"g.GRC_QTY, g.GRC_VALUE "
                 f"FROM {GRC_TBL} g"
                 + _gp_art_join(g_art, "TRY_TO_NUMBER(g.ARTICLE_NUMBER)")
                 + f" WHERE g.POSTING_DATE_DT BETWEEN {base._dt(g_lo)} AND {base._dt(g_hi)}")
        g_agg = ", ".join(f"{GRC_DAY_AGG[m]} AS {m}" for m in g_need)
        grc_daily = (f"SELECT {dim_sel + ', ' if dims else ''}f.DT, {g_agg} "
                     f"FROM ({g_row}) f{joins} "
                     + ((" WHERE " + " AND ".join(conds)) if conds else "")
                     + f" GROUP BY {dim_grp + ', ' if dims else ''}f.DT")
        gbranches = []
        for anc in anchors:
            sel = []
            for w in defn["windows"]:
                lo, hi = wins_by_anchor[anc][w]
                inw = f"DT BETWEEN {base._dt(lo)} AND {base._dt(hi)}"
                for m in grc_meas:
                    alias = f"G_{w}__{m}"
                    if m == "GRC_DAYS":
                        sel.append(f'COUNT(CASE WHEN {inw} AND {GRC_DAYS_SRC} > 0 THEN 1 END) '
                                   f'AS "{alias}"')
                    else:
                        sel.append(f'ROUND(SUM(CASE WHEN {inw} THEN {m} END),4) AS "{alias}"')
            head = ((f"{base._dt(anc)} AS DT, " if by_day else "")
                    + (dim_csv + ", " if dims else ""))
            gbranches.append(f"SELECT {head}{', '.join(sel)} FROM grc_daily"
                             + (f" GROUP BY {dim_csv}" if dims else ""))
        grc_block = " UNION ALL ".join(gbranches)

    # ---- PO: the open order book, one aggregate and no axis -----------------------------------
    #
    # No daily rollup under this one, because there is no day in it: the view holds the lines that
    # are still pending AS OF NOW. So this is a single GROUP BY at the report's own grain, which is
    # also what makes PO_COUNT's COUNT(DISTINCT) safe here - there is no second aggregation step
    # for a distinct count to be taken across twice.
    #
    # BLANK WHEN THE REPORT GROUPS BY DATE. One live position stamped onto each of thirty days is
    # the same number told thirty times, and a reader totalling that column down the page would get
    # thirty times the open book. The Month Plan block blanks on the same principle when it cannot
    # resolve a grain; here the un-resolvable grain is always and only DATE.
    po_meas = defn.get("po_measures") or []
    po_block, po_null = None, []
    if po_cols:
        o_used = (set(dims) | set(defn["filters"])
                  | {f["col"] for f in (defn.get("dim_filters") or [])})
        # DATE IN ANY ROLE BLANKS THIS BLOCK, not only DATE in the grouping. The open book has no
        # day column to group by, to filter or to test a condition against - it is one position,
        # taken now - so `f.DT` would be an alias nothing on this row defines and the report would
        # fail to compile rather than return an honest blank. Same reasoning either way round: a
        # per-day reading of this view does not exist to be given.
        if by_day or "DT" in o_used:
            po_null = [f'CAST(NULL AS FLOAT) AS "{c}"' for c in po_cols]
        else:
            o_art = bool(o_used & GP_ART_DIMS)
            if o_art and GP_ART_CTE not in gp_pre:
                gp_pre.append(GP_ART_CTE)
            # ONE anchor by construction: by_day is the only thing that makes more than one, and
            # that branch blanked above. In view mode it is the month-end expression, so the
            # overdue test re-resolves itself on every read of the published view.
            anc = anchors[0]
            o_row = (f"SELECT TRIM(o.STORE_CODE) ST_CD, o.ARTICLE_NUMBER, "
                     f"{_gp_fact_cols(o_art)}, "
                     f"o.MAJOR_CATEGORY MAJ_CAT, CAST(NULL AS VARCHAR) SEG, "
                     f"o.PENDING_PO_QUANTITY, o.PENDING_PO_VALUE, o.ACTUAL_PO_QUANTITY, "
                     f"o.ACTUAL_PO_VALUE, o.GR_QTY, o.PO_NUMBER, o.EX_FAC_DT, o.PO_STATS "
                     f"FROM {PO_TBL} o"
                     + _gp_art_join(o_art, "o.ARTICLE_NUMBER"))
            # The three predicates the conditional measures test, resolved against this run's
            # anchor - an expression, not a date, in view mode. Built by base so the variant grid
            # and this one cannot come to disagree about what "overdue" or "released" means.
            o_pred = base.po_preds(base._dt(anc), lambda c: f"f.{c}")
            o_sel = []
            for m in po_meas:
                alias = f'"O_{PO_PERIOD}__{m}"'
                if m in PO_AGG:
                    o_sel.append(f"ROUND({PO_AGG[m]},4) AS {alias}")
                else:
                    col, which = PO_COND_AGG[m]
                    o_sel.append(f"ROUND(SUM(CASE WHEN {o_pred[which]} THEN {col} END),4) "
                                 f"AS {alias}")
            po_block = (f"SELECT {dim_sel + ', ' if dims else ''}{', '.join(o_sel)} "
                        f"FROM ({o_row}) f{joins} "
                        + ((" WHERE " + " AND ".join(conds)) if conds else "")
                        + (f" GROUP BY {dim_grp}" if dims else ""))

    # ---- assemble whichever blocks this report actually has -----------------------------------
    #
    # Three independent aggregates now - sale, stock and Day Ref - and any subset of them is a
    # legitimate report: stock-only is what an ARS_STOCK_REPORT wants, and sale plus Day Ref with
    # no tags is the common comparison. Written as a LIST rather than as branches because three
    # optional blocks are eight combinations, and eight hand-written branches is where one of them
    # ends up subtly different from the other seven.
    #
    # The CTEs are only emitted for blocks that exist: `daily` scans the sale fact across the
    # widest window and `ref_daily` the reference table, so leaving an unused one in the query
    # would pay for a read nobody reads.
    parts = [p for p in ((("k", sale, sale_cols)) if sale_cols else None,
                         (("t", stock, stock_cols)) if stock_cols else None,
                         (("g", ref_block, ref_cols)) if ref_cols else None,
                         (("q", plan_block, plan_cols)) if plan_block else None,
                         (("c", grc_block, grc_cols)) if grc_block else None,
                         # `b`, not `o` - the PO block's own inner scan already aliases the view
                         # `o`, and two `o`s one scope apart is exactly the sort of thing that
                         # compiles until the day somebody adds a correlated reference.
                         (("b", po_block, po_cols)) if po_block else None) if p] + mplan_parts
    # EVERY block blanked. A report whose ONLY measures are plan columns, grouped below the grain
    # the plan is made at, has nothing left to scan - there is no fact read to hang the dimensions
    # on and no vocabulary to invent rows from. It used to reach parts[0] and fail with an
    # IndexError; it fails here with the sentence that says what to change instead.
    if not parts:
        raise HTTPException(400, "Every column in this report is a plan column the plan cannot "
                                 "resolve at this grouping, so there is nothing to read. "
                                 f"{PLAN_NOTE if plan_cols else PO_NOTE if po_cols else MONTH_NOTE}")
    # ref_pre holds the Day Ref scaffolding (the calendar spine, its span, the article bridge and
    # the two source scans) and MUST precede ref_daily, which reads all four.
    # gp_pre holds the article bridge the GRC and PO blocks share, and it goes FIRST because both
    # of them read it. It is emitted at most once however many of the two asked for it, and not at
    # all unless a report actually groups, filters or conditions on one of the four variant
    # columns neither view carries.
    ctes = list(gp_pre) \
        + ([f"daily AS ({daily})"] if sale_cols else []) \
        + ((ref_pre + [f"ref_daily AS ({ref_daily})"]) if ref_cols else []) \
        + ([f"plan_daily AS ({plan_daily})"] if plan_block else []) \
        + ([f"grc_daily AS ({grc_daily})"] if grc_block else []) \
        + mplan_ctes
    with_sql = ("WITH " + ", ".join(ctes) + " ") if ctes else ""
    join_keys = keys if by_day else dims

    if not join_keys:
        # No dimensions at all: each block is one grand-total row, so a cross join is exactly right.
        sel = ", ".join([f'{a}."{c}"' for a, _s, cs in parts for c in cs]
                        + plan_null + mplan_null + po_null)
        frm = ", ".join(f"({s}) {a}" for a, s, _cs in parts)
        return _finish(f"{with_sql}SELECT {sel} FROM {frm}", keys, cols, defn, saved_conds)

    # FULL OUTER, not LEFT: only ~30% of keys carry both sale and stock, so either side alone would
    # drop a third of the report - the stock-no-sale and sale-no-stock populations. Day Ref joins
    # the same way and for the same reason, from the other direction: a store-category can have a
    # reference plan and no sale at all, and that row is exactly the one worth seeing.
    #
    # Each block after the first joins on the COALESCE of every block BEFORE it, not on the first
    # alone. With three blocks that is the difference between keeping and losing a key that stock
    # and Day Ref both have but sale does not - it would match neither side of a k-only predicate
    # and fall out of a report built to surface it.
    #
    # EQUAL_NULL, not `=`: a key column that is NULL on both sides (MERGE_VENDOR_CODE is NULL on
    # ~99% of articles) never matches under `=`, so the FULL OUTER JOIN kept the sale half and the
    # stock half as two rows that looked identical after the COALESCE - DC-GEN-CLR showed 163k
    # generic articles twice (e.g. 1110008198: one stock-only row, one sale-only row).
    alias0 = parts[0][0]
    frm = f"({parts[0][1]}) {alias0}"
    prior = [alias0]
    for a, s, _cs in parts[1:]:
        on = " AND ".join(f"EQUAL_NULL({_coal(prior, c)}, {a}.{c})" for c in join_keys)
        frm += f" FULL OUTER JOIN ({s}) {a} ON {on}"
        prior.append(a)
    sel = ", ".join([f"{_coal([a for a, _s, _cs in parts], c)} AS {c}" for c in join_keys]
                    + [f'{a}."{c}"' for a, _s, cs in parts for c in cs] + plan_null + mplan_null + po_null)
    return _finish(f"{with_sql}SELECT {sel} FROM {frm}", keys, cols, defn, saved_conds)


# ══════════════════════════════════════════════════════════════════════════════
# catalogue - what a report can be built from
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/catalogue")
def mis_catalogue():
    """Everything the builder needs to render its pickers.

    Dimensions are grouped by where they come from, because a flat list of 160 names is unusable:
    a person hunting for CLUSTER thinks "store attribute", not "column 47".
    """
    months, snapshot = _month_catalogue()
    return {
        "dims": {
            "Fact": FACT_DIMS + ["GEN_ART_DESC"],
            "Store master": list(base.STORE_ATTRS) + [base.PLANT_COL],
            "Product master": PROD_DIMS,
        },
        "windows": WINDOWS,
        "measures": SALE_MEASURES,
        "planMeasures": PLAN_MEASURES,
        "planDims": sorted(PLAN_DIMS),
        "planNote": PLAN_NOTE,
        "stockTags": ALL_STOCK_TAGS,
        "composites": {c: base.COMPOSITE_TAGS[c] for c in base.COMPOSITE_TAGS},
        "rules": list(base.RULE_TAGS),
        # PICKS, with their members in order. The page names them in the header note: a pick is
        # the same rack under a different storage type per DC, so "which tags am I actually
        # reading" is the one question the column cannot answer about itself.
        "picks": {p: base.PICK_TAGS[p] for p in base.PICK_TAGS},
        # THE PLANTS UNDER V2R, each with what the master types it and whether it still trades -
        # so the picker can show "DH24 · DC" beside "DP01 · HUB" and offer "the DCs" as one click.
        # Read from the FACT (which plants actually carry V2R stock) joined to the master, never
        # from a list here: a warehouse is reorganised more often than this file is edited.
        "v2rPlants": base.v2r_plants_catalogue(),
        # EVERY PLANT, for the stock-plant pickers. Shipped here rather than fetched per picker:
        # it is one cached read of a 795-row master, and a menu that has to go to Snowflake before
        # it can draw itself is a menu somebody waits for every time they open one.
        "plants": base.all_plants_catalogue(),
        # WHICH PLANT FAMILY EACH TAG BELONGS TO. The builder groups its plant pickers by this, and
        # reading it off the catalogue is what stops a second copy of the prefix rules (ET vs
        # SDC / V2R / V2S vs LQ / LQL / LQB ...) living in JavaScript and drifting from the one in
        # base.plant_family that actually writes the SQL.
        "tagFamily": {t: base.plant_family(t) for t in ALL_STOCK_TAGS if base.plant_family(t)},
        "refMeasures": REF_MEASURES,
        # MONTH PLAN. The measure list is grouped by PLAN rather than flat - a reader hunting for
        # "Bgt Disp Cl Qty" thinks "transfer-in", not "the eighth of twenty-four".
        "monthMeasures": MONTH_MEASURE_GROUPS,
        # The month axis: the five relative tokens, then whatever the snapshot holds. `offset` is
        # carried so the builder can say what a token resolves to for the dates on screen without
        # re-deriving the arithmetic.
        "monthTokens": [{"id": t, "offset": MONTH_OFFSETS[t]} for t in MONTH_TOKENS],
        "months": months,
        "monthSnapshot": snapshot,
        "monthDims": sorted(MPLAN_DIMS),
        "monthRdcDims": sorted(MPLAN_RDC_DIMS),
        "monthRdcMeasures": list(MPLAN_RDC_MEASURES),
        "monthNote": MONTH_NOTE,
        # GRC rides the WINDOWS picker, so it ships a flat measure list and nothing else. PO rides
        # no axis at all, which is the one thing about it a reader has to be told - hence the note.
        "grcMeasures": GRC_MEASURES,
        "grcNote": GRC_NOTE,
        "poMeasures": PO_MEASURES,
        "poNote": PO_NOTE,
        # The PO block's single period, so the page can label its row in the period layout with
        # the same string the SQL spells rather than a second copy of it in JavaScript.
        "poPeriod": PO_PERIOD,
    }


# ══════════════════════════════════════════════════════════════════════════════
# definitions - list / save / delete
# ══════════════════════════════════════════════════════════════════════════════
class SaveReq(BaseModel):
    report_id: Optional[str] = None      # absent = create
    name: str
    slug: Optional[str] = None
    description: Optional[str] = None
    definition: dict
    is_shared: bool = False
    # Whether it gets a tab under MIS. Separate from is_shared: one decides who may see it, the
    # other where it is listed.
    show_in_sidebar: bool = True
    user: Optional[str] = None


def _slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")[:60]
    return s or "report"


def _who(u):
    # V2Pulse: the signed-in employee code (pulse.mis_engine.CURRENT_USER), not the browser's value
    from .. import who
    return who(u)


def _row_to_def(r):
    raw = r[4]
    return {"reportId": r[0], "slug": r[1], "name": r[2], "description": r[3],
            # Migrated on the way out too, so the editor shows a renamed condition rather than a
            # row it cannot match to its catalogue. The stored JSON is untouched until the next save.
            "definition": _migrate_def(json.loads(raw) if isinstance(raw, str) else (raw or {})),
            "isShared": bool(r[5]), "createdBy": r[6],
            "createdAt": base._fmt_str(r[7]), "updatedAt": base._fmt_str(r[8]),
            # Derived, not stored: _view_name is the ONE place the slug -> identifier rule lives
            # (dashes and every other non-identifier char to underscores, upper, capped at 100).
            # The UI shows this so you can paste it straight into a worksheet, and deriving it here
            # rather than in JS keeps the app's label and the view that actually exists from drifting.
            # Best-effort publish means the view may not exist yet; the name is still what it WOULD be.
            "view": _view_name(r[1]),
            # Defaults TRUE for a row written before the column existed.
            "showInSidebar": True if len(r) < 10 or r[9] is None else bool(r[9])}


@router.get("/reports")
def mis_list(user: Optional[str] = None):
    """Shared reports, plus this user's own private ones."""
    me = _who(user)
    conn = base._conn()
    try:
        cur = conn.cursor()
        cur.execute(f"""SELECT REPORT_ID, SLUG, NAME, DESCRIPTION, TO_VARCHAR(DEF_JSON),
                               IS_SHARED, CREATED_BY, CREATED_AT, UPDATED_AT, SHOW_IN_SIDEBAR
                          FROM {DEF_TBL}
                         WHERE IS_DELETED = FALSE
                           AND (IS_SHARED = TRUE OR CREATED_BY = {base._q(me)})
                         ORDER BY NAME""")
        return {"reports": [_row_to_def(r) for r in cur.fetchall()]}
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# Every saved report is also published as a SILVER view
#
# WHY A VIEW AND NOT A TABLE. A table would be a snapshot, and a snapshot of a report whose whole
# premise is "never drifts from its source" is a contradiction - it is exactly what the RPT_* tables
# in MIS_REPORTS already are. A view stores only the SQL, costs nothing until read, and answers from
# the live facts every time.
#
# WHY THE ANCHOR IS AN EXPRESSION. A view takes no parameters. Baking today's date in would freeze
# the view on its creation day, so the anchor is SQL - the last day of the previous month, the
# month-end close - and the view re-resolves it on every read. It therefore rolls over by itself on
# the 1st of each month with nothing scheduled and nothing to forget.
#
# BEST EFFORT, DELIBERATELY. A view that fails to publish must not fail the save: the definition is
# the thing of record, and the report goes on working through the app either way. Failures are logged
# and returned in `view` so the caller can see them; they are never raised.
VIEW_SCHEMA = "V2RETAIL.SILVER"


def _view_name(slug):
    """VW_MIS_<slug>, with dashes to underscores - a slug is URL-shaped, an identifier is not."""
    ident = re.sub(r"[^A-Za-z0-9_]", "_", str(slug or "").strip().lower()).upper()[:100]
    return f"{VIEW_SCHEMA}.VW_MIS_{ident}"


def _view_sql(defn):
    """The report's SQL with a self-resolving month-end anchor, and its saved conditions.

    The conditions have to be in here too. `filters` already were - build_sql puts them in the
    inner scan - but col_filters were not, so a report narrowed to seven stores published a view
    holding every store, and the two disagreed about what the report meant. A view is the one copy
    nobody re-runs to check.
    """
    sql, _keys, cols = build_sql(defn, None, None, anchor_sql=base.MONTH_END_ANCHOR_SQL)
    where = _col_where_sql(cols, defn.get("col_filters"))
    return f"SELECT * FROM ({sql}){where}" if where else sql


def _sync_view(cur, slug, defn, old_slug=None):
    """CREATE OR REPLACE the report's view, dropping the previous name after a rename.

    The drop comes SECOND on purpose: if the create fails, the old view is still standing and still
    correct, rather than the rename having deleted the only working copy.
    """
    name = _view_name(slug)
    try:
        cur.execute(f"CREATE OR REPLACE VIEW {name} AS {_view_sql(defn)}")
        cur.execute(f"COMMENT ON VIEW {name} IS "
                    f"{base._q('Auto-published from BRONZE.MIS_REPORT_DEF slug ' + str(slug) + '. Anchored on the LAST DAY OF THE PREVIOUS MONTH, resolved at read time, so it rolls over by itself. Edit the report in the app - do not ALTER this view, it is replaced on every save.')}")
        if old_slug and str(old_slug).lower() != str(slug).lower():
            try:
                cur.execute(f"DROP VIEW IF EXISTS {_view_name(old_slug)}")
            except Exception as e:
                logger.warning("[mis-report] view rename left %s behind: %s", old_slug, e)
        logger.info("[mis-report] published view %s", name)
        return {"view": name, "ok": True}
    except Exception as e:
        logger.warning("[mis-report] could not publish view %s: %s", name, e)
        return {"view": name, "ok": False, "error": str(e)[:300]}


def _drop_view(cur, slug):
    name = _view_name(slug)
    try:
        cur.execute(f"DROP VIEW IF EXISTS {name}")
        logger.info("[mis-report] dropped view %s", name)
        return {"view": name, "ok": True}
    except Exception as e:
        logger.warning("[mis-report] could not drop view %s: %s", name, e)
        return {"view": name, "ok": False, "error": str(e)[:300]}


@router.post("/reports")
def mis_save(req: SaveReq):
    name = str(req.name or "").strip()[:120]
    if not name:
        raise HTTPException(400, "name is required")
    defn = _clean_def(req.definition)
    me = _who(req.user)
    slug = str(req.slug or "").strip().lower() or _slugify(name)
    if not _SLUG_RE.match(slug):
        raise HTTPException(400, f"the URL must be lowercase letters, digits and dashes (got '{slug}')")

    conn = base._conn()
    try:
        cur = conn.cursor()
        rid = str(req.report_id or "").strip()
        if rid and not _ID_RE.match(rid):
            raise HTTPException(400, "bad report_id")

        # The slug is the URL, so it must be unique among the living. Checked here because
        # Snowflake has no unique constraint to lean on.
        cur.execute(f"SELECT REPORT_ID FROM {DEF_TBL} WHERE IS_DELETED = FALSE "
                    f"AND SLUG = {base._q(slug)}")
        clash = [r[0] for r in cur.fetchall() if r[0] != rid]
        if clash:
            raise HTTPException(400, f"a report with the URL '{slug}' already exists")

        payload = base._q(json.dumps(defn))
        shared = "TRUE" if req.is_shared else "FALSE"
        in_bar = "TRUE" if req.show_in_sidebar else "FALSE"
        if rid:
            # Only the creator may edit. Without this, one person's saved definition could be
            # rewritten under another's name and nobody would know which numbers moved.
            cur.execute(f"SELECT CREATED_BY, SLUG FROM {DEF_TBL} WHERE REPORT_ID = {base._q(rid)} "
                        f"AND IS_DELETED = FALSE")
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "report not found")
            old_slug = row[1]
            if row[0] != me:
                raise HTTPException(403, f"'{row[0]}' created this report; only they can change it")
            cur.execute(f"""UPDATE {DEF_TBL}
                               SET NAME = {base._q(name)}, SLUG = {base._q(slug)},
                                   DESCRIPTION = {base._q(str(req.description or '')[:1000])},
                                   DEF_JSON = PARSE_JSON({payload}),
                                   IS_SHARED = {shared}, SHOW_IN_SIDEBAR = {in_bar},
                                   UPDATED_BY = {base._q(me)}, UPDATED_AT = CURRENT_TIMESTAMP()
                             WHERE REPORT_ID = {base._q(rid)}""")
        else:
            old_slug = None
            rid = uuid.uuid4().hex[:32]
            cur.execute(f"""INSERT INTO {DEF_TBL}
                              (REPORT_ID, SLUG, NAME, DESCRIPTION, DEF_JSON, IS_SHARED,
                               SHOW_IN_SIDEBAR, CREATED_BY)
                            SELECT {base._q(rid)}, {base._q(slug)}, {base._q(name)},
                                   {base._q(str(req.description or '')[:1000])},
                                   PARSE_JSON({payload}), {shared}, {in_bar}, {base._q(me)}""")
        logger.info("[mis-report] saved %s (%s) by %s", name, slug, me)
        view = _sync_view(cur, slug, defn, old_slug)
        return {"reportId": rid, "slug": slug, "name": name, "definition": defn, **view}
    finally:
        conn.close()


@router.delete("/reports/{report_id}")
def mis_delete(report_id: str, user: Optional[str] = None):
    if not _ID_RE.match(report_id):
        raise HTTPException(400, "bad report_id")
    me = _who(user)
    conn = base._conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT CREATED_BY, SLUG FROM {DEF_TBL} WHERE REPORT_ID = {base._q(report_id)} "
                    f"AND IS_DELETED = FALSE")
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "report not found")
        if row[0] != me:
            raise HTTPException(403, f"'{row[0]}' created this report; only they can delete it")
        # Soft delete: a shared report someone has bookmarked should stop appearing without its
        # definition evaporating, and a mis-click should be recoverable.
        cur.execute(f"UPDATE {DEF_TBL} SET IS_DELETED = TRUE, UPDATED_BY = {base._q(me)}, "
                    f"UPDATED_AT = CURRENT_TIMESTAMP() WHERE REPORT_ID = {base._q(report_id)}")
        # The VIEW is dropped for real, though. A soft-deleted report vanishes from the app, so a
        # view still answering under its name would be a dataset nobody can see the definition of -
        # and re-saving the report republishes it, so nothing is lost that a restore would want.
        view = _drop_view(cur, row[1])
        return {"deleted": report_id, **view}
    finally:
        conn.close()


@router.get("/defs")
def mis_defs(slugs: str = ""):
    """Name + definition for a list of slugs, in the order asked, by URL rather than by owner.

    The multi-report workbook needs each report's NAME (the sheet tab) and GROUP-BY (the columns
    that stay text, so a plant code 0001 is not written as the number 1) before it streams a row.
    /run with limit 1 would give both, but at the price of a full Snowflake aggregation per report
    just to read a definition; /reports filters by owner, and a schedule has no user. This is one
    read of the definition table. An unknown slug is simply absent - the caller reports it."""
    want = [s.strip().lower() for s in str(slugs or "").split(",") if s.strip()][:100]
    if not want:
        return {"reports": []}
    conn = base._conn()
    try:
        cur = conn.cursor()
        cur.execute(f"""SELECT REPORT_ID, SLUG, NAME, DESCRIPTION, TO_VARCHAR(DEF_JSON),
                               IS_SHARED, CREATED_BY, CREATED_AT, UPDATED_AT, SHOW_IN_SIDEBAR
                          FROM {DEF_TBL}
                         WHERE IS_DELETED = FALSE
                           AND SLUG IN ({", ".join(base._q(s) for s in want)})""")
        got = {r[1]: _row_to_def(r) for r in cur.fetchall()}
        return {"reports": [got[s] for s in want if s in got]}
    finally:
        conn.close()


def _load_def(cur, slug):
    cur.execute(f"""SELECT REPORT_ID, SLUG, NAME, DESCRIPTION, TO_VARCHAR(DEF_JSON),
                           IS_SHARED, CREATED_BY, CREATED_AT, UPDATED_AT, SHOW_IN_SIDEBAR
                      FROM {DEF_TBL} WHERE IS_DELETED = FALSE AND SLUG = {base._q(slug)}""")
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"no report with the URL '{slug}'")
    return _row_to_def(row)


class DatedSqlReq(BaseModel):
    slug: str
    as_of: str


@router.post("/dated-sql")
def mis_dated_sql(req: DatedSqlReq):
    """The report's VIEW shape, but for ONE chosen day instead of the self-resolving month-end.

    For planning-service's AKS Master Data snapshots (routes/akash/varStockSnap.js), which let a
    person pick the as-of date and store a copy per date. Same shape the view has - wide, DT dropped
    from the grouping, the saved conditions applied - so a dated copy and the view's copy hold the
    same columns. Only the SQL is returned; the caller runs its own CTAS on its own warehouse.
    """
    conn = base._conn()
    try:
        cur = conn.cursor()
        d = _load_def(cur, str(req.slug).strip().lower())
    finally:
        conn.close()
    defn = _clean_def(d["definition"])
    defn = {**defn, "group_by": [c for c in defn["group_by"] if c != "DT"], "layout": "horizontal"}
    sql, _keys, cols = build_sql(defn, req.as_of, req.as_of)
    where = _col_where_sql(cols, defn.get("col_filters"))
    return {"name": d.get("name"), "columns": cols,
            "sql": f"SELECT * FROM ({sql}){where}" if where else sql}


# ══════════════════════════════════════════════════════════════════════════════
# run
# ══════════════════════════════════════════════════════════════════════════════
class RunReq(BaseModel):
    slug: Optional[str] = None
    definition: Optional[dict] = None     # unsaved, for the builder's live preview
    # Chosen on the report page for this run only - one store and all its maj-cats, one maj-cat
    # across every store, a handful of stores. NEVER written back to the saved definition: a
    # filter someone applied to look at one store must not silently become what the report means
    # for everyone else, or for the schedules that dump it.
    filters: Optional[dict] = None
    # /values only: which column to list. Its own field rather than smuggled through filters,
    # which would read as "filter on this" and mean the opposite.
    column: Optional[str] = None
    # /values only: several columns at once, for a COMBINATION split. Returns the pairs that
    # actually occur - ST_CD + MAJ_CAT is the maj-cats each store really sold, not every store
    # crossed with every maj-cat, which would ask for thousands of empty files.
    columns: Optional[List[str]] = None
    from_date: str
    to_date: str
    page: int = 1
    limit: int = 200
    sort_col: Optional[str] = None
    sort_dir: Optional[str] = None
    # The grid header's Excel-style conditions - [{col, op, v1, v2}] - for THIS run only, same as
    # `filters`. A ">= 100" on a measure has to be applied AFTER the aggregation, so these are a
    # separate field rather than more entries in `filters`, which the engine puts in the inner scan.
    col_filters: Optional[List[dict]] = None
    # "horizontal" | "vertical" | "period", for THIS run only - the toggle above the grid. Never
    # written back to the saved definition, exactly like `filters`: somebody turning a report on
    # its side to read it must not change what that report means for everyone else, or for the
    # schedule that dumps it overnight. The report's own saved layout is what it opens as.
    layout: Optional[str] = None


def _resolve(cur, req):
    """The definition to run: an unsaved one, or a saved one plus this run's own filters."""
    def _lay(defn):
        # An unrecognised value is ignored rather than defaulted, so a stale client cannot flip a
        # vertical report back to wide by sending "" - only an explicit choice moves it.
        v = str(req.layout or "").strip().lower()
        return {**defn, "layout": v} if v in LAYOUTS else defn

    if req.definition is not None:
        return {"name": "Preview", "definition": _lay(_clean_def(req.definition))}
    if not req.slug:
        raise HTTPException(400, "either slug or definition is required")
    d = _load_def(cur, str(req.slug).strip().lower())
    defn = _clean_def(d["definition"])
    if req.filters:
        # The run's filters go THROUGH _clean_def, so a column outside the catalogue is dropped
        # rather than reaching SQL - the same guarantee a saved definition gets.
        # ref_measures and plan_measures ride along even though only ["filters"] is read back:
        # _clean_def rejects a definition with no measures of any kind, so a REF-ONLY or
        # PLAN-ONLY report (windows plus one of those lists, no sale measures, no tags) would
        # fail this probe on the way to being filtered.
        extra = _clean_def({"group_by": defn["group_by"], "windows": defn["windows"],
                            "measures": defn["measures"], "stock_tags": defn["stock_tags"],
                            "ref_measures": defn.get("ref_measures") or [],
                            "plan_measures": defn.get("plan_measures") or [],
                            "plan_months": defn.get("plan_months") or [],
                            "month_measures": defn.get("month_measures") or [],
                            "grc_measures": defn.get("grc_measures") or [],
                            "po_measures": defn.get("po_measures") or [],
                            "filters": req.filters})["filters"]
        # A saved filter and a run filter on the same column INTERSECT rather than replace: the
        # report's own scope is part of what it means, and a viewer must not be able to widen it.
        merged = dict(defn["filters"])
        for k, v in extra.items():
            merged[k] = ([x for x in v if x in set(merged[k])] if k in merged else v)
        defn = {**defn, "filters": merged}
    d["definition"] = _lay(defn)
    return d


def _cancel_conn():
    """The session used to CANCEL a query, never to run one.

    Deliberately the READ warehouse. A cancel issued on the warehouse that is jammed would
    queue behind the very pile-up it was sent to clear - which is the one way this could make
    things worse instead of better.
    """
    return get_sf_read_connection()


def _mis_values_body(req: RunReq, guard):
    """The distinct values of one column - or the distinct COMBINATIONS of several.

    Asks the engine for a throwaway definition grouped on those columns alone - one row per value
    or per combination, which is cheap - rather than pulling the whole report and reducing it
    client-side. `columns` drives a combination split: the pairs returned are the ones that occur
    in the data, so a caller writing one file per pair never asks for a file that would be empty.
    """
    want = req.columns if req.columns else [req.column]
    cols_in = []
    for c in (want or []):
        c = str(c or "").strip().upper()
        if c not in DIM_COLS:
            raise HTTPException(400, f"'{c}' is not a column this report can filter on")
        if c not in cols_in:
            cols_in.append(c)
    if not cols_in:
        raise HTTPException(400, "column (or columns) is required")
    conn = base._conn()
    try:
        cur = conn.cursor()
        # `filters` travels into _resolve here on purpose: the folder dump asks for the level
        # combinations of an ALREADY-scoped definition, and combinations from outside that scope
        # would each cost an empty file. The report page's menu sends none, so its tick-lists
        # stay complete.
        meta = _resolve(cur, RunReq(slug=req.slug, definition=req.definition,
                                    filters=req.filters,
                                    from_date=req.from_date, to_date=req.to_date))
        defn = meta["definition"]
        # The definition's own SCOPE is kept, unlike its columns: a report scoped to one division
        # must not offer values from outside it, and a split that planned one would only write files
        # the export then comes back empty from. What is deliberately NOT applied is the per-run
        # header filter - a tick-list that hid the values you had just unticked would leave no way
        # to tick them back on, so callers that want that narrowing send the filters themselves.
        #
        # Scope is BOTH of the definition's condition fields. `filters` alone was not enough: the
        # report editor writes "ST_CD Equals HB05" as a col_filter, so a report narrowed to seven
        # stores still offered all 600 in its tick-list - the grid was filtered and the menu above
        # it was not, which reads as the conditions having been ignored.
        #
        # Only conditions on DIMENSIONS can be honoured. This probe is grouped on the picked columns
        # with one throwaway measure, so a condition on S_MTD__SALE_V names a column it does not
        # produce and _col_where drops it - the tick-list is then a little wider than the result,
        # which is the safe direction to be wrong in.
        scope = [f for f in (defn.get("col_filters") or [])
                 if str(f.get("col") or "").strip().upper() in DIM_COLS]
        # Their columns join the grouping, or _col_where would drop them for the same reason.
        grp = list(cols_in)
        for f in scope:
            c = str(f["col"]).strip().upper()
            if c not in grp:
                grp.append(c)
        # A DISTINCT over dimension values only, so every measure block is stripped - Day Ref, the
        # sale plan and the month plan would each add a scan that is paid for and thrown away.
        # HORIZONTAL whatever the report displays as: turning it on its side would multiply every
        # value by the measure count and then throw the duplicates away - and `scope` names wide
        # columns, which _col_where would drop.
        #
        # WHICH FACT IS SCANNED FOLLOWS THE REPORT, and that is why there can be two probes. The
        # universe used to be the SALE fact alone, so a value with stock and no sale could not be
        # offered: the 15xx article types (DIVISION FA/CO - fixtures, UPS units, pamphlets, screws)
        # are stocked in every store and have never been billed once, so a stock report's ART TYPE
        # panel showed 41 of the master's 95 and silently hid them. The report itself WOULD have
        # returned those rows - the blocks are stitched with FULL OUTER JOIN precisely so the
        # stock-no-sale population survives - so the menu was narrower than its own query.
        #
        # Sale measures therefore ask for the sale probe, stock tags for the stock probe, and a
        # report with both gets both, UNIONed: the dimension-side reading of that FULL OUTER. A
        # definition with neither - a new report, or one carrying only plan columns - keeps the
        # sale probe, which is what it has always done.
        tags = [t for t in (defn.get("stock_tags") or []) if t in ALL_STOCK_TAGS]
        shapes = []
        if defn.get("measures") or not tags:
            shapes.append({"windows": ["TD"], "measures": ["SALE_V"], "stock_tags": []})
        if tags:
            # No windows and no measures IS the stock-only shape, which _clean_def allows for this
            # exact reason - build_sql then emits the stock block and no `daily` CTE at all. ALL
            # the tags, not the first: they are one scan of the stock fact under a KPI filter, so
            # narrowing to one would drop values and save nothing.
            shapes.append({"windows": [], "measures": [], "stock_tags": tags})
        sel = ", ".join(f'"{c}"' for c in cols_in)
        order = ", ".join(str(i + 1) for i in range(len(cols_in)))
        selects = []
        for shape in shapes:
            probe = {**defn, "group_by": grp, "ref_measures": [], "plan_measures": [],
                     "plan_months": [], "month_measures": [],
                     # GRC and PO are stripped for the same reason every other block is: this
                     # probe asks which VALUES a dimension takes, and the answer must not depend
                     # on which measure blocks the report happens to carry - a PO-only report
                     # would otherwise offer only the plants that have an open order.
                     "grc_measures": [], "po_measures": [],
                     "filters": defn.get("filters") or {}, "layout": "horizontal", **shape}
            sql, _keys, cols = build_sql(_clean_def(probe), req.from_date, req.to_date)
            selects.append(f"SELECT DISTINCT {sel} FROM ({sql}){_col_where_sql(cols, scope)}")
        # UNION, not UNION ALL: the two probes overlap on everything that both sold and stocked.
        # Wrapped before the ORDER BY so the sort reads the whole union rather than binding to its
        # last leg.
        guard.execute(cur, f"SELECT * FROM ({' UNION '.join(selects)}) ORDER BY {order}")
        rows = [[base._fmt_str(v) for v in r] for r in cur.fetchall()]
        # `column`/`values` stay for the single-column callers (the filter pickers); `combos` is
        # the shape a multi-column split reads.
        out = {"columns": cols_in, "combos": rows}
        if len(cols_in) == 1:
            out.update({"column": cols_in[0], "values": [r[0] for r in rows]})
        return out
    finally:
        conn.close()


@router.post("/values")
async def mis_values(req: RunReq, request: Request):
    """See _mis_values_body. Async only so the disconnect can be watched while the body
    blocks in a worker thread - see app/sf_cancel.py for why that matters."""
    return await sf_cancel.guarded(
        request, lambda g: _mis_values_body(req, g), _cancel_conn, label="mis/values")


def _mis_run_body(req: RunReq, guard):
    limit = max(1, min(int(req.limit or 200), MAX_LIMIT))
    page = max(1, int(req.page or 1))
    conn = base._conn()
    try:
        cur = conn.cursor()
        meta = _resolve(cur, req)
        defn = meta["definition"]
        sql, keys, cols = build_sql(defn, req.from_date, req.to_date,
                                    saved_conds=defn.get("col_filters"))
        where = _col_where_sql(cols, _outer_conds(defn, req.col_filters))

        # EITHER TURN projects the column list by name so that "_ORD" can sort the result without
        # appearing in it. HORIZONTAL keeps SELECT * - `cols` lists the grouping in the order it
        # was PICKED while the query emits DT first, so naming them here would quietly reorder
        # every existing report's columns.
        turned = _is_turned(defn)
        sel = ", ".join(f'"{c}"' for c in cols) if turned else "*"
        # Measures stay in catalogue order within each group, whatever the grid is sorted on - a
        # sort that scattered Sale V, Sale Q and GM V through each other would be unreadable, and
        # so would one that scattered TD, MTD and QTR.
        tail = ', "_ORD"' if turned else ""

        order = ""
        sc = str(req.sort_col or "")
        if sc in cols:
            direction = "DESC" if str(req.sort_dir or "").lower() == "desc" else "ASC"
            order = f' ORDER BY "{sc}" {direction} NULLS LAST{tail}'
        elif keys:
            order = " ORDER BY " + ", ".join(keys) + tail
        elif turned:
            order = ' ORDER BY "_ORD"'

        # The count is taken THROUGH the same WHERE, so the row total and the page numbering agree
        # with what the filter left - a count of the unfiltered result would page into emptiness.
        guard.execute(cur, f"SELECT COUNT(*) FROM ({sql}){where}")
        total = int(cur.fetchone()[0] or 0)
        guard.execute(cur, f"SELECT {sel} FROM ({sql}){where}{order} "
                           f"LIMIT {limit} OFFSET {(page - 1) * limit}")
        out_cols = [c[0] for c in cur.description]
        rows = [[base._fmt_str(v) for v in r] for r in cur.fetchall()]
        return {"name": meta["name"], "columns": out_cols, "keys": keys,
                "rows": rows, "total": total, "page": page, "limit": limit,
                # The grid formats a TURNED cell from the ROW, not from its column header - one
                # "TD" column holds a value, a quantity and a day count depending on which row it
                # is, and so does one "QTY" column - so it has to be told which shape came back.
                "layout": _layout_of(defn),
                # ... and for the period turn, WHICH wide column each cell came from. The vertical
                # turn needs no equivalent: its two label columns carry the block and the measure
                # on every row, so the browser can rebuild that itself.
                "period": _period_meta(defn) if _is_period(defn) else None,
                "definition": defn}
    finally:
        conn.close()


@router.post("/run")
async def mis_run(req: RunReq, request: Request):
    """Run a saved report.

    THE RE-FIRE THAT MADE THIS ASYNC. The page reruns on every filter, sort, page and date
    change and aborts the previous fetch client-side; without the guard below, each abandoned
    run left its COUNT(*) and its page query executing on the compute warehouse. 84 of them
    in an hour on 2026-09-14 took AKS_WH_L to 40 queued and stalled an unrelated bulk upload
    for 512 seconds. See app/sf_cancel.py.
    """
    return await sf_cancel.guarded(
        request, lambda g: _mis_run_body(req, g), _cancel_conn, label="mis/run")


@router.post("/export")
def mis_export(req: RunReq):
    conn = base._conn()
    cur = conn.cursor()
    meta = _resolve(cur, req)
    defn = meta["definition"]
    sql, keys, cols = build_sql(defn, req.from_date, req.to_date,
                                saved_conds=defn.get("col_filters"))
    # Same conditions the grid is showing: an export that quietly dropped them would hand back
    # more rows than the page it was clicked from.
    where = _col_where_sql(cols, _outer_conds(defn, req.col_filters))
    turned = _is_turned(defn)
    sel = ", ".join(f'"{c}"' for c in cols) if turned else "*"
    tail = ', "_ORD"' if turned else ""
    if keys:
        order = " ORDER BY " + ", ".join(keys) + tail
    elif turned:
        order = ' ORDER BY "_ORD"'
    else:
        order = ""
    cur.execute(f"SELECT {sel} FROM ({sql}){where}{order}")
    # RAW column names, matching the grid and the base report's own exports.
    hdr = [c[0] for c in cur.description]
    a, b = base._range(req.from_date, req.to_date)
    # The layout is in the FILENAME: the same report exports three different shapes now, and two
    # files called the same thing in one downloads folder is how the wrong one gets circulated.
    lay = "" if not turned else f"_{_layout_of(defn)}"
    return _stream_csv(cur, hdr, f"{_slugify(meta['name'])}{lay}_{a}_to_{b}.csv")

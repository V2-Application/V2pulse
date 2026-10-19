"""
Snowflake MIS — VARIANT ARTICLE, sale and stock side by side (mounted at /sf-var-combined).

Reads the DAY-WISE facts built by db-scripts/298:

    BRONZE.DAY_WISE_ST_VAR_SALE     DT x ST_CD x ARTICLE_NUMBER
    BRONZE.DAY_WISE_ST_VAR_STOCK    DT x ST_CD x ARTICLE_NUMBER x KPI(storage location)

NO WINDOWS ARE STORED, AND THAT IS THE POINT
--------------------------------------------
The month-wise predecessors stored nine pre-computed windows (TD, MTD, QTR, YTD ...) and
overwrote themselves on every load, so history was impossible and the report could only select
whole months. Here the window IS the From/To range: sale is SUMmed across it, and every named
window is simply a pair of dates the caller sends. That makes a range nobody anticipated as easy
as the nine that used to be hard-coded, and a day of sale is stored once instead of nine times.
Measured: 334,064 sale rows a day, against ~7.58 crore for a daily snapshot of the nine windows.

SALE IS SUMMED OVER THE RANGE. STOCK IS A POSITION AT THE END OF IT.
-------------------------------------------------------------------
Summing stock across days would count the same shirt once per day it sat there, which is a
stock-DAYS figure and not what anyone means by "stock". So stock is taken at each store's LATEST
REPORTED day on or before `to_date`: ET_STOCK_DATA does not report every store every day, and
picking the store's own last snapshot is the difference between "no stock" and "no file".

THE BASE IS A UNION, NOT AN INNER JOIN
--------------------------------------
A variant appears if it had EITHER sale in the range or stock at the end of it. Measured on the
month-wise predecessors only ~30% of keys had both, so an inner join would drop the two
populations this report exists to surface: holding stock without selling, and sold out. A blank
pivot cell means "no row on that side", which the grid renders differently from a stored 0.

A THIRD SOURCE, ONE LEVEL UP: THE DAY REF FACTS
-----------------------------------------------
    AKS_SILVER.SLV_FCT_DAY_REF      CAL_DATE x ST_CD x MAJ_CAT   (the LY families)
    BRONZE.SAP_VBRP / BRONZE.ET_STOCK_DATA                       (ACT, per article)

The reference the AKASH SIR MODULE plans against, and it arrives here two different ways because
the two halves of it behave differently.

ACT - this year - is computed PER ARTICLE, by running SP_AKS_DAY_REF_LOAD's own logic and stopping
one step before its final GROUP BY. Today's Rs 1,000 at a store-category really is some split
across the articles under it, and the proc already reads that split; it just discards it. The A__
columns are that split, and they add up.

LYSP and LYSM come from the finished category table AS THEY STAND, repeated across every variant of
that store-category-day - context to read a variant against, never measures to total. Not for want
of trying: last year sold a different set of articles, measurably a DISJOINT one, so there is no
per-article LY figure to show. See ART_PREFIX below for the measurement.

Escaping: Snowflake takes no bind variables inside the pivot CASE expressions and the tag list is
read from table DATA, so everything inlined is either _COL_RE-checked (identifier shape) or passed
through _q() (single-quote doubling) — the same two mechanisms as sf_art_kpi.py.
"""
import io
import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import mis_run_log
from .. import sf_cancel
from ..worker.auto_gandola_sf import get_sf_connection
from .sf_gandola import _stream_csv

logger = logging.getLogger(__name__)
router = APIRouter()

SALE_TBL = "V2RETAIL.BRONZE.DAY_WISE_ST_VAR_SALE"
STOCK_TBL = "V2RETAIL.BRONZE.DAY_WISE_ST_VAR_STOCK"
# STORED windows (db-scripts/309). Same nine windows this file computes, but materialised per
# ANCHOR DAY straight from VBRP, so an anchor is right even when the day-wise fact has no history
# for the days inside those windows. Its columns are named exactly as _build projects them
# (S_<WINDOW>__<MEASURE>), which is what lets the stored branch drop in without a rename map.
KPI_TBL = "V2RETAIL.BRONZE.DAY_WISE_ST_VAR_SALE_KPI"
# One row per anchor: which exist, how wide, and whether the load covered ALL stores. Every
# "is this anchor usable?" question goes here and never to KPI_TBL - a DISTINCT over 20.5M rows
# x 90 columns put /params past its 120s proxy timeout, and a timed-out /params made the page
# report "no stored windows" and silently serve the short computed numbers.
KPI_LOG_TBL = "V2RETAIL.BRONZE.DAY_WISE_ST_VAR_SALE_KPI_LOG"
PROD_TBL = "V2RETAIL.SILVER.VW_PRODUCT_MASTER"
# THE MIS STORE MASTER, AND ONLY IT. Standing rule, 2026-09-10: everything under the MIS module
# reads V2RETAIL.AKS_SILVER.SLV_MIS_STORE_MASTER for stores - never SLV_DIM_STORE, never
# VW_DIM_STORE_PLANNING, never BRONZE.ST_MASTER. db-script 353 split the two masters on purpose:
# /aks/store-master owns SLV_DIM_STORE for the plan engines, /sf-mis-store-master owns this table
# for MIS, and nothing syncs them after the one-time seed. Divergence IS the feature - an MIS
# operator retagging a CLUSTER or a REF STATE has to change what MIS slices by without moving a
# single plan number, and a query that reaches back into the AKASH master silently re-couples them
# and makes the MIS uploader a no-op.
#
# ALL 777 ROWS, UNFILTERED. There is no MIS equivalent of VW_DIM_STORE_PLANNING and this does not
# want one: the report is a stock and sale report over LOCATIONS, and the DCs, hubs and offices
# hold more of it than the stores do (measured 08-Sep-2026 on the 0099 split: STORE Rs 25.39 Cr,
# HUB Rs 28.57 Cr, DC Rs 16.35 Cr). Filtering to trading stores would drop two of the three tiers
# out of every enrichment column and leave the plant-type split with nothing to split.
#
# So STORE_TBL and PLANT_TBL are now the SAME table, kept as two names because they answer two
# questions - "what are this location's attributes" and "what kind of plant is it" - and a future
# reader looking for the second should not have to know they happen to share a source today.
#
# ITS OWN 24 COLUMNS SINCE 16-Sep-2026 (db-scripts/377), NOT SLV_DIM_STORE's 48. The comment here
# used to say the two signatures were identical, and they were - because db-script 353 had built
# this table with CREATE TABLE ... LIKE SLV_DIM_STORE. That encoded an assumption that turned out
# to be false: MIS keeps its OWN workbook (ST-MASTER.xlsx sheet 'ST', 795 rows x 24 columns), which
# shares six column names with the planning master's file and means something different by two of
# them. The table was rebuilt on the workbook, so every name below moved with it - see STORE_ATTRS,
# PLANT_COL and STATUS_COL.
#
# THREE NAMES TO READ CAREFULLY BEFORE ADDING A COLUMN HERE:
#   AREA     is a REGION CODE ('N2', 'E9'), TEXT. Floor space is ST_AREA (leased) and ACT_ST_AREA
#            (carpet). The planning master's AREA is the sq-ft one, so a query copied from an
#            AKASH route lands on a text column here and silently aggregates to nothing.
#   ST_STAT  is the SAP plant status (ACT / IN-ACT / UPC / NA / SEDC / SEST), which is what the
#            rule tags want. The OLD / NEW / UPC lifecycle flag is OLD_NEW.
#   ST_NM    is the SHORT name (KPSHR); ST_FULL_NM is the readable one (KAPASHERA) - the opposite
#            way round from SLV_DIM_STORE.
STORE_TBL = "V2RETAIL.AKS_SILVER.SLV_MIS_STORE_MASTER"
PLANT_TBL = "V2RETAIL.AKS_SILVER.SLV_MIS_STORE_MASTER"

# What the day-wise stock loader carries: every non-blank storage location in ET_STOCK_DATA
# (db-scripts/312 removed the 13-location whitelist), plus the LQUA families below.
#
# PRD IS GONE (db-scripts/315). It was ZWM_PRD_DATE1's "packed and awaiting dispatch", credited to
# the destination store, and it is no longer loaded or exposed - 418,210 rows / Rs 204.08 Cr left
# the report with it. A saved report still listing 'PRD' in stock_tags is fine: _tags() and
# _clean_def filter a saved list against STOCK_TAGS, so it is dropped rather than erroring.
#
# The loader has NO location filter now, so a location SAP adds tomorrow lands in the fact on its
# own. This list only decides what the report EXPOSES, which is a display decision and belongs
# here; a new location needs one line added below and no reload.
#
# The LQUA tags used to be absent because LQUA has no date column, so they could only have meant
# "as at the last refresh" and stamping them with a DT would have invented history. They are here
# now because db-scripts/313 reads BRONZE.LQUA_SNAPSHOT instead, which carries SNAP_DATE - one
# snapshot a day. LQUA itself is still never read.
#
# EVERY LGORT ET_STOCK_DATA HOLDS, not the thirteen this list used to carry (db-scripts/312).
# Measured 01-Sep-2026: the table holds 48 distinct locations, so 35 were unreachable - including
# 0032 (Rs 460 Cr), 0044 (Rs 71 Cr) and 0014 (255 stores, Rs 31 Cr).
#
# ORDER IS APPEND-ONLY, AND THAT IS THE WHOLE POINT. The original fourteen keep positions 1-14,
# PRD included, so a spreadsheet taken before this change still lines up column for column; the 35
# new locations go on the END. Slotting them into numeric order would have been tidier to read and
# would have shifted every stock column in every existing export by up to seventy places.
# THE ET_STOCK_DATA FAMILY, on its own, because two things now need to know exactly which tags
# came out of that one table: STOCK_TAGS below (which just concatenates it) and the MIS report
# builder's "stores only" switch, which restricts this family and leaves LQUA / PRD / PTL alone.
# Listing it twice would be a drift waiting to happen - a location appended to one copy and not
# the other reads as an ordinary empty column, which is indistinguishable from "no stock".
ET_LGORT_TAGS = [
    # ── the original LGORT thirteen, positions frozen ──
    # PRD used to sit at position 14 and was removed by db-scripts/315. Positions 1-13 are
    # untouched, which is what an older export actually depends on; the tags appended below shift
    # up by one, and they are a day old, so that is a cost worth paying to stop showing a tag that
    # no longer has any data behind it.
    "0001", "0002", "0003", "0004", "0005", "0006", "0007",
    "0009", "0011", "0013", "0016", "0017", "0099",
    # ── appended by db-scripts/312 ──
    "0008", "0014", "0020", "0031", "0032", "0033", "0035", "0036", "0038", "0040",
    "0041", "0042", "0043", "0044", "0052", "0053", "0059", "0060", "0061", "0063",
    "0064", "0065", "0067", "0068", "0069", "0070", "0073", "0075", "0077", "0078",
    "0079", "0100", "0101", "0102", "PSA1",
]
STOCK_TAGS = ET_LGORT_TAGS + [
    # ── appended by db-scripts/313: the LQUA family, from BRONZE.LQUA_SNAPSHOT ──
    #
    # LQ_<LGTYP>  warehouse bin stock by storage TYPE   (82 tags)
    # LQL_<LGORT> the SAME stock by storage LOCATION    (3 tags)
    #
    # THESE OVERLAP. LQ_* and LQL_* each total to the whole of LQUA, so adding both
    # double-counts; and LQUA bin stock is a SUBSET of the location stock the plain
    # ET_STOCK_DATA tags above report, so adding either family to those double-counts
    # again. The prefixes are the only warning a header can carry - the grid cannot
    # stop anyone summing across them, so read this before totalling stock columns.
    #
    # Valued VERME x MBEW.VERPR / PEINH at the OWN plant only (db-scripts/294's rule,
    # kept). Own-plant material coverage in MBEW is roughly 31%, so many of these
    # carry a quantity and a blank value by design.
    "LQ_128", "LQ_A01", "LQ_A02", "LQ_B05", "LQ_B06", "LQ_B08", "LQ_B09", "LQ_B11", "LQ_B15",
    "LQ_B16", "LQ_B17", "LQ_B20", "LQ_B21", "LQ_B33", "LQ_B35", "LQ_B36", "LQ_B41", "LQ_B47",
    "LQ_B50", "LQ_B54", "LQ_B56", "LQ_B58", "LQ_B66", "LQ_B67", "LQ_C03", "LQ_C08", "LQ_C10",
    "LQ_C11", "LQ_C12", "LQ_D04", "LQ_D22", "LQ_D26", "LQ_E01", "LQ_E03", "LQ_F01", "LQ_G01",
    "LQ_G03", "LQ_H01", "LQ_H13", "LQ_J14", "LQ_J17", "LQ_J20", "LQ_K11", "LQ_K24", "LQ_K26",
    "LQ_K33", "LQ_L13", "LQ_M21", "LQ_M24", "LQ_M52", "LQ_M53", "LQ_N62", "LQ_O21", "LQ_O30",
    "LQ_P02", "LQ_R18", "LQ_R30", "LQ_S11", "LQ_U44", "LQ_U58", "LQ_U61", "LQ_U71", "LQ_U88",
    "LQ_U96", "LQ_V01", "LQ_V04", "LQ_V05", "LQ_V06", "LQ_V07", "LQ_V08", "LQ_V09", "LQ_V10",
    "LQ_V11", "LQ_V15", "LQ_V16", "LQ_V18", "LQ_V21", "LQ_V25", "LQ_V51", "LQ_W17", "LQ_W44",
    "LQ_W45",
    "LQL_0001", "LQL_0002", "LQL_0040",
    # ── appended by db-scripts/314: LQUA by BIN GROUP, DH24 only ──
    #
    # A THIRD relationship in this family, and the odd one out. LQ_* and LQL_* each
    # regroup the WHOLE of LQUA; these three cover three bin groups only, so they
    # total to a FRACTION of it and are never "the rest" of anything.
    #
    #   LQB_F1    990 bins LIKE 'F1-%'   storage type E01
    #   LQB_G1  1,158 bins LIKE 'G1-%'   storage type E01
    #   LQB_GRT     2 bins LIKE '%GRT%'  GRT-REV (V08), BIN-GRT (V07)
    #
    # Every one of those bins belongs to DH24, so these are DC KPIs, not network
    # ones - summing them across the estate sums a single site.
    "LQB_F1", "LQB_G1", "LQB_GRT",
    # ── appended by db-scripts/316: PRD per SHIPPING POINT ──
    #
    # From ET_ZWM_PRD_BCG_REP_RFC, one tag per VSTEL, credited to SITE. Qty = VEMNG,
    # value = VEMNG x VERPR, and VERPR is a column on that table - so unlike the LQUA
    # families there is no MBEW join and no own-plant pricing gap.
    #
    # This replaces the single 'PRD' tag that db-scripts/315 removed. A NEW VSTEL will
    # load into the fact on its own but will NOT appear until it is named here.
    "DH24_PRD", "DW01_PRD",
    # ── appended by db-scripts/317: PTL (put-to-light) ──
    #
    # From ET_ZWM_PTL_STK_RFC. ONE tag yields both columns the report needs - PTL - Stk Q
    # (MENGE) and PTL - Stk V - so there is no separate PTL_Q / PTL_V tag to add.
    #
    # Attributed to DWERKS, the DESTINATION store, but PRICED at SWERKS, the DC that
    # physically holds it: measured 99.4% of rows can be valued at the DC against 47.9%
    # at the destination store, because a DC transacts everything it ships and a single
    # store does not. See db-scripts/317.
    "PTL",
    # ── appended by db-scripts/318: HUB PRD, one tag per HU type ──
    #
    # From ZWM_PRD_HUB, credited to STORE_WERKS. Qty = QTY, value = the table's own VALUE
    # column - checked to equal QTY x MAP to 0.00%, so no MBEW join and an exact tie to
    # source. HU_TYPE holds only F and G today; a new one loads into the fact on its own
    # but will not appear until it is named here.
    "F_HUBPRD", "G_HUBPRD",
    # -- appended by db-scripts/345: LQUA IN-TRANSIT --
    #
    # LQUA at LGTYP 'V04', bin 'IN-TRANSIT'. The third term of the TRF-IN store opening stock
    # (db-scripts/286), carried here so TOTAL_STORE_STOCK below can be that same definition.
    #
    # NOT LOADED UNTIL db-scripts/345 IS APPLIED AND THE STOCK FACT RELOADED. Listed now so the
    # composite needs no code change on the day it lands, and so /params can name it as a missing
    # member in the meantime rather than letting the total quietly read short.
    #
    # NOT LQ_V04. That tag is the whole storage type - the IN-TRANSIT bin plus a few thousand
    # numeric ones - measured -90.6 L pcs for the bin against -54.3 L for the type, so the two
    # differ by 36.3 L pcs and in the direction that flatters the number.
    #
    # SIGNED, and net NEGATIVE. That is the data, not a defect; db-scripts/286 records the same.
    "LQB_TRANSIT",
]

# ── LQUA A THIRD TIME: WAREHOUSE NUMBER x STORAGE TYPE (db-scripts/380) ───────────────────────
#
# <LGNUM>_<LGTYP> - SDC_V01, V2R_E01, V2S_V11. The LQ_* family above keys on the storage TYPE
# alone, and a type number is only unique WITHIN a warehouse, so one LQ_ tag can be three
# different things. Measured on the 16-Sep-2026 snapshot, LQ_V01 is:
#
#     SDC_V01   441 store plants   1,05,06,481 pcs      the stores' own bins
#     V2R_V01    27 DC plants        30,01,963 pcs      the DCs' V01
#     V2S_V01     2 plants              14,076 pcs
#
# Same collision on V04, V07 and A01/A02. This family is the general form of the LQ_V02 RULE tag
# below, which exists only because LQ_V01 could not tell store HV01's own stock from the network's.
#
# IT IS THE THIRD FULL REGROUPING OF LQUA. LQ_ (by type), LQL_ (by location) and this one each
# total to the WHOLE snapshot, so adding any two double-counts - and all three overlap the plain ET
# LGORT tags, which report the same stock by storage location. Never total across them.
#
# NOT IN THE DEFAULT SELECTION, and that is a measured decision rather than a preference - see
# _tags(). 98 pairs is 196 more columns, and the grid was already timed at 279.6s with 141 tags
# against a 300s client ceiling ([[ref_ml_fetch_headers_timeout]] / the note in _stock_pick). They
# are in the picker, they are in /params, and picking one asks for exactly that one.
#
# NEEDS db-scripts/380 APPLIED AND THE STOCK FACT RELOADED for the days you want them on. LGNUM is
# not derivable from what is already loaded - the fact carries KPI and ST_CD only, and the plant
# does not decide the warehouse number (DD03 reports under both V2R and V2S) - so there was no
# expression this module could have computed instead. Until the reload they are simply absent from
# the fact, and /params filters the picker against what the fact holds, so nothing shows a blank.
#
# HAND-MAINTAINED AND IT FAILS SILENTLY, exactly like the LQ_ list: a pair SAP creates tomorrow
# loads into the fact on its own and stays invisible until it is named here. The re-measure query
# is at the foot of db-scripts/380.
#
# 98 PAIRS ARE LISTED, 96 REACH THE FACT. V2S_A02 and V2S_V01 hold only articles the loader's
# master join drops (no product-master row with a non-blank major category), so they carry no rows
# and /params filters them out of the picker rather than offering an empty column. They stay in the
# list: the day an article of theirs is classified, the tag appears with no code change.
#
# VERIFIED 17-Sep-2026, on the 16-Sep stock day, after db-scripts/380 and a reload: all three LQUA
# regroupings total 3,70,02,810 pcs - by type (LQ_), by location (LQL_) and by warehouse x type -
# and across 91 storage types not one pair disagrees with its LQ_ tag. SDC_V01 1,05,06,430 +
# V2R_V01 30,01,876 = LQ_V01 1,35,08,306 exactly.
WHN_TAGS = [
    # SDC: 67 pairs, biggest SDC_V01 (441 plants, 10,506,481 pcs), SDC_V04 (135 plants, -347,027 pcs), SDC_D04 (1 plant, 25,671 pcs)
    "SDC_128", "SDC_B05", "SDC_B06", "SDC_B08", "SDC_B09", "SDC_B11", "SDC_B15", "SDC_B16",
    "SDC_B17", "SDC_B20", "SDC_B21", "SDC_B33", "SDC_B35", "SDC_B36", "SDC_B39", "SDC_B41",
    "SDC_B47", "SDC_B50", "SDC_B54", "SDC_B56", "SDC_B58", "SDC_B66", "SDC_B67", "SDC_B79",
    "SDC_C03", "SDC_C08", "SDC_C10", "SDC_C11", "SDC_C12", "SDC_D04", "SDC_D22", "SDC_D26",
    "SDC_H01", "SDC_H13", "SDC_J14", "SDC_J17", "SDC_J20", "SDC_K11", "SDC_K24", "SDC_K26",
    "SDC_K33", "SDC_L13", "SDC_L17", "SDC_M21", "SDC_M24", "SDC_M52", "SDC_M53", "SDC_M58",
    "SDC_N62", "SDC_O21", "SDC_O30", "SDC_P02", "SDC_R18", "SDC_R30", "SDC_S11", "SDC_U44",
    "SDC_U71", "SDC_U88", "SDC_U96", "SDC_V01", "SDC_V04", "SDC_W17", "SDC_W42", "SDC_W44",
    "SDC_W45", "SDC_X53", "SDC_X55",
    # V2R: 26 pairs, biggest V2R_A01 (2 plants, 23,642,792 pcs), V2R_E01 (1 plant, 11,609,412 pcs), V2R_E03 (1 plant, 8,053,063 pcs)
    "V2R_A01", "V2R_D01", "V2R_E01", "V2R_E03", "V2R_F01", "V2R_G01", "V2R_G03", "V2R_V01",
    "V2R_V04", "V2R_V05", "V2R_V06", "V2R_V07", "V2R_V08", "V2R_V09", "V2R_V10", "V2R_V11",
    "V2R_V15", "V2R_V16", "V2R_V18", "V2R_V19", "V2R_V21", "V2R_V23", "V2R_V24", "V2R_V25",
    "V2R_V26", "V2R_V51",
    # V2S: 5 pairs, biggest V2S_V11 (4 plants, 24,816,572 pcs), V2S_V07 (3 plants, -24,811,294 pcs), V2S_V04 (2 plants, 49,147 pcs)
    "V2S_A02", "V2S_V01", "V2S_V04", "V2S_V07", "V2S_V11",
    # ── appended 17-Oct-2026: pairs the loader was already writing but nobody could pick ──
    # Found by diffing the fact's KPIs over the last 30 days against this file: new storage types
    # (E04 first seen 15-Oct, G04 03-Oct at ~3.5 lakh pcs/day, F04 10-Oct) and a tail of small SDC
    # bins. Appended, per the rule at the top of this list.
    "SDC_120", "SDC_125", "SDC_137", "SDC_B73", "SDC_B77", "SDC_B78", "SDC_F02", "SDC_F04",
    "SDC_K15", "SDC_L02", "SDC_L12", "SDC_L15", "SDC_N23", "SDC_N53", "SDC_N80", "SDC_W47",
    "SDC_W55", "SDC_W57", "SDC_W59", "SDC_Y05",
    "V2R_E04", "V2R_G04",
]
_WHN_SET = frozenset(WHN_TAGS)
STOCK_TAGS = STOCK_TAGS + WHN_TAGS
# LQ_<LGTYP> tags the loader writes that the db-scripts/313 list predates - the same 17-Oct diff as
# the WHN pairs above. Here, at the very END, not in the LQ_ block: inserting them there would
# shift every PRD / PTL / HUB / WHN column of an existing export. Every LQ_ tag LQUA can produce
# loads on its own (db-scripts/313 groups by LGTYP); it only reaches the pickers once named here.
STOCK_TAGS = STOCK_TAGS + [
    "LQ_120", "LQ_125", "LQ_137", "LQ_B39", "LQ_B73", "LQ_B77", "LQ_B78", "LQ_B79", "LQ_D01",
    "LQ_E04", "LQ_F02", "LQ_F04", "LQ_G04", "LQ_K15", "LQ_L02", "LQ_L12", "LQ_L15", "LQ_L17",
    "LQ_M58", "LQ_N23", "LQ_N53", "LQ_N80", "LQ_V19", "LQ_V23", "LQ_V24", "LQ_V26", "LQ_W42",
    "LQ_W47", "LQ_W55", "LQ_W57", "LQ_W59", "LQ_X53", "LQ_X55", "LQ_Y05",
]
SALE_MEASURES = ["SALE_V", "SALE_Q", "GM_V", "DISCOUNT_V"]
# Per window, in the order the month-wise tables emitted them, so an export lines up with one taken
# before the day-wise switch.
WINDOW_MEASURES = ["SALE_V", "SALE_Q", "GM_V", "DISCOUNT_V",
                   "AVG_SALE_V", "AVG_SALE_Q", "AVG_GM_V",
                   "SAL_DAYS", "GM_DAYS", "DATA_DAYS"]
# The nine named windows, in load order. NOTHING IS STORED PER WINDOW - each is a pair of dates
# derived from the To date and evaluated against the day-wise fact, which is why adding a tenth
# costs one line here and no reload at all.
SALE_WINDOWS = ["TD", "MTD", "QTR", "LAST_7DAYS", "LAST_4WEEK", "YTD",
                "LAST_6MONTH", "LYSP_SSN", "LYSP_MONTH"]
STOCK_MEASURES = ["STOCK_V", "STOCK_Q"]

# ── THE PLANT-TYPE SPLIT: one stock tag, re-projected across the network tiers ────────────────
#
# A tag like 0099 is one number covering three completely different things. Measured on the latest
# reported day per location, 01-Sep-2026:
#
#     HUB    24 locations   20.8 L pcs   Rs 34.06 Cr
#     STORE 443 locations   72.8 L pcs   Rs 26.49 Cr
#     DC      4 locations    9.7 L pcs   Rs 18.50 Cr
#
# Read as one column that is Rs 79 Cr of "0099" and says nothing; the three tiers hold it for
# different reasons and move it on different clocks, so they are three measures wearing one name.
#
# THIS IS A RE-PROJECTION, NOT A SECOND AGGREGATION. The buckets are a function of the ROW'S STORE
# alone, and the grid's grain already contains the store - so each split column is the plain
# K_<tag>__<measure> column passed through an IFF on the row's plant type. Nothing extra is
# scanned, nothing extra is grouped, and the arithmetic is exact by construction:
#
#     the buckets are MUTUALLY EXCLUSIVE and EXHAUSTIVE, so on any row exactly ONE of them
#     carries the value and the rest are blank, and down any column set the splits SUM BACK to
#     the plain tag column to the paisa.
#
# That exhaustiveness is why OTHER and UNMAPPED are here rather than dropped as rounding error.
# UNMAPPED is the locations the MIS master has no row for at all - 23 of the stock fact's 541 as
# measured 10-Sep-2026, closed stores and test plants; it was 86 while this read the 666-row
# planning view, and the 63 that moved are the DCs and hubs the master does carry, which now land
# in their own tier instead. OTHER is every typed plant that is not one of the three tiers
# (NA, PLANT, the offices) AND any plant type SAP adds tomorrow. Without those two a new plant type
# would silently leak out of the split and the columns would quietly stop tying to the whole.
#
# ON A SINGLE ROW THE BLOCK IS SPARSE BY DESIGN. One store has one plant type, so a store row fills
# STORE and leaves DC and HUB blank. These columns earn their keep in the SUBTOTAL row, in a CSV
# pivot and in a filtered view - "how much of this article's 0099 is still at the DC" is a question
# about a set of rows, never about one.

# ── The two master columns the splits and the rule tags read ─────────────────────────────────
# The plant type itself, carried as a plain text column so the split can be read on a single row
# and filtered on. 'UNMAPPED' rather than a blank for a location with no master row, so it matches
# the bucket column beside it instead of reading as missing data.
# ST_TYP since db-scripts/377 - the MIS workbook's name for what the planning master calls
# PLANT_TYPE. Same eight values (STORE / NA / HUB / DC / OFFICE-OLD / OFFICE-NEW / PLANT /
# ZONAL_OFFICE), so every bucket predicate and every split column below is unchanged; only the
# column this reads from is. A saved report holding a col_filter on "PLANT_TYPE" no longer matches
# anything and comes back empty rather than erroring (_filter_sql drops an unknown column) -
# re-pick the value once under its new header and it saves the new one.
PLANT_COL = "ST_TYP"
# The plant-status column, and the one value in it that means "this location is closed". ST_STAT
# since db-scripts/377; the master spelled this STATUS while it was a clone of the planning one.
# 'IN-ACT' is 59 of the workbook's 795 rows; ACT (448), UPC (119), NA (167), SEDC and SEST are all
# read as trading here. Read only by the rule tags below, and named here so the spelling lives
# next to PLANT_COL rather than inside a lambda.
#
# NOT OLD_NEW, deliberately. That is the column carrying the planning master's OLD / NEW / UPC
# vocabulary and it also carries CLOSED, so it LOOKS like the better test - but 11 of its rows hold
# Excel's '#N/A' and its CLOSED and the plant status disagree on real rows. ST_STAT is the SAP
# system of record for whether a plant transacts, which is the question a rule tag is asking.
STATUS_COL = "ST_STAT"
INACTIVE_STATUS = "IN-ACT"

# ONE f-string per bucket, off PLANT_COL. They were five literal "pt.PLANT_TYPE" strings until
# db-scripts/377 renamed the column, at which point four of the five split columns would have gone
# empty and the fifth (UNMAPPED, which never named the column) would have absorbed the lot - a
# silent, plausible-looking answer. Spell the column once.
PLANT_BUCKETS = [
    ("STORE",    f"pt.{PLANT_COL} = 'STORE'"),
    ("DC",       f"pt.{PLANT_COL} = 'DC'"),
    ("HUB",      f"pt.{PLANT_COL} = 'HUB'"),
    ("OTHER",    f"pt.ST_CD IS NOT NULL AND pt.{PLANT_COL} NOT IN ('STORE', 'DC', 'HUB')"),
    ("UNMAPPED", "pt.ST_CD IS NULL"),
]
# Which tags get split. ONE LINE PER TAG and no reload - the split is computed from a column the
# query already produces. Deliberately not every tag: 141 tags x 5 buckets x 2 measures is 1,410
# columns nobody asked for, on a grid that is already wide.
SPLIT_TAGS = ["0099"]
# ── "STORES ONLY": one report-level switch over the whole ET_STOCK_DATA family ────────────────
#
# The RDC MAJ CAT report wants its ROWS to span the estate - every plant, so the RDC totals and
# the sale windows are complete - while its ET STOCK columns count STORES alone. A report-wide
# ST_TYP condition cannot express that: it would take the DCs out of the sale figures too.
#
# So the restriction rides on the TAG, not the report. It is the same shape as a plant-type split
# (`pt.ST_TYP = 'STORE'`, evaluated per row off a column the query already produces) applied to a
# FAMILY instead of to one hand-picked tag - which is why it does not add 240 names to a picker
# that 149 of them are already ticked in.
#
# ET_STOCK_DATA ONLY, and that is a judgement about the data, not a shortcut. LQUA bin stock and
# the PRD/PTL/HUBPRD tags LIVE at DCs and hubs - DH24_PRD is by definition stock at a DC - so
# restricting them to STORE would not re-attribute them, it would blank them.
#
# APPLIED AT THE LEAF, so a composite inherits it through tag_predicate's own recursion:
# TOTAL_STORE_STOCK's eight ET members become store-only, its PRD and LQUA members do not. That
# is the reading that makes the total mean what its name says at a DC row.
#
# A TAG THAT ALREADY NAMES A TIER IS SKIPPED. '0099_DC' asked for the DC on purpose; ANDing STORE
# onto it is FALSE on every row, not a narrowing. So the switch touches plain ET tags only, and
# '0099_STORE' keeps meaning exactly what it did before anyone turned it on.
STORE_ONLY_PRED = f"pt.{PLANT_COL} = 'STORE'"
_ET_TAG_SET = frozenset(ET_LGORT_TAGS)

# ── "V2R: THE DCs ONLY": the same switch, for the warehouse-number family ─────────────────────
#
# V2R IS NOT THREE DCs, IT IS A WAREHOUSE NUMBER. Measured 17-Sep-2026 on the 16-Sep snapshot, the
# 27 plants under it split 4 / 23 by what the MIS master types them as:
#
#     DC    DH24 (FRKH-NGR-RDC), DK04 (BANGALORE-RDC), DW01 (KOLKATA-RDC), DH26 (JMLPR-RDC)
#           10,07,030 pcs of V2R_V01
#     HUB   DP01, DH27, DO02 and twenty more
#           19,94,933 pcs - TWO THIRDS of the tag
#
# So a reader who means "the DC stock" and reads V2R_V01 gets three times what they asked for, and
# a reader comparing it to SAP for one RDC gets a number nine times larger. This switch is what
# lets the column mean the first thing without the tag having to lie about the second.
#
# 'DC' AND TRADING, NOT A LIST OF THREE. DH26 is typed DC and carries ST_STAT = 'IN-ACT' - the
# Jamalpur RDC, closed, holding 15 pcs in 6 bins. So the master already distinguishes exactly the
# three that are asked for, and a hard-coded triple would have frozen that: an RDC that opens needs
# a code change, one that closes keeps counting. The same predicate shape the LQ_V02 rule tag uses
# for stores (see RULE_TAGS), for the same reason.
#
# COLUMNS ONLY, LIKE STORE_ONLY_PRED. It rides the TAG, never the report, so the hubs keep their
# rows, their sale and every other column - a dim filter on ST_CD would have taken them out of the
# sale figures too.
#
# THE V2R FAMILY ONLY. SDC_* is the stores' own warehouse and V2S_* is six plants; ANDing 'DC' onto
# either would not narrow them, it would blank them - which is the same judgement STORE_ONLY_PRED
# makes when it leaves the LQUA and PRD families alone.
#
# A TAG THAT ALREADY NAMES A TIER IS SKIPPED, exactly as the stores-only switch skips '0099_DC':
# asking for the HUB slice and then ANDing DC onto it is FALSE on every row, not a narrowing.
DC_ONLY_PRED = f"pt.{PLANT_COL} = 'DC' AND pt.{STATUS_COL} <> '{INACTIVE_STATUS}'"
# Derived from WHN_TAGS, never a second list: the family is "every tag whose warehouse number is
# V2R", and a hand-kept copy would miss the first V2R storage type SAP adds.
_V2R_TAG_SET = frozenset(t for t in WHN_TAGS if t.startswith("V2R_"))


def plant_pick_pred(plants, kpi_col):
    """The chosen-plants test for a tag - `TRIM(ST_CD) IN (...)` on the row itself.

    NAMED FOR WHAT IT DOES, not for the family it was written for. It began as the V2R switch and
    is now what every per-tag plant pick is built from (see tag_predicate's `tag_plants`): the test
    is the same whichever tag asked for it, because a plant is a plant.

    THE READER PICKS THE PLANTS, the code does not pick them for the reader. DC_ONLY_PRED above is
    a RULE ('DC' and trading, which today means DH24 / DW01 / DK04) and it stays, as the preset the
    picker fills itself from - but a warehouse is reorganised more often than this code is, and the
    question "which plants count as our DCs" belongs to whoever is reading the report.

    NO PLANT LOOKUP. This tests the fact's OWN ST_CD, so unlike the rule it needs no `pt` join at
    all - which is also why tag_needs_plant stays False for it. That matters on the report builder,
    where the join it avoids is against a 795-row master on every stock row."""
    codes = [str(p).strip().upper() for p in (plants or []) if str(p).strip()]
    if not codes:
        return None
    st = _row_col(kpi_col, "ST_CD")
    return f"TRIM({st}) IN (" + ", ".join(_q(c) for c in codes) + ")"
PLANT_EXPR = f"IFF(pt.ST_CD IS NULL, 'UNMAPPED', NULLIF(pt.{PLANT_COL}, '')) AS {PLANT_COL}"
PLANT_FILT_EXPR = f"IFF(pt.ST_CD IS NULL, 'UNMAPPED', NULLIF(pt.{PLANT_COL}, ''))"


# ── COMPOSITE TAGS: one column that is the SUM of several ────────────────────────────────────
#
# TOTAL_STORE_STOCK answers "everything that is effectively this store's stock" - what is
# physically in it, what is in transit to it, and what is packed for it somewhere upstream. It is
# the same definition SP_AKS_TRF_STORE_STOCK_FROM_STOCK_DATA (db-scripts/286) uses for the TRF-IN
# opening stock, which is why the member list is not an arbitrary pick:
#
#     ET LGORT 0001,0002,0004,0005,0006,0008,0009,0017   physically in the store
#     0099_STORE                                          in transit, at real stores only
#     DH24_PRD, DW01_PRD                                  packed at a DC, credited to the store
#     F_HUBPRD, G_HUBPRD                                  packed at a hub, credited to the store
#     LQB_TRANSIT                                         LQUA V04 / IN-TRANSIT   (see below)
#
# THE MEMBERS ARE MUTUALLY EXCLUSIVE, WHICH IS WHAT MAKES THE SUM LEGITIMATE. The ET LGORTs are
# disjoint by definition; 0099_STORE is a slice of a different LGORT; the four PRD tags come from
# two different source tables keyed on different columns; and LQB_TRANSIT is a bin cut of LQUA.
# Nothing here is a regrouping of anything else here - which is exactly the trap the LQ_* / LQL_*
# families carry (see STOCK_TAGS above), and why NEITHER of those may ever join this list.
#
# 0099_STORE, NOT 0099. The plain tag includes the hubs and DCs, which hold more of it than the
# stores do (Rs 34.06 Cr + 18.50 Cr against 26.49 Cr, 01-Sep-2026). Summing 0099 into a STORE total
# would put the whole network's in-transit stock on every store row.
#
# TWO MEMBERS CONTRIBUTE NOTHING TODAY, and the page says so rather than quietly reading short -
# /params reports which members are actually present in the fact:
#   * 0008        the tag exists and ET_STOCK_DATA holds 1,787 rows / 12,974 pcs of it, but no
#                 0008 row reaches DAY_WISE_ST_VAR_STOCK. Left in the list: if the loader starts
#                 carrying it, the total picks it up with no code change.
#   * LQB_TRANSIT NOT LOADED YET - it needs db-scripts/345 applied and the stock fact reloaded.
#                 It is emphatically NOT LQ_V04: storage type V04 is the IN-TRANSIT bin PLUS a few
#                 thousand numeric bins, measured -90.6 L pcs against -54.3 L for the whole type,
#                 so LQ_V04 would miss the mark by 36 L pcs in the wrong direction.
#
# IT CAN GO NEGATIVE, AND THAT IS THE DATA. LQB_TRANSIT is signed and net negative (-90.6 L pcs
# measured); db-scripts/286 records the same thing and the same consequence - a store whose other
# terms are small can total below zero. A negative here is traceable to that one member.
COMPOSITE_TAGS = {
    "TOTAL_STORE_STOCK": ["0001", "0002", "0004", "0005", "0006", "0008", "0009", "0017",
                          "0099_STORE", "DH24_PRD", "DW01_PRD", "F_HUBPRD", "G_HUBPRD",
                          "LQB_TRANSIT"],
}


# ── RULE TAGS: one column whose rows are picked by a RULE, not by a KPI name ───────────────
#
# LQ_V02 is the LQUA stock sitting under the store's OWN storage type. Every other stock tag names
# ONE KPI and reads that same KPI on every row; this one reads a DIFFERENT KPI on every row - the
# one the row's own store code spells:
#
#     store HD22 -> LQ_D22        store HB05 -> LQ_B05        store U128 -> LQ_128
#
# The storage type is the store code's LAST THREE characters, which is how a store's own WM
# storage type is named here. Measured 05-Sep-2026: 59 active stores, 72,357 pcs, Rs 1.44 Cr.
# Sixty-odd tags that each carry one store and are blank for the other 664, read as one column.
#
# WHY IT IS NOT A COMPOSITE. A composite is a fixed member list, and a member list breaks on
# LQ_V01: storage type V01 is a network-wide type held by 468 locations (2.06 Cr pcs) and it
# collides, by pure coincidence, with the real store HV01. Listing LQ_V01 as a member would put
# the whole network's V01 stock on every store row. The rule tests THE ROW instead -
# KPI = 'LQ_' || RIGHT(ST_CD, 3) - so HV01 contributes its own 822 pcs and the other 467 locations
# contribute nothing. The same test keeps LQ_F01 off store HF01: that stock is stamped with a DC's
# ST_CD, so it never meets the store whose code would otherwise claim it.
#
# PLANT TYPE AND STATUS, both off the store master. The bare code match would also claim LQ_D04
# and LQ_H01, which belong to the OFFICE plants RD04 and RH01 - so the row's location must be a
# STORE; and a closed store's leftovers are not stock anyone plans against, so IN-ACT is out.
# Both tests name `pt`, which is why this tag reports tag_needs_plant() = True.
#
# IT IS A SLICE OF LQUA, so every warning on STOCK_TAGS holds: it overlaps the LQ_* tags it is
# drawn from, the LQL_* regrouping of the same rows, and the plain ET tags that already report
# this stock by location. It may never join TOTAL_STORE_STOCK or any other total.
RULE_TAGS = {
    "LQ_V02": {
        # How the SCAN narrows - a PREFIX TEST, never a list of names. Every other tag prunes the
        # read with `KPI IN (...)` built from STOCK_TAGS, and that list is a hand-maintained
        # snapshot: measured 05-Sep-2026 the fact already held LQ_B39, LQ_B79, LQ_L17 and LQ_M58,
        # four store storage types created since db-scripts/313 was written and so absent from it.
        # Pruning this tag that way read 70,593 pcs against a true 73,366 - short by exactly those
        # four stores, and short SILENTLY, since a tag nobody has named looks identical to a tag
        # with no stock. STARTSWITH cannot go stale: a store opened tomorrow is in the column the
        # day its bin stock loads. It matches neither 'LQL_' nor 'LQB_' - both fail at the third
        # character - so the two other LQUA families stay out of the read.
        #
        # Read WITHOUT the plant lookup, by the day-picker as well as the aggregate, so it may only
        # ever test the KPI. The store tests live in `pred`, which runs where `pt` is joined.
        "scan": (lambda kpi_col: f"STARTSWITH({kpi_col}, 'LQ_')"),
        "pred": (lambda kpi_col, st_col:
                 f"{kpi_col} = 'LQ_' || RIGHT(TRIM({st_col}), 3) "
                 f"AND pt.{PLANT_COL} = 'STORE' AND pt.{STATUS_COL} <> '{INACTIVE_STATUS}'"),
    },
}

# ── PICK TAGS: one column that is the FIRST NON-ZERO of several ──────────────────────────────
#
# MSA and GRT are the same rack at two different DCs. LQUA names a storage type per site, so the
# MSA rack is storage type E01 at DH24 and E03 at DW01, and the GRT rack is G01 at DH24 and G03
# at DW01 - four tags for two things. Read as four columns, half of every one of them is blank
# and no single column answers "how much is in MSA".
#
#     MSA_Q = COALESCE(K_LQ_E01__STOCK_Q, K_LQ_E03__STOCK_Q)      COALESCE(0,5) = 5
#     MSA_V = COALESCE(K_LQ_E01__STOCK_V, K_LQ_E03__STOCK_V)      COALESCE(5,0) = 5
#     GRT_Q = COALESCE(K_LQ_G01__STOCK_Q, K_LQ_G03__STOCK_Q)
#     GRT_V = COALESCE(K_LQ_G01__STOCK_V, K_LQ_G03__STOCK_V)
#
# NON-ZERO, NOT NON-NULL, which is what those examples ask for and is NOT what SQL's COALESCE
# does: SQL hands back the first non-NULL, so a member that loaded a hard 0 would beat a real
# second one. The expression is COALESCE(NULLIF(m1,0), NULLIF(m2,0), m1, m2) - first non-zero,
# falling back to a zero member so a genuine 0 still prints 0 rather than a dash, and to NULL
# only when no member has a row at all.
#
# A PICK, NOT A SUM, AND THE TWO AGREE TODAY. Measured 17-Sep-2026 on each tag's latest reported
# day, every one of the four members lives at exactly ONE location - LQ_E01 and LQ_G01 at DH24,
# LQ_E03 and LQ_G03 at DW01 - so a row, which carries one store, can only ever have one of a pair
# filled, and first-non-zero and a sum return the same number. The pick is the safer of the two if
# a third site ever reports both: it names one rack rather than quietly adding two.
#
#   LQ_E01  DH24  1.16 Cr pcs  Rs 118.70 Cr        LQ_G01  DH24  73,859 pcs  Rs 1.92 Cr
#   LQ_E03  DW01  0.81 Cr pcs  Rs 106.54 Cr        LQ_G03  DW01  27,229 pcs  Rs 0.68 Cr
#
# THE GRAIN DECIDES HOW THE PICK IS SPELLED, and tag_measure_sql's `multi_store` is where that is
# settled. Every row of THIS grid carries a store, so exactly one member can be filled and the pick
# is a rename. Aggregate ABOVE the store - the MIS report builder grouped by MAJ_CAT alone, where
# both DCs land in one bucket - and first-non-zero would report DH24 and silently drop DW01; there
# the same tag is emitted as the UNION of its members, which is exactly "each store's pick, summed"
# because the members are disjoint by location. Same number wherever both spellings are defined.
#
# IT IS A SLICE OF LQUA, so every warning on STOCK_TAGS holds: these overlap the LQ_* tags they
# read, the LQL_* regrouping of the same rows, and the plain ET tags that already report this
# stock by location. Never total one with any of them, and never make one a member of
# TOTAL_STORE_STOCK.
#
# NOT LQB_GRT, which is a different column with a similar name: that is the two BIN GROUPS at DH24
# whose bins spell GRT (GRT-REV, BIN-GRT), 0.7% of LQUA. GRT here is the whole G01 / G03 storage
# TYPE at each DC.
#
# ORDER MATTERS AND IT IS THE ORDER BELOW - DH24's tag first - so a row that somehow carried both
# has a documented answer rather than whichever term the optimiser reached first.
PICK_TAGS = {
    "MSA": ["LQ_E01", "LQ_E03"],
    "GRT": ["LQ_G01", "LQ_G03"],
}


def _row_col(kpi_col, col):
    """Another column off the SAME row as `kpi_col` - 's.KPI' -> 's.ST_CD'.

    A rule tag tests the row's STORE as well as its KPI, and the two callers alias the stock fact
    differently: `s` in the variant grid, `f` in the report builder. Deriving the alias from the
    column they already pass is what saves them from having to agree on a name."""
    return f"{kpi_col.rsplit('.', 1)[0]}.{col}" if "." in kpi_col else col


def composite_members(tag):
    """A composite's members, dropped to the ones this build actually knows.

    A member naming a tag that is not in STOCK_TAGS yet (LQB_TRANSIT before db-scripts/345) is
    SKIPPED rather than raising: the definition is allowed to describe the finished total, and the
    column reads short until the loader catches up. /params is what makes that visible."""
    known = set(STOCK_TAGS) | set(split_tag_names())
    return [m for m in COMPOSITE_TAGS.get(tag, []) if m in known]


def pick_members(tag):
    """A pick tag's members, IN ORDER and dropped to the ones this build knows.

    Order is the whole definition here, unlike a composite's - the first non-zero member wins, so
    a list that came back in a different order could come back with a different number. Same
    skip-the-unknown rule as composite_members for the same reason: a definition may name a tag
    the loader has not started carrying yet, and the column reads short (visibly, via /params)
    instead of erroring."""
    known = set(STOCK_TAGS) | set(split_tag_names())
    return [m for m in PICK_TAGS.get(tag, []) if m in known]


def split_tag_names():
    """The plant-type splits as TAG NAMES - '0099_DC' and so on.

    Named tags rather than a separate concept because that is what they are to every caller: the
    MIS report builder lists them in its stock-tag picker, saves them in a definition, and gets
    K_0099_DC__STOCK_V out of its ordinary K_<tag>__<measure> loop. One vocabulary, so the two
    engines cannot disagree about what a split column is called."""
    return [f"{t}_{b}" for t in SPLIT_TAGS for b, _pred in PLANT_BUCKETS]


def resolve_tag(tag):
    """(KPI to read, bucket predicate or None) for a tag name.

    A plain tag resolves to itself and no predicate; a split tag resolves to the KPI it slices and
    the predicate that slices it. The predicate names alias `pt`, so any caller using it must have
    plant_lookup_sql() joined under that alias.

    Exact match against the generated names, never a string split: '0099_DC' and 'LQL_0001' are
    the same shape, and a parser would happily read the second as tag LQL, bucket 0001."""
    for t in SPLIT_TAGS:
        for b, pred in PLANT_BUCKETS:
            if tag == f"{t}_{b}":
                return t, pred
    return tag, None


def tag_predicate(tag, kpi_col="s.KPI", store_only=False, dc_only=False, v2r_plants=None,
                  tag_plants=None, family_plants=None):
    """The SQL boolean that selects a tag's rows, narrowed to the plants picked for it.

    `tag_plants` is {TAG: [ST_CD, ...]} - WHICH PLANTS COUNT FOR THIS TAG, and it is the general
    form of the V2R switch above. V2R needed one first because it is a warehouse NUMBER spanning
    27 plants, but the question it answers is not about V2R: 0001 spans every store, V2S_* six
    plants, and "the DC's 0001, not the estate's" is the same question with a different tag in it.

    `family_plants` is {FAMILY: [ST_CD, ...]} - the SAME pick, said once for a whole warehouse or
    storage family (see plant_family): ET, SDC, V2R, V2S, LQ, LQL, LQB, PRD, HUBPRD. Naming the
    plants once per family is what somebody reading eight V2S storage types actually means, and
    setting the same list on eight tags by hand is the thing this exists to stop.

    THE MOST SPECIFIC PICK WINS - a tag's own list is used INSTEAD OF its family's, not ANDed with
    it. Two explicit lists ANDed would be their intersection, and two picks that do not overlap
    would blank the column while both controls on screen showed plants ticked. A reader who set a
    list on the tag said what they meant about the tag.

    THE LEGACY `v2r_plants` IS NOT PART OF THAT RULE. It is applied inside the core, where it has
    always been, so a report saved before family picks existed reads back exactly as it did; when
    the V2R family control is next touched the pick moves into `family_plants` and there is one
    list again.

    ANDed onto the FINISHED predicate, whatever shape it has, which is what lets one line serve
    every kind of tag: the pick is a test on the row's own ST_CD and knows nothing about KPIs, so
    it composes with a plain tag, a split, a rule tag and a composite alike. A pick on a COMPOSITE
    therefore narrows all of its members at once, and a member carrying a pick of its own narrows
    further from there - both apply, which is the only reading that cannot silently widen either.

    NO `pt` JOIN, exactly like the V2R list: it reads the fact's own ST_CD. See tag_needs_plant.

    The rest of the contract is unchanged:

    Four shapes behind one call, which is what lets every caller write
    `SUM(CASE WHEN {tag_predicate(t)} THEN ...)` and stop caring which kind of tag it has:

        plain      KPI = '0001'
        split      KPI = '0099' AND (pt.PLANT_TYPE = 'STORE')
        composite  ((...) OR (...) OR ...)   over its members, each resolved the same way
        rule       KPI = 'LQ_' || RIGHT(ST_CD, 3) AND ...   - the KPI it reads depends on the row

    A PICK TAG HAS NO PREDICATE and raises. It is not a selection of rows: MSA is the first
    NON-ZERO of two finished aggregates, and the OR of its members' predicates - the only predicate
    that could be written here - would SUM them, which is a different number under the same name.
    Raising is the point: the wrong answer would be plausible and silent. Use tag_measure_sql().

    A predicate naming `pt` requires plant_lookup_sql() joined under that alias in the SAME scope -
    ask tag_needs_plant() before building the FROM."""
    pred = _tag_predicate_core(tag, kpi_col, store_only, dc_only, v2r_plants, tag_plants,
                               family_plants)
    own = (tag_plants or {}).get(tag)
    if not own and family_plants:
        fam = plant_family(tag)
        own = family_plants.get(fam) if fam else None
    picked = plant_pick_pred(own, kpi_col)
    return f"({pred} AND ({picked}))" if picked else pred


def _tag_predicate_core(tag, kpi_col, store_only, dc_only, v2r_plants, tag_plants,
                        family_plants=None):
    """tag_predicate without the tag's own plant pick - the four shapes, and nothing else.

    Split out so the pick is applied in ONE place for every shape. The recursions below go back
    through tag_predicate rather than through here, so a member's own pick is applied to that
    member as well."""
    if tag in PICK_TAGS:
        raise ValueError(f"{tag} is a PICK tag - it has no row predicate; use tag_measure_sql()")
    if tag in RULE_TAGS:
        # Parenthesised as a unit: this one is a conjunction, and a caller that ORs it with
        # another predicate would otherwise bind the AND tighter than the OR and silently widen it.
        return "(" + RULE_TAGS[tag]["pred"](kpi_col, _row_col(kpi_col, "ST_CD")) + ")"
    if tag in COMPOSITE_TAGS:
        members = composite_members(tag)
        if not members:                      # every member still unloaded - select nothing
            return "FALSE"
        return "(" + " OR ".join(
            f"({tag_predicate(m, kpi_col, store_only, dc_only, v2r_plants, tag_plants, family_plants)})"
            for m in members) + ")"
    kpi, bucket = resolve_tag(tag)
    base_pred = f"{kpi_col} = {_q(kpi)}"
    if bucket:
        base_pred = f"{base_pred} AND ({bucket})"
    # The family switch - and NEVER on a tag that already carries a bucket. A split is an explicit
    # request for one tier, so ANDing STORE onto '0099_DC' yields
    # `ST_TYP = 'DC' AND ST_TYP = 'STORE'`, which is FALSE on every row: the DC, HUB, OTHER and
    # UNMAPPED columns would go silently, permanently blank and read as "no stock".
    if store_only and bucket is None and kpi in _ET_TAG_SET:
        base_pred = f"({base_pred} AND ({STORE_ONLY_PRED}))"
    # The V2R narrowing, and the same two guards as above: never on a tag that already names a
    # tier, and only on the family it is about. A chosen LIST wins over the DC rule when both are
    # present - an explicit pick is the reader saying what they mean, and silently ANDing a rule
    # onto it would make a picked hub vanish with nothing on screen explaining why.
    if bucket is None and kpi in _V2R_TAG_SET:
        picked = plant_pick_pred(v2r_plants, kpi_col)
        if picked:
            base_pred = f"({base_pred} AND ({picked}))"
        elif dc_only:
            base_pred = f"({base_pred} AND ({DC_ONLY_PRED}))"
    return base_pred

def _tag_sum_sql(tag, measure, kpi_col, store_only, dc_only=False, v2r_plants=None,
                 tag_plants=None, family_plants=None):
    """One tag's conditional SUM for one measure - the expression every stock column was, before a
    pick tag needed to be built out of two of them."""
    return (f"SUM(CASE WHEN {tag_predicate(tag, kpi_col, store_only, dc_only, v2r_plants, tag_plants, family_plants)} "
            f"THEN {_row_col(kpi_col, measure)} END)")


def tag_measure_sql(tag, measure, kpi_col="s.KPI", store_only=False, multi_store=False,
                    dc_only=False, v2r_plants=None, tag_plants=None, family_plants=None):
    """THE one place a (tag, measure) becomes an aggregate expression. Rounded, no alias.

    Every kind but one is `ROUND(SUM(CASE WHEN <predicate> THEN <measure> END),4)`, which is what
    both engines wrote inline until PICK_TAGS arrived. A pick is not a predicate at all - it is a
    choice BETWEEN finished aggregates - so it cannot be expressed as one, and the two engines
    calling this instead of writing the SUM themselves is what stops them disagreeing about what
    MSA means.

    FIRST NON-ZERO, spelled COALESCE(NULLIF(a,0), NULLIF(b,0), a, b):
        the NULLIFs pick the first member that is neither NULL nor 0 - COALESCE(0,5) = 5;
        the bare members behind them keep a genuine 0 printing as 0 rather than as a dash;
        all four NULL - no member had a row - leaves the cell blank, which is the honest answer.
    The member SUMs appear twice in the text and once in the plan: Snowflake folds the repeated
    expression, so this costs no extra scan.

    `multi_store=True` SAYS THE AGGREGATE MAY COVER SEVERAL STORES, and it changes a pick into the
    UNION of its members. This is the whole of what made these tags safe to offer in the MIS report
    builder, where the grain is whatever the report groups by:

        MSA's members are disjoint BY LOCATION - LQ_E01 is DH24's rack, LQ_E03 is DW01's (measured
        17-Sep-2026: 401,995 store x article rows, NOT ONE carrying both). So for a single store s,
        pick(s) = E01(s) + E03(s), and summing that over the stores in a group gives exactly
        SUM(E01) + SUM(E03) - the union below. First-non-zero over a MULTI-STORE group is a
        different number entirely: it returns DH24's rack and silently drops DW01's.

    So the two spellings are the same number wherever a row carries one store, and the union is the
    one that stays right when it does not. A caller that knows its grain has the store (the variant
    grid; a report grouped by ST_CD) may leave this False and get the pick the spec asks for.
    """
    if tag in PICK_TAGS:
        members = pick_members(tag)
        if not members:                      # every member still unloaded - nothing to choose from
            return "CAST(NULL AS NUMBER(38,4))"
        # A PLANT PICK ON THE PICK TAG ITSELF is inherited by its members, and it has to happen
        # here: tag_predicate never sees a pick tag (it raises for one), so a pick keyed on MSA
        # would otherwise be accepted, saved, and silently do nothing.
        #
        # A MEMBER'S OWN PICK WINS over the inherited one rather than being intersected with it.
        # Two lists cannot be ANDed into one IN clause, and an empty intersection would have to
        # mean "no plants" while an empty list already means "no narrowing" everywhere else -
        # the one reading that could silently widen a filter. Whoever picked plants for the
        # member said what they meant about the member.
        own = (tag_plants or {}).get(tag)
        if own:
            tag_plants = {**{m: own for m in members}, **(tag_plants or {})}
        sums = [_tag_sum_sql(m, measure, kpi_col, store_only, dc_only, v2r_plants, tag_plants,
                             family_plants)
                for m in members]
        if multi_store:
            # ONE conditional SUM over every member's KPI, not the sum of the member SUMs: the
            # inner CASEs are mutually exclusive by construction, so this is the same arithmetic in
            # one pass, and it keeps NULL (rather than 0) where no member had a row at all.
            pred = " OR ".join(
                f"({tag_predicate(m, kpi_col, store_only, dc_only, v2r_plants, tag_plants, family_plants)})"
                for m in members)
            return (f"ROUND(SUM(CASE WHEN {pred} "
                    f"THEN {_row_col(kpi_col, measure)} END),4)")
        terms = [f"NULLIF({s}, 0)" for s in sums] + sums
        return f"ROUND(COALESCE({', '.join(terms)}),4)"
    return f"ROUND({_tag_sum_sql(tag, measure, kpi_col, store_only, dc_only, v2r_plants, tag_plants, family_plants)},4)"


def tag_family(tag):
    """Which OVERLAPPING family a tag belongs to - the one thing that decides whether two tags may
    be added together.

    The fact carries the same stock several times over, regrouped: the ET storage locations, LQUA
    by storage TYPE (LQ_*), the same LQUA by LOCATION (LQL_*), a slice of it by BIN GROUP (LQB_*),
    and all of it again by WAREHOUSE x TYPE (SDC_/V2R_/V2S_). Two tags from ONE family are disjoint
    and add up; two from DIFFERENT families double-count, which is the warning STOCK_TAGS carries
    at the top of this file and the reason anything that totals tags has to ask this first."""
    if tag in PICK_TAGS:
        return "PICK"
    if tag in COMPOSITE_TAGS:
        return "TOTAL"
    if tag in RULE_TAGS:
        return "LQUA_TYPE"
    kpi = resolve_tag(tag)[0]
    if kpi in _ET_TAG_SET:
        return "ET_LGORT"
    if kpi in _WHN_SET:
        return "LQUA_WHN_TYPE"
    if kpi.startswith("LQL_"):
        return "LQUA_LGORT"
    if kpi.startswith("LQB_"):
        return "LQUA_BIN"
    if kpi.startswith("LQ_"):
        return "LQUA_TYPE"
    if kpi.endswith("_HUBPRD"):
        return "HUBPRD"
    if kpi.endswith("_PRD"):
        return "PRD"
    if kpi == "PTL":
        return "PTL"
    return "OTHER"


# ── THE GROUP ONE PLANT PICK MAY COVER ───────────────────────────────────────────────────────
#
# tag_family above answers a DIFFERENT question - which tags may be ADDED together - and answers it
# too coarsely to pick plants by: SDC_*, V2R_* and V2S_* are one family there because they are the
# same stock regrouped by warehouse x storage type, and adding two of them double-counts. But they
# are THREE WAREHOUSES, and "which plants count as ours" is asked of each one separately: V2R is 27
# plants, V2S six, SDC the stores' own.
#
# So this splits by WAREHOUSE NUMBER, which is what a plant pick is actually about, and leaves the
# other groups as they are. The names are the ones a reader already sees in the tag: ET for the
# storage locations (0001 ... PSA1), then SDC / V2R / V2S, LQ / LQL / LQB, PRD, HUBPRD.
#
# A COMPOSITE OR A PICK TAG HAS NO FAMILY. It spans several by construction, and its members are
# resolved through tag_predicate one at a time - so each leaf answers for itself and a family pick
# lands on exactly the members it is about. Returning a family here would apply one warehouse's
# plant list to the ET and PRD members of TOTAL_STORE_STOCK as well.
def plant_family(tag):
    """The family a plant pick may be applied to at once, or None for a tag that spans several."""
    if tag in PICK_TAGS or tag in COMPOSITE_TAGS:
        return None
    # A rule tag reads a KPI that depends on the row (LQ_V02 -> LQ_<store>), so its NAME is what
    # says which family it belongs to.
    if tag in RULE_TAGS:
        return tag.split("_", 1)[0] if "_" in tag else tag
    kpi = resolve_tag(tag)[0]
    if kpi in _ET_TAG_SET:
        return "ET"
    if kpi in _WHN_SET:
        return kpi.split("_", 1)[0]                  # SDC / V2R / V2S
    for pre in ("LQL_", "LQB_", "LQ_"):
        if kpi.startswith(pre):
            return pre[:-1]
    if kpi.endswith("_HUBPRD"):
        return "HUBPRD"
    if kpi.endswith("_PRD"):
        return "PRD"
    return kpi                                       # PTL and anything else stands alone



def tag_kpis(tag):
    """The KPI values a tag actually reads, deduped and in order.

    What the SCAN needs, as opposed to what the CASE needs: '0099_STORE' reads KPI '0099' and
    TOTAL_STORE_STOCK reads thirteen of them. Feeding a split or composite NAME to a KPI filter
    matches nothing - there is no row in the fact stamped '0099_STORE'.

    A RULE tag returns NOTHING, because there is no list to return: which KPIs it reads is a fact
    about the store master and about which storage types SAP created this week, not about anything
    this module can enumerate. Callers narrow it with tag_scan_pred() instead, and one that only
    calls this will scan wide rather than read short - the safe direction of the two.

    A PICK tag returns its members' KPIs, exactly like a composite. The scan is the same question
    for both - which rows have to be read - and it does not care that one adds them up and the
    other chooses between them."""
    if tag in RULE_TAGS:
        return []
    if tag in PICK_TAGS:
        members = pick_members(tag)
    else:
        members = [tag] if tag not in COMPOSITE_TAGS else composite_members(tag)
    out, seen = [], set()
    for t in members:
        k = resolve_tag(t)[0]
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def tag_scan_pred(tag, kpi_col="KPI"):
    """How to narrow the SCAN for a tag that cannot be narrowed by a KPI list. None for every
    other kind, which means "use tag_kpis()".

    Two ways of saying the same thing to two different readers: a list of names where one exists,
    a prefix test where it does not. A caller ORs whichever it gets from each requested tag, so a
    report asking for '0001' and LQ_V02 together reads `KPI IN ('0001') OR STARTSWITH(KPI, 'LQ_')`.

    NO `pt` HERE. This runs in the day-picker too, which groups the raw fact and joins no master."""
    if tag in RULE_TAGS:
        return RULE_TAGS[tag]["scan"](kpi_col)
    return None


def scan_filter(tags, kpi_col="KPI"):
    """The whole scan-narrowing clause for a set of tags, or '' for no narrowing at all.

    ONE place, because the two things that must agree about it - the day-picker and the aggregate
    that joins to it - are eighty lines apart, and a picker that read fewer KPIs than the aggregate
    would quietly decide the stock day from the wrong source."""
    kpis, preds = [], []
    for t in tags:
        p = tag_scan_pred(t, kpi_col)
        if p is not None:
            if p not in preds:
                preds.append(p)
            continue
        for k in tag_kpis(t):
            if k not in kpis:
                kpis.append(k)
    terms = ([f"{kpi_col} IN (" + ", ".join(_q(k) for k in kpis) + ")"] if kpis else []) + preds
    return " AND (" + " OR ".join(terms) + ")" if terms else ""


def tag_needs_plant(tag, store_only=False, dc_only=False, v2r_plants=None):
    """Whether this tag's predicate names `pt` - true for a split, for a composite holding one,
    always for a rule tag, which tests the row's plant type and status, and for ANY ET tag once
    the stores-only switch is on.

    MUST mirror tag_predicate exactly. These two are the classic pair that drift: the predicate
    grows a `pt` term, this one does not, and the query fails to compile with an unknown alias -
    or worse, compiles against some other `pt` in scope.

    `tag_plants` IS DELIBERATELY NOT A PARAMETER. A per-tag plant pick tests the fact's own ST_CD
    (plant_pick_pred), so it can never put `pt` into a predicate - the same reason the V2R list
    does not, and the reason both are cheap on the report builder, where the join they avoid is
    against a 795-row master on every stock row."""
    if tag in RULE_TAGS:
        return True
    if tag in PICK_TAGS:
        return any(tag_needs_plant(m, store_only, dc_only, v2r_plants)
                   for m in pick_members(tag))
    if tag in COMPOSITE_TAGS:
        return any(tag_needs_plant(m, store_only, dc_only, v2r_plants)
                   for m in composite_members(tag))
    kpi, bucket = resolve_tag(tag)
    # `bucket is not None` short-circuits the same case tag_predicate skips, so the two agree
    # whether or not the switch is on.
    # A chosen plant list needs NO join - it tests the fact's own ST_CD - so only the DC rule, and
    # only when no list overrides it, can put `pt` into a V2R predicate. Mirrors tag_predicate
    # exactly; these two drifting is how a query ends up naming an alias that is not in the FROM.
    return (bucket is not None
            or (store_only and kpi in _ET_TAG_SET)
            or (dc_only and not plant_pick_pred(v2r_plants, "x.KPI") and kpi in _V2R_TAG_SET))


def plant_lookup_sql():
    """One row per store code, carrying its plant type and its status. Join as `pt`.

    TWO columns off the MIS master (PLANT_TBL, all 795 rows). It is the same source as STORE_TBL
    now and stays a separate lookup on purpose: `pt` must never be filtered to trading stores,
    because a plant-type split whose source has been narrowed to STORE can only ever return STORE
    and the DC and HUB columns go silently, permanently empty. Measured 16-Sep-2026 the MIS master
    carries the same 8 distinct plant types on all 795 rows, now under the name ST_TYP.

    ST_STAT is here for the rule tags (RULE_TAGS), which exclude a closed store; the plant-type
    splits ignore it. Carried in the same subquery rather than a second lookup - it is the same
    master row, keyed the same way, so reading it twice would buy nothing.

    BOTH COLUMNS ARE REFERENCED THROUGH PLANT_COL / STATUS_COL, never spelled inline. db-scripts/377
    renamed them out from under this query once (PLANT_TYPE -> ST_TYP, STATUS -> ST_STAT) and the
    only thing that made that a two-line change was that the names lived in one place."""
    return (f"(SELECT TRIM(ST_CD) ST_CD, TRIM(NVL({PLANT_COL}, '')) {PLANT_COL}, "
            f"TRIM(NVL({STATUS_COL}, '')) {STATUS_COL} FROM {PLANT_TBL} "
            f"QUALIFY ROW_NUMBER() OVER (PARTITION BY TRIM(ST_CD) "
            f"ORDER BY UPDATED_AT DESC NULLS LAST) = 1)")


# The V2R plants, cached for the session: which plants carry V2R stock, and what the master says
# each one is. Cheap and bounded - one day of the fact pruned to the V2R KPIs, joined to a 795-row
# master - and cached because a catalogue call happens on every page load while this list changes
# when a warehouse is reorganised.
_V2R_PLANT_CACHE = {"at": 0.0, "rows": None}
_V2R_PLANT_TTL_S = 30 * 60


def v2r_plants_catalogue():
    """[{cd, typ, stat, nm}] for every plant carrying V2R stock on the latest loaded day.

    FROM THE FACT, not from a literal: the point of the picker is that the reader decides which
    plants are "the DCs", and a hard-coded list here would quietly decide it for them again. The
    master supplies the type and the trading status so the picker can label them and offer the
    DC ones as a preset."""
    import time as _t
    if _V2R_PLANT_CACHE["rows"] is not None             and (_t.time() - _V2R_PLANT_CACHE["at"]) < _V2R_PLANT_TTL_S:
        return _V2R_PLANT_CACHE["rows"]
    rows = []
    try:
        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute(f"""
                WITH d AS (SELECT MAX(DT) DT FROM {STOCK_TBL}),
                     p AS (SELECT DISTINCT TRIM(f.ST_CD) ST_CD
                             FROM {STOCK_TBL} f, d
                            WHERE f.DT = d.DT AND STARTSWITH(f.KPI, 'V2R_')),
                     m AS (SELECT TRIM(ST_CD) ST_CD, TRIM(NVL({PLANT_COL},'')) TYP,
                                  TRIM(NVL({STATUS_COL},'')) STAT, TRIM(NVL(ST_NM,'')) NM
                             FROM {PLANT_TBL}
                           QUALIFY ROW_NUMBER() OVER (PARTITION BY TRIM(ST_CD)
                                                      ORDER BY UPDATED_AT DESC NULLS LAST) = 1)
                SELECT p.ST_CD, NVL(m.TYP,'UNMAPPED'), NVL(m.STAT,''), NVL(m.NM,'')
                  FROM p LEFT JOIN m ON m.ST_CD = p.ST_CD
                 ORDER BY 2, 1""")
            rows = [{"cd": r[0], "typ": r[1], "stat": r[2], "nm": r[3]} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        # An empty list still saves and still runs: no picks means no narrowing, which is the
        # behaviour every report had before this existed.
        logger.warning("[sf-var-combined] V2R plant list unavailable: %s", e)
    _V2R_PLANT_CACHE["at"], _V2R_PLANT_CACHE["rows"] = _t.time(), rows
    return rows


# EVERY PLANT IN THE MASTER, cached for the session. 795 rows off a 795-row table, which is why
# this can ride along with /catalogue and every plant picker can open with no fetch at all.
#
# WHY NOT "WHICH PLANTS CARRY THIS TAG". That was the first cut of this, and per tag it is a
# genuinely better list - 0001 sits on 501 of the 795, V2S on four. But it has to read the FACT:
# one day of DAY_WISE_ST_VAR_STOCK pruned to the tag's KPIs is 92 MB scanned, and although the
# query itself lands in half a second (measured 18-Sep-2026: 0.5s and 0.8s on AKS_WH_L), it is
# paid on a FRESH Snowflake connection, once per picker opened, behind two proxy hops - so the
# menu sat on "reading the plants..." for seconds every time somebody opened one.
#
# The master answers instantly and is the same question asked one step earlier: these are the
# plants that exist. A plant that carries none of the tag simply returns nothing when it is
# picked, which is the honest answer rather than a slow way of hiding the option.
_ALL_PLANT_CACHE = {"at": 0.0, "rows": None}


def all_plants_catalogue():
    """[{cd, typ, stat, nm}] for every plant the MIS master knows, DC / HUB / OFFICE / STORE first
    by type then by code - the order a warehouse pick reads in.

    The same dedupe every other reader of this master uses (latest UPDATED_AT per code), so the
    type and the trading status here are the ones the predicates are evaluated against."""
    import time as _t
    if _ALL_PLANT_CACHE["rows"] is not None and (_t.time() - _ALL_PLANT_CACHE["at"]) < _V2R_PLANT_TTL_S:
        return _ALL_PLANT_CACHE["rows"]
    rows = []
    try:
        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute(f"""
                SELECT TRIM(ST_CD), TRIM(NVL({PLANT_COL},'UNMAPPED')),
                       TRIM(NVL({STATUS_COL},'')), TRIM(NVL(ST_NM,''))
                  FROM {PLANT_TBL}
                QUALIFY ROW_NUMBER() OVER (PARTITION BY TRIM(ST_CD)
                                           ORDER BY UPDATED_AT DESC NULLS LAST) = 1
                 ORDER BY 2, 1""")
            rows = [{"cd": r[0], "typ": r[1], "stat": r[2], "nm": r[3]} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        # An empty list still saves and still runs: no picks means no narrowing, which is what
        # every report did before plant picks existed. The picker says the list could not be read.
        logger.warning("[sf-var-combined] plant master unavailable: %s", e)
    _ALL_PLANT_CACHE["at"], _ALL_PLANT_CACHE["rows"] = _t.time(), rows
    return rows


def _split_cols(k_tags):
    """(alias, source column, bucket predicate) for every plant-type split column in scope.

    Only for tags the caller actually asked for: a split of a tag that is not in the result has no
    source column to read, and emitting it would hand back a block of guaranteed nulls."""
    out = []
    for t in SPLIT_TAGS:
        if t not in k_tags:
            continue
        for bucket, pred in PLANT_BUCKETS:
            for m in STOCK_MEASURES:
                out.append((f"K_{t}_{bucket}__{m}", f"K_{t}__{m}", pred))
    return out

# ── DAY REF, the CATEGORY-level reference facts (AKS_SILVER.SLV_FCT_DAY_REF) ──────────────────
#
# The same table the AKASH SIR MODULE > Day Ref Data page browses, joined on here so a variant row
# can be read against what its whole category did last year - same period (LYSP) and same month
# (LYSM), on sale, GM and average stock.
#
# ONLY THE LY FAMILIES COME FROM HERE. This year's figures (ACT) are computed per ARTICLE instead,
# from the proc's own sources - see ART_PREFIX below for why that split is a property of the
# merchandise rather than a shortcut.
#
# ITS GRAIN IS ST_CD x MAJ_CAT x CAL_DATE. IT HAS NO ARTICLE COLUMN.
# So these are CATEGORY numbers repeated across every variant of that store-category-day, exactly
# as they stand in the source - not allocated down to the article, which would have been a derived
# split presented as a measurement. That makes them CONTEXT, never measures:
#
#   * they are NOT summable down the column. Two variants of the same category on the same day
#     carry the SAME category figure; adding them counts the category twice. /totals therefore
#     never includes them (see _summable), and the grid renders them in their own colour.
#   * a value ties back to the Day Ref Data page cell for cell, which is the whole point of
#     carrying them as-is.
#
# COVERAGE IS PARTIAL AND THAT IS NOT A BUG. Measured 01-Sep-2026: the variant fact saw 410 stores
# and 529 categories that day, SLV_FCT_DAY_REF only 238 and 412 - so 43% of the grid's
# (store, category) keys find a ref row and the rest come back blank. The missing side is DCs,
# hubs and stores/categories the display plan does not cover. A dash here means "this store-category
# is not in the reference plan", which is a different statement from "it sold nothing".
REF_TBL = "V2RETAIL.AKS_SILVER.SLV_FCT_DAY_REF"
# The two reference DATES the row was built from - which day last year LYSP and LYSM actually read.
# Carried because the gap is not a constant 364 days (it is 354-385, set per store by
# SLV_LYSP_MASTER_PLANNING), so without them a LYSP figure cannot be traced back to a day.
REF_DATE_COLS = ["LYSP_DATE", "LYSM_DATE"]
# All fifteen, in the source table's own order: ACT (this year), LYSP, LYSM x sale Q, sale V,
# GM value, average stock Q, average stock V.
REF_MEASURES = ["ACT_SALE_Q", "ACT_SALE_V", "ACT_GM_VAL", "ACT_AVG_STK_Q", "ACT_AVG_STK_V",
                "LYSP_SALE_Q", "LYSP_SALE_V", "LYSP_GM_VAL", "LYSP_AVG_STK_Q", "LYSP_AVG_STK_V",
                "LYSM_SALE_Q", "LYSM_SALE_V", "LYSM_GM_VAL", "LYSM_AVG_STK_Q", "LYSM_AVG_STK_V"]
# Not a stored column: computed live, as on the Day Ref Data page. See _ref_sql.
REF_DERIVED = ["LYSP_DAY_CONTRI"]
# Prefixed R__ so the shape says what it is, the same way S_ means sale and K_ means stock. The
# page keys its formatting and its colour off that prefix, so a measure added above needs no
# frontend change.
REF_PREFIX = "R__"
# THE ACT FAMILY IS SPLIT OUT AND COMPUTED PER ARTICLE.
#
# ACT is THIS YEAR, so unlike LYSP and LYSM it genuinely divides across the articles on the row:
# a store-category that sold Rs 1,000 today sold it as some split across the articles under it,
# and each article's share is a real number this page can show. The proc reads exactly that - it
# aggregates SAP_VBRP line by line and only collapses to MAJ_CAT at the very end - so the split
# needs no invention, only stopping one step earlier. These carry the A__ prefix, add up, and are
# subtotalled like any other measure.
#
# LYSP AND LYSM CANNOT BE SPLIT THE SAME WAY, AND THAT IS A FACT ABOUT THE MERCHANDISE, NOT A
# LIMITATION HERE. Measured at HB05 / M_JEANS, 01-Sep-2026 against its LYSP day 12-Aug-2025:
#     13-digit articles   57 sold this year, 53 last year,  0 the same
#     generic articles    44 this year,      49 last year,  1 the same
#     colours             19 this year,      17 last year,  0 the same
# Last year's Rs 38,738 does divide across articles - across last year's 53, which are not this
# year's 53. A per-article LYSP column would therefore read 0 against every article now selling,
# and last year's articles would have to join as extra rows carrying LY and no current sale,
# roughly doubling the grid with rows that never compare to anything. So the LY families stay at
# the grain the comparison actually exists at - the category - and keep the R__ prefix and the
# "do not total" treatment.
#
# The MIS report builder has no such problem and does not do this: there the reference is
# recomputed at article grain and aggregated to whatever the report groups by, because the
# aggregation happens ABOVE the article and both years land in the same bucket.
ART_PREFIX = "A__"
# SALE AND GM ONLY. Day Ref's ACT stock is LGORT 0001 + 0006 of ET_STOCK_DATA, and this page
# ALREADY carries exactly that, per article, as its own stock tags: DAY_WISE_ST_VAR_STOCK keys on
# KPI = LGORT with STOCK_V = SUM(LABST_DMBTR) and STOCK_Q = SUM(LABST), which is the proc's stock
# branch line for line. So K_0001__STOCK_V + K_0006__STOCK_V IS the ACT stock - adding an A__ pair
# would have been the same number under a second name, and it cost the whole ET_STOCK_DATA scan
# (~19.7M rows a day) to produce. The scan is gone with it.
ART_MEASURES = ["ACT_SALE_Q", "ACT_SALE_V", "ACT_GM_VAL"]
ART_COLS = [ART_PREFIX + c for c in ART_MEASURES]
# What is left on the category side: the two LY families, their dates and the contribution.
REF_CAT_MEASURES = [m for m in REF_MEASURES if not m.startswith("ACT_")]
REF_COLS = [REF_PREFIX + c for c in REF_DATE_COLS + REF_CAT_MEASURES + REF_DERIVED]
# VERBATIM from planning-service/src/routes/akash/salePlan.js (DREF_CONTRI_EXPR), so the two pages
# cannot print two different contributions for the same cell: each day's LYSP sale value as a share
# of its own FULL CALENDAR MONTH for that store and category.
#
# "Full calendar month" is why _ref_sql scans whole months and trims back with QUALIFY. Filtering
# the rows to the report's dates BEFORE the window would divide by the days in the report instead
# of the days in the month - a 5-day range would show every contribution five times too big.
REF_CONTRI_EXPR = ("GREATEST(LYSP_SALE_V, 0) * 1.0 "
                   "/ NULLIF(SUM(GREATEST(LYSP_SALE_V, 0)) OVER ("
                   "PARTITION BY YEAR(CAL_DATE), MONTH(CAL_DATE), ST_CD, MAJ_CAT), 0)")


def _ref_sql(a, b, col_filters=None):
    """The Day Ref facts for the report's own days, one row per (CAL_DATE, ST_CD, MAJ_CAT).

    Verified unique on that key (3,564,740 rows / 3,564,740 keys over 01-Aug -> 03-Sep-2026), so the
    join in _build cannot fan out - which matters here more than usual, since a fan-out on this side
    would multiply the SALE columns, not just the reference ones.

    The scan is widened to whole calendar months for REF_CONTRI_EXPR's window and narrowed back by
    QUALIFY, which runs after the window function. Cheap: the table is clustered on CAL_DATE and a
    month is ~3.5M rows.
    """
    m_lo = f"DATE_TRUNC('MONTH', {_q(a)}::DATE)"
    m_hi = f"LAST_DAY({_q(b)}::DATE)"
    # The report's store scope, pushed into the scan. Without it a one-store grid still reads a
    # whole month of every store's reference rows AND runs the contribution's window function over
    # all of them, only to discard the lot after the join. The outer WHERE applies the same
    # predicate, so this changes nothing but how much is read.
    st = _store_pred(col_filters, "ST_CD")
    sel = [f'{c} AS "{REF_PREFIX}{c}"' for c in REF_DATE_COLS + REF_CAT_MEASURES]
    sel.append(f'ROUND({REF_CONTRI_EXPR}, 6) AS "{REF_PREFIX}LYSP_DAY_CONTRI"')
    return (f"(SELECT CAL_DATE, TRIM(ST_CD) ST_CD, TRIM(MAJ_CAT) MAJ_CAT, {', '.join(sel)} "
            f"FROM {REF_TBL} WHERE CAL_DATE BETWEEN {m_lo} AND {m_hi}{st} "
            f"QUALIFY CAL_DATE BETWEEN {_q(a)}::DATE AND {_q(b)}::DATE "
            # Belt as well as braces. The key is unique in the source as it stands (measured), but
            # this side is TRIMmed to join, and a trim can only ever MERGE keys - so the guarantee
            # the join needs is about the trimmed key, not the stored one. Newest write wins, the
            # same rule sm_sql uses for the store master.
            f"AND ROW_NUMBER() OVER (PARTITION BY CAL_DATE, TRIM(ST_CD), TRIM(MAJ_CAT) "
            f"ORDER BY UPDATE_DT DESC NULLS LAST, CREATE_DT DESC NULLS LAST) = 1)")


# ── ACT, PER ARTICLE: the reference proc's own sale and stock logic, one step short of its
#    final GROUP BY ──────────────────────────────────────────────────────────────────────────
ART_CAL_TBL = "V2RETAIL.AKS_SILVER.SLV_LYSP_MASTER_PLANNING"
ART_VBRP = "V2RETAIL.BRONZE.SAP_VBRP"
ART_DIM_PROD = "V2RETAIL.BRONZE.DIM_PRODUCT"
ART_VBRP_DATE = ("COALESCE(TRY_TO_DATE(v.FKDAT_ANA, 'YYYYMMDD'), "
                 "TRY_TO_TIMESTAMP(v.FKDAT_ANA)::DATE)")


def _store_pred(col_filters, col):
    """The report's ST_CD filter as a literal IN list, for pushing INTO a source scan.

    The outer WHERE already applies it, so this changes no result - it changes WHEN it is applied.
    Without it a report scoped to one store still reads every store out of VBRP and the stock
    snapshot and throws the rest away after the join, which is the whole cost of the query.
    """
    vals = [str(v) for v in ((col_filters or {}).get("ST_CD") or []) if str(v).strip()]
    if not vals:
        return ""
    return f" AND {col} IN ({', '.join(_q(v) for v in vals)})"


def _art_act_sql(a, b, col_filters=None):
    """Day Ref's ACT measures at DT x ST_CD x ARTICLE_NUMBER, from the proc's own sources.

    WHY NOT JUST USE THIS PAGE'S OWN SALE COLUMNS? Because they are not the same number, and the
    difference is not rounding. The S_* columns read DAY_WISE_ST_VAR_SALE, which is backfilled per
    date range and can be short on a day the backfill did not fully cover; these read SAP_VBRP,
    which is the billing document itself. Measured at HB05 / M_JEANS on 12-Aug-2025: VBRP
    Rs 38,738.78, the day-wise fact Rs 36,264.52 over 50 rows against 53 billing lines. So this
    column is also the check on the other one - where A__ACT_SALE_V exceeds TD Sale V, that day's
    day-wise load is incomplete.

    Transcribed from SP_AKS_DAY_REF_LOAD (db-scripts/250): FKART_ANA='FP'; quantity negates on
    SHKZG='X' while NETWR and WAVWR already carry the credit sign; GM = NETWR - WAVWR. The
    calendar spine is only needed for the LY branches, so ACT reads the row's own date directly and
    skips it, and the proc's stock branch is already on this page as its 0001 / 0006 stock tags.

    The article bridge is DIM_PRODUCT with the day-wise loader's own rule (db-scripts/298), so the
    ARTICLE_NUMBER here is the same one the rest of the row is keyed by.
    """
    st_v = _store_pred(col_filters, "v.WERKS")
    return f"""(
        WITH ap AS (
            SELECT mat_key, ARTICLE_NUMBER FROM (
                SELECT LTRIM(MATNR,'0') mat_key, TO_NUMBER(ARTICLE_NUMBER) ARTICLE_NUMBER,
                       ROW_NUMBER() OVER (PARTITION BY LTRIM(MATNR,'0')
                                          ORDER BY MODIFY_DATE DESC NULLS LAST) rn
                  FROM {ART_DIM_PROD}
                 WHERE MATNR IS NOT NULL AND TRIM(NVL(MAJ_CAT,'')) <> ''
                   AND ARTICLE_NUMBER IS NOT NULL AND GEN_ART_NUMBER IS NOT NULL)
             WHERE rn = 1),
        asale AS (
            SELECT {ART_VBRP_DATE} DT, v.WERKS ST_CD, p.ARTICLE_NUMBER,
                   SUM(CASE WHEN NVL(v.SHKZG,'') = 'X'
                            THEN -TRY_TO_DOUBLE(TO_VARCHAR(v.FKIMG))
                            ELSE  TRY_TO_DOUBLE(TO_VARCHAR(v.FKIMG)) END) Q,
                   SUM(TRY_TO_DOUBLE(TO_VARCHAR(v.NETWR))) V,
                   SUM(TRY_TO_DOUBLE(TO_VARCHAR(v.NETWR))
                     - TRY_TO_DOUBLE(TO_VARCHAR(v.WAVWR))) GM
              FROM {ART_VBRP} v JOIN ap p ON p.mat_key = LTRIM(v.MATNR,'0')
             WHERE v.FKART_ANA = 'FP'
               AND {ART_VBRP_DATE} BETWEEN {_q(a)}::DATE AND {_q(b)}::DATE{st_v}
             GROUP BY 1, 2, 3)
        SELECT sa.DT, sa.ST_CD, sa.ARTICLE_NUMBER,
               ROUND(sa.Q,4)  AS "{ART_PREFIX}ACT_SALE_Q",
               ROUND(sa.V,4)  AS "{ART_PREFIX}ACT_SALE_V",
               ROUND(sa.GM,4) AS "{ART_PREFIX}ACT_GM_VAL"
          FROM asale sa)"""


# ── GRC AND PO: what came IN, beside what went out ────────────────────────────────────────────
#
# THE TWO SOURCES AND THE ONE DIFFERENCE BETWEEN THEM, which decides everything below.
#
#   VW_GRC_REPORT_AKA      a movement LEDGER - one row per goods-receipt line, stamped with the
#                          day it posted. 2,498,610 rows, 2021-12-25 to today, measured
#                          22-Sep-2026. It is a day-wise flow at (day, plant, article), which is
#                          this grid's own grain, so it windows exactly like sale.
#   VW_PO_PENDING_NEW      the OPEN order book, and PENDING-ONLY: all 122,416 of its rows carry
#                          PENDING_PO_QUANTITY > 0, so a fully-received line is not in it - it has
#                          LEFT it. That is a POSITION as it stands now, with no history in it, and
#                          there is no window reading of it to give. "PO raised MTD" cannot be read
#                          from a table that keeps only what has not arrived yet.
#
# BOTH ARE OPT-IN, for the reason the A__ block is: every other column on this page comes from a
# pre-aggregated day-wise fact, and these are two more scans nobody should pay for on a page load
# they did not ask for them on.
#
# THE TABLE NAMES AND THE DATE FORMAT LIVE HERE rather than in sf_mis_report, which imports this
# module as `base`. The MIS report builder offers its own measure vocabulary over the same two
# views - it can afford a line count and a day count where this grid cannot - but the SOURCE must
# have one spelling, or the day the view is renamed only one of the two pages finds out.
GRC_TBL = "V2RETAIL.SILVER.VW_GRC_REPORT_AKA"
PO_TBL = "V2RETAIL.SILVER.VW_PO_PENDING_NEW"
# EX_FAC_DT and DOC_DT are DD.MM.YYYY TEXT. Passed explicitly wherever they are parsed, never left
# to Snowflake's AUTO date parse - which reads 01.02.2026 as January or February depending on what
# it has already seen in the column, the same trap the SRM TNA report walked into.
PO_DATE_FMT = "'DD.MM.YYYY'"

# THE SIGNS ARE ALREADY IN THE GRC DATA. Five movement types are present - 101 receipt, 102 its
# reversal, 122 return to vendor, 123 that return's reversal, 161 returns PO - and the reversing
# ones carry NEGATIVE GRC_QTY and GRC_VALUE. So a plain SUM is the NET receipt and needs no
# movement list to maintain. Gross and Returns split the SAME rows by the row's own SIGN rather
# than by a hard-coded set of movement types: GREATEST/LEAST cannot go stale when SAP stamps a
# sixth movement, and gross minus returns IS net by construction, at every grain.
GRC_PREFIX = "G_"
# SIX MEASURES, NOT THE BUILDER'S EIGHT. The MIS report's GRC block also offers a LINE COUNT and a
# DAY COUNT, and both earn their place there because a report groups - "how many receipt days did
# this category have" is a real question at category grain. Here the row IS one variant at one
# store on one day, so a line count is almost always 1 or 2 and a day count is always 1. Ten
# windows times two measures of no information is twenty columns of noise on a grid that already
# carries 292, so they are left out deliberately.
GRC_MEASURES = ["GRC_Q", "GRC_V", "GRC_GRS_Q", "GRC_GRS_V", "GRC_RET_Q", "GRC_RET_V"]
# (what to sum, which way up). Held as the SUMMAND rather than as a finished aggregate because the
# two readers wrap it differently - this grid puts a window's CASE inside the SUM, the MIS builder
# sums a whole day first and windows the daily totals - and the thing that must not have two
# spellings is the SIGN RULE, which is the only part either of them could get wrong. Returns come
# back NEGATED so the column reads as "how much came back" instead of as a minus sign the eye has
# to carry across the sheet; that is what the -1 is.
GRC_SRC = {
    "GRC_Q":     ("GRC_QTY", 1),
    "GRC_V":     ("GRC_VALUE", 1),
    "GRC_GRS_Q": ("GREATEST(GRC_QTY, 0)", 1),
    "GRC_GRS_V": ("GREATEST(GRC_VALUE, 0)", 1),
    "GRC_RET_Q": ("LEAST(GRC_QTY, 0)", -1),
    "GRC_RET_V": ("LEAST(GRC_VALUE, 0)", -1),
}


def grc_sum(measure, when=None, col=lambda c: c):
    """This GRC measure as a SUM, optionally over one window's days.

    `col` re-qualifies the source column for a caller whose row carries an alias - the MIS builder
    reads `f.GRC_QTY` off a fact-shaped subquery - so the sign rule above is applied in exactly one
    place however the column is spelled.
    """
    src, sign = GRC_SRC[measure]
    for c in ("GRC_QTY", "GRC_VALUE"):
        src = src.replace(c, col(c))
    body = f"SUM(CASE WHEN {when} THEN {src} END)" if when else f"SUM({src})"
    return ("-" if sign < 0 else "") + body

# PO. One column per measure and no window in the middle, but the name still spells O_PO__<measure>
# - the same wide name the MIS report builder produces - so a reader who knows one page knows the
# other, and a CSV from either lines up under the same header.
PO_PREFIX = "O_PO__"
PO_MEASURES = ["PO_PEND_Q", "PO_PEND_V", "PO_ORD_Q", "PO_ORD_V", "PO_GR_Q",
               "PO_OVD_Q", "PO_OVD_V", "PO_REL_Q", "PO_UNREL_Q", "PO_LINES"]
# EVERY ONE OF THEM IS "over the lines that are still open" - that is the view's whole population.
# So PO_ORD_Q is the ORDERED SIZE OF THE OPEN BOOK, not what was ordered this year, and
# ORD - GR = PEND by construction (measured exactly 0 across the whole book, 22-Sep-2026).
# PO_COUNT (distinct PO numbers) is deliberately absent: this grid's row is ONE article at ONE
# store, where a distinct count of purchase orders is a small integer that says nothing the line
# count does not. It stays on the MIS builder, where a report can group above the article.
# The summand, not a finished aggregate - same reasoning as GRC_SRC. None is the line count, which
# counts rows rather than summing a column.
PO_SRC = {
    "PO_PEND_Q": "PENDING_PO_QUANTITY",
    "PO_PEND_V": "PENDING_PO_VALUE",
    "PO_ORD_Q":  "ACTUAL_PO_QUANTITY",
    "PO_ORD_V":  "ACTUAL_PO_VALUE",
    "PO_GR_Q":   "GR_QTY",
    "PO_LINES":  None,
}


def po_sum(measure, col=lambda c: c):
    """This PO measure as an aggregate. `col` re-qualifies the source as in grc_sum."""
    src = PO_SRC[measure]
    return "COUNT(*)" if src is None else f"SUM({col(src)})"
# The four that need a predicate too: (source column, which test). The overdue test is completed by
# the caller, because it compares against that caller's own anchor - which in the MIS builder's
# view mode is an EXPRESSION that has to re-resolve on every read of the published view.
#
# NVL on the status so a row with none falls into UNRELEASE rather than out of BOTH halves: REL
# plus UNREL has to add back to PEND exactly, or the split silently loses lines.
PO_COND_AGG = {
    "PO_OVD_Q":   ("PENDING_PO_QUANTITY", "overdue"),
    "PO_OVD_V":   ("PENDING_PO_VALUE", "overdue"),
    "PO_REL_Q":   ("PENDING_PO_QUANTITY", "release"),
    "PO_UNREL_Q": ("PENDING_PO_QUANTITY", "unrelease"),
}


def po_preds(anchor_sql, col=lambda c: c):
    """The three PO predicates, resolved against one anchor. `col` re-qualifies as in grc_sum."""
    return {
        "overdue": f"TRY_TO_DATE({col('EX_FAC_DT')}, {PO_DATE_FMT}) < {anchor_sql}",
        "release": f"UPPER(TRIM(NVL({col('PO_STATS')},''))) = 'RELEASE'",
        "unrelease": f"UPPER(TRIM(NVL({col('PO_STATS')},''))) <> 'RELEASE'",
    }
GRC_COLS = [f"{GRC_PREFIX}{w}__{m}" for w in SALE_WINDOWS + ["RANGE"] for m in GRC_MEASURES]
PO_COLS = [PO_PREFIX + m for m in PO_MEASURES]

# BOTH BLOCKS ENRICH THE ROWS THIS GRID ALREADY HAS; NEITHER ADDS ANY.
#
# The base of this report is "a variant appears if it SOLD in the range OR HELD STOCK at the end of
# it" (see base_sql in _build). GRC and PO join onto that, LEFT - so a variant that was received or
# is on order but neither sold nor held stock has no row here to carry its receipt, and its GRC and
# PO are simply not on the page.
#
# THAT IS DELIBERATE, and it is the smaller error of the two available. Adding either source to the
# base union would change what this grid IS - a sale-and-stock report - and would pull in the DC's
# whole open order book as rows, 122k of them at 97% DC, most carrying nothing in any other column.
# A report that is about what sold and what is held should not silently become one about what is on
# order. The MIS report builder is where a PO-led or GRC-led reading belongs: there the blocks join
# FULL OUTER, so a key with a receipt and no sale is a row, which is exactly the row worth seeing
# when that is the question being asked.
#
# Measured 22-Sep-2026 for scale: on 21-Sep the GRC ledger touched 31,000-odd (plant, article) keys
# and 98% of them were DW01 or DH24, so on a STORE-scoped view of this page the two blocks are
# mostly blank - which is the estate, not the join. Goods receipt happens at the DCs.


# DT LEADS THE GRAIN. Both facts are stored per date, so the report is one row per DAY per store
# per variant - not one undated row per store x variant, which is what collapsing the day produced.
# ARTICLE_DESC, not GEN_ART_DESC. The loaders store VW_PRODUCT_MASTER.ARTICLE_DESCRIPTION here,
# which is the VARIANT text ending in size and colour ("..., L, WHT"); calling it GEN_ART_DESC put
# the article description under the generic name. The true generic description arrives separately,
# as enrichment - see GEN_ART_DESC in _build.
KEY_COLS = ["DT", "ST_CD", "ARTICLE_NUMBER", "GEN_ART_NUMBER", "ARTICLE_DESC",
            "COLOR", "SIZE_1", "MAJ_CAT", "SEG"]
# The fact column is still physically named GEN_ART_DESC; this aliases it on the way out. Renaming
# the table column would need an ALTER and a full reload for no gain.
_FACT_ALIAS = {"ARTICLE_DESC": "GEN_ART_DESC"}

# THE MASTER OWNS THE HIERARCHY, NOT THE FACT ROW.
# MAJ_CAT and SEG are physically ON the fact - the loaders stamp them from VW_PRODUCT_MASTER at load
# time (db-scripts/298, 300, 302) - but they are read from the master here, live, exactly like
# DIV / SUB_DIV / MC_CODE already were. The stamp is a SNAPSHOT of the day it was loaded; SUB_DIV
# was a LIVE lookup; so the instant SAP re-classified an article the two halves of the same ladder
# disagreed and one MAJ_CAT sprouted a second SUB_DIV.
#
# Measured, 2026-09-04: 6 articles moved from JG_H_ST to IG_ST on 2 Sep. Days up to 1 Sep still
# carried the old JG_H_ST stamp while SUB_DIV read the new KI, so JG_H_ST showed BOTH KG-U and KI on
# every window reaching back that far (MTD, QTR, YTD) and was clean on TD, which is only the 3 Sep
# rows - loaded after the change. 21,580 articles across the fact carry a stamp the master no longer
# agrees with, and 3,234 on the newest day alone, so this was never one bad category.
#
# Reading live means the whole ladder - SEG > DIV > SUB_DIV > MAJ_CAT - always comes from ONE row of
# ONE master, so it cannot self-contradict whatever the date range is. The cost is deliberate: a
# re-classification re-labels history, and last month's report re-run today files those articles
# under the category they are in NOW. That is the trade the alternative reverses - freezing the whole
# ladder onto the fact would keep history stable but needs a full reload of 127M rows, and would
# still disagree with every OTHER report that reads the master live.
#
# COALESCEd back to the fact stamp, never left NULL: 2 articles in the fact have no master row at
# all, and a live-only read would blank their category rather than show the last one known.
PROD_KEY_MAP = {"MAJ_CAT": "MAJOR_CATEGORY_DESCRIPTION", "SEG": "SEGMENT"}


def _key_expr(col):
    """The SQL for one KEY_COL. ONE definition, used by the projection, the filter AND the sort.

    They were three separate strings; the moment a column moved sides they could disagree, and a
    filter reading one value while the grid displays another is the exact bug this change is fixing.
    """
    if col in PROD_KEY_MAP:
        return f"COALESCE(pm.{col}, b.{col})"
    return f"b.{_FACT_ALIAS.get(col, col)}"

# Master enrichment. A RIGHT JOIN in effect: the fact row always survives and the masters only ADD
# columns. NOT an inner join - the MIS master is 795 locations and the stock fact carries 541, a
# handful of which have no master row at all. LOC_IN_MASTER flags those so a blank hierarchy reads
# as "the master has never heard of this location" rather than as missing data.
# It used to read 86 against the 666-row planning view and its "no" branch was labelled
# "NO - DC/hub", because on that source a DC or a hub was the usual reason to be missing. On the
# full MIS master they are IN it and carry a real hierarchy, so the label would now be a lie on
# every one of them - hence LOC_NO_LABEL below. "Is this a DC?" is the plant type's job now
# (PLANT_COL), and it is populated for all 795.
# EVERY column of both masters, not a chosen few. The report used to expose 9 of the store master's
# columns and 11 of the product master's 116, and the rest were simply unreachable. They are
# LITERALS rather than read from INFORMATION_SCHEMA at startup: column POSITION has to stay stable
# across exports, and a master gaining a column must not silently shift everyone's spreadsheet.
# That cuts the other way too, which db-scripts/377 proved: a master that CHANGES SHAPE does not
# quietly re-point this list, it breaks the query until someone rewrites it deliberately.
#
# Eight product columns are deliberately absent. ARTICLE_NUMBER and GEN_ARTICLE_NUMBER are the join
# keys; COLOR, SIZE1, MAJOR_CATEGORY_DESCRIPTION and SEGMENT are already report columns carried on
# the fact row; and ARTICLE_DESCRIPTION / GEN_ARTICLE_DESC are BYTE-IDENTICAL to each other on all
# 3,028,059 in-scope rows (measured), with GEN_ART_DESC on the fact row already carrying the text -
# so a separate "article description" column would be a duplicate, not a new field.
#
# Two store columns are absent for the same reason: ST_CD is the join key, and UPDATED_AT only
# picks which duplicate row survives.
#
# The eleven product attributes that already had report-side names (DIV, SUB_DIV, MC_CODE, ...)
# KEEP them, so an export taken before this change still lines up; everything new is exposed under
# the master's own column name.
# THE MIS MASTER'S OWN 21, REWRITTEN 16-Sep-2026 (db-scripts/377). This list held 45 columns of
# SLV_DIM_STORE, which is what SLV_MIS_STORE_MASTER was a clone of until that script rebuilt it on
# the MIS team's actual workbook. Of those 45, exactly four survive under the same name (ST_NM,
# RDC_CD, RDC_NM, STATE) and one of THOSE changed meaning - see the ST_NM note at STORE_TBL.
#
# WHAT LEFT, AND WHERE IT WENT:
#   STATUS, PLANT_TYPE, OP_DATE, AREA   renamed - ST_STAT, ST_TYP, ST_OP_DT, ST_AREA. Note AREA:
#       it still EXISTS in the master and is now a REGION CODE, so it is in this list carrying
#       something else entirely. A pre-377 export's "AREA" column is ST_AREA here.
#   CLUSTER, HUB_CD, HUB_NM, REF_*, the 14 festival flags, WINTER_GROUP, APF, PUJA_STAT, ASP,
#   BK_FLD_STATUS, IS_DIMAPUR_MODEL, the OLD_* block, REGION_GRP_OLD, WINTER_GRP_STAT
#       GONE. The MIS workbook has no column for any of them, so on the cloned table they were
#       either NULL or a stale copy of the 10-Sep seed. A column that can only ever be blank is
#       worse than an absent one: it reads as "this store has no cluster" rather than "this report
#       cannot tell you".
#   ZONE, REG, AREA, ST_FULL_NM, ST_PART, ST_FIX, AVG_AREA_PER_FIX, AVG_PSF, EXCESS_DAYS,
#   ST_CL_DT, ZONE_RETAIL, IN_TR_DAYS, OLD_NEW, ACT_ST_AREA
#       NEW, and the reason the rebuild is worth the churn - a geography (zone / region / area),
#       a productivity block and a lifecycle flag the report could not reach before.
#
# WHAT THIS DOES TO A SAVED REPORT: a col_filter on a departed column stops matching and comes back
# empty rather than erroring, because _filter_sql drops any column not in _ENRICH_FILT. An EXPORT
# taken before today does not line up column-for-column with one taken after; that is unavoidable
# when the underlying master changes shape, and the headers are the raw column names precisely so
# the difference is visible in the file rather than inferred.
#
# ST_CD and UPDATED_AT stay out for the reasons they always did: ST_CD is the join key and
# UPDATED_AT only picks which duplicate row survives. SN is out too - it is the workbook's row
# number, it is not stable across loads, and nothing may key on it.
# ST_TYP is NOT here, exactly as PLANT_TYPE was not: it is the plant-type column and comes off
# `pt` as a tail column (PLANT_EXPR), where 'UNMAPPED' can stand in for a location the master has
# never heard of. Listing it here too would emit it twice, once of them NULL on those rows.
STORE_ATTRS = [
    # identity
    "ST_FULL_NM", "ST_NM", "ST_PART",
    # geography
    "ZONE", "ZONE_RETAIL", "REG", "AREA", "STATE",
    # supply
    "RDC_CD", "RDC_NM",
    # status and lifecycle
    "ST_STAT", "OLD_NEW", "ST_OP_DT", "ST_CL_DT",
    # space and productivity
    "ST_AREA", "ACT_ST_AREA", "ST_FIX", "AVG_AREA_PER_FIX", "AVG_PSF",
    "EXCESS_DAYS", "IN_TR_DAYS",
]

PROD_ATTR_MAP = {
    "DIV": "DIVISION",
    "SUB_DIV": "SUB_DIVISION",
    "MC_CODE": "MERCHANDISE_CATEGORY_CODE",
    "MC_DESC": "MERCHANDISE_CATEGORY_DESCRIPTION",
    "MRP": "MRP",
    "REG_SEG": "RANGE_SEGMENT",
    "ART_TYPE": "ARTICLE_TYPE",
    "MACRO_MVGR": "M_MACRO_MVGR",
    "MAIN_MVGR": "M_MAIN_MVGR",
    "VENDOR_NAME": "VENDOR_NAME",
    "ART_STATUS": "ART_STATUS",
    "ARTICLE_CREATION_DATE": "ARTICLE_CREATION_DATE",
    "ARTICLE_COST": "ARTICLE_COST",
    "NATURE_OF_ARTICLE": "NATURE_OF_ARTICLE",
    "VENDOR_DESIGN": "VENDOR_DESIGN",
    "MAJOR_CATEGORY_CODE": "MAJOR_CATEGORY_CODE",
    "SUB_CATEGORY_CODE": "SUB_CATEGORY_CODE",
    "SUB_CATEGORY_DESCRIPTION": "SUB_CATEGORY_DESCRIPTION",
    "SIZE_DIMENSION": "SIZE_DIMENSION",
    "VENDOR_CODE": "VENDOR_CODE",
    "VENDOR_CITY": "VENDOR_CITY",
    "MERGE_VENDOR_CODE": "MERGE_VENDOR_CODE",
    "MERGE_VENDOR_NAME": "MERGE_VENDOR_NAME",
    "MERGE_VENDOR_CITY": "MERGE_VENDOR_CITY",
    "M_ACC_COL": "M_ACC_COL",
    "M_ADD_ACC": "M_ADD_ACC",
    "M_ADD_ACC1": "M_ADD_ACC1",
    "M_BLT_MAIN_STYLE": "M_BLT_MAIN_STYLE",
    "M_BLT_SIZE": "M_BLT_SIZE",
    "M_BTM_FOLD": "M_BTM_FOLD",
    "M_BTN_MAIN_MVGR": "M_BTN_MAIN_MVGR",
    "M_BUYING_TYPE": "M_BUYING_TYPE",
    "M_COLLAR": "M_COLLAR",
    "M_COLLAR_SIZE": "M_COLLAR_SIZE",
    "M_COUNT": "M_COUNT",
    "M_DC_EDGE_LOOP": "M_DC_EDGE_LOOP",
    "M_DC_SUB_STYLE": "M_DC_SUB_STYLE",
    "M_EMB_TYPE": "M_EMB_TYPE",
    "M_EMBROIDERY": "M_EMBROIDERY",
    "M_FAB": "M_FAB",
    "M_FAB_WEIGHT": "M_FAB_WEIGHT",
    "M_FAB2": "M_FAB2",
    "M_FIT": "M_FIT",
    "M_FO_BTN_STYLE": "M_FO_BTN_STYLE",
    "M_GSM": "M_GSM",
    "M_LENGTH": "M_LENGTH",
    "M_LYCRA": "M_LYCRA",
    "M_MAIN_STYLE": "M_MAIN_STYLE",
    "M_NECK_BAND": "M_NECK_BAND",
    "M_NECK_BAND_STYLE": "M_NECK_BAND_STYLE",
    "M_OUNZ": "M_OUNZ",
    "M_PATCH_TYPE": "M_PATCH_TYPE",
    "M_PATCHES": "M_PATCHES",
    "M_PATTERN": "M_PATTERN",
    "M_PD": "M_PD",
    "M_PLACEMENT": "M_PLACEMENT",
    "M_PLACKET": "M_PLACKET",
    "M_POCKET": "M_POCKET",
    "M_PRINT_PLACEMENT": "M_PRINT_PLACEMENT",
    "M_PRINT_STYLE": "M_PRINT_STYLE",
    "M_PRINT_TYPE": "M_PRINT_TYPE",
    "M_SET": "M_SET",
    "M_SHADE": "M_SHADE",
    "M_SLEEVES_MAIN_STYLE": "M_SLEEVES_MAIN_STYLE",
    "M_SUB_STYLE_BLT": "M_SUB_STYLE_BLT",
    "M_SUB_STYLE_BTN_CLR": "M_SUB_STYLE_BTN_CLR",
    "M_WASH": "M_WASH",
    "M_WASH_COLOR": "M_WASH_COLOR",
    "M_WEAVE_1": "M_WEAVE_1",
    "M_WEAVE_2": "M_WEAVE_2",
    "M_WEAVE_3": "M_WEAVE_3",
    "M_YARN": "M_YARN",
    "M_ZIP": "M_ZIP",
    "M_ZIP_COL": "M_ZIP_COL",
    "M_FAB_DIV": "M_FAB_DIV",
    "M_YARN_02": "M_YARN_02",
    "M_COMPOSITION": "M_COMPOSITION",
    "M_CONSTRUCTION": "M_CONSTRUCTION",
    "M_FINISH": "M_FINISH",
    "M_FAB_MAIN_MVGR_1": "M_FAB_MAIN_MVGR_1",
    "M_FAB_MAIN_MVGR_2": "M_FAB_MAIN_MVGR_2",
    "M_WEAVE_01": "M_WEAVE_01",
    "M_WEAVE_02": "M_WEAVE_02",
    "M_NECK_TYPE": "M_NECK_TYPE",
    "M_NECK_STYLE": "M_NECK_STYLE",
    "M_COLLAR_TYPE": "M_COLLAR_TYPE",
    "M_BLT_TYPE": "M_BLT_TYPE",
    "M_BLT_STYLE": "M_BLT_STYLE",
    "M_BODY_STYLE": "M_BODY_STYLE",
    "M_DC_STYLE": "M_DC_STYLE",
    "M_ZIP_TYPE": "M_ZIP_TYPE",
    "M_BTN_TYPE": "M_BTN_TYPE",
    "M_PATCH_STYLE": "M_PATCH_STYLE",
    "M_PATCHE_TYPE": "M_PATCHE_TYPE",
    "M_EMBROIDERY_STYLE": "M_EMBROIDERY_STYLE",
    "M_FAB_VDR": "M_FAB_VDR",
    "M_IMP_ATBT": "M_IMP_ATBT",
    "M_WIDTH": "M_WIDTH",
    "M_COLLAR_STYLE": "M_COLLAR_STYLE",
    "M_NO_OF_POCKET": "M_NO_OF_POCKET",
    "M_SLEEVE_FOLD": "M_SLEEVE_FOLD",
    "M_HTRF_STYLE": "M_HTRF_STYLE",
    "M_BTN_CLR": "M_BTN_CLR",
    "M_DC_SHAPE": "M_DC_SHAPE",
    "M_HTRF_TYPE": "M_HTRF_TYPE",
    "M_EMB_PLACEMENT": "M_EMB_PLACEMENT",
    "M_AGE_GROUP": "M_AGE_GROUP",
    "M_EXTRA_POCKET": "M_EXTRA_POCKET",
}
PROD_ATTRS = list(PROD_ATTR_MAP)

# ── THE TWO RATIONALISATION NAMES, off the same master row (db-scripts/379) ───────────────────
#
# SAP stamps a colour and a size on the variant; merchandising has since collapsed that
# vocabulary, and db-scripts/379 joined the two RFC extracts that hold the collapse
# (ET_ZMM_COLOR_MAP_RFC, 791 rows -> 203 colours; ET_ZMM_SIZE_MAPPING_GET_RFC, 138 rows) into
# VW_PRODUCT_MASTER. So these are plain master columns here - no join of this report's own, and
# every other page reading that view spells them the same way.
#
#   NEW_COLOR_DESC   'ALMND' -> 'ALMOND'   the column to GROUP BY for "how did red do" rather
#                                          than "how did each of the nine reds do"
#   NEW_SIZE_DESC    'XXL'   -> '2XL'      almost an identity: 135 of the map's 138 rows spell
#                                          the two the same, and the three that differ are the
#                                          XXL / XXXL / XXXXL ones a header actually cares about
#
# COALESCE(new, old) INSIDE THE VIEW, so they are never blank where COLOR and SIZE_1 are not.
# Which means you cannot read "was this mapped?" off one of them - COLOR sits beside
# NEW_COLOR_DESC on the same row, and equal-vs-different answers it.
#
# NOT IN PROD_ATTR_MAP, though they come from the same subquery and the same join. That list is
# projected inside `enrich_cols`, which sits BEFORE every sale, stock and ref column - adding to
# it would shift the whole grid two places right in every export ever taken. These go on the END
# with the tail block instead, the same append-only rule the stock tags and the plant split follow.
#
# ALL SIX COLUMNS OF THE TWO EXTRACTS, not just the two descriptions. The join keys
# (OLD_COLOR_DESC, SIZE_NAME) are the only ones absent, and only because the join makes them
# byte-equal to COLOR and SIZE_1, which this report already shows as key columns.
#
#   COLOR_CODE       'BLACK' -> 'BLK'      the short form of THIS row's colour
#   NEW_COLOR_CODE   'AF_BL' -> 'AF_BLU'   COALESCE(new, old) like the description
#   COLOR_MAP_DATE / SIZE_MAP_DATE         when each extract was pulled
#
# THE TWO DATES ARE CONSTANTS. Each extract carries ONE value across every row (2026-09-15 as at
# the 16-Sep-2026 pull), so on the grid they are the same date on every row - a provenance stamp
# answering "how stale is the mapping I am reading", never a measure and never worth grouping by.
# They are offered as filters anyway, because a one-value dropdown is how someone notices that the
# map has not been re-pulled since September.
MAP_ATTR_MAP = {
    "NEW_COLOR_DESC": "NEW_COLOR_DESC",
    "NEW_SIZE_DESC": "NEW_SIZE_DESC",
    "COLOR_CODE": "COLOR_CODE",
    "NEW_COLOR_CODE": "NEW_COLOR_CODE",
    "COLOR_MAP_DATE": "COLOR_MAP_DATE",
    "SIZE_MAP_DATE": "SIZE_MAP_DATE",
}
MAP_COLS = list(MAP_ATTR_MAP)
# The two DATE columns among them. TRIM() on a DATE is a compile error in Snowflake, so every
# place that treats a master column as text has to cast these first - see /params below.
MAP_DATE_COLS = {"COLOR_MAP_DATE", "SIZE_MAP_DATE"}
LOC_FLAG = "LOC_IN_MASTER"
# The two values LOC_FLAG can take, as ONE definition. They are built into the filter list, the
# SELECT and the ORDER BY, and the three had already been written out by hand three times - which
# is exactly how a filter comes to read one string while the grid displays another.
# NOTE for a saved report carried over from before 10-Sep-2026: a col_filter holding the old
# "NO - DC/hub" no longer matches anything. _clean_def does not police enrich values, so it comes
# back empty rather than erroring; re-pick the value once and it saves the new one.
LOC_YES, LOC_NO_LABEL = "yes", "NO - not in master"
LOC_FLAG_EXPR = f"IFF(sm.ST_CD IS NULL, '{LOC_NO_LABEL}', '{LOC_YES}')"

# MAJ_CAT and SEG are NOT here: they now resolve against the master (PROD_KEY_MAP), so their
# predicate has to be applied after the join like every other master column. Filtering the fact
# stamp while displaying the master value would hand back rows that do not match the filter.
_FILT_COLS = {"ST_CD", "ARTICLE_NUMBER", "GEN_ART_NUMBER", "COLOR", "SIZE_1"}
# The store half re-pointed at the MIS master's own names (db-scripts/377). HUB_CD, CLUSTER and
# BK_FLD_STATUS left because the column left; ZONE, ZONE_RETAIL, REG and OLD_NEW joined because
# they are the geography and lifecycle buckets a reader actually slices this report by, and they
# had no counterpart before. A saved col_filter on a departed name is silently dropped here, not
# an error - see the note over STORE_ATTRS.
# MAP_COLS ride the product-master half of this set: they are columns of VW_PRODUCT_MASTER like
# any other, so _filter_sql's "sm or pm" branch already resolves them to pm.<name> and the sort
# branch already orders by it. Nothing special is needed for either - only for the PROJECTION,
# which puts them in the tail rather than among the enrich columns.
_ENRICH_FILT = {"RDC_CD", "STATE", "ST_STAT", "OLD_NEW", "ZONE", "ZONE_RETAIL", "REG",
                "DIV", "SUB_DIV", "MC_CODE", "REG_SEG", "ART_TYPE", "ART_STATUS", "MAIN_MVGR",
                LOC_FLAG, PLANT_COL} | set(MAP_COLS)
_COL_RE = re.compile(r"^[A-Za-z0-9_]+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

MAX_LIMIT = 5000

# EXPORT HEADERS ARE THE RAW COLUMN NAMES - S_TD__SALE_V, K_PRD__STOCK_Q - so a header in the file
# is exactly the column you can query, join or script against, and it matches the month-wise exports
# that came before. The friendly labels ("TD Sale V", "PRD - Stk V") live in the PAGE, which is the
# only place they belong: on screen they save horizontal room, in a file they cost traceability.
# There is deliberately no label map here any more; one existed and made the two disagree.


def _conn():
    try:
        return get_sf_connection()
    except Exception as e:
        logger.warning("[sf-var-combined] Snowflake connection failed: %s", e)
        raise HTTPException(503, f"Could not connect to Snowflake: {e}")


def _ex(guard, cur, sql):
    """``cur.execute``, through the cancel guard when there is one.

    The guarded endpoints share their read helpers with /export, which has no guard because
    nothing aborts a download. An unguarded statement inside a guarded body is not merely
    untracked - it also slips past the guard's refusal to START work once the client is known
    to be gone (see app/sf_cancel.py) - so the helpers take the guard rather than assume it.
    """
    return guard.execute(cur, sql) if guard is not None else cur.execute(sql)


def _q(v):
    return "'" + str(v).replace("'", "''") + "'"


def _fmt_str(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return "" if v is None else v


def _f(v):
    return None if v is None else float(v)


def _check_date(d, what):
    d = str(d or "").strip()
    if not _DATE_RE.match(d):
        raise HTTPException(400, f"{what} must be a date in YYYY-MM-DD form")
    return d


def _range(from_date, to_date):
    a, b = _check_date(from_date, "fromDate"), _check_date(to_date, "toDate")
    if a > b:
        raise HTTPException(400, f"fromDate ({a}) is after toDate ({b})")
    return a, b


def _tags(requested):
    """Validate a caller's stock-tag subset. Order follows the canonical list, never the request,
    so column positions stay stable between calls and an export lines up with an earlier one."""
    # Composites LAST, so they land at the right-hand end of the stock block: a total reads
    # naturally after its parts, and an export taken before they existed still lines up. Rule tags
    # after even those, for the same append-only reason - slotting LQ_V02 in beside the LQ_* tags
    # it reads would have been tidier and would have shifted every stock column to its right,
    # TOTAL_STORE_STOCK included, in every export already taken.
    # Pick tags last of all, for the same append-only reason - MSA and GRT read LQ_E01 / LQ_G01 and
    # would have read best beside them, and slotting them there would have shifted every stock
    # column to their right, composites and LQ_V02 included, in every export already taken.
    universe = list(STOCK_TAGS) + list(COMPOSITE_TAGS) + list(RULE_TAGS) + list(PICK_TAGS)
    if not requested:
        # "EVERYTHING" DELIBERATELY EXCLUDES THE WHN FAMILY (db-scripts/380). Asking for no tags
        # means "the usual columns", and 98 warehouse-number x storage-type pairs is 196 more of
        # them on a grid that was already measured at 279.6s with 141 tags against a 300s client
        # ceiling - so including them by default would have made the DEFAULT page the slow one and
        # pushed it over, to show columns almost nobody opens the page for.
        #
        # They are NOT hidden: /params lists them, the picker offers them, and naming one asks for
        # exactly it. This is the one place a tag can be in the catalogue and out of the default,
        # so a reader of `universe` above sees the whole vocabulary in one line.
        return [t for t in universe if t not in _WHN_SET]
    want = {str(t).strip().upper() for t in requested}
    picked = [t for t in universe if t.upper() in want]
    if not picked:
        raise HTTPException(400,
                            f"none of the requested stock tags exist; expected some of {universe}")
    return picked


class SqlExpr(str):
    """A date bound that is already SQL and must NOT be quoted.

    A subclass of str so it flows through the existing f-strings untouched; _dt() is the only thing
    that has to know the difference. The alternative - a parallel set of builders for the view path -
    is how the two would drift apart, which is the same trap the window definitions already carry
    (see _window_ranges and db-scripts/309)."""
    __slots__ = ()


def _dt(v):
    """Format a window bound: a literal date gets quoted and cast, an expression is passed through."""
    return str(v) if isinstance(v, SqlExpr) else f"'{v}'::DATE"


# The reporting anchor, in SQL: the LAST DAY OF THE PREVIOUS MONTH - the month-end close, which is
# the only date the business signs off and therefore the only one worth pinning a view to.
#
# CONVERT_TIMEZONE, not CURRENT_DATE. The session timezone is America/Los_Angeles, so CURRENT_DATE is
# a day behind IST until 12:30 IST. On the 1st of a month that is not cosmetic: it would resolve the
# anchor to the end of the month BEFORE last, and the view would silently serve a month-old close.
IST_TODAY_SQL = "CONVERT_TIMEZONE('UTC','Asia/Kolkata',CURRENT_TIMESTAMP())::DATE"
MONTH_END_ANCHOR_SQL = SqlExpr(f"LAST_DAY(ADD_MONTHS({IST_TODAY_SQL}, -1))")


def _window_exprs(anchor_sql):
    """The nine windows as SQL EXPRESSION pairs, for an anchor that is itself an expression.

    A transcription of _window_ranges below - and of the LET block in db-scripts/309, which is the
    same arithmetic a third time. All three must agree; a change to any window lands in all of them.

    The anchor expression is repeated rather than held in a CTE because these bounds are formatted
    into predicates scattered across the query. Snowflake folds the repetition; a reader should know
    it is deliberate and not a missed refactor.
    """
    x = str(anchor_sql)
    mtd_a = f"DATE_TRUNC('MONTH', {x})"
    qtr_a = (f"DATE_FROM_PARTS(YEAR({x}), CASE WHEN MONTH({x}) BETWEEN 4 AND 6 THEN 4 "
             f"WHEN MONTH({x}) BETWEEN 7 AND 9 THEN 7 WHEN MONTH({x}) >= 10 THEN 10 ELSE 1 END, 1)")
    ytd_a = f"DATE_FROM_PARTS(IFF(MONTH({x}) >= 4, YEAR({x}), YEAR({x})-1), 4, 1)"
    lym_a = f"DATEADD(year, -1, {mtd_a})"
    lyq_a = f"DATEADD(year, -1, {qtr_a})"
    E = SqlExpr
    return {
        "TD":          (E(x), E(x)),
        "MTD":         (E(mtd_a), E(x)),
        "QTR":         (E(qtr_a), E(x)),
        "LAST_7DAYS":  (E(f"DATEADD(day, -6, {x})"), E(x)),
        "LAST_4WEEK":  (E(f"DATEADD(day, -27, {x})"), E(x)),
        "YTD":         (E(ytd_a), E(x)),
        "LAST_6MONTH": (E(f"DATEADD(month, -6, {mtd_a})"), E(f"DATEADD(day, -1, {mtd_a})")),
        "LYSP_SSN":    (E(lyq_a), E(f"DATEADD(day, -1, DATEADD(month, 3, {lyq_a}))")),
        "LYSP_MONTH":  (E(lym_a), E(f"LAST_DAY({lym_a})")),
    }


def _window_ranges(b):
    """The nine named windows as (start, end) date pairs, anchored on the To date `b`.

    These reproduce the definitions the month-wise procs used, so the numbers tie:
      TD           the anchor day itself - i.e. THE ROW'S OWN DATE, so TD is the one window
                   that is safe to total down a multi-day column; the rest are cumulative
      MTD          that month's 1st -> anchor
      QTR          fiscal quarter (Apr-Jun / Jul-Sep / Oct-Dec / Jan-Mar) start -> anchor
      LAST_7DAYS   ROLLING 7 days ending at the anchor - not a calendar week
      LAST_4WEEK   ROLLING 28 days - not 30
      YTD          FISCAL year start (1-Apr) -> anchor; Jan/Feb/Mar belong to the previous April
      LAST_6MONTH  the six COMPLETED months ending at the previous month end
      LYSP_MONTH   the same calendar month, one year back
      LYSP_SSN     the same fiscal quarter, one year back
    """
    d = datetime.fromisoformat(b).date()
    y, m = d.year, d.month
    qs = 4 if 4 <= m <= 6 else 7 if 7 <= m <= 9 else 10 if m >= 10 else 1
    fy_start = date(y if m >= 4 else y - 1, 4, 1)
    prev_end = date(y, m, 1) - timedelta(days=1)            # last day of the previous month
    l6_start = date(prev_end.year, prev_end.month, 1) - timedelta(days=1)
    for _ in range(4):                                       # step back to the 6th month start
        l6_start = date(l6_start.year, l6_start.month, 1) - timedelta(days=1)
    l6_start = date(l6_start.year, l6_start.month, 1)
    ly_m_start = date(y - 1, m, 1)
    ly_m_end = (date(y - 1, m + 1, 1) - timedelta(days=1)) if m < 12 else date(y - 1, 12, 31)
    ly_q_start = date(y - 1, qs, 1)
    ly_q_end = (date(y - 1, qs + 3, 1) - timedelta(days=1)) if qs <= 9 else date(y - 1, 12, 31)
    return {
        "TD":          (d, d),
        "MTD":         (date(y, m, 1), d),
        "QTR":         (date(y, qs, 1) if qs != 1 or m <= 3 else date(y, 1, 1), d),
        "LAST_7DAYS":  (d - timedelta(days=6), d),
        "LAST_4WEEK":  (d - timedelta(days=27), d),
        "YTD":         (fy_start, d),
        "LAST_6MONTH": (l6_start, prev_end),
        "LYSP_SSN":    (ly_q_start, ly_q_end),
        "LYSP_MONTH":  (ly_m_start, ly_m_end),
    }


# /params CACHE.
#
# Every option list on that endpoint is a SELECT DISTINCT over a fact table, so its cost tracks the
# size of the facts and not the size of the answer. Backfilling DAY_WISE_ST_VAR_SALE took it from
# 8.7M rows to 87M, and opening the stock loader to every LGORT plus the LQUA families took stock to
# ~46M for two days - which pushed the endpoint past the 120s proxy timeout it sits behind. A
# timed-out /params leaves the page with no storedAnchors, so it reports "no stored windows" and
# quietly serves the short computed numbers: the exact failure this table was built to end.
#
# Caching is the right fix rather than shaving the queries. The answer changes ONLY when a load
# runs, and both loaders invalidate it below, so the TTL is a backstop for a proc run outside the
# API rather than the thing correctness depends on.
_PARAMS_TTL_S = 600
_params_cache = {"at": 0.0, "data": None}


def _params_invalidate():
    """Called by both loaders. A load is the only thing that can change /params' answer, so it is
    also the only thing that needs to expire it - and after a load is exactly when a stale
    coverage line would be read as a failed load."""
    _params_cache["at"] = 0.0
    _params_cache["data"] = None


def _stored_anchors(cur, a, b, guard=None):
    """Which anchor days inside [a,b] have STORED windows (db-scripts/309).

    Read from the REGISTRY, which has one row per anchor - not from KPI_TBL, where the same
    question costs a scan of 20M+ rows.

    STORE_SCOPE = 'ALL' only. A date EXISTING is not a date being COMPLETE: an anchor loaded for one
    store would otherwise flip the whole grid onto the stored path and show that store's numbers as
    if they were the company's - the same silent shortfall, just from the other direction.

    The report uses the stored table only when EVERY day in the range qualifies. A range served half
    from stored windows and half from re-aggregated day rows would put two definitions of "correct"
    in one grid, and the reader has no way to tell which cell came from which.
    """
    _ex(guard, cur, f"SELECT DT FROM {KPI_LOG_TBL} "
                    f"WHERE DT BETWEEN {_q(a)}::DATE AND {_q(b)}::DATE AND STORE_SCOPE = 'ALL'")
    return {_fmt_str(r[0]) for r in cur.fetchall()}


def _stored_sale_agg(a, b):
    """The nine windows read straight off DAY_WISE_ST_VAR_SALE_KPI, plus a derived RANGE.

    The stored columns are already named S_<WINDOW>__<MEASURE>, so they project through unchanged -
    that naming is why the stored path needs no translation layer.

    RANGE CANNOT BE STORED: it is the caller's own From/To, not a window derived from the anchor. It
    is derived here instead, from the stored TD columns across the anchors in the range - TD is the
    anchor's own day, so summing TD over the anchors in [a,b] IS the range total. The AVG_* and the
    three day counts follow the same rule they follow everywhere else, which keeps RANGE meaning the
    same thing on both paths.
    """
    part = "PARTITION BY k.ST_CD, k.ARTICLE_NUMBER"
    cols = [f'k."S_{w}__{m}"' for w in SALE_WINDOWS for m in WINDOW_MEASURES]
    for m in WINDOW_MEASURES:
        alias = f"S_RANGE__{m}"
        if m.startswith("AVG_"):
            src = f'k."S_TD__{m[4:]}"'
            cols.append(f'ROUND(AVG(CASE WHEN {src} > 0 THEN {src} END) '
                        f'OVER ({part}),4) AS "{alias}"')
        elif m.endswith("_DAYS"):
            cols.append(f'SUM(k."S_TD__{m}") OVER ({part}) AS "{alias}"')
        else:
            cols.append(f'ROUND(SUM(k."S_TD__{m}") OVER ({part}),4) AS "{alias}"')
    return (f"(SELECT k.DT ANCHOR_DT, k.ST_CD, k.ARTICLE_NUMBER, {', '.join(cols)} "
            f"FROM {KPI_TBL} k WHERE k.DT BETWEEN {_q(a)}::DATE AND {_q(b)}::DATE)")


def _stock_pick(anchors, tags=None, where=None):
    """For each anchor day, each store's LATEST reported stock day on or before it.

    Returns (ANCHOR_DT, ST_CD, KPI, DT) so a 27-Aug row sees the stock position as of 27-Aug and a
    28-Aug row sees it as of 28-Aug. Anchoring the whole range on its last day gave every row the
    same closing position, which is not a day-wise report.

    PER TAG, not per store. The two sources have different day coverage - ET_STOCK_DATA and
    ZWM_PRD_DATE1 are separate extracts - so one store-wide MAX(DT) would let the source that
    reported latest decide the day for BOTH, and every tag from the other source would read empty.
    That is "no file" rendered as "no stock", the exact failure this function exists to prevent.

    Not `DT = anchor`: ET_STOCK_DATA does not report every store every day, and the day-wise loader
    records only days actually reported (no carry-forward). Pinning to one date would make every
    store that did not report that day look empty, which is "no file" rendered as "no stock".
    """
    if isinstance(anchors, str):                 # a bare date would iterate as characters
        anchors = [anchors]
    # PRUNE BY TAG when the caller named a subset. This groups by (ST_CD, KPI) across the whole
    # table, so with 141 tags loaded it read every one of them to answer a question about the few a
    # report asked for. Measured: the grid went 68.6s at 14 tags -> 279.6s at 141, right on the
    # 300s client ceiling. Both arguments None keeps the old behaviour of reading everything.
    #
    # `where` is scan_filter()'s finished clause, for a caller holding a RULE tag - which narrows
    # by a prefix test rather than by names, and so cannot be expressed as a list. Callers pass one
    # or the other; `tags` stays for the ones that only ever hold plain KPIs.
    kpi_in = where or ((" AND KPI IN (" + ", ".join(_q(t) for t in tags) + ")") if tags else "")
    # _dt(), not _q(): an anchor may be a SqlExpr, which must not be quoted (view mode).
    parts = [f"SELECT {_dt(d)} ANCHOR_DT, ST_CD, KPI, MAX(DT) DT FROM {STOCK_TBL} "
             f"WHERE DT <= {_dt(d)}{kpi_in} GROUP BY 2,3"
             for d in anchors]
    return "(" + " UNION ALL ".join(parts) + ")"


# ══════════════════════════════════════════════════════════════════════════════
# params
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/params")
def sf_var_combined_params(refresh: int = 0):
    """What days exist on each side, plus the filter option lists.

    Days, not months. The two sides are loaded by separate procs over separate ranges, so one can
    legitimately reach further back than the other - reporting each side's own span makes that
    visible instead of hiding it behind a single date.

    CACHED - see _PARAMS_TTL_S. `refresh=1` forces a rebuild. `cachedAt` on the reply says which
    read the caller got, so a stale coverage line can be recognised as stale rather than argued
    about.
    """
    if not refresh and _params_cache["data"] is not None \
            and (time.time() - _params_cache["at"]) < _PARAMS_TTL_S:
        out = dict(_params_cache["data"])
        out["cached"] = True
        return out
    conn = _conn()
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"""
                SELECT 'sale' SIDE, MIN(DT) MIN_DT, MAX(DT) MAX_DT,
                       COUNT(DISTINCT DT) N_DAYS, COUNT(*) N_ROWS, COUNT(DISTINCT ST_CD) N_ST
                  FROM {SALE_TBL}
                UNION ALL
                SELECT 'stock', MIN(DT), MAX(DT), COUNT(DISTINCT DT), COUNT(*), COUNT(DISTINCT ST_CD)
                  FROM {STOCK_TBL}
                UNION ALL
                SELECT 'kpi', MIN(DT), MAX(DT), COUNT(*), NVL(SUM(N_ROWS),0), NVL(MAX(N_STORES),0)
                  FROM {KPI_LOG_TBL}""")
            cov = {r[0]: {"minDt": _fmt_str(r[1]), "maxDt": _fmt_str(r[2]),
                          "days": int(r[3] or 0), "rows": int(r[4] or 0),
                          "stores": int(r[5] or 0)} for r in cur.fetchall()}

            # The individual stored anchors, not just their span: they are deliberately sparse
            # (you load the days you read), so a min/max would imply a continuity that is not there.
            # ALL-stores anchors only, matching what _stored_anchors will actually accept - the strip
            # must not promise a stored read the source decision is going to refuse.
            cur.execute(f"SELECT DT FROM {KPI_LOG_TBL} WHERE STORE_SCOPE = 'ALL' "
                        f"ORDER BY 1 DESC LIMIT 400")
            stored_anchors = [_fmt_str(r[0]) for r in cur.fetchall()]

            cur.execute(f"SELECT DISTINCT ST_CD FROM {SALE_TBL} WHERE ST_CD IS NOT NULL "
                        f"UNION SELECT DISTINCT ST_CD FROM {STOCK_TBL} WHERE ST_CD IS NOT NULL "
                        f"ORDER BY 1")
            stores = [r[0] for r in cur.fetchall()]
            # FROM THE MASTER, not the fact. The grid resolves MAJ_CAT and SEG against
            # VW_PRODUCT_MASTER (PROD_KEY_MAP), so a list built from the fact's frozen stamps would
            # offer categories that no longer exist - pick one and the filter matches nothing. It is
            # also two full scans of a 127M-row table cheaper.
            # NOT scoped to SEG IN ('APP','GM'), unlike the enrich options just below. The list this
            # replaced came from the FACT, and the fact carries six segments, not two - 769k APP and
            # 34k GM articles, but also 29k FAB, 9k NT, and a few hundred ACC and MKT. Filtering to
            # APP/GM here would offer 1,638 categories where the fact holds 2,894, and silently take
            # away every fabric and non-trade category anyone used to be able to filter on. The
            # master's full list is a superset of what occurs, which is the safe direction to err.
            _key_opts = {}
            for _col in PROD_KEY_MAP:
                _src = PROD_KEY_MAP[_col]
                cur.execute(f"SELECT DISTINCT {_src} FROM {PROD_TBL} "
                            f"WHERE {_src} IS NOT NULL AND TRIM({_src}) <> '' ORDER BY 1")
                _key_opts[_col] = [r[0] for r in cur.fetchall()]
            maj_cats = _key_opts.get("MAJ_CAT", [])
            # FOUR option lists, ONE pass. These were four separate SELECT DISTINCTs over the same
            # multi-million-row stock table, so the endpoint paid for four full scans to build four
            # dropdowns. /params sits behind a 120s proxy timeout and was measured at 106s; a
            # timed-out /params leaves the page with no storedAnchors, which makes it report "no
            # stored windows" and quietly fall back to the short computed numbers.
            cur.execute(f"""SELECT ARRAY_AGG(DISTINCT SIZE_1) WITHIN GROUP (ORDER BY SIZE_1),
                                   ARRAY_AGG(DISTINCT COLOR)  WITHIN GROUP (ORDER BY COLOR),
                                   ARRAY_AGG(DISTINCT KPI)    WITHIN GROUP (ORDER BY KPI)
                              FROM {STOCK_TBL}""")
            _r = cur.fetchone() or (None, None, None)
            import json as _json
            _lst = lambda v: [x for x in _json.loads(v) if x is not None] if v else []
            sizes, colors = _lst(_r[0]), _lst(_r[1])
            have_tags = set(_lst(_r[2]))
            # SEG left this aggregate for the same reason as MAJ_CAT: it is master-owned now.
            # COLOR and SIZE_1 stay - they are per-variant facts off MARA, not the ZMC_MASTER
            # hierarchy, and nothing re-classifies them.
            segs = _key_opts.get("SEG", [])

            enrich_opts = {}
            # The MIS master's own filterable columns (db-scripts/377) - the store half of
            # _ENRICH_FILT, and it has to stay in step with it: a column offered here that the
            # filter does not accept is a dropdown whose every choice is a no-op.
            for col in ("RDC_CD", "STATE", "ST_STAT", "OLD_NEW", "ZONE", "ZONE_RETAIL", "REG"):
                cur.execute(f"SELECT DISTINCT {col} FROM {STORE_TBL} "
                            f"WHERE {col} IS NOT NULL AND TRIM({col}) <> '' ORDER BY 1")
                enrich_opts[col] = [r[0] for r in cur.fetchall()]
            # MAP_COLS ride this same loop: they are master columns, so the APP/GM scoping and
            # the blank filter apply to them exactly as to DIV or ART_TYPE. NEW_COLOR_DESC comes
            # back at roughly 203 values against COLOR's 545 - which IS the point of the column.
            for col in ("DIV", "SUB_DIV", "REG_SEG", "ART_TYPE", "ART_STATUS") + tuple(MAP_COLS):
                src = {**PROD_ATTR_MAP, **MAP_ATTR_MAP}[col]
                # TO_VARCHAR first for the two DATE columns: TRIM(<date>) does not compile in
                # Snowflake, and this loop is the one place a master column is assumed to be text.
                _txt = f"TO_VARCHAR({src})" if col in MAP_DATE_COLS else src
                cur.execute(f"SELECT DISTINCT {src} FROM {PROD_TBL} "
                            f"WHERE {src} IS NOT NULL AND TRIM({_txt}) <> '' "
                            f"AND TRIM(NVL(SEGMENT,'')) IN ('APP','GM') ORDER BY 1")
                enrich_opts[col] = [r[0] for r in cur.fetchall()]
            enrich_opts[LOC_FLAG] = [LOC_YES, LOC_NO_LABEL]
            # From the FULL master, plus UNMAPPED for the locations it has no row for - the same
            # vocabulary the split columns bucket by, so a filter value and a column header can
            # never disagree about what a DC is.
            cur.execute(f"SELECT DISTINCT TRIM({PLANT_COL}) FROM {PLANT_TBL} "
                        f"WHERE {PLANT_COL} IS NOT NULL AND TRIM({PLANT_COL}) <> '' ORDER BY 1")
            enrich_opts[PLANT_COL] = [r[0] for r in cur.fetchall()] + ["UNMAPPED"]

            # Every store that EXISTS, for the load picker - as opposed to `stores` above, which is
            # every store already LOADED and is what the grid filter wants. Feeding the loaded list
            # to the load picker offered only the store you had already loaded.
            cur.execute(f"SELECT DISTINCT TRIM(ST_CD) FROM {STORE_TBL} "
                        f"WHERE ST_CD IS NOT NULL AND TRIM(ST_CD) <> '' ORDER BY 1")
            all_stores = [r[0] for r in cur.fetchall()]
        except Exception as e:
            logger.warning("[sf-var-combined] params unavailable: %s", e)
            return {"coverage": {}, "stores": [], "allStores": [], "majCats": [], "segs": [],
                    "sizes": [], "colors": [],
                    "stockTags": list(STOCK_TAGS) + list(RULE_TAGS) + list(PICK_TAGS),
                    "composites": {}, "picks": {}, "defaultOffTags": list(WHN_TAGS),
                    "enrichOpts": {},
                    "storedAnchors": [],
                    "enrichCols": ([LOC_FLAG] + STORE_ATTRS + PROD_ATTRS
                                   + [PLANT_COL] + MAP_COLS),
                    "error": str(e)}
    finally:
        conn.close()
    out = {"coverage": cov, "stores": stores, "allStores": all_stores,
           "storedAnchors": stored_anchors,
           "majCats": maj_cats, "segs": segs,
           "sizes": sizes, "colors": colors,
           # RULE TAGS ARE NOT FILTERED BY have_tags, unlike the plain ones. There is no row in
           # the fact stamped 'LQ_V02' for a DISTINCT KPI to find - the tag is a rule over rows
           # stamped LQ_D22, LQ_B05 and the rest - so testing it that way would drop it from the
           # picker permanently. Its members ARE tested, below.
           # PICK TAGS ARE NOT FILTERED BY have_tags either, and for the composites' reason: there
           # is no row in the fact stamped 'MSA'. Their members are checked in `picks` below.
           "stockTags": ([t for t in STOCK_TAGS if t in have_tags]
                         + list(COMPOSITE_TAGS) + list(RULE_TAGS) + list(PICK_TAGS)),
           # WHICH MEMBERS ARE ACTUALLY THERE. `have_tags` is the DISTINCT KPI list this endpoint
           # already computes, so this costs nothing and answers the one question a total cannot
           # answer about itself: a member with no rows in the fact contributes zero and reads
           # exactly like a member that contributed zero. Today that is 0008 (never loaded) and
           # LQB_TRANSIT (needs db-scripts/345 plus a stock reload). The page names them in the
           # column header rather than letting the number quietly read short.
           "composites": {c: {"members": COMPOSITE_TAGS[c],
                              "missing": [m for m in COMPOSITE_TAGS[c]
                                          if resolve_tag(m)[0] not in have_tags]}
                          for c in COMPOSITE_TAGS},
           # The same question a composite answers with `missing`, asked of a rule tag - and it has
           # to be asked differently, because a rule tag has no member list to check off. What it
           # can report is how many KPIs in the fact its rule COULD match: 0 means the LQUA
           # snapshot behind it has not loaded at all, which is the one case where an empty column
           # is a data problem rather than an honest zero.
           "rules": {r: {"candidates": len([k for k in have_tags if k.startswith("LQ_")])}
                     for r in RULE_TAGS},
           # The same question asked of a pick, where it matters MORE than it does of a total: a
           # composite missing a member reads short, a pick missing its FIRST member reads its
           # second and looks entirely healthy doing it. Both lists go to the page, in order, so
           # the header can say which rack a number actually came from.
           "picks": {p: {"members": PICK_TAGS[p],
                         "missing": [m for m in PICK_TAGS[p]
                                     if resolve_tag(m)[0] not in have_tags]}
                     for p in PICK_TAGS},
           # The tags that exist but are NOT in "everything" - see _tags(). The page says so on
           # the picker, because a column that is absent until you tick it is otherwise
           # indistinguishable from a column that is absent because nothing loaded.
           "defaultOffTags": [t for t in WHN_TAGS if t in have_tags],
           "enrichOpts": enrich_opts,
           # PLANT_COL then MAP_COLS last, matching where _build puts them in the grid. The page
           # reads this list to know which columns are TEXT rather than measures, so a map column
           # left out of it would be right-aligned and K/L-scaled - '2XL' rendered as a number.
           "enrichCols": ([LOC_FLAG] + STORE_ATTRS + PROD_ATTRS
                          + [PLANT_COL] + MAP_COLS),
           "plantBuckets": [b for b, _p in PLANT_BUCKETS], "splitTags": list(SPLIT_TAGS)}
    _params_cache["at"] = time.time()
    _params_cache["data"] = out
    return {**out, "cached": False}


# ══════════════════════════════════════════════════════════════════════════════
# result
# ══════════════════════════════════════════════════════════════════════════════
class CombinedReq(BaseModel):
    from_date: str
    to_date: str
    stock_tags: Optional[list] = None
    page: int = 1
    limit: int = 200
    sort_col: Optional[str] = None
    sort_dir: Optional[str] = "asc"
    col_filters: Optional[dict] = None
    search: Optional[str] = None
    # 'auto'     - stored windows when every anchor in the range has them, else computed
    # 'stored'   - stored only; fails loudly rather than quietly serving short numbers
    # 'computed' - always re-aggregate the day-wise fact, which is what this page did before 309
    sale_source: Optional[str] = "auto"
    # Whether to compute the per-article ACT columns (A__), which read SAP_VBRP directly.
    #
    # OFF BY DEFAULT, AND THAT IS A COST DECISION, NOT A DOUBT ABOUT THE NUMBER. Every other column
    # on this page comes from a pre-aggregated day-wise fact; these come from the billing document,
    # which is the whole reason they can catch a short backfill - and also why they cost a VBRP
    # read the rest of the grid does not. Nobody should pay that on a page load they did not ask
    # for it on, so the page asks.
    ref_act: Optional[bool] = False
    # GRC and PO, off for the same cost reason and asked for the same way. Each is one extra scan
    # of one SILVER view; neither touches the sale or stock path, so a page that leaves both off
    # builds exactly the query it built before they existed.
    ref_grc: Optional[bool] = False
    ref_po: Optional[bool] = False


def _resolve_source(cur, req, a, b, guard=None):
    """Pick the sale source for this request and say which was picked.

    'auto' is the default because the stored table is the one that can be RIGHT when the day-wise
    fact has gaps, and it is also the cheaper read - but it only covers anchors somebody loaded, so
    falling back keeps every range answerable.
    """
    want = str(getattr(req, "sale_source", None) or "auto").lower()
    if want not in ("auto", "stored", "computed"):
        raise HTTPException(400, "saleSource must be auto, stored or computed")
    if want == "computed":
        return False, "computed"
    d0 = datetime.fromisoformat(a).date()
    span = (datetime.fromisoformat(b).date() - d0).days + 1
    need = {(d0 + timedelta(days=i)).isoformat() for i in range(span)}
    have = _stored_anchors(cur, a, b, guard)
    missing = sorted(need - have)
    if not missing:
        return True, "stored"
    if want == "stored":
        raise HTTPException(400,
            f"{len(missing)} of {len(need)} day(s) in this range have no stored KPI windows "
            f"(first: {missing[0]}). Load them, or use saleSource=auto.")
    return False, "computed"


def _filter_sql(col_filters, search, enrich_only=False):
    """Predicates for the fact side, or (enrich_only) for the master-join side.

    The two belong in different places - fact columns narrow the base, master columns can only be
    applied after the join - so splitting them here keeps the caller from having to know which is
    which.
    """
    parts = []
    for col, vals in (col_filters or {}).items():
        cu = str(col).upper()
        # Master-owned key columns sit on the enrich side even though they are also physically on
        # the fact - _key_expr is what the grid shows, so it is what the filter has to test.
        if cu in PROD_KEY_MAP and _COL_RE.match(cu):
            if not enrich_only:
                continue
            clean = [str(v) for v in (vals or []) if str(v).strip() != ""]
            if clean:
                parts.append(f"{_key_expr(cu)} IN ({', '.join(_q(v) for v in clean)})")
            continue
        if cu in _ENRICH_FILT and _COL_RE.match(cu):
            if not enrich_only:
                continue
            clean = [str(v) for v in (vals or []) if str(v).strip() != ""]
            if clean:
                if cu == LOC_FLAG:
                    parts.append(f"{LOC_FLAG_EXPR} "
                                 f"IN ({', '.join(_q(v) for v in clean)})")
                elif cu == PLANT_COL:
                    # Off `pt`, the FULL master - NOT sm. Filtering plant type against the planning
                    # view could only ever return STORE and NA, so "show me the DCs" would come back
                    # empty and read as "the DCs hold nothing".
                    parts.append(f"{PLANT_FILT_EXPR} IN ({', '.join(_q(v) for v in clean)})")
                else:
                    tbl = "sm" if cu in STORE_ATTRS else "pm"
                    parts.append(f"{tbl}.{cu} IN ({', '.join(_q(v) for v in clean)})")
            continue
        if enrich_only or cu not in _FILT_COLS or not _COL_RE.match(cu):
            continue
        clean = [str(v) for v in (vals or []) if str(v).strip() != ""]
        if clean:
            parts.append(f"b.{cu} IN ({', '.join(_q(v) for v in clean)})")
    s = str(search or "").strip()
    if s and not enrich_only:
        parts.append(f"(TO_VARCHAR(b.ARTICLE_NUMBER) LIKE {_q(s + '%')} "
                     f"OR TO_VARCHAR(b.GEN_ART_NUMBER) LIKE {_q(s + '%')} "
                     f"OR UPPER(b.GEN_ART_DESC) LIKE UPPER({_q('%' + s + '%')}))")  # fact col name
    return parts


# Each day in the range becomes its own set of window aggregates, so the SQL grows with the span.
# Ten days is 10 x 100 conditional aggregates, which Snowflake handles; a quarter would not be
# sensible to ask for in one grid.
MAX_ANCHOR_DAYS = 31


def _build(req, use_stored=False, ordered=True):
    """SQL for the grid. `use_stored` picks the STORED windows (db-scripts/309) over re-aggregating
    the day-wise fact; the caller decides, because deciding needs a round trip to Snowflake.

    `ordered=False` drops the ORDER BY. The subtotal endpoint wraps this in an aggregate, where a
    sort is pure cost - and stripping it back off the finished string would be guesswork about which
    of the several ORDER BYs in the query was the outer one."""
    a, b = _range(req.from_date, req.to_date)
    k_tags = _tags(req.stock_tags)
    want_act = bool(getattr(req, "ref_act", False))
    art_cols = list(ART_COLS) if want_act else []
    want_grc = bool(getattr(req, "ref_grc", False))
    want_po = bool(getattr(req, "ref_po", False))
    grc_cols = list(GRC_COLS) if want_grc else []
    po_cols = list(PO_COLS) if want_po else []

    d0, d1 = datetime.fromisoformat(a).date(), datetime.fromisoformat(b).date()
    span = (d1 - d0).days + 1
    if span > MAX_ANCHOR_DAYS:
        raise HTTPException(
            400, f"{span} days requested. Each day carries its own TD/MTD/YTD window, so the range "
                 f"is capped at {MAX_ANCHOR_DAYS} days - narrow the dates.")
    anchors = [(d0 + timedelta(days=i)).isoformat() for i in range(span)]
    # THE UNDERLYING KPIs, not the tag names. _stock_pick filters the fact on KPI, and a composite
    # or a split name matches no row there - passing k_tags straight through would have pruned the
    # pick to nothing and reported "no stock" for a report that asked only for TOTAL_STORE_STOCK.
    # A rule tag contributes a prefix test rather than names; scan_filter resolves both.
    pick = _stock_pick(anchors, where=scan_filter(k_tags, "KPI"))

    # SALE, summed across the whole range. SALE_DAYS counts the days that actually sold - the
    # denominator needed to turn a range total into a rate, and not derivable from the totals.
    # SALE: the nine named windows plus the caller's own From/To range, EACH ANCHORED ON THE ROW'S
    # OWN DATE. The 27-Aug row gets MTD = 1-27 Aug; the 28-Aug row gets MTD = 1-28 Aug. Anchoring
    # every row on the To date gave both rows 1-28, which overstates every day but the last and
    # makes the column meaningless to read down.
    #
    # One branch per anchor day, UNIONed, then joined on the full day key. Nothing is stored per
    # window - each is a conditional aggregate over a date range derived from that day's date.
    # Column names reproduce the month-wise ones exactly so an older export still lines up.
    #
    # AVG_* averages over the days that measure was > 0 (the shipped rule) and SAL_DAYS / GM_DAYS
    # are its exact denominators; DATA_DAYS counts every day the key appeared. Keeping the three
    # day counts is what lets a reader reconcile an average back to its total.
    win_order = SALE_WINDOWS + ["RANGE"]
    sale_cols = [f"S_{w}__{meas}" for w in win_order for meas in WINDOW_MEASURES]

    branches, lo_all, hi_all = [], None, None
    anchor_spans = []                    # (anchor, widest-window lo, widest-window hi)
    for anc in anchors:
        wins = _window_ranges(anc)
        # RANGE stays the caller's own From/To on every row - it is the range they asked for, not a
        # window derived from the row, so it is deliberately NOT re-anchored.
        wins["RANGE"] = (datetime.fromisoformat(a).date(), datetime.fromisoformat(b).date())
        # Each branch scans only as far back as ITS OWN widest window needs.
        lo = min(v[0] for v in wins.values())
        hi = max(v[1] for v in wins.values())
        lo_all = lo if lo_all is None else min(lo_all, lo)
        hi_all = hi if hi_all is None else max(hi_all, hi)
        anchor_spans.append((anc, lo, hi))
        if use_stored:
            # The windows for this anchor are already materialised; the 100 conditional aggregates
            # below would only recompute them from a fact that may not hold the days.
            continue
        sel = []
        for w in win_order:
            w_a, w_b = wins[w]
            inw = f"DT BETWEEN '{w_a}'::DATE AND '{w_b}'::DATE"
            for meas in WINDOW_MEASURES:
                alias = f"S_{w}__{meas}"
                if meas.startswith("AVG_"):
                    src = meas[4:]                   # AVG_SALE_V -> SALE_V
                    sel.append(
                        f'ROUND(AVG(CASE WHEN {inw} AND {src} > 0 THEN {src} END),4) AS "{alias}"')
                elif meas == "SAL_DAYS":
                    sel.append(f'COUNT(CASE WHEN {inw} AND SALE_Q > 0 THEN 1 END) AS "{alias}"')
                elif meas == "GM_DAYS":
                    sel.append(f'COUNT(CASE WHEN {inw} AND GM_V > 0 THEN 1 END) AS "{alias}"')
                elif meas == "DATA_DAYS":
                    sel.append(f'COUNT(CASE WHEN {inw} THEN 1 END) AS "{alias}"')
                else:
                    sel.append(f'ROUND(SUM(CASE WHEN {inw} THEN {meas} END),4) AS "{alias}"')
        branches.append(
            f"SELECT {_q(anc)}::DATE ANCHOR_DT, ST_CD, ARTICLE_NUMBER, {', '.join(sel)} "
            f"FROM {SALE_TBL} WHERE DT BETWEEN '{lo}'::DATE AND '{hi}'::DATE GROUP BY 2,3")
    sale_agg = (_stored_sale_agg(a, b) if use_stored
                else "(" + " UNION ALL ".join(branches) + ")")
    scan_lo, scan_hi = lo_all, hi_all

    # GRC, WINDOWED EXACTLY LIKE SALE and for the same reason: it is a day-wise flow at
    # (day, plant, article), which is this grid's own grain. So G_MTD__GRC_V on a row is the
    # receipt for precisely the days S_MTD__SALE_V sold in, and the two read straight across.
    #
    # One branch per anchor day, each scanning only as far back as ITS widest window needs - the
    # same shape the sale block above uses, written out rather than shared because the sale block
    # has a stored-windows alternative and this has none.
    #
    # NOT restricted to receipt movements. The reversals and returns are part of what a store
    # received, they carry their own negative sign in the source, and GRC_GRS_* / GRC_RET_* split
    # them back out - see GRC_SRC.
    grc_agg = None
    if want_grc:
        gbr = []
        for anc, _alo, _ahi in anchor_spans:
            wins = _window_ranges(anc)
            wins["RANGE"] = (d0, d1)
            g_lo = min(v[0] for v in wins.values())
            g_hi = max(v[1] for v in wins.values())
            gsel = []
            for w in win_order:
                w_a, w_b = wins[w]
                inw = f"DT BETWEEN '{w_a}'::DATE AND '{w_b}'::DATE"
                for m in GRC_MEASURES:
                    gsel.append(f'ROUND({grc_sum(m, inw)},4) AS "{GRC_PREFIX}{w}__{m}"')
            # The view's own keys, renamed to this grid's: a plant code that needs trimming and an
            # article number that is TEXT here and a NUMBER on every fact this joins to.
            gbr.append(
                f"SELECT {_q(anc)}::DATE ANCHOR_DT, TRIM(STORE_CODE) ST_CD, "
                f"TRY_TO_NUMBER(ARTICLE_NUMBER) ARTICLE_NUMBER, {', '.join(gsel)} "
                f"FROM (SELECT POSTING_DATE_DT DT, STORE_CODE, ARTICLE_NUMBER, GRC_QTY, GRC_VALUE "
                f"FROM {GRC_TBL} "
                f"WHERE POSTING_DATE_DT BETWEEN '{g_lo}'::DATE AND '{g_hi}'::DATE) "
                f"GROUP BY 1,2,3")
        grc_agg = "(" + " UNION ALL ".join(gbr) + ")"

    # PO: the open book, and the ONE block on this grid that is not per-day.
    #
    # It is a position as it stands NOW - that view holds only lines still pending - so there is no
    # day in it to anchor on. Rather than repeat one live number across every day of a multi-day
    # range, which a reader totalling the column would multiply by the span, it is stamped on the
    # report's LAST day only and every earlier row reads blank. On the usual one-day view of this
    # page (From = To) that is simply "the open book", which is what it is.
    po_agg = None
    if want_po:
        preds = po_preds(f"'{d1}'::DATE")
        osel = []
        for m in PO_MEASURES:
            if m in PO_SRC:
                osel.append(f'ROUND({po_sum(m)},4) AS "{PO_PREFIX}{m}"')
            else:
                src, which = PO_COND_AGG[m]
                osel.append(f'ROUND(SUM(CASE WHEN {preds[which]} THEN {src} END),4) '
                            f'AS "{PO_PREFIX}{m}"')
        po_agg = (f"(SELECT '{d1}'::DATE ANCHOR_DT, TRIM(STORE_CODE) ST_CD, "
                  f"ARTICLE_NUMBER, {', '.join(osel)} FROM {PO_TBL} GROUP BY 1,2,3)")

    # STOCK, a POSITION at the end of the range - never a sum across it, which would be stock-DAYS.
    stock_sel, stock_cols = [], []
    for t in k_tags:
        # tag_measure_sql, not a conditional SUM written here: a COMPOSITE selects several KPIs at
        # once, one of its members can itself be a plant-type slice, and a PICK tag is not a row
        # selection at all - it chooses between two finished aggregates. Plain tags come back as
        # the same `ROUND(SUM(CASE WHEN s.KPI = '...' ...` this line always emitted.
        for m in STOCK_MEASURES:
            alias = f"K_{t}__{m}"
            stock_cols.append(alias)
            stock_sel.append(f'{tag_measure_sql(t, m, "s.KPI")} AS "{alias}"')
    # A composite whose member is a plant-type slice needs the plant lookup HERE, inside the
    # aggregate - unlike the split columns, which are an outer re-projection of a finished column
    # and read the outer `pt`. Same alias, different scope; Snowflake resolves each to its own.
    _pt_join = (f" LEFT JOIN {plant_lookup_sql()} pt ON pt.ST_CD = TRIM(s.ST_CD)"
                if any(tag_needs_plant(t) for t in k_tags) else "")
    # The same pruning on the aggregate side, off the same function so the two cannot disagree.
    # Without it the scan reads every tag's rows and the CASE expressions discard the ones nobody
    # asked for - work done purely to throw away. Tag names never appear in it: there is no row in
    # the fact stamped '0099_STORE' or 'TOTAL_STORE_STOCK' for an IN list to match.
    _kpi_in = scan_filter(k_tags, "s.KPI")
    stock_agg = (f"(SELECT p2.ANCHOR_DT, s.ST_CD, s.ARTICLE_NUMBER, {', '.join(stock_sel)} "
                 f"FROM {STOCK_TBL} s JOIN {pick} p2 "
                 f"ON p2.ST_CD = s.ST_CD AND p2.KPI = s.KPI AND p2.DT = s.DT"
                 f"{_pt_join}"
                 f"{_kpi_in} "
                 f"GROUP BY 1,2,3)")

    # BASE: a variant appears if it sold in the range OR held stock at the end of it.
    # BASE: for each anchor day, every key with sale anywhere in THAT DAY'S WIDEST WINDOW, plus
    # every key holding stock as at that day - all stamped with the ANCHOR date, not the date the
    # sale or the stock snapshot actually fell on.
    #
    # The wide span is what makes a window column ADD UP. Restricting the base to sale or stock on
    # the anchor day itself dropped every variant that sold earlier in the month and was gone by
    # the anchor, so the MTD column totalled 59.2 L against a true 71.4 L - a 17% shortfall that
    # looked like a calculation error and was really a missing row.
    #
    # It does NOT turn history into rows: a key that sold on the 3rd contributes ONE row, dated the
    # anchor, carrying its month-to-date figure. The dates in between are columns, never rows.
    base_parts = []
    if use_stored:
        # The stored table already holds exactly one row per key that had activity in ANY of the
        # nine windows, stamped with the anchor. It is the base - there is nothing to rescan.
        base_parts.append(
            f"SELECT DT, ST_CD, ARTICLE_NUMBER, GEN_ART_NUMBER, GEN_ART_DESC, "
            f"COLOR, SIZE_1, MAJ_CAT, SEG FROM {KPI_TBL} "
            f"WHERE DT BETWEEN {_q(a)}::DATE AND {_q(b)}::DATE")
    else:
        for anc, lo, hi in anchor_spans:
            base_parts.append(
                f"SELECT {_q(anc)}::DATE DT, ST_CD, ARTICLE_NUMBER, GEN_ART_NUMBER, GEN_ART_DESC, "
                f"COLOR, SIZE_1, MAJ_CAT, SEG FROM {SALE_TBL} "
                f"WHERE DT BETWEEN '{lo}'::DATE AND '{hi}'::DATE")
    base_parts.append(
        f"SELECT p3.ANCHOR_DT DT, s.ST_CD, s.ARTICLE_NUMBER, s.GEN_ART_NUMBER, s.GEN_ART_DESC, "
        f"s.COLOR, s.SIZE_1, s.MAJ_CAT, s.SEG "
        f"FROM {STOCK_TBL} s JOIN {pick} p3 "
        f"ON p3.ST_CD = s.ST_CD AND p3.KPI = s.KPI AND p3.DT = s.DT")
    base_sql = f"""
        SELECT DT, ST_CD, ARTICLE_NUMBER,
               ANY_VALUE(GEN_ART_NUMBER) GEN_ART_NUMBER, ANY_VALUE(GEN_ART_DESC) GEN_ART_DESC,
               ANY_VALUE(COLOR) COLOR, ANY_VALUE(SIZE_1) SIZE_1,
               ANY_VALUE(MAJ_CAT) MAJ_CAT, ANY_VALUE(SEG) SEG
          FROM ({" UNION ALL ".join(base_parts)})
         GROUP BY 1,2,3"""

    sm_sql = (f"(SELECT TRIM(ST_CD) ST_CD, {', '.join(STORE_ATTRS)} FROM {STORE_TBL} "
              f"QUALIFY ROW_NUMBER() OVER (PARTITION BY TRIM(ST_CD) "
              f"ORDER BY UPDATED_AT DESC NULLS LAST) = 1)")
    # A second, separate join rather than widening sm_sql, even though both now read the SAME MIS
    # master: sm is keyed for attributes and pt for the plant-type split, and they are the two
    # things most likely to be narrowed differently later. Folding them together would mean the
    # next filter added to one of them silently applies to the other.
    pt_sql = plant_lookup_sql()
    # Aliased to the report's own names so nothing downstream has to know the view's vocabulary.
    # TRY_TO_NUMBER because ARTICLE_NUMBER is TEXT in this view; ARTICLE_CREATION_DATE stands in
    # for the MODIFY_DATE the old master ordered by, since the view carries no modified stamp.
    # PROD_KEY_MAP rides along in the same subquery rather than in a second join: it is the same
    # master row, keyed the same way, so asking for it twice would only buy a second scan.
    pm_cols = ", ".join(f"v.{src} AS {alias}"
                        for alias, src in {**PROD_ATTR_MAP, **PROD_KEY_MAP,
                                           **MAP_ATTR_MAP}.items())
    # The GENERIC description lives on the parent article's OWN row, where ARTICLE_NUMBER equals
    # GEN_ARTICLE_NUMBER - the view repeats the variant text in its GEN_ARTICLE_DESC column, so
    # reading that field would just hand back the variant description a second time. Scoped to
    # parent rows only, so it is a small lookup rather than a self-join over 3.6M rows.
    gen_sql = (f"(SELECT TRY_TO_NUMBER(ARTICLE_NUMBER) GEN_ART_NUMBER, "
               f"ARTICLE_DESCRIPTION AS GEN_ART_DESC FROM {PROD_TBL} "
               f"WHERE TRY_TO_NUMBER(ARTICLE_NUMBER) IS NOT NULL "
               f"AND TRY_TO_NUMBER(ARTICLE_NUMBER) = TRY_TO_NUMBER(GEN_ARTICLE_NUMBER) "
               f"QUALIFY ROW_NUMBER() OVER (PARTITION BY TRY_TO_NUMBER(ARTICLE_NUMBER) "
               f"ORDER BY ARTICLE_CREATION_DATE DESC NULLS LAST) = 1)")
    pm_sql = (f"(SELECT TRY_TO_NUMBER(v.ARTICLE_NUMBER) ARTICLE_NUMBER, {pm_cols} "
              f"FROM {PROD_TBL} v WHERE TRY_TO_NUMBER(v.ARTICLE_NUMBER) IS NOT NULL "
              f"QUALIFY ROW_NUMBER() OVER (PARTITION BY TRY_TO_NUMBER(v.ARTICLE_NUMBER) "
              f"ORDER BY v.ARTICLE_CREATION_DATE DESC NULLS LAST) = 1)")

    # Each side is already ONE row per key, so every join here is 1:1 and nothing can fan out. The
    # month-wise version joined two multi-row tables at once and inflated sale by 20.7%.
    joined = (f"({base_sql}) b "
              f"LEFT JOIN {sale_agg} sa ON sa.ANCHOR_DT = b.DT AND sa.ST_CD = b.ST_CD "
              f"AND sa.ARTICLE_NUMBER = b.ARTICLE_NUMBER "
              f"LEFT JOIN {stock_agg} st ON st.ANCHOR_DT = b.DT AND st.ST_CD = b.ST_CD "
              f"AND st.ARTICLE_NUMBER = b.ARTICLE_NUMBER "
              f"LEFT JOIN {sm_sql} sm ON sm.ST_CD = TRIM(b.ST_CD) "
              f"LEFT JOIN {pt_sql} pt ON pt.ST_CD = TRIM(b.ST_CD) "
              f"LEFT JOIN {pm_sql} pm ON pm.ARTICLE_NUMBER = b.ARTICLE_NUMBER "
              f"LEFT JOIN {gen_sql} gm ON gm.GEN_ART_NUMBER = b.GEN_ART_NUMBER "
              # DAY REF, after pm because it joins on the MAJ_CAT the report DISPLAYS
              # (_key_expr - the master's value, falling back to the fact stamp), not on the
              # stamp alone. Joining the stamp while showing the master value would put a
              # category's reference row beside a row labelled with a different category, on
              # exactly the 21,580 re-classified articles PROD_KEY_MAP exists to handle.
              f"LEFT JOIN {_ref_sql(a, b, req.col_filters)} rf ON rf.CAL_DATE = b.DT "
              f"AND rf.ST_CD = TRIM(b.ST_CD) AND rf.MAJ_CAT = TRIM({_key_expr('MAJ_CAT')}) "
              # ACT per ARTICLE, on the row's own key - a plain 1:1 join, no category involved.
              + (f"LEFT JOIN {_art_act_sql(a, b, req.col_filters)} af ON af.DT = b.DT "
                 f"AND af.ST_CD = b.ST_CD AND af.ARTICLE_NUMBER = b.ARTICLE_NUMBER"
                 if want_act else "")
              # GRC on the full day key, 1:1 like the sale aggregate beside it. PO on the key
              # WITHOUT the day in its own grain - its ANCHOR_DT is the report's last day, so the
              # equality here is what confines one live position to one row per key.
              + (f" LEFT JOIN {grc_agg} gr ON gr.ANCHOR_DT = b.DT AND gr.ST_CD = TRIM(b.ST_CD) "
                 f"AND gr.ARTICLE_NUMBER = b.ARTICLE_NUMBER" if want_grc else "")
              + (f" LEFT JOIN {po_agg} po ON po.ANCHOR_DT = b.DT AND po.ST_CD = TRIM(b.ST_CD) "
                 f"AND po.ARTICLE_NUMBER = b.ARTICLE_NUMBER" if want_po else ""))

    conds = _filter_sql(req.col_filters, req.search) + _filter_sql(req.col_filters, None, True)
    where_sql = (" WHERE " + " AND ".join(conds)) if conds else ""

    enrich_cols = ["GEN_ART_DESC", LOC_FLAG] + STORE_ATTRS + PROD_ATTRS
    enrich_sel = (['gm.GEN_ART_DESC',
                   f"{LOC_FLAG_EXPR} AS {LOC_FLAG}"]
                  + [f"sm.{c}" for c in STORE_ATTRS] + [f"pm.{c}" for c in PROD_ATTRS])
    # THE PLANT-TYPE SPLIT, appended AFTER the ref block for the same reason the ref block was
    # appended after everything else: column POSITION is what an existing spreadsheet depends on.
    # PLANT_TYPE is a text attribute and would read better among the other store attributes, but
    # putting it there would shift every sale, stock and ref column one place to the right in every
    # export ever taken. It goes on the end with the columns it explains.
    split_defs = _split_cols(k_tags)
    split_cols = [alias for alias, _src, _pred in split_defs]
    # PLANT_TYPE UNCONDITIONALLY, the split block only when its tag is in scope. The filter is
    # offered whatever stock tags are picked - it is a store attribute, not a property of a tag -
    # so hiding the column with the block would let you filter the grid to DCs and then have
    # nothing on screen saying which rows those are.
    # MAP_COLS on the very end, after the split - see MAP_ATTR_MAP. They are master columns and
    # would read better beside COLOR and SIZE_1, which is exactly the move the append-only rule
    # forbids: it would shift every measure column two places right in every export.
    tail_cols = [PLANT_COL] + split_cols + MAP_COLS
    tail_sel = ([PLANT_EXPR] + [f'IFF({pred}, st."{src}", NULL) AS "{alias}"'
                                for alias, src, pred in split_defs]
                + [f"pm.{c}" for c in MAP_COLS])

    # REF LAST, always. Column POSITION is what an existing spreadsheet depends on - the same
    # append-only rule the stock tags follow - so eighteen new columns go on the END and every
    # export taken before this change still lines up column for column.
    # GRC AND PO ON THE VERY END, after the tail block, the same append-only rule every block
    # before them followed - and the reason is unchanged: column POSITION is what an existing
    # spreadsheet depends on, so an export taken before either existed still lines up column for
    # column. Both are empty lists unless the caller asked for them.
    cols = (KEY_COLS + enrich_cols + sale_cols + stock_cols + art_cols + REF_COLS + tail_cols
            + grc_cols + po_cols)

    # NULLS LAST ON EVERY MEASURE SORT, and it is not cosmetic on this grid.
    #
    # Snowflake orders DESC with NULLS FIRST. The base here is a UNION - a variant appears if it
    # sold OR held stock, and only ~30% carry both - so most rows are blank in any one measure.
    # Sorting a measure "biggest first" therefore opened on a page of dashes, which reads as an
    # empty report rather than as a sort. The DEFAULT order has always said NULLS LAST; this is
    # the click-to-sort path finally agreeing with it.
    direction = ("DESC" if str(req.sort_dir or "").lower() == "desc" else "ASC")
    meas_dir = direction + " NULLS LAST"
    key_upper = {c.upper() for c in KEY_COLS}
    sc = str(req.sort_col or "")
    if sc.upper() in key_upper and _COL_RE.match(sc):
        order = f"{_key_expr(sc.upper())} {direction}"
    elif sc in sale_cols:
        order = f'sa."{sc}" {meas_dir}'
    elif sc in stock_cols:
        order = f'st."{sc}" {meas_dir}'
    elif sc in art_cols:
        order = f'af."{sc}" {meas_dir}'
    elif sc in REF_COLS:
        order = f'rf."{sc}" {meas_dir}'
    elif sc in grc_cols:
        order = f'gr."{sc}" {meas_dir}'
    elif sc in po_cols:
        order = f'po."{sc}" {meas_dir}'
    elif sc in split_cols:
        # The EXPRESSION, not the output alias. Snowflake would accept the alias here, but the
        # ORDER BY is assembled well before the projection is and a name that only exists in the
        # other half is exactly how the two halves drift apart.
        _a, _src, _pred = next(d for d in split_defs if d[0] == sc)
        order = f'IFF({_pred}, st."{_src}", NULL) {meas_dir}'
    elif sc.upper() == PLANT_COL:
        order = f"{PLANT_FILT_EXPR} {direction}"
    elif sc.upper() in _ENRICH_FILT and _COL_RE.match(sc):
        order = (f"{'sm' if sc.upper() in STORE_ATTRS else 'pm'}.{sc.upper()} {direction}"
                 if sc.upper() != LOC_FLAG else
                 f"{LOC_FLAG_EXPR} {direction}")
    else:
        # Date first, because the grid is day-wise and a reader scanning it expects the days in
        # order; then that day's biggest sellers - TD, which IS the row's own date - so each day
        # opens with numbers on it; then generic parents last, since their 10-digit numbers would
        # otherwise lead and they carry the least activity; then the key, so paging stays
        # deterministic among the many ties.
        order = ('b.DT, sa."S_TD__SALE_V" DESC NULLS LAST, '
                 "IFF(b.ARTICLE_NUMBER = b.GEN_ART_NUMBER, 1, 0), b.ST_CD, b.ARTICLE_NUMBER")

    proj = ", ".join([f"{_key_expr(c)} AS {c}" for c in KEY_COLS] + enrich_sel
                     + [f'sa."{c}"' for c in sale_cols] + [f'st."{c}"' for c in stock_cols]
                     + [f'af."{c}"' for c in art_cols] + [f'rf."{c}"' for c in REF_COLS]
                     + tail_sel
                     + [f'gr."{c}"' for c in grc_cols] + [f'po."{c}"' for c in po_cols])
    data_sql = (f"SELECT {proj} FROM {joined}{where_sql}"
                + (f" ORDER BY {order}" if ordered else ""))
    count_sql = f"SELECT COUNT(*) FROM {joined}{where_sql}"
    return data_sql, count_sql, cols


def _result_body(req: CombinedReq, guard):
    page = max(1, int(req.page or 1))
    limit = max(1, min(int(req.limit or 200), MAX_LIMIT))
    a, b = _range(req.from_date, req.to_date)
    conn = _conn()
    try:
        cur = conn.cursor()
        use_stored, src = _resolve_source(cur, req, a, b, guard)
        data_sql, count_sql, _cols = _build(req, use_stored)
        guard.execute(cur, count_sql)
        total = cur.fetchone()[0]
        guard.execute(cur, f"{data_sql} LIMIT {limit} OFFSET {(page - 1) * limit}")
        columns = [c[0] for c in cur.description]
        rows = [[_fmt_str(v) for v in r] for r in cur.fetchall()]
    finally:
        conn.close()
    return {"fromDate": a, "toDate": b, "columns": columns, "rows": rows,
            "total": total, "page": page, "limit": limit, "shown": len(rows),
            # Which side of db-scripts/309 served these numbers. The page shows it: the two can
            # legitimately disagree while the day-wise fact is short of history, and a reader
            # comparing two screenshots needs to know which one they are looking at.
            "saleSource": src}


@router.post("/result")
async def sf_var_combined_result(req: CombinedReq, request: Request):
    """One page of the grid. Async so an abandoned request takes its Snowflake query with it -
    this page re-fires on every date, tag, filter, search, sort and page change; see
    app/sf_cancel.py."""
    return await sf_cancel.guarded(request, lambda g: _result_body(req, g), get_sf_connection,
                                   label="sf-var-combined/result")


# ══════════════════════════════════════════════════════════════════════════════
# summary
# ══════════════════════════════════════════════════════════════════════════════
class CombinedSummaryReq(BaseModel):
    from_date: str
    to_date: str
    col_filters: Optional[dict] = None
    search: Optional[str] = None


def _summary_body(req: CombinedSummaryReq, guard):
    """How the keys split between the two sides, plus the range totals.

    The split is the headline, not a footnote: measured on the month-wise predecessors only ~30% of
    keys carried both, so "holding stock, not selling" and "sold, nothing left" are each about a
    third of the report.
    """
    a, b = _range(req.from_date, req.to_date)
    # A LIST - _stock_pick anchors per day now. The summary is a range headline, so the only anchor
    # it needs is the closing one.
    pick = _stock_pick([b])
    # The SAME span the grid's base uses, so the cards and the row count agree. Counting only sale
    # inside From/To made the cards say 73,272 while the grid showed 75,667 - the difference being
    # the variants that sold earlier in the window and were gone by the To date.
    _w = _window_ranges(b)
    _w["RANGE"] = (datetime.fromisoformat(a).date(), datetime.fromisoformat(b).date())
    s_lo, s_hi = min(v[0] for v in _w.values()), max(v[1] for v in _w.values())
    conn = _conn()
    try:
        cur = conn.cursor()
        guard.execute(cur, f"""
            WITH sa AS (SELECT DISTINCT ST_CD, ARTICLE_NUMBER FROM {SALE_TBL}
                         WHERE DT BETWEEN '{s_lo}'::DATE AND '{s_hi}'::DATE),
                 st AS (SELECT DISTINCT s.ST_CD, s.ARTICLE_NUMBER FROM {STOCK_TBL} s
                          JOIN {pick} p ON p.ST_CD = s.ST_CD AND p.KPI = s.KPI
                                       AND p.DT = s.DT),
                 k  AS (SELECT IFF(sa.ST_CD IS NULL, 0, 1) IN_SALE,
                               IFF(st.ST_CD IS NULL, 0, 1) IN_STOCK
                          FROM sa FULL OUTER JOIN st
                            ON sa.ST_CD = st.ST_CD AND sa.ARTICLE_NUMBER = st.ARTICLE_NUMBER)
            SELECT COUNT(*), SUM(IFF(IN_SALE=1 AND IN_STOCK=1,1,0)),
                   SUM(IFF(IN_SALE=1 AND IN_STOCK=0,1,0)),
                   SUM(IFF(IN_SALE=0 AND IN_STOCK=1,1,0)) FROM k""")
        r = cur.fetchone()
        out = {"unionKeys": int(r[0] or 0), "both": int(r[1] or 0),
               "saleOnly": int(r[2] or 0), "stockOnly": int(r[3] or 0)}

        guard.execute(cur, f"""SELECT ROUND(SUM(SALE_V),2), ROUND(SUM(SALE_Q),2), ROUND(SUM(GM_V),2),
                                      ROUND(SUM(DISCOUNT_V),2), COUNT(DISTINCT DT)
                                 FROM {SALE_TBL} WHERE DT BETWEEN {_q(a)}::DATE AND {_q(b)}::DATE""")
        r = cur.fetchone()
        out["sale"] = {"SALE_V": _f(r[0]), "SALE_Q": _f(r[1]), "GM_V": _f(r[2]),
                       "DISCOUNT_V": _f(r[3]), "days": int(r[4] or 0)}

        guard.execute(cur, f"""SELECT s.KPI, ROUND(SUM(s.STOCK_V),2), ROUND(SUM(s.STOCK_Q),2), MAX(s.DT)
                                 FROM {STOCK_TBL} s JOIN {pick} p
                                   ON p.ST_CD = s.ST_CD AND p.KPI = s.KPI AND p.DT = s.DT
                                GROUP BY 1 ORDER BY 1""")
        out["stockByTag"] = {t: {"v": _f(v), "q": _f(q), "asOf": _fmt_str(d)}
                             for t, v, q, d in cur.fetchall()}
    finally:
        conn.close()
    return out


@router.post("/summary")
async def sf_var_combined_summary(req: CombinedSummaryReq, request: Request):
    """The split cards and the range headline. Guarded for the same reason as /result: it
    re-fires on every date and filter change, and the FULL OUTER JOIN of the two key sets
    behind it is not cheap."""
    return await sf_cancel.guarded(request, lambda g: _summary_body(req, g), get_sf_connection,
                                   label="sf-var-combined/summary")


# ══════════════════════════════════════════════════════════════════════════════
# export
# ══════════════════════════════════════════════════════════════════════════════
class CombinedExportReq(CombinedReq):
    fmt: str = "csv"


_XLSX_SHEET_MAX = 1_000_000


def _totals_body(req: CombinedReq, guard):
    """Subtotals for every measure column, over the WHOLE filtered result - not the visible page.

    HOW EACH COLUMN IS SUBTOTALLED, because one rule does not fit them:
      SALE_V / SALE_Q / GM_V / DISCOUNT_V, the stock measures, and the three day counts -> SUM.
      AVG_*                                                                              -> AVG.
        Summing per-key averages produces a number with no meaning; averaging them at least answers
        "what did an average selling day look like across these keys".

    CUMULATIVE WINDOWS ARE NOT TOTALLED ON A MULTI-DAY RANGE. Every window except TD is anchored on
    the row's own date, so on a 3-day range the MTD column holds three OVERLAPPING month-to-dates and
    adding them counts the same days repeatedly. The page says so in its own column tooltips; a
    subtotal row that quietly printed that sum would contradict it and look authoritative doing it.
    Those columns come back null, and `cumulativeSuppressed` says why so the page can label them.
    """
    a, b = _range(req.from_date, req.to_date)
    span = (datetime.fromisoformat(b).date() - datetime.fromisoformat(a).date()).days + 1
    k_tags = _tags(req.stock_tags)
    sale_cols = [f"S_{w}__{m}" for w in (SALE_WINDOWS + ["RANGE"]) for m in WINDOW_MEASURES]
    # The plant-type splits total exactly like the tag they came out of - they ARE that column,
    # partitioned - so they follow the same rule (_summable: a POSITION, additive on one day only)
    # and land in the same list rather than getting one of their own.
    # A PICK TAG SUBTOTALS AS A SUM OF PICKS, not as a pick of sums, and that is the right way
    # round: every row has already chosen the one rack its own store has, so adding the column up
    # adds DH24's MSA to DW01's exactly once. Picking again over the total would report one DC.
    stock_cols = ([f"K_{t}__{m}" for t in k_tags for m in STOCK_MEASURES]
                  + [alias for alias, _s, _p in _split_cols(k_tags)])

    # ON A MULTI-DAY RANGE, TD IS THE ONLY COLUMN THAT ADDS UP. TD is the row's OWN day; everything
    # else on the row describes a period, and the rows repeat that period once per anchor.
    #
    # Two traps, both caught by a 2-day test rather than by reading the code:
    #   RANGE is the caller's From/To evaluated for the key, so every anchor row carries the SAME
    #   value. Summing 2 days gave Rs 44.41 Cr against a true Rs 22.20 Cr. Repeated is not additive.
    #   STOCK is a POSITION at the anchor, not a flow. Adding the 30th's position to the 31st's counts
    #   the same garment twice - a stock-DAYS figure, which is the error this whole report is built to
    #   avoid. It read Rs 2,115 Cr over 2 days against roughly half that truly held.
    def _summable(c):
        if span == 1:
            return True
        # G_ joins S_ here, not as a favour but because it has the identical property: a GRC column
        # is a WINDOW over days anchored on the row's own date, so every window but TD is a period
        # the rows repeat once per anchor, and adding it up would count those days once per anchor.
        return (c.startswith("S_") or c.startswith("G_")) and c.split("__")[0][2:] == "TD"

    # REF COLUMNS ARE NEVER SUBTOTALLED, ON ANY SPAN. They are CATEGORY figures repeated across
    # every variant of that store-category-day, so a column of them holds the same number many
    # times over and summing it counts the category once per variant - on a busy category that is
    # a hundredfold overstatement, and it would print looking authoritative. Averaging is no better:
    # the mean of a repeated value weights each category by how many variants it happens to carry.
    # There is no honest one-number roll-up of a repeated value, so the page shows none and labels
    # the blank. This is why they are absent from the list below rather than filtered out of it.
    # The per-article ACT columns DO total on any span, because each is the row's OWN day - the
    # same property that makes TD the one summable window. They are flows only; the stock half of
    # Day Ref's ACT is this page's own 0001 / 0006 stock tags, which the stock rule above governs.
    art_cols = list(ART_COLS) if getattr(req, "ref_act", False) else []
    grc_cols = list(GRC_COLS) if getattr(req, "ref_grc", False) else []
    # PO TOTALS ON ANY SPAN, and that is a property of how it is joined rather than a rule bent for
    # it. The open book is one live position with no day in it, so _build stamps it on the report's
    # LAST day only - which means each key contributes its pending quantity to exactly one row of
    # the grid, whatever the span. Summing the column therefore counts every open line once. Had it
    # been repeated per anchor instead, this would be the RANGE trap above with a different name.
    po_cols = list(PO_COLS) if getattr(req, "ref_po", False) else []
    picked = ([c for c in sale_cols + stock_cols + grc_cols if _summable(c)]
              + art_cols + po_cols)
    aggs = []
    for c in picked:
        meas = c.split("__")[1]
        fn = "AVG" if meas.startswith("AVG_") else "SUM"
        aggs.append(f'ROUND({fn}("{c}"),4) AS "{c}"')

    conn = _conn()
    try:
        cur = conn.cursor()
        use_stored, src = _resolve_source(cur, req, a, b, guard)
        data_sql, _c, _cols = _build(req, use_stored, ordered=False)
        guard.execute(cur, f"SELECT COUNT(*) AS N_ROWS, {', '.join(aggs)} FROM ({data_sql})")
        names = [d[0] for d in cur.description]
        row = cur.fetchone() or []
        vals = dict(zip(names, row))
    finally:
        conn.close()
    n_rows = int(vals.pop("N_ROWS", 0) or 0)
    return {"fromDate": a, "toDate": b, "rows": n_rows, "saleSource": src,
            "totals": {k: _f(v) for k, v in vals.items()},
            "cumulativeSuppressed": span > 1,
            "days": span}


@router.post("/totals")
async def sf_var_combined_totals(req: CombinedReq, request: Request):
    """The subtotal row - the most expensive single statement on this warehouse, one aggregate
    over the WHOLE filtered result, measured at ~13 minutes and ~9 GB scanned. Which makes it
    the one an abandoned request was most worth stopping."""
    return await sf_cancel.guarded(request, lambda g: _totals_body(req, g), get_sf_connection,
                                   label="sf-var-combined/totals")


class KpiLoadReq(BaseModel):
    """One ANCHOR date, not a range. The windows are anchored on a single day by definition, so a
    range here would only ever mean "repeat this N times" - which the caller can do, and should do
    knowingly, because each anchor is its own ~24M rows."""
    anchor_date: str
    stores: Optional[list] = None
    triggered_by: Optional[str] = "manual"
    operator: Optional[str] = None


@router.post("/kpi-load")
def sf_var_combined_kpi_load(req: KpiLoadReq):
    """Compute and SAVE the nine KPI windows for one anchor date (db-scripts/309).

    Reads SAP_VBRP directly, so this is correct for MTD / QTR / YTD even when DAY_WISE_ST_VAR_SALE
    holds no history for the days inside those windows - which is exactly the state that made the
    computed path read short. DELETE-then-INSERT for the anchor, so a re-run is authoritative.

    Not capped by MAX_LOAD_DAYS: there is no range to cap. It is capped by reality instead - all
    stores is ~24M rows and minutes of warehouse time, so the page asks before firing it.
    """
    anc = _check_date(req.anchor_date, "anchorDate")
    stores, seen = [], set()
    for x in (req.stores or []):
        c = str(x or "").strip().upper()
        if not c or c in seen:
            continue
        if not _STORE_RE.match(c):
            raise HTTPException(400, f"store must be a short store code like HA10 (got '{c}')")
        seen.add(c)
        stores.append(c)
    st_lit = _q(",".join(stores)) if stores else "NULL"

    run_group = mis_run_log.new_id()
    trig = (req.triggered_by or "manual").strip().lower()
    if trig not in ("manual", "scheduled", "monitor"):
        trig = "manual"
    # from_dt and to_dt are both the anchor: the log's range columns describe WHAT WAS ASKED FOR,
    # and what was asked for is one day. The windows the proc actually read reach a year back and
    # are named in the proc's own return message, which the log keeps verbatim.
    run_id = mis_run_log.start(run_group=run_group, kind="kpi", from_dt=anc, to_dt=anc,
                               stores=stores, triggered_by=trig, operator=req.operator)
    t0 = time.time()
    conn = _conn()
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"CALL V2RETAIL.BRONZE.SP_DAY_WISE_ST_VAR_SALE_KPI_LOAD("
                        f"{_q(anc)}::DATE, {st_lit})")
            row = cur.fetchone()
            msg = row[0] if row else ""
        except Exception as e:
            mis_run_log.finish(run_id, status="FAILED", error=e,
                               started_ms=(time.time() - t0) * 1000)
            raise
        mis_run_log.finish(run_id, status="SUCCESS", proc_msg=msg,
                           started_ms=(time.time() - t0) * 1000)
        logger.info("[sf-var-combined] kpi: %s", msg)
    finally:
        conn.close()
    _params_invalidate()
    return {"anchorDate": anc, "stores": stores, "runGroup": run_group, "message": msg}


@router.post("/export")
def sf_var_combined_export(req: CombinedExportReq):
    a, b = _range(req.from_date, req.to_date)
    conn = _conn()
    cur = conn.cursor()
    use_stored, _src = _resolve_source(cur, req, a, b)
    data_sql, _c, _cols = _build(req, use_stored)
    cur.execute(data_sql)
    # RAW names - see the note by MAX_LIMIT.
    cols = [c[0] for c in cur.description]
    base = f"VAR_SALE_STOCK_{a}_to_{b}"
    if req.fmt == "xlsx":
        import openpyxl
        wb = openpyxl.Workbook(write_only=True)
        ws = wb.create_sheet("Data1")
        ws.append(cols)
        n, sheet = 0, 1
        while True:
            batch = cur.fetchmany(2000)
            if not batch:
                break
            for r in batch:
                if n and n % _XLSX_SHEET_MAX == 0:
                    sheet += 1
                    ws = wb.create_sheet(f"Data{sheet}")
                    ws.append(cols)
                ws.append([_fmt_str(v) for v in r])
                n += 1
        conn.close()
        buf = io.BytesIO(); wb.save(buf); buf.seek(0)
        return StreamingResponse(
            buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{base}.xlsx"'})
    return _stream_csv(cur, cols, f"{base}.csv")


# ══════════════════════════════════════════════════════════════════════════════
# load — run the two day-wise loader procs for a date range
# ══════════════════════════════════════════════════════════════════════════════
class LoadReq(BaseModel):
    from_date: str
    to_date: str
    # `stores` is what the page sends now. `store` is kept because callers predating the
    # multi-select still post a single code, and dropping it would silently load EVERY store for
    # them - the most expensive possible way to break backwards compatibility.
    stores: Optional[list] = None
    store: Optional[str] = None
    # Who asked, for BRONZE.MIS_LOAD_RUN. Optional and defaulted rather than required: the
    # scheduler and the monitor both set it, and a caller predating the run log still logs -
    # as 'manual', which is what it was before anything set this at all.
    triggered_by: Optional[str] = "manual"
    operator: Optional[str] = None
    # Look back this many days BEFORE the load and fill the ET block of any day that has none
    # (see _et_heal). 0 = off, which is what the page's own Load button sends; the scheduler sends 7.
    heal_days: Optional[int] = 0


_STORE_RE = re.compile(r"^[A-Z0-9_-]{1,20}$")

STOCK_TBL_ET_SRC = "ET_STOCK_DATA"
MAX_HEAL_DAYS = 14

# Block 1 of SP_DAY_WISE_ST_VAR_STOCK_LOAD, alone, for ONE day - byte-for-byte the proc's product
# key and aggregation, so a healed day cannot differ from a day the proc loaded.
#
# WHY NOT JUST RE-RUN THE PROC. The nightly load fires before SAP's ET extract has always landed;
# on those nights block 1 inserts 0 rows while the LQUA / PRD / PTL / HUB blocks load, and the run
# reads SUCCESS. By the time anyone notices, LQUA_SNAPSHOT and ET_ZWM_PRD_BCG_REP_RFC have rolled
# past that date (~2 days kept), and the proc DELETEs the whole day before re-inserting - a re-run
# would trade the missing ET rows for missing LQ_* / PRD rows. This inserts only, and the NOT
# EXISTS makes a second run of it a no-op. 9 days were found this way on 17-Oct-2026.
_ET_HEAL_SQL = """
INSERT INTO V2RETAIL.BRONZE.DAY_WISE_ST_VAR_STOCK
    (DT, ST_CD, ARTICLE_NUMBER, GEN_ART_NUMBER, GEN_ART_DESC, COLOR, SIZE_1, MAJ_CAT, SEG,
     KPI, SRC, STOCK_V, STOCK_Q, LOG_DATE, UPDATE_DT)
WITH p AS (
    SELECT LTRIM(v.ARTICLE_NUMBER,'0')                      mat_key,
           TRY_TO_NUMBER(v.ARTICLE_NUMBER)                  ARTICLE_NUMBER,
           TRY_TO_NUMBER(v.GEN_ARTICLE_NUMBER)              GEN_ART_NUMBER,
           NVL(NULLIF(TRIM(v.ARTICLE_DESCRIPTION),''),
               NULLIF(TRIM(v.GEN_ARTICLE_DESC),''))         GEN_ART_DESC,
           NVL(NULLIF(TRIM(v.COLOR),''),'NA')               COL,
           NVL(NULLIF(TRIM(v.SIZE1),''),'NA')               SZ,
           TRIM(v.MAJOR_CATEGORY_DESCRIPTION)               MAJ_CAT,
           TRIM(v.SEGMENT)                                  SEG
    FROM V2RETAIL.SILVER.VW_PRODUCT_MASTER v
    WHERE TRY_TO_NUMBER(v.ARTICLE_NUMBER) IS NOT NULL
      AND TRY_TO_NUMBER(v.GEN_ARTICLE_NUMBER) IS NOT NULL
      AND TRIM(NVL(v.MAJOR_CATEGORY_DESCRIPTION,'')) <> ''
    QUALIFY ROW_NUMBER() OVER (PARTITION BY LTRIM(v.ARTICLE_NUMBER,'0')
                               ORDER BY v.ARTICLE_CREATION_DATE DESC NULLS LAST) = 1)
SELECT s.STOCK_DATE::date DT, s.WERKS, p.ARTICLE_NUMBER, p.GEN_ART_NUMBER, p.GEN_ART_DESC,
       p.COL, p.SZ, p.MAJ_CAT, p.SEG,
       TRIM(s.LGORT) KPI, 'ET_STOCK_DATA'::VARCHAR SRC,
       ROUND(SUM(s.LABST_DMBTR),4) STOCK_V,
       ROUND(SUM(s.LABST),4)       STOCK_Q,
       CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
FROM V2RETAIL.BRONZE.ET_STOCK_DATA s
JOIN p ON LTRIM(s.MATNR,'0') = p.mat_key
WHERE s.STOCK_DATE::date = {d}::DATE
  AND NVL(TRIM(s.LGORT),'') <> ''
  AND NOT EXISTS (SELECT 1 FROM V2RETAIL.BRONZE.DAY_WISE_ST_VAR_STOCK x
                   WHERE x.DT = {d}::DATE AND x.SRC = 'ET_STOCK_DATA')
GROUP BY 1,2,3,4,5,6,7,8,9,10,11
"""


def _et_gap_days(cur, a, b):
    """Days in [a, b] that ET_STOCK_DATA has but the day-wise fact holds no ET row for."""
    cur.execute(f"""
        WITH src AS (SELECT DISTINCT STOCK_DATE::DATE DT FROM V2RETAIL.BRONZE.ET_STOCK_DATA
                      WHERE STOCK_DATE::DATE BETWEEN {_q(a)}::DATE AND {_q(b)}::DATE),
             got AS (SELECT DISTINCT DT FROM {STOCK_TBL}
                      WHERE DT BETWEEN {_q(a)}::DATE AND {_q(b)}::DATE AND SRC = '{STOCK_TBL_ET_SRC}')
        SELECT src.DT::VARCHAR FROM src LEFT JOIN got ON got.DT = src.DT
         WHERE got.DT IS NULL ORDER BY 1""")
    return [r[0] for r in cur.fetchall()]


def _et_absent_days(cur, a, b):
    """Days in [a, b] with NO ET row in the day-wise fact, whether or not the source has them yet.
    Unlike _et_gap_days this does catch the late file: at load time ET_STOCK_DATA does not have the
    day either, so a source-vs-fact comparison would call it clean."""
    n = (datetime.fromisoformat(b) - datetime.fromisoformat(a)).days + 1
    cur.execute(f"""
        WITH spine AS (SELECT DATEADD(day, ROW_NUMBER() OVER (ORDER BY SEQ4()) - 1, {_q(a)}::DATE) DT
                         FROM TABLE(GENERATOR(ROWCOUNT => {int(n)}))),
             got AS (SELECT DISTINCT DT FROM {STOCK_TBL}
                      WHERE DT BETWEEN {_q(a)}::DATE AND {_q(b)}::DATE AND SRC = '{STOCK_TBL_ET_SRC}')
        SELECT spine.DT::VARCHAR FROM spine LEFT JOIN got ON got.DT = spine.DT
         WHERE got.DT IS NULL ORDER BY 1""")
    return [r[0] for r in cur.fetchall()]


def _et_heal(cur, a, b, run_group, trig, operator):
    """Insert the ET block for every gap day in [a, b]. Each day is its own MIS_LOAD_RUN row
    (kind 'stock_et' - KIND is VARCHAR(10)), so the run log shows that a day was repaired and when."""
    healed = {}
    for d in _et_gap_days(cur, a, b):
        run_id = mis_run_log.start(run_group=run_group, kind="stock_et", from_dt=d, to_dt=d,
                                   stores=[], triggered_by=trig, operator=operator)
        t0 = time.time()
        try:
            cur.execute(_ET_HEAL_SQL.format(d=_q(d)))
            n = int((cur.fetchone() or [0])[0] or 0)
        except Exception as e:
            mis_run_log.finish(run_id, status="FAILED", error=e, started_ms=(time.time() - t0) * 1000)
            raise
        # "0 deleted, N ... inserted" is the shape mis_run_log.parse_counts reads the counts from.
        msg = f"day-wise STOCK ET heal {d}: 0 deleted, {n} LGORT inserted (ET block only)"
        mis_run_log.finish(run_id, status="SUCCESS", proc_msg=msg, started_ms=(time.time() - t0) * 1000)
        logger.info("[sf-var-combined] %s", msg)
        healed[d] = n
    return healed

# A bounded range only. Stock is ~19.7M rows a DAY, so a wide backfill belongs in a scheduled job,
# not in a request a browser is waiting on. 31 days keeps the worst case near a month of stock.
MAX_LOAD_DAYS = 31


@router.post("/load")
def sf_var_combined_load(req: LoadReq):
    """Run SP_DAY_WISE_ST_VAR_SALE_LOAD then SP_DAY_WISE_ST_VAR_STOCK_LOAD.

    Both procs DELETE-then-INSERT over (date range, stores), so this is "delete what is there for
    that scope and re-insert it" - safe to re-run, and with stores named it leaves every other store
    alone. Sequential and synchronous: for one store-day it is seconds, and the day range is capped
    so a browser is never left waiting on a multi-hour backfill.

    Several stores go down as ONE comma-separated list, not one call each: the proc scans VBRP and
    ET_STOCK_DATA once per call, so looping per store would repeat the expensive part for every one.
    """
    a, b = _range(req.from_date, req.to_date)
    span = (datetime.fromisoformat(b) - datetime.fromisoformat(a)).days + 1
    if span > MAX_LOAD_DAYS:
        raise HTTPException(400,
            f"{span} days requested; this endpoint loads at most {MAX_LOAD_DAYS} at a time "
            f"(stock is ~19.7M rows a day). Load in chunks, or run the procs directly for a backfill.")
    # Both shapes collapse into one ordered, de-duplicated list. Order is kept rather than sorted
    # so the message back names the stores the way they were asked for.
    raw = list(req.stores or ([req.store] if req.store else []))
    stores, seen = [], set()
    for x in raw:
        c = str(x or "").strip().upper()
        if not c or c in seen:
            continue
        if not _STORE_RE.match(c):
            raise HTTPException(400, f"store must be a short store code like HA10 (got '{c}')")
        seen.add(c)
        stores.append(c)
    # The proc splits on commas, so a code containing one would be read as two stores. _STORE_RE
    # already forbids it; this is the belt to that braces.
    if any("," in c for c in stores):
        raise HTTPException(400, "store codes cannot contain a comma")
    st_lit = _q(",".join(stores)) if stores else "NULL"

    # Every call is logged to BRONZE.MIS_LOAD_RUN, one row per PROC. The two procs fail
    # independently - stock is ~60x the rows and is the one that times out - so logging the CLICK
    # would hide a half-success behind a single verdict, which is the state most worth seeing.
    # They share RUN_GROUP so the pair can still be shown as one action. See db-scripts/305.
    run_group = mis_run_log.new_id()
    trig = (req.triggered_by or "manual").strip().lower()
    if trig not in ("manual", "scheduled", "monitor"):
        trig = "manual"

    heal_days = max(0, min(int(req.heal_days or 0), MAX_HEAL_DAYS))
    out, healed, et_missing = {}, {}, []
    conn = _conn()
    try:
        cur = conn.cursor()
        # Earlier nights first: a day the previous runs loaded without its ET file is filled now
        # that the file has landed. Only all-store runs heal - a store-scoped load has no business
        # writing the whole estate.
        if heal_days and not stores:
            ha = (datetime.fromisoformat(a) - timedelta(days=heal_days)).date().isoformat()
            hb = (datetime.fromisoformat(a) - timedelta(days=1)).date().isoformat()
            healed = _et_heal(cur, ha, hb, run_group, trig, req.operator)
        for label, proc in (("sale", "SP_DAY_WISE_ST_VAR_SALE_LOAD"),
                            ("stock", "SP_DAY_WISE_ST_VAR_STOCK_LOAD")):
            run_id = mis_run_log.start(run_group=run_group, kind=label, from_dt=a, to_dt=b,
                                       stores=stores, triggered_by=trig, operator=req.operator)
            t0 = time.time()
            try:
                cur.execute(f"CALL V2RETAIL.BRONZE.{proc}({_q(a)}::DATE, {_q(b)}::DATE, {st_lit})")
                row = cur.fetchone()
                out[label] = row[0] if row else ""
            except Exception as e:
                # The log row is closed before the error leaves this function, so a load that blew
                # up on stock still leaves a FAILED row naming the range and the reason. Then it
                # re-raises unchanged: the caller's contract does not change because we log.
                mis_run_log.finish(run_id, status="FAILED", error=e,
                                   started_ms=(time.time() - t0) * 1000)
                raise
            mis_run_log.finish(run_id, status="SUCCESS", proc_msg=out[label],
                               started_ms=(time.time() - t0) * 1000)
            logger.info("[sf-var-combined] %s: %s", label, out[label])
        # The proc reports SUCCESS with "0 LGORT" when the ET file was late - name those days so
        # the caller can fail the run instead of showing it green. Store-scoped loads skip this:
        # a store with no ET reading that day is a fact, not a missing file.
        if not stores:
            et_missing = _et_absent_days(cur, a, b)
    finally:
        conn.close()
    _params_invalidate()
    return {"fromDate": a, "toDate": b, "stores": stores, "runGroup": run_group,
            "store": stores[0] if len(stores) == 1 else None, "messages": out,
            "etHealed": healed, "etMissing": et_missing}

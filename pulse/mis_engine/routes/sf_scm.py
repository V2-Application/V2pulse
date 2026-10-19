"""
Snowflake MIS — SCM REPORT (mounted at /sf-scm).

TEN SUPPLY-CHAIN VIEWS, ONE PAGE. The SCM team's manual workbooks — scanning per hours, HU
clearance, the PRD outstanding list, inbound stock in transit, the Wazirpur dispatch register, V01
storage-type stock and picking vs pending — were reproduced in SQL against live SAP data and
published as views in V2RETAIL.SILVER by db-scripts/387; db-scripts/391 added three more (DH24 daily bin
movement, GRC vs putaway, the REC-4 purchase register). This module is the read-only door onto
them: one grid, one export, one dump, and a report picker that switches which view is behind it.

ONE MODULE, NOT TEN. Every one of them is the same shape — a flat view, a tick-list of
low-cardinality dimensions, a search box, a totals strip over the additive measures, and for five
of them an anchor on a date. What differs between them is a column catalogue, so the difference
lives in REPORTS below as data and not as code. That is also what lets the Dump Scheduler carry
ONE view kind (`scm_report`) with the report as a field, rather than ten kinds that would each
need their own validate branch, their own runner and their own form.

THE VIEWS ARE THE CONTRACT. Not a line of their SQL is copied here: the catalogues name columns the
views publish, and if a view is re-cut with a column renamed this page says so by refusing that
column rather than by silently dumping a different number. db-scripts/387 owns the SQL, including
the caveats each view's header records; the ones a reader of the GRID has to know are repeated in
`note` and printed on the page.

THREE KINDS OF ANCHOR, and picking the right one per report is the whole of what this page gets
right that a generic table browser would not:

  run    RUN_DATE is a discrete daily EXTRACT, stored as YYYYMMDD text, and the report IS one run —
         the PRD outstanding list and the inbound in-transit position. Mixing two runs into one
         grid double-counts every HU that was still open on both days, so exactly one run is
         selected and the default is the LATEST one loaded. Every loaded run is in the picker.

  date   A real DATE column that a range is genuinely taken over — HU creation, the scan day, the
         gate-exit day. The default is a trailing window ending at the view's OWN MAX(date), never
         at today: BRONZE.ZWM_HU_BIN stopped loading on 18-Jun-2026, so a today-anchored default
         would open V_MSA_SCANNING_PER_HOURS empty for ever and read as a broken page instead of a
         stalled feed. Same reasoning as the bin report's latest-snapshot default.

  none   The view is the CURRENT state and has no anchor at all — V01 stock and picking vs pending.
         V01 carries GR_DATE, but that is when the stock was RECEIVED, not when the position was
         taken, so defaulting a range onto it would hide most of the warehouse. It stays an
         ordinary column.

Measured 22-Sep-2026: V_V01_STK_REPORT 936,895 rows | V_PICKING_VS_PENDING 320,729 |
V_HU_CLEARANCE_REPORT 188,271 | V_PRD_REPORT 144,876 (runs 20260821..20260921) |
V_MSA_SCANNING_PER_HOURS 63,837 (to 18-Jun-2026) | V_WAZIRPUR_DISPATCH_IN_TRANSIT 47,799 |
V_IN_TRANSIT_SIT1 26,575 (runs 20260601..20260921).
"""
import json
import logging
import os
import re
import tempfile
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from . import sf_var_combined as base
from .sf_gandola import _stream_csv

logger = logging.getLogger(__name__)
router = APIRouter()

SCHEMA = "V2RETAIL.SILVER"

# Excel's own ceiling, minus the header row. An xlsx export past it is refused with the row count in
# the message rather than truncated, exactly as the TNA export does it.
XLSX_MAX_ROWS = 1_048_575

# The grid pages 200 at a time; this ceiling is for a caller asking for one big slab. The CSV export
# streams and is not capped by it.
MAX_LIMIT = 100_000

# How many runs / distinct dates the picker offers. A run list is one row per day, so this is years.
MAX_ANCHOR_VALUES = 730

# ── THE REPORTS ──────────────────────────────────────────────────────────────────────────────────
#
# Per report:
#   label       what the picker shows
#   view        the SILVER view name (SCHEMA is prefixed; nothing else reaches the SQL)
#   tcode       the SAP transaction the manual workbook came out of, where there is one
#   note        the caveat a reader of THIS GRID has to know, printed on the page. Only the ones
#               that change how a number should be read - the full set is in db-scripts/387.
#   anchor      {"kind": "run"|"date"|"none", "col": ..., "days": <default window, date only>}
#   columns     every column, in DISPLAY order - which is the view's own order
#   defaultCols what the grid opens with; absent means all of them
#   filterCols  the tick-lists, AND the columns a dump may split on. Low-cardinality only: the
#               cardinalities behind each choice are in the comments, measured 22-Sep-2026 as
#               COUNT(DISTINCT col) - which EXCLUDES null. A tick-list on a column with blanks in it
#               therefore shows one MORE choice than the number quoted: the '(blank)' token, which
#               means IS NULL / '' rather than that literal text. Not a drift between the two.
#   searchCols  what the search box matches. Wrapped in TO_VARCHAR, so a number column is fine.
#   measures    the ADDITIVE columns, for the totals strip. Percentages and ageing are deliberately
#               absent - a summed average is a wrong number, and the strip has no way to say it is
#               averaging (the same rule the month roll-ups follow).
#   order       the default ORDER BY, as a list of "COL" or "COL DESC"
REPORTS = {
    "msa_scanning": {
        "label": "MSA Scanning Per Hours",
        "view": "V_MSA_SCANNING_PER_HOURS",
        "tcode": None,
        "note": ("BRONZE.ZWM_HU_BIN has loaded nothing since 18-Jun-2026, so this report is empty "
                 "for Jul-2026 onward until that feed restarts - the date box opens on the latest "
                 "day that IS loaded, not on today. HHT_USER is the HHT DEVICE, not the person: "
                 "ID / CARD / DEVICE NAME all identify the handset, and the picker's name lives "
                 "only in the manual roster. IDs 1-6 are bulk crate-scan accounts and are "
                 "excluded by the view."),
        "anchor": {"kind": "date", "col": "SCAN_DATE", "days": 7},
        "columns": ["SCAN_DATE", "ID", "CARD", "DEVICE_NAME", "FLOOR", "HOURLY_BENCHMARK",
                    "BENCH_MARK", "PICKING_START_TIME", "END_TIME",
                    "BGT_QTY_1000", "ACT_QTY_1000", "BGT_QTY_1100", "ACT_QTY_1100",
                    "BGT_QTY_1200", "ACT_QTY_1200", "BGT_QTY_1240", "ACT_QTY_1240",
                    "BGT_QTY_1400", "ACT_QTY_1400", "BGT_QTY_1500", "ACT_QTY_1500",
                    "BGT_QTY_1600", "ACT_QTY_1600", "BGT_QTY_1700", "ACT_QTY_1700",
                    "ACT_QTY_DAY", "ACH_PCT_1700"],
        # FLOOR 5, ID 331. CARD and DEVICE_NAME are the same handset under two names (331 each), so
        # they are searchable but not two more identical tick-lists.
        "filterCols": ["FLOOR", "ID"],
        "searchCols": ["ID", "CARD", "DEVICE_NAME"],
        "measures": ["ACT_QTY_1000", "ACT_QTY_1100", "ACT_QTY_1200", "ACT_QTY_1240",
                     "ACT_QTY_1400", "ACT_QTY_1500", "ACT_QTY_1600", "ACT_QTY_1700",
                     "ACT_QTY_DAY", "BENCH_MARK"],
        "order": ["SCAN_DATE DESC", "ACT_QTY_DAY DESC"],
    },
    "hu_clearance": {
        "label": "HU Clearance Report",
        "view": "V_HU_CLEARANCE_REPORT",
        "tcode": "YWM_PRDNEW",
        "note": ("DELIVERY is the HU's CURRENT delivery; the manual sheet froze it at run time, so "
                 "the two agree on about a quarter of rows. HU creation TIME, floor, HHT user and "
                 "the DC card are not in Snowflake and are omitted rather than shown empty. Scope "
                 "is LIKP.VSTEL = 'DH24' - MSA dispatches only, which is the filter that makes the "
                 "row set match the manual sheet."),
        "anchor": {"kind": "date", "col": "HU_CREATION_DT", "days": 30},
        "columns": ["HU_CREATION_DT", "REC_SITE", "ST_NAME", "STATUS_OF_STORES", "DELIVERY",
                    "EX_HU", "SAP_HU", "QTY", "TRADING_NON_TRADING", "HU_HANDOVER_DATE",
                    "HU_SCAN_TIME", "LEAD_TIME_FOR_HU", "STATUS", "HU_AGEING"],
        # REC_SITE 74, ST_NAME 42, STATUS_OF_STORES 4, STATUS 3. TRADING_NON_TRADING is the literal
        # 'TRADING' on every row - a tick-list with one choice narrows nothing, so it is a column
        # and not a filter.
        "filterCols": ["REC_SITE", "ST_NAME", "STATUS_OF_STORES", "STATUS"],
        "searchCols": ["REC_SITE", "ST_NAME", "SAP_HU", "EX_HU", "DELIVERY"],
        "measures": ["QTY"],
        "order": ["HU_CREATION_DT DESC", "SAP_HU"],
    },
    "prd_report": {
        "label": "PRD Report",
        "view": "V_PRD_REPORT",
        "tcode": "YWM_PRDNEW",
        "note": ("ONE RUN AT A TIME. The daily PRD run IS the outstanding-HU list, so two runs in "
                 "one grid would count every HU still open on both days twice. The report is "
                 "dumped the day AFTER the run, which is why AGING is measured against RUN_DATE + 1 "
                 "(REPORT_DATE). CLA BIN is only ~58% filled - the source carries no bin for HUs "
                 "still pending - and BATCH is in no Snowflake table at all."),
        "anchor": {"kind": "run", "col": "RUN_DATE"},
        "columns": ["RUN_DATE", "REPORT_DATE", "SHIP_POINT", "STORE_DESC", "STORE", "SEG", "SLOC",
                    "NEW_HU_NO", "EXT_HU_NO", "PALETTE", "CLA_BIN", "VIA", "HU_COUNT", "PKD_QTY",
                    "TRF_VAL", "CREATED_DATE", "STATUS", "DELIVERY_NO", "AGING", "HU_STATUS",
                    "REMARKS"],
        # STORE / STORE_DESC 368 each, VIA 21, SEG 4, STATUS 2, HU_STATUS 2, REMARKS 3. PALETTE
        # (1,492) and CLA_BIN (518) are searchable instead - a pallet is something you look up, not
        # something you slice by, and splitting a dump on either would order hundreds of files.
        "filterCols": ["STORE", "STORE_DESC", "SEG", "VIA", "STATUS", "HU_STATUS", "REMARKS"],
        "searchCols": ["STORE", "STORE_DESC", "NEW_HU_NO", "EXT_HU_NO", "DELIVERY_NO", "CLA_BIN",
                       "PALETTE"],
        "measures": ["HU_COUNT", "PKD_QTY", "TRF_VAL"],
        "order": ["STORE", "NEW_HU_NO"],
    },
    "in_transit": {
        "label": "Stock In Transit (inbound)",
        "view": "V_IN_TRANSIT_SIT1",
        "tcode": "YWM_SIT1",
        "note": ("ONE RUN AT A TIME: a run is the stock-in-transit position at that day's close, so "
                 "a HU on the run is still PENDING. A HU is DONE when it was on the previous run "
                 "and is absent from this one - which is a comparison of two runs, not a column "
                 "here. VBELN is the PROFORMA INVOICE and VBELV the OUTBOUND DELIVERY. "
                 "STORAGE_LOCATION does not tie to the manual sheet's values; gate entry/exit, "
                 "vehicle and driver are not in this table."),
        "anchor": {"kind": "run", "col": "RUN_DATE"},
        "columns": ["RUN_DATE", "GOODS_ISSUE_DATE", "SHIP_POINT", "SHIP_PNT_DESCR",
                    "SHIP_TO_PARTY", "SHIP_TO_PARTY_DESCR", "VIA_HUB", "STORAGE_LOCATION",
                    "HU_CREATION_DATE", "HANDLING_UNIT_NO", "EX_HU", "HU_COUNT",
                    "PROFORMA_INVOICE_DT", "PROFORMA_INVOICE_NO", "OUTBOUND_DEL_NO", "NO_OF_SETS",
                    "LOOSE_STOCK", "PRE_PACK_QTY", "NET_QTY", "TRANSFER_VALUE",
                    "GOODS_ISSUE_AGEING", "REMARKS"],
        # SHIP_POINT / SHIP_PNT_DESCR 20 each, STORAGE_LOCATION 5, REMARKS 3. SHIP_TO_PARTY and
        # VIA_HUB are 'DH24' on every row - the view filters KUNNR = 'DH24' - so they are columns.
        "filterCols": ["SHIP_POINT", "SHIP_PNT_DESCR", "STORAGE_LOCATION", "REMARKS"],
        "searchCols": ["HANDLING_UNIT_NO", "EX_HU", "PROFORMA_INVOICE_NO", "OUTBOUND_DEL_NO",
                       "SHIP_PNT_DESCR"],
        "measures": ["HU_COUNT", "NO_OF_SETS", "LOOSE_STOCK", "PRE_PACK_QTY", "NET_QTY",
                     "TRANSFER_VALUE"],
        "order": ["HANDLING_UNIT_NO"],
    },
    "wazirpur_dispatch": {
        "label": "Wazirpur Dispatch In-Transit",
        "view": "V_WAZIRPUR_DISPATCH_IN_TRANSIT",
        "tcode": "YWM_GENR",
        "note": ("The gate-EXIT register at DH24. The source's own column names are SWAPPED - "
                 "VLRNO is the LR number and LRNO the vehicle - and are un-swapped in the view, so "
                 "LR_NO and VEH_NO here mean what they say. TOT_PKT_HU undercounts: HUs packed "
                 "onto a shipment cannot be tied back to the delivery because VTTK/VTTP are not in "
                 "Snowflake. Mobile, vehicle size, ack no. and e-way-bill are not available."),
        "anchor": {"kind": "date", "col": "EXIT_DATE", "days": 30},
        "columns": ["SHIP_POINT", "ACT_DISP_DATE", "EXIT_DATE", "EXIT_TIME", "INTRANSIT_DAYS",
                    "TTL_LOAD_STR_OF_1_VEHICLE", "SHIP_TO_PARTY", "STR_CODE", "TOT_PKT_HU", "QTY",
                    "INV_VAL", "TAXABLE_AMT", "TAX_AMT", "GE_NO", "GE_DT", "GE_TIME", "STATUS",
                    "SAP_INV_NO", "GST_INV_OD_NO", "GATE_EXT_NO", "SEAL_NO", "LR_NO", "VEH_NO",
                    "TPT_NM", "DR_NM"],
        # STR_CODE 469, SHIP_TO_PARTY 468, VEH_NO 1,298, TPT_NM 108, STATUS 2. DR_NM (2,655) is
        # searchable only - a driver is who you look up on a row, not a slice of the register.
        "filterCols": ["STR_CODE", "SHIP_TO_PARTY", "STATUS", "TPT_NM", "VEH_NO"],
        "searchCols": ["GATE_EXT_NO", "SAP_INV_NO", "GST_INV_OD_NO", "VEH_NO", "LR_NO", "DR_NM",
                       "TPT_NM", "STR_CODE", "SHIP_TO_PARTY"],
        "measures": ["TOT_PKT_HU", "QTY", "INV_VAL", "TAXABLE_AMT", "TAX_AMT"],
        "order": ["EXIT_DATE DESC", "GATE_EXT_NO"],
    },
    "v01_stock": {
        "label": "V01 Stock Report",
        "view": "V_V01_STK_REPORT",
        "tcode": None,
        "note": ("Storage type V01 stock as it stands NOW - there is no snapshot date and no run to "
                 "choose. GR_DATE is when the quant was RECEIVED, not when the position was taken, "
                 "so it is an ordinary column and AGEING_DAYS is measured from it to today. MRP "
                 "comes from the article master, so STOCK_VALUE_MRP is blank wherever the master "
                 "has no price."),
        "anchor": {"kind": "none"},
        "columns": ["WHSE", "PLANT", "PLANT_NAME", "SLOC", "STORAGE_TYPE", "BIN", "SECTION",
                    "FLOOR", "ARTICLE", "ARTICLE_DESC", "SEG", "DIV", "MC_CODE", "MC_DESC", "MRP",
                    "BATCH", "UOM", "TOTAL_QTY", "AVAILABLE_QTY", "QTY_TO_PUTAWAY", "QTY_TO_PICK",
                    "QTY_IN_TRANSFER", "STOCK_VALUE_MRP", "GR_DATE", "AGEING_DAYS", "DELIVERY",
                    "QUANT"],
        "defaultCols": ["WHSE", "PLANT", "PLANT_NAME", "SLOC", "BIN", "SECTION", "FLOOR",
                        "ARTICLE", "ARTICLE_DESC", "SEG", "DIV", "MC_DESC", "MRP", "UOM",
                        "TOTAL_QTY", "AVAILABLE_QTY", "STOCK_VALUE_MRP", "GR_DATE",
                        "AGEING_DAYS"],
        # PLANT / PLANT_NAME 477 each, DIV 27, FLOOR 12, SECTION 8, SEG 7, WHSE 3, UOM 3, SLOC 2,
        # MC_DESC 1,095. MC_CODE (1,106) is the same category under its code, so one of the pair is
        # enough for a tick-list and both stay searchable. STORAGE_TYPE is 'V01' on every row - the
        # view's own WHERE - so it is a column.
        "filterCols": ["WHSE", "PLANT", "PLANT_NAME", "SLOC", "SECTION", "FLOOR", "SEG", "DIV",
                       "MC_DESC", "UOM"],
        "searchCols": ["BIN", "ARTICLE", "ARTICLE_DESC", "MC_CODE", "MC_DESC", "PLANT_NAME",
                       "DELIVERY", "BATCH", "QUANT"],
        "measures": ["TOTAL_QTY", "AVAILABLE_QTY", "QTY_TO_PUTAWAY", "QTY_TO_PICK",
                     "QTY_IN_TRANSFER", "STOCK_VALUE_MRP"],
        "order": ["PLANT", "BIN", "ARTICLE"],
    },
    "picking_vs_pending": {
        "label": "Picking vs Pending",
        "view": "V_PICKING_VS_PENDING",
        "tcode": None,
        "note": ("ACH_PCT IS UNRELIABLE TODAY. SILVER.V_ZWM_PICKING_PEN carries SCAN_QTY = 0 for "
                 "DH24 and DW01, so PICKED_QTY reads 0 and PENDING_QTY equals DO_QTY throughout - "
                 "the same scan-feed gap that stalls the scanning-per-hours report. DO_QTY and the "
                 "bins are sound; the achievement columns are not, until the scan data lands."),
        "anchor": {"kind": "none"},
        "columns": ["PLANT", "PLANT_NAME", "SOURCE_BIN", "SECTION", "FLOOR", "DEST_BIN", "ARTICLE",
                    "ARTICLE_DESC", "SEG", "DIV", "MC_CODE", "MC_DESC", "DO_QTY", "PICKED_QTY",
                    "PENDING_QTY", "ACH_PCT", "PENDING_PCT", "STATUS"],
        # SECTION 20, DIV 15, FLOOR 11, SEG 4, PLANT 3, STATUS 2, MC_DESC 559. DEST_BIN (8,618) is
        # searchable only - a tick-list of eight thousand bins is a scrolling wall and a dump split
        # on it would order eight thousand files.
        "filterCols": ["PLANT", "PLANT_NAME", "SECTION", "FLOOR", "SEG", "DIV", "MC_DESC",
                       "STATUS"],
        "searchCols": ["SOURCE_BIN", "DEST_BIN", "ARTICLE", "ARTICLE_DESC", "MC_CODE", "MC_DESC"],
        "measures": ["DO_QTY", "PICKED_QTY", "PENDING_QTY"],
        "order": ["PLANT", "SOURCE_BIN", "ARTICLE"],
    },

    # ── db-scripts/391: the second batch. These three publish the manual workbooks' OWN headers -
    # spaces, dots, mixed case ("Date", "SLoc") - so every name below is the view's exact spelling
    # and _canon() is what lets an upper-cased caller still find it. Measured 23-Sep-2026.
    "dh24_bin_mvmt": {
        "label": "DH24 Bin Movement (daily)",
        "view": "V_DH24_BIN_MVMT_DAILY",
        "tcode": "ZBIN_EMPTY",
        "note": ("ONE DAY AT A TIME. Every row is a bin's closing position for DATA DATE, so a "
                 "range of days adds each bin's stock once per day - the date box opens on the "
                 "latest day loaded and should be kept to one. BIN STATUS uses the real rule (FULL "
                 "when the bin holds any LQUA quant) only for YESTERDAY, the one day "
                 "SILVER.V_RDC_BIN_MVMT holds; older days fall back to MOC's own flag, which agreed "
                 "with the manual sheet on ~86% of bins. AGING is text as SAP sends it."),
        # ~217k bins a day, 20 days loaded (03..22-Sep-2026). Days=1: see the note.
        "anchor": {"kind": "date", "col": "DATA DATE", "days": 1},
        "columns": ["DATA DATE", "RDC CD", "RDC NM", "LOB", "SLoc", "FLR", "SEC", "ROW", "GND",
                    "TYP", "STORAGE BIN", "BIN STATUS", "BIN-STATUS (GRT/FRESH)",
                    "BIN-STATUS (IN-SAP)", "CRATE", "STK STATUS", "BIN-TYPE (WALL/MSA)",
                    "CRNT STOCK", "LAST MOV", "AGING", "OP-STK", "CL-STK", "PICKING PEND", "V05",
                    "V06", "TTL QTY", "TD-IN", "TD-OUT"],
        # FLR 9, SEC 85, ROW 43, GND 65, BIN STATUS 2, GRT/FRESH 3, IN-SAP 2, STK STATUS 3,
        # WALL/MSA 2. TYP is 'E01' on every row and RDC CD / LOB / SLoc are literals in the view, so
        # they are columns. STORAGE BIN and CRATE are one-per-row - searched, never split on.
        "filterCols": ["FLR", "SEC", "ROW", "GND", "BIN STATUS", "BIN-STATUS (GRT/FRESH)",
                       "BIN-STATUS (IN-SAP)", "STK STATUS", "BIN-TYPE (WALL/MSA)"],
        "searchCols": ["STORAGE BIN", "CRATE", "SEC", "ROW"],
        "measures": ["CRNT STOCK", "OP-STK", "CL-STK", "PICKING PEND", "V05", "V06", "TTL QTY",
                     "TD-IN", "TD-OUT"],
        "order": ["STORAGE BIN"],
    },
    "grc_vs_putaway": {
        "label": "GRC vs Putaway",
        "view": "V_GRC_VS_PUTAWAY",
        "tcode": "ZWM_BIN_GRC",
        "note": ("One row per vendor GRC at DH24 (STOs excluded). V01 PEND is today's LIVE V01 "
                 "balance, not the balance as of the GRC date: a PO's V01 bin is shared by all its "
                 "GRCs, so it is allocated newest-GRC-first and older receipts read as put away. "
                 "DIV is the dominant article segment on the doc (MENS / LADIES / KIDS / GM / NT). "
                 "GE NO., FLOOR PRINT, RECEIVING TL, PUTAWAY TYPE and REMARKS are manual-sheet "
                 "columns with no SAP source and are always blank."),
        # 134k GRCs, 25-Dec-2021 .. today; ~2,250 in a 30-day window.
        "anchor": {"kind": "date", "col": "GRC DATE", "days": 30},
        "columns": ["LOCATION", "GE NO.", "PO-NO", "GRC-NO", "FLOOR PRINT", "GRC DATE",
                    "VEND-NAME", "BILL NO.", "DIV", "NO.OF CRT", "RECEIVING TL", "GRC QTY", "VALLU",
                    "V01 PEND QTY AFTER PUTAWAY", "PENDING CRATE", "PUTAWAY DONE QTY", "STATUS",
                    "SAP GE DOC", "ACT RECEIVING DT", "ACT RECEIVING TIME", "PROCESS ST DATE",
                    "PROCESS END DATE", "GRC vs PUTAWAY AGEING", "PUTAWAY TYPE", "REMARKS"],
        "defaultCols": ["LOCATION", "PO-NO", "GRC-NO", "GRC DATE", "VEND-NAME", "BILL NO.", "DIV",
                        "NO.OF CRT", "GRC QTY", "VALLU", "V01 PEND QTY AFTER PUTAWAY",
                        "PENDING CRATE", "PUTAWAY DONE QTY", "STATUS", "SAP GE DOC",
                        "ACT RECEIVING DT", "ACT RECEIVING TIME", "PROCESS ST DATE",
                        "PROCESS END DATE", "GRC vs PUTAWAY AGEING"],
        # DIV 5, STATUS 3. VEND-NAME is 1,933 across the view - searched, not a tick-list.
        "filterCols": ["DIV", "STATUS"],
        "searchCols": ["PO-NO", "GRC-NO", "VEND-NAME", "BILL NO.", "SAP GE DOC"],
        "measures": ["NO.OF CRT", "GRC QTY", "VALLU", "V01 PEND QTY AFTER PUTAWAY",
                     "PENDING CRATE", "PUTAWAY DONE QTY"],
        "order": ["GRC DATE DESC", "GRC-NO"],
    },
    "rec4_purchase": {
        "label": "REC-4 Purchase Report",
        "view": "V_REC4_PURCHASE_REPORT",
        "tcode": "ZMB51",
        "note": ("One row per vendor GRC at DH24 (STOs excluded). PI AMT / TAX AMT are DERIVED - "
                 "GRC AMT plus 5% (J2/I1) or 18% (J4/I3) on the PO's tax code - not read off the "
                 "invoice. BILL AMT and SAP GE NO. come from the EARLIEST gate entry on the PO, so a "
                 "PO received in several GRCs repeats one bill on each; that is why BILL AMT and the "
                 "DIFF column are left out of the totals. SEG. / DIV come from the article-number "
                 "prefix, not the product master."),
        # Same 134k GRCs as GRC vs Putaway; ~2,250 in a 30-day window.
        "anchor": {"kind": "date", "col": "Date", "days": 30},
        "columns": ["Date", "PI LOC CD", "PI LOC", "PI NO", "SAP GE NO.", "SEG.", "DIV",
                    "VEND CD", "VEND NM", "VEND LOC", "GRC AMT", "PI AMT", "DISC AMT", "DISC %",
                    "NET PI AMT", "BILL AMT", "DIFF BETWEEN PI AMT & BILL AMT.", "VEND TIN",
                    "TAX CD", "TAX AMT", "TAX%", "PUR TYPE", "PI STATE", "VEND STATE CD",
                    "GRC DATE", "GRC NO", "GRC QTY", "MN-GE-NO", "BILL NO", "BILL DATE", "PO NO",
                    "PO DATE", "PO TYPE"],
        # SEG. 3, DIV 15, VEND LOC 104, TAX CD 4, PUR TYPE 2, PO TYPE 9, VEND STATE CD 19. VEND NM
        # (1,933) is searched instead.
        "filterCols": ["SEG.", "DIV", "VEND LOC", "VEND STATE CD", "TAX CD", "PUR TYPE",
                       "PO TYPE"],
        "searchCols": ["VEND CD", "VEND NM", "PI NO", "GRC NO", "PO NO", "BILL NO", "SAP GE NO.",
                       "VEND TIN"],
        "measures": ["GRC AMT", "PI AMT", "DISC AMT", "NET PI AMT", "TAX AMT", "GRC QTY"],
        "order": ["Date DESC", "GRC NO"],
    },
}

# A run date is YYYYMMDD TEXT in the source, and that is the shape the picker hands back.
_RUN_RE = re.compile(r"^\d{8}$")


def _spec(report):
    """The report definition, or a 400 naming the ones that exist.

    EVERY entry point starts here. It is the only place a caller's `report` is turned into a view
    name, which is what keeps an arbitrary string out of the FROM clause. User-added reports come
    through the same door: their view name was validated against _VIEW_RE when it was saved."""
    r = str(report or "").strip().lower()
    cat = _catalog()
    if r not in cat:
        raise HTTPException(400, f"unknown report '{report}' - choose from "
                                 f"{', '.join(sorted(cat))}")
    return r, cat[r]


# ── USER-ADDED REPORTS (db-scripts/392) ──────────────────────────────────────────────────────────
#
# The ten above are code. These are DATA: a row in BRONZE.SCM_REPORT_DEF per report, written by the
# page's "Add view" editor, which also CREATEs / ALTERs the view in SILVER. They are merged into
# the same catalogue, so the grid, the export, /values and the `scm_report` dump kind treat a
# user-added report exactly like a built-in one - nothing downstream knows the difference.
#
# THE COLUMN LIST IS READ LIVE from INFORMATION_SCHEMA, not trusted from the saved row: a view
# re-cut in Snowsight would otherwise leave the page offering columns that are gone. Tick-lists,
# search, measures and defaults are intersected with what the view really has.
DEF_TBL = "V2RETAIL.BRONZE.SCM_REPORT_DEF"
HIST_TBL = "V2RETAIL.BRONZE.SCM_REPORT_DEF_HIST"
_VIEW_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,120}$")
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
_CUSTOM_TTL_S = 60
_custom_cache = {"at": 0.0, "data": {}}

# Anchors a column of each type can carry. A run id is YYYYMMDD text (or a number of that shape).
_DATE_TYPES = {"DATE", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ", "TIMESTAMP"}
_NUM_TYPES = {"NUMBER", "FIXED", "FLOAT", "REAL", "DECIMAL", "INTEGER"}


def _builtin_views():
    return {d["view"].upper() for d in REPORTS.values()}


def _load_custom(force=False):
    """{slug: spec} for every live user-added report. Never raises: a missing table or a Snowflake
    hiccup degrades to "no custom reports" and the ten built-ins go on working."""
    if not force and (time.time() - _custom_cache["at"]) < _CUSTOM_TTL_S:
        return _custom_cache["data"]
    out = {}
    try:
        conn = base._conn()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT SLUG, VIEW_NAME, ORIGIN, TO_VARCHAR(DEF_JSON), UPDATED_BY, "
                        f"UPDATED_AT, CREATED_BY FROM {DEF_TBL} WHERE IS_DELETED = FALSE "
                        f"ORDER BY CREATED_AT")
            rows = cur.fetchall()
            live = {}
            if rows:
                names = ", ".join(base._q(r[1]) for r in rows)
                cur.execute("SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM "
                            "V2RETAIL.INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'SILVER' "
                            f"AND TABLE_NAME IN ({names}) ORDER BY TABLE_NAME, ORDINAL_POSITION")
                for t, c, ty in cur.fetchall():
                    live.setdefault(t, []).append((c, ty))
        finally:
            conn.close()
    except Exception as e:
        logger.warning("[sf-scm] user-added reports not loaded: %s", e)
        _custom_cache.update(at=time.time(), data={})
        return {}

    for slug, view, origin, raw, upd_by, upd_at, cr_by in rows:
        try:
            d = json.loads(raw) if raw else {}
        except ValueError:
            d = {}
        cols = [c for c, _ in live.get(view, [])]
        if not cols:
            # The view is gone (dropped in Snowsight). Listed, so it can be fixed or deleted from
            # the page, but with no columns every read refuses rather than erroring mid-SQL.
            logger.warning("[sf-scm] user-added report %s: view SILVER.%s has no columns", slug, view)
        keep = lambda xs: [x for x in (xs or []) if x in cols]
        anchor = d.get("anchor") or {"kind": "none"}
        if anchor.get("kind") in ("run", "date") and anchor.get("col") not in cols:
            anchor = {"kind": "none"}
        order = [o for o in (d.get("order") or [])
                 if o.rpartition(" ")[0] in cols or o in cols]
        out[slug] = {
            "label": d.get("label") or slug, "view": view, "tcode": d.get("tcode") or None,
            "note": d.get("note") or None, "anchor": anchor, "columns": cols,
            "types": {c: ty for c, ty in live.get(view, [])},
            "defaultCols": keep(d.get("defaultCols")) or None,
            "filterCols": keep(d.get("filterCols")), "searchCols": keep(d.get("searchCols")),
            "measures": keep(d.get("measures")), "order": order,
            "custom": True, "origin": origin, "createdBy": cr_by, "updatedBy": upd_by,
            "updatedAt": base._fmt_str(upd_at),
        }
    _custom_cache.update(at=time.time(), data=out)
    return out


def _catalog():
    """Built-ins first, in their own order, then the user-added ones. A slug clash cannot happen -
    the save refuses a built-in slug - but the built-in wins if one ever did."""
    cat = dict(REPORTS)
    for k, v in _load_custom().items():
        cat.setdefault(k, v)
    return cat


def _mask(sql):
    """The SQL with every string literal, quoted identifier, $$-string and comment blanked to spaces,
    SAME LENGTH, so a position in the mask is a position in the original. Every structural check
    below (one statement, starts with SELECT, where CREATE ... AS ends) runs on the mask, so a ';'
    or a 'CREATE' inside a literal or a comment can neither trip nor fool it."""
    out, i, n = [], 0, len(sql)
    while i < n:
        ch, nx = sql[i], sql[i + 1] if i + 1 < n else ""
        if ch == "-" and nx == "-" or ch == "/" and nx == "/":
            j = sql.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j
        elif ch == "/" and nx == "*":
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", sql[i:j])); i = j
        elif ch == "$" and nx == "$":
            j = sql.find("$$", i + 2)
            j = n if j < 0 else j + 2
            out.append("_" * (j - i)); i = j
        elif ch in ("'", '"'):
            j = i + 1
            while j < n:
                if sql[j] == "\\" and ch == "'":
                    j += 2; continue
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:
                        j += 2; continue
                    break
                j += 1
            j = min(n, j + 1)
            # Literals become '_' not ' ' so "SELECT'x'" still reads as two tokens-with-content.
            out.append("_" * (j - i)); i = j
        else:
            out.append(ch); i += 1
    return "".join(out)


_CREATE_RE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:SECURE\s+)?(?:RECURSIVE\s+)?VIEW\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>\S+)(?P<mid>.*?)\bAS\b", re.I | re.S)


def _parse_sql(text):
    """(view_name_or_None, body). Accepts a bare SELECT / WITH, or a whole pasted
    `CREATE [OR REPLACE] VIEW <name> ... AS <select>` - the SCM team's files arrive that way, with
    comments on top and a trailing ';'. Refuses anything that is not exactly one query."""
    sql = str(text or "")
    if not sql.strip():
        raise HTTPException(400, "SQL is empty")
    m = _mask(sql)
    # Trailing ';' (and whitespace/comments after it) is the end of the one statement.
    stripped = m.rstrip()
    while stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    end = len(stripped)
    if ";" in m[:end]:
        raise HTTPException(400, "Only ONE statement - there is a ';' in the middle of the SQL.")
    name = None
    start = len(m[:end]) - len(m[:end].lstrip())
    cm = _CREATE_RE.match(m[:end])
    if cm:
        raw = sql[cm.start("name"):cm.end("name")]
        name = raw.split(".")[-1].strip('"').upper()
        # Column lists / COMMENT between the name and AS are dropped - the view is re-created with
        # the body's own column names, which is what the catalogue reads anyway.
        start = cm.end()
    body = sql[start:end].strip()
    bm = m[start:end].strip()
    if not re.match(r"^(SELECT|WITH|\()", bm, re.I):
        raise HTTPException(400, "The SQL must be a query: SELECT ... or WITH ... (a pasted "
                                 "CREATE VIEW ... AS SELECT ... is fine too).")
    if re.search(r"\bSYSTEM\$", bm, re.I):
        raise HTTPException(400, "SYSTEM$ functions are not allowed in an SCM view.")
    return name, body


def _view_ident(name):
    v = str(name or "").strip().split(".")[-1].strip('"').upper()
    if not _VIEW_RE.match(v):
        raise HTTPException(400, f"View name '{name}' - use letters, digits and _ only, starting "
                                 "with a letter (e.g. V_MY_REPORT). It is always created in "
                                 f"{SCHEMA}.")
    return v


def _describe(cur, body=None, view=None, limit=0):
    """[(col, TYPE)] and the first `limit` rows of a query body or an existing view."""
    src = f"({body})" if body is not None else f"{SCHEMA}.{view}"
    cur.execute(f"SELECT * FROM {src} LIMIT {int(limit)}", timeout=240)
    from snowflake.connector.constants import FIELD_ID_TO_NAME
    cols = [(d[0], FIELD_ID_TO_NAME.get(d[1], "TEXT")) for d in cur.description]
    rows = cur.fetchall() if limit else []
    return cols, rows


def _object(cur, view):
    """(TABLE_TYPE, exists) of SILVER.<view> - 'VIEW', 'BASE TABLE' or None."""
    cur.execute("SELECT TABLE_TYPE FROM V2RETAIL.INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA = 'SILVER' AND TABLE_NAME = %s", (view,))
    r = cur.fetchone()
    return r[0] if r else None


def _custom_row(cur, slug):
    cur.execute(f"SELECT SLUG, VIEW_NAME, ORIGIN, TO_VARCHAR(DEF_JSON), SQL_BODY, IS_DELETED "
                f"FROM {DEF_TBL} WHERE SLUG = %s", (slug,))
    return cur.fetchone()


def _invalidate(slug=None):
    _custom_cache["at"] = 0.0
    if slug:
        _params_cache.pop(slug, None)


def _tbl(spec):
    return f"{SCHEMA}.{spec['view']}"


def _canon(spec, c):
    """The catalogue's own spelling of a caller's column name, or None.

    CASE-INSENSITIVE, because the callers are not: the Dump Scheduler upper-cases every column and
    level it saves. For the 387 views that was harmless - their names are upper-case already - but
    the 391 views publish the workbooks' own headers, and "Date", "SLoc" and "GRC vs PUTAWAY
    AGEING" upper-cased are names the view does not have. The quoted identifier has to be the
    catalogue's exact spelling."""
    s = str(c or "").strip()
    if s in spec["columns"]:
        return s
    u = s.upper()
    for k in spec["columns"]:
        if k.upper() == u:
            return k
    return None


def _col(spec, c):
    """A quoted, validated column reference. Anything outside the report's catalogue never reaches
    the SQL - which is the whole of the injection story for this module, since the FROM comes from
    REPORTS and every other identifier comes through here."""
    k = _canon(spec, c)
    if k is None:
        raise HTTPException(400, f"unknown column '{c}' on {spec['view']}")
    return f'"{k}"'


def _cols(spec, requested):
    """The projection: the caller's picks in CATALOGUE order, never request order.

    Fixed here so a file taken today lines up with one taken after somebody re-ticked the columns in
    a different sequence, and so the grid's headers cannot arrive shuffled."""
    if not requested:
        return list(spec.get("defaultCols") or spec["columns"])
    want = {str(c).strip().upper() for c in requested}
    picked = [c for c in spec["columns"] if c.upper() in want]
    if not picked:
        raise HTTPException(400, f"none of the requested columns exist on {spec['view']}")
    return picked


def _order_sql(spec, sort_col=None, sort_dir=None):
    """The ORDER BY. A caller's sort wins; otherwise the report's own default.

    NULLS LAST on every sort. These views LEFT JOIN their dimensions and leave columns blank on
    whole slices (CLA_BIN on 42% of PRD rows, MRP wherever the article master has no price), and
    Snowflake orders DESC with NULLS FIRST - so "biggest first" would open on a page of dashes and
    read as an empty grid rather than as a sort."""
    sc = _canon(spec, sort_col)
    if sc:
        direction = "DESC" if str(sort_dir or "").lower() == "desc" else "ASC"
        return f" ORDER BY {_col(spec, sc)} {direction} NULLS LAST"
    parts = []
    for o in spec["order"]:
        # Only a TRAILING ASC/DESC is a direction - the 391 views have spaces inside their names
        # ("GRC DATE DESC" is the column "GRC DATE", descending).
        name, _, tail = o.strip().rpartition(" ")
        if tail.upper() in ("ASC", "DESC") and name:
            direction = tail.upper()
        else:
            name, direction = o.strip(), "ASC"
        parts.append(f"{_col(spec, name)} {direction} NULLS LAST")
    return (" ORDER BY " + ", ".join(parts)) if parts else ""


# ── /params, cached ──────────────────────────────────────────────────────────────────────────────
# A params call is one scan of the view for the anchor values and one more for every tick-list, and
# the two biggest views take ten seconds between them. The lists change when SAP moves, not when a
# reader opens the page, so they are cached PER REPORT exactly as the product master's are.
# `refresh=1` forces a rebuild.
_PARAMS_TTL_S = 15 * 60
_params_cache = {}


def _anchor_values(cur, spec):
    """What the anchor picker offers, and what the page opens on.

    Returns the whole anchor block, including the RESOLVED default - the page does not compute a
    default from a range, because the rule (a trailing window ending at the view's own maximum, not
    at today) is the part that has to stay in one place. See the module header."""
    a = spec["anchor"]
    kind = a["kind"]
    if kind == "none":
        return {"kind": "none"}

    col = f'"{a["col"]}"'
    if kind == "run":
        cur.execute(f"SELECT DISTINCT {col} FROM {_tbl(spec)} WHERE {col} IS NOT NULL "
                    f"ORDER BY 1 DESC LIMIT {MAX_ANCHOR_VALUES}")
        runs = [base._fmt_str(r[0]) for r in cur.fetchall()]
        return {"kind": "run", "col": a["col"], "runs": runs,
                "latest": runs[0] if runs else None}

    cur.execute(f"SELECT MIN({col})::DATE, MAX({col})::DATE FROM {_tbl(spec)}")
    r = cur.fetchone() or (None, None)
    lo = base._fmt_str(r[0]) or None
    hi = base._fmt_str(r[1]) or None
    days = int(a.get("days") or 30)
    out = {"kind": "date", "col": a["col"], "days": days,
           "min": lo, "max": hi, "from": None, "to": None}
    if hi:
        # The window ENDS at the view's own maximum. Anchoring on today would open the scanning
        # report empty for ever - see the module header.
        cur.execute(f"SELECT DATEADD('day', {1 - days}, {base._q(hi)}::DATE)::DATE")
        start = base._fmt_str(cur.fetchone()[0])
        out["from"] = max(start, lo) if lo else start
        out["to"] = hi
    return out


@router.get("/reports")
def scm_reports():
    """The catalogue, with no Snowflake call at all.

    Its own endpoint so the page's report picker, the Dump Scheduler's form and the dump's own
    validation all read ONE list. Nothing here is per-report state, so it is cheap enough to call on
    every page load and needs no cache. The user-added reports ride a 60-second cache of their own
    (_load_custom), so this stays cheap with them in it."""
    return {"reports": [
        {"slug": s, "label": d["label"], "view": d["view"], "table": f"{SCHEMA}.{d['view']}",
         "tcode": d.get("tcode"), "note": d.get("note"),
         "anchor": d["anchor"]["kind"], "anchorCol": d["anchor"].get("col"),
         "anchorDays": d["anchor"].get("days"),
         "columns": d["columns"],
         "defaultCols": d.get("defaultCols") or d["columns"],
         "filterCols": d["filterCols"], "searchCols": d["searchCols"],
         "measures": d["measures"],
         "custom": bool(d.get("custom")), "origin": d.get("origin"),
         "updatedBy": d.get("updatedBy"), "updatedAt": d.get("updatedAt")}
        for s, d in _catalog().items()]}


@router.get("/params")
def scm_params(report: str, refresh: int = 0):
    """The anchor values, the tick-list options and the row count - all read from the view."""
    slug, spec = _spec(report)
    hit = _params_cache.get(slug)
    if not refresh and hit and (time.time() - hit["at"]) < _PARAMS_TTL_S:
        return {**hit["data"], "cached": True}

    conn = base._conn()
    try:
        cur = conn.cursor()
        anchor = _anchor_values(cur, spec)

        # ONE PASS for every option list. Ten separate DISTINCTs over V01's 937k rows is ten scans
        # to fill ten dropdowns - the same reason the bin report aggregates its lists together.
        opts = {}
        if spec["filterCols"]:
            sel = ", ".join(
                "ARRAY_AGG(DISTINCT NVL(NULLIF(TRIM(TO_VARCHAR({c})),''),'(blank)')) \"{n}\""
                .format(c=_col(spec, c), n=c)
                for c in spec["filterCols"])
            cur.execute(f"SELECT {sel} FROM {_tbl(spec)}")
            names = [d[0] for d in cur.description]
            for n, v in zip(names, cur.fetchone() or []):
                vals = [x for x in (json.loads(v) if v else []) if x is not None]
                opts[n] = sorted(vals)

        cur.execute(f"SELECT COUNT(*) FROM {_tbl(spec)}")
        rows = int(cur.fetchone()[0] or 0)
    finally:
        conn.close()

    out = {"report": slug, "label": spec["label"], "table": _tbl(spec),
           "tcode": spec.get("tcode"), "note": spec.get("note"),
           "anchor": anchor, "options": opts,
           "columns": spec["columns"],
           "defaultCols": spec.get("defaultCols") or spec["columns"],
           "filterCols": spec["filterCols"], "searchCols": spec["searchCols"],
           "measures": spec["measures"], "stats": {"rows": rows}}
    _params_cache[slug] = {"at": time.time(), "data": out}
    return {**out, "cached": False}


class ScmReq(BaseModel):
    report: str
    run_date: Optional[str] = None       # anchor 'run': absent = the latest run loaded
    from_date: Optional[str] = None      # anchor 'date': absent = the resolved default window
    to_date: Optional[str] = None
    all_dates: bool = False              # anchor 'date': the whole view, no window at all
    col_filters: Optional[dict] = None   # {COL: [values]}
    search: Optional[str] = None
    columns: Optional[list] = None
    page: int = 1
    limit: int = 200
    sort_col: Optional[str] = None
    sort_dir: Optional[str] = "asc"


def _where(cur, spec, req):
    """The WHERE for one request, plus the anchor it resolved to.

    The anchor is resolved HERE and not by the caller, so /result, /export and /values can never
    disagree about which run or which window a dump was taken over - and a schedule that stored no
    dates lands on the CURRENT run every firing rather than on the one that was current when it was
    created."""
    a = spec["anchor"]
    kind = a["kind"]
    conds = []
    anchor = {"kind": kind}

    if kind == "run":
        run = str(getattr(req, "run_date", "") or "").strip()
        if not run:
            cur.execute(f'SELECT MAX("{a["col"]}") FROM {_tbl(spec)}')
            r = cur.fetchone()
            run = base._fmt_str(r[0]) if r and r[0] else ""
        if not run:
            raise HTTPException(404, f"{spec['label']}: no runs have been loaded")
        if not _RUN_RE.match(run):
            raise HTTPException(400, "runDate must be a run in YYYYMMDD form")
        conds.append(f'"{a["col"]}" = {base._q(run)}')
        anchor = {"kind": "run", "col": a["col"], "runDate": run}

    elif kind == "date":
        if bool(getattr(req, "all_dates", False)):
            anchor = {"kind": "date", "col": a["col"], "from": None, "to": None, "all": True}
        else:
            frm = str(getattr(req, "from_date", "") or "").strip()
            to = str(getattr(req, "to_date", "") or "").strip()
            if not frm or not to:
                # The SAME default the page opened on, recomputed rather than trusted from the
                # request. See the docstring.
                d = _anchor_values(cur, spec)
                frm = frm or d.get("from") or ""
                to = to or d.get("to") or ""
            if not frm or not to:
                raise HTTPException(404, f"{spec['label']}: no dates have been loaded")
            frm, to = base._range(frm, to)
            conds.append(f'"{a["col"]}" BETWEEN {base._q(frm)}::DATE AND {base._q(to)}::DATE')
            anchor = {"kind": "date", "col": a["col"], "from": frm, "to": to}

    for c, vals in (req.col_filters or {}).items():
        cu = _canon(spec, c)
        if cu not in spec["filterCols"]:
            continue
        clean = [str(v) for v in (vals or []) if str(v).strip() != ""]
        if not clean:
            continue
        # '(blank)' is a real choice in the tick-list, so it has to mean IS NULL / '' rather than
        # match the literal text nobody has. Same token /params and /values publish.
        lits = [v for v in clean if v != "(blank)"]
        parts = []
        if lits:
            parts.append(f"TRIM(TO_VARCHAR({_col(spec, cu)})) IN "
                         f"({', '.join(base._q(v) for v in lits)})")
        if "(blank)" in clean:
            parts.append(f"NVL(TRIM(TO_VARCHAR({_col(spec, cu)})),'') = ''")
        conds.append("(" + " OR ".join(parts) + ")")

    s = str(req.search or "").strip()
    if s:
        # TO_VARCHAR on every search column, so a number (HU, invoice, article) and a date match the
        # same way text does and no per-column type table has to be kept in step with the views.
        like = base._q("%" + s + "%")
        conds.append("(" + " OR ".join(
            f"UPPER(TO_VARCHAR({_col(spec, c)})) LIKE UPPER({like})"
            for c in spec["searchCols"]) + ")")

    return ((" WHERE " + " AND ".join(conds)) if conds else ""), anchor


@router.post("/result")
def scm_result(req: ScmReq):
    slug, spec = _spec(req.report)
    page = max(1, int(req.page or 1))
    limit = max(1, min(int(req.limit or 200), MAX_LIMIT))
    cols = _cols(spec, req.columns)
    conn = base._conn()
    try:
        cur = conn.cursor()
        where, anchor = _where(cur, spec, req)

        cur.execute(f"SELECT COUNT(*) FROM {_tbl(spec)}{where}")
        total = int(cur.fetchone()[0] or 0)

        # The totals strip is over the WHOLE filtered selection, not the page: a page total of 200
        # quants out of 936,895 is a number nobody wants. Only the measures the report declares
        # additive are in it, and only the ones actually on screen.
        totals = {}
        shown_meas = [m for m in spec["measures"] if m in cols]
        if shown_meas:
            tsel = ", ".join(f'ROUND(SUM({_col(spec, m)}),2) "{m}"' for m in shown_meas)
            cur.execute(f"SELECT {tsel} FROM {_tbl(spec)}{where}")
            tnames = [d[0] for d in cur.description]
            totals = {n: base._f(v) for n, v in zip(tnames, cur.fetchone() or [])}

        cur.execute(f"SELECT {', '.join(_col(spec, c) for c in cols)} FROM {_tbl(spec)}{where}"
                    f"{_order_sql(spec, req.sort_col, req.sort_dir)} "
                    f"LIMIT {limit} OFFSET {(page - 1) * limit}")
        names = [c[0] for c in cur.description]
        rows = [[base._fmt_str(v) for v in r] for r in cur.fetchall()]
    finally:
        conn.close()
    return {"report": slug, "label": spec["label"], "table": _tbl(spec), "anchor": anchor,
            "columns": names, "rows": rows, "total": total, "page": page, "limit": limit,
            "shown": len(rows), "totals": totals, "measures": spec["measures"]}


def _stamp(anchor):
    """The anchor as a filename fragment - a run, a window, or nothing.

    The file says which extract it IS, which is the point: two dumps taken between two loads are
    the same numbers, and naming them by the moment they ran would file them twice under two names.
    """
    if anchor.get("kind") == "run":
        return str(anchor.get("runDate") or "")
    if anchor.get("kind") == "date" and anchor.get("from"):
        a, b = anchor["from"], anchor["to"]
        return a.replace("-", "") if a == b else f"{a.replace('-', '')}_{b.replace('-', '')}"
    return ""


@router.post("/export")
def scm_export(req: ScmReq, fmt: str = "csv"):
    """The WHOLE filtered selection, streamed - not the page.

    RAW COLUMN NAMES in the header, the same rule every export in this service follows: a header in
    a file is the column you can query against, and a prettified one is how a file and a screen come
    to disagree about what a column is called."""
    if fmt not in ("csv", "xlsx"):
        raise HTTPException(400, "fmt must be csv or xlsx")
    slug, spec = _spec(req.report)
    cols = _cols(spec, req.columns)
    conn = base._conn()
    cur = conn.cursor()
    where, anchor = _where(cur, spec, req)
    stamp = _stamp(anchor)
    stem = f"{spec['view']}{f'_{stamp}' if stamp else ''}"
    sql = (f"SELECT {', '.join(_col(spec, c) for c in cols)} FROM {_tbl(spec)}{where}"
           f"{_order_sql(spec, req.sort_col, req.sort_dir)}")

    if fmt == "csv":
        # The connection stays open behind the generator, the same shape every streamed CSV in this
        # service has. Closing it here would end the cursor mid-stream.
        cur.execute(sql)
        return _stream_csv(cur, [c[0] for c in cur.description], f"{stem}.csv")

    import xlsxwriter
    try:
        cur.execute(f"SELECT COUNT(*) FROM {_tbl(spec)}{where}")
        total = int(cur.fetchone()[0] or 0)
        if total > XLSX_MAX_ROWS:
            raise HTTPException(
                400, f"{total:,} rows is past Excel's own limit of {XLSX_MAX_ROWS:,} - narrow the "
                     f"filters, or take it as CSV.")
        cur.execute(sql)
        head = [c[0] for c in cur.description]
        fd, path = tempfile.mkstemp(suffix=".xlsx", prefix=f"scm_{slug}_")
        os.close(fd)
        wb = xlsxwriter.Workbook(path, {"constant_memory": True, "use_zip64": True})
        ws = wb.add_worksheet(spec["view"][:31])
        head_fmt = wb.add_format({"bold": True, "font_size": 9, "bg_color": "#DDEBF7"})
        # The header BEFORE any data row: constant memory flushes a row to disk the moment the next
        # one is touched, so a write back to an earlier row is silently DROPPED.
        for j, v in enumerate(head):
            ws.write(0, j, v, head_fmt)
        ws.set_column(0, max(0, len(head) - 1), 16)
        ws.freeze_panes(1, 0)
        r = 1
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
        headers={"Content-Disposition": f'attachment; filename="{stem}.xlsx"'})


class ScmValuesReq(ScmReq):
    split_cols: Optional[list] = None


@router.post("/values")
def scm_values(req: ScmValuesReq):
    """The distinct COMBINATIONS of the split columns that actually OCCUR, with their row counts.

    A folder dump asks this before it writes anything, so it plans one file per combination that
    EXISTS rather than crossing every value of every level - two levels crossed blindly would order
    hundreds of files that are nearly all empty.

    SPLIT LEVELS ARE THE FILTER COLUMNS, for the same single reason the bin report gives: a slice is
    taken by re-running the report with that value as a col_filter, so a level has to be something
    _where() can filter on. It is also what keeps a split off DEST_BIN (8,618 values) and PALETTE
    (1,492)."""
    slug, spec = _spec(req.report)
    cols = []
    for raw in (req.split_cols or []):
        c = _canon(spec, raw)
        if c not in spec["filterCols"]:
            raise HTTPException(400, f"'{raw}' cannot be split on for {spec['label']} - choose "
                                     f"from {', '.join(spec['filterCols'])}")
        cols.append(c)
    if not cols:
        return {"report": slug, "combos": [], "counts": [], "total": 0}

    conn = base._conn()
    try:
        cur = conn.cursor()
        where, anchor = _where(cur, spec, req)
        # '(blank)' here and in the tick-list are the SAME token on purpose: the combination goes
        # straight back as a col_filter, where it means IS NULL / '' rather than that literal text.
        proj = ", ".join(f"NVL(NULLIF(TRIM(TO_VARCHAR({_col(spec, c)})),''),'(blank)')"
                         for c in cols)
        grp = ", ".join(str(i + 1) for i in range(len(cols)))
        # COUNT(*) alongside the combination, in the SAME group-by: the caller needs the true size of
        # every slice BEFORE it starts building, so asking per slice would be one query each.
        cur.execute(f"SELECT {proj}, COUNT(*) FROM {_tbl(spec)}{where} "
                    f"GROUP BY {grp} ORDER BY {grp}")
        rows = cur.fetchall()
        combos = [[base._fmt_str(v) for v in r[:-1]] for r in rows]
        counts = [int(r[-1] or 0) for r in rows]
    finally:
        conn.close()
    return {"report": slug, "anchor": anchor, "columns": cols, "combos": combos,
            "counts": counts, "total": sum(counts)}


# ── THE "ADD VIEW" EDITOR ────────────────────────────────────────────────────────────────────────
#
# preview  -> SQL (or an existing view's name) in, its columns + types + a sample + cardinality
#             hints out. Runs the query READ-ONLY; nothing is created.
# save     -> validate, DESCRIBE, check the chosen config against the real columns, and only then
#             CREATE OR REPLACE VIEW ... COPY GRANTS. The definition row and a history row follow.
# get      -> one report for editing, with the view's LIVE DDL (GET_DDL) as the SQL - so a view
#             altered in Snowsight is edited as it stands, not as it was last saved here.
# delete   -> soft-delete the row; DROP the view only when this editor created it.
#
# GUARDS, in the order they bite:
#   * the SQL must reduce to ONE query body (_parse_sql);
#   * the ten built-in views (387, 391) can never be the target - they belong to their db-scripts;
#   * an existing SILVER object this table does not own is replaced only with replace_existing, and
#     a TABLE is never replaced at all;
#   * every chosen column must be one the query really returns.

class ScmPreviewReq(BaseModel):
    sql: Optional[str] = None
    view: Optional[str] = None           # register-an-existing-view mode, when sql is empty


class ScmCustomReq(BaseModel):
    slug: Optional[str] = None           # absent = a new report
    label: str
    view: Optional[str] = None           # absent = the name inside a pasted CREATE VIEW
    tcode: Optional[str] = None
    note: Optional[str] = None
    sql: Optional[str] = None            # absent/empty = register the existing view as-is
    anchor: Optional[dict] = None        # {kind, col, days}
    filterCols: Optional[list] = None
    searchCols: Optional[list] = None
    measures: Optional[list] = None
    defaultCols: Optional[list] = None
    order: Optional[list] = None         # ["COL", "COL DESC"]
    replace_existing: bool = False
    user: Optional[str] = None


def _who(u):
    return (str(u or "").strip() or "unknown")[:120]


# Name hints for the SUGGESTIONS only - never enforced. A column called *_PCT or *AGEING summed is a
# wrong number, and a *_NO / *_CD / ID is a key, not a quantity.
_NOT_MEASURE = re.compile(r"(PCT|%|RATE|RATIO|AGE|AGING|AGEING|DAYS|_NO\b|NO\.|\bNO\b|_CD\b|\bCD\b|"
                          r"CODE|\bID\b|_ID\b|YEAR|MONTH|WEEK|MRP|PRICE|ASP)", re.I)


@router.post("/custom/preview")
def scm_custom_preview(req: ScmPreviewReq):
    sql = str(req.sql or "").strip()
    conn = base._conn()
    try:
        cur = conn.cursor()
        if sql:
            name, body = _parse_sql(sql)
            try:
                cols, rows = _describe(cur, body=body, limit=50)
            except Exception as e:
                raise HTTPException(400, f"Snowflake refused the query: {str(e)[:600]}")
            src = f"({body})"
        else:
            name = _view_ident(req.view)
            if not _object(cur, name):
                raise HTTPException(404, f"{SCHEMA}.{name} does not exist - paste its SQL instead.")
            cols, rows = _describe(cur, view=name, limit=50)
            src = f"{SCHEMA}.{name}"
        exists = _object(cur, name) if name else None

        # Cardinality HINTS over the first 200k rows - enough to tell a 4-value SEG from a
        # million-value HU number, cheap enough to run on every Validate. Capped, so it says "+".
        texty = [c for c, t in cols if t in ("TEXT", "DATE", "FIXED", "BOOLEAN")]
        card, sampled = {}, 0
        if texty:
            sel = ", ".join('APPROX_COUNT_DISTINCT("{}")'.format(c.replace('"', '""'))
                            for c in texty)
            try:
                cur.execute(f"SELECT COUNT(*), {sel} FROM (SELECT * FROM {src} LIMIT 200000)",
                            timeout=240)
                r = cur.fetchone()
                sampled = int(r[0] or 0)
                card = {c: int(v or 0) for c, v in zip(texty, r[1:])}
            except Exception as e:
                logger.info("[sf-scm] preview cardinality skipped: %s", e)
    finally:
        conn.close()

    date_cols = [c for c, t in cols if t in _DATE_TYPES]
    return {
        "view": name, "exists": exists,
        "builtin": bool(name and name in _builtin_views()),
        "columns": [{"name": c, "type": t, "distinct": card.get(c)} for c, t in cols],
        "rows": [[base._fmt_str(v) for v in r] for r in rows],
        "sampled": sampled, "capped": sampled >= 200000,
        "suggest": {
            "anchor": ({"kind": "date", "col": date_cols[0], "days": 30} if date_cols
                       else {"kind": "none"}),
            "filterCols": [c for c, t in cols if t == "TEXT" and 1 < card.get(c, 0) <= 150],
            "measures": [c for c, t in cols if t in ("FIXED", "REAL")
                         and not _NOT_MEASURE.search(c) and card.get(c, 999) > 2],
            "searchCols": [c for c, t in cols
                           if t in ("TEXT", "FIXED") and card.get(c, 0) > 150][:8],
        },
    }


def _clean_list(xs, cols, what):
    """The caller's names in the query's own spelling, or a 400 naming every one it does not
    return. Case-insensitive, like _canon, and de-duplicated."""
    out, bad = [], []
    for x in (xs or []):
        k = next((c for c in cols if c.upper() == str(x).strip().upper()), None)
        (out if k else bad).append(k or str(x))
    if bad:
        raise HTTPException(400, f"{what}: {', '.join(bad)} - not returned by the query")
    return list(dict.fromkeys(out))


@router.post("/custom")
def scm_custom_save(req: ScmCustomReq):
    label = str(req.label or "").strip()[:120]
    if not label:
        raise HTTPException(400, "Give the report a name")
    who = _who(req.user)
    sql = str(req.sql or "").strip()

    conn = base._conn()
    try:
        cur = conn.cursor()
        # ── which report, and which view ──
        existing = None
        slug = str(req.slug or "").strip().lower()
        if slug:
            if slug in REPORTS:
                raise HTTPException(400, f"'{slug}' is a built-in report - its view belongs to its "
                                         "db-script and is not edited here.")
            existing = _custom_row(cur, slug)
            if not existing or existing[5]:
                raise HTTPException(404, f"No user-added report '{slug}'")

        pasted_name, body = _parse_sql(sql) if sql else (None, None)
        view = _view_ident(req.view or pasted_name or (existing[1] if existing else ""))
        if view in _builtin_views():
            raise HTTPException(400, f"{view} is one of the built-in SCM views (db-scripts/387, "
                                     "391) - pick another name.")
        cur.execute(f"SELECT SLUG FROM {DEF_TBL} WHERE VIEW_NAME = %s AND IS_DELETED = FALSE "
                    f"AND SLUG <> %s", (view, slug or ""))
        clash = cur.fetchone()
        if clash:
            raise HTTPException(400, f"{view} is already the view behind report '{clash[0]}'.")

        kind = _object(cur, view)
        ours = bool(existing and existing[1] == view)
        if kind == "BASE TABLE":
            raise HTTPException(400, f"{SCHEMA}.{view} is a TABLE - an SCM view is never allowed "
                                     "to replace a table. Pick another name.")
        if not sql and not kind:
            raise HTTPException(400, f"{SCHEMA}.{view} does not exist - paste the SQL to create it.")
        if sql and kind and not ours and not req.replace_existing:
            raise HTTPException(409, f"{SCHEMA}.{view} already exists and was not made by this "
                                     "editor. Tick 'replace the existing view' to overwrite it, or "
                                     "leave the SQL empty to add it as it is.")

        # ── describe BEFORE any DDL, and check the config against what it really returns ──
        try:
            desc, _ = _describe(cur, body=body) if sql else _describe(cur, view=view)
        except Exception as e:
            raise HTTPException(400, f"Snowflake refused the query: {str(e)[:600]}")
        cols = [c for c, _ in desc]
        types = dict(desc)
        if len(set(cols)) != len(cols):
            dup = sorted({c for c in cols if cols.count(c) > 1})
            raise HTTPException(400, f"The query returns duplicate column names: "
                                     f"{', '.join(dup)} - alias them apart.")
        if any('"' in c for c in cols):
            raise HTTPException(400, "A column name contains a double quote - alias it.")

        a = dict(req.anchor or {"kind": "none"})
        ak = str(a.get("kind") or "none").lower()
        if ak not in ("none", "date", "run"):
            raise HTTPException(400, "anchor.kind must be none, date or run")
        anchor = {"kind": "none"}
        if ak != "none":
            if not a.get("col"):
                raise HTTPException(400, f"A '{ak}' anchor needs a column")
            ac = _clean_list([a.get("col")], cols, "Anchor column")[0]
            if ak == "date" and types.get(ac) not in _DATE_TYPES:
                raise HTTPException(400, f"{ac} is {types.get(ac)}, not a date - a date range "
                                         "needs a DATE / TIMESTAMP column.")
            if ak == "run" and types.get(ac) not in ("TEXT", "FIXED"):
                raise HTTPException(400, f"{ac} is {types.get(ac)} - a run column holds YYYYMMDD "
                                         "text or numbers.")
            anchor = {"kind": ak, "col": ac}
            if ak == "date":
                anchor["days"] = max(1, min(3650, int(a.get("days") or 30)))

        order = []
        for o in (req.order or []):
            nm, _, tail = str(o).strip().rpartition(" ")
            if tail.upper() not in ("ASC", "DESC") or not nm:
                nm, tail = str(o).strip(), "ASC"
            order.append(_clean_list([nm], cols, "Sort")[0]
                         + (" DESC" if tail.upper() == "DESC" else ""))

        defn = {
            "label": label, "tcode": (str(req.tcode or "").strip()[:40] or None),
            "note": (str(req.note or "").strip()[:2000] or None), "anchor": anchor,
            "filterCols": _clean_list(req.filterCols, cols, "Tick-lists"),
            "searchCols": _clean_list(req.searchCols, cols, "Search columns"),
            "measures": _clean_list(req.measures, cols, "Totals"),
            "defaultCols": _clean_list(req.defaultCols, cols, "Default columns"),
            "order": order,
        }

        # ── the DDL. Only now, with everything above passed ──
        action = "register"
        origin = existing[2] if existing else ("adopted" if kind else "created")
        if sql:
            cur.execute(f"CREATE OR REPLACE VIEW {SCHEMA}.{view} COPY GRANTS AS\n{body}")
            cur.execute(f"COMMENT ON VIEW {SCHEMA}.{view} IS %s",
                        (f"SCM Report user-added view '{label}', edited from MIS > SCM Report by "
                         f"{who}. Definition in {DEF_TBL}, history in {HIST_TBL}.",))
            action = "alter" if kind else "create"
            if existing and existing[1] != view:
                # A rename onto a fresh name: this editor made the new view.
                origin = "created" if not kind else "adopted"
        # A rename: the OLD view is dropped only if this editor made it, and only after the new one
        # stands - a failed create above leaves the old one untouched.
        if existing and existing[1] != view and existing[2] == "created":
            try:
                cur.execute(f"DROP VIEW IF EXISTS {SCHEMA}.{existing[1]}")
            except Exception as e:
                logger.warning("[sf-scm] rename left %s behind: %s", existing[1], e)

        if not slug:
            stem = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:50] or "report"
            if not stem[0].isalpha():
                stem = "r_" + stem
            cur.execute(f"SELECT SLUG FROM {DEF_TBL}")
            taken = {r[0] for r in cur.fetchall()} | set(REPORTS)
            slug, n = stem, 2
            while slug in taken:
                slug, n = f"{stem}_{n}", n + 1

        dj = json.dumps(defn)
        cur.execute(
            f"""MERGE INTO {DEF_TBL} t
                USING (SELECT %s SLUG, %s VIEW_NAME, %s ORIGIN, PARSE_JSON(%s) DEF_JSON,
                              %s SQL_BODY, %s WHO) s
                   ON t.SLUG = s.SLUG
                 WHEN MATCHED THEN UPDATE SET VIEW_NAME = s.VIEW_NAME, ORIGIN = s.ORIGIN,
                      DEF_JSON = s.DEF_JSON, SQL_BODY = COALESCE(s.SQL_BODY, t.SQL_BODY),
                      IS_DELETED = FALSE, UPDATED_BY = s.WHO, UPDATED_AT = CURRENT_TIMESTAMP()
                 WHEN NOT MATCHED THEN INSERT (SLUG, VIEW_NAME, ORIGIN, DEF_JSON, SQL_BODY,
                      CREATED_BY, UPDATED_BY)
                      VALUES (s.SLUG, s.VIEW_NAME, s.ORIGIN, s.DEF_JSON, s.SQL_BODY, s.WHO, s.WHO)""",
            (slug, view, origin, dj, body, who))
        cur.execute(f"INSERT INTO {HIST_TBL} (SLUG, VIEW_NAME, ACTION, DEF_JSON, SQL_BODY, ACTED_BY) "
                    f"SELECT %s, %s, %s, PARSE_JSON(%s), %s, %s",
                    (slug, view, action, dj, body, who))
        conn.commit()
    finally:
        conn.close()
    _invalidate(slug)
    logger.info("[sf-scm] %s user-added report %s -> %s.%s by %s", action, slug, SCHEMA, view, who)
    return {"ok": True, "slug": slug, "view": f"{SCHEMA}.{view}", "action": action,
            "columns": cols}


@router.get("/custom/{slug}")
def scm_custom_get(slug: str):
    slug = str(slug or "").strip().lower()
    conn = base._conn()
    try:
        cur = conn.cursor()
        r = _custom_row(cur, slug)
        if not r or r[5]:
            raise HTTPException(404, f"No user-added report '{slug}'")
        defn = json.loads(r[3]) if r[3] else {}
        live_sql, ddl_error = None, None
        try:
            cur.execute("SELECT GET_DDL('VIEW', %s)", (f"{SCHEMA}.{r[1]}",))
            live_sql = _parse_sql(cur.fetchone()[0])[1]
        except Exception as e:
            ddl_error = str(getattr(e, "detail", e))[:300]
        cur.execute(f"SELECT ACTION, ACTED_BY, ACTED_AT FROM {HIST_TBL} WHERE SLUG = %s "
                    f"ORDER BY ACTED_AT DESC LIMIT 10", (slug,))
        hist = [{"action": h[0], "by": h[1], "at": base._fmt_str(h[2])} for h in cur.fetchall()]
    finally:
        conn.close()
    return {"slug": slug, "view": r[1], "origin": r[2], "definition": defn,
            # The LIVE body first: if someone altered the view in Snowsight, that is what is edited.
            "sql": live_sql or r[4] or "", "sqlSource": "live" if live_sql else "saved",
            "ddlError": ddl_error, "history": hist}


@router.delete("/custom/{slug}")
def scm_custom_delete(slug: str, drop_view: int = 1, user: Optional[str] = None):
    slug = str(slug or "").strip().lower()
    who = _who(user)
    dropped = False
    conn = base._conn()
    try:
        cur = conn.cursor()
        r = _custom_row(cur, slug)
        if not r or r[5]:
            raise HTTPException(404, f"No user-added report '{slug}'")
        # Only a view this editor CREATED is dropped. An adopted one existed before the app knew
        # about it, and something else may read it.
        if drop_view and r[2] == "created" and r[1].upper() not in _builtin_views():
            cur.execute(f"DROP VIEW IF EXISTS {SCHEMA}.{r[1]}")
            dropped = True
        cur.execute(f"UPDATE {DEF_TBL} SET IS_DELETED = TRUE, UPDATED_BY = %s, "
                    f"UPDATED_AT = CURRENT_TIMESTAMP() WHERE SLUG = %s", (who, slug))
        cur.execute(f"INSERT INTO {HIST_TBL} (SLUG, VIEW_NAME, ACTION, DEF_JSON, SQL_BODY, ACTED_BY) "
                    f"SELECT %s, %s, 'delete', PARSE_JSON(%s), %s, %s",
                    (slug, r[1], r[3] or "{}", r[4], who))
        conn.commit()
    finally:
        conn.close()
    _invalidate(slug)
    return {"ok": True, "slug": slug, "viewDropped": dropped, "view": f"{SCHEMA}.{r[1]}"}

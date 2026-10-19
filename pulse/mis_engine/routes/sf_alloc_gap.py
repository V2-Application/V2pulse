"""Snowflake MIS — ALLOCATION GAP REPORTS (mounted at /sf-alloc-gap).

TWENTY-SEVEN WORKBOOKS IN THE ALLOCATION TEAM'S OWN FORMAT, filled from the ARS run.

This is not another grid. Each of the 27 reports is a sheet the allocation team already reads,
with its own title, its own column names and — the whole point — its own GAP formula in the last
column. A row is not interesting because it is large; it is interesting because the formula turns
it into the word GAP. So every report here answers one question ("the store was given display but
got no allocation", "the DC is holding stock in a size nobody budgets") and the file is the
question's population with the verdict beside each row.

THE FORMAT IS THE TEMPLATE'S, NOT OURS
--------------------------------------
gap_templates/ holds the 27 .xlsx files exactly as they were handed over, and
gap_templates/_extract_layout.py turns them into gap_layout.json: title, header row, header text
(with its embedded newlines — 'SUB\\nDIV' is two lines in one cell), column widths, number formats
and every formula on the template's seed row with its row number replaced by {r}. The writer
below reproduces that spec with xlsxwriter and drops the data underneath. Nothing about the look
of these sheets is decided in this file; change a template, re-run the extractor, and the output
follows. What lives here is what the columns MEAN and which Snowflake column feeds each one.

The formulas are written as FORMULAS, not as their computed values. That is deliberate: the sheet
the team receives has to behave like the one they built — change a threshold in J and the verdict
column re-decides — and a report whose verdict is frozen text cannot be argued with.

WHERE THE NUMBERS COME FROM, AND WHY NOT ALL FROM ONE PLACE
-----------------------------------------------------------
  V2RETAIL.SILVER.V_ARS_ST_MJ_GAP     store x maj-cat. The balance sheet of the run: MBQ, stock
                                      before, allocated, stock after, still short. 255,638 rows
                                      on session 20260919_125317_275.
  BRONZE.ARS_MSA_OP_CL_VAR_ART        the DC's own pool, per RDC x variant article, carrying SZ,
  BRONZE.ARS_MSA_OP_CL_GEN_CLR        RNG_SEG, FAB_MVGR and PAK_SZ — which is what makes the
  BRONZE.ARS_MSA_OP_CL_MAJ_CAT        "MSA sitting in a dead size" family answerable at all.
  ARS_BRONZE.MASTER_CONT_SZ           the budget contribution masters, per store x maj-cat x
  ARS_BRONZE.MASTER_CONT_RNG_SEG      attribute. A maj-cat x attribute with contribution 0 in
  ARS_BRONZE.MASTER_CONT_M_YARN_02    every store is one nobody budgets anywhere.
  ARS_BRONZE.MASTER_PRODUCT           pack size, accessory density, and whether the article is
                                      active — the only per-article status there is.
  AKS_SILVER.SLV_MIS_STORE_MASTER     store open date and ST_FIX, the installed fixture count.
                                      The MIS master, for the coverage reasons noted at STM below.
  SILVER.V_ARS_MSA_STOCK              DC stock split by storage type, which is where GRT lives.

READ THE MSA TABLES FROM **BRONZE**, NEVER FROM MIS_REPORTS. Both schemas hold
ARS_MSA_OP_CL_VAR_ART and the names are identical. Measured 2026-09-22: BRONZE carries five
sessions up to 20260919_125317_275 (194,323 rows); MIS_REPORTS stops at 20260908_195824_318. A
report built from the MIS_REPORTS copy would have put an eleven-day-old DC pool beside today's
allocation and read as a mountain of phantom gaps. The grid view V_ARS_ST_MJ_GAP already reads
BRONZE.ARS_GRID_MJ, so BRONZE is also the consistent choice.

FOUR COLUMNS THE ARS GRID DOES NOT PUBLISH, AND WHAT STANDS IN FOR THEM
------------------------------------------------------------------------
V_ARS_ST_MJ_GAP selects literal NULL into C11_MJ_STAT, C14_SCHLD_ST, C20_ALC_GND and
C107_PROC_GRT_Q — not "usually empty" but `NULL "C11_MJ_STAT"` in the view text, and measured zero
non-null on all 255,638 rows of the current session. Six of the 27 reports are built on exactly
those columns, so each is fed from the nearest real source instead and SAYS SO: every proxied
column carries a cell note naming the substitute, and `substitutes` comes back from /params so a
caller can list them without opening a file. The proxies are in PROXY_NOTE below. They are not
corrections and they are not guesses dressed up as data — when the grid starts publishing the real
column, the proxy is deleted and the mapping changes in one place.

GRT IS A STORAGE-TYPE SPLIT, not a column. SILVER.V_ARS_MSA_STOCK buckets DC stock by storage
type: V02_GRT is GRT put away into the E-type picking bins (3.10M units, so it is the bulk of it)
and G01/G03 are the GRT receiving bins (74k). PROC-GRT is therefore the former and UN-PROC-GRT the
latter, which is the split the two reports of that name are asking about.

EVERY SHEET NAMES ITS RDC, AND THIRTEEN OF THEM CHANGED GRAIN TO DO IT
-----------------------------------------------------------------------
Nine of the templates always carried an RDC column, because they are store sheets and a store
belongs to one DC. On 2026-09-22 the allocation team asked for the same column on the thirteen
that had none — every remaining report except the five maj-cat x attribute sheets (SZ, MVGR,
RNG-SEG, ASR-SZ, PACK-SIZE), which they left alone.

A column cannot be added to a national row without changing what the row IS. A "maj-cat" row
covers both DCs, so there is no single RDC to print on it; the row therefore became RDC x
MAJ-CAT, and the thirteen reports go from 720 rows to 1,346 (and the two style x colour sheets
from 73,984 to 99,319). That is the more answerable question as well as the only honest one:
the pool is physically held at a DC and can only be picked for the stores that DC serves, so
"the stores are short and the DC still holds stock" was previously being asked of two DCs mixed
together.

NOTHING IS DOUBLE-COUNTED BY THE SPLIT, and it was checked rather than assumed: the MSA tables
are keyed on RDC already, so one row there is still one pool, and the remaining MSA across the
1,346 new rows totals 5,536,791 units — the same figure as across the 720 old ones. What the
split DOES drop is GRT held at one DC for a category no store of that DC carries: 17 RDC x
maj-cat rows, 73,264 units, which used to be set against the shortfall of the OTHER DC's stores.
That is the mixing the split exists to end, not a loss of data.

HOW BIG THESE GET
-----------------
The largest population is store x maj-cat at 255,638 rows, comfortably inside Excel. A report is
still checked against XLSX_MAX_ROWS before a single cell is written, because the alternative is a
workbook that Excel refuses to open after twenty minutes of work. GAP ROWS SORT FIRST in every
report: the file is the whole population (the verdict column is only meaningful against the rows
it clears as well as the rows it fails), but nobody should have to scroll to find the ones that
failed.
"""
import json
import logging
import os
import tempfile
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import sf_var_combined as base

logger = logging.getLogger(__name__)
router = APIRouter()

# Excel's own ceiling, less the header block. A report past this is refused rather than truncated:
# a silently short gap report is worse than no gap report, because it reads as "nothing to fix".
XLSX_MAX_ROWS = 1_048_000

_LAYOUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "gap_templates", "gap_layout.json")

# ── Sources ───────────────────────────────────────────────────────────────────
MJ = "V2RETAIL.SILVER.V_ARS_ST_MJ_GAP"
# What MJ is a view OVER. Every report reads the view; only the run's own metadata
# (APPROVED_AT, _LOADED_AT) has to come from here, because the view does not carry it.
MJ_BASE = "V2RETAIL.BRONZE.ARS_GRID_MJ"
MSA_VA = "V2RETAIL.BRONZE.ARS_MSA_OP_CL_VAR_ART"
MSA_GC = "V2RETAIL.BRONZE.ARS_MSA_OP_CL_GEN_CLR"
MSA_MJ = "V2RETAIL.BRONZE.ARS_MSA_OP_CL_MAJ_CAT"
# THE MIS STORE MASTER, not ARS_BRONZE.STORE_PLANT_MASTER, although the latter is the ARS
# module's own and carries the same three columns. Measured against this session's 522 stores:
# the MIS master matches 521 of them and the plant master 396; ST_OP_DT lands on 418 stores here
# against 306 there, and ST_FIX — the installed fixture count the INSTALLED VS ALC GND report is
# built on — is present on 521 here against 143 there. Its ST_OP_DT is also a real DATE, where
# the plant master's is free text ('23-Aug-25', 'NA', '0.00') that TRY_TO_DATE with AUTO parsed
# as the year 16 for at least one store. Four reports carry ST-OP-DT and one carries ST_FIX; on
# the plant master, three quarters of that fixture column would have been blank.
STM = "V2RETAIL.AKS_SILVER.SLV_MIS_STORE_MASTER"
PRD = "V2RETAIL.ARS_BRONZE.MASTER_PRODUCT"
DCSTK = "V2RETAIL.SILVER.V_ARS_MSA_STOCK"
CONT_SZ = "V2RETAIL.ARS_BRONZE.MASTER_CONT_SZ"
CONT_RS = "V2RETAIL.ARS_BRONZE.MASTER_CONT_RNG_SEG"
CONT_MV = "V2RETAIL.ARS_BRONZE.MASTER_CONT_M_YARN_02"

# The note attached to every cell of a proxied column. Keyed by the template's own header text so
# the writer can match without each report repeating itself.
PROXY_NOTE = {
    "MJ\nSTS": "PROXY — the ARS grid publishes C11_MJ_STAT as NULL. Read here as ACT when the "
               "major category still has at least one active article in MASTER_PRODUCT, IN when "
               "it has none.",
    "MJ-STS": "PROXY — the ARS grid publishes C11_MJ_STAT as NULL. Read here as ACT when the "
              "major category still has at least one active article in MASTER_PRODUCT, IN when "
              "it has none.",
    "SCHLD\nST": "PROXY — the ARS grid publishes C14_SCHLD_ST as NULL. Read here as 1 when the "
                 "store carries any budgeted MBQ in this session's grid, i.e. the run had "
                 "something to allocate to it.",
    "INSTALLED GND": "PROXY — read from the MIS store master's ST_FIX, the installed fixture "
                     "count. "
                     "The ARS grid has no installed-gandola column.",
    # The one substitution that does NOT measure the thing its header names, said plainly rather
    # than smoothed over. Nothing in this data counts allocated gandolas, and the nearest figure
    # is an OPTION count an order of magnitude larger (measured on the 142 stores that carry a
    # fixture count: mean 4,003 options against 442 fixtures, correlation 0.82). Manufacturing a
    # divisor to make the two meet would be inventing a number, so the option count is given as
    # it is and the reader is told the verdict column cannot be read on this sheet.
    "ALC GND": "NOT A GANDOLA COUNT — the ARS grid publishes C20_ALC_GND as NULL and nothing "
               "here counts allocated gandolas. This column is the BUDGETED OPTION count "
               "(display quantity / accessory density), which tracks fixtures (correlation 0.82) "
               "but runs about 9x larger: measured across the 142 stores with a fixture count, "
               "4,003 options against 442 fixtures. The GAP column on this sheet is therefore "
               "NOT meaningful — it reads GAP on every row — until ARS publishes C20_ALC_GND. "
               "Installed GND beside it is real.",
    "PROC\nGRT\nSTK": "PROXY — the ARS grid publishes C107_PROC_GRT_Q as NULL. Read here from "
                      "V_ARS_MSA_STOCK storage type V02_GRT: GRT put away into the picking bins.",
    "UN-PROC\nGRT\nSTK": "PROXY — no such column exists in the ARS grid. Read here from "
                         "V_ARS_MSA_STOCK storage types G01 and G03: GRT still in the receiving "
                         "bins.",
}


# ══════════════════════════════════════════════════════════════════════════════
# Report definitions
# ══════════════════════════════════════════════════════════════════════════════
class Report:
    """One workbook: a template to lay out, and a SELECT whose columns fill it left to right.

    `select` returns the SQL for one session. The column list it produces must line up with the
    template's non-formula columns IN ORDER — validate() checks that at import time rather than
    letting a mis-ordered report reach a user as plausible-looking nonsense in the wrong columns.

    `gap_when` is the template's own GAP condition, restated once as a SQL boolean. It is the
    ONLY place that condition is written, and three things read it: the ORDER BY that floats the
    failing rows to the top of the workbook, the verdict column the page shows beside each
    previewed row, and the gap COUNT on the page's picker. It began life as an ORDER BY fragment
    with the condition buried inside an IFF, which meant the page could sort by the verdict but
    not show it — and two of the conditions had already drifted from their template formula
    (PACK-SIZE's sort had lost the `SSN <> 'SSNL'` and `MJ-STS = ACT` terms the formula carries).

    It is still never a FILTER. The workbook is the whole population of the question, because a
    verdict column only means something against the rows it clears as well as the rows it fails.
    And the formula in the sheet remains the authority: it is what the reader sees, it re-decides
    when someone edits a threshold, and this is a restatement of it for sorting and for the page.

    `gap_sort` is the tiebreak among rows with the same verdict — usually the size of the problem.

    `shown` is the SQL for the template's INTERMEDIATE formula columns — the fill rate, the DIFF,
    the stock-after — keyed by the header they sit under. The workbook does not need them: Excel
    computes them from the formula the extractor lifted out of the template. The PAGE does, and
    on a fill-rate report that column IS the number the reader came for; a grid that showed MBQ
    and stock but not the ratio between them would be asking them to do the division. Eight of
    the 27 have one. Written in the same sheet-column terms as `gap_when`, for the same reason.
    """

    def __init__(self, key, template, title, columns, select, gap_when=None, gap_sort=None,
                 note=None, proxies=(), shown=None):
        self.key = key
        self.template = template          # basename in gap_templates/, without .xlsx
        self.title = title                # short name for the picker
        self.columns = columns            # template header text, in order, for the data columns
        self.select = select              # fn(session) -> SQL
        self.gap_when = gap_when           # SQL boolean: is THIS row a gap?
        self.gap_sort = gap_sort           # tiebreak within a verdict
        self.note = note                   # what the report is asking, one sentence
        self.proxies = list(proxies)       # header texts fed from a substitute source
        self.shown = dict(shown or {})     # {template header: SQL}, for the page only

    @property
    def gap_order(self):
        """Gap rows first, then the tiebreak. Derived so the condition cannot be stated twice."""
        parts = []
        if self.gap_when:
            parts.append(f"IFF({self.gap_when}, 0, 1)")
        if self.gap_sort:
            parts.append(self.gap_sort)
        return ", ".join(parts)


def _ses(s):
    """The session predicate. Sessions are opaque run ids, so they are quoted, never interpolated
    raw — and `base._q` is the same escaper every other Snowflake route in this service uses."""
    return f"SESSION_ID = {base._q(s)}"


# DIV IS RESERVED IN SNOWFLAKE and every one of these reports has a division column. Unquoted it
# is a syntax error at the alias, not a wrong result — all 27 statements failed to compile with
# "unexpected ','" until it was quoted — so the alias is written "DIV" everywhere below and every
# reference to it, qualified or not, carries the quotes with it. Snowflake folds an unquoted
# identifier to upper case, so "DIV" and a bare DIV elsewhere would refer to the same column;
# consistency here is for the reader, correctness is for the parser.


# ── the store x maj-cat spine, shared by the store-grain reports ──────────────
def _mj_base(s, extra=""):
    return f"""
  SELECT "C01_WERKS" ST_CD, "C02_ST_STAT" ST_STAT, "C03_STORE_NAME" ST_NM, "C04_RDC" RDC,
         "C07_SEG" SEG, "C08_DIV" "DIV", "C09_SUB_DIV" SUB_DIV, "C10_MAJ_CAT" MJ, "C12_SSN" SSN,
         COALESCE("C21_ACC_DENSITY",0) ACC_DENS, COALESCE("C56_BGT_DISP_Q",0) BGT_DISP,
         COALESCE("C61_MBQ_ST_TTL",0) MBQ, COALESCE("C64_ST_TTL_STK_BEFORE",0) STK_BEF,
         COALESCE("C67_ALC_Q",0) ALC_Q, COALESCE("C68_ST_TTL_STK_AFT",0) STK_AFT,
         COALESCE("C79_SHORT_Q_AFT",0) SHORT_AFT, COALESCE("C80_EXCESS_Q_AFT",0) EXCESS_AFT,
         COALESCE("C69_OP_HOLD_Q",0) OP_HOLD, COALESCE("C74_REM_HOLD_MSA_Q",0) REM_HOLD,
         COALESCE("C104_REM_MSA_Q",0) REM_MSA, COALESCE("C100_OP_MSA_V02",0) OP_MSA,
         COALESCE("C101_OPT_COUNT",0) OPT_CNT_OP, COALESCE("C105_OPT",0) OPT_CNT_CL,
         COALESCE("C90_SHORT_OPT",0) SHORT_OPT, COALESCE("C83_PER_OPT_MBQ",0) PER_OPT_MBQ
  FROM {MJ} WHERE {_ses(s)} {extra}"""


# The DC pool per RDC x maj-cat, counted ONCE. C104_REM_MSA_Q on the grid view is the pool
# repeated onto every store row beneath it (three of its siblings are literally SUM() OVER
# (PARTITION BY RDC, MAJ_CAT) inside the view), so a national report that added it down the store
# axis would multiply the DC's stock by the number of stores it serves. These come from the MSA
# tables directly, where one row IS one pool.
def _msa_mj(s):
    return f"""
  SELECT RDC, MAJ_CAT MJ,
         SUM(COALESCE(TRY_TO_DOUBLE(REM_MSA_Q),0))            REM_MSA,
         SUM(COALESCE(TRY_TO_DOUBLE(OP_MSA_Q_USE_IN_ALC),0))  OP_MSA,
         SUM(COALESCE(TRY_TO_DOUBLE(HOLD_AFT_ALC),0))         REM_HOLD,
         SUM(COALESCE(TRY_TO_DOUBLE(ALC_Q),0))                ALC_Q
  FROM {MSA_MJ} WHERE {_ses(s)} GROUP BY 1,2"""


def _msa_va(s):
    """Variant-grain DC pool. Carries the attributes the 'dead attribute' family needs."""
    return f"""
  SELECT RDC, SEG, "DIV", SUB_DIV, MAJ_CAT MJ, GEN_ART_NUMBER GEN, CLR, ARTICLE_NUMBER ART,
         NULLIF(TRIM(SZ),'') SZ, NULLIF(TRIM(RNG_SEG),'') RNG_SEG,
         NULLIF(TRIM(FAB_MVGR_1),'') MVGR, TRY_TO_DOUBLE(PAK_SZ) PAK_SZ,
         COALESCE(TRY_TO_DOUBLE(REM_MSA_Q),0)           REM_MSA,
         COALESCE(TRY_TO_DOUBLE(OP_MSA_Q_USE_IN_ALC),0) OP_MSA,
         COALESCE(TRY_TO_DOUBLE(HOLD_AFT_ALC),0)        REM_HOLD,
         COALESCE(TRY_TO_DOUBLE(ALC_Q),0)               ALC_Q
  FROM {MSA_VA} WHERE {_ses(s)}"""


# SSN and the hierarchy for a maj-cat, taken from the grid rather than the product master: the
# grid is what the run actually used, and the master carries categories the run never saw.
_MJ_DIM = """
  SELECT MJ, MAX(SEG) SEG, MAX("DIV") "DIV", MAX(SUB_DIV) SUB_DIV, MAX(SSN) SSN
  FROM b GROUP BY MJ"""

# THE SAME SPINE SPLIT BY RDC, which is the grain thirteen of these reports now read at.
#
# The allocation team asked for an RDC column on every sheet that did not already carry one
# except the five maj-cat x attribute sheets, and on a sheet with no store block there is only
# one way to honour that: the row becomes RDC x MAJ-CAT instead of MAJ-CAT, because a national
# row serves two DCs and cannot name one of them. That is also the more useful question — the
# pool is physically held at an RDC and can only be picked for the stores that RDC serves, so
# "stores short while the DC holds stock" was already being asked of a mixture of two DCs.
#
# The pairs come from the GRID, not from the pool tables: a pair the run never touched has no
# demand side to be short against, and would arrive as a row of zeroes with a verdict on it.
_RDC_MJ_DIM = """
  SELECT RDC, MJ, MAX(SEG) SEG, MAX("DIV") "DIV", MAX(SUB_DIV) SUB_DIV, MAX(SSN) SSN
  FROM b GROUP BY RDC, MJ"""

# The proxy for MJ STS. A major category is ACT while any article under it is still active in the
# product master, IN once none is — which is the only per-category status the warehouse has.
_MJ_STS = f"""
  SELECT MAJ_CAT MJ, IFF(MAX(IFF(COALESCE(IS_ACTIVE, FALSE), 1, 0)) = 1, 'ACT', 'IN') MJ_STS
  FROM {PRD} WHERE NULLIF(TRIM(MAJ_CAT),'') IS NOT NULL GROUP BY 1"""

# Pack size and accessory density per major category, averaged over its articles. Both are
# article attributes the PACK-SIZE report reads at category level.
_MJ_PAK = f"""
  SELECT MAJ_CAT MJ, AVG(PAK_SZ) PAK_SZ, AVG(ACC_DENSITY) ACC_DENS
  FROM {PRD} WHERE NULLIF(TRIM(MAJ_CAT),'') IS NOT NULL GROUP BY 1"""

# GRT at the DC, by RDC and major category. The storage-type split is the whole definition — see
# the module docstring. MATNR is 18-char zero-padded in the stock feed and in the product master,
# so they join without a cast.
#
# WERKS IS THE RDC. The stock view names the plant WERKS where the ARS tables name it RDC, and
# measured on 2026-09-22 the values are the same vocabulary: WERKS carries DH24, DW01 and DK04,
# the grid carries DH24 and DW01. DK04 holds GRT that this run never allocated from, and it
# drops out of every report because the spine is the grid — which is correct, not a loss: a
# report row for a DC the run did not use has no shortfall to set the GRT against.
_GRT = f"""
  SELECT k.WERKS RDC, p.MAJ_CAT MJ,
         SUM(IFF(k.PARTICULARS_NAME = 'V02_GRT',  k.PARTICULARS_VALUE, 0)) PROC_GRT,
         SUM(IFF(k.PARTICULARS_NAME IN ('G01','G03'), k.PARTICULARS_VALUE, 0)) UNPROC_GRT
  FROM {DCSTK} k
  JOIN {PRD} p ON p.MATNR = k.MATNR
  WHERE k.PARTICULARS_NAME IN ('V02_GRT','G01','G03')
  GROUP BY 1,2"""


# ── SIZE APPLICABLE ───────────────────────────────────────────────────────────
# Y when the category's articles carry real sizes, N when they are all one of the
# no-size tokens. There is no SZ_APP column on any ARS table at maj-cat grain; the gen_clr GAP
# view has one but only per store x gen article, which is a finer grain than these reports read.
_SZ_APP = f"""
  SELECT MAJ_CAT MJ,
         IFF(COUNT(DISTINCT IFF(UPPER(NVL(TRIM(SZ),'')) IN ('','A','ASSORTED','NA','FS','FREE'),
                                NULL, UPPER(TRIM(SZ)))) > 1, 'Y', 'N') SZ_APP
  FROM {PRD} WHERE NULLIF(TRIM(MAJ_CAT),'') IS NOT NULL GROUP BY 1"""


# ══════════════════════════════════════════════════════════════════════════════
# The 27 reports
# ══════════════════════════════════════════════════════════════════════════════
# ── store grain ───────────────────────────────────────────────────────────────
def _sql_disp_no_alc(s):
    """Display budgeted to a store, nothing allocated to it. Store grain, so the maj-cat rows are
    added up first — a store is only in trouble when the WHOLE store got nothing."""
    return f"""
WITH b AS ({_mj_base(s)}),
g AS (SELECT ST_CD, MAX(ST_STAT) ST_STAT, MAX(ST_NM) ST_NM, MAX(RDC) RDC,
             SUM(BGT_DISP) BGT_DISP, SUM(ALC_Q) ALC_Q
      FROM b GROUP BY ST_CD)
SELECT g.ST_CD, g.ST_STAT, m.ST_OP_DT, g.ST_NM, g.RDC,
       g.BGT_DISP, g.ALC_Q
FROM g LEFT JOIN {STM} m ON TRIM(m.ST_CD) = TRIM(g.ST_CD)"""


def _sql_disp_vs_density(s):
    """Display given to a store x category against the density its fixtures need. Rows with no
    display at all are dropped — the template's own formula only fires when K > 0, and a file of
    two hundred thousand blank verdicts is not a report."""
    return f"""
WITH b AS ({_mj_base(s, 'AND COALESCE("C56_BGT_DISP_Q",0) > 0')})
SELECT ST_CD, ST_STAT, ST_NM, RDC, SEG, "DIV", SUB_DIV, MJ, SSN, ACC_DENS, BGT_DISP FROM b"""


def _sql_excess_alc(s):
    """Stock already over MBQ before the run, and the run shipped more anyway."""
    return f"""
WITH b AS ({_mj_base(s)})
SELECT ST_CD, ST_STAT, ST_NM, RDC, SEG, "DIV", SUB_DIV, MJ, SSN, MBQ, STK_BEF, ALC_Q FROM b"""


def _sql_msa_vs_short(s):
    """The store is short and the DC still holds stock for that category."""
    return f"""
WITH b AS ({_mj_base(s)}), p AS ({_msa_mj(s)})
SELECT b.ST_CD, b.ST_STAT, b.ST_NM, b.RDC, b.SEG, b."DIV", b.SUB_DIV, b.MJ, b.SSN,
       b.MBQ, b.STK_AFT, COALESCE(p.REM_MSA,0)
FROM b LEFT JOIN p ON p.RDC = b.RDC AND p.MJ = b.MJ"""


def _sql_disp_not_given_alc_done(s):
    return f"""
WITH b AS ({_mj_base(s)})
SELECT ST_CD, ST_STAT, ST_NM, RDC, SEG, "DIV", SUB_DIV, MJ, SSN, BGT_DISP, ALC_Q FROM b"""


def _sql_st_mj_fillrate(s):
    return f"""
WITH b AS ({_mj_base(s)})
SELECT ST_CD, ST_STAT, ST_NM, RDC, SEG, "DIV", SUB_DIV, MJ, SSN, MBQ, STK_AFT FROM b"""


def _sql_one_linear_fillrate(s):
    """Fill rate for the whole store, every category added together."""
    return f"""
WITH b AS ({_mj_base(s)}),
g AS (SELECT ST_CD, MAX(ST_STAT) ST_STAT, MAX(ST_NM) ST_NM, MAX(RDC) RDC,
             SUM(MBQ) MBQ, SUM(STK_AFT) STK_AFT
      FROM b GROUP BY ST_CD)
SELECT g.ST_CD, g.ST_STAT, m.ST_OP_DT, g.ST_NM, g.RDC, g.MBQ, g.STK_AFT
FROM g LEFT JOIN {STM} m ON TRIM(m.ST_CD) = TRIM(g.ST_CD)"""


def _sql_zero_alc_scheduled(s):
    """A store the run was supposed to serve that came away with nothing.

    SCHLD ST is a PROXY (see PROXY_NOTE): the grid has no scheduled-store flag, so a store counts
    as scheduled when it carries budgeted MBQ in this session — the run had something to give it."""
    return f"""
WITH b AS ({_mj_base(s)}),
g AS (SELECT ST_CD, MAX(ST_STAT) ST_STAT, MAX(ST_NM) ST_NM, MAX(RDC) RDC,
             IFF(SUM(MBQ) > 0, 1, 0) SCHLD_ST, SUM(ALC_Q) ALC_Q
      FROM b GROUP BY ST_CD)
SELECT g.ST_CD, g.ST_STAT, m.ST_OP_DT, g.ST_NM, g.RDC,
       g.SCHLD_ST, g.ALC_Q
FROM g LEFT JOIN {STM} m ON TRIM(m.ST_CD) = TRIM(g.ST_CD)"""


def _sql_installed_vs_alc_gnd(s):
    """Fixtures installed in the store against the gandolas the allocation implies.

    THE ONE REPORT OF THE 27 WHOSE VERDICT CANNOT BE TRUSTED, and it is better to say that than
    to make the numbers meet. Installed GND is real — ST_FIX from the store master, on the 142
    stores that carry one. The allocated side is not: the ARS grid's C20_ALC_GND is NULL on every
    row and nothing else in this data counts gandolas. Display quantity over accessory density is
    an OPTION count; measured, it tracks fixtures well (correlation 0.82) but runs about nine
    times larger, so the template's +/-10 test reads GAP on every row. Dividing by nine to make it
    fit would be inventing a constant, so the option count is reported as what it is and the
    header carries a note saying so. The fix belongs upstream, in the grid."""
    return f"""
WITH b AS ({_mj_base(s)}),
g AS (SELECT ST_CD, MAX(ST_STAT) ST_STAT, MAX(ST_NM) ST_NM, MAX(RDC) RDC,
             SUM(IFF(ACC_DENS > 0, ROUND(BGT_DISP / ACC_DENS, 0), 0)) ALC_GND
      FROM b GROUP BY ST_CD)
SELECT g.ST_CD, g.ST_STAT, m.ST_OP_DT, g.ST_NM, g.RDC,
       TRY_TO_DOUBLE(m.ST_FIX::VARCHAR) INSTALLED_GND, g.ALC_GND
FROM g LEFT JOIN {STM} m ON TRIM(m.ST_CD) = TRIM(g.ST_CD)"""


def _sql_bgt_disp_vs_alc_disp(s):
    """Budgeted display against the display the stock can actually make after the run, by
    category.

    Two template quirks are honoured rather than tidied. The '.' column is a spacer the author
    uses to hold the block apart and is written empty, as the template has it. DIFF is a VALUE
    column here and not a formula — the percentage beside it (J) and the verdict (K) are the
    formulas — so it is computed in SQL, which also lets the gap sort use it."""
    return f"""
WITH b AS ({_mj_base(s)})
SELECT RDC, SEG, "DIV", SUB_DIV, MJ, SSN, NULL, SUM(BGT_DISP), SUM(STK_AFT),
       SUM(STK_AFT) - SUM(BGT_DISP)
FROM b GROUP BY RDC, SEG, "DIV", SUB_DIV, MJ, SSN"""


# ── RDC x maj-cat grain ───────────────────────────────────────────────────────
def _nat(s, extra_sel="", extra_join=""):
    """The RDC x maj-cat spine the ZERO/LOW/HOLD/GRT family all share.

    Quantities that belong to the STORE axis (MBQ, short after allocation, option counts) are
    summed across the stores of that RDC. Quantities that belong to the DC (remaining MSA, hold,
    GRT) come from the pool tables at their own grain and are joined on — summing those down the
    store axis is the single biggest trap in this data and would multiply the DC's stock by its
    store count. Splitting by RDC does not reintroduce that trap: the pool tables are keyed on
    RDC, so one row there is still one pool, and the two DCs' pools are now added to nothing."""
    return f"""
WITH b AS ({_mj_base(s)}),
d AS ({_RDC_MJ_DIM}),
st AS (SELECT RDC, MJ, SUM(MBQ) MBQ, SUM(ALC_Q) ALC_Q, SUM(SHORT_AFT) SHORT_AFT,
              SUM(SHORT_OPT) SHORT_OPT, SUM(BGT_DISP) BGT_DISP
       FROM b GROUP BY RDC, MJ),
pool AS (SELECT RDC, MJ, SUM(REM_MSA) REM_MSA, SUM(OP_MSA) OP_MSA, SUM(REM_HOLD) REM_HOLD
         FROM ({_msa_mj(s)}) GROUP BY RDC, MJ),
opt AS (SELECT RDC, MJ, SUM(REM_MSA) REM_MSA,
               COUNT(DISTINCT IFF(REM_MSA > 0, ART, NULL)) MSA_OPT_CNT
        FROM ({_msa_va(s)}) GROUP BY RDC, MJ)
{extra_sel}
SELECT d.RDC, d.SEG, d."DIV", d.SUB_DIV, d.MJ, d.SSN,
       COALESCE(st.MBQ,0) MBQ, COALESCE(st.ALC_Q,0) ALC_Q,
       COALESCE(st.SHORT_AFT,0) SHORT_AFT, COALESCE(st.SHORT_OPT,0) SHORT_OPT,
       COALESCE(pool.REM_MSA,0) REM_MSA, COALESCE(opt.MSA_OPT_CNT,0) MSA_OPT_CNT,
       COALESCE(pool.REM_HOLD,0) REM_HOLD, COALESCE(st.BGT_DISP,0) BGT_DISP
FROM d LEFT JOIN st ON st.RDC = d.RDC AND st.MJ = d.MJ
       LEFT JOIN pool ON pool.RDC = d.RDC AND pool.MJ = d.MJ
       LEFT JOIN opt ON opt.RDC = d.RDC AND opt.MJ = d.MJ
       {extra_join}"""


def _nat_cols(*picks):
    """Project the RDC x maj-cat spine down to one report's columns, in template order."""
    def build(s):
        return f"SELECT {', '.join(picks)} FROM ({_nat(s)})"
    return build


def _sql_hold_gap(s):
    return ('SELECT RDC, SEG, "DIV", SUB_DIV, MJ, SSN, MBQ, SHORT_AFT, SHORT_OPT, REM_MSA, '
            f'REM_HOLD FROM ({_nat(s)})')


def _sql_grt(s, which):
    """The two GRT-versus-shortfall reports and the one that wants both MSA and GRT.

    `which` picks the storage-type bucket — see the module docstring for why GRT is a bucket and
    not a column."""
    col = "PROC_GRT" if which == "proc" else "UNPROC_GRT"
    return f"""
WITH n AS ({_nat(s)}), grt AS ({_GRT})
SELECT n.RDC, n.SEG, n."DIV", n.SUB_DIV, n.MJ, n.SSN, n.MBQ, n.ALC_Q, n.SHORT_AFT, n.SHORT_OPT,
       COALESCE(grt.{col},0), 0
FROM n LEFT JOIN grt ON grt.RDC = n.RDC AND grt.MJ = n.MJ"""


def _sql_grt_and_msa(s):
    """GRT on the shelf at the DC while stores are short and the MSA pool is empty."""
    return f"""
WITH n AS ({_nat(s)}), grt AS ({_GRT})
SELECT n.RDC, n.SEG, n."DIV", n.SUB_DIV, n.MJ, n.SSN, n.MBQ, n.ALC_Q, n.SHORT_AFT, n.SHORT_OPT,
       n.REM_MSA, n.MSA_OPT_CNT, COALESCE(grt.PROC_GRT,0), 0
FROM n LEFT JOIN grt ON grt.RDC = n.RDC AND grt.MJ = n.MJ"""


def _sql_msa_in_inact_mj(s):
    """DC stock sitting under a category nobody sells any more. MJ STS is a proxy — see above.

    MJ STS has no RDC axis and is not given one: whether a category still has an active article
    is a property of the product master, the same answer at both DCs. The stock beside it is
    per-RDC, which is the point — a dead category can be dead at one DC and still held at the
    other."""
    return f"""
WITH b AS ({_mj_base(s)}), d AS ({_RDC_MJ_DIM}), sts AS ({_MJ_STS}),
pool AS (SELECT RDC, MJ, SUM(REM_MSA) REM_MSA FROM ({_msa_mj(s)}) GROUP BY RDC, MJ)
SELECT d.RDC, d.SEG, d."DIV", d.SUB_DIV, d.MJ, d.SSN, COALESCE(sts.MJ_STS,'IN'),
       COALESCE(pool.REM_MSA,0)
FROM d LEFT JOIN sts ON sts.MJ = d.MJ
       LEFT JOIN pool ON pool.RDC = d.RDC AND pool.MJ = d.MJ"""


def _sql_msa_no_disp(s):
    return f'SELECT RDC, SEG, "DIV", SUB_DIV, MJ, SSN, BGT_DISP, REM_MSA FROM ({_nat(s)})'


# ── gen article x colour grain ────────────────────────────────────────────────
def _sql_high_msa(s):
    """One colour of one style with more stock at the DC than the thresholds allow."""
    return f"""
WITH b AS ({_mj_base(s)}), d AS ({_RDC_MJ_DIM}),
m AS (SELECT RDC, MAJ_CAT MJ, GEN_ART_NUMBER GEN, CLR,
             SUM(COALESCE(TRY_TO_DOUBLE(REM_MSA_Q),0)) REM_MSA
      FROM {MSA_GC} WHERE {_ses(s)} GROUP BY 1,2,3,4)
SELECT m.RDC, d.SEG, d."DIV", d.SUB_DIV, m.MJ, d.SSN, m.GEN, m.CLR, m.REM_MSA
FROM m LEFT JOIN d ON d.RDC = m.RDC AND d.MJ = m.MJ"""


def _sql_low_msa_no_alc(s):
    """Stores short of a style x colour, nothing allocated, and barely any left at the DC."""
    return f"""
WITH b AS ({_mj_base(s)}), d AS ({_RDC_MJ_DIM}),
st AS (SELECT RDC, MJ, SUM(MBQ) MBQ, SUM(SHORT_AFT) SHORT_AFT, SUM(SHORT_OPT) SHORT_OPT
       FROM b GROUP BY RDC, MJ),
m AS (SELECT RDC, MAJ_CAT MJ, GEN_ART_NUMBER GEN, CLR,
             SUM(COALESCE(TRY_TO_DOUBLE(REM_MSA_Q),0)) REM_MSA,
             SUM(COALESCE(TRY_TO_DOUBLE(ALC_Q),0)) ALC_Q,
             COUNT(DISTINCT IFF(COALESCE(TRY_TO_DOUBLE(REM_MSA_Q),0) > 0, ARTICLE_NUMBER, NULL))
               MSA_OPT_CNT
      FROM {MSA_GC} WHERE {_ses(s)} GROUP BY 1,2,3,4)
SELECT m.RDC, d.SEG, d."DIV", d.SUB_DIV, m.MJ, m.GEN, m.CLR, d.SSN,
       COALESCE(st.MBQ,0), m.ALC_Q, COALESCE(st.SHORT_AFT,0), COALESCE(st.SHORT_OPT,0),
       m.REM_MSA, m.MSA_OPT_CNT
FROM m LEFT JOIN d ON d.RDC = m.RDC AND d.MJ = m.MJ
       LEFT JOIN st ON st.RDC = m.RDC AND st.MJ = m.MJ"""


# ── maj-cat x attribute grain: "the DC is holding a dead attribute" ───────────
def _sql_inact_attr(s, attr, cont_tbl, cont_col, only_assorted=False):
    """One family, three attributes, and the size-assortment variant of it.

    THE QUESTION. For a major category, an attribute value with a budget contribution of zero in
    every store is one the business does not plan for. If the DC is nonetheless holding MSA
    against it, that stock will never be allocated by contribution and is dead where it stands.

    BGT CONT% is the contribution across stores for that maj-cat x value, taken as the MAXIMUM
    rather than the sum or the mean. The question is 'does ANY store budget this at all', and a
    value budgeted heavily in one store and nowhere else must not average down to zero and be
    reported as dead.

    `only_assorted` switches the report from 'the attribute is dead' to 'the size is an
    assortment token' — the ASR-SZ sheet, which asks whether MSA is sitting under A / ASSORTED /
    NA in a category that does have real sizes.
    """
    where = ""
    if only_assorted:
        where = "WHERE UPPER(NVL(v.VAL,'')) IN ('A','ASSORTED','NA')"
    return f"""
WITH b AS ({_mj_base(s)}), d AS ({_MJ_DIM}), sa AS ({_SZ_APP}),
v AS (SELECT MJ, {attr} VAL, SUM(REM_MSA) REM_MSA
      FROM ({_msa_va(s)}) WHERE {attr} IS NOT NULL GROUP BY 1,2),
c AS (SELECT TRIM(MAJ_CAT) MJ, TRIM({cont_col}) VAL, MAX(TRY_TO_DOUBLE(CONT)) CONT
      FROM {cont_tbl} GROUP BY 1,2)
SELECT d.SEG, d."DIV", d.SUB_DIV, v.MJ, d.SSN, v.VAL, COALESCE(sa.SZ_APP,'N'),
       COALESCE(c.CONT,0), v.REM_MSA
FROM v LEFT JOIN d ON d.MJ = v.MJ
       LEFT JOIN sa ON sa.MJ = v.MJ
       LEFT JOIN c ON c.MJ = v.MJ AND UPPER(c.VAL) = UPPER(v.VAL)
{where}"""


def _sql_pack_size(s):
    """A GM category whose pack size is more than half its accessory density cannot fill the
    fixture in whole packs — which is what the template's H/2 < I test is asking."""
    return f"""
WITH b AS ({_mj_base(s)}), d AS ({_MJ_DIM}), sts AS ({_MJ_STS}), sa AS ({_SZ_APP}),
pk AS ({_MJ_PAK}),
pool AS (SELECT MJ, SUM(REM_MSA) REM_MSA FROM ({_msa_mj(s)}) GROUP BY MJ)
SELECT d.SEG, d."DIV", d.SUB_DIV, d.MJ, d.SSN, COALESCE(sts.MJ_STS,'IN'),
       COALESCE(sa.SZ_APP,'N'), pk.ACC_DENS, pk.PAK_SZ, COALESCE(pool.REM_MSA,0)
FROM d LEFT JOIN sts ON sts.MJ = d.MJ
       LEFT JOIN sa ON sa.MJ = d.MJ
       LEFT JOIN pk ON pk.MJ = d.MJ
       LEFT JOIN pool ON pool.MJ = d.MJ"""


REPORTS = [
    # ── store grain ──
    Report("disp_no_alc", "DISP GIVEN BUT ALC NOT DONE GAP REPORT",
           "Display given but no allocation",
           ["ST-CD", "ST-STAT", "ST-OP-DT", "ST-NM", "RDC", "BGT\nDISP", "ALC-Q"],
           _sql_disp_no_alc,
           gap_when="BGT_DISP > 0 AND ALC_Q = 0",
           gap_sort="BGT_DISP DESC",
           note="Stores the run budgeted display for and then shipped nothing to."),
    Report("disp_vs_density", "DISP VS ACC-DENSITY GAP REPORT",
           "Display below accessory density",
           ["ST-CD", "ST-STAT", "ST-NM", "RDC", "SEG", "DIV", "SUB\nDIV", "MJ", "SSN",
            "FINAL\nACC-DENS", "BGT\nDISP"],
           _sql_disp_vs_density,
           gap_when="BGT_DISP > 0 AND BGT_DISP < FINAL_ACC_DENS",
           gap_sort="(FINAL_ACC_DENS - BGT_DISP) DESC",
           shown={
                    "DIFF": "IFF(BGT_DISP > 0, BGT_DISP - FINAL_ACC_DENS, 0)",
                    },
           note="Display given to a store x category that will not even fill one fixture."),
    Report("excess_alc", "EXCESS ALC @ ST-MJ LEVEL REPORT",
           "Excess allocation at store x category",
           ["ST-CD", "ST-STAT", "ST-NM", "RDC", "SEG", "DIV", "SUB\nDIV", "MJ", "SSN",
            "ST-MJ\nMBQ", "ST-MJ\nBEFORE\nSTK", "ALC-Q"],
           _sql_excess_alc,
           gap_when="COALESCE(ST_MJ_BEFORE_STK / NULLIF(ST_MJ_MBQ,0), 0) > 1.1 AND ALC_Q > 0",
           gap_sort="ALC_Q DESC",
           shown={
                    "ST-MJ\nBEFORE\nFR%": "COALESCE(ST_MJ_BEFORE_STK / NULLIF(ST_MJ_MBQ,0), 0)",
                    "ST-MJ\nAFTER\nSTK": "ST_MJ_BEFORE_STK + ALC_Q",
                    "ST-MJ\nAFT\nFR%": "COALESCE((ST_MJ_BEFORE_STK + ALC_Q) / NULLIF(ST_MJ_MBQ,0), 0)",
                    },
           note="The store was already over 110% of MBQ and the run shipped more anyway."),
    Report("msa_vs_short", "MSA AVAILABLE VS STORE SHORT GAP",
           "DC holds stock, store still short",
           ["ST-CD", "ST-STAT", "ST-NM", "RDC", "SEG", "DIV", "SUB\nDIV", "MJ", "SSN",
            "ST-MJ\nMBQ", "TTL\nST-MJ\nSTK", "REM\nMSA"],
           _sql_msa_vs_short,
           gap_when="TTL_ST_MJ_STK > ST_MJ_MBQ AND REM_MSA > 0",
           gap_sort="ST_MJ_MBQ DESC",
           shown={
                    "SHORT\nAFT\nALC": "GREATEST(TTL_ST_MJ_STK - ST_MJ_MBQ, 0)",
                    },
           note="A store still short after the run, for a category the DC has stock in."),
    Report("disp_not_given_alc_done", "ST-MJ-DISPLAY NOT GIVEN STILL ALC DONE GAP REPORT",
           "No display, allocation done anyway",
           ["ST-CD", "ST-STAT", "ST-NM", "RDC", "SEG", "DIV", "SUB\nDIV", "MJ", "SSN",
            "BGT\nDISP-Q", "ALC-Q"],
           _sql_disp_not_given_alc_done,
           gap_when="BGT_DISP_Q = 0 AND ALC_Q > 0",
           gap_sort="ALC_Q DESC",
           note="Stock shipped into a store x category that has no display budgeted for it."),
    Report("st_mj_fillrate", "ST-MJ-FILLRATE GAP REPORT",
           "Store x category fill rate out of band",
           ["ST-CD", "ST-STAT", "ST-NM", "RDC", "SEG", "DIV", "SUB\nDIV", "MJ", "SSN",
            "ST-MJ\nMBQ", "ST-MJ\nTTL\nSTK"],
           _sql_st_mj_fillrate,
           gap_when="COALESCE(ST_MJ_TTL_STK / NULLIF(ST_MJ_MBQ,0), 0) NOT BETWEEN 0.8 AND 1.2",
           gap_sort="ABS(ST_MJ_TTL_STK - ST_MJ_MBQ) DESC",
           shown={
                    "ST-MJ\nFR%": "COALESCE(ST_MJ_TTL_STK / NULLIF(ST_MJ_MBQ,0), 0)",
                    },
           note="Fill rate after the run outside 80-120% of MBQ, either way."),
    Report("one_linear_fillrate", "ST-ONE-LINEAR-FILLRATE GAP REPORT",
           "Whole-store fill rate out of band",
           ["ST-CD", "ST-STAT", "ST-OP-DT", "ST-NM", "RDC", "ST-MBQ", "ST\nTTL\nSTK"],
           _sql_one_linear_fillrate,
           gap_when="COALESCE(ST_TTL_STK / NULLIF(ST_MBQ,0), 0) NOT BETWEEN 0.8 AND 1.2",
           gap_sort="ABS(ST_TTL_STK - ST_MBQ) DESC",
           shown={
                    "ST\nFR%": "COALESCE(ST_TTL_STK / NULLIF(ST_MBQ,0), 0)",
                    },
           note="The same test as the category sheet, run once for the whole store."),
    Report("zero_alc_scheduled", "ZERO ALC IN SCHEDULE ST REPORT",
           "Scheduled store got nothing",
           ["ST-CD", "ST-STAT", "ST-OP-DT", "ST-NM", "RDC", "SCHLD\nST", "ALC-Q"],
           _sql_zero_alc_scheduled,
           gap_when="NOT (SCHLD_ST > 0 AND ALC_Q > 0)",
           gap_sort="ST_CD",
           note="A store the run had something to give and gave nothing to.",
           proxies=["SCHLD\nST"]),
    Report("installed_vs_alc_gnd", "INSTALLED VS ALC GND REPORT",
           "Installed fixtures vs allocated gandolas",
           ["ST-CD", "ST-STAT", "ST-OP-DT", "ST-NM", "RDC", "INSTALLED GND", "ALC GND"],
           _sql_installed_vs_alc_gnd,
           gap_when="ABS(COALESCE(INSTALLED_GND,0) - ALC_GND) > 10",
           gap_sort="ABS(COALESCE(INSTALLED_GND,0) - ALC_GND) DESC",
           shown={
                    "INT \nVS\n ALC GND": "COALESCE(INSTALLED_GND,0) - ALC_GND",
                    },
           note="Fixtures the store has against the gandolas the allocation fills.",
           proxies=["INSTALLED GND", "ALC GND"]),
    Report("bgt_disp_vs_alc_disp", "BGT-DISP VS ALC-DISP GAP REPORT",
           "Budgeted display vs display after allocation",
           ["RDC", "SEG", "DIV", "SUB\nDIV", "MAJ-CAT", "SSN", ".", "BGT DISP", "ALC DISP", "DIFF"],
           _sql_bgt_disp_vs_alc_disp,
           gap_when="DIFF > 3000 OR DIFF < -1000 OR ABS(COALESCE(ALC_DISP / NULLIF(BGT_DISP,0) - "
                    "1, 0)) > 0.05",
           gap_sort="ABS(DIFF) DESC",
           shown={
                    None: "COALESCE(ALC_DISP / NULLIF(BGT_DISP,0) - 1, 0)",
                    },
           note="Display budgeted for a category against what its stock can actually dress."),

    # ── RDC x maj-cat grain ──
    Report("zero_allocation", "ZERO ALLOCATION GAP REPORT (MAJ-CAT)",
           "Stores short, DC has stock, nothing moved",
           ["RDC", "SEG", "DIV", "SUB\nDIV", "MJ", "SSN", "MJ\nMBQ", "ALC-Q", "SHORT\nAFT\nALC",
           "OPT SHORT\nAFT\nALC", "REM\nMSA", "REM MSA\nOPT\nCOUNT"],
           _nat_cols("RDC", "SEG", '"DIV"', "SUB_DIV", "MJ", "SSN", "MBQ", "ALC_Q", "SHORT_AFT",
                     "SHORT_OPT", "REM_MSA", "MSA_OPT_CNT"),
           gap_when="SHORT_AFT_ALC > 0 AND REM_MSA > 0",
           gap_sort="SHORT_AFT_ALC DESC",
           note="A category short across stores with stock still at the DC."),
    Report("zero_or_low_msa", "ZERO OR LOW MSA GAP REPORT",
           "Big shortfall, little or no MSA",
           ["RDC", "SEG", "DIV", "SUB\nDIV", "MJ", "SSN", "MJ\nMBQ", "ALC-Q", "SHORT\nAFT\nALC",
           "OPT SHORT\nAFT\nALC", "REM\nMSA", "REM MSA\nOPT\nCOUNT"],
           _nat_cols("RDC", "SEG", '"DIV"', "SUB_DIV", "MJ", "SSN", "MBQ", "ALC_Q", "SHORT_AFT",
                     "SHORT_OPT", "REM_MSA", "MSA_OPT_CNT"),
           gap_when="SHORT_AFT_ALC > 3000 AND REM_MSA < 3000",
           gap_sort="SHORT_AFT_ALC DESC",
           note="The shortfall is real and the DC cannot cover it — a buying signal, not a "
                "picking one."),
    Report("msa_available_no_alc", "MSA STK AVAILABLE VS NO ALC (CO_MAJ)",
           "MSA available, zero allocated",
           ["RDC", "SEG", "DIV", "SUB\nDIV", "MJ", "SSN", "MJ\nMBQ", "ALC-Q", "SHORT\nAFT\nALC",
           "OPT SHORT\nAFT\nALC", "REM\nMSA", "REM MSA\nOPT\nCOUNT"],
           _nat_cols("RDC", "SEG", '"DIV"', "SUB_DIV", "MJ", "SSN", "MBQ", "ALC_Q", "SHORT_AFT",
                     "SHORT_OPT", "REM_MSA", "MSA_OPT_CNT"),
           gap_when="SHORT_AFT_ALC > 100 AND REM_MSA > 1000 AND ALC_Q = 0",
           gap_sort="REM_MSA DESC",
           note="Stock, demand, and not one unit shipped."),
    Report("hold_gap", "HOLD GAP REPORT",
           "Too much of the pool is on hold",
           ["RDC", "SEG", "DIV", "SUB\nDIV", "MJ", "SSN", "MJ\nMBQ", "SHORT\nAFT\nALC",
           "OPT SHORT\nAFT\nALC", "REM\nMSA", "REM\nHOLD\nMSA"],
           _sql_hold_gap,
           gap_when="SHORT_AFT_ALC > 0 AND COALESCE(REM_HOLD_MSA / NULLIF(REM_MSA + "
                    "REM_HOLD_MSA,0), 0) > 0.3",
           gap_sort="REM_HOLD_MSA DESC",
           shown={
                    "HOLD\nVS\nTTL-MSA CONTB%": "COALESCE(REM_HOLD_MSA / NULLIF(REM_MSA + REM_HOLD_MSA,0), 0)",
                    },
           note="Stores are short while a third or more of the DC's pool is held back."),
    Report("hold_msa_gap", "HOLD MSA GAP REPORT",
           "Hold with no free pool behind it",
           ["RDC", "SEG", "DIV", "SUB\nDIV", "MJ", "SSN", "MJ\nMBQ", "SHORT\nAFT\nALC",
           "OPT SHORT\nAFT\nALC", "REM\nMSA", "REM\nHOLD\nMSA"],
           _sql_hold_gap,
           gap_when="(SEG = 'GM' AND REM_HOLD_MSA > 0) OR (REM_MSA = 0 AND REM_HOLD_MSA > 0 AND "
                    "SHORT_AFT_ALC > 0)",
           gap_sort="REM_HOLD_MSA DESC",
           note="Everything left for this category is on hold, and stores are short of it."),
    Report("msa_no_disp", "MSA AVAILABLE BUT DISP NOT GIVEN GAP REPORT",
           "MSA available, no display budgeted",
           ["RDC", "SEG", "DIV", "SUB\nDIV", "MJ", "SSN", "BGT\nDISP", "REM\nMSA"],
           _sql_msa_no_disp,
           gap_when="BGT_DISP = 0 AND REM_MSA > 1000",
           gap_sort="REM_MSA DESC",
           note="The DC is holding a category no store has been given room for."),
    Report("proc_grt_vs_short", "PROC-GRT VS CO-SHORT GAP REPORT",
           "Processed GRT against the shortfall",
           ["RDC", "SEG", "DIV", "SUB\nDIV", "MJ", "SSN", "MJ\nMBQ", "ALC-Q", "SHORT\nAFT\nALC",
           "OPT SHORT\nAFT\nALC", "PROC\nGRT\nSTK", "GRT\nOPT\nCOUNT"],
           lambda s: _sql_grt(s, "proc"),
           gap_when="SHORT_AFT_ALC > 1000 AND PROC_GRT_STK > 1000",
           gap_sort="SHORT_AFT_ALC DESC",
           note="Categories where prioritising GRT picking would close a real shortfall.",
           proxies=["PROC\nGRT\nSTK"]),
    Report("unproc_grt_vs_short", "UN-PROC-GRT VS CO-SHORT GAP REPORT",
           "Unprocessed GRT against the shortfall",
           ["RDC", "SEG", "DIV", "SUB\nDIV", "MJ", "SSN", "MJ\nMBQ", "ALC-Q", "SHORT\nAFT\nALC",
           "OPT SHORT\nAFT\nALC", "UN-PROC\nGRT\nSTK", "GRT\nOPT\nCOUNT"],
           lambda s: _sql_grt(s, "unproc"),
           gap_when="SHORT_AFT_ALC > 1000 AND UN_PROC_GRT_STK > 1000",
           gap_sort="SHORT_AFT_ALC DESC",
           note="The same, for GRT that has not been put away yet.",
           proxies=["UN-PROC\nGRT\nSTK"]),
    Report("grt_stock_st_short", "GRT STOCK AVAILABLE BUT ST-SHORT (CO-MJ)",
           "GRT on hand, MSA empty, stores short",
           ["RDC", "SEG", "DIV", "SUB\nDIV", "MJ", "SSN", "MJ\nMBQ", "ALC-Q", "SHORT\nAFT\nALC",
           "OPT SHORT\nAFT\nALC", "REM\nMSA", "REM MSA\nOPT\nCOUNT", "PROC\nGRT\nSTK",
           "GRT\nOPT\nCOUNT"],
           _sql_grt_and_msa,
           gap_when="SHORT_AFT_ALC > 100 AND REM_MSA = 0 AND PROC_GRT_STK > 0",
           gap_sort="SHORT_AFT_ALC DESC",
           note="The free pool is exhausted but GRT would cover it.",
           proxies=["PROC\nGRT\nSTK"]),
    Report("msa_in_inact_mj", "MSA IN IN-ACT MJ GAP REPORT",
           "MSA under an inactive category",
           ["RDC", "SEG", "DIV", "SUB\nDIV", "MJ", "SSN", "MJ\nSTS", "MSA\nSTK"],
           _sql_msa_in_inact_mj,
           gap_when="MJ_STS = 'IN' AND MSA_STK > 0",
           gap_sort="MSA_STK DESC",
           note="Stock at the DC under a category with nothing active left in it.",
           proxies=["MJ\nSTS"]),

    # ── gen article x colour grain ──
    Report("high_msa", "HIGH MSA GAP REPORT",
           "One colour holding too much",
           ["RDC", "SEG", "DIV", "SUB\nDIV", "MJ", "SSN", "GEN", "CLR", "REM\nMSA"],
           _sql_high_msa,
           gap_when="REM_MSA > IFF(SEG = 'GM', 5000, 3000)",
           gap_sort="REM_MSA DESC",
           note="A single style x colour over the threshold its segment allows."),
    Report("low_msa_no_alc", "LOW MSA-Q, ALC NOT DONE (MJ-RDC WISE)",
           "Short, nothing allocated, nothing left",
           ["RDC", "SEG", "DIV", "SUB\nDIV", "MJ", "GEN", "CLR", "SSN", "MJ\nMBQ", "ALC-Q",
           "SHORT\nAFT\nALC", "OPT SHORT\nAFT\nALC", "REM\nMSA", "REM MSA\nOPT\nCOUNT"],
           _sql_low_msa_no_alc,
           gap_when="SHORT_AFT_ALC > 0 AND ALC_Q = 0 AND REM_MSA < 50",
           gap_sort="SHORT_AFT_ALC DESC",
           note="A style x colour with demand, no allocation and an empty pool — push it "
                "manually or buy it."),

    # ── maj-cat x attribute grain ──
    Report("msa_inact_sz", "MSA IN IN-ACT MJ-SZ GAP REPORT",
           "MSA in a size nobody budgets",
           ["SEG", "DIV", "SUB\nDIV", "MJ", "SSN", "SZ", "SZ\nAPP", "BGT\nCONT%", "MSA\nSTK"],
           lambda s: _sql_inact_attr(s, "SZ", CONT_SZ, "SZ"),
           gap_when="SZ_APP = 'Y' AND BGT_CONT_PCT = 0 AND MSA_STK > 0",
           gap_sort="MSA_STK DESC",
           note="The DC holds a size with zero budget contribution in every store."),
    Report("msa_inact_mvgr", "MSA IN IN-ACT MJ-MVGR GAP REPORT",
           "MSA in an MVGR nobody budgets",
           ["SEG", "DIV", "SUB\nDIV", "MJ", "SSN", "MVGR", "SZ\nAPP", "BGT\nCONT%", "MSA\nSTK"],
           lambda s: _sql_inact_attr(s, "MVGR", CONT_MV, "M_YARN_02"),
           gap_when="SZ_APP = 'Y' AND BGT_CONT_PCT = 0 AND MSA_STK > 0",
           gap_sort="MSA_STK DESC",
           note="The same question asked of the fabric MVGR."),
    Report("msa_inact_rng_seg", "MSA IN IN-ACT RNG-SEG GAP REPORT",
           "MSA in a range segment nobody budgets",
           ["SEG", "DIV", "SUB\nDIV", "MJ", "SSN", "RNG\nSEG", "SZ\nAPP", "BGT\nCONT%",
            "MSA\nSTK"],
           lambda s: _sql_inact_attr(s, "RNG_SEG", CONT_RS, "RNG_SEG"),
           gap_when="SZ_APP = 'Y' AND BGT_CONT_PCT = 0 AND MSA_STK > 0",
           gap_sort="MSA_STK DESC",
           note="The same question asked of the range segment."),
    Report("asr_sz", "ASR-SZ IN SZ APP MJ GAP REPORT",
           "Assorted-size MSA in a sized category",
           ["SEG", "DIV", "SUB\nDIV", "MJ", "SSN", "SZ", "SZ\nAPP", "BGT\nCONT%", "MSA\nSTK"],
           lambda s: _sql_inact_attr(s, "SZ", CONT_SZ, "SZ", only_assorted=True),
           gap_when="SZ_APP = 'Y' AND MSA_STK > 0",
           gap_sort="MSA_STK DESC",
           note="Stock booked against A / ASSORTED / NA in a category that does run sizes."),
    Report("pack_size", "PACK-SIZE GAP REPORT",
           "Pack too big for the fixture",
           ["SEG", "DIV", "SUB-DIV", "MAJ_CAT", "SSN", "MJ-STS", "SZ-APP", "ACC\nDENSITY",
            "PAK_SZ", "REM\nMSA"],
           _sql_pack_size,
           gap_when="SEG = 'GM' AND COALESCE(SSN,'') <> 'SSNL' AND MJ_STS = 'ACT' AND REM_MSA > "
                    "100 AND ACC_DENSITY / 2 < PAK_SZ",
           gap_sort="REM_MSA DESC",
           note="A GM category whose pack size will not fit half the fixture.",
           proxies=["MJ-STS"]),
]

BY_KEY = {r.key: r for r in REPORTS}


# ══════════════════════════════════════════════════════════════════════════════
# Layout
# ══════════════════════════════════════════════════════════════════════════════
_LAYOUT = None


def _layout():
    """The template spec, read once. A missing or unreadable file is fatal on FIRST USE rather
    than at import: the rest of the service must not fail to start because one report's templates
    were not deployed."""
    global _LAYOUT
    if _LAYOUT is None:
        try:
            with open(_LAYOUT_PATH, encoding="utf-8") as fh:
                _LAYOUT = json.load(fh)
        except Exception as e:
            raise HTTPException(500, f"Gap report templates are not readable ({_LAYOUT_PATH}): {e}")
    return _LAYOUT


def _spec(rep):
    lay = _layout()
    if rep.template not in lay:
        raise HTTPException(500, f"No template layout for '{rep.template}' — re-run "
                                 f"gap_templates/_extract_layout.py")
    return lay[rep.template]


def validate():
    """Every report's SELECT must fill exactly the template's non-formula columns, in order.

    This is checked at import because the failure it catches is SILENT: a report with one column
    too few does not error, it shifts every value one cell left and hands over a plausible sheet
    full of wrong numbers. The check is on the HEADER TEXT the report declares against the header
    text the template carries, so it also catches a template renamed underneath a report.
    """
    problems = []
    try:
        with open(_LAYOUT_PATH, encoding="utf-8") as fh:
            lay = json.load(fh)
    except Exception as e:                       # deployment problem, reported by the endpoints
        logger.warning("[sf-alloc-gap] layout not readable at import: %s", e)
        return []
    for rep in REPORTS:
        spec = lay.get(rep.template)
        if not spec:
            problems.append(f"{rep.key}: no template '{rep.template}'")
            continue
        formula_cols = set(spec["formulas"])
        want = [h for i, h in enumerate(spec["headers"])
                if _col_letter(i) not in formula_cols]
        if [str(w) for w in want] != [str(c) for c in rep.columns]:
            problems.append(f"{rep.key}: template wants {want!r}, report declares {rep.columns!r}")
        for p in rep.proxies:
            if p not in spec["headers"]:
                problems.append(f"{rep.key}: proxy column {p!r} is not in the template")
    return problems


def _col_letter(i):
    """0-based index to an Excel column letter. Only ever called for the first 26x26."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


# ══════════════════════════════════════════════════════════════════════════════
# Reading
# ══════════════════════════════════════════════════════════════════════════════
def _sessions(cur):
    """The runs this report can be built for, newest first.

    Read from the grid view, which is the spine every report joins to: a session present only in
    the MSA tables has no store x category rows and would produce 27 empty workbooks."""
    cur.execute(f'SELECT DISTINCT SESSION_ID FROM {MJ} WHERE SESSION_ID IS NOT NULL '
                f'ORDER BY SESSION_ID DESC LIMIT 60')
    return [base._fmt_str(r[0]) for r in cur.fetchall()]


def _resolve_session(cur, session):
    s = str(session or "").strip()
    if s and s.lower() != "latest":
        return s
    cur.execute(f"SELECT MAX(SESSION_ID) FROM {MJ}")
    row = cur.fetchone()
    if not row or not row[0]:
        raise HTTPException(404, "No allocation session has been loaded — nothing to report on.")
    return base._fmt_str(row[0])


def _slug(header):
    """A template header turned into a SQL identifier: 'SHORT\\nAFT\\nALC' -> SHORT_AFT_ALC.

    The wrapper below names the report's output columns with these, which is what lets a gap
    condition be written about THE SHEET rather than about whichever internal alias the query
    happened to use. '%' becomes PCT because 'BGT\\nCONT%' is a real header, and the spacer column
    in BGT-DISP is literally named '.'."""
    s = str(header or "").strip().upper().replace("%", "_PCT")
    out = "".join(c if c.isalnum() else "_" for c in s).strip("_")
    while "__" in out:
        out = out.replace("__", "_")
    return out or "SPACER"


def _out_cols(rep):
    """The report's output column names — its template headers, slugified, in order.

    QUOTED, because 'DIV' is a header on 24 of the 27 sheets and a reserved word in Snowflake: an
    unquoted DIV in a derived table's column list is a syntax error naming the comma after it.
    Quoting the whole list rather than that one name keeps the rule from being re-learnt the next
    time a template gains a header that collides."""
    return [f'"{_slug(h)}"' for h in rep.columns]


def _report_sql(rep, session, verdict=False):
    """The report's rows, named after the sheet's own columns, failing rows first.

    EVERY QUERY IS WRAPPED IN A DERIVED TABLE WITH NAMED COLUMNS, and that is what makes the gap
    condition writable at all. A template's GAP formula only ever references cells in its own row
    of its own sheet — that is what a spreadsheet formula can do — so the condition is always
    expressible in terms of the report's OUTPUT. Naming those outputs after the headers lets
    `gap_when` say `BGT_DISP = 0 AND ALC_Q > 0`, which is the formula, instead of naming whichever
    CTE alias that value came from three joins ago. It also decouples the two: a report can be
    re-plumbed without touching its verdict.

    `verdict` adds the GAP/OK word as an extra last column, for the page. The WORKBOOK never asks
    for it — there the verdict is the template's own formula, so a reader who edits a threshold
    gets a sheet that re-decides. On screen there is no formula engine, so the same condition is
    evaluated in SQL. One condition, two renderings.

    Appended rather than inserted, even though the sheet puts GAP last among its columns: the
    projection has to keep matching `rep.columns` position for position, and sliding a column into
    the middle of that is how a preview starts quietly disagreeing with the file it previews."""
    named = f"SELECT * FROM (\n{rep.select(session)}\n) AS r({', '.join(_out_cols(rep))})"
    if verdict:
        # The data columns, then the template's intermediate formulas, then the verdict — the
        # order _preview_grid() below relies on to put them back in the sheet's own order.
        extra = [f'{sql_} AS "S{i}"' for i, sql_ in enumerate(rep.shown.values())]
        if rep.gap_when:
            extra.append(f"IFF({rep.gap_when}, 'GAP', 'OK') AS GAP_VERDICT")
        if extra:
            sql = f"SELECT r.*, {', '.join(extra)}\nFROM (\n{named}\n) AS r"
        else:
            sql = named
    else:
        sql = named
    if rep.gap_order:
        sql = f"{sql}\nORDER BY {rep.gap_order}"
    return sql


def _preview_grid(rep, spec, rows):
    """Re-lay the SELECT's columns into the SHEET's order, and say what each one is.

    The query returns data columns, then the computed ones, then the verdict — three blocks,
    because that is the order they can be computed in. The sheet interleaves them: ST-MJ-FILLRATE
    puts its FR% between the stock and the verdict, and EXCESS ALC has three computed columns
    scattered through sixteen. A grid that showed the three blocks in query order would not be
    the sheet any more, which is the one thing this page has to be."""
    n_data = len(rep.columns)
    shown_at = {h: n_data + i for i, h in enumerate(rep.shown)}
    verdict_at = n_data + len(rep.shown)
    # The GAP column is the LAST formula the template carries; the others are the computed ones.
    gap_header = spec["headers"][max(_col_index(c) for c in spec["formulas"])] \
        if spec["formulas"] else None

    order, kinds = [], []
    for h in spec["headers"]:
        if h in shown_at:
            order.append(shown_at[h]); kinds.append("computed")
        elif h == gap_header and rep.gap_when:
            order.append(verdict_at); kinds.append("verdict")
        elif h in rep.columns:
            order.append(rep.columns.index(h)); kinds.append("data")
        else:
            # A formula column with no `shown` SQL — it exists in the sheet and is blank here,
            # which is honest: Excel will fill it, this page cannot.
            order.append(None); kinds.append("formula-only")
    grid = [[(r[i] if i is not None and i < len(r) else None) for i in order] for r in rows]
    return spec["headers"], kinds, grid


def _gap_count_sql(rep, session):
    """How many rows this report calls a GAP — the number the page's picker leads with."""
    if not rep.gap_when:
        return None
    named = f"SELECT * FROM (\n{rep.select(session)}\n) AS r({', '.join(_out_cols(rep))})"
    return f"SELECT COUNT_IF({rep.gap_when}), COUNT(*) FROM (\n{named}\n) AS r"


def _count_sql(rep, session):
    return f"SELECT COUNT(*) FROM (\n{rep.select(session)}\n)"


# ══════════════════════════════════════════════════════════════════════════════
# Writing — the template's layout, reproduced
# ══════════════════════════════════════════════════════════════════════════════
def _write_xlsx(path, rep, spec, cur, session):
    """One workbook, laid out exactly as the template and filled from the cursor.

    CONSTANT MEMORY MEANS ROWS GO DOWN AND NEVER BACK UP. xlsxwriter flushes a row to disk the
    moment the next one is touched, so the title, the header and the totals strip are all written
    before the first data row — a write back to an earlier row after that is silently dropped and
    the file comes out looking like the header was never set.

    The totals strip is the one part that cannot be written in order, because SUM over the data
    has to know where the data ends. It is written as a FORMULA over the full column range the
    template itself uses (row 7 to Excel's last row), which is what the template does and what
    makes the strip keep working when someone deletes a row.
    """
    import xlsxwriter

    hdr_row = spec["header_row"] - 1                 # xlsxwriter is 0-based, the spec is 1-based
    data_row = spec["data_row"] - 1
    ncol = spec["n_cols"]
    headers = spec["headers"]
    formulas = spec["formulas"]
    formula_cols = {c: f for c, f in formulas.items()}

    wb = xlsxwriter.Workbook(path, {"constant_memory": True, "use_zip64": True,
                                    "default_date_format": "dd-mm-yyyy"})
    ws = wb.add_worksheet((spec.get("sheet") or "Sheet1")[:31])

    # THE FORMULA PREPARATION, RUN ONCE INSTEAD OF HALF A MILLION TIMES.
    #
    # xlsxwriter's Worksheet._prepare_formula rewrites the dynamic-array function names Excel
    # stores with an _xlfn. prefix — FILTER, SORT, XLOOKUP, LAMBDA and two dozen more — with one
    # re.sub per name, on EVERY write_formula call. These templates use IF / OR / AND / IFERROR /
    # MAX / SUM / SUBTOTAL / COUNTIFS and nothing else, so all of those substitutions are no-ops;
    # but ST-MJ-FILLRATE writes two formulas on each of 255,638 rows, which is 511,276 calls and
    # some fourteen million regex passes to produce exactly the string it was given.
    #
    # So it is run ONCE per formula, here, and the result asserted identical to the plain string.
    # Only if that holds for every formula in this template is the per-cell call replaced. A
    # template that ever did need the rewriting keeps it — the check fails closed, not open.
    prepared = {c: ws._prepare_formula(f.replace("{r}", "7")) == f.replace("{r}", "7").lstrip("=")
                for c, f in formulas.items()}
    if formulas and all(prepared.values()):
        ws._prepare_formula = lambda f, expand_future_functions=False: f.lstrip("=")
    elif formulas:
        logger.info("[sf-alloc-gap] %s: formula preparation is NOT a no-op for %s — keeping it",
                    rep.key, [c for c, ok in prepared.items() if not ok])

    title_fmt = wb.add_format({"bold": True, "font_size": 8})
    base_hdr = {"bold": True, "text_wrap": True, "valign": "bottom", "font_size": 8,
                "border": 1}
    hdr_fmts = []
    for i in range(ncol):
        f = dict(base_hdr)
        fill = (spec.get("header_fills") or [None] * ncol)[i]
        # The GAP column is yellow in every template. Other header fills are theme colours the
        # extractor cannot resolve to RGB, so they fall back to the neutral grey the sheets use.
        f["bg_color"] = f"#{fill}" if fill else "#D9D9D9"
        hdr_fmts.append(wb.add_format(f))

    # One format per column, from the template's own number format. A date column gets a real
    # date format so ST-OP-DT is a date in Excel and not a five-digit serial.
    col_fmts = []
    for i in range(ncol):
        nf = (spec.get("formats") or [])[i] if i < len(spec.get("formats") or []) else "General"
        spec_fmt = {"font_size": 9}
        if nf and nf != "General":
            spec_fmt["num_format"] = nf
        col_fmts.append(wb.add_format(spec_fmt))
    date_fmt = wb.add_format({"font_size": 9, "num_format": "dd-mm-yyyy"})

    if spec.get("title"):
        ws.write(0, 0, spec["title"], title_fmt)

    for i in range(ncol):
        ws.write(hdr_row, i, headers[i], hdr_fmts[i])
        w = (spec.get("widths") or [None] * ncol)[i]
        ws.set_column(i, i, w if w else 11)
    if spec.get("header_row_height"):
        ws.set_row(hdr_row, spec["header_row_height"])
    else:
        # The headers wrap onto up to three lines ('OPT SHORT\nAFT\nALC'); without a set height
        # Excel shows one line and clips the rest.
        ws.set_row(hdr_row, 11 * (1 + max(str(h).count("\n") for h in headers if h)))

    # The strip between the header and the data, re-pointed at this file's data range.
    for off, row in (spec.get("strip") or {}).items():
        r = hdr_row + int(off)
        for letter, val in row.items():
            c = _col_index(letter)
            if isinstance(val, str) and val.startswith("="):
                ws.write_formula(r, c, val, col_fmts[c])
            else:
                ws.write(r, c, val, col_fmts[c])

    # A note on every proxied header, so the substitution travels with the file rather than
    # living only in a chat message.
    for i in range(ncol):
        h = headers[i]
        if h in rep.proxies and h in PROXY_NOTE:
            ws.write_comment(hdr_row, i, PROXY_NOTE[h], {"width": 320, "height": 120})

    # Which template columns the SELECT fills, and which are computed by Excel.
    data_idx = [i for i in range(ncol) if _col_letter(i) not in formula_cols]

    # TYPED WRITES, BOUND ONCE. `ws.write()` is a dispatcher: it re-decides what every value is —
    # string, number, bool, None, datetime, a URL worth turning into a hyperlink — on all 3.3
    # million cells of the largest report. The types here come out of Snowflake and are already
    # known per value, so the dispatch is done with one `type()` lookup and the typed method is
    # called directly. Measured on 120,000 rows x 13 columns: 63.4 s to 49.3 s, a fifth off, on
    # top of the formula-preparation saving above. Anything unexpected still falls through to
    # `write()`, so a type nobody anticipated is handled rather than dropped.
    w_str, w_num, w_dt, w_f, w_any = (ws.write_string, ws.write_number, ws.write_datetime,
                                      ws.write_formula, ws.write)
    formula_at = [(_col_index(letter), tpl) for letter, tpl in formula_cols.items()]

    # Blank TEXT cells are written 'NA' (= ISNULL(col,'NA')); numeric blanks stay empty so the
    # template's formulas keep computing.
    from .sf_gandola import _text_cols, NA
    text = _text_cols(cur)
    r = data_row
    n = 0
    while True:
        batch = cur.fetchmany(5000)
        if not batch:
            break
        for src in batch:
            if r > XLSX_MAX_ROWS:
                raise HTTPException(
                    400, f"{rep.title} is past Excel's row limit — narrow the session or take "
                         f"it as CSV.")
            excel_row = r + 1                        # formulas are written 1-based
            for j, i in enumerate(data_idx):
                v = src[j] if j < len(src) else None
                if v is None:
                    if j < len(text) and text[j]:
                        w_str(r, i, NA, col_fmts[i])
                    continue
                t = v.__class__
                if t is str:
                    w_str(r, i, v if v.strip() else NA, col_fmts[i])
                elif t is float or t is int:
                    w_num(r, i, v, col_fmts[i])
                elif t is Decimal:
                    # Snowflake hands NUMBER back as Decimal; xlsxwriter wants something float()
                    # understands, and Excel has no decimal type to preserve anyway.
                    w_num(r, i, float(v), col_fmts[i])
                elif hasattr(v, "year"):
                    w_dt(r, i, v, date_fmt)
                else:
                    w_any(r, i, v, col_fmts[i])
            for c, tpl in formula_at:
                w_f(r, c, tpl.replace("{r}", str(excel_row)), col_fmts[c])
            r += 1
            n += 1

    if n:
        # Filter over the header and the data. The template anchors its filter on the data row,
        # which leaves the header outside it; anchoring on the header is what a reader expects
        # and is the one deliberate departure from the template's own setting.
        ws.autofilter(hdr_row, 0, r - 1, ncol - 1)
        ws.freeze_panes(data_row, 0)
    wb.close()
    return n


def _col_index(letter):
    i = 0
    for ch in letter:
        i = i * 26 + (ord(ch) - 64)
    return i - 1


# ══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/params")
def params():
    """The catalogue and the sessions, which is everything a caller needs to order a file."""
    conn = base._conn()
    try:
        cur = conn.cursor()
        sessions = _sessions(cur)
        # APPROVED_AT COMES FROM THE BASE TABLE, NOT THE VIEW. V_ARS_ST_MJ_GAP selects SESSION_ID
        # and its hundred-odd C-columns and nothing else — APPROVED_AT and _LOADED_AT stay on
        # BRONZE.ARS_GRID_MJ underneath it. Asking the view for it is not an empty column, it is
        # "invalid identifier 'APPROVED_AT'" and a 502 on the whole catalogue.
        cur.execute(f"SELECT MAX(APPROVED_AT), COUNT(*) FROM {MJ_BASE} "
                    f"WHERE {_ses(sessions[0])}"
                    if sessions else "SELECT NULL, 0")
        row = cur.fetchone() or (None, 0)
    finally:
        conn.close()
    problems = validate()
    if problems:
        logger.warning("[sf-alloc-gap] layout mismatches: %s", "; ".join(problems))
    return {
        "sessions": sessions,
        "latest": sessions[0] if sessions else None,
        "approvedAt": base._fmt_str(row[0]),
        "gridRows": int(row[1] or 0),
        "reports": [{"key": r.key, "title": r.title, "file": r.template, "note": r.note,
                     "columns": r.columns, "substitutes": r.proxies} for r in REPORTS],
        # Named once, here, so a caller can show the caveat without opening a workbook.
        "substituteNotes": PROXY_NOTE,
        "layoutProblems": problems,
    }


# The gap counts for one session, cached. A session is a FINISHED run — its numbers cannot change
# unless it is reloaded — so the 27 counts are computed once and kept. Without this the page pays
# ~40 s of Snowflake on every visit to answer a question whose answer is frozen. ?refresh=1 is the
# reload case, the same escape hatch the allocation grid's option cache has.
_COUNTS_CACHE = {}


@router.get("/counts")
def counts(session: Optional[str] = None, refresh: int = 0):
    """How many rows each of the 27 reports calls a GAP, for one run.

    THIS IS THE PAGE'S MAIN QUESTION. Twenty-seven sheets is too many to open one by one every
    morning; what an allocator needs first is which of them has anything in it today. So the
    counts come back together, and the page can rank the list by them instead of listing 27
    titles alphabetically and leaving the reader to go fishing.

    A report that fails to count does not fail the sweep — it comes back with `error` and the
    other twenty-six still answer. One broken report must not hide the state of the rest.
    """
    conn = base._conn()
    try:
        cur = conn.cursor()
        ses = _resolve_session(cur, session)
        if not refresh and ses in _COUNTS_CACHE:
            return {"session": ses, "cached": True, "counts": _COUNTS_CACHE[ses]}
        out = []
        for rep in REPORTS:
            row = {"key": rep.key, "title": rep.title, "note": rep.note,
                   "file": rep.template, "substitutes": rep.proxies}
            try:
                gap_sql = _gap_count_sql(rep, ses)
                if gap_sql:
                    cur.execute(gap_sql)
                    g, t = [int(v or 0) for v in (cur.fetchone() or (0, 0))]
                else:
                    cur.execute(_count_sql(rep, ses))
                    g, t = 0, int((cur.fetchone() or [0])[0] or 0)
                row["gaps"], row["total"] = g, t
            except Exception as e:                       # noqa: BLE001 — reported, not raised
                logger.warning("[sf-alloc-gap] count failed for %s: %s", rep.key, e)
                row["gaps"], row["total"] = None, None
                row["error"] = str(e).splitlines()[0][:200]
            out.append(row)
        _COUNTS_CACHE[ses] = out
        return {"session": ses, "cached": False, "counts": out}
    finally:
        conn.close()


class PreviewReq(BaseModel):
    report: str
    session: Optional[str] = None
    limit: int = 200


@router.post("/preview")
def preview(req: PreviewReq):
    """The first rows of one report, for a screen. Same SQL and same order as the file.

    The rows carry the VERDICT as a last column, because a gap report that cannot show its own
    verdict is just a table. The count beside it is how many rows the whole report calls a GAP —
    the number that decides whether this sheet is worth opening at all, and the reason the page
    can rank the 27 rather than list them alphabetically."""
    rep = BY_KEY.get(str(req.report or "").strip())
    if not rep:
        raise HTTPException(400, f"unknown report '{req.report}'")
    spec = _spec(rep)
    limit = max(1, min(int(req.limit or 200), 2000))
    conn = base._conn()
    try:
        cur = conn.cursor()
        session = _resolve_session(cur, req.session)
        cur.execute(f"{_report_sql(rep, session, verdict=True)}\nLIMIT {limit}")
        raw = [[base._fmt_str(v) for v in row] for row in cur.fetchall()]
        headers, kinds, rows = _preview_grid(rep, spec, raw)
        gap_sql = _gap_count_sql(rep, session)
        if gap_sql:
            cur.execute(gap_sql)
            gaps, total = [int(v or 0) for v in (cur.fetchone() or (0, 0))]
        else:
            cur.execute(_count_sql(rep, session))
            gaps, total = 0, int((cur.fetchone() or [0])[0] or 0)
    finally:
        conn.close()
    return {"report": rep.key, "title": rep.title, "note": rep.note,
            "session": session, "total": total, "gaps": gaps,
            # The sheet's OWN columns, in the sheet's own order — computed ones included, so the
            # grid is the workbook rather than a subset of it. `kinds` says which is which, so
            # the page can mark a computed column as computed.
            "columns": headers, "kinds": kinds, "rows": rows,
            "file": rep.template,
            "substitutes": rep.proxies,
            "substituteNotes": {p: PROXY_NOTE[p] for p in rep.proxies if p in PROXY_NOTE}}


@router.get("/export")
def export(report: str, session: Optional[str] = None):
    """One workbook, in the template's format, streamed back.

    The count runs BEFORE the workbook is opened. A report past Excel's limit has to be refused
    while refusing is still cheap — finding out at row 1,048,001 means twenty minutes of work and
    a temp file to clean up."""
    rep = BY_KEY.get(str(report or "").strip())
    if not rep:
        raise HTTPException(400, f"unknown report '{report}'")
    spec = _spec(rep)

    conn = base._conn()
    path = None
    try:
        cur = conn.cursor()
        ses = _resolve_session(cur, session)
        cur.execute(_count_sql(rep, ses))
        total = int((cur.fetchone() or [0])[0] or 0)
        if total > XLSX_MAX_ROWS:
            raise HTTPException(400, f"{rep.title}: {total:,} rows is past Excel's own limit of "
                                     f"{XLSX_MAX_ROWS:,}.")
        cur.execute(_report_sql(rep, ses))
        fd, path = tempfile.mkstemp(suffix=".xlsx", prefix="alloc_gap_")
        os.close(fd)
        _write_xlsx(path, rep, spec, cur, ses)
    except Exception:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
        raise
    finally:
        conn.close()

    fname = f"{rep.template}.xlsx"

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
        stream(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"',
                 "X-Gap-Rows": str(total), "X-Gap-Session": ses})

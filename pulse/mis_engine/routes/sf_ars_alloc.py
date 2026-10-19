"""
Snowflake MIS — ALLOCATION (mounted at /sf-ars-alloc).

THREE GRAINS OF ONE RUN, behind one set of endpoints. The ARS allocation run publishes its gap
report at three levels, and they are the same report read at three zoom settings:

  level      view                            grain                          measured (20260908)
  ---------  ------------------------------  -----------------------------  -------------------
  var_art    SILVER.V_ARS_ST_VAR_ART_GAP     store x variant article        2,530,535 rows
  gen_clr    SILVER.V_ARS_ST_GEN_CLR_GAP     store x maj-cat x gen x colour 4,913,163 rows
  maj_cat    SILVER.V_ARS_ST_MJ_GAP          store x major category           209,434 rows

Everything below — filters, search, paging, totals, the summary strip and the export — is written
ONCE against a LEVEL description, because the three differ in their columns and in nothing else.
A caller picks the level; absent, it gets var_art, which is what this route served before the
other two existed.

A SESSION, NOT A DATE RANGE — WHICH IS WHY IT IS ITS OWN PAGE
------------------------------------------------------------
Every other MIS report is anchored on days: sale summed over a range, stock at the end of it. An
allocation run has no range at all. It is one decision taken at one instant, and the whole report
is the state of that decision — what the store needed, what the DC could give it, what it got, and
what is still short afterwards. Squeezing it into the report builder would have meant inventing a
window dimension it does not have. So the picker here is a SESSION, defaulting to the latest one
loaded, and every number on the page belongs to that single run.

IT IS A GAP REPORT, AND MOST OF IT IS THE GAP
---------------------------------------------
Measured on session 20260908_195824_318 at var_art: 2,113,404 rows SKIPPED against 362,406
ALLOCATED and 54,725 PARTIAL. At gen_clr the same run shipped on 166,082 of 4,913,163 rows. The
rows that shipped nothing are the point — they carry the shortfall the run declined to fill and
the reason it declined. A report that showed only what shipped would be hiding most of its own
subject, which is why the summary strip leads with the reasons.

THE DERIVED COLUMNS, AND WHY EACH ONE EXISTS
--------------------------------------------
Nothing is hidden and nothing is corrected; the derived columns sit BESIDE the raw ones.

  var_art  SKIP_FAMILY    C54_SKIP_REASON is parameterised — SEC_CAP_PRE_MJ_RNG_SEG(cap=130%,
                          cont=34%) and (cap=150%,cont=16%) are one rule with different numbers.
                          5,641 distinct raw reasons become 16 families. A PARTIAL row states its
                          reason LAST, after the engine's trace, so the family comes from the last
                          ';' segment, not the first token (cutting at the first bracket returned
                          "B" for 19,949 rows and lost SKIP_MSA_EXHAUSTED entirely).
  gen_clr  ALLOC_FAMILY   the same treatment of C138_ALLOC_REASON: 282 raw strings, 19 families.
           SHIPPED        YES when C46_ALC_Q_MACHINE > 0. This view has no status column, and
                          measured, "shipped" and "the reason is blank" are the same 166,082 rows.
  maj_cat  —              this view carries no reason at all. It is the store x category balance
                          sheet: MBQ, stock before, allocated, stock after, what is still short.

FOUR WAYS TO SUBTOTAL A COLUMN, BECAUSE ONE RULE IS WRONG FOR MOST OF THEM
--------------------------------------------------------------------------
  sum        — the quantities that live at the row's own grain. They add.
  avg        — ratios and article attributes (MRP, pack size, rods, fill rate %). Adding a
               percentage across millions of rows produces a number with no meaning.
  dedup      — a POOLED figure repeated onto every row under its key, summed ONCE per key.
  dedup-avg  — a pooled RATIO: averaged once per key.
Which rule each column got comes back with the totals (`rule`) and the key with it (`dedupKeys`),
so the page can label the strip rather than leave the reader to guess. The pooled columns are the
single biggest trap in this report:

  var_art  C45/C46 MSA are the DC's pool per variant x RDC, constant across all 39,100
           combinations: summed naively 210,696,556 against 5,524,108 counted once — 38x.
  gen_clr  FIVE roll-up blocks are stapled onto every gen x colour row — the store x maj-cat
           block (C62-C74) and then the same thirteen figures again per MERGE RNG SEG (C77-C89),
           RNG SEG (C92-C104), MVGR 1 (C107-C119) and FIT (C122-C134). Measured: each block is
           constant within its key on every one of its keys (218,968 store x maj-cat keys, zero
           disagreements), so a store's maj-cat MBQ would otherwise be counted once per gen x
           colour row beneath it. C50/C53/C54 _TTL are the DC total per RDC x gen x colour:
           C50_ALC_TTL sums to 333,281,604 naively against an actual allocation of 1,563,266.
  maj_cat  C100-C106 are the MSA pool and hold per RDC x maj-cat — three of them are literally
           SUM() OVER (PARTITION BY RDC, MAJ_CAT) inside the view. 1,317 keys, zero disagreements.

WHAT THE SOURCE VIEWS GET WRONG, STATED HERE RATHER THAN PAPERED OVER
---------------------------------------------------------------------
var_art C41_FR_PCT_BEFORE is defined in the view as STK_AFT_ALC / MBQ — the same expression as
C42_FR_PCT_AFTER. Measured: equal on all 2,530,535 rows, so "fill rate before" reports the fill
rate AFTER. Both are exposed unchanged; the page carries the warning and the fix belongs in the
view (N_BFR in place of N_AFT).
COLUMNS THAT CARRY NOTHING are declared per level (`deadCols`), because the views select literal
NULLs and placeholders into them: 2 of 49 at var_art, 12 of 137 at gen_clr (eight literal NULLs,
C23_ART_TYPE_FINAL empty on every row, and C140/C142/C144 the constant string 'NA'), and 33 of 106
at maj_cat — a third of that view. They are hidden by default and one click brings them back: a
column that is empty by construction is worth knowing about, and worth not scrolling past.
GRAIN IS EXACT AT var_art AND maj_cat (row count = distinct key). At gen_clr, 3,990 rows of
4,913,163 repeat their store x maj-cat x gen x colour key, split by SSN in the ARS source.
maj_cat covers 463 stores where the article levels cover 434: the category grid carries stores
that got no article rows at all.
maj_cat also holds a legacy load with SESSION_ID NULL (219,756 rows, loaded 2026-08-24). It is
offered in the picker as '(no session)' rather than dropped, because it is real data the view
returns.

TWO WAYS TO FILTER, BOTH LANDING IN THE SAME WHERE
--------------------------------------------------
  col_filters  {COL: [values]} for the tick-lists in the Filters panel — only the level's own
               FILTER_COLS, with '(blank)' meaning NULL or empty.
  conds        the column-header menu: [{col, op, v1, v2, vals}] over ANY column, with the
               operators the MIS grid offers (contains / begins / between / is any of / is
               blank …). Compiled by sf_mis_report._col_where with `ref=_expr`, so one condition
               vocabulary serves both grids and a condition can sit on a DERIVED column.
Both are applied by _where(), which the grid, the totals, the summary and the export all go
through — so a filter can never narrow the rows without also narrowing the strip above them.

Escaping: this route inlines its filter values (Snowflake takes no bind variables inside the
option-list aggregation, and the code paths would otherwise disagree), so every identifier is
checked against that level's column catalogue and every literal goes through base._q().
"""
import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import sf_cancel
from ..worker.auto_gandola_sf import get_sf_read_connection
from .sf_gandola import _stream_csv
from . import sf_var_combined as base
# The column-header conditions (contains / begins with / between / is any of / is blank …) are
# compiled by the MIS report's OWN clause builder rather than a second one written here. It takes
# a `ref` callable for how a column is named, which is exactly the hook a derived column like
# SKIP_FAMILY needs — and it already carries the escaping, the code-vs-number rules and the
# NULL handling that took that page several rounds to get right. One grid menu, one compiler.
from . import sf_mis_report as _conds

logger = logging.getLogger(__name__)
router = APIRouter()

# The grid pages 200 at a time. This ceiling is for a caller that asks for more in one go.
MAX_LIMIT = 5_000

# The legacy maj_cat load has no SESSION_ID. This token stands for "the rows with none" in the
# picker and in col_filters, and _where() turns it back into IS NULL.
NO_SESSION = "(no session)"


class Level:
    """One grain of the allocation gap report: a view plus how to read its columns.

    Everything the endpoints need is here, so adding a fourth grain is a fourth Level and no new
    code path. The lists are kept in the VIEW'S OWN column order deliberately — the C-numbers are
    the order the allocation team reads these in, and re-sorting them into "dimensions then
    measures" would stop the grid lining up with the sheet they check it against.
    """

    def __init__(self, key, label, grain, view, meta_tbl, all_cols, text_cols, sum_cols, avg_cols,
                 pool_cols, pct_cols, dead_cols, filter_groups, search_cols, essentials, order_by,
                 headline, count_dims, breakdowns, derived=None):
        self.key = key
        self.label = label                # what the tab says
        self.grain = grain                # one row per …, in words
        self.view = view
        self.meta_tbl = meta_tbl          # APPROVED_AT / _LOADED_AT live here, not on the view
        self.all_cols = all_cols          # display order, derived columns included
        self.text_cols = text_cols
        self.sum_cols = sum_cols
        self.avg_cols = avg_cols
        self.pool_cols = pool_cols        # {col: (key tuple, 'sum' | 'avg')}
        self.pct_cols = pct_cols          # fractions: 0.489 is 48.9%
        self.dead_cols = dead_cols        # NULL on every row — the view selects a literal NULL
        # THE TICK-LISTS, IN FAMILIES. The page draws one picker per column under a heading per
        # family — what the run did, which store, which article — because nineteen pickers in one
        # undifferentiated grid is a wall nobody reads. filter_cols is DERIVED from the families
        # rather than kept beside them: two lists of the same columns is how a filter ends up in
        # the payload with no panel to tick it.
        self.filter_groups = filter_groups          # [(family title, [cols])], page order
        self.filter_cols = [c for _t, cs in filter_groups for c in cs]
        self.search_cols = search_cols
        self.essentials = essentials      # the "what happened and why" subset
        self.order_by = order_by          # default sort, the run's own order
        self.headline = headline          # {slot: column} for the summary strip
        self.count_dims = count_dims      # [(slot, label, column)] distinct counts
        self.breakdowns = breakdowns      # [(slot, title, note, column)] three cuts of the run
        self.derived = derived or {}      # {name: SQL expression}
        self.measures = sum_cols + avg_cols + list(pool_cols)
        self.known = set(all_cols)


# ══════════════════════════════════════════════════════════════════════════════
# derived-column SQL
# ══════════════════════════════════════════════════════════════════════════════
def _family_sql(col):
    """The RULE that stopped a row, with its parameters cut off.

    THE REASON IS AT THE END, NOT THE START — which the obvious rule gets wrong. A skipped row
    says its reason plainly: SEC_CAP_PRE_MJ_RNG_SEG(cap=130%,cont=34%). A PARTIAL row prepends the
    engine's own trace:  " B[RL.r1.rk1] ship=1 hold=0 msa=1->0; SKIP_MSA_EXHAUSTED(rem=0)"  — so
    cutting at the first bracket returns "B", which is 19,949 rows measured under a family that
    means nothing and SKIP_MSA_EXHAUSTED nowhere in the list. Taking the LAST ';' segment first
    puts those rows under the rule that actually stopped them (SKIP_PRI_BROKEN went 200,829 ->
    211,374, SKIP_MSA_EXHAUSTED appeared with 1,606) and still leaves 16 families.

    The REGEXP_LIKE is the safety net: anything that survives all that without looking like a
    reason token is a trace and nothing else, and says so rather than inventing a family from its
    first letter.
    """
    last = f"REGEXP_SUBSTR(RTRIM(NVL(\"{col}\",''),'; '),'[^;]*$')"
    f0 = f"TRIM(SPLIT_PART(SPLIT_PART({last},'(',1),'[',1))"
    return (f"IFF({f0}='', '', "
            f"IFF(REGEXP_LIKE({f0},'[A-Z][A-Z0-9_]{{2,}}.*'), {f0}, '(trace only)'))")


def _ineligible_family_sql(col):
    """gen_clr's C138_ALLOC_REASON, parameters stripped.

    Unlike the var_art reason this one states its rule FIRST and never carries a trace, and the
    INELIGIBLE_ rows list several rule codes in one string — those stay whole, because
    'INELIGIBLE_R04_MSA_POS;R05_REQ_POS;' is one outcome and splitting it would double-count the
    row. Only the (cap=…,cont=…) parameters come off: 282 raw strings -> 19 families, measured.
    """
    f0 = f"TRIM(SPLIT_PART(SPLIT_PART(NVL(\"{col}\",''),'(',1),'[',1))"
    return f"IFF({f0}='', '', {f0})"


# ══════════════════════════════════════════════════════════════════════════════
# var_art — store x variant article
# ══════════════════════════════════════════════════════════════════════════════
_VA_TEXT = ["C01_ST_CD", "C02_ST_STAT", "C03_ST_NM", "C04_RDC_CD", "C05_RDC_NM", "C06_SEG",
            "C07_DIV", "C08_SUB_DIV", "C09_MAJ_CAT", "C10_SSN", "C11_GEN_ART", "C12_GEN_DESC",
            "C13_VAR_ART", "C14_VAR_DESC", "C15_SZ", "C16_CLR", "C18_RNG_SEG",
            "C19_MERGE_RNG_SEG", "C20_FAB_MVGR_1", "C21_FAB_MVGR_2", "C22_ART_TYP_LISTING",
            "C23_ART_TYP_FINAL", "C50_ALLOC_WAVE", "C51_ALLOC_ROUND", "C52_ALLOC_STATUS",
            "C53_ALLOC_REMARKS", "C54_SKIP_REASON"]

_VA_SUM = ["C29_OPT_MBQ_VAR_ART", "C30_BGT_ST_TTL_STK_MBQ", "C32_STK_BFR_ALC", "C33_STK_AFT_ALC",
           "C35_SHORT_BEFORE", "C36_SHORT_AFTER", "C37_EXCESS_BEFORE", "C38_EXCESS_AFTER",
           "C39_BGT_PD_SALE_Q", "C40_ALC_Q", "C43_HOLD_Q"]

_VA_AVG = ["C17_MRP", "C25_PAK_SZ", "C26_MAX_I_ROD", "C27_ALC_I_ROD", "C31_CONT_PCT",
           "C41_FR_PCT_BEFORE", "C42_FR_PCT_AFTER"]

_VA_POOL = {
    "C28_OPT_MBQ_GEN_ART": (("C11_GEN_ART", "C01_ST_CD"), "sum"),
    "C45_EXTRA_MSA_QTY":   (("C13_VAR_ART", "C04_RDC_CD"), "sum"),
    "C46_OP_MSA_QTY":      (("C13_VAR_ART", "C04_RDC_CD"), "sum"),
}

VAR_ART = Level(
    key="var_art", label="Store × Variant Article",
    grain="one row per store × variant article",
    view="V2RETAIL.SILVER.V_ARS_ST_VAR_ART_GAP",
    meta_tbl="V2RETAIL.MIS_REPORTS.ARS_ST_VAR_ART",
    all_cols=["C01_ST_CD", "C02_ST_STAT", "C03_ST_NM", "C04_RDC_CD", "C05_RDC_NM", "C06_SEG",
              "C07_DIV", "C08_SUB_DIV", "C09_MAJ_CAT", "C10_SSN", "C11_GEN_ART", "C12_GEN_DESC",
              "C13_VAR_ART", "C14_VAR_DESC", "C15_SZ", "C16_CLR", "C17_MRP", "C18_RNG_SEG",
              "C19_MERGE_RNG_SEG", "C20_FAB_MVGR_1", "C21_FAB_MVGR_2", "C22_ART_TYP_LISTING",
              "C23_ART_TYP_FINAL", "C25_PAK_SZ", "C26_MAX_I_ROD", "C27_ALC_I_ROD",
              "C28_OPT_MBQ_GEN_ART", "C29_OPT_MBQ_VAR_ART", "C30_BGT_ST_TTL_STK_MBQ",
              "C31_CONT_PCT", "C32_STK_BFR_ALC", "C33_STK_AFT_ALC", "C35_SHORT_BEFORE",
              "C36_SHORT_AFTER", "C37_EXCESS_BEFORE", "C38_EXCESS_AFTER", "C39_BGT_PD_SALE_Q",
              "C40_ALC_Q", "C41_FR_PCT_BEFORE", "C42_FR_PCT_AFTER", "C43_HOLD_Q",
              "C45_EXTRA_MSA_QTY", "C46_OP_MSA_QTY", "C50_ALLOC_WAVE", "C51_ALLOC_ROUND",
              "C52_ALLOC_STATUS", "SKIP_FAMILY", "C53_ALLOC_REMARKS", "C54_SKIP_REASON"],
    text_cols=_VA_TEXT, sum_cols=_VA_SUM, avg_cols=_VA_AVG, pool_cols=_VA_POOL,
    pct_cols=["C31_CONT_PCT", "C41_FR_PCT_BEFORE", "C42_FR_PCT_AFTER"],
    dead_cols=["C19_MERGE_RNG_SEG", "C23_ART_TYP_FINAL"],
    # Only what is worth ticking: the raw skip reason has 5,641 values and is served by
    # SKIP_FAMILY plus the search box, and descriptions and article numbers are searched.
    filter_groups=[
        ("The run", ["C52_ALLOC_STATUS", "SKIP_FAMILY", "C50_ALLOC_WAVE", "C51_ALLOC_ROUND"]),
        ("Store", ["C04_RDC_CD", "C05_RDC_NM", "C02_ST_STAT", "C01_ST_CD"]),
        ("Article", ["C09_MAJ_CAT", "C06_SEG", "C07_DIV", "C08_SUB_DIV", "C10_SSN",
                     "C18_RNG_SEG", "C22_ART_TYP_LISTING", "C20_FAB_MVGR_1", "C21_FAB_MVGR_2",
                     "C15_SZ", "C16_CLR"]),
    ],
    search_cols=["C01_ST_CD", "C03_ST_NM", "C11_GEN_ART", "C13_VAR_ART", "C12_GEN_DESC",
                 "C14_VAR_DESC"],
    essentials=["C01_ST_CD", "C03_ST_NM", "C04_RDC_CD", "C09_MAJ_CAT", "C11_GEN_ART",
                "C13_VAR_ART", "C14_VAR_DESC", "C15_SZ", "C16_CLR", "C29_OPT_MBQ_VAR_ART",
                "C32_STK_BFR_ALC", "C33_STK_AFT_ALC", "C35_SHORT_BEFORE", "C36_SHORT_AFTER",
                "C40_ALC_Q", "C43_HOLD_Q", "C42_FR_PCT_AFTER", "C50_ALLOC_WAVE",
                "C52_ALLOC_STATUS", "SKIP_FAMILY"],
    order_by=["C01_ST_CD", "C13_VAR_ART"],
    headline={"mbq": "C30_BGT_ST_TTL_STK_MBQ", "stockBefore": "C32_STK_BFR_ALC",
              "stockAfter": "C33_STK_AFT_ALC", "shortBefore": "C35_SHORT_BEFORE",
              "shortAfter": "C36_SHORT_AFTER", "excessBefore": "C37_EXCESS_BEFORE",
              "excessAfter": "C38_EXCESS_AFTER", "allocated": "C40_ALC_Q", "hold": "C43_HOLD_Q"},
    count_dims=[("stores", "stores", "C01_ST_CD"), ("variants", "variants", "C13_VAR_ART")],
    breakdowns=[("byStatus", "By status", "ALLOCATED shipped in full, PARTIAL shipped some of it, "
                 "SKIPPED shipped nothing.", "C52_ALLOC_STATUS"),
                ("byWave", "By wave · round", "The pass the row was decided in. Rows with no wave "
                 "were never picked up.", "C50_ALLOC_WAVE"),
                ("bySkip", "Why it was skipped", "The skip reason with its parameters cut off — "
                 "16 rules rather than 5,641 strings.", "SKIP_FAMILY")],
    derived={"SKIP_FAMILY": _family_sql("C54_SKIP_REASON")},
)

# ══════════════════════════════════════════════════════════════════════════════
# gen_clr — store x maj-cat x gen article x colour
# ══════════════════════════════════════════════════════════════════════════════
# The five roll-up blocks. Each is the SAME thirteen figures computed at a coarser level and
# stapled onto every gen x colour row underneath it, so each is deduped on its own key. The FR %
# member of a block is a ratio and is averaged once per key rather than added.
_GC_BLOCKS = [
    (("C01_ST_CD", "C09_MAJ_CAT"),
     ["C62_BGT_DISP_Q", "C63_BGT_MBQ", "C64_MAJ_CAT_EXCESS", "C65_BEFORE_ALC_STK",
      "C66_AFTER_ALC_STK", "C67_ST_MJ_REQ", "C68_ALC_QTY", "C69_FR_PCT", "C70_AFTER_ALC_SH",
      "C71_AFTER_ALC_EX", "C72_ALC_EXCESS", "C73_REQ_REM_Q", "C74_BGT_OPT_CNT"], "C69_FR_PCT"),
    (("C01_ST_CD", "C09_MAJ_CAT", "C17_M_RNG_SEG"),
     ["C77_BGT_DISP_Q", "C78_BGT_MBQ", "C79_EXCESS", "C80_BEFORE_ALC_STK", "C81_AFTER_ALC_STK",
      "C82_REQ", "C83_ALC_Q", "C84_FR_PCT", "C85_AFTER_ALC_SH", "C86_AFTER_ALC_EX",
      "C87_STK_EXCESS", "C88_REQ_REM", "C89_BGT_OPT_CNT"], "C84_FR_PCT"),
    (("C01_ST_CD", "C09_MAJ_CAT", "C16_RNG_SEG"),
     ["C92_BGT_DISP_Q", "C93_BGT_MBQ", "C94_EXCESS", "C95_BEFORE_ALC_STK", "C96_AFTER_ALC_STK",
      "C97_REQ", "C98_ALC_Q", "C99_FR_PCT", "C100_AFTER_ALC_SH", "C101_AFTER_ALC_EX",
      "C102_STK_EXCESS", "C103_REQ_REM", "C104_BGT_OPT_CNT"], "C99_FR_PCT"),
    (("C01_ST_CD", "C09_MAJ_CAT", "C18_FAB_MVGR_1"),
     ["C107_BGT_DISP_Q", "C108_BGT_MBQ", "C109_EXCESS", "C110_BEFORE_ALC_STK",
      "C111_AFTER_ALC_STK", "C112_REQ", "C113_ALC_Q", "C114_FR_PCT", "C115_AFTER_ALC_SH",
      "C116_AFTER_ALC_EX", "C117_STK_EXCESS", "C118_REQ_REM", "C119_BGT_OPT_CNT"], "C114_FR_PCT"),
    (("C01_ST_CD", "C09_MAJ_CAT", "C20_FIT"),
     ["C122_BGT_DISP_Q", "C123_BGT_MBQ", "C124_EXCESS", "C125_BEFORE_ALC_STK",
      "C126_AFTER_ALC_STK", "C127_REQ", "C128_ALC_Q", "C129_FR_PCT", "C130_AFTER_ALC_SH",
      "C131_AFTER_ALC_EX", "C132_STK_EXCESS", "C133_REQ_REM", "C134_BGT_OPT_CNT"], "C129_FR_PCT"),
]

_GC_POOL = {}
for _keys, _cols, _ratio in _GC_BLOCKS:
    for _c in _cols:
        _GC_POOL[_c] = (_keys, "avg" if _c == _ratio else "sum")
# The DC's own figures, repeated onto every store row that could draw on them.
for _c in ("C55_EXTRA_MSA_QTY", "C56_OP_MSA_QTY"):
    _GC_POOL[_c] = (("C04_RDC", "C09_MAJ_CAT", "C12_GEN_ART", "C14_CLR"), "sum")
for _c in ("C50_ALC_TTL", "C53_FROM_HOLD_TTL", "C54_HOLD_TTL"):
    _GC_POOL[_c] = (("C04_RDC", "C12_GEN_ART", "C14_CLR"), "sum")

GEN_CLR = Level(
    key="gen_clr", label="Store × Gen Article × Colour",
    grain="one row per store × major category × gen article × colour",
    view="V2RETAIL.SILVER.V_ARS_ST_GEN_CLR_GAP",
    meta_tbl="V2RETAIL.MIS_REPORTS.ARS_ST_GEN_CLR",
    all_cols=["C01_ST_CD", "C02_ST_STAT", "C03_ST_NM", "C04_RDC", "C05_RDC_NM", "C06_SEG",
              "C07_DIV", "C08_SUB_DIV", "C09_MAJ_CAT", "C10_SIZE_APPLICABLE", "C11_SSN",
              "C12_GEN_ART", "C13_GEN_ART_DESC", "C14_CLR", "C15_MRP", "C16_RNG_SEG",
              "C17_M_RNG_SEG", "C18_FAB_MVGR_1", "C19_FAB_MVGR_2", "C20_FIT", "C21_ALC_GRID",
              "C22_ART_TYPE", "C23_ART_TYPE_FINAL", "C24_FIRST_AGING", "C27_ACC_DENSITY",
              "C28_ALC_DAYS", "C29_MAX_I_ROD", "C30_ALC_I_ROD", "C31_OPT_MBQ", "C32_OPT_MBQ_WH",
              "C33_SZ_MBQ_BU", "C34_HOLD_MBQ_BU", "C35_BEFORE_ST_TTL", "C36_AFTER_STK_TTL_Q",
              "C38_SHORT_BEFORE", "C39_SHORT_AFTER", "C40_VAR_SHORT_BU", "C41_EXCESS_BEFORE",
              "C42_EXCESS_AFTER", "C43_PD_SALE", "C44_OPT_REQ", "C45_OPT_REQ_WH",
              "C46_ALC_Q_MACHINE", "C47_STR_AFT_ALC", "C48_FILL_RATE_BEFORE",
              "C49_FILL_RATE_AFTER", "C50_ALC_TTL", "C51_HOLD_QTY", "C52_FROM_HOLD_QTY",
              "C53_FROM_HOLD_TTL", "C54_HOLD_TTL", "C55_EXTRA_MSA_QTY", "C56_OP_MSA_QTY",
              "C57_BGT_SZ_COUNT", "C59_SZ_COUNT_WITH_MSA", "C60_SZ_COUNT_AFTER_ALC",
              "C62_BGT_DISP_Q", "C63_BGT_MBQ", "C64_MAJ_CAT_EXCESS", "C65_BEFORE_ALC_STK",
              "C66_AFTER_ALC_STK", "C67_ST_MJ_REQ", "C68_ALC_QTY", "C69_FR_PCT",
              "C70_AFTER_ALC_SH", "C71_AFTER_ALC_EX", "C72_ALC_EXCESS", "C73_REQ_REM_Q",
              "C74_BGT_OPT_CNT", "C77_BGT_DISP_Q", "C78_BGT_MBQ", "C79_EXCESS",
              "C80_BEFORE_ALC_STK", "C81_AFTER_ALC_STK", "C82_REQ", "C83_ALC_Q", "C84_FR_PCT",
              "C85_AFTER_ALC_SH", "C86_AFTER_ALC_EX", "C87_STK_EXCESS", "C88_REQ_REM",
              "C89_BGT_OPT_CNT", "C91_BLANK", "C92_BGT_DISP_Q", "C93_BGT_MBQ", "C94_EXCESS",
              "C95_BEFORE_ALC_STK", "C96_AFTER_ALC_STK", "C97_REQ", "C98_ALC_Q", "C99_FR_PCT",
              "C100_AFTER_ALC_SH", "C101_AFTER_ALC_EX", "C102_STK_EXCESS", "C103_REQ_REM",
              "C104_BGT_OPT_CNT", "C106_BLANK", "C107_BGT_DISP_Q", "C108_BGT_MBQ", "C109_EXCESS",
              "C110_BEFORE_ALC_STK", "C111_AFTER_ALC_STK", "C112_REQ", "C113_ALC_Q",
              "C114_FR_PCT", "C115_AFTER_ALC_SH", "C116_AFTER_ALC_EX", "C117_STK_EXCESS",
              "C118_REQ_REM", "C119_BGT_OPT_CNT", "C121_BLANK", "C122_BGT_DISP_Q", "C123_BGT_MBQ",
              "C124_EXCESS", "C125_BEFORE_ALC_STK", "C126_AFTER_ALC_STK", "C127_REQ",
              "C128_ALC_Q", "C129_FR_PCT", "C130_AFTER_ALC_SH", "C131_AFTER_ALC_EX",
              "C132_STK_EXCESS", "C133_REQ_REM", "C134_BGT_OPT_CNT", "C136_BLANK", "SHIPPED",
              "ALLOC_FAMILY", "C137_LISTED_REASON", "C138_ALLOC_REASON", "C139_ALLOC_REMARKS",
              "C140_GAP_HOLD_Q", "C141_HOLD_Q_GAP", "C142_LISTING_GAP", "C143_EXCESS_ALC_GAP",
              "C144_ALC_Q_GAP", "C145_PARTIAL_ALC_DONE", "C147_CHECK"],
    text_cols=["C01_ST_CD", "C02_ST_STAT", "C03_ST_NM", "C04_RDC", "C05_RDC_NM", "C06_SEG",
               "C07_DIV", "C08_SUB_DIV", "C09_MAJ_CAT", "C10_SIZE_APPLICABLE", "C11_SSN",
               "C12_GEN_ART", "C13_GEN_ART_DESC", "C14_CLR", "C16_RNG_SEG", "C17_M_RNG_SEG",
               "C18_FAB_MVGR_1", "C19_FAB_MVGR_2", "C20_FIT", "C21_ALC_GRID", "C22_ART_TYPE",
               "C23_ART_TYPE_FINAL", "C24_FIRST_AGING", "C47_STR_AFT_ALC", "C91_BLANK",
               "C106_BLANK", "C121_BLANK", "C136_BLANK", "C137_LISTED_REASON",
               "C138_ALLOC_REASON", "C139_ALLOC_REMARKS", "C140_GAP_HOLD_Q", "C142_LISTING_GAP",
               "C143_EXCESS_ALC_GAP", "C144_ALC_Q_GAP", "C145_PARTIAL_ALC_DONE", "C147_CHECK"],
    sum_cols=["C31_OPT_MBQ", "C32_OPT_MBQ_WH", "C33_SZ_MBQ_BU", "C34_HOLD_MBQ_BU",
              "C35_BEFORE_ST_TTL", "C36_AFTER_STK_TTL_Q", "C38_SHORT_BEFORE", "C39_SHORT_AFTER",
              "C40_VAR_SHORT_BU", "C41_EXCESS_BEFORE", "C42_EXCESS_AFTER", "C43_PD_SALE",
              "C44_OPT_REQ", "C45_OPT_REQ_WH", "C46_ALC_Q_MACHINE", "C51_HOLD_QTY",
              "C52_FROM_HOLD_QTY", "C57_BGT_SZ_COUNT", "C59_SZ_COUNT_WITH_MSA",
              "C60_SZ_COUNT_AFTER_ALC", "C141_HOLD_Q_GAP"],
    avg_cols=["C15_MRP", "C27_ACC_DENSITY", "C28_ALC_DAYS", "C29_MAX_I_ROD", "C30_ALC_I_ROD",
              "C48_FILL_RATE_BEFORE", "C49_FILL_RATE_AFTER"],
    pool_cols=_GC_POOL,
    pct_cols=["C48_FILL_RATE_BEFORE", "C49_FILL_RATE_AFTER", "C69_FR_PCT", "C84_FR_PCT",
              "C99_FR_PCT", "C114_FR_PCT", "C129_FR_PCT"],
    dead_cols=["C23_ART_TYPE_FINAL", "C47_STR_AFT_ALC", "C91_BLANK", "C106_BLANK", "C121_BLANK",
               "C136_BLANK", "C140_GAP_HOLD_Q", "C142_LISTING_GAP", "C143_EXCESS_ALC_GAP",
               "C144_ALC_Q_GAP", "C145_PARTIAL_ALC_DONE", "C147_CHECK"],
    filter_groups=[
        ("The run", ["SHIPPED", "ALLOC_FAMILY", "C137_LISTED_REASON"]),
        ("Store", ["C04_RDC", "C05_RDC_NM", "C02_ST_STAT", "C01_ST_CD"]),
        ("Article", ["C09_MAJ_CAT", "C06_SEG", "C07_DIV", "C08_SUB_DIV", "C11_SSN",
                     "C10_SIZE_APPLICABLE", "C16_RNG_SEG", "C17_M_RNG_SEG", "C18_FAB_MVGR_1",
                     "C19_FAB_MVGR_2", "C20_FIT", "C21_ALC_GRID", "C22_ART_TYPE", "C14_CLR"]),
    ],
    search_cols=["C01_ST_CD", "C03_ST_NM", "C12_GEN_ART", "C13_GEN_ART_DESC", "C14_CLR",
                 "C09_MAJ_CAT"],
    essentials=["C01_ST_CD", "C03_ST_NM", "C04_RDC", "C09_MAJ_CAT", "C11_SSN", "C12_GEN_ART",
                "C13_GEN_ART_DESC", "C14_CLR", "C31_OPT_MBQ", "C35_BEFORE_ST_TTL",
                "C36_AFTER_STK_TTL_Q", "C38_SHORT_BEFORE", "C39_SHORT_AFTER",
                "C46_ALC_Q_MACHINE", "C49_FILL_RATE_AFTER", "C51_HOLD_QTY", "SHIPPED",
                "ALLOC_FAMILY", "C137_LISTED_REASON"],
    order_by=["C01_ST_CD", "C12_GEN_ART", "C14_CLR"],
    headline={"mbq": "C31_OPT_MBQ", "stockBefore": "C35_BEFORE_ST_TTL",
              "stockAfter": "C36_AFTER_STK_TTL_Q", "shortBefore": "C38_SHORT_BEFORE",
              "shortAfter": "C39_SHORT_AFTER", "excessBefore": "C41_EXCESS_BEFORE",
              "excessAfter": "C42_EXCESS_AFTER", "allocated": "C46_ALC_Q_MACHINE",
              "hold": "C51_HOLD_QTY"},
    count_dims=[("stores", "stores", "C01_ST_CD"), ("variants", "gen articles", "C12_GEN_ART")],
    breakdowns=[("byStatus", "Did it ship", "DERIVED: YES where the machine allocated anything. "
                 "Measured, that is exactly the set of rows with no allocation reason.", "SHIPPED"),
                ("bySkip", "Why it did not ship", "The allocation reason with its parameters cut "
                 "off — 19 families rather than 282 strings.", "ALLOC_FAMILY"),
                ("byWave", "Listing gates it passed", "Which eligibility rules the row cleared "
                 "before allocation was even considered.", "C137_LISTED_REASON")],
    derived={"ALLOC_FAMILY": _ineligible_family_sql("C138_ALLOC_REASON"),
             "SHIPPED": "IFF(COALESCE(\"C46_ALC_Q_MACHINE\",0) > 0, 'YES', 'NO')"},
)

# ══════════════════════════════════════════════════════════════════════════════
# maj_cat — store x major category
# ══════════════════════════════════════════════════════════════════════════════
_MJ_DEAD = ["C11_MJ_STAT", "C13_MRST", "C14_SCHLD_ST", "C15_ALC_DY", "C16_PICKING_DY",
            "C17_ALC_FREQ", "C20_ALC_GND", "C29_STK_0006", "C30_STK_0017", "C34_PRD_FRK",
            "C35_PRD_KOL", "C38_V06", "C47_DW01_PTL_V06_Q", "C49_DW01_PTL_V09_Q",
            "C78_OVER_AGING_STK_Q", "C85_CNT_RL", "C86_CNT_WRL", "C87_CNT_NL", "C88_CNT_MIX",
            "C89_TTL_ACT_OPT", "C91_RL_ALC_CNT", "C92_RL_ALC_QTY", "C93_RL_VAR_SHORT",
            "C94_NL_ALC_CNT", "C95_NL_ALC_QTY", "C96_WRL_ALC_QTY", "C97_MIX_ALC_CNT",
            "C98_UNLISTED_OPT", "C107_PROC_GRT_Q", "C109_AGING_0_90", "C110_AGING_91_180",
            "C111_AGING_181_360", "C112_AGING_360_UP"]

MAJ_CAT = Level(
    key="maj_cat", label="Store × Major Category",
    grain="one row per store × major category",
    view="V2RETAIL.SILVER.V_ARS_ST_MJ_GAP",
    meta_tbl="V2RETAIL.MIS_REPORTS.ARS_GRID_MJ",
    all_cols=["C01_WERKS", "C02_ST_STAT", "C03_STORE_NAME", "C04_RDC", "C05_RDC_NM", "C06_HUB",
              "C07_SEG", "C08_DIV", "C09_SUB_DIV", "C10_MAJ_CAT", "C11_MJ_STAT", "C12_SSN",
              "C13_MRST", "C14_SCHLD_ST", "C15_ALC_DY", "C16_PICKING_DY", "C17_ALC_FREQ",
              "C18_ST_CDMJ", "C20_ALC_GND", "C21_ACC_DENSITY", "C22_L7D", "C23_L7D_PD",
              "C24_PD_SALE_BGT_Q", "C26_STK_0001", "C27_STK_0002", "C28_STK_0004", "C29_STK_0006",
              "C30_STK_0017", "C31_INTRA", "C32_HUB_INT", "C33_HUB_PRD_Q", "C34_PRD_FRK",
              "C35_PRD_KOL", "C36_PICK_FRK", "C37_PICK_KOL", "C38_V06", "C39_V07",
              "C40_PND_DO_INCL_ALC", "C41_DH24_PTL_V06_Q", "C42_DH24_PTL_V07_Q",
              "C43_DH24_PTL_V09_Q", "C44_DH24_PTL_V18_Q", "C45_DH24_PTL_V10_Q",
              "C46_DH24_PTL_V25_Q", "C47_DW01_PTL_V06_Q", "C48_DW01_PTL_V07_Q",
              "C49_DW01_PTL_V09_Q", "C50_DW01_PTL_V18_Q", "C51_DW01_PTL_V25_Q", "C52_PND_DO_Q",
              "C53_DC_PEND_Q", "C54_TTL_ST_STK_AFT_ALC", "C56_BGT_DISP_Q", "C57_ALC_COVER_DAYS",
              "C58_DISP_GR", "C59_LW_SALE_GR", "C60_BGT_SALE_GR", "C61_MBQ_ST_TTL", "C62_STR",
              "C63_ART_EXCESS", "C64_ST_TTL_STK_BEFORE", "C65_ST_MJ_FR_BEFORE",
              "C66_ST_MJ_REQ_BEFORE", "C67_ALC_Q", "C68_ST_TTL_STK_AFT", "C69_OP_HOLD_Q",
              "C70_TD_ALC_FROM_HOLD", "C71_TD_HOLD_CONSUMED", "C72_TD_HOLD_ALC_Q",
              "C73_HOLD_ADDED_TODAY", "C74_REM_HOLD_MSA_Q", "C75_FILL_RATE_AFT",
              "C76_ST_STK_WO_ART_EXC", "C77_ST_STK_WO_EXC", "C78_OVER_AGING_STK_Q",
              "C79_SHORT_Q_AFT", "C80_EXCESS_Q_AFT", "C81_EXCESS_ALC_Q_110", "C83_PER_OPT_MBQ",
              "C84_BGT_OPT_CNT", "C85_CNT_RL", "C86_CNT_WRL", "C87_CNT_NL", "C88_CNT_MIX",
              "C89_TTL_ACT_OPT", "C90_SHORT_OPT", "C91_RL_ALC_CNT", "C92_RL_ALC_QTY",
              "C93_RL_VAR_SHORT", "C94_NL_ALC_CNT", "C95_NL_ALC_QTY", "C96_WRL_ALC_QTY",
              "C97_MIX_ALC_CNT", "C98_UNLISTED_OPT", "C100_OP_MSA_V02", "C101_OPT_COUNT",
              "C102_HOLD_MSA_Q", "C103_ALL_ST_HOLD_ALC_Q", "C104_REM_MSA_Q", "C105_OPT",
              "C106_HOLD_MSA_Q2", "C107_PROC_GRT_Q", "C109_AGING_0_90", "C110_AGING_91_180",
              "C111_AGING_181_360", "C112_AGING_360_UP"],
    text_cols=["C01_WERKS", "C02_ST_STAT", "C03_STORE_NAME", "C04_RDC", "C05_RDC_NM", "C06_HUB",
               "C07_SEG", "C08_DIV", "C09_SUB_DIV", "C10_MAJ_CAT", "C12_SSN",
               "C18_ST_CDMJ"] + _MJ_DEAD,
    sum_cols=["C22_L7D", "C23_L7D_PD", "C24_PD_SALE_BGT_Q", "C26_STK_0001", "C27_STK_0002",
              "C28_STK_0004", "C31_INTRA", "C32_HUB_INT", "C33_HUB_PRD_Q", "C36_PICK_FRK",
              "C37_PICK_KOL", "C39_V07", "C40_PND_DO_INCL_ALC", "C41_DH24_PTL_V06_Q",
              "C42_DH24_PTL_V07_Q", "C43_DH24_PTL_V09_Q", "C44_DH24_PTL_V18_Q",
              "C45_DH24_PTL_V10_Q", "C46_DH24_PTL_V25_Q", "C48_DW01_PTL_V07_Q",
              "C50_DW01_PTL_V18_Q", "C51_DW01_PTL_V25_Q", "C52_PND_DO_Q", "C53_DC_PEND_Q",
              "C54_TTL_ST_STK_AFT_ALC", "C56_BGT_DISP_Q", "C61_MBQ_ST_TTL", "C63_ART_EXCESS",
              "C64_ST_TTL_STK_BEFORE", "C66_ST_MJ_REQ_BEFORE", "C67_ALC_Q", "C68_ST_TTL_STK_AFT",
              "C69_OP_HOLD_Q", "C70_TD_ALC_FROM_HOLD", "C71_TD_HOLD_CONSUMED",
              "C72_TD_HOLD_ALC_Q", "C73_HOLD_ADDED_TODAY", "C74_REM_HOLD_MSA_Q",
              "C76_ST_STK_WO_ART_EXC", "C77_ST_STK_WO_EXC", "C79_SHORT_Q_AFT", "C80_EXCESS_Q_AFT",
              "C81_EXCESS_ALC_Q_110", "C84_BGT_OPT_CNT", "C90_SHORT_OPT"],
    avg_cols=["C21_ACC_DENSITY", "C57_ALC_COVER_DAYS", "C58_DISP_GR", "C59_LW_SALE_GR",
              "C60_BGT_SALE_GR", "C62_STR", "C65_ST_MJ_FR_BEFORE", "C75_FILL_RATE_AFT",
              "C83_PER_OPT_MBQ"],
    # The MSA pool and the hold balance for the whole RDC x category, repeated onto every store
    # row under it — three of them are SUM() OVER (PARTITION BY RDC, MAJ_CAT) inside the view.
    pool_cols={c: (("C04_RDC", "C10_MAJ_CAT"), "sum")
               for c in ["C100_OP_MSA_V02", "C101_OPT_COUNT", "C102_HOLD_MSA_Q",
                         "C103_ALL_ST_HOLD_ALC_Q", "C104_REM_MSA_Q", "C105_OPT",
                         "C106_HOLD_MSA_Q2"]},
    pct_cols=["C58_DISP_GR", "C59_LW_SALE_GR", "C60_BGT_SALE_GR", "C65_ST_MJ_FR_BEFORE",
              "C75_FILL_RATE_AFT"],
    dead_cols=_MJ_DEAD,
    filter_groups=[
        # No run family here: the category view carries no wave, round or status column.
        ("Store", ["C04_RDC", "C05_RDC_NM", "C06_HUB", "C02_ST_STAT", "C01_WERKS"]),
        ("Article", ["C07_SEG", "C08_DIV", "C09_SUB_DIV", "C10_MAJ_CAT", "C12_SSN"]),
    ],
    search_cols=["C01_WERKS", "C03_STORE_NAME", "C10_MAJ_CAT", "C18_ST_CDMJ"],
    essentials=["C01_WERKS", "C03_STORE_NAME", "C04_RDC", "C07_SEG", "C08_DIV", "C10_MAJ_CAT",
                "C12_SSN", "C24_PD_SALE_BGT_Q", "C61_MBQ_ST_TTL", "C64_ST_TTL_STK_BEFORE",
                "C66_ST_MJ_REQ_BEFORE", "C67_ALC_Q", "C68_ST_TTL_STK_AFT", "C75_FILL_RATE_AFT",
                "C79_SHORT_Q_AFT", "C80_EXCESS_Q_AFT", "C69_OP_HOLD_Q"],
    order_by=["C01_WERKS", "C10_MAJ_CAT"],
    headline={"mbq": "C61_MBQ_ST_TTL", "stockBefore": "C64_ST_TTL_STK_BEFORE",
              "stockAfter": "C68_ST_TTL_STK_AFT", "shortBefore": "C66_ST_MJ_REQ_BEFORE",
              "shortAfter": "C79_SHORT_Q_AFT", "excessBefore": None,
              "excessAfter": "C80_EXCESS_Q_AFT", "allocated": "C67_ALC_Q",
              "hold": "C69_OP_HOLD_Q"},
    count_dims=[("stores", "stores", "C01_WERKS"), ("variants", "categories", "C10_MAJ_CAT")],
    breakdowns=[("byStatus", "By RDC", "Which distribution centre served the row.", "C05_RDC_NM"),
                ("byWave", "By division", "Where the shortfall sits in the hierarchy.", "C08_DIV"),
                ("bySkip", "By segment", "APP and GM — the only two this view carries; the source "
                 "grid is filtered to them.", "C07_SEG")],
)

LEVELS = {lv.key: lv for lv in (VAR_ART, GEN_CLR, MAJ_CAT)}
DEFAULT_LEVEL = VAR_ART.key


def _lvl(key):
    """The level a request asked for. Absent or unknown-but-empty falls back to var_art, which is
    what this route served before there were three; a WRONG name is an error, not a silent
    fallback to a different report."""
    k = str(key or "").strip().lower()
    if not k:
        return LEVELS[DEFAULT_LEVEL]
    if k not in LEVELS:
        raise HTTPException(400, f"unknown level '{key}' — expected one of {', '.join(LEVELS)}")
    return LEVELS[k]


def _expr(c, lvl):
    """The SQL for a column name — a quoted identifier, or a derived column's expression."""
    if c in lvl.derived:
        return lvl.derived[c]
    if c not in lvl.known:
        raise HTTPException(400, f"unknown column '{c}' at level {lvl.key}")
    return f'"{c}"'


def _sel(c, lvl):
    return f'{_expr(c, lvl)} AS "{c}"' if c in lvl.derived else _expr(c, lvl)


# ══════════════════════════════════════════════════════════════════════════════
# params — the sessions, and the tick-list options for one of them
# ══════════════════════════════════════════════════════════════════════════════
# Options are a SELECT DISTINCT over millions of rows for up to 21 columns. They are also frozen:
# a session is a finished run, so its option lists cannot change unless it is reloaded. Cached per
# level x session, with ?refresh=1 for the reload case.
_OPTS_CACHE = {}
_OPTS_TTL = 6 * 3600


def _latest_session(cur, lvl):
    cur.execute(f"SELECT MAX(SESSION_ID) FROM {lvl.view}")
    r = cur.fetchone()
    if r and r[0]:
        return base._fmt_str(r[0])
    # A level whose only rows carry no SESSION_ID (the maj_cat legacy load, on its own) still has
    # a report to show — the picker's own token for those rows is the answer, not "no sessions".
    cur.execute(f"SELECT COUNT(*) FROM {lvl.view} WHERE SESSION_ID IS NULL")
    return NO_SESSION if int((cur.fetchone() or [0])[0] or 0) else None


def _sessions(cur, lvl):
    """Every session, newest first, with what it holds and when it was approved.

    APPROVED_AT and _LOADED_AT live only on the ARS source table; the views drop both. If that
    table moves, the list still comes back from the view — a picker with no timestamps beats no
    picker at all. A NULL SESSION_ID becomes the '(no session)' token rather than disappearing:
    maj_cat holds 219,756 such rows from an August load and they are still readable.
    """
    try:
        cur.execute(f"""SELECT SESSION_ID, COUNT(*), MAX(APPROVED_AT), MAX(_LOADED_AT)
                          FROM {lvl.meta_tbl} GROUP BY 1 ORDER BY 1 DESC NULLS LAST LIMIT 100""")
        rows = [(s, n, a, l) for s, n, a, l in cur.fetchall()]
    except Exception as e:                                    # noqa: BLE001 - see the docstring
        logger.warning("[sf-ars-alloc/%s] session metadata unavailable (%s); listing from the view",
                       lvl.key, e)
        cur.execute(f"SELECT SESSION_ID, COUNT(*) FROM {lvl.view} "
                    f"GROUP BY 1 ORDER BY 1 DESC NULLS LAST LIMIT 100")
        rows = [(s, n, None, None) for s, n in cur.fetchall()]
    return [{"session": base._fmt_str(s) or NO_SESSION, "rows": int(n or 0),
             "approvedAt": base._fmt_str(a), "loadedAt": base._fmt_str(l)}
            for s, n, a, l in rows]


def _options(cur, lvl, session):
    """Every tick-list for one session, in ONE pass.

    Twenty separate DISTINCTs would be twenty scans of millions of rows to populate twenty
    dropdowns. '(blank)' is a real choice, and means IS NULL / '' wherever it is sent back.
    """
    ck = (lvl.key, session)
    hit = _OPTS_CACHE.get(ck)
    if hit and time.time() - hit[0] < _OPTS_TTL:
        return hit[1]
    import json as _json
    sel = ", ".join(
        f"ARRAY_AGG(DISTINCT NVL(NULLIF(TRIM({_expr(c, lvl)}),''),'(blank)')) \"{c}\""
        for c in lvl.filter_cols)
    cur.execute(f"SELECT {sel} FROM {lvl.view} WHERE {_session_cond(session)}")
    names = [d[0] for d in cur.description]
    row = cur.fetchone() or []
    opts = {}
    for n, v in zip(names, row):
        vals = [x for x in (_json.loads(v) if v else []) if x is not None]
        opts[n] = sorted(vals)
    _OPTS_CACHE[ck] = (time.time(), opts)
    return opts


def _session_cond(ses):
    return "SESSION_ID IS NULL" if ses == NO_SESSION else f"SESSION_ID = {base._q(ses)}"


def _spec(lvl):
    """What a level IS, for the page: its columns and the rule each one follows.

    Sent with every params response rather than duplicated in the frontend, because a column list
    kept in two places is a column list that disagrees with itself the first time the view moves.
    """
    return {
        "key": lvl.key, "label": lvl.label, "grain": lvl.grain, "view": lvl.view,
        "columns": lvl.all_cols, "textCols": lvl.text_cols, "measures": lvl.measures,
        "pctCols": lvl.pct_cols, "sumCols": lvl.sum_cols, "avgCols": lvl.avg_cols,
        "poolCols": list(lvl.pool_cols), "deadCols": lvl.dead_cols,
        "dedupKeys": {c: list(k) for c, (k, _agg) in lvl.pool_cols.items()},
        "derivedCols": list(lvl.derived), "filterCols": lvl.filter_cols,
        "filterGroups": [{"title": t, "cols": cs} for t, cs in lvl.filter_groups],
        "searchCols": lvl.search_cols, "essentials": lvl.essentials,
        "headline": lvl.headline,
        "countDims": [{"slot": s, "label": lb, "col": c} for s, lb, c in lvl.count_dims],
        "breakdowns": [{"slot": s, "title": t, "note": n, "col": c}
                       for s, t, n, c in lvl.breakdowns],
    }


@router.get("/params")
def alloc_params(level: Optional[str] = None, session: Optional[str] = None, refresh: int = 0):
    lvl = _lvl(level)
    conn = base._conn()
    try:
        cur = conn.cursor()
        sessions = _sessions(cur, lvl)
        sel = (session or "").strip() or (sessions[0]["session"] if sessions else None)
        if not sel:
            return {"levels": [_spec(x) for x in LEVELS.values()], "sessions": [], "session": None,
                    "options": {}, **_spec(lvl)}
        if refresh:
            _OPTS_CACHE.pop((lvl.key, sel), None)
        opts = _options(cur, lvl, sel)
    finally:
        conn.close()
    # levels[] carries every grain's spec so the page can draw the tabs — and switch to one —
    # without a second round trip per tab.
    return {"levels": [_spec(x) for x in LEVELS.values()], "sessions": sessions, "session": sel,
            "options": opts, **_spec(lvl)}


# ══════════════════════════════════════════════════════════════════════════════
# the shared WHERE
# ══════════════════════════════════════════════════════════════════════════════
class AllocReq(BaseModel):
    level: Optional[str] = None          # absent = var_art, this route's original grain
    session: Optional[str] = None        # absent = the latest run loaded
    col_filters: Optional[dict] = None   # {COL: [values]}, '(blank)' meaning empty
    # The column-header conditions: [{col, op, v1, v2, vals}]. Any column, any operator the MIS
    # grid offers. col_filters stays beside it because a ticked list on a FILTER column has its own
    # cheap shape (and its own '(blank)' spelling); everything else arrives here.
    conds: Optional[list] = None
    search: Optional[str] = None
    page: int = 1
    limit: int = 200
    sort_col: Optional[str] = None
    sort_dir: Optional[str] = "asc"
    columns: Optional[list] = None       # projection; absent = every column


def _where(cur, req, lvl, guard=None):
    """The WHERE for one request, plus the session it resolved to.

    Everything on the page runs through this one function — grid, totals, summary and export —
    so a filter can never apply to the rows and not to the strip above them.
    """
    ses = (req.session or "").strip()
    if not ses:
        if guard:
            guard.track(cur)
        ses = _latest_session(cur, lvl)
    if not ses:
        raise HTTPException(404, f"no allocation sessions have been loaded at level {lvl.key}")
    conds = [_session_cond(ses)]

    for c, vals in (req.col_filters or {}).items():
        cu = str(c).strip().upper()
        if cu not in lvl.filter_cols:
            continue
        clean = [str(v) for v in (vals or []) if str(v).strip() != ""]
        if not clean:
            continue
        lits = [v for v in clean if v != "(blank)"]
        parts = []
        if lits:
            parts.append(f"TRIM({_expr(cu, lvl)}) IN ({', '.join(base._q(v) for v in lits)})")
        if "(blank)" in clean:
            parts.append(f"NVL(TRIM({_expr(cu, lvl)}),'') = ''")
        conds.append("(" + " OR ".join(parts) + ")")

    s = str(req.search or "").strip()
    if s:
        like = base._q("%" + s + "%")
        conds.append("(" + " OR ".join(f"UPPER({_expr(c, lvl)}) LIKE UPPER({like})"
                                       for c in lvl.search_cols) + ")")

    # The header menu's conditions. Compiled against THIS level's catalogue, so a condition left
    # over from another grain names a column that does not exist here and is dropped rather than
    # escaped — the same rule the MIS grid follows. The `ref` is _expr, which is what lets a
    # condition sit on a derived column (SKIP_FAMILY, SHIPPED, ALLOC_FAMILY) as if it were real.
    conds += _conds._col_where(lvl.known, req.conds or [], ref=lambda c: _expr(c, lvl))
    return " WHERE " + " AND ".join(conds), ses


def _projection(req, lvl):
    cols = [str(c).strip().upper() for c in (req.columns or [])] or list(lvl.all_cols)
    unknown = [c for c in cols if c not in lvl.known]
    if unknown:
        raise HTTPException(400, f"unknown column(s) at level {lvl.key}: {', '.join(unknown[:5])}")
    return cols


# ══════════════════════════════════════════════════════════════════════════════
# result
# ══════════════════════════════════════════════════════════════════════════════
def _result_body(req: AllocReq, guard):
    lvl = _lvl(req.level)
    page = max(1, int(req.page or 1))
    limit = max(1, min(int(req.limit or 200), MAX_LIMIT))
    conn = base._conn()
    try:
        cur = conn.cursor()
        where, ses = _where(cur, req, lvl, guard)
        cols = _projection(req, lvl)
        proj = ", ".join(_sel(c, lvl) for c in cols)

        direction = "DESC" if str(req.sort_dir or "").lower() == "desc" else "ASC"
        sc = str(req.sort_col or "").upper()
        # The default order is the one the run itself has: store, then article. NULLS LAST on a
        # chosen sort because in Snowflake a DESC sort puts NULLs first, which opens a measure
        # grid on the rows that have no measure.
        order = (f" ORDER BY {_expr(sc, lvl)} {direction} NULLS LAST" if sc in lvl.known
                 else " ORDER BY " + ", ".join(f'"{c}"' for c in lvl.order_by))

        guard.execute(cur, f"SELECT COUNT(*) FROM {lvl.view}{where}")
        total = int(cur.fetchone()[0] or 0)
        guard.execute(cur, f"SELECT {proj} FROM {lvl.view}{where}{order} "
                           f"LIMIT {limit} OFFSET {(page - 1) * limit}")
        out_cols = [c[0] for c in cur.description]
        rows = [[base._fmt_str(v) for v in r] for r in cur.fetchall()]
    finally:
        conn.close()
    return {"level": lvl.key, "session": ses, "columns": out_cols, "rows": rows, "total": total,
            "page": page, "limit": limit, "measures": lvl.measures, "pctCols": lvl.pct_cols}


@router.post("/result")
async def alloc_result(req: AllocReq, request: Request):
    """One page of the run. Async so an abandoned request takes its Snowflake query with it —
    this page re-fires on every filter, sort and page change; see app/sf_cancel.py."""
    return await sf_cancel.guarded(request, lambda g: _result_body(req, g), get_sf_read_connection,
                                   label="ars-alloc/result")


# ══════════════════════════════════════════════════════════════════════════════
# totals — over the WHOLE filtered result, not the visible page
# ══════════════════════════════════════════════════════════════════════════════
def _totals_body(req: AllocReq, guard):
    lvl = _lvl(req.level)
    conn = base._conn()
    try:
        cur = conn.cursor()
        where, ses = _where(cur, req, lvl, guard)
        # Only what the page is SHOWING is subtotalled. gen_clr carries 90 measures across five
        # roll-up blocks; totalling the 70 a reader has hidden costs a window sort each and buys
        # nothing. Absent projection still means everything, for a caller that wants it.
        want = set(_projection(req, lvl))
        sums = [c for c in lvl.sum_cols if c in want]
        avgs = [c for c in lvl.avg_cols if c in want]
        pools = [c for c in lvl.pool_cols if c in want]

        agg = ['COUNT(*) AS "_ROWS"']
        agg += [f'ROUND(SUM("{c}"),2) "{c}"' for c in sums]
        agg += [f'ROUND(AVG("{c}"),4) "{c}"' for c in avgs]

        if not pools:
            guard.execute(cur, f"SELECT {', '.join(agg)} FROM {lvl.view}{where}")
        else:
            # ONE pass for every pooled column, whatever its key. A pooled figure is repeated onto
            # every row under its key, so it is counted on ONE row per key: MAX over the key gives
            # the value, ROW_NUMBER over the same partition picks the row to count it on. Both
            # windows share the partition, so each key costs one sort and no extra table scan —
            # the alternative, a GROUP BY per key, is one scan of the filtered set each.
            keys = {}
            for c in pools:
                keys.setdefault(lvl.pool_cols[c][0], []).append(c)
            need = sorted(set(sums) | set(avgs) | set(pools)
                          | {k for ks in keys for k in ks})
            win, pick = [], []
            for i, (ks, cs) in enumerate(keys.items()):
                part = ", ".join(f'"{k}"' for k in ks)
                win.append(f'ROW_NUMBER() OVER (PARTITION BY {part} ORDER BY {part}) "_RN{i}"')
                for c in cs:
                    win.append(f'MAX("{c}") OVER (PARTITION BY {part}) "_MX_{c}"')
                    fn = "AVG" if lvl.pool_cols[c][1] == "avg" else "SUM"
                    dp = 4 if fn == "AVG" else 2
                    pick.append(f'ROUND({fn}(IFF("_RN{i}"=1, "_MX_{c}", NULL)),{dp}) "{c}"')
            guard.execute(cur, f"""
                WITH b AS (SELECT {', '.join(f'"{c}"' for c in need)} FROM {lvl.view}{where}),
                     r AS (SELECT b.*, {', '.join(win)} FROM b)
                SELECT {', '.join(agg + pick)} FROM r""")

        names = [d[0] for d in cur.description]
        vals = cur.fetchone() or []
        out = {n: base._f(v) for n, v in zip(names, vals)}
        rows = int(out.pop("_ROWS") or 0)
    finally:
        conn.close()

    rule = {c: "sum" for c in lvl.sum_cols}
    rule.update({c: "avg" for c in lvl.avg_cols})
    rule.update({c: ("dedup-avg" if a == "avg" else "dedup")
                 for c, (_k, a) in lvl.pool_cols.items()})
    return {"level": lvl.key, "session": ses, "rows": rows, "totals": out, "rule": rule,
            "dedupKeys": {c: list(k) for c, (k, _a) in lvl.pool_cols.items()}}


@router.post("/totals")
async def alloc_totals(req: AllocReq, request: Request):
    return await sf_cancel.guarded(request, lambda g: _totals_body(req, g), get_sf_read_connection,
                                   label="ars-alloc/totals")


# ══════════════════════════════════════════════════════════════════════════════
# summary — the headline, and the three breakdowns, in ONE scan
# ══════════════════════════════════════════════════════════════════════════════
def _summary_body(req: AllocReq, guard):
    lvl = _lvl(req.level)
    conn = base._conn()
    try:
        cur = conn.cursor()
        where, ses = _where(cur, req, lvl, guard)
        dims = [(slot, title, note, _expr(col, lvl)) for slot, title, note, col in lvl.breakdowns]
        # Numbered aliases, not the column names: two headline slots may point at one column
        # (and at maj_cat one slot points at none), and a repeated output name is a SQL error.
        slots = [(s, lvl.headline.get(s)) for s in
                 ("mbq", "stockBefore", "stockAfter", "shortBefore", "shortAfter", "excessBefore",
                  "excessAfter", "allocated", "hold")]
        meas = [(s, f'SUM("{c}")') for s, c in slots if c]
        cnts = [(s, f'COUNT(DISTINCT "{c}")') for s, _lb, c in lvl.count_dims]

        # GROUPING SETS, not four queries: the three cuts and the totals row are four reads of the
        # same millions of rows. One scan answers all of them, and on a warehouse that queues
        # (every app warehouse here is Small) three scans avoided is the difference between a
        # strip that lands with the grid and one that lands after it.
        gsel = ", ".join(f"GROUPING({e}) \"_G{i}\", {e} \"_D{i}\"" for i, (_s, _t, _n, e)
                         in enumerate(dims))
        msel = ", ".join(f'{e} "{s}"' for s, e in meas + cnts)
        sets = ", ".join(f"({e})" for _s, _t, _n, e in dims)
        guard.execute(cur, f"""
            SELECT {gsel}, COUNT(*) "rows", {msel}
              FROM {lvl.view}{where}
             GROUP BY GROUPING SETS ({sets}, ())""")
        names = [d[0] for d in cur.description]
        head, buckets = {}, {s: [] for s, _t, _n, _e in dims}
        for r in cur.fetchall():
            row = dict(zip(names, r))
            m = {"rows": int(row.get("rows") or 0)}
            for s, _e in meas:
                m[s] = base._f(row.get(s))
            for s, _e in cnts:
                m[s] = int(row.get(s) or 0)
            for i, (slot, _t, _n, _e) in enumerate(dims):
                if not row.get(f"_G{i}"):
                    key = base._fmt_str(row.get(f"_D{i}"))
                    buckets[slot].append({"key": key if key != "" else "(blank)", **m})
                    break
            else:
                head = m
    finally:
        conn.close()

    out = []
    for slot, title, note, _e in dims:
        rows = buckets[slot]
        rows.sort(key=lambda d: -d["rows"])
        out.append({"slot": slot, "title": title, "note": note, "rows": rows})
    # How much of the shortfall the run actually closed. Reported from the shortfall columns
    # rather than from what was allocated: a unit sent to a store that was already at MBQ closes
    # no gap, and this is the number the run is judged on.
    sb, sa = head.get("shortBefore") or 0, head.get("shortAfter") or 0
    head["gapClosedPct"] = None if not sb else round((sb - sa) / sb, 4)
    return {"level": lvl.key, "session": ses, "headline": head, "breakdowns": out}


@router.post("/summary")
async def alloc_summary(req: AllocReq, request: Request):
    return await sf_cancel.guarded(request, lambda g: _summary_body(req, g), get_sf_read_connection,
                                   label="ars-alloc/summary")


# ══════════════════════════════════════════════════════════════════════════════
# export
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/export")
def alloc_export(req: AllocReq):
    """Streamed CSV of exactly what the filters leave, with the RAW column names as headers.

    Raw names on purpose, as everywhere else in MIS: a header in a file is the column you can
    query and join against, and a friendly label is the thing that makes the screen and the file
    disagree. Streamed in batches, so a whole session (millions of rows) costs no more memory than
    a filtered slice — it only costs time.
    """
    lvl = _lvl(req.level)
    conn = base._conn()
    cur = conn.cursor()
    where, ses = _where(cur, req, lvl)
    cols = _projection(req, lvl)
    proj = ", ".join(_sel(c, lvl) for c in cols)
    order = ", ".join(f'"{c}"' for c in lvl.order_by)
    cur.execute(f"SELECT {proj} FROM {lvl.view}{where} ORDER BY {order}")
    hdr = [c[0] for c in cur.description]
    tag = "" if ses == NO_SESSION else f"_{ses}"
    return _stream_csv(cur, hdr, f"ARS_ALLOCATION_{lvl.key.upper()}{tag}.csv")


# ══════════════════════════════════════════════════════════════════════════════
# col-values — one column's own vocabulary, for the header menu's tick-list
# ══════════════════════════════════════════════════════════════════════════════
# The 21 tick-lists in the Filters panel come down with /params in one pass. This is for the other
# hundred columns, where building every list up front would be a hundred DISTINCTs over millions of
# rows to fill a menu nobody opened.
COL_VALUES_CAP = 1_000


class AllocColValuesReq(BaseModel):
    level: Optional[str] = None
    session: Optional[str] = None
    col: str


@router.post("/col-values")
def alloc_col_values(req: AllocColValuesReq):
    """The distinct values of ONE column in one session, capped.

    Scoped to the session and NOTHING else — deliberately not to the filters already applied. A
    list that hid the values you had just unticked would leave no way to tick them back on; the
    MIS grid's menu makes the same promise and this is the same menu.

    Capped at COL_VALUES_CAP because a description column has millions of distinct values and a
    tick-list is not how anyone would filter one — `truncated` says so, and Contains is the answer.
    """
    lvl = _lvl(req.level)
    col = str(req.col or "").strip().upper()
    if col not in lvl.known:
        raise HTTPException(400, f"unknown column '{req.col}' at level {lvl.key}")
    conn = base._conn()
    try:
        cur = conn.cursor()
        ses = (req.session or "").strip() or _latest_session(cur, lvl)
        if not ses:
            return {"values": [], "truncated": False}
        e = _expr(col, lvl)
        cur.execute(f"SELECT DISTINCT {e} FROM {lvl.view} WHERE {_session_cond(ses)} "
                    f"ORDER BY 1 NULLS LAST LIMIT {COL_VALUES_CAP + 1}")
        vals = [base._fmt_str(r[0]) for r in cur.fetchall()]
    finally:
        conn.close()
    return {"level": lvl.key, "session": ses, "col": col,
            "values": vals[:COL_VALUES_CAP], "truncated": len(vals) > COL_VALUES_CAP}


# ══════════════════════════════════════════════════════════════════════════════
# values — the distinct combinations that OCCUR, for a split export
# ══════════════════════════════════════════════════════════════════════════════
class AllocValuesReq(AllocReq):
    columns: Optional[list] = None


@router.post("/values")
def alloc_values(req: AllocValuesReq):
    """Which combinations of the split columns exist, and how big each is.

    A split is taken by re-running the export with that value as a col_filter, so a level has to
    be something _where() can filter on — hence the filter-column check rather than the full
    catalogue. The count comes back in the same group-by so the caller can refuse a slice it
    cannot deliver whole instead of silently truncating it.
    """
    lvl = _lvl(req.level)
    cols = [str(c).strip().upper() for c in (req.columns or [])]
    for c in cols:
        if c not in lvl.filter_cols:
            raise HTTPException(400, f"'{c}' cannot be split on at level {lvl.key}")
    if not cols:
        return {"combos": [], "counts": [], "total": 0}
    conn = base._conn()
    try:
        cur = conn.cursor()
        where, ses = _where(cur, req, lvl)
        proj = ", ".join(f"NVL(NULLIF(TRIM({_expr(c, lvl)}),''),'(blank)')" for c in cols)
        order = ", ".join(str(i + 1) for i in range(len(cols)))
        cur.execute(f"SELECT {proj}, COUNT(*) FROM {lvl.view}{where} "
                    f"GROUP BY {order} ORDER BY {order}")
        rows = cur.fetchall()
    finally:
        conn.close()
    return {"level": lvl.key, "session": ses, "columns": cols,
            "combos": [[base._fmt_str(v) for v in r[:-1]] for r in rows],
            "counts": [int(r[-1] or 0) for r in rows],
            "total": sum(int(r[-1] or 0) for r in rows)}

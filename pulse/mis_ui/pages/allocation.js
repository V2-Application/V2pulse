// Allocation (DFS /sf-mis-alloc, SfAllocReport.jsx + lib/allocColumnDocs.js + ColumnMenu.jsx): what one ARS allocation run
// decided, at three grains of the same run - ?level=var_art (store x variant article, default), gen_clr (store x gen art x
// colour) and maj_cat (store x major category). A SESSION picker instead of a date range (a run is one decision taken at one
// instant), search, the level's tick-list filters under their family headings (21 at variant level; one filter state shared
// with the grid header menus), "Where the gap is" (headline + three breakdowns over the WHOLE filtered set), the subtotal row
// with its four rules (sum / avg / pooled once-per-key / pooled avg), column docs on every header, the column set (hidden
// columns are never fetched; All / Hide empty / Essentials + named selections = grid saved views per level) and CSV export.
// API: /api/v1/mis/alloc/{params, result, totals, summary, export, col-values}.
(function () {
  "use strict";
  var M = window.MIS, el = M.el;
  var API = "/api/v1/mis/alloc";

  // ── column docs (DFS lib/allocColumnDocs.js, unchanged) ────────────────────────────────────────────────────────────────
  // The screen keeps the words, the file keeps the raw C-numbers (the export header is the column you can query the view
  // with); every header carries its raw name and the note people misread in its tooltip.
  function pretty(c) { return String(c || "").replace(/^C\d+_/, "").replace(/_/g, " "); }
  var GC_BLOCKS = [
    { lo: 62, hi: 74, tag: 'MJ', of: 'store × major category' },
    { lo: 77, hi: 89, tag: 'M-RNG', of: 'store × maj cat × MERGED range segment' },
    { lo: 92, hi: 104, tag: 'RNG', of: 'store × maj cat × range segment' },
    { lo: 107, hi: 119, tag: 'MVGR1', of: 'store × maj cat × fabric MVGR 1' },
    { lo: 122, hi: 134, tag: 'FIT', of: 'store × maj cat × fit' },
  ];
  
  
  var LABELS = {
    var_art: {
      C01_ST_CD: 'ST CD', C02_ST_STAT: 'ST STAT', C03_ST_NM: 'STORE', C04_RDC_CD: 'RDC',
      C05_RDC_NM: 'RDC NM', C06_SEG: 'SEG', C07_DIV: 'DIV', C08_SUB_DIV: 'SUB-DIV',
      C09_MAJ_CAT: 'MAJ CAT', C10_SSN: 'SSN', C11_GEN_ART: 'GEN ART', C12_GEN_DESC: 'GEN DESC',
      C13_VAR_ART: 'VAR ART', C14_VAR_DESC: 'VAR DESC', C15_SZ: 'SIZE', C16_CLR: 'COLOUR',
      C17_MRP: 'MRP', C18_RNG_SEG: 'RNG SEG', C19_MERGE_RNG_SEG: 'MERGE RNG SEG',
      C20_FAB_MVGR_1: 'MVGR 1', C21_FAB_MVGR_2: 'MVGR 2', C22_ART_TYP_LISTING: 'ART TYP · listing',
      C23_ART_TYP_FINAL: 'ART TYP · final', C25_PAK_SZ: 'PACK SZ', C26_MAX_I_ROD: 'MAX / ROD',
      C27_ALC_I_ROD: 'ALC / ROD', C28_OPT_MBQ_GEN_ART: 'MBQ gen art',
      C29_OPT_MBQ_VAR_ART: 'MBQ var art', C30_BGT_ST_TTL_STK_MBQ: 'BGT stock MBQ',
      C31_CONT_PCT: 'CONT %', C32_STK_BFR_ALC: 'Stk before', C33_STK_AFT_ALC: 'Stk after',
      C35_SHORT_BEFORE: 'Short before', C36_SHORT_AFTER: 'Short after',
      C37_EXCESS_BEFORE: 'Excess before', C38_EXCESS_AFTER: 'Excess after',
      C39_BGT_PD_SALE_Q: 'BGT sale Q', C40_ALC_Q: 'Allocated Q', C41_FR_PCT_BEFORE: 'FR % before',
      C42_FR_PCT_AFTER: 'FR % after', C43_HOLD_Q: 'Hold Q', C45_EXTRA_MSA_QTY: 'Extra MSA',
      C46_OP_MSA_QTY: 'Open MSA', C50_ALLOC_WAVE: 'Wave', C51_ALLOC_ROUND: 'Round',
      C52_ALLOC_STATUS: 'Status', SKIP_FAMILY: 'Skip family', C53_ALLOC_REMARKS: 'Remarks',
      C54_SKIP_REASON: 'Skip reason',
    },
    gen_clr: {
      C01_ST_CD: 'ST CD', C02_ST_STAT: 'ST STAT', C03_ST_NM: 'STORE', C04_RDC: 'RDC',
      C05_RDC_NM: 'RDC NM', C06_SEG: 'SEG', C07_DIV: 'DIV', C08_SUB_DIV: 'SUB-DIV',
      C09_MAJ_CAT: 'MAJ CAT', C10_SIZE_APPLICABLE: 'Size applicable', C11_SSN: 'SSN',
      C12_GEN_ART: 'GEN ART', C13_GEN_ART_DESC: 'GEN DESC', C14_CLR: 'COLOUR', C15_MRP: 'MRP',
      C16_RNG_SEG: 'RNG SEG', C17_M_RNG_SEG: 'MERGE RNG SEG', C18_FAB_MVGR_1: 'MVGR 1',
      C19_FAB_MVGR_2: 'MVGR 2', C20_FIT: 'FIT', C21_ALC_GRID: 'ALC grid', C22_ART_TYPE: 'ART TYPE',
      C23_ART_TYPE_FINAL: 'ART TYPE · final', C24_FIRST_AGING: 'First aging',
      C27_ACC_DENSITY: 'ACC density', C28_ALC_DAYS: 'ALC days', C29_MAX_I_ROD: 'MAX / ROD',
      C30_ALC_I_ROD: 'ALC / ROD', C31_OPT_MBQ: 'MBQ', C32_OPT_MBQ_WH: 'MBQ · WH',
      C33_SZ_MBQ_BU: 'Size MBQ BU', C34_HOLD_MBQ_BU: 'Hold MBQ BU', C35_BEFORE_ST_TTL: 'Stk before',
      C36_AFTER_STK_TTL_Q: 'Stk after', C38_SHORT_BEFORE: 'Short before',
      C39_SHORT_AFTER: 'Short after', C40_VAR_SHORT_BU: 'Var short BU',
      C41_EXCESS_BEFORE: 'Excess before', C42_EXCESS_AFTER: 'Excess after', C43_PD_SALE: 'PD sale',
      C44_OPT_REQ: 'Req', C45_OPT_REQ_WH: 'Req · WH', C46_ALC_Q_MACHINE: 'Allocated Q',
      C47_STR_AFT_ALC: 'STR after', C48_FILL_RATE_BEFORE: 'FR % before',
      C49_FILL_RATE_AFTER: 'FR % after', C50_ALC_TTL: 'DC allocated TTL', C51_HOLD_QTY: 'Hold Q',
      C52_FROM_HOLD_QTY: 'From hold Q', C53_FROM_HOLD_TTL: 'DC from-hold TTL',
      C54_HOLD_TTL: 'DC hold TTL', C55_EXTRA_MSA_QTY: 'Extra MSA', C56_OP_MSA_QTY: 'Open MSA',
      C57_BGT_SZ_COUNT: 'BGT size count', C59_SZ_COUNT_WITH_MSA: 'Size count · with MSA',
      C60_SZ_COUNT_AFTER_ALC: 'Size count · after',
      SHIPPED: 'Shipped', ALLOC_FAMILY: 'Reason family', C137_LISTED_REASON: 'Listing gates',
      C138_ALLOC_REASON: 'Allocation reason', C139_ALLOC_REMARKS: 'Remarks',
      C141_HOLD_Q_GAP: 'Hold Q gap',
    },
    maj_cat: {
      C01_WERKS: 'ST CD', C02_ST_STAT: 'ST STAT', C03_STORE_NAME: 'STORE', C04_RDC: 'RDC',
      C05_RDC_NM: 'RDC NM', C06_HUB: 'HUB', C07_SEG: 'SEG', C08_DIV: 'DIV', C09_SUB_DIV: 'SUB-DIV',
      C10_MAJ_CAT: 'MAJ CAT', C12_SSN: 'SSN', C18_ST_CDMJ: 'ST+MAJ key',
      C21_ACC_DENSITY: 'ACC density', C22_L7D: 'L7D sale', C23_L7D_PD: 'L7D per day',
      C24_PD_SALE_BGT_Q: 'BGT sale / day', C26_STK_0001: 'Stk 0001', C27_STK_0002: 'Stk 0002',
      C28_STK_0004: 'Stk 0004', C31_INTRA: 'Intra', C32_HUB_INT: 'Hub intra',
      C33_HUB_PRD_Q: 'Hub PRD Q', C36_PICK_FRK: 'Pick FRK', C37_PICK_KOL: 'Pick KOL',
      C39_V07: 'V07', C40_PND_DO_INCL_ALC: 'Pending DO (incl. this run)',
      C52_PND_DO_Q: 'Pending DO Q', C53_DC_PEND_Q: 'DC pending Q',
      C54_TTL_ST_STK_AFT_ALC: 'Store stock TTL (source)', C56_BGT_DISP_Q: 'BGT display Q',
      C57_ALC_COVER_DAYS: 'Cover days', C58_DISP_GR: 'Display GR', C59_LW_SALE_GR: 'LW sale GR',
      C60_BGT_SALE_GR: 'BGT sale GR', C61_MBQ_ST_TTL: 'MBQ', C62_STR: 'STR',
      C63_ART_EXCESS: 'Article excess', C64_ST_TTL_STK_BEFORE: 'Stk before',
      C65_ST_MJ_FR_BEFORE: 'FR % before', C66_ST_MJ_REQ_BEFORE: 'Short before',
      C67_ALC_Q: 'Allocated Q', C68_ST_TTL_STK_AFT: 'Stk after', C69_OP_HOLD_Q: 'Open hold Q',
      C70_TD_ALC_FROM_HOLD: 'Allocated from hold', C71_TD_HOLD_CONSUMED: 'Hold consumed',
      C72_TD_HOLD_ALC_Q: 'Hold allocated Q', C73_HOLD_ADDED_TODAY: 'Hold added',
      C74_REM_HOLD_MSA_Q: 'Hold remaining', C75_FILL_RATE_AFT: 'FR % after',
      C76_ST_STK_WO_ART_EXC: 'Stk w/o article excess', C77_ST_STK_WO_EXC: 'Stk capped at MBQ',
      C79_SHORT_Q_AFT: 'Short after', C80_EXCESS_Q_AFT: 'Excess after (>110%)',
      C81_EXCESS_ALC_Q_110: 'Excess shipped this run', C83_PER_OPT_MBQ: 'MBQ / option',
      C84_BGT_OPT_CNT: 'BGT options', C90_SHORT_OPT: 'Short options',
      C100_OP_MSA_V02: 'Open MSA', C101_OPT_COUNT: 'MSA options', C102_HOLD_MSA_Q: 'Hold MSA',
      C103_ALL_ST_HOLD_ALC_Q: 'Hold allocated (all stores)', C104_REM_MSA_Q: 'MSA remaining',
      C105_OPT: 'MSA options · close', C106_HOLD_MSA_Q2: 'Hold MSA · close',
    },
  };
  
  /* Only where the name is not the whole story. These are the columns people misread. */
  var NOTES = {
    var_art: {
      C28_OPT_MBQ_GEN_ART: 'The GEN ARTICLE MBQ for this store, repeated on each of its variant '
        + 'rows — so the subtotal counts it once per gen article × store, not once per row.',
      C29_OPT_MBQ_VAR_ART: "This variant's own MBQ at this store. Adds down the grid.",
      C30_BGT_ST_TTL_STK_MBQ: 'The budgeted total stock MBQ — the same figure as MBQ var art in this view.',
      C31_CONT_PCT: "This variant's share of its gen article at this store.",
      C35_SHORT_BEFORE: 'MBQ minus stock held before the run, floored at zero. The gap the run set out to close.',
      C36_SHORT_AFTER: 'The same gap after the run. On a SKIPPED row it equals Short before, because nothing moved.',
      C41_FR_PCT_BEFORE: 'WARNING — the source view computes this from stock AFTER allocation, so it is '
        + 'identical to FR % after on every row. Shown as the view defines it; the fix belongs in the view.',
      C42_FR_PCT_AFTER: 'Stock after allocation / MBQ.',
      C45_EXTRA_MSA_QTY: "The DC's extra MSA pool for this variant — one figure per variant × RDC, repeated "
        + 'onto every store row. Subtotalled once per variant × RDC, never summed down the grid.',
      C46_OP_MSA_QTY: "The DC's open MSA pool for this variant. Same rule as Extra MSA.",
      C19_MERGE_RNG_SEG: 'NULL on every row — the view selects a literal NULL here.',
      C23_ART_TYP_FINAL: 'NULL on every row — the view selects a literal NULL here.',
      C53_ALLOC_REMARKS: 'The engine’s own trace for the row: which bucket and rank it was picked in, '
        + 'what shipped and what was held.',
      C54_SKIP_REASON: 'The raw reason, parameters and all — 5,641 distinct values in one session. '
        + 'On a PARTIAL row the engine’s trace comes first and the reason last.',
      SKIP_FAMILY: 'DERIVED, not in the view: the rule that stopped the row, taken from the LAST part '
        + 'of the skip reason with its parameters cut off — 5,641 raw strings become 16 rules.',
      C06_SEG: 'Blank on 61% of rows — the ARS source table leaves it empty, evenly across all three statuses.',
    },
    gen_clr: {
      C31_OPT_MBQ: 'What this store should hold of this gen article × colour. Adds down the grid; '
        + 'everything called short or excess here is measured against it.',
      C32_OPT_MBQ_WH: 'The same MBQ computed on warehouse availability — a store figure, not a pool: '
        + 'it varies store by store and is summed like any other quantity.',
      C35_BEFORE_ST_TTL: 'Stock the store held before the run.',
      C36_AFTER_STK_TTL_Q: 'Stock after. Across the session the difference is exactly Allocated Q.',
      C38_SHORT_BEFORE: 'MBQ minus stock before, floored at zero. The gap the run set out to close.',
      C39_SHORT_AFTER: 'The same gap after the run. Where nothing shipped the two are equal.',
      C46_ALC_Q_MACHINE: 'WHAT ACTUALLY SHIPPED to this store for this gen article × colour. Not to '
        + 'be confused with DC allocated TTL, which is the whole DC’s figure repeated onto the row.',
      C48_FILL_RATE_BEFORE: 'Stock before / MBQ. Unlike the variant-level report, this one is '
        + 'computed from the BEFORE stock and is usable.',
      C49_FILL_RATE_AFTER: 'Stock after / MBQ.',
      C50_ALC_TTL: 'The DC’s TOTAL allocation for this gen article × colour, repeated onto every '
        + 'store row under it. Summed down the grid it reads 333,281,604 against an actual '
        + 'allocation of 1,563,266 — 200× — so it is subtotalled once per RDC × gen × colour.',
      C53_FROM_HOLD_TTL: 'DC total, repeated onto every store row. Counted once per RDC × gen × colour.',
      C54_HOLD_TTL: 'DC total, repeated onto every store row. Counted once per RDC × gen × colour.',
      C55_EXTRA_MSA_QTY: 'The DC’s extra MSA pool for this gen article × colour — one figure per '
        + 'RDC × maj cat × gen × colour, repeated onto every store row underneath it.',
      C56_OP_MSA_QTY: 'The DC’s open MSA pool. Same rule as Extra MSA.',
      C137_LISTED_REASON: 'Which eligibility gates the row passed BEFORE allocation was considered — '
        + 'R01 listing, R04 MSA positive, R06 priority, R09 headroom. 14 combinations in one session.',
      C138_ALLOC_REASON: 'The raw allocation reason, parameters and all — 282 distinct values. An '
        + 'INELIGIBLE_ row never reached allocation; a SEC_CAP_ row reached it and was capped.',
      ALLOC_FAMILY: 'DERIVED, not in the view: the allocation reason with its (cap=…,cont=…) '
        + 'parameters cut off — 282 raw strings become 19 families you can tick.',
      SHIPPED: 'DERIVED, not in the view: YES where the machine allocated anything. Measured on one '
        + 'session it is exactly the set of rows with no allocation reason — 166,082 of 4,913,163.',
      C23_ART_TYPE_FINAL: 'Empty on every row — the view carries the column and nothing in it.',
      C140_GAP_HOLD_Q: "The literal string 'NA' on every row.",
      C142_LISTING_GAP: "The literal string 'NA' on every row.",
      C144_ALC_Q_GAP: "The literal string 'NA' on every row.",
      C141_HOLD_Q_GAP: 'Zero on every row — the view selects a literal 0.',
    },
    maj_cat: {
      C61_MBQ_ST_TTL: 'DERIVED IN THE VIEW: budgeted display Q + budgeted sale/day × cover days. '
        + 'This is the MBQ every short and excess figure on the row is measured against.',
      C64_ST_TTL_STK_BEFORE: 'Stock TTL minus what this run allocated — the state the run started '
        + 'from, reconstructed, because the source grid stores the AFTER figure.',
      C66_ST_MJ_REQ_BEFORE: 'MBQ minus stock before, floored at zero. The gap the run set out to close.',
      C67_ALC_Q: 'What this run allocated to the store for this category. Across the session it '
        + 'matches the variant-level report exactly.',
      C68_ST_TTL_STK_AFT: 'Stock before + allocated.',
      C65_ST_MJ_FR_BEFORE: 'Stock before / MBQ. Above 100% means the store was already over its MBQ.',
      C75_FILL_RATE_AFT: 'Stock after / MBQ.',
      C79_SHORT_Q_AFT: 'What is still missing once the run has finished.',
      C80_EXCESS_Q_AFT: 'Stock after, above 110% of MBQ. The 10% is the view’s own tolerance band.',
      C81_EXCESS_ALC_Q_110: 'How much of that excess THIS run shipped — the part of the overstock the '
        + 'run is answerable for.',
      C63_ART_EXCESS: 'Stock sitting in articles the store should not be carrying at all.',
      C100_OP_MSA_V02: 'The DC’s MSA pool for this category, repeated onto every store row under it. '
        + 'Subtotalled once per RDC × maj cat, never summed down the grid.',
      C102_HOLD_MSA_Q: 'A SUM OVER the whole RDC × category inside the view itself — every store row '
        + 'carries the same total. Counted once per RDC × maj cat.',
      C103_ALL_ST_HOLD_ALC_Q: 'Likewise a view-level SUM over RDC × category, repeated onto each row.',
      C106_HOLD_MSA_Q2: 'Likewise. The closing balance of the same pool.',
      C53_DC_PEND_Q: 'Everything already on its way to the store: pending DO plus each PTL bucket.',
      C22_L7D: 'Last 7 days’ sale quantity. C23 is the same figure per day.',
      C07_SEG: 'APP or GM only — the source grid is filtered to those two, so this view has no others.',
    },
  };
  
  // The help text under the title, per grain. What the reader is looking at and what to watch for.
  var LEVEL_HELP = {
    var_art: {
      blurb: 'One row per store × variant article — the finest grain the run publishes, and the only '
        + 'one that carries an allocation status and a skip reason per row.',
      points: [
        ['Short before → allocated → short after is the whole story of a row.', 'Short before is MBQ '
          + 'minus the stock the store already had; short after is what is still missing once the run '
          + 'has finished. On a SKIPPED row the two are equal, because nothing moved — that difference '
          + 'is what “gap closed” measures.'],
        ['Four rows in five shipped nothing.', 'Measured on one session: 2,113,404 SKIPPED against '
          + '362,406 ALLOCATED and 54,725 PARTIAL. The skipped rows carry the shortfall the run '
          + 'declined to fill, which is why the strip leads with the reasons rather than the shipments.'],
        ['FR % before is not usable.', 'The source view computes it from stock AFTER allocation — the '
          + 'identical expression to FR % after — so the two are equal on every row. It is shown as '
          + 'the view defines it rather than quietly corrected here; the fix belongs in the view.'],
        ['Skip family is derived.', 'The rule that stopped the row with its parameters cut off: 5,641 '
          + 'raw strings become 16 rules you can filter on. It is read from the END of the reason, '
          + 'because a PARTIAL row states the engine’s trace first and the rule last.'],
      ],
    },
    gen_clr: {
      blurb: 'One row per store × major category × gen article × colour — the level the allocation '
        + 'decision is actually taken at, before it is split across sizes.',
      points: [
        ['Allocated Q is C46, not the TTL columns.', 'C46 is what shipped to THIS store: it sums to '
          + '1,563,266 on one session, exactly the stock the run added. DC allocated TTL is the whole '
          + 'DC’s figure stamped onto every store row and sums to 333,281,604 if you let it.'],
        ['Five roll-up blocks ride along on every row.', 'The store × maj-cat balance and then the '
          + 'same thirteen figures per merged range segment, range segment, MVGR 1 and fit. Each is '
          + 'constant within its own key, so each is subtotalled once per key — never added down the '
          + 'grid, where one store’s category MBQ would be counted once per colour beneath it.'],
        ['Why it did not ship is in two columns.', 'The listing gates say which eligibility rules the '
          + 'row passed before allocation was considered; the reason family says what stopped it '
          + 'afterwards. Rows with neither are the rows that shipped.'],
        ['The key repeats on 3,990 rows.', 'Out of 4,913,163 — the ARS source splits a handful of '
          + 'store × category × article × colour keys by season. A store total double-counts those.'],
      ],
    },
    maj_cat: {
      blurb: 'One row per store × major category — the balance sheet the run works from: what the '
        + 'category should hold, what it held, what moved and what is still missing.',
      points: [
        ['No status, no skip reason — this view has neither.', 'It is the category-level balance, not '
          + 'a decision log. What it answers is where the shortfall sits: by RDC, by division, by '
          + 'category, per store.'],
        ['MBQ here is computed, not stored.', 'Budgeted display Q + budgeted sale per day × cover '
          + 'days. Stock before is likewise reconstructed, by taking this run’s allocation back off '
          + 'the stored after-figure.'],
        ['Excess is measured with a 10% tolerance.', 'Excess after counts only stock above 110% of '
          + 'MBQ, and Excess shipped this run is the part of that overstock the run itself sent.'],
        ['463 stores, against 434 at the article levels.', 'The category grid carries stores that got '
          + 'no article rows at all. Same run, wider store list — the difference is not a data error.'],
        ['A third of the columns are empty by construction.', 'Thirty-three of the 106 are literal '
          + 'NULLs in the view. They are hidden until you ask for them.'],
      ],
    },
  };

  function gcBlock(c) {
    var m = String(c).match(/^C(\d+)_/), n = m ? Number(m[1]) : NaN;
    return GC_BLOCKS.filter(function (b) { return n >= b.lo && n <= b.hi; })[0] || null;
  }
  function allocLabel(level, c) {
    var hit = (LABELS[level] || {})[c];
    if (hit) return hit;
    if (level === "gen_clr") { var b = gcBlock(c); if (b) return b.tag + " · " + pretty(c); }
    return pretty(c);
  }
  function allocNote(level, c) {
    var hit = (NOTES[level] || {})[c];
    if (hit) return hit;
    if (level === "gen_clr") {
      var b = gcBlock(c);
      if (b) return "Part of the " + b.tag + " roll-up block: this is a " + b.of + " figure, the same on every gen " +
        "article × colour row under that key. Subtotalled once per key, never summed down the grid.";
    }
    return "";
  }

  // ── page constants (SfAllocReport.jsx) ───────────────────────────────────────────────────────────────────────────────
  // The headline in reading order: the gap, what closed it, what is left. Slots a level does not have are skipped.
  var HEAD_ORDER = [["shortBefore", "Short before"], ["allocated", "Allocated", "up"], ["hold", "Held back"],
                    ["shortAfter", "Short after", "down"], ["excessAfter", "Excess after"], ["mbq", "MBQ"]];
  var STATUS_CHIP = { ALLOCATED: "ok", YES: "ok", PARTIAL: "warn", SKIPPED: "grey", NO: "grey" };
  var CHIP_COLS = { C52_ALLOC_STATUS: 1, SHIPPED: 1 };            // drawn as a chip: whichever column says what happened
  var RULE_MARK = { sum: "Σ", avg: "x̄", dedup: "¹", "dedup-avg": "¹x̄" };
  var BIG_EXPORT = 500000;
  var GENERAL_HELP = [
    ["Subtotals follow four rules, and every header says which.", "Quantities are summed (Σ). Percentages and article attributes are " +
      "averaged (x̄) - adding a percentage across millions of rows means nothing. Pooled columns (¹) are a DC or roll-up figure repeated " +
      "onto every row beneath it, so they are counted once per key: summed down the grid, one measured session inflated the variant MSA " +
      "pool 38-fold and the gen × colour DC allocation 200-fold. A pooled percentage (¹x̄) is averaged per key."],
    ["Every column header filters and sorts,", "the way a spreadsheet does: tick its values, or use contains / begins with / between / " +
      "is blank. The chips above the grid are the full list of filters, and the tick-lists in the Filters panel are the same filters."],
    ["Blank cells are blank in the source", "- a dash means no value, never a measured zero. Columns that are empty on every row start " +
      "hidden and are marked in the column set."],
    ["Percentages", "are shown as percent; a condition typed on a percentage column is typed in percent too (85 means 85%)."]
  ];

  function set(a) { var o = {}; (a || []).forEach(function (x) { o[x] = true; }); return o; }
  function enc(s) { return encodeURIComponent(s); }
  function nFmt(v) { return v === null || v === undefined || v === "" ? "—" : M.fmt(v, "dec"); }
  function pFmt(v) { return v === null || v === undefined || v === "" ? "—" : M.fmt(Number(v) * 100, "dec") + "%"; }

  M.page("allocation", {
    render: function (ctx) {
      var me = ctx.me || { can: {} };
      var level = M.qs("level") || "var_art";
      var st = { meta: null, session: M.qs("session") || "", search: M.qs("q") || "", hidden: {}, res: null,
                 summary: null, totals: null, stripKey: "", stripSeq: 0, stripCtl: null, stripBusy: false, cache: {}, valCache: {}, showStrip: true };
      var help = LEVEL_HELP[level] || LEVEL_HELP.var_art;

      M.setHeader({ title: "Allocation", crumbs: [{ label: "Allocation", href: "/mis/allocation" }],
                    sub: "What the ARS run decided. Pick a session - a run is a single decision taken at one instant, so there is no date range " +
                         "here and every number belongs to that one run - and a grain. " + help.blurb,
                    tiles: [{ k: "Short before", skel: true }, { k: "Allocated", skel: true }, { k: "Short after", skel: true }, { k: "Gap closed", skel: true }, { k: "Rows", skel: true }] });

      function params(lv, ses, refresh) {
        return M.get(API + "/params?level=" + enc(lv) + (ses ? "&session=" + enc(ses) : "") + (refresh ? "&refresh=1" : ""));
      }
      return params(level, st.session).then(function (p) {
        if (!ctx.alive()) return null;
        // A session the level does not have (maj_cat carries one the article views do not) falls back to its newest.
        var list = p.sessions || [];
        if (st.session && list.length && !list.some(function (s) { return s.session === st.session; })) {
          st.session = list[0].session; return params(p.key || level, st.session);
        }
        return p;
      }).then(function (p) { if (p && ctx.alive()) return build(p); });

      function build(p) {
        st.meta = p; level = p.key || level; st.session = p.session || "";
        help = LEVEL_HELP[level] || LEVEL_HELP.var_art;
        var allCols = p.columns || [], measures = set(p.measures), pcts = set(p.pctCols), filterCols = set(p.filterCols), dead = set(p.deadCols);
        var sumCols = set(p.sumCols), avgCols = set(p.avgCols), poolCols = set(p.poolCols);
        function L(c) { return allocLabel(level, c); }
        function N(c) { return allocNote(level, c); }

        // The column set this person last had showing, per grain (DFS kept it server side per user; here per browser). First
        // visit: the columns that are NULL or a constant on every row start hidden.
        var saved = M.store.get("alloc.hidden." + level, null);
        st.hidden = set(Array.isArray(saved) ? saved.filter(function (c) { return allCols.indexOf(c) >= 0; }) : (p.deadCols || []));
        function visibleCols() { return allCols.filter(function (c) { return !st.hidden[c]; }); }
        function projection() { return Object.keys(st.hidden).length ? visibleCols() : null; }
        function setVisible(cols) {
          var want = set(cols); st.hidden = {};
          allCols.forEach(function (c) { if (!want[c]) st.hidden[c] = true; });
          M.store.set("alloc.hidden." + level, Object.keys(st.hidden)); paintColCount();
        }
        function syncUrl() {
          var q = new URLSearchParams(); q.set("level", level); if (st.session) q.set("session", st.session); if (st.search) q.set("q", st.search);
          history.replaceState({}, "", "/mis/allocation?" + q.toString());
        }
        syncUrl();

        // ── controls card: grain, session, search, filters, column set, help ─────────────────────────────────────────
        var levelSeg = el("div", { "class": "seg", role: "group", "aria-label": "Grain" }, (p.levels || []).map(function (lv) {
          return el("button", { type: "button", "aria-pressed": String(lv.key === level), title: lv.grain + " — " + lv.view, text: lv.label,
                                onclick: function () { switchLevel(lv.key); } });
        }));
        var sessions = p.sessions || [];
        var sesSel = el("select", { "aria-label": "Allocation session", style: { minWidth: "320px", fontFamily: "Consolas, ui-monospace, monospace", fontSize: "12.5px" } },
          sessions.map(function (s) {
            return el("option", { value: s.session, selected: s.session === st.session,
              text: s.session + (s.approvedAt ? " · approved " + String(s.approvedAt).slice(0, 16).replace("T", " ") : "") + " · " + M.fmt(s.rows, "int") + " rows" });
          }));
        sesSel.addEventListener("change", function () { changeSession(sesSel.value); });
        var search = el("input", { type: "search", value: st.search, style: { minWidth: "260px" },
                                   placeholder: (p.searchCols || []).map(L).join(", ") || "Search", title: "Searches " + (p.searchCols || []).join(", "),
                                   "aria-label": "Search" });
        search.addEventListener("input", M.debounce(function () { st.search = search.value.trim(); syncUrl(); if (grid) grid.reload(true); }, 450));
        var filCnt = el("span", { "class": "cnt hidden" }), colCnt = el("span", { "class": "muted small" });
        var filBtn = el("button", { type: "button", "class": "btn", "aria-expanded": "false" }, [M.icon("filter", 15), "Filters", filCnt]);
        var colBtn = el("button", { type: "button", "class": "btn", title: "Which columns are fetched, subtotalled and exported" }, [M.icon("cols", 15), "Column set", colCnt]);
        var helpBtn = el("button", { type: "button", "class": "btn ghost", "aria-expanded": "false" }, [M.icon("info", 15), "How to read these numbers"]);
        filCnt.style.cssText = "background:var(--brand-2);color:#fff;border-radius:999px;font-size:11px;padding:0 6px;font-weight:600";

        var ctrl = el("div", { "class": "card" });
        ctrl.appendChild(el("div", { "class": "fbar" }, [el("label", { "class": "f" }, ["Grain", levelSeg]),
          el("label", { "class": "f" }, ["Allocation session", sesSel]), el("label", { "class": "f" }, ["Search", search]),
          el("span", { "class": "spacer" }), filBtn, colBtn, helpBtn]));

        // Tick-list filters under the family headings the level sends (what the run did, which store, which article).
        var fpanel = el("div", { "class": "stack hidden", style: { padding: "12px 18px", borderBottom: "1px solid var(--line-2)" } });
        var pickers = {}, famCounts = [];
        var fams = (p.filterGroups && p.filterGroups.length) ? p.filterGroups : [{ title: "Filters", cols: p.filterCols || [] }];
        fams.forEach(function (f) {
          var cols = f.cols || []; if (!cols.length) return;
          var cnt = el("span", { "class": "chip hidden", style: { textTransform: "none", letterSpacing: 0 } });
          fpanel.appendChild(el("div", { "class": "section-title" }, [el("span", { text: f.title }), cnt]));
          var row = el("div", { "class": "row", style: { alignItems: "flex-end" } });
          cols.forEach(function (c) {
            var ms = M.multiSelect(L(c), function () { return Promise.resolve(((st.meta.options || {})[c] || []).slice()); }, [],
                                   function (vals) { setFilter(c, vals); });
            ms.el.title = c + (N(c) ? " — " + N(c) : "");
            pickers[c] = ms; row.appendChild(ms.el);
          });
          famCounts.push({ cols: cols, cnt: cnt });
          fpanel.appendChild(row);
        });
        var reread = el("button", { type: "button", "class": "btn ghost sm", title: "The lists are cached for six hours per session" }, [M.icon("refresh", 14), "Re-read the lists"]);
        reread.addEventListener("click", function () {
          M.action(reread, "Reading…", function () { return params(level, st.session, true).then(function (p2) { st.meta.options = p2.options || {}; resetPickers(); }); }, "Filter lists re-read.");
        });
        fpanel.appendChild(el("div", { "class": "row muted small" }, ["Each list is this run's own values; '(blank)' means empty. The same filters are on every column header menu.",
          el("span", { "class": "spacer" }), reread]));
        ctrl.appendChild(fpanel);
        filBtn.addEventListener("click", function () { var o = fpanel.classList.toggle("hidden"); filBtn.setAttribute("aria-expanded", String(!o)); });
        ctx.main.appendChild(ctrl);

        // How to read these numbers (LEVEL_HELP + the page-wide rules)
        var helpCard = el("div", { "class": "card hidden" }, [el("div", { "class": "card-h" }, [el("h2", { text: "How to read these numbers" }), el("span", { "class": "note", text: help.blurb })]),
          el("div", { "class": "card-b stack small" }, help.points.concat(GENERAL_HELP).map(function (pt) {
            var warn = /^FR % before/.test(pt[0]);
            return el("p", { style: { margin: 0 }, "class": warn ? "notice warn" : null }, [el("b", { text: pt[0] }), " " + pt[1]]);
          }))]);
        helpBtn.addEventListener("click", function () { var o = helpCard.classList.toggle("hidden"); helpBtn.setAttribute("aria-expanded", String(!o)); });
        ctx.main.appendChild(helpCard);

        if (!sessions.length || !st.session) {
          M.setHeader({ tiles: [] });
          ctx.main.appendChild(el("div", { "class": "notice warn" }, [M.icon("info", 16), el("span", { text: "No allocation sessions have been loaded at this grain (" + (p.label || level) + ")." })]));
          return;
        }

        // ── "Where the gap is": the headline and the three cuts, over the WHOLE filtered set ─────────────────────────
        var stripNote = el("span", { "class": "note" }), stripBody = el("div", { "class": "card-b" });
        var stripTog = el("button", { type: "button", "class": "btn ghost sm", "aria-expanded": "true", text: "Hide" });
        stripTog.addEventListener("click", function () { st.showStrip = !st.showStrip; stripBody.classList.toggle("hidden", !st.showStrip);
          stripTog.textContent = st.showStrip ? "Hide" : "Show"; stripTog.setAttribute("aria-expanded", String(st.showStrip)); });
        ctx.main.appendChild(el("div", { "class": "card" }, [el("div", { "class": "card-h" }, [el("h2", { text: "Where the gap is" }), stripNote,
          el("span", { "class": "spacer" }), stripTog]), stripBody]));

        // ── the grid ─────────────────────────────────────────────────────────────────────────────────────────────────
        var gridMeta = el("span", { "class": "note" });
        var gcard = el("div", { "class": "card" }, [el("div", { "class": "card-h" }, [el("h2", { text: p.label || "Rows" }), gridMeta]),
          el("div", { "class": "fchips", style: { borderBottom: "1px solid var(--line-2)" } }, [el("span", { "class": "muted small", text: "Subtotal row = the whole filtered result, not the page:" }),
            el("span", { "class": "chip grey", text: "Σ summed" }), el("span", { "class": "chip grey", text: "x̄ averaged" }),
            el("span", { "class": "chip grey", text: "¹ counted once per key (pooled)" }), el("span", { "class": "chip grey", text: "¹x̄ averaged once per key" }),
            el("span", { "class": "muted small", text: "Hover a header for its raw name, its note and its rule." })])]);
        ctx.main.appendChild(gcard);

        function pv(c, v) {   // a percentage condition is typed in percent; the view stores a fraction
          if (!pcts[c] || v === undefined || v === null || v === "") return v;
          var n = Number(String(v).replace(/,/g, "")); return isFinite(n) ? String(n / 100) : v;
        }
        // ONE filter state (the grid's), split by shape at the last moment: a ticked list on one of the level's filter
        // columns travels as col_filters (cheap, and the only path that spells '(blank)'); everything else as conds.
        function scopeOf(gs) {
          var cf = {}, cl = [];
          Object.keys(gs.filters || {}).forEach(function (c) {
            var v = gs.filters[c]; if (!v || !v.length) return;
            if (filterCols[c]) cf[c] = v.slice(); else cl.push({ col: c, op: "in", vals: v.slice() });
          });
          (gs.conds || []).forEach(function (x) {
            var o = { col: x.col, op: x.op };
            if (x.op !== "blank" && x.op !== "nonblank") { o.v1 = pv(x.col, x.v1); if (x.op === "between") o.v2 = pv(x.col, x.v2); }
            cl.push(o);
          });
          return { level: level, session: st.session || null, col_filters: cf, conds: cl, search: st.search || null };
        }
        function scaleRow(cols, r) { return r.map(function (v, i) { return pcts[cols[i]] && v !== "" && v !== null && isFinite(Number(v)) ? Number(v) * 100 : v; }); }

        function fetchRows(gs, signal) {
          var scope = scopeOf(gs), proj = projection();
          var skey = JSON.stringify([scope, proj]), rkey = JSON.stringify([skey, gs.page, gs.limit, gs.sort_col, gs.sort_dir]);
          if (skey !== st.stripKey) loadStrip(scope, proj, skey);
          if (st.cache.key === rkey && st.cache.data) return Promise.resolve(withTotals(st.cache.data, skey));
          return M.post(API + "/result", Object.assign({}, scope, { page: gs.page, limit: gs.limit, sort_col: gs.sort_col || null,
            sort_dir: gs.sort_dir || "asc", columns: proj }), { signal: signal }).then(function (d) {
            var cols = d.columns || [];
            var out = { columns: cols, rows: (d.rows || []).map(function (r) { return scaleRow(cols, r); }), total: d.total };
            st.res = d; st.cache = { key: rkey, skey: skey, data: out };
            return withTotals(out, skey);
          });
        }
        function withTotals(out, skey) {
          var o = { columns: out.columns, rows: out.rows, total: out.total, totals: null };
          if (st.totals && st.totals.key === skey && st.totals.data && st.totals.data.totals) {
            var t = {}, src = st.totals.data.totals;
            out.columns.forEach(function (c) { var v = src[c]; t[c] = pcts[c] && v !== null && v !== undefined ? Number(v) * 100 : v; });
            var first = out.columns.filter(function (c) { return !st.hidden[c]; })[0];
            if (first && !measures[first]) t[first] = "Total · " + M.fmt(st.totals.data.rows, "int") + " rows";
            o.totals = t;
          }
          return o;
        }

        // The strip and the subtotals load AFTER the rows, on their own requests (aggregates over millions of rows must not
        // hold the grid). When the totals land, the grid redraws from its cached page with the subtotal row.
        function loadStrip(scope, proj, skey) {
          st.stripKey = skey; var my = ++st.stripSeq; st.summary = null; st.totals = null; st.stripBusy = true;
          if (st.stripCtl) st.stripCtl.abort();
          var ac = window.AbortController ? new AbortController() : null; st.stripCtl = ac;
          paintStrip(); paintTiles();
          var errs = [];
          function soft(pr) { return pr.then(null, function (e) { if (e && e.name !== "AbortError") errs.push(e.message); return null; }); }
          Promise.all([soft(M.post(API + "/summary", scope, { signal: ac && ac.signal })),
                       soft(M.post(API + "/totals", Object.assign({}, scope, { columns: proj }), { signal: ac && ac.signal }))]).then(function (r) {
            if (my !== st.stripSeq || !ctx.alive()) return;
            st.stripBusy = false; st.summary = r[0]; st.totals = r[1] ? { key: skey, data: r[1] } : null;
            if (errs.length) M.toast("Summary: " + errs[0], "bad");
            paintStrip(); paintTiles();
            if (r[1] && st.cache.skey === skey) grid.reload();
          });
        }

        function paintTiles() {
          var hc = p.headline || {};
          if (st.stripBusy || !st.summary) {
            M.setHeader({ tiles: HEAD_ORDER.filter(function (h) { return hc[h[0]]; }).slice(0, 4).map(function (h) { return { k: h[1], skel: true }; })
              .concat([{ k: "Gap closed", skel: true }, { k: "Rows", skel: true }]) });
            return;
          }
          var h = st.summary.headline || {}, tiles = [];
          HEAD_ORDER.forEach(function (x) {
            if (!hc[x[0]]) return;
            tiles.push({ k: x[1], v: M.fmt(h[x[0]], "int"), title: x[1] + " — " + hc[x[0]] + " (" + nFmt(h[x[0]]) + ")",
                         s: x[0] === "allocated" && h.shortBefore ? "against " + M.compact(h.shortBefore) + " short" : hc[x[0]], cls: x[2] });
          });
          if (hc.stockBefore && hc.stockAfter) tiles.push({ k: "Stock before → after", v: M.compact(h.stockBefore) + " → " + M.compact(h.stockAfter),
            title: hc.stockBefore + " → " + hc.stockAfter + ": " + nFmt(h.stockBefore) + " → " + nFmt(h.stockAfter) });
          tiles.push({ k: "Gap closed", v: pFmt(h.gapClosedPct), s: "of the shortfall this run closed",
                       title: "Short before minus short after, over short before - how much of the shortfall this run actually closed." });
          tiles.push({ k: "Rows", v: M.fmt(h.rows, "int"), s: (p.countDims || []).map(function (d) { return M.fmt(h[d.slot], "int") + " " + d.label; }).join(" · ") });
          M.setHeader({ tiles: tiles });
        }

        function paintStrip() {
          M.clear(stripNote); M.clear(stripBody);
          if (st.stripBusy) {
            stripNote.appendChild(el("span", { "class": "spin" }));
            stripBody.appendChild(el("div", { "class": "grid12" }, [0, 1, 2].map(function () { return el("div", { "class": "c4 skel", style: { height: "120px" } }); })));
            return;
          }
          if (!st.summary) { stripBody.appendChild(el("div", { "class": "muted small", text: "The summary could not be loaded - the grid below is unaffected." })); return; }
          var h = st.summary.headline || {};
          stripNote.textContent = M.fmt(h.rows, "int") + " rows" + (p.countDims || []).map(function (d) { return " · " + M.fmt(h[d.slot], "int") + " " + d.label; }).join("") +
            " · gap closed " + pFmt(h.gapClosedPct);
          var g = el("div", { "class": "grid12" });
          (st.summary.breakdowns || []).forEach(function (b) { g.appendChild(breakdown(b)); });
          stripBody.appendChild(g);
        }

        // One breakdown table: rows (with a bar for the row COUNT behind the key), short before, allocated, short after.
        function breakdown(b) {
          var rows = b.rows || [], limit = rows.length > 14 ? 10 : 8, all = false;
          var max = Math.max.apply(null, [1].concat(rows.map(function (r) { return r.rows || 0; })));
          var box = el("div", { "class": "c6", style: { minWidth: 0, border: "1px solid var(--line)", borderRadius: "10px", padding: "10px 12px" } });
          var th = "padding:3px 6px;text-align:right;font-weight:600;color:var(--muted);font-size:11.5px";
          var td = "padding:3px 6px;text-align:right;font-variant-numeric:tabular-nums;border-top:1px solid var(--line-2)";
          function draw() {
            M.clear(box);
            box.appendChild(el("div", { "class": "section-title", title: b.note || "" }, [el("span", { text: b.title })]));
            if (b.note) box.appendChild(el("div", { "class": "muted tiny", style: { margin: "2px 0 6px" }, text: b.note }));
            var tb = el("tbody");
            (all ? rows : rows.slice(0, limit)).forEach(function (r) {
              var w = Math.round(100 * (r.rows || 0) / max);
              tb.appendChild(el("tr", {}, [
                el("td", { style: { position: "relative", padding: "3px 6px", borderTop: "1px solid var(--line-2)" } }, [
                  el("span", { "aria-hidden": "true", style: { position: "absolute", left: "0", top: "3px", bottom: "3px", width: w + "%", background: "var(--brand-soft)", borderRadius: "3px" } }),
                  el("span", { title: r.key, style: { position: "relative", display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }, text: r.key })]),
                el("td", { style: td, text: nFmt(r.rows) }), el("td", { style: td, text: nFmt(r.shortBefore) }),
                el("td", { style: td + ";color:var(--ok)", text: nFmt(r.allocated) }), el("td", { style: td + ";color:var(--bad)", text: nFmt(r.shortAfter) })]));
            });
            if (!rows.length) tb.appendChild(el("tr", {}, el("td", { colspan: "5", "class": "muted", style: "padding:8px 6px", text: "Nothing in this cut." })));
            box.appendChild(el("div", { style: { overflowX: "auto" } }, el("table", { style: { width: "100%", minWidth: "400px", borderCollapse: "collapse", tableLayout: "fixed", fontSize: "12.5px" } }, [
              el("thead", {}, el("tr", {}, [el("th", { style: "width:38%;padding:3px 6px" }), el("th", { style: th, text: "rows" }), el("th", { style: th, text: "short bef." }),
                el("th", { style: th, text: "alloc." }), el("th", { style: th, text: "short aft." })])), tb])));
            if (rows.length > limit) box.appendChild(el("button", { type: "button", "class": "btn ghost sm", style: { marginTop: "4px" },
              text: all ? "Show fewer" : "Show all " + rows.length, onclick: function () { all = !all; draw(); } }));
          }
          draw(); return box;
        }

        function ruleOf(c) {
          var r = st.totals && st.totals.data && st.totals.data.rule ? st.totals.data.rule[c] : null;
          return r || (poolCols[c] ? "dedup" : avgCols[c] ? "avg" : sumCols[c] ? "sum" : null);
        }
        function ruleText(c) {
          var rule = ruleOf(c); if (!rule || !measures[c]) return "";
          var keys = (st.totals && st.totals.data && st.totals.data.dedupKeys && st.totals.data.dedupKeys[c]) || (p.dedupKeys || {})[c];
          if (rule === "avg") return "x̄ Subtotal: average across the filtered rows - " + L(c) + " is a ratio or an attribute, and summing it would mean nothing.";
          if (rule === "dedup" || rule === "dedup-avg") return RULE_MARK[rule] + " Subtotal: " + (rule === "dedup-avg" ? "averaged" : "counted") + " ONCE per " +
            (keys ? keys.map(L).join(" × ") : "key") + " - this is a pooled figure repeated onto every row beneath that key, so summing it down the grid multiplies it.";
          return "Σ Subtotal: sum across the filtered rows.";
        }
        function docTitle(c) {
          var n = N(c), r = ruleText(c);
          return L(c) + "\n" + c + (n ? "\n\n" + n : "") + (r ? "\n\n" + r : "") + (dead[c] ? "\n\nEmpty (or one constant) on every row of this view." : "");
        }
        // Column docs on the headers and the rule on each subtotal cell, re-applied after every grid draw.
        function decorate() {
          if (!grid) return;
          var cols = grid.columns();
          [].forEach.call(grid.el.querySelectorAll("thead tr.cols th"), function (th, i) {
            var c = cols[i]; if (!c || th.getAttribute("data-doc") === c) return;
            th.setAttribute("data-doc", c); var t = docTitle(c); th.title = t;
            var lb = th.querySelector(".lbl"); if (lb) lb.title = t;
            if (c === "C41_FR_PCT_BEFORE" && lb) lb.parentNode.insertBefore(el("span", { "class": "badge", style: { background: "var(--warn-soft)", color: "var(--warn)" },
              text: "!", "aria-label": "Warning: see the column note" }), lb);
          });
          [].forEach.call(grid.el.querySelectorAll("tfoot td"), function (td, i) { var c = cols[i]; if (c && measures[c]) td.title = ruleText(c); });
        }

        function cell(td, c, v) {
          var blank = v === "" || v === null || v === undefined;
          if (CHIP_COLS[c]) { M.clear(td); td.appendChild(el("span", { "class": "chip " + (STATUS_CHIP[v] || "grey"), text: blank ? "—" : String(v) })); return; }
          if (blank) { td.textContent = "—"; td.classList.add("muted"); }
        }

        function loadValues(c) {
          var packed = (st.meta.options || {})[c];
          if (packed) return Promise.resolve(packed);
          var key = level + "|" + st.session + "|" + c;
          if (st.valCache[key]) return Promise.resolve(st.valCache[key]);
          return M.post(API + "/col-values", { level: level, session: st.session || null, col: c }).then(function (d) {
            if (d.truncated) M.toast(L(c) + " has more than " + M.fmt((d.values || []).length, "int") + " distinct values - the list is cut off. Use a condition (contains) for the rest.");
            st.valCache[key] = (d.values || []).filter(function (x) { return x !== "" && x !== null; });
            return st.valCache[key];
          });
        }

        function setFilter(c, vals) {
          var s = grid.state();
          if (vals && vals.length) s.filters[c] = vals; else delete s.filters[c];
          grid.setState({ filters: s.filters }); grid.reload(true);
        }
        function resetPickers() { Object.keys(pickers).forEach(function (c) { pickers[c].reset(); }); }
        function syncFilters(gs) {
          var on = {}; Object.keys(gs.filters || {}).forEach(function (c) { if ((gs.filters[c] || []).length) on[c] = 1; });
          (gs.conds || []).forEach(function (x) { on[x.col] = 1; });
          Object.keys(pickers).forEach(function (c) { pickers[c].set(gs.filters[c] || []); });
          famCounts.forEach(function (f) { var n = f.cols.filter(function (c) { return on[c]; }).length; f.cnt.textContent = n + " filtered"; f.cnt.classList.toggle("hidden", !n); });
          var n = Object.keys(on).length; filCnt.textContent = n; filCnt.classList.toggle("hidden", !n);
          var filtered = n > 0 || !!st.search;
          var ses = sessions.filter(function (s) { return s.session === st.session; })[0] || {};
          gridMeta.textContent = (st.res ? M.fmt(st.res.total, "int") + " row(s)" + (filtered ? " after filters" : "") : "") +
            " · session " + st.session + (ses.loadedAt ? " · loaded " + M.fmtDate(ses.loadedAt) : "") + (ses.approvedAt ? " · approved " + M.fmtDate(ses.approvedAt) : "");
          if (expBtn) { expBtn.lastChild.textContent = filtered ? "Export CSV · filtered" : "Export CSV";
            expBtn.title = (filtered ? "Carries the filters and search this page is showing. " : "") + "Raw C-numbered headers, so the file is queryable against the view."; }
        }
        function paintColCount() { var n = visibleCols().length; colCnt.textContent = n === allCols.length ? "all " + n : n + " of " + allCols.length; }
        paintColCount();

        // Column set: hidden columns are never fetched, subtotalled or exported. Named selections = grid saved views.
        colBtn.addEventListener("click", function () {
          var cl = M.checklist([{ label: p.label + " · " + allCols.length + " columns", items: allCols.map(function (c) {
            return { value: c, label: L(c) + "   ·  " + c.split("_")[0] + (dead[c] ? "   (empty in the view)" : "") };
          }) }], visibleCols(), { height: "380px", prettify: false, placeholder: "Find a column" });
          var bAll = el("button", { type: "button", "class": "btn sm", text: "All " + allCols.length, onclick: function () { cl.set(allCols); } });
          var bLive = el("button", { type: "button", "class": "btn sm", text: "Hide empty", title: "Every column that carries a value - the ones that are NULL or a constant on every row stay hidden",
                                     onclick: function () { cl.set(allCols.filter(function (c) { return !dead[c]; })); } });
          var bEss = el("button", { type: "button", "class": "btn sm", text: "Essentials", title: "Store, article, MBQ, stock, the gap, what shipped and why not",
                                    onclick: function () { cl.set((p.essentials || []).filter(function (c) { return allCols.indexOf(c) >= 0; })); } });
          var ok = el("button", { type: "button", "class": "btn primary", text: "Show these columns" });
          var m = M.modal("Columns at " + (p.label || level), [
            el("p", { "class": "muted small", style: { margin: 0 }, text: "Hidden columns are never fetched, subtotalled or exported. This set is kept for you on this grain and " +
              "reopens with whatever is showing. To keep several named selections (an allocation check, the MSA pool, a skip audit), use Views on the grid - " +
              "a saved view carries this column set with the filters and sort." }),
            el("div", { "class": "row" }, [el("span", { "class": "muted small", text: "Built-in:" }), bAll, bLive, bEss]), cl.el], [ok]);
          ok.addEventListener("click", function () {
            var v = cl.get(); if (!v.length) { M.toast("Tick at least one column.", "bad"); return; }
            setVisible(allCols.filter(function (c) { return v.indexOf(c) >= 0; })); m.close(); grid.reload(true);
          });
        });

        function doExport(gs) {
          if (!me.can.export) { M.toast("You do not have MIS export access.", "bad"); return Promise.resolve(); }
          var n = st.res ? st.res.total : 0;
          var go = n > BIG_EXPORT ? M.confirm("Large export", M.fmt(n, "int") + " rows will be written. That is a large file and takes a few minutes - " +
            "filter first if you only need part of the run. Continue?", "Export") : Promise.resolve(true);
          return go.then(function (yes) {
            if (!yes) return;
            M.toast("Building CSV - " + M.fmt(n, "int") + " row(s)…");
            var tag = String(st.session || "").replace(/[^\w]+/g, "_");
            return M.download("POST", API + "/export", Object.assign({}, scopeOf(gs), { columns: projection() }), "ARS_ALLOCATION_" + level.toUpperCase() + "_" + tag + ".csv")
              .then(function (nm) { M.toast("Downloaded " + nm, "ok"); });
          });
        }
        var expBtn = me.can.export ? el("button", { type: "button", "class": "btn primary" }, [M.icon("down", 15), el("span", { text: "Export CSV" })]) : null;
        if (expBtn) expBtn.addEventListener("click", function () { M.action(expBtn, "Exporting…", function () { return doExport(grid.state()); }).catch(function () { /* toasted */ }); });
        M.setHeader({ actions: [expBtn] });

        var grid = M.Grid(gcard, {
          id: "allocation-" + level, title: "", limit: 200, pageSizes: [100, 200, 500, 1000],
          isKey: function (c) { return !measures[c]; },
          format: function (c) { return pcts[c] ? "pct" : measures[c] ? undefined : "text"; },
          label: L, fetch: fetchRows, values: loadValues, cell: cell,
          getExtra: function () { return { cols: visibleCols() }; },
          setExtra: function (x) {
            if (!x || !Array.isArray(x.cols)) return;
            var want = x.cols.filter(function (c) { return allCols.indexOf(c) >= 0; });
            if (!want.length) { M.toast("This view names no column this grain carries - the column set is unchanged.", "bad"); return; }
            setVisible(want);
            if (want.length < x.cols.length) M.toast((x.cols.length - want.length) + " column(s) of this view are no longer in the grain.");
          },
          onData: function (d, gs) { syncFilters(gs); },
          exports: me.can.export ? [{ label: "CSV - the whole filtered selection (raw column names)", fn: function (gs) { doExport(gs).catch(function (e) { M.toast(e.message, "bad"); }); } }] : []
        });
        var mo = new MutationObserver(function () { if (!ctx.alive()) { mo.disconnect(); return; } decorate(); });
        mo.observe(grid.el, { childList: true, subtree: true });

        function switchLevel(k) {
          if (k === level) return;
          // Filters, sort and the column set are named in the OLD level's vocabulary; the session and search survive.
          var q = new URLSearchParams(); q.set("level", k); if (st.session) q.set("session", st.session); if (st.search) q.set("q", st.search);
          M.go("/mis/allocation?" + q.toString(), true);
        }
        function changeSession(s) {
          st.session = s; st.valCache = {}; st.cache = {}; syncUrl();
          // The tick-lists are the run's own values, so a new session re-reads them; the filters stay.
          params(level, s).then(function (p2) {
            if (!ctx.alive()) return;
            st.meta.options = p2.options || {}; resetPickers(); grid.reload(true);
          }, function (e) { M.toast(e.message, "bad"); });
        }

        return grid.applyDefaultView().then(function () { if (ctx.alive()) return grid.reload(true); });
      }
    }
  });
})();

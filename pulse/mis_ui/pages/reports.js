// MIS Reports - port of DFS SfMisReports.jsx (SfMisReportManager + SfMisReportView), with the components it uses
// (MisConditions, MisBookSheets, MisRangeHealth, ColumnMenu, ValuePicker, lib/misConds, lib/misBook).
//
//   "reports"  /mis           saved reports as a card gallery, the multi-report workbook, and the builder
//                             (/mis?new=1, /mis?edit=<slug>, /mis?copy=<slug>) with its live preview
//   "report"   /mis/r/<slug>  one saved report: dates, layout, header filters, conditions, exports, templates
//
// A definition is {group_by, windows, measures, stock_tags, ref_measures, plan_measures, plan_months,
// month_measures, grc_measures, po_measures, filters, dim_filters, col_filters, tag_plants, family_plants,
// store_only_stock, layout, ...}. Nothing is stored per report: every run re-aggregates from the day-wise
// facts. API: /api/v1/mis/report/{catalogue, reports, defs, values, run, export, export-xlsx},
// /api/v1/mis/book, /api/v1/mis/template/<slug>[/info], /api/v1/mis/template-export.
(function () {
  "use strict";
  var M = window.MIS, el = M.el;
  var API = "/api/v1/mis/report";
  var PAGE_SIZE = 200, QUICK_DIM = "ART_TYPE", MAX_DOWNLOADS = 25;

  // ── page-local styles (pick lists, plant pickers, report cards) ─────────────────────────────────────
  (function css() {
    if (document.getElementById("mr-css")) return;
    var s = document.createElement("style"); s.id = "mr-css";
    s.textContent = [
      ".mr-picks{display:grid;grid-template-columns:repeat(auto-fill,minmax(215px,1fr));gap:12px}",
      ".mr-pl{border:1px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column;min-width:0;background:var(--surface)}",
      ".mr-pl-h{display:flex;align-items:center;justify-content:space-between;gap:6px;padding:7px 10px;background:var(--surface-2);border-bottom:1px solid var(--line-2);font-size:11.5px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--fg-2)}",
      ".mr-pl-h .t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".mr-pl-h .n{font-weight:500;text-transform:none;letter-spacing:0;color:var(--muted);font-size:11px;flex:none;cursor:help}",
      ".mr-pl-h .x{border:0;background:none;cursor:pointer;color:var(--muted);padding:0 2px;line-height:0}",
      ".mr-pl-s{display:flex;align-items:center;gap:4px;padding:2px 8px;border-bottom:1px solid var(--line-2);color:var(--muted)}",
      ".mr-pl-s input{border:0;padding:5px 2px;flex:1;background:transparent;font-size:12.5px;min-width:0}",
      ".mr-pl-s input:focus{outline:none}",
      ".mr-pl-s button{border:0;background:none;font:inherit;font-size:11px;font-weight:600;cursor:pointer;color:var(--brand);padding:2px 4px;flex:none}",
      ".mr-pl-s button.clr{color:var(--bad)} .mr-pl-s button:disabled{opacity:.35;cursor:default}",
      ".mr-pl-b{overflow:auto;padding:4px;user-select:none;max-height:300px}",
      ".mr-pl-f{font-size:11px;color:var(--muted);padding:6px 10px;border-top:1px solid var(--line-2);margin:0}",
      ".mr-pl-f.bad{color:var(--bad)} .mr-pl-f.warn{color:var(--warn)}",
      ".mr-opt{display:flex;width:100%;gap:8px;align-items:center;text-align:left;border:0;background:none;font:inherit;font-size:12.5px;padding:4px 6px;border-radius:6px;cursor:pointer;color:var(--fg)}",
      ".mr-opt:hover{background:var(--surface-2)}",
      ".mr-opt i{width:13px;height:13px;border:1.5px solid var(--line);border-radius:3px;flex:none;position:relative}",
      ".mr-opt.on i{background:var(--brand-2);border-color:var(--brand-2)}",
      ".mr-opt.on i::after{content:'';position:absolute;left:3px;top:0;width:4px;height:7px;border:solid #fff;border-width:0 2px 2px 0;transform:rotate(45deg)}",
      ".mr-opt.stale i{background:var(--warn);border-color:var(--warn)} .mr-opt.stale .l{color:var(--warn);font-style:italic}",
      ".mr-opt .l{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".mr-opt .r{margin-left:auto;color:var(--muted);font-size:11px;flex:none}",
      ".mr-opt .r.dc{color:var(--ok)}",
      ".mr-opt .mono,.mr-mono{font-family:ui-monospace,Consolas,monospace}",
      ".mr-grp{font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);padding:8px 6px 2px;font-weight:600}",
      ".mr-pop{width:300px;max-width:92vw}",
      ".mr-pop .mr-pl-b{max-height:260px}",
      ".mr-plant.on{border-color:var(--brand-2);background:var(--brand-soft);color:var(--brand)}",
      ".rcard.picked{border-color:var(--brand-2);box-shadow:0 0 0 1px var(--brand-2)}",
      ".rcard .top{display:flex;align-items:flex-start;gap:8px;min-width:0}",
      ".rcard .top h3{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".rcard .sum{font-size:12px;color:var(--fg-2)}",
      ".rcard .acts{display:flex;gap:4px;flex-wrap:wrap;border-top:1px solid var(--line-2);padding-top:8px;margin-top:2px}",
      ".mr-badge{background:var(--brand-2);color:#fff;border-radius:5px;font-size:10.5px;font-weight:700;padding:1px 6px;flex:none}",
      ".mr-view{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:var(--muted);border:0;background:none;padding:0;cursor:pointer;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}",
      ".mr-view:hover{color:var(--brand)}",
      ".mr-tbl{width:100%;border-collapse:collapse;font-size:12.5px}",
      ".mr-tbl th{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);text-align:left;padding:6px 8px;background:var(--surface-2);border-bottom:1px solid var(--line-2)}",
      ".mr-tbl td{padding:5px 8px;border-bottom:1px solid var(--line-2);vertical-align:middle}",
      ".mr-tbl input{width:100%;padding:4px 8px;font-size:12.5px}",
      ".mr-tbl input.bad{border-color:var(--bad)}",
      ".mr-chip-m{background:color-mix(in srgb,var(--s4) 14%,var(--surface));color:var(--s4)}",
      ".mr-cond{display:flex;flex-wrap:wrap;gap:8px;align-items:center}",
      ".mr-cond select,.mr-cond input{font-size:12.5px;padding:5px 8px}",
      ".mr-hint{font-size:11.5px;color:var(--muted)}",
      ".mr-meas{padding:8px;display:flex;flex-direction:column;gap:6px}",
      ".mr-meas select,.mr-meas input{width:100%;font-size:12.5px;padding:5px 8px}",
      ".mr-sticky{position:sticky;top:0;z-index:20}"
    ].join("\n");
    document.head.appendChild(s);
  })();

  // ══ VOCABULARY - ported 1:1 from SfMisReports.jsx ═══════════════════════════════════════════════════
  function starts(c, ps) { for (var i = 0; i < ps.length; i++) if (String(c).indexOf(ps[i]) === 0) return true; return false; }
  function has2(c) { return String(c).indexOf("__") >= 0; }
  function measOf(c) { return c.slice(c.indexOf("__") + 2); }
  // S_ sale, K_ stock, R_ Day Ref, P_ Sale Plan, M_ Month Plan, G_ GRC, O_ PO - all <prefix><tag>__<measure>.
  function isMetric(c) { return starts(c, ["S_", "K_", "R_", "P_", "M_", "G_", "O_"]) && has2(c); }
  // A count of DAYS, never of pieces or rupees - the K/L divisor leaves it alone.
  function isDayCount(c) { return starts(c, ["S_", "G_"]) && /DAYS$/.test(String(c).split("__")[1] || ""); }
  function isGrcCol(c) { return starts(c, ["G_"]) && has2(c); }
  function isPoCol(c) { return starts(c, ["O_"]) && has2(c); }
  var COUNT_MEAS = { GRC_LINES: 1, PO_LINES: 1, PO_COUNT: 1 };
  function isCountCol(c) { return (isGrcCol(c) || isPoCol(c)) && !!COUNT_MEAS[measOf(c)]; }
  function isMonthCol(c) { return starts(c, ["M_"]) && has2(c); }
  var MONTH_PLAIN = { TRF_COVER_DAY: 1 };
  function isRefCol(c) { return starts(c, ["R_"]) && has2(c); }
  function isRefPct(c) { return isRefCol(c) && /__LYSP_DAY_CONTRI$/.test(c); }

  var WIN_LABEL = { TD: "TD", MTD: "MTD", QTR: "QTR", LAST_7DAYS: "L7D", LAST_4WEEK: "L4W", YTD: "YTD",
    LAST_6MONTH: "L6M", LYSP_SSN: "LYSP SSN", LYSP_MONTH: "LYSP MTH", RANGE: "Range" };
  var MONTH_TOK_LABEL = { PM: "Prev month", CM: "This month", NM: "Next month", M2: "+2 months", M3: "+3 months" };
  var MON_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function monthLabel(t) { return MONTH_TOK_LABEL[t] || (/^[0-9]{6}$/.test(t) ? MON_ABBR[Number(t.slice(4)) - 1] + "-" + t.slice(0, 4) : null); }

  var MEAS_LABEL = {
    SALE_V: "Sale V", SALE_Q: "Sale Q", GM_V: "GM V", DISCOUNT_V: "Disc V", AVG_SALE_V: "Avg Sale V", AVG_SALE_Q: "Avg Sale Q", AVG_GM_V: "Avg GM V",
    SAL_DAYS: "Sale Days", GM_DAYS: "GM Days", DATA_DAYS: "Data Days", STOCK_V: "Stk V", STOCK_Q: "Stk Q",
    ACT_SALE_Q: "LY-Ref Act Qty", ACT_SALE_V: "LY-Ref Act Val", ACT_GM_VAL: "LY-Ref Act GM", ACT_AVG_STK_Q: "LY-Ref Act Stk Q", ACT_AVG_STK_V: "LY-Ref Act Stk V",
    LYSP_SALE_Q: "LYSP Qty", LYSP_SALE_V: "LYSP Val", LYSP_GM_VAL: "LYSP GM", LYSP_AVG_STK_Q: "LYSP Stk Q", LYSP_AVG_STK_V: "LYSP Stk V",
    LYSM_SALE_Q: "LYSM Qty", LYSM_SALE_V: "LYSM Val", LYSM_GM_VAL: "LYSM GM", LYSM_AVG_STK_Q: "LYSM Stk Q", LYSM_AVG_STK_V: "LYSM Stk V",
    LYSP_DAY_CONTRI: "LYSP Day Contri %",
    SALE_W_Q: "Sale Qty (W/STR)", SALE_W_V: "Sale Val (W/STR)", SALE_WO_Q: "Sale Qty (WO STR)", SALE_WO_V: "Sale Val (WO STR)",
    SPACE_DISP_Q: "Initial Disp Qty", TRF_SALE_Q: "TRF Sale Qty", TRF_COVER_SALE_Q: "Cover Sale Qty", TRF_DISP_CL_Q: "Bgt Disp Cl Qty",
    TRF_OP_STK: "Opening Stock", TRF_IN_STK_Q: "Transfer In Qty", TRF_CL_STK: "Closing Stock", TRF_SHORT_Q: "Short Qty", TRF_EXCESS_Q: "Excess Qty",
    TRF_COVER_DAY: "Cover Days", PP_BGT_PUR_Q: "Purchase Qty", PP_POS_PO_RAISED: "PO Raised (+)", PP_NEG_PO_RAISED: "PO Raised (-)", PP_DC_STK_Q: "DC Stock",
    PP_DC_CL_STK_Q: "DC Closing Stock", PP_TTL_TRF_OUT_Q: "PP Trf Out Qty", PP_CM_TRF_OUT_Q: "PP Cur Mth Trf Out", PP_DC_SHORT_Q: "DC Short",
    PP_DC_EXCESS_Q: "DC Excess", PP_DEL_PEND_Q: "Delivery Pending",
    GRC_Q: "GRC Qty", GRC_V: "GRC Val", GRC_GRS_Q: "GRC Gross Qty", GRC_GRS_V: "GRC Gross Val", GRC_RET_Q: "GRC Return Qty", GRC_RET_V: "GRC Return Val",
    GRC_LINES: "GRC Lines", GRC_DAYS: "GRC Days",
    PO_PEND_Q: "PO Pending Qty", PO_PEND_V: "PO Pending Val", PO_ORD_Q: "PO Ordered Qty", PO_ORD_V: "PO Ordered Val", PO_GR_Q: "PO Received Qty",
    PO_OVD_Q: "PO Overdue Qty", PO_OVD_V: "PO Overdue Val", PO_REL_Q: "PO Released Qty", PO_UNREL_Q: "PO Unreleased Qty", PO_LINES: "PO Lines", PO_COUNT: "PO Count"
  };
  // Display-only header renames for a few stock tags (the CSV keeps the raw K_<tag>__<measure> names).
  var STOCK_HEAD_OVERRIDE = { PTL: { STOCK_Q: "PTL_Q", STOCK_V: "PTL_V" }, MSA: { STOCK_Q: "MSA_Q", STOCK_V: "MSA_V" }, GRT: { STOCK_Q: "GRT_Q", STOCK_V: "GRT_V" } };
  var PICK_LABELS = { MSA: "MSA", GRT: "GRT" };
  var PLANT_BUCKETS = ["STORE", "DC", "HUB", "OTHER", "UNMAPPED"];
  var BUCKET_RE = new RegExp("^(.+)_(" + PLANT_BUCKETS.join("|") + ")$");
  var COMPOSITE_LABELS = { TOTAL_STORE_STOCK: "Total Store Stock" };

  function headLabel(c) {
    if (!isMetric(c)) return c;
    var i = c.indexOf("__"), tag = c.slice(2, i), meas = c.slice(i + 2), ml = MEAS_LABEL[meas] || meas;
    if (starts(c, ["K_"]) && STOCK_HEAD_OVERRIDE[tag] && STOCK_HEAD_OVERRIDE[tag][meas]) return STOCK_HEAD_OVERRIDE[tag][meas];
    if (starts(c, ["K_"])) {
      if (COMPOSITE_LABELS[tag]) return COMPOSITE_LABELS[tag] + " · " + ml;
      var sp = BUCKET_RE.exec(tag);
      if (sp) return sp[1] + " · " + sp[2] + " · " + ml;
    }
    if (starts(c, ["M_"])) return (monthLabel(tag) || tag) + " · " + ml;
    var left = starts(c, ["S_", "R_", "P_", "G_"]) ? (WIN_LABEL[tag] || tag) : tag;
    return left + " · " + ml;
  }
  function nIN(n) { return n.toLocaleString("en-IN", { maximumFractionDigits: 2 }); }
  // A cell, formatted from the WIDE column name it came from (see vertRowKey / the period map for the turned layouts).
  function fmtCell(c, v, scaled) {
    if (v === null || v === undefined || v === "") return "–";
    if (!isMetric(c)) return String(v);
    var n = Number(v);
    if (!isFinite(n)) return String(v);
    if (isRefPct(c)) return (n * 100).toFixed(2) + "%";
    if (!scaled || isDayCount(c) || isCountCol(c) || (isMonthCol(c) && MONTH_PLAIN[measOf(c)])) return nIN(n);
    // A month measure cannot be read by its suffix: decide on _V and default to quantity.
    if (isMonthCol(c)) return nIN(n / (/_V$/.test(c) ? 100000 : 1000));
    return nIN(n / (/_Q$/.test(c) ? 1000 : 100000));
  }

  // ── the turned layouts ──
  var VERT_BLOCK = "BLOCK", VERT_MEAS = "MEASURE", VERT_AS_AT = "AS_AT";
  var BLOCK_LABEL = { SALE: "Sale", STOCK: "Stock", DAY_REF: "Day Ref", SALE_PLAN: "Sale Plan", MONTH_PLAN: "Month Plan", GRC: "GRC", PO: "PO" };
  var BLOCK_PREFIX = { SALE: "S_", DAY_REF: "R_", SALE_PLAN: "P_", GRC: "G_" };
  function vertRowKey(block, measure) {
    return block === "STOCK" ? "K_" + measure : block === "MONTH_PLAN" ? "M_CM__" + measure : block === "PO" ? "O_PO__" + measure
      : (BLOCK_PREFIX[block] || "S_") + "TD__" + measure;
  }
  function vertMeasLabel(block, measure) { return block === "STOCK" ? headLabel("K_" + measure) : (MEAS_LABEL[measure] || measure); }
  function vertVocab(defn, col) {
    var d = defn || {}, rows = [];
    (d.measures || []).forEach(function (m) { rows.push(["SALE", m]); });
    (d.stock_tags || []).forEach(function (t) { ["STOCK_V", "STOCK_Q"].forEach(function (m) { rows.push(["STOCK", t + "__" + m]); }); });
    (d.ref_measures || []).forEach(function (m) { rows.push(["DAY_REF", m]); });
    (d.plan_measures || []).forEach(function (m) { rows.push(["SALE_PLAN", m]); });
    (d.month_measures || []).forEach(function (m) { rows.push(["MONTH_PLAN", m]); });
    (d.grc_measures || []).forEach(function (m) { rows.push(["GRC", m]); });
    (d.po_measures || []).forEach(function (m) { rows.push(["PO", m]); });
    var out = [], seen = {};
    rows.forEach(function (r) {
      var v = col === VERT_BLOCK ? r[0] : r[1];
      if (seen[v]) return; seen[v] = 1;
      out.push({ value: v, label: col === VERT_BLOCK ? (BLOCK_LABEL[v] || v) : vertMeasLabel(r[0], v) });
    });
    return out;
  }
  var PERIOD_COL = "PERIOD", PERIOD_KINDS = { QTY: 1, VALUE: 1, GM: 1 }, PO_PERIOD = "PO";
  var FAM_LABEL = { WINDOW: "window rows", STOCK: "stock tag rows", MONTH: "month rows", PO: "the PO row" };
  function tagLabel(t) { if (COMPOSITE_LABELS[t]) return COMPOSITE_LABELS[t]; var sp = BUCKET_RE.exec(t); return sp ? sp[1] + " · " + sp[2] : t; }
  function periodLabel(v) { return v === PO_PERIOD ? "PO (open book)" : (WIN_LABEL[v] || monthLabel(v) || tagLabel(v)); }
  function periodHead(c) { return PERIOD_KINDS[c] ? c : (MEAS_LABEL[c] || c); }
  function optLabel(o) { return MEAS_LABEL[o] || WIN_LABEL[o] || monthLabel(o) || o; }
  var BLOCK_OF = { S_: "Sale", K_: "Stock", R_: "Day Ref (LY)", P_: "Sale Plan", M_: "Month Plan (360)", G_: "GRC", O_: "PO (open)" };
  var LAYOUTS = [["horizontal", "Horizontal"], ["vertical", "Vertical"], ["period", "Period"]];
  function layoutName(v) { var l = LAYOUTS.filter(function (x) { return x[0] === v; })[0]; return l ? l[1] : "Horizontal"; }

  // ══ CONDITIONS - lib/misConds.js ════════════════════════════════════════════════════════════════════
  var IN_OP = "in", NOT_IN_OP = "not_in";
  function IS_LIST(op) { return op === IN_OP || op === NOT_IN_OP; }
  var BLANK_OPS = [["nonblank", "Is not blank"], ["blank", "Is blank"]];
  var LIKE_OPS = [["like", "Matches pattern"], ["not_like", "Does not match pattern"]];
  var LIST_OPS = [[IN_OP, "Is any of"], [NOT_IN_OP, "Is none of"]];
  var NUM_OPS = [["gte", "Greater than or equal"], ["gt", "Greater than"], ["lte", "Less than or equal"], ["lt", "Less than"],
    ["eq", "Equals"], ["ne", "Does not equal"], ["between", "Between"]].concat(BLANK_OPS);
  var MEAS_OPS = NUM_OPS.filter(function (o) { return o[0] !== "blank" && o[0] !== "nonblank"; }).concat(LIST_OPS, BLANK_OPS);
  var ALL_OPS = LIST_OPS.concat([["between", "Between"], ["gte", "Greater than or equal"], ["gt", "Greater than"], ["lte", "Less than or equal"],
    ["lt", "Less than"], ["eq", "Equals"], ["ne", "Does not equal"], ["contains", "Contains"], ["not_contains", "Does not contain"],
    ["starts", "Begins with"], ["ends", "Ends with"]], LIKE_OPS, BLANK_OPS);
  var MAX_IN_VALUES = 500;
  function isLike(op) { return op === "like" || op === "not_like"; }
  function splitVals(s) { return String(s == null ? "" : s).split(",").map(function (v) { return v.trim(); }).filter(Boolean); }
  // A condition row -> {filters (dimension "is any of", inside the scan), dim_filters (other dimension
  // conditions, inside the scan), col_filters (measures, on the finished totals)}.
  function condsToDef(rows, dims) {
    var filters = {}, colFilters = [], dimFilters = [];
    (rows || []).forEach(function (r) {
      var col = String(r.col || "").trim(); if (!col) return;
      var isDim = (dims || []).indexOf(col) >= 0, bucket = isDim ? dimFilters : colFilters;
      if (IS_LIST(r.op)) {
        var vals = splitVals(r.v1); if (!vals.length) return;
        if (r.op === IN_OP && isDim) filters[col] = vals; else bucket.push({ col: col, op: r.op, vals: vals });
      } else if (r.op === "blank" || r.op === "nonblank") bucket.push({ col: col, op: r.op });
      else if (r.op === "between") { if (String(r.v1 == null ? "" : r.v1) !== "" && String(r.v2 == null ? "" : r.v2) !== "") bucket.push({ col: col, op: r.op, v1: r.v1, v2: r.v2 }); }
      else if (String(r.v1 == null ? "" : r.v1) !== "") bucket.push({ col: col, op: r.op, v1: r.v1 });
    });
    return { filters: filters, col_filters: colFilters, dim_filters: dimFilters };
  }
  function condsFromDef(defn) {
    var rows = [], f = (defn && defn.filters) || {};
    Object.keys(f).forEach(function (col) { rows.push({ col: col, op: IN_OP, v1: (f[col] || []).join(", "), v2: "" }); });
    ((defn && defn.col_filters) || []).concat((defn && defn.dim_filters) || []).forEach(function (x) {
      rows.push({ col: x.col, op: x.op, v2: x.v2 == null ? "" : x.v2, v1: Array.isArray(x.vals) ? x.vals.join(", ") : (x.v1 == null ? "" : x.v1) });
    });
    return rows;
  }

  // ══ WORKBOOK - lib/misBook.js ═══════════════════════════════════════════════════════════════════════
  var SHEET_MAX = 31;   // Excel: at most 31 characters, none of : \ / ? * [ ]
  function sheetSafe(s) { return String(s == null ? "" : s).replace(/[:\\/?*[\]]+/g, "-").replace(/\s+/g, " ").trim().slice(0, SHEET_MAX); }
  function sheetRow(r) { return { slug: r.slug, sheet: sheetSafe(r.name || r.slug) }; }
  function sheetsProblem(rows) {
    var seen = {};
    for (var i = 0; i < rows.length; i++) {
      var n = sheetSafe(rows[i].sheet);
      if (!n) return "Every sheet needs a name";
      if (seen[n.toUpperCase()]) return "Two sheets are both called “" + n + "” - names must differ";
      seen[n.toUpperCase()] = 1;
    }
    return "";
  }

  // ══ helpers ═════════════════════════════════════════════════════════════════════════════════════════
  function yday() { return M.addDays(M.today(), -1); }   // the facts load a day behind, so today is always empty
  function inc(a, x) { return (a || []).indexOf(x) >= 0; }
  function uniq(a) { var s = {}, o = []; (a || []).forEach(function (x) { if (!s[x]) { s[x] = 1; o.push(x); } }); return o; }
  function clone(o) { return JSON.parse(JSON.stringify(o == null ? {} : o)); }
  function flatDims(cat) { var o = []; Object.keys((cat && cat.dims) || {}).forEach(function (k) { o = o.concat(cat.dims[k] || []); }); return o; }
  function isMine(r, me) { return !!r && !!me && String(r.createdBy || "").toUpperCase() === String(me.emp_code || "").toUpperCase(); }
  function plural(n, w) { return M.fmt(n, "int") + " " + w + (n === 1 ? "" : "s"); }
  function days(a, b) { return Math.round((new Date(b + "T00:00:00Z") - new Date(a + "T00:00:00Z")) / 864e5) + 1; }
  function loadCatalogue() {
    if (!M.cache.misCat) M.cache.misCat = M.get(API + "/catalogue").catch(function (e) { M.cache.misCat = null; throw e; });
    return M.cache.misCat;
  }
  function listReports() { return M.get(API + "/reports").then(function (r) { return r.reports || []; }); }
  function valuesOf(body) { return M.post(API + "/values", body).then(function (d) { return (d && d.values) || []; }); }
  // A labelled control for a filter bar; clicking the caption must not press the first button inside it.
  function fld(caption, control, title) {
    var l = el("label", { "class": "f", title: title || null }, [caption, control]);
    l.addEventListener("click", function (e) { if (e.target === l) e.preventDefault(); });
    return l;
  }
  function check(label, checked, onChange, title) {
    var cb = el("input", { type: "checkbox", checked: !!checked });
    cb.addEventListener("change", function () { onChange(cb.checked); });
    return el("label", { "class": "row small", style: { gap: "6px", cursor: "pointer" }, title: title || null }, [cb, label]);
  }
  function note(text, cls) { return el("p", { "class": "mr-hint " + (cls || ""), style: { margin: 0 }, text: text }); }
  function csrf() { var m = document.cookie.match(/(?:^|;\s*)pulse_csrf=([^;]+)/); return m ? decodeURIComponent(m[1]) : ""; }
  // A file download under a name chosen here (MIS.download always takes the server's name; a per-store split
  // needs one name per file, as DFS gave them).
  function dlAs(method, url, body, name) {
    var o = { method: method, credentials: "same-origin", headers: {} };
    if (method !== "GET") o.headers["X-CSRF-Token"] = csrf();
    if (body !== undefined) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(body); }
    return fetch(url, o).then(function (r) {
      if (!r.ok) return r.text().then(function (t) {
        var d = null; try { d = JSON.parse(t); } catch (e) { d = null; }
        var msg = r.status === 401 ? "Your V2Pulse session has ended - sign in again and reload this page."
          : (d && (typeof d.detail === "string" ? d.detail : d.error)) || (r.status === 403 ? "You do not have access to this." : "The request failed (error " + r.status + ").");
        throw new Error(msg);
      });
      return r.blob().then(function (b) {
        var a = el("a", { href: URL.createObjectURL(b), download: name }); document.body.appendChild(a); a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 20000); return name;
      });
    });
  }
  function copyText(t) {
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(t).then(function () { return true; }, function () { return false; });
    return Promise.resolve(false);
  }

  // ══ WIDGETS ═════════════════════════════════════════════════════════════════════════════════════════
  // A checkbox list with search (DFS PickList): All takes the SEARCH MATCHES, Clear empties everything,
  // drag down the list to pick several (starting on a picked row un-picks the sweep), Shift-click extends a range.
  var DRAG = null;
  window.addEventListener("mouseup", function () { DRAG = null; });
  window.addEventListener("dragend", function () { DRAG = null; });
  function pickList(o) {
    var value = (o.value || []).slice(), groups = o.groups || {}, q = "", labelOf = o.labelOf || optLabel, anchor = null, vis = [], btns = {};
    var cnt = el("span", { "class": "n", title: "Click to pick. Drag down the list to pick several at once - starting on a picked row un-picks the sweep instead. Shift-click extends from the last row you touched." });
    var search = el("input", { type: "search", placeholder: "Search…", "aria-label": "Search " + o.title });
    var allB = el("button", { type: "button" }), clrB = el("button", { type: "button", "class": "clr", text: "Clear" });
    var body = el("div", { "class": "mr-pl-b", style: { maxHeight: o.height || "300px" } });
    var foot = el("p", { "class": "mr-pl-f hidden" });
    var root = el("div", { "class": "mr-pl" + (o.cls ? " " + o.cls : "") }, [
      el("div", { "class": "mr-pl-h" }, [el("span", { "class": "t", text: o.title, title: o.note || o.title }), o.note ? el("span", { "class": "n", title: o.note }, [M.icon("info", 13)]) : null, cnt]),
      el("div", { "class": "mr-pl-s" }, [M.icon("search", 12), search, allB, el("span", { text: "|" }), clrB]), body, foot]);
    function flat() { var f = []; Object.keys(groups).forEach(function (k) { f = f.concat(groups[k] || []); }); return f; }
    function matches() { var f = flat(); return q ? f.filter(function (x) { return (x + " " + labelOf(x)).toLowerCase().indexOf(q) >= 0; }) : f; }
    function fire() { if (o.onChange) o.onChange(value.slice()); }
    function paintStates() {
      Object.keys(btns).forEach(function (k) { btns[k].classList.toggle("on", value.indexOf(k) >= 0); });
      cnt.textContent = value.length + " picked";
      var shown = matches(), allPicked = shown.length > 0 && shown.every(function (x) { return value.indexOf(x) >= 0; });
      allB.textContent = q ? "All " + shown.length : "All"; allB.disabled = allPicked || !shown.length;
      allB.title = q ? "Pick the " + shown.length + " option(s) matching “" + q + "”, keeping what is already picked" : "Pick all " + flat().length + " option(s)";
      clrB.disabled = !value.length;
    }
    function paint(x, sel) {
      var hasIt = value.indexOf(x) >= 0; if (hasIt === sel) return;
      value = sel ? value.concat([x]) : value.filter(function (v) { return v !== x; }); paintStates(); fire();
    }
    function paintRange(a, b, sel) {
      var i = vis.indexOf(a), j = vis.indexOf(b);
      if (i < 0 || j < 0) { paint(b, sel); return; }
      vis.slice(Math.min(i, j), Math.max(i, j) + 1).forEach(function (x) { paint(x, sel); });
    }
    function draw() {
      M.clear(body); btns = {};
      var shownAll = matches(); vis = o.cap ? shownAll.slice(0, o.cap) : shownAll;
      var visSet = {}; vis.forEach(function (x) { visSet[x] = 1; });
      var keys = Object.keys(groups);
      keys.forEach(function (g) {
        var shown = (groups[g] || []).filter(function (x) { return visSet[x]; }); if (!shown.length) return;
        if (keys.length > 1 && g) body.appendChild(el("div", { "class": "mr-grp", text: g }));
        shown.forEach(function (x) {
          var b = el("button", { type: "button", "class": "mr-opt", title: x !== labelOf(x) ? x : null }, [el("i"), el("span", { "class": "l", text: labelOf(x) })]);
          b.addEventListener("mousedown", function (e) {
            if (e.button !== 0) return; e.preventDefault();
            var sel = value.indexOf(x) < 0;
            if (e.shiftKey && anchor !== null) { paintRange(anchor, x, sel); anchor = x; return; }
            paint(x, sel); anchor = x; DRAG = { list: root, sel: sel };
          });
          b.addEventListener("mouseenter", function () {
            if (!DRAG || DRAG.list !== root) return;
            paintRange(anchor === null ? x : anchor, x, DRAG.sel); anchor = x;
          });
          b.addEventListener("click", function (e) { if (e.detail === 0) paint(x, value.indexOf(x) < 0); });   // keyboard only
          btns[x] = b; body.appendChild(b);
        });
      });
      if (!vis.length) body.appendChild(el("div", { "class": "muted small", style: { padding: "6px" }, text: o.emptyText || (q ? "Nothing matches." : "Nothing to pick.") }));
      if (shownAll.length > vis.length) body.appendChild(el("div", { "class": "mr-hint", style: { padding: "4px 6px" }, text: (shownAll.length - vis.length) + " more - narrow with the box above" }));
      paintStates();
    }
    search.addEventListener("input", function () { q = search.value.trim().toLowerCase(); draw(); });
    allB.addEventListener("click", function () { var shown = matches(); value = flat().filter(function (x) { return value.indexOf(x) >= 0 || shown.indexOf(x) >= 0; }); paintStates(); fire(); });
    clrB.addEventListener("click", function () { value = []; paintStates(); fire(); });
    draw();
    return { el: root, get: function () { return value.slice(); }, set: function (v) { value = (v || []).slice(); paintStates(); },
             setGroups: function (g) { groups = g || {}; draw(); },
             setFoot: function (text, cls) { foot.textContent = text || ""; foot.className = "mr-pl-f" + (cls ? " " + cls : "") + (text ? "" : " hidden"); } };
  }

  // A toolbar-sized value picker (DFS ValuePicker): button + popover tick-list of a column's real values.
  var VP_CACHE = {};
  function valuePicker(o) {
    var vals = (o.value || []).slice();
    var b = el("button", { type: "button", "class": "btn sm dd-btn", title: o.title || null, style: { minWidth: (o.width || 140) + "px" } });
    function paint() {
      M.clear(b);
      b.appendChild(el("span", { "class": "t", text: vals.length ? (vals.length === 1 ? vals[0] : vals.length + " selected") : (o.placeholder || "All") }));
      b.appendChild(vals.length ? el("span", { "class": "cnt", text: vals.length }) : M.icon("chev", 13));
    }
    b.addEventListener("click", function () {
      var body = el("div", { "class": "pop-b" }, [el("div", { "class": "muted small", html: '<span class="spin"></span> Loading values…' })]);
      var ok = el("button", { type: "button", "class": "btn primary sm", text: "Apply" }), clr = el("button", { type: "button", "class": "btn sm", text: "Clear" });
      M.popover(b, [el("div", { "class": "pop-h" }, [el("b", { text: o.label || "Values" })]), body, el("div", { "class": "pop-f" }, [clr, ok])]);
      var key = typeof o.cacheKey === "function" ? o.cacheKey() : o.cacheKey, cl = null;
      var p = key && VP_CACHE[key] ? Promise.resolve(VP_CACHE[key])
        : Promise.resolve(o.load()).then(function (v) { v = (v || []).map(function (x) { return x !== null && typeof x === "object" ? x : String(x); }); if (key) VP_CACHE[key] = v; return v; });
      p.then(function (list) {
        M.clear(body);
        var plain = list.map(function (x) { return typeof x === "object" ? x.value : x; });
        var stale = vals.filter(function (x) { return plain.indexOf(x) < 0; });
        var groups = [{ items: list }]; if (stale.length) groups.push({ label: "Picked, but not among this range's values", items: stale });
        cl = M.checklist(groups, vals, { height: "280px", prettify: false, placeholder: "Search values" }); body.appendChild(cl.el);
        if (o.max && cl.get().length > o.max) body.appendChild(note("More than " + o.max + " picked - the engine keeps the first " + o.max + ".", "warn"));
      }, function (e) { M.clear(body).appendChild(el("div", { "class": "notice bad", text: "Could not load the list - " + e.message })); });
      ok.addEventListener("click", function () { if (cl) vals = cl.get(); M.closePop(); paint(); o.onChange(vals.slice()); });
      clr.addEventListener("click", function () { vals = []; M.closePop(); paint(); o.onChange([]); });
    });
    paint();
    return { el: b, get: function () { return vals.slice(); }, set: function (v) { vals = (v || []).slice(); paint(); } };
  }

  // Which plants count for one stock tag, or for a whole plant family (DFS TagPlantPicker). COLUMNS ONLY:
  // the plants left out keep their rows, their sale and every other column.
  function plantPicker(label, plants, value, onChange, family) {
    var vals = (value || []).slice(), q = "";
    var dcs = plants.filter(function (p) { return p.typ === "DC" && p.stat !== "IN-ACT"; }).map(function (p) { return p.cd; });
    var b = el("button", { type: "button", "class": "btn sm mr-plant",
      title: "Narrows the " + label + " stock COLUMNS to the plants picked here. No rows are dropped - the plants left out keep their sale and every other column.\n\n"
        + (family ? "Set for the whole family: every tag in it, unless that tag has a pick of its own, which wins.\n\n" : "") + "The list is every plant in the MIS store master." });
    function paint() {
      M.clear(b); b.classList.toggle("on", vals.length > 0);
      b.appendChild(el("span", { "class": "mr-mono", text: label })); b.appendChild(document.createTextNode(": "));
      b.appendChild(el("b", { text: vals.length ? vals.length + " picked" : plants.length ? "all " + plants.length + " plants" : "all plants" }));
      b.appendChild(M.icon("chev", 12));
    }
    function set(v) { vals = v; paint(); onChange(vals.slice()); }
    b.addEventListener("click", function () {
      var list = el("div", { "class": "mr-pl-b" }), cnt = el("span", { "class": "mr-hint", style: { marginLeft: "auto" } });
      var s = el("input", { type: "search", placeholder: plants.length + " plants - code, name or type", "aria-label": "Find a plant" });
      function draw() {
        M.clear(list); cnt.textContent = vals.length + " picked";
        var ql = q.trim().toLowerCase();
        var shown = (ql ? plants.filter(function (p) { return (p.cd + " " + p.nm + " " + p.typ).toLowerCase().indexOf(ql) >= 0; }) : plants).slice(0, 300);
        shown.forEach(function (p) {
          var on = vals.indexOf(p.cd) >= 0;
          list.appendChild(el("button", { type: "button", "class": "mr-opt" + (on ? " on" : ""), onclick: function () { set(on ? vals.filter(function (x) { return x !== p.cd; }) : vals.concat([p.cd])); draw(); } },
            [el("i"), el("span", { "class": "mono", text: p.cd }), el("span", { "class": "l muted", text: p.nm || "" }),
             el("span", { "class": "r" + (p.typ === "DC" ? " dc" : ""), text: (p.typ || "") + (p.stat === "IN-ACT" ? " · IN-ACT" : "") })]));
        });
        vals.filter(function (cd) { return !plants.some(function (p) { return p.cd === cd; }); }).forEach(function (cd) {
          list.appendChild(el("button", { type: "button", "class": "mr-opt stale on", title: "Picked, but not in the store master",
            onclick: function () { set(vals.filter(function (x) { return x !== cd; })); draw(); } }, [el("i"), el("span", { "class": "l mono", text: cd }), el("span", { "class": "r", text: "not in master" })]));
        });
        if (!plants.length) list.appendChild(note("No plant list in the catalogue."));
        else if (!shown.length && ql) list.appendChild(note("No match."));
      }
      s.addEventListener("input", function () { q = s.value; draw(); });
      var head = el("div", { "class": "pop-h" }, [
        dcs.length ? el("button", { type: "button", "class": "btn ghost sm", text: "DCs", title: "Every plant the master types DC and does not mark IN-ACT: " + dcs.join(" / "), onclick: function () { set(dcs.slice()); draw(); } }) : null,
        el("button", { type: "button", "class": "btn ghost sm", text: "All (no narrowing)", onclick: function () { set([]); draw(); } }), cnt]);
      M.popover(b, [el("div", { "class": "mr-pop" }, [head, el("div", { style: { padding: "6px 10px 0" } }, [s]), list,
        el("p", { "class": "mr-pl-f", text: "Columns only - the plants left out keep their rows and their sale." })])]);
      draw(); s.focus();
    });
    paint();
    return { el: b };
  }

  // The Layout switch (one control, two callers - the builder saves it, the report page overrides it for one visit).
  var LAYOUT_TIP = "Horizontal - one row per group, a column per window x measure.\nVertical - one row per group PER MEASURE, with the windows as the columns.\n"
    + "Period - one row per group PER PERIOD (TD, MTD, each stock tag, each plan month), with QTY / VALUE / GM as the columns.\n\n"
    + "The same numbers whichever way: all three are built from one aggregate in Snowflake.";
  function layoutToggle(value, onChange, title) {
    var seg = el("div", { "class": "seg", role: "group", "aria-label": "Layout" });
    function paint(v) { [].forEach.call(seg.children, function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-v") === v)); }); }
    LAYOUTS.forEach(function (l) { seg.appendChild(el("button", { type: "button", "data-v": l[0], text: l[1], onclick: function () { paint(l[0]); onChange(l[0]); } })); });
    paint(value || "horizontal");
    return { el: fld("Layout", seg, title), set: paint };
  }

  // The CONDITIONS panel (DFS MisConditions): any catalogue dimension (inside the scan) or a measure this report
  // produces (on the finished totals), in the grid header's words.
  function condPanel(o) {
    var list = el("div", { "class": "stack", style: { gap: "8px" } }), notes = el("div", { "class": "stack", style: { gap: "6px" } });
    var cnt = el("span", { "class": "chip hidden" });
    var addB = el("button", { type: "button", "class": "btn sm" }, [M.icon("plus", 14), "Add condition"]);
    addB.addEventListener("click", function () { o.rows().push({ col: "", op: "gte", v1: "", v2: "" }); redraw(); o.onChange("conds"); });
    var card = el("div", { "class": "card" }, [
      el("div", { "class": "card-h" }, [el("h2", { text: "Conditions" }), cnt, el("span", { "class": "note", text: o.note }), el("span", { "class": "spacer" }), addB]),
      el("div", { "class": "card-b stack" }, [list, notes])]);
    function redraw() {
      var rows = o.rows(), grouped = o.groupedDims(), offBy = o.offBy(), meas = o.measures();
      var off = []; offBy.forEach(function (g) { off = off.concat(g[1]); });
      var measIds = meas.map(function (m) { return m.id; });
      function isDim(c) { return inc(grouped, c) || inc(off, c); }
      M.clear(list);
      var live = rows.filter(function (r) { return r.col; }).length;
      cnt.textContent = live; cnt.classList.toggle("hidden", !live);
      if (!rows.length) list.appendChild(note(o.emptyText));
      rows.forEach(function (c, i) {
        var dim = isDim(c.col), isOff = inc(off, c.col), ops = dim || !measIds.length ? ALL_OPS : MEAS_OPS;
        var needs2 = c.op === "between", needs0 = c.op === "blank" || c.op === "nonblank", isIn = IS_LIST(c.op), isPat = isLike(c.op);
        var colSel = el("select", { "aria-label": "Column", style: { minWidth: "200px", maxWidth: "320px" } }, [el("option", { value: "", text: "— pick a column —" })]);
        if (grouped.length) colSel.appendChild(el("optgroup", { label: "Group-by columns (filters rows before totalling)" }, grouped.map(function (x) { return el("option", { value: x, text: x }); })));
        offBy.forEach(function (g) { colSel.appendChild(el("optgroup", { label: g[0] + " — filter only, not a column here" }, g[1].map(function (x) { return el("option", { value: x, text: x }); }))); });
        if (meas.length) colSel.appendChild(el("optgroup", { label: "Measures (filters the totals themselves)" }, meas.map(function (m) { return el("option", { value: m.id, text: m.label }); })));
        if (c.col && !dim && !inc(measIds, c.col)) colSel.appendChild(el("option", { value: c.col, text: c.col + " (not produced by this report)" }));
        colSel.value = c.col || "";
        var opSel = el("select", { "aria-label": "Operator" }, ops.map(function (x) { return el("option", { value: x[0], text: x[1] }); }));
        if (!ops.some(function (x) { return x[0] === c.op; })) opSel.appendChild(el("option", { value: c.op, text: c.op }));
        opSel.value = c.op;
        var v1 = el("input", { type: c.col === "DT" && !isIn ? "date" : "text", value: c.v1 == null ? "" : c.v1, "aria-label": "Value", style: { width: "180px" },
          placeholder: isIn ? "value, value, value" : isPat ? "HB%  ·  %KURTA%" : needs2 ? "from" : "value" });
        var v2 = el("input", { type: c.col === "DT" ? "date" : "text", value: c.v2 == null ? "" : c.v2, "aria-label": "Second value", placeholder: "to", style: { width: "150px" } });
        v1.addEventListener("input", function () { c.v1 = v1.value; drawNotes(); o.onChange("conds"); });
        v2.addEventListener("input", function () { c.v2 = v2.value; o.onChange("conds"); });
        colSel.addEventListener("change", function () {
          var col = colSel.value, nowDim = isDim(col), stillOk = (nowDim ? ALL_OPS : MEAS_OPS).some(function (x) { return x[0] === c.op; });
          c.col = col; c.op = stillOk ? c.op : (nowDim ? IN_OP : "gte"); redraw(); o.onChange("conds");
        });
        opSel.addEventListener("change", function () { c.op = opSel.value; redraw(); o.onChange("conds"); });
        var kids = [colSel, opSel, needs0 ? null : v1, needs2 ? v2 : null];
        if (isIn && dim && o.loadValues) {
          kids.push(valuePicker({ label: c.col, load: function () { return o.loadValues(c.col); }, cacheKey: function () { return "cond|" + c.col + "|" + o.valuesKey(); },
            value: splitVals(c.v1), placeholder: "pick values…", width: 132, max: MAX_IN_VALUES,
            title: "The values " + c.col + " actually has over the dates this page is showing.\nTicking writes them into the box beside it, so you can still type your own - and either way it is the same condition.",
            onChange: function (vals) { c.v1 = vals.join(", "); v1.value = c.v1; drawNotes(); o.onChange("conds"); } }).el);
        }
        if (isIn) kids.push(el("span", { "class": "mr-hint", text: "separate with commas" }));
        if (isOff) kids.push(el("span", { "class": "mr-hint" }, ["filters only — ", el("b", { "class": "mr-mono", text: c.col }), " is not a column here"]));
        if (isPat) kids.push(el("span", { "class": "mr-hint", text: "% any run of characters, _ one character; case is ignored. For a literal one write !% or !_." }));
        kids.push(el("button", { type: "button", "class": "btn ghost sm", "aria-label": "Remove this condition", title: "Remove this condition",
          onclick: function () { rows.splice(i, 1); redraw(); o.onChange("conds"); } }, [M.icon("trash", 14)]));
        list.appendChild(el("div", { "class": "mr-cond" }, kids));
      });
      drawNotes();
    }
    function drawNotes() {
      var rows = o.rows(); M.clear(notes);
      if (rows.some(function (c) { return !c.col; })) notes.appendChild(el("div", { "class": "notice warn small", text: "A row with no column is ignored." }));
      var eqCols = rows.filter(function (c) { return c.col && c.op === "eq"; }).map(function (c) { return c.col; });
      var stacked = uniq(eqCols.filter(function (v) { return eqCols.indexOf(v) !== eqCols.lastIndexOf(v); }));
      if (stacked.length) notes.appendChild(el("div", { "class": "notice small" }, [el("span", {}, [el("b", { text: stacked.join(", ") }),
        ": several Equals rows on one column are read as any of those values, not all of them at once. Is any of with a comma-separated list says the same thing in one row, and filters the rows before they are totalled."])]));
      if (rows.some(function (c) { return c.col === "DT" && c.op !== IN_OP; })) notes.appendChild(el("div", { "class": "notice warn small" }, [el("span", {}, [el("b", { text: "A condition on DATE holds fixed dates. " }),
        "The report's own window is the From / To, and a schedule re-resolves that on every run - so a fixed DATE condition will not move with it, and the report will return nothing once the two stop overlapping. Use From / To for the range; use a DATE condition only to carve a fixed slice out of it."])]));
    }
    redraw();
    return { el: card, redraw: redraw };
  }

  // ══ THE RESULT GRID (DFS ResultGrid) on MIS.Grid: horizontal / vertical / period, formatted per DFS ═══════
  function resultGrid(box, o) {
    var cur = null, cols = [], lay = "horizontal", bi = -1, mi = -1, pi = -1, pend = false, g;
    function index(d) {
      cur = d; cols = d.columns || []; lay = d.layout || "horizontal";
      bi = lay === "vertical" ? cols.indexOf(VERT_BLOCK) : -1; mi = lay === "vertical" ? cols.indexOf(VERT_MEAS) : -1;
      pi = lay === "period" ? cols.indexOf(PERIOD_COL) : -1;
    }
    function jOf(c) { return cols.indexOf(c); }
    // The value columns are everything to the RIGHT of the last label column on either turn (the engine
    // guarantees group-by, then BLOCK + MEASURE or PERIOD, then the values).
    function isVal(c) { var j = jOf(c); return lay === "vertical" ? j > mi : lay === "period" ? j > pi : isMetric(c); }
    function headOf(c) {
      var j = jOf(c);
      if (lay === "vertical") return j <= mi ? c : c === VERT_AS_AT ? "As At" : (WIN_LABEL[c] || c);
      if (lay === "period") return j < pi ? c : j === pi ? "Period" : periodHead(c);
      return headLabel(c);
    }
    function perMap(c) { return (cur && cur.period && cur.period.cols && cur.period.cols[c]) || null; }
    function perRep(c) { var m = perMap(c); if (!m) return c; var k = Object.keys(m); return k.length ? m[k[0]] : c; }
    function headTitle(c) {
      if (lay === "period" && jOf(c) > pi) {
        var m = perMap(c); if (!m || !Object.keys(m).length) return c;
        return c + "  —  " + Object.keys(m).map(function (f) { return (FAM_LABEL[f] || f) + ": " + headLabel(m[f]).split(" · ").pop(); }).join(", ");
      }
      if (isRefCol(c)) return c + "  —  Day Ref: the SP_AKS_DAY_REF_LOAD logic (VBRP sale, ET_STOCK_DATA 0001/0006 stock, each store's own LY date) recomputed at article grain and aggregated to this grouping. A real total.";
      if (isMonthCol(c)) return c + "  —  Month Plan: the whole of " + (monthLabel(c.slice(2, c.indexOf("__"))) || "that month") + " from the Plan Live 360 snapshot, not a window of days. Blank means that plan has no row for this key; 0 means it says zero.";
      if (isGrcCol(c)) return c + "  —  GRC: the goods-receipt ledger over this window's days. Qty and Val are NET - the source signs its own reversals and returns - and Gross less Returns is exactly the net.";
      if (isPoCol(c)) return c + "  —  PO: the OPEN order book as it stands now, not a window. That view holds only lines still pending, so a received line has left it - which is why there is no “PO raised MTD” reading of it.";
      return c;
    }
    function headTone(c) {
      var ck = lay === "period" ? perRep(c) : c;
      return isRefCol(ck) ? "var(--s3)" : isMonthCol(ck) ? "var(--s4)" : isGrcCol(ck) ? "var(--s2)" : isPoCol(ck) ? "var(--s1)" : "";
    }
    // A TURNED value cell is formatted from ITS ROW: the vertical sheet rebuilds the wide name from its two label
    // columns, the period sheet looks it up by the row's family (res.period, sent by the engine for exactly this).
    function cellText(c, v, r) {
      var j = jOf(c);
      if (lay === "vertical" && j === bi) return v == null || v === "" ? "–" : (BLOCK_LABEL[v] || String(v));
      if (lay === "vertical" && j === mi) return v == null || v === "" ? "–" : vertMeasLabel(r[bi], v);
      if (lay === "period" && j === pi) return v == null || v === "" ? "–" : periodLabel(v);
      var key = c;
      if (lay === "vertical" && isVal(c)) key = vertRowKey(r[bi], r[mi]);
      else if (lay === "period" && isVal(c)) {
        var fam = cur.period && cur.period.rows ? cur.period.rows[r[pi]] : null, m = perMap(c);
        key = (m && fam && m[fam]) || c;
      }
      return fmtCell(key, v, o.scaled());
    }
    function decorate() {
      if (!cur || !g) return;
      var ths = g.el.querySelectorAll("thead tr.cols th"), vis = g.columns();
      for (var i = 0; i < ths.length && i < vis.length; i++) {
        var c = vis[i], t = headTitle(c), lb = ths[i].querySelector(".lbl");
        ths[i].title = t;
        if (lb) { lb.title = t + "\n\nClick to sort."; lb.style.color = headTone(c); }
      }
    }
    g = M.Grid(box, {
      id: o.id, title: o.title || "", limit: PAGE_SIZE, pageSizes: [100, 200, 500, 1000, 5000], tools: o.tools, exports: o.exports,
      fetch: function (gs, signal) { return o.fetch(gs, signal).then(function (d) { index(d || {}); return d; }); },
      isKey: function (c) { return !isVal(c); },
      format: function (c) { return isVal(c) ? "dec" : "text"; },
      label: headOf,
      groups: function (vis) {
        if (lay !== "horizontal" || !cur || !vis.some(isMetric)) return null;
        var out = [], last = null;
        vis.forEach(function (c) {
          var b = isMetric(c) ? (BLOCK_OF[c.slice(0, 2)] || "") : "";
          if (last && last.label === b) last.span++; else { last = { label: b, span: 1 }; out.push(last); }
        });
        return out;
      },
      cell: function (td, c, v, r) {
        var t = cellText(c, v, r); td.textContent = t;
        if (t.length > 18) td.title = t; else td.removeAttribute("title");
        if (isVal(c)) td.classList.add("num");
        if (v === null || v === undefined || v === "") td.classList.add("muted");
      },
      values: function (c, gs) {
        if (lay === "vertical" && (c === VERT_BLOCK || c === VERT_MEAS)) return Promise.resolve(vertVocab(cur && cur.definition, c));
        if (lay === "period" && c === PERIOD_COL) return Promise.resolve(Object.keys((cur && cur.period && cur.period.rows) || {}).map(function (p) { return { value: p, label: periodLabel(p) }; }));
        if (isVal(c)) return Promise.reject(new Error("a total has no list of values - use the condition above it"));
        return o.values(c, gs);
      },
      onData: function (d, st) { decorate(); if (o.onData) o.onData(d, st); }
    });
    // Redraws that do not refetch (column resize, the Columns panel) keep the header tones and notes.
    if (window.MutationObserver) new MutationObserver(function () {
      if (pend) return; pend = true; requestAnimationFrame(function () { pend = false; decorate(); });
    }).observe(g.el, { childList: true, subtree: true });
    function reformat() {   // the K / L switch: re-format the cells on screen without another Snowflake run
      var data = g.data(), rows = data.rows || [], raw = data.rawColumns || [], vis = g.columns();
      [].forEach.call(g.el.querySelectorAll("tbody tr"), function (tr, i) {
        var r = rows[i]; if (!r) return;
        [].forEach.call(tr.children, function (td, k) { var c = vis[k]; if (c) td.textContent = cellText(c, Array.isArray(r) ? r[raw.indexOf(c)] : r[c], r); });
      });
    }
    return { grid: g, reformat: reformat, current: function () { return cur; }, isVal: isVal };
  }
  function klTool(get, set) {
    var cb = el("input", { type: "checkbox", checked: !!get() });
    cb.addEventListener("change", function () { set(cb.checked); });
    return el("label", { "class": "row small", style: { gap: "5px", cursor: "pointer" }, title: "Show values in lakhs (V) and thousands (Q). Day counts, counts, cover days and percentages are never scaled." }, [cb, "K / L"]);
  }

  // ══ PAGE 1: /mis - the gallery, the workbook and the builder ═══════════════════════════════════════════
  M.page("reports", {
    render: function (ctx) {
      var me = ctx.me || { can: {} };
      var edit = M.qs("edit"), copy = M.qs("copy"), isNew = M.qs("new");
      M.setHeader({ title: "MIS Reports", sub: "Build a report by choosing what to group by and which measures to show. Nothing is stored per report - every run re-aggregates from the day-wise sale and stock facts, so a saved report can never drift from its source.",
        crumbs: [{ label: "Reports", href: "/mis" }],
        tiles: [{ k: "Reports", skel: true }, { k: "Mine", skel: true }, { k: "Shared", skel: true }, { k: "In the sidebar", skel: true }] });
      return Promise.all([loadCatalogue(), listReports()]).then(function (a) {
        if (!ctx.alive()) return;
        var cat = a[0] || {}, reports = a[1];
        if (edit || copy || isNew) {
          if (!me.can.manage) { M.toast("Building or changing a report needs the MIS manage permission.", "bad"); return listView(ctx, cat, reports); }
          var src = null;
          if (edit || copy) {
            src = reports.filter(function (r) { return r.slug === (edit || copy); })[0];
            if (!src) { M.toast("There is no saved report at /mis/r/" + (edit || copy) + ".", "bad"); return listView(ctx, cat, reports); }
            if (edit && !isMine(src, me)) { M.toast("“" + src.name + "” was created by " + src.createdBy + "; only they can change it. This is a copy you can save as your own.", "bad"); return builderView(ctx, cat, src, "copy"); }
          }
          return builderView(ctx, cat, src, edit ? "edit" : copy ? "copy" : "new");
        }
        return listView(ctx, cat, reports);
      });
    }
  });

  // ── the report gallery + multi-report workbook ──
  function listView(ctx, cat, reports) {
    var me = ctx.me || { can: {} };
    var st = { q: "", who: M.store.get("mr.who", "all") };
    var book = { sheets: [], from: yday(), to: yday(), name: "" };
    function headerTiles() {
      var mine = reports.filter(function (r) { return isMine(r, me); }).length, shared = reports.filter(function (r) { return r.isShared; }).length;
      M.setHeader({
        tiles: [{ k: "Reports", v: M.fmt(reports.length, "int"), s: "you can open" }, { k: "Mine", v: M.fmt(mine, "int"), s: "created by you" },
          { k: "Shared", v: M.fmt(shared, "int"), s: "with everyone" },
          { k: "In the sidebar", v: M.fmt(reports.filter(function (r) { return r.showInSidebar !== false; }).length, "int"), s: "listed under Reports" }],
        actions: [me.can.manage ? el("button", { type: "button", "class": "btn primary", onclick: function () { M.go("/mis?new=1"); } }, [M.icon("plus", 15), "New report"]) : null,
          el("button", { type: "button", "class": "btn", onclick: function () { reload(); } }, [M.icon("refresh", 15), "Refresh"])]
      });
    }
    function reload() {
      return listReports().then(function (r) { if (!ctx.alive()) return; reports = r; book.sheets = book.sheets.filter(function (s) { return reports.some(function (x) { return x.slug === s.slug; }); }); headerTiles(); drawCards(); drawBook(); },
        function (e) { M.toast(e.message, "bad"); });
    }
    headerTiles();

    // filter bar
    var search = el("input", { type: "search", placeholder: "Name, URL, description or owner", style: { minWidth: "280px" } });
    search.addEventListener("input", M.debounce(function () { st.q = search.value.trim().toLowerCase(); drawCards(); }, 200));
    var seg = el("div", { "class": "seg", role: "group", "aria-label": "Whose reports" });
    [["all", "All"], ["mine", "Mine"], ["shared", "Shared with me"], ["private", "Private"]].forEach(function (w) {
      seg.appendChild(el("button", { type: "button", "aria-pressed": String(st.who === w[0]), text: w[1], onclick: function () {
        st.who = w[0]; M.store.set("mr.who", st.who); [].forEach.call(seg.children, function (b, i) { b.setAttribute("aria-pressed", String(i === [].indexOf.call(seg.children, this))); }, this); drawCards(); } }));
    });
    var countEl = el("span", { "class": "muted small" });
    ctx.main.appendChild(el("div", { "class": "card" }, el("div", { "class": "fbar" }, [fld("Search", search), fld("Show", seg), el("span", { "class": "spacer" }), countEl])));

    var bookBox = el("div", { "class": "card mr-sticky" });
    if (me.can.export) ctx.main.appendChild(bookBox);
    var gal = el("div", { "class": "rgrid" });
    ctx.main.appendChild(gal);

    function summary(d) {
      d = d || {};
      var s = ((d.group_by || []).join(" × ") || "grand total") + " · " + plural((d.windows || []).length, "window") + " · " + plural((d.measures || []).length, "measure");
      if ((d.stock_tags || []).length) s += " · " + plural(d.stock_tags.length, "stock tag");
      if ((d.ref_measures || []).length) s += " · " + d.ref_measures.length + " Day Ref";
      if ((d.plan_measures || []).length) s += " · " + d.plan_measures.length + " Sale Plan";
      if ((d.month_measures || []).length) s += " · " + d.month_measures.length + " Month Plan × " + plural((d.plan_months || []).length, "month");
      if ((d.grc_measures || []).length) s += " · " + d.grc_measures.length + " GRC";
      if ((d.po_measures || []).length) s += " · " + d.po_measures.length + " PO";
      return s;
    }
    function shown() {
      return reports.filter(function (r) {
        var mine = isMine(r, me);
        if (st.who === "mine" && !mine) return false;
        if (st.who === "shared" && (mine || !r.isShared)) return false;
        if (st.who === "private" && r.isShared) return false;
        if (!st.q) return true;
        return [r.name, r.slug, r.description, r.createdBy].join(" ").toLowerCase().indexOf(st.q) >= 0;
      });
    }
    function pickedIdx(slug) { for (var i = 0; i < book.sheets.length; i++) if (book.sheets[i].slug === slug) return i; return -1; }
    function togglePick(r) { var i = pickedIdx(r.slug); if (i >= 0) book.sheets.splice(i, 1); else book.sheets.push(sheetRow(r)); drawCards(); drawBook(); }
    function remove(r) {
      M.confirm("Delete “" + r.name + "”?", "It disappears from everyone's sidebar and its published SILVER view is dropped. The definition is kept, so it can be restored from the database if this was a mistake.", "Delete", true).then(function (ok) {
        if (!ok) return;
        M.api("DELETE", API + "/reports/" + encodeURIComponent(r.reportId)).then(function () {
          M.toast("Deleted “" + r.name + "”.", "ok"); M.refreshSidebarReports(); reload();
        }, function (e) { M.toast(e.message, "bad"); });
      });
    }
    function drawCards() {
      M.clear(gal); var list = shown();
      countEl.textContent = list.length === reports.length ? plural(reports.length, "report") : list.length + " of " + plural(reports.length, "report");
      if (!reports.length) {
        gal.appendChild(el("div", { "class": "card", style: { gridColumn: "1 / -1" } }, el("div", { "class": "card-b stack", style: { alignItems: "center", textAlign: "center", padding: "40px" } }, [
          M.icon("report", 30), el("h2", { style: { margin: 0, fontSize: "16px" }, text: "No reports yet" }),
          el("p", { "class": "muted", style: { margin: 0 }, text: me.can.manage ? "New report starts one: pick what to group by, the windows and measures, then preview and save." : "Reports others share with you will appear here." }),
          me.can.manage ? el("button", { type: "button", "class": "btn primary", onclick: function () { M.go("/mis?new=1"); } }, [M.icon("plus", 15), "New report"]) : null])));
        return;
      }
      if (!list.length) { gal.appendChild(el("div", { "class": "notice", style: { gridColumn: "1 / -1" }, text: "No report matches the search and the filter above." })); return; }
      list.forEach(function (r) {
        var mine = isMine(r, me), pi = pickedIdx(r.slug), href = "/mis/r/" + encodeURIComponent(r.slug);
        var top = el("div", { "class": "top" });
        if (me.can.export) {
          var cb = el("input", { type: "checkbox", checked: pi >= 0, title: "Include in the multi-report workbook", "aria-label": "Include " + r.name + " in the workbook" });
          cb.addEventListener("change", function () { togglePick(r); });
          top.appendChild(cb);
          if (pi >= 0) top.appendChild(el("span", { "class": "mr-badge", title: "Sheet number in the workbook", text: pi + 1 }));
        }
        top.appendChild(el("h3", {}, el("a", { href: href, text: r.name, title: r.name })));
        top.appendChild(el("span", { "class": "chip " + (r.isShared ? "ok" : "grey"), title: r.isShared ? "Shared with everyone" : "Only you can see this", text: r.isShared ? "Shared" : "Private" }));
        var acts = el("div", { "class": "acts" }, [
          el("a", { "class": "btn sm", href: href }, [M.icon("play", 13), "Open"]),
          me.can.manage ? el("button", { type: "button", "class": "btn sm ghost", title: "Start a new report from this one", onclick: function () { M.go("/mis?copy=" + encodeURIComponent(r.slug)); } }, [M.icon("copy", 13), "Copy"]) : null,
          mine && me.can.manage ? el("button", { type: "button", "class": "btn sm ghost", onclick: function () { M.go("/mis?edit=" + encodeURIComponent(r.slug)); } }, [M.icon("edit", 13), "Edit"]) : null,
          el("span", { "class": "spacer" }),
          mine && me.can.manage ? el("button", { type: "button", "class": "btn sm ghost", style: { color: "var(--bad)" }, onclick: function () { remove(r); } }, [M.icon("trash", 13), "Delete"]) : null]);
        gal.appendChild(el("div", { "class": "rcard" + (pi >= 0 ? " picked" : "") }, [top,
          r.description ? el("p", { text: r.description }) : null,
          el("div", { "class": "sum", text: summary(r.definition) }),
          el("div", { "class": "foot" }, [el("span", { text: "by " + (r.createdBy || "–") + (mine ? " (you)" : "") }),
            r.updatedAt ? el("span", { title: M.fmtDate(r.updatedAt), text: "· updated " + M.ago(r.updatedAt) }) : null,
            r.showInSidebar === false ? el("span", { text: "· not in the sidebar" }) : null,
            (r.definition && r.definition.layout && r.definition.layout !== "horizontal") ? el("span", { text: "· " + layoutName(r.definition.layout) + " layout" }) : null]),
          r.view ? el("button", { type: "button", "class": "mr-view", title: "Published as " + r.view + " - click to copy", onclick: function () {
            copyText(r.view).then(function (ok) { M.toast(ok ? "View name copied." : "Could not copy - " + r.view, ok ? "ok" : "bad"); }); } }, [r.view]) : null,
          acts]));
      });
    }
    // Multi-report workbook: one sheet per ticked report, in the order ticked (move to reorder). One From / To for
    // the whole book: tabs covering different ranges is how the wrong numbers get compared.
    function drawBook() {
      if (!me.can.export) return;
      M.clear(bookBox);
      var n = book.sheets.length;
      var allB = el("button", { type: "button", "class": "btn ghost sm", text: "Tick all shown", onclick: function () {
        shown().forEach(function (r) { if (pickedIdx(r.slug) < 0) book.sheets.push(sheetRow(r)); }); drawCards(); drawBook(); } });
      var clrB = n ? el("button", { type: "button", "class": "btn ghost sm", text: "Clear", onclick: function () { book.sheets = []; drawCards(); drawBook(); } }) : null;
      bookBox.appendChild(el("div", { "class": "card-h" }, [M.icon("down", 16), el("h2", { text: n ? plural(n, "report") + " → one workbook, a sheet each" : "Multi-report workbook" }),
        el("span", { "class": "note", text: n ? "" : "Tick reports below to export them into one Excel workbook." }), el("span", { "class": "spacer" }), allB, clrB]));
      if (!n) return;
      var from = el("input", { type: "date", value: book.from }), to = el("input", { type: "date", value: book.to });
      var name = el("input", { type: "text", value: book.name, placeholder: "MIS workbook", style: { width: "200px" } });
      from.addEventListener("change", function () { book.from = from.value; }); to.addEventListener("change", function () { book.to = to.value; });
      name.addEventListener("input", function () { book.name = name.value; });
      var go = el("button", { type: "button", "class": "btn primary" }, [M.icon("down", 15), "Export workbook"]);
      var prob = el("div", { "class": "notice bad small hidden" });
      go.addEventListener("click", function () {
        if (!book.from || !book.to || book.from > book.to) { M.toast("Pick a valid From / To.", "bad"); return; }
        var bad = sheetsProblem(book.sheets); if (bad) { M.toast(bad, "bad"); return; }
        M.action(go, "Building " + plural(n, "sheet") + "…", function () {
          return M.download("POST", "/api/v1/mis/book", { name: book.name.trim() || undefined, from_date: book.from, to_date: book.to,
            sheets: book.sheets.map(function (s) { return { slug: s.slug, sheet: sheetSafe(s.sheet) }; }) }, "MIS_REPORTS_" + book.from + "_to_" + book.to + ".xlsx");
        }, function (f) { return "Downloaded " + f; }).catch(function () { /* toasted */ });
      });
      var tb = el("tbody");
      book.sheets.forEach(function (s, i) {
        var rep = reports.filter(function (r) { return r.slug === s.slug; })[0] || { name: s.slug };
        var inp = el("input", { type: "text", value: s.sheet, maxlength: String(SHEET_MAX), "aria-label": "Sheet name", title: "Excel tab name: up to 31 characters, none of : \\ / ? * [ ]" });
        function mark() { var nm = sheetSafe(s.sheet).toUpperCase(); inp.classList.toggle("bad", !nm || book.sheets.some(function (o, j) { return j !== i && sheetSafe(o.sheet).toUpperCase() === nm; })); }
        inp.addEventListener("input", function () { s.sheet = inp.value; mark(); var p = sheetsProblem(book.sheets); prob.textContent = p; prob.classList.toggle("hidden", !p); });
        inp.addEventListener("blur", function () { s.sheet = sheetSafe(inp.value) || sheetSafe(rep.name); inp.value = s.sheet; mark(); });
        mark();
        function mv(d) { var j = i + d; if (j < 0 || j >= book.sheets.length) return; var t = book.sheets[i]; book.sheets[i] = book.sheets[j]; book.sheets[j] = t; drawCards(); drawBook(); }
        tb.appendChild(el("tr", {}, [
          el("td", { "class": "nowrap" }, [el("b", { style: { color: "var(--brand)", display: "inline-block", width: "20px" }, text: i + 1 }),
            el("button", { type: "button", "class": "btn ghost sm", "aria-label": "Move up", title: "Move up", disabled: i === 0, onclick: function () { mv(-1); }, text: "↑" }),
            el("button", { type: "button", "class": "btn ghost sm", "aria-label": "Move down", title: "Move down", disabled: i === book.sheets.length - 1, onclick: function () { mv(1); }, text: "↓" })]),
          el("td", { title: s.slug, text: rep.name }), el("td", {}, inp),
          el("td", {}, el("button", { type: "button", "class": "btn ghost sm", "aria-label": "Remove from the workbook", title: "Remove from the workbook", onclick: function () { book.sheets.splice(i, 1); drawCards(); drawBook(); } }, [M.icon("x", 13)]))]));
      });
      var p0 = sheetsProblem(book.sheets); prob.textContent = p0; prob.classList.toggle("hidden", !p0);
      bookBox.appendChild(el("div", { "class": "fbar" }, [fld("From", from), fld("To", to), fld("Workbook name", name), el("span", { "class": "spacer" }), go]));
      bookBox.appendChild(el("div", { "class": "card-b stack" }, [
        el("div", { style: { border: "1px solid var(--line)", borderRadius: "8px", overflow: "auto" } }, el("table", { "class": "mr-tbl" }, [
          el("thead", {}, el("tr", {}, [el("th", { style: { width: "120px" }, text: "#" }), el("th", { text: "Report" }), el("th", { text: "Sheet name" }), el("th", { style: { width: "40px" } })])), tb])),
        prob,
        note("Tabs are written top to bottom - the arrows reorder, and the number on each card follows. Every report is exported whole, as saved - the filters on a report's own page are not applied here. To put this on a timer, use Dump Scheduler → MIS Report and pick several reports.")]));
    }
    drawBook(); drawCards();
  }

  // ── the builder ──
  function blankDef() {
    return { group_by: ["ST_CD", "MAJ_CAT"], windows: ["TD", "MTD"], measures: ["SALE_V", "SALE_Q", "GM_V"], stock_tags: [], ref_measures: [],
      plan_measures: [], plan_months: [], month_measures: [], grc_measures: [], po_measures: [], filters: {}, col_filters: [], dim_filters: [],
      tag_plants: {}, family_plants: {}, layout: "horizontal" };
  }
  // Start FROM the saved definition, so every field the builder does not edit (dc_only_stock, v2r_plants, ...) is kept on save.
  function normDef(d) {
    ["group_by", "windows", "measures", "stock_tags", "ref_measures", "plan_measures", "plan_months", "month_measures", "grc_measures",
     "po_measures", "col_filters", "dim_filters"].forEach(function (k) { if (!Array.isArray(d[k])) d[k] = []; });
    ["filters", "tag_plants", "family_plants"].forEach(function (k) { if (!d[k] || typeof d[k] !== "object" || Array.isArray(d[k])) d[k] = {}; });
    if (!d.layout) d.layout = "horizontal";
    return d;
  }

  function builderView(ctx, cat, src, mode) {
    var dims = flatDims(cat), plants = cat.plants || [];
    var E = mode === "new" ? { reportId: null, name: "", slug: "", description: "", isShared: false, showInSidebar: true, definition: blankDef() }
      : { reportId: mode === "edit" ? src.reportId : null, name: mode === "copy" ? src.name + " (copy)" : src.name, slug: mode === "edit" ? src.slug : "",
          description: src.description || "", isShared: mode === "edit" ? !!src.isShared : false, showInSidebar: src.showInSidebar !== false,
          definition: normDef(clone(src.definition)), view: mode === "edit" ? src.view : null };
    E.from = yday(); E.to = yday();
    var d = E.definition;
    // The Conditions panel edits ONE flat list; condsToDef folds it back into filters + dim_filters + col_filters on save.
    var cond2 = condsFromDef(d);
    // Which columns have a filter panel: dimensions with an "is any of" row (except ART_TYPE, which has its own
    // panel) and measures with a non-list condition.
    var filterCols = uniq(cond2.filter(function (r) { return r.col && r.col !== QUICK_DIM && (inc(dims, r.col) ? r.op === IN_OP : !IS_LIST(r.op)); }).map(function (r) { return r.col; }));
    var dirty = false, scaled = !!M.store.get("mr.scaled", false), pv = null;
    var title = mode === "edit" ? "Edit report" : mode === "copy" ? "Copy of a report" : "New report";

    function setDef(k, v) { d[k] = v; changed("def"); }
    // ── derived: the columns this definition produces, grouped like the panels (DFS condCols) ──
    function condCols() {
      var gb = (d.group_by || []).slice();
      var offBy = Object.keys(cat.dims || {}).map(function (g) { return [g, (cat.dims[g] || []).filter(function (c) { return gb.indexOf(c) < 0; })]; }).filter(function (x) { return x[1].length; });
      var off = []; offBy.forEach(function (x) { off = off.concat(x[1]); });
      var measGroups = {};
      function cross(name, left, right, f) { var out = []; (left || []).forEach(function (a) { (right || []).forEach(function (b) { out.push(f(a, b)); }); }); if (out.length) measGroups[name] = out; }
      cross("Sale", d.windows, d.measures, function (w, m) { return "S_" + w + "__" + m; });
      cross("Stock tags", d.stock_tags, ["STOCK_V", "STOCK_Q"], function (t, m) { return "K_" + t + "__" + m; });
      cross("Day Ref (LY)", d.windows, d.ref_measures, function (w, m) { return "R_" + w + "__" + m; });
      cross("Sale Plan", d.windows, d.plan_measures, function (w, m) { return "P_" + w + "__" + m; });
      cross("Month Plan (360)", d.plan_months, d.month_measures, function (t, m) { return "M_" + t + "__" + m; });
      cross("GRC", d.windows, d.grc_measures, function (w, m) { return "G_" + w + "__" + m; });
      cross("PO", [PO_PERIOD], d.po_measures, function (t, m) { return "O_" + t + "__" + m; });
      var meas = []; Object.keys(measGroups).forEach(function (k) { meas = meas.concat(measGroups[k]); });
      return { dims: gb, off: off, offBy: offBy, meas: meas, measGroups: measGroups, isDim: function (c) { return inc(gb, c) || inc(off, c); } };
    }
    // ── the condition rows behind the filter panels ──
    function inRowAt(col) { for (var i = 0; i < cond2.length; i++) if (cond2[i].col === col && cond2[i].op === IN_OP) return i; return -1; }
    function inRowVals(col) { var i = inRowAt(col); return i < 0 ? [] : splitVals(cond2[i].v1); }
    function setInRow(col, vals) {
      var i = inRowAt(col);
      if (!vals.length) { if (i >= 0) cond2.splice(i, 1); }
      else { var row = { col: col, op: IN_OP, v1: vals.join(", "), v2: "" }; if (i < 0) cond2.push(row); else Object.assign(cond2[i], row); }
      changed("filters");
    }
    function measRowAt(col) { for (var i = 0; i < cond2.length; i++) if (cond2[i].col === col && !IS_LIST(cond2[i].op)) return i; return -1; }
    function measRow(col) { var i = measRowAt(col); return i < 0 ? { col: col, op: "gte", v1: "", v2: "" } : cond2[i]; }
    function setMeasRow(col, patch) { var i = measRowAt(col); if (i < 0) cond2.push(Object.assign({ col: col, op: "gte", v1: "", v2: "" }, patch)); else Object.assign(cond2[i], patch); changed("filters"); }
    function dropMeasRow(col) { var i = measRowAt(col); if (i >= 0) cond2.splice(i, 1); changed("filters"); }
    function activeFilters() {
      var out = {};
      cond2.forEach(function (c) { if (c.op !== IN_OP || !inc(dims, c.col)) return; var v = splitVals(c.v1); if (v.length) out[c.col] = v; });
      return out;
    }
    // Which fact the value lists are read from follows the report (a stock-only article type has never sold).
    function artShape() {
      var tags = uniq(d.stock_tags || []).sort(), sale = (d.measures || []).length > 0 || !tags.length;
      return { tags: tags, sale: sale, key: (sale ? "S" : "") + "|" + tags.join(",") };
    }
    var fvCache = {};
    function filterValues(col) {
      var others = activeFilters(); delete others[col];
      var s = artShape(), key = col + "|" + E.from + "|" + E.to + "|" + s.key + "|" + JSON.stringify(others);
      if (fvCache[key]) return Promise.resolve(fvCache[key]);
      return valuesOf({ definition: { group_by: [col], windows: s.sale ? ["TD"] : [], measures: s.sale ? ["SALE_V"] : [], stock_tags: s.tags, filters: others },
        column: col, from_date: E.from, to_date: E.to }).then(function (v) { v = v.map(String).filter(function (x) { return x.trim() !== ""; }); fvCache[key] = v; return v; });
    }
    function filterKey(col) { var o = activeFilters(); delete o[col]; return col + "|" + E.from + "|" + E.to + "|" + artShape().key + "|" + JSON.stringify(o); }
    function condValues(col) { return valuesOf({ definition: { group_by: [col], windows: ["TD"], measures: ["SALE_V"] }, column: col, from_date: E.from, to_date: E.to }); }

    // ── masthead ──
    var saveBtn = el("button", { type: "button", "class": "btn primary" }, [M.icon("save", 15), "Save"]);
    var pvBtn = el("button", { type: "button", "class": "btn" }, [M.icon("play", 15), "Preview"]);
    var backBtn = el("button", { type: "button", "class": "btn" }, [M.icon("x", 15), "Cancel"]);
    backBtn.addEventListener("click", function () {
      if (!dirty) { M.go("/mis"); return; }
      M.confirm("Leave without saving?", "The changes to this report have not been saved.", "Discard changes", true).then(function (ok) { if (ok) M.go("/mis"); });
    });
    M.setHeader({ title: title, sub: mode === "edit" ? "Changing “" + src.name + "” - saved for everyone who opens, exports or schedules it."
        : mode === "copy" ? "A new report, starting from “" + src.name + "”. Saving it does not change the original."
        : "Pick what to group by and which measures to show, preview it, then save.",
      crumbs: [{ label: "Reports", href: "/mis" }, { label: title }], actions: [backBtn, pvBtn, saveBtn] });
    function updateTiles() {
      var cc = condCols(), blocks = Object.keys(cc.measGroups).map(function (k) { return k.split(" (")[0] + " " + cc.measGroups[k].length; });
      M.setHeader({ tiles: [
        { k: "Group by", v: (d.group_by || []).length ? (d.group_by || []).length : "Total", s: (d.group_by || []).join(" × ") || "one grand-total row" },
        { k: "Value columns", v: M.fmt(cc.meas.length, "int"), s: blocks.join(" · ") || "pick some measures", cls: cc.meas.length ? "" : "down" },
        { k: "Conditions", v: cond2.filter(function (c) { return c.col; }).length, s: "saved with the report" },
        { k: "Layout", v: layoutName(d.layout), s: (E.isShared ? "shared" : "private") + (E.showInSidebar !== false ? " · in the sidebar" : "") }] });
    }

    // ── 1. report details ──
    var nameIn = el("input", { type: "text", value: E.name, placeholder: "e.g. Store × Maj-Cat daily sale", maxlength: "120" });
    var slugIn = el("input", { type: "text", value: E.slug, placeholder: "store-majcat-daily", "class": "mr-mono", pattern: "[a-z0-9-]*" });
    var descIn = el("input", { type: "text", value: E.description, placeholder: "What this report answers", maxlength: "1000" });
    nameIn.addEventListener("input", function () { E.name = nameIn.value; dirty = true; });
    slugIn.addEventListener("input", function () { E.slug = slugIn.value.trim().toLowerCase(); dirty = true; });
    descIn.addEventListener("input", function () { E.description = descIn.value; dirty = true; });
    ctx.main.appendChild(el("div", { "class": "card" }, [el("div", { "class": "card-h" }, [el("h2", { text: "Report details" }),
        E.view ? el("span", { "class": "note" }, ["Published as ", el("span", { "class": "mr-mono", text: E.view }), " (republished on every save)"]) : null]),
      el("div", { "class": "card-b grid12" }, [el("div", { "class": "c4" }, fld("Report name", nameIn)), el("div", { "class": "c4" }, fld("URL (optional - from the name if blank)", slugIn)),
        el("div", { "class": "c4" }, fld("Description", descIn))])]));
    [nameIn, slugIn, descIn].forEach(function (x) { x.style.width = "100%"; });

    // ── 2. the pickers ──
    var PL = {};
    function pl(key, title, groups, opts) {
      opts = opts || {};
      PL[key] = pickList({ title: title, groups: groups, value: d[key] || [], note: opts.note, labelOf: opts.labelOf, height: "280px",
        onChange: function (v) { setDef(key, v); } });
      return PL[key];
    }
    var stockNote = "Every storage location and LQUA family, plus the totals and the picks. " + Object.keys(PICK_LABELS).filter(function (t) { return inc(cat.stockTags, t); })
      .map(function (t) { return t + " = " + ((cat.picks || {})[t] || []).join(" / ") + " - the same rack under the storage type each DC names it by, as one column instead of two half-empty ones"; }).join(". ");
    var plArt = pickList({ title: "Art type", groups: { "": [] }, value: inRowVals(QUICK_DIM), height: "280px", emptyText: "Loading article types…",
      note: "A SCOPE picker, not a column picker: the report is built from the article types ticked, on every run, export and scheduled dump, and no column is added. It saves as a condition, so it also shows in Conditions below.",
      onChange: function (v) { setInRow(QUICK_DIM, v); } });
    var picks = el("div", { "class": "mr-picks" }, [
      pl("group_by", "Group by", cat.dims || {}).el,
      pl("windows", "Windows", { "": cat.windows || [] }).el,
      pl("measures", "Measures", { "": cat.measures || [] }).el,
      pl("stock_tags", "Stock tags", { "": cat.stockTags || [] }, { note: stockNote }).el,
      pl("ref_measures", "Day Ref (LY)", { "": cat.refMeasures || [] }, { note: "The SP_AKS_DAY_REF_LOAD logic (VBRP sale, ET_STOCK_DATA 0001/0006 stock, each store's own LY date) re-run at article grain and aggregated to this report's grouping - so it works at every grouping, and it totals." }).el,
      pl("plan_measures", "Sale Plan", { "": cat.planMeasures || [] }, { note: cat.planNote || "The saved month plan from the latest approved session, spread over the same windows. Planned at store × category: below that grain the columns come back blank." }).el,
      pl("plan_months", "Month", { "Relative to the report date": (cat.monthTokens || []).map(function (t) { return t.id; }), "Named month": (cat.months || []).map(function (m) { return m.id; }) },
        { note: "The Month Plan block's own axis. The relative months move with the report's To date, so a saved report and its schedule read the month they run in; a named month stays fixed." }).el,
      pl("month_measures", "Month Plan (360)", cat.monthMeasures || {}, { note: cat.monthNote || "Sale, Space, Transfer In and Purchase as Plan Live 360 publishes them, one measure of one picked month - not windowed." }).el,
      pl("grc_measures", "GRC", { "": cat.grcMeasures || [] }, { note: cat.grcNote }).el,
      pl("po_measures", "PO (open)", { "": cat.poMeasures || [] }, { note: cat.poNote }).el,
      plArt.el]);
    ctx.main.appendChild(el("div", { "class": "card" }, [el("div", { "class": "card-h" }, [el("h2", { text: "What the report shows" }),
      el("span", { "class": "note", text: "Click to pick; drag down a list to pick several; Shift-click picks a range. Group by keeps the order you pick in - it becomes the column order." })]),
      el("div", { "class": "card-b" }, picks)]));
    var artCache = {}, artKey = null, artOpts = [];
    function refreshArt() {
      var s = artShape(), key = E.from + "|" + E.to + "|" + s.key, sel = inRowVals(QUICK_DIM);
      function paint(err) {
        plArt.setGroups({ "": artOpts.concat(sel.filter(function (x) { return artOpts.indexOf(x) < 0; })) }); plArt.set(sel);
        plArt.setFoot(err ? "Could not load the list - " + err : !artOpts.length ? "Loading article types…" : sel.length ? "Narrowed to " + sel.length + " of " + artOpts.length + " article types - no column is added."
          : "Narrows the rows. Picks nothing = every article type, and no column either way.", err ? "bad" : "");
      }
      if (key === artKey) { paint(); return; }
      artKey = key;
      if (artCache[key]) { artOpts = artCache[key]; paint(); return; }
      artOpts = []; paint();
      valuesOf({ definition: { group_by: [QUICK_DIM], windows: s.sale ? ["TD"] : [], measures: s.sale ? ["SALE_V"] : [], stock_tags: s.tags }, column: QUICK_DIM, from_date: E.from, to_date: E.to })
        .then(function (v) { v = v.map(String).filter(function (x) { return x.trim() !== ""; }); artCache[key] = v; if (artKey === key && ctx.alive()) { artOpts = v; paint(); } },
          function (e) { if (artKey === key && ctx.alive()) paint(e.message); });
    }

    // ── 3. stock plants (column narrowing per tag / per family) ──
    var stockCard = el("div", { "class": "card hidden" }), stockSig = null;
    ctx.main.appendChild(stockCard);
    function famOf(t) { return (cat.tagFamily || {})[t] || null; }
    function drawStock() {
      var tags = d.stock_tags || [], sig = JSON.stringify([tags, d.family_plants, d.tag_plants, d.v2r_plants]);
      stockCard.classList.toggle("hidden", !tags.length);
      if (sig === stockSig) return; stockSig = sig;
      M.clear(stockCard); if (!tags.length) return;
      var fams = uniq(tags.map(famOf).filter(Boolean).concat(Object.keys(d.family_plants || {})).concat((d.v2r_plants || []).length ? ["V2R"] : []));
      var row = el("div", { "class": "row" });
      fams.forEach(function (f) {
        // V2R reads its legacy field until this control is touched; touching it moves the pick into family_plants.
        var cur = (d.family_plants || {})[f] || (f === "V2R" ? (d.v2r_plants || []) : []);
        row.appendChild(plantPicker(f + "_*", plants, cur, function (p) {
          var next = Object.assign({}, d.family_plants || {}); if (p.length) next[f] = p; else delete next[f];
          d.family_plants = next; if (f === "V2R") d.v2r_plants = []; changed("plants");
        }, true).el);
      });
      if (fams.length) row.appendChild(el("span", { "class": "muted", text: "|" }));
      tags.forEach(function (t) {
        row.appendChild(plantPicker(t, plants, (d.tag_plants || {})[t] || [], function (p) {
          var next = Object.assign({}, d.tag_plants || {}); if (p.length) next[t] = p; else delete next[t];   // an empty pick is "no narrowing", never stored
          d.tag_plants = next; changed("plants");
        }, false).el);
      });
      stockCard.appendChild(el("div", { "class": "card-h" }, [M.icon("stock", 16), el("h2", { text: "Stock plants" }), el("span", { "class": "note", text: "which plants each stock column counts - columns only, no rows are dropped" })]));
      stockCard.appendChild(el("div", { "class": "card-b stack" }, [row,
        note("Saved with the report and applied to those columns on every run, export and scheduled dump. A family pick covers every tag in it - ET the storage locations, V2R / V2S / SDC the warehouses - and a tag's own pick replaces its family's for that tag rather than narrowing it further, so two picks can never blank a column between them. Nothing picked = every plant.")]));
    }

    // ── 4. filters (panels over the condition list) ──
    var addFilterBtn = el("button", { type: "button", "class": "btn sm" }, [M.icon("plus", 14), "Add a filter"]);
    var chips = el("div", { "class": "row", style: { gap: "6px" } }), fEmpty = note("None - the report returns every row. A filter narrows it for everyone who opens, exports or schedules it.");
    var panels = el("div", { "class": "mr-picks" }), fNote = note("");
    fNote.textContent = "Saved with the report, and every panel also appears as a row in Conditions below - one filter, two places to read it. A dimension is matched inside the scan, so no column is added and the totals are of the filtered rows; several dimensions are ANDed and the values within one are ORed, and each list shows what the other filters and the preview dates leave. A measure is tested after the grouping, on the total as the report prints it - and filtering on a stock tag ticks it under Stock tags, since the column has to exist before there is a total to test.";
    ctx.main.appendChild(el("div", { "class": "card" }, [el("div", { "class": "card-h" }, [M.icon("filter", 16), el("h2", { text: "Filters" }), addFilterBtn, chips]),
      el("div", { "class": "card-b stack" }, [fEmpty, panels, fNote])]));
    addFilterBtn.addEventListener("click", function () {
      var cc = condCols(), groups = {};
      Object.keys(cat.dims || {}).forEach(function (g) { var cs = (cat.dims[g] || []).filter(function (c) { return c !== QUICK_DIM; }); if (cs.length) groups[g] = cs; });
      Object.keys(cc.measGroups).forEach(function (g) { groups[g] = cc.measGroups[g]; });
      var stockCols = []; (cat.stockTags || []).forEach(function (t) { stockCols.push("K_" + t + "__STOCK_V", "K_" + t + "__STOCK_Q"); });
      if (stockCols.length) groups["Stock tags"] = stockCols;
      var p = pickList({ title: "Filter on", groups: groups, value: filterCols, labelOf: headLabel, height: "320px",
        note: "Pick what to filter on; each gets its own panel. A DIMENSION gets a tick-list of its values and narrows the rows before they are totalled, adding no column - tick it under Group by instead if you want it printed. A MEASURE gets an operator and a number, tested on the finished total. Every stock tag is offered here; filtering on one ticks it under Stock tags, because a total has to be a column before it can be tested.",
        onChange: function (next) {
          var tags = (d.stock_tags || []).slice();
          next.forEach(function (c) { if (c.indexOf("K_") !== 0) return; var t = c.slice(2, c.indexOf("__")); if (t && tags.indexOf(t) < 0) tags.push(t); });
          if (tags.length !== (d.stock_tags || []).length) { d.stock_tags = tags; PL.stock_tags.set(tags); }
          filterCols = next; changed("filters");
        } });
      M.popover(addFilterBtn, [el("div", { "class": "mr-pop", style: { width: "320px" } }, p.el)]);
    });
    var dimPanels = {}, measPanels = {};
    function dimPanel(col) {
      var opts = [], busy = false, err = "", q = "", value = [], key = null;
      var spin = el("span", { "class": "spin hidden" }), cnt = el("span", { "class": "n" });
      var search = el("input", { type: "search", "aria-label": "Search " + col + " values" });
      var allB = el("button", { type: "button", text: "All" }), clrB = el("button", { type: "button", "class": "clr", text: "Clear" });
      var list = el("div", { "class": "mr-pl-b", style: { maxHeight: "180px" } }), foot = el("p", { "class": "mr-pl-f hidden" });
      var root = el("div", { "class": "mr-pl" }, [el("div", { "class": "mr-pl-h" }, [el("span", { "class": "t", text: col, title: col }), spin, cnt,
        el("button", { type: "button", "class": "x", "aria-label": "Remove the " + col + " filter", title: "Remove the " + col + " filter",
          onclick: function () { filterCols = filterCols.filter(function (y) { return y !== col; }); setInRow(col, []); } }, [M.icon("x", 13)])]),
        el("div", { "class": "mr-pl-s" }, [M.icon("search", 12), search, allB, el("span", { text: "|" }), clrB]), list, foot]);
      function matches() { var ql = q.trim().toLowerCase(); return ql ? opts.filter(function (o) { return String(o).toLowerCase().indexOf(ql) >= 0; }) : opts; }
      function draw() {
        spin.classList.toggle("hidden", !busy); cnt.textContent = value.length + " picked"; search.placeholder = opts.length + " values";
        var m = matches(), shown = m.slice(0, 300);
        allB.disabled = !m.length; clrB.disabled = !value.length;
        M.clear(list);
        shown.forEach(function (v) {
          var on = value.indexOf(v) >= 0;
          list.appendChild(el("button", { type: "button", "class": "mr-opt" + (on ? " on" : ""), title: v,
            onclick: function () { setInRow(col, on ? value.filter(function (x) { return x !== v; }) : value.concat([v])); } }, [el("i"), el("span", { "class": "l", text: v })]));
        });
        value.filter(function (v) { return opts.indexOf(v) < 0; }).forEach(function (v) {
          list.appendChild(el("button", { type: "button", "class": "mr-opt stale on", title: "Picked, but not among the values this range and the other filters leave",
            onclick: function () { setInRow(col, value.filter(function (x) { return x !== v; })); } }, [el("i"), el("span", { "class": "l", text: v })]));
        });
        if (busy && !opts.length) list.appendChild(note("loading…"));
        if (!busy && !err && !m.length) list.appendChild(note("no values"));
        if (m.length > shown.length) list.appendChild(note("showing 300 of " + m.length + " - type to narrow", "warn"));
        var f = err ? ["Could not load the list - " + err, "bad"] : value.length > MAX_IN_VALUES ? [value.length + " picked - the engine keeps the first " + MAX_IN_VALUES + ". Filter on something above this instead.", "warn"] : null;
        foot.textContent = f ? f[0] : ""; foot.className = "mr-pl-f" + (f ? " " + f[1] : " hidden");
      }
      function load(k) {
        busy = true; err = ""; draw();
        filterValues(col).then(function (v) { if (key !== k) return; opts = v; busy = false; draw(); }, function (e) { if (key !== k) return; opts = []; err = e.message; busy = false; draw(); });
      }
      search.addEventListener("input", function () { q = search.value; draw(); });
      allB.addEventListener("click", function () { setInRow(col, uniq(value.concat(matches()))); });
      clrB.addEventListener("click", function () { setInRow(col, []); });
      return { el: root, update: function (v, k) { value = v; if (k !== key) { key = k; load(k); } else draw(); } };
    }
    function measPanel(col) {
      var opSel = el("select", { "aria-label": "Operator" }, NUM_OPS.map(function (o) { return el("option", { value: o[0], text: o[1] }); }));
      var v1 = el("input", { type: "number", "aria-label": "Value", "class": "tnum" }), v2 = el("input", { type: "number", "aria-label": "To", placeholder: "to", "class": "tnum" });
      var fill = note(""), stale = note("This report no longer produces this column - the filter is ignored until it does.", "warn");
      var root = el("div", { "class": "mr-pl" }, [el("div", { "class": "mr-pl-h" }, [el("span", { "class": "t", text: headLabel(col), title: col }),
        el("button", { type: "button", "class": "x", "aria-label": "Remove the " + headLabel(col) + " filter", title: "Remove this filter",
          onclick: function () { filterCols = filterCols.filter(function (y) { return y !== col; }); dropMeasRow(col); } }, [M.icon("x", 13)])]),
        el("div", { "class": "mr-meas" }, [opSel, el("div", { "class": "row", style: { gap: "6px", flexWrap: "nowrap" } }, [v1, v2]), fill, stale])]);
      opSel.addEventListener("change", function () { setMeasRow(col, { op: opSel.value }); });
      v1.addEventListener("input", function () { setMeasRow(col, { v1: v1.value }); });
      v2.addEventListener("input", function () { setMeasRow(col, { v2: v2.value }); });
      return { el: root, update: function (row, isStale) {
        var needs2 = row.op === "between", needs0 = row.op === "blank" || row.op === "nonblank";
        if (document.activeElement !== opSel) opSel.value = row.op;
        if (document.activeElement !== v1) v1.value = row.v1 == null ? "" : row.v1;
        if (document.activeElement !== v2) v2.value = row.v2 == null ? "" : row.v2;
        v1.placeholder = needs2 ? "from" : "value"; v1.classList.toggle("hidden", needs0); v2.classList.toggle("hidden", !needs2);
        var filled = needs0 || (String(row.v1 == null ? "" : row.v1) !== "" && (!needs2 || String(row.v2 == null ? "" : row.v2) !== ""));
        fill.textContent = filled ? "tested on the finished total, after the grouping" : "no value yet - nothing is filtered until there is one";
        fill.className = "mr-hint " + (filled ? "" : "warn"); fill.style.color = filled ? "" : "var(--warn)";
        stale.classList.toggle("hidden", !isStale);
      } };
    }
    var OP_SYM = { gte: "≥", gt: ">", lte: "≤", lt: "<", eq: "=", ne: "≠" };
    function drawFilters() {
      var cc = condCols(), af = activeFilters();
      var mf = cond2.filter(function (c) {
        if (!c.col || cc.isDim(c.col) || IS_LIST(c.op)) return false;
        if (c.op === "blank" || c.op === "nonblank") return true;
        if (String(c.v1 == null ? "" : c.v1) === "") return false;
        return c.op !== "between" || String(c.v2 == null ? "" : c.v2) !== "";
      });
      M.clear(chips);
      Object.keys(af).forEach(function (k) {
        var v = af[k];
        chips.appendChild(el("span", { "class": "chip", title: v.join(", ") }, [el("b", { text: k }), el("span", { "class": "t", text: v.length === 1 ? v[0] : v.length + " values" }),
          el("button", { type: "button", "aria-label": "Remove the " + k + " filter", text: "×", onclick: function () { setInRow(k, []); } })]));
      });
      mf.forEach(function (c) {
        var t = c.op === "between" ? c.v1 + " – " + c.v2 : c.op === "blank" ? "is blank" : c.op === "nonblank" ? "is not blank" : (OP_SYM[c.op] || c.op) + " " + c.v1;
        chips.appendChild(el("span", { "class": "chip mr-chip-m", title: c.col + " - tested on the finished total, after the grouping" }, [el("b", { text: headLabel(c.col) }), el("span", { "class": "t tnum", text: t }),
          el("button", { type: "button", "aria-label": "Remove this filter", text: "×", onclick: function () { dropMeasRow(c.col); } })]));
      });
      var any = filterCols.length || Object.keys(af).length || mf.length;
      fEmpty.classList.toggle("hidden", !!any); fNote.classList.toggle("hidden", !any); panels.classList.toggle("hidden", !filterCols.length);
      // reconcile the panels with filterCols (kept, not rebuilt, so a search typed in one survives)
      Object.keys(dimPanels).forEach(function (c) { if (filterCols.indexOf(c) < 0 || !cc.isDim(c)) { dimPanels[c].el.remove(); delete dimPanels[c]; } });
      Object.keys(measPanels).forEach(function (c) { if (filterCols.indexOf(c) < 0 || cc.isDim(c)) { measPanels[c].el.remove(); delete measPanels[c]; } });
      filterCols.forEach(function (col) {
        if (cc.isDim(col)) { var p = dimPanels[col] || (dimPanels[col] = dimPanel(col)); panels.appendChild(p.el); p.update(inRowVals(col), filterKey(col)); }
        else { var m = measPanels[col] || (measPanels[col] = measPanel(col)); panels.appendChild(m.el); m.update(measRow(col), cc.meas.indexOf(col) < 0); }
      });
    }

    // ── 5. warnings: blocks this grouping cannot fill ──
    var warnBox = el("div", { "class": "stack" });
    ctx.main.appendChild(warnBox);
    function drawWarn() {
      M.clear(warnBox);
      var condDims = cond2.map(function (c) { return String(c.col || "").trim(); }).filter(function (c) { return inc(dims, c); });
      var picked = uniq((d.group_by || []).concat(Object.keys(d.filters || {}), condDims));
      function warn(head, kids) { warnBox.appendChild(el("div", { "class": "notice warn small" }, [M.icon("info", 16), el("span", {}, [el("b", { text: head + " " })].concat(kids))])); }
      function mono(t) { return el("span", { "class": "mr-mono", text: t }); }
      if ((d.plan_measures || []).length) {
        var tooFine = picked.filter(function (c) { return !inc(cat.planDims, c); });
        if (tooFine.length) warn("Sale Plan will be blank:", [(cat.planNote || "Sale Plan is planned at store × category.") + " This report " + ((d.group_by || []).some(function (c) { return !inc(cat.planDims, c); }) ? "groups" : "filters") + " by ", mono(tooFine.join(", ")), ", which the plan has no value for."]);
      }
      var mStore = (d.month_measures || []).filter(function (m) { return !inc(cat.monthRdcMeasures, m); }), mRdc = (d.month_measures || []).filter(function (m) { return inc(cat.monthRdcMeasures, m); });
      var mFine = mStore.length ? picked.filter(function (c) { return !inc(cat.monthDims, c); }) : [];
      var rFine = mRdc.length ? picked.filter(function (c) { return !inc(cat.monthRdcDims, c); }) : [];
      if (mFine.length) warn("Month Plan will be blank:", ["Sale, Space and Transfer In are planned at store × category. This report " + ((d.group_by || []).some(function (c) { return !inc(cat.monthDims, c); }) ? "groups" : "filters") + " by ", mono(mFine.join(", ")), ", which the plan has no value for."]);
      if (rFine.length) warn("Purchase will be blank:", ["The purchase plan has no store in it - it is made at RDC × category. Group only by ", mono("RDC_CD / RDC_NM / MAJ_CAT"), " to read it; this report also uses ", mono(rFine.join(", ")), "."]);
      if ((d.po_measures || []).length && inc(picked, "DT")) warn("PO will be blank:", ["The open order book is a position as it stands now, not a history - that view holds only lines still pending, so a received line has left it. There is no per-day reading of it, and this report uses ", mono("DT"), ". Drop DATE to read the PO columns, or filter ", mono("DOC_DT"), " in Snowflake for “raised since X and still open”."]);
      if ((d.month_measures || []).length && cat.monthSnapshot && cat.monthSnapshot.refreshedAt)
        warnBox.appendChild(el("div", { "class": "notice small" }, [M.icon("info", 16), el("span", {}, ["Month Plan columns read the Plan Live 360 snapshot taken ", el("b", { text: cat.monthSnapshot.refreshedAt }), ". Refresh it from the Plan Live 360 page to pick up newer saves."])]));
      if (!(d.group_by || []).length) warn("One row:", ["Nothing is selected under Group by, so this report will be a single grand-total row."]);
    }

    // ── 6. options, preview dates and save ──
    var pFrom = el("input", { type: "date", value: E.from }), pTo = el("input", { type: "date", value: E.to });
    pFrom.addEventListener("change", function () { E.from = pFrom.value; changed("dates"); });
    pTo.addEventListener("change", function () { E.to = pTo.value; changed("dates"); });
    var lt = layoutToggle(d.layout, function (v) { setDef("layout", v); }, LAYOUT_TIP + "\n\nSaved WITH the report, so it opens the way it was asked for. The report page can still flip it for one visit. The published SILVER view stays horizontal whatever this is set to.");
    var optCard = el("div", { "class": "card" }, [el("div", { "class": "card-h" }, [el("h2", { text: "Preview & save" }), el("span", { "class": "note", text: "the preview dates are for checking only - the report itself runs on whatever dates it is opened or scheduled with" })]),
      el("div", { "class": "fbar", style: { borderBottom: "0", borderRadius: "0 0 var(--radius) var(--radius)" } }, [fld("Preview from", pFrom), fld("to", pTo), lt.el,
        el("div", { "class": "stack", style: { gap: "4px" } }, [
          check(el("span", {}, ["ET stock: stores only ", el("span", { "class": "muted", text: "(columns only - no rows are dropped)" })]), d.store_only_stock, function (v) { setDef("store_only_stock", v); },
            "ET stock tags (0001 ... PSA1) count plants whose ST_TYP is STORE and nothing else.\n\nRows are NOT removed - the DCs, hubs and offices keep their rows, their sale figures and every other column.\n\nThe LQUA (LQ_*, LQL_*, LQB_*), PRD, PTL and HUBPRD tags are NOT affected: that stock lives at DCs and hubs by nature.\n\nA plant-type split you picked yourself (0099_DC) is left alone."),
          check(el("span", {}, ["Share with everyone ", el("span", { "class": "muted", text: "(otherwise only you see it)" })]), E.isShared, function (v) { E.isShared = v; changed("meta"); }),
          check(el("span", {}, ["Show in sidebar ", el("span", { "class": "muted", text: "(off keeps it on this page only)" })]), E.showInSidebar !== false, function (v) { E.showInSidebar = v; changed("meta"); })])])]);
    ctx.main.appendChild(optCard);

    // ── 7. saved conditions ──
    var conds = condPanel({
      rows: function () { return cond2; }, onChange: function (src2) { changed(src2 || "conds"); },
      groupedDims: function () { return condCols().dims; }, offBy: function () { return condCols().offBy; },
      measures: function () { return condCols().meas.map(function (m) { return { id: m, label: headLabel(m) }; }); },
      loadValues: condValues, valuesKey: function () { return E.from + "|" + E.to; },
      note: "kept with the report - applied on every run, export and scheduled dump",
      emptyText: "No conditions - the report returns every row. Add one to keep it narrowed for everyone who opens or schedules it. A condition may name any column in the catalogue, grouped or not."
    });
    ctx.main.appendChild(conds.el);

    // ── 8. preview ──
    var pvNote = el("span", { "class": "note" }), pvBody = el("div", {}, el("div", { "class": "dg-empty" }, [M.icon("play", 26), el("div", { style: { marginTop: "6px" }, text: "Nothing run yet. Preview runs this definition - including the conditions being edited - over the preview dates." })]));
    ctx.main.appendChild(el("div", { "class": "card" }, [el("div", { "class": "card-h" }, [el("h2", { text: "Preview" }), pvNote]), pvBody]));
    var pvCache = {};
    function liveDef() { return Object.assign({}, d, condsToDef(cond2, dims)); }
    function runPreview() {
      if (!pv) {
        M.clear(pvBody);
        pv = resultGrid(pvBody, {
          title: "Preview", tools: [klTool(function () { return scaled; }, function (v) { scaled = v; M.store.set("mr.scaled", v); pv.reformat(); })],
          scaled: function () { return scaled; },
          fetch: function (gs, signal) {
            // A header list on a column the report already scopes NARROWS it - never widens it (the rule _resolve applies).
            var saved = condsToDef(cond2, dims), filters = Object.assign({}, saved.filters), cf = [];
            Object.keys(gs.filters).forEach(function (k) {
              var v = gs.filters[k]; if (!v || !v.length) return;
              if (inc(dims, k)) filters[k] = filters[k] ? v.filter(function (x) { return inc(filters[k], x); }) : v;
              else cf.push({ col: k, op: IN_OP, vals: v });
            });
            gs.conds.forEach(function (x) { cf.push({ col: x.col, op: x.op, v1: x.v1, v2: x.v2 }); });
            return M.post(API + "/run", { definition: Object.assign({}, d, saved, { filters: filters }), col_filters: cf.length ? cf : undefined,
              sort_col: gs.sort_col || undefined, sort_dir: gs.sort_dir || undefined, from_date: E.from, to_date: E.to, page: gs.page, limit: gs.limit }, { signal: signal });
          },
          values: function (col) {
            var defn = liveDef(), key = col + "|" + E.from + "|" + E.to + "|" + JSON.stringify(defn);
            if (pvCache[key]) return Promise.resolve(pvCache[key]);
            return valuesOf({ definition: defn, column: col, from_date: E.from, to_date: E.to }).then(function (v) { pvCache[key] = v; return v; });
          },
          onData: function (res, gs) {
            var narrowed = Object.keys(gs.filters).length || gs.conds.length;
            pvNote.textContent = M.fmt(res.total, "int") + " rows · " + M.fmtDate(E.from) + (E.to !== E.from ? " – " + M.fmtDate(E.to) : "") + (narrowed ? " · narrowed by the column menu - not saved with the report" : "");
          }
        });
      }
      pv.grid.reload(true);
      if (optCard.scrollIntoView) pvBody.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    pvBtn.addEventListener("click", runPreview);
    saveBtn.addEventListener("click", function () {
      if (!E.name.trim()) { M.toast("Give the report a name.", "bad"); nameIn.focus(); return; }
      if (E.slug && !/^[a-z0-9][a-z0-9-]*$/.test(E.slug)) { M.toast("The URL must be lowercase letters, digits and dashes.", "bad"); slugIn.focus(); return; }
      M.action(saveBtn, "Saving…", function () {
        return M.post(API + "/reports", { report_id: E.reportId || null, name: E.name.trim(), slug: E.slug || undefined, description: E.description,
          definition: liveDef(), is_shared: !!E.isShared, show_in_sidebar: E.showInSidebar !== false });
      }).then(function (res) {
        dirty = false;
        M.toast("Saved “" + res.name + "”.", "ok");
        if (res.ok === false) M.toast("Saved, but the SILVER view " + (res.view || "") + " could not be published: " + (res.error || "unknown error") + ". The report works in the app either way.", "bad");
        M.refreshSidebarReports();
        if (ctx.alive()) M.go("/mis");
      }, function () { /* toasted */ });
    });

    // ── change propagation ──
    var pend = null;
    function changed(src2) {
      dirty = true; updateTiles();
      clearTimeout(pend);
      pend = setTimeout(function () {
        if (!ctx.alive()) return;
        drawStock(); drawFilters(); drawWarn(); refreshArt();
        if (src2 !== "conds") conds.redraw();
      }, 40);
    }
    updateTiles(); drawStock(); drawFilters(); drawWarn(); refreshArt();
  }

  // ══ PAGE 2: /mis/r/<slug> - one saved report ═══════════════════════════════════════════════════════════
  M.page("report", {
    render: function (ctx) {
      var slug = ctx.route.slug, me = ctx.me || { can: {} };
      var st = { from: yday(), to: yday(), layout: null, art: [], levels: [] };
      var scaled = !!M.store.get("mr.scaled", false);
      var res = null, info = null, gsLast = null, tpl = { exists: false, nameRow: 1, dataRow: 2 }, valCache = {};
      M.setHeader({ title: slug, sub: "", crumbs: [{ label: "Reports", href: "/mis" }, { label: slug }],
        tiles: [{ k: "Rows", skel: true }, { k: "Period", skel: true }, { k: "Layout", skel: true }, { k: "Grouped by", skel: true }, { k: "Owner", skel: true }] });

      function filteredCount(gs) { gs = gs || { filters: {}, conds: [] }; return uniq(Object.keys(gs.filters).filter(function (k) { return (gs.filters[k] || []).length; }).concat(gs.conds.map(function (c) { return c.col; }))).length + (st.art.length ? 1 : 0); }
      function paintHeader() {
        var defn = (res && res.definition) || (info && info.definition) || {};
        var saved = (info && info.definition && info.definition.layout) || null, lay = st.layout || (res && res.layout) || saved || "horizontal";
        var name = (res && res.name) || (info && info.name) || slug, nf = filteredCount(gsLast), n = days(st.from, st.to);
        var mine = isMine(info, me);
        M.setHeader({ title: name,
          sub: "Grouped by " + ((defn.group_by || []).join(" × ") || "nothing - one grand-total row") + (info && info.description ? " · " + info.description : ""),
          crumbs: [{ label: "Reports", href: "/mis" }, { label: name }],
          tiles: [{ k: "Rows", v: res ? M.fmt(res.total, "int") : "–", s: nf ? "filtered on " + plural(nf, "column") : "the whole report", cls: nf ? "down" : "" },
            { k: "Period", v: plural(n, "day"), s: M.fmtDate(st.from) + (st.to !== st.from ? " – " + M.fmtDate(st.to) : "") },
            { k: "Layout", v: layoutName(lay), s: st.layout && saved && st.layout !== saved ? "this visit only - saved " + layoutName(saved) : "as saved" },
            { k: "Grouped by", v: (defn.group_by || []).length || "Total", s: (defn.group_by || []).join(" × ") || "one grand-total row" },
            { k: "Owner", v: info ? info.createdBy || "–" : "–", s: info ? (info.isShared ? "shared with everyone" : "private") + (info.showInSidebar === false ? " · not in the sidebar" : "") : "" },
            { k: "Updated", v: info && info.updatedAt ? M.ago(info.updatedAt) : "–", s: info && info.updatedAt ? M.fmtDate(info.updatedAt) : "" }],
          actions: [mine && me.can.manage ? el("a", { "class": "btn", href: "/mis?edit=" + encodeURIComponent(slug) }, [M.icon("edit", 15), "Edit report"]) : null,
            me.can.manage ? el("a", { "class": "btn", href: "/mis?copy=" + encodeURIComponent(slug), title: "Start a new report from this one" }, [M.icon("copy", 15), "Copy"]) : null,
            el("a", { "class": "btn", href: "/mis" }, [M.icon("report", 15), "All reports"])] });
      }
      M.get(API + "/defs?slugs=" + encodeURIComponent(slug)).then(function (r) { info = (r.reports || [])[0] || null; if (ctx.alive()) { paintHeader(); paintArtTitle(); } }, function () { /* the run says the same */ });

      // Templates (DFS: a formatted Excel per report, columns matched by name)
      var tplLine = el("div", { "class": "fchips" });
      function loadTpl() {
        return M.get("/api/v1/mis/template/" + encodeURIComponent(slug) + "/info").then(function (i) { Object.assign(tpl, i || {}); }, function () { tpl.exists = false; })
          .then(function () { if (ctx.alive()) paintTpl(); });
      }
      function paintTpl() {
        M.clear(tplLine);
        tplLine.appendChild(tpl.exists
          ? el("span", { "class": "mr-hint" }, ["Export template: names on row " + tpl.nameRow + ", data " + (tpl.dataRow ? "from row " + tpl.dataRow : "appended after the last row")
              + (tpl.mappedColumns ? " · " + tpl.mappedColumns + " columns mapped" : "") + (tpl.updatedBy ? " · last replaced by " + tpl.updatedBy : "") + (tpl.updatedAt ? " " + M.ago(tpl.updatedAt) : "")])
          : el("span", { "class": "mr-hint", text: "No export template uploaded for this report - Export to template is off until one is." }));
      }

      // ── filter bar ──
      var health = el("div");
      ctx.main.appendChild(health); M.rangeHealth(health, st.from, st.to);
      var dr = M.dateRange(st.from, st.to, function (v) {
        if (!v.from || !v.to) return;
        if (v.from > v.to) { M.toast("From is after To.", "bad"); return; }
        st.from = v.from; st.to = v.to; valCache = {}; art.reset(); M.rangeHealth(health, st.from, st.to); paintHeader(); rg.grid.reload(true);
      });
      var lt = layoutToggle("horizontal", function (v) { setLayout(v); }, LAYOUT_TIP + "\n\nTHIS VISIT ONLY. The report keeps the layout it was saved with, and so do the template export, the workbook and the nightly dump. CSV and Excel follow what is on screen.");
      var art = M.multiSelect("Art type", function () { return loadValues(QUICK_DIM, null); }, [], function (v) { st.art = v; rg.grid.reload(true); }, { allText: "All ART TYPE" });
      function paintArtTitle() {
        var d = (res && res.definition) || (info && info.definition) || {}, plan = ((d.plan_measures || []).length + (d.month_measures || []).length) > 0;
        art.el.title = "Narrows THIS RUN to the article types ticked. The report need not group by ART TYPE or carry it as a column at all - the filter is applied inside the scan. Exports follow it; the saved report, the schedule and the nightly dump do not."
          + (plan ? "\n\nThis report has Sale Plan / Month Plan columns. Neither plan is planned by article type, so they come back BLANK while this filter is on, rather than showing the unfiltered plan beside filtered sale." : "");
      }
      var lv = M.multiSelect("Split exports by", function () { return Promise.resolve(((res && res.definition && res.definition.group_by) || []).slice()); }, [], function (v) { st.levels = v; },
        { allText: "Whole report" });
      lv.el.title = "Empty = one file for the whole report. Pick a level for one file per value - several levels mean the COMBINATION: one file per pair that exists in the data, not one set of files per level. Downloaded in sequence (at most " + MAX_DOWNLOADS + "); for a bigger split use the Dump Scheduler.";
      var tplBtn = el("button", { type: "button", "class": "btn" }, [M.icon("upload", 15), "Template…"]);
      tplBtn.addEventListener("click", templateDialog);
      var dumpLink = me.can.dumps ? el("a", { "class": "btn", href: "/mis/dumps?new=mis_report&target=" + encodeURIComponent(slug), title: "Write this report to a folder on a timer (or right now) from the Dump Scheduler - one file, or one per store, maj-cat and so on." }, [M.icon("clock", 15), "Schedule a dump…"]) : null;
      var expStatus = el("span", { "class": "mr-hint", "aria-live": "polite" });
      var card = el("div", { "class": "card" });
      card.appendChild(el("div", { "class": "fbar" }, [dr.el, lt.el, art.el, me.can.export ? lv.el : null, el("span", { "class": "spacer" }), expStatus, tplBtn, dumpLink]));
      card.appendChild(tplLine);
      ctx.main.appendChild(card);
      paintTpl(); loadTpl();

      // ── the grid ──
      function dimsOfRes() { return ((res && res.keys) || []).concat((res && res.definition && res.definition.group_by) || []); }
      // Header ticks on a dimension go to `filters` (inside the scan, and the shape an export split reads); ticks on
      // anything else (BLOCK / MEASURE / PERIOD on a turned sheet) and every condition go to col_filters.
      function scopeOf(gs) {
        var filters = {}, cf = [], dd = dimsOfRes();
        Object.keys((gs && gs.filters) || {}).forEach(function (c) {
          var v = gs.filters[c]; if (!v || !v.length) return;
          if (inc(dd, c)) filters[c] = v.slice(); else cf.push({ col: c, op: IN_OP, vals: v.slice() });
        });
        ((gs && gs.conds) || []).forEach(function (x) { cf.push({ col: x.col, op: x.op, v1: x.v1, v2: x.v2 }); });
        if (st.art.length) filters[QUICK_DIM] = st.art.slice();
        return { filters: Object.keys(filters).length ? filters : undefined, col_filters: cf.length ? cf : undefined };
      }
      // The distinct values of one column, cached per column, range and the OTHER columns' filters.
      function loadValues(col, gs) {
        var f = Object.assign({}, scopeOf(gs).filters || {}); delete f[col];
        var key = [col, st.from, st.to, JSON.stringify(f)].join("|");
        if (valCache[key]) return Promise.resolve(valCache[key]);
        return valuesOf({ slug: slug, column: col, from_date: st.from, to_date: st.to, filters: Object.keys(f).length ? f : undefined })
          .then(function (v) { valCache[key] = v; return v; });
      }
      var gridCard = el("div", { "class": "card" });
      ctx.main.appendChild(gridCard);
      var rg = resultGrid(gridCard, {
        id: "mis-report:" + slug, title: "Rows",
        tools: [klTool(function () { return scaled; }, function (v) { scaled = v; M.store.set("mr.scaled", v); rg.reformat(); })],
        scaled: function () { return scaled; },
        fetch: function (gs, signal) {
          gsLast = gs;
          return M.post(API + "/run", Object.assign({ slug: slug, from_date: st.from, to_date: st.to, page: gs.page, limit: gs.limit,
            sort_col: gs.sort_col || undefined, sort_dir: gs.sort_dir || undefined, layout: st.layout || undefined }, scopeOf(gs)), { signal: signal });
        },
        values: function (col, gs) { return loadValues(col, gs); },
        exports: me.can.export ? [
          { label: "CSV - as on screen", fn: function (gs) { exportAs("csv", gs); } },
          { label: "Excel (.xlsx) - as on screen", fn: function (gs) { exportAs("xlsx", gs); } },
          { label: "Export to template (formatted Excel)", fn: function (gs) { exportAs("template", gs); } }] : [],
        onData: function (d, gs) {
          res = d; gsLast = gs;
          lt.set(st.layout || d.layout || "horizontal");
          var lvls = (d.definition && d.definition.group_by) || [];
          st.levels = st.levels.filter(function (l) { return inc(lvls, l); }); lv.set(st.levels); lv.reset();
          paintHeader(); paintArtTitle();
          var nf = filteredCount(gs);
          expStatus.textContent = nf ? "Exports carry these filters - " + M.fmt(d.total, "int") + " row(s)" : "";
        }
      });
      // Flipping the layout RENAMES every value column, so a header filter or sort on one stops naming anything that
      // exists; both are cleared with the turn. The grouping columns (and ART_TYPE) survive.
      function setLayout(v) {
        st.layout = v;
        var gs = rg.grid.state(), keys = (res && res.keys) || [], f = {};
        Object.keys(gs.filters).forEach(function (k) { if (inc(keys, k)) f[k] = gs.filters[k]; });
        rg.grid.setState({ filters: f, conds: gs.conds.filter(function (x) { return inc(keys, x.col); }), sort_col: inc(keys, gs.sort_col) ? gs.sort_col : null });
        paintHeader(); rg.grid.reload(true);
      }

      // ── exports ──
      function exportAs(kind, gs) {
        if (kind === "template" && !tpl.exists) { M.toast("No template uploaded for this report yet - use Template… to upload one.", "bad"); return; }
        var sc = scopeOf(gs);
        if (st.levels.length) { exportEach(kind, sc, gs); return; }
        var lay = st.layout || (res && res.layout) || "horizontal", shape = lay === "horizontal" ? "" : "_" + lay;
        var body = Object.assign({ slug: slug, from_date: st.from, to_date: st.to }, sc);
        // The formatted export is not given the layout: its template was mapped against the saved report's columns.
        if (kind !== "template" && st.layout) body.layout = st.layout;
        var url = kind === "csv" ? API + "/export" : kind === "xlsx" ? API + "/export-xlsx" : "/api/v1/mis/template-export";
        var name = kind === "template" ? slug + "_" + st.from + "_to_" + st.to + ".xlsx" : slug + shape + "_" + st.from + "_to_" + st.to + "." + (kind === "csv" ? "csv" : "xlsx");
        expStatus.textContent = kind === "csv" ? "Building CSV…" : "Building Excel…";
        dlAs("POST", url, body, name).then(function (n) { expStatus.textContent = ""; M.toast("Downloaded " + n, "ok"); }, function (e) { expStatus.textContent = ""; M.toast(e.message, "bad"); });
      }
      // One file per value (or combination) of the chosen level(s), each a filtered export the engine re-aggregates.
      function exportEach(kind, sc, gs) {
        var levels = st.levels.slice(), filters = sc.filters || {}, nf = filteredCount(gs);
        expStatus.textContent = "Finding " + levels.join(" + ") + " values…";
        M.post(API + "/values", { slug: slug, columns: levels, from_date: st.from, to_date: st.to }).then(function (v) {
          var jobs = ((v && v.combos) || []).filter(function (c) { return levels.every(function (l, j) { return !filters[l] || inc(filters[l], String(c[j])); }); });
          if (!jobs.length) throw new Error("No " + levels.join(" + ") + " values in this range" + (nf ? " once the column filters are applied" : ""));
          if (jobs.length > MAX_DOWNLOADS) throw new Error(jobs.length + " files is too many for a browser to download one by one - use Schedule a dump for a split this size.");
          expStatus.textContent = "";
          return M.confirm("Download " + plural(jobs.length, "file") + "?", "One file per " + levels.join(" + ") + (nf ? ", each carrying the filters on screen" : "") + ". Your browser will ask once to allow multiple downloads.", "Download").then(function (ok) {
            if (!ok) return;
            var i = 0;
            function safe(x) { return String(x == null ? "BLANK" : x).replace(/[\\/:*?"<>|]+/g, "_").slice(0, 40); }
            function next() {
              if (i >= jobs.length) return Promise.resolve();
              var combo = jobs[i++], fl = Object.assign({}, filters);
              levels.forEach(function (l, j) { fl[l] = [combo[j]]; });
              var stem = levels.map(function (l, j) { return l + "_" + safe(combo[j]); }).join("__");
              expStatus.textContent = i + "/" + jobs.length + " - " + levels.map(function (l, j) { return l + "=" + combo[j]; }).join(", ");
              var body = { slug: slug, from_date: st.from, to_date: st.to, filters: fl, col_filters: sc.col_filters };
              if (kind !== "template" && st.layout) body.layout = st.layout;
              var url = kind === "csv" ? API + "/export" : kind === "xlsx" ? API + "/export-xlsx" : "/api/v1/mis/template-export";
              return dlAs("POST", url, body, slug + "_" + stem + "." + (kind === "csv" ? "csv" : "xlsx")).then(next);
            }
            return next().then(function () { expStatus.textContent = ""; M.toast(plural(jobs.length, "file") + " downloaded.", "ok"); });
          });
        }).catch(function (e) { expStatus.textContent = ""; M.toast(e.message, "bad"); });
      }

      // ── template manager ──
      function templateDialog() {
        var stat = el("div", { "class": "stack", style: { gap: "6px" } }), result = el("div", { "class": "stack", style: { gap: "8px" } });
        var nameRow = el("input", { type: "number", min: "1", value: tpl.nameRow || 1, style: { width: "100px" } });
        var dataRow = el("input", { type: "number", min: "0", value: tpl.dataRow == null ? 2 : tpl.dataRow, style: { width: "100px" } });
        var file = el("input", { type: "file", accept: ".xlsx" });
        var dl = el("button", { type: "button", "class": "btn" }, [M.icon("down", 15), "Download current template"]);
        var up = el("button", { type: "button", "class": "btn primary" }, [M.icon("upload", 15), "Upload template"]);
        function paintStat() {
          M.clear(stat); dl.disabled = !tpl.exists;
          if (!tpl.exists) { stat.appendChild(el("div", { "class": "notice warn" }, [M.icon("info", 16), el("span", { text: "No template uploaded for this report yet. The CSV and Excel exports work without one." })])); return; }
          stat.appendChild(el("dl", { "class": "kv" }, [el("dt", { text: "Workbook" }), el("dd", { text: tpl.sourceFile || slug + ".xlsx" }),
            el("dt", { text: "Column names on" }), el("dd", { text: "row " + tpl.nameRow }),
            el("dt", { text: "Data written" }), el("dd", { text: tpl.dataRow ? "from row " + tpl.dataRow + " down, copying that row's formatting" : "after the last row of the sheet" }),
            el("dt", { text: "Columns mapped" }), el("dd", { text: tpl.mappedColumns == null ? "–" : String(tpl.mappedColumns) }),
            el("dt", { text: "Last replaced" }), el("dd", { text: (tpl.updatedBy || "–") + (tpl.updatedAt ? " · " + M.fmtDate(tpl.updatedAt) : "") })]));
        }
        dl.addEventListener("click", function () {
          M.action(dl, "Downloading…", function () { return dlAs("GET", "/api/v1/mis/template/" + encodeURIComponent(slug), undefined, slug + "-TEMPLATE.xlsx"); }, "Template downloaded.").catch(function () { /* toasted */ });
        });
        up.addEventListener("click", function () {
          var f = file.files && file.files[0];
          if (!f) { M.toast("Choose the .xlsx workbook first.", "bad"); file.focus(); return; }
          var nr = parseInt(nameRow.value, 10), dr2 = parseInt(dataRow.value, 10);
          if (!(nr >= 1) || !(dr2 >= 0) || (dr2 && dr2 <= nr)) { M.toast("The column-name row must be 1 or more; the data row must be 0 (append) or below the name row.", "bad"); return; }
          var fd = new FormData(); fd.append("file", f); fd.append("nameRow", String(nr)); fd.append("dataRow", String(dr2));
          M.clear(result);
          M.action(up, "Checking…", function () { return M.api("POST", "/api/v1/mis/template/" + encodeURIComponent(slug), fd); }).then(function (r) {
            result.appendChild(el("div", { "class": "notice ok" }, [M.icon("info", 16), el("span", { text: r.message || "Template saved." + (r.archived ? " The previous one was kept as " + r.archived + "." : "") })]));
            if (r.archived) result.appendChild(note("The previous template was kept as " + r.archived + "."));
            if ((r.unmatched || []).length) result.appendChild(el("div", { "class": "stack", style: { gap: "6px" } }, [
              el("b", { "class": "small", text: r.unmatched.length + " heading(s) on row " + r.nameRow + " match no column of this report - they are left empty:" }),
              el("div", { "class": "row", style: { gap: "5px" } }, r.unmatched.map(function (u) { return el("span", { "class": "chip warn", text: u }); }))]));
            var g = r.headerRowGuess;
            if (g && g.row && g.row !== r.nameRow) {
              result.appendChild(el("div", { "class": "notice warn" }, [M.icon("info", 16), el("span", {}, ["Row " + g.row + " names " + g.hit + " of its " + g.of + " headings as report columns - it looks like the real column-name row. ",
                el("button", { type: "button", "class": "btn sm", text: "Use row " + g.row, onclick: function () { nameRow.value = g.row; if (+dataRow.value && +dataRow.value <= g.row) dataRow.value = g.row + 1; M.toast("Row numbers updated - upload the file again to apply them."); } })])]));
            }
            file.value = "";
            loadTpl().then(paintStat);
          }, function (e) { result.appendChild(el("div", { "class": "notice bad", text: e.message })); });
        });
        var colsBox = null;
        var c0 = (res && res.columns) || [];
        if (c0.length && !st.layout) {
          var names = c0.filter(function (c) { return c !== "_ORD"; });
          colsBox = el("div", { "class": "stack", style: { gap: "6px" } }, [el("div", { "class": "section-title", text: "This report's column names" }),
            note("Type these on the column-name row of your sheet, in any order and with any formatting around them; columns you leave out are simply not written."),
            el("div", { "class": "row", style: { gap: "5px", maxHeight: "140px", overflow: "auto" } }, names.map(function (c) { return el("span", { "class": "chip grey mr-mono", text: c }); })),
            el("div", {}, el("button", { type: "button", "class": "btn sm", onclick: function () { copyText(names.join("\t")).then(function (ok) { M.toast(ok ? "Column names copied (tab-separated - paste into row " + nameRow.value + ")." : "Could not copy.", ok ? "ok" : "bad"); }); } }, [M.icon("copy", 13), "Copy as a header row"]))]);
        }
        var body = [stat,
          note("A template is your own formatted workbook. Export to template pours this report's rows into its first sheet: columns are matched BY NAME on the column-name row, and data is written from the data row down (0 = appended after the last row), copying that row's formatting."),
          me.can.manage ? el("div", { "class": "stack", style: { gap: "8px" } }, [el("div", { "class": "section-title", text: tpl.exists ? "Replace the template" : "Upload a template" }),
            el("div", { "class": "row", style: { alignItems: "flex-end" } }, [fld("Workbook (.xlsx)", file), fld("Column-name row", nameRow, "The row carrying this report's raw column names"),
              fld("First data row (0 = append)", dataRow, "The first row data is written to; its formatting is copied down")]), result])
            : note("Uploading a template needs the MIS manage permission."),
          colsBox];
        M.modal("Export template · " + ((res && res.name) || slug), body, [dl, me.can.manage ? up : null].filter(Boolean));
        paintStat();
      }

      return rg.grid.applyDefaultView().then(function () { if (ctx.alive()) return rg.grid.reload(true); });
    }
  });
})();

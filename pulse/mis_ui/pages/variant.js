// Variant Article Data (DFS /sf-mis-var-kpi, SfVarKpiReport.jsx): sale and stock on one row per DAY × store ×
// variant article (the 13-digit article number - one colour in one size), from the day-wise facts
// DAY_WISE_ST_VAR_SALE / DAY_WISE_ST_VAR_STOCK. Sale windows (S_<window>__<measure>) sit next to the stock storage
// locations (K_<tag>__<measure>); optional GRC (G_) and open-PO (O_PO__) blocks, the per-article Day Ref ACT columns
// (A__) and the category reference (R__). Subtotals over the WHOLE filtered result come from a separate call.
// API: /api/v1/mis/var-combined/{params, result, summary, totals, export, load, kpi-load}.
(function () {
  "use strict";
  var M = window.MIS, el = M.el, API = "/api/v1/mis/var-combined";

  // ── column vocabulary (verbatim from the DFS page) ──────────────────────────────────────────────
  var KEY_COLS_ARR = ["DT", "ST_CD", "ARTICLE_NUMBER", "GEN_ART_NUMBER", "GEN_ART_DESC", "COLOR", "SIZE_1", "MAJ_CAT", "SEG"];
  var LOC_FLAG = "LOC_IN_MASTER", PLANT_COL = "PLANT_TYPE";
  // Right-aligned like measures but never K/L-scaled: an MRP of 150 must not read as 0.00 L.
  var NUMERIC_ATTRS = set(["MRP", "AREA", "APF", "ASP", "ARTICLE_COST", "REF_GRP_NEW", "REF_GRP_OLD", "VENDOR_CODE", "MERGE_VENDOR_CODE"]);
  var ENRICH_FILTERS = [LOC_FLAG, "PLANT_TYPE", "RDC_CD", "HUB_CD", "CLUSTER", "STATE", "BK_FLD_STATUS", "DIV", "SUB_DIV", "REG_SEG", "ART_TYPE", "ART_STATUS",
                        "NEW_COLOR_DESC", "NEW_SIZE_DESC", "COLOR_CODE", "NEW_COLOR_CODE", "COLOR_MAP_DATE", "SIZE_MAP_DATE"];
  var LABELS = {
    LOC_IN_MASTER: "In store master", PLANT_TYPE: "Plant type", RDC_CD: "RDC", HUB_CD: "HUB", BK_FLD_STATUS: "Book-fold",
    SUB_DIV: "SUB-DIV", REG_SEG: "RNG SEG", ART_TYPE: "ART TYPE", MC_CODE: "MC CODE", MC_DESC: "MC DESC", MACRO_MVGR: "MACRO MVGR", MAIN_MVGR: "MAIN MVGR",
    VENDOR_NAME: "VENDOR", ARTICLE_NUMBER: "VARIANT ART", GEN_ART_NUMBER: "GEN ART", GEN_ART_DESC: "ARTICLE DESC", COLOR: "COLOUR", SIZE_1: "SIZE",
    NEW_COLOR_DESC: "NEW COLOUR", NEW_SIZE_DESC: "NEW SIZE", COLOR_CODE: "COLOUR CODE", NEW_COLOR_CODE: "NEW COLOUR CODE",
    COLOR_MAP_DATE: "COLOUR MAP DATE", SIZE_MAP_DATE: "SIZE MAP DATE", ART_STATUS: "ART STATUS", DT: "DATE",
    SALE_V: "Sale V", SALE_Q: "Sale Q", GM_V: "GM V", DISCOUNT_V: "Disc V", SALE_DAYS: "Sale Days",
    AVG_SALE_V: "Avg Sale V", AVG_SALE_Q: "Avg Sale Q", AVG_GM_V: "Avg GM V", SAL_DAYS: "Sale Days", GM_DAYS: "GM Days", DATA_DAYS: "Data Days",
    STOCK_V: "Stk V", STOCK_Q: "Stk Q",
    GRC_Q: "GRC Qty", GRC_V: "GRC Val", GRC_GRS_Q: "GRC Gross Qty", GRC_GRS_V: "GRC Gross Val", GRC_RET_Q: "GRC Return Qty", GRC_RET_V: "GRC Return Val",
    PO_PEND_Q: "Pending Qty", PO_PEND_V: "Pending Val", PO_ORD_Q: "Ordered Qty", PO_ORD_V: "Ordered Val", PO_GR_Q: "Received Qty",
    PO_OVD_Q: "Overdue Qty", PO_OVD_V: "Overdue Val", PO_REL_Q: "Released Qty", PO_UNREL_Q: "Unreleased Qty", PO_LINES: "Lines"
  };
  // Header prefix per window. Must match _WIN_LABELS in the route, or the grid and the CSV disagree.
  var WIN_LABELS = { TD: "TD", MTD: "MTD", QTR: "QTR", LAST_7DAYS: "L7D", LAST_4WEEK: "L4W", YTD: "YTD", LAST_6MONTH: "L6M", LYSP_SSN: "LYSP SSN", LYSP_MONTH: "LYSP MTH", RANGE: "Range" };
  var WIN_NOTES = { TD: "that date itself - this row's own day", MTD: "month to date, the 1st to that date", QTR: "fiscal quarter to date",
    LAST_7DAYS: "rolling 7 days ending on that date", LAST_4WEEK: "rolling 28 days ending on that date", YTD: "fiscal year to date, from 1 April",
    LAST_6MONTH: "the six completed months before this one", LYSP_SSN: "last year's same quarter", LYSP_MONTH: "last year's same month", RANGE: "the From-To range you selected" };
  // Display-only renames (the CSV keeps the raw K_PTL__STOCK_Q names on purpose).
  var STOCK_HEAD_OVERRIDE = { PTL: { STOCK_Q: "PTL_Q", STOCK_V: "PTL_V" }, MSA: { STOCK_Q: "MSA_Q", STOCK_V: "MSA_V" }, GRT: { STOCK_Q: "GRT_Q", STOCK_V: "GRT_V" } };
  var PLANT_BUCKETS = ["STORE", "DC", "HUB", "OTHER", "UNMAPPED"];
  var BUCKET_RE = new RegExp("^K_(.+)_(" + PLANT_BUCKETS.join("|") + ")__(.+)$");
  function splitParts(c) { var m = BUCKET_RE.exec(c); return m ? { tag: m[1], bucket: m[2], meas: m[3] } : null; }
  var COMPOSITE_LABELS = { TOTAL_STORE_STOCK: "Total Store Stock" };
  var RULE_NOTES = { LQ_V02: "STOCK, THE STORE'S OWN LQUA STORAGE TYPE - for each row this reads the LQUA tag its own store code spells: store HD22 reads LQ_D22, HB05 reads LQ_B05, U128 reads LQ_128 - the last three characters of the code. Counted only where the store master types that location as a STORE and its status is not IN-ACT, which keeps the two OFFICE plants (RD04, RH01) and the closed stores out. A different KPI per row, so there is no single storage type this column reports. It is a slice of LQUA and OVERLAPS the LQ_* tags it is drawn from, the LQL_* regrouping of the same rows, and the plain ET tags - never total it with any of them." };
  var PICK_LABELS = { MSA: "MSA", GRT: "GRT" };
  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
  var PLANT_NOTES = { STORE: "a selling store", DC: "a distribution centre", HUB: "a hub",
    OTHER: "a plant the master types as something other than store, DC or hub - NA, PLANT, the offices, and any plant type SAP adds later",
    UNMAPPED: "a location the store master has no row for at all - closed stores and test plants" };
  var REF_LABELS = { LYSP_DATE: "LYSP Date", LYSM_DATE: "LYSM Date", ACT_SALE_Q: "Act Qty", ACT_SALE_V: "Act Val", ACT_GM_VAL: "Act GM",
    ACT_AVG_STK_Q: "Act Stk Q", ACT_AVG_STK_V: "Act Stk V", LYSP_SALE_Q: "LYSP Qty", LYSP_SALE_V: "LYSP Val", LYSP_GM_VAL: "LYSP GM",
    LYSP_AVG_STK_Q: "LYSP Stk Q", LYSP_AVG_STK_V: "LYSP Stk V", LYSM_SALE_Q: "LYSM Qty", LYSM_SALE_V: "LYSM Val", LYSM_GM_VAL: "LYSM GM",
    LYSM_AVG_STK_Q: "LYSM Stk Q", LYSM_AVG_STK_V: "LYSM Stk V", LYSP_DAY_CONTRI: "Day Contri %" };
  var REF_NOTES = { ACT: "this year, as loaded by the Day Ref ACT SALE run",
    LYSP: "last year's same period - the exact day is in LYSP Date, and the gap is 354-385 days, set per store, never a flat 364",
    LYSM: "last year's same month - the exact day is in LYSM Date" };
  var WHN_PREFIXES = ["SDC_", "V2R_", "V2S_"], WHN_NAMES = { SDC: "the stores (SDC)", V2R: "the DCs (V2R)", V2S: "V2S" };
  var COUNT_MEAS = set(["GRC_LINES", "PO_LINES"]);

  function set(a) { var o = {}; a.forEach(function (x) { o[x] = true; }); return o; }
  function starts(c, p) { return String(c).indexOf(p) === 0; }
  function isStockCol(c) { return starts(c, "K_") && c.indexOf("__") >= 0; }
  function isSaleCol(c) { return starts(c, "S_") && c.indexOf("__") >= 0; }
  function isRefCol(c) { return starts(c, "R__"); }
  function isArtCol(c) { return starts(c, "A__"); }
  function isGrcCol(c) { return starts(c, "G_") && c.indexOf("__") >= 0; }
  function isPoCol(c) { return starts(c, "O_PO__"); }
  function isRefDate(c) { return isRefCol(c) && /_DATE$/.test(c); }
  function isRefPct(c) { return c === "R__LYSP_DAY_CONTRI"; }
  function isMetric(c) { return isStockCol(c) || isSaleCol(c) || isArtCol(c) || isGrcCol(c) || isPoCol(c) || (isRefCol(c) && !isRefDate(c)); }
  function isDayCount(c) { return (isSaleCol(c) && /DAYS$/.test(c.split("__")[1])) || ((isGrcCol(c) || isPoCol(c)) && !!COUNT_MEAS[c.slice(c.indexOf("__") + 2)]); }
  function isWhnTag(t) { return WHN_PREFIXES.some(function (p) { return starts(t, p); }); }
  function stockNote(t) {
    return isWhnTag(t)
      ? "LQUA warehouse " + (WHN_NAMES[t.slice(0, 3)] || t.slice(0, 3)) + ", storage type " + t.slice(4) + " - the bin stock LQ_" + t.slice(4) + " carries for this warehouse alone, valued VERME × MBEW.VERPR. LQ_" + t.slice(4) + " is the same type across EVERY warehouse, so these add up to it and must never be totalled with it, nor with the LQL_ regrouping or the plain ET tags"
      : "store storage location " + t + ", valued at LABST_DMBTR - the position at the To date (each store's latest reported day on or before it), not a sum across the range";
  }

  // ── dates and the nine window presets (they only SET From / To, relative to the To date) ─────────
  function iso(d) { return d.toISOString().slice(0, 10); }
  function ymd(t) { return t.split("-").map(Number); }
  function qStart(m) { return m >= 4 && m <= 6 ? 4 : m >= 7 && m <= 9 ? 7 : m >= 10 ? 10 : 1; }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  var PRESETS = [
    ["TD", "the To date itself - a single day", function (t) { return [t, t]; }],
    ["MTD", "the To date's month, 1st → To", function (t) { return [t.slice(0, 8) + "01", t]; }],
    ["QTR", "fiscal quarter (Apr-Jun / Jul-Sep / Oct-Dec / Jan-Mar) → To", function (t) { var a = ymd(t); return [a[0] + "-" + pad(qStart(a[1])) + "-01", t]; }],
    ["LAST_7DAYS", "rolling 7 days ending at To", function (t) { return [M.addDays(t, -6), t]; }],
    ["LAST_4WEEK", "rolling 28 days ending at To", function (t) { return [M.addDays(t, -27), t]; }],
    ["YTD", "fiscal year to date: 1-Apr → To", function (t) { var a = ymd(t); return [(a[1] >= 4 ? a[0] : a[0] - 1) + "-04-01", t]; }],
    ["LAST_6MONTH", "the six completed months ending at the previous month end", function (t) {
      var a = ymd(t), end = new Date(Date.UTC(a[0], a[1] - 1, 0)), start = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() - 5, 1)); return [iso(start), iso(end)]; }],
    ["LYSP_MONTH", "the same calendar month, last year", function (t) { var a = ymd(t); return [iso(new Date(Date.UTC(a[0] - 1, a[1] - 1, 1))), iso(new Date(Date.UTC(a[0] - 1, a[1], 0)))]; }],
    ["LYSP_SSN", "last year's same season (the quarter, a year back)", function (t) {
      var a = ymd(t), qs = qStart(a[1]); return [iso(new Date(Date.UTC(a[0] - 1, qs - 1, 1))), iso(new Date(Date.UTC(a[0] - 1, qs + 2, 0)))]; }]
  ];
  var MAX_LOAD_DAYS = 31;

  // ── header text + tooltip ───────────────────────────────────────────────────────────────────────
  function headLabel(c) {
    if (isArtCol(c)) { var a = c.slice(3); return "ACT · " + (REF_LABELS[a] || a).replace(/^Act /, ""); }
    if (isRefCol(c)) { var r = c.slice(3); return "CAT · " + (REF_LABELS[r] || r); }
    if (isPoCol(c)) { var p = c.slice(6); return "PO · " + (LABELS[p] || p); }
    if (!isStockCol(c) && !isSaleCol(c) && !isGrcCol(c)) return LABELS[c] || c;
    var i = c.indexOf("__"), tag = c.slice(2, i), raw = c.slice(i + 2);
    if (isStockCol(c) && STOCK_HEAD_OVERRIDE[tag] && STOCK_HEAD_OVERRIDE[tag][raw]) return STOCK_HEAD_OVERRIDE[tag][raw];
    if (isStockCol(c) && has(COMPOSITE_LABELS, tag)) return COMPOSITE_LABELS[tag] + " · " + (LABELS[raw] || raw);
    var sp = isStockCol(c) && splitParts(c);
    if (sp) return sp.tag + " · " + sp.bucket + " · " + (LABELS[sp.meas] || sp.meas);
    return (isSaleCol(c) || isGrcCol(c) ? (WIN_LABELS[tag] || tag) : tag) + " · " + (LABELS[raw] || raw);
  }
  function headTitle(c, meta) {
    if (isArtCol(c)) return "DAY REF, THIS ARTICLE - the reference loader's own logic (SP_AKS_DAY_REF_LOAD: SAP_VBRP for sale, ET_STOCK_DATA at LGORT 0001/0006 for stock) run at article grain instead of collapsed to the category. This article's own number, and it totals. Read it against TD Sale V beside it: that comes from the day-wise fact, which can be short on a day its backfill did not fully cover, while this comes from the billing document. A gap between them is a load gap, not a sale.";
    if (isRefCol(c)) {
      var m = c.slice(3), fam = REF_NOTES[m.split("_")[0]];
      var head = "CATEGORY REFERENCE - this row's MAJ_CAT in this store on this date, from AKS_SILVER.SLV_FCT_DAY_REF (the Day Ref Data page).";
      if (isRefDate(c)) return head + " The day this row's " + (starts(m, "LYSP") ? "LYSP" : "LYSM") + " figures were read from. Set per store, so it is not the same offset for every row.";
      var b = isRefPct(c) ? "This day's LYSP value as a share of the whole calendar month for the same store and category - so a month of it adds to 100%, and a partial range does not."
        : (fam ? fam.charAt(0).toUpperCase() + fam.slice(1) + ". " : "");
      return head + " " + b + " It is the CATEGORY's number, repeated on every variant in it - not this article's, and never safe to total down the column.";
    }
    if (isGrcCol(c)) {
      var w = c.slice(2, c.indexOf("__"));
      return "GOODS RECEIVED - SILVER.VW_GRC_REPORT_AKA, this store and article over " + (WIN_NOTES[w] || WIN_LABELS[w] || w) + ". Qty and Val are NET: the source signs its own reversals and returns (movements 102 / 122 / 161 carry negative quantities), so a plain sum is the net movement, and Gross less Returns is exactly that net. Goods receipt is overwhelmingly a DC activity - a blank on a store row is usually the estate, not a missing join." +
        (w === "TD" ? "" : " This window is cumulative and anchored on the row's own date, so it is NOT safe to total down a multi-day column; TD is.");
    }
    if (isPoCol(c)) return "OPEN PURCHASE ORDERS - SILVER.VW_PO_PENDING_NEW, this store and article as the book stands NOW. That view holds only lines still PENDING, so a fully received line has left it: there is no history in it and no 'PO raised MTD' reading to give. Ordered is the ordered size of the open book, not what was ordered this year, and Ordered less Received is exactly Pending. On a multi-day range it is stamped on the LAST day only, so earlier rows read blank rather than repeating one live number across the span.";
    if (c === PLANT_COL) return "PLANT TYPE - what this row's location is, from PLANT_TYPE on the FULL store master (all 705 plants, not the 666-row planning view the other store columns come from). UNMAPPED means the master has no row for it at all. This is the column the STORE / DC / HUB stock splits bucket by.";
    if (isStockCol(c)) {
      var tg = c.slice(2, c.indexOf("__"));
      if (has(COMPOSITE_LABELS, tg)) {
        var info = (meta.composites || {})[tg] || {}, mem = info.members || [], miss = info.missing || [];
        return COMPOSITE_LABELS[tg] + " - the SUM of " + mem.length + " stock tags: " + mem.join(", ") + ". Everything that is effectively this store's stock - what is in it, what is in transit to it, and what is packed for it upstream - the same definition TRF-IN uses for opening stock. The members do not overlap, so the sum is a real total and not a double count; note it is 0099_STORE, never plain 0099. It can read NEGATIVE: the in-transit member is signed. " +
          (miss.length ? "INCOMPLETE RIGHT NOW - " + miss.join(", ") + (miss.length > 1 ? " have" : " has") + " no rows in the fact, so " + (miss.length > 1 ? "those terms are" : "that term is") + " missing from this total." : "Every member is loaded.");
      }
      if (has(RULE_NOTES, tg)) return RULE_NOTES[tg];
      if (has(PICK_LABELS, tg)) {
        var pi = (meta.picks || {})[tg] || {}, pm = pi.members || [], pmiss = pi.missing || [];
        var meas = c.slice(c.indexOf("__") + 2) === "STOCK_Q" ? "quantity" : "value";
        return "STOCK, THE " + tg + " RACK AT WHICHEVER DC THIS ROW IS AT - the FIRST NON-ZERO of " + pm.join(" then ") + ", which are the same rack named per site by LQUA (" + (pm[0] || "") + " is DH24's storage type, " + (pm[1] || "") + " is DW01's). Not a sum: a member that loaded a hard 0 does not win over a real one. Only one of the two can be filled on a row, which is why the subtotal still counts both DCs once. It is a slice of LQUA and OVERLAPS the LQ_* tags it reads, the LQL_* regrouping and the plain ET tags - never total it with any of them. The position at the To date, in " + meas + ". " +
          (pmiss.length ? "INCOMPLETE RIGHT NOW - " + pmiss.join(", ") + (pmiss.length > 1 ? " have" : " has") + " no rows in the fact." : "Both members are loaded.");
      }
      var sp = splitParts(c);
      if (sp) return "STOCK, " + sp.tag + " HELD AT " + sp.bucket + " - the " + sp.tag + " column, kept only where this row's location is " + (PLANT_NOTES[sp.bucket] || sp.bucket) + ". The buckets partition the tag: exactly one is filled on any row, and STORE + DC + HUB + OTHER + UNMAPPED add back to plain " + sp.tag + " exactly. Blank here means this row is a different plant type, not that there is no stock - read these in the subtotal row or a filtered view, never down a single row. " + stockNote(sp.tag) + ".";
      return "STOCK - " + stockNote(tg);
    }
    if (isSaleCol(c)) {
      var sw = c.slice(2, c.indexOf("__")), n = "SALE - " + (WIN_NOTES[sw] || sw) + ", as at this row's own date.";
      return sw === "TD" ? n + " Safe to total down the column." : n + " Cumulative - do not total it down a multi-day column; use TD for that.";
    }
    return headLabel(c);
  }
  // header band + colour per column family (sale green, stock blue, split slate, ACT teal, CAT amber, GRC orange, PO violet)
  function family(c) {
    if (isSaleCol(c)) { var w = c.slice(2, c.indexOf("__")); return ["Sale · " + (WIN_LABELS[w] || w), "var(--s7)"]; }
    if (isStockCol(c)) return splitParts(c) ? ["Stock by plant type", "var(--s6)"] : ["Stock · storage locations", "var(--s1)"];
    if (isArtCol(c)) return ["ACT · this article", "var(--s2)"];
    if (isRefCol(c)) return ["CAT · category reference", "var(--s8)"];
    if (isGrcCol(c)) { var g = c.slice(2, c.indexOf("__")); return ["GRC · " + (WIN_LABELS[g] || g), "var(--s3)"]; }
    if (isPoCol(c)) return ["Open purchase orders", "var(--s4)"];
    return ["", ""];
  }

  M.page("variant-data", {
    render: function (ctx) {
      var YDAY = M.addDays(M.today(), -1);
      var st = { from: YDAY, to: YDAY, search: "", stockTags: [], grc: false, po: false, scaled: false };
      var meta = { coverage: {}, stores: [], majCats: [], segs: [], sizes: [], colors: [], stockTags: [], enrichOpts: {}, storedAnchors: [] };
      var metaLoading = true, lastRes = null, cache = { key: null, res: null }, pending = 0;
      var totals = { key: null, data: null, busy: false, err: "", ctrl: null }, summary = { key: null, data: null, ctrl: null };
      var grid = null;

      var refreshBtn = el("button", { type: "button", "class": "btn" }, [M.icon("refresh", 15), "Refresh"]);
      M.setHeader({ title: "Variant Article Data",
        sub: "Sale and stock on one row per day × store × variant article, from the day-wise facts. Pick any From / To range - sale is summed across it, stock is the position at the end of it; the named windows are presets that set those dates.",
        actions: [refreshBtn],
        tiles: ["Variants in scope", "Sale and stock", "Stock, no sale", "Sale, no stock", "Sale value", "Gross margin"].map(function (k) { return { k: k, skel: true }; }) });

      function nFmt(n) { return M.fmt(n || 0, "int"); }
      function pct(a, b) { return b ? Math.round(a / b * 100) + "%" : "—"; }
      function fmtCell(c, v) {
        if (v === null || v === undefined || v === "") return isMetric(c) ? "—" : "";
        if (NUMERIC_ATTRS[c]) return M.fmt(v, "dec");
        if (isRefPct(c)) { var p = Number(v); return isFinite(p) ? (p * 100).toFixed(2) + "%" : String(v); }
        if (!isMetric(c)) return String(v);
        var n = Number(v); if (!isFinite(n)) return String(v);
        if (!st.scaled || isDayCount(c)) return M.fmt(n, "dec");
        return M.fmt(n / (/_Q$/.test(c) ? 1000 : 100000), "dec");
      }
      function keyCols() { return set(meta.enrichCols && meta.enrichCols.length ? KEY_COLS_ARR.concat(meta.enrichCols) : KEY_COLS_ARR.concat([LOC_FLAG])); }

      // ── filter controls ─────────────────────────────────────────────────────────────────────────
      var from = el("input", { type: "date", value: st.from, "aria-label": "From date" }), to = el("input", { type: "date", value: st.to, "aria-label": "To date" });
      var search = el("input", { type: "search", placeholder: "e.g. 1110008198001 or JEANS", style: { minWidth: "240px" }, "aria-label": "Variant, article number or description" });
      function toggle(label, title, fn) {
        var b = el("button", { type: "button", "aria-pressed": "false", title: title, text: label });
        b.addEventListener("click", function () { var on = b.getAttribute("aria-pressed") !== "true"; b.setAttribute("aria-pressed", String(on)); fn(on); });
        return b;
      }
      var tScaled = toggle("K / L", "Quantities in thousands, values in lakhs", function (on) { st.scaled = on; grid.reload(); });
      var tGrc = toggle("GRC", "GOODS RECEIVED - adds " + Object.keys(WIN_LABELS).length + " windows × 6 measures on the right, windowed exactly like sale, so GRC reads straight across from Sale. Qty and Val are NET: the source signs its own reversals and returns. One extra scan of SILVER.VW_GRC_REPORT_AKA; off by default so nobody pays for it unasked.", function (on) { st.grc = on; grid.reload(true); });
      var tPo = toggle("PO", "OPEN PURCHASE ORDERS - the order book as it stands NOW. That view holds only lines still pending, so there is no window reading of it and no 'PO raised MTD'; on a multi-day range it lands on the last day only. One extra scan of SILVER.VW_PO_PENDING_NEW; off by default.", function (on) { st.po = on; grid.reload(true); });
      var presetSeg = el("div", { "class": "seg", role: "group", "aria-label": "Window presets" });
      var presetBtns = PRESETS.map(function (p) {
        var b = el("button", { type: "button", "aria-pressed": "false", text: WIN_LABELS[p[0]] || p[0] });
        b.addEventListener("click", function () { var r = p[2](st.to || YDAY); setRange(r[0], r[1]); });
        presetSeg.appendChild(b); return b;
      });
      function paintPresets() {
        PRESETS.forEach(function (p, i) { var r = p[2](st.to || YDAY); presetBtns[i].setAttribute("aria-pressed", String(r[0] === st.from && r[1] === st.to)); presetBtns[i].title = p[0] + ": " + p[1] + " - " + r[0] + " → " + r[1]; });
      }
      function setRange(f, t) { st.from = f; st.to = t; from.value = f; to.value = t; rangeChanged(); }
      function rangeChanged() { paintPresets(); drawStrips(); M.rangeHealth(healthBox, st.from, st.to); if (kpiBtnLabel) kpiBtnLabel(); grid.reload(true); }
      from.addEventListener("change", function () { st.from = from.value; rangeChanged(); });
      to.addEventListener("change", function () { st.to = to.value; rangeChanged(); });
      search.addEventListener("input", M.debounce(function () { st.search = search.value.trim(); grid.reload(true); }, 400));

      // coverage + which source served the sale windows
      var covLine = el("div", { "class": "row tiny muted" }), srcLine = el("div", { "class": "row tiny" });
      function rangeDays() {
        if (!st.from || !st.to || st.from > st.to) return [];
        var out = [], d = st.from; while (d <= st.to && out.length < 4000) { out.push(d); d = M.addDays(d, 1); } return out;
      }
      function drawStrips() {
        M.clear(covLine); var cov = meta.coverage || {};
        ["sale", "stock"].forEach(function (k) {
          var c = cov[k]; if (!c) return;
          covLine.appendChild(el("span", {}, [el("b", { text: k.toUpperCase() + " loaded: " }), c.days === 0 ? el("span", { style: { color: "var(--warn)" }, text: "nothing yet" })
            : c.minDt + (c.minDt !== c.maxDt ? " → " + c.maxDt : "") + " · " + c.days + " day" + (c.days > 1 ? "s" : "") + " · " + nFmt(c.rows) + " rows · " + c.stores + " store" + (c.stores > 1 ? "s" : "")]));
        });
        M.clear(srcLine);
        srcLine.appendChild(el("span", { "class": "muted", style: { fontWeight: 600, textTransform: "uppercase" }, text: "Sale windows" }));
        var src = lastRes && lastRes.saleSource;
        if (src === "stored") srcLine.appendChild(el("span", { "class": "chip", title: "Read from the stored KPI table (db-scripts/309): computed from VBRP at load time, so MTD / QTR / YTD do not depend on the day-wise fact holding those days.", text: "stored" }));
        else if (src === "computed") srcLine.appendChild(el("span", { "class": "chip grey", title: "Re-aggregated from the day-wise fact. Every window is only as complete as the DAYS inside it that have been loaded.", text: "computed" }));
        var days = rangeDays(), stored = set(meta.storedAnchors || []), n = days.filter(function (d) { return stored[d]; }).length;
        if (metaLoading && !(meta.storedAnchors || []).length) srcLine.appendChild(el("span", { "class": "muted", html: '<span class="spin"></span> checking which days have stored windows…' }));
        else if (days.length) {
          if (n === days.length) srcLine.appendChild(el("span", { "class": "muted", text: "all " + days.length + " day" + (days.length > 1 ? "s" : "") + " of this range have stored windows" }));
          else if (n === 0) srcLine.appendChild(el("span", { "class": "muted" }, ["no stored windows for this range - ", el("span", { style: { color: "var(--warn)" }, text: "MTD / QTR / YTD are only as good as the days loaded into the day-wise fact" })]));
          else srcLine.appendChild(el("span", { style: { color: "var(--warn)" }, text: "only " + n + " of " + days.length + " days have stored windows, so the grid falls back to computed" }));
        }
        if (cov.kpi && cov.kpi.days > 0) srcLine.appendChild(el("span", { "class": "muted", text: "· stored anchors: " + cov.kpi.days + " day" + (cov.kpi.days > 1 ? "s" : "") + " · " + nFmt(cov.kpi.rows) + " rows" }));
      }

      // pickers: they write into the grid's own filter state, so header filters, chips and saved views stay one thing
      var pickBox = el("div", { "class": "fbar", style: { borderRadius: 0 } }), pickers = {}, tagsMs = null, tagNote = el("span", { "class": "muted tiny", style: { maxWidth: "280px" } });
      var filterCount = el("span", { "class": "chip hidden" }), filtersBtn = el("button", { type: "button", "class": "btn sm", "aria-expanded": "false" }, [M.icon("filter", 14), "Filters", filterCount]);
      var clearAll = el("button", { type: "button", "class": "btn danger sm hidden", text: "Clear all" });
      filtersBtn.addEventListener("click", function () { var open = pickBox.classList.toggle("hidden") === false; filtersBtn.setAttribute("aria-expanded", String(open)); M.store.set("var.filtersOpen", open); });
      clearAll.addEventListener("click", function () { st.stockTags = []; if (tagsMs) tagsMs.set([]); grid.setState({ filters: {} }); grid.reload(true); });
      function pickerList(col) {
        if (col === "ST_CD") return meta.stores || []; if (col === "MAJ_CAT") return meta.majCats || []; if (col === "SEG") return meta.segs || [];
        if (col === "COLOR") return meta.colors || []; if (col === "SIZE_1") return meta.sizes || [];
        return (meta.enrichOpts || {})[col] || null;
      }
      function setFilter(col, vals) { var f = grid.state().filters; if (vals.length) f[col] = vals; else delete f[col]; grid.setState({ filters: f }); grid.reload(true); }
      function drawPickers() {
        M.clear(pickBox); pickers = {};
        var f = grid ? grid.state().filters : {};
        [["ST_CD", "Stores"], ["MAJ_CAT", "Maj-Cats"], ["SEG", "Segments"], ["COLOR", "Colours"], ["SIZE_1", "Sizes"]].forEach(function (x) {
          pickers[x[0]] = M.multiSelect(x[1], function () { return Promise.resolve(pickerList(x[0])); }, f[x[0]] || [], function (v) { setFilter(x[0], v); });
          pickBox.appendChild(pickers[x[0]].el);
        });
        tagsMs = M.multiSelect("Stock tags", function () { return Promise.resolve(meta.stockTags || []); }, st.stockTags, function (v) { st.stockTags = v; paintFilterCount(); grid.reload(true); }, { allText: "Default set" });
        pickBox.appendChild(tagsMs.el); pickBox.appendChild(tagNote);
        // every enrich column the server publishes options for - DFS order first, then any the server added since
        var eo = meta.enrichOpts || {}, keys = ENRICH_FILTERS.filter(function (k) { return (eo[k] || []).length; });
        Object.keys(eo).forEach(function (k) { if (keys.indexOf(k) < 0 && (eo[k] || []).length) keys.push(k); });
        keys.forEach(function (k) {
          pickers[k] = M.multiSelect(LABELS[k] || M.label(k), function () { return Promise.resolve(eo[k]); }, f[k] || [], function (v) { setFilter(k, v); });
          pickBox.appendChild(pickers[k].el);
        });
        pickBox.appendChild(clearAll);
        paintFilterCount();
      }
      function paintFilterCount() {
        var f = grid ? grid.state().filters : {}, n = st.stockTags.length;
        Object.keys(f).forEach(function (k) { n += (f[k] || []).length; });
        filterCount.textContent = n; filterCount.classList.toggle("hidden", !n); clearAll.classList.toggle("hidden", !n);
        tagNote.textContent = !st.stockTags.length && (meta.defaultOffTags || []).length ? meta.defaultOffTags.length + " LQUA warehouse × type tags (SDC_V01, V2R_E01…) are not in the default - tick them in Stock tags to add them." : "";
      }

      // ── layout ──────────────────────────────────────────────────────────────────────────────────
      var metaErr = el("div");
      var filterCard = el("div", { "class": "card" }, [
        el("div", { "class": "fbar" }, [el("label", { "class": "f" }, ["From date", from]), el("label", { "class": "f" }, ["To date", to]),
          el("label", { "class": "f" }, ["Windows", presetSeg]), el("label", { "class": "f" }, ["Variant / article no. / description", search]),
          el("label", { "class": "f" }, ["Show", el("div", { "class": "seg", role: "group", "aria-label": "Options" }, [tScaled, tGrc, tPo])]),
          el("span", { "class": "spacer" }), filtersBtn]),
        pickBox,
        el("div", { "class": "card-b stack", style: { gap: "4px" } }, [el("div", { "class": "muted tiny", text: "Each window sets From / To relative to the To date - nothing is stored per window, so a custom range is exactly as valid as a named one." }), covLine, srcLine])]);
      if (!M.store.get("var.filtersOpen", false)) pickBox.classList.add("hidden"); else filtersBtn.setAttribute("aria-expanded", "true");
      var healthBox = el("div");
      var totalsStrip = el("div", { "class": "card hidden" });
      var howTo = el("details", { "class": "card" }, [el("summary", { "class": "card-h", style: { cursor: "pointer" } }, [el("h2", { text: "How to read these numbers" }), el("span", { "class": "note", text: "dashes, sums, stock positions and the reference columns" })]),
        el("div", { "class": "card-b stack small", style: { maxWidth: "110ch" } }, [
          "A dash is not a zero. It means there is no row on that side for this store and variant - no sale in the range, or no stock at the end of it. A 0 means a row exists and the measure is zero. Only about 30% of variants have both, so the one-sided rows (holding stock without selling, and sold out) are most of the report; the base is a UNION of the two sides.",
          "Sale sums across the range; stock does not. Adding stock over days would count the same shirt once per day it sat there - a stock-days figure, not stock. Stock is taken at each store's latest reported day on or before the To date, because the feed does not cover every store every day.",
          "Every window is anchored on the row's own date - MTD on the 27-Aug row means 1-27 Aug. TD is the row's own day and the only sale / GRC window safe to total down a multi-day column; the others are cumulative, which is why the subtotal row leaves them as a dash on a multi-day range.",
          "Sale windows are read from the STORED KPI table where every day of the range has been stored (which is what makes MTD / QTR / YTD right without a day-wise backfill) and COMPUTED from the day-wise fact otherwise - never half and half. The 'Sale windows' strip says which ran.",
          "Sale Days counts the days that actually sold in the range - the denominator for a per-day rate. Disc V is −KZWI5, carried on only ~0.17% of billing lines, so 0 is normal.",
          "The teal ACT · columns are this article's own Day Ref numbers (SAP_VBRP for sale, ET_STOCK_DATA at LGORT 0001/0006 for stock) and they add up. Read ACT · Val against TD Sale V: a gap between them is a day-wise load gap, not a sale.",
          "The amber CAT · columns are the CATEGORY's numbers (AKS_SILVER.SLV_FCT_DAY_REF at store × MAJ_CAT × day), repeated on every variant of that category - they do not add up and are never subtotalled. A blank CAT · column is not a zero either: only about 43% of store × category keys have a Day Ref row, and CAT · Act reads 0 for dates the ACT SALE run has not reached yet. LYSP Date / LYSM Date name the exact day each looked back to (354-385 days, set per store).",
          "The 0099 · STORE / DC / HUB columns split that tag by the row location's plant type: one bucket is filled per row and they add back to plain 0099 exactly, so read them in the subtotal row or the CSV, never down one row. TOTAL_STORE_STOCK is a sum of non-overlapping tags and can read negative (in-transit is signed); MSA / GRT pick the first non-zero of the two DCs' racks; the LQ_*, LQL_* and SDC_ / V2R_ / V2S_ families each regroup the same LQUA snapshot, so never add two of them.",
          "Store and product hierarchy are joined on, never filtered by: locations absent from the store master (DCs and hubs) are kept and flagged rather than dropped."
        ].map(function (t) { return el("p", { style: { margin: 0 }, text: t }); }))]);
      ctx.main.appendChild(metaErr); ctx.main.appendChild(filterCard); ctx.main.appendChild(healthBox);

      // ── admin: load from proc + store KPI windows ───────────────────────────────────────────────
      var kpiBtnLabel = null;
      if (ctx.me.can.admin) ctx.main.appendChild(adminCard());
      ctx.main.appendChild(totalsStrip);
      var gridCard = el("div", { "class": "card" }); ctx.main.appendChild(gridCard); ctx.main.appendChild(howTo);

      function adminCard() {
        var lf = el("input", { type: "date", value: YDAY, max: YDAY, "aria-label": "Load from" }), lt = el("input", { type: "date", value: YDAY, max: YDAY, "aria-label": "Load to" });
        var stores = [];
        var ms = M.multiSelect("Stores (optional)", function () { return Promise.resolve(meta.allStores || meta.stores || []); }, [], function (v) { stores = v; }, { allText: "Every store" });
        var loadBtn = el("button", { type: "button", "class": "btn primary" }, [M.icon("play", 15), "Load from proc"]);
        var kpiBtn = el("button", { type: "button", "class": "btn" }, [M.icon("save", 15), el("span", { text: "Store KPI windows" })]);
        kpiBtnLabel = function () { kpiBtn.lastChild.textContent = "Store KPI windows for " + (st.to || "the To date"); };
        kpiBtnLabel();
        var status = el("div");
        function storesClean() { var seen = {}; return stores.map(function (x) { return String(x).trim().toUpperCase(); }).filter(function (x) { if (!x || seen[x]) return false; seen[x] = 1; return true; }); }
        function who(sts) { return !sts.length ? "ALL stores" : sts.length <= 6 ? "store" + (sts.length > 1 ? "s " : " ") + sts.join(", ") : sts.length + " stores"; }
        function run(label, url, payload, done) {
          var t0 = Date.now(), msg = el("span", { text: label + " - running…" });
          M.clear(status).appendChild(el("div", { "class": "notice" }, [el("span", { "class": "spin" }), msg]));
          var tick = setInterval(function () { var s = Math.round((Date.now() - t0) / 1000); msg.textContent = label + " - running for " + (s >= 60 ? Math.floor(s / 60) + " min " : "") + (s % 60) + " s. Keep this page open; the warehouse is doing the work."; }, 1000);
          loadBtn.disabled = kpiBtn.disabled = true;
          return M.post(url, payload).then(function (r) {
            clearInterval(tick); loadBtn.disabled = kpiBtn.disabled = false;
            if (!ctx.alive()) return;
            M.clear(status).appendChild(done(r, Math.round((Date.now() - t0) / 1000)));
            M.toast(label + " finished.", "ok");
            cache.key = null; totals.key = null; summary.key = null;
            return loadMeta().then(function () { grid.reload(); });
          }, function (e) {
            clearInterval(tick); loadBtn.disabled = kpiBtn.disabled = false;
            if (!ctx.alive()) return;
            M.clear(status).appendChild(el("div", { "class": "notice bad" }, [M.icon("info", 16), el("span", { text: label + " failed: " + e.message })]));
            M.toast(e.message, "bad");
          });
        }
        loadBtn.addEventListener("click", function () {
          var a = lf.value, b = lt.value;
          if (!a || !b) { M.toast("Pick both dates.", "bad"); return; }
          if (a > b) { M.toast("From date is after To date.", "bad"); return; }
          var days = Math.round((Date.parse(b) - Date.parse(a)) / 864e5) + 1;
          if (days > MAX_LOAD_DAYS) { M.toast("The load is capped at " + MAX_LOAD_DAYS + " days - this range is " + days + ".", "bad"); return; }
          var sts = storesClean();
          M.confirm("Load " + days + " day" + (days > 1 ? "s" : "") + " for " + who(sts) + "?",
            "Runs both day-wise loaders (SP_DAY_WISE_ST_VAR_SALE_LOAD and SP_DAY_WISE_ST_VAR_STOCK_LOAD) for " + a + " → " + b + ". Existing rows for that range" + (sts.length ? " at those stores" : "") +
            " are DELETED and re-inserted" + (sts.length ? "; other stores are untouched." : ".") + " Sale is ~3.3 lakh rows a day and stock ~1.97 crore a day, so a wide all-store range takes a while.", "Delete and reload", true).then(function (ok) {
            if (!ok) return;
            run("Load " + a + " → " + b + " (" + who(sts) + ")", API + "/load", { from_date: a, to_date: b, stores: sts }, function (r, secs) {
              var m = r.messages || {}, miss = r.etMissing || [], healed = r.etHealed || [];
              return el("div", { "class": "notice " + (miss.length ? "warn" : "ok") }, [M.icon("info", 16), el("div", { "class": "stack", style: { gap: "3px" } }, [
                el("b", { text: "Loaded " + r.fromDate + " → " + r.toDate + " for " + who(r.stores || []) + " in " + secs + " s" + (r.runGroup ? " · run " + r.runGroup : "") }),
                el("span", { text: "Sale: " + (m.sale || "done") }), el("span", { text: "Stock: " + (m.stock || "done") }),
                healed && healed.length ? el("span", { text: "ET stock healed for: " + [].concat(healed).join(", ") }) : null,
                miss.length ? el("span", { text: "The ET stock file had no locations for: " + miss.join(", ") + " - stock for those days is incomplete until the file arrives and the load is re-run." }) : null])]);
            });
          });
        });
        kpiBtn.addEventListener("click", function () {
          if (!st.to) { M.toast("Pick a To date.", "bad"); return; }
          var sts = storesClean(), anc = st.to;
          M.confirm("Store the nine KPI windows for " + anc + "?",
            "Computes and SAVES TD / MTD / QTR / L7D / L4W / YTD / L6M / LYSP for " + anc + " (the To date) for " + who(sts) + ", reading VBRP directly - so MTD / QTR / YTD come out right even where the day-wise fact has no history for those days. Any stored windows for " +
            anc + (sts.length ? " at those stores" : "") + " are DELETED and rebuilt. All stores is ~2.4 crore rows and takes minutes.", "Delete and rebuild", true).then(function (ok) {
            if (!ok) return;
            run("Store KPI windows for " + anc + " (" + who(sts) + ")", API + "/kpi-load", { anchor_date: anc, stores: sts }, function (r, secs) {
              return el("div", { "class": "notice ok" }, [M.icon("info", 16), el("div", { "class": "stack", style: { gap: "3px" } }, [
                el("b", { text: "KPI windows stored for " + r.anchorDate + " (" + who(r.stores || []) + ") in " + secs + " s" + (r.runGroup ? " · run " + r.runGroup : "") }),
                el("span", { text: r.message || "done" })])]);
            });
          });
        });
        return el("div", { "class": "card" }, [
          el("div", { "class": "card-h" }, [el("h2", { text: "Load from proc" }), el("span", { "class": "note", text: "Admin - rewrites the day-wise facts or the stored KPI windows" })]),
          el("div", { "class": "fbar", style: { borderRadius: 0 } }, [el("label", { "class": "f" }, ["From", lf]), el("label", { "class": "f" }, ["To", lt]), ms.el, loadBtn, kpiBtn]),
          el("div", { "class": "card-b stack small" }, [
            el("p", { "class": "muted", style: { margin: 0 }, text: "Load from proc runs both day-wise loaders for the From → To above (capped at " + MAX_LOAD_DAYS + " days). Existing rows for the range - at the picked stores only, if any are picked - are deleted and re-inserted, so it is safe to re-run. The store list offers every store that exists, since you pick one here precisely because it has not been loaded yet." }),
            el("p", { "class": "muted", style: { margin: 0 }, text: "Store KPI windows is the one to use when MTD / QTR / YTD look short: it computes those windows for the grid's To date from VBRP and saves them, so they are right immediately - no day-wise backfill needed. The grid then reads the stored numbers for any range whose every day has them. It shares the store picker." }),
            status])]);
      }

      // ── summary (tiles + range totals strip) and subtotals ─────────────────────────────────────
      function scopeBody(gs) {
        return { from_date: st.from, to_date: st.to, col_filters: gs.filters || {}, search: st.search || null };
      }
      function loadSummary(gs) {
        var b = scopeBody(gs), key = JSON.stringify(b);
        if (summary.key === key) return; summary.key = key;
        if (summary.ctrl) summary.ctrl.abort(); var ac = summary.ctrl = new AbortController();
        M.setHeader({ tiles: ["Variants in scope", "Sale and stock", "Stock, no sale", "Sale, no stock", "Sale value", "Gross margin"].map(function (k) { return { k: k, skel: true }; }) });
        M.post(API + "/summary", b, { signal: ac.signal }).then(function (s) {
          if (summary.ctrl !== ac || !ctx.alive()) return;
          summary.data = s; drawSummary();
        }, function (e) {
          if (summary.ctrl !== ac || !ctx.alive() || e.name === "AbortError") return;
          summary.data = null; summary.key = null; M.setHeader({ tiles: [] }); totalsStrip.classList.add("hidden");
          M.toast("Summary: " + e.message, "bad");
        });
      }
      function drawSummary() {
        var s = summary.data || {}, u = s.unionKeys, sale = s.sale || {};
        M.setHeader({ tiles: [
          { k: "Variants in scope", v: nFmt(u), s: "sale in range OR stock at end" },
          { k: "Sale and stock", v: nFmt(s.both), s: pct(s.both, u) + " · selling from stock it holds", cls: "up" },
          { k: "Stock, no sale", v: nFmt(s.stockOnly), s: pct(s.stockOnly, u) + " · holding it, not selling it", cls: "down" },
          { k: "Sale, no stock", v: nFmt(s.saleOnly), s: pct(s.saleOnly, u) + " · sold out, or sold from stock that moved" },
          { k: "Sale value", v: M.compact(sale.SALE_V || 0), s: nFmt(Math.round(sale.SALE_Q || 0)) + " pcs · " + (sale.days || 0) + " selling day" + (sale.days === 1 ? "" : "s") },
          { k: "Gross margin", v: M.compact(sale.GM_V || 0), s: "Disc V " + nFmt(Math.round(sale.DISCOUNT_V || 0)) }] });
        M.clear(totalsStrip); totalsStrip.classList.toggle("hidden", !s.sale);
        if (s.sale) totalsStrip.appendChild(el("div", { "class": "card-b row small" }, [
          el("span", { "class": "section-title", style: { margin: 0 }, text: "Range totals · " + st.from + " → " + st.to }),
          el("span", {}, ["Sale V ", el("b", { "class": "tnum", text: nFmt(Math.round(sale.SALE_V || 0)) })]),
          el("span", {}, ["Sale Q ", el("b", { "class": "tnum", text: nFmt(Math.round(sale.SALE_Q || 0)) })]),
          el("span", {}, ["GM V ", el("b", { "class": "tnum", text: nFmt(Math.round(sale.GM_V || 0)) })]),
          el("span", {}, ["Disc V ", el("b", { "class": "tnum", text: nFmt(Math.round(sale.DISCOUNT_V || 0)) })]),
          el("span", { "class": "muted", text: (sale.days || 0) + " selling day" + (sale.days === 1 ? "" : "s") + " in range" })]));
      }
      // Subtotals: one aggregate over the WHOLE filtered result (minutes on a wide range), so it never sits in front
      // of the rows and is not re-run on a page turn or a sort.
      function loadTotals(gs) {
        var b = scopeBody(gs); b.stock_tags = st.stockTags.length ? st.stockTags : null; b.ref_grc = st.grc; b.ref_po = st.po;
        var key = JSON.stringify(b); if (totals.key === key) return; totals.key = key;
        if (totals.ctrl) totals.ctrl.abort(); var ac = totals.ctrl = new AbortController();
        totals.busy = true; totals.err = ""; totals.data = null;
        M.post(API + "/totals", b, { signal: ac.signal }).then(function (d) {
          if (totals.ctrl !== ac) return; totals.busy = false; totals.data = d; if (ctx.alive() && !pending) grid.reload();
        }, function (e) {
          if (totals.ctrl !== ac || e.name === "AbortError") return; totals.busy = false; totals.err = e.message; if (ctx.alive() && !pending) grid.reload();
        });
      }
      function totalsRow(cols) {
        var row = {}, d = totals.data, first = (grid && grid.columns()[0]) || cols[0];
        cols.forEach(function (c) {
          if (!isMetric(c)) { row[c] = ""; return; }
          if (!d) { row[c] = ""; return; }
          if (!has(d.totals || {}, c)) { row[c] = "—"; return; }
          var v = d.totals[c]; if (v === null || v === undefined) { row[c] = "—"; return; }
          var n = Number(v);
          if (isRefPct(c)) row[c] = isFinite(n) ? (n * 100).toFixed(2) + "%" : String(v);
          else row[c] = st.scaled && !isDayCount(c) ? n / (/_Q$/.test(c) ? 1000 : 100000) : n;
        });
        row[first] = totals.busy ? "Subtotal…" : totals.err ? "Subtotal failed" : "SUBTOTAL";
        return row;
      }

      // ── the grid ────────────────────────────────────────────────────────────────────────────────
      function resultBody(gs) {
        return { from_date: st.from, to_date: st.to, stock_tags: st.stockTags.length ? st.stockTags : null, page: gs.page, limit: gs.limit,
                 col_filters: gs.filters || {}, search: st.search || null, sort_col: gs.sort_col, sort_dir: gs.sort_dir, ref_grc: st.grc, ref_po: st.po };
      }
      function exportCsv(gs) {
        var b = resultBody(gs); delete b.page; delete b.limit; b.fmt = "csv";
        M.toast("Preparing the CSV - the whole filtered selection, raw column names…");
        M.download("POST", API + "/export", b, "VAR_SALE_STOCK_" + st.from + "_to_" + st.to + ".csv").then(function (n) { M.toast("Downloaded " + n, "ok"); }, function (e) { M.toast(e.message, "bad"); });
      }
      function columnSets(anchor) {
        var cols = grid.allColumns(), kc = keyCols();
        function apply(hidden) { var lay = M.store.get("grid.variant-data", {}) || {}; lay.hidden = hidden; M.store.set("grid.variant-data", lay); makeGrid(true); }
        var refs = cols.filter(isRefCol), lay = M.store.get("grid.variant-data", {}) || {}, hid = lay.hidden || [];
        var refsOff = refs.length && refs.every(function (c) { return hid.indexOf(c) >= 0; });
        M.menu(anchor, [
          { label: "Show all columns", icon: "cols", fn: function () { apply([]); } },
          { label: "Keys only", fn: function () { apply(cols.filter(function (c) { return !kc[c]; })); } },
          { label: "Sale only (hide stock)", fn: function () { apply(cols.filter(isStockCol)); } },
          { label: "Stock only (hide sale)", fn: function () { apply(cols.filter(isSaleCol)); } }, "-",
          { label: refsOff ? "Show category reference (CAT ·)" : "Hide category reference (CAT ·)", fn: function () {
            var h = hid.slice(); refs.forEach(function (c) { var i = h.indexOf(c); if (refsOff && i >= 0) h.splice(i, 1); if (!refsOff && i < 0) h.push(c); }); apply(h); } }
        ], { alignRight: true });
      }
      function decorate() {
        if (!grid) return;
        var cols = grid.columns(), ths = gridCard.querySelectorAll("thead tr.cols th"), tds = gridCard.querySelectorAll("tfoot td");
        for (var i = 0; i < ths.length && i < cols.length; i++) {
          var c = cols[i], f = family(c);
          ths[i].title = headTitle(c, meta);
          var lab = ths[i].querySelector(".lbl"); if (lab) { lab.title = ths[i].title; if (f[1]) lab.style.color = f[1]; }
          if (tds[i] && isMetric(c) && tds[i].textContent === "—" && totals.data) {
            tds[i].style.color = "var(--warn)"; tds[i].style.fontWeight = "400";
            tds[i].title = isRefCol(c) ? headLabel(c) + " is never totalled, on any range. It is a CATEGORY figure repeated on every variant in that category, so adding the column counts the category once per article."
              : headLabel(c) + " cannot be totalled over a " + totals.data.days + "-day range. " + (isStockCol(c) ? "Stock is a POSITION at each day, not a flow - adding two days of it counts the same garment twice." : "Each row carries its own window, so adding them down the column counts the same days twice.") + " TD is the only column that adds up here - or pick a single day.";
          }
          if (i === 0 && tds[0]) tds[0].title = "Subtotal over all " + nFmt(totals.data ? totals.data.rows : 0) + " rows matching the current filters - not just this page";
        }
      }
      var observer = null;
      function makeGrid(keepState) {
        var old = grid, state = old ? old.state() : null;
        if (observer) observer.disconnect();
        if (old) old.el.remove();
        grid = M.Grid(gridCard, {
          id: "variant-data", title: "Store × variant rows", limit: 200, pageSizes: [100, 200, 500, 1000], conditions: false,
          tools: [el("button", { type: "button", "class": "btn sm", onclick: function (e) { columnSets(e.currentTarget); } }, [M.icon("cols", 15), "Column sets"])],
          isKey: function (c) { return !isMetric(c) && !NUMERIC_ATTRS[c]; },
          format: function (c) { return isMetric(c) || NUMERIC_ATTRS[c] ? "dec" : "text"; },
          label: headLabel,
          groups: function (cols) {
            var out = [];
            cols.forEach(function (c) { var l = family(c)[0]; if (out.length && out[out.length - 1].label === l) out[out.length - 1].span++; else out.push({ label: l, span: 1 }); });
            return out.length > 1 ? out : null;
          },
          cell: function (td, c, v) {
            if (!isMetric(c) && !NUMERIC_ATTRS[c]) return;
            td.textContent = fmtCell(c, v); td.classList.add("num");
            if (v === null || v === undefined || v === "") td.style.color = "var(--muted)";
          },
          values: function (c) {
            var l = pickerList(c);
            if (l) return Promise.resolve(l);
            return Promise.reject(new Error("the engine filters only the store, product and master columns - use Search for article numbers"));
          },
          fetch: function (gs, signal) {
            Object.keys(pickers).forEach(function (k) { pickers[k].set((gs.filters || {})[k] || []); });
            paintFilterCount();
            if (!st.from || !st.to) return Promise.reject(new Error("Pick a From and a To date."));
            if (st.from > st.to) return Promise.reject(new Error("From date is after To date."));
            loadSummary(gs); loadTotals(gs);
            var b = resultBody(gs), key = JSON.stringify(b);
            function done(r) { lastRes = r; drawStrips(); return { columns: r.columns, rows: r.rows, total: r.total, totals: totalsRow(r.columns), saleSource: r.saleSource }; }
            if (cache.key === key && cache.res) return Promise.resolve(done(cache.res));
            pending++;
            return M.post(API + "/result", b, { signal: signal }).then(function (r) { pending--; cache.key = key; cache.res = r; return done(r); },
              function (e) { pending--; throw e; });
          },
          onData: function () { decorate(); },
          getExtra: function () { return { from: st.from, to: st.to, stockTags: st.stockTags, grc: st.grc, po: st.po, scaled: st.scaled }; },
          setExtra: function (x) {
            if (x.from && x.to) { st.from = x.from; st.to = x.to; from.value = x.from; to.value = x.to; paintPresets(); drawStrips(); M.rangeHealth(healthBox, st.from, st.to); if (kpiBtnLabel) kpiBtnLabel(); }
            st.stockTags = x.stockTags || []; if (tagsMs) tagsMs.set(st.stockTags);
            [["grc", tGrc], ["po", tPo], ["scaled", tScaled]].forEach(function (p) { st[p[0]] = !!x[p[0]]; p[1].setAttribute("aria-pressed", String(st[p[0]])); });
          },
          exports: ctx.me.can.export ? [{ label: "CSV - the whole filtered selection (raw column names)", fn: function (gs) { exportCsv(gs); } }] : []
        });
        var wrap = gridCard.querySelector(".dg-wrap");
        if (wrap && window.MutationObserver) { observer = new MutationObserver(function () { if (lastRes) decorate(); }); observer.observe(wrap, { childList: true }); }
        if (keepState && state) { grid.setState(state); return grid.reload(); }
        return null;
      }

      function loadMeta() {
        metaLoading = true; drawStrips();
        return M.get(API + "/params").then(function (p) {
          if (!ctx.alive()) return;
          meta = p; metaLoading = false; M.clear(metaErr);
          if (p.error) metaErr.appendChild(el("div", { "class": "notice bad" }, [M.icon("info", 16), el("span", { text: p.error })]));
          drawPickers(); drawStrips();
        }, function (e) {
          if (!ctx.alive()) return;
          metaLoading = false; M.clear(metaErr).appendChild(el("div", { "class": "notice bad" }, [M.icon("info", 16), el("span", { text: "The page's lists could not be read: " + e.message })]));
          drawStrips();
        });
      }
      refreshBtn.addEventListener("click", function () { M.action(refreshBtn, "Refreshing…", function () { return loadMeta(); }); });

      makeGrid(false);
      drawPickers(); paintPresets(); drawStrips(); M.rangeHealth(healthBox, st.from, st.to);
      // The grid does not wait for /params (DFS fires both at once); the pickers fill in when it lands.
      loadMeta();
      return grid.applyDefaultView().then(function () { if (ctx.alive()) return grid.reload(true); });
    }
  });
})();

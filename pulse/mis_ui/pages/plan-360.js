// Plan Live 360 (DFS /aks/plan-live-360, PlanLive360.jsx + components/FilterDropdown.jsx + config/plan360Cols.js): the four
// monthly plans on one key - Sale, Space, Transfer In (store x category) and Purchase (RDC x category, its own grain) - read
// from the snapshot AKS_GOLD.PLAN_LIVE_360*_MAT. Month picker, grain switch, the snapshot state (built at / by, stale when a
// plan changed since) with "Refresh snapshot" (mis.admin, single-flight), the lineage banner (which sale plan each plan was
// built from), the per-source health strip, the named exception filters ("Sale, no TRF" ...), header filters on the dimension
// columns, the grouped header (Identity / Sale / Space / TRF / Purchase / Cross-check / Coverage), K / L display scaling,
// totals over the filtered set and the Excel export.
// NULL IS NOT ZERO: a blank cell means that plan has no row for the key; 0 means the plan says zero.
// API: /api/v1/mis/plan360/{months, rows, summary, col-values?col=, export, refresh}.
(function () {
  "use strict";
  var M = window.MIS, el = M.el;
  var API = "/api/v1/mis/plan360";

  var SOURCES = [["SALE", "Sale Plan", "var(--s1)"], ["SPACE", "Space Plan", "var(--s4)"], ["TRF", "Transfer In", "var(--s7)"], ["PURCHASE", "Purchase", "var(--s3)"]];
  var GROUP_LABEL = { IDENTITY: "Identity", SALE: "Sale Plan", SPACE: "Space Plan", TRF: "Transfer In", PURCHASE: "Purchase", CHECK: "Cross-check", COVERAGE: "Coverage" };
  // The browser's copy of the column contract (plan360Cols.js); the server's /months "columns" wins when it is there.
  var LOCAL_COLS = {
    store: [["MONTH_LABEL", "Month", "s", "IDENTITY"], ["ST_CD", "Store", "s", "IDENTITY"], ["ST_NM", "Store Name", "s", "IDENTITY"], ["RDC_CD", "RDC", "s", "IDENTITY"],
      ["MAJ_CAT", "Category", "s", "IDENTITY"], ["SSN", "Season", "s", "IDENTITY"], ["SALE_W_Q", "Sale Qty (W/STR)", "q", "SALE"], ["SALE_W_V", "Sale Val (W/STR)", "v", "SALE"],
      ["SALE_WO_Q", "Sale Qty (WO STR)", "q", "SALE"], ["SALE_WO_V", "Sale Val (WO STR)", "v", "SALE"], ["SPACE_DISP_Q", "Initial Disp Qty", "q", "SPACE"],
      ["TRF_SALE_Q", "TRF Sale Qty", "q", "TRF"], ["TRF_COVER_SALE_Q", "Cover Sale Qty", "q", "TRF"], ["TRF_OP_STK", "Opening Stock", "q", "TRF"],
      ["TRF_DISP_CL_Q", "Bgt Disp Cl Qty", "q", "TRF"], ["TRF_IN_STK_Q", "Transfer In Qty", "q", "TRF"], ["TRF_CL_STK", "Closing Stock", "q", "TRF"],
      ["TRF_SHORT_Q", "Short Qty", "q", "TRF"], ["TRF_EXCESS_Q", "Excess Qty", "q", "TRF"], ["TRF_COVER_DAY", "Cover Days", "n", "TRF"],
      ["HAS_SALE", "Sale", "b", "COVERAGE"], ["HAS_SPACE", "Space", "b", "COVERAGE"], ["HAS_TRF", "TRF", "b", "COVERAGE"]],
    rdc: [["MONTH_LABEL", "Month", "s", "IDENTITY"], ["RDC_CD", "RDC", "s", "IDENTITY"], ["RDC_NM", "RDC Name", "s", "IDENTITY"], ["MAJ_CAT", "Category", "s", "IDENTITY"],
      ["SSN_LIST", "Season", "s", "IDENTITY"], ["STORES", "Stores", "n", "IDENTITY"], ["SALE_W_Q", "Sale Qty (W/STR)", "q", "SALE"], ["SALE_W_V", "Sale Val (W/STR)", "v", "SALE"],
      ["SPACE_DISP_Q", "Initial Disp Qty", "q", "SPACE"], ["TRF_IN_STK_Q", "Transfer In Qty", "q", "TRF"], ["TRF_DISP_CL_Q", "Bgt Disp Cl Qty", "q", "TRF"],
      ["TRF_CL_STK", "Closing Stock", "q", "TRF"], ["PP_BGT_PUR_Q", "Purchase Qty", "q", "PURCHASE"], ["PP_POS_PO_RAISED", "PO Raised (+)", "q", "PURCHASE"],
      ["PP_NEG_PO_RAISED", "PO Raised (-)", "q", "PURCHASE"], ["PP_DC_STK_Q", "DC Stock", "q", "PURCHASE"], ["PP_DC_CL_STK_Q", "DC Closing Stock", "q", "PURCHASE"],
      ["PP_TTL_TRF_OUT_Q", "PP Trf Out Qty", "q", "PURCHASE"], ["PP_DC_SHORT_Q", "DC Short", "q", "PURCHASE"], ["PP_DC_EXCESS_Q", "DC Excess", "q", "PURCHASE"],
      ["TRF_VS_PP_OUT_DIFF", "TRF vs PP Diff", "q", "CHECK"], ["HAS_STORE_PLANS", "Store Plans", "b", "CHECK"], ["HAS_PURCHASE", "Purchase", "b", "CHECK"]]
  };
  var LOCAL_FILTERABLE = { store: ["ST_CD", "ST_NM", "RDC_CD", "MAJ_CAT", "SSN"], rdc: ["RDC_CD", "RDC_NM", "MAJ_CAT"] };
  // The named exception filters - the cross-plan questions no single plan can answer.
  var GAPS = {
    store: [["complete", "Complete", "All three plans have a row for this store-category"],
            ["sale_no_trf", "Sale, no TRF", "Planned to sell it, no transfer-in planned"],
            ["no_space", "No Space", "No space-plan row - the store or category is outside the space plan"],
            ["no_sale", "No Sale Plan", "Transfer-in or space plans it, the sale plan does not"]],
    rdc: [["pp_mismatch", "TRF ≠ PP", "Transfer-in and the purchase plan disagree on transfer-out demand"],
          ["no_purchase", "No Purchase", "No purchase-plan row for this RDC-category"],
          ["no_store", "No Store Plans", "Purchase plans it, no store-level plan rolls up to it"]]
  };
  var HEAD_GAP = { store: ["sale_no_trf", "Sale, no TRF", "store-categories have a sale plan and no transfer-in"],
                   rdc: ["pp_mismatch", "TRF ≠ PP", "RDC-categories where transfer-in and purchase disagree on transfer-out"] };
  var VIEW_NAME = { store: "V2RETAIL.AKS_GOLD.VW_PLAN_LIVE_360", rdc: "V2RETAIL.AKS_GOLD.VW_PLAN_LIVE_360_RDC" };
  var GRAINS = [["store", "Store × Category"], ["rdc", "RDC × Category · Purchase"]];
  var inflight = null;          // the snapshot rebuild, single-flight across page visits

  function truthy(v) { return v === true || v === 1 || v === "1" || String(v).toLowerCase() === "true"; }
  function dt(v) { return v ? M.fmtDate(v) : ""; }
  function int(v) { return M.fmt(v == null || v === "" ? 0 : v, "int"); }

  M.page("plan-360", {
    render: function (ctx) {
      var me = ctx.me || { can: {} };
      var st = { grain: M.qs("grain") === "rdc" ? "rdc" : "store", monthId: M.qs("month") ? Number(M.qs("month")) : null, months: [], lineage: null, snapshot: null,
                 cols: null, filterable: null, gaps: null, gap: null, scaled: M.store.get("p360.scaled", true) !== false, summary: null, headGap: null,
                 rowsCache: { key: "", data: null }, sumCache: { key: "", data: null }, gapCache: { key: "", n: null }, showHealth: M.store.get("p360.health", true) !== false };
      var grid = null;

      // ── masthead ───────────────────────────────────────────────────────────────────────────────────────────────────
      var recheck = el("button", { type: "button", "class": "btn", title: "Re-check every plan for a newer save" }, [M.icon("refresh", 15), "Re-check plans"]);
      recheck.addEventListener("click", function () { M.action(recheck, "Checking…", function () { return reloadAll(true); }).catch(function () { /* toasted */ }); });
      var refreshBtn = me.can.admin ? el("button", { type: "button", "class": "btn", title: "Rebuild the snapshot from the four plan views (tens of seconds)" }, [M.icon("play", 15), "Refresh snapshot"]) : null;
      if (refreshBtn) refreshBtn.addEventListener("click", refreshSnapshot);
      var excelBtn = me.can.export ? el("button", { type: "button", "class": "btn primary" }, [M.icon("down", 15), "Excel"]) : null;
      if (excelBtn) excelBtn.addEventListener("click", function () { if (grid) M.action(excelBtn, "Generating…", function () { return doExport(grid.state()); }).catch(function () { /* toasted */ }); });
      M.setHeader({ title: "Plan Live 360", crumbs: [{ label: "Plan Live 360", href: "/mis/plan-360" }],
                    sub: "Sale · Space · Transfer In · Purchase on one key - read from a snapshot of the newest save of each plan. A blank cell means that plan has no row for the key; 0 means the plan says zero.",
                    actions: [recheck, refreshBtn, excelBtn],
                    tiles: [{ k: "Rows", skel: true }, { k: "Sale value", skel: true }, { k: "Transfer in", skel: true }, { k: "Coverage gap", skel: true }, { k: "Snapshot", skel: true }] });

      // ── page skeleton ─────────────────────────────────────────────────────────────────────────────────────────────
      var statusBox = el("div", { "class": "stack" });
      var healthCard = el("div", { "class": "card" });
      var controls = el("div", { "class": "card" });
      var gridCard = el("div", { "class": "card" });
      var emptyBox = el("div");
      [statusBox, healthCard, controls, gridCard, emptyBox].forEach(function (n) { ctx.main.appendChild(n); });

      function syncUrl() {
        var q = new URLSearchParams(); q.set("grain", st.grain); if (st.monthId) q.set("month", st.monthId);
        history.replaceState({}, "", "/mis/plan-360?" + q.toString());
      }
      function cols() { return ((st.cols && st.cols[st.grain]) || LOCAL_COLS[st.grain].map(function (c) { return { k: c[0], h: c[1], t: c[2], grp: c[3] }; })); }
      function colOf(k) { return cols().filter(function (c) { return c.k === k; })[0] || null; }
      function filterable() { return (st.filterable && st.filterable[st.grain]) || LOCAL_FILTERABLE[st.grain]; }
      function gapsOf() { var srv = st.gaps && st.gaps[st.grain]; return GAPS[st.grain].filter(function (g) { return !srv || srv.indexOf(g[0]) >= 0; }); }
      function monthMeta() { return st.months.filter(function (m) { return m.month_id === st.monthId; })[0] || null; }

      // ── month list, snapshot state, lineage, health ─────────────────────────────────────────────────────────────
      function loadMeta() {
        return M.get(API + "/months").then(function (r) {
          if (!ctx.alive()) return;
          st.months = r.months || []; st.lineage = r.lineage || null; st.snapshot = r.snapshot || null;
          st.cols = r.columns || null; st.filterable = r.filterable || null; st.gaps = r.gaps || null;
          if (!st.monthId || !st.months.some(function (m) { return m.month_id === st.monthId; })) {
            // Open on the first month that has all four plans: the spine's edges are months where only one plan exists.
            var full = st.months.filter(function (m) { return Object.keys(m.sources || {}).length >= 4; })[0];
            st.monthId = (full || st.months[0] || {}).month_id || null;
          }
          syncUrl(); paintStatus(); paintHealth(); paintControls(); paintTiles();
        });
      }

      function paintStatus() {
        M.clear(statusBox);
        var s = st.snapshot || {}, L = st.lineage;
        if (s.status === "NEVER" || !s.status) {
          statusBox.appendChild(el("div", { "class": "notice warn" }, [M.icon("info", 16), el("span", { text: "The snapshot has never been built" +
            (me.can.admin ? " - press Refresh snapshot above." : " - ask an MIS administrator to refresh it.") })]));
        } else {
          var bits = "Snapshot built " + dt(s.refreshed_at) + (s.refreshed_at ? " (" + M.ago(s.refreshed_at) + ")" : "") + (s.refreshed_by ? " by " + s.refreshed_by : "") +
            (s.rows_store != null ? " · " + int(s.rows_store) + " store rows" : "") + (s.rows_rdc != null ? " · " + int(s.rows_rdc) + " RDC rows" : "") +
            (s.duration_ms != null ? " · built in " + Math.round(Number(s.duration_ms) / 1000) + "s" : "");
          if (s.status === "RUNNING") statusBox.appendChild(el("div", { "class": "notice" }, [el("span", { "class": "spin" }), el("span", { text: "A snapshot refresh is running" + (s.refreshed_by ? " (started by " + s.refreshed_by + ")" : "") + ". " + bits })]));
          else if (s.stale) statusBox.appendChild(el("div", { "class": "notice warn" }, [M.icon("info", 16), el("span", {}, [el("b", { text: "A plan changed since this snapshot. " }),
            bits + ". The grid shows the plans as they were when it was built" + (me.can.admin ? " - Refresh snapshot to read the newest saves." : ".")])]));
          else statusBox.appendChild(el("div", { "class": "notice ok" }, [M.icon("info", 16), el("span", {}, [el("b", { text: "Current. " }), bits + ". No plan has been saved since."])]));
          if (s.status === "FAILED" && s.error) statusBox.appendChild(el("div", { "class": "notice bad" }, [M.icon("x", 16), el("span", { text: "Last refresh failed: " + s.error })]));
        }
        // Lineage: which sale plan the plans were built from. They can differ, and a reader who cannot see it assumes an arithmetic error.
        if (L) {
          var drift = truthy(L.IS_DRIFT);
          var line = function (lab, ses, at, stt) { return el("div", { "class": "small" }, [lab + ": ", el("code", { text: ses || "none" }), (dt(at) ? " · saved " + dt(at) : "") + (stt ? " · " + stt : "")]); };
          statusBox.appendChild(el("div", { "class": "notice " + (drift ? "warn" : "ok") }, [M.icon(drift ? "info" : "deps", 16), el("div", { "class": "stack", style: { gap: "3px" } }, [
            el("b", { text: drift ? "The sale plan on screen is not the one Transfer In and Purchase were built from." : "All four plans are built from one sale plan." }),
            L.DRIFT_REASON ? el("div", { "class": "small", text: L.DRIFT_REASON }) : null,
            line("Sale columns", L.DISPLAYED_SESSION_ID, L.DISPLAYED_SAVED_AT, L.DISPLAYED_STATUS),
            line("Transfer In & Purchase used", L.CONSUMED_SESSION_ID, L.CONSUMED_SAVED_AT, L.CONSUMED_STATUS)])]));
        }
      }

      function paintHealth() {
        M.clear(healthCard);
        var m = monthMeta(); if (!m) { healthCard.classList.add("hidden"); return; }
        healthCard.classList.remove("hidden");
        var bad = SOURCES.filter(function (s) { var h = (m.sources || {})[s[0]]; return !h || (Number(h.rows) > 0 && Number(h.non_zero) === 0); });
        var tog = el("button", { type: "button", "class": "btn ghost sm", "aria-expanded": String(st.showHealth), text: st.showHealth ? "Hide" : "Show" });
        var body = el("div", { "class": "card-b" + (st.showHealth ? "" : " hidden") });
        tog.addEventListener("click", function () { st.showHealth = !st.showHealth; M.store.set("p360.health", st.showHealth); paintHealth(); });
        healthCard.appendChild(el("div", { "class": "card-h" }, [el("h2", { text: "Plan sources · " + m.label }),
          bad.length ? el("span", { "class": "chip warn", text: "No usable data: " + bad.map(function (s) { return s[1]; }).join(", ") })
                     : el("span", { "class": "chip ok", text: "All four plans loaded" }),
          el("span", { "class": "spacer" }), tog]));
        var g = el("div", { "class": "grid12" });
        SOURCES.forEach(function (s) {
          var h = (m.sources || {})[s[0]], empty = h && Number(h.rows) > 0 && Number(h.non_zero) === 0;
          g.appendChild(el("div", { "class": "c3", style: { border: "1px solid var(--line)", borderRadius: "10px", padding: "10px 12px", borderTop: "3px solid " + s[2],
            background: empty || !h ? "var(--warn-soft)" : "var(--surface)" } }, [
            el("div", { "class": "section-title", style: { color: s[2] } }, [el("span", { text: s[1] })]),
            !h ? el("div", { "class": "muted small", style: { marginTop: "4px" }, text: "No data this month" }) : el("div", { "class": "stack", style: { gap: "2px", marginTop: "4px" } }, [
              el("div", {}, [el("b", { "class": "tnum", style: { fontSize: "18px" }, text: int(h.non_zero) }), el("span", { "class": "muted small", text: " / " + int(h.rows) + " rows non-zero" }),
                empty ? el("span", { "class": "chip warn", style: { marginLeft: "6px" }, text: "all zero" }) : null]),
              (h.stores != null || h.categories != null) ? el("div", { "class": "small muted", text: (h.stores != null ? int(h.stores) + " stores" : "") + (h.categories != null ? " · " + int(h.categories) + " categories" : "") }) : null,
              h.provenance ? el("div", { "class": "small", title: h.provenance, style: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }, text: h.provenance }) : null,
              h.last_written ? el("div", { "class": "tiny muted", text: "Last written " + dt(h.last_written) + " (" + M.ago(h.last_written) + ")" }) : null])]));
        });
        body.appendChild(g);
        body.appendChild(el("p", { "class": "muted tiny", style: { margin: "10px 0 0" }, text: "Sale months are fiscal; transfer-in and purchase months are calendar, so the sale columns " +
          "describe an overlapping, not identical, window. The plans also do not cover the same keys - rows a plan does not have are blank, never zero." }));
        healthCard.appendChild(body);
      }

      // ── controls: grain, month, units, exceptions, view name ───────────────────────────────────────────────────
      function seg(label, items, cur, onPick, wrap) {
        var s = el("div", { "class": "seg", role: "group", "aria-label": label, style: wrap ? { flexWrap: "wrap" } : null });
        items.forEach(function (it) {
          s.appendChild(el("button", { type: "button", "aria-pressed": String(it[0] === cur), title: it[2] || null, text: it[1], onclick: function () { onPick(it[0]); } }));
        });
        return s;
      }
      function paintControls() {
        M.clear(controls);
        if (!st.months.length) { controls.classList.add("hidden"); return; }
        controls.classList.remove("hidden");
        var months = st.months.map(function (m) {
          var n = Object.keys(m.sources || {}).length;
          return [m.month_id, m.label + (n < 4 ? " *" : ""), n < 4 ? "Only " + Object.keys(m.sources || {}).join(", ") + " have data this month" : "All four plans have data"];
        });
        var copyBtn = el("button", { type: "button", "class": "btn sm", title: "Copy the Snowflake view behind this grain, to hand the result to someone without a portal login" },
          [M.icon("copy", 14), el("code", { text: VIEW_NAME[st.grain] })]);
        copyBtn.addEventListener("click", function () {
          var t = VIEW_NAME[st.grain];
          (navigator.clipboard ? navigator.clipboard.writeText(t) : Promise.reject(new Error("no clipboard"))).then(function () { M.toast("Copied " + t, "ok"); },
            function () { M.toast("Could not copy - the view is " + t, "bad"); });
        });
        controls.appendChild(el("div", { "class": "fbar" }, [
          el("label", { "class": "f" }, ["Grain", seg("Grain", GRAINS, st.grain, setGrain)]),
          el("label", { "class": "f" }, ["Month", seg("Month", months, st.monthId, setMonth, true)]),
          el("label", { "class": "f", title: "Display only - Qty ÷ 1,000 and Val ÷ 1,00,000. The Snowflake views and the Excel file carry raw units." },
            ["Units", seg("Units", [["kl", "K / L"], ["raw", "Actual"]], st.scaled ? "kl" : "raw", function (u) { st.scaled = u === "kl"; M.store.set("p360.scaled", st.scaled); paintControls(); if (grid) grid.reload(); })]),
          el("span", { "class": "spacer" }), copyBtn]));
        var gaps = gapsOf();
        controls.appendChild(el("div", { "class": "fbar", style: { background: "var(--surface)" } }, [
          el("label", { "class": "f", title: "The cross-plan questions no single plan can answer. Click again to clear." },
            ["Exceptions", seg("Exceptions", [[null, "All rows", "No exception filter"]].concat(gaps), st.gap, setGap, true)]),
          el("span", { "class": "muted small", style: { alignSelf: "center" }, text: st.gap ? (gaps.filter(function (g) { return g[0] === st.gap; })[0] || [])[2] || "" : "" }),
          el("span", { "class": "muted tiny", style: { alignSelf: "center" }, text: "* = a month where not every plan has data. Header filters work on the dimension columns." })]));
      }
      function resetGrid() { if (!grid) return; grid.setState({ filters: {}, conds: [], sort_col: null, sort_dir: "asc", page: 1 }); }
      function setMonth(id) {
        if (id === st.monthId) return;
        // A month change resets sort, header filters and the exception filter (DFS).
        st.monthId = id; st.gap = null; syncUrl(); paintHealth(); paintControls(); resetGrid(); if (grid) grid.reload(true);
      }
      function setGrain(g) {
        if (g === st.grain) return;
        st.grain = g; st.gap = null; syncUrl(); paintControls(); buildGrid();
      }
      function setGap(k) { st.gap = st.gap === k ? null : k; paintControls(); if (grid) grid.reload(true); }

      // ── grid ─────────────────────────────────────────────────────────────────────────────────────────────────────
      function q(gs, withPage) {
        var cf = {}, fl = filterable();
        Object.keys(gs.filters || {}).forEach(function (c) { if (fl.indexOf(c) >= 0 && gs.filters[c] && gs.filters[c].length) cf[c] = gs.filters[c].slice(); });
        var b = { grain: st.grain, month_id: st.monthId, col_filters: Object.keys(cf).length ? cf : null, gap: st.gap || null, sort_col: gs.sort_col || null, sort_dir: gs.sort_dir || "asc" };
        if (withPage) { b.page = gs.page; b.limit = gs.limit; }
        return b;
      }
      function scale(c, v) {
        if (v === "" || v === null || v === undefined || !st.scaled) return v;
        var n = Number(v); if (!isFinite(n)) return v;
        return c.t === "q" ? n / 1e3 : c.t === "v" ? n / 1e5 : n;
      }
      function fetchRows(gs, signal) {
        if (!st.monthId) return Promise.resolve({ columns: [], rows: [], total: 0 });
        var body = q(gs, true), rkey = JSON.stringify(body), skey = JSON.stringify([body.grain, body.month_id, body.col_filters, body.gap]);
        var rowsP = st.rowsCache.key === rkey ? Promise.resolve(st.rowsCache.data) : M.post(API + "/rows", body, { signal: signal }).then(function (d) { st.rowsCache = { key: rkey, data: d }; return d; });
        // The summary rides the same filters, so the tiles and the totals row describe exactly the rows underneath them.
        var sumP = st.sumCache.key === skey ? Promise.resolve(st.sumCache.data) : M.post(API + "/summary", body, { signal: signal }).then(function (d) { st.sumCache = { key: skey, data: d }; return d; });
        loadHeadGap(body, skey);
        return Promise.all([rowsP, sumP]).then(function (r) {
          var d = r[0], s = r[1] || {}, raw = d.columns || [], keep = [];
          raw.forEach(function (c, i) { if (c !== "MONTH_ID") keep.push(i); });
          var colsOut = keep.map(function (i) { return raw[i]; });
          var defs = colsOut.map(colOf);
          st.summary = s; paintTiles();
          var rows = (d.rows || []).map(function (row) { return keep.map(function (i, j) { var c = defs[j]; return c ? scale(c, row[i]) : row[i]; }); });
          var totals = {};
          colsOut.forEach(function (k, j) {
            var c = defs[j]; if (!c) return;
            if (c.t === "q" || c.t === "v") totals[k] = s[k] == null || s[k] === "" ? "" : scale(c, s[k]);
            else if (c.t === "b") totals[k] = int(s["N_" + k]) + " yes · " + int(s["N_NOT_" + k]) + " no";
          });
          if (colsOut.length) totals[colsOut[0]] = "Total · " + int(s.ROWS_TOTAL != null ? s.ROWS_TOTAL : d.total) + " rows";
          return { columns: colsOut, rows: rows, total: d.total, totals: totals };
        });
      }
      // "N store-categories have a sale plan and no transfer-in": the headline exception, counted under the same filters.
      function loadHeadGap(body, skey) {
        var hg = HEAD_GAP[st.grain];
        if (st.gap === hg[0]) { st.headGap = null; return; }      // the main summary already counts it
        var key = skey + "|" + hg[0];
        if (st.gapCache.key === key) return;
        st.gapCache = { key: key, n: null }; st.headGap = null;
        M.post(API + "/summary", Object.assign({}, body, { gap: hg[0], page: 1 })).then(function (s) {
          if (st.gapCache.key !== key || !ctx.alive()) return;
          st.gapCache.n = s ? s.ROWS_TOTAL : null; st.headGap = st.gapCache.n; paintTiles();
        }, function () { /* the tile just stays blank */ });
      }
      function values(c, gs) {
        if (filterable().indexOf(c) < 0) return Promise.reject(new Error("only the dimension columns (" + filterable().join(", ") + ") can be filtered here"));
        // Fetched with the OTHER filters applied but not this column's (the server skips it), so a filter can be widened again.
        return M.post(API + "/col-values?col=" + encodeURIComponent(c), q(gs, false)).then(function (r) { return r.values || []; });
      }
      function doExport(gs) {
        if (!me.can.export) { M.toast("You do not have MIS export access.", "bad"); return Promise.resolve(); }
        var m = monthMeta();
        M.toast("Generating Excel…");
        return M.download("POST", API + "/export", q(gs, false), "PlanLive360_" + st.grain + "_" + (m ? m.label : st.monthId) + ".xlsx")
          .then(function (n) { M.toast("Downloaded " + n, "ok"); }, function (e) { M.toast("Export failed: " + e.message, "bad"); throw e; });
      }

      function buildGrid() {
        M.clear(gridCard);
        st.rowsCache = { key: "", data: null }; st.sumCache = { key: "", data: null }; st.gapCache = { key: "", n: null }; st.summary = null; st.headGap = null;
        if (!st.months.length) { gridCard.classList.add("hidden"); grid = null; return Promise.resolve(); }
        gridCard.classList.remove("hidden");
        gridCard.appendChild(el("div", { "class": "card-h" }, [el("h2", { text: st.grain === "store" ? "Store × Category" : "RDC × Category · Purchase" }),
          el("span", { "class": "note", text: st.grain === "store" ? "Sale, space and transfer-in per store and category. Coverage says which plans have a row for the key."
            : "The purchase plan has no store dimension, so it lives at RDC × category; store plans are rolled up to it. Cross-check compares transfer-in with the purchase plan's transfer-out." })]));
        grid = M.Grid(gridCard, {
          id: "plan-360-" + st.grain, title: "", limit: 500, pageSizes: [200, 500, 1000, 2000], conditions: false,
          isKey: function (k) { var c = colOf(k); return !c || c.t === "s"; },
          format: function (k) { var c = colOf(k); if (!c) return undefined; return c.t === "s" || c.t === "b" ? "text" : c.t === "n" ? undefined : "dec"; },
          label: function (k) { var c = colOf(k); return c ? c.h : M.label(k); },
          groups: function (vis) {
            var out = [];
            vis.forEach(function (k) { var c = colOf(k), g = c ? c.grp : ""; var last = out[out.length - 1];
              if (last && last.key === g) last.span++; else out.push({ key: g, label: GROUP_LABEL[g] || g, span: 1 }); });
            return out;
          },
          fetch: fetchRows, values: values,
          cell: function (td, k, v) {
            var c = colOf(k), blank = v === "" || v === null || v === undefined;
            if (c && c.t === "b" && !blank) { M.clear(td); td.appendChild(el("span", { "class": "chip " + (truthy(v) ? "ok" : "grey"), text: truthy(v) ? "Yes" : "No" })); return; }
            if (blank) { td.textContent = "—"; td.classList.add("muted"); td.title = "This plan has no row for this key"; }
          },
          exports: me.can.export ? [{ label: "Excel - the filtered grid, raw units (coverage as Yes / No)", fn: function (gs) { doExport(gs).catch(function () { /* toasted */ }); } }] : []
        });
        return grid.applyDefaultView().then(function () { if (ctx.alive() && grid) return grid.reload(true); });
      }

      // ── masthead tiles ─────────────────────────────────────────────────────────────────────────────────────────────
      function paintTiles() {
        var s = st.summary, snap = st.snapshot || {}, tiles = [];
        var snapTile = { k: "Snapshot", v: snap.status === "NEVER" || !snap.refreshed_at ? "Never built" : M.ago(snap.refreshed_at),
                         s: snap.stale ? "stale - a plan changed since" : (snap.refreshed_by ? "by " + snap.refreshed_by : "current"), cls: snap.stale ? "down" : "up",
                         title: snap.refreshed_at ? "Built " + dt(snap.refreshed_at) : "" };
        if (!st.months.length) { M.setHeader({ tiles: [snapTile] }); return; }
        if (!s) { M.setHeader({ tiles: [{ k: "Rows", skel: true }, { k: "Sale value", skel: true }, { k: "Transfer in", skel: true }, { k: "Coverage gap", skel: true }, snapTile] }); return; }
        var m = monthMeta(), hg = HEAD_GAP[st.grain];
        var gapN = st.gap === hg[0] ? s.ROWS_TOTAL : st.headGap;
        tiles.push({ k: st.grain === "store" ? "Store-categories" : "RDC-categories", v: int(s.ROWS_TOTAL), s: (m ? m.label : "") + (st.gap ? " · exception filter on" : "") });
        tiles.push({ k: "Sale value (W/STR)", v: s.SALE_W_V == null || s.SALE_W_V === "" ? "–" : M.compact(s.SALE_W_V), s: s.SALE_W_Q == null || s.SALE_W_Q === "" ? "" : M.compact(s.SALE_W_Q) + " qty" });
        tiles.push({ k: "Transfer in qty", v: s.TRF_IN_STK_Q == null || s.TRF_IN_STK_Q === "" ? "–" : M.compact(s.TRF_IN_STK_Q), s: "closing " + (s.TRF_CL_STK == null || s.TRF_CL_STK === "" ? "–" : M.compact(s.TRF_CL_STK)) });
        if (st.grain === "rdc") tiles.push({ k: "Purchase qty", v: s.PP_BGT_PUR_Q == null || s.PP_BGT_PUR_Q === "" ? "–" : M.compact(s.PP_BGT_PUR_Q),
                                             s: "DC short " + (s.PP_DC_SHORT_Q == null || s.PP_DC_SHORT_Q === "" ? "–" : M.compact(s.PP_DC_SHORT_Q)) });
        tiles.push({ k: hg[1], v: gapN == null ? "…" : int(gapN), s: hg[2], cls: gapN ? "down" : "up", title: int(gapN) + " " + hg[2] });
        if (st.grain === "store") {
          tiles.push({ k: "No space plan", v: int(s.N_NOT_HAS_SPACE), s: "store-categories outside the space plan" });
          tiles.push({ k: "No sale plan", v: int(s.N_NOT_HAS_SALE), s: "planned by transfer-in or space only" });
        } else {
          tiles.push({ k: "No purchase", v: int(s.N_NOT_HAS_PURCHASE), s: "RDC-categories with no purchase-plan row" });
          tiles.push({ k: "No store plans", v: int(s.N_NOT_HAS_STORE_PLANS), s: "purchase only, nothing rolls up" });
        }
        tiles.push(snapTile);
        M.setHeader({ tiles: tiles });
      }

      // ── Refresh snapshot (mis.admin): confirm, single-flight, then re-read everything ──────────────────────────────
      function refreshSnapshot() {
        if (inflight) { M.toast("A snapshot rebuild is already running - it will finish on its own."); return; }
        M.confirm("Refresh the snapshot?", "This rebuilds the Plan Live 360 snapshot from all four plan views (Sale, Space, Transfer In, Purchase). " +
          "It takes tens of seconds of warehouse time; the grid keeps showing the current snapshot until it is done.", "Rebuild snapshot").then(function (yes) {
          if (!yes || !ctx.alive()) return;
          inflight = M.action(refreshBtn, "Rebuilding…", function () {
            M.toast("Rebuilding the snapshot from all four plans - this takes a few moments…");
            return M.post(API + "/refresh", {});
          }, function (r) {
            return (r.joined ? "Joined the rebuild already running - " : "Snapshot rebuilt - ") + int(r.rows_store) + " store rows, " + int(r.rows_rdc) + " RDC rows in " +
              Math.round(Number(r.duration_ms || 0) / 1000) + "s";
          }).then(function () {
            inflight = null;
            if (!ctx.alive()) return;
            return reloadAll(false);
          }, function () { inflight = null; if (ctx.alive()) loadMeta().catch(function () { /* shown */ }); });
        });
      }

      // Re-read the months / snapshot / lineage; keepGrid keeps the grid (and its filters) and only drops the cached pages.
      function reloadAll(keepGrid) {
        return loadMeta().then(function () {
          if (!ctx.alive()) return;
          paintEmpty();
          if (keepGrid && grid && st.months.length) {
            st.rowsCache = { key: "", data: null }; st.sumCache = { key: "", data: null }; st.gapCache = { key: "", n: null };
            return grid.reload();
          }
          return buildGrid();
        });
      }

      function paintEmpty() {
        M.clear(emptyBox);
        if (st.months.length) return;
        var never = !st.snapshot || st.snapshot.status === "NEVER";
        emptyBox.appendChild(el("div", { "class": "card" }, el("div", { "class": "card-b stack", style: { alignItems: "center", textAlign: "center", padding: "40px 18px" } }, [
          M.icon("plan", 34), el("h2", { style: { margin: 0, fontSize: "16px" }, text: never ? "The snapshot has never been built" : "No plan data yet" }),
          el("p", { "class": "muted", style: { margin: 0 }, text: never ? (me.can.admin ? "Press Refresh snapshot above to build it from the four plan views." : "Ask an MIS administrator to refresh the snapshot.")
            : "Save a month plan or space plan, or run the monthly transfer-in engine." })])));
      }

      if (refreshBtn && inflight) { refreshBtn.disabled = true; refreshBtn.innerHTML = '<span class="spin"></span> Rebuilding…';
        inflight.then(function () { if (ctx.alive()) { M.go(location.pathname + location.search, true); } }, function () { /* toasted */ }); }
      return loadMeta().then(function () {
        if (!ctx.alive()) return;
        paintEmpty();
        return buildGrid();
      });
    }
  });
})();

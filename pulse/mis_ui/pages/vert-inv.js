// Vertical Inventory (Article) (DFS /sf-mis-vert-inv-art, SfVertInvArt.jsx): plant x ARTICLE x SLOC opening / IN / OUT /
// closing / negative stock over BRONZE.MIS_VERT_INV_ART - as-of date (or the latest ET reading), dynamic Group-by chips in
// click order (+ store-master SM: and product-master PM: columns), measures, father / child grid columns, K · Lakh or raw
// units, tick-list filters, search, Excel-style header conditions (dim_conds / meas_conds), CSV export and the admin
// Rebuild of an as-of date.
// API: /api/v1/mis/vert-inv-art/{params, result, values, export, build}.
// The same grid "locked to a saved Inventory Report" (SfInvReports.jsx -> SfVertInvArt report=...) is
// MIS.vertInv.mount(ctx, {report}) - used by inv-reports.js; it runs through /api/v1/mis/inv-report/{run, col-values, export}.
(function () {
  "use strict";
  var M = window.MIS, el = M.el;

  var DEFAULT_DIMS = ["ST_CD", "ST_TYP", "SEG", "DIV", "SUB_DIV", "MJ", "GEN_ART", "CLR", "ART", "SLOC", "SRC"];
  var DIM_LABEL = { ST_CD: "Plant", ART: "ARTICLE" };
  var CARD = [["OP_Q", "OP-STK-Q"], ["OP_V", "OP-STK-V"], ["IN_Q", "IN-Q"], ["OUT_Q", "OUT-Q"], ["CL_Q", "CL-STK-Q"], ["CL_V", "CL-STK-V"],
              ["NEG_Q", "NEG-CL-STK-Q"], ["NEG_V", "NEG-CL-STK-V"]];
  var HINT = {
    OP_Q: "Opening: the source's previous available day, net (negative articles included)",
    CL_Q: "Closing: the as-of day, net (negative articles included)",
    IN_Q: "Moved in between the OP and CL readings. ET_STOCK_DATA rows: MATDOC receipts (SHKZG S), LGORT 0099 = stock in transit; DC bins: LTAP NLTYP + NISTM. PTL / PRD / hub PRD / pick: no movement source, 0",
    OUT_Q: "Moved out between the OP and CL readings. ET_STOCK_DATA rows: MATDOC issues (SHKZG H), LGORT 0099 = stock in transit; DC bins: LTAP VLTYP + VISTM. PTL / PRD / hub PRD / pick: no movement source, 0",
    NEG_Q: "Negative closing: articles below zero at this plant x SLOC",
    SRC: "Which table the row comes from",
    SLOC_DESC: "T001L storage-location name; derived codes (MSA_, PTL-, -PRD, HUB_) are their own name",
    MAP: "Moving average price: AVG(VERPR) of the article over its plants in MBEW, Rs per piece (not scaled by K · Lakh)",
    ART_TYPE: "Article type (product master ARTICLE_TYPE, e.g. 1110 / 1510)",
    ART: "Article = the source table's MATNR (variant, leading zeros stripped)"
  };
  var SRC_DAYS = [["ET_STOCK_DATA", "et"], ["LQUA", "lq"], ["PTL", "ptl"], ["RDC-PRD", "bcg"], ["HUB-PRD", "hub"], ["PICK", "pick"]];
  // Columns that ride along with a Group-by column (or are the grid's own) - a header condition on them survives a chip change.
  var RIDERS = ["ST_NM", "ART_DESC", "SLOC_DESC", "GRID_COL", "GRID_VAL"];
  var ABOUT = "Plant × ARTICLE × SLOC (article = the source's MATNR, with its SEG / DIV / SUB_DIV / MJ / GEN_ART / CLR), opening / closing / " +
    "negative stock - the Vertical Inventory report without the article summed away. ET_STOCK_DATA LGORTs: stores 0001–0099, DCs 0032 / 0042 / " +
    "0044 / 0099, hubs 0032 / 0033 / 0044 / 0099 (0099 = stock in transit); DCs also show their LQUA bins for 0001 (MSA_…; the V02 bins by LAGP " +
    "status as MSA_V02_FRESH / MSA_V02_GRT); PTL, RDC-PRD and hub PRD sit at the receiving store, and hubs also show their own HUB_FRESH / HUB_GRT. " +
    "Articles are SEG APP / GM / MKT / NT (hard tags, packing), DIV not SCRAP, counted in pieces (EA) - fabric, trims and SCRAP are left out. " +
    "OP and CL are net stock (negative articles included); NEG-CL shows the negatives alone. IN / OUT are the moves between the two readings " +
    "(MATDOC for ET_STOCK_DATA rows, LTAP for DC bins), so OP + IN − OUT = CL there.";

  function fixed(n, d) { return Number(n).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }); }

  function mount(ctx, opts) {
    opts = opts || {};
    var rep = opts.report || null, rdef = rep ? (rep.definition || {}) : null;
    var me = ctx.me || { can: {} };
    var st = {
      meta: null, res: null, sel: {}, search: "", building: false,
      dims: rdef ? (rdef.group_by || DEFAULT_DIMS).slice() : arr(M.store.get("via.dims", null), DEFAULT_DIMS),
      measures: rdef ? (rdef.measures || []).slice() : arr(M.store.get("via.meas", null), []),
      gridCols: rdef ? (rdef.grid || []).slice() : arr(M.store.get("via.grid", null), []),
      units: rdef ? (rdef.units === "raw" ? "raw" : "kl") : (M.store.get("via.units", "kl") === "raw" ? "raw" : "kl")
    };
    function arr(v, d) { return Array.isArray(v) && v.length ? v.slice() : d.slice(); }

    // ── vocabulary helpers ───────────────────────────────────────────────────────────────────────
    function meta() { return st.meta || {}; }
    function isMeas(c) { return (meta().measures || []).indexOf(c) >= 0; }
    function isAttr(c) { return (meta().attrs || []).indexOf(c) >= 0; }
    function isPct(c) { return /_PCT$/.test(c); }
    function pmP() { return meta().pmPrefix || "PM:"; }
    function smP() { return meta().smPrefix || "SM:"; }
    function isExtra(d) { return d.indexOf(pmP()) === 0 || d.indexOf(smP()) === 0; }
    function dimLabel(d) {
      if (DIM_LABEL[d]) return DIM_LABEL[d];
      if (d.indexOf(pmP()) === 0) return d.slice(pmP().length);
      if (d.indexOf(smP()) === 0) return d.slice(smP().length);
      return d;
    }
    function label(c) { return (meta().labels || {})[c] || dimLabel(c); }
    function fmtM(c, v) {
      if (v === null || v === undefined || v === "") return "—";
      var n = Number(v);
      if (st.units === "kl") return fixed(n / (/_Q$/.test(c) ? 1000 : 100000), 3);
      return fixed(n, 0);
    }
    function unitWord(c) { return st.units === "kl" ? (/_Q$/.test(c) ? "thousand pcs" : "Rs lakh") : (/_Q$/.test(c) ? "pcs" : "Rs"); }
    function colFilters() { var f = {}; Object.keys(st.sel).forEach(function (k) { if (st.sel[k] && st.sel[k].length) f[k] = st.sel[k]; }); return f; }
    function nFilters() { var n = 0; Object.keys(st.sel).forEach(function (k) { n += (st.sel[k] || []).length; }); return n; }

    // A measure value in a header condition is typed in the units on screen: under K · Lakh "1.5" on a quantity means
    // 1,500 pieces - scaled here before it is sent (as DFS does).
    function scaleV(c, v) {
      if (v === undefined || v === null || v === "" || st.units !== "kl") return v;
      var n = Number(String(v).replace(/,/g, ""));
      return isFinite(n) ? String(n * (/_Q$/.test(c) ? 1000 : 100000)) : v;
    }
    // Grid header state -> the engine's dim_conds (inside the scan) / meas_conds (on the finished totals).
    function hdrConds(gs) {
      var dimC = [], measC = [], bad = [];
      function add(x) {
        if (isMeas(x.col)) {
          var y = { col: x.col, op: x.op };
          if (x.v1 !== undefined) y.v1 = scaleV(x.col, x.v1);
          if (x.v2 !== undefined) y.v2 = scaleV(x.col, x.v2);
          if (x.vals) y.vals = x.vals.map(function (v) { return scaleV(x.col, v); });
          measC.push(y);
        } else if (isAttr(x.col) || isPct(x.col)) bad.push(label(x.col));
        else dimC.push(x);
      }
      Object.keys(gs.filters || {}).forEach(function (c) { var v = gs.filters[c]; if (v && v.length) add({ col: c, op: "in", vals: v.slice() }); });
      (gs.conds || []).forEach(function (x) {
        var o = { col: x.col, op: x.op };
        if (x.op !== "blank" && x.op !== "nonblank") { o.v1 = x.v1; if (x.op === "between") o.v2 = x.v2; }
        add(o);
      });
      if (bad.length) throw new Error(bad.join(", ") + " cannot be filtered (a per-article attribute / a share %) - remove that filter.");
      return { dim_conds: dimC, meas_conds: measC };
    }
    function body(gs) {
      var h = hdrConds(gs);
      var b = { as_of: meta().asOf || null, col_filters: colFilters(), search: st.search || null, sort_col: gs.sort_col || null, sort_dir: gs.sort_dir,
                dim_conds: h.dim_conds, meas_conds: h.meas_conds, page: gs.page, limit: gs.limit };
      if (rep) b.slug = rep.slug;
      else { b.dims = st.dims; b.measures = st.measures.length ? st.measures : null; b.grid = st.gridCols.length ? st.gridCols : null; }
      return b;
    }

    // ── masthead ─────────────────────────────────────────────────────────────────────────────────
    var buildBtn = me.can.admin ? el("button", { type: "button", "class": "btn", title: "Re-read every source for this as-of date and replace its stored slice (~45 s)" },
      [M.icon("gear", 15), "Rebuild…"]) : null;
    var actions = [];
    if (rep && me.can.manage && String(rep.createdBy) === String(me.emp_code))
      actions.push(el("a", { "class": "btn", href: "/mis/inv-reports?edit=" + encodeURIComponent(rep.slug) }, [M.icon("edit", 15), "Edit report"]));
    if (rep && me.can.dumps)
      actions.push(el("a", { "class": "btn", href: "/mis/dumps?new=inv_report&target=" + encodeURIComponent(rep.slug), title: "Write this report to a folder on a schedule (the DFS Write to folder)" },
        [M.icon("clock", 15), "Schedule a dump…"]));
    if (buildBtn) actions.push(buildBtn);
    M.setHeader({
      title: rep ? rep.name : "Vertical Inventory (Article)",
      sub: rep ? (rep.description || "An Inventory Report: the Vertical Inventory (Article) grid locked to this report's Group by and measures.")
               : "Plant × article × storage location - opening, IN / OUT, closing and negative stock of one as-of date, from BRONZE.MIS_VERT_INV_ART.",
      crumbs: rep ? [{ label: "Inventory Reports", href: "/mis/inv-reports" }, { label: rep.name }] : undefined,
      actions: actions,
      tiles: rep ? [{ k: "As of", skel: true }, { k: "Rows", skel: true }, { k: "Group by", skel: true }, { k: "Units", skel: true }]
                 : [{ k: "As of", skel: true }].concat(CARD.map(function (c) { return { k: c[1], skel: true }; }))
    });
    function paintTiles() {
      var d = st.res, total = d ? M.fmt(d.total, "int") : "–";
      if (rep) {
        M.setHeader({ tiles: [{ k: "As of", v: M.fmtDate(meta().asOf), s: meta().asOf === meta().latestEtDate ? "latest ET reading" : "latest " + (meta().latestEtDate || "–") },
          { k: "Rows", v: total, s: (nFilters() || st.search) ? "after filters" : "" },
          { k: "Group by", v: (rdef.group_by || []).length + " columns", s: (rdef.group_by || []).map(dimLabel).join(" × "), title: (rdef.group_by || []).map(dimLabel).join(" × ") },
          { k: "Measures", v: (rdef.measures || []).length, s: (rdef.measures || []).map(label).join(", ") },
          { k: "Units", v: st.units === "kl" ? "K · Lakh" : "pcs / Rs", s: st.units === "kl" ? "thousand pcs / Rs lakh" : "pieces / rupees" }] });
        return;
      }
      var t = (d && d.totals) || {};
      M.setHeader({ tiles: [{ k: "As of", v: M.fmtDate(meta().asOf), s: total + " rows" + ((nFilters() || st.search) ? " after filters" : "") }].concat(
        CARD.filter(function (c) { return c[0] in t; }).map(function (c) {
          return { k: c[1], v: fmtM(c[0], t[c[0]]), s: unitWord(c[0]), title: HINT[c[0]] || c[1], cls: /^NEG/.test(c[0]) ? "down" : "" };
        })) });
    }

    // ── load the build of a date (the first open of a date builds it, ~45 s) ──────────────────────────
    function loadMeta(d) {
      return M.get("/api/v1/mis/vert-inv-art/params" + (d ? "?as_of=" + encodeURIComponent(d) : ""));
    }
    var boot = el("div", { "class": "card" }, el("div", { "class": "card-b muted" }, [el("span", { "class": "spin" }),
      " Reading the build - the first open of a date builds it (about 45 seconds, ~23M rows)…"]));
    ctx.main.appendChild(boot);

    return loadMeta("").then(function (p) {
      if (!ctx.alive()) return;
      boot.remove(); st.meta = p;
      return ui();
    }, function (e) {
      if (!ctx.alive()) return;
      M.clear(boot).appendChild(el("div", { "class": "card-b" }, el("div", { "class": "notice bad", text: e.message })));
    });

    function ui() {
      // ── filter bar ─────────────────────────────────────────────────────────────────────────────
      var date = el("input", { type: "date", value: meta().asOf || "", max: meta().latestEtDate || null, "aria-label": "As of date" });
      var latest = el("button", { type: "button", "class": "btn sm", text: "Latest", title: "The latest ET_STOCK_DATA reading" });
      date.addEventListener("change", function () { if (date.value && date.value !== meta().asOf) pickDate(date.value); });
      latest.addEventListener("click", function () { if (meta().asOf !== meta().latestEtDate) pickDate(""); });
      var search = el("input", { type: "search", placeholder: "plant, name, MJ, SLOC or article…", style: { minWidth: "260px" }, "aria-label": "Search" });
      search.addEventListener("input", M.debounce(function () { st.search = search.value.trim(); grid.reload(true); }, 400));
      var units = el("div", { "class": "seg", role: "group", "aria-label": "Units", title: "K · Lakh: quantity in thousands, value in lakhs (as in the sample workbook). Raw: pieces and rupees." }, [
        el("button", { type: "button", "aria-pressed": String(st.units === "kl"), text: "K · Lakh" }), el("button", { type: "button", "aria-pressed": String(st.units === "raw"), text: "Raw" })]);
      units.querySelectorAll("button").forEach(function (b, i) { b.addEventListener("click", function () {
        var u = i === 0 ? "kl" : "raw"; if (u === st.units) return; setUnits(u); grid.reload(); }); });
      function setUnits(u) {
        st.units = u; if (!rep) M.store.set("via.units", u);
        units.querySelectorAll("button").forEach(function (x, j) { x.setAttribute("aria-pressed", String((j === 0) === (u === "kl"))); });
      }
      var fCount = el("span", { "class": "cnt" });
      var fBtn = el("button", { type: "button", "class": "btn", "aria-expanded": "false" }, [M.icon("filter", 15), "Filters", fCount]);
      var fbar2 = el("div", { "class": "fbar hidden" });
      fBtn.addEventListener("click", function () { var open = fbar2.classList.toggle("hidden") === false; fBtn.setAttribute("aria-expanded", String(open)); });
      function paintFCount() { var n = nFilters(); fCount.textContent = n ? " " + n : ""; fCount.style.fontWeight = "600"; fCount.style.color = "var(--brand)"; }
      var fms = (meta().filterCols || []).map(function (c) {
        var ms = M.multiSelect(label(c), function () { return Promise.resolve((meta().options || {})[c] || []); }, st.sel[c] || [], function (v) {
          if (v.length) st.sel[c] = v; else delete st.sel[c]; paintFCount(); grid.reload(true);
        });
        fbar2.appendChild(ms.el); return { c: c, ms: ms };
      });
      var clearF = el("button", { type: "button", "class": "btn ghost sm", text: "Clear all filters" });
      clearF.addEventListener("click", function () { st.sel = {}; fms.forEach(function (x) { x.ms.set([]); }); paintFCount(); grid.reload(true); });
      fbar2.appendChild(el("span", { "class": "spacer" })); fbar2.appendChild(clearF);
      paintFCount();

      var ctl = el("div", { "class": "card" });
      ctl.appendChild(el("div", { "class": "fbar" }, [
        el("label", { "class": "f" }, ["As of", el("div", { "class": "row", style: { gap: "6px" } }, [date, latest])]),
        el("label", { "class": "f" }, ["Search", search]), el("label", { "class": "f" }, ["Units", units]),
        el("label", { "class": "f" }, ["Tick-list filters", fBtn])]));
      ctl.appendChild(fbar2);

      // ── layout: Group by chips, measures, father / child grid (standalone) / the locked definition (report) ──
      var layout = el("div", { "class": "card-b stack", style: { gap: "10px", borderBottom: "1px solid var(--line-2)" } });
      ctl.appendChild(layout);
      var chipsBox = el("div", { "class": "row", style: { gap: "6px" } }), msMeas = null, msGrid = null;
      if (rep) layout.appendChild(defSummary());
      else {
        layout.appendChild(chipsBox);
        msMeas = M.multiSelect("Measures", function () { return Promise.resolve((meta().measures || []).map(function (m) { return { value: m, label: label(m) }; })); },
          st.measures, function (v) { st.measures = v; M.store.set("via.meas", v); pruneHeader(); grid.reload(true); }, { allText: "All measures" });
        msGrid = M.multiSelect("Grid (child rows)", function () { return Promise.resolve(gridChoices()); }, st.gridCols, function (v) {
          if (v.length > 10) { M.toast("At most 10 grid columns - keeping the first 10.", "bad"); v = v.slice(0, 10); msGrid.set(v); }
          st.gridCols = v.filter(function (c) { return st.dims.indexOf(c) < 0; }); M.store.set("via.grid", st.gridCols); pruneHeader(); grid.reload(true);
        }, { allText: "None - plain view" });
        layout.appendChild(el("div", { "class": "row", style: { alignItems: "flex-end", gap: "10px 14px" } }, [msMeas.el, msGrid.el,
          el("span", { "class": "muted tiny", style: { maxWidth: "70ch" }, text: "Grid view: every column picked under Grid adds a block of child rows (GRID / VALUE) under each Group-by row, with a share % per measure; none = the plain view." })]));
        paintChips();
      }
      var info = el("div", { "class": "card-b tiny muted", style: { lineHeight: "1.7" } });
      ctl.appendChild(info);
      paintInfo();
      ctx.main.appendChild(ctl);

      if (rep) ctx.main.appendChild(el("div", { "class": "notice" }, [M.icon("info", 16), el("span", { text:
        "The formatted (template) export and Write to folder of DFS are not available in V2Pulse yet. The CSV export below carries exactly what the grid shows; " +
        "to write this report to a folder, use Schedule a dump…" })]));

      // ── the grid ───────────────────────────────────────────────────────────────────────────────
      var gcard = el("div", { "class": "card" });
      ctx.main.appendChild(gcard);
      var grid = M.Grid(gcard, {
        id: rep ? "inv-report." + rep.slug : "vert-inv", title: rep ? rep.name : "Vertical inventory", limit: 200, pageSizes: [200, 1000, 5000],
        isKey: function (c) { return !isMeas(c) && !isPct(c) && !isAttr(c); },
        format: function (c) { return isMeas(c) ? (st.units === "raw" ? "int" : "dec") : isPct(c) ? "pct" : isAttr(c) ? "dec" : "text"; },
        label: label,
        fetch: function (gs, signal) {
          var b;
          try { b = body(gs); } catch (e) { return Promise.reject(e); }
          return M.post(rep ? "/api/v1/mis/inv-report/run" : "/api/v1/mis/vert-inv-art/result", b, { signal: signal }).then(function (d) {
            if (!ctx.alive()) return { columns: [], rows: [], total: 0 };
            st.res = d; paintTiles();
            var out = { columns: d.columns || [], rows: d.rows || [], total: d.total || 0 };
            // No TOTAL row on an opened Inventory Report (DFS user ask 2026-09-28); on a father / child view these are the fathers' totals.
            if (!rep && d.totals) {
              out.totals = {};
              (d.columns || []).forEach(function (c) { if (c in d.totals) out.totals[c] = st.units === "kl" ? d.totals[c] / (/_Q$/.test(c) ? 1000 : 100000) : d.totals[c]; });
            }
            return out;
          });
        },
        // The tick-list behind a header's "is any of": the page's filters apply, its header conditions do not.
        values: function (c) {
          if (isMeas(c)) return Promise.reject(new Error("a measure has no value list - use a condition above"));
          if (isAttr(c) || isPct(c)) return Promise.reject(new Error("this column cannot be filtered"));
          var b = { as_of: meta().asOf || null, col_filters: colFilters(), search: st.search || null, col: c };
          if (rep) b.slug = rep.slug; else { b.dims = st.dims; b.grid = st.gridCols.length ? st.gridCols : null; }
          return M.post(rep ? "/api/v1/mis/inv-report/col-values" : "/api/v1/mis/vert-inv-art/values", b).then(function (r) {
            var v = (r.values || []).filter(function (x) { return String(x).trim() !== ""; });
            if (r.capped) M.toast("Only the first " + M.fmt(v.length, "int") + " values are listed - use Search or a Contains condition for the rest.");
            return v;
          });
        },
        cell: function (td, c, v) {
          if (isMeas(c)) { td.textContent = fmtM(c, v); if (!Number(v)) td.style.color = "var(--muted)"; }
          else if (isPct(c)) { td.textContent = v === null || v === undefined || v === "" ? "—" : fixed(v, 2) + "%"; td.classList.add("num"); }
          else if (isAttr(c)) { td.textContent = v === null || v === undefined || v === "" ? "—" : fixed(v, 2); td.classList.add("num"); }
          else if (v === null || v === undefined || v === "") { td.textContent = "—"; td.style.color = "var(--muted)"; }
        },
        onData: function () { polish(); },
        getExtra: function () { return rep ? { units: st.units, sel: st.sel, search: st.search } : { units: st.units, sel: st.sel, search: st.search, dims: st.dims, measures: st.measures, gridCols: st.gridCols }; },
        setExtra: function (x) {
          if (x.units) setUnits(x.units === "raw" ? "raw" : "kl");
          st.sel = x.sel || {}; fms.forEach(function (f) { f.ms.set(st.sel[f.c] || []); }); paintFCount();
          st.search = x.search || ""; search.value = st.search;
          if (!rep) {
            if (Array.isArray(x.dims) && x.dims.length) { st.dims = x.dims.slice(); M.store.set("via.dims", st.dims); paintChips(); }
            if (Array.isArray(x.measures)) { st.measures = x.measures.slice(); msMeas.set(st.measures); M.store.set("via.meas", st.measures); }
            if (Array.isArray(x.gridCols)) { st.gridCols = x.gridCols.slice(); msGrid.set(st.gridCols); msGrid.reset(); M.store.set("via.grid", st.gridCols); }
          }
        },
        exports: me.can.export ? [{ label: "CSV - the whole filtered selection, in the units on screen", fn: exportCsv }] : []
      });

      // Header hints (DFS column titles) and the K · Lakh footer at three decimals, after every draw that follows a fetch.
      function polish() {
        var cols = grid.columns(), ths = grid.el.querySelectorAll("thead tr.cols th"), tds = grid.el.querySelectorAll("tfoot td");
        cols.forEach(function (c, i) {
          if (ths[i] && HINT[c]) ths[i].title = label(c) + " - " + HINT[c];
          if (tds[i] && st.res && st.res.totals && isMeas(c) && c in st.res.totals) tds[i].textContent = fmtM(c, st.res.totals[c]);
        });
      }

      function exportCsv(gs) {
        var b;
        try { b = body(gs); } catch (e) { M.toast(e.message, "bad"); return; }
        delete b.page; delete b.limit; b.units = st.units;
        var stem = (rep ? rep.slug : "VERTICAL_INVENTORY_ARTICLE") + "_" + (meta().asOf || "") + (st.units === "kl" ? "_K_LAKH" : "") + ".csv";
        M.toast("Building the CSV…");
        M.download("POST", rep ? "/api/v1/mis/inv-report/export" : "/api/v1/mis/vert-inv-art/export", b, stem)
          .then(function (n) { M.toast("Downloaded " + n, "ok"); }, function (e) { M.toast(e.message, "bad"); });
      }

      // ── Group by chips (click order = column order) ─────────────────────────────────────────────
      function paintChips() {
        M.clear(chipsBox);
        chipsBox.appendChild(el("span", { "class": "section-title", style: { marginRight: "4px" }, text: "Group by" }));
        var all = (meta().dims || []).concat(st.dims.filter(function (d) { return isExtra(d); }));
        all.forEach(function (d) {
          var at = st.dims.indexOf(d);
          var b = el("button", { type: "button", "class": "chip" + (at >= 0 ? "" : " grey"), "aria-pressed": String(at >= 0),
                                 style: { cursor: "pointer", font: "inherit", fontSize: "12px" },
                                 title: (HINT[d] ? HINT[d] + ". " : "") + (at >= 0 ? "Column " + (at + 1) + " - click to remove" : "Click to add as the last column") }, [
            at >= 0 ? el("span", { style: { background: "var(--brand-2)", color: "#fff", borderRadius: "999px", padding: "0 5px", fontSize: "10.5px", fontWeight: "600" }, text: String(at + 1) }) : null,
            el("span", { text: dimLabel(d) + (d.indexOf(smP()) === 0 ? " (store)" : d.indexOf(pmP()) === 0 ? " (product)" : "") }),
            isExtra(d) ? el("span", { "aria-hidden": "true", text: "×" }) : null]);
          b.addEventListener("click", function () { toggleDim(d); });
          chipsBox.appendChild(b);
        });
        function adder(title, cols, prefix, tip) {
          if (!cols || !cols.length) return;
          var s = el("select", { "aria-label": title, title: tip, style: { fontSize: "12px", padding: "3px 8px", borderStyle: "dashed", borderRadius: "999px" } },
            [el("option", { value: "", text: title })].concat(cols.filter(function (c) { return st.dims.indexOf(prefix + c) < 0; }).map(function (c) { return el("option", { value: c, text: c }); })));
          s.addEventListener("change", function () { if (s.value) setDims(st.dims.concat([prefix + s.value])); });
          chipsBox.appendChild(s);
        }
        adder("+ store-master column…", meta().smCols, smP(), "Group by any store-master column - the store hierarchy (ZONE, REG, AREA, STATE, RDC …) first");
        adder("+ product-master column…", meta().pmCols, pmP(), "Group by any product-master column (joined on the article)");
        chipsBox.appendChild(el("button", { type: "button", "class": "btn ghost sm", text: "Reset", onclick: function () { setDims(DEFAULT_DIMS.slice()); } }));
        chipsBox.appendChild(el("span", { "class": "muted tiny", text: "Columns follow the click order; stock is summed over whatever is unticked." }));
      }
      function toggleDim(d) {
        var next = st.dims.indexOf(d) >= 0 ? st.dims.filter(function (x) { return x !== d; }) : st.dims.concat([d]);
        if (!next.length) { M.toast("Keep at least one Group-by column."); return; }
        setDims(next);
      }
      function setDims(next) {
        st.dims = next; M.store.set("via.dims", next);
        var g = st.gridCols.filter(function (c) { return next.indexOf(c) < 0; });
        if (g.length !== st.gridCols.length) { st.gridCols = g; msGrid.set(g); M.store.set("via.grid", g); }
        if (msGrid) msGrid.reset();
        paintChips(); pruneHeader(); grid.reload(true);
      }
      function gridChoices() {
        var all = (meta().dims || []).map(function (c) { return { value: c, label: dimLabel(c) }; })
          .concat((meta().smCols || []).map(function (c) { return { value: smP() + c, label: c + " (store)" }; }))
          .concat((meta().pmCols || []).map(function (c) { return { value: pmP() + c, label: c + " (product)" }; }));
        return all.filter(function (x) { return st.dims.indexOf(x.value) < 0; });
      }
      // A header filter lives on a header: unticking that column drops its filter with it, rather than leaving an
      // invisible condition narrowing the grid.
      function pruneHeader() {
        if (!grid) return;
        var s = grid.state();
        function keep(c) {
          if (isMeas(c)) return !st.measures.length || st.measures.indexOf(c) >= 0;
          if (c === "GRID_COL" || c === "GRID_VAL") return st.gridCols.length > 0;
          return st.dims.indexOf(c) >= 0 || RIDERS.indexOf(c) >= 0;
        }
        var f = {}; Object.keys(s.filters).forEach(function (c) { if (keep(c)) f[c] = s.filters[c]; });
        grid.setState({ filters: f, conds: s.conds.filter(function (x) { return keep(x.col); }) });
      }

      // ── a saved report's shape: shown, not editable here ──────────────────────────────────────────────
      function defSummary() {
        var box = el("div", { "class": "small", style: { lineHeight: "1.8" } });
        function part(k, v) { box.appendChild(el("span", { style: { marginRight: "18px", display: "inline-block" } }, [el("b", { "class": "muted tiny", style: { textTransform: "uppercase", letterSpacing: ".05em", marginRight: "6px" }, text: k }), v])); }
        part("Group by", (rdef.group_by || []).map(dimLabel).join(" × "));
        if ((rdef.grid || []).length) part("Grid", rdef.grid.map(dimLabel).join(", "));
        part("Measures", (rdef.measures || []).map(label).join(", "));
        var f = rdef.filters || {};
        if (Object.keys(f).length) part("Saved filters", Object.keys(f).map(function (c) { var v = f[c]; return c + ": " + (v.length > 4 ? v.slice(0, 4).join(", ") + " +" + (v.length - 4) : v.join(", ")); }).join(" · "));
        var conds = (rdef.dim_filters || []).concat(rdef.col_filters || []);
        if (conds.length) part("Conditions", conds.map(function (c) { return M.condLabel({ col: label(c.col), op: c.op, v1: c.v1, v2: c.v2, vals: c.vals }); }).join(" · "));
        box.appendChild(el("div", { "class": "muted tiny", text: "The report's Group by, measures and saved filters are its definition (Edit on Inventory Reports changes them). The date, tick-list filters, search, header filters, units and paging below stay live." }));
        return box;
      }

      // ── build info: which day each source was read for, and when the slice was built ─────────────────────
      function paintInfo(extra) {
        M.clear(info);
        if (extra) info.appendChild(extra);
        var b = meta().build;
        if (b) {
          var s = b.sources || {}, line = el("div");
          line.appendChild(el("b", { text: "CL / OP days  " }));
          SRC_DAYS.forEach(function (x) {
            var cl = s[x[1] + "_cl"], off = cl && cl !== meta().asOf;
            line.appendChild(el("span", { style: { marginRight: "14px", display: "inline-block" } }, [el("code", { text: x[0] }), " ",
              el("span", { style: off ? { color: "var(--warn)", fontWeight: "600" } : null, title: off ? "This source has no reading for " + meta().asOf + " - its latest earlier day is used" : null, text: cl || "no data" }),
              " / " + (s[x[1] + "_op"] || "—")]));
          });
          info.appendChild(line);
          info.appendChild(el("div", { text: "Built " + String(b.builtAt || "").replace("T", " ").slice(0, 16) + " by " + (b.builtBy || "–") + " in " + (b.secs || 0) + " s · " + M.fmt(b.rows, "int") + " stored rows" +
            (meta().latestEtDate ? " · latest ET reading " + meta().latestEtDate : "") }));
        }
        if (!rep) info.appendChild(el("details", { style: { marginTop: "4px" } }, [el("summary", { style: { cursor: "pointer" }, text: "How these numbers are built" }), el("p", { style: { margin: "6px 0 0", maxWidth: "140ch" }, text: ABOUT })]));
      }

      // ── as-of date / rebuild ─────────────────────────────────────────────────────────────────────
      function busyNote(text) { return el("div", { "class": "notice", style: { marginBottom: "6px" } }, [el("span", { "class": "spin" }), el("span", { text: text })]); }
      function pickDate(d) {
        date.disabled = latest.disabled = true;
        paintInfo(busyNote("Reading " + (d || "the latest date") + " - the first open of a date builds it (about 45 seconds)…"));
        loadMeta(d).then(function (p) {
          if (!ctx.alive()) return;
          st.meta = p; date.value = p.asOf || ""; date.max = p.latestEtDate || "";
          fms.forEach(function (x) { x.ms.reset(); });
          paintInfo(); grid.reload(true);
        }, function (e) {
          if (!ctx.alive()) return;
          date.value = meta().asOf || ""; paintInfo(el("div", { "class": "notice bad", style: { marginBottom: "6px" }, text: e.message }));
        }).then(function () { date.disabled = latest.disabled = false; });
      }
      if (buildBtn) buildBtn.addEventListener("click", function () {
        var d = meta().asOf;
        if (!d || st.building) return;
        M.confirm("Rebuild " + d + "?", "This re-reads every source (ET_STOCK_DATA, LQUA, PTL, RDC-PRD, hub PRD, pick, MATDOC, LTAP) for " + d +
          " and replaces its stored slice in BRONZE.MIS_VERT_INV_ART. It takes about 45 seconds and changes what every Inventory Report shows for that date.", "Rebuild", false).then(function (ok) {
          if (!ok || !ctx.alive()) return;
          st.building = true; date.disabled = latest.disabled = true;
          var t0 = Date.now(), note = busyNote(""), txt = note.lastChild;
          function tick() { txt.textContent = "Rebuilding " + d + " from the sources… " + Math.round((Date.now() - t0) / 1000) + " s (usually about 45 s)"; }
          tick(); var iv = setInterval(tick, 1000); paintInfo(note);
          M.action(buildBtn, "Rebuilding…", function () { return M.post("/api/v1/mis/vert-inv-art/build", { as_of: d }); }).then(function (r) {
            clearInterval(iv);
            if (!ctx.alive()) return;
            var b = (r && r.build) || {};
            M.toast("Rebuilt " + d + " from the sources: " + M.fmt(b.rows, "int") + " rows in " + (b.secs || 0) + " s.", "ok");
            return loadMeta(d).then(function (p) { if (!ctx.alive()) return; st.meta = p; fms.forEach(function (x) { x.ms.reset(); }); paintInfo(); grid.reload(); });
          }, function (e) {
            clearInterval(iv);
            if (ctx.alive()) paintInfo(el("div", { "class": "notice bad", style: { marginBottom: "6px" }, text: "Rebuild failed: " + e.message }));
          }).then(function () { st.building = false; date.disabled = latest.disabled = false; }, function () { st.building = false; date.disabled = latest.disabled = false; });
        });
      });

      return grid.applyDefaultView().then(function () { if (ctx.alive()) return grid.reload(true); });
    }
  }

  M.vertInv = { mount: mount, DEFAULT_DIMS: DEFAULT_DIMS };
  M.page("vert-inv", { render: function (ctx) { return mount(ctx, {}); } });
})();

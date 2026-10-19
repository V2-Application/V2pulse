// Day Wise Trend Reports (DFS /sf-mis-day-trend-reports, SfDayTrendReports.jsx SfDayTrendReportManager): the card
// gallery of saved Day Wise Trend reports and their builder. A report is a definition - Group by (in pick order), stock
// tags, sale measures, V2R plants, the stock-total mode, stores-only, saved filters, conditions and a window of the last
// N loaded days - stored in BRONZE.MIS_DAY_TREND_REPORT_DEF (shared with DFS). Opening one is the Day Wise Trend page
// locked to it (/mis/day-trend-reports/r/<slug>, pages/day-trend.js). ?edit=<slug> opens the builder on that report.
// API: /api/v1/mis/day-trend-report/reports (GET / POST / DELETE {id}), /api/v1/mis/day-trend/{params, result, values}.
// The builder's widgets (condition panel, filter panels, V2R picker) come from MIS.dtKit in pages/day-trend.js.
(function () {
  "use strict";
  var M = window.MIS, el = M.el;
  var API = "/api/v1/mis/day-trend-report/reports";

  // pages/day-trend.js publishes MIS.dtKit; load it the way core.js loads a page module (same cache key).
  function kit() {
    if (M.dtKit) return Promise.resolve(M.dtKit);
    if (!M.cache["mod:day-trend"]) M.cache["mod:day-trend"] = new Promise(function (res, rej) {
      var s = el("script", { src: "/mis/static/pages/day-trend.js?v=" + (window.MIS_VERSION || "1") });
      s.onload = res; s.onerror = function () { rej(new Error("The Day Wise Trend module could not be loaded.")); }; document.head.appendChild(s);
    });
    return M.cache["mod:day-trend"].then(function () { if (!M.dtKit) throw new Error("The Day Wise Trend module could not be loaded."); return M.dtKit; });
  }

  M.page("day-trend-reports", {
    render: function (ctx) {
      var K = null, meta = null, reports = [], view = { q: "", who: "all" };
      var canManage = !!ctx.me.can.manage, me = ctx.me.emp_code;
      function head() {
        M.setHeader({ title: "Day Wise Trend Reports", crumbs: [{ label: "Day Wise Trend Reports", href: "/mis/day-trend-reports" }],
          sub: "Save a Day Wise Sale & Stock Trend as a report: the group by, the stock tags and the sale measures, any filters, and how many days back. Nothing is stored per report - every open reads the last N loaded days, so a saved report always follows the data.",
          actions: canManage ? [el("button", { type: "button", "class": "btn primary", onclick: function () { openEditor(blank()); } }, [M.icon("plus", 15), "New report"])] : [] });
      }
      head();
      M.setHeader({ tiles: [{ k: "Reports", skel: true }, { k: "Shared", skel: true }, { k: "Yours", skel: true }, { k: "Latest loaded day", skel: true }] });

      function load() { return M.get(API).then(function (r) { reports = r.reports || []; return reports; }); }
      return Promise.all([kit(), M.get("/api/v1/mis/day-trend/params"), load()]).then(function (a) {
        if (!ctx.alive()) return;
        K = a[0]; meta = a[1];
        var edit = M.qs("edit");
        var target = edit ? reports.filter(function (r) { return r.slug === edit; })[0] : null;
        if (target && canManage && target.createdBy === me) openEditor(target); else gallery();
      });

      function tiles() {
        var latest = reports.map(function (r) { return r.updatedAt || ""; }).sort().pop();
        M.setHeader({ tiles: [
          { k: "Reports", v: M.fmt(reports.length, "int"), s: "you can open" },
          { k: "Shared", v: M.fmt(reports.filter(function (r) { return r.isShared; }).length, "int"), s: "with everyone" },
          { k: "Yours", v: M.fmt(reports.filter(function (r) { return r.createdBy === me; }).length, "int"), s: "created by " + me },
          { k: "Last change", v: latest ? M.ago(latest) : "–", s: latest ? M.fmtDate(latest) : "" },
          { k: "Latest loaded day", v: meta.latest ? K.fmtDay(meta.latest) : "–", s: "every open ends here" }] });
      }

      // ── card gallery ───────────────────────────────────────────────────────────────────────
      function gallery() {
        if (location.search) history.replaceState({}, "", "/mis/day-trend-reports");
        head(); tiles(); M.clear(ctx.main);
        var q = el("input", { type: "search", placeholder: "Find a report by name, description or owner", value: view.q, style: { minWidth: "300px" }, "aria-label": "Find a report" });
        var seg = el("div", { "class": "seg", role: "group", "aria-label": "Which reports" }, [["all", "All"], ["mine", "Mine"], ["shared", "Shared"]].map(function (x) {
          return el("button", { type: "button", "aria-pressed": String(view.who === x[0]), text: x[1], onclick: function () { view.who = x[0]; seg.querySelectorAll("button").forEach(function (b, i) { b.setAttribute("aria-pressed", String(["all", "mine", "shared"][i] === view.who)); }); draw(); } });
        }));
        var box = el("div", { "class": "rgrid" });
        q.addEventListener("input", M.debounce(function () { view.q = q.value.trim().toLowerCase(); draw(); }, 200));
        ctx.main.appendChild(el("div", { "class": "card" }, [el("div", { "class": "fbar" }, [el("label", { "class": "f" }, ["Search", q]), el("label", { "class": "f" }, ["Show", seg])])]));
        ctx.main.appendChild(box);
        function draw() {
          M.clear(box);
          var list = reports.filter(function (r) {
            if (view.who === "mine" && r.createdBy !== me) return false;
            if (view.who === "shared" && !r.isShared) return false;
            return !view.q || [r.name, r.description, r.createdBy, r.slug].join(" ").toLowerCase().indexOf(view.q) >= 0;
          });
          if (!list.length) {
            box.appendChild(el("div", { "class": "card", style: { gridColumn: "1 / -1" } }, el("div", { "class": "card-b muted", style: { textAlign: "center", padding: "36px" },
              text: reports.length ? "No report matches." : "No day wise trend reports yet." + (canManage ? " New report starts one." : "") })));
            return;
          }
          list.forEach(function (r) { box.appendChild(cardOf(r)); });
        }
        draw();
      }
      function cardOf(r) {
        var d = r.definition || {}, nf = Object.keys(d.filters || {}).length, nc = (d.dim_filters || []).length + (d.col_filters || []).length, own = r.createdBy === me;
        var href = "/mis/day-trend-reports/r/" + encodeURIComponent(r.slug);
        var foot = el("div", { "class": "foot" }, [el("a", { "class": "btn sm primary", style: { textDecoration: "none" }, href: href }, [M.icon("play", 13), "Open"])]);
        if (canManage) foot.appendChild(el("button", { type: "button", "class": "btn sm", onclick: function () { duplicate(r); } }, [M.icon("copy", 13), "Duplicate"]));
        if (canManage && own) {
          foot.appendChild(el("button", { type: "button", "class": "btn sm", onclick: function () { openEditor(r); } }, [M.icon("edit", 13), "Edit"]));
          foot.appendChild(el("span", { "class": "spacer" }));
          foot.appendChild(el("button", { type: "button", "class": "btn sm danger", "aria-label": "Delete " + r.name, onclick: function () { remove(r); } }, [M.icon("trash", 13), "Delete"]));
        }
        return el("div", { "class": "rcard" }, [
          el("div", { "class": "row", style: { justifyContent: "space-between", flexWrap: "nowrap" } }, [el("h3", { style: { minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, el("a", { href: href, text: r.name, title: r.name })),
            el("span", { "class": "chip " + (r.isShared ? "ok" : "grey"), title: r.isShared ? "Shared with everyone" : "Only you can see this", text: r.isShared ? "Shared" : "Private" })]),
          r.description ? el("p", { text: r.description }) : null,
          el("div", { "class": "muted tiny", text: (d.group_by || []).join(" × ") + " · last " + (d.days || 30) + " day(s) · " + (d.stock_tags || []).length + " stock tag(s) · " + (d.measures || []).length + " sale measure(s)" +
            (nf ? " · " + nf + " saved filter(s)" : "") + (nc ? " · " + nc + " condition(s)" : "") }),
          el("div", { "class": "muted tiny", text: "by " + r.createdBy + (r.updatedAt ? " · updated " + M.ago(r.updatedAt) : "") + (r.showInSidebar === false ? " · not in sidebar" : "") }),
          foot]);
      }
      function remove(r) {
        M.confirm("Delete report", "Delete the day wise trend report “" + r.name + "”? Schedules that dump it will stop finding it.", "Delete", true).then(function (ok) {
          if (!ok) return;
          M.api("DELETE", API + "/" + encodeURIComponent(r.reportId)).then(function () { M.toast("Deleted.", "ok"); return load(); }).then(function () { if (ctx.alive()) gallery(); }, function (e) { M.toast(e.message, "bad"); });
        });
      }
      function blank() {
        return { name: "", slug: "", description: "", isShared: false, showInSidebar: true,
          definition: { group_by: (meta.defaultDims || ["ST_CD", "VAR_ART"]).slice(), stock_tags: (meta.defaultStockTags || ["0001", "0006"]).slice(),
                        measures: (meta.defaultMeasures || ["SALE_Q", "SALE_V", "GM_V"]).slice(), filters: {}, v2r_plants: [], store_only: false, stock_total: "add", days: meta.defaultDays || 30 } };
      }
      function duplicate(r) { var c = K.clone(r); delete c.reportId; c.name = r.name + " (copy)"; c.slug = ""; c.isShared = false; openEditor(c); }

      // ── the builder ────────────────────────────────────────────────────────────────────────
      function openEditor(r) {
        var e = K.clone(r), d = e.definition = e.definition || {};
        d.group_by = d.group_by || []; d.stock_tags = d.stock_tags || []; d.measures = d.measures || []; d.filters = d.filters || {}; d.v2r_plants = d.v2r_plants || [];
        d.stock_total = d.stock_total || "add"; d.days = d.days || meta.defaultDays || 30;
        var condRows = K.condsFromDef({ dim_filters: d.dim_filters, col_filters: d.col_filters }), filterCols = Object.keys(d.filters), panels = {};
        var dimGroups = (meta.dims || []).map(function (g) { return { label: g.label, items: g.cols || [] }; });
        var allDims = [].concat.apply([], dimGroups.map(function (g) { return g.items; }));
        var articleDims = meta.articleDims || [];
        function measLabel(m) { return (meta.measureLabels || {})[m] || m; }
        function range() {
          if (!meta.latest) return { from: "", to: "" };
          var n = Math.max(1, Math.min(Number(d.days) || 30, meta.maxDays || 31));
          return { from: M.addDays(meta.latest, -(n - 1)), to: meta.latest };
        }
        function offBy() { return dimGroups.map(function (g) { return [g.label, g.items.filter(function (c) { return d.group_by.indexOf(c) < 0; })]; }).filter(function (g) { return g[1].length; }); }
        function outDef() {
          var od = K.condsToDef(condRows, allDims), o = K.clone(d);
          o.col_filters = od.col_filters;
          o.dim_filters = od.dim_filters.concat(Object.keys(od.filters).map(function (c) { return { col: c, op: "in", vals: od.filters[c] }; }));
          return o;
        }

        M.clear(ctx.main);
        M.setHeader({ title: e.reportId ? "Edit report" : "New report", sub: e.reportId ? "Changing " + e.name + " - everyone who opens it, and every schedule that dumps it, gets the new definition." : "Build a saved Day Wise Trend: grain, stock tags, sale measures, filters, conditions and how many days back.",
          crumbs: [{ label: "Day Wise Trend Reports", href: "/mis/day-trend-reports" }, { label: e.reportId ? e.name : "New report" }], tiles: [],
          actions: [el("button", { type: "button", "class": "btn", onclick: function () { gallery(); } }, [M.icon("x", 15), "Cancel"])] });

        // name / url / description
        var name = el("input", { type: "text", maxlength: "120", value: e.name || "", placeholder: "e.g. Store × Maj-Cat 30-day trend" });
        var slugIn = el("input", { type: "text", maxlength: "60", value: e.slug || "", placeholder: "store-majcat-trend", style: { fontFamily: "monospace" } });
        var desc = el("input", { type: "text", maxlength: "500", value: e.description || "", placeholder: "What this report answers" });
        name.addEventListener("input", function () { e.name = name.value; }); slugIn.addEventListener("input", function () { e.slug = slugIn.value; }); desc.addEventListener("input", function () { e.description = desc.value; });
        var card = el("div", { "class": "card" }, [el("div", { "class": "card-h" }, [el("h2", { text: "Report" })]),
          el("div", { "class": "card-b grid12" }, [el("label", { "class": "f c4" }, ["Report name", name]), el("label", { "class": "f c4" }, ["URL (optional - from the name if blank)", slugIn]), el("label", { "class": "f c4" }, ["Description", desc])])]);
        ctx.main.appendChild(card);

        // picks
        var cp = null;
        function pane(t, note, cl) { return el("div", { "class": "c4 stack", style: { gap: "6px" } }, [el("div", { "class": "section-title", text: t }), el("div", { "class": "muted tiny", text: note }), cl.el]); }
        var clDims = M.checklist(dimGroups, d.group_by, { height: "280px", prettify: false, placeholder: "Find a dimension", onChange: function (v) { d.group_by = K.pickOrder(d.group_by, v); cp.setContext({ grouped: d.group_by.slice(), offBy: offBy() }); paintFilterNote(); } });
        var clTags = M.checklist([{ items: meta.stockTags || [] }], d.stock_tags, { height: "280px", prettify: false, placeholder: "Find a stock tag", onChange: function (v) { d.stock_tags = K.pickOrder(d.stock_tags, v); totalWrap.classList.toggle("hidden", d.stock_tags.length < 2); cp.setContext({ measures: K.dayTrendMeasConds(d.stock_tags, d.measures) }); } });
        var clMeas = M.checklist([{ items: (meta.measures || []).map(function (m) { return { value: m, label: measLabel(m) }; }) }], d.measures, { height: "280px", prettify: false, onChange: function (v) { d.measures = K.pickOrder(d.measures, v); cp.setContext({ measures: K.dayTrendMeasConds(d.stock_tags, d.measures) }); } });

        var days = el("input", { type: "number", min: "1", max: String(meta.maxDays || 31), value: String(d.days), style: { width: "80px" }, "aria-label": "Days back" });
        var rangeTxt = el("span", { "class": "muted small" });
        function paintRange() { var r = range(); rangeTxt.textContent = "ending on the latest loaded day (" + r.from + " → " + r.to + ")"; }
        days.addEventListener("change", function () { d.days = Math.max(1, Math.min(Number(days.value) || 1, meta.maxDays || 31)); days.value = String(d.days); paintRange(); Object.keys(panels).forEach(function (k) { panels[k].refresh(); }); cp.clearCache(); });
        paintRange();
        var v2r = K.v2rPicker(meta.v2rPlants || [], d.v2r_plants, function (v) { d.v2r_plants = v; });
        var stTotal = el("select", { "aria-label": "Stock columns" }, Object.keys(K.STOCK_TOTAL).map(function (k) { return el("option", { value: k, text: K.STOCK_TOTAL[k] }); }));
        stTotal.value = d.stock_total; stTotal.addEventListener("change", function () { d.stock_total = stTotal.value; });
        var totalWrap = el("label", { "class": "row small", style: { gap: "6px" } }, [el("span", { "class": "muted", text: "Stock columns" }), stTotal]);
        totalWrap.classList.toggle("hidden", d.stock_tags.length < 2);
        var so = el("input", { type: "checkbox", checked: !!d.store_only }); so.addEventListener("change", function () { d.store_only = so.checked; });
        ctx.main.appendChild(el("div", { "class": "card" }, [el("div", { "class": "card-h" }, [el("h2", { text: "Grain, stock and sale" })]),
          el("div", { "class": "card-b stack" }, [
            el("div", { "class": "grid12" }, [pane("Group by", "The grain of a row, in the order you pick it.", clDims), pane("Stock tags", "Two columns per tag, Q and V, inside every day's block.", clTags), pane("Sale measures", "The day's own sale, never a window.", clMeas)]),
            el("div", { "class": "row" }, [el("label", { "class": "row small", style: { gap: "6px" } }, [el("span", { "class": "muted", text: "Days back" }), days]), rangeTxt]),
            el("div", { "class": "row" }, [el("span", { "class": "section-title", style: { flex: "none" }, text: "V2R plants" }), v2r.el, totalWrap,
              el("label", { "class": "row small", style: { gap: "6px" } }, [so, "ET stock: stores only", el("span", { "class": "muted", text: "(columns only)" })])])])]));

        // FILTERS - a value list per dimension, saved with the report
        var fRow = el("div", { "class": "row" }), fGrid = el("div", { "class": "grid12" }), fNote = el("span", { "class": "small" });
        function paintFilterNote() {
          var nf = Object.keys(d.filters).filter(function (k) { return (d.filters[k] || []).length; }).length, deep = d.group_by.some(function (c) { return articleDims.indexOf(c) >= 0; });
          fNote.textContent = nf ? "" : deep ? "none - this grain is one row per article, so an unfiltered open is refused; the reader then adds a filter on the page (or a dump splits by store)."
                                             : "none - the report returns every row. A filter narrows it for everyone who opens it.";
          fNote.style.color = !nf && deep ? "var(--warn)" : "var(--muted)";
        }
        var addBtn = K.addFilterButton(function () { return dimGroups; }, function () { return filterCols.slice(); }, function (v) {
          filterCols = v; Object.keys(d.filters).forEach(function (k) { if (v.indexOf(k) < 0) delete d.filters[k]; }); paintFilters();
        });
        function paintFilters() {
          M.clear(fRow); fRow.appendChild(addBtn); fRow.appendChild(fNote); paintFilterNote();
          Object.keys(panels).forEach(function (k) { if (filterCols.indexOf(k) < 0) { panels[k].el.remove(); delete panels[k]; } });
          filterCols.forEach(function (c) {
            if (panels[c]) return;
            var p = K.filterPanel({ dim: c, range: range, filters: function () { return d.filters; }, value: d.filters[c] || [],
              onChange: function (v) { if (v.length) d.filters[c] = v; else delete d.filters[c]; paintFilterNote(); Object.keys(panels).forEach(function (k) { if (k !== c) panels[k].refresh(); }); },
              onRemove: function () { filterCols = filterCols.filter(function (x) { return x !== c; }); delete d.filters[c]; paintFilters(); } });
            panels[c] = p; p.el.classList.add("c3"); fGrid.appendChild(p.el);
          });
          fGrid.classList.toggle("hidden", !filterCols.length);
        }
        ctx.main.appendChild(el("div", { "class": "card" }, [el("div", { "class": "card-h" }, [el("h2", { text: "Filters" }), el("span", { "class": "note", text: "a value list per dimension - ANDed across dimensions, ORed within one; saved with the report" })]),
          el("div", { "class": "card-b stack" }, [fRow, fGrid])]));
        paintFilters();

        // CONDITIONS - kept with the report
        cp = K.condPanel({ grouped: d.group_by.slice(), offBy: offBy(), measures: K.dayTrendMeasConds(d.stock_tags, d.measures), initial: condRows,
          loadValues: function (col) { var r = range(); return M.post("/api/v1/mis/day-trend/values", { dim: col, from_date: r.from, to_date: r.to, filters: d.filters }).then(function (x) { return x.values || []; }); },
          valuesKey: function () { var r = range(); return r.from + "|" + r.to; },
          note: "kept with the report - applied on every open, export and scheduled dump",
          emptyText: "No conditions - the report returns every row. A condition may name any dimension (grouped or not), a sale measure's total over the window (SALE-Q greater than 0), or a stock tag on the last day (0001-STK-Q greater than 0).",
          onChange: function (rows) { condRows = rows; } });
        ctx.main.appendChild(cp.el);

        // preview / share / save
        var shared = el("input", { type: "checkbox", checked: !!e.isShared }), side = el("input", { type: "checkbox", checked: e.showInSidebar !== false });
        shared.addEventListener("change", function () { e.isShared = shared.checked; }); side.addEventListener("change", function () { e.showInSidebar = side.checked; });
        var prevBtn = el("button", { type: "button", "class": "btn", title: "Run on the report's window, 50 rows a page" }, [M.icon("play", 15), "Preview"]);
        var saveBtn = el("button", { type: "button", "class": "btn primary" }, [M.icon("save", 15), "Save"]);
        var prevBox = el("div");
        ctx.main.appendChild(el("div", { "class": "card" }, el("div", { "class": "card-b row" }, [prevBtn,
          el("label", { "class": "row small", style: { gap: "6px" } }, [shared, "Share with everyone", el("span", { "class": "muted", text: "(otherwise only you see it)" })]),
          el("label", { "class": "row small", style: { gap: "6px" } }, [side, "Show in sidebar", el("span", { "class": "muted", text: "(off keeps it on this page only)" })]),
          el("span", { "class": "spacer" }), saveBtn])));
        ctx.main.appendChild(prevBox);

        var prevRes = null, prevGrid = null;
        prevBtn.addEventListener("click", function () {
          if (!d.group_by.length || !d.stock_tags.length || !d.measures.length) { M.toast("Pick a Group by, a stock tag and a sale measure first.", "bad"); return; }
          var r = range(), od = outDef();
          var req = { dim_conds: od.dim_filters.length ? od.dim_filters : null, meas_conds: od.col_filters.length ? od.col_filters : null, from_date: r.from, to_date: r.to,
                      group_by: d.group_by, stock_tags: d.stock_tags, measures: d.measures, filters: Object.keys(d.filters).length ? d.filters : null,
                      v2r_plants: d.v2r_plants.length ? d.v2r_plants : null, store_only: !!d.store_only, stock_total: d.stock_total || "add" };
          M.clear(prevBox);
          var pc = el("div", { "class": "card" }); prevBox.appendChild(pc);
          var cover = {}; (meta.stockDays || []).forEach(function (x) { cover[x.day] = x.stores; });
          var go = K.dayGridOpts(function () { return prevRes; }, cover);
          prevGrid = M.Grid(pc, { title: "Preview · " + r.from + " → " + r.to, limit: 50, pageSizes: [50, 100], filterable: false,
            fetch: function (gs, signal) { var b = K.clone(req); b.page = gs.page; b.limit = gs.limit; b.sort_col = gs.sort_col || null; b.sort_dir = gs.sort_dir; return M.post("/api/v1/mis/day-trend/result", b, { signal: signal }); },
            onData: function (x) { prevRes = x; }, isKey: go.isKey, format: go.format, label: go.label, groups: go.groups, cell: go.cell });
          M.action(prevBtn, "Running…", function () { return prevGrid.reload(true); });
          pc.scrollIntoView({ behavior: "smooth", block: "start" });
        });
        saveBtn.addEventListener("click", function () {
          if (!String(e.name || "").trim()) { name.focus(); M.toast("Enter a report name.", "bad"); return; }
          M.action(saveBtn, "Saving…", function () {
            return M.post(API, { report_id: e.reportId || null, name: e.name.trim(), slug: String(e.slug || "").trim() || null, description: e.description || "",
                                 definition: outDef(), is_shared: !!e.isShared, show_in_sidebar: e.showInSidebar !== false });
          }, "Saved.").then(function () { return load(); }).then(function () { if (ctx.alive()) gallery(); }, function () { /* toast shown */ });
        });
      }
    }
  });
})();

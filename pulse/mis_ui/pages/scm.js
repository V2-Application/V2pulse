// SCM Report (DFS /sf-mis-scm, SfScmReport.jsx + components/ScmViewEditor.jsx): the SCM team's workbooks reproduced in
// SQL over live SAP data and published as views in V2RETAIL.SILVER (db-scripts 387 / 391), plus views added from this
// page. ONE page over all of them: the engine's catalogue says which views exist, and each view's /params shapes the
// toolbar - the anchor (one RUN, a DATE range ending at the view's own latest date, or none = current position), the
// tick-list filters, the search columns and the additive measures for the totals strip.
// API: /api/v1/mis/scm/{reports, params?report=&refresh=1, result, values, export?fmt=csv|xlsx,
//      custom/preview, custom (save), custom/{slug} (GET / DELETE?drop_view=0|1)}. Custom-view writes need mis.admin.
// Not ported: DFS "Write to folder" (planning-service Node runner) - and the SCM dump kind is not in V2Pulse's Dump
// Scheduler yet, so no "Schedule a dump…" either.
(function () {
  "use strict";
  var M = window.MIS, el = M.el, API = "/api/v1/mis/scm";
  var PAGE_SIZE = 200;

  // A run date is YYYYMMDD on the wire - the extract's own identifier. Only the label is prettied.
  function runLabel(r) { r = String(r || ""); return /^\d{8}$/.test(r) ? r.slice(6, 8) + "-" + r.slice(4, 6) + "-" + r.slice(0, 4) : r; }
  function anchorText(r) { return r.anchor === "run" ? "one run (" + r.anchorCol + ")" : r.anchor === "date" ? r.anchorCol + " range" : "current position"; }
  function csrf() { var m = document.cookie.match(/(?:^|;\s*)pulse_csrf=([^;]+)/); return m ? decodeURIComponent(m[1]) : ""; }
  // A POST download saved under a name WE choose (MIS.download prefers the server's name, and every slice of a split
  // export comes back with the same server name).
  function saveAs(url, body, name) {
    return fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() }, body: JSON.stringify(body) }).then(function (r) {
      if (!r.ok) return r.text().then(function (t) { var d = null; try { d = JSON.parse(t); } catch (e) { /* text */ }
        throw new Error(d && typeof d.detail === "string" ? d.detail : d && d.error ? d.error : "The export failed (error " + r.status + ")."); });
      return r.blob().then(function (b) {
        var a = el("a", { href: URL.createObjectURL(b), download: name }); document.body.appendChild(a); a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 3000); return name;
      });
    });
  }
  function errMsg(e) {
    return e && e.status === 403 ? "You need the MIS admin right (mis.admin) to add or edit SCM views. Ask your V2Pulse administrator." : (e && e.message) || String(e);
  }

  M.page("scm", {
    render: function (ctx) {
      var catalogue = [], slug = "", meta = null, grid = null, seq = 0;
      var st = { run: "", from: "", to: "", all: false, cols: [], search: "" };
      var canAdmin = !!ctx.me.can.admin;

      var refreshBtn = el("button", { type: "button", "class": "btn", title: "Re-read this view's runs, dates and tick-lists from Snowflake" }, [M.icon("refresh", 15), "Refresh"]);
      var addBtn = canAdmin ? el("button", { type: "button", "class": "btn primary", title: "Add a Snowflake view to this page - write its SQL (create / alter the view in SILVER) or pick one that already exists" }, [M.icon("plus", 15), "Add view"]) : null;
      var manageBtn = canAdmin ? el("button", { type: "button", "class": "btn" }, [M.icon("gear", 15), "Manage views"]) : null;
      M.setHeader({ title: "SCM Report",
        sub: "The SCM team's workbooks, reproduced in SQL over live SAP data and published as views in V2RETAIL.SILVER. Pick a report; the columns, the filters and the date control all come from the view behind it.",
        actions: [refreshBtn, manageBtn, addBtn],
        tiles: ["Rows", "Scope", "Totals"].map(function (k) { return { k: k, skel: true }; }) });

      var pickerCard = el("div", { "class": "card" }), work = el("div", { "class": "stack", style: { gap: "16px" } });
      ctx.main.appendChild(pickerCard); ctx.main.appendChild(work);

      // ── the catalogue ─────────────────────────────────────────────────────────────────────────────
      function loadCatalogue(pick) {
        return M.get(API + "/reports").then(function (d) {
          if (!ctx.alive()) return;
          catalogue = d.reports || [];
          var want = pick && catalogue.some(function (x) { return x.slug === pick; }) ? pick
            : slug && catalogue.some(function (x) { return x.slug === slug; }) ? slug : (catalogue[0] || {}).slug || "";
          drawPicker(want);
          return want;
        });
      }
      function drawPicker(on) {
        M.clear(pickerCard);
        var built = catalogue.filter(function (r) { return !r.custom; }), added = catalogue.filter(function (r) { return r.custom; });
        pickerCard.appendChild(el("div", { "class": "card-h" }, [el("h2", { text: "Reports" }),
          el("span", { "class": "note", text: catalogue.length + " views · " + built.length + " built in" + (added.length ? ", " + added.length + " added from this page" : "") })]));
        var b = el("div", { "class": "card-b stack" });
        function group(title, list, withAdd) {
          if (!list.length && !withAdd) return;
          b.appendChild(el("div", { "class": "section-title", text: title }));
          var g = el("div", { "class": "rgrid", style: { gridTemplateColumns: "repeat(auto-fill, minmax(250px, 1fr))", gap: "10px" } });
          list.forEach(function (r) {
            var sel = r.slug === on;
            var edit = r.custom && canAdmin ? el("button", { type: "button", "class": "btn ghost sm", "aria-label": "Edit the view " + r.label, title: "Edit this view - its SQL, anchor, filters and totals" }, [M.icon("edit", 13)]) : null;
            var card = el("div", { "class": "rcard", role: "button", tabindex: "0", "aria-pressed": String(sel), title: r.note || r.label,
              style: { padding: "10px 12px", gap: "3px", cursor: "pointer", borderColor: sel ? "var(--brand-2)" : null, background: sel ? "var(--sel)" : null, boxShadow: sel ? "0 0 0 1px var(--brand-2)" : null } }, [
              el("div", { "class": "row", style: { flexWrap: "nowrap", gap: "6px" } }, [el("b", { style: { fontSize: "13.3px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }, text: r.label }),
                r.custom ? el("span", { "class": "chip", style: { fontSize: "10.5px", padding: "1px 7px" }, title: "Added from this page" + (r.updatedBy ? " · last edited by " + r.updatedBy : ""), text: "custom" }) : null, edit]),
              el("code", { "class": "muted tiny", style: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }, text: r.view }),
              el("div", { "class": "muted tiny" }, [r.tcode ? el("code", { text: r.tcode }) : el("i", { text: "no tcode" }), " · " + anchorText(r) + " · " + (r.columns || []).length + " cols"])]);
            card.addEventListener("click", function () { if (r.slug !== slug) openView(r.slug); });
            card.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (r.slug !== slug) openView(r.slug); } });
            if (edit) edit.addEventListener("click", function (e) { e.stopPropagation(); editor(r.slug); });
            g.appendChild(card);
          });
          if (withAdd) {
            var add = el("button", { type: "button", "class": "rcard", style: { padding: "10px 12px", borderStyle: "dashed", alignItems: "center", justifyContent: "center", flexDirection: "row", cursor: "pointer", color: "var(--muted)", font: "inherit", fontWeight: 600 } }, [M.icon("plus", 15), "Add view"]);
            add.addEventListener("click", function () { editor(null); });
            g.appendChild(add);
          }
          b.appendChild(g);
        }
        group("Built in (db-scripts 387, 391)", built, false);
        group("Added from this page", added, canAdmin && catalogue.length > 0);
        if (!catalogue.length) b.appendChild(el("div", { "class": "muted small", text: "No SCM views are published." }));
        pickerCard.appendChild(b);
      }

      // ── one view: note, toolbar, grid ──────────────────────────────────────────────────────────────
      // Switching report resets everything that belonged to the old one; a Refresh keeps the filters, columns and search.
      function openView(s, opts) {
        opts = opts || {};
        var my = ++seq, keep = opts.keep ? { cols: st.cols, search: st.search, filters: grid ? grid.state().filters : {} } : null;
        slug = s; drawPicker(s);
        history.replaceState({}, "", "/mis/scm" + (s ? "?view=" + encodeURIComponent(s) : ""));
        meta = null; grid = null; M.clear(work);
        st = { run: "", from: "", to: "", all: false, cols: keep ? keep.cols : [], search: keep ? keep.search : "" };
        M.setHeader({ tiles: ["Rows", "Scope", "Totals"].map(function (k) { return { k: k, skel: !!s }; }) });
        if (!s) { work.appendChild(el("div", { "class": "card" }, el("div", { "class": "dg-empty", text: "Pick a report above." }))); return Promise.resolve(); }
        work.appendChild(el("div", { "class": "card" }, el("div", { "class": "card-b muted" }, [el("span", { "class": "spin" }), " Reading the view…"])));
        return M.get(API + "/params?report=" + encodeURIComponent(s) + (opts.refresh ? "&refresh=1" : "")).then(function (p) {
          if (!ctx.alive() || my !== seq) return;
          meta = p;
          // The engine RESOLVED the default window and the newest run; adopt them rather than computing our own.
          var a = p.anchor || {};
          if (a.kind === "run") st.run = a.latest || "";
          else if (a.kind === "date") { st.from = a.from || ""; st.to = a.to || ""; }
          drawWork(keep ? keep.filters : {});
        }, function (e) {
          if (!ctx.alive() || my !== seq) return;
          M.clear(work).appendChild(el("div", { "class": "notice bad", text: e.message }));
          M.setHeader({ tiles: [] });
        });
      }

      function kind() { return (meta && meta.anchor && meta.anchor.kind) || "none"; }
      // The scope, in ONE place: the grid, the totals, the value lists and every export send this object.
      function scope(gs) {
        var k = kind(), f = {};
        Object.keys((gs && gs.filters) || {}).forEach(function (c) { if (gs.filters[c] && gs.filters[c].length) f[c] = gs.filters[c]; });
        return { report: slug, run_date: k === "run" ? (st.run || null) : null,
                 from_date: k === "date" && !st.all ? (st.from || null) : null, to_date: k === "date" && !st.all ? (st.to || null) : null,
                 all_dates: k === "date" ? !!st.all : false, col_filters: f, search: st.search || null, columns: st.cols.length ? st.cols : null };
      }
      function stem(res) {
        var a = (res && res.anchor) || {}, t = "";
        if (a.kind === "run") t = a.runDate || "";
        else if (a.kind === "date" && a.from) { var f = String(a.from).replace(/-/g, ""), to = String(a.to).replace(/-/g, ""); t = f === to ? f : f + "_" + to; }
        var v = String((res && res.table) || (meta && meta.table) || "").split(".").pop() || "SCM_REPORT";
        return v + (t ? "_" + t : "");
      }

      function tiles(res, gs) {
        var a = (res && res.anchor) || {}, filtered = Object.keys(scope(gs).col_filters).length || st.search;
        var scopeTile = a.kind === "run" ? { k: "Run · " + a.col, v: runLabel(a.runDate), s: ((meta.anchor || {}).runs || []).length + " runs loaded" }
          : a.kind === "date" ? (a.all ? { k: a.col, v: "All dates", s: (meta.anchor.min || "?") + " → " + (meta.anchor.max || "?") }
                                       : { k: a.col, v: M.fmtDate(a.from, false) + " →", s: M.fmtDate(a.to, false) + " · loaded to " + (meta.anchor.max || "?") })
          : { k: "Scope", v: "Current position", s: "the view has no date" };
        var t = [{ k: "Rows", v: M.fmt(res.total, "int"), s: filtered ? "after filters · " + M.fmt((meta.stats || {}).rows, "int") + " in the view" : M.fmt((meta.stats || {}).rows, "int") + " in the view" }, scopeTile];
        // Totals over the WHOLE filtered selection, not the page - additive measures only (no %, no ageing).
        Object.keys(res.totals || {}).forEach(function (m) {
          var v = res.totals[m], n = Number(v);
          t.push({ k: M.label(m), v: isFinite(n) && Math.abs(n) >= 1e5 ? M.compact(n) : M.fmt(v), s: isFinite(n) && Math.abs(n) >= 1e5 ? M.fmt(v, "int") : "total", title: m });
        });
        M.setHeader({ tiles: t });
      }

      function drawWork(initFilters) {
        M.clear(work);
        var p = meta, a = p.anchor || {}, measures = {};
        (p.measures || []).forEach(function (m) { measures[m] = true; });

        // The caveat that changes how a number should be read, straight from the engine's own note.
        if (p.note) work.appendChild(el("div", { "class": "notice warn" }, [M.icon("info", 16), el("span", { "class": "small", text: p.note })]));

        var card = el("div", { "class": "card" }), bar = el("div", { "class": "fbar" }), bar2 = el("div", { "class": "fbar", style: { paddingTop: "10px" } });
        card.appendChild(bar); work.appendChild(card);
        var reload = function () { if (grid) grid.reload(true); };

        // ── the anchor ──
        if (a.kind === "run") {
          // ONE run, chosen from the runs that exist - not a calendar.
          var runSel = el("select", { "aria-label": "Run", style: { minWidth: "190px", fontFamily: "ui-monospace, Consolas, monospace" } },
            (a.runs || []).length ? a.runs.map(function (r) { return el("option", { value: r, selected: r === st.run, text: runLabel(r) + " · " + r }); }) : [el("option", { value: "", text: "no runs loaded" })]);
          runSel.addEventListener("change", function () { st.run = runSel.value; reload(); });
          bar.appendChild(el("label", { "class": "f" }, [a.col + " · " + (a.runs || []).length + " loaded", runSel]));
        } else if (a.kind === "date") {
          var f = el("input", { type: "date", value: st.from, min: a.min || null, max: a.max || null, "aria-label": a.col + " from" });
          var t = el("input", { type: "date", value: st.to, min: a.min || null, max: a.max || null, "aria-label": a.col + " to" });
          var all = el("input", { type: "checkbox", checked: st.all, "aria-label": "All dates" });
          function syncDates() { f.disabled = t.disabled = all.checked; }
          f.addEventListener("change", function () { st.from = f.value; reload(); });
          t.addEventListener("change", function () { st.to = t.value; reload(); });
          all.addEventListener("change", function () { st.all = all.checked; syncDates(); reload(); });
          syncDates();
          bar.appendChild(el("label", { "class": "f" }, [a.col + " from", f]));
          bar.appendChild(el("label", { "class": "f" }, ["to", t]));
          bar.appendChild(el("label", { "class": "row small", style: { gap: "5px", paddingBottom: "8px", cursor: "pointer" }, title: "The whole view - " + (a.min || "?") + " to " + (a.max || "?") }, [all, "All dates"]));
          // The loaded span, stated: the only thing that says a feed stopped rather than that the filters are wrong.
          bar.appendChild(el("span", { "class": "muted tiny", style: { paddingBottom: "9px", fontFamily: "ui-monospace, Consolas, monospace" }, text: "loaded " + (a.min || "–") + " → " + (a.max || "–") }));
        }

        // ── search + columns ──
        var search = el("input", { type: "search", value: st.search, placeholder: (p.searchCols || []).slice(0, 3).join(", ").toLowerCase() || "Search…",
          title: "Matches any of: " + (p.searchCols || []).join(", "), "aria-label": "Search", style: { minWidth: "240px" } });
        search.addEventListener("input", M.debounce(function () { st.search = search.value.trim(); reload(); }, 400));
        if ((p.searchCols || []).length) bar.appendChild(el("label", { "class": "f" }, ["Search", search]));
        var colBtn = el("button", { type: "button", "class": "btn" }, [M.icon("cols", 15), el("span")]);
        function paintCols() { colBtn.lastChild.textContent = st.cols.length ? st.cols.length + " columns" : "Default columns"; }
        paintCols();
        colBtn.addEventListener("click", function () {
          var cl = M.checklist([{ items: p.columns || [] }], st.cols.length ? st.cols : (p.defaultCols || []), { height: "380px", prettify: false });
          var ok = el("button", { type: "button", "class": "btn primary", text: "Show these columns" }), def = el("button", { type: "button", "class": "btn", text: "The report's defaults" });
          var m = M.modal("Columns of " + p.label, [el("p", { "class": "muted small", style: { margin: 0 }, text: "The columns the engine selects - totals cover the measures on screen. Pick any of the " + (p.columns || []).length + "." }), cl.el], [def, ok]);
          ok.addEventListener("click", function () { var v = cl.get(); st.cols = v.length ? (p.columns || []).filter(function (c) { return v.indexOf(c) >= 0; }) : []; m.close(); paintCols(); reload(); });
          def.addEventListener("click", function () { st.cols = []; m.close(); paintCols(); reload(); });
        });
        bar.appendChild(el("span", { "class": "spacer" }));
        bar.appendChild(el("label", { "class": "f" }, ["Columns", colBtn]));

        // ── tick-list filters (the view's filter columns), kept in step with the grid's header filters ──
        var pickers = {};
        (p.filterCols || []).forEach(function (c) {
          var opts = (p.options || {})[c] || [];
          pickers[c] = M.multiSelect(c + " (" + opts.length + ")", function () { return Promise.resolve(opts); }, (initFilters || {})[c] || [], function (vals) {
            if (!grid) return; var fs = grid.state().filters; if (vals.length) fs[c] = vals; else delete fs[c]; grid.setState({ filters: fs }); grid.reload(true);
          });
          bar2.appendChild(pickers[c].el);
        });
        if ((p.filterCols || []).length) card.appendChild(bar2);
        else card.appendChild(el("div", { "class": "fbar muted tiny", style: { paddingTop: "8px", paddingBottom: "8px" }, text: "This report has no tick-lists - use the search box." }));

        // ── exports ──
        var lastRes = null, exports = [];
        function exportOne(fmt, gs) {
          M.toast("Building " + fmt.toUpperCase() + "…");
          M.download("POST", API + "/export?fmt=" + fmt, scope(gs), stem(lastRes) + "." + fmt).then(function (n) { M.toast("Downloaded " + n, "ok"); }, function (e) { M.toast(e.message, "bad"); });
        }
        // One file per value of the split columns, downloaded in sequence - only for small splits (40 files at most).
        function exportEach(gs) {
          var cl = M.checklist([{ items: p.filterCols || [] }], [], { height: "220px", prettify: false });
          var fmtSel = el("select", { "aria-label": "Format" }, [el("option", { value: "csv", text: "CSV" }), el("option", { value: "xlsx", text: "Excel (xlsx)" })]);
          var go = el("button", { type: "button", "class": "btn primary" }, [M.icon("down", 15), "Download the files"]);
          var m = M.modal("One file per value", [el("p", { "class": "muted small", style: { margin: 0 }, text: "Split the current selection (filters and search included) by one or more tick-list columns. Several columns mean the combination: one file per pair that exists. Your browser may ask once to allow several downloads; a split of more than 40 files is refused." }),
            el("label", { "class": "f" }, ["Format", fmtSel]), el("div", { "class": "section-title", text: "Split by" }), cl.el], [go]);
          go.addEventListener("click", function () {
            var levels = cl.get(), fmt = fmtSel.value; if (!levels.length) { M.toast("Pick at least one column to split by.", "bad"); return; }
            var base = scope(gs), name = stem(lastRes);
            M.action(go, "Working…", function () {
              return M.post(API + "/values", Object.assign({}, base, { split_cols: levels })).then(function (v) {
                var combos = v.combos || [];
                if (!combos.length) throw new Error("No values to split on.");
                if (combos.length > 40) throw new Error(combos.length + " files is too many to download one by one - narrow the selection or split by fewer columns.");
                var i = 0;
                function next() {
                  if (i >= combos.length) return combos.length;
                  var combo = combos[i++], f = Object.assign({}, base.col_filters);
                  levels.forEach(function (l, j) { f[l] = [combo[j]]; });
                  var sfx = levels.map(function (l, j) { return l + "_" + String(combo[j]).replace(/[\\/:*?"<>|]+/g, "_"); }).join("__");
                  go.lastChild.textContent = " " + i + " / " + combos.length;
                  return saveAs(API + "/export?fmt=" + fmt, Object.assign({}, base, { col_filters: f }), name + "__" + sfx + "." + fmt).then(next);
                }
                return next();
              });
            }, function (n) { return n + " file(s) downloaded"; }).then(function () { m.close(); }, function () { /* toast shown */ });
          });
        }
        if (ctx.me.can.export) {
          exports = [{ label: "CSV - the whole filtered selection (no row limit)", fn: function (gs) { exportOne("csv", gs); } },
                     { label: "Excel (xlsx) - capped at 1,048,575 rows", fn: function (gs) { exportOne("xlsx", gs); } }];
          if ((p.filterCols || []).length) exports.push({ label: "One file per value…", fn: exportEach });
        }

        // ── the grid ──
        grid = M.Grid(card, {
          id: "scm." + slug, title: p.label, limit: PAGE_SIZE, conditions: false, exports: exports,
          label: function (c) { return c; },
          isKey: function (c) { return !measures[c]; },
          format: function (c) { return measures[c] ? undefined : "text"; },
          fetch: function (gs, signal) {
            if (kind() === "run" && !st.run) M.setHeader({ tiles: [{ k: "Rows", v: "–", s: "no runs loaded" }, { k: "Run · " + (a.col || ""), v: "–", s: "0 runs loaded" }] });
            if (kind() === "run" && !st.run) return Promise.reject(new Error((p.label || "This report") + " has no runs loaded yet - nothing to show."));
            return M.post(API + "/result", Object.assign(scope(gs), { page: gs.page, limit: gs.limit, sort_col: gs.sort_col || null, sort_dir: gs.sort_dir || "asc" }), { signal: signal }).then(function (d) {
              if (d && !d.rows.length && !Object.keys(scope(gs).col_filters).length && !st.search && kind() === "date" && !st.all)
                M.toast("Nothing in this window. The view holds " + (a.min || "?") + " to " + (a.max || "?") + " - widen the dates, or tick All dates.");
              return d;
            });
          },
          // Header value lists: the filter columns only (a slice is a col_filter the engine can apply), counted within
          // the current scope minus this column's own filter.
          values: function (c, gs) {
            if ((p.filterCols || []).indexOf(c) < 0) return Promise.reject(new Error("only the tick-list columns (" + (p.filterCols || []).join(", ") + ") can be filtered - use Search for the rest"));
            var sc = scope(gs); delete sc.col_filters[c];
            return M.post(API + "/values", Object.assign(sc, { split_cols: [c] })).then(function (v) {
              return (v.combos || []).map(function (x, i) { return { value: x[0], label: x[0], count: (v.counts || [])[i] }; });
            });
          },
          onData: function (d, gs) {
            if (!ctx.alive()) return;
            lastRes = d; tiles(d, gs);
            Object.keys(pickers).forEach(function (c) { pickers[c].set(gs.filters[c] || []); });
            foot.textContent = p.label + " · " + (d.table || p.table) + (p.tcode ? " · SAP " + p.tcode : "") + " · " + (p.columns || []).length + " columns, " + (p.filterCols || []).length + " filterable"
              + ((p.stats || {}).rows != null ? " · " + M.fmt(p.stats.rows, "int") + " rows in the view" : "") + (p.cached ? " · lists cached up to 15 min (Refresh re-reads)" : "");
          },
          getExtra: function () { return { cols: st.cols, search: st.search }; },
          setExtra: function (x) { st.cols = x.cols || []; st.search = x.search || ""; search.value = st.search; paintCols(); }
        });
        var cur = catalogue.filter(function (r) { return r.slug === slug; })[0] || {};
        var foot = el("div", { "class": "muted tiny", style: { padding: "0 4px" } });
        work.appendChild(foot);
        if (cur.custom) work.appendChild(el("div", { "class": "muted tiny", style: { padding: "0 4px", marginTop: "-10px" }, text: "Added from this page" + (cur.updatedBy ? ", last edited by " + cur.updatedBy : "") + (cur.updatedAt ? " · " + M.fmtDate(cur.updatedAt) : "") }));
        grid.setState({ filters: initFilters || {} });
        return grid.applyDefaultView().then(function () { if (ctx.alive() && grid) grid.reload(true); });
      }

      // ── the "Add view" editor (ScmViewEditor) ──────────────────────────────────────────────────────
      function removeView(r, after) {
        var drop = el("input", { type: "checkbox", checked: true });
        var ok = el("button", { type: "button", "class": "btn danger" }, [M.icon("trash", 15), "Yes, remove"]), cancel = el("button", { type: "button", "class": "btn", text: "Cancel" });
        var m = M.modal("Remove " + r.label + "?", [el("p", { style: { margin: 0 }, text: "The report disappears from the SCM Report (and from any dump that names it)." }),
          r.origin === "created" ? el("label", { "class": "row small", style: { gap: "6px" } }, [drop, "Also DROP the view ", el("code", { text: r.view })])
                                 : el("div", { "class": "muted small", text: "The view existed before it was added here - it is left in SILVER." })], [cancel, ok]);
        cancel.addEventListener("click", function () { m.close(); });
        ok.addEventListener("click", function () {
          M.action(ok, "Removing…", function () {
            return M.api("DELETE", API + "/custom/" + encodeURIComponent(r.slug) + "?drop_view=" + (r.origin === "created" && drop.checked ? 1 : 0));
          }, function (d) { return d.viewDropped ? "Removed, and " + d.view + " dropped." : "Removed from the SCM Report."; }).then(function () {
            m.close(); if (after) after(); if (!ctx.alive()) return;
            loadCatalogue(r.slug === slug ? null : slug).then(function (s) { if (r.slug === slug || s !== slug) openView(s || ""); });
          }, function () { /* toast shown */ });
        });
      }

      function manage() {
        var list = el("div", { "class": "stack", style: { gap: "6px" } });
        var added = catalogue.filter(function (r) { return r.custom; });
        if (!added.length) list.appendChild(el("div", { "class": "muted small", text: "No views have been added from this page yet." }));
        var m;
        added.forEach(function (r) {
          var ed = el("button", { type: "button", "class": "btn sm" }, [M.icon("edit", 14), "Edit"]), del = el("button", { type: "button", "class": "btn sm danger" }, [M.icon("trash", 14), "Remove"]);
          ed.addEventListener("click", function () { m.close(); editor(r.slug); });
          del.addEventListener("click", function () { m.close(); removeView(r); });
          list.appendChild(el("div", { "class": "row", style: { padding: "8px 10px", border: "1px solid var(--line)", borderRadius: "10px", flexWrap: "nowrap" } }, [
            el("div", { style: { flex: 1, minWidth: 0 } }, [el("b", { text: r.label }), el("div", { "class": "muted tiny" }, [el("code", { text: r.table || r.view }),
              " · " + anchorText(r) + " · " + (r.columns || []).length + " cols" + (r.origin ? " · " + (r.origin === "created" ? "created here" : "existing view") : "") + (r.updatedBy ? " · last edited by " + r.updatedBy : "") + (r.updatedAt ? " " + M.ago(r.updatedAt) : "")])]),
            ed, del]));
        });
        var add = el("button", { type: "button", "class": "btn primary" }, [M.icon("plus", 15), "Add view"]);
        add.addEventListener("click", function () { m.close(); editor(null); });
        m = M.modal("Views added to the SCM Report", [el("p", { "class": "muted small", style: { margin: 0 }, text: "The built-in views carry no edit: their SQL belongs to their db-scripts. Added views are created / altered in V2RETAIL.SILVER from here." }), list], [add]);
      }

      function editor(editSlug) {
        var blank = { label: "", view: "", tcode: "", note: "", sql: "", mode: "sql", anchor: { kind: "none", col: "", days: 30 },
                      filterCols: [], searchCols: [], measures: [], defaultCols: [], sortCol: "", sortDir: "asc", replace: false };
        var form = JSON.parse(JSON.stringify(blank)), saved = JSON.stringify(form), preview = null, previewedKey = null, origin = null, hist = [], sqlSource = null, first = true, armed = false;
        function dirty() { return JSON.stringify(form) !== saved; }
        function srcKey() { return form.mode === "sql" ? "sql:" + form.sql : "view:" + form.view; }
        function stale() { return !preview || previewedKey !== srcKey(); }

        var body = el("div", { "class": "stack" }, el("div", { "class": "muted small" }, [el("span", { "class": "spin" }), " Reading the view…"]));
        var resetBtn = el("button", { type: "button", "class": "btn", text: "Reset" }), saveBtn = el("button", { type: "button", "class": "btn primary" }, [M.icon("save", 15), "Save"]);
        var delBtn = el("button", { type: "button", "class": "btn danger", style: { marginRight: "auto" } }, [M.icon("trash", 15), "Delete"]);
        var footNote = el("span", { "class": "muted tiny", style: { maxWidth: "420px" } });
        var m = M.modal(editSlug ? "Edit SCM view" : "Add a Snowflake view to the SCM Report", [body], [editSlug ? delBtn : el("span", { style: { marginRight: "auto" } }), footNote, resetBtn, saveBtn],
          { wide: true, onClose: function () { window.removeEventListener("keydown", escCap, true); window.removeEventListener("beforeunload", unload); } });
        // Closing with unsaved edits takes a second try (as DFS does) - close button, backdrop and Escape.
        function attemptClose() { if (dirty() && !armed) { armed = true; M.toast("Unsaved changes - close again to discard them."); return; } m.close(); }
        var bg = m.el.parentNode, xb = m.el.querySelector(".modal-h .btn");
        if (xb) { var nx = xb.cloneNode(true); xb.parentNode.replaceChild(nx, xb); nx.addEventListener("click", attemptClose); }
        bg.addEventListener("mousedown", function (e) { if (e.target === bg) { e.stopImmediatePropagation(); attemptClose(); } }, true);
        function escCap(e) {
          if (e.key !== "Escape" || document.querySelector(".pop")) return;
          var all = document.querySelectorAll(".modal-bg"), topBg = all[all.length - 1];
          e.stopPropagation();
          if (topBg === bg) attemptClose();
          else if (topBg) { var tx = topBg.querySelector(".modal-h .btn"); if (tx) tx.click(); }   // a dialog above the editor closes alone
        }
        window.addEventListener("keydown", escCap, true);
        function unload(e) { if (dirty()) { e.preventDefault(); e.returnValue = ""; } }
        window.addEventListener("beforeunload", unload);

        function runPreview() {
          var key = srcKey();
          return M.post(API + "/custom/preview", form.mode === "sql" ? { sql: form.sql } : { view: form.view }).then(function (d) {
            preview = d; previewedKey = key; err = "";
            // A NEW report adopts the preview's suggestions the first time only - after that the picks are the user's.
            if (!editSlug && first) {
              first = false; var s = d.suggest || {};
              form.view = form.view || d.view || "";
              form.anchor = { kind: (s.anchor || {}).kind || "none", col: (s.anchor || {}).col || "", days: (s.anchor || {}).days || 30 };
              form.filterCols = s.filterCols || []; form.measures = s.measures || []; form.searchCols = s.searchCols || [];
            }
            return d;
          }, function (e) { err = errMsg(e); preview = null; return null; });
        }
        var err = "";

        function draw() {
          M.clear(body);
          var cols = (preview && preview.columns) || [], names = cols.map(function (c) { return c.name; });
          var dateCols = cols.filter(function (c) { return /^(DATE|TIMESTAMP)/.test(c.type); }).map(function (c) { return c.name; });
          var runCols = cols.filter(function (c) { return c.type === "TEXT" || c.type === "FIXED"; }).map(function (c) { return c.name; });
          var savedView = (JSON.parse(saved).view || "").toUpperCase();
          var needsReplace = form.mode === "sql" && preview && preview.exists === "VIEW" && !(editSlug && (form.view || preview.view || "").toUpperCase() === savedView);
          function inp(key, attrs, upper) {
            var i = el("input", Object.assign({ type: "text", value: form[key] }, attrs || {}));
            i.addEventListener("input", function () { form[key] = upper ? i.value.toUpperCase() : i.value; if (upper) i.value = form[key]; sync(); });
            return i;
          }
          body.appendChild(el("div", { "class": "grid12", style: { gap: "12px" } }, [
            el("label", { "class": "f c6" }, ["Report name", inp("label", { placeholder: "e.g. DH24 Crate Ageing", maxlength: "120" })]),
            el("label", { "class": "f c3" }, ["View name (in V2RETAIL.SILVER)", inp("view", { placeholder: (preview && preview.view) || "V_MY_REPORT", style: { fontFamily: "ui-monospace, Consolas, monospace" } }, true)]),
            el("label", { "class": "f c3" }, ["SAP tcode (optional)", inp("tcode", { style: { fontFamily: "ui-monospace, Consolas, monospace" } })]),
            (function () { var ta = el("textarea", { rows: "2" }); ta.value = form.note; ta.addEventListener("input", function () { form.note = ta.value; sync(); });
              return el("label", { "class": "f c12" }, ["Note - printed above the grid (caveats a reader must know)", ta]); })()]));

          var modes = el("div", { "class": "row small" });
          [["sql", "Write SQL (create / alter the view)"], ["view", "Add an existing SILVER view as it is"]].forEach(function (x) {
            var r = el("input", { type: "radio", name: "scm-mode", checked: form.mode === x[0] });
            r.addEventListener("change", function () { form.mode = x[0]; draw(); });
            modes.appendChild(el("label", { "class": "row", style: { gap: "5px", cursor: "pointer" } }, [r, x[1]]));
          });
          if (sqlSource && form.mode === "sql") modes.appendChild(el("span", { "class": "muted tiny", text: sqlSource === "live" ? "SQL read live from the view (GET_DDL)" : "SQL as last saved here" }));
          body.appendChild(modes);
          if (form.mode === "sql") {
            var sql = el("textarea", { rows: "14", spellcheck: "false", "aria-label": "SQL", placeholder: "SELECT ... FROM V2RETAIL.BRONZE....\n\n- or paste a whole  CREATE OR REPLACE VIEW V2RETAIL.SILVER.V_X AS SELECT ...  file",
              style: { fontFamily: "ui-monospace, Consolas, monospace", fontSize: "12.5px", lineHeight: "1.5", background: "var(--surface-2)" } });
            sql.value = form.sql; sql.addEventListener("input", function () { form.sql = sql.value; sync(); });
            body.appendChild(sql);
          } else body.appendChild(el("p", { "class": "muted small", style: { margin: 0 }, text: "Type the view's name above and Validate. Nothing is created or altered - the view is added to the SCM Report exactly as it stands in SILVER, and is never dropped by the app." }));

          var valBtn = el("button", { type: "button", "class": "btn primary" }, [M.icon("play", 15), "Validate & preview"]);
          var status = el("span", { "class": "small" });
          valBtn.addEventListener("click", function () { M.action(valBtn, "Running…", function () { return runPreview(); }).then(draw, draw); });
          body.appendChild(el("div", { "class": "row" }, [valBtn, status]));
          if (err) body.appendChild(el("div", { "class": "notice bad" }, el("pre", { style: { margin: 0, whiteSpace: "pre-wrap", fontSize: "12px" }, text: err })));
          if (needsReplace) {
            var rp = el("input", { type: "checkbox", checked: form.replace }); rp.addEventListener("change", function () { form.replace = rp.checked; sync(); });
            body.appendChild(el("label", { "class": "notice warn small", style: { cursor: "pointer" } }, [rp, el("span", {}, [el("code", { text: preview.view || form.view }),
              " already exists in SILVER and was not made by this editor. Tick to ", el("b", { text: "replace the existing view" }), " with this SQL."])]));
          }

          if (preview) {
            // anchor + default sort
            var ak = el("select", { "aria-label": "Anchor" }, [el("option", { value: "none", text: "None - the current position" }),
              el("option", { value: "date", text: "Date range on a column", disabled: !dateCols.length }), el("option", { value: "run", text: "One run at a time (YYYYMMDD)" })]);
            ak.value = form.anchor.kind;
            ak.addEventListener("change", function () { form.anchor = { kind: ak.value, col: ak.value === "date" ? (dateCols[0] || "") : ak.value === "run" ? (runCols[0] || "") : "", days: form.anchor.days }; draw(); });
            var row = [el("label", { "class": "f c3" }, ["Anchor", ak])];
            if (form.anchor.kind !== "none") {
              var ac = el("select", { "aria-label": "Anchor column" }, (form.anchor.kind === "date" ? dateCols : runCols).map(function (c) { return el("option", { value: c, selected: c === form.anchor.col, text: c }); }));
              ac.addEventListener("change", function () { form.anchor.col = ac.value; sync(); });
              row.push(el("label", { "class": "f c3" }, ["Anchor column", ac]));
            }
            if (form.anchor.kind === "date") {
              var days = el("input", { type: "number", min: "1", max: "3650", value: form.anchor.days });
              days.addEventListener("input", function () { form.anchor.days = days.value; sync(); });
              row.push(el("label", { "class": "f c3" }, ["Default window (days, ending at the latest date)", days]));
            }
            var sc = el("select", { "aria-label": "Default sort column", style: { flex: 1 } }, [el("option", { value: "", text: "- none -" })].concat(names.map(function (c) { return el("option", { value: c, selected: c === form.sortCol, text: c }); })));
            var sd = el("select", { "aria-label": "Default sort direction" }, [el("option", { value: "asc", text: "asc" }), el("option", { value: "desc", text: "desc" })]); sd.value = form.sortDir;
            sc.addEventListener("change", function () { form.sortCol = sc.value; sync(); }); sd.addEventListener("change", function () { form.sortDir = sd.value; sync(); });
            row.push(el("div", { "class": "f c3" }, ["Default sort", el("div", { "class": "row", style: { flexWrap: "nowrap", gap: "6px" } }, [sc, sd])]));
            body.appendChild(el("div", { "class": "card", style: { boxShadow: "none" } }, el("div", { "class": "card-b grid12", style: { gap: "12px" } }, row)));

            // column grid
            var KEYS = [["filterCols", "Tick-list"], ["searchCols", "Search"], ["measures", "Total"], ["defaultCols", "Default"]];
            var tb = el("tbody");
            cols.forEach(function (c) {
              var tr = el("tr", {}, [el("td", { style: { fontFamily: "ui-monospace, Consolas, monospace" }, text: c.name }),
                el("td", { "class": "tiny", style: { fontFamily: "ui-monospace, Consolas, monospace", color: c.type === "DATE" ? "var(--s1)" : c.type === "FIXED" || c.type === "REAL" ? "var(--s2)" : "var(--muted)" }, text: c.type }),
                el("td", { "class": "num", style: { color: c.distinct > 150 ? "var(--muted)" : null }, text: c.distinct == null ? "–" : M.fmt(c.distinct, "int") })]);
              KEYS.forEach(function (k) {
                var cb = el("input", { type: "checkbox", checked: form[k[0]].indexOf(c.name) >= 0, disabled: k[0] === "measures" && !(c.type === "FIXED" || c.type === "REAL"), "aria-label": k[1] + " " + c.name });
                cb.addEventListener("change", function () { var a = form[k[0]]; if (cb.checked) { if (a.indexOf(c.name) < 0) a.push(c.name); } else form[k[0]] = a.filter(function (x) { return x !== c.name; }); sync(); });
                tr.appendChild(el("td", { style: { textAlign: "center" } }, cb));
              });
              tb.appendChild(tr);
            });
            var th = function (t, al) { return el("th", { style: { textAlign: al || "left" }, text: t }); };
            body.appendChild(el("div", { "class": "card", style: { boxShadow: "none" } }, [
              el("div", { "class": "card-b small muted", style: { borderBottom: "1px solid var(--line-2)" } }, [el("b", { text: "Columns. " }),
                "Tick-list = a filter AND a split for exports (keep to low-cardinality columns); Search = matched by the search box; Total = summed in the totals strip (additive quantities only - never a %, a rate or an ageing); Default = shown when the grid opens (none ticked = all). \"distinct\" is counted over the first 200,000 rows."]),
              el("div", { style: { maxHeight: "38vh", overflow: "auto" } }, el("table", { "class": "dg-t", style: { tableLayout: "auto", width: "100%" } }, [
                el("thead", {}, el("tr", {}, [th("Column"), th("Type"), th("distinct", "right")].concat(KEYS.map(function (k) { return th(k[1], "center"); })))), tb]))]));
            // sample
            if ((preview.rows || []).length) {
              var st2 = el("table", { "class": "dg-t", style: { tableLayout: "auto" } }, [el("thead", {}, el("tr", {}, names.map(function (c) { return el("th", { text: c }); }))),
                el("tbody", {}, preview.rows.map(function (r) { return el("tr", {}, r.map(function (v) { return el("td", { text: v === "" || v == null ? "–" : String(v) }); })); }))]);
              body.appendChild(el("details", { "class": "card", style: { boxShadow: "none" } }, [el("summary", { "class": "small muted", style: { padding: "10px 14px", cursor: "pointer" }, text: "Sample - first " + preview.rows.length + " rows" }),
                el("div", { style: { maxHeight: "30vh", overflow: "auto" } }, st2)]));
            }
          }
          if (hist.length) body.appendChild(el("div", { "class": "muted tiny", text: "History: " + hist.slice(0, 4).map(function (h) { return h.action + " by " + h.by + " · " + String(h.at).slice(0, 16).replace("T", " "); }).join("  ·  ") }));

          function sync() {
            M.clear(status);
            if (preview && !stale()) status.appendChild(el("span", { style: { color: "var(--ok)" }, text: cols.length + " columns · " + (preview.capped ? "200,000+ rows" : M.fmt(preview.sampled, "int") + " rows") +
              (preview.view ? " · " + preview.view : "") + (preview.exists ? " · already exists (" + String(preview.exists).toLowerCase() + ")" : " · new view") }));
            else if (preview) status.appendChild(el("span", { style: { color: "var(--warn)" }, text: "The SQL changed since it was validated - validate again before saving." }));
            valBtn.disabled = form.mode === "sql" ? !form.sql.trim() : !form.view.trim();
            saveBtn.disabled = stale() || !form.label.trim();
            resetBtn.disabled = !dirty();
            if (dirty()) armed = false;
            footNote.textContent = form.mode === "sql" ? "Save runs CREATE OR REPLACE VIEW in V2RETAIL.SILVER (grants kept) - only after the checks pass." : "Save adds the existing view to the SCM Report; nothing in Snowflake changes.";
            saveBtn._needsReplace = needsReplace; saveBtn._names = names;
          }
          sync();
        }

        resetBtn.addEventListener("click", function () { form = JSON.parse(saved); draw(); });
        saveBtn.addEventListener("click", function () {
          if (!form.label.trim()) { M.toast("Give the report a name.", "bad"); return; }
          if (stale()) { M.toast("Validate the SQL first - the column grid must describe what is being saved.", "bad"); return; }
          if (saveBtn._needsReplace && !form.replace) { M.toast("That view already exists - tick \"replace the existing view\" to overwrite it.", "bad"); return; }
          var names = saveBtn._names || [], keep = function (xs) { return xs.filter(function (c) { return names.indexOf(c) >= 0; }); };
          var body2 = { slug: editSlug || null, label: form.label, view: form.view || (preview && preview.view) || null, tcode: form.tcode || null, note: form.note || null,
            sql: form.mode === "sql" ? form.sql : null,
            anchor: form.anchor.kind === "none" ? { kind: "none" } : { kind: form.anchor.kind, col: form.anchor.col, days: Number(form.anchor.days) || 30 },
            filterCols: keep(form.filterCols), searchCols: keep(form.searchCols), measures: keep(form.measures), defaultCols: keep(form.defaultCols),
            order: form.sortCol ? [form.sortCol + (form.sortDir === "desc" ? " DESC" : "")] : [], replace_existing: !!form.replace };
          M.action(saveBtn, "Saving…", function () { return M.post(API + "/custom", body2); },
            function (d) { return (d.action === "create" ? "Created " : d.action === "alter" ? "Altered " : "Added ") + d.view; }).then(function (d) {
            saved = JSON.stringify(form); m.close();
            if (!ctx.alive()) return;
            // A re-saved report may have new columns, so its cached params are stale too.
            var same = d.slug === slug;
            loadCatalogue(d.slug).then(function (s) { openView(s, same ? { refresh: true } : {}); });
          }, function (e) { err = errMsg(e); draw(); });
        });
        delBtn.addEventListener("click", function () {
          var r = catalogue.filter(function (x) { return x.slug === editSlug; })[0] || { slug: editSlug, label: form.label, view: form.view };
          r = Object.assign({}, r, { origin: origin || r.origin, view: form.view || r.view });
          removeView(r, function () { saved = JSON.stringify(form); m.close(); });
        });

        if (!editSlug) { draw(); return; }
        // Editing: the definition + the view's LIVE SQL, then validate straight away so the column grid is populated.
        M.get(API + "/custom/" + encodeURIComponent(editSlug)).then(function (d) {
          var df = d.definition || {}, o = (df.order || [])[0] || "", i = o.lastIndexOf(" "), tail = i > 0 ? o.slice(i + 1).toUpperCase() : "";
          var sortCol = tail === "DESC" || tail === "ASC" ? o.slice(0, i) : o, sortDir = tail === "DESC" ? "desc" : "asc";
          form = Object.assign(JSON.parse(JSON.stringify(blank)), { label: df.label || "", view: d.view || "", tcode: df.tcode || "", note: df.note || "", sql: d.sql || "", mode: d.sql ? "sql" : "view",
            anchor: { kind: (df.anchor || {}).kind || "none", col: (df.anchor || {}).col || "", days: (df.anchor || {}).days || 30 },
            filterCols: df.filterCols || [], searchCols: df.searchCols || [], measures: df.measures || [], defaultCols: df.defaultCols || [], sortCol: sortCol, sortDir: sortDir });
          saved = JSON.stringify(form); origin = d.origin; hist = d.history || []; sqlSource = d.sqlSource;
          if (d.ddlError) err = "Could not read the view's live SQL: " + d.ddlError;
          var e0 = err;
          return runPreview().then(function () { if (e0 && !err) err = e0; draw(); });
        }, function (e) { M.toast(errMsg(e), "bad"); m.close(); });
      }

      // ── wiring ────────────────────────────────────────────────────────────────────────────────────
      refreshBtn.addEventListener("click", function () { if (slug) M.action(refreshBtn, "Reading…", function () { return openView(slug, { refresh: true, keep: true }); }); });
      if (addBtn) addBtn.addEventListener("click", function () { editor(null); });
      if (manageBtn) manageBtn.addEventListener("click", manage);

      pickerCard.appendChild(el("div", { "class": "card-b muted" }, [el("span", { "class": "spin" }), " Loading the report catalogue…"]));
      return loadCatalogue(M.qs("view")).then(function (s) { if (ctx.alive()) return openView(s || ""); });
    }
  });
})();

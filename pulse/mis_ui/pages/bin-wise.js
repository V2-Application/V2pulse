// Bin Wise (DFS /sf-mis-bin, SfBinReport.jsx): one row per warehouse BIN from BRONZE.ET_ZPBI_ZWM_BIN_MOC - where it sits,
// what state it is in and what it holds. Snapshot date on a coverage calendar (green = loaded, red = no snapshot), tick-list
// filters, search, server sort / paging, totals over the whole filtered set, CSV export (whole selection, or one file per
// value of the split columns).
// API: /api/v1/mis/bin/{params, result, export, values}.
// Not here (DFS planning-service, Node-only): the formatted template, the workbook with a tab per value and Write to folder -
// "Schedule a dump…" (/mis/dumps?new=bin_wise) writes files to a folder instead.
(function () {
  "use strict";
  var M = window.MIS, el = M.el;
  var API = "/api/v1/mis/bin";
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  var TILE_MEAS = ["CURR_STK", "OP_STK", "CL_STK", "PICK_PEN", "TOT_PEN", "TD_IN_Q", "TD_OUT_Q"];
  function iso(y, m, d) { return y + "-" + String(m + 1).padStart(2, "0") + "-" + String(d).padStart(2, "0"); }

  // A month calendar that colours coverage: green where a snapshot is loaded, red where it is not (not selectable).
  function calendar(anchor, value, dates, onPick) {
    var have = {}; (dates || []).forEach(function (d) { have[d] = true; });
    var cur = value || (dates || [])[0] || M.today(), y = +cur.slice(0, 4), m = +cur.slice(5, 7) - 1;
    var box = el("div", { "class": "pop-b", style: { width: "268px" } });
    function step(n) { var d = new Date(Date.UTC(y, m + n, 1)); y = d.getUTCFullYear(); m = d.getUTCMonth(); draw(); }
    function draw() {
      M.clear(box);
      box.appendChild(el("div", { "class": "row", style: { justifyContent: "space-between", marginBottom: "6px", flexWrap: "nowrap" } }, [
        el("button", { type: "button", "class": "btn ghost sm", "aria-label": "Previous month", text: "‹", onclick: function () { step(-1); } }),
        el("b", { "class": "small", text: MONTHS[m] + " " + y }),
        el("button", { type: "button", "class": "btn ghost sm", "aria-label": "Next month", text: "›", onclick: function () { step(1); } })]));
      var g = el("div", { style: { display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: "3px", textAlign: "center" } });
      ["S", "M", "T", "W", "T", "F", "S"].forEach(function (d) { g.appendChild(el("span", { "class": "muted", style: { fontSize: "10px" }, text: d })); });
      var first = new Date(Date.UTC(y, m, 1)).getUTCDay(), days = new Date(Date.UTC(y, m + 1, 0)).getUTCDate(), loaded = 0;
      for (var i = 0; i < first; i++) g.appendChild(el("span"));
      for (var d = 1; d <= days; d++) (function (d) {
        var day = iso(y, m, d), ok = !!have[day], on = day === value;
        if (ok) loaded++;
        var b = el("button", { type: "button", disabled: !ok, title: day + (ok ? " - loaded" : " - no snapshot loaded"), text: String(d),
          style: { font: "inherit", fontSize: "12px", border: on ? "2px solid var(--ok)" : "1px solid transparent", borderRadius: "6px", padding: "4px 0",
                   background: ok ? "var(--ok-soft)" : "var(--bad-soft)", color: ok ? "var(--ok)" : "var(--bad)", fontWeight: ok ? "600" : "400",
                   cursor: ok ? "pointer" : "not-allowed", opacity: ok ? "1" : ".6" } });
        if (ok) b.addEventListener("click", function () { M.closePop(); onPick(day); });
        g.appendChild(b);
      })(d);
      box.appendChild(g);
      box.appendChild(el("div", { "class": "row tiny muted", style: { marginTop: "8px", gap: "12px" } }, [
        el("span", {}, [el("i", { style: { display: "inline-block", width: "10px", height: "10px", borderRadius: "3px", background: "var(--ok-soft)", border: "1px solid var(--ok)", marginRight: "4px" } }), "loaded"]),
        el("span", {}, [el("i", { style: { display: "inline-block", width: "10px", height: "10px", borderRadius: "3px", background: "var(--bad-soft)", marginRight: "4px" } }), "no data"]),
        el("span", { "class": "spacer" }), loaded + " day(s) this month"]));
    }
    draw();
    M.popover(anchor, [box]);
  }

  M.page("bin-wise", {
    render: function (ctx) {
      var me = ctx.me || { can: {} }, st = { date: "", search: "", res: null, meta: null };
      var dumpBtn = me.can.dumps ? el("a", { "class": "btn", href: "/mis/dumps?new=bin_wise", title: "Write the bin report to a folder on a schedule (the DFS Write to folder)" },
        [M.icon("clock", 15), "Schedule a dump…"]) : null;
      M.setHeader({ title: "Bin Wise", sub: "One row per warehouse bin - where it sits (site, floor, section, row, gondola, storage type, bin), what state it is in and what it holds. Opens on the most recent snapshot loaded.",
        actions: [dumpBtn], tiles: [{ k: "Snapshot", skel: true }, { k: "Bins", skel: true }].concat(TILE_MEAS.map(function (m) { return { k: m, skel: true }; })) });

      return M.get(API + "/params").then(function (p) {
        if (!ctx.alive()) return;
        st.meta = p; st.date = p.latest || "";
        var opts = p.options || {}, measures = p.measures || [], filterCols = p.filterCols || [];
        function isMeas(c) { return measures.indexOf(c) >= 0; }

        // ── filter bar ─────────────────────────────────────────────────────────────────────────────
        var dateBtn = el("button", { type: "button", "class": "btn dd-btn", "aria-label": "Snapshot date" });
        function paintDate() { M.clear(dateBtn); dateBtn.appendChild(M.icon("day", 15)); dateBtn.appendChild(el("span", { "class": "t", style: { fontFamily: "monospace" }, text: st.date || "pick a date" })); dateBtn.appendChild(M.icon("chev", 13)); }
        paintDate();
        dateBtn.addEventListener("click", function () { calendar(dateBtn, st.date, p.dates || [], function (d) { if (d === st.date) return; st.date = d; paintDate(); grid.reload(true); }); });
        var search = el("input", { type: "search", placeholder: "bin, site name or last movement…", style: { minWidth: "260px" }, "aria-label": "Search" });
        search.addEventListener("input", M.debounce(function () { st.search = search.value.trim(); grid.reload(true); }, 400));
        // The tick-list filters ARE the grid's header filters on the same columns - one state, two ways in.
        var fms = filterCols.map(function (c) {
          return { c: c, ms: M.multiSelect(c, function () { return Promise.resolve(opts[c] || []); }, [], function (v) {
            var s = grid.state(); if (v.length) s.filters[c] = v; else delete s.filters[c]; grid.setState({ filters: s.filters }); grid.reload(true);
          }) };
        });
        var card = el("div", { "class": "card" });
        card.appendChild(el("div", { "class": "fbar" }, [el("label", { "class": "f" }, ["Snapshot date", dateBtn]), el("label", { "class": "f" }, ["Search", search])]
          .concat(fms.map(function (x) { return x.ms.el; }))));
        ctx.main.appendChild(card);
        ctx.main.appendChild(el("div", { "class": "notice" }, [M.icon("info", 16), el("span", { text:
          "The formatted (template) export, the workbook with a tab per value and Write to folder of DFS are not available in V2Pulse yet. " +
          "The CSV export carries the filters and search this page is showing" + (me.can.dumps ? "; to write files to a folder on a timer, use Schedule a dump…" : ".") })]));

        function body(gs) {
          var f = {}; Object.keys(gs.filters || {}).forEach(function (c) { if (gs.filters[c] && gs.filters[c].length) f[c] = gs.filters[c]; });
          return { date: st.date || null, col_filters: f, search: st.search || null, page: gs.page, limit: gs.limit, sort_col: gs.sort_col || null, sort_dir: gs.sort_dir };
        }
        function filtered(gs) { return !!st.search || Object.keys(body(gs).col_filters).length > 0; }
        function paintTiles(gs) {
          var d = st.res || {}, t = d.totals || {};
          M.setHeader({ tiles: [{ k: "Snapshot", v: d.date ? M.fmtDate(d.date) : "–", s: (p.dates || []).length + " day(s) loaded" },
            { k: "Bins", v: M.fmt(d.total, "int"), s: filtered(gs) ? "after filters" : "whole snapshot" }].concat(
            TILE_MEAS.filter(function (m) { return m in t; }).map(function (m) { return { k: m, v: M.fmt(t[m], "dec"), s: "total over the filtered set" }; })) });
        }

        var gcard = el("div", { "class": "card" });
        ctx.main.appendChild(gcard);
        var grid = M.Grid(gcard, {
          id: "bin-wise", title: "Bins", limit: 200, pageSizes: [100, 200, 500, 1000], conditions: false,
          isKey: function (c) { return !isMeas(c); },
          format: function (c) { return isMeas(c) ? "dec" : "text"; },
          label: function (c) { return c; },
          fetch: function (gs, signal) {
            return M.post(API + "/result", body(gs), { signal: signal }).then(function (d) {
              st.res = d; if (ctx.alive()) paintTiles(gs);
              return { columns: d.columns || [], rows: d.rows || [], total: d.total || 0, totals: d.totals || null };
            });
          },
          values: function (c) {
            if (opts[c]) return Promise.resolve(opts[c]);
            return Promise.reject(new Error(isMeas(c) ? "a measure has no value list" : "too many values for a list - use Search (bin, site name or last movement)"));
          },
          cell: function (td, c, v) { if (v === null || v === undefined || v === "") { td.textContent = "—"; td.style.color = "var(--muted)"; } },
          onData: function (d, gs) { fms.forEach(function (x) { x.ms.set((gs.filters || {})[x.c] || []); }); },
          getExtra: function () { return { search: st.search }; },
          setExtra: function (x) { st.search = x.search || ""; search.value = st.search; },
          exports: me.can.export ? [
            { label: "CSV - the whole filtered selection", fn: function (gs) {
              var b = body(gs); delete b.page; delete b.limit;
              M.toast("Building the CSV…");
              M.download("POST", API + "/export", b, "BIN_WISE_" + st.date + ".csv").then(function (n) { M.toast("Downloaded " + n, "ok"); }, function (e) { M.toast(e.message, "bad"); });
            } },
            { label: "CSV - one file per value of…", fn: function (gs) { splitExport(gs); } }] : []
        });

        // One CSV per value (or per combination of values) of the split columns, downloaded in sequence. Only the filter
        // columns can be split on (a split is the report re-run with that value as a filter); large splits belong in a dump.
        function splitExport(gs) {
          var cl = M.checklist([{ items: filterCols }], [], { prettify: false, height: "220px" });
          var go = el("button", { type: "button", "class": "btn primary", text: "Download the files" }), cancel = el("button", { type: "button", "class": "btn", text: "Cancel" });
          var md = M.modal("Export one CSV per value", [el("p", { "class": "muted small", style: { margin: 0 }, text: "Pick the columns to split by. Two columns mean the combination: one file per pair that exists in the data, not one set per column. At most 40 files; for a bigger split use Schedule a dump…" }), cl.el], [cancel, go]);
          cancel.addEventListener("click", function () { md.close(); });
          go.addEventListener("click", function () {
            var levels = cl.get(); if (!levels.length) { M.toast("Pick at least one column to split by.", "bad"); return; }
            md.close();
            var base = body(gs); delete base.page; delete base.limit;
            M.toast("Finding " + levels.join(" + ") + " values…");
            M.post(API + "/values", Object.assign({}, base, { columns: levels })).then(function (v) {
              var combos = v.combos || [];
              if (!combos.length) throw new Error("No values to split on.");
              if (combos.length > 40) throw new Error(combos.length + " files is too many to download one by one - use Schedule a dump… for a split this size.");
              var i = 0;
              function next() {
                if (i >= combos.length) { M.toast(combos.length + " file(s) downloaded.", "ok"); return; }
                var f = JSON.parse(JSON.stringify(base.col_filters || {})), combo = combos[i];
                levels.forEach(function (l, j) { f[l] = [combo[j]]; });
                var stem = levels.map(function (l, j) { return l + "_" + String(combo[j]).replace(/[\\/:*?"<>|]+/g, "_"); }).join("__");
                M.toast((i + 1) + " / " + combos.length + " - " + stem);
                i++;
                return M.download("POST", API + "/export", Object.assign({}, base, { col_filters: f }), "BIN_WISE_" + (v.date || st.date) + "__" + stem + ".csv").then(next);
              }
              return next();
            }).catch(function (e) { M.toast(e.message, "bad"); });
          });
        }

        return grid.applyDefaultView().then(function () { if (ctx.alive()) return grid.reload(true); });
      }, function (e) {
        if (!ctx.alive()) return;
        ctx.main.appendChild(el("div", { "class": "notice bad", text: e.message }));
      });
    }
  });
})();

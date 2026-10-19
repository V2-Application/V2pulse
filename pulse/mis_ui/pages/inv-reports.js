// Inventory Reports (DFS /sf-mis-inv-reports, SfInvReports.jsx): saved report definitions over the Vertical Inventory
// (Article) slice - the card gallery, the builder (Group by in pick order, measures, father / child grid columns, Art
// type, saved filters, conditions, units, share / sidebar flags, preview) and save / delete for the owner.
//   "inv-reports"  /mis/inv-reports               (?edit=<slug> opens the builder on that report, ?new=1 a blank one)
//   "inv-report"   /mis/inv-reports/r/<slug>      the Vertical Inventory (Article) grid locked to the report (vert-inv.js)
// API: /api/v1/mis/inv-report/{catalogue, reports (GET / POST), reports/<id> (DELETE), run, col-values, export}.
// Not here (DFS planning-service, Node-only): the multi-report workbook and the INV templates - see the note on the page.
(function () {
  "use strict";
  var M = window.MIS, el = M.el;
  var API = "/api/v1/mis/inv-report";
  var DIM_LABEL = { ST_CD: "Plant", ART: "ARTICLE" };
  function dimLabel(d) { return DIM_LABEL[d] || (/^(PM|SM):/.test(d) ? d.slice(3) : d); }
  function blank() {
    return { name: "", slug: "", description: "", isShared: false, showInSidebar: true,
             definition: { group_by: ["ST_CD", "MJ"], measures: ["OP_Q", "IN_Q", "OUT_Q", "CL_Q", "NEG_Q"], filters: {}, units: "kl" } };
  }
  function isOwner(ctx, r) { return String(r.createdBy || "") === String((ctx.me || {}).emp_code || ""); }
  function fixed(n, d) { return Number(n).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }); }

  // Load vert-inv.js (the grid the opened report is) once, through the same module cache core.js uses.
  function needVertInv() {
    if (M.vertInv) return Promise.resolve(M.vertInv);
    if (!M.cache["mod:vert-inv"]) M.cache["mod:vert-inv"] = new Promise(function (res, rej) {
      var s = el("script", { src: "/mis/static/pages/vert-inv.js?v=" + (window.MIS_VERSION || "1") });
      s.onload = res; s.onerror = function () { rej(new Error("The Vertical Inventory page could not be loaded.")); }; document.head.appendChild(s);
    });
    return M.cache["mod:vert-inv"].then(function () { if (!M.vertInv) throw new Error("The Vertical Inventory page could not be loaded."); return M.vertInv; });
  }

  // An ordered pick list (DFS PickList): ticking order IS the order kept - load-bearing for Group by (the column order).
  // groups: [[label, [values]]]. onChange(values in pick order).
  function pickList(title, groups, value, labelOf, onChange, opts) {
    opts = opts || {}; var picks = (value || []).slice();
    var q = el("input", { type: "search", placeholder: "Search", "aria-label": "Search " + title });
    var list = el("div", { "class": "checks", style: { maxHeight: opts.height || "300px" } }), cnt = el("span", { "class": "muted tiny" });
    var clr = el("button", { type: "button", "class": "btn ghost sm", text: "Clear" });
    clr.addEventListener("click", function () { picks = []; draw(); onChange([]); });
    q.addEventListener("input", draw);
    function draw() {
      M.clear(list); var s = q.value.trim().toLowerCase(), n = 0;
      groups.forEach(function (g) {
        var items = (g[1] || []).filter(function (v) { return !s || String(labelOf(v)).toLowerCase().indexOf(s) >= 0 || String(v).toLowerCase().indexOf(s) >= 0; });
        if (!items.length) return;
        if (g[0]) list.appendChild(el("div", { "class": "grp", text: g[0] }));
        items.slice(0, opts.cap || 2000).forEach(function (v) {
          n++; var at = picks.indexOf(v);
          var cb = el("input", { type: "checkbox", checked: at >= 0 });
          cb.addEventListener("change", function () {
            if (cb.checked) { if (picks.indexOf(v) < 0) picks.push(v); } else picks = picks.filter(function (x) { return x !== v; });
            draw(); onChange(picks.slice());
          });
          list.appendChild(el("label", { title: String(v) }, [cb, el("span", { text: String(labelOf(v)) }),
            at >= 0 && opts.ordered ? el("span", { "class": "cnt", style: { color: "var(--brand)", fontWeight: "600" }, text: "#" + (at + 1) }) : null]));
        });
      });
      if (!n) list.appendChild(el("div", { "class": "muted small", style: { padding: "8px" }, text: "Nothing matches." }));
      cnt.textContent = picks.length + " picked";
    }
    draw();
    var card = el("div", { "class": "card", style: { height: "100%" } }, [
      el("div", { "class": "card-h" }, [el("h2", { text: title }), el("span", { "class": "spacer" }), cnt, clr]),
      el("div", { "class": "card-b stack", style: { gap: "6px" } }, [q, list, opts.note ? el("div", { "class": "muted tiny", text: opts.note }) : null])]);
    return { el: card, get: function () { return picks.slice(); },
             setGroups: function (g) { groups = g; draw(); }, set: function (v) { picks = (v || []).slice(); draw(); } };
  }

  // ── the gallery + builder ────────────────────────────────────────────────────────────────────────
  M.page("inv-reports", {
    render: function (ctx) {
      var me = ctx.me || { can: {} }, cat = null, reports = [];
      var newBtn = me.can.manage ? el("button", { type: "button", "class": "btn primary" }, [M.icon("plus", 15), "New report"]) : null;
      var dumpBtn = me.can.dumps ? el("a", { "class": "btn", href: "/mis/dumps?new=inv_report", title: "Write inventory reports to a folder on a schedule" }, [M.icon("clock", 15), "Schedule a dump…"]) : null;
      var SUB = "Saved reports over the Vertical Inventory (Article) stock - opening, IN / OUT, closing and negative stock by plant, article and storage location. " +
                "Nothing is stored per report: every run reads the stock of the chosen as-of date, so a saved report always follows the data.";
      M.setHeader({ title: "Inventory Reports", sub: SUB,
        actions: [dumpBtn, newBtn], tiles: [{ k: "Reports", skel: true }, { k: "Shared", skel: true }, { k: "Yours", skel: true }, { k: "Latest stock", skel: true }] });
      var boot = el("div", { "class": "card" }, el("div", { "class": "card-b muted" }, [el("span", { "class": "spin" }), " Loading - the first open of a date builds it (about a minute)…"]));
      ctx.main.appendChild(boot);

      function reload() { return M.get(API + "/reports").then(function (r) { reports = r.reports || []; return reports; }); }
      return Promise.all([M.get(API + "/catalogue"), reload()]).then(function (x) {
        if (!ctx.alive()) return;
        cat = x[0]; boot.remove();
        if (newBtn) newBtn.addEventListener("click", function () { openEditor(blank()); });
        var e = M.qs("edit");
        if (e) {
          var r = reports.filter(function (y) { return y.slug === e; })[0];
          if (!r) M.toast("No inventory report '" + e + "' that you can see.", "bad");
          else if (!me.can.manage || !isOwner(ctx, r)) M.toast("Only " + r.createdBy + " can edit “" + r.name + "” - Duplicate it to make your own.", "bad");
          else return openEditor(r);
        }
        if (M.qs("new") && me.can.manage) return openEditor(blank());
        gallery();
      }, function (e) {
        if (!ctx.alive()) return;
        M.clear(boot).appendChild(el("div", { "class": "card-b" }, el("div", { "class": "notice bad", text: e.message })));
      });

      function tiles() {
        M.setHeader({ tiles: [{ k: "Reports", v: M.fmt(reports.length, "int"), s: "you can see" },
          { k: "Shared", v: M.fmt(reports.filter(function (r) { return r.isShared; }).length, "int"), s: "with everyone" },
          { k: "Yours", v: M.fmt(reports.filter(function (r) { return isOwner(ctx, r); }).length, "int") },
          { k: "Latest stock", v: cat && cat.asOf ? M.fmtDate(cat.asOf) : "–", s: cat && cat.latestEtDate ? "ET reading " + cat.latestEtDate : "" }] });
      }
      function setUrl(u) { history.replaceState({}, "", u); }

      // ── card list ──────────────────────────────────────────────────────────────────────────────
      function gallery() {
        setUrl("/mis/inv-reports"); M.clear(ctx.main); tiles();
        M.setHeader({ title: "Inventory Reports", sub: SUB, crumbs: [{ label: "Inventory Reports" }], actions: [dumpBtn, newBtn] });
        var q = el("input", { type: "search", placeholder: "Find a report", style: { minWidth: "260px" }, "aria-label": "Find a report" });
        var who = M.store.get("invrep.who", "all");
        var seg = el("div", { "class": "seg", role: "group", "aria-label": "Show" }, [["all", "All"], ["mine", "Mine"], ["shared", "Shared"]].map(function (o) {
          return el("button", { type: "button", "aria-pressed": String(who === o[0]), text: o[1], onclick: function () {
            who = o[0]; M.store.set("invrep.who", who); seg.querySelectorAll("button").forEach(function (b, i) { b.setAttribute("aria-pressed", String(["all", "mine", "shared"][i] === who)); }); draw(); } });
        }));
        var box = el("div", { "class": "rgrid" }), card = el("div", { "class": "card" });
        card.appendChild(el("div", { "class": "fbar" }, [el("label", { "class": "f" }, ["Search", q]), el("label", { "class": "f" }, ["Show", seg]), el("span", { "class": "spacer" }),
          el("span", { "class": "muted tiny", style: { maxWidth: "60ch" }, text: "The multi-report workbook of DFS is not available in V2Pulse yet - to export several reports together, use Schedule a dump… (kind Inventory Report, several reports)." })]));
        card.appendChild(el("div", { "class": "card-b" }, box));
        ctx.main.appendChild(card);
        q.addEventListener("input", M.debounce(draw, 150));
        function draw() {
          M.clear(box); var s = q.value.trim().toLowerCase();
          var list = reports.filter(function (r) {
            if (who === "mine" && !isOwner(ctx, r)) return false;
            if (who === "shared" && !r.isShared) return false;
            return !s || (r.name + " " + (r.description || "") + " " + r.slug).toLowerCase().indexOf(s) >= 0;
          });
          if (!reports.length) { box.appendChild(el("div", { "class": "dg-empty", style: { gridColumn: "1 / -1" } }, [M.icon("box", 28), el("div", { style: { marginTop: "6px" }, text: "No inventory reports yet." + (me.can.manage ? " New report starts one." : "") })])); return; }
          if (!list.length) { box.appendChild(el("div", { "class": "dg-empty", style: { gridColumn: "1 / -1" }, text: "No report matches." })); return; }
          list.forEach(function (r) { box.appendChild(rcard(r)); });
        }
        draw();
      }
      function rcard(r) {
        var def = r.definition || {}, nf = Object.keys(def.filters || {}).length, nc = (def.dim_filters || []).length + (def.col_filters || []).length;
        var href = "/mis/inv-reports/r/" + encodeURIComponent(r.slug), own = isOwner(ctx, r);
        var foot = el("div", { "class": "foot" }, [el("a", { "class": "btn sm primary", href: href }, [M.icon("play", 13), "Open"])]);
        if (me.can.manage) foot.appendChild(el("button", { type: "button", "class": "btn sm", onclick: function () { duplicate(r); } }, [M.icon("copy", 13), "Duplicate"]));
        if (me.can.manage && own) {
          foot.appendChild(el("button", { type: "button", "class": "btn sm", onclick: function () { openEditor(r); } }, [M.icon("edit", 13), "Edit"]));
          foot.appendChild(el("span", { "class": "spacer" }));
          foot.appendChild(el("button", { type: "button", "class": "btn sm danger", "aria-label": "Delete " + r.name, onclick: function (e) { remove(r, e.currentTarget); } }, [M.icon("trash", 13), "Delete"]));
        }
        var view = r.view ? el("button", { type: "button", "class": "btn ghost sm", style: { justifyContent: "flex-start", fontFamily: "monospace", fontSize: "11px", padding: "2px 4px", color: "var(--muted)" },
          title: "Published as the Snowflake view " + r.view + " (latest built as-of date) - click to copy", onclick: function () {
            var done = function () { M.toast("View name copied.", "ok"); };
            if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(r.view).then(done, function () { M.toast("Could not copy.", "bad"); });
            else M.toast(r.view);
          } }, [M.icon("copy", 12), " " + r.view]) : null;
        return el("div", { "class": "rcard" }, [
          el("div", { "class": "row", style: { justifyContent: "space-between", flexWrap: "nowrap" } }, [el("h3", { style: { minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, el("a", { href: href, text: r.name, title: r.name })),
            el("span", { "class": "chip " + (r.isShared ? "ok" : "grey"), title: r.isShared ? "Shared with everyone" : "Only you can see this", text: r.isShared ? "Shared" : "Private" })]),
          r.description ? el("p", { text: r.description }) : null,
          el("div", { "class": "tiny muted" , text: (def.group_by || []).map(dimLabel).join(" × ") + ((def.grid || []).length ? " → grid " + def.grid.map(dimLabel).join(", ") : "") +
            " · " + (def.measures || []).length + " measure(s)" + (nf ? " · " + nf + " saved filter(s)" : "") + (nc ? " · " + nc + " condition(s)" : "") +
            " · " + (def.units === "raw" ? "pcs / Rs" : "K · Lakh") }),
          el("div", { "class": "tiny muted", text: "by " + (r.createdBy || "–") + (r.updatedAt ? " · updated " + M.fmtDate(r.updatedAt) : "") + (r.showInSidebar === false ? " · not in sidebar" : "") }),
          view, foot]);
      }
      function duplicate(r) {
        var c = JSON.parse(JSON.stringify(r)); delete c.reportId; c.name = r.name + " (copy)"; c.slug = ""; c.isShared = false; openEditor(c);
      }
      function remove(r, btn) {
        M.confirm("Delete this report?", "Delete the inventory report “" + r.name + "”? Its Snowflake view is dropped too, and any dump schedule that names it will fail.", "Delete", true).then(function (ok) {
          if (!ok) return;
          M.action(btn, "Deleting…", function () { return M.api("DELETE", API + "/reports/" + encodeURIComponent(r.reportId)); }, "Deleted.")
            .then(function () { return reload(); }).then(function () { if (ctx.alive()) gallery(); }, function () { /* toasted */ });
        });
      }

      // ── builder ────────────────────────────────────────────────────────────────────────────────
      function openEditor(src) {
        var ed = JSON.parse(JSON.stringify(src)), d = ed.definition = ed.definition || {};
        d.group_by = d.group_by || []; d.measures = d.measures || []; d.filters = d.filters || {}; d.units = d.units === "raw" ? "raw" : "kl";
        var filterCols = Object.keys(d.filters).filter(function (c) { return c !== "ART_TYPE"; });
        var labels = cat.labels || {}, measures = cat.measures || [];
        var dimGroups = Object.keys(cat.dims || {}).map(function (g) { return [g, cat.dims[g]]; });
        var allDims = [].concat.apply([], dimGroups.map(function (g) { return g[1]; }));
        function measLabel(m) { return labels[m] || m; }
        // The saved conditions, read back into the editor's one flat list (dimension and measure rows alike).
        var condRows = (d.dim_filters || []).concat(d.col_filters || []).map(function (c) { return { col: c.col, op: c.op, v1: c.v1, v2: c.v2, vals: c.vals }; });

        setUrl(ed.reportId ? "/mis/inv-reports?edit=" + encodeURIComponent(ed.slug) : "/mis/inv-reports?new=1");
        M.clear(ctx.main);
        var cancel = el("button", { type: "button", "class": "btn" }, [M.icon("x", 15), "Cancel"]);
        cancel.addEventListener("click", function () { gallery(); });
        M.setHeader({ title: ed.reportId ? "Edit report" : "New report", sub: ed.reportId ? ed.name : "A definition over the Vertical Inventory (Article) slice: Group by, measures, filters and units.",
          crumbs: [{ label: "Inventory Reports", href: "/mis/inv-reports" }, { label: ed.reportId ? "Edit " + ed.name : "New report" }], actions: [cancel],
          tiles: [{ k: "Latest stock", v: M.fmtDate(cat.asOf), s: "preview runs on this date" }, { k: "Group by", v: d.group_by.length }, { k: "Measures", v: d.measures.length }] });
        function liveTiles() { M.setHeader({ tiles: [{ k: "Latest stock", v: M.fmtDate(cat.asOf), s: "preview runs on this date" }, { k: "Group by", v: d.group_by.length, s: d.group_by.map(dimLabel).join(" × ") },
          { k: "Measures", v: d.measures.length, s: d.measures.map(measLabel).join(", ") }, { k: "Grid columns", v: (d.grid || []).length }] }); }

        // name / url / description
        var name = el("input", { type: "text", value: ed.name || "", placeholder: "e.g. Store × Maj-Cat closing stock", maxlength: "200", style: { minWidth: "280px" } });
        var slug = el("input", { type: "text", value: ed.slug || "", placeholder: "store-majcat-stock", maxlength: "61", style: { fontFamily: "monospace" } });
        var desc = el("input", { type: "text", value: ed.description || "", placeholder: "What this report answers", maxlength: "1000", style: { minWidth: "360px", flex: "1" } });
        ctx.main.appendChild(el("div", { "class": "card" }, el("div", { "class": "fbar" }, [el("label", { "class": "f" }, ["Report name", name]),
          el("label", { "class": "f" }, ["URL (optional - from the name if blank)", slug]), el("label", { "class": "f", style: { flex: "1" } }, ["Description", desc])])));

        // Group by / Measures / Grid / Art type
        var gb = pickList("Group by", dimGroups, d.group_by, dimLabel, function (v) { d.group_by = v; gridPick.setGroups(gridGroups()); gridPick.set(gridVal()); liveTiles(); },
          { ordered: true, note: "Columns appear in the order you pick them; stock is summed over everything not picked." });
        var ms = pickList("Measures", [["", measures]], d.measures, measLabel, function (v) { d.measures = measures.filter(function (m) { return v.indexOf(m) >= 0; }); liveTiles(); });
        function gridGroups() { return dimGroups.map(function (g) { return [g[0], g[1].filter(function (c) { return d.group_by.indexOf(c) < 0; })]; }).filter(function (g) { return g[1].length; }); }
        function gridVal() { return (d.grid || []).filter(function (c) { return d.group_by.indexOf(c) < 0; }); }
        var gridPick = pickList("Grid (child rows)", gridGroups(), gridVal(), dimLabel, function (v) {
          if (v.length > 10) { M.toast("At most 10 grid columns.", "bad"); v = v.slice(0, 10); gridPick.set(v); }
          d.grid = v; liveTiles();
        }, { ordered: true, note: "Optional. Each column picked here becomes a block of rows under every Group-by row, with a share % per measure." });
        var art = pickList("Art type", [["", (cat.options || {}).ART_TYPE || []]], d.filters.ART_TYPE || [], function (v) { return v; }, function (v) { setFilter("ART_TYPE", v); },
          { note: "Narrows the rows. Picks nothing = every article type, and no column either way (tick ART_TYPE under Group by for a column)." });
        ctx.main.appendChild(el("div", { "class": "grid12" }, [el("div", { "class": "c3" }, gb.el), el("div", { "class": "c3" }, ms.el), el("div", { "class": "c3" }, gridPick.el), el("div", { "class": "c3" }, art.el)]));
        function setFilter(c, v) { if (v && v.length) d.filters[c] = v; else delete d.filters[c]; }

        // Filters - a value list per column, saved with the report
        var fRow = el("div", { "class": "row", style: { alignItems: "flex-end", gap: "10px 14px" } });
        var fCard = el("div", { "class": "card" }, [el("div", { "class": "card-h" }, [el("h2", { text: "Filters" }),
          el("span", { "class": "note", text: "A filter narrows the report for everyone who opens, exports or schedules it." })]), el("div", { "class": "card-b" }, fRow)]);
        ctx.main.appendChild(fCard);
        function drawFilters() {
          M.clear(fRow);
          filterCols.forEach(function (c) {
            var m = M.multiSelect(c, function () { return Promise.resolve((cat.options || {})[c] || []); }, d.filters[c] || [], function (v) { setFilter(c, v); });
            var x = el("button", { type: "button", "class": "btn ghost sm", "aria-label": "Remove the " + c + " filter", title: "Remove the " + c + " filter" }, [M.icon("x", 13)]);
            x.addEventListener("click", function () { setFilter(c, []); filterCols = filterCols.filter(function (y) { return y !== c; }); drawFilters(); });
            fRow.appendChild(el("div", { "class": "row", style: { gap: "2px", alignItems: "flex-end" } }, [m.el, x]));
          });
          var free = (cat.filterCols || []).filter(function (c) { return c !== "ART_TYPE" && filterCols.indexOf(c) < 0; });
          if (free.length) {
            var add = el("select", { "aria-label": "Add a filter" }, [el("option", { value: "", text: "+ add a filter" })].concat(free.map(function (c) { return el("option", { value: c, text: c }); })));
            add.addEventListener("change", function () { if (add.value) { filterCols.push(add.value); drawFilters(); } });
            fRow.appendChild(el("label", { "class": "f" }, ["Add", add]));
          }
          if (!filterCols.length) fRow.appendChild(el("span", { "class": "muted small", text: "None - the report returns every row." }));
        }
        drawFilters();

        // Conditions - any dimension (grouped or not) or a measure's total
        var condCols = allDims.concat(measures);
        var ce = M.condEditor(condCols, condRows, function (v) { condRows = v; });
        ctx.main.appendChild(el("div", { "class": "card" }, [el("div", { "class": "card-h" }, [el("h2", { text: "Conditions" }),
          el("span", { "class": "note", text: "Kept with the report - applied on every open, export and scheduled dump. A condition may name any column (grouped or not), or a measure's total such as CL-STK-Q greater than 0 (in pieces / rupees)." })]),
          el("div", { "class": "card-b" }, ce.el)]));

        // units / sharing / preview / save
        var units = el("select", { "aria-label": "Units" }, [el("option", { value: "kl", selected: d.units === "kl", text: "K · Lakh (thousand pcs / Rs lakh)" }), el("option", { value: "raw", selected: d.units === "raw", text: "Raw (pcs / Rs)" })]);
        units.addEventListener("change", function () { d.units = units.value; });
        var shared = el("input", { type: "checkbox", checked: !!ed.isShared }), side = el("input", { type: "checkbox", checked: ed.showInSidebar !== false });
        var prevBtn = el("button", { type: "button", "class": "btn", title: "Run on the latest date (" + cat.asOf + "), first 50 rows" }, [M.icon("play", 15), "Preview"]);
        var saveBtn = el("button", { type: "button", "class": "btn primary" }, [M.icon("save", 15), "Save"]);
        var prevBox = el("div");
        ctx.main.appendChild(el("div", { "class": "card" }, el("div", { "class": "fbar", style: { borderBottom: "0", borderRadius: "var(--radius)" } }, [
          el("label", { "class": "f" }, ["Units", units]),
          el("label", { "class": "row small", style: { gap: "6px" } }, [shared, "Share with everyone ", el("span", { "class": "muted", text: "(otherwise only you see it)" })]),
          el("label", { "class": "row small", style: { gap: "6px" } }, [side, "Show in sidebar ", el("span", { "class": "muted", text: "(off keeps it on this page only)" })]),
          el("span", { "class": "spacer" }), prevBtn, el("button", { type: "button", "class": "btn", text: "Cancel", onclick: function () { gallery(); } }), saveBtn])));
        ctx.main.appendChild(prevBox);
        if (!me.can.manage) { prevBtn.disabled = saveBtn.disabled = true; saveBtn.title = prevBtn.title = "You need MIS manage rights"; }

        function clean(rows) {
          var dimC = [], measC = [];
          rows.forEach(function (r) {
            if (!r.col || !r.op) return;
            var o = { col: r.col, op: r.op };
            if (r.op === "in" || r.op === "not_in") { if (!(r.vals || []).length) return; o.vals = r.vals; }
            else if (r.op === "between") { if (String(r.v1 == null ? "" : r.v1) === "" || String(r.v2 == null ? "" : r.v2) === "") return; o.v1 = r.v1; o.v2 = r.v2; }
            else if (r.op !== "blank" && r.op !== "nonblank") { if (String(r.v1 == null ? "" : r.v1) === "") return; o.v1 = r.v1; }
            (measures.indexOf(r.col) >= 0 ? measC : dimC).push(o);
          });
          return { dim_filters: dimC, col_filters: measC };
        }
        function outDef() {
          var c = clean(ce.get()), o = { group_by: d.group_by, measures: d.measures, filters: d.filters, units: d.units, dim_filters: c.dim_filters, col_filters: c.col_filters };
          var g = gridVal(); if (g.length) o.grid = g;
          return o;
        }
        function check() {
          if (!d.group_by.length) throw new Error("Pick at least one Group by column.");
          if (!d.measures.length) throw new Error("Pick at least one measure.");
        }

        prevBtn.addEventListener("click", function () {
          try { check(); } catch (e) { M.toast(e.message, "bad"); return; }
          var def = outDef();
          M.clear(prevBox);
          var pc = el("div", { "class": "card" }); prevBox.appendChild(pc);
          var isM = function (c) { return measures.indexOf(c) >= 0; };
          var pg = M.Grid(pc, { title: "Preview · as of " + cat.asOf, limit: 50, pageSizes: [50], filterable: false,
            isKey: function (c) { return !isM(c) && !/_PCT$/.test(c) && c !== "MAP"; },
            format: function (c) { return isM(c) ? "dec" : /_PCT$/.test(c) ? "pct" : c === "MAP" ? "dec" : "text"; },
            label: function (c) { return labels[c] || dimLabel(c); },
            fetch: function (gs) {
              return M.post(API + "/run", { definition: def, page: gs.page, limit: 50, sort_col: gs.sort_col, sort_dir: gs.sort_dir }).then(function (r) {
                if (ctx.alive()) pg.setTitle("Preview · as of " + (r.asOf || cat.asOf));
                return { columns: r.columns, rows: r.rows, total: r.total };
              });
            },
            cell: function (td, c, v) {
              if (isM(c)) { if (v === null || v === undefined || v === "") { td.textContent = "—"; return; } var n = Number(v);
                td.textContent = d.units === "kl" ? fixed(n / (/_Q$/.test(c) ? 1000 : 100000), 3) : fixed(n, 0); }
              else if (/_PCT$/.test(c)) td.textContent = v == null || v === "" ? "—" : fixed(v, 2) + "%";
              else if (c === "MAP") td.textContent = v == null || v === "" ? "—" : fixed(v, 2);
            }
          });
          M.action(prevBtn, "Running…", function () { return pg.reload(true); });
          pc.scrollIntoView({ behavior: "smooth", block: "start" });
        });

        saveBtn.addEventListener("click", function () {
          if (!name.value.trim()) { M.toast("Enter a report name.", "bad"); name.focus(); return; }
          try { check(); } catch (e) { M.toast(e.message, "bad"); return; }
          var req = { report_id: ed.reportId || null, name: name.value.trim(), slug: slug.value.trim() || null, description: desc.value.trim(),
                      definition: outDef(), is_shared: shared.checked, show_in_sidebar: side.checked };
          M.action(saveBtn, "Saving…", function () { return M.post(API + "/reports", req); }).then(function (saved) {
            var vs = saved && saved.viewSync;
            if (vs && !vs.ok) M.toast("Saved, but the Snowflake view " + vs.view + " could not be published: " + vs.error, "bad");
            else M.toast(vs && vs.view ? "Saved - published as " + vs.view : "Saved.", "ok");
            return reload().then(function () { if (ctx.alive()) gallery(); });
          }, function () { /* toasted */ });
        });
        liveTiles();
        name.focus();
      }
    }
  });

  // ── a saved report, opened: the Vertical Inventory (Article) grid locked to its definition ─────────────
  M.page("inv-report", {
    render: function (ctx) {
      var slug = ctx.route.slug;
      M.setHeader({ title: slug, crumbs: [{ label: "Inventory Reports", href: "/mis/inv-reports" }, { label: slug }] });
      var boot = el("div", { "class": "card" }, el("div", { "class": "card-b muted" }, [el("span", { "class": "spin" }), " Loading the report…"]));
      ctx.main.appendChild(boot);
      // As DFS: the report is found in the caller's own list (shared + their own), so the visibility rule holds.
      return Promise.all([M.get(API + "/reports"), needVertInv()]).then(function (x) {
        if (!ctx.alive()) return;
        var rep = (x[0].reports || []).filter(function (r) { return r.slug === slug; })[0];
        if (!rep) {
          M.clear(boot).appendChild(el("div", { "class": "card-b stack" }, [el("div", { "class": "notice bad", text: "There is no inventory report “" + slug + "” that you can see." }),
            el("div", {}, el("a", { "class": "btn", href: "/mis/inv-reports" }, "Back to Inventory Reports"))]));
          return;
        }
        boot.remove();
        return x[1].mount(ctx, { report: rep });
      }, function (e) {
        if (!ctx.alive()) return;
        M.clear(boot).appendChild(el("div", { "class": "card-b" }, el("div", { "class": "notice bad", text: e.message })));
      });
    }
  });
})();

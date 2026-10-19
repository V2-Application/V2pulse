// Dependencies (DFS /sf-mis-lineage, SfMisLineage.jsx): every Snowflake table, view and procedure behind the MIS
// numbers, grouped by pipeline and ordered the way data moves (SOURCE -> LOADER -> FACT -> what reads it), with live
// rows, size, "data through" and "last loaded" from Snowflake metadata. Missing objects and orphan report views are
// called out; MIS admins can drop the orphan views. API: /api/v1/mis/lineage/{objects, export, drop-orphans}.
(function () {
  "use strict";
  var M = window.MIS, el = M.el;
  var API = "/api/v1/mis/lineage";
  var STAGE_ORDER = ["SOURCE", "LOADER", "FACT", "REGISTRY", "MASTER", "DEFINITION", "RUN LOG", "PUBLISHED VIEW"];
  var PIPE_COLOR = { SALE: "var(--s2)", STOCK: "var(--s4)", MASTER: "var(--s8)", REPORTS: "var(--s1)" };
  var PIPE_NOTE = {
    SALE: "VBRP is the only source with full history - which is why a stored KPI anchor can be right on a day the day-wise fact has never been loaded for.",
    STOCK: "Several separate sources feed one fact, and their tag families OVERLAP. Never total across the ET LGORT tags, the LQUA LQ_/LQL_ tags and LQB_ - they describe the same stock.",
    MASTER: "Joined LEFT and live, so a store or article renamed today changes the labels on a past-dated report. The measures are fixed by the fact rows; the descriptions are not.",
    REPORTS: "Nothing is materialised per report - a definition is a few hundred bytes of JSON. The SILVER views are generated from those definitions and replaced on every save."
  };
  var TYPE_ICON = { TABLE: "master", VIEW: "search", PROCEDURE: "gear" };

  // A filter-bar caption over a button group: a click on the caption text must not "click" the first button.
  function fl(caption, ctrl) { var l = el("label", { "class": "f" }, [caption, ctrl]); l.addEventListener("click", function (e) { if (e.target === l) e.preventDefault(); }); return l; }

  function fmtBytes(b) {
    if (b === null || b === undefined) return "–";
    if (b < 1048576) return (b / 1024).toFixed(0) + " KB";
    if (b < 1073741824) return (b / 1048576).toFixed(0) + " MB";
    return (b / 1073741824).toFixed(1) + " GB";
  }
  function fmtWhen(s) {
    if (!s) return "–"; var d = new Date(s);
    return isNaN(d.getTime()) ? String(s).slice(0, 16) : d.toLocaleString("en-IN", { day: "2-digit", month: "short", year: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  M.page("lineage", {
    render: function (ctx) {
      var S = { data: null, q: "", pipe: "", status: "" }, admin = !!ctx.me.can.admin;
      var refreshBtn = el("button", { type: "button", "class": "btn" }, [M.icon("refresh", 15), "Refresh"]);
      var csvBtn = ctx.me.can.export ? el("button", { type: "button", "class": "btn" }, [M.icon("down", 15), "Export CSV"]) : null;
      var dropBtn = admin ? el("button", { type: "button", "class": "btn hidden" }, [M.icon("trash", 15), "Drop orphan views"]) : null;
      M.setHeader({ title: "MIS Dependencies",
        sub: "Every table, view and procedure behind the MIS numbers, in the order data moves through them - source → loader → fact → what reads it. Data through is the newest day each table holds; Last loaded is when that was written. Both come from Snowflake's own metadata, so this page is free to open even though one source holds 2.5 billion rows.",
        actions: [refreshBtn, csvBtn, dropBtn],
        tiles: [{ k: "Objects", skel: true }, { k: "Missing", skel: true }, { k: "Orphan views", skel: true }, { k: "Sale data through", skel: true }, { k: "Stock data through", skel: true }] });

      // filter bar
      var search = el("input", { type: "search", placeholder: "Search name, schema or note…", "aria-label": "Search objects", style: { minWidth: "300px" } });
      search.addEventListener("input", M.debounce(function () { S.q = search.value.trim().toLowerCase(); draw(); }, 200));
      var pipeSeg = el("div", { "class": "seg", role: "group", "aria-label": "Pipeline" });
      var statusSel = el("select", { "aria-label": "Status" }, [["", "Any status"], ["OK", "OK"], ["MISSING", "Missing"], ["ORPHAN", "Orphan"], ["UNMANAGED", "Not ours (unmanaged)"]].map(function (o) { return el("option", { value: o[0], text: o[1] }); }));
      statusSel.addEventListener("change", function () { S.status = statusSel.value; draw(); });
      var countNote = el("span", { "class": "muted small" });
      ctx.main.appendChild(el("div", { "class": "card" }, el("div", { "class": "fbar", style: { borderBottom: "0", borderRadius: "var(--radius)" } }, [
        el("label", { "class": "f" }, ["Search", search]), fl("Pipeline", pipeSeg), el("label", { "class": "f" }, ["Status", statusSel]),
        el("span", { "class": "spacer" }), countNote])));
      var alerts = el("div", { "class": "stack" }), body = el("div", { "class": "stack", style: { gap: "16px" } });
      ctx.main.appendChild(alerts); ctx.main.appendChild(body);

      refreshBtn.addEventListener("click", function () { M.action(refreshBtn, "Reading…", load).catch(function () { /* toasted */ }); });
      if (csvBtn) csvBtn.addEventListener("click", function () {
        M.action(csvBtn, "Exporting…", function () { return M.download("GET", API + "/export", undefined, "MIS_DEPENDENCIES.csv"); }, function (n) { return "Downloaded " + n; }).catch(function () { /* toasted */ });
      });
      if (dropBtn) dropBtn.addEventListener("click", function () { dropOrphans(dropBtn); });

      function load() {
        M.clear(body).appendChild(el("div", { "class": "card" }, el("div", { "class": "card-b muted", html: '<span class="spin"></span> Reading Snowflake metadata…' })));
        return M.get(API + "/objects").then(function (d) {
          if (!ctx.alive()) return;
          S.data = d; drawPipes(); drawHeader(); draw();
        }, function (e) {
          if (!ctx.alive()) return;
          S.data = null; M.clear(alerts); M.clear(body).appendChild(el("div", { "class": "notice bad", text: e.message }));
          M.setHeader({ tiles: [] }); throw e;
        });
      }

      function drawPipes() {
        M.clear(pipeSeg);
        [""].concat(S.data.pipes || []).forEach(function (p) {
          var b = el("button", { type: "button", "aria-pressed": String(S.pipe === p), text: p ? p.charAt(0) + p.slice(1).toLowerCase() : "All" });
          b.addEventListener("click", function () { S.pipe = p; pipeSeg.querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); }); draw(); });
          pipeSeg.appendChild(b);
        });
      }

      function drawHeader() {
        var d = S.data, c = d.counts || {}, rows = d.rows || [];
        function thru(name) { var r = rows.filter(function (x) { return x.name === name; })[0]; return r; }
        var sale = thru("DAY_WISE_ST_VAR_SALE"), stock = thru("DAY_WISE_ST_VAR_STOCK");
        var unmanaged = rows.filter(function (r) { return r.status === "UNMANAGED"; }).length;
        var views = rows.filter(function (r) { return r.stage === "PUBLISHED VIEW"; }).length;
        M.setHeader({ tiles: [
          { k: "Objects", v: M.fmt(c.total, "int"), s: (d.pipes || []).length + " pipelines · " + views + " published view(s)" },
          { k: "Missing", v: M.fmt(c.missing, "int"), s: c.missing ? "referenced but not in Snowflake" : "all objects present", cls: c.missing ? "down" : "up" },
          { k: "Orphan views", v: M.fmt(c.orphans, "int"), s: c.orphans ? "outlived their report" : "none" + (unmanaged ? " · " + unmanaged + " not ours" : ""), cls: c.orphans ? "down" : "up" },
          { k: "Sale data through", v: sale && sale.dataThrough ? M.fmtDate(String(sale.dataThrough).slice(0, 10), false) : "–", s: sale && sale.lastLoaded ? "loaded " + fmtWhen(sale.lastLoaded) : "" },
          { k: "Stock data through", v: stock && stock.dataThrough ? M.fmtDate(String(stock.dataThrough).slice(0, 10), false) : "–", s: stock && stock.lastLoaded ? "loaded " + fmtWhen(stock.lastLoaded) : "" }] });
        if (dropBtn) dropBtn.classList.toggle("hidden", !c.orphans);
      }

      function draw() {
        if (!S.data) return;
        var d = S.data, all = d.rows || [], missing = d.missing || [], orphans = d.orphans || [];
        // alerts: a missing object must be loud; an orphan view is the only place a failed drop-on-delete surfaces
        M.clear(alerts);
        if (missing.length) alerts.appendChild(el("div", { "class": "notice bad" }, [M.icon("info", 16), el("div", {}, [
          el("b", { text: "These objects are referenced by the MIS module but do not exist:" }),
          el("ul", { style: { margin: "4px 0 0", paddingLeft: "18px", fontFamily: "monospace", fontSize: "12px" } }, missing.map(function (m) { return el("li", { text: m }); }))])]));
        if (orphans.length) {
          var kids = [el("b", { text: orphans.length + " view(s) outlived the report that made them:" }),
            el("ul", { style: { margin: "4px 0 6px", paddingLeft: "18px", fontFamily: "monospace", fontSize: "12px" } }, orphans.map(function (o) { return el("li", { text: o }); }))];
          if (admin) { var b = el("button", { type: "button", "class": "btn sm danger" }, [M.icon("trash", 14), "Drop orphan view(s)"]); b.addEventListener("click", function () { dropOrphans(b); }); kids.push(b); }
          else kids.push(el("span", { "class": "small", text: "Dropping them needs the mis.admin permission." }));
          alerts.appendChild(el("div", { "class": "notice warn" }, [M.icon("info", 16), el("div", {}, kids)]));
        }

        var rows = all.filter(function (r) {
          if (S.pipe && r.pipe !== S.pipe) return false;
          if (S.status && r.status !== S.status) return false;
          // searches the NOTE too: the notes carry the traps, so "overlap" finds the object they describe
          return !S.q || (r.pipe + " " + r.stage + " " + r.schema + " " + r.name + " " + (r.note || "")).toLowerCase().indexOf(S.q) >= 0;
        });
        countNote.textContent = rows.length === all.length ? M.fmt(all.length, "int") + " objects" : M.fmt(rows.length, "int") + " of " + M.fmt(all.length, "int") + " objects";
        var by = {};
        rows.forEach(function (r) { (by[r.pipe] = by[r.pipe] || []).push(r); });
        Object.keys(by).forEach(function (k) { by[k].sort(function (a, b) { return (STAGE_ORDER.indexOf(a.stage) - STAGE_ORDER.indexOf(b.stage)) || a.name.localeCompare(b.name); }); });
        M.clear(body);
        var pipes = (d.pipes || []).filter(function (p) { return by[p] && by[p].length; });
        Object.keys(by).forEach(function (p) { if (pipes.indexOf(p) < 0) pipes.push(p); });
        if (!pipes.length) { body.appendChild(el("div", { "class": "card" }, el("div", { "class": "dg-empty" }, [M.icon("search", 28), el("div", { text: S.q ? "Nothing matches “" + search.value.trim() + "”." : "No objects match." })]))); return; }
        pipes.forEach(function (p) { body.appendChild(pipeCard(p, by[p])); });
      }

      function pipeCard(pipe, rows) {
        var color = PIPE_COLOR[pipe] || "var(--s6)";
        // the chain: each stage present, in pipeline order, with its object count
        var stages = STAGE_ORDER.filter(function (s) { return rows.some(function (r) { return r.stage === s; }); });
        var chain = el("div", { "class": "row", style: { gap: "6px" } });
        stages.forEach(function (s, i) {
          if (i) chain.appendChild(el("span", { "class": "muted", "aria-hidden": "true", text: "→" }));
          chain.appendChild(el("span", { "class": "chip grey", text: s + " · " + rows.filter(function (r) { return r.stage === s; }).length }));
        });
        var bad = rows.filter(function (r) { return r.status === "MISSING" || r.status === "ORPHAN"; }).length;
        var tb = el("tbody");
        rows.forEach(function (r) {
          var badges = [];
          if (r.status === "MISSING") badges.push(el("span", { "class": "chip bad", text: "missing" }));
          if (r.status === "ORPHAN") badges.push(el("span", { "class": "chip warn", text: "orphan" }));
          if (r.status === "UNMANAGED") badges.push(el("span", { "class": "chip grey", title: "Shares the VW_MIS_ prefix but was not published by the report builder; the orphan cleanup never touches it.", text: "not ours" }));
          var icon = M.icon(TYPE_ICON[r.type] || "master", 13); icon.style.color = "var(--muted)"; icon.style.flex = "none";
          tb.appendChild(el("tr", {}, [
            el("td", { style: { verticalAlign: "top" } }, el("span", { "class": "badge", style: { background: "color-mix(in srgb, " + color + " 14%, transparent)", color: color }, text: r.stage })),
            el("td", { style: { verticalAlign: "top", whiteSpace: "normal" } }, [
              el("div", { "class": "row", style: { gap: "6px", flexWrap: "nowrap" } }, [icon, el("span", { title: r.type || "", style: { fontFamily: "monospace", fontWeight: "600" }, text: r.name })].concat(badges)),
              el("div", { "class": "tiny muted", style: { fontFamily: "monospace", userSelect: "all" }, text: r.fqn })]),
            el("td", { "class": "num", style: { verticalAlign: "top" }, text: r.rows == null ? "–" : M.fmt(r.rows, "int") }),
            el("td", { "class": "num muted", style: { verticalAlign: "top" }, text: fmtBytes(r.bytes) }),
            el("td", { style: { verticalAlign: "top", fontWeight: r.dataThrough ? "600" : null },
                       title: r.dataThrough ? null : "No native date column to read - its date is stored as TEXT (or it has none), so a MAX would be a string comparison over hundreds of millions of rows.",
                       text: r.dataThrough ? String(r.dataThrough).slice(0, 10) : "–" }),
            el("td", { "class": "muted", style: { verticalAlign: "top" }, text: fmtWhen(r.lastLoaded) }),
            el("td", { "class": "muted", style: { verticalAlign: "top" }, title: "Last altered (object metadata)", text: fmtWhen(r.lastAltered) }),
            el("td", { style: { verticalAlign: "top", whiteSpace: "normal", minWidth: "320px", color: "var(--fg-2)" }, text: r.note || "" })]));
        });
        var heads = [["Stage"], ["Object"], ["Rows", 1], ["Size", 1], ["Data through"], ["Last loaded"], ["Altered"], ["What it is"]];
        return el("div", { "class": "card", style: { borderLeft: "4px solid " + color } }, [
          el("div", { "class": "card-h" }, [el("h2", { text: pipe }), el("span", { "class": "note", text: rows.length + " object(s)" }),
            bad ? el("span", { "class": "chip bad", text: bad + " need attention" }) : null, el("span", { "class": "spacer" }), chain]),
          el("div", { "class": "card-b muted small", style: { paddingBottom: "8px" }, text: PIPE_NOTE[pipe] || "" }),
          el("div", { "class": "dg-wrap", style: { maxHeight: "none", minHeight: "0" } }, el("table", { "class": "dg-t", style: { width: "100%", tableLayout: "auto" } }, [
            el("thead", {}, el("tr", { "class": "cols" }, heads.map(function (h) { return el("th", { "class": h[1] ? "num" : null, style: h[1] ? { textAlign: "right" } : null, text: h[0] }); }))), tb]))]);
      }

      function dropOrphans(btn) {
        var n = ((S.data || {}).orphans || []).length;
        if (!admin || !n) return;
        M.confirm("Drop orphan views", "Drop " + n + " orphan view(s) from Snowflake? Only VW_MIS_* views in SILVER whose report no longer exists are dropped (the list is re-checked on the server). Views belonging to a live report, and views that merely share the prefix, cannot be touched.", "Drop " + n + " view(s)", true)
          .then(function (yes) {
            if (!yes) return;
            M.action(btn, "Dropping…", function () { return M.post(API + "/drop-orphans", {}); }, function (r) { return (r && r.message) || "Done."; }).then(function (r) {
              if (r && r.failed && r.failed.length) M.toast(r.failed.length + " could not be dropped: " + r.failed.map(function (f) { return f.view + " (" + f.error + ")"; }).join("; "), "bad");
              if (ctx.alive()) load().catch(function () { /* shown */ });
            }, function () { /* toasted */ });
          });
      }

      return load().catch(function () { /* shown in the page */ });
    }
  });
})();

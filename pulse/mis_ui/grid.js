// MIS enterprise data grid: server paging + sorting, Excel-style header filters (value lists + conditions), frozen
// columns, drag-resize, column chooser (show / hide / order / freeze), density, totals row, export menu, saved views
// (per user, in V2Pulse) and an auto-saved local layout.
//
//   var g = MIS.Grid(container, {
//     id: "product-master",                    // saved-views key (optional)
//     title: "Articles",
//     fetch: function (st) -> Promise<{columns, rows, total, totals?}>   st = {page, limit, sort_col, sort_dir, filters, conds}
//     values: function (col, st) -> Promise<[v | {value,count}]>         header filter value lists (optional)
//     isKey: function (col) -> bool             text / dimension columns (left aligned, frozen by default)
//     format: function (col) -> "int"|"dec"|"pct"|"text"|undefined
//     label: function (col) -> string
//     groups: function (cols) -> [{label, span}] a header row above the columns (optional)
//     exports: [{label, fn: function (st) {...}}], tools: [nodes], limit: 200, pageSizes: [...], filterable: true,
//     conditions: false (header filter = value list only), values: false (condition only), sortable: false
//   });
//   g.reload(), g.state(), g.setState({...}), g.columns()
(function () {
  "use strict";
  var M = window.MIS, el = M.el;

  M.Grid = function (container, opts) {
    opts = opts || {};
    var st = { page: 1, limit: opts.limit || 200, sort_col: opts.sort_col || null, sort_dir: opts.sort_dir || "asc", filters: {}, conds: [] };
    var layout = M.store.get("grid." + (opts.id || "x"), {}) || {};       // {order, hidden, widths, pin, density}
    var data = { columns: [], rows: [], total: 0 }, seq = 0, ctrl = null, api = {};
    var root = el("div", { "class": "dg" + (layout.density === "compact" ? " compact" : "") + (opts.tall ? " tall" : "") });
    var meta = el("span", { "class": "meta" }), chips = el("div", { "class": "fchips" });
    var wrap = el("div", { "class": "dg-wrap" }), pager = el("div", { "class": "dg-pager" });
    var titleEl = el("span", { "class": "title", text: opts.title || "" });
    var bar = el("div", { "class": "dg-bar" }, [titleEl, meta, el("span", { "class": "sp" })].concat(opts.tools || []));
    if (opts.id) bar.appendChild(btn("save", "Views", viewsMenu));
    bar.appendChild(btn("cols", "Columns", colsPanel));
    bar.appendChild(btn("density", null, function () {
      layout.density = layout.density === "compact" ? "" : "compact"; root.classList.toggle("compact", layout.density === "compact"); saveLayout();
    }, "Compact / comfortable rows"));
    if (opts.exports && opts.exports.length) bar.appendChild(btn("down", "Export", function (b) {
      M.menu(b, opts.exports.map(function (x) { return { label: x.label, icon: "down", fn: function () { x.fn(api.state(), visibleCols()); } }; }), { alignRight: true });
    }));
    bar.appendChild(btn("refresh", null, function () { api.reload(); }, "Refresh"));
    root.appendChild(bar); root.appendChild(chips); root.appendChild(wrap); root.appendChild(pager);
    container.appendChild(root);

    function btn(icon, label, fn, title) {
      var b = el("button", { type: "button", "class": "btn sm" + (label ? "" : " ghost"), title: title || label, "aria-label": title || label }, [M.icon(icon, 15), label]);
      b.addEventListener("click", function () { fn(b); }); return b;
    }
    function saveLayout() { if (opts.id) M.store.set("grid." + opts.id, layout); }
    function isKey(c) { return opts.isKey ? opts.isKey(c) : false; }
    function fmtOf(c) { return opts.format ? opts.format(c) : undefined; }
    function lbl(c) { return opts.label ? opts.label(c) : M.label(c); }

    // column order / visibility from the layout, new columns appended
    function visibleCols() {
      var cols = data.columns.slice(), order = (layout.order || []).filter(function (c) { return cols.indexOf(c) >= 0; });
      cols.forEach(function (c) { if (order.indexOf(c) < 0) order.push(c); });
      var hid = layout.hidden || [];
      return order.filter(function (c) { return hid.indexOf(c) < 0; });
    }
    function pinCount(cols) {
      if (layout.pin != null) return Math.min(layout.pin, cols.length);
      var n = 0; while (n < cols.length && n < 4 && isKey(cols[n])) n++; return n;
    }
    function width(c, i) {
      if (layout.widths && layout.widths[c]) return layout.widths[c];
      var len = String(lbl(c)).length, rows = data.rows.slice(0, 40), ci = data.columns.indexOf(c);
      rows.forEach(function (r) { var v = Array.isArray(r) ? r[ci] : r[c]; var s = v == null ? "" : (M.isNum(v) && !isKey(c) ? M.fmt(v, fmtOf(c)) : String(v)); if (s.length > len) len = s.length; });
      return Math.max(64, Math.min(340, Math.round(len * 7.4 + 34)));
    }

    // ── fetch + render ───────────────────────────────────────────────────────────────────────────
    api.reload = function (resetPage) {
      if (resetPage) st.page = 1;
      var my = ++seq; if (ctrl) ctrl.abort(); ctrl = window.AbortController ? new AbortController() : null;
      var ov = el("div", { "class": "dg-load" }, [el("span", { "class": "spin" }), "Loading…"]);
      if (!data.columns.length) { M.clear(wrap); wrap.appendChild(skeleton()); } else wrap.appendChild(ov);
      drawChips();
      return Promise.resolve(opts.fetch(api.state(), ctrl && ctrl.signal)).then(function (d) {
        if (my !== seq) return;
        data = { columns: (d.columns || []).filter(function (c) { return c !== "_ORD"; }), rawColumns: d.columns || [], rows: d.rows || [], total: d.total == null ? (d.rows || []).length : d.total,
                 totals: d.totals || null, extra: d };
        draw(); if (opts.onData) opts.onData(d, api.state());
      }, function (e) {
        if (my !== seq || (e && e.name === "AbortError")) return;
        M.clear(wrap).appendChild(el("div", { "class": "dg-empty" }, [el("div", { "class": "notice bad", style: { display: "inline-flex" }, text: e.message })]));
        meta.textContent = ""; M.clear(pager);
      });
    };
    function skeleton() {
      var t = el("div", { style: { padding: "14px" } });
      for (var i = 0; i < 8; i++) t.appendChild(el("div", { "class": "skel", style: { height: "18px", margin: "8px 0", width: (60 + (i * 37) % 40) + "%" } }));
      return t;
    }
    function cellVal(r, c) { var ci = data.rawColumns.indexOf(c); return Array.isArray(r) ? r[ci] : r[c]; }

    function draw() {
      var cols = visibleCols(), pin = pinCount(cols), left = 0, offs = [];
      M.clear(wrap);
      meta.textContent = M.fmt(data.total, "int") + " rows";
      if (!data.columns.length || !data.rows.length) {
        wrap.appendChild(el("div", { "class": "dg-empty" }, [M.icon("search", 28), el("div", { style: { marginTop: "6px" }, text: data.columns.length ? "No rows match." : "No data." })]));
        drawPager(); return;
      }
      var t = el("table", { "class": "dg-t" }), cg = el("colgroup");
      cols.forEach(function (c, i) { var w = width(c, i); offs.push(left); left += w; cg.appendChild(el("col", { style: { width: w + "px" } })); });
      t.appendChild(cg);
      var thead = el("thead");
      var groups = opts.groups ? opts.groups(cols) : null;
      if (groups && groups.length) {
        var gr = el("tr", { "class": "grp" }), at = 0;
        groups.forEach(function (g) {
          var th = el("th", { colspan: g.span, text: g.label || "" });
          if (at < pin) { th.className = "pin" + (at + g.span >= pin ? " last" : ""); th.style.left = offs[at] + "px"; }
          gr.appendChild(th); at += g.span;
        });
        thead.appendChild(gr);
      }
      var hr = el("tr", { "class": "cols" });
      cols.forEach(function (c, i) {
        var num = !isKey(c) && isNumCol(c);
        var sortOn = st.sort_col === c, fon = !!(st.filters[c] && st.filters[c].length) || st.conds.some(function (x) { return x.col === c; });
        var lab = el("span", { "class": "lbl", text: lbl(c), title: "Sort by " + lbl(c) });
        lab.addEventListener("click", function () { if (opts.sortable === false) return; st.sort_dir = st.sort_col === c && st.sort_dir === "asc" ? "desc" : "asc"; st.sort_col = c; api.reload(true); });
        var fb = opts.filterable === false ? null : el("button", { type: "button", "class": "fb" + (fon ? " on" : ""), "aria-label": "Filter " + lbl(c) }, [M.icon("filter", 13)]);
        if (fb) fb.addEventListener("click", function (e) { e.stopPropagation(); filterPop(fb, c, num); });
        var rz = el("span", { "class": "rz", "aria-hidden": "true" });
        rz.addEventListener("mousedown", function (e) { startResize(e, c, th); });
        var th = el("th", { "class": (num ? "num " : "") + (i < pin ? "pin" + (i === pin - 1 ? " last" : "") : ""), title: lbl(c), "aria-sort": sortOn ? (st.sort_dir === "desc" ? "descending" : "ascending") : null },
          [el("div", { "class": "h" }, [lab, sortOn ? el("span", { "class": "srt", text: st.sort_dir === "desc" ? "▼" : "▲" }) : null, fb]), rz]);
        if (i < pin) th.style.left = offs[i] + "px";
        hr.appendChild(th);
      });
      thead.appendChild(hr); t.appendChild(thead);
      var tb = el("tbody");
      data.rows.forEach(function (r) {
        var tr = el("tr");
        cols.forEach(function (c, i) {
          var v = cellVal(r, c), num = !isKey(c) && M.isNum(v) && fmtOf(c) !== "text";
          var txt = num ? M.fmt(v, fmtOf(c)) : (v == null ? "" : String(v));
          var td = el("td", { "class": (num ? "num" : "") + (num && Number(v) < 0 ? " neg" : "") + (i < pin ? " pin" + (i === pin - 1 ? " last" : "") : ""), text: txt });
          if (txt.length > 18) td.title = txt;
          if (i < pin) td.style.left = offs[i] + "px";
          if (opts.cell) opts.cell(td, c, v, r);
          tr.appendChild(td);
        });
        if (opts.rowClick) { tr.style.cursor = "pointer"; tr.addEventListener("click", function () { opts.rowClick(r, data); }); }
        tb.appendChild(tr);
      });
      t.appendChild(tb);
      if (data.totals) {
        var tf = el("tfoot"), fr = el("tr");
        cols.forEach(function (c, i) {
          var v = Array.isArray(data.totals) ? data.totals[data.rawColumns.indexOf(c)] : data.totals[c];
          var td = el("td", { "class": (M.isNum(v) ? "num" : "") + (i < pin ? " pin" + (i === pin - 1 ? " last" : "") : ""), text: i === 0 && (v == null || v === "") ? "Total" : (M.isNum(v) && !isKey(c) ? M.fmt(v, fmtOf(c)) : (v == null ? "" : String(v))) });
          if (i < pin) td.style.left = offs[i] + "px"; fr.appendChild(td);
        });
        tf.appendChild(fr); t.appendChild(tf);
      }
      wrap.appendChild(t);
      if (groups && groups.length) { var g0 = thead.querySelector("tr.grp"); if (g0) t.style.setProperty("--grp-h", g0.offsetHeight + "px"); }
      drawPager();
    }
    function isNumCol(c) {
      var f = fmtOf(c); if (f === "text") return false; if (f) return true;
      for (var i = 0; i < Math.min(30, data.rows.length); i++) { var v = cellVal(data.rows[i], c); if (v !== null && v !== "" && v !== undefined) return M.isNum(v); }
      return false;
    }
    function drawPager() {
      M.clear(pager);
      var pages = Math.max(1, Math.ceil(data.total / st.limit)), from = data.total ? (st.page - 1) * st.limit + 1 : 0, to = Math.min(data.total, st.page * st.limit);
      var size = el("select", { "aria-label": "Rows per page" }, (opts.pageSizes || [100, 200, 500, 1000, 5000]).map(function (n) { return el("option", { value: n, selected: n === st.limit, text: n + " / page" }); }));
      size.addEventListener("change", function () { st.limit = +size.value; api.reload(true); });
      function go(p) { st.page = Math.min(pages, Math.max(1, p)); api.reload(); }
      pager.appendChild(el("span", { text: M.fmt(from, "int") + "–" + M.fmt(to, "int") + " of " + M.fmt(data.total, "int") }));
      pager.appendChild(el("span", { "class": "sp" })); pager.appendChild(size);
      [["«", 1, "First page"], ["‹", st.page - 1, "Previous page"]].forEach(function (b) { pager.appendChild(el("button", { type: "button", "class": "btn sm", disabled: st.page <= 1, "aria-label": b[2], onclick: function () { go(b[1]); }, text: b[0] })); });
      pager.appendChild(el("span", { text: "Page " + M.fmt(st.page, "int") + " of " + M.fmt(pages, "int") }));
      [["›", st.page + 1, "Next page"], ["»", pages, "Last page"]].forEach(function (b) { pager.appendChild(el("button", { type: "button", "class": "btn sm", disabled: st.page >= pages, "aria-label": b[2], onclick: function () { go(b[1]); }, text: b[0] })); });
    }
    function drawChips() {
      M.clear(chips);
      Object.keys(st.filters).forEach(function (c) {
        var v = st.filters[c]; if (!v || !v.length) return;
        chips.appendChild(el("span", { "class": "chip" }, [el("span", { "class": "t", text: lbl(c) + ": " + (v.length > 3 ? v.slice(0, 3).join(", ") + " +" + (v.length - 3) : v.join(", ")) }),
          el("button", { type: "button", "aria-label": "Remove filter", text: "×", onclick: function () { delete st.filters[c]; api.reload(true); } })]));
      });
      st.conds.forEach(function (x, i) {
        chips.appendChild(el("span", { "class": "chip" }, [el("span", { "class": "t", text: M.condLabel(x) }),
          el("button", { type: "button", "aria-label": "Remove condition", text: "×", onclick: function () { st.conds.splice(i, 1); api.reload(true); } })]));
      });
      if (chips.childNodes.length > 1) chips.appendChild(el("button", { type: "button", "class": "btn ghost sm", text: "Clear all", onclick: function () { st.filters = {}; st.conds = []; api.reload(true); } }));
    }

    // ── header filter: value list + condition ─────────────────────────────────────────────────────
    function filterPop(anchor, c, num) {
      var body = el("div", { "class": "pop-b" }), cl = null;
      var ops = num ? [["", "— no condition —"], ["gt", ">"], ["gte", "≥"], ["lt", "<"], ["lte", "≤"], ["eq", "="], ["ne", "≠"], ["between", "between"], ["blank", "is blank"], ["nonblank", "is not blank"]]
                    : [["", "— no condition —"], ["contains", "contains"], ["not_contains", "does not contain"], ["starts", "starts with"], ["eq", "equals"], ["ne", "not equal"], ["blank", "is blank"], ["nonblank", "is not blank"]];
      var cur = st.conds.filter(function (x) { return x.col === c; })[0] || {};
      var op = el("select", { "aria-label": "Condition" }, ops.map(function (o) { return el("option", { value: o[0], selected: o[0] === (cur.op || ""), text: o[1] }); }));
      var v1 = el("input", { type: num ? "number" : "text", value: cur.v1 == null ? "" : cur.v1, placeholder: "value", style: "width:110px" });
      var v2 = el("input", { type: num ? "number" : "text", value: cur.v2 == null ? "" : cur.v2, placeholder: "and", style: "width:90px" });
      function vis() { v2.classList.toggle("hidden", op.value !== "between"); v1.classList.toggle("hidden", !op.value || op.value === "blank" || op.value === "nonblank"); }
      op.addEventListener("change", vis); vis();
      var valuesBox = el("div", {}, el("div", { "class": "muted small", html: '<span class="spin"></span> Loading values…' }));
      body.appendChild(el("div", { "class": "stack" }, [opts.conditions === false ? null : el("div", { "class": "row" }, [op, v1, v2]), opts.values === false ? null : valuesBox]));
      var ok = el("button", { type: "button", "class": "btn primary sm", text: "Apply" }), clr = el("button", { type: "button", "class": "btn sm", text: "Clear" });
      var sortA = el("button", { type: "button", "class": "btn ghost sm", text: "Sort A→Z" }), sortD = el("button", { type: "button", "class": "btn ghost sm", text: "Sort Z→A" });
      M.popover(anchor, [el("div", { "class": "pop-h" }, [el("b", { text: lbl(c) }), el("span", { "class": "spacer" }), sortA, sortD]), body, el("div", { "class": "pop-f" }, [clr, ok])]);
      sortA.addEventListener("click", function () { M.closePop(); st.sort_col = c; st.sort_dir = "asc"; api.reload(true); });
      sortD.addEventListener("click", function () { M.closePop(); st.sort_col = c; st.sort_dir = "desc"; api.reload(true); });
      if (opts.values !== false) {
        var p = opts.values ? opts.values(c, api.state()) : Promise.resolve(distinctOnPage(c));
        Promise.resolve(p).then(function (vals) {
          M.clear(valuesBox); cl = M.checklist([{ items: vals || [] }], st.filters[c] || [], { height: "260px", prettify: false, placeholder: "Search values" }); valuesBox.appendChild(cl.el);
        }, function (e) { M.clear(valuesBox).appendChild(el("div", { "class": "muted small", text: "Value list not available: " + e.message })); });
      }
      ok.addEventListener("click", function () {
        st.conds = st.conds.filter(function (x) { return x.col !== c; });
        if (op.value) st.conds.push({ col: c, op: op.value, v1: v1.value, v2: v2.value });
        if (cl) { var v = cl.get(); if (v.length) st.filters[c] = v; else delete st.filters[c]; }
        M.closePop(); api.reload(true);
      });
      clr.addEventListener("click", function () { delete st.filters[c]; st.conds = st.conds.filter(function (x) { return x.col !== c; }); M.closePop(); api.reload(true); });
    }
    function distinctOnPage(c) {
      var seen = {}, out = [];
      data.rows.forEach(function (r) { var v = cellVal(r, c); v = v == null || v === "" ? "(blank)" : String(v); if (!seen[v]) { seen[v] = 1; out.push(v); } });
      return out.sort();
    }

    // ── column resize ─────────────────────────────────────────────────────────────────────────────
    function startResize(e, c, th) {
      e.preventDefault(); e.stopPropagation();
      var x0 = e.clientX, w0 = th.getBoundingClientRect().width;
      function mv(ev) { var w = Math.max(50, Math.round(w0 + ev.clientX - x0)); layout.widths = layout.widths || {}; layout.widths[c] = w;
        var cols = visibleCols(), idx = cols.indexOf(c), col = wrap.querySelectorAll("col")[idx]; if (col) col.style.width = w + "px"; }
      function up() { document.removeEventListener("mousemove", mv); document.removeEventListener("mouseup", up); saveLayout(); draw(); }
      document.addEventListener("mousemove", mv); document.addEventListener("mouseup", up);
    }

    // ── columns panel ─────────────────────────────────────────────────────────────────────────────
    function colsPanel(anchor) {
      var all = data.columns.slice(), order = (layout.order || []).filter(function (c) { return all.indexOf(c) >= 0; });
      all.forEach(function (c) { if (order.indexOf(c) < 0) order.push(c); });
      var hidden = (layout.hidden || []).slice(), pin = layout.pin, q = el("input", { type: "search", placeholder: "Find a column" }), list = el("div", { "class": "stack", style: { gap: "1px" } });
      function drawList() {
        M.clear(list); var s = q.value.trim().toLowerCase(), vis = order.filter(function (c) { return hidden.indexOf(c) < 0; }), pc = pin == null ? pinCount(vis) : pin;
        order.forEach(function (c, i) {
          if (s && lbl(c).toLowerCase().indexOf(s) < 0) return;
          var cb = el("input", { type: "checkbox", checked: hidden.indexOf(c) < 0, "aria-label": "Show " + lbl(c) });
          cb.addEventListener("change", function () { if (cb.checked) hidden = hidden.filter(function (x) { return x !== c; }); else hidden.push(c); });
          var vi = vis.indexOf(c);
          list.appendChild(el("div", { "class": "colrow" }, [cb, el("span", { text: lbl(c) + (vi >= 0 && vi < pc ? "  ·  frozen" : "") }),
            el("button", { type: "button", "class": "mv", title: "Move up", "aria-label": "Move up", text: "↑", onclick: function () { if (i > 0) { order.splice(i, 1); order.splice(i - 1, 0, c); drawList(); } } }),
            el("button", { type: "button", "class": "mv", title: "Freeze up to here", "aria-label": "Freeze up to here", text: "❄", onclick: function () { pin = vi >= 0 ? vi + 1 : pin; drawList(); } })]));
        });
      }
      q.addEventListener("input", drawList); drawList();
      var ok = el("button", { type: "button", "class": "btn primary sm", text: "Apply" }), reset = el("button", { type: "button", "class": "btn sm", text: "Reset" });
      var nopin = el("button", { type: "button", "class": "btn ghost sm", text: "Unfreeze", onclick: function () { pin = 0; drawList(); } });
      M.popover(anchor, [el("div", { "class": "pop-h" }, [el("b", { text: "Columns" }), el("span", { "class": "spacer" }), nopin]), el("div", { "class": "pop-b" }, [q, el("div", { style: { height: "6px" } }), list]),
        el("div", { "class": "pop-f" }, [reset, ok])], { alignRight: true });
      ok.addEventListener("click", function () { layout.order = order; layout.hidden = hidden; layout.pin = pin; saveLayout(); M.closePop(); draw(); });
      reset.addEventListener("click", function () { layout = { density: layout.density }; saveLayout(); M.closePop(); draw(); });
    }

    // ── saved views ───────────────────────────────────────────────────────────────────────────────
    function snapshot() { return { layout: JSON.parse(JSON.stringify(layout)), filters: st.filters, conds: st.conds, sort_col: st.sort_col, sort_dir: st.sort_dir, limit: st.limit,
                                   extra: opts.getExtra ? opts.getExtra() : null }; }
    function applyView(v) {
      var s = v.state || {}; layout = s.layout || {}; saveLayout(); root.classList.toggle("compact", layout.density === "compact");
      st.filters = s.filters || {}; st.conds = s.conds || []; st.sort_col = s.sort_col || null; st.sort_dir = s.sort_dir || "asc"; st.limit = s.limit || st.limit;
      if (opts.setExtra && s.extra) opts.setExtra(s.extra);
      api.reload(true); M.toast("View “" + v.name + "” applied.");
    }
    function viewsMenu(anchor) {
      var box = el("div", { "class": "pop-b" }, el("div", { "class": "muted small", html: '<span class="spin"></span>' }));
      var name = el("input", { type: "text", placeholder: "Name this view", maxlength: "80" }), def = el("input", { type: "checkbox" });
      var save = el("button", { type: "button", "class": "btn primary sm", text: "Save view" });
      M.popover(anchor, [el("div", { "class": "pop-h" }, [el("b", { text: "Saved views" })]), box,
        el("div", { "class": "pop-f", style: { flexWrap: "wrap", justifyContent: "flex-start" } }, [name, el("label", { "class": "row small", style: { gap: "5px" } }, [def, "Default"]), save])], { alignRight: true });
      function load() {
        M.views.list(opts.id).then(function (vs) {
          M.clear(box);
          if (!vs.length) box.appendChild(el("div", { "class": "muted small", style: { padding: "6px" }, text: "No saved views yet. Arrange the columns, filters and sort, then save them here." }));
          vs.forEach(function (v) {
            box.appendChild(el("div", { "class": "colrow", style: { gridTemplateColumns: "minmax(0,1fr) auto auto" } }, [
              el("button", { type: "button", "class": "btn ghost sm", style: { justifyContent: "flex-start" }, text: v.name + (v.is_default ? "  ★" : ""), onclick: function () { M.closePop(); applyView(v); } }),
              el("button", { type: "button", "class": "mv", title: "Overwrite with the current layout", text: "⟳", onclick: function () { M.views.save(opts.id, v.name, snapshot(), v.is_default).then(function () { M.toast("Saved."); load(); }); } }),
              el("button", { type: "button", "class": "mv", title: "Delete", text: "✕", onclick: function () { M.views.remove(opts.id, v.id).then(load); } })]));
          });
        }, function (e) { M.clear(box).appendChild(el("div", { "class": "notice bad", text: e.message })); });
      }
      save.addEventListener("click", function () {
        if (!name.value.trim()) { name.focus(); return; }
        M.views.save(opts.id, name.value.trim(), snapshot(), def.checked).then(function () { M.toast("View saved."); name.value = ""; load(); }, function (e) { M.toast(e.message, "bad"); });
      });
      load();
    }

    Object.assign(api, {
      el: root,
      state: function () { return { page: st.page, limit: st.limit, sort_col: st.sort_col, sort_dir: st.sort_dir, filters: JSON.parse(JSON.stringify(st.filters)), conds: JSON.parse(JSON.stringify(st.conds)) }; },
      setState: function (s) { Object.keys(s || {}).forEach(function (k) { st[k] = s[k]; }); },
      columns: function () { return visibleCols(); }, allColumns: function () { return data.columns.slice(); },
      data: function () { return data; }, setTitle: function (t) { titleEl.textContent = t; },
      applyDefaultView: function () {
        if (!opts.id) return Promise.resolve(false);
        return M.views.list(opts.id).then(function (vs) { var d = vs.filter(function (v) { return v.is_default; })[0]; if (d) { var s = d.state || {};
          layout = s.layout || layout; st.filters = s.filters || {}; st.conds = s.conds || []; st.sort_col = s.sort_col || st.sort_col; st.sort_dir = s.sort_dir || st.sort_dir; st.limit = s.limit || st.limit;
          if (opts.setExtra && s.extra) opts.setExtra(s.extra); root.classList.toggle("compact", layout.density === "compact"); return true; } return false; }, function () { return false; });
      }
    });
    return api;
  };
})();

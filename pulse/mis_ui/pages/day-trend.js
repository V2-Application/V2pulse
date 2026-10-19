// Day Wise Trend (DFS /sf-mis-day-trend, SfMisDayTrend.jsx) and a saved Day Wise Trend report opened
// (/mis/day-trend-reports/r/<slug>, DFS SfDayTrendReportView): the LAST-30-DAYS SALE-STOCK-TREND workbook served from
// the day-wise facts - one row per grain tuple, one block of columns per DAY (stock tags first, then that day's sale),
// newest day first. Stock is what that day REPORTED (no carry-forward), so the coverage strip says how many stores
// reported each day. A saved report locks its grain, tags, measures, V2R plants, stock-total mode, store-only switch,
// saved filters and conditions; dates, extra filters, conditions and search stay live.
// API: /api/v1/mis/day-trend/{params, result, values, export?fmt=xlsx|csv}, /api/v1/mis/day-trend-report/reports.
// Also publishes MIS.dtKit (condition panel, filter panel, V2R picker...) for pages/day-trend-reports.js.
(function () {
  "use strict";
  var M = window.MIS, el = M.el;
  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var STOCK_TOTAL = { add: "each tag + their TOTAL", only: "TOTAL only", off: "each tag, no total" };
  var DAY_RE = /^(\d{4}-\d{2}-\d{2}) (.+)$/;
  // '2026-09-16' -> '16-Sep-26', the workbook's date band spelling (built from the parts, not the browser locale).
  function fmtDay(d) { var p = String(d).split("-"); return p[2] + "-" + MON[Number(p[1]) - 1] + "-" + String(p[0]).slice(2); }
  function addDays(d, n) { return M.addDays(d, n); }

  // ── the MIS condition vocabulary (DFS lib/misConds.js) ─────────────────────────────────────────
  var LIST_OPS = [["in", "Is any of"], ["not_in", "Is none of"]];
  var BLANK_OPS = [["nonblank", "Is not blank"], ["blank", "Is blank"]];
  var LIKE_OPS = [["like", "Matches pattern"], ["not_like", "Does not match pattern"]];
  var NUM_OPS = [["gte", "Greater than or equal"], ["gt", "Greater than"], ["lte", "Less than or equal"], ["lt", "Less than"],
                 ["eq", "Equals"], ["ne", "Does not equal"], ["between", "Between"]];
  var ALL_OPS = LIST_OPS.concat([["between", "Between"]], NUM_OPS.filter(function (o) { return o[0] !== "between"; }),
    [["contains", "Contains"], ["not_contains", "Does not contain"], ["starts", "Begins with"], ["ends", "Ends with"]], LIKE_OPS, BLANK_OPS);
  var MEAS_OPS = NUM_OPS.concat(LIST_OPS, BLANK_OPS);
  function isList(op) { return op === "in" || op === "not_in"; }
  function clone(x) { return JSON.parse(JSON.stringify(x == null ? null : x)); }

  // The editor's flat rows -> the engine's three shapes (DFS condsToDef).
  function condsToDef(rows, dims) {
    var filters = {}, colFilters = [], dimFilters = [];
    (rows || []).forEach(function (r) {
      var col = String(r.col || "").trim(); if (!col) return;
      var isDim = dims.indexOf(col) >= 0, bucket = isDim ? dimFilters : colFilters;
      var v1 = r.v1 == null ? "" : String(r.v1), v2 = r.v2 == null ? "" : String(r.v2);
      if (isList(r.op)) {
        var vals = v1.split(",").map(function (v) { return v.trim(); }).filter(Boolean);
        if (!vals.length) return;
        if (r.op === "in" && isDim) filters[col] = vals; else bucket.push({ col: col, op: r.op, vals: vals });
      } else if (r.op === "blank" || r.op === "nonblank") bucket.push({ col: col, op: r.op });
      else if (r.op === "between") { if (v1 !== "" && v2 !== "") bucket.push({ col: col, op: r.op, v1: r.v1, v2: r.v2 }); }
      else if (v1 !== "") bucket.push({ col: col, op: r.op, v1: r.v1 });
    });
    return { filters: filters, col_filters: colFilters, dim_filters: dimFilters };
  }
  // Every dimension condition as one scan-side list (DFS dimCondsOnly) - the trend pages' shape.
  function dimCondsOnly(rows, dims) {
    var d = condsToDef(rows, dims);
    return d.dim_filters.concat(Object.keys(d.filters).map(function (c) { return { col: c, op: "in", vals: d.filters[c] }; }));
  }
  // Keep the order things were PICKED in (DFS PickList), whatever order the checklist reports them in.
  function pickOrder(prev, next) {
    return prev.filter(function (x) { return next.indexOf(x) >= 0; }).concat(next.filter(function (x) { return prev.indexOf(x) < 0; }));
  }

  // ── the CONDITIONS panel (DFS components/MisConditions.jsx) ─────────────────────────────────────
  // o: {grouped:[cols], offBy:[[label, cols]], measures:[{id,label}], loadValues(col)->Promise<values>, valuesKey()->string,
  //     note, emptyText, initial:[rows], onApply(rows) (Apply / Applied button) | onChange(rows) (live)}
  function condPanel(o) {
    var rows = clone(o.initial || []) || [], applied = clone(rows), cache = {};
    var countEl = el("span", { "class": "chip" }), list = el("div", { "class": "stack" }), notes = el("div", { "class": "stack" });
    var addBtn = el("button", { type: "button", "class": "btn sm" }, [M.icon("plus", 14), "Add condition"]);
    var clearBtn = el("button", { type: "button", "class": "btn sm ghost" }, [M.icon("x", 14), "Clear all"]);
    var applyBtn = o.onApply ? el("button", { type: "button", "class": "btn sm primary" }) : null;
    var box = el("div", { "class": "card" }, [
      el("div", { "class": "card-h" }, [el("h2", { text: "Conditions" }), countEl, el("span", { "class": "note", text: o.note || "" }), el("span", { "class": "spacer" }), clearBtn, addBtn, applyBtn]),
      el("div", { "class": "card-b stack" }, [list, notes])]);
    function offAll() { var a = []; (o.offBy || []).forEach(function (g) { a = a.concat(g[1]); }); return a; }
    function measIds() { return (o.measures || []).map(function (m) { return m.id; }); }
    function isDim(c) { return (o.grouped || []).indexOf(c) >= 0 || offAll().indexOf(c) >= 0; }
    function dirty() { return JSON.stringify(rows) !== JSON.stringify(applied); }
    function paintHead() {
      var live = rows.filter(function (r) { return r.col; }).length;
      countEl.textContent = live + " condition" + (live === 1 ? "" : "s"); countEl.className = "chip" + (live ? "" : " grey");
      clearBtn.classList.toggle("hidden", !live);
      if (applyBtn) { var d = dirty(); applyBtn.disabled = !d; M.clear(applyBtn); applyBtn.appendChild(M.icon("filter", 14)); applyBtn.appendChild(document.createTextNode(d ? "Apply" : "Applied")); }
    }
    function changed() { paintHead(); paintNotes(); if (o.onChange) o.onChange(clone(rows)); }
    function optgroup(label, items) {
      return el("optgroup", { label: label }, items.map(function (x) { return el("option", { value: x[0], text: x[1] }); }));
    }
    function valuePicker(btn, r) {
      var body = el("div", { "class": "pop-b" }, [el("div", { "class": "muted small", html: '<span class="spin"></span> Loading values…' })]);
      var ok = el("button", { type: "button", "class": "btn primary sm", text: "Use these" }), cl = null;
      M.popover(btn, [el("div", { "class": "pop-h" }, [el("b", { text: r.col }), el("span", { "class": "muted tiny", text: "values over the dates on this page" })]), body, el("div", { "class": "pop-f" }, [ok])]);
      var key = r.col + "|" + (o.valuesKey ? o.valuesKey() : "");
      var cur = String(r.v1 || "").split(",").map(function (v) { return v.trim(); }).filter(Boolean);
      (cache[key] || (cache[key] = Promise.resolve(o.loadValues(r.col)))).then(function (vals) {
        M.clear(body); cl = M.checklist([{ items: vals || [] }], cur, { height: "280px", prettify: false, placeholder: "Search values", max: 500 }); body.appendChild(cl.el);
      }, function (e) { delete cache[key]; M.clear(body).appendChild(el("div", { "class": "notice bad", text: e.message })); });
      ok.addEventListener("click", function () { if (cl) { var v = cl.get(); if (v.length > 500) { M.toast("At most 500 values in one list.", "bad"); return; } r.v1 = v.join(", "); } M.closePop(); draw(); changed(); });
    }
    function draw() {
      M.clear(list);
      if (!rows.length) list.appendChild(el("p", { "class": "muted small", style: { margin: 0 }, text: o.emptyText || "No conditions." }));
      var off = offAll(), mids = measIds();
      rows.forEach(function (r, i) {
        var dim = isDim(r.col), ops = dim || !mids.length ? ALL_OPS : MEAS_OPS;
        var col = el("select", { "aria-label": "Column", style: { minWidth: "200px" } }, [el("option", { value: "", text: "- pick a column -" })]);
        if ((o.grouped || []).length) col.appendChild(optgroup("Group-by columns (filters rows before totalling)", o.grouped.map(function (c) { return [c, c]; })));
        (o.offBy || []).forEach(function (g) { col.appendChild(optgroup(g[0] + " - filter only, not a column here", g[1].map(function (c) { return [c, c]; }))); });
        if (mids.length) col.appendChild(optgroup("Measures (filters the totals themselves)", o.measures.map(function (m) { return [m.id, m.label]; })));
        if (r.col && !dim && mids.indexOf(r.col) < 0) col.appendChild(el("option", { value: r.col, text: r.col + " (not available here)" }));
        col.value = r.col || "";
        col.addEventListener("change", function () {
          var nowDim = isDim(col.value), ok = (nowDim || !mids.length ? ALL_OPS : MEAS_OPS).some(function (x) { return x[0] === r.op; });
          r.col = col.value; if (!ok) r.op = nowDim ? "in" : "gte"; draw(); changed();
        });
        var op = el("select", { "aria-label": "Operator" }, ops.map(function (x) { return el("option", { value: x[0], text: x[1] }); }));
        op.value = r.op; if (op.value !== r.op) { r.op = ops[0][0]; op.value = r.op; }
        op.addEventListener("change", function () { r.op = op.value; draw(); changed(); });
        var needs0 = r.op === "blank" || r.op === "nonblank", needs2 = r.op === "between", lst = isList(r.op), pat = r.op === "like" || r.op === "not_like";
        var kids = [col, op];
        if (!needs0) {
          var v1 = el("input", { type: r.col === "DT" && !lst ? "date" : "text", value: r.v1 == null ? "" : r.v1, "aria-label": "Value", style: { width: "180px" },
                                 placeholder: lst ? "value, value, value" : pat ? "HB%  ·  %KURTA%" : needs2 ? "from" : "value" });
          v1.addEventListener("input", function () { r.v1 = v1.value; changed(); }); kids.push(v1);
        }
        if (needs2) {
          var v2 = el("input", { type: r.col === "DT" ? "date" : "text", value: r.v2 == null ? "" : r.v2, "aria-label": "Second value", placeholder: "to", style: { width: "150px" } });
          v2.addEventListener("input", function () { r.v2 = v2.value; changed(); }); kids.push(v2);
        }
        if (lst && dim && o.loadValues) {
          var pv = el("button", { type: "button", "class": "btn sm", title: "The values " + r.col + " actually has over the dates this page is showing. Ticking writes them into the box beside it." }, [M.icon("filter", 13), "Pick values…"]);
          pv.addEventListener("click", function () { valuePicker(pv, r); }); kids.push(pv);
        }
        if (lst) kids.push(el("span", { "class": "muted tiny", text: "separate with commas" }));
        if (off.indexOf(r.col) >= 0) kids.push(el("span", { "class": "muted tiny", text: "filters only - " + r.col + " is not a column here" }));
        if (pat) kids.push(el("span", { "class": "muted tiny", text: "% any run of characters, _ one character; case is ignored. For a literal one write !% or !_." }));
        kids.push(el("button", { type: "button", "class": "btn ghost sm", "aria-label": "Remove this condition", title: "Remove this condition", onclick: function () { rows.splice(i, 1); draw(); changed(); } }, [M.icon("trash", 14)]));
        list.appendChild(el("div", { "class": "row" }, kids));
      });
      paintHead(); paintNotes();
    }
    function paintNotes() {
      M.clear(notes);
      if (rows.some(function (r) { return !r.col; })) notes.appendChild(el("div", { "class": "notice warn small", text: "A row with no column is ignored." }));
      var eqCols = rows.filter(function (r) { return r.col && r.op === "eq"; }).map(function (r) { return r.col; });
      var stacked = eqCols.filter(function (c, i) { return eqCols.indexOf(c) !== i; }).filter(function (c, i, a) { return a.indexOf(c) === i; });
      if (stacked.length) notes.appendChild(el("div", { "class": "notice small", text: stacked.join(", ") + ": several Equals rows on one column are read as ANY of those values, not all of them at once. Is any of with a comma-separated list says the same thing in one row." }));
      if (rows.some(function (r) { return r.col === "DT" && r.op !== "in"; })) notes.appendChild(el("div", { "class": "notice warn small", text: "A condition on DATE holds fixed dates. The window is the From / To above, so a fixed DATE condition will not move with it - use it only to carve a fixed slice out of the range." }));
    }
    addBtn.addEventListener("click", function () { rows.push({ col: "", op: "gte", v1: "", v2: "" }); draw(); changed(); });
    clearBtn.addEventListener("click", function () { rows = []; if (o.onApply) { applied = []; o.onApply([]); } draw(); changed(); });
    if (applyBtn) applyBtn.addEventListener("click", function () { applied = clone(rows); paintHead(); o.onApply(clone(applied)); });
    draw();
    return { el: box, rows: function () { return clone(rows); }, applied: function () { return clone(applied); },
             set: function (r) { rows = clone(r || []) || []; applied = clone(rows); draw(); },
             setContext: function (c) { Object.keys(c).forEach(function (k) { o[k] = c[k]; }); draw(); },
             clearCache: function () { cache = {}; } };
  }

  // dim filters saved back into the one flat list the panel edits (DFS condsFromDef)
  function condsFromDef(defn) {
    var rows = [];
    Object.keys((defn && defn.filters) || {}).forEach(function (c) { rows.push({ col: c, op: "in", v1: (defn.filters[c] || []).join(", "), v2: "" }); });
    ((defn && defn.col_filters) || []).concat((defn && defn.dim_filters) || []).forEach(function (f) {
      rows.push({ col: f.col, op: f.op, v2: f.v2 == null ? "" : f.v2, v1: Array.isArray(f.vals) ? f.vals.join(", ") : (f.v1 == null ? "" : f.v1) });
    });
    return rows;
  }
  // The measure-condition ids (engine meas_cond_ids): a sale measure's window total, a stock tag on the LAST day.
  var MCL = { SALE_Q: "SALE-Q", SALE_V: "SALE-V", GM_V: "GM-V", DISCOUNT_V: "DISC-V" };
  function dayTrendMeasConds(tags, meas) {
    var out = (meas || []).map(function (m) { return { id: m, label: (MCL[m] || m) + " (window total)" }; });
    var all = (tags || []).length > 1 ? tags.concat(["TOTAL"]) : (tags || []);
    all.forEach(function (t) { ["Q", "V"].forEach(function (q) { out.push({ id: "STK_" + q + ":" + t, label: t + "-STK-" + q + " (last day)" }); }); });
    return out;
  }
  function condText(c) {
    if (isList(c.op) && Array.isArray(c.vals)) return (c.op === "in" ? (c.vals.length === 1 ? "= " + c.vals[0] : c.vals.length + " values") : (c.vals.length === 1 ? "≠ " + c.vals[0] : "not " + c.vals.length + " values"));
    var name = (ALL_OPS.filter(function (o) { return o[0] === c.op; })[0] || [c.op, c.op])[1];
    if (c.op === "between") return c.v1 + " – " + c.v2;
    if (c.op === "blank" || c.op === "nonblank") return name;
    return name + " " + c.v1;
  }

  // ── a dimension's VALUE filter (DFS FilterPanel): values narrowed by the OTHER filters, never its own ─────────
  // o: {dim, range()->{from,to}, filters()->{DIM:[v]}, value:[], onChange(v), onRemove()}
  function filterPanel(o) {
    var value = (o.value || []).slice(), lastKey = null, seq = 0;
    var cnt = el("span", { "class": "muted tiny" }), spin = el("span", { "class": "spin hidden" }), trunc = el("div", { "class": "muted tiny hidden", style: { color: "var(--warn)" }, text: "List capped - type to narrow, or filter something else first." });
    var cl = M.checklist([{ items: [] }], value, { height: "170px", prettify: false, max: 300, placeholder: "Search values",
      onChange: function (v) { value = v; paint(); o.onChange(v.slice()); } });
    var clear = el("button", { type: "button", "class": "btn ghost sm", text: "Clear", onclick: function () { value = []; cl.set([]); paint(); o.onChange([]); } });
    var rm = el("button", { type: "button", "class": "btn ghost sm", "aria-label": "Remove the " + o.dim + " filter", title: "Remove the " + o.dim + " filter", onclick: function () { o.onRemove(); } }, [M.icon("x", 13)]);
    var box = el("div", { "class": "rcard", style: { gap: "6px", padding: "10px 12px" } }, [
      el("div", { "class": "row", style: { gap: "6px" } }, [el("b", { "class": "small", text: o.dim }), spin, el("span", { "class": "spacer" }), cnt, clear, rm]), cl.el, trunc]);
    function paint() { cnt.textContent = value.length + " picked"; }
    function load(force) {
      var r = o.range(), f = {}, all = o.filters() || {};
      Object.keys(all).forEach(function (k) { if (k !== o.dim && all[k] && all[k].length) f[k] = all[k]; });
      var key = JSON.stringify([r.from, r.to, f]); if (!force && key === lastKey) return; lastKey = key;
      if (!r.from || !r.to) return;
      var my = ++seq; spin.classList.remove("hidden");
      M.post("/api/v1/mis/day-trend/values", { dim: o.dim, from_date: r.from, to_date: r.to, filters: f }).then(function (d) {
        if (my !== seq) return; spin.classList.add("hidden");
        cl.setGroups([{ items: d.values || [] }]); trunc.classList.toggle("hidden", !d.truncated);
      }, function (e) { if (my !== seq) return; spin.classList.add("hidden"); M.toast(o.dim + ": " + e.message, "bad"); });
    }
    paint(); load(true);
    return { el: box, dim: o.dim, refresh: function () { load(false); }, get: function () { return value.slice(); } };
  }

  // ── which V2R plants count toward the V2R_* stock COLUMNS (DFS V2rPlantPicker) ─────────────────────────────
  function v2rPicker(plants, value, onChange) {
    value = (value || []).slice(); plants = plants || [];
    var dcs = plants.filter(function (p) { return p.typ === "DC" && p.stat !== "IN-ACT"; }).map(function (p) { return p.cd; });
    var b = el("button", { type: "button", "class": "btn dd-btn", title: "Narrows the V2R_* stock COLUMNS to the plants picked here. No rows are dropped - the plants left out keep their sale and every other column.\n\nOnly V2R_* is affected: SDC_* is the stores' own warehouse and V2S_* is six plants, so narrowing either to a DC would blank it rather than narrow it." });
    function label() {
      if (!value.length) return "all " + (plants.length || "") + " plants";
      if (value.length === dcs.length && dcs.every(function (d) { return value.indexOf(d) >= 0; })) return "the DCs (" + dcs.join(" / ") + ")";
      return value.length + " plant(s)";
    }
    function paint() { M.clear(b); b.appendChild(el("span", { "class": "t", text: "V2R stock: " + label() })); b.appendChild(M.icon("chev", 13)); }
    b.addEventListener("click", function () {
      var cl = M.checklist([{ items: plants.map(function (p) { return { value: p.cd, label: p.cd + "  " + (p.nm || "") + "  ·  " + p.typ + (p.stat === "IN-ACT" ? " · IN-ACT" : "") }; }) }], value, { height: "260px", prettify: false, placeholder: "Search plants" });
      var dc = el("button", { type: "button", "class": "btn sm", text: "DCs", onclick: function () { cl.set(dcs); } });
      var all = el("button", { type: "button", "class": "btn sm", text: "All (no narrowing)", onclick: function () { cl.set([]); } });
      var ok = el("button", { type: "button", "class": "btn primary sm", text: "Apply" });
      M.popover(b, [el("div", { "class": "pop-h" }, [el("b", { text: "V2R plants" }), el("span", { "class": "spacer" }), dc, all]),
        el("div", { "class": "pop-b" }, [plants.length ? cl.el : el("div", { "class": "muted small", text: "No V2R plants loaded." })]),
        el("div", { "class": "pop-f" }, [el("span", { "class": "muted tiny", style: { marginRight: "auto" }, text: "Columns only - the plants left out keep their rows and their sale." }), ok])]);
      ok.addEventListener("click", function () { value = cl.get(); M.closePop(); paint(); onChange(value.slice()); });
    });
    paint();
    return { el: b, get: function () { return value.slice(); }, set: function (v) { value = (v || []).slice(); paint(); } };
  }

  // A button that opens a checklist of dimensions to filter on ("add a condition" in DFS).
  function addFilterButton(groupsFn, selectedFn, onApply) {
    var b = el("button", { type: "button", "class": "btn sm" }, [M.icon("plus", 14), "Add a filter"]);
    b.addEventListener("click", function () {
      var cl = M.checklist(groupsFn(), selectedFn(), { height: "300px", prettify: false, placeholder: "Find a dimension" });
      var ok = el("button", { type: "button", "class": "btn primary sm", text: "Apply" });
      M.popover(b, [el("div", { "class": "pop-h" }, [el("b", { text: "Filter on" }), el("span", { "class": "muted tiny", text: "each gets its own value list" })]), el("div", { "class": "pop-b" }, cl.el), el("div", { "class": "pop-f" }, ok)]);
      ok.addEventListener("click", function () { var v = cl.get(); M.closePop(); onApply(pickOrder(selectedFn(), v)); });
    });
    return b;
  }

  // Grid helpers shared by the page and the report builder's preview: two header rows (the date over its block).
  function dayGridOpts(getRes, cover) {
    function dims() { var r = getRes(); return (r && r.dims) || []; }
    function totalCol(c) { var r = getRes(), t = (r && r.stockTotalCol) || "TOTAL"; return !!c && (c.slice(-(t.length + 6)) === t + "-STK-Q" || c.slice(-(t.length + 6)) === t + "-STK-V"); }
    return {
      isKey: function (c) { return dims().indexOf(c) >= 0; },
      format: function (c) { return dims().indexOf(c) >= 0 ? "text" : "dec"; },
      label: function (c) { var m = DAY_RE.exec(c); return m ? m[2] : c; },
      groups: function (cols) {
        var out = [];
        cols.forEach(function (c) {
          var m = DAY_RE.exec(c), key = m ? m[1] : "", last = out[out.length - 1];
          if (last && last.key === key) last.span++; else out.push({ key: key, span: 1 });
        });
        return out.map(function (g) { return { span: g.span, label: g.key ? fmtDay(g.key) + (cover && cover[g.key] === 0 ? " · no stock file" : "") : "" }; });
      },
      cell: function (td, c, v) {
        var key = dims().indexOf(c) >= 0;
        if (v === null || v === undefined || v === "") { td.textContent = key ? "—" : "·"; td.classList.add("muted"); if (!key) td.style.textAlign = "right"; }
        if (totalCol(c)) { td.style.background = "var(--warn-soft)"; td.style.fontWeight = "600"; td.title = "the picked stock tags added up"; }
      }
    };
  }

  M.dtKit = { condPanel: condPanel, condsToDef: condsToDef, condsFromDef: condsFromDef, dimCondsOnly: dimCondsOnly, dayTrendMeasConds: dayTrendMeasConds,
              condText: condText, filterPanel: filterPanel, v2rPicker: v2rPicker, addFilterButton: addFilterButton, pickOrder: pickOrder, fmtDay: fmtDay,
              dayGridOpts: dayGridOpts, STOCK_TOTAL: STOCK_TOTAL, clone: clone };

  // ════════════════════════════════════════════════════════════════════════════════════════════
  M.page("day-trend", {
    render: function (ctx) {
      var slug = ctx.route.slug || null;
      M.setHeader({ title: slug ? "Day Wise Trend Report" : "Day Wise Trend",
        crumbs: slug ? [{ label: "Day Wise Trend Reports", href: "/mis/day-trend-reports" }, { label: slug }] : [{ label: "Day Wise Trend", href: "/mis/day-trend" }],
        sub: "One row per grain you pick and one block of columns per day - stock tags first, then that day's sale, newest day first.",
        tiles: [{ k: "Window", skel: true }, { k: "Rows", skel: true }, { k: "Columns", skel: true }, { k: "Latest loaded day", skel: true }] });
      var pRep = slug ? M.get("/api/v1/mis/day-trend-report/reports").then(function (r) { return (r.reports || []).filter(function (x) { return x.slug === slug; })[0] || null; }) : Promise.resolve(null);
      return Promise.all([M.get("/api/v1/mis/day-trend/params"), pRep]).then(function (a) {
        if (!ctx.alive()) return;
        var meta = a[0], report = a[1];
        if (slug && !report) {
          M.setHeader({ tiles: [] });
          ctx.main.appendChild(el("div", { "class": "notice bad" }, [el("span", { text: "No day wise trend report “" + slug + "” that you can see. " }),
            el("a", { href: "/mis/day-trend-reports", text: "Back to Day Wise Trend Reports" })]));
          return;
        }
        return build(ctx, meta, report);
      });
    }
  });

  function build(ctx, meta, report) {
    var rdef = report ? (report.definition || {}) : null, mine = report && report.createdBy === ctx.me.emp_code;
    var dimGroups = (meta.dims || []).map(function (g) { return { label: g.label, items: g.cols || [] }; });
    var allDims = [].concat.apply([], dimGroups.map(function (g) { return g.items; }));
    var st = { from: meta.defaultFrom || "", to: meta.defaultTo || "", dims: (meta.defaultDims || []).slice(), tags: (meta.defaultStockTags || []).slice(),
               meas: (meta.defaultMeasures || []).slice(), v2r: [], storeOnly: false, stockTotal: "add", filterDims: [], filters: {}, search: "", condApplied: [] };
    if (rdef) {
      var n = Math.max(1, Math.min(Number(rdef.days) || meta.defaultDays || 30, meta.maxDays || 31));
      st.to = meta.latest || ""; st.from = meta.latest ? addDays(meta.latest, -(n - 1)) : "";
      st.dims = (rdef.group_by || []).slice(); st.tags = (rdef.stock_tags || []).slice(); st.meas = (rdef.measures || []).slice();
      st.v2r = (rdef.v2r_plants || []).slice(); st.storeOnly = !!rdef.store_only; st.stockTotal = rdef.stock_total || "add";
    }
    var savedFilters = (rdef && rdef.filters) || {}, res = null, grid = null, panels = {};
    var cover = {}; (meta.stockDays || []).forEach(function (d) { cover[d.day] = d.stores; });
    var coverMax = Math.max.apply(null, [1].concat(Object.keys(cover).map(function (k) { return cover[k]; })));
    function measLabel(m) { return (meta.measureLabels || {})[m] || m; }
    function span() { return st.from && st.to ? Math.round((Date.parse(st.to) - Date.parse(st.from)) / 864e5) + 1 : 0; }
    function activeFilters() { var f = {}; Object.keys(st.filters).forEach(function (k) { if (st.filters[k] && st.filters[k].length && !savedFilters[k]) f[k] = st.filters[k]; }); return f; }
    function effFilters() { var f = activeFilters(); Object.keys(savedFilters).forEach(function (k) { f[k] = savedFilters[k]; }); return f; }
    function dimConds() { return ((rdef && rdef.dim_filters) || []).concat(dimCondsOnly(st.condApplied, allDims)); }
    function measConds() { return ((rdef && rdef.col_filters) || []).concat(condsToDef(st.condApplied, allDims).col_filters); }
    function body() {
      var ef = effFilters(), dc = dimConds(), mc = measConds();
      return { from_date: st.from, to_date: st.to, group_by: st.dims.length ? st.dims : null, stock_tags: st.tags.length ? st.tags : null,
               measures: st.meas.length ? st.meas : null, filters: Object.keys(ef).length ? ef : null, v2r_plants: st.v2r.length ? st.v2r : null,
               store_only: !!st.storeOnly, stock_total: st.stockTotal, search: st.search || null, dim_conds: dc.length ? dc : null, meas_conds: mc.length ? mc : null };
    }
    var reload = M.debounce(function () { paintSummary(); Object.keys(panels).forEach(function (k) { panels[k].refresh(); }); grid.reload(true); }, 350);
    var reloadSoon = M.debounce(function () { reload(); }, 500);

    M.setHeader({ title: report ? report.name : "Day Wise Trend",
      sub: report ? (report.description || "A saved Day Wise Trend report.") + " Grain, stock tags and measures are fixed by the report (edit it on the reports page); dates, extra filters, conditions and search are yours."
                  : "One row per grain you pick, one block of columns per day - stock tags first, then that day's sale. Newest day first, so the default (store × variant, 0001 and 0006 stock, sale and GM) lines up with LAST-30-DAYS SALE-STOCK-TREND column for column. Stock is what that day reported: blank means no file from that store that day, not that it held nothing." });

    // ── masthead actions ─────────────────────────────────────────────────────────────────────
    function exportAs(fmt) {
      if (!st.dims.length) { M.toast("Pick at least one Group by dimension first.", "bad"); return; }
      M.toast(fmt === "csv" ? "Building CSV…" : "Building Excel…");
      var name = (report ? report.slug.toUpperCase().replace(/-/g, "_") : "DAY_WISE_SALE_STOCK_TREND") + "_" + st.from + "_to_" + st.to + "." + fmt;
      M.download("POST", "/api/v1/mis/day-trend/export?fmt=" + fmt, body(), name).then(function (nm) { M.toast("Downloaded " + nm, "ok"); }, function (e) { M.toast(e.message, "bad"); });
    }
    var actions = [el("button", { type: "button", "class": "btn", onclick: function () { reload(); } }, [M.icon("refresh", 15), "Refresh"])];
    if (ctx.me.can.export) {
      actions.push(el("button", { type: "button", "class": "btn primary", title: "The whole selection, in the workbook's own layout: each date merged above its block, measure names below.", onclick: function () { exportAs("xlsx"); } }, [M.icon("down", 15), "Excel"]));
      actions.push(el("button", { type: "button", "class": "btn", onclick: function () { exportAs("csv"); } }, [M.icon("down", 15), "CSV"]));
    }
    if (ctx.me.can.manage && !report) actions.push(el("button", { type: "button", "class": "btn", onclick: saveDialog }, [M.icon("save", 15), "Save as report…"]));
    if (ctx.me.can.manage && report && mine) actions.push(el("a", { "class": "btn", style: { textDecoration: "none" }, href: "/mis/day-trend-reports?edit=" + encodeURIComponent(report.slug) }, [M.icon("edit", 15), "Edit report"]));
    if (ctx.me.can.dumps) actions.push(el("a", { "class": "btn", style: { textDecoration: "none" }, href: "/mis/dumps?new=day_trend" + (report ? "&target=" + encodeURIComponent(report.slug) : ""), title: "Write this sheet to a folder on a schedule (Dump Scheduler)." }, [M.icon("clock", 15), "Schedule a dump…"]));
    M.setHeader({ actions: actions });

    // ── filter bar ───────────────────────────────────────────────────────────────────────────
    var fFrom = el("input", { type: "date", value: st.from, "aria-label": "From date" }), fTo = el("input", { type: "date", value: st.to, "aria-label": "To date" });
    function dates() { st.from = fFrom.value; st.to = fTo.value; paintCover(); M.rangeHealth(health, st.from, st.to); cp.clearCache(); reload(); }
    fFrom.addEventListener("change", dates); fTo.addEventListener("change", dates);
    var nDays = (rdef && rdef.days) || meta.defaultDays || 30;
    var lastN = el("button", { type: "button", "class": "btn", text: "Last " + nDays + " days", onclick: function () {
      if (!meta.latest) return; fTo.value = meta.latest; fFrom.value = addDays(meta.latest, -(nDays - 1)); dates(); } });
    var search = el("input", { type: "search", placeholder: "article no. or description…", "aria-label": "Search", style: { width: "230px" } });
    search.addEventListener("input", M.debounce(function () { st.search = search.value.trim(); reload(); }, 400));
    var summary = el("span", { "class": "muted small", style: { paddingBottom: "8px" } });
    function paintSummary() {
      var sp = span(), bw = (res && res.dayCols ? res.dayCols : meta.dayCols || []).length || 7, nd = (res && res.dims ? res.dims : st.dims).length;
      summary.textContent = sp > 0 ? sp + " day" + (sp === 1 ? "" : "s") + " · " + M.fmt(nd + sp * bw, "int") + " columns" + (res ? " · " + M.fmt(res.total, "int") + " rows" : "") : "";
    }
    var card = el("div", { "class": "card" });
    card.appendChild(el("div", { "class": "fbar" }, [el("label", { "class": "f" }, ["From date", fFrom]), el("label", { "class": "f" }, ["To date", fTo]), lastN,
      el("label", { "class": "f" }, ["Search", search]), el("span", { "class": "spacer" }), summary]));
    var cb = el("div", { "class": "card-b stack" }); card.appendChild(cb);
    ctx.main.appendChild(card);

    // grain / tags / measures: pickers, or the report's locked picks
    if (report) {
      cb.appendChild(el("div", { "class": "grid12" }, [["Group by", st.dims.join(" × ")], ["Stock tags", st.tags.join(" × ")], ["Sale measures", st.meas.map(measLabel).join(" × ")]].map(function (x) {
        return el("div", { "class": "c4 rcard", style: { padding: "10px 12px", gap: "4px" } }, [el("div", { "class": "section-title" }, [M.icon("info", 13), x[0] + " · fixed by the report"]), el("div", { "class": "small", text: x[1] || "—" })]);
      })));
    } else {
      var clDims = M.checklist(dimGroups, st.dims, { height: "230px", prettify: false, placeholder: "Find a dimension", onChange: function (v) { st.dims = pickOrder(st.dims, v); cp.setContext({ grouped: st.dims.slice(), offBy: offBy() }); reloadSoon(); } });
      var clTags = M.checklist([{ items: meta.stockTags || [] }], st.tags, { height: "230px", prettify: false, placeholder: "Find a stock tag", onChange: function (v) { st.tags = pickOrder(st.tags, v); tagsChanged(); reloadSoon(); } });
      var clMeas = M.checklist([{ items: (meta.measures || []).map(function (m) { return { value: m, label: measLabel(m) }; }) }], st.meas, { height: "230px", prettify: false,
        onChange: function (v) { st.meas = pickOrder(st.meas, v); cp.setContext({ measures: dayTrendMeasConds(st.tags, st.meas) }); reloadSoon(); } });
      var pane = function (t, note, cl) { return el("div", { "class": "c4 stack", style: { gap: "6px" } }, [el("div", { "class": "section-title", text: t }), el("div", { "class": "muted tiny", text: note }), cl.el]); };
      cb.appendChild(el("div", { "class": "grid12" }, [
        pane("Group by", "The grain of a row, in the order you pick it. Fact dimensions are stamped on both facts and cost nothing; store and product ones join their master 1:1.", clDims),
        pane("Stock tags", "Two columns per tag, Q and V, inside every day's block. The same catalogue the Variant Article grid offers - composites, rule tags and the MSA / GRT picks included.", clTags),
        pane("Sale measures", "The day's own sale, never a window - so these columns add up across the sheet.", clMeas)]));
    }

    // V2R plants / stock columns / ET stores-only
    var optRow = el("div", { "class": "row" }), stTotalWrap = el("label", { "class": "row small", style: { gap: "6px" } });
    var v2rNote = el("span", { "class": "muted tiny", text: "applies to the V2R_* stock columns only - no rows are dropped" });
    if (report) {
      optRow.appendChild(el("span", { "class": "small" }, [el("b", { text: "V2R plants: " }), st.v2r.length ? st.v2r.join(", ") : "all plants",
        st.tags.length > 1 ? " · stock columns: " + STOCK_TOTAL[st.stockTotal] : "", st.storeOnly ? " · ET stock: stores only" : "", " (fixed by the report)"]));
    } else {
      var v2r = v2rPicker(meta.v2rPlants || [], st.v2r, function (v) { st.v2r = v; v2rNote.classList.toggle("hidden", !v.length); reload(); });
      var stTotal = el("select", { "aria-label": "Stock columns" }, Object.keys(STOCK_TOTAL).map(function (k) { return el("option", { value: k, text: STOCK_TOTAL[k] }); }));
      stTotal.value = st.stockTotal; stTotal.addEventListener("change", function () { st.stockTotal = stTotal.value; reload(); });
      stTotalWrap.appendChild(el("span", { "class": "muted", text: "Stock columns" })); stTotalWrap.appendChild(stTotal);
      var so = el("input", { type: "checkbox", checked: st.storeOnly });
      so.addEventListener("change", function () { st.storeOnly = so.checked; reload(); });
      optRow.appendChild(el("span", { "class": "section-title", style: { flex: "none" }, text: "V2R plants" }));
      optRow.appendChild(v2r.el); optRow.appendChild(stTotalWrap);
      optRow.appendChild(el("label", { "class": "row small", style: { gap: "6px" }, title: "ET stock tags (0001 … PSA1) count plants whose ST_TYP is STORE and nothing else.\n\nRows are NOT removed - the DCs, hubs and offices keep their rows, their sale and every other column.\n\nThe LQUA (LQ_*, LQL_*, LQB_*, SDC_*, V2R_*), PRD, PTL and HUBPRD tags are NOT affected." },
        [so, "ET stock: stores only", el("span", { "class": "muted", text: "(columns only - no rows are dropped)" })]));
      optRow.appendChild(v2rNote); v2rNote.classList.toggle("hidden", !st.v2r.length);
    }
    cb.appendChild(optRow);
    function tagsChanged() { stTotalWrap.classList.toggle("hidden", st.tags.length < 2); cp.setContext({ measures: dayTrendMeasConds(st.tags, st.meas) }); }
    stTotalWrap.classList.toggle("hidden", st.tags.length < 2);
    var famBox = el("div"); cb.appendChild(famBox);
    function paintFamilies() {
      M.clear(famBox);
      var fams = []; Object.keys((res && res.tagFamilies) || {}).forEach(function (t) { var f = res.tagFamilies[t]; if (fams.indexOf(f) < 0) fams.push(f); });
      if (st.stockTotal !== "off" && fams.length > 1) famBox.appendChild(el("div", { "class": "notice warn" }, [M.icon("info", 16), el("span", { text: "The picked tags come from " + fams.length + " different families (" + fams.join(", ") + "). Those regroup the same stock, so TOTAL counts some of it twice - tags from one family (0001 + 0006, say) are the ones that add up." })]));
    }

    // FILTERS - a value list per dimension, ANDed across dimensions and ORed within one
    var fRow = el("div", { "class": "row" }), fGrid = el("div", { "class": "grid12" });
    cb.appendChild(fRow); cb.appendChild(fGrid);
    var addBtn = addFilterButton(function () {
      return dimGroups.map(function (g) { return { label: g.label, items: g.items.filter(function (c) { return !savedFilters[c]; }) }; }).filter(function (g) { return g.items.length; });
    }, function () { return st.filterDims.slice(); }, function (v) {
      st.filterDims = v; Object.keys(st.filters).forEach(function (k) { if (v.indexOf(k) < 0) delete st.filters[k]; }); paintFilters(); reload();
    });
    function paintFilters() {
      M.clear(fRow);
      fRow.appendChild(el("span", { "class": "section-title", style: { flex: "none" } }, [M.icon("filter", 13), "Filters"])); fRow.appendChild(addBtn);
      Object.keys(savedFilters).forEach(function (k) { var v = savedFilters[k]; fRow.appendChild(el("span", { "class": "chip grey", title: "Saved with the report: " + v.join(", ") }, [el("span", { "class": "t", text: "Locked · " + k + ": " + (v.length === 1 ? v[0] : v.length + " values") })])); });
      var af = activeFilters();
      Object.keys(af).forEach(function (k) { var v = af[k]; fRow.appendChild(el("span", { "class": "chip" }, [el("span", { "class": "t", text: k + ": " + (v.length === 1 ? v[0] : v.length + " values") }),
        el("button", { type: "button", "aria-label": "Clear the " + k + " filter", text: "×", onclick: function () { st.filters[k] = []; if (panels[k]) { panels[k].el.remove(); delete panels[k]; } st.filterDims = st.filterDims.filter(function (x) { return x !== k; }); paintFilters(); reload(); } })])); });
      if (!st.filterDims.length && !Object.keys(savedFilters).length) fRow.appendChild(el("span", { "class": "muted tiny", text: "none - an article-deep grain needs at least one, and every grain is capped at " + M.fmt(meta.maxRows || 400000, "int") + " rows" }));
      // panels: add new, drop removed, keep the rest (and their picks)
      Object.keys(panels).forEach(function (k) { if (st.filterDims.indexOf(k) < 0) { panels[k].el.remove(); delete panels[k]; } });
      st.filterDims.forEach(function (d) {
        if (panels[d]) return;
        var p = filterPanel({ dim: d, range: function () { return { from: st.from, to: st.to }; }, filters: effFilters, value: st.filters[d] || [],
          onChange: function (v) { st.filters[d] = v; paintFilters(); reloadSoon(); },
          onRemove: function () { st.filterDims = st.filterDims.filter(function (x) { return x !== d; }); delete st.filters[d]; paintFilters(); reload(); } });
        panels[d] = p; p.el.classList.add("c3"); fGrid.appendChild(p.el);
      });
      fGrid.classList.toggle("hidden", !st.filterDims.length);
    }
    paintFilters();

    // report's own conditions (locked) + the run-time conditions panel
    if (rdef && ((rdef.dim_filters || []).length || (rdef.col_filters || []).length)) {
      cb.appendChild(el("div", { "class": "row small" }, [el("span", { "class": "muted", text: "Report conditions (locked):" })].concat(
        (rdef.dim_filters || []).concat(rdef.col_filters || []).map(function (c) { return el("span", { "class": "chip grey" }, [el("span", { "class": "t", text: c.col + " " + condText(c) })]); }))));
    }
    function offBy() { return dimGroups.map(function (g) { return [g.label, g.items.filter(function (c) { return st.dims.indexOf(c) < 0; })]; }).filter(function (g) { return g[1].length; }); }
    var cp = condPanel({ grouped: st.dims.slice(), offBy: offBy(), measures: dayTrendMeasConds(st.tags, st.meas),
      loadValues: function (col) { return M.post("/api/v1/mis/day-trend/values", { dim: col, from_date: st.from, to_date: st.to, filters: effFilters() }).then(function (r) { return r.values || []; }); },
      valuesKey: function () { return st.from + "|" + st.to + "|" + JSON.stringify(activeFilters()); },
      note: report ? "for this view only - ANDed with the report's own" : "applied on every run and export",
      emptyText: "No conditions. A condition may name any dimension (grouped or not), a sale measure's total over the window (SALE-Q greater than 0), or a stock tag on the last day (0001-STK-Q greater than 0).",
      onApply: function (rows) { st.condApplied = rows; reload(); } });
    ctx.main.appendChild(cp.el);
    var health = el("div"); ctx.main.appendChild(health); M.rangeHealth(health, st.from, st.to);

    // COVERAGE, per day, above the numbers: a blank stock column can mean nothing held or no file - only this tells them apart.
    var coverBody = el("div", { "class": "row", style: { gap: "5px" } });
    ctx.main.appendChild(el("div", { "class": "card" }, [el("div", { "class": "card-h" }, [el("h2", { text: "Stock coverage" }), el("span", { "class": "note", text: "stores that reported stock each day - a thin day is visible before anyone reads a number off it" })]), el("div", { "class": "card-b" }, coverBody)]));
    function paintCover() {
      M.clear(coverBody); var sp = span();
      if (sp <= 0) { coverBody.appendChild(el("span", { "class": "muted small", text: "Pick a date range." })); return; }
      for (var i = 0; i < sp && i < 400; i++) {
        var d = addDays(st.to, -i), nn = cover[d] || 0;
        coverBody.appendChild(el("span", { "class": "chip " + (nn === 0 ? "bad" : nn < coverMax * 0.9 ? "warn" : "ok"), title: fmtDay(d) + " - " + nn + " store(s) reported stock", text: fmtDay(d).slice(0, 6) + " · " + nn }));
      }
    }
    paintCover();

    // ── the grid ─────────────────────────────────────────────────────────────────────────────
    var gridCard = el("div", { "class": "card" }); ctx.main.appendChild(gridCard);
    var go = dayGridOpts(function () { return res; }, cover);
    grid = M.Grid(gridCard, {
      id: report ? "day-trend-r-" + report.slug : "day-trend", title: report ? report.name : "Day Wise Sale & Stock Trend", limit: 200, filterable: false,
      fetch: function (gs, signal) {
        if (!st.from || !st.to || !st.dims.length) { res = null; return Promise.reject(new Error(st.dims.length ? "Pick a date range." : "Pick at least one Group by dimension.")); }
        var b = body(); b.page = gs.page; b.limit = gs.limit; b.sort_col = gs.sort_col || null; b.sort_dir = gs.sort_dir;
        return M.post("/api/v1/mis/day-trend/result", b, { signal: signal });
      },
      onData: function (d) { res = d; paintSummary(); paintFamilies(); paintTiles(); },
      isKey: go.isKey, format: go.format, label: go.label, groups: go.groups, cell: go.cell,
      exports: ctx.me.can.export ? [{ label: "Excel - the whole selection, workbook layout", fn: function () { exportAs("xlsx"); } }, { label: "CSV - the whole selection", fn: function () { exportAs("csv"); } }] : []
    });
    function paintTiles() {
      var sp = span(), bw = (res && res.dayCols ? res.dayCols : meta.dayCols || []).length || 7, nd = (res && res.dims ? res.dims : st.dims).length;
      var latestN = meta.latest ? cover[meta.latest] : null;
      M.setHeader({ tiles: [
        { k: "Window", v: sp + " day" + (sp === 1 ? "" : "s"), s: st.from && st.to ? fmtDay(st.from) + " → " + fmtDay(st.to) : "" },
        { k: "Rows", v: res ? M.fmt(res.total, "int") : "–", s: res ? "showing " + M.fmt(res.rows.length, "int") + " on this page" : "" },
        { k: "Columns", v: M.fmt(nd + sp * bw, "int"), s: bw + " per day · " + nd + " grain" },
        { k: "Grain", v: st.dims.length ? st.dims.join(" × ") : "–", s: st.tags.length + " stock tag(s) · " + st.meas.length + " sale measure(s)" },
        { k: "Latest loaded day", v: meta.latest ? fmtDay(meta.latest) : "–", s: latestN != null ? M.fmt(latestN, "int") + " stores reported stock" : "", cls: latestN != null && latestN < coverMax * 0.9 ? "down" : "" }] });
    }
    paintSummary(); paintTiles();

    // ── save as a Day Wise Trend Report (mis.manage) ─────────────────────────────────────────────────
    function saveDialog() {
      if (!st.dims.length || !st.tags.length || !st.meas.length) { M.toast("A report needs a Group by, a stock tag and a sale measure.", "bad"); return; }
      var name = el("input", { type: "text", maxlength: "120", placeholder: "e.g. Store × Maj-Cat 30-day trend" });
      var slugIn = el("input", { type: "text", maxlength: "60", placeholder: "store-majcat-trend", style: { fontFamily: "monospace" } });
      var desc = el("input", { type: "text", maxlength: "500", placeholder: "What this report answers" });
      var days = el("input", { type: "number", min: "1", max: String(meta.maxDays || 31), value: String(Math.max(1, Math.min(span() || 30, meta.maxDays || 31))), style: { width: "90px" } });
      var shared = el("input", { type: "checkbox" }), side = el("input", { type: "checkbox", checked: true });
      var ok = el("button", { type: "button", "class": "btn primary" }, [M.icon("save", 15), "Save report"]), cancel = el("button", { type: "button", "class": "btn", text: "Cancel" });
      var nc = st.condApplied.length, nf = Object.keys(activeFilters()).length;
      var m = M.modal("Save as a Day Wise Trend Report", [
        el("p", { "class": "muted small", style: { margin: 0 }, text: "Saves the grain (" + st.dims.join(" × ") + "), " + st.tags.length + " stock tag(s), " + st.meas.length + " sale measure(s), the V2R plants, the stock-column mode, the stores-only switch" +
          (nf ? ", " + nf + " filter(s)" : "") + (nc ? " and " + nc + " applied condition(s)" : "") + ". Every open reads the last N loaded days, so the report follows the data." }),
        el("div", { "class": "grid12" }, [el("label", { "class": "f c6" }, ["Report name", name]), el("label", { "class": "f c6" }, ["URL (optional - from the name if blank)", slugIn]),
          el("label", { "class": "f c12" }, ["Description", desc]), el("label", { "class": "f c4" }, ["Days back", days])]),
        el("label", { "class": "row small" }, [shared, "Share with everyone", el("span", { "class": "muted", text: "(otherwise only you see it)" })]),
        el("label", { "class": "row small" }, [side, "Show in sidebar", el("span", { "class": "muted", text: "(off keeps it on the reports page only)" })])], [cancel, ok]);
      cancel.addEventListener("click", function () { m.close(); });
      ok.addEventListener("click", function () {
        if (!name.value.trim()) { name.focus(); M.toast("Enter a report name.", "bad"); return; }
        var d = condsToDef(st.condApplied, allDims);
        var def = { group_by: st.dims, stock_tags: st.tags, measures: st.meas, filters: activeFilters(), v2r_plants: st.v2r, store_only: !!st.storeOnly,
                    stock_total: st.stockTotal, days: Math.max(1, Math.min(Number(days.value) || 30, meta.maxDays || 31)), col_filters: d.col_filters,
                    dim_filters: d.dim_filters.concat(Object.keys(d.filters).map(function (c) { return { col: c, op: "in", vals: d.filters[c] }; })) };
        M.action(ok, "Saving…", function () {
          return M.post("/api/v1/mis/day-trend-report/reports", { report_id: null, name: name.value.trim(), slug: slugIn.value.trim() || null, description: desc.value.trim(),
            definition: def, is_shared: shared.checked, show_in_sidebar: side.checked });
        }, "Report saved.").then(function (r) { m.close(); var s = r && r.report && r.report.slug; if (s) M.go("/mis/day-trend-reports/r/" + encodeURIComponent(s)); }, function () { /* toast shown */ });
      });
    }

    return grid.applyDefaultView().then(function () { if (ctx.alive()) return grid.reload(true); });
  }
})();

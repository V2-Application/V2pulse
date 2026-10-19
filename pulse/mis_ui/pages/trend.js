// Sale Trend / Stock Trend (DFS /sf-mis-sale-trend + /sf-mis-stock-trend, SfMisTrend.jsx): the MIS facts over a date
// range chopped into buckets that do not overlap (day / week / month / quarter / year), pivoted (one row per grouping,
// a column per bucket x measure plus a Total) or long (one row per grouping x bucket). The pivot is done in Snowflake.
// Stock is a position, not a flow, so the Stock Trend carries a basis (period close / average of reported days) and
// storage-location tags (KPI). Conditions panel = dimension conditions applied INSIDE the scan (dim_filters); grid
// header tick-lists on a grouped dimension = `filters` (inside the scan); everything else from the header (Period,
// period / total / measure columns, text and numeric conditions) = `col_filters` on the finished columns.
// API: /api/v1/mis/trend/{catalogue, run, values, export}.
(function () {
  "use strict";
  var M = window.MIS, el = M.el;

  var MEAS_LABEL = { SALE_V: "Sale V", SALE_Q: "Sale Q", GM_V: "GM V", DISCOUNT_V: "Disc V", STOCK_V: "Stk V", STOCK_Q: "Stk Q" };
  var BUCKET_LABEL = { day: "Day", week: "Week (Mon)", month: "Month", quarter: "Quarter", year: "Year" };
  var BASIS_LABEL = { close: "Period close - last reported day", avg: "Average of reported days" };

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

  // ── column helpers ───────────────────────────────────────────────────────────────────────────
  function isMetricCol(c, measures) { return (c.indexOf("P_") === 0 && c.indexOf("__") > 0) || c.indexOf("TOTAL__") === 0 || measures.indexOf(c) >= 0; }
  function headLabel(c, totalAgg) {
    if (c === "PERIOD") return "Period";
    if (c.indexOf("TOTAL__") === 0) { var m = c.slice(7); return (totalAgg === "AVG" ? "Avg" : "Total") + " · " + (MEAS_LABEL[m] || m); }
    if (c.indexOf("P_") === 0 && c.indexOf("__") > 0) { var i = c.indexOf("__"); var mm = c.slice(i + 2); return c.slice(2, i) + " · " + (MEAS_LABEL[mm] || mm); }
    return MEAS_LABEL[c] || c;
  }
  function daysBetween(a, b) { return Math.round((Date.parse(b) - Date.parse(a)) / 864e5) + 1; }

  // ════════════════════════════════════════════════════════════════════════════════════════════
  function trendPage(kind) {
    var isStock = kind === "stock", title = isStock ? "Stock Trend" : "Sale Trend";
    return { render: function (ctx) {
      var pk = "trend." + kind, saved = M.store.get(pk, null) || {}, y = M.addDays(M.today(), -1);
      var st = { from: saved.from || M.addDays(y, -30), to: saved.to || y, bucket: saved.bucket || "week", layout: saved.layout || "pivot",
                 basis: saved.basis || "close", dims: saved.dims || ["ST_CD"], meas: saved.meas || (isStock ? ["STOCK_V", "STOCK_Q"] : ["SALE_V", "SALE_Q"]),
                 tags: saved.tags || (isStock ? ["0001"] : []), openCfg: saved.openCfg !== false, conds: saved.conds || [], scaled: !!saved.scaled, chartMeas: saved.chartMeas || null };
      var res = null, cat = null, valCache = {}, grid = null;
      function persist() { M.store.set(pk, { from: st.from, to: st.to, bucket: st.bucket, layout: st.layout, basis: st.basis, dims: st.dims, meas: st.meas,
                                             tags: st.tags, openCfg: st.openCfg, conds: st.conds, scaled: st.scaled, chartMeas: st.chartMeas }); }
      function skelTiles() {
        M.setHeader({ tiles: [{ k: "Range", skel: true }, { k: "Buckets", skel: true }, { k: "Rows", skel: true }, { k: isStock ? "Stock locations" : "Measures", skel: true }] });
      }
      M.setHeader({ title: title, crumbs: [{ label: title, href: isStock ? "/mis/stock-trend" : "/mis/sale-trend" }],
        sub: isStock ? "Stock position across the range, bucket by bucket. A position is not a flow - pick the basis below."
                     : "Sale, GM and discount across the range, bucket by bucket. Buckets do not overlap, so they add back up to the range." });
      skelTiles();

      return M.get("/api/v1/mis/trend/catalogue").then(function (c) {
        if (!ctx.alive()) return;
        cat = c;
        // KPI (the storage tag) is a stock DIMENSION as well as a filter, so a stock trend can be broken out BY location.
        var dimGroups = Object.keys(cat.dims || {}).map(function (g) {
          return { label: g, items: (g === "Fact" && isStock ? [cat.stockDim] : []).concat(cat.dims[g] || []) };
        });
        var allDims = [].concat.apply([], dimGroups.map(function (g) { return g.items; }));
        var measCat = (isStock ? cat.stockMeasures : cat.saleMeasures) || [];
        st.dims = st.dims.filter(function (d) { return allDims.indexOf(d) >= 0; });
        st.meas = st.meas.filter(function (m) { return measCat.indexOf(m) >= 0; });
        if (isStock) st.tags = st.tags.filter(function (t) { return (cat.stockTags || []).indexOf(t) >= 0; });
        function measList() { return (res && res.definition && res.definition.measures) || st.meas; }

        // ── filter bar ──────────────────────────────────────────────────────────────────────
        var health = el("div");
        var dr = M.dateRange(st.from, st.to, function (v) { st.from = v.from; st.to = v.to; valCache = {}; cp.clearCache(); M.rangeHealth(health, st.from, st.to); shapeChanged(false); });
        var bucket = el("select", { "aria-label": "Bucket", title: "How the range is chopped up. The buckets do not overlap, so a coarser bucket is the same data with fewer columns - never a different number." },
          (cat.buckets || ["day", "week", "month", "quarter", "year"]).map(function (b) { return el("option", { value: b, text: BUCKET_LABEL[b] || b }); }));
        bucket.value = st.bucket;
        bucket.addEventListener("change", function () { st.bucket = bucket.value; shapeChanged(true); });
        var layoutSeg = el("div", { "class": "seg", role: "group", "aria-label": "Layout" }, [
          el("button", { type: "button", "data-v": "pivot", text: "Pivot", title: "One row per grouping, a column per bucket - the shape you read across." }),
          el("button", { type: "button", "data-v": "long", text: "Unpivot", title: "One row per grouping per bucket - the long shape a chart or a PivotTable is fed." })]);
        function paintSeg() { layoutSeg.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-v") === st.layout)); }); }
        layoutSeg.querySelectorAll("button").forEach(function (b) { b.addEventListener("click", function () { if (st.layout === b.getAttribute("data-v")) return; st.layout = b.getAttribute("data-v"); paintSeg(); shapeChanged(true); }); });
        paintSeg();
        var basis = isStock ? el("select", { "aria-label": "Stock basis", title: "Stock is a POSITION, so a bucket cannot be the sum of its days - that would be stock-days.\n\nPeriod close: the position on the last day EACH STORE reported inside the bucket, per storage tag.\nAverage: the daily total averaged over the days that reported. A day with no row is absent, not zero." },
          (cat.stockBases || ["close", "avg"]).map(function (b) { return el("option", { value: b, text: BASIS_LABEL[b] || b }); })) : null;
        if (basis) { basis.value = st.basis; basis.addEventListener("change", function () { st.basis = basis.value; shapeChanged(false); }); }
        var scaled = el("input", { type: "checkbox", checked: st.scaled });
        scaled.addEventListener("change", function () { st.scaled = scaled.checked; persist(); grid.reload(); });
        var cfgBtn = el("button", { type: "button", "class": "btn", "aria-expanded": String(st.openCfg) }, [M.icon("gear", 15), "Group by & measures"]);
        var fbar = el("div", { "class": "fbar" }, [dr.el, el("label", { "class": "f" }, ["Bucket", bucket]), el("div", { role: "group", "aria-label": "Layout", style: { display: "flex", flexDirection: "column", gap: "4px" } }, [
            el("span", { style: { fontSize: "11.5px", color: "var(--muted)", fontWeight: "600", letterSpacing: ".03em", textTransform: "uppercase" }, text: "Layout" }), layoutSeg]),
          basis ? el("label", { "class": "f" }, ["Stock basis", basis]) : null,
          el("label", { "class": "row small", style: { gap: "6px", paddingBottom: "8px" }, title: "Quantities in thousands, values in lakhs - the same divisor pair the report grid uses." }, [scaled, "K / L"]),
          el("span", { "class": "spacer" }), cfgBtn]);

        // ── group by / measures / stock locations ─────────────────────────────────────────────
        var clDims = M.checklist(dimGroups, st.dims, { height: "340px", prettify: false, placeholder: "Search ~160 dimensions",
          onChange: function (v) { st.dims = pickOrder(st.dims, v); shapeChangedSoon(true); } });
        var clMeas = M.checklist([{ items: measCat.map(function (m) { return { value: m, label: m + "  (" + (MEAS_LABEL[m] || m) + ")" }; }) }], st.meas, { height: "340px", prettify: false,
          onChange: function (v) { st.meas = measCat.filter(function (m) { return v.indexOf(m) >= 0; }); shapeChangedSoon(true); } });
        var clTags = isStock ? M.checklist([{ items: cat.stockTags || [] }], st.tags, { height: "340px", prettify: false, placeholder: "Search storage locations",
          onChange: function (v) { st.tags = pickOrder(st.tags, v); shapeChangedSoon(false); } }) : null;
        function pane(t, note, cl, cls) { return el("div", { "class": cls + " stack", style: { gap: "6px" } }, [el("div", { "class": "section-title", text: t }), note ? el("div", { "class": "muted tiny", text: note }) : null, cl.el]); }
        var cfgBody = el("div", { "class": "card-b grid12" + (st.openCfg ? "" : " hidden") }, [
          pane("Group by", "Fact, then the store master, then the product master. Rows come out in the order you pick.", clDims, isStock ? "c4" : "c6"),
          pane("Measures", isStock ? "Stock value and quantity." : "Sale, GM and discount.", clMeas, isStock ? "c4" : "c6"),
          clTags ? pane("Stock locations (KPI)", "Storage tags in scope. Add KPI to Group by to see them apart.", clTags, "c4") : null]);
        cfgBtn.addEventListener("click", function () { st.openCfg = !st.openCfg; cfgBody.classList.toggle("hidden", !st.openCfg); cfgBtn.setAttribute("aria-expanded", String(st.openCfg)); persist(); });
        ctx.main.appendChild(el("div", { "class": "card" }, [fbar, cfgBody]));
        ctx.main.appendChild(health);
        M.rangeHealth(health, st.from, st.to);

        // ── conditions ─────────────────────────────────────────────────────────────────────────
        function offBy() { return dimGroups.map(function (g) { return [g.label, g.items.filter(function (c) { return st.dims.indexOf(c) < 0; })]; }).filter(function (g) { return g[1].length; }); }
        function loadDimValues(col) {
          var key = col + "|" + st.from + "|" + st.to + "|" + (isStock ? st.tags.join(",") : "");
          if (valCache[key]) return valCache[key];
          var body = { kind: kind, column: col, from_date: st.from, to_date: st.to }; if (isStock) body.stock_tags = st.tags;
          valCache[key] = M.post("/api/v1/mis/trend/values", body).then(function (r) { return r.values || []; }, function (e) { delete valCache[key]; throw e; });
          return valCache[key];
        }
        var cp = condPanel({ grouped: st.dims.slice(), offBy: offBy(), measures: [], initial: st.conds, loadValues: loadDimValues,
          valuesKey: function () { return kind + "|" + st.from + "|" + st.to; }, note: "applied inside the scan - on every run and export",
          emptyText: "No conditions - the trend covers every row in the range. A condition may name any dimension in the catalogue, grouped or not; measure and period columns are filtered from the grid header below.",
          onApply: function (rows) { st.conds = rows; persist(); paintNotes(); grid.reload(true); } });
        ctx.main.appendChild(cp.el);
        var notesBox = el("div", { "class": "stack" });
        ctx.main.appendChild(notesBox);
        function paintNotes() {
          M.clear(notesBox);
          var bad = st.conds.filter(function (c) { return c.col && allDims.indexOf(c.col) < 0; }).map(function (c) { return c.col; });
          if (bad.length) notesBox.appendChild(el("div", { "class": "notice warn" }, [M.icon("info", 16), el("span", { text: bad.join(", ") + " is not a dimension of this trend, so that condition is not being applied. Remove it or pick another column." })]));
          if (isStock && st.tags.length > 1 && st.dims.indexOf("KPI") < 0) notesBox.appendChild(el("div", { "class": "notice warn" }, [M.icon("info", 16),
            el("span", { text: st.tags.length + " stock locations are summed into each cell. The LQ_, LQL_ and plain numeric families OVERLAP - each regroups the same warehouse stock - so mixing them double-counts. Add KPI to Group by to see them apart." })]));
          if (isStock && !st.tags.length) notesBox.appendChild(el("div", { "class": "notice bad", text: "Pick at least one stock location." }));
          if (!st.meas.length) notesBox.appendChild(el("div", { "class": "notice bad", text: "Pick at least one measure." }));
          if (!st.dims.length) notesBox.appendChild(el("div", { "class": "notice", text: "No Group by picked - the trend is one grand-total row." }));
        }
        paintNotes();

        // ── chart of the period totals (pivot only, derived from the rows on screen) ───────────
        var chartBox = el("div", { "class": "card-b" }), chartSeg = el("div", { "class": "seg", role: "group", "aria-label": "Chart measure" }), chartNote = el("span", { "class": "note" });
        var chartCard = el("div", { "class": "card hidden" }, [el("div", { "class": "card-h" }, [el("h2", { text: "Period totals" }), chartNote, el("span", { "class": "spacer" }), chartSeg]), chartBox]);
        ctx.main.appendChild(chartCard);
        function drawChart() {
          var show = res && res.definition && res.definition.layout === "pivot" && (res.rows || []).length && (res.periods || []).length > 1;
          chartCard.classList.toggle("hidden", !show); if (!show) return;
          var ml = measList(), m = ml.indexOf(st.chartMeas) >= 0 ? st.chartMeas : ml[0];
          M.clear(chartSeg);
          ml.forEach(function (x) { chartSeg.appendChild(el("button", { type: "button", "aria-pressed": String(x === m), text: MEAS_LABEL[x] || x, onclick: function () { st.chartMeas = x; persist(); drawChart(); } })); });
          var vals = res.periods.map(function (p) {
            var ci = res.columns.indexOf("P_" + p + "__" + m), s = 0, any = false; if (ci < 0) return null;
            res.rows.forEach(function (r) { var v = r[ci]; if (v !== null && v !== "" && v !== undefined && isFinite(Number(v))) { s += Number(v); any = true; } });
            return any ? s : null;
          });
          // charts.lines prints label.slice(5) on the axis (made for ISO days); a year bucket would print nothing.
          var labels = res.periods.map(function (p) { return res.definition.bucket === "year" ? "Year " + p : p; });
          var tb = el("tbody");
          res.periods.forEach(function (p, i) {
            var v = vals[i], prev = i ? vals[i - 1] : null, ch = v != null && prev ? (v - prev) / Math.abs(prev) * 100 : null;
            tb.appendChild(el("tr", {}, [el("td", { style: { padding: "3px 8px 3px 0" }, text: p }), el("td", { "class": "num", style: { padding: "3px 8px", textAlign: "right" }, text: v == null ? "–" : M.compact(v) }),
              el("td", { "class": "num tiny", style: { padding: "3px 0", textAlign: "right", color: ch == null ? "var(--muted)" : ch < 0 ? "var(--bad)" : "var(--ok)" }, text: ch == null ? "" : (ch > 0 ? "+" : "") + ch.toFixed(1) + "%" })]));
          });
          M.clear(chartBox).appendChild(el("div", { "class": "grid12" }, [
            el("div", { "class": "c8" }, M.charts.lines([{ name: (MEAS_LABEL[m] || m) + (isStock && res.definition.stock_basis === "avg" ? " (avg)" : ""), values: vals }], { labels: labels, height: 300, title: "Period totals of " + m })),
            el("div", { "class": "c4", style: { maxHeight: "300px", overflow: "auto" } }, el("table", { "class": "small", style: { width: "100%", borderCollapse: "collapse" } }, [
              el("thead", {}, el("tr", { "class": "muted tiny" }, [el("th", { style: { textAlign: "left", fontWeight: "600" }, text: BUCKET_LABEL[res.definition.bucket] || "Period" }), el("th", { style: { textAlign: "right", fontWeight: "600" }, text: MEAS_LABEL[m] || m }), el("th", { style: { textAlign: "right", fontWeight: "600" }, text: "vs prev" })])), tb]))]));
          chartNote.textContent = (res.total > res.rows.length ? "the " + M.fmt(res.rows.length, "int") + " rows on this page (of " + M.fmt(res.total, "int") + ")" : "all " + M.fmt(res.total, "int") + " rows") +
            " added up per " + (BUCKET_LABEL[res.definition.bucket] || res.definition.bucket).toLowerCase() + (isStock ? " · " + (BASIS_LABEL[res.definition.stock_basis] || "") : "");
        }

        // ── the grid ───────────────────────────────────────────────────────────────────────────
        function filteredCount(gs) { var n = 0; Object.keys(gs.filters).forEach(function (c) { if (gs.filters[c] && gs.filters[c].length) n++; }); return n + gs.conds.length + dimCondsOnly(st.conds, allDims).length; }
        function body(gs, paged) {
          var b = { kind: kind, from_date: st.from, to_date: st.to, bucket: st.bucket, layout: st.layout, group_by: st.dims, measures: st.meas };
          if (isStock) { b.stock_tags = st.tags; b.stock_basis = st.basis; }
          var filters = {}, colf = [];
          // A tick-list on a grouped DIMENSION goes inside the scan; one on PERIOD or a bucket / measure column does not
          // exist until after the aggregation, so it is applied to the finished column instead.
          Object.keys(gs.filters).forEach(function (c) { var v = gs.filters[c]; if (!v || !v.length) return; if (st.dims.indexOf(c) >= 0) filters[c] = v; else colf.push({ col: c, op: "in", vals: v }); });
          gs.conds.forEach(function (x) { colf.push({ col: x.col, op: x.op, v1: x.v1, v2: x.v2 }); });
          var df = dimCondsOnly(st.conds, allDims);
          if (Object.keys(filters).length) b.filters = filters;
          if (colf.length) b.col_filters = colf;
          if (df.length) b.dim_filters = df;
          if (gs.sort_col) { b.sort_col = gs.sort_col; b.sort_dir = gs.sort_dir; }
          if (paged) { b.page = gs.page; b.limit = gs.limit; }
          return b;
        }
        function doExport(gs) {
          if (!st.meas.length || (isStock && !st.tags.length)) { M.toast("Pick at least one measure" + (isStock ? " and one stock location" : "") + " first.", "bad"); return; }
          M.toast(filteredCount(gs) ? "Exporting the rows the grid is showing - the filters travel with it…" : "Exporting every row of this trend, in the layout on screen…");
          M.download("POST", "/api/v1/mis/trend/export", body(gs, false), kind + "_trend_" + st.bucket + "_" + st.layout + "_" + st.from + "_to_" + st.to + ".csv")
            .then(function (n) { M.toast("Downloaded " + n, "ok"); }, function (e) { M.toast(e.message, "bad"); });
        }
        var gridCard = el("div", { "class": "card" });
        ctx.main.appendChild(gridCard);
        grid = M.Grid(gridCard, {
          id: kind + "-trend", title: title, limit: 200, sort_dir: "desc",
          fetch: function (gs, signal) {
            if (!st.meas.length || (isStock && !st.tags.length)) { res = null; return Promise.resolve({ columns: [], rows: [], total: 0 }); }
            return M.post("/api/v1/mis/trend/run", body(gs, true), { signal: signal });
          },
          onData: function (d, gs) { res = d && d.columns && d.columns.length ? d : null; paintTiles(gs); drawChart(); },
          values: function (c) {
            if (c === "PERIOD") return Promise.resolve((res && res.periods) || []);
            if (isMetricCol(c, measList())) return Promise.reject(new Error("a number column - use a condition above"));
            return loadDimValues(c);
          },
          isKey: function (c) { return !isMetricCol(c, measList()); },
          format: function (c) { return isMetricCol(c, measList()) ? undefined : "text"; },
          label: function (c) { return headLabel(c, res && res.totalAgg); },
          cell: function (td, c, v) {
            if (c.indexOf("TOTAL__") === 0) { td.style.fontWeight = "600"; td.style.background = "var(--brand-soft)"; }
            if (v === null || v === undefined || v === "") { td.textContent = "–"; td.classList.add("muted"); if (isMetricCol(c, measList())) td.style.textAlign = "right"; return; }
            if (st.scaled && isMetricCol(c, measList()) && isFinite(Number(v))) {
              // Quantities in thousands, values in lakhs - the report grid's divisor pair.
              td.textContent = M.fmt(Number(v) / (/_Q$/.test(c) ? 1000 : 100000), "dec");
            }
          },
          getExtra: function () { return { from: st.from, to: st.to, bucket: st.bucket, layout: st.layout, basis: st.basis, dims: st.dims, meas: st.meas, tags: st.tags, conds: st.conds, scaled: st.scaled }; },
          setExtra: function (x) { applyCfg(x); },
          exports: ctx.me.can.export ? [{ label: "CSV - every row, in the layout on screen (filters travel with it)", fn: doExport }] : []
        });

        function paintTiles(gs) {
          var days = daysBetween(st.from, st.to), tiles = [{ k: "Range", v: M.fmt(days, "int") + " day" + (days === 1 ? "" : "s"), s: M.fmtDate(st.from) + " → " + M.fmtDate(st.to) }];
          if (!res) {
            tiles.push({ k: "Buckets", v: "–", s: BUCKET_LABEL[st.bucket] }, { k: "Rows", v: "–", s: "nothing run" });
          } else {
            var nf = filteredCount(gs);
            tiles.push({ k: "Buckets", v: M.fmt(res.periods.length, "int"), s: (BUCKET_LABEL[res.definition.bucket] || "").toLowerCase() + " · " + (res.definition.layout === "pivot" ? "pivot" : "unpivot") });
            tiles.push({ k: "Rows", v: M.fmt(res.total, "int"), s: nf ? "filtered on " + nf + " column" + (nf > 1 ? "s" : "") : (res.definition.group_by.length ? "by " + res.definition.group_by.join(" × ") : "grand total") });
            if (res.definition.layout === "pivot") {
              res.definition.measures.slice(0, 2).forEach(function (m) {
                var ci = res.columns.indexOf("TOTAL__" + m), s = 0; if (ci < 0) return;
                res.rows.forEach(function (r) { var v = Number(r[ci]); if (isFinite(v)) s += v; });
                tiles.push({ k: (res.totalAgg === "AVG" ? "Avg · " : "Total · ") + (MEAS_LABEL[m] || m), v: M.compact(s), s: res.total > res.rows.length ? "rows on this page" : "all rows" });
              });
            }
          }
          if (isStock) tiles.push({ k: "Stock locations", v: M.fmt(st.tags.length, "int"), s: BASIS_LABEL[st.basis] });
          else tiles.push({ k: "Measures", v: M.fmt(st.meas.length, "int"), s: st.meas.map(function (m) { return MEAS_LABEL[m] || m; }).join(", ") });
          M.setHeader({ tiles: tiles });
        }
        M.setHeader({ actions: ctx.me.can.export ? [el("button", { type: "button", "class": "btn", onclick: function () { doExport(grid.state()); } }, [M.icon("down", 15), "Export CSV"])] : [] });

        // Changing the shape renames the period columns (bucket / layout) or removes a dimension, so header filters on a
        // column that has gone are pruned - a filter nobody can see must not keep narrowing the grid.
        function pruneHeader() {
          var gs = grid.state(), ml = measList();
          function keep(c) { return !(c === "PERIOD" || c.indexOf("P_") === 0 || c.indexOf("TOTAL__") === 0) && (ml.indexOf(c) >= 0 || st.dims.indexOf(c) >= 0); }
          var f = {}; Object.keys(gs.filters).forEach(function (c) { if (keep(c)) f[c] = gs.filters[c]; });
          grid.setState({ filters: f, conds: gs.conds.filter(function (x) { return keep(x.col); }), sort_col: gs.sort_col && keep(gs.sort_col) ? gs.sort_col : null });
        }
        function shapeChanged(prune) {
          persist(); paintNotes();
          cp.setContext({ grouped: st.dims.slice(), offBy: offBy() });
          if (prune) pruneHeader();
          grid.reload(true);
        }
        var shapeChangedSoon = (function () { var t = null, pr = false; return function (prune) { pr = pr || prune; clearTimeout(t); t = setTimeout(function () { var p = pr; pr = false; shapeChanged(p); }, 700); }; })();
        function applyCfg(x) {
          if (!x) return;
          ["from", "to", "bucket", "layout", "basis", "dims", "meas", "tags", "conds", "scaled"].forEach(function (k) { if (x[k] !== undefined && x[k] !== null) st[k] = clone(x[k]); });
          dr.set(st.from, st.to); bucket.value = st.bucket; paintSeg(); if (basis) basis.value = st.basis; scaled.checked = st.scaled;
          clDims.set(st.dims); clMeas.set(st.meas); if (clTags) clTags.set(st.tags);
          cp.setContext({ grouped: st.dims.slice(), offBy: offBy() }); cp.set(st.conds);
          valCache = {}; M.rangeHealth(health, st.from, st.to); persist(); paintNotes();
        }

        return grid.applyDefaultView().then(function () { if (ctx.alive()) return grid.reload(true); });
      });
    } };
  }

  M.page("sale-trend", trendPage("sale"));
  M.page("stock-trend", trendPage("stock"));
})();

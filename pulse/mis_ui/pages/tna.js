// TNA Report (DFS /sf-mis-tna, SfTnaReport.jsx): V2RETAIL.BRONZE.SRM_TNA_REPORT, the SRM portal's Time-and-Action
// extract - one row per PENDING PO line and one block of columns per production STAGE, in the order the goods move
// through them. Group by / measures / stages / stage columns are picked; value filters (ANDed across dimensions,
// ORed within one, run BEFORE the grouping) and number conditions (a HAVING over the group, run AFTER it) are
// separate on purpose. API: /api/v1/mis/tna/{params, result, values, export?fmt=xlsx|csv}.
(function () {
  "use strict";
  var M = window.MIS, el = M.el, API = "/api/v1/mis/tna";
  // The stage columns that hold a NUMBER. BUD / ACT / STATUS are text (a date or a word).
  var NUM_STAGE_COLS = { DELAY: 1, DONE: 1, PEND: 1 };
  var OPS = [">", ">=", "<", "<=", "=", "!="];
  // grid header condition ops -> the engine's operators
  var GRID_OPS = { gt: ">", gte: ">=", lt: "<", lte: "<=", eq: "=", ne: "!=" };

  function note(text) { return el("p", { "class": "muted tiny", style: { margin: "6px 0 0" }, text: text }); }

  M.page("tna", {
    render: function (ctx) {
      var st = { dims: [], meas: [], stages: [], stageCols: [], basis: "", from: "", to: "", search: "", filterDims: [], conds: [] };
      var meta = null, lastRes = null;
      var dumpBtn = ctx.me.can.dumps ? el("button", { type: "button", "class": "btn", onclick: function () { M.go("/mis/dumps?new=tna"); } }, [M.icon("clock", 15), "Schedule a dump…"]) : null;
      var refreshBtn = el("button", { type: "button", "class": "btn", title: "Re-read the extract's counts and load stamp" }, [M.icon("refresh", 15), "Refresh stats"]);
      M.setHeader({ title: "TNA Report",
        sub: "The SRM portal's Time-and-Action extract: one row per pending PO line and one block of columns per stage, in the order the goods move through them. At the default grain (PO × article × store) every column is the line's own value; group by a vendor or a category and the same columns become the aggregate their header names.",
        actions: [refreshBtn, dumpBtn],
        tiles: ["Lines", "Articles", "Pending qty", "Pending value", "Overdue lines", "Stale lines", "Extract loaded"].map(function (k) { return { k: k, skel: true }; }) });

      function tiles() {
        var s = (meta && meta.stats) || {};
        var pct = s.rows ? Math.round(100 * (s.overdueLines || 0) / s.rows) + "% of lines" : "";
        M.setHeader({ tiles: [
          { k: "Lines", v: M.fmt(s.rows, "int"), s: M.fmt(s.pos, "int") + " POs · " + M.fmt(s.stores, "int") + " plants" },
          { k: "Articles", v: M.fmt(s.articles, "int"), s: M.fmt(s.vendors, "int") + " vendors" },
          { k: "Pending qty", v: M.compact(s.pendQty), s: M.fmt(s.pendQty, "int") + " pcs" },
          { k: "Pending value", v: M.compact(s.pendValue), s: "Rs " + M.fmt(s.pendValue, "int") },
          { k: "Overdue lines", v: M.fmt(s.overdueLines, "int"), s: pct, cls: s.overdueLines ? "down" : "" },
          { k: "Stale lines", v: M.fmt(s.staleLines, "int"), s: "not updated in SRM recently" },
          { k: "Extract loaded", v: s.loadedAt ? String(s.loadedAt).replace("T", " ").slice(0, 16) : "–", s: s.loadedAt ? M.ago(s.loadedAt) + " · a snapshot, as at this stamp" : "" }] });
      }

      return M.get(API + "/params").then(function (p) {
        if (!ctx.alive()) return;
        meta = p;
        st.dims = (p.defaultDims || []).slice(); st.meas = (p.defaultMeasures || []).slice();
        st.stages = (p.defaultStages || []).slice(); st.stageCols = (p.defaultStageCols || []).slice();
        st.basis = p.defaultDateBasis || "DELIVERY_DATE";
        tiles();
        refreshBtn.addEventListener("click", function () {
          M.action(refreshBtn, "Reading…", function () {
            return M.get(API + "/params?refresh=1").then(function (q) { if (!ctx.alive()) return; meta = q; tiles(); drawRangeNote(); drawWarnings(); drawFilterNone(); });
          }, "Extract stats refreshed.");
        });

        var reloadSoon = M.debounce(function () { grid.reload(true); }, 500);

        // ── scope bar: date basis + range + search ──────────────────────────────────────────────
        var basis = el("select", { "aria-label": "Date basis", title: "Which of the PO's dates the range applies to. Nothing else changes." },
          (p.dateBases || []).map(function (b) { return el("option", { value: b, selected: b === st.basis, text: b }); }));
        var from = el("input", { type: "date", "aria-label": "From" }), to = el("input", { type: "date", "aria-label": "To" });
        var allDates = el("button", { type: "button", "class": "btn sm hidden", text: "All dates" });
        var search = el("input", { type: "search", placeholder: "PO, article, description, vendor…", style: { minWidth: "260px" }, "aria-label": "Search" });
        function scopeChanged() {
          st.basis = basis.value; st.from = from.value; st.to = to.value;
          allDates.classList.toggle("hidden", !(st.from || st.to));
          resetPanels(); drawWarnings(); grid.reload(true);
        }
        basis.addEventListener("change", scopeChanged); from.addEventListener("change", scopeChanged); to.addEventListener("change", scopeChanged);
        allDates.addEventListener("click", function () { from.value = ""; to.value = ""; scopeChanged(); });
        search.addEventListener("input", M.debounce(function () { st.search = search.value; grid.reload(true); }, 400));
        var rangeNote = el("span", { "class": "muted tiny" });
        function drawRangeNote() {
          var s = meta.stats || {};
          rangeNote.textContent = (s.docFrom ? "PO dates " + s.docFrom + " → " + s.docTo : "") + (s.deliveryFrom ? " · delivery " + s.deliveryFrom + " → " + s.deliveryTo : "") + " · leave both dates empty for the whole extract";
        }
        drawRangeNote();

        // ── the four pickers (DFS PickList panels) ───────────────────────────────────────────────
        function picker(title, groups, value, onChange, noteText) {
          var cl = M.checklist(groups, value, { height: "220px", prettify: false, onChange: onChange });
          return { api: cl, el: el("div", { "class": "c3 card", style: { boxShadow: "none" } }, [el("div", { "class": "card-b" }, [el("div", { "class": "section-title", text: title }), el("div", { style: { height: "6px" } }), cl.el, note(noteText)])]) };
        }
        var dimGroups = (p.dims || []).map(function (g) { return { label: g.label, items: g.cols }; });
        var pDims = picker("Group by", dimGroups, st.dims, function (v) { st.dims = v; drawWarnings(); reloadSoon(); },
          "The grain of a row. PO × article × store is the line itself - every column is then its own value. SM_* and PM_* are the store and product masters, joined on STORE_CODE and ARTICLE_NUMBER; the unprefixed hierarchy is SRM's own stamp on the PO, and the two do disagree.");
        var pMeas = picker("Measures", [{ items: (p.measures || []).map(function (m) { return { value: m, label: (p.measureLabels || {})[m] || m }; }) }], st.meas,
          function (v) { st.meas = v; drawCondFields(); reloadSoon(); }, "Sums are sums; anything averaged or maxed says so in its column name.");
        function stageLabelOf(s) { var n = (meta.stageUse || {})[s], nm = (meta.stageLabels || {})[s] || s; return n === 0 ? nm + " · unused" : nm; }
        var pStages = picker("Stages", [{ items: (p.stages || []).map(function (s) { return { value: s, label: stageLabelOf(s), count: (p.stageUse || {})[s] }; }) }], st.stages,
          function (v) { st.stages = v; drawCondFields(); reloadSoon(); }, "One block of columns each, in the order the goods move through them. The count is how many lines have ever stamped the stage.");
        var pCols = picker("Stage columns", [{ items: (p.stageCols || []).map(function (c) { return { value: c, label: c + " - " + ((p.stageColNotes || {})[c] || "") }; }) }], st.stageCols,
          function (v) { st.stageCols = v; drawCondFields(); reloadSoon(); }, "What every stage block holds. The five stages with no budgeted date carry no BUD and no DELAY.");

        // ── warnings (DFS amber notes) ──────────────────────────────────────────────────────────
        var warnBox = el("div", { "class": "stack" });
        function drawWarnings() {
          M.clear(warnBox); var s = meta.stats || {};
          if ((st.from || st.to) && (st.basis === "DELIVERY_DATE" || st.basis === "CRNT_SAP_DEL_DT") && s.noDeliveryDate) {
            warnBox.appendChild(el("div", { "class": "notice warn" }, [M.icon("info", 16), el("span", { text: M.fmt(s.noDeliveryDate, "int") + " lines have no " + st.basis +
              " in the extract. A range on it leaves every one of them out - range on DOC_DT or BUDGETED_DATE, or clear the dates, to keep them." })]));
          }
          var sm = meta.storeDims || [], filt = (grid ? grid.state().filters : {}) || {};
          var uses = st.dims.some(function (d) { return sm.indexOf(d) >= 0; }) || Object.keys(filt).some(function (d) { return sm.indexOf(d) >= 0 && filt[d].length; });
          var mix = (meta.plantMix || []).filter(function (x) { return x.type !== "STORE"; });
          var dc = mix.reduce(function (n, x) { return n + x.lines; }, 0);
          if (uses && dc) {
            warnBox.appendChild(el("div", { "class": "notice warn" }, [M.icon("info", 16), el("span", { text: "A PO's STORE_CODE is the plant that will RECEIVE it, not a shop - " +
              mix.map(function (x) { return M.fmt(x.lines, "int") + " " + x.type; }).join(", ") + " of " + M.fmt(s.rows, "int") +
              " lines. Grouping by SM_ZONE or SM_STATE puts those in one NA bucket, correctly. Add SM_ST_TYP to see which is which." })]));
          }
        }

        // ── value filters: one panel per dimension, narrowed by the other filters ────────────────
        var panels = {}, panelBox = el("div", { "class": "row", style: { alignItems: "flex-end" } }), filterNone = el("span", { "class": "muted tiny" });
        function drawFilterNone() { filterNone.textContent = st.filterDims.length ? "" : "none - the whole extract is " + M.fmt((meta.stats || {}).rows, "int") + " lines, so this page opens on all of it"; }
        function setFilter(dim, vals) {
          var f = grid.state().filters; if (vals && vals.length) f[dim] = vals; else delete f[dim];
          grid.setState({ filters: f }); resetPanels(dim); drawWarnings(); grid.reload(true);
        }
        function resetPanels(except) { Object.keys(panels).forEach(function (d) { if (d !== except) panels[d].ms.reset(); }); }
        function loadValues(dim) {
          var f = grid.state().filters, others = {};
          Object.keys(f).forEach(function (k) { if (k !== dim && f[k].length) others[k] = f[k]; });
          return M.post(API + "/values", { dim: dim, date_basis: st.basis, from_date: st.from || null, to_date: st.to || null, filters: others }).then(function (d) {
            var v = d.values || [];
            if (d.truncated) M.toast(dim + ": the list is capped at " + M.fmt(d.cap, "int") + " values - filter something else first to reach the rest.");
            return v;
          });
        }
        function drawPanels() {
          var f = grid ? grid.state().filters : {};
          Object.keys(panels).forEach(function (d) { if (st.filterDims.indexOf(d) < 0) { panels[d].wrap.remove(); delete panels[d]; } });
          st.filterDims.forEach(function (d) {
            if (panels[d]) return;
            var ms = M.multiSelect(d, function () { return loadValues(d); }, f[d] || [], function (v) { setFilter(d, v); });
            var x = el("button", { type: "button", "class": "btn ghost sm", "aria-label": "Remove the " + d + " filter", title: "Remove the " + d + " filter" }, [M.icon("x", 14)]);
            var wrap = el("div", { "class": "row", style: { gap: "2px", alignItems: "flex-end" } }, [ms.el, x]);
            x.addEventListener("click", function () { st.filterDims = st.filterDims.filter(function (y) { return y !== d; }); wrap.remove(); delete panels[d]; drawFilterNone(); setFilter(d, []); });
            panels[d] = { ms: ms, wrap: wrap }; panelBox.appendChild(wrap);
          });
          drawFilterNone();
        }
        var addFilter = el("button", { type: "button", "class": "btn sm" }, [M.icon("plus", 14), "Add a filter"]);
        addFilter.addEventListener("click", function () {
          var cl = M.checklist(dimGroups, st.filterDims, { height: "300px", prettify: false });
          var ok = el("button", { type: "button", "class": "btn primary sm", text: "Apply" });
          M.popover(addFilter, [el("div", { "class": "pop-h" }, [el("b", { text: "Filter on" })]), el("div", { "class": "pop-b" }, [cl.el, note("Pick the dimensions to filter on; each gets its own value list.")]), el("div", { "class": "pop-f" }, [ok])]);
          ok.addEventListener("click", function () {
            var next = cl.get(), dropped = st.filterDims.filter(function (d) { return next.indexOf(d) < 0; });
            st.filterDims = next; M.closePop(); drawPanels();
            if (dropped.length) { var f = grid.state().filters; dropped.forEach(function (d) { delete f[d]; }); grid.setState({ filters: f }); grid.reload(true); }
          });
        });

        // ── number conditions (HAVING, after the grouping) ──────────────────────────────────────
        var condBox = el("div", { "class": "row" });
        function condFields() {
          var out = (meta.measures || []).map(function (m) { return { v: m, label: (meta.measureLabels || {})[m] || m }; });
          st.stages.forEach(function (s) { st.stageCols.forEach(function (c) { if (NUM_STAGE_COLS[c]) out.push({ v: s + " " + c, label: ((meta.stageLabels || {})[s] || s) + " · " + c }); }); });
          return out;
        }
        var condReload = M.debounce(function () { grid.reload(true); }, 600);
        function drawCondFields() {
          M.clear(condBox); var fields = condFields();
          st.conds.forEach(function (c, i) {
            var f = el("select", { "aria-label": "Condition field" }, [el("option", { value: "", text: "field…" })].concat(fields.map(function (x) { return el("option", { value: x.v, selected: x.v === c.field, text: x.label }); })));
            var op = el("select", { "aria-label": "Operator" }, OPS.map(function (o) { return el("option", { value: o, selected: o === c.op, text: o }); }));
            var v = el("input", { type: "number", value: c.value, placeholder: "0", style: { width: "90px" }, "aria-label": "Value" });
            f.addEventListener("change", function () { c.field = f.value; condReload(); });
            op.addEventListener("change", function () { c.op = op.value; condReload(); });
            v.addEventListener("input", function () { c.value = v.value; condReload(); });
            condBox.appendChild(el("span", { "class": "row", style: { gap: "4px", border: "1px solid var(--line)", borderRadius: "8px", padding: "3px 5px" } }, [f, op, v,
              el("button", { type: "button", "class": "btn ghost sm", "aria-label": "Remove condition", onclick: function () { st.conds.splice(i, 1); drawCondFields(); grid.reload(true); } }, [M.icon("x", 13)])]));
          });
          condBox.appendChild(el("button", { type: "button", "class": "btn sm", onclick: function () { st.conds.push({ field: "", op: ">", value: "" }); drawCondFields(); } }, [M.icon("plus", 14), "Add a condition"]));
          if (!st.conds.length) condBox.appendChild(el("span", { "class": "muted tiny", text: "none - a condition tests a measure or a stage's DELAY / DONE / PEND, after the grouping (e.g. vendors whose average cutting delay is over 7 days)" }));
        }

        // ── layout ───────────────────────────────────────────────────────────────────────────────
        var setup = el("div", { "class": "card" }, [
          el("div", { "class": "fbar" }, [el("label", { "class": "f" }, ["Date basis", basis]), el("label", { "class": "f" }, ["From", from]), el("label", { "class": "f" }, ["To", to]), allDates,
            el("label", { "class": "f" }, ["Search", search]), el("span", { "class": "spacer" }), rangeNote]),
          el("div", { "class": "card-b stack" }, [
            el("div", { "class": "grid12" }, [pDims.el, pMeas.el, pStages.el, pCols.el]),
            warnBox,
            el("div", { "class": "row" }, [el("span", { "class": "section-title", style: { minWidth: "90px" }, text: "Filters" }), addFilter, panelBox, filterNone]),
            el("div", { "class": "row" }, [el("span", { "class": "section-title", style: { minWidth: "90px" }, text: "Conditions" }), condBox])])]);
        ctx.main.appendChild(setup);
        var card = el("div", { "class": "card" }); ctx.main.appendChild(card);

        // ── grid ─────────────────────────────────────────────────────────────────────────────────
        function isDim(c) { return ((lastRes && lastRes.dims) || st.dims).indexOf(c) >= 0; }
        function isMeasure(c) { return !!(lastRes && (lastRes.measures || []).indexOf(c) >= 0); }
        function stageOf(c) {
          var stg = (lastRes && lastRes.stages) || [];
          for (var i = 0; i < stg.length; i++) if (String(c).indexOf(stg[i] + " ") === 0) return stg[i];
          return null;
        }
        function isNumCol(c) { return isMeasure(c) || (!!stageOf(c) && !!NUM_STAGE_COLS[String(c).split(" ").pop()]); }
        function gridConds(gs) {
          var out = [];
          (gs.conds || []).forEach(function (c) {
            if (!isNumCol(c.col)) throw new Error("A condition here tests a number - a measure, or a stage's DELAY / DONE / PEND. Filter " + c.col + " with its value list instead.");
            var field = c.col;
            if (isMeasure(c.col)) field = lastRes.measureKeys[lastRes.measures.indexOf(c.col)];
            if (c.op === "between") { out.push({ field: field, op: ">=", value: c.v1 }); out.push({ field: field, op: "<=", value: c.v2 }); return; }
            if (!GRID_OPS[c.op]) throw new Error("The " + c.col + " condition: use >, ≥, <, ≤, = or ≠ (a HAVING over the group).");
            out.push({ field: field, op: GRID_OPS[c.op], value: c.v1 });
          });
          return out;
        }
        function body(gs) {
          var f = {}; Object.keys(gs.filters || {}).forEach(function (k) { if (gs.filters[k] && gs.filters[k].length) f[k] = gs.filters[k]; });
          var conds = st.conds.filter(function (c) { return c.field && c.value !== "" && c.value != null; }).map(function (c) { return { field: c.field, op: c.op, value: c.value }; }).concat(gridConds(gs));
          return { group_by: st.dims, measures: st.meas, stages: st.stages, stage_cols: st.stageCols,
                   filters: Object.keys(f).length ? f : null, conditions: conds.length ? conds : null,
                   date_basis: st.basis, from_date: st.from || null, to_date: st.to || null, search: st.search || null,
                   sort_col: gs.sort_col || null, sort_dir: gs.sort_dir };
        }
        function doExport(fmt, gs) {
          var b; try { b = body(gs); } catch (e) { M.toast(e.message, "bad"); return; }
          M.toast(fmt === "csv" ? "Building the CSV…" : "Building the Excel workbook…");
          M.download("POST", API + "/export?fmt=" + fmt, b, "TNA_REPORT." + fmt).then(function (n) { M.toast("Downloaded " + n, "ok"); }, function (e) { M.toast(e.message, "bad"); });
        }
        var grid = M.Grid(card, {
          id: "tna", title: "PO lines", limit: 200, pageSizes: [100, 200, 500, 1000],
          isKey: isDim,
          format: function (c) { return isNumCol(c) ? "dec" : "text"; },
          groups: function (cols) {
            var out = [];
            cols.forEach(function (c) {
              var s = stageOf(c), lab = s ? ((lastRes.stageLabels || {})[s] || s) + ((meta.stageUse || {})[s] === 0 ? " · never stamped" : "") : (isDim(c) ? "Grain" : "Measures");
              if (out.length && out[out.length - 1].label === lab) out[out.length - 1].span++; else out.push({ label: lab, span: 1 });
            });
            return out;
          },
          cell: function (td, c, v) {
            var blank = v === null || v === undefined || v === "";
            if (blank) { td.textContent = isDim(c) ? "—" : "·"; td.style.color = "var(--muted)"; if (isNumCol(c)) td.classList.add("num"); }
            else if (v === "MIXED") { td.style.color = "var(--warn)"; td.style.fontWeight = "600"; td.title = "The group disagrees - not a status the source ever holds"; }
            if (isDim(c)) td.style.borderRight = "1px solid var(--line)";
          },
          fetch: function (gs, signal) {
            syncPanels(gs);
            if (!st.dims.length) return Promise.reject(new Error("Pick at least one Group by dimension."));
            var b; try { b = body(gs); } catch (e) { return Promise.reject(e); }
            b.page = gs.page; b.limit = gs.limit;
            return M.post(API + "/result", b, { signal: signal }).then(function (r) {
              lastRes = r; grid.setTitle(r.lineGrain ? "PO lines · one row per PO line" : "Groups · aggregated above the PO line");
              return r;
            });
          },
          values: function (c, gs) {
            if (!isDim(c)) return Promise.reject(new Error("value lists are for the grain columns - test numbers with a condition"));
            var others = {}; Object.keys(gs.filters).forEach(function (k) { if (k !== c && gs.filters[k].length) others[k] = gs.filters[k]; });
            return M.post(API + "/values", { dim: c, date_basis: st.basis, from_date: st.from || null, to_date: st.to || null, filters: others }).then(function (d) { return d.values || []; });
          },
          onData: function () { decorate(); },
          getExtra: function () { return { dims: st.dims, meas: st.meas, stages: st.stages, stageCols: st.stageCols, basis: st.basis, from: st.from, to: st.to, filterDims: st.filterDims, conds: st.conds }; },
          setExtra: function (x) {
            ["dims", "meas", "stages", "stageCols", "filterDims", "conds"].forEach(function (k) { if (Array.isArray(x[k])) st[k] = JSON.parse(JSON.stringify(x[k])); });
            if (x.basis) st.basis = x.basis; st.from = x.from || ""; st.to = x.to || "";
            basis.value = st.basis; from.value = st.from; to.value = st.to; allDates.classList.toggle("hidden", !(st.from || st.to));
            pDims.api.set(st.dims); pMeas.api.set(st.meas); pStages.api.set(st.stages); pCols.api.set(st.stageCols);
            drawPanels(); drawCondFields(); drawWarnings();
          },
          exports: ctx.me.can.export ? [
            { label: "Excel - the whole selection, each stage merged above its block", fn: function (gs) { doExport("xlsx", gs); } },
            { label: "CSV - the whole selection (two header rows)", fn: function (gs) { doExport("csv", gs); } }] : []
        });
        // keep the filter panels in step with the grid's own filter state (header filters, chips, saved views)
        function syncPanels(gs) {
          var changed = false;
          Object.keys(gs.filters || {}).forEach(function (k) { if (gs.filters[k].length && st.filterDims.indexOf(k) < 0) { st.filterDims.push(k); changed = true; } });
          if (changed) drawPanels();
          Object.keys(panels).forEach(function (d) { panels[d].ms.set((gs.filters || {})[d] || []); });
        }
        // full stage + note on each header (the grid's own title is the column name)
        function decorate() {
          var ths = card.querySelectorAll("thead tr.cols th"), cols = grid.columns();
          for (var i = 0; i < ths.length && i < cols.length; i++) {
            var c = cols[i], s = stageOf(c);
            if (s) { var tail = String(c).split(" ").pop(); ths[i].title = ((lastRes.stageLabels || {})[s] || s) + " · " + ((meta.stageColNotes || {})[tail] || tail); }
          }
        }
        var wrap = card.querySelector(".dg-wrap");
        if (wrap && window.MutationObserver) new MutationObserver(function () { if (lastRes) decorate(); }).observe(wrap, { childList: true });

        drawPanels(); drawCondFields(); drawWarnings();
        return grid.applyDefaultView().then(function () { if (ctx.alive()) return grid.reload(true); });
      }, function (e) {
        if (!ctx.alive()) return;
        M.setHeader({ tiles: [] });
        ctx.main.appendChild(el("div", { "class": "notice bad" }, [M.icon("info", 16), el("span", { text: "The TNA catalogue could not be read: " + e.message })]));
      });
    }
  });
})();

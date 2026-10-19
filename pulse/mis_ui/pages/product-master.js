// Product Master (DFS /sf-mis-product-master, SfMisProductMaster.jsx): read-only article attributes from
// SILVER.VW_PRODUCT_MASTER - search, tick-list filters per column, column groups, the MIS-universe switch, CSV export.
// API: /api/v1/mis/product-master/{params, result, export}. REFERENCE PAGE for the MIS page pattern.
(function () {
  "use strict";
  var M = window.MIS, el = M.el;

  M.page("product-master", {
    render: function (ctx) {
      var st = { search: "", universe: false, columns: null };
      M.setHeader({ title: "Product Master", sub: "Every article and its attributes, read live from SILVER.VW_PRODUCT_MASTER (read-only).",
                    tiles: [{ k: "Articles", skel: true }, { k: "Generic articles", skel: true }, { k: "Major categories", skel: true }, { k: "In the MIS universe", skel: true }] });
      return M.get("/api/v1/mis/product-master/params").then(function (p) {
        if (!ctx.alive()) return;
        var s = p.stats || {};
        M.setHeader({ tiles: [{ k: "Articles", v: M.fmt(s.articles, "int"), s: M.fmt(s.rows, "int") + " rows" },
          { k: "Generic articles", v: M.fmt(s.genArticles, "int") }, { k: "Major categories", v: M.fmt(s.majCats, "int") },
          { k: "In the MIS universe", v: M.fmt(s.universeRows, "int"), s: "SEG APP / GM, category filled" },
          { k: "Latest article", v: s.latestArticle || "–", s: s.colorMapDate ? "colour map " + s.colorMapDate : "" }] });
        st.columns = (M.store.get("pm.cols", null) || p.defaultCols || []).filter(function (c) { return p.columns.indexOf(c) >= 0; });
        var empty = p.emptyCols || [];

        // filter bar: search + universe switch + column groups
        var search = el("input", { type: "search", placeholder: "Search " + (p.searchCols || []).join(", ").toLowerCase(), style: { minWidth: "320px" } });
        var uni = el("div", { "class": "seg", role: "group", "aria-label": "Universe" }, [
          el("button", { type: "button", "aria-pressed": "true", text: "All articles" }), el("button", { type: "button", "aria-pressed": "false", text: "MIS universe only" })]);
        uni.querySelectorAll("button").forEach(function (b, i) { b.addEventListener("click", function () {
          st.universe = i === 1; uni.querySelectorAll("button").forEach(function (x, j) { x.setAttribute("aria-pressed", String(j === i)); }); grid.reload(true); }); });
        search.addEventListener("input", M.debounce(function () { st.search = search.value.trim(); grid.reload(true); }, 400));
        var colBtn = el("button", { type: "button", "class": "btn" }, [M.icon("cols", 15), "Choose columns"]);
        colBtn.addEventListener("click", function () {
          var cl = M.checklist((p.groups || []).map(function (g) { return { label: g.label, items: g.cols.map(function (c) { return { value: c, label: c + (empty.indexOf(c) >= 0 ? "  (empty in the source)" : "") }; }) }; }),
                               st.columns, { height: "380px", prettify: false });
          var ok = el("button", { type: "button", "class": "btn primary", text: "Show these columns" }), def = el("button", { type: "button", "class": "btn", text: "Default set" });
          var m = M.modal("Columns of the product master", [el("p", { "class": "muted small", style: { margin: 0 }, text: "Pick any of the " + p.columns.length + " columns; ticking one costs nothing extra (it rides the same query)." }), cl.el], [def, ok]);
          ok.addEventListener("click", function () { st.columns = cl.get(); M.store.set("pm.cols", st.columns); m.close(); grid.reload(true); });
          def.addEventListener("click", function () { st.columns = (p.defaultCols || []).slice(); M.store.set("pm.cols", null); m.close(); grid.reload(true); });
        });
        var card = el("div", { "class": "card" });
        card.appendChild(el("div", { "class": "fbar" }, [el("label", { "class": "f" }, ["Search", search]), el("label", { "class": "f" }, ["Universe", uni]), el("span", { "class": "spacer" }), colBtn]));
        ctx.main.appendChild(card);

        function body(gs) {
          return { col_filters: Object.keys(gs.filters).length ? gs.filters : null, search: st.search || null, universe: st.universe, columns: st.columns,
                   page: gs.page, limit: gs.limit, sort_col: gs.sort_col, sort_dir: gs.sort_dir };
        }
        var grid = M.Grid(card, {
          id: "product-master", title: "Articles", conditions: false, isKey: function () { return true; }, format: function () { return "text"; },
          fetch: function (gs, signal) { return M.post("/api/v1/mis/product-master/result", body(gs), { signal: signal }); },
          values: function (c) {
            if ((p.options || {})[c]) return Promise.resolve(p.options[c]);
            return Promise.reject(new Error("this column has too many values for a list - use Search"));
          },
          exports: ctx.me.can.export ? [{ label: "CSV - the whole filtered selection", fn: function (gs) {
            M.toast("Preparing the export…");
            M.download("POST", "/api/v1/mis/product-master/export", body(gs), "product-master.csv").then(function (n) { M.toast("Downloaded " + n, "ok"); }, function (e) { M.toast(e.message, "bad"); });
          } }] : []
        });
        return grid.applyDefaultView().then(function () { return grid.reload(true); });
      });
    }
  });
})();

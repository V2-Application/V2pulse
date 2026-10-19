// Monitor (DFS /sf-mis-monitor, SfMisMonitor.jsx): is every day of the two day-wise facts (BRONZE.DAY_WISE_ST_VAR_SALE
// / _STOCK) loaded to the extent its source holds? Each day is scored FULL / PARTIAL / MISSING / NOT_IN_SOURCE against
// the loader's own source predicate. Coverage calendar per fact, what to load (gap ranges, max 31 days each), the
// load-run log, stale-run warning; MIS admins can load a gap and clear the 10-minute cache.
// API: /api/v1/mis/monitor/{overview, coverage, gaps, runs, cache/clear} + POST /api/v1/mis/monitor/load-gap.
// Honours ?from=YYYY-MM-DD&to=YYYY-MM-DD (the range-health strips on the report pages link here with them).
(function () {
  "use strict";
  var M = window.MIS, el = M.el;
  var API = "/api/v1/mis/monitor";
  var V = { FULL: { label: "Full", chip: "ok", color: "var(--ok)" }, PARTIAL: { label: "Partial", chip: "warn", color: "var(--warn)" },
            MISSING: { label: "Missing", chip: "bad", color: "var(--bad)" }, NOT_IN_SOURCE: { label: "Not in source", chip: "grey", color: "var(--muted)" } };
  var ORDER = ["FULL", "PARTIAL", "MISSING", "NOT_IN_SOURCE"];
  var KIND = { sale: { title: "Sale - DAY_WISE_ST_VAR_SALE", color: "var(--s1)",
                       note: "Scored on stores AND on money: the day's SALE_V is tied back to SUM(NETWR) at source, so a day loaded before billing finished is caught even when every store is present." },
               stock: { title: "Stock - DAY_WISE_ST_VAR_STOCK", color: "var(--s2)",
                        note: "Scored on stores only. Its source is ~19.7M rows a day, and summing that across the window would not open quickly." } };
  var ISO = /^\d{4}-\d{2}-\d{2}$/;

  // A filter-bar caption over a button group: a click on the caption text must not "click" the first button.
  function fl(caption, ctrl) { var l = el("label", { "class": "f" }, [caption, ctrl]); l.addEventListener("click", function (e) { if (e.target === l) e.preventDefault(); }); return l; }

  function pretty(d) { return d ? String(d).slice(0, 10).split("-").reverse().join("-") : "–"; }
  function when(iso) { return iso ? new Date(iso).toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "–"; }
  function dur(ms) { return ms == null ? "–" : ms < 1000 ? ms + "ms" : ms < 90000 ? (ms / 1000).toFixed(1) + "s" : (ms / 60000).toFixed(1) + "m"; }

  // Client-side paging / sorting / tick-list filtering for a Grid over rows already in the browser.
  function localGrid(getRows) {
    return {
      fetch: function (gs) {
        var all = getRows(), cols = all.columns, rows = all.rows.filter(function (r) {
          return Object.keys(gs.filters).every(function (c) { var i = cols.indexOf(c), v = r[i] == null || r[i] === "" ? "(blank)" : String(r[i]); return gs.filters[c].indexOf(v) >= 0; });
        });
        if (gs.sort_col) { var si = cols.indexOf(gs.sort_col), dir = gs.sort_dir === "desc" ? -1 : 1;
          rows = rows.slice().sort(function (a, b) { var x = a[si], y = b[si]; if (x == null) return 1; if (y == null) return -1;
            return (M.isNum(x) && M.isNum(y) ? Number(x) - Number(y) : String(x).localeCompare(String(y))) * dir; }); }
        return Promise.resolve({ columns: cols, rows: rows.slice((gs.page - 1) * gs.limit, gs.page * gs.limit), total: rows.length });
      },
      values: function (c) {
        var all = getRows(), i = all.columns.indexOf(c), seen = {}, out = [];
        all.rows.forEach(function (r) { var v = r[i] == null || r[i] === "" ? "(blank)" : String(r[i]); if (!seen[v]) { seen[v] = 1; out.push(v); } });
        return Promise.resolve(out.sort());
      }
    };
  }

  M.page("monitor", {
    render: function (ctx) {
      var yday = M.addDays(M.today(), -1);
      var qf = M.qs("from"), qt = M.qs("to");
      var S = { from: ISO.test(qf || "") ? qf : yday, to: ISO.test(qt || "") ? qt : (ISO.test(qf || "") ? qf : yday),
                cov: null, ov: null, gaps: null, runs: [], runErr: "", picked: null, loading: null, tab: "coverage", busy: false };
      var admin = !!ctx.me.can.admin;

      var refreshBtn = el("button", { type: "button", "class": "btn primary" }, [M.icon("refresh", 15), "Refresh"]);
      var clearBtn = admin ? el("button", { type: "button", "class": "btn", title: "Results are cached for 10 minutes" }, [M.icon("x", 15), "Clear cache"]) : null;
      M.setHeader({ title: "MIS Load Monitor",
        sub: "Every MIS window (TD, MTD, YTD, LAST_6MONTH) is a sum over a date range of the two day-wise facts. A day loaded for one store contributes one store, and the report prints that total exactly like a whole one - so this page scores every day against the source rows that should have produced it.",
        actions: [refreshBtn, clearBtn],
        tiles: [{ k: "Sale days sound", skel: true }, { k: "Stock days sound", skel: true }, { k: "Freshest sound sale day", skel: true }, { k: "Fact span", skel: true }, { k: "Last load", skel: true }] });

      // ── layout ───────────────────────────────────────────────────────────────────────────────
      var staleBox = el("div", { "class": "stack" });
      var from = el("input", { type: "date", value: S.from, "aria-label": "From" }), to = el("input", { type: "date", value: S.to, "aria-label": "To" });
      var presets = el("div", { "class": "seg", role: "group", "aria-label": "Quick ranges" });
      [[1, "Yesterday"], [30, "30d"], [60, "60d"], [90, "90d"], [180, "6m"], [365, "1y"]].forEach(function (p) {
        var b = el("button", { type: "button", "aria-pressed": "false", text: p[1] });
        b.addEventListener("click", function () {
          if (p[0] === 1) { from.value = yday; to.value = yday; } else { to.value = M.today(); from.value = M.addDays(M.today(), -(p[0] - 1)); }
          presets.querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
          apply();
        });
        presets.appendChild(b);
      });
      var check = el("button", { type: "button", "class": "btn primary" }, [M.icon("search", 15), "Check range"]);
      from.addEventListener("change", function () { clearPresets(); }); to.addEventListener("change", function () { clearPresets(); });
      function clearPresets() { presets.querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-pressed", "false"); }); }
      check.addEventListener("click", apply);
      var tabs = el("div", { "class": "seg", role: "tablist", "aria-label": "Section" });
      var TABS = [["coverage", "Coverage"], ["gaps", "What to load"], ["runs", "Load history"]];
      TABS.forEach(function (t) {
        var b = el("button", { type: "button", role: "tab", "aria-pressed": String(t[0] === S.tab), "data-tab": t[0], text: t[1] });
        b.addEventListener("click", function () { S.tab = t[0]; drawTabs(); });
        tabs.appendChild(b);
      });
      var ctrl = el("div", { "class": "card" }, el("div", { "class": "fbar", style: { borderBottom: "0", borderRadius: "var(--radius)" } }, [
        el("label", { "class": "f" }, ["From", from]), el("label", { "class": "f" }, ["To", to]), fl("Quick range", presets), check,
        el("span", { "class": "spacer" }), fl("Show", tabs)]));
      var paneCov = el("div", { "class": "stack", style: { gap: "16px" } }), paneGaps = el("div", { "class": "stack", style: { gap: "16px" } }), paneRuns = el("div", { "class": "stack", style: { gap: "16px" } });
      [staleBox, ctrl, paneCov, paneGaps, paneRuns].forEach(function (n) { ctx.main.appendChild(n); });

      function drawTabs() {
        tabs.querySelectorAll("button").forEach(function (b) {
          var k = b.getAttribute("data-tab"), n = k === "gaps" ? ((S.gaps || {}).ranges || []).length : k === "runs" ? S.runs.length : 0;
          b.setAttribute("aria-pressed", String(k === S.tab)); b.textContent = TABS.filter(function (t) { return t[0] === k; })[0][1] + (n ? " (" + n + ")" : "");
        });
        paneCov.classList.toggle("hidden", S.tab !== "coverage"); paneGaps.classList.toggle("hidden", S.tab !== "gaps"); paneRuns.classList.toggle("hidden", S.tab !== "runs");
      }

      refreshBtn.addEventListener("click", function () { load(true); loadRuns(); });
      if (clearBtn) clearBtn.addEventListener("click", function () {
        M.action(clearBtn, "Clearing…", function () { return M.post(API + "/cache/clear", {}); }, function (r) { return "Cache cleared (" + (r && r.cleared != null ? r.cleared : 0) + " entries)."; })
          .then(function () { if (ctx.alive()) load(true); }, function () { /* toasted */ });
      });

      function apply() {
        if (!from.value || !to.value) { M.toast("Pick both dates.", "bad"); return; }
        if (from.value > to.value) { M.toast("From date is after To date", "bad"); return; }
        S.from = from.value; S.to = to.value; S.picked = null;
        history.replaceState({}, "", "/mis/monitor?from=" + S.from + "&to=" + S.to);
        load(false);
      }

      // ── data ─────────────────────────────────────────────────────────────────────────────────
      function load(refresh) {
        S.busy = true; refreshBtn.disabled = true;
        waiting(paneCov, "Scoring every day against its source…"); waiting(paneGaps, "Working out what to load…");
        var q = "from=" + S.from + "&to=" + S.to, r = refresh ? "&refresh=1" : "";
        return Promise.all([
          M.get(API + "/coverage?" + q + "&kind=both" + r).then(function (c) { S.cov = c; }, function (e) { S.cov = { error: e.message }; }),
          M.get(API + "/overview?days=60" + r).then(function (o) { S.ov = o; }, function (e) { S.ov = { error: e.message }; }),
          M.get(API + "/gaps?" + q + "&kind=both").then(function (g) { S.gaps = g; }, function (e) { S.gaps = { error: e.message }; })
        ]).then(function () {
          if (!ctx.alive()) return;
          S.busy = false; refreshBtn.disabled = false;
          drawHeader(); drawStale(); drawCoverage(); drawGaps(); drawTabs();
        });
      }
      function loadRuns() {
        return M.get(API + "/runs?days=30&limit=200").then(function (d) { S.runs = d.runs || []; S.runErr = d.error || ""; }, function (e) { S.runs = []; S.runErr = e.message; })
          .then(function () { if (!ctx.alive()) return; drawRuns(); drawTabs(); });
      }
      function waiting(box, text) { M.clear(box).appendChild(el("div", { "class": "card" }, el("div", { "class": "card-b muted", html: '<span class="spin"></span> ' + M.esc(text) }))); }

      // ── masthead tiles ───────────────────────────────────────────────────────────────────────
      function drawHeader() {
        var c = S.cov && !S.cov.error ? S.cov : null, o = S.ov && !S.ov.error ? S.ov : null, n = c ? c.days : 0;
        function sound(t, k) {
          if (!t) return { k: k, v: "–", s: "in the selected range" };
          var bad = t.actionable / Math.max(n, 1);
          return { k: k, v: t.FULL + " / " + n, s: t.actionable ? t.actionable + " need loading" : "all sound", cls: bad === 0 ? "up" : "down",
                   title: ORDER.map(function (v) { return (t[v] || 0) + " " + V[v].label.toLowerCase(); }).join(" · ") };
        }
        var lr = o && o.lastRuns ? o.lastRuns : {}, last = [lr.sale, lr.stock].filter(Boolean).sort(function (a, b) { return String(b.startedAt).localeCompare(String(a.startedAt)); })[0];
        M.setHeader({ sub: "Range " + pretty(S.from) + " to " + pretty(S.to) + (n ? " · " + n + " day(s)" : "") + ". Every MIS window is a sum over a date range of the two day-wise facts; a day loaded for one store contributes one store and prints exactly like a whole one, so every day is scored against the source rows that should have produced it.",
          tiles: [sound(c && c.tally.sale, "Sale days sound"), sound(c && c.tally.stock, "Stock days sound"),
            { k: "Freshest sound sale day", v: o ? pretty((o.freshestSoundDay || {}).sale) : "–", s: o ? "stock " + pretty((o.freshestSoundDay || {}).stock) + " · newest FULLY loaded day" : "",
              title: "The newest fully loaded day - not simply the newest day present" },
            { k: "Fact span", v: o && o.span && o.span.sale ? M.fmt(o.span.sale.days, "int") + " d" : "–",
              s: o && o.span && o.span.sale ? "sale " + M.compact(o.span.sale.rows) + " rows · stock " + M.compact((o.span.stock || {}).rows) + " rows" : "",
              title: o && o.span ? Object.keys(o.span).map(function (k) { return k + ": " + pretty(o.span[k].minDt) + " to " + pretty(o.span[k].maxDt) + ", " + M.fmt(o.span[k].days, "int") + " days, " + M.fmt(o.span[k].rows, "int") + " rows"; }).join("\n") : null },
            { k: "Last load", v: last ? last.status : "–", cls: last && last.status === "SUCCESS" ? "up" : last ? "down" : "",
              s: last ? when(last.finishedAt || last.startedAt) + " · " + (last.triggeredBy || "") : (o && o.runLogError ? "run log not readable" : "") }] });
      }

      function drawStale() {
        M.clear(staleBox);
        if (S.ov && S.ov.error) staleBox.appendChild(el("div", { "class": "notice bad", text: "Overview: " + S.ov.error }));
        var stale = (S.ov && S.ov.staleRuns) || [];
        if (stale.length) staleBox.appendChild(el("div", { "class": "notice warn" }, [M.icon("clock", 16), el("div", {}, [
          el("b", { text: stale.length + " load" + (stale.length > 1 ? "s" : "") + " started and never finished. " }),
          el("span", { text: "Either still running, or the engine stopped mid-call - Snowflake cannot tell the two apart, so the age is shown and the judgement is yours." }),
          el("ul", { style: { margin: "6px 0 0", paddingLeft: "18px", fontFamily: "monospace", fontSize: "12px" } }, stale.map(function (r) {
            return el("li", { text: r.kind + " · " + pretty(r.from) + " → " + pretty(r.to) + " · " + r.ageMin + " min ago · " + (r.host || "") });
          }))])]));
        if (S.ov && S.ov.runLogError) staleBox.appendChild(el("div", { "class": "notice warn", text: "Load-run log: " + S.ov.runLogError }));
      }

      // ── coverage ─────────────────────────────────────────────────────────────────────────────
      function tally(t) {
        return el("div", { "class": "row", style: { gap: "6px" } }, ORDER.filter(function (k) { return t && t[k]; }).map(function (k) {
          return el("span", { "class": "chip " + V[k].chip, text: t[k] + " " + V[k].label.toLowerCase() });
        }));
      }
      function drawCoverage() {
        M.clear(paneCov);
        if (!S.cov || S.cov.error) { paneCov.appendChild(el("div", { "class": "notice bad", text: (S.cov && S.cov.error) || "No coverage." })); return; }
        var c = S.cov, compact = c.days > 62;
        ["sale", "stock"].forEach(function (kind) {
          var days = (c.coverage || {})[kind] || [];
          var cal = el("div", { "class": "cal", style: compact ? { gridTemplateColumns: "repeat(auto-fill, minmax(38px, 1fr))", gap: "4px" } : null });
          days.forEach(function (d) {
            var v = V[d.verdict] || V.NOT_IN_SOURCE, on = S.picked && S.picked.dt === d.dt && S.picked.kind === kind;
            var tip = pretty(d.dt) + " - " + v.label + "\n" + d.why + "\n\n" + M.fmt(d.factRows, "int") + " rows" +
                      (d.valuePct != null ? "\n" + d.valuePct + "% of the day's billed value" : "") + (d.lastLoadedAt ? "\nloaded " + when(d.lastLoadedAt) : "");
            var cell = el("button", { type: "button", "class": "d " + (V[d.verdict] ? d.verdict : "NOT_IN_SOURCE"), title: tip, "aria-pressed": String(!!on),
              "aria-label": pretty(d.dt) + " " + v.label,
              style: { textAlign: compact ? "center" : "left", cursor: "pointer", font: "inherit", fontSize: compact ? "11px" : "12px", padding: compact ? "4px" : null,
                       outline: on ? "2px solid var(--brand-2)" : null, outlineOffset: on ? "1px" : null, color: "var(--fg)" } },
              compact ? [el("b", { text: d.dt.slice(8, 10) }), el("div", { "class": "tiny muted", text: d.dt.slice(5, 7) + "/" + d.dt.slice(2, 4) })]
                      : [el("b", { text: pretty(d.dt) }), el("div", { text: v.label }),
                         el("div", { "class": "muted tiny", text: d.factStores + "/" + d.srcStores + " stores" + (d.valuePct != null ? " · " + d.valuePct + "% value" : "") })]);
            cell.addEventListener("click", function () { S.picked = on ? null : Object.assign({ kind: kind }, d); drawCoverage(); });
            cal.appendChild(cell);
          });
          paneCov.appendChild(el("div", { "class": "card" }, [
            el("div", { "class": "card-h" }, [el("h2", { text: KIND[kind].title }), el("span", { "class": "spacer" }), tally(c.tally[kind])]),
            el("div", { "class": "card-b stack" }, [days.length ? cal : el("div", { "class": "muted small", text: "No days in range." }),
              el("div", { "class": "muted small", text: KIND[kind].note })])]));
        });
        if (S.picked) paneCov.appendChild(dayDetail(S.picked));
        // daily coverage chart: % of source stores loaded per day (blank where the source has nothing)
        if (c.days > 1) {
          var labels = ((c.coverage || {}).sale || (c.coverage || {}).stock || []).map(function (d) { return d.dt; });
          var series = ["sale", "stock"].filter(function (k) { return (c.coverage || {})[k]; }).map(function (k) {
            return { name: k === "sale" ? "Sale - % of stores" : "Stock - % of stores", color: KIND[k].color, values: c.coverage[k].map(function (d) { return d.storePct; }) };
          });
          var sv = ((c.coverage || {}).sale || []).map(function (d) { return d.valuePct; });
          if (sv.some(function (x) { return x != null; })) series.push({ name: "Sale - % of billed value", color: "var(--s3)", values: sv });
          var th = c.thresholds || {};
          paneCov.appendChild(el("div", { "class": "card" }, [
            el("div", { "class": "card-h" }, [el("h2", { text: "Daily coverage" }), el("span", { "class": "note", text: "% of the source's stores (and, for sale, of its billed value) present in the fact each day. Thresholds: sale " + th.saleStorePct + "% stores / " + th.saleValuePct + "% value, stock " + th.stockStorePct + "% stores." })]),
            el("div", { "class": "card-b stack" }, [M.charts.lines(series, { labels: labels, height: 220, format: function (v) { return v == null ? "–" : M.fmt(v, "dec") + "%"; }, title: "Daily coverage" }),
              M.charts.legend(series.map(function (s) { return { label: s.name, color: s.color }; }))])]));
        }
        var t = c.thresholds;
        if (t) paneCov.appendChild(el("div", { "class": "notice" }, [M.icon("info", 16), el("div", { "class": "small" }, [el("b", { text: "How a day is scored. " }),
          el("span", { text: "Against the source rows the loader itself would have read (FKART_ANA='FP'), not against \"every store that exists\" - which is why a whole day ties exactly rather than leaving an unexplained remainder. Thresholds: sale " +
            t.saleStorePct + "% of stores and " + t.saleValuePct + "% of value, stock " + t.stockStorePct + "% of stores. " + (t.note || "") })])]));
      }
      function dayDetail(p) {
        var v = V[p.verdict] || V.NOT_IN_SOURCE, items = [["Rows in fact", M.fmt(p.factRows, "int")], ["Stores loaded", p.factStores + " of " + p.srcStores],
          ["Store %", p.storePct == null ? "–" : p.storePct + "%"], ["Value % of source", p.valuePct == null ? "not scored" : p.valuePct + "%"], ["Last loaded", when(p.lastLoadedAt)]];
        if (p.tags != null) items.push(["Stock tags", String(p.tags)]);
        var close = el("button", { type: "button", "class": "btn ghost sm", "aria-label": "Close the day detail" }, [M.icon("x", 14)]);
        close.addEventListener("click", function () { S.picked = null; drawCoverage(); });
        var body = el("div", { "class": "card-b stack" }, [el("p", { style: { margin: 0 }, text: p.why }),
          el("div", { "class": "grid12" }, items.map(function (kv) { return el("div", { "class": "c3" }, [el("div", { "class": "tiny muted", style: { textTransform: "uppercase", letterSpacing: ".05em" }, text: kv[0] }), el("b", { "class": "tnum", text: kv[1] })]); }))]);
        if (p.verdict === "MISSING" || p.verdict === "PARTIAL") {
          if (admin) { var b = el("button", { type: "button", "class": "btn primary" }, [M.icon("play", 15), "Load this day"]);
            b.addEventListener("click", function () { runGap({ kind: p.kind, from: p.dt, to: p.dt, days: 1 }, b); }); body.appendChild(el("div", {}, b)); }
          else body.appendChild(el("div", { "class": "muted small", text: "Loading a day needs the mis.admin permission." }));
        } else if (p.verdict === "NOT_IN_SOURCE") body.appendChild(el("div", { "class": "muted small", text: "Nothing to load - the source has no rows for this day either." }));
        return el("div", { "class": "card" }, [el("div", { "class": "card-h" }, [el("span", { "class": "chip " + v.chip, text: v.label }),
          el("h2", { text: pretty(p.dt) + " · " + p.kind }), el("span", { "class": "spacer" }), close]), body]);
      }

      // ── gaps ─────────────────────────────────────────────────────────────────────────────────
      function drawGaps() {
        M.clear(paneGaps);
        var g = S.gaps;
        if (!g || g.error) { paneGaps.appendChild(el("div", { "class": "notice bad", text: (g && g.error) || "No gap list." })); return; }
        var ranges = g.ranges || [], card = el("div", { "class": "card" });
        paneGaps.appendChild(card);
        if (!ranges.length) {
          card.appendChild(el("div", { "class": "card-b" }, el("div", { "class": "notice ok" }, [M.icon("info", 16), el("span", { text: "Nothing to load - every day in this range is loaded to the extent its source holds." })])));
          return;
        }
        var total = ranges.reduce(function (a, r) { return a + r.days; }, 0);
        card.appendChild(el("div", { "class": "card-h" }, [el("h2", { text: "What to load" }), el("span", { "class": "note", text: ranges.length + " range" + (ranges.length > 1 ? "s" : "") + " covering " + total + " day-loads, widest first" })]));
        card.appendChild(el("div", { "class": "card-b muted small", text: "Consecutive days are grouped because each proc scans its source once per call - running ten days as one call does the expensive part once instead of ten times. Ranges are capped at " + g.maxLoadDays + " days, the same limit the loader itself enforces, so every range here can be run exactly as shown." }));
        var tb = el("tbody");
        ranges.forEach(function (r) {
          var key = r.kind + "|" + r.from + "|" + r.to, btn = null;
          if (admin) { btn = el("button", { type: "button", "class": "btn sm primary", disabled: !!S.loading, "aria-label": "Load " + r.kind + " " + r.from + " to " + r.to }, [M.icon("play", 13), S.loading === key ? "Loading…" : "Load"]);
            btn.addEventListener("click", function () { runGap(r, btn); }); }
          tb.appendChild(el("tr", {}, [el("td", {}, el("span", { "class": "chip " + (r.kind === "sale" ? "" : "grey"), text: r.kind.toUpperCase() })),
            el("td", { "class": "tnum", text: pretty(r.from) }), el("td", { "class": "tnum", text: pretty(r.to) }), el("td", { "class": "num", style: { fontWeight: "600" }, text: String(r.days) }),
            el("td", { "class": "num", style: { color: "var(--bad)" }, text: r.missing ? String(r.missing) : "" }), el("td", { "class": "num", style: { color: "var(--warn)" }, text: r.partial ? String(r.partial) : "" }),
            el("td", { style: { textAlign: "right" } }, btn || el("span", { "class": "muted tiny", text: "admin only" }))]));
        });
        card.appendChild(el("div", { "class": "dg-wrap", style: { maxHeight: "none", minHeight: "0" } }, el("table", { "class": "dg-t", style: { width: "100%" } }, [
          el("thead", {}, el("tr", { "class": "cols" }, ["Fact", "From", "To", "Days", "Missing", "Partial", ""].map(function (h, i) { return el("th", { "class": i >= 3 && i <= 5 ? "num" : null, style: i >= 3 && i <= 5 ? { textAlign: "right" } : null, text: h }); }))), tb])));
        card.appendChild(el("div", { "class": "card-b" }, el("div", { "class": "notice warn" }, [M.icon("info", 16), el("span", { text: "Loading runs BOTH procs (sale and stock) for the range, for EVERY store. Stock is ~19.7M rows a day, so a wide range takes minutes and costs real warehouse time - start with the ranges that matter to the report you need." + (admin ? "" : " Loading needs the mis.admin permission.") })])));
      }

      // Load a gap: the same endpoint and rules as the load panel (31-day cap), logged as triggered_by = monitor.
      function runGap(r, btn) {
        if (!admin || S.loading) return;
        var max = (S.gaps && S.gaps.maxLoadDays) || 31, days = Math.round((new Date(r.to) - new Date(r.from)) / 86400000) + 1;
        if (days > max) { M.toast("At most " + max + " days per load - split the range.", "bad"); return; }
        M.confirm("Load " + pretty(r.from) + (r.from !== r.to ? " → " + pretty(r.to) : ""),
          "Re-load sale AND stock for " + pretty(r.from) + (r.from !== r.to ? " to " + pretty(r.to) + " (" + days + " days)" : "") + " for every store? The load procedures delete and re-insert that range, so it is safe to re-run. Stock is ~19.7M rows a day: it can take several minutes.",
          "Load", false).then(function (yes) {
          if (!yes) return;
          S.loading = r.kind + "|" + r.from + "|" + r.to;
          M.toast("Loading " + pretty(r.from) + " → " + pretty(r.to) + " …");
          M.action(btn, "Loading…", function () { return M.post(API + "/load-gap", { from_date: r.from, to_date: r.to, stores: [] }); }, function (d) {
            var msg = Object.keys((d && d.messages) || {}).map(function (k) { return d.messages[k]; }).filter(Boolean).join(" · ");
            return msg || "Loaded " + pretty(r.from) + " → " + pretty(r.to) + ".";
          }).then(function () {
            S.loading = null; if (!ctx.alive()) return;
            // bypass the TTL cache: the point of pressing Load was to change what the grid says
            load(true); loadRuns();
          }, function () { S.loading = null; });
        });
      }

      // ── runs ─────────────────────────────────────────────────────────────────────────────────
      var runsGrid = null, runNotes = null;
      var RCOLS = ["STARTED", "FACT", "FROM", "TO", "DAYS", "STORES", "BY", "OPERATOR", "STATUS", "DELETED", "INSERTED", "TOOK", "HOST", "MESSAGE"];
      function runRows() {
        return { columns: RCOLS, rows: S.runs.map(function (r) {
          return [r.startedAt, (r.kind || "").toUpperCase(), r.from, r.to, r.days, r.allStores ? "all" : r.nStores, r.triggeredBy, r.operator, r.status,
                  r.rowsDeleted, r.rowsInserted, r.durationMs, r.host, r.error || r.procMsg || ""];
        }) };
      }
      function drawRuns() {
        if (!runsGrid) {
          M.clear(paneRuns);
          runNotes = el("div", { "class": "stack" }); paneRuns.appendChild(runNotes);
          var card = el("div", { "class": "card" }); paneRuns.appendChild(card);
          var lg = localGrid(runRows);
          runsGrid = M.Grid(card, {
            id: "monitor-runs", title: "Load history - last 30 days", conditions: false, sort_col: "STARTED", sort_dir: "desc", limit: 100, pageSizes: [50, 100, 200],
            isKey: function (c) { return ["DAYS", "DELETED", "INSERTED", "TOOK"].indexOf(c) < 0; },
            format: function (c) { return ["DAYS", "DELETED", "INSERTED"].indexOf(c) >= 0 ? "int" : "text"; },
            label: function (c) { return c.charAt(0) + c.slice(1).toLowerCase(); },
            fetch: lg.fetch, values: lg.values,
            cell: function (td, c, v) {
              if (c === "STARTED") td.textContent = when(v);
              else if (c === "FROM" || c === "TO") td.textContent = pretty(v);
              else if (c === "TOOK") { td.textContent = dur(v); td.classList.add("num"); }
              else if ((c === "DELETED" || c === "INSERTED") && v == null) td.textContent = "–";
              else if (c === "INSERTED" && Number(v) === 0) { td.style.color = "var(--bad)"; td.style.fontWeight = "700"; td.title = "A SUCCESS that inserted nothing looks identical to a table that was never loaded - only the log tells them apart."; }
              else if (c === "STATUS" && v) M.clear(td).appendChild(el("span", { "class": "chip " + (v === "SUCCESS" ? "ok" : v === "FAILED" ? "bad" : "warn"), text: v }));
              else if (c === "MESSAGE" && v) td.title = v;
            }
          });
        }
        M.clear(runNotes);
        if (S.runErr) runNotes.appendChild(el("div", { "class": "notice warn" }, [M.icon("info", 16), el("span", { text: S.runErr })]));
        else if (!S.runs.length) runNotes.appendChild(el("div", { "class": "notice" }, [M.icon("info", 16), el("span", { text: "No loads recorded in the last 30 days. Every load from this page, from the Variant Article page and from the nightly schedule is written to BRONZE.MIS_LOAD_RUN." })]));
        runsGrid.reload(true);
      }

      drawTabs();
      return Promise.all([load(false), loadRuns()]);
    }
  });
})();

// Allocation Gap Reports (DFS /sf-mis-alloc-gap, SfAllocGapReport.jsx): the 27 workbooks the allocation team reads,
// each one QUESTION asked of one ARS run ("display given but nothing allocated", "MSA in a size nobody budgets"),
// answered in the team's own template with its own GAP formula. The page leads with the COUNT - how many rows each
// report calls a gap for the chosen session, ranked, clean ones pushed down and greyed - then shows the picked report's
// rows (the whole population, gaps sorted first) with the verdict column the engine evaluates in SQL, and downloads
// the workbook in its own template (formulas intact).
// API: /api/v1/mis/alloc-gap/{params, counts?session=&refresh=1, preview {report, session, limit}, export?report=&session=}.
(function () {
  "use strict";
  var M = window.MIS, el = M.el, API = "/api/v1/mis/alloc-gap";
  var PREVIEW_LIMIT = 2000;   // DFS asks for 2,000 rows; the engine caps there too

  // A session id is 20260919_125317_275 - the run's own identifier. Only the label is prettied; the raw value goes back.
  function sesLabel(s) {
    s = String(s || "");
    return /^\d{8}_\d{6}/.test(s) ? s.slice(6, 8) + "-" + s.slice(4, 6) + "-" + s.slice(0, 4) + " " + s.slice(9, 11) + ":" + s.slice(11, 13) : s;
  }
  // How long a workbook of N rows takes to build (~2,500 rows/s end to end, rounded on the slow side, as DFS says).
  function est(rows) {
    var s = Math.round(rows / 2500);
    if (s < 20) return "a few seconds";
    if (s < 90) return "about " + Math.round(s / 10) * 10 + " seconds";
    return "about " + Math.round(s / 30) / 2 + " minutes";
  }
  function oneLine(h) { return String(h == null ? "" : h).replace(/\s*\n\s*/g, " "); }
  function isGap(v) { return !!v && v !== "OK"; }
  // Severity of a report in the ranked list.
  function severity(r) {
    if (r.error) return "failed";
    var g = r.gaps || 0; if (!g) return "clean";
    return g >= 1000 || (r.total && g / r.total >= 0.2) ? "high" : "some";
  }
  var SEV = { failed: { c: "var(--bad)", chip: "bad" }, high: { c: "var(--bad)", chip: "bad" }, some: { c: "var(--warn)", chip: "warn" }, clean: { c: "var(--line)", chip: "grey" } };

  // Client-side header conditions over the loaded rows (the preview is at most 2,000 rows, all in the browser).
  function condOk(v, c) {
    var s = v == null ? "" : String(v), n = Number(v), a = Number(c.v1), b = Number(c.v2), low = s.toLowerCase(), t = String(c.v1 == null ? "" : c.v1).toLowerCase();
    var num = M.isNum(v) && c.v1 !== "" && isFinite(a);
    switch (c.op) {
      case "blank": return s === "";
      case "nonblank": return s !== "";
      case "eq": return num ? n === a : low === t;
      case "ne": return num ? n !== a : low !== t;
      case "gt": return M.isNum(v) && n > a;
      case "gte": return M.isNum(v) && n >= a;
      case "lt": return M.isNum(v) && n < a;
      case "lte": return M.isNum(v) && n <= a;
      case "between": return M.isNum(v) && n >= Math.min(a, b) && n <= Math.max(a, b);
      case "contains": return low.indexOf(t) >= 0;
      case "not_contains": return low.indexOf(t) < 0;
      case "starts": return low.slice(0, t.length) === t;
      case "ends": return !t || low.slice(-t.length) === t;
      case "in": return (c.vals || []).map(String).indexOf(s) >= 0;
      case "not_in": return (c.vals || []).map(String).indexOf(s) < 0;
      default: return true;
    }
  }

  M.page("alloc-gaps", {
    render: function (ctx) {
      var params = null, counts = null, counting = false, hideEmpty = true, picked = M.qs("report") || "", session = "";
      var data = null, keys = [], textCols = {}, gapKey = null, kindOf = {}, headOf = {}, grid = null, previewCtl = null;
      var view = { onlyGaps: false, q: "" };

      var recount = el("button", { type: "button", "class": "btn", title: "Count all 27 again against this run (the counts are cached per session on the engine)" }, [M.icon("refresh", 15), "Re-count"]);
      M.setHeader({ title: "Allocation Gap Reports",
        sub: "Twenty-seven questions asked of one allocation run, each answered in the allocation team's own template with its own GAP rule. The list is ranked by how many rows each one calls a gap; Download gives the sheet as it is filed - formulas intact, so changing a threshold re-decides the verdict column.",
        actions: [recount],
        tiles: ["Session", "Reports with gaps", "Total gap rows", "Worst report"].map(function (k) { return { k: k, skel: true }; }) });

      function tiles() {
        var s = session, latest = params && s === params.latest;
        var list = counts || [], withGaps = list.filter(function (r) { return (r.gaps || 0) > 0; }), failed = list.filter(function (r) { return r.error; });
        var total = list.reduce(function (a, r) { return a + (r.gaps || 0); }, 0);
        var worst = withGaps.slice().sort(function (a, b) { return b.gaps - a.gaps; })[0];
        M.setHeader({ tiles: [
          { k: "Session", v: s ? sesLabel(s) : "–", s: latest && params.approvedAt ? "latest · approved " + String(params.approvedAt).slice(0, 16).replace("T", " ") : (s ? s : "no runs") , title: s },
          counts ? { k: "Reports with gaps", v: withGaps.length + " of " + list.length, s: failed.length ? failed.length + " could not be counted" : (list.length - withGaps.length) + " clean", cls: failed.length ? "down" : "" }
                 : { k: "Reports with gaps", skel: counting, v: counting ? null : "–", s: counting ? "counting all 27…" : "" },
          counts ? { k: "Total gap rows", v: M.fmt(total, "int"), s: "across " + list.length + " reports", cls: total ? "down" : "up" } : { k: "Total gap rows", skel: counting, v: "–" },
          counts ? { k: "Worst report", v: worst ? worst.title : "None", s: worst ? M.fmt(worst.gaps, "int") + " gap rows of " + M.fmt(worst.total, "int") : "every report came back clean", cls: worst ? "down" : "up", title: worst ? worst.title : "" }
                 : { k: "Worst report", skel: counting, v: "–" }] });
      }

      return M.get(API + "/params").then(function (p) {
        if (!ctx.alive()) return;
        params = p;
        var want = M.qs("session");
        session = want && (p.sessions || []).indexOf(want) >= 0 ? want : (p.latest || "");
        // A layout mismatch means the templates and the engine disagree about a column - the workbook would have its
        // values one cell to the left. Loud, not logged (as DFS does).
        if ((p.layoutProblems || []).length) M.toast("Template layout mismatch: " + p.layoutProblems.join(" | "), "bad");
        tiles();

        // ── session bar ──────────────────────────────────────────────────────────────────────────
        var sesSel = el("select", { "aria-label": "Allocation session", style: { minWidth: "260px", fontFamily: "ui-monospace, Consolas, monospace" } },
          (p.sessions || []).map(function (s) { return el("option", { value: s, selected: s === session, text: s + (s === p.latest ? "  · latest" : "") + "   (" + sesLabel(s) + ")" }); }));
        if (!(p.sessions || []).length) sesSel.appendChild(el("option", { value: "", text: "no allocation sessions loaded" }));
        var sesInfo = el("span", { "class": "muted tiny", style: { paddingBottom: "8px" } });
        function drawSesInfo() {
          sesInfo.textContent = p.approvedAt && session === p.latest ? "approved " + String(p.approvedAt).slice(0, 19).replace("T", " ") + " · " + M.fmt(p.gridRows, "int") + " grid rows" : "";
        }
        drawSesInfo();
        var top = el("div", { "class": "card" }, el("div", { "class": "fbar", style: { borderBottom: 0, borderRadius: "var(--radius)" } }, [
          el("label", { "class": "f" }, ["Allocation session", sesSel]), sesInfo, el("span", { "class": "spacer" }),
          el("span", { "class": "muted tiny", style: { maxWidth: "460px", paddingBottom: "8px" }, text: "The counts are one sweep of 27 queries - about 20 seconds the first time for a session, then remembered, because a finished run's numbers cannot change." })]));
        ctx.main.appendChild(top);

        // ── the two panes ────────────────────────────────────────────────────────────────────────
        var listHead = el("span", { "class": "small", style: { fontWeight: 600 } }), cleanBtn = el("button", { type: "button", "class": "btn ghost sm hidden" });
        var listBox = el("div", { style: { maxHeight: "calc(100vh - 300px)", minHeight: "240px", overflowY: "auto" } });
        var listCard = el("div", { "class": "card c4" }, [el("div", { "class": "card-h" }, [el("h2", { text: "Reports, ranked" }), el("span", { "class": "spacer" }), cleanBtn]),
          el("div", { style: { padding: "8px 18px", borderBottom: "1px solid var(--line-2)" } }, listHead), listBox]);
        var right = el("div", { "class": "card c8" });
        ctx.main.appendChild(el("div", { "class": "grid12", style: { alignItems: "start" } }, [listCard, right]));
        ctx.main.appendChild(el("div", { "class": "notice" }, [M.icon("info", 16), el("span", { "class": "small",
          text: "The verdict column here is the same rule the sheet computes, evaluated in SQL because a browser has no formula engine. In the downloaded file it is the template's own formula, so editing a threshold re-decides it. Rows are the whole population with gaps sorted first - a verdict only means something against the rows it clears too." })]));

        cleanBtn.addEventListener("click", function () { hideEmpty = !hideEmpty; drawList(); });

        function setUrl() {
          var q = new URLSearchParams(); if (session && session !== p.latest) q.set("session", session); if (picked) q.set("report", picked);
          var s = q.toString(); history.replaceState({}, "", "/mis/alloc-gaps" + (s ? "?" + s : ""));
        }

        function drawList() {
          M.clear(listBox);
          if (counting && !counts) {
            listHead.textContent = "Counting all 27…";
            listBox.appendChild(el("div", { "class": "muted small", style: { padding: "28px 18px", textAlign: "center" } }, [el("span", { "class": "spin" }), el("div", { style: { marginTop: "8px" },
              text: "Counting every report against this run - about 20 seconds the first time, then it is remembered." })]));
            for (var i = 0; i < 6; i++) listBox.appendChild(el("div", { "class": "skel", style: { height: "44px", margin: "8px 18px" } }));
            cleanBtn.classList.add("hidden"); return;
          }
          if (!counts) { listHead.textContent = session ? "Reports" : "No allocation session to report on."; return; }
          var total = counts.reduce(function (a, r) { return a + (r.gaps || 0); }, 0);
          var empty = counts.filter(function (r) { return !r.error && !(r.gaps || 0); }).length;
          listHead.textContent = (counting ? "Re-counting… · " : "") + M.fmt(total, "int") + " gap rows across " + counts.length + " reports";
          cleanBtn.classList.toggle("hidden", !empty); cleanBtn.textContent = hideEmpty ? "Show " + empty + " clean" : "Hide clean";
          // Failures first, biggest first; a report that could not be counted sorts with them - "we do not know" is a
          // thing to look at, not a thing to hide at the bottom.
          var rows = counts.slice().sort(function (a, b) { var ae = a.error ? 1 : 0, be = b.error ? 1 : 0; return ae !== be ? be - ae : (b.gaps || 0) - (a.gaps || 0); });
          if (hideEmpty) rows = rows.filter(function (r) { return r.error || (r.gaps || 0) > 0; });
          var max = rows.reduce(function (a, r) { return Math.max(a, r.gaps || 0); }, 0);
          rows.forEach(function (r, i) {
            var sev = severity(r), on = r.key === picked, sv = SEV[sev];
            var right = r.error ? el("span", { "class": "chip bad", title: r.error, text: "failed" })
              : sev === "clean" ? el("span", { "class": "chip grey", text: "clean" })
              : el("div", { style: { textAlign: "right" } }, [el("span", { "class": "chip " + sv.chip, style: { fontWeight: 700 }, text: M.fmt(r.gaps, "int") }),
                  el("div", { "class": "muted tiny tnum", style: { marginTop: "2px" }, text: "of " + M.fmt(r.total, "int") })]);
            var bar = sev === "high" || sev === "some" ? el("div", { style: { height: "3px", borderRadius: "2px", background: "var(--surface-3)", marginTop: "6px" } },
              el("div", { style: { height: "3px", borderRadius: "2px", width: Math.max(2, Math.round(100 * r.gaps / (max || 1))) + "%", background: sv.c } })) : null;
            var b = el("button", { type: "button", "aria-pressed": String(on), title: r.note || r.title,
              style: { display: "block", width: "100%", textAlign: "left", font: "inherit", cursor: "pointer", padding: "9px 18px 9px 15px", border: 0,
                       borderBottom: "1px solid var(--line-2)", borderLeft: "3px solid " + (on ? "var(--brand-2)" : sv.c),
                       background: on ? "var(--sel)" : "transparent", color: "var(--fg)" } }, [
              el("div", { "class": "row", style: { flexWrap: "nowrap", alignItems: "flex-start", gap: "10px" } }, [
                el("span", { "class": "muted tiny tnum", style: { width: "18px", paddingTop: "2px" }, text: String(i + 1) }),
                el("div", { style: { flex: 1, minWidth: 0 } }, [
                  el("div", { style: { fontWeight: 600, fontSize: "13.3px", color: sev === "clean" ? "var(--muted)" : "var(--fg)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }, text: r.title }),
                  el("div", { "class": "muted tiny", style: { overflow: "hidden", display: "-webkit-box", webkitLineClamp: "2", webkitBoxOrient: "vertical", lineHeight: "1.35" }, text: r.note || "" }),
                  (r.substitutes || []).length ? el("div", { "class": "tiny", style: { color: "var(--warn)", marginTop: "2px" }, text: r.substitutes.length + " substituted column" + (r.substitutes.length > 1 ? "s" : "") }) : null,
                  bar]),
                right])]);
            b.addEventListener("mouseenter", function () { if (r.key !== picked) b.style.background = "var(--hover)"; });
            b.addEventListener("mouseleave", function () { if (r.key !== picked) b.style.background = "transparent"; });
            b.addEventListener("click", function () { pick(r.key); });
            listBox.appendChild(b);
          });
          if (!rows.length) listBox.appendChild(el("div", { "class": "notice ok", style: { margin: "14px 18px" } }, [M.icon("info", 16), el("span", { text: "Nothing to fix on this run - every one of the " + counts.length + " came back clean." })]));
        }

        function sweep(refresh) {
          if (!session) { drawList(); return Promise.resolve(); }
          counting = true; tiles(); drawList();
          var ses = session;
          return M.get(API + "/counts?session=" + encodeURIComponent(ses) + (refresh ? "&refresh=1" : "")).then(function (d) {
            if (!ctx.alive() || ses !== session) return;
            counts = d.counts || []; counting = false; tiles(); drawList();
          }, function (e) {
            if (!ctx.alive() || ses !== session) return;
            counting = false; tiles(); drawList(); M.toast(e.message, "bad");
          });
        }
        recount.addEventListener("click", function () { M.action(recount, "Counting…", function () { return sweep(true); }); });
        sesSel.addEventListener("change", function () {
          session = sesSel.value; counts = null; picked = ""; data = null; drawSesInfo(); setUrl(); drawEmptyRight(); sweep(false);
        });

        // ── the chosen report ────────────────────────────────────────────────────────────────────
        function drawEmptyRight() {
          if (previewCtl) previewCtl.abort(); grid = null; M.clear(right);
          right.appendChild(el("div", { "class": "dg-empty", style: { padding: "70px 20px" } }, [M.icon("gap", 30),
            el("div", { style: { marginTop: "8px" }, text: "Pick a report to see its rows. The one at the top has the most to answer for." })]));
        }

        function pick(key) {
          picked = key; setUrl(); drawList();
          var chosen = (counts || []).filter(function (r) { return r.key === key; })[0] || (p.reports || []).filter(function (r) { return r.key === key; })[0] || { key: key, title: key };
          if (previewCtl) previewCtl.abort();
          previewCtl = window.AbortController ? new AbortController() : null;
          data = null; grid = null; view.onlyGaps = false; view.q = "";
          M.clear(right);
          var title = el("h2", { text: chosen.title }), note = el("div", { "class": "muted small", text: chosen.note || "" });
          var meta = el("div", { "class": "muted tiny", style: { marginTop: "3px" } }, [el("code", { text: (chosen.file || key) + ".xlsx" })]);
          var dl = el("button", { type: "button", "class": "btn primary", disabled: !ctx.me.can.export, title: ctx.me.can.export ? "Download this workbook, in its own template" : "You need the MIS export right to download." }, [M.icon("down", 15), "Download Excel"]);
          var estNote = el("div", { "class": "muted tiny", style: { maxWidth: "200px", textAlign: "right", marginTop: "4px" } });
          var notes = el("div", { "class": "stack", style: { gap: "6px" } });
          var head = el("div", { style: { padding: "13px 18px", borderBottom: "1px solid var(--line-2)" } }, [
            el("div", { "class": "row", style: { flexWrap: "nowrap", alignItems: "flex-start" } }, [el("div", { style: { flex: 1, minWidth: 0 } }, [title, note, meta]),
              el("div", { style: { display: "flex", flexDirection: "column", alignItems: "flex-end" } }, [dl, estNote])]), notes]);
          title.style.margin = "0 0 2px"; title.style.fontSize = "15.5px";
          var body = el("div", {}, el("div", { "class": "dg-empty" }, [el("span", { "class": "spin" }), el("div", { style: { marginTop: "8px" }, text: "Reading the run…" })]));
          right.appendChild(head); right.appendChild(body);

          dl.addEventListener("click", function () {
            var file = (data && data.file) || chosen.file || key;
            if (data && data.total > 40000) estNote.textContent = "writing " + M.fmt(data.total, "int") + " rows with live formulas - " + est(data.total);
            M.action(dl, "Building…", function () {
              return M.download("GET", API + "/export?report=" + encodeURIComponent(key) + (session ? "&session=" + encodeURIComponent(session) : ""), undefined, file + ".xlsx");
            }, function (n) { return "Downloaded " + n; }).then(drawEst, drawEst);
          });
          // A silent wait is what makes a slow download feel broken - say the size of the wait up front.
          function drawEst() { estNote.textContent = data && data.total > 40000 ? M.fmt(data.total, "int") + " rows · " + est(data.total) + " to build" : ""; }

          M.post(API + "/preview", { report: key, session: session || null, limit: PREVIEW_LIMIT }, { signal: previewCtl && previewCtl.signal }).then(function (d) {
            if (!ctx.alive() || picked !== key) return;
            data = d;
            if (d.title) title.textContent = d.title; if (d.note) note.textContent = d.note;
            M.clear(meta);
            meta.appendChild(el("code", { text: (d.file || chosen.file || key) + ".xlsx" }));
            meta.appendChild(document.createTextNode(" · "));
            meta.appendChild(el("b", { style: { color: d.gaps ? "var(--warn)" : "var(--ok)" }, text: M.fmt(d.gaps, "int") }));
            meta.appendChild(document.createTextNode(" gap of " + M.fmt(d.total, "int") + " rows"));
            if (d.total > d.rows.length) meta.appendChild(el("span", { text: " · showing the first " + M.fmt(d.rows.length, "int") + ", gaps first - the file has all of them" }));
            drawEst();
            // the caveats, before the reader starts trusting a number
            Object.keys(d.substituteNotes || {}).forEach(function (col) {
              notes.appendChild(el("div", { "class": "notice warn", style: { padding: "7px 11px", marginTop: "8px" } }, [M.icon("info", 15),
                el("span", { "class": "small" }, [el("b", { text: oneLine(col) }), " - " + d.substituteNotes[col]])]));
            });
            buildGrid(body, d, key);
          }, function (e) {
            if (!ctx.alive() || picked !== key || (e && e.name === "AbortError")) return;
            M.clear(body).appendChild(el("div", { "class": "dg-empty" }, el("div", { "class": "notice bad", style: { display: "inline-flex" }, text: e.message })));
          });
        }

        function buildGrid(body, d, key) {
          // Grid columns = the SHEET's headers, in the sheet's order. Headers carry real newlines ('SUB\nDIV') and can
          // repeat, so each gets a unique one-line key; the grid labels it with the header text.
          var seen = {}; keys = []; kindOf = {}; headOf = {}; textCols = {}; gapKey = null;
          (d.columns || []).forEach(function (h, i) {
            var k = oneLine(h) || "(column " + (i + 1) + ")"; if (seen[k]) { seen[k]++; k = k + " (" + seen[k] + ")"; } else seen[k] = 1;
            keys.push(k); kindOf[k] = (d.kinds || [])[i]; headOf[k] = h;
            if (kindOf[k] === "verdict") gapKey = k;
            for (var j = 0; j < Math.min(40, d.rows.length); j++) { var v = d.rows[j][i]; if (v !== null && v !== "" && v !== undefined) { textCols[k] = !M.isNum(v); break; } }
          });
          var subs = {}; (d.substitutes || []).forEach(function (s) { subs[oneLine(s)] = d.substituteNotes && d.substituteNotes[s] ? d.substituteNotes[s] : "fed from a substitute source"; });
          var gi = keys.indexOf(gapKey);

          function filtered(gs, skipCol) {
            var rows = d.rows, needle = view.q.trim().toLowerCase();
            if (view.onlyGaps && gi >= 0) rows = rows.filter(function (r) { return isGap(r[gi]); });
            if (needle) rows = rows.filter(function (r) { return r.some(function (v) { return String(v == null ? "" : v).toLowerCase().indexOf(needle) >= 0; }); });
            Object.keys(gs.filters || {}).forEach(function (c) {
              if (c === skipCol) return; var ci = keys.indexOf(c), vals = gs.filters[c]; if (ci < 0 || !vals || !vals.length) return;
              rows = rows.filter(function (r) { var v = r[ci]; v = v == null || v === "" ? "(blank)" : String(v); return vals.indexOf(v) >= 0; });
            });
            (gs.conds || []).forEach(function (c) { var ci = keys.indexOf(c.col); if (ci >= 0 && c.op) rows = rows.filter(function (r) { return condOk(r[ci], c); }); });
            return rows;
          }
          var tools = [];
          var only = el("input", { type: "checkbox", "aria-label": "Only gap rows", disabled: gi < 0 });
          only.addEventListener("change", function () { view.onlyGaps = only.checked; grid.reload(true); });
          tools.push(el("label", { "class": "row small", style: { gap: "5px", cursor: "pointer" }, title: gi < 0 ? "This report has no verdict column" : "A view of the loaded rows, never a different query" }, [only, "Only gap rows"]));
          var q = el("input", { type: "search", placeholder: "Find in the loaded rows", "aria-label": "Find in the loaded rows", style: { width: "200px", padding: "4px 9px", fontSize: "12.5px" } });
          q.addEventListener("input", M.debounce(function () { view.q = q.value; grid.reload(true); }, 250));
          tools.push(q);

          M.clear(body);
          grid = M.Grid(body, {
            id: "alloc-gaps." + key, title: "Rows", tools: tools,
            label: function (c) { return c; },
            isKey: function (c) { return c === gapKey || !!textCols[c]; },
            format: function (c) { return c === gapKey || textCols[c] ? "text" : undefined; },
            fetch: function (gs) {
              var rows = filtered(gs);
              if (gs.sort_col) {
                var ci = keys.indexOf(gs.sort_col), dir = gs.sort_dir === "desc" ? -1 : 1;
                rows = rows.slice().sort(function (a, b) {
                  var x = a[ci], y = b[ci], xn = M.isNum(x), yn = M.isNum(y);
                  if (x == null || x === "") return 1; if (y == null || y === "") return -1;
                  if (xn && yn) return (Number(x) - Number(y)) * dir;
                  return String(x).localeCompare(String(y), "en", { numeric: true }) * dir;
                });
              }
              var start = (gs.page - 1) * gs.limit;
              return Promise.resolve({ columns: keys, rows: rows.slice(start, start + gs.limit), total: rows.length });
            },
            values: function (c, gs) {
              var ci = keys.indexOf(c), cnt = {}, order = [];
              filtered(gs, c).forEach(function (r) { var v = r[ci]; v = v == null || v === "" ? "(blank)" : String(v); if (!(v in cnt)) { cnt[v] = 0; order.push(v); } cnt[v]++; });
              order.sort(function (a, b) { return a.localeCompare(b, "en", { numeric: true }); });
              return Promise.resolve(order.map(function (v) { return { value: v, label: v, count: cnt[v] }; }));
            },
            cell: function (td, c, v) {
              if (c === gapKey) {
                if (isGap(v)) { td.style.background = "var(--warn-soft)"; td.style.color = "var(--warn)"; td.style.fontWeight = "700"; }
                else td.style.color = "var(--muted)";
              } else if (kindOf[c] === "formula-only") td.style.color = "var(--muted)";
            }
          });
          // Mark the header cells: the verdict column, computed columns and substituted columns (DFS colours them).
          function markHeads() {
            var cols = grid.columns(), ths = grid.el.querySelectorAll("thead tr.cols th");
            for (var i = 0; i < ths.length; i++) {
              var c = cols[i], th = ths[i]; if (!c) continue;
              var tip = String(headOf[c] == null ? c : headOf[c]);
              if (c === gapKey) { th.style.background = "var(--warn-soft)"; th.style.color = "var(--warn)"; tip += " - the verdict: the sheet's GAP rule, evaluated in SQL"; }
              else if (subs[c]) { th.style.color = "var(--warn)"; tip += " - substituted: " + subs[c]; }
              else if (kindOf[c] === "computed") { th.style.fontStyle = "italic"; tip += " - computed (the sheet's formula, evaluated in SQL)"; }
              else if (kindOf[c] === "formula-only") { th.style.color = "var(--muted)"; tip += " - a formula column: Excel fills it in the downloaded file"; }
              th.title = tip;
            }
          }
          if (window.MutationObserver) new MutationObserver(markHeads).observe(grid.el, { childList: true, subtree: true });
          grid.applyDefaultView().then(function () { if (ctx.alive() && picked === key) grid.reload(true); });
        }

        drawEmptyRight();
        var first = sweep(false);
        if (picked) pick(picked);
        return first;
      });
    }
  });
})();

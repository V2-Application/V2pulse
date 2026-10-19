// Analytics command centre (/analytics). Everything is read live through the normal V2Pulse APIs, so the caller's
// roles and store scope apply: /api/v1/analytics/catalog (which dashboards they may open) and /api/v1/bi/query
// (cached figures). Sections whose dataset the caller cannot read are left out. DOM is built with textContent only.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var SVGNS = "http://www.w3.org/2000/svg";
  var BRIEF = "0 · Claude brief";
  var main = $("main"), tip = $("tip");

  // ------------------------------------------------------------------ plumbing
  function csrf() {
    var m = document.cookie.match(/(?:^|;\s*)pulse_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }
  function api(method, url, body) {
    var opts = { method: method, credentials: "same-origin", headers: { Accept: "application/json" } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.headers["X-CSRF-Token"] = csrf();
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (d) { return { ok: r.ok, status: r.status, data: d }; });
    });
  }
  // One BI query -> { rows: [{key: value}], snapshot } (empty rows when the caller may not read the dataset).
  function q(dataset, dims, measures, filters, sort, limit) {
    return api("POST", "/api/v1/bi/query", { dataset: dataset, dims: dims || [], measures: measures || [],
                                             filters: filters || [], sort: sort || null, limit: limit || 500 })
      .then(function (res) {
        if (!res.ok || !res.data || !res.data.data) return { rows: [], snapshot: null };
        var d = res.data.data, keys = d.columns.map(function (c) { return c.key; });
        return { snapshot: d.snapshot, rows: d.rows.map(function (r) {
          var o = {}; keys.forEach(function (k, i) { o[k] = r[i]; }); return o; }) };
      })
      .catch(function () { return { rows: [], snapshot: null }; });
  }
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function sv(tag, attrs, text) {
    var e = document.createElementNS(SVGNS, tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function n(v) { return v === null || v === undefined || isNaN(v) ? null : Number(v); }
  function fmt(v, kind) {
    v = n(v);
    if (v === null) return "–";
    if (kind === "pct") return (v * 100).toFixed(1) + "%";
    if (kind === "inr") {
      var a = Math.abs(v);
      if (a >= 1e7) return "₹" + (v / 1e7).toFixed(2) + " Cr";
      if (a >= 1e5) return "₹" + (v / 1e5).toFixed(1) + " L";
      return "₹" + Math.round(v).toLocaleString("en-IN");
    }
    if (kind === "dec") return v.toFixed(1);
    return Math.round(v).toLocaleString("en-IN");
  }
  function dark() {
    var t = document.documentElement.getAttribute("data-theme");
    return t === "dark" || (t !== "light" && window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches);
  }
  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  // Charts are drawn at the card's own width (1 unit = 1 px), so labels keep their real size in narrow cards.
  function widthOf(c, fallback) { var w = Math.round(c.getBoundingClientRect().width - 40); return w > 260 ? w : fallback; }

  // ------------------------------------------------------------------ tooltip
  function showTip(evt, head, rows) {
    tip.textContent = "";
    tip.appendChild(el("div", { "class": "h" }, head));
    rows.forEach(function (r) {
      var line = el("div", { "class": "r" });
      line.appendChild(el("span", {}, r[0])); line.appendChild(el("b", {}, r[1])); tip.appendChild(line);
    });
    tip.style.display = "block";
    var x = evt.clientX + 14, y = evt.clientY + 14, w = tip.offsetWidth, h = tip.offsetHeight;
    if (x + w > window.innerWidth - 8) x = evt.clientX - w - 14;
    if (y + h > window.innerHeight - 8) y = evt.clientY - h - 14;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  function hideTip() { tip.style.display = "none"; }
  function hover(node, head, rows) {
    node.addEventListener("pointermove", function (e) { showTip(e, head, rows); });
    node.addEventListener("pointerleave", hideTip);
    node.setAttribute("tabindex", "0");
    node.addEventListener("focus", function () {
      var b = node.getBoundingClientRect(); showTip({ clientX: b.right, clientY: b.top }, head, rows); });
    node.addEventListener("blur", hideTip);
  }

  // ------------------------------------------------------------------ layout helpers
  function section(id, title, color) {
    var s = el("div", { "class": "sect", id: id });
    var b = el("span", { "class": "bar" }); b.style.background = color; s.appendChild(b);
    s.appendChild(el("span", {}, title)); main.appendChild(s); return s;
  }
  function card(span, title, note, link) {
    var c = el("section", { "class": "card " + span });
    var h = el("div", { "class": "ch" }), left = el("div");
    left.appendChild(el("h2", {}, title)); if (note) left.appendChild(el("div", { "class": "note" }, note));
    h.appendChild(left);
    if (link) h.appendChild(el("a", { "class": "more", href: link.href }, link.text + " →"));
    c.appendChild(h); main.appendChild(c); return c;
  }
  function empty(c, text) { c.appendChild(el("div", { "class": "empty" }, text)); }

  // Horizontal bars, one series: label, bar, value. 4px rounded ends, 2px gaps; tooltip per bar.
  function hbars(c, items, opts) {
    if (!items.length) return empty(c, opts.empty || "Nothing to show.");
    var W = widthOf(c, 640), row = 28, lw = Math.min(opts.labelWidth || 170, Math.round(W * 0.38)), vw = 70, H = items.length * row + 6;
    var max = Math.max.apply(null, items.map(function (i) { return Math.abs(i.value) || 0; })) || 1;
    if (opts.max) max = Math.max(max, opts.max);
    var s = sv("svg", { viewBox: "0 0 " + W + " " + H, "class": "chart", role: "img", "aria-label": opts.title || "" });
    items.forEach(function (it, i) {
      var y = i * row + 3, bw = Math.max(2, (W - lw - vw) * (Math.abs(it.value) || 0) / max);
      var g = sv("g");
      g.appendChild(sv("text", { x: lw - 10, y: y + 17, "text-anchor": "end", "class": "lbl" }, it.label.length > 24 ? it.label.slice(0, 23) + "…" : it.label));
      g.appendChild(sv("rect", { x: lw, y: y + 4, width: bw, height: row - 10, rx: 4, fill: it.color || opts.color || css("--series-1") }));
      g.appendChild(sv("text", { x: lw + bw + 8, y: y + 17, "class": "val" }, fmt(it.value, opts.fmt)));
      g.appendChild(sv("rect", { x: 0, y: y, width: W, height: row, fill: "transparent" }));
      hover(g, it.label, it.tip || [[opts.measure || "", fmt(it.value, opts.fmt)]]);
      s.appendChild(g);
    });
    c.appendChild(s);
  }

  // Stacked horizontal bars (priority mix): segments separated by a 2px surface gap.
  function stacked(c, items, keys) {
    var W = widthOf(c, 640), row = 34, lw = Math.min(190, Math.round(W * 0.40)), H = items.length * row + 6;
    var max = Math.max.apply(null, items.map(function (i) { return i.total; })) || 1;
    var s = sv("svg", { viewBox: "0 0 " + W + " " + H, "class": "chart", role: "img", "aria-label": "Alerts by type and priority" });
    items.forEach(function (it, i) {
      var y = i * row + 3, x = lw, g = sv("g");
      g.appendChild(sv("text", { x: lw - 10, y: y + 20, "text-anchor": "end", "class": "lbl" }, it.label.length > 26 ? it.label.slice(0, 25) + "…" : it.label));
      keys.forEach(function (k) {
        var v = it.parts[k.key] || 0; if (!v) return;
        var w = Math.max(2, (W - lw - 120) * v / max);
        g.appendChild(sv("rect", { x: x, y: y + 7, width: Math.max(1, w - 2), height: row - 14, rx: 3, fill: k.color }));
        x += w;
      });
      g.appendChild(sv("text", { x: x + 6, y: y + 20, "class": "val" }, fmt(it.total) + (it.parts.P1 ? "  · " + fmt(it.parts.P1) + " P1" : "")));
      g.appendChild(sv("rect", { x: 0, y: y, width: W, height: row, fill: "transparent" }));
      hover(g, it.label, keys.map(function (k) { return [k.label, fmt(it.parts[k.key] || 0)]; }).concat([["All", fmt(it.total)]]));
      s.appendChild(g);
    });
    c.appendChild(s);
  }

  // Two-series line (this year solid, last year dashed) with a crosshair that snaps to the nearest week.
  function lines(c, pts, opts) {
    if (pts.length < 2) return empty(c, "Not enough weeks yet.");
    var W = widthOf(c, 720), H = 260, L = 64, R = 14, T = 12, B = 30;
    var max = 0; pts.forEach(function (p) { max = Math.max(max, p.a || 0, p.b || 0); });
    var step = Math.pow(10, Math.floor(Math.log10(max || 1))), nice = Math.ceil(max / step) * step;
    if (nice / step <= 2) step /= 2;
    nice = Math.ceil(max / step) * step || 1;
    var X = function (i) { return L + (W - L - R) * i / (pts.length - 1); }, Y = function (v) { return T + (H - T - B) * (1 - v / nice); };
    var s = sv("svg", { viewBox: "0 0 " + W + " " + H, "class": "chart", role: "img", "aria-label": opts.title });
    for (var v = 0; v <= nice + 1e-9; v += step) {
      s.appendChild(sv("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), "class": "grid" }));
      s.appendChild(sv("text", { x: L - 8, y: Y(v) + 4, "text-anchor": "end" }, fmt(v, "inr").replace(".00", "")));
    }
    var lastMonth = "";
    pts.forEach(function (p, i) {
      var m = p.label.slice(0, 7);
      if (m !== lastMonth) {
        lastMonth = m;
        var d = new Date(p.label + "T00:00:00");
        s.appendChild(sv("text", { x: X(i), y: H - 10, "text-anchor": "middle" }, d.toLocaleString("en-IN", { month: "short" })));
      }
    });
    function path(key) {
      return pts.map(function (p, i) { return (i ? "L" : "M") + X(i).toFixed(1) + "," + Y(p[key] || 0).toFixed(1); }).join(" ");
    }
    s.appendChild(sv("path", { d: path("b"), fill: "none", stroke: css("--series-ly"), "stroke-width": 2, "stroke-dasharray": "5 4" }));
    s.appendChild(sv("path", { d: path("a"), fill: "none", stroke: css("--series-1"), "stroke-width": 2 }));
    var last = pts.length - 1;
    s.appendChild(sv("circle", { cx: X(last), cy: Y(pts[last].a || 0), r: 4, fill: css("--series-1"), stroke: css("--surface"), "stroke-width": 2 }));
    var hair = sv("line", { y1: T, y2: H - B, stroke: css("--muted"), "stroke-width": 1, visibility: "hidden" });
    s.appendChild(hair);
    var over = sv("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent" });
    over.addEventListener("pointermove", function (e) {
      var b = s.getBoundingClientRect(), px = (e.clientX - b.left) * W / b.width;
      var i = Math.max(0, Math.min(last, Math.round((px - L) / (W - L - R) * last))), p = pts[i];
      hair.setAttribute("x1", X(i)); hair.setAttribute("x2", X(i)); hair.setAttribute("visibility", "visible");
      var g = p.b ? (p.a / p.b - 1) : null;
      showTip(e, "Week of " + p.label, [["This year", fmt(p.a, "inr")], ["Last year", fmt(p.b, "inr")],
                                        ["Growth", g === null ? "–" : (g * 100).toFixed(1) + "%"]]);
    });
    over.addEventListener("pointerleave", function () { hair.setAttribute("visibility", "hidden"); hideTip(); });
    s.appendChild(over);
    c.appendChild(s);
  }

  function legend(c, items) {
    var lg = el("div", { "class": "legend" });
    items.forEach(function (it) {
      var sp = el("span"), sw = el("i", { "class": it.cls || "" });
      if (!it.cls || it.cls === "line") sw.style.background = it.color;
      sp.appendChild(sw); sp.appendChild(document.createTextNode(it.label)); lg.appendChild(sp);
    });
    c.appendChild(lg);
  }

  // ------------------------------------------------------------------ links into the dashboards
  var DASH = {};   // dataset -> [{id, name, pages}]
  function pageLink(dataset, nameHint, pageName) {
    var list = DASH[dataset] || [];
    var d = list.filter(function (x) { return !nameHint || x.name.indexOf(nameHint) >= 0; })[0] || list[0];
    if (!d) return null;
    var i = pageName ? d.pages.indexOf(pageName) : 0;
    return "/reports/dynamic/" + encodeURIComponent(d.id) + (i > 0 ? "?page=" + i : "");
  }

  // ------------------------------------------------------------------ masthead tiles
  // A tile is placed at once (so the order is fixed) and filled in when its figures arrive.
  function slot(label, color, href) {
    var t = el("a", { "class": "tile", href: href || "#" });
    var k = el("div", { "class": "k" }); var d = el("span", { "class": "dot" }); d.style.background = color;
    k.appendChild(d); k.appendChild(document.createTextNode(label));
    var v = el("div", { "class": "v" }, "…"), s = el("div", { "class": "s" }, "Loading");
    t.appendChild(k); t.appendChild(v); t.appendChild(s); $("tiles").appendChild(t);
    return function (value, sub) { v.textContent = value; s.textContent = sub; };
  }

  // ------------------------------------------------------------------ Claude's brief
  function renderBrief(box, text) {
    box.textContent = "";
    var lines = String(text || "").split(/\r?\n/).map(function (l) { return l.trim(); }).filter(Boolean);
    var list = null;
    lines.forEach(function (l, i) {
      if (/^(today'?s priorities|actions)\s*:?$/i.test(l)) { box.appendChild(el("h3", {}, l.replace(/:$/, ""))); list = null; return; }
      if (/^[-•*]\s+/.test(l)) {
        if (!list) { list = el("ul"); box.appendChild(list); }
        list.appendChild(el("li", {}, l.replace(/^[-•*]\s+/, ""))); return;
      }
      list = null;
      box.appendChild(el("p", { "class": i === 0 ? "lead" : "" }, l));
    });
  }

  // ------------------------------------------------------------------ sections
  function aiSection(ds) {
    var NB = [{ dim: "category", op: "not_in", values: [BRIEF] }];
    section("s-brief", "Today · AI suggestions", css("--ai"));
    var bc = card("c8", "Claude's brief", "Written each morning by Claude from the day's alerts; every figure is in the AI suggestions report.",
                  { href: pageLink("ai_suggestions", null, "Claude's brief") || "#", text: "AI suggestions" });
    var chips = el("div", { "class": "chips", role: "group", "aria-label": "Brief for" }); bc.appendChild(chips);
    var body = el("div", { "class": "brief-body" }, "Loading the brief…"); bc.appendChild(body);
    var foot = el("div", { "class": "brief-foot" }); bc.appendChild(foot);
    var mix = card("c4", "Alerts by type and priority", "Every alert has an owner team and a suggested action.",
                   { href: pageLink("ai_suggestions") || "#", text: "Open" });

    var tiles = q(ds, [], ["suggestions", "p1", "buy_value", "buy_qty", "buy_p1", "send_qty", "send_n", "send_p1", "store_n",
                           "store_p1", "dc_p1"], NB, null, 1);
    var briefs = q(ds, ["brief_scope", "brief_text", "as_of"], ["briefs"], [{ dim: "category", op: "in", values: [BRIEF] }],
                   { by: "brief_scope", dir: "asc" }, 50);
    var bytype = q(ds, ["category", "priority"], ["suggestions"], NB, null, 100);

    var t1 = slot("P1 · act today", css("--p1"), pageLink(ds)),
        t2 = slot("Suggested buying", css("--ai"), pageLink(ds, null, "What to buy")),
        t3 = slot("Send to stores", css("--series-1"), pageLink(ds, null, "What to send to stores")),
        t4 = slot("Store issues", css("--p2"), pageLink(ds, null, "What is wrong in stores"));
    tiles.then(function (r) {
      var t = r.rows[0] || {};
      if (r.snapshot) $("asof").textContent = "Alerts as of " + r.snapshot + " · refreshed every morning";
      t1(fmt(t.p1), "of " + fmt(t.suggestions) + " alerts today");
      t2(fmt(t.buy_value, "inr"), fmt(t.buy_qty) + " units · " + fmt(t.buy_p1) + " P1 articles");
      t3(fmt(t.send_qty), "units on " + fmt(t.send_n) + " lines · " + fmt(t.send_p1) + " P1");
      t4(fmt(t.store_n), fmt(t.store_p1) + " P1 · " + fmt(t.dc_p1) + " P1 DC alerts");
    });
    briefs.then(function (r) {
      var rows = r.rows.filter(function (x) { return x.brief_text; });
      if (!rows.length) { body.textContent = "No brief yet for your scope. Briefs are written for the company, each zone and each team."; return; }
      function pick(i) {
        Array.prototype.forEach.call(chips.children, function (b, j) { b.setAttribute("aria-pressed", String(i === j)); });
        renderBrief(body, rows[i].brief_text);
        foot.textContent = "Claude Sonnet 4.5 on Snowflake Cortex · as of " + rows[i].as_of;
      }
      rows.forEach(function (x, i) {
        var b = el("button", { "class": "chip", type: "button", "aria-pressed": "false" }, x.brief_scope.replace(/^\d · /, ""));
        b.addEventListener("click", function () { pick(i); }); chips.appendChild(b);
      });
      pick(0);
    });
    bytype.then(function (r) {
      var keys = [{ key: "P1", label: "P1 · act today", color: css("--p1") }, { key: "P2", label: "P2 · this week", color: css("--p2") },
                  { key: "P3", label: "P3 · plan", color: css("--p3") }];
      var m = {};
      r.rows.forEach(function (x) {
        var c = x.category; if (!c) return;
        m[c] = m[c] || { label: c.slice(4), parts: {}, total: 0, order: c };
        m[c].parts[(x.priority || "").slice(0, 2)] = n(x.suggestions) || 0; m[c].total += n(x.suggestions) || 0;
      });
      var items = Object.keys(m).sort().map(function (k) { return m[k]; });
      legend(mix, keys.map(function (k) { return { label: k.label, color: k.color }; }));
      if (!items.length) return empty(mix, "No alerts in your scope today.");
      stacked(mix, items, keys);
      q(ds, ["owner"], ["suggestions", "p1", "p2"], NB, { by: "p1", dir: "desc" }, 10).then(function (o) {
        var rows = o.rows.filter(function (x) { return x.owner; });
        if (!rows.length) return;
        mix.appendChild(el("h2", { style: "font-size:14px;margin-top:4px" }, "By owner team"));
        var w = el("div", { "class": "tbl" }), t = el("table", { "class": "mini" }), h = el("tr");
        ["Team", "Alerts", "P1", "P2"].forEach(function (x) { h.appendChild(el("th", { scope: "col" }, x)); }); t.appendChild(h);
        rows.forEach(function (x) {
          var tr = el("tr"); tr.appendChild(el("td", {}, x.owner));
          [x.suggestions, x.p1, x.p2].forEach(function (v) { tr.appendChild(el("td", {}, fmt(v))); }); t.appendChild(tr);
        });
        t.style.minWidth = "0"; w.appendChild(t); mix.appendChild(w);
      });
    });

    // Where the alerts are: zone x type, P1 count shaded on one sequential ramp.
    section("s-where", "Where the alerts are", css("--ai"));
    var wc = card("c12", "Zones and alert types", "Number in each cell: P1 alerts (all alerts on hover). Darker = more P1.",
                  { href: pageLink(ds, null, "What is wrong in stores") || "#", text: "Store issues" });
    q(ds, ["zone", "category"], ["p1", "suggestions", "value"], NB, null, 500).then(function (r) {
      var zones = {}, cats = {};
      r.rows.forEach(function (x) { if (!x.zone || !x.category) return; zones[x.zone] = zones[x.zone] || {}; zones[x.zone][x.category] = x; cats[x.category] = 1; });
      var cl = Object.keys(cats).sort(), zl = Object.keys(zones).sort(function (a, b) {
        return a === "Company / DC" ? 1 : b === "Company / DC" ? -1 : a.localeCompare(b); });
      if (!zl.length) return empty(wc, "No alerts in your scope today.");
      var max = 0; r.rows.forEach(function (x) { max = Math.max(max, n(x.p1) || 0); });
      var ramp = ["--seq-0", "--seq-1", "--seq-2", "--seq-3", "--seq-4", "--seq-5"].map(css);
      var wrap = el("div", { "class": "heat-wrap" }), t = el("table", { "class": "heat" });
      var hr = el("tr"); hr.appendChild(el("th", { "class": "z", scope: "col" }, "Zone"));
      cl.forEach(function (c) { hr.appendChild(el("th", { scope: "col" }, c.slice(4))); });
      hr.appendChild(el("th", { scope: "col" }, "All P1")); t.appendChild(hr);
      zl.forEach(function (z) {
        var tr = el("tr"); tr.appendChild(el("td", { "class": "z" }, z)); var tot = 0;
        cl.forEach(function (c) {
          var x = zones[z][c] || {}, p = n(x.p1) || 0; tot += p;
          var lvl = p === 0 ? 0 : Math.min(5, 1 + Math.floor(4 * p / (max || 1)));
          var td = el("td", {}, p ? fmt(p) : "·"); td.style.background = ramp[lvl];
          td.style.color = lvl >= 4 ? (dark() ? "#0b1119" : "#ffffff") : css("--fg");
          hover(td, z + " · " + c.slice(4), [["P1 alerts", fmt(p)], ["All alerts", fmt(x.suggestions || 0)], ["Value", fmt(x.value, "inr")]]);
          tr.appendChild(td);
        });
        tr.appendChild(el("td", { "class": "tot" }, fmt(tot))); t.appendChild(tr);
      });
      wrap.appendChild(t); wc.appendChild(wrap);
      var sc = el("div", { "class": "scale" }); sc.appendChild(document.createTextNode("Fewer P1"));
      ramp.forEach(function (c) { var i = el("i"); i.style.background = c; sc.appendChild(i); });
      sc.appendChild(document.createTextNode("More P1")); wc.appendChild(sc);
    });

    // Top actions per owner team.
    section("s-actions", "Top actions today", css("--ai"));
    var ac = card("c12", "Most urgent first, by team", "Highest-priority alerts with the biggest value at stake. Open a list for every alert.", null);
    var grid = el("div", { "class": "acts" }); ac.appendChild(grid);
    var LISTS = [
      { key: "buy", title: "Buying · what to buy", page: "What to buy", dims: ["buy_priority", "buy_item", "buy_action", "buy_alert"],
        w: "buy_item", a: "buy_action", m: "buy_alert", v: "buy_value", s: "buy_score" },
      { key: "send", title: "Allocation · send to stores", page: "What to send to stores",
        dims: ["send_priority", "send_where", "send_item", "send_action"], w: "send_where", a: "send_action", m: "send_item",
        v: "send_value", s: "send_score" },
      { key: "store", title: "Store ops · fix in stores", page: "What is wrong in stores",
        dims: ["store_priority", "store_where", "store_alert", "store_action"], w: "store_where", a: "store_alert", m: "store_action",
        v: "store_value", s: "store_score" },
      { key: "dc", title: "RDC · DC placement", page: "DC placement", dims: ["dc_priority", "dc_where", "dc_mc", "dc_alert", "dc_action"],
        w: "dc_where", a: "dc_alert", m: "dc_action", v: "dc_value", s: "dc_score", extra: "dc_mc" }];
    LISTS.forEach(function (L) {
      var box = el("div", { "class": "act" }), h = el("h3", {}, L.title);
      var link = pageLink(ds, null, L.page);
      if (link) { var a = el("a", { href: link, "class": "more" }, "All →"); var sm = el("small"); sm.appendChild(a); h.appendChild(sm); }
      box.appendChild(h); var ol = el("ol"); box.appendChild(ol); grid.appendChild(box);
      q(ds, L.dims, [L.v, L.s], NB, { by: L.s, dir: "desc" }, 7).then(function (r) {
        var rows = r.rows.filter(function (x) { return x[L.w]; }).slice(0, 5);
        if (!rows.length) { ol.appendChild(el("li", {}, "Nothing urgent in your scope.")); return; }
        rows.forEach(function (x) {
          var li = el("li"), pr = (x[L.dims[0]] || "P3").slice(0, 2);
          li.appendChild(el("span", { "class": "pill " + pr }, pr));
          var t = el("div");
          t.appendChild(el("div", { "class": "w" }, (L.extra ? x[L.extra] + " · " : "") + x[L.w]));
          t.appendChild(el("div", {}, x[L.a]));
          t.appendChild(el("div", { "class": "m" }, x[L.m] + (n(x[L.v]) ? " · " + fmt(x[L.v], "inr") : "")));
          li.appendChild(t); ol.appendChild(li);
        });
      });
    });
  }

  function heroSection(ds) {
    section("s-hero", "Hero articles health", css("--hero"));
    var fy = (function () { var d = new Date(), y = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1; return y + "-04-01"; })();
    var today = new Date().toISOString().slice(0, 10);
    var th = slot("Top-100 availability", css("--hero"), pageLink(ds, "All pages"));
    q(ds, [], ["t100_sale", "t100_avail", "t100_oos", "t100_lost"], [], null, 1).then(function (r) {
      var t = r.rows[0] || {};
      th(fmt(t.t100_avail, "pct"), fmt(t.t100_oos) + " out of stock · " + fmt(t.t100_lost, "inr") + " at risk");
    });
    var zc = card("c5", "Top-100 availability by zone", "Share of stores that should stock a top-100 hero and do.",
                  { href: pageLink(ds, "All pages", "Locations") || "#", text: "Locations" });
    q(ds, ["zone"], ["t100_avail", "t100_oos", "t100_lost", "t100_sale"], [], { by: "t100_avail", dir: "asc" }, 20).then(function (r) {
      hbars(zc, r.rows.filter(function (x) { return x.zone && n(x.t100_avail) !== null; }).map(function (x) {
        return { label: x.zone, value: n(x.t100_avail), tip: [["Availability", fmt(x.t100_avail, "pct")], ["Out of stock", fmt(x.t100_oos)],
                 ["Sale at risk (7 days)", fmt(x.t100_lost, "inr")], ["Top-100 sale L30D", fmt(x.t100_sale, "inr")]] };
      }), { fmt: "pct", max: 1, labelWidth: 120, measure: "Availability", title: "Availability by zone" });
    });
    var tc = card("c7", "Weekly sale against last year", "This fiscal year, your stores. Last year = same stores, same weekday-aligned week.",
                  { href: pageLink(ds, "All pages", "Trend") || "#", text: "Trend" });
    legend(tc, [{ label: "This year", color: css("--series-1"), cls: "line" }, { label: "Last year", cls: "dash" }]);
    q(ds, ["week"], ["sale_v", "ly_sale_v"], [{ dim: "date", op: "between", values: [fy, today] }], { by: "week", dir: "asc" }, 60)
      .then(function (r) {
        var pts = r.rows.filter(function (x) { return x.week && n(x.sale_v); })
                        .map(function (x) { return { label: String(x.week).slice(0, 10), a: n(x.sale_v), b: n(x.ly_sale_v) }; });
        if (pts.length > 1) pts.pop();   // the running week is incomplete
        lines(tc, pts, { title: "Weekly sale this year and last year" });
      });
    var hc = card("c12", "The top 10 heroes in your stores", "Last 30 days. Availability = stores in stock ÷ stores that should stock it.",
                  { href: pageLink(ds, "All pages", "Top 100") || "#", text: "Top 100" });
    q(ds, ["gen_article", "gen_article_desc", "major_category"], ["t100_sale", "t100_avail", "t100_oos", "t100_stock", "t100_fcst"],
      [], { by: "t100_sale", dir: "desc" }, 10).then(function (r) {
      var rows = r.rows.filter(function (x) { return n(x.t100_sale); });
      if (!rows.length) return empty(hc, "No top-100 heroes sold in your stores in the last 30 days.");
      var w = el("div", { "class": "tbl" }), t = el("table", { "class": "mini" }), h = el("tr");
      ["Article", "Sale L30D", "Availability", "Out of stock", "Store stock", "Forecast 30 days"].forEach(function (x) { h.appendChild(el("th", { scope: "col" }, x)); });
      t.appendChild(h);
      rows.forEach(function (x) {
        var tr = el("tr"), a = el("td"); a.appendChild(el("span", { "class": "t" }, x.gen_article_desc));
        a.appendChild(el("span", { "class": "m" }, x.major_category + " · " + x.gen_article)); tr.appendChild(a);
        tr.appendChild(el("td", {}, fmt(x.t100_sale, "inr")));
        var av = el("td"), avs = el("span", {}, fmt(x.t100_avail, "pct"));
        if (n(x.t100_avail) !== null && x.t100_avail < 0.7) { avs.style.color = css("--p1"); avs.textContent = "▼ " + avs.textContent; }
        av.appendChild(avs); tr.appendChild(av);
        tr.appendChild(el("td", {}, fmt(x.t100_oos))); tr.appendChild(el("td", {}, fmt(x.t100_stock))); tr.appendChild(el("td", {}, fmt(x.t100_fcst)));
        t.appendChild(tr);
      });
      w.appendChild(t); hc.appendChild(w);
    });
  }

  function fillSection(ds) {
    section("s-fill", "Store fill rate", css("--series-1"));
    var tf = slot("Store fill rate", css("--series-1"), pageLink(ds));
    q(ds, [], ["fill_rate", "below85", "short_q", "short_v", "stores"], [], null, 1).then(function (r) {
      var t = r.rows[0] || {};
      tf(fmt(t.fill_rate, "pct"), fmt(t.below85) + " categories below 85% · " + fmt(t.short_v, "inr") + " short");
    });
    var zc = card("c5", "Fill rate by zone", "Sales-area stock up to MBQ ÷ MBQ (ARS norm). Hover for the shortfall.",
                  { href: pageLink(ds, null, "Overview") || "#", text: "Overview" });
    q(ds, ["zone"], ["fill_rate", "below85", "short_q", "short_v", "stores"], [], { by: "fill_rate", dir: "asc" }, 20).then(function (r) {
      hbars(zc, r.rows.filter(function (x) { return x.zone && n(x.fill_rate) !== null; }).map(function (x) {
        return { label: x.zone, value: n(x.fill_rate), color: n(x.fill_rate) < 0.85 ? css("--p2") : css("--series-1"),
                 tip: [["Fill rate", fmt(x.fill_rate, "pct")], ["Stores", fmt(x.stores)], ["Categories below 85%", fmt(x.below85)],
                       ["Short (units)", fmt(x.short_q)], ["Short (value)", fmt(x.short_v, "inr")]] };
      }), { fmt: "pct", max: 1, labelWidth: 120, measure: "Fill rate", title: "Fill rate by zone" });
      legend(zc, [{ label: "Below 85%", color: css("--p2") }, { label: "85% or more", color: css("--series-1") }]);
    });
    var sc = card("c7", "Stores with the lowest fill rate", "Biggest gaps against MBQ first. The AI suggestions say what to send.",
                  { href: pageLink(ds, null, "Stores") || "#", text: "All stores" });
    q(ds, ["store", "store_name", "zone"], ["fill_rate", "below85", "short_q", "short_v", "excess_q"], [], { by: "fill_rate", dir: "asc" }, 10)
      .then(function (r) {
        var rows = r.rows.filter(function (x) { return n(x.fill_rate) !== null; });
        if (!rows.length) return empty(sc, "No MBQ for your stores in the latest ARS run.");
        var w = el("div", { "class": "tbl" }), t = el("table", { "class": "mini" }), h = el("tr");
        ["Store", "Fill rate", "Categories < 85%", "Short units", "Short value", "Excess units"].forEach(function (x) {
          h.appendChild(el("th", { scope: "col" }, x)); });
        t.appendChild(h);
        rows.forEach(function (x) {
          var tr = el("tr"), a = el("td"); a.appendChild(el("span", { "class": "t" }, x.store_name || x.store));
          a.appendChild(el("span", { "class": "m" }, x.store + " · " + x.zone)); tr.appendChild(a);
          var f = el("td"), fs = el("span", {}, fmt(x.fill_rate, "pct"));
          if (n(x.fill_rate) < 0.75) { fs.style.color = css("--p1"); fs.textContent = "▼ " + fs.textContent; }
          f.appendChild(fs); tr.appendChild(f);
          [fmt(x.below85), fmt(x.short_q), fmt(x.short_v, "inr"), fmt(x.excess_q)].forEach(function (v) { tr.appendChild(el("td", {}, v)); });
          t.appendChild(tr);
        });
        w.appendChild(t); sc.appendChild(w);
      });
    var cc = card("c12", "Where the shortfall is: major categories", "Value short of MBQ at selling price, across your stores.",
                  { href: pageLink(ds, null, "Categories") || "#", text: "Categories" });
    q(ds, ["major_category"], ["short_v", "short_q", "fill_rate", "below85", "rdc_stock"], [], { by: "short_v", dir: "desc" }, 12)
      .then(function (r) {
        hbars(cc, r.rows.filter(function (x) { return x.major_category && n(x.short_v); }).map(function (x) {
          return { label: x.major_category, value: n(x.short_v),
                   tip: [["Short (value)", fmt(x.short_v, "inr")], ["Short (units)", fmt(x.short_q)], ["Fill rate", fmt(x.fill_rate, "pct")],
                         ["Stores below 85%", fmt(x.below85)]] };
        }), { fmt: "inr", labelWidth: 200, measure: "Short (value)", title: "Shortfall by major category" });
      });
  }

  function peopleSection(ds) {
    section("s-people", "People · Karma productivity", css("--karma"));
    var HO = [{ dim: "location_group", op: "in", values: ["HO"] }];
    var tk = slot("HO checklist fill rate", css("--karma"), pageLink(ds, "productivity"));
    var kc = card("c5", "Head office yesterday", "Y/N checklist and tasks, from Karma.", { href: pageLink(ds, "productivity") || "#", text: "Karma" });
    q(ds, [], ["employees", "filled", "not_filled", "on_leave", "fill_rate", "avg_hours", "raised_yd", "pending_overaged"], HO, null, 1)
      .then(function (r) {
        var t = r.rows[0] || {};
        tk(fmt(t.fill_rate, "pct"), fmt(t.not_filled) + " not filled · " + fmt(t.on_leave) + " on leave");
        var w = el("div", { "class": "tbl" }), tb = el("table", { "class": "mini" });
        [["Employees", fmt(t.employees)], ["Filled", fmt(t.filled)], ["Not filled", fmt(t.not_filled)], ["On leave", fmt(t.on_leave)],
         ["Fill rate (of present)", fmt(t.fill_rate, "pct")], ["Average hours (filled)", fmt(t.avg_hours, "dec")],
         ["Tasks raised", fmt(t.raised_yd)], ["Overaged pending tasks", fmt(t.pending_overaged)]].forEach(function (x) {
          var tr = el("tr"); tr.appendChild(el("td", {}, x[0])); tr.appendChild(el("td", {}, x[1])); tb.appendChild(tr); });
        tb.style.minWidth = "0"; w.appendChild(tb); kc.appendChild(w);
      });
    var dc = card("c7", "Departments with the lowest fill rate", "Filled ÷ present, yesterday. Hover for the counts.",
                  { href: pageLink(ds, "productivity", "Department summary") || "#", text: "Department summary" });
    q(ds, ["dept"], ["fill_rate", "employees", "not_filled", "on_leave", "pending_overaged"], HO, { by: "fill_rate", dir: "asc" }, 10)
      .then(function (r) {
        hbars(dc, r.rows.filter(function (x) { return x.dept && n(x.fill_rate) !== null; }).map(function (x) {
          return { label: x.dept, value: n(x.fill_rate), color: css("--karma"),
                   tip: [["Fill rate", fmt(x.fill_rate, "pct")], ["Employees", fmt(x.employees)], ["Not filled", fmt(x.not_filled)],
                         ["On leave", fmt(x.on_leave)], ["Overaged tasks", fmt(x.pending_overaged)]] };
        }), { fmt: "pct", max: 1, measure: "Fill rate", title: "Lowest fill rate departments" });
      });
  }

  function directory(groups) {
    section("s-dir", "All Analytics dashboards", css("--fg-2"));
    var c = card("c12", "Dashboards you can open", "Each opens with its own filters; the buttons jump straight to a page.", null);
    var grid = el("div", { "class": "dir" }); c.appendChild(grid);
    var COLORS = { ai: "--ai", hero: "--hero", karma: "--karma" };
    groups.forEach(function (g) {
      g.dashboards.forEach(function (d) {
        var url = "/reports/dynamic/" + encodeURIComponent(d.id), b = el("article", { "class": "dcard" });
        var tg = el("span", { "class": "tag" }, g.title.split(" · ")[0]); tg.style.background = css(COLORS[g.key] || "--fg-2");
        b.appendChild(tg);
        var h = el("h3"); h.appendChild(el("a", { href: url }, d.name)); b.appendChild(h);
        if (d.description) b.appendChild(el("p", {}, d.description));
        if (d.pages.length > 1) {
          var ch = el("div", { "class": "dchips" });
          d.pages.forEach(function (p, i) { ch.appendChild(el("a", { href: url + (i ? "?page=" + i : "") }, p)); });
          b.appendChild(ch);
        }
        grid.appendChild(b);
      });
    });
  }

  // ------------------------------------------------------------------ start
  api("GET", "/api/v1/analytics/catalog").then(function (res) {
    var status = $("status");
    if (res.status === 401) { status.textContent = "Sign in to V2Pulse first, then reload this page."; var a = el("a", { href: "/login" }, " Sign in"); status.appendChild(a); return; }
    if (res.status === 403) { status.textContent = "Analytics is not part of your role yet. Ask your V2Pulse administrator."; return; }
    if (!res.ok || !res.data) { status.textContent = "Analytics could not be loaded. Reload the page, or try again in a minute."; return; }
    var groups = res.data.data || [];
    if (res.data.user) $("who").textContent = res.data.user.name + " · " + (res.data.user.scope || "your stores");
    if (!groups.length) { status.textContent = "No Analytics dashboards are published for your role yet."; return; }
    status.remove();
    groups.forEach(function (g) { g.dashboards.forEach(function (d) { (DASH[d.dataset] = DASH[d.dataset] || []).push(d); }); });
    if (DASH.ai_suggestions) aiSection("ai_suggestions");
    if (DASH.analytics) heroSection("analytics");
    if (DASH.store_fill_rate) fillSection("store_fill_rate");
    if (DASH.karma_emp) peopleSection("karma_emp");
    directory(groups);
    if (location.hash) { var t = document.querySelector(location.hash); if (t) t.scrollIntoView(); }
  }).catch(function () { $("status").textContent = "Could not reach V2Pulse. Check the network and reload."; });
})();

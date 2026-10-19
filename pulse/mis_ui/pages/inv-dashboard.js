// Inventory Dashboard (DFS /sf-mis-inv-dashboard, SfInvDashboard.jsx): stock (the Vertical Inventory (Article) slice)
// next to VBRP FP sale, per plant x OPTION (generic article + colour), stored per as-of date by the engine
// (BRONZE.MIS_INV_DASH_OPT / _SZ / _WK). Seven tabs, each answering one question in plain words:
//   Overview       headline numbers, gauges, donuts, weekly sale, ABC        Stock health   cover buckets, by division
//   Mix            stock share vs sale share (division, price band, zone, plant type)
//   Size & colour  size curve, colour mix, colour x size grid, per store x size short / excess finder, top shades
//   Stores         status count, cover vs sale scatter, league table       Categories     treemap, over / under, table
//   Act now        option lists: out of stock with DC stock, dead on shelf, excess
// Clicking a division, zone, store, category, colour, size or shade anywhere filters the whole page on it (as DFS).
// API: /api/v1/mis/inv-dash/{params (GET ?as_of), data (POST DashReq), build (POST, mis.admin)}.
// Charts: MIS.charts (bars, legend) + local SVG (donut with legend, gauge, area, grouped / stacked bars, scatter, treemap).
(function () {
  "use strict";
  var M = window.MIS, el = M.el, NS = "http://www.w3.org/2000/svg";
  var API = "/api/v1/mis/inv-dash";

  var FILTERS = [["ZONE", "Zone"], ["STATE", "State"], ["ST_TYP", "Plant type"], ["ST_CD", "Store"], ["SEG", "Segment"], ["DIV", "Division"],
                 ["SUB_DIV", "Sub-division"], ["MJ", "Major category"], ["CLR_FAM", "Colour"], ["CLR", "Shade"], ["SZ", "Size"]];
  var FLABEL = {}; FILTERS.forEach(function (f) { FLABEL[f[0]] = f[1]; });
  var PAL = ["var(--s1)", "var(--s3)", "var(--s2)", "var(--s8)", "var(--s5)", "var(--s7)", "var(--s4)", "var(--s6)"];
  // The four plain-language health groups (status colours, always shown with a label).
  var GROUPS = [
    { k: "RUN", label: "Running out", color: "#eda100", desc: "Less than 15 days of stock left at the current sale rate - replenish", b: ["C15"] },
    { k: "OK", label: "Healthy", color: "#12a36f", desc: "15 to 60 days of stock - the right amount", b: ["C30", "C60"] },
    { k: "HIGH", label: "Too much", color: "#3f7fd0", desc: "Selling, but more than 60 days of stock - slow down buying / transfer", b: ["C90", "C180", "C999"] },
    { k: "NOT", label: "Not selling", color: "#d23b3a", desc: "No sale at that store in the last 30 days (slow) or 90 days (dead) - markdown / move", b: ["SLOW", "DEAD"] }
  ];
  var BUCKETS = [["C15", "< 15 days", "#eda100"], ["C30", "15–30 days", "#34c38f"], ["C60", "30–60 days", "#0f9f6e"], ["C90", "60–90 days", "#8fb8ec"],
                 ["C180", "90–180 days", "#4a86d4"], ["C999", "> 180 days", "#1f4f9a"], ["SLOW", "Slow · no sale 30 d", "#f08a89"], ["DEAD", "Dead · no sale 90 d", "#c0302f"]];
  var PB = { P0: "< 200", P200: "200–400", P400: "400–700", P700: "700–1,000", P1000: "1,000–1,500", P1500: "1,500+", PNA: "no MAP" };
  var PB_ORDER = ["P0", "P200", "P400", "P700", "P1000", "P1500", "PNA"];
  var WHERE = [["SV", "Store shelf"], ["TR_V", "In transit"], ["PIPE_V", "Pipeline (PTL / PRD)"], ["DC_V", "DC"], ["HUB_V", "Hub"]];
  var TABS = [["overview", "Overview"], ["health", "Stock health"], ["mix", "Mix"], ["size", "Size & colour"], ["stores", "Stores"], ["cats", "Categories"], ["act", "Act now"]];
  var FAM_COLORS = { BLACK: "#1f2328", WHITE: "#f4f4f2", GREY: "#9aa1ab", BLUE: "#2f64c8", RED: "#c62828", PINK: "#e76fa3", PURPLE: "#7e57c2", GREEN: "#2e8b3e",
                     YELLOW: "#eec13a", ORANGE: "#f07c1b", BROWN: "#8a5a3b", BEIGE: "#d8c19b", MULTI: "#a78bfa", OTHER: "#94a3b8", "(blank)": "#cbd5e1" };
  var FAM_WORDS = [["MULTI", ["ASSORT", "MULTI", "MIX", "PRINT", "CHECK", "STRIPE"]],
    ["BEIGE", ["OFF WHITE", "OFFWHITE", "CREAM", "BEIGE", "IVORY", "FAWN", "SAND", "STONE", "NUDE", "SKIN", "ECRU", "ALMND", "ALMOND"]],
    ["BLACK", ["BLACK", "BLK", "ONYX"]], ["WHITE", ["WHITE"]],
    ["GREY", ["GREY", "GRAY", "GRY", "CHARCOAL", "CHRCOL", "ASH", "SILVER", "SMOKE", "ANTHRA", "ANTRA", "SLATE"]],
    ["BLUE", ["BLUE", "NAVY", "DENIM", "INDIGO", "TEAL", "TURQ", "AQUA", "CYAN", "FIROZI", "SKY"]],
    ["RED", ["RED", "MAROON", "WINE", "RUST", "BURGUNDY", "CHERRY", "CORAL"]], ["PINK", ["PINK", "ROSE", "PEACH", "MAGENTA", "FUCHSIA", "ONION", "RANI"]],
    ["PURPLE", ["PURPLE", "VIOLET", "LAVENDER", "LILAC", "MAUVE", "PLUM"]],
    ["GREEN", ["GREEN", "GRN", "OLIVE", "MINT", "LIME", "PISTA", "PASTA", "MEHNDI", "SAGE", "BOTTLE"]],
    ["YELLOW", ["YELLOW", "MUSTARD", "LEMON", "GOLD"]], ["ORANGE", ["ORANGE", "SAFFRON", "TANGERINE"]],
    ["BROWN", ["BROWN", "BRW", "BRN", "KHAKI", "KHAKHI", "TAN", "COFFEE", "CHOCO", "CACAO", "CAMEL", "MOCHA", "COPPER"]]];
  function famColor(f) { return FAM_COLORS[f] || "#94a3b8"; }
  function famOfShade(c) {
    var u = String(c || "").toUpperCase();
    var hit = FAM_WORDS.filter(function (fw) { return fw[1].some(function (x) { return u.indexOf(x) >= 0; }); })[0];
    return hit ? hit[0] : "OTHER";
  }
  // Size order: numbers ascending, then letter sizes XXS..6XL, then the rest.
  var LETTER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "XXXL", "3XL", "4XL", "5XL", "6XL"];
  function sizeKey(sz) {
    var u = String(sz || "").toUpperCase().trim(), li = LETTER.indexOf(u);
    if (li >= 0) return 10000 + li;
    var n = parseFloat(u); return isNaN(n) ? 20000 : n;
  }
  function sizeCmp(a, b) { return sizeKey(a) - sizeKey(b) || String(a).localeCompare(String(b)); }

  // ── formatting (DFS short / rs / full / pct / days; Cr · L · K through MIS.compact) ──────────────────
  function num(v) { return v === null || v === undefined || !isFinite(Number(v)) ? null : Number(v); }
  function short(n) { return num(n) === null ? "—" : M.compact(Math.abs(n) < 1000 ? Math.round(n) : n); }
  function rs(n) { return num(n) === null ? "—" : "₹ " + short(n); }
  function full(n) { return M.fmt(Number(n || 0), "int"); }
  function pctv(a, b) { return b ? (a / b) * 100 : null; }
  function pct(v, dp) { return v === null || v === undefined || !isFinite(v) ? "—" : v.toFixed(dp === undefined ? 1 : dp) + "%"; }
  function days(v) { return v === null || v === undefined || !isFinite(v) ? "—" : Math.round(v) + " d"; }
  function sum(rows, f) { return rows.reduce(function (s, r) { return s + (typeof f === "function" ? f(r) : (r[f] || 0)); }, 0); }
  function assign(a) { for (var i = 1; i < arguments.length; i++) { var b = arguments[i] || {}; Object.keys(b).forEach(function (k) { a[k] = b[k]; }); } return a; }
  function derive(r) {
    var rate = (r.ST30_Q || 0) / 30;
    return { cover: rate > 0 ? r.SQ / rate : null, st: pctv(r.ST30_Q, (r.ST30_Q || 0) + (r.SQ || 0)), oos: pctv(r.OOS_N, r.SELL_N),
             deadPct: pctv((r.DEAD_V || 0) + (r.SLOW_V || 0), r.SV), exPct: pctv(r.EXV, r.SV), gm: pctv(r.S90_GM, r.S90_V) };
  }
  function healthOf(r, target) {
    if (r.deadPct !== null && r.deadPct !== undefined && r.deadPct >= 50) return GROUPS[3];
    if (r.cover === null || r.cover === undefined) return r.SV > 0 ? GROUPS[3] : null;
    if (r.cover < 15) return GROUPS[0];
    if (r.cover > target) return GROUPS[2];
    return GROUPS[1];
  }
  // Cell tones (DFS coverCls / badCls)
  function coverTone(c, target) { return c === null || c === undefined ? "ivd-mut" : c < 15 ? "ivd-warn" : c > target * 2 ? "ivd-bad" : c > target ? "ivd-info" : "ivd-ok"; }
  function badTone(v, warn, bad) { return v === null || v === undefined ? "ivd-mut" : v >= bad ? "ivd-bad" : v >= warn ? "ivd-warn" : ""; }

  // ── page styles (page-only bits; everything else is mis.css) ───────────────────────────────────────
  function css() {
    if (document.getElementById("ivd-css")) return;
    document.head.appendChild(el("style", { id: "ivd-css", text: [
      ".ivd-body{display:flex;flex-direction:column;gap:16px;transition:opacity .15s}.ivd-body.busy{opacity:.55;pointer-events:none}",
      ".ivd-tabs{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;padding:10px 18px}",
      ".ivd-tabs .seg button{padding:7px 13px;font-size:13px}.ivd-tabs .bang{margin-left:5px;background:var(--warn-soft);color:var(--warn);border-radius:999px;padding:0 6px;font-size:11px;font-weight:700}",
      ".ivd-leg{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:2px;flex:1;min-width:0;width:100%}",
      ".ivd-leg li{display:flex;align-items:center;gap:8px;font-size:12.3px;padding:2px 5px;border-radius:6px}.ivd-leg li.clk{cursor:pointer}.ivd-leg li.clk:hover{background:var(--surface-2)}",
      ".ivd-leg .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--fg-2)}.ivd-leg .v{margin-left:auto;font-weight:600;font-variant-numeric:tabular-nums}",
      ".ivd-leg .p{width:44px;text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}",
      ".ivd-sw{width:11px;height:11px;border-radius:3px;flex:none;display:inline-block;border:1px solid color-mix(in srgb,var(--fg) 12%,transparent)}",
      ".ivd-sw.round{border-radius:50%}",
      ".ivd-dn{display:flex;gap:14px;align-items:center}@media (max-width:640px){.ivd-dn{flex-direction:column}}",
      ".ivd-g{display:flex;flex-direction:column;align-items:center;gap:2px}.ivd-g svg{max-width:240px}",
      ".ivd-ins{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:8px 24px}",
      ".ivd-ins li{display:flex;gap:9px;font-size:13px;color:var(--fg-2);line-height:1.45}.ivd-ins i{width:9px;height:9px;border-radius:50%;flex:none;margin-top:5px}",
      ".ivd-badge{display:inline-flex;align-items:center;gap:5px;border-radius:999px;padding:1px 8px;font-size:11.5px;font-weight:600}",
      ".ivd-badge i{width:7px;height:7px;border-radius:50%;background:currentColor}",
      ".ivd-mut{color:var(--muted)!important}.ivd-warn{color:var(--warn)!important;font-weight:600}.ivd-bad{color:var(--bad)!important;font-weight:600}",
      ".ivd-info{color:var(--brand)!important}.ivd-ok{color:var(--ok)!important}",
      ".ivd-tw{overflow:auto;max-height:28rem;border:1px solid var(--line-2);border-radius:10px}",
      "table.ivd-t{width:100%;border-collapse:collapse;font-size:12.5px}.ivd-t th,.ivd-t td{padding:5px 9px;border-bottom:1px solid var(--line-2);white-space:nowrap;text-align:left}",
      ".ivd-t thead th{position:sticky;top:0;background:var(--head);color:var(--fg-2);font-weight:600;cursor:pointer;user-select:none;z-index:1}",
      ".ivd-t .n{text-align:right;font-variant-numeric:tabular-nums}.ivd-t tbody tr:hover td{background:var(--hover)}.ivd-t tr.clk{cursor:pointer}",
      ".ivd-groups{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin-top:10px}",
      ".ivd-groups div{border-radius:10px;padding:8px 10px}.ivd-groups b{display:block;font-size:15px;color:var(--fg);font-variant-numeric:tabular-nums}",
      ".ivd-act{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}",
      ".ivd-act button{font:inherit;text-align:left;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px 15px;cursor:pointer;box-shadow:var(--shadow);color:var(--fg)}",
      ".ivd-act button:hover{background:var(--surface-2)}.ivd-act button[aria-pressed=true]{box-shadow:0 0 0 2px var(--ac) inset}",
      ".ivd-act .k{font-weight:600;font-size:13px;color:var(--ac)}.ivd-act .v{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}",
      ".ivd-heat{border-collapse:separate;border-spacing:3px;font-size:11.5px}.ivd-heat th{font-weight:600;color:var(--fg-2);padding:0 4px;white-space:nowrap}",
      ".ivd-heat td{text-align:center;border-radius:4px;font-variant-numeric:tabular-nums;min-width:44px;height:24px}.ivd-heat td.lbl{text-align:left;min-width:0;padding-right:8px;white-space:nowrap}",
      ".ivd-heat td.e{background:var(--surface-3)}.ivd-heat button.lnk{border:0;background:none;padding:0;font:inherit;color:var(--fg);cursor:pointer;display:inline-flex;align-items:center;gap:5px}",
      ".ivd-heat button.lnk:hover{text-decoration:underline}",
      ".ivd-stat{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-bottom:12px}",
      ".ivd-stat div{border:1px solid var(--line);border-left:4px solid var(--ac);border-radius:10px;padding:8px 12px}",
      ".ivd-stat .k{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}.ivd-stat .v{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums}",
      ".ivd-stat .s{font-size:11.5px;color:var(--muted)}",
      ".ivd-body .chart text{pointer-events:none}",
      ".ivd-fbars button{display:flex;align-items:center;gap:8px;width:100%;border:0;background:none;padding:2px 0;cursor:pointer;font:inherit;color:var(--fg-2)}",
      ".ivd-fbars .nm{width:86px;font-size:11.5px;display:inline-flex;align-items:center;gap:5px;overflow:hidden;white-space:nowrap}",
      ".ivd-fbars .tr{flex:1;position:relative;height:14px}.ivd-fbars .a{position:absolute;left:0;top:0;height:8px;border-radius:2px}",
      ".ivd-fbars .b{position:absolute;left:0;bottom:0;height:4px;border-radius:2px;background:var(--muted)}.ivd-fbars .d{width:48px;text-align:right;font-size:11.5px;color:var(--muted);font-variant-numeric:tabular-nums}",
      ".ivd-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.ivd-chips button{font:inherit;font-size:12px;border:1px solid currentColor;background:none;color:var(--warn);border-radius:999px;padding:2px 9px;cursor:pointer}",
      ".ivd-find{width:170px;padding:4px 8px;font-size:12.5px}"
    ].join("\n") }));
  }

  // ── local SVG kit (same look as charts.js: viewBox 640 wide, .chart text, .gl grid lines, .tip tooltip) ──
  var uid = 0;
  function s(tag, attrs, kids) {
    var e = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k]; if (v === null || v === undefined) return;
      if (k === "fill" || k === "stroke" || k === "stop-color") e.style.setProperty(k, v); else e.setAttribute(k, v);
    });
    (kids || []).forEach(function (c) { if (c !== null && c !== undefined) e.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c); });
    return e;
  }
  var tipEl = null;
  function showTip(e, html) {
    if (!tipEl) { tipEl = el("div", { "class": "tip", role: "tooltip" }); document.body.appendChild(tipEl); }
    tipEl.innerHTML = html; tipEl.style.display = "block";
    var x = e.clientX + 14, y = e.clientY + 14, w = tipEl.offsetWidth, h = tipEl.offsetHeight;
    if (x + w > window.innerWidth - 8) x = e.clientX - w - 14; if (y + h > window.innerHeight - 8) y = e.clientY - h - 14;
    tipEl.style.left = x + "px"; tipEl.style.top = y + "px";
  }
  function hideTip() { if (tipEl) tipEl.style.display = "none"; }
  function tipHtml(head, rows) {
    return '<div class="h">' + M.esc(head) + "</div>" + (rows || []).map(function (r) {
      return '<div class="r"><span>' + M.esc(r[0]) + "</span><b>" + M.esc(r[1]) + "</b></div>"; }).join("");
  }
  function hover(node, html, onClick) {
    node.addEventListener("mousemove", function (e) { showTip(e, typeof html === "function" ? html() : html); });
    node.addEventListener("mouseleave", hideTip);
    if (onClick) { node.style.cursor = "pointer"; node.addEventListener("click", function () { hideTip(); onClick(); }); }
  }
  function nice(max) { if (!(max > 0)) return 1; var p = Math.pow(10, Math.floor(Math.log10(max))), f = max / p; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p; }
  function trunc(t, n) { t = String(t == null ? "" : t); return t.length > n ? t.slice(0, Math.max(1, n - 1)) + "…" : t; }
  function svgRoot(W, H, label) { return s("svg", { "class": "chart", viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": label || "Chart" }); }
  function empty(text) { return el("div", { "class": "muted small", style: { padding: "28px 8px", textAlign: "center" }, text: text || "Nothing to show under these filters." }); }

  // Donut / pie. rows [{name, value, color}]; o: {size, inner (0 = pie), center, sub, fmt, onClick, pctLabels}
  function donutSvg(rows, o) {
    o = o || {}; var size = o.size || 180, r = size / 2 - 3, inner = o.inner === 0 ? 0 : r * (o.inner || 0.62), cx = size / 2, cy = size / 2;
    var fmt = o.fmt || rs, shown = rows.filter(function (x) { return x.value > 0; }), total = sum(shown, "value");
    var svg = s("svg", { "class": "chart", viewBox: "0 0 " + size + " " + size, width: size, height: size, role: "img", "aria-label": o.label || "Donut chart", style: "flex:none;width:" + size + "px" });
    if (!total) svg.appendChild(s("circle", { cx: cx, cy: cy, r: inner ? (r + inner) / 2 : r / 2, fill: "none", stroke: "var(--surface-3)", "stroke-width": inner ? r - inner : r }));
    var a0 = -Math.PI / 2;
    shown.forEach(function (x) {
      var share = x.value / total, a1 = a0 + 2 * Math.PI * share; if (share > 0.9999) a1 = a0 + 2 * Math.PI - 0.0001;
      var large = a1 - a0 > Math.PI ? 1 : 0, p0 = [cx + r * Math.cos(a0), cy + r * Math.sin(a0)], p1 = [cx + r * Math.cos(a1), cy + r * Math.sin(a1)];
      var d = "M" + p0[0] + " " + p0[1] + "A" + r + " " + r + " 0 " + large + " 1 " + p1[0] + " " + p1[1];
      d += inner ? "L" + (cx + inner * Math.cos(a1)) + " " + (cy + inner * Math.sin(a1)) + "A" + inner + " " + inner + " 0 " + large + " 0 " + (cx + inner * Math.cos(a0)) + " " + (cy + inner * Math.sin(a0)) + "Z"
                 : "L" + cx + " " + cy + "Z";
      var p = s("path", { d: d, fill: x.color, stroke: "var(--surface)", "stroke-width": 2 });
      hover(p, function () { return tipHtml(x.name, [["Value", fmt(x.value)], ["Share", pct(share * 100)]]); }, o.onClick ? function () { o.onClick(x); } : null);
      svg.appendChild(p);
      if (o.pctLabels && share > 0.08) {
        var am = (a0 + a1) / 2, rr = inner ? (r + inner) / 2 : r * 0.62;
        svg.appendChild(s("text", { x: cx + rr * Math.cos(am), y: cy + rr * Math.sin(am) + 4, "text-anchor": "middle", style: "fill:#fff;font-weight:600;pointer-events:none" }, [Math.round(share * 100) + "%"]));
      }
      a0 = a1;
    });
    if (o.center !== undefined && inner) {
      svg.appendChild(s("text", { x: cx, y: cy + (o.sub ? 1 : 5), "text-anchor": "middle", "class": "val", style: "font-size:" + (size < 150 ? 12 : 15) + "px;pointer-events:none" }, [String(o.center)]));
      if (o.sub) svg.appendChild(s("text", { x: cx, y: cy + 16, "text-anchor": "middle", style: "font-size:10.5px;pointer-events:none" }, [o.sub]));
    }
    return svg;
  }
  // Donut with the total in the middle and a legend (value + share) beside it (DFS Donut).
  function legendList(rows, o) {
    o = o || {}; var fmt = o.fmt || rs, tot = sum(rows, function (d) { return Math.max(0, d.value || 0); });
    return el("ul", { "class": "ivd-leg" }, rows.map(function (d) {
      var li = el("li", { "class": o.onClick ? "clk" : null, title: o.onClick ? "Click to filter on " + d.name : null }, [
        el("span", { "class": "ivd-sw", style: { background: d.color } }), el("span", { "class": "nm", text: d.name }),
        el("span", { "class": "v", text: fmt(d.value) }), el("span", { "class": "p", text: pct(pctv(d.value, tot), 0) })]);
      if (o.onClick) li.addEventListener("click", function () { o.onClick(d); });
      return li;
    }));
  }
  function donutBox(rows, o) {
    o = o || {};
    return el("div", { "class": "ivd-dn" }, [donutSvg(rows, o), legendList(rows, o)]);
  }

  // Semicircle gauge (DFS Gauge / RadialBar): value on 0..max, the arc coloured by the zone it falls in.
  function gauge(value, max, display, zones) {
    var v = num(value), z = v === null ? null : (zones.filter(function (x) { return v <= x[0]; })[0] || zones[zones.length - 1]);
    var W = 220, H = 128, cx = 110, cy = 112, r = 88, svg = svgRoot(W, H, "Gauge");
    svg.appendChild(s("path", { d: "M" + (cx - r) + " " + cy + "A" + r + " " + r + " 0 0 1 " + (cx + r) + " " + cy, fill: "none", stroke: "var(--surface-3)", "stroke-width": 16, "stroke-linecap": "round" }));
    if (v !== null && v > 0) {
      var f = Math.min(v, max) / max, a = Math.PI - Math.PI * f;
      svg.appendChild(s("path", { d: "M" + (cx - r) + " " + cy + "A" + r + " " + r + " 0 0 1 " + (cx + r * Math.cos(a)) + " " + (cy - r * Math.sin(a)),
                                  fill: "none", stroke: z[1], "stroke-width": 16, "stroke-linecap": "round" }));
    }
    svg.appendChild(s("text", { x: cx, y: cy - 22, "text-anchor": "middle", "class": "val", style: "font-size:24px;fill:var(--fg)" }, [display]));
    if (z) svg.appendChild(s("text", { x: cx, y: cy - 2, "text-anchor": "middle", style: "font-size:12px;font-weight:600;fill:" + z[1] }, [z[2]]));
    return svg;
  }

  // Area chart (DFS weekly sale): rows [{label, value}], dashed average line, dots, nearest-point tooltip.
  function areaChart(rows, o) {
    o = o || {}; var W = 640, H = o.height || 240, pad = { l: 56, r: 14, t: 14, b: 30 }, n = rows.length, color = o.color || PAL[0];
    var svg = svgRoot(W, H, o.label || "Area chart"), iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    var max = nice(Math.max.apply(null, rows.map(function (r) { return r.value || 0; }).concat([0])));
    function X(i) { return pad.l + iw * (n > 1 ? i / (n - 1) : 0.5); } function Y(v) { return pad.t + ih - ih * Math.max(0, v) / max; }
    var id = "ivdg" + (++uid);
    svg.appendChild(s("defs", {}, [s("linearGradient", { id: id, x1: 0, y1: 0, x2: 0, y2: 1 }, [
      s("stop", { offset: "0%", "stop-color": color, "stop-opacity": 0.35 }), s("stop", { offset: "100%", "stop-color": color, "stop-opacity": 0.02 })])]));
    for (var g = 0; g <= 4; g++) { var gv = max * g / 4; svg.appendChild(s("line", { "class": "gl", x1: pad.l, x2: W - pad.r, y1: Y(gv), y2: Y(gv) })); svg.appendChild(s("text", { x: pad.l - 6, y: Y(gv) + 4, "text-anchor": "end" }, [short(gv)])); }
    var step = Math.ceil(n / 13) || 1;
    rows.forEach(function (r, i) { if (i % step === 0 || i === n - 1) svg.appendChild(s("text", { x: X(i), y: H - 10, "text-anchor": "middle" }, [r.label])); });
    if (n) {
      var line = rows.map(function (r, i) { return (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(r.value || 0).toFixed(1); }).join("");
      svg.appendChild(s("path", { d: line + "L" + X(n - 1).toFixed(1) + " " + (pad.t + ih) + "L" + X(0).toFixed(1) + " " + (pad.t + ih) + "Z", style: "fill:url(#" + id + ")" }));
      svg.appendChild(s("path", { d: line, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round" }));
      if (o.avg != null) svg.appendChild(s("line", { x1: pad.l, x2: W - pad.r, y1: Y(o.avg), y2: Y(o.avg), stroke: "var(--muted)", "stroke-dasharray": "4 3" }));
      var dots = rows.map(function (r, i) { var c = s("circle", { cx: X(i), cy: Y(r.value || 0), r: 3, fill: color, stroke: "var(--surface)", "stroke-width": 1.5 }); svg.appendChild(c); return c; });
      var hit = s("rect", { x: pad.l - 8, y: pad.t, width: iw + 16, height: ih, fill: "transparent" }), last = -1;
      hit.addEventListener("mousemove", function (e) {
        var b = svg.getBoundingClientRect(), i = Math.round(((e.clientX - b.left) / b.width * W - pad.l) / (iw || 1) * (n - 1)); i = Math.max(0, Math.min(n - 1, i));
        if (last >= 0) dots[last].setAttribute("r", 3); dots[i].setAttribute("r", 5); last = i;
        showTip(e, o.tip ? o.tip(rows[i], i) : tipHtml(rows[i].label, [["Value", short(rows[i].value)]]));
      });
      hit.addEventListener("mouseleave", function () { hideTip(); if (last >= 0) dots[last].setAttribute("r", 3); last = -1; });
      svg.appendChild(hit);
    }
    return svg;
  }

  // Vertical bars, one or more series side by side. o: {series:[{key,name,color}], label(r), fmt (axis), top(r) (label above a
  // single bar), colorOf(r), tip(r), onClick(r), height, maxBar}
  function vbars(rows, o) {
    var W = 640, H = o.height || 260, ser = o.series, ns = ser.length, n = rows.length || 1, two = rows.some(function (r) { return String(o.label(r)).indexOf(" · ") >= 0; });
    var pad = { l: 52, r: 10, t: o.top ? 20 : 12, b: two ? 44 : 30 }, svg = svgRoot(W, H, o.title || "Bar chart"), iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    var max = nice(Math.max.apply(null, [0].concat.apply([0], rows.map(function (r) { return ser.map(function (k) { return Math.max(0, r[k.key] || 0); }); }))));
    var fmt = o.fmt || short;
    for (var g = 0; g <= 4; g++) { var gy = pad.t + ih - ih * g / 4; svg.appendChild(s("line", { "class": "gl", x1: pad.l, x2: W - pad.r, y1: gy, y2: gy })); svg.appendChild(s("text", { x: pad.l - 6, y: gy + 4, "text-anchor": "end" }, [fmt(max * g / 4)])); }
    var gw = iw / n, bw = Math.min(o.maxBar || 42, gw * 0.76 / ns);
    rows.forEach(function (r, i) {
      var x0 = pad.l + gw * i + (gw - bw * ns) / 2, cxm = pad.l + gw * i + gw / 2;
      ser.forEach(function (k, j) {
        var v = Math.max(0, r[k.key] || 0), h = ih * v / max;
        svg.appendChild(s("rect", { x: x0 + j * bw + (ns > 1 ? 1 : 0), y: pad.t + ih - h, width: Math.max(1, bw - (ns > 1 ? 2 : 0)), height: Math.max(v > 0 ? 1 : 0, h), rx: 3, fill: o.colorOf ? o.colorOf(r, k) : k.color }));
      });
      if (o.top) { var hv = ih * Math.max(0, r[ser[0].key] || 0) / max; svg.appendChild(s("text", { x: cxm, y: pad.t + ih - hv - 5, "text-anchor": "middle", "class": "val" }, [o.top(r)])); }
      var parts = String(o.label(r)).split(" · "), lt = s("text", { x: cxm, y: H - pad.b + 14, "text-anchor": "middle" });
      parts.forEach(function (p, j) { lt.appendChild(s("tspan", { x: cxm, dy: j ? 12 : 0 }, [trunc(p, Math.max(4, Math.floor(gw / 6)))])); });
      svg.appendChild(lt);
      var hit = s("rect", { x: pad.l + gw * i, y: pad.t, width: gw, height: ih, fill: "transparent" });
      hit.addEventListener("mouseenter", function () { hit.style.setProperty("fill", "color-mix(in srgb, var(--fg) 5%, transparent)"); });
      hit.addEventListener("mouseleave", function () { hit.style.setProperty("fill", "transparent"); });
      hover(hit, function () { return o.tip ? o.tip(r) : tipHtml(o.label(r), ser.map(function (k) { return [k.name, fmt(r[k.key])]; })); }, o.onClick ? function () { o.onClick(r); } : null);
      svg.appendChild(hit);
    });
    return svg;
  }

  // Horizontal bars: one series, several side by side, or stacked. o: {series, label(r), fmt (axis), valFmt (value at the bar end),
  // stacked, max, ref:{v, text}, colorOf(r,k), tip(r), onClick(r), labelW, axis:false}
  function hbars(rows, o) {
    var ser = o.series, ns = ser.length, n = rows.length, grouped = ns > 1 && !o.stacked, rowH = o.rowH || (grouped ? 30 : 24);
    var W = 640, pad = { l: o.labelW || 100, r: o.valFmt ? 58 : 16, t: o.ref ? 16 : 6, b: o.axis === false ? 6 : 22 }, H = n * rowH + pad.t + pad.b;
    var svg = svgRoot(W, H, o.title || "Bar chart"), iw = W - pad.l - pad.r, fmt = o.fmt || short;
    var max = o.max || nice(Math.max.apply(null, [0].concat(rows.map(function (r) {
      return o.stacked ? sum(ser, function (k) { return Math.max(0, r[k.key] || 0); }) : Math.max.apply(null, ser.map(function (k) { return Math.max(0, r[k.key] || 0); })); }))));
    if (o.axis !== false) for (var g = 0; g <= 4; g++) { var gx = pad.l + iw * g / 4; svg.appendChild(s("line", { "class": "gl", x1: gx, x2: gx, y1: pad.t, y2: H - pad.b })); svg.appendChild(s("text", { x: gx, y: H - 6, "text-anchor": "middle" }, [fmt(max * g / 4)])); }
    var chars = Math.max(6, Math.floor((pad.l - 10) / 6.4));
    rows.forEach(function (r, i) {
      var y0 = pad.t + rowH * i, hit = s("rect", { x: 0, y: y0, width: W, height: rowH, fill: "transparent" });
      svg.appendChild(hit);
      svg.appendChild(s("text", { x: pad.l - 6, y: y0 + rowH / 2 + 4, "text-anchor": "end" }, [trunc(o.label(r), chars)]));
      var bh = grouped ? Math.min(o.barH || 11, rowH * 0.8 / ns) : Math.min(o.barH || 16, rowH * 0.7), x = pad.l, endX = pad.l;
      ser.forEach(function (k, j) {
        var v = Math.max(0, r[k.key] || 0), w = iw * Math.min(v, max) / max, y = grouped ? y0 + (rowH - bh * ns) / 2 + j * bh : y0 + (rowH - bh) / 2;
        var rect = s("rect", { x: o.stacked ? x : pad.l, y: y + (grouped ? 0.5 : 0), width: Math.max(v > 0 ? 1 : 0, w), height: grouped ? bh - 1 : bh, rx: o.stacked ? 0 : 3,
                               fill: o.colorOf ? o.colorOf(r, k) : k.color, stroke: o.stacked ? "var(--surface)" : null, "stroke-width": o.stacked ? 1 : null });
        rect.style.pointerEvents = "none"; svg.appendChild(rect);
        if (o.stacked) x += w; endX = Math.max(endX, (o.stacked ? x : pad.l + w));
      });
      if (o.valFmt && !o.stacked) svg.appendChild(s("text", { "class": "val", x: endX + 5, y: y0 + rowH / 2 + 4 }, [o.valFmt(r[ser[0].key], r)]));
      hit.addEventListener("mouseenter", function () { hit.style.setProperty("fill", "color-mix(in srgb, var(--fg) 5%, transparent)"); });
      hit.addEventListener("mouseleave", function () { hit.style.setProperty("fill", "transparent"); });
      hover(hit, function () { return o.tip ? o.tip(r) : tipHtml(o.label(r), ser.map(function (k) { return [k.name, fmt(r[k.key])]; })); }, o.onClick ? function () { o.onClick(r); } : null);
    });
    if (o.ref && o.ref.v <= max) {
      var rx = pad.l + iw * o.ref.v / max;
      svg.appendChild(s("line", { x1: rx, x2: rx, y1: pad.t - 2, y2: H - pad.b, stroke: "var(--muted)", "stroke-dasharray": "4 3", style: "pointer-events:none" }));
      if (o.ref.text) svg.appendChild(s("text", { x: rx, y: pad.t - 5, "text-anchor": "middle", style: "font-size:10px" }, [o.ref.text]));
    }
    return svg;
  }

  // Scatter / bubbles (DFS store cover vs sale per day). dots [{x, y, z, color, r}]
  function scatter(dots, o) {
    var W = 640, H = o.height || 290, pad = { l: 58, r: 16, t: 12, b: 34 }, svg = svgRoot(W, H, "Scatter chart"), iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    var xmax = nice(Math.max.apply(null, dots.map(function (d) { return d.x; }).concat([o.ref || 0, 1]))), ymax = nice(Math.max.apply(null, dots.map(function (d) { return d.y; }).concat([1])));
    var zs = dots.map(function (d) { return d.z; }), zmin = Math.min.apply(null, zs.concat([0])), zmax = Math.max.apply(null, zs.concat([1]));
    function X(v) { return pad.l + iw * v / xmax; } function Y(v) { return pad.t + ih - ih * v / ymax; }
    function R(z) { var a = 30 + 230 * (zmax > zmin ? (z - zmin) / (zmax - zmin) : 0.5); return Math.sqrt(a / Math.PI); }
    for (var g = 0; g <= 4; g++) {
      svg.appendChild(s("line", { "class": "gl", x1: pad.l, x2: W - pad.r, y1: Y(ymax * g / 4), y2: Y(ymax * g / 4) })); svg.appendChild(s("text", { x: pad.l - 6, y: Y(ymax * g / 4) + 4, "text-anchor": "end" }, [short(ymax * g / 4)]));
      svg.appendChild(s("line", { "class": "gl", x1: X(xmax * g / 4), x2: X(xmax * g / 4), y1: pad.t, y2: pad.t + ih })); svg.appendChild(s("text", { x: X(xmax * g / 4), y: H - 16, "text-anchor": "middle" }, [Math.round(xmax * g / 4) + " d"]));
    }
    svg.appendChild(s("text", { x: pad.l + iw / 2, y: H - 2, "text-anchor": "middle", style: "font-size:10.5px" }, [o.xTitle || ""]));
    if (o.ref) svg.appendChild(s("line", { x1: X(o.ref), x2: X(o.ref), y1: pad.t, y2: pad.t + ih, stroke: "var(--muted)", "stroke-dasharray": "4 3" }));
    dots.slice().sort(function (a, b) { return b.z - a.z; }).forEach(function (d) {
      var c = s("circle", { cx: X(d.x), cy: Y(d.y), r: R(d.z), fill: d.color, "fill-opacity": 0.75, stroke: "var(--surface)", "stroke-width": 1 });
      hover(c, function () { return o.tip(d); }, o.onClick ? function () { o.onClick(d); } : null);
      svg.appendChild(c);
    });
    return svg;
  }

  // Squarified treemap (DFS Treemap): items [{name, value, color}] sorted by value desc.
  function treemap(items, o) {
    var W = 1000, H = o.height || 440, svg = svgRoot(W, H, "Treemap"), total = sum(items, "value");
    if (!total) return empty();
    var scale = W * H / total, nodes = items.map(function (it) { return { it: it, a: it.value * scale }; }), out = [], r = { x: 0, y: 0, w: W, h: H }, row = [];
    function worst(rw, len) { var t = sum(rw, "a"), mx = Math.max.apply(null, rw.map(function (x) { return x.a; })), mn = Math.min.apply(null, rw.map(function (x) { return x.a; })); return Math.max(len * len * mx / (t * t), t * t / (len * len * mn)); }
    function place(rw) {
      var t = sum(rw, "a");
      if (r.w >= r.h) { var cw = t / r.h, yy = r.y; rw.forEach(function (nd) { var hh = nd.a / cw; out.push({ it: nd.it, x: r.x, y: yy, w: cw, h: hh }); yy += hh; }); r = { x: r.x + cw, y: r.y, w: r.w - cw, h: r.h }; }
      else { var ch = t / r.w, xx = r.x; rw.forEach(function (nd) { var ww = nd.a / ch; out.push({ it: nd.it, x: xx, y: r.y, w: ww, h: ch }); xx += ww; }); r = { x: r.x, y: r.y + ch, w: r.w, h: r.h - ch }; }
    }
    var i = 0;
    while (i < nodes.length) {
      var len = Math.min(r.w, r.h);
      if (!row.length || worst(row.concat([nodes[i]]), len) <= worst(row, len)) { row.push(nodes[i]); i++; } else { place(row); row = []; }
    }
    if (row.length) place(row);
    out.forEach(function (b) {
      if (b.w <= 0 || b.h <= 0) return;
      var g = s("g", {});
      g.appendChild(s("rect", { x: b.x, y: b.y, width: b.w, height: b.h, rx: 3, fill: b.it.color, stroke: "var(--surface)", "stroke-width": 2 }));
      if (b.w > 60 && b.h > 30) {
        g.appendChild(s("text", { x: b.x + 6, y: b.y + 16, style: "fill:#fff;font-size:12px;font-weight:600;pointer-events:none" }, [trunc(b.it.name, Math.floor(b.w / 7.4))]));
        g.appendChild(s("text", { x: b.x + 6, y: b.y + 30, style: "fill:rgba(255,255,255,.85);font-size:11px;pointer-events:none" }, [rs(b.it.value)]));
      }
      hover(g, function () { return o.tip(b.it); }, o.onClick ? function () { o.onClick(b.it); } : null);
      svg.appendChild(g);
    });
    return svg;
  }

  // Compact sortable table (DFS SortTable) - for the small tables and the "show the numbers" views of the charts.
  // cols [{k, h, v(r) sort key, f(r) text|node, num, tone(r), t}]
  function sortTable(rows, cols, o) {
    o = o || {}; var sort = o.initial || { k: cols[0].k, dir: -1 }, limit = o.limit || 200;
    var wrap = el("div", { "class": "ivd-tw", style: o.maxH ? { maxHeight: o.maxH } : null });
    function draw() {
      var c = cols.filter(function (x) { return x.k === sort.k; })[0] || cols[0], val = c.v || function (r) { return r[c.k]; };
      var sorted = rows.slice().sort(function (a, b) {
        var x = val(a), y = val(b);
        if (x === null || x === undefined) return 1; if (y === null || y === undefined) return -1;
        return (x > y ? 1 : x < y ? -1 : 0) * sort.dir;
      }).slice(0, limit);
      var head = el("tr", {}, cols.map(function (cc) {
        var th = el("th", { "class": cc.num ? "n" : null, title: cc.t || null, scope: "col", "aria-sort": sort.k === cc.k ? (sort.dir < 0 ? "descending" : "ascending") : null },
          [cc.h + (sort.k === cc.k ? (sort.dir < 0 ? " ↓" : " ↑") : "")]);
        th.addEventListener("click", function () { sort = { k: cc.k, dir: sort.k === cc.k ? -sort.dir : (cc.num ? -1 : 1) }; draw(); });
        return th;
      }));
      var body = el("tbody", {}, sorted.map(function (r) {
        var tr = el("tr", { "class": o.onRow ? "clk" : null, title: o.rowTitle || null }, cols.map(function (cc) {
          var f = cc.f ? cc.f(r) : (r[cc.k] === null || r[cc.k] === undefined ? "—" : String(r[cc.k]));
          return el("td", { "class": [(cc.num ? "n" : ""), (cc.tone ? cc.tone(r) : "")].join(" ").trim() || null }, [f]);
        }));
        if (o.onRow) tr.addEventListener("click", function () { o.onRow(r); });
        return tr;
      }));
      if (!sorted.length) body.appendChild(el("tr", {}, el("td", { colspan: cols.length, "class": "ivd-mut", style: { textAlign: "center", padding: "20px" }, text: "Nothing here." })));
      M.clear(wrap).appendChild(el("table", { "class": "ivd-t" }, [el("thead", {}, head), body]));
      if (rows.length > limit) wrap.appendChild(el("div", { "class": "tiny muted", style: { padding: "4px 8px" }, text: "Showing " + limit + " of " + rows.length + " - sort or filter to narrow." }));
    }
    draw(); return wrap;
  }

  // A swatch + label, and a health badge (label + colour, never colour alone).
  function swatch(color, round) { return el("span", { "class": "ivd-sw" + (round ? " round" : ""), style: { background: color } }); }
  function badge(g) {
    if (!g) return el("span", { "class": "ivd-mut", text: "—" });
    return el("span", { "class": "ivd-badge", title: g.desc, style: { color: g.color, background: "color-mix(in srgb, " + g.color + " 14%, transparent)" } }, [el("i"), g.label]);
  }

  // MIS.Grid over rows already in the browser: header value filters, conditions, sort and paging done here.
  // cols [{k, h, key (text column), fmt, show(r) text, tone(r), node(r)}]
  function localGrid(container, o) {
    var defs = {}; o.cols.forEach(function (c) { defs[c.k] = c; });
    function blank(v) { return v === null || v === undefined || v === ""; }
    function passCond(v, c) {
      var n = Number(v), a = Number(c.v1), b = Number(c.v2), sv = String(v == null ? "" : v).toLowerCase(), t = String(c.v1 == null ? "" : c.v1).toLowerCase();
      var numeric = !blank(v) && isFinite(n) && c.v1 !== "" && isFinite(a);
      switch (c.op) {
        case "blank": return blank(v); case "nonblank": return !blank(v);
        case "eq": return numeric ? n === a : sv === t; case "ne": return numeric ? n !== a : sv !== t;
        case "gt": return numeric && n > a; case "gte": return numeric && n >= a; case "lt": return numeric && n < a; case "lte": return numeric && n <= a;
        case "between": return !blank(v) && isFinite(n) && n >= Math.min(a, b) && n <= Math.max(a, b);
        case "in": return (c.vals || []).map(function (x) { return String(x).toLowerCase(); }).indexOf(sv) >= 0;
        case "not_in": return (c.vals || []).map(function (x) { return String(x).toLowerCase(); }).indexOf(sv) < 0;
        case "contains": return sv.indexOf(t) >= 0; case "not_contains": return sv.indexOf(t) < 0;
        case "starts": return sv.indexOf(t) === 0; case "ends": return t === "" || sv.slice(-t.length) === t;
        default: return true;
      }
    }
    function filtered(gs) {
      var rows = o.rows();
      Object.keys(gs.filters || {}).forEach(function (c) {
        var vals = (gs.filters[c] || []).map(String); if (!vals.length) return;
        rows = rows.filter(function (r) { var v = r[c]; return vals.indexOf(blank(v) ? "(blank)" : String(v)) >= 0; });
      });
      (gs.conds || []).forEach(function (c) { rows = rows.filter(function (r) { return passCond(r[c.col], c); }); });
      return rows;
    }
    var grid = M.Grid(container, {
      id: o.id, title: o.title, tools: o.tools, limit: o.limit || 100, pageSizes: [50, 100, 200, 500], sort_col: o.sort_col, sort_dir: o.sort_dir || "desc",
      isKey: function (c) { return !!(defs[c] && defs[c].key); }, format: function (c) { return defs[c] ? defs[c].fmt || (defs[c].key ? "text" : "dec") : undefined; },
      label: function (c) { return defs[c] ? defs[c].h : M.label(c); },
      fetch: function (gs) {
        var rows = filtered(gs);
        if (gs.sort_col) {
          var k = gs.sort_col, dir = gs.sort_dir === "desc" ? -1 : 1;
          rows = rows.slice().sort(function (a, b) {
            var x = a[k], y = b[k];
            if (blank(x)) return 1; if (blank(y)) return -1;
            return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * dir;
          });
        }
        var from = (gs.page - 1) * gs.limit;
        return Promise.resolve({ columns: o.cols.map(function (c) { return c.k; }), rows: rows.slice(from, from + gs.limit), total: rows.length });
      },
      values: function (c) {
        var cnt = {}; o.rows().forEach(function (r) { var v = blank(r[c]) ? "(blank)" : String(r[c]); cnt[v] = (cnt[v] || 0) + 1; });
        return Promise.resolve(Object.keys(cnt).sort().map(function (v) { return { value: v, label: v, count: cnt[v] }; }));
      },
      cell: function (td, c, v, r) {
        var d = defs[c]; if (!d) return;
        if (d.node) { M.clear(td).appendChild(d.node(r)); }
        else if (d.show) { td.textContent = d.show(r); }
        if (d.title) td.title = d.title(r);
        if (d.tone) { var t = d.tone(r); if (t) td.className += " " + t; }
      },
      rowClick: o.rowClick
    });
    grid.reload(true);
    return grid;
  }

  M.page("inv-dashboard", {
    render: function (ctx) {
      css();
      var me = ctx.me || { can: {} };
      var st = { meta: null, metaP: null, data: null, sel: {}, search: "", target: 60, merch: true, grain: "option",
                 tab: M.store.get("invdash.tab", "overview"), actTab: "OOS", finderView: "short", storeQ: "", mjQ: "",
                 busy: false, building: false, err: null, ctrl: null, seq: 0 };
      if (!TABS.some(function (t) { return t[0] === st.tab; })) st.tab = "overview";

      // ── masthead ─────────────────────────────────────────────────────────────────────────────────
      var refreshBtn = el("button", { type: "button", "class": "btn", title: "Read the numbers again" }, [M.icon("refresh", 15), "Refresh"]);
      var buildBtn = me.can.admin ? el("button", { type: "button", "class": "btn", title: "Re-read stock and sale for this date (~1 min)" }, [M.icon("gear", 15), "Rebuild…"]) : null;
      M.setHeader({ title: "Inventory Dashboard",
        sub: "How much stock we hold, where it is, how long it will last and what to do about it - Vertical Inventory (Article) stock against the last 30 / 90 days of sale, per store and option (article + colour). Stock at cost (MAP), sale at billed value.",
        actions: [refreshBtn, buildBtn], crumbs: [{ label: "Inventory Dashboard", href: "/mis/inv-dashboard" }],
        tiles: ["Total stock", "In stores", "In DC + hub", "Sale · last 30 days", "Stock turn", "Negative stock"].map(function (k) { return { k: k, skel: true }; }) });

      // ── filter card: settings bar, the 11 filters, "showing only" chips, the tab strip ───────────
      var asOf = el("input", { type: "date", "aria-label": "As of date" });
      var search = el("input", { type: "search", placeholder: "Store, category or article…", style: { minWidth: "220px" }, "aria-label": "Search" });
      var target = el("select", { "aria-label": "Target days of stock" }, [30, 45, 60, 90, 120].map(function (v) { return el("option", { value: v, selected: v === 60, text: v + " days" }); }));
      var grainSeg = el("div", { "class": "seg", role: "group", "aria-label": "Judge per option or per size",
        title: "Judge days of stock, out of stock and not-selling per option (article + colour, all sizes together) or per size. Filtering on a size always uses sizes." },
        [el("button", { type: "button", text: "Per option" }), el("button", { type: "button", text: "Per size" })]);
      var merch = el("input", { type: "checkbox", checked: true });
      var merchLbl = el("span", { "class": "row", style: { gap: "6px", fontSize: "13px", textTransform: "none", letterSpacing: 0, fontWeight: 400, color: "var(--fg)", cursor: "pointer", minHeight: "34px" },
        title: "Leave out SEG NT - hard tags, packing, carry bags, fixed assets (AC units) - which is never sold and would swamp every figure" }, [merch, "Merchandise only"]);
      var ms = {};
      FILTERS.forEach(function (f) {
        ms[f[0]] = M.multiSelect(f[1], function () {
          return st.metaP.then(function (m) {
            var vals = ((m.options || {})[f[0]] || []).slice();
            if (f[0] === "SZ") vals.sort(sizeCmp);
            if (f[0] === "ST_CD") return vals.map(function (v) { var n = (m.storeNames || {})[v]; return { value: v, label: n ? v + " · " + n : v }; });
            return vals;
          });
        }, [], function (vals) { st.sel[f[0]] = vals; afterFilter(); });
      });
      var chips = el("div", { "class": "fchips" });
      var tabSeg = el("div", { "class": "seg", role: "tablist", "aria-label": "Dashboard sections" });
      var tabBtns = TABS.map(function (t) {
        var b = el("button", { type: "button", role: "tab", "data-k": t[0] }, [t[1]]);
        b.addEventListener("click", function () { setTab(t[0]); });
        tabSeg.appendChild(b); return b;
      });
      var busyEl = el("span", { "class": "small muted hidden" }, [el("span", { "class": "spin" }), " Updating…"]);
      var buildInfo = el("span", { "class": "tiny muted" });
      var fcard = el("div", { "class": "card" }, [
        el("div", { "class": "fbar" }, [el("label", { "class": "f" }, ["As of", asOf]), el("label", { "class": "f" }, ["Search", search]),
          el("label", { "class": "f", title: "Shelf beyond this many days of stock counts as excess" }, ["Target days", target]),
          el("label", { "class": "f" }, ["Judge per", grainSeg]), el("label", { "class": "f" }, ["Scope", merchLbl])]),
        el("div", { "class": "fbar", style: { background: "var(--surface)" } }, FILTERS.map(function (f) { return ms[f[0]].el; })),
        chips,
        el("div", { "class": "ivd-tabs" }, [tabSeg, busyEl, el("span", { "class": "spacer" }), buildInfo])]);
      var statusBox = el("div");
      var body = el("div", { "class": "ivd-body" });
      ctx.main.appendChild(fcard); ctx.main.appendChild(statusBox); ctx.main.appendChild(body);

      // ── state changes ─────────────────────────────────────────────────────────────────────────────
      function colFilters() { var f = {}; Object.keys(st.sel).forEach(function (k) { if (st.sel[k] && st.sel[k].length) f[k] = st.sel[k]; }); return f; }
      function afterFilter() { drawChips(); paintGrain(); loadData(); }
      function toggleF(col, v) {
        if (v === null || v === undefined || v === "") return;
        var cur = (st.sel[col] || []).slice(), i = cur.indexOf(v);
        if (i >= 0) cur.splice(i, 1); else cur.push(v);
        st.sel[col] = cur; ms[col].set(cur); afterFilter();
      }
      function setTab(k) {
        st.tab = k; M.store.set("invdash.tab", k);
        tabBtns.forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-k") === k)); b.setAttribute("aria-selected", String(b.getAttribute("data-k") === k)); });
        drawBody();
      }
      function paintGrain() {
        var sz = (st.sel.SZ || []).length > 0, btns = grainSeg.querySelectorAll("button");
        btns[0].setAttribute("aria-pressed", String(st.grain === "option" && !sz)); btns[1].setAttribute("aria-pressed", String(st.grain === "size" || sz));
      }
      grainSeg.querySelectorAll("button").forEach(function (b, i) { b.addEventListener("click", function () { st.grain = i ? "size" : "option"; paintGrain(); loadData(); }); });
      search.addEventListener("input", M.debounce(function () { st.search = search.value.trim(); loadData(); }, 500));
      target.addEventListener("change", function () { st.target = +target.value; loadData(); });
      merch.addEventListener("change", function () { st.merch = merch.checked; loadData(); });
      asOf.addEventListener("change", function () {
        var d = asOf.value; if (!d || !st.meta || d === st.meta.asOf || st.building) return;
        st.data = null; loadMeta(d);
      });
      refreshBtn.addEventListener("click", function () { if (st.meta) loadData(); else loadMeta(asOf.value || null); });
      if (buildBtn) buildBtn.addEventListener("click", doBuild);

      function drawChips() {
        M.clear(chips); var f = colFilters(), keys = Object.keys(f);
        if (!keys.length) return;
        chips.appendChild(el("span", { "class": "tiny muted", style: { textTransform: "uppercase", letterSpacing: ".06em", alignSelf: "center" }, text: "Showing only" }));
        keys.forEach(function (c) {
          f[c].forEach(function (v) {
            var nm = c === "ST_CD" && st.meta && (st.meta.storeNames || {})[v] ? v + " " + st.meta.storeNames[v] : v;
            chips.appendChild(el("span", { "class": "chip" }, [el("span", { "class": "t", text: FLABEL[c] + ": " + nm }),
              el("button", { type: "button", "aria-label": "Remove " + FLABEL[c] + " " + v, text: "×", onclick: function () { toggleF(c, v); } })]));
          });
        });
        chips.appendChild(el("button", { type: "button", "class": "btn ghost sm", text: "Clear all", onclick: function () {
          st.sel = {}; FILTERS.forEach(function (x) { ms[x[0]].set([]); }); afterFilter(); } }));
      }
      function paintBuildInfo() {
        var b = (st.data && st.data.build) || (st.meta && st.meta.build);
        M.clear(buildInfo);
        buildInfo.appendChild(document.createTextNode(b ? "Stock as of " + b.asOf + " · sale through " + (b.saleTo || "—") + " · built " + M.fmtDate(b.builtAt) + (b.builtBy ? " by " + b.builtBy : "") + " · " : ""));
        buildInfo.appendChild(el("a", { href: "/mis/vert-inv", text: "Vertical Inventory (Article)" }));
      }

      // ── loading ───────────────────────────────────────────────────────────────────────────────────
      function waiting(text) {
        M.clear(body).appendChild(el("div", { "class": "card" }, el("div", { "class": "card-b", style: { padding: "40px 18px", textAlign: "center" } },
          [el("span", { "class": "spin" }), " ", el("span", { "class": "muted", text: text })])));
      }
      function failed(msg, retry) {
        M.clear(body).appendChild(el("div", { "class": "notice bad" }, [M.icon("info", 16), el("span", { text: msg + " " }),
          el("button", { type: "button", "class": "btn sm", text: "Try again", onclick: retry })]));
      }
      function loadMeta(d) {
        st.meta = null; st.data = null; paintTiles(); asOf.disabled = true;
        waiting("Opening " + (d || "the latest date") + " - a date opened for the first time is built from stock and sale (about 1–2 minutes)…");
        var p = M.get(API + "/params" + (d ? "?as_of=" + encodeURIComponent(d) : ""));
        st.metaP = p;
        FILTERS.forEach(function (f) { ms[f[0]].reset(); });
        return p.then(function (m) {
          if (!ctx.alive() || st.metaP !== p) return;
          st.meta = m; asOf.value = m.asOf || ""; asOf.max = m.latestEtDate || ""; asOf.disabled = false;
          paintBuildInfo(); drawChips(); loadData();
        }, function (e) {
          if (!ctx.alive() || st.metaP !== p) return;
          asOf.disabled = false; failed(e.message, function () { loadMeta(d); });
        });
      }
      function loadData() {
        if (!st.meta) return;
        if (st.ctrl) st.ctrl.abort();
        var ctrl = window.AbortController ? new AbortController() : null, my = ++st.seq; st.ctrl = ctrl;
        st.busy = true; busyEl.classList.remove("hidden");
        if (st.data) body.classList.add("busy"); else waiting("Comparing stock with sale…");
        M.post(API + "/data", { as_of: st.meta.asOf, col_filters: colFilters(), search: st.search || null, target_days: st.target, merch_only: st.merch, grain: st.grain },
               { signal: ctrl && ctrl.signal }).then(function (d) {
          if (!ctx.alive() || my !== st.seq) return;
          st.data = d; st.busy = false; busyEl.classList.add("hidden"); body.classList.remove("busy");
          paintTiles(); paintBuildInfo(); drawBody();
        }, function (e) {
          if (!ctx.alive() || my !== st.seq || (e && e.name === "AbortError")) return;
          st.busy = false; busyEl.classList.add("hidden"); body.classList.remove("busy");
          if (st.data) M.toast(e.message, "bad"); else failed(e.message, loadData);
        });
      }
      function doBuild() {
        var d = st.meta && st.meta.asOf; if (!d || st.building) return;
        M.confirm("Rebuild " + d + "?", "This re-reads stock (the Vertical Inventory (Article) slice) and VBRP sale for " + d +
          " and replaces the dashboard's stored slice for that date (BRONZE.MIS_INV_DASH_OPT / _SZ / _WK). It takes about a minute; everyone who opens " + d + " sees the new numbers.", "Rebuild").then(function (ok) {
          if (!ok || !ctx.alive()) return;
          st.building = true; asOf.disabled = true;
          var t0 = Date.now(), txt = el("span"), note = el("div", { "class": "notice warn" }, [el("span", { "class": "spin" }), txt]);
          function tick() { txt.textContent = "Rebuilding " + d + " from stock + sale… " + Math.round((Date.now() - t0) / 1000) + " s (usually about a minute). The page stays usable."; }
          tick(); var iv = setInterval(function () { if (!ctx.alive()) { clearInterval(iv); return; } tick(); }, 1000);
          M.clear(statusBox).appendChild(note);
          M.action(buildBtn, "Rebuilding…", function () { return M.post(API + "/build", { as_of: d, user: me.emp_code || null }); },
                   function (r) { var b = (r && r.build) || {}; return "Rebuilt " + d + (b.rows ? " - " + M.fmt(b.rows, "int") + " store × option rows in " + b.secs + " s" : ""); })
            .then(function () { if (ctx.alive()) loadMeta(d); }, function (e) {
              if (ctx.alive()) M.clear(statusBox).appendChild(el("div", { "class": "notice bad", text: "Rebuild failed: " + e.message }));
            }).then(function () {
              clearInterval(iv); st.building = false; asOf.disabled = false;
              if (ctx.alive() && note.parentNode) note.remove();
            });
        });
      }

      // ── masthead tiles (DFS overview tiles) ───────────────────────────────────────────────────────
      function paintTiles() {
        var t = st.data && st.data.total;
        if (!t) { M.setHeader({ tiles: ["Total stock", "In stores", "In DC + hub", "Sale · last 30 days", "Stock turn", "Negative stock"].map(function (k) { return { k: k, skel: true }; }) }); return; }
        var td = derive(t), turn = t.CL_V > 0 ? ((t.S90_V - t.S90_GM) * 365 / 90) / t.CL_V : null, gmroi = t.CL_V > 0 ? (t.S90_GM * 365 / 90) / t.CL_V : null;
        M.setHeader({ tiles: [
          { k: "Total stock", v: rs(t.CL_V), s: short(t.CL_Q) + " pcs · " + (t.CL_V >= t.OP_V ? "▲ " : "▼ ") + rs(Math.abs(t.CL_V - t.OP_V)) + " vs opening", title: "Every plant and location, net, at MAP (cost)" },
          { k: "In stores", v: rs(t.SV), s: pct(pctv(t.SV, t.CL_V), 0) + " of total · " + short(t.SQ) + " pcs", title: "Store shelf: store plants, ET_STOCK_DATA, SLOC other than 0099" },
          { k: "In DC + hub", v: rs(t.WH_V), s: "DC " + rs(t.DC_V) + " · hub " + rs(t.HUB_V) },
          { k: "Sale · last 30 days", v: rs(t.S30_V), s: rs(t.S30_V / 30) + " a day · GM " + pct(td.gm), title: "VBRP FP net billed value" },
          { k: "Stock turn", v: turn === null ? "—" : turn.toFixed(1) + "× a year", s: "GMROI " + (gmroi === null ? "—" : gmroi.toFixed(2)),
            title: "Turn = 90-day cost of sale, annualised, ÷ stock at cost. GMROI = annualised 90-day GM ÷ stock at cost" },
          { k: "Negative stock", v: rs(t.NEG_V), s: short(t.NEG_Q) + " pcs below zero", cls: "down" }] });
        var bang = tabBtns[TABS.length - 1].querySelector(".bang");
        if (t.OOS_N > 0 && !bang) tabBtns[TABS.length - 1].appendChild(el("span", { "class": "bang", title: "Options out of stock at stores", text: "!" }));
        if (!(t.OOS_N > 0) && bang) bang.remove();
      }

      // ── card helper: title + one-line note, optional chart / table toggle and right-hand tools ─────
      function card(span, title, note, content, o) {
        o = o || {}; var b = el("div", { "class": "card-b" }), asTable = false;
        var tools = el("span", { "class": "row", style: { marginLeft: "auto", gap: "6px" } }, o.right || []);
        if (o.table) {
          var tb = el("button", { type: "button", "class": "btn ghost sm", title: "Show the numbers as a table", "aria-label": "Show the numbers as a table" }, [M.icon("cols", 15)]);
          tb.addEventListener("click", function () {
            asTable = !asTable; tb.title = asTable ? "Show the chart" : "Show the numbers as a table"; tb.setAttribute("aria-label", tb.title);
            M.clear(tb).appendChild(M.icon(asTable ? "dash" : "cols", 15)); fill();
          });
          tools.appendChild(tb);
        }
        function fill() { M.clear(b); [].concat(asTable ? o.table() : content).forEach(function (n) { if (n) b.appendChild(n); }); }
        fill();
        return el("div", { "class": "card " + span }, [el("div", { "class": "card-h" }, [el("div", { style: { minWidth: 0 } }, [el("h2", { text: title }), note ? el("div", { "class": "note", text: note }) : null]), tools]), b]);
      }
      function grid12(kids) { return el("div", { "class": "grid12" }, kids); }
      function legendOf(rows) { return M.charts.legend(rows.map(function (r) { return { label: r.label || r.name, color: r.color }; })); }
      function groupLegend() { return legendOf(GROUPS.map(function (g) { return { label: g.label, color: g.color }; })); }

      // ── derived views (DFS useMemo block) ───────────────────────────────────────────────────────────
      function views() {
        var data = st.data, t = data.total || {}, target = st.target, V = { t: t, td: derive(t), target: target };
        V.netCover = t.S30_Q > 0 ? t.CL_Q / (t.S30_Q / 30) : null;
        V.abc = data.abc || {};
        V.abcTot = Object.keys(V.abc).reduce(function (a, k) { var v = V.abc[k]; return { n: a.n + v.n, S: a.S + v.S90_V, C: a.C + v.CL_V }; }, { n: 0, S: 0, C: 0 });
        V.whereData = WHERE.map(function (w, i) { return { name: w[1], value: Math.max(0, t[w[0]] || 0), color: PAL[i] }; });
        var bm = {}; (data.bkt || []).forEach(function (r) { bm[r.BKT] = r; });
        V.bkt = BUCKETS.map(function (b) { var r = bm[b[0]] || {}; return { k: b[0], l: b[1], c: b[2], SV: r.SV || 0, SQ: r.SQ || 0, LIVE_N: r.LIVE_N || 0 }; });
        V.bktTot = sum(V.bkt, "SV");
        V.groups = GROUPS.map(function (g) {
          var rows = V.bkt.filter(function (r) { return g.b.indexOf(r.k) >= 0; });
          return assign({}, g, { name: g.label, value: sum(rows, "SV"), n: sum(rows, "LIVE_N") });
        });
        V.segData = (data.seg || []).filter(function (r) { return r.SV > 0 || r.S30_V > 0; }).sort(function (a, b) { return b.SV - a.SV; });
        V.divs = (data.div || []).filter(function (r) { return r.SV > 0 || r.S30_V > 0; })
          .map(function (r) { return assign({}, r, derive(r), { stockShare: pctv(r.SV, t.SV), saleShare: pctv(r.S30_V, t.S30_V) }); })
          .sort(function (a, b) { return b.SV - a.SV; });
        var dm = {}; (data.div_bkt || []).forEach(function (r) { (dm[r.DIV] = dm[r.DIV] || {})[r.BKT] = r.SV; });
        V.divHealth = V.divs.map(function (d) {
          var row = dm[d.DIV] || {}, tot = sum(BUCKETS, function (b) { return row[b[0]] || 0; }) || 1, out = { DIV: d.DIV, _abs: {} };
          GROUPS.forEach(function (g) { var a = sum(g.b, function (k) { return row[k] || 0; }); out[g.k] = a / tot * 100; out._abs[g.k] = a; });
          return out;
        });
        var pm = {}; (data.pb || []).forEach(function (r) { pm[r.PB] = r; });
        V.pbRows = PB_ORDER.filter(function (k) { return pm[k] && (pm[k].SV || pm[k].S30_V); })
          .map(function (k) { return assign({}, pm[k], derive(pm[k]), { PB: PB[k], stockShare: pctv(pm[k].SV, t.SV), saleShare: pctv(pm[k].S30_V, t.S30_V) }); });
        V.zones = (data.zone || []).map(function (r) { return r.ZONE; }).sort();
        V.zoneColor = function (z) { return PAL[Math.max(0, V.zones.indexOf(z)) % PAL.length]; };
        V.zoneRows = (data.zone || []).map(function (r) { return assign({}, r, derive(r)); });
        V.stores = (data.store || []).map(function (r) { var x = assign({}, r, derive(r), { saleDay: r.S30_V / 30 }); x.health = healthOf(x, target); x.STATUS = x.health ? x.health.label : null; return x; });
        V.mjAll = (data.mj || []).filter(function (r) { return r.SV > 0 || r.S30_V > 0 || r.WH_V > 0; }).map(function (r) {
          var x = assign({}, r, derive(r), { stockShare: pctv(r.SV, t.SV), saleShare: pctv(r.S30_V, t.S30_V) });
          x.idx = x.saleShare ? x.stockShare / x.saleShare : null; x.health = healthOf(x, target); x.STATUS = x.health ? x.health.label : null; return x;
        });
        V.weekly = (data.weekly || []).map(function (w) { return assign({}, w, { gm: pctv(w.S_GM, w.S_V) }); });
        V.weekAvg = V.weekly.length ? sum(V.weekly, "S_V") / V.weekly.length : 0;
        V.actions = data.actions || {};
        return V;
      }

      // ── the tabs ──────────────────────────────────────────────────────────────────────────────────
      function drawBody() {
        hideTip(); if (!st.data) return;
        M.clear(body);
        tabBtns.forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-k") === st.tab)); });
        var V = views();
        if (!V.t || (!V.t.CL_V && !V.t.S30_V && !V.t.SV)) body.appendChild(el("div", { "class": "notice warn", text: "No stock and no sale under these filters - widen the filters or pick another date." }));
        var fn = { overview: overview, health: health, mix: mix, size: sizeTab, stores: storesTab, cats: catsTab, act: actTab }[st.tab] || overview;
        fn(V).forEach(function (n) { if (n) body.appendChild(n); });
      }

      function overview(V) {
        var t = V.t, td = V.td, target = V.target, out = [];
        function gcard(title, hint, g, sub) { return card("c3", title, hint, [el("div", { "class": "ivd-g" }, [g, el("div", { "class": "tiny muted", style: { textAlign: "center" }, text: sub })])]); }
        out.push(grid12([
          gcard("Days of stock in stores", "How many days the store shelf lasts at the last 30 days' sale rate",
            gauge(td.cover, target * 3, days(td.cover), [[15, "#eda100", "Running low"], [target, "#12a36f", "On target"], [target * 2, "#3f7fd0", "Above target"], [Infinity, "#d23b3a", "Far above target"]]),
            "target " + target + " d · whole network " + days(V.netCover)),
          gcard("Sell-through · 30 days", "30-day sale qty ÷ (sale qty + shelf qty)",
            gauge(td.st, 50, pct(td.st), [[10, "#d23b3a", "Slow"], [20, "#eda100", "Fair"], [Infinity, "#12a36f", "Good"]]), "of (sold + on shelf) was sold"),
          gcard("Out of stock", "Share of the options a store sold in 30 days that it has no shelf stock of now",
            gauge(td.oos, 40, pct(td.oos), [[8, "#12a36f", "Low"], [15, "#eda100", "Watch"], [Infinity, "#d23b3a", "High"]]), rs(t.LOST_V) + " of sale a day at risk"),
          gcard("Stock not selling", "Shelf value on options with no sale at that store in 30 days",
            gauge(td.deadPct, 100, pct(td.deadPct, 0), [[20, "#12a36f", "Low"], [35, "#eda100", "Watch"], [Infinity, "#d23b3a", "High"]]), rs((t.DEAD_V || 0) + (t.SLOW_V || 0)) + " of shelf")]));

        // In plain words
        var ins = [];
        if (t.SV) {
          var g = {}; V.groups.forEach(function (x) { g[x.k] = x; });
          ins.push(["var(--ok)", pct(pctv(g.OK.value, V.bktTot), 0) + " of store shelf value is healthy (15–60 days of stock). " + pct(pctv(g.HIGH.value, V.bktTot), 0) + " is more than needed and " + pct(pctv(g.RUN.value, V.bktTot), 0) + " is about to run out."]);
          ins.push(["var(--bad)", rs(g.NOT.value) + " (" + pct(pctv(g.NOT.value, V.bktTot), 0) + " of shelf) sits on options that have not sold at that store in 30 days - " + rs(t.DEAD_V) + " of it nothing in 90 days."]);
          var oos = V.actions.OOS || [], oosDc = oos.filter(function (r) { return r.WH_Q > 0; }).length;
          ins.push(["var(--warn)", pct(td.oos, 0) + " of the options a store sells are out of stock there right now - about " + rs(t.LOST_V) + " of sale a day at risk. " + oosDc + " of the top " + oos.length + " have DC / hub stock ready to send (see Act now)."]);
          if (V.abc.A && V.abcTot.C) ins.push(["var(--brand)", "The best " + pct(pctv(V.abc.A.n, V.abcTot.n), 0) + " of options bring 80% of sale but hold only " + pct(pctv(V.abc.A.CL_V, V.abcTot.C), 0) + " of stock; options with little or no sale hold " + pct(pctv(((V.abc.C || {}).CL_V || 0) + ((V.abc.N || {}).CL_V || 0), V.abcTot.C), 0) + "."]);
          var big = V.divs.filter(function (d) { return d.cover !== null && d.SV > t.SV * 0.02; });
          var over = big.slice().sort(function (a, b) { return b.cover - a.cover; })[0], under = big.slice().sort(function (a, b) { return a.cover - b.cover; })[0];
          if (over && under && over !== under) ins.push(["var(--brand)", over.DIV + " has the most stock for its sale (" + days(over.cover) + " of cover); " + under.DIV + " the least (" + days(under.cover) + "). Target is " + target + " days."]);
        }
        if (ins.length) out.push(card("c12", "In plain words", "What the numbers below say", [el("ul", { "class": "ivd-ins" }, ins.map(function (x) { return el("li", {}, [el("i", { style: { background: x[0] } }), el("span", { text: x[1] })]); }))]));

        // donuts
        var whereTot = sum(V.whereData, "value");
        var details = el("button", { type: "button", "class": "btn ghost sm", text: "Details →", onclick: function () { setTab("health"); } });
        var segDn = [["SV", "Stock"], ["S30_V", "Sale"]].map(function (kv) {
          var d = V.segData.map(function (r) { return { name: r.SEG, value: Math.max(0, r[kv[0]]), color: segColor(r.SEG) }; });
          return donutSvg(d, { size: 130, center: kv[1], sub: rs(sum(d, "value")), onClick: function (x) { toggleF("SEG", x.name); } });
        });
        var segList = el("ul", { "class": "ivd-leg", style: { marginTop: "8px" } }, V.segData.map(function (r) {
          return el("li", { "class": "clk", title: "Click to filter on " + r.SEG, onclick: function () { toggleF("SEG", r.SEG); } }, [swatch(segColor(r.SEG)), el("span", { "class": "nm", text: r.SEG }),
            el("span", { "class": "v", style: { fontWeight: 400, color: "var(--muted)" } }, ["stock ", el("b", { style: { color: "var(--fg)" }, text: pct(pctv(r.SV, t.SV), 0) })]),
            el("span", { style: { width: "88px", textAlign: "right", color: "var(--muted)" } }, ["sale ", el("b", { style: { color: "var(--fg)" }, text: pct(pctv(r.S30_V, t.S30_V), 0) })])]);
        }));
        out.push(grid12([
          card("c4", "Where is the stock?", "Total stock by location", [donutBox(V.whereData, { center: rs(whereTot), sub: "total" })]),
          card("c4", "Is the store stock healthy?", "Store shelf by how long it will last at the current sale rate", [
            donutBox(V.groups, { center: pct(pctv(V.groups[1].value, V.bktTot), 0), sub: "healthy" }),
            el("div", { "class": "stack", style: { gap: "2px", marginTop: "8px" } }, GROUPS.map(function (x) {
              return el("div", { "class": "tiny muted" }, [el("b", { style: { color: x.color }, text: x.label + ": " }), x.desc]); }))], { right: [details] }),
          card("c4", "What sells vs what we hold", "Segment share of store stock (left) and of 30-day sale (right) · click to filter", [
            el("div", { "class": "row", style: { justifyContent: "space-around" } }, segDn), segList])]));

        // weekly + ABC
        var wk = V.weekly.map(function (w) { return { label: String(w.wkEnd || "").slice(5), value: w.S_V, w: w }; });
        var abcK = ["A", "B", "C", "N"].filter(function (c) { return V.abc[c]; });
        var abcPies = [["S90_V", "Share of sale"], ["CL_V", "Share of stock"]].map(function (kv) {
          var d = abcK.map(function (c, i) { return { name: c === "N" ? "No sale" : "Class " + c, value: Math.max(0, V.abc[c][kv[0]]), color: PAL[i] }; });
          return el("div", { style: { display: "flex", flexDirection: "column", alignItems: "center", gap: "4px" } }, [donutSvg(d, { size: 120, inner: 0, pctLabels: true }), el("div", { "class": "small", style: { fontWeight: 600 }, text: kv[1] })]);
        });
        out.push(grid12([
          card("c8", "Weekly sale - last 13 weeks", "Net billed value per 7-day week ending on the as-of date · dashed line = 13-week average",
            wk.length ? [areaChart(wk, { avg: V.weekAvg, label: "Weekly sale", tip: function (r) { return tipHtml("Week ending " + r.w.wkEnd, [["Sale", rs(r.w.S_V)], ["Qty", short(r.w.S_Q) + " pcs"], ["GM", pct(r.w.gm)]]); } })] : [empty()],
            { table: function () { return sortTable(V.weekly, [{ k: "wk", h: "Week ending", v: function (r) { return -r.wk; }, f: function (r) { return r.wkEnd; } },
              { k: "S_V", h: "Sale ₹", num: 1, f: function (r) { return full(r.S_V); } }, { k: "S_Q", h: "Qty", num: 1, f: function (r) { return full(r.S_Q); } },
              { k: "gm", h: "GM%", num: 1, f: function (r) { return pct(r.gm); } }], { initial: { k: "wk", dir: 1 } }); } }),
          card("c4", "Best sellers vs stock (ABC)", "Options ranked by 90-day sale - A: the ones that make 80% of sale", abcK.length ? [
            el("div", { "class": "row", style: { justifyContent: "space-around" } }, abcPies),
            el("ul", { "class": "ivd-leg", style: { marginTop: "8px" } }, abcK.map(function (c, i) {
              return el("li", {}, [swatch(PAL[i]), el("span", { "class": "nm", text: c === "N" ? "No sale" : "Class " + c }), el("span", { "class": "tiny muted", text: full(V.abc[c].n) + " options" }),
                el("span", { "class": "v", style: { fontWeight: 400 } }, ["sale ", el("b", { text: pct(pctv(V.abc[c].S90_V, V.abcTot.S), 0) }), " · stock ", el("b", { text: pct(pctv(V.abc[c].CL_V, V.abcTot.C), 0) })])]);
            }))] : [empty()])]));
        return out;
      }
      function segColor(sg) { var i = ["APP", "GM", "MKT", "NT"].indexOf(sg); return PAL[i >= 0 ? i : 4]; }

      function health(V) {
        var t = V.t, target = V.target;
        var bktChart = vbars(V.bkt, { series: [{ key: "SV", name: "Shelf" }], label: function (r) { return r.l; }, colorOf: function (r) { return r.c; },
          top: function (r) { return pct(pctv(r.SV, V.bktTot), 0); }, title: "Store shelf by days of stock",
          tip: function (r) { return tipHtml(r.l, [["Shelf", rs(r.SV)], ["Share", pct(pctv(r.SV, V.bktTot))], ["Store × options", full(r.LIVE_N)]]); } });
        var groupsBox = el("div", { "class": "ivd-groups" }, V.groups.map(function (g) {
          return el("div", { style: { background: "color-mix(in srgb, " + g.color + " 10%, transparent)" }, title: g.desc }, [
            el("div", { "class": "small", style: { color: g.color, fontWeight: 600 }, text: g.label }), el("b", { text: rs(g.value) }),
            el("div", { "class": "tiny muted", text: pct(pctv(g.value, V.bktTot), 0) + " · " + full(g.n) + " store × options" })]);
        }));
        var dh = V.divHealth.length ? hbars(V.divHealth, { series: GROUPS.map(function (g) { return { key: g.k, name: g.label, color: g.color }; }), stacked: true, max: 100,
          label: function (r) { return r.DIV; }, fmt: function (v) { return Math.round(v) + "%"; }, onClick: function (r) { toggleF("DIV", r.DIV); },
          tip: function (r) { return tipHtml(r.DIV, GROUPS.map(function (g) { return [g.label, pct(r[g.k], 0) + " · " + rs(r._abs[g.k])]; })); } }) : empty();
        var covDivs = V.divs.filter(function (d) { return d.cover !== null; });
        var dc = covDivs.length ? hbars(covDivs, { series: [{ key: "cover", name: "Days of stock" }], label: function (r) { return r.DIV; }, fmt: function (v) { return Math.round(v); },
          valFmt: function (v) { return days(v); }, colorOf: function (r) { return (healthOf(r, target) || GROUPS[1]).color; }, ref: { v: target, text: "target" },
          onClick: function (r) { toggleF("DIV", r.DIV); }, tip: function (r) { return tipHtml(r.DIV, [["Days of stock", days(r.cover)], ["Shelf", rs(r.SV)], ["Sale 30d", rs(r.S30_V)]]); } }) : empty();
        var dd = V.divs.slice(0, 7).map(function (d, i) { return { name: d.DIV, value: d.SV, color: PAL[i % PAL.length] }; });
        if (V.divs.length > 7) dd.push({ name: "Other (" + (V.divs.length - 7) + ")", value: sum(V.divs.slice(7), "SV"), color: "#9ca3af", other: true });
        return [grid12([
          card("c6", "Store shelf by days of stock", "Each store × option with stock, by how many days its own 30-day sale would take to clear it", [bktChart, groupsBox], {
            table: function () { return sortTable(V.bkt, [
              { k: "k", h: "Bucket", v: function (r) { return BUCKETS.map(function (b) { return b[0]; }).indexOf(r.k); }, f: function (r) { return el("span", { "class": "row", style: { gap: "6px" } }, [swatch(r.c), r.l]); } },
              { k: "SV", h: "Shelf ₹", num: 1, f: function (r) { return full(r.SV); } }, { k: "sh", h: "Share", num: 1, v: function (r) { return r.SV; }, f: function (r) { return pct(pctv(r.SV, V.bktTot)); } },
              { k: "SQ", h: "Qty", num: 1, f: function (r) { return full(r.SQ); } }, { k: "LIVE_N", h: "Store × options", num: 1, f: function (r) { return full(r.LIVE_N); } }], { initial: { k: "k", dir: 1 } }); } }),
          card("c6", "Health by division", "Share of each division's shelf in each group · click a bar to filter", [groupLegend(), dh]),
          card("c6", "Days of stock by division", "Store shelf ÷ daily sale · dashed line = target " + target + " days · click to filter", [dc]),
          card("c6", "Where stock is, by division", "Store shelf of each division - the biggest slices hold the most money",
            [donutBox(dd, { size: 200, center: rs(t.SV), sub: "store shelf", onClick: function (d) { if (!d.other) toggleF("DIV", d.name); } })])])];
      }

      function shareSeries() { return [{ key: "stockShare", name: "Share of stock", color: PAL[0] }, { key: "saleShare", name: "Share of sale", color: PAL[1] }]; }
      function shareLegend() { return legendOf([{ label: "Share of stock", color: PAL[0] }, { label: "Share of sale", color: PAL[1] }]); }
      function pctAxis(v) { return Math.round(v) + "%"; }

      function mix(V) {
        var t = V.t, target = V.target;
        var divChart = V.divs.length ? hbars(V.divs, { series: shareSeries(), label: function (r) { return r.DIV; }, fmt: pctAxis, onClick: function (r) { toggleF("DIV", r.DIV); },
          tip: function (r) { return tipHtml(r.DIV, [["Share of stock", pct(r.stockShare)], ["Share of sale", pct(r.saleShare)], ["Days of stock", days(r.cover)]]); } }) : empty();
        var pbChart = V.pbRows.length ? vbars(V.pbRows, { series: shareSeries(), label: function (r) { return r.PB; }, fmt: pctAxis, maxBar: 26,
          tip: function (r) { return tipHtml("₹ " + r.PB + " · " + days(r.cover) + " of stock", [["Share of stock", pct(r.stockShare)], ["Share of sale", pct(r.saleShare)]]); } }) : empty();
        var zoneDn = [["SV", "Store shelf", t.SV], ["S30_V", "Sale · 30 days", t.S30_V]].map(function (kv) {
          return el("div", { "class": "stack", style: { gap: "4px" } }, [el("div", { "class": "small", style: { fontWeight: 600 }, text: kv[1] }),
            donutBox(V.zoneRows.map(function (r) { return { name: r.ZONE, value: r[kv[0]], color: V.zoneColor(r.ZONE) }; }), { size: 140, center: rs(kv[2]), onClick: function (d) { toggleF("ZONE", d.name); } })]);
        });
        var cov = function (q) { return t.S30_Q > 0 ? q / (t.S30_Q / 30) : null; };
        return [grid12([
          card("c6", "Stock share vs sale share - by division", "A division whose stock bar is much longer than its sale bar holds more stock than it sells · click to filter", [shareLegend(), divChart], {
            table: function () { return sortTable(V.divs, [{ k: "DIV", h: "Division" }, { k: "SV", h: "Shelf ₹", num: 1, f: function (r) { return short(r.SV); } },
              { k: "S30_V", h: "Sale 30d ₹", num: 1, f: function (r) { return short(r.S30_V); } }, { k: "stockShare", h: "Stock %", num: 1, f: function (r) { return pct(r.stockShare); } },
              { k: "saleShare", h: "Sale %", num: 1, f: function (r) { return pct(r.saleShare); } },
              { k: "cover", h: "Days of stock", num: 1, f: function (r) { return days(r.cover); }, tone: function (r) { return coverTone(r.cover, target); } },
              { k: "st", h: "Sell-thru", num: 1, f: function (r) { return pct(r.st); } }, { k: "oos", h: "Out of stock", num: 1, f: function (r) { return pct(r.oos); } },
              { k: "deadPct", h: "Not selling", num: 1, f: function (r) { return pct(r.deadPct, 0); } }, { k: "gm", h: "GM%", num: 1, f: function (r) { return pct(r.gm); } }],
              { initial: { k: "SV", dir: -1 }, onRow: function (r) { toggleF("DIV", r.DIV); }, rowTitle: "Click to filter on this division" }); } }),
          card("c6", "Price bands - stock vs sale", "Bands on MAP (cost per piece): which price points we hold vs which ones sell", [shareLegend(), pbChart], {
            table: function () { return sortTable(V.pbRows, [{ k: "PB", h: "MAP band ₹" }, { k: "SV", h: "Shelf ₹", num: 1, f: function (r) { return short(r.SV); } },
              { k: "S30_V", h: "Sale 30d ₹", num: 1, f: function (r) { return short(r.S30_V); } }, { k: "stockShare", h: "Stock %", num: 1, f: function (r) { return pct(r.stockShare); } },
              { k: "saleShare", h: "Sale %", num: 1, f: function (r) { return pct(r.saleShare); } }, { k: "cover", h: "Days of stock", num: 1, f: function (r) { return days(r.cover); } },
              { k: "deadPct", h: "Not selling", num: 1, f: function (r) { return pct(r.deadPct, 0); } }], { initial: { k: "SV", dir: -1 } }); } }),
          card("c6", "Zones - stock and sale", "Click a slice or a row to filter on that zone", [
            el("div", { "class": "grid12", style: { gap: "12px" } }, zoneDn.map(function (z) { return el("div", { "class": "c6" }, z); })),
            el("div", { style: { marginTop: "12px" } }, sortTable(V.zoneRows, [
              { k: "ZONE", h: "Zone", f: function (r) { return el("span", { "class": "row", style: { gap: "6px", fontWeight: 600 } }, [swatch(V.zoneColor(r.ZONE), true), r.ZONE]); } },
              { k: "SV", h: "Shelf ₹", num: 1, f: function (r) { return short(r.SV); } }, { k: "S30_V", h: "Sale 30d ₹", num: 1, f: function (r) { return short(r.S30_V); } },
              { k: "cover", h: "Days of stock", num: 1, f: function (r) { return days(r.cover); }, tone: function (r) { return coverTone(r.cover, target); } },
              { k: "st", h: "Sell-thru", num: 1, f: function (r) { return pct(r.st); } },
              { k: "oos", h: "Out of stock", num: 1, f: function (r) { return pct(r.oos); }, tone: function (r) { return badTone(r.oos, 10, 20); } },
              { k: "deadPct", h: "Not selling", num: 1, f: function (r) { return pct(r.deadPct, 0); }, tone: function (r) { return badTone(r.deadPct, 30, 50); } }],
              { initial: { k: "SV", dir: -1 }, onRow: function (r) { toggleF("ZONE", r.ZONE); }, maxH: "16rem", rowTitle: "Click to filter on this zone" }))]),
          card("c6", "Plant type - where the value sits", "Total stock by plant type · click to filter", [
            donutBox((st.data.st_typ || []).map(function (r, i) { return { name: r.ST_TYP, value: Math.max(0, r.CL_V), color: PAL[i % PAL.length] }; }),
              { center: rs(t.CL_V), sub: "total", onClick: function (d) { toggleF("ST_TYP", d.name); } }),
            el("p", { "class": "small muted", style: { margin: "12px 0 0" }, text: "DC + hub stock alone would cover " + days(cov((t.DC_Q || 0) + (t.HUB_Q || 0)) ) +
              " of network sale; stock in transit + PTL / PRD pipeline another " + days(cov((t.TR_Q || 0) + (t.PIPE_Q || 0))) + "." })])])];
      }

      function storeLabel(r) { return r.ST_CD + " " + (r.ST_NM || ""); }
      function storesTab(V) {
        var target = V.target, stores = V.stores;
        var counts = GROUPS.map(function (g) { return { name: g.label, color: g.color, value: stores.filter(function (x) { return x.health === g; }).length }; });
        var dots = stores.filter(function (r) { return r.cover !== null && r.saleDay > 0 && r.SV > 0; })
          .map(function (r) { return { x: Math.min(r.cover, 400), y: r.saleDay, z: r.SV, color: V.zoneColor(r.ZONE), r: r }; });
        var sc = dots.length ? scatter(dots, { ref: target, xTitle: "Days of stock", onClick: function (d) { toggleF("ST_CD", d.r.ST_CD); },
          tip: function (d) { var r = d.r; return tipHtml(storeLabel(r), [["Zone", r.ZONE || "—"], ["Days of stock", days(r.cover)], ["Sale / day", rs(r.saleDay)], ["Shelf", rs(r.SV)],
            ["Out of stock", pct(r.oos)], ["Not selling", pct(r.deadPct, 0)]]); } }) : empty();
        var most = stores.filter(function (x) { return x.cover !== null && x.SV > 0; }).sort(function (a, b) { return b.cover - a.cover; }).slice(0, 12);
        var oos = stores.filter(function (x) { return x.oos !== null && x.SELL_N >= 50; }).sort(function (a, b) { return b.oos - a.oos; }).slice(0, 12);
        function top12(rows, key, color, fmtv, name) {
          return rows.length ? M.charts.bars(rows.map(function (r) { return { label: storeLabel(r), value: r[key], r: r }; }),
            { horizontal: true, color: color, format: fmtv, valueName: name, title: name, onClick: function (x) { toggleF("ST_CD", x.r.ST_CD); } }) : empty();
        }
        var q = el("input", { type: "search", "class": "ivd-find", placeholder: "Find store", "aria-label": "Find store", value: st.storeQ });
        var gbox = el("div", { "class": "card c12" });
        var g = localGrid(gbox, {
          id: "inv-dash-stores", title: "All stores - " + stores.length + " plants", sort_col: "SV", tools: [q],
          rows: function () { var s2 = st.storeQ.trim().toLowerCase(); return s2 ? stores.filter(function (r) { return (r.ST_CD + " " + r.ST_NM + " " + r.ZONE).toLowerCase().indexOf(s2) >= 0; }) : stores; },
          rowClick: function (r) { toggleF("ST_CD", r.ST_CD); },
          cols: [{ k: "ST_CD", h: "Store", key: 1, show: function (r) { return storeLabel(r); } }, { k: "ZONE", h: "Zone", key: 1 },
            { k: "STATUS", h: "Status", key: 1, node: function (r) { return badge(r.health); } },
            { k: "SV", h: "Shelf ₹", show: function (r) { return short(r.SV); } }, { k: "S30_V", h: "Sale 30d ₹", show: function (r) { return short(r.S30_V); } },
            { k: "cover", h: "Days of stock", show: function (r) { return days(r.cover); }, tone: function (r) { return coverTone(r.cover, target); }, title: function () { return "Shelf qty ÷ 30-day daily sale qty"; } },
            { k: "st", h: "Sell-thru %", show: function (r) { return pct(r.st); } },
            { k: "oos", h: "Out of stock %", show: function (r) { return pct(r.oos); }, tone: function (r) { return badTone(r.oos, 10, 20); } },
            { k: "deadPct", h: "Not selling %", show: function (r) { return pct(r.deadPct, 0); }, tone: function (r) { return badTone(r.deadPct, 30, 50); } },
            { k: "EXV", h: "Excess ₹", show: function (r) { return short(r.EXV); }, title: function () { return "Shelf beyond " + target + " days of cover"; } },
            { k: "gm", h: "GM%", show: function (r) { return pct(r.gm); } },
            { k: "NEG_V", h: "Negative ₹", show: function (r) { return short(r.NEG_V); }, tone: function (r) { return r.NEG_V < 0 ? "ivd-bad" : "ivd-mut"; } }]
        });
        q.addEventListener("input", M.debounce(function () { st.storeQ = q.value; g.reload(true); }, 250));
        return [grid12([
          card("c4", "How many stores are in each state?", "Each store's status from its shelf cover and unsold share",
            [donutBox(counts, { center: full(stores.filter(function (x) { return x.health; }).length), sub: "stores", fmt: full })]),
          card("c8", "Stores - days of stock vs sale per day", "Each dot is a store (bigger = more shelf value); right of the dashed line = more than " + target + " days of stock · click a dot to filter",
            [legendOf(V.zones.map(function (z) { return { label: z, color: V.zoneColor(z) }; })), sc]),
          card("c6", "Most stock for their sale", "Top 12 stores by days of stock · click to filter", [top12(most, "cover", "#3f7fd0", days, "Days of stock")]),
          card("c6", "Most out of stock", "Top 12 stores by share of selling options with no shelf stock (stores selling 50+ options)", [top12(oos, "oos", "#eda100", function (v) { return pct(v, 0); }, "Out of stock")]),
          gbox])];
      }

      function catsTab(V) {
        var target = V.target, mjAll = V.mjAll;
        var tree = mjAll.filter(function (r) { return r.SV > 0; }).sort(function (a, b) { return b.SV - a.SV; }).slice(0, 60)
          .map(function (r) { return { name: r.MJ, value: r.SV, color: r.health ? r.health.color : "#9ca3af", r: r }; });
        var tm = tree.length ? treemap(tree, { onClick: function (x) { toggleF("MJ", x.name); },
          tip: function (x) { var r = x.r; return tipHtml(r.MJ, [["Shelf", rs(r.SV)], ["Sale 30d", rs(r.S30_V)], ["Days of stock", days(r.cover)], ["Not selling", pct(r.deadPct, 0)], ["Out of stock", pct(r.oos)], ["Status", r.health ? r.health.label : "—"]]); } }) : empty();
        var over = mjAll.filter(function (r) { return r.idx !== null && r.stockShare >= 0.3; }).sort(function (a, b) { return b.idx - a.idx; }).slice(0, 12);
        var fast = mjAll.filter(function (r) { return r.cover !== null && r.saleShare >= 0.3; }).sort(function (a, b) { return a.cover - b.cover; }).slice(0, 12);
        var overC = over.length ? hbars(over, { series: [{ key: "idx", name: "Stock ÷ sale share", color: "#3f7fd0" }], label: function (r) { return r.MJ; }, labelW: 150, axis: false,
          valFmt: function (v) { return v.toFixed(1) + "×"; }, ref: { v: 1, text: "1×" }, onClick: function (r) { toggleF("MJ", r.MJ); },
          tip: function (r) { return tipHtml(r.MJ, [["Stock ÷ sale share", r.idx.toFixed(2) + "×"], ["Stock share", pct(r.stockShare)], ["Sale share", pct(r.saleShare)]]); } }) : empty();
        var fastC = fast.length ? M.charts.bars(fast.map(function (r) { return { label: r.MJ, value: r.cover, r: r }; }),
          { horizontal: true, color: "#eda100", format: days, valueName: "Days of stock", title: "Selling faster than stocked", onClick: function (x) { toggleF("MJ", x.r.MJ); } }) : empty();
        var q = el("input", { type: "search", "class": "ivd-find", placeholder: "Find category", "aria-label": "Find category", value: st.mjQ });
        var gbox = el("div", { "class": "card c12" });
        var rowsFn = function () { var s2 = st.mjQ.trim().toLowerCase(); return s2 ? mjAll.filter(function (r) { return String(r.MJ).toLowerCase().indexOf(s2) >= 0; }) : mjAll; };
        var g = localGrid(gbox, {
          id: "inv-dash-mj", title: "All major categories - " + mjAll.length, sort_col: "SV", tools: [q], rows: rowsFn, rowClick: function (r) { toggleF("MJ", r.MJ); },
          cols: [{ k: "MJ", h: "Major category", key: 1 }, { k: "STATUS", h: "Status", key: 1, node: function (r) { return badge(r.health); } },
            { k: "SV", h: "Shelf ₹", show: function (r) { return short(r.SV); } }, { k: "WH_V", h: "DC+Hub ₹", show: function (r) { return short(r.WH_V); } },
            { k: "S30_V", h: "Sale 30d ₹", show: function (r) { return short(r.S30_V); } },
            { k: "idx", h: "Stock ÷ sale share", show: function (r) { return r.idx === null ? "—" : r.idx.toFixed(2); }, tone: function (r) { return badTone(r.idx, 1.3, 2); },
              title: function () { return "Share of shelf ÷ share of sale. Above 1 = holds more stock than it sells"; } },
            { k: "cover", h: "Days of stock", show: function (r) { return days(r.cover); }, tone: function (r) { return coverTone(r.cover, target); } },
            { k: "st", h: "Sell-thru %", show: function (r) { return pct(r.st); } },
            { k: "oos", h: "Out of stock %", show: function (r) { return pct(r.oos); }, tone: function (r) { return badTone(r.oos, 10, 20); } },
            { k: "deadPct", h: "Not selling %", show: function (r) { return pct(r.deadPct, 0); }, tone: function (r) { return badTone(r.deadPct, 30, 50); } },
            { k: "EXV", h: "Excess ₹", show: function (r) { return short(r.EXV); } }, { k: "gm", h: "GM%", show: function (r) { return pct(r.gm); } }]
        });
        q.addEventListener("input", M.debounce(function () { st.mjQ = q.value; g.reload(true); }, 250));
        return [grid12([
          card("c12", "Category map", "The 60 major categories with the most store shelf - tile size = shelf value, colour = status · click a tile to filter", [tm], { right: [groupLegend()] }),
          card("c6", "Holding more than they sell", "Stock share ÷ sale share, categories with at least 0.3% of shelf · click to filter", [overC]),
          card("c6", "Selling faster than stocked", "Lowest days of stock, categories with at least 0.3% of sale - replenish first", [fastC]),
          gbox])];
      }

      function actTab(V) {
        var t = V.t, target = V.target, actions = V.actions;
        var ACT = [
          ["OOS", "Out of stock at stores", "#eda100", "LOST_V", rs(t.LOST_V) + "/day",
           "These options sold at stores in the last 30 days but those stores have none on the shelf now. Ranked by the sale a day they are missing. A green DC + hub qty means stock is available to send."],
          ["DEAD", "Dead on shelf", "#d23b3a", "DEAD_V", rs(t.DEAD_V),
           "Shelf stock at stores where the option has not sold in 90 days, ranked by value. \"Sold 30d elsewhere\" above 0 means other stores sell it - transfer it there; otherwise mark it down."],
          ["EXCESS", "More than " + target + " days", "#3f7fd0", "EXV", rs(t.EXV),
           "Options that sell, but whose shelf stock is beyond " + target + " days of their own sale, ranked by the value above the target - hold back replenishment."]];
        var opt = [{ k: "GEN_ART", h: "Option", key: 1 }, { k: "CLR", h: "Colour", key: 1 }, { k: "DSC", h: "Description", key: 1, show: function (r) { return r.DSC || r.VENDOR || ""; } },
                   { k: "MJ", h: "Category", key: 1 }];
        var COLS = {
          OOS: opt.concat([{ k: "OOS_N", h: "Stores out of stock", show: function (r) { return full(r.OOS_N) + " of " + full(r.SELL_N); }, title: function () { return "Stores out of stock / stores that sold it in 30 days"; } },
            { k: "LOST_V", h: "Sale at risk / day", show: function (r) { return rs(r.LOST_V); } }, { k: "S30_Q", h: "Sold 30d", fmt: "int" },
            { k: "WH_Q", h: "DC + hub qty", fmt: "int", tone: function (r) { return r.WH_Q > 0 ? "ivd-ok" : "ivd-bad"; } }, { k: "TR_Q", h: "In transit", fmt: "int" }]),
          DEAD: opt.concat([{ k: "DEAD_V", h: "Dead shelf ₹", show: function (r) { return rs(r.DEAD_V); } }, { k: "DEAD_Q", h: "Dead qty", fmt: "int" },
            { k: "DEAD_ST", h: "Stores", fmt: "int", title: function () { return "Stores where it has not sold in 90 days"; } },
            { k: "S30_Q", h: "Sold 30d elsewhere", fmt: "int", title: function () { return "Sale at other stores - a transfer candidate if > 0"; } },
            { k: "LAST_SALE", h: "Last sale", key: 1, show: function (r) { return r.LAST_SALE || "> 90 d"; } }, { k: "MRP", h: "MRP", fmt: "int" }]),
          EXCESS: opt.concat([{ k: "EXV", h: "Excess ₹", show: function (r) { return rs(r.EXV); } }, { k: "EXQ", h: "Excess qty", fmt: "int" }, { k: "SQ", h: "Shelf qty", fmt: "int" },
            { k: "S30_Q", h: "Sold 30d", fmt: "int" }, { k: "cov", h: "Days of stock", show: function (r) { return days(r.cov); } }])
        };
        var pick = el("div", { "class": "ivd-act", role: "group", "aria-label": "Action lists" }, ACT.map(function (a) {
          var b = el("button", { type: "button", "aria-pressed": String(st.actTab === a[0]), style: "--ac:" + a[2] }, [
            el("div", { "class": "k", text: a[1] }), el("div", { "class": "v", text: a[4] }), el("div", { "class": "tiny muted", text: (actions[a[0]] || []).length + " top options listed" })]);
          b.addEventListener("click", function () { st.actTab = a[0]; drawBody(); });
          return b;
        }));
        var a = ACT.filter(function (x) { return x[0] === st.actTab; })[0] || ACT[0];
        var rows = (actions[a[0]] || []).map(function (r) { return assign({}, r, { cov: r.S30_Q > 0 ? r.SQ * 30 / r.S30_Q : null }); });
        var gcard = el("div", { "class": "card" }, [el("div", { "class": "card-h" }, [el("div", {}, [el("h2", { style: { color: a[2] }, text: a[1] }), el("div", { "class": "note", text: a[5] })])])]);
        localGrid(gcard, { id: "inv-dash-act-" + a[0].toLowerCase(), title: rows.length + " options", rows: function () { return rows; }, cols: COLS[a[0]], sort_col: a[3], limit: 50 });
        return [pick, gcard];
      }

      // ── size & colour ─────────────────────────────────────────────────────────────────────────────
      function sizeTab(V) {
        var data = st.data, t = V.t, target = V.target, sel = st.sel, out = [];
        var mjPicked = (sel.MJ || []).length > 0;
        var famRows = (data.clrFam || []).filter(function (r) { return r.SV > 0 || r.S30_V > 0; })
          .map(function (r) { return assign({}, r, derive(r), { stockShare: pctv(r.SV, t.SV), saleShare: pctv(r.S30_V, t.S30_V) }); }).sort(function (a, b) { return b.SV - a.SV; });
        var srows = (data.size || []).filter(function (r) { return r.SQ > 0 || r.ST30_Q > 0; }), tq = sum(srows, "SQ"), sq = sum(srows, "ST30_Q");
        var sizeRows = srows.map(function (r) { return assign({}, r, derive(r), { stockShare: pctv(r.SQ, tq), saleShare: pctv(r.ST30_Q, sq) }); }).sort(function (a, b) { return sizeCmp(a.SZ, b.SZ); });
        var gridSizes = sizeRows.slice().sort(function (a, b) { return b.ST30_Q - a.ST30_Q; }).slice(0, 14).sort(function (a, b) { return sizeCmp(a.SZ, b.SZ); }).map(function (r) { return r.SZ; });
        var fm = {}; (data.famSize || []).forEach(function (r) { (fm[r.CLR_FAM] = fm[r.CLR_FAM] || {})[r.SZ] = assign({}, r, derive(r)); });
        var famGrid = famRows.slice(0, 12).map(function (f) { return { fam: f.CLR_FAM, cells: fm[f.CLR_FAM] || {} }; });
        var shades = (data.clr || []).filter(function (r) { return r.SV > 0; }).map(function (r) { return assign({}, r, derive(r)); }).sort(function (a, b) { return b.SV - a.SV; }).slice(0, 15);
        var finder = data.storeFinder || { stores: [], cells: [] };
        var fStores = (finder.stores || []).map(function (r) { return assign({}, r, { cover: r.SQ30 > 0 ? r.SQ * 30 / r.SQ30 : null }); });
        var fv = st.finderView;
        var fTop = fStores.filter(function (r) { return fv === "short" ? r.SHORT_N > 0 : r.EXCESS_N > 0; })
          .sort(function (a, b) { return fv === "short" ? ((b.LOST_V - a.LOST_V) || (b.SHORT_N - a.SHORT_N)) : b.EXV - a.EXV; }).slice(0, 30);
        var fSizes = []; (finder.cells || []).forEach(function (c) { if (fSizes.indexOf(c.SZ) < 0) fSizes.push(c.SZ); }); fSizes.sort(sizeCmp);
        var cellMap = {}; (finder.cells || []).forEach(function (c) { cellMap[c.ST_CD + "|" + c.SZ] = c; });
        var nShort = fStores.filter(function (r) { return r.SHORT_N > 0; }).length, nExcess = fStores.filter(function (r) { return r.EXCESS_N > 0; }).length;
        var lostSum = sum(fStores, "LOST_V"), exSum = sum(fStores, "EXV");
        var selLabel = [(sel.MJ || []).join(", "), (sel.CLR_FAM || []).join(", "), (sel.CLR || []).join(", "), (sel.SZ || []).length ? "size " + sel.SZ.join(", ") : ""]
          .filter(Boolean).join(" · ") || "everything in the filters";

        if (!mjPicked) {
          var topMj = (data.mj || []).slice().sort(function (a, b) { return b.SV - a.SV; }).slice(0, 10).map(function (r) { return r.MJ; });
          out.push(el("div", { "class": "notice warn", style: { flexDirection: "column", gap: "4px" } }, [
            el("span", { text: "Sizes only compare within one kind of product - pick a major category first (and a colour if you like), e.g. M_JEANS + BLACK." }),
            el("div", { "class": "ivd-chips" }, topMj.map(function (m) { return el("button", { type: "button", text: m, onclick: function () { toggleF("MJ", m); } }); }))]));
        }

        // colours: donut + stock-share / sale-share bars
        var mx = Math.max.apply(null, famRows.map(function (x) { return Math.max(x.stockShare || 0, x.saleShare || 0); }).concat([1]));
        var famBars = el("div", { "class": "ivd-fbars stack", style: { gap: "2px", flex: 1, minWidth: 0 } }, [
          el("div", { "class": "tiny muted" }, [el("span", { style: { display: "inline-block", width: "94px" } }), "Bar = stock share · thin line = sale share · days of stock"])]
          .concat(famRows.slice(0, 12).map(function (r) {
            return el("button", { type: "button", title: r.CLR_FAM + ": stock " + pct(r.stockShare) + " · sale " + pct(r.saleShare) + " · " + days(r.cover) + " of stock", onclick: function () { toggleF("CLR_FAM", r.CLR_FAM); } }, [
              el("span", { "class": "nm" }, [swatch(famColor(r.CLR_FAM), true), r.CLR_FAM]),
              el("span", { "class": "tr" }, [el("span", { "class": "a", style: { width: (r.stockShare || 0) / mx * 100 + "%", background: famColor(r.CLR_FAM), outline: r.CLR_FAM === "WHITE" ? "1px solid #cbd5e1" : "none" } }),
                el("span", { "class": "b", style: { width: (r.saleShare || 0) / mx * 100 + "%" } })]),
              el("span", { "class": "d", text: days(r.cover) })]);
          })));
        var colours = card("c6", "Colours - what we hold vs what sells", "Store shelf by colour family · click a colour to filter", famRows.length ? [
          el("div", { "class": "ivd-dn", style: { alignItems: "flex-start" } }, [
            donutSvg(famRows.map(function (r) { return { name: r.CLR_FAM, value: r.SV, color: famColor(r.CLR_FAM) }; }), { size: 170, center: rs(t.SV), sub: "store shelf", onClick: function (d) { toggleF("CLR_FAM", d.name); } }),
            famBars])] : [empty()], {
          table: function () { return sortTable(famRows, [
            { k: "CLR_FAM", h: "Colour", f: function (r) { return el("span", { "class": "row", style: { gap: "6px" } }, [swatch(famColor(r.CLR_FAM), true), r.CLR_FAM]); } },
            { k: "SV", h: "Shelf ₹", num: 1, f: function (r) { return short(r.SV); } }, { k: "S30_V", h: "Sale 30d ₹", num: 1, f: function (r) { return short(r.S30_V); } },
            { k: "stockShare", h: "Stock %", num: 1, f: function (r) { return pct(r.stockShare); } }, { k: "saleShare", h: "Sale %", num: 1, f: function (r) { return pct(r.saleShare); } },
            { k: "cover", h: "Days of stock", num: 1, f: function (r) { return days(r.cover); }, tone: function (r) { return coverTone(r.cover, target); } },
            { k: "oos", h: "Out of stock", num: 1, f: function (r) { return pct(r.oos); } }, { k: "deadPct", h: "Not selling", num: 1, f: function (r) { return pct(r.deadPct, 0); } }],
            { initial: { k: "SV", dir: -1 }, onRow: function (r) { toggleF("CLR_FAM", r.CLR_FAM); } }); } });

        // size curve
        var curve;
        if (!sizeRows.length) curve = [empty()];
        else if (sizeRows.length > 40) curve = [empty(sizeRows.length + " sizes across different products - pick a major category to see its size curve.")];
        else curve = [shareLegend(), vbars(sizeRows, { series: shareSeries(), label: function (r) { return r.SZ; }, fmt: pctAxis, maxBar: 22, height: 240, onClick: function (r) { toggleF("SZ", r.SZ); },
            tip: function (r) { return tipHtml("Size " + r.SZ, [["Share of stock", pct(r.stockShare)], ["Share of sale", pct(r.saleShare)], ["On shelf", full(r.SQ)], ["Sold 30d", full(r.ST30_Q)], ["Days of stock", days(r.cover)]]); } }),
          el("div", { "class": "row", style: { gap: "4px", marginTop: "4px" } }, sizeRows.map(function (r) {
            var g = (r.SQ <= 0 && r.ST30_Q > 0 ? GROUPS[0] : healthOf(r, target)) || GROUPS[1];
            return el("span", { "class": "tiny", title: g.label + " - " + g.desc, style: { borderRadius: "5px", padding: "1px 6px", background: "color-mix(in srgb, " + g.color + " 14%, transparent)", color: g.color } }, [r.SZ + ": ", el("b", { text: days(r.cover) })]);
          })),
          el("div", { "class": "tiny muted", style: { marginTop: "4px" }, text: "Sale bar taller than the stock bar = the size sells more than we stock (short); stock taller = over-stocked." })];
        var sizeCard = card("c6", "Size curve - stock vs sale", "Share of shelf pieces vs share of 30-day pieces sold, per size - " + selLabel + " · click a size to filter", curve, {
          table: function () { return sortTable(sizeRows, [{ k: "SZ", h: "Size", v: function (r) { return sizeKey(r.SZ); } }, { k: "SQ", h: "Shelf pcs", num: 1, f: function (r) { return full(r.SQ); } },
            { k: "ST30_Q", h: "Sold 30d pcs", num: 1, f: function (r) { return full(r.ST30_Q); } }, { k: "stockShare", h: "Stock %", num: 1, f: function (r) { return pct(r.stockShare); } },
            { k: "saleShare", h: "Sale %", num: 1, f: function (r) { return pct(r.saleShare); } },
            { k: "cover", h: "Days of stock", num: 1, f: function (r) { return days(r.cover); }, tone: function (r) { return coverTone(r.cover, target); } },
            { k: "oos", h: "Out of stock", num: 1, f: function (r) { return pct(r.oos); } }, { k: "WH_Q", h: "DC + hub pcs", num: 1, f: function (r) { return full(r.WH_Q); } }],
            { initial: { k: "SZ", dir: 1 }, onRow: function (r) { toggleF("SZ", r.SZ); } }); } });
        out.push(grid12([colours, sizeCard]));

        // colour x size grid
        var cxs;
        if (!gridSizes.length || (gridSizes.length > 14 && !mjPicked)) cxs = [empty("Pick a major category to see its colours by size.")];
        else {
          var head = el("tr", {}, [el("th")].concat(gridSizes.map(function (sz) { return el("th", { scope: "col", text: sz }); })));
          var rowsEl = famGrid.map(function (row) {
            return el("tr", {}, [el("td", { "class": "lbl" }, el("button", { type: "button", "class": "lnk", onclick: function () { toggleF("CLR_FAM", row.fam); } }, [swatch(famColor(row.fam), true), row.fam]))]
              .concat(gridSizes.map(function (sz) {
                var c = row.cells[sz], has = c && (c.SQ > 0 || c.ST30_Q > 0), g = has ? (c.SQ <= 0 && c.ST30_Q > 0 ? GROUPS[0] : healthOf(c, target)) : null;
                return el("td", { "class": g ? null : "e", style: g ? { background: g.color, color: "#fff" } : { color: "var(--muted)" },
                  title: c ? row.fam + " · size " + sz + "\nshelf " + full(c.SQ) + " pcs · sold 30d " + full(c.ST30_Q) + " · " + days(c.cover) + " of stock" + (g ? " · " + g.label : "") : "none",
                  text: has ? (c.SQ <= 0 ? "0" : c.cover === null ? "∞" : String(Math.round(c.cover))) : "" });
              })));
          });
          cxs = [el("div", { style: { overflow: "auto" } }, el("table", { "class": "ivd-heat" }, [el("thead", {}, head), el("tbody", {}, rowsEl)])),
            el("div", { "class": "tiny muted", style: { marginTop: "4px" }, text: "0 = sold in 30 days but nothing on the shelf · ∞ = on the shelf but no sale in 30 days." })];
        }
        out.push(grid12([card("c12", "Colour × size - days of stock", "Each cell is a colour family in one size, coloured by status; number = days its shelf lasts · " + selLabel, cxs, { right: [groupLegend()] })]));

        // store finder
        var seg = el("div", { "class": "seg", role: "group", "aria-label": "Short or too much" }, [["short", "Short · " + nShort + " stores"], ["excess", "Too much · " + nExcess + " stores"]].map(function (x) {
          return el("button", { type: "button", "aria-pressed": String(fv === x[0]), text: x[1], onclick: function () { st.finderView = x[0]; drawBody(); } });
        }));
        function stat(ac, k, v, sub) { return el("div", { style: "--ac:" + ac }, [el("div", { "class": "k", text: k }), el("div", { "class": "v", text: v }), el("div", { "class": "s", text: sub })]); }
        function cellStyle(c) {
          if (c.OOS_N) return ["#d23b3a", "0"]; if (c.SHORT_N) return ["#eda100", full(c.SQ)];
          if (c.EXCESS_N) return [c.SQ30 > 0 ? "#3f7fd0" : "#1f4f9a", full(c.SQ)]; return ["#12a36f", full(c.SQ)];
        }
        var fHead = el("tr", {}, [el("th", { style: { textAlign: "left" }, text: "Store" }), el("th", { style: { textAlign: "right" }, title: "Shelf pieces", text: "Shelf" }),
          el("th", { style: { textAlign: "right" }, title: "Pieces sold in 30 days", text: "Sold 30d" }), el("th", { style: { textAlign: "right" }, text: fv === "short" ? "At risk / day" : "Excess ₹" })]
          .concat(fSizes.map(function (sz) { return el("th", { text: sz }); })));
        var fBody = fTop.map(function (r) {
          return el("tr", {}, [el("td", { "class": "lbl" }, el("button", { type: "button", "class": "lnk", title: "Filter on this store", onclick: function () { toggleF("ST_CD", r.ST_CD); } }, [el("b", { text: r.ST_CD }), el("span", { "class": "muted", text: r.ST_NM || "" })])),
            el("td", { style: { textAlign: "right" }, text: full(r.SQ) }), el("td", { style: { textAlign: "right" }, text: full(r.SQ30) }),
            el("td", { style: { textAlign: "right", fontWeight: 600 }, text: fv === "short" ? rs(r.LOST_V) : rs(r.EXV) })]
            .concat(fSizes.map(function (sz) {
              var c = cellMap[r.ST_CD + "|" + sz]; if (!c) return el("td", { "class": "e", title: "Not stocked, not sold" });
              var cs = cellStyle(c);
              return el("td", { style: { background: cs[0], color: "#fff" }, text: cs[1],
                title: r.ST_CD + " · size " + sz + ": shelf " + full(c.SQ) + " · sold 30d " + full(c.SQ30) + (c.OOS_N ? " · OUT OF STOCK" : c.SHORT_N ? " · short" : c.EXCESS_N ? " · excess " + full(c.EXQ) + " pcs" : " · ok") });
            })));
        });
        if (!fBody.length) fBody = [el("tr", {}, el("td", { colspan: 4 + fSizes.length, "class": "muted", style: { padding: "24px", textAlign: "center" }, text: "No store " + (fv === "short" ? "is short" : "has excess") + " under these filters." }))];
        out.push(grid12([card("c12", "Which stores are short, which have too much - " + selLabel,
          "Per store and size: SHORT = sold in 30 days and under 15 days of shelf (red = none at all); EXCESS = more than " + target + " days of shelf, or shelf with no sale", [
            el("div", { "class": "ivd-stat" }, [stat("#eda100", "Stores short in a size", full(nShort), "of " + full(fStores.length) + " stores stocking / selling it"),
              stat("#d23b3a", "Sale at risk / day", rs(lostSum), "sizes that sold but have no shelf stock"),
              stat("#3f7fd0", "Stores with too much", full(nExcess), "beyond " + target + " days in at least one size"),
              stat("#1f4f9a", "Excess value", rs(exSum), "shelf above " + target + " days of sale")]),
            mjPicked ? null : el("div", { "class": "small", style: { color: "var(--warn)", marginBottom: "8px" }, text: "Tip: this is most useful for one category (and colour) - pick them above." }),
            el("div", { style: { overflow: "auto", maxHeight: "34rem" } }, el("table", { "class": "ivd-heat", style: { width: "100%" } }, [el("thead", {}, fHead), el("tbody", {}, fBody)])),
            el("div", { "class": "row", style: { marginTop: "8px" } }, [legendOf([{ label: "Out of stock (sold, none on shelf)", color: "#d23b3a" }, { label: "Short (< 15 days)", color: "#eda100" },
              { label: "OK", color: "#12a36f" }, { label: "Too much (> " + target + " days)", color: "#3f7fd0" }, { label: "On shelf, no sale", color: "#1f4f9a" }]),
              el("span", { "class": "tiny muted", text: "Number in a cell = shelf pieces · top 30 stores, the most " + (fv === "short" ? "sale at risk" : "excess value") + " first" })])],
          { right: [seg] })]));

        // top shades
        var shadeChart = shades.length ? M.charts.bars(shades.map(function (r) { return { label: r.CLR, value: r.SV, color: famColor(r.CLR_FAM || famOfShade(r.CLR)), r: r }; }),
          { horizontal: true, format: short, valueName: "Shelf", title: "Top shades", onClick: function (x) { toggleF("CLR", x.r.CLR); } }) : empty();
        out.push(grid12([card("c12", "Top shades", "The 15 colour shades with the most store shelf - click to filter", [shadeChart], {
          table: function () { return sortTable(shades, [{ k: "CLR", h: "Shade" }, { k: "SV", h: "Shelf ₹", num: 1, f: function (r) { return short(r.SV); } },
            { k: "S30_V", h: "Sale 30d ₹", num: 1, f: function (r) { return short(r.S30_V); } },
            { k: "cover", h: "Days of stock", num: 1, f: function (r) { return days(r.cover); }, tone: function (r) { return coverTone(r.cover, target); } },
            { k: "oos", h: "Out of stock", num: 1, f: function (r) { return pct(r.oos); } }, { k: "deadPct", h: "Not selling", num: 1, f: function (r) { return pct(r.deadPct, 0); } }],
            { initial: { k: "SV", dir: -1 }, onRow: function (r) { toggleF("CLR", r.CLR); } }); } })]));
        return out;
      }

      // ── go ──────────────────────────────────────────────────────────────────────────────────────────
      tabBtns.forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-k") === st.tab)); });
      paintGrain(); paintBuildInfo();
      return loadMeta(null);
    }
  });
})();

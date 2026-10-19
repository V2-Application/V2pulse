// MIS charts: small dependency-free SVG charts (CSP-safe), themed by the CSS variables. Each returns an element.
//   MIS.charts.bars(rows, {label, value, horizontal, format, height, color, onClick})       rows: [{label, value}]
//   MIS.charts.lines(series, {labels, format, height})                                       series: [{name, values, color}]
//   MIS.charts.donut(rows, {format, size, onClick})                                          rows: [{label, value}]
(function () {
  "use strict";
  var M = window.MIS, NS = "http://www.w3.org/2000/svg";
  var COLORS = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--s6)", "var(--s7)", "var(--s8)"];
  function s(tag, attrs, kids) {
    var e = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { if (attrs[k] != null) e.setAttribute(k, attrs[k]); });
    (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  var tip = null;
  function showTip(e, html) {
    if (!tip) { tip = M.el("div", { "class": "tip", role: "tooltip" }); document.body.appendChild(tip); }
    tip.innerHTML = html; tip.style.display = "block";
    var x = e.clientX + 14, y = e.clientY + 14, w = tip.offsetWidth, h = tip.offsetHeight;
    if (x + w > window.innerWidth - 8) x = e.clientX - w - 14; if (y + h > window.innerHeight - 8) y = e.clientY - h - 14;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  function hideTip() { if (tip) tip.style.display = "none"; }
  function nice(max) { if (max <= 0) return 1; var p = Math.pow(10, Math.floor(Math.log10(max))), f = max / p; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p; }
  function fmtv(v, f) { return f ? f(v) : M.compact(v); }

  M.charts = {
    colors: COLORS,
    bars: function (rows, o) {
      o = o || {}; var W = 640, horizontal = !!o.horizontal, n = rows.length || 1;
      var H = o.height || (horizontal ? Math.max(120, n * 26 + 30) : 260), pad = horizontal ? { l: Math.min(200, 8 + 7 * Math.max.apply(null, rows.map(function (r) { return String(r.label).length; }).concat([4]))), r: 60, t: 8, b: 20 } : { l: 52, r: 10, t: 14, b: 48 };
      var max = nice(Math.max.apply(null, rows.map(function (r) { return Math.abs(+r.value || 0); }).concat([0])));
      var svg = s("svg", { "class": "chart", viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": o.title || "Bar chart" });
      var iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
      for (var g = 0; g <= 4; g++) {
        var gv = max * g / 4;
        if (horizontal) { var gx = pad.l + iw * g / 4; svg.appendChild(s("line", { "class": "gl", x1: gx, x2: gx, y1: pad.t, y2: H - pad.b })); svg.appendChild(s("text", { x: gx, y: H - 4, "text-anchor": "middle" }, [fmtv(gv, o.format)])); }
        else { var gy = pad.t + ih - ih * g / 4; svg.appendChild(s("line", { "class": "gl", x1: pad.l, x2: W - pad.r, y1: gy, y2: gy })); svg.appendChild(s("text", { x: pad.l - 6, y: gy + 4, "text-anchor": "end" }, [fmtv(gv, o.format)])); }
      }
      rows.forEach(function (r, i) {
        var v = +r.value || 0, color = r.color || o.color || COLORS[0], rect;
        if (horizontal) {
          var bh = Math.min(20, ih / n * 0.72), y = pad.t + ih / n * i + (ih / n - bh) / 2, w = iw * Math.abs(v) / max;
          rect = s("rect", { x: pad.l, y: y, width: Math.max(1, w), height: bh, rx: 3, fill: color });
          svg.appendChild(s("text", { x: pad.l - 6, y: y + bh / 2 + 4, "text-anchor": "end" }, [String(r.label).slice(0, 28)]));
          svg.appendChild(s("text", { "class": "val", x: pad.l + w + 5, y: y + bh / 2 + 4 }, [fmtv(v, o.format)]));
        } else {
          var bw = Math.min(46, iw / n * 0.7), x = pad.l + iw / n * i + (iw / n - bw) / 2, h = ih * Math.abs(v) / max;
          rect = s("rect", { x: x, y: pad.t + ih - h, width: bw, height: Math.max(1, h), rx: 3, fill: color });
          var lt = s("text", { x: x + bw / 2, y: H - pad.b + 14, "text-anchor": n > 12 ? "end" : "middle", transform: n > 12 ? "rotate(-35 " + (x + bw / 2) + " " + (H - pad.b + 14) + ")" : null }, [String(r.label).slice(0, 16)]);
          svg.appendChild(lt);
        }
        rect.style.cursor = o.onClick ? "pointer" : "default";
        rect.addEventListener("mousemove", function (e) { showTip(e, '<div class="h">' + M.esc(r.label) + '</div><div class="r"><span>' + M.esc(o.valueName || "Value") + '</span><b>' + M.esc(fmtv(v, o.format)) + "</b></div>"); });
        rect.addEventListener("mouseleave", hideTip);
        if (o.onClick) rect.addEventListener("click", function () { o.onClick(r); });
        svg.appendChild(rect);
      });
      return svg;
    },
    lines: function (series, o) {
      o = o || {}; var W = 640, H = o.height || 240, pad = { l: 52, r: 12, t: 12, b: 36 }, labels = o.labels || [];
      var all = []; series.forEach(function (sr) { sr.values.forEach(function (v) { if (v != null) all.push(+v); }); });
      var max = nice(Math.max.apply(null, all.concat([0]))), min = Math.min(0, Math.min.apply(null, all.concat([0])));
      var svg = s("svg", { "class": "chart", viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": o.title || "Line chart" }), iw = W - pad.l - pad.r, ih = H - pad.t - pad.b, n = Math.max(1, labels.length - 1);
      function X(i) { return pad.l + iw * i / n; } function Y(v) { return pad.t + ih - ih * (v - min) / (max - min || 1); }
      for (var g = 0; g <= 4; g++) { var gv = min + (max - min) * g / 4; svg.appendChild(s("line", { "class": "gl", x1: pad.l, x2: W - pad.r, y1: Y(gv), y2: Y(gv) })); svg.appendChild(s("text", { x: pad.l - 6, y: Y(gv) + 4, "text-anchor": "end" }, [fmtv(gv, o.format)])); }
      var step = Math.ceil(labels.length / 10);
      labels.forEach(function (l, i) { if (i % step === 0 || i === labels.length - 1) svg.appendChild(s("text", { x: X(i), y: H - pad.b + 16, "text-anchor": "middle" }, [String(l).slice(5)])); });
      series.forEach(function (sr, k) {
        var d = "", color = sr.color || COLORS[k % COLORS.length];
        sr.values.forEach(function (v, i) { if (v == null) return; d += (d ? "L" : "M") + X(i).toFixed(1) + " " + Y(+v).toFixed(1); });
        svg.appendChild(s("path", { d: d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round" }));
      });
      var hover = s("rect", { x: pad.l, y: pad.t, width: iw, height: ih, fill: "transparent" });
      hover.addEventListener("mousemove", function (e) {
        var b = svg.getBoundingClientRect(), i = Math.round(((e.clientX - b.left) / b.width * W - pad.l) / iw * n); i = Math.max(0, Math.min(labels.length - 1, i));
        showTip(e, '<div class="h">' + M.esc(labels[i]) + "</div>" + series.map(function (sr) { return '<div class="r"><span>' + M.esc(sr.name) + "</span><b>" + M.esc(fmtv(sr.values[i], o.format)) + "</b></div>"; }).join(""));
      });
      hover.addEventListener("mouseleave", hideTip); svg.appendChild(hover);
      return svg;
    },
    donut: function (rows, o) {
      o = o || {}; var size = o.size || 200, r = size / 2 - 6, inner = r * 0.6, cx = size / 2, cy = size / 2;
      var total = rows.reduce(function (a, x) { return a + Math.max(0, +x.value || 0); }, 0) || 1, a0 = -Math.PI / 2;
      var svg = s("svg", { "class": "chart", viewBox: "0 0 " + size + " " + size, width: size, height: size, role: "img", "aria-label": o.title || "Donut chart" });
      rows.forEach(function (x, i) {
        var v = Math.max(0, +x.value || 0), a1 = a0 + 2 * Math.PI * v / total, large = a1 - a0 > Math.PI ? 1 : 0, color = x.color || COLORS[i % COLORS.length];
        if (v / total > 0.9999) a1 = a0 + 2 * Math.PI - 0.0001;
        var d = "M" + (cx + r * Math.cos(a0)) + " " + (cy + r * Math.sin(a0)) + "A" + r + " " + r + " 0 " + large + " 1 " + (cx + r * Math.cos(a1)) + " " + (cy + r * Math.sin(a1)) +
                "L" + (cx + inner * Math.cos(a1)) + " " + (cy + inner * Math.sin(a1)) + "A" + inner + " " + inner + " 0 " + large + " 0 " + (cx + inner * Math.cos(a0)) + " " + (cy + inner * Math.sin(a0)) + "Z";
        var p = s("path", { d: d, fill: color });
        p.addEventListener("mousemove", function (e) { showTip(e, '<div class="h">' + M.esc(x.label) + '</div><div class="r"><span>Value</span><b>' + M.esc(fmtv(v, o.format)) + '</b></div><div class="r"><span>Share</span><b>' + (100 * v / total).toFixed(1) + "%</b></div>"); });
        p.addEventListener("mouseleave", hideTip); if (o.onClick) { p.style.cursor = "pointer"; p.addEventListener("click", function () { o.onClick(x); }); }
        svg.appendChild(p); a0 = a1;
      });
      svg.appendChild(s("text", { x: cx, y: cy + 5, "text-anchor": "middle", "class": "val", style: "font-size:15px" }, [fmtv(o.total != null ? o.total : total, o.format)]));
      return svg;
    },
    legend: function (rows) {
      return M.el("div", { "class": "legend" }, rows.map(function (x, i) { return M.el("span", {}, [M.el("i", { style: { background: x.color || COLORS[i % COLORS.length] } }), x.label]); }));
    }
  };
})();

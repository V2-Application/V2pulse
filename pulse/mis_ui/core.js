// V2Pulse MIS workspace - core: helpers, API, the app shell (sidebar with the 19 DFS MIS tabs + masthead), a small
// router that loads one page module per tab (/mis/static/pages/<id>.js), and the shared widgets.
// Plain JS, no build step (CSP script-src 'self'). Pages register with MIS.page(id, {render(ctx)}).
(function () {
  "use strict";
  var MIS = window.MIS = { pages: {}, cache: {} };

  // ── DOM helpers ───────────────────────────────────────────────────────────────────────────────
  var el = MIS.el = function (tag, attrs, kids) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "text") e.textContent = v;
      else if (k === "html") e.innerHTML = v;
      else if (k === "class") e.className = v;
      else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
      else if (k.slice(0, 2) === "on" && typeof v === "function") e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    });
    [].concat(kids === undefined ? [] : kids).forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      e.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
    });
    return e;
  };
  MIS.$ = function (s, r) { return (r || document).querySelector(s); };
  MIS.clear = function (n) { while (n && n.firstChild) n.removeChild(n.firstChild); return n; };
  MIS.qs = function (k) { return new URLSearchParams(location.search).get(k); };
  MIS.debounce = function (fn, ms) { var t; return function () { var a = arguments, s = this; clearTimeout(t); t = setTimeout(function () { fn.apply(s, a); }, ms || 250); }; };
  MIS.esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };

  // ── icons (inline SVG, 24px grid) ──────────────────────────────────────────────────────────────
  var P = {
    report: "M4 4h16v16H4z M8 9h8 M8 13h8 M8 17h5", trend: "M3 17l6-6 4 4 8-8 M15 7h6v6", stock: "M3 7l9-4 9 4-9 4z M3 12l9 4 9-4 M3 17l9 4 9-4",
    day: "M4 5h16v15H4z M4 9h16 M8 3v4 M16 3v4", box: "M3 8l9-5 9 5v8l-9 5-9-5z M3 8l9 5 9-5 M12 13v8", bin: "M4 4h16v6H4z M4 14h16v6H4z M8 7h2 M8 17h2",
    alloc: "M12 3v6 M5 21v-6h14v6 M12 9l-7 6 M12 9l7 6", gap: "M4 12h4 M16 12h4 M10 6v12 M14 6v12", plan: "M12 3a9 9 0 1 0 9 9 M12 3v9h9",
    tna: "M4 6h10 M4 12h16 M4 18h7 M17 4l3 3-3 3", scm: "M3 7h13v10H3z M16 10h3l2 3v4h-5 M7 19a2 2 0 1 0 0-.01 M17 19a2 2 0 1 0 0-.01",
    master: "M4 5h16v4H4z M4 11h16v4H4z M4 17h16v2H4z", store: "M4 10l2-6h12l2 6 M5 10v10h14V10 M9 20v-6h6v6", monitor: "M3 12h4l3-8 4 16 3-8h4",
    deps: "M6 6a2 2 0 1 0 0-.01 M18 6a2 2 0 1 0 0-.01 M12 18a2 2 0 1 0 0-.01 M6 8v3h12V8 M12 11v5", dash: "M4 13h7V4H4z M13 20h7v-9h-7z M13 4h7v5h-7z M4 20h7v-5H4z",
    variant: "M12 3l8 4.5v9L12 21l-8-4.5v-9z M12 12l8-4.5 M12 12v9 M12 12L4 7.5", clock: "M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18z M12 7v5l3 2",
    home: "M3 11l9-7 9 7 M5 10v10h14V10", menu: "M4 6h16 M4 12h16 M4 18h16", filter: "M4 5h16l-6 7v6l-4 2v-8z", cols: "M4 4h5v16H4z M10 4h4v16h-4z M15 4h5v16h-5z",
    down: "M12 4v12 M6 11l6 6 6-6 M4 20h16", save: "M5 4h11l3 3v13H5z M8 4v5h7 M8 20v-6h8v6", refresh: "M20 12a8 8 0 1 1-2.3-5.6 M20 4v5h-5", x: "M6 6l12 12 M18 6L6 18",
    search: "M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14z M21 21l-5-5", gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M19 12h2 M3 12h2 M12 3v2 M12 19v2 M5.6 5.6l1.4 1.4 M17 17l1.4 1.4 M5.6 18.4L7 17 M17 7l1.4-1.4",
    plus: "M12 5v14 M5 12h14", play: "M7 4l12 8-12 8z", upload: "M12 20V8 M6 13l6-6 6 6 M4 4h16", trash: "M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13",
    edit: "M4 20h4L20 8l-4-4L4 16z", copy: "M8 8h12v12H8z M4 16V4h12", density: "M4 6h16 M4 10h16 M4 14h16 M4 18h16", chev: "M9 6l6 6-6 6", info: "M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18z M12 11v6 M12 7.5v.5"
  };
  MIS.icon = function (name, size) {
    var s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor");
    s.setAttribute("stroke-width", "1.8"); s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round"); s.setAttribute("aria-hidden", "true");
    if (size) { s.setAttribute("width", size); s.setAttribute("height", size); }
    var p = document.createElementNS("http://www.w3.org/2000/svg", "path"); p.setAttribute("d", P[name] || P.report); s.appendChild(p);
    return s;
  };

  // ── formatting ─────────────────────────────────────────────────────────────────────────────────
  var nf0 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }), nf2 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
  MIS.isNum = function (v) { return typeof v === "number" || (typeof v === "string" && /^-?\d+(\.\d+)?(e[+-]?\d+)?$/i.test(v) && !/^-?0\d/.test(v)); };
  MIS.fmt = function (v, kind) {
    if (v === null || v === undefined || v === "") return "";
    if (kind === "text") return String(v);
    var n = Number(v);
    if (!isFinite(n)) return String(v);
    if (kind === "pct") return nf2.format(n) + "%";
    if (kind === "int") return nf0.format(n);
    if (kind === "dec") return nf2.format(n);
    return Math.abs(n) >= 100 || Number.isInteger(n) ? nf0.format(n) : nf2.format(n);
  };
  MIS.compact = function (v) {   // 12.3 Cr / 4.5 L / 12.3 K, Indian style
    var n = Number(v); if (!isFinite(n)) return v == null ? "" : String(v);
    var a = Math.abs(n), s = n < 0 ? "-" : "";
    if (a >= 1e7) return s + nf2.format(a / 1e7) + " Cr"; if (a >= 1e5) return s + nf2.format(a / 1e5) + " L";
    if (a >= 1e3) return s + nf2.format(a / 1e3) + " K"; return s + nf2.format(a);
  };
  MIS.label = function (c) { return String(c).replace(/__/g, " · ").replace(/_/g, " "); };
  MIS.today = function () { return new Date(Date.now() + 330 * 60000).toISOString().slice(0, 10); };
  MIS.addDays = function (iso, n) { var d = new Date(iso + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
  MIS.fmtDate = function (iso, withTime) {
    if (!iso) return ""; var d = new Date(iso); if (isNaN(d)) return String(iso);
    return d.toLocaleString("en-IN", withTime === false || String(iso).length <= 10 ? { day: "2-digit", month: "short", year: "numeric" }
      : { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
  };
  MIS.ago = function (iso) {
    if (!iso) return ""; var s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 60) return "just now"; if (s < 3600) return Math.round(s / 60) + " min ago"; if (s < 86400) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " d ago";
  };

  // ── API ────────────────────────────────────────────────────────────────────────────────────────
  function csrf() { var m = document.cookie.match(/(?:^|;\s*)pulse_csrf=([^;]+)/); return m ? decodeURIComponent(m[1]) : ""; }
  MIS.csrf = csrf;
  function errText(status, d) {
    if (status === 401) return "Your V2Pulse session has ended - sign in again and reload this page.";
    if (status === 403 && (!d || !d.detail)) return "You do not have access to this.";
    if (d) {
      if (typeof d.detail === "string") return d.detail;
      if (Array.isArray(d.detail)) return d.detail.map(function (x) { return x.msg || JSON.stringify(x); }).join("; ");
      if (d.title) return d.title; if (d.error) return d.error;
    }
    return "The request failed (error " + status + ").";
  }
  MIS.api = function (method, url, body, opts) {
    opts = opts || {};
    var o = { method: method, credentials: "same-origin", headers: { Accept: "application/json" }, signal: opts.signal };
    if (method !== "GET") o.headers["X-CSRF-Token"] = csrf();
    if (body instanceof FormData) o.body = body;
    else if (body !== undefined) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(body); }
    return fetch(url, o).then(function (r) {
      return r.text().then(function (t) {
        var d = null; try { d = t ? JSON.parse(t) : null; } catch (e) { d = null; }
        if (!r.ok) { var err = new Error(errText(r.status, d)); err.status = r.status; throw err; }
        return d;
      });
    });
  };
  MIS.get = function (url, opts) { return MIS.api("GET", url, undefined, opts); };
  MIS.post = function (url, body, opts) { return MIS.api("POST", url, body, opts); };
  MIS.download = function (method, url, body, fallback) {
    var o = { method: method, credentials: "same-origin", headers: {} };
    if (method !== "GET") o.headers["X-CSRF-Token"] = csrf();
    if (body !== undefined) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(body); }
    return fetch(url, o).then(function (r) {
      if (!r.ok) return r.text().then(function (t) { var d = null; try { d = JSON.parse(t); } catch (e) { /* text */ } throw new Error(errText(r.status, d)); });
      var cd = r.headers.get("Content-Disposition") || "", m = cd.match(/filename="?([^";]+)"?/), name = m ? m[1] : (fallback || "export");
      return r.blob().then(function (b) {
        var a = el("a", { href: URL.createObjectURL(b), download: name }); document.body.appendChild(a); a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 3000); return name;
      });
    });
  };
  // A button that runs an async action: busy state, toast on success / failure.
  MIS.action = function (btn, label, fn, okMsg) {
    var old = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<span class="spin"></span> ' + MIS.esc(label || "Working…");
    return Promise.resolve().then(fn).then(function (r) {
      btn.disabled = false; btn.innerHTML = old; if (okMsg) MIS.toast(typeof okMsg === "function" ? okMsg(r) : okMsg, "ok"); return r;
    }, function (e) { btn.disabled = false; btn.innerHTML = old; MIS.toast(e.message, "bad"); throw e; });
  };

  // ── toast / modal / popover / confirm ───────────────────────────────────────────────────────────
  MIS.toast = function (msg, kind) {
    var box = MIS.$(".toasts") || document.body.appendChild(el("div", { "class": "toasts", "aria-live": "polite" }));
    var t = el("div", { "class": "toast" + (kind ? " " + kind : ""), role: "status" }, [el("span", { text: msg })]);
    box.appendChild(t); setTimeout(function () { t.remove(); }, kind === "bad" ? 10000 : 4500);
  };
  MIS.modal = function (title, body, buttons, opts) {
    opts = opts || {};
    var bg = el("div", { "class": "modal-bg", role: "dialog", "aria-modal": "true", "aria-label": title });
    var close = el("button", { type: "button", "class": "btn ghost sm", "aria-label": "Close" }, [MIS.icon("x")]);
    var m = el("div", { "class": "modal" + (opts.wide ? " wide" : "") }, [
      el("div", { "class": "modal-h" }, [el("h2", { text: title }), el("span", { "class": "spacer" }), close]),
      el("div", { "class": "modal-b" }, body), buttons && buttons.length ? el("div", { "class": "modal-f" }, buttons) : null]);
    bg.appendChild(m);
    function done() { bg.remove(); document.removeEventListener("keydown", esc); if (opts.onClose) opts.onClose(); }
    function esc(e) { if (e.key === "Escape") done(); }
    close.addEventListener("click", done); document.addEventListener("keydown", esc);
    bg.addEventListener("mousedown", function (e) { if (e.target === bg) done(); });
    document.body.appendChild(bg);
    var f = m.querySelector(".modal-b input, .modal-b select, .modal-b textarea"); if (f) f.focus();
    return { close: done, el: m };
  };
  MIS.confirm = function (title, text, okLabel, danger) {
    return new Promise(function (res) {
      var ok = el("button", { type: "button", "class": "btn " + (danger ? "danger" : "primary"), text: okLabel || "OK" });
      var cancel = el("button", { type: "button", "class": "btn", text: "Cancel" });
      var m = MIS.modal(title, [el("p", { style: "margin:0", text: text })], [cancel, ok], { onClose: function () { res(false); } });
      ok.addEventListener("click", function () { res(true); m.close(); }); cancel.addEventListener("click", function () { m.close(); });
    });
  };
  var openPop = null;
  MIS.closePop = function () { if (openPop) { openPop.remove(); openPop = null; } };
  MIS.popover = function (anchor, content, opts) {
    MIS.closePop(); opts = opts || {};
    var p = el("div", { "class": "pop", role: "dialog" }, content); document.body.appendChild(p);
    var r = anchor.getBoundingClientRect(), w = p.offsetWidth, h = p.offsetHeight;
    var left = Math.min(Math.max(8, opts.alignRight ? r.right - w : r.left), window.innerWidth - w - 8);
    var top = r.bottom + 6; if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 6);
    p.style.left = left + "px"; p.style.top = top + "px"; openPop = p;
    setTimeout(function () {
      function out(e) { if (!p.contains(e.target) && e.target !== anchor && !anchor.contains(e.target)) { document.removeEventListener("mousedown", out); if (openPop === p) MIS.closePop(); } }
      document.addEventListener("mousedown", out);
    });
    return p;
  };
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") MIS.closePop(); });
  MIS.menu = function (anchor, items, opts) {
    var m = el("div", { "class": "menu" });
    items.forEach(function (it) {
      if (it === "-") { m.appendChild(el("hr")); return; }
      var b = el("button", { type: "button", "class": it.on ? "on" : null }, [it.icon ? MIS.icon(it.icon, 15) : null, el("span", { text: it.label })]);
      b.addEventListener("click", function () { MIS.closePop(); it.fn(); }); m.appendChild(b);
    });
    return MIS.popover(anchor, [m], opts);
  };

  // ── widgets ────────────────────────────────────────────────────────────────────────────────────
  // Checklist of values in groups, with search / all / none. groups: [{label, items:[v | {value,label,count}]}]
  MIS.checklist = function (groups, selected, opts) {
    opts = opts || {}; var chosen = {}; (selected || []).forEach(function (v) { chosen[v] = true; });
    var q = el("input", { type: "search", placeholder: opts.placeholder || "Search", "aria-label": "Search" });
    var list = el("div", { "class": "checks", style: opts.height ? { maxHeight: opts.height } : null }), cnt = el("span", { "class": "muted tiny" });
    function norm(i) { return i !== null && typeof i === "object" ? i : { value: i, label: i }; }
    function match(i, s) { return !s || String(i.label).toLowerCase().indexOf(s) >= 0 || String(i.value).toLowerCase().indexOf(s) >= 0; }
    function visible() { var s = q.value.trim().toLowerCase(), o = []; groups.forEach(function (g) { g.items.map(norm).forEach(function (i) { if (match(i, s)) o.push(i.value); }); }); return o; }
    function fire() { cnt.textContent = Object.keys(chosen).length + " selected"; if (opts.onChange) opts.onChange(api.get()); }
    function draw() {
      MIS.clear(list); var s = q.value.trim().toLowerCase(), n = 0;
      groups.forEach(function (g) {
        var items = g.items.map(norm).filter(function (i) { return match(i, s); }); if (!items.length) return;
        if (g.label) list.appendChild(el("div", { "class": "grp", text: g.label }));
        items.slice(0, opts.max || 1000).forEach(function (i) {
          n++; var cb = el("input", { type: opts.single ? "radio" : "checkbox", name: opts.single ? "rad" + Math.random() : null, checked: !!chosen[i.value] });
          cb.addEventListener("change", function () { if (opts.single) chosen = {}; if (cb.checked) chosen[i.value] = true; else delete chosen[i.value]; fire(); });
          list.appendChild(el("label", {}, [cb, el("span", { text: i.label === i.value && opts.prettify !== false ? MIS.label(i.label) : String(i.label) }),
            i.count != null ? el("span", { "class": "cnt", text: MIS.fmt(i.count) }) : null]));
        });
      });
      if (!n) list.appendChild(el("div", { "class": "muted small", style: "padding:8px", text: "Nothing matches." }));
      cnt.textContent = Object.keys(chosen).length + " selected";
    }
    q.addEventListener("input", draw);
    var head = el("div", { "class": "row", style: "margin-bottom:6px" }, [q,
      opts.single ? null : el("button", { type: "button", "class": "btn ghost sm", text: "All", onclick: function () { visible().forEach(function (v) { chosen[v] = true; }); draw(); fire(); } }),
      el("button", { type: "button", "class": "btn ghost sm", text: "None", onclick: function () { visible().forEach(function (v) { delete chosen[v]; }); draw(); fire(); } }), cnt]);
    var api = { el: el("div", {}, [head, list]),
      get: function () { var o = []; groups.forEach(function (g) { g.items.map(norm).forEach(function (i) { if (chosen[i.value] && o.indexOf(i.value) < 0) o.push(i.value); }); });
                         Object.keys(chosen).forEach(function (k) { if (o.indexOf(k) < 0) o.push(k); }); return o; },
      set: function (v) { chosen = {}; (v || []).forEach(function (x) { chosen[x] = true; }); draw(); },
      setGroups: function (g) { groups = g; draw(); } };
    draw(); return api;
  };

  // A dropdown button that opens a checklist (multi-select filter). load() -> Promise<items> (lazy).
  MIS.multiSelect = function (label, load, selected, onChange, opts) {
    opts = opts || {}; var vals = (selected || []).slice(), items = null;
    var b = el("button", { type: "button", "class": "btn dd-btn" }), wrap = el("label", { "class": "f" }, [label, b]);
    function paint() { MIS.clear(b); b.appendChild(el("span", { "class": "t", text: vals.length ? (vals.length === 1 ? String(vals[0]) : vals.length + " selected") : (opts.allText || "All") }));
      b.appendChild(vals.length ? el("span", { "class": "cnt", text: vals.length }) : MIS.icon("chev", 13)); }
    b.addEventListener("click", function () {
      var body = el("div", { "class": "pop-b" }, [el("div", { "class": "muted small", html: '<span class="spin"></span> Loading values…' })]);
      var ok = el("button", { type: "button", "class": "btn primary sm", text: "Apply" }), clr = el("button", { type: "button", "class": "btn sm", text: "Clear" });
      MIS.popover(b, [el("div", { "class": "pop-h" }, [el("b", { text: label })]), body, el("div", { "class": "pop-f" }, [clr, ok])]);
      var cl = null;
      Promise.resolve(items || load()).then(function (it) {
        items = it; MIS.clear(body); cl = MIS.checklist([{ items: it }], vals, { height: "300px", single: opts.single, prettify: false }); body.appendChild(cl.el);
      }, function (e) { MIS.clear(body).appendChild(el("div", { "class": "notice bad", text: e.message })); });
      ok.addEventListener("click", function () { if (cl) vals = cl.get(); MIS.closePop(); paint(); onChange(vals.slice()); });
      clr.addEventListener("click", function () { vals = []; MIS.closePop(); paint(); onChange([]); });
    });
    paint();
    return { el: wrap, get: function () { return vals.slice(); }, set: function (v) { vals = (v || []).slice(); paint(); }, reset: function () { items = null; } };
  };

  // Excel-style conditions [{col, op, v1, v2, vals}] (DFS lib/misConds.js operators)
  var OPS = [["eq", "="], ["ne", "≠"], ["gt", ">"], ["gte", "≥"], ["lt", "<"], ["lte", "≤"], ["between", "between"], ["in", "is any of"], ["not_in", "is none of"],
             ["contains", "contains"], ["not_contains", "does not contain"], ["starts", "starts with"], ["ends", "ends with"], ["blank", "is blank"], ["nonblank", "is not blank"]];
  MIS.OPS = OPS;
  MIS.condLabel = function (c) {
    var op = (OPS.filter(function (o) { return o[0] === c.op; })[0] || [c.op, c.op])[1];
    var v = c.vals ? c.vals.join(", ") : (c.v1 == null ? "" : c.v1) + (c.op === "between" ? " and " + c.v2 : "");
    return MIS.label(c.col) + " " + op + (c.op === "blank" || c.op === "nonblank" ? "" : " " + v);
  };
  MIS.condEditor = function (columns, initial, onChange) {
    var rows = JSON.parse(JSON.stringify(initial || [])), box = el("div", { "class": "stack" }), list = el("div", { "class": "stack" });
    box.appendChild(list);
    box.appendChild(el("div", {}, el("button", { type: "button", "class": "btn sm", onclick: function () { rows.push({ col: columns[0] || "", op: "gt", v1: "" }); draw(); } }, [MIS.icon("plus", 14), "Add condition"])));
    function fire() { if (onChange) onChange(api.get()); }
    function draw() {
      MIS.clear(list);
      rows.forEach(function (r, i) {
        var col = el("select", { "aria-label": "Column" }, columns.map(function (c) { return el("option", { value: c, selected: c === r.col, text: MIS.label(c) }); }));
        if (r.col && columns.indexOf(r.col) < 0) col.appendChild(el("option", { value: r.col, selected: true, text: r.col }));
        var op = el("select", { "aria-label": "Operator" }, OPS.map(function (o) { return el("option", { value: o[0], selected: o[0] === r.op, text: o[1] }); }));
        var v1 = el("input", { type: "text", value: r.vals ? r.vals.join(", ") : (r.v1 == null ? "" : r.v1), placeholder: "value", "aria-label": "Value", style: "width:150px" });
        var v2 = el("input", { type: "text", value: r.v2 || "", placeholder: "and", "aria-label": "Second value", style: "width:100px" });
        function vis() { v2.classList.toggle("hidden", op.value !== "between"); v1.classList.toggle("hidden", op.value === "blank" || op.value === "nonblank"); }
        function sync() { r.col = col.value; r.op = op.value;
          if (r.op === "in" || r.op === "not_in") { r.vals = v1.value.split(",").map(function (s) { return s.trim(); }).filter(Boolean); delete r.v1; } else { r.v1 = v1.value; delete r.vals; }
          r.v2 = v2.value; vis(); fire(); }
        [col, op, v1, v2].forEach(function (x) { x.addEventListener("change", sync); }); vis();
        list.appendChild(el("div", { "class": "row" }, [col, op, v1, v2, el("button", { type: "button", "class": "btn ghost sm", "aria-label": "Remove condition", onclick: function () { rows.splice(i, 1); draw(); fire(); } }, [MIS.icon("x", 14)])]));
      });
    }
    var api = { el: box, get: function () { return rows.filter(function (r) { return r.col && r.op; }); }, set: function (v) { rows = JSON.parse(JSON.stringify(v || [])); draw(); },
                setColumns: function (c) { columns = c; draw(); } };
    draw(); return api;
  };

  // Date range control with presets. onChange({from, to}).
  MIS.dateRange = function (from, to, onChange, opts) {
    opts = opts || {};
    var f = el("input", { type: "date", value: from, "aria-label": "From" }), t = el("input", { type: "date", value: to, "aria-label": "To" });
    var y = MIS.addDays(MIS.today(), -1);
    var presets = [["Yesterday", y, y], ["Last 7 days", MIS.addDays(y, -6), y], ["MTD", y.slice(0, 8) + "01", y], ["Last 30 days", MIS.addDays(y, -29), y]];
    var pre = el("select", { "aria-label": "Date preset" }, [el("option", { value: "", text: "Custom" })].concat(presets.map(function (p, i) { return el("option", { value: i, text: p[0] }); })));
    pre.addEventListener("change", function () { if (pre.value === "") return; var p = presets[+pre.value]; f.value = p[1]; t.value = p[2]; fire(); });
    function fire() { onChange({ from: f.value, to: t.value }); }
    f.addEventListener("change", function () { pre.value = ""; fire(); }); t.addEventListener("change", function () { pre.value = ""; fire(); });
    return { el: el("div", { "class": "row", style: "gap:6px" }, [el("label", { "class": "f" }, [opts.label || "Period", el("div", { "class": "row", style: "gap:6px" }, [pre, f, el("span", { "class": "muted", text: "→" }), t])])]),
             get: function () { return { from: f.value, to: t.value }; }, set: function (a, b) { f.value = a; t.value = b; } };
  };

  // Range-health strip: is the date range fully loaded? (MIS Load Monitor)
  MIS.rangeHealth = function (box, from, to) {
    MIS.clear(box); if (!from || !to) return;
    MIS.get("/api/v1/mis/monitor/range-health?from=" + from + "&to=" + to).then(function (h) {
      MIS.clear(box); if (!h || !h.level || String(h.level).toLowerCase() === "ok") return;
      box.appendChild(el("div", { "class": "notice " + (h.level === "error" ? "bad" : "warn") }, [MIS.icon("info", 16), el("span", { text: h.headline + " " }),
        el("a", { href: "/mis/monitor?from=" + from + "&to=" + to, "data-nav": "1", text: "Open the Load Monitor" })]));
    }, function () { /* say nothing */ });
  };

  // Saved views (column layout / filters / sort) per user and page, in V2Pulse Postgres.
  MIS.views = {
    list: function (page) { return MIS.get("/api/v1/mis/views/" + encodeURIComponent(page)).then(function (r) { return r.data || []; }); },
    save: function (page, name, state, isDefault) { return MIS.post("/api/v1/mis/views/" + encodeURIComponent(page), { name: name, state: state, is_default: !!isDefault }); },
    remove: function (page, id) { return MIS.api("DELETE", "/api/v1/mis/views/" + encodeURIComponent(page) + "/" + id); }
  };

  // KPI tiles in the masthead. tiles: [{k, v, s, cls}]
  MIS.tiles = function (box, tiles) {
    MIS.clear(box); box.classList.toggle("hidden", !tiles || !tiles.length);
    (tiles || []).forEach(function (t) {
      box.appendChild(el("div", { "class": "tile" + (t.skel ? " skel" : ""), title: t.title || null }, [el("div", { "class": "k" }, [t.dot ? el("span", { "class": "dot", style: { background: t.dot, width: "8px", height: "8px", borderRadius: "50%", display: "inline-block" } }) : null, t.k]),
        el("div", { "class": "v" + (t.v != null && String(t.v).length > 12 ? " long" : ""), text: t.skel ? "—" : (t.v == null ? "–" : String(t.v)) }), t.s ? el("div", { "class": "s " + (t.cls || ""), text: t.s }) : null]));
    });
  };

  // ── the 19 DFS MIS tabs (same names and order as dfs.v2retail.net) ────────────────────────────────
  MIS.TABS = [
    { g: "Reports", id: "reports", path: "/mis", label: "Reports", icon: "report" },
    { g: "Reports", id: "day-trend-reports", path: "/mis/day-trend-reports", label: "Day Wise Trend Reports", icon: "day" },
    { g: "Reports", id: "inv-reports", path: "/mis/inv-reports", label: "Inventory Reports", icon: "box" },
    { g: "Trends", id: "sale-trend", path: "/mis/sale-trend", label: "Sale Trend", icon: "trend" },
    { g: "Trends", id: "stock-trend", path: "/mis/stock-trend", label: "Stock Trend", icon: "stock" },
    { g: "Trends", id: "day-trend", path: "/mis/day-trend", label: "Day Wise Trend", icon: "day" },
    { g: "Inventory", id: "vert-inv", path: "/mis/vert-inv", label: "Vertical Inventory (Article)", icon: "box" },
    { g: "Inventory", id: "inv-dashboard", path: "/mis/inv-dashboard", label: "Inventory Dashboard", icon: "dash" },
    { g: "Inventory", id: "bin-wise", path: "/mis/bin-wise", label: "Bin Wise", icon: "bin" },
    { g: "Inventory", id: "variant-data", path: "/mis/variant-data", label: "Variant Article Data", icon: "variant" },
    { g: "Allocation & supply", id: "allocation", path: "/mis/allocation", label: "Allocation", icon: "alloc" },
    { g: "Allocation & supply", id: "alloc-gaps", path: "/mis/alloc-gaps", label: "Allocation Gaps", icon: "gap" },
    { g: "Allocation & supply", id: "plan-360", path: "/mis/plan-360", label: "Plan Live 360", icon: "plan" },
    { g: "Allocation & supply", id: "tna", path: "/mis/tna", label: "TNA Report", icon: "tna" },
    { g: "Allocation & supply", id: "scm", path: "/mis/scm", label: "SCM Report", icon: "scm" },
    { g: "Masters", id: "product-master", path: "/mis/product-master", label: "Product Master", icon: "master" },
    { g: "Masters", id: "store-master", path: "/mis/store-master", label: "Store Master", icon: "store" },
    { g: "Operations", id: "monitor", path: "/mis/monitor", label: "Monitor", icon: "monitor" },
    { g: "Operations", id: "lineage", path: "/mis/lineage", label: "Dependencies", icon: "deps" },
    { g: "Operations", id: "dumps", path: "/mis/dumps", label: "Dump Scheduler", icon: "clock", perm: "dumps" }
  ];
  // path -> page id (+ params). Sub-pages: /mis/r/<slug>, /mis/inv-reports/r/<slug>, /mis/day-trend-reports/r/<slug>.
  MIS.route = function (path) {
    var p = path.replace(/\/+$/, "") || "/mis", m;
    if ((m = p.match(/^\/mis\/r\/([^/]+)$/))) return { id: "report", slug: decodeURIComponent(m[1]), tab: "reports" };
    if ((m = p.match(/^\/mis\/inv-reports\/r\/([^/]+)$/))) return { id: "inv-report", slug: decodeURIComponent(m[1]), tab: "inv-reports" };
    if ((m = p.match(/^\/mis\/day-trend-reports\/r\/([^/]+)$/))) return { id: "day-trend", slug: decodeURIComponent(m[1]), tab: "day-trend-reports" };
    if (p === "/mis/trend") return { id: MIS.qs("kind") === "stock" ? "stock-trend" : "sale-trend", tab: MIS.qs("kind") === "stock" ? "stock-trend" : "sale-trend" };
    var t = MIS.TABS.filter(function (x) { return x.path === p; })[0];
    return t ? { id: t.id, tab: t.id } : { id: "not-found", tab: null };
  };
  // Which page module file a page id lives in (several ids share a module).
  var MODULE = { "report": "reports", "sale-trend": "trend", "stock-trend": "trend", "inv-report": "inv-reports", "variant-data": "variant", "day-trend-reports": "day-trend-reports" };

  // ── shell ──────────────────────────────────────────────────────────────────────────────────────
  var S = {};
  function buildShell() {
    var app = el("div", { "class": "app" + (localStorageGet("mis.side") === "collapsed" ? " collapsed" : "") });
    var side = el("nav", { "class": "side", "aria-label": "MIS" });
    side.appendChild(el("a", { "class": "side-brand", href: "/" }, [el("i", { "aria-hidden": "true" }), el("span", {}, ["V2Pulse", el("small", { text: "MIS workspace" })])]));
    var search = el("input", { type: "search", placeholder: "Find a page or report…", "aria-label": "Find a page" });
    side.appendChild(el("div", { "class": "side-search" }, search));
    var navBox = el("div"); side.appendChild(navBox);
    side.appendChild(el("div", { "class": "side-foot" }, [el("a", { href: "/" }, [MIS.icon("home", 15), el("span", { "class": "t", text: " Back to V2Pulse" })])]));
    var main = el("div", { "class": "main" });
    var tilesBox = el("div", { "class": "tiles hidden", "aria-live": "polite" });
    var titleH = el("h1"), sub = el("div", { "class": "sub" }), actions = el("div", { "class": "mast-actions" }), crumbs = el("div", { "class": "crumbs" });
    var toggle = el("button", { type: "button", "class": "icon-btn", "aria-label": "Collapse or expand the menu" }, [MIS.icon("menu", 18)]);
    toggle.addEventListener("click", function () {
      if (window.innerWidth <= 900) { app.classList.toggle("menu-open"); return; }
      app.classList.toggle("collapsed"); localStorageSet("mis.side", app.classList.contains("collapsed") ? "collapsed" : "");
    });
    var mast = el("header", { "class": "mast" }, el("div", { "class": "mast-in" }, [el("div", { "class": "mast-top" }, [toggle, crumbs]),
      el("div", { "class": "title-row" }, [el("div", {}, [titleH, sub]), actions]), tilesBox]));
    var content = el("main", { "class": "content", id: "content" });
    main.appendChild(mast); main.appendChild(content);
    app.appendChild(side); app.appendChild(main);
    document.body.appendChild(app);
    S = { app: app, nav: navBox, search: search, title: titleH, sub: sub, actions: actions, tiles: tilesBox, crumbs: crumbs, content: content };
    search.addEventListener("input", function () { drawNav(); });
    // In-workspace links (data-nav, or any /mis link) switch page without a reload.
    document.addEventListener("click", function (e) {
      var a = e.target.closest ? e.target.closest("a[href]") : null;
      if (!a || a.target === "_blank" || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
      var u = new URL(a.href, location.href);
      if (u.origin !== location.origin || !/^\/mis(\/|$)/.test(u.pathname) || /^\/mis\/static\//.test(u.pathname)) return;
      e.preventDefault(); MIS.go(u.pathname + u.search);
    });
    window.addEventListener("popstate", function () { render(); });
  }
  function localStorageGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function localStorageSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  MIS.store = { get: function (k, d) { var v = localStorageGet("mis." + k); try { return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
                set: function (k, v) { localStorageSet("mis." + k, JSON.stringify(v)); } };

  function drawNav() {
    MIS.clear(S.nav); var s = S.search.value.trim().toLowerCase(), cur = MIS.current && MIS.current.tab, groups = {};
    MIS.TABS.forEach(function (t) { (groups[t.g] = groups[t.g] || []).push(t); });
    Object.keys(groups).forEach(function (g) {
      var items = groups[g].filter(function (t) { return !s || t.label.toLowerCase().indexOf(s) >= 0; });
      var subs = [];
      var invSubs = [];
      if (g === "Reports" && MIS.sidebarReports) subs = MIS.sidebarReports.filter(function (r) { return !s || r.name.toLowerCase().indexOf(s) >= 0; });
      if (g === "Reports" && MIS.sidebarInvReports) invSubs = MIS.sidebarInvReports.filter(function (r) { return !s || r.name.toLowerCase().indexOf(s) >= 0; });
      if (!items.length && !subs.length && !invSubs.length) return;
      var box = el("div", { "class": "side-group" }, [el("h4", { text: g })]);
      items.forEach(function (t) {
        if (t.perm === "dumps" && MIS.me && !MIS.me.can.dumps) return;
        box.appendChild(el("a", { "class": "item" + (t.id === cur ? " on" : ""), href: t.path, title: t.label, "aria-current": t.id === cur ? "page" : null },
          [MIS.icon(t.icon), el("span", { text: t.label }), t.wave ? el("span", { "class": "soon", text: "soon" }) : null]));
        if (t.id === "reports" && subs.length) subs.slice(0, 40).forEach(function (r) {
          box.appendChild(el("a", { "class": "item sub" + (MIS.current && MIS.current.slug === r.slug && MIS.current.id === "report" ? " on" : ""), href: "/mis/r/" + encodeURIComponent(r.slug), title: r.name }, [el("span", { text: r.name })]));
        });
        if (t.id === "inv-reports" && invSubs.length) invSubs.slice(0, 40).forEach(function (r) {
          box.appendChild(el("a", { "class": "item sub" + (MIS.current && MIS.current.slug === r.slug && MIS.current.id === "inv-report" ? " on" : ""), href: "/mis/inv-reports/r/" + encodeURIComponent(r.slug), title: r.name }, [el("span", { text: r.name })]));
        });
      });
      S.nav.appendChild(box);
    });
  }

  // Page context handed to a page module.
  MIS.setHeader = function (o) {
    o = o || {};
    if (o.title !== undefined) { S.title.textContent = o.title; document.title = o.title + " · MIS · V2Pulse"; }
    if (o.sub !== undefined) S.sub.textContent = o.sub || "";
    if (o.actions !== undefined) { MIS.clear(S.actions); (o.actions || []).forEach(function (a) { if (a) S.actions.appendChild(a); }); }
    if (o.tiles !== undefined) MIS.tiles(S.tiles, o.tiles);
    if (o.crumbs !== undefined) {
      MIS.clear(S.crumbs); var parts = [{ label: "V2Pulse", href: "/" }, { label: "MIS", href: "/mis" }].concat(o.crumbs || []);
      parts.forEach(function (p, i) { if (i) S.crumbs.appendChild(el("span", { "class": "sep", text: "/" }));
        S.crumbs.appendChild(p.href && i < parts.length - 1 ? el("a", { href: p.href, text: p.label }) : el("span", { text: p.label })); });
    }
  };
  MIS.go = function (url, replace) { history[replace ? "replaceState" : "pushState"]({}, "", url); render(); window.scrollTo(0, 0); };
  MIS.page = function (id, def) { MIS.pages[id] = def; };

  function loadModule(id) {
    var file = MODULE[id] || id;
    if (MIS.pages[id]) return Promise.resolve(MIS.pages[id]);
    if (MIS.cache["mod:" + file]) return MIS.cache["mod:" + file].then(function () { return MIS.pages[id]; });
    MIS.cache["mod:" + file] = new Promise(function (res, rej) {
      var s = el("script", { src: "/mis/static/pages/" + file + ".js?v=" + (window.MIS_VERSION || "1") });
      s.onload = res; s.onerror = function () { rej(new Error("This page could not be loaded.")); }; document.head.appendChild(s);
    });
    return MIS.cache["mod:" + file].then(function () { return MIS.pages[id]; });
  }

  var renderSeq = 0, leaveFns = [];
  function render() {
    leaveFns.splice(0).forEach(function (fn) { try { fn(); } catch (e) { /* ignore */ } });
    var seq = ++renderSeq, r = MIS.route(location.pathname);
    MIS.current = r; MIS.closePop(); drawNav();
    if (S.app.classList.contains("menu-open")) S.app.classList.remove("menu-open");
    var tab = MIS.TABS.filter(function (t) { return t.id === r.tab; })[0];
    MIS.setHeader({ title: tab ? tab.label : "MIS", sub: "", actions: [], tiles: [], crumbs: tab ? [{ label: tab.label, href: tab.path }] : [] });
    MIS.clear(S.content);
    if (MIS.current.id === "not-found") { S.content.appendChild(el("div", { "class": "notice bad", text: "There is no such MIS page." })); return; }
    if (tab && tab.wave) {
      S.content.appendChild(el("div", { "class": "card" }, el("div", { "class": "card-b stack" }, [el("h2", { style: "margin:0", text: tab.label + " is on its way" }),
        el("p", { "class": "muted", style: "margin:0", text: "This DFS tab is being brought into V2Pulse in wave " + tab.wave + ". Until then it is available in DFS." }),
        el("div", {}, el("a", { "class": "btn", href: "https://dfs.v2retail.net", target: "_blank", rel: "noopener", text: "Open DFS" }))])));
      return;
    }
    S.content.appendChild(el("div", { "class": "card" }, el("div", { "class": "card-b muted", html: '<span class="spin"></span> Loading…' })));
    loadModule(r.id).then(function (pg) {
      if (seq !== renderSeq) return;
      MIS.clear(S.content);
      if (!pg) throw new Error("This page is not available yet.");
      return pg.render({ main: S.content, route: r, me: MIS.me, alive: function () { return seq === renderSeq; },
                         onLeave: function (fn) { leaveFns.push(fn); } });
    }).catch(function (e) {
      if (seq !== renderSeq) return;
      MIS.clear(S.content).appendChild(el("div", { "class": "notice bad", text: e.message }));
    });
  }

  // Saved MIS reports with "show in sidebar", listed under Reports (as DFS does).
  MIS.refreshSidebarReports = function () {
    MIS.get("/api/v1/mis/inv-report/reports").then(function (r) {
      MIS.sidebarInvReports = (r.reports || []).filter(function (x) { return x.showInSidebar !== false; }).sort(function (a, b) { return a.name.localeCompare(b.name); });
      drawNav();
    }, function () { MIS.sidebarInvReports = []; });
    return MIS.get("/api/v1/mis/report/reports").then(function (r) {
      MIS.sidebarReports = (r.reports || []).filter(function (x) { return x.showInSidebar !== false; }).sort(function (a, b) { return a.name.localeCompare(b.name); });
      drawNav(); return r.reports || [];
    }, function () { MIS.sidebarReports = []; return []; });
  };

  function boot() {
    buildShell();
    MIS.get("/api/v1/mis/me").then(function (me) {
      MIS.me = me; render(); MIS.refreshSidebarReports();
    }, function (e) {
      MIS.setHeader({ title: "MIS", sub: "" });
      S.content.appendChild(el("div", { "class": "notice bad", text: e.status === 403 ? "You do not have access to the MIS pages. Ask your V2Pulse administrator for MIS access." : e.message }));
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();

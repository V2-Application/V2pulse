// Simulation (/simulation). Reads /api/v1/simulation (catalog, Store Revival options and runs) through the normal
// V2Pulse session, so the caller's permission and store scope apply. DOM is built with textContent only.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };

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
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function inr(v) {
    if (v === null || v === undefined || isNaN(v)) return "–";
    var a = Math.abs(v), s = v < 0 ? "−" : "";
    if (a >= 1e7) return s + "₹" + (a / 1e7).toFixed(2) + " Cr";
    if (a >= 1e5) return s + "₹" + (a / 1e5).toFixed(1) + " L";
    return s + "₹" + Math.round(a).toLocaleString("en-IN");
  }
  function units(v) { return v === null || v === undefined ? "–" : Math.round(Math.abs(v)).toLocaleString("en-IN"); }
  function pct(v) { return v === null || v === undefined ? "–" : Math.round(v * 100) + "%"; }
  function signed(v, f) { return (v > 0 ? "+" : v < 0 ? "−" : "") + f(Math.abs(v)); }
  function stateRow(tbody, cols, text) {
    clear(tbody);
    var tr = el("tr"), td = el("td", { "class": "state", colspan: String(cols) }, text);
    tr.appendChild(td); tbody.appendChild(tr);
  }

  // ------------------------------------------------------------------ state
  var opts = null, last = null, timer = null, seq = 0;
  var st = { scope: "store", store: "", zone: "", div: "", cat: "", target: 0.85, mode: "raise", basis: "peer", cap: true };
  try {   // per-viewer convenience: remember the last store
    var saved = localStorage.getItem("pulse.sim.store");
    if (saved) st.store = saved;
  } catch (e) { /* storage blocked */ }

  var HINTS = {
    raise: "Tops up categories below the target. Categories already above it are left alone.",
    set: "Also takes stock out of categories above the target, which shows lost sales.",
    peer: "Sell-through of well-stocked stores in the same category. Conservative.",
    own: "This store's own 30-day sell-through, capped at 100%. Optimistic for fast sellers."
  };

  // ------------------------------------------------------------------ pickers
  function fillSelect(sel, items, current, allLabel) {
    clear(sel);
    if (allLabel) sel.appendChild(el("option", { value: "" }, allLabel));
    items.forEach(function (it) {
      var o = el("option", { value: it.value }, it.label);
      if (it.value === current) o.selected = true;
      sel.appendChild(o);
    });
  }
  function fillCategories() {
    var list = opts.categories.filter(function (c) { return !st.div || c.div === st.div; })
      .map(function (c) { return c.maj_cat; });
    list = list.filter(function (v, i) { return list.indexOf(v) === i; }).sort();
    if (st.cat && list.indexOf(st.cat) < 0) st.cat = "";
    fillSelect($("cat"), list.map(function (m) { return { value: m, label: m }; }), st.cat, "All categories (" + list.length + ")");
  }
  function storeHint() {
    var s = opts.stores.filter(function (x) { return x.code === st.store; })[0];
    $("storeHint").textContent = s ? (s.zone || "") + " · RDC " + (s.rdc || "–") + " · " + inr(s.sale_v_l30d) + " sales last 30 days" : "";
  }
  function setPressed(group, attr, value) {
    Array.prototype.forEach.call(document.querySelectorAll("[" + attr + "]"), function (b) {
      b.setAttribute("aria-pressed", b.getAttribute(attr) === value ? "true" : "false");
    });
  }

  function initPickers(d) {
    opts = d;
    if (!opts.stores.some(function (s) { return s.code === st.store; })) {
      // default: the store with the highest sales in the caller's scope
      var top = opts.stores.slice().sort(function (a, b) { return (b.sale_v_l30d || 0) - (a.sale_v_l30d || 0); })[0];
      st.store = top ? top.code : "";
    }
    st.zone = opts.zones[0] || "";
    fillSelect($("store"), opts.stores.map(function (s) { return { value: s.code, label: s.code + " · " + (s.name || "") }; }), st.store);
    fillSelect($("zone"), opts.zones.map(function (z) { return { value: z, label: z }; }), st.zone);
    var divs = opts.categories.map(function (c) { return c.div; }).filter(function (v, i, a) { return v && a.indexOf(v) === i; }).sort();
    fillSelect($("div"), divs.map(function (v) { return { value: v, label: v }; }), st.div, "All divisions");
    fillCategories();
    storeHint();
    $("modeHint").textContent = HINTS[st.mode];
    $("basisHint").textContent = HINTS[st.basis];
  }

  // ------------------------------------------------------------------ run + render
  function schedule(delay) {
    clearTimeout(timer);
    timer = setTimeout(run, delay === undefined ? 350 : delay);
  }
  function run() {
    if (!opts) return;
    var body = { scope: st.scope, code: st.scope === "store" ? st.store : st.scope === "zone" ? st.zone : null,
                 div: st.div || null, maj_cat: st.cat || null, target: st.target, mode: st.mode, basis: st.basis, cap: st.cap };
    if (st.scope !== "all" && !body.code) return;
    var my = ++seq;
    $("results").classList.add("busy");
    api("POST", "/api/v1/simulation/store-revival/run", body).then(function (res) {
      if (my !== seq) return;   // a newer run is on its way
      $("results").classList.remove("busy");
      if (!res.ok || !res.data || !res.data.data) {
        showError(res.data && (res.data.detail || res.data.title) || "The simulation could not run. Try again in a moment.");
        return;
      }
      last = res.data.data;
      render(last);
    }).catch(function () {
      if (my !== seq) return;
      $("results").classList.remove("busy");
      showError("Could not reach V2Pulse. Check the connection and try again.");
    });
  }
  function showError(msg) {
    clear($("notes"));
    $("notes").appendChild(el("div", { "class": "warnline", role: "alert" }, msg));
    $("csv").disabled = true;
  }

  function render(d) {
    var t = d.totals, neg = t.extra_sales < 0;
    $("kHero").classList.toggle("neg", neg);
    $("kSale").textContent = signed(t.extra_sales, inr);
    $("kSalePct").textContent = t.sales_base ? signed(100 * t.extra_sales / t.sales_base, function (v) { return v.toFixed(1); }) +
      "% of " + inr(t.sales_base) + " sales" : " ";
    $("kUnits").textContent = signed(t.units, units);
    $("kCats").textContent = t.changed.toLocaleString("en-IN") + " of " + t.rows_used.toLocaleString("en-IN") + " store-categories change";
    $("kValue").textContent = inr(t.stock_value);
    $("kFr").textContent = pct(t.fill_before) + " → " + pct(t.fill_after);
    $("kFrS").textContent = t.stores > 1 ? t.stores + " stores" : "of MBQ";

    clear($("notes"));
    var note = [];
    if (d.as_of) note.push("Snapshot " + d.as_of + ".");
    if (t.skipped) note.push(t.skipped.toLocaleString("en-IN") + " store-categories skipped: too few well-stocked stores to estimate demand, or no sales price.");
    if (d.truncated) note.push("The table shows the top " + d.rows.length + " changes; the totals include all of them.");
    if (note.length) $("notes").appendChild(el("p", { "class": "hint" }, note.join(" ")));

    // by division
    var box = $("divBars"); clear(box);
    if (!d.by_div.length) box.appendChild(el("div", { "class": "state" }, "Nothing to change for this selection."));
    var max = Math.max.apply(null, d.by_div.map(function (x) { return Math.abs(x.extra_sales); }).concat([1]));
    d.by_div.slice(0, 12).forEach(function (x) {
      var row = el("div", { "class": "bar" });
      row.appendChild(el("span", { "class": "mono" }, x.div || "–"));
      var tr = el("span", { "class": "track" }), f = el("span", { "class": "fill" + (x.extra_sales < 0 ? " neg" : "") });
      f.style.width = (100 * Math.abs(x.extra_sales) / max).toFixed(1) + "%";
      tr.appendChild(f); row.appendChild(tr);
      row.appendChild(el("span", { "class": "val" }, signed(x.extra_sales, inr)));
      box.appendChild(row);
    });

    // table
    var many = st.scope !== "store";
    var head = $("thead"); clear(head);
    (many ? ["Store"] : []).concat(["Category", "Fill rate", "MBQ", "Stock", "Units", "Sell-through", "Extra sales"]).forEach(function (h, i, arr) {
      head.appendChild(el("th", { "class": i >= arr.length - 5 ? "r" : "" }, h));
    });
    var tb = $("tbody");
    if (!d.rows.length) { stateRow(tb, many ? 8 : 7, "Nothing to change for this selection."); }
    else {
      clear(tb);
      d.rows.forEach(function (r) {
        var tr = el("tr");
        if (many) {
          var c = el("td"); c.appendChild(el("span", { "class": "mono" }, r.store)); c.appendChild(el("br"));
          c.appendChild(el("span", { "class": "hint" }, r.store_name || "")); tr.appendChild(c);
        }
        var cat = el("td"); cat.appendChild(el("span", { "class": "mono" }, r.maj_cat)); cat.appendChild(el("br"));
        cat.appendChild(el("span", { "class": "hint" }, r.div || "")); tr.appendChild(cat);
        var fr = el("td"), bar = el("span", { "class": "frbar" }), i0 = el("i"), b1 = el("b");
        var w0 = Math.min(r.fr_before || 0, 1.2) / 1.2 * 100, w1 = Math.min(r.fr_after || 0, 1.2) / 1.2 * 100;
        i0.style.width = w0.toFixed(1) + "%"; b1.style.left = Math.min(w0, w1).toFixed(1) + "%"; b1.style.width = Math.abs(w1 - w0).toFixed(1) + "%";
        bar.appendChild(i0); bar.appendChild(b1); fr.appendChild(bar);
        fr.appendChild(el("span", { "class": "mono" }, pct(r.fr_before) + "→" + pct(r.fr_after))); tr.appendChild(fr);
        tr.appendChild(el("td", { "class": "r" }, units(r.mbq)));
        tr.appendChild(el("td", { "class": "r" }, units(r.stock)));
        tr.appendChild(el("td", { "class": "r" }, signed(r.units, units)));
        tr.appendChild(el("td", { "class": "r" }, pct(r.rate)));
        tr.appendChild(el("td", { "class": "r " + (r.extra_sales >= 0 ? "up" : "down") }, signed(r.extra_sales, inr)));
        tb.appendChild(tr);
      });
    }
    $("csv").disabled = !d.rows.length;
  }

  function downloadCsv() {
    if (!last || !last.rows.length) return;
    var cols = ["store", "store_name", "zone", "div", "maj_cat", "mbq", "stock", "units", "rate", "extra_sales", "fr_before", "fr_after"];
    var esc = function (v) { var s = v === null || v === undefined ? "" : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
    var lines = [cols.join(",")].concat(last.rows.map(function (r) { return cols.map(function (c) { return esc(r[c]); }).join(","); }));
    var blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    var a = el("a", { href: URL.createObjectURL(blob), download: "store-revival-" + (last.as_of || "latest") + ".csv" });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }

  // ------------------------------------------------------------------ wiring
  function wire() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-scope]"), function (b) {
      b.addEventListener("click", function () {
        st.scope = b.getAttribute("data-scope"); setPressed("scope", "data-scope", st.scope);
        $("storeField").hidden = st.scope !== "store"; $("zoneField").hidden = st.scope !== "zone"; schedule(0);
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-mode]"), function (b) {
      b.addEventListener("click", function () {
        st.mode = b.getAttribute("data-mode"); setPressed("mode", "data-mode", st.mode); $("modeHint").textContent = HINTS[st.mode]; schedule(0);
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-basis]"), function (b) {
      b.addEventListener("click", function () {
        st.basis = b.getAttribute("data-basis"); setPressed("basis", "data-basis", st.basis); $("basisHint").textContent = HINTS[st.basis]; schedule(0);
      });
    });
    $("store").addEventListener("change", function (e) {
      st.store = e.target.value; storeHint();
      try { localStorage.setItem("pulse.sim.store", st.store); } catch (err) { /* storage blocked */ }
      schedule(0);
    });
    $("zone").addEventListener("change", function (e) { st.zone = e.target.value; schedule(0); });
    $("div").addEventListener("change", function (e) { st.div = e.target.value; fillCategories(); schedule(0); });
    $("cat").addEventListener("change", function (e) { st.cat = e.target.value; schedule(0); });
    $("target").addEventListener("input", function (e) {
      st.target = Number(e.target.value) / 100; $("targetOut").textContent = e.target.value; schedule();
    });
    $("cap").addEventListener("change", function (e) { st.cap = e.target.checked; schedule(0); });
    $("csv").addEventListener("click", downloadCsv);
    $("controls").addEventListener("submit", function (e) { e.preventDefault(); });
  }

  function start() {
    wire();
    api("GET", "/api/v1/simulation/catalog").then(function (res) {
      var box = $("sims"); clear(box);
      if (!res.ok || !res.data) {
        box.appendChild(el("div", { "class": "warnline", role: "alert" },
          res.status === 403 ? "You do not have access to Simulation yet. Ask the V2Pulse team." : "Simulation could not load. Try again in a moment."));
        $("revival").hidden = true;
        return;
      }
      (res.data.data || []).forEach(function (s) {
        var b = el("button", { "class": "chip", type: "button", title: s.description || "", "aria-pressed": s.key === "store-revival" ? "true" : "false" }, s.name);
        if (s.status !== "live") { b.disabled = true; b.appendChild(el("small", {}, "soon")); }
        box.appendChild(b);
      });
      if (res.data.user) $("who").textContent = (res.data.user.name || "") + (res.data.user.scope ? " · " + res.data.user.scope : "");
      return api("GET", "/api/v1/simulation/store-revival/options").then(function (o) {
        if (!o.ok || !o.data || !o.data.data) { showError((o.data && o.data.detail) || "Store list could not load."); return; }
        if (!o.data.data.stores.length) { showError("No stores in your scope have fill-rate data."); return; }
        initPickers(o.data.data);
        schedule(0);
      });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();

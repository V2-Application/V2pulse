// V2Pulse web-app add-ons, injected into every page by pulse/app.py (spa), outside static/ so a web-app rebuild
// keeps them.
//  1. Sidebar labels: the web app looks each tab's label up in its built-in dictionary and shows nothing for a tab
//     it does not know (e.g. Analytics). The label then comes from /api/v1/me instead.
//  2. Server pages: /analytics, /mis, /dumps (and anything under /manage/) are pages of their own, not web-app screens. A click
//     on their sidebar entry, or an in-app jump to them, loads them as a full page.
//  3. Active tab: the web app marks a tab active by its path alone, so a tab that links into a library with a query
//     (Scheduled Reports = /reports?tree=custom:scheduledreports) lights up together with Reports. The tab whose
//     query matches the address wins; the other gets data-pulse-inactive, which overrides.css draws as not active.
//     aria-current is left to the web app (it re-renders it), we only add our own attribute.
//  4. Library split: the Scheduled Reports section is reached from its own sidebar tab, so on the Reports page its
//     chip is hidden, and on the Scheduled Reports tab (?tree=custom:scheduledreports) every other chip is hidden.
//     Sections listed in HIDDEN_TREES are also left out of the Reports page (empty department sections).
(function () {
  "use strict";
  var SERVER_PAGES = /^\/(analytics|manage|mis|dumps|simulation)(\/|$|\?)/;   // MIS and Dump Scheduler pages too (2026-10-17)
  var SCHED_TREE = "custom:scheduledreports";
  var HIDDEN_TREES = ["dept:PLATFORM"];
  var hiddenNames = [];
  var labels = null;
  var schedName = null;

  function loadTrees() {
    fetch("/api/v1/reports/trees", { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (res) {
        var data = (res && res.data) || [];
        hiddenNames = data.filter(function (x) { return HIDDEN_TREES.indexOf(x.tree) >= 0; })
          .map(function (x) { return x.name || x.default_name; });
        var t = data.filter(function (x) { return x.tree === SCHED_TREE; })[0];
        if (!t) return;
        schedName = t.name || "Scheduled Reports";
        schedule();
      })
      .catch(function () { /* signed out or offline */ });
  }

  // The chip's own text (the section name), without the item-count tag inside it.
  function chipName(chip) {
    var s = "";
    for (var i = 0; i < chip.childNodes.length; i++) {
      if (chip.childNodes[i].nodeType === 3) s += chip.childNodes[i].textContent;
    }
    return s.trim();
  }

  function splitLibrary() {
    if (!schedName || location.pathname !== "/reports") return;
    var onSched = new URLSearchParams(location.search).get("tree") === SCHED_TREE;
    var chips = document.querySelectorAll('[role="group"][aria-label="Library"] button.chip');
    for (var i = 0; i < chips.length; i++) {
      var name = chipName(chips[i]), isSched = name === schedName;
      var hide = onSched ? !isSched : (isSched || hiddenNames.indexOf(name) >= 0);
      if (hide) { if (!chips[i].hasAttribute("data-pulse-hidden")) chips[i].setAttribute("data-pulse-hidden", ""); }
      else if (chips[i].hasAttribute("data-pulse-hidden")) chips[i].removeAttribute("data-pulse-hidden");
    }
  }

  function loadLabels() {
    fetch("/api/v1/me", { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (me) {
        if (!me || !me.nav) return;
        labels = {};
        me.nav.forEach(function (n) { labels[n.path] = n.label; });
        fixLabels();
      })
      .catch(function () { /* signed out or offline: nothing to fix */ });
  }

  function fixLabels() {
    if (!labels) return;
    var links = document.querySelectorAll("a.rail-link");
    for (var i = 0; i < links.length; i++) {
      var a = links[i], label = labels[a.getAttribute("href")];
      if (!label) continue;
      if (!a.getAttribute("title")) a.setAttribute("title", label);
      if (!a.getAttribute("aria-label")) a.setAttribute("aria-label", label);
      var span = a.querySelector("span.truncate");
      if (span && !span.textContent.trim()) span.textContent = label;
    }
  }

  // A rail link with a query is active only when every one of its query values is in the address; a plain link
  // gives way when a query link on the same path is active.
  function fixActive() {
    var links = document.querySelectorAll("a.rail-link");
    var here = new URLSearchParams(location.search), winners = {}, i, a, u;
    for (i = 0; i < links.length; i++) {
      a = links[i];
      u = new URL(a.getAttribute("href") || "", location.href);
      if (!u.search) continue;
      var match = u.pathname === location.pathname;
      u.searchParams.forEach(function (v, k) { if (here.get(k) !== v) match = false; });
      setInactive(a, !match);
      if (match) winners[u.pathname] = true;
    }
    for (i = 0; i < links.length; i++) {
      a = links[i];
      u = new URL(a.getAttribute("href") || "", location.href);
      if (!u.search) setInactive(a, !!winners[u.pathname]);
    }
  }

  function setInactive(a, off) {
    if (off) { if (!a.hasAttribute("data-pulse-inactive")) a.setAttribute("data-pulse-inactive", ""); }
    else if (a.hasAttribute("data-pulse-inactive")) a.removeAttribute("data-pulse-inactive");
  }

  var queued = false;
  function schedule() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(function () { queued = false; fixLabels(); fixActive(); splitLibrary(); });
  }
  new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener("popstate", schedule);

  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest ? e.target.closest("a[href]") : null;
    if (!a || a.target === "_blank") return;
    var href = a.getAttribute("href");
    if (href && SERVER_PAGES.test(href)) {
      e.preventDefault();
      e.stopPropagation();
      window.location.assign(href);
    }
  }, true);

  // Jumps made inside the web app (command palette, search) would land on its catch-all route, which sends you
  // home; load those as pages instead.
  ["pushState", "replaceState"].forEach(function (fn) {
    var orig = history[fn];
    history[fn] = function (state, title, url) {
      if (url != null) {
        var path = new URL(String(url), location.href);
        if (path.origin === location.origin && SERVER_PAGES.test(path.pathname)) {
          window.location.assign(path.pathname + path.search);
          return;
        }
      }
      var r = orig.apply(this, arguments);
      schedule(); // a query-only change (switching library tree) may not touch the sidebar DOM
      return r;
    };
  });

  loadLabels();
  loadTrees();
})();

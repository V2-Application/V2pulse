// "Ask me a question": the Claude panel on every V2Pulse report page. Injected by pulse/app.py into the web app and
// into the server pages (Analytics, MIS, Simulation); the answers come from /api/v1/assist (pulse/modules/assist.py).
//  - What the report is showing is recorded as the page loads it: a table report's last /reports/<code>/data filters,
//    a dashboard's last /bi/query bodies (these carry the user's slicer choices). The server re-reads that data with
//    the user's own scope; only the question, those query shapes and the screen text are sent from here.
//  - The conversation is kept per page in sessionStorage (this tab only) and sent back as history with each question.
(function () {
  "use strict";
  if (window.__pulseAssist) return;
  window.__pulseAssist = true;

  var HIDE = /^\/(login|admin|me|auth|manage)(\/|$)|^\/reports\/dynamic\/(new|[^/]+\/edit)(\/|$)/;
  var MAX_BI = 40;
  var cap = { path: location.pathname, params: {}, bi: [] };

  // ---------------------------------------------------------------- record what the page loads
  function resetCapture() {
    if (cap.path !== location.pathname) cap = { path: location.pathname, params: {}, bi: [] };
  }
  function record(url, init) {
    try {
      var u = new URL(String(url), location.href);
      if (u.origin !== location.origin) return;
      resetCapture();
      var m = u.pathname.match(/^\/api\/v1\/reports\/([^/]+)\/data$/);
      if (m && u.searchParams.get("format") !== "csv") {
        var p = {};
        u.searchParams.forEach(function (v, k) { if (["cursor", "limit", "format"].indexOf(k) < 0) p[k] = v; });
        cap.params = p;
      } else if (u.pathname === "/api/v1/bi/query" && init && typeof init.body === "string") {
        var b = JSON.parse(init.body);
        var key = JSON.stringify(b);
        cap.bi = cap.bi.filter(function (x) { return JSON.stringify(x) !== key; });
        cap.bi.push(b);
        if (cap.bi.length > MAX_BI) cap.bi.shift();
      }
    } catch (e) { /* never break the page's own request */ }
  }
  var origFetch = window.fetch;
  window.fetch = function (input, init) {
    record(typeof input === "string" || input instanceof URL ? input : (input && input.url), init);
    return origFetch.apply(this, arguments);
  };

  // ---------------------------------------------------------------- what page is this
  function subject() {
    var path = location.pathname, m;
    var s = { path: path + location.search, kind: "page", title: pageTitle() };
    if ((m = path.match(/^\/reports\/view\/([^/]+)/))) { s.kind = "table"; s.report_code = decodeURIComponent(m[1]); }
    else if ((m = path.match(/^\/reports\/dynamic\/([0-9a-f-]{36})\/?$/i))) {
      s.kind = "bi"; s.bi_report_id = m[1];
      s.bi_page = parseInt(new URLSearchParams(location.search).get("page") || "0", 10) || 0;
    }
    return s;
  }
  function pageTitle() {
    var h = document.querySelector("main h1, h1, main h2");
    var t = h && h.textContent.trim();
    return (t || document.title || "This page").slice(0, 200);
  }
  function screenText() {
    var root = document.querySelector("main") || document.body;
    var clone = root.cloneNode(true);
    var mine = clone.querySelectorAll("#pa-root, script, style, svg");
    for (var i = 0; i < mine.length; i++) mine[i].remove();
    var t = (clone.innerText || clone.textContent || "");
    if (!t.trim()) t = root.innerText || "";
    return t.replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n").trim().slice(0, 25000);
  }

  // ---------------------------------------------------------------- storage (per page, this tab only)
  function storeKey() { return "pulse-assist:" + location.pathname; }
  function loadChat() {
    try { return JSON.parse(sessionStorage.getItem(storeKey()) || "[]") || []; } catch (e) { return []; }
  }
  function saveChat(list) {
    try { sessionStorage.setItem(storeKey(), JSON.stringify(list.slice(-30))); } catch (e) { /* private mode */ }
  }

  // ---------------------------------------------------------------- markdown (escaped first, then a safe subset)
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function inline(s) {
    return s.replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>");
  }
  function md(src) {
    var lines = esc(src || "").split("\n"), out = [], i = 0;
    while (i < lines.length) {
      var l = lines[i];
      if (/^```/.test(l)) {
        var code = [];
        for (i++; i < lines.length && !/^```/.test(lines[i]); i++) code.push(lines[i]);
        i++;
        out.push("<pre><code>" + code.join("\n") + "</code></pre>");
        continue;
      }
      if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1])) {
        var cells = function (x) { return x.trim().replace(/^\||\|$/g, "").split("|").map(function (c) { return c.trim(); }); };
        var head = cells(l), rows = [];
        for (i += 2; i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i]); i++) rows.push(cells(lines[i]));
        var num = function (c) { return /^[-+]?(Rs\.?\s*)?[\d,.]+\s*(%|L|Cr|lakh|crore|k)?$/i.test(c); };
        out.push('<div class="pa-tbl"><table><thead><tr>' + head.map(function (c) { return "<th>" + inline(c) + "</th>"; }).join("") +
          "</tr></thead><tbody>" + rows.map(function (r) {
            return "<tr>" + r.map(function (c) { return "<td" + (num(c) ? ' class="pa-n"' : "") + ">" + inline(c) + "</td>"; }).join("") + "</tr>";
          }).join("") + "</tbody></table></div>");
        continue;
      }
      var h = l.match(/^(#{1,4})\s+(.*)$/);
      if (h) { out.push("<h4>" + inline(h[2]) + "</h4>"); i++; continue; }
      if (/^\s*([-*•]|\d+[.)])\s+/.test(l)) {
        var ordered = /^\s*\d+[.)]/.test(l), items = [];
        for (; i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i]); i++) {
          items.push("<li>" + inline(lines[i].replace(/^\s*([-*•]|\d+[.)])\s+/, "")) + "</li>");
        }
        out.push((ordered ? "<ol>" : "<ul>") + items.join("") + (ordered ? "</ol>" : "</ul>"));
        continue;
      }
      if (!l.trim()) { i++; continue; }
      var para = [];
      for (; i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*([-*•]|\d+[.)])\s+|\s*\|)/.test(lines[i]); i++) para.push(lines[i]);
      if (!para.length) { para.push(lines[i]); i++; }
      out.push("<p>" + inline(para.join("<br>")) + "</p>");
    }
    return out.join("");
  }

  // ---------------------------------------------------------------- UI
  var SUGGEST = {
    table: ["Summarise this report", "Where are the biggest gaps?", "Which stores need action first?", "Explain the columns"],
    bi: ["Summarise this dashboard", "What stands out on this page?", "Which zone or store is worst, and why?", "What should I act on today?"],
    page: ["Summarise what I'm looking at", "What needs attention here?", "Explain this page"]
  };
  var el = {}, busy = false, chat = [], lastPath = null, timer = null;

  function h(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  function build() {
    var root = h("div"); root.id = "pa-root";
    root.innerHTML =
      '<button type="button" class="pa-launch" aria-haspopup="dialog" aria-controls="pa-panel">' +
      '<span class="pa-spark" aria-hidden="true">✦</span><span>Ask me a question</span></button>' +
      '<section id="pa-panel" class="pa-panel" role="dialog" aria-label="Ask me a question" hidden>' +
      '<header class="pa-head"><div class="pa-title"><span class="pa-spark" aria-hidden="true">✦</span>' +
      '<div><div class="pa-h">Ask me a question</div><div class="pa-sub"></div></div></div>' +
      '<button type="button" class="pa-icon pa-new" title="New conversation" aria-label="New conversation">' +
      '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg></button>' +
      '<button type="button" class="pa-icon pa-close" title="Close (Esc)" aria-label="Close">' +
      '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button></header>' +
      '<div class="pa-log" aria-live="polite"></div>' +
      '<form class="pa-form"><textarea rows="1" maxlength="2000" placeholder="Ask about this report…" aria-label="Your question"></textarea>' +
      '<button type="submit" class="pa-send" aria-label="Send"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg></button></form>' +
      '<div class="pa-foot">Claude reads this report with your access. Check figures before acting.</div></section>';
    document.body.appendChild(root);
    el.root = root;
    el.launch = root.querySelector(".pa-launch");
    el.panel = root.querySelector(".pa-panel");
    el.sub = root.querySelector(".pa-sub");
    el.log = root.querySelector(".pa-log");
    el.form = root.querySelector(".pa-form");
    el.input = root.querySelector("textarea");
    el.launch.addEventListener("click", open);
    root.querySelector(".pa-close").addEventListener("click", close);
    root.querySelector(".pa-new").addEventListener("click", function () { if (!busy) { chat = []; saveChat(chat); render(); el.input.focus(); } });
    el.form.addEventListener("submit", function (e) { e.preventDefault(); send(el.input.value); });
    el.input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(el.input.value); }
    });
    el.input.addEventListener("input", grow);
    el.panel.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
    el.log.addEventListener("click", function (e) {
      var chip = e.target.closest(".pa-chip");
      if (chip) { send(chip.getAttribute("data-q")); return; }
      var cp = e.target.closest(".pa-copy");
      if (cp) {
        var txt = chat[+cp.getAttribute("data-i")].content;
        try { navigator.clipboard.writeText(txt); cp.textContent = "Copied"; setTimeout(function () { cp.textContent = "Copy"; }, 1500); } catch (x) { /* no clipboard */ }
      }
    });
  }

  function grow() { el.input.style.height = "auto"; el.input.style.height = Math.min(el.input.scrollHeight, 140) + "px"; }
  function open() {
    el.panel.hidden = false;
    el.launch.setAttribute("aria-expanded", "true");
    el.root.classList.add("pa-open");
    syncPage(true);
    setTimeout(function () { el.input.focus(); }, 30);
  }
  function close() {
    el.panel.hidden = true;
    el.launch.setAttribute("aria-expanded", "false");
    el.root.classList.remove("pa-open");
    el.launch.focus();
  }

  function render() {
    var s = subject(), html = [];
    el.sub.textContent = s.title;
    if (!chat.length) {
      html.push('<div class="pa-hello"><p>Hi! I can read <strong>' + esc(s.title) + "</strong> and answer questions about it: " +
        "what it shows, where the gaps are, what to act on.</p>" +
        '<div class="pa-chips">' + SUGGEST[s.kind].map(function (q) { return '<button type="button" class="pa-chip" data-q="' + esc(q) + '">' + esc(q) + "</button>"; }).join("") +
        "</div></div>");
    }
    chat.forEach(function (m, i) {
      if (m.role === "user") { html.push('<div class="pa-msg pa-me">' + esc(m.content) + "</div>"); return; }
      if (m.error) { html.push('<div class="pa-msg pa-err">' + esc(m.content) + "</div>"); return; }
      var steps = (m.steps || []).length ? '<details class="pa-steps"><summary>Worked out with ' + m.steps.length + " calculation" + (m.steps.length > 1 ? "s" : "") +
        "</summary><ol>" + m.steps.map(function (x) { return "<li" + (x.ok ? "" : ' class="pa-bad"') + ">" + esc(x.step) + "</li>"; }).join("") + "</ol></details>" : "";
      var last = i === chat.length - 1;
      html.push('<div class="pa-msg pa-ai"><div class="pa-md">' + md(m.content) + "</div>" + steps +
        '<div class="pa-meta">' + (m.source ? esc(sourceLine(m.source)) + " · " : "") + '<button type="button" class="pa-copy" data-i="' + i + '">Copy</button></div>' +
        (last && m.followups && m.followups.length ? '<div class="pa-chips">' + m.followups.map(function (q) {
          return '<button type="button" class="pa-chip" data-q="' + esc(q) + '">' + esc(q) + "</button>"; }).join("") + "</div>" : "") +
        "</div>");
    });
    if (busy) html.push('<div class="pa-msg pa-ai pa-wait"><span class="pa-dots"><i></i><i></i><i></i></span> <span class="pa-wait-t">Reading the report…</span></div>');
    el.log.innerHTML = html.join("");
    el.log.scrollTop = el.log.scrollHeight;
  }
  function csrf() {   // double-submit token (pulse/auth/sessions.py): the pulse_csrf cookie echoed in a header
    var m = document.cookie.match(/(?:^|;\s*)pulse_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }
  function sourceLine(src) {
    if (src.kind === "table") return src.rows.toLocaleString("en-IN") + " rows" + (src.as_of ? " · data " + fmtTime(src.as_of) : "");
    if (src.kind === "bi") return "Dashboard data";
    return "From the screen";
  }
  function fmtTime(iso) {
    try { return new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); } catch (e) { return ""; }
  }

  function send(q) {
    q = (q || "").trim();
    if (!q || busy) return;
    var s = subject();
    var history = chat.filter(function (m) { return !m.error; }).slice(-12).map(function (m) { return { role: m.role, content: String(m.content).slice(0, 6000) }; });
    chat.push({ role: "user", content: q });
    saveChat(chat);
    el.input.value = ""; grow();
    busy = true; render();
    var t0 = Date.now(), msgs = ["Reading the report…", "Looking through the data…", "Working out the figures…", "Writing the answer…"];
    timer = setInterval(function () {
      var w = el.log.querySelector(".pa-wait-t"), sec = Math.round((Date.now() - t0) / 1000);
      if (w) w.textContent = msgs[Math.min(msgs.length - 1, Math.floor(sec / 6))] + " " + sec + "s";
    }, 1000);
    resetCapture();
    var body = { question: q, path: s.path, title: s.title, history: history, page_text: screenText() };
    if (s.report_code) { body.report_code = s.report_code; body.params = cap.params; }
    if (s.bi_report_id) { body.bi_report_id = s.bi_report_id; body.bi_page = s.bi_page; body.bi_queries = cap.bi; }
    origFetch("/api/v1/assist/ask", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRF-Token": csrf() },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error((j && (j.detail || j.title)) || ("The assistant is not available (" + r.status + ")."));
        return j.data;
      });
    }).then(function (d) {
      chat.push({ role: "assistant", content: d.answer, followups: d.followups, steps: d.steps, source: d.source });
    }).catch(function (e) {
      chat.push({ role: "assistant", error: true, content: e.message || "Something went wrong. Try again." });
    }).then(function () {
      clearInterval(timer); busy = false; saveChat(chat); render();
      if (!el.panel.hidden) el.input.focus();
    });
  }

  function syncPage(force) {
    var show = !HIDE.test(location.pathname);
    el.root.style.display = show ? "" : "none";
    if (!show && !el.panel.hidden) close();
    if (force || lastPath !== location.pathname) {
      lastPath = location.pathname;
      resetCapture();
      if (!busy) { chat = loadChat(); render(); }
    } else if (!el.panel.hidden && !busy) {
      el.sub.textContent = subject().title;
    }
  }

  function start() {
    build();
    syncPage(true);
    window.addEventListener("popstate", function () { syncPage(); });
    setInterval(function () { syncPage(); }, 800);   // the web app changes routes with pushState
  }

  origFetch("/api/v1/assist/status", { credentials: "same-origin", headers: { Accept: "application/json" } })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (j) {
      if (!j || !j.data || !j.data.enabled) return;
      if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);
    })
    .catch(function () { /* signed out: no panel */ });
})();

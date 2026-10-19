// Dump Scheduler (/dumps): schedules that write a V2Pulse master or MIS reports to CSV / Excel in a folder on a
// timetable, with Run now / Stop, run history and recent activity. Port of DFS DumpScheduler.jsx. API: /api/v1/dumps/*.
(function () {
  "use strict";
  var M = window.MIS, el = M.el, main = M.$("#main");
  var INST = null, SCHED = [], ROLES = [], owner = "visible", poll = null, openRuns = {};
  var listBox = el("div", { "class": "card" }), actBox = el("div", { "class": "card" });
  var DOW = [["1", "Mon"], ["2", "Tue"], ["3", "Wed"], ["4", "Thu"], ["5", "Fri"], ["6", "Sat"], ["0", "Sun"]];
  var RULES = { yesterday: "Yesterday", last7: "Last 7 days", last30: "Last 30 days", mtd: "Month to date (to yesterday)", last_month: "Last month", fixed: "Fixed dates" };

  M.frame("Dump Scheduler", "dumps").then(function () {
    return M.api("GET", "/api/v1/dumps/instance");
  }).then(function (i) {
    INST = i;
    main.appendChild(el("p", { "class": "muted", style: "margin:0", text: "Write a V2Pulse master or MIS reports to CSV / Excel in a folder on a timetable. Files go to <folder>\\YYYY_MM_DD\\<file>; the V2Pulse worker on " +
      i.host + " writes them, so the folder must be one that server can reach. " + (i.mail ? "Failures are e-mailed to the schedule owner." : "Failure e-mails are not set up yet; failures show here.") }));
    main.appendChild(listBox); main.appendChild(actBox);
    M.api("GET", "/api/v1/dumps/roles").then(function (r) { ROLES = r.data || []; });
    load();
    var n = M.qs("new");
    if (n && i.can.manage) edit(null, { kind: n, target: M.qs("target") || "" });
  }, function (e) { main.appendChild(el("div", { "class": "notice bad", text: e.message })); });

  function load() {
    return Promise.all([M.api("GET", "/api/v1/dumps/schedules?owner=" + owner), M.api("GET", "/api/v1/dumps/runs?limit=40&owner=" + (owner === "mine" ? "mine" : "visible"))]).then(function (res) {
      SCHED = res[0].data || [];
      drawList(); drawActivity(res[1].data || []);
      var live = SCHED.some(function (s) { return s.live_status; });
      clearTimeout(poll);
      poll = setTimeout(load, live ? 4000 : 30000);
    }, function (e) { M.clear(listBox).appendChild(el("div", { "class": "notice bad", text: e.message })); });
  }

  function cronText(s) {
    var p = s.cron_expr.split(/\s+/);
    if (p.length !== 5) return s.cron_expr;
    var hm = /^\d+$/.test(p[0]) && /^\d+$/.test(p[1]) ? ("0" + p[1]).slice(-2) + ":" + ("0" + p[0]).slice(-2) : null;
    if (/^\*\/\d+$/.test(p[0]) && p[1] === "*") return "every " + p[0].slice(2) + " min";
    if (/^\d+$/.test(p[0]) && p[1] === "*") return "hourly at :" + ("0" + p[0]).slice(-2);
    if (hm && p[2] === "*" && p[3] === "*" && p[4] === "*") return "daily " + hm;
    if (hm && p[2] === "*" && p[3] === "*") return p[4].split(",").map(function (d) { return (DOW.filter(function (x) { return x[0] === d; })[0] || [0, d])[1]; }).join(", ") + " " + hm;
    if (hm && /^\d+$/.test(p[2]) && p[3] === "*") return "monthly on " + p[2] + " at " + hm;
    return s.cron_expr;
  }

  function statusChip(st) {
    var cls = { DONE: "ok", FAILED: "bad", RUNNING: "", QUEUED: "", SKIPPED: "grey", STOPPED: "warn" }[st];
    return st ? el("span", { "class": "chip " + (cls === undefined ? "grey" : cls), text: st }) : el("span", { "class": "muted", text: "never run" });
  }

  function drawList() {
    M.clear(listBox);
    var q = el("input", { type: "search", placeholder: "Search schedules", value: listBox.dataset.q || "" });
    var who = el("select", { "aria-label": "Whose schedules" }, [el("option", { value: "visible", text: "All I can see" }), el("option", { value: "mine", text: "Mine" })]);
    who.value = owner;
    who.addEventListener("change", function () { owner = who.value; load(); });
    listBox.appendChild(el("div", { "class": "row" }, [el("h2", { style: "margin:0", text: "Schedules" }), el("span", { "class": "spacer" }), q, who,
      INST.can.manage ? el("button", { type: "button", "class": "primary", text: "New schedule", onclick: function () { edit(null); } }) : null]));
    var list = el("div", { "class": "list", style: "margin-top:8px" });
    listBox.appendChild(list);
    function draw() {
      listBox.dataset.q = q.value;
      M.clear(list);
      var s = q.value.trim().toLowerCase();
      var rows = SCHED.filter(function (x) { return !s || (x.name + " " + x.target + " " + x.owner_emp_code + " " + x.folder).toLowerCase().indexOf(s) >= 0; });
      if (!rows.length) list.appendChild(el("div", { "class": "muted", style: "padding:8px 4px", text: "No schedules yet." }));
      rows.forEach(function (x) { list.appendChild(item(x)); });
    }
    q.addEventListener("input", draw);
    draw();
  }

  function item(x) {
    var mine = x.owner_emp_code === INST.emp_code, canChange = INST.can.admin || (INST.can.manage && mine);
    var live = x.live_status;
    var runsBox = el("div", { style: "flex:1 1 100%" });
    var row = el("div", { "class": "item" }, [
      el("div", { style: "flex:1 1 340px;min-width:0" }, [
        el("div", { "class": "row", style: "gap:6px" }, [el("span", { "class": "name", text: x.name }), x.enabled ? null : el("span", { "class": "chip grey", text: "disabled" }),
          live ? el("span", { "class": "chip", html: '<span class="spin"></span> ' + live + (x.live_rows ? " · " + M.fmtNum(x.live_rows) + " rows" : "") }) : statusChip(x.last_status)]),
        el("div", { "class": "muted small", text: { master: "Master", mis_report: "MIS report", mis_book: "MIS workbook" }[x.kind] + ": " + x.target + " · " + x.format.toUpperCase() +
          " · " + cronText(x) + " · next " + (x.next_run_at ? M.fmtDate(x.next_run_at) : "-") + " · owner " + x.owner_emp_code }),
        el("div", { "class": "muted small", text: x.folder + (x.live_progress ? " · " + x.live_progress : "") })]),
      INST.can.run && !live ? el("button", { type: "button", text: "Run now", onclick: function (e) { act(e.target, "run-now", x); } }) : null,
      INST.can.run && live ? el("button", { type: "button", "class": "danger", text: "Stop", onclick: function (e) { act(e.target, "stop", x); } }) : null,
      el("button", { type: "button", text: openRuns[x.id] ? "Hide runs" : "Runs", onclick: function () { openRuns[x.id] = !openRuns[x.id]; drawList(); } }),
      canChange ? el("button", { type: "button", text: "Edit", onclick: function () { edit(x); } }) : null,
      INST.can.manage ? el("button", { type: "button", text: "Duplicate", onclick: function () { var c = JSON.parse(JSON.stringify(x)); c.name = x.name + " (copy)"; delete c.id; edit(null, c); } }) : null,
      canChange ? el("button", { type: "button", text: x.enabled ? "Disable" : "Enable", onclick: function (e) { act(e.target, "toggle", x); } }) : null,
      canChange ? el("button", { type: "button", "class": "danger", text: "Delete", onclick: function () { del(x); } }) : null,
      runsBox]);
    if (openRuns[x.id]) loadRuns(x, runsBox);
    return row;
  }

  function act(btn, what, x) {
    M.busy(btn, true);
    M.api("POST", "/api/v1/dumps/schedules/" + x.id + "/" + what, {}).then(function () {
      M.toast({ "run-now": "Queued - the worker starts it within about 20 seconds.", stop: "Stopping…", toggle: "Updated." }[what]);
      load();
    }, function (e) { M.busy(btn, false); M.toast(e.message, true); });
  }

  function del(x) {
    var m = M.modal("Delete schedule", el("p", { text: "Delete \"" + x.name + "\" and its run history? Files already written stay in the folder." }), [
      el("button", { type: "button", text: "Cancel", onclick: function () { m.close(); } }),
      el("button", { type: "button", "class": "danger", text: "Delete", onclick: function () {
        M.api("DELETE", "/api/v1/dumps/schedules/" + x.id).then(function () { m.close(); M.toast("Deleted."); load(); }, function (e) { M.toast(e.message, true); }); } })]);
  }

  var RUN_COLS = ["id", "status", "trigger_type", "triggered_by", "queued_at", "duration", "files", "rows_written", "host", "detail"];
  function runRows(runs) {
    return runs.map(function (r) {
      return { id: r.id, status: r.status + (r.stale ? " (no heartbeat)" : ""), trigger_type: r.trigger_type, triggered_by: r.triggered_by || "", queued_at: M.fmtDate(r.queued_at),
               duration: r.duration_ms ? Math.round(r.duration_ms / 1000) + " s" : "", files: r.files, rows_written: r.rows_written, host: r.host || "",
               detail: r.error_msg || (r.file_paths || []).join(" | ") || r.progress || "", schedule: r.schedule_name };
    });
  }

  function loadRuns(x, box) {
    box.appendChild(el("div", { "class": "muted small", text: "Loading runs…" }));
    M.api("GET", "/api/v1/dumps/schedules/" + x.id + "/runs").then(function (r) {
      M.clear(box);
      M.table(box, {}).render(RUN_COLS, runRows(r.data || []));
      if ((r.mail || []).length) box.appendChild(el("div", { "class": "muted small", style: "margin-top:4px", text: "Failure mails: " + r.mail.map(function (m) { return M.fmtDate(m.created_at) + " " + m.status + (m.mail_to ? " to " + m.mail_to : "") + (m.error_msg ? " (" + m.error_msg + ")" : ""); }).join(" · ") }));
    }, function (e) { M.clear(box).appendChild(el("div", { "class": "notice bad", text: e.message })); });
  }

  function drawActivity(runs) {
    M.clear(actBox).appendChild(el("h2", { text: "Recent activity" }));
    var b = el("div"); actBox.appendChild(b);
    M.table(b, {}).render(["schedule"].concat(RUN_COLS), runRows(runs));
  }

  // ── create / edit ───────────────────────────────────────────────────────────────────────────────
  function freqFromCron(cron, runOnce) {
    var p = (cron || "0 8 * * *").split(/\s+/), f = { mode: "cron", cron: cron || "0 8 * * *", every: 30, minute: 0, time: "08:00", days: ["1"], dom: 1, onceAt: "" };
    if (p.length !== 5) return f;
    var hm = /^\d+$/.test(p[0]) && /^\d+$/.test(p[1]) ? ("0" + p[1]).slice(-2) + ":" + ("0" + p[0]).slice(-2) : null;
    if (hm) f.time = hm;
    if (runOnce && hm && /^\d+$/.test(p[2]) && /^\d+$/.test(p[3])) { f.mode = "once"; return f; }
    if (/^\*\/\d+$/.test(p[0]) && p[1] === "*" && p[2] === "*") { f.mode = "minutes"; f.every = +p[0].slice(2); }
    else if (/^\d+$/.test(p[0]) && p[1] === "*" && p[2] === "*") { f.mode = "hourly"; f.minute = +p[0]; }
    else if (hm && p[2] === "*" && p[3] === "*" && p[4] === "*") f.mode = "daily";
    else if (hm && p[2] === "*" && p[3] === "*") { f.mode = "weekly"; f.days = p[4].split(","); }
    else if (hm && /^\d+$/.test(p[2]) && p[3] === "*" && p[4] === "*") { f.mode = "monthly"; f.dom = +p[2]; }
    return f;
  }

  function edit(x, preset) {
    var s = x || preset || {};
    var cfg = s.config || {};
    var name = el("input", { type: "text", maxlength: "120", value: s.name || "" });
    var kind = el("select", {}, INST.kinds.map(function (k) { return el("option", { value: k.code, selected: k.code === (s.kind || "master"), text: k.name }); }));
    var targetBox = el("div");
    var fmt = el("select", {}, [el("option", { value: "xlsx", text: "Excel (.xlsx)" }), el("option", { value: "csv", text: "CSV" })]);
    fmt.value = s.format || "xlsx";
    var folder = el("input", { type: "text", maxlength: "400", value: s.folder || "", placeholder: "\\\\file\\01-CENTRAL- REPORTS\\...", style: "min-width:340px" });
    var fileName = el("input", { type: "text", maxlength: "120", value: s.file_name || "", placeholder: "default: the schedule name" });
    var desc = el("input", { type: "text", maxlength: "1000", value: s.description || "" });
    var rule = el("select", {}, INST.date_rules.map(function (r) { return el("option", { value: r, selected: r === (cfg.dateRule || "yesterday"), text: RULES[r] || r }); }));
    var fFrom = el("input", { type: "date", value: cfg.from_date || "" }), fTo = el("input", { type: "date", value: cfg.to_date || "" });
    var fixedBox = el("div", { "class": "row" }, [el("label", { "class": "f" }, ["From", fFrom]), el("label", { "class": "f" }, ["To", fTo])]);
    var misBox = el("div", { "class": "row" }, [el("label", { "class": "f" }, ["Date range of the report", rule]), fixedBox]);
    var mFilters = el("textarea", { placeholder: '[{"col":"ZONE","op":"eq","value":"ZONE-1"}]', text: cfg.filters ? JSON.stringify(cfg.filters) : "" });
    var masterBox = el("label", { "class": "f" }, ["Master filters (optional, as in the master page: JSON list of {col, op, value})", mFilters]);
    var timeout = el("input", { type: "number", min: "1", max: "600", value: s.timeout_min || "", placeholder: "60", style: "width:90px" });
    var startAt = el("input", { type: "datetime-local", value: s.start_at ? s.start_at.slice(0, 16) : "" });
    var endAt = el("input", { type: "datetime-local", value: s.end_at ? s.end_at.slice(0, 16) : "" });
    var enabled = el("input", { type: "checkbox", checked: s.enabled !== false });
    var roles = M.checklist([{ items: ROLES.map(function (r) { return { value: r.code, label: r.name + " (" + r.code + ")" }; }) }], s.allowed_roles || [], { name: "Roles", height: "140px" });
    var ownerIn = INST.can.admin ? el("input", { type: "text", value: s.owner_emp_code || "", placeholder: "employee code (blank = me)", style: "width:200px" }) : null;

    // frequency builder
    var fr = freqFromCron(s.cron_expr, s.run_once);
    var mode = el("select", {}, [["once", "Once"], ["minutes", "Every N minutes"], ["hourly", "Hourly"], ["daily", "Daily"], ["weekly", "Weekly"], ["monthly", "Monthly"], ["cron", "Advanced (cron)"]]
      .map(function (m) { return el("option", { value: m[0], selected: m[0] === fr.mode, text: m[1] }); }));
    var every = el("input", { type: "number", min: "5", max: "720", value: fr.every, style: "width:80px" });
    var minute = el("input", { type: "number", min: "0", max: "59", value: fr.minute, style: "width:80px" });
    var time = el("input", { type: "time", value: fr.time });
    var dom = el("input", { type: "number", min: "1", max: "28", value: fr.dom, style: "width:80px" });
    var onceAt = el("input", { type: "datetime-local" });
    var cron = el("input", { type: "text", value: fr.cron, style: "width:200px", placeholder: "min hour day month weekday" });
    var dayBoxes = DOW.map(function (d) { return el("label", { "class": "row" }, [el("input", { type: "checkbox", value: d[0], checked: fr.days.indexOf(d[0]) >= 0 }), d[1]]); });
    var freqParts = { minutes: el("label", { "class": "f" }, ["Every (minutes)", every]), hourly: el("label", { "class": "f" }, ["At minute", minute]),
      time: el("label", { "class": "f" }, ["At (IST)", time]), days: el("div", { "class": "row" }, dayBoxes), dom: el("label", { "class": "f" }, ["Day of month", dom]),
      once: el("label", { "class": "f" }, ["On (IST)", onceAt]), cron: el("label", { "class": "f" }, ["Cron (IST)", cron]) };
    var preview = el("div", { "class": "muted small" });
    function cronNow() {
      var hm = (time.value || "08:00").split(":");
      var h = +hm[0], mi = +hm[1];
      switch (mode.value) {
        case "minutes": return "*/" + Math.max(5, +every.value || 30) + " * * * *";
        case "hourly": return (+minute.value || 0) + " * * * *";
        case "daily": return mi + " " + h + " * * *";
        case "weekly": return mi + " " + h + " * * " + (dayBoxes.map(function (l) { var c = l.querySelector("input"); return c.checked ? c.value : null; }).filter(Boolean).join(",") || "1");
        case "monthly": return mi + " " + h + " " + (+dom.value || 1) + " * *";
        case "once": { var d = onceAt.value ? new Date(onceAt.value) : null; return d ? d.getMinutes() + " " + d.getHours() + " " + d.getDate() + " " + (d.getMonth() + 1) + " *" : mi + " " + h + " * * *"; }
        default: return cron.value.trim();
      }
    }
    function showFreq() {
      var m = mode.value;
      freqParts.minutes.classList.toggle("hidden", m !== "minutes"); freqParts.hourly.classList.toggle("hidden", m !== "hourly");
      freqParts.time.classList.toggle("hidden", ["daily", "weekly", "monthly"].indexOf(m) < 0); freqParts.days.classList.toggle("hidden", m !== "weekly");
      freqParts.dom.classList.toggle("hidden", m !== "monthly"); freqParts.once.classList.toggle("hidden", m !== "once"); freqParts.cron.classList.toggle("hidden", m !== "cron");
      var c = cronNow();
      M.api("POST", "/api/v1/dumps/preview", { cron_expr: c, timezone: "Asia/Kolkata", start_at: startAt.value ? new Date(startAt.value).toISOString() : null,
                                               end_at: endAt.value ? new Date(endAt.value).toISOString() : null }).then(function (r) {
        preview.textContent = "cron " + c + " · next: " + (r.next.length ? r.next.map(M.fmtDate).join(" · ") : "never");
      }, function (e) { preview.textContent = e.message; });
    }
    [mode, every, minute, time, dom, onceAt, cron, startAt, endAt].concat(dayBoxes.map(function (l) { return l.querySelector("input"); }))
      .forEach(function (i) { i.addEventListener("change", showFreq); });

    // target picker per kind
    var targetSel = null, bookPick = null, sheetsIn = null;
    function showKind() {
      var k = kind.value;
      misBox.classList.toggle("hidden", k === "master"); masterBox.classList.toggle("hidden", k !== "master");
      fixedBox.classList.toggle("hidden", rule.value !== "fixed");
      if (k === "mis_book") { fmt.value = "xlsx"; fmt.disabled = true; } else fmt.disabled = false;
      M.clear(targetBox).appendChild(el("div", { "class": "muted small", text: "Loading…" }));
      M.api("GET", "/api/v1/dumps/targets?kind=" + k).then(function (r) {
        M.clear(targetBox);
        var items = r.data || [];
        if (k === "mis_book") {
          var cur = (s.kind === "mis_book" ? s.target : "").split(",").filter(Boolean);
          bookPick = M.checklist([{ items: items.map(function (t) { return { value: t.code, label: t.name + " (" + t.code + ")" }; }) }], cur, { name: "Reports", height: "200px" });
          targetBox.appendChild(el("div", { "class": "f" }, [el("span", { "class": "small muted", text: "Reports (one sheet each, in list order)" }), bookPick.el]));
          targetSel = null;
        } else {
          targetSel = el("select", { style: "min-width:320px" }, [el("option", { value: "", text: "Pick…" })].concat(items.map(function (t) {
            return el("option", { value: t.code, selected: t.code === s.target, text: t.name + " (" + t.code + ")" }); })));
          if (s.target && !items.some(function (t) { return t.code === s.target; })) targetSel.appendChild(el("option", { value: s.target, selected: true, text: s.target }));
          targetBox.appendChild(el("label", { "class": "f" }, [k === "master" ? "Master" : "MIS report", targetSel]));
          bookPick = null;
        }
      }, function (e) { M.clear(targetBox).appendChild(el("div", { "class": "notice bad", text: e.message })); });
    }
    kind.addEventListener("change", showKind); rule.addEventListener("change", showKind);

    var bodyEl = el("div", { style: "display:flex;flex-direction:column;gap:10px" }, [
      el("div", { "class": "grid2" }, [el("label", { "class": "f" }, ["Name", name]), el("label", { "class": "f" }, ["What to dump", kind])]),
      targetBox, misBox, masterBox,
      el("div", { "class": "grid2" }, [el("label", { "class": "f" }, ["Folder (files go to <folder>\\YYYY_MM_DD\\)", folder]), el("label", { "class": "f" }, ["File name (no extension)", fileName]),
        el("label", { "class": "f" }, ["Format", fmt]), el("label", { "class": "f" }, ["Description", desc])]),
      el("h3", { style: "margin:4px 0 0", text: "When" }),
      el("div", { "class": "row" }, [el("label", { "class": "f" }, ["Frequency", mode]), freqParts.minutes, freqParts.hourly, freqParts.time, freqParts.dom, freqParts.once, freqParts.cron]),
      freqParts.days, preview,
      el("div", { "class": "row" }, [el("label", { "class": "f" }, ["Start (optional)", startAt]), el("label", { "class": "f" }, ["End (optional)", endAt]),
        el("label", { "class": "f" }, ["Time limit (min)", timeout]), el("label", { "class": "row" }, [enabled, "Enabled"]), ownerIn ? el("label", { "class": "f" }, ["Owner", ownerIn]) : null]),
      el("details", { "class": "sect" }, [el("summary", { text: "Also visible to roles (besides the owner)" }), el("div", {}, roles.el)])]);

    var saveBtn = el("button", { type: "button", "class": "primary", text: x ? "Save" : "Create" });
    var m = M.modal(x ? "Edit schedule" : "New schedule", bodyEl, [el("button", { type: "button", text: "Cancel", onclick: function () { m.close(); } }), saveBtn]);
    showKind(); showFreq();

    saveBtn.addEventListener("click", function () {
      var k = kind.value, target = bookPick ? bookPick.get().join(",") : (targetSel ? targetSel.value : "");
      if (!name.value.trim() || !target || !folder.value.trim()) { M.toast("Name, what to dump and the folder are required.", true); return; }
      var conf = {};
      if (k === "master") {
        if (mFilters.value.trim()) { try { conf.filters = JSON.parse(mFilters.value); } catch (e) { M.toast("Master filters are not valid JSON.", true); return; } }
      } else {
        conf.dateRule = rule.value;
        if (rule.value === "fixed") { conf.from_date = fFrom.value; conf.to_date = fTo.value; }
        if (k === "mis_book" && s.config && s.config.sheets) conf.sheets = s.config.sheets;
      }
      var b = { name: name.value.trim(), kind: k, target: target, config: conf, format: k === "mis_book" ? "xlsx" : fmt.value, folder: folder.value.trim(),
                file_name: fileName.value.trim() || null, cron_expr: cronNow(), timezone: "Asia/Kolkata", enabled: enabled.checked, description: desc.value || null,
                start_at: startAt.value ? new Date(startAt.value).toISOString() : null, end_at: endAt.value ? new Date(endAt.value).toISOString() : null,
                run_once: mode.value === "once", timeout_min: timeout.value ? +timeout.value : null, allowed_roles: roles.get(),
                owner_emp_code: ownerIn && ownerIn.value.trim() ? ownerIn.value.trim() : null };
      M.busy(saveBtn, true, "Saving…");
      M.api(x ? "PUT" : "POST", "/api/v1/dumps/schedules" + (x ? "/" + x.id : ""), b).then(function () { m.close(); M.toast("Saved."); load(); },
        function (e) { M.busy(saveBtn, false); M.toast(e.message, true); });
    });
  }
})();

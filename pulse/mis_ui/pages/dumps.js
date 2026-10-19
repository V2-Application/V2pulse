// Dump Scheduler (DFS /dump-scheduler, DumpScheduler.jsx) in the MIS workspace: schedules that write a V2Pulse master,
// MIS reports or one of the DFS report kinds (Day Wise Trend, Inventory report, Bin Wise, TNA) to CSV / Excel in a
// folder on a timetable, plus the MIS day-wise load (no files). Schedule list with run / stop / enable / edit /
// duplicate / delete, run history per schedule (with failure mails), Recent Activity grouped by day, 4 s polling while
// anything runs, and the create / edit form (kind-specific fields, frequency builder with a 5-run preview, start / end,
// time limit, roles, owner). Route /mis/dumps; ?new=<kind>&target=<x> opens the New schedule form prefilled.
// API: /api/v1/dumps/{instance, targets?kind=, roles, users, schedules, schedules/{id}[/toggle|run-now|stop|runs], runs,
// preview} (pulse/modules/dumps.py); option lists for the DFS kinds come from the copied engine routes under /api/v1/mis.
(function () {
  "use strict";
  var M = window.MIS, el = M.el;
  var API = "/api/v1/dumps", IST = "Asia/Kolkata";

  var KIND_LABEL = { master: "Master", mis_report: "MIS report", mis_book: "MIS workbook", day_trend: "Day Wise Trend",
                     inv_report: "Inventory report", bin_wise: "Bin Wise", tna: "TNA report", mis_day_load: "MIS day-wise load" };
  var KIND_HINT = {
    master: "A V2Pulse master, as its owner sees it, with optional filters.",
    mis_report: "One saved MIS report; optionally one file per level value.",
    mis_book: "Several saved MIS reports, a sheet each, in one Excel workbook.",
    day_trend: "The Day Wise Trend workbook over a rolling window, or a saved Day Wise Trend Report.",
    inv_report: "A saved Inventory report as of the latest stock reading or yesterday.",
    bin_wise: "The latest Bin Wise snapshot, whole or one file per value.",
    tna: "The TNA extract as at its last load (what is pending).",
    mis_day_load: "Runs the two day-wise MIS load procedures. Writes no files."
  };
  var KIND_ORDER = ["master", "mis_report", "mis_book", "day_trend", "inv_report", "bin_wise", "tna", "mis_day_load"];
  var MIS_RULES = [["yesterday", "Yesterday"], ["last7", "Last 7 days"], ["last30", "Last 30 days"], ["mtd", "Month to date"], ["last_month", "Last month (whole)"]];
  var INV_RULES = [["latest", "Latest stock reading"], ["yesterday", "Yesterday"]];
  var BIN_COLS = ["WERKS", "FLR", "LGTYP", "SEC", "GND", "AGING"];
  var DT_MAX_DAYS = 31, DT_ARTICLE_DIMS = ["VAR_ART", "GEN_ART_NUMBER", "GEN_ART_DESC", "COLOR", "SIZE_1"];
  var DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var FREQS = [["once", "Once"], ["minutes", "Every N min"], ["hourly", "Hourly"], ["daily", "Daily"], ["weekly", "Weekly"], ["monthly", "Monthly"], ["advanced", "Advanced (cron)"]];
  var NO_FOLDER = { mis_day_load: true };

  // Icons the shared set does not have yet (see the report: worth adding to core.js).
  var XP = { stop: "M7 7h10v10H7z", power: "M12 3v9 M6.4 6.6a8 8 0 1 0 11.2 0", history: "M3 12a9 9 0 1 0 3-6.7 M3 4v5h5 M12 7v5l3 2",
             folder: "M3 6h6l2 2h10v11H3z", activity: "M3 12h4l3-8 4 16 3-8h4" };
  function ico(name, size) {
    if (!XP[name]) return M.icon(name, size || 16);
    var s = M.icon("x", size || 16); s.firstChild.setAttribute("d", XP[name]); return s;
  }

  // ── page CSS (scoped dk-*; a candidate for mis.css) ───────────────────────────────────────────────
  function css() {
    if (document.getElementById("dk-css")) return;
    var s = document.createElement("style"); s.id = "dk-css";
    s.textContent = [
      ".dk-list{display:flex;flex-direction:column}",
      ".dk-head,.dk-row{display:grid;grid-template-columns:36px minmax(0,2.7fr) minmax(0,.9fr) minmax(0,1.25fr) minmax(0,1.15fr) 196px;gap:12px;align-items:start;padding:10px 18px;border-bottom:1px solid var(--line-2)}",
      ".dk-head{position:sticky;top:0;z-index:2;background:var(--head);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600;padding-top:8px;padding-bottom:8px}",
      ".dk-row:hover{background:var(--hover)} .dk-row.off .dk-dim{opacity:.55}",
      ".dk-name{font-weight:600;display:flex;gap:6px;align-items:center;min-width:0} .dk-name .t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".dk-sub{display:flex;gap:6px;align-items:flex-start;margin-top:4px;min-width:0} .dk-sub .s{font-size:12px;color:var(--muted);line-height:1.4;min-width:0}",
      ".dk-kind{flex:none;font-size:10.5px;font-weight:600;padding:1px 7px;border-radius:5px;background:var(--surface-3);color:var(--fg-2);white-space:nowrap;letter-spacing:.02em}",
      ".dk-tag{font-size:10px;font-weight:700;padding:1px 6px;border-radius:5px;background:color-mix(in srgb,var(--s4) 15%,var(--surface));color:var(--s4);letter-spacing:.04em;flex:none}",
      ".dk-l2{display:block;font-size:11.5px;color:var(--muted);margin-top:2px} .dk-l2.warn{color:var(--warn)}",
      ".dk-cell{font-size:12.8px;min-width:0} .dk-dot-u{border-bottom:1px dotted var(--muted)}",
      ".dk-you{display:inline-block;padding:0 6px;border-radius:5px;background:var(--brand-soft);color:var(--brand);font-weight:600;font-size:12px}",
      ".dk-acts{display:flex;gap:2px;justify-content:flex-end;flex-wrap:wrap}",
      ".dk-ib{border:0;background:transparent;padding:6px;border-radius:7px;color:var(--fg-2);cursor:pointer;line-height:0}",
      ".dk-ib:hover{background:var(--surface-3)} .dk-ib:disabled{opacity:.35;cursor:default;background:transparent}",
      ".dk-ib.go{color:var(--ok)} .dk-ib.stop{color:var(--warn)} .dk-ib.on{color:var(--brand-2)} .dk-ib.del{color:var(--bad)} .dk-ib[aria-expanded=true]{color:var(--brand-2);background:var(--brand-soft)}",
      ".dk-exp{padding:12px 18px 16px 66px;background:var(--surface-2);border-bottom:1px solid var(--line-2);display:flex;flex-direction:column;gap:10px}",
      ".dk-kv{display:flex;flex-wrap:wrap;gap:4px 22px;font-size:12.3px} .dk-kv span b{color:var(--muted);font-weight:600;margin-right:6px}",
      ".dk-t{width:100%;border-collapse:collapse;font-size:12.3px;background:var(--surface);border:1px solid var(--line-2);border-radius:8px}",
      ".dk-t th{text-align:left;color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.05em;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap;background:var(--head)}",
      ".dk-t td{padding:5px 8px;border-bottom:1px solid var(--line-2);vertical-align:top} .dk-t td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}",
      ".dk-tw{overflow:auto;max-height:420px}",
      ".dk-err{margin-top:4px;max-width:96ch;border-radius:7px;background:var(--bad-soft);color:var(--bad);padding:4px 8px;font-size:11.5px;white-space:pre-wrap;word-break:break-word;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;cursor:pointer}",
      ".dk-err.open{display:block;cursor:text}",
      ".dk-host{display:inline-block;padding:0 6px;border-radius:5px;background:var(--surface-3);color:var(--fg-2);font-size:11px}",
      ".dk-host.other{background:color-mix(in srgb,var(--s4) 15%,var(--surface));color:var(--s4)}",
      ".dk-feed{padding:4px 18px 12px}",
      ".dk-dayh{position:sticky;top:0;z-index:1;background:var(--surface);padding:8px 0 4px;font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);font-weight:600}",
      ".dk-dayh span{font-weight:400;margin-left:6px;letter-spacing:0;text-transform:none}",
      ".dk-feed ul{list-style:none;margin:0 0 4px 7px;padding:0;border-left:1px solid var(--line)}",
      ".dk-feed li{position:relative;display:flex;gap:12px;align-items:flex-start;padding:7px 0 7px 16px}",
      ".dk-dot{position:absolute;left:-6px;top:12px;width:11px;height:11px;border-radius:50%;box-shadow:0 0 0 2px var(--surface);background:var(--line)}",
      ".dk-dot.DONE{background:var(--ok)} .dk-dot.FAILED{background:var(--bad)} .dk-dot.RUNNING{background:var(--s8)} .dk-dot.QUEUED{background:var(--s3)} .dk-dot.STOPPED{background:var(--warn)}",
      ".dk-dot.live{animation:dkp 1.2s ease-in-out infinite} @keyframes dkp{50%{opacity:.35}}",
      ".dk-ft{flex:1;min-width:0} .dk-ft .h{display:flex;gap:8px;align-items:center;font-weight:600;font-size:13px;min-width:0} .dk-ft .h .t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".dk-meta{display:flex;flex-wrap:wrap;gap:2px 6px;align-items:center;font-size:11.8px;color:var(--muted);margin-top:2px} .dk-meta .sep{opacity:.5}",
      ".dk-when{flex:none;text-align:right;font-size:12px;color:var(--fg-2);font-variant-numeric:tabular-nums;white-space:nowrap} .dk-when small{display:block;color:var(--muted);font-size:11px}",
      ".dk-kinds{display:grid;grid-template-columns:repeat(auto-fill,minmax(196px,1fr));gap:8px}",
      ".dk-kinds button{text-align:left;border:1px solid var(--line);background:var(--surface);border-radius:10px;padding:8px 11px;cursor:pointer;font:inherit;color:var(--fg)}",
      ".dk-kinds button:hover{border-color:color-mix(in srgb,var(--brand-2) 45%,var(--line))}",
      ".dk-kinds button[aria-pressed=true]{border-color:var(--brand-2);background:var(--brand-soft);box-shadow:inset 0 0 0 1px var(--brand-2)}",
      ".dk-kinds button:disabled{opacity:.45;cursor:not-allowed}",
      ".dk-kinds b{display:block;font-size:13px} .dk-kinds span{display:block;font-size:11.5px;color:var(--muted);line-height:1.35;margin-top:2px}",
      ".dk-g2{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;align-items:start}",
      ".dk-f{display:flex;flex-direction:column;gap:4px;min-width:0} .dk-f label.f > input,.dk-f label.f > select,.dk-f label.f > textarea{width:100%}",
      ".dk-lab{display:flex;flex-direction:column;gap:4px;min-width:0} .dk-lab > span:first-child{font-size:11.5px;color:var(--muted);font-weight:600;letter-spacing:.03em;text-transform:uppercase}",
      ".dk-hint{font-size:11.5px;color:var(--muted);line-height:1.4} .dk-hint.warn{color:var(--warn)}",
      ".dk-box{background:var(--surface-2);border:1px solid var(--line-2);border-radius:10px;padding:12px;display:flex;flex-direction:column;gap:10px}",
      ".dk-box > summary{cursor:pointer;font-weight:600;font-size:13px;color:var(--fg-2)} details.dk-box:not([open]){gap:0}",
      ".dk-role{border:1px solid var(--line);background:var(--surface);border-radius:999px;padding:3px 11px;font:inherit;font-size:12.3px;cursor:pointer;color:var(--fg-2)}",
      ".dk-role[aria-pressed=true]{background:var(--brand-2);border-color:var(--brand-2);color:#fff}",
      ".dk-prev{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:2px;font-size:12.5px;font-variant-numeric:tabular-nums}",
      ".dk-prev li span{color:var(--muted);margin-left:8px}",
      ".dk-book{display:flex;flex-direction:column;gap:6px} .dk-book .r{display:grid;grid-template-columns:22px minmax(0,1fr) 200px auto;gap:8px;align-items:center;font-size:13px}",
      ".dk-book .r .i{color:var(--muted);text-align:right;font-variant-numeric:tabular-nums}",
      ".dk-cron{font-family:Consolas,monospace;font-size:12.5px;background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:1px 7px}",
      ".dk-empty{padding:34px 18px;text-align:center;color:var(--muted)}",
      "@media (max-width:1100px){.dk-head{display:none}.dk-row{grid-template-columns:36px minmax(0,1fr)}.dk-row>.dk-cell,.dk-row>.dk-acts{grid-column:2}.dk-acts{justify-content:flex-start}.dk-exp{padding-left:18px}.dk-book .r{grid-template-columns:22px minmax(0,1fr)}}"
    ].join("\n");
    document.head.appendChild(s);
  }

  // ── time helpers (everything is shown and entered in IST, the schedules' time zone) ────────────────
  var ISTF = new Intl.DateTimeFormat("en-CA", { timeZone: IST, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  function istParts(d) { var o = {}; ISTF.formatToParts(d).forEach(function (p) { o[p.type] = p.value; }); return o; }
  function istInput(iso) { if (!iso) return ""; var d = new Date(iso); if (isNaN(d)) return ""; var p = istParts(d); return p.year + "-" + p.month + "-" + p.day + "T" + p.hour + ":" + p.minute; }
  function fromIstInput(v) { return v ? v + ":00+05:30" : null; }
  function dayKey(iso) { if (!iso) return ""; var d = new Date(iso); if (isNaN(d)) return ""; var p = istParts(d); return p.year + "-" + p.month + "-" + p.day; }
  function todayKey() { return dayKey(new Date().toISOString()); }
  function dayLabel(k) {
    if (!k) return "Unknown day";
    var t = todayKey(); if (k === t) return "Today"; if (k === M.addDays(t, -1)) return "Yesterday";
    return new Date(k + "T00:00:00Z").toLocaleDateString("en-IN", { timeZone: "UTC", weekday: "short", day: "2-digit", month: "short" });
  }
  function fmtT(iso) { if (!iso) return "–"; var d = new Date(iso); return isNaN(d) ? String(iso) : d.toLocaleString("en-IN", { timeZone: IST, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }); }
  function fmtLong(iso) { if (!iso) return "–"; var d = new Date(iso); return isNaN(d) ? String(iso) : d.toLocaleString("en-IN", { timeZone: IST, weekday: "short", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }); }
  function timeOnly(iso) { if (!iso) return "–"; var d = new Date(iso); return isNaN(d) ? "–" : d.toLocaleTimeString("en-IN", { timeZone: IST, hour: "numeric", minute: "2-digit" }); }
  function relTime(iso) {
    if (!iso) return "–"; var t = new Date(iso).getTime(); if (isNaN(t)) return "–";
    var diff = t - Date.now(), m = Math.round(Math.abs(diff) / 60000);
    var say = m < 1 ? "just now" : m < 60 ? m + " min" : m < 60 * 36 ? Math.round(m / 60) + " h" : Math.round(m / 1440) + " d";
    return say === "just now" ? say : diff > 0 ? "in " + say : say + " ago";
  }
  function fmtDur(ms) { if (ms === null || ms === undefined) return "–"; var s = Math.round(ms / 1000); return s < 60 ? s + "s" : s < 3600 ? Math.floor(s / 60) + "m " + (s % 60) + "s" : Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m"; }
  function n0(v) { return M.fmt(v || 0, "int"); }
  function pad(n) { return ("0" + n).slice(-2); }
  function clamp(v, lo, hi) { var n = parseInt(v, 10); if (!isFinite(n)) n = lo; return Math.min(hi, Math.max(lo, n)); }
  function clone(o) { return JSON.parse(JSON.stringify(o || {})); }

  // ── cron <-> frequency builder (DFS buildTrigger / describeCron / parseCronToForm) ──────────────────
  function describeCron(expr, once) {
    if (!expr) return "–"; var p = String(expr).trim().split(/\s+/); if (p.length !== 5) return expr;
    var mi = p[0], ho = p[1], dom = p[2], mon = p[3], dow = p[4], m;
    function at(h, n) { return pad(+h) + ":" + pad(+n); }
    if (once) return /^\d+$/.test(dom) && /^\d+$/.test(mon) ? "Once on " + dom + " " + ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][+mon] + " at " + at(ho, mi) : "Once at " + at(ho, mi);
    if ((m = /^\*\/(\d+)$/.exec(mi)) && ho === "*" && dom === "*" && dow === "*") return "Every " + m[1] + " min";
    if ((m = /^\*\/(\d+)$/.exec(ho)) && dom === "*" && dow === "*") return "Every " + m[1] + " h at :" + pad(+mi);
    if (ho === "*" && dom === "*" && dow === "*") return mi === "*" ? "Every minute" : "Hourly at :" + pad(+mi);
    if (dom === "*" && dow === "*" && /^\d+$/.test(ho)) return "Daily at " + at(ho, mi);
    if (dom === "*" && dow !== "*" && /^\d+$/.test(ho)) return "Weekly (" + dow.split(",").map(function (d) { return DOW[+d] || d; }).join(", ") + ") at " + at(ho, mi);
    if (dow === "*" && /^\d+$/.test(dom) && /^\d+$/.test(ho)) return "Monthly on day " + dom + " at " + at(ho, mi);
    return expr;
  }
  function parseCron(expr) {
    var p = String(expr || "").trim().split(/\s+/), m;
    if (p.length !== 5) return { freq: "advanced", rawCron: expr || "" };
    var mi = p[0], ho = p[1], dom = p[2], mon = p[3], dow = p[4], tm = function (h, n) { return pad(+h) + ":" + pad(+n); };
    if ((m = /^\*\/(\d+)$/.exec(mi)) && ho === "*" && dom === "*" && mon === "*" && dow === "*") return { freq: "minutes", everyN: +m[1] };
    if (/^\d+$/.test(mi) && (m = /^\*\/(\d+)$/.exec(ho)) && dom === "*" && mon === "*" && dow === "*") return { freq: "hourly", everyH: +m[1], minute: +mi };
    if (/^\d+$/.test(mi) && ho === "*" && dom === "*" && mon === "*" && dow === "*") return { freq: "hourly", everyH: 1, minute: +mi };
    if (/^\d+$/.test(mi) && /^\d+$/.test(ho) && dom === "*" && mon === "*" && dow === "*") return { freq: "daily", time: tm(ho, mi) };
    if (/^\d+$/.test(mi) && /^\d+$/.test(ho) && dom === "*" && mon === "*" && dow !== "*")
      return { freq: "weekly", time: tm(ho, mi), days: dow.split(",").map(Number).filter(function (n) { return !isNaN(n); }) };
    if (/^\d+$/.test(mi) && /^\d+$/.test(ho) && /^\d+$/.test(dom) && mon === "*" && dow === "*") return { freq: "monthly", time: tm(ho, mi), dom: +dom };
    return { freq: "advanced", rawCron: expr };
  }
  // -> {cron, runOnce, startAt, endAt} (start / end as ISO with the IST offset)
  function buildTrigger(f) {
    var hm = String(f.time || "06:30").split(":"), h = +hm[0] || 0, mi = +hm[1] || 0;
    var out = { cron: "", runOnce: false, startAt: fromIstInput(f.startAt), endAt: fromIstInput(f.endAt) };
    switch (f.freq) {
      case "once":
        out.runOnce = true;
        var mm = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(f.onceAt || "");
        if (mm) {
          out.cron = (+mm[5]) + " " + (+mm[4]) + " " + (+mm[3]) + " " + (+mm[2]) + " *";
          out.startAt = fromIstInput(f.onceAt);
          out.endAt = new Date(new Date(out.startAt).getTime() + 60000).toISOString();   // caps the preview to one firing
        }
        break;
      case "minutes": out.cron = "*/" + clamp(f.everyN, 1, 59) + " * * * *"; break;
      case "hourly": var n = clamp(f.everyH, 1, 23), m2 = clamp(f.minute, 0, 59); out.cron = n === 1 ? m2 + " * * * *" : m2 + " */" + n + " * * *"; break;
      case "daily": out.cron = mi + " " + h + " * * *"; break;
      case "weekly": out.cron = mi + " " + h + " * * " + ((f.days && f.days.length ? f.days.slice().sort() : [1]).join(",")); break;
      case "monthly": out.cron = mi + " " + h + " " + clamp(f.dom, 1, 31) + " * *"; break;
      default: out.cron = String(f.rawCron || "").trim();
    }
    return out;
  }

  // ── small UI helpers ──────────────────────────────────────────────────────────────────────────────
  function iconBtn(icon, label, cls, fn, disabled, extra) {
    var b = el("button", Object.assign({ type: "button", "class": "dk-ib" + (cls ? " " + cls : ""), "aria-label": label, title: label, disabled: !!disabled }, extra || {}), [ico(icon, 16)]);
    if (fn) b.addEventListener("click", function () { fn(b); });
    return b;
  }
  function stChip(st, live) {
    var cls = { DONE: "ok", FAILED: "bad", RUNNING: "", QUEUED: "warn", SKIPPED: "grey", STOPPED: "warn" }[st];
    return el("span", { "class": "chip " + (cls === undefined ? "grey" : cls) + " nowrap", style: { fontSize: "11.5px", padding: "1px 8px", fontWeight: "600" } },
      [st === "RUNNING" || live ? el("span", { "class": "spin", style: { width: "11px", height: "11px" } }) : null, st]);
  }
  // A labelled control; non-form controls (button groups, lists) get a plain caption so a click on it presses nothing.
  function field(label, control, hint, warn) {
    var formy = /^(INPUT|SELECT|TEXTAREA)$/.test(control.tagName);
    return el("div", { "class": "dk-f" }, [formy ? el("label", { "class": "f" }, [label, control]) : el("div", { "class": "dk-lab", role: "group", "aria-label": label }, [el("span", { text: label }), control]),
      hint ? el("div", { "class": "dk-hint" + (warn ? " warn" : ""), text: hint }) : null]);
  }
  function selectEl(opts, value, aria) {
    var s = el("select", { "aria-label": aria || null });
    opts.forEach(function (o) { s.appendChild(el("option", { value: o[0], text: o[1], selected: String(o[0]) === String(value) })); });
    return s;
  }
  function seg(opts, value, onChange, aria) {
    var cur = value, box = el("div", { "class": "seg", role: "group", "aria-label": aria || null }), btns = [];
    opts.forEach(function (o) {
      var b = el("button", { type: "button", "aria-pressed": String(o[0] === cur) }, [o[1]]); btns.push(b);
      b.addEventListener("click", function () { if (b.disabled) return; cur = o[0]; paint(); if (onChange) onChange(cur); });
      box.appendChild(b);
    });
    function paint() { btns.forEach(function (b, i) { b.setAttribute("aria-pressed", String(opts[i][0] === cur)); }); }
    return { el: box, get: function () { return cur; }, set: function (v) { cur = v; paint(); }, buttons: btns };
  }
  // A multi-select with a hint under it. items: () -> Promise<[v | {value,label}]>
  function ms(label, items, selected, hint, opts) {
    var w = M.multiSelect(label, items, selected || [], function () { if (opts && opts.onChange) opts.onChange(w.get()); }, { allText: (opts && opts.allText) || "None" });
    var h = hint ? el("div", { "class": "dk-hint", text: hint }) : null;
    return { el: el("div", { "class": "dk-f" }, [w.el, h]), get: w.get, set: w.set, reset: w.reset, hint: h };
  }
  function errNote(msg) {
    var d = el("div", { "class": "dk-err", text: msg, title: "Click to show the whole message", role: "button", tabindex: "0" });
    function t() { d.classList.toggle("open"); }
    d.addEventListener("click", function () { if (!d.classList.contains("open")) t(); });
    d.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); t(); } });
    return d;
  }
  function countFilters(f) {
    if (Array.isArray(f)) return f.length;
    return Object.keys(f || {}).filter(function (k) { return (f[k] || []).length; }).length;
  }

  // ── option lists (cached for the session) ─────────────────────────────────────────────────────────
  function cached(key, fn) { var k = "dumps:" + key; if (!M.cache[k]) M.cache[k] = fn().catch(function (e) { delete M.cache[k]; throw e; }); return M.cache[k]; }
  var OPT = {
    targets: function (kind) { return cached("t." + kind, function () { return M.get(API + "/targets?kind=" + kind).then(function (r) { return r.data || []; }); }); },
    misReports: function () { return cached("mis", function () { return M.get("/api/v1/mis/report/reports").then(function (r) { return r.reports || []; }); }); },
    dtParams: function () { return cached("dtp", function () { return M.get("/api/v1/mis/day-trend/params"); }); },
    dtReports: function () { return cached("dtr", function () { return M.get("/api/v1/mis/day-trend-report/reports").then(function (r) { return r.reports || []; }); }); },
    invReports: function () { return cached("inv", function () { return M.get("/api/v1/mis/inv-report/reports").then(function (r) { return r.reports || []; }); }); },
    binParams: function () { return cached("bin", function () { return M.get("/api/v1/mis/bin/params"); }); },
    tnaParams: function () { return cached("tna", function () { return M.get("/api/v1/mis/tna/params"); }); },
    stores: function () { return cached("st", function () { return M.get("/api/v1/mis/var-combined/params").then(function (r) { return r.allStores || r.stores || []; }); }); }
  };
  function dimItems(p) { var o = []; ((p && p.dims) || []).forEach(function (g) { (g.cols || []).forEach(function (c) { o.push({ value: c, label: c + "  ·  " + g.label }); }); }); return o; }

  // "Filter on" (dimensions) + a value list per chosen dimension, narrowed by the others (DFS Optional Filters).
  function dimFilters(loadDims, initial, valuesFor, note) {
    var cur = clone(initial), lists = {}, vals = el("div", { "class": "dk-g2" }), cnt = el("span", { "class": "muted" });
    var on = M.multiSelect("Filter on", loadDims, Object.keys(cur), function (dims) {
      var n = {}; dims.forEach(function (d) { n[d] = cur[d] || []; }); cur = n; draw();
    }, { allText: "No filter" });
    function others(d) { var o = {}; Object.keys(cur).forEach(function (k) { if (k !== d && cur[k].length) o[k] = cur[k]; }); return o; }
    function paint() { var n = countFilters(cur); cnt.textContent = n ? " (" + n + ")" : ""; }
    function draw() {
      M.clear(vals); lists = {};
      Object.keys(cur).forEach(function (d) {
        var w = M.multiSelect(d, function () { return valuesFor(d, others(d)); }, cur[d], function (v) {
          cur[d] = v; paint(); Object.keys(lists).forEach(function (k) { if (k !== d) lists[k].reset(); });   // the others' lists cascade
        }, { allText: "All" });
        lists[d] = w; vals.appendChild(w.el);
      });
      paint();
    }
    var clr = el("button", { type: "button", "class": "btn ghost sm" }, [M.icon("x", 13), "Clear"]);
    clr.addEventListener("click", function () { cur = {}; on.set([]); draw(); });
    var box = el("details", { "class": "dk-box", open: countFilters(cur) ? true : null }, [
      el("summary", {}, ["Optional filters", cnt]),
      el("div", { "class": "row" }, [el("span", { "class": "dk-hint", text: note }), el("span", { "class": "spacer" }), clr]),
      el("div", { "class": "dk-g2" }, [on.el]), vals,
      el("div", { "class": "dk-hint warn", text: "Filtered runs write the same file names as an unfiltered dump - give this schedule its own folder so a filtered file is never mistaken for a full one." })]);
    draw();
    return { el: box, get: function () { var o = {}; Object.keys(cur).forEach(function (k) { if (cur[k] && cur[k].length) o[k] = cur[k]; }); return o; } };
  }

  // A <select> filled from a promise of [{code,name}] (keeps an unknown current value visible).
  function asyncSelect(load, value, placeholder, aria) {
    var s = el("select", { "aria-label": aria || null }, [el("option", { value: "", text: "Loading…" })]);
    var api = { el: s, ready: null, items: [] };
    api.ready = load().then(function (items) {
      api.items = items; M.clear(s); s.appendChild(el("option", { value: "", text: placeholder }));
      items.forEach(function (t) { s.appendChild(el("option", { value: t.code, text: t.name + (t.name !== t.code ? "  (" + t.code + ")" : ""), selected: t.code === value })); });
      if (value && !items.some(function (t) { return t.code === value; })) s.appendChild(el("option", { value: value, text: value + "  (not in your list)", selected: true }));
      return items;
    }, function (e) {
      M.clear(s); s.appendChild(el("option", { value: value || "", text: value ? value : "Could not load: " + e.message })); throw e;
    });
    api.ready.catch(function () { /* shown in the select */ });
    return api;
  }

  M.page("dumps", {
    render: function (ctx) {
      css();
      var S = { inst: null, sched: [], runs: [], roles: [], runsBy: {}, mailBy: {}, open: {}, owners: {}, tnames: {}, timer: null, loading: false, again: false,
                f: Object.assign({ q: "", owner: "visible", kind: "", status: "all" }, M.store.get("dumps.f", {}) || {}) };
      M.setHeader({ title: "Dump Scheduler", sub: "Write V2Pulse masters, MIS reports and the DFS report kinds to CSV / Excel in a folder on a timetable.",
                    tiles: [{ k: "Schedules", skel: true }, { k: "Enabled", skel: true }, { k: "Running now", skel: true }, { k: "Failed last run", skel: true }, { k: "Runs today", skel: true }] });

      var ready = M.get(API + "/instance").then(function (inst) {
        if (!ctx.alive()) return;
        S.inst = inst; var can = inst.can || {};
        if (!can.admin && S.f.owner !== "visible" && S.f.owner !== "mine") S.f.owner = "visible";
        var newBtn = can.manage ? el("button", { type: "button", "class": "btn primary" }, [M.icon("plus", 15), "New schedule"]) : null;
        if (newBtn) newBtn.addEventListener("click", function () { openForm(null, "new"); });
        var refBtn = el("button", { type: "button", "class": "btn", title: "Refresh now" }, [M.icon("refresh", 15), "Refresh"]);
        refBtn.addEventListener("click", function () { load(); });
        M.setHeader({ sub: "Write V2Pulse masters, MIS reports and the DFS report kinds to CSV / Excel in a folder on a timetable. Files go to <folder>\\YYYY_MM_DD\\. " +
                           "Times are IST (" + IST + "). The V2Pulse worker on " + inst.host + " writes them, so the folder must be one that server can reach. " +
                           (can.admin ? "You are a Dump Scheduler admin, so you see everybody's schedules." : "You see your own schedules and those shared with your roles."),
                      actions: [refBtn, newBtn] });
        build();
        M.get(API + "/roles").then(function (r) { S.roles = r.data || []; }, function () { S.roles = []; });
        return load().then(function () {
          var nk = M.qs("new"), nt = M.qs("target") || "";
          if (nk && ctx.alive()) {
            history.replaceState({}, "", "/mis/dumps");
            if (!can.manage) M.toast("You cannot create schedules (dumps.manage is needed).", "bad");
            else if (KIND_ORDER.indexOf(nk) < 0) M.toast("Unknown dump kind \"" + nk + "\".", "bad");
            else openForm(presetFor(nk, nt), "new");
          }
        });
      });

      // ── layout ──────────────────────────────────────────────────────────────────────────────────────
      var L = {};
      function build() {
        var inst = S.inst;
        L.q = el("input", { type: "search", placeholder: "Name, owner, report, folder", value: S.f.q, style: { minWidth: "260px" }, "aria-label": "Search schedules" });
        L.q.addEventListener("input", M.debounce(function () { S.f.q = L.q.value.trim(); save(); drawList(); }, 200));
        L.owner = el("select", { "aria-label": "Whose schedules" });
        L.owner.addEventListener("change", function () { var was = S.f.owner; S.f.owner = L.owner.value; save(); if ((was === "mine") !== (S.f.owner === "mine")) load(); else draw(); });
        L.kind = selectEl([["", "All kinds"]].concat(KIND_ORDER.map(function (k) { return [k, KIND_LABEL[k]]; })), S.f.kind, "Kind");
        L.kind.addEventListener("change", function () { S.f.kind = L.kind.value; save(); drawList(); });
        L.status = seg([["all", "All"], ["busy", "Running"], ["failed", "Failing"], ["paused", "Paused"], ["never", "Never run"]], S.f.status, function (v) { S.f.status = v; save(); drawList(); }, "Status");
        L.shown = el("span", { "class": "muted small" });
        L.list = el("div", { "class": "dk-list", role: "list" });
        L.listCard = el("section", { "class": "card", "aria-label": "Schedules" }, [
          el("div", { "class": "fbar" }, [el("label", { "class": "f" }, ["Search", L.q]), el("label", { "class": "f" }, ["Owner", L.owner]),
            el("label", { "class": "f" }, ["Kind", L.kind]), el("div", { "class": "dk-lab" }, [el("span", { text: "Status" }), L.status.el]),
            el("span", { "class": "spacer" }), L.shown]),
          inst.mail ? null : el("div", { "class": "notice warn", style: { margin: "12px 18px 0" } }, [M.icon("info", 16), el("span", { text: "Failure e-mails are not set up on this V2Pulse server yet - failures show here only." })]),
          L.list]);
        L.feed = el("div", { "class": "dk-feed" }); L.feedCnt = el("span", { "class": "chip grey" }); L.feedScope = el("span", { "class": "chip" });
        var fr = el("button", { type: "button", "class": "btn ghost sm" }, [M.icon("refresh", 14), "Refresh"]);
        fr.addEventListener("click", function () { load(); });
        L.actCard = el("section", { "class": "card", "aria-label": "Recent activity" }, [
          el("div", { "class": "card-h" }, [ico("activity", 16), el("h2", { text: "Recent activity" }), L.feedCnt, L.feedScope, el("span", { "class": "note", text: "Every run of the schedules listed above, newest first." }), el("span", { "class": "spacer" }), fr]),
          L.feed]);
        ctx.main.appendChild(L.listCard); ctx.main.appendChild(L.actCard);
        L.list.appendChild(el("div", { "class": "dk-empty" }, [el("span", { "class": "spin" }), " Loading schedules…"]));
      }
      function save() { M.store.set("dumps.f", S.f); }

      // ── data ────────────────────────────────────────────────────────────────────────────────────────
      function anyLive() {
        return S.sched.some(function (s) { return s.live_status; }) || S.runs.some(function (r) { return r.status === "RUNNING" || r.status === "QUEUED"; });
      }
      function load() {
        if (S.loading) { S.again = true; return Promise.resolve(); }
        S.loading = true; clearTimeout(S.timer);
        var own = S.f.owner === "mine" ? "mine" : "visible";
        return Promise.all([M.get(API + "/schedules?owner=" + own), M.get(API + "/runs?limit=150&owner=" + own)]).then(function (r) {
          if (!ctx.alive()) return;
          S.sched = r[0].data || []; S.runs = r[1].data || []; S.err = null;
          S.sched.forEach(function (s) { S.owners[s.owner_emp_code] = true; });
          var live = anyLive(), ids = Object.keys(S.open).filter(function (id) { return S.open[id]; });
          return Promise.all(ids.map(function (id) {
            var sc = S.sched.filter(function (s) { return String(s.id) === id; })[0];
            if (!sc) { delete S.open[id]; return null; }
            return (live || !S.runsBy[id]) ? fetchRuns(id) : null;
          })).then(function () { draw(); loadNames(); });
        }, function (e) {
          if (!ctx.alive()) return;
          S.err = e.message; draw();
        }).then(function () {
          S.loading = false;
          if (S.again) { S.again = false; return load(); }
          plan();
        });
      }
      function plan() {
        clearTimeout(S.timer);
        if (!ctx.alive()) return;
        S.timer = setTimeout(function () {
          if (!ctx.alive()) return;
          if (document.hidden || document.querySelector(".modal-bg")) { plan(); return; }   // no redraws under an open form
          load();
        }, anyLive() ? 4000 : 30000);
      }
      function fetchRuns(id) {
        return M.get(API + "/schedules/" + id + "/runs").then(function (r) { S.runsBy[id] = r.data || []; S.mailBy[id] = r.mail || []; },
          function (e) { S.runsBy[id] = { error: e.message }; });
      }
      function loadNames() {
        if (S.namesAsked) return; S.namesAsked = true;
        var kinds = {}; S.sched.forEach(function (s) { if (s.kind === "master") kinds.master = 1; if (s.kind === "mis_report" || s.kind === "mis_book") kinds.mis_report = 1; });
        Object.keys(kinds).forEach(function (k) {
          OPT.targets(k).then(function (items) {
            var m = {}; items.forEach(function (t) { m[t.code] = t.name; }); S.tnames[k] = m;
            if (ctx.alive() && !document.querySelector(".modal-bg")) drawList();
          }, function () { /* codes stay */ });
        });
      }

      // ── derived ─────────────────────────────────────────────────────────────────────────────────────
      function scoped() {
        var o = S.f.owner;
        return o === "visible" || o === "mine" ? S.sched : S.sched.filter(function (s) { return s.owner_emp_code === o; });
      }
      function scopedRuns() {
        if (S.f.owner === "visible" || S.f.owner === "mine") return S.runs;
        var ids = {}; scoped().forEach(function (s) { ids[s.id] = true; });
        return S.runs.filter(function (r) { return ids[r.schedule_id]; });
      }
      function isBusy(s) { return !!s.live_status; }
      function visible() {
        var q = S.f.q.toLowerCase();
        return scoped().filter(function (s) {
          if (S.f.kind && s.kind !== S.f.kind) return false;
          if (S.f.status === "busy" && !isBusy(s)) return false;
          if (S.f.status === "failed" && s.last_status !== "FAILED") return false;
          if (S.f.status === "paused" && s.enabled) return false;
          if (S.f.status === "never" && s.last_status) return false;
          if (!q) return true;
          return [s.name, s.owner_emp_code, KIND_LABEL[s.kind], s.target, tname(s.kind, s.target), s.folder, s.description, s.file_name]
            .some(function (v) { return String(v || "").toLowerCase().indexOf(q) >= 0; });
        });
      }
      function tname(kind, code) {
        var m = S.tnames[kind === "mis_book" ? "mis_report" : kind]; if (!m) return code;
        return String(code || "").split(",").map(function (c) { return m[c] || c; }).join(", ");
      }
      function ruleText(c) {
        if ((c.dateRule || "yesterday") === "fixed") return (c.from_date || "?") + " to " + (c.to_date || "?");
        var r = MIS_RULES.filter(function (x) { return x[0] === (c.dateRule || "yesterday"); })[0]; return r ? r[1] : c.dateRule;
      }
      function summary(s) {
        var c = s.config || {}, lv = (c.levels || []).filter(Boolean), f = (s.format || "").toUpperCase(), out;
        switch (s.kind) {
          case "master": out = [tname("master", s.target), f, c.q ? "search \"" + c.q + "\"" : null, countFilters(c.filters) ? countFilters(c.filters) + " filter(s)" : null]; break;
          case "mis_report": out = [tname("mis_report", s.target), ruleText(c), f, lv.length ? "by " + lv.join(", ") : null]; break;
          case "mis_book": var n = String(s.target || "").split(",").filter(Boolean).length; out = [n + " report" + (n === 1 ? "" : "s") + " → 1 workbook", ruleText(c), "XLSX"]; break;
          case "day_trend": out = [c.reportSlug ? "report " + c.reportSlug : "last " + (c.days || 30) + " day(s) to the latest load",
            c.reportSlug ? null : ((c.groupBy || []).length ? "by " + c.groupBy.join(" × ") : "store × variant"), (c.format || "xlsx").toUpperCase(),
            lv.length ? "file per " + lv.join(" + ") : null, countFilters(c.colFilters) ? countFilters(c.colFilters) + " filter(s)" : null]; break;
          case "inv_report": out = [c.slug || s.target, (INV_RULES.filter(function (x) { return x[0] === (c.dateRule || "latest"); })[0] || [0, c.dateRule])[1], "CSV",
            lv.length ? "by " + lv.map(function (x) { return x.replace(/^(SM|PM):/, ""); }).join(", ") : null]; break;
          case "bin_wise": out = [lv.length ? "file per " + lv.join(" + ") : "whole snapshot", "latest snapshot", "CSV",
            countFilters(c.colFilters) ? countFilters(c.colFilters) + " filter(s)" : null]; break;
          case "tna": out = ["current snapshot", (c.groupBy || []).length ? "by " + c.groupBy.join(" × ") : "per PO line",
            (c.stages || []).length ? c.stages.length + " stage(s)" : null, (c.format || "xlsx").toUpperCase(), lv.length ? "file per " + lv.join(" + ") : null,
            countFilters(c.colFilters) ? countFilters(c.colFilters) + " filter(s)" : null]; break;
          case "mis_day_load": out = ["no files", ruleText(c), (c.stores || []).length ? c.stores.length + " store(s)" : "all stores"]; break;
          default: out = [s.target, f];
        }
        return out.filter(Boolean).join(" · ");
      }
      function mineCode(code) { return S.inst && String(code || "").toUpperCase() === String(S.inst.emp_code || "").toUpperCase(); }
      function canChange(s) { var c = S.inst.can || {}; return !!(c.admin || (c.manage && mineCode(s.owner_emp_code))); }

      // ── drawing ─────────────────────────────────────────────────────────────────────────────────────
      function draw() { if (!ctx.alive()) return; drawTiles(); drawOwners(); drawList(); drawFeed(); }

      function drawTiles() {
        var sc = scoped(), runs = scopedRuns(), tk = todayKey();
        var mine = sc.filter(function (s) { return mineCode(s.owner_emp_code); }).length;
        var en = sc.filter(function (s) { return s.enabled; }).length;
        var running = sc.filter(function (s) { return s.live_status === "RUNNING"; }).length, queued = sc.filter(function (s) { return s.live_status === "QUEUED"; }).length;
        var ran = sc.filter(function (s) { return s.last_status; }).length, failed = sc.filter(function (s) { return s.last_status === "FAILED"; }).length;
        var today = runs.filter(function (r) { return dayKey(r.started_at || r.queued_at) === tk; });
        var tDone = today.filter(function (r) { return r.status === "DONE"; }).length, tFail = today.filter(function (r) { return r.status === "FAILED"; }).length;
        var capped = S.runs.length >= 150 && dayKey(S.runs[S.runs.length - 1].started_at || S.runs[S.runs.length - 1].queued_at) === tk;
        M.setHeader({ tiles: [
          { k: "Schedules", v: n0(sc.length), s: mine + " yours" },
          { k: "Enabled", v: n0(en), s: (sc.length - en) + " paused" },
          { k: "Running now", v: n0(running), s: queued ? queued + " queued" : (running ? "polling every 4 s" : "idle"), dot: running ? "var(--s8)" : null },
          { k: "Failed last run", v: n0(failed), s: "of " + ran + " that have run", cls: failed ? "down" : "up" },
          { k: "Runs today", v: n0(today.length) + (capped ? "+" : ""), s: tDone + " done · " + tFail + " failed", cls: tFail ? "down" : null }] });
      }

      function drawOwners() {
        var cur = S.f.owner, opts = [["visible", "All I can see"], ["mine", "Mine (" + S.inst.emp_code + ")"]];
        if ((S.inst.can || {}).admin) Object.keys(S.owners).sort().forEach(function (o) { if (!mineCode(o)) opts.push([o, o]); });
        if (cur !== "visible" && cur !== "mine" && !opts.some(function (o) { return o[0] === cur; })) opts.push([cur, cur]);
        M.clear(L.owner); opts.forEach(function (o) { L.owner.appendChild(el("option", { value: o[0], text: o[1], selected: o[0] === cur })); });
      }

      function drawList() {
        if (!ctx.alive() || !L.list) return;
        M.clear(L.list);
        var sc = scoped(), rows = visible();
        var cnt = { all: sc.length, busy: 0, failed: 0, paused: 0, never: 0 };
        sc.forEach(function (s) { if (isBusy(s)) cnt.busy++; if (s.last_status === "FAILED") cnt.failed++; if (!s.enabled) cnt.paused++; if (!s.last_status) cnt.never++; });
        ["all", "busy", "failed", "paused", "never"].forEach(function (k, i) {
          var b = L.status.buttons[i]; M.clear(b); b.appendChild(document.createTextNode(["All", "Running", "Failing", "Paused", "Never run"][i] + " "));
          b.appendChild(el("span", { "class": "muted", text: String(cnt[k]) }));
        });
        L.shown.textContent = rows.length === sc.length ? sc.length + " schedule" + (sc.length === 1 ? "" : "s") : "showing " + rows.length + " of " + sc.length;
        if (S.err) { L.list.appendChild(el("div", { "class": "notice bad", style: { margin: "14px 18px" }, text: S.err })); if (!S.sched.length) return; }
        L.list.appendChild(el("div", { "class": "dk-head", role: "presentation" }, [el("span"), el("span", { text: "Schedule" }), el("span", { text: "Owner" }),
          el("span", { text: "When" }), el("span", { text: "Last run" }), el("span", { style: { textAlign: "right" }, text: "Actions" })]));
        if (!rows.length) {
          var msg;
          if (sc.length) {
            var clr = el("button", { type: "button", "class": "btn sm" }, ["Clear the filters"]);
            clr.addEventListener("click", function () { S.f.q = ""; S.f.kind = ""; S.f.status = "all"; L.q.value = ""; L.kind.value = ""; L.status.set("all"); save(); drawList(); });
            msg = [el("div", { text: "No schedule matches these filters." }), el("div", { style: { marginTop: "8px" } }, clr)];
          } else if (S.f.owner !== "visible") msg = "No schedules for this owner. Switch Owner to \"All I can see\" to see the rest.";
          else msg = (S.inst.can || {}).manage ? "No schedules you can see yet. Use New schedule to add one, or ask an admin to share one with your role."
                                               : "No schedules you can see. Ask a Dump Scheduler admin to share one with your role.";
          L.list.appendChild(el("div", { "class": "dk-empty" }, msg));
          return;
        }
        rows.forEach(function (s) { L.list.appendChild(row(s)); if (S.open[s.id]) L.list.appendChild(expanded(s)); });
      }

      function row(s) {
        var can = S.inst.can || {}, live = s.live_status, change = canChange(s), roles = s.allowed_roles || [];
        var hist = iconBtn("history", "Run history of " + s.name, null, function () {
          S.open[s.id] = !S.open[s.id];
          if (S.open[s.id] && !S.runsBy[s.id]) fetchRuns(s.id).then(function () { if (ctx.alive()) drawList(); });
          drawList();
        }, false, { "aria-expanded": String(!!S.open[s.id]) });
        var name = el("div", { "class": "dk-name" }, [el("span", { "class": "t", text: s.name, title: s.name + (s.description ? "\n" + s.description : "") }),
          s.run_once ? el("span", { "class": "dk-tag", text: "ONCE" }) : null, s.enabled ? null : el("span", { "class": "chip grey", style: { fontSize: "11px", padding: "0 7px" }, text: "paused" })]);
        var job = el("div", { "class": "dk-cell" }, [name, el("div", { "class": "dk-sub dk-dim" }, [el("span", { "class": "dk-kind", text: KIND_LABEL[s.kind] || s.kind }),
          el("span", { "class": "s", text: summary(s), title: NO_FOLDER[s.kind] ? "Writes no files" : "Folder: " + s.folder + "\\YYYY_MM_DD\\" + (s.file_name ? "\nFile name: " + s.file_name : "") })])]);
        var owner = el("div", { "class": "dk-cell dk-dim" }, [mineCode(s.owner_emp_code) ? el("span", { "class": "dk-you", title: s.owner_emp_code, text: "you" }) : el("span", { text: s.owner_emp_code || "–" }),
          roles.length ? el("span", { "class": "dk-l2", title: "Also visible to: " + roles.join(", "), text: "+" + roles.join(", ") }) : null]);
        var when = el("div", { "class": "dk-cell dk-dim" }, [el("span", { "class": "dk-dot-u", title: "cron " + s.cron_expr + " (" + (s.timezone || IST) + ")", text: describeCron(s.cron_expr, s.run_once) }),
          el("span", { "class": "dk-l2", title: s.enabled && s.next_run_at ? fmtLong(s.next_run_at) : null, text: s.enabled ? (s.next_run_at ? "next " + relTime(s.next_run_at) : "will not fire again") : "paused" }),
          s.end_at ? el("span", { "class": "dk-l2 warn", text: "expires " + fmtT(s.end_at) }) : null]);
        var last;
        if (live) {
          var line = [s.live_progress, s.live_rows ? n0(s.live_rows) + " rows" : null].filter(Boolean).join(" · ");
          last = el("div", { "class": "dk-cell" }, [stChip(live, live === "RUNNING"),
            el("span", { "class": "dk-l2", title: line || null, text: live === "QUEUED" ? "waiting for a free slot" : (line || "started " + relTime(s.live_started)) })]);
        } else if (s.last_status) {
          last = el("div", { "class": "dk-cell" }, [stChip(s.last_status), el("span", { "class": "dk-l2", title: fmtLong(s.last_run_at), text: relTime(s.last_run_at) })]);
        } else last = el("div", { "class": "dk-cell muted small", text: "never run" });
        var acts = el("div", { "class": "dk-acts" }, [
          iconBtn("play", can.run ? (live ? "Already " + live.toLowerCase() : "Run now") : "You cannot run schedules (dumps.run)", "go", function (b) { runNow(b, s); }, !can.run || !!live),
          live ? iconBtn("stop", !can.run ? "You cannot stop runs (dumps.run)" : live === "QUEUED" ? "Cancel the queued run (nothing written yet)" : "Stop the run (files already written are kept)", "stop", function (b) { stop(b, s); }, !can.run) : null,
          iconBtn("power", change ? (s.enabled ? "Disable (pause) this schedule" : "Enable this schedule") : "Only the owner or a Dump Scheduler admin can enable / disable", s.enabled ? "on" : null,
            function (b) { toggle(b, s); }, !change, { "aria-pressed": String(!!s.enabled) }),
          iconBtn("edit", change ? "Edit" : "View the configuration (read-only)", null, function () { openForm(s, change ? "edit" : "view"); }),
          iconBtn("copy", can.manage ? "Duplicate (opens a copy - rename, then save)" : "You cannot create schedules (dumps.manage)", null, function () { openForm(s, "copy"); }, !can.manage),
          iconBtn("trash", change ? "Delete" : "Only the owner or a Dump Scheduler admin can delete", "del", function () { del(s); }, !change)]);
        return el("div", { "class": "dk-row" + (s.enabled ? "" : " off"), role: "listitem" }, [el("div", {}, hist), job, owner, when, last, acts]);
      }

      function hostChip(h) {
        if (!h) return el("span", { "class": "muted", text: "–" });
        var mine = h === S.inst.host;
        return el("span", { "class": "dk-host" + (mine ? "" : " other"), title: mine ? "Ran on the server this page talks to" : "Ran on " + h + ", another V2Pulse worker on the same database", text: h });
      }
      function userOf(r, sc) {
        var who = r.triggered_by || (sc && sc.owner_emp_code) || "";
        if (!who) return el("span", { "class": "muted", text: "–" });
        var t = r.triggered_by ? who + " pressed Run now" : "Ran on " + (mineCode(who) ? "your" : who + "'s") + " schedule";
        return mineCode(who) ? el("span", { "class": "dk-you", title: t, text: "you" }) : el("span", { title: t, text: who });
      }
      function filesCell(r) {
        var paths = r.file_paths || [];
        if (!paths.length) return el("span", { "class": "muted", text: r.status === "QUEUED" ? "–" : n0(r.files) });
        return el("details", {}, [el("summary", { text: n0(r.files) + " file" + (r.files === 1 ? "" : "s") }),
          el("div", { "class": "small", style: { whiteSpace: "pre-wrap", wordBreak: "break-all", maxWidth: "70ch" }, text: paths.join("\n") })]);
      }

      function expanded(s) {
        var box = el("div", { "class": "dk-exp", role: "region", "aria-label": "Run history of " + s.name });
        box.appendChild(el("div", { "class": "dk-kv" }, [
          el("span", {}, [el("b", { text: "Folder" }), NO_FOLDER[s.kind] ? "none - this kind writes no files" : s.folder + "\\YYYY_MM_DD\\"]),
          NO_FOLDER[s.kind] ? null : el("span", {}, [el("b", { text: "File name" }), s.file_name || s.name]),
          el("span", {}, [el("b", { text: "Cron" }), el("span", { "class": "dk-cron", text: s.cron_expr }), " " + (s.timezone || IST)]),
          el("span", {}, [el("b", { text: "Time limit" }), (s.timeout_min || 60) + " min" + (s.timeout_min ? "" : " (default)")]),
          s.start_at ? el("span", {}, [el("b", { text: "Starts" }), fmtT(s.start_at)]) : null,
          el("span", {}, [el("b", { text: "Created by" }), (s.created_by || "–") + (s.created_at ? " on " + fmtT(s.created_at) : "")]),
          s.description ? el("span", {}, [el("b", { text: "Description" }), s.description]) : null]));
        var runs = S.runsBy[s.id];
        if (!runs) { box.appendChild(el("div", { "class": "muted small" }, [el("span", { "class": "spin" }), " Loading the run history…"])); return box; }
        if (runs.error) { box.appendChild(el("div", { "class": "notice bad", text: runs.error })); return box; }
        box.appendChild(el("div", { "class": "section-title", text: "Run history (last " + runs.length + ")" }));
        if (!runs.length) box.appendChild(el("div", { "class": "muted small", text: "No runs yet." }));
        else {
          var tb = el("tbody");
          runs.forEach(function (r) {
            var st = r.status, detail = [];
            if (r.error_msg) detail.push(errNote(r.error_msg));
            else if (r.progress && (st === "RUNNING" || st === "QUEUED")) detail.push(el("span", { "class": "muted", text: r.progress }));
            if (r.stale) detail.push(el("div", { "class": "dk-hint warn", text: "No heartbeat for over 3 minutes - the worker on " + (r.host || "?") + " may have been restarted." }));
            tb.appendChild(el("tr", {}, [el("td", { "class": "n muted", text: r.id }), el("td", {}, userOf(r, s)), el("td", { text: r.trigger_type }), el("td", {}, hostChip(r.host)),
              el("td", {}, stChip(st)), el("td", { "class": "nowrap", text: fmtT(r.started_at || r.queued_at) }), el("td", { "class": "nowrap", text: fmtT(r.completed_at) }),
              el("td", { "class": "n", text: fmtDur(r.duration_ms) }), el("td", {}, filesCell(r)), el("td", { "class": "n", text: st === "QUEUED" ? "–" : n0(r.rows_written) }),
              el("td", { style: { minWidth: "220px" } }, detail)]));
          });
          box.appendChild(el("div", { "class": "dk-tw" }, el("table", { "class": "dk-t" }, [el("thead", {}, el("tr", {}, ["#", "User", "Trigger", "Server", "Status", "Started", "Completed", "Duration", "Files", "Rows", "Detail"]
            .map(function (h) { return el("th", { text: h }); }))), tb])));
        }
        var mail = S.mailBy[s.id] || [];
        if (mail.length) {
          box.appendChild(el("div", { "class": "section-title", text: "Failure mails" }));
          var mt = el("tbody");
          mail.forEach(function (m) {
            mt.appendChild(el("tr", {}, [el("td", { "class": "nowrap", text: fmtT(m.created_at) }), el("td", { "class": "n muted", text: m.run_id || "–" }),
              el("td", {}, el("span", { "class": "chip " + (m.status === "SENT" ? "ok" : m.status === "FAILED" ? "bad" : "grey"), style: { fontSize: "11px", padding: "0 7px" }, text: m.status })),
              el("td", { text: m.mail_to || "–" }), el("td", {}, m.error_msg ? errNote(m.error_msg) : null)]));
          });
          box.appendChild(el("table", { "class": "dk-t" }, [el("thead", {}, el("tr", {}, ["Sent", "Run", "Status", "To", "Error"].map(function (h) { return el("th", { text: h }); }))), mt]));
        }
        return box;
      }

      function drawFeed() {
        if (!L.feed) return;
        var runs = scopedRuns(), bySched = {};
        S.sched.forEach(function (s) { bySched[s.id] = s; });
        L.feedCnt.textContent = String(runs.length);
        L.feedScope.textContent = S.f.owner === "visible" ? "all I can see" : S.f.owner === "mine" ? "mine" : S.f.owner;
        M.clear(L.feed);
        if (!runs.length) { L.feed.appendChild(el("div", { "class": "dk-empty", text: "No runs yet." })); return; }
        var groups = [];
        runs.forEach(function (r) { var k = dayKey(r.started_at || r.queued_at), g = groups[groups.length - 1]; if (g && g.k === k) g.rows.push(r); else groups.push({ k: k, rows: [r] }); });
        groups.forEach(function (g) {
          var ul = el("ul");
          g.rows.forEach(function (r) {
            var sc = bySched[r.schedule_id], st = r.status, busy = st === "RUNNING" || st === "QUEUED", sep = function () { return el("span", { "class": "sep", text: "·" }); };
            var meta = el("div", { "class": "dk-meta" }, [userOf(r, sc), sep(), el("span", { text: r.trigger_type }),
              sc ? sep() : null, sc ? el("span", { text: KIND_LABEL[sc.kind] || sc.kind }) : null,
              !busy && r.files ? sep() : null, !busy && r.files ? el("span", { text: n0(r.files) + " file" + (r.files === 1 ? "" : "s") }) : null,
              !busy && r.rows_written ? sep() : null, !busy && r.rows_written ? el("span", { text: n0(r.rows_written) + " rows" }) : null,
              sep(), hostChip(r.host), r.stale ? el("span", { "class": "dk-l2 warn", style: { margin: 0 }, text: "no heartbeat" }) : null]);
            ul.appendChild(el("li", {}, [el("span", { "class": "dk-dot " + st + (busy ? " live" : ""), title: st }),
              el("div", { "class": "dk-ft" }, [el("div", { "class": "h" }, [el("span", { "class": "t", text: r.schedule_name || "(deleted schedule)", title: r.schedule_name || "" }), st !== "DONE" ? stChip(st) : null]),
                meta, busy && (r.progress || r.rows_written) ? el("div", { "class": "dk-meta" }, [r.rows_written ? el("span", { "class": "tnum", text: n0(r.rows_written) + " rows so far" }) : null, r.progress ? el("span", { text: r.progress }) : null]) : null,
                r.error_msg ? errNote(r.error_msg) : null]),
              el("div", { "class": "dk-when", title: "Queued " + fmtLong(r.queued_at) + (r.started_at ? "\nStarted " + fmtLong(r.started_at) : "") + (r.completed_at ? "\nFinished " + fmtLong(r.completed_at) : "") },
                [timeOnly(r.started_at || r.queued_at), el("small", { text: busy ? relTime(r.started_at || r.queued_at) : fmtDur(r.duration_ms) })])]));
          });
          L.feed.appendChild(el("section", {}, [el("h3", { "class": "dk-dayh" }, [dayLabel(g.k), el("span", { text: String(g.rows.length) })]), ul]));
        });
      }

      // ── row actions ─────────────────────────────────────────────────────────────────────────────────
      function busyBtn(b, fn) {
        b.disabled = true; var old = b.innerHTML; b.innerHTML = '<span class="spin" style="width:13px;height:13px"></span>';
        return Promise.resolve().then(fn).then(function (r) { b.innerHTML = old; b.disabled = false; return r; }, function (e) { b.innerHTML = old; b.disabled = false; M.toast(e.message, "bad"); throw e; });
      }
      function runNow(b, s) {
        busyBtn(b, function () { return M.post(API + "/schedules/" + s.id + "/run-now", {}); }).then(function (r) {
          M.toast("Queued run " + r.run_id + " of \"" + s.name + "\" - the worker starts it within about 20 seconds.", "ok"); load();
        }, function () { /* toasted */ });
      }
      function stop(b, s) {
        var queued = s.live_status === "QUEUED";
        M.confirm(queued ? "Cancel the queued run?" : "Stop the running dump?", queued ? "\"" + s.name + "\" is still waiting for a free slot, so nothing has been written yet." :
          "\"" + s.name + "\" stops at the next check. Files already written stay in the folder.", queued ? "Cancel the run" : "Stop", true).then(function (ok) {
          if (!ok) return;
          busyBtn(b, function () { return M.post(API + "/schedules/" + s.id + "/stop", {}); }).then(function () { M.toast(queued ? "Cancelled - it will not start." : "Stopping…", "ok"); load(); }, function () { /* toasted */ });
        });
      }
      function toggle(b, s) {
        busyBtn(b, function () { return M.post(API + "/schedules/" + s.id + "/toggle", {}); }).then(function (r) {
          M.toast("\"" + s.name + "\" " + (r.enabled ? "enabled" + (r.next_run_at ? " - next run " + fmtT(r.next_run_at) : "") : "paused") + ".", "ok"); load();
        }, function () { /* toasted */ });
      }
      function del(s) {
        M.confirm("Delete schedule", "Delete \"" + s.name + "\" and its run history? A run in progress is stopped. Files already written stay in the folder.", "Delete", true).then(function (ok) {
          if (!ok) return;
          M.api("DELETE", API + "/schedules/" + s.id).then(function () { delete S.open[s.id]; M.toast("Deleted \"" + s.name + "\".", "ok"); load(); }, function (e) { M.toast(e.message, "bad"); });
        });
      }

      // ── create / edit / duplicate / view ────────────────────────────────────────────────────────────
      function presetFor(kind, target) {
        var p = { kind: kind, target: target, config: {}, enabled: true };
        if (kind === "day_trend" && target) p.config.reportSlug = target;
        if (kind === "inv_report" && target) p.config.slug = target;
        if (kind === "mis_report" && target) p.config.dateRule = "yesterday";
        return p;
      }
      function copyName(base) {
        var taken = {}; S.sched.forEach(function (x) { taken[String(x.name || "").trim().toLowerCase()] = true; });
        var stem = String(base || "").trim().replace(/\s*\(copy(?:\s+\d+)?\)$/i, ""), name = stem + " (copy)";
        for (var n = 2; taken[name.toLowerCase()]; n++) name = stem + " (copy " + n + ")";
        return name.slice(0, 120);
      }

      function openForm(src, mode) {
        var inst = S.inst, can = inst.can || {}, s = src || {}, isEdit = mode === "edit" || mode === "view";
        var kind = s.kind && KIND_ORDER.indexOf(s.kind) >= 0 ? s.kind : "mis_report";
        var origKind = kind, origCfg = clone(s.config), origTarget = s.target || "";
        var readOnly = mode === "view";

        var name = el("input", { type: "text", maxlength: "120", value: mode === "copy" ? copyName(s.name) : (s.name || ""), placeholder: "e.g. Nightly zone sale dump" });
        var fileName = el("input", { type: "text", maxlength: "120", value: s.file_name || "", placeholder: "Same as the schedule name" });
        var fileNote = el("div", { "class": "dk-hint" });
        function paintFileNote() {
          fileNote.textContent = "Files are named " + (fileName.value.trim() || name.value.trim() || "<schedule name>") + " followed by the date / split value. Characters \\ / : * ? \" < > | are not allowed.";
        }
        name.addEventListener("input", paintFileNote); fileName.addEventListener("input", paintFileNote); paintFileNote();
        var desc = el("input", { type: "text", maxlength: "1000", value: s.description || "", placeholder: "Optional - who reads these files, and why" });
        var folder = el("input", { type: "text", maxlength: "400", value: NO_FOLDER[s.kind] ? "" : (s.folder || ""), placeholder: "\\\\server\\share\\dumps", style: { fontFamily: "Consolas, monospace" } });
        var folderBox = field("Dump folder (share or drive path on the V2Pulse server)", folder, "Files land in a dated subfolder: <folder>\\YYYY_MM_DD\\. The V2Pulse worker on " + inst.host + " must be able to write there.");

        // kind picker
        var kindBtns = {}, kindGrid = el("div", { "class": "dk-kinds", role: "radiogroup", "aria-label": "What to dump" });
        KIND_ORDER.forEach(function (k) {
          var meta = (inst.kinds || []).filter(function (x) { return x.code === k; })[0];
          var b = el("button", { type: "button", role: "radio", "aria-pressed": String(k === kind), "aria-checked": String(k === kind), disabled: isEdit && k !== kind && readOnly ? true : null },
            [el("b", { text: KIND_LABEL[k] }), el("span", { text: KIND_HINT[k] + (meta && meta.copied_only ? " (DFS kind)" : "") })]);
          b.addEventListener("click", function () { if (kind === k) return; kind = k; Object.keys(kindBtns).forEach(function (x) { kindBtns[x].setAttribute("aria-pressed", String(x === k)); kindBtns[x].setAttribute("aria-checked", String(x === k)); }); showKind(); });
          kindBtns[k] = b; kindGrid.appendChild(b);
        });
        var kindBox = el("div", { "class": "stack" }), permNote = el("div");
        var panel = null;
        function showKind() {
          // a different kind starts from an empty config; switching back restores what the schedule had
          var cfg = kind === origKind ? clone(origCfg) : {}, tgt = kind === origKind ? origTarget : "";
          panel = PANELS[kind](cfg, tgt, s.format, readOnly);
          M.clear(kindBox).appendChild(panel.el);
          folderBox.classList.toggle("hidden", !!NO_FOLDER[kind]);
          M.clear(permNote);
          var need = kind === "master" ? null : kind === "mis_day_load" ? (ctx.me.can.admin ? null : "mis.admin") : (ctx.me.can.export ? null : "mis.export");
          if (need) permNote.appendChild(el("div", { "class": "notice warn" }, [M.icon("info", 16), el("span", { text: "Saving this kind needs the " + need + " permission, which you do not have." })]));
          if (kind === "master") permNote.appendChild(el("div", { "class": "dk-hint", text: "Dumping a master needs the masters.export permission; the file holds what the owner may see." }));
        }

        // frequency builder
        var fr = { freq: "daily", time: "06:30", minute: 0, everyN: 15, everyH: 1, days: [1], dom: 1, onceAt: "", rawCron: s.cron_expr || "",
                   startAt: istInput(s.start_at), endAt: istInput(s.end_at) };
        if (s.cron_expr) {
          if (s.run_once) { fr.freq = "once"; fr.onceAt = istInput(s.start_at); fr.startAt = ""; fr.endAt = ""; }
          else Object.assign(fr, parseCron(s.cron_expr));
        }
        var freqSeg = seg(FREQS, fr.freq, function (v) {
          var was = buildTrigger(readFr()).cron;
          fr.freq = v; if (v === "advanced" && !raw.value.trim() && was) raw.value = was;   // start the cron from what was built
          showFreq();
        }, "Frequency");
        var onceAt = el("input", { type: "datetime-local", value: fr.onceAt, "aria-label": "Run once at (IST)" });
        var everyN = el("input", { type: "number", min: "1", max: "59", value: fr.everyN, style: { width: "80px" }, "aria-label": "Every N minutes" });
        var everyH = el("input", { type: "number", min: "1", max: "23", value: fr.everyH, style: { width: "70px" }, "aria-label": "Every N hours" });
        var minute = el("input", { type: "number", min: "0", max: "59", value: fr.minute, style: { width: "70px" }, "aria-label": "At minute" });
        var time = el("input", { type: "time", value: fr.time, "aria-label": "At time (IST)" });
        var dom = el("input", { type: "number", min: "1", max: "31", value: fr.dom, style: { width: "80px" }, "aria-label": "Day of the month" });
        var raw = el("input", { type: "text", value: fr.rawCron, placeholder: "30 6 * * *", style: { fontFamily: "Consolas, monospace", width: "220px" }, "aria-label": "Cron expression" });
        var daySeg = el("div", { "class": "seg", role: "group", "aria-label": "Days of the week" });
        DOW.forEach(function (d, i) {
          var b = el("button", { type: "button", "aria-pressed": String(fr.days.indexOf(i) >= 0), text: d });
          b.addEventListener("click", function () { var at = fr.days.indexOf(i); if (at >= 0) fr.days.splice(at, 1); else fr.days.push(i); b.setAttribute("aria-pressed", String(at < 0)); upd(); });
          daySeg.appendChild(b);
        });
        var startAt = el("input", { type: "datetime-local", value: fr.startAt, "aria-label": "Start (IST)" });
        var endAt = el("input", { type: "datetime-local", value: fr.endAt, "aria-label": "Expire (IST)" });
        var parts = {
          once: el("div", { "class": "row" }, [el("span", { "class": "small", text: "Run once at" }), onceAt, el("span", { "class": "muted small", text: "IST" })]),
          minutes: el("div", { "class": "row" }, [el("span", { "class": "small", text: "Every" }), everyN, el("span", { "class": "small", text: "minutes" })]),
          hourly: el("div", { "class": "row" }, [el("span", { "class": "small", text: "Every" }), everyH, el("span", { "class": "small", text: "hour(s), at minute" }), minute]),
          time: el("div", { "class": "row" }, [el("span", { "class": "small", text: "At" }), time, el("span", { "class": "muted small", text: "IST" })]),
          weekly: el("div", { "class": "row" }, [el("span", { "class": "small", text: "On" }), daySeg]),
          monthly: el("div", { "class": "row" }, [el("span", { "class": "small", text: "On day" }), dom, el("span", { "class": "small", text: "of the month (a month without that day is skipped)" })]),
          advanced: el("div", { "class": "row" }, [el("span", { "class": "small", text: "Cron (minute hour day month weekday, IST)" }), raw])
        };
        var cronLine = el("div", { "class": "dk-hint" }), prevBox = el("div");
        var windowBox = el("div", { "class": "dk-g2" }, [
          field("Start (do not run before)", startAt, "Optional, IST."), field("Expire (auto-disable after)", endAt, "Optional, IST. The schedule switches itself off after this.")]);
        function readFr() {
          fr.onceAt = onceAt.value; fr.everyN = everyN.value; fr.everyH = everyH.value; fr.minute = minute.value; fr.time = time.value; fr.dom = dom.value;
          fr.rawCron = raw.value; fr.startAt = startAt.value; fr.endAt = endAt.value; return fr;
        }
        function showFreq() {
          var f = fr.freq;
          parts.once.classList.toggle("hidden", f !== "once"); parts.minutes.classList.toggle("hidden", f !== "minutes"); parts.hourly.classList.toggle("hidden", f !== "hourly");
          parts.time.classList.toggle("hidden", ["daily", "weekly", "monthly"].indexOf(f) < 0); parts.weekly.classList.toggle("hidden", f !== "weekly");
          parts.monthly.classList.toggle("hidden", f !== "monthly"); parts.advanced.classList.toggle("hidden", f !== "advanced");
          windowBox.classList.toggle("hidden", f === "once");
          upd();
        }
        var prevSeq = 0;
        var upd = M.debounce(function () {
          var t = buildTrigger(readFr()), seq = ++prevSeq;
          M.clear(cronLine).appendChild(document.createTextNode("Cron: ")); cronLine.appendChild(el("span", { "class": "dk-cron", text: t.cron || "–" }));
          cronLine.appendChild(document.createTextNode("  " + (t.cron ? describeCron(t.cron, t.runOnce) : "")));
          if (!t.cron) { M.clear(prevBox).appendChild(el("div", { "class": "dk-hint", text: fr.freq === "once" ? "Pick a date and time to preview." : "Set a schedule to preview its next runs." })); return; }
          M.clear(prevBox).appendChild(el("div", { "class": "dk-hint" }, [el("span", { "class": "spin" }), " Working out the next runs…"]));
          M.post(API + "/preview", { cron_expr: t.cron, timezone: IST, start_at: t.startAt, end_at: t.endAt }).then(function (r) {
            if (seq !== prevSeq) return;
            M.clear(prevBox);
            if (!(r.next || []).length) { prevBox.appendChild(el("div", { "class": "notice warn", text: "This schedule never fires" + (t.endAt ? " before it expires." : ".") })); return; }
            prevBox.appendChild(el("ul", { "class": "dk-prev" }, r.next.map(function (x) { return el("li", {}, [fmtLong(x), el("span", { text: relTime(x) })]); })));
          }, function (e) { if (seq === prevSeq) M.clear(prevBox).appendChild(el("div", { "class": "notice bad", text: e.message })); });
        }, 300);
        [onceAt, everyN, everyH, minute, time, dom, raw, startAt, endAt].forEach(function (i) { i.addEventListener("input", upd); i.addEventListener("change", upd); });

        var timeout = el("input", { type: "number", min: "1", max: "600", value: s.timeout_min || "", placeholder: "60", style: { width: "110px" } });
        var enabled = el("input", { type: "checkbox", checked: mode === "copy" ? false : s.enabled !== false });

        // access
        var roleSel = (s.allowed_roles || []).slice(), roleBox = el("div", { "class": "row", style: { gap: "6px" } });
        var myRoles = inst.roles || [];
        function drawRoles() {
          M.clear(roleBox);
          if (!S.roles.length) { roleBox.appendChild(el("span", { "class": "dk-hint", text: "No roles to choose from." })); return; }
          S.roles.forEach(function (r) {
            var on = roleSel.indexOf(r.code) >= 0, mine = myRoles.indexOf(r.code) >= 0;
            var b = el("button", { type: "button", "class": "dk-role", "aria-pressed": String(on), title: r.code + (mine ? " - one of your roles" : ""), disabled: readOnly || null }, [r.name + (mine ? " ·" : "")]);
            b.addEventListener("click", function () { var i = roleSel.indexOf(r.code); if (i >= 0) roleSel.splice(i, 1); else roleSel.push(r.code); drawRoles(); });
            roleBox.appendChild(b);
          });
        }
        drawRoles();
        var ownerIn = null, ownerBox;
        if (can.admin) {
          var dl = el("datalist", { id: "dk-users" });
          ownerIn = el("input", { type: "text", list: "dk-users", value: mode === "copy" || mode === "new" ? "" : (s.owner_emp_code || ""), placeholder: "Employee code - blank = " + (mode === "edit" ? "keep the owner" : "you (" + inst.emp_code + ")"), style: { width: "300px" } });
          ownerIn.addEventListener("input", M.debounce(function () {
            var q = ownerIn.value.trim(); if (q.length < 2) return;
            M.get(API + "/users?q=" + encodeURIComponent(q)).then(function (r) {
              M.clear(dl); (r.data || []).forEach(function (u) { dl.appendChild(el("option", { value: u.emp_code, text: u.display_name })); });
            }, function () { /* free text still works */ });
          }, 250));
          ownerBox = field("Owner", el("div", {}, [ownerIn, dl]), "The owner sees the schedule in their own list and can run, edit and delete it. Admins see it either way; the file holds what the owner may see.");
        } else {
          ownerBox = el("div", { "class": "dk-hint", text: isEdit && !mineCode(s.owner_emp_code) ? "Owned by " + s.owner_emp_code + " - shared with you by role. Saving leaves it theirs."
                                                                                                 : "This schedule is yours. Only you and the Dump Scheduler admins see it, unless you share it with a role below." });
        }

        var body = [];
        if (readOnly) body.push(el("div", { "class": "notice" }, [M.icon("info", 16), el("span", { text: "Read-only: only the owner (" + s.owner_emp_code + ") or a Dump Scheduler admin can change this schedule." })]));
        if (mode === "copy") body.push(el("div", { "class": "notice" }, [M.icon("copy", 16), el("span", { text: "Copy of \"" + s.name + "\" - everything below was carried over. It saves as a NEW schedule, owned by you, and stays paused until you switch Enabled on." })]));
        body.push(el("div", { "class": "section-title", text: "Schedule" }),
          el("div", { "class": "dk-g2" }, [field("Name", name), el("div", { "class": "dk-f" }, [el("label", { "class": "f" }, ["File name (optional)", fileName]), fileNote])]),
          field("Description", desc),
          el("div", { "class": "section-title", text: "What to dump" }), kindGrid, permNote, kindBox,
          el("div", { "class": "section-title", text: "Where" }), folderBox,
          el("div", { "class": "section-title", text: "When (IST)" }), freqSeg.el,
          el("div", { "class": "dk-box" }, [parts.once, parts.minutes, parts.hourly, parts.time, parts.weekly, parts.monthly, parts.advanced, cronLine]),
          windowBox,
          el("div", { "class": "dk-g2" }, [
            field("Time limit (minutes)", timeout, "A run still going after this is stopped (blank = 60). Files already written are kept."),
            el("div", { "class": "dk-lab" }, [el("span", { text: "Next 5 runs" }), prevBox])]),
          el("label", { "class": "row small" }, [enabled, "Enabled - run on this schedule"]),
          el("div", { "class": "section-title", text: "Access" }), ownerBox,
          el("div", { "class": "dk-f" }, [el("span", { "class": "small", style: { fontWeight: 600 }, text: "Also visible to these roles" }),
            el("div", { "class": "dk-hint", text: "Holders of a ticked role may see and run this schedule (and change it, with dumps.manage). No selection = the owner and the admins only." }), roleBox]));

        var saveBtn = el("button", { type: "button", "class": "btn primary", disabled: readOnly || !can.manage || null, title: readOnly ? "Read-only" : null, text: mode === "edit" ? "Save changes" : "Create schedule" });
        var cancel = el("button", { type: "button", "class": "btn", text: readOnly ? "Close" : "Cancel" });
        var title = mode === "edit" ? "Edit schedule" : mode === "view" ? "Schedule (read-only)" : mode === "copy" ? "Duplicate schedule" : "New schedule";
        var m = M.modal(title + (isEdit ? " - " + s.name : ""), body, [cancel, saveBtn], { wide: true });
        cancel.addEventListener("click", function () { m.close(); });
        showKind(); showFreq();

        saveBtn.addEventListener("click", function () {
          var t = buildTrigger(readFr()), out;
          if (!name.value.trim()) return M.toast("Enter a name.", "bad");
          if (!t.cron) return M.toast(fr.freq === "once" ? "Pick the date and time to run once." : "Set a schedule.", "bad");
          if (fr.freq === "weekly" && !fr.days.length) return M.toast("Pick at least one day of the week.", "bad");
          if (/[\\/:*?"<>|]/.test(fileName.value)) return M.toast("The file name cannot contain \\ / : * ? \" < > |", "bad");
          try { out = panel.collect(); } catch (e) { return M.toast(e.message, "bad"); }
          var fold = folder.value.trim();
          if (!NO_FOLDER[kind] && !fold) return M.toast("Enter the dump folder.", "bad");
          if (NO_FOLDER[kind]) fold = (s.kind === kind && s.folder) || "(no files)";
          else if (["day_trend", "inv_report", "bin_wise", "tna"].indexOf(kind) >= 0) out.config.base = fold;   // DFS config shape
          var to = timeout.value ? clamp(timeout.value, 1, 600) : null;
          var b = { name: name.value.trim(), kind: kind, target: out.target, config: out.config, format: out.format, folder: fold,
                    file_name: fileName.value.trim() || null, cron_expr: t.cron, timezone: IST, enabled: enabled.checked, description: desc.value.trim() || null,
                    start_at: t.startAt, end_at: t.endAt, run_once: t.runOnce, timeout_min: to, allowed_roles: roleSel.slice(),
                    owner_emp_code: ownerIn && ownerIn.value.trim() ? ownerIn.value.trim().toUpperCase() : null };
          M.action(saveBtn, "Saving…", function () {
            return M.api(mode === "edit" ? "PUT" : "POST", API + "/schedules" + (mode === "edit" ? "/" + s.id : ""), b);
          }, mode === "edit" ? "Schedule saved." : "Schedule created.").then(function () { m.close(); load(); }, function () { /* toasted */ });
        });
      }

      // ── kind panels: {el, collect() -> {target, config, format}} ────────────────────────────────────
      function ruleControl(cfg, allowFixed) {
        var rules = MIS_RULES.concat(allowFixed ? [["fixed", "Fixed dates"]] : []);
        var cur = cfg.dateRule && rules.some(function (r) { return r[0] === cfg.dateRule; }) ? cfg.dateRule : "yesterday";
        var sel = selectEl(rules, cur, "Date range"), f1 = el("input", { type: "date", value: cfg.from_date || "", "aria-label": "From" }), f2 = el("input", { type: "date", value: cfg.to_date || "", "aria-label": "To" });
        var fixed = el("div", { "class": "row" }, [f1, el("span", { "class": "muted", text: "to" }), f2]);
        function vis() { fixed.classList.toggle("hidden", sel.value !== "fixed"); }
        sel.addEventListener("change", vis); vis();
        return { el: field("Date range", el("div", { "class": "stack", style: { gap: "6px" } }, [sel, fixed]),
                   "Resolved every time the schedule fires, anchored on yesterday (today's facts are not loaded yet)." + (allowFixed ? " Fixed dates never move." : "")),
                 get: function () {
                   var o = { dateRule: sel.value };
                   if (sel.value === "fixed") { if (!f1.value || !f2.value) throw new Error("Pick both dates of the fixed range."); if (f2.value < f1.value) throw new Error("The fixed range ends before it starts."); o.from_date = f1.value; o.to_date = f2.value; }
                   return o;
                 } };
      }
      function fmtSeg(cur, opts) { return seg(opts || [["xlsx", "Excel (.xlsx)"], ["csv", "CSV"]], cur === "csv" ? "csv" : "xlsx", null, "Format"); }
      function strip(cfg, keys) { keys.forEach(function (k) { delete cfg[k]; }); return cfg; }

      var PANELS = {
        master: function (cfg, tgt, format) {
          var tsel = asyncSelect(function () { return OPT.targets("master"); }, tgt, "Pick a master…", "Master");
          var fm = fmtSeg(format), q = el("input", { type: "text", value: cfg.q || "", placeholder: "Same as the master page's search box" });
          var fl = el("textarea", { placeholder: '[{"col":"ZONE","op":"eq","value":"ZONE-1"}]', style: { fontFamily: "Consolas, monospace", fontSize: "12.5px" } });
          fl.value = cfg.filters ? (typeof cfg.filters === "string" ? cfg.filters : JSON.stringify(cfg.filters)) : "";
          var sortC = el("input", { type: "text", value: cfg.sort || "", placeholder: "column (optional)" }), sortD = selectEl([["asc", "Ascending"], ["desc", "Descending"]], cfg.dir || "asc", "Sort direction");
          return { el: el("div", { "class": "stack" }, [el("div", { "class": "dk-g2" }, [field("Master", tsel.el, "The masters you may open in V2Pulse."), field("Format", fm.el)]),
              el("div", { "class": "dk-g2" }, [field("Search (optional)", q), field("Sort by (optional)", el("div", { "class": "row", style: { flexWrap: "nowrap" } }, [sortC, sortD]))]),
              field("Filters (optional)", fl, "As on the master page: a JSON list of {col, op, value}. Leave empty for the whole master (as its owner may see it).")]),
            collect: function () {
              if (!tsel.el.value) throw new Error("Pick the master to dump.");
              var c = strip(clone(cfg), ["filters", "q", "sort", "dir"]);
              if (fl.value.trim()) { try { c.filters = JSON.parse(fl.value); } catch (e) { throw new Error("The master filters are not valid JSON."); } }
              if (q.value.trim()) c.q = q.value.trim();
              if (sortC.value.trim()) { c.sort = sortC.value.trim(); c.dir = sortD.value; }
              return { target: tsel.el.value, config: c, format: fm.get() };
            } };
        },
        mis_report: function (cfg, tgt, format) {
          var tsel = asyncSelect(function () { return OPT.targets("mis_report"); }, tgt, "Pick a saved MIS report…", "MIS report");
          var rule = ruleControl(cfg, true), fm = fmtSeg(format);
          var lv = ms("Level (one file per value)", function () {
            var slug = tsel.el.value; if (!slug) return Promise.reject(new Error("Pick a report first."));
            return OPT.misReports().then(function (reps) {
              var r = reps.filter(function (x) { return x.slug === slug; })[0];
              var gb = (r && r.definition && r.definition.group_by) || [];
              if (!gb.length) throw new Error("This report has no group-by columns to split on.");
              return gb;
            });
          }, cfg.levels || [], "Leave empty for ONE file. Several levels mean the combination (Store and Maj-Cat = one file per store and maj-cat that exists). Only the report's own group-by columns can be levels.", { allText: "Whole report in one file" });
          tsel.el.addEventListener("change", function () { lv.reset(); lv.set([]); });
          return { el: el("div", { "class": "stack" }, [el("div", { "class": "dk-g2" }, [field("MIS report", tsel.el, "Built on MIS → Reports. Pick the MIS workbook kind for several reports in one file."), rule.el]),
              el("div", { "class": "dk-g2" }, [lv.el, field("Format", fm.el, "File: <name>_<from>_to_<to>[__LEVEL_value].<ext>")])]),
            collect: function () {
              if (!tsel.el.value) throw new Error("Pick the MIS report to dump.");
              var c = Object.assign(strip(clone(cfg), ["dateRule", "from_date", "to_date", "levels"]), rule.get());
              c.levels = lv.get();
              return { target: tsel.el.value, config: c, format: fm.get() };
            } };
        },
        mis_book: function (cfg, tgt) {
          var picked = String(tgt || "").split(",").map(function (x) { return x.trim(); }).filter(Boolean);
          var sheets = clone(cfg.sheets || {}), names = {};
          var add = el("select", { "aria-label": "Add a report" }, [el("option", { value: "", text: "Loading…" })]), list = el("div", { "class": "dk-book" });
          OPT.targets("mis_book").then(function (items) {
            items.forEach(function (t) { names[t.code] = t.name; }); fillAdd(items); draw();
          }, function (e) { M.clear(add).appendChild(el("option", { value: "", text: "Could not load: " + e.message })); });
          var all = [];
          function fillAdd(items) { if (items) all = items; M.clear(add); add.appendChild(el("option", { value: "", text: "Add a report…" }));
            all.forEach(function (t) { if (picked.indexOf(t.code) < 0) add.appendChild(el("option", { value: t.code, text: t.name })); }); }
          add.addEventListener("change", function () { if (!add.value) return; if (picked.length >= 30) { M.toast("A workbook holds at most 30 reports.", "bad"); add.value = ""; return; } picked.push(add.value); fillAdd(); draw(); });
          function draw() {
            M.clear(list);
            if (!picked.length) { list.appendChild(el("div", { "class": "dk-hint", text: "No reports yet - add them in the order the sheets should appear." })); return; }
            picked.forEach(function (slug, i) {
              var sn = el("input", { type: "text", maxlength: "31", value: sheets[slug] || "", placeholder: "Sheet name (default: report name)", "aria-label": "Sheet name for " + slug });
              sn.addEventListener("input", function () { if (sn.value.trim()) sheets[slug] = sn.value.trim(); else delete sheets[slug]; });
              var up = iconBtn("chev", "Move up", null, function () { if (i) { picked.splice(i - 1, 0, picked.splice(i, 1)[0]); draw(); } }, i === 0);
              up.firstChild.style.transform = "rotate(-90deg)";
              var dn = iconBtn("chev", "Move down", null, function () { if (i < picked.length - 1) { picked.splice(i + 1, 0, picked.splice(i, 1)[0]); draw(); } }, i === picked.length - 1);
              dn.firstChild.style.transform = "rotate(90deg)";
              var rm = iconBtn("x", "Remove from the workbook", "del", function () { picked.splice(i, 1); delete sheets[slug]; fillAdd(); draw(); });
              list.appendChild(el("div", { "class": "r" }, [el("span", { "class": "i", text: String(i + 1) }), el("span", { text: (names[slug] || slug) + (names[slug] ? "  (" + slug + ")" : ""), title: slug }), sn,
                el("span", { "class": "row", style: { gap: "0", flexWrap: "nowrap" } }, [up, dn, rm])]));
            });
          }
          draw();
          var rule = ruleControl(cfg, true);
          return { el: el("div", { "class": "stack" }, [field("Reports (one sheet each, in this order)", add, "Built on MIS → Reports. 1 to 30 reports; every sheet covers the same date range."), list,
              el("div", { "class": "dk-g2" }, [rule.el, field("Format", el("div", { "class": "small", text: "Excel (.xlsx) - a workbook is always xlsx" }), "File: <file name>.xlsx in the dated subfolder.")])]),
            collect: function () {
              if (!picked.length) throw new Error("Add at least one MIS report to the workbook.");
              var c = Object.assign(strip(clone(cfg), ["dateRule", "from_date", "to_date", "sheets"]), rule.get()), sh = {};
              picked.forEach(function (p) { if (sheets[p]) sh[p] = sheets[p]; });
              if (Object.keys(sh).length) c.sheets = sh;
              return { target: picked.join(","), config: c, format: "xlsx" };
            } };
        },
        day_trend: function (cfg) {
          var p = OPT.dtParams(), dims = function () { return p.then(dimItems); };
          var rep = el("select", { "aria-label": "Saved Day Wise Trend Report" }, [el("option", { value: cfg.reportSlug || "", text: cfg.reportSlug ? cfg.reportSlug : "Loading…" })]);
          OPT.dtReports().then(function (reps) {
            M.clear(rep); rep.appendChild(el("option", { value: "", text: "None - pick the grain below" }));
            reps.forEach(function (r) { rep.appendChild(el("option", { value: r.slug, text: r.name, selected: r.slug === cfg.reportSlug })); });
            if (cfg.reportSlug && !reps.some(function (r) { return r.slug === cfg.reportSlug; })) rep.appendChild(el("option", { value: cfg.reportSlug, selected: true, text: cfg.reportSlug + "  (not found - deleted or renamed?)" }));
          }, function (e) { M.clear(rep); rep.appendChild(el("option", { value: cfg.reportSlug || "", text: cfg.reportSlug || "Could not load the saved reports: " + e.message })); });
          var days = el("input", { type: "number", min: "1", max: String(DT_MAX_DAYS), value: cfg.days || 30, style: { width: "100px" } });
          var fm = seg([["xlsx", "xlsx - the workbook"], ["csv", "csv - flat"]], cfg.format === "csv" ? "csv" : "xlsx", null, "Format");
          var gb = ms("Group by", dims, cfg.groupBy, "Leave empty for the workbook's own grain (store × variant). An article-deep grain (" + DT_ARTICLE_DIMS.slice(0, 2).join(", ") + "…) needs a filter or a split - unfiltered it is about 1.5 crore rows.", { allText: "Store × Variant (the workbook)" });
          var tags = ms("Stock tags", function () { return p.then(function (x) { return x.stockTags || []; }); }, cfg.stockTags, null, { allText: "Default (0001 + 0006)" });
          var meas = ms("Sale measures", function () { return p.then(function (x) { return (x.measures || []).map(function (v) { return { value: v, label: (x.measureLabels || {})[v] || v }; }); }); }, cfg.measures, null, { allText: "Default (Sale Q / V + GM)" });
          var lv = ms("Split by (one file per value)", dims, cfg.levels, "Leave empty for ONE file. One file per combination that occurs in the window; a split also makes an article-deep grain legal.", { allText: "Nothing - one file" });
          var filt = dimFilters(dims, cfg.colFilters, function (d, others) {
            return p.then(function (x) {
              var to = x.latest; if (!to) throw new Error("No stock day is loaded.");
              var n = clamp(days.value, 1, DT_MAX_DAYS);
              return M.post("/api/v1/mis/day-trend/values", { dim: d, from_date: M.addDays(to, -(n - 1)), to_date: to, filters: others }).then(function (r) { return r.values || []; });
            });
          }, "Narrow every run of this schedule. Values are those in the schedule's own window. Leave empty to dump the whole window.");
          var inline = el("div", { "class": "stack" }, [gb.el, el("div", { "class": "dk-g2" }, [tags.el, meas.el])]);
          var daysF = field("Days", days, "The width of the sheet, ending on the latest loaded stock day - resolved on every firing. At most " + DT_MAX_DAYS + ": each day is its own block of columns.");
          function vis() { var r = !!rep.value; inline.classList.toggle("hidden", r); daysF.classList.toggle("hidden", r); }
          rep.addEventListener("change", vis); vis();
          return { el: el("div", { "class": "stack" }, [field("Saved Day Wise Trend Report", rep, "A saved report brings its own grain, stock tags, measures, filters and days, read when the schedule fires. Format, split and extra filters below still apply."),
              el("div", { "class": "dk-g2" }, [daysF, field("Format", fm.el)]), inline, lv.el, filt.el]),
            collect: function () {
              var c = strip(clone(cfg), ["reportSlug", "days", "format", "groupBy", "stockTags", "measures", "levels", "colFilters"]);
              c.format = fm.get(); c.levels = lv.get(); c.colFilters = filt.get();
              if (rep.value) c.reportSlug = rep.value;
              else {
                c.days = clamp(days.value, 1, DT_MAX_DAYS); c.groupBy = gb.get(); c.stockTags = tags.get(); c.measures = meas.get();
                var deep = c.groupBy.some(function (g) { return DT_ARTICLE_DIMS.indexOf(g) >= 0; });
                if (deep && !c.levels.length && !countFilters(c.colFilters)) throw new Error("An article-deep grouping needs a filter or a split - unfiltered it is about 1.5 crore rows and the run would fail.");
              }
              return { target: c.reportSlug || "day_trend", config: c, format: c.format };
            } };
        },
        inv_report: function (cfg, tgt) {
          var cur = cfg.slug || (cfg.slugs || [])[0] || tgt || "";
          var rep = el("select", { "aria-label": "Inventory report" }, [el("option", { value: cur, text: cur || "Loading…" })]), reps = [];
          OPT.invReports().then(function (r) {
            reps = r; M.clear(rep); rep.appendChild(el("option", { value: "", text: r.length ? "Pick a saved Inventory report…" : "No saved Inventory reports yet" }));
            r.forEach(function (x) { rep.appendChild(el("option", { value: x.slug, text: x.name, selected: x.slug === cur })); });
            if (cur && !r.some(function (x) { return x.slug === cur; })) rep.appendChild(el("option", { value: cur, selected: true, text: cur + "  (not in your list)" }));
          }, function (e) { M.clear(rep); rep.appendChild(el("option", { value: cur, text: cur || "Could not load: " + e.message })); });
          var rule = selectEl(INV_RULES, cfg.dateRule === "yesterday" ? "yesterday" : "latest", "As of");
          var lv = ms("Level (one file per value)", function () {
            if (!rep.value) return Promise.reject(new Error("Pick a report first."));
            return OPT.invReports().then(function (r) {
              var x = r.filter(function (y) { return y.slug === rep.value; })[0], gb = (x && x.definition && x.definition.group_by) || [];
              if (!gb.length) throw new Error("This report has no group-by columns to split on.");
              return gb.map(function (c) { return { value: c, label: /^(SM|PM):/.test(c) ? c.slice(3) : c }; });
            });
          }, cfg.levels, "Leave empty for ONE file. Several levels mean the combination (ZONE and MJ = one file per zone-and-MJ that exists).", { allText: "Whole report in one file" });
          rep.addEventListener("change", function () { lv.reset(); lv.set([]); });
          var many = (cfg.slugs || []).length > 1 ? el("div", { "class": "notice warn" }, [M.icon("info", 16), el("span", { text: "This DFS schedule names " + cfg.slugs.length + " reports in one workbook; V2Pulse dumps the first one only (" + cfg.slugs[0] + ")." })]) : null;
          return { el: el("div", { "class": "stack" }, [many, el("div", { "class": "dk-g2" }, [field("Inventory report", rep, "Built on MIS → Inventory Reports."),
              field("As of", rule, "An inventory report is one day's stock: the latest built reading (the 08:00 rebuild), or yesterday. Resolved on every firing.")]),
              el("div", { "class": "dk-g2" }, [lv.el, field("Format", el("div", { "class": "small", text: "CSV (raw)" }), "DFS's \"format\" option (the report's uploaded Excel template) is not available in V2Pulse yet; files are plain CSV: <name>_<as-of>[__LEVEL_value].csv")])]),
            collect: function () {
              if (!rep.value) throw new Error("Pick the Inventory report to dump.");
              var c = strip(clone(cfg), ["slug", "slugs", "sheets", "bookName", "dateRule", "levels", "format"]);
              c.slug = rep.value; c.dateRule = rule.value; c.levels = lv.get(); c.format = "raw";
              return { target: rep.value, config: c, format: "csv" };
            } };
        },
        bin_wise: function (cfg) {
          var p = OPT.binParams();
          var lv = ms("Split by (one file per value)", function () { return Promise.resolve(BIN_COLS); }, cfg.levels,
            "Leave empty for ONE file holding the whole snapshot. Only combinations that occur get a file. These six are the only columns worth splitting on (LGPLA holds about 3.5 lakh bins).", { allText: "Nothing - one file" });
          var cf = clone(cfg.colFilters), boxes = el("div", { "class": "dk-g2" }), cnt = el("span", { "class": "muted" });
          function paint() { var n = countFilters(cf); cnt.textContent = n ? " (" + n + ")" : ""; }
          BIN_COLS.forEach(function (c) {
            var w = M.multiSelect(c, function () { return p.then(function (x) { return ((x.options || {})[c]) || []; }); }, cf[c] || [], function (v) { if (v.length) cf[c] = v; else delete cf[c]; paint(); }, { allText: "All" });
            boxes.appendChild(w.el);
          });
          paint();
          var filt = el("details", { "class": "dk-box", open: countFilters(cf) ? true : null }, [el("summary", {}, ["Optional filters", cnt]),
            el("div", { "class": "dk-hint", text: "Narrow every run of this schedule. Leave empty to dump the whole snapshot. Values are read from the bin table." }), boxes,
            el("div", { "class": "dk-hint warn", text: "Filtered runs write the same file names as an unfiltered dump - give this schedule its own folder." })]);
          return { el: el("div", { "class": "stack" }, [el("div", { "class": "notice" }, [M.icon("info", 16), el("span", { text: "Always the latest snapshot - there is no date to pick. It is resolved on every firing, and the file names carry the snapshot date (<name>_<date>…)." })]),
              el("div", { "class": "dk-g2" }, [lv.el, field("Format", el("div", { "class": "small", text: "CSV (raw)" }), "DFS's Bin Wise template and \"one workbook, a tab per value\" are not available in V2Pulse yet; DFS too falls back to CSV without a template.")]), filt]),
            collect: function () {
              var c = strip(clone(cfg), ["levels", "colFilters", "format", "sheetPer"]);
              c.levels = lv.get(); c.colFilters = {}; Object.keys(cf).forEach(function (k) { if ((cf[k] || []).length) c.colFilters[k] = cf[k]; });
              c.format = "raw"; c.sheetPer = false;
              return { target: "bin_wise", config: c, format: "csv" };
            } };
        },
        tna: function (cfg) {
          var p = OPT.tnaParams(), dims = function () { return p.then(dimItems); };
          var fm = seg([["xlsx", "xlsx - stages merged over each block"], ["csv", "csv - flat"]], cfg.format === "csv" ? "csv" : "xlsx", null, "Format");
          var gb = ms("Group by", dims, cfg.groupBy, "Leave empty for the PO line (the extract unchanged). Group above it and BUD / ACT / STATUS / DELAY become aggregates.", { allText: "PO × Article × Store (the line)" });
          var meas = ms("Measures", function () { return p.then(function (x) { return (x.measures || []).map(function (v) { return { value: v, label: (x.measureLabels || {})[v] || v }; }); }); }, cfg.measures, null, { allText: "Default measures" });
          var scols = ms("Stage columns", function () { return p.then(function (x) { return (x.stageCols || []).map(function (v) { return { value: v, label: v + ((x.stageColNotes || {})[v] ? " - " + x.stageColNotes[v] : "") }; }); }); }, cfg.stageCols, null, { allText: "Default (BUD / ACT / STATUS)" });
          var stages = ms("Stages", function () { return p.then(function (x) { return (x.stages || []).map(function (v) { return { value: v, label: ((x.stageLabels || {})[v] || v) + ((x.stageUse || {})[v] === 0 ? "  ·  unused" : "") }; }); }); }, cfg.stages,
            "One block of columns each, in the order the goods move. Stages marked unused have never been stamped in this extract - ticking one writes blank columns.", { allText: "Default stages" });
          var lv = ms("Split by (one file per value)", dims, cfg.levels, "Leave empty for ONE file. One file per combination that occurs in the extract - split by vendor to give each one its own sheet.", { allText: "Nothing - one file" });
          var filt = dimFilters(dims, cfg.colFilters, function (d, others) {
            return M.post("/api/v1/mis/tna/values", { dim: d, filters: others }).then(function (r) { return r.values || []; });
          }, "Narrow every run of this schedule. Leave empty to dump the whole extract.");
          return { el: el("div", { "class": "stack" }, [el("div", { "class": "notice" }, [M.icon("info", 16), el("span", { text: "No dates on this kind: the extract is a snapshot of what is pending, replaced whole on each load. Files are stamped with the extract's load date (<name>_<load date>…), not the run time." })]),
              field("Format", fm.el), gb.el, el("div", { "class": "dk-g2" }, [meas.el, scols.el]), stages.el, lv.el, filt.el]),
            collect: function () {
              var c = strip(clone(cfg), ["format", "groupBy", "measures", "stages", "stageCols", "levels", "colFilters"]);
              c.format = fm.get(); c.groupBy = gb.get(); c.measures = meas.get(); c.stages = stages.get(); c.stageCols = scols.get(); c.levels = lv.get(); c.colFilters = filt.get();
              return { target: "tna", config: c, format: c.format };
            } };
        },
        mis_day_load: function (cfg) {
          var rule = ruleControl(cfg, false);
          var st = ms("Stores", function () { return OPT.stores(); }, cfg.stores, "Leave empty for every store. Naming stores narrows the procedures' delete-and-reinsert to those stores; the rest are untouched.", { allText: "All stores" });
          return { el: el("div", { "class": "stack" }, [el("div", { "class": "notice" }, [M.icon("info", 16), el("span", { text: "No files. This runs the two day-wise loader procedures and refreshes DAY_WISE_ST_VAR_SALE and _STOCK - the facts every MIS report reads. Each deletes the chosen days for the chosen stores and re-inserts them, so it is safe to re-run. Pair a nightly \"Yesterday\" load with the MIS dumps that read it, and set those to fire after it." })]),
              el("div", { "class": "dk-g2" }, [rule.el, st.el])]),
            collect: function () {
              var c = Object.assign(strip(clone(cfg), ["dateRule", "from_date", "to_date", "stores"]), rule.get());
              c.stores = st.get();
              return { target: "mis_day_load", config: c, format: "csv" };
            } };
        }
      };
      return ready;
    }
  });
})();

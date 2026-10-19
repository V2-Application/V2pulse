// Store Master (DFS /sf-mis-store-master = StoreMaster.jsx variant 'mis', with BulkUpload + TruncateButton): the MIS
// team's own store master, V2RETAIL.AKS_SILVER.SLV_MIS_STORE_MASTER (ST-MASTER.xlsx sheet ST, 24 columns - NOT the
// planning master's STORE MASTER.xlsx / SLV_DIM_STORE). Search, tick-list filters, column chooser, CSV + Excel export,
// sample file, upload (Bronze load + MERGE into Silver on ST_CD) and delete-all (mis.admin).
// API: /api/v1/mis/store-master/{params, result, export?fmt=csv|xlsx, sample, upload, all}.
(function () {
  "use strict";
  var M = window.MIS, el = M.el;
  var BASE = "/api/v1/mis/store-master";

  // DFS MIS_ALL_COLS: labels, order and the default-on set. NOTE: AREA is a REGION code (N2, E9), not floor space;
  // ST_STAT is the SAP plant status; OLD_NEW is the lifecycle flag; ST_FULL_NM is the readable name.
  var COLS = [
    ["ST_CD", "Store Code", 1], ["ST_FULL_NM", "Store Name", 1], ["ST_NM", "Short Nm", 0], ["ST_TYP", "Plant Type", 1],
    ["ST_STAT", "Status", 1], ["OLD_NEW", "Old / New", 1], ["ZONE", "Zone", 1], ["ZONE_RETAIL", "Retail Zone", 1],
    ["REG", "Region", 1], ["AREA", "Area Cd", 1], ["STATE", "State", 1], ["RDC_CD", "RDC CD", 1], ["RDC_NM", "RDC", 1],
    ["ST_PART", "Part", 1], ["ST_OP_DT", "Opening Date", 1], ["ST_CL_DT", "Closing Date", 0], ["ST_AREA", "St Area", 1],
    ["ACT_ST_AREA", "Act Area", 1], ["ST_FIX", "Fixtures", 1], ["AVG_AREA_PER_FIX", "Area / Fix", 0], ["AVG_PSF", "Avg PSF", 0],
    ["EXCESS_DAYS", "Excess Days", 0], ["IN_TR_DAYS", "In-Tr Days", 0], ["SN", "SN", 0]
  ];
  var LABEL = {}; COLS.forEach(function (c) { LABEL[c[0]] = c[1]; });
  // housekeeping columns never shown by default (DFS INTERNAL_COLS + the Bronze audit stamps)
  var INTERNAL = ["_ROW_ID", "ID", "CREATED_AT", "UPDATED_AT", "SYNCED_AT", "LOAD_ID", "LOAD_TS", "SRC_FILE", "LOADED_BY"];
  var INT_COLS = { ST_AREA: 1, ACT_ST_AREA: 1, SN: 1 };
  var DATE_COLS = { ST_OP_DT: 1, ST_CL_DT: 1 };

  // DFS COLUMNS_DEF_MIS - the upload contract shown under "View expected columns".
  var UPLOAD_COLS = [
    ["SN", 0, "Serial no. in the workbook - presentation only"], ["ZONE", 0, "NORTH / EAST / NA"], ["REG", 0, "Region code R1..R17 / NA"],
    ["AREA", 0, "Region AREA CODE (N2, E9) - text, NOT floor space"], ["ST_FULL_NM", 0, "Full store name, e.g. KAPASHERA"],
    ["ST_CD", 1, "Store / plant code (upsert key)"], ["ST_NM", 0, "Short store name, e.g. KPSHR"],
    ["ST_PART", 0, "Part group PART1..PART19 / NA - header may read ST-PART"], ["RDC_CD", 0, "DH24 / DW01 / NA"], ["RDC_NM", 0, "FRK / KOL"],
    ["ST_TYP", 0, "STORE / NA / HUB / DC / OFFICE-OLD / OFFICE-NEW / PLANT / ZONAL_OFFICE"], ["ST_FIX", 0, "Fixture count (numeric)"],
    ["AVG_AREA_PER_FIX", 0, "Sq ft per fixture (numeric)"], ["AVG_PSF", 0, "Average sale per sq ft (numeric)"],
    ["EXCESS_DAYS", 0, "Excess-cover days (numeric)"], ["ST_STAT", 0, "SAP plant status - ACT / IN-ACT / UPC / NA / SEDC / SEST"],
    ["STATE", 0, ""], ["ST_AREA", 0, "Leased area in sq ft - \"NA\" loads as blank"], ["ST_OP_DT", 0, "Opening date - \"NA\" loads as blank"],
    ["ST_CL_DT", 0, "Closing date - \"NA\" loads as blank"], ["ZONE_RETAIL", 0, "ZONE-1..ZONE-4 / NA - header may read ZONE-RETAIL"],
    ["IN_TR_DAYS", 0, "In-transit days (numeric)"], ["OLD_NEW", 0, "OLD / NEW / UPC / CLOSED - the lifecycle flag"],
    ["ACT_ST_AREA", 0, "Actual (carpet) area in sq ft"]
  ];
  var ST_STAT_CHIP = { ACT: "ok", "IN-ACT": "bad", UPC: "warn" };
  var OLD_NEW_CHIP = { OLD: "", NEW: "ok", UPC: "warn" };

  function csrf() { var m = document.cookie.match(/(?:^|;\s*)pulse_csrf=([^;]+)/); return m ? decodeURIComponent(m[1]) : ""; }
  function kb(n) { return (n / 1024).toFixed(1) + " KB"; }

  // Multipart upload with progress (fetch has no upload progress). Rejects with the server's message verbatim.
  function postFile(url, file, onProgress) {
    return new Promise(function (res, rej) {
      var x = new XMLHttpRequest(), fd = new FormData();
      fd.append("file", file);
      x.open("POST", url); x.withCredentials = true;
      x.setRequestHeader("X-CSRF-Token", csrf()); x.setRequestHeader("Accept", "application/json");
      x.upload.onprogress = function (e) { if (e.lengthComputable) onProgress(Math.round(100 * e.loaded / e.total)); };
      x.onload = function () {
        var d = null; try { d = x.responseText ? JSON.parse(x.responseText) : null; } catch (e) { d = null; }
        if (x.status >= 200 && x.status < 300) return res(d || {});
        var msg = x.status === 401 ? "Your V2Pulse session has ended - sign in again and reload this page."
          : d && typeof d.detail === "string" ? d.detail
          : d && Array.isArray(d.detail) ? d.detail.map(function (z) { return z.msg || JSON.stringify(z); }).join("; ")
          : x.status === 403 ? "You do not have access to this." : "The upload failed (error " + x.status + ").";
        rej(new Error(msg));
      };
      x.onerror = function () { rej(new Error("The upload could not reach V2Pulse - check the connection and try again.")); };
      x.send(fd);
    });
  }

  // Minimal CSV reader for the client-side preview (quoted fields, doubled quotes).
  function parseCsv(text, maxRows) {
    var rows = [], row = [], f = "", q = false, i = 0;
    if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
    for (; i < text.length && rows.length <= maxRows; i++) {
      var ch = text[i];
      if (q) { if (ch === '"') { if (text[i + 1] === '"') { f += '"'; i++; } else q = false; } else f += ch; continue; }
      if (ch === '"') q = true;
      else if (ch === ",") { row.push(f); f = ""; }
      else if (ch === "\n" || ch === "\r") { if (ch === "\r" && text[i + 1] === "\n") i++; row.push(f); f = ""; if (row.some(function (v) { return v !== ""; })) rows.push(row); row = []; }
      else f += ch;
    }
    if (f !== "" || row.length) { row.push(f); if (row.some(function (v) { return v !== ""; })) rows.push(row); }
    return rows;
  }

  M.page("store-master", {
    render: function (ctx) {
      var st = { search: "" }, P = null, grid = null, admin = !!ctx.me.can.admin;
      var sampleBtn = el("button", { type: "button", "class": "btn" }, [M.icon("down", 15), "Download sample file"]);
      var upBtn = admin ? el("button", { type: "button", "class": "btn primary" }, [M.icon("upload", 15), "Upload"]) : null;
      var delBtn = admin ? el("button", { type: "button", "class": "btn" }, [M.icon("trash", 15), "Delete all records"]) : null;
      M.setHeader({ title: "Store Master", sub: "The MIS team's own store master, V2RETAIL.AKS_SILVER.SLV_MIS_STORE_MASTER - its own 24-column document (ST-MASTER.xlsx, sheet ST), not a copy of the planning master. Note: Area Cd is a REGION code (N2, E9); floor space is St Area / Act Area.",
                    actions: [sampleBtn, upBtn, delBtn],
                    tiles: [{ k: "Stores", skel: true }, { k: "Active (ACT)", skel: true }, { k: "Type STORE", skel: true }, { k: "Last updated", skel: true }] });

      sampleBtn.addEventListener("click", function () { downloadSample(sampleBtn); });
      function downloadSample(btn) {
        M.action(btn, "Preparing…", function () { return M.download("GET", BASE + "/sample", undefined, "MIS_STORE_MASTER_SAMPLE.xlsx"); },
                 function (n) { return "Downloaded " + n + " - fill sheet ST and upload it here."; }).catch(function () { /* toasted */ });
      }

      // ── masthead tiles: row count + last update from /params, counts by ST_STAT / ST_TYP / ZONE via cheap filtered counts
      function countWhere(filters) {
        return M.post(BASE + "/result", { col_filters: filters, page: 1, limit: 1 }).then(function (r) { return r.total; }, function () { return null; });
      }
      function refreshHeader(p) {
        var opts = p.options || {}, has = function (c, v) { return (opts[c] || []).indexOf(v) >= 0; };
        var zones = (opts.ZONE || []).filter(function (z) { return z && z !== "NA" && z !== "(blank)"; }).slice(0, 3);
        var base = [{ k: "Stores", v: M.fmt(p.rows, "int"), s: "rows in the master" },
          { k: "Last updated", v: p.updatedAt ? M.fmtDate(p.updatedAt, false) : "–", s: p.updatedAt ? M.ago(p.updatedAt) : "no UPDATED_AT stamp" }];
        M.setHeader({ tiles: base.slice(0, 1).concat([{ k: "Active (ACT)", skel: true }, { k: "Type STORE", skel: true }]).concat(zones.map(function (z) { return { k: "Zone " + z, skel: true }; })).concat(base.slice(1)) });
        return Promise.all([has("ST_STAT", "ACT") ? countWhere({ ST_STAT: ["ACT"] }) : null, has("ST_TYP", "STORE") ? countWhere({ ST_TYP: ["STORE"] }) : null]
          .concat(zones.map(function (z) { return countWhere({ ZONE: [z] }); }))).then(function (n) {
          if (!ctx.alive()) return;
          var pct = function (v) { return v == null || !p.rows ? "" : M.fmt(100 * v / p.rows, "dec") + "% of the master"; };
          M.setHeader({ tiles: base.slice(0, 1).concat([
            { k: "Active (ACT)", v: n[0] == null ? "–" : M.fmt(n[0], "int"), s: n[0] == null ? "no ACT status in the master" : "SAP plant status · " + pct(n[0]) },
            { k: "Type STORE", v: n[1] == null ? "–" : M.fmt(n[1], "int"), s: n[1] == null ? "" : "rest are DC / HUB / office / NA" }])
            .concat(zones.map(function (z, i) { return { k: "Zone " + z, v: n[2 + i] == null ? "–" : M.fmt(n[2 + i], "int"), s: pct(n[2 + i]) }; })).concat(base.slice(1)) });
        });
      }
      function reloadAll() {
        return M.get(BASE + "/params").then(function (p) { if (!ctx.alive()) return; P = p; refreshHeader(p); if (grid) grid.reload(true); });
      }

      return M.get(BASE + "/params").then(function (p) {
        if (!ctx.alive()) return;
        P = p; refreshHeader(p);
        var types = p.types || {};
        function isNumCol(c) { return /NUMBER|DECIMAL|NUMERIC|INT|FLOAT|DOUBLE|REAL/.test(String(types[c] || "")); }

        // ── upload panel (admin) ─────────────────────────────────────────────────────────────────
        var panel = admin ? uploadPanel() : null;
        if (panel) ctx.main.appendChild(panel.el);
        if (upBtn) upBtn.addEventListener("click", function () { panel.open(true); panel.el.scrollIntoView({ behavior: "smooth", block: "start" }); });
        if (delBtn) delBtn.addEventListener("click", deleteAll);

        // ── filter bar + grid ───────────────────────────────────────────────────────────────────
        var search = el("input", { type: "search", placeholder: "Search store code or name (" + (p.searchCols || []).map(function (c) { return LABEL[c] || M.label(c); }).join(", ").toLowerCase() + ")", style: { minWidth: "340px" }, "aria-label": "Search" });
        search.addEventListener("input", M.debounce(function () { st.search = search.value.trim(); grid.reload(true); }, 300));
        var card = el("div", { "class": "card" });
        card.appendChild(el("div", { "class": "fbar" }, [el("label", { "class": "f" }, ["Search", search]), el("span", { "class": "spacer" }),
          el("span", { "class": "muted small", text: "Filter any of " + (p.filterCols || []).map(function (c) { return LABEL[c] || c; }).join(", ") + " from its column header." })]));
        ctx.main.appendChild(card);

        // DFS default layout (order, default-off columns, Store Code frozen) - only until the user arranges their own.
        if (M.store.get("grid.store-master", null) == null) {
          var order = COLS.map(function (c) { return c[0]; }).filter(function (c) { return p.columns.indexOf(c) >= 0; });
          p.columns.forEach(function (c) { if (order.indexOf(c) < 0) order.push(c); });
          M.store.set("grid.store-master", { order: order, pin: 1, hidden: COLS.filter(function (c) { return !c[2]; }).map(function (c) { return c[0]; }).concat(INTERNAL) });
        }
        function body(gs) {
          return { search: st.search || null, col_filters: Object.keys(gs.filters).length ? gs.filters : null,
                   page: gs.page, limit: gs.limit, sort_col: gs.sort_col, sort_dir: gs.sort_dir };
        }
        function exp(fmt) {
          return function (gs) {
            M.toast("Preparing the " + fmt.toUpperCase() + " export…");
            M.download("POST", BASE + "/export?fmt=" + fmt, body(gs), "MIS_STORE_MASTER." + fmt).then(function (n) { M.toast("Downloaded " + n, "ok"); }, function (e) { M.toast(e.message, "bad"); });
          };
        }
        grid = M.Grid(card, {
          id: "store-master", title: "Stores", conditions: false, sort_col: p.key || "ST_CD",
          label: function (c) { return LABEL[c] || M.label(c); },
          isKey: function (c) { return !isNumCol(c); },
          format: function (c) { return isNumCol(c) ? (INT_COLS[c] ? "int" : "dec") : "text"; },
          cell: function (td, c, v) {
            if (c === "ST_CD") td.style.fontWeight = "600";
            if (DATE_COLS[c] && v) td.textContent = M.fmtDate(v, false);
            if ((c === "ST_STAT" || c === "OLD_NEW") && v != null && v !== "") {
              var map = c === "ST_STAT" ? ST_STAT_CHIP : OLD_NEW_CHIP;
              M.clear(td).appendChild(el("span", { "class": "chip " + (map[v] === undefined ? "grey" : map[v]), text: String(v) }));
            }
          },
          fetch: function (gs, signal) { return M.post(BASE + "/result", body(gs), { signal: signal }); },
          values: function (c) {
            if ((p.options || {})[c]) return Promise.resolve(p.options[c]);
            return Promise.reject(new Error("only " + (p.filterCols || []).map(function (x) { return LABEL[x] || x; }).join(", ") + " have value lists - use Search for the rest"));
          },
          exports: ctx.me.can.export ? [{ label: "CSV - the whole filtered selection", fn: exp("csv") }, { label: "Excel - the whole filtered selection", fn: exp("xlsx") }] : []
        });
        return grid.applyDefaultView().then(function () { return grid.reload(true); });
      });

      // ── Delete all records (DFS TruncateButton / "Delete All Records"): type-to-confirm ─────────
      function deleteAll() {
        var n = P ? P.rows : null;
        var inp = el("input", { type: "text", placeholder: "Type DELETE", "aria-label": "Type DELETE to confirm", autocomplete: "off", style: { width: "220px" } });
        var ok = el("button", { type: "button", "class": "btn danger", disabled: true }, [M.icon("trash", 15), "Delete all " + (n == null ? "" : M.fmt(n, "int") + " ") + "records"]);
        var cancel = el("button", { type: "button", "class": "btn", text: "Cancel" });
        var m = M.modal("Delete all MIS store master records", [
          el("div", { "class": "notice bad" }, [M.icon("info", 16), el("span", { text: "This deletes every row of the MIS store master - all " + (n == null ? "" : M.fmt(n, "int") + " ") +
            "stores, not just the rows currently filtered - from AKS_SILVER.SLV_MIS_STORE_MASTER, and clears AKS_BRONZE.BRZ_MIS_STORE_MASTER too. It cannot be undone: re-upload ST-MASTER.xlsx to restore it." })]),
          el("label", { "class": "f" }, ["Type DELETE to confirm", inp])], [cancel, ok]);
        inp.addEventListener("input", function () { ok.disabled = inp.value.trim().toUpperCase() !== "DELETE"; });
        cancel.addEventListener("click", function () { m.close(); });
        ok.addEventListener("click", function () {
          M.action(ok, "Deleting…", function () { return M.api("DELETE", BASE + "/all"); }, function (r) {
            return r && r.deleted != null ? "Deleted " + M.fmt(r.deleted, "int") + " store master rows" : "All MIS store master records deleted";
          }).then(function () { m.close(); if (ctx.alive()) reloadAll(); }, function () { /* toasted */ });
        });
      }

      // ── Bulk upload panel (DFS BulkUpload, collapsible) ────────────────────────────────────────
      function uploadPanel() {
        var file = null, busy = false;
        var toggle = el("button", { type: "button", "class": "btn ghost sm", "aria-expanded": "false" }, [M.icon("chev", 14), "Show"]);
        var bodyEl = el("div", { "class": "card-b stack hidden" });
        var head = el("div", { "class": "card-h" }, [M.icon("upload", 16), el("h2", { text: "Bulk upload MIS Store Master" }),
          el("span", { "class": "note", text: "Upload mode MERGE on ST_CD - fixed by the Bronze-to-Silver promotion, so there is no append / truncate choice." }),
          el("span", { "class": "spacer" }), toggle]);
        head.style.cursor = "pointer";
        function open(v) { bodyEl.classList.toggle("hidden", !v); toggle.setAttribute("aria-expanded", String(v)); M.clear(toggle).appendChild(M.icon("chev", 14)); toggle.appendChild(document.createTextNode(v ? "Hide" : "Show")); toggle.firstChild.style.transform = v ? "rotate(90deg)" : ""; }
        head.addEventListener("click", function (e) { if (e.target.closest("button") && e.target.closest("button") !== toggle) return; open(bodyEl.classList.contains("hidden")); });

        var input = el("input", { type: "file", accept: ".xlsx,.csv", "class": "hidden", "aria-label": "Choose a file" });
        var zoneText = el("div", { "class": "stack", style: { alignItems: "center", gap: "4px" } });
        var zone = el("div", { role: "button", tabindex: "0", "aria-label": "Drop a file here or press to choose one",
          style: { border: "2px dashed var(--line)", borderRadius: "12px", padding: "22px 16px", textAlign: "center", cursor: "pointer", background: "var(--surface-2)", transition: "border-color .15s, background .15s" } }, [zoneText]);
        var preview = el("div"), progress = el("div", { "class": "hidden" }), result = el("div");
        var upload = el("button", { type: "button", "class": "btn primary", disabled: true }, [M.icon("upload", 15), "Upload file"]);
        var sample2 = el("button", { type: "button", "class": "btn" }, [M.icon("down", 15), "Download sample file"]);
        var del2 = el("button", { type: "button", "class": "btn danger" }, [M.icon("trash", 15), "Delete all records"]);
        sample2.addEventListener("click", function () { downloadSample(sample2); });
        del2.addEventListener("click", deleteAll);

        function drawZone() {
          M.clear(zoneText);
          if (file) {
            var clr = el("button", { type: "button", "class": "btn ghost sm", "aria-label": "Remove the file" }, [M.icon("x", 14)]);
            clr.addEventListener("click", function (e) { e.stopPropagation(); setFile(null); });
            zoneText.appendChild(el("div", { "class": "row", style: { justifyContent: "center" } }, [M.icon("report", 20),
              el("div", { style: { textAlign: "left" } }, [el("b", { text: file.name }), el("div", { "class": "muted small", text: kb(file.size) })]), clr]));
          } else {
            zoneText.appendChild(M.icon("upload", 22));
            zoneText.appendChild(el("b", { text: "Drag and drop ST-MASTER.xlsx here, or click to choose it" }));
            zoneText.appendChild(el("span", { "class": "muted small", text: "Excel (.xlsx - sheet ST, else the first sheet) or CSV · up to 25 MB" }));
          }
        }
        function setFile(f) {
          M.clear(result); M.clear(preview);
          if (f && !/\.(xlsx|csv)$/i.test(f.name)) { result.appendChild(el("div", { "class": "notice bad", text: "“" + f.name + "” is not an .xlsx or .csv file. Save the workbook as Excel (.xlsx) or CSV and choose it again." })); f = null; }
          file = f; upload.disabled = !file; input.value = ""; drawZone();
          if (file && /\.csv$/i.test(file.name)) {
            file.slice(0, 256 * 1024).text().then(function (t) {
              if (file !== f) return;
              var rows = parseCsv(t, 20); if (!rows.length) return;
              var hdr = rows[0], tb = el("tbody");
              rows.slice(1).forEach(function (r) { tb.appendChild(el("tr", {}, hdr.map(function (h, i) { return el("td", { text: r[i] == null ? "" : r[i] }); }))); });
              preview.appendChild(el("div", { "class": "section-title", text: "Preview - first " + (rows.length - 1) + " row(s)" }));
              preview.appendChild(el("div", { "class": "dg-wrap", style: { maxHeight: "220px", minHeight: "0", border: "1px solid var(--line-2)", borderRadius: "8px" } },
                el("table", { "class": "dg-t" }, [el("thead", {}, el("tr", {}, hdr.map(function (h) { return el("th", { text: h }); }))), tb])));
            }, function () { /* no preview */ });
          } else if (file) {
            preview.appendChild(el("div", { "class": "muted small", text: "The whole workbook is checked by the server when you upload it - headers, the ST_CD key, dates, numbers and the ST_TYP / ST_STAT / ZONE values - and nothing is loaded unless every row passes." }));
          }
        }
        zone.addEventListener("click", function () { if (!busy) input.click(); });
        zone.addEventListener("keydown", function (e) { if ((e.key === "Enter" || e.key === " ") && !busy) { e.preventDefault(); input.click(); } });
        input.addEventListener("change", function () { if (input.files && input.files[0]) setFile(input.files[0]); });
        function hl(on) { zone.style.borderColor = on ? "var(--brand-2)" : "var(--line)"; zone.style.background = on ? "var(--brand-soft)" : "var(--surface-2)"; }
        zone.addEventListener("dragover", function (e) { e.preventDefault(); hl(true); });
        zone.addEventListener("dragleave", function () { hl(false); });
        zone.addEventListener("drop", function (e) { e.preventDefault(); hl(false); if (!busy && e.dataTransfer.files && e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); });

        upload.addEventListener("click", function () {
          if (!file || busy) return;
          busy = true; upload.disabled = true; M.clear(result);
          var bar = el("div", { style: { height: "100%", width: "0%", background: "var(--brand-2)", borderRadius: "999px", transition: "width .2s" } });
          var lab = el("span", { text: "Uploading… 0%" });
          M.clear(progress).appendChild(el("div", { "class": "stack", style: { gap: "4px" } }, [el("div", { "class": "row small muted" }, [el("span", { "class": "spin" }), lab]),
            el("div", { style: { height: "8px", background: "var(--surface-3)", borderRadius: "999px", overflow: "hidden" } }, bar)]));
          progress.classList.remove("hidden");
          var name = file.name;
          postFile(BASE + "/upload", file, function (pct) {
            bar.style.width = pct + "%"; lab.textContent = pct < 100 ? "Uploading… " + pct + "%" : "File received - checking every row and merging into Silver…";
          }).then(function (r) {
            progress.classList.add("hidden"); busy = false;
            M.clear(result).appendChild(el("div", { "class": "notice ok", style: { flexDirection: "column", gap: "8px" } }, [
              el("b", { text: "Upload successful" }), el("span", { text: r.message || "Loaded." }),
              el("dl", { "class": "kv", style: { color: "var(--fg)" } }, [
                el("dt", { text: "File" }), el("dd", { text: name }),
                el("dt", { text: "Rows in the file" }), el("dd", { text: M.fmt(r.rows, "int") }),
                el("dt", { text: "Rows merged" }), el("dd", { text: r.merged == null ? "–" : M.fmt(r.merged, "int") }),
                el("dt", { text: "Stores in the master now" }), el("dd", { text: M.fmt(r.silverRows, "int") }),
                el("dt", { text: "Columns loaded" }), el("dd", { text: (r.columns || []).length + (r.columns && r.columns.length ? ": " + r.columns.join(", ") : "") }),
                el("dt", { text: "Load ID" }), el("dd", { style: { fontFamily: "monospace" }, text: r.loadId || "–" })])]));
            M.toast(M.fmt(r.rows, "int") + " stores uploaded", "ok");
            file = null; drawZone(); M.clear(preview);
            if (ctx.alive()) reloadAll();
          }, function (e) {
            progress.classList.add("hidden"); busy = false; upload.disabled = !file;
            // The server's validation message, verbatim: it names the rows / columns to fix. Nothing was loaded.
            M.clear(result).appendChild(el("div", { "class": "notice bad", role: "alert" }, [M.icon("x", 16),
              el("div", { "class": "stack", style: { gap: "4px" } }, [el("b", { text: "Upload failed" }), el("span", { style: { whiteSpace: "pre-wrap" }, text: e.message })])]));
          });
        });

        var colTable = el("table", { "class": "dg-t", style: { width: "100%" } }, [
          el("thead", {}, el("tr", {}, ["#", "Column name", "Required", "What it holds"].map(function (h) { return el("th", { text: h }); }))),
          el("tbody", {}, UPLOAD_COLS.map(function (c, i) {
            return el("tr", {}, [el("td", { "class": "muted", text: String(i + 1) }), el("td", { style: { fontFamily: "monospace", fontWeight: "600" }, text: c[0] }),
              el("td", {}, c[1] ? el("span", { "class": "chip bad", text: "Yes" }) : el("span", { "class": "muted", text: "No" })), el("td", { "class": "muted", text: c[2] })]);
          }))]);

        bodyEl.appendChild(el("p", { "class": "muted small", style: { margin: 0 }, text: "Upload ST-MASTER.xlsx (sheet ST) - the MIS module's own 24-column store master, NOT the planning master's STORE MASTER.xlsx. It is loaded to AKS_BRONZE.BRZ_MIS_STORE_MASTER, then MERGEd into AKS_SILVER.SLV_MIS_STORE_MASTER by ST_CD in the same request (the planning master is not touched). A partial file never drops stores. If any check fails the upload is rejected and nothing is loaded, so a success message always means Silver is current." }));
        bodyEl.appendChild(zone); bodyEl.appendChild(input); bodyEl.appendChild(preview); bodyEl.appendChild(progress);
        bodyEl.appendChild(el("div", { "class": "row" }, [upload, sample2, el("span", { "class": "spacer" }), del2]));
        bodyEl.appendChild(result);
        bodyEl.appendChild(el("details", {}, [el("summary", { "class": "small", style: { cursor: "pointer", color: "var(--brand)", fontWeight: "600" }, text: "View expected columns (" + UPLOAD_COLS.length + ")" }),
          el("div", { style: { marginTop: "8px", border: "1px solid var(--line-2)", borderRadius: "8px", overflow: "auto" } }, colTable),
          el("p", { "class": "muted tiny", text: "Headers are matched case-insensitively; spaces and hyphens read as underscores (ST-PART = ST_PART). Every column in the file must exist in the table. Dates are day-first (DD-MMM-YYYY, DD-MM-YYYY) or YYYY-MM-DD; NA in a date or number cell loads as blank." })]));
        drawZone();
        return { el: el("div", { "class": "card" }, [head, bodyEl]), open: open };
      }
    }
  });
})();

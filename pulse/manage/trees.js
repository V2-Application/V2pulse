// Library trees page: lists the Reports and Masters trees the signed-in user can see and renames the ones
// they may edit (PUT /api/v1/<section>/trees/<tree>). Portal administrators can also create new directories
// in a tree (POST /api/v1/<section>/folders), gated on the <section>.folders.create permission.
// Uses the normal V2Pulse session cookie and the double-submit CSRF token (pulse_csrf cookie -> X-CSRF-Token).
(function () {
  "use strict";
  var SECTIONS = [["reports", "Reports library"], ["masters", "Masters library"]];
  var statusEl = document.getElementById("status");
  var sectionsEl = document.getElementById("sections");
  var PERMS = {};
  var FOLDER = '<svg class="folder" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke-width="1.6" ' +
               'stroke-linejoin="round" aria-hidden="true"><path d="M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/></svg>';

  function csrf() {
    var m = document.cookie.match(/(?:^|;\s*)pulse_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function api(method, url, body) {
    var opts = { method: method, credentials: "same-origin", headers: { "Accept": "application/json" } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.headers["X-CSRF-Token"] = csrf();
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (r) {
      return r.text().then(function (t) {
        var data = null;
        try { data = t ? JSON.parse(t) : null; } catch (e) { data = null; }
        return { status: r.status, ok: r.ok, data: data };
      });
    });
  }

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function errorText(res) {
    if (res.data && (res.data.detail || res.data.title)) return res.data.detail || res.data.title;
    return "The change was not saved (error " + res.status + ").";
  }

  // depth of a folder from its materialised path "/id/id/" -> number of ancestors
  function depthOf(path) {
    var parts = String(path || "").split("/").filter(Boolean);
    return Math.max(0, parts.length - 1);
  }

  // A "New directory" panel for one tree (portal admin only). Loads the tree's folders to offer a parent.
  function createPanel(section, tree) {
    var box = el("div", { "class": "newdir" });
    var title = el("div", { "class": "newdir-title" });
    var ic = el("span"); ic.innerHTML = FOLDER; title.appendChild(ic);
    title.appendChild(el("strong", {}, "New directory"));
    box.appendChild(title);

    var form = el("div", { "class": "newdir-form" });
    var name = el("input", { type: "text", maxlength: "120", placeholder: "Directory name", "aria-label": "New directory name" });
    var parent = el("select", { "aria-label": "Parent directory" });
    parent.appendChild(el("option", { value: "" }, "(top level)"));
    var create = el("button", { "class": "primary", type: "button" }, "Create directory");
    form.appendChild(name); form.appendChild(parent); form.appendChild(create);
    box.appendChild(form);
    var msg = el("div", { "class": "msg", role: "status" });
    box.appendChild(msg);

    function loadParents() {
      api("GET", "/api/v1/" + section + "/tree?tree=" + encodeURIComponent(tree)).then(function (res) {
        var d = res.data && res.data.data ? res.data.data : res.data;
        var folders = (d && d.folders) || [];
        folders.sort(function (a, b) { return String(a.path).localeCompare(String(b.path)); });
        // keep only the top option, then (re)add folders
        while (parent.options.length > 1) parent.remove(1);
        folders.forEach(function (f) {
          var label = new Array(depthOf(f.path) + 1).join(" ") + f.name;
          parent.appendChild(el("option", { value: f.id }, label));
        });
      });
    }
    loadParents();

    create.addEventListener("click", function () {
      var nm = name.value.trim();
      if (!nm) { msg.className = "msg bad"; msg.textContent = "Type a directory name."; name.focus(); return; }
      create.disabled = true; msg.className = "msg";
      api("POST", "/api/v1/" + section + "/folders", { tree: tree, parent_id: parent.value || null, name: nm })
        .then(function (res) {
          create.disabled = false;
          if (!res.ok) { msg.className = "msg bad"; msg.textContent = errorText(res); return; }
          msg.className = "msg ok";
          msg.textContent = "Created directory “" + nm + "”. Reload the library to see it.";
          name.value = "";
          loadParents();
        })
        .catch(function () {
          create.disabled = false;
          msg.className = "msg bad"; msg.textContent = "Could not reach V2Pulse. Check the network and try again.";
        });
    });
    return box;
  }

  // Portal-admin only: create a new top-level tree (section) in a library.
  function createSectionPanel() {
    var libs = [];
    if (PERMS["reports.folders.create"]) libs.push(["reports", "Reports"]);
    if (PERMS["masters.folders.create"]) libs.push(["masters", "Masters"]);
    if (!libs.length) return null;
    var box = el("section", { "class": "newtree" });
    var head = el("div", { "class": "head" });
    head.appendChild(el("h2", {}, "New section"));
    head.appendChild(el("span", { "class": "muted", style: "font-size:13px" }, "Create a new top-level tree in the library"));
    box.appendChild(head);
    var wrap = el("div", { "class": "newdir", style: "padding-left:18px" });
    var form = el("div", { "class": "newdir-form" });
    var name = el("input", { type: "text", maxlength: "60", placeholder: "Section name (e.g. Compliance)", "aria-label": "New section name" });
    var lib = el("select", { "aria-label": "Library" });
    libs.forEach(function (l) { lib.appendChild(el("option", { value: l[0] }, l[1])); });
    var create = el("button", { "class": "primary", type: "button" }, "Create section");
    form.appendChild(name);
    if (libs.length > 1) form.appendChild(lib);
    form.appendChild(create);
    wrap.appendChild(form);
    var msg = el("div", { "class": "msg", role: "status" });
    wrap.appendChild(msg);
    box.appendChild(wrap);
    create.addEventListener("click", function () {
      var nm = name.value.trim();
      if (!nm) { msg.className = "msg bad"; msg.textContent = "Type a section name."; name.focus(); return; }
      create.disabled = true; msg.className = "msg";
      var libCode = libs.length > 1 ? lib.value : libs[0][0];
      api("POST", "/api/v1/" + libCode + "/trees", { name: nm })
        .then(function (res) {
          create.disabled = false;
          if (!res.ok) { msg.className = "msg bad"; msg.textContent = errorText(res); return; }
          msg.className = "msg ok";
          msg.textContent = "Created section “" + nm + "”. Reloading…";
          setTimeout(function () { location.reload(); }, 900);
        })
        .catch(function () { create.disabled = false; msg.className = "msg bad"; msg.textContent = "Could not reach V2Pulse. Try again."; });
    });
    return box;
  }

  function renderSection(section, title, trees) {
    var canCreate = !!PERMS[section + ".folders.create"];
    var box = el("section");
    var head = el("div", { "class": "head" });
    head.appendChild(el("h2", {}, title));
    var editable = trees.filter(function (t) { return t.can_edit; }).length;
    head.appendChild(el("span", { "class": "muted", style: "font-size:13px" },
      editable ? editable + " of " + trees.length + " trees you can rename" : "You can see these trees but not rename them"));
    box.appendChild(head);
    var msg = el("div", { "class": "msg", role: "status" });
    box.appendChild(msg);

    trees.forEach(function (t) {
      var row = el("div", { "class": "row" });
      var icon = el("div"); icon.innerHTML = FOLDER; row.appendChild(icon);

      var nameCell = el("div");
      var id = section + "-" + t.tree.replace(/[^a-zA-Z0-9]/g, "_");
      var name = el("input", { id: id, value: t.name, maxlength: "60", "aria-label": "Name of tree " + t.name });
      if (!t.can_edit) name.disabled = true;
      nameCell.appendChild(name);
      var meta = el("div", { "class": "meta" }, (t.tree === "central" ? "Central tree" : "Department tree") +
        " · default name: " + t.default_name + " · " + t.folder_count + " folders, " + t.item_count + " items");
      if (t.mine) { var tag = el("span", { "class": "tag" }, "your department"); meta.appendChild(tag); }
      nameCell.appendChild(meta);
      row.appendChild(nameCell);

      var hiCell = el("div", { "class": "hi" });
      var hi = el("input", { id: id + "-hi", value: t.name_hi || "", maxlength: "60", placeholder: "Hindi name (optional)",
                             "aria-label": "Hindi name of tree " + t.name, lang: "hi" });
      if (!t.can_edit) hi.disabled = true;
      hiCell.appendChild(hi);
      row.appendChild(hiCell);

      var btns = el("div", { "class": "btns" });
      var save = el("button", { "class": "primary", type: "button" }, "Save");
      var reset = el("button", { type: "button", title: "Go back to '" + t.default_name + "'" }, "Use default");
      if (!t.can_edit) { save.disabled = true; reset.disabled = true; }
      btns.appendChild(save); btns.appendChild(reset);
      row.appendChild(btns);

      function submit(newName, newHi) {
        save.disabled = reset.disabled = true;
        msg.className = "msg";
        api("PUT", "/api/v1/" + section + "/trees/" + encodeURIComponent(t.tree), { name: newName, name_hi: newHi })
          .then(function (res) {
            save.disabled = reset.disabled = false;
            if (!res.ok) { msg.className = "msg bad"; msg.textContent = errorText(res); return; }
            var shown = newName && newName.trim() ? newName.trim() : t.default_name;
            name.value = shown; t.name = shown;
            if (!newName) hi.value = "";
            msg.className = "msg ok";
            msg.textContent = "Saved. The tree is now called “" + shown + "”. Reload the library to see it.";
          })
          .catch(function () {
            save.disabled = reset.disabled = false;
            msg.className = "msg bad"; msg.textContent = "Could not reach V2Pulse. Check the network and try again.";
          });
      }
      save.addEventListener("click", function () {
        if (!name.value.trim()) { msg.className = "msg bad"; msg.textContent = "Type a name, or use Use default."; name.focus(); return; }
        submit(name.value.trim(), hi.value.trim() || null);
      });
      reset.addEventListener("click", function () { submit("", null); });
      box.appendChild(row);

      // Portal-admin only: create directories within this tree.
      if (canCreate && t.can_edit) box.appendChild(createPanel(section, t.tree));
    });
    return box;
  }

  api("GET", "/api/v1/me").then(function (me) {
    if (me.status === 401) {
      statusEl.className = "notice";
      statusEl.innerHTML = 'Sign in to V2Pulse first, then come back to this page. <a href="/login">Sign in</a>';
      return null;
    }
    if (!me.ok) { statusEl.textContent = errorText(me); return null; }
    PERMS = {};
    (me.data && me.data.permissions ? me.data.permissions : []).forEach(function (p) { PERMS[p] = true; });
    var user = me.data && me.data.user ? me.data.user.display_name : "";
    var panel = createSectionPanel();
    if (panel) sectionsEl.appendChild(panel);
    return Promise.all(SECTIONS.map(function (s) { return api("GET", "/api/v1/" + s[0] + "/trees"); })).then(function (all) {
      var shown = 0;
      all.forEach(function (res, i) {
        if (res.ok && res.data && res.data.data) {
          sectionsEl.appendChild(renderSection(SECTIONS[i][0], SECTIONS[i][1], res.data.data));
          shown++;
        }
      });
      statusEl.className = "notice muted";
      statusEl.textContent = shown ? "Signed in as " + user + "." : "You do not have access to the Reports or Masters library.";
    });
  }).catch(function () {
    statusEl.textContent = "Could not reach V2Pulse. Check the network and reload.";
  });
})();

(function () {
  "use strict";
  var root = document.getElementById("cl-app");
  var project = root.dataset.project;
  var csrf = root.dataset.csrf;
  var data = null;
  var edits = {};
  var timer = null;
  var lastBlock = null;

  function $(id) { return document.getElementById(id); }
  function el(tag, text, cls) {
    var e = document.createElement(tag);
    if (text !== undefined && text !== null) { e.textContent = text; }
    if (cls) { e.className = cls; }
    return e;
  }
  function status(text, error) {
    var s = $("cl-status");
    s.textContent = text || "";
    s.className = error ? "error" : "";
  }
  function request(method, path, body) {
    var headers = {};
    var init = { method: method, credentials: "same-origin", headers: headers };
    if (body) { headers["Content-Type"] = "application/json"; headers["X-CSRF-Token"] = csrf; init.body = JSON.stringify(body); }
    return fetch(path, init).then(function (r) {
      if (r.status === 401) { window.location.href = "/login"; throw new Error("session expired"); }
      return r.json().then(function (j) {
        if (!r.ok) { throw new Error(j.error || "request failed"); }
        return j;
      });
    });
  }

  function effective(node, inherited) {
    if (Object.prototype.hasOwnProperty.call(edits, node.path)) { return edits[node.path]; }
    if (inherited) { return inherited; }
    return null;
  }
  function kindOf(node, inherited) {
    var e = effective(node, inherited);
    if (e) { return e; }
    var ks = Object.keys(node.kinds);
    return ks.length === 1 ? ks[0] : "mixed";
  }
  function touched(node) {
    if (Object.prototype.hasOwnProperty.call(edits, node.path)) { return true; }
    return node.type === "dir" && node.children.some(touched);
  }
  function visible(node, inherited, f) {
    var inh = effective(node, inherited);
    if (node.type === "file") {
      var k = inh || node.kind;
      if (f.kind && k !== f.kind) { return false; }
      if (f.weak && !node.weak) { return false; }
      if (f.changed && !touched(node)) { return false; }
      if (f.search && node.path.toLowerCase().indexOf(f.search) < 0) { return false; }
      return true;
    }
    return node.children.some(function (c) { return visible(c, inh, f); });
  }

  function select(node, inherited) {
    var s = el("select");
    var ruleKinds = data.ruleKinds;
    var cur = kindOf(node, inherited);
    if (cur === "mixed") { var m = el("option", "mixed"); m.value = ""; s.appendChild(m); }
    ruleKinds.forEach(function (k) { var o = el("option", k); o.value = k; s.appendChild(o); });
    s.value = cur === "mixed" ? "" : cur;
    s.setAttribute("aria-label", "Kind of " + node.path);
    s.addEventListener("change", function () {
      if (s.value) {
        Object.keys(edits).forEach(function (p) { if (p.indexOf(node.path + "/") === 0) { delete edits[p]; } });
        edits[node.path] = s.value;
      }
      refresh();
    });
    return s;
  }

  function row(node, inherited, leaf) {
    var r = el("div", null, "cl-row" + (leaf ? " cl-leaf" : ""));
    var name = el("span", node.name + (node.type === "dir" ? "/" : ""), touched(node) ? "cl-edited" : "");
    r.appendChild(name);
    if (node.weak) { var w = el("span", "?", "cl-weak"); w.title = "needs a look"; r.appendChild(w); }
    r.appendChild(select(node, inherited));
    if (node.type === "dir") { r.appendChild(el("span", node.files + " files", "cl-note")); }
    else { r.appendChild(el("span", touched(node) ? "you" : (node.source + ": " + node.reason), "cl-note")); }
    return r;
  }

  function render(node, inherited, f) {
    if (!visible(node, inherited, f)) { return null; }
    if (node.type === "file") { return row(node, inherited, true); }
    var inh = effective(node, inherited);
    var d = el("details");
    var s = el("summary");
    s.appendChild(row(node, inherited, false));
    d.appendChild(s);
    var filtered = f.kind || f.weak || f.changed || f.search;
    d.open = filtered ? true : Object.keys(node.kinds).length > 1 && !inh;
    node.children.forEach(function (c) {
      var e = render(c, inh, f);
      if (e) { d.appendChild(e); }
    });
    return d;
  }

  function filters() {
    return { kind: $("cl-kind").value, weak: $("cl-weak").checked, changed: $("cl-changed").checked, search: $("cl-search").value.toLowerCase() };
  }

  function drawTree() {
    var host = $("cl-tree");
    var open = {};
    Array.prototype.forEach.call(host.querySelectorAll("details"), function (d) {
      var n = d.querySelector("select");
      if (n && d.open) { open[n.getAttribute("aria-label")] = true; }
    });
    host.textContent = "";
    var f = filters();
    data.tree.children.forEach(function (c) {
      var e = render(c, null, f);
      if (e) { host.appendChild(e); }
    });
    Array.prototype.forEach.call(host.querySelectorAll("details"), function (d) {
      if (open[d.querySelector("select").getAttribute("aria-label")]) { d.open = true; }
    });
  }

  function drawSummary() {
    var box = $("cl-summary");
    box.textContent = "";
    var ing = data.ingestion;
    var head = "Last ingestion " + (ing ? "#" + ing.seq + (ing.createdAt ? " (" + ing.createdAt + ")" : "") : "none") +
      ", " + data.files + " files, classifier " + (data.classifierVersions.length ? "v" + data.classifierVersions.join(", v") : "unknown");
    box.appendChild(el("div", head));
    var kinds = Object.keys(data.kinds);
    box.appendChild(el("div", kinds.map(function (k) { return k + " " + data.kinds[k]; }).join(" | ")));
    var bar = el("div"); bar.id = "cl-bar";
    kinds.forEach(function (k) {
      var seg = el("span"); seg.style.flex = String(data.kinds[k]); seg.title = k + " " + data.kinds[k];
      bar.appendChild(seg);
    });
    box.appendChild(bar);
    if (data.outdated) { box.appendChild(el("div", "Warning: some artifacts were classified by another classifier version than v" + data.currentClassifierVersion + ". Ingest again to update them.", "error")); }
    if (data.weak) { box.appendChild(el("div", data.weak + " files need a look (marked ?).", "cl-note")); }
    if (data.truncated) { box.appendChild(el("div", "Warning: the file list is truncated.", "error")); }
    (ing ? ing.warnings : []).forEach(function (w) { box.appendChild(el("div", "Warning: " + w, "error")); });
    box.hidden = false;
  }

  function currentEdits() {
    return Object.keys(edits).map(function (p) { return { path: p, kind: edits[p] }; });
  }

  function updateBlock() {
    request("POST", "/api/classification/rules", { project: project, edits: currentEdits(), yaml: $("cl-yaml").value })
      .then(function (r) {
        lastBlock = r;
        $("cl-text").textContent = r.block;
        $("cl-diff").textContent = r.diff || "No changes.";
        status("");
      })
      .catch(function (e) { status(e.message, true); });
  }

  function refresh() {
    drawTree();
    clearTimeout(timer);
    timer = setTimeout(updateBlock, 200);
  }

  function copy() {
    if (!lastBlock) { return; }
    var text = $("cl-yaml").value ? lastBlock.fileText : lastBlock.block;
    if (navigator.clipboard) { navigator.clipboard.writeText(text).then(function () { status("Copied."); }, function () { status("Copy failed.", true); }); }
  }
  function download() {
    if (!lastBlock) { return; }
    var blob = new Blob([lastBlock.fileText], { type: "text/yaml" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "sandman.yaml";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
  }

  function load() {
    status("Loading...");
    request("GET", "/api/classification?project=" + encodeURIComponent(project)).then(function (d) {
      data = d;
      var sel = $("cl-kind");
      Object.keys(d.kinds).forEach(function (k) { var o = el("option", k); o.value = k; sel.appendChild(o); });
      drawSummary();
      $("cl-filters").hidden = false;
      $("cl-block").hidden = false;
      status(d.files ? "" : "No current artifacts for this project.");
      refresh();
    }).catch(function (e) { status(e.message, true); });
  }

  if (project) {
    ["cl-kind", "cl-weak", "cl-changed", "cl-search"].forEach(function (id) {
      $(id).addEventListener("input", function () { if (data) { drawTree(); } });
    });
    $("cl-yaml").addEventListener("input", function () { if (data) { refresh(); } });
    $("cl-diff-btn").addEventListener("click", function () { var d = $("cl-diff"); d.hidden = !d.hidden; });
    $("cl-copy").addEventListener("click", copy);
    $("cl-download").addEventListener("click", download);
    load();
  }
})();

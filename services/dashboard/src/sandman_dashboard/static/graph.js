(function () {
  "use strict";
  var root = document.getElementById("graph-app");
  var csrf = root.dataset.csrf;
  var cy = cytoscape({
    container: document.getElementById("cy"),
    wheelSensitivity: 0.3,
    style: [
      { selector: "node", style: { label: "data(label)", "font-size": 10, "background-color": "data(color)", color: "#1b1f24", "text-valign": "bottom", "text-margin-y": 3 } },
      { selector: "edge", style: { label: "data(label)", "font-size": 8, width: 1, "curve-style": "bezier", "target-arrow-shape": "triangle", "line-color": "#8c959f", "target-arrow-color": "#8c959f", color: "#57606a" } },
      { selector: ".path", style: { "line-color": "#d1242f", "target-arrow-color": "#d1242f", "border-width": 3, "border-color": "#d1242f" } },
      { selector: ":selected", style: { "border-width": 3, "border-color": "#0969da", "line-color": "#0969da", "target-arrow-color": "#0969da" } }
    ]
  });
  var selected = null;
  var pathEnds = { source: null, target: null };
  var lastCypher = [];

  function $(id) { return document.getElementById(id); }
  function status(text, error) {
    var el = $("status");
    el.textContent = text || "";
    el.className = error ? "error" : "";
  }
  function color(labels) {
    var key = (labels && labels[labels.length - 1]) || "";
    var h = 0;
    for (var i = 0; i < key.length; i++) { h = (h * 31 + key.charCodeAt(i)) % 360; }
    return "hsl(" + h + ", 60%, 78%)";
  }
  function checked(cls) {
    return Array.prototype.map.call(document.querySelectorAll("input." + cls + ":checked"), function (e) { return e.value; });
  }
  function query(params) {
    var q = new URLSearchParams();
    Object.keys(params).forEach(function (k) {
      var v = params[k];
      if (Array.isArray(v)) { v.forEach(function (x) { q.append(k, x); }); }
      else if (v !== null && v !== undefined && v !== "") { q.append(k, v); }
    });
    return q.toString();
  }
  function get(path, params) {
    return fetch(path + "?" + query(params), { credentials: "same-origin" }).then(function (r) {
      if (r.status === 401) { window.location.href = "/login"; throw new Error("session expired"); }
      return r.json().then(function (body) {
        if (!r.ok) { throw new Error(body.error || "request failed"); }
        return body;
      });
    });
  }
  function add(resp) {
    var fresh = resp.elements.filter(function (e) { return cy.getElementById(e.data.id).empty(); });
    fresh.forEach(function (e) { if (e.group === "nodes") { e.data.color = color(e.data.labels); } });
    cy.add(fresh);
    applyFilters();
    if (fresh.length) { cy.layout({ name: "cose", animate: false, fit: true, padding: 30 }).run(); }
    status(cy.nodes().length + " nodes, " + cy.edges().length + " edges" + (resp.truncated ? " (truncated at 500 nodes)" : ""));
  }
  function fail(e) { status(e.message, true); }
  function filters() { return { project: $("project").value, labels: checked("filter-label") }; }

  function applyFilters() {
    var labels = checked("filter-label");
    var types = checked("filter-type");
    cy.nodes().forEach(function (n) {
      var ok = !labels.length || n.data("labels").some(function (l) { return labels.indexOf(l) >= 0; });
      n.style("display", ok ? "element" : "none");
    });
    cy.edges().forEach(function (e) {
      var ok = !types.length || types.indexOf(e.data("label")) >= 0;
      e.style("display", ok ? "element" : "none");
    });
  }

  function show(ele) {
    var dl = $("props");
    dl.textContent = "";
    $("props-empty").hidden = true;
    var data = ele.data();
    var rows = [];
    if (ele.isNode()) { rows.push(["labels", data.labels.join(", ")]); } else { rows.push(["type", data.label]); }
    Object.keys(data.props).forEach(function (k) {
      var v = data.props[k];
      rows.push([k, typeof v === "string" ? v : JSON.stringify(v)]);
    });
    rows.forEach(function (r) {
      var dt = document.createElement("dt");
      var dd = document.createElement("dd");
      dt.textContent = r[0];
      dd.textContent = r[1];
      dl.appendChild(dt);
      dl.appendChild(dd);
    });
    $("set-source").hidden = !ele.isNode();
    $("set-target").hidden = !ele.isNode();
  }

  cy.on("tap", "node", function (evt) {
    var node = evt.target;
    selected = node;
    show(node);
    get("/api/graph/expand", { id: node.data("nid"), project: $("project").value, labels: checked("filter-label"), types: checked("filter-type") })
      .then(add).catch(fail);
  });
  cy.on("tap", "edge", function (evt) { selected = null; show(evt.target); });

  function setEnd(which) {
    if (!selected) { return; }
    pathEnds[which] = selected.data("nid");
    $("path-" + which).textContent = selected.data("label");
  }
  $("set-source").addEventListener("click", function () { setEnd("source"); });
  $("set-target").addEventListener("click", function () { setEnd("target"); });

  $("start").addEventListener("click", function () {
    var f = filters();
    get("/api/graph/start", { project: f.project, label: $("start-label").value, labels: f.labels }).then(add).catch(fail);
  });
  $("search").addEventListener("click", function () {
    var f = filters();
    get("/api/graph/search", { project: f.project, q: $("search-text").value, labels: f.labels }).then(add).catch(fail);
  });
  $("search-text").addEventListener("keydown", function (e) { if (e.key === "Enter") { $("search").click(); } });
  Array.prototype.forEach.call(document.querySelectorAll("input.filter-label, input.filter-type"), function (el) {
    el.addEventListener("change", applyFilters);
  });
  $("path").addEventListener("click", function () {
    if (pathEnds.source === null || pathEnds.target === null) { status("Choose a start and an end node first.", true); return; }
    get("/api/graph/path", { source: pathEnds.source, target: pathEnds.target, hops: $("path-hops").value }).then(function (resp) {
      add(resp);
      cy.elements(".path").removeClass("path");
      resp.elements.forEach(function (e) { cy.getElementById(e.data.id).addClass("path"); });
      if (!resp.found) { status("No path within the hop limit.", true); }
    }).catch(fail);
  });
  $("clear").addEventListener("click", function () { cy.elements().remove(); status(""); });

  function download(href, name) {
    var a = document.createElement("a");
    a.href = href;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
  $("export-png").addEventListener("click", function () {
    download(cy.png({ full: true, bg: "#ffffff", output: "base64uri" }), "subgraph.png");
  });
  $("export-json").addEventListener("click", function () {
    var blob = new Blob([JSON.stringify(cy.json().elements, null, 2)], { type: "application/json" });
    var url = URL.createObjectURL(blob);
    download(url, "subgraph.json");
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  });

  function table(columns, rows) {
    var t = document.createElement("table");
    var head = t.createTHead().insertRow();
    columns.forEach(function (c) { var th = document.createElement("th"); th.textContent = c; head.appendChild(th); });
    var body = t.createTBody();
    rows.forEach(function (row) {
      var tr = body.insertRow();
      row.forEach(function (v) { tr.insertCell().textContent = typeof v === "string" ? v : JSON.stringify(v); });
    });
    return t;
  }
  $("cypher-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var out = $("cypher-result");
    out.textContent = "";
    $("cypher-add").hidden = true;
    $("cypher-status").className = "";
    $("cypher-status").textContent = "Running";
    fetch("/api/cypher", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ cypher: $("cypher").value })
    }).then(function (r) {
      if (r.status === 401) { window.location.href = "/login"; throw new Error("session expired"); }
      return r.json().then(function (body) {
        if (!r.ok) { throw new Error(body.error || "request failed"); }
        return body;
      });
    }).then(function (resp) {
      $("cypher-status").textContent = resp.rows.length + " rows" + (resp.truncated ? " (truncated)" : "");
      out.appendChild(table(resp.columns, resp.rows));
      lastCypher = resp.elements;
      $("cypher-add").hidden = !lastCypher.length;
    }).catch(function (e) {
      $("cypher-status").textContent = e.message;
      $("cypher-status").className = "error";
    });
  });
  $("cypher-add").addEventListener("click", function () { add({ elements: lastCypher, truncated: false }); });
})();

(function () {
  "use strict";
  var root = document.getElementById("graph-app");
  if (!root) { return; }
  var csrf = root.dataset.csrf;
  var base = root.dataset.base;
  var scheme = window.matchMedia("(prefers-color-scheme: dark)");
  var theme = {};
  function readTheme() {
    var cs = getComputedStyle(document.documentElement);
    ["surface", "bg", "border", "text", "muted", "accent", "bad", "warn"].forEach(function (k) {
      theme[k] = cs.getPropertyValue("--" + k).trim();
    });
    theme.dark = scheme.matches;
  }
  function fill(hue) { return "hsl(" + hue + ", " + (theme.dark ? "55%, 52%" : "60%, 78%") + ")"; }
  function styleSheet() {
    return [
      { selector: "node", style: { label: "data(label)", "font-size": 10, "background-color": function (n) { return fill(n.data("hue")); }, color: theme.text, "text-valign": "bottom", "text-margin-y": 3, "text-wrap": "ellipsis", "text-max-width": 90 } },
      { selector: "edge", style: { label: "data(label)", "font-size": 8, "text-wrap": "ellipsis", "text-max-width": 80, width: 1, "curve-style": "bezier", "target-arrow-shape": "triangle", "line-color": theme.muted, "target-arrow-color": theme.muted, color: theme.muted } },
      { selector: "node[status = 'rejected']", style: { "border-width": 3, "border-style": "dashed", "border-color": theme.bad, opacity: 0.7 } },
      { selector: "node[status = 'OPEN'], node[status = 'REOPENED']", style: { "border-width": 3, "border-color": theme.bad } },
      { selector: "node[status = 'candidate']", style: { "border-width": 2, "border-style": "dotted", "border-color": theme.warn } },
      { selector: ".found", style: { "border-width": 4, "border-color": "#8250df" } },
      { selector: ".path", style: { "line-color": theme.bad, "target-arrow-color": theme.bad, "border-width": 3, "border-color": theme.bad } },
      { selector: ":selected", style: { "border-width": 3, "border-color": theme.accent, "line-color": theme.accent, "target-arrow-color": theme.accent } }
    ];
  }
  readTheme();
  var cy = cytoscape({
    container: document.getElementById("cy"),
    userZoomingEnabled: true,
    userPanningEnabled: true,
    minZoom: 0.1,
    maxZoom: 3,
    wheelSensitivity: 0.3,
    style: styleSheet()
  });
  var selected = null;
  var pathEnds = { source: null, target: null };
  var lastCypher = [];
  var legend = {};

  function $(id) { return document.getElementById(id); }
  function status(text, error) {
    var el = $("status");
    el.textContent = text || "";
    el.className = error ? "error" : "";
  }
  function hue(labels) {
    var key = (labels && labels[labels.length - 1]) || "";
    var h = 0;
    for (var i = 0; i < key.length; i++) { h = (h * 31 + key.charCodeAt(i)) % 360; }
    return h;
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
  function reply(r) {
    if (r.status === 401) { window.location.href = "/console/login"; throw new Error("session expired"); }
    return r.json().then(function (body) {
      if (!r.ok) { throw new Error(body.error || body.detail || "request failed"); }
      return body;
    });
  }
  function get(route, params) {
    return fetch(base + "/" + route + "?" + query(params), { credentials: "same-origin" }).then(reply);
  }
  function layout() {
    var name = $("layout").value;
    var options = { name: name, animate: false, fit: true, padding: 40, stop: function () { if (cy.zoom() > 1.5) { cy.zoom(1.5); cy.center(); } } };
    if (name === "cose") {
      options.nodeRepulsion = function () { return 40000; };
      options.idealEdgeLength = function () { return 120; };
      options.nodeOverlap = 30;
    }
    cy.layout(options).run();
  }
  function drawLegend() {
    var ul = $("legend");
    ul.textContent = "";
    Object.keys(legend).sort().forEach(function (label) {
      var li = document.createElement("li");
      var dot = document.createElement("i");
      dot.style.backgroundColor = fill(legend[label]);
      li.appendChild(dot);
      li.appendChild(document.createTextNode(label));
      ul.appendChild(li);
    });
    var edges = document.createElement("li");
    edges.textContent = "Dashed red border: rejected. Solid red border: open or reopened. Dotted border: candidate.";
    ul.appendChild(edges);
  }
  function add(resp) {
    var fresh = resp.elements.filter(function (e) { return cy.getElementById(e.data.id).empty(); });
    fresh.forEach(function (e) {
      if (e.group !== "nodes") { return; }
      e.data.hue = hue(e.data.labels);
      e.data.status = e.data.props.status;
      legend[e.data.labels[e.data.labels.length - 1]] = e.data.hue;
    });
    cy.add(fresh);
    applyFilters();
    if (fresh.length) { layout(); drawLegend(); }
    status(cy.nodes().length + " nodes, " + cy.edges().length + " edges" + (resp.truncated ? " (truncated at 500 nodes: filter to see more)" : ""));
  }
  function fail(e) { status(e.message, true); }

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
    $("properties").hidden = false;
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

  function startParams() {
    return { labels: checked("filter-label"), types: checked("filter-type"), artifact: $("artifact").value, state: $("state").value, q: $("search-text").value };
  }
  function remember() {
    var p = startParams();
    p.hops = $("depth").value;
    p.cypher = $("cypher").value;
    if (p.hops === "1") { p.hops = ""; }
    var qs = query(p);
    window.history.replaceState(null, "", window.location.pathname + (qs ? "?" + qs : ""));
  }
  function load() {
    remember();
    return get("start", startParams()).then(add).catch(fail);
  }

  cy.on("tap", "node", function (evt) {
    var node = evt.target;
    selected = node;
    show(node);
    get("expand", { id: node.data("nid"), hops: $("depth").value, labels: checked("filter-label"), types: checked("filter-type") })
      .then(add).catch(fail);
  });
  cy.on("tap", function (evt) { if (evt.target === cy) { selected = null; $("properties").hidden = true; } });
  cy.on("tap", "edge", function (evt) { selected = null; show(evt.target); });

  function setEnd(which) {
    if (!selected) { return; }
    pathEnds[which] = selected.data("nid");
    $("path-" + which).textContent = selected.data("label");
  }
  $("set-source").addEventListener("click", function () { setEnd("source"); });
  $("set-target").addEventListener("click", function () { setEnd("target"); });

  $("start").addEventListener("click", load);
  $("search-text").addEventListener("keydown", function (e) { if (e.key === "Enter") { load(); } });
  Array.prototype.forEach.call(document.querySelectorAll("input.filter-label, input.filter-type"), function (el) {
    el.addEventListener("change", function () { applyFilters(); remember(); });
  });
  $("layout").addEventListener("change", layout);
  $("find").addEventListener("input", function () {
    var text = $("find").value.trim().toLowerCase();
    cy.nodes().removeClass("found");
    if (!text) { return; }
    var hits = cy.nodes().filter(function (n) {
      return [n.data("label"), n.data("props").key, n.data("props").name, n.data("props").id].some(function (v) {
        return typeof v === "string" && v.toLowerCase().indexOf(text) >= 0;
      });
    });
    hits.addClass("found");
    status(hits.length + " matching nodes in the visible graph");
  });
  $("path").addEventListener("click", function () {
    if (pathEnds.source === null || pathEnds.target === null) { status("Choose a start and an end node first.", true); return; }
    get("path", { source: pathEnds.source, target: pathEnds.target, hops: $("path-hops").value }).then(function (resp) {
      add(resp);
      cy.elements(".path").removeClass("path");
      resp.elements.forEach(function (e) { cy.getElementById(e.data.id).addClass("path"); });
      if (!resp.found) { status("No path within the hop limit.", true); }
    }).catch(fail);
  });
  function zoom(factor) {
    cy.zoom({ level: Math.min(cy.maxZoom(), Math.max(cy.minZoom(), cy.zoom() * factor)), renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
  }
  $("zoom-in").addEventListener("click", function () { zoom(1.25); });
  $("zoom-out").addEventListener("click", function () { zoom(0.8); });
  $("zoom-fit").addEventListener("click", function () { cy.fit(undefined, 40); });
  $("zoom-selected").addEventListener("click", function () {
    var sel = cy.elements(":selected");
    if (sel.empty()) { status("Select a node or edge first.", true); return; }
    cy.fit(sel, 80);
    if (cy.zoom() > 1.5) { cy.zoom({ level: 1.5, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } }); }
  });
  $("zoom-reset").addEventListener("click", function () { cy.zoom(1); cy.center(); });
  $("toggle-controls").addEventListener("click", function () { $("controls").hidden = !$("controls").hidden; cy.resize(); });
  $("toggle-props").addEventListener("click", function () { $("properties").hidden = !$("properties").hidden; cy.resize(); });
  $("fullscreen").addEventListener("click", function () {
    if (document.fullscreenElement) { document.exitFullscreen(); } else if ($("workspace").requestFullscreen) { $("workspace").requestFullscreen(); }
  });
  document.addEventListener("fullscreenchange", function () { cy.resize(); cy.fit(undefined, 40); });
  window.addEventListener("resize", function () { cy.resize(); });
  scheme.addEventListener("change", function () { readTheme(); cy.style(styleSheet()); drawLegend(); });
  $("clear").addEventListener("click", function () { cy.elements().remove(); legend = {}; drawLegend(); status(""); });

  function download(href, name) {
    var a = document.createElement("a");
    a.href = href;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
  $("export-png").addEventListener("click", function () {
    download(cy.png({ full: true, bg: theme.surface, output: "base64uri" }), "subgraph.png");
  });
  $("export-json").addEventListener("click", function () {
    var blob = new Blob([JSON.stringify(cy.json().elements, null, 2)], { type: "application/json" });
    var url = URL.createObjectURL(blob);
    download(url, "subgraph.json");
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  });
  $("share").addEventListener("click", function () {
    remember();
    if (navigator.clipboard) {
      navigator.clipboard.writeText(window.location.href).then(function () { status("Link copied."); }, function () { status(window.location.href); });
    } else {
      status(window.location.href);
    }
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
    remember();
    var out = $("cypher-result");
    out.textContent = "";
    $("cypher-add").hidden = true;
    $("cypher-status").className = "";
    $("cypher-status").textContent = "Running";
    fetch(base + "/query", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ cypher: $("cypher").value })
    }).then(reply).then(function (resp) {
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

  (function restore() {
    var p = new URLSearchParams(window.location.search);
    if (!p.toString()) { return; }
    var labels = p.getAll("labels");
    var types = p.getAll("types");
    Array.prototype.forEach.call(document.querySelectorAll("input.filter-label"), function (el) { el.checked = labels.indexOf(el.value) >= 0; });
    Array.prototype.forEach.call(document.querySelectorAll("input.filter-type"), function (el) { el.checked = types.indexOf(el.value) >= 0; });
    $("artifact").value = p.get("artifact") || "";
    $("state").value = p.get("state") || "";
    $("search-text").value = p.get("q") || "";
    $("depth").value = p.get("hops") || "1";
    $("cypher").value = p.get("cypher") || "";
    if (labels.length || types.length || p.get("artifact") || p.get("state") || p.get("q")) { load(); }
  })();
  drawLegend();
})();

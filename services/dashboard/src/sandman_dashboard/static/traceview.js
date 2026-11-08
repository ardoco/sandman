(function () {
  "use strict";
  var root = document.getElementById("tv-app");
  var project = root.dataset.project;
  var COLUMNS = ["Requirements", "Documentation", "Architecture", "Code", "Tests"];
  var COLUMN_OF = { Requirement: 0, TextUnit: 1, Decision: 1, ModelElement: 2, CodeUnit: 3, TestCase: 4 };
  var RANK = { info: 1, warning: 2, error: 3 };
  var NODE_W = 240, GAP_X = 120, ROW_H = 64, INDENT = 24, BIG = 1000;
  var scheme = window.matchMedia("(prefers-color-scheme: dark)");
  var theme = {};
  var data = null, cy = null;

  function $(id) { return document.getElementById(id); }
  function readTheme() {
    var cs = getComputedStyle(document.documentElement);
    ["surface", "border", "text", "muted", "accent", "bad", "warn", "ok"].forEach(function (k) { theme[k] = cs.getPropertyValue("--" + k).trim(); });
    theme.dark = scheme.matches;
  }
  function status(text, error) {
    var el = $("tv-status");
    el.textContent = text || "";
    el.className = error ? "error" : "";
  }
  function get(path, params) {
    var q = new URLSearchParams(params).toString();
    return fetch(path + "?" + q, { credentials: "same-origin" }).then(function (r) {
      if (r.status === 401) { window.location.href = "/login"; throw new Error("session expired"); }
      return r.json().then(function (body) {
        if (!r.ok) { throw new Error(body.error || "request failed"); }
        return body;
      });
    });
  }
  function el(tag, text, cls) {
    var e = document.createElement(tag);
    if (text !== undefined && text !== null) { e.textContent = text; }
    if (cls) { e.className = cls; }
    return e;
  }

  function columnOf(label) { return COLUMN_OF.hasOwnProperty(label) ? COLUMN_OF[label] : -1; }
  function columnsLayout(elements) {
    var byId = {};
    elements.forEach(function (e) { byId[e.id] = e; });
    var next = COLUMNS.map(function () { return 0; });
    var out = {};
    elements.forEach(function (e) {
      var column = columnOf(e.label);
      if (column < 0) { return; }
      var depth = 0;
      for (var p = e.parentId ? byId[e.parentId] : null; p && depth < 20; p = p.parentId ? byId[p.parentId] : null) { depth++; }
      out[e.id] = { x: column * (NODE_W + GAP_X) + depth * INDENT, y: next[column]++ * ROW_H, column: column };
    });
    return out;
  }
  function filterLinks(links, f) {
    return links.filter(function (l) {
      return f.statuses.indexOf(l.status) >= 0 &&
        (f.approaches.length === 0 || f.approaches.indexOf(l.approach || "") >= 0) &&
        (l.confidence || 0) >= f.minConfidence;
    });
  }
  function isOpen(s) { return s === "OPEN" || s === "REOPENED"; }
  function worstSeverity(findings, id, openOnly) {
    var severity = null, open = 0;
    findings.forEach(function (f) {
      if (!f.involved.some(function (i) { return i.id === id; })) { return; }
      var o = isOpen(f.status);
      if (o) { open++; }
      if (openOnly && !o) { return; }
      var s = String(f.severity).toLowerCase();
      if (severity === null || (RANK[s] || 0) > (RANK[severity] || 0)) { severity = s; }
    });
    return { severity: severity, open: open };
  }
  function checked(sel) {
    return Array.prototype.filter.call(document.querySelectorAll(sel), function (e) { return e.checked; }).map(function (e) { return e.value; });
  }
  function sevColor(s) { return s === "error" ? theme.bad : s === "warning" ? theme.warn : theme.accent; }

  function styleSheet() {
    return [
      { selector: "node[kind = 'artifact']", style: { shape: "roundrectangle", "background-opacity": 0.08, "background-color": theme.muted, "border-width": 1, "border-color": theme.border, label: "data(label)", "text-valign": "top", "text-halign": "center", color: theme.muted, "font-size": 11, padding: 14 } },
      { selector: "node[kind = 'element']", style: { shape: "rectangle", width: NODE_W, height: 40, "background-color": theme.surface, "border-width": 2, "border-color": theme.border, label: "data(label)", color: theme.text, "font-size": 10, "text-valign": "center", "text-halign": "center", "text-wrap": "ellipsis", "text-max-width": NODE_W - 12 } },
      { selector: "node[inferred]", style: { "border-style": "dashed" } },
      { selector: "node[sev]", style: { "border-color": function (n) { return sevColor(n.data("sev")); }, "border-width": 3 } },
      { selector: "edge", style: { width: function (e) { return 1 + 3 * (e.data("confidence") || 0); }, "curve-style": "bezier", "target-arrow-shape": "triangle", "line-color": theme.muted, "target-arrow-color": theme.muted, "line-style": "solid" } },
      { selector: "edge[status = 'candidate']", style: { "line-style": "dashed" } },
      { selector: "edge[status = 'rejected']", style: { "line-style": "dotted" } },
      { selector: "edge[sev]", style: { width: 5, "line-color": theme.bad, "target-arrow-color": theme.bad } },
      { selector: "edge.show", style: { label: "data(approach)", "font-size": 8, color: theme.muted, "text-background-color": theme.surface, "text-background-opacity": 1 } },
      { selector: ".faded", style: { opacity: 0.15 } },
      { selector: ".hit", style: { "border-color": theme.ok, "border-width": 4 } },
      { selector: ":selected", style: { "border-color": theme.accent, "line-color": theme.accent, "target-arrow-color": theme.accent } }
    ];
  }

  function build() {
    var pos = columnsLayout(data.elements);
    var open = $("tv-open").checked;
    var nodes = [], artifacts = {};
    data.elements.forEach(function (e) {
      if (!pos[e.id]) { return; }
      if (e.artifactId && !artifacts[e.artifactId]) {
        artifacts[e.artifactId] = true;
        nodes.push({ group: "nodes", data: { id: "art:" + e.artifactId, kind: "artifact", label: e.artifactPath || e.artifactId } });
      }
      var w = worstSeverity(data.findings, e.id, open);
      var text = e.name || e.excerpt || e.key;
      nodes.push({
        group: "nodes",
        data: { id: e.id, kind: "element", column: pos[e.id].column, parent: e.artifactId ? "art:" + e.artifactId : undefined, label: (w.open ? "[" + w.open + "] " : "") + text, text: text, inferred: e.inferred ? true : undefined, sev: w.severity || undefined, elabel: e.label },
        position: { x: pos[e.id].x, y: pos[e.id].y }
      });
    });
    var edges = data.links.map(function (l) {
      var w = worstSeverity(data.findings, l.id, open);
      return { group: "edges", data: { id: l.id, source: l.sourceId, target: l.targetId, status: l.status, approach: l.approach || "", confidence: l.confidence || 0, sev: w.severity || undefined } };
    });
    var ids = {};
    nodes.forEach(function (n) { ids[n.data.id] = true; });
    edges = edges.filter(function (e) { return ids[e.data.source] && ids[e.data.target]; });
    readTheme();
    if (cy) { cy.destroy(); }
    cy = cytoscape({ container: $("tv-cy"), elements: nodes.concat(edges), layout: { name: "preset" }, style: styleSheet(), userZoomingEnabled: true, userPanningEnabled: true, minZoom: 0.05, maxZoom: 3, wheelSensitivity: 0.3 });
    cy.on("tap", "node[kind = 'element']", function (ev) { select(ev.target.id()); });
    cy.on("tap", "edge", function (ev) { selectEdge(ev.target.id()); });
    cy.on("tap", function (ev) { if (ev.target === cy) { clearSelection(); } });
    cy.fit(undefined, 30);
  }

  function applyFilters() {
    if (!cy) { return; }
    var f = { statuses: checked(".tv-status"), approaches: checked(".tv-approach"), minConfidence: parseFloat($("tv-conf").value) || 0 };
    var shownCols = checked(".tv-col").map(Number);
    var visible = {};
    filterLinks(data.links, f).forEach(function (l) { visible[l.id] = true; });
    var touched = {};
    cy.edges().forEach(function (e) {
      var ok = !!visible[e.id()] && shownCols.indexOf(e.source().data("column")) >= 0 && shownCols.indexOf(e.target().data("column")) >= 0;
      e.style("display", ok ? "element" : "none");
      if (ok) { touched[e.source().id()] = true; touched[e.target().id()] = true; }
    });
    var onlyLinked = $("tv-linked").checked;
    cy.nodes("[kind = 'element']").forEach(function (n) {
      var ok = shownCols.indexOf(n.data("column")) >= 0 && (!onlyLinked || touched[n.id()] || n.data("sev"));
      n.style("display", ok ? "element" : "none");
    });
    cy.nodes("[kind = 'artifact']").forEach(function (a) {
      a.style("display", a.children(":visible").length ? "element" : "none");
    });
    var q = $("tv-search").value.trim().toLowerCase();
    cy.nodes().removeClass("hit");
    if (q) {
      var hits = cy.nodes("[kind = 'element']").filter(function (n) { return n.data("text").toLowerCase().indexOf(q) >= 0 && n.visible(); });
      hits.addClass("hit");
      if (hits.length) { cy.fit(hits, 40); }
    }
  }

  function clearSelection() {
    cy.elements().removeClass("faded show");
    cy.elements().unselect();
    $("tv-detail").hidden = true;
  }
  function focus(node) {
    cy.elements().addClass("faded");
    var hood = node.closedNeighborhood();
    hood.removeClass("faded");
    hood.edges().addClass("show");
    node.parents().removeClass("faded");
    hood.nodes().parents().removeClass("faded");
  }
  function line(parent, label, value) {
    if (value === undefined || value === null || value === "") { return; }
    parent.appendChild(el("dt", label));
    parent.appendChild(el("dd", String(value)));
  }
  function section(panel, title) { panel.appendChild(el("h3", title)); }
  function pick(id, text) {
    var b = el("button", text, "pick");
    b.type = "button";
    b.addEventListener("click", function () { var n = cy.getElementById(id); if (n.nonempty()) { cy.animate({ center: { eles: n }, duration: 150 }); } select(id); });
    return b;
  }
  function show(build) {
    var panel = $("tv-detail");
    panel.replaceChildren();
    build(panel);
    panel.hidden = false;
  }
  function linkItems(parent, links, self) {
    var ul = el("ul");
    links.forEach(function (l) {
      var li = el("li");
      var other = l.sourceId === self ? l.targetId : l.sourceId;
      li.appendChild(el("span", (l.sourceId === self ? "to " : "from ")));
      li.appendChild(pick(other, nameOf(other)));
      li.appendChild(el("span", " " + [l.approach, l.status, l.confidence !== null && l.confidence !== undefined ? "conf " + l.confidence : null, l.reviewedBy ? "by " + l.reviewedBy : null].filter(Boolean).join(", ")));
      if (l.rationale) { li.appendChild(el("pre", l.rationale)); }
      if (l.reviewReason) { li.appendChild(el("div", "review: " + l.reviewReason, "muted")); }
      ul.appendChild(li);
    });
    parent.appendChild(ul);
  }
  function nameOf(id) {
    var n = cy.getElementById(id);
    return n.nonempty() ? n.data("text") : id;
  }
  function select(id) {
    var node = cy.getElementById(id);
    if (node.empty()) { return; }
    cy.elements().unselect();
    node.select();
    focus(node);
    get("/api/traceview/element", { project: project, id: id }).then(function (d) {
      show(function (panel) {
        var e = d.element;
        panel.appendChild(el("h2", e.name || e.key));
        var dl = el("dl", null, "kv");
        line(dl, "Label", e.label);
        line(dl, "Kind", e.kind);
        line(dl, "Key", e.key);
        line(dl, "Id", e.id);
        line(dl, "Path", e.artifactPath ? e.artifactPath + (e.startLine ? ":" + e.startLine : "") : null);
        line(dl, "Sentence", e.sentenceIndex);
        panel.appendChild(dl);
        if (e.text) { panel.appendChild(el("pre", e.text)); }
        section(panel, "Links (" + d.links.length + ")");
        linkItems(panel, d.links, e.id);
        section(panel, "Findings (" + d.findings.length + ")");
        var fl = el("ul");
        d.findings.forEach(function (f) {
          var li = el("li", f.severity + " " + f.status + " " + f.title + (f.role ? " (" + f.role + ")" : ""));
          if (f.description) { li.appendChild(el("pre", f.description)); }
          if (f.evidence) { li.appendChild(el("pre", typeof f.evidence === "string" ? f.evidence : JSON.stringify(f.evidence, null, 2))); }
          fl.appendChild(li);
        });
        panel.appendChild(fl);
        section(panel, "Neighbours (" + d.neighbours.length + ")");
        var nl = el("ul");
        d.neighbours.forEach(function (n) {
          var li = el("li", n.relation + " " + n.direction + " ");
          li.appendChild(pick(n.id, n.name || n.id));
          nl.appendChild(li);
        });
        panel.appendChild(nl);
      });
    }).catch(function (e) { status(e.message, true); });
  }
  function selectEdge(id) {
    var edge = cy.getElementById(id);
    var l = data.links.filter(function (x) { return x.id === id; })[0];
    cy.elements().unselect();
    edge.select();
    cy.elements().addClass("faded");
    edge.removeClass("faded").addClass("show");
    edge.connectedNodes().removeClass("faded");
    edge.connectedNodes().parents().removeClass("faded");
    show(function (panel) {
      panel.appendChild(el("h2", "Trace link"));
      var dl = el("dl", null, "kv");
      line(dl, "Id", l.id);
      line(dl, "Source", nameOf(l.sourceId));
      line(dl, "Target", nameOf(l.targetId));
      line(dl, "Type", l.linkType);
      line(dl, "Approach", l.approach);
      line(dl, "Confidence", l.confidence);
      line(dl, "Status", l.status);
      line(dl, "Reviewed by", l.reviewedBy);
      panel.appendChild(dl);
    });
  }

  function setup() {
    var colBox = $("tv-columns");
    COLUMNS.forEach(function (c, i) {
      var label = el("label", null, "chip");
      var cb = el("input");
      cb.type = "checkbox"; cb.className = "tv-col"; cb.value = String(i); cb.checked = true;
      cb.addEventListener("change", applyFilters);
      label.appendChild(cb);
      label.appendChild(document.createTextNode(" " + c));
      colBox.appendChild(label);
    });
    var apBox = $("tv-approaches");
    var seen = {};
    data.links.forEach(function (l) { seen[l.approach || ""] = true; });
    Object.keys(seen).sort().forEach(function (a) {
      var label = el("label", null, "chip");
      var cb = el("input");
      cb.type = "checkbox"; cb.className = "tv-approach"; cb.value = a; cb.checked = false;
      cb.addEventListener("change", applyFilters);
      label.appendChild(cb);
      label.appendChild(document.createTextNode(" " + (a || "none")));
      apBox.appendChild(label);
    });
    $("tv-linked").checked = data.elements.length > BIG;
    $("tv-filters").hidden = false;
    Array.prototype.forEach.call(document.querySelectorAll(".tv-status, #tv-linked"), function (e) { e.addEventListener("change", applyFilters); });
    $("tv-conf").addEventListener("input", applyFilters);
    $("tv-open").addEventListener("change", function () { build(); applyFilters(); });
    $("tv-search").addEventListener("input", applyFilters);
    $("tv-fit").addEventListener("click", function () { cy.fit(cy.elements(":visible"), 30); });
  }

  if (!project) { return; }
  status("Loading ...");
  get("/api/traceview", { project: project }).then(function (d) {
    data = d;
    setup();
    build();
    applyFilters();
    status(d.elements.length + " elements, " + d.links.length + " links, " + d.findings.length + " findings" + (d.truncated ? " (truncated)" : ""));
  }).catch(function (e) { status(e.message, true); });
})();

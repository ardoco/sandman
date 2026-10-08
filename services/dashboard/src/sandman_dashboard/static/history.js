(function () {
  "use strict";
  var root = document.getElementById("history");
  var range = root.dataset.range;
  var PALETTE = ["#0969da", "#bf3989", "#1a7f37", "#9a6700", "#8250df", "#cf222e", "#0a7ea4", "#57606a"];
  var CHARTS = [
    { id: "chart-health", title: "Health (0 down, 1 degraded, 2 up)", series: ["health"], step: true },
    { id: "chart-tasks", title: "Active tasks", series: ["active_tasks"] },
    { id: "chart-requests", title: "LLM requests per interval", series: ["llm_requests"] },
    { id: "chart-tokens", title: "LLM tokens per interval (in + out)", series: ["llm_tokens_in", "llm_tokens_out"], sum: true },
    { id: "chart-cost", title: "LLM cost per interval (USD)", series: ["llm_cost"] },
    { id: "chart-errors", title: "LLM errors per interval", series: ["llm_errors"] },
    { id: "chart-memgraph", title: "Memgraph memory (bytes)", series: ["memgraph_memory"] }
  ];
  var plots = [];

  function merge(chart, data) {
    var keys = {};
    chart.series.forEach(function (s) {
      Object.keys(data.series[s] || {}).forEach(function (k) { keys[k] = true; });
    });
    var names = Object.keys(keys).sort();
    var times = {};
    names.forEach(function (k) {
      chart.series.forEach(function (s) {
        ((data.series[s] || {})[k] || []).forEach(function (p) { times[p[0]] = true; });
      });
    });
    var xs = Object.keys(times).map(Number).sort(function (a, b) { return a - b; });
    var index = {};
    xs.forEach(function (t, i) { index[t] = i; });
    var columns = names.map(function (k) {
      var col = xs.map(function () { return null; });
      chart.series.forEach(function (s) {
        ((data.series[s] || {})[k] || []).forEach(function (p) {
          var i = index[p[0]];
          col[i] = chart.sum ? (col[i] || 0) + p[1] : p[1];
        });
      });
      return col;
    });
    return { names: names, data: [xs].concat(columns) };
  }

  function draw(chart, data) {
    var holder = document.getElementById(chart.id);
    holder.textContent = "";
    var merged = merge(chart, data);
    if (merged.names.length === 0) {
      holder.textContent = "No samples in this range yet.";
      return;
    }
    var width = Math.max(holder.clientWidth, 320);
    var opts = {
      width: width,
      height: 220,
      title: chart.title,
      scales: { x: { time: true, range: [data.from, data.to] } },
      series: [{}].concat(merged.names.map(function (name, i) {
        var s = { label: name, stroke: PALETTE[i % PALETTE.length], width: 1.5, spanGaps: true };
        if (chart.step) { s.paths = uPlot.paths.stepped({ align: 1 }); }
        return s;
      }))
    };
    plots.push(new uPlot(opts, merged.data, holder));
  }

  fetch("/api/history?range=" + encodeURIComponent(range), { credentials: "same-origin" }).then(function (r) {
    if (r.status === 401) { window.location.href = "/login"; throw new Error("session expired"); }
    if (!r.ok) { throw new Error("request failed"); }
    return r.json();
  }).then(function (data) {
    CHARTS.forEach(function (c) { draw(c, data); });
  }).catch(function (e) {
    document.getElementById("history-status").textContent = e.message;
  });
})();

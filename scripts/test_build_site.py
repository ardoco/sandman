"""The generated site stays in sync with compose, services/ and libs/, and links resolve."""

import functools
import glob
import http.server
import json
import shutil
import subprocess
import threading
from pathlib import Path

import build_site
import pytest
import yaml
from build_site import ROOT


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    out = tmp_path_factory.mktemp("site")
    build_site.main(out)
    assert (out / "index.html").exists()
    return json.loads((out / "components.json").read_text())


def test_all_sources_appear(data):
    ids = {c["id"] for c in data["components"]}
    compose = yaml.safe_load((ROOT / "deploy/docker-compose.yml").read_text())["services"]
    services = {d.name for d in (ROOT / "services").iterdir() if d.is_dir()}
    libs = {d.name for p in ("java", "python") for d in (ROOT / "libs" / p).iterdir() if d.is_dir()}
    assert (set(compose) | services | libs) <= ids


def test_edges_and_links_resolve(data):
    ids = {c["id"] for c in data["components"]}
    assert all(e["from"] in ids and e["to"] in ids for e in data["edges"])
    for c in data["components"]:
        assert c["group"] in data["groups"] and c["description"]
        for link in c["links"]:
            assert (ROOT / link["path"]).exists(), link
            assert link["url"].startswith(f"{data['repo']}/")


def test_anchor_slugs():
    assert build_site.slugs(["A b: `c`", "Dup", "Dup"]) == ["a-b-c", "dup", "dup-1"]


def meta():
    return yaml.safe_load(build_site.META.read_text())


def test_descriptions_in_sync_with_repo(data):
    ids = {c["id"] for c in data["components"]}
    assert build_site.sync_problems(ids, meta()) == []


def test_sync_guard_detects_new_and_stale_components(data):
    ids = {c["id"] for c in data["components"]}
    problems = build_site.sync_problems(ids | {"brand-new"}, meta())
    assert problems == ["no entry in components.yaml: brand-new"]
    problems = build_site.sync_problems(ids - {"nats"}, meta())
    assert "components.yaml entry without component: nats" in problems


def test_glossary_and_intro_present(data):
    assert data["intro"]["paragraphs"] and len(data["glossary"]) >= 10
    assert {g["term"] for g in data["glossary"]} >= {"Knowledge graph", "Trace link", "Inconsistency", "Gateway"}
    assert all(c["summary"] and c["details"] and c["flow"] for c in data["components"])


def test_milestone_text_only_via_planned_field(data):
    ms = build_site.milestones()
    ids = {c["id"] for c in data["components"]}
    assert {m["id"] for m in ms} >= {"M0", "M1", "M2", "M3", "M4"}
    assert build_site.sync_problems(ids, meta(), ms) == []
    bad = meta()
    bad["components"]["nats"]["details"] += " Planned for M1."
    assert any("nats.details" in p for p in build_site.sync_problems(ids, bad, ms))
    bad = meta()
    bad["components"]["nats"]["planned"] = "M99"
    assert any("not in the Stage 3 table" in p for p in build_site.sync_problems(ids, bad, ms))


def test_build_does_not_use_git_tags(monkeypatch, tmp_path):
    real = build_site.subprocess.run

    def run(cmd, *a, **k):
        assert "tag" not in cmd, cmd
        return real(cmd, *a, **k)

    monkeypatch.setattr(build_site.subprocess, "run", run)
    build_site.main(tmp_path)
    out = json.loads((tmp_path / "components.json").read_text())
    assert out["status"].startswith("Status: ") and "M4" not in out["status"]
    assert all("done" not in m for m in out["milestones"])


def comp(data, cid):
    return next(c for c in data["components"] if c["id"] == cid)


def test_every_component_has_internals(data):
    for c in data["components"]:
        i = c["internals"]
        assert set(i["files"]) == {"source", "tests", "config", "docs"}
        assert {"image", "profiles", "networks"} <= set(i["compose"])
        for entries in i["files"].values():
            assert len(entries) <= build_site.MAX_FILES
            assert entries == sorted(entries, key=lambda e: e["path"])
            for e in entries:
                assert (ROOT / e["path"]).exists() and e["url"].startswith(f"{data['repo']}/")


def test_infra_image_internals_are_empty_but_have_compose(data):
    i = comp(data, "nats")["internals"]
    assert i["files"]["source"] == i["files"]["tests"] == i["features"] == []
    assert i["compose"]["image"]


def test_extraction_agent_internals(data):
    i = comp(data, "extraction-agent")["internals"]
    assert i["files"]["source"] and i["files"]["tests"]
    assert i["testCount"] > 0 and i["tests"] and all(t["count"] >= 0 for t in i["tests"])
    kinds = {f["kind"] for f in i["features"]}
    assert {"operation", "skill", "module"} <= kinds
    assert any(f["name"].startswith("POST /") for f in i["features"])


def test_java_library_internals_count_tests(data):
    i = comp(data, "agent-lib")["internals"]
    assert i["files"]["source"] and i["testCount"] > 0


def test_zone_grid_is_valid_and_every_zone_has_cards(data):
    zones = data["zones"]
    cells = {(z["col"], z["row"]) for z in zones}
    assert len(cells) == len(zones) and all(0 <= z["col"] < 4 and z["row"] >= 0 for z in zones)
    assert all(z.get("span", 1) <= 4 for z in zones)
    used = {c["zone"] for c in data["components"]}
    assert used == {z["id"] for z in zones}


def test_every_flow_and_edge_endpoint_has_a_details_entry(data):
    ids = {c["id"] for c in data["components"]}
    detailed = {c["id"] for c in data["components"] if c["summary"] and c["details"] and c["flow"]}
    assert ids == detailed
    assert {i for s in data["flow"] for i in s["components"]} <= ids
    assert {e[k] for e in data["edges"] for k in ("from", "to")} <= ids


def test_status_line_stays_short(data):
    assert "(" not in data["status"] and len(data["status"]) < 400


ANCHOR_CHECK = """<iframe id=f src="index.html#" style="width:1600px;height:1200px"></iframe><pre id=r>pending</pre><script>
f.onload = () => setTimeout(() => {
  const d = f.contentDocument, bad = [], eps = .01, nodes = {};
  d.querySelectorAll(".node").forEach(n => { const m = /translate\\(([-\\d.]+) ([-\\d.]+)\\)/.exec(n.getAttribute("transform")), r = n.querySelector("rect.card"); nodes[n.dataset.id] = { x: +m[1], y: +m[2], w: +r.getAttribute("width"), h: +r.getAttribute("height") }; });
  const on = (b, p, side) => Math.abs(p.x - (side > 0 ? b.x + b.w : b.x)) < eps && p.y >= b.y && p.y <= b.y + b.h;
  const ok = (b, p) => on(b, p, 1) || on(b, p, -1);
  const paths = [...d.querySelectorAll("path.edge")];
  paths.forEach(p => { const L = p.getTotalLength(), a = p.getPointAtLength(0), z = p.getPointAtLength(L); if (!ok(nodes[p.dataset.from], a) || !ok(nodes[p.dataset.to], z)) bad.push(p.dataset.from + ">" + p.dataset.to); });
  r.textContent = JSON.stringify({ n: paths.length, bad, lanes: [f.contentWindow.routes.initial, f.contentWindow.routes.crossings] });
}, 1500);
</script>"""  # noqa: E501


def chromium():
    env = shutil.which("chromium") or shutil.which("chrome-headless-shell")
    found = glob.glob(str(Path.home() / "*/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell"))
    found += glob.glob(
        str(Path.home() / "Library/Caches/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell")
    )
    return env or (sorted(found)[-1] if found else None)


def test_edges_start_and_end_on_box_edges_in_browser(data, tmp_path):
    exe = chromium()
    if not exe:
        pytest.skip("headless Chromium not available")
    build_site.main(tmp_path)
    (tmp_path / "check.html").write_text(ANCHOR_CHECK)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        out = subprocess.run(
            [
                exe,
                "--no-sandbox",
                "--disable-gpu",
                "--virtual-time-budget=6000",
                "--dump-dom",
                f"http://127.0.0.1:{srv.server_port}/check.html",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
    finally:
        srv.shutdown()
    result = json.loads(out.split('<pre id="r">')[1].split("</pre>")[0])
    assert result["n"] == len(data["edges"]) and result["bad"] == []
    assert result["lanes"][1] < result["lanes"][0]

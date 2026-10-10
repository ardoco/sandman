"""The generated site stays in sync with compose, services/ and libs/, and links resolve."""

import json

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
    assert {m["id"] for m in ms} >= {"M0", "M1", "M2", "M3", "M4"}
    assert build_site.sync_problems({c["id"] for c in data["components"]}, meta(), ms) == []
    bad = meta()
    bad["components"]["nats"]["details"] += " Planned for M1."
    assert any("nats.details" in p for p in build_site.sync_problems({c["id"] for c in data["components"]}, bad, ms))
    done = [{**m, "done": True} for m in ms]
    assert any("already done" in p for p in build_site.sync_problems({c["id"] for c in data["components"]}, meta(), done))
    assert "Status:" in data["status"]

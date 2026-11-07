"""Pure unit tests of the rule functions, config loading and KG lookups (no orchestrator, no Docker)."""

from pathlib import Path
from typing import Any

import pytest
from fakes import FakeGraph as BaseGraph
from fakes import aid, event, ingestion, vid
from sandman_agent import stable_id
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_agent.contracts import Contracts
from sandman_orchestrator.rules import (
    DERIVATION_ACTOR,
    DERIVE,
    DETECT,
    EXTRACT,
    PRUNE_DATA,
    PRUNE_LINKS,
    TLR,
    KgGraph,
    KindPair,
    ModelDerivation,
    Retention,
    Rules,
    Version,
    derive,
    detect,
    ids,
    manual,
    merge,
    plan,
    protected_hashes,
    seconds,
    tlr_steps,
)


class FakeGraph(BaseGraph):
    async def versions(self, project_id: str, version_ids: Any) -> list[Version]:
        assert project_id in ("mini", "pp")
        return await super().versions(project_id, version_ids)

    async def current(self, project_id: str) -> list[Version]:
        assert project_id in ("mini", "pp")
        return await super().current(project_id)


PAIR = KindPair("requirements", "code", ("stub",))
RULES = Rules(kind_pairs=(PAIR,))
HASH = "ab" * 32


def spec_tuple(s: Any) -> tuple[str, dict[str, Any], bool]:
    return s.skill, s.input, s.mergeable


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("30", 30.0), ("1.5m", 90.0), (" 2 h ", 7200.0), ("0s", 0.0), (2.5, 2.5), (7, 7.0)],
)
def test_seconds_values(raw: Any, expected: float) -> None:
    assert seconds(raw) == expected
    assert isinstance(seconds(raw), float)


@pytest.mark.parametrize("raw", ["", "abc", "5x", "-3s", "1d", "s", "1 2s"])
def test_seconds_rejects_invalid(raw: str) -> None:
    with pytest.raises(ValueError, match="invalid duration"):
        seconds(raw)


def test_ids_sorts_and_deduplicates() -> None:
    assert ids(["b", "a", "b"]) == ["a", "b"]
    assert ids(x for x in "ba") == ["a", "b"]


def test_defaults() -> None:
    r = Rules()
    assert (r.debounce, r.max_per_agent, r.poll_initial, r.poll_max, r.max_attempts) == (10.0, 2, 30.0, 120.0, 3)
    assert r.backoff == (30.0, 120.0, 600.0)
    assert r.timeouts == {
        EXTRACT: 900.0,
        TLR: 3600.0,
        DETECT: 1800.0,
        PRUNE_LINKS: 1800.0,
        PRUNE_DATA: 1800.0,
        DERIVE: 1800.0,
    }
    assert r.kind_pairs == ()
    assert r.retention == Retention(True, 86400.0, 20, True, Path("/experiments"), {})
    assert r.model_derivation.enabled is True
    assert (r.planner.enabled, r.planner.max_steps, r.planner.approval_ttl) == (False, 5, 86400.0)


def load(tmp_path: Path, text: str) -> Rules:
    f = tmp_path / "rules.yaml"
    f.write_text(text, encoding="utf-8")
    return Rules.load(f, Contracts(DEFAULT_CONTRACTS_DIR))


def test_load_empty_gives_defaults(tmp_path: Path) -> None:
    assert load(tmp_path, "{}\n") == Rules()


def test_load_overrides(tmp_path: Path) -> None:
    r = load(
        tmp_path,
        """
debounceSeconds: 3
maxConcurrentPerAgent: 4
timeouts: {extract-artifact-versions: 2m}
poll: {initial: 5s, max: 1m}
retry: {maxAttempts: 7, backoff: [1s, 2s]}
tlr:
  kindPairs:
    - {source: requirements, target: code, approaches: [a, b]}
    - {source: architecture-doc, target: code}
retention:
  enabled: false
  interval: 2h
  keepVersionsPerArtifact: 9
  dryRun: false
  experimentsDir: /x
  projects: {proj-a: {enabled: true}}
modelDerivation: {enabled: false, projects: {proj-a: {enabled: true}}}
planner: {enabled: true, maxSteps: 3, approvalTtl: 1h}
""",
    )
    assert (r.debounce, r.max_per_agent, r.poll_initial, r.poll_max, r.max_attempts) == (3.0, 4, 5.0, 60.0, 7)
    assert r.timeouts[EXTRACT] == 120.0 and r.timeouts[TLR] == 3600.0
    assert r.backoff == (1.0, 2.0)
    assert r.kind_pairs == (
        KindPair("requirements", "code", ("a", "b")),
        KindPair("architecture-doc", "code", ()),
    )
    assert r.retention == Retention(False, 7200.0, 9, False, Path("/x"), {"proj-a": {"enabled": True}})
    assert r.model_derivation == ModelDerivation(False, {"proj-a": {"enabled": True}})
    assert (r.planner.enabled, r.planner.max_steps, r.planner.approval_ttl) == (True, 3, 3600.0)


def test_retention_project_overrides() -> None:
    ret = Retention(
        enabled=True,
        keep=20,
        dry_run=True,
        projects={"pp": {"enabled": False, "keepVersionsPerArtifact": 3, "dryRun": False}},
    )
    assert ret.project("pp") == (False, 3, False)
    assert ret.project("other") == (True, 20, True)
    assert Retention(enabled=False, keep=4, dry_run=False).project("x") == (False, 4, False)


def test_model_derivation_project() -> None:
    md = ModelDerivation(enabled=True, projects={"pp": {"enabled": False}})
    assert md.project("pp") is False
    assert md.project("other") is True
    assert ModelDerivation(enabled=False, projects={"pp": {"enabled": True}}).project("pp") is True
    assert ModelDerivation(enabled=False).project("pp") is False
    assert ModelDerivation(enabled=True, projects={"pp": {}}).project("pp") is True


def test_detect_scopes_and_whole_project() -> None:
    s = detect("mini", [aid(2), aid(1), aid(2)])
    assert spec_tuple(s) == (DETECT, {"projectId": "mini", "scopeArtifactIds": [aid(1), aid(2)]}, True)
    knowledge = stable_id("art", "mini", "sandman-knowledge.yaml")
    assert spec_tuple(detect("mini", [aid(1), knowledge])) == (DETECT, {"projectId": "mini"}, True)
    other = stable_id("art", "other", "sandman-knowledge.yaml")
    assert "scopeArtifactIds" in detect("mini", [other]).input


def test_derive_spec() -> None:
    assert spec_tuple(derive("mini")) == (DERIVE, {"projectId": "mini"}, True)


def test_tlr_steps_both_directions() -> None:
    cur = [Version(vid(1), aid(1), "requirements"), Version(vid(3), aid(3), "code"), Version(vid(4), aid(4), "code")]
    [s] = tlr_steps("mini", [cur[0]], cur, RULES)
    assert spec_tuple(s) == (
        TLR,
        {
            "projectId": "mini",
            "sourceVersionIds": [vid(1)],
            "targetVersionIds": [vid(3), vid(4)],
            "approaches": ["stub"],
        },
        True,
    )
    [s] = tlr_steps("mini", [cur[1]], cur, RULES)
    assert s.input["sourceVersionIds"] == [vid(1)]
    assert s.input["targetVersionIds"] == [vid(3)]
    both = tlr_steps("mini", cur, cur, RULES)
    assert len(both) == 2
    no_approaches = tlr_steps("mini", [cur[0]], cur, Rules(kind_pairs=(KindPair("requirements", "code"),)))
    assert "approaches" not in no_approaches[0].input
    assert tlr_steps("mini", [cur[0]], [cur[0]], RULES) == []
    assert tlr_steps("mini", [], cur, RULES) == []


def run_plan(ev: Any, graph: FakeGraph | None = None, rules: Rules = RULES) -> Any:
    import asyncio

    return asyncio.run(plan(ev, graph or FakeGraph(), rules))


def extraction(*versions: int) -> Any:
    return event(
        "sandman.extraction.completed", {"projectId": "mini", "artifactVersionIds": [vid(n) for n in versions]}
    )


def test_plan_ingestion_created_and_reactivated() -> None:
    [s] = run_plan(ingestion(created=[vid(2), vid(1)], reactivated=[vid(2)]))
    assert spec_tuple(s) == (EXTRACT, {"projectId": "mini", "artifactVersionIds": [vid(1), vid(2)]}, False)
    assert run_plan(ingestion()) == []


def test_plan_ingestion_deleted_detects_and_derives() -> None:
    specs = run_plan(ingestion(deleted=[aid(1)]))
    assert [s.skill for s in specs] == [DETECT, DERIVE]
    assert specs[0].input["scopeArtifactIds"] == [aid(1)]


def test_plan_ingestion_deleted_derive_suppressed() -> None:
    assert [s.skill for s in run_plan(ingestion(deleted=[aid(1)], actor=DERIVATION_ACTOR))] == [DETECT]
    off = Rules(model_derivation=ModelDerivation(False))
    assert [s.skill for s in run_plan(ingestion(deleted=[aid(1)]), rules=off)] == [DETECT]
    per_project = Rules(model_derivation=ModelDerivation(True, {"mini": {"enabled": False}}))
    assert [s.skill for s in run_plan(ingestion(deleted=[aid(1)]), rules=per_project)] == [DETECT]


def test_plan_mixed_ingestion_orders_extract_first() -> None:
    assert [s.skill for s in run_plan(ingestion(created=[vid(1)], deleted=[aid(9)]))] == [EXTRACT, DETECT, DERIVE]


def test_plan_extraction_links_and_detects_unlinked() -> None:
    specs = run_plan(extraction(1, 5))
    assert [s.skill for s in specs] == [TLR, DETECT]
    assert specs[1].input == {"projectId": "mini", "scopeArtifactIds": [aid(5)]}
    assert specs[0].input["sourceVersionIds"] == [vid(1)]


def test_plan_extraction_without_kind_pairs_detects_changed() -> None:
    [s] = run_plan(extraction(1, 2), rules=Rules())
    assert spec_tuple(s) == (DETECT, {"projectId": "mini", "scopeArtifactIds": [aid(1), aid(2)]}, True)


def test_plan_extraction_all_linked_has_no_detect() -> None:
    assert [s.skill for s in run_plan(extraction(1, 3))] == [TLR, TLR]


def test_plan_extraction_derives_from_user_architecture_input() -> None:
    graph = FakeGraph(
        known=[Version(vid(1), aid(1), "architecture-doc"), Version(vid(2), aid(2), "architecture-model", "derived")]
    )
    assert [s.skill for s in run_plan(extraction(1), graph, Rules())] == [DETECT, DERIVE]
    assert [s.skill for s in run_plan(extraction(2), graph, Rules())] == [DETECT]
    off = Rules(model_derivation=ModelDerivation(False))
    assert [s.skill for s in run_plan(extraction(1), graph, off)] == [DETECT]
    model = FakeGraph(known=[Version(vid(1), aid(1), "architecture-model")])
    assert DERIVE in [s.skill for s in run_plan(extraction(1), model, Rules())]
    other = FakeGraph(known=[Version(vid(1), aid(1), "code")])
    assert DERIVE not in [s.skill for s in run_plan(extraction(1), other, Rules())]


@pytest.mark.parametrize("type_", ["sandman.tracelinks.updated", "sandman.tracelink.reviewed"])
def test_plan_tracelink_events_detect_touched_artifacts(type_: str) -> None:
    ev = event(type_, {"projectId": "mini", "sourceVersionIds": [vid(1)], "targetVersionIds": [vid(3)]})
    [s] = run_plan(ev)
    assert spec_tuple(s) == (DETECT, {"projectId": "mini", "scopeArtifactIds": [aid(1), aid(3)]}, True)
    gone = event(type_, {"projectId": "mini", "sourceVersionIds": ["x"], "targetVersionIds": []})
    assert run_plan(gone) == []


def test_plan_retention_pruned_chains_data_prune() -> None:
    d = {
        "projectId": "mini",
        "scope": "trace-links",
        "keepVersionsPerArtifact": 4,
        "dryRun": False,
        "protectedContentHashes": [HASH],
        "x": 1,
    }
    [s] = run_plan(event("sandman.retention.pruned", d))
    assert spec_tuple(s) == (
        PRUNE_DATA,
        {"projectId": "mini", "keepVersionsPerArtifact": 4, "dryRun": False, "protectedContentHashes": [HASH]},
        False,
    )
    [s] = run_plan(event("sandman.retention.pruned", {"projectId": "mini", "scope": "trace-links"}))
    assert s.input == {"projectId": "mini"}
    assert run_plan(event("sandman.retention.pruned", {"projectId": "mini", "scope": "artifact-data"})) == []


def test_plan_ignores_other_events() -> None:
    assert run_plan(event("sandman.status.changed", {"projectId": "mini"})) == []


def test_merge() -> None:
    a = {"projectId": "p", "artifactVersionIds": ["b"], "x": 1}
    assert merge(a, {"projectId": "p", "artifactVersionIds": ["a", "b"], "x": 1}) == {
        "projectId": "p",
        "artifactVersionIds": ["a", "b"],
        "x": 1,
    }
    assert merge(a, {"projectId": "p", "artifactVersionIds": ["a"], "x": 2}) is None
    assert merge(a, {"projectId": "p", "artifactVersionIds": ["a"]}) is None
    assert merge({"projectId": "p"}, {"projectId": "p", "scopeArtifactIds": ["a"]}) == {"projectId": "p"}
    assert merge({"projectId": "p", "scopeArtifactIds": ["a"]}, {"projectId": "p"}) == {"projectId": "p"}
    assert merge({"projectId": "p", "sourceVersionIds": ["a"], "targetVersionIds": ["t"]},
                 {"projectId": "p", "sourceVersionIds": ["b"], "targetVersionIds": ["u"]}) == {
        "projectId": "p", "sourceVersionIds": ["a", "b"], "targetVersionIds": ["t", "u"]}  # fmt: skip
    assert merge({"projectId": "p", "sourceVersionIds": ["a"]}, {"projectId": "p", "targetVersionIds": ["u"]}) == {
        "projectId": "p"
    }
    src = {"projectId": "p", "scopeArtifactIds": ["a"]}
    merge(src, {"projectId": "p", "scopeArtifactIds": ["b"]})
    assert src == {"projectId": "p", "scopeArtifactIds": ["a"]}


def write_manifest(root: Path, name: str, body: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "experiment.yaml").write_text(body, encoding="utf-8")


def test_protected_hashes(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    other = "cd" * 32
    write_manifest(
        tmp_path,
        "e1",
        f"snapshot:\n  artifacts:\n    a: {HASH}\n    b: short\n    c: 5\n    d: {HASH.upper()}\n    e: {HASH}0\n",
    )
    write_manifest(tmp_path, "e2", f"snapshot:\n  artifacts:\n    a: {other}\n    b: {HASH}\n")
    write_manifest(tmp_path, "e3", "")
    write_manifest(tmp_path, "e4", "snapshot:\n")
    write_manifest(tmp_path, "e5", "snapshot:\n  artifacts:\n")
    (tmp_path / "e1" / "other.yaml").write_text(f"snapshot:\n  artifacts:\n    z: {'ef' * 32}\n", encoding="utf-8")
    assert protected_hashes(tmp_path) == [HASH, other]
    with caplog.at_level("WARNING"):
        assert protected_hashes(tmp_path / "missing") == []
    assert "experiments directory missing, no version is protected by an experiment" in caplog.text
    assert caplog.records[0].fields == {"dir": str(tmp_path / "missing")}


def test_protected_hashes_unreadable_manifest_raises(tmp_path: Path) -> None:
    write_manifest(tmp_path, "bad", "snapshot: [unclosed\n")
    with pytest.raises(Exception, match=r"."):  # noqa: B017
        protected_hashes(tmp_path)


async def test_manual_detect_and_derive() -> None:
    [s] = await manual("detect", "mini", {"scopeArtifactIds": ["a"]}, FakeGraph(), RULES)
    assert spec_tuple(s) == (DETECT, {"projectId": "mini", "scopeArtifactIds": ["a"]}, False)
    [s] = await manual("derive", "mini", {"ignored": 1}, FakeGraph(), RULES)
    assert spec_tuple(s) == (DERIVE, {"projectId": "mini"}, False)


async def test_manual_extract_and_full() -> None:
    for kind in ("extract", "full"):
        [s] = await manual(kind, "mini", {"p": 1}, FakeGraph(), RULES)
        assert spec_tuple(s) == (
            EXTRACT,
            {"projectId": "mini", "artifactVersionIds": [vid(n) for n in range(1, 6)], "p": 1},
            False,
        )
        assert await manual(kind, "mini", {}, FakeGraph(known=[]), RULES) == []


async def test_manual_tlr() -> None:
    [s] = await manual("tlr", "mini", {"p": 1}, FakeGraph(), RULES)
    assert spec_tuple(s) == (
        TLR,
        {
            "projectId": "mini",
            "sourceVersionIds": [vid(1), vid(2)],
            "targetVersionIds": [vid(3), vid(4)],
            "approaches": ["stub"],
            "p": 1,
        },
        False,
    )
    plain = Rules(kind_pairs=(KindPair("requirements", "code"), KindPair("requirements", "other")))
    specs = await manual("tlr", "mini", {}, FakeGraph(), plain)
    assert [s.input["targetVersionIds"] for s in specs] == [[vid(3), vid(4)], [vid(5)]]
    assert all("approaches" not in s.input for s in specs)
    assert await manual("tlr", "mini", {}, FakeGraph(known=[Version(vid(1), aid(1), "requirements")]), RULES) == []
    assert await manual("tlr", "mini", {}, FakeGraph(), Rules()) == []
    only_tgt = FakeGraph(known=[Version(vid(3), aid(3), "code")])
    assert await manual("tlr", "mini", {}, only_tgt, RULES) == []


async def test_manual_prune(tmp_path: Path) -> None:
    rules = Rules(
        retention=Retention(
            keep=7, dry_run=False, experiments_dir=tmp_path / "none", projects={"pp": {"keepVersionsPerArtifact": 2}}
        )
    )
    [s] = await manual("prune", "mini", {}, FakeGraph(), rules)
    assert spec_tuple(s) == (PRUNE_LINKS, {"projectId": "mini", "keepVersionsPerArtifact": 7, "dryRun": False}, False)
    [s] = await manual("prune", "pp", {"dryRun": True}, FakeGraph(), rules)
    assert s.input == {"projectId": "pp", "keepVersionsPerArtifact": 2, "dryRun": True}
    write_manifest(tmp_path, "e", f"snapshot:\n  artifacts:\n    a: {HASH}\n")
    with_hashes = Rules(retention=Retention(experiments_dir=tmp_path))
    [s] = await manual("prune", "mini", {}, FakeGraph(), with_hashes)
    assert s.input == {
        "projectId": "mini",
        "keepVersionsPerArtifact": 20,
        "dryRun": True,
        "protectedContentHashes": [HASH],
    }


class FakeKg:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows, self.calls = rows, []

    async def read(self, query: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((query, params))
        return self.rows


VERSION_ROWS = [{"id": "v1", "artifact": "a1", "kind": "code", "origin": "derived"}]


async def test_kg_graph_versions_and_current() -> None:
    kg = FakeKg(VERSION_ROWS)
    g = KgGraph(kg)  # type: ignore[arg-type]
    assert await g.versions("mini", ("v1", "v2")) == [Version("v1", "a1", "code", "derived")]
    query, params = kg.calls[-1]
    assert params == {"p": "mini", "ids": ["v1", "v2"]}
    assert "HAS_VERSION" in query and "v.id IN $ids" in query and "coalesce(a.origin, 'user')" in query
    assert await g.current("mini") == [Version("v1", "a1", "code", "derived")]
    query, params = kg.calls[-1]
    assert params == {"p": "mini"}
    assert "CURRENT_VERSION" in query and "projectId: $p" in query and "coalesce(a.origin, 'user')" in query


async def test_kg_graph_projects_and_policy() -> None:
    assert await KgGraph(FakeKg([{"id": "a"}, {"id": "b"}])).projects() == ["a", "b"]  # type: ignore[arg-type]
    kg = FakeKg([{"policy": "local-only"}])
    assert await KgGraph(kg).policy("mini") == "local-only"  # type: ignore[arg-type]
    assert kg.calls[-1][1] == {"p": "mini"}
    assert "p.llmPolicy" in kg.calls[-1][0]
    assert await KgGraph(FakeKg([])).policy("mini") is None  # type: ignore[arg-type]

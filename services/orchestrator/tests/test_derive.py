"""Model derivation rules (T2.7): derive steps in plan, no loop, per-project switch, manual kind."""

from dataclasses import replace
from pathlib import Path

from fakes import RULES, FakeGraph, aid, ingestion, vid
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_agent.contracts import Contracts
from sandman_orchestrator.rules import DERIVATION_ACTOR, DERIVE, ModelDerivation, Rules, Version, manual, plan
from test_rules import extraction

ON = replace(RULES, model_derivation=ModelDerivation(enabled=True))
INPUT = {"projectId": "mini"}
GRAPH = FakeGraph()
GRAPH.known += [Version(vid(6), aid(6), "architecture-doc"), Version(vid(7), aid(7), "architecture-model", "derived")]


async def skills(ev, rules=ON) -> list[str]:
    return [s.skill for s in await plan(ev, GRAPH, rules)]


async def test_user_doc_or_model_version_derives() -> None:
    [spec] = [s for s in await plan(extraction(6), GRAPH, ON) if s.skill == DERIVE]
    assert spec.input == INPUT and spec.mergeable


async def test_derived_model_version_never_derives() -> None:
    assert DERIVE not in await skills(extraction(7))


async def test_other_kinds_do_not_derive() -> None:
    assert DERIVE not in await skills(extraction(1, 3))


async def test_deleted_artifacts_derive() -> None:
    assert DERIVE in await skills(ingestion(deleted=[aid(6)]))


async def test_derivation_ingestion_never_derives() -> None:
    assert DERIVE not in await skills(ingestion(deleted=[aid(6)], actor=DERIVATION_ACTOR))


async def test_created_versions_alone_do_not_derive() -> None:
    assert DERIVE not in await skills(ingestion(created=[vid(6)]))


async def test_disabled_derives_nothing() -> None:
    off = replace(RULES, model_derivation=ModelDerivation(enabled=False))
    assert DERIVE not in await skills(extraction(6), off)
    assert DERIVE not in await skills(ingestion(deleted=[aid(6)]), off)


async def test_project_override() -> None:
    off = replace(RULES, model_derivation=ModelDerivation(True, {"mini": {"enabled": False}}))
    on = replace(RULES, model_derivation=ModelDerivation(False, {"mini": {"enabled": True}}))
    assert DERIVE not in await skills(extraction(6), off)
    assert DERIVE in await skills(extraction(6), on)


async def test_manual_derive_runs_even_when_disabled() -> None:
    [spec] = await manual("derive", "mini", {}, FakeGraph(), RULES)
    assert (spec.skill, spec.input) == (DERIVE, INPUT)


def test_shipped_config_enables_derivation_and_pairs_model_with_code() -> None:
    rules = Rules.load(Path(__file__).parents[3] / "config/orchestrator/rules.yaml", Contracts(DEFAULT_CONTRACTS_DIR))
    assert rules.model_derivation.project("any")
    assert ("architecture-model", "code") in [(p.source, p.target) for p in rules.kind_pairs]

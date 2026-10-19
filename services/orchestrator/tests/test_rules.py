"""Rule unit tests: events -> steps (Stage 1 §6)."""

from pathlib import Path

from fakes import Env, aid, event, ingestion, vid
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_agent.contracts import Contracts
from sandman_orchestrator.rules import DETECT, EXTRACT, TLR, Rules, Version, seconds, tlr_steps


def pending(env: Env) -> list[dict]:
    return [s for s in env.steps() if s["status"] == "PENDING"]


def extraction(*versions: int):
    data = {"projectId": "mini", "runId": "run_" + "0" * 32, "status": "completed", "elementCount": 1,
            "explicitLinkCount": 0, "pluginRuns": [], "artifactVersionIds": [vid(n) for n in versions]}  # fmt: skip
    return event("sandman.extraction.completed", data)


def test_seconds() -> None:
    assert (seconds("30s"), seconds("2m"), seconds("1h"), seconds(5)) == (30, 120, 3600, 5)


async def test_ingestion_extracts_created_and_reactivated(env: Env) -> None:
    await env.orch.handle(ingestion(created=[vid(1)], reactivated=[vid(2)]))
    [step] = pending(env)
    assert (step["skill"], step["agent"]) == (EXTRACT, "extraction-agent")
    assert env.orch.get_workflow(step["workflow_id"])["steps"][0]["input"] == {
        "projectId": "mini",
        "artifactVersionIds": [vid(1), vid(2)],
    }


async def test_deletion_only_ingestion_detects_deleted_artifacts(env: Env) -> None:
    await env.orch.handle(ingestion(deleted=[aid(1)]))
    [step] = pending(env)
    assert step["skill"] == DETECT
    assert step["input_json"] == f'{{"projectId": "mini", "scopeArtifactIds": ["{aid(1)}"]}}'


async def test_extraction_links_changed_x_counterparts(env: Env) -> None:
    await env.orch.handle(extraction(1))
    [step] = pending(env)
    assert step["skill"] == TLR
    assert f'"sourceVersionIds": ["{vid(1)}"]' in step["input_json"]
    assert f'"targetVersionIds": ["{vid(3)}", "{vid(4)}"]' in step["input_json"]
    assert '"approaches": ["stub-name-match"]' in step["input_json"]


async def test_extraction_without_kind_pair_detects(env: Env) -> None:
    await env.orch.handle(extraction(5))
    [step] = pending(env)
    assert step["skill"] == DETECT and aid(5) in step["input_json"]


async def test_tracelinks_updated_detects_source_and_target_artifacts(env: Env) -> None:
    data = {"projectId": "mini", "sourceVersionIds": [vid(1)], "targetVersionIds": [vid(3)], "runs": []}
    await env.orch.handle(event("sandman.tracelinks.updated", data))
    [step] = pending(env)
    assert step["skill"] == DETECT
    assert f'"scopeArtifactIds": ["{aid(1)}", "{aid(3)}"]' in step["input_json"]


async def test_tracelink_reviewed_detects_source_and_target_artifacts(env: Env) -> None:
    data = {
        "projectId": "mini",
        "linkIds": ["tl_" + "a" * 32],
        "status": "rejected",
        "actor": "user:alice",
        "at": "2026-10-19T10:00:00Z",
        "sourceVersionIds": [vid(1)],
        "targetVersionIds": [vid(3)],
    }
    await env.orch.handle(event("sandman.tracelink.reviewed", data))
    [step] = pending(env)
    assert step["skill"] == DETECT
    assert f'"scopeArtifactIds": ["{aid(1)}", "{aid(3)}"]' in step["input_json"]


async def test_debounce_merges_three_extractions_into_one_tlr_step(env: Env) -> None:
    env.with_rules(debounce=10)
    for n in (1, 2, 3):
        await env.orch.handle(extraction(n))
    [step] = pending(env)
    assert step["skill"] == TLR
    await env.orch.tick()  # inside the debounce window nothing starts
    assert [s["status"] for s in env.steps() if s["merged_into"] is None] == ["PENDING"]


async def test_inconsistencies_updated_and_status_changed_create_no_steps(env: Env) -> None:
    data = {"projectId": "mini", "runId": "run_" + "0" * 32, "status": "completed", "newIds": [], "reopenedIds": [],
            "autoResolvedIds": [], "stillOpenCount": 0}  # fmt: skip
    await env.orch.handle(event("sandman.inconsistencies.updated", data))
    assert env.steps() == []


async def test_duplicate_event_processed_once(env: Env) -> None:
    e = ingestion(created=[vid(1)])
    await env.orch.consume(e, 1)
    await env.orch.consume(e, 1)
    assert len(env.steps()) == 1


async def test_no_step_starts_while_another_of_the_project_runs(env: Env) -> None:
    env.skills["extract-artifact-versions"].script = ["hang"]
    await env.orch.handle(ingestion(created=[vid(1)], deleted=[aid(2)]))
    await env.orch.handle(ingestion(created=[vid(2)]))
    for _ in range(3):
        await env.orch.tick()
    assert sorted(s["status"] for s in env.steps()) == ["PENDING", "PENDING", "RUNNING"]


async def test_merged_step_moves_to_newest_workflow_and_old_workflow_records_merged_into(env: Env) -> None:
    env.with_rules(debounce=10)
    await env.orch.handle(extraction(1))
    [first] = pending(env)
    old = first["workflow_id"]
    await env.orch.handle(extraction(2))
    [target] = pending(env)
    assert target["id"] == first["id"] and target["workflow_id"] != old
    old_wf = env.orch.get_workflow(old)
    [marker] = old_wf["steps"]
    assert marker["mergedInto"] == target["id"] and old_wf["status"] == "SUCCEEDED"
    assert "mergedInto" not in env.orch.get_workflow(target["workflow_id"])["steps"][0]
    assert env.orch.get_workflow(target["workflow_id"])["status"] == "PENDING"


async def test_workflow_stays_running_while_debounce_timer_pending(env: Env) -> None:
    env.with_rules(debounce=10)
    wf = await env.orch.trigger("full", "mini", {}, "user:alice")
    await env.until(lambda: any(s["skill"] == EXTRACT and s["status"] == "SUCCEEDED" for s in env.steps()))
    assert any(s["status"] == "PENDING" for s in env.steps())
    assert env.orch.get_workflow(wf)["status"] == "RUNNING"


def test_invalid_rules_config_fails_at_startup(tmp_path, contracts) -> None:
    import pytest
    from sandman_agent.contracts import ContractError
    from sandman_orchestrator.rules import Rules

    bad = tmp_path / "rules.yaml"
    bad.write_text("maxConcurrentPerAgent: 0\n")
    with pytest.raises(ContractError, match="rules.yaml"):
        Rules.load(bad, contracts)


def test_shipped_rules_link_a_changed_code_version_to_the_documents_without_an_approaches_list() -> None:
    rules = Rules.load(Path(__file__).parents[3] / "config/orchestrator/rules.yaml", Contracts(DEFAULT_CONTRACTS_DIR))
    pair = next(p for p in rules.kind_pairs if (p.source, p.target) == ("architecture-doc", "code"))
    assert pair.approaches == ()
    doc, code = Version("ver_doc", "art_doc", "architecture-doc"), Version("ver_code", "art_code", "code")
    [step] = tlr_steps("p", [code], [doc, code], rules)
    assert step.input == {"projectId": "p", "sourceVersionIds": ["ver_doc"], "targetVersionIds": ["ver_code"]}


def test_retention_loaded_from_repository_config() -> None:
    rules = Rules.load(Path(__file__).parents[3] / "config/orchestrator/rules.yaml", Contracts(DEFAULT_CONTRACTS_DIR))
    r = rules.retention
    assert (r.enabled, r.interval, r.keep, r.dry_run) == (True, 86400.0, 20, True)
    assert rules.timeouts["prune-trace-links"] == 1800.0

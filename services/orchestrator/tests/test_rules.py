"""Rule unit tests: events -> steps (Stage 1 §6)."""

from fakes import Env, aid, event, ingestion, vid
from sandman_orchestrator.rules import DETECT, EXTRACT, TLR, seconds


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


async def test_debounce_merges_three_extractions_into_one_tlr_step(env: Env) -> None:
    env.with_rules(debounce=10)
    for n in (1, 2, 3):
        await env.orch.handle(extraction(n))
    [step] = pending(env)
    assert step["skill"] == TLR
    await env.orch.tick()  # inside the debounce window nothing starts
    assert [s["status"] for s in env.steps()] == ["PENDING"]


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

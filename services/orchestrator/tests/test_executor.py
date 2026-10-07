"""Executor tests against fake A2A agents (sandman_agent.a2a.serve) that publish completion events."""

import json
import sqlite3

from fakes import AGENTS, RULES, Env, ingestion, vid
from sandman_agent import SkillError
from sandman_orchestrator.rules import DETECT, EXTRACT, StepSpec


def statuses(env: Env) -> list[str]:
    return [s["status"] for s in env.steps()]


async def run_manual(env: Env, kind: str = "extract", **kw) -> str:
    return await env.orch.trigger(kind, "mini", {}, "user:alice", **kw)


async def test_success_completes_on_event_and_forwards_actor(env: Env) -> None:
    await run_manual(env)
    await env.until(lambda: statuses(env) == ["SUCCEEDED"])
    [step] = env.steps()
    assert json.loads(step["result_json"])["artifactVersionIds"] == [vid(n) for n in range(1, 6)]
    [ctx] = env.skills[EXTRACT].calls
    assert (ctx.actor, ctx.caller, ctx.workflow_id) == ("user:alice", "orchestrator", step["workflow_id"])
    assert ctx.message_id in {r[0] for r in env.orch.db.execute("SELECT message_id FROM messages")}
    assert env.orch.get_workflow(step["workflow_id"])["status"] == "SUCCEEDED"


async def test_event_chain_full_workflow(env: Env) -> None:
    await run_manual(env, "full")
    await env.until(lambda: statuses(env).count("SUCCEEDED") == 3 and len(statuses(env)) == 3)
    assert [s["skill"] for s in env.steps()][0] == EXTRACT
    assert len({s["workflow_id"] for s in env.steps()}) == 1


async def test_rejected_is_not_retried(env: Env) -> None:
    spec = StepSpec(EXTRACT, {"projectId": "mini", "artifactVersionIds": ["bad"]})
    with env.orch.db:
        env.orch._add_steps("mini", {"kind": "manual", "type": "x", "actor": "user:a"}, [spec], "t", False)
    await env.until(lambda: statuses(env) == ["FAILED"])
    assert env.steps()[0]["attempts"] == 1


async def test_retryable_failure_retries_then_fails(env: Env) -> None:
    env.skills[DETECT].script = [SkillError("INTERNAL", "boom", True)] * 3
    await run_manual(env, "detect")
    await env.until(lambda: statuses(env) == ["FAILED"])
    assert env.steps()[0]["attempts"] == 3 and len(env.skills[DETECT].calls) == 3


async def test_non_retryable_failure_fails_at_once(env: Env) -> None:
    env.skills[DETECT].script = [SkillError("INVALID_INPUT", "no", False)]
    await run_manual(env, "detect")
    await env.until(lambda: statuses(env) == ["FAILED"])
    assert env.steps()[0]["attempts"] == 1


async def test_budget_exceeded_blocks_step(env: Env) -> None:
    env.skills[DETECT].script = [SkillError("BUDGET_EXCEEDED", "limit", False)]
    wf = await run_manual(env, "detect")
    await env.until(lambda: statuses(env) == ["BLOCKED"])
    assert env.orch.get_workflow(wf)["status"] == "BLOCKED"


async def test_task_not_found_after_agent_restart_retries(env: Env) -> None:
    env.skills[DETECT].script = ["hang"]
    await run_manual(env, "detect")
    await env.until(lambda: bool(env.steps()) and env.steps()[0]["a2a_task_id"] is not None)
    env.restart_agent(DETECT)
    await env.until(lambda: statuses(env) == ["SUCCEEDED"])
    assert env.steps()[0]["attempts"] == 2


async def test_timeout_cancels_task_and_retries(env: Env) -> None:
    env.with_rules(timeouts={**RULES.timeouts, DETECT: 0.1})
    env.skills[DETECT].script = ["hang"]
    await run_manual(env, "detect")
    await env.until(lambda: statuses(env) == ["SUCCEEDED"])
    await env.until(lambda: env.skills[DETECT].cancelled == 1)
    assert env.steps()[0]["attempts"] == 2


async def test_identical_trigger_once_force_twice(env: Env) -> None:
    await env.orch.trigger("detect", "mini", {}, "user:alice", request_id="r1")
    await env.orch.trigger("detect", "mini", {}, "user:alice", request_id="r1")
    assert len(env.steps()) == 1
    await env.orch.trigger("detect", "mini", {}, "user:alice", force=True, request_id="r1")
    assert len(env.steps()) == 2
    await env.orch.trigger("detect", "mini", {}, "user:alice")  # a later trigger runs again
    assert len(env.steps()) == 3


async def test_cancel_sends_cancel_task(env: Env) -> None:
    env.skills[DETECT].script = ["hang"]
    wf = await run_manual(env, "detect")
    await env.until(lambda: bool(env.steps()) and env.steps()[0]["a2a_task_id"] is not None)
    assert await env.orch.cancel(wf)
    await env.until(lambda: env.skills[DETECT].cancelled == 1)
    assert statuses(env) == ["CANCELED"]


async def test_restart_resumes_running_step(env: Env, tmp_path) -> None:
    env.skills[DETECT].script = ["hang"]
    await run_manual(env, "detect")
    await env.until(lambda: bool(env.steps()) and env.steps()[0]["a2a_task_id"] is not None)
    env.orch.close()  # kill
    orch = env.new_orchestrator(tmp_path / "orchestrator.db")
    await orch.recover()
    assert statuses(env) == ["RUNNING"]
    env.skills[DETECT].release.set()  # agent finishes; its event reaches the restarted orchestrator
    await env.until(lambda: statuses(env) == ["SUCCEEDED"])
    assert env.steps()[0]["attempts"] == 1


async def test_recovery_processes_unprocessed_inbox_exactly_once(env: Env, tmp_path) -> None:
    e = ingestion(created=[vid(1)])
    env.orch.db.execute(
        "INSERT INTO inbox VALUES (?, ?, ?, ?, 'x', NULL)", (e.id, e.type, e.subject, json.dumps(e.to_json()))
    )
    env.orch.db.commit()
    env.orch.close()  # crash after the insert, before processing
    orch = env.new_orchestrator(tmp_path / "orchestrator.db")
    await orch.recover()
    await orch.consume(e, 2)  # JetStream redelivery of the unacked message
    assert len(env.steps()) == 1
    assert orch.db.execute("SELECT processed_at FROM inbox").fetchone()[0] is not None


async def test_invalid_event_goes_to_dead_events_and_replay_reprocesses(env: Env) -> None:
    bad = ingestion(created=["nope"])
    await env.orch.consume(bad, 1)
    assert [d["eventId"] for d in env.orch.dead_events()] == [bad.id]
    assert env.steps() == []
    env.orch.db.execute(
        "UPDATE dead_events SET payload = ?", (json.dumps(ingestion(created=[vid(1)], id=bad.id).to_json()),)
    )
    assert await env.orch.replay(bad.id)
    assert env.orch.dead_events() == [] and len(env.steps()) == 1
    assert not await env.orch.replay("missing")


def test_migrations_apply_once(tmp_path) -> None:
    from sandman_orchestrator.orchestrator import Orchestrator

    for _ in range(2):
        Orchestrator(tmp_path / "m.db", RULES, None, None, AGENTS, None).close()  # type: ignore[arg-type]
    assert sqlite3.connect(tmp_path / "m.db").execute("PRAGMA user_version").fetchone()[0] == 1

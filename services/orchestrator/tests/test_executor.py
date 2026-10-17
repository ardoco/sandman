"""Executor tests against fake A2A agents (sandman_agent.a2a.serve) that publish completion events."""

import json
import sqlite3

import httpx
import pytest
from fakes import AGENTS, RULES, Env, event, ingestion, output_and_event, vid
from sandman_agent import SkillError
from sandman_orchestrator.orchestrator import Conflict
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


async def test_completed_task_without_event_times_out_and_retries(env: Env) -> None:
    env.with_rules(timeouts={**RULES.timeouts, DETECT: 0.1})
    env.bus.target = None  # the completion event is lost
    await run_manual(env, "detect")
    await env.until(lambda: bool(env.steps()) and env.steps()[0]["status"] == "PENDING" and env.steps()[0]["attempts"])
    env.bus.target = env.orch.consume
    await env.until(lambda: statuses(env) == ["SUCCEEDED"])
    assert env.steps()[0]["attempts"] == 2 and env.skills[DETECT].cancelled == 0


async def test_late_completion_of_abandoned_attempt_is_ignored(env: Env) -> None:
    env.skills[DETECT].script = ["hang", "hang"]
    await run_manual(env, "detect")
    await env.until(lambda: bool(env.steps()) and env.steps()[0]["a2a_task_id"] is not None)
    env.restart_agent(DETECT)
    await env.until(lambda: env.steps()[0]["attempts"] == 2 and env.steps()[0]["a2a_task_id"] is not None)
    first = env.orch.db.execute("SELECT message_id FROM messages ORDER BY rowid").fetchone()[0]
    done = output_and_event(DETECT, {"projectId": "mini"})
    await env.orch.consume(event(done.event_type, dict(done.event_data), causation=first), 1)
    assert statuses(env) == ["RUNNING"]
    env.skills[DETECT].release.set()
    await env.until(lambda: statuses(env) == ["SUCCEEDED"])


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
    assert sqlite3.connect(tmp_path / "m.db").execute("PRAGMA user_version").fetchone()[0] == 4


def test_failing_migration_leaves_user_version(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from sandman_orchestrator.orchestrator import Orchestrator

    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: real(self, *a, **k) + (
        "; CREATE TABLE inbox (x)" if self.name.startswith("004") else ""))  # fmt: skip
    with pytest.raises(sqlite3.OperationalError):
        Orchestrator(tmp_path / "f.db", RULES, None, None, AGENTS, None)  # type: ignore[arg-type]
    assert sqlite3.connect(tmp_path / "f.db").execute("PRAGMA user_version").fetchone()[0] == 3


def test_upgrade_from_v1_keeps_rows_and_is_atomic(tmp_path) -> None:
    from pathlib import Path

    import sandman_orchestrator
    from sandman_orchestrator.orchestrator import Orchestrator

    migrations = Path(sandman_orchestrator.__file__).parent / "migrations"
    path = tmp_path / "v1.db"
    old = sqlite3.connect(path)
    old.executescript((migrations / "001_init.sql").read_text() + "; PRAGMA user_version = 1;")
    old.execute("INSERT INTO workflows VALUES ('wf_1', 'mini', '{}', 'RUNNING', 't', 't')")
    old.execute(
        "INSERT INTO steps (id, workflow_id, project_id, agent, skill, input_json, action_key, mergeable, status, "
        "next_attempt_at) VALUES ('step_1', 'wf_1', 'mini', 'a', 's', '{}', 'k', 0, 'PENDING', 0)"
    )
    old.commit()
    old.close()
    orch = Orchestrator(path, RULES, None, None, AGENTS, None)  # type: ignore[arg-type]
    row = orch.db.execute("SELECT id, traceparent FROM steps").fetchone()
    assert (row["id"], row["traceparent"]) == ("step_1", None)
    assert orch.db.execute("PRAGMA user_version").fetchone()[0] == 4
    orch.close()


async def test_crash_after_commit_before_ack_loses_nothing_and_duplicates_nothing(env: Env, tmp_path) -> None:
    e = ingestion(created=[vid(1)])
    await env.orch.handle(e)  # committed; the ack never happens
    env.orch.close()
    orch = env.new_orchestrator(tmp_path / "orchestrator.db")
    await orch.recover()
    await orch.consume(e, 2)  # redelivery
    assert statuses(env) == ["PENDING"]


async def test_crash_before_commit_is_redelivered(env: Env, tmp_path, monkeypatch) -> None:
    e = ingestion(created=[vid(1)])

    def boom(*a, **k):
        raise RuntimeError("crash")

    monkeypatch.setattr(env.orch, "_add_steps", boom)
    with pytest.raises(RuntimeError):
        await env.orch.consume(e, 1)  # not acked
    assert env.steps() == [] and env.orch.db.execute("SELECT count(*) FROM inbox").fetchone()[0] == 0
    env.orch.close()
    orch = env.new_orchestrator(tmp_path / "orchestrator.db")
    await orch.consume(e, 2)
    assert statuses(env) == ["PENDING"]


def test_action_key_vectors() -> None:
    from sandman_agent.config import DEFAULT_CONTRACTS_DIR
    from sandman_orchestrator.orchestrator import action_key

    for case in json.loads((DEFAULT_CONTRACTS_DIR / "conformance" / "vectors.json").read_text())["actionKey"]["cases"]:
        o = case["object"]
        assert action_key(o["agent"], o["skill"], o["input"], o["triggerId"]) == case["expected"], case["name"]


async def test_blocked_workflow_retry_resumes_and_non_blocked_conflicts(env: Env) -> None:
    env.skills[DETECT].script = [SkillError("BUDGET_EXCEEDED", "limit", False)]
    wf = await run_manual(env, "detect")
    await env.until(lambda: statuses(env) == ["BLOCKED"])
    assert env.orch.retry(wf)
    await env.until(lambda: statuses(env) == ["SUCCEEDED"])
    assert env.orch.get_workflow(wf)["status"] == "SUCCEEDED"
    with pytest.raises(Conflict):
        env.orch.retry(wf)
    assert not env.orch.retry("wf_" + "0" * 32)


async def test_retry_after_unacknowledged_submit_reuses_message_id_and_runs_skill_once(env: Env) -> None:
    env.skills[DETECT].script = ["hang"]
    send, sent = env.orch.a2a.send, []

    async def lossy_send(agent_url, message):
        sent.append(message["messageId"])
        snap = await send(agent_url, message)
        if len(sent) == 1:
            raise httpx.ReadTimeout("response lost")
        return snap

    env.orch.a2a.send = lossy_send
    await run_manual(env, "detect")
    await env.until(lambda: len(sent) == 2)
    env.skills[DETECT].release.set()
    await env.until(lambda: statuses(env) == ["SUCCEEDED"])
    assert sent[0] == sent[1] and len(env.skills[DETECT].calls) == 1
    assert env.orch.db.execute("SELECT count(*) FROM messages").fetchone()[0] == 1


async def test_retry_after_agent_error_uses_new_message_id(env: Env) -> None:
    env.skills[DETECT].script = [SkillError("INTERNAL", "boom", True)]
    await run_manual(env, "detect")
    await env.until(lambda: statuses(env) == ["SUCCEEDED"])
    first, second = (c.message_id for c in env.skills[DETECT].calls)
    assert first != second


async def test_retry_after_jsonrpc_error_answer_uses_new_message_id(env: Env) -> None:
    send, sent = env.orch.a2a.send, []

    async def rejecting_send(agent_url, message):
        sent.append(message["messageId"])
        if len(sent) == 1:
            raise RuntimeError("JSON-RPC error -32602")
        return await send(agent_url, message)

    env.orch.a2a.send = rejecting_send
    await run_manual(env, "detect")
    await env.until(lambda: statuses(env) == ["SUCCEEDED"])
    assert len(sent) == 2 and sent[0] != sent[1]


async def test_agent_activity_is_stored_while_running_and_dropped_after(env: Env) -> None:
    from test_rest import check

    env.skills[EXTRACT].script = ["talk"]
    wf = await run_manual(env)
    await env.until(lambda: any(s["activity_text"] for s in env.steps()))
    detail = env.orch.get_workflow(wf)
    assert detail is not None
    check(detail, "WorkflowDetail")
    assert detail["steps"][0]["activity"]["text"] == "annotating sentences 1/3"
    env.skills[EXTRACT].release.set()
    await env.until(lambda: statuses(env) == ["SUCCEEDED"])
    done = env.orch.get_workflow(wf)
    assert done is not None and "activity" not in done["steps"][0]
    check(done, "WorkflowDetail")

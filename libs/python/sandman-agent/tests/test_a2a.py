import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sandman_agent.a2a import A2aClient, SkillContext, SkillError, SkillResult, serve
from sandman_agent.config import DEFAULT_CONTRACTS_DIR, Settings
from sandman_agent.contracts import Contracts
from sandman_agent.testing import InMemoryEventBus

URL = "http://tlr-agent:8080"
TOKENS = {"orchestrator": "orch-token", "tlr-agent": "tlr-token", "plugin:plugin-stubs": "plugin-token"}
CARD = DEFAULT_CONTRACTS_DIR / "agent-cards" / "tlr-agent.json"
TLR_OUTPUT: dict[str, Any] = {
    "runs": [
        {"approach": "stub", "approachVersion": "0", "runId": "run_" + "0" * 32, "status": "completed", "linkCount": 1}
    ]
}
INPUT = {"projectId": "mini", "sourceVersionIds": ["ver_" + "0" * 32], "targetVersionIds": ["ver_" + "1" * 32]}


class Recover:
    id = "recover-trace-links"

    def __init__(self, output: dict[str, Any] | None = None, error: SkillError | None = None) -> None:
        self.output, self.error = output, error
        self.seen: list[SkillContext] = []

    async def run(self, ctx: SkillContext, input: dict[str, Any]) -> SkillResult:
        self.seen.append(ctx)
        if self.error:
            raise self.error
        out = self.output if self.output is not None else TLR_OUTPUT
        event = {"projectId": input["projectId"], "sourceVersionIds": input["sourceVersionIds"],
                 "targetVersionIds": input["targetVersionIds"], "runs": out.get("runs", [])}  # fmt: skip
        return SkillResult(out, "sandman.tracelinks.updated", event)


async def wait(client: A2aClient, task_id: str) -> Any:
    for _ in range(100):
        snap = await client.get(URL, task_id)
        if snap.terminal:
            return snap
        await asyncio.sleep(0.01)
    raise AssertionError("task did not finish")


def setup(settings: Settings, contracts: Contracts, skill: Recover, token: str = TOKENS["orchestrator"]):  # type: ignore[no-untyped-def]
    bus = InMemoryEventBus(contracts, "tlr-agent")
    app = serve(CARD, [skill], settings, events=bus, contracts=contracts)
    return bus, A2aClient(token, transport=httpx.ASGITransport(app=app))


async def test_round_trip_publishes_event_before_completion(settings: Settings, contracts: Contracts) -> None:
    skill = Recover()
    bus, client = setup(settings, contracts, skill)
    task = await client.submit(
        URL, "recover-trace-links", INPUT, workflow_id="wf_" + "a" * 32, actor="user:alice", message_id="m-1"
    )
    done = await wait(client, task.task_id)
    assert done.state == "TASK_STATE_COMPLETED"
    assert done.result == TLR_OUTPUT
    assert len(bus.events) == 1 and bus.events[0].causation_id == "m-1"
    assert bus.events[0].correlation_id == "wf_" + "a" * 32
    assert skill.seen[0].caller == "orchestrator" and skill.seen[0].actor == "user:alice"


async def test_actor_rejected_from_non_orchestrator(settings: Settings, contracts: Contracts) -> None:
    skill = Recover()
    _, client = setup(settings, contracts, skill, TOKENS["tlr-agent"])
    task = await client.submit(URL, "recover-trace-links", INPUT, actor="user:mallory")
    done = await wait(client, task.task_id)
    assert done.state == "TASK_STATE_REJECTED" and done.error["code"] == "FORBIDDEN" and not skill.seen


async def test_invalid_output_fails_without_event(settings: Settings, contracts: Contracts) -> None:
    bus, client = setup(settings, contracts, Recover(output={"runs": "nope"}))
    done = await wait(client, (await client.submit(URL, "recover-trace-links", INPUT)).task_id)
    assert done.state == "TASK_STATE_FAILED"
    assert done.error == {"code": "INTERNAL", "message": done.error["message"], "retryable": False}
    assert bus.events == []


async def test_skill_error_is_reported(settings: Settings, contracts: Contracts) -> None:
    _, client = setup(settings, contracts, Recover(error=SkillError("BUDGET_EXCEEDED", "budget", False)))
    done = await wait(client, (await client.submit(URL, "recover-trace-links", INPUT)).task_id)
    assert done.state == "TASK_STATE_FAILED" and done.error["code"] == "BUDGET_EXCEEDED"


async def test_unknown_and_plugin_tokens_are_rejected(settings: Settings, contracts: Contracts) -> None:
    for token in ("wrong", TOKENS["plugin:plugin-stubs"]):
        _, client = setup(settings, contracts, Recover(), token)
        with pytest.raises(Exception):  # noqa: B017 - 401 surfaces as a client error
            await client.submit(URL, "recover-trace-links", INPUT)


async def test_card_is_public(settings: Settings, contracts: Contracts) -> None:
    _, client = setup(settings, contracts, Recover(), "wrong")
    card_json = await client.card(URL)
    assert card_json["name"] == json.loads(CARD.read_text())["name"]


@pytest.mark.parametrize(
    "case",
    json.loads((DEFAULT_CONTRACTS_DIR / "conformance/vectors.json").read_text())["skillInvocation"]["cases"],
    ids=lambda c: c["name"],
)
async def test_skill_invocation_vectors(case: dict[str, Any], settings: Settings, contracts: Contracts) -> None:
    _, client = setup(settings, contracts, Recover())
    skill = case["message"]["metadata"].get("sandman.skill")
    done = await wait(client, (await client.send(URL, case["message"])).task_id)
    if case["expectedState"] == "TASK_STATE_REJECTED":
        assert done.state == "TASK_STATE_REJECTED" and done.error["code"] == case["expectedErrorCode"], (skill, done)
    else:
        assert done.state in {"TASK_STATE_SUBMITTED", "TASK_STATE_WORKING", "TASK_STATE_COMPLETED"}


async def test_status_reports_working_while_a_skill_runs(settings: Settings, contracts: Contracts) -> None:
    started, release = asyncio.Event(), asyncio.Event()

    class Slow(Recover):
        async def run(self, ctx: SkillContext, input: dict[str, Any]) -> SkillResult:
            started.set()
            await release.wait()
            return await super().run(ctx, input)

    bus = InMemoryEventBus(contracts, "tlr-agent")
    app = serve(CARD, [Slow()], settings, events=bus, contracts=contracts)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL)
    headers = {"Authorization": f"Bearer {TOKENS['orchestrator']}"}
    assert (await http.get("/v1/status")).status_code == 401
    idle = (await http.get("/v1/status", headers=headers)).json()
    assert idle["state"] == "idle" and idle["activeTasks"] == 0 and idle["service"] == "tlr-agent"
    client = A2aClient(TOKENS["orchestrator"], transport=httpx.ASGITransport(app=app))
    task = await client.submit(URL, "recover-trace-links", INPUT)
    await started.wait()
    working = (await http.get("/v1/status", headers=headers)).json()
    assert working["state"] == "working" and working["activeTasks"] == 1
    release.set()
    await wait(client, task.task_id)
    assert (await http.get("/v1/status", headers=headers)).json()["state"] == "idle"


async def test_planned_skill_is_rejected_not_implemented(
    settings: Settings, contracts: Contracts, tmp_path: Path
) -> None:
    card = json.loads(CARD.read_text())
    params = card["capabilities"]["extensions"][0]["params"]
    params["plannedSkills"] = {"prune-trace-links": params["skills"].pop("prune-trace-links")}
    planned_card = tmp_path / "card.json"
    planned_card.write_text(json.dumps(card))
    bus = InMemoryEventBus(contracts, "tlr-agent")
    client = A2aClient(
        TOKENS["orchestrator"],
        transport=httpx.ASGITransport(app=serve(planned_card, [Recover()], settings, events=bus, contracts=contracts)),
    )
    task = await client.submit(URL, "prune-trace-links", {})
    done = await wait(client, task.task_id)
    assert (
        done.state == "TASK_STATE_REJECTED"
        and done.error["code"] == "NOT_IMPLEMENTED"
        and done.error["retryable"] is False
    )


async def test_same_message_id_runs_the_skill_once(settings: Settings, contracts: Contracts) -> None:
    started, release = asyncio.Event(), asyncio.Event()

    class Slow(Recover):
        async def run(self, ctx: SkillContext, input: dict[str, Any]) -> SkillResult:
            started.set()
            await release.wait()
            return await super().run(ctx, input)

    skill = Slow()
    bus, client = setup(settings, contracts, skill)
    first = await client.submit(URL, "recover-trace-links", INPUT, message_id="m-dup")
    await started.wait()
    second = await client.submit(URL, "recover-trace-links", INPUT, message_id="m-dup")
    release.set()
    for snap in (first, second):
        assert (await wait(client, snap.task_id)).result == TLR_OUTPUT
    third = await client.submit(URL, "recover-trace-links", INPUT, message_id="m-dup")
    assert (await wait(client, third.task_id)).result == TLR_OUTPUT
    assert len(skill.seen) == 1 and len(bus.events) == 1
    other = await client.submit(URL, "recover-trace-links", INPUT, message_id="m-other")
    await wait(client, other.task_id)
    assert len(skill.seen) == 2


async def test_reused_message_id_with_different_input_is_rejected_without_running(
    settings: Settings, contracts: Contracts
) -> None:
    skill = Recover()
    _, client = setup(settings, contracts, skill)
    await wait(client, (await client.submit(URL, "recover-trace-links", INPUT, message_id="m-fp")).task_id)
    other = {**INPUT, "projectId": "other"}
    snap = await wait(client, (await client.submit(URL, "recover-trace-links", other, message_id="m-fp")).task_id)
    assert snap.state == "TASK_STATE_REJECTED" and snap.result is None
    assert snap.error is not None and snap.error["code"] == "INVALID_INPUT" and snap.error["retryable"] is False
    assert len(skill.seen) == 1


async def test_same_message_id_from_different_callers_runs_separately(settings: Settings, contracts: Contracts) -> None:
    skill = Recover()
    app = serve(CARD, [skill], settings, events=InMemoryEventBus(contracts, "tlr-agent"), contracts=contracts)
    transport = httpx.ASGITransport(app=app)
    one = A2aClient(TOKENS["orchestrator"], transport=transport)
    two = A2aClient(TOKENS["tlr-agent"], transport=transport)
    first = await wait(one, (await one.submit(URL, "recover-trace-links", INPUT, message_id="m-c")).task_id)
    skill.output = {"runs": []}
    second = await wait(two, (await two.submit(URL, "recover-trace-links", INPUT, message_id="m-c")).task_id)
    assert first.result == TLR_OUTPUT and second.result == {"runs": []}
    assert [c.caller for c in skill.seen] == ["orchestrator", "tlr-agent"]


async def test_retryable_failure_is_not_cached_but_permanent_failure_is(
    settings: Settings, contracts: Contracts
) -> None:
    retry = Recover(error=SkillError("INTERNAL", "boom", True))
    _, client = setup(settings, contracts, retry)
    for _ in range(2):
        await wait(client, (await client.submit(URL, "recover-trace-links", INPUT, message_id="m-r")).task_id)
    assert len(retry.seen) == 2
    permanent = Recover(error=SkillError("INVALID_INPUT", "bad", False))
    _, client = setup(settings, contracts, permanent)
    for _ in range(2):
        await wait(client, (await client.submit(URL, "recover-trace-links", INPUT, message_id="m-p")).task_id)
    assert len(permanent.seen) == 1


async def test_eviction_never_drops_pending_entries(
    settings: Settings, contracts: Contracts, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sandman_agent.a2a.MAX_SEEN", 1)
    release = asyncio.Event()

    class Slow(Recover):
        async def run(self, ctx: SkillContext, input: dict[str, Any]) -> SkillResult:
            await release.wait()
            return await super().run(ctx, input)

    skill = Slow()
    _, client = setup(settings, contracts, skill)
    snaps = [await client.submit(URL, "recover-trace-links", INPUT, message_id=m) for m in ("m-1", "m-2", "m-1")]
    release.set()
    for snap in snaps:
        await wait(client, snap.task_id)
    assert len(skill.seen) == 2


async def test_progress_text_reaches_the_caller_and_is_throttled(settings: Settings, contracts: Contracts) -> None:
    started, release = asyncio.Event(), asyncio.Event()

    class Talk(Recover):
        async def run(self, ctx: SkillContext, input: dict[str, Any]) -> SkillResult:
            await ctx.progress("annotating 1/3")
            await ctx.progress("annotating 2/3")
            started.set()
            await release.wait()
            return await super().run(ctx, input)

    bus = InMemoryEventBus(contracts, "tlr-agent")
    app = serve(CARD, [Talk()], settings, events=bus, contracts=contracts)
    client = A2aClient(TOKENS["orchestrator"], transport=httpx.ASGITransport(app=app))
    task = await client.submit(URL, "recover-trace-links", INPUT)
    await started.wait()
    for _ in range(100):
        snap = await client.get(URL, task.task_id)
        if snap.activity:
            break
        await asyncio.sleep(0.01)
    assert snap.state == "TASK_STATE_WORKING" and snap.activity == "annotating 1/3"
    release.set()
    done = await wait(client, task.task_id)
    assert done.state == "TASK_STATE_COMPLETED" and done.activity is None

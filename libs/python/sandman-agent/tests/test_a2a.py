import asyncio
import json
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


async def test_planned_skill_is_rejected_not_implemented(settings: Settings, contracts: Contracts) -> None:
    _, client = setup(settings, contracts, Recover())
    task = await client.submit(URL, "prune-trace-links", {})
    done = await wait(client, task.task_id)
    assert (
        done.state == "TASK_STATE_REJECTED"
        and done.error["code"] == "NOT_IMPLEMENTED"
        and done.error["retryable"] is False
    )

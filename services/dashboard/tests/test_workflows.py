import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from dash_fakes import ADMIN_PASSWORD, SERVICE_TOKEN, Env

WF = "wf_" + "a" * 32
WF2 = "wf_" + "b" * 32
BAD = "<script>alert(1)</script>"


def workflow(wf_id: str, status: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": wf_id,
        "projectId": "demo",
        "trigger": {"kind": "event", "eventType": "sandman.ingestion.completed"},
        "status": status,
        "createdAt": "2026-10-08T10:00:00Z",
        "updatedAt": "2026-10-08T10:00:05Z",
        **extra,
    }


STEP = {
    "id": "s1",
    "agent": "tlr-agent",
    "skill": "tlr",
    "status": "FAILED",
    "attempts": 2,
    "actionKey": "0" * 64,
    "a2aTaskId": "task-1",
    "startedAt": "2026-10-08T10:00:00Z",
    "endedAt": "2026-10-08T10:00:02Z",
    "input": {"x": BAD},
    "result": {"y": 1},
    "error": {"code": "E_FAIL", "message": BAD, "retryable": True},
}


class FakeOrchestrator:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.down = False
        self.cancel_status = 202
        self.replay_status = 202
        self.workflows = {WF: workflow(WF, "RUNNING", steps=[STEP]), WF2: workflow(WF2, "SUCCEEDED", steps=[])}
        self.dead = [{"eventId": "evt/1", "type": "t", "error": BAD, "receivedAt": "2026-10-08T09:00:00Z"}]

    async def __call__(self, request: httpx.Request) -> httpx.Response | None:
        path = request.url.path
        if request.url.host != "orchestrator" or not path.startswith("/v1/") or path == "/v1/status":
            return None
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        if path == "/v1/workflows":
            items = [w for w in self.workflows.values() if request.url.params.get("status") in (None, w["status"])]
            body: dict[str, Any] = {"items": items}
            if request.url.params.get("cursor") is None:
                body["nextCursor"] = "50"
            return httpx.Response(200, json=body)
        if path == "/v1/dead-events":
            return httpx.Response(200, json={"items": self.dead})
        if path.endswith("/cancel"):
            code = self.cancel_status
            return httpx.Response(
                code, json=self.workflows[path.split("/")[3]] if code == 202 else {"detail": SERVICE_TOKEN}
            )
        if path.endswith("/replay"):
            return httpx.Response(
                self.replay_status, content=b"" if self.replay_status == 202 else b"secret " + SERVICE_TOKEN.encode()
            )
        wf = self.workflows.get(path.rsplit("/", 1)[1])
        return httpx.Response(200, json=wf) if wf else httpx.Response(404, json={"title": "workflow not found"})


@pytest.fixture
def orch(env: Env) -> FakeOrchestrator:
    fake = FakeOrchestrator()

    async def hook(request: httpx.Request) -> httpx.Response | None:
        return await fake(request)

    env.upstream.hook = hook
    return fake


async def test_requires_session(env: Env, orch: FakeOrchestrator) -> None:
    assert (await env.http.get("/workflows")).headers["location"] == "/login"
    assert (await env.http.get(f"/workflows/{WF}")).status_code == 303
    assert (await env.http.get(f"/fragments/workflows/{WF}")).status_code == 401
    assert (await env.http.post(f"/workflows/{WF}/cancel", data={"csrf": "x"})).status_code == 303
    assert (await env.http.post("/dead-events/replay", data={"eventId": "e"})).status_code == 303
    assert orch.requests == []


async def test_list_filters_paging_and_dead_events(env: Env, orch: FakeOrchestrator) -> None:
    await env.login()
    text = (await env.http.get("/workflows", params={"projectId": "demo", "status": "RUNNING", "cursor": "50"})).text
    assert WF in text and WF2 not in text and "Next page" not in text
    request = orch.requests[0]
    assert request.url.params["projectId"] == "demo" and request.url.params["status"] == "RUNNING"
    assert request.url.params["cursor"] == "50"
    assert request.headers["authorization"] == f"Bearer {SERVICE_TOKEN}" and "x-sandman-actor" not in request.headers
    text = (await env.http.get("/workflows")).text
    assert "cursor=50" in text and "evt/1" in text and "&lt;script&gt;alert(1)" in text and BAD not in text


async def test_invalid_filter_is_rejected_without_upstream_call(env: Env, orch: FakeOrchestrator) -> None:
    await env.login()
    text = (await env.http.get("/workflows", params={"status": "NOPE"})).text
    assert "invalid filter" in text and orch.requests == []


async def test_detail_escapes_and_refreshes_only_while_active(env: Env, orch: FakeOrchestrator) -> None:
    await env.login()
    text = (await env.http.get(f"/workflows/{WF}")).text
    assert "every 5s" in text and "Cancel workflow" in text and "hx-confirm" in text
    assert "E_FAIL" in text and "(retryable)" in text and "2.0 s" in text and "task-1" in text
    assert BAD not in text and "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    done = (await env.http.get(f"/fragments/workflows/{WF2}")).text
    assert "every 5s" not in done and "Cancel workflow" not in done


async def test_cancel_needs_csrf_and_forwards_with_audit_log(
    env: Env, orch: FakeOrchestrator, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    await env.login()
    token = await env.csrf(f"/workflows/{WF}")
    assert (await env.http.post(f"/workflows/{WF}/cancel", data={"csrf": "wrong"})).status_code == 403
    assert (await env.http.post(f"/workflows/{WF}/cancel", data={})).status_code == 403
    assert not [r for r in orch.requests if r.method == "POST"]
    resp = await env.http.post(f"/workflows/{WF}/cancel", data={"csrf": token})
    assert resp.status_code == 200 and "cancel requested" in resp.text
    post = [r for r in orch.requests if r.method == "POST"]
    assert len(post) == 1 and post[0].url.path == f"/v1/workflows/{WF}/cancel"
    assert post[0].headers["authorization"] == f"Bearer {SERVICE_TOKEN}" and "x-sandman-actor" not in post[0].headers
    audit = [
        r.__dict__["fields"] for r in caplog.records if r.__dict__.get("fields", {}).get("event") == "admin_action"
    ]
    assert audit == [{"event": "admin_action", "action": "cancel_workflow", "target": WF, "upstream": 202}]


@pytest.mark.parametrize(("code", "text"), [(404, "not found"), (409, "no pending or running steps")])
async def test_cancel_problems_are_messages(env: Env, orch: FakeOrchestrator, code: int, text: str) -> None:
    await env.login()
    token = await env.csrf(f"/workflows/{WF}")
    orch.cancel_status = code
    resp = await env.http.post(f"/workflows/{WF}/cancel", data={"csrf": token})
    assert resp.status_code == 200 and "cancel failed: " in resp.text and text in resp.text
    assert SERVICE_TOKEN not in resp.text


async def test_replay_and_404(env: Env, orch: FakeOrchestrator, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    await env.login()
    token = await env.csrf("/workflows")
    assert (await env.http.post("/dead-events/replay", data={"eventId": "evt/1", "csrf": "bad"})).status_code == 403
    resp = await env.http.post("/dead-events/replay", data={"eventId": "evt/1", "csrf": token})
    assert "replay requested" in resp.text
    assert orch.requests[-3].url.raw_path == b"/v1/dead-events/evt%2F1/replay"
    orch.replay_status = 404
    resp = await env.http.post("/dead-events/replay", data={"eventId": "evt/1", "csrf": token})
    assert "replay failed: not found" in resp.text and SERVICE_TOKEN not in resp.text
    audit = [
        r.__dict__["fields"] for r in caplog.records if r.__dict__.get("fields", {}).get("event") == "admin_action"
    ]
    assert [a["upstream"] for a in audit] == [202, 404] and audit[0]["action"] == "replay_dead_event"


async def test_unknown_workflow_and_bad_id(env: Env, orch: FakeOrchestrator) -> None:
    await env.login()
    unknown = await env.http.get("/workflows/wf_" + "c" * 32)
    assert unknown.status_code == 200 and "workflow not found" in unknown.text
    assert (await env.http.get("/workflows/not-an-id")).status_code == 404


async def test_orchestrator_down_or_5xx_renders_message(env: Env, orch: FakeOrchestrator) -> None:
    await env.login()
    token = await env.csrf("/workflows")
    for failure in ("down", "5xx"):
        orch.down = failure == "down"
        if failure == "5xx":

            async def boom(request: httpx.Request) -> httpx.Response | None:
                if (
                    request.url.host == "orchestrator"
                    and request.url.path.startswith("/v1/")
                    and request.url.path != "/v1/status"
                ):
                    return httpx.Response(500, text=f"trace {SERVICE_TOKEN}")
                return None

            env.upstream.hook = boom
        for path in ("/workflows", f"/workflows/{WF}"):
            resp = await env.http.get(path)
            assert (
                resp.status_code == 200 and "orchestrator unreachable" in resp.text and SERVICE_TOKEN not in resp.text
            )
        resp = await env.http.post(f"/workflows/{WF}/cancel", data={"csrf": token})
        assert resp.status_code == 200 and "orchestrator unreachable" in resp.text
        resp = await env.http.post("/dead-events/replay", data={"eventId": "e", "csrf": token})
        assert resp.status_code == 200 and "orchestrator unreachable" in resp.text


async def test_failed_workflow_and_dead_event_raise_alerts_after_poll(env: Env, orch: FakeOrchestrator) -> None:
    orch.workflows[WF2] = workflow(WF2, "FAILED", updatedAt=datetime.now(UTC).isoformat(), steps=[])
    await env.login()
    await env.app.state.poller.poll_once()
    banner = (await env.http.get("/fragments/alerts")).text
    assert (
        f'href="/workflows/{WF2}"' in banner and "1 dead events" in banner and 'href="/workflows#dead-events"' in banner
    )
    orch.down = True
    await env.app.state.poller.poll_once()
    assert f"/workflows/{WF2}" in (await env.http.get("/fragments/alerts")).text


async def test_secrets_in_step_data_are_redacted(env: Env, orch: FakeOrchestrator) -> None:
    orch.workflows[WF]["steps"] = [{**STEP, "input": {"t": SERVICE_TOKEN}, "result": {"p": ADMIN_PASSWORD}}]
    await env.login()
    text = (await env.http.get(f"/workflows/{WF}")).text
    assert SERVICE_TOKEN not in text and ADMIN_PASSWORD not in text and "[redacted]" in text

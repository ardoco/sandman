"""REST API tests; responses are validated against contracts/openapi/orchestrator.yaml schemas."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import yaml
from fakes import TOKENS, VERIFY, Env, ingestion
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from sandman_agent import TokenVerifier
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_orchestrator.app import create_app

COMMON = json.loads((DEFAULT_CONTRACTS_DIR / "common.schema.json").read_text())
SPEC = yaml.safe_load((DEFAULT_CONTRACTS_DIR / "openapi" / "orchestrator.yaml").read_text())
SHARED = yaml.safe_load((DEFAULT_CONTRACTS_DIR / "openapi" / "shared.yaml").read_text())
REGISTRY: Registry[Any] = Registry().with_resources([(COMMON["$id"], Resource.from_contents(COMMON))])


def check(body: Any, schema: str, spec: dict[str, Any] = SPEC) -> None:
    text = json.dumps({"$ref": f"#/components/schemas/{schema}", "components": spec["components"]})
    text = text.replace("../common.schema.json#", COMMON["$id"] + "#")
    Draft202012Validator(json.loads(text), registry=REGISTRY).validate(body)


def auth(name: str = "query-service", **headers: str) -> dict[str, str]:
    return {"authorization": f"Bearer {TOKENS[name]}", **headers}


@pytest.fixture
async def client(env: Env) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(env.orch, TokenVerifier(VERIFY))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://orch") as c:
        yield c


async def test_auth(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/workflows")).status_code == 401
    assert (await client.get("/healthz")).status_code == 200
    body = {"type": "detect", "projectId": "mini"}
    assert (await client.post("/v1/workflows", json=body, headers=auth("tlr-agent"))).status_code == 403
    assert (await client.post("/v1/workflows", json=body, headers=auth("dashboard"))).status_code == 403


async def test_workflow_lifecycle(client: httpx.AsyncClient, env: Env) -> None:
    body = {"type": "detect", "projectId": "mini", "params": {}}
    r = await client.post("/v1/workflows", json=body, headers=auth(**{"x-sandman-actor": "user:alice"}))
    assert r.status_code == 202
    check(r.json(), "WorkflowDetail")
    assert (
        r.json()["trigger"] == {"kind": "manual", "type": "detect", "actor": "user:alice"}
        or r.json()["trigger"]["actor"] == "user:alice"
    )
    wf = r.json()["id"]

    r = await client.get(f"/v1/workflows/{wf}", headers=auth("dashboard"))
    assert r.status_code == 200
    check(r.json(), "WorkflowDetail")

    r = await client.get("/v1/workflows", params={"projectId": "mini", "status": "PENDING", "limit": 1}, headers=auth())
    assert r.status_code == 200 and len(r.json()["items"]) == 1
    for item in r.json()["items"]:
        check(item, "Workflow")

    r = await client.post(f"/v1/workflows/{wf}/cancel", headers=auth("dashboard"))
    assert r.status_code == 202 and r.json()["status"] == "CANCELED"
    check(r.json(), "WorkflowDetail")
    assert (await client.post(f"/v1/workflows/{wf}/cancel", headers=auth())).status_code == 409


async def test_errors(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/workflows/wf_" + "0" * 32, headers=auth())).status_code == 404
    assert (await client.post("/v1/workflows/wf_" + "0" * 32 + "/cancel", headers=auth())).status_code == 404
    r = await client.post("/v1/workflows", json={"type": "nope", "projectId": "mini"}, headers=auth())
    assert r.status_code == 400 and r.headers["content-type"] == "application/problem+json"
    r = await client.post(
        "/v1/workflows", json={"type": "detect", "projectId": "mini", "params": {"x": 1}}, headers=auth()
    )
    assert r.status_code == 400
    assert (await client.get("/v1/workflows", params={"limit": 0}, headers=auth())).status_code == 400


async def test_dead_events_and_replay(client: httpx.AsyncClient, env: Env) -> None:
    bad = ingestion(created=["nope"])
    await env.orch.consume(bad, 1)
    r = await client.get("/v1/dead-events", headers=auth())
    assert r.status_code == 200 and r.json()["items"][0]["eventId"] == bad.id
    assert (await client.post("/v1/dead-events/missing/replay", headers=auth())).status_code == 404


async def test_status(client: httpx.AsyncClient, env: Env) -> None:
    await env.orch.trigger("detect", "mini", {}, "user:alice")
    r = await client.get("/v1/status", headers=auth("dashboard"))
    assert r.status_code == 200
    check(r.json(), "ServiceStatus", SHARED)
    assert r.json()["details"] == {"runningWorkflows": 0, "pendingSteps": 1}


async def test_readyz(client: httpx.AsyncClient) -> None:
    assert (await client.get("/readyz")).json() == {"status": "ok", "checks": {"sqlite": "ok"}}


async def test_dashboard_may_cancel_and_replay_but_other_callers_may_not(client: httpx.AsyncClient, env: Env) -> None:
    wf = await env.orch.trigger("detect", "mini", {}, "user:alice")
    other = auth("tlr-agent")
    assert (await client.post(f"/v1/workflows/{wf}/cancel", headers=other)).status_code == 403
    assert (await client.post("/v1/dead-events/x/replay", headers=other)).status_code == 403
    assert (await client.post(f"/v1/workflows/{wf}/cancel", headers=auth("dashboard"))).status_code == 202
    bad = ingestion(created=["nope"])
    await env.orch.consume(bad, 1)
    assert (await client.post(f"/v1/dead-events/{bad.id}/replay", headers=auth("dashboard"))).status_code == 202

"""The dashboard against the real orchestrator app (in-process), dashboard token verified like in production."""

import hashlib
import importlib.util
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from dash_fakes import SERVICE_TOKEN, Env, FakeGraph, FakeKg, Upstream, make_app
from sandman_agent import TokenVerifier
from sandman_orchestrator.app import create_app

FAKES = Path(__file__).parents[2] / "orchestrator" / "tests" / "fakes.py"
spec = importlib.util.spec_from_file_location("orchestrator_fakes", FAKES)
assert spec and spec.loader
fakes = importlib.util.module_from_spec(spec)
sys.modules["orchestrator_fakes"] = fakes
spec.loader.exec_module(fakes)


@pytest.fixture
async def real(tmp_path: Path) -> AsyncIterator[tuple[Env, Any]]:
    contracts = fakes.Contracts(fakes.DEFAULT_CONTRACTS_DIR)
    bus = fakes.Bus(contracts, "fake-agent")
    orch_env = fakes.Env(
        None, {s: fakes.FakeSkill(s) for s in fakes.CARDS}, fakes.Router(), bus, contracts, tmp_path / "o.db"
    )
    for skill in fakes.CARDS:
        orch_env.restart_agent(skill)
    orch_env.new_orchestrator(orch_env.path)
    verifier = TokenVerifier({"dashboard": hashlib.sha256(SERVICE_TOKEN.encode()).hexdigest()})
    orchestrator_app = create_app(orch_env.orch, verifier)
    orchestrator_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=orchestrator_app), base_url="http://orchestrator"
    )

    upstream = Upstream()

    async def hook(request: httpx.Request) -> httpx.Response | None:
        if (
            request.url.host == "orchestrator"
            and request.url.path.startswith("/v1/")
            and request.url.path != "/v1/status"
        ):
            return await orchestrator_http.request(
                request.method,
                request.url.path,
                params=request.url.params,
                headers={"authorization": request.headers["authorization"]},
            )
        return None

    upstream.hook = hook
    app, upstream_http = make_app(upstream, FakeKg(), FakeGraph())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dashboard") as client:
        yield Env(client, app, FakeKg(), FakeGraph(), upstream), orch_env
    await upstream_http.aclose()
    await orchestrator_http.aclose()
    for s in orch_env.skills.values():
        s.release.set()
    orch_env.orch.close()


async def test_cancel_running_workflow_and_replay_dead_event(real: tuple[Env, Any]) -> None:
    dash, orch_env = real
    orch_env.skills[fakes.DETECT].script = ["hang"]
    wf = await orch_env.orch.trigger("detect", "mini", {}, "user:alice")
    await orch_env.until(lambda: any(s["status"] == "RUNNING" for s in orch_env.steps()))
    await dash.login()

    listing = (await dash.http.get("/workflows")).text
    assert wf in listing and "RUNNING" in listing
    token = await dash.csrf(f"/workflows/{wf}")
    resp = await dash.http.post(f"/workflows/{wf}/cancel", data={"csrf": token})
    assert resp.status_code == 200 and "cancel requested" in resp.text and "CANCELED" in resp.text
    assert orch_env.orch.get_workflow(wf)["status"] == "CANCELED"
    assert {s["status"] for s in orch_env.steps()} == {"CANCELED"}

    again = await dash.http.post(f"/workflows/{wf}/cancel", data={"csrf": token})
    assert "no pending or running steps" in again.text

    bad = fakes.ingestion(created=["nope"])
    await orch_env.orch.consume(bad, 1)
    assert bad.id in (await dash.http.get("/workflows")).text
    done = await dash.http.post("/dead-events/replay", data={"eventId": bad.id, "csrf": token})
    assert "replay requested" in done.text
    missing = await dash.http.post("/dead-events/replay", data={"eventId": "missing", "csrf": token})
    assert "replay failed: not found" in missing.text

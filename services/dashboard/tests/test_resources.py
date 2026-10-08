from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from dash_fakes import Env, FakeGraph, FakeKg, Upstream, make_app
from sandman_dashboard.resources import parse

GIB = 1024**3


def container(service: str, project: str, working_set: int, limit: int, cpu_ns: tuple[int, int]) -> dict[str, Any]:
    return {
        "spec": {
            "labels": {"com.docker.compose.service": service, "com.docker.compose.project": project},
            "memory": {"limit": limit},
        },
        "stats": [
            {"timestamp": "2026-10-09T10:00:00Z", "cpu": {"usage": {"total": cpu_ns[0]}}, "memory": {"working_set": 1}},
            {
                "timestamp": "2026-10-09T10:00:10Z",
                "cpu": {"usage": {"total": cpu_ns[1]}},
                "memory": {"working_set": working_set},
            },
        ],
    }


CADVISOR = {
    "/docker/a": container("orchestrator", "sandman", 410 * 1024**2, 512 * 1024**2, (0, 2_000_000_000)),
    "/docker/b": container("memgraph", "sandman", 3 * GIB, 10 * GIB, (5, 5)),
    "/docker/c": container("cadvisor", "sandman", 100 * 1024**2, 256 * 1024**2, (0, 1_000_000_000)),
    "/docker/d": container("other-service", "foreign", GIB, GIB, (0, 0)),
    "/docker/e": container("tlr-agent", "sandman", GIB, 2**63 - 4096, (0, 0)),
    "/system.slice/x": {"spec": {"labels": {}}, "stats": []},
}


def test_parse_maps_compose_services_limits_and_cpu() -> None:
    usage = {u.service: u for u in parse(CADVISOR)}
    assert set(usage) == {"orchestrator", "memgraph", "cadvisor", "tlr-agent"}
    orch = usage["orchestrator"]
    assert orch.memory == 410 * 1024**2 and orch.limit == 512 * 1024**2
    assert orch.ratio == pytest.approx(410 / 512) and orch.warn and orch.cpu == pytest.approx(0.2)
    assert usage["memgraph"].ratio == pytest.approx(0.3) and not usage["memgraph"].warn
    assert usage["memgraph"].cpu == 0.0
    assert usage["tlr-agent"].limit is None and usage["tlr-agent"].ratio is None and not usage["tlr-agent"].warn


def test_parse_garbage() -> None:
    assert parse([]) == [] and parse({"/x": 5, "/y": {"spec": None, "stats": None}}) == []


@pytest.fixture
async def cadvisor_env() -> AsyncIterator[tuple[Env, dict[str, Any]]]:
    state: dict[str, Any] = {"doc": CADVISOR, "down": False}
    upstream, kg, graph = Upstream(), FakeKg(), FakeGraph()

    async def hook(request: httpx.Request) -> httpx.Response | None:
        if request.url.host != "cadvisor":
            return None
        if state["down"]:
            raise httpx.ConnectError("refused", request=request)
        assert request.method == "POST" and request.url.path == "/api/v1.3/docker/"
        return httpx.Response(200, json=state["doc"])

    upstream.hook = hook
    app, upstream_http = make_app(upstream, kg, graph, cadvisor_url="http://cadvisor:8080")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dashboard") as client:
        yield Env(client, app, kg, graph, upstream), state
    await upstream_http.aclose()


async def test_resources_page_with_cadvisor(cadvisor_env: tuple[Env, dict[str, Any]]) -> None:
    env, _ = cadvisor_env
    await env.login()
    await env.app.state.poller.poll_once()
    text = (await env.http.get("/resources")).text
    for service in ("orchestrator", "memgraph", "cadvisor", "tlr-agent"):
        assert f"<td>{service}</td>" in text
    assert "other-service" not in text
    assert '<tr class="warn">\n  <td>orchestrator</td>' in text and "80 %" in text and "410 MiB" in text
    assert "0.20" in text and "not active" not in text
    assert "2.00MiB" in text


async def test_resources_page_without_cadvisor(cadvisor_env: tuple[Env, dict[str, Any]]) -> None:
    env, state = cadvisor_env
    state["down"] = True
    await env.login()
    resp = await env.http.get("/resources")
    assert resp.status_code == 200 and "profile <code>resources</code> not active" in resp.text
    assert (await env.http.get("/healthz")).json() == {"status": "ok"}
    assert (await env.http.get("/readyz")).status_code == 200


async def test_resources_requires_session(cadvisor_env: tuple[Env, dict[str, Any]]) -> None:
    assert (await cadvisor_env[0].http.get("/resources")).status_code == 303

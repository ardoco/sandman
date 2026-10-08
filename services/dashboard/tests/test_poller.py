import asyncio
import time

import httpx
import pytest
from helpers import SERVICE_TOKEN, TARGETS, FakeKg, Upstream
from sandman_dashboard import poller as poller_module
from sandman_dashboard.gateway import GatewayClient
from sandman_dashboard.poller import Poller, nats_details, parse_targets


def make(upstream: Upstream, kg: FakeKg | None = None, targets: dict[str, str] | None = None) -> Poller:
    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    return Poller(
        targets or TARGETS,
        client,
        SERVICE_TOKEN,
        kg=kg,  # type: ignore[arg-type]
        nats_monitor_url="http://nats:8222",
        gateway=GatewayClient("http://gateway:8080", "pw", client),
    )


async def test_all_up_with_infrastructure() -> None:
    poller = make(Upstream(), FakeKg())
    await poller.poll_once()
    health = {n: e.health for n, e in poller.entries.items()}
    assert health == {"orchestrator": "up", "tlr-agent": "up", "memgraph": "up", "nats": "up", "llm-gateway": "up"}
    assert poller.entries["orchestrator"].state == "idle" and poller.entries["orchestrator"].version == "1.0"
    assert poller.entries["memgraph"].details == {
        "nodes": 12,
        "edges": 30,
        "memory": "2.00MiB",
        "memoryLimit": "8.00GiB",
    }
    assert poller.entries["nats"].details == {
        "stream": {"messages": 7, "bytes": 700},
        "consumer": {"pending": 1, "ackPending": 2, "redelivered": 3},
    }
    assert poller.gateway_snapshot is not None and poller.gateway_snapshot.budgets


async def test_sends_service_token_only_to_status() -> None:
    seen: dict[str, str | None] = {}
    upstream = Upstream()

    async def hook(request: httpx.Request) -> None:
        seen[request.url.path] = request.headers.get("authorization")

    upstream.hook = hook
    await make(upstream, targets={"orchestrator": "http://orchestrator:8000"}).poll_once()
    assert seen["/v1/status"] == f"Bearer {SERVICE_TOKEN}" and seen["/readyz"] is None


async def test_target_that_stops_answering_is_down_within_one_poll() -> None:
    upstream = Upstream()
    poller = make(upstream)
    await poller.poll_once()
    assert poller.entries["tlr-agent"].health == "up"
    first_ok = poller.entries["tlr-agent"].last_ok_at
    upstream.down.add("tlr-agent")
    await poller.poll_once()
    entry = poller.entries["tlr-agent"]
    assert entry.health == "down" and entry.reason == "connection error" and entry.last_ok_at == first_ok
    assert poller.entries["orchestrator"].health == "up"


async def test_readyz_ok_and_status_unauthorized_is_degraded() -> None:
    upstream = Upstream()
    upstream.status_code["tlr-agent"] = 401
    poller = make(upstream)
    await poller.poll_once()
    assert poller.entries["tlr-agent"].health == "degraded"
    assert poller.entries["tlr-agent"].state is None


async def test_invalid_status_document_is_degraded() -> None:
    upstream = Upstream()

    async def hook(request: httpx.Request) -> httpx.Response | None:
        if request.url.path == "/v1/status":
            return httpx.Response(200, json={"state": "sleeping", "activeTasks": -1})
        return None

    upstream.hook = hook
    poller = make(upstream, targets={"orchestrator": "http://orchestrator:8000"})
    await poller.poll_once()
    assert poller.entries["orchestrator"].health == "degraded"


async def test_not_ready_is_down() -> None:
    upstream = Upstream()

    async def hook(request: httpx.Request) -> httpx.Response | None:
        return httpx.Response(503) if request.url.path == "/readyz" else None

    upstream.hook = hook
    poller = make(upstream, targets={"orchestrator": "http://orchestrator:8000"})
    await poller.poll_once()
    assert poller.entries["orchestrator"].health == "down" and poller.entries["orchestrator"].reason == "not ready"


async def test_slow_target_does_not_delay_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(poller_module, "TIMEOUT", 0.3)
    upstream = Upstream()

    async def hook(request: httpx.Request) -> httpx.Response | None:
        if request.url.host == "tlr-agent":
            await asyncio.sleep(30)
        return None

    upstream.hook = hook
    poller = make(upstream)
    started = time.monotonic()
    await poller.poll_once()
    assert time.monotonic() - started < 1.5
    assert poller.entries["tlr-agent"].health == "down" and poller.entries["tlr-agent"].reason == "timeout"
    assert poller.entries["orchestrator"].health == "up"


async def test_infrastructure_down_and_error_bodies_not_kept() -> None:
    upstream = Upstream()
    upstream.gateway_status = 500
    kg = FakeKg()
    kg.up = False
    poller = make(upstream, kg)
    await poller.poll_once()
    assert poller.entries["memgraph"].health == "down"
    assert poller.entries["llm-gateway"].health == "up" and poller.gateway_snapshot is None
    assert "LEAK" not in repr(poller.entries)


async def test_loop_survives_errors_and_stops() -> None:
    poller = make(Upstream(), FakeKg())
    poller.interval = 0.01
    poller.start()
    await asyncio.sleep(0.1)
    await poller.stop()
    assert "orchestrator" in poller.entries


def test_parse_targets() -> None:
    assert parse_targets("a=http://a:1/, b=http://b:2") == {"a": "http://a:1", "b": "http://b:2"}
    assert parse_targets("") == {}
    with pytest.raises(ValueError):
        parse_targets("broken")


def test_nats_details_ignores_other_shapes() -> None:
    assert nats_details({}) == {} and nats_details("x") == {}
    assert nats_details({"account_details": [{"stream_detail": [{"name": "OTHER"}]}]}) == {}

import asyncio
import json
from collections.abc import AsyncIterator, Iterator

import httpx
import nats
import pytest
from dash_fakes import Env, FakeGraph, FakeKg, Upstream, make_app
from sandman_agent import ensure_stream
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_agent.contracts import Contracts
from sandman_agent.events import EventConsumer, EventPublisher
from sandman_dashboard.events_view import MAX_PAYLOAD, EventViewer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy


def envelope(n: int, type_: str = "sandman.ingestion.completed", project: str = "demo", **data: object) -> bytes:
    return json.dumps(
        {
            "specversion": "1.0",
            "id": f"evt-{n}",
            "source": "sandman/test",
            "type": type_,
            "subject": project,
            "time": "2026-10-08T10:00:00Z",
            "correlationid": f"corr-{n}",
            "data": {"projectId": project, **data},
        }
    ).encode()


@pytest.fixture
async def viewer_env() -> AsyncIterator[tuple[Env, EventViewer]]:
    viewer = EventViewer("nats://unused", size=3)
    upstream, kg, graph = Upstream(), FakeKg(), FakeGraph()
    app, upstream_http = make_app(upstream, kg, graph, events=viewer)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dashboard") as client:
        yield Env(client, app, kg, graph, upstream), viewer
    await upstream_http.aclose()


async def test_list_filters_and_ring_bound(viewer_env: tuple[Env, EventViewer]) -> None:
    env, viewer = viewer_env
    await env.login()
    await env.app.state.poller.poll_once()
    viewer.add(1, envelope(1, project="old"))
    viewer.add(2, envelope(2, "sandman.tracelinks.updated"))
    viewer.add(3, envelope(3, project="other"))
    viewer.add(4, envelope(4))
    text = (await env.http.get("/events")).text
    assert text.index("evt-4") < text.index("evt-3") < text.index("evt-2") and "evt-1" not in text
    assert "corr-4" in text and "sandman/test" in text and "7 messages" in text and "ack pending 2" in text
    only = (await env.http.get("/fragments/events", params={"type": "sandman.tracelinks.updated"})).text
    assert "evt-2" in only and "evt-4" not in only
    only = (await env.http.get("/events", params={"project": "other"})).text
    assert "evt-3" in only and "evt-2" not in only.split("<tbody>")[1]


async def test_payload_is_escaped_and_truncated(viewer_env: tuple[Env, EventViewer]) -> None:
    env, viewer = viewer_env
    await env.login()
    viewer.add(1, envelope(1, note="<script>alert(1)</script>"))
    viewer.add(2, envelope(2, big="x" * (MAX_PAYLOAD * 2)))
    viewer.add(3, b"<script>alert(2)</script>")
    first = (await env.http.get("/events/1")).text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in first and "<script>alert" not in first
    big = (await env.http.get("/events/2")).text
    assert "... truncated" in big and len(big) < MAX_PAYLOAD + 5000
    bad = await env.http.get("/events/3")
    assert "(invalid)" in (await env.http.get("/events")).text and "&lt;script&gt;alert(2)" in bad.text
    assert (await env.http.get("/events/99")).status_code == 404


async def test_requires_session(viewer_env: tuple[Env, EventViewer]) -> None:
    env, _ = viewer_env
    assert (await env.http.get("/events")).status_code == 303
    assert (await env.http.get("/events/1")).status_code == 303
    assert (await env.http.get("/fragments/events")).status_code == 401


@pytest.fixture(scope="module")
def nats_url() -> Iterator[str]:
    try:
        container = DockerContainer("nats:2.15.0-alpine").with_command("-js").with_exposed_ports(4222)
        container.waiting_for(LogMessageWaitStrategy("Server is ready"))
        container.start()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Docker unavailable: {e}")
    try:
        yield f"nats://{container.get_container_host_ip()}:{container.get_exposed_port(4222)}"
    finally:
        container.stop()


async def test_viewer_sees_published_events_and_leaves_durable_consumer_untouched(nats_url: str) -> None:
    nc = await nats.connect(nats_url)
    js = nc.jetstream()
    await ensure_stream(js)
    await js.add_consumer("SANDMAN", config=EventConsumer(js, "orchestrator").config())
    publisher = EventPublisher(js, Contracts(DEFAULT_CONTRACTS_DIR), "test")
    data = {
        "projectId": "demo",
        "ingestionId": "ing_" + "a" * 32,
        "seq": 1,
        "mode": "full",
        "actor": "user:alice",
        "createdVersionIds": [],
        "reactivatedVersionIds": [],
        "deletedArtifactIds": [],
        "unchangedCount": 0,
    }
    first = await publisher.publish("sandman.ingestion.completed", "demo", data)
    before = await js.consumer_info("SANDMAN", "orchestrator")
    viewer = EventViewer(nats_url)
    viewer.start()
    try:
        async with asyncio.timeout(15):
            while not viewer.connected or not viewer.find(1):
                await asyncio.sleep(0.1)
            later = await publisher.publish("sandman.ingestion.completed", "demo", {**data, "seq": 2})
            while viewer.find(2) is None:
                await asyncio.sleep(0.1)
        assert viewer.listing()[0].id == later.id and viewer.listing()[1].id == first.id
        assert viewer.listing(project="nope") == []
        after = await js.consumer_info("SANDMAN", "orchestrator")
        assert (after.delivered.stream_seq, after.ack_floor.stream_seq) == (
            before.delivered.stream_seq,
            before.ack_floor.stream_seq,
        )
        assert after.num_pending == 2
        assert [c.config.durable_name for c in await js.consumers_info("SANDMAN") if c.config.durable_name] == [
            "orchestrator"
        ]
    finally:
        await viewer.stop()
        await nc.close()

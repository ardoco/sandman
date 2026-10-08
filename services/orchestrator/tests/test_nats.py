"""Consumer against a real NATS JetStream (Testcontainers; skipped without Docker)."""

import asyncio
import contextlib

import nats
import pytest
from fakes import Env, ingestion, vid
from sandman_agent import ensure_stream
from sandman_agent.events import EventPublisher
from sandman_orchestrator.app import consume
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy


async def test_event_delivered_once_through_jetstream(env: Env) -> None:
    try:
        container = DockerContainer("nats:2.10").with_command("-js").with_exposed_ports(4222)
        container.waiting_for(LogMessageWaitStrategy("Server is ready"))
        container.start()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Docker unavailable: {e}")
    try:
        nc = await nats.connect(f"nats://{container.get_container_host_ip()}:{container.get_exposed_port(4222)}")
        js = nc.jetstream()
        await ensure_stream(js)
        task = asyncio.create_task(consume(js, env.orch.consume))
        e = ingestion(created=[vid(1)])
        await EventPublisher(js, env.contracts, "test").publish(e.type, e.subject, e.data, event_id=e.id)
        await EventPublisher(js, env.contracts, "test").publish(e.type, e.subject, e.data, event_id=e.id)
        async with asyncio.timeout(10):
            while not env.steps():
                await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert len(env.steps()) == 1
        await nc.close()
    finally:
        container.stop()

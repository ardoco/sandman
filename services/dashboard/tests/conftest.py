"""Fixtures: dashboard app with fake upstreams, fake KG client and fake graph service."""

import asyncio
import sys
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from neo4j import AsyncDriver, AsyncGraphDatabase
from testcontainers.core.container import DockerContainer

sys.path.insert(0, str(Path(__file__).parent))

from dash_fakes import Env, FakeGraph, FakeKg, Upstream, make_app  # noqa: E402


@pytest.fixture
async def env() -> AsyncIterator[Env]:
    upstream, kg, graph = Upstream(), FakeKg(), FakeGraph()
    app, upstream_http = make_app(upstream, kg, graph)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dashboard") as client:
        yield Env(client, app, kg, graph, upstream)
    await upstream_http.aclose()


def _docker_available() -> bool:
    try:
        import docker  # type: ignore[import-untyped]

        docker.from_env().ping()
        return True
    except Exception:  # noqa: BLE001 - no daemon reachable
        return False


DOCKER = _docker_available()


TRAVERSE_IMAGE = "truespar/traverse@sha256:e39b5739a525f2b955dcac0e5747039845f3a5c3cd89397aec923312d5d69965"


@pytest.fixture(scope="session")
def traverse() -> Iterator[str]:
    """Bolt URL of a Traverse 0.9.1 container (skipped without Docker)."""
    if not DOCKER:
        pytest.skip("no Docker daemon reachable")
    with (
        DockerContainer(TRAVERSE_IMAGE, platform="linux/amd64")
        .with_command(["--data", "/data", "--query-memory-limit", "256MB"])
        .with_exposed_ports(7690)
    ) as container:
        url = f"bolt://{container.get_container_host_ip()}:{container.get_exposed_port(7690)}"

        async def ping() -> None:
            driver = AsyncGraphDatabase.driver(url)
            try:
                await driver.verify_connectivity()
            finally:
                await driver.close()

        deadline = time.monotonic() + 60
        while True:
            try:
                asyncio.run(ping())
                break
            except Exception:  # noqa: BLE001 - not ready yet
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        yield url


@pytest.fixture
async def driver(traverse: str) -> AsyncIterator[AsyncDriver]:
    drv = AsyncGraphDatabase.driver(traverse)
    async with drv.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")
    yield drv
    await drv.close()

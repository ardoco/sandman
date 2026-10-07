"""Fixtures: app with fake KG reader and fake owner services; users and tokens are created in the store."""

import asyncio
import hashlib
import shutil
import sys
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from argon2 import PasswordHasher
from neo4j import AsyncGraphDatabase
from sandman_agent import Settings
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_query.app import create_app
from sandman_query.store import UserStore
from testcontainers.core.container import DockerContainer

sys.path.insert(0, str(Path(__file__).parent))

from helpers import TOKENS, FakeOwner, FakeReader  # noqa: E402

ADMIN_PASSWORD = "bootstrap-password"
FAST = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
OWNERS = {"extraction-agent": "http://ext", "inconsistency-agent": "http://inc", "orchestrator": "http://orch"}


def _docker_available() -> bool:
    try:
        import docker  # type: ignore[import-untyped]

        docker.from_env().ping()
        return True
    except Exception:  # noqa: BLE001 - no daemon reachable
        return False


DOCKER = _docker_available()


@pytest.fixture(scope="session")
def memgraph() -> Iterator[str]:
    """Bolt URL of a Memgraph 3.13.1 container (skipped without Docker)."""
    if not DOCKER:
        pytest.skip("no Docker daemon reachable")
    with (
        DockerContainer("memgraph/memgraph:3.13.1").with_command("--telemetry-enabled=false").with_exposed_ports(7687)
    ) as container:
        url = f"bolt://{container.get_container_host_ip()}:{container.get_exposed_port(7687)}"

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


@dataclass
class Env:
    http: httpx.AsyncClient
    app: Any
    store: UserStore
    owner: FakeOwner
    reader: FakeReader

    def user(self, name: str, roles: dict[str, str] | None = None, admin: bool = False) -> None:
        self.store.create_user(name, "password-" + name * 2, ["admin"] if admin else [], must_change=False)
        for project, role in (roles or {}).items():
            self.store.set_member(project, name, role)

    def token(self, name: str, project: str | None = None, max_role: str | None = None) -> dict[str, str]:
        _, secret = self.store.create_token(name, "t", project, max_role, None)
        return {"authorization": f"Bearer {secret}"}


def service_headers(name: str) -> dict[str, str]:
    return {"authorization": f"Bearer {TOKENS[name]}"}


async def build_env(tmp_path: Path, kg_uri: str | None = None) -> AsyncIterator[Env]:
    """App with a fake reader, or with the real one when kg_uri is given."""
    (tmp_path / "query").mkdir()
    (tmp_path / "query" / "plugins.yaml").write_text("stub: ['alpha']\nother: '*'\n")
    shutil.copy(DEFAULT_CONTRACTS_DIR.parent / "config" / "query" / "intents.yaml", tmp_path / "query")
    settings = Settings(
        service_name="query-service",
        service_token="qs-token",
        kg_uri=kg_uri,
        service_verify={n: hashlib.sha256(t.encode()).hexdigest() for n, t in TOKENS.items()},
        config_dir=tmp_path,
    )
    owner, reader = FakeOwner(), FakeReader()
    app = create_app(
        settings,
        admin_password=ADMIN_PASSWORD,
        owner_urls=OWNERS,
        owner_transport=httpx.ASGITransport(app=owner.app),
        reader=None if kg_uri else reader,  # type: ignore[arg-type]
        hasher=FAST,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://query") as http:
            yield Env(http, app, app.state.service.store, owner, reader)


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[Env]:
    async for e in build_env(tmp_path):
        yield e

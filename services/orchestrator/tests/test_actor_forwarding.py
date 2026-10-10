"""R-005: X-Sandman-Actor is accepted only from the query-service, others get 403."""

from collections.abc import AsyncIterator

import httpx
import pytest
from fakes import TOKENS, VERIFY, Env
from sandman_agent import TokenVerifier
from sandman_orchestrator.app import create_app


@pytest.fixture
async def client(env: Env) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(env.orch, TokenVerifier(VERIFY))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://orch") as c:
        yield c


@pytest.mark.parametrize("caller", ["dashboard", "tlr-agent", "orchestrator"])
async def test_actor_header_from_non_forwarder_is_forbidden(client: httpx.AsyncClient, caller: str) -> None:
    headers = {"authorization": f"Bearer {TOKENS[caller]}", "x-sandman-actor": "user:mallory"}
    resp = await client.post("/v1/workflows", json={"type": "detect", "projectId": "mini"}, headers=headers)
    assert resp.status_code == 403


async def test_actor_header_from_query_service_is_accepted(client: httpx.AsyncClient) -> None:
    headers = {"authorization": f"Bearer {TOKENS['query-service']}", "x-sandman-actor": "user:alice"}
    resp = await client.post("/v1/workflows", json={"type": "detect", "projectId": "mini"}, headers=headers)
    assert resp.status_code == 202 and resp.json()["trigger"]["actor"] == "user:alice"

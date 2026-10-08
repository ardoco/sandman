from collections.abc import AsyncIterator

import httpx
import pytest
from dash_fakes import TARGETS, Env, FakeGraph, FakeKg, Upstream, make_app


@pytest.fixture
async def replay_env() -> AsyncIterator[Env]:
    upstream, kg, graph = Upstream(), FakeKg(), FakeGraph()
    app, upstream_http = make_app(upstream, kg, graph, targets={**TARGETS, "llm-replay": "http://llm-replay:8000"})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dashboard") as client:
        yield Env(client, app, kg, graph, upstream)
    await upstream_http.aclose()


async def test_live_mode(replay_env: Env) -> None:
    env = replay_env
    await env.login()
    await env.app.state.poller.poll_once()
    text = (await env.http.get("/experiments")).text
    assert "<th>Mode</th><td>live</td>" in text and "every 5s" in text
    assert '<tr class="warn">' not in text


async def test_replay_misses_are_highlighted(replay_env: Env) -> None:
    env = replay_env
    await env.login()
    env.upstream.details["llm-replay"] = {
        "mode": "replay",
        "experiment": "<b>exp</b>",
        "entries": 12,
        "hits": 30,
        "misses": 2,
        "recorded": 0,
        "forwarded": 0,
    }
    await env.app.state.poller.poll_once()
    text = (await env.http.get("/fragments/experiments")).text
    assert '<tr class="warn"><th>Misses</th><td>2' in text
    assert "<th>Cassette entries</th><td>12</td>" in text and "<th>Hits</th><td>30</td>" in text
    assert "&lt;b&gt;exp&lt;/b&gt;" in text and "<b>exp</b>" not in text


async def test_down(replay_env: Env) -> None:
    env = replay_env
    await env.login()
    env.upstream.down.add("llm-replay")
    await env.app.state.poller.poll_once()
    assert "llm-replay down" in (await env.http.get("/experiments")).text


async def test_requires_session(replay_env: Env) -> None:
    assert (await replay_env.http.get("/experiments")).status_code == 303
    assert (await replay_env.http.get("/fragments/experiments")).status_code == 401

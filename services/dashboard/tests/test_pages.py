import logging
import re
from pathlib import Path

import pytest
from helpers import SECRETS, Env

PAGES = ["/", "/graph", "/gateway", "/fragments/services", "/fragments/gateway"]
APIS = [
    "/api/services",
    "/api/gateway",
    "/api/graph/projects",
    "/api/graph/start",
    "/api/graph/search?q=x",
    "/api/graph/expand?id=1",
    "/api/graph/path?source=1&target=2",
]
SRC = Path(__file__).parents[1] / "src" / "sandman_dashboard"


async def poll(env: Env) -> None:
    await env.app.state.poller.poll_once()


async def test_services_page_shows_all_targets_and_infrastructure(env: Env) -> None:
    await env.login()
    await poll(env)
    text = (await env.http.get("/")).text
    for name in ("orchestrator", "tlr-agent", "memgraph", "nats", "llm-gateway"):
        assert f"<td>{name}</td>" in text
    assert text.count('class="health-up"') == 5
    assert "ollama" not in text
    assert 'hx-trigger="every 5s"' in text
    assert "<dd>12</dd>" in text and "2.00MiB" in text and "idle" in text


async def test_services_page_before_first_poll(env: Env) -> None:
    await env.login()
    assert "Waiting for the first poll" in (await env.http.get("/")).text


async def test_down_service_is_shown_down(env: Env) -> None:
    await env.login()
    env.upstream.down.add("tlr-agent")
    await poll(env)
    text = (await env.http.get("/fragments/services")).text
    assert re.search(r'<td>tlr-agent</td>\s*<td class="health-down">down \(connection error\)</td>', text)


async def test_gateway_page_totals_budgets_and_na(env: Env) -> None:
    await env.login()
    await poll(env)
    text = (await env.http.get("/gateway")).text
    assert "gpt-4.1-nano" in text and "1,200" in text and "$0.0006" in text
    assert '<tr class="warn">' in text and "85 %" in text and "at least 80 %" in text
    assert "n/a" in text


async def test_gateway_page_when_unavailable(env: Env) -> None:
    await env.login()
    env.upstream.gateway_status = 500
    await poll(env)
    assert "Gateway statistics unavailable" in (await env.http.get("/gateway")).text
    assert (await env.http.get("/api/gateway")).json() == {"available": False, "rows": [], "budgets": None}


async def test_xss_details_and_project_names_are_escaped(env: Env) -> None:
    await env.login()
    env.upstream.details["orchestrator"] = {"<script>alert(1)</script>": "<script>alert(2)</script>", "n": {"x": "<b>"}}
    await poll(env)
    for path in ("/", "/fragments/services", "/graph"):
        text = (await env.http.get(path)).text
        assert "<script>alert" not in text and "<b>" not in text
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in (await env.http.get("/")).text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in (await env.http.get("/graph")).text
    assert (await env.http.get("/api/graph/projects")).headers["content-type"].startswith("application/json")


def test_templates_have_no_inline_scripts_or_handlers() -> None:
    for path in (SRC / "templates").glob("*.html"):
        text = path.read_text()
        assert not re.search(r"<script(?![^>]*\bsrc=)", text), path
        assert not re.search(r"\son[a-z]+\s*=", text), path
        assert "javascript:" not in text, path
    assert "innerHTML" not in (SRC / "static" / "graph.js").read_text()


def test_vendored_libraries_have_licenses() -> None:
    static = SRC / "static"
    assert (static / "cytoscape-LICENSE.txt").read_text().startswith("Copyright (c) 2016-")
    assert b'version="3.34.' in (static / "cytoscape.min.js").read_bytes()
    assert (static / "htmx-LICENSE.txt").exists()


@pytest.mark.parametrize("failure", ["down", "gateway-401", "gateway-500", "status-500"])
async def test_no_secret_in_responses_or_logs(env: Env, caplog: pytest.LogCaptureFixture, failure: str) -> None:
    caplog.set_level(logging.DEBUG)
    if failure == "down":
        env.upstream.down.update({"orchestrator", "tlr-agent", "nats", "gateway"})
        env.kg.up = False
    elif failure.startswith("gateway"):
        env.upstream.gateway_status = int(failure.split("-")[1])
    else:
        env.upstream.status_code.update({"orchestrator": 500, "tlr-agent": 401})
    login = await env.login()
    await poll(env)
    bodies = [login.text, str(login.headers)]
    for path in [*PAGES, *APIS]:
        resp = await env.http.get(path)
        bodies.append(resp.text + str(resp.headers))
    token = await env.csrf()
    cypher = await env.http.post("/api/cypher", json={"cypher": "boom"}, headers={"x-csrf-token": token})
    bodies.append(cypher.text)
    bodies.append((await env.http.get("/readyz")).text)
    bodies.append((await env.login("wrong-password")).text)
    blob = "\n".join(bodies) + "\n".join(f"{r.getMessage()} {r.__dict__.get('fields', '')}" for r in caplog.records)
    for secret in SECRETS:
        assert secret not in blob
    assert "upstream body" not in blob and "error body" not in blob


async def test_unknown_route_and_error_page(env: Env) -> None:
    resp = await env.http.get("/nope")
    assert resp.status_code == 404 and "Error 404" in resp.text

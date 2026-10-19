"""Console graph explorer: page, access control, CSRF and hidden labels (no graph database)."""

import re
from typing import Any

from conftest import Env
from sandman_query.graph_console import HIDDEN_LABELS, hidden_in
from test_console import logged_in


class StubGraph:
    labels = ["Requirement", "Run", "Ingestion"]
    rel_types = ["HAS_ELEMENT"]

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def artifacts(self, project: str) -> list[dict[str, str]]:
        return [{"id": "art_1", "path": "<b>req.md</b>"}]

    async def start_nodes(self, project: str, hidden: list[str], *args: Any) -> dict[str, Any]:
        self.calls.append(("start", hidden))
        return {"elements": [], "truncated": False}

    async def query(self, project: str, statement: str, hidden: list[str]) -> dict[str, Any]:
        self.calls.append(("query", hidden))
        return {"columns": [], "rows": [], "truncated": False, "elements": []}


def stub(env: Env) -> StubGraph:
    graph = StubGraph()
    env.app.state.service.graph = graph
    return graph


async def test_page_needs_a_session_and_a_role(env: Env) -> None:
    assert (await env.http.get("/console/projects/alpha/graph")).status_code in (303, 401, 403)
    await logged_in(env, "mia", {"beta": "viewer"})
    assert (await env.http.get("/console/projects/alpha/graph")).status_code == 403
    assert (await env.http.get("/console/projects/alpha/graph/start")).status_code == 403


async def test_page_has_no_inline_script_and_escapes_values(env: Env) -> None:
    stub(env)
    await logged_in(env, "mia", {"alpha": "viewer"})
    page = await env.http.get("/console/projects/alpha/graph")
    assert page.status_code == 200
    assert page.headers["content-security-policy"] == "default-src 'self'"
    assert "&lt;b&gt;req.md&lt;/b&gt;" in page.text and "<b>req.md</b>" not in page.text
    assert not re.search(r"<script(?![^>]*src=)", page.text)
    assert "Run" not in re.findall(r'class="filter-label" value="([^"]+)"', page.text)


async def test_projects_page_links_to_the_graph_without_boost(env: Env) -> None:
    await logged_in(env, "mia", {"alpha": "viewer"})
    page = await env.http.get("/console/projects")
    assert 'href="/console/projects/alpha/graph" hx-boost="false"' in page.text


async def test_assets_are_served(env: Env) -> None:
    for name in ("graph.js", "graph.css", "cytoscape.min.js"):
        assert (await env.http.get(f"/static/{name}")).status_code == 200


async def test_hidden_labels_depend_on_the_admin_flag(env: Env) -> None:
    graph = stub(env)
    await logged_in(env, "mia", {"alpha": "viewer"})
    assert (await env.http.get("/console/projects/alpha/graph/start")).status_code == 200
    assert graph.calls == [("start", list(HIDDEN_LABELS))]
    await logged_in(env, "root", {}, admin=True)
    await env.http.get("/console/projects/alpha/graph/start")
    assert graph.calls[-1] == ("start", [])


async def test_query_needs_the_csrf_token_and_a_statement(env: Env) -> None:
    graph = stub(env)
    csrf = await logged_in(env, "mia", {"alpha": "viewer"})
    url = "/console/projects/alpha/graph/query"
    assert (await env.http.post(url, json={"cypher": "RETURN 1"})).status_code == 403
    assert (await env.http.post(url, json={"cypher": "RETURN 1"}, headers={"x-csrf-token": "x"})).status_code == 403
    assert (await env.http.post(url, json={"cypher": " "}, headers={"x-csrf-token": csrf})).status_code == 400
    assert (await env.http.post(url, content=b"x", headers={"x-csrf-token": csrf})).status_code == 400
    assert graph.calls == []
    assert (await env.http.post(url, json={"cypher": "RETURN 1"}, headers={"x-csrf-token": csrf})).status_code == 200
    assert graph.calls == [("query", list(HIDDEN_LABELS))]


async def test_missing_graph_answers_503_and_the_page_shows_the_reason(env: Env) -> None:
    await logged_in(env, "mia", {"alpha": "viewer"})
    resp = await env.http.get("/console/projects/alpha/graph/start")
    assert resp.status_code == 503 and "unavailable" in resp.json()["error"]
    page = await env.http.get("/console/projects/alpha/graph")
    assert page.status_code == 200 and "unavailable" in page.text and "graph.js" not in page.text


def test_hidden_in_finds_nested_labels() -> None:
    assert hidden_in([{"a": {"_labels": ["Run"]}}], ["Run"])
    assert not hidden_in([{"_labels": ["Element"]}, 1, "Run"], ["Run"])

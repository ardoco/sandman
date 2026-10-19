"""Console graph explorer against Testcontainers Memgraph (skipped without Docker)."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from conftest import Env, build_env
from neo4j import AsyncGraphDatabase
from sandman_agent.ids import stable_id
from test_console import logged_in
from test_memgraph import seed


@pytest.fixture
async def mg(memgraph: str, tmp_path: Path) -> AsyncIterator[Env]:
    await seed(memgraph)
    async for env in build_env(tmp_path, memgraph):
        yield env


def node_id(project: str, name: str, prefix: str) -> str:
    return stable_id(prefix, project, name)


async def get(env: Env, project: str, route: str, **params: Any) -> Any:
    resp = await env.http.get(f"/console/projects/{project}/graph/{route}", params=params)
    assert resp.status_code in (200, 403), resp.text
    assert resp.status_code == 403 or "elements" in resp.json(), resp.text
    return resp


def nodes_of(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [e["data"] for e in body["elements"] if e["group"] == "nodes"]


def labels_of(body: dict[str, Any]) -> set[str]:
    return {label for n in nodes_of(body) for label in n["labels"]}


def by_key(body: dict[str, Any], name: str) -> dict[str, Any]:
    return next(n for n in nodes_of(body) if n["props"].get("name") == name)


async def test_start_is_scoped_and_hides_run_and_ingestion(mg: Env) -> None:
    await logged_in(mg, "mia", {"alpha": "viewer"})
    body = (await get(mg, "alpha", "start")).json()
    assert nodes_of(body) and "Ingestion" not in labels_of(body)
    assert {n["props"]["projectId"] for n in nodes_of(body) if "projectId" in n["props"]} == {"alpha"}


async def test_start_filters_label_artifact_and_state(mg: Env) -> None:
    await logged_in(mg, "mia", {"alpha": "viewer"})
    req = node_id("alpha", "req", "art")
    by_label = (await get(mg, "alpha", "start", labels="Requirement")).json()
    assert labels_of(by_label) == {"Element", "Requirement"}
    by_artifact = (await get(mg, "alpha", "start", labels="Requirement", artifact=req)).json()
    assert len(nodes_of(by_artifact)) == 4
    current = (await get(mg, "alpha", "start", labels="Requirement", artifact=req, state="current")).json()
    historic = (await get(mg, "alpha", "start", labels="Requirement", artifact=req, state="historic")).json()
    assert len(nodes_of(current)) == 3 and len(nodes_of(historic)) == 1
    by_text = (await get(mg, "alpha", "start", q="r1")).json()
    assert by_key(by_text, "R1")


async def test_expand_and_path_run_and_stay_in_project(mg: Env) -> None:
    await logged_in(mg, "mia", {"alpha": "viewer"})
    start = (await get(mg, "alpha", "start", labels=["ModelElement", "CodeUnit"])).json()
    c1, u1 = by_key(start, "C1")["nid"], by_key(start, "U1")["nid"]
    expanded = (await get(mg, "alpha", "expand", id=c1, hops=2)).json()
    assert {"C1", "U1"} <= {n["props"].get("name") for n in nodes_of(expanded)}
    assert "Ingestion" not in labels_of(expanded)
    found = (await get(mg, "alpha", "path", source=c1, target=u1)).json()
    assert found["found"] is True and len(nodes_of(found)) == 2


async def test_expand_of_a_foreign_project_node_is_empty(mg: Env) -> None:
    await logged_in(mg, "mia", {"alpha": "viewer", "beta": "viewer"})
    beta = (await get(mg, "beta", "start", labels="ModelElement")).json()
    foreign = nodes_of(beta)[0]["nid"]
    assert nodes_of((await get(mg, "alpha", "expand", id=foreign)).json()) == []


async def test_user_query_scoped_and_hidden_labels(mg: Env) -> None:
    csrf = await logged_in(mg, "mia", {"alpha": "viewer"})
    url, headers = "/console/projects/alpha/graph/query", {"x-csrf-token": csrf}
    ok = await mg.http.post(
        url, json={"cypher": "MATCH (n:Requirement {projectId: $projectId}) RETURN n"}, headers=headers
    )
    assert ok.status_code == 200 and len(nodes_of(ok.json())) == 4, ok.text
    for cypher in ("MATCH (n:Ingestion {projectId: $projectId}) RETURN n", "MATCH (n) DETACH DELETE n"):
        resp = await mg.http.post(url, json={"cypher": cypher}, headers=headers)
        assert resp.status_code == 422, resp.text
    no_token = await mg.http.post(url, json={"cypher": "MATCH (n:Requirement {projectId: $projectId}) RETURN n"})
    assert no_token.status_code == 403


async def test_admin_sees_hidden_labels(mg: Env) -> None:
    await logged_in(mg, "root", {}, admin=True)
    body = (await get(mg, "alpha", "start", labels="Ingestion")).json()
    assert labels_of(body) == {"Ingestion"}


async def test_page_lists_artifacts_and_only_visible_labels(mg: Env) -> None:
    await logged_in(mg, "mia", {"alpha": "viewer"})
    page = await mg.http.get("/console/projects/alpha/graph")
    assert page.status_code == 200 and "requirements.md" in page.text and "graph.js" in page.text
    assert 'value="Requirement"' in page.text and 'value="Ingestion"' not in page.text


async def test_no_role_is_forbidden(mg: Env) -> None:
    await logged_in(mg, "mia", {"beta": "viewer"})
    assert (await get(mg, "alpha", "start")).status_code == 403


async def post_query(env: Env, csrf: str, cypher: str) -> Any:
    return await env.http.post(
        "/console/projects/alpha/graph/query", json={"cypher": cypher}, headers={"x-csrf-token": csrf}
    )


async def bulk(url: str, count: int) -> None:
    driver = AsyncGraphDatabase.driver(url)
    async with driver.session() as s:
        await s.run(
            "UNWIND range(1, $n) AS i CREATE (:Element:Requirement {projectId: 'alpha', id: 'bulk' + i, i: i})",
            n=count,
        )
    await driver.close()


async def test_start_caps_at_500_nodes_and_unknown_filters_give_422(mg: Env, memgraph: str) -> None:
    await bulk(memgraph, 600)
    await logged_in(mg, "mia", {"alpha": "viewer"})
    body = (await get(mg, "alpha", "start", labels="Requirement")).json()
    assert len(nodes_of(body)) == 500 and body["truncated"] is True
    base = "/console/projects/alpha/graph/start"
    assert (await mg.http.get(base, params={"labels": "NoSuchLabel"})).status_code == 422
    assert (await mg.http.get(base, params={"types": "NO_SUCH_TYPE"})).status_code == 422


async def test_scope_corpus_is_rejected(mg: Env) -> None:
    csrf = await logged_in(mg, "mia", {"alpha": "viewer"})
    for cypher in (
        "MATCH (n) RETURN n",
        "MATCH (n:Requirement {projectId: $projectId}) RETURN n UNION MATCH (m:Requirement) RETURN m",
        "MATCH (a:Requirement {projectId: $projectId}) OPTIONAL MATCH (b:Requirement) RETURN a, b",
        "MATCH (a:Requirement {projectId: $projectId}) RETURN [(x:Requirement) | x] AS everything",
    ):
        resp = await post_query(mg, csrf, cypher)
        assert resp.status_code == 422, (cypher, resp.text)


async def test_cartesian_product_and_timeout_show_messages(mg: Env, memgraph: str) -> None:
    await bulk(memgraph, 3000)
    csrf = await logged_in(mg, "mia", {"alpha": "viewer"})
    graph = mg.app.state.service.graph
    graph.memory_limit = "5 MB"
    big = await post_query(
        mg,
        csrf,
        "MATCH (a:Requirement {projectId: $projectId}), (b:Requirement {projectId: $projectId}) "
        "WITH a, b ORDER BY a.i + b.i RETURN count(*) AS c",
    )
    assert big.status_code == 422 and "memory limit" in big.json()["error"], big.text
    graph.memory_limit = "100 MB"
    original = graph.run

    async def quick(cypher: str, params: Any, timeout: float = 1.0, max_rows: int | None = None) -> Any:
        return await original(cypher, params, 1.0, max_rows)

    graph.run = quick
    slow = "RETURN size([x IN range(1, 40000) WHERE size([y IN range(1, 40000) WHERE y = x]) = 0]) AS c"
    resp = await post_query(mg, csrf, slow)
    assert resp.status_code == 422 and "timed out" in resp.json()["error"], resp.text

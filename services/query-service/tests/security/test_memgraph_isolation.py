# ruff: noqa: E501
"""T4.4: two projects in Memgraph (Testcontainers, skipped without Docker); hostile input must return zero foreign data."""

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from conftest import Env
from neo4j import AsyncGraphDatabase
from sandman_query import nl as nl_module
from sandman_query import query as query_module
from sandman_query.guard import Checked
from test_memgraph import count, ids, mg, q  # noqa: F401 - mg is a fixture

sys.path.insert(0, str(Path(__file__).parent))

from test_query_path import CORPUS, HostileLlm  # noqa: E402

MARKER = "BETA-SECRET-NAME"
ANCHOR = "(n:Element {projectId: $projectId})"
BETA_IDS = [ids("beta", k, "el") for k in ("r1", "r2", "c1", "u1")]


@pytest.fixture
async def two(mg: Env) -> AsyncIterator[Env]:  # noqa: F811
    driver = AsyncGraphDatabase.driver(mg.kg)  # type: ignore[attr-defined]
    async with driver.session() as s:
        await s.run("MATCH (n {projectId: 'beta'}) SET n.name = $m", m=MARKER)
        await s.run("MATCH (n:Project {id: 'beta'}) SET n.name = $m", m=MARKER)
    await driver.close()
    yield mg


def leaked(text: str) -> bool:
    return MARKER in text or any(i in text for i in BETA_IDS)


def passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    def check(cypher: str, scoped: bool = True) -> Checked:
        return Checked(cypher.strip().rstrip(";"), has_limit=False)

    monkeypatch.setattr(query_module, "check", check)
    monkeypatch.setattr(nl_module, "check", check)


async def test_reject_corpus_returns_422_and_changes_nothing(two: Env) -> None:
    before = await count(two.kg)  # type: ignore[attr-defined]
    for e in CORPUS["reject"]:
        resp = await q(two, e["query"].replace("@A@", ANCHOR))
        assert resp.status_code in (400, 422) and not leaked(resp.text), e["id"]
    assert await count(two.kg) == before  # type: ignore[attr-defined]


async def test_bounded_corpus_is_bounded_and_leaks_nothing(two: Env) -> None:
    for e in CORPUS["bounded"]:
        resp = await q(two, e["query"], timeoutMs=2000)
        assert resp.status_code in (200, 422) and not leaked(resp.text), (e["id"], resp.status_code, resp.text[:200])
        if resp.status_code == 200:
            assert len(resp.json()["rows"]) <= 200


async def test_hostile_llm_cypher_is_refused_on_the_real_graph(two: Env) -> None:
    before = await count(two.kg)  # type: ignore[attr-defined]
    for e in CORPUS["reject"]:
        llm = HostileLlm([e["query"].replace("@A@", ANCHOR)])
        two.app.state.service.nl.llm_factory = lambda policy, llm=llm: llm
        resp = await two.http.post(
            "/v1/query/nl",
            json={"projectId": "alpha", "question": "which requirements exist"},
            headers=two.token("alice"),
        )
        assert resp.status_code == 422 and not leaked(resp.text), e["id"]
    assert await count(two.kg) == before  # type: ignore[attr-defined]


async def test_compliant_query_sees_only_its_project(two: Env) -> None:
    resp = await q(two, f"MATCH {ANCHOR} RETURN n.projectId AS p, n.name AS name")
    rows = resp.json()["rows"]
    assert rows and {r[0] for r in rows} == {"alpha"} and not leaked(resp.text)


NODE_LEAKS = [
    "MATCH (n:Element) RETURN n",
    "MATCH (n {projectId: 'beta'}) RETURN n",
    "MATCH (n:Project) RETURN n",
    "MATCH p = (a:Project)-[*1..3]-(b) RETURN p",
    "MATCH (n:Element) RETURN collect(n) AS l",
    "MATCH (n:Element) RETURN {x: n} AS m",
]


@pytest.mark.parametrize("cypher", NODE_LEAKS)
async def test_guard_off_post_check_drops_foreign_nodes(two: Env, monkeypatch: pytest.MonkeyPatch, cypher: str) -> None:
    passthrough(monkeypatch)
    resp = await q(two, cypher)
    assert resp.status_code == 422 and not leaked(resp.text)


@pytest.mark.xfail(strict=True, reason="F-3: scalar projections of foreign data cannot be checked by check_scope")
async def test_guard_off_scalar_projection_leaks(two: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    passthrough(monkeypatch)
    resp = await q(two, "MATCH (n:Element) RETURN n.name AS name")
    assert not leaked(resp.text)


@pytest.mark.parametrize(
    "cypher",
    [
        "CREATE (:Element {projectId: 'alpha', name: 'pwn'})",
        "MATCH (n:Element) DETACH DELETE n",
        "MATCH (n:Element) SET n.name = 'pwn'",
        "MATCH (n:Project) SET n.llmPolicy = 'cloud-allowed'",
    ],
)
async def test_guard_off_writes_do_not_change_the_graph(two: Env, monkeypatch: pytest.MonkeyPatch, cypher: str) -> None:
    passthrough(monkeypatch)
    before = await count(two.kg)  # type: ignore[attr-defined]
    resp = await q(two, cypher)
    assert resp.status_code in (422, 503), resp.text
    assert await count(two.kg) == before  # type: ignore[attr-defined]
    check: Any = await q(two, "MATCH (n:Element {projectId: $projectId, name: 'pwn'}) RETURN count(n) AS c")
    assert check.json()["rows"] == [[0]]

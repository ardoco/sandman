"""T4.5: strategy graphrag with fake embedding client (fake reader) and on Memgraph (Testcontainers)."""

import asyncio
import json
from typing import Any

import pytest
import yaml
from conftest import Env
from helpers import validate
from neo4j import AsyncGraphDatabase
from neo4j.exceptions import ClientError, ServiceUnavailable, TransientError
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_agent.llm import BudgetExceededError, ChatMessage, ChatRequest, ChatResponse, Usage
from sandman_query.nl import schema_text
from test_memgraph import ids, mg  # noqa: F401 - mg is a fixture

QUESTION = "which parts deal with caching"
MARKER = "BETA-SECRET-NAME"
DIM = 768


def onehot(i: int, weight: float = 1.0) -> list[float]:
    return [weight if j == i else 0.0 for j in range(DIM)]


class FakeLlm:
    """Records embed routes; chat answers with the scripted answer (cites the given ids)."""

    def __init__(self, cites: list[str] | None = None, error: Exception | None = None) -> None:
        self.routes: list[str] = []
        self.texts: list[str] = []
        self.requests: list[ChatRequest] = []
        self.cites, self.error = cites or [], error

    async def embed(self, model: str, texts: Any) -> list[list[float]]:
        if self.error:
            raise self.error
        self.routes.append(model)
        self.texts += list(texts)
        return [onehot(0) for _ in texts]

    async def chat(self, req: ChatRequest) -> ChatResponse:
        self.requests.append(req)
        text = " ".join(f"[[{c}]]" for c in self.cites)
        content = {"answer": f"Caching: {text}", "citations": self.cites}
        return ChatResponse(ChatMessage("assistant", json.dumps(content)), "stop", Usage(), "gw-model")


def graph(env: Env, present: bool = True, hits: list[dict[str, Any]] | None = None) -> list[tuple[str, dict[str, Any]]]:
    """Scripts the reader: vector hits, seeds, neighbours; every call is recorded."""
    calls: list[tuple[str, dict[str, Any]]] = []
    original = env.reader.run
    hits = hits if hits is not None else [
        {"key": "a#R1", "similarity": 0.9},
        {"key": "a#R1", "similarity": 0.8},
        {"key": "a#R2", "similarity": 0.7},
    ]  # fmt: skip

    async def run(cypher: str, params: Any, timeout: float = 10.0, max_rows: int | None = None) -> Any:
        calls.append((cypher, dict(params)))
        if "e.embedding IS NOT NULL" in cypher:
            return ["key"], [["a#R1"]] if present else []
        if "vector_search" in cypher:
            return ["key", "projectId", "similarity"], [
                [h["key"], h.get("projectId") or params["projectId"], h["similarity"]] for h in hits
            ]
        if "e.text AS text" in cypher:
            return ["key", "name", "kind", "text"], [
                ["a#R1", "R1", "functional", "x" * 900],
                ["a#R2", "R2", "functional", None],
            ]
        if "CONTAINS|DEPENDS_ON" in cypher:
            return ["source", "relation", "key", "name", "kind"], [["a#R1", "CONTAINS", "a#C1", "C1", "component"]] * 7
        if "TRACE_LINK" in cypher:
            return ["source", "relation", "key", "name", "kind"], [["a#R2", "TRACE_LINK", "a#U1", "U1", "type"]]
        return await original(cypher, params, timeout, max_rows)

    env.reader.run = run  # type: ignore[method-assign]
    return calls


async def ask(env: Env, llm: Any, project: str = "alpha", **body: Any) -> Any:
    if env.store.get_user("alice") is None:
        env.user("alice", {"alpha": "viewer", "beta": "viewer"})
    nl = env.app.state.service.nl
    nl.config["graphrag"]["enabled"] = True
    nl.llm_factory = lambda policy: llm
    payload = {"projectId": project, "question": QUESTION, "strategy": "graphrag"} | body
    return await env.http.post("/v1/query/nl", json=payload, headers=env.token("alice"))


async def test_rows_evidence_and_answer(env: Env) -> None:
    calls = graph(env)
    llm = FakeLlm(cites=["a#C1", "a#U1"])
    resp = await ask(env, llm)
    body = resp.json()
    assert resp.status_code == 200, body
    validate("/v1/query/nl", "post", 200, body)
    assert body["strategy"] == "graphrag" and "cypher" not in body and body["attempts"] == []
    assert [e["key"] for e in body["evidence"]] == ["a#R1", "a#R2", "a#C1", "a#U1"]
    assert body["evidence"][0]["score"] == 0.9 and body["evidence"][2]["path"] == ["a#R1", "a#C1"]
    keys = [r[0] for r in body["rows"]]
    assert keys.count("a#C1") == 5  # maxNeighbours
    assert len(body["rows"][0][3]) == 500
    assert body["answer"].startswith("Caching") and body["citations"] == ["a#C1", "a#U1"]
    search = next(p for c, p in calls if "vector_search" in c)
    assert search["k"] == 100 and search["projectId"] == "alpha" and len(search["vector"]) == DIM
    assert llm.texts == [QUESTION] and llm.routes == ["embed-local"]  # alpha is local-only


async def test_cloud_project_uses_default_route(env: Env) -> None:
    graph(env)
    llm = FakeLlm()
    resp = await ask(env, llm, project="beta")
    assert resp.status_code == 200 and llm.routes == ["embed-default"]


async def test_without_embeddings_the_answer_is_409(env: Env) -> None:
    graph(env, present=False)
    llm = FakeLlm()
    resp = await ask(env, llm)
    assert resp.status_code == 409 and "embeddings" in resp.json()["detail"]
    validate("/v1/query/nl", "post", 409, resp.json())
    assert llm.routes == []


async def test_no_hit_returns_empty_rows(env: Env) -> None:
    graph(env, hits=[])
    resp = await ask(env, FakeLlm())
    assert resp.status_code == 200 and resp.json()["rows"] == [] and "answer" not in resp.json()


@pytest.mark.parametrize("body", [{"mode": "cypher-only"}, {"execute": False}])
async def test_cypher_only_and_dry_run_embed_nothing(env: Env, body: dict[str, Any]) -> None:
    calls = graph(env)
    llm = FakeLlm()
    resp = await ask(env, llm, **body)
    assert resp.status_code == 200 and "rows" not in resp.json() and resp.json()["warnings"]
    assert llm.routes == [] and not [c for c, _ in calls if "vector_search" in c]


@pytest.mark.parametrize("error", [BudgetExceededError("budget sk-x"), RuntimeError("http://10.0.0.5 sk-secret")])
async def test_embedding_failure_is_503_without_leak(env: Env, error: Exception) -> None:
    graph(env)
    resp = await ask(env, FakeLlm(error=error))
    assert resp.status_code == 503 and "10.0.0.5" not in resp.text and "sk-secret" not in resp.text


async def test_foreign_hit_is_refused(env: Env) -> None:
    graph(env, hits=[{"key": MARKER, "projectId": "beta", "similarity": 0.99}])
    resp = await ask(env, FakeLlm())
    assert resp.status_code == 422 and MARKER not in resp.text


async def test_citation_outside_rows_and_evidence_is_dropped(env: Env) -> None:
    graph(env)
    resp = await ask(env, FakeLlm(cites=["a#FOREIGN"]))
    body = resp.json()
    assert resp.status_code == 200 and "answer" not in body and body["rows"]
    assert "a#FOREIGN" not in body["warnings"][0]  # F-6


async def test_without_llm_access_is_503(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    env.app.state.service.nl.config["graphrag"]["enabled"] = True
    resp = await env.http.post(
        "/v1/query/nl",
        json={"projectId": "alpha", "question": QUESTION, "strategy": "graphrag"},
        headers=env.token("alice"),
    )
    assert resp.status_code == 503


@pytest.mark.parametrize("strategy", ["cypher", "graphrag"])
async def test_policy_graph_error_is_503(env: Env, strategy: str) -> None:  # F-5
    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise ServiceUnavailable("bolt://memgraph:7687 down")

    env.reader.run = boom  # type: ignore[method-assign]
    resp = await ask(env, FakeLlm(), strategy=strategy, question="how many requirements are there")
    assert resp.status_code == 503 and "memgraph" not in resp.text


def test_schema_text_skips_internal_properties() -> None:
    schema = yaml.safe_load((DEFAULT_CONTRACTS_DIR / "kg" / "schema.yaml").read_text())
    text = schema_text(schema)
    assert "embedding" not in text and "name: string" in text


# ---------------------------------------------------------------- Memgraph

VECTORS = {"r1": onehot(0), "r2": onehot(1), "c1": onehot(2), "u1": onehot(3)}


async def embed_graph(uri: str, with_embeddings: bool = True) -> None:
    driver = AsyncGraphDatabase.driver(uri)
    async with driver.session() as s:
        for _ in range(60):
            try:
                result = await s.run(
                    "CREATE VECTOR INDEX element_embedding ON :Element(embedding) "
                    'WITH CONFIG {"dimension": 768, "capacity": 1000, "metric": "cos"}'
                )
                await result.consume()
                break
            except ClientError as exc:
                if "already exists" not in str(exc):
                    raise
                break
            except TransientError:
                await asyncio.sleep(1)
        else:
            raise AssertionError("vector index was not created")
        if with_embeddings:
            for project in ("alpha", "beta"):
                for name, vec in VECTORS.items():
                    await s.run("MATCH (e:Element {id: $id}) SET e.embedding = $v", id=ids(project, name, "el"), v=vec)
            await s.run("MATCH (n {projectId: 'beta'}) SET n.name = $m", m=MARKER)
    await driver.close()


async def test_memgraph_search_expansion_and_isolation(mg: Env) -> None:  # noqa: F811
    await embed_graph(mg.kg)  # type: ignore[attr-defined]
    llm = FakeLlm(cites=[ids("alpha", "req", "art") + "#R1"])
    resp = await ask(mg, llm)
    body = resp.json()
    assert resp.status_code == 200, body
    validate("/v1/query/nl", "post", 200, body)
    assert MARKER not in resp.text and ids("beta", "r1", "el") not in resp.text and "beta" not in resp.text
    keys = [e["key"] for e in body["evidence"]]
    assert keys[0] == ids("alpha", "req", "art") + "#R1"  # the query vector is r1's
    assert any(r[4] == "TRACE_LINK" for r in body["rows"]) and any(r[4] == "CONTAINS" for r in body["rows"])
    assert body["rows"][0][4] is None and body["answer"], body["warnings"]


async def test_memgraph_without_embeddings_is_409(mg: Env) -> None:  # noqa: F811
    await embed_graph(mg.kg, with_embeddings=False)  # type: ignore[attr-defined]
    resp = await ask(mg, FakeLlm())
    assert resp.status_code == 409

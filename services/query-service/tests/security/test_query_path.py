"""T4.4: hostile LLM, forged rows, authz, errors and injection through POST /v1/query/{cypher,nl} (fake reader)."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from conftest import Env
from neo4j.exceptions import Neo4jError
from sandman_agent.llm import BudgetExceededError, ChatMessage, ChatRequest, ChatResponse, Usage
from sandman_query import nl as nl_module
from sandman_query import query as query_module
from sandman_query.guard import Checked

CORPUS = yaml.safe_load((Path(__file__).parent / "corpus.yaml").read_text())
ANCHOR = "(n:Element {projectId: $projectId})"
GOOD = f"MATCH {ANCHOR} RETURN n.key AS key"
QUESTION = "which requirements exist"
MARKER = "BETA-SECRET-NAME"
FOREIGN = {"id": "x", "projectId": "beta", "name": MARKER, "_labels": ["Element"]}
FORGED = {
    "node": [[FOREIGN]],
    "relationship": [[{"projectId": "beta", "note": MARKER, "_type": "CONTAINS"}]],
    "list": [[[1, FOREIGN]]],
    "map": [[{"a": {"b": FOREIGN}}]],
    "path": [[[FOREIGN, {"_type": "CONTAINS"}, FOREIGN]]],
    "project": [[{"id": "beta", "name": MARKER, "_labels": ["Project"]}]],
}


class HostileLlm:
    """Answers every call with the next payload (cycled): out-of-scope Cypher and foreign citations."""

    def __init__(self, cypher: list[str] | None = None, cite: str = "") -> None:
        self.payloads, self.cite = cypher or [GOOD], cite
        self.requests: list[ChatRequest] = []

    async def chat(self, req: ChatRequest) -> ChatResponse:
        self.requests.append(req)
        cypher = self.payloads[(len(self.requests) - 1) % len(self.payloads)]
        content = {
            "cypher": cypher,
            "explanation": MARKER,
            "tool": "find_elements",
            "query": "a",
            "projectId": "beta",
            "answer": f"[[{self.cite}]]",
            "citations": [self.cite],
        }
        return ChatResponse(ChatMessage("assistant", json.dumps(content)), "stop", Usage(), "gw-model")

    async def embed(self, model: str, texts: Any) -> list[list[float]]:
        return [[1.0] + [0.0] * 767 for _ in texts]


class FailingLlm:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def chat(self, req: ChatRequest) -> ChatResponse:
        raise self.error


@pytest.fixture(params=["on", "off"])
def validation(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """`off` replaces the guard by a pass-through in the runner and in the NL service."""
    if request.param == "off":

        def passthrough(cypher: str, scoped: bool = True) -> Checked:
            return Checked(cypher.strip().rstrip(";"), has_limit=False)

        monkeypatch.setattr(query_module, "check", passthrough)
        monkeypatch.setattr(nl_module, "check", passthrough)
    return str(request.param)


@pytest.fixture(params=["cypher", "graphrag", "explore", "auto"])
def strategy(request: pytest.FixtureRequest, env: Env) -> str:
    nl = env.app.state.service.nl
    if request.param not in nl.strategies:
        pytest.skip(f"strategy {request.param} not implemented (T4.5/T4.6/T4.7)")
    for name in ("graphrag", "explore") if request.param == "auto" else (request.param,):
        nl.config.setdefault(name, {})["enabled"] = True  # auto: all enabled, so the classifier runs
    return str(request.param)


@pytest.fixture
def alice(env: Env) -> dict[str, str]:
    env.user("alice", {"alpha": "viewer"})
    return env.token("alice")


async def ask(env: Env, headers: dict[str, str], llm: Any, strategy: str = "cypher", **body: Any) -> httpx.Response:
    env.app.state.service.nl.llm_factory = lambda policy: llm
    payload = {"projectId": "alpha", "question": QUESTION, "strategy": strategy} | body
    return await env.http.post("/v1/query/nl", json=payload, headers=headers)


def hostile_queries(env: Env) -> list[str]:
    return [c for c, _ in env.reader.queries if not c.startswith(("MATCH (p:Project", "/* evidence"))]


def entries(section: str, key: str) -> list[Any]:
    return [pytest.param(e, id=e["id"]) for e in CORPUS[section] if key in e]


@pytest.mark.parametrize("entry", [pytest.param(e, id=e["id"]) for e in CORPUS["reject"]])
async def test_hostile_llm_cypher_never_reaches_the_reader(
    env: Env, alice: dict[str, str], entry: dict[str, Any]
) -> None:
    llm = HostileLlm([entry["query"].replace("@A@", ANCHOR)])
    resp = await ask(env, alice, llm)
    assert resp.status_code == 422 and len(llm.requests) == 3
    assert hostile_queries(env) == []
    assert MARKER not in resp.text


@pytest.mark.parametrize("entry", entries("injection", "emits"))
async def test_question_injection_has_no_effect(env: Env, alice: dict[str, str], entry: dict[str, Any]) -> None:
    llm = HostileLlm([entry["emits"]])
    resp = await ask(env, alice, llm, question=entry["question"])
    assert resp.status_code == 422 and hostile_queries(env) == []
    messages = llm.requests[0].messages
    assert [m.content for m in messages if m.role == "user"].count(entry["question"]) == 1
    assert all(entry["question"] not in m.content for m in messages if m.role != "user")


@pytest.mark.parametrize("entry", entries("injection", "kg_text"))
async def test_kg_injection_cannot_cite_foreign_ids(env: Env, alice: dict[str, str], entry: dict[str, Any]) -> None:
    env.reader.result = (["key", "text"], [["a#R1", entry["kg_text"]]])
    llm = HostileLlm([GOOD], cite=entry["foreign_id"])
    resp = await ask(env, alice, llm)
    body = resp.json()
    assert resp.status_code == 200 and body["rows"] == [["a#R1", entry["kg_text"]]]
    assert "answer" not in body and "citations" not in body
    assert body["cypher"] == GOOD


@pytest.mark.parametrize("kind", FORGED)
async def test_forged_foreign_rows_never_reach_the_caller(
    env: Env, alice: dict[str, str], strategy: str, validation: str, kind: str
) -> None:
    env.reader.result = (["n"], FORGED[kind])
    cypher = await env.http.post("/v1/query/cypher", json={"projectId": "alpha", "cypher": GOOD}, headers=alice)
    nl = await ask(env, alice, HostileLlm([GOOD]), strategy)
    for resp in (cypher, nl):
        assert resp.status_code == 422 and MARKER not in resp.text, resp.text


@pytest.mark.xfail(strict=True, reason="F-3: scalar projections of foreign data cannot be checked by check_scope")
async def test_foreign_scalar_is_not_detected(env: Env, alice: dict[str, str]) -> None:
    env.reader.result = (["name"], [[MARKER]])
    resp = await env.http.post("/v1/query/cypher", json={"projectId": "alpha", "cypher": GOOD}, headers=alice)
    assert MARKER not in resp.text


@pytest.mark.xfail(strict=True, reason="F-3: StatusChange has no projectId, so a foreign one passes check_scope")
async def test_foreign_node_without_project_id_is_not_detected(env: Env, alice: dict[str, str]) -> None:
    env.reader.result = (["n"], [[{"id": "s", "note": MARKER, "_labels": ["StatusChange"]}]])
    resp = await env.http.post("/v1/query/cypher", json={"projectId": "alpha", "cypher": GOOD}, headers=alice)
    assert MARKER not in resp.text


@pytest.mark.parametrize("strategy_name", ["cypher", "graphrag", "explore", "auto"])
async def test_role_check_runs_before_strategy_availability(env: Env, strategy_name: str) -> None:
    env.user("alice", {"alpha": "viewer"})
    env.user("mallory")
    for headers, project, status in [
        ({}, "alpha", 401),
        (env.token("mallory"), "alpha", 404),
        (env.token("alice"), "beta", 404),
        (env.token("alice", project="beta"), "alpha", 404),
    ]:
        resp = await ask(env, headers, HostileLlm(), strategy_name, projectId=project)
        assert resp.status_code == status, (project, resp.text)
    assert hostile_queries(env) == []


async def test_authz_before_strategy_for_cypher_endpoint(env: Env) -> None:
    env.user("mallory")
    resp = await env.http.post(
        "/v1/query/cypher", json={"projectId": "beta", "cypher": GOOD}, headers=env.token("mallory")
    )
    assert resp.status_code == 404 and hostile_queries(env) == []


async def test_strategy_that_does_not_exist_is_refused_after_authz(env: Env, alice: dict[str, str]) -> None:
    resp = await ask(env, alice, HostileLlm(), "graphrag")
    assert resp.status_code == 501 and hostile_queries(env) == []


async def test_repairs_are_bounded(env: Env, alice: dict[str, str]) -> None:
    llm = HostileLlm(["MATCH (n) RETURN n"])
    resp = await ask(env, alice, llm)
    assert resp.status_code == 422 and len(llm.requests) == 3


async def test_gateway_errors_do_not_leak(env: Env, alice: dict[str, str]) -> None:
    secret = "http://10.0.0.5:4000 key sk-secret"
    for error in (RuntimeError(secret), BudgetExceededError("budget of key sk-budget exhausted")):
        resp = await ask(env, alice, FailingLlm(error))
        assert resp.status_code == 503
        assert "10.0.0.5" not in resp.text and "sk-secret" not in resp.text


async def test_database_errors_do_not_leak(env: Env, alice: dict[str, str], validation: str) -> None:
    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise Neo4jError("bolt://memgraph:7687 refused for user kg-admin")

    env.reader.run = boom  # type: ignore[method-assign]
    env.app.state.service.nl.llm_factory = lambda policy: HostileLlm()
    transport = httpx.ASGITransport(app=env.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://query") as http:
        direct = await http.post("/v1/query/cypher", json={"projectId": "alpha", "cypher": GOOD}, headers=alice)
        nl = await http.post("/v1/query/nl", json={"projectId": "alpha", "question": QUESTION}, headers=alice)
    assert direct.status_code == 503
    assert nl.status_code in (500, 503)
    for resp in (direct, nl):
        assert "memgraph" not in resp.text and "kg-admin" not in resp.text


async def test_reserved_parameters_are_refused(env: Env, alice: dict[str, str]) -> None:
    resp = await env.http.post(
        "/v1/query/cypher", json={"projectId": "alpha", "cypher": GOOD, "params": {"projectId": "beta"}}, headers=alice
    )
    assert resp.status_code == 422 and hostile_queries(env) == []


async def test_project_id_is_always_the_callers_project(env: Env, alice: dict[str, str]) -> None:
    resp = await env.http.post(
        "/v1/query/cypher", json={"projectId": "alpha", "cypher": GOOD, "params": {"other": "beta"}}, headers=alice
    )
    assert resp.status_code == 200
    assert env.reader.queries[-1][1]["projectId"] == "alpha"


async def test_scope_violation_is_not_repaired(env: Env, alice: dict[str, str]) -> None:
    env.reader.result = (["n"], FORGED["node"])
    llm = HostileLlm()
    resp = await ask(env, alice, llm)
    assert resp.status_code == 422 and len(llm.requests) == 1

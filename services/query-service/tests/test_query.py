"""Cypher endpoint, NL stub, schema and exports with a fake KG reader (real Memgraph: test_memgraph.py)."""

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import Env, service_headers
from helpers import validate, validate_shared
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_agent.contracts import ContractError, Contracts
from sandman_query.auth import load_plugin_grants
from sandman_query.query import Intents

ANCHORED = "MATCH (n:Element {projectId: $projectId}) RETURN n.key AS key"
INTENTS = yaml.safe_load((DEFAULT_CONTRACTS_DIR.parent / "config" / "query" / "intents.yaml").read_text())["intents"]


async def cypher(env: Env, headers: dict[str, str], **body: Any) -> Any:
    return await env.http.post(
        "/v1/query/cypher", json={"projectId": "alpha", "cypher": ANCHORED, **body}, headers=headers
    )


async def test_cypher_binds_project_limit_and_memory_limit(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    resp = await cypher(env, env.token("alice"))
    assert resp.status_code == 200
    validate("/v1/query/cypher", "post", 200, resp.json())
    text, params = env.reader.queries[-1]
    assert text.endswith("RETURN n.key AS key LIMIT 201 QUERY MEMORY LIMIT 100 MB")
    assert params == {"projectId": "alpha"}
    await cypher(env, env.token("alice"), cypher=ANCHORED + " LIMIT 5", limit=10, params={"x": 1})
    text, params = env.reader.queries[-1]
    assert text.endswith("LIMIT 5 QUERY MEMORY LIMIT 100 MB") and params == {"projectId": "alpha", "x": 1}


async def test_user_cannot_supply_project_id_or_reserved_params(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    for name in ("projectId", "timeout", "cypher"):
        resp = await cypher(env, env.token("alice"), params={name: "beta"})
        assert resp.status_code == 422, name
        validate("/v1/query/cypher", "post", 422, resp.json())
    assert not any("beta" in str(p) for _, p in env.reader.queries)


async def test_guard_rejection_is_422_problem(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    resp = await cypher(env, env.token("alice"), cypher="MATCH (n) RETURN n")
    assert resp.status_code == 422 and "not anchored" in resp.json()["detail"]
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert not env.reader.queries
    assert (await cypher(env, env.token("alice"), cypher="x" * 20001)).status_code == 400


async def test_truncated_and_result_cap(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    env.reader.result = (["key"], [["a"], ["b"], ["c"]])
    body = (await cypher(env, env.token("alice"), limit=2)).json()
    assert body["rows"] == [["a"], ["b"]] and body["truncated"] is True
    body = (await cypher(env, env.token("alice"), limit=3)).json()
    assert len(body["rows"]) == 3 and body["truncated"] is False


async def test_forged_row_of_another_project_is_rejected_and_logged(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    env.user("alice", {"alpha": "viewer"})
    forged = {"key": "x", "projectId": "beta", "_labels": ["Element"]}
    for row in ([forged], [{"nested": [forged]}], [{"projectId": "alpha"}, forged]):
        env.reader.result = (["n"], [[row]])
        with caplog.at_level(logging.ERROR):
            resp = await cypher(env, env.token("alice"))
        assert resp.status_code == 422 and "another project" in resp.json()["detail"]
    assert any(getattr(r, "fields", {}).get("event") == "scope_violation" for r in caplog.records)
    env.reader.result = (["n"], [[{"key": "x", "projectId": "alpha"}]])
    assert (await cypher(env, env.token("alice"))).status_code == 200


async def test_unscoped_queries_are_for_global_admins_only(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    env.user("root", admin=True)
    body = {"cypher": "MATCH (n) RETURN count(n) AS c"}
    assert (await env.http.post("/v1/query/cypher", json=body, headers=env.token("alice"))).status_code == 403
    assert (
        await env.http.post("/v1/query/cypher", json=body, headers=service_headers("orchestrator"))
    ).status_code == 403
    assert (
        await env.http.post("/v1/query/cypher", json=body, headers=service_headers("plugin:other"))
    ).status_code == 403
    assert (
        await env.http.post("/v1/query/cypher", json=body, headers=env.token("root", project="alpha"))
    ).status_code == 403
    resp = await env.http.post("/v1/query/cypher", json=body, headers=env.token("root"))
    assert resp.status_code == 200 and "projectId" not in env.reader.queries[-1][1]
    bad = await env.http.post("/v1/query/cypher", json={"cypher": "MATCH (n) DELETE n"}, headers=env.token("root"))
    assert bad.status_code == 422  # the clause allowlist applies to admins as well
    scoped = await env.http.post(
        "/v1/query/cypher", json={"cypher": "MATCH (n) RETURN n", "projectId": "alpha"}, headers=env.token("root")
    )
    assert scoped.status_code == 422  # a project was named, so the anchoring applies


async def test_plugin_grants(env: Env) -> None:
    ok = await cypher(env, service_headers("plugin:stub"))
    assert ok.status_code == 200
    assert (await cypher(env, service_headers("plugin:stub"), projectId="beta")).status_code == 404  # not granted
    assert (await cypher(env, service_headers("plugin:other"), projectId="beta")).status_code == 200  # "*"


async def test_timeouts_are_capped_per_caller_kind(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    await cypher(env, env.token("alice"), timeoutMs=30000)
    await cypher(env, service_headers("orchestrator"), timeoutMs=30000)
    await cypher(env, env.token("alice"), timeoutMs=500)
    assert env.reader.timeouts == [10.0, 30.0, 0.5]


@pytest.mark.parametrize("intent", INTENTS, ids=[i["id"] for i in INTENTS])
async def test_nl_intents_return_their_cypher(env: Env, intent: dict[str, str]) -> None:
    env.user("alice", {"alpha": "viewer"})
    questions = {
        "untraced-requirements": ("Untraced requirements?", {}),
        "links-of": ("links of Media Cache", {"name": "Media Cache"}),
        "open-inconsistencies": ("show open inconsistencies", {}),
        "components-without-documentation": ("Which components without documentation", {}),
        "changes-since": ("what changed since abc123", {"revision": "abc123"}),
    }
    question, extracted = questions[intent["id"]]
    resp = await env.http.post(
        "/v1/query/nl", json={"projectId": "alpha", "question": question}, headers=env.token("alice")
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate("/v1/query/nl", "post", 200, body)
    assert body["cypher"] == intent["cypher"].strip()
    assert body["params"] == {"projectId": "alpha", **extracted}
    assert env.reader.queries[-1][1] == {"projectId": "alpha", **extracted}


async def test_nl_modes_and_unknown_question(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    h = env.token("alice")
    only = await env.http.post(
        "/v1/query/nl",
        json={"projectId": "alpha", "question": "open inconsistencies", "mode": "cypher-only"},
        headers=h,
    )
    assert only.json()["columns"] == [] and "rows" not in only.json() and not env.reader.queries
    no_exec = await env.http.post(
        "/v1/query/nl", json={"projectId": "alpha", "question": "open inconsistencies", "execute": False}, headers=h
    )
    assert no_exec.json()["rows"] == [] and not env.reader.queries
    validate("/v1/query/nl", "post", 200, only.json())
    unknown = await env.http.post(
        "/v1/query/nl", json={"projectId": "alpha", "question": "why is the sky blue"}, headers=h
    )
    assert unknown.status_code == 422 and "open-inconsistencies" in unknown.json()["detail"]
    validate("/v1/query/nl", "post", 422, unknown.json())
    short = await env.http.post("/v1/query/nl", json={"projectId": "alpha", "question": "ab"}, headers=h)
    assert short.status_code == 400
    assert (
        await env.http.post("/v1/query/nl", json={"projectId": "beta", "question": "open inconsistencies"}, headers=h)
    ).status_code == 404


async def test_schema_is_the_kg_contract(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    env.user("nobody")
    assert (await env.http.get("/v1/schema", headers=env.token("nobody"))).status_code == 403
    resp = await env.http.get("/v1/schema", headers=env.token("alice"))
    assert resp.json() == yaml.safe_load(Path(DEFAULT_CONTRACTS_DIR / "kg" / "schema.yaml").read_text())
    validate("/v1/schema", "get", 200, resp.json())


async def test_exports_with_empty_graph(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    h = env.token("alice")
    csv_resp = await env.http.get("/v1/projects/alpha/export/tracelinks", headers=h)
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert csv_resp.text == "source_key,target_key,link_type,approach,approach_version,confidence,status,run_id\n"
    assert env.reader.queries[-1][1] == {
        "projectId": "alpha",
        "approach": None,
        "minConfidence": 0.0,
        "includeRejected": False,
    }
    await env.http.get(
        "/v1/projects/alpha/export/tracelinks?approach=lissa&minConfidence=0.5&includeRejected=true", headers=h
    )
    assert env.reader.queries[-1][1] == {
        "projectId": "alpha",
        "approach": "lissa",
        "minConfidence": 0.5,
        "includeRejected": True,
    }
    assert (await env.http.get("/v1/projects/alpha/export/tracelinks?minConfidence=2", headers=h)).status_code == 400
    assert (await env.http.get("/v1/projects/alpha/export/inconsistencies", headers=h)).json() == []
    assert (await env.http.get("/v1/projects/alpha/export/inconsistencies?format=csv", headers=h)).status_code == 200


async def test_status_counts_in_flight_requests(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    env.reader.gate = asyncio.Event()
    task = asyncio.create_task(cypher(env, env.token("alice")))
    await asyncio.sleep(0.05)
    status = (await env.http.get("/v1/status", headers=service_headers("orchestrator"))).json()
    validate_shared("ServiceStatus", status)
    assert status["state"] == "working" and status["activeTasks"] == 1
    env.reader.gate.set()
    assert (await task).status_code == 200
    assert (await env.http.get("/v1/status", headers=service_headers("orchestrator"))).json()["state"] == "idle"


def test_config_files_are_validated_against_their_schemas(tmp_path: Path) -> None:
    contracts = Contracts(DEFAULT_CONTRACTS_DIR)
    config = DEFAULT_CONTRACTS_DIR.parent / "config" / "query"
    assert Intents(config / "intents.yaml", contracts).supported()
    assert load_plugin_grants(config / "plugins.yaml", contracts)
    contracts.load_config("query-examples", config / "examples.yaml")
    bad = tmp_path / "intents.yaml"
    bad.write_text("intents: [{id: Bad_Id, pattern: x, cypher: y}]\n")
    with pytest.raises(ContractError):
        Intents(bad, contracts)
    bad.write_text("plugin-stubs: nope\n")
    with pytest.raises(ContractError):
        load_plugin_grants(bad, contracts)


async def test_nl_strategy(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    h = env.token("alice")
    body = {"projectId": "alpha", "question": "open inconsistencies"}
    for strategy in ("auto", "cypher"):
        ok = await env.http.post("/v1/query/nl", json={**body, "strategy": strategy}, headers=h)
        assert ok.status_code == 200 and ok.json()["strategy"] == "cypher"
    for strategy in ("graphrag", "explore"):
        r = await env.http.post("/v1/query/nl", json={**body, "strategy": strategy}, headers=h)
        assert r.status_code == 501
    bad = await env.http.post("/v1/query/nl", json={**body, "strategy": "magic"}, headers=h)
    assert bad.status_code == 400

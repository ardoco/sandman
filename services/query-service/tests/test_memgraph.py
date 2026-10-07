# ruff: noqa: E501
"""Integration with Testcontainers Memgraph 3.13.1 (skipped without Docker): read path, guard corpus, NL, exports."""

import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from conftest import Env, build_env
from helpers import SPEC, validate
from jsonschema import Draft202012Validator
from neo4j import AsyncGraphDatabase
from neo4j.exceptions import Neo4jError
from sandman_agent.ids import sha256_hex, stable_id
from test_guard import ACCEPTED, REJECTED

TS = "2026-10-06T12:00:00Z"
RUN = "run_" + "0" * 32


def ids(project: str, name: str, prefix: str) -> str:
    return stable_id(prefix, project, name)


SEED = """
MATCH (pr:Project {id: $p})
CREATE (pr)-[:HAS_ARTIFACT]->(req:Artifact {id: $req, projectId: $p, path: 'requirements.md', kind: 'requirements'})
CREATE (pr)-[:HAS_ARTIFACT]->(arch:Artifact {id: $arch, projectId: $p, path: 'model.repository', kind: 'architecture-model'})
CREATE (pr)-[:HAS_ARTIFACT]->(code:Artifact {id: $code, projectId: $p, path: 'Cache.java', kind: 'code'})
CREATE (req)-[:HAS_VERSION]->(vold:ArtifactVersion {id: $vold, artifactId: $req, projectId: $p})
CREATE (req)-[:HAS_VERSION]->(vreq:ArtifactVersion {id: $vreq, artifactId: $req, projectId: $p})
CREATE (arch)-[:HAS_VERSION]->(varch:ArtifactVersion {id: $varch, artifactId: $arch, projectId: $p})
CREATE (code)-[:HAS_VERSION]->(vcode:ArtifactVersion {id: $vcode, artifactId: $code, projectId: $p})
CREATE (req)-[:CURRENT_VERSION]->(vreq), (arch)-[:CURRENT_VERSION]->(varch), (code)-[:CURRENT_VERSION]->(vcode)
CREATE (vreq)-[:HAS_ELEMENT]->(r1:Element:Requirement {id: $r1, key: $req + '#R1', projectId: $p, kind: 'functional',
       name: 'R1', text: '<script>alert(1)</script>', startLine: 3, endLine: 4, runId: $run})
CREATE (vreq)-[:HAS_ELEMENT]->(r2:Element:Requirement {id: $r2, key: $req + '#R2', projectId: $p, kind: 'functional', name: 'R2', runId: $run})
CREATE (vreq)-[:HAS_ELEMENT]->(r3:Element:Requirement {id: $r3, key: $req + '#R3', projectId: $p, kind: 'functional', name: 'R3', runId: $run})
CREATE (vold)-[:HAS_ELEMENT]->(rold:Element:Requirement {id: $rold, key: $req + '#Rold', projectId: $p, kind: 'functional', name: 'Rold', runId: $run})
CREATE (varch)-[:HAS_ELEMENT]->(c1:Element:ModelElement {id: $c1, key: $arch + '#C1', projectId: $p, kind: 'component', name: 'C1', runId: $run})
CREATE (varch)-[:HAS_ELEMENT]->(c2:Element:ModelElement {id: $c2, key: $arch + '#C2', projectId: $p, kind: 'component', name: 'C2', runId: $run})
CREATE (vcode)-[:HAS_ELEMENT]->(u1:Element:CodeUnit {id: $u1, key: $code + '#U1', projectId: $p, kind: 'type', name: 'U1', runId: $run})
CREATE (c1)-[:CONTAINS]->(u1)
CREATE (l1:TraceLink {id: $l1, projectId: $p, linkType: 'implements', confidence: 0.9, approach: 'lissa', approachVersion: '1',
       configHash: 'x', status: 'candidate', active: true, runId: $run, createdAt: $ts})
CREATE (l2:TraceLink {id: $l2, projectId: $p, linkType: 'implements', confidence: 0.9, approach: 'lissa', approachVersion: '0',
       configHash: 'x', status: 'candidate', active: false, runId: $run, createdAt: $ts})
CREATE (l3:TraceLink {id: $l3, projectId: $p, linkType: 'implements', confidence: 0.5, approach: 'lissa', approachVersion: '1',
       configHash: 'x', status: 'rejected', active: true, runId: $run, createdAt: $ts})
CREATE (l4:TraceLink {id: $l4, projectId: $p, linkType: 'documents', confidence: 0.8, approach: 'explicit', approachVersion: '1',
       configHash: 'x', status: 'confirmed', active: true, runId: $run, createdAt: $ts})
CREATE (l5:TraceLink {id: $l5, projectId: $p, linkType: 'relates', confidence: 0.7, approach: 'lissa', approachVersion: '1',
       configHash: 'x', status: 'candidate', active: true, runId: $run, createdAt: $ts})
CREATE (l1)-[:SOURCE]->(r1), (l1)-[:TARGET]->(u1), (l2)-[:SOURCE]->(r1), (l2)-[:TARGET]->(u1)
CREATE (l3)-[:SOURCE]->(r2), (l3)-[:TARGET]->(u1), (l4)-[:SOURCE]->(r1), (l4)-[:TARGET]->(c1)
CREATE (l5)-[:SOURCE]->(r2), (l5)-[:TARGET]->(rold)
CREATE (i1:Inconsistency {id: $i1, fingerprint: $f1, projectId: $p, type: 'untraced', severity: 'warning', title: 'I1',
       description: 'd', evidence: '{"k": 1}', status: 'ACCEPTED', verification: 'none', detector: 'stub', detectorVersion: '1',
       firstRunId: $run, lastRunId: $run, evidenceHash: 'h', createdAt: $ts, updatedAt: $ts})
CREATE (i2:Inconsistency {id: $i2, fingerprint: $f2, projectId: $p, type: 'untraced', severity: 'error', title: 'I2',
       status: 'OPEN', verification: 'none', detector: 'stub', detectorVersion: '1',
       firstRunId: $run, lastRunId: $run, evidenceHash: 'h', createdAt: $ts, updatedAt: $ts})
CREATE (i1)-[:INVOLVES {role: 'subject'}]->(r3), (i1)-[:INVOLVES {role: 'link'}]->(l4), (i2)-[:INVOLVES {role: 'subject'}]->(r3)
CREATE (i1)-[:HAS_STATUS_CHANGE]->(:StatusChange {from: 'OPEN', to: 'ACCEPTED', actor: 'user:alice', reason: 'fine', at: $ts})
CREATE (g1:Ingestion {id: $g1, projectId: $p, seq: 1, mode: 'full', sourceRevision: 'rev1', actor: 'u', createdAt: $ts})
CREATE (g2:Ingestion {id: $g2, projectId: $p, seq: 2, mode: 'partial', sourceRevision: 'rev2', actor: 'u', createdAt: $ts})
CREATE (pr)-[:HAS_INGESTION]->(g1), (pr)-[:HAS_INGESTION]->(g2)
CREATE (g1)-[:ACTIVATED]->(vold), (g2)-[:ACTIVATED]->(vreq), (g2)-[:ACTIVATED]->(varch)
"""
NAMES = {"req": "art", "arch": "art", "code": "art", "vold": "ver", "vreq": "ver", "varch": "ver", "vcode": "ver",
         "r1": "el", "r2": "el", "r3": "el", "rold": "el", "c1": "el", "c2": "el", "u1": "el",
         "l1": "tl", "l2": "tl", "l3": "tl", "l4": "tl", "l5": "tl", "i1": "inc", "i2": "inc", "g1": "ing", "g2": "ing"}  # fmt: skip


async def seed(uri: str) -> None:
    driver = AsyncGraphDatabase.driver(uri)
    async with driver.session() as s:
        await s.run("MATCH (n) DETACH DELETE n")
        for p in ("alpha", "beta"):
            await s.run(
                "CREATE (:Project {id: $p, name: $p, llmPolicy: 'local-only', ingestionSeq: 2, createdAt: $ts})",
                p=p,
                ts=TS,
            )
            params = {k: ids(p, k, prefix) for k, prefix in NAMES.items()}
            params |= {"f1": sha256_hex(p + "1"), "f2": sha256_hex(p + "2")}
            await s.run(SEED, p=p, run=RUN, ts=TS, **params)  # type: ignore[arg-type]
    await driver.close()


async def count(uri: str) -> tuple[int, int]:
    driver = AsyncGraphDatabase.driver(uri)
    async with driver.session() as s:
        nodes = (await (await s.run("MATCH (n) RETURN count(n) AS c")).single())["c"]  # type: ignore[index]
        rels = (await (await s.run("MATCH ()-[r]->() RETURN count(r) AS c")).single())["c"]  # type: ignore[index]
    await driver.close()
    return nodes, rels


@pytest.fixture
async def mg(memgraph: str, tmp_path: Path) -> AsyncIterator[Env]:
    await seed(memgraph)
    async for env in build_env(tmp_path, memgraph):
        env.user("alice", {"alpha": "viewer"})
        env.kg = memgraph  # type: ignore[attr-defined]
        yield env


async def q(env: Env, cypher: str, project: str = "alpha", **extra: Any) -> Any:
    return await env.http.post(
        "/v1/query/cypher", json={"projectId": project, "cypher": cypher, **extra}, headers=env.token("alice")
    )


async def test_readyz_and_project_list_use_the_real_graph(mg: Env) -> None:
    assert (await mg.http.get("/readyz")).json()["checks"] == {"memgraph": "ok", "userstore": "ok"}
    projects = (await mg.http.get("/v1/projects", headers=mg.token("alice"))).json()
    assert [p["id"] for p in projects] == ["alpha"]


async def test_rows_are_json_with_labels_and_types_and_no_internal_ids(mg: Env) -> None:
    resp = await q(
        mg, "MATCH (a:Element {projectId: $projectId})-[r:CONTAINS]->(b) RETURN a, r, b.key AS bk, [a.name] AS l"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate("/v1/query/cypher", "post", 200, body)
    a, r, bk, names = body["rows"][0]
    assert body["columns"] == ["a", "r", "bk", "l"]
    assert a["_labels"] == ["Element", "ModelElement"] and a["name"] == "C1" and r == {"_type": "CONTAINS"}
    assert bk.endswith("#U1") and names == ["C1"]
    assert "id(" not in resp.text and "elementId" not in resp.text


async def test_limit_and_truncation(mg: Env) -> None:
    body = (await q(mg, "MATCH (n:Element {projectId: $projectId}) RETURN n.key AS k ORDER BY k", limit=3)).json()
    assert len(body["rows"]) == 3 and body["truncated"] is True
    body = (
        await q(mg, "MATCH (n:Element {projectId: $projectId}) RETURN n.key AS k ORDER BY k LIMIT 100000", limit=5)
    ).json()
    assert len(body["rows"]) == 5 and body["truncated"] is True
    body = (await q(mg, "MATCH (n:Project {id: $projectId}) RETURN n.id AS k")).json()
    assert body["rows"] == [["alpha"]] and body["truncated"] is False
    union = "MATCH (n:Element {projectId: $projectId}) RETURN n.key AS k UNION ALL MATCH (n:Element {projectId: $projectId}) RETURN n.key AS k"
    assert len((await q(mg, union, limit=4)).json()["rows"]) == 4


async def test_projects_are_isolated(mg: Env) -> None:
    keys = (await q(mg, "MATCH (n:Element {projectId: $projectId}) RETURN DISTINCT n.projectId AS p")).json()["rows"]
    assert keys == [["alpha"]]
    reach = "MATCH (n:Project {id: $projectId})-[*1..6]-(m) RETURN DISTINCT m.projectId AS p"
    assert {r[0] for r in (await q(mg, reach)).json()["rows"]} <= {"alpha", None}
    other = (await q(mg, "MATCH (n:Element {projectId: $projectId}) RETURN n.key AS k", project="beta")).status_code
    assert other == 404  # alice is no member of beta


async def test_post_check_catches_a_relationship_between_projects(mg: Env) -> None:
    driver = AsyncGraphDatabase.driver(mg.kg)  # type: ignore[attr-defined]
    async with driver.session() as s:  # violates the writer invariant on purpose
        await s.run(
            "MATCH (a:Element {key: $a}), (b:Element {key: $b}) CREATE (a)-[:DEPENDS_ON]->(b)",
            a=ids("alpha", "req", "art") + "#R1", b=ids("beta", "req", "art") + "#R1",
        )  # fmt: skip
    await driver.close()
    resp = await q(mg, "MATCH (a:Element {projectId: $projectId})-[:DEPENDS_ON]->(b) RETURN b")
    assert resp.status_code == 422 and "another project" in resp.json()["detail"]
    ok = await q(mg, "MATCH (a:Element {projectId: $projectId})-[:DEPENDS_ON]->(b) RETURN b.name AS name")
    assert ok.status_code == 200  # a scalar without projectId passes (the invariant is the writers' duty)


@pytest.mark.parametrize(("query", "message"), [r for r in REJECTED if len(r[0]) < 1000])
async def test_corpus_is_rejected_and_changes_nothing(mg: Env, query: str, message: str) -> None:
    before = await count(mg.kg)  # type: ignore[attr-defined]
    resp = await q(mg, query)
    assert resp.status_code in (400, 422), resp.text
    assert await count(mg.kg) == before  # type: ignore[attr-defined]


RUNNABLE = [a for a in ACCEPTED if "projectId" in a and "EXISTS { (" not in a]  # Memgraph needs MATCH inside EXISTS


@pytest.mark.parametrize("query", RUNNABLE)
async def test_accepted_queries_run_on_memgraph(mg: Env, query: str) -> None:
    resp = await q(mg, query)
    assert resp.status_code == 200, (query, resp.text)


WRITES = [
    "CREATE (n:X) RETURN n",
    "MATCH (n:Element) SET n.x = 1 RETURN count(n)",
    "MATCH (n:Element) DETACH DELETE n",
    "MATCH (n:Element) REMOVE n.name RETURN n",
    "MERGE (n:Y {id: 1}) RETURN n",
    "MATCH (n:Project) CALL { WITH n CREATE (:Z) } RETURN n",
    "SHOW STORAGE INFO",
    "LOAD CSV FROM '/etc/passwd' AS row RETURN row",
]


@pytest.mark.parametrize("query", WRITES)
async def test_with_validation_off_the_read_transaction_still_blocks_writes(mg: Env, query: str) -> None:
    before = await count(mg.kg)  # type: ignore[attr-defined]
    reader = mg.app.state.service.reader  # the guard is bypassed: straight into the read path
    with pytest.raises(Neo4jError):
        await reader.run(query, {}, 10)
    assert await count(mg.kg) == before  # type: ignore[attr-defined]


async def test_cartesian_product_hits_the_memory_limit(mg: Env) -> None:
    driver = AsyncGraphDatabase.driver(mg.kg)  # type: ignore[attr-defined]
    async with driver.session() as s:
        await s.run("UNWIND range(1, 3000) AS i CREATE (:Bulk {projectId: 'alpha', i: i})")
    await driver.close()
    mg.app.state.service.runner.memory_limit = "5 MB"
    started = time.monotonic()
    resp = await q(mg, "MATCH (a:Bulk {projectId: $projectId}), (b:Bulk {projectId: $projectId}) "
                       "WITH a, b ORDER BY a.i + b.i RETURN count(*) AS c")  # fmt: skip
    assert resp.status_code == 422 and "memory limit" in resp.json()["detail"], resp.text
    assert time.monotonic() - started < 10
    assert (await q(mg, "MATCH (n:Project {id: $projectId}) RETURN n.id AS id")).status_code == 200  # server alive


async def test_timeout_aborts_long_queries(mg: Env) -> None:
    slow = "RETURN size([x IN range(1, 40000) WHERE size([y IN range(1, 40000) WHERE y = x]) = 0]) AS c"
    started = time.monotonic()
    resp = await q(mg, slow, timeoutMs=1000)
    assert resp.status_code == 422 and "timed out" in resp.json()["detail"], resp.text
    assert time.monotonic() - started < 8
    assert (await q(mg, "MATCH (n:Project {id: $projectId}) RETURN n.id AS id")).status_code == 200


NL_EXPECTED = {
    "untraced requirements": [["art#R3"]],
    "links of R1": [["art#R1", "art#U1"], ["art#R1", "art#C1"]],
    "open inconsistencies": [["I2"]],
    "components without documentation": [["art#C2"]],
    "what changed since rev1": [[2, "rev2", "model.repository"], [2, "rev2", "requirements.md"]],
}


@pytest.mark.parametrize("question", NL_EXPECTED)
async def test_nl_intents_on_data(mg: Env, question: str) -> None:
    resp = await mg.http.post(
        "/v1/query/nl", json={"projectId": "alpha", "question": question}, headers=mg.token("alice")
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate("/v1/query/nl", "post", 200, body)
    rows = body["rows"]
    if question == "untraced requirements":
        assert [r[0].split("#")[1] for r in rows] == ["R3"]
    elif question == "links of R1":
        assert sorted((r[0].split("#")[1], r[1].split("#")[1]) for r in rows) == [("R1", "C1"), ("R1", "U1")]
    elif question == "open inconsistencies":
        assert [r[1:4] for r in rows] == [["untraced", "error", "I2"]]
    elif question == "components without documentation":
        assert [r[0].split("#")[1] for r in rows] == ["C2"]
    else:
        assert [(r[0], r[1], r[2]) for r in rows] == [(2, "rev2", "model.repository"), (2, "rev2", "requirements.md")]


async def test_tracelink_export(mg: Env) -> None:
    h = mg.token("alice")
    base = "/v1/projects/alpha/export/tracelinks"
    resp = await mg.http.get(base, headers=h)
    lines = resp.text.splitlines()
    assert lines[0] == "source_key,target_key,link_type,approach,approach_version,confidence,status,run_id"
    rows = sorted(line.split(",")[1:7] for line in lines[1:])
    assert [(r[0].split("#")[1], r[1], r[2]) for r in rows] == [
        ("C1", "documents", "explicit"),
        ("U1", "implements", "lissa"),
    ]
    assert all(line.split(",")[0].startswith(ids("alpha", "req", "art")) for line in lines[1:])  # only alpha
    as_json = (await mg.http.get(base + "?format=json", headers=h)).json()
    validate("/v1/projects/{projectId}/export/tracelinks", "get", 200, as_json)
    assert {link["status"] for link in as_json} == {
        "candidate",
        "confirmed",
    }  # inactive, non-current endpoint, rejected excluded
    with_rejected = (await mg.http.get(base + "?format=json&includeRejected=true", headers=h)).json()
    assert len(with_rejected) == 3 and "rejected" in {link["status"] for link in with_rejected}
    assert len((await mg.http.get(base + "?format=json&minConfidence=0.85", headers=h)).json()) == 1
    assert len((await mg.http.get(base + "?format=json&approach=explicit", headers=h)).json()) == 1


async def test_inconsistency_export_with_history(mg: Env) -> None:
    h = mg.token("alice")
    items = (await mg.http.get("/v1/projects/alpha/export/inconsistencies", headers=h)).json()
    validate("/v1/projects/{projectId}/export/inconsistencies", "get", 200, items)
    by_title = {i["title"]: i for i in items}
    i1 = by_title["I1"]
    assert i1["status"] == "ACCEPTED" and i1["evidence"] == {"k": 1} and i1["allowedTransitions"] == ["OPEN"]
    assert i1["history"] == [{"from": "OPEN", "to": "ACCEPTED", "actor": "user:alice", "reason": "fine", "at": TS}]
    assert {e["role"] for e in i1["involved"]} == {"subject", "link"}
    subject = next(e for e in i1["involved"] if e["role"] == "subject")["element"]
    assert (
        subject["name"] == "R3" and subject["label"] == "Requirement" and subject["artifactPath"] == "requirements.md"
    )
    assert i1["artifactPaths"] == ["model.repository", "requirements.md"]
    csv_text = (await mg.http.get("/v1/projects/alpha/export/inconsistencies?format=csv", headers=h)).text
    assert csv_text.splitlines()[0].startswith("id,type,severity") and len(csv_text.splitlines()) == 3
    assert json.loads(json.dumps(items)) == items
    assert Draft202012Validator is not None and SPEC is not None

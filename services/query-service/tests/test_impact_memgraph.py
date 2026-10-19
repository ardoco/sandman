# ruff: noqa: E501
"""Change impact on a real Memgraph graph (skipped without Docker)."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from conftest import Env, build_env
from helpers import validate
from neo4j import AsyncGraphDatabase

ING1, ING2 = "ing_" + "1" * 32, "ing_" + "2" * 32
PATH = "/v1/projects/{projectId}/ingestions/{ingestionId}/impact"
SEED = """
CREATE (:Project {id: 'alpha', name: 'alpha', llmPolicy: 'local-only', ingestionSeq: 2, createdAt: 't'})
CREATE (:Project {id: 'beta', name: 'beta', llmPolicy: 'local-only', ingestionSeq: 1, createdAt: 't'})
CREATE (req:Artifact {id: 'req', projectId: 'alpha', path: 'req.md', kind: 'requirements'})
CREATE (arch:Artifact {id: 'arch', projectId: 'alpha', path: 'model.repository', kind: 'architecture-model'})
CREATE (code:Artifact {id: 'code', projectId: 'alpha', path: 'Cache.java', kind: 'code'})
CREATE (req)-[:HAS_VERSION]->(v1:ArtifactVersion {id: 'v1', projectId: 'alpha'})
CREATE (req)-[:HAS_VERSION]->(v2:ArtifactVersion {id: 'v2', projectId: 'alpha'})
CREATE (arch)-[:HAS_VERSION]->(va:ArtifactVersion {id: 'va', projectId: 'alpha'})
CREATE (code)-[:HAS_VERSION]->(vc:ArtifactVersion {id: 'vc', projectId: 'alpha'})
CREATE (req)-[:CURRENT_VERSION]->(v2), (arch)-[:CURRENT_VERSION]->(va), (code)-[:CURRENT_VERSION]->(vc)
CREATE (i1:Ingestion {id: $ing1, projectId: 'alpha', seq: 1, mode: 'full', sourceRevision: 'r1', createdAt: '2026-10-06T12:00:00Z'})
CREATE (i2:Ingestion {id: $ing2, projectId: 'alpha', seq: 2, mode: 'partial', sourceRevision: 'r2', createdAt: '2026-10-06T13:00:00Z'})
CREATE (i1)-[:ACTIVATED]->(v1), (i1)-[:ACTIVATED]->(va), (i1)-[:ACTIVATED]->(vc), (i2)-[:ACTIVATED]->(v2)
CREATE (v1)-[:HAS_ELEMENT]->(r1o:Element:Requirement {id: 'r1o', key: 'req#R1', projectId: 'alpha', contentHash: 'h1', kind: 'functional', name: 'R1'})
CREATE (v1)-[:HAS_ELEMENT]->(gone:Element:Requirement {id: 'gone', key: 'req#Gone', projectId: 'alpha', contentHash: 'g', kind: 'functional', name: 'Gone'})
CREATE (v1)-[:HAS_ELEMENT]->(:Element:Requirement {id: 'ko', key: 'req#Keep', projectId: 'alpha', contentHash: 'k', kind: 'functional', name: 'Keep'})
CREATE (v2)-[:HAS_ELEMENT]->(r1:Element:Requirement {id: 'r1', key: 'req#R1', projectId: 'alpha', contentHash: 'h2', kind: 'functional', name: 'R1'})
CREATE (v2)-[:HAS_ELEMENT]->(r1c:Element:Requirement {id: 'r1c', key: 'req#R1c', projectId: 'alpha', contentHash: 'c', kind: 'functional', name: 'R1c'})
CREATE (v2)-[:HAS_ELEMENT]->(:Element:Requirement {id: 'k', key: 'req#Keep', projectId: 'alpha', contentHash: 'k', kind: 'functional', name: 'Keep'})
CREATE (va)-[:HAS_ELEMENT]->(c1:Element:ModelElement {id: 'c1', key: 'arch#C1', projectId: 'alpha', kind: 'component', name: 'C1'})
CREATE (va)-[:HAS_ELEMENT]->(c2:Element:ModelElement {id: 'c2', key: 'arch#C2', projectId: 'alpha', kind: 'component', name: 'C2'})
CREATE (vc)-[:HAS_ELEMENT]->(u1:Element:CodeUnit {id: 'u1', key: 'code#U1', projectId: 'alpha', kind: 'type', name: 'U1'})
CREATE (vc)-[:HAS_ELEMENT]->(u2:Element:CodeUnit {id: 'u2', key: 'code#U2', projectId: 'alpha', kind: 'type', name: 'U2'})
CREATE (r1)-[:CONTAINS]->(r1c)
CREATE (c1)-[:CONTAINS]->(u1)
CREATE (l1:TraceLink {id: 'l1', projectId: 'alpha', status: 'candidate', active: true})
CREATE (l2:TraceLink {id: 'l2', projectId: 'alpha', status: 'rejected', active: true})
CREATE (l3:TraceLink {id: 'l3', projectId: 'alpha', status: 'candidate', active: false})
CREATE (l4:TraceLink {id: 'l4', projectId: 'alpha', status: 'candidate', active: true})
CREATE (l1)-[:SOURCE]->(r1), (l1)-[:TARGET]->(u1)
CREATE (l2)-[:SOURCE]->(r1), (l2)-[:TARGET]->(u2)
CREATE (l3)-[:SOURCE]->(gone), (l3)-[:TARGET]->(c2)
CREATE (l4)-[:SOURCE]->(r1), (l4)-[:TARGET]->(foreign:Element:CodeUnit {id: 'foreign', key: 'code#Foreign', projectId: 'beta', name: 'Foreign'})
CREATE (:Artifact {id: 'bart', projectId: 'beta', path: 'Foreign.java', kind: 'code'})
CREATE (:Ingestion {id: $ing3, projectId: 'beta', seq: 1, mode: 'full', createdAt: '2026-10-06T12:00:00Z'})
"""


@pytest.fixture
async def mg(memgraph: str, tmp_path: Path) -> AsyncIterator[Env]:
    driver = AsyncGraphDatabase.driver(memgraph)
    async with driver.session() as s:
        await s.run("MATCH (n) DETACH DELETE n")
        await s.run(SEED, ing1=ING1, ing2=ING2, ing3="ing_" + "3" * 32)  # type: ignore[arg-type]
    await driver.close()
    async for env in build_env(tmp_path, memgraph):
        env.user("alice", {"alpha": "viewer", "beta": "viewer"})
        yield env


async def impact(env: Env, ing: str = ING2, project: str = "alpha", query: str = "") -> Any:
    return await env.http.get(f"/v1/projects/{project}/ingestions/{ing}/impact{query}", headers=env.token("alice"))


async def test_changes_and_distance_two_path(mg: Env) -> None:
    resp = await impact(mg)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate(PATH, "get", 200, body)
    assert [(c["key"], c["change"]) for c in body["changed"]] == [
        ("req#Gone", "removed"), ("req#R1", "modified"), ("req#R1c", "added")
    ]  # fmt: skip
    by_key = {a["key"]: a for a in body["affected"]}
    assert set(by_key) == {"arch#C1", "arch#C2", "code#U1"}
    assert by_key["code#U1"]["distance"] == 1 and by_key["arch#C1"]["distance"] == 2
    assert [p["key"] for p in by_key["arch#C1"]["paths"][0]] == ["req#R1", "code#U1", "arch#C1"]
    assert [p["edge"] for p in by_key["arch#C1"]["paths"][0]] == ["start", "TRACE_LINK", "CONTAINS"]
    assert by_key["arch#C2"]["paths"][0][0] == {"key": "req#Gone", "edge": "start", "via": "previous-version"}


async def test_rejected_link_same_artifact_and_foreign_project_are_excluded(mg: Env) -> None:
    keys = {a["key"] for a in (await impact(mg)).json()["affected"]}
    assert "code#U2" not in keys and "req#R1c" not in keys and "code#Foreign" not in keys


async def test_depth_one_hides_distance_two(mg: Env) -> None:
    keys = {a["key"] for a in (await impact(mg, query="?depth=1")).json()["affected"]}
    assert keys == {"code#U1", "arch#C2"}


async def test_limit_truncates(mg: Env) -> None:
    body = (await impact(mg, query="?limit=1")).json()
    assert len(body["affected"]) == 1 and body["truncated"]


async def test_first_ingestion_lists_all_as_added(mg: Env) -> None:
    body = (await impact(mg, ING1)).json()
    assert {c["change"] for c in body["changed"]} == {"added"} and body["complete"]


async def test_foreign_ingestion_id_is_404(mg: Env) -> None:
    assert (await impact(mg, ING2, project="beta")).status_code == 404
    assert (await impact(mg, "ing_" + "3" * 32, project="alpha")).status_code == 404
    body = (await impact(mg, "ing_" + "3" * 32, project="beta")).json()
    assert body["changed"] == [] and body["affected"] == []

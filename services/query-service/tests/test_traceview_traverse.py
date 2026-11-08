# ruff: noqa: E501
"""Trace view statements on a real Traverse graph (skipped without Docker)."""

from collections.abc import AsyncIterator
from functools import partial

import pytest
from neo4j import AsyncGraphDatabase
from sandman_query import traceview
from sandman_query.reader import Reader

SEED = """
CREATE (a:Artifact {id: 'a1', projectId: 'alpha', path: 'req.md', kind: 'requirements'})
CREATE (b:Artifact {id: 'a2', projectId: 'alpha', path: 'model.repository', kind: 'architecture-model'})
CREATE (a)-[:HAS_VERSION]->(v1:ArtifactVersion {id: 'v1', projectId: 'alpha'})
CREATE (b)-[:HAS_VERSION]->(v2:ArtifactVersion {id: 'v2', projectId: 'alpha'})
CREATE (a)-[:CURRENT_VERSION]->(v1), (b)-[:CURRENT_VERSION]->(v2)
CREATE (v1)-[:HAS_ELEMENT]->(r1:Element:Requirement {id: 'r1', key: 'req#1', projectId: 'alpha', kind: 'functional', name: 'R1', text: 'The system shall cache.', sentenceIndex: 1})
CREATE (v1)-[:HAS_ELEMENT]->(r2:Element:Requirement {id: 'r2', key: 'req#2', projectId: 'alpha', kind: 'functional', name: 'R2', sentenceIndex: 2, embedding: [0.1, 0.2], embeddingModel: 'm'})
CREATE (v2)-[:HAS_ELEMENT]->(c1:Element:ModelElement {id: 'c1', key: 'arch#C1', projectId: 'alpha', kind: 'component', name: 'C1'})
CREATE (v2)-[:HAS_ELEMENT]->(c2:Element:ModelElement {id: 'c2', key: 'arch#C2', projectId: 'alpha', kind: 'component', name: 'C2', startLine: 7, inferred: true})
CREATE (r1)-[:CONTAINS]->(r2)
CREATE (c1)-[:DEPENDS_ON]->(c2)
CREATE (l:TraceLink {id: 'l1', projectId: 'alpha', active: true, linkType: 'implements', approach: 'x', confidence: 0.9, status: 'candidate'})
CREATE (l)-[:SOURCE]->(r1), (l)-[:TARGET]->(c1)
CREATE (x:TraceLink {id: 'l2', projectId: 'alpha', active: false, status: 'candidate'})
CREATE (x)-[:SOURCE]->(r1), (x)-[:TARGET]->(c2)
CREATE (i:Inconsistency {id: 'i1', projectId: 'alpha', title: 'T', severity: 'high', status: 'open', type: 'missing', description: 'd'})
CREATE (i)-[:INVOLVES {role: 'subject'}]->(r1)
CREATE (i)-[:INVOLVES {role: 'object'}]->(l)
CREATE (:Element:CodeUnit {id: 'foreign', key: 'f', projectId: 'beta', name: 'F'})
"""


@pytest.fixture
async def reader(traverse: str) -> AsyncIterator[Reader]:
    driver = AsyncGraphDatabase.driver(traverse)
    async with driver.session() as s:
        await s.run("MATCH (n) DETACH DELETE n")
        await s.run(SEED)
    await driver.close()
    r = Reader(traverse, None, None)
    yield r
    await r.close()


async def test_trace_view(reader: Reader) -> None:
    out = await reader.transaction(partial(traceview.trace_view, project="alpha"), timeout=120)
    by_id = {e["id"]: e for e in out["elements"]}
    assert set(by_id) == {"r1", "r2", "c1", "c2"}
    assert (
        by_id["r1"].items()
        >= {"artifactId": "a1", "label": "Requirement", "excerpt": "The system shall cache.", "order": 1}.items()
    )
    assert "parentId" not in by_id["r1"] and by_id["r2"]["parentId"] == "r1"
    assert by_id["c2"]["order"] == 7 and by_id["c2"]["inferred"] is True and by_id["c2"]["excerpt"] == ""
    assert [lk["id"] for lk in out["links"]] == ["l1"]
    assert out["links"][0]["sourceId"] == "r1" and out["links"][0]["targetId"] == "c1"
    assert [(f["id"], sorted(i["id"] for i in f["involved"])) for f in out["findings"]] == [("i1", ["l1", "r1"])]
    assert not out["truncated"]


async def test_element_detail(reader: Reader) -> None:
    out = await reader.transaction(partial(traceview.element, project="alpha", element_id="r1"), timeout=120)
    assert out is not None
    assert out["element"]["label"] == "Requirement" and out["element"]["name"] == "R1"
    assert [lk["id"] for lk in out["links"]] == ["l1"] and out["links"][0]["confidence"] == 0.9
    assert sorted((f["id"], f["role"]) for f in out["findings"]) == [("i1", "object"), ("i1", "subject")]
    assert out["neighbours"] == [
        {"relation": "CONTAINS", "direction": "out", "id": "r2", "name": "R2", "label": "Requirement"}
    ]
    r2 = await reader.transaction(partial(traceview.element, project="alpha", element_id="r2"), timeout=120)
    assert r2 is not None and "embedding" not in r2["element"] and "embeddingModel" not in r2["element"]
    assert r2["neighbours"][0]["direction"] == "in" and r2["links"] == [] and r2["findings"] == []
    c1 = await reader.transaction(partial(traceview.element, project="alpha", element_id="c1"), timeout=120)
    assert c1 is not None and c1["neighbours"][0]["relation"] == "DEPENDS_ON"


async def test_element_foreign_or_unknown_is_none(reader: Reader) -> None:
    for eid in ("foreign", "nope"):
        assert (
            await reader.transaction(partial(traceview.element, project="alpha", element_id=eid), timeout=120) is None
        )

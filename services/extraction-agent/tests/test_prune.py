"""T5.2: prune-artifact-data keeps referenced versions and removes old ones (Memgraph via Testcontainers)."""

import asyncio
from typing import Any

import httpx
from sandman_agent import A2aClient, TaskSnapshot

URL = "http://extraction-agent:8000"
SEED = """
CREATE (p:Project {id: $p})
CREATE (a:Artifact {id: 'art_' + $p, projectId: $p, path: 'A.java', kind: 'code'})
CREATE (p)-[:HAS_ARTIFACT]->(a)
CREATE (req:Element:Requirement {id: 'req_' + $p, projectId: $p, key: 'r#1', name: 'r', runId: 'run_req_' + $p})
CREATE (runR:Run {id: 'run_req_' + $p, projectId: $p, agent: 'extraction-agent', status: 'completed'})
CREATE (runR)-[:PRODUCED]->(req)
WITH p, a, req
UNWIND range(1, 6) AS n
CREATE (g:Ingestion {id: 'ing_' + $p + n, projectId: $p, seq: n, eventPublished: true})
CREATE (v:ArtifactVersion {id: 'ver_' + $p + n, projectId: $p, contentHash: $hashes[n - 1]})
CREATE (a)-[:HAS_VERSION]->(v)
CREATE (g)-[:ACTIVATED]->(v)
CREATE (e:Element:CodeUnit {id: 'el_' + $p + n, projectId: $p, key: 'A#' + n, name: 'e' + n, runId: 'run_' + $p + n})
CREATE (v)-[:HAS_ELEMENT]->(e)
CREATE (r:Run {id: 'run_' + $p + n, projectId: $p, agent: 'extraction-agent', status: 'completed'})
CREATE (r)-[:PRODUCED]->(e)
CREATE (c:Concept {id: 'con_' + $p + n, projectId: $p, name: 'c' + n})
CREATE (e)-[:MENTIONS]->(c)
CREATE (l:TraceLink {id: 'tl_' + $p + n, projectId: $p, approach: 'explicit-reference', status: 'candidate',
                     active: false, runId: 'run_req_' + $p})
CREATE (l)-[:SOURCE]->(req) CREATE (l)-[:TARGET]->(e)
WITH p, a, collect(v) AS vs
WITH a, vs[5] AS current
CREATE (a)-[:CURRENT_VERSION]->(current)
"""
HASHES = [f"{n:064x}" for n in range(1, 7)]


async def seed(env: Any, project: str) -> None:
    await env.svc.kg.write(SEED, p=project, hashes=HASHES)
    await env.svc.kg.write(
        "MATCH (:Project {id: $p}) CREATE (i:Inconsistency {id: 'inc_' + $p, projectId: $p, status: 'OPEN'}) "
        "WITH i MATCH (e:Element {id: 'el_' + $p + '2'}) CREATE (i)-[:INVOLVES]->(e)",
        p=project,
    )
    await env.svc.kg.write("MATCH (l:TraceLink {id: 'tl_' + $p + '3'}) SET l.active = true", p=project)
    await env.svc.kg.write("MATCH (l:TraceLink {id: 'tl_' + $p + '5'}) SET l.status = 'rejected'", p=project)


async def prune(env: Any, project: str, **options: Any) -> TaskSnapshot:
    client = A2aClient(env.tokens["orchestrator"], transport=httpx.ASGITransport(app=env.app))
    try:
        task = await client.submit(URL, "prune-artifact-data", {"projectId": project, **options})
        for _ in range(300):
            if task.terminal:
                return task
            await asyncio.sleep(0.1)
            task = await client.get(URL, task.task_id)
        raise TimeoutError(task)
    finally:
        await client.close()


async def counts(env: Any, project: str) -> dict[str, Any]:
    rows = await env.svc.kg.read(
        "MATCH (p:Project {id: $p}) "
        "OPTIONAL MATCH (v:ArtifactVersion {projectId: $p}) WITH p, collect(v.id) AS versions "
        "OPTIONAL MATCH (e:Element {projectId: $p}) WITH p, versions, count(e) AS elements "
        "OPTIONAL MATCH (l:TraceLink {projectId: $p}) WITH versions, elements, count(l) AS links "
        "RETURN versions, elements, links",
        p=project,
    )
    return {**rows[0], "versions": sorted(rows[0]["versions"])}


async def test_prune_removes_old_and_keeps_referenced(env: Any) -> None:
    other = env.project + "-other"
    await seed(env, env.project)
    await seed(env, other)
    before = await counts(env, other)
    protected = {"protectedContentHashes": [HASHES[3]]}

    dry = await prune(env, env.project, keepVersionsPerArtifact=1, **protected)
    assert dry.state == "TASK_STATE_COMPLETED", dry
    assert dry.result["dryRun"] is True
    assert dry.result["removed"]["versions"] == 2
    assert (await counts(env, env.project))["versions"] == sorted(f"ver_{env.project}{n}" for n in range(1, 7))

    done = await prune(env, env.project, keepVersionsPerArtifact=1, dryRun=False, **protected)
    assert done.state == "TASK_STATE_COMPLETED", done
    assert done.result["removed"] == {
        "links": 2, "elements": 2, "concepts": 2, "versions": 2, "ingestions": 2, "runs": 2,
    }  # fmt: skip
    after = await counts(env, env.project)
    assert after["versions"] == sorted(f"ver_{env.project}{n}" for n in (2, 3, 4, 6))
    assert after["elements"] == 5
    assert await counts(env, other) == before
    assert not await env.svc.kg.read("MATCH (c:Concept {id: $c}) RETURN c", c=f"con_{env.project}1")
    assert await env.svc.kg.read("MATCH (c:Concept {id: $c}) RETURN c", c=f"con_{env.project}2")

    again = await prune(env, env.project, keepVersionsPerArtifact=1, dryRun=False, **protected)
    assert again.result["removedCount"] == 0
    event = (await env.events("sandman.retention.pruned"))[-1]
    assert event["data"]["scope"] == "artifact-data" and event["data"]["removedCount"] == 0
    audit = await env.svc.kg.read(
        "MATCH (r:Run {projectId: $p, skill: 'prune-artifact-data'}) RETURN count(r) AS n", p=env.project
    )
    assert audit[0]["n"] == 3

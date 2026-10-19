"""Strategy `graphrag` (T4.5, ADR-0018): vector search over Element.embedding, bounded expansion, evidence list."""

import logging
import math
import time
from typing import TYPE_CHECKING, Any

from neo4j.exceptions import DriverError, Neo4jError
from sandman_agent.llm import BudgetExceededError, PolicyError
from starlette.exceptions import HTTPException

from sandman_query.auth import Principal
from sandman_query.reader import ScopeViolation, check_scope

if TYPE_CHECKING:
    from sandman_query.nl import NlService

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {"enabled": False, "topK": 5, "overFetch": 20, "maxNeighbours": 5, "dimension": 768}
TEXT_CHARS = 500
COLUMNS = ["key", "name", "kind", "text", "relation", "from"]
PRESENT = "MATCH (e:Element {projectId: $projectId}) WHERE e.embedding IS NOT NULL RETURN e.key AS key LIMIT 1"
SEARCH = """CALL vector_search.search('element_embedding', $k, $vector) YIELD node, similarity
WITH node, similarity WHERE node.projectId = $projectId
RETURN node.key AS key, node.projectId AS projectId, similarity ORDER BY similarity DESC"""
SEEDS = """MATCH (:Artifact)-[:CURRENT_VERSION]->(:ArtifactVersion)-[:HAS_ELEMENT]->(e:Element {projectId: $projectId})
WHERE e.key IN $keys
RETURN e.key AS key, e.name AS name, e.kind AS kind, e.text AS text"""
STRUCTURE = """MATCH (:Artifact)-[:CURRENT_VERSION]->(:ArtifactVersion)-[:HAS_ELEMENT]->
      (e:Element {projectId: $projectId})
WHERE e.key IN $keys
MATCH (e)-[r:CONTAINS|DEPENDS_ON|PROVIDES|REQUIRES]-(n:Element {projectId: $projectId})
RETURN e.key AS source, type(r) AS relation, n.key AS key, n.name AS name, n.kind AS kind"""
TRACES = """MATCH (e:Element {projectId: $projectId}) WHERE e.key IN $keys
MATCH (l:TraceLink {projectId: $projectId, active: true})-[:SOURCE|TARGET]->(e)
WHERE l.status <> 'rejected'
MATCH (l)-[:SOURCE|TARGET]->(n:Element {projectId: $projectId})
WHERE n.key <> e.key
RETURN e.key AS source, 'TRACE_LINK' AS relation, n.key AS key, n.name AS name, n.kind AS kind"""


def fit(vector: list[float], dimension: int) -> list[float]:
    """Cut to `dimension` and re-normalize (as the extraction-agent does when it writes); shorter is an error."""
    if len(vector) < dimension:
        raise ValueError(f"embedding has {len(vector)} dimensions, the index needs {dimension}")
    if len(vector) == dimension:
        return [float(x) for x in vector]
    cut = vector[:dimension]
    norm = math.sqrt(sum(x * x for x in cut)) or 1.0
    return [x / norm for x in cut]


async def read(nl: "NlService", cypher: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        return await nl.reader.rows(cypher, params)
    except (Neo4jError, DriverError, OSError, TimeoutError):
        log.warning("graphrag read failed", exc_info=True)
        raise HTTPException(503, "knowledge graph unavailable") from None


async def graphrag(
    nl: "NlService", who: Principal, project: str, question: str, mode: str, execute: bool
) -> dict[str, Any]:
    cfg = {**DEFAULTS, **nl.config.get("graphrag", {})}
    out: dict[str, Any] = {"columns": COLUMNS, "truncated": False, "elapsedMs": 0, "strategy": "graphrag"}
    out |= {"attempts": [], "warnings": []}
    if mode == "cypher-only" or not execute:
        return out | {"warnings": ["strategy graphrag runs no Cypher query; nothing was executed"]}
    if not nl.can_call_llm():
        raise HTTPException(503, "LLM unavailable: no embedding access configured")
    started = time.monotonic()
    if not await read(nl, PRESENT, {"projectId": project}):
        raise HTTPException(409, "this project has no element embeddings (embedding is disabled or has not run yet)")
    client, route = await nl.llm(project, embed=True)
    try:
        vector = fit((await client.embed(route, [question]))[0], cfg["dimension"])
    except (PolicyError, BudgetExceededError) as e:
        raise HTTPException(503, f"LLM unavailable: {e}") from None
    except Exception:
        log.exception("embedding call failed")
        raise HTTPException(503, "LLM unavailable") from None
    found = await read(nl, SEARCH, {"k": cfg["topK"] * cfg["overFetch"], "vector": vector, "projectId": project})
    try:
        check_scope(found, project)
    except ScopeViolation as e:
        raise HTTPException(422, str(e)) from None
    scores: dict[str, float] = {}
    for hit in found:
        if isinstance(hit.get("key"), str):
            scores.setdefault(hit["key"], float(hit.get("similarity") or 0.0))
    keys = list(scores)[: cfg["topK"]]
    if not keys:
        return out | {"rows": [], "evidence": [], "elapsedMs": int((time.monotonic() - started) * 1000)}
    params = {"keys": keys}
    seeds = {r[0]: r for r in (await nl.runner.run(who, project, SEEDS, params))["rows"]}
    ranked = [k for k in keys if k in seeds]
    rows: list[list[Any]] = [
        [k, seeds[k][1], seeds[k][2], (seeds[k][3] or "")[:TEXT_CHARS], None, None] for k in ranked
    ]
    evidence: list[dict[str, Any]] = [{"key": k, "score": scores[k]} for k in ranked]
    seen = {k for k in ranked}
    per_seed: dict[str, int] = {}
    for query in (STRUCTURE, TRACES):
        for source, relation, key, name, kind in (await nl.runner.run(who, project, query, params))["rows"]:
            if per_seed.get(source, 0) >= cfg["maxNeighbours"]:
                continue
            per_seed[source] = per_seed.get(source, 0) + 1
            rows.append([key, name, kind, None, relation, source])
            if key not in seen:
                seen.add(key)
                evidence.append({"key": key, "path": [source, key]})
    elapsed = int((time.monotonic() - started) * 1000)
    return out | {"rows": rows, "evidence": evidence, "elapsedMs": elapsed}

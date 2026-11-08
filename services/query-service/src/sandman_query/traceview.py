"""Trace viewer data (current elements, active trace links, findings) of one project in one read transaction.

Every statement is parameterized and anchored to the project.
"""

from typing import Any

from sandman_query.reader import TxRows

MAX_ELEMENTS = 5000  # ponytail: fixed cap, paging or per-artifact loading if projects outgrow it
MAX_LINKS = 5000
MAX_NEIGHBOURS = 200
MAX_DETAIL_LINKS = 500
EXCERPT = 300
TIMEOUT = 20.0

ELEMENTS = f"""
MATCH (a:Artifact {{projectId: $p}})-[:CURRENT_VERSION]->(:ArtifactVersion {{projectId: $p}})
      -[:HAS_ELEMENT]->(e:Element {{projectId: $p}})
OPTIONAL MATCH (par:Element {{projectId: $p}})-[:CONTAINS]->(e)
RETURN a.id AS artifactId, a.path AS artifactPath, e.id AS id, e.key AS key,
       [l IN labels(e) WHERE l <> 'Element'][0] AS label, e.kind AS kind, e.name AS name,
       substring(coalesce(e.text, ''), 0, {EXCERPT}) AS excerpt, par.id AS parentId,
       e.sentenceIndex AS sentenceIndex, e.startLine AS startLine, e.inferred AS inferred
ORDER BY a.path, coalesce(e.sentenceIndex, e.startLine, 0), e.key LIMIT {MAX_ELEMENTS + 1}
"""
LINKS = f"""
MATCH (l:TraceLink {{projectId: $p, active: true}})
MATCH (l)-[:SOURCE]->(s:Element {{projectId: $p}})<-[:HAS_ELEMENT]-(:ArtifactVersion {{projectId: $p}})
      <-[:CURRENT_VERSION]-(:Artifact {{projectId: $p}})
MATCH (l)-[:TARGET]->(t:Element {{projectId: $p}})<-[:HAS_ELEMENT]-(:ArtifactVersion {{projectId: $p}})
      <-[:CURRENT_VERSION]-(:Artifact {{projectId: $p}})
RETURN l.id AS id, s.id AS sourceId, t.id AS targetId, l.linkType AS linkType, l.approach AS approach,
       l.confidence AS confidence, l.status AS status, l.reviewedBy AS reviewedBy
ORDER BY l.id LIMIT {MAX_LINKS + 1}
"""
FINDINGS = """
MATCH (i:Inconsistency {projectId: $p})-[v:INVOLVES]->(x {projectId: $p})
RETURN i.id AS id, i.title AS title, i.severity AS severity, i.status AS status, i.type AS type,
       v.role AS role, x.id AS involvedId
ORDER BY i.id, x.id
"""
ELEMENT = """
MATCH (e:Element {projectId: $p, id: $id})
OPTIONAL MATCH (a:Artifact {projectId: $p})-[:HAS_VERSION]->(:ArtifactVersion {projectId: $p})
               -[:HAS_ELEMENT]->(e)
RETURN properties(e) AS props, [l IN labels(e) WHERE l <> 'Element'][0] AS label,
       a.id AS artifactId, a.path AS artifactPath
LIMIT 1
"""
ELEMENT_LINKS = f"""
MATCH (e:Element {{projectId: $p, id: $id}})
MATCH (l:TraceLink {{projectId: $p, active: true}})-[:SOURCE|TARGET]->(e)
MATCH (l)-[:SOURCE]->(s:Element {{projectId: $p}})
MATCH (l)-[:TARGET]->(t:Element {{projectId: $p}})
RETURN l.id AS id, s.id AS sourceId, t.id AS targetId, l.linkType AS linkType, l.approach AS approach,
       l.confidence AS confidence, l.status AS status, l.reviewedBy AS reviewedBy, l.rationale AS rationale,
       l.reviewedAt AS reviewedAt, l.reviewReason AS reviewReason
ORDER BY l.confidence DESC, l.id LIMIT {MAX_DETAIL_LINKS}
"""
ELEMENT_FINDINGS = """
MATCH (e:Element {projectId: $p, id: $id})
OPTIONAL MATCH (l:TraceLink {projectId: $p, active: true})-[:SOURCE|TARGET]->(e)
WITH e, collect(l) AS links
UNWIND [e] + links AS x
MATCH (i:Inconsistency {projectId: $p})-[v:INVOLVES]->(x)
RETURN DISTINCT i.id AS id, i.title AS title, i.severity AS severity, i.status AS status, i.type AS type,
       i.description AS description, i.evidence AS evidence, v.role AS role
ORDER BY id
"""
NEIGHBOURS = f"""
MATCH (e:Element {{projectId: $p, id: $id}})-[r:CONTAINS|DEPENDS_ON|PROVIDES|REQUIRES]-(n:Element {{projectId: $p}})
RETURN type(r) AS relation, startNode(r).id = e.id AS out, n.id AS id, n.name AS name,
       [l IN labels(n) WHERE l <> 'Element'][0] AS label
ORDER BY relation, out DESC, id LIMIT {MAX_NEIGHBOURS}
"""
INTERNAL = {"embedding", "embeddingModel"}


def _compact(row: dict[str, Any], *drop: str) -> dict[str, Any]:
    return {k: v for k, v in row.items() if v is not None and k not in drop}


async def trace_view(rows: TxRows, project: str) -> dict[str, Any]:
    p = {"p": project}
    elements = await rows(ELEMENTS, p)
    links = await rows(LINKS, p)
    findings: dict[str, dict[str, Any]] = {}
    for r in await rows(FINDINGS, p):
        f = findings.setdefault(
            r["id"], {k: r[k] for k in ("id", "title", "severity", "status", "type")} | {"involved": []}
        )
        f["involved"].append(_compact({"id": r["involvedId"], "role": r["role"]}))
    return {
        "elements": [
            _compact(r, "sentenceIndex", "startLine") | {"order": r["sentenceIndex"] or r["startLine"] or 0}
            for r in elements[:MAX_ELEMENTS]
        ],
        "links": [_compact(r) for r in links[:MAX_LINKS]],
        "findings": list(findings.values()),
        "truncated": len(elements) > MAX_ELEMENTS or len(links) > MAX_LINKS,
    }


async def element(rows: TxRows, project: str, element_id: str) -> dict[str, Any] | None:
    """Detail of one element, or None if the project has no such element."""
    p = {"p": project, "id": element_id}
    head = await rows(ELEMENT, p)
    if not head:
        return None
    h = head[0]
    props = {k: v for k, v in h["props"].items() if k not in INTERNAL}
    return {
        "element": props
        | _compact({"label": h["label"], "artifactId": h["artifactId"], "artifactPath": h["artifactPath"]}),
        "links": [_compact(r) for r in await rows(ELEMENT_LINKS, p)],
        "findings": [_compact(r) for r in await rows(ELEMENT_FINDINGS, p)],
        "neighbours": [
            {
                "relation": r["relation"],
                "direction": "out" if r["out"] else "in",
                "id": r["id"],
                "name": r["name"] or "",
                "label": r["label"],
            }
            for r in await rows(NEIGHBOURS, p)
        ],  # fmt: skip
    }

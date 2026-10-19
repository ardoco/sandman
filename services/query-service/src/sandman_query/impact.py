"""Change impact of one ingestion (T6.3): changed elements and the elements of other artifacts they affect.

A deterministic traversal in one read transaction. Every statement is parameterized and anchored to the project.
"""

from collections.abc import Iterator
from typing import Any

from sandman_query.reader import Reader, TxRows

MAX_PATHS = 3
MAX_CHANGED = 1000
MAX_FRONTIER = 2000
EXCERPT = 300
TIMEOUT = 20.0

INGESTION = (
    "MATCH (i:Ingestion {id: $ing, projectId: $p}) RETURN i.seq AS seq, i.sourceRevision AS revision, i.createdAt AS at"
)
ACTIVATED = """
MATCH (i:Ingestion {id: $ing, projectId: $p})-[:ACTIVATED]->(nv:ArtifactVersion {projectId: $p})
MATCH (a:Artifact {projectId: $p})-[:HAS_VERSION]->(nv)
OPTIONAL MATCH (a)-[:HAS_VERSION]->(ov:ArtifactVersion {projectId: $p})<-[:ACTIVATED]-(j:Ingestion {projectId: $p})
WHERE j.seq < i.seq
WITH a, nv, ov, j ORDER BY j.seq DESC
WITH a, nv, collect(ov)[0] AS ov
RETURN a.id AS artifact, a.path AS path, nv.id AS new, ov.id AS old
"""
DELETED = """
MATCH (i:Ingestion {id: $ing, projectId: $p})-[:DELETED]->(a:Artifact {projectId: $p})
OPTIONAL MATCH (a)-[:HAS_VERSION]->(ov:ArtifactVersion {projectId: $p})<-[:ACTIVATED]-(j:Ingestion {projectId: $p})
WHERE j.seq < i.seq
WITH a, ov, j ORDER BY j.seq DESC
WITH a, collect(ov)[0] AS ov
RETURN a.id AS artifact, a.path AS path, ov.id AS old
"""
OTHER_VERSIONS = """
MATCH (a:Artifact {projectId: $p})-[:HAS_VERSION]->(v:ArtifactVersion {projectId: $p})
WHERE a.id IN $artifacts AND NOT v.id IN $versions
RETURN DISTINCT a.id AS artifact
"""
ELEMENTS = """
MATCH (v:ArtifactVersion {projectId: $p}) WHERE v.id IN $versions
MATCH (v)-[:HAS_ELEMENT]->(e:Element {projectId: $p})
RETURN v.id AS version, e.id AS id, e.key AS key, e.contentHash AS hash, e.kind AS kind,
       [l IN labels(e) WHERE l <> 'Element'][0] AS label
ORDER BY key
"""
NEIGHBOURS = """
MATCH (e:Element {projectId: $p}) WHERE e.id IN $ids
MATCH (e)<-[:SOURCE]-(l:TraceLink {projectId: $p})-[:TARGET]->(n:Element {projectId: $p})
WHERE l.status <> 'rejected' AND (l.active OR e.id IN $old)
RETURN e.id AS src, n.key AS key, 'TRACE_LINK' AS edge
UNION ALL
MATCH (e:Element {projectId: $p}) WHERE e.id IN $ids
MATCH (e)<-[:TARGET]-(l:TraceLink {projectId: $p})-[:SOURCE]->(n:Element {projectId: $p})
WHERE l.status <> 'rejected' AND (l.active OR e.id IN $old)
RETURN e.id AS src, n.key AS key, 'TRACE_LINK' AS edge
UNION ALL
MATCH (e:Element {projectId: $p}) WHERE e.id IN $ids
MATCH (e)-[:DEPENDS_ON]-(n:Element {projectId: $p})
RETURN e.id AS src, n.key AS key, 'DEPENDS_ON' AS edge
UNION ALL
MATCH (e:Element {projectId: $p}) WHERE e.id IN $ids
MATCH (n:Element {projectId: $p})-[:CONTAINS]->(e)
RETURN e.id AS src, n.key AS key, 'CONTAINS' AS edge
"""
CURRENT = """
MATCH (a:Artifact {projectId: $p})-[:CURRENT_VERSION]->(:ArtifactVersion {projectId: $p})
      -[:HAS_ELEMENT]->(c:Element {projectId: $p})
WHERE c.key IN $keys
RETURN c.id AS id, c.key AS key, a.path AS path, c.kind AS kind, c.name AS name, c.text AS text,
       [l IN labels(c) WHERE l <> 'Element'][0] AS label
"""


def change_set(old: dict[str, dict[str, Any]], new: dict[str, dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """(change, element) per changed key; old and new map key -> element row of one artifact."""
    out = [
        ("added" if k not in old else "modified", e)
        for k, e in new.items()
        if k not in old or old[k]["hash"] != e["hash"]
    ]
    out += [("removed", e) for k, e in old.items() if k not in new]
    return out


def paths(
    key: str, parents: dict[str, list[tuple[str, str]]], roots: dict[str, bool]
) -> Iterator[list[dict[str, Any]]]:
    """Shortest paths from a changed element to key; the start item carries via for removed elements."""
    if key in roots:
        yield [{"key": key, "edge": "start"} | ({"via": "previous-version"} if roots[key] else {})]
        return
    for parent, edge in sorted(set(parents[key])):
        for head in paths(parent, parents, roots):
            yield [*head, {"key": key, "edge": edge}]


async def traverse(
    rows: TxRows, project: str, roots: dict[str, tuple[str, bool]], depth: int, limit: int
) -> tuple[list[dict[str, Any]], bool]:
    """Affected elements (ordered) and the truncation flag; roots map key -> (element id, previous-version)."""
    truncated = len(roots) > MAX_FRONTIER
    start = dict(sorted(roots.items())[:MAX_FRONTIER])
    flags = {k: old for k, (_, old) in start.items()}
    frontier = {i: k for k, (i, _) in start.items()}
    old_ids = [i for k, (i, old) in start.items() if old]
    dist: dict[str, int] = dict.fromkeys(start, 0)
    parents: dict[str, list[tuple[str, str]]] = {}
    info: dict[str, dict[str, Any]] = {}
    for level in range(1, depth + 1):
        if not frontier:
            break
        found = await rows(NEIGHBOURS, {"p": project, "ids": list(frontier), "old": old_ids})
        current = {r["key"]: r for r in await rows(CURRENT, {"p": project, "keys": sorted({r["key"] for r in found})})}
        previous, frontier, old_ids = frontier, {}, []
        for r in sorted(found, key=lambda r: (r["key"], r["edge"], r["src"])):
            key, c = r["key"], current.get(r["key"])
            if c is None or dist.get(key, level) < level:
                continue
            dist[key] = level
            parents.setdefault(key, [])
            parents[key].append((previous[r["src"]], r["edge"]))
            info[key] = c
            frontier[c["id"]] = key
        if len(frontier) > MAX_FRONTIER:
            truncated = True
            frontier = dict(sorted(frontier.items(), key=lambda kv: kv[1])[:MAX_FRONTIER])
    affected = []
    for key in dist:
        if key in start:
            continue
        found_paths = [
            p for p in _limited(paths(key, parents, flags)) if p[0]["key"].split("#", 1)[0] != key.split("#", 1)[0]
        ]
        if found_paths:
            c = info[key]
            affected.append(
                {"key": key, "artifactPath": c["path"], "label": c["label"], "kind": c["kind"], "name": c["name"],
                 "excerpt": (c.get("text") or "")[:EXCERPT], "distance": dist[key], "paths": found_paths[:MAX_PATHS]}
            )  # fmt: skip
    affected.sort(key=lambda a: (a["distance"], a["artifactPath"], a["key"]))
    return affected[:limit], truncated or len(affected) > limit


def _limited(it: Iterator[list[dict[str, Any]]], cap: int = 50) -> list[list[dict[str, Any]]]:
    out = []
    for p in it:
        out.append(p)
        if len(out) >= cap:
            break
    return out


async def report(reader: Reader, project: str, ingestion: str, depth: int, limit: int) -> dict[str, Any] | None:
    """The impact report without workflow state, or None for an unknown ingestion."""

    async def work(rows: TxRows) -> dict[str, Any] | None:
        p = {"p": project, "ing": ingestion}
        head = await rows(INGESTION, p)
        if not head:
            return None
        pairs = {r["artifact"]: r for r in await rows(ACTIVATED, p)}
        for r in await rows(DELETED, p):
            pairs[r["artifact"]] = r | {"new": None}
        versions = sorted({v for r in pairs.values() for v in (r["old"], r["new"]) if v})
        loaded: dict[str, dict[str, dict[str, Any]]] = {v: {} for v in versions}
        for r in await rows(ELEMENTS, {"p": project, "versions": versions}):
            loaded[r["version"]][r["key"]] = r
        warnings = []
        missing = {r["artifact"] for r in pairs.values() if r["old"] is None}
        other = {"p": project, "artifacts": sorted(missing), "versions": versions}
        unknown = {r["artifact"] for r in await rows(OTHER_VERSIONS, other)} if missing else set()
        changes: list[tuple[str, dict[str, Any], str]] = []
        for art, r in sorted(pairs.items()):
            if art in unknown:
                warnings.append(f"The previous version of {r['path']} is not available; its changes are not listed.")
                continue
            old = loaded.get(r["old"], {}) if r["old"] else {}
            new = loaded.get(r["new"], {}) if r["new"] else {}
            changes += [(c, e, r["path"]) for c, e in change_set(old, new)]
        changes.sort(key=lambda x: (x[2], x[1]["key"], x[0]))
        changed = [
            {"key": e["key"], "artifactPath": path, "label": e["label"], "kind": e["kind"], "change": c}
            for c, e, path in changes
        ]  # fmt: skip
        roots = {e["key"]: (e["id"], c == "removed") for c, e, _ in changes}
        affected, truncated = await traverse(rows, project, roots, depth, limit)
        h = head[0]
        doc: dict[str, Any] = {"ingestionId": ingestion, "seq": h["seq"]}
        if h.get("revision"):
            doc["sourceRevision"] = h["revision"]
        doc |= {"changed": changed[:MAX_CHANGED], "affected": affected,
                "truncated": truncated or len(changed) > MAX_CHANGED, "warnings": warnings,
                "createdAt": h["at"], "hasChanges": bool(pairs)}  # fmt: skip
        return doc

    return await reader.transaction(work, TIMEOUT)

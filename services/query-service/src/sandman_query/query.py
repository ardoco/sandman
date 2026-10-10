"""Guarded query execution, the NL intent stub and exports (all through Reader, the single read path)."""

import csv
import io
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from neo4j.exceptions import ClientError, Neo4jError
from sandman_agent.contracts import Contracts
from sandman_agent.kg import MemoryLimitError
from starlette.exceptions import HTTPException

from sandman_query.auth import Principal
from sandman_query.guard import GuardError, check
from sandman_query.reader import Reader, ScopeViolation, check_scope

log = logging.getLogger(__name__)

USER_TIMEOUT_MS, SERVICE_TIMEOUT_MS = 10_000, 30_000
RESERVED_PARAMS = {"projectId", "cypher", "timeout"}  # bound by the service
USER_TRANSITIONS = {
    "OPEN": ["DEFERRED", "ACCEPTED", "REJECTED", "RESOLVED"],
    "REOPENED": ["DEFERRED", "ACCEPTED", "REJECTED", "RESOLVED"],
    "DEFERRED": ["OPEN", "ACCEPTED", "REJECTED", "RESOLVED"],
    "ACCEPTED": ["OPEN"],
    "REJECTED": ["OPEN"],
    "RESOLVED": [],
}  # mirrors the inconsistency-agent lifecycle (ADR-0007)

TRACELINKS = """
MATCH (l:TraceLink {projectId: $projectId, active: true})
WHERE ($approach IS NULL OR l.approach = $approach) AND l.confidence >= $minConfidence
  AND ($includeRejected OR l.status <> 'rejected')
MATCH (l)-[:SOURCE]->(s:Element)<-[:HAS_ELEMENT]-(:ArtifactVersion)<-[:CURRENT_VERSION]-(:Artifact),
      (l)-[:TARGET]->(t:Element)<-[:HAS_ELEMENT]-(:ArtifactVersion)<-[:CURRENT_VERSION]-(:Artifact)
RETURN s.key AS sourceKey, t.key AS targetKey, l.linkType AS linkType, l.approach AS approach,
       l.approachVersion AS approachVersion, l.confidence AS confidence, l.status AS status, l.runId AS runId
ORDER BY sourceKey, targetKey, approach, approachVersion
"""
TRACELINK_COLUMNS = [
    "source_key",
    "target_key",
    "link_type",
    "approach",
    "approach_version",
    "confidence",
    "status",
    "run_id",
]
INCONSISTENCIES = """
MATCH (i:Inconsistency {projectId: $projectId})
OPTIONAL MATCH (i)-[:HAS_STATUS_CHANGE]->(c:StatusChange)
WITH i, c ORDER BY c.at
WITH i, collect(c) AS changes
RETURN properties(i) AS i, [h IN changes | properties(h)] AS history ORDER BY i.id
"""
INVOLVED = """
MATCH (i:Inconsistency {projectId: $projectId})-[inv:INVOLVES]->(x)
OPTIONAL MATCH (x)-[:SOURCE]->(s:Element)
OPTIONAL MATCH (x)-[:TARGET]->(t:Element)
OPTIONAL MATCH (x)<-[:HAS_ELEMENT]-(v:ArtifactVersion)
RETURN i.id AS id, inv.role AS role, labels(x) AS labels, properties(x) AS x, v.id AS version, s.key AS sk, t.key AS tk
ORDER BY id, role, x.key, x.id
"""
ARTIFACTS = "MATCH (a:Artifact {projectId: $projectId}) RETURN a.id AS id, a.path AS path"


class Intents:
    """Regex intents of the NL stub (config/query/intents.yaml) with fixed, anchored Cypher templates."""

    def __init__(self, path: Path, contracts: Contracts) -> None:
        raw = contracts.load_config("query-intents", path)["intents"]
        self.items = [(i["id"], re.compile(i["pattern"], re.IGNORECASE), i["cypher"].strip()) for i in raw]
        for _, _, cypher in self.items:
            check(cypher)  # templates must pass the guard, scoped

    def match(self, question: str) -> tuple[str, str, dict[str, str]] | None:
        q = re.sub(r"\s+", " ", question).strip().rstrip("?!. ")
        for id_, pattern, cypher in self.items:
            if m := pattern.fullmatch(q):
                return id_, cypher, {k: v for k, v in m.groupdict().items() if v is not None}
        return None

    def supported(self) -> list[str]:
        return [i for i, _, _ in self.items]


class Runner:
    """Scope guard, limits, read transaction and result post-check."""

    def __init__(self, reader: Reader, memory_limit: str = "100 MB") -> None:
        self.reader, self.memory_limit = reader, memory_limit

    async def run(
        self,
        who: Principal,
        project: str | None,
        cypher: str,
        params: dict[str, Any],
        limit: int = 200,
        timeout_ms: int = USER_TIMEOUT_MS,
    ) -> dict[str, Any]:
        """QueryResult; HTTP 422 for guard rejections, DB errors, timeouts, memory limit and scope violations."""
        if params.keys() & RESERVED_PARAMS:
            raise HTTPException(422, f"parameter names {sorted(RESERVED_PARAMS)} are reserved")
        try:
            checked = check(cypher, scoped=project is not None)
        except GuardError as e:
            raise HTTPException(422, str(e)) from None
        text = (
            checked.text
            + ("" if checked.has_limit else f" LIMIT {limit + 1}")
            + f" QUERY MEMORY LIMIT {self.memory_limit}"
        )
        cap = USER_TIMEOUT_MS if who.kind == "user" else SERVICE_TIMEOUT_MS
        bound = {**params, **({"projectId": project} if project else {})}
        started = time.monotonic()
        try:
            columns, rows = await self.reader.run(text, bound, min(timeout_ms, cap) / 1000, max_rows=limit + 1)
        except MemoryLimitError:
            raise HTTPException(422, f"query exceeded the memory limit of {self.memory_limit}") from None
        except TimeoutError:
            raise HTTPException(422, "query timed out") from None
        except ClientError as e:
            raise HTTPException(422, f"query rejected by the database: {e.message}") from None
        except Neo4jError as e:
            if "timeout" in (e.message or "").lower() or "aborted" in (e.message or "").lower():
                raise HTTPException(422, "query timed out") from None
            raise HTTPException(503, "knowledge graph unavailable") from None
        truncated = len(rows) > limit
        rows = rows[:limit]
        if project:
            try:
                check_scope(rows, project)
            except ScopeViolation as e:
                raise HTTPException(422, str(e)) from None
        return {"columns": columns, "rows": rows, "truncated": truncated,
                "elapsedMs": int((time.monotonic() - started) * 1000)}  # fmt: skip

    async def nl(
        self, who: Principal, intents: Intents, project: str, question: str, mode: str, execute: bool
    ) -> dict[str, Any]:
        """NL stub: a regex intent mapped to a fixed Cypher template."""
        found = intents.match(question)
        if found is None:
            raise HTTPException(
                422, "question not understood (M0 stub, no LLM); supported intents: " + ", ".join(intents.supported())
            )
        _, cypher, extracted = found
        out: dict[str, Any] = {"columns": [], "rows": [], "truncated": False, "elapsedMs": 0}
        if mode == "cypher-only":
            del out["rows"]
        elif execute:
            out = await self.run(who, project, cypher, dict(extracted))
        return {**out, "cypher": cypher, "params": {"projectId": project, **extracted},
                "attempts": [{"cypher": cypher}], "warnings": []}  # fmt: skip

    # ---------------------------------------------------------------- exports

    async def tracelinks(
        self, project: str, approach: str | None, min_confidence: float, rejected: bool
    ) -> list[dict[str, Any]]:
        params = {
            "projectId": project,
            "approach": approach,
            "minConfidence": min_confidence,
            "includeRejected": rejected,
        }
        rows = await self.reader.rows(TRACELINKS, params, SERVICE_TIMEOUT_MS / 1000)
        return [dict(r) for r in rows]

    async def inconsistencies(self, project: str) -> list[dict[str, Any]]:
        """InconsistencyDetail items (with history), assembled like the inconsistency-agent's detail view."""
        p = {"projectId": project}
        t = SERVICE_TIMEOUT_MS / 1000
        items = await self.reader.rows(INCONSISTENCIES, p, t)
        involved = await self.reader.rows(INVOLVED, p, t)
        paths = {a["id"]: a["path"] for a in await self.reader.rows(ARTIFACTS, p, t)}
        by_id: dict[str, list[dict[str, Any]]] = {}
        for row in involved:
            by_id.setdefault(row["id"], []).append(row)
        out = []
        for item in items:
            props = item["i"]
            entries, artifact_paths = [], []
            for row in by_id.get(props["id"], []):
                x = row["x"]
                if "key" in x:
                    path = paths.get(x["key"].split("#", 1)[0], "")
                    artifact_paths.append(path)
                    label = next((lb for lb in row["labels"] if lb != "Element"), "Element")
                    element = {"id": x["id"], "key": x["key"], "label": label, "name": x["name"], "artifactPath": path,
                               "artifactVersionId": row["version"], "startLine": x.get("startLine"),
                               "endLine": x.get("endLine"), "excerpt": x.get("text")}  # fmt: skip
                    entries.append(
                        {"role": row["role"], "element": {k: v for k, v in element.items() if v is not None}}
                    )
                else:
                    link = {"id": x["id"], "approach": x["approach"], "confidence": x["confidence"],
                            "sourceKey": row["sk"], "targetKey": row["tk"]}  # fmt: skip
                    entries.append({"role": row["role"], "traceLink": link})
                    artifact_paths += [paths.get(k.split("#", 1)[0], "") for k in (row["sk"], row["tk"]) if k]
            keys = ("id", "projectId", "type", "severity", "title", "status", "verification", "confidence")
            detail = {k: props[k] for k in (*keys, "detector", "detectorVersion", "fingerprint") if k in props}
            detail["artifactPaths"] = sorted({p for p in artifact_paths if p})
            detail.update(createdAt=props["createdAt"], updatedAt=props["updatedAt"],
                          description=props.get("description", ""), involved=entries)  # fmt: skip
            if props.get("evidence"):
                detail["evidence"] = json.loads(props["evidence"])
            detail["history"] = [{k: h[k] for k in ("from", "to", "actor", "reason", "at") if h.get(k) is not None}
                                 for h in item["history"]]  # fmt: skip
            detail["allowedTransitions"] = USER_TRANSITIONS.get(props["status"], [])
            out.append(detail)
        return out


def to_csv(columns: list[str], rows: list[list[Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return buf.getvalue()


def inconsistencies_csv(items: list[dict[str, Any]]) -> str:
    """One row per inconsistency; evidence, involved elements and history as JSON cells."""
    cols = ["id", "type", "severity", "title", "status", "verification", "detector", "detectorVersion", "confidence",
            "createdAt", "updatedAt"]  # fmt: skip
    rows = [
        [i.get(c, "") for c in cols] + [json.dumps(i[c], ensure_ascii=False) for c in ("involved", "history")]
        for i in items
    ]
    return to_csv([*cols, "involved", "history"], rows)

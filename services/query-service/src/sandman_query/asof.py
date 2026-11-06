"""Time-aware queries (T7.4): the artifact versions that were current after one ingestion."""

import re
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from starlette.exceptions import HTTPException

from sandman_query.reader import Reader

AS_OF: ContextVar[list[str] | None] = ContextVar("as_of_versions", default=None)  # set by NlService.answer
CURRENT = re.compile(
    r"\(\s*:Artifact\s*(?:\{[^}]*\})?\s*\)\s*-\s*\[\s*:CURRENT_VERSION\s*\]\s*->"
    r"\s*\(\s*(\w*)\s*:ArtifactVersion\s*(?:\{[^}]*\})?\s*\)"
)
INGESTIONS = """
MATCH (i:Ingestion {projectId: $projectId})
RETURN i.seq AS seq, i.createdAt AS createdAt, i.sourceRevision AS sourceRevision ORDER BY seq
"""
ACTIVATED = """
MATCH (i:Ingestion {projectId: $projectId})-[:ACTIVATED]->(v:ArtifactVersion)<-[:HAS_VERSION]-(a:Artifact)
RETURN i.seq AS seq, a.id AS artifactId, v.id AS versionId ORDER BY seq
"""
DELETED = """
MATCH (i:Ingestion {projectId: $projectId})-[:DELETED]->(a:Artifact)
RETURN i.seq AS seq, a.id AS artifactId ORDER BY seq
"""
WARNING = "asOf: trace links and inconsistencies keep no end time; they show their state now, limited to old elements"


def scope(cypher: str) -> str | None:
    """The query for the versions in $versionIds, or None if it does not read CURRENT_VERSION in a rewritable way."""
    text, n = CURRENT.subn(lambda m: f"({m[1]}:ArtifactVersion {{id: asOfVid, projectId: $projectId}})", cypher)
    return f"UNWIND $versionIds AS asOfVid {text}" if n and "CURRENT_VERSION" not in text else None


def applicable(cypher: str) -> bool:
    return scope(cypher) is not None or "$versionIds" in cypher


async def resolve(
    reader: Reader, project: str, at: datetime | None, revision: str | None
) -> tuple[list[str], dict[str, Any]]:
    """(version ids, asOf info) of the cutoff ingestion; HTTP 422 if no or both inputs fit or retention removed it."""
    if (at is None) == (revision is None):
        raise HTTPException(422, "send exactly one of asOf and asOfRevision")
    p = {"projectId": project}
    if at is not None and at.tzinfo is None:
        at = at.replace(tzinfo=UTC)

    async def read(tx: Any) -> tuple[list[dict[str, Any]], ...]:
        return await tx(INGESTIONS, p), await tx(ACTIVATED, p), await tx(DELETED, p)

    ingestions, activated, deleted = await reader.transaction(read)
    oldest = ingestions[0] if ingestions else None
    if at is not None:
        hits = [i for i in ingestions if datetime.fromisoformat(i["createdAt"]) <= at]
    else:
        hits = [i for i in ingestions if i["sourceRevision"] == revision]
        if not hits:
            raise HTTPException(422, f"no ingestion with source revision {revision} (unknown, or removed by retention)")
    if not hits:
        if oldest is not None and oldest["seq"] > 1:
            raise HTTPException(
                422,
                f"asOf is older than the history that retention kept; the oldest available ingestion is from "
                f"{oldest['createdAt']}",
            )
        return [], {"ingestionSeq": 0}
    cutoff = hits[-1]
    events = sorted(
        [(r["seq"], 0, r["artifactId"], r["versionId"]) for r in activated]
        + [(r["seq"], 1, r["artifactId"], None) for r in deleted]
    )
    state: dict[str, str] = {}
    for seq, _, artifact, version in events:
        if seq > cutoff["seq"]:
            break
        if version:
            state[artifact] = version
        else:
            state.pop(artifact, None)
    info = {"ingestionSeq": cutoff["seq"], "ingestedAt": cutoff["createdAt"]}
    if cutoff["sourceRevision"]:
        info["sourceRevision"] = cutoff["sourceRevision"]
    return sorted(state.values()), info

"""Per-project knowledge-graph statistics from read transactions (Stage 1 §4 "current" rules), cached for 60 s.

Every figure is its own query: a slow or failing query turns only that figure into None (shown as n/a).
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sandman_dashboard.graph import GraphError, GraphService

CACHE = 60.0
MAX_PROJECTS = 100
CURRENT = "(:Artifact {projectId: $p})-[:CURRENT_VERSION]->(:ArtifactVersion)-[:HAS_ELEMENT]->"
CURRENT_ENDPOINT = "<-[:HAS_ELEMENT]-(:ArtifactVersion)<-[:CURRENT_VERSION]-(:Artifact {projectId: $p})"


@dataclass
class Stats:
    artifacts_current: int | None = None
    artifacts_deleted: int | None = None
    versions: int | None = None
    elements: dict[str, int] | None = None
    links: dict[str, int] | None = None
    inconsistency_status: dict[str, int] | None = None
    inconsistency_type: dict[str, int] | None = None
    ingestion: dict[str, Any] | None = None

    @property
    def elements_total(self) -> int | None:
        return None if self.elements is None else sum(self.elements.values())

    @property
    def links_total(self) -> int | None:
        return None if self.links is None else sum(self.links.values())

    @property
    def inconsistencies_total(self) -> int | None:
        return None if self.inconsistency_status is None else sum(self.inconsistency_status.values())


def _count(rows: list[list[Any]]) -> int:
    return int(rows[0][0]) if rows else 0


def _grouped(rows: list[list[Any]]) -> dict[str, int]:
    return {str(k): int(n) for k, n in rows if k is not None}


class ProjectStats:
    def __init__(self, graph: GraphService, clock: Callable[[], float] = time.monotonic) -> None:
        self.graph, self.clock = graph, clock
        self._cache: dict[str, tuple[float, Stats]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _figure(self, cypher: str, project: str) -> list[list[Any]] | None:
        try:
            return await self.graph.fixed(cypher, {"p": project})
        except (GraphError, OSError):
            return None

    async def compute(self, project: str) -> Stats:
        q = self._figure
        deleted, current, versions, elements, links, incons, ingestion = await asyncio.gather(
            q("MATCH (a:Artifact {projectId: $p}) WHERE a.deletedAt IS NOT NULL RETURN count(a)", project),
            q(
                "MATCH (a:Artifact {projectId: $p})-[:CURRENT_VERSION]->(:ArtifactVersion) RETURN count(DISTINCT a)",
                project,
            ),
            q("MATCH (v:ArtifactVersion {projectId: $p}) RETURN count(v)", project),
            q(
                f"MATCH {CURRENT}(e:Element) UNWIND labels(e) AS l WITH l, e WHERE l <> 'Element' "
                "RETURN l, count(DISTINCT e) ORDER BY l",
                project,
            ),
            q(
                "MATCH (t:TraceLink {projectId: $p, active: true})-[:SOURCE]->(:Element)"
                f"{CURRENT_ENDPOINT} MATCH (t)-[:TARGET]->(:Element){CURRENT_ENDPOINT} "
                "RETURN t.approach AS approach, count(DISTINCT t) AS n ORDER BY approach",
                project,
            ),
            q("MATCH (i:Inconsistency {projectId: $p}) RETURN i.status, i.type, count(i)", project),
            q(
                "MATCH (i:Ingestion {projectId: $p}) WITH i ORDER BY i.seq DESC LIMIT 1 "
                "OPTIONAL MATCH (i)-[:ACTIVATED]->(v) WITH i, count(v) AS activated "
                "OPTIONAL MATCH (i)-[:DELETED]->(a) "
                "RETURN i.seq, i.createdAt, i.sourceRevision, activated, count(a)",
                project,
            ),
        )
        stats = Stats(
            artifacts_current=None if current is None else _count(current),
            artifacts_deleted=None if deleted is None else _count(deleted),
            versions=None if versions is None else _count(versions),
            elements=None if elements is None else _grouped(elements),
            links=None if links is None else _grouped(links),
        )
        if incons is not None:
            by_status: dict[str, int] = {}
            by_type: dict[str, int] = {}
            for status, type_, n in incons:
                by_status[str(status)] = by_status.get(str(status), 0) + int(n)
                by_type[str(type_)] = by_type.get(str(type_), 0) + int(n)
            stats.inconsistency_status, stats.inconsistency_type = by_status, by_type
        if ingestion is not None:
            stats.ingestion = (
                {"seq": ingestion[0][0], "createdAt": ingestion[0][1], "sourceRevision": ingestion[0][2],
                 "activated": ingestion[0][3], "deleted": ingestion[0][4]}
                if ingestion
                else {}
            )  # fmt: skip
        return stats

    async def get(self, project: str) -> Stats:
        cached = self._cache.get(project)
        if cached and self.clock() - cached[0] < CACHE:
            return cached[1]
        async with self._locks.setdefault(project, asyncio.Lock()):
            cached = self._cache.get(project)
            if cached and self.clock() - cached[0] < CACHE:
                return cached[1]
            stats = await self.compute(project)
            self._cache[project] = (self.clock(), stats)
            return stats

    async def many(self, projects: list[str], parallel: int = 4) -> dict[str, Stats]:
        gate = asyncio.Semaphore(parallel)

        async def one(p: str) -> tuple[str, Stats]:
            async with gate:
                return p, await self.get(p)

        return dict(await asyncio.gather(*(one(p) for p in projects[:MAX_PROJECTS])))

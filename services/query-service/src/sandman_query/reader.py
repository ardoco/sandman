"""Read-only KG access in a driver read transaction, with JSON conversion and the result post-check."""

import asyncio
import logging
import math
from collections.abc import Mapping
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncManagedTransaction, unit_of_work
from neo4j.exceptions import Neo4jError
from neo4j.graph import Node, Path, Relationship
from sandman_agent.kg import MemoryLimitError

log = logging.getLogger(__name__)


class ScopeViolation(Exception):
    """A result contained data of another project."""


def jsonable(v: Any) -> Any:
    """Graph values as property maps plus _labels/_type (no internal ids); other values as JSON values."""
    if isinstance(v, Node):
        return {**{k: jsonable(x) for k, x in v.items()}, "_labels": sorted(v.labels)}
    if isinstance(v, Relationship):
        return {**{k: jsonable(x) for k, x in v.items()}, "_type": v.type}
    if isinstance(v, Path):
        return [
            jsonable(x) for pair in zip(v.nodes, [*v.relationships, None], strict=False) for x in pair if x is not None
        ]
    if isinstance(v, Mapping):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple | set):
        return [jsonable(x) for x in v]
    if isinstance(v, bool | int | str) or v is None:
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def check_scope(value: Any, project: str) -> None:
    """Every map with a projectId must carry the requested project, at any depth."""
    if isinstance(value, dict):
        if "projectId" in value and value["projectId"] != project:
            log.error("scope violation", extra={"fields": {"event": "scope_violation", "projectId": project}})
            raise ScopeViolation("result contains data of another project")
        for x in value.values():
            check_scope(x, project)
    elif isinstance(value, list):
        for x in value:
            check_scope(x, project)


class Reader:
    """The single read path of the service (queries, exports, project list)."""

    def __init__(self, uri: str, user: str | None, password: str | None, driver: AsyncDriver | None = None) -> None:
        self._driver = driver or AsyncGraphDatabase.driver(uri, auth=(user, password or "") if user else None)

    async def close(self) -> None:
        await self._driver.close()

    async def run(
        self, cypher: str, params: Mapping[str, Any], timeout: float = 10.0, max_rows: int | None = None
    ) -> tuple[list[str], list[list[Any]]]:
        """(columns, rows) from a read transaction; at most max_rows rows are fetched."""

        async def work(tx: AsyncManagedTransaction) -> tuple[list[str], list[list[Any]]]:
            try:
                result = await tx.run(cypher, dict(params))  # type: ignore[arg-type]
                rows: list[list[Any]] = []
                async for record in result:
                    rows.append([jsonable(x) for x in record.values()])
                    if max_rows is not None and len(rows) >= max_rows:
                        break
                return list(result.keys()), rows
            except Neo4jError as e:
                # a plain exception stops the driver's retry of transient errors (Memgraph reports this one as such)
                if "memory limit exceeded" in (e.message or "").lower():
                    raise MemoryLimitError(e.message) from e
                raise

        async with asyncio.timeout(timeout), self._driver.session() as session:
            return await session.execute_read(unit_of_work(timeout=timeout)(work))

    async def rows(self, cypher: str, params: Mapping[str, Any], timeout: float = 10.0) -> list[dict[str, Any]]:
        columns, rows = await self.run(cypher, params, timeout)
        return [dict(zip(columns, r, strict=True)) for r in rows]

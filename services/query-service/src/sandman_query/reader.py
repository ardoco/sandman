"""Read-only KG access in a driver read transaction, with JSON conversion and the result post-check."""

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncManagedTransaction, unit_of_work
from neo4j.exceptions import Neo4jError
from sandman_agent.graphview import jsonable
from sandman_agent.kg import MemoryLimitError

log = logging.getLogger(__name__)


class ScopeViolation(Exception):
    """A result contained data of another project."""


def check_scope(value: Any, project: str) -> None:
    """Every map with a projectId, and every Project node, must belong to the requested project, at any depth."""
    if isinstance(value, dict):
        foreign_project = "Project" in value.get("_labels", ()) and value.get("id") != project
        if foreign_project or ("projectId" in value and value["projectId"] != project):
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

    @property
    def driver(self) -> AsyncDriver:
        return self._driver

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

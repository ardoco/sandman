"""Read-only knowledge-graph access shared by dashboard and query-service: graph exploration and Cypher box.

Every statement runs in a driver read transaction (Memgraph rejects writes there) with timeout and memory limit.
Exploration queries are fixed and parameterized; labels and relationship types only come from the schema contract.
The caller supplies the Cypher guard, so this module does not depend on the query-service.
"""

import asyncio
import json
import logging
import math
import re
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol

import yaml
from neo4j import AsyncDriver, AsyncManagedTransaction, unit_of_work
from neo4j.exceptions import ClientError, Neo4jError
from neo4j.graph import Node, Relationship
from neo4j.graph import Path as GraphPath

from sandman_agent.kg import MemoryLimitError

MAX_NODES = 500
MAX_ROWS = 500
TIMEOUT = 10.0
MAX_HOPS = 6
MAX_TEXT = 5000
MAX_CELL = 2000
CUT_MARK = "... [truncated]"
MAX_PAYLOAD = 2_000_000
CLIENT_ERRORS = (
    ("accessor type", "write statements are not allowed (read-only)"),
    ("misaligned", "write statements are not allowed (read-only)"),
    ("syntax", "syntax error in the statement"),
    ("unbound variable", "unbound variable in the statement"),
    ("not allowed", "the statement is not allowed in a read transaction"),
)
MEMORY_LIMIT = "100 MB"
NODE_ID = re.compile(r"^[0-9]{1,18}$")
PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
log = logging.getLogger(__name__)
DISPLAY = ("name", "key", "id", "path")


def in_project(var: str) -> str:
    return f"($project IS NULL OR {var}.projectId = $project OR ('Project' IN labels({var}) AND {var}.id = $project))"


class Checked(Protocol):
    @property
    def text(self) -> str: ...

    @property
    def has_limit(self) -> bool: ...


def jsonable(v: Any) -> Any:
    """Graph values as property maps plus _labels/_type (no internal ids); other values as JSON values."""
    if isinstance(v, Node):
        return {**{k: jsonable(x) for k, x in v.items()}, "_labels": sorted(v.labels)}
    if isinstance(v, Relationship):
        return {**{k: jsonable(x) for k, x in v.items()}, "_type": v.type}
    if isinstance(v, GraphPath):
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


class GraphError(Exception):
    """A request that is rejected or that the database could not serve; the message is shown to the caller."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def cap(value: Any) -> Any:
    """Table cell with every string cut to MAX_CELL characters."""
    if isinstance(value, str):
        return value if len(value) <= MAX_CELL else value[:MAX_CELL] + CUT_MARK
    if isinstance(value, list):
        return [cap(v) for v in value]
    if isinstance(value, dict):
        return {k: cap(v) for k, v in value.items()}
    return value


def schema_names(contracts_dir: Path) -> tuple[list[str], list[str]]:
    """(node labels, relationship types) from contracts/kg/schema.yaml."""
    doc = yaml.safe_load((contracts_dir / "kg" / "schema.yaml").read_text(encoding="utf-8"))
    labels: list[str] = []
    for name, node in doc["nodes"].items():
        labels.append(name)
        labels.extend(node.get("labels", {}))
    return sorted(set(labels)), sorted(doc["relationships"])


def _props(entity: Node | Relationship) -> dict[str, Any]:
    out = {k: jsonable(v) for k, v in entity.items()}
    return {k: v[:MAX_TEXT] + CUT_MARK if isinstance(v, str) and len(v) > MAX_TEXT else v for k, v in out.items()}


def node_element(node: Node) -> dict[str, Any]:
    props = _props(node)
    title = next((str(props[k]) for k in DISPLAY if props.get(k)), "")
    labels = sorted(node.labels)
    return {
        "group": "nodes",
        "data": {
            "id": f"n{node.element_id}",
            "nid": node.element_id,
            "label": title[:60] or (labels[-1] if labels else ""),
            "labels": labels,
            "props": props,
        },
    }


def edge_element(rel: Relationship) -> dict[str, Any]:
    start, end = rel.start_node, rel.end_node
    assert start is not None and end is not None
    return {
        "group": "edges",
        "data": {
            "id": f"e{rel.element_id}",
            "source": f"n{start.element_id}",
            "target": f"n{end.element_id}",
            "label": rel.type,
            "props": _props(rel),
        },
    }


def collect(values: Iterable[Any], nodes: dict[str, Node], rels: dict[str, Relationship]) -> None:
    """Graph entities in (nested) result values."""
    for v in values:
        if isinstance(v, Node):
            nodes.setdefault(v.element_id, v)
        elif isinstance(v, Relationship):
            rels.setdefault(v.element_id, v)
        elif isinstance(v, GraphPath):
            collect(v.nodes, nodes, rels)
            collect(v.relationships, nodes, rels)
        elif isinstance(v, list | tuple | set):
            collect(v, nodes, rels)
        elif isinstance(v, Mapping):
            collect(v.values(), nodes, rels)


class GraphService:
    def __init__(
        self,
        driver: AsyncDriver,
        labels: list[str],
        rel_types: list[str],
        *,
        guard: Callable[[str, bool], Checked],
        guard_error: type[Exception],
        memory_limit: str = MEMORY_LIMIT,
    ) -> None:
        self.driver, self.labels, self.rel_types = driver, labels, rel_types
        self.guard, self.guard_error, self.memory_limit = guard, guard_error, memory_limit

    async def run(
        self, cypher: str, params: Mapping[str, Any], timeout: float = TIMEOUT, max_rows: int | None = None
    ) -> tuple[list[str], list[list[Any]]]:
        """(columns, raw rows) from a read transaction; at most max_rows rows are fetched."""

        async def work(tx: AsyncManagedTransaction) -> tuple[list[str], list[list[Any]]]:
            try:
                result = await tx.run(cypher, dict(params))
                rows: list[list[Any]] = []
                async for record in result:
                    rows.append(list(record.values()))
                    if max_rows is not None and len(rows) >= max_rows:
                        break
                return list(result.keys()), rows
            except Neo4jError as e:
                if "memory limit exceeded" in (e.message or "").lower():
                    raise MemoryLimitError(e.message) from e
                raise

        try:
            async with asyncio.timeout(timeout), self.driver.session() as session:
                return await session.execute_read(unit_of_work(timeout=timeout)(work))
        except MemoryLimitError:
            raise GraphError(f"query exceeded the memory limit of {self.memory_limit}") from None
        except TimeoutError:
            raise GraphError("query timed out") from None
        except ClientError as e:
            message = (e.message or "").lower()
            log.warning("query rejected", extra={"fields": {"detail": (e.message or "")[:300]}})
            raise GraphError(
                next((text for key, text in CLIENT_ERRORS if key in message), "query rejected by the database")
            ) from None
        except Neo4jError as e:
            if "timeout" in (e.message or "").lower() or "aborted" in (e.message or "").lower():
                raise GraphError("query timed out") from None
            raise GraphError("knowledge graph unavailable", 503) from None
        except OSError:
            raise GraphError("knowledge graph unavailable", 503) from None

    async def fixed(self, cypher: str, params: Mapping[str, Any], max_rows: int | None = None) -> list[list[Any]]:
        """Rows of a fixed, parameterized statement in a read transaction with the query memory limit."""
        return (await self.run(f"{cypher} QUERY MEMORY LIMIT {self.memory_limit}", params, max_rows=max_rows))[1]

    # ------------------------------------------------------------ exploration

    def _filters(self, labels: list[str] | None, types: list[str] | None) -> tuple[list[str] | None, list[str] | None]:
        if labels is not None and not set(labels) <= set(self.labels):
            raise GraphError("unknown label")
        if types is not None and not set(types) <= set(self.rel_types):
            raise GraphError("unknown relationship type")
        return labels or None, types or None

    @staticmethod
    def _project(project: str | None) -> str | None:
        if project and not PROJECT_ID.match(project):
            raise GraphError("invalid project id")
        return project or None

    @staticmethod
    def _node_id(value: str) -> int:
        if not NODE_ID.match(value):
            raise GraphError("invalid node id")
        return int(value)

    async def projects(self) -> list[dict[str, Any]]:
        rows = await self.fixed("MATCH (p:Project) RETURN p.id AS id, p.name AS name ORDER BY p.id", {})
        return [{"id": r[0], "name": r[1]} for r in rows]

    async def _result(
        self,
        rows: list[list[Any]],
        limit: int,
        types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Cytoscape elements of the entities in `rows` plus the relationships among their nodes."""
        nodes: dict[str, Node] = {}
        rels: dict[str, Relationship] = {}
        collect((v for row in rows for v in row), nodes, rels)
        truncated = len(nodes) > limit
        nodes = dict(list(nodes.items())[:limit])
        ids = [int(i) for i in nodes]
        if ids:
            edge_rows = await self.fixed(
                "UNWIND $ids AS i MATCH (a) WHERE id(a) = i MATCH (a)-[r]->(b) "
                "WHERE id(b) IN $ids AND ($types IS NULL OR type(r) IN $types) RETURN r",
                {"ids": ids, "types": types},
            )
            collect((v for row in edge_rows for v in row), {}, rels)
        shown = set(nodes)
        elements = [node_element(n) for n in nodes.values()]
        elements += [
            edge_element(r)
            for r in rels.values()
            if r.start_node is not None
            and r.end_node is not None
            and r.start_node.element_id in shown
            and r.end_node.element_id in shown
            and (types is None or r.type in types)
        ]
        return {"elements": elements, "truncated": truncated}

    async def start(self, project: str | None, label: str | None, labels: list[str] | None = None) -> dict[str, Any]:
        project = self._project(project)
        if label is not None:
            self._filters([label], None)
        wanted, _ = self._filters(labels, None)
        rows = await self.fixed(
            f"MATCH (n) WHERE {in_project('n')} "
            "AND ($label IS NULL OR $label IN labels(n)) "
            "AND ($labels IS NULL OR any(l IN labels(n) WHERE l IN $labels)) RETURN n LIMIT $limit",
            {"project": project, "label": label, "labels": wanted, "limit": MAX_NODES + 1},
        )
        return await self._result(rows, MAX_NODES)

    async def search(self, project: str | None, query: str, labels: list[str] | None = None) -> dict[str, Any]:
        project = self._project(project)
        needle = query.strip().lower()[:200]
        if not needle:
            raise GraphError("empty search")
        wanted, _ = self._filters(labels, None)
        rows = await self.fixed(
            f"MATCH (n) WHERE {in_project('n')} "
            "AND ($labels IS NULL OR any(l IN labels(n) WHERE l IN $labels)) "
            "AND (toLower(n.key) CONTAINS $q OR toLower(n.name) CONTAINS $q OR toLower(n.id) CONTAINS $q) "
            "RETURN n LIMIT $limit",
            {"project": project, "labels": wanted, "q": needle, "limit": MAX_NODES + 1},
        )
        return await self._result(rows, MAX_NODES)

    async def expand(
        self, project: str | None, node_id: str, labels: list[str] | None = None, types: list[str] | None = None
    ) -> dict[str, Any]:
        project = self._project(project)
        wanted, wanted_types = self._filters(labels, types)
        rows = await self.fixed(
            "MATCH (n)-[r]-(m) WHERE id(n) = $id AND ($types IS NULL OR type(r) IN $types) "
            f"AND {in_project('m')} "
            "AND ($labels IS NULL OR any(l IN labels(m) WHERE l IN $labels)) RETURN n, r, m LIMIT $limit",
            {"id": self._node_id(node_id), "types": wanted_types, "project": project, "labels": wanted,
             "limit": MAX_NODES + 1},
        )  # fmt: skip
        return await self._result(rows, MAX_NODES, types=wanted_types)

    async def path(self, source: str, target: str, hops: int = MAX_HOPS) -> dict[str, Any]:
        if not 1 <= hops <= 12:
            raise GraphError("max hops must be between 1 and 12")
        rows = await self.fixed(
            f"MATCH p = (a)-[*BFS ..{int(hops)}]-(b) WHERE id(a) = $a AND id(b) = $b RETURN p LIMIT 1",
            {"a": self._node_id(source), "b": self._node_id(target)},
        )
        result = await self._result(rows, MAX_NODES)
        result["found"] = bool(rows)
        return result

    # --------------------------------------------------------------- cypher box

    async def cypher(self, statement: str, project: str | None = None) -> dict[str, Any]:
        """Guarded read-only statement: columns, rows, truncated flag and graph elements of returned entities.

        With a project the guard anchors every node pattern and `$projectId` is bound to it.
        """
        try:
            checked = self.guard(statement, project is not None)
        except self.guard_error as e:
            raise GraphError(str(e)) from None
        text = (
            checked.text
            + ("" if checked.has_limit else f" LIMIT {MAX_ROWS + 1}")
            + f" QUERY MEMORY LIMIT {self.memory_limit}"
        )
        columns, rows = await self.run(text, {"projectId": project} if project else {}, max_rows=MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
        graph = await self._result(rows, MAX_NODES)
        table: list[list[Any]] = []
        size = 0
        for row in rows:
            cells = [cap(jsonable(v)) for v in row]
            size += len(json.dumps(cells, default=str))
            if size > MAX_PAYLOAD:
                truncated = True
                break
            table.append(cells)
        return {
            "columns": columns,
            "rows": table,
            "truncated": truncated or graph["truncated"],
            "elements": graph["elements"],
        }

"""Read-only knowledge-graph access of the dashboard: the shared service of sandman_agent.graphview with the guard."""

from collections.abc import Callable

from neo4j import AsyncDriver
from sandman_agent.graphview import (
    MAX_NODES,
    MEMORY_LIMIT,
    PROJECT_ID,
    Checked,
    GraphError,
    cap,
    collect,
    edge_element,
    in_project,
    node_element,
    schema_names,
)
from sandman_agent.graphview import GraphService as _GraphService
from sandman_query.guard import GuardError, check

__all__ = [
    "MAX_NODES",
    "PROJECT_ID",
    "GraphError",
    "GraphService",
    "cap",
    "collect",
    "edge_element",
    "in_project",
    "node_element",
    "schema_names",
]


class GraphService(_GraphService):
    def __init__(
        self,
        driver: AsyncDriver,
        labels: list[str],
        rel_types: list[str],
        *,
        guard: Callable[[str, bool], Checked] = check,
        memory_limit: str = MEMORY_LIMIT,
    ) -> None:
        super().__init__(driver, labels, rel_types, guard=guard, guard_error=GuardError, memory_limit=memory_limit)

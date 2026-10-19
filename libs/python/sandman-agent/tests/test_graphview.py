"""graphview: truncation mark and project binding of the guarded statement."""

from typing import Any

import pytest
from sandman_agent import graphview
from sandman_agent.graphview import CUT_MARK, MAX_TEXT, GraphError, GraphService


class Plan:
    text = "RETURN 1"
    has_limit = True


class Refused(Exception):
    pass


class Entity:
    def __init__(self, **props: Any) -> None:
        self.props = props

    def items(self) -> list[tuple[str, Any]]:
        return list(self.props.items())


def service(scoped: list[bool], bound: list[dict[str, Any]]) -> GraphService:
    def guard(statement: str, is_scoped: bool) -> Plan:
        scoped.append(is_scoped)
        if "DELETE" in statement:
            raise Refused("write")
        return Plan()

    graph = GraphService(None, [], [], guard=guard, guard_error=Refused)  # type: ignore[arg-type]

    async def run(text: str, params: dict[str, Any], max_rows: int) -> tuple[list[str], list[list[Any]]]:
        bound.append(params)
        return ["x"], [[1]]

    graph.run = run  # type: ignore[method-assign, assignment]
    return graph


def test_long_text_is_cut_and_marked() -> None:
    props = graphview._props(Entity(text="x" * (MAX_TEXT + 5), name="n"))  # type: ignore[arg-type]
    assert props["text"] == "x" * MAX_TEXT + CUT_MARK
    assert props["name"] == "n"


@pytest.mark.parametrize(("project", "params"), [(None, {}), ("alpha", {"projectId": "alpha"})])
async def test_cypher_scopes_the_guard_and_binds_the_project(project: str | None, params: dict[str, Any]) -> None:
    scoped: list[bool] = []
    bound: list[dict[str, Any]] = []
    result = await service(scoped, bound).cypher("MATCH (n) RETURN n", project)
    assert scoped == [project is not None] and bound == [params]
    assert result["rows"] == [[1]]


async def test_guard_error_becomes_graph_error() -> None:
    with pytest.raises(GraphError, match="write"):
        await service([], []).cypher("MATCH (n) DELETE n", "alpha")

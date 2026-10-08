"""Integration with Testcontainers Memgraph 3.13.1 (skipped without Docker): graph queries, Cypher box, read-only."""

import pytest
from neo4j import AsyncDriver
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_agent.kg import KgClient
from sandman_dashboard.graph import MAX_NODES, GraphError, GraphService, schema_names
from sandman_query.guard import Checked

REJECTED = [
    "CREATE (n:X) RETURN n",
    "MERGE (n:X {id: 'a'}) RETURN n",
    "MATCH (n) SET n.x = 1 RETURN n",
    "MATCH (n) DELETE n",
    "MATCH (n) DETACH DELETE n",
    "MATCH (n) REMOVE n.id RETURN n",
    "CALL mg.procedures() YIELD name RETURN name",
    "DUMP DATABASE",
    "SHOW STORAGE INFO",
    "SHOW INDEX INFO",
    "LOAD CSV FROM '/etc/passwd' AS row RETURN row",
    "MATCH (n) RETURN n; MATCH (m) RETURN m",
    "MATCH (n) RETURN n; CREATE (x:X)",
    "MATCH (n) RETURN n QUERY MEMORY LIMIT 1 GB",
]


async def count(driver: AsyncDriver) -> int:
    async with driver.session() as session:
        return int((await (await session.run("MATCH (n) RETURN count(n) AS c")).single())["c"])  # type: ignore[index]


async def seed(driver: AsyncDriver) -> None:
    async with driver.session() as session:
        await session.run(
            "CREATE (p:Project {id: 'demo', name: 'Demo'}), (q:Project {id: 'other', name: 'Other'}), "
            "(r:Element:Requirement {id: 'el_1', key: 'art#REQ-1', projectId: 'demo', name: 'Upload <b>audio</b>'}), "
            "(c:Element:CodeUnit {id: 'el_2', key: 'art#Cache', projectId: 'demo', name: 'MediaCache'}), "
            "(t:Element:TestCase {id: 'el_3', key: 'art#CacheTest', projectId: 'demo', name: 'MediaCacheTest'}), "
            "(x:Element:Requirement {id: 'el_9', key: 'art#REQ-9', projectId: 'other', name: 'Foreign'}), "
            "(p)-[:CONTAINS {w: 1}]->(r), (r)-[:DEPENDS_ON]->(c), (c)-[:DEPENDS_ON]->(t), (q)-[:CONTAINS]->(x)"
        )


def service(driver: AsyncDriver, **kwargs: object) -> GraphService:
    labels, rel_types = schema_names(DEFAULT_CONTRACTS_DIR)
    return GraphService(driver, labels, rel_types, **kwargs)  # type: ignore[arg-type]


def node_names(result: dict[str, object]) -> set[str]:
    return {e["data"]["props"].get("name", "") for e in result["elements"] if e["group"] == "nodes"}  # type: ignore[index, union-attr]


async def test_projects_and_start(driver: AsyncDriver) -> None:
    await seed(driver)
    graph = service(driver)
    assert await graph.projects() == [{"id": "demo", "name": "Demo"}, {"id": "other", "name": "Other"}]
    result = await graph.start("demo", "Requirement")
    assert node_names(result) == {"Upload <b>audio</b>"} and not result["truncated"]
    everything = await graph.start(None, None)
    assert len(everything["elements"]) > len(result["elements"])  # type: ignore[arg-type]
    scoped = await graph.start("demo", None)
    assert "Foreign" not in node_names(scoped) and "Demo" in node_names(scoped)
    edges = [e for e in scoped["elements"] if e["group"] == "edges"]  # type: ignore[union-attr]
    assert {e["data"]["label"] for e in edges} == {"CONTAINS", "DEPENDS_ON"}
    props = next(e for e in edges if e["data"]["label"] == "CONTAINS")["data"]["props"]
    assert props == {"w": 1}


async def test_search_filters_and_unknown_values(driver: AsyncDriver) -> None:
    await seed(driver)
    graph = service(driver)
    assert node_names(await graph.search("demo", "mediacache")) == {"MediaCache", "MediaCacheTest"}
    assert node_names(await graph.search("demo", "MediaCache", ["TestCase"])) == {"MediaCacheTest"}
    assert node_names(await graph.search(None, "foreign")) == {"Foreign"}
    for bad in (
        lambda: graph.start("demo", "Nope"),
        lambda: graph.search("demo", " "),
        lambda: graph.start("a b", None),
    ):
        with pytest.raises(GraphError):
            await bad()
    with pytest.raises(GraphError):
        await graph.expand("demo", "1", types=["NOPE) DETACH DELETE (n"])
    with pytest.raises(GraphError):
        await graph.expand("demo", "1; MATCH (n) DELETE n")
    assert await count(driver) == 6


async def test_expand_and_path(driver: AsyncDriver) -> None:
    await seed(driver)
    graph = service(driver)
    start = await graph.search("demo", "REQ-1")
    req = next(e["data"] for e in start["elements"] if e["group"] == "nodes")  # type: ignore[union-attr]
    assert req["id"] == f"n{req['nid']}" and req["props"]["id"] == "el_1"
    expanded = await graph.expand("demo", req["nid"])
    assert node_names(expanded) == {"Demo", "Upload <b>audio</b>", "MediaCache"}
    only = await graph.expand("demo", req["nid"], types=["DEPENDS_ON"])
    assert node_names(only) == {"Upload <b>audio</b>", "MediaCache"}
    labelled = await graph.expand("demo", req["nid"], labels=["Project"])
    assert node_names(labelled) == {"Demo", "Upload <b>audio</b>"}
    test = next(e["data"] for e in (await graph.search("demo", "CacheTest"))["elements"] if e["group"] == "nodes")  # type: ignore[union-attr]
    found = await graph.path(req["nid"], test["nid"])
    assert found["found"] and node_names(found) == {"Upload <b>audio</b>", "MediaCache", "MediaCacheTest"}
    assert len([e for e in found["elements"] if e["group"] == "edges"]) == 2  # type: ignore[union-attr]
    short = await graph.path(req["nid"], test["nid"], 1)
    assert short["found"] is False


async def test_truncation_at_500_nodes(driver: AsyncDriver) -> None:
    async with driver.session() as session:
        await session.run("UNWIND range(1, 600) AS i CREATE (:Element:CodeUnit {id: toString(i), projectId: 'big'})")
    result = await service(driver).start("big", None)
    assert result["truncated"] is True
    assert len([e for e in result["elements"] if e["group"] == "nodes"]) == MAX_NODES  # type: ignore[union-attr]


async def test_storage_info_runs_autocommit_but_not_in_a_read_transaction(driver: AsyncDriver) -> None:
    await seed(driver)
    kg = KgClient("bolt://unused", None, None, driver=driver)
    info = {r["storage info"]: r["value"] for r in await kg.run_autocommit("SHOW STORAGE INFO")}
    assert "memory_res" in info and "memory_limit" in info
    with pytest.raises(GraphError):
        await service(driver).run("SHOW STORAGE INFO", {})


@pytest.mark.parametrize("statement", REJECTED)
async def test_cypher_box_rejects(driver: AsyncDriver, statement: str) -> None:
    await seed(driver)
    with pytest.raises(GraphError):
        await service(driver).cypher(statement)
    assert await count(driver) == 6


async def test_write_fails_in_read_transaction_even_without_guard(driver: AsyncDriver) -> None:
    await seed(driver)
    graph = service(driver, guard=lambda text, scoped: Checked(text, True))
    for statement in ("CREATE (n:Evil) RETURN n", "MATCH (n) SET n.pwned = true RETURN n", "MATCH (n) DETACH DELETE n"):
        with pytest.raises(GraphError):
            await graph.cypher(statement)
    assert await count(driver) == 6
    async with driver.session() as session:
        assert (await (await session.run("MATCH (n {pwned: true}) RETURN count(n) AS c")).single())["c"] == 0  # type: ignore[index]


async def test_cypher_box_returns_table_and_graph(driver: AsyncDriver) -> None:
    await seed(driver)
    result = await service(driver).cypher(
        "MATCH (a:Requirement {projectId: 'demo'})-[r:DEPENDS_ON]->(b) RETURN a, r, b"
    )
    assert result["columns"] == ["a", "r", "b"] and result["rows"][0][2]["name"] == "MediaCache"  # type: ignore[index]
    assert {e["group"] for e in result["elements"]} == {"nodes", "edges"}  # type: ignore[union-attr]
    assert result["truncated"] is False
    limited = await service(driver).cypher("UNWIND range(1, 800) AS i RETURN i")
    assert len(limited["rows"]) == 500 and limited["truncated"] is True  # type: ignore[arg-type]


async def test_cartesian_product_hits_the_memory_limit(driver: AsyncDriver) -> None:
    async with driver.session() as session:
        await session.run("UNWIND range(1, 300) AS i CREATE (:Element:CodeUnit {id: toString(i), projectId: 'big'})")
    graph = service(driver, memory_limit="1 MB")
    with pytest.raises(GraphError, match="memory limit"):
        await graph.cypher("MATCH (a), (b), (c) RETURN count(*) AS c ORDER BY c")


async def test_large_cells_and_payload_are_capped(driver: AsyncDriver) -> None:
    graph = service(driver)
    result = await graph.cypher("RETURN reduce(s = '', i IN range(1, 10000) | s + 'abcdefgh') AS big")
    cell = result["rows"][0][0]  # type: ignore[index]
    assert len(cell) < 2100 and cell.endswith("[truncated]")
    wide = "reduce(s = '', j IN range(1, 1000) | s + 'x')"
    many = await graph.cypher(f"UNWIND range(1, 600) AS i RETURN {wide} AS a, {wide} AS b, {wide} AS c, {wide} AS d")
    assert many["truncated"] is True and len(many["rows"]) < 500  # type: ignore[arg-type]


async def test_database_errors_are_mapped_to_short_messages(driver: AsyncDriver) -> None:
    graph = service(driver, guard=lambda text, scoped: Checked(text, True))
    with pytest.raises(GraphError, match="read-only"):
        await graph.cypher("CREATE (n:Evil) RETURN n")
    with pytest.raises(GraphError) as e:
        await graph.cypher("RETURN toInteger('x') + 'y' + {a: 1}.a.b.c AS v, 1/0")
    assert len(str(e.value)) < 80

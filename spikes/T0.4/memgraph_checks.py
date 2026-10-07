"""T0.4 checks 7, 8, 10, 14, 15 and V-38 against a throwaway Memgraph 3.13.1 container.

Run: DOCKER_HOST=... uv run python spikes/T0.4/memgraph_checks.py   (prints one line per check)
"""

import asyncio
import re
import sys
import time
from pathlib import Path

from neo4j import READ_ACCESS, AsyncGraphDatabase, GraphDatabase
from neo4j.exceptions import Neo4jError
from testcontainers.core.container import DockerContainer

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "memgraph/memgraph:3.13.1"
results: list[tuple[str, str, str]] = []


def record(check: str, ok: bool | None, evidence: str) -> None:
    verdict = "PASS" if ok else ("FAIL" if ok is False else "INFO")
    results.append((check, verdict, evidence))
    print(f"[{verdict}] {check}: {evidence}", flush=True)


def stage1_statements() -> list[str]:
    text = (ROOT / "docs/stage-1-analysis-architecture.md").read_text()
    section = text[text.index("### 4.4 Example Cypher") : text.index("## 5. Agent design")]
    blocks = re.findall(r"```cypher\n(.*?)```", section, re.S)
    statements = []
    for block in blocks:
        body = "\n".join(line.split("//")[0] for line in block.splitlines())
        statements += [s.strip() for s in body.split(";") if s.strip()]
    return statements


PARAMS = {
    "projectId": "mini", "ingestionId": "ing_1", "artifactId": "art_1", "path": "a.md", "kind": "requirements",
    "format": "markdown", "versionId": "ver_1", "hash": "h", "uri": "artifact://mini/h", "size": 1, "now": "t",
    "rev": "r", "runId": "run_1", "elements": [{"id": "el_1", "key": "art_1#REQ-1", "kind": "file", "name": "n",
    "qualifiedName": "q", "text": "x", "startLine": 1, "endLine": 2, "contentHash": "c"}], "elementIds": ["el_1"],
    "links": [{"id": "tl_1", "sourceId": "el_1", "targetId": "el_1", "linkType": "relates", "confidence": 0.5,
    "rationale": None}], "approach": "a", "approachVersion": "1", "configHash": "c",
    "sourceVersionIds": ["ver_1"], "targetVersionIds": ["ver_1"], "id": "inc_1", "expectedStatus": "OPEN",
    "to": "DEFERRED", "actor": "user:bob", "reason": "later", "minConfidence": 0.0, "linkId": "tl_1",
}  # fmt: skip


def main() -> None:
    with DockerContainer(IMAGE).with_command("--telemetry-enabled=false --memory-limit=512").with_exposed_ports(
        7687
    ) as c:
        url = f"bolt://{c.get_container_host_ip()}:{c.get_exposed_port(7687)}"
        driver = GraphDatabase.driver(url)
        for _ in range(60):
            try:
                driver.verify_connectivity()
                break
            except Exception:  # noqa: BLE001 - starting
                time.sleep(0.5)
        import neo4j

        record("7 driver", True, f"neo4j-python {neo4j.__version__} connects to {IMAGE}")

        with driver.session() as s:
            try:
                s.execute_read(lambda tx: tx.run("CREATE (:X)").consume())
                record("7 execute_read rejects writes", False, "write succeeded")
            except Neo4jError as e:
                record("7 execute_read rejects writes", True, f"{e.code}: {e.message[:90]}")
        with driver.session(default_access_mode=READ_ACCESS) as s:
            try:
                s.run("CREATE (:Y)").consume()
                n = driver.execute_query("MATCH (y:Y) RETURN count(y) AS n").records[0]["n"]
                record("7 auto-commit in READ access mode", n == 0, f"write accepted, {n} node(s) created")
            except Neo4jError as e:
                record("7 auto-commit in READ access mode", True, f"rejected: {e.code}")

        lines = (ROOT / "contracts/kg/init.cypher").read_text().splitlines()
        code = "\n".join(line for line in lines if not line.lstrip().startswith("//"))
        init = [stmt.strip() for stmt in code.split(";") if stmt.strip()]
        failed = []
        for stmt in init:
            try:
                with driver.session() as session:  # schema statements only work as auto-commit queries
                    session.run(stmt).consume()
            except Neo4jError as e:
                failed.append(f"{stmt[:50]} -> {e.message[:80]}")
        record("8 init.cypher (auto-commit; rejected in explicit transactions)", not failed, f"{len(init)} statements, failures: {failed or 'none'}")

        driver.execute_query("CREATE (:Project {id: 'mini'})")
        driver.execute_query("CREATE (:Run {id: 'run_1'})")
        driver.execute_query("CREATE (:Ingestion {id: 'ing_1'})")
        driver.execute_query("CREATE (:Inconsistency {id: 'inc_1', status: 'OPEN', evidenceHash: 'e'})")
        failed = []
        for stmt in stage1_statements():
            try:
                driver.execute_query(stmt, PARAMS)
            except Neo4jError as e:
                failed.append(f"{' '.join(stmt.split())[:70]} -> {e.message[:100]}")
        record("8 Stage 1 §4.4 statements", not failed, f"{len(stage1_statements())} statements, failures: {failed or 'none'}")

        for q in ("EXPLAIN MATCH (n:NoSuchLabel) RETURN n", "EXPLAIN MATCH (n:Element) WHERE n.nope = 1 RETURN n"):
            try:
                rows = driver.execute_query(q).records
                record("8 EXPLAIN unknown label/property", None, f"{q[8:40]}… accepted, {len(rows)} plan rows (no error)")
            except Neo4jError as e:
                record("8 EXPLAIN unknown label/property", None, f"error {e.message[:80]}")
        for q in ("MATCH (n) RETURN count(n) QUERY MEMORY LIMIT 10 MB", "MATCH (n) RETURN count(n) QUERY MEMORY LIMIT 10MB"):
            try:
                driver.execute_query(q)
                record("8 QUERY MEMORY LIMIT syntax", True, f"accepted: {q[25:]}")
            except Neo4jError as e:
                record("8 QUERY MEMORY LIMIT syntax", None, f"rejected: {q[25:]} ({e.message[:60]})")
        try:
            driver.execute_query("UNWIND range(1, 2000000) AS i WITH collect(i) AS l RETURN size(l) QUERY MEMORY LIMIT 1 MB")
            record("8 QUERY MEMORY LIMIT enforced", False, "large collect passed under 1 MB")
        except Neo4jError as e:
            record("8 QUERY MEMORY LIMIT enforced", True, e.message[:100])

        driver.execute_query("CREATE (:P {id: 'a'})-[:R]->(:P {id: 'b'})-[:R]->(:P {id: 'c'})")
        for q in ("MATCH p = (a:P {id: 'a'})-[*BFS ..5]-(c:P {id: 'c'}) RETURN size(relationships(p)) AS len",
                  "MATCH p = shortestPath((a:P {id: 'a'})-[*..5]-(c:P {id: 'c'})) RETURN length(p) AS len"):  # fmt: skip
            try:
                rows = driver.execute_query(q).records
                record("V-38 path query", True, f"{q[10:44]}… -> length {rows[0]['len']}")
            except Neo4jError as e:
                record("V-38 path query", None, f"{q[10:44]}… rejected: {e.message[:70]}")

        res = c.get_wrapped_container().exec_run(
            ["sh", "-c", "echo 'RETURN 1;' | mgconsole --host 127.0.0.1 --port 7999; echo exit=$?"]
        )  # mgconsole against a port without server = what the healthcheck sees when Memgraph is down
        code = res.output.decode().strip().splitlines()[-1]
        record("10 mgconsole exit code without server", code != "exit=0", code)
        asyncio.run(conflict_check(url))
        memory_check(driver)
        driver.close()

    print("\nSUMMARY " + " ".join(f"{c}={v}" for c, v, _ in results))


async def conflict_check(url: str) -> None:
    driver = AsyncGraphDatabase.driver(url)
    await driver.execute_query("MERGE (:Counter {id: 'c', n: 0})")
    attempts = {"n": 0}
    errors: list[str] = []

    async def bump(tx):  # type: ignore[no-untyped-def]
        attempts["n"] += 1
        await (await tx.run("MATCH (c:Counter {id: 'c'}) SET c.n = c.n + 1")).consume()
        await asyncio.sleep(0.05)

    async def worker() -> None:
        async with driver.session() as s:
            try:
                await s.execute_write(bump)
            except Neo4jError as e:
                errors.append(e.message[:80])

    await asyncio.gather(*(worker() for _ in range(8)))
    n = (await driver.execute_query("MATCH (c:Counter {id: 'c'}) RETURN c.n AS n")).records[0]["n"]
    await driver.close()
    record("15 write conflict retried by execute_write", n == 8 and not errors,
           f"8 concurrent writers -> n={n}, attempts={attempts['n']} (retries={attempts['n'] - 8}), errors={errors}")  # fmt: skip


def memory_check(driver) -> None:  # type: ignore[no-untyped-def]
    try:
        for _ in range(200):
            driver.execute_query("UNWIND range(1, 50000) AS i CREATE (:Fill {i: i, s: 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'})")
        record("14 --memory-limit=512 enforced", False, "10M nodes created without error")
    except Neo4jError as e:
        with driver.session() as session:  # SHOW STORAGE INFO only works as an auto-commit query
            info = {r["storage info"]: r["value"] for r in session.run("SHOW STORAGE INFO")}
        record("14 --memory-limit=512 enforced", True,
               f"query aborted ({e.message[:70]}); server alive, memory_tracked={info.get('memory_tracked')}, limit={info.get('allocation_limit')}")  # fmt: skip


if __name__ == "__main__":
    sys.exit(main())

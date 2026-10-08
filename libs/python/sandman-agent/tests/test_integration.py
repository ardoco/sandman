"""Integration tests against real Memgraph and NATS containers (skipped without Docker)."""

import asyncio
import time
from collections.abc import Iterator

import nats
import pytest
from neo4j import AsyncGraphDatabase
from neo4j.exceptions import Neo4jError
from sandman_agent import events
from sandman_agent.contracts import Contracts
from sandman_agent.kg import KgClient, RunRecorder
from sandman_agent.llm import Usage
from testcontainers.core.container import DockerContainer


def _docker_available() -> bool:
    try:
        import docker  # type: ignore[import-untyped]

        docker.from_env().ping()
        return True
    except Exception:  # noqa: BLE001 - no daemon reachable
        return False


pytestmark = pytest.mark.skipif(not _docker_available(), reason="no Docker daemon reachable")

DATA = {"projectId": "mini", "sourceVersionIds": [], "targetVersionIds": [], "runs": []}


def _wait_port(container: DockerContainer, port: int, check: str) -> str:
    host = container.get_container_host_ip()
    url = f"{check}://{host}:{container.get_exposed_port(port)}"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if check == "bolt":
                asyncio.run(_bolt_ping(url))
            else:
                asyncio.run(_nats_ping(url))
            return url
        except Exception:  # noqa: BLE001 - not ready yet
            time.sleep(0.5)
    raise TimeoutError(url)


async def _bolt_ping(url: str) -> None:
    driver = AsyncGraphDatabase.driver(url)
    try:
        await driver.verify_connectivity()
    finally:
        await driver.close()


async def _nats_ping(url: str) -> None:
    nc = await nats.connect(url, connect_timeout=1, max_reconnect_attempts=0)
    await nc.close()


@pytest.fixture(scope="module")
def memgraph() -> Iterator[str]:
    with (
        DockerContainer("memgraph/memgraph:3.13.1")
        .with_command("--telemetry-enabled=false")
        .with_exposed_ports(7687) as c
    ):
        yield _wait_port(c, 7687, "bolt")


@pytest.fixture(scope="module")
def nats_url() -> Iterator[str]:
    with DockerContainer("nats:2.15.0-alpine").with_command("-js").with_exposed_ports(4222) as c:
        yield _wait_port(c, 4222, "nats")


async def test_kg_write_read_and_read_transaction_rejects_writes(memgraph: str) -> None:
    kg = KgClient(memgraph, None, None)
    try:
        await kg.write("CREATE (:Probe {id: $id})", id="p1")
        assert await kg.read("MATCH (p:Probe {id: $id}) RETURN p.id AS id", id="p1") == [{"id": "p1"}]
        with pytest.raises(Neo4jError):
            await kg.read("CREATE (:Probe {id: 'p2'})")
        assert await kg.read("MATCH (p:Probe {id: 'p2'}) RETURN p") == []
    finally:
        await kg.close()


async def test_cleanup_stale_removes_only_facts_created_by_unfinished_runs(memgraph: str) -> None:
    kg = KgClient(memgraph, None, None)
    runs = RunRecorder(kg, "test-agent", "0")
    try:
        done = await runs.start(project_id="mini", skill="s", tool="t", tool_version="0", input_key="k1")
        await kg.write("MATCH (r:Run {id: $r}) CREATE (r)-[:PRODUCED]->(:Element {id: 'keep', runId: $r})", r=done)
        await runs.complete(done)
        stale = await runs.start(project_id="mini", skill="s", tool="t", tool_version="0", input_key="k1")
        await kg.write(
            "MATCH (r:Run {id: $r}), (old:Element {id: 'keep'}) "
            "CREATE (r)-[:PRODUCED]->(:Element {id: 'drop', runId: $r}) CREATE (r)-[:PRODUCED]->(old)",
            r=stale,
        )
        assert await runs.cleanup_stale("k1") == 1
        ids = await kg.read("MATCH (e:Element) WHERE e.id IN ['keep', 'drop'] RETURN e.id AS id")
        assert ids == [{"id": "keep"}]
        status = await kg.read("MATCH (r:Run) WHERE r.id IN [$a, $b] RETURN r.id AS id, r.status AS s", a=done, b=stale)
        assert {r["id"]: r["s"] for r in status} == {done: "completed", stale: "failed"}
    finally:
        await kg.close()


async def test_run_records_prompt_ids_and_resolved_model(memgraph: str) -> None:
    kg = KgClient(memgraph, None, None)
    runs = RunRecorder(kg, "test-agent", "0")
    try:
        rid = await runs.start(
            project_id="mini", skill="s", tool="llm-enrichment", tool_version="0", input_key="k2",
            model="chat-local", prompt_ids=["prompts/a.txt@0123456789ab"],
        )  # fmt: skip
        await runs.complete(rid, Usage(3, 4), resolved_model="qwen3:1.7b")
        row = (await kg.read("MATCH (r:Run {id: $r}) RETURN properties(r) AS p", r=rid))[0]["p"]
        assert row["promptIds"] == ["prompts/a.txt@0123456789ab"]
        assert (row["resolvedModel"], row["inputTokens"], row["outputTokens"]) == ("qwen3:1.7b", 3, 4)
    finally:
        await kg.close()


async def test_publish_consume_round_trip_with_deduplication(nats_url: str, contracts: Contracts) -> None:
    nc = await nats.connect(nats_url)
    try:
        js = nc.jetstream()
        await events.ensure_stream(js)
        await events.ensure_stream(js)  # idempotent
        publisher = events.EventPublisher(js, contracts, "tlr-agent")
        sent = await publisher.publish("sandman.tracelinks.updated", "mini", DATA, causation_id="m-1")
        await publisher.publish("sandman.tracelinks.updated", "mini", DATA, causation_id="m-1", event_id=sent.id)
        received: list[events.Event] = []
        stop = asyncio.Event()

        async def handler(e: events.Event) -> None:
            received.append(e)

        consumer = events.EventConsumer(js, "test-consumer")
        task = asyncio.create_task(consumer.run(handler, stop))
        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.5)  # a duplicate would arrive meanwhile
        stop.set()
        await task
        assert [e.id for e in received] == [sent.id]
        assert (await js.stream_info(events.STREAM)).state.messages == 1
    finally:
        await nc.close()


async def test_init_schema_is_idempotent_and_memory_limit_is_not_retried(memgraph: str) -> None:
    from sandman_agent.config import DEFAULT_CONTRACTS_DIR
    from sandman_agent.kg import MemoryLimitError

    kg = KgClient(memgraph, None, None)
    try:
        await kg.init_schema(DEFAULT_CONTRACTS_DIR / "kg/init.cypher")
        await kg.init_schema(DEFAULT_CONTRACTS_DIR / "kg/init.cypher")
        info = await kg.run_autocommit("SHOW STORAGE INFO")
        assert "memory_res" in {row["storage info"] for row in info}
        started = time.monotonic()
        with pytest.raises(MemoryLimitError):
            await kg.read("UNWIND range(1, 2000000) AS i WITH collect(i) AS l RETURN size(l) QUERY MEMORY LIMIT 1 MB")
        assert time.monotonic() - started < 5, "memory-limit error must not be retried"
    finally:
        await kg.close()

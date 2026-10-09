"""Export tests against a small graph in a Memgraph container (skipped without Docker)."""

import json
import time
from collections.abc import Iterator

import pytest
from kg_canonical_export import STRIP, STRIP_RUN, STRIP_SUFFIXES, export
from neo4j import Driver, GraphDatabase
from testcontainers.core.container import DockerContainer


def _docker_available() -> bool:
    try:
        import docker  # type: ignore[import-untyped]

        docker.from_env().ping()
        return True
    except Exception:  # noqa: BLE001 - no daemon reachable
        return False


pytestmark = pytest.mark.skipif(not _docker_available(), reason="no Docker daemon reachable")


@pytest.fixture(scope="module")
def driver() -> Iterator[Driver]:
    with (
        DockerContainer("memgraph/memgraph:3.13.1")
        .with_command("--telemetry-enabled=false")
        .with_exposed_ports(7687) as c
    ):
        url = f"bolt://{c.get_container_host_ip()}:{c.get_exposed_port(7687)}"
        deadline = time.monotonic() + 60
        while True:
            try:
                d = GraphDatabase.driver(url)
                d.verify_connectivity()
                break
            except Exception:  # noqa: BLE001 - not ready yet
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        yield d
        d.close()


def seed(driver: Driver, project: str, run_id: str, wf: str, ts: str) -> None:
    driver.execute_query("MATCH (n) WHERE n.projectId = $p OR n.id = $p DETACH DELETE n", p=project)
    driver.execute_query(
        """
        CREATE (p:Project {id: $p, name: 'n', llmPolicy: 'local-only', ingestionSeq: 1, createdAt: $ts})
        CREATE (a:Artifact {id: 'art_1', projectId: $p, path: 'b.md', kind: 'requirements'})
        CREATE (a0:Artifact {id: 'art_0', projectId: $p, path: 'a.md', kind: 'requirements'})
        CREATE (e1:Element:Requirement {id: 'el_2', projectId: $p, name: 'two', runId: $run})
        CREATE (e0:Element:Requirement {id: 'el_1', projectId: $p, name: 'one', runId: $run})
        CREATE (c:Concept {id: 'con_1', projectId: $p, name: 'x'})
        CREATE (r:Run {id: $run, projectId: $p, agent: 'extraction-agent', agentVersion: $run, skill: 's',
                       replayMode: 'replay', workflowId: $wf, a2aTaskId: $wf, startedAt: $ts, endedAt: $ts})
        CREATE (p)-[:HAS_ARTIFACT]->(a), (p)-[:HAS_ARTIFACT]->(a0)
        CREATE (e0)-[:MENTIONS {confidence: 0.5, runId: $run}]->(c)
        CREATE (r)-[:PRODUCED]->(e1), (r)-[:PRODUCED]->(e0), (r)-[:PRODUCED]->(c)
        CREATE (:Element:Requirement {id: 'el_other', projectId: 'other', name: 'o'})
        """,
        p=project,
        run=run_id,
        wf=wf,
        ts=ts,
    )


def test_export_is_deterministic_across_random_run_ids(driver: Driver) -> None:
    seed(driver, "t1", "run_aaa", "wf-1", "2026-01-01T00:00:00Z")
    first = export(driver, "t1")
    seed(driver, "t1", "run_bbb", "wf-2", "2027-05-05T05:05:05Z")
    assert export(driver, "t1") == first


def test_strip_list_run_endpoints_and_sort_order(driver: Driver) -> None:
    seed(driver, "t2", "run_ccc", "wf-3", "2026-01-01T00:00:00Z")
    doc = json.loads(export(driver, "t2"))
    text = json.dumps(doc)
    for leaked in ("run_ccc", "wf-3", "2026-01-01", "other"):
        assert leaked not in text
    for node in doc["nodes"]:
        for name in node["properties"]:
            assert name not in STRIP and not name.endswith(tuple(STRIP_SUFFIXES))
            assert not (node["labels"] == ["Run"] and name in STRIP_RUN)
    run = next(n for n in doc["nodes"] if n["labels"] == ["Run"])
    assert "replayMode" not in run["properties"]
    produced = [r for r in doc["relationships"] if r["type"] == "PRODUCED"]
    assert len(produced) == 3 and all(r["source"] == run for r in produced)
    assert {r["target"]["id"] for r in produced} == {"el_1", "el_2", "con_1"}
    mention = next(r for r in doc["relationships"] if r["type"] == "MENTIONS")
    assert mention["properties"] == {"confidence": 0.5}
    assert mention["source"] == {"labels": ["Element", "Requirement"], "id": "el_1"}
    keys = [json.dumps(n, sort_keys=True, separators=(",", ":")) for n in doc["nodes"]]
    assert keys == sorted(keys)
    assert [n["properties"].get("path") for n in doc["nodes"] if n["labels"] == ["Artifact"]] == ["a.md", "b.md"]

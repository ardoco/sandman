"""Canonical KG export of one project for determinism checks (Stage 1 §11.6, T1.6).

Run: uv run python scripts/kg_canonical_export.py --project <id> [--uri bolt://127.0.0.1:7687] [--out file]

Auth: SANDMAN_KG_USER (default `sandman`) with SANDMAN_KG_PASSWORD, SANDMAN_KG_PASSWORD_FILE or, as the dev fallback,
deploy/secrets/memgraph_password. Without any password the connection is unauthenticated.

Output: RFC 8785 (JCS) JSON `{"nodes": [...], "relationships": [...]}`, lists sorted by their own JCS bytes. Nodes are
all nodes with `projectId` = project, the Project node and the StatusChange nodes of its inconsistencies. Properties
with random or time-based values are dropped; Run nodes carry no id, so relationship endpoints that are Runs (or
otherwise id-less) are replaced by their canonical content.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import rfc8785
from neo4j import Driver, GraphDatabase, ManagedTransaction

ROOT = Path(__file__).resolve().parent.parent

STRIP = {
    "runId": "run ids are random (Element, TraceLink, MENTIONS)",
    "firstRunId": "run id is random",
    "lastRunId": "run id is random",
    "a2aTaskId": "A2A task id is random per call",
    "workflowId": "workflow id is a random uuid per ingestion",
    "agentVersion": "image tag + git commit, differs between builds that behave identically",
    "at": "StatusChange timestamp",
}
STRIP_SUFFIXES = {"At": "timestamps (createdAt, startedAt, endedAt, ingestedAt, deletedAt, updatedAt)"}
STRIP_RUN = {
    "id": "run id is random (run_ + uuid)",
    "replayMode": "differs between the recording (record) and every replay (replay) by design",
}


def strip(label: str | None, name: str) -> bool:
    """True if property `name` (of a node with main label `label`; None otherwise) is random or time-based."""
    return name in STRIP or name.endswith(tuple(STRIP_SUFFIXES)) or (label == "Run" and name in STRIP_RUN)


def _props(label: str | None, props: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in props.items() if not strip(label, k)}


def _node(labels: list[str], props: dict[str, Any]) -> dict[str, Any]:
    main = "Run" if "Run" in labels else None
    return {"labels": sorted(labels), "properties": _props(main, props)}


def _jcs(value: Any) -> bytes:
    return rfc8785.dumps(value)


def _ref(node: dict[str, Any], raw_id: Any) -> dict[str, Any]:
    if isinstance(raw_id, str) and "Run" not in node["labels"]:
        return {"labels": node["labels"], "id": raw_id}
    return node


def _fetch(tx: ManagedTransaction, project: str) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    nodes: dict[int, dict[str, Any]] = {}
    queries = (
        "MATCH (n) WHERE n.projectId = $p RETURN id(n) AS i, labels(n) AS l, properties(n) AS props",
        "MATCH (n:Project {id: $p}) RETURN id(n) AS i, labels(n) AS l, properties(n) AS props",
        "MATCH (:Inconsistency {projectId: $p})-[:HAS_STATUS_CHANGE]->(n) "
        "RETURN id(n) AS i, labels(n) AS l, properties(n) AS props",
    )
    for q in queries:
        for r in tx.run(q, p=project):
            nodes[r["i"]] = {"labels": r["l"], "props": r["props"]}
    rels = tx.run(
        "MATCH (a)-[r]->(b) WHERE id(a) IN $ids AND id(b) IN $ids "
        "RETURN type(r) AS t, id(a) AS a, id(b) AS b, properties(r) AS props",
        ids=list(nodes),
    ).data()
    return nodes, rels


def export(driver: Driver, project_id: str) -> bytes:
    """Canonical JCS export of `project_id`, read in one read transaction."""
    with driver.session() as session:
        raw, rels = session.execute_read(_fetch, project_id)
    nodes = {i: _node(n["labels"], n["props"]) for i, n in raw.items()}
    refs = {i: _ref(nodes[i], raw[i]["props"].get("id")) for i in raw}
    out_rels = []
    for r in rels:
        rel: dict[str, Any] = {"type": r["t"], "source": refs[r["a"]], "target": refs[r["b"]]}
        if props := _props(None, r["props"]):
            rel["properties"] = props
        out_rels.append(rel)
    doc = {
        "nodes": sorted(nodes.values(), key=_jcs),
        "relationships": sorted(out_rels, key=_jcs),
    }
    return _jcs(doc)


def connect(uri: str) -> Driver:
    """Driver with the auth described in the module docstring."""
    password = os.environ.get("SANDMAN_KG_PASSWORD")
    file = os.environ.get("SANDMAN_KG_PASSWORD_FILE") or ROOT / "deploy" / "secrets" / "memgraph_password"
    if password is None and Path(file).is_file():
        password = Path(file).read_text().strip()
    auth = (os.environ.get("SANDMAN_KG_USER", "sandman"), password) if password else None
    return GraphDatabase.driver(uri, auth=auth)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("--uri", default="bolt://127.0.0.1:7687")
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", type=Path, default=None, help="output file (default stdout)")
    args = ap.parse_args(argv)
    with connect(args.uri) as driver:
        data = export(driver, args.project)
    if args.out:
        args.out.write_bytes(data)
    else:
        sys.stdout.buffer.write(data + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

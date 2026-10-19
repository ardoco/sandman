"""Seed KG for the NL query evaluation set (T4.2) and the gold-Cypher validator.

Builds, in a Memgraph testcontainer (or --bolt URL), the KG of the benchmark projects: elements and relations are the
extraction goldens of services/extraction-agent/tests/golden/benchmark, trace links are the ArDoCo SAD-SAM results in
seed/<project>-links.csv. Confidence, link status, inconsistencies, concepts and ingestions are SYNTHETIC (deterministic
rules below), because the stack has no run that produces them for all projects.

  uv run python experiments/nlquery/seed_kg.py gold [--dataset dataset.yaml] [--out gold-results.json]
  uv run python experiments/nlquery/seed_kg.py run PREDICTIONS.json [--out results.json]   # baseline predictions
"""

# ruff: noqa: E501
import argparse
import csv
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from neo4j import GraphDatabase
from sandman_agent.ids import stable_id
from sandman_query.guard import check
from testcontainers.core.container import DockerContainer

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
GOLDEN = ROOT / "services/extraction-agent/tests/golden/benchmark"
PROJECTS = {  # project -> (model golden file, display name)
    "mediastore": ("mediastore/pcm/ms.repository.json", "MediaStore"),
    "teastore": ("teastore/pcm/teastore.repository.json", "TeaStore"),
    "teammates": ("teammates/pcm/teammates.repository.json", "TEAMMATES"),
    "bigbluebutton": ("bigbluebutton/pcm/bbb.repository.json", "BigBlueButton"),
    "jabref": ("jabref/pcm/jabref.repository.json", "JabRef"),
}
TS = "2026-10-17T10:00:00Z"
CONFIDENCE = (0.95, 0.8, 0.65, 0.5)
STATUS_CYCLE = ("OPEN", "OPEN", "ACCEPTED", "REJECTED", "REOPENED", "DEFERRED", "OPEN", "RESOLVED")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def build(project: str) -> dict[str, list[dict[str, Any]]]:
    """Plain-data description of one project's KG (nodes by label, edges by type)."""
    model_file, name = PROJECTS[project]
    doc = json.loads((GOLDEN / project / f"{project}.txt.json").read_text(encoding="utf-8"))
    model = json.loads((GOLDEN / model_file).read_text(encoding="utf-8"))
    pid = project
    g: dict[str, list[dict[str, Any]]] = {
        k: [] for k in ("elements", "contains", "rel", "links", "incs", "concepts", "mentions", "artifacts", "ings")
    }
    runs = {k: "run_" + sha(f"{pid}:{k}")[:32] for k in ("ext", "tlr", "det")}
    g["project"] = [{"id": pid, "name": name}]
    g["runs"] = [{"id": r, "kind": k} for k, r in runs.items()]
    keys: dict[tuple[str, str], str] = {}
    for kind, path, label, content in (
        ("architecture-doc", f"benchmark/{project}/{project}.txt", "TextUnit", doc),
        ("architecture-model", f"benchmark/{model_file.rsplit('.json', 1)[0]}", "ModelElement", model),
    ):
        art = stable_id("art", pid, path)
        v1 = stable_id("ver", art, sha(path + "v1"))
        v2 = stable_id("ver", art, sha(path + "v2"))
        g["artifacts"].append({"id": art, "path": path, "kind": kind, "v1": v1, "v2": v2, "h1": sha(path + "v1"),
                               "h2": sha(path + "v2")})  # fmt: skip
        for e in content["elements"]:
            key = f"{art}#{e['localKey']}"
            keys[(path, e["localKey"])] = key
            text = (e.get("text") or "")[:4000]
            g["elements"].append({
                "id": stable_id("el", v2, e["localKey"]), "key": key, "label": label, "kind": e["kind"],
                "name": e["name"], "qualifiedName": e.get("qualifiedName"), "text": text or None,
                "startLine": e.get("startLine"), "endLine": e.get("endLine"), "sentenceIndex": e.get("sentenceIndex"),
                "contentHash": sha(text or e["name"]), "version": v2, "run": runs["ext"],
            })  # fmt: skip
            if e.get("parent"):
                g["contains"].append({"a": f"{art}#{e['parent']}", "b": key})
    model_path = g["artifacts"][1]["path"]
    for r in model["relations"]:
        g["rel"].append({"type": r["type"], "a": keys[(r["source"]["path"], r["source"]["localKey"])],
                         "b": keys[(r["target"]["path"], r["target"]["localKey"])]})  # fmt: skip
    doc_art = g["artifacts"][0]
    sentences = {e["sentenceIndex"] + 1: e["key"] for e in g["elements"] if e["sentenceIndex"] is not None}
    comps = {e["key"].split("#", 1)[1]: e for e in g["elements"] if e["kind"] == "component"}
    rows = list(csv.reader((HERE / "seed" / f"{project}-links.csv").read_text().splitlines()))[1:]
    linked_sent: set[int] = set()
    linked_comp: set[str] = set()
    for i, (model_id, sent) in enumerate(sorted(rows, key=lambda r: (int(r[1]), r[0]))):
        if model_id not in comps:
            continue
        s, t = sentences[int(sent)], comps[model_id]["key"]
        linked_sent.add(int(sent))
        linked_comp.add(model_id)
        g["links"].append({
            "id": stable_id("tl", "ardoco-sam", "0.1.0", "cfg", s, t), "s": s, "t": t, "linkType": "documents",
            "confidence": CONFIDENCE[i % 4], "approach": "ardoco-sam", "status": "confirmed" if i % 4 == 0 else "candidate",
            "active": True, "run": runs["tlr"],
        })  # fmt: skip
    for link in g["links"][:3]:  # superseded links of an earlier run (inactive)
        g["links"].append({**link, "id": stable_id("tl", "ardoco-sam", "0.0.9", "old", link["s"], link["t"]),
                           "confidence": 0.4, "status": "candidate", "active": False})  # fmt: skip
    n = 0
    for model_id, c in sorted(comps.items(), key=lambda kv: kv[1]["name"]):
        if model_id not in linked_comp:
            n += 1
            g["incs"].append(inc(pid, n, "undocumented-model-element", c["key"], f"Undocumented model element: {c['name']}",
                                 runs["det"]))  # fmt: skip
    for sent in sorted(set(sentences) - linked_sent):
        if sent % 5 == 0:
            n += 1
            g["incs"].append(inc(pid, n, "missing-model-element", sentences[sent], f"Missing model element in sentence {sent}",
                                 runs["det"]))  # fmt: skip
    for c in comps.values():  # concepts: component names mentioned (case-insensitive) in sentences
        pat = re.compile(re.escape(c["name"]), re.IGNORECASE)
        cid = stable_id("con", pid, c["name"].lower())
        hits = [e["key"] for e in g["elements"] if e["sentenceIndex"] is not None and pat.search(e["text"] or "")]
        if hits:
            g["concepts"].append({"id": cid, "name": c["name"]})
            g["mentions"] += [{"e": k, "c": cid, "confidence": 0.8, "run": runs["ext"]} for k in hits]
    g["ings"] = [
        {"id": stable_id("ing", pid, "1"), "seq": 1, "mode": "full", "rev": "v1"},
        {"id": stable_id("ing", pid, "2"), "seq": 2, "mode": "partial", "rev": "v2"},
    ]
    assert doc_art and model_path
    return g


def inc(pid: str, n: int, type_: str, key: str, title: str, run: str) -> dict[str, Any]:
    fp = sha(f"{type_}\x1f{key}")
    return {
        "id": stable_id("inc", pid, fp), "fingerprint": fp, "type": type_, "severity": "warning" if n % 3 else "info",
        "title": title, "status": STATUS_CYCLE[n % len(STATUS_CYCLE)], "detector": "ardoco-id" if n % 3 else "artemis-id",
        "key": key, "run": run, "evidenceHash": sha(key), "verification": "none", "n": n,
    }  # fmt: skip


LOAD = [
    """MERGE (p:Project {id: $project.id}) SET p.name = $project.name, p.llmPolicy = 'cloud-allowed', p.ingestionSeq = 2,
       p.createdAt = $ts""",
    """UNWIND $runs AS r CREATE (:Run {id: r.id, projectId: $pid, agent: CASE r.kind WHEN 'ext' THEN 'extraction-agent'
       WHEN 'tlr' THEN 'tlr-agent' ELSE 'inconsistency-agent' END, tool: CASE r.kind WHEN 'ext' THEN 'markdown-text'
       WHEN 'tlr' THEN 'ardoco-sam' ELSE 'ardoco-id' END, status: 'completed', replayMode: 'live', startedAt: $ts})""",
    """MATCH (p:Project {id: $pid}) UNWIND $ings AS i CREATE (p)-[:HAS_INGESTION]->(:Ingestion {id: i.id, projectId: $pid,
       seq: i.seq, mode: i.mode, sourceRevision: i.rev, actor: 'seed', unchangedCount: 0, eventPublished: true,
       createdAt: $ts})""",
    """MATCH (p:Project {id: $pid}) UNWIND $artifacts AS a
       CREATE (p)-[:HAS_ARTIFACT]->(x:Artifact {id: a.id, projectId: $pid, path: a.path, kind: a.kind})
       CREATE (v1:ArtifactVersion {id: a.v1, artifactId: a.id, projectId: $pid, contentHash: a.h1, sizeBytes: 1000,
              storageUri: 'artifact://' + $pid + '/' + a.h1, ingestionId: $ing1, ingestedAt: $ts})
       CREATE (v2:ArtifactVersion {id: a.v2, artifactId: a.id, projectId: $pid, contentHash: a.h2, sizeBytes: 1200,
              storageUri: 'artifact://' + $pid + '/' + a.h2, ingestionId: $ing2, ingestedAt: $ts})
       CREATE (x)-[:HAS_VERSION]->(v1), (x)-[:HAS_VERSION]->(v2), (x)-[:CURRENT_VERSION]->(v2)
       WITH v1, v2 MATCH (i1:Ingestion {id: $ing1}), (i2:Ingestion {id: $ing2})
       CREATE (i1)-[:ACTIVATED]->(v1), (i2)-[:ACTIVATED]->(v2)""",
    """UNWIND $elements AS e MATCH (v:ArtifactVersion {id: e.version}), (r:Run {id: e.run})
       CREATE (x:Element {id: e.id, key: e.key, projectId: $pid, kind: e.kind, name: e.name, qualifiedName: e.qualifiedName,
              text: e.text, startLine: e.startLine, endLine: e.endLine, sentenceIndex: e.sentenceIndex,
              contentHash: e.contentHash, runId: e.run})
       CREATE (v)-[:HAS_ELEMENT]->(x), (r)-[:PRODUCED]->(x)""",
]


def load(session: Any, project: str) -> None:
    g = build(project)
    ings = g["ings"]
    common = {"pid": project, "ts": TS, "ing1": ings[0]["id"], "ing2": ings[1]["id"]}

    def run(q: str, **p: Any) -> None:
        session.run(q, **(common | p)).consume()

    run(LOAD[0], project=g["project"][0])
    run(LOAD[1], runs=g["runs"])
    run(LOAD[2], ings=ings)
    run(LOAD[3], artifacts=g["artifacts"])
    for label in ("TextUnit", "ModelElement"):  # secondary label per element
        run(LOAD[4], elements=[e for e in g["elements"] if e["label"] == label])
        run(f"MATCH (x:Element {{projectId: $pid}}) WHERE x.key IN $keys SET x:{label}",
            keys=[e["key"] for e in g["elements"] if e["label"] == label])  # fmt: skip
    for rel, rows in (("CONTAINS", g["contains"]),):
        run(
            f"UNWIND $rows AS r MATCH (a:Element {{key: r.a}}), (b:Element {{key: r.b}}) CREATE (a)-[:{rel}]->(b)",
            rows=rows,
        )
    for t in ("PROVIDES", "REQUIRES"):
        run(f"UNWIND $rows AS r MATCH (a:Element {{key: r.a}}), (b:Element {{key: r.b}}) CREATE (a)-[:{t}]->(b)",
            rows=[r for r in g["rel"] if r["type"] == t])  # fmt: skip
    run("""UNWIND $rows AS l MATCH (s:Element {key: l.s}), (t:Element {key: l.t}), (r:Run {id: l.run})
           CREATE (x:TraceLink {id: l.id, projectId: $pid, linkType: l.linkType, confidence: l.confidence,
                  approach: l.approach, approachVersion: '0.1.0', configHash: 'cfg', status: l.status, active: l.active,
                  runId: l.run, createdAt: $ts}) CREATE (x)-[:SOURCE]->(s), (x)-[:TARGET]->(t), (r)-[:PRODUCED]->(x)""",
        rows=g["links"])  # fmt: skip
    run(
        """UNWIND $rows AS i MATCH (e:Element {key: i.key}), (r:Run {id: i.run})
           CREATE (x:Inconsistency {id: i.id, fingerprint: i.fingerprint, projectId: $pid, type: i.type,
                  severity: i.severity, title: i.title, status: i.status, verification: i.verification,
                  detector: i.detector, detectorVersion: '0.1.0', firstRunId: i.run, lastRunId: i.run,
                  evidenceHash: i.evidenceHash, createdAt: $ts, updatedAt: $ts})
           CREATE (x)-[:INVOLVES {role: 'subject'}]->(e), (r)-[:DETECTED]->(x)
           WITH x, i WHERE i.status <> 'OPEN'
           CREATE (x)-[:HAS_STATUS_CHANGE]->(:StatusChange {to: i.status, actor: 'user:seed', at: $ts})""",
        rows=g["incs"],
    )
    run("UNWIND $rows AS c CREATE (:Concept {id: c.id, projectId: $pid, name: c.name})", rows=g["concepts"])
    run(
        """UNWIND $rows AS m MATCH (e:Element {key: m.e}), (c:Concept {id: m.c})
           CREATE (e)-[:MENTIONS {confidence: m.confidence, runId: m.run}]->(c)""",
        rows=g["mentions"],
    )


def start(bolt: str | None, reload: bool = True) -> tuple[Any, Any]:
    """(driver, container or None)."""
    container = None
    if bolt is None:
        container = DockerContainer("memgraph/memgraph:3.13.1").with_command("--telemetry-enabled=false")
        container.with_exposed_ports(7687).start()
        bolt = f"bolt://{container.get_container_host_ip()}:{container.get_exposed_port(7687)}"
    driver = GraphDatabase.driver(bolt)
    deadline = time.monotonic() + 60
    while True:
        try:
            driver.verify_connectivity()
            break
        except Exception:  # noqa: BLE001 - not ready yet
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)
    if not reload:
        return driver, container
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n").consume()  # a given --bolt instance is reset to the seed state
        for line in (ROOT / "contracts/kg/init.cypher").read_text().splitlines():
            if line.strip() and not line.startswith("//"):
                s.run(line.strip().rstrip(";")).consume()
        for p in PROJECTS:
            load(s, p)
    return driver, container


def execute(driver: Any, cypher: str, params: dict[str, Any]) -> dict[str, Any]:
    def work(tx: Any) -> dict[str, Any]:
        result = tx.run(cypher, params)
        return {"columns": list(result.keys()), "rows": [[jsonable(v) for v in r.values()] for r in result]}

    with driver.session() as s:
        try:
            return s.execute_read(work)  # type: ignore[no-any-return]
        except Exception as e:  # noqa: BLE001 - reported as the prediction error
            return {"error": str(e)}


def jsonable(v: Any) -> Any:
    if isinstance(v, list):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    return v if v is None or isinstance(v, (str, int, float, bool)) else str(v)


def cmd_gold(driver: Any, args: argparse.Namespace) -> int:
    items = yaml.safe_load(args.dataset.read_text(encoding="utf-8"))["items"]
    keys = {r[0] for r in execute(driver, "MATCH (e:Element) RETURN e.key AS key", {})["rows"]}
    in_project = {
        i["project"]: {r[0] for r in execute(driver, "MATCH (e:Element {projectId: $p}) RETURN e.key", {"p": i["project"]})["rows"]}
        for i in items
    }  # fmt: skip
    results: dict[str, Any] = {}
    bad = []
    for i in items:
        if i["strategy"] != "cypher":
            missing = [k for k in i["gold_keys"] if k not in in_project[i["project"]]]
            if missing:
                bad.append(f"{i['id']}: gold keys not in project {i['project']}: {missing[:3]}")
        if not i.get("cypher"):
            continue
        try:
            check(i["cypher"], scoped=True)
        except Exception as e:  # noqa: BLE001
            bad.append(f"{i['id']}: guard rejects the gold cypher: {e}")
            continue
        r = execute(driver, i["cypher"], {"projectId": i["project"]})
        if "error" in r:
            bad.append(f"{i['id']}: {r['error']}")
        elif not r["rows"]:
            bad.append(f"{i['id']}: empty gold result")
        elif i["strategy"] != "cypher":
            col = [row[0] for row in r["rows"]]
            if set(col) != set(i["gold_keys"]):
                bad.append(
                    f"{i['id']}: gold_keys differ from the reference cypher ({len(col)} vs {len(i['gold_keys'])})"
                )
        results[i["id"]] = r
    assert keys
    args.out.write_text(json.dumps(results, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(f"validated {len(results)} gold queries of {len(items)} items; {len(bad)} problems")
    print("\n".join(bad))
    return 1 if bad else 0


def cmd_run(driver: Any, args: argparse.Namespace) -> int:
    pred = json.loads(args.predictions.read_text(encoding="utf-8"))
    out = {
        k: execute(driver, v["cypher"], v["params"]) if v.get("cypher") else {"error": v.get("error", "no query")}
        for k, v in pred.items()
    }
    args.out.write_text(json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bolt", help="use this Memgraph instead of a testcontainer (loads the seed KG into it)")
    p.add_argument("--no-load", action="store_true", help="with --bolt: keep the loaded KG (authoring)")
    sub = p.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("query", help="run ad-hoc Cypher (for authoring), parameters: $projectId")
    q.add_argument("--project", required=True)
    q.add_argument("cypher")
    g = sub.add_parser("gold")
    g.add_argument("--dataset", type=Path, default=HERE / "dataset.yaml")
    g.add_argument("--out", type=Path, default=HERE / "gold-results.json")
    r = sub.add_parser("run")
    r.add_argument("predictions", type=Path)
    r.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    driver, container = start(args.bolt, not args.no_load)
    try:
        if args.cmd == "query":
            print(json.dumps(execute(driver, args.cypher, {"projectId": args.project}), ensure_ascii=False))
            return 0
        return cmd_gold(driver, args) if args.cmd == "gold" else cmd_run(driver, args)
    finally:
        driver.close()
        if container:
            container.stop()


if __name__ == "__main__":
    sys.exit(main())

"""Performance budget harness (T5.4, docs/tasks/T5.4-performance-budget.md).

Run: `make perf` (uv run python scripts/perf.py [--files 1000] [--delta 100] [--kg-nodes 200000] [--keep]).

Starts the scratch compose project `sandman-perf` (never `sandman`): Memgraph on 127.0.0.1:17687, query-service on
127.0.0.1:18080, CoreNLP local, plugin stubs, LLM enrichment and LiSSA off (no LLM credentials needed). Then:
1. ingest of a deterministic synthetic project (`--files` files, md/xml/java), end to end up to the workflow end;
2. incremental ingest of `--delta` changed files;
3. Memgraph: seeded KG (experiments/nlquery/seed_kg.py data, cloned per project id) up to `--kg-nodes` nodes, memory and
   snapshot size per step, latency of the NL benchmark Cypher queries over bolt and a few queries over the HTTP path.
Container memory comes from `docker stats` (sampled every 3 s). Output: JSON file and a table; exit 1 if a budget fails.
"""

import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import platform
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "experiments/nlquery"), str(ROOT / "tests/e2e")]

PROJECT = "sandman-perf"
BOLT = "bolt://127.0.0.1:17687"
HTTP = "http://127.0.0.1:18080"
SERVICES = [
    "memgraph",
    "kg-migrate",
    "nats",
    "llm-gateway",
    "llm-replay",
    "extraction-agent",
    "code-extractor-ardoco",
    "tlr-agent",
    "inconsistency-agent",
    "orchestrator",
    "query-service",
    "corenlp-gateway",
    "corenlp-provider",
    "plugin-stubs",
]
BUDGET: dict[str, tuple[str, float]] = {  # id -> (unit, limit); keep equal to docs/tasks/T5.4-performance-budget.md
    "ingest-upload": ("s", 60),
    "ingest-e2e": ("s", 900),
    "ingest-extraction": ("s", 300),
    "ingest-tlr": ("s", 480),
    "detection": ("s", 120),
    "incremental-e2e": ("s", 300),
    "query-bolt-p50": ("ms", 20),
    "query-bolt-p95": ("ms", 250),
    "query-http-p95": ("ms", 1000),
    "memgraph-memory": ("MiB", 4096),
    "memgraph-bytes-per-node": ("KiB", 16),
    "snapshot-size": ("MiB", 1024),
    "snapshot-time": ("s", 60),
    "container-memory": ("% of mem_limit", 85),
}
NOUNS = ["Billing", "Catalog", "Media", "Order", "Auth", "Search", "Report", "Cache", "Queue", "Store", "Mail", "Audit"]
ADJ = ["Core", "Edge", "Smart", "Fast", "Safe", "Open", "Live", "Deep", "Flat", "Warm"]
VERBS = ["stores", "validates", "forwards", "caches", "indexes", "renders", "signs", "archives", "schedules"]
OBJECTS = ["requests", "records", "sessions", "invoices", "events", "tokens", "reports", "messages", "files"]
KINDS = """kindRules:
  - { glob: "requirements/**", kind: requirements }
  - { glob: "docs/**", kind: architecture-doc }
  - { glob: "model/**", kind: architecture-model }
  - { glob: "src/test/**", kind: test }
  - { glob: "src/main/**", kind: code }
"""
SIZE = {"B": 1 / 1048576, "KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "TiB": 1048576}


def mib(text: str) -> float:
    """'1.5GiB' or '12.3 MiB' -> MiB."""
    m = re.fullmatch(r"\s*([0-9.]+)\s*([KMGT]?i?B)\s*", text)
    assert m, text
    return float(m[1]) * SIZE[m[2] if m[2].endswith("iB") or m[2] == "B" else m[2][0] + "iB"]


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)]


def generate(out: Path, files: int, seed: int = 20261018) -> list[Path]:
    """Deterministic synthetic project: requirements, architecture docs, PCM models, config XML, Java code and tests."""
    rng = random.Random(seed)
    n_req, n_doc, n_model, n_xml, n_test = (max(1, round(files * f)) for f in (0.04, 0.20, 0.01, 0.05, 0.10))
    n_main = files - n_req - n_doc - n_model - n_xml - n_test
    comps = [f"{a}{b}" for a in ADJ for b in NOUNS][: max(8, files // 12)]
    reqs = [f"REQ-{i}" for i in range(1, n_req * 8 + 1)]
    paths: dict[Path, str] = {Path("sandman.yaml"): KINDS}
    for i in range(n_req):
        body = "".join(
            f"## {r}\n\nThe system shall let {rng.choice(comps)} {rng.choice(VERBS)} {rng.choice(OBJECTS)}.\n\n"
            for r in reqs[i * 8 : i * 8 + 8]
        )
        paths[Path(f"requirements/req-{i:04d}.md")] = f"# Requirements {i}\n\n{body}"
    for i in range(n_doc):
        used = rng.sample(comps, 3)
        text = " ".join(
            f"The {rng.choice(used)} component {rng.choice(VERBS)} {rng.choice(OBJECTS)} for {rng.choice(used)}."
            for _ in range(24)
        )
        paths[Path(f"docs/arch-{i:04d}.md")] = f"# Architecture {i}\n\n{text}\n"
    per_model = math.ceil(len(comps) / n_model)
    for i in range(n_model):
        items = "".join(
            f'  <components__Repository xsi:type="repository:BasicComponent" id="_perf_{c}" entityName="{c}"/>\n'
            for c in comps[i * per_model : (i + 1) * per_model]
        )
        paths[Path(f"model/perf-{i:03d}.repository")] = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<repository:Repository xmi:version="2.0" '
            'xmlns:xmi="http://www.omg.org/XMI" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:repository="http://palladiosimulator.org/PalladioComponentModel/Repository/5.2" '
            f'id="_perf_repo_{i}" entityName="perf{i}">\n{items}</repository:Repository>\n'
        )
    for i in range(n_xml):
        paths[Path(f"config/cfg-{i:04d}.xml")] = (
            f'<?xml version="1.0"?>\n<config id="{i}"><v>{rng.random():.6f}</v></config>\n'
        )
    for kind, count, folder, suffix in (
        ("main", n_main, "src/main/java", ""),
        ("test", n_test, "src/test/java", "Test"),
    ):
        for i in range(count):
            comp = comps[i % len(comps)]
            name = f"{comp}{rng.choice(OBJECTS).title()}{kind.title()}{i}{suffix}"
            ref = f"Implements {rng.choice(reqs)}." if rng.random() < 0.6 else "Helper."
            methods = "".join(
                f"    public int m{j}(int x) {{ return x + {rng.randint(1, 99)}; }}\n" for j in range(rng.randint(3, 8))
            )
            paths[Path(f"{folder}/perf/{comp.lower()}/{name}.java")] = (
                f"package perf.{comp.lower()};\n\n/** {ref} Part of {comp}. */\npublic class {name} {{\n{methods}}}\n"
            )
    for rel, text in paths.items():
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return sorted(out / rel for rel in paths)


def iso(text: str) -> datetime:
    return datetime.fromisoformat(text)


class Compose:
    def __init__(self, tmp: Path, keep: bool) -> None:
        self.keep = keep
        self.cfg = tmp / "config"
        self.override = tmp / "override.yml"
        self.env = {**os.environ, "SANDMAN_CORENLP_MODE": "local", "COMPOSE_PROJECT_NAME": PROJECT}

    def prepare(self) -> None:
        shutil.copytree(ROOT / "config", self.cfg)
        enrich = self.cfg / "extraction/enrichment.yaml"
        enrich.write_text(re.sub(r"(?m)^enabled: true", "enabled: false", enrich.read_text()))
        approaches = self.cfg / "tlr/approaches.yaml"
        approaches.write_text(approaches.read_text().replace("lissa: { enabled: true", "lissa: { enabled: false"))
        mount = {"volumes": [f"{self.cfg}:/config:ro"]}
        services = {s: dict(mount) for s in ("extraction-agent", "tlr-agent", "inconsistency-agent", "orchestrator")}
        services["query-service"] = {**mount, "networks": ["internal", "edge"], "ports": ["127.0.0.1:18080:8000"]}
        services["memgraph"] = {"networks": ["internal", "edge"], "ports": ["127.0.0.1:17687:7687"]}
        self.override.write_text(yaml.safe_dump({"services": services}))

    def dc(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = [
            "docker",
            "compose",
            "-p",
            PROJECT,
            "-f",
            str(ROOT / "deploy/docker-compose.yml"),
            "-f",
            str(self.override),
        ]
        cmd += ["--profile", "corenlp-local", "--profile", "stubs", *args]
        run = subprocess.run(cmd, capture_output=True, text=True, env=self.env, cwd=ROOT)
        assert not check or run.returncode == 0, f"{' '.join(args)}: {run.stderr[-2000:]}"
        return run

    def up(self) -> float:
        subprocess.run([str(ROOT / "deploy/check-env.sh")], check=True, env=self.env, cwd=ROOT)
        self.dc("down", "-v", "--remove-orphans")
        started = time.monotonic()
        self.dc("up", "-d", "--build", "--wait", "--wait-timeout", "600", *SERVICES)
        return time.monotonic() - started

    def down(self) -> None:
        if not self.keep:
            self.dc("down", "-v", "--remove-orphans", check=False)


def docker_api() -> httpx.Client:
    sock = os.environ.get("DOCKER_HOST", "unix://" + str(Path.home() / ".docker/run/docker.sock"))
    return httpx.Client(transport=httpx.HTTPTransport(uds=sock.removeprefix("unix://")), base_url="http://docker")


def project_containers(api: httpx.Client) -> list[dict[str, Any]]:
    query = json.dumps({"label": [f"com.docker.compose.project={PROJECT}"]})
    return api.get("/containers/json", params={"filters": query, "all": "true"}).json()


class Sampler(threading.Thread):
    """Peak memory per container of the project from `docker stats`."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.peak: dict[str, float] = {}
        self.limit: dict[str, float] = {}

    def snapshot(self) -> None:
        with docker_api() as api:
            for box in project_containers(api):
                if box["State"] != "running":
                    continue
                stats = api.get(
                    f"/containers/{box['Id']}/stats", params={"stream": "false", "one-shot": "true"}
                ).json()["memory_stats"]
                key = box["Labels"]["com.docker.compose.service"]
                used = (stats["usage"] - stats.get("stats", {}).get("inactive_file", 0)) / 1048576
                self.peak[key] = max(self.peak.get(key, 0.0), used)
                self.limit[key] = stats["limit"] / 1048576

    def run(self) -> None:
        while not self.stop.is_set():
            self.snapshot()
            self.stop.wait(3)


def storage_info(driver: Any) -> dict[str, Any]:
    with driver.session() as s:
        info = {r[0]: r[1] for r in s.run("SHOW STORAGE INFO")}
        vertices = s.run("MATCH (n) RETURN count(n)").single()[0]
        edges = s.run("MATCH ()-[r]->() RETURN count(r)").single()[0]
    return {
        "vertices": vertices,
        "edges": edges,
        "memoryResMiB": round(mib(str(info["memory_res"])), 1),
        "memoryTrackedMiB": round(mib(str(info["memory_tracked"])), 1),
    }


def snapshot_size(driver: Any, comp: Compose) -> dict[str, float]:
    started = time.monotonic()
    with driver.session() as s:
        s.run("CREATE SNAPSHOT").consume()
    took = time.monotonic() - started
    out = comp.dc("exec", "-T", "memgraph", "sh", "-c", "stat -c %s $(ls -t /var/lib/memgraph/snapshots/* | head -n 1)")
    return {"sizeMiB": round(int(out.stdout.strip()) / 1048576, 2), "seconds": round(took, 2)}


def ingest_phase(stack: Any, root: Path, label: str, known: set[str], timeout: float) -> dict[str, Any]:
    from sandman_cli.__main__ import main as sandman

    os.environ.update(SANDMAN_URL=HTTP, SANDMAN_TOKEN=stack.alice_token)
    started = time.monotonic()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        rc = sandman(["ingest", "--project", stack.project, "--mode", "full", "--revision", label, str(root)])
    upload = time.monotonic() - started
    ingestion = json.loads(buf.getvalue())
    assert rc == 0
    deadline = time.monotonic() + timeout
    done: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        new = [w for w in stack.workflows() if w["id"] not in known]
        if new and all(w["status"] in ("SUCCEEDED", "FAILED", "CANCELED", "BLOCKED") for w in new):
            done = new
            break
        time.sleep(3)
    total = time.monotonic() - started
    result: dict[str, Any] = {
        "uploadSeconds": round(upload, 2),
        "endToEndSeconds": round(total, 2),
        "createdVersions": len(ingestion["createdVersionIds"]),
        "workflows": [],
    }
    for w in done:
        known.add(w["id"])
        detail = stack.users["alice"].get(f"/v1/workflows/{w['id']}").json()
        steps = {}
        for st in detail["steps"]:
            seconds = (iso(st["endedAt"]) - iso(st["startedAt"])).total_seconds() if st.get("endedAt") else None
            steps[f"{st['skill']}#{st['id'][-4:]}"] = {"status": st["status"], "seconds": seconds}
        result["workflows"].append({"status": detail["status"], "steps": steps})
    result["timedOut"] = not done
    return result


def step_seconds(phase: dict[str, Any], skill: str) -> float | None:
    values = [
        st["seconds"]
        for w in phase["workflows"]
        for key, st in w["steps"].items()
        if key.startswith(skill) and st["seconds"] is not None
    ]
    return round(sum(values), 2) if values else None


def seed_clones(driver: Any, target_nodes: int, current: int) -> list[dict[str, Any]]:
    """Loads clones of the five benchmark projects (new ids per copy) until the KG has `target_nodes` nodes."""
    import seed_kg

    original = seed_kg.build

    def cloned(name: str) -> dict[str, list[dict[str, Any]]]:
        base, copy = name.rsplit("-c", 1)
        text = json.dumps(original(base))
        text = re.sub(
            r"\b([a-z]+)_([0-9a-f]{32})\b",
            lambda m: f"{m[1]}_{hashlib.sha256((m[0] + copy).encode()).hexdigest()[:32]}",
            text,
        )
        text = text.replace(f'"id": "{base}"', f'"id": "{name}"')
        return json.loads(text)  # type: ignore[no-any-return]

    seed_kg.build = cloned
    steps = []
    try:
        with driver.session() as s:
            for stmt in (ROOT / "contracts/kg/init.cypher").read_text().splitlines():
                if stmt.strip() and not stmt.startswith("//") and "VECTOR" not in stmt:
                    with contextlib.suppress(Exception):
                        s.run(stmt.strip().rstrip(";")).consume()
            copy = 0
            while current < target_nodes:
                for base in seed_kg.PROJECTS:
                    seed_kg.load(s, f"{base}-c{copy}")
                copy += 1
                info = storage_info(driver)
                current = info["vertices"]
                steps.append({"copies": copy, **info})
    finally:
        seed_kg.build = original
    return steps


def query_latency(driver: Any, copies: int, reps: int) -> dict[str, Any]:
    import seed_kg

    items = [
        i for i in yaml.safe_load((ROOT / "experiments/nlquery/dataset.yaml").read_text())["items"] if i.get("cypher")
    ]
    samples: list[float] = []
    slow: list[tuple[float, str]] = []
    errors = 0
    for rep in range(reps):
        for n, item in enumerate(items):
            project = f"{item['project']}-c{(rep + n) % copies}" if copies else item["project"]
            started = time.perf_counter()
            result = seed_kg.execute(driver, item["cypher"], {"projectId": project})
            took = (time.perf_counter() - started) * 1000
            errors += "error" in result
            samples.append(took)
            slow.append((took, item["id"]))
    return {
        "queries": len(items),
        "samples": len(samples),
        "errors": errors,
        "p50Ms": round(statistics.median(samples), 2),
        "p95Ms": round(percentile(samples, 0.95), 2),
        "maxMs": round(max(samples), 2),
        "slowest": [{"id": i, "ms": round(t, 1)} for t, i in sorted(slow, reverse=True)[:5]],
    }


HTTP_QUERIES = [
    "MATCH (e:Element {projectId: $projectId}) RETURN e.kind AS kind, count(e) AS n ORDER BY kind",
    "MATCH (e:Element {projectId: $projectId, kind: 'class'}) WHERE e.name CONTAINS 'Billing' RETURN e.name LIMIT 50",
    "MATCH (t:TraceLink {projectId: $projectId, active: true})-[:SOURCE]->(s) RETURN s.key, count(t) LIMIT 100",
    "MATCH (i:Inconsistency {projectId: $projectId}) RETURN i.type, i.status, count(i)",
    "MATCH (t:TraceLink {projectId: $projectId, active: true})-[:TARGET]->(g) RETURN g.name, t.approach LIMIT 200",
]


def http_latency(stack: Any, reps: int) -> dict[str, Any]:
    samples = []
    for _ in range(reps):
        for text in HTTP_QUERIES:
            started = time.perf_counter()
            resp = stack.cypher("alice", text)
            samples.append((time.perf_counter() - started) * 1000)
            assert resp.status_code == 200, resp.text
    return {
        "samples": len(samples),
        "p50Ms": round(statistics.median(samples), 2),
        "p95Ms": round(percentile(samples, 0.95), 2),
        "maxMs": round(max(samples), 2),
    }


def oom_killed() -> list[str]:
    with docker_api() as api:
        return [
            box["Labels"]["com.docker.compose.service"]
            for box in project_containers(api)
            if api.get(f"/containers/{box['Id']}/json").json()["State"]["OOMKilled"]
        ]


def evaluate(result: dict[str, Any]) -> list[dict[str, Any]]:
    ing, inc, kg = result.get("ingest"), result.get("incremental"), result.get("memgraph")
    values: dict[str, float | None] = {}
    if ing:
        values |= {
            "ingest-upload": ing["uploadSeconds"],
            "ingest-e2e": ing["endToEndSeconds"],
            "ingest-extraction": step_seconds(ing, "extract-artifact-versions"),
            "ingest-tlr": step_seconds(ing, "recover-trace-links"),
            "detection": step_seconds(ing, "detect-inconsistencies"),
        }
    if inc:
        values["incremental-e2e"] = inc["endToEndSeconds"]
    if kg:
        last = kg["steps"][-1]
        values |= {
            "query-bolt-p50": kg["bolt"]["p50Ms"],
            "query-bolt-p95": kg["bolt"]["p95Ms"],
            "query-http-p95": kg["http"]["p95Ms"],
            "memgraph-memory": last["memoryResMiB"],
            "memgraph-bytes-per-node": round(last["memoryTrackedMiB"] * 1024 / max(1, last["vertices"]), 2),
            "snapshot-size": kg["snapshot"]["sizeMiB"],
            "snapshot-time": kg["snapshot"]["seconds"],
        }
    mem = result.get("containers", {})
    if mem:
        values["container-memory"] = max(round(100 * v["peakMiB"] / v["limitMiB"], 1) for v in mem.values())
    rows = []
    for key, (unit, limit) in BUDGET.items():
        value = values.get(key)
        verdict = "NOT RUN" if value is None else ("PASS" if value <= limit else "FAIL")
        rows.append({"id": key, "unit": unit, "limit": limit, "value": value, "verdict": verdict})
    return rows


def table(rows: list[dict[str, Any]]) -> str:
    lines = [f"{'budget item':26} {'limit':>8} {'measured':>10}  unit             verdict"]
    lines += [
        f"{r['id']:26} {r['limit']:>8} {'-' if r['value'] is None else r['value']:>10}  {r['unit']:16} {r['verdict']}"
        for r in rows
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument(
        "--files", type=int, default=1000, help="files of the synthetic project (changed files of ingest 1)"
    )
    ap.add_argument("--delta", type=int, default=100, help="files changed for the incremental ingest (0: skip)")
    ap.add_argument("--kg-nodes", type=int, default=200_000, help="seeded KG size for memory and query latency")
    ap.add_argument("--reps", type=int, default=3, help="repetitions of the query set")
    ap.add_argument("--timeout", type=float, default=3600, help="seconds per workflow")
    ap.add_argument("--out", type=Path, default=ROOT / ".work/perf/result.json")
    ap.add_argument("--keep", action="store_true", help="leave project sandman-perf running")
    args = ap.parse_args(argv)
    assert PROJECT != "sandman"

    from kg_canonical_export import connect

    tmp = Path(tempfile.mkdtemp(prefix="sandman-perf-"))
    comp = Compose(tmp, args.keep)
    comp.prepare()
    sampler = Sampler()
    result: dict[str, Any] = {
        "date": datetime.now().astimezone().isoformat(timespec="seconds"),
        "machine": {"platform": platform.platform(), "cpus": os.cpu_count()},
        "files": args.files,
        "delta": args.delta,
    }
    try:
        result["startSeconds"] = round(comp.up(), 1)
        info = subprocess.run(
            ["docker", "info", "--format", "{{.MemTotal}} {{.NCPU}} {{.ServerVersion}}"], capture_output=True, text=True
        )
        total, cpus, version = info.stdout.split()
        result["docker"] = {"memGiB": round(int(total) / 2**30, 1), "cpus": int(cpus), "version": version}
        sampler.start()
        sampler.snapshot()
        result["idleMiB"] = dict(sampler.peak)
        os.environ["SANDMAN_E2E_URL"] = HTTP
        import test_walking_skeleton as e2e

        stack = e2e.Stack(project="perf")
        known: set[str] = set()
        src = tmp / "project"
        generate(src, args.files)
        result["ingest"] = ingest_phase(stack, src, "perf-1", known, args.timeout)
        if args.delta:
            rng = random.Random(1)
            for path in rng.sample(sorted(src.glob("src/main/java/**/*.java")), args.delta):
                path.write_text(path.read_text() + "// rev 2\n")
            result["incremental"] = ingest_phase(stack, src, "perf-2", known, args.timeout)
        with connect(BOLT) as driver:
            steps = [{"copies": 0, **storage_info(driver)}]
            steps += seed_clones(driver, args.kg_nodes, steps[0]["vertices"])
            copies = steps[-1]["copies"]
            result["memgraph"] = {
                "steps": steps,
                "snapshot": snapshot_size(driver, comp),
                "bolt": query_latency(driver, copies, args.reps),
                "http": http_latency(stack, 20),
            }
        sampler.stop.set()
        sampler.join()
        result["containers"] = {
            k: {"peakMiB": round(v, 1), "limitMiB": round(sampler.limit[k], 1)} for k, v in sorted(sampler.peak.items())
        }
        result["oomKilled"] = oom_killed()
    finally:
        sampler.stop.set()
        comp.down()
        shutil.rmtree(tmp, ignore_errors=True)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        result["budget"] = evaluate(result)
        args.out.write_text(json.dumps(result, indent=1) + "\n")
    print(table(result["budget"]))
    print(f"result: {args.out}")
    return 1 if result["oomKilled"] or any(r["verdict"] == "FAIL" for r in result["budget"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())

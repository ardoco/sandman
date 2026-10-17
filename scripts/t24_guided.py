#!/usr/bin/env python3
# ruff: noqa: E501
"""Guided run of all T2.4 checks (docs/user/evaluation.md). Writes results into docs/tasks/T2.4-reproduction-study.md.

uv run python scripts/t24_guided.py [--resume [RUN_ID]] [--only STEP...] [--yes] [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs/tasks/T2.4-reproduction-study.md"
LIVE = ROOT / "experiments/live"
OUT = ROOT / "out"
FIXTURES = ROOT / "services/extraction-agent/tests/fixtures/benchmark"
START, END = "<!-- T2.4-RESULTS:START -->", "<!-- T2.4-RESULTS:END -->"
HEADER = ["| Check | Result | Date |", "|---|---|---|"]
R_MVN = "`BenchmarkEquivalenceTest` (adapter against standalone runner)"
R_EQ = "`sandman eval equal` (`ardoco-sad-sam`)"
R_MG = "Memgraph integration tests (`services/query-service`)"
R_LISSA = "LiSSA live re-run with Δ and CI"
R_PILOT = "Variance pilot (run count)"
R_CONT = "Contamination probe"
ROWS = [R_MVN, R_EQ, R_MG, R_LISSA, R_PILOT, R_CONT]
NOT_REPORTED = ("Not reported", "–")
PCM_CASES = [("mediastore", "ms.repository", "pcm"), ("teastore", "teastore.repository", "pcm")]
STEPS = {
    0: "Preflight",
    1: "Memgraph integration tests",
    2: "ArDoCo check A (adapter against standalone)",
    3: "ArDoCo check B (live stack against standalone)",
    4: "LiSSA live re-run",
    5: "Variance pilot",
    6: "Contamination probe",
}


def today() -> str:
    return datetime.date.today().isoformat()


def git_head() -> str:
    r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip() or "unknown"


def f1_of(compare_json: str) -> float:
    return float(json.loads(compare_json)["a"]["f1"])


def failed_steps(wf: dict) -> list[str]:
    """One line per workflow step that did not succeed, with the likely cause."""
    lines = []
    for s in wf.get("steps", []):
        if s.get("status") == "SUCCEEDED":
            continue
        err = s.get("error") or {}
        text = f"{s.get('agent')}/{s.get('skill')}: {s.get('status')} {err.get('code', '')} {err.get('message', '')}".strip()
        low = text.lower()
        if "corenlp" in low or "credential" in low or "unauthorized" in low:
            text += "  <- likely cause: CoreNLP credentials (set SANDMAN_CORENLP_MODE=local or fill deploy/secrets/corenlp_credentials)"
        elif "no model" in low or "no document" in low or "empty" in low:
            text += "  <- likely cause: kind rules (ingest printed other=N for all files)"
        lines.append(text)
    return lines


def code_files(inp: Path) -> list[str]:
    files = sorted(str(p.relative_to(inp)) for e in ("*.java", "*.py") for p in (inp / "src").rglob(e) if p.is_file())
    if not files:
        raise ValueError(f"no code files under {inp}/src")
    names = [Path(f).name for f in files]
    dups = sorted({n for n in names if names.count(n) > 1})
    if dups:
        raise ValueError(f"duplicate file names: {', '.join(dups)}")
    return files


def copy_back(flat: Path, perturbed: Path, rels: list[str]) -> None:
    for rel in rels:
        shutil.copy(flat / Path(rel).name, perturbed / rel)


def parse_rows(block: str) -> dict[str, tuple[str, str]]:
    rows = {}
    for line in block.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] in ROWS:
            rows[cells[0]] = (cells[1], cells[2])
    return rows


def status_line(line: str, rows: dict[str, tuple[str, str]]) -> str:
    drop = ("ArDoCo exact", "LiSSA", "variance pilot", "contamination", "live checks")
    parts = [p for p in line.removeprefix("Status:").strip().split(" · ") if not p.startswith(drop)]
    new, open_names = [], []
    for label, name in (
        (R_EQ, "ArDoCo exact equality"),
        (R_LISSA, "LiSSA re-run"),
        (R_PILOT, "variance pilot"),
        (R_CONT, "contamination probe"),
    ):
        result, date = rows.get(label, NOT_REPORTED)
        if result.startswith("Not reported"):
            open_names.append(name)
        else:
            ok = result.startswith(("Exit code 0", "Passed", "Δ", "n ", "Not flagged", "Flagged"))
            new.append(f"{name} {'done' if ok else 'reported, check Results'} ({date.split(' ')[0]})")
    if open_names:
        new.append(f"{', '.join(open_names)} not yet reported (see Results)")
        new.append("live checks postponed, guided script `scripts/t24_guided.py` ready")
    return "Status: " + " · ".join(parts[:1] + new + parts[1:])


def update_results(text: str, updates: dict[str, tuple[str, str]]) -> str:
    """Rewrite the Results table between the markers; wrap the existing table on first use. Idempotent."""
    if START in text:
        head, rest = text.split(START, 1)
        old, tail = rest.split(END, 1)
    else:
        m = re.search(r"^\| Check \| Result \| Date \|\n(?:\|.*\n?)+", text, re.M)
        if not m:
            raise ValueError("Results table not found")
        head, old = text[: m.start()], m.group(0)
        tail = text[m.end() - 1 :] if old.endswith("\n") else text[m.end() :]
    rows = {**{r: NOT_REPORTED for r in ROWS}, **parse_rows(old), **updates}
    body = "\n".join(HEADER + [f"| {r} | {rows[r][0]} | {rows[r][1]} |" for r in ROWS])
    out = f"{head}{START}\n{body}\n{END}{tail}"
    return re.sub(r"^Status:.*$", lambda m: status_line(m.group(0), rows), out, count=1, flags=re.M)


def build_updates(results: dict, commit: str) -> dict[str, tuple[str, str]]:
    """Table rows from the real results in the state; steps that did not run give no row."""
    u: dict[str, tuple[str, str]] = {}

    def row(label: str, step: str, text: str) -> None:
        u[label] = (text, f"{results[step]['date']} ({commit})")

    if "mvn" in results.get("2", {}):
        row(R_MVN, "2", "Passed" if results["2"]["mvn"] == "PASS" else "Failed")
    if results.get("1", {}).get("status", "SKIP") != "SKIP":
        row(R_MG, "1", results["1"]["detail"])
    pairs = [(c, p) for s, c in (("2", "A"), ("3", "B")) for p in results.get(s, {}).get("pairs", [])]
    if pairs:
        bad = [f"{c}:{p['name']}" for c, p in pairs if not p["equal"]]
        names = ", ".join(f"{c}:{p['name']}" for c, p in pairs)
        date = max(results[s]["date"] for s in ("2", "3") if s in results and results[s].get("pairs"))
        u[R_EQ] = (
            f"Exit code 1 for {', '.join(bad)}" if bad else f"Exit code 0 for {len(pairs)} pairs ({names})",
            f"{date} ({commit})",
        )
    if "4" in results and results["4"].get("f1"):
        r = results["4"]
        row(
            R_LISSA,
            "4",
            f"Δ {r['delta']:+.4f} (mean F1 {r['mean']:.4f} of {len(r['f1'])} runs, published {r['published']}), mean 95 % CI of Δ [{r['ci'][0]:+.4f}, {r['ci'][1]:+.4f}]; model {r['model']}, provider {r['provider']}",
        )
    if "5" in results and results["5"].get("pilot"):
        p = results["5"]["pilot"]
        row(
            R_PILOT,
            "5",
            f"n {p['runs']} runs needed ({p['pilotRuns']} pilot runs, mean F1 {p['mean']:.4f}, sd {p['sd']:.4f}{', capped at 20' if p['capped'] else ''})",
        )
    if "6" in results and results["6"].get("verdict"):
        v = results["6"]["verdict"]
        row(
            R_CONT,
            "6",
            f"{'Flagged' if v['flagged'] else 'Not flagged'} (drop {v['drop']:.4f}, CI [{v['ci'][0]:.4f}, {v['ci'][1]:.4f}], baseline sd {v['baselineSd']:.4f}; paraphrase step manual, not done)",
        )
    return u


def write_report(run_dir: Path, run_id: str, state: dict, commit: str) -> None:
    lines = [f"# Run {run_id}", "", f"Commit: {commit}", ""]
    for n, r in sorted(state["results"].items()):
        lines.append(f"- Step {n} ({STEPS[int(n)]}): {r['status']} {r.get('detail', '')} ({r['date']})")
    (run_dir / "report.md").write_text("\n".join(lines) + "\n")


class Stop(Exception):
    pass


class Guide:
    def __init__(self, args: argparse.Namespace, run_id: str) -> None:
        self.args, self.run_id = args, run_id
        self.dir = LIVE / run_id
        self.state_file = self.dir / "state.json"
        self.state = {"inputs": {}, "results": {}}
        self.commit = git_head()
        if self.state_file.exists():
            self.state = json.loads(self.state_file.read_text())

    def save(self) -> None:
        if not self.args.dry_run:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(self.state, indent=2))

    def say(self, text: str = "") -> None:
        print(text, flush=True)

    def wait(self, text: str) -> None:
        self.say(f"  MANUAL: {text}")
        if not self.args.yes and not self.args.dry_run:
            input("  Press Enter when done. ")

    def confirm(self, question: str, default: bool = True) -> bool:
        if self.args.yes or self.args.dry_run:
            return default
        a = input(f"  {question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
        return default if not a else a.startswith("y")

    def ask(self, key: str, prompt: str, default: str = "") -> str:
        val = self.state["inputs"].get(key) or getattr(self.args, key, None)
        if val is None or val == "":
            if self.args.yes or self.args.dry_run:
                val = default
            else:
                val = input(f"  {prompt}{f' [{default}]' if default else ''}: ").strip() or default
        if val:
            self.state["inputs"][key] = str(val)
        return str(val)

    def sh(self, cmd: list[str], log: str, env: dict | None = None, out: Path | None = None) -> tuple[int, str]:
        """Run a command; stdout goes to `out` (if set) and the log; stderr to the log. Returns (code, stdout)."""
        self.say(f"  $ {' '.join(cmd)}")
        if self.args.dry_run:
            return 0, ""
        self.dir.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env={**os.environ, **(env or {})})
        if out:
            out.write_text(r.stdout)
        with (self.dir / f"{log}.log").open("a") as f:
            f.write(f"$ {' '.join(cmd)}\n[exit {r.returncode}]\n{r.stdout if not out else ''}{r.stderr}\n")
        if r.returncode:
            self.say("\n".join("    " + line for line in (r.stderr or r.stdout).strip().splitlines()[-8:]))
        return r.returncode, r.stdout

    def sandman(self, args: list[str], log: str, out: Path | None = None) -> tuple[int, str]:
        return self.sh(["uv", "run", "sandman", *args], log, out=out)

    def http(self, method: str, path: str, body: dict | None = None) -> int:
        url = os.environ["SANDMAN_URL"].rstrip("/") + path
        req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None)
        req.add_header("Authorization", f"Bearer {os.environ.get('SANDMAN_TOKEN', '')}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=15) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except OSError as e:
            self.say(f"    {e}")
            return 0

    def finish(self, n: int, status: str, detail: str = "", **data: object) -> str:
        self.say(f"  {status} step {n}: {detail}")
        self.state["results"][str(n)] = {"status": status, "detail": detail, "date": today(), **data}
        self.save()
        self.report()
        return status

    def report(self) -> None:
        if self.args.dry_run:
            return
        updates = build_updates(self.state["results"], self.commit)
        if updates:
            DOC.write_text(update_results(DOC.read_text(), updates))
        write_report(self.dir, self.run_id, self.state, self.commit)

    def wait_workflow(self, project: str, tag: str, timeout: int) -> bool:
        """Wait for the newest workflow of the project. Saves the workflow JSON and prints failed steps."""
        time.sleep(0 if self.args.dry_run else 2)
        code, listing = self.sandman(["workflows", "--project", project], tag)
        rows = sorted(line.split("\t") for line in listing.splitlines() if line.count("\t") >= 3)
        if self.args.dry_run:
            return True
        if not rows:
            self.say("    no workflow found: ingestion did not start one")
            return False
        wf_id = max(rows, key=lambda r: r[3])[0]
        wf_file = self.dir / f"{tag}.workflow.json"
        code, _ = self.sandman(
            ["workflows", "--project", project, "--wait", wf_id, "--timeout", str(timeout)], tag, out=wf_file
        )
        if code == 0:
            return True
        self.say(f"    workflow {wf_id} not SUCCEEDED (saved to {wf_file})")
        try:
            lines = failed_steps(json.loads(wf_file.read_text()))
        except ValueError:
            lines = ["workflow JSON missing (timeout or request error), see log"]
        for line in lines or ["no failed step listed; check status in the JSON"]:
            self.say(f"      {line}")
        return False

    def ingest_run(self, project: str, folder: Path, policy: str, tag: str, rules: list[str], timeout: int) -> bool:
        if (
            self.http("PUT", f"/v1/admin/projects/{project}", {"name": project, "llmPolicy": policy}) != 200
            and not self.args.dry_run
        ):
            self.say("    project creation failed (admin token?)")
            return False
        cmd = ["ingest", "--project", project, "--mode", "full", "--revision", "live"]
        for r in rules:
            cmd += ["--kind-rule", r]
        code, _ = self.sandman([*cmd, str(folder)], tag)
        if code:
            return False
        return self.wait_workflow(project, tag, timeout)

    def export_nonempty(self, project: str, fmt: str, approach: str, dest: Path, tag: str) -> bool:
        code, _ = self.sandman(
            ["export", "tracelinks", "--project", project, "--format", fmt, "--approach", approach], tag, out=dest
        )
        if self.args.dry_run:
            return True
        n = len(dest.read_text().splitlines()) - 1 if dest.exists() else -1
        if code or n < 1:
            self.say(
                f"    export gave {max(n, 0)} links: check failed workflow steps above and the kind rules (other=N in the ingest log {tag}.log)"
            )
            return False
        self.say(f"    {n} links exported to {dest.name}")
        return True

    def equal(self, expected: Path, actual: Path, tag: str) -> bool:
        code, out = self.sandman(
            ["eval", "equal", "--format", "ardoco-sad-sam", "--expected", str(expected), "--actual", str(actual)], tag
        )
        if code and out and not self.args.dry_run:
            self.say("    " + out.replace("\n", "\n    "))
        return code == 0

    def lissa_run(self, folder: Path, tag: str, gold: Path, published: float | None) -> float | None:
        project = f"lissa-{tag}-{int(time.time())}"[:63]
        policy = self.state["inputs"].get("llm_policy", "cloud-allowed")
        if not self.ingest_run(project, folder, policy, tag, [], 3600):
            return None
        csv_file, report = self.dir / f"{tag}.csv", self.dir / f"{tag}.json"
        if not self.export_nonempty(project, "lissa", "lissa", csv_file, tag):
            return None
        cmd = ["eval", "compare", "--format", "lissa", "--gold", str(gold), "--a", str(csv_file)]
        if published is not None:
            cmd += ["--published", str(published)]
        code, _ = self.sandman(cmd, tag, out=report)
        if self.args.dry_run:
            return 0.0
        if code:
            return None
        f1 = f1_of(report.read_text())
        self.say(f"    F1 {f1:.4f}")
        return f1

    def preflight_ok(self) -> bool:
        return self.state["results"].get("0", {}).get("status") == "PASS" or self.args.dry_run

    def lissa_inputs(self, with_gold: bool = True) -> tuple[Path, Path] | None:
        inp = self.ask("lissa_input", "INPUT_DIR (requirements + code + sandman.yaml)", "tests/e2e/fixtures/mini/s1")
        gold = self.ask("gold", "gold answer.csv path")
        if not (self.args.dry_run or (inp and gold and Path(inp).is_dir() and Path(gold).is_file())):
            self.say("    input directory or gold file missing")
            return None
        return Path(inp).resolve(), Path(gold).resolve()

    # steps

    def step0(self) -> str:
        self.say("  Needs a running stack (make up). Checks URL, token, proxy, Docker and admin rights.")
        os.environ.setdefault("SANDMAN_URL", "http://127.0.0.1:8080")
        url = os.environ["SANDMAN_URL"]
        os.environ["NO_PROXY"] = "127.0.0.1,localhost" + (
            f",{os.environ['NO_PROXY']}" if os.environ.get("NO_PROXY") else ""
        )
        if not os.environ.get("SANDMAN_TOKEN"):
            self.wait(f"Open {url}/console/login as admin. Open Tokens, create a personal API token, copy the secret.")
            if not self.args.yes and not self.args.dry_run:
                os.environ["SANDMAN_TOKEN"] = getpass.getpass("  Paste the token (no echo): ").strip()
        fails = []
        if self.args.dry_run:
            self.say(f"  would check: GET {url}/readyz, docker info, PUT admin project, CoreNLP mode")
        else:
            if self.http("GET", "/readyz") != 200:
                fails.append(f"{url}/readyz not reachable (start the stack with make up)")
            if not os.environ.get("SANDMAN_TOKEN"):
                fails.append("SANDMAN_TOKEN not set")
            elif (
                not fails
                and self.http(
                    "PUT",
                    f"/v1/admin/projects/t24-preflight-{int(time.time())}",
                    {"name": "t24-preflight", "llmPolicy": "local-only"},
                )
                != 200
            ):
                fails.append("admin check failed: PUT project is not allowed (use a token of the admin user)")
            if self.sh(["docker", "info"], "preflight")[0]:
                fails.append("Docker not reachable")
        cred = ROOT / "deploy/secrets/corenlp_credentials"
        env_file = ROOT / "deploy/.env"
        local = (
            "SANDMAN_CORENLP_MODE=local" in (env_file.read_text() if env_file.exists() else "")
            or os.environ.get("SANDMAN_CORENLP_MODE") == "local"
        )
        if not local and (not cred.exists() or "change-me" in cred.read_text()):
            self.say(
                "  HINT: CoreNLP credentials are a placeholder. Set SANDMAN_CORENLP_MODE=local (deploy/.env) or fill deploy/secrets/corenlp_credentials, then restart the stack."
            )
        return self.finish(0, "FAIL" if fails else "PASS", "; ".join(fails) or "stack, token, admin and Docker ok")

    def step1(self) -> str:
        if not self.confirm("The tests pull a Memgraph image and take minutes. Run them now?"):
            return self.finish(1, "SKIP", "not run")
        code, out = self.sh(["uv", "run", "pytest", "services/query-service", "-q", "-rs"], "memgraph")
        tail = out.strip().splitlines()[-1] if out.strip() else "no output"
        if self.args.dry_run:
            return self.finish(1, "PASS", "dry run")
        if code == 0 and "skipped" in tail:
            return self.finish(1, "FAIL", f"Failed: skips are not a pass: {tail} (start Docker, run again)")
        return self.finish(1, "PASS" if code == 0 else "FAIL", f"Passed ({tail})" if code == 0 else f"Failed: {tail}")

    def step2(self) -> str:
        self.say("  The Maven test starts its own CoreNLP container. Docker must run.")
        if not self.args.dry_run:
            OUT.mkdir(exist_ok=True)
        mvn = "-q -pl services/tlr-agent/approach-ardoco -am -Dtest=BenchmarkEquivalenceTest -Dsurefire.failIfNoSpecifiedTests=false test".split()
        code, _ = self.sh(["mvn", *mvn], "mvn", env={"SANDMAN_BENCHMARK_CSV_DIR": str(OUT)})
        if code:
            return self.finish(2, "FAIL", "BenchmarkEquivalenceTest failed, see mvn.log", mvn="FAIL", pairs=[])
        pairs = []
        for exp in sorted(OUT.glob("*.standalone.csv")):
            name = exp.name.removesuffix(".standalone.csv")
            act = OUT / f"{name}.adapter.csv"
            if act.exists():
                pairs.append({"name": name, "equal": self.equal(exp, act, "equal-a")})
        if not pairs and not self.args.dry_run:
            return self.finish(2, "FAIL", f"no CSV pairs in {OUT}", mvn="PASS", pairs=[])
        ok = all(p["equal"] for p in pairs)
        return self.finish(
            2, "PASS" if ok else "FAIL", f"{len(pairs)} pairs {'equal' if ok else 'differ'}", mvn="PASS", pairs=pairs
        )

    def step3(self) -> str:
        policy = "local-only"
        pairs = []
        for project, model, glob in PCM_CASES:
            self.say(f"  case {project} ({model})")
            tag = f"b-{project}"
            pid = f"{project}-{glob}-{int(time.time())}"
            rules = ["*.txt=architecture-doc", f"{glob}/**=architecture-model"]
            live = self.dir / f"{project}-live.csv"
            ok = (
                self.ingest_run(pid, FIXTURES / project, policy, tag, rules, 600)
                and self.export_nonempty(pid, "ardoco-sad-sam", "ardoco", live, tag)
                and self.equal(OUT / f"{project}-{model}.standalone.csv", live, tag)
            )
            pairs.append({"name": f"{project}-{model}", "equal": bool(ok)})
            self.say(f"  {'PASS' if ok else 'FAIL'} {project}")
        ok = all(p["equal"] for p in pairs)
        return self.finish(
            3,
            "PASS" if ok else "FAIL",
            ", ".join(f"{p['name']}={'equal' if p['equal'] else 'FAIL'}" for p in pairs),
            pairs=pairs,
        )

    def step4(self) -> str:
        self.say("  Needs a running LLM endpoint. Runs the LiSSA approach N times on a project directory.")
        ins = self.lissa_inputs()
        if not ins:
            return self.finish(4, "SKIP", "inputs missing")
        published = float(self.ask("published", "published F1 of LiSSA (0.xx)", "0.0"))
        n = int(self.ask("runs", "number of runs", "3"))
        self.ask("llm_policy", "llmPolicy of the project (cloud-allowed or local-only)", "cloud-allowed")
        model = self.ask("model", "model name used by the stack", "unknown")
        provider = self.ask("provider", "provider of the model", "unknown")
        self.wait("Confirm the LLM endpoint is configured (config/llm/models.yaml, gateway running).")
        f1s, cis = [], []
        for i in range(1, n + 1):
            f1 = self.lissa_run(ins[0], f"live-{i}", ins[1], published)
            if f1 is None:
                return self.finish(4, "FAIL", f"run {i} failed, see logs and workflow JSON", f1=f1s)
            f1s.append(f1)
            if not self.args.dry_run:
                cis.append(json.loads((self.dir / f"live-{i}.json").read_text())["published"]["ci"])
        if self.args.dry_run:
            return self.finish(4, "PASS", "dry run")
        mean = sum(f1s) / len(f1s)
        ci = [sum(c[0] for c in cis) / len(cis), sum(c[1] for c in cis) / len(cis)]
        zero = ci[0] <= 0 <= ci[1]
        return self.finish(
            4,
            "PASS",
            f"delta {mean - published:+.4f}, CI {'contains' if zero else 'excludes'} 0",
            f1=f1s,
            mean=mean,
            published=published,
            delta=mean - published,
            ci=ci,
            model=model,
            provider=provider,
        )

    def step5(self) -> str:
        prev = self.state["results"].get("4", {}).get("f1", [])
        f1s: list[float] = []
        if len(prev) >= 2 and self.confirm(f"Reuse the {len(prev)} F1 values of step 4 as pilot runs?"):
            f1s = list(prev)
        else:
            ins = self.lissa_inputs()
            if not ins:
                return self.finish(5, "SKIP", "inputs missing")
            n = int(self.ask("pilot_runs", "pilot runs", "5"))
            for i in range(1, n + 1):
                f1 = self.lissa_run(ins[0], f"pilot-{i}", ins[1], None)
                if f1 is None:
                    return self.finish(5, "FAIL", f"pilot run {i} failed")
                f1s.append(f1)
        cmd = ["eval", "pilot"] + [a for f in f1s for a in ("--f1", str(f))]
        code, out = self.sandman(cmd, "pilot", out=self.dir / "pilot.json")
        if self.args.dry_run:
            return self.finish(5, "PASS", "dry run")
        if code:
            return self.finish(5, "FAIL", "eval pilot failed")
        p = json.loads(out)
        return self.finish(5, "PASS", f"{p['runs']} runs needed" + (" (capped)" if p["capped"] else ""), pilot=p)

    def step6(self) -> str:
        self.say("  Perturbs the code files, runs LiSSA on original and perturbed input, applies the verdict rule.")
        ins = self.lissa_inputs()
        names = self.ask("names", "names file (one identifier per line)")
        if not ins or not (self.args.dry_run or Path(names).is_file()):
            return self.finish(6, "SKIP", "inputs missing")
        inp, gold = ins
        n = int(self.ask("contamination_runs", "runs per variant", "3"))
        self.ask("llm_policy", "llmPolicy of the project", "cloud-allowed")
        pert = self.dir / "perturbed-input"
        try:
            rels = code_files(inp) if not self.args.dry_run else []
        except ValueError as e:
            return self.finish(6, "FAIL", str(e))
        if not self.args.dry_run:
            shutil.rmtree(pert, ignore_errors=True)
            shutil.copytree(inp, pert)
        absf = [str(inp / r) for r in rels]
        code, _ = self.sandman(
            ["eval", "perturb", "--names", str(Path(names).resolve()), "--out", str(self.dir / "flat"), *absf],
            "perturb",
        )
        if code:
            return self.finish(6, "FAIL", "perturb failed")
        if not self.args.dry_run:
            copy_back(self.dir / "flat", pert, rels)
        orig, per = [], []
        for variant, folder, acc in (("orig", inp, orig), ("pert", pert, per)):
            for i in range(1, n + 1):
                f1 = self.lissa_run(folder, f"{variant}-{i}", gold, None)
                if f1 is None:
                    return self.finish(6, "FAIL", f"{variant} run {i} failed")
                acc.append(f1)
        cmd = ["eval", "contamination", "--original", *map(str, orig), "--perturbed", *map(str, per)]
        code, out = self.sandman(cmd, "contamination", out=self.dir / "contamination.json")
        if self.args.dry_run:
            return self.finish(6, "PASS", "dry run")
        if code:
            return self.finish(6, "FAIL", "eval contamination failed")
        v = json.loads(out)
        self.say(
            "  REMINDER: the paraphrase step is manual (experiments/PROTOCOL.md section 6). This run covers identifier renaming only."
        )
        return self.finish(
            6, "PASS", "flagged" if v["flagged"] else "not flagged", verdict=v, original=orig, perturbed=per
        )

    def run(self) -> int:
        order = self.args.only or list(STEPS)
        self.say(f"Run {self.run_id} · commit {self.commit} · results in {self.dir}")
        for n in order:
            prev = self.state["results"].get(str(n))
            self.say(f"\n== Step {n}: {STEPS[n]}" + (f" (last result: {prev['status']})" if prev else ""))
            if prev and self.args.yes and not self.args.only and not self.args.dry_run:
                self.say("  done already, skipped (use --only to redo)")
                continue
            if n > 0 and not self.preflight_ok() and not self.args.only:
                self.say("  Preflight did not pass. Redo step 0 first.")
                continue
            if not self.confirm("Run this step? (n = skip)", default=not (prev and prev["status"] == "PASS")):
                if not prev:
                    self.finish(n, "SKIP", "skipped by user")
                continue
            if n in (2, 3) and not shutil.which("mvn" if n == 2 else "uv"):
                self.finish(n, "FAIL", "mvn/uv not found")
                continue
            try:
                getattr(self, f"step{n}")()
            except (Stop, KeyboardInterrupt):
                self.say("\nStopped. Continue with --resume.")
                return 130
        self.say("\nSummary: " + ", ".join(f"{n}={r['status']}" for n, r in sorted(self.state["results"].items())))
        return 1 if any(r["status"] == "FAIL" for r in self.state["results"].values()) else 0


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Guided T2.4 checks. Updates docs/tasks/T2.4-reproduction-study.md.")
    p.add_argument("--resume", nargs="?", const="latest", metavar="RUN_ID")
    p.add_argument("--only", nargs="+", type=int, choices=list(STEPS), metavar="STEP")
    p.add_argument(
        "--yes", action="store_true", help="no prompts; inputs come from flags, missing inputs skip the step"
    )
    p.add_argument("--dry-run", action="store_true", help="print what would run, call nothing")
    for k in (
        "lissa_input",
        "gold",
        "published",
        "runs",
        "pilot_runs",
        "contamination_runs",
        "names",
        "llm_policy",
        "model",
        "provider",
    ):
        p.add_argument("--" + k.replace("_", "-"), dest=k)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    if args.resume:
        runs = sorted(p.parent.name for p in LIVE.glob("*/state.json"))
        run_id = runs[-1] if args.resume == "latest" and runs else args.resume
        if run_id == "latest":
            sys.exit("no earlier run to resume")
    else:
        run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return Guide(args, run_id).run()


if __name__ == "__main__":
    sys.exit(main())

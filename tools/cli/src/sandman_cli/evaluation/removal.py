"""Removal-protocol evaluation of the model-code detector (T7.3, experiments/PROTOCOL.md section 9).

Links are (model element id, decoded code path) pairs of the format `ardoco-sam-code`.
"""

import csv
import json
import random
import shlex
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from defusedxml import ElementTree as SafeET

from sandman_cli.evaluation import formats, stats

MODEL = "model-element-without-code"
CODE = "code-without-model-element"
TYPES = (MODEL, CODE)
EXCLUDE_GLOBS = ("**/package-info.java", "**/module-info.java")
PER_TYPE = 3
PCM = {
    "mediastore": "ms.repository",
    "teastore": "teastore.repository",
    "teammates": "teammates.repository",
    "bigbluebutton": "bbb.repository",
    "jabref": "jabref.repository",
}
Finding = tuple[str, str]
Link = tuple[str, str]


def locate(benchmark: Path, project: str) -> tuple[Path, Path, Path]:
    """Gold file, PCM repository and architecture text of a benchmark project."""
    root = benchmark / project
    (gold,) = (root / "goldstandards").glob("goldstandard_sam_*-code_*.csv")
    (pcm,) = root.glob(f"model_*/pcm/{PCM[project]}")
    (text,) = root.glob(f"text_*/{project}.txt")
    return gold, pcm, text


def units(acm: Path) -> dict[str, str]:
    """Item id to decoded path of the compilation units of an ACM."""
    repo = json.loads(acm.read_text(encoding="utf-8"))["codeItemRepository"]["repository"]
    out = {}
    for key, item in repo.items():
        if item["type"] == "CodeCompilationUnit":
            name = item["name"] + ("." + item["extension"] if item.get("extension") else "")
            encoded = "/".join([*item["pathElements"], name])
            cut = 0 if item["language"] == "JAVA" else encoded.rfind("/") + 1
            out[key] = unquote(encoded[:cut]) + encoded[cut:] if cut else unquote(encoded)
    return out


def drop_files(acm: Path, ids: Iterable[str]) -> str:
    """ACM text without the given compilation units, their content and their references."""
    data = json.loads(acm.read_text(encoding="utf-8"))
    repo = data["codeItemRepository"]["repository"]
    gone: set[str] = set()
    todo = list(ids)
    while todo:
        item = todo.pop()
        if item in repo and item not in gone:
            gone.add(item)
            todo.extend(repo[item].get("content") or [])
    for item in gone:
        del repo[item]
    for item in [data, *repo.values()]:
        if item.get("content"):
            item["content"] = [c for c in item["content"] if c not in gone]
    return json.dumps(data)


def read_gold(path: Path) -> dict[str, tuple[str, list[str]]]:
    """Element id to (name, entries). Entries that end with `/` are directories."""
    out: dict[str, tuple[str, list[str]]] = {}
    for row in csv.DictReader(path.open(encoding="utf-8")):
        entry = (row.get("ce_ids") or row.get("ce_id") or "").strip()
        name, entries = out.setdefault(row["ae_id"].strip(), (row["ae_name"].strip(), []))
        if entry:
            entries.append(entry)
    return out


def expand(entries: Iterable[str], paths: Iterable[str]) -> set[str]:
    paths = set(paths)
    out: set[str] = set()
    for entry in entries:
        if entry.endswith("/"):
            out.update(p for p in paths if p.startswith(entry))
        elif entry in paths:
            out.add(entry)
    return out


def components(pcm: Path) -> dict[str, str]:
    root = SafeET.parse(pcm).getroot()
    return {c.get("id", ""): c.get("entityName", "") for c in root.iter("components__Repository")}


def drop_component(pcm: Path, element_id: str) -> str:
    """PCM text without the component of the given id."""
    for _, (prefix, uri) in SafeET.iterparse(pcm, events=("start-ns",)):
        ET.register_namespace(prefix, uri)
    tree = SafeET.parse(pcm)
    root = tree.getroot()
    for child in list(root):
        if child.tag == "components__Repository" and child.get("id") == element_id:
            root.remove(child)
    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def excluded(path: str) -> bool:
    return any(PurePosixPath(path).full_match(g) for g in EXCLUDE_GLOBS)


def detect(comps: Iterable[str], files: Iterable[str], links: Iterable[Link]) -> set[Finding]:
    """The rules of ModelCodeDetector on the given elements and links."""
    comps, files = set(comps), set(files)
    live = {(c, f) for c, f in links if f in files}
    linked_files = {f for _, f in live}
    linked_comps = {c for c, _ in live}
    out = {(MODEL, c) for c in comps - linked_comps}
    out |= {(CODE, f) for f in files - linked_files if not excluded(f)}
    return out


class Setup:
    def __init__(self, gold: Mapping[str, tuple[str, list[str]]], comps: Mapping[str, str], paths: Iterable[str]):
        self.paths = set(paths)
        self.comps = dict(comps)
        self.gold_files = {i: expand(e, self.paths) for i, (_, e) in gold.items()}
        self.gold_comps = {
            i: self.gold_files[i] for i, (n, _) in gold.items() if n.startswith("Component:") and i in self.comps
        }

    def mapped_elsewhere(self, element: str) -> set[str]:
        return set().union(*(f for i, f in self.gold_comps.items() if i != element))


def runs_of(setup: Setup, baseline: set[Finding]) -> tuple[list[dict], list[dict]]:
    """Removal runs with expected findings. A run whose expected findings are all in the baseline is skipped."""
    runs, skipped = [], []
    for element in sorted(setup.gold_comps):
        files = setup.gold_comps[element]
        if not files:
            skipped.append({"element": element, "reason": "no files"})
            continue
        orphans = files - setup.mapped_elsewhere(element)
        candidates = [
            {"id": f"code:{element}", "kind": "code", "element": element, "expected": {(MODEL, element)}},
            {"id": f"model:{element}", "kind": "model", "element": element, "expected": {(CODE, f) for f in orphans}},
        ]
        for run in candidates:
            fresh = run["expected"] - baseline
            if fresh:
                runs.append({**run, "expected": fresh})
            else:
                skipped.append({"id": run["id"], "reason": "expected finding in baseline"})
    return runs, skipped


def state_after(setup: Setup, links: set[Link], run: Mapping) -> tuple[set[str], set[str], set[Link]]:
    element = run["element"]
    if run["kind"] == "code":
        files = setup.gold_comps[element]
        return set(setup.comps), setup.paths - files, {(c, f) for c, f in links if f not in files}
    return set(setup.comps) - {element}, set(setup.paths), {(c, f) for c, f in links if c != element}


def simulate(setup: Setup, links: set[Link], baseline: set[Finding], run: Mapping) -> set[Finding]:
    comps, files, kept = state_after(setup, links, run)
    return detect(comps, files, kept) - baseline


def counts(runs: Sequence[Mapping], reported: Mapping[str, set[Finding]], resamples: int, seed: int) -> dict:
    out = {}
    for kind in TYPES:
        gold = {(r["id"], f) for r in runs for f in r["expected"] if f[0] == kind}
        predicted = {(r["id"], f) for r in runs for f in reported[r["id"]] if f[0] == kind}
        m = stats.link_metrics(predicted, gold)
        tp = len(predicted & gold)
        ci = stats.f1_ci(predicted, gold, resamples, seed) if gold | predicted else [0.0, 0.0]
        out[kind] = {"tp": tp, "fp": len(predicted) - tp, "fn": len(gold) - tp, **m, "f1Ci": ci}
    return out


def baseline_agreement(setup: Setup, links: set[Link], baseline: set[Finding]) -> dict:
    gold_comp = {i for i, f in setup.gold_comps.items() if f}
    unpaired_comps = set(setup.comps) - gold_comp
    paired_files = set().union(*setup.gold_files.values()) if setup.gold_files else set()
    unpaired_files = {f for f in setup.paths if f not in paired_files and not excluded(f)}
    pred_c = {k for t, k in baseline if t == MODEL}
    pred_f = {k for t, k in baseline if t == CODE}
    return {
        MODEL: {"predicted": len(pred_c), "gold": len(unpaired_comps), **stats.link_metrics(pred_c, unpaired_comps)},
        CODE: {"predicted": len(pred_f), "gold": len(unpaired_files), **stats.link_metrics(pred_f, unpaired_files)},
    }


def sample(runs: Sequence[Mapping], per_type: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    chosen: list[dict] = []
    for kind in ("code", "model"):
        pool = sorted((r for r in runs if r["kind"] == kind), key=lambda r: r["id"])
        chosen += rng.sample(pool, min(per_type, len(pool)))
    return chosen


def read_links(path: Path, decoded: Mapping[str, str]) -> set[Link]:
    by_encoded = {unquote(p): p for p in decoded.values()}
    return {(a, by_encoded.get(unquote(b), unquote(b))) for a, b in formats.read_file("ardoco-sam-code", path)}


def run_command(cmd: str, jobs: Path) -> None:
    subprocess.run([*shlex.split(cmd), str(jobs)], check=True)


def full_links(
    cmd: str,
    text: Path,
    setup_runs: Sequence[Mapping],
    acm: Path,
    pcm: Path,
    decoded: Mapping[str, str],
    gold_files: Mapping[str, set[str]],
) -> dict[str, set[Link]]:
    """Runs the TLR command once for the given removal runs. Returns the links of each run."""
    by_path = {p: i for i, p in decoded.items()}
    with tempfile.TemporaryDirectory(prefix="removal-") as tmp:
        jobs = Path(tmp)
        for run in setup_runs:
            job = jobs / run["id"].replace(":", "_")
            job.mkdir()
            (job / "text.txt").write_text(text.read_text(encoding="utf-8"), encoding="utf-8")
            if run["kind"] == "code":
                dropped = [by_path[p] for p in gold_files[run["element"]]]
                (job / "code.acm").write_text(drop_files(acm, dropped), encoding="utf-8")
                (job / "model.repository").write_text(pcm.read_text(encoding="utf-8"), encoding="utf-8")
            else:
                (job / "code.acm").write_text(acm.read_text(encoding="utf-8"), encoding="utf-8")
                (job / "model.repository").write_text(drop_component(pcm, run["element"]), encoding="utf-8")
        run_command(cmd, jobs)
        return {r["id"]: read_links(jobs / r["id"].replace(":", "_") / "links.csv", decoded) for r in setup_runs}


def baseline_links(cmd: str, text: Path, acm: Path, pcm: Path, out: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="removal-") as tmp:
        job = Path(tmp) / "baseline"
        job.mkdir()
        (job / "text.txt").write_text(text.read_text(encoding="utf-8"), encoding="utf-8")
        (job / "code.acm").write_text(acm.read_text(encoding="utf-8"), encoding="utf-8")
        (job / "model.repository").write_text(pcm.read_text(encoding="utf-8"), encoding="utf-8")
        run_command(cmd, job.parent)
        out.write_text((job / "links.csv").read_text(encoding="utf-8"), encoding="utf-8")


def evaluate(
    gold_file: Path,
    pcm: Path,
    acm: Path,
    links_file: Path,
    *,
    mode: str,
    seed: int,
    resamples: int = stats.RESAMPLES,
    per_type: int = PER_TYPE,
    cmd: str | None = None,
    text: Path | None = None,
) -> dict:
    decoded = units(acm)
    links = read_links(links_file, decoded)
    setup = Setup(read_gold(gold_file), components(pcm), decoded.values())
    baseline = detect(setup.comps, setup.paths, links)
    runs, skipped = runs_of(setup, baseline)
    simulated = {r["id"]: simulate(setup, links, baseline, r) for r in runs}
    result = {
        "mode": mode,
        "seed": seed,
        "files": len(setup.paths),
        "components": len(setup.comps),
        "goldComponents": len(setup.gold_comps),
        "links": len(links),
        "baseline": {t: len([1 for x, _ in baseline if x == t]) for t in TYPES},
        "baselineAgreement": baseline_agreement(setup, links, baseline),
        "runs": len(runs),
        "skipped": skipped,
        "simulated": counts(runs, simulated, resamples, seed),
    }
    if mode == "full":
        assert cmd and text
        chosen = sample(runs, per_type, seed)
        tlr = full_links(cmd, text, chosen, acm, pcm, decoded, setup.gold_comps)
        full = {}
        for r in chosen:
            comps, files, _ = state_after(setup, links, r)
            full[r["id"]] = detect(comps, files, tlr[r["id"]]) - baseline
        result["sample"] = [r["id"] for r in chosen]
        result["full"] = counts(chosen, full, resamples, seed)
        result["agreement"] = agreement(chosen, simulated, full)
    return result


def agreement(
    chosen: Sequence[Mapping], simulated: Mapping[str, set[Finding]], full: Mapping[str, set[Finding]]
) -> dict:
    same = total = 0
    jaccard = []
    for r in chosen:
        for f in r["expected"]:
            total += 1
            same += (f in simulated[r["id"]]) == (f in full[r["id"]])
        union = simulated[r["id"]] | full[r["id"]]
        jaccard.append(len(simulated[r["id"]] & full[r["id"]]) / len(union) if union else 1.0)
    return {
        "expectedFindings": total,
        "expectedAgreement": same / total if total else 1.0,
        "findingSetJaccard": sum(jaccard) / len(jaccard) if jaccard else 1.0,
    }

#!/usr/bin/env python3
"""T2.7 metrics: SAD-code precision, recall and F1 of ardocode and transarc (gold SAM, derived SAM).

uv run python scripts/metrics_t27.py listings CACHE_JSON FIXTURES OUT_DIR [LOG]
uv run python scripts/metrics_t27.py table LINKS_DIR GOLD_DIR ACM_DIR PROJECT...

LINKS_DIR holds <project>.<variant>.csv of scripts/metrics_t27/MetricsDriver.java. GOLD_DIR holds
<project>/goldstandards/goldstandard_sad_*-code_*.csv of the ArDoCo benchmark.
"""

import json
import sys
from pathlib import Path
from urllib.parse import unquote

from sandman_cli.evaluation import formats, stats
from sandman_extraction import derive

FORMAT = "ardoco-sad-code"
VARIANTS = ("ardocode", "transarc-gold", "transarc-derived")
THRESHOLD = 0.05


def units(acm: Path) -> dict[str, str]:
    """Encoded path to decoded path of the compilation units of an ACM, as the ArDoCo benchmark reads them."""
    repo = json.loads(acm.read_text(encoding="utf-8"))["codeItemRepository"]["repository"]
    out = {}
    for item in repo.values():
        if item["type"] == "CodeCompilationUnit":
            name = item["name"] + ("." + item["extension"] if item.get("extension") else "")
            encoded = "/".join([*item["pathElements"], name])
            cut = 0 if item["language"] == "JAVA" else encoded.rfind("/") + 1
            out[encoded] = unquote(encoded[:cut]) + encoded[cut:] if cut else unquote(encoded)
    return out


def enroll(gold: set[formats.Link], paths: set[str]) -> set[formats.Link]:
    """A gold entry that ends with `/` stands for every compilation unit below it."""
    out = set()
    for sentence, entry in gold:
        if entry.endswith("/"):
            out.update((sentence, p) for p in paths if p.startswith(entry))
        else:
            out.add((sentence, entry))
    return out


def answers(cache: dict[str, str], texts: dict[str, list[str]]) -> dict[str, str]:
    """Project to the answer of the second turn of the documentation-only prompt (DOCUMENTATION_ONLY_V1)."""
    second = f'text = "{derive.PROMPTS[1]}" }}'
    out: dict[str, str] = {}
    for key, answer in cache.items():
        if key.count("UserMessage {") != 2 or second not in key or "Your task is to identify" not in key:
            continue
        for project, lines in texts.items():
            if lines[0] in key and lines[-1] in key:
                out[project] = answer
    return out


def logged_names(log: str, project: str, llm: str = "GPT_4_O") -> list[str]:
    """Component names that the replication log prints for a project and model."""
    head = f"Evaluating project {project.upper()} with LLM '{llm}'"
    tail = log.split(head, 1)[1].split("Component names:\n", 1)[1]
    names = []
    for line in tail.split("\n"):
        if line.startswith(("INFO", "WARN")):
            break
        if line.strip():
            names.append(line.strip())
    return names


def gold_file(gold_dir: Path, project: str) -> Path:
    (path,) = (gold_dir / project / "goldstandards").glob("goldstandard_sad_*-code_*.csv")
    return path


def predicted(links_dir: Path, project: str, variant: str, mapping: dict[str, str]) -> set[formats.Link]:
    return {(s, mapping[p]) for s, p in formats.read_file(FORMAT, links_dir / f"{project}.{variant}.csv")}


def evaluate(links_dir: Path, gold_dir: Path, acm_dir: Path, projects: list[str]) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for project in projects:
        mapping = units(acm_dir / f"{project}-code-model.acm")
        gold = enroll(formats.read_file(FORMAT, gold_file(gold_dir, project)), set(mapping.values()))
        out[project] = {v: stats.link_metrics(predicted(links_dir, project, v, mapping), gold) for v in VARIANTS}
    return out


def macro(results: dict[str, dict[str, dict]]) -> dict[str, dict[str, float]]:
    return {
        v: {m: sum(r[v][m] for r in results.values()) / len(results) for m in ("precision", "recall", "f1")}
        for v in VARIANTS
    }


def verdict(avg: dict[str, dict[str, float]]) -> tuple[float, bool]:
    gap = avg["ardocode"]["f1"] - avg["transarc-derived"]["f1"]
    return gap, gap > THRESHOLD


def table(results: dict[str, dict[str, dict]]) -> str:
    rows = [
        "| Project | " + " | ".join(f"{v} P | {v} R | {v} F1" for v in VARIANTS) + " |",
        "|---|" + "---|" * (3 * len(VARIANTS)),
    ]
    for project, r in results.items():
        rows.append(
            f"| {project} | "
            + " | ".join(f"{r[v][m]:.3f}" for v in VARIANTS for m in ("precision", "recall", "f1"))
            + " |"
        )
    avg = macro(results)
    rows.append(
        "| macro | " + " | ".join(f"{avg[v][m]:.3f}" for v in VARIANTS for m in ("precision", "recall", "f1")) + " |"
    )
    return "\n".join(rows)


def write_listings(cache_json: Path, fixtures: Path, out_dir: Path, log: Path | None) -> None:
    texts = {
        p.name: (p / f"{p.name}.txt").read_text(encoding="utf-8").strip().split("\n")
        for p in fixtures.iterdir()
        if p.is_dir()
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    for project, answer in sorted(answers(json.loads(cache_json.read_text(encoding="utf-8")), texts).items()):
        names, dropped = derive.postprocess(answer)
        if log is not None:
            expected = sorted(n.lower() for n in logged_names(log.read_text(encoding="utf-8"), project))
            assert sorted(n.lower() for n in names) == expected, f"{project}: {names} differs from the log {expected}"
        (out_dir / f"{project}.components").write_bytes(derive.listing(names))
        print(project, len(names), "names", "dropped" if dropped else "", dropped or "")


def main(argv: list[str]) -> None:
    if argv[0] == "listings":
        write_listings(Path(argv[1]), Path(argv[2]), Path(argv[3]), Path(argv[4]) if len(argv) > 4 else None)
    else:
        results = evaluate(Path(argv[1]), Path(argv[2]), Path(argv[3]), argv[4:])
        print(table(results))
        gap, flag = verdict(macro(results))
        print(
            f"\nmacro F1 gap ardocode - transarc-derived: {gap:+.3f}; flag (> {THRESHOLD}): {'yes' if flag else 'no'}"
        )


if __name__ == "__main__":
    main(sys.argv[1:])

"""NL query evaluation set T4.2 (docs/tasks/T4.2-nl-query-evaluation-set.md): dataset, checks and metrics.

Dataset file (YAML): `items` = list of {id, project, question, strategy, author, split, cypher, gold_keys}.
`strategy` is the gold label (cypher | graphrag | explore). `cypher` is required for `cypher` items (gold query);
`gold_keys` (Element keys) are required for `graphrag` and `explore` items.
"""

import argparse
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

STRATEGIES = ("cypher", "graphrag", "explore")
SPLITS = ("dev", "test")
AI = "ai-draft"
TARGET_TOTAL, TARGET_CONTENT = 150, 50
_LITERAL = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|\b\d+(?:\.\d+)?\b")
_TOKEN = re.compile(r"[a-z0-9]+")


def load(text: str) -> list[dict[str, Any]]:
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise ValueError("dataset must be a mapping with an 'items' list")
    return list(raw["items"])


def validate(items: Sequence[Mapping[str, Any]]) -> list[str]:
    """Format errors, one string per problem (empty = valid)."""
    errors: list[str] = []
    seen: set[str] = set()
    for i, it in enumerate(items):
        where = f"item {i} ({it.get('id', '?')})"
        for field in ("id", "project", "question", "strategy", "author", "split"):
            if not isinstance(it.get(field), str) or not it[field].strip():
                errors.append(f"{where}: missing {field}")
        if it.get("id") in seen:
            errors.append(f"{where}: duplicate id")
        seen.add(str(it.get("id")))
        if it.get("strategy") not in STRATEGIES:
            errors.append(f"{where}: strategy must be one of {', '.join(STRATEGIES)}")
        if it.get("split") not in SPLITS:
            errors.append(f"{where}: split must be dev or test")
        author = str(it.get("author", ""))
        if author != AI and not re.fullmatch(r"human:\S+", author):
            errors.append(f"{where}: author must be 'ai-draft' or 'human:<id>'")
        if it.get("split") == "test" and author == AI:
            errors.append(f"{where}: test items must be human-written (PROTOCOL section 7)")
        cypher = it.get("cypher")
        if it.get("strategy") == "cypher":
            if not isinstance(cypher, str) or not cypher.strip():
                errors.append(f"{where}: cypher items need a gold cypher")
        elif not it.get("gold_keys") or not all(isinstance(k, str) for k in it["gold_keys"]):
            errors.append(f"{where}: {it.get('strategy')} items need gold_keys")
        if isinstance(cypher, str):
            if "$projectId" not in cypher:
                errors.append(f"{where}: cypher must be anchored with $projectId")
            if re.search(r"\blimit\b", cypher, re.IGNORECASE):
                errors.append(f"{where}: no LIMIT in gold cypher (execution accuracy without truncation)")
    return errors


def template(cypher: str) -> str:
    """Query shape: literals replaced by '?', whitespace and case normalized."""
    return re.sub(r"\s+", " ", _LITERAL.sub("?", cypher)).strip().lower()


def _tokens(question: str) -> set[str]:
    return set(_TOKEN.findall(question.lower()))


def leakage(items: Sequence[Mapping[str, Any]], by_project: bool = True, near: float = 0.8) -> list[str]:
    """Problems that let dev information reach the test split: shared template, shared project, near-duplicates."""
    dev = [i for i in items if i["split"] == "dev"]
    test = [i for i in items if i["split"] == "test"]
    problems: list[str] = []
    dev_templates = {template(i["cypher"]): i["id"] for i in dev if i.get("cypher")}
    for t in test:
        if t.get("cypher") and (d := dev_templates.get(template(t["cypher"]))):
            problems.append(f"{t['id']}: same query template as dev item {d}")
    if by_project:
        shared = {i["project"] for i in dev} & {i["project"] for i in test}
        problems += [f"project {p} occurs in dev and test" for p in sorted(shared)]
    for t in test:
        tt = _tokens(t["question"])
        for d in dev:
            dt = _tokens(d["question"])
            if tt and dt and len(tt & dt) / len(tt | dt) >= near:
                problems.append(f"{t['id']}: question near-duplicate of dev item {d['id']}")
                break
    return problems


def counts(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts against the T4.2 targets; `met` is true only when every target holds."""
    by = Counter((i["split"], i["strategy"]) for i in items)
    content = sum(1 for i in items if i["strategy"] != "cypher")
    human_test = sum(1 for i in items if i["split"] == "test" and i["author"] != AI)
    out = {
        "total": len(items),
        "cypher": sum(1 for i in items if i["strategy"] == "cypher"),
        "content_and_multihop": content,
        "by_split_strategy": {f"{s}/{k}": n for (s, k), n in sorted(by.items())},
        "by_project": dict(sorted(Counter(i["project"] for i in items).items())),
        "by_author": dict(sorted(Counter(i["author"] for i in items).items())),
        "templates": len({template(i["cypher"]) for i in items if i.get("cypher")}),
        "human_test_items": human_test,
    }
    out["met"] = len(items) >= TARGET_TOTAL and content >= TARGET_CONTENT and human_test > 0
    return out


def norm(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def same_rows(gold: Mapping[str, Any], pred: Mapping[str, Any]) -> bool:
    """Multiset equality of rows without truncation; order of rows, column names and column order are ignored."""

    def bag(r: Mapping[str, Any]) -> Counter[tuple[str, ...]]:
        return Counter(tuple(sorted(norm(v) for v in row)) for row in r.get("rows", []))

    return not pred.get("error") and not pred.get("truncated") and bag(gold) == bag(pred)


def exec_accuracy(
    items: Iterable[Mapping[str, Any]], gold: Mapping[str, Any], pred: Mapping[str, Any], split: str | None = None
) -> dict[str, Any]:
    """Execution accuracy and validity rate over `cypher` items (a missing prediction counts as wrong)."""
    rows = [i for i in items if i["strategy"] == "cypher" and (split is None or i["split"] == split)]
    ok = valid = 0
    per: dict[str, list[int]] = {}
    wrong = []
    for i in rows:
        p = pred.get(i["id"], {"error": "no prediction"})
        good = i["id"] in gold and same_rows(gold[i["id"]], p)
        ok += good
        valid += not p.get("error")
        s = per.setdefault(i["project"], [0, 0])
        s[0] += good
        s[1] += 1
        if not good:
            wrong.append(i["id"])
    n = len(rows)
    return {
        "items": n,
        "correct": ok,
        "execution_accuracy": ok / n if n else None,
        "validity_rate": valid / n if n else None,
        "by_project": {p: {"correct": a, "items": b} for p, (a, b) in sorted(per.items())},
        "wrong": wrong,
    }


def retrieval(items: Iterable[Mapping[str, Any]], pred: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    """Gold-element recall and precision per `graphrag`/`explore` item, plus macro averages per strategy."""
    out: dict[str, Any] = {}
    for strategy in ("graphrag", "explore"):
        scores = []
        for i in (i for i in items if i["strategy"] == strategy):
            gold, got = set(i["gold_keys"]), set(pred.get(i["id"], []))
            hit = len(gold & got)
            scores.append({"id": i["id"], "recall": hit / len(gold), "precision": hit / len(got) if got else 0.0})
        n = len(scores)
        out[strategy] = {
            "items": n,
            "macro_recall": sum(s["recall"] for s in scores) / n if n else None,
            "macro_precision": sum(s["precision"] for s in scores) / n if n else None,
            "per_item": scores,
        }
    return out


def confusion(items: Iterable[Mapping[str, Any]], pred: Mapping[str, str]) -> dict[str, Any]:
    """Confusion matrix gold x predicted strategy for `auto` (an unpredicted item counts as 'none')."""
    labels = [*STRATEGIES, "none"]
    matrix = {g: dict.fromkeys(labels, 0) for g in STRATEGIES}
    n = 0
    for i in items:
        p = pred.get(i["id"])
        matrix[i["strategy"]][p if p in STRATEGIES else "none"] += 1
        n += 1
    per = {}
    for s in STRATEGIES:
        tp = matrix[s][s]
        predicted = sum(matrix[g][s] for g in STRATEGIES)
        gold = sum(matrix[s].values())
        per[s] = {"precision": tp / predicted if predicted else None, "recall": tp / gold if gold else None}
    correct = sum(matrix[s][s] for s in STRATEGIES)
    return {"matrix": matrix, "accuracy": correct / n if n else None, "per_strategy": per}


def intent_rules(text: str) -> list[tuple[str, re.Pattern[str], str]]:
    """Intents of config/query/intents.yaml (same matching rule as sandman_query.query.Intents)."""
    return [
        (i["id"], re.compile(i["pattern"], re.IGNORECASE), i["cypher"].strip()) for i in yaml.safe_load(text)["intents"]
    ]


def baseline(
    items: Iterable[Mapping[str, Any]], rules: Sequence[tuple[str, re.Pattern[str], str]]
) -> dict[str, dict[str, Any]]:
    """Intent-template baseline: per question the matched intent with its Cypher and parameters (none = unanswered).

    Run the Cypher (parameters bound, $projectId = the item's project) to get result sets for `exec`.
    """
    out: dict[str, dict[str, Any]] = {}
    for i in items:
        q = re.sub(r"\s+", " ", i["question"]).strip().rstrip("?!. ")
        for id_, pattern, cypher in rules:
            if m := pattern.fullmatch(q):
                params = {k: v for k, v in m.groupdict().items() if v is not None}
                out[i["id"]] = {"intent": id_, "cypher": cypher, "params": params | {"projectId": i["project"]}}
                break
        else:
            out[i["id"]] = {"intent": None, "error": "no intent matches"}
    return out


# ---------------------------------------------------------------- CLI


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _items(args: argparse.Namespace) -> list[dict[str, Any]]:
    items = load(args.dataset.read_text(encoding="utf-8"))
    if errors := validate(items):
        raise SystemExit("sandman: invalid dataset:\n  " + "\n  ".join(errors))
    return items


def _print(obj: Any) -> int:
    print(json.dumps(obj, indent=2, ensure_ascii=False))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    items = load(args.dataset.read_text(encoding="utf-8"))
    errors = validate(items)
    problems = [] if errors else leakage(items, not args.allow_shared_projects)
    report = {"format_errors": errors, "leakage": problems, "counts": None if errors else counts(items)}
    _print(report)
    return 1 if errors or problems else 0


def cmd_baseline(args: argparse.Namespace) -> int:
    items = _items(args)
    pred = baseline(items, intent_rules(args.intents.read_text(encoding="utf-8")))
    args.out.write_text(json.dumps(pred, indent=2, ensure_ascii=False), encoding="utf-8")
    cyph = [i for i in items if i["strategy"] == "cypher"]
    matched = sum(1 for i in cyph if pred[i["id"]]["intent"])
    return _print(
        {"cypher_items": len(cyph), "intent_matched": matched, "coverage": matched / len(cyph) if cyph else None}
    )


def cmd_exec(args: argparse.Namespace) -> int:
    return _print(exec_accuracy(_items(args), _read(args.gold), _read(args.predictions), args.split))


def cmd_retrieval(args: argparse.Namespace) -> int:
    return _print(retrieval(_items(args), _read(args.predictions)))


def cmd_classifier(args: argparse.Namespace) -> int:
    return _print(confusion(_items(args), _read(args.predictions)))


def register(ev: Any) -> None:
    """Add `nlquery` with its subcommands to the `eval` subparsers."""
    nl = ev.add_parser("nlquery", help="NL query evaluation set: checks and metrics (T4.2)").add_subparsers(
        dest="nlquery", required=True
    )

    def sub(name: str, help_: str, func: Any, *extra: tuple[str, str]) -> Any:
        p = nl.add_parser(name, help=help_)
        p.add_argument("--dataset", type=Path, required=True)
        for flag, h in extra:
            p.add_argument(flag, type=Path, required=True, help=h)
        p.set_defaults(func=func, offline=True)
        return p

    p = sub("validate", "format, counts and split/leakage checks (exit 1 on a problem)", cmd_validate)
    p.add_argument("--allow-shared-projects", action="store_true", help="skip the project split check")
    p = sub(
        "baseline",
        "intent-template baseline: matched intent and Cypher per question",
        cmd_baseline,
        ("--intents", "config/query/intents.yaml"),
        ("--out", "predictions JSON for the baseline"),
    )
    p = sub(
        "exec",
        "execution accuracy: row multiset equality against gold results",
        cmd_exec,
        ("--gold", "JSON {id: {columns, rows}}"),
        ("--predictions", "JSON {id: {columns, rows} | {error}}"),
    )
    p.add_argument("--split", choices=SPLITS, help="only this split")
    sub(
        "retrieval",
        "gold-element recall and precision for graphrag/explore",
        cmd_retrieval,
        ("--predictions", "JSON {id: [element keys]}"),
    )
    sub(
        "classifier",
        "confusion matrix of the `auto` classifier",
        cmd_classifier,
        ("--predictions", "JSON {id: strategy}"),
    )

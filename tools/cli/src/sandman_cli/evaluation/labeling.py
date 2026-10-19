"""Labeling study T3.6 (docs/tasks/T3.6-labeling-study.md): stratified blinded sample of findings and its scoring.

Input is the output of `sandman export inconsistencies --format json` (a list of InconsistencyDetail objects).
A stratum is detector x finding type x project. Findings with the same rater-visible content form one item.
"""

import csv
import io
import json
import random
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sandman_cli.evaluation import stats

DETECTORS = ("ardoco-id", "artemis-id")
LABELS = ("true", "false", "unclear")
VIEW = ("project", "type", "artifact", "sentence_no", "sentence", "element", "element_kind", "terms")
SHEET = ("item", *VIEW, "label", "note")
RATERS = ("a", "b")


def stratum(f: Mapping[str, Any]) -> str:
    return f"{f['detector']}|{f['type']}|{f['projectId']}"


def view(f: Mapping[str, Any]) -> tuple[str, ...]:
    """Rater-visible fields (VIEW order): no detector, version, confidence, severity, title or status."""
    ev = f.get("evidence") or {}
    text = [x["element"] for x in f["involved"] if "element" in x and "excerpt" in x["element"]]
    other = [x["element"] for x in f["involved"] if "element" in x and "excerpt" not in x["element"]]
    first = (text or other or [{}])[0]
    return (
        f["projectId"],
        f["type"],
        first.get("artifactPath", ""),
        str(ev.get("sentence", "")),
        " ".join(e["excerpt"] for e in text),
        ev.get("elementName") or "; ".join(e["name"] for e in other),
        ev.get("elementType") or "; ".join(e["label"] for e in other),
        "; ".join(w["word"] for w in ev.get("words", [])),
    )


def sample(
    findings: Iterable[Mapping[str, Any]], per_stratum: int, seed: int, detectors: Sequence[str] = DETECTORS
) -> tuple[dict[str, list[dict[str, str]]], dict[str, Any]]:
    """Rater sheets (rows per rater, each in its own order) and the key. RESOLVED findings are not sampled."""
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for f in findings:
        if f["detector"] in detectors and f["status"] != "RESOLVED":
            groups.setdefault(stratum(f), []).append(f)
    by_view: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    strata = {}
    for s in sorted(groups):
        pop = sorted(groups[s], key=lambda f: f["id"])
        picked = random.Random(f"{seed}:{s}").sample(pop, min(per_stratum, len(pop)))
        strata[s] = {"population": len(pop), "sampled": len(picked)}
        for f in picked:
            entry = {"finding": f["id"], "stratum": s, "confidence": f.get("confidence")}
            by_view.setdefault(view(f), []).append(entry)
    views = sorted(by_view)
    random.Random(seed).shuffle(views)
    ids = {v: f"i{n:04d}" for n, v in enumerate(views, 1)}
    sheets = {}
    for r in RATERS:
        order = views[:]
        random.Random(f"{seed}:rater-{r}").shuffle(order)
        sheets[r] = [{"item": ids[v], **dict(zip(VIEW, v, strict=True)), "label": "", "note": ""} for v in order]
    key = {
        "study": "T3.6",
        "seed": seed,
        "perStratum": per_stratum,
        "detectors": list(detectors),
        "strata": strata,
        "items": {ids[v]: by_view[v] for v in sorted(views, key=ids.__getitem__)},
    }
    return sheets, key


def write_sheet(rows: Sequence[Mapping[str, str]], columns: Sequence[str] = SHEET) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, columns, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def read_sheet(text: str, items: Iterable[str], partial: bool = False) -> dict[str, dict[str, str]]:
    """Rows by item id. Labels are checked; all key items must be labeled unless `partial` (adjudication files)."""
    rows = {r["item"]: r for r in csv.DictReader(io.StringIO(text))}
    expected = set(items)
    errors = sorted(set(rows) - expected)
    if errors:
        raise ValueError(f"unknown items: {', '.join(errors)}")
    if not partial and set(rows) != expected:
        raise ValueError(f"missing items: {', '.join(sorted(expected - set(rows)))}")
    for i, r in rows.items():
        r["label"] = (r.get("label") or "").strip().lower()
        if r["label"] not in LABELS and not (partial and not r["label"]):
            raise ValueError(f"item {i}: label {r['label']!r} not in {', '.join(LABELS)}")
    return rows


def _agreement(pairs: Sequence[tuple[str, str]], resamples: int, seed: int) -> dict[str, Any]:
    return {
        "n": len(pairs),
        "observed": sum(1 for a, b in pairs if a == b) / len(pairs),
        "kappa": stats.cohens_kappa(pairs),
        "ci": stats.kappa_ci(pairs, resamples, seed),
    }


def score(
    key: Mapping[str, Any],
    a: Mapping[str, Mapping[str, str]],
    b: Mapping[str, Mapping[str, str]],
    adjudicated: Mapping[str, Mapping[str, str]] | None = None,
    resamples: int = stats.RESAMPLES,
    seed: int = stats.SEED,
) -> dict[str, Any]:
    """Kappa (all items and per finding type), disagreements, precision with Wilson CI per stratum, pooled."""
    items = key["items"]
    pairs = {i: (a[i]["label"], b[i]["label"]) for i in items}
    by_type: dict[str, list[tuple[str, str]]] = {}
    for i, p in pairs.items():
        by_type.setdefault(a[i]["type"], []).append(p)
    adjudicated = adjudicated or {}
    final = {i: la if la == lb else (adjudicated.get(i, {}).get("label") or None) for i, (la, lb) in pairs.items()}
    counts = {s: {"true": 0, "false": 0, "unclear": 0, "unresolved": 0} for s in key["strata"]}
    for i, entries in items.items():
        for e in entries:
            counts[e["stratum"]][final[i] or "unresolved"] += 1
    strata = {}
    for s, c in counts.items():
        n = c["true"] + c["false"]
        strata[s] = {
            **key["strata"][s],
            **c,
            "precision": c["true"] / n if n else None,
            "ci": stats.wilson(c["true"], n),
        }
    pooled: dict[str, dict[str, Any]] = {}
    for name, part in (("byDetectorType", 2), ("byDetector", 1)):
        groups: dict[str, list[tuple[int, int, int]]] = {}
        for s, v in strata.items():
            groups.setdefault("|".join(s.split("|")[:part]), []).append(
                (v["population"], v["true"] + v["false"], v["true"])
            )
        pooled[name] = {g: stats.stratified_proportion(x) for g, x in sorted(groups.items())}
    return {
        "items": len(items),
        "agreement": {
            "all": _agreement(list(pairs.values()), resamples, seed),
            "byType": {t: _agreement(p, resamples, seed) for t, p in sorted(by_type.items())},
        },
        "disagreements": [
            {"item": i, "a": la, "b": lb, "final": final[i]} for i, (la, lb) in sorted(pairs.items()) if la != lb
        ],
        "unresolved": sum(1 for v in final.values() if v is None),
        "strata": strata,
        "pooled": pooled,
    }


def disagreement_sheet(a: Mapping[str, Mapping[str, str]], b: Mapping[str, Mapping[str, str]]) -> str:
    """Adjudication sheet: the item view, both labels and notes, and empty `label`/`note` for the agreed decision."""
    rows = [
        {
            **{k: a[i][k] for k in ("item", *VIEW)},
            "label_a": a[i]["label"],
            "note_a": a[i].get("note", ""),
            "label_b": b[i]["label"],
            "note_b": b[i].get("note", ""),
            "label": "",
            "note": "",
        }  # fmt: skip
        for i in sorted(a)
        if a[i]["label"] != b[i]["label"]
    ]
    return write_sheet(rows, ("item", *VIEW, "label_a", "note_a", "label_b", "note_b", "label", "note"))


def load_exports(texts: Iterable[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in texts:
        out += json.loads(t)
    return out

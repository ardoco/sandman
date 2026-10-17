"""Per-project comparison report of experiments/PROTOCOL.md section 3 and 4."""

from collections.abc import Mapping
from typing import Any

from sandman_cli.evaluation import stats
from sandman_cli.evaluation.formats import Link


def compare(
    gold: set[Link],
    a: set[Link],
    others: Mapping[str, set[Link]] | None = None,
    published_f1: float | None = None,
    resamples: int = stats.RESAMPLES,
    seed: int = stats.SEED,
) -> dict[str, Any]:
    """Metrics of A with CIs; for each other system B: bootstrap differences (B - A), McNemar, effect sizes, with
    Holm-adjusted p-values over all comparisons of the call; optionally the delta of F1(A) to a published value."""
    lo, hi = stats.f1_ci(a, gold, resamples, seed)
    report: dict[str, Any] = {
        "links": {"gold": len(gold), "a": len(a)},
        "a": {**stats.link_metrics(a, gold), "f1Ci": [lo, hi]},
        "comparisons": {},
    }
    if published_f1 is not None:
        f1 = report["a"]["f1"]
        report["published"] = {
            "f1": published_f1,
            "delta": f1 - published_f1,
            "ci": [lo - published_f1, hi - published_f1],
        }
    for name, b in (others or {}).items():
        data = stats.items(gold, a, b)
        bc = stats.mcnemar_counts(data)
        report["comparisons"][name] = {
            "links": len(b),
            "b": stats.link_metrics(b, gold),
            "bootstrap": stats.paired_bootstrap(data, resamples, seed),
            "mcnemar": {
                "onlyA": bc[0],
                "onlyB": bc[1],
                "p": stats.mcnemar_exact(*bc),
                "cohensG": stats.cohens_g(*bc),
                "oddsRatioBvsA": stats.odds_ratio(bc[1], bc[0]),
            },
        }
    names = list(report["comparisons"])
    ps = [report["comparisons"][n][k]["p"] if k == "mcnemar" else report["comparisons"][n]["bootstrap"]["f1"]["p"]
          for n in names for k in ("mcnemar", "bootstrap")]  # fmt: skip
    adjusted = iter(stats.holm(ps))
    for n in names:
        report["comparisons"][n]["mcnemar"]["pHolm"] = next(adjusted)
        report["comparisons"][n]["bootstrap"]["f1"]["pHolm"] = next(adjusted)
    return report


def identical(expected: set[Link], actual: set[Link]) -> dict[str, Any]:
    """Exact-equality check of the ArDoCo reproduction (no tolerance)."""
    return {
        "equal": expected == actual,
        "onlyExpected": sorted(expected - actual),
        "onlyActual": sorted(actual - expected),
    }

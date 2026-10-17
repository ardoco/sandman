"""Statistics of experiments/PROTOCOL.md section 3, 5 and 6. Standard library only.

A link is any hashable pair. Link-level items are (gold, a, b) booleans over the union of gold and predicted links.
"""

import math
import random
import statistics
from collections import Counter
from collections.abc import Hashable, Sequence
from collections.abc import Set as AbstractSet
from typing import Any

RESAMPLES = 10_000
SEED = 20261017
MIN_PROJECTS_WILCOXON = 6
T975 = (  # two-sided 95 % t quantile for 1..30 degrees of freedom
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131,
    2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042,
)  # fmt: skip
Item = tuple[bool, bool, bool]


def t_quantile(df: int) -> float:
    return T975[df - 1] if 1 <= df <= len(T975) else 1.96


def prf(tp: int, fp: int, fn: int, beta: float = 1.0) -> dict[str, float]:
    """Precision, recall and F-beta; 0 where the denominator is 0."""
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    b2 = beta * beta
    f = (1 + b2) * p * r / (b2 * p + r) if p + r else 0.0
    return {"precision": p, "recall": r, "f1" if beta == 1.0 else f"f{beta:g}": f}


def link_metrics(predicted: AbstractSet[Hashable], gold: AbstractSet[Hashable]) -> dict[str, float]:
    tp = len(predicted & gold)
    return prf(tp, len(predicted) - tp, len(gold) - tp)


def items(gold: AbstractSet[Hashable], a: AbstractSet[Hashable], b: AbstractSet[Hashable]) -> list[Item]:
    """One item per link of the union, in sorted order (determinism)."""
    union = sorted(gold | a | b, key=repr)
    return [(x in gold, x in a, x in b) for x in union]


def _counts(sample: Sequence[Item]) -> tuple[int, ...]:
    tpa = sum(1 for g, a, _ in sample if g and a)
    fpa = sum(1 for g, a, _ in sample if a and not g)
    fna = sum(1 for g, a, _ in sample if g and not a)
    tpb = sum(1 for g, _, b in sample if g and b)
    fpb = sum(1 for g, _, b in sample if b and not g)
    fnb = sum(1 for g, _, b in sample if g and not b)
    return tpa, fpa, fna, tpb, fpb, fnb


def _diffs(c: tuple[int, ...]) -> dict[str, tuple[float, float, float]]:
    ma, mb = prf(*c[:3]), prf(*c[3:])
    return {k: (ma[k], mb[k], mb[k] - ma[k]) for k in ("precision", "recall", "f1")}


def paired_bootstrap(data: Sequence[Item], resamples: int = RESAMPLES, seed: int = SEED) -> dict[str, dict[str, Any]]:
    """Per metric: a, b, diff = b - a, 95 % percentile CI of diff and a two-sided bootstrap p-value of diff = 0."""
    rng = random.Random(seed)
    point = _diffs(_counts(data))
    draws: dict[str, list[float]] = {k: [] for k in point}
    for _ in range(resamples):
        for k, v in _diffs(_counts(rng.choices(data, k=len(data)))).items():
            draws[k].append(v[2])
    out: dict[str, dict[str, Any]] = {}
    for k, (a, b, d) in point.items():
        s = sorted(draws[k])
        lo, hi = s[int(0.025 * resamples)], s[math.ceil(0.975 * resamples) - 1]
        le, ge = sum(1 for x in s if x <= 0), sum(1 for x in s if x >= 0)
        p = min(1.0, 2 * (min(le, ge) + 1) / (resamples + 1))
        out[k] = {"a": a, "b": b, "diff": d, "ci": [lo, hi], "p": p}
    return out


def f1_ci(
    predicted: AbstractSet[Hashable], gold: AbstractSet[Hashable], resamples: int = RESAMPLES, seed: int = SEED
) -> list[float]:
    """95 % bootstrap CI of the F1 of one system (resampling the union of gold and predicted links)."""
    pairs = [(x in gold, x in predicted) for x in sorted(gold | predicted, key=repr)]
    rng = random.Random(seed)
    vals = []
    for _ in range(resamples):
        s = rng.choices(pairs, k=len(pairs))
        tp = sum(1 for g, a in s if g and a)
        vals.append(prf(tp, sum(1 for g, a in s if a and not g), sum(1 for g, a in s if g and not a))["f1"])
    vals.sort()
    return [vals[int(0.025 * resamples)], vals[math.ceil(0.975 * resamples) - 1]]


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact binomial p-value; b and c count the pairs only A (b) or only B (c) decides correctly."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return float(min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2**n))


def mcnemar_counts(data: Sequence[Item]) -> tuple[int, int]:
    """(b, c): items only A decides correctly, items only B decides correctly."""
    b = sum(1 for g, a, bb in data if a == g and bb != g)
    c = sum(1 for g, a, bb in data if bb == g and a != g)
    return b, c


def cohens_g(b: int, c: int) -> float:
    return b / (b + c) - 0.5 if b + c else 0.0


def odds_ratio(b: int, c: int) -> float:
    """b / c; 0.5 is added to both counts if one is zero."""
    if b == 0 or c == 0:
        b, c = b + 0.5, c + 0.5  # type: ignore[assignment]
    return b / c


def cliffs_delta(x: Sequence[float], y: Sequence[float]) -> float:
    return sum((u > v) - (u < v) for u in x for v in y) / (len(x) * len(y))


def holm(pvalues: Sequence[float]) -> list[float]:
    """Holm-adjusted p-values in the input order."""
    m = len(pvalues)
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(sorted(range(m), key=lambda j: pvalues[j])):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def wilcoxon_exact(diffs: Sequence[float]) -> float:
    """Two-sided exact signed-rank p-value. Zero differences are dropped. At least 6 pairs are required."""
    d = [x for x in diffs if x != 0]
    if len(d) < MIN_PROJECTS_WILCOXON:
        raise ValueError(f"Wilcoxon needs at least {MIN_PROJECTS_WILCOXON} non-zero pairs, got {len(d)}")
    order = sorted(range(len(d)), key=lambda i: abs(d[i]))
    ranks2 = [0] * len(d)  # doubled average ranks are integers
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and abs(d[order[j + 1]]) == abs(d[order[i]]):
            j += 1
        for k in range(i, j + 1):
            ranks2[order[k]] = i + j + 2
        i = j + 1
    dist = Counter({0: 1})
    for r in ranks2:
        nxt: Counter[int] = Counter()
        for s, n in dist.items():
            nxt[s] += n
            nxt[s + r] += n
        dist = nxt
    w = sum(r for r, x in zip(ranks2, d, strict=True) if x > 0)
    total = 2 ** len(d)
    lower = sum(n for s, n in dist.items() if s <= w) / total
    upper = sum(n for s, n in dist.items() if s >= w) / total
    return float(min(1.0, 2 * min(lower, upper)))


def cohens_kappa(pairs: Sequence[tuple[Hashable, Hashable]]) -> float:
    """Cohen's kappa of two raters over (label a, label b) pairs; 1.0 if chance agreement is 1."""
    n = len(pairs)
    if n == 0:
        raise ValueError("kappa needs at least one item")
    po = sum(1 for a, b in pairs if a == b) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in ca) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def kappa_ci(pairs: Sequence[tuple[Hashable, Hashable]], resamples: int = RESAMPLES, seed: int = SEED) -> list[float]:
    """95 % percentile bootstrap CI of Cohen's kappa, resampling items."""
    rng = random.Random(seed)
    s = sorted(cohens_kappa(rng.choices(pairs, k=len(pairs))) for _ in range(resamples))
    return [s[int(0.025 * resamples)], s[math.ceil(0.975 * resamples) - 1]]


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    """95 % Wilson score interval of k successes in n trials; [0, 1] for n = 0."""
    if n == 0:
        return [0.0, 1.0]
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [max(0.0, centre - half), min(1.0, centre + half)]


def stratified_proportion(strata: Sequence[tuple[int, int, int]], z: float = 1.96) -> dict[str, Any]:
    """Stratified estimate over (population N, labeled n, successes k) per stratum, weights N / sum N.

    Normal CI with finite population correction; strata with n = 0 are dropped and the weights renormalized.
    """
    # ponytail: normal approximation, too narrow when a sampled stratum is all 0 or all 1; bootstrap if that matters
    used = [(big, n, k) for big, n, k in strata if n]
    total = sum(big for big, _, _ in used)
    if not total:
        return {"estimate": None, "ci": [0.0, 1.0], "strata": 0}
    est = sum(big / total * k / n for big, n, k in used)
    var = sum(
        (big / total) ** 2 * (1 - n / big) * (k / n) * (1 - k / n) / max(n - 1, 1) for big, n, k in used if big > 0
    )
    half = z * math.sqrt(var)
    return {"estimate": est, "ci": [max(0.0, est - half), min(1.0, est + half)], "strata": len(used)}


def runs_needed(sd: float, pilot_runs: int, half_width: float = 0.02, lo: int = 3, hi: int = 20) -> tuple[int, bool]:
    """Variance pilot (protocol section 5): runs per configuration and whether the cap hid a larger need."""
    if pilot_runs < 2:
        raise ValueError("the pilot needs at least 2 runs")
    raw = math.ceil((t_quantile(pilot_runs - 1) * sd / half_width) ** 2)
    return min(max(raw, lo), hi), raw > hi


def contamination_verdict(original: Sequence[float], perturbed: Sequence[float]) -> dict[str, Any]:
    """Protocol section 6: flag if the drop exceeds 2 sd of the baseline and the Welch CI excludes 0."""
    if len(original) < 2 or len(perturbed) < 2:
        raise ValueError("at least 2 runs per variant are required")
    drop = statistics.fmean(original) - statistics.fmean(perturbed)
    vo, vp = statistics.variance(original) / len(original), statistics.variance(perturbed) / len(perturbed)
    se = math.sqrt(vo + vp)
    df = max(1, int((vo + vp) ** 2 / (vo**2 / (len(original) - 1) + vp**2 / (len(perturbed) - 1)))) if se else 1
    half = t_quantile(df) * se
    sd = statistics.stdev(original)
    return {
        "drop": drop,
        "ci": [drop - half, drop + half],
        "baselineSd": sd,
        "flagged": drop > 2 * sd and drop - half > 0,
    }

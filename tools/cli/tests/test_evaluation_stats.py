import math

import pytest
from sandman_cli.evaluation import stats


def test_prf_hand_computed() -> None:
    m = stats.prf(6, 2, 4)  # p = 0.75, r = 0.6, f1 = 2*0.45/1.35
    assert m["precision"] == 0.75 and m["recall"] == 0.6 and math.isclose(m["f1"], 2 / 3)
    assert stats.prf(0, 0, 0) == {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    assert math.isclose(stats.prf(6, 2, 4, beta=2)["f2"], 5 * 0.75 * 0.6 / (4 * 0.75 + 0.6))


def test_bootstrap_deterministic_and_identical_systems() -> None:
    gold = {(i, "m") for i in range(30)}
    a = {(i, "m") for i in range(20)} | {(100, "x")}
    b = {(i, "m") for i in range(25)}
    data = stats.items(gold, a, b)
    r1 = stats.paired_bootstrap(data, resamples=500, seed=1)
    assert r1 == stats.paired_bootstrap(data, resamples=500, seed=1)
    f1 = r1["f1"]
    assert f1["diff"] > 0 and f1["ci"][0] <= f1["diff"] <= f1["ci"][1]
    same = stats.paired_bootstrap(stats.items(gold, a, a), resamples=200, seed=1)
    assert same["f1"]["ci"] == [0.0, 0.0] and same["f1"]["p"] == 1.0


def test_f1_ci_contains_point_estimate() -> None:
    gold = {(i, "m") for i in range(30)}
    pred = {(i, "m") for i in range(10, 40)}
    lo, hi = stats.f1_ci(pred, gold, resamples=500)
    assert lo <= stats.link_metrics(pred, gold)["f1"] <= hi


def test_mcnemar() -> None:
    assert stats.mcnemar_exact(8, 2) == 0.109375
    assert stats.mcnemar_exact(0, 0) == 1.0 and stats.mcnemar_exact(5, 5) == 1.0
    data = [(True, True, False)] * 3 + [(True, False, True)] + [(False, True, False)] * 2
    assert stats.mcnemar_counts(data) == (3, 3)  # A right on 3 items, B right on 1 + 2
    assert stats.cohens_g(8, 2) == pytest.approx(0.3)
    assert stats.odds_ratio(4, 2) == 2.0 and stats.odds_ratio(3, 0) == 7.0


def test_holm_textbook() -> None:
    assert stats.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert stats.holm([0.5]) == [0.5]
    assert stats.holm([0.2, 0.01, 0.9]) == pytest.approx([0.4, 0.03, 0.9])


def test_wilcoxon_needs_six_pairs() -> None:
    with pytest.raises(ValueError, match="at least 6"):
        stats.wilcoxon_exact([0.1] * 5)
    assert stats.wilcoxon_exact([0.1, 0.2, 0.3, 0.4, 0.5, 0.6]) == 0.03125
    assert stats.wilcoxon_exact([0.1, -0.1, 0.2, -0.2, 0.3, -0.3]) == 1.0
    assert stats.wilcoxon_exact([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.0]) == 0.03125  # zeros dropped


def test_cliffs_delta() -> None:
    assert stats.cliffs_delta([3, 4], [1, 2]) == 1.0 and stats.cliffs_delta([1, 2], [1, 2]) == 0.0


def test_variance_pilot() -> None:
    n, capped = stats.runs_needed(0.02, 5)  # t(4) = 2.776: (2.776 * 0.02 / 0.02)^2 = 7.7 -> 8
    assert (n, capped) == (8, False)
    assert stats.runs_needed(0.0, 5) == (3, False)
    assert stats.runs_needed(0.2, 5) == (20, True)
    with pytest.raises(ValueError):
        stats.runs_needed(0.1, 1)


def test_contamination_probe() -> None:
    base = [0.60, 0.62, 0.61, 0.59, 0.60]
    assert stats.contamination_verdict(base, [0.40, 0.41, 0.39, 0.40, 0.42])["flagged"] is True
    assert stats.contamination_verdict(base, [0.60, 0.61, 0.59, 0.62, 0.60])["flagged"] is False
    assert stats.contamination_verdict(base, [0.59, 0.60, 0.60, 0.58, 0.60])["flagged"] is False  # drop below 2 sd

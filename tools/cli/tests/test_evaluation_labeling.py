import csv
import io
import json
import math
from pathlib import Path
from typing import Any

import pytest
from sandman_cli.__main__ import main
from sandman_cli.evaluation import labeling, stats


def _model(fid: str, detector: str, project: str, name: str, status: str = "OPEN") -> dict[str, Any]:
    return {
        "id": fid, "projectId": project, "type": "undocumented-model-element", "detector": detector,
        "detectorVersion": "0.1.0+0d1f22b", "confidence": 0.9, "status": status, "severity": "warning",
        "title": f"Undocumented model element: {name}",
        "evidence": {"modelId": f"_{name}", "elementName": name, "elementType": "BasicComponent"},
        "involved": [{"role": "subject", "element": {"id": f"e_{name}", "key": f"m#_{name}", "label": "ModelElement",
                                                      "name": name, "artifactPath": "model/ms.repository"}}],
    }  # fmt: skip


def _text(fid: str, detector: str, project: str, n: int, word: str) -> dict[str, Any]:
    return {
        "id": fid, "projectId": project, "type": "missing-model-element", "detector": detector,
        "confidence": 0.63, "status": "OPEN",
        "evidence": {"sentence": n, "words": [{"word": word, "confidence": 0.63}]},
        "involved": [{"role": "subject", "element": {"id": f"t{n}", "key": f"d#{n}", "label": "TextUnit", "name": "s",
                                                      "artifactPath": "doc/ms.md", "excerpt": f"S{n}: {word}."}}],
    }  # fmt: skip


def _export() -> list[dict[str, Any]]:
    out = []
    for p in ("mediastore", "teastore"):
        for d in labeling.DETECTORS:
            out += [_model(f"{p}-{d}-m{i}", d, p, f"C{i}") for i in range(8)]
            out += [_text(f"{p}-{d}-t{i}", d, p, i, "cache" if d == "ardoco-id" else "Cache Store") for i in range(12)]
    out.append(_model("resolved", "ardoco-id", "mediastore", "Gone", status="RESOLVED"))
    out.append(_model("stub", "stub-untraced", "mediastore", "X"))
    return out


def test_kappa_known_values() -> None:
    # 20 yes/yes, 5 yes/no, 10 no/yes, 15 no/no: po = 0.7, pe = 0.5
    pairs = [("y", "y")] * 20 + [("y", "n")] * 5 + [("n", "y")] * 10 + [("n", "n")] * 15
    assert math.isclose(stats.cohens_kappa(pairs), 0.4)
    assert stats.cohens_kappa([("x", "x"), ("x", "y"), ("y", "x"), ("y", "y")]) == 0.0
    assert stats.cohens_kappa([("a", "a"), ("b", "b"), ("c", "c")]) == 1.0
    assert stats.cohens_kappa([("a", "a")] * 4) == 1.0
    lo, hi = stats.kappa_ci(pairs, resamples=2000, seed=1)
    assert lo < 0.4 < hi and lo > 0 and hi < 0.8
    with pytest.raises(ValueError):
        stats.cohens_kappa([])


def test_wilson_and_stratified() -> None:
    assert [round(x, 4) for x in stats.wilson(5, 10)] == [0.2366, 0.7634]
    assert [round(x, 4) for x in stats.wilson(0, 10)] == [0.0, 0.2775]
    assert stats.wilson(0, 0) == [0.0, 1.0]
    census = stats.stratified_proportion([(10, 10, 8), (30, 30, 15)])
    assert math.isclose(census["estimate"], 0.25 * 0.8 + 0.75 * 0.5) and census["ci"][0] == census["ci"][1]
    s = stats.stratified_proportion([(100, 30, 15), (100, 0, 0)])
    assert s["strata"] == 1 and s["estimate"] == 0.5 and s["ci"][0] < 0.5 < s["ci"][1]


def test_sample_strata_blinding_and_determinism() -> None:
    sheets, key = labeling.sample(_export(), per_stratum=5, seed=7)
    assert len(key["strata"]) == 8 and all(v == {"population": 8 if "undoc" in s else 12, "sampled": 5}
                                           for s, v in key["strata"].items())  # fmt: skip
    assert "stub-untraced" not in json.dumps(key["strata"]) and "resolved" not in json.dumps(key["items"])
    a, b = sheets["a"], sheets["b"]
    assert {r["item"] for r in a} == {r["item"] for r in b} == set(key["items"])
    assert [r["item"] for r in a] != [r["item"] for r in b]
    text = labeling.write_sheet(a)
    assert text.splitlines()[0] == ",".join(labeling.SHEET)
    for hint in ("ardoco", "artemis", "0.63", "0.9", "OPEN", "warning", "Undocumented model element"):
        assert hint not in text
    # same model element flagged by both detectors is one item
    merged = [v for v in key["items"].values() if len(v) > 1]
    assert merged and all(e["stratum"].startswith("ardoco-id") != f["stratum"].startswith("ardoco-id")
                          for e, f in merged)  # fmt: skip
    assert labeling.sample(_export(), 5, 7) == (sheets, key)
    assert labeling.sample(_export(), 5, 8)[1] != key


def _labels(rows: list[dict[str, str]], pick: dict[str, str]) -> str:
    return labeling.write_sheet([{**r, "label": pick[r["item"]]} for r in rows])


def test_score_kappa_precision_adjudication(tmp_path: Path) -> None:
    findings = [_model(f"f{i}", "ardoco-id", "mediastore", f"C{i}") for i in range(50)]
    sheets, key = labeling.sample(findings, per_stratum=50, seed=1)
    ids = sorted(key["items"])
    a = {i: "true" if n < 25 else "false" for n, i in enumerate(ids)}
    b = {i: "true" if n < 20 or 35 <= n < 45 else "false" for n, i in enumerate(ids)}  # 20/5/10/15 table
    ra = labeling.read_sheet(_labels(sheets["a"], a), ids)
    rb = labeling.read_sheet(_labels(sheets["b"], b), ids)
    report = labeling.score(key, ra, rb, resamples=500)
    assert math.isclose(report["agreement"]["all"]["kappa"], 0.4) and report["agreement"]["all"]["observed"] == 0.7
    assert len(report["disagreements"]) == 15 and report["unresolved"] == 15
    st = report["strata"]["ardoco-id|undocumented-model-element|mediastore"]
    assert (st["true"], st["false"], st["unresolved"]) == (20, 15, 15)
    adj_text = labeling.disagreement_sheet(ra, rb)
    adj_rows = list(csv.DictReader(io.StringIO(adj_text)))
    assert len(adj_rows) == 15 and {"label_a", "label_b", "sentence"} <= set(adj_rows[0])
    adj = labeling.read_sheet(labeling.write_sheet([{**r, "label": "true"} for r in adj_rows]), ids, partial=True)
    done = labeling.score(key, ra, rb, adj, resamples=200)
    st = done["strata"]["ardoco-id|undocumented-model-element|mediastore"]
    assert done["unresolved"] == 0 and st["precision"] == 35 / 50 and st["ci"] == stats.wilson(35, 50)
    pooled = done["pooled"]["byDetector"]["ardoco-id"]
    assert pooled["estimate"] == 0.7 and pooled["ci"][0] == pooled["ci"][1]  # census


def test_read_sheet_rejects_bad_labels() -> None:
    sheet = "item,label\ni0001,maybe\n"
    with pytest.raises(ValueError, match="not in"):
        labeling.read_sheet(sheet, ["i0001"])
    with pytest.raises(ValueError, match="missing"):
        labeling.read_sheet("item,label\ni0001,TRUE\n", ["i0001", "i0002"])
    with pytest.raises(ValueError, match="unknown"):
        labeling.read_sheet("item,label\ni0009,true\n", ["i0001"])
    assert labeling.read_sheet("item,label\ni0001, True \n", ["i0001"])["i0001"]["label"] == "true"


def test_cli_label_sample_and_score(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exp = tmp_path / "ms.json"
    exp.write_text(json.dumps(_export()), encoding="utf-8")
    out = tmp_path / "study"
    assert main(["eval", "label-sample", "--export", str(exp), "--per-stratum", "4", "--out", str(out)]) == 0
    summary = json.loads(capsys.readouterr().out)
    key = json.loads((out / "key.json").read_text())
    assert summary["items"] == len(key["items"]) and str(exp) in key["exports"]
    for r in ("a", "b"):
        rows = list(csv.DictReader(io.StringIO((out / f"rater-{r}.csv").read_text())))
        (out / f"rater-{r}.csv").write_text(labeling.write_sheet([{**x, "label": "true"} for x in rows]))
    argv = ["eval", "label-score", "--key", str(out / "key.json"), "--a", str(out / "rater-a.csv"),
            "--b", str(out / "rater-b.csv"), "--resamples", "100", "--disagreements", str(out / "dis.csv")]  # fmt: skip
    assert main(argv) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["agreement"]["all"]["kappa"] == 1.0 and report["disagreements"] == []
    assert (out / "dis.csv").read_text().startswith("item,")
    (out / "rater-b.csv").write_text("item,label\n")
    with pytest.raises(SystemExit, match="missing items"):
        main(argv)

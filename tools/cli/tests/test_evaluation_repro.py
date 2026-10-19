import json
from pathlib import Path

import pytest
from sandman_cli.__main__ import main
from sandman_cli.evaluation.perturb import build_mapping, rename


def test_rename_whole_identifiers_only() -> None:
    m = build_mapping(["Cache", "MediaCache"], 1)
    out = rename("MediaCache uses Cache; CacheEntry and my_Cache stay. Cache.get()", m)
    assert out == f"{m['MediaCache']} uses {m['Cache']}; CacheEntry and my_Cache stay. {m['Cache']}.get()"
    assert build_mapping(["Cache"], 1) == build_mapping(["Cache"], 1) != build_mapping(["Cache"], 2)


def test_cli_equal_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("modelElementID,sentence\nm1,1\nm2,2\n")
    b.write_text("modelElementID,sentence\nm2,2\nm1,1\n")
    args = ["eval", "equal", "--format", "ardoco-sad-sam", "--expected", str(a), "--actual", str(b)]
    assert main(args) == 0
    b.write_text("modelElementID,sentence\nm1,1\nm3,3\n")
    assert main(args) == 1
    out = json.loads(capsys.readouterr().out.split("}\n", 1)[1])
    assert out["onlyExpected"] == [["m2", "2"]] and out["onlyActual"] == [["m3", "3"]]


def test_cli_pilot_contamination_perturb(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["eval", "pilot", "--f1", "0.70", "--f1", "0.74", "--f1", "0.66", "--f1", "0.72", "--f1", "0.68"]) == 0
    pilot = json.loads(capsys.readouterr().out)
    assert pilot["runs"] == 20 and pilot["capped"] is False
    assert (
        main(["eval", "contamination", "--original", "0.8", "0.81", "0.79", "--perturbed", "0.5", "0.52", "0.48"]) == 0
    )
    assert json.loads(capsys.readouterr().out)["flagged"] is True
    names, src = tmp_path / "names.txt", tmp_path / "sad.txt"
    names.write_text("Cache\n")
    src.write_text("The Cache stores data.\n")
    assert main(["eval", "perturb", "--names", str(names), "--out", str(tmp_path / "o"), str(src)]) == 0
    mapping = json.loads((tmp_path / "o/mapping.json").read_text())
    assert (tmp_path / "o/sad.txt").read_text() == f"The {mapping['Cache']} stores data.\n"

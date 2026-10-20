import json
from pathlib import Path

from sandman_cli.evaluation import removal

GOLD = """ae_id,ae_name,ce_ids
a,Component: A,src/a/
b,Component: B,src/b/B1.java
c,Component: C,src/c/C1.java
i,Interface: I,src/a/A1.java
"""
PCM = """<?xml version="1.0" encoding="UTF-8"?>
<repository:Repository xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:repository="urn:repo" id="r">
  <components__Repository xsi:type="repository:BasicComponent" id="a" entityName="A"/>
  <components__Repository xsi:type="repository:BasicComponent" id="b" entityName="B"/>
  <components__Repository xsi:type="repository:BasicComponent" id="c" entityName="C"/>
</repository:Repository>
"""
PATHS = ["src/a/A1.java", "src/a/A2.java", "src/b/B1.java", "src/c/C1.java", "src/c/package-info.java"]


def unit(name: str, parent: str) -> dict[str, object]:
    return {
        "type": "CodeCompilationUnit",
        "name": name,
        "extension": "java",
        "language": "JAVA",
        "pathElements": parent.split("/"),
        "content": [],
    }


def write_acm(tmp_path: Path) -> Path:
    repo = {f"u{i}": unit(Path(p).stem, str(Path(p).parent)) for i, p in enumerate(PATHS)}
    repo["u0"]["content"] = ["t0"]
    repo["t0"] = {"type": "ClassUnit", "name": "A1", "content": []}
    path = tmp_path / "code.acm"
    path.write_text(json.dumps({"codeItemRepository": {"repository": repo}, "content": list(repo)}))
    return path


def write_links(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "links.csv"
    path.write_text("ae_id,ce_ids\n" + "\n".join(rows) + "\n")
    return path


def evaluate(tmp_path: Path, rows: list[str]) -> dict:
    (tmp_path / "gold.csv").write_text(GOLD)
    (tmp_path / "ms.repository").write_text(PCM)
    return removal.evaluate(
        tmp_path / "gold.csv",
        tmp_path / "ms.repository",
        write_acm(tmp_path),
        write_links(tmp_path, rows),
        mode="simulated",
        seed=42,
        resamples=50,
    )


FULL_LINKS = ["a,src/a/A1.java", "a,src/a/A2.java", "b,src/b/B1.java", "c,src/c/C1.java"]


def test_units_decode_and_drop_files(tmp_path: Path) -> None:
    acm = write_acm(tmp_path)
    assert sorted(removal.units(acm).values()) == sorted(PATHS)
    kept = json.loads(removal.drop_files(acm, ["u0"]))
    assert "u0" not in kept["codeItemRepository"]["repository"]
    assert "t0" not in kept["codeItemRepository"]["repository"]
    assert "u0" not in kept["content"]


def test_components_and_drop_component(tmp_path: Path) -> None:
    pcm = tmp_path / "ms.repository"
    pcm.write_text(PCM)
    assert removal.components(pcm) == {"a": "A", "b": "B", "c": "C"}
    pcm.write_text(removal.drop_component(pcm, "b"))
    assert removal.components(pcm) == {"a": "A", "c": "C"}


def test_detect_applies_rules_and_globs() -> None:
    found = removal.detect({"a", "b"}, PATHS, [("a", "src/a/A1.java"), ("a", "gone.java")])
    assert found == {
        (removal.MODEL, "b"),
        (removal.CODE, "src/a/A2.java"),
        (removal.CODE, "src/b/B1.java"),
        (removal.CODE, "src/c/C1.java"),
    }


def test_perfect_links_give_perfect_scores(tmp_path: Path) -> None:
    result = evaluate(tmp_path, FULL_LINKS)
    assert result["runs"] == 6
    assert result["baseline"] == {removal.MODEL: 0, removal.CODE: 0}
    assert result["simulated"][removal.MODEL]["tp"] == 3
    assert result["simulated"][removal.CODE]["tp"] == 4
    for kind in removal.TYPES:
        assert result["simulated"][kind]["f1"] == 1.0
        assert result["simulated"][kind]["fp"] == 0
        assert result["simulated"][kind]["fn"] == 0


def test_relinked_file_is_a_false_negative(tmp_path: Path) -> None:
    result = evaluate(tmp_path, [*FULL_LINKS, "b,src/a/A2.java"])
    assert result["simulated"][removal.CODE]["fn"] == 1
    assert result["simulated"][removal.CODE]["recall"] == 0.75


def test_baseline_findings_are_not_false_positives_and_skip_runs(tmp_path: Path) -> None:
    result = evaluate(tmp_path, ["a,src/a/A1.java", "a,src/a/A2.java", "b,src/b/B1.java"])
    assert result["baseline"] == {removal.MODEL: 1, removal.CODE: 1}
    assert {s["id"] for s in result["skipped"]} == {"code:c", "model:c"}
    assert result["simulated"][removal.MODEL]["fp"] == 0
    agreement = result["baselineAgreement"]
    assert agreement[removal.MODEL]["gold"] == 0
    assert agreement[removal.CODE]["gold"] == 0
    assert agreement[removal.CODE]["predicted"] == 1


def test_sample_is_seeded_and_per_kind() -> None:
    runs = [{"id": f"{k}:{i}", "kind": k} for k in ("code", "model") for i in range(5)]
    first = removal.sample(runs, 3, 42)
    assert first == removal.sample(runs, 3, 42)
    assert [r["kind"] for r in first] == ["code"] * 3 + ["model"] * 3


def test_agreement_counts_expected_findings() -> None:
    chosen = [{"id": "x", "expected": {(removal.MODEL, "a"), (removal.MODEL, "b")}}]
    simulated = {"x": {(removal.MODEL, "a"), (removal.MODEL, "b")}}
    full = {"x": {(removal.MODEL, "a")}}
    result = removal.agreement(chosen, simulated, full)
    assert result["expectedAgreement"] == 0.5
    assert result["findingSetJaccard"] == 0.5

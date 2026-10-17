import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from sandman_cli.__main__ import main
from sandman_cli.evaluation import nlquery

Q_UNTRACED = "MATCH (r:Requirement {projectId: $projectId}) WHERE r.name = 'a' RETURN r.key AS key"
Q_UNTRACED_OTHER = "MATCH (r:Requirement {projectId: $projectId}) WHERE r.name = 'bb' RETURN r.key AS key"
Q_ART = "MATCH (a:Artifact {projectId: $projectId}) RETURN a.path AS path"
Q_COUNT = "MATCH (c:ModelElement {projectId: $projectId}) RETURN count(c) AS n"


def item(id_: str, project: str = "p1", split: str = "dev", strategy: str = "cypher", **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": id_, "project": project, "question": f"question {id_} about things", "strategy": strategy,
        "author": "ai-draft", "split": split,
    }  # fmt: skip
    if strategy == "cypher":
        base["cypher"] = Q_COUNT
    else:
        base["gold_keys"] = ["a#1", "a#2"]
    return base | kw


def test_validate_format() -> None:
    assert nlquery.validate([item("a"), item("b", strategy="graphrag")]) == []
    bad = [
        item("a"),
        item("a"),
        item("c", cypher="MATCH (n) RETURN n LIMIT 5"),
        item("d", strategy="explore", gold_keys=[]),
        item("e", split="test"),
        item("f", strategy="nope"),
    ]
    text = "\n".join(nlquery.validate(bad))
    for needle in ("duplicate id", "$projectId", "no LIMIT", "need gold_keys", "human-written", "strategy must be"):
        assert needle in text


def test_template_ignores_literals_not_shape() -> None:
    assert nlquery.template(Q_UNTRACED) == nlquery.template(Q_UNTRACED_OTHER)
    assert nlquery.template(Q_UNTRACED) != nlquery.template(Q_COUNT)


def test_leakage() -> None:
    dev = [item("d1", cypher=Q_UNTRACED), item("d2", "p2", question="list all components please", cypher=Q_ART)]
    clean = [item("t1", "p3", "test", author="human:x", cypher=Q_COUNT, question="how many elements exist")]
    assert nlquery.leakage(dev + clean) == []
    leaky = [
        item("t2", "p1", "test", author="human:x", cypher=Q_UNTRACED_OTHER, question="what is this"),
        item("t3", "p4", "test", author="human:x", cypher=Q_COUNT, question="list all components please"),
    ]
    text = "\n".join(nlquery.leakage(dev + leaky))
    assert "t2: same query template as dev item d1" in text
    assert "project p1 occurs in dev and test" in text
    assert "t3: question near-duplicate of dev item d2" in text
    assert "project p1" not in "\n".join(nlquery.leakage(dev + leaky, by_project=False))


def test_counts_do_not_claim_target() -> None:
    c = nlquery.counts([item("a"), item("b", strategy="explore")])
    assert (c["total"], c["content_and_multihop"], c["met"]) == (2, 1, False)


def test_same_rows_multiset_order_insensitive() -> None:
    gold = {"columns": ["k", "n"], "rows": [["a", 1], ["b", 2], ["b", 2]]}
    assert nlquery.same_rows(gold, {"columns": ["x", "y"], "rows": [["b", 2], [1, "a"], ["b", 2]]})
    assert not nlquery.same_rows(gold, {"rows": [["a", 1], ["b", 2]]})  # multiset, duplicates count
    assert not nlquery.same_rows(gold, {"rows": gold["rows"], "truncated": True})
    assert not nlquery.same_rows(gold, {"error": "boom", "rows": gold["rows"]})
    assert nlquery.same_rows({"rows": []}, {"rows": []})


def test_exec_accuracy() -> None:
    items = [item("a"), item("b", "p2"), item("c"), item("g", strategy="graphrag")]
    gold = {i: {"rows": [[1]]} for i in "abc"}
    pred = {"a": {"rows": [[1]]}, "b": {"rows": [[2]]}, "c": {"error": "syntax"}}
    r = nlquery.exec_accuracy(items, gold, pred)
    assert (r["items"], r["correct"], r["validity_rate"]) == (3, 1, 2 / 3)
    assert r["wrong"] == ["b", "c"] and r["by_project"]["p2"] == {"correct": 0, "items": 1}
    assert nlquery.exec_accuracy(items, gold, {})["correct"] == 0


def test_retrieval() -> None:
    items = [item("g1", strategy="graphrag"), item("e1", strategy="explore")]
    r = nlquery.retrieval(items, {"g1": ["a#1", "x", "y", "z"], "e1": ["a#1", "a#2"]})
    assert r["graphrag"]["macro_recall"] == 0.5 and r["graphrag"]["macro_precision"] == 0.25
    assert r["explore"]["macro_recall"] == 1.0 and r["explore"]["macro_precision"] == 1.0
    assert nlquery.retrieval(items, {})["explore"]["macro_precision"] == 0.0


def test_confusion() -> None:
    items = [item("a"), item("b"), item("c", strategy="graphrag"), item("d", strategy="explore")]
    r = nlquery.confusion(items, {"a": "cypher", "b": "explore", "c": "graphrag"})
    assert r["matrix"]["cypher"] == {"cypher": 1, "graphrag": 0, "explore": 1, "none": 0}
    assert r["matrix"]["explore"]["none"] == 1
    assert r["accuracy"] == 0.5
    assert r["per_strategy"]["explore"] == {"precision": 0.0, "recall": 0.0}


INTENTS = """intents:
  - id: open
    pattern: '(?:show |list )?(?:the )?open inconsistencies'
    cypher: |
      MATCH (i:Inconsistency {projectId: $projectId}) RETURN i.id AS id
  - id: links-of
    pattern: 'links of (?P<name>.+)'
    cypher: |
      MATCH (e:Element {projectId: $projectId}) WHERE e.name = $name RETURN e.key AS key
"""


def test_baseline_matches_like_the_service() -> None:
    items = [
        item("a", question="Show the open inconsistencies?"),
        item("b", question="links of  Facade"),
        item("c", question="which component is largest"),
    ]
    pred = nlquery.baseline(items, nlquery.intent_rules(INTENTS))
    assert pred["a"]["intent"] == "open" and pred["a"]["params"] == {"projectId": "p1"}
    assert pred["b"]["params"] == {"name": "Facade", "projectId": "p1"}
    assert pred["c"] == {"intent": None, "error": "no intent matches"}


def test_cli_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    items = [item("a", question="Show open inconsistencies"), item("g", strategy="graphrag")]
    ds = tmp_path / "ds.yaml"
    ds.write_text(yaml.safe_dump({"items": items}), encoding="utf-8")
    intents = tmp_path / "intents.yaml"
    intents.write_text(INTENTS, encoding="utf-8")
    base = tmp_path / "base.json"
    assert (
        main(["eval", "nlquery", "baseline", "--dataset", str(ds), "--intents", str(intents), "--out", str(base)]) == 0
    )
    assert json.loads(capsys.readouterr().out)["coverage"] == 1.0
    assert main(["eval", "nlquery", "validate", "--dataset", str(ds)]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["met"] is False
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({"a": {"rows": [[1]]}}), encoding="utf-8")
    pred = tmp_path / "pred.json"
    pred.write_text(json.dumps({"a": {"rows": [[1]]}}), encoding="utf-8")
    assert main(["eval", "nlquery", "exec", "--dataset", str(ds), "--gold", str(gold), "--predictions", str(pred)]) == 0
    assert json.loads(capsys.readouterr().out)["execution_accuracy"] == 1.0
    pred.write_text(json.dumps({"g": ["a#1"]}), encoding="utf-8")
    assert main(["eval", "nlquery", "retrieval", "--dataset", str(ds), "--predictions", str(pred)]) == 0
    assert json.loads(capsys.readouterr().out)["graphrag"]["macro_recall"] == 0.5
    pred.write_text(json.dumps({"a": "cypher", "g": "graphrag"}), encoding="utf-8")
    assert main(["eval", "nlquery", "classifier", "--dataset", str(ds), "--predictions", str(pred)]) == 0
    assert json.loads(capsys.readouterr().out)["accuracy"] == 1.0
    ds.write_text(yaml.safe_dump({"items": [item("x", split="test")]}), encoding="utf-8")
    assert main(["eval", "nlquery", "validate", "--dataset", str(ds)]) == 1

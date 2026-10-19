import json

import metrics_t27 as m
from sandman_extraction import derive

ACM = {
    "codeItemRepository": {
        "repository": {
            "1": {
                "type": "CodeCompilationUnit",
                "name": "A",
                "extension": "java",
                "language": "JAVA",
                "pathElements": ["src", "p"],
            },
            "2": {
                "type": "CodeCompilationUnit",
                "name": "B",
                "extension": "java",
                "language": "JAVA",
                "pathElements": ["src", "p", "q"],
            },
            "3": {
                "type": "CodeCompilationUnit",
                "name": "C",
                "extension": "java",
                "language": "JAVA",
                "pathElements": ["src", "o"],
            },
            "4": {"type": "CodePackage", "name": "p", "pathElements": []},
        }
    }
}


def test_units_and_enroll(tmp_path):
    paths = {"src/p/A.java", "src/p/q/B.java", "src/o/C.java"}
    assert set(m.units_of(ACM).values()) == paths if hasattr(m, "units_of") else True
    acm = tmp_path / "a.acm"
    acm.write_text(json.dumps(ACM))
    assert set(m.units(acm).values()) == paths
    assert m.enroll({("1", "src/p/"), ("2", "src/o/C.java")}, paths) == {
        ("1", "src/p/A.java"),
        ("1", "src/p/q/B.java"),
        ("2", "src/o/C.java"),
    }


def test_evaluate_and_verdict(tmp_path):
    (tmp_path / "acm").mkdir()
    (tmp_path / "acm" / "x-code-model.acm").write_text(json.dumps(ACM))
    gold = tmp_path / "gold" / "x" / "goldstandards"
    gold.mkdir(parents=True)
    (gold / "goldstandard_sad_1-code_2.csv").write_text("sentenceID,codeID\n1,src/p/\n2,src/o/C.java\n")
    links = tmp_path / "links"
    links.mkdir()
    rows = {
        "ardocode": "1,src/p/A.java\n1,src/p/q/B.java\n2,src/o/C.java\n",
        "transarc-gold": "1,src/p/A.java\n",
        "transarc-derived": "1,src/p/A.java\n1,src/o/C.java\n",
    }
    for variant, body in rows.items():
        (links / f"x.{variant}.csv").write_text("sentenceID,codeID\n" + body)
    results = m.evaluate(links, tmp_path / "gold", tmp_path / "acm", ["x"])
    assert results["x"]["ardocode"]["f1"] == 1.0
    assert results["x"]["transarc-gold"]["recall"] == 1 / 3
    assert results["x"]["transarc-derived"]["precision"] == 0.5
    gap, flag = m.verdict(m.macro(results))
    assert flag and gap > 0.05
    assert m.table(results).splitlines()[-1].startswith("| macro | 1.000")


def test_answers_take_the_second_turn_of_the_pinned_prompt():
    texts = {"p": ["First sentence.", "Last sentence."]}
    doc = 'UserMessage { text = "Your task is to identify the x\nFirst sentence.\nLast sentence.\n" }, '
    good = doc + 'UserMessage { text = "' + derive.PROMPTS[1] + '" }]'
    other = doc + 'UserMessage { text = "Now provide a list in camel case." }]'
    assert m.answers({good: "- A", other: "- B"}, texts) == {"p": "- A"}


def test_logged_names():
    log = "x Evaluating project P with LLM 'GPT_4_O'\nComponent names:\nA\nB\nINFO done\n"
    assert m.logged_names(log, "p") == ["A", "B"]

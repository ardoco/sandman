from sandman_agent.ids import sha256_hex
from sandman_extraction import builtin

LISTING = b"Alpha::Alpha\nBeta Gamma::Beta Gamma\n"


def test_one_component_per_line() -> None:
    elements, relations = builtin.extract(".sandman/derived/exarch/doc.md.components", "architecture-model",
                                          "component-listing", LISTING)  # fmt: skip
    assert relations == []
    assert [(e["label"], e["kind"], e["name"], e["localKey"]) for e in elements] == [
        ("ModelElement", "file", "doc.md.components", "file"),
        ("ModelElement", "component", "Alpha", "Alpha"),
        ("ModelElement", "component", "Beta Gamma", "Beta Gamma"),
    ]
    assert all(e["parent"] == "file" for e in elements[1:])


def test_component_without_text_hashes_the_name() -> None:
    elements, _ = builtin.extract("x.components", "architecture-model", None, LISTING)
    assert "text" not in elements[1]
    assert sha256_hex(elements[1].get("text") or elements[1]["name"]) == sha256_hex("Alpha")


def test_blank_lines_are_skipped() -> None:
    elements, _ = builtin.extract("x.components", "architecture-model", "component-listing", b"\n\nA::A\n\n")
    assert [e["name"] for e in elements[1:]] == ["A"]

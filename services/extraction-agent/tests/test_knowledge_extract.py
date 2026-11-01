"""T7.2 unit tests of the knowledge file extractor (no Docker)."""

from sandman_agent.ids import sha256_hex
from sandman_extraction import builtin, knowledge
from sandman_extraction.ingest import KNOWLEDGE_PATH

FILE = b"""version: 1
elements:
  - id: payment-provider
    label: ModelElement
    kind: component
    name: External Payment Provider
    text: Third-party service.
aliases:
  - element: model/mediastore.repository#_qx
    names: [Media Store, MS]
  - element: decl:payment-provider
    names: [PSP]
  - element: model/mediastore.repository#_qx
    names: [MS, Store]
links: []
"""
REORDERED = b"""aliases:
  - names: [Media Store, MS]
    element: model/mediastore.repository#_qx
  - names: [PSP]
    element: decl:payment-provider
  - names: [MS, Store]
    element: model/mediastore.repository#_qx
links: []
elements:
  - text: Third-party service.
    name: External Payment Provider
    kind: component
    label: ModelElement
    id: payment-provider
version: 1
"""


def by_key(content: bytes) -> dict[str, dict]:
    elements, relations = builtin.extract(KNOWLEDGE_PATH, "other", knowledge.FORMAT, content)
    assert relations == []
    return {e["localKey"]: e for e in elements}


def test_declared_elements_get_decl_keys_under_the_file_element() -> None:
    found = by_key(FILE)
    declared = found["decl:payment-provider"]
    assert (declared["label"], declared["kind"], declared["name"], declared["text"]) == (
        "ModelElement", "component", "External Payment Provider", "Third-party service.")  # fmt: skip
    assert declared["parent"] == "file"
    assert found["file"]["label"] == "TextUnit" and found["file"]["kind"] == "file"


def test_alias_elements_hold_all_names_of_one_reference() -> None:
    found = by_key(FILE)
    key = "alias:" + sha256_hex("model/mediastore.repository#_qx")[:12]
    assert (found[key]["label"], found[key]["kind"], found[key]["name"]) == ("TextUnit", "alias", "Media Store")
    assert found[key]["aliases"] == '["Media Store","MS","Store"]'
    assert len(found) == 4


def test_content_hashes_do_not_depend_on_key_order() -> None:
    first, second = by_key(FILE), by_key(REORDERED)
    assert {k: e["contentHash"] for k, e in first.items() if k != "file"} == {
        k: e["contentHash"] for k, e in second.items() if k != "file"
    }

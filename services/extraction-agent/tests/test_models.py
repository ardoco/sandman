"""PCM and UML extractors: benchmark id sets, snippets, hostile XML (T1.2)."""

import re
from pathlib import Path
from xml.etree.ElementTree import ParseError

import pytest
from sandman_extraction import builtin
from sandman_extraction.models import PCM, UML, extract

TESTS = Path(__file__).parent
BENCH = TESTS / "fixtures" / "benchmark"
MODELS = {
    "mediastore": (BENCH / "mediastore/pcm/ms.repository", BENCH / "mediastore/uml/ms.uml"),
    "teastore": (BENCH / "teastore/pcm/teastore.repository", BENCH / "teastore/uml/teastore.uml"),
}
PCM_HEAD = '<repository:Repository xmlns:repository="r" xmlns:xsi="x" id="r" entityName="r">'
UML_HEAD = '<uml:Model xmlns:uml="u" xmlns:xmi="x" xmi:id="m" name="m">'


def keys(elements: list[dict]) -> list[str]:
    return [e["localKey"] for e in elements if e["kind"] != "file"]


def pairs(relations: list[dict]) -> set[tuple[str, str, str]]:
    return {(r["type"], r["source"]["localKey"], r["target"]["localKey"]) for r in relations}


def pcm(body: str) -> bytes:
    return f"{PCM_HEAD}{body}</repository:Repository>".encode()


def uml(body: str) -> bytes:
    return f"{UML_HEAD}{body}</uml:Model>".encode()


def test_mediastore_ids_match_ardoco_endpoints() -> None:
    expected = {line.split()[0] for line in (TESTS / "mediastore-endpoints.txt").read_text().splitlines()}
    assert len(expected) == 23
    pcm_path, uml_path = MODELS["mediastore"]
    pcm_els, pcm_rels = extract("ms.repository", pcm_path.read_bytes(), PCM)
    uml_els, uml_rels = extract("ms.uml", uml_path.read_bytes(), UML)
    assert set(keys(pcm_els)) == expected == set(keys(uml_els))
    assert len(keys(pcm_els)) == 23
    assert pairs(pcm_rels) == pairs(uml_rels)
    assert {r["type"] for r in pcm_rels} == {"PROVIDES", "REQUIRES"}


@pytest.mark.parametrize("project", MODELS)
def test_every_key_occurs_in_source(project: str) -> None:
    for path, model in zip(MODELS[project], (PCM, UML), strict=True):
        content = path.read_bytes()
        elements, _ = extract(path.name, content, model)
        source = content.decode()
        for key in keys(elements):
            assert re.search(rf'\bid="{re.escape(key)}"', source)


def test_teastore_pcm_and_uml_agree() -> None:
    pcm_path, uml_path = MODELS["teastore"]
    pcm_els, pcm_rels = extract("t.repository", pcm_path.read_bytes(), PCM)
    uml_els, uml_rels = extract("t.uml", uml_path.read_bytes(), UML)
    assert set(keys(pcm_els)) == set(keys(uml_els))
    assert pairs(pcm_rels) == pairs(uml_rels)


def test_mini_repository() -> None:
    elements, relations = builtin.extract("m.repository", "architecture-model", None,
                                          (TESTS / "fixtures/mini/model/mini.repository").read_bytes())  # fmt: skip
    assert [(e["localKey"], e["name"]) for e in elements[1:]] == [("_mediacache", "MediaCache"), ("_userdb", "UserDB")]
    assert elements[0]["label"] == "ModelElement"
    assert relations == []


def test_pcm_roles_and_types() -> None:
    body = (
        '<components__Repository xsi:type="repository:CompositeComponent" id="c" entityName="C">'
        '<providedRoles_InterfaceProvidingEntity id="p" providedInterface__OperationProvidedRole="i"/>'
        '<requiredRoles_InterfaceRequiringEntity id="q" requiredInterface__OperationRequiredRole="j"/>'
        "</components__Repository>"
        '<components__Repository id="bare" entityName="Bare"/>'
        '<interfaces__Repository id="i" entityName="I"/><interfaces__Repository id="j" entityName="J"/>'
        '<interfaces__Repository id="lonely" entityName="Lonely"/>'
    )
    elements, relations = extract("a.repository", pcm(body), PCM)
    assert keys(elements) == ["c", "bare", "i", "j", "lonely"]
    assert pairs(relations) == {("PROVIDES", "c", "i"), ("REQUIRES", "c", "j")}
    c = elements[1]
    assert c["kind"] == "component" and c["text"] == "CompositeComponent C; provides I; requires J"
    assert c["parent"] == "file" and c["startLine"] == 1


def test_pcm_missing_interface_warns_without_relation() -> None:
    body = (
        '<components__Repository id="c" entityName="C">'
        '<providedRoles_InterfaceProvidingEntity providedInterface__OperationProvidedRole="gone"/>'
        "</components__Repository>"
    )
    warnings: list[str] = []
    elements, relations = extract("a.repository", pcm(body), PCM, warnings=warnings)
    assert keys(elements) == ["c"] and relations == []
    assert len(warnings) == 1 and "unknown interface gone" in warnings[0]


def test_duplicate_ids_first_wins() -> None:
    body = '<components__Repository id="c" entityName="First"/><components__Repository id="c" entityName="Second"/>'
    warnings: list[str] = []
    elements, _ = extract("a.repository", pcm(body), PCM, warnings=warnings)
    assert [e["name"] for e in elements[1:]] == ["First"]
    assert len(warnings) == 1 and "duplicate model id c" in warnings[0]


def test_uml_snippet_and_foreign_elements() -> None:
    body = (
        '<packagedElement xmi:type="uml:Interface" xmi:id="i" name="I"/>'
        '<packagedElement xmi:type="uml:Class" xmi:id="k" name="K"/>'
        '<packagedElement xmi:type="uml:Component" xmi:id="c" name="C">'
        '<interfaceRealization xmi:id="r" client="c" supplier="i"/>'
        '<packagedElement xmi:type="uml:Usage" xmi:id="u" client="c" supplier="i"/>'
        '<packagedElement xmi:type="uml:Usage" xmi:id="u2" client="c" supplier="i"/>'
        "</packagedElement>"
    )
    elements, relations = extract("a.uml", uml(body), UML)
    assert keys(elements) == ["i", "c"]
    assert [r["type"] for r in relations] == ["PROVIDES", "REQUIRES"]


def test_wrong_root_is_rejected() -> None:
    with pytest.raises(ValueError, match="root element"):
        extract("a.uml", b"<other/>", UML)
    with pytest.raises(ValueError, match="root element"):
        extract("a.repository", uml(""), PCM)


def test_size_limit() -> None:
    with pytest.raises(ValueError, match="limit"):
        extract("a.repository", pcm(""), PCM, max_bytes=10)
    with pytest.raises(ValueError, match="limit"):
        builtin.extract("a.repository", "architecture-model", None, pcm(""), max_xml_bytes=10)


HOSTILE = {
    "dtd": b"<!DOCTYPE repository:Repository [<!ELEMENT a ANY>]>" + pcm(""),
    "external entity": b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    + pcm('<components__Repository id="&x;"/>'),
    "billion laughs": (
        b'<!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;">]>'
        + pcm('<components__Repository id="&c;" entityName="x"/>')
    ),
}


@pytest.mark.parametrize("payload", HOSTILE.values(), ids=HOSTILE.keys())
def test_hostile_xml_is_rejected(payload: bytes) -> None:
    with pytest.raises(ValueError):
        builtin.extract("a.repository", "architecture-model", None, payload)


def test_malformed_xml_raises() -> None:
    with pytest.raises(ParseError):
        builtin.extract("a.uml", "architecture-model", None, b"<uml:Model")

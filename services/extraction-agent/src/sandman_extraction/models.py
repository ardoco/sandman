"""PCM repository and UML XMI extractors: components, interfaces and PROVIDES/REQUIRES relations (T1.2).

Follows ArDoCo's model reading (PcmExtractor/PcmComponent, UmlExtractor/UmlModelRoot/PackagedElement):
- PCM: `components__Repository` and `interfaces__Repository` of the root `Repository`; provided/required interfaces
  come from the `providedInterface__*` / `requiredInterface__*` attribute of `providedRoles_InterfaceProvidingEntity` /
  `requiredRoles_InterfaceRequiringEntity`. Composite structure and operation signatures are not modelled.
- UML: only direct `packagedElement` children of the root with `xmi:type` `uml:Component` / `uml:Interface`;
  `interfaceRealization/@supplier` is PROVIDES, `@supplier` of a nested `packagedElement` (the benchmark's
  `uml:Usage`) is REQUIRES.
Deviations from ArDoCo: a reference to an unknown interface is skipped with a warning instead of failing the model,
repeated identical relations are merged, duplicate ids keep the first element.
Other root elements or dialects raise ValueError so the caller falls back to the file-level element.
"""

from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Any
from xml.etree.ElementTree import Element, TreeBuilder

from defusedxml.ElementTree import XMLParser

from sandman_extraction.stub import FILE_KEY

MAX_XML_BYTES = 10 * 1024 * 1024
TEXT_MAX = 500
PCM, UML = "pcm", "uml"

Item = tuple[Element, str, str, list[str], list[str]]


def _local(name: str) -> str:
    return name.rpartition("}")[2]


def _attr(el: Element, name: str) -> str | None:
    return next((v for k, v in el.attrib.items() if _local(k) == name), None)


class _Lines(TreeBuilder):
    """Tree builder that remembers the first and last source line of every element."""

    def __init__(self) -> None:
        super().__init__()
        self.parser: Any = None
        self.first: dict[Element, int] = {}
        self.last: dict[Element, int] = {}

    def start(self, tag: Any, attrs: Any) -> Element:
        el = super().start(tag, attrs)
        self.first[el] = self.parser.parser.CurrentLineNumber
        return el

    def end(self, tag: Any) -> Element:
        el = super().end(tag)
        self.last[el] = self.parser.parser.CurrentLineNumber
        return el


def _parse(content: bytes) -> tuple[Element, _Lines]:
    builder = _Lines()
    builder.parser = parser = XMLParser(target=builder, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    parser.feed(content)
    return parser.close(), builder


def _pcm_items(root: Element) -> Iterator[Item]:
    """(element, kind, PCM type, provided ids, required ids) in document order."""
    for el in root:
        tag = _local(el.tag)
        if tag not in ("components__Repository", "interfaces__Repository"):
            continue
        provided: list[str] = []
        required: list[str] = []
        for role in el:
            role_tag = _local(role.tag)
            if role_tag == "providedRoles_InterfaceProvidingEntity":
                target, prefix = provided, "providedInterface__"
            elif role_tag == "requiredRoles_InterfaceRequiringEntity":
                target, prefix = required, "requiredInterface__"
            else:
                continue
            if ref := next((v for k, v in role.attrib.items() if _local(k).startswith(prefix)), None):
                target.append(ref)
        pcm_type = (_attr(el, "type") or "").rpartition(":")[2]
        yield el, "component" if tag.startswith("components") else "interface", pcm_type, provided, required


def _uml_items(root: Element) -> Iterator[Item]:
    for el in root:
        if _local(el.tag) != "packagedElement":
            continue
        kind = {"uml:Component": "component", "uml:Interface": "interface"}.get(_attr(el, "type") or "")
        if kind is None:
            continue
        provided = [s for c in el if _local(c.tag) == "interfaceRealization" if (s := c.get("supplier"))]
        required = [s for c in el if _local(c.tag) == "packagedElement" if (s := c.get("supplier"))]
        yield el, kind, "", provided, required


def extract(
    path: str, content: bytes, model: str, max_bytes: int = MAX_XML_BYTES, warnings: list[str] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """File element, one element per component and interface, and PROVIDES/REQUIRES relations from components."""
    if len(content) > max_bytes:
        raise ValueError(f"{path} has {len(content)} bytes, limit is {max_bytes}")
    warnings = [] if warnings is None else warnings
    root, lines = _parse(content)
    expected, items = ("Repository", _pcm_items) if model == PCM else ("Model", _uml_items)
    if _local(root.tag) != expected:
        raise ValueError(f"{path}: root element {_local(root.tag)!r} is not a {model} {expected}")
    text = content.decode("utf-8", errors="replace")
    name_attr = "entityName" if model == PCM else "name"
    elements: list[dict[str, Any]] = [{
        "localKey": FILE_KEY, "label": "ModelElement", "kind": "file", "name": PurePosixPath(path).name,
        "qualifiedName": path, "text": text[:TEXT_MAX], "startLine": 1, "endLine": max(len(text.splitlines()), 1),
    }]  # fmt: skip
    found = list(items(root))
    names: dict[str, str] = {}
    for el, *_ in found:
        if (key := _attr(el, "id")) and key not in names:
            names[key] = el.get(name_attr) or key
    components: list[tuple[str, list[str], list[str]]] = []
    seen: set[str] = set()
    for el, kind, pcm_type, provided, required in found:
        key = _attr(el, "id")
        if not key:
            warnings.append(f"{path}:{lines.first[el]}: model element without id skipped")
            continue
        if key in seen:
            warnings.append(f"{path}:{lines.first[el]}: duplicate model id {key} skipped")
            continue
        seen.add(key)
        name = names[key]
        parts = [f"{pcm_type} {name}".strip()]
        if provided:
            parts.append("provides " + ", ".join(names.get(r, r) for r in provided))
        if required:
            parts.append("requires " + ", ".join(names.get(r, r) for r in required))
        elements.append({
            "localKey": key, "label": "ModelElement", "kind": kind, "name": name, "qualifiedName": name,
            "text": "; ".join(parts)[:TEXT_MAX], "startLine": lines.first[el], "endLine": lines.last[el],
            "parent": FILE_KEY,
        })  # fmt: skip
        if kind == "component":
            components.append((key, provided, required))
    interfaces = {e["localKey"] for e in elements if e["kind"] == "interface"}
    relations: list[dict[str, Any]] = []
    done: set[tuple[str, str, str]] = set()
    for key, provided, required in components:
        for rel, refs in (("PROVIDES", provided), ("REQUIRES", required)):
            for ref in refs:
                if ref not in interfaces:
                    warnings.append(f"{path}: relation {rel} from {key} to unknown interface {ref} skipped")
                elif (rel, key, ref) not in done:
                    done.add((rel, key, ref))
                    relations.append({"type": rel, "source": {"path": path, "localKey": key},
                                      "target": {"path": path, "localKey": ref}})  # fmt: skip
    return elements, relations

"""Built-in extractor dispatch by artifact format or file extension (Stage 1 §5.2)."""

from pathlib import PurePosixPath
from typing import Any

from sandman_extraction import decisions, knowledge, models, requirements, text
from sandman_extraction.stub import FILE_KEY, LABEL_BY_KIND, stub_elements

TOOL, TOOL_VERSION = "builtin-extractor", "2"
FORMAT_BY_SUFFIX = {".md": "markdown", ".markdown": "markdown", ".txt": "text", ".csv": "csv",
                    ".repository": "pcm-repository", ".uml": "uml", ".xmi": "uml",
                    ".components": "component-listing"}  # fmt: skip
TEXT_FORMATS = ("markdown", "text")
DOCUMENT_FORMATS = {".pdf": "pdf", ".docx": "docx"}
MODEL_FORMATS = {"pcm-repository": models.PCM, "uml": models.UML}
COMPONENT_LISTING = "component-listing"


def component_listing(path: str, kind: str, content: bytes) -> list[dict[str, Any]]:
    """File element and one component per `Name::Id` line (a line without `::` uses the name as id)."""
    elements = [{"localKey": FILE_KEY, "label": LABEL_BY_KIND[kind], "kind": "file", "name": PurePosixPath(path).name,
                 "qualifiedName": path}]  # fmt: skip
    for line in content.decode("utf-8", errors="replace").split("\n"):
        name, _, key = line.partition("::")
        if name:
            elements.append({"localKey": key or name, "label": "ModelElement", "kind": "component",
                             "name": name, "parent": FILE_KEY})  # fmt: skip
    return elements


def extract(
    path: str,
    kind: str,
    fmt: str | None,
    content: bytes,
    warnings: list[str] | None = None,
    max_xml_bytes: int = models.MAX_XML_BYTES,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(elements, relations); unsupported formats yield the file-level stub elements.

    Skipped input is reported in `warnings`; extractor exceptions propagate so the caller can record the fallback.
    """
    fmt = fmt or FORMAT_BY_SUFFIX.get(PurePosixPath(path).suffix.lower())
    if kind == "requirements":
        return requirements.extract(path, content, fmt, [] if warnings is None else warnings), []
    if fmt == "adr":
        return decisions.extract(path, content, [] if warnings is None else warnings), []
    if fmt == knowledge.FORMAT:
        return knowledge.extract(path, content), []
    if fmt in TEXT_FORMATS:
        elements = text.extract(path, content)
        elements[0]["label"] = LABEL_BY_KIND[kind]
        return elements, []
    if fmt == COMPONENT_LISTING:
        return component_listing(path, kind, content), []
    if fmt in MODEL_FORMATS:
        elements, relations = models.extract(
            path, content, MODEL_FORMATS[fmt], max_xml_bytes, [] if warnings is None else warnings
        )
        elements[0]["label"] = LABEL_BY_KIND[kind]
        return elements, relations
    return stub_elements(path, kind, content), []


def document_format(path: str, fmt: str | None) -> str | None:
    """ "pdf" or "docx" for artifacts converted to Markdown by `documents` before extraction, else None."""
    found = fmt or DOCUMENT_FORMATS.get(PurePosixPath(path).suffix.lower())
    return found if found in DOCUMENT_FORMATS.values() else None


def extract_document(path: str, kind: str, markdown: bytes) -> list[dict[str, Any]]:
    """Elements of a converted document; line numbers refer to the rendering, so callers drop them after use."""
    elements = text.extract(path, markdown, markdown=True)
    elements[0]["label"] = LABEL_BY_KIND[kind]
    return elements


def drop_lines(elements: list[dict[str, Any]]) -> None:
    """Remove startLine/endLine (they refer to the Markdown rendering, not to the PDF/DOCX source)."""
    for e in elements:
        e.pop("startLine", None)
        e.pop("endLine", None)

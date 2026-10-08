"""Built-in extractor dispatch by artifact format or file extension (Stage 1 §5.2)."""

from pathlib import PurePosixPath
from typing import Any

from sandman_extraction import models, requirements, text
from sandman_extraction.stub import LABEL_BY_KIND, stub_elements

TOOL, TOOL_VERSION = "builtin-extractor", "2"
FORMAT_BY_SUFFIX = {".md": "markdown", ".markdown": "markdown", ".txt": "text", ".csv": "csv",
                    ".repository": "pcm-repository", ".uml": "uml", ".xmi": "uml"}  # fmt: skip
TEXT_FORMATS = ("markdown", "text")
DOCUMENT_FORMATS = {".pdf": "pdf", ".docx": "docx"}
MODEL_FORMATS = {"pcm-repository": models.PCM, "uml": models.UML}


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
    if fmt in TEXT_FORMATS:
        elements = text.extract(path, content)
        elements[0]["label"] = LABEL_BY_KIND[kind]
        return elements, []
    if fmt in MODEL_FORMATS:
        elements, relations = models.extract(
            path, content, MODEL_FORMATS[fmt], max_xml_bytes, [] if warnings is None else warnings
        )
        elements[0]["label"] = LABEL_BY_KIND[kind]
        return elements, relations
    return stub_elements(path, kind, content), []


def is_document(path: str, fmt: str | None) -> bool:
    """PDF/DOCX artifacts, converted to Markdown by `documents` before extraction."""
    return (fmt or DOCUMENT_FORMATS.get(PurePosixPath(path).suffix.lower())) in DOCUMENT_FORMATS.values()


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

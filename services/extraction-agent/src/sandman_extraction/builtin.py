"""Built-in extractor dispatch by artifact format or file extension (Stage 1 §5.2)."""

from pathlib import PurePosixPath
from typing import Any

from sandman_extraction import text
from sandman_extraction.stub import LABEL_BY_KIND, stub_elements

TOOL, TOOL_VERSION = "builtin-extractor", "1"
FORMAT_BY_SUFFIX = {".md": "markdown", ".markdown": "markdown", ".txt": "text"}
TEXT_FORMATS = ("markdown", "text")


def extract(path: str, kind: str, fmt: str | None, content: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(elements, relations); unsupported formats and requirements yield the file-level stub elements.

    Extractor exceptions propagate so the caller can record the fallback.
    """
    fmt = fmt or FORMAT_BY_SUFFIX.get(PurePosixPath(path).suffix.lower())
    if fmt in TEXT_FORMATS and kind != "requirements":
        elements = text.extract(path, content)
        elements[0]["label"] = LABEL_BY_KIND[kind]
        return elements, []
    return stub_elements(path, kind, content), []

"""M0 file-level extraction (stub, Stage 2 §4.2): fallback for unsupported formats."""

from pathlib import PurePosixPath
from typing import Any

FILE_KEY = "file"
# requirements files get a TextUnit file element so only the requirements extractor yields Requirement nodes
LABEL_BY_KIND = {"requirements": "TextUnit", "architecture-doc": "TextUnit", "architecture-model": "ModelElement",
                 "code": "CodeUnit", "test": "TestCase", "other": "TextUnit"}  # fmt: skip


def stub_elements(path: str, kind: str, content: bytes) -> list[dict[str, Any]]:
    """One file-level element."""
    text = content.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return [{"localKey": FILE_KEY, "label": LABEL_BY_KIND[kind], "kind": "file", "name": PurePosixPath(path).name,
             "qualifiedName": path, "text": text[:500], "startLine": 1, "endLine": max(len(lines), 1)}]  # fmt: skip

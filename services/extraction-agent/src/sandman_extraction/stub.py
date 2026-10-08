"""M0 file-level extraction (stub, Stage 2 §4.2): fallback for unsupported formats and requirements."""

import re
from pathlib import PurePosixPath
from typing import Any

FILE_KEY = "file"
# requirements files get a TextUnit file element so only the headings yield Requirement nodes
LABEL_BY_KIND = {"requirements": "TextUnit", "architecture-doc": "TextUnit", "architecture-model": "ModelElement",
                 "code": "CodeUnit", "test": "TestCase", "other": "TextUnit"}  # fmt: skip
REQ_HEADING = re.compile(r"^##\s+([A-Za-z][\w.-]*\d)\s+(.+?)\s*$")


def stub_elements(path: str, kind: str, content: bytes) -> list[dict[str, Any]]:
    """One file element; for requirements Markdown one Requirement per '## <ID> <title>' heading."""
    text = content.decode("utf-8", errors="replace")
    lines = text.splitlines()
    elements = [{"localKey": FILE_KEY, "label": LABEL_BY_KIND[kind], "kind": "file", "name": PurePosixPath(path).name,
                 "qualifiedName": path, "text": text[:500], "startLine": 1, "endLine": max(len(lines), 1)}]  # fmt: skip
    if kind == "requirements" and path.lower().endswith(".md"):
        headings = [i for i, line in enumerate(lines) if line.startswith("#")]
        for i in headings:
            if m := REQ_HEADING.match(lines[i]):
                end = next((j for j in headings if j > i), len(lines))
                elements.append({"localKey": m[1], "label": "Requirement", "kind": "requirement", "name": m[2],
                                 "text": "\n".join(lines[i:end]).strip()[:4000], "startLine": i + 1, "endLine": end,
                                 "parent": FILE_KEY})  # fmt: skip
    return elements

"""Benchmark formats of experiments/PROTOCOL.md section 1 (R-027, V-24): gold readers, writers and the KG row mapping.

Links are (first column, second column) string pairs as in the file. Sentence numbers are 1-based in the files.
"""

import csv
import io
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

FORMATS: dict[str, tuple[str, str]] = {
    "ardoco-sad-sam": ("modelElementID", "sentence"),
    "ardoco-sad-code": ("sentenceID", "codeID"),
    "lissa": ("source", "target"),
}
PAGE = 1000  # the Cypher endpoint maximum
LINKS_QUERY = """
MATCH (l:TraceLink {projectId: $projectId, active: true})
WHERE l.status <> 'rejected' AND ($approach IS NULL OR l.approach = $approach)
MATCH (l)-[:SOURCE]->(s:Element)<-[:HAS_ELEMENT]-(:ArtifactVersion)<-[:CURRENT_VERSION]-(sa:Artifact),
      (l)-[:TARGET]->(t:Element)<-[:HAS_ELEMENT]-(:ArtifactVersion)<-[:CURRENT_VERSION]-(ta:Artifact)
RETURN s.key AS sourceKey, s.sentenceIndex AS sourceIndex, sa.kind AS sourceKind, sa.path AS sourcePath,
       t.key AS targetKey, ta.kind AS targetKind, ta.path AS targetPath
ORDER BY l.id SKIP $skip LIMIT $page
"""
Link = tuple[str, str]


def _local_key(key: str) -> str:
    return key.partition("#")[2]


def _lissa_id(key: str, path: str) -> str:
    name, local = PurePosixPath(path).name, _local_key(key)
    return name if local == "file" else f"{name}#{local}"


def _sort_key(fmt: str, link: Link) -> tuple[Any, ...]:
    a, b = link
    if fmt == "ardoco-sad-sam":
        return (a, int(b))
    if fmt == "ardoco-sad-code":
        return (int(a), b)
    return (a, b)


def write_links(fmt: str, links: Iterable[Link]) -> str:
    """CSV text with the header of the format, rows sorted, no duplicates."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(FORMATS[fmt])
    ordered = sorted(set(links), key=lambda link: _sort_key(fmt, link))
    writer.writerows(ordered)
    return buf.getvalue()


def read_links(fmt: str, text: str) -> set[Link]:
    """Gold standard or exported links; the header row is skipped, extra columns are ignored."""
    rows = [r for r in csv.reader(io.StringIO(text)) if r]
    return {(r[0].strip(), r[1].strip()) for r in rows[1:] if len(r) >= 2}


def read_file(fmt: str, path: Path) -> set[Link]:
    return read_links(fmt, path.read_text(encoding="utf-8"))


def rows_to_links(fmt: str, rows: Iterable[Mapping[str, Any]], code_root: str = "") -> set[Link]:
    """Maps KG link rows (LINKS_QUERY columns) to benchmark links. Rows that do not fit the format are skipped."""
    out: set[Link] = set()
    for r in rows:
        if fmt == "ardoco-sad-sam":
            if (
                r["sourceKind"] == "architecture-doc"
                and r["targetKind"] == "architecture-model"
                and r["sourceIndex"] is not None
            ):
                out.add((_local_key(r["targetKey"]), str(r["sourceIndex"] + 1)))
        elif fmt == "ardoco-sad-code":
            if r["sourceKind"] == "architecture-doc" and r["targetKind"] == "code" and r["sourceIndex"] is not None:
                path = r["targetPath"]
                if code_root and path.startswith(code_root):
                    path = path[len(code_root) :].lstrip("/")
                out.add((str(r["sourceIndex"] + 1), path))
        elif fmt == "lissa":
            out.add((_lissa_id(r["sourceKey"], r["sourcePath"]), _lissa_id(r["targetKey"], r["targetPath"])))
        else:
            raise ValueError(f"unknown format {fmt!r}")
    return out


def fetch_rows(
    cypher: Callable[[dict[str, Any]], dict[str, Any]], project: str, approach: str | None
) -> list[dict[str, Any]]:
    """Pages through LINKS_QUERY. `cypher` posts a QueryResult request body and returns the QueryResult."""
    rows: list[dict[str, Any]] = []
    while True:
        body = {
            "projectId": project,
            "cypher": LINKS_QUERY,
            "params": {"approach": approach, "skip": len(rows), "page": PAGE},
            "limit": PAGE,
        }
        result = cypher(body)
        columns = result["columns"]
        page = [dict(zip(columns, row, strict=True)) for row in result["rows"]]
        rows += page
        if len(page) < PAGE:
            return rows

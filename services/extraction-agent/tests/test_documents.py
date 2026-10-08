"""PDF/DOCX extraction (T1.4) against a fake document-converter; the real conversion is tested in the converter."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sandman_extraction import builtin, documents
from sandman_extraction.documents import ConversionError
from sandman_extraction.text import extract

TESTS = Path(__file__).parent
RENDERED = TESTS / "fixtures" / "rendered"
PDF, DOCX = "docs/teastore.pdf", "docs/structure.docx"
PDF_BLOB, DOCX_BLOB = b"%PDF-1.4 teastore", b"PK\x03\x04 structure"
ELEMENTS = (
    "MATCH (:Artifact {projectId: $p, path: $path})-[:CURRENT_VERSION]->(:ArtifactVersion)-[:HAS_ELEMENT]->(e) "
    "RETURN e.kind AS kind, e.sentenceIndex AS i, e.startLine AS start, e.endLine AS end ORDER BY e.sentenceIndex"
)


def client(handler: Any) -> httpx.AsyncClient | None:
    return documents.converter_client("http://c:8000", "tok", 5, httpx.MockTransport(handler))


async def test_posts_path_and_format_with_the_service_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"markdown": "# T\n"})

    assert await documents.to_markdown(client(handler), Path("p/abc"), "pdf") == "# T\n"
    assert json.loads(seen[0].content) == {"path": "p/abc", "format": "pdf"}
    assert seen[0].headers["authorization"] == "Bearer tok" and seen[0].url.path == "/v1/convert"


async def test_unconfigured_converter_is_a_conversion_error() -> None:
    with pytest.raises(ConversionError, match="not configured"):
        await documents.to_markdown(documents.converter_client(None, None, 5), Path("/x"), "pdf")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(422, json={"detail": "limit is 5"}),
        httpx.Response(500, text="<html>boom</html>"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"other": 1}),
    ],
)
async def test_error_responses_raise(response: httpx.Response) -> None:
    with pytest.raises(ConversionError):
        await documents.to_markdown(client(lambda request: response), Path("/x"), "pdf")


async def test_error_detail_is_bounded_and_network_errors_are_wrapped() -> None:
    long = httpx.Response(422, json={"detail": "x" * 5000})
    with pytest.raises(ConversionError) as e:
        await documents.to_markdown(client(lambda request: long), Path("/x"), "pdf")
    assert len(str(e.value)) <= documents.ERROR_MAX

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ConversionError, match="unreachable: ConnectError"):
        await documents.to_markdown(client(down), Path("/x"), "pdf")


def test_document_format() -> None:
    assert builtin.document_format("a/b.PDF", None) == "pdf"
    assert builtin.document_format("a/b.docx", None) == "docx"
    assert builtin.document_format("a/b.bin", "pdf") == "pdf"
    assert builtin.document_format("a/b.md", None) is None


def test_rendered_docx_headings_equal_markdown_sections() -> None:
    markdown = (
        "# Architecture\n\nThe system has two components. The first stores media.\n\n"
        "## Components\n\n- The Cache keeps hot items.\n- The Store persists all items.\n\n"
        "## Interfaces\n\nClients use the interface. It is stable.\n"
    )
    ref = extract("structure.md", markdown.encode())
    got = builtin.extract_document("structure.docx", "architecture-doc", (RENDERED / "structure.md").read_bytes())
    sections = lambda els: [(e["localKey"], e["name"], e["parent"]) for e in els if e["kind"] == "section"]  # noqa: E731
    assert sections(got) == sections(ref)


async def run_skill(env: Any, files: dict[str, bytes]) -> Any:
    await env.put_project()
    resp = await env.ingest(files)
    assert resp.status_code == 201, resp.text
    task = await env.extract(resp.json()["createdVersionIds"])
    assert task.state == "TASK_STATE_COMPLETED", task
    return task.result


async def test_pdf_and_docx_through_the_skill(env: Any) -> None:
    result = await run_skill(env, {PDF: PDF_BLOB, DOCX: DOCX_BLOB})
    assert result["status"] == "completed", result
    for path, sentences in ((PDF, 43), (DOCX, 6)):
        rows = await env.svc.kg.read(ELEMENTS, p=env.project, path=path)
        found = [r for r in rows if r["kind"] == "sentence"]
        assert [r["i"] for r in found] == list(range(sentences))
        assert all(r["start"] is None and r["end"] is None for r in rows)
    formats = sorted((c["format"], auth) for c, auth in env.converter.calls)
    assert formats == [("docx", "Bearer ea-token"), ("pdf", "Bearer ea-token")]
    assert all(not Path(c["path"]).is_absolute() and ".." not in c["path"] for c, _ in env.converter.calls)


async def test_converter_rejection_falls_back_and_others_extract(env: Any) -> None:
    env.converter.status = 422
    result = await run_skill(env, env.mini() | {PDF: PDF_BLOB})
    assert result["status"] == "partial"
    assert any(f"built-in extractor failed for {PDF}" in w and "converter says no" in w for w in result["warnings"])
    assert await env.svc.kg.read(ELEMENTS, p=env.project, path=PDF) == [
        {"kind": "file", "i": None, "start": None, "end": None}
    ]
    arch = await env.svc.kg.read(ELEMENTS, p=env.project, path="docs/architecture.md")
    assert sum(r["kind"] == "sentence" for r in arch) == 2


async def test_converter_down_falls_back(env: Any) -> None:
    env.converter.error = httpx.ConnectError("refused")
    result = await run_skill(env, env.mini() | {DOCX: DOCX_BLOB})
    assert result["status"] == "partial"
    assert any(f"built-in extractor failed for {DOCX}" in w and "unreachable" in w for w in result["warnings"])
    assert [r["kind"] for r in await env.svc.kg.read(ELEMENTS, p=env.project, path=DOCX)] == ["file"]

"""PDF/DOCX extraction (T1.4): subprocess wrapper with fake converters; Docling tests carry the `docling` marker."""

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from sandman_extraction import builtin, documents
from sandman_extraction.documents import ConversionError, Limits
from sandman_extraction.text import extract, render_sentences

TESTS = Path(__file__).parent
DOCS = TESTS / "fixtures" / "documents"
GOLDEN = TESTS / "golden" / "documents"
TEASTORE = TESTS / "fixtures" / "benchmark" / "teastore" / "teastore.txt"
CASES = ["teastore.pdf", "teastore.docx", "structure.docx"]
docling = pytest.mark.docling
needs_models = pytest.mark.skipif(not documents.models_dir().is_dir(), reason="Docling models not prefetched")


def fake(code: str) -> list[str]:
    return [sys.executable, "-c", code]


@pytest.fixture
def blob(tmp_path: Path) -> Path:
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    return path


async def test_success(blob: Path) -> None:
    out = await documents.to_markdown(blob, command=fake("print('# T\\n\\nHello.')"))
    assert out == "# T\n\nHello.\n"


async def test_garbage_output_is_decoded_lossily(blob: Path) -> None:
    out = await documents.to_markdown(blob, command=fake("import sys; sys.stdout.buffer.write(b'\\xff\\xfeabc')"))
    assert out.endswith("abc")


async def test_non_zero_exit_reports_last_stderr_line(blob: Path) -> None:
    code = "import sys; sys.stderr.write('noise' * 1000 + '\\nfinal\\x00'); sys.exit(3)"
    with pytest.raises(ConversionError, match="exited with 3") as e:
        await documents.to_markdown(blob, command=fake(code))
    assert len(str(e.value)) < 400 and "\x00" not in str(e.value) and str(e.value).endswith("final ")


async def test_crash_by_signal(blob: Path) -> None:
    with pytest.raises(ConversionError, match="exited with -"):
        await documents.to_markdown(blob, command=fake("import os, signal; os.kill(os.getpid(), signal.SIGSEGV)"))


async def test_timeout_kills_the_converter(blob: Path) -> None:
    with pytest.raises(ConversionError, match="timed out"):
        await documents.to_markdown(blob, Limits(timeout_seconds=0.5), command=fake("import time; time.sleep(60)"))


async def test_oversized_file_is_not_converted(blob: Path) -> None:
    with pytest.raises(ConversionError, match="limit is 5"):
        await documents.to_markdown(blob, Limits(max_file_bytes=5), command=["/nonexistent"])


async def test_unbounded_output_is_cut(blob: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(documents, "MAX_MARKDOWN_BYTES", 1000)
    with pytest.raises(ConversionError, match="exceeds 1000"):
        await documents.to_markdown(blob, command=fake("print('a' * 10_000_000)"))


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS is only enforced on Linux")
async def test_memory_limit(blob: Path) -> None:
    with pytest.raises(ConversionError, match="exited with"):
        await documents.to_markdown(
            blob, Limits(memory_bytes=512 * 1024**2), command=fake("x = bytearray(1024**3); print('ok')")
        )


def test_limits_from_config() -> None:
    assert Limits.from_config({"timeoutSeconds": 5, "maxPages": 3, "other": 1}) == Limits(5, max_pages=3)


@docling
@needs_models
@pytest.mark.parametrize("case", CASES)
async def test_golden_documents(case: str) -> None:
    markdown = await documents.to_markdown(DOCS / case)
    elements = builtin.extract_document(case, "architecture-doc", markdown.encode())
    builtin.drop_lines(elements)
    for e in elements:
        if e["kind"] != "sentence":
            e.pop("text")  # the rendering of PDF layout differs between platforms
    actual = json.dumps({"elements": elements}, indent=1, sort_keys=True, ensure_ascii=False)
    golden = GOLDEN / f"{case}.json"
    if os.environ.get("SANDMAN_UPDATE_GOLDEN"):
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(actual + "\n", encoding="utf-8")
    assert golden.read_text(encoding="utf-8") == actual + "\n"
    assert not any("startLine" in e or "endLine" in e for e in elements)


@docling
@needs_models
@pytest.mark.parametrize("case", ["teastore.pdf", "teastore.docx"])
async def test_sentence_keys_match_text_extraction(case: str, capsys: pytest.CaptureFixture[str]) -> None:
    ref = extract("teastore.txt", TEASTORE.read_bytes())
    markdown = await documents.to_markdown(DOCS / case)
    got = builtin.extract_document(case, "architecture-doc", markdown.encode())
    keys = {e["localKey"] for e in got if e["kind"] == "sentence"}
    gold = {e["localKey"] for e in ref if e["kind"] == "sentence"}
    with capsys.disabled():
        print(f"\nsentence alignment {case}: {len(keys & gold)}/{len(gold)}")
    assert keys == gold
    assert render_sentences(got) == render_sentences(ref)


@docling
@needs_models
async def test_docx_headings_equal_markdown_sections() -> None:
    markdown = (
        "# Architecture\n\nThe system has two components. The first stores media.\n\n"
        "## Components\n\n- The Cache keeps hot items.\n- The Store persists all items.\n\n"
        "## Interfaces\n\nClients use the interface. It is stable.\n"
    )
    ref = extract("structure.md", markdown.encode())
    rendering = await documents.to_markdown(DOCS / "structure.docx")
    got = builtin.extract_document("structure.docx", "architecture-doc", rendering.encode())
    sections = lambda els: [(e["localKey"], e["name"], e["parent"]) for e in els if e["kind"] == "section"]  # noqa: E731
    assert sections(got) == sections(ref)


def test_no_orphan_document_goldens() -> None:
    assert {p.name for p in GOLDEN.glob("*.json")} == {f"{c}.json" for c in CASES}


PDF, DOCX = "docs/teastore.pdf", "docs/structure.docx"
ELEMENTS = (
    "MATCH (:Artifact {projectId: $p, path: $path})-[:CURRENT_VERSION]->(:ArtifactVersion)-[:HAS_ELEMENT]->(e) "
    "RETURN e.kind AS kind, e.sentenceIndex AS i, e.startLine AS start, e.endLine AS end ORDER BY e.sentenceIndex"
)


async def run_skill(env: Any, files: dict[str, bytes]) -> Any:
    await env.put_project()
    resp = await env.ingest(files)
    assert resp.status_code == 201, resp.text
    task = await env.extract(resp.json()["createdVersionIds"])
    assert task.state == "TASK_STATE_COMPLETED", task
    return task.result


async def test_oversized_document_falls_back_and_others_extract(env: Any) -> None:
    env.svc.config["builtin"] = {"docling": {"maxFileBytes": 100}}
    result = await run_skill(env, env.mini() | {PDF: (DOCS / "teastore.pdf").read_bytes()})
    assert result["status"] == "partial"
    assert any(f"built-in extractor failed for {PDF}" in w and "limit is 100" in w for w in result["warnings"])
    assert await env.svc.kg.read(ELEMENTS, p=env.project, path=PDF) == [
        {"kind": "file", "i": None, "start": None, "end": None}
    ]
    arch = await env.svc.kg.read(ELEMENTS, p=env.project, path="docs/architecture.md")
    assert sum(r["kind"] == "sentence" for r in arch) == 2


@docling
@needs_models
async def test_pdf_and_docx_through_the_skill(env: Any) -> None:
    result = await run_skill(
        env, {PDF: (DOCS / "teastore.pdf").read_bytes(), DOCX: (DOCS / "structure.docx").read_bytes()}
    )
    assert result["status"] == "completed", result
    for path, sentences in ((PDF, 43), (DOCX, 6)):
        rows = await env.svc.kg.read(ELEMENTS, p=env.project, path=path)
        found = [r for r in rows if r["kind"] == "sentence"]
        assert [r["i"] for r in found] == list(range(sentences))
        assert all(r["start"] is None and r["end"] is None for r in rows)


@docling
@needs_models
async def test_corrupt_pdf_falls_back(env: Any) -> None:
    result = await run_skill(env, env.mini() | {PDF: b"%PDF-1.4 not a pdf\x00\x01"})
    assert result["status"] == "partial"
    assert any(f"built-in extractor failed for {PDF}" in w for w in result["warnings"])
    assert [r["kind"] for r in await env.svc.kg.read(ELEMENTS, p=env.project, path=PDF)] == ["file"]

"""PDF/DOCX conversion (T1.4): subprocess wrapper with fake converters; Docling tests carry the `docling` marker."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from sandman_document_converter import convert as documents
from sandman_document_converter.convert import ConversionError, Limits
from sandman_extraction import builtin
from sandman_extraction.text import extract, render_sentences

TESTS = Path(__file__).parent
EXTRACTION_TESTS = TESTS.parents[1] / "extraction-agent" / "tests"
DOCS = TESTS / "fixtures" / "documents"
GOLDEN = TESTS / "golden" / "documents"
TEASTORE = EXTRACTION_TESTS / "fixtures" / "benchmark" / "teastore" / "teastore.txt"
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
    code = "from sandman_document_converter.worker import apply_limits as a; a(60, 512 * 1024**2); bytearray(1024**3)"
    with pytest.raises(ConversionError, match="exited with"):
        await documents.to_markdown(blob, command=fake(code))


async def test_converter_env_has_no_secrets(blob: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDMAN_SERVICE_TOKEN_FILE", "/run/secrets/x")
    monkeypatch.setenv("HF_TOKEN", "hf_secret")
    monkeypatch.setenv("SANDMAN_MODELS_DIR", "/models")
    out = await documents.to_markdown(blob, command=fake("import os; print(sorted(os.environ))"))
    assert "SANDMAN_SERVICE_TOKEN_FILE" not in out and "HF_TOKEN" not in out
    assert "SANDMAN_MODELS_DIR" in out and "HF_HUB_OFFLINE" in out


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
async def test_timeout_kills_grandchildren(blob: Path, tmp_path: Path) -> None:
    pidfile = tmp_path / "pid"
    code = (
        "import subprocess, sys, time; "
        f"p = subprocess.Popen(['sleep', '60']); open({str(pidfile)!r}, 'w').write(str(p.pid)); time.sleep(60)"
    )
    with pytest.raises(ConversionError, match="timed out"):
        await documents.to_markdown(blob, Limits(timeout_seconds=1.5), command=fake(code))
    pid = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.1)
    pytest.fail("grandchild survived the timeout")


def pdf_pages(n: int) -> bytes:
    """Minimal PDF with n text pages."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{' '.join(f'{4 + 2 * i} 0 R' for i in range(n))}] /Count {n} >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]  # fmt: skip
    for i in range(n):
        text = f"BT /F1 12 Tf 72 720 Td (Page {i + 1} has one sentence.) Tj ET"
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {5 + 2 * i} 0 R "
                    "/Resources << /Font << /F1 3 0 R >> >> >>")  # fmt: skip
        objs.append(f"<< /Length {len(text)} >>\nstream\n{text}\nendstream")
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


@docling
@needs_models
async def test_page_limit_with_real_multipage_pdf(tmp_path: Path) -> None:
    path = tmp_path / "three.pdf"
    path.write_bytes(pdf_pages(3))
    assert "Page 3" in await documents.to_markdown(path, Limits(max_pages=3))
    with pytest.raises(ConversionError, match="exited with 1"):
        await documents.to_markdown(path, Limits(max_pages=2))


def test_prefetch_verify_rejects_tampered_and_extra_files(tmp_path: Path) -> None:
    from sandman_document_converter import prefetch

    (tmp_path / "a").write_bytes(b"x")
    good = {"a": prefetch.sha256(tmp_path / "a")}
    prefetch.verify(tmp_path, good)
    with pytest.raises(SystemExit, match="differs from pinned"):
        prefetch.verify(tmp_path, {"a": "0" * 64})
    (tmp_path / "extra").write_bytes(b"y")
    with pytest.raises(SystemExit, match="differ from pinned"):
        prefetch.verify(tmp_path, good)


def test_limits_from_env() -> None:
    env = {"SANDMAN_CONVERTER_TIMEOUT_SECONDS": "5", "SANDMAN_CONVERTER_MAX_PAGES": "3", "OTHER": "1"}
    assert Limits.from_env(env) == Limits(5, max_pages=3)
    assert Limits.from_env({}) == Limits()


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

"""PDF/DOCX to Markdown with Docling in a resource-limited subprocess (T1.4, R-029).

`python -m sandman_extraction.documents [--max-pages N] <path>` prints the Markdown rendering; `to_markdown` runs it
with timeout, CPU/address-space limits and bounded output. Docling is imported only in the subprocess, offline,
without OCR; its models are read from `SANDMAN_MODELS_DIR` (see `prefetch`).
"""

import argparse
import asyncio
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODELS_ENV = "SANDMAN_MODELS_DIR"
MAX_MARKDOWN_BYTES = 5 * 1024 * 1024
ERROR_MAX = 300
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


class ConversionError(Exception):
    """Conversion failed, timed out or exceeded a limit."""


@dataclass(frozen=True)
class Limits:
    timeout_seconds: float = 120
    memory_bytes: int = 2 * 1024**3
    max_pages: int = 300
    max_file_bytes: int = 50 * 1024**2

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "Limits":
        names = {"timeoutSeconds": "timeout_seconds", "memoryBytes": "memory_bytes", "maxPages": "max_pages",
                 "maxFileBytes": "max_file_bytes"}  # fmt: skip
        return cls(**{names[k]: v for k, v in config.items() if k in names})


def models_dir() -> Path:
    return Path(os.environ.get(MODELS_ENV) or Path.home() / ".cache" / "sandman-models")


def _limiter(limits: Limits) -> Callable[[], None]:
    def apply() -> None:
        import resource

        cpu = int(limits.timeout_seconds * 2)  # CPU time of all threads; wall-clock is bounded by the timeout
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        if sys.platform == "linux":  # RLIMIT_AS is not enforced elsewhere
            resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))

    return apply


async def _read(stream: asyncio.StreamReader | None, cap: int) -> bytes:
    """Drain `stream`, keeping at most cap+1 bytes."""
    buf = bytearray()
    while stream and (chunk := await stream.read(65536)):
        buf += chunk[: max(cap + 1 - len(buf), 0)]
    return bytes(buf)


async def to_markdown(path: Path, limits: Limits | None = None, command: list[str] | None = None) -> str:
    """Markdown rendering of a PDF/DOCX; raises ConversionError on crash, timeout, limit or oversized input.

    `command` replaces the Docling subprocess command (the path is appended); for tests.
    """
    limits = limits or Limits()
    size = path.stat().st_size
    if size > limits.max_file_bytes:
        raise ConversionError(f"{path.name} has {size} bytes, limit is {limits.max_file_bytes}")
    command = command or [sys.executable, "-m", "sandman_extraction.documents", "--max-pages", str(limits.max_pages)]
    proc = await asyncio.create_subprocess_exec(
        *command, str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL, preexec_fn=_limiter(limits),
        env={**os.environ, "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"},
    )  # fmt: skip
    try:
        out, err, _ = await asyncio.wait_for(
            asyncio.gather(_read(proc.stdout, MAX_MARKDOWN_BYTES), _read(proc.stderr, 2000), proc.wait()),
            limits.timeout_seconds,
        )
    except TimeoutError:
        raise ConversionError(f"conversion of {path.name} timed out after {limits.timeout_seconds:g}s") from None
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    if proc.returncode != 0:
        detail = _CONTROL.sub(" ", err.decode("utf-8", errors="replace").strip().rpartition("\n")[2])[:ERROR_MAX]
        raise ConversionError(f"converter exited with {proc.returncode}: {detail}")
    if len(out) > MAX_MARKDOWN_BYTES:
        raise ConversionError(f"rendering of {path.name} exceeds {MAX_MARKDOWN_BYTES} bytes")
    return out.decode("utf-8", errors="replace")


def converter() -> Any:
    """Docling converter for PDF and DOCX: no OCR, no enrichment, models from the local models dir only."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions(artifacts_path=models_dir(), do_ocr=False, enable_remote_services=False)
    return DocumentConverter(
        allowed_formats=[InputFormat.PDF, InputFormat.DOCX],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
    )


def convert(path: Path, max_pages: int = Limits.max_pages) -> str:
    return str(converter().convert(path, max_num_pages=max_pages).document.export_to_markdown())


def main(argv: list[str] | None = None) -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    parser = argparse.ArgumentParser(prog="sandman_extraction.documents")
    parser.add_argument("--max-pages", type=int, default=Limits.max_pages)
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    stdout, sys.stdout = sys.stdout, sys.stderr  # libraries must not pollute the rendering
    stdout.buffer.write(convert(args.path, args.max_pages).encode("utf-8"))


if __name__ == "__main__":
    main()

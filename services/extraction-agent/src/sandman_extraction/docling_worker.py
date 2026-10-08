"""Docling converter process: `python -m sandman_extraction.docling_worker [options] <path>` prints Markdown (T1.4).

Resource limits are applied first thing in `main`, before Docling and torch are imported. Docling runs offline without
OCR or enrichment; models come from `SANDMAN_MODELS_DIR` only. Only this module imports Docling.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any

MODELS_ENV = "SANDMAN_MODELS_DIR"
DEFAULT_MAX_PAGES = 300


def models_dir() -> Path:
    return Path(os.environ.get(MODELS_ENV) or Path.home() / ".cache" / "sandman-models")


def apply_limits(cpu_seconds: int, memory_bytes: int) -> None:
    """RLIMIT_CPU (all threads) and, on Linux only (not enforced elsewhere), RLIMIT_AS."""
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))


def converter() -> Any:
    """Docling converter for PDF and DOCX: no OCR, no enrichment, no remote services."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions(artifacts_path=models_dir(), do_ocr=False, enable_remote_services=False)
    return DocumentConverter(
        allowed_formats=[InputFormat.PDF, InputFormat.DOCX],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
    )


def convert(path: Path, max_pages: int = DEFAULT_MAX_PAGES) -> str:
    return str(converter().convert(path, max_num_pages=max_pages).document.export_to_markdown())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sandman_extraction.docling_worker")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--cpu-seconds", type=int, default=240)
    parser.add_argument("--memory-bytes", type=int, default=8 * 1024**3)
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    apply_limits(args.cpu_seconds, args.memory_bytes)
    os.environ["HF_HUB_OFFLINE"] = "1"
    stdout, sys.stdout = sys.stdout, sys.stderr  # libraries must not pollute the rendering
    stdout.buffer.write(convert(args.path, args.max_pages).encode("utf-8"))


if __name__ == "__main__":
    main()

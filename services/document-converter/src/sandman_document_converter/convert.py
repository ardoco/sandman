"""PDF/DOCX to Markdown with Docling in a resource-limited subprocess (T1.4, R-029).

`to_markdown` runs `worker` (the only module importing Docling) in its own session with a minimal environment,
a wall-clock timeout, CPU/address-space limits set by the worker itself, bounded output, and kills the whole process
group on any exit. Docling runs offline (HF offline mode, models from `SANDMAN_MODELS_DIR`) and without OCR.
Limits: OS-level network isolation is NOT provided here; a compromised converter could still open sockets unless the
container is run without network (the deployment's job: the compose network is internal). Offline mode only stops the
Hugging Face client.
"""

import asyncio
import contextlib
import os
import re
import signal
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sandman_document_converter.worker import DEFAULT_MAX_PAGES, MODELS_ENV, models_dir

__all__ = ["ConversionError", "Limits", "models_dir", "to_markdown"]

MAX_MARKDOWN_BYTES = 5 * 1024 * 1024
ERROR_MAX = 300
ENV_PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", MODELS_ENV, "HF_HOME", "HF_HUB_CACHE",
                   "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")  # fmt: skip
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


class ConversionError(Exception):
    """Conversion failed, timed out or exceeded a limit."""


@dataclass(frozen=True)
class Limits:
    timeout_seconds: float = 120
    memory_bytes: int = (
        8 * 1024**3
    )  # address space: torch and docling-parse reserve ~6 GiB; RAM is bounded by the container
    max_pages: int = DEFAULT_MAX_PAGES
    max_file_bytes: int = 50 * 1024**2

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Limits":
        env = os.environ if env is None else env
        names = {"SANDMAN_CONVERTER_TIMEOUT_SECONDS": ("timeout_seconds", float),
                 "SANDMAN_CONVERTER_MEMORY_BYTES": ("memory_bytes", int),
                 "SANDMAN_CONVERTER_MAX_PAGES": ("max_pages", int),
                 "SANDMAN_CONVERTER_MAX_FILE_BYTES": ("max_file_bytes", int)}  # fmt: skip
        return cls(**{field: cast(env[name]) for name, (field, cast) in names.items() if env.get(name)})


def worker_env() -> dict[str, str]:
    """Minimal environment of the converter: no service secrets or tokens."""
    env = {k: os.environ[k] for k in ENV_PASSTHROUGH if k in os.environ}
    return env | {"HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}


async def _read(stream: asyncio.StreamReader | None, cap: int, tail: bool = False) -> bytes:
    """Drain `stream`, keeping the first cap+1 bytes (or the last cap bytes with `tail`)."""
    buf = bytearray()
    while stream and (chunk := await stream.read(65536)):
        buf += chunk
        buf = buf[-cap:] if tail else buf[: cap + 1]
    return bytes(buf)


async def to_markdown(path: Path, limits: Limits | None = None, command: list[str] | None = None) -> str:
    """Markdown rendering of a PDF/DOCX; raises ConversionError on crash, timeout, limit or oversized input.

    `command` replaces the worker command (the path is appended); for tests.
    """
    limits = limits or Limits()
    size = path.stat().st_size
    if size > limits.max_file_bytes:
        raise ConversionError(f"{path.name} has {size} bytes, limit is {limits.max_file_bytes}")
    command = command or [
        sys.executable, "-m", "sandman_document_converter.worker", "--max-pages", str(limits.max_pages),
        "--cpu-seconds", str(int(limits.timeout_seconds * 2)),  # CPU time sums all threads; wall clock is the timeout
        "--memory-bytes", str(limits.memory_bytes),
    ]  # fmt: skip
    proc = await asyncio.create_subprocess_exec(
        *command, str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL, env=worker_env(), start_new_session=True,
    )  # fmt: skip
    try:
        out, err, _ = await asyncio.wait_for(
            asyncio.gather(_read(proc.stdout, MAX_MARKDOWN_BYTES), _read(proc.stderr, 2000, tail=True), proc.wait()),
            limits.timeout_seconds,
        )
    except TimeoutError:
        raise ConversionError(f"conversion of {path.name} timed out after {limits.timeout_seconds:g}s") from None
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)  # also grandchildren; the group id outlives the leader
        await proc.wait()
    if proc.returncode != 0:
        detail = _CONTROL.sub(" ", err.decode("utf-8", errors="replace").strip().rpartition("\n")[2])[:ERROR_MAX]
        raise ConversionError(f"converter exited with {proc.returncode}: {detail}")
    if len(out) > MAX_MARKDOWN_BYTES:
        raise ConversionError(f"rendering of {path.name} exceeds {MAX_MARKDOWN_BYTES} bytes")
    return out.decode("utf-8", errors="replace")

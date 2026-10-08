"""PDF/DOCX to Markdown through the document-converter service (T1.4, R-029).

The conversion runs in its own container (Docling, resource limits, no network); the artifact store is mounted
read-only there at the same path. Any failure raises `ConversionError`, which callers turn into the file-level fallback.
"""

from pathlib import Path

import httpx

ERROR_MAX = 300


class ConversionError(Exception):
    """Conversion failed, timed out, exceeded a limit or the converter is unavailable."""


def converter_client(
    url: str | None, token: str | None, timeout: float, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient | None:
    """Client of the document-converter; None if `url` is not configured."""
    if not url:
        return None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(
        base_url=url, headers=headers, transport=transport, timeout=httpx.Timeout(timeout, connect=10.0)
    )


async def to_markdown(client: httpx.AsyncClient | None, path: Path, fmt: str) -> str:
    """Markdown of the stored PDF/DOCX at `path` (relative to the store root); ConversionError on any failure."""
    if client is None:
        raise ConversionError("document converter is not configured (SANDMAN_CONVERTER_URL)")
    try:
        resp = await client.post("/v1/convert", json={"path": str(path), "format": fmt})
    except httpx.HTTPError as e:
        raise ConversionError(f"document converter unreachable: {type(e).__name__}") from None
    if resp.status_code != 200:
        try:
            detail = str(resp.json()["detail"])
        except (ValueError, KeyError, TypeError):
            detail = ""
        raise ConversionError(f"document converter returned {resp.status_code}: {detail}"[:ERROR_MAX])
    try:
        return str(resp.json()["markdown"])
    except (ValueError, KeyError, TypeError):
        raise ConversionError("document converter returned an invalid response") from None

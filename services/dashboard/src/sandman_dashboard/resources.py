"""Container resource usage from the cAdvisor REST API (compose profile `resources`).

Containers are mapped to Compose services by the label `com.docker.compose.service`; only the Compose project of the
cAdvisor container itself is shown.
"""

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

API = "/api/v1.3/docker/"
SERVICE_LABEL = "com.docker.compose.service"
PROJECT_LABEL = "com.docker.compose.project"
UNLIMITED = 2**60
WARN = 0.8
CACHE = 5.0
TIMEOUT = 5.0


@dataclass(frozen=True)
class Usage:
    service: str
    memory: float | None
    limit: float | None
    cpu: float | None

    @property
    def ratio(self) -> float | None:
        return self.memory / self.limit if self.memory is not None and self.limit else None

    @property
    def warn(self) -> bool:
        return (self.ratio or 0.0) >= WARN


def _seconds(stamp: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(stamp)).timestamp()
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def cpu_rate(stats: list[Any]) -> float | None:
    """Cores used between the last two samples (cumulative `cpu.usage.total` in nanoseconds)."""
    if len(stats) < 2:
        return None
    first, last = stats[-2], stats[-1]
    try:
        used = _number(last["cpu"]["usage"]["total"]) or 0.0
        before = _number(first["cpu"]["usage"]["total"]) or 0.0
        seconds = (_seconds(last["timestamp"]) or 0.0) - (_seconds(first["timestamp"]) or 0.0)
    except (KeyError, TypeError):
        return None
    return (used - before) / 1e9 / seconds if seconds > 0 and used >= before else None


def parse(doc: Any) -> list[Usage]:
    """Usage per Compose service of the cAdvisor document (container path -> info); one entry per service."""
    if not isinstance(doc, dict):
        return []
    containers = [c for c in doc.values() if isinstance(c, dict)]
    labels = [(c, (c.get("spec") or {}).get("labels") or {}) for c in containers]
    project = next((lab.get(PROJECT_LABEL) for _, lab in labels if lab.get(SERVICE_LABEL) == "cadvisor"), None)
    found: dict[str, Usage] = {}
    for container, lab in labels:
        service = lab.get(SERVICE_LABEL)
        if not isinstance(service, str) or (project is not None and lab.get(PROJECT_LABEL) != project):
            continue
        stats = container.get("stats") or []
        memory = _number(((stats[-1].get("memory") or {}).get("working_set")) if stats else None)
        limit = _number(((container.get("spec") or {}).get("memory") or {}).get("limit"))
        found[service] = Usage(service, memory, limit if limit and limit < UNLIMITED else None, cpu_rate(stats))
    return sorted(found.values(), key=lambda u: u.service)


class Cadvisor:
    """Fetches and caches the usage table; `usage()` is None when cAdvisor is not reachable (profile not active)."""

    def __init__(self, url: str | None, client: httpx.AsyncClient) -> None:
        self.url, self.client = (url or "").rstrip("/"), client
        self._at = float("-inf")
        self._value: list[Usage] | None = None

    async def usage(self) -> list[Usage] | None:
        if not self.url:
            return None
        if time.monotonic() - self._at < CACHE:
            return self._value
        try:
            resp = await self.client.post(f"{self.url}{API}", json={"num_stats": 2}, timeout=TIMEOUT)
            resp.raise_for_status()
            self._value = parse(resp.json())
        except (httpx.HTTPError, ValueError):
            self._value = None
        self._at = time.monotonic()
        return self._value

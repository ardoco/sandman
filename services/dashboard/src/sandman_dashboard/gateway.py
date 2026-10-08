"""LLM gateway statistics: Bifrost /metrics (Prometheus text, own small parser) and budgets (management API)."""

import re
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

SAMPLE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+(\S+)(?:\s+-?\d+)?$")
LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')
UNESCAPE = re.compile(r"\\(.)")
COUNTERS = {
    "requests": "bifrost_upstream_requests_total",
    "input_tokens": "bifrost_input_tokens_total",
    "output_tokens": "bifrost_output_tokens_total",
    "cost": "bifrost_cost_total",
    "errors": "bifrost_error_requests_total",
}
LATENCY = "bifrost_upstream_latency_seconds"
BUDGET_WARN = 0.8
MAX_GROUPS = 500


@dataclass(frozen=True)
class Sample:
    name: str
    labels: Mapping[str, str]
    value: float


def _unescape(value: str) -> str:
    return UNESCAPE.sub(lambda m: "\n" if m[1] == "n" else m[1], value)


def parse_metrics(text: str) -> list[Sample]:
    """The `bifrost_*` samples of a Prometheus text exposition; malformed lines are skipped."""
    samples: list[Sample] = []
    for line in text.splitlines():
        if not line.startswith("bifrost_"):
            continue
        m = SAMPLE.match(line.strip())
        if not m:
            continue
        try:
            value = float(m[3])
        except ValueError:
            continue
        labels = {k: _unescape(v) for k, v in LABEL.findall(m[2] or "")}
        samples.append(Sample(m[1], labels, value))
    return samples


Group = tuple[str, str, str]  # virtual key name, model, provider


def _group(s: Sample) -> Group:
    labels = s.labels
    return (
        labels.get("virtual_key_name") or labels.get("virtual_key_id") or "",
        labels.get("model", ""),
        labels.get("provider", ""),
    )


def quantile(q: float, buckets: Iterable[tuple[float, float]]) -> float | None:
    """histogram_quantile over cumulative (upper bound, count) buckets; None without observations."""
    ordered = sorted(buckets)
    if not ordered or ordered[-1][1] <= 0:
        return None
    rank = q * ordered[-1][1]
    lower, below = 0.0, 0.0
    for bound, count in ordered:
        if count >= rank:
            if bound == float("inf"):
                return lower
            return lower + (bound - lower) * ((rank - below) / (count - below) if count > below else 1.0)
        lower, below = bound, count
    return lower


@dataclass
class Row:
    key: str
    model: str
    provider: str
    requests: float | None = None
    input_tokens: float | None = None
    output_tokens: float | None = None
    cost: float | None = None
    errors: float | None = None
    p50: float | None = None
    p95: float | None = None
    rates: dict[str, float | None] = field(default_factory=dict)


@dataclass
class Budget:
    key_id: str
    key_name: str
    limit: float
    usage: float

    @property
    def ratio(self) -> float:
        return self.usage / self.limit if self.limit > 0 else 0.0

    @property
    def warn(self) -> bool:
        return self.ratio >= BUDGET_WARN


@dataclass
class GatewaySnapshot:
    taken: float
    rows: list[Row]
    budgets: list[Budget] | None


def summarize(samples: list[Sample]) -> list[Row]:
    """Totals per virtual key, model and provider; a metric family absent from the scrape stays None (n/a)."""
    present = {s.name for s in samples}
    rows: dict[Group, Row] = {}
    buckets: dict[Group, dict[float, float]] = defaultdict(lambda: defaultdict(float))

    def row(g: Group) -> Row:
        if g not in rows and len(rows) >= MAX_GROUPS:
            raise ValueError("too many metric groups")
        return rows.setdefault(g, Row(*g))

    for attr, family in COUNTERS.items():
        for s in samples:
            if s.name == family:
                r = row(_group(s))
                setattr(r, attr, (getattr(r, attr) or 0.0) + s.value)
    for s in samples:
        if s.name == f"{LATENCY}_bucket" and "le" in s.labels:
            try:
                buckets[_group(s)][float(s.labels["le"])] += s.value
            except ValueError:
                continue
            row(_group(s))
    for g, r in rows.items():
        if f"{LATENCY}_bucket" in present:
            r.p50, r.p95 = quantile(0.5, buckets[g].items()), quantile(0.95, buckets[g].items())
        for attr, family in COUNTERS.items():
            if family in present and getattr(r, attr) is None:
                setattr(r, attr, 0.0)
    return sorted(rows.values(), key=lambda r: (r.key, r.model, r.provider))


def add_rates(rows: list[Row], previous: GatewaySnapshot | None, now: float) -> None:
    """Per-second rates since the previous poll; None without a previous sample or after a counter reset."""
    before = {(r.key, r.model, r.provider): r for r in previous.rows} if previous else {}
    seconds = now - previous.taken if previous else 0.0
    for r in rows:
        old = before.get((r.key, r.model, r.provider))
        for attr in COUNTERS:
            cur, prev = getattr(r, attr), getattr(old, attr, None) if old else None
            ok = seconds > 0 and cur is not None and prev is not None and cur >= prev
            r.rates[attr] = (cur - prev) / seconds if ok and cur is not None and prev is not None else None


def parse_budgets(doc: Any, names: Mapping[str, str]) -> list[Budget]:
    out: list[Budget] = []
    for b in (doc or {}).get("budgets", []) if isinstance(doc, dict) else []:
        try:
            key_id = str(b["virtual_key_id"])
            out.append(
                Budget(
                    key_id,
                    names.get(key_id, key_id.removeprefix("vk-")),
                    float(b["max_limit"]),
                    float(b["current_usage"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda b: b.key_name)


class GatewayClient:
    """Reads statistics with the gateway admin credentials; error bodies are never exposed."""

    def __init__(self, url: str, password: str, client: httpx.AsyncClient, user: str = "admin") -> None:
        self.url, self.client, self.auth = url.rstrip("/"), client, httpx.BasicAuth(user, password)

    async def fetch(self, previous: GatewaySnapshot | None) -> GatewaySnapshot:
        resp = await self.client.get(f"{self.url}/metrics", auth=self.auth, timeout=2.0)
        resp.raise_for_status()
        samples = parse_metrics(resp.text)
        rows = summarize(samples)
        taken = time.monotonic()
        add_rates(rows, previous, taken)
        names = {
            s.labels["virtual_key_id"]: s.labels.get("virtual_key_name", "")
            for s in samples
            if "virtual_key_id" in s.labels
        }
        budgets: list[Budget] | None = None
        try:
            resp = await self.client.get(f"{self.url}/api/governance/budgets", auth=self.auth, timeout=2.0)
            if resp.status_code == 200:
                budgets = parse_budgets(resp.json(), names)
        except (httpx.HTTPError, ValueError):
            budgets = None
        return GatewaySnapshot(taken, rows, budgets)

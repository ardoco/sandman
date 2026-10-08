"""Alerts evaluated after each poll from the cached data (Stage 1 §10.1, ADR-0011: the log is the only channel).

State is kept in memory; a condition that holds after a restart is raised again. One log line per transition.
"""

import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)

DOWN_AFTER = 30.0
WORKFLOW_WINDOW = timedelta(hours=24)
MEMORY_WARN = 0.8
UNITS = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
SIZE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?i?B)\s*$")


@dataclass(frozen=True)
class Alert:
    rule: str
    key: str
    message: str
    href: str


def parse_size(value: Any) -> float | None:
    """Bytes from a Memgraph size string such as `2.00MiB` or from a number."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    match = SIZE.match(value) if isinstance(value, str) else None
    return float(match[1]) * UNITS[match[2]] if match and match[2] in UNITS else None


def memory_ratio(details: Mapping[str, Any]) -> float | None:
    used = parse_size(details.get("memoryTracked", details.get("memory")))
    limit = parse_size(details.get("memoryLimit"))
    return used / limit if used is not None and limit else None


def budget_alert(b: Any) -> Alert:
    return Alert("budget", f"budget:{b.key_name}", f"budget of {b.key_name} at {b.ratio:.0%}", "/gateway")


class AlertEngine:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.clock, self.wall = clock, wall
        self.active: dict[str, Alert] = {}
        self._down_since: dict[str, float] = {}

    def evaluate(
        self,
        entries: Mapping[str, Any],
        workflows: Iterable[Mapping[str, Any]] | None,
        dead_events: list[Any] | None,
        budgets: Iterable[Any] | None,
    ) -> None:
        """Entries: poller entries. workflows/dead_events/budgets are None when unknown (alerts of that rule stay)."""
        self._set("service_down", self._down(entries))
        if workflows is not None:
            self._set("workflow", self._workflows(workflows))
        if dead_events is not None:
            text = f"{len(dead_events)} dead events"
            self._set(
                "dead_events",
                {"dead-events": Alert("dead_events", "dead-events", text, "/workflows#dead-events")}
                if dead_events
                else {},
            )
        if budgets is not None:
            self._set("budget", {f"budget:{b.key_name}": budget_alert(b) for b in budgets if b.warn})
        memgraph = entries.get("memgraph")
        ratio = memory_ratio(memgraph.details) if memgraph is not None and memgraph.health == "up" else None
        if ratio is not None:
            text = f"Memgraph memory at {ratio:.0%} of its limit"
            high = {"memgraph-memory": Alert("memgraph_memory", "memgraph-memory", text, "/resources")}
            self._set("memgraph_memory", high if ratio >= MEMORY_WARN else {})

    def _down(self, entries: Mapping[str, Any]) -> dict[str, Alert]:
        now = self.clock()
        for name, entry in entries.items():
            if entry.health == "down":
                self._down_since.setdefault(name, now)
            else:
                self._down_since.pop(name, None)
        for name in set(self._down_since) - set(entries):
            del self._down_since[name]
        return {
            f"down:{name}": Alert("service_down", f"down:{name}", f"{name} down for {int(now - since)} s", "/")
            for name, since in self._down_since.items()
            if now - since > DOWN_AFTER
        }

    def _workflows(self, workflows: Iterable[Mapping[str, Any]]) -> dict[str, Alert]:
        limit = self.wall() - WORKFLOW_WINDOW
        found: dict[str, Alert] = {}
        for w in workflows:
            try:
                if datetime.fromisoformat(w["updatedAt"]) >= limit:
                    key = f"workflow:{w['id']}"
                    found[key] = Alert("workflow", key, f"workflow {w['id']} {w['status']}", f"/workflows/{w['id']}")
            except (KeyError, TypeError, ValueError):
                continue
        return found

    def _set(self, rule: str, found: Mapping[str, Alert]) -> None:
        """Replace the alerts of one rule; log one line per raise and per clear."""
        for key in [k for k, a in self.active.items() if a.rule == rule and k not in found]:
            self._log("cleared", self.active.pop(key))
        for key, alert in found.items():
            if key not in self.active:
                self._log("raised", alert)
            self.active[key] = alert

    @staticmethod
    def _log(state: str, alert: Alert) -> None:
        log.warning(
            f"alert {state}",
            extra={
                "fields": {
                    "event": "alert",
                    "alert": alert.rule,
                    "key": alert.key,
                    "state": state,
                    "message": alert.message,
                }
            },
        )

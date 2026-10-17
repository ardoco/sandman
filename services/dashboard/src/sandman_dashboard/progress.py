"""Progress view of a workflow detail: step i of n, current step, timing, stalled hint."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

STALL_SECONDS = 600
LABELS = {
    "PENDING": "waiting", "RUNNING": "running", "SUCCEEDED": "done", "FAILED": "failed",
    "CANCELED": "canceled", "BLOCKED": "blocked",
}  # fmt: skip
PROBLEMS = {
    "DEPENDENCY_UNAVAILABLE": "The agent was not reachable.",
    "TIMEOUT": "The step took too long.",
    "TASK_NOT_FOUND": "The agent lost the task.",
    "BUDGET_EXCEEDED": "A budget limit was exceeded.",
}


def _time(iso: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None


def duration(seconds: float) -> str:
    s = max(int(seconds), 0)
    if s < 60:
        return f"{s} s"
    return f"{s // 60} min {s % 60:02d} s" if s < 3600 else f"{s // 3600} h {s % 3600 // 60:02d} min"


def problem(error: Mapping[str, Any] | None) -> str:
    if not error:
        return ""
    text = PROBLEMS.get(str(error.get("code")), "")
    return (
        f"{text} ({error.get('code')}: {error.get('message')})"
        if text
        else f"{error.get('code')}: {error.get('message')}"
    )


def progress(wf: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    steps = list(wf.get("steps") or [])
    rows = []
    for s in steps:
        start, end = _time(s.get("startedAt")), _time(s.get("endedAt"))
        status = s.get("status", "PENDING")
        elapsed = (end or now) - start if start and (end or status == "RUNNING") else None
        rows.append({
            "id": s.get("id"), "agent": s.get("agent"), "skill": s.get("skill"), "status": status,
            "label": LABELS.get(status, status.lower()),
            "attempts": s.get("attempts", 0),
            "retry": status == "PENDING" and s.get("attempts", 0) > 0,
            "merged": bool(s.get("mergedInto")),
            "elapsed": duration(elapsed.total_seconds()) if elapsed else "",
            "problem": problem(s.get("error")),
            "activity": (s.get("activity") or {}).get("text", "") if status == "RUNNING" else "",
        })  # fmt: skip
    finished = sum(r["status"] in ("SUCCEEDED", "CANCELED") for r in rows)
    current = next((r for r in rows if r["status"] == "RUNNING"), None) or next(
        (r for r in rows if r["status"] == "PENDING"), None
    )
    if current:
        current["current"] = True
    updated = _time(wf.get("updatedAt"))
    quiet = (now - updated).total_seconds() if updated else None
    return {
        "steps": rows, "total": len(rows), "finished": finished,
        "percent": round(100 * finished / len(rows)) if rows else 0,
        "position": rows.index(current) + 1 if current else None, "current": current,
        "since_update": duration(quiet) if quiet is not None else "",
        "stalled": wf.get("status") == "RUNNING" and bool(current and current["status"] == "RUNNING")
        and quiet is not None and quiet > STALL_SECONDS,
        "failed": next((r for r in rows if r["status"] in ("FAILED", "BLOCKED")), None),
    }  # fmt: skip

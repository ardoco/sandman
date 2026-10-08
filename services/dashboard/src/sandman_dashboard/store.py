"""SQLite session store of the dashboard (ADR-0016): only the sha256 of the session id is stored."""

import hashlib
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sandman_agent.kg import now

SESSION_TTL = timedelta(hours=12)
SESSION_IDLE = timedelta(hours=2)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _later(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat(timespec="seconds").replace("+00:00", "Z")


class SessionStore:
    def __init__(self, path: str | Path) -> None:
        self.db = sqlite3.connect(path, autocommit=True, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        for n, script in enumerate(sorted((Path(__file__).parent / "migrations").glob("*.sql")), 1):
            if n > version:
                self.db.executescript(f"BEGIN; {script.read_text()}; PRAGMA user_version = {n}; COMMIT;")

    def close(self) -> None:
        self.db.close()

    def ping(self) -> None:
        self.db.execute("SELECT 1").fetchone()

    def create(self, username: str) -> tuple[str, str]:
        """(session id for the cookie, csrf token); expired sessions are purged."""
        self.db.execute("DELETE FROM sessions WHERE expires_at <= ? OR last_seen <= ?", (now(), _later(-SESSION_IDLE)))
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        self.db.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
            (sha256(sid), username, csrf, now(), _later(SESSION_TTL), now()),
        )
        return sid, csrf

    def get(self, sid: str) -> sqlite3.Row | None:
        """Live session (absolute and idle expiry); touches last_seen."""
        row: sqlite3.Row | None = self.db.execute(
            "SELECT * FROM sessions WHERE id_sha256 = ? AND expires_at > ? AND last_seen > ?",
            (sha256(sid), now(), _later(-SESSION_IDLE)),
        ).fetchone()
        if row:
            self.db.execute("UPDATE sessions SET last_seen = ? WHERE id_sha256 = ?", (now(), row["id_sha256"]))
        return row

    def revoke(self, sid: str) -> None:
        self.db.execute("DELETE FROM sessions WHERE id_sha256 = ?", (sha256(sid),))

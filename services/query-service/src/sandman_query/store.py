"""SQLite user store: users, memberships, personal tokens, console sessions, last visits (ADR-0013)."""

import hashlib
import secrets
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sandman_agent.kg import now

ROLES = ("viewer", "triager", "maintainer")
SESSION_TTL = timedelta(hours=12)
SESSION_IDLE = timedelta(hours=2)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _later(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat(timespec="seconds").replace("+00:00", "Z")


class Exists(Exception):
    """Username already taken."""


class UserStore:
    """All methods are synchronous and fast except hashing, which callers run in a thread."""

    def __init__(self, path: str | Path, hasher: PasswordHasher | None = None) -> None:
        self.db = sqlite3.connect(path, autocommit=True, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.hasher = hasher or PasswordHasher()  # Argon2id
        self._dummy = self.hasher.hash(secrets.token_hex(8))  # equalizes timing for unknown users
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        for n, script in enumerate(sorted((Path(__file__).parent / "migrations").glob("*.sql")), 1):
            if n > version:
                self.db.executescript(f"BEGIN; {script.read_text()}; PRAGMA user_version = {n}; COMMIT;")

    def close(self) -> None:
        self.db.close()

    def ping(self) -> None:
        self.db.execute("SELECT 1").fetchone()

    # ---------------------------------------------------------------- users

    def create_user(
        self, username: str, password: str, global_roles: Sequence[str] = (), must_change: bool = True
    ) -> None:
        try:
            self.db.execute(
                "INSERT INTO users VALUES (?, ?, ?, 0, ?, ?)",
                (username, self.hasher.hash(password), ",".join(global_roles), int(must_change), now()),
            )
        except sqlite3.IntegrityError:
            raise Exists(username) from None

    def user_count(self) -> int:
        return int(self.db.execute("SELECT count(*) FROM users").fetchone()[0])

    def get_user(self, username: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return row

    def list_users(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM users ORDER BY username").fetchall()

    def update_user(
        self,
        username: str,
        disabled: bool | None = None,
        password: str | None = None,
        global_roles: Sequence[str] | None = None,
        must_change: bool | None = None,
    ) -> bool:
        """Apply the given changes; disabling or a password change revokes the user's sessions."""
        if self.get_user(username) is None:
            return False
        if disabled is not None:
            self.db.execute("UPDATE users SET disabled = ? WHERE username = ?", (int(disabled), username))
        if password is not None:
            self.db.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?", (self.hasher.hash(password), username)
            )
        if global_roles is not None:
            self.db.execute("UPDATE users SET global_roles = ? WHERE username = ?", (",".join(global_roles), username))
        if must_change is not None:
            self.db.execute(
                "UPDATE users SET must_change_password = ? WHERE username = ?", (int(must_change), username)
            )
        if disabled or password is not None:
            self.revoke_user_sessions(username)
        return True

    def verify_password(self, username: str, password: str) -> sqlite3.Row | None:
        """The enabled user if the password matches (constant work for unknown users)."""
        user = self.get_user(username)
        try:
            self.hasher.verify(user["password_hash"] if user else self._dummy, password)
        except (VerificationError, InvalidHashError):
            return None
        return user if user and not user["disabled"] else None

    # ---------------------------------------------------------- memberships

    def set_member(self, project: str, username: str, role: str) -> bool:
        if self.get_user(username) is None:
            return False
        self.db.execute(
            "INSERT INTO memberships VALUES (?, ?, ?) ON CONFLICT (project, username) DO UPDATE SET role = ?",
            (project, username, role, role),
        )
        return True

    def delete_member(self, project: str, username: str) -> None:
        self.db.execute("DELETE FROM memberships WHERE project = ? AND username = ?", (project, username))

    def memberships(self, username: str) -> dict[str, str]:
        rows = self.db.execute("SELECT project, role FROM memberships WHERE username = ?", (username,))
        return {r["project"]: r["role"] for r in rows}

    def members(self, project: str) -> dict[str, str]:
        rows = self.db.execute("SELECT username, role FROM memberships WHERE project = ?", (project,))
        return {r["username"]: r["role"] for r in rows}

    # --------------------------------------------------------------- tokens

    def create_token(
        self, username: str, name: str, project_id: str | None, max_role: str | None, expires_at: str | None
    ) -> tuple[str, str]:
        """(token id, secret); only the sha256 of the secret is stored."""
        token_id, secret = uuid.uuid4().hex, "sdm_" + secrets.token_urlsafe(32)
        self.db.execute(
            "INSERT INTO tokens VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (token_id, username, name, sha256(secret), project_id, max_role, now(), expires_at),
        )
        return token_id, secret

    def list_tokens(self, username: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM tokens WHERE username = ? ORDER BY created_at, id", (username,)
        ).fetchall()

    def get_token(self, username: str, token_id: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.db.execute(
            "SELECT * FROM tokens WHERE id = ? AND username = ?", (token_id, username)
        ).fetchone()
        return row

    def delete_token(self, username: str, token_id: str) -> bool:
        return self.db.execute("DELETE FROM tokens WHERE id = ? AND username = ?", (token_id, username)).rowcount > 0

    def find_token(self, secret: str) -> sqlite3.Row | None:
        """Unexpired token of an enabled user by secret; records the use."""
        row: sqlite3.Row | None = self.db.execute(
            "SELECT t.* FROM tokens t JOIN users u USING (username) WHERE t.secret_sha256 = ? AND u.disabled = 0 "
            "AND (t.expires_at IS NULL OR t.expires_at > ?)",
            (sha256(secret), now()),
        ).fetchone()
        if row:
            self.db.execute("UPDATE tokens SET last_used_at = ? WHERE id = ?", (now(), row["id"]))
        return row

    # ------------------------------------------------------------- sessions

    def create_session(self, username: str) -> tuple[str, str]:
        """(session id for the cookie, csrf token)."""
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        self.db.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
            (sha256(sid), username, csrf, now(), _later(SESSION_TTL), now()),
        )
        return sid, csrf

    def get_session(self, sid: str) -> sqlite3.Row | None:
        """Live session of an enabled user (absolute and idle expiry); touches last_seen."""
        row: sqlite3.Row | None = self.db.execute(
            "SELECT s.* FROM sessions s JOIN users u USING (username) WHERE s.id_sha256 = ? AND u.disabled = 0 "
            "AND s.expires_at > ? AND s.last_seen > ?",
            (sha256(sid), now(), _later(-SESSION_IDLE)),
        ).fetchone()
        if row:
            self.db.execute("UPDATE sessions SET last_seen = ? WHERE id_sha256 = ?", (now(), row["id_sha256"]))
        return row

    def revoke_session(self, sid: str) -> None:
        self.db.execute("DELETE FROM sessions WHERE id_sha256 = ?", (sha256(sid),))

    def revoke_user_sessions(self, username: str) -> None:
        self.db.execute("DELETE FROM sessions WHERE username = ?", (username,))

    # ---------------------------------------------------------- last visits

    def last_visits(self, username: str) -> dict[str, str]:
        rows = self.db.execute("SELECT project, visited_at FROM last_visits WHERE username = ?", (username,))
        return {r["project"]: r["visited_at"] for r in rows}

    def touch_visit(self, username: str, project: str) -> None:
        self.db.execute(
            "INSERT INTO last_visits VALUES (?, ?, ?) ON CONFLICT (username, project) DO UPDATE SET visited_at = ?",
            (username, project, now(), now()),
        )


def row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)

"""Principals, roles and the authorization decision (ADR-0013, Stage 2 §1.5)."""

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from sandman_agent.auth import PLUGIN_PREFIX
from sandman_agent.contracts import Contracts
from starlette.exceptions import HTTPException

LEVEL = {"authenticated": 0, "viewer": 1, "triager": 2, "maintainer": 3, "admin": 4}
USER_ONLY = {"me", "listTokens", "createToken", "revokeToken"}  # about the caller's own account
PLUGIN_OPS = {"queryCypher", "getSchema"}


def load_min_roles(contracts_dir: Path) -> dict[str, str]:
    """operationId -> x-sandman-min-role from the OpenAPI contract."""
    spec = yaml.safe_load((contracts_dir / "openapi" / "query-service.yaml").read_text(encoding="utf-8"))
    return {
        op["operationId"]: op["x-sandman-min-role"]
        for item in spec["paths"].values()
        for op in item.values()
        if isinstance(op, dict) and "x-sandman-min-role" in op
    }


def load_plugin_grants(path: Path, contracts: Contracts) -> dict[str, list[str] | str]:
    """config/query/plugins.yaml: {plugin-id: [project, ...] | "*"}, validated against query-plugins.schema.json."""
    return contracts.load_config("query-plugins", path) if path.exists() else {}  # type: ignore[no-any-return]


@dataclass(frozen=True)
class Principal:
    kind: Literal["user", "service", "plugin"]
    name: str  # username, service name or plugin:<id>
    admin: bool = False
    memberships: dict[str, str] = field(default_factory=dict)
    token_project: str | None = None
    token_max_role: str | None = None
    grants: list[str] | str = field(default_factory=list)  # plugins: granted projects or "*"

    @property
    def actor(self) -> str | None:
        return f"user:{self.name}" if self.kind == "user" else None

    @property
    def unrestricted_admin(self) -> bool:
        return self.admin and self.token_project is None and self.token_max_role is None

    def level(self, project: str) -> int:
        """Effective project role (0 = none): min(membership, token maxRole); services read everything."""
        if self.token_project not in (None, project):
            return 0
        if self.kind == "plugin":
            return LEVEL["viewer"] if self.grants == "*" or project in self.grants else 0
        base = (
            LEVEL["maintainer"]
            if self.admin
            else LEVEL["viewer"]
            if self.kind == "service"
            else LEVEL.get(self.memberships.get(project, ""), 0)
        )
        return min(base, LEVEL[self.token_max_role]) if self.token_max_role else base


def authorize(p: Principal, min_roles: dict[str, str], op: str, project: str | None = None) -> None:
    """Raise 403 (not allowed) or 404 (project not readable) unless the caller may run operation `op`."""
    need = min_roles[op]
    if p.kind == "plugin" and op not in PLUGIN_OPS:
        raise HTTPException(403, "plugin callers may only run Cypher queries and read the schema")
    if p.kind == "service" and (op in USER_ONLY or LEVEL[need] > LEVEL["viewer"]):
        raise HTTPException(403, "service callers may only read")
    if need == "admin":
        if not p.unrestricted_admin:
            raise HTTPException(403, "admin role required")
    elif project is None:
        if p.kind == "user" and LEVEL[need] >= LEVEL["viewer"]:
            member = p.level(p.token_project) > 0 if p.token_project else p.admin or bool(p.memberships)
            if not member:
                raise HTTPException(403, f"role {need} required")
    else:
        level = p.level(project)
        if level == 0:
            raise HTTPException(404, "not found")
        if level < LEVEL[need]:
            raise HTTPException(403, f"role {need} required")


class RateLimiter:
    """Failed logins per key within a window (in memory; ceiling: single process, per-process counts)."""

    def __init__(self, limit: int = 5, window: float = 900.0) -> None:
        self.limit, self.window = limit, window
        self._fails: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, key: str) -> deque[float]:
        q = self._fails.get(key)
        if q is None:
            return deque()
        while q and q[0] < time.monotonic() - self.window:
            q.popleft()
        if not q:
            del self._fails[key]
        return q

    def blocked(self, *keys: str) -> bool:
        return any(len(self._recent(k)) >= self.limit for k in keys)

    def fail(self, *keys: str) -> None:
        for k in keys:
            self._fails[k] = self._recent(k) or deque()
            self._fails[k].append(time.monotonic())

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)


def plugin_principal(name: str, grants: dict[str, Any]) -> Principal:
    return Principal("plugin", name, grants=grants.get(name.removeprefix(PLUGIN_PREFIX), []))

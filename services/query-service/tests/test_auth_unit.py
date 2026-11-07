"""Pure authorization logic: effective level, decision and failed-login limiter (no app, no Docker)."""

from pathlib import Path
from typing import Any

import pytest
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_agent.contracts import ContractError, Contracts
from sandman_query import auth
from sandman_query.auth import LEVEL, Principal, RateLimiter, authorize, load_min_roles, load_plugin_grants, plugin_principal
from starlette.exceptions import HTTPException

ROLES = {
    "me": "authenticated",
    "view": "viewer",
    "triage": "triager",
    "maintain": "maintainer",
    "adm": "admin",
    "queryCypher": "viewer",
    "getSchema": "authenticated",
    "listTokens": "authenticated",
}


def code(p: Principal, op: str, project: str | None = None) -> int:
    try:
        authorize(p, ROLES, op, project)
    except HTTPException as e:
        return e.status_code
    return 200


def user(role: str | None = None, **kw: Any) -> Principal:
    return Principal("user", "u", memberships={"a": role} if role else {}, **kw)


def test_actor_and_unrestricted_admin() -> None:
    assert user().actor == "user:u"
    assert Principal("service", "s").actor is None
    assert Principal("user", "u", admin=True).unrestricted_admin
    assert not Principal("user", "u").unrestricted_admin
    assert not Principal("user", "u", admin=True, token_project="a").unrestricted_admin
    assert not Principal("user", "u", admin=True, token_max_role="viewer").unrestricted_admin


@pytest.mark.parametrize(
    ("principal", "project", "level"),
    [
        (user("viewer"), "a", LEVEL["viewer"]),
        (user("maintainer"), "a", LEVEL["maintainer"]),
        (user("viewer"), "b", 0),
        (user("bogus"), "a", 0),
        (Principal("user", "u", admin=True), "zzz", LEVEL["maintainer"]),
        (Principal("service", "s"), "zzz", LEVEL["viewer"]),
        (user("maintainer", token_max_role="viewer"), "a", LEVEL["viewer"]),
        (user("viewer", token_max_role="maintainer"), "a", LEVEL["viewer"]),
        (user("maintainer", token_project="a"), "a", LEVEL["maintainer"]),
        (user("maintainer", token_project="a"), "b", 0),
        (Principal("user", "u", admin=True, token_max_role="triager"), "x", LEVEL["triager"]),
        (Principal("plugin", "plugin:p", grants="*"), "x", LEVEL["viewer"]),
        (Principal("plugin", "plugin:p", grants=["a"]), "a", LEVEL["viewer"]),
        (Principal("plugin", "plugin:p", grants=["a"]), "b", 0),
        (Principal("plugin", "plugin:p", grants="*", token_project="a"), "b", 0),
        (Principal("service", "s", token_project="a"), "b", 0),
    ],
)
def test_effective_level(principal: Principal, project: str, level: int) -> None:
    assert principal.level(project) == level


def test_plugins_run_only_cypher_and_schema() -> None:
    p = Principal("plugin", "plugin:p", grants=["a"])
    assert code(p, "queryCypher", "a") == 200
    assert code(p, "getSchema") == 200
    assert code(p, "view", "a") == 403
    assert code(p, "queryCypher", "b") == 404


def test_services_only_read() -> None:
    s = Principal("service", "s")
    assert code(s, "view", "a") == 200
    assert code(s, "triage", "a") == 403
    assert code(s, "me") == 403
    assert code(s, "listTokens") == 403
    assert code(s, "view") == 200


def test_admin_operations_need_unrestricted_admin() -> None:
    assert code(Principal("user", "u", admin=True), "adm") == 200
    assert code(Principal("user", "u", admin=True, token_project="a"), "adm") == 403
    assert code(Principal("user", "u", admin=True, token_max_role="viewer"), "adm") == 403
    assert code(user("maintainer"), "adm") == 403
    assert code(Principal("service", "s"), "adm") == 403


def test_projectless_operations_need_some_membership() -> None:
    assert code(user("viewer"), "view") == 200
    assert code(user("viewer"), "me") == 200
    assert code(user(), "view") == 403
    assert code(user(), "me") == 200
    assert code(user(), "getSchema") == 200
    assert code(Principal("user", "u", admin=True), "view") == 200
    assert code(user("viewer", token_project="a"), "view") == 200
    assert code(user("viewer", token_project="b"), "view") == 403


def test_projectless_error_names_the_role() -> None:
    with pytest.raises(HTTPException) as e:
        authorize(user(), ROLES, "triage")
    assert (e.value.status_code, e.value.detail) == (403, "role triager required")


def test_project_operations_compare_levels() -> None:
    assert code(user("viewer"), "view", "a") == 200
    assert code(user("viewer"), "triage", "a") == 403
    assert code(user("triager"), "triage", "a") == 200
    assert code(user("triager"), "maintain", "a") == 403
    assert code(user("maintainer"), "maintain", "a") == 200
    assert code(user("viewer"), "view", "b") == 404
    assert code(user("viewer"), "me", "a") == 200
    with pytest.raises(HTTPException) as e:
        authorize(user("viewer"), ROLES, "maintain", "a")
    assert (e.value.status_code, e.value.detail) == (403, "role maintainer required")
    with pytest.raises(HTTPException) as e:
        authorize(user("viewer"), ROLES, "view", "b")
    assert (e.value.status_code, e.value.detail) == (404, "not found")


def test_deny_messages() -> None:
    cases = [
        (Principal("plugin", "plugin:p"), "view", "plugin callers may only run Cypher queries and read the schema"),
        (Principal("service", "s"), "triage", "service callers may only read"),
        (user("viewer"), "adm", "admin role required"),
    ]
    for p, op, msg in cases:
        with pytest.raises(HTTPException) as e:
            authorize(p, ROLES, op)
        assert (e.value.status_code, e.value.detail) == (403, msg)


def test_plugin_principal_strips_prefix() -> None:
    p = plugin_principal("plugin:x", {"x": ["a"], "plugin:x": ["b"]})
    assert (p.kind, p.name, p.grants) == ("plugin", "plugin:x", ["a"])
    assert plugin_principal("plugin:y", {"x": ["a"]}).grants == []
    assert plugin_principal("plugin:z", {"z": "*"}).grants == "*"


def test_load_min_roles_reads_operation_roles(tmp_path: Path) -> None:
    (tmp_path / "openapi").mkdir()
    (tmp_path / "openapi" / "query-service.yaml").write_text(
        "paths:\n  /a:\n    parameters: []\n    get: {operationId: one, x-sandman-min-role: viewer}\n"
        "    post: {operationId: two}\n  /b:\n    get: {operationId: three, x-sandman-min-role: admin}\n",
        encoding="utf-8",
    )
    assert load_min_roles(tmp_path) == {"one": "viewer", "three": "admin"}


def test_real_contract_roles_are_known_levels() -> None:
    roles = load_min_roles(DEFAULT_CONTRACTS_DIR)
    assert roles
    assert set(roles.values()) <= set(LEVEL)


def test_load_plugin_grants(tmp_path: Path) -> None:
    contracts = Contracts(DEFAULT_CONTRACTS_DIR)
    assert load_plugin_grants(tmp_path / "missing.yaml", contracts) == {}
    f = tmp_path / "plugins.yaml"
    f.write_text("p1: '*'\np2: [aa, bb]\n", encoding="utf-8")
    assert load_plugin_grants(f, contracts) == {"p1": "*", "p2": ["aa", "bb"]}
    f.write_text("p1: 3\n", encoding="utf-8")
    with pytest.raises(ContractError):
        load_plugin_grants(f, contracts)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(auth.time, "monotonic", c)
    return c


def test_rate_limiter_blocks_at_limit_and_expires(clock: Clock) -> None:
    rl = RateLimiter(limit=3, window=10.0)
    assert not rl.blocked("k")
    rl.fail("k")
    rl.fail("k")
    assert not rl.blocked("k")
    rl.fail("k")
    assert rl.blocked("k")
    assert rl.blocked("other", "k")
    assert not rl.blocked("other")
    clock.now += 10.0
    assert rl.blocked("k")
    clock.now += 0.5
    assert not rl.blocked("k")
    assert "k" not in rl._fails


def test_rate_limiter_window_is_sliding(clock: Clock) -> None:
    rl = RateLimiter(limit=2, window=10.0)
    rl.fail("k")
    clock.now += 6
    rl.fail("k")
    assert rl.blocked("k")
    clock.now += 5
    assert not rl.blocked("k")
    rl.fail("k")
    assert rl.blocked("k")


def test_rate_limiter_fail_counts_every_key_and_reset_clears_one(clock: Clock) -> None:
    rl = RateLimiter(limit=1, window=10.0)
    rl.fail("a", "b")
    assert rl.blocked("a") and rl.blocked("b")
    rl.reset("a")
    assert not rl.blocked("a") and rl.blocked("b")
    rl.reset("missing")


def test_rate_limiter_defaults() -> None:
    rl = RateLimiter()
    assert (rl.limit, rl.window) == (5, 900.0)

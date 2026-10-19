"""Authorization matrix: every operation x caller kind, from x-sandman-min-role (ADR-0013, Stage 2 §1.5)."""

from typing import Any

import pytest
from conftest import Env, service_headers
from helpers import INC_ID, LINK_ID, SPEC, WF_ID, validate, validate_problem, validate_projects

CALLERS = ["anonymous", "stranger", "viewer", "triager", "maintainer", "admin", "service", "plugin_stub",
           "plugin_other", "tok_max_viewer", "tok_beta", "tok_admin_restricted"]  # fmt: skip
USERS = {"viewer", "triager", "maintainer", "admin", "stranger", "tok_max_viewer", "tok_beta", "tok_admin_restricted"}

# kind -> {caller: expected} where "ok" means the operation runs
ADMIN_ONLY = {c: 403 for c in CALLERS} | {"anonymous": 401, "admin": "ok"}
KINDS: dict[str, dict[str, Any]] = {
    "own": {c: "ok" for c in USERS} | {"anonymous": 401, "service": 403, "plugin_stub": 403, "plugin_other": 403},
    "list": {c: "ok" for c in USERS} | {"anonymous": 401, "service": "ok", "plugin_stub": 403, "plugin_other": 403},
    "admin": ADMIN_ONLY,
    "schema": {c: "ok" for c in CALLERS} | {"anonymous": 401},
    "cypher": {
        "anonymous": 401,
        "stranger": 404,
        "viewer": "ok",
        "triager": "ok",
        "maintainer": "ok",
        "admin": "ok",
        "service": "ok",
        "plugin_stub": "ok",
        "plugin_other": "ok",
        "tok_max_viewer": "ok",
        "tok_beta": 404,
        "tok_admin_restricted": 404,
    },  # fmt: skip
    "read": {
        "anonymous": 401,
        "stranger": 404,
        "viewer": "ok",
        "triager": "ok",
        "maintainer": "ok",
        "admin": "ok",
        "service": "ok",
        "plugin_stub": 403,
        "plugin_other": 403,
        "tok_max_viewer": "ok",
        "tok_beta": 404,
        "tok_admin_restricted": 404,
    },  # fmt: skip
    "triager": {
        "anonymous": 401,
        "stranger": 404,
        "viewer": 403,
        "triager": "ok",
        "maintainer": "ok",
        "admin": "ok",
        "service": 403,
        "plugin_stub": 403,
        "plugin_other": 403,
        "tok_max_viewer": 403,
        "tok_beta": 404,
        "tok_admin_restricted": 404,
    },  # fmt: skip
    "maintainer": {
        "anonymous": 401,
        "stranger": 404,
        "viewer": 403,
        "triager": 403,
        "maintainer": "ok",
        "admin": "ok",
        "service": 403,
        "plugin_stub": 403,
        "plugin_other": 403,
        "tok_max_viewer": 403,
        "tok_beta": 404,
        "tok_admin_restricted": 404,
    },  # fmt: skip
}
PROJECTLESS = {"getInconsistency", "transitionInconsistency", "getWorkflow"}
MULTIPART = {"files": {"manifest": (None, '{"mode":"full","files":[]}', "application/json")}}
# operationId -> (kind, method, path, request kwargs, success statuses)
OPS: dict[str, tuple[str, str, str, dict[str, Any], set[int]]] = {
    "me": ("own", "GET", "/v1/auth/me", {}, {200}),
    "listTokens": ("own", "GET", "/v1/auth/tokens", {}, {200}),
    "listUsers": ("admin", "GET", "/v1/admin/users", {}, {200}),
    "createUser": ("admin", "POST", "/v1/admin/users", {"json": {"username": "newbie", "password": "x" * 12}}, {201}),
    "updateUser": ("admin", "PATCH", "/v1/admin/users/target", {"json": {"disabled": False}}, {200}),
    "putProject": ("admin", "PUT", "/v1/admin/projects/alpha",
                   {"json": {"name": "A", "llmPolicy": "local-only"}}, {200}),
    "putMember": ("admin", "PUT", "/v1/admin/projects/alpha/members/target", {"json": {"role": "viewer"}}, {204}),
    "deleteMember": ("admin", "DELETE", "/v1/admin/projects/alpha/members/target", {}, {204}),
    "listProjects": ("list", "GET", "/v1/projects", {}, {200}),
    "checkIngestion": ("maintainer", "POST", "/v1/projects/alpha/ingestions/check",
                       {"json": {"mode": "full", "files": []}}, {200}),
    "createIngestion": ("maintainer", "POST", "/v1/projects/alpha/ingestions", MULTIPART, {201}),
    "listInconsistencies": ("read", "GET", "/v1/projects/alpha/inconsistencies", {}, {200}),
    "getInconsistency": ("read", "GET", f"/v1/inconsistencies/{INC_ID}", {}, {200}),
    "transitionInconsistency": ("triager", "POST", f"/v1/inconsistencies/{INC_ID}/transitions",
                                {"json": {"to": "ACCEPTED", "expectedStatus": "OPEN", "reason": "r"}}, {200}),
    "reviewTraceLink": ("triager", "POST", f"/v1/projects/alpha/links/{LINK_ID}/review",
                        {"json": {"status": "confirmed"}}, {200}),
    "listWorkflows": ("read", "GET", "/v1/projects/alpha/workflows", {}, {200}),
    "createWorkflow": ("maintainer", "POST", "/v1/projects/alpha/workflows", {"json": {"type": "full"}}, {202}),
    "getSchema": ("schema", "GET", "/v1/schema", {}, {200}),
    "queryCypher": ("cypher", "POST", "/v1/query/cypher",
                    {"json": {"projectId": "alpha", "cypher": "MATCH (n {projectId: $projectId}) RETURN n"}}, {200}),
    "queryNl": ("read", "POST", "/v1/query/nl",
                {"json": {"projectId": "alpha", "question": "open inconsistencies"}}, {200}),
    "exportTraceLinks": ("read", "GET", "/v1/projects/alpha/export/tracelinks?format=json", {}, {200}),
    "exportInconsistencies": ("read", "GET", "/v1/projects/alpha/export/inconsistencies", {}, {200}),
    "getWorkflow": ("read", "GET", f"/v1/workflows/{WF_ID}", {}, {200}),
}  # fmt: skip


def headers_for(env: Env, caller: str) -> dict[str, str]:
    alpha = {"alpha": "maintainer"}
    match caller:
        case "anonymous":
            return {}
        case "service":
            return service_headers("orchestrator")
        case "plugin_stub" | "plugin_other":
            return service_headers(caller.replace("plugin_", "plugin:"))
        case "stranger":
            env.user("stranger", {"beta": "viewer"})
        case "viewer" | "triager" | "maintainer":
            env.user(caller, {"alpha": caller})
        case "admin":
            env.user("admin2", admin=True)
            return env.token("admin2")
        case "tok_max_viewer":
            env.user(caller, alpha)
            return env.token(caller, max_role="viewer")
        case "tok_beta":
            env.user(caller, alpha)
            return env.token(caller, project="beta")
        case "tok_admin_restricted":
            env.user(caller, admin=True)
            return env.token(caller, project="beta")
    return env.token(caller)


ROLE_OF_KIND = {"own": "authenticated", "list": "authenticated", "schema": "viewer", "read": "viewer",
                "cypher": "viewer"}  # fmt: skip


def _operations() -> list[tuple[str, str, str, dict[str, Any]]]:
    return [(path, m, op["operationId"], op) for path, item in SPEC["paths"].items() for m, op in item.items()
            if isinstance(op, dict) and "operationId" in op]  # fmt: skip


async def test_every_operation_is_covered(env: Env) -> None:
    ops = _operations()
    out_of_scope = {op for *_, op, spec in ops if spec.get("x-sandman-status") == "out-of-scope-M0"}
    declared = {
        op: spec["x-sandman-min-role"]
        for *_, op, spec in ops
        if op not in out_of_scope and "x-sandman-min-role" in spec
    }
    assert set(declared) == set(OPS) | {"revokeToken", "createToken"}  # tested in test_api.py
    for op, (kind, *_rest) in OPS.items():
        assert declared[op] == ROLE_OF_KIND.get(kind, kind), op
    routes = {(m.lower(), r.path) for r in env.app.routes for m in getattr(r, "methods", ())}  # type: ignore[attr-defined]
    for path, method, op, _ in ops:  # R-007: out-of-scope operations stay unforwarded
        if op in out_of_scope:
            assert (method, path) not in routes, op


@pytest.mark.parametrize("caller", CALLERS)
@pytest.mark.parametrize("op", OPS)
async def test_matrix(env: Env, op: str, caller: str) -> None:
    kind, method, path, kwargs, success = OPS[op]
    expected = KINDS[kind][caller]
    if caller == "tok_beta" and (kind == "schema" or op in PROJECTLESS):
        expected = 403  # token scoped to beta, membership only in alpha: project-less operations are refused
    env.user("target", {"alpha": "viewer"})  # object of the admin operations
    resp = await env.http.request(method, path, headers=headers_for(env, caller), **kwargs)
    if expected == "ok":
        assert resp.status_code in success, resp.text
        if op == "listProjects":
            validate_projects(resp.json())
        elif resp.status_code != 204:
            validate(next(p for p in SPEC["paths"] if _matches(p, path)), method.lower(), resp.status_code, resp.json())
    else:
        assert resp.status_code == expected, resp.text
        validate_problem(resp.json())
        assert resp.headers["content-type"].startswith("application/problem+json")


def _matches(template: str, path: str) -> bool:
    a, b = template.strip("/").split("/"), path.split("?")[0].strip("/").split("/")
    return len(a) == len(b) and all(x == y or x.startswith("{") for x, y in zip(a, b, strict=True))


def test_project_scoped_token_needs_membership_in_its_own_project_for_projectless_operations() -> None:
    from sandman_query.auth import Principal, authorize
    from starlette.exceptions import HTTPException

    roles = {"list": "viewer"}
    authorize(Principal("user", "u", memberships={"a": "viewer"}, token_project="a"), roles, "list")
    for p in (
        Principal("user", "u", memberships={"b": "viewer"}, token_project="a"),
        Principal("user", "u", admin=False, token_project="a"),
    ):
        with pytest.raises(HTTPException) as e:
            authorize(p, roles, "list")
        assert e.value.status_code == 403

"""Authentication, tokens, admin and forwarding behavior of the /v1 API."""

from pathlib import Path

import httpx
from conftest import ADMIN_PASSWORD, Env, service_headers
from helpers import INC_ID, OTHER_INC_ID, validate, validate_shared
from sandman_query.app import build


async def login(env: Env, username: str = "admin", password: str = ADMIN_PASSWORD) -> "object":
    return await env.http.post("/v1/auth/login", json={"username": username, "password": password})


async def test_login_sets_strict_httponly_cookie_and_v1_ignores_it(env: Env) -> None:
    resp = await login(env)
    assert resp.status_code == 200
    validate("/v1/auth/login", "post", 200, resp.json())
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" not in cookie
    assert (await env.http.get("/v1/auth/me")).status_code == 401  # the http client keeps the cookie
    env.app.state.service.cookie_secure = True
    env.user("alice")
    assert "secure" in (await login(env, "alice", "password-alicealice")).headers["set-cookie"].lower()


async def test_login_failures_and_rate_limit(env: Env) -> None:
    for _ in range(5):
        assert (await login(env, "admin", "wrong")).status_code == 401
    blocked = await login(env, "admin", ADMIN_PASSWORD)
    assert blocked.status_code == 429 and blocked.headers["retry-after"]
    assert (await login(env, "someone-else", "wrong")).status_code == 429  # same client address


async def test_logout_revokes_session(env: Env) -> None:
    await login(env)
    sid = env.http.cookies["sandman_session"]
    assert env.store.get_session(sid)
    assert (await env.http.post("/v1/auth/logout")).status_code == 204
    assert env.store.get_session(sid) is None


async def test_tokens_lifecycle(env: Env) -> None:
    env.user("alice", {"alpha": "maintainer"})
    first = env.token("alice")
    resp = await env.http.post(
        "/v1/auth/tokens", json={"name": "ci", "projectId": "alpha", "maxRole": "triager"}, headers=first
    )
    assert resp.status_code == 201
    validate("/v1/auth/tokens", "post", 201, resp.json())
    secret = resp.json()["secret"]
    assert secret not in str(env.store.db.execute("SELECT * FROM tokens").fetchall())  # only the hash is stored
    ci = {"authorization": f"Bearer {secret}"}
    me = await env.http.get("/v1/auth/me", headers=ci)
    assert me.status_code == 200
    validate("/v1/auth/me", "get", 200, me.json())
    listed = (await env.http.get("/v1/auth/tokens", headers=first)).json()
    validate("/v1/auth/tokens", "get", 200, listed)
    assert "secret" not in str(listed) and {t["name"] for t in listed} == {"t", "ci"}
    assert next(t for t in listed if t["name"] == "ci")["lastUsedAt"]
    # a restricted token cannot mint or revoke tokens
    assert (await env.http.post("/v1/auth/tokens", json={"name": "x"}, headers=ci)).status_code == 403
    assert (await env.http.delete(f"/v1/auth/tokens/{resp.json()['id']}", headers=ci)).status_code == 403
    assert (await env.http.delete(f"/v1/auth/tokens/{resp.json()['id']}", headers=first)).status_code == 204
    assert (await env.http.delete(f"/v1/auth/tokens/{resp.json()['id']}", headers=first)).status_code == 404
    assert (await env.http.get("/v1/auth/me", headers=ci)).status_code == 401


async def test_token_input_validation_expiry_and_foreign_tokens(env: Env) -> None:
    env.user("alice")
    env.user("bob")
    h = env.token("alice")
    assert (await env.http.post("/v1/auth/tokens", json={"name": "x" * 101}, headers=h)).status_code == 400
    assert (
        await env.http.post("/v1/auth/tokens", json={"name": "x", "maxRole": "admin"}, headers=h)
    ).status_code == 400
    assert (await env.http.post("/v1/auth/tokens", json={"name": "x", "extra": 1}, headers=h)).status_code == 400
    past = await env.http.post("/v1/auth/tokens", json={"name": "old", "expiresAt": "2020-01-01T00:00:00Z"}, headers=h)
    assert (
        await env.http.get("/v1/auth/me", headers={"authorization": f"Bearer {past.json()['secret']}"})
    ).status_code == 401
    other = env.store.list_tokens("alice")[0]["id"]
    assert (await env.http.delete(f"/v1/auth/tokens/{other}", headers=env.token("bob"))).status_code == 404


async def test_disabled_user_and_forced_password_change_block_tokens(env: Env) -> None:
    env.user("alice")
    h = env.token("alice")
    env.store.update_user("alice", must_change=True)
    assert (await env.http.get("/v1/auth/me", headers=h)).status_code == 403
    env.store.update_user("alice", must_change=False, disabled=True)
    assert (await env.http.get("/v1/auth/me", headers=h)).status_code == 401
    assert (await login(env, "alice", "password-alicealice")).status_code == 401


async def test_bootstrap_admin_must_change_password(env: Env) -> None:
    admin = env.store.get_user("admin")
    assert admin and admin["must_change_password"] and admin["global_roles"] == "admin"
    assert env.store.user_count() == 1


async def test_admin_user_and_membership_management(env: Env) -> None:
    env.user("root", admin=True)
    h = env.token("root")
    created = await env.http.post(
        "/v1/admin/users", json={"username": "carol", "password": "long-enough-pw"}, headers=h
    )
    assert created.status_code == 201 and created.json()["globalRoles"] == []
    assert (
        await env.http.post("/v1/admin/users", json={"username": "carol", "password": "long-enough-pw"}, headers=h)
    ).status_code == 409
    assert (
        await env.http.post("/v1/admin/users", json={"username": "d", "password": "long-enough-pw"}, headers=h)
    ).status_code == 400
    assert (
        await env.http.post("/v1/admin/users", json={"username": "dave", "password": "short"}, headers=h)
    ).status_code == 400
    assert env.store.get_user("carol")["must_change_password"]  # type: ignore[index]
    assert (
        await env.http.put("/v1/admin/projects/alpha/members/carol", json={"role": "triager"}, headers=h)
    ).status_code == 204
    assert (
        await env.http.put("/v1/admin/projects/alpha/members/nobody", json={"role": "triager"}, headers=h)
    ).status_code == 404
    assert (
        await env.http.put("/v1/admin/projects/alpha/members/carol", json={"role": "admin"}, headers=h)
    ).status_code == 400
    listed = await env.http.get("/v1/admin/users", headers=h)
    validate("/v1/admin/users", "get", 200, listed.json())
    assert {"projectId": "alpha", "role": "triager"} in next(u for u in listed.json() if u["username"] == "carol")[
        "memberships"
    ]
    patched = await env.http.patch(
        "/v1/admin/users/carol", json={"disabled": True, "globalRoles": ["admin"]}, headers=h
    )
    assert patched.json()["disabled"] and patched.json()["globalRoles"] == ["admin"]
    assert (await env.http.patch("/v1/admin/users/ghost", json={"disabled": True}, headers=h)).status_code == 404
    assert (await env.http.delete("/v1/admin/projects/alpha/members/carol", headers=h)).status_code == 204
    assert env.store.memberships("carol") == {}


async def test_projects_listing_is_confined_to_readable_projects(env: Env) -> None:
    env.user("alice", {"beta": "triager"})
    projects = (await env.http.get("/v1/projects", headers=env.token("alice"))).json()
    assert [(p["id"], p["role"]) for p in projects] == [("beta", "triager")]
    assert len((await env.http.get("/v1/projects", headers=service_headers("orchestrator"))).json()) == 2
    env.user("capped", {"alpha": "maintainer"})
    capped = (await env.http.get("/v1/projects", headers=env.token("capped", max_role="viewer"))).json()
    assert [(p["id"], p["role"]) for p in capped] == [("alpha", "viewer")]


async def test_status_and_health(env: Env) -> None:
    assert (await env.http.get("/healthz")).status_code == 200
    ready = await env.http.get("/readyz")
    validate_shared("Health", ready.json())
    assert ready.status_code == 200 and ready.json()["checks"] == {"memgraph": "ok", "userstore": "ok"}
    assert (await env.http.get("/v1/status")).status_code == 401
    status = await env.http.get("/v1/status", headers=service_headers("orchestrator"))
    assert status.status_code == 200 and status.json()["service"] == "query-service"
    assert status.json()["state"] == "idle"
    env.user("alice", {"alpha": "viewer"})
    assert (await env.http.get("/v1/status", headers=env.token("alice"))).status_code == 403
    assert (await env.http.get("/v1/status", headers=service_headers("plugin:stub"))).status_code == 403


# ------------------------------------------------------------------ forwarding


def sent(env: Env, path_suffix: str) -> dict:  # type: ignore[type-arg]
    return next(c for c in reversed(env.owner.calls) if c["path"].endswith(path_suffix))


async def test_owner_gets_actor_only_from_the_query_service(env: Env) -> None:
    env.user("tina", {"alpha": "triager"})
    forged = {"x-sandman-actor": "user:root", "x-sandman-roles": "admin"}
    resp = await env.http.post(
        f"/v1/inconsistencies/{INC_ID}/transitions",
        json={"to": "ACCEPTED", "expectedStatus": "OPEN", "reason": "ok"},
        headers={**env.token("tina"), **forged},
    )
    assert resp.status_code == 200
    validate("/v1/inconsistencies/{inconsistencyId}/transitions", "post", 200, resp.json())
    call = sent(env, "/transitions")
    assert call["headers"]["x-sandman-actor"] == "user:tina"
    assert "x-sandman-roles" not in call["headers"]
    assert call["headers"]["authorization"] == "Bearer qs-token"  # own service token, not the user's
    assert all("user:root" not in v for v in call["headers"].values())
    for c in env.owner.calls:  # also the detail lookup before the transition
        assert c["headers"].get("x-sandman-actor") == "user:tina"


async def test_service_callers_are_forwarded_without_actor(env: Env) -> None:
    resp = await env.http.get("/v1/projects/alpha/inconsistencies?status=OPEN&bogus=1", headers={
        **service_headers("orchestrator"), "x-sandman-actor": "user:root"})  # fmt: skip
    assert resp.status_code == 200
    validate("/v1/projects/{projectId}/inconsistencies", "get", 200, resp.json())
    call = sent(env, "/inconsistencies")
    assert "x-sandman-actor" not in call["headers"] and call["query"] == "status=OPEN"


async def test_inconsistency_of_unreadable_project_is_404_like_a_missing_one(env: Env) -> None:
    env.user("alice", {"alpha": "triager"})
    h = env.token("alice")
    hidden = await env.http.get(f"/v1/inconsistencies/{OTHER_INC_ID}", headers=h)
    missing = await env.http.get("/v1/inconsistencies/inc_" + "d" * 32, headers=h)
    assert hidden.status_code == missing.status_code == 404 and hidden.json() == missing.json()
    body = {"to": "ACCEPTED", "expectedStatus": "OPEN", "reason": "r"}
    assert (
        await env.http.post(f"/v1/inconsistencies/{OTHER_INC_ID}/transitions", json=body, headers=h)
    ).status_code == 404
    assert not any(c["method"] == "POST" for c in env.owner.calls)  # nothing was forwarded


async def test_unexpected_owner_status_on_inconsistency_lookup_is_a_generic_502(env: Env) -> None:
    env.user("tina", {"other": "viewer"})
    env.owner.force_status = 403
    resp = await env.http.get(f"/v1/inconsistencies/{INC_ID}", headers=env.token("tina"))
    assert resp.status_code == 502 and "{}" not in resp.text and "projectId" not in resp.text


async def test_owner_problems_are_passed_through(env: Env) -> None:
    env.user("tina", {"alpha": "triager"})
    env.owner.transition_status = 409
    body = {"to": "ACCEPTED", "expectedStatus": "OPEN", "reason": "r"}
    resp = await env.http.post(f"/v1/inconsistencies/{INC_ID}/transitions", json=body, headers=env.token("tina"))
    assert resp.status_code == 409 and resp.json()["detail"] == "status changed concurrently"
    assert resp.headers["content-type"].startswith("application/problem+json")


async def test_upload_is_streamed_with_original_multipart_body(env: Env) -> None:
    env.user("mia", {"alpha": "maintainer"})
    archive = b"\x1f\x8b" + bytes(range(256)) * 2000
    files = {"manifest": (None, '{"mode":"full","files":[]}', "application/json"), "archive": ("a.tar.gz", archive)}
    resp = await env.http.post("/v1/projects/alpha/ingestions", files=files, headers=env.token("mia"))
    assert resp.status_code == 201
    validate("/v1/projects/{projectId}/ingestions", "post", 201, resp.json())
    call = sent(env, "/ingestions")
    assert call["headers"]["content-type"].startswith("multipart/form-data; boundary=")
    assert archive in call["body"] and call["headers"]["x-sandman-actor"] == "user:mia"
    assert "x-sandman-roles" not in call["headers"]
    bad = await env.http.post("/v1/projects/alpha/ingestions", json={}, headers=env.token("mia"))
    assert bad.status_code == 400


async def test_workflow_forwarding_adds_project_and_validates_body(env: Env) -> None:
    env.user("mia", {"alpha": "maintainer"})
    h = env.token("mia")
    resp = await env.http.post("/v1/projects/alpha/workflows", json={"type": "tlr", "force": True}, headers=h)
    assert resp.status_code == 202
    validate("/v1/projects/{projectId}/workflows", "post", 202, resp.json())
    assert b'"projectId": "alpha"' in sent(env, "/v1/workflows")["body"]
    assert (await env.http.post("/v1/projects/alpha/workflows", json={"type": "nope"}, headers=h)).status_code == 400
    listed = await env.http.get("/v1/projects/alpha/workflows?limit=5&evil=x", headers=h)
    validate("/v1/projects/{projectId}/workflows", "get", 200, listed.json())
    assert sent(env, "/v1/workflows")["query"] == "projectId=alpha&limit=5"
    other = await env.http.get(f"/v1/workflows/{'wf_' + 'c' * 32}", headers=env.token("mia"))
    validate("/v1/workflows/{workflowId}", "get", 200, other.json())


async def test_put_project_is_forwarded_for_admins_only(env: Env) -> None:
    env.user("mia", {"alpha": "maintainer"})
    spec = {"name": "A", "llmPolicy": "local-only"}
    assert (await env.http.put("/v1/admin/projects/alpha", json=spec, headers=env.token("mia"))).status_code == 403
    env.user("root", admin=True)
    ok = await env.http.put("/v1/admin/projects/alpha", json=spec, headers=env.token("root"))
    validate("/v1/admin/projects/{projectId}", "put", 200, ok.json())
    assert sent(env, "/v1/projects/alpha")["headers"]["x-sandman-actor"] == "user:root"


async def test_owner_rejecting_our_token_and_unreachable_owners_are_503(env: Env) -> None:
    env.user("mia", {"alpha": "maintainer"})
    h = env.token("mia")
    env.owner.force_status = 401
    resp = await env.http.get("/v1/projects/alpha/workflows", headers=h)
    assert resp.status_code == 503 and resp.json()["title"] == "owner rejected the query-service token"
    env.owner.force_status = 403  # owner problems are passed through
    assert (await env.http.get("/v1/projects/alpha/workflows", headers=h)).status_code == 403

    class Down(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

    env.app.state.service.owners._transport = Down()
    env.app.state.service.owners._clients.clear()
    down = await env.http.get("/v1/projects/alpha/workflows", headers=h)
    assert down.status_code == 503


def test_build_from_environment(tmp_path: Path) -> None:
    (tmp_path / "pw").write_text("bootstrap-secret\n")
    env = {"SANDMAN_SERVICE_NAME": "query-service", "SANDMAN_SERVICE_TOKEN": "t", "SANDMAN_KG_URI": "bolt://localhost:1",
           "SANDMAN_USER_DB": str(tmp_path / "users.db"), "SANDMAN_ADMIN_PASSWORD_FILE": str(tmp_path / "pw"),
           "SANDMAN_COOKIE_SECURE": "true", "SANDMAN_EXTRACTION_URL": "http://e"}  # fmt: skip
    svc = build(env).state.service
    admin = svc.store.get_user("admin")
    assert admin["must_change_password"] and svc.cookie_secure  # type: ignore[index]
    assert svc.store.verify_password("admin", "bootstrap-secret")
    assert svc.store.db.execute("PRAGMA user_version").fetchone()[0] == 1

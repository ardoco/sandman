"""Console: sessions, CSRF, CSP, escaping and the pages."""

import hashlib
import io
import json
import re
import tarfile
from typing import Any

import httpx
from conftest import ADMIN_PASSWORD, Env
from helpers import INC_ID, TS


def csrf_of(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, html
    return match.group(1)


async def console_login(env: Env, user: str, password: str | None = None) -> httpx.Response:
    page = await env.http.get("/console/login")
    password = password or f"password-{user * 2}"
    return await env.http.post(
        "/console/login", data={"username": user, "password": password, "csrf": csrf_of(page.text)}
    )


async def logged_in(env: Env, user: str = "mia", roles: dict[str, str] | None = None, admin: bool = False) -> str:
    """Create the user, log in on the console and return the session's csrf token."""
    env.user(user, roles if roles is not None else {"alpha": "maintainer"}, admin=admin)
    assert (await console_login(env, user)).status_code == 303
    page = await env.http.get("/console/projects")
    assert page.status_code == 200
    return csrf_of(page.text)


async def post(env: Env, path: str, csrf: str | None, **data: Any) -> httpx.Response:
    return await env.http.post(path, data={**data, **({"csrf": csrf} if csrf is not None else {})})


async def test_session_cookie_flags_and_csp(env: Env) -> None:
    env.user("mia")
    page = await env.http.get("/console/login")
    assert page.headers["content-security-policy"] == "default-src 'self'"
    assert "httponly" in page.headers["set-cookie"].lower()
    resp = await console_login(env, "mia")
    assert resp.status_code == 303 and resp.headers["location"] == "/console/projects"
    cookie = next(c for c in resp.headers.get_list("set-cookie") if c.startswith("sandman_session"))
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Secure" not in cookie
    env.user("zoe")
    login = await env.http.get("/console/login")
    env.app.state.service.cookie_secure = True  # after the page: a Secure CSRF cookie is not sent over http
    zoe = await env.http.post(
        "/console/login", data={"username": "zoe", "password": "password-zoezoe", "csrf": csrf_of(login.text)}
    )
    assert any("Secure" in c for c in zoe.headers.get_list("set-cookie") if c.startswith("sandman_session"))
    projects = await env.http.get("/console/projects")
    assert projects.headers["content-security-policy"] == "default-src 'self'"


async def test_static_htmx_is_served_with_csp(env: Env) -> None:
    resp = await env.http.get("/static/htmx.min.js")
    assert resp.status_code == 200 and 'version:"2.0.4"' in resp.text
    assert resp.headers["content-security-policy"] == "default-src 'self'"
    assert "<script>" not in (await env.http.get("/console/login")).text.replace(
        '<script src="/static/htmx.min.js">', ""
    )


async def test_login_needs_csrf_and_rate_limits(env: Env) -> None:
    env.user("mia")
    body = {"username": "mia", "password": "password-miamia"}
    assert (await env.http.post("/console/login", data=body)).status_code == 403  # no token
    await env.http.get("/console/login")
    assert (await env.http.post("/console/login", data={**body, "csrf": "wrong"})).status_code == 403
    page = await env.http.get("/console/login")
    for _ in range(5):
        bad = await env.http.post("/console/login", data={**body, "password": "nope", "csrf": csrf_of(page.text)})
        assert bad.status_code == 401
    blocked = await env.http.post("/console/login", data={**body, "csrf": csrf_of(page.text)})
    assert blocked.status_code == 429


async def test_bootstrap_admin_must_change_password_before_anything_else(env: Env) -> None:
    resp = await console_login(env, "admin", ADMIN_PASSWORD)
    assert resp.status_code == 303 and resp.headers["location"] == "/console/password"
    for path in ("/console/projects", "/console/admin", "/console/tokens"):
        redirected = await env.http.get(path)
        assert redirected.status_code == 303 and redirected.headers["location"] == "/console/password"
    form = await env.http.get("/console/password")
    csrf = csrf_of(form.text)
    weak = await post(env, "/console/password", csrf, current=ADMIN_PASSWORD, new="short")
    assert weak.status_code == 400
    wrong = await post(env, "/console/password", csrf, current="nope", new="a-new-long-password")
    assert wrong.status_code == 400
    assert (
        await post(env, "/console/password", None, current=ADMIN_PASSWORD, new="a-new-long-password")
    ).status_code == 403
    done = await post(env, "/console/password", csrf, current=ADMIN_PASSWORD, new="a-new-long-password")
    assert done.status_code == 303 and done.headers["location"] == "/console/login"
    assert (await env.http.get("/console/projects")).status_code == 303  # the session was revoked
    assert (await console_login(env, "admin", ADMIN_PASSWORD)).status_code == 401
    ok = await console_login(env, "admin", "a-new-long-password")
    assert ok.headers["location"] == "/console/projects"
    assert (await env.http.get("/console/admin")).status_code == 200


async def test_forms_need_a_valid_csrf_token_and_logout_revokes_the_session(env: Env) -> None:
    csrf = await logged_in(env)
    sid = env.http.cookies["sandman_session"]
    assert (await post(env, "/console/logout", None)).status_code == 403
    assert (await post(env, "/console/logout", "wrong")).status_code == 403
    assert env.store.get_session(sid)
    out = await post(env, "/console/logout", csrf)
    assert out.status_code == 303 and out.headers["location"] == "/console/login"
    assert env.store.get_session(sid) is None
    env.http.cookies.set("sandman_session", sid)  # replaying the old cookie does not work
    assert (await env.http.get("/console/projects")).status_code == 303


async def test_expired_and_idle_sessions_are_rejected(env: Env) -> None:
    await logged_in(env)
    env.store.db.execute("UPDATE sessions SET expires_at = '2020-01-01T00:00:00Z'")
    assert (await env.http.get("/console/projects")).headers["location"] == "/console/login"
    await console_login(env, "mia")
    env.store.db.execute("UPDATE sessions SET last_seen = '2020-01-01T00:00:00Z'")
    assert (await env.http.get("/console/projects")).headers["location"] == "/console/login"


async def test_every_page_renders_for_a_member(env: Env) -> None:
    env.owner.items = [
        {
            "id": INC_ID,
            "projectId": "alpha",
            "type": "t",
            "severity": "error",
            "title": "A title",
            "status": "OPEN",
            "detector": "d",
            "createdAt": TS,
            "updatedAt": TS,
        }  # fmt: skip
    ]
    await logged_in(env, "mia", {"alpha": "maintainer"}, admin=True)
    for path in (
        "/console", "/console/projects", "/console/projects/alpha/inconsistencies",
        "/console/projects/alpha/inconsistencies?status=OPEN&severity=error&sort=updated",
        f"/console/inconsistencies/{INC_ID}", "/console/projects/alpha/upload", "/console/projects/alpha/ask",
        "/console/projects/alpha/workflows", "/console/tokens", "/console/admin",
    ):  # fmt: skip
        resp = await env.http.get(path, follow_redirects=True)
        assert resp.status_code == 200, path
        assert resp.headers["content-type"].startswith("text/html")
        assert resp.headers["content-security-policy"] == "default-src 'self'"
    assert "A title" in (await env.http.get("/console/projects/alpha/inconsistencies")).text


async def test_data_is_escaped(env: Env) -> None:
    evil = "<script>alert(1)</script>"
    element = {
        "id": "el_" + "1" * 32,
        "key": evil,
        "label": "Requirement",
        "name": evil,
        "artifactPath": evil,
        "excerpt": evil,
    }
    env.owner.detail_patch = {
        "title": evil, "description": evil, "evidence": {"x": evil},
        "involved": [{"role": evil, "element": element}],
        "history": [{"from": "OPEN", "to": "REJECTED", "actor": "user:x", "reason": evil, "at": TS}],
    }  # fmt: skip
    await logged_in(env)
    page = (await env.http.get(f"/console/inconsistencies/{INC_ID}")).text
    assert evil not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert page.count("<script") == 1  # only the vendored htmx
    env.reader.result = (["x"], [[evil]])
    ask = await env.http.post(
        "/console/projects/alpha/ask", data={"question": "untraced requirements", "csrf": csrf_of(page)}
    )
    assert ask.status_code == 200 and evil not in ask.text and "&lt;script&gt;" in ask.text


async def test_transition_form_posts_with_csrf_and_shows_conflicts(env: Env) -> None:
    csrf = await logged_in(env, "tina", {"alpha": "triager"})
    page = await env.http.get(f"/console/inconsistencies/{INC_ID}")
    assert (
        "Change status" in page.text and 'value="ACCEPTED"' in page.text and f'name="csrf" value="{csrf}"' in page.text
    )
    path = f"/console/inconsistencies/{INC_ID}/transition"
    fields = {"to": "ACCEPTED", "expectedStatus": "OPEN", "reason": "intended"}
    assert (await post(env, path, None, **fields)).status_code == 403
    assert (await post(env, path, "wrong", **fields)).status_code == 403
    assert not any(c["method"] == "POST" for c in env.owner.calls)
    missing = await post(env, path, csrf, to="REJECTED", expectedStatus="OPEN")
    assert missing.status_code == 400 and "reason is required" in missing.text
    done = await post(env, path, csrf, **fields)
    assert done.status_code == 303 and done.headers["location"] == f"/console/inconsistencies/{INC_ID}"
    sent = next(c for c in env.owner.calls if c["method"] == "POST")
    assert json.loads(sent["body"]) == {"to": "ACCEPTED", "expectedStatus": "OPEN", "reason": "intended"}
    assert sent["headers"]["x-sandman-actor"] == "user:tina" and "x-sandman-roles" not in sent["headers"]
    env.owner.transition_status = 409
    conflict = await post(env, path, csrf, **fields)
    assert conflict.status_code == 409 and "changed concurrently" in conflict.text


async def test_viewer_sees_no_transition_form_and_cannot_post(env: Env) -> None:
    csrf = await logged_in(env, "vic", {"alpha": "viewer"})
    assert "Change status" not in (await env.http.get(f"/console/inconsistencies/{INC_ID}")).text
    resp = await post(
        env, f"/console/inconsistencies/{INC_ID}/transition", csrf, to="ACCEPTED", expectedStatus="OPEN", reason="r"
    )
    assert resp.status_code == 403
    assert (await env.http.get("/console/admin")).status_code == 403
    assert (await env.http.get("/console/projects/alpha/upload")).status_code == 403
    assert (await post(env, "/console/projects/alpha/workflows", csrf, type="full")).status_code == 403


async def test_unreadable_projects_are_404(env: Env) -> None:
    csrf = await logged_in(env, "sam", {"beta": "viewer"})
    pages = ("inconsistencies", "ask", "workflows", "upload")
    for path in (f"/console/projects/alpha/{p}" for p in pages):
        assert (await env.http.get(path)).status_code == 404, path
    other = env.owner.inconsistencies  # INC_ID belongs to alpha
    assert INC_ID in other and (await env.http.get(f"/console/inconsistencies/{INC_ID}")).status_code == 404
    assert (await post(env, "/console/projects/alpha/ask", csrf, question="open inconsistencies")).status_code == 404


async def test_last_visit_drives_the_new_badge(env: Env) -> None:
    env.owner.items = [
        {
            "id": INC_ID,
            "projectId": "alpha",
            "type": "t",
            "severity": "error",
            "title": "T",
            "status": "OPEN",
            "detector": "d",
            "createdAt": TS,
            "updatedAt": "2999-01-01T00:00:00Z",
        }  # fmt: skip
    ]
    await logged_in(env, "mia", {"alpha": "viewer"})
    assert 'class="badge"' not in (await env.http.get("/console/projects")).text  # never visited
    await env.http.get("/console/projects/alpha/inconsistencies")
    assert "alpha" in env.store.last_visits("mia")
    page = (await env.http.get("/console/projects")).text
    assert '<span class="badge">1</span>' in page
    assert env.owner.calls[-1]["query"] == "limit=1" or any("changedSince" in c["query"] for c in env.owner.calls)


def archive(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


async def test_upload_builds_the_manifest_and_streams_the_archive(env: Env) -> None:
    csrf = await logged_in(env)
    files = {"requirements/r.md": b"# R1\n", "src/main/A.java": b"class A {}"}
    data = archive(files)
    rules = "requirements/** = requirements:markdown\nsrc/** = code"
    resp = await env.http.post(
        "/console/projects/alpha/upload",
        data={"csrf": csrf, "mode": "full", "sourceRevision": "rev9", "kindRules": rules},
        files={"archive": ("a.tar.gz", data, "application/gzip")},
    )
    assert resp.status_code == 200 and "Upload completed" in resp.text
    call = next(c for c in env.owner.calls if c["path"].endswith("/ingestions"))
    assert data in call["body"] and call["headers"]["x-sandman-actor"] == "user:mia"
    manifest = re.search(rb'name="manifest".*?\r\n\r\n(\{.*?\})\r\n--', call["body"], re.S)
    assert manifest
    parsed = json.loads(manifest.group(1))
    assert parsed["mode"] == "full" and parsed["sourceRevision"] == "rev9"
    assert parsed["files"] == [{"path": p, "sha256": hashlib.sha256(files[p]).hexdigest()} for p in sorted(files)]
    assert parsed["kindRules"][0] == {"glob": "requirements/**", "kind": "requirements", "format": "markdown"}
    bad_rule = await env.http.post(
        "/console/projects/alpha/upload", data={"csrf": csrf, "kindRules": "x = nonsense"},
        files={"archive": ("a.tar.gz", data)},
    )  # fmt: skip
    assert bad_rule.status_code == 400 and "invalid kind rule" in bad_rule.text
    not_archive = await env.http.post(
        "/console/projects/alpha/upload", data={"csrf": csrf}, files={"archive": ("a", b"plain text")}
    )
    assert not_archive.status_code == 400
    no_csrf = await env.http.post("/console/projects/alpha/upload", files={"archive": ("a.tar.gz", data)})
    assert no_csrf.status_code == 403


async def test_ask_shows_cypher_and_rows(env: Env) -> None:
    csrf = await logged_in(env, "vic", {"alpha": "viewer"})
    env.reader.result = (["key", "name"], [["a#R1", "R1"]])
    resp = await post(env, "/console/projects/alpha/ask", csrf, question="Untraced requirements?")
    assert resp.status_code == 200 and "a#R1" in resp.text and "$projectId" in resp.text
    unknown = await post(env, "/console/projects/alpha/ask", csrf, question="why is the sky blue")
    assert unknown.status_code == 422 and "not understood" in unknown.text
    assert (await post(env, "/console/projects/alpha/ask", None, question="open inconsistencies")).status_code == 403


async def test_workflows_trigger_for_maintainers(env: Env) -> None:
    csrf = await logged_in(env)
    page = await env.http.get("/console/projects/alpha/workflows")
    assert "RUNNING" in page.text and 'value="detect"' in page.text
    done = await post(env, "/console/projects/alpha/workflows", csrf, type="detect")
    assert done.status_code == 303
    sent = next(c for c in env.owner.calls if c["method"] == "POST")
    assert json.loads(sent["body"]) == {"projectId": "alpha", "type": "detect", "force": False}
    assert (await post(env, "/console/projects/alpha/workflows", csrf, type="nonsense")).status_code == 400
    assert (await post(env, "/console/projects/alpha/workflows", None, type="full")).status_code == 403


async def test_token_management(env: Env) -> None:
    csrf = await logged_in(env, "mia", {"alpha": "viewer"})
    created = await post(env, "/console/tokens", csrf, name="ci", projectId="alpha", maxRole="viewer", expiresAt="")
    assert created.status_code == 200
    secret = re.search(r"<code>(sdm_[^<]+)</code>", created.text)
    assert secret
    me = await env.http.get("/v1/auth/me", headers={"authorization": f"Bearer {secret.group(1)}"})
    assert me.status_code == 200
    assert secret.group(1) not in (await env.http.get("/console/tokens")).text  # shown once
    assert (await post(env, "/console/tokens", csrf, name="")).status_code == 400
    assert (await post(env, "/console/tokens", csrf, name="x", expiresAt="tomorrow")).status_code == 400
    token_id = env.store.list_tokens("mia")[0]["id"]
    assert (await post(env, f"/console/tokens/{token_id}/revoke", None)).status_code == 403
    assert (await post(env, f"/console/tokens/{token_id}/revoke", csrf)).status_code == 303
    assert (
        await env.http.get("/v1/auth/me", headers={"authorization": f"Bearer {secret.group(1)}"})
    ).status_code == 401


async def test_administration(env: Env) -> None:
    csrf = await logged_in(env, "root", {}, admin=True)
    made = await post(env, "/console/admin/users", csrf, username="newbie", password="long-enough-pw")
    assert made.status_code == 303
    assert env.store.get_user("newbie")["must_change_password"]  # type: ignore[index]
    assert (
        await post(env, "/console/admin/users", csrf, username="newbie", password="long-enough-pw")
    ).status_code == 409
    assert (await post(env, "/console/admin/users", csrf, username="x", password="short")).status_code == 400
    assert (
        await post(env, "/console/admin/members", csrf, username="newbie", project="alpha", role="triager")
    ).status_code == 303
    assert env.store.memberships("newbie") == {"alpha": "triager"}
    assert "newbie" in (await env.http.get("/console/admin")).text
    token = env.token("newbie")
    assert (await env.http.get("/v1/auth/me", headers=token)).status_code == 403  # must change the password first
    env.store.update_user("newbie", must_change=False)
    assert (await env.http.get("/v1/auth/me", headers=token)).status_code == 200
    assert (await post(env, "/console/admin/users/newbie/disabled", csrf, disabled="true")).status_code == 303
    assert (await env.http.get("/v1/auth/me", headers=token)).status_code == 401
    assert (
        await post(env, "/console/admin/members/remove", csrf, username="newbie", project="alpha")
    ).status_code == 303
    assert env.store.memberships("newbie") == {}
    assert (
        await post(env, "/console/admin/users", None, username="evil", password="long-enough-pw")
    ).status_code == 403
    await post(env, "/console/logout", csrf)
    csrf2 = await logged_in(env, "plain", {"alpha": "viewer"})
    assert (
        await post(env, "/console/admin/users", csrf2, username="evil", password="long-enough-pw")
    ).status_code == 403
    assert env.store.get_user("evil") is None


async def test_static_console_css_is_served(env: Env) -> None:
    assert (await env.http.get("/static/console.css")).status_code == 200
    assert 'class="auth card"' in (await env.http.get("/console/login")).text

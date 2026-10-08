import httpx
import pytest
from dash_fakes import ADMIN_PASSWORD, Env, FakeGraph, FakeKg, Upstream, make_app
from sandman_dashboard.store import SessionStore

HTML_ROUTES = ["/", "/graph", "/gateway"]
JSON_ROUTES = [
    "/fragments/services",
    "/fragments/gateway",
    "/api/services",
    "/api/gateway",
    "/api/graph/projects",
    "/api/graph/start",
    "/api/graph/search?q=x",
    "/api/graph/expand?id=1",
    "/api/graph/path?source=1&target=2",
]


@pytest.mark.parametrize("path", HTML_ROUTES)
async def test_html_routes_redirect_to_login(env: Env, path: str) -> None:
    resp = await env.http.get(path)
    assert resp.status_code == 303 and resp.headers["location"] == "/login"


@pytest.mark.parametrize("path", JSON_ROUTES)
async def test_json_routes_return_401(env: Env, path: str) -> None:
    resp = await env.http.get(path)
    assert resp.status_code == 401 and resp.json() == {"error": "authentication required"}


async def test_post_routes_need_a_session(env: Env) -> None:
    assert (await env.http.post("/api/cypher", json={"cypher": "RETURN 1"})).status_code == 401
    assert (await env.http.post("/logout", data={"csrf": "x"})).status_code == 303


async def test_public_routes(env: Env) -> None:
    assert (await env.http.get("/healthz")).status_code == 200
    assert (await env.http.get("/readyz")).status_code == 200
    assert (await env.http.get("/login")).status_code == 200
    assert (await env.http.get("/static/dashboard.css")).status_code == 200
    env.kg.up = False
    resp = await env.http.get("/readyz")
    assert resp.status_code == 503 and resp.json()["checks"] == {"memgraph": "down", "sessionstore": "ok"}


async def test_login_sets_cookie_with_flags(env: Env) -> None:
    resp = await env.login()
    assert resp.status_code == 303 and resp.headers["location"] == "/"
    cookie = next(c for c in resp.headers.get_list("set-cookie") if c.startswith("sandman_dashboard_session="))
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie and "Secure" not in cookie
    assert (await env.http.get("/")).status_code == 200


async def test_cookie_secure_flag() -> None:
    upstream, graph = Upstream(), FakeGraph()
    app, _ = make_app(upstream, FakeKg(), graph, cookie_secure=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dashboard") as http:
        env = Env(http, app, FakeKg(), graph, upstream)
        resp = await env.login()
    assert all("Secure" in c for c in resp.headers.get_list("set-cookie") if c.startswith("sandman_dashboard_session"))


async def test_wrong_credentials_and_missing_csrf(env: Env) -> None:
    resp = await env.login("wrong")
    assert resp.status_code == 401 and "sandman_dashboard_session" not in resp.headers.get("set-cookie", "")
    assert (await env.login(username="root")).status_code == 401
    assert (await env.http.post("/login", data={"username": "admin", "password": ADMIN_PASSWORD})).status_code == 403
    assert (
        await env.http.post("/login", data={"csrf": "bad", "username": "admin", "password": ADMIN_PASSWORD})
    ).status_code == 403


async def test_sixth_failed_login_is_rate_limited(env: Env) -> None:
    for _ in range(5):
        assert (await env.login("wrong")).status_code == 401
    resp = await env.login()
    assert resp.status_code == 429 and resp.headers["retry-after"] == "900"


async def test_csrf_required_on_post(env: Env) -> None:
    await env.login()
    assert (await env.http.post("/logout")).status_code == 403
    assert (await env.http.post("/logout", data={"csrf": "wrong"})).status_code == 403
    assert (await env.http.post("/api/cypher", json={"cypher": "RETURN 1"})).status_code == 403
    assert (
        await env.http.post("/api/cypher", json={"cypher": "RETURN 1"}, headers={"x-csrf-token": "wrong"})
    ).status_code == 403
    assert (await env.http.get("/")).status_code == 200


async def test_cypher_endpoint_with_csrf(env: Env) -> None:
    await env.login()
    headers = {"x-csrf-token": await env.csrf()}
    ok = await env.http.post("/api/cypher", json={"cypher": "RETURN 1"}, headers=headers)
    assert ok.status_code == 200 and env.graph.statements == ["RETURN 1"]
    bad = await env.http.post("/api/cypher", json={"cypher": "boom"}, headers=headers)
    assert bad.status_code == 422 and bad.json() == {"error": "query rejected"}
    assert (await env.http.post("/api/cypher", json={"cypher": ""}, headers=headers)).status_code == 422
    assert (await env.http.post("/api/cypher", content=b"{", headers=headers)).status_code == 400


async def test_logout_revokes_session(env: Env) -> None:
    await env.login()
    resp = await env.http.post("/logout", data={"csrf": await env.csrf()})
    assert resp.status_code == 303 and resp.headers["location"] == "/login"
    env.http.cookies.clear()
    env.http.cookies.set("sandman_dashboard_session", "x")
    assert (await env.http.get("/")).status_code == 303


async def test_revoked_session_stays_revoked(env: Env) -> None:
    await env.login()
    cookie = env.http.cookies["sandman_dashboard_session"]
    await env.http.post("/logout", data={"csrf": await env.csrf()})
    env.http.cookies.set("sandman_dashboard_session", cookie)
    assert (await env.http.get("/api/services")).status_code == 401


@pytest.mark.parametrize("column", ["expires_at", "last_seen"])
async def test_session_expiry_is_honored(env: Env, column: str) -> None:
    await env.login()
    store: SessionStore = env.app.state.store
    store.db.execute(f"UPDATE sessions SET {column} = '2000-01-01T00:00:00Z'")
    assert (await env.http.get("/")).status_code == 303
    assert (await env.http.get("/api/services")).status_code == 401


async def test_only_hash_of_session_id_is_stored(env: Env) -> None:
    await env.login()
    sid = env.http.cookies["sandman_dashboard_session"]
    store: SessionStore = env.app.state.store
    stored = [r["id_sha256"] for r in store.db.execute("SELECT id_sha256 FROM sessions")]
    assert len(stored) == 1 and sid not in stored[0] and len(stored[0]) == 64


async def test_security_headers(env: Env) -> None:
    for path in ("/login", "/static/htmx.min.js", "/healthz"):
        resp = await env.http.get(path)
        assert resp.headers["content-security-policy"] == "default-src 'self'"
        assert resp.headers["x-content-type-options"] == "nosniff"
    await env.login()
    assert (await env.http.get("/")).headers["content-security-policy"] == "default-src 'self'"

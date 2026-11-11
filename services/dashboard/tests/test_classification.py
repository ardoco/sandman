import json
from typing import Any

from dash_fakes import Env
from sandman_agent import CLASSIFIER_VERSION

ROWS = [
    ["docs/a.md", "documentation", "markdown", "heuristic", "docs dir", True, CLASSIFIER_VERSION],
    ["src/B.java", "code", "java", "heuristic", "suffix .java", False, CLASSIFIER_VERSION],
    ["old.txt", "other", None, None, None, None, None],
]


def seed(env: Env) -> None:
    env.graph.fixed_rows["a.kindWeak"] = ROWS
    env.graph.fixed_rows["i.kindCounts"] = [[4, "2026-01-01T00:00:00Z", '{"code": 1}', ["version mismatch"]]]


async def post(env: Env, body: Any, csrf: str | None = None) -> Any:
    token = csrf if csrf is not None else await env.csrf("/classification?project=demo")
    return await env.http.post("/api/classification/rules", content=json.dumps(body), headers={"x-csrf-token": token})


async def test_page_renders_with_csp_nav_and_escaping(env: Env) -> None:
    resp = await env.http.get("/classification?project=demo")
    assert resp.status_code == 303 and resp.headers["location"] == "/login"
    await env.login()
    resp = await env.http.get("/classification?project=demo")
    assert resp.status_code == 200
    assert 'id="cl-app" data-project="demo"' in resp.text and "/static/classification.js" in resp.text
    assert resp.headers["content-security-policy"].startswith("default-src 'self'")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in resp.text and "<script>alert" not in resp.text
    assert '<a href="/classification"' in (await env.http.get("/")).text
    assert (await env.http.get("/classification?project=a b")).status_code == 422


async def test_api_requires_session_and_valid_project(env: Env) -> None:
    assert (await env.http.get("/api/classification?project=demo")).status_code == 401
    await env.login()
    assert (await env.http.get("/api/classification")).status_code == 422
    assert (await env.http.get("/api/classification?project=a b")).status_code == 422


async def test_api_shape_anchoring_and_absent_props(env: Env) -> None:
    await env.login()
    seed(env)
    data = (await env.http.get("/api/classification?project=demo")).json()
    assert data["kinds"] == {"code": 1, "documentation": 1, "other": 1}
    assert data["ingestion"] == {
        "seq": 4,
        "createdAt": "2026-01-01T00:00:00Z",
        "kindCounts": {"code": 1},
        "warnings": ["version mismatch"],
    }
    assert data["classifierVersions"] == [CLASSIFIER_VERSION] and data["outdated"] is False
    assert data["weak"] == 1 and data["files"] == 3
    old = next(c for c in data["tree"]["children"] if c["name"] == "old.txt")
    assert old["source"] == "unknown" and old["weak"] is False
    assert env.graph.statements and all("{projectId: $p}" in s for s in env.graph.statements)


async def test_api_empty_project(env: Env) -> None:
    await env.login()
    data = (await env.http.get("/api/classification?project=demo")).json()
    assert data["files"] == 0 and data["ingestion"] is None and data["tree"]["children"] == []


async def test_rules_need_session_csrf_and_valid_body(env: Env) -> None:
    body = {"project": "demo", "edits": []}
    assert (await post(env, body, csrf="x")).status_code == 401
    await env.login()
    assert (await post(env, body, csrf="wrong")).status_code == 403
    for bad in (
        {"project": "demo", "edits": [{"path": "a", "kind": "bogus"}]},
        {"project": "demo", "edits": [{"path": "../a", "kind": "code"}]},
        {"project": "demo", "edits": [{"path": "/a", "kind": "code"}]},
        {"project": "demo", "edits": "x"},
        {"project": "a b", "edits": []},
        {"project": "demo", "edits": [], "yaml": 3},
        [],
    ):
        assert (await post(env, bad)).status_code == 422, bad
    resp = await env.http.post("/api/classification/rules", content="{", headers={"x-csrf-token": await env.csrf("/")})
    assert resp.status_code == 400
    big = await env.http.post(
        "/api/classification/rules", content=b"x" * (2 * 1024 * 1024), headers={"x-csrf-token": await env.csrf("/")}
    )
    assert big.status_code == 413


async def test_rules_block_diff_and_file_text(env: Env) -> None:
    await env.login()
    seed(env)
    existing = "kindRules: []\n"
    resp = await post(
        env,
        {
            "project": "demo",
            "edits": [{"path": "docs/a.md", "kind": "ignored"}, {"path": "src", "kind": "test"}],
            "yaml": existing,
        },
    )
    assert resp.status_code == 200
    out = resp.json()
    assert out["rules"] == [
        {"glob": "docs/a.md", "kind": "ignored"},
        {"glob": "src/**", "kind": "test"},
    ]
    assert 'reviewedRules:\n  - { glob: "docs/a.md", kind: ignored }' in out["block"]
    assert out["fileText"].startswith(existing) and out["fileText"].endswith(out["block"])
    assert "+++ sandman.yaml" in out["diff"] and out["reviewed"]
    again = await post(env, {"project": "demo", "edits": [], "yaml": out["fileText"]})
    assert again.json()["fileText"] == out["fileText"] and again.json()["diff"] == ""


async def test_rules_reject_broken_markers_and_store_nothing(env: Env) -> None:
    await env.login()
    seed(env)
    broken = "# >>> sandman:classification >>> BEGIN MANAGED BLOCK\n"
    assert (await post(env, {"project": "demo", "edits": [], "yaml": broken})).status_code == 422
    await post(env, {"project": "demo", "edits": [{"path": "src", "kind": "test"}]})
    assert all(s.lstrip().upper().startswith("MATCH") for s in env.graph.statements)

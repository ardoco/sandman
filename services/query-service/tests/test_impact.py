"""Change impact report of one ingestion: traversal rules, workflow state, roles and the response contract."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import Env
from helpers import TS, validate
from sandman_query import impact

ING = "ing_" + "1" * 32
URL = f"/v1/projects/alpha/ingestions/{ING}/impact"


def el(key: str, hash_: str = "h", version: str = "v2") -> dict[str, Any]:
    return {"version": version, "id": f"id:{key}", "key": key, "hash": hash_, "kind": "section", "label": "Requirement"}


def current(key: str, path: str) -> dict[str, Any]:
    return {"id": f"id:{key}", "key": key, "path": path, "kind": "class", "name": key, "text": "x" * 400,
            "label": "CodeElement"}  # fmt: skip


def seed(env: Env, neighbours: list[dict[str, Any]] | None = None) -> None:
    env.reader.tx_data = {
        impact.INGESTION: [{"seq": 2, "revision": "abc", "at": TS}],
        impact.ACTIVATED: [{"artifact": "art1", "path": "req.md", "new": "v2", "old": "v1"}],
        impact.ELEMENTS: [
            el("art1#a", "old", "v1"),
            el("art1#a", "new"),
            el("art1#same"),
            el("art1#same", version="v1"),
            el("art1#gone", version="v1"),
        ],  # fmt: skip
        impact.NEIGHBOURS: neighbours
        if neighbours is not None
        else [
            {"src": "id:art1#a", "key": "art2#c", "edge": "TRACE_LINK"},
            {"src": "id:art1#a", "key": "art1#same", "edge": "CONTAINS"},
        ],
        impact.CURRENT: [current("art2#c", "src/C.java"), current("art1#same", "req.md")],
    }
    env.owner.workflows = [{"trigger": {"kind": "event", "eventId": str(uuid.uuid5(uuid.NAMESPACE_URL, ING))},
                            "status": "SUCCEEDED"}]  # fmt: skip


async def get(env: Env, query: str = "", user: str = "alice") -> Any:
    return await env.http.get(URL + query, headers=env.token(user))


async def test_report_lists_changes_and_cross_artifact_impact(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    seed(env)
    resp = await get(env)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate("/v1/projects/{projectId}/ingestions/{ingestionId}/impact", "get", 200, body)
    assert body["workflowStatus"] == "SUCCEEDED" and body["complete"] and not body["truncated"]
    assert [(c["key"], c["change"]) for c in body["changed"]] == [("art1#a", "modified"), ("art1#gone", "removed")]
    [hit] = body["affected"]
    assert hit["key"] == "art2#c" and hit["distance"] == 1 and len(hit["excerpt"]) == impact.EXCERPT
    assert hit["paths"] == [[{"key": "art1#a", "edge": "start"}, {"key": "art2#c", "edge": "TRACE_LINK"}]]


async def test_ingestion_without_changes_has_no_workflow(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    env.reader.tx_data = {impact.INGESTION: [{"seq": 1, "revision": None, "at": TS}]}
    body = (await get(env)).json()
    validate("/v1/projects/{projectId}/ingestions/{ingestionId}/impact", "get", 200, body)
    assert body["workflowStatus"] == "none" and body["complete"] and body["changed"] == []
    assert env.owner.calls == []


@pytest.mark.parametrize(("status", "complete"), [("RUNNING", False), ("FAILED", True)])
async def test_complete_follows_workflow_status(env: Env, status: str, complete: bool) -> None:
    env.user("alice", {"alpha": "viewer"})
    seed(env)
    env.owner.workflows[0]["status"] = status  # type: ignore[index]
    body = (await get(env)).json()
    assert body["workflowStatus"] == status and body["complete"] is complete


async def test_unreachable_orchestrator_is_unknown_and_incomplete(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    seed(env)
    env.owner.force_status = 503
    body = (await get(env)).json()
    assert body["workflowStatus"] == "unknown" and not body["complete"]
    assert any("unknown" in w for w in body["warnings"])


async def test_missing_workflow_is_pending_only_during_grace(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    seed(env)
    env.owner.workflows = []
    assert (await get(env)).json()["workflowStatus"] == "none"
    env.reader.tx_data[impact.INGESTION][0]["at"] = datetime.now(UTC).isoformat()
    assert (await get(env)).json()["workflowStatus"] == "PENDING"


async def test_unknown_ingestion_is_404_problem(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    resp = await get(env)
    assert resp.status_code == 404


@pytest.mark.parametrize("query", ["?depth=0", "?depth=5", "?limit=0", "?limit=1001"])
async def test_bad_bounds_are_400(env: Env, query: str) -> None:
    env.user("alice", {"alpha": "viewer"})
    seed(env)
    assert (await get(env, query)).status_code == 400


async def test_bad_ingestion_id_is_rejected(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    resp = await env.http.get("/v1/projects/alpha/ingestions/x/impact", headers=env.token("alice"))
    assert resp.status_code in (400, 404)


async def test_roles_and_projects(env: Env) -> None:
    env.user("bob", {"beta": "viewer"})
    seed(env)
    assert (await get(env, user="bob")).status_code in (403, 404)
    assert (await env.http.get(URL)).status_code == 401


async def test_queries_are_parameterized_and_project_anchored(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    seed(env)
    await get(env)
    assert env.reader.queries
    for cypher, params in env.reader.queries:
        assert params["p"] == "alpha" and "alpha" not in cypher and ING not in cypher
        assert "$p" in cypher


async def test_depth_limit_and_same_artifact_filter() -> None:
    async def rows(cypher: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if cypher == impact.NEIGHBOURS:
            edges = {
                "id:art1#a": [("art2#b", "TRACE_LINK")],
                "id:art2#b": [("art3#c", "DEPENDS_ON"), ("art1#z", "CONTAINS")],
            }
            return [{"src": i, "key": k, "edge": e} for i in params["ids"] for k, e in edges.get(i, [])]
        return [current(k, k.split("#")[0]) for k in params["keys"]]

    one, _ = await impact.traverse(rows, "alpha", {"art1#a": ("id:art1#a", False)}, 1, 10)
    assert [a["key"] for a in one] == ["art2#b"]
    two, _ = await impact.traverse(rows, "alpha", {"art1#a": ("id:art1#a", False)}, 2, 10)
    keys = {a["key"]: a for a in two}
    assert keys["art3#c"]["paths"][0] == [
        {"key": "art1#a", "edge": "start"}, {"key": "art2#b", "edge": "TRACE_LINK"},
        {"key": "art3#c", "edge": "DEPENDS_ON"},
    ]  # fmt: skip
    assert keys["art2#b"]["distance"] == 1 and keys["art3#c"]["distance"] == 2 and "art1#z" not in keys
    capped, truncated = await impact.traverse(rows, "alpha", {"art1#a": ("id:art1#a", False)}, 2, 1)
    assert len(capped) == 1 and truncated


async def test_removed_element_marks_previous_version() -> None:
    async def rows(cypher: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if cypher == impact.NEIGHBOURS:
            assert params["old"] == ["id:art1#gone"]
            return [{"src": "id:art1#gone", "key": "art2#c", "edge": "TRACE_LINK"}]
        return [current("art2#c", "C.java")]

    [hit], _ = await impact.traverse(rows, "alpha", {"art1#gone": ("id:art1#gone", True)}, 1, 10)
    assert hit["paths"][0][0] == {"key": "art1#gone", "edge": "start", "via": "previous-version"}


def test_change_set() -> None:
    old = {"a": {"hash": "1"}, "b": {"hash": "1"}, "c": {"hash": "1"}}
    new = {"a": {"hash": "1"}, "b": {"hash": "2"}, "d": {"hash": "1"}}
    assert [(c, e) for c, e in impact.change_set(old, new)] == [
        ("modified", new["b"]), ("added", new["d"]), ("removed", old["c"])
    ]  # fmt: skip

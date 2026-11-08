"""Trace viewer: project anchoring of every statement, grouping, truncation, 404 and the response contract."""

from typing import Any

from conftest import Env
from helpers import validate
from sandman_query import traceview

EL = "el_" + "1" * 32
URL = "/v1/projects/alpha/trace-view"


def element_row(i: int, **kw: Any) -> dict[str, Any]:
    return {"artifactId": "art1", "artifactPath": "req.md", "id": f"el_{i:032x}", "key": f"art1#{i}",
            "label": "Requirement", "kind": "functional", "name": f"R{i}", "excerpt": "x", "parentId": None,
            "sentenceIndex": None, "startLine": None, "inferred": None} | kw  # fmt: skip


def link_row(i: int) -> dict[str, Any]:
    return {"id": f"tl_{i:032x}", "sourceId": f"el_{1:032x}", "targetId": f"el_{2:032x}", "linkType": "documents",
            "approach": "a", "confidence": 0.5, "status": "candidate", "reviewedBy": None}  # fmt: skip


def finding_row(inc: str, target: str, role: str) -> dict[str, Any]:
    return {"id": inc, "title": "t", "severity": "warning", "status": "OPEN", "type": "x", "role": role,
            "involvedId": target}  # fmt: skip


async def test_view_groups_findings_and_validates(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    env.reader.tx_data = {
        traceview.ELEMENTS: [element_row(1, sentenceIndex=4), element_row(2, startLine=7, inferred=True)],
        traceview.LINKS: [link_row(1)],
        traceview.FINDINGS: [
            finding_row("inc_a", f"el_{1:032x}", "subject"),
            finding_row("inc_a", f"tl_{1:032x}", "link"),
        ],
    }
    resp = await env.http.get(URL, headers=env.token("alice"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate("/v1/projects/{projectId}/trace-view", "get", 200, body)
    assert [e["order"] for e in body["elements"]] == [4, 7]
    assert "parentId" not in body["elements"][0] and body["elements"][1]["inferred"] is True
    assert body["links"][0]["id"] == f"tl_{1:032x}" and "reviewedBy" not in body["links"][0]
    assert len(body["findings"]) == 1 and len(body["findings"][0]["involved"]) == 2
    assert body["truncated"] is False
    assert env.reader.queries and all(q[1]["p"] == "alpha" and "projectId: $p" in q[0] for q in env.reader.queries)


async def test_view_truncates_at_cap(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    env.reader.tx_data = {traceview.ELEMENTS: [element_row(i) for i in range(traceview.MAX_ELEMENTS + 1)]}
    body = (await env.http.get(URL, headers=env.token("alice"))).json()
    assert len(body["elements"]) == traceview.MAX_ELEMENTS and body["truncated"] is True
    env.reader.tx_data = {traceview.LINKS: [link_row(i) for i in range(traceview.MAX_LINKS + 1)]}
    body = (await env.http.get(URL, headers=env.token("alice"))).json()
    assert len(body["links"]) == traceview.MAX_LINKS and body["truncated"] is True


async def test_element_detail_validates(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    env.reader.tx_data = {
        traceview.ELEMENT: [{"props": {"id": EL, "key": "art1#1", "name": "R", "embedding": [0.1]},
                             "label": "Requirement", "artifactId": "art1", "artifactPath": "req.md"}],
        traceview.ELEMENT_LINKS: [link_row(1) | {"rationale": "why", "reviewedAt": None, "reviewReason": None}],
        traceview.ELEMENT_FINDINGS: [{"id": "inc_a", "title": "t", "severity": "error", "status": "OPEN",
                                      "type": "x", "description": None, "evidence": "{}", "role": "subject"}],
        traceview.NEIGHBOURS: [
            {"relation": "CONTAINS", "out": True, "id": "el_" + "2" * 32, "name": "C", "label": "TextUnit"},
            {"relation": "DEPENDS_ON", "out": False, "id": "el_" + "3" * 32, "name": None, "label": "CodeUnit"},
        ],
    }  # fmt: skip
    resp = await env.http.get(f"{URL}/elements/{EL}", headers=env.token("alice"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate("/v1/projects/{projectId}/trace-view/elements/{elementId}", "get", 200, body)
    assert "embedding" not in body["element"] and body["element"]["artifactPath"] == "req.md"
    assert [n["direction"] for n in body["neighbours"]] == ["out", "in"]
    assert body["links"][0]["rationale"] == "why" and "reviewedAt" not in body["links"][0]
    assert all(q[1]["p"] == "alpha" and q[1]["id"] == EL and "projectId: $p" in q[0] for q in env.reader.queries)


async def test_unknown_element_is_404_and_bad_id_is_400(env: Env) -> None:
    env.user("alice", {"alpha": "viewer"})
    resp = await env.http.get(f"{URL}/elements/{EL}", headers=env.token("alice"))
    assert resp.status_code == 404
    assert (await env.http.get(f"{URL}/elements/bad", headers=env.token("alice"))).status_code == 400

"""Trace-link review (T6.1) and triage cause (T6.2) against a running stack: `make e2e` runs it after the console test.

Same environment as test_walking_skeleton.py. The test ingests only the recorded mini snapshots (s1, s2), so strict
LLM replay needs no new cassette entries. Every run uses its own project.
"""

import csv
import io
import os
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from test_console_graph import admin_password
from test_walking_skeleton import CSRF, URL, Stack

pytestmark = pytest.mark.skipif(not os.environ.get("SANDMAN_E2E"), reason="set SANDMAN_E2E=1 with the stack running")

LINKS = (
    "MATCH (t:TraceLink {projectId: $projectId, active: true})-[:SOURCE]->(s)<-[:HAS_ELEMENT]-(:ArtifactVersion)"
    "<-[:CURRENT_VERSION]-(:Artifact), (t)-[:TARGET]->(g)<-[:HAS_ELEMENT]-(:ArtifactVersion)"
    "<-[:CURRENT_VERSION]-(:Artifact) "
    "WHERE s.key ENDS WITH $req RETURN t.id, t.approach, g.key, t.status, t.reviewOrigin, t.reviewedBy, t.reviewReason "
    "ORDER BY t.id"
)
OPEN_STATES = {"OPEN", "REOPENED"}


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    health = httpx.get(f"{URL}/readyz", timeout=10)
    assert health.status_code == 200, f"stack not ready at {URL}: {health.text}"
    yield Stack()


@pytest.fixture(scope="module")
def console() -> Iterator[httpx.Client]:
    http = httpx.Client(base_url=URL, timeout=60)
    csrf = CSRF.search(http.get("/console/login").text)
    assert csrf
    login = http.post("/console/login", data={"csrf": csrf[1], "username": "admin", "password": admin_password()})
    assert login.status_code == 303, login.text
    yield http
    http.close()


def links(stack: Stack, req: str) -> list[dict[str, Any]]:
    keys = ("id", "approach", "target", "status", "origin", "by", "reason")
    result = stack.cypher("alice", LINKS, params={"req": f"#{req}"})
    assert result.status_code == 200, result.text
    return [dict(zip(keys, row, strict=True)) for row in result.json()["rows"]]


def reviewable(stack: Stack, req: str) -> dict[str, dict[str, Any]]:
    """One reviewable link per target element of the requirement (explicit-reference links are not reviewable)."""
    return {link["target"]: link for link in links(stack, req) if link["approach"] != "explicit-reference"}


def post_form(console: httpx.Client, page: str, action: str, data: dict[str, str]) -> httpx.Response:
    csrf = CSRF.search(console.get(page).text)
    assert csrf, page
    return console.post(action, data={"csrf": csrf[1], **data})


def test_link_review_cause_and_carry_over(
    stack: Stack, console: httpx.Client, capsys: pytest.CaptureFixture[str]
) -> None:
    project = stack.project
    alice, bob, carol = stack.users["alice"], stack.users["bob"], stack.users["carol"]
    known: set[str] = set()
    stack.ingest("s1", "s1", capsys)
    stack.wait_workflow(known)
    req2_id = stack.inconsistencies()["Print monthly invoices"]["id"]

    req1 = reviewable(stack, "REQ-1")
    assert req1 and {link["status"] for link in req1.values()} == {"candidate"}
    assert not [link for link in links(stack, "REQ-1") if link["origin"]]

    # console: reject needs a reason; confirm stores the review and starts a detection
    page = f"/console/projects/{project}/links"
    one = next(link for target, link in req1.items() if target.endswith("/MediaCache.java"))
    action = f"{page}/{one['id']}/review"
    assert post_form(console, page, action, {"status": "rejected"}).status_code == 400
    assert stack.rows("MATCH (t:TraceLink {projectId: $projectId, status: 'rejected'}) RETURN count(t)") == [[0]]
    done = post_form(console, page, action, {"status": "confirmed", "reason": "matches the code"})
    assert done.status_code == 303, done.text
    stack.wait_workflow(known)
    confirmed = {link["id"]: link for link in links(stack, "REQ-1") if link["target"] == one["target"]}
    assert confirmed and {(link["status"], link["origin"], link["by"]) for link in confirmed.values()} == {
        ("confirmed", "user", "user:admin")
    }
    assert "user:admin" in console.get(page).text

    # snapshot 2: REQ-1 and its code are unchanged, so the review is carried over to the new links
    stack.ingest("s2", "s2", capsys)
    stack.wait_workflow(known)
    carried = [link for link in links(stack, "REQ-1") if link["target"] == one["target"]]
    assert carried and {id_ for id_ in (link["id"] for link in carried)}.isdisjoint(confirmed), "links are new"
    assert {(link["status"], link["origin"], link["by"], link["reason"]) for link in carried} == {
        ("confirmed", "carried-over", "user:admin", "matches the code")
    }
    # the changed sentence has no earlier review: nothing is carried over to its links
    req2 = reviewable(stack, "REQ-2")
    assert req2 and {(link["status"], link["origin"]) for link in req2.values()} == {("candidate", None)}
    assert alice.get(f"/v1/inconsistencies/{req2_id}").json()["status"] == "RESOLVED"

    # API: roles, reason rule and the effect on detection
    first = next(iter(req2.values()))
    review = f"/v1/projects/{project}/links/{first['id']}/review"
    assert carol.post(review, json={"status": "rejected", "reason": "no"}).status_code == 403
    assert bob.post(review, json={"status": "rejected"}).status_code == 422
    assert bob.post(review, json={"status": "rejected", "reason": ""}).status_code == 422
    for link in req2.values():
        resp = bob.post(
            f"/v1/projects/{project}/links/{link['id']}/review", json={"status": "rejected", "reason": "unrelated"}
        )
        assert resp.status_code == 200, resp.text
        assert link["id"] in resp.json()["reviewedLinkIds"]
    stack.wait_workflow(known)
    reopened = alice.get(f"/v1/inconsistencies/{req2_id}").json()
    assert reopened["status"] in OPEN_STATES and reopened["type"] == "untraced-requirement", reopened

    # export: review columns in the default CSV, members in JSON
    export = f"/v1/projects/{project}/export/tracelinks"
    plain = list(csv.DictReader(io.StringIO(alice.get(export).text)))
    assert plain and {"reviewed_by", "reviewed_at", "review_reason"} <= plain[0].keys()
    assert not [row for row in plain if row["status"] == "rejected"]
    assert any(row["reviewed_by"] == "user:admin" and row["review_reason"] == "matches the code" for row in plain)
    with_rejected = alice.get(export, params={"includeRejected": "true", "format": "json"}).json()
    rejected = [row for row in with_rejected if row["status"] == "rejected"]
    assert rejected and all(
        row["reviewedBy"].startswith("user:bob-") and row["reviewReason"] == "unrelated" for row in rejected
    )
    assert all("reviewedAt" in row for row in rejected)

    # triage cause: required in the console only
    inconsistencies = alice.get(f"/v1/projects/{project}/inconsistencies").json()["items"]
    target = next(i for i in inconsistencies if i["title"].endswith(": Encrypt user passwords"))
    iid = target["id"]
    detail_page = f"/console/inconsistencies/{iid}"
    transition = f"{detail_page}/transition"
    for to in ("ACCEPTED", "REJECTED", "DEFERRED"):
        resp = post_form(console, detail_page, transition, {"to": to, "expectedStatus": "OPEN", "reason": "because"})
        assert resp.status_code == 400 and "cause is required" in resp.text, (to, resp.text)
    assert alice.get(f"/v1/inconsistencies/{iid}").json()["status"] == "OPEN"
    body = {"to": "ACCEPTED", "expectedStatus": "OPEN", "reason": "x"}
    assert (
        bob.post(f"/v1/inconsistencies/{iid}/transitions", json={**body, "cause": "no-such-cause"}).status_code == 400
    )
    ok = post_form(
        console,
        detail_page,
        transition,
        {"to": "ACCEPTED", "expectedStatus": "OPEN", "reason": "planned", "cause": "planned-change"},
    )
    assert ok.status_code == 303, ok.text
    bad_open = {"to": "OPEN", "expectedStatus": "ACCEPTED", "cause": "planned-change"}
    assert bob.post(f"/v1/inconsistencies/{iid}/transitions", json=bad_open).status_code == 422
    shown = console.get(detail_page).text
    assert "planned-change" in shown and "planned" in shown
    detail = alice.get(f"/v1/inconsistencies/{iid}").json()
    assert detail["status"] == "ACCEPTED" and detail["history"][-1]["cause"] == "planned-change", detail["history"]
    exported = alice.get(f"/v1/projects/{project}/export/inconsistencies").json()
    item = next(i for i in exported if i["id"] == iid)
    assert item["history"][-1]["cause"] == "planned-change"
    rows = csv.DictReader(io.StringIO(alice.get(f"/v1/projects/{project}/export/inconsistencies?format=csv").text))
    by_id = {row["id"]: row for row in rows}
    assert by_id[iid]["cause"] == "planned-change"
    assert by_id[req2_id]["cause"] == ""

    # revert to snapshot 1: the changed sentence matches no link, so the rejection applies to nothing
    stack.ingest("s1", "s1b", capsys)
    stack.wait_workflow(known)
    assert not [link for link in links(stack, "REQ-2") if link["status"] == "rejected"]
    assert {link["status"] for link in links(stack, "REQ-1") if link["target"] == one["target"]} == {"confirmed"}
    assert alice.get(f"/v1/inconsistencies/{req2_id}").json()["status"] in OPEN_STATES

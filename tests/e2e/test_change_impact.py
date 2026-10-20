"""Change impact report (T6.3) against a running stack: `make e2e` runs it after the link review test.

Same environment as test_walking_skeleton.py. It ingests the recorded mini snapshots (s1, s2), so strict LLM replay
needs no new cassette entries. Snapshot s2 changes one requirement sentence.
"""

import os

import httpx
import pytest
import test_link_review_and_cause as shared
from test_walking_skeleton import Stack

pytestmark = pytest.mark.skipif(not os.environ.get("SANDMAN_E2E"), reason="set SANDMAN_E2E=1 with the stack running")

stack = shared.stack
console = shared.console


def impact(project: Stack, ingestion: str, who: str = "carol", depth: int = 2) -> httpx.Response:
    path = f"/v1/projects/{project.project}/ingestions/{ingestion}/impact"
    return project.users[who].get(path, params={"depth": depth})


def test_second_ingestion_reports_changed_requirement_and_linked_elements(
    stack: Stack, console: httpx.Client, capsys: pytest.CaptureFixture[str]
) -> None:
    known: set[str] = set()
    first = stack.ingest("s1", "s1", capsys)
    stack.wait_workflow(known)
    second = stack.ingest("s2", "s2", capsys)
    stack.wait_workflow(known)

    body = impact(stack, second["ingestionId"]).json()
    assert body["seq"] > first["seq"] and body["complete"] and body["workflowStatus"] == "SUCCEEDED"
    changed = {c["key"].split("#", 1)[1]: c["change"] for c in body["changed"]}
    assert changed.get("REQ-2") == "modified" and "REQ-1" not in changed
    targets = {link["target"] for link in shared.reviewable(stack, "REQ-2").values()}
    assert targets, "REQ-2 has links after the second workflow"
    by_key = {a["key"]: a for a in body["affected"]}
    assert targets <= set(by_key)
    for target in targets:
        path = by_key[target]["paths"][0]
        assert path[0]["key"].endswith("#REQ-2") and path[-1]["key"] == target

    one = impact(stack, second["ingestionId"], depth=1).json()
    assert {a["distance"] for a in one["affected"]} <= {1}
    assert {c["change"] for c in impact(stack, first["ingestionId"]).json()["changed"]} == {"added"}

    page = console.get(f"/console/projects/{stack.project}/ingestions/{second['ingestionId']}/impact")
    assert page.status_code == 200 and "REQ-2" in page.text
    assert impact(stack, second["ingestionId"], who="alice").status_code == 200
    other = stack.users["carol"].get(f"/v1/projects/e2e-other/ingestions/{second['ingestionId']}/impact")
    assert other.status_code in (403, 404)


def test_decision_is_linked_and_listed_when_the_named_file_changes(
    stack: Stack, capsys: pytest.CaptureFixture[str]
) -> None:
    known = {w["id"] for w in stack.workflows()}
    stack.ingest("s3", "s3", capsys)
    stack.wait_workflow(known)
    decisions = stack.rows(
        "MATCH (d:Decision {projectId: $projectId})<-[:HAS_ELEMENT]-(:ArtifactVersion)"
        "<-[:CURRENT_VERSION]-(a:Artifact) "
        "RETURN a.path, d.name, d.decisionStatus, d.decisionDate"
    )
    assert decisions == [["adr/0001-use-memgraph.md", "Keep media files in the MediaCache", "accepted", "2026-10-06"]]
    linked = stack.rows(
        "MATCH (t:TraceLink {projectId: $projectId, approach: 'decision-mention', active: true})"
        "-[:SOURCE]->(:Decision), "
        "(t)-[:TARGET]->(g) RETURN g.name, labels(g)"
    )
    assert {row[0] for row in linked} >= {"MediaCache", "MediaCache.java"}, linked

    known = {w["id"] for w in stack.workflows()}
    fourth = stack.ingest("s4", "s4", capsys)
    stack.wait_workflow(known)
    body = impact(stack, fourth["ingestionId"], depth=3).json()
    assert {c["artifactPath"] for c in body["changed"]} == {"src/main/java/mini/MediaCache.java"}, body["changed"]
    assert any(a["label"] == "Decision" and a["artifactPath"] == "adr/0001-use-memgraph.md" for a in body["affected"])

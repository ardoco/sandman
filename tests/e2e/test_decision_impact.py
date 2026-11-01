"""Decision elements (T7.1) against a running stack: `make e2e` runs it last, after the replay determinism test.

The ADR sentences are new LLM inputs that the recorded cassette does not contain. In strict replay they count as
misses, so this test runs after the determinism test, which asserts zero misses, and `make record-cassettes` does not
run it. The snapshots s3 (s2 plus adr/0001-use-memgraph.md) and s4 (s3 plus one method in MediaCache.java) are new.
"""

import os

import pytest
import test_link_review_and_cause as shared
from test_change_impact import impact
from test_walking_skeleton import Stack

pytestmark = pytest.mark.skipif(not os.environ.get("SANDMAN_E2E"), reason="set SANDMAN_E2E=1 with the stack running")

stack = shared.stack


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

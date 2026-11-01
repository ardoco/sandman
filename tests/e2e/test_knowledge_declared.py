"""Declared knowledge (T7.2) against a running stack: `make e2e` runs it last, after the replay determinism test.

Snapshot s5 is s4 plus sandman-knowledge.yaml, which links a declared component to REQ-3.
"""

import os

import pytest
import test_link_review_and_cause as shared
from test_walking_skeleton import Stack

pytestmark = pytest.mark.skipif(not os.environ.get("SANDMAN_E2E"), reason="set SANDMAN_E2E=1 with the stack running")

stack = shared.stack


def test_declared_link_is_confirmed_and_resolves_the_untraced_requirement(
    stack: Stack, capsys: pytest.CaptureFixture[str]
) -> None:
    known = {w["id"] for w in stack.workflows()}
    stack.ingest("s4", "s4", capsys)
    stack.wait_workflow(known)
    assert stack.inconsistencies()["Encrypt user passwords"]["status"] in shared.OPEN_STATES
    known = {w["id"] for w in stack.workflows()}
    stack.ingest("s5", "s5", capsys)
    stack.wait_workflow(known)
    links = stack.rows(
        "MATCH (t:TraceLink {projectId: $projectId, approach: 'user-declared', active: true})"
        "-[:SOURCE]->(s), (t)-[:TARGET]->(g) RETURN s.name, g.name, t.status, t.confidence"
    )
    assert links == [["Password Hasher", "Encrypt user passwords", "confirmed", 1.0]], links
    assert stack.inconsistencies()["Encrypt user passwords"]["status"] == "RESOLVED"

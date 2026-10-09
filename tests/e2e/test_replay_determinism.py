"""Replay determinism (T1.6): the canonical KG export of the mini fixture equals the golden file.

`make e2e` runs it after the walking skeleton on the same stack (strict replay from tests/cassettes/walking-skeleton),
`make record-cassettes` runs it with SANDMAN_UPDATE_GOLDEN=1 against a recording stack and writes the golden file.
Environment: SANDMAN_E2E=1 (required), SANDMAN_E2E_BOLT (default bolt://127.0.0.1:7687), SANDMAN_E2E_PROJECT or
COMPOSE_PROJECT_NAME (docker compose project of the stack, default "sandman"),
SANDMAN_UPDATE_GOLDEN=1 (write the golden file, not compare).
"""

import json
import os
import subprocess
from collections.abc import Iterator

import httpx
import pytest
from kg_canonical_export import connect, export
from test_walking_skeleton import ROOT, URL, Stack

pytestmark = pytest.mark.skipif(not os.environ.get("SANDMAN_E2E"), reason="set SANDMAN_E2E=1 with the stack running")

PROJECT = "replay-mini"
GOLDEN = ROOT / "tests" / "cassettes" / "walking-skeleton" / "kg-canonical.json"
BOLT = os.environ.get("SANDMAN_E2E_BOLT", "bolt://127.0.0.1:7687")
STATUS = (
    "import json,os,urllib.request;"
    "r=urllib.request.Request('http://127.0.0.1:8000/v1/status',headers={'Authorization':'Bearer '+os.environ['T']});"
    "print(urllib.request.urlopen(r).read().decode())"
)


def llm_replay_status() -> dict[str, object]:
    project = os.environ.get("SANDMAN_E2E_PROJECT") or os.environ.get("COMPOSE_PROJECT_NAME") or "sandman"
    token = (ROOT / "deploy" / "secrets" / "svc_token_dashboard").read_text().strip()
    cmd = ["docker", "compose", "-p", project, "-f", str(ROOT / "deploy" / "docker-compose.yml")]
    cmd += ["exec", "-T", "-e", "T", "llm-replay", "python", "-c", STATUS]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True, env={**os.environ, "T": token}).stdout
    details: dict[str, object] = json.loads(out)["details"]
    return details


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    health = httpx.get(f"{URL}/readyz", timeout=10)
    assert health.status_code == 200, f"stack not ready at {URL}: {health.text}"
    yield Stack(PROJECT)


def test_replay_is_deterministic(stack: Stack, capsys: pytest.CaptureFixture[str]) -> None:
    update = bool(os.environ.get("SANDMAN_UPDATE_GOLDEN"))
    if not update and not GOLDEN.is_file():
        pytest.fail(f"golden file {GOLDEN} is missing: run `make record-cassettes` and commit the result")

    stack.ingest("s1", "s1", capsys)
    stack.wait_workflow(set())

    with connect(BOLT) as driver:
        actual = export(driver, PROJECT)
        runs = "MATCH (r:Run {projectId: $p}) RETURN r.replayMode"
        modes = {r[0] for r in driver.execute_query(runs, p=PROJECT).records}
        enrich = "MATCH (r:Run {projectId: $p, tool: 'llm-enrichment'}) RETURN count(r)"
        enriched = driver.execute_query(enrich, p=PROJECT).records[0][0]

    details = llm_replay_status()
    mode = details["mode"]
    assert mode in ("record", "replay"), f"stack runs in llm mode {mode!r}, expected record or replay"
    assert modes == {mode}, f"Run.replayMode {modes} differs from the llm-replay mode {mode}"
    assert enriched > 0, "no llm-enrichment run: the test would not exercise any LLM call"
    if mode == "replay":
        assert details["misses"] == 0, f"llm-replay misses: {details}; see its log for the keys, re-record if intended"
    served = "hits" if mode == "replay" else "recorded"
    assert details[served] > 0, f"llm-replay {served} == 0, enrichment made no served LLM call: {details}"  # type: ignore[operator]

    if update:
        GOLDEN.write_bytes(actual)
        return
    assert actual == GOLDEN.read_bytes(), "canonical KG export differs from the golden file"

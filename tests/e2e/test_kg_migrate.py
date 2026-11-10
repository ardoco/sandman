"""kg-migrate (R-029) against a running stack: SchemaVersion exists and a second run changes nothing.

Environment: SANDMAN_E2E=1 (required); compose project: SANDMAN_E2E_PROJECT, else COMPOSE_PROJECT_NAME, else "sandman".
"""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("SANDMAN_E2E"), reason="set SANDMAN_E2E=1 with the stack running")

ROOT = Path(__file__).resolve().parents[2]
PROJECT = os.environ.get("SANDMAN_E2E_PROJECT") or os.environ.get("COMPOSE_PROJECT_NAME") or "sandman"
COMPOSE = [
    "docker",
    "compose",
    "-p",
    PROJECT,
    "-f",
    str(ROOT / "deploy/docker-compose.yml"),
    "-f",
    str(ROOT / "deploy/docker-compose.dev.yml"),
]
SNAPSHOT = """import os
from neo4j import GraphDatabase
with open(os.environ['SANDMAN_KG_PASSWORD_FILE']) as f:
    pw = f.read().strip()
with GraphDatabase.driver(os.environ['SANDMAN_KG_URI'], auth=(os.environ['SANDMAN_KG_USER'], pw)) as d:
    for q in (
        'MATCH (v:SchemaVersion) RETURN count(v)',
        'MATCH (v:SchemaVersion) RETURN v.id, v.version',
        'SHOW CONSTRAINTS',
    ):
        print(sorted(map(str, d.execute_query(q).records)))
"""


def snapshot() -> str:
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", "query-service", "python", "-c", SNAPSHOT], capture_output=True, text=True, check=True
    )
    return result.stdout


def test_schema_version_and_idempotent_migrate() -> None:
    before = snapshot()
    assert "'kg'" in before
    subprocess.run([*COMPOSE, "run", "--rm", "--no-deps", "kg-migrate"], check=True, capture_output=True, text=True)
    assert snapshot() == before

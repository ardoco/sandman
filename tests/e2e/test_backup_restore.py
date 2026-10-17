"""Backup and restore drill (T5.3): seed a scratch compose project, back up, wipe its volumes, restore, compare.

Run: `make backup-drill`. The test starts its own project `sandman-drill` (never `sandman`), publishes Memgraph on
127.0.0.1:17687 and removes the project with its volumes at the end.
Environment: SANDMAN_E2E=1 (required), DOCKER_HOST if Docker is not on the default socket.
"""

import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from kg_canonical_export import connect, export

pytestmark = pytest.mark.skipif(
    not os.environ.get("SANDMAN_E2E"), reason="set SANDMAN_E2E=1 (starts project sandman-drill)"
)

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "sandman-drill"
OVERRIDE = Path(tempfile.gettempdir()) / "sandman-drill-override.yml"
BOLT = "bolt://127.0.0.1:17687"
SERVICES = ["memgraph", "nats", "orchestrator", "query-service"]
SEED_WORKFLOW = (
    "import sqlite3; d = sqlite3.connect('/data/orchestrator.db'); "
    "d.execute(\"INSERT INTO workflows VALUES ('wf-drill', 'drill', '{}', 'SUCCEEDED', "
    "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')\"); d.commit()"
)
DUMP = "import sqlite3, sys; print('\\n'.join(sqlite3.connect(sys.argv[1]).iterdump()))"
SEED_KG = (
    "CREATE (p:Project {id: 'drill', name: 'Drill'}) "
    "CREATE (a:Element {id: 'drill:a', projectId: 'drill', name: 'A', kind: 'component'}) "
    "CREATE (b:Element {id: 'drill:b', projectId: 'drill', name: 'B', kind: 'component'}) "
    "CREATE (a)-[:DEPENDS_ON {weight: 1}]->(b)"
)


@dataclass
class Drill:
    out: Path
    archive: Path
    before: dict[str, object]


def dc(*args: str, check: bool = True, input: str | None = None) -> subprocess.CompletedProcess[str]:
    cmd = ["docker", "compose", "-p", PROJECT, "-f", str(ROOT / "deploy/docker-compose.yml")]
    cmd += ["-f", str(OVERRIDE), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, input=input)
    assert not check or result.returncode == 0, f"{' '.join(args)}: {result.stderr}"
    return result


def script(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "COMPOSE_PROJECT_NAME": PROJECT}
    return subprocess.run([str(ROOT / "scripts" / name), *args], capture_output=True, text=True, env=env)


def snapshot() -> dict[str, object]:
    with connect(BOLT) as driver:
        kg = export(driver, "drill")
    dumps = {
        "orchestrator": dc("exec", "-T", "orchestrator", "python", "-c", DUMP, "/data/orchestrator.db").stdout,
        "users": dc("exec", "-T", "query-service", "python", "-c", DUMP, "/data/users.db").stdout,
    }
    files = dc("exec", "-T", "query-service", "sh", "-c", "cd /artifacts && find . -type f | sort | xargs sha256sum")
    return {"kg": kg, **dumps, "artifacts": files.stdout}


def start() -> None:
    dc("up", "-d", "--build", "--wait", "--wait-timeout", "300", "--no-deps", *SERVICES)


@pytest.fixture(scope="module")
def drill(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Drill]:
    assert PROJECT != "sandman"
    OVERRIDE.write_text('services:\n  memgraph:\n    networks: [internal, edge]\n    ports: ["127.0.0.1:17687:7687"]\n')
    try:
        dc("down", "-v", "--remove-orphans")
        dc("up", "-d", "--wait", "--wait-timeout", "300", "memgraph")
        dc("run", "--rm", "--no-deps", "-T", "kg-migrate")
        start()
        with connect(BOLT) as driver:
            driver.execute_query(SEED_KG)
        dc("stop", "orchestrator")
        dc("run", "--rm", "--no-deps", "-T", "orchestrator", "python", "-c", SEED_WORKFLOW)
        start()
        seed = "mkdir -p /artifacts/drill && echo drill > /artifacts/drill/a.txt && chown -R 10001 /artifacts/drill"
        dc("run", "--rm", "--no-deps", "-T", "-u", "root", "--entrypoint", "sh", "extraction-agent", "-c", seed)
        before = snapshot()
        out = tmp_path_factory.mktemp("backups")
        result = script("backup.sh", "--out", str(out))
        assert result.returncode == 0, result.stderr
        (archive,) = out.glob("*.tar.gz")
        yield Drill(out, archive, before)
    finally:
        dc("down", "-v", "--remove-orphans", check=False)
        OVERRIDE.unlink(missing_ok=True)


def test_archive_has_manifest_and_no_secrets(drill: Drill) -> None:
    with tarfile.open(drill.archive) as tar:
        names = [n.removeprefix("./") for n in tar.getnames()]
        manifest = tar.extractfile("./manifest.json")
        assert manifest is not None
        text = manifest.read().decode()
    assert "manifest.json" in names and not any(n.startswith("secrets") for n in names)
    assert '"formatVersion": 1' in text and '"kgSchemaVersion"' in text and '"orchestrator.db"' in text
    assert (drill.out / f"{drill.archive.name}.sha256").is_file()


def test_restore_refuses_bad_input(drill: Drill, tmp_path: Path) -> None:
    result = script("restore.sh", str(drill.archive))
    assert result.returncode != 0 and "running" in result.stderr

    broken = tmp_path / "broken.tar.gz"
    shutil.copy(drill.archive, broken)
    shutil.copy(f"{drill.archive}.sha256", f"{broken}.sha256")
    with broken.open("ab") as f:
        f.write(b"x")
    assert "checksum" in script("restore.sh", str(broken), "--force").stderr

    unpacked = tmp_path / "unpacked"
    with tarfile.open(drill.archive) as tar:
        tar.extractall(unpacked, filter="data")
    manifest = unpacked / "manifest.json"
    manifest.write_text(manifest.read_text().replace('"formatVersion": 1', '"formatVersion": 99'))
    old = tmp_path / "old.tar.gz"
    with tarfile.open(old, "w:gz") as tar:
        tar.add(unpacked, arcname=".")
    Path(f"{old}.sha256").write_text(f"{hashlib.sha256(old.read_bytes()).hexdigest()}  old.tar.gz\n")
    assert "format" in script("restore.sh", str(old), "--force").stderr


def test_restore_after_wipe_matches_original(drill: Drill) -> None:
    dc("down", "-v", "--remove-orphans")
    result = script("restore.sh", str(drill.archive))
    assert result.returncode == 0, result.stderr
    start()
    after = snapshot()
    assert after == drill.before
    assert b"drill:a" in after["kg"] and "wf-drill" in str(after["orchestrator"]) and "a.txt" in str(after["artifacts"])

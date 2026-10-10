import os
import shutil
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "deploy" / "gen-secrets.sh"


def run(tmp_path: Path) -> Path:
    (tmp_path / "deploy").mkdir(exist_ok=True)
    shutil.copy(SCRIPT, tmp_path / "deploy" / "gen-secrets.sh")
    env = {"PATH": os.environ["PATH"], "SANDMAN_LOG_GID": str(os.getgid())}
    subprocess.run(["sh", str(tmp_path / "deploy" / "gen-secrets.sh")], check=True, capture_output=True, env=env)
    return tmp_path / "deploy" / "secrets"


def test_secrets_group_readable_never_world_readable(tmp_path):
    secrets = run(tmp_path)
    assert stat.S_IMODE(secrets.stat().st_mode) & 0o757 == 0o750
    files = list(secrets.iterdir())
    assert files
    for f in files:
        assert stat.S_IMODE(f.stat().st_mode) == 0o640, f
        assert f.stat().st_gid == os.getgid(), f


def test_second_run_idempotent(tmp_path):
    secrets = run(tmp_path)
    before = {f.name: f.read_bytes() for f in secrets.iterdir() if f.name != "svc_tokens_verify.json"}
    run(tmp_path)
    assert before == {f.name: f.read_bytes() for f in secrets.iterdir() if f.name != "svc_tokens_verify.json"}
    assert all(stat.S_IMODE(f.stat().st_mode) == 0o640 for f in secrets.iterdir())


def test_symlink_in_secrets_refused(tmp_path):
    secrets = run(tmp_path)
    (secrets / "evil").symlink_to(tmp_path)
    script = tmp_path / "deploy" / "gen-secrets.sh"
    env = {"PATH": os.environ["PATH"], "SANDMAN_LOG_GID": str(os.getgid())}
    r = subprocess.run(["sh", str(script)], capture_output=True, env=env)
    assert r.returncode != 0

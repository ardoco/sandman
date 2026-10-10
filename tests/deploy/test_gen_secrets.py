import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "deploy" / "gen-secrets.sh"
OWNER_ONLY = {"gateway.env", "memgraph.env", "e2e_admin_password"}


def run(tmp_path: Path) -> Path:
    (tmp_path / "deploy").mkdir(exist_ok=True)
    for f in [SCRIPT, *SCRIPT.parent.glob("docker-compose*.yml")]:
        shutil.copy(f, tmp_path / "deploy" / f.name)
    env = {"PATH": os.environ["PATH"], "SANDMAN_LOG_GID": str(os.getgid())}
    subprocess.run(["sh", str(tmp_path / "deploy" / "gen-secrets.sh")], check=True, capture_output=True, env=env)
    return tmp_path / "deploy" / "secrets"


def mounted_names() -> set[str]:
    return set(re.findall(r"\{ *file: \./secrets/([^ }]+)", SCRIPT.with_name("docker-compose.yml").read_text()))


def test_only_mounted_secrets_group_readable(tmp_path):
    secrets = run(tmp_path)
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o750
    mounted = mounted_names()
    names = {f.name for f in secrets.iterdir()}
    assert mounted <= names and OWNER_ONLY <= names
    assert not mounted & OWNER_ONLY
    for f in secrets.iterdir():
        want = 0o640 if f.name in mounted else 0o600
        assert stat.S_IMODE(f.stat().st_mode) == want, f
        assert f.stat().st_gid == os.getgid(), f


def test_non_mounted_reset_to_owner_only(tmp_path):
    secrets = run(tmp_path)
    (secrets / "gateway.env").chmod(0o644)
    (secrets / "extra").write_text("x")
    (secrets / "extra").chmod(0o666)
    run(tmp_path)
    assert stat.S_IMODE((secrets / "gateway.env").stat().st_mode) == 0o600
    assert stat.S_IMODE((secrets / "extra").stat().st_mode) == 0o600


def test_second_run_idempotent(tmp_path):
    secrets = run(tmp_path)

    def snap():
        return {
            f.name: (f.read_bytes(), stat.S_IMODE(f.stat().st_mode))
            for f in secrets.iterdir()
            if f.name != "svc_tokens_verify.json"
        }

    before = snap()
    run(tmp_path)
    assert before == snap()


def test_symlink_in_secrets_refused(tmp_path):
    secrets = run(tmp_path)
    (secrets / "evil").symlink_to(tmp_path)
    script = tmp_path / "deploy" / "gen-secrets.sh"
    env = {"PATH": os.environ["PATH"], "SANDMAN_LOG_GID": str(os.getgid())}
    r = subprocess.run(["sh", str(script)], capture_output=True, env=env)
    assert r.returncode != 0

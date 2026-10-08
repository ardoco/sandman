import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "deploy" / "check-env.sh"


def run(tmp_path: Path, env: str | None) -> int:
    (tmp_path / "secrets").mkdir()
    shutil.copy(SCRIPT, tmp_path / "check-env.sh")
    if env is not None:
        (tmp_path / "secrets" / "gateway.env").write_text(env)
    return subprocess.run(["sh", str(tmp_path / "check-env.sh")], capture_output=True).returncode


def test_empty_url_ok(tmp_path):
    assert run(tmp_path, "SELFHOSTED_LLM_URL=\nSELFHOSTED_LLM_KEY=\n") == 0


def test_http_url_fails(tmp_path):
    assert run(tmp_path, "SELFHOSTED_LLM_URL=http://x\nSELFHOSTED_LLM_KEY=k\n") == 1


def test_https_empty_key_fails(tmp_path):
    assert run(tmp_path, "SELFHOSTED_LLM_URL=https://x\nSELFHOSTED_LLM_KEY=\n") == 1


def test_https_with_key_ok(tmp_path):
    assert run(tmp_path, "SELFHOSTED_LLM_URL=\"https://x\"\nSELFHOSTED_LLM_KEY='k'\n") == 0


def test_missing_file_fails(tmp_path):
    assert run(tmp_path, None) == 1

import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "deploy" / "check-env.sh"


def run(tmp_path: Path, env: str | None, environ: dict[str, str] | None = None) -> int:
    (tmp_path / "secrets").mkdir(exist_ok=True)
    shutil.copy(SCRIPT, tmp_path / "check-env.sh")
    if env is not None:
        (tmp_path / "secrets" / "gateway.env").write_text(env)
    return subprocess.run(
        ["sh", str(tmp_path / "check-env.sh")], capture_output=True, env={"PATH": "/usr/bin:/bin", **(environ or {})}
    ).returncode


def test_empty_url_ok(tmp_path):
    assert run(tmp_path, "SELFHOSTED_LLM_URL=\nSELFHOSTED_LLM_KEY=\n") == 0


def test_http_url_fails(tmp_path):
    assert run(tmp_path, "SELFHOSTED_LLM_URL=http://x\nSELFHOSTED_LLM_KEY=k\n") == 1


def test_compose_ollama_without_key_ok(tmp_path):
    assert run(tmp_path, "SELFHOSTED_LLM_URL=http://ollama:11434\nSELFHOSTED_LLM_KEY=\n") == 0


def test_other_http_ollama_variants_fail(tmp_path):
    assert run(tmp_path, "SELFHOSTED_LLM_URL=http://ollama:11434/x\nSELFHOSTED_LLM_KEY=\n") == 1


def test_open_webui_https_with_key_ok(tmp_path):
    assert run(tmp_path, "SELFHOSTED_LLM_URL=https://webui.example/ollama\nSELFHOSTED_LLM_KEY=sk-x\n") == 0


def test_https_empty_key_fails(tmp_path):
    assert run(tmp_path, "SELFHOSTED_LLM_URL=https://x\nSELFHOSTED_LLM_KEY=\n") == 1


def test_https_with_key_ok(tmp_path):
    assert run(tmp_path, "SELFHOSTED_LLM_URL=\"https://x\"\nSELFHOSTED_LLM_KEY='k'\n") == 0


def test_missing_file_fails(tmp_path):
    assert run(tmp_path, None) == 1


def test_telemetry_on_without_endpoint_fails(tmp_path):
    ok = "SELFHOSTED_LLM_URL=\n"
    assert run(tmp_path, ok, {"OTEL_SDK_DISABLED": "false"}) == 1


def test_telemetry_on_with_endpoint_or_off_ok(tmp_path):
    ok = "SELFHOSTED_LLM_URL=\n"
    assert (
        run(tmp_path, ok, {"OTEL_SDK_DISABLED": "false", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-lgtm:4318"}) == 0
    )
    assert run(tmp_path, ok, {"OTEL_SDK_DISABLED": "true"}) == 0
    assert run(tmp_path, ok) == 0

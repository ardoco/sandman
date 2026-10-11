import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "deploy" / "check-env.sh"


def run(
    tmp_path: Path, env: str | None, environ: dict[str, str] | None = None, corenlp: str | None = "kit:secret"
) -> int:
    (tmp_path / "secrets").mkdir(exist_ok=True)
    shutil.copy(SCRIPT, tmp_path / "check-env.sh")
    if corenlp is not None:
        (tmp_path / "secrets" / "corenlp_credentials").write_text(corenlp)
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


def test_corenlp_remote_needs_real_credentials(tmp_path):
    ok = "SELFHOSTED_LLM_URL=\n"
    assert run(tmp_path, ok, corenlp="kit:secret\n") == 0
    for bad in ["change-me:change-me", "kit:change-me", "nocolon", ":secret", "kit:", "", "\n"]:
        assert run(tmp_path, ok, corenlp=bad) == 1, bad
    assert run(tmp_path, ok, corenlp=None) == 1
    (tmp_path / "secrets" / "corenlp_credentials").unlink()
    assert run(tmp_path, ok, {"SANDMAN_CORENLP_MODE": "remote"}, corenlp=None) == 1


def test_corenlp_local_does_not_check_the_remote_file(tmp_path):
    ok = "SELFHOSTED_LLM_URL=\n"
    local = {"SANDMAN_CORENLP_MODE": "local"}
    assert run(tmp_path, ok, local, corenlp=None) == 0
    assert run(tmp_path, ok, local, corenlp="change-me:change-me") == 0


def test_corenlp_other_mode_fails(tmp_path):
    for mode in ["auto", "Local", "recorded"]:
        assert run(tmp_path, "SELFHOSTED_LLM_URL=\n", {"SANDMAN_CORENLP_MODE": mode}) == 1, mode


def test_corenlp_url_must_be_https_or_the_compose_provider(tmp_path):
    ok = "SELFHOSTED_LLM_URL=\n"
    for url in ["https://corenlp.example", "http://corenlp-provider:8080"]:
        assert run(tmp_path, ok, {"SANDMAN_CORENLP_URL": url}) == 0, url
    for url in ["http://corenlp.example", "http://corenlp-provider:8080/x", "ftp://x"]:
        assert run(tmp_path, ok, {"SANDMAN_CORENLP_URL": url}) == 1, url


def test_corenlp_mode_read_from_dotenv(tmp_path):
    (tmp_path / ".env").write_text("SANDMAN_CORENLP_MODE=local\n")
    assert run(tmp_path, "SELFHOSTED_LLM_URL=\n", corenlp="change-me:change-me") == 0

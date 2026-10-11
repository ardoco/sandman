"""code-extractor-ardoco (T2.6) processes uploaded customer code: its Compose service must stay locked down."""

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
COMPOSE = yaml.safe_load((ROOT / "deploy" / "docker-compose.yml").read_text())
SERVICE = COMPOSE["services"]["code-extractor-ardoco"]
PLUGINS = yaml.safe_load((ROOT / "config" / "extraction" / "plugins.yaml").read_text())


def test_internal_network_only_and_no_published_port() -> None:
    assert SERVICE["networks"] == ["internal"]
    assert COMPOSE["networks"]["internal"]["internal"] is True
    assert "ports" not in SERVICE


def test_artifacts_read_only_and_root_filesystem_read_only() -> None:
    assert SERVICE["volumes"] == ["artifact-store:/artifacts:ro"]
    assert SERVICE["read_only"] is True
    assert SERVICE["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in SERVICE["security_opt"]
    assert len(SERVICE["tmpfs"]) == 1 and {"noexec", "nosuid", "nodev"} <= set(SERVICE["tmpfs"][0].split(","))


def test_only_the_inbound_tokens_are_mounted_one_per_caller() -> None:
    assert SERVICE["secrets"] == ["plugin_in_code-extractor-ardoco", "plugin_in_code-extractor-ardoco_tlr"]
    callers = dict(e.split("=") for e in SERVICE["environment"]["SANDMAN_PLUGIN_INBOUND_TOKEN_FILES"].split(","))
    assert callers == {
        "extraction-agent": "/run/secrets/plugin_in_code-extractor-ardoco",
        "tlr-agent": "/run/secrets/plugin_in_code-extractor-ardoco_tlr",
    }
    assert "SANDMAN_PLUGIN_INBOUND_TOKEN_FILE" not in SERVICE["environment"]
    assert not any(k.startswith(("SANDMAN_KG", "SANDMAN_LLM", "SANDMAN_SERVICE_TOKEN")) for k in SERVICE["environment"])


def test_extraction_agent_calls_the_plugin_with_the_inbound_token() -> None:
    entry = next(p for p in PLUGINS["plugins"] if p["id"] == "code-extractor")
    assert entry["url"] == "http://code-extractor-ardoco:8000"
    assert entry["tokenFile"] == "/run/secrets/plugin_in_code-extractor-ardoco"
    assert entry["match"] == {"kinds": ["code", "test"]}
    assert "plugin_in_code-extractor-ardoco" in COMPOSE["services"]["extraction-agent"]["secrets"]


def test_each_caller_mounts_only_its_own_token() -> None:
    tlr = COMPOSE["services"]["tlr-agent"]
    extraction = COMPOSE["services"]["extraction-agent"]
    assert "plugin_in_code-extractor-ardoco_tlr" in tlr["secrets"]
    assert "plugin_in_code-extractor-ardoco" not in tlr["secrets"]
    assert tlr["environment"]["SANDMAN_CODE_MODEL_TOKEN_FILE"] == "/run/secrets/plugin_in_code-extractor-ardoco_tlr"
    assert tlr["environment"]["SANDMAN_CODE_MODEL_URL"] == "http://code-extractor-ardoco:8000"
    assert "plugin_in_code-extractor-ardoco_tlr" not in extraction["secrets"]
    assert "plugin_in_code-extractor-ardoco_tlr" in COMPOSE["secrets"]


def test_plugin_has_a_token_pair() -> None:
    script = (ROOT / "deploy" / "gen-secrets.sh").read_text()
    assert 'PLUGINS="plugin-stubs code-extractor-ardoco"' in script
    assert 'write "plugin_in_code-extractor-ardoco_tlr"' in script


def test_base_images_are_pinned_by_digest() -> None:
    froms = [
        line
        for line in (ROOT / "services/code-extractor-ardoco/Dockerfile").read_text().splitlines()
        if line.startswith("FROM ")
    ]
    assert froms and all("@sha256:" in line for line in froms)

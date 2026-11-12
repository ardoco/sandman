from pathlib import Path

import yaml
from sandman_extraction.extract import plugin_for
from sandman_extraction.stub import stub_elements

ROOT = Path(__file__).resolve().parents[3]
PLUGINS = yaml.safe_load((ROOT / "config" / "extraction" / "plugins.yaml").read_text())["plugins"]


def test_build_files_go_to_the_code_extractor_only() -> None:
    assert [p["id"] for p in PLUGINS] == ["code-extractor"]
    assert plugin_for(PLUGINS, "pom.xml", "build", None) == PLUGINS[0]
    assert plugin_for(PLUGINS, "notes.md", "documentation", None) is None


def test_build_file_fallback_is_a_code_unit() -> None:
    assert stub_elements("Dockerfile", "build", b"FROM x\n")[0]["label"] == "CodeUnit"

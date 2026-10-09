import hashlib
from pathlib import Path

import yaml
from experiment_manifest import build, write

ROOT = Path(__file__).resolve().parent.parent


def fixture(tmp: Path) -> None:
    (tmp / "config/llm").mkdir(parents=True)
    (tmp / "config/extraction").mkdir()
    (tmp / "deploy/gateway").mkdir(parents=True)
    (tmp / "config/llm/models.yaml").write_text("routes:\n  chat-local: ollama/qwen3:1.7b\n")
    (tmp / "config/extraction/enrichment.yaml").write_text("llm:\n  seed: 42\n")
    (tmp / "deploy/gateway/config.json").write_text("{}")


def test_manifest_has_every_section_and_hashes(tmp_path: Path) -> None:
    fixture(tmp_path)
    images = {"memgraph": {"image": "memgraph/memgraph:3.13.1", "id": "sha256:x", "repoDigests": ["m@sha256:y"]}}
    snapshot = {"project": "p", "snapshotHash": "h", "artifacts": {"a.md": "c"}}
    m = build(tmp_path, "exp", "replay", "abc123", True, images, snapshot)
    path = write(tmp_path, m)
    assert path == tmp_path / "experiments/exp/experiment.yaml"
    loaded = yaml.safe_load(path.read_text())
    assert loaded == m
    assert set(loaded) == {"experiment", "git", "images", "configFiles", "routes", "snapshot", "llmMode", "seeds"}
    assert loaded["git"] == {"commit": "abc123", "dirty": True}
    assert loaded["routes"] == {"chat-local": "ollama/qwen3:1.7b"}
    assert loaded["seeds"] == {"config/extraction/enrichment.yaml:llm.seed": 42}
    assert loaded["configFiles"]["deploy/gateway/config.json"] == hashlib.sha256(b"{}").hexdigest()
    assert set(loaded["configFiles"]) == {
        "config/llm/models.yaml",
        "config/extraction/enrichment.yaml",
        "deploy/gateway/config.json",
    }


def test_builds_on_repository_config() -> None:
    m = build(ROOT, "x", "replay", "c", False, {}, {})
    assert m["routes"]["chat-local"] and "config/llm/models.yaml" in m["configFiles"]

"""Experiments run without gateway fallbacks (R-041) and the custom-provider example stays valid (R-031)."""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
CONFIG = json.loads((ROOT / "deploy" / "gateway" / "config.json").read_text())
MODELS = yaml.safe_load((ROOT / "config" / "llm" / "models.yaml").read_text())


def keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from keys(v)


def test_gateway_config_has_no_fallbacks():
    assert not [k for k in keys(CONFIG) if "fallback" in k.lower()]
    assert not [k for k in keys(MODELS) if "fallback" in k.lower()]


def test_experiments_key_and_routes_have_no_fallbacks():
    vk = next(v for v in CONFIG["governance"]["virtual_keys"] if v["name"] == "experiments")
    assert all(set(p) == {"provider", "allowed_models", "key_ids", "weight"} for p in vk["provider_configs"])
    assert not [r for r in MODELS["routes"].values() if "," in r or "|" in r]


def test_custom_provider_example_is_ignored_by_bifrost_and_complete():
    example = CONFIG["_example_custom_provider"]
    assert example["name"] not in CONFIG["providers"]
    provider = example["provider"]
    assert provider["custom_provider_config"]["base_provider_type"] == "openai"
    assert provider["network_config"]["base_url"].startswith("http")


def test_every_openrouter_route_is_pinned():
    pins = MODELS["openrouterProviders"]
    models = [r.removeprefix("openrouter/") for r in MODELS["routes"].values() if r.startswith("openrouter/")]
    assert models
    assert all(pins.get(m) for m in models)

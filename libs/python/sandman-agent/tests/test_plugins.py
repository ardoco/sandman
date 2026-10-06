from typing import Any

import httpx
import pytest
from sandman_agent.a2a import SkillContext, SkillResult, serve
from sandman_agent.config import DEFAULT_CONTRACTS_DIR, Settings
from sandman_agent.contracts import Contracts
from sandman_agent.plugins import A2aPluginClient, PluginError

CARD = DEFAULT_CONTRACTS_DIR / "agent-cards" / "plugin-extract.template.json"
INPUT = {
    "projectId": "mini",
    "llmPolicy": "local-only",
    "artifacts": [
        {
            "artifactVersionId": "ver_" + "0" * 32,
            "path": "src/A.java",
            "kind": "code",
            "storagePath": "/artifacts/mini/" + "a" * 64,
            "contentHash": "a" * 64,
        }
    ],
    "context": {"currentArtifacts": []},
}
OUTPUT = {
    "elements": [{"path": "src/A.java", "localKey": "A", "label": "CodeUnit", "kind": "type", "name": "A"}],
    "relations": [],
}


class Extract:
    id = "sandman.plugin.extract"

    def __init__(self, output: dict[str, Any]) -> None:
        self.output = output

    async def run(self, ctx: SkillContext, input: dict[str, Any]) -> SkillResult:
        return SkillResult(self.output)  # plugin skills publish no event


def client(contracts: Contracts, output: dict[str, Any], token: str = "inbound") -> A2aPluginClient:
    app = serve(
        CARD, [Extract(output)], Settings(service_name="plugin", plugin_inbound_token="inbound"), contracts=contracts
    )
    return A2aPluginClient(
        "http://plugin:8000", token, contracts, poll=(0.01, 0.05), transport=httpx.ASGITransport(app=app)
    )


async def test_extract_returns_validated_output(contracts: Contracts) -> None:
    assert await client(contracts, OUTPUT).extract(INPUT) == OUTPUT


async def test_invalid_plugin_output_is_a_non_retryable_error(contracts: Contracts) -> None:
    with pytest.raises(PluginError) as e:
        await client(contracts, {"elements": "nope"}).extract(INPUT)
    assert not e.value.retryable


async def test_wrong_inbound_token_is_rejected(contracts: Contracts) -> None:
    with pytest.raises(Exception):  # noqa: B017 - 401 from the plugin
        await client(contracts, OUTPUT, token="owner-service-token").extract(INPUT)

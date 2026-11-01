"""T7.2: intake validation of sandman-knowledge.yaml (Memgraph via Testcontainers for the route tests)."""

import hashlib
from typing import Any

import pytest
from sandman_agent import Contracts, stable_id
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_extraction import knowledge
from sandman_extraction.ingest import KNOWLEDGE_PATH

VALID = b"""version: 1
elements:
  - id: payment-provider
    label: ModelElement
    kind: component
    name: External Payment Provider
aliases:
  - element: decl:payment-provider
    names: [Payments, PSP]
links:
  - source: docs/architecture.md#sec:overview
    target: decl:payment-provider
    type: documents
"""
CASES = {
    "too large": (b"#" * (knowledge.MAX_BYTES + 1), "/: file exceeds"),
    "not utf-8": (b"version: \xff\xfe", "/: file is not UTF-8"),
    "anchor": (b"version: 1\nelements: &a []\n", "/: YAML anchors"),
    "alias": (b"version: 1\nelements: &a []\nlinks: *a\n", "/: YAML anchors"),
    "syntax": (b"version: [1\n", "/: invalid YAML"),
    "schema": (b"version: 2\n", "/version: "),
    "nested schema": (VALID.replace(b"label: ModelElement", b"label: Nope"), "/elements/0/label: "),
    "extra key": (VALID + b"extra: 1\n", "/: "),
    "bad reference": (
        VALID.replace(b"decl:payment-provider\n    names", b"no-hash\n    names"),
        "/aliases/0/element: ",
    ),
    "duplicate id": (
        VALID.replace(b"aliases:", b"  - {id: payment-provider, label: TextUnit, kind: x, name: Y}\naliases:"),
        "/elements/1/id: duplicate id payment-provider",
    ),
    "unknown decl": (VALID.replace(b"target: decl:payment-provider", b"target: decl:missing"),
                     "/links/0/target: unknown element decl:missing"),
}  # fmt: skip


def test_valid_file_passes() -> None:
    data = knowledge.validate(VALID, Contracts(DEFAULT_CONTRACTS_DIR))
    assert data["elements"][0]["id"] == "payment-provider"


@pytest.mark.parametrize(("content", "message"), CASES.values(), ids=CASES.keys())
def test_invalid_file_is_rejected(content: bytes, message: str) -> None:
    with pytest.raises(knowledge.KnowledgeError) as e:
        knowledge.validate(content, Contracts(DEFAULT_CONTRACTS_DIR))
    assert str(e.value).startswith(message), e.value


@pytest.mark.parametrize(("content", "message"), CASES.values(), ids=CASES.keys())
async def test_invalid_file_gives_400_and_stores_nothing(env: Any, content: bytes, message: str) -> None:
    await env.put_project()
    resp = await env.ingest({**env.mini(), KNOWLEDGE_PATH: content})
    assert resp.status_code == 400
    assert resp.json()["detail"].startswith(f"INVALID_INPUT: {KNOWLEDGE_PATH}: {message}"), resp.json()
    assert not env.svc.store.path_for(f"artifact://{env.project}/{hashlib.sha256(content).hexdigest()}").exists()
    assert await env.svc.kg.read("MATCH (g:Ingestion {projectId: $p}) RETURN g.id", p=env.project) == []


async def test_valid_file_is_an_artifact_of_kind_other_ignoring_kind_rules(env: Any) -> None:
    await env.put_project()
    resp = await env.ingest({**env.mini(), KNOWLEDGE_PATH: VALID})
    assert resp.status_code == 201, resp.text
    rows = await env.svc.kg.read(
        "MATCH (a:Artifact {id: $a})-[:CURRENT_VERSION]->(v) RETURN a.kind AS kind, a.format AS format",
        a=stable_id("art", env.project, KNOWLEDGE_PATH),
    )
    assert rows == [{"kind": "other", "format": "sandman-knowledge"}]

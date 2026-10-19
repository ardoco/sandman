"""Reserved path prefix and derived artifacts at intake (T2.7, Testcontainers Memgraph + NATS)."""

import hashlib
import json
from typing import Any

from sandman_agent import stable_id
from sandman_extraction import ingest

DERIVED = ".sandman/derived/exarch/docs/architecture.md.components"


async def write_derived(env: Any, source: str = "ver_source", run: str = "run_x") -> str:
    data = b"Alpha::Alpha\n"
    manifest = ingest.Manifest(
        mode="partial",
        kindRules=[ingest.KindRule(glob=".sandman/**", kind="architecture-model", format="component-listing")],
        files=[ingest.ManifestFile(path=DERIVED, sha256=hashlib.sha256(data).hexdigest())],
    )
    return await ingest.ingest(
        env.svc.kg, env.svc.store, env.project, manifest, {DERIVED: data}, "system:model-derivation",
        derived={DERIVED: (source, run)},
    )  # fmt: skip


async def test_reserved_prefix_is_rejected_at_intake(env: Any) -> None:
    await env.put_project()
    manifest = {
        "mode": "partial",
        "files": [{"path": ".sandman/derived/x.components", "sha256": hashlib.sha256(b"A").hexdigest()}],
    }
    for url, kwargs in (
        ("ingestions/check", {"json": manifest}),
        ("ingestions", {"files": {"manifest": (None, json.dumps(manifest), "application/json")}}),
    ):
        resp = await env.http.post(f"/v1/projects/{env.project}/{url}", **kwargs)
        assert resp.status_code == 400
        assert "INVALID_INPUT" in resp.json()["detail"]
    assert await env.svc.kg.read("MATCH (a:Artifact {projectId: $p}) RETURN a.id", p=env.project) == []


async def test_full_snapshot_keeps_derived_artifact(env: Any) -> None:
    await env.put_project()
    assert (await env.ingest(env.mini())).status_code == 201
    await write_derived(env)
    resp = await env.ingest({"README.md": b"# other\n"}, mode="full")
    assert resp.status_code == 201, resp.text
    art = stable_id("art", env.project, DERIVED)
    assert art not in resp.json()["deletedArtifactIds"]
    rows = await env.svc.kg.read(
        "MATCH (a:Artifact {id: $a})-[:CURRENT_VERSION]->(v) RETURN a.origin AS origin, v.derivedFrom AS src, "
        "v.derivationRunId AS run, a.kind AS kind, a.format AS format",
        a=art,
    )
    assert rows == [{"origin": "derived", "src": "ver_source", "run": "run_x", "kind": "architecture-model",
                     "format": "component-listing"}]  # fmt: skip

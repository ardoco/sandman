"""R-005: X-Sandman-Actor is accepted only from the query-service (HTTP) and the orchestrator (A2A sandman.actor)."""

from typing import Any


async def test_actor_header_from_non_forwarder_is_forbidden(env: Any) -> None:
    await env.put_project()
    resp = await env.ingest(env.mini(), **{"Authorization": "Bearer cli-token", "X-Sandman-Actor": "user:mallory"})
    assert resp.status_code == 403
    resp = await env.http.put(
        f"/v1/projects/{env.project}",
        json={"name": "x", "llmPolicy": "local-only"},
        headers={"Authorization": "Bearer cli-token", "X-Sandman-Actor": "user:mallory"},
    )
    assert resp.status_code == 403

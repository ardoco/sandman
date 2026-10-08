"""Skill run with a PCM model: PROVIDES/REQUIRES between current elements, re-extraction drops removed components."""

import re
from pathlib import Path
from typing import Any

MODEL = "model/ms.repository"
PCM = (Path(__file__).parent / "fixtures/benchmark/mediastore/pcm/ms.repository").read_bytes()
CURRENT = (
    "MATCH (:Artifact {projectId: $p, path: $path})-[:CURRENT_VERSION]->(:ArtifactVersion)"
    "-[:HAS_ELEMENT]->(c:ModelElement) "
    "OPTIONAL MATCH (c)-[:PROVIDES]->(pi) OPTIONAL MATCH (c)-[:REQUIRES]->(ri) "
    "RETURN c.name AS name, c.kind AS kind, count(DISTINCT pi) AS provides, count(DISTINCT ri) AS requires"
)


async def extract(env: Any, files: dict[str, bytes]) -> Any:
    resp = await env.ingest(files)
    assert resp.status_code == 201, resp.text
    task = await env.extract(resp.json()["createdVersionIds"])
    assert task.state == "TASK_STATE_COMPLETED", task
    return task.result


async def current(env: Any) -> dict[str, tuple[str, int, int]]:
    rows = await env.svc.kg.read(CURRENT, p=env.project, path=MODEL)
    return {r["name"]: (r["kind"], r["provides"], r["requires"]) for r in rows}


def components(elements: dict[str, tuple[str, int, int]]) -> int:
    return sum(kind == "component" for kind, _, _ in elements.values())


async def test_model_relations_and_reextraction(env: Any) -> None:
    await env.put_project()
    files = env.mini() | {MODEL: PCM}
    result = await extract(env, files)
    assert result["status"] == "completed"
    got = await current(env)
    assert len(got) == 24 and got["ms.repository"][0] == "file"
    assert components(got) == 14
    assert got["Facade"] == ("component", 1, 2)
    assert got["IDownload"] == ("interface", 0, 0)

    gone = re.sub(
        rb'<components__Repository [^>]*entityName="Packaging">.*?</components__Repository>\n?', b"", PCM, flags=re.S
    )
    assert gone != PCM
    await extract(env, files | {MODEL: gone})
    after = await current(env)
    assert "Packaging" not in after and "IPackaging" in after
    assert components(after) == 13

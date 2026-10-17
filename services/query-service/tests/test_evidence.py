# ruff: noqa: E501
"""Console evidence view: sentence text, element state, links, detection runs (Memgraph) and failure/authz (fakes)."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from conftest import Env, build_env
from helpers import INC_ID, TS
from neo4j import AsyncGraphDatabase
from test_console import logged_in

EVIL = "<script>alert(1)</script>"
SEED = """
CREATE (pr:Project {id: 'alpha', name: 'a', llmPolicy: 'local-only', ingestionSeq: 2, createdAt: $ts})
CREATE (pr)-[:HAS_ARTIFACT]->(d:Artifact {id: 'art_d', projectId: 'alpha', path: 'docs/arch.md', kind: 'architecture-doc'})
CREATE (pr)-[:HAS_ARTIFACT]->(m:Artifact {id: 'art_m', projectId: 'alpha', path: 'model.repository', kind: 'architecture-model'})
CREATE (d)-[:HAS_VERSION]->(vd1:ArtifactVersion {id: 'ver_d1', artifactId: 'art_d', projectId: 'alpha', sourceRevision: 'rev1'})
CREATE (d)-[:HAS_VERSION]->(vd2:ArtifactVersion {id: 'ver_d2', artifactId: 'art_d', projectId: 'alpha', sourceRevision: 'rev2'})
CREATE (m)-[:HAS_VERSION]->(vm:ArtifactVersion {id: 'ver_m', artifactId: 'art_m', projectId: 'alpha'})
CREATE (d)-[:CURRENT_VERSION]->(vd2), (m)-[:CURRENT_VERSION]->(vm)
CREATE (g1:Ingestion {id: 'ing_1', projectId: 'alpha', seq: 1, mode: 'full', actor: 'u', createdAt: $ts})
CREATE (g2:Ingestion {id: 'ing_2', projectId: 'alpha', seq: 2, mode: 'full', actor: 'u', createdAt: $ts})
CREATE (g1)-[:ACTIVATED]->(vd1), (g2)-[:ACTIVATED]->(vd2), (g1)-[:ACTIVATED]->(vm)
CREATE (vd1)-[:HAS_ELEMENT]->(:Element:TextUnit {id: 'el_s1_old', key: 'art_d#sec:intro/aaa', projectId: 'alpha', kind: 'sentence',
       name: 'S1', text: 'The old sentence.', sentenceIndex: 3, contentHash: 'h1', runId: 'r'})
CREATE (vd2)-[:HAS_ELEMENT]->(s1:Element:TextUnit {id: 'el_s1', key: 'art_d#sec:intro/aaa', projectId: 'alpha', kind: 'sentence',
       name: 'S1', text: $evil, sentenceIndex: 4, startLine: 7, endLine: 8, contentHash: 'h2', runId: 'r'})
CREATE (vd1)-[:HAS_ELEMENT]->(:Element:TextUnit {id: 'el_gone', key: 'art_d#sec:/gone', projectId: 'alpha', kind: 'sentence',
       name: 'G', text: 'Removed sentence.', sentenceIndex: 0, contentHash: 'h3', runId: 'r'})
CREATE (vm)-[:HAS_ELEMENT]->(c1:Element:ModelElement {id: 'el_c1', key: 'art_m#C1', projectId: 'alpha', kind: 'component',
       name: 'Cache', contentHash: 'h4', runId: 'r'})
CREATE (vm)-[:HAS_ELEMENT]->(:Element:ModelElement {id: 'el_c2', key: 'art_m#C2', projectId: 'alpha', kind: 'component',
       name: 'Lonely', contentHash: 'h5', runId: 'r'})
CREATE (l1:TraceLink {id: 'tl_1', projectId: 'alpha', linkType: 'documents', confidence: 0.8, approach: 'ardoco', approachVersion: '1',
       configHash: 'x', status: 'confirmed', active: true, runId: 'r', createdAt: $ts})
CREATE (l2:TraceLink {id: 'tl_2', projectId: 'alpha', linkType: 'documents', confidence: 0.4, approach: 'lissa', approachVersion: '1',
       configHash: 'x', status: 'candidate', active: false, runId: 'r', createdAt: $ts})
CREATE (l1)-[:SOURCE]->(s1), (l1)-[:TARGET]->(c1)
CREATE (l2)-[:SOURCE]->(:Element:TextUnit {id: 'el_s1_x', key: 'art_d#sec:intro/aaa', projectId: 'alpha', kind: 'sentence',
       name: 'S1', contentHash: 'h1', runId: 'r'}), (l2)-[:TARGET]->(c1)
CREATE (i:Inconsistency {id: $iid, projectId: 'alpha', fingerprint: 'f', type: 't', severity: 'info', title: 'T', status: 'OPEN',
       verification: 'none', detector: 'ardoco-id', detectorVersion: '1', firstRunId: 'run_1', lastRunId: 'run_2',
       evidenceHash: 'h', createdAt: $ts, updatedAt: $ts})
CREATE (:Run {id: 'run_1', projectId: 'alpha', tool: 'ardoco-id', toolVersion: '0.1.0', status: 'completed', startedAt: '2026-10-01T10:00:00Z'})-[:DETECTED]->(i)
CREATE (:Run {id: 'run_2', projectId: 'alpha', tool: 'ardoco-id', toolVersion: '0.1.1', status: 'completed', startedAt: '2026-10-02T10:00:00Z'})-[:DETECTED]->(i)
"""


def involved(
    el_id: str, key: str, label: str, name: str, version: str, excerpt: str, role: str = "subject"
) -> dict[str, Any]:
    element = {"id": el_id, "key": key, "label": label, "name": name, "artifactPath": "docs/arch.md",
               "artifactVersionId": version, "excerpt": excerpt}  # fmt: skip
    return {"role": role, "element": element}


@pytest.fixture
async def mg(memgraph: str, tmp_path: Path) -> AsyncIterator[Env]:
    driver = AsyncGraphDatabase.driver(memgraph)
    async with driver.session() as s:
        await s.run("MATCH (n) DETACH DELETE n")
        await s.run(SEED, ts=TS, evil=EVIL, iid=INC_ID)  # type: ignore[arg-type]
    await driver.close()
    async for env in build_env(tmp_path, memgraph):
        yield env


async def page(env: Env, *items: dict[str, Any]) -> str:
    env.owner.detail_patch = {"id": INC_ID, "involved": list(items)}
    if not env.http.cookies.get("sandman_session"):
        await logged_in(env)
    resp = await env.http.get(f"/console/inconsistencies/{INC_ID}")
    assert resp.status_code == 200, resp.text
    return resp.text


async def test_changed_sentence_shows_text_position_links_runs_and_escapes(mg: Env) -> None:
    html = await page(mg, involved("el_s1_old", "art_d#sec:intro/aaa", "TextUnit", "S1", "ver_d1", "The old sentence."))
    assert "changed" in html and "The old sentence." in html  # text at detection
    assert EVIL not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html  # current text, escaped
    assert html.count("<script") == 1  # only htmx
    assert "docs/arch.md" in html and "<dt>Section</dt><dd>intro</dd>" in html and "<dt>Sentence</dt><dd>5</dd>" in html
    assert "<dt>Lines</dt><dd>7-8</dd>" in html
    assert "<dt>Revision at detection</dt><dd>rev1</dd>" in html and "<dt>Revision now</dt><dd>rev2</dd>" in html
    assert "ardoco" in html and "0.8" in html and "confirmed" in html and "lissa" in html and "0.4" in html
    assert html.index("ardoco</td>") < html.index("lissa</td>")  # active links first
    assert "<dt>First detected</dt><dd>2026-10-01T10:00:00Z" in html
    assert "<dt>Last detected</dt><dd>2026-10-02T10:00:00Z" in html and "<dt>Detections</dt><dd>2</dd>" in html


async def test_gone_element_and_model_element_without_links(mg: Env) -> None:
    html = await page(
        mg,
        involved("el_gone", "art_d#sec:/gone", "TextUnit", "G", "ver_d1", "Removed sentence."),
        involved("el_c2", "art_m#C2", "ModelElement", "Lonely", "ver_m", ""),
    )
    assert "no longer current" in html and "Removed sentence." in html and "<dd>removed</dd>" in html
    assert "(before the first heading)" in html and "Lonely" in html and ">current<" in html
    assert "No trace link ends at these elements." in html


async def test_unchanged_sentence_is_current_and_no_involved_element_renders(mg: Env) -> None:
    html = await page(mg, involved("el_s1", "art_d#sec:intro/aaa", "TextUnit", "S1", "ver_d2", "x"))
    assert ">current<" in html and ">changed<" not in html
    assert "No involved element." in (await page(mg))


async def test_kg_failure_renders_notice_and_keeps_the_form(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_: Any, **__: Any) -> Any:
        raise OSError("kg down")

    env.owner.detail_patch = {"involved": [involved("el_1", "art#k", "TextUnit", "n", "ver_1", "t")]}
    await logged_in(env)
    monkeypatch.setattr(env.reader, "rows", boom)
    resp = await env.http.get(f"/console/inconsistencies/{INC_ID}")
    assert resp.status_code == 200 and "evidence could not be read" in resp.text and "Change status" in resp.text


async def test_no_access_gets_404_and_runs_no_evidence_query(env: Env) -> None:
    await logged_in(env, "zed", {"beta": "viewer"})
    assert (await env.http.get(f"/console/inconsistencies/{INC_ID}")).status_code == 404
    assert not [q for q, _ in env.reader.queries if q.startswith("/* evidence")]
    await logged_in(env, "amy", {"alpha": "viewer"})
    assert (await env.http.get(f"/console/inconsistencies/{INC_ID}")).status_code == 200
    assert {q.split()[1] for q, _ in env.reader.queries if q.startswith("/* evidence")} >= {"evidence:runs"}

"""T7.4: asOf replay, query rewrite and the API behaviour with a fake reader (Traverse paths: test_traverse.py)."""

from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import Env
from helpers import validate
from sandman_query import asof
from starlette.exceptions import HTTPException

ING = [
    {"seq": 1, "createdAt": "2026-10-01T10:00:00Z", "sourceRevision": "r1"},
    {"seq": 2, "createdAt": "2026-10-02T10:00:00Z", "sourceRevision": "r2"},
    {"seq": 3, "createdAt": "2026-10-03T10:00:00Z", "sourceRevision": None},
]
ACT = [
    {"seq": 1, "artifactId": "a", "versionId": "va1"},
    {"seq": 1, "artifactId": "b", "versionId": "vb1"},
    {"seq": 2, "artifactId": "a", "versionId": "va2"},
    {"seq": 3, "artifactId": "a", "versionId": "va1"},
]
DEL = [{"seq": 3, "artifactId": "b"}]


class Tx:
    def __init__(self, ing: list[dict[str, Any]] = ING) -> None:
        self.data = {asof.INGESTIONS: ing, asof.ACTIVATED: ACT, asof.DELETED: DEL}

    async def transaction(self, work: Any) -> Any:
        async def rows(cypher: str, params: Any) -> list[dict[str, Any]]:
            return self.data[cypher]

        return await work(rows)


def day(d: int) -> datetime:
    return datetime(2026, 10, d, 12, tzinfo=UTC)


async def test_replay_activation_change_revert_and_delete() -> None:
    assert (await asof.resolve(Tx(), "alpha", day(1), None))[0] == ["va1", "vb1"]  # type: ignore[arg-type]
    assert (await asof.resolve(Tx(), "alpha", day(2), None))[0] == ["va2", "vb1"]  # type: ignore[arg-type]
    versions, info = await asof.resolve(Tx(), "alpha", day(9), None)  # type: ignore[arg-type]
    assert versions == ["va1"] and info["ingestionSeq"] == 3  # reverted a, deleted b


async def test_revision_picks_its_ingestion() -> None:
    versions, info = await asof.resolve(Tx(), "alpha", None, "r2")  # type: ignore[arg-type]
    assert versions == ["va2", "vb1"] and info == {
        "ingestionSeq": 2,
        "ingestedAt": "2026-10-02T10:00:00Z",
        "sourceRevision": "r2",
    }


async def test_older_than_retention_is_a_clear_error() -> None:
    kept = [{**i, "seq": i["seq"] + 4} for i in ING]  # seq 1..4 were pruned
    with pytest.raises(HTTPException, match="retention") as e:
        await asof.resolve(Tx(kept), "alpha", datetime(2026, 9, 1, tzinfo=UTC), None)  # type: ignore[arg-type]
    assert e.value.status_code == 422
    with pytest.raises(HTTPException, match="retention"):
        await asof.resolve(Tx(ING[1:]), "alpha", None, "r0")  # type: ignore[arg-type]


async def test_before_first_ingestion_is_empty_when_nothing_was_pruned() -> None:
    assert await asof.resolve(Tx(), "alpha", datetime(2026, 9, 1, tzinfo=UTC), None) == ([], {"ingestionSeq": 0})  # type: ignore[arg-type]


async def test_exactly_one_input() -> None:
    for args in ((None, None), (day(1), "r1")):
        with pytest.raises(HTTPException, match="exactly one"):
            await asof.resolve(Tx(), "alpha", *args)  # type: ignore[arg-type]


def test_scope_rewrites_the_anonymous_current_pattern() -> None:
    q = "MATCH (:Artifact)-[:CURRENT_VERSION]->(:ArtifactVersion)-[:HAS_ELEMENT]->(r:Element {projectId: $projectId})"
    out = asof.scope(q)
    assert out and out.startswith(
        "UNWIND $versionIds AS asOfVid MATCH (:ArtifactVersion {id: asOfVid, projectId: $projectId})"
    )
    assert "CURRENT_VERSION" not in out
    assert (
        asof.scope("MATCH (a:Artifact)-[:CURRENT_VERSION]->(:ArtifactVersion) RETURN a.path") is None
    )  # named artifact
    assert not asof.applicable("MATCH (i:Inconsistency {projectId: $projectId}) RETURN i.id")
    assert asof.applicable("UNWIND $versionIds AS v MATCH (x:ArtifactVersion {id: v}) RETURN x")


async def ask(env: Env, question: str, **body: Any) -> Any:
    if env.store.get_user("alice") is None:
        env.user("alice", {"alpha": "viewer"})
    env.reader.tx_data = {asof.INGESTIONS: ING, asof.ACTIVATED: ACT, asof.DELETED: DEL}
    payload = {"projectId": "alpha", "question": question, "strategy": "cypher"} | body
    return await env.http.post("/v1/query/nl", json=payload, headers=env.token("alice"))


async def test_intent_runs_scoped_to_the_version_ids(env: Env) -> None:
    resp = await ask(env, "untraced requirements", asOf="2026-10-02T12:00:00Z")
    assert resp.status_code == 200, resp.text
    validate("/v1/query/nl", "post", 200, resp.json())
    text, params = env.reader.queries[-1]
    assert text.startswith("UNWIND $versionIds AS asOfVid") and "CURRENT_VERSION" not in text
    assert params["versionIds"] == ["va2", "vb1"]
    body = resp.json()
    assert body["asOf"]["ingestionSeq"] == 2 and asof.WARNING in body["warnings"]


async def test_without_asof_nothing_changes(env: Env) -> None:
    resp = await ask(env, "untraced requirements")
    text, params = env.reader.queries[-1]
    assert "CURRENT_VERSION" in text and "versionIds" not in params and "asOf" not in resp.json()


async def test_api_errors(env: Env) -> None:
    both = await ask(env, "untraced requirements", asOf="2026-10-02T12:00:00Z", asOfRevision="r1")
    assert both.status_code == 422 and "exactly one" in both.json()["detail"]
    unscoped = await ask(env, "show open inconsistencies", asOfRevision="r1")
    assert unscoped.status_code == 422 and "asOf cannot apply" in unscoped.json()["detail"]
    validate("/v1/query/nl", "post", 422, unscoped.json())

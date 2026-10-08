"""Project statistics: fake-graph page tests; figures against Testcontainers Memgraph (skipped without Docker)."""

from dash_fakes import Env
from neo4j import AsyncDriver
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_dashboard.graph import GraphService, schema_names
from sandman_dashboard.projects import ProjectStats


async def test_pages_show_figures_and_na_per_figure(env: Env) -> None:
    await env.login()
    env.graph.fixed_rows = {
        "i.seq": [[3, "2026-10-09T10:00:00Z", "abc123", 2, 1]],
        "count(a)": [[4]],
        "count(DISTINCT a)": [[3]],
        "count(v)": [[9]],
        "UNWIND labels(e)": [["Requirement", 5], ["CodeUnit", 7]],
        "i.status": [["OPEN", "missing-link", 2], ["RESOLVED", "missing-link", 1]],
    }
    listing = (await env.http.get("/projects")).text
    assert '<a href="/projects/demo">demo</a>' in listing and "&lt;script&gt;alert(1)&lt;/script&gt;" in listing
    assert "<td>3 / 4</td>" in listing and "<td>12</td>" in listing and "#3" in listing
    detail = (await env.http.get("/projects/demo")).text
    assert "<td>Requirement</td><td>5</td>" in detail and "<td>missing-link</td><td>3</td>" in detail
    assert "abc123" in detail and "<script>alert" not in detail


async def test_failing_queries_show_na_only_for_that_figure(env: Env) -> None:
    await env.login()
    env.graph.fixed_error = True
    detail = (await env.http.get("/projects/demo")).text
    assert detail.count("<p>n/a</p>") >= 4 and "<dt>Current</dt><dd>n/a</dd>" in detail


async def test_unknown_or_bad_project_and_session(env: Env) -> None:
    assert (await env.http.get("/projects")).status_code == 303
    await env.login()
    assert (await env.http.get("/projects/nope")).status_code == 404
    assert (await env.http.get("/projects/a b")).status_code == 404


async def test_stats_are_cached_for_60_seconds(env: Env) -> None:
    t = [0.0]
    stats = ProjectStats(env.graph, lambda: t[0])  # type: ignore[arg-type]
    await stats.get("demo")
    first = len(env.graph.statements)
    await stats.get("demo")
    assert len(env.graph.statements) == first
    t[0] = 61
    await stats.get("demo")
    assert len(env.graph.statements) == 2 * first


async def seed(driver: AsyncDriver) -> None:
    """Project a: artifacts A1 (current v1b, reactivated), A2 (deleted), A3 (current v3).
    Elements: v1b has Requirement r1, r2 and CodeUnit c1; v3 has CodeUnit c3; old version v1a has r_old (not current).
    Links (all project a): t1 active r1->c1 (approach x), t2 active r2->c3 (x), t3 inactive r1->c1 (x),
    t4 active r_old->c1 (x, non-current endpoint), t5 active r1->c3 (y). Expected current links: x=2, y=1.
    Inconsistencies: OPEN/missing 2, RESOLVED/missing 1, OPEN/other 1. Project b mirrors a minimal set."""
    async with driver.session() as s:
        await s.run(
            "CREATE (pa:Project {id:'a'}), (pb:Project {id:'b'}) "
            "CREATE (a1:Artifact {id:'a1', projectId:'a'}), (a2:Artifact {id:'a2', projectId:'a', deletedAt:'t'}), "
            "(a3:Artifact {id:'a3', projectId:'a'}) "
            "CREATE (v1a:ArtifactVersion {id:'v1a', projectId:'a'}), (v1b:ArtifactVersion {id:'v1b', projectId:'a'}), "
            "(v2:ArtifactVersion {id:'v2', projectId:'a'}), (v3:ArtifactVersion {id:'v3', projectId:'a'}) "
            "CREATE (a1)-[:HAS_VERSION]->(v1a), (a1)-[:HAS_VERSION]->(v1b), (a1)-[:CURRENT_VERSION]->(v1b), "
            "(a2)-[:HAS_VERSION]->(v2), (a3)-[:HAS_VERSION]->(v3), (a3)-[:CURRENT_VERSION]->(v3) "
            "CREATE (r1:Element:Requirement {id:'r1', projectId:'a'}), "
            "(r2:Element:Requirement {id:'r2', projectId:'a'}), "
            "(c1:Element:CodeUnit {id:'c1', projectId:'a'}), (c3:Element:CodeUnit {id:'c3', projectId:'a'}), "
            "(ro:Element:Requirement {id:'r_old', projectId:'a'}) "
            "CREATE (v1b)-[:HAS_ELEMENT]->(r1), (v1b)-[:HAS_ELEMENT]->(r2), (v1b)-[:HAS_ELEMENT]->(c1), "
            "(v3)-[:HAS_ELEMENT]->(c3), (v1a)-[:HAS_ELEMENT]->(ro) "
            "CREATE (t1:TraceLink {id:'t1', projectId:'a', active:true, approach:'x'}), "
            "(t2:TraceLink {id:'t2', projectId:'a', active:true, approach:'x'}), "
            "(t3:TraceLink {id:'t3', projectId:'a', active:false, approach:'x'}), "
            "(t4:TraceLink {id:'t4', projectId:'a', active:true, approach:'x'}), "
            "(t5:TraceLink {id:'t5', projectId:'a', active:true, approach:'y'}) "
            "CREATE (t1)-[:SOURCE]->(r1), (t1)-[:TARGET]->(c1), (t2)-[:SOURCE]->(r2), (t2)-[:TARGET]->(c3), "
            "(t3)-[:SOURCE]->(r1), (t3)-[:TARGET]->(c1), (t4)-[:SOURCE]->(ro), (t4)-[:TARGET]->(c1), "
            "(t5)-[:SOURCE]->(r1), (t5)-[:TARGET]->(c3) "
            "CREATE (:Inconsistency {id:'i1', projectId:'a', status:'OPEN', type:'missing'}), "
            "(:Inconsistency {id:'i2', projectId:'a', status:'OPEN', type:'missing'}), "
            "(:Inconsistency {id:'i3', projectId:'a', status:'RESOLVED', type:'missing'}), "
            "(:Inconsistency {id:'i4', projectId:'a', status:'OPEN', type:'other'}) "
            "CREATE (g1:Ingestion {id:'g1', projectId:'a', seq:1, createdAt:'2026-01-01'}), "
            "(g2:Ingestion {id:'g2', projectId:'a', seq:2, createdAt:'2026-02-01', sourceRevision:'rev2'}) "
            "CREATE (pa)-[:HAS_INGESTION]->(g1), (pa)-[:HAS_INGESTION]->(g2), "
            "(g1)-[:ACTIVATED]->(v1a), (g2)-[:ACTIVATED]->(v1b), (g2)-[:ACTIVATED]->(v3), (g2)-[:DELETED]->(a2) "
            "CREATE (b1:Artifact {id:'b1', projectId:'b'}), (vb:ArtifactVersion {id:'vb', projectId:'b'}), "
            "(rb:Element:Requirement {id:'rb', projectId:'b'}), (cb:Element:CodeUnit {id:'cb', projectId:'b'}), "
            "(b1)-[:CURRENT_VERSION]->(vb), (vb)-[:HAS_ELEMENT]->(rb), (vb)-[:HAS_ELEMENT]->(cb), "
            "(tb:TraceLink {id:'tb', projectId:'b', active:true, approach:'x'}), "
            "(tb)-[:SOURCE]->(rb), (tb)-[:TARGET]->(cb), "
            "(:Inconsistency {id:'ib', projectId:'b', status:'OPEN', type:'missing'})"
        )


async def test_figures_match_hand_computed_values(driver: AsyncDriver) -> None:
    await seed(driver)
    labels, rel_types = schema_names(DEFAULT_CONTRACTS_DIR)
    stats = ProjectStats(GraphService(driver, labels, rel_types))
    a = await stats.get("a")
    assert (a.artifacts_current, a.artifacts_deleted, a.versions) == (2, 1, 4)
    assert a.elements == {"CodeUnit": 2, "Requirement": 2}
    assert a.links == {"x": 2, "y": 1}
    assert a.inconsistency_status == {"OPEN": 3, "RESOLVED": 1}
    assert a.inconsistency_type == {"missing": 3, "other": 1}
    assert a.ingestion == {"seq": 2, "createdAt": "2026-02-01", "sourceRevision": "rev2", "activated": 2, "deleted": 1}
    b = await stats.get("b")
    assert (b.artifacts_current, b.artifacts_deleted, b.versions) == (1, 0, 1)
    assert b.elements == {"CodeUnit": 1, "Requirement": 1} and b.links == {"x": 1}
    assert b.inconsistency_status == {"OPEN": 1} and b.ingestion == {}

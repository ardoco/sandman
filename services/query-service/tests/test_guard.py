"""Scope guard: accepted queries, bypass corpus and rewriting."""

import pytest
from sandman_query.guard import GuardError, check, tokenize

ANCHOR = "(n:Element {projectId: $projectId})"

ACCEPTED = [
    f"MATCH {ANCHOR} RETURN n",
    f"MATCH {ANCHOR}-[r:CONTAINS]->(m) RETURN n.key, m.key, type(r) AS t ORDER BY n.key DESC SKIP 1 LIMIT 5",
    "MATCH (p:Project {id: $projectId})-[:HAS_ARTIFACT]->(a:Artifact) RETURN a.path",
    "match (n {projectId: $projectId}) return count(*) as c",
    f"MATCH {ANCHOR} WITH n MATCH (n)-[:CONTAINS]->(m) RETURN m",
    f"MATCH {ANCHOR} WITH n AS x MATCH (x)-->(y) RETURN y",
    f"MATCH {ANCHOR} OPTIONAL MATCH (n)<-[:SOURCE]-(l:TraceLink) RETURN n, l",
    f"MATCH {ANCHOR}, (n)-[:CONTAINS]->(m) RETURN m",
    f"MATCH {ANCHOR} WHERE (n)-[:CONTAINS]->() AND n.name STARTS WITH 'a' RETURN n",
    f"MATCH {ANCHOR} WHERE EXISTS {{ (n)-[:CONTAINS]->(c) WHERE c.kind = 'x' }} RETURN n",
    f"MATCH {ANCHOR} WHERE EXISTS {{ MATCH (n)-[:CONTAINS]->(c) RETURN c }} RETURN n",
    f"MATCH {ANCHOR} WHERE NOT exists((n)-->()) RETURN n",
    f"MATCH {ANCHOR} RETURN [(n)-[:CONTAINS]->(c) WHERE c.kind = 'x' | c.key] AS keys",
    f"MATCH {ANCHOR} RETURN [x IN [1, 2, 3] WHERE x > 1 | x * 2], CASE WHEN n.a IS NULL THEN 0 ELSE 1 END",
    f"MATCH {ANCHOR} RETURN n.name AS a UNION MATCH {ANCHOR} RETURN n.key AS a",
    f"MATCH {ANCHOR} RETURN n.name AS a UNION ALL MATCH {ANCHOR} RETURN n.key AS a",
    f"MATCH {ANCHOR} UNWIND [1, 2] AS i RETURN n, i",
    f"MATCH p = {ANCHOR}-[*1..3]-(m) RETURN length(p), nodes(p)",
    "UNWIND [1, 2] AS i RETURN i",
    "RETURN 1; ",
    f"MATCH {ANCHOR} WHERE any(l IN labels(n) WHERE l = 'Element') RETURN reduce(s = 0, x IN [1, 2] | s + x)",
    f"MATCH {ANCHOR} RETURN n // trailing comment",
    f"MATCH {ANCHOR} RETURN 'a\\'b' AS s, \"x\" AS t, 0x1F, 1.5e3, -2",
    f"MATCH {ANCHOR} RETURN n.`odd name`",
    "MATCH (a:Element {projectId: $projectId}) WITH * MATCH (a)-->(b) RETURN b",
]
UNANCHORED_ADMIN_OK = ["MATCH (n) RETURN n", "MATCH (n)-->(m) RETURN count(*)"]

# (query, text expected in the message)
REJECTED = [
    ("MATCH (n) RETURN n", "not anchored"),
    ("MATCH (n:Element) WHERE n.projectId = $projectId RETURN n", "not anchored"),  # anchoring only in WHERE
    ("WITH $projectId AS p MATCH (n) RETURN n", "not anchored"),
    ("WITH $projectId AS p MATCH (n:Requirement) RETURN n", "not anchored"),
    (f"MATCH {ANCHOR}, (m) RETURN m", "not anchored"),  # second component
    (f"MATCH {ANCHOR} MATCH (m) RETURN m", "not anchored"),
    (f"MATCH {ANCHOR} OPTIONAL MATCH (m) RETURN m", "not anchored"),
    (f"MATCH {ANCHOR} RETURN [(m)-->(k) | k]", "not anchored"),  # comprehension reaching other projects
    (f"MATCH {ANCHOR} RETURN [(x:Element)-->(k) | k.key]", "not anchored"),
    (f"MATCH {ANCHOR} WHERE EXISTS {{ MATCH (m) RETURN m }} RETURN n", "not anchored"),  # subquery
    (f"MATCH {ANCHOR} WHERE EXISTS {{ (m)-->(k) }} RETURN n", "not anchored"),
    (f"MATCH {ANCHOR} WHERE exists((m)-->()) RETURN n", "not anchored"),
    (f"MATCH {ANCHOR} WHERE (m)-->() RETURN n", "not anchored"),
    (f"MATCH {ANCHOR} RETURN n UNION MATCH (m) RETURN m", "not anchored"),  # unanchored branch
    (f"MATCH (m) RETURN m UNION MATCH {ANCHOR} RETURN n", "not anchored"),
    (f"MATCH {ANCHOR} WITH collect(n) AS ns UNWIND ns AS m MATCH (m)-->(k) RETURN k", "not anchored"),
    (f"MATCH {ANCHOR} WITH n AS x MATCH (n)-->(y) RETURN y", "not anchored"),  # n is no longer bound
    (f"MATCH {ANCHOR} WITH n.name AS x MATCH (x)-->(y) RETURN y", "not anchored"),
    (f"MATCH {ANCHOR} UNWIND [1] AS n MATCH (n)-->(y) RETURN y", "not anchored"),  # rebound by UNWIND
    (f"MATCH {ANCHOR} RETURN [n IN [1] | [(n)-->(k) | k]]", "not anchored"),  # comprehension variable shadows
    ("MATCH (n {projectId: 'other'}) RETURN n", "not anchored"),
    ("MATCH (n {projectId: $other}) RETURN n", "not anchored"),
    ("MATCH (n {projectId: $projectId + ''}) RETURN n", "not anchored"),
    ("MATCH (n:Project {name: $projectId}) RETURN n", "not anchored"),
    ("MATCH (n:Element {id: $projectId}) RETURN n", "not anchored"),
    ("MATCH (n {projectId: $projectId, projectId: 'other'}) RETURN n", "duplicate map key"),
    ("MATCH (n {projectId: 'x'})-[r {projectId: $projectId}]->(m) RETURN n", "not anchored"),  # not a node anchor
    ("CALL db.labels() YIELD x RETURN x", "CALL"),
    (f"MATCH {ANCHOR} CALL {{ MATCH (m) RETURN m }} RETURN n", "CALL"),
    ("CALL mg.procedures() YIELD * RETURN *", "CALL"),
    ("SHOW STORAGE INFO", "SHOW"),
    ("DUMP DATABASE", "DUMP"),
    ("TERMINATE TRANSACTIONS 'x'", "TERMINATE"),
    ("LOAD CSV FROM '/etc/passwd' AS row RETURN row", "LOAD"),
    ("CREATE INDEX ON :Element(key)", "CREATE"),
    ("DROP GRAPH", "DROP"),
    ("EXPLAIN MATCH (n) RETURN n", "EXPLAIN"),
    ("PROFILE MATCH (n) RETURN n", "PROFILE"),
    (f"MATCH {ANCHOR} CREATE (m:Element) RETURN m", "CREATE"),
    (f"MATCH {ANCHOR} SET n.x = 1 RETURN n", "SET"),
    (f"MATCH {ANCHOR} DETACH DELETE n", "DETACH"),
    (f"MATCH {ANCHOR} DELETE n", "DELETE"),
    (f"MATCH {ANCHOR} REMOVE n.x RETURN n", "REMOVE"),
    (f"MATCH {ANCHOR} MERGE (m:Element {{id: 'x'}}) RETURN m", "MERGE"),
    (f"MATCH {ANCHOR} FOREACH (x IN [1] | CREATE (:A)) RETURN n", "FOREACH"),
    ("MERGE (n:Element {projectId: $projectId}) RETURN n", "MERGE"),
    (f"MATCH {ANCHOR} RETURN n QUERY MEMORY LIMIT 1 GB", "QUERY MEMORY LIMIT is set by the service"),
    (f"MATCH {ANCHOR} RETURN n; MATCH (m) DELETE m", "end of query"),
    (f"MATCH {ANCHOR} RETURN n; DROP GRAPH", "end of query"),
    (f"MATCH {ANCHOR} RETURN apoc.cypher.run('x', {{}})", "syntax error"),  # namespaced function
    (f"MATCH {ANCHOR} RETURN mg.x()", "syntax error"),
    (f"MATCH {ANCHOR} RETURN n{{.*}}", "syntax error"),  # map projection
    (f"MATCH {ANCHOR} RETURN count {{ MATCH (m) RETURN m }}", "syntax error"),
    (f"MATCH {ANCHOR}-[*bfs]-(m) RETURN m", "syntax error"),  # non-openCypher path algorithms
    (f"MATCH {ANCHOR}-[*wShortest (r, n | 1) w]-(m) RETURN m", "syntax error"),
    (f"MATCH {ANCHOR} RETURN n /* unterminated", "unterminated comment"),
    (f"MATCH {ANCHOR} RETURN 'unterminated", "unterminated string"),
    (f"MATCH {ANCHOR} RETURN '\\q'", "invalid escape"),
    (f"MATCH {ANCHOR} RETURN n ", "unexpected character"),  # no-break space
    (f"MATCH {ANCHOR} RETURN n\x00", "unexpected character"),
    (f"MATCH {ANCHOR} RETURN ä", "unexpected character"),
    (f"MATCH {ANCHOR} RETURN n.name AS match", "syntax error"),  # reserved word as name
    ("", "syntax error"),
    (f"MATCH {ANCHOR}", "syntax error"),  # no RETURN
    (f"MATCH {ANCHOR} RETURN " + "(" * 40 + "1" + ")" * 40, "nested too deeply"),
    ("MATCH " + "(n {a: " * 14 + "1" + "})" * 14 + " RETURN 1", "nested too deeply"),
    ("RETURN " + "x" * 20001, "longer than"),
]  # fmt: skip

HIDDEN_KEYWORDS = [  # keywords in comments and strings must neither hide nor create clauses
    (f"MATCH {ANCHOR} RETURN n // CREATE (x)", True),
    (f"MATCH {ANCHOR} /* CALL db.x() */ RETURN n", True),
    (f"MATCH {ANCHOR} RETURN 'CREATE (x) DELETE n' AS s", True),
    (f'MATCH {ANCHOR} RETURN "; DROP GRAPH" AS s', True),
    (f"MATCH {ANCHOR} RETURN n.`CREATE`", True),
    (f"MATCH {ANCHOR} /* hides */ DELETE n // x", False),
    (f"MATCH {ANCHOR} // newline hides\nDELETE n", False),
    (f"MATCH {ANCHOR} // \rDELETE n", False),
    (f"MATCH {ANCHOR} RETURN n /* x */ CREATE (m)", False),
    (f"/* RETURN 1 */ MATCH {ANCHOR} DELETE n", False),
]


@pytest.mark.parametrize("query", ACCEPTED)
def test_accepted(query: str) -> None:
    check(query)


@pytest.mark.parametrize("query", UNANCHORED_ADMIN_OK)
def test_admin_skips_only_anchoring(query: str) -> None:
    check(query, scoped=False)
    with pytest.raises(GuardError, match="not anchored"):
        check(query)


@pytest.mark.parametrize(("query", "message"), REJECTED)
def test_rejected(query: str, message: str) -> None:
    with pytest.raises(GuardError, match=message):
        check(query)


@pytest.mark.parametrize(("query", "ok"), HIDDEN_KEYWORDS)
def test_comments_and_strings_do_not_hide_keywords(query: str, ok: bool) -> None:
    if ok:
        assert "CREATE" not in check(query).text.replace("'CREATE (x) DELETE n'", "").replace("`CREATE`", "")
    else:
        with pytest.raises(GuardError):
            check(query)


@pytest.mark.parametrize("query", ["CALL db.labels()", "SHOW STORAGE INFO", "CREATE (n)", "MATCH (n) DELETE n"])
def test_admin_cannot_use_denied_clauses(query: str) -> None:
    with pytest.raises(GuardError):
        check(query, scoped=False)


def test_rewritten_statement_has_no_comments_and_keeps_adjacency() -> None:
    q = check(f"MATCH {ANCHOR}<-[r]-(m) /* c */ RETURN n.name AS a // tail\n;")
    assert q.text == f"MATCH {ANCHOR}<-[r]-(m) RETURN n.name AS a"
    assert not q.has_limit
    assert check(f"MATCH {ANCHOR} RETURN n LIMIT 3").has_limit
    assert not check(
        f"MATCH {ANCHOR} RETURN n AS limit_ UNION MATCH {ANCHOR} RETURN n LIMIT 2 UNION MATCH {ANCHOR} RETURN n"
    ).has_limit


def test_all_fixed_templates_pass_the_guard() -> None:
    from sandman_query import query

    for template in (query.TRACELINKS, query.INCONSISTENCIES, query.INVOLVED, query.ARTIFACTS):
        check(template)


def toks(src: str) -> list[tuple[str, str, int, int]]:
    return [(t.kind, t.text, t.pos, t.end) for t in tokenize(src)]


def test_tokenize_positions_and_kinds() -> None:
    assert toks("a 1.5e3 'x' `q` $p <= .") == [
        ("name", "a", 0, 1),
        ("num", "1.5e3", 2, 7),
        ("str", "x", 8, 11),
        ("qname", "q", 12, 15),
        ("param", "p", 16, 18),
        ("sym", "<=", 19, 21),
        ("sym", ".", 22, 23),
        ("eof", "", 23, 23),
    ]


def test_tokenize_names_and_numbers() -> None:
    assert [t[:2] for t in toks("_a9 0x1F 7")] == [("name", "_a9"), ("num", "0x1F"), ("num", "7"), ("eof", "")]
    with pytest.raises(GuardError, match="unexpected character"):
        tokenize("é")


def test_tokenize_whitespace_kinds() -> None:
    assert [t[1] for t in toks("a\tb\nc\rd\fe")] == ["a", "b", "c", "d", "e", ""]


def test_tokenize_line_comment_ends_at_cr_or_lf() -> None:
    assert [t[1] for t in toks("a // x\rb")] == ["a", "b", ""]
    assert [t[1] for t in toks("a // x\nb")] == ["a", "b", ""]
    assert [t[1] for t in toks("a // x\r\nb // y\nc")] == ["a", "b", "c", ""]
    assert [t[1] for t in toks("a // x")] == ["a", ""]
    assert [t[1] for t in toks("//\nb")] == ["b", ""]


def test_tokenize_block_comment() -> None:
    assert toks("a /* x */ b") == [("name", "a", 0, 1), ("name", "b", 10, 11), ("eof", "", 11, 11)]
    assert [t[1] for t in toks("/**/a /* x */ b /* y */ c")] == ["a", "b", "c", ""]
    assert [t[1] for t in toks("/*/ x */a")] == ["a", ""]
    with pytest.raises(GuardError, match="^syntax error: unterminated comment$"):
        tokenize("a /* x")
    with pytest.raises(GuardError, match="^syntax error: unterminated comment$"):
        tokenize("/*/")


def test_tokenize_strings() -> None:
    assert toks("'a\\'b' 'c'")[0] == ("str", "a\\'b", 0, 6)
    assert toks("''") == [("str", "", 0, 2), ("eof", "", 2, 2)]
    assert toks("'\\u00e9' '\\U0001f600'")[0][1] == "\\u00e9"
    assert toks("'\\U0001f600'")[0][1] == "\\U0001f600"
    with pytest.raises(GuardError, match="^syntax error: unterminated string$"):
        tokenize("'abc")
    with pytest.raises(GuardError, match="^syntax error: unterminated string$"):
        tokenize("'")
    with pytest.raises(GuardError, match="invalid escape at 1"):
        tokenize("'\\q'")
    for bad in ("'\\u00'", "'\\u00zz'", "'\\U0001f60'", "'\\U0001f60g'", "'\\u'"):
        with pytest.raises(GuardError, match="invalid unicode escape at 1"):
            tokenize(bad)
    with pytest.raises(GuardError, match="invalid escape"):
        tokenize("'\\")


def test_tokenize_quoted_names() -> None:
    assert toks("`a``b` x") == [("qname", "a`b", 0, 6), ("name", "x", 7, 8), ("eof", "", 8, 8)]
    assert toks("`a`` ``b`")[0][1:] == ("a` `b", 0, 9)
    assert toks("`` x")[0] == ("qname", "", 0, 2)
    with pytest.raises(GuardError, match="^syntax error: unterminated quoted name$"):
        tokenize("`abc")
    with pytest.raises(GuardError, match="^syntax error: unterminated quoted name$"):
        tokenize("`abc``")
    with pytest.raises(GuardError, match="^syntax error: unterminated quoted name$"):
        tokenize("`")


def test_tokenize_symbols_and_params() -> None:
    assert [t[1] for t in toks("<> != >= =~ .. <- -> ; |")] == [
        "<>", "!=", ">=", "=~", "..", "<", "-", "-", ">", ";", "|", "",
    ]  # fmt: skip
    assert toks("..x")[0] == ("sym", "..", 0, 2)
    assert toks("$1 $a_1")[0][1] == "1"
    with pytest.raises(GuardError, match="invalid parameter at 2"):
        tokenize("a $")
    with pytest.raises(GuardError, match="unexpected character '#' at 0"):
        tokenize("#")
    with pytest.raises(GuardError, match="unexpected character '!' at 0"):
        tokenize("!x")


def test_nesting_depth_error_message_for_expressions() -> None:
    with pytest.raises(GuardError, match="^expression nested too deeply$"):
        check("RETURN " + "(" * 5000 + "1" + ")" * 5000)

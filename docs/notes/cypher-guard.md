# Cypher scope guard: parser decision (V-28, RK-19)

Task T0.9, review decision D-2 (ADR-0009 amendment). Code: `services/query-service/src/sandman_query/guard.py`.

## Decision

**Own recursive-descent parser for exactly the allowlisted subset (the RK-19 fallback); no ANTLR parser.**
The guard rejects everything the parser does not understand.

## Why not ANTLR (time-box: evaluated, not adopted)

- The available openCypher grammars are Neo4j-flavoured. The official openCypher grammar file is no longer at the
  documented URL; the maintained one is `antlr/grammars-v4` `cypher/CypherParser.g4` (BSD, third party, ~520 lines).
  Memgraph's own grammar (`memgraph/memgraph` `Cypher.g4`) is under the Memgraph licence (BSL) and is not vendored.
  Neither covers Memgraph's dialect exactly (`SHOW`, `DUMP DATABASE`, `QUERY MEMORY LIMIT`, `*bfs`/`*wShortest`, streams).
- A grammar accepts the full language; the guard needs the opposite (deny by default). Every construct beyond the
  allowlist would have to be rejected in a visitor over a large parse tree, and each grammar/database difference is a
  possible bypass.
- Cost: ANTLR tool (Java jar) in the build path, 100+ KB of generated Python, slow runtime; the anchoring analysis is
  still hand-written. The subset parser is ~450 lines and covers MATCH, OPTIONAL MATCH, WHERE, WITH, UNWIND, RETURN,
  ORDER BY, SKIP, LIMIT, UNION [ALL], pattern comprehensions, pattern predicates and `EXISTS { }`.

## Rules implemented

- Tokenizer: ASCII identifiers and whitespace only, strict string escapes, comments removed. The statement sent to
  Memgraph is rebuilt from the parsed tokens (comments dropped, token adjacency kept), so service and database see the
  same tokens. Any other character is rejected.
- Clauses: only the allowlist; `CALL`, write clauses, `SHOW`, `DUMP`, `LOAD`, `TERMINATE`, `EXPLAIN`, index/constraint
  statements, `;`-chained statements and a caller-written `QUERY MEMORY LIMIT` are rejected. Namespaced (module)
  function calls, map projections, label expressions and Memgraph path algorithms do not parse.
- Anchoring: every connected component (patterns sharing a variable) of each `MATCH`/`OPTIONAL MATCH`, pattern
  predicate, pattern comprehension and `EXISTS` subquery needs a node with `{projectId: $projectId}` (the value must be
  exactly that parameter), a `(:Project {id: $projectId})` node, or a variable bound by an earlier anchored pattern.
  Variables survive `WITH` only when projected as a bare variable (alias allowed); `UNWIND` and comprehension variables
  shadow outer ones. Duplicate keys in a property map are rejected. Global admin without `projectId`: anchoring is
  skipped, the clause allowlist still applies.
- Execution (`query.py`, `reader.py`): `$projectId` is bound by the service (a caller parameter of that name, `cypher`
  or `timeout` is rejected), `LIMIT limit+1` appended when the final `RETURN` has none, rows capped at `limit+1`,
  `QUERY MEMORY LIMIT 100 MB` appended, timeout 10 s (users) or 30 s (services), read transaction, result post-check on
  every map with a `projectId`.

## Findings against Memgraph 3.13.1 (integration tests)

- A read transaction blocks `CREATE`/`SET`/`DELETE`/`REMOVE`/`MERGE` ("Accessor type READ") and `SHOW STORAGE INFO`.
- `DUMP DATABASE` and read-only `CALL` procedures **run** in a read transaction (`DUMP DATABASE` returns the whole
  graph): the clause allowlist is the only guard for them.
- `EXISTS { }` requires a `MATCH` inside in Memgraph; the guard also accepts the bare-pattern form (the database rejects it).
- Plain cartesian products are materialized and hit the memory limit quickly (HTTP 422).

## Known limits

- Anchoring relies on the writer invariant that no relationship connects nodes of different projects; the result
  post-check only sees maps that carry `projectId` (nodes, relationships and maps built from them).
- The accepted language is a subset: valid but unusual read queries (e.g. `COUNT { }` subqueries, `CASE` forms not
  listed, list slicing with unusual syntax) may be rejected; extend the parser and the corpus together.

---
status: accepted
date: 2026-10-06
---

# NL-to-Cypher approach

## Context and Problem Statement

The query service answers natural-language questions over the KG and executes direct Cypher, for users and agents. The query path must be read-only. Memgraph Community has users but no read-only roles (RBAC is Enterprise). How are questions translated and how is the path guarded?

## Decision Drivers

* Answer quality on our schema (not on generic benchmarks; existing text2cypher datasets target Neo4j dialect).
* Safety: no writes, bounded cost, no tenant leakage across projects.
* Simplicity and evaluability.

## Considered Options

* A: Intent templates only (classify question → fixed parameterized Cypher).
* B: Schema-grounded single-shot generation + validation + bounded repair.
* C: Agentic loop with schema/query tools (multiple read queries per question).
* D: Fine-tuned text2cypher model.
* E: GraphRAG (vector retrieval over element text + graph expansion).

## Decision Outcome

Chosen option: "B", with A as stub and C/E as later extensions if evaluation shows need.

Pipeline: schema contract (`contracts/kg/schema.yaml`, with descriptions and enums; drift-checked against `SHOW SCHEMA INFO`) + top-k few-shot examples → structured output `{cypher, params, explanation}` → validation (lexical denylist of write/admin keywords and non-allowlisted `CALL`, `$projectId` required, `EXPLAIN`, enforced `LIMIT`) → execution in a driver **read transaction** (Memgraph rejects writes in read-access transactions) with server timeout and per-query memory limit → up to 2 repair attempts with error feedback → answer synthesis citing element keys.

### Consequences

* Good, because defense in depth without Enterprise features; the read transaction is the primary guard, lexical checks and `EXPLAIN` catch errors early.
* Good, because the few-shot file doubles as evaluation set (execution accuracy on a held-out split).
* Bad, because single-shot generation fails on multi-hop questions needing intermediate lookups (→ option C later).
* Bad, because lexical checks are bypass-prone on their own — they are not relied upon as the only guard.

### Confirmation

Security tests: write/admin queries are rejected by validation and, with validation disabled, by the read transaction. Quality: execution accuracy on the held-out example split reported per release.

## Pros and Cons of the Options

### A: Intent templates

* Good, because safe and deterministic.
* Bad, because covers only anticipated questions.

### B: Single-shot + validation + repair

* Good, because broad coverage at one or two LLM calls; simple to evaluate.
* Bad, because limited on multi-step questions.

### C: Agentic loop

* Good, because handles exploration and multi-hop.
* Bad, because more calls, harder to evaluate and bound.

### D: Fine-tuned model

* Good, because potentially best accuracy and local execution.
* Bad, because needs training data in our schema and GPU serving.

### E: GraphRAG

* Good, because good for fuzzy content questions.
* Bad, because complementary (answers from text, not structure); needs embeddings pipeline.

## More Information

* Memgraph's own GraphChat is part of the proprietary Lab and not usable standalone; Memgraph's MCP server uses regex-only read-only checks — not adopted as sole guard.
* To verify: `QUERY MEMORY LIMIT` syntax, `EXPLAIN` behavior, read-mode enforcement for auto-commit and procedures (V-7, V-8).
* Related: ADR-0005.

## Amendments

* 2026-10-06, review decision D-2 (option A, R-036): reads are confined to one project. The lexical denylist is replaced by a **parse-tree scope guard** applied to NL-generated and raw Cypher:
  * parse with an openCypher grammar (parser to verify, V-28); allowlisted clauses only (`MATCH`, `OPTIONAL MATCH`, `WHERE`, `WITH`, `UNWIND`, `RETURN`, `ORDER BY`, `SKIP`, `LIMIT`, `UNION`); no `CALL`, no Memgraph-specific clauses;
  * every connected pattern component in every (`OPTIONAL`) `MATCH`, pattern comprehension and existential subquery must contain a node anchored by `{projectId: $projectId}` or `(:Project {id: $projectId})`, or a variable bound by an earlier anchored pattern; this relies on the invariant that no relationship connects nodes of different projects (enforced by the writers);
  * results are post-checked: every returned node or relationship map with a `projectId` must carry the requested project;
  * user queries run with a per-query memory limit and a timeout ≤ 10 s; read transaction and `EXPLAIN` stay.
  * Unscoped queries only for global `admin`. Confidential projects will get separate deployments in the future (ADR-0013).

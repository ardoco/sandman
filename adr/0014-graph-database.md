---
status: accepted
date: 2026-10-06
---

# Graph database: Memgraph Community

## Context and Problem Statement

The task prescribes Memgraph as knowledge graph. Memgraph Community is licensed under BSL 1.1 (source-available, not OSI open source; each version converts to Apache-2.0 at its change date), while the project prefers open source and avoids Enterprise features (Q-10). The user chose to keep Memgraph because speed is key and considers Neo4j slow (Q-11) and asked to look for alternatives. Which graph database do we use, and what is the fallback?

## Decision Drivers

* Query and write latency for interactive use and agent batch writes (graphs ≤ ~200k nodes per project, several projects).
* Cypher over Bolt with the official Neo4j drivers (Python and Java) — no driver rewrites, LLM-generated Cypher works.
* Multi-statement ACID transactions and read-only transactions (query guard, ADR-0009).
* License: OSI preferred; free features sufficient (no Enterprise).
* Maturity and maintenance.

## Considered Options

Research 2026-10-06 (GitHub metadata, vendor docs, published benchmarks):

* Memgraph Community 3.13 (BSL 1.1, in-memory, Bolt)
* Neo4j Community (GPL-3.0, Bolt, reference Cypher)
* ArcadeDB (Apache-2.0, Bolt 4.4/5.x with certified Neo4j drivers per vendor CI, native openCypher engine with 97.8 % TCK per vendor, multi-model, JVM, disk with cache)
* FalkorDB (SSPL-1.0, Redis module, in-memory)
* Screened out: Kùzu (archived 2025-10) and its fork LadybugDB (embedded, schema-first, no production Bolt server); Apache AGE (Cypher only inside SQL, no Bolt); NebulaGraph (nGQL, no Bolt, no release since 2024-05); TuGraph (Bolt caveats, slowing releases); HugeGraph (Gremlin-first); DuckPGQ (SQL/PGQ only); Grafeo and Engram (< 1 year old, watch list).

## Decision Outcome

Chosen option: "Memgraph Community", because it is the user's preference for speed, it is in-memory, speaks Bolt with the Neo4j drivers, supports multi-statement transactions, read-access transactions, constraints, triggers, vector and text indexes in Community, and is prescribed by the task. Internal research use fits the BSL "internal business purposes" grant; the repository references the official image and does not redistribute Memgraph.

**Fallback: ArcadeDB** (first) — the only OSI-licensed candidate combining Bolt with certified Neo4j drivers, near-complete openCypher, free multi-database and an active release cadence. **Second fallback: Neo4j Community** (maximum compatibility, GPL-3.0, single database, uniqueness constraints only).

Evidence caveat: the speed driver is not backed by independent data for our workload size. Vendor benchmarks (Memgraph 2022: Memgraph ahead; FalkorDB: FalkorDB ahead) are biased and old; one 2026 third-party test (AIMultiple, 381k nodes, 80/20 read/write) measured FalkorDB 837 QPS, Neo4j 738 QPS and Memgraph 467 QPS with undisclosed tuning; an academic study (KubeGraphBench 2026) reports Memgraph ahead on write-heavy loads and on small graphs. Therefore task **T1.8** benchmarks Memgraph, Neo4j Community and ArcadeDB on SANDMAN's own workload; the decision is revisited only if Memgraph is clearly slower or its license terms change.

### Consequences

* Good, because fast in-memory engine with the features we use, no driver changes.
* Good, because portability is kept: contract tests use portable Cypher; Memgraph-specific constructs (e.g., `QUERY MEMORY LIMIT`) stay in one place (query-service guard).
* Bad, because BSL is not OSI; distribution to third parties as part of a product would need a license review.
* Bad, because Community lacks multi-tenancy and RBAC (projects share one database; isolation limits in ADR-0013).
* Bad, because the whole graph must fit in RAM (retention, Stage 1 §4.2).

### Confirmation

T1.8 benchmark report (latency p50/p95 for the walking-skeleton and M1 workloads, ingestion throughput, memory) with all three engines; contract test suite runs green against the fallback (ArcadeDB) at least once per milestone (nightly job, non-blocking).

## Pros and Cons of the Options

### Memgraph Community

* Good, because in-memory speed (write-heavy advantage per academic study), Bolt, triggers/vector/text in Community.
* Bad, because BSL, no multi-tenancy/RBAC, RAM-bound.

### Neo4j Community

* Good, because reference Cypher and drivers, OSI (GPL-3.0), competitive in the 2026 third-party test.
* Bad, because single database, only uniqueness constraints, no RBAC; perceived slower by the user.

### ArcadeDB

* Good, because Apache-2.0, Bolt with certified drivers, multi-database free, vector support.
* Bad, because JVM/disk-based, young Cypher engine, schema introspection procedures undocumented, all compatibility numbers vendor-reported.

### FalkorDB

* Good, because fast in published (vendor and third-party) tests, multi-graph per instance.
* Bad, because SSPL (not OSI), Bolt experimental, no multi-statement transactions (conflicts with batch writes and driver transaction semantics).

## More Information

* Related: ADR-0005, ADR-0009, ADR-0013.
* Sources: research notes summarized in the Stage 1/3 documents (2026-10-06).

## Amendments

* 2026-10-06, review decision D-7 (R-052): the three-way benchmark (T1.8) and the nightly ArcadeDB job are dropped. Memgraph is checked against the T5.4 performance budget; the fallback order (ArcadeDB, Neo4j) is revisited only if it fails or the license changes. Memory: `--memory-limit` below the container limit (deployment).
* 2026-10-18, review item R-009: NFR-13 (Stage 1 §3) now names the BSL exception for Memgraph Community. It runs as a separate service from the official image and is not redistributed. The decision is unchanged.
* 2026-10-18, T5.4 performance budget: Memgraph passes. At 200 000 nodes it used 537 MiB resident, the Bolt p95 was 18.5 ms, the HTTP p95 was 42 ms, and the snapshot had 144 MiB (`docs/tasks/T5.4-performance-budget.md`). The fallback (ArcadeDB, Neo4j) is not started. The decision is unchanged. The ingest budget items fail because of ArDoCo, not because of the database (follow-up F-1).

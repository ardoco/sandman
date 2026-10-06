---
status: accepted
date: 2026-10-06
---

# KG schema, provenance and artifact versioning

## Context and Problem Statement

Memgraph stores extraction results, trace links, inconsistencies and their status. Every fact needs provenance (agent, tool, model, run, confidence, time, artifact version), and artifacts change over time. How are links, inconsistencies, provenance and versions modeled?

## Decision Drivers

* Several approaches may produce links for the same pair; links need confidence, origin, review status; inconsistencies must reference links.
* Reproducibility: earlier results must stay queryable after artifacts change.
* Continuous use on an evolving project (Q-7): frequent small changes, deletions, bounded growth.
* Re-detection of inconsistencies across artifact versions.
* Query simplicity (also for NL-to-Cypher) and write simplicity.
* Memgraph constraints: relationships cannot be endpoints of other relationships; internal ids are not stable.

## Considered Options

* Links: (L1) reified `TraceLink` nodes; (L2) relationships `(:Element)-[:TRACES_TO {…}]->(:Element)`.
* Provenance: (P1) `Run` nodes (PROV-style activity) + `runId` property on facts; (P2) provenance properties only on each fact.
* Versioning: (V1) immutable snapshot per artifact version with stable element keys; (V2) shared elements with validity intervals; (V3) current state only, history in events.

## Decision Outcome

Chosen: **L1 + P1 + V1**, plus the **single-writer rule** (each node type has exactly one owning component).

* `TraceLink` nodes with `SOURCE`/`TARGET` edges, `approach`, `approachVersion`, `configHash`, `confidence`, `status` (`candidate`/`confirmed`/`rejected`); deterministic id = hash(approach, version, configHash, source, target) → idempotent re-runs.
* `Run` nodes with agent, versions, tool, model alias and resolved model, parameters, prompt ids, input key, workflow/task ids; `USED` → `ArtifactVersion`, `PRODUCED`/`DETECTED` → facts.
* `Artifact` → `HAS_VERSION`/`CURRENT_VERSION` → `ArtifactVersion` → `HAS_ELEMENT` → `Element` (+ kind label); `Element.key` stable across versions, `Element.id` per version.
* Closed, versioned vocabulary in `contracts/kg/schema.yaml`; uniqueness constraints on `id`.

### Consequences

* Good, because links/inconsistencies carry full provenance and can be referenced; history is preserved without "superseded" bookkeeping (links on old versions are simply not current).
* Good, because fingerprints over stable keys make re-detection across versions straightforward (ADR-0007).
* Bad, because one more hop for link queries than with plain relationships.
* Bad, because each changed file creates a new version with all its elements (growth proportional to changes). Ceiling: retention policy (keep last N versions per artifact plus versions referenced by inconsistencies, current links or experiment manifests) with per-owner `prune` skills, added when KG size requires it.
* Neutral, because deletions in full snapshots are recorded as `Artifact.deletedAt`; renames appear as delete + add.

### Confirmation

Contract tests run the example writes/reads of the design document (Section 4.4) against Memgraph; schema contract and live `SHOW SCHEMA INFO` are compared at query-service startup.

## Pros and Cons of the Options

### L1 reified links

* Good, because provenance, multiple approaches per pair, reviewable, referencable by inconsistencies.
* Bad, because longer patterns.

### L2 relationship links

* Good, because short queries.
* Bad, because cannot be referenced by `INVOLVES`; parallel relationships per approach are clumsy.

### P1 Run nodes

* Good, because provenance is traversable and shared across facts of one run; cleanup of failed runs by `runId`.
* Bad, because extra nodes.

### P2 properties only

* Good, because simple.
* Bad, because duplicated metadata on every fact; no single place for run status.

### V1 snapshots

* Good, because immutable, simple writes, history for free.
* Bad, because duplication.

### V2 validity intervals

* Good, because no duplication.
* Bad, because every query needs interval predicates; complex writes.

### V3 current state only

* Good, because smallest graph.
* Bad, because earlier experiment results are lost.

## More Information

* Related: ADR-0007, ADR-0009.

## Amendments

* 2026-10-06, review decision D-5 (R-001, R-012, R-015; R-020 coupled):
  * **Keys:** TextUnit local keys are content-based (section slug path + hash of the normalized sentence + occurrence counter); `sentenceIndex` stays a property. Inserting text no longer changes other keys (vectors `sentenceKey`).
  * **Reverts and re-added files:** `Ingestion` nodes (owner extraction-agent, `ing_` = stableId(projectId, seq)) with `ACTIVATED` → `ArtifactVersion` and `DELETED` → `Artifact` edges replace `PREVIOUS`. A path returning to earlier content re-activates the existing version (moves `CURRENT_VERSION`, clears `deletedAt`), reported in `reactivatedVersionIds` and processed like a new version. Idempotency is state-based: a manifest matching the current state changes nothing.
  * **Active facts:** each artifact version's element set is written in one transaction; re-extraction replaces it (dropped elements lose `HAS_ELEMENT`). `TraceLink.active`: links are written in batches inactive and activated in the run's final transaction, which deactivates earlier links of the same approach between the same versions; the tlr-agent deactivates links of approaches disabled in its configuration at startup. Current link = active and both endpoints current. Readers never need `Run.status`.

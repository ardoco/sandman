---
status: accepted
date: 2026-10-06
---

# Pluggable TLR approaches, extractors and detectors

## Context and Problem Statement

The TLR agent must run various tools and approaches; approaches must be pluggable. Code extraction (Q-5) and further inconsistency detectors (Q-6) will come from separate services that are plugged in; ArDoCo's inconsistency detection (branch `feature/make-inconsistency-great-again`) and ArDoCo/LiSSA TLR run in-process in the Java agents (Q-1). How are approaches, extractors and detectors added without changing contracts or the orchestrator?

## Decision Drivers

* New approaches/extractors/detectors without contract changes (NFR-11, FR-19).
* Plugged-in services may be written in any language and are developed separately.
* Single-writer rule and provenance: every fact must be written by its owner with run provenance.
* Plugins should not need KG write credentials (Memgraph Community cannot restrict them).

## Considered Options

* In-process: (I1) Java SPI (`ServiceLoader`) in tlr-agent and inconsistency-agent; (I2) no in-process extension, everything as services.
* Out-of-process plugins: (P1) A2A plugin services implementing a fixed SANDMAN plugin skill; plugins return results, the owner writes; (P2) plain-HTTP plugin contract (`GET /manifest`, `POST /v1/extract`, `POST /v1/detect`); (P3) plugins as full agents that write the KG themselves and publish events.

## Decision Outcome

Chosen: **I1 + P1 + P2** (P2 added after Q-13: "A2A where it makes sense, plain HTTP where better").

* `TraceLinkApproach { id(); version(); supports(); recover(TlrInput, TlrContext) }` — ArDoCo and LiSSA in-process; configured in `config/tlr/approaches.yaml`; `configHash` enters the link id.
* `InconsistencyDetector { id(); version(); types(); detect(DetectionScope, DetectorContext) }` — ArDoCo inconsistency detection in-process (pinned commit of the feature branch until released).
* Plugin skills: `sandman.plugin.extract` (extraction-agent calls; input: batch of artifact versions with read-only storage paths + current artifact list; output: elements, relations, explicit references) and `sandman.plugin.detect` (inconsistency-agent calls; input: scope + query-service URL; output: findings with involved element keys/link ids). Registered in `config/extraction/plugins.yaml` and `config/inconsistency/plugins.yaml`; plugin id/version from its Agent Card go into `Run.tool`/`toolVersion`.
* Plain-HTTP variant ([contracts/openapi/plugin-http.yaml](../contracts/openapi/plugin-http.yaml)): `GET /manifest`, `POST /v1/extract`, `POST /v1/detect`, synchronous within the caller's timeout, same payload schemas. Configuration selects `transport: a2a | http` per plugin.
* Rule of thumb: A2A when a call can exceed a few minutes, must be cancellable, reports progress, or the plugin is itself an LLM agent; plain HTTP for fast deterministic tools and for wrapping existing services.
* The same mechanism can serve TLR plugins later (`sandman.plugin.recover-links`) if a non-Java approach appears.

### Consequences

* Good, because plugins stay simple (no KG writes, no events, no lifecycle logic) and the owner keeps validation, provenance and fingerprinting in one place.
* Good, because plugin authors choose the cheaper transport; payloads and owner-side handling are identical (one `PluginClient` interface with two implementations).
* Bad, because two plugin transports to test (both covered by the plugin stubs: extract via A2A, detect via HTTP).
* Bad, because results travel through the owner (memory for very large code bases; ceiling: page results via multiple task artifacts if needed).
* Bad, because in-process approaches share the agent's JVM (memory, dependency conflicts, e.g., LangChain4j versions).
* Bad, because detector plugins read the KG through the query-service (row limits must be raised for service callers).

### Confirmation

Adding an approach or plugin requires no change outside the agent's module and `config/`; contract tests unchanged. A sample plugin (stub) per plugin skill is part of the walking skeleton.

## Pros and Cons of the Options

### I1 Java SPI

* Good, because direct access to ArDoCo/LiSSA APIs and intermediate data.
* Bad, because shared JVM.

### I2 services only

* Good, because isolation.
* Bad, because ArDoCo/LiSSA would need wrappers — contradicts Q-1.

### P1 A2A plugin skill

* Good, because reuses the existing protocol, libraries and auth.
* Bad, because plugin authors need an A2A server (small with the SDKs).

### P2 plain HTTP

* Good, because trivial for any language and for wrapping existing services.
* Bad, because synchronous only (no cancel/progress); long-running work belongs to A2A.

### P3 plugins as KG writers

* Good, because no data through the owner.
* Bad, because breaks the single-writer rule; plugins need write credentials with no way to restrict them; fingerprinting/lifecycle duplicated.

## More Information

* Related: ADR-0001, ADR-0002, ADR-0005, ADR-0007.

## Amendments

* 2026-10-06, review decision D-9 (R-047): plugins are **external systems**: any language and internal design (H1/H2 do not apply), but obligations: run on the `internal` network without egress; call LLMs only through the SANDMAN gateway with their own virtual keys (`plugin-<id>` and `plugin-<id>-local`, provisioned in `deploy/gateway/config.json`); honor `llmPolicy` from the plugin input; never write the KG.
* 2026-10-06, review decision D-4 (R-033): each plugin gets its own outbound token (query-service caller `plugin:<id>`, read-only queries only) and an inbound token that owners present when calling it; owners never send their own service token to plugins.
* 2026-10-06, review decision D-7 (R-051): cut until needed: the `llm-rag` and `external-http` approaches, `cypher-rule` detectors and the `sandman.plugin.recover-links` idea. Both plugin transports stay in the contract; implementations support one each until needed: the extraction-agent calls A2A plugins, the inconsistency-agent HTTP plugins.
* 2026-10-06, Q-20: inconsistency detectors (incl. the ArDoCo adapter) consume the active trace links of all approaches from the KG (ADR-0015).

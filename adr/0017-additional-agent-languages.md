---
status: accepted
date: 2026-10-07
---

# Additional agent languages: TypeScript, Go, Rust

## Context and Problem Statement

SANDMAN agents are written in Python or Java (ADR-0001), each on a small shared library per language with the same contracts (ADR-0010). The user wants to be able to write agents in TypeScript, Go and Rust as well. Plugins can already be written in any language (ADR-0012), but they are external systems without library support. What is needed so that a full SANDMAN agent (A2A skills, events, KG access, LLM via gateway, provenance) can be written in these languages, and is it feasible?

## Decision Drivers

* Every agent keeps exactly one language (NFR-2); the contracts in `contracts/` stay language-neutral and normative.
* No agent frameworks in any language (NFR-3).
* Behavioral parity with the Python and Java libraries, checked by the shared conformance vectors (NFR-4).
* Maintenance cost: every language adds a library, CI job, Dockerfile template and dependency updates (R-2, RK-14).
* Only maintained, open-source building blocks (NFR-13).

## Feasibility (research 2026-10-07, GitHub/npm/crates.io metadata)

| Building block | TypeScript (Node.js) | Go | Rust |
|---|---|---|---|
| A2A SDK (spec 1.0, JSON-RPC, server + client) | `@a2a-js/sdk` 1.3 (a2aproject, Apache-2.0), "v1.0 stable", JSON-RPC/REST/gRPC | `a2a-go` v2.6 (a2aproject, Apache-2.0), A2A v1.0, `a2asrv`/`a2aclient` | `a2a-lf` 0.4 (a2aproject `a2a-rs`, Apache-2.0), A2A v1, axum server — **pre-1.0** |
| NATS JetStream | `nats.js` v3 (official) | `nats.go` (official) | `async-nats` (official) |
| Bolt driver (Memgraph) | `neo4j-driver` 6.x (official) | `neo4j-go-driver` v6 (official) | `neo4rs` 0.9 RC (neo4j-labs, community) or `rsmgclient` (Memgraph, C binding) — **Memgraph compatibility unverified** |
| OpenAI-compatible client | `openai` (official) | `openai-go` (official) | `async-openai` (community, MIT) |
| JSON Schema 2020-12 | Ajv 8 (`ajv/dist/2020`) | `santhosh-tekuri/jsonschema` v6 | `jsonschema` crate |
| JCS (RFC 8785) | `canonicalize` (reference author) | `cyberphone/json-canonicalization` | `serde_json_canonicalizer` |
| OpenTelemetry | official | official | official |

Result: **feasible**. TypeScript and Go have official, stable building blocks throughout. Rust is feasible but less mature: the A2A SDK is pre-1.0 and the Bolt driver is community-maintained.

## Considered Options

* (A) Full parity libraries in all three languages now.
* (B) **Minimal agent kits** per language, implementing exactly the agent-facing contract (skill server convention, auth, status, completion events, KG client with auto-commit and memory-limit handling, run provenance, ids/keys/fingerprints, contract validation, LLM client against `llm-replay`), verified by the shared conformance vectors and a cross-language interop test; built when the first agent in that language is planned. Tiers: TypeScript and Go tier 1, Rust tier 2 (after A2A `a2a-lf` 1.0 and a Memgraph check of the Bolt driver).
* (C) No new languages; such components only as plugins (any language, no library).

## Decision Outcome

Chosen: **B** (accepted 2026-10-07). Python and Java remain the languages of the existing agents (ADR-0001 unchanged). A new language is admitted for an agent once its kit exists and passes the conformance vectors and the interop test against the Python orchestrator and a Java agent. Plugins remain possible in any language.

* Layout: `libs/typescript/sandman-agent`, `libs/go/sandman-agent`, `libs/rust/sandman-agent`; services as today under `services/<name>`.
* Build tools (binding, like uv/Maven): TypeScript npm workspaces with `package-lock.json`, Go modules, Rust Cargo workspace with `Cargo.lock`. Naming: npm scope `@ardoco/sandman-*`, Go module `github.com/ArDoCo/sandman/...`, Rust crates `sandman-*` (Q-22: default confirmed; unpublished until needed).
* Framework guard per language: ban LangChain(.js)/LangGraph(.js)/LlamaIndex/Mastra (TS), LangChainGo/Eino (Go), langchain-rust/rig (Rust) in agent code.
* The kits do not include the cut modules (tool loop, record/replay — `llm-replay` covers it).

### Consequences

* Good, because agents can be written by people in their preferred ecosystem without changing contracts.
* Good, because the contracts and conformance vectors already make a third, fourth and fifth implementation testable.
* Bad, because each kit costs about M–L effort plus ongoing maintenance (three more CI jobs, Dockerfile templates, dependency bots).
* Bad, because Rust depends on pre-1.0/community components (RK-23).

### Confirmation

Per kit: conformance vectors (`stableId`, `sectionKey`, `sentenceKey`, `fingerprint`, `eventEnvelope`, `skillInvocation`) pass; interop test (orchestrator → new-language agent → completion event; new-language client → Java and Python agents); Memgraph integration test (read transaction rejects writes, `init_schema`, memory-limit not retried).

## Pros and Cons of the Options

### A full libraries now

* Good, because everything is ready at once.
* Bad, because effort before any agent needs it (YAGNI); Rust maturity risk paid up front.

### B minimal kits on demand

* Good, because cost is paid when an agent in that language is planned; tiers reflect maturity.
* Bad, because the first agent in a language waits for its kit.

### C plugins only

* Good, because no cost.
* Bad, because plugins cannot be orchestrated agents (no A2A skills of their own, no events, no KG writes).

## More Information

* Q-21 decided 2026-10-07: none of the current agents is moved or planned in these languages; the kits are for future agents only (MX stays on demand).
* Q-22 decided 2026-10-07: proposed default naming (npm `@ardoco/sandman-*`, Go module `github.com/ArDoCo/sandman/libs/go/...`, crates `sandman-*`), unpublished until needed.
* Related: ADR-0001, ADR-0002, ADR-0010, ADR-0012.

# TX.3 Rust agent kit

Milestone MX · Effort L · Depends on: M0, V-40 · Risk: RK-23 (pre-1.0 and community dependencies), RK-24

Status: ready (2026-11-07). The user accepted RK-23. The kit is built in `libs/rust/sandman-agent`.

## Preconditions

The plan starts TX.3 after `a2a-lf` 1.0 or with RK-23 accepted. ADR-0017 also builds a kit only when the first agent in the language is planned.

| Precondition | State (2026-11-07) |
| --- | --- |
| `a2a-lf` 1.0 released | Not met. The newest version on crates.io is 0.4.2. |
| RK-23 accepted | Met. The user accepted RK-23 on 2026-11-07: the kit uses `a2a-lf` 0.4.x (pre-1.0). |
| V-40: Rust Bolt driver | The kit uses `neo4rs` 0.9 (Bolt). The target is Traverse, not Memgraph. Unit tests use fakes. The live check against Traverse is open. |
| First Rust agent planned (Q-21) | Not met. The kit is built on explicit request. No agent uses it yet. |

Pin rule (condition of the RK-23 acceptance):
- Pin the exact version of `a2a-lf` in `Cargo.toml` (`=0.4.2`) and pin the other pre-1.0 dependencies.
- Confine all `a2a-lf` types to the single module `a2a/protocol.rs`. The rest of the kit sees only kit types. A 1.0 upgrade stays local to that module.

## Context

ADR-0017 admits Rust for agents once a minimal kit exists. The kit is the Rust counterpart of `libs/typescript/sandman-agent` (TX.1).

Read first:
- `adr/0017-additional-agent-languages.md`
- `docs/tasks/TX.1-typescript-agent-kit.md` (module list, driver rules, acceptance)
- `contracts/conformance/vectors.json` (normative vectors, reused unchanged)

## Scope

Location: `libs/rust/sandman-agent` in a Cargo workspace (`libs/rust/Cargo.toml`, `Cargo.lock`). Crate `sandman-agent`, unpublished.

Same modules as TX.1: `ids`, `contracts`, `config`, `auth`, `status`, `events`, `kg`, `a2a`, `llm`, `telemetry`.

| Module | Dependency |
| --- | --- |
| `a2a` | `a2a-lf` (pin the exact version) |
| `events` | `async-nats` |
| `kg` | `neo4rs` (Bolt) |
| `llm` | `async-openai` against the gateway |
| `contracts` | `jsonschema` |
| `ids` | `serde_json_canonicalizer` (RFC 8785) |
| `telemetry` | OpenTelemetry |

Also in scope: a conformance test suite on the 40 vectors, fake-based unit tests, A2A server tests, a CI job with a paths filter, and `Dockerfile.template`.

## Rules

- Same Traverse and Memgraph driver rules as TX.1.
- No agent framework (NFR-3). The CI guard bans langchain-rust and rig.
- No record or replay module and no tool loop.
- Pin all pre-1.0 dependencies.

## Out of scope

- A Rust agent and its Agent Card.
- Publishing to crates.io.
- The interop test and the Memgraph integration test (need the Docker stack).

## Acceptance criteria

- All conformance vectors pass byte-for-byte, with `vectors.json` unchanged.
- Fake-driver tests show: read uses a read transaction, the memory-limit error is not retried, `init_schema` ignores "already exists".
- A2A server tests reject missing skill, unknown skill and invalid input with the expected error code.
- `cargo test` and `cargo clippy` pass in `libs/rust`. The CI job runs both.

---
status: accepted
date: 2026-10-06
---

# Monorepo layout with shared contracts

## Context and Problem Statement

The system has five services in two languages, two agent libraries, shared contracts, deployment files and ADRs. One repository or several?

## Decision Drivers

* Contract changes must update schemas, both libraries and affected services atomically.
* One Compose file for the whole system.
* Small team, research prototype; low release overhead.

## Considered Options

* A: Monorepo.
* B: Multi-repo (one per service, per library, contracts repo).
* C: Hybrid (libraries + contracts in one repo, services separate).

## Decision Outcome

Chosen option: "A". Layout (details in Stage 2):

```text
adr/                      ADRs (this folder)
docs/                     design documents
contracts/                JSON Schemas (skills, events, envelope, cassette), Agent Cards,
                          OpenAPI, kg/schema.yaml, conformance/ vectors
libs/python/sandman-agent/
libs/java/agent-lib/, libs/java/agent-lib-a2a/
services/orchestrator/, services/extraction-agent/, services/query-service/   (Python, uv workspace)
services/tlr-agent/, services/inconsistency-agent/                            (Java, Maven modules)
config/                   rules, approaches, detectors, extraction settings
deploy/                   docker-compose.yml, gateway and NATS config, secrets/ (git-ignored)
tools/cli/                sandman CLI (Python)
tests/e2e/                walking skeleton and end-to-end tests
experiments/              experiment manifests (results archived outside Git)
```

Python: one uv workspace at the root. Java: root Maven aggregator POM for `libs/java/*` and Java services. CI runs per-language jobs plus conformance and end-to-end jobs; path filters keep runs short.

### Consequences

* Good, because one pull request can change a contract and both implementations; conformance tests run together.
* Good, because one version (Git commit) identifies the whole system in run provenance.
* Bad, because mixed build tooling in one repository.
* Bad, because no independent versioning of services (not needed for a prototype).

### Confirmation

CI fails if `contracts/` changes without passing conformance in both languages.

## Pros and Cons of the Options

### A: Monorepo

* Good, because atomic changes and one provenance version.
* Bad, because larger repository and CI matrix.

### B: Multi-repo

* Good, because independent release cycles.
* Bad, because contract changes need coordinated releases across repositories; version matrix in Compose.

### C: Hybrid

* Good, because libraries versioned separately.
* Bad, because still cross-repo changes for most features.

## More Information

* Repository license: AGPL-3.0 (existing `LICENSE`).
* Related: ADR-0010.

## Amendments

* 2026-10-06, review decision D-3: new service `services/llm-replay` (standalone Python, member of the uv workspace).

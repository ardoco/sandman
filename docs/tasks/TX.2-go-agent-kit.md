# TX.2 Go agent kit

Milestone MX · Effort M–L · Depends on: M0 · Risk: RK-14 (maintenance cost per language)

Status: kit built in `libs/go/sandman-agent` (ids, contracts, config, auth, status, telemetry, kg, events, llm, a2a). Unit tests and the conformance vectors pass. Not yet verified: interop with the Python orchestrator, the Traverse integration, a Docker build, a CI run, and real NATS and Neo4j servers.

## Context

ADR-0017 admits Go for agents once a minimal kit exists. The kit offers exactly the agent-facing contract. It is the Go counterpart of `libs/typescript/sandman-agent` (TX.1), `libs/python/sandman-agent` and `libs/java/agent-lib`.

Read first:
- `adr/0017-additional-agent-languages.md` (scope, layout, confirmation)
- `docs/tasks/TX.1-typescript-agent-kit.md` (pattern to follow)
- `libs/typescript/sandman-agent/` (surface and tests to mirror)
- `libs/python/sandman-agent/src/sandman_agent/` (reference behavior)
- `contracts/conformance/vectors.json` (normative vectors, reused unchanged)

## Preconditions of ADR-0017

| Precondition | State | Consequence |
| --- | --- | --- |
| The kit is built when the first agent in the language is planned (Q-21: no agent is planned) | Not met. The kit is built on explicit request. | No agent uses the kit. The kit stays unpublished. |
| Interop test: orchestrator to Go agent to completion event, and Go client to Java and Python agents | Not met. It needs the Docker stack. | Open. The language is not admitted for an agent (NFR-2) until the test passes. |
| Traverse integration test: read transaction rejects writes, `InitSchema`, memory-limit not retried | Not met. It needs a Traverse container. | Open. Unit tests use a fake driver. |

## Scope

Location: `libs/go/sandman-agent` (ADR-0017 layout). Go module `github.com/ArDoCo/sandman/libs/go/sandman-agent`, unpublished, with `go.mod` and `go.sum`.

| Package | Content |
| --- | --- |
| `ids` | `Sha256Hex`, `StableID`, `CanonicalJSON` (RFC 8785, cyberphone JCS), `SectionLocalKey`, `SentenceLocalKey`, `NormalizeConceptName`, `Fingerprint`, `EvidenceHash`, `NewRunID`, `NewWorkflowID` |
| `contracts` | `santhosh-tekuri/jsonschema` v6 validation against `contracts/*.schema.json` (no network). Same refs as the Python kit. |
| `config` | `Settings` from `SANDMAN_*` variables, `*_FILE` secrets |
| `auth` | `TokenVerifier` (sha256 hashes, `crypto/subtle` compare), `ActorFromMetadata` |
| `status` | `StatusTracker`, `GET /v1/status` payload |
| `events` | CloudEvents envelope, validation, JetStream publisher on `nats.go` (`Nats-Msg-Id`, ack, three retries), stream and durable consumer config |
| `kg` | `Client` on `neo4j-go-driver` v6 (`Read`, `Write`, `WriteTx`, `RunAutocommit`, `InitSchema`), memory-limit error not retried, `RunRecorder` |
| `a2a` | Skill server on `a2a-go` v2: skill metadata, one data part, input and output validation, `SkillError`, one completion event before completion, idempotent messageId, bearer auth |
| `llm` | `openai-go` client against the gateway, HTTP 402 to `BudgetExceededError` |
| `telemetry` | OpenTelemetry spans for KG calls and events |

Also in scope: a conformance test suite that reads `contracts/conformance/vectors.json` (40 vectors, unchanged), a CI job `go` with a paths filter, and `libs/go/Dockerfile.template`.

## Rules

- Driver quirks of Traverse (from the Traverse migration note):
  - `Read` uses a read transaction. Traverse rejects the words drop, set, delete, create, merge and remove in read query text, also in string literals. Callers pass such words as parameters.
  - `InitSchema` ignores "already exists". `CREATE INDEX` is not idempotent.
  - The memory-limit code `Neo.TransientError.General.MemoryPoolOutOfMemoryError` becomes `MemoryLimitError`. It is not retried.
  - Reads that feed an LLM end in `ORDER BY` on a stable key.
- No agent framework (NFR-3). The CI guard bans LangChainGo, Eino and LlamaIndex ports. The guard follows the TX.1 guard.
- No record or replay module and no tool loop (`llm-replay` covers it).
- Use the Go standard library first (`net/http`, `encoding/json`, `crypto`).

## Out of scope

- TypeScript and Rust kits (TX.1, TX.3).
- A Go agent and its Agent Card.
- Publishing the module.
- The interop test and the Traverse integration test (see Preconditions).
- Building the Docker image (the template is not built in CI).

## Acceptance criteria

- Every `stableId`, `sectionKey`, `sentenceKey`, `conceptName`, `fingerprint`, `evidenceHash` case, and the `eventEnvelope` and `skillInvocation` cases, pass byte-for-byte.
- `CanonicalJSON` output matches the `canonical` strings of the `inputKey`, `actionKey` and `cassetteKey` vectors.
- Unit tests with a fake driver show: read uses a read transaction, the memory-limit error is not retried, `InitSchema` ignores "already exists".
- The skill server rejects missing skill, unknown skill and invalid input with the expected error code.
- `go vet ./...` and `go test ./...` pass in `libs/go/sandman-agent`. The CI job runs both.
- `make contracts` reports `contracts ok`.

## Risks

| Risk | Handling |
| --- | --- |
| RK-14: one more language to maintain | Minimal scope. One CI job. Dependency bot entry. |
| `a2a-go` API differs from `a2a-sdk` (Python) and `@a2a-js/sdk` | The skill convention is tested through the SDK request handler, not only by unit tests of helpers. |
| `neo4j-go-driver` v6 API differs from the Python and JS drivers | The fake driver sits behind a small interface. Unit tests cover read transaction, retry and error mapping. |
| Number handling: JSON numbers decode to `float64` by default | The kit decodes with `json.Number`. JCS output is checked against the vectors. |
| Unicode case folding differs between runtimes | `NormalizeConceptName` uses `strings.ToLower` after NFKC where the vectors require it. A gap is open for characters outside the vectors. |
| JSON Schema format checks differ from Python | The kit does not enforce format keywords, as the Python kit does. |
| Toolchain absent in some environments | CI pins the Go version. Local work needs `go` installed. |
| Interop not verified | Listed under Preconditions. Do not admit an agent before the test passes. |

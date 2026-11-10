# TX.1 TypeScript agent kit

Milestone MX · Effort M–L · Depends on: M0 · Risk: RK-14 (maintenance cost per language)

Status: kit built in `libs/typescript/sandman-agent` (64 unit and conformance tests, CI job `typescript`, `libs/typescript/Dockerfile.template`). The interop test and a Traverse integration test are open. Two preconditions of ADR-0017 are not met (see Preconditions).

## Context

ADR-0017 admits TypeScript for agents once a minimal kit exists. The kit offers exactly the agent-facing contract. It is the TypeScript counterpart of `libs/python/sandman-agent` and `libs/java/agent-lib`.

Read first:
- `adr/0017-additional-agent-languages.md` (scope, layout, confirmation)
- `libs/python/sandman-agent/src/sandman_agent/` (reference behavior)
- `contracts/conformance/vectors.json` (normative vectors)

## Preconditions of ADR-0017

| Precondition | State | Consequence |
| --- | --- | --- |
| The kit is built when the first agent in the language is planned (Q-21: no agent is planned) | Not met. The kit is built on explicit request. | No agent uses the kit yet. The kit stays unpublished. |
| Interop test: orchestrator to TypeScript agent to completion event, and TypeScript client to Java and Python agents | Not met. It needs the Docker stack. | Open. The language is not admitted for an agent (NFR-2) until the test passes. |
| Traverse integration test: read transaction rejects writes, `init_schema`, memory-limit not retried | Not met. It needs a Traverse container. | Open. Unit tests use a fake driver. |

## Scope

Location: `libs/typescript/sandman-agent` in an npm workspace (`libs/typescript/package.json`, `package-lock.json`). npm scope `@ardoco/sandman-agent`, unpublished.

| Module | Content |
| --- | --- |
| `ids` | `sha256Hex`, `stableId`, `canonicalJson` (RFC 8785), `sectionLocalKey`, `sentenceLocalKey`, `normalizeConceptName`, `fingerprint`, `evidenceHash`, `newRunId`, `newWorkflowId` |
| `contracts` | Ajv 2020 validation against `contracts/*.schema.json` (no network). Same refs as the Python kit. |
| `config` | `Settings` from `SANDMAN_*` variables, `*_FILE` secrets |
| `auth` | `TokenVerifier` (sha256 hashes, constant-time compare), `actorFromMetadata` |
| `status` | `StatusTracker`, `GET /v1/status` payload |
| `events` | CloudEvents envelope, validation, JetStream publisher (`Nats-Msg-Id`, ack, three retries), stream and durable consumer config |
| `kg` | `KgClient` (`read`, `write`, `writeTx`, `runAutocommit`, `initSchema`), memory-limit error not retried, `RunRecorder` |
| `a2a` | Skill server on `@a2a-js/sdk`: skill metadata, one data part, input and output validation, `SkillError`, one completion event before completion, idempotent messageId, bearer auth |
| `llm` | `openai` client against the gateway, HTTP 402 to `BudgetExceededError` |
| `telemetry` | OpenTelemetry spans for KG calls and events |

Also in scope: a conformance test suite that reads `contracts/conformance/vectors.json`, a CI job, and `Dockerfile.template`.

## Rules

- Driver quirks of Traverse (from the Traverse migration note):
  - `read` uses a read transaction. Traverse rejects the words drop, set, delete, create, merge and remove in read query text, also in string literals. Callers pass such words as parameters.
  - `initSchema` ignores "already exists". `CREATE INDEX` is not idempotent.
  - The memory-limit code `Neo.TransientError.General.MemoryPoolOutOfMemoryError` becomes `MemoryLimitError`. It is not retried.
  - Reads that feed an LLM end in `ORDER BY` on a stable key.
- No agent framework (NFR-3). The CI guard bans LangChain.js, LangGraph.js, LlamaIndex and Mastra.
- No record or replay module and no tool loop (`llm-replay` covers it).

## Out of scope

- Go and Rust kits (TX.2, TX.3).
- A TypeScript agent and its Agent Card.
- Publishing to npm.
- The interop test and the Traverse integration test (see Preconditions).

## Acceptance criteria

- Every `stableId`, `sectionKey`, `sentenceKey`, `conceptName`, `fingerprint`, `evidenceHash` case, and the `eventEnvelope` and `skillInvocation` cases, pass byte-for-byte.
- `canonicalJson` output matches the `canonical` strings of the `inputKey`, `actionKey` and `cassetteKey` vectors.
- Unit tests with a fake driver show: read uses a read transaction, the memory-limit error is not retried, `initSchema` ignores "already exists".
- The skill server rejects missing skill, unknown skill and invalid input with the expected error code.
- `npm run typecheck` and `npm test` pass in `libs/typescript`. The CI job runs both.
- `make contracts` reports `contracts ok`.

## Risks

| Risk | Handling |
| --- | --- |
| RK-14: one more language to maintain | Minimal scope. One CI job. Dependency bot entry. |
| `@a2a-js/sdk` API differs from `a2a-sdk` (Python) | The skill convention is tested through the SDK request handler, not only by unit tests of helpers. |
| Number handling: JSON numbers without float widening | The kit parses with plain JSON. Large integers beyond 2^53 are not supported. |
| Unicode case folding differs between runtimes | `normalizeConceptName` approximates case folding with `toUpperCase().toLowerCase()`. A gap is open for characters outside the vectors. |
| Ajv format checks differ from Python | The kit sets `validateFormats: false`, as the Python kit does. Format keywords are not enforced. |
| Interop not verified | Listed under Preconditions. Do not admit an agent before the test passes. |

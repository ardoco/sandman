# SANDMAN – Stage 4: Plan Review

Date: 2026-10-06 · Update: all your decisions D-1…D-10 and Q-20 are recorded in §3/§4 and applied (§8); the decision batch of 2026-10-09 is in §3 and §9 · Reviewed state: `docs/stage-1-analysis-architecture.md` (rev. 3), `docs/stage-2-contracts-repository.md` (rev. 2), `docs/stage-3-implementation-plan.md` (rev. 2), `docs/tasks/T0.1–T0.8`, `adr/0001–0014`, `contracts/**`, `deploy/**`.

**Method.** Three independent review passes covered all artifacts:
- (A) requirements traceability and constraint compliance;
- (B) cross-document consistency and implementability;
- (C) critical assessment: over-engineering, security, research validity.

The reviewers had no access to the authoring discussion. Their raw reports (about 95 findings) were merged and deduplicated into 52 log entries (R-001…R-052), each backed by the evidence of at least one pass. Problems were **not** fixed; only trivial slips were corrected (Section 7).

**Abbreviations.** S1/S2/S3 = stage 1/2/3 document; WS = walking skeleton (S2 §5); `c/` = `contracts/`; Src A/B/C = review pass and its finding number.

## 1. Consistency and completeness checks

### 1.1 Requirements traceability matrix

| Req | Design (sections) | Contracts | ADRs | Stub / test | Tasks | Coverage | Gap (→ log) |
|---|---|---|---|---|---|---|---|
| FR-1 ingest, hash, keep versions | S1 §3.4a, §4.1, §4.2, §5.2 | c/openapi/extraction-agent.yaml; c/events.schema.json `IngestionCompleted`; c/kg/schema.yaml | 0005 | S2 §4.2; WS 2, 7 | T0.5, T0.10, T5.1 | partial | Reverts (R-012); ingestion record not stored (R-020) |
| FR-2 structure + content extraction | S1 §4.1, §5.2 | `ExtractArtifactVersions*`, `PluginExtract*`; kg Element/Concept | 0005, 0012 | S2 §4.2, §4.6; WS 4 | T0.5, T0.10, T1.1–T1.5 | full | Code extraction depends on the external team (RK-12) |
| FR-3 explicit links | S1 §5.2 | `PluginExtractOutput.explicitReferences`; S2 §1.6 | 0005 | S2 §4.2; WS 4 | T0.5, T1.3 | partial | Matcher scope/direction (R-013); links lost on new versions (R-014 → T1.12) |
| FR-4 pluggable TLR | S1 §4.1, §5.3 | `RecoverTraceLinks*`, `ListApproachesOutput`; tlr-agent card + OpenAPI; `TracelinksUpdated` | 0005, 0012 | S2 §4.3; WS 4 | T0.6, T2.1–T2.4 | full | Incremental scope and kind pairs (R-022) |
| FR-5 pluggable detectors | S1 §5.4 | `DetectInconsistencies*`, `Finding`, `PluginDetect*`; plugin-http.yaml | 0007, 0012 | S2 §4.4, §4.6; WS 5 | T0.7, T0.10, T3.1–T3.3 | full | ArDoCo pairing and link source (R-025, Q-20) |
| FR-6 inform users | S1 §10 | query-service `Me.lastVisit`; inconsistency-agent `changedSince` | 0011 | notification log; WS 5 | T0.7, T0.9 (no spec) | partial | Notification log format unspecified; T0.9 unspecified (R-026) |
| FR-7 view + change status incl. reopen | S1 §3.4c, §4.3, §10 | inconsistency-agent.yaml; query-service.yaml | 0007, 0011, 0013 | S2 §4.4; WS 6 | T0.7, T0.9, T3.4 | partial | Users cannot reopen RESOLVED (R-018 → T1.13) |
| FR-8 re-detection + auto transitions | S1 §4.2, §4.3, §6 | `InconsistenciesUpdated`; vectors `fingerprint` | 0007 | WS 8; T0.7 scenario | T0.7, T0.8 | partial | Fingerprints (R-001); scope and auto-resolve guard (R-011); idempotency (R-039) |
| FR-9 notify orchestrator | S1 §3.2 | c/events.schema.json | 0003 | all stubs publish | T0.2, T0.3, T0.5–T0.7 | full | Double completion signal (R-050) |
| FR-10 orchestration | S1 §3.4d, §5.1, §6 | orchestrator.yaml | 0006 | S2 §4.1; WS 3, 10 | T0.8 | full | Concurrency (R-016); debounce (R-023 → T1.13); inbox (R-024 → T1.13) |
| FR-11 NL + Cypher | S1 §3.4b, §7 | query-service `/v1/query/*`, `/v1/schema`; kg schema | 0009 | S2 §4.5 | T0.9, T4.1–T4.4 | full | Cross-project reads (R-036) |
| FR-12 provenance | S1 §4.1, §11.6 | kg `Run` | 0005, 0010 | Run per invocation | T0.2/T0.3 `RunRecorder`, T1.6 | partial | LangChain4j calls not recorded (R-017); `Inconsistency`/`Concept` lack `runId` |
| FR-13 gateway-only LLM | S1 §2.3, §8, §11.2 | deploy/* | 0004 | T0.4 | T0.2–T0.4 | partial | Egress loopholes (R-035); admin plane (R-034) |
| FR-14 independent + communicate | S1 §3.1, §3.3 | agent cards; `SkillInvocationMetadata` | 0002, 0003 | vectors `skillInvocation` | T0.2–T0.4, T0.11 | full | – |
| FR-15 stubs honor final contract | S2 §4 | all | 0010 | S2 §4, §5 | T0.5–T0.11 | partial | Planned operations missing from contracts (R-019 → T1.13, planned ops added as 501); T0.9–T0.12 without spec (R-026) |
| FR-16 manual triggers | S1 §6 | orchestrator/query-service `createWorkflow` | 0006 | S2 §4.1 | T0.8, T0.10 | partial | `params` untyped; manual trigger not in the WS (R-021 → T1.13) |
| FR-17 benchmark export | S1 §7, §11.6 | query-service `export/*` | – | WS 9 | T2.3 | partial | Benchmark formats undefined (R-027 → T2.3) |
| FR-18 users + roles | S1 §11.2 | query-service auth/admin; S2 §1.5 | 0013 | WS 1, 6 | T0.9, T4.4 | partial | Token impersonation (R-033); read isolation (R-036); session security (R-045) |
| FR-19 plug-in services | S1 §3.3, §5.2, §5.4 | plugin schemas, plugin-http.yaml, templates | 0012 | S2 §4.6; WS 4, 9 | T0.5, T0.7, T0.10 | full | Plugin rules vs. H1/H2/H4 (R-047) |
| FR-20 continuous processing | S1 §4.2, §6 | extraction-agent check + `mode` | 0005 | WS 7–8 | T0.5, T0.10, T5.x | partial | Scope (R-011); reverts (R-012); idempotency (R-039) |
| NFR-1 containers, one Compose | S1 §2.3 | deploy/docker-compose.yml | 0008 | CI compose | T0.1, T0.11 | full | – |
| NFR-2 one language per agent | S1 §2.2, §5 | – | 0001 | – | T0.1 | partial | Plugins "any language" (R-047) |
| NFR-3 no frameworks | S1 §9 | – | 0001, 0010 | framework guard | T0.1–T0.3 | partial | Ban not scopable per package (R-030 → T2.2, T3.1) |
| NFR-4 library parity | S1 §9.6 | vectors, schemas | 0010 | conformance | T0.2, T0.3 | partial | Missing vectors for derived keys, policy, forwarder (R-028 → T1.13) |
| NFR-5 stable contracts | S2 §1.2, S3 §2 | all | 0008 | contract tests | all | partial | R-019 → T1.13 |
| NFR-6 reproducibility | S1 §9.5, §11.6 | `Cassette*`; kg `Run` | 0005, 0010 | S3 §3.2 | T1.6, T2.4 | partial | Replay coverage (R-017); fallbacks (R-041 → T1.14) |
| NFR-7 observability | S1 §11.3 | S2 §1.7 | – | – | T1.7 | full | – |
| NFR-8 security | S1 §7, §11.2, §11.7 | query-service, shared.yaml, Compose | 0009, 0013 | S3 §3.1 security level | T0.9, T4.4 | partial | R-029 → T1.14, R-033–R-036, R-045 |
| NFR-9 robustness | S1 §3.2, §3.4d | events, `SkillError` | 0003, 0006 | failure injection; WS 10 | T0.8 | full | Crash windows (R-024) |
| NFR-10 persistence/backup | S1 §11.5 | Compose volumes | – | restore drill | T5.3 | partial | "Restore per milestone" vs. M5 only (R-003) |
| NFR-11 extensibility | S1 §5.3, §5.4 | plugin contracts | 0012 | – | T0.6, T0.7 | full | – |
| NFR-12 local models | S1 §8.4 | gateway config (ollama); `local-llm` | 0004 | T0.4 | T0.4 | partial | vLLM/generic endpoints (R-031 → T1.14); policy not enforced at the gateway (R-035) |
| NFR-13 OSS only | S1 §8.2, §11.3 | – | 0004, 0014 | – | – | partial | NFR text vs. accepted BSL exception (R-009) |
| NFR-14 offline testability | S1 §9.5 | `CassetteEntry` | 0010 | S3 §3.2 | T0.2, T0.3, T1.6 | partial | R-017 |
| NFR-15 single server, sizes | S1 A-6/A-7 | – | 0014 | perf budget | T1.8, T5.4 | full | Memory limits (R-037) |
| NFR-16 ADRs | S1 §12 | adr/README.md | all | – | – | full | Statuses and in-place edits (R-009) |

No requirement is uncovered. 12 FRs and 10 NFRs are only partially covered.

### 1.2 Cross-document consistency (summary)

**Mechanical checks pass:**
- JSON Schemas are valid (2020-12).
- All conformance vectors recompute correctly.
- All Agent Card references resolve.
- All 7 OpenAPI files validate.
- `docker compose config` succeeds with every profile and the dev overlay.
- The milestone order (TLR = M2, inconsistency detection = M3) is consistent everywhere.
- Every event type has a producer and a consumer.
- ADR statuses match between the index and S1 §12.

**Semantic inconsistencies:**
- fingerprint definition differs between S1, ADR-0007, schema and vectors (R-001);
- walking-skeleton expectations vs. the stub spec (R-002);
- calendar vs. sum of task efforts (R-003);
- ingestion status code 201 vs. 202 (R-004);
- actor header "ignored" vs. 403 (R-005);
- `configHash` defined twice, `X-Sandman-Roles` unused, single-writer co-owner (R-006);
- query-service forwarding vs. owner APIs (R-007);
- orchestrator KG access and library API signatures (R-008);
- ADR/NFR texts lagging decisions (R-009);
- example query counting non-current links (R-010).

### 1.3 Constraint compliance

| Constraint | Verdict | Evidence | Loopholes (→ log) |
|---|---|---|---|
| H1 one language per agent; one library per language | compliant for SANDMAN components | ADR-0001; S1 §2.2, §9.1 | Plugins and external tools may use any language and may themselves be LLM agents (R-047) |
| H2 no agent frameworks; direct LLM calls via small library | compliant in intent | ADR-0010; S2 §2.2 framework guard | Maven ban works per module while adapters are packages (R-030); LangChain4j calls bypass the library (R-017) |
| H3 one container each, one Compose | compliant | deploy/docker-compose.yml | Stack gated on gateway health without provider keys (R-040); JVM heaps sized by host RAM (R-037) |
| H4 LLM only via OSS gateway, no provider keys in agents | mostly compliant | provider keys only in `gateway.env` | query-service egress and direct Ollama access (R-035); open admin plane (R-034); `local-only` enforced client-side only (R-035) |
| H5 stubs honor final contracts | partially | S2 §4; T0.5–T0.8 validate against contracts | Planned final operations not in contracts; config schemas have no owner task (R-019) |
| H6 ADRs, index, proposed when dependent | mostly | adr/README.md | ADR-0002 is accepted although T0.4 may change it; no ADR for the ArDoCo integration; in-place edits contradict the README rule (R-009) |
| H7 independent agents, A2A evaluated | compliant | ADR-0002, S1 §3 | – |
| H8 user decisions reflected | mostly | S1 §0.1, §1.4; ADR-0012–0014; S3 rev. 2 | Generic OpenAI-compatible endpoints not configured (R-031); manual/batch trigger not in the WS (R-021) |

### 1.4 Implementability

- **Self-containment:** task specs T0.1–T0.8 are largely self-contained, and all referenced files and sections exist.
- **Hidden dependencies:**
  - T0.5's acceptance tests need the `mini` fixture and the plugin stub from T0.10 (R-013).
  - Every task forbids editing `contracts/`, yet the config schemas are due in M0 (R-019).
  - Schemathesis (V-25) and Testcontainers with Memgraph (V-22) appear in acceptance criteria, but no task verifies them (R-042).
- **Missing spec:** the most security-critical component, the query-service (T0.9), has no spec (R-026).
- **Derivations an implementer would have to guess:** `inputKey`, `actionKey`, `ing`, `tl_`, `evidenceHash`, plugin identity (R-018, R-028).
- **Calendar:** the M0 calendar (6–8 weeks) is below the sum of the minimum task efforts, about 8.4 weeks (R-003).

## 2. Critical assessment (summary)

**Over-engineering**
- Two completion signals per step: NATS events and A2A polling (R-050).
- Library modules and extension points with no consumer in M0–M5 (R-051).
- Per-project read roles that provide no real isolation (R-036).
- A three-database benchmark (R-052).
- Quarkus plus `a2a-java` for three internal agents (R-048).

**Under-specified areas**
- Fingerprints and re-detection scope (R-001, R-011).
- Reverts (R-012).
- Fact supersession after an extractor or approach changes (R-015).
- Concurrency between steps, triage and Memgraph transactions (R-016).
- Incremental TLR scope (R-022).
- ArDoCo doc/model pairing and text rendering (R-025).
- Schema migration (R-029).

**Risky assumptions and single points of failure**
- In-memory Memgraph without memory limits next to two unbounded JVMs (R-037).
- Gateway health gating the keyless walking skeleton (R-040).
- Crash windows in the orchestrator (R-024).

**Security and privacy**
- The shared service-token file is handed to third-party plugins, which enables user impersonation (R-033).
- The gateway management plane is unauthenticated and holds prompt logs (R-034).
- `local-only` is not enforced at the gateway, and there are egress loopholes (R-035).
- NL queries can read other projects (R-036).
- Console session/CSRF handling is unspecified (R-045).
- Upload and XML hardening is incomplete (R-029).
- Rendering of LLM and plugin text in the console (R-032).
- Supply-chain pinning claims do not match the deploy files (R-046).

**Research validity**
- Record/replay misses LangChain4j calls and embeddings (R-017).
- The evaluation statistics cannot reach significance, and the M2 exit criterion is ill-posed (R-038).
- Gateway fallbacks confound which model was used (R-041).
- Benchmark contamination and NL-eval leakage (R-043).
- Triage labels are biased as evaluation data (R-044).

## 3. Review log

The columns "My decision / comment" and "Status" (open / accepted / rejected / deferred) are for you. Entries are grouped by category and sorted by impact within each group.

### 3.1 Inconsistency

| ID | Affected artifacts | Description | Proposed resolution / default | Impact | Effort | Src | My decision / comment | Status |
|---|---|---|---|---|---|---|---|---|
| R-001 | c/conformance/vectors.json `fingerprint` (detector `ardoco-id@2.0.1`, key `art_y#Sentence:12`); c/skills.schema.json `Finding` (`traceLinkId`); c/kg/schema.yaml `Inconsistency.fingerprint`; S1 §4.2; ADR-0007 | Fingerprints are not stable. (1) They include the detector version, so every upgrade (e.g., T3.5) resets all triage decisions. (2) They include version-specific `tl_` link ids. (3) Sentence keys are index-based, so inserting one sentence re-fingerprints all later findings: the old ones become false `RESOLVED (confirmed)` and new `OPEN` items appear. This contradicts S1's "keys, not ids". | Fingerprint = sha256(type, detector **id without version**, sorted element keys). Represent links as `sourceKey→targetKey`. TextUnit `localKey` = section path + normalized-sentence hash + occurrence counter, with `sentenceIndex` as a property. Fix the vectors before T0.2/T0.3 start. Add the scenario test "inserting a sentence changes no status". | high | S | A-02, B-06, B-07, B-15, C-07 | D-5: use recommendation. | accepted · applied |
| R-002 | S2 §4.2, §4.4, §5 step 5; S1 §5.2 stub | The stub creates a file-level `Requirement` for `requirements.md`, and `stub-untraced` flags it. The WS then gets 3 findings instead of 2. The file-level `localKey`, and whether file-level elements exist for plugin-routed artifacts, are unspecified. | The file-level element is never labeled `Requirement`: use `TextUnit`/`CodeUnit`/`TestCase`, `kind=file`, `localKey="<file>"`. Write it for every artifact, and plugins reference it as parent. | medium | S | D-8: use recommendation. Applied in code: the file-level element of `requirements` is a `TextUnit` (`services/extraction-agent/src/sandman_extraction/stub.py`); Stage 2 §4.2 prose aligned. | accepted · applied |
| R-003 | S3 §1.2/§1.3 calendar; S1 §11.5 ("restore per milestone") vs. T5.3; S1 §4.2 ("retention not in prototype") vs. T5.2; S2 §5 ("< 5 min") vs. S3 A3-4 ("< 15 min") | The M0 calendar (6–8 weeks) is below the minimum sum of task efforts (≈ 8.4 weeks; range 8.4–16). Promises about restore drills, retention and e2e time budgets are inconsistent. | Re-baseline M0 at 10–16 weeks, or cut scope (R-050, R-051, R-036 option B) and re-estimate. Align the restore, retention and e2e statements. | medium | S | C-15, A-21, B-31 | D-10: confirmed (M0 = 13 weeks, range 9–17); restore/retention/e2e statements aligned. | accepted · applied |
| R-004 | S1 §3.2, §3.4a; ADR-0003 vs. extraction-agent.yaml, query-service.yaml, T0.5 | Ingestion response is 202 in prose and ADR, 201 in contracts and task. | Keep 201 (completion is synchronous); fix the prose and ADR-0003. | low | S | D-8: use recommendation. Contracts already say 201; Stage 1 §3.2/§3.4a and an ADR-0003 amendment fixed. | accepted · applied |
| R-005 | S1 §3.3, shared.yaml `Actor` ("ignored") vs. S2 §1.2, T0.7 (403) | An actor header from a non-forwarder is ignored in one place and rejected with 403 in another. | Reject with 403 everywhere; update S1 and shared.yaml; add the criterion to T0.5. | low | S | D-8: use recommendation. Stage 1 and Stage 2 prose say 403 (code: `auth.py`). `shared.yaml` text, per-service tests: T1.13. | accepted · applied |
| R-006 | T0.6 (`configHash` = JCS of approach params) vs. S2 §1.6 (file hash in `Run.configHash`); shared.yaml `Roles`; S1 single-writer rule vs. schema.yaml TraceLink co-owner | `configHash` is defined twice; a file hash inside link ids would duplicate all links whenever any approach is enabled. `X-Sandman-Roles` is defined but unused. The single-writer rule contradicts the explicit-reference co-owner. | Use one `configHash` definition (per-approach params). Remove `X-Sandman-Roles` or use it in owners. Document the co-owner exception and exclude `explicit-reference` links from tlr-agent cleanup and prune. | low | S | D-8: use recommendation. Docs applied (Stage 1 co-owner exception, Stage 2 `configHash` definition). `X-Sandman-Roles` removal in `shared.yaml` and the tlr-agent cleanup/prune exclusion: T1.13. | accepted · applied |
| R-007 | c/openapi/query-service.yaml vs. orchestrator.yaml, extraction-agent.yaml, S1 §7/§11.4 | Forwarding gaps: workflow cancel, dead events, `GET ingestions/{id}`, `GET /v1/projects/{p}` and the catalogs are not forwarded. `/v1/schema` requires `authenticated` instead of viewer. `limit ≤ 1000` is hard although services may need more. The NL response requires rows even for `cypher-only`. | Align, or mark explicitly as out of M0 scope. | low | S | D-8: use recommendation. Owner: T1.13 (align or mark out of M0 scope). | accepted · applied |
| R-008 | T0.8 (orchestrator reads the KG) vs. S1 §2.2/§5.1; T0.2 `serve(agent_card_path…)` vs. S1 §9.2 `serve(agent: AgentInfo…)`; `stable_id(*parts)` vs. `(prefix, *parts)`; modules `plugins`/`auth` missing in S1 §9.1 | Architecture and library API descriptions drift from the task specs. How a gateway budget error maps to `SkillError` is undefined. | Task specs take precedence; update S1 §2.2/§5.1/§9 and ADR-0010. Add LLM error mapping (budget → `BUDGET_EXCEEDED`) to T0.2/T0.3 after T0.4. | low | S | D-8: use recommendation. Applied: Stage 1 §5.1/§9 aligned with T0.2/T0.3, ADR-0010 amendment; `BUDGET_EXCEEDED` mapping exists in `sandman_agent.a2a` (Java has no LLM client). | accepted · applied |
| R-009 | ADR-0002 (accepted) vs. S3 §1.3 ("T0.4 may change ADR-0002"); NFR-13 vs. ADR-0014; adr/README ("never rewrite") vs. in-place revisions of ADR-0002/0012 | ADR statuses and texts lag behind decisions; the README process rule is violated; there is no ADR for the ArDoCo integration (pinned build, sentence mapping, Q-20). | Mark ADR-0002's Java-runtime part `proposed` until T0.4. Reword NFR-13 with the Memgraph exception. Allow dated amendments before review in the README. Add ADR-0015 "ArDoCo integration" as `proposed`. | low | S | A-18, B-32, C-29 | (partly applied with the decisions) README amendment rule, ADR-0002 runtime provisional (D-6), ADR-0015 written. NFR-13 wording still open. | partially applied |
| R-010 | S1 §4.4 "untraced requirements" example; S2 §4.4 `stub-untraced` | The example counts links whose other endpoint is no longer current. | Require the other endpoint to be current, in the example and in the `stub-untraced` spec. | low | S | B-30 | (coupled to D-5/R-015, applied by the architect) "current link" now requires active + both endpoints current; example and stub spec updated. | accepted · applied |

### 3.2 Gap

| ID | Affected artifacts | Description | Proposed resolution / default | Impact | Effort | Src | My decision / comment | Status |
|---|---|---|---|---|---|---|---|---|
| R-011 | S1 §4.3 step 3, §6 rules; skills.schema `scopeVersionIds`, `detectors`; S2 §5 step 8; T0.7 | **Re-detection scope and auto-resolve guard.** (1) Scope is a set of version ids, but INVOLVES edges point to earlier-version elements, so nothing is ever in scope; auto-resolve never fires and WS step 8 fails. (2) Deletions in mixed ingestions are never re-checked, and the deletion-only rule cannot resolve anything because deleted artifacts have no current version. (3) Changes to kinds without a TLR pair trigger no detection. (4) A run with a detector subset, a disabled detector, or a plugin without declared types falsely resolves other detectors' items. (5) "Non-terminal" is undefined, and system transitions are unguarded. | Define scope by **artifact**: an inconsistency is in scope iff all involved keys' artifacts are in S, plus deleted artifacts of the triggering ingestion and project-wide runs. Auto-resolve only items whose detector completed in this run. Findings that involve only deleted artifacts become `RESOLVED` with reason `artifact-deleted`. Rule: `extraction.completed` without a TLR pair triggers detection. Enumerate the non-terminal states. Guard system writes with `WHERE i.status = $seen`. Add T0.7 tests. | high | S | A-03, B-08, B-09, C-08 | D-5: use recommendation. | accepted · applied |
| R-012 | S1 §4.2/§4.4 (`CREATE (v:ArtifactVersion…)`); schema.yaml `ver_`; T0.5 (`ing` = hash(manifest), "identical re-ingestion creates nothing") | **Reverts (A→B→A) and re-added files.** An existing `ver_` id causes a uniqueness violation or a `PREVIOUS` cycle. `deletedAt` is never cleared. `createdVersionIds` stays empty, so no TLR or detection runs. A re-sent manifest is treated as a duplicate, so the KG diverges from the repository. | Re-activate the existing version: move `CURRENT_VERSION` and clear `deletedAt`. Report it in an additive `reactivatedVersionIds`, handled like created versions. Check idempotency against the current state, and include a sequence in `ing`. Order versions by ingestion sequence instead of `PREVIOUS`. Add a revert step to the WS. | high | S | A-05, B-04, C-11 | D-5: use recommendation. | accepted · applied |
| R-013 | T0.5 scope 4; S2 §1.6, §4.2, §4.6, §5 | **Explicit-reference matcher.** Its scope is undefined: scanning `requirements.md` links REQ-1..3 to themselves and breaks WS step 5. Matcher and plugin may both create the link. The direction contradicts the WS. The resolution rule and `linkType` are unclear. T0.5 depends on the T0.10 fixture and stub. | Run the built-in matcher only on non-plugin artifacts and exclude definition sites. Link source = requirement, target = referencing element. Resolve by `Requirement.localKey` among current versions. T0.5 ships its own minimal fixture. | high | S | B-01 | T1.3: explicit references target `Requirement` elements only. | accepted · applied |
| R-014 | S1 §4.2, §6; T0.5; schema.yaml `current` | Explicit links stop being current when the **referenced** artifact gets a new version. Nothing re-resolves them, because only new versions are extracted. | Store unresolved reference texts on referencing elements, and re-resolve them after every ingestion in a cheap extraction-agent step. | high | M | B-03 | D-8: use recommendation (accepted 2026-10-09). Owner: T1.12. | accepted · applied |
| R-015 | S1 §4.4 (`MERGE … ON CREATE SET`), §4.2; schema.yaml `current`; S3 §0/§2 ("replacement is a configuration switch") | **No fact supersession.** Stub elements stay current after switching extractors. Links from old approaches or configs stay current forever and feed detection. Facts of failed or abandoned runs stay visible, because no reader filters on `Run.status`. | Define "active facts" in the schema conventions. (1) Extraction replaces that version's elements per tool in one transaction. (2) Per-project `activeApproaches` (approach, version, configHash) decide which links detection, the console and exports use. (3) Readers use only facts of `completed` runs. | high | M | B-14, C-10 | D-5: use recommendation. Simplified: approach enablement is deployment-wide (tlr-agent config), not per project; `TraceLink.active` instead of an `activeApproaches` list. | accepted · applied |
| R-016 | S1 §6 (exclusivity per agent/skill), §3.4, §4.3; S2 §1.6 `maxConcurrentPerAgent: 2`; T0.7, T0.8 | **Concurrency is unspecified.** Detection can run mid-TLR and produce spurious findings. A 30-minute detection run can overwrite a user's triage. Memgraph aborts conflicting writers on shared vertices. Transaction sizes (10⁴–10⁵ links) are unquantified. | Add a per-project pipeline lock: extract → tlr → detect run strictly in sequence, and triggers coalesce behind it. Make system transitions conditional on the observed status. Use a fixed batch size (e.g., 5 000 facts) together with the R-015 reader rule. Treat Memgraph conflicts as retryable per batch, and verify the conflict behavior in T0.4. | high | M | C-09 | D-5: use recommendation. | accepted · applied |
| R-017 | S1 §8.4 (LangChain4j gets base URL + key), §9.5; ADR-0010; common.schema `CassetteEntry` (chat only); S3 M2 exit, T1.6 | **Record/replay coverage.** LangChain4j calls from LiSSA, ArDoCo and Artemis are not recorded, not policy-checked and not in provenance. Embeddings have no cassette format. Keys use logical routes, so re-pointing a route replays old answers. The T1.6 KG-hash determinism test cannot pass because run ids and timestamps are random. | Build one OpenAI-compatible **record/replay server** (chat + embeddings, keyed by canonical body including the provider model). All languages and LangChain4j use it in record/replay mode, replacing both library recorders. Optionally add LangChain4j `ChatModel` adapters that delegate to `LlmClient`. Require `ORDER BY` in prompt-building reads. Compute determinism over a canonical export without ids and timestamps. | high | M | A-01, C-05 | D-3: use recommendation. | accepted · applied |
| R-018 | S1 §4.3; ADR-0007; inconsistency-agent.yaml `history[].from`; schema.yaml `evidenceHash`; T0.7 | **Lifecycle table and evidence hash are incomplete.** Users cannot reopen `RESOLVED` (FR-7). The creation entry has no valid `from`. Pending→confirmed handling is unspecified. ACCEPTED/REJECTED behavior when not re-detected is unclear. `evidenceHash` (order, separators, links, missing `contentHash`) is undefined, so `ACCEPTED→REOPENED` may never trigger. | Publish one normative transition table (from, to, actor, condition, StatusChange, event) in ADR-0007, or in `contracts/` as JSON. Add the user transition `RESOLVED → OPEN`. Make `from` optional for creation. Leave ACCEPTED/REJECTED untouched when not re-detected. Pending→confirmed writes a StatusChange and counts as auto-resolved. Define `evidenceHash` (sorted `key=contentHash` pairs, U+001F-joined), make `Element.contentHash` required, and add a vector. | medium | S | D-8: use recommendation. Owner: T1.13 (contract PR, ADR-0007 amendment, inconsistency-agent). | accepted · applied |
| R-019 | NFR-5; S1 §5.1 (`/v1/requests` 501), §6 (link review, approval), §4.2 (prune skills); S2 §1.6 ("config schemas added in M0"); S3 §5 ("never change contracts in a task") | **Contract gaps.** Planned final operations (planner, approval, link review, prune) are absent from the contracts, so "replace without contract change" becomes an additive change. The config schemas have no owner task while all tasks forbid contract edits. No task owns the `models.yaml`/`intents.yaml` defaults. The examples location differs between S1 and S2. | Either add the planned operations now (`x-sandman-status: planned`, returning 501), or reword NFR-5 as "no breaking change". Add task T0.1b "config schemas + default config files" before T0.5–T0.8. Pick one examples path. | medium | S | D-8: use recommendation. Choice: add the planned operations now (`x-sandman-status: planned`, 501), because the change is additive and conflicts with no contract. Config schemas (former T0.1b) in `contracts/config/`. Examples path: `config/query/examples.yaml`. Owner: T1.13. | accepted · applied |
| R-020 | extraction-agent.yaml `GET …/ingestions/{id}`, `Ingestion`; schema.yaml (no Ingestion node) | The ingestion record has no storage, and the response to an idempotent re-send is unspecified. | Add an `Ingestion` node to the KG schema (owner: extraction-agent) and return the stored original on re-sends. | medium | S | B-05 | (coupled to D-5/R-012, applied by the architect) `Ingestion` node with sequence; re-sends recorded with empty lists, no event. | accepted · applied |
| R-021 | S2 §5 steps 2, 9, 10; S1 §0.1 ("both modes in WS"); compose `plugin-stubs` | **The walking skeleton cannot run as written.** There is no manual/batch trigger step. There is no source for kind rules, so all files become `other`. Plugin warnings and notification lines are not observable via the API. `plugin-stubs` has no healthcheck, which causes a race. `docker compose kill` may not trigger a restart. | Add a manual `POST /workflows {type: tlr, force}` step. Have the CLI read kind rules from a project-level `sandman.yaml`. Add `warnings[]` to `detectorRuns` and enable the log file in e2e. Add a healthcheck for `plugin-stubs` and wait for it. Kill PID 1 or `start` the service explicitly. | medium | S | D-8: use recommendation. Partly done: kind rules come from a project-level `sandman.yaml` in the fixtures. Open: manual `tlr` trigger step, `warnings[]` in `detectorRuns`, `plugin-stubs` healthcheck, restart check. Owner: T1.13. | accepted · applied |
| R-022 | skills.schema `RecoverTraceLinksInput`; S2 §1.6 `kindPairs`; S1 §6; T0.6, T0.8 | **Incremental TLR scope.** The flat source×target input cannot express several kind pairs; union coalescing creates doc→code and req→model links. Whole-artifact approaches (ArDoCo SAD↔SAM, TransArC) get incomplete inputs. LiSSA rankings shift when targets are a subset. | Approaches declare `scopeMode: pairwise \| whole-project`. Whole-project approaches recompute over all current versions of the pair and replace their active links. Pairwise approaches get (changed_src × all_tgt) ∪ (all_src × changed_tgt). Use one step per kind pair, or an additive `kindPairs` input. Test that incremental and batch runs give equal results for deterministic approaches. | medium | M | D-8: use recommendation. Owner: T2.1 (first approach that needs `scopeMode`; contract PR first). | accepted · applied (T2.1, 2026-10-10: contracts 0.6.0 `scopeMode`, whole-project run semantics in the tlr-agent, incremental-equals-batch test) |
| R-023 | S1 §6; T0.8; orchestrator.yaml | Workflow semantics under debounce and coalescing are undefined: a workflow can show SUCCEEDED while a debounce timer is pending, and ownership of merged steps is unclear. A `BLOCKED` workflow cannot be resumed. | A workflow stays RUNNING while timers or pending triggers exist. A merged step belongs to the newest workflow; the others record `mergedInto`. Add `POST /v1/workflows/{id}/retry`. | medium | S | D-8: use recommendation. Owner: T1.13. | accepted · applied |
| R-024 | T0.8 items 2, 5; S1 §3.2, §5.1 | The orchestrator acks right after the inbox insert, so handler failures are never redelivered. Unprocessed inbox rows and in-memory debounce windows are lost on a crash. | Write the inbox row and the resulting PENDING steps in one SQLite transaction, with debounce as a PENDING step with `not_before`; ack afterwards. Scan for unprocessed rows at startup. | medium | S | D-8: use recommendation. Owner: T1.13 (code already commits inbox row and steps together; verify, add crash test). | accepted · applied |
| R-025 | S1 §5.4; S2 §1.6 `textKinds/modelKinds`; S3 T3.1, Q-20, V-27 | The ArDoCo integration is under-specified. Which doc/model pairs run is not defined. It is unclear which text rendering ArDoCo sees (Markdown vs. Docling output vs. TextUnits, duplicate sentences). If ArDoCo recomputes links internally, the evidence shown in the console no longer matches. | Configure per-project pairs `{docGlob, modelGlob, codeRoot?}`. Store one canonical plain-text rendering per doc version, consumed by extraction, ArDoCo TLR and ArDoCo ID. Document this in ADR-0015. | medium | M | C-17 | Q-20 answered (links from all approaches, ADR-0015). Doc/model pairing and canonical text rendering still open. | partially applied |
| R-026 | S3 T0.9 "(spec after T0.2)"; S2 §1.5, §4.5; S1 §7, §11.2 | The query-service (policy decision point, console, Cypher guard) has no task spec; its acceptance criteria are spread over three documents. The notification log format has no schema. | Write the T0.9 spec next, covering: authorization-matrix tests; forwarder spoofing tests; Cypher-guard tests with validation off; resource→project resolution for `/v1/inconsistencies/{id}`; `lastVisit`; session security (R-045); a notification schema. | medium | M | A-19 | Spec written 2026-10-07: `docs/tasks/T0.9-query-service.md` (incl. R-045 session rules; notification log format stays as in Stage 1 §10). | accepted · applied |
| R-027 | S1 §11.6 ("formats in Stage 3"); S3 T2.3, V-24; query-service export | The benchmark export formats (FR-17) are undefined. Gold standards use sentence numbers, model ids and paths, not element keys. | Add `format=ardoco-sad-sam \| ardoco-sad-code \| lissa` as an additive enum, with a column mapping once V-24 is answered. Fix the S1 reference. | medium | S | D-8: use recommendation. Owner: T2.3 (export formats with V-24). | accepted |
| R-028 | c/kg/schema.yaml `Run.inputKey`; T0.5 `ing`; T0.8 `actionKey`; `tl_` for explicit references; plugin identity in `Run.tool` | The derivations needed for idempotency are not pinned down and have no vectors: canonical manifest, `inputKey`, `actionKey`, `tl_` for explicit links, plugin id. | Define each as a JCS object with named members and add vectors for `inputKey`, `actionKey`, `ing` and `tl_`. Plugin identity = config id + version from the card or manifest. | medium | S | D-8: use recommendation. Owner: T1.13 (contract PR with vectors). | accepted · applied |
| R-029 | T0.5 scope 3; extraction-agent.yaml `Manifest.path`; S1 §5.2, §10; schema.yaml version; init.cypher | **Hardening and migration gaps.** Upload hardening misses zip bombs, entry caps, hardlinks/devices and backslash/drive paths. XMI parsing is open to XXE and billion-laughs, and Docling runs without limits. The console cannot produce the per-file sha256 manifest. KG and SQLite schema migration has no mechanism and no owner. | Use `tarfile` with `filter='data'`, size and entry caps, `defusedxml` (DTDs off in Java), and Docling in a subprocess with limits. Make the manifest optional for archive-only uploads. An archive-only upload is a partial ingestion: it retires no artifact. Add a `(:SchemaVersion)` node with a one-shot `kg-migrate` service, and `PRAGMA user_version` for SQLite. | medium | S | D-8: use recommendation. Parts exist (`defusedxml`, entry caps, symlink check, converter service, SQLite `user_version`). Remainder: T1.14. | accepted · applied |
| R-030 | T0.1 enforcer; S2 §2.1 ("approaches as packages"); T0.6 | The LangChain4j ban cannot be scoped to packages. Once ArDoCo/LiSSA are module dependencies, agent code can import LangChain4j, and the H2 guard disappears in M2/M3. | Put the adapters in separate Maven modules (`approach-lissa`, `detector-ardoco`) with an enforcer exception only there, plus an ArchUnit rule forbidding `dev.langchain4j..` elsewhere. | medium | S | D-8: use recommendation. Owner: T2.2 (`approach-lissa`) and T3.1 (`detector-ardoco`). | accepted |
| R-031 | S1 NFR-12, §8.4 ("any OpenAI-compatible endpoint"); gateway/config.json | Generic OpenAI-compatible endpoints (vLLM, institutional servers) are claimed but not configured. | Add a V-item and a T0.4 check for a Bifrost custom provider with base URL, plus a commented example in config.json. | low | S | D-8: use recommendation. Owner: T1.14. | accepted · applied |
| R-032 | S1 R-4, §7 step 7, §11.7; skills.schema `Finding.evidence` | Prompt-injection mitigations cover actions, but not result integrity or how LLM rationale and plugin evidence are rendered in the console. | Escape all rendered text (no `\|safe`, no markdown→HTML) and set a CSP. Answers must cite ids present in the rows. Injection fixtures measure effects on outputs. | low | S | D-8: use recommendation. Treated as security-relevant despite "low". Console already autoescapes and sets a CSP (`services/query-service`). Owner: T4.3 (cited ids), T4.4 (injection fixtures, escaping and CSP checks). | accepted |

### 3.3 Risk

| ID | Affected artifacts | Description | Proposed resolution / default | Impact | Effort | Src | My decision / comment | Status |
|---|---|---|---|---|---|---|---|---|
| R-033 | deploy/gen-secrets.sh (one `service_tokens.json`); compose (mounted everywhere incl. plugin-stubs and plugin template); S2 §1.6 `tokenName: extraction-agent`; common.schema `sandman.actor` | **Shared token file.** Every service and every third-party plugin holds the query-service token. Any of them can impersonate any user (forging triage actors, and with them evaluation labels), ingest, trigger workflows and run raw Cypher. Owners also send their own token to plugins, and `sandman.actor` has no forwarder check. | Give each service a secret holding only its own token, plus a verifier file of caller hashes. Give plugins separate tokens (`plugin:<id>`) usable only for read calls scoped to the current project. Apply the forwarder rule to `sandman.actor` too. | high | S | A-10, C-01 | D-4: use recommendation. | accepted · applied |
| R-034 | compose `llm-gateway` (UI and inference on port 8080, published 127.0.0.1:8081); gateway/config.json (no admin auth, logging on); S1 §2.3 `gateway_admin_key` (missing) | **Gateway management plane.** It is reachable from every container and by local host users. It can change providers, keys and budgets, and it exposes `logs.db` with full prompts, including those of `local-only` projects. T0.4 does not test it. | Add a T0.4 check: call the management API without credentials from an agent container. Enable Bifrost admin auth and add the secret. If that is impossible, ADR-0004's confirmation fails → switch to LiteLLM (its master key protects admin routes). Define log retention. | high | S | A-09, B-22, C-02 | D-4: use recommendation. Bifrost `governance.auth_config` configured; enforcement on the management API checked in T0.4 (V-33). | accepted · applied |
| R-035 | gateway/config.json (all VKs allow openai/openrouter/ollama `*`); compose `edge` (egress), `ollama` on `internal`; S1 §8.4 fallbacks, §11.7 | **Confidentiality and gateway-only access are not enforced.** `local-only` is checked only client-side, and LangChain4j bypasses it. Fallbacks may route to OpenRouter. query-service and otel-lgtm have direct egress. Ollama is reachable without key, budget or log. Logs, cassettes and backups have no retention rule. | Per-component `*-local` virtual keys restricted to Ollama, so the gateway enforces the policy. No cross-provider fallbacks. Ollama on a dedicated network shared only with the gateway. `edge` without masquerade (`enable_ip_masquerade=false`, to verify) or behind a reverse proxy. Retention rules. OpenRouter with `data_collection: deny` and pinned providers, or excluded for proprietary projects. T0.4 egress checks. | high | S | A-07, A-08, C-03 | D-4: use recommendation. | accepted · applied |
| R-036 | S1 §7 (tenant guard: `$projectId` must appear), denylist; ADR-0009, ADR-0013; query-service `/v1/query/nl` (viewer) | **Cross-project reads.** `WITH $projectId AS p MATCH (r:Requirement) RETURN r` passes the guard, so any viewer can read all projects. The denylist misses Memgraph clauses (`SHOW …`, `DUMP DATABASE`, `TERMINATE TRANSACTIONS`, streams, `SET DATABASE SETTING`). Cartesian products can exhaust memory. | Option B (recommended): one deployment = one read-trust domain; per-project roles only for write actions; isolation = separate deployment. Option A: openCypher allowlist parser, start-node `projectId` constraint checked on the parse tree, plus a result post-check. Both: memory limit and short timeout for user queries. | high | S (B) / M (A) | C-04 | D-2: projects isolated, only per-project reads (option A); in the future separate deployments for confidential projects. | accepted · applied |
| R-037 | compose (`JAVA_TOOL_OPTIONS: -XX:MaxRAMPercentage=75`, no `mem_limit`, Memgraph without `--memory-limit`) | Two JVMs may each claim 75 % of host RAM next to in-memory Memgraph. The OOM killer may hit Memgraph and take all projects down. An ArDoCo OOM also takes the triage API down. | Set `mem_limit` per service (e.g., Memgraph 12 GB with `--memory-limit=10240`, JVMs 6 GB, Python 1 GB). Add a memory budget table to S2 §3.2 and monitor `SHOW STORAGE INFO`. | high | S | A-16, C-12 | D-4: use recommendation. | accepted · applied |
| R-038 | S3 M2 exit ("±0.02 F1 (replay)"), §3.3 (Wilcoxon across projects); S2 §4.3 | **Evaluation statistics.** "Reproduce published results in replay" is ill-posed: replay only reproduces our own recordings, and the published LLM runs used unrecorded models. ArDoCo is deterministic, so a tolerance hides mapping bugs. Wilcoxon with n = 5 projects can never reach p < 0.05 (minimum p = 0.0625). There is no variance pilot and no multiple-comparison correction. | ArDoCo: exact link-set equality with the standalone runner. LiSSA: a live re-run with the closest model, reporting Δ with a CI, then recorded. Use paired bootstrap / McNemar per project, effect sizes and Holm correction; Wilcoxon only with ≥ 6 projects. Derive n runs from a pilot. Pre-register the protocol in `experiments/`. | high | S | A-20, C-06 | D-8: adopt as proposed. | accepted · applied |
| R-039 | S1 §3.2 `actionKey`; S1 §6; T0.8 | Idempotency suppresses legitimate re-runs, e.g., a second deletion-only detection or re-linking after a config change. | Include the triggering event or ingestion id in `actionKey` for event-driven steps, and deduplicate only retries within a workflow. Document that config changes need `force`. | medium | S | A-06 | (coupled to D-5/R-012, applied by the architect) `actionKey` includes the trigger id; otherwise reverts would not re-run extraction. | accepted · applied |
| R-040 | compose `x-depends-core`; gen-secrets.sh (empty provider keys); S2 §5 ("no provider keys") | The keyless walking skeleton depends on Bifrost starting healthy with empty `env.*` provider keys. Otherwise nothing starts in CI. | Add a T0.4 check. Fallback: `condition: service_started` for the gateway, and drop the gateway from agent readiness. | medium | S | A-17, B-23 | No decision; V-34 added to T0.4 as a check only. D-8: use recommendation. Resolved by implemented work: `docs/notes/spike-T0.4.md` check 17 PASS (Bifrost healthy with empty provider keys, V-34); the CI `e2e` job runs the keyless walking skeleton (`.github/workflows/ci.yml`, `tests/e2e/test_walking_skeleton.py`). No fallback needed. | closed |
| R-041 | S1 §8.4 (fallbacks), §11.6; schema.yaml `Run.resolvedModel` | Gateway fallbacks can switch models mid-run, and a single `resolvedModel` hides mixed-model runs. OpenRouter "equivalents" differ in provider and quantization. Experiments are confounded. | No fallbacks for the `experiments` key and routes. Record the resolved model per call; make `Run.resolvedModel` a list or add a `mixed` flag. For OpenRouter, pin the provider and set `allow_fallbacks: false`. | medium | S | D-8: use recommendation. Owner: T1.14 (contract PR: additive `Run.mixedModels`). | accepted · applied |
| R-042 | T0.5, T0.7, T0.8 (Schemathesis); T0.2/T0.3 (Testcontainers with Memgraph); S3 V-22, V-25 | Acceptance criteria rely on unverified tooling: OpenAPI 3.1 with external `$ref`/`$id`, and the Neo4j Testcontainers module with the Memgraph image. | Add V-22 and V-25 to T0.4, or as a CI smoke check in T0.1. | medium | S | D-8: use recommendation. Resolved: Testcontainers with Memgraph is used in `libs/python/sandman-agent/tests/test_integration.py`, `libs/java/agent-lib/.../IntegrationTest.java`, `scripts/test_kg_canonical_export.py`; OpenAPI 3.1 with external `$ref` is validated in CI by `scripts/validate_contracts.py` (`base_uri`) and by the service tests (`openapi_validate`). Schemathesis is not used; V-25 stays in Stage 3 §7 only if it is adopted. | closed |
| R-043 | S3 §3.3, RK-15, T4.2; S1 §7 step 2 | Contamination is acknowledged but not addressed. The NL-to-Cypher few-shot pool and eval set come from the same file, so near-duplicates cross the split and the same people tune the prompts. Result-set equality under LIMIT is undefined. | Run a perturbed-benchmark probe (consistent renaming + paraphrase) and include ≥ 1 non-public or post-cutoff project. Split the NL eval by template and project. Use independent question authors. Compare multisets without truncation. Report the intent-template baseline. | medium | M | C-19 | D-8: adopt as proposed. | accepted · applied |
| R-044 | S3 §3.3 ("field precision = 1 − REJECTED/triaged"); S1 §4.3 | Triage labels are biased: users pick which items to triage, raters see detector and confidence, and there is a single rater. Recall is not measurable. System `RESOLVED` can also come from fingerprint churn. | Run a separate labeling study: stratified random sample, two blinded raters with guidelines, Cohen's κ. Use triage only as a secondary signal. Record the resolution cause (user / not-redetected / artifact-deleted). | medium | M | C-20 | D-8: adopt as proposed. | accepted · applied |
| R-045 | S1 §11.2; ADR-0013; query-service.yaml (session on all routes) | Console session security is unspecified: CSRF (a multipart POST is a simple request), cookie flags, revocation of stateless cookies, login throttling, bootstrap password change. | SameSite=Strict + HttpOnly cookies (+ Secure behind TLS); server-side session table; CSRF tokens on forms; `/v1/*` bearer-only; rate limiting; forced bootstrap password change; CSP. Put this into the T0.9 spec. | medium | S | C-22 | D-4: use recommendation. Requirements recorded in S1 §11.2 and ADR-0013; T0.9 spec still to be written (R-026). | accepted · applied |
| R-046 | S1 §11.7, §11.6; compose/Dockerfiles (tags, floating bases); S2 §3.1 (LiSSA and model downloads unpinned) | The supply-chain claim (image digests) does not match the deploy files. The LiSSA commit, Docling/HF model revisions, apt packages and plugin images are unpinned. | Use `image@sha256` and `FROM …@sha256`, with Renovate bumping them. Add `ARG LISSA_COMMIT`. Pin model revisions. Pin plugin images by digest. | low | S | D-8: use recommendation. Owner: T1.14. | accepted · applied |

### 3.4 Question

| ID | Affected artifacts | Description | Proposed resolution / default | Impact | Effort | Src | My decision / comment | Status |
|---|---|---|---|---|---|---|---|---|
| R-047 | ADR-0001, ADR-0012; S1 §2.2, §5.3 (`external-http`), §8.4 (plugin keys); gateway/config.json, gen-secrets.sh | Do plugins and external tools fall under H1/H2/H4 (one language, no frameworks, gateway-only)? Plugins may be LLM agents, and the promised plugin virtual keys are not provisioned. | Plugins built inside SANDMAN: Python/Java on the shared library. Third-party plugins: treated as external systems, but if they use LLMs they must use a SANDMAN gateway key and run on `internal`. Provision plugin VKs. State the rule in ADR-0012 and `docs/plugins.md`. | medium | S | A-12 | D-9: use recommendation. | accepted · applied |
| R-048 | ADR-0002, ADR-0001; S3 RK-1, RK-2; T0.3, T0.4 | Is A2A with Quarkus/`a2a-java` worth it for three internal agents? SANDMAN's conventions are custom anyway; Quarkus/CDI may conflict with ArDoCo/CoreNLP/LangChain4j; the SDKs churn. | Default: keep `a2a-sdk` (Python) and decide the Java runtime by T0.4 criteria. Fallback: hand-written JSON-RPC (`SendMessage`, `GetTask`, `CancelTask` + static card) without Quarkus. | medium | M | C-25 | D-6: use recommendation (decide in T0.4, default a2a-java). | accepted · applied |
| R-049 | S1 §2.2 | Should tlr-agent and inconsistency-agent merge into one Java service? That gives one JVM and one ArDoCo/CoreNLP instance, and resolves V-27. | Default: keep them separate, as the assignment lists separate agents. Share the canonical text rendering (R-025). | medium | M | D-8: use recommendation. Keep the services separate; nothing to change (shared text rendering: R-025). | accepted · applied |

### 3.5 Suggestion

| ID | Affected artifacts | Description | Proposed resolution / default | Impact | Effort | Src | My decision / comment | Status |
|---|---|---|---|---|---|---|---|---|
| R-050 | ADR-0003; S1 §3.2, §3.4a, §6; events.schema.json; T0.2 `events`; T0.8 | **Double completion signal.** The orchestrator polls A2A results and also chains steps from events that carry the same data. The two signals can disagree (an event is published, then output validation fails). NATS costs a container, two client wrappers, the envelope, the inbox, dead events, the commit-publish-complete protocol and failure tests. | (a) Drop NATS: chain from A2A results, have the extraction-agent call the orchestrator on ingestion, and reconcile at startup (saves ≈ L). (b) Keep NATS as the **only** chaining signal, and poll only for timeouts and failures. | high | M | C-13 | D-1: use recommendation (b). | accepted · applied |
| R-051 | S1 §9.1, §5.3, §5.4; T0.2, T0.3; vectors `toolDispatch`; ADR-0012 | Library modules and extension points have no consumer in M0–M5: the tool loop in both languages; the Java `LlmClient`/replay/`ModelSelector` (Java LLM use happens inside LangChain4j); `llm-rag` (duplicates `lissa`); `external-http`; `cypher-rule`; `sandman.plugin.recover-links`; both plugin transports in both languages. | Cut until needed: tools/loop until M6; Java LLM client/replay (replaced by the replay server, R-017); `llm-rag`, `external-http`, `cypher-rule`. Plugin clients: Python A2A extract and Java HTTP detect only. About 30–40 % of T0.3 disappears. | medium | S | C-14 | D-7: use recommendation. | accepted · applied |
| R-052 | ADR-0014; S3 T1.8, RK-17 | The three-database benchmark plus a nightly ArcadeDB job costs an M, while at ≤ 200k nodes latency is dominated by LLM calls and CoreNLP. | Keep Memgraph. Drop the T1.8 three-way benchmark and the nightly ArcadeDB job. Check Memgraph against the T5.4 performance budget and revisit only if it fails. | low | S | C-26 | D-7: use recommendation. | accepted · applied |

## 4. Decisions needed from you (prioritized)

| # | Decision | Options | Recommendation |
|---|---|---|---|
| D-1 | Event mechanism (R-050) | (a) drop NATS; chain via A2A results + direct ingestion call. (b) keep NATS as the only chaining signal; A2A polling only for timeouts/failures. (c) keep both as designed | **(b)**: it keeps the replayable event log and the decoupling that continuous use and later notifiers need, and removes the double signal. Choose (a) if M0 time is the main constraint. |
| D-2 | Read isolation between projects (R-036) | (A) per-project reads with an allowlist parser and parse-tree checks. (B) one deployment = one read-trust domain; roles gate only writes | **(B)** for the prototype; separate deployments for confidential projects. |
| D-3 | Record/replay implementation (R-017) | (a) library-level recorders (current). (b) one OpenAI-compatible replay server for all callers incl. LangChain4j and embeddings | **(b)**: it is the only way to cover LiSSA/ArDoCo, and it simplifies both libraries. |
| D-4 | Security baseline before M0 implementation (R-033, R-034, R-035, R-037, R-045) | accept the proposed fixes now / defer to M5 | **Accept now**: all are S effort at contract/deploy level, and deferring costs more once data and plugins exist. |
| D-5 | Fingerprint, scope, revert, supersession and concurrency semantics (R-001, R-011, R-012, R-015, R-016) | accept the proposed semantics / alternative | **Accept**: these must be fixed in contracts and vectors before T0.2/T0.3 start. |
| D-6 | Java A2A runtime (R-048) | `a2a-java` + Quarkus (current) / hand-written JSON-RPC without Quarkus | Decide in T0.4 with explicit criteria (a custom executor works; no dependency conflicts with ArDoCo). Default `a2a-java`. |
| D-7 | Scope trimming (R-051, R-052) | cut the listed modules, extension points and T1.8 / keep them | **Cut**, and re-add on the first concrete need. |
| D-8 | Evaluation protocol (R-038, R-043, R-044) | adopt the proposed protocol / keep S3 §3.3 | **Adopt**, and pre-register it in `experiments/`. |
| D-9 | Plugin rules (R-047) | plugins are external systems with a gateway obligation / plugins must follow H1/H2 | **External systems with a gateway obligation** and their own tokens (R-033). |
| D-10 | M0 re-baseline (R-003) | 10–16 weeks / cut scope via D-1(a), D-2(B), D-7 and re-estimate | Apply D-2 and D-7 (and D-1 as chosen), then re-estimate. Expect about 10–12 weeks for one developer. |

Also still open from Stage 3: **Q-20**: should the ArDoCo inconsistency adapter consume links from all approaches, or only ArDoCo's? Default: only ArDoCo's first.

**Your decisions (2026-10-06):**

| # | Decision | Applied as |
|---|---|---|
| D-1 | recommendation (b) | events are the only chaining signal; one completion event per task |
| D-2 | projects isolated, only per-project reads; separate deployments for confidential projects in the future | option A: parse-tree scope guard, `analyst` removed |
| D-3 | recommendation | `llm-replay` proxy |
| D-4 | recommendation | per-service tokens, plugin token pairs, gateway admin auth, `-local` keys, networks, memory limits, session rules |
| D-5 | recommendation | sentence keys, fingerprint, artifact scope, guards, `Ingestion`/reactivation, active facts, pipeline lock |
| D-6 | recommendation | runtime decided in T0.4 (check 16) |
| D-7 | recommendation | modules, approaches, detector kinds, plugin transports and T1.8 cut |
| D-8 | adopt as proposed | pre-registered protocol (S3 §3.3), tasks T2.3, T2.4, T3.6 (new), T4.2, M2 exit criterion |
| D-9 | recommendation | plugins as external systems (ADR-0012) |
| D-10 | confirmed | M0 = 13 weeks (range 9–17), S3 §1.3; restore, retention and e2e statements aligned |
| Q-20 | links from all approaches; ArDoCo and other approaches write their links to the KG, detection uses them | ADR-0015 (proposed), T3.1 |

## 5. ADR impact

| ADR | Status now | Change depending on your answers |
|---|---|---|
| 0001 language per component | accepted | Only if the Java agents are merged (R-049; not recommended) |
| 0002 A2A for commands | accepted | Java runtime part → `proposed` until T0.4 (D-6, R-009); event/A2A relation per D-1 |
| 0003 NATS event bus | accepted | D-1: superseded (a), or amended to "events are the only chaining signal" (b) |
| 0004 LLM gateway | proposed | Add confirmation criteria: admin auth (R-034), local-only keys (R-035), no fallbacks for experiments (R-041), keyless start (R-040) |
| 0005 KG schema/provenance/versioning | accepted | Supersession ("active facts"), reverts, `Ingestion` node, content-based sentence keys, migration mechanism (R-001, R-012, R-015, R-020, R-029) |
| 0006 orchestration | accepted | Per-project pipeline lock, `actionKey` with trigger id, inbox transaction, BLOCKED resume (R-016, R-039, R-024, R-023) |
| 0007 inconsistency lifecycle | accepted | Normative transition table, user reopen, artifact-based scope, completed-detector guard, fingerprint without detector version, `evidenceHash` (R-001, R-011, R-018) |
| 0009 NL-to-Cypher | accepted | D-2 isolation model; allowlist parser if option A (R-036) |
| 0010 agent library | accepted | D-3 replay server; D-7 module cuts (R-017, R-051) |
| 0011 console | accepted | Session/CSRF requirements, safe rendering (R-045, R-032) |
| 0012 pluggable approaches/detectors | accepted | Plugin rules and tokens (R-047, R-033); adapter modules (R-030); one transport per language (R-051) |
| 0013 users and roles | accepted | D-2 role model; per-service token files (R-036, R-033) |
| 0014 graph database | accepted | D-7: drop the three-way benchmark (R-052) |
| 0015 ArDoCo integration (new) | – | To be written as `proposed`: pinned build until release, doc/model pairing, canonical text rendering, link source (Q-20, R-025, V-16, V-17, V-27) |

## 6. Proposed next steps

1. You fill in "My decision / comment" and "Status" in Section 3, and answer D-1…D-10 and Q-20.
2. One **contract/doc PR** applies the accepted items that change contracts or semantics (R-001, R-011, R-012, R-015, R-018, R-019, R-020, R-022, R-028, R-033; config schemas):
   - update `contracts/` and the conformance vectors;
   - update the S1/S2 sections;
   - update the ADRs per Section 5, recording new decisions as dated amendments or superseding ADRs, and adding ADR-0015 as `proposed`.
3. One **deploy PR** for the security and resource items (R-033, R-034, R-035, R-037, R-040), including the new T0.4 checks.
4. Update Stage 3: re-baselined calendar (D-10); new or changed tasks (T0.1b config schemas, T0.9 spec, replay server, removed modules); evaluation protocol (D-8); risk register.
5. Re-run the three review passes on the changed sections only and close the log entries. Then start T0.1.

## 7. Changelog of trivial fixes applied during review

| File | Fix |
|---|---|
| docs/stage-1-analysis-architecture.md §0 item 1 | "behind fixed A2A plugin skills" → "behind fixed plugin operations (A2A skill or plain HTTP)" |
| docs/stage-1-analysis-architecture.md §0 item 10 | Stale "BSL license is an open point (Q-11)" → kept for speed (Q-11, ADR-0014) |
| docs/stage-1-analysis-architecture.md §3.2 event table | `tracelinks.updated` payload `created` → `approachVersion, …, linkCount` (as in the contract) |
| docs/stage-1-analysis-architecture.md §3.3 skills table | Plugin extract input: added the required `contentHash` |
| docs/stage-2-contracts-repository.md header | "Stage 1 (revision 2)" → "revision 3" |
| docs/stage-3-implementation-plan.md | RK-17 moved after RK-16; V-26/V-27 reordered; V-24 "T3.3" → "T2.3"; T0.4 V-list aligned (V-18–V-21) |
| docs/tasks/T0.4-gateway-and-interop-spike.md | V-list aligned with Stage 3 (V-4–V-8 added) |
| adr/0004-llm-gateway.md | Route list includes `chat-alt` |
| adr/0010-minimal-agent-library.md | Q-2 marked answered |
| adr/0012-pluggable-approaches-and-detectors.md | Option P2 endpoints aligned with the contract (`/manifest`, `/v1/extract`, `/v1/detect`) |
| contracts/agent-cards/tlr-agent.json, inconsistency-agent.json | Each description names only its own catalog |
| contracts/common.schema.json `SkillError.code.examples` | Added `UNKNOWN_SKILL` (used in the vectors) |

These slips were deliberately **not** fixed, because they touch semantics or deploy files; they are listed for the next PR:
- S1 §3.4c transition body contains `actor`, while the contract uses the header.
- Consumer backoff has 3 values in S1 and 4 in S2.
- S1 `Run.status` enum lacks `partial`.
- S1 §4.4 example omits required properties.
- The S1 volume table lacks `otel-data`.
- S2 §3.3 refers to a non-existent `deploy/dev.env`.
- `make ardoco` and `scripts/` are missing from the T0.1/S2 layout.
- The compose anchor `x-agent-volumes` is unused.

## 8. Changes applied for the decisions (2026-10-06)

Coupled items applied together with a decision, because the decision does not work without them: R-020 and R-039 (reverts, D-5), R-010 (current-link definition, D-5), parts of R-009 (amendment rule, ADR-0002 runtime, ADR-0015) and R-025 (Q-20). All other log entries are unchanged and open.

| Artifact | Change | Decisions |
|---|---|---|
| contracts (all) | Contract version 0.1.0 → 0.2.0 (breaking changes before 1.0) | – |
| contracts/common.schema.json | Chat/tool/cassette-request types removed; `CassetteEntry` redefined for `llm-replay` (raw OpenAI bodies, key rule); `sandman.actor` only from the orchestrator | D-3, D-4, D-7 |
| contracts/skills.schema.json | `scopeVersionIds` → `scopeArtifactIds` (detect input, plugin detect input); `llmPolicy` required in plugin inputs; fingerprint rule in `Finding`; `cypher-rule` removed from detector kinds | D-5, D-9, D-7 |
| contracts/events.schema.json | `causationid` required for completion events; `tracelinks.updated` once per task with `runs[]`; `ingestion.completed` with `seq`, `reactivatedVersionIds`, published only on change; `scopeArtifactIds` | D-1, D-5 |
| contracts/kg/schema.yaml, init.cypher | `Ingestion` node, `HAS_INGESTION`/`ACTIVATED`/`DELETED`, `PREVIOUS` removed, `Project.ingestionSeq`, `TraceLink.active`, content-based TextUnit keys, fingerprint/active-facts conventions, system reasons | D-5 |
| contracts/conformance/vectors.json | New `sentenceKey` cases; fingerprints recomputed (detector without version, link keys, byte-wise sort); cassette keys recomputed for OpenAI bodies; `ing` stable id; `toolDispatch` removed; completion-event envelope cases | D-1, D-3, D-5, D-7 |
| contracts/openapi | extraction-agent: `Ingestion.seq`, `reactivatedVersionIds`, state-based idempotency; query-service: scoped raw Cypher for viewers, `analyst` removed, unscoped only admin, 10 s user timeout; shared/plugin-http: token model, plugin obligations; orchestrator: `force` semantics | D-2, D-4, D-5, D-9, R-039 |
| deploy/docker-compose.yml | `llm-replay` service; per-service token secrets + hash file; `_local` LLM keys; `edge` without masquerading; `llm-local` network for Ollama; `mem_limit` everywhere, Memgraph `--memory-limit`; gateway off `edge`; unused `x-agent-volumes` anchor removed | D-3, D-4 |
| deploy/gen-secrets.sh | Token per service/plugin, `svc_tokens_verify.json`, plugin inbound tokens, `-local` virtual keys, Bifrost admin password (policy-compliant) | D-4, D-9 |
| deploy/gateway/config.json | `governance.auth_config`; Ollama-only `<component>-local` keys | D-4 |
| adr/README.md, adr/0015 | Amendment rule before M0; ADR-0015 "ArDoCo integration" (proposed) | R-009, Q-20 |
| adr/0002–0010, 0012–0014 | Dated amendments | D-1…D-7, D-9 |
| docs/stage-1 (rev. 4) | §0.2 changelog and all affected sections (2.2, 2.3, 3.1–3.4, 4.1–4.4, 5, 6, 7, 8.4, 9, 11, 12, 13) | all |
| docs/stage-2 (rev. 3) | Conventions, A2A completion, events table, authorization matrix, config formats, env vars, layout, Compose notes incl. memory budget, stub specs, walking skeleton steps 6, 7, 9 (revert) | all |
| docs/stage-3 (rev. 3) | Tasks T0.3 (M), T0.4 (M), T0.5 (L), T0.9 (L+S), new T0.13 `llm-replay`, T1.6, T2.2, T3.1, T3.3, T5.4; T1.8 removed; calendar re-estimate; security tests; RK-2/4/6/10/17 updated, RK-18/19 new; V-28…V-35; Q-20 answered | all |
| docs/tasks/T0.1–T0.8 | Updated to the decisions (library modules, completion events, tokens, ingestion/reactivation, active links, detection semantics, pipeline lock, T0.4 checks 11–17) | all |

Validation after the changes: JSON Schemas valid (2020-12); event vectors behave as expected (valid/invalid); Agent Card references resolve; all 7 OpenAPI files validate; vectors recomputed with `rfc8785`; `docker compose config` passes for the base file, all profiles and the dev overlay with secrets from the new `gen-secrets.sh`.

**Follow-up from a consistency pass over the applied changes** (fixed in the same change set):
- re-runs with identical link ids keep their links active (activation via `PRODUCED`);
- element `runId` set only on create, so retries never delete pre-existing elements;
- `Ingestion.eventPublished` with re-publishing, so a failed publish is not lost under state-based idempotency;
- plugin callers registered as `plugin:<id>`, rejected everywhere except query-service reads on granted projects (`config/query/plugins.yaml`);
- plugin skills publish no completion event and authenticate with the inbound token;
- `causationid` = `messageId` of the A2A request (stored before sending, so no race with fast tasks);
- `/v1/*` without session cookie;
- section-slug rule with vectors `sectionKey`, and ArDoCo sentence mapping by text hash plus document order;
- leftovers of per-approach events, plugin transports, Java `ScriptedLlmClient`, the ADR-0010 title and the events schema path;
- ADR-0008 amendment for `llm-replay`;
- T0.4 check for V-32;
- calendar arithmetic (9–17 weeks, planning value ≈ 13).

Not fixed, because they were already open log entries before the decisions:
- actor header "ignore vs. 403" (R-005);
- ingestion status code 202 vs. 201 (R-004);
- file-level `Requirement` element in the stub (R-002);
- re-resolving explicit references when the referenced artifact changes (R-014);
- plugin warnings not visible through the API (R-021).

**D-8 and D-10 (2026-10-06):**

| Artifact | Change |
|---|---|
| docs/stage-3 (rev. 4) | §3.3 evaluation protocol (exact ArDoCo equality, LiSSA live re-run with Δ and CI, paired bootstrap/McNemar/Holm, Wilcoxon only with ≥ 6 projects, variance pilot, contamination probe, NL splits by template and project, independent authors, labeling study); M2 exit criterion; T2.3, T2.4, T4.2 extended; new T3.6 labeling study; RK-7, RK-15; calendar confirmed (M0 = 13 weeks, M3 ≈ 4 weeks); A3-4 e2e times |
| docs/stage-2 | TLR "finalized when" uses the protocol instead of ±0.02 F1 in replay |
| docs/stage-1 | §0.2 rows D-8, D-10; §11.6 protocol pointer; §11.5 restore drill from M5; §4.2 retention in M5 |

## 9. Changes applied for decisions (2026-10-09)

Batch "D-8" of 2026-10-09 (a different batch from the evaluation-protocol decision D-8 of 2026-10-06): you accepted the recommendation for all remaining open items. Contract changes are not applied here; each owning task starts with a contract PR (additive), as in §8.

| Item | Outcome | Owning task |
|---|---|---|
| R-002 | applied (code already: file element is a `TextUnit`; Stage 2 §4.2 prose fixed) | – |
| R-004 | applied (Stage 1 §3.2/§3.4a: 201; ADR-0003 amendment) | – |
| R-005 | accepted; Stage 1/2 prose fixed, `shared.yaml` and tests open | T1.13 |
| R-006 | accepted; docs part applied (Stage 1 co-owner exception, Stage 2 `configHash`), rest open | T1.13 |
| R-007 | accepted | T1.13 |
| R-008 | applied (Stage 1 §5.1/§9 aligned, ADR-0010 amendment; budget error mapping exists) | – |
| R-014 | accepted (committed separately) | T1.12 (new) |
| R-018 | accepted | T1.13 (new) |
| R-019 | accepted: planned operations returning 501, config schemas in `contracts/config/`, examples path `config/query/examples.yaml` (Stage 1 fixed) | T1.13 |
| R-021 | accepted; kind rules already from `sandman.yaml`, rest open | T1.13 |
| R-022 | applied (T2.1) | – |
| R-023, R-024 | accepted (R-024 verify first: code already commits inbox row and steps together) | T1.13 |
| R-027 | accepted | T2.3 |
| R-028 | accepted | T1.13 |
| R-029 | accepted; parts already in code, remainder open | T1.14 (new) |
| R-030 | accepted | T2.2, T3.1 |
| R-031 | accepted | T1.14 |
| R-032 | accepted, treated as security-relevant | T4.3, T4.4 |
| R-040 | closed: V-34 check passed in T0.4, keyless e2e runs in CI | – |
| R-041 | accepted | T1.14 |
| R-042 | closed: Testcontainers with Memgraph in use; OpenAPI 3.1 external `$ref` validated in CI; Schemathesis not used | – |
| R-046 | accepted | T1.14 |
| R-049 | applied: services stay separate | – |

| Artifact | Change |
|---|---|
| docs/tasks/T1.12–T1.14 | new task specs; rows added to Stage 3 §1.2 |
| docs/stage-3 | T2.1 (R-022), T2.2 and T3.1 (R-030), T2.3 (R-027), T4.3 and T4.4 (R-032) extended |
| docs/stage-1, docs/stage-2 | prose: 201 on intake, 403 for forged actor headers, co-owner exception, `configHash` definition, §5.1 orchestrator KG reads, §9 library API, examples path, §4.2 file-level element |
| adr/0003, adr/0010 | dated amendments (R-004, R-008) |
| docs/tasks/T1.6 | marked done; `chat-local` = `ollama/qwen3:4b`; nightly canary deferred; deviations |
| docs/user/getting-started.md | `local-llm` profile needs about 12 GiB Docker memory |

Not changed: `contracts/`, code, config, deploy, tests.

## 10. Applied via T1.12 and T1.13 (2026-10-10)

R-005, R-006, R-007, R-014, R-018, R-019, R-021, R-023, R-024 and R-028 are applied by T1.12 and T1.13 (commits `2144b6a..cfd2e58` on `develop`); their status is `accepted · applied`. Deviations are listed in the task specs.

## 11. Applied via T1.14 (2026-10-10)

R-029, R-031, R-041 and R-046 are applied by T1.14; their status is `accepted · applied`. Remainders:

- LiSSA `ARG LISSA_COMMIT` waits for T2.2, because nothing fetches LiSSA yet.
- The apt package `curl` is unpinned in the Java Dockerfiles.
- The testcontainers images use tags only.
- The extraction agent holds uploaded archives in memory. The caps `SANDMAN_MAX_ARCHIVE_BYTES` and `SANDMAN_MAX_ENTRY_BYTES` bound the size.
- The agent messageId dedupe is in memory (1024 entries). A restart clears it. The orchestrator `_poll` recovers.

Deviations are listed in the task spec.

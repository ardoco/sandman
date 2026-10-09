---
status: accepted
date: 2026-10-06
---

# Inconsistency lifecycle and re-detection

## Context and Problem Statement

Users view detected inconsistencies and resolve them or mark them (e.g., "later", "unproblematic"). After artifacts change, detectors run again. Which states exist, which transitions are allowed, and how are re-detected inconsistencies matched to existing ones?

## Decision Drivers

* Users must not see the same issue again after they decided on it — unless the underlying content changed.
* User claims ("fixed") should be verified by re-detection.
* False positives are valuable evaluation data and must be distinguishable from accepted real issues.
* Matching must survive new artifact versions (new element ids).

## Considered Options

* States: (S1) `OPEN, DEFERRED, ACCEPTED, REJECTED, RESOLVED (+verification), REOPENED`; (S2) `OPEN, CLOSED` with a resolution reason; (S3) S1 without `REJECTED` (false positive folded into `ACCEPTED`).
* Matching: (M1) fingerprint = hash(type, detector, sorted involved element keys); (M2) match by involved element ids; (M3) LLM-based similarity matching.

## Decision Outcome

Chosen: **S1 + M1**.

* User transitions: `OPEN/REOPENED → DEFERRED | ACCEPTED | REJECTED | RESOLVED`; `DEFERRED → OPEN | ACCEPTED | REJECTED | RESOLVED`; `ACCEPTED/REJECTED → OPEN`. Optimistic concurrency via `expectedStatus` (409 on conflict). Reason required for `ACCEPTED`, `REJECTED`, `DEFERRED`.
* System transitions after a detection run with scope S: re-detected `RESOLVED` → `REOPENED`; re-detected `ACCEPTED/REJECTED` → `REOPENED` only if `evidenceHash ≠ acceptedEvidenceHash`; not re-detected and fully inside S → `RESOLVED` (`verification=confirmed`); outside S → untouched. A failed detector never auto-resolves its types.
* Every transition creates a `StatusChange` node (actor `user:<name>` or `system:<detector>`).

### Consequences

* Good, because decisions persist across versions and are reconsidered only on evidence change.
* Good, because labeled false positives support precision evaluation.
* Bad, because fingerprints over keys break if an element is renamed (key changes) → appears as new inconsistency (ceiling; key-mapping across renames later if needed).
* Bad, because six states are more to explain than open/closed.

### Confirmation

State machine unit tests covering every allowed/forbidden transition; scenario test: detect → accept → change artifact → re-detect → `REOPENED`.

## Pros and Cons of the Options

### S1

* Good, because expresses all user intents named in the requirements plus verification.
* Bad, because more states.

### S2

* Good, because simple.
* Bad, because "later" and "unproblematic" become reasons, making re-detection rules depend on free-text-like values.

### S3

* Good, because one state fewer.
* Bad, because loses false-positive labels.

### M1 fingerprint over keys

* Good, because stable across versions, deterministic.
* Bad, because sensitive to renames.

### M2 element ids

* Good, because exact.
* Bad, because every new version breaks the match.

### M3 LLM similarity

* Good, because robust to renames.
* Bad, because non-deterministic and costly.

## More Information

* Related: ADR-0005, ADR-0011.

## Amendments

* 2026-10-06, review decision D-5 (R-001, R-011, R-016):
  * **Fingerprint** = sha256(type, detector id **without version**, involved keys sorted byte-wise by UTF-8), involved key = element key or `sourceKey->targetKey` for a link. Detector upgrades and new link ids no longer reset triage decisions.
  * **Scope by artifact:** an existing inconsistency is in scope iff every artifact of its involved keys is in the run's `scopeArtifactIds` (absent = project).
  * **Auto-resolve guard:** only items whose detector completed in this run may be auto-resolved; detectors not run (subset, disabled, failed) leave their items untouched.
  * **Auto-resolvable states:** `OPEN`, `REOPENED`, `DEFERRED` and `RESOLVED` with `verification=pending`. `ACCEPTED`, `REJECTED` and confirmed `RESOLVED` are never changed by non-detection.
  * **Deleted artifacts:** every detection run resolves auto-resolvable items that involve an artifact without current version (reason `artifact-deleted`, `verification=confirmed`), independent of scope and detector; if the artifact returns and the finding is re-detected, the item is `REOPENED`.
  * **Conditional system writes:** system transitions use `WHERE i.status = $observed`; on a concurrent user transition the user's decision wins and the system transition is skipped.
* 2026-10-09, review decision D-8 (R-018):
  * **Normative transition table.** This table replaces the transition prose of the Decision Outcome; the contracts (`inconsistency-agent.yaml`, `contracts/kg/schema.yaml`) and the state machine tests follow it. "Event" is the CloudEvent published; every transition writes one `StatusChange` node (`HAS_STATUS_CHANGE`). System writes are conditional on the observed status (`WHERE i.status = $observed`), so a concurrent user decision wins.

    | # | From | To | Actor | Condition | StatusChange (`reason`) | Event |
    |---|---|---|---|---|---|---|
    | 1 | – (new) | `OPEN` | `system:<detector>` | finding with unknown fingerprint; `verification=none` | creation entry, **no `from`**, `to=OPEN` (`detected`) | `inconsistencies.updated` (`newIds`) |
    | 2 | `OPEN`, `REOPENED`, `DEFERRED` | same | `system:<detector>` | re-detected | none (evidence and `lastRunId` refreshed) | – |
    | 3 | `RESOLVED` (any `verification`) | `REOPENED` | `system:<detector>` | re-detected; `verification=none` | `re-detected` | `inconsistencies.updated` (`reopenedIds`) |
    | 4 | `ACCEPTED`, `REJECTED` | `REOPENED` | `system:<detector>` | re-detected and `evidenceHash ≠ acceptedEvidenceHash`; `verification=none` | `re-detected` | `inconsistencies.updated` (`reopenedIds`) |
    | 5 | `ACCEPTED`, `REJECTED` | same | `system:<detector>` | re-detected and `evidenceHash = acceptedEvidenceHash` | none (only `lastRunId`, `detectorVersion`) | – |
    | 6 | `OPEN`, `REOPENED`, `DEFERRED`, `RESOLVED` with `verification=pending` | `RESOLVED`, `verification=confirmed` | `system:<detector>` | not re-detected, its detector completed in this run, and every involved artifact is in the run's scope | `not-redetected` | `inconsistencies.updated` (`autoResolvedIds`) |
    | 7 | as row 6 | `RESOLVED`, `verification=confirmed` | `system:<detector>` | an involved artifact has no current version (independent of scope and detector) | `artifact-deleted` | `inconsistencies.updated` (`autoResolvedIds`) |
    | 8 | `ACCEPTED`, `REJECTED`, `RESOLVED` with `verification=confirmed` | same | – | not re-detected | none (untouched) | – |
    | 9 | `OPEN`, `REOPENED` | `DEFERRED`, `ACCEPTED`, `REJECTED`, `RESOLVED` | `user:<name>` | `expectedStatus` matches; `reason` required for `DEFERRED`, `ACCEPTED`, `REJECTED`; `RESOLVED` sets `verification=pending`; `ACCEPTED`/`REJECTED` set `acceptedEvidenceHash = evidenceHash` | user text | `inconsistency.status.changed` |
    | 10 | `DEFERRED` | `OPEN`, `ACCEPTED`, `REJECTED`, `RESOLVED` | `user:<name>` | as row 9 | user text | `inconsistency.status.changed` |
    | 11 | `ACCEPTED`, `REJECTED` | `OPEN` | `user:<name>` | `expectedStatus` matches | user text (optional) | `inconsistency.status.changed` |
    | 12 | `RESOLVED` | `OPEN` | `user:<name>` | **new:** any `verification` (the user disagrees with the resolution or a claimed fix is wrong); `expectedStatus` matches; sets `verification=none` | user text (optional) | `inconsistency.status.changed` |

    Every other user transition is rejected with 422 (e.g. `RESOLVED → ACCEPTED`, `OPEN → OPEN`); `allowedTransitions` of the API lists the targets of rows 9 to 12 for the current status. `status.changed` has a required `from` (it is published for user transitions only, which always have one); rows 6 and 7 from `RESOLVED` with `verification=pending` write a `StatusChange` `RESOLVED → RESOLVED` (verification confirmed) and count as auto-resolved.
  * **`evidenceHash`** = sha256 hex of the involved Elements' `key=contentHash` pairs (for an involved link: of both endpoint Elements), de-duplicated, sorted byte-wise by UTF-8 and joined by U+001F (conformance vectors `evidenceHash`). `Element.contentHash` is required, so a pair is never incomplete.

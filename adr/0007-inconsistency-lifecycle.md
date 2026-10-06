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

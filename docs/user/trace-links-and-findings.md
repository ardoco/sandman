# Trace links and findings

## Trace links

A trace link connects two elements, for example a requirement and a code unit. It records the `approach` that produced it, `approachVersion`, a `confidence` (0 to 1), a `linkType` and a `status`. Only **active** links between **current** elements count: queries, exports and detection ignore the rest. A new run of an approach replaces its earlier active links between the same versions.

Read links on the console links page (see [Review](#review-trace-links)), through [Ask and Cypher](querying.md) (`links of <name>`), through `sandman export --project <id> tracelinks`, or in the "Involved" table of a finding.

### Approaches today

| Approach | Where | What it does |
|---|---|---|
| `explicit-reference` | extraction agent | a requirement id mentioned in a file links the requirement to the element containing the mention; confidence 1.0; `relates` for built-in extraction ([ingesting-artifacts.md](ingesting-artifacts.md)) |
| `stub-name-match` | TLR agent | links two elements whose names share a word of at least 4 letters (camelCase and punctuation split, lower-cased); `relates`, confidence 0.5, rationale `shared tokens: ...`. It exists to exercise the pipeline and is not a quality approach |
| `ardoco` | TLR agent | ArDoCo links the sentences of an architecture document to the components of an architecture model (PCM or UML); `documents`, confidence 1.0 |
| `transarc` | TLR agent | TransArC links the sentences of an architecture document to the source files through an architecture model. It is the default approach for document to code. It uses the model of the project or, if there is none, a [derived model](derived-models.md). It needs the container `code-extractor-ardoco` |
| `ardocode` | TLR agent | ArDoCo links the sentences of an architecture document to the source files of the project without a model; `documents`, confidence 1.0. ArDoCo links a sentence to a package or directory too. The approach then links the sentence to every file in it. It is off by default (`enabled: false` in `config/tlr/approaches.yaml`). It needs the container `code-extractor-ardoco` |
| `lissa` | TLR agent | LiSSA links requirements to source code. It ranks the code files of each requirement by embedding similarity. A language model then confirms each candidate pair. It calls the LLM gateway |

In the mini example, REQ-1 gets a `stub-name-match` link to the `MediaCache` code unit and component (shared words "cache" and "media") and an `explicit-reference` link to `MediaCacheTest` (type `verifies`, supplied by the stub code extractor); REQ-2 and REQ-3 get none. Because the link-recovery steps of the configured kind pairs are merged while they wait, links between other kind combinations can appear too, for example a requirement to a model component.

Status values are `candidate`, `confirmed` and `rejected`. A new link is `candidate`. A triager can confirm or reject it. Exports leave out `rejected` links unless asked.

### Review trace links

A triager (or higher) confirms or rejects a link. The review applies to the element pair. It changes every active link with the same source element and the same target element, over all approaches.

Console: **Links** on the project row opens `/console/projects/<id>/links`.

- Filter by artifact id, approach, status and minimum confidence.
- Each row shows one element pair: source and target with an excerpt, the approaches, the best confidence, the number of approaches, the status and the reviewer.
- Open **Rationale** to read the rationale of each approach and the review reason.
- Press **Confirm**, **Reject** or **Reset**. **Reject** needs a reason. **Reset** sets the status back to `candidate` and removes the review.
- A viewer sees the page without these buttons.

The API offers the same action. Use the id of an active link (`tl_...`) and a token with the role `triager` or higher:

```sh
curl -s -X POST -H "Authorization: Bearer $SANDMAN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"status":"rejected","reason":"wrong component"}' \
  http://127.0.0.1:8080/v1/projects/demo/links/tl_.../review
```

The body has `status` (`confirmed`, `rejected` or `candidate`) and an optional `reason` of up to 2000 characters. The response lists `reviewedLinkIds`.

| Answer | Cause |
|---|---|
| `403` | your role is lower than `triager` |
| `404` | the link does not exist in this project |
| `409` | the link is not active, or it has the approach `explicit-reference` (change the reference text in the artifact instead) |
| `422` | the status is `rejected` and the reason is empty |

After a review, SANDMAN runs the detection again for the affected artifacts. A rejected link no longer counts as a trace, so a requirement without another link gets an `untraced-requirement` finding. The review writes the event `sandman.tracelink.reviewed`; the orchestrator starts the detection from it.

A review survives a new version of an artifact. At activation of new links, SANDMAN copies the latest review of an earlier link if the link type, the key of both elements and the content hash of both elements are equal. The link then shows the origin `carried-over`. If the content of one element changed, SANDMAN copies nothing and the link stays `candidate`.

Default CSV and JSON exports of `tracelinks` have the review columns `reviewed_by`, `reviewed_at` and `review_reason` ([querying.md](querying.md#export)).

## Findings

A finding is a potential inconsistency reported by a detector. Two detectors run by default: `stub-untraced` and `ardoco-id`. The detector `artemis-id` (LLM based) exists but is disabled in `config/inconsistency/plugins.yaml`.

The detector `ardoco-id` runs the ArDoCo inconsistency detection on each current document and architecture model (PCM or UML). It uses the active trace links of all approaches. It reports two types:

| Type | Severity | Meaning |
|---|---|---|
| `undocumented-model-element` | `warning` | A model component has no mention in the documentation. The involved element is the model element. |
| `missing-model-element` | `info` | A sentence names an entity that is not in the model. One finding per sentence. The evidence lists the words with their confidence. |

The detector needs the CoreNLP service. If the service is down, the run is `partial` and the detector reports `failed`. No `ardoco-id` finding resolves in that run.

The detector `stub-untraced` reports type `untraced-requirement`, severity `warning`, title `Requirement without trace link: <requirement name>`, for each current requirement that has no active, non-rejected link to another current element. In the mini example after `s1`, REQ-2 and REQ-3 are reported.

### Console

**Projects**, then the project name, opens `/console/projects/<id>/inconsistencies`:

- the counts per status and a table with title, type, severity, status, detector and last update (50 per page, **Next page**);
- filters for status, severity, type and artifact id (`art_...`), sorted by severity by default or by last update;
- rows shaded as new when they are `OPEN` or `REOPENED` and changed since your previous visit to this page (the Projects page counts them in "New / reopened since last visit").

The detail page shows status, severity, type, detector and version, confidence, verification, a description, the evidence (JSON), the involved elements or links (role, name, key, file and lines, excerpt), the full status history with actor and reason, and, for triagers and above, the **Change status** form.

The API offers the same data: `GET /v1/projects/{id}/inconsistencies?status=OPEN,REOPENED&severity=warning&type=...&artifactId=...&changedSince=<ISO time>&limit=50&cursor=...` and `GET /v1/inconsistencies/{id}`.

### Statuses

| Status | Meaning |
|---|---|
| `OPEN` | new, not yet looked at |
| `REOPENED` | came back: it was resolved or accepted/rejected and the detector reports it again |
| `DEFERRED` | postponed; reason required |
| `ACCEPTED` | known and intended; reason required |
| `REJECTED` | false positive; reason required |
| `RESOLVED` | gone or fixed |

A triager (or higher) can move a finding. A move to `ACCEPTED`, `REJECTED` or `DEFERRED` needs a cause (see below):

| From | To |
|---|---|
| `OPEN`, `REOPENED` | `DEFERRED`, `ACCEPTED`, `REJECTED`, `RESOLVED` |
| `DEFERRED` | `OPEN`, `ACCEPTED`, `REJECTED`, `RESOLVED` |
| `ACCEPTED`, `REJECTED` | `OPEN` |
| `RESOLVED` | none (the system reopens it if it reappears) |

### Cause

The cause says why you moved the finding. The console requires a cause for `ACCEPTED`, `REJECTED` and `DEFERRED`. It offers an optional cause for `RESOLVED`. The history of the finding shows the cause next to the reason.

| Cause | Meaning |
|---|---|
| `extraction-error` | an extractor produced a wrong or missing element |
| `link-error` | a trace link is wrong or missing |
| `detector-error` | the detector is wrong although its inputs are correct |
| `artifact-inconsistency` | the artifacts really disagree; somebody must fix an artifact |
| `planned-change` | the artifacts disagree on purpose because of a known future change |
| `intentional` | the disagreement is intended and stays |

The API accepts a transition without a cause, so old scripts keep working. A cause together with `to: OPEN` answers `422`. An unknown cause answers `400`. The system sets no cause for its own moves.

The reason is free text up to 2000 characters. The form sends the status you saw; if someone else changed it meanwhile you get `409` ("The status was changed concurrently") and must review the current state. Forbidden moves answer `422`; viewers get `403`.

```sh
curl -s -X POST -H "Authorization: Bearer $SANDMAN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"to":"DEFERRED","expectedStatus":"OPEN","reason":"after the next release","cause":"planned-change"}' \
  http://127.0.0.1:8080/v1/inconsistencies/inc_.../transitions
```

### What the system does by itself

After each detection run, for findings within the run's scope:

- A finding that is detected again stays as it is (`OPEN`, `REOPENED`, `DEFERRED`).
- An `OPEN`, `REOPENED` or `DEFERRED` finding that is no longer detected becomes `RESOLVED` (actor `system:<detector>`, reason `not-redetected`, verification `confirmed`). In the mini example, ingesting `s2` renames REQ-2 so that it matches `MediaCache`; its finding resolves.
- When a file is deleted, findings about it resolve with reason `artifact-deleted`.
- A `RESOLVED` finding that is detected again becomes `REOPENED`. Reverting to `s1` reopens the REQ-2 finding.
- `ACCEPTED` and `REJECTED` findings are kept as long as the involved elements are unchanged since your decision. If their content changed and the finding is still detected, they become `REOPENED`.
- A finding you set to `RESOLVED` has verification `pending`: the next run that does not find it sets `confirmed`; one that finds it reopens it.

Your own decisions win over concurrent system changes. New and reopened findings are also written to the inconsistency agent's log, one line each (logger `sandman.notification`).

Findings and their history can be exported with `sandman export --project <id> inconsistencies` ([querying.md](querying.md)). The export has the cause of each finding.

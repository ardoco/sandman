# Trace links and findings

## Trace links

A trace link connects two elements, for example a requirement and a code unit. It records the `approach` that produced it, `approachVersion`, a `confidence` (0 to 1), a `linkType` and a `status`. Only **active** links between **current** elements count: queries, exports and detection ignore the rest. A new run of an approach replaces its earlier active links between the same versions.

There is no links page in the console. Read links through [Ask and Cypher](querying.md) (`links of <name>`), through `sandman export --project <id> tracelinks`, or in the "Involved" table of a finding.

### Approaches today

| Approach | Where | What it does |
|---|---|---|
| `explicit-reference` | extraction agent | a requirement id mentioned in a file links the requirement to the element containing the mention; confidence 1.0; `relates` for built-in extraction ([ingesting-artifacts.md](ingesting-artifacts.md)) |
| `stub-name-match` | TLR agent | links two elements whose names share a word of at least 4 letters (camelCase and punctuation split, lower-cased); `relates`, confidence 0.5, rationale `shared tokens: ...`. It exists to exercise the pipeline and is not a quality approach |
| `ardoco` | TLR agent | ArDoCo links the sentences of an architecture document to the components of an architecture model (PCM or UML); `documents`, confidence 1.0 |
| `ardocode` | TLR agent | ArDoCo links the sentences of an architecture document to the source files of the project; `documents`, confidence 1.0. ArDoCo links a sentence to a package or directory too. The approach then links the sentence to every file in it. It needs the container `code-extractor-ardoco` |

`lissa` appears in `config/tlr/approaches.yaml` but is disabled. In the mini example, REQ-1 gets a `stub-name-match` link to the `MediaCache` code unit and component (shared words "cache" and "media") and an `explicit-reference` link to `MediaCacheTest` (type `verifies`, supplied by the stub code extractor); REQ-2 and REQ-3 get none. Because the link-recovery steps of the configured kind pairs are merged while they wait, links between other kind combinations can appear too, for example a requirement to a model component.

Status values are `candidate`, `confirmed` and `rejected`. Every link is `candidate` today; neither the console nor the API can confirm or reject a link yet. Exports leave out `rejected` links unless asked.

## Findings

A finding is a potential inconsistency reported by a detector. Today there is one detector, `stub-untraced`: type `untraced-requirement`, severity `warning`, title `Requirement without trace link: <requirement name>`, for each current requirement that has no active, non-rejected link to another current element. In the mini example after `s1`, REQ-2 and REQ-3 are reported.

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

A triager (or higher) can move a finding:

| From | To |
|---|---|
| `OPEN`, `REOPENED` | `DEFERRED`, `ACCEPTED`, `REJECTED`, `RESOLVED` |
| `DEFERRED` | `OPEN`, `ACCEPTED`, `REJECTED`, `RESOLVED` |
| `ACCEPTED`, `REJECTED` | `OPEN` |
| `RESOLVED` | none (the system reopens it if it reappears) |

The reason is free text up to 2000 characters. The form sends the status you saw; if someone else changed it meanwhile you get `409` ("The status was changed concurrently") and must review the current state. Forbidden moves answer `422`; viewers get `403`.

```sh
curl -s -X POST -H "Authorization: Bearer $SANDMAN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"to":"DEFERRED","expectedStatus":"OPEN","reason":"after the next release"}' \
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

Findings and their history can be exported with `sandman export --project <id> inconsistencies` ([querying.md](querying.md)).

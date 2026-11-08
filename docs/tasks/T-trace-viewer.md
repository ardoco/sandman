# T-trace-viewer Trace viewer

Status: implemented (2026-11-08), not yet run against a live stack. Tests use fake data. No browser or desktop window test ran.

The trace viewer shows the artifacts of one project side by side in columns. Trace links are edges. Findings are markers on elements and links. Click an element or link to see its details.

## Scope

| Part | Where | Commit |
|---|---|---|
| Contract 0.10.0 | `contracts/openapi/query-service.yaml` | 371c62a |
| Query module and routes | `services/query-service/src/sandman_query/traceview.py`, `service.py`, `app.py` | 8bf55ef, acd4472 |
| Dashboard page (read-only) | `services/dashboard/src/sandman_dashboard/traceview.py`, `static/traceview.js` | 67d8da9, 81329ec |
| Desktop tab | `apps/desktop/src/views/traceview.ts`, `logic.ts` | 29a89e9, d506515 |

## Query-service API

Both operations need role `viewer`.

| Operation | Route | Answer |
|---|---|---|
| `getTraceView` | `GET /v1/projects/{projectId}/trace-view` | `elements`, `links`, `findings`, `truncated` |
| `getTraceViewElement` | `GET /v1/projects/{projectId}/trace-view/elements/{elementId}` | `element`, `links`, `findings`, `neighbours` |

* `elementId` matches `^el_[0-9a-f]{32}$`. An unknown element gives `404`.
* The service omits properties with a null value. A client must treat every optional property as absent.
* `getTraceView` returns at most 5000 elements and 5000 links. If the project has more, `truncated` is `true`.
* `getTraceViewElement` returns at most 500 links and 200 neighbours.
* The element detail does not contain the properties `embedding` and `embeddingModel`.
* Every statement uses the anchor `projectId = $p` and runs in one read transaction.
* A neighbour has `relation` (`CONTAINS`, `DEPENDS_ON`, `PROVIDES` or `REQUIRES`), `direction` (`out` or `in`), `id`, `name` and `label`.

## Dashboard

* Page: `GET /traceview` with optional query `project`. The nav bar and the project page link to it.
* JSON routes: `GET /api/traceview?project=` and `GET /api/traceview/element?project=&id=`.
* The routes need an admin session. Without a session the dashboard redirects to the login page.
* An invalid project id or element id gives `422`. An unknown element gives `404`.
* The dashboard is read-only. It has no review buttons.
* The dashboard imports `sandman_query.traceview` and runs the same statements on its own Traverse connection. It makes no runtime call to the query-service (ADR-0016).
* The script `static/traceview.js` uses Cytoscape, written in plain ES5 without a build step. The content security policy stays `default-src 'self'`.

## Desktop

* The tab **Trace viewer** follows **Trace links**. Shortcut: Ctrl+6 (Cmd+6 on macOS).
* The tabs **Ask**, **Graph** and **Settings** move to Ctrl/Cmd+7, 8 and 9.
* The detail panel shows **confirmed** and **rejected** buttons for a link. Buttons appear only if the approach is not `explicit-reference` or `user-declared`. They call `reviewTraceLink`.
* **rejected** needs a reason.
* The pure functions `columnsLayout`, `filterLinks` and `worstSeverity` have unit tests in `logic.test.ts`. The dashboard has its own ES5 copy. There is no shared module.

## View behavior

* One column per kind: Requirements, Documentation (`TextUnit`, `Decision`), Architecture (`ModelElement`, dashed if inferred), Code, Tests. Each artifact is a group labelled with its path.
* `confirmed` links are solid. `candidate` links are dashed. `rejected` links are dotted and hidden by default.
* Edge width grows with confidence. The approach label shows only on selection.
* An element with open findings gets a border color by the worst open severity and a prefix `[n]` with the number of open findings.
* A click on an element fades unlinked elements. A click on a neighbour in the panel selects it.
* Filters: columns, link status, approach, minimum confidence, open findings only, only linked or flagged elements, text search.
* If the project has more than 1000 elements, the filter "only linked or flagged" is on by default (dashboard).

## Open

* Run the viewer against a live stack with real ingestion data.
* Check the dashboard page in a browser and the desktop tab in the app.
* Add paging if projects exceed the 5000 element limit.

# Querying and exporting

All queries are read-only and confined to one project. Any project role (`viewer` and up) may run them.

## Ask (console)

**Ask** on the project row (`/console/projects/<id>/ask`) answers a few fixed questions first. The text is matched against five patterns (case-insensitive, a trailing `?` is ignored) and mapped to a fixed Cypher query. The page shows the Cypher and the result table.

| Question | Returns |
|---|---|
| `untraced requirements` (also `show`, `list`, `which`, `the` in front) | requirements without any active trace link: key, name, text |
| `links of <name>` or `links for <name>` | active links of the element with that name or full key: source key, target key, link type, approach, confidence, status |
| `open inconsistencies` | findings in `OPEN` or `REOPENED`: id, type, severity, title, status |
| `components without documentation` | model components with no active `documents` link. The approach `stub-name-match` creates `relates` links only, so a component linked only by it still appears |
| `what changed since <revision>` | versions activated by ingestions after the latest one with that `--revision`: ingestion number, revision, path, version id |

`<name>` is the element's name: for a requirement its title (`links of Cache media files`), for code `MediaCache`. A requirement id alone (`REQ-1`) does not match; use the title or the full key (`art_...#REQ-1`, see the `key` column). Any other question goes to the LLM (strategy `cypher`), which writes one read-only Cypher query. The query passes the scope guard; if it fails, the model gets the error and tries again (at most twice). The response lists all `attempts`. Without a configured LLM, other questions answer `question not understood (M0 stub, no LLM)` and list the supported intents (`id`s from `config/query/intents.yaml`). The strategies `graphrag` and `explore` exist, but `config/query/strategies.yaml` disables them. A disabled strategy answers 501. The Ask page has a strategy selector (`auto`, `cypher`, `graphrag`, `explore`). It shows the resolved strategy, the classification, the evidence and the steps.

The same through the API; `mode: "cypher-only"` returns the Cypher without running it:

```sh
curl -s -H "Authorization: Bearer $SANDMAN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"projectId":"demo","question":"untraced requirements"}' http://127.0.0.1:8080/v1/query/nl
```

## Cypher

`POST /v1/query/cypher` runs a read query against the graph (Memgraph, openCypher dialect). The console has a Cypher box in the [graph explorer](#graph-explorer-console).

```sh
curl -s -H "Authorization: Bearer $SANDMAN_TOKEN" -H 'Content-Type: application/json' http://127.0.0.1:8080/v1/query/cypher -d '{
  "projectId": "demo",
  "cypher": "MATCH (:Artifact)-[:CURRENT_VERSION]->(:ArtifactVersion)-[:HAS_ELEMENT]->(r:Requirement {projectId: $projectId}) RETURN r.key AS key, r.name AS name ORDER BY key"
}'
# {"columns":["key","name"],"rows":[["art_983d...#REQ-1","Cache media files"], ...],"truncated":false,"elapsedMs":2}
```

Body: `projectId`, `cypher` (up to 20 000 characters), optional `params`, `limit` (1 to 1000, default 200; `truncated` tells whether rows were cut) and `timeoutMs` (100 to 30 000, default 10 000; users are capped at 10 s).

### What is allowed

- Clauses: `MATCH`, `OPTIONAL MATCH`, `WHERE`, `WITH`, `UNWIND`, `RETURN`, `ORDER BY`, `SKIP`, `LIMIT`, `UNION [ALL]`, plus pattern comprehensions and `EXISTS { }`.
- Rejected with `422` and a message: `CALL`, `CREATE`, `MERGE`, `SET`, `DELETE`, `REMOVE`, `LOAD`, `SHOW`, `DUMP`, `EXPLAIN`, `PROFILE`, several statements separated by `;`, a user-written `QUERY MEMORY LIMIT`, and any syntax outside the supported subset. Valid but unusual queries may be rejected; rewrite them in the plain form.
- Comments are removed. The statement runs in a read transaction with a 100 MB memory limit; a `LIMIT` is appended when your final `RETURN` has none.

### Project scope

The service binds `$projectId`. Every `MATCH`, `OPTIONAL MATCH`, comprehension and `EXISTS` subquery must be anchored to it in one of three ways: a node with `{projectId: $projectId}`, a `(:Project {id: $projectId})` node, or a variable bound by an earlier anchored pattern. Otherwise:

```
422 pattern is not anchored to the project: use {projectId: $projectId}, (:Project {id: $projectId}) or a variable bound by an anchored pattern in every MATCH, comprehension and subquery
```

Your own parameter names `projectId`, `cypher` and `timeout` are reserved. Without `projectId` in the body, only global admins may query (without anchoring, still read-only); everyone else gets `403`. A project you cannot read answers `404`. As a last check, a result containing data of another project is refused.

### Graph model

`GET /v1/schema` returns the schema contract (`contracts/kg/schema.yaml`). The parts you need most:

- Nodes: `Project`, `Ingestion`, `Artifact` (`path`, `kind`, `format`, `deletedAt`), `ArtifactVersion` (`contentHash`, `sourceRevision`, `ingestedAt`), `Element` with one of the labels `Requirement`, `TextUnit`, `ModelElement`, `CodeUnit`, `TestCase` (`key`, `kind`, `name`, `text`, `startLine`, `endLine`), `TraceLink`, `Inconsistency`, `StatusChange`, `Run`.
- An element is current when `(:Artifact)-[:CURRENT_VERSION]->(:ArtifactVersion)-[:HAS_ELEMENT]->(element)`. A trace link is current when `active = true` and both ends are current elements. Use these two patterns; queries on all versions also return old and deleted data.
- Links: `(link)-[:SOURCE]->(element)`, `(link)-[:TARGET]->(element)`. Findings: `(:Inconsistency)-[:INVOLVES]->(element or link)`, `-[:HAS_STATUS_CHANGE]->(:StatusChange)`.
- Element keys have the form `art_<artifact id>#<local key>`; resolve the artifact to its path through the `Artifact` node.

Trace links with the files they connect (mini example, after `s2`):

```cypher
MATCH (l:TraceLink {projectId: $projectId, active: true})-[:SOURCE]->(s:Element), (l)-[:TARGET]->(t:Element)
MATCH (sa:Artifact {projectId: $projectId})-[:CURRENT_VERSION]->(:ArtifactVersion)-[:HAS_ELEMENT]->(s)
MATCH (ta:Artifact {projectId: $projectId})-[:CURRENT_VERSION]->(:ArtifactVersion)-[:HAS_ELEMENT]->(t)
RETURN sa.path AS source, s.name AS sourceName, ta.path AS target, t.name AS targetName,
       l.approach AS approach, l.confidence AS confidence
ORDER BY source, target
```

Versions of one file:

```cypher
MATCH (a:Artifact {projectId: $projectId, path: $path})-[:HAS_VERSION]->(v:ArtifactVersion)
RETURN v.id AS version, v.ingestedAt AS at, v.sourceRevision AS revision ORDER BY at
```
with `"params": {"path": "requirements/requirements.md"}`.

## Graph explorer (console)

The console shows the graph of one project as a picture. Open it with the `Graph` link in the row of the project on `/console/projects`, or go to `/console/projects/<project>/graph`. You need at least the role `viewer` in the project. If you have no role, the page answers `403`.

### Load and filter

The controls are foldout menus: `Load`, `Filters`, `Neighborhood`, `Path between two nodes`, `View` and `Export and share`. Only `Load` is open at the start. The Cypher box is a foldout below the graph.

1. Choose an artifact, a version state or both. The version state `current` shows the elements of current versions. The state `historic` shows the elements of older versions.
2. Enter a text in the search field. The search finds nodes by key, name or id.
3. Select labels or relationship types in the filter lists.
4. Click `Load`. The page shows up to 500 nodes. If more nodes match, the page shows `truncated`. Add a filter to see the rest.

### Explore

- Click a node to expand its neighborhood. The field `Hop depth` sets the depth from 1 to 3.
- Click a node, then `Use as path start` or `Use as path end`. Click `Find path` to show the shortest path. The field `Max hops` sets the limit from 1 to 6.
- Click a node or an edge to read its properties in the panel. Texts longer than 5000 characters end with `... [truncated]`.
- Use the mouse wheel or a pinch gesture to zoom. Drag the background to pan. The toolbar on the graph has `+`, `-`, `Fit`, `Selection`, `1:1`, `Info` and `Fullscreen`. `Controls` and `Info` hide or show the side panels.
- Use `Layout` to switch between force, hierarchical and concentric layouts.
- Use `Find in the visible graph` to mark matching nodes.
- Rejected trace links have a dashed red border. Open and reopened findings have a solid red border. Candidate trace links have a dotted border.

### Share and export

- `Copy link` copies a URL that holds the filters, the hop depth and the Cypher text. Open the URL to load the same view. The page does not run the Cypher text by itself.
- `PNG` and `JSON` export the visible graph.

### Cypher box

The box runs one read-only statement. The rules are the same as for `POST /v1/query/cypher`, see [Cypher](#cypher). The service binds `$projectId`. Anchor every node pattern with `{projectId: $projectId}`.

- A result with nodes, relationships or paths is drawn in the graph. Other results show as a table.
- A statement that the guard rejects shows the reason. It never shows an error page.
- The limits are 10 s, 100 MB and 500 rows.
- The service writes one log line `event=graph_query` per statement. It holds the user, the project and the duration. It does not hold the statement or the result.

Caution: Users without the global admin flag cannot see nodes with the labels `Run` and `Ingestion`. A statement that names these labels is refused.

## REST API

Base URL `http://127.0.0.1:8080`, header `Authorization: Bearer <token>` ([projects-and-access.md](projects-and-access.md)). Errors are `application/problem+json` with `title`, `status` and `detail`: `400` invalid request, `401` no or invalid token, `403` role too low, `404` unknown or invisible, `422` rejected content (queries, status changes), `503` a backing service is unavailable. There is no interactive documentation; the contract is `contracts/openapi/query-service.yaml` (minimum role per operation in `x-sandman-min-role`).

| Operation | Role |
|---|---|
| `GET /v1/projects` (projects you can read, with your role) | any |
| `GET /v1/projects/{id}/inconsistencies`, `GET /v1/inconsistencies/{id}` | viewer |
| `POST /v1/inconsistencies/{id}/transitions` (body member `cause`) | triager |
| `POST /v1/projects/{id}/links/{linkId}/review` | triager |
| `GET /v1/projects/{id}/workflows`, `GET /v1/workflows/{id}` | viewer |
| `POST /v1/projects/{id}/workflows` | maintainer |
| `POST /v1/projects/{id}/ingestions/check`, `POST /v1/projects/{id}/ingestions` | maintainer |
| `POST /v1/query/cypher`, `POST /v1/query/nl`, `GET /v1/schema` | viewer (schema: any) |
| `GET /v1/projects/{id}/export/tracelinks`, `.../export/inconsistencies` | viewer |
| `/v1/auth/...`, `/v1/admin/...` | own account / admin |

Health: `GET /healthz`, `GET /readyz` (no token).

## Export

```sh
uv run sandman export --project demo tracelinks > links.csv
uv run sandman export --project demo tracelinks --format json
uv run sandman export --project demo inconsistencies > findings.json
uv run sandman export --project demo inconsistencies --format csv
```

- `tracelinks`: current links only, rejected ones excluded. CSV (default) columns: `source_key,target_key,link_type,approach,approach_version,confidence,status,run_id,reviewed_by,reviewed_at,review_reason`. The review columns are empty for links without a review ([trace-links-and-findings.md](trace-links-and-findings.md#review-trace-links)). JSON uses camelCase members. The REST endpoint also accepts `approach`, `minConfidence` (0 to 1) and `includeRejected=true`.
- `inconsistencies`: all findings of the project with involved elements and status history; JSON by default. CSV has one row per finding, with the column `cause` (the cause of the latest history entry that has one) and `involved` and `history` as JSON cells.

Keys in the export are `art_<id>#<local key>`; map them to paths with the Cypher above.

## Change impact

The change impact report shows what an ingestion changed and which elements in other artifacts it affects.

```sh
uv run sandman impact --project demo ing_<id> --depth 2 --format markdown
```

- `ing_<id>` is the `ingestionId` that `sandman ingest` prints.
- `--depth` is 1 to 4 (default 2). `--format` is `json` (default) or `markdown`.
- The REST endpoint is `GET /v1/projects/{projectId}/ingestions/{ingestionId}/impact` with the parameters `depth` and `limit` (1 to 1000, default 200). The role `viewer` is enough.
- The console shows the report at `/console/projects/<id>/ingestions/<ingestionId>/impact`.

A changed element is added, modified or removed. A renamed element counts as one removed and one added element.
An affected element is in a different artifact. It is reachable from a changed element over active trace links (not rejected), `DEPENDS_ON` edges (both directions) or `CONTAINS` edges (upward only).
Each affected element has its distance and up to three shortest paths. A path of a removed element starts with `via: previous-version`.

If `complete` is `false`, the workflow of the ingestion has not ended. Links may be missing. Run the command again later.
If `truncated` is `true`, the lists reached a limit.

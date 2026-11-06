# T-kg-traverse-migration: Traverse replaces Memgraph

Status: code done (2026-11-06). End-to-end run result: see Verification.

## Context

Memgraph Community is BSL 1.1. The project prefers a store with a different licence. Truespar Traverse 0.9.1 speaks Bolt and openCypher. See the amendment of 2026-11-06 in `adr/0014-graph-database.md`. Spike checks are in `spikes/traverse/`.

## What changed

- Image: `truespar/traverse@sha256:e39b5739a525f2b955dcac0e5747039845f3a5c3cd89397aec923312d5d69965`, `platform: linux/amd64`. Bolt port 7690, HTTP port 7691.
- `contracts/kg/init.cypher`: Neo4j 5 syntax with named constraints and a vector index.
- `libs/python/sandman-agent` and `libs/java/agent-lib`: the memory-limit error is detected by its code. `init_schema` ignores "already exists".
- `services/query-service`: vector search uses `db.index.vector.queryNodes`. Element ids use `id(n)`. The path search uses `shortestPath`. The NL prompt names the dialect "openCypher".
- `services/dashboard`: node and memory numbers come from `GET /api/health` (`SANDMAN_KG_HEALTH_URL`, `SANDMAN_KG_API_KEY_FILE`, `SANDMAN_KG_MEMORY_LIMIT`).
- `deploy`: service `traverse`, sidecar `traverse-health`, secrets `traverse_password`, `traverse_api_key` and `traverse.toml` (created by `gen-secrets.sh`), volume `traverse-data`.
- `scripts/backup.sh`, `scripts/restore.sh`: `POST /api/save`, then copy `/data/traverse.tvdb`. The manifest key is `traverseImage`.
- `scripts/perf.py`: budget keys `traverse-memory` and `traverse-bytes-per-node`.
- Renames in tests, e2e tests, site and README.

## Deviations and open issues

| Item | Detail |
|---|---|
| Read-mode keyword bug | In a read transaction Traverse rejects query text with the words drop, set, delete, create, merge or remove, also inside string literals. Tests use the id `stale` instead of `drop`. Report to the vendor. |
| No per-query memory limit | The server flag `--query-memory-limit` applies. The guard still rejects `QUERY MEMORY LIMIT`. |
| Healthcheck | The image has no shell. The sidecar `traverse-health` (image `nats`, `wget` against `/api/ready`) is the healthcheck. Other services depend on it. |
| `init_schema` | `CREATE INDEX` is not idempotent. The code ignores "already exists" and does not read `SHOW INDEXES`. |
| Java driver | The Java driver needs a server agent that starts with `Neo4j/`. The flag `--server-agent=Neo4j/5.26.0` is set in compose and in the Java test containers. |
| Credentials | `traverse.toml` holds the password and the API key. It is created with owner-only mode. |
| `LOAD CSV` | Traverse does not block it. The guard still rejects it. |
| amd64 only | The image runs emulated on arm64 hosts. Speed there is lower. |
| Vector dimension | The index does not validate it. The embedding writer checks 768. |
| Replay cassettes | The prompt change did not touch any cassette. 53 NL and replay tests pass. No re-record was needed. |
| Memory-limit tests | Test containers use `--query-memory-limit` 64 MB (Python) and 32 MB (Java). |
| Insert speed | See the section below. The unique constraint slows inserts a lot. |

### Insert speed

Measured 2026-11-06 on one local container, amd64 emulation on arm64. Each case inserts nodes with six properties into a fresh label.

Effect of the batch size (20 000 nodes, `UNWIND ... CREATE`, unique constraint on `id` created before the load):

| Batch size | Time |
|---|---|
| 1 000 | 24.9 s |
| 5 000 | 25.4 s |
| 10 000 | 24.4 s |

The batch size has no effect. `MERGE` on the constrained `id` shows the same: 16.7 s for all three sizes.

Effect of the constraint position (20 000 nodes, batch 5 000):

| Case | Load time | Constraint creation |
|---|---|---|
| Constraint before the load | 25.4 s | none |
| Constraint after the load (`CREATE`) | 0.44 s | 0.02 s |
| Index only before the load | 0.37 s | none |
| `MERGE` without constraint or index | 24.5 s | 0.01 s |

The cost depends on the constraint, not on the batch. It grows faster than the node count: 5 000 nodes take 1.3 s, 20 000 nodes take 19 s. An index on the property costs almost nothing. `MERGE` needs the index on the key. Without it, `MERGE` scans the label.

Decision:

* Keep the unique constraints in the normal ingest path. They guard the correctness of ADR-0005.
* Keep the batch sizes. A larger batch gives no gain.
* Change the `make perf` filler (`seed_clones` in `scripts/perf.py`). It drops each constraint, creates an index on the property, loads the clones, and creates the constraint again. The new constraint fails on duplicate ids, so the load is still checked.
* Result of the filler on a scratch container (20 000 nodes): 2.9 s with the change, 17.0 s before. The constraints are active after the run.

Open: the per-batch cost of the normal writers stays. A writer that ingests many new nodes into one label pays the quadratic cost. Ask the Traverse vendor about it, or re-check with a newer version.

## Verification

- Python: query-service, dashboard, extraction-agent, libs and deploy tests pass against Traverse.
- Java: `mvn verify` for agent-lib, tlr-agent and inconsistency-agent (see the report of the task).
- `ruff check`, `ruff format --check` and `mypy` pass.

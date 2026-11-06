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
| Insert speed | See the table below. The unique constraint slows inserts a lot. |

### Insert speed (open issue)

5 000 nodes (`UNWIND ... CREATE`), one local container, amd64 emulation on arm64:

| Unique constraint | Batch 1000 | Batch 100 |
|---|---|---|
| no | 0.08 s | 0.20 s |
| yes | 1.55 s | 2.16 s |

The constraint costs about 20 times more at this size. Check the cost at 200 000 nodes with `make perf` before a release.

## Verification

- Python: query-service, dashboard, extraction-agent, libs and deploy tests pass against Traverse.
- Java: `mvn verify` for agent-lib, tlr-agent and inconsistency-agent (see the report of the task).
- `ruff check`, `ruff format --check` and `mypy` pass.

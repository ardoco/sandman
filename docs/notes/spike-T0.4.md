# Spike T0.4 — gateway, Memgraph, A2A interoperability

Date: 2026-10-06/07. Scripts: `spikes/T0.4/` (`memgraph_checks.py`, `gateway_checks.py`, `java-a2a/`). Detailed A2A results:
`spikes/T0.4/java-a2a/RESULT.md`.

## Results

| # | Check | Result | Evidence | Consequence |
|---|---|---|---|---|
| 1 | Bifrost v2.2.6 starts with read-only `config.json`, keys from env | PASS | governance active, 6 budgets and 2 rate limits loaded, healthy after ~10 s | – |
| 2 | Inference without key / with wrong key | PASS (401 both) | `curl` on `/openai/chat/completions` | – |
| 2 | Inference with a virtual key | PASS | resolved model `gpt-4.1-nano-2025-04-14` (`gateway-results.txt`) | – |
| 3 | OpenAI Python SDK via base URL `…/openai`: tool calling, structured output (`json_schema`), streaming, embeddings | PASS | all four; embeddings 1536 dims | V-2/A2-3 closed for Python; Java uses LangChain4j (T2.2) |
| 4 | Routes `openai/…`, `openrouter/<vendor>/<model>` | PASS | OpenRouter resolved `openai/gpt-4.1-nano`; Ollama route not run (profile `local-llm` off) | V-18/V-20 nested names closed; Ollama still open |
| 5 | Budget exhaustion, rate limit | PASS | `budget_check.sh` on a throwaway gateway: budget → **HTTP 402** `budget_exceeded` (first call still served; spending counted afterwards); rate limit → **HTTP 429** `request_limited`, no `Retry-After` header. UI bug: editing a standalone VK budget fails ("invalid reset duration format:") | Python `OpenAiLlmClient` maps 402 → `BudgetExceededError` → `SkillError BUDGET_EXCEEDED` (workflow BLOCKED); 429 stays retryable (SDK retries). Java adapters (LangChain4j) map the same in T2.2/T3.2. Change budgets via `config.json` + volume reset, not the UI |
| 6 | Request log and `traceparent` | PASS / INFO | log via management API with admin auth; **`traceparent` is not stored in the request log** | correlate LLM calls via OpenTelemetry and `user` = runId, not the gateway log |
| V-36 | Bifrost `/metrics` with admin Basic auth | PASS | 20 `bifrost_*` metrics incl. `cost_total`, `input/output_tokens_total`, `error_requests_total`, latency histograms | dashboard LLM statistics feasible (T1.9) |
| 12 | `-local` virtual key on a cloud route | PASS | HTTP 403 `provider_blocked` | gateway enforces `local-only` (R-035) |
| 18 | OpenRouter privacy setting (V-32) | **OPEN** | account setting, see `TODO.md` | – |
| 7 | Neo4j Python driver 6.4 ↔ Memgraph 3.13.1 | PASS | connects; Java driver 6.3 also passes (agent-lib integration tests) | V-6 closed |
| 7 | `execute_read` rejects writes | PASS | `Accessor type READ and query type WRITE are misaligned!` | primary write guard holds (ADR-0009) |
| 7 | Auto-commit query in a session with `READ` access mode | **FAIL** | write accepted, node created | never rely on the session access mode; read paths must use read **transactions** (libraries do) |
| 8 | `init.cypher` (25 statements) | PASS **only as auto-commit** | in explicit transactions: `Constraint manipulation is not allowed in multicommand transactions` | both libraries need an auto-commit method (`KgClient.run_autocommit`) for schema setup and `SHOW STORAGE INFO` |
| 8 | Stage 1 §4.4 statements (10) | PASS | incl. `FOREACH`-free version activation, `MERGE … ON CREATE SET`, link finalization | V-7 closed |
| 8 | `QUERY MEMORY LIMIT` | PASS | `… QUERY MEMORY LIMIT 10 MB` and `10MB` accepted; 1 MB limit aborts a large `collect` | syntax confirmed for the query-service guard |
| 8 | `EXPLAIN` with unknown label/property | INFO | accepted without error (plan returned) | `EXPLAIN` catches syntax errors only; schema checks must come from the scope guard / schema contract |
| V-38 | Path query | PASS / INFO | `[*BFS ..5]` works; `shortestPath()` is rejected by the parser | dashboard uses BFS syntax |
| 10 | `mgconsole` without server | PASS | exit code 1 | healthcheck works |
| 11 | Gateway management API, logs, `/metrics` without credentials | PASS (401) | from the host and from a container on `internal` | R-034 closed for Bifrost |
| 13 | `edge` bridge with `enable_ip_masquerade=false` | **FAIL on Docker Desktop (macOS)** | published port served (200), but the container still reaches the internet and resolves DNS | the D-4 egress control does not hold on Docker Desktop; Linux unverified. Proposal: query-service and dashboard only on `internal`, a small reverse proxy on `edge` publishes their ports (R-035 alternative) |
| 14 | Memgraph `--memory-limit` (MiB) | PASS | query aborted, server alive | **the error is a `TransientError`**: drivers retry it up to ~30 s; libraries must not retry memory-limit errors |
| 15 | Concurrent writes on one node | PASS | 8 writers → correct result, 11 automatic retries (`Cannot resolve conflicting transactions`, transient) | per-batch retry by the driver works (R-016) |
| 9 | Python ↔ Java A2A | PASS with one change | Java server requires the card's `required` extension in the `A2A-Extensions` header; added to the Python `A2aClient` | see W-1–W-4 in `java-a2a/RESULT.md` |
| 16 | Java A2A runtime (D-6) | **PASS: keep a2a-java on Quarkus 3.33.4** | custom executor with all 4 skill-invocation vectors; ArDoCo + LiSSA on the Quarkus classpath, ArDoCo MediaStore = 42 findings as in T0.12 | ADR-0002 amendment; T0.3 `agent-lib-a2a` settings below |
| 17 | Bifrost with empty provider keys | PASS | healthy, no errors | keyless walking skeleton works (R-040, V-34) |

## Consequences for implementation

- **Both libraries:** add an auto-commit `KgClient` call for schema setup and storage info; classify Memgraph "Memory limit exceeded" as non-retryable; Java `Contracts` now uses `Locale.ROOT` (messages were German on this machine).
- **Python `A2aClient`:** sends `A2A-Extensions: <skill-invocation extension>` (done).
- **Java `agent-lib-a2a` (T0.3):** `a2a.authorization.required=false` plus our bearer filter; route the card's path `/a2a` to a2a-java's `POST /` (or move the cards' interface URL to `/`); normalize integral doubles in data parts; send `A2A-Extensions` and `A2A-Version: 1.0` from the Java client.
- **Callers:** do not assume the first state after `SendMessage` (Java returns `REJECTED` directly, Python `SUBMITTED` first).
- **ArDoCo + LiSSA in one agent:** pin LangChain4j explicitly, exclude LiSSA's `slf4j-simple`, align `model-provider`; image grows by ~640 MB, ~1.7 GB RSS after two ArDoCo runs (memory budget of 5 GB per Java agent holds).
- **Deployment (decided 2026-10-07):** query-service, dashboard and Grafana moved to the internal network only; a Caddy `edge-proxy` (`deploy/proxy/Caddyfile`) publishes their ports. Verified with a throwaway project: host → proxy → internal service HTTP 200, internal service has no egress.

## Still open

- Check 18 (OpenRouter account privacy setting), Ollama route (profile `local-llm`).
- ADR-0004 (Bifrost) accepted on 2026-10-07: all confirmation criteria hold (governance and budgets in the OSS build, admin auth, `-local` keys, keyless start).

---
status: accepted
date: 2026-10-06
---

# LLM gateway: Bifrost, LiteLLM as fallback

## Context and Problem Statement

All LLM access must go through a free/open-source, self-hosted gateway so that agents never hold provider keys. The gateway must run in Docker Compose on one server. Which gateway?

## Decision Drivers

* OpenAI-compatible API (chat, tool calling, streaming, embeddings).
* Providers (Q-3): OpenAI and other OpenAI-compatible endpoints, OpenRouter, local Ollama.
* Per-component virtual keys with budgets and rate limits, available without paid license.
* Request logging, cost tracking, OpenTelemetry export.
* Routing, fallbacks, retries; local models (Ollama, vLLM).
* Low operational complexity (few dependencies) and a good security record.
* License: open source; clear which features are paywalled.

## Considered Options

Search: started with LiteLLM, expanded via web search and gateway ecosystem lists; checked license files, `enterprise/`/`ee/` folders, releases, docs and advisories (2026-10-06).

* Bifrost (Maxim, Go, Apache-2.0)
* LiteLLM proxy (Python, MIT + commercial `enterprise/`)
* agentgateway (Linux Foundation, Rust, Apache-2.0)
* LLM Gateway / llmgateway.io (TypeScript, AGPL-3.0 + `ee/`)
* Portkey OSS gateway (TypeScript, MIT)
* APISIX ai-proxy, Kong OSS ai-proxy, Agent Router (ex Envoy AI Gateway)
* Screened out: TensorZero (archived 2026-06), Helicone AI Gateway (relicensed to GPL-3.0, maintenance mode), One API (stale), new-api (billing/reseller focus), GoModel/Otari/AxonHub (< 1 year old, pre-1.0)

## Decision Outcome

Chosen option: "Bifrost", because it is a single container without mandatory database (SQLite default), Apache-2.0, OpenAI-compatible, and documents virtual keys with budgets and rate limits, a request log UI, OTel export and Ollama support in the open-source build — the lowest operational cost for the prototype. **Runner-up: LiteLLM proxy** (Docker image pinned to a patched release, Postgres for keys).

Bifrost supports OpenAI, OpenRouter (`openrouter/<model>`) and Ollama natively; LiteLLM does as well. Status is `proposed` until the M0 smoke test confirms that virtual keys/budgets are not runtime-gated (Bifrost's pricing page lists "Governance" under Enterprise, while its docs describe virtual keys and budgets as OSS).

### Consequences

* Good, because minimal footprint and a smaller attack surface than LiteLLM (17 advisories in 2026 incl. 4 critical; PyPI compromise on 2026-03-24, Docker image reportedly unaffected).
* Good, because the agent library is gateway-agnostic (standard OpenAI fields only), so switching to LiteLLM means changing URL and keys.
* Bad, because Bifrost is younger and less widely documented than LiteLLM; fewer providers.
* Bad, because the feature-gating ambiguity must be resolved empirically.

### Confirmation

Smoke test with Python and Java clients: chat, tools, streaming, embeddings, OpenAI, OpenRouter and Ollama routes, budget cut-off (status code and body), rate limit, request log, `traceparent` propagation. Switch to LiteLLM if any required item fails.

## Pros and Cons of the Options

### Bifrost

* Good, because single binary/container, SQLite default, Apache-2.0, VKs/budgets/rate limits documented as OSS, OTel plugin, Ollama.
* Bad, because governance gating ambiguous; rate limits only per virtual key (no team level); vendor performance claims unverified.

### LiteLLM proxy

* Good, because most mature and documented; broadest provider coverage; VKs, key/user/team budgets, rpm/tpm, spend logs, Langfuse/OTel callbacks in OSS.
* Bad, because Postgres required for keys; per-model budgets, SSO (> 5 users), audit logs, key rotation are Enterprise; high advisory rate in 2026.

### agentgateway

* Good, because Apache-2.0, LF-governed, also governs MCP/A2A traffic.
* Bad, because keys are static in config; local rate limits are in-memory (reset on restart) → no durable budgets without Redis rate-limit service.

### LLM Gateway (llmgateway.io)

* Good, because budgets and limits in self-host.
* Bad, because Postgres + Redis, AGPL + `ee/` split, no OTel found.

### Portkey OSS

* Good, because stateless, MIT, solid retries/fallbacks.
* Bad, because virtual keys/budgets/log UI not confirmed in OSS; last release 2026-01.

### APISIX / Kong / Agent Router

* Good, because mature general gateways (APISIX, Kong) or Kubernetes-native (Agent Router).
* Bad, because no USD budgets (APISIX), AI advanced plugins Enterprise and OSS frozen (Kong), Kubernetes required (Agent Router).

## More Information

* Configuration: routes `chat-default`, `chat-alt`, `chat-local`, `embed-default`, `embed-local`; one virtual key per component plus `experiments`; admin key never in agents (design document, Section 8.4).
* Change triggers: gated governance or missing provider → LiteLLM; MCP/A2A traffic governance needed → agentgateway.
* Related: ADR-0010.

## Amendments

* 2026-10-06, review decision D-4: admin plane secured with `governance.auth_config` (admin password from `BIFROST_ADMIN_PASSWORD`, Bifrost policy ≥ 12 chars with upper/lower/digit/special); the admin password never enters agent containers. Per LLM-using component a second virtual key `<component>-local` restricted to the Ollama provider, so the `local-only` policy is enforced by the gateway. No cross-provider fallbacks. Ollama sits on a dedicated internal network shared only with the gateway. OpenRouter is used only for `cloud-allowed` projects, with the account-level privacy setting that excludes providers which train on data (to verify, V-32). Gateway request logs are retained 30 days (mechanism to verify, V-15).
* Additional confirmation criteria (T0.4): management API rejects unauthenticated calls from an agent container; a `-local` key cannot reach OpenAI/OpenRouter routes. If admin authentication cannot be enforced, switch to LiteLLM (master key protects admin routes).
* 2026-10-06, review decision D-3: all callers reach the gateway through the `llm-replay` proxy (ADR-0010 amendment).
* 2026-10-07, accepted after spike T0.4 (`docs/notes/spike-T0.4.md`): virtual keys, budgets (HTTP 402 `budget_exceeded`) and rate limits (HTTP 429 `request_limited`) work in the open-source build; admin plane and `/metrics` require the admin login; `-local` keys are refused on cloud routes (403); Bifrost starts without provider keys. Budgets are changed in `config.json` (the UI fails to edit standalone budgets in v2.2.6). The request log does not keep `traceparent`.
* 2026-10-07, egress (spike T0.4 check 13): disabling IP masquerading on `edge` does not block egress on Docker Desktop. Web UIs (query-service, dashboard, Grafana) now live only on `internal`, and a Caddy `edge-proxy` publishes their ports on `127.0.0.1`; only the gateway (and Ollama for model pulls) has egress.
* 2026-10-08, self-hosted models: no Ollama runs in this deployment (compose service, `local-llm` profile, `llm-local` network and `ollama-models` volume removed). Routes `chat-local`/`embed-local` and the `local-only` policy use an Ollama instance on another server, exposed as an OpenAI-compatible endpoint behind an HTTPS reverse proxy. Gateway: provider name stays `ollama` (model ids `ollama/<model>:<tag>` unchanged); `ollama_key_config.url` = `env.SELFHOSTED_LLM_URL`, key value = `env.SELFHOSTED_LLM_KEY` (bearer for the proxy), both in `deploy/secrets/gateway.env` (`deploy/gateway/config.json`, `deploy/gen-secrets.sh`). The `-local` virtual keys stay restricted to provider `ollama`. Trust implication: `local-only` now means "self-hosted Ollama on a trusted server the operator controls"; prompts leave this host, so the endpoint must use HTTPS and should require a key.

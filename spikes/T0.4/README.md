# T0.4 spike scripts

Report: `docs/notes/spike-T0.4.md` (written when all checks are done).

| Script | Checks | Needs |
|---|---|---|
| `memgraph_checks.py` | 7, 8, 10, 14, 15, V-38 | Docker (throwaway Memgraph 3.13.1, no secrets); last output in `memgraph-results.txt` |
| `gateway_checks.py` | 2, 3, 4, 6, 12, V-36 | running `llm-gateway` and read access to `deploy/secrets/llm_key_query*`, `gateway_admin_password` |
| `custom_provider_check.py` | V-42 | Docker (throwaway Bifrost, stdlib stub server, no secrets) |
| `java-a2a/` | 9, 16 | JDK 21, Maven, ArDoCo bootstrap (spike T0.12) |

Run from the repository root:

```sh
DOCKER_HOST=unix://$HOME/.docker/run/docker.sock uv run python spikes/T0.4/memgraph_checks.py
docker compose -f deploy/docker-compose.yml up -d llm-gateway
uv run python spikes/T0.4/gateway_checks.py        # optional: CHAT_MODEL=..., OLLAMA_MODEL=...
```

Manual check 5 (budget exhaustion, rate limit): in the gateway UI (http://127.0.0.1:8081, user `admin`,
password in `deploy/secrets/gateway_admin_password`) set the budget of `vk-query` to 0.0001 USD, run
`gateway_checks.py` again and note the HTTP status and error body of the failing call; reset the budget.
For the rate limit, lower `rl-default` to 2 requests/minute and send 3 requests.

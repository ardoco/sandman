# Getting started

Goal: a running stack, a logged-in admin account, a project, and an API token.

## Requirements

- Docker with Compose v2 (`docker compose`), running. The containers' memory limits assume a host with 32 GB RAM (`deploy/docker-compose.yml`); Memgraph alone is limited to 10 GB.
- `openssl` (used by `make secrets`).
- [uv](https://docs.astral.sh/uv/) with Python 3.13, only for the `sandman` CLI. Run `uv sync` once in the checkout.
- JDK and Maven are not needed to run the stack; the Java images are built inside Docker.

## 1. Generate secrets

```sh
make secrets
```

This creates `deploy/secrets/*` (service tokens, Memgraph password, session secret, initial admin password, LLM gateway keys) and `data/` directories. It never overwrites existing files, so it is safe to run again. `deploy/secrets/` is git-ignored; keep it private.

## 2. Provider keys (optional today)

Open `deploy/secrets/gateway.env`. It contains empty entries for:

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY`, `OPENROUTER_API_KEY` | cloud LLM providers behind the LLM gateway |
| `SELFHOSTED_LLM_URL`, `SELFHOSTED_LLM_KEY` | remote self-hosted model server (Ollama behind an HTTPS reverse proxy) used for the `local-only` policy |

No built-in component calls an LLM today, so the stack starts and works with all four empty. Fill them in before you enable features that use models.

SANDMAN does not run a model server itself. If you set `SELFHOSTED_LLM_URL`, then:

- it must start with `https://`, and
- `SELFHOSTED_LLM_KEY` (the bearer token of the reverse proxy) must not be empty.

`deploy/check-env.sh` enforces this and `make up`, `make infra` and `make e2e` run it first. A violation stops the command with one of these messages:

```
check-env: SELFHOSTED_LLM_URL must start with https:// (prompts would leave this host in cleartext)
check-env: SELFHOSTED_LLM_KEY must not be empty when SELFHOSTED_LLM_URL is set
check-env: <path>/deploy/secrets/gateway.env missing; run 'make secrets'
```

## 3. Start the stack

```sh
make up
```

The first start builds all images and takes several minutes. Then check readiness:

```sh
curl http://127.0.0.1:8080/readyz
# {"status":"ok","checks":{"memgraph":"ok","userstore":"ok"}}
docker compose -f deploy/docker-compose.yml ps
```

Plugin stubs. The shipped configuration (`config/extraction/plugins.yaml`, `config/inconsistency/plugins.yaml`) routes code and test files to a `code-extractor` plugin and adds a `detect-stub` detector, both provided by the `plugin-stubs` container of the `stubs` profile. `make up` does not start that profile. Without it, those plugin calls fail: code and test files then get file-level elements only, and the affected runs end as `partial` or `failed` (the workflow itself still shows `SUCCEEDED`; see [workflows.md](workflows.md)). To run the full demo pipeline, including the mini example used in these pages, start with the profile:

```sh
docker compose -f deploy/docker-compose.yml --profile stubs up -d --build
```

## 4. First login

Open `http://127.0.0.1:8080/console/login`.

- User: `admin`
- Initial password: the content of `deploy/secrets/admin_bootstrap_password`

The admin account is created only when the user database is empty, so changing that file later has no effect on an existing installation.

After the first login the console forces a password change: at least 12 characters, different from the current one. Changing the password signs you out; log in again with the new one. Failed logins are limited to 5 per user name or client address within 15 minutes (then HTTP 429). Console sessions last at most 12 hours and expire after 2 hours without activity.

## 5. Create a token and a project

1. In the console open **Tokens**, enter a name, press **Create**, and copy the secret (`sdm_...`). It is shown once.
2. Create a project. There is no console page for this; an admin token calls the API:

```sh
export SANDMAN_URL=http://127.0.0.1:8080
export SANDMAN_TOKEN=sdm_...
curl -X PUT "$SANDMAN_URL/v1/admin/projects/demo" \
  -H "Authorization: Bearer $SANDMAN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Demo","llmPolicy":"local-only"}'
```

`llmPolicy` is `cloud-allowed` or `local-only`. The call is idempotent: repeating it updates the name and policy.

3. Ingest the mini example (a checkout of this repository, see [ingesting-artifacts.md](ingesting-artifacts.md)):

```sh
uv run sandman ingest --project demo --mode full --revision s1 tests/e2e/fixtures/mini/s1
uv run sandman workflows --project demo
```

Then open `http://127.0.0.1:8080/console/projects` and follow the `demo` link. Next: [projects-and-access.md](projects-and-access.md) for more users and roles.

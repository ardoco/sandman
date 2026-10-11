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
| `SELFHOSTED_LLM_URL`, `SELFHOSTED_LLM_KEY` | self-hosted model server used for the `local-only` policy (routes `chat-local`, `embed-local`) |

Cloud routes (`chat-default`, `chat-alt`, `embed-default` in `config/llm/models.yaml`) need `OPENROUTER_API_KEY` or `OPENAI_API_KEY`. Without them only `local-only` projects can use LLM features.

`make secrets` sets `SELFHOSTED_LLM_URL=http://ollama:11434`. Choose one of two modes for the self-hosted models:

**A. Local container (default).** Start the Ollama container and pull the two models (`qwen3:4b` for chat, `nomic-embed-text` for embeddings, about 3 GB), then start the stack:

```sh
make models
make up
```

The container is CPU-only, limited to 4 GB, and reachable only by the gateway. The `local-llm` profile pulls `qwen3:4b`, which needs about 12 GiB of Docker memory. All recorded cassettes use this model, so do not change `chat-local`.

**B. Remote Open WebUI.** If Open WebUI runs in front of Ollama on another machine, create an API key there (Settings, Account, API Keys; an admin must have enabled API keys) and set in `gateway.env`:

```
SELFHOSTED_LLM_URL=https://<open-webui-host>/ollama
SELFHOSTED_LLM_KEY=<the Open WebUI API key>
```

The value is exactly `SELFHOSTED_LLM_URL=https://<your-open-webui-host>/ollama`. Do not use the `/api` base of its OpenAI-compatible API: the gateway appends `/v1/chat/completions` and `/v1/embeddings` to the URL, and Open WebUI serves those paths only below `/ollama` (its `/api` API has `/api/chat/completions` without `/v1`). Models served from other backends of Open WebUI (OpenAI connections) are not reachable this way; `chat-local` and `embed-local` must be models of its Ollama.

The gateway sends the key as bearer token to `<url>/v1/chat/completions` and `<url>/v1/embeddings`, which Open WebUI serves under `/ollama`. The models used by `models.yaml` must exist on that Ollama.

`deploy/check-env.sh` enforces: any URL other than exactly `http://ollama:11434` must start with `https://`, and `SELFHOSTED_LLM_KEY` must then not be empty. `make up`, `make infra` and `make e2e` run it first. A violation stops the command with one of these messages:

```
check-env: SELFHOSTED_LLM_URL must start with https:// (prompts would leave this host in cleartext), or be exactly http://ollama:11434
check-env: SELFHOSTED_LLM_KEY must not be empty when SELFHOSTED_LLM_URL is set
check-env: <path>/deploy/secrets/gateway.env missing; run 'make secrets'
```

## 3. CoreNLP service (ArDoCo approach)

The approach `ardoco` needs a service that splits text into sentences and words (Stanford CoreNLP).
The tlr-agent does not run this tool itself.
It sends the text to the container `corenlp-gateway`.
The gateway forwards the text to one service.
You choose the service with `SANDMAN_CORENLP_MODE`.

| Mode | Service | What you must do |
|---|---|---|
| `remote` (default) | the KIT instance `https://vdl-ws.sdq.kastel.kit.edu` | write the user name and the password into `deploy/secrets/corenlp_credentials` |
| `local` | the container `corenlp-provider` | nothing; `make secrets` generates the credentials |

Fill in the KIT credentials as follows.

1. Run `make secrets`. It creates `deploy/secrets/corenlp_credentials` with the placeholder `change-me:change-me`.
2. Open the file `deploy/secrets/corenlp_credentials`.
3. Replace the content with one line: `user:password`. Use the credentials that the KIT operator gave you.
4. Save the file. Do not add a second line.
5. Run `make up`.

`make secrets` never overwrites the file. Only the container `corenlp-gateway` can read it.
The agents get no KIT credentials. They get a separate token, which `make secrets` generates.

To work without KIT credentials, use the local container.

1. Create the file `deploy/.env` from `deploy/.env.example`, if it does not exist.
2. Set `SANDMAN_CORENLP_MODE=local` in `deploy/.env`.
3. Run `make up`. The command starts the container `corenlp-provider` (image of about 1.6 GB, limit 4 GB of memory).

`make e2e` always uses the local container.
No mode switches by itself: if the KIT instance is down in mode `remote`, the run of `ardoco` fails and the workflow step is retried.
Change the mode by hand, then.

`deploy/check-env.sh` runs before `make up`. In mode `remote`, it stops with one of these messages:

```
check-env: <path>/deploy/secrets/corenlp_credentials missing; run 'make secrets' and put user:password of the CoreNLP service into it
check-env: <path>/deploy/secrets/corenlp_credentials must hold one line user:password (or set SANDMAN_CORENLP_MODE=local)
check-env: <path>/deploy/secrets/corenlp_credentials still holds the placeholder; put the user:password of the CoreNLP service into it (or set SANDMAN_CORENLP_MODE=local)
check-env: SANDMAN_CORENLP_MODE must be remote or local, got '<value>'
check-env: SANDMAN_CORENLP_URL must start with https:// (credentials would leave this host in cleartext), or be exactly http://corenlp-provider:8080
```

If the log of the tlr-agent shows `CoreNLP credentials rejected`, the service refused the credentials or the token of the agent.
Check `deploy/secrets/corenlp_credentials` first.

## 4. Start the stack

```sh
make up
```

The first start builds all images and takes several minutes. Then check readiness:

```sh
curl http://127.0.0.1:8080/readyz
# {"status":"ok","checks":{"memgraph":"ok","userstore":"ok"}}
docker compose -f deploy/docker-compose.yml ps
```

Plugins. The shipped configuration routes code and test files (`config/extraction/plugins.yaml`) to the `code-extractor` plugin. The container `code-extractor-ardoco` provides it and `make up` starts it. It reads Java and shell files with the code extractors of ArDoCo. If it is down, code and test files get file-level elements only and the run ends as `partial`. The approach `ardocode` of the TLR agent also calls it, with its own token, to get the ArDoCo code model. If it is down, the run of `ardocode` fails and the workflow step is retried. The shipped configuration also adds a `detect-stub` detector (`config/inconsistency/plugins.yaml`). The `plugin-stubs` container of the `stubs` profile provides it, and `make up` does not start that profile. Without it, the detector call fails and the affected run ends as `partial` or `failed` (the workflow itself still shows `SUCCEEDED`; see [workflows.md](workflows.md)). To run the full demo pipeline, including the mini example used in these pages, start with the profile:

```sh
docker compose -f deploy/docker-compose.yml --profile stubs up -d --build
```

## 5. First login

Open `http://127.0.0.1:8080/console/login`.

- User: `admin`
- Initial password: the content of `deploy/secrets/admin_bootstrap_password`

The admin account is created only when the user database is empty, so changing that file later has no effect on an existing installation.

After the first login the console forces a password change: at least 12 characters, different from the current one. Changing the password signs you out; log in again with the new one. Failed logins are limited to 5 per user name or client address within 15 minutes (then HTTP 429). Console sessions last at most 12 hours and expire after 2 hours without activity.

## 6. Create a token and a project

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

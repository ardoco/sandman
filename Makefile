# Developer entry points. Python: uv only; Java: Maven only.
# CoreNLP mode (T2.5): remote (KIT instance, deploy/secrets/corenlp_credentials) or local (container, profile corenlp-local).
# Command line > environment > deploy/.env > remote. e2e, replay-twice and record-cassettes use local unless the command line says otherwise (no credentials in CI).
SANDMAN_CORENLP_MODE ?= $(or $(shell sed -n 's/^SANDMAN_CORENLP_MODE=//p' deploy/.env 2>/dev/null | tail -n 1),remote)
export SANDMAN_CORENLP_MODE
e2e replay-twice record-cassettes: SANDMAN_CORENLP_MODE = local
COMPOSE     = docker compose -f deploy/docker-compose.yml $(if $(filter local,$(SANDMAN_CORENLP_MODE)),--profile corenlp-local)
COMPOSE_DEV = $(COMPOSE) -f deploy/docker-compose.dev.yml
FRAMEWORKS  := langchain|langgraph|deepagents|llama_index|crewai|autogen

.PHONY: secrets infra up models down logs images test test-py test-java lint guard contracts e2e record-cassettes replay-twice backup site

secrets:            ## generate deploy/secrets/* (never overwrites)
	deploy/gen-secrets.sh

infra:              ## memgraph, nats, llm-gateway with dev ports on localhost
	deploy/check-env.sh
	$(COMPOSE_DEV) up -d memgraph nats llm-gateway

up:                 ## whole system
	deploy/check-env.sh
	$(COMPOSE) up -d --build

models:             ## start the local Ollama container (profile local-llm) and pull the ollama/* models of config/llm/models.yaml, restart the gateway (it caches the model list at start)
	$(COMPOSE) --profile local-llm up -d --wait ollama
	@for m in $$(sed -n 's|^ *[a-z-]*: *ollama/\([^ ]*\).*|\1|p' config/llm/models.yaml); do \
		$(COMPOSE) --profile local-llm exec -T ollama ollama pull $$m || exit 1; done
	$(COMPOSE) --profile local-llm restart llm-gateway

images: ## build all M0 images
	$(COMPOSE) --profile stubs build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

test: test-py test-java

test-py:
	uv run pytest

test-java:
	mvn -B verify

lint: guard        ## Python lint/format/type checks (Java: Spotless runs in mvn verify)
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy libs/python

guard:              ## no agent frameworks in Python code (NFR-3)
	@! grep -rEn --include='*.py' '^\s*(import|from)\s+($(FRAMEWORKS))\b' libs services || { echo "agent framework import found"; exit 1; }

contracts:
	uv run python scripts/validate_contracts.py

REPLAY_ENV  := SANDMAN_LOG_FILE=/logs/sandman.jsonl SANDMAN_LLM_RECORDS_HOST_DIR=$(CURDIR)/tests/cassettes SANDMAN_EXPERIMENT=walking-skeleton
DETERMINISM := uv run pytest tests/e2e/test_replay_determinism.py -q
STACK_UP    = $(COMPOSE_DEV) --profile stubs --profile dashboard up -d --build --wait --wait-timeout 300
STACK_DOWN  = $(COMPOSE_DEV) --profile stubs --profile dashboard down -v

e2e: secrets        ## walking skeleton, dashboard and replay determinism: stack in strict replay mode from tests/cassettes
	deploy/check-env.sh
	$(STACK_DOWN)
	$(REPLAY_ENV) SANDMAN_LLM_MODE=replay $(STACK_UP)
	SANDMAN_E2E=1 uv run pytest tests/e2e/test_walking_skeleton.py -q
	SANDMAN_E2E=1 SANDMAN_LLM_MODE=replay uv run pytest tests/e2e/test_dashboard.py -q
	SANDMAN_E2E=1 uv run pytest tests/e2e/test_kg_migrate.py -q
	SANDMAN_E2E=1 $(DETERMINISM)

record-cassettes: secrets  ## record tests/cassettes/walking-skeleton (same test sequence as e2e, so replay has no misses) against the remote Ollama (SELFHOSTED_LLM_URL) and regenerate the golden KG export
	deploy/check-env.sh
	@url=$$(sed -n 's/^SELFHOSTED_LLM_URL=//p' deploy/secrets/gateway.env | tail -n 1); \
		[ -n "$$url" ] && [ "$$url" != "http://ollama:11434" ] || { echo "record-cassettes: set SELFHOSTED_LLM_URL/KEY in deploy/secrets/gateway.env to the remote Ollama endpoint"; exit 1; }
	$(STACK_DOWN)
	rm -f tests/cassettes/walking-skeleton/cassette.jsonl
	@rc=0; \
		$(REPLAY_ENV) SANDMAN_LLM_MODE=record $(STACK_UP) && \
		SANDMAN_E2E=1 uv run pytest tests/e2e/test_walking_skeleton.py -q && \
		SANDMAN_E2E=1 SANDMAN_LLM_MODE=record uv run pytest tests/e2e/test_dashboard.py -q && \
		SANDMAN_E2E=1 SANDMAN_UPDATE_GOLDEN=1 $(DETERMINISM) || rc=$$?; \
		$(STACK_DOWN); \
		exit $$rc

replay-twice: secrets  ## two fresh replay runs must export byte-identical canonical KGs
	deploy/check-env.sh
	@mkdir -p $(CURDIR)/.work; \
	rc=0; \
	for n in 1 2; do \
		$(STACK_DOWN) && \
		$(REPLAY_ENV) SANDMAN_LLM_MODE=replay $(STACK_UP) && \
		SANDMAN_E2E=1 $(DETERMINISM) && \
		uv run python scripts/kg_canonical_export.py --project replay-mini --out $(CURDIR)/.work/replay-$$n.json || { rc=$$?; break; }; \
	done; \
	$(STACK_DOWN); \
	[ $$rc -eq 0 ] || exit $$rc; \
	cmp .work/replay-1.json .work/replay-2.json && echo "replay-twice: identical"

backup:
	@echo "not implemented (T5.3)"; exit 1

site:               ## build the component site into site/dist (open index.html via a local web server)
	uv run python scripts/build_site.py site/dist

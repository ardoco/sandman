# Developer entry points. Python: uv only; Java: Maven only.
COMPOSE     := docker compose -f deploy/docker-compose.yml
COMPOSE_DEV := $(COMPOSE) -f deploy/docker-compose.dev.yml
FRAMEWORKS  := langchain|langgraph|deepagents|llama_index|crewai|autogen

.PHONY: secrets infra up models down logs images test test-py test-java lint guard contracts e2e backup

secrets:            ## generate deploy/secrets/* (never overwrites)
	deploy/gen-secrets.sh

infra:              ## memgraph, nats, llm-gateway with dev ports on localhost
	deploy/check-env.sh
	$(COMPOSE_DEV) up -d memgraph nats llm-gateway

up:                 ## whole system
	deploy/check-env.sh
	$(COMPOSE) up -d --build

models:             ## start the local Ollama container (profile local-llm) and pull the ollama/* models of config/llm/models.yaml
	$(COMPOSE) --profile local-llm up -d --wait ollama
	@for m in $$(sed -n 's|^ *[a-z-]*: *ollama/\([^ ]*\).*|\1|p' config/llm/models.yaml); do \
		$(COMPOSE) --profile local-llm exec -T ollama ollama pull $$m || exit 1; done

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

e2e: secrets        ## walking skeleton and dashboard: stack with plugin stubs and dashboard up, then the e2e tests
	deploy/check-env.sh
	$(COMPOSE) --profile stubs --profile dashboard up -d --build --wait --wait-timeout 300
	SANDMAN_E2E=1 uv run pytest tests/e2e/test_walking_skeleton.py -q
	SANDMAN_E2E=1 uv run pytest tests/e2e/test_dashboard.py -q

backup:
	@echo "not implemented (T5.3)"; exit 1

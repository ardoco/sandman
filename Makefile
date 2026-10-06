# Developer entry points. Python: uv only; Java: Maven only.
COMPOSE     := docker compose -f deploy/docker-compose.yml
COMPOSE_DEV := $(COMPOSE) -f deploy/docker-compose.dev.yml
FRAMEWORKS  := langchain|langgraph|deepagents|llama_index|crewai|autogen

.PHONY: secrets infra up down logs test test-py test-java lint guard contracts e2e backup

secrets:            ## generate deploy/secrets/* (never overwrites)
	deploy/gen-secrets.sh

infra:              ## memgraph, nats, llm-gateway with dev ports on localhost
	$(COMPOSE_DEV) up -d memgraph nats llm-gateway

up:                 ## whole system
	$(COMPOSE) up -d --build

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

e2e:
	@echo "not implemented (T0.11)"; exit 1

backup:
	@echo "not implemented (T5.3)"; exit 1

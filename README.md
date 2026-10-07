# sandman
Agent-based system for Software Architecture kNowledge Description and MANagement

## Development

Requirements: [uv](https://docs.astral.sh/uv/) (Python 3.13), JDK 21+ with Maven 3.9+, Docker with Compose.
Python parts are managed with uv only, Java parts are built with Maven only. Java naming is fixed: Maven groupId `io.github.ardoco` (artifactIds prefixed `sandman-`, e.g. `sandman-agent-lib`), Java packages start with `edu.kit.kastel.mcse.ardoco` (SANDMAN code under `edu.kit.kastel.mcse.ardoco.sandman`).

```sh
uv sync            # Python workspace (libs/python, services, tools/cli, tests/plugin-stubs)
make test          # uv run pytest + mvn -B verify
```

| Target | What it does |
|---|---|
| `make secrets` | Generates `deploy/secrets/*` (never overwrites); add provider keys to `deploy/secrets/gateway.env` |
| `make infra` | Starts Memgraph, NATS and the LLM gateway with dev ports on localhost |
| `make up` / `make down` / `make logs` | Whole system via Docker Compose |
| `make test` | `make test-py` (`uv run pytest`) and `make test-java` (`mvn -B verify`, incl. Spotless and the enforcer) |
| `make lint` | Framework guard, `ruff check`, `ruff format --check`, `mypy libs/python` |
| `make guard` | Fails if Python code under `libs/` or `services/` imports an agent framework |
| `make contracts` | Validates `contracts/` (schemas, conformance vectors, Agent Cards, OpenAPI, KG schema) |
| `make e2e`, `make backup` | Placeholders (T0.11, T5.3) |

Design: [docs/](docs/) (stage 1–3, task specs), decisions: [adr/](adr/README.md), review: [REVIEW.md](REVIEW.md).

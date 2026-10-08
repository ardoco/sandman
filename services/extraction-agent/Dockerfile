FROM python:3.13-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
COPY libs/python libs/python
COPY services services
COPY tools tools
COPY tests/plugin-stubs tests/plugin-stubs
RUN uv sync --frozen --no-dev --no-editable --package sandman-extraction-agent
# Docling models are fetched here at pinned revisions and sha256 (see prefetch.py), the runtime stage has no egress.
# Licenses (V-13): THIRD-PARTY.md. The prefetch converts a bundled PDF offline with an empty HF cache at the end,
# so a build with missing or altered models fails.
RUN SANDMAN_MODELS_DIR=/models .venv/bin/python -m sandman_extraction.prefetch

FROM python:3.13-slim-bookworm
RUN useradd --uid 10001 --create-home sandman && install -d -o sandman /artifacts
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build --chown=sandman /models /home/sandman/.cache/sandman-models
COPY contracts /contracts
ENV PATH=/app/.venv/bin:$PATH SANDMAN_CONTRACTS_DIR=/contracts PYTHONUNBUFFERED=1 \
    SANDMAN_MODELS_DIR=/home/sandman/.cache/sandman-models HF_HUB_OFFLINE=1
USER sandman
EXPOSE 8000
CMD ["uvicorn", "--factory", "sandman_extraction.app:create_app", "--host", "0.0.0.0", "--port", "8000"]

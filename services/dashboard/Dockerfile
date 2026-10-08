FROM python:3.13-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
COPY libs/python libs/python
COPY services services
COPY tools tools
COPY tests/plugin-stubs tests/plugin-stubs
RUN uv sync --frozen --no-dev --no-editable --package sandman-dashboard

FROM python:3.13-slim-bookworm
RUN useradd --uid 10001 --create-home sandman && install -d -o sandman /data
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY contracts /contracts
ENV PATH=/app/.venv/bin:$PATH SANDMAN_CONTRACTS_DIR=/contracts PYTHONUNBUFFERED=1
USER sandman
EXPOSE 8000
CMD ["python", "-m", "sandman_dashboard"]

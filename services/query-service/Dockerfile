FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641 AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
COPY libs/python libs/python
COPY services services
COPY tools tools
COPY tests/plugin-stubs tests/plugin-stubs
RUN uv sync --frozen --no-dev --no-editable --package sandman-query-service

FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641
RUN useradd --uid 10001 --create-home sandman && install -d -o sandman /artifacts /data
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY contracts /contracts
ENV PATH=/app/.venv/bin:$PATH SANDMAN_CONTRACTS_DIR=/contracts PYTHONUNBUFFERED=1
USER sandman
EXPOSE 8000
CMD ["python", "-m", "sandman_query"]

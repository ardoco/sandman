"""Run both plugin stubs in one process: extract (A2A) on 8000, detect (HTTP) on 8001."""

import asyncio

import uvicorn
from sandman_agent import ConfigError, Settings

from sandman_plugin_stubs.app import create_detect_app, create_extract_app


async def main() -> None:
    settings = Settings.from_env()
    apps = [(create_extract_app(settings), 8000), (create_detect_app(settings), 8001)]
    await asyncio.gather(*(uvicorn.Server(uvicorn.Config(a, host="0.0.0.0", port=p)).serve() for a, p in apps))


try:
    asyncio.run(main())
except ConfigError as e:
    raise SystemExit(f"plugin-stubs: {e}") from e

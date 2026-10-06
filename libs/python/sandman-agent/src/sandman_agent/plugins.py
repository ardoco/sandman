"""Client for extraction plugins via A2A (sandman.plugin.extract); HTTP variant cut until needed (D-7)."""

import asyncio
import time
from collections.abc import Mapping
from typing import Any

import httpx

from sandman_agent.a2a import A2aClient
from sandman_agent.contracts import Contracts

EXTRACT_SKILL = "sandman.plugin.extract"


class PluginError(Exception):
    """Plugin call failed; retryable tells whether the owner should retry later."""

    def __init__(self, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class A2aPluginClient:
    """Calls one plugin with its inbound token and returns validated PluginExtractOutput."""

    def __init__(
        self,
        url: str,
        inbound_token: str,
        contracts: Contracts,
        timeout: float = 600.0,
        poll: tuple[float, float] = (0.5, 10.0),
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url, self._contracts, self._timeout, self._poll = url, contracts, timeout, poll
        self._client = A2aClient(inbound_token, transport=transport)

    async def close(self) -> None:
        """Close connections."""
        await self._client.close()

    async def extract(self, input: Mapping[str, Any]) -> dict[str, Any]:
        """Run sandman.plugin.extract and wait for its result (CancelTask on timeout)."""
        self._contracts.validate("skills.schema.json#/$defs/PluginExtractInput", dict(input))
        try:
            task = await self._client.submit(self._url, EXTRACT_SKILL, input)
            deadline, delay = time.monotonic() + self._timeout, self._poll[0]
            while not task.terminal:
                if time.monotonic() > deadline:
                    await self._client.cancel(self._url, task.task_id)
                    raise PluginError(f"plugin {self._url} timed out", retryable=True)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._poll[1])
                task = await self._client.get(self._url, task.task_id)
        except httpx.HTTPError as e:
            raise PluginError(f"plugin {self._url} unreachable: {e}", retryable=True) from e
        if task.state != "TASK_STATE_COMPLETED" or task.result is None:
            error = task.error or {}
            raise PluginError(f"plugin {self._url}: {task.state} {error}", bool(error.get("retryable", False)))
        self._contracts.validate("skills.schema.json#/$defs/PluginExtractOutput", task.result)
        return task.result

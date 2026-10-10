"""Forwarding of owner operations with the query-service's own token and the actor identity."""

from collections.abc import AsyncIterable, Mapping
from typing import Any

import httpx
from fastapi.responses import JSONResponse, Response

from sandman_query.auth import Principal


def problem(status: int, title: str, detail: str | None = None, errors: list[str] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"type": "about:blank", "title": title, "status": status}
    if detail:
        body["detail"] = detail
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status, media_type="application/problem+json")


class Owners:
    """HTTP clients of extraction-agent, inconsistency-agent and orchestrator."""

    def __init__(self, urls: Mapping[str, str], token: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._urls, self._token, self._transport = urls, token, transport
        self._clients: dict[str, httpx.AsyncClient] = {}

    def _client(self, owner: str) -> httpx.AsyncClient:
        if owner not in self._clients:
            self._clients[owner] = httpx.AsyncClient(
                base_url=self._urls.get(owner, "http://unconfigured.invalid"), transport=self._transport, timeout=600
            )
        return self._clients[owner]

    async def close(self) -> None:
        for c in self._clients.values():
            await c.aclose()

    async def call(
        self,
        owner: str,
        method: str,
        path: str,
        who: Principal,
        project: str | None,
        *,
        params: Mapping[str, str] | list[tuple[str, str]] | None = None,
        body: bytes | AsyncIterable[bytes] | None = None,
        headers: Mapping[str, str] | None = None,
        files: Any = None,
    ) -> httpx.Response:
        """Only the headers built here are sent; nothing from the user's request is copied (X-Sandman-Actor)."""
        out = {"authorization": f"Bearer {self._token}", **(headers or {})}
        if who.actor:
            out["x-sandman-actor"] = who.actor
        client = self._client(owner)
        request = client.build_request(method, path, params=params, content=body, files=files, headers=out)
        return await client.send(request)


def relay(resp: httpx.Response) -> Response:
    """Pass owner status and body through; an owner rejecting our token is our problem, not the user's."""
    if resp.status_code == 401:
        return problem(503, "owner rejected the query-service token")
    return Response(resp.content, resp.status_code, media_type=resp.headers.get("content-type", "application/json"))

"""Service-token verification and trusted forwarders (ADR-0013 amendment)."""

import hashlib
import hmac
from collections.abc import Awaitable, Callable, Mapping

from starlette.exceptions import HTTPException
from starlette.requests import Request

QUERY_SERVICE = "query-service"
ORCHESTRATOR = "orchestrator"
PLUGIN_PREFIX = "plugin:"


class TokenVerifier:
    """Maps a presented bearer token to its caller name via sha256 hashes."""

    def __init__(self, hashes: Mapping[str, str]) -> None:
        self._hashes = dict(hashes)

    def caller(self, token: str | None) -> str | None:
        """Caller name or None."""
        if not token:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        for name, expected in self._hashes.items():
            if hmac.compare_digest(digest, expected):
                return name
        return None

    def caller_from_header(self, authorization: str | None) -> str | None:
        """Caller name from an 'Authorization: Bearer ...' header."""
        if not authorization or not authorization.lower().startswith("bearer "):
            return None
        return self.caller(authorization[7:].strip())


def require_service(verifier: TokenVerifier, *, allow_plugins: bool = False) -> Callable[[Request], Awaitable[str]]:
    """FastAPI/Starlette dependency returning the caller; 401 unknown token, 403 plugin callers."""

    async def dependency(request: Request) -> str:
        caller = verifier.caller_from_header(request.headers.get("authorization"))
        if caller is None:
            raise HTTPException(401, "missing or invalid service token")
        if caller.startswith(PLUGIN_PREFIX) and not allow_plugins:
            raise HTTPException(403, "plugin callers are not allowed here")
        return caller

    return dependency


def trusted_actor(caller: str, headers: Mapping[str, str]) -> str | None:
    """X-Sandman-Actor if sent by the query-service; 403 if another caller sends it."""
    actor = headers.get("x-sandman-actor")
    if actor is None:
        return None
    if caller != QUERY_SERVICE:
        raise HTTPException(403, "X-Sandman-Actor is accepted only from the query-service")
    return actor


def actor_from_metadata(caller: str, metadata: Mapping[str, object]) -> str | None:
    """A2A sandman.actor, accepted only from the orchestrator; PermissionError if another caller sends it."""
    actor = metadata.get("sandman.actor")
    if actor is None:
        return None
    if caller != ORCHESTRATOR:
        raise PermissionError("sandman.actor is accepted only from the orchestrator")
    return actor if isinstance(actor, str) else None

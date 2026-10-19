"""Shared state and operations of the REST routes and the console."""

import asyncio
import json
import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from fastapi import Path as PathParam
from fastapi import Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sandman_agent import Settings, StatusTracker, TokenVerifier
from sandman_agent.auth import PLUGIN_PREFIX
from sandman_agent.contracts import Contracts
from starlette.exceptions import HTTPException

from sandman_query import __version__
from sandman_query.auth import (
    LEVEL,
    Principal,
    RateLimiter,
    authorize,
    load_min_roles,
    load_plugin_grants,
    plugin_principal,
)
from sandman_query.forward import Owners, problem, relay
from sandman_query.nl import NlService
from sandman_query.query import Intents, Runner
from sandman_query.reader import Reader
from sandman_query.store import UserStore

log = logging.getLogger(__name__)
PROJECT_ID = r"^[a-z0-9][a-z0-9-]{1,62}$"
USERNAME = r"^[A-Za-z0-9._@-]{2,64}$"
ProjectId = Annotated[str, PathParam(pattern=PROJECT_ID)]
ProjectRole = Literal["viewer", "triager", "maintainer"]
COOKIE = "sandman_session"
QUIET = {"/healthz", "/readyz", "/v1/status"}  # not counted as in-flight requests


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: str


class NewToken(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=100)
    projectId: str | None = Field(default=None, pattern=PROJECT_ID)
    maxRole: ProjectRole | None = None
    expiresAt: datetime | None = None


class NewUser(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(pattern=USERNAME)
    password: str = Field(min_length=12)
    globalRoles: list[Literal["admin"]] = []


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disabled: bool | None = None
    password: str | None = Field(default=None, min_length=12)
    globalRoles: list[Literal["admin"]] | None = None


class Member(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: ProjectRole


class CypherBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projectId: str | None = Field(default=None, pattern=PROJECT_ID)
    cypher: str = Field(max_length=20000)
    params: dict[str, Any] = {}
    limit: int = Field(default=200, ge=1, le=1000)
    timeoutMs: int = Field(default=10000, ge=100, le=30000)


class NlBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projectId: str = Field(pattern=PROJECT_ID)
    question: str = Field(min_length=3, max_length=2000)
    strategy: Literal["auto", "cypher", "graphrag", "explore"] = "auto"
    mode: Literal["answer", "cypher-only"] = "answer"
    execute: bool = True


class WorkflowBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["full", "extract", "tlr", "detect"]
    params: dict[str, Any] | None = None
    force: bool = False


def iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class Service:
    """Shared state and operations of the REST routes and the console."""

    def __init__(
        self,
        settings: Settings,
        store: UserStore,
        reader: Reader,
        owners: Owners,
        cookie_secure: bool,
        config_dir: Path,
        memory_limit: str = "100 MB",
    ) -> None:
        self.settings, self.store, self.reader, self.owners = settings, store, reader, owners
        self.cookie_secure = cookie_secure
        self.verifier = TokenVerifier(settings.service_verify)
        contracts = Contracts(settings.contracts_dir)
        self.min_roles = load_min_roles(settings.contracts_dir)
        self.grants = load_plugin_grants(config_dir / "query" / "plugins.yaml", contracts)
        self.runner = Runner(reader, memory_limit)
        self.intents = Intents(config_dir / "query" / "intents.yaml", contracts)
        self.nl = NlService(settings, contracts, config_dir, reader, self.runner, self.intents)
        self.limiter = RateLimiter()
        self.tracker = StatusTracker("query-service", __version__)

    # ------------------------------------------------------------ principals

    def user_principal(self, username: str, **token: Any) -> Principal:
        user = self.store.get_user(username)
        assert user is not None
        return Principal(
            "user",
            username,
            admin="admin" in user["global_roles"].split(","),
            memberships=self.store.memberships(username),
            token_project=token.get("project_id"),
            token_max_role=token.get("max_role"),
        )

    def authenticate(self, authorization: str | None) -> Principal:
        """Bearer token only: service/plugin token or personal token."""
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "missing or invalid token", headers={"WWW-Authenticate": "Bearer"})
        secret = authorization[7:].strip()
        caller = self.verifier.caller(secret)
        if caller is not None:
            if caller.startswith(PLUGIN_PREFIX):
                return plugin_principal(caller, self.grants)
            return Principal("service", caller)
        token = self.store.find_token(secret)
        if token is None:
            raise HTTPException(401, "missing or invalid token", headers={"WWW-Authenticate": "Bearer"})
        user = self.store.get_user(token["username"])
        if user is None or user["must_change_password"]:
            raise HTTPException(403, "password change required")
        return self.user_principal(token["username"], project_id=token["project_id"], max_role=token["max_role"])

    def check(self, who: Principal, op: str, project: str | None = None) -> None:
        authorize(who, self.min_roles, op, project)

    async def login(self, username: str, password: str, request: Request) -> str:
        """Session id for valid credentials; 429 after 5 failures per username or client address in 15 min."""
        keys = (f"u:{username.lower()}", f"ip:{request.client.host if request.client else '?'}")
        if self.limiter.blocked(*keys):
            raise HTTPException(429, "too many failed logins", headers={"Retry-After": "900"})
        user = await asyncio.to_thread(self.store.verify_password, username, password)
        if user is None:
            self.limiter.fail(*keys)
            raise HTTPException(401, "invalid credentials")
        self.limiter.reset(keys[0])
        return self.store.create_session(user["username"])[0]

    def set_cookie(self, response: Response, sid: str) -> None:
        response.set_cookie(COOKIE, sid, httponly=True, samesite="strict", secure=self.cookie_secure, path="/")

    # -------------------------------------------------------------- documents

    def user_doc(self, username: str) -> dict[str, Any]:
        u = self.store.get_user(username)
        assert u is not None
        return {
            "username": u["username"],
            "globalRoles": [r for r in u["global_roles"].split(",") if r],
            "disabled": bool(u["disabled"]),
            "createdAt": u["created_at"],
            "memberships": [{"projectId": p, "role": r} for p, r in sorted(self.store.memberships(username).items())],
        }

    def me_doc(self, username: str) -> dict[str, Any]:
        return {**self.user_doc(username), "lastVisit": self.store.last_visits(username)}

    @staticmethod
    def token_doc(t: Mapping[str, Any]) -> dict[str, Any]:
        doc = {"id": t["id"], "name": t["name"], "projectId": t["project_id"], "maxRole": t["max_role"],
               "createdAt": t["created_at"], "expiresAt": t["expires_at"], "lastUsedAt": t["last_used_at"]}  # fmt: skip
        return {k: v for k, v in doc.items() if v is not None}

    async def projects(self, who: Principal) -> list[dict[str, Any]]:
        """KG projects the caller can read, with the caller's role."""
        names = {v: k for k, v in LEVEL.items()}
        rows = await self.reader.rows("MATCH (p:Project) RETURN properties(p) AS p ORDER BY p.id", {})
        out = []
        for p in (r["p"] for r in rows):
            level = who.level(p["id"])
            if level:
                out.append({k: p[k] for k in ("id", "name", "llmPolicy", "createdAt")} | {"role": names[level]})
        return out

    # ------------------------------------------------------------- forwarding

    async def forward(self, owner: str, method: str, path: str, who: Principal, project: str | None, **kw: Any) -> Any:
        try:
            return await self.owners.call(owner, method, path, who, project, **kw)
        except httpx.HTTPError as e:
            raise HTTPException(503, f"{owner} unavailable: {type(e).__name__}") from None

    async def inconsistency(self, who: Principal, iid: str, op: str) -> httpx.Response:
        """Owner detail, authorized against its project (404 if not readable)."""
        resp: httpx.Response = await self.forward("inconsistency-agent", "GET", f"/v1/inconsistencies/{iid}", who, None)
        if resp.status_code == 404:
            raise HTTPException(404, "not found")
        if resp.status_code != 200:
            raise HTTPException(502, f"inconsistency-agent returned {resp.status_code}")
        self.check(who, op, resp.json()["projectId"])
        return resp

    async def inconsistency_response(self, who: Principal, iid: str, op: str) -> Response:
        return relay(await self.inconsistency(who, iid, op))

    async def evidence(self, project: str, detail: dict[str, Any]) -> dict[str, Any] | None:
        """Evidence of a finding from the KG (current state, links, runs); None if the KG read fails.

        Call only after the caller is authorized for the project; every query is scoped to it.
        """
        roles = [(x["element"], x["role"]) for x in detail.get("involved", []) if x.get("element")]
        involved = [e for e, _ in roles]
        p = {"p": project}
        try:
            ids, keys = [e["id"] for e in involved], [e["key"] for e in involved]
            detected, current, links, runs = await asyncio.gather(
                self.reader.rows(_EV_DETECTED, p | {"ids": ids}),
                self.reader.rows(_EV_CURRENT, p | {"keys": keys}),
                self.reader.rows(_EV_LINKS, p | {"keys": keys}),
                self.reader.rows(_EV_RUNS, p | {"id": detail["id"]}),
            )
            by_id, by_key = {r["id"]: r for r in detected}, {r["key"]: r for r in current}
            versions = {v for e in involved if (v := e.get("artifactVersionId"))} | {r["version"] for r in current}
            revisions = {r["id"]: r for r in await self.reader.rows(_EV_REVISIONS, p | {"ids": sorted(versions)})}
        except Exception:  # noqa: BLE001 - evidence is optional; the page must still render
            log.warning("evidence read failed", exc_info=True)
            return None
        items = [_evidence_item(e, by_id.get(e["id"], {}), by_key.get(e["key"]), revisions) | {"role": r}
                 for e, r in roles]  # fmt: skip
        runs = [r for r in runs if r.get("id")]
        return {"items": items, "links": links[:EVIDENCE_LINKS], "more_links": len(links) > EVIDENCE_LINKS,
                "runs": runs, "revisions": revisions}  # fmt: skip

    async def transition(self, who: Principal, iid: str, body: bytes) -> Response:
        detail = await self.inconsistency(who, iid, "transitionInconsistency")
        project = detail.json()["projectId"]
        resp = await self.forward(
            "inconsistency-agent", "POST", f"/v1/inconsistencies/{iid}/transitions", who, project,
            body=body, headers={"content-type": "application/json"},
        )  # fmt: skip
        return relay(resp)

    async def list_inconsistencies(self, who: Principal, project: str, query: list[tuple[str, str]]) -> Response:
        keep = {"status", "type", "severity", "artifactId", "changedSince", "limit", "cursor"}
        resp = await self.forward(
            "inconsistency-agent", "GET", f"/v1/projects/{project}/inconsistencies", who, project,
            params=[(k, v) for k, v in query if k in keep],
        )  # fmt: skip
        return relay(resp)

    async def review_link(self, who: Principal, project: str, link_id: str, body: bytes) -> Response:
        if not await self.reader.rows(_LINK_OF_PROJECT, {"id": link_id, "p": project}):
            return problem(404, "Not Found", f"link {link_id} not found")
        resp = await self.forward(
            "tlr-agent", "POST", f"/v1/links/{link_id}/review", who, project,
            body=body, headers={"content-type": "application/json"},
        )  # fmt: skip
        return relay(resp)

    async def link_rows(self, project: str, approach: str | None, artifact: str | None) -> list[dict[str, Any]]:
        return await self.reader.rows(
            _LINKS, {"p": project, "approach": approach or None, "artifact": artifact or None}
        )

    async def list_workflows(self, who: Principal, project: str, query: list[tuple[str, str]]) -> Response:
        params = [("projectId", project), *[(k, v) for k, v in query if k in {"status", "limit", "cursor"}]]
        return relay(await self.forward("orchestrator", "GET", "/v1/workflows", who, project, params=params))

    async def create_workflow(self, who: Principal, project: str, body: WorkflowBody) -> Response:
        payload = {"projectId": project, **body.model_dump(exclude_none=True)}
        resp = await self.forward(
            "orchestrator", "POST", "/v1/workflows", who, project,
            body=_json(payload), headers={"content-type": "application/json"},
        )  # fmt: skip
        return relay(resp)


EVIDENCE_LINKS = 30
_LINK_OF_PROJECT = "MATCH (l:TraceLink {id: $id, projectId: $p}) RETURN l.id AS id"
_LINKS = """/* console:links */
MATCH (l:TraceLink {projectId: $p, active: true})-[:SOURCE]->(s:Element)<-[:HAS_ELEMENT]-(:ArtifactVersion)
      <-[:HAS_VERSION]-(sa:Artifact),
      (l)-[:TARGET]->(t:Element)<-[:HAS_ELEMENT]-(:ArtifactVersion)<-[:HAS_VERSION]-(ta:Artifact)
WHERE ($approach IS NULL OR l.approach = $approach) AND ($artifact IS NULL OR sa.id = $artifact OR ta.id = $artifact)
RETURN l.id AS id, l.approach AS approach, l.confidence AS confidence, l.status AS status, l.rationale AS rationale,
       l.reviewedBy AS reviewedBy, l.reviewedAt AS reviewedAt, l.reviewReason AS reviewReason,
       l.reviewOrigin AS reviewOrigin, s.id AS sourceId, s.key AS sourceKey, s.name AS sourceName, s.text AS sourceText,
       t.id AS targetId, t.key AS targetKey, t.name AS targetName, t.text AS targetText
ORDER BY sourceKey, targetKey, approach LIMIT 5000"""
_EV_DETECTED = """/* evidence:detected */ MATCH (e:Element {projectId: $p}) WHERE e.id IN $ids
RETURN e.id AS id, e.contentHash AS hash, e.sentenceIndex AS idx"""
_EV_CURRENT = """/* evidence:current */
MATCH (:Artifact {projectId: $p})-[:CURRENT_VERSION]->(v:ArtifactVersion)-[:HAS_ELEMENT]->(c:Element {projectId: $p})
WHERE c.key IN $keys
RETURN c.key AS key, c.contentHash AS hash, c.text AS text, c.sentenceIndex AS idx, c.startLine AS startLine,
       c.endLine AS endLine, v.id AS version"""
_EV_LINKS = f"""/* evidence:links */
MATCH (l:TraceLink {{projectId: $p}})-[:SOURCE]->(s:Element), (l)-[:TARGET]->(t:Element)
WHERE s.key IN $keys OR t.key IN $keys
RETURN l.id AS id, l.approach AS approach, l.confidence AS confidence, l.status AS status, l.active AS active,
       l.linkType AS linkType, s.key AS sourceKey, s.name AS sourceName, t.key AS targetKey, t.name AS targetName
ORDER BY l.active DESC, l.confidence DESC, l.id LIMIT {EVIDENCE_LINKS + 1}"""
_EV_RUNS = """/* evidence:runs */ MATCH (i:Inconsistency {id: $id, projectId: $p})
OPTIONAL MATCH (r:Run)-[:DETECTED]->(i)
RETURN r.id AS id, r.startedAt AS at, r.tool AS tool, r.toolVersion AS toolVersion, r.status AS status
ORDER BY r.startedAt"""
_EV_REVISIONS = """/* evidence:revisions */ MATCH (v:ArtifactVersion {projectId: $p}) WHERE v.id IN $ids
OPTIONAL MATCH (g:Ingestion)-[:ACTIVATED]->(v)
RETURN v.id AS id, v.sourceRevision AS revision, min(g.seq) AS seq, min(g.createdAt) AS at"""


def _evidence_item(e: dict[str, Any], detected: dict[str, Any], cur: dict[str, Any] | None,
                   revisions: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
    """One involved element: its text as detected, its current state and its position."""
    if cur is None:
        state = "gone"
    else:
        state = "current" if cur.get("hash") == detected.get("hash") else "changed"
    local = e["key"].split("#", 1)[-1]
    idx = (cur or {}).get("idx") if state != "gone" else detected.get("idx")
    return {
        "element": e, "state": state, "current": cur, "detectedText": e.get("excerpt"),
        "section": local.rsplit("/", 1)[0].removeprefix("sec:") if isinstance(idx, int) else None,
        "sentence": idx + 1 if isinstance(idx, int) else None,
        "detectedRevision": revisions.get(e.get("artifactVersionId") or ""),
        "currentRevision": revisions.get((cur or {}).get("version") or ""),
    }  # fmt: skip


def _json(data: Any) -> bytes:
    return json.dumps(data).encode()


def valid_username(name: str) -> bool:
    return re.fullmatch(USERNAME, name) is not None


def valid_project(project: str) -> bool:
    return re.fullmatch(PROJECT_ID, project) is not None


def new_token(name: str, project: str | None, max_role: str | None, expires: str | None) -> tuple[Any, ...]:
    """Validated (name, project, max role, expiry) for UserStore.create_token; ValueError with a message."""
    if not name or len(name) > 100:
        raise ValueError("The name needs 1 to 100 characters.")
    if project and not valid_project(project):
        raise ValueError("Invalid project id.")
    if max_role and max_role not in ("viewer", "triager", "maintainer"):
        raise ValueError("Invalid role.")
    try:
        expiry = iso(datetime.fromisoformat(expires)) if expires else None
    except ValueError:
        raise ValueError("Invalid expiry; use e.g. 2027-01-01T00:00:00Z.") from None
    return name, project or None, max_role or None, expiry

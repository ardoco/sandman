"""Server-rendered console (/console): Jinja2 with autoescape, vendored htmx, session cookie and CSRF on every form."""

import asyncio
import hashlib
import hmac
import io
import json
import re
import secrets
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

import jinja2
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException

from sandman_query import graph_console
from sandman_query.auth import LEVEL, Principal
from sandman_query.service import Service, WorkflowBody, new_token, valid_project, valid_username
from sandman_query.store import Exists
from sandman_query.synthesis import MARKER

TEMPLATES = jinja2.Environment(
    loader=jinja2.FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=jinja2.select_autoescape(["html"]),
)
LOGIN_CSRF = "sandman_login_csrf"
STATUSES = ["OPEN", "REOPENED", "DEFERRED", "ACCEPTED", "REJECTED", "RESOLVED"]
REASON_REQUIRED = {"ACCEPTED", "REJECTED", "DEFERRED"}
CAUSES = ["extraction-error", "link-error", "detector-error", "artifact-inconsistency", "planned-change", "intentional"]
KINDS = ["requirements", "architecture-doc", "architecture-model", "code", "test", "other"]
MAX_FILES = 20000
DEFAULT_RULES = "\n".join(
    ["requirements/** = requirements:markdown", "docs/** = architecture-doc", "model/** = architecture-model",
     "src/test/** = test", "src/main/** = code"]
)  # fmt: skip


@dataclass
class Ctx:
    username: str
    csrf: str
    sid: str
    who: Principal
    must_change: bool


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, 303)


def parse(resp: Response) -> Any:
    """JSON body of a relayed owner response; owner problems become HTTP errors."""
    data = json.loads(resp.body) if resp.body else None
    if resp.status_code >= 400:
        raise HTTPException(
            resp.status_code, (data or {}).get("detail") or (data or {}).get("title") or "request failed"
        )
    return data


STRATEGIES = ("auto", "cypher", "graphrag", "explore")
INCONSISTENCY_ID = re.compile(r"^inc_[0-9a-f]{32}$")


def answer_parts(answer: str, rows: list[list[str]]) -> list[tuple[str, str | None]]:
    """(text, link) pairs; link is set for the [[id]] citations (finding page or evidence row), text is autoescaped."""
    parts: list[tuple[str, str | None]] = []
    last = 0
    for m in MARKER.finditer(answer):
        parts.append((answer[last : m.start()], None))
        ident = m.group(1)
        row = next((i for i, r in enumerate(rows) if ident in r), None)
        link = f"/console/inconsistencies/{ident}" if INCONSISTENCY_ID.match(ident) else None
        parts.append((ident, link or (f"#row-{row}" if row is not None else None)))
        last = m.end()
    return [*parts, (answer[last:], None)]


def cell(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


# ponytail: hashing runs on the event loop; move to a thread if archives of several hundred MB are common
def archive_manifest(file: BinaryIO) -> list[dict[str, str]]:
    """path/sha256 of the regular files of a tar(.gz) or zip archive (streamed); links, unsafe paths rejected."""
    files: dict[str, str] = {}

    def add(name: str, open_member: Any) -> None:
        path = PurePosixPath(name.removeprefix("./"))
        if path.is_absolute() or ".." in path.parts or not path.parts or len(files) >= MAX_FILES:
            raise ValueError(f"unsafe or too many archive entries: {name}")
        if path.as_posix() in files:
            raise ValueError(f"duplicate archive entry {path}")
        digest = hashlib.sha256()
        with open_member() as member:
            while chunk := member.read(1 << 20):
                digest.update(chunk)
        files[path.as_posix()] = digest.hexdigest()

    file.seek(0)
    if zipfile.is_zipfile(file):
        with zipfile.ZipFile(file) as zf:
            for info in zf.infolist():
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError(f"links are not allowed: {info.filename}")
                if not info.is_dir():
                    add(info.filename, lambda i=info: zf.open(i))  # type: ignore[misc]
    else:
        file.seek(0)
        try:
            tf = tarfile.open(fileobj=file, mode="r:*")
        except tarfile.TarError:
            raise ValueError("archive is neither tar(.gz) nor zip") from None
        with tf:
            for member in tf:
                if member.isdir():
                    continue
                if not member.isreg():
                    raise ValueError(f"not a regular file: {member.name}")
                add(member.name, lambda m=member: tf.extractfile(m) or io.BytesIO())  # type: ignore[misc]
    file.seek(0)
    return [{"path": p, "sha256": h} for p, h in sorted(files.items())]


def kind_rules(text: str) -> list[dict[str, str]]:
    rules = []
    for line in filter(None, (ln.strip() for ln in text.splitlines())):
        glob, sep, kind = (part.strip() for part in line.partition("="))
        kind, _, fmt = kind.partition(":")
        if not sep or not glob or kind not in KINDS:
            raise ValueError(f"invalid kind rule {line!r}; kinds: {', '.join(KINDS)}")
        rules.append({"glob": glob, "kind": kind, **({"format": fmt} if fmt else {})})
    return rules


def error_response(e: HTTPException) -> Response:
    """HTML for errors of console pages; 303 carries the redirect target."""
    if e.status_code == 303:
        return redirect((e.headers or {})["Location"])
    page = TEMPLATES.get_template("error.html").render(
        user=None, csrf="", is_admin=False, status=e.status_code, message=e.detail
    )
    return HTMLResponse(page, e.status_code, headers=e.headers)


def build_router(svc: Service) -> APIRouter:
    router = APIRouter(prefix="/console")
    store = svc.store

    def render(ctx: Ctx | None, template: str, status: int = 200, **data: Any) -> HTMLResponse:
        base = {"user": ctx.username if ctx else None, "csrf": ctx.csrf if ctx else data.pop("csrf", ""),
                "is_admin": bool(ctx and ctx.who.unrestricted_admin)}  # fmt: skip
        return HTMLResponse(TEMPLATES.get_template(template).render(**base, **data), status)

    def ctx_dep(request: Request) -> Ctx:
        sid = request.cookies.get("sandman_session")
        row = store.get_session(sid) if sid else None
        if row is None:
            raise HTTPException(303, headers={"Location": "/console/login"})
        user = store.get_user(row["username"])
        assert user is not None
        ctx = Ctx(
            row["username"],
            row["csrf"],
            sid or "",
            svc.user_principal(row["username"]),
            bool(user["must_change_password"]),
        )
        if ctx.must_change and request.url.path not in ("/console/password", "/console/logout"):
            raise HTTPException(303, headers={"Location": "/console/password"})
        return ctx

    async def form(request: Request, ctx: Ctx | None = None) -> FormData:
        """Parsed form; 403 unless its csrf token matches the session's (or the login cookie's before login)."""
        data = await request.form()
        expected = ctx.csrf if ctx else request.cookies.get(LOGIN_CSRF, "")
        sent = data.get("csrf")
        if not expected or not isinstance(sent, str) or not hmac.compare_digest(sent.encode(), expected.encode()):
            raise HTTPException(403, "missing or invalid CSRF token")
        return data

    def text(data: FormData, name: str) -> str:
        value = data.get(name, "")
        return value.strip() if isinstance(value, str) else ""

    Session = Depends(ctx_dep)

    # ----------------------------------------------------------- login/logout

    @router.get("")
    async def index() -> RedirectResponse:
        return redirect("/console/projects")

    @router.get("/login")
    async def login_page(request: Request) -> HTMLResponse:
        token = secrets.token_urlsafe(24)
        resp = render(None, "login.html", csrf=token)
        resp.set_cookie(LOGIN_CSRF, token, httponly=True, samesite="strict", secure=svc.cookie_secure, path="/console")
        return resp

    @router.post("/login")
    async def login(request: Request) -> Response:
        data = await form(request)
        try:
            sid = await svc.login(text(data, "username"), str(data.get("password", "")), request)
        except HTTPException as e:
            if e.status_code in (401, 429):
                token = request.cookies[LOGIN_CSRF]
                return render(None, "login.html", e.status_code, csrf=token, message=str(e.detail), error=True)
            raise
        user = store.get_user(text(data, "username"))
        resp = redirect("/console/password" if user and user["must_change_password"] else "/console/projects")
        svc.set_cookie(resp, sid)
        resp.delete_cookie(LOGIN_CSRF, path="/console")
        return resp

    @router.post("/logout")
    async def logout(request: Request, ctx: Ctx = Session) -> Response:
        await form(request, ctx)
        store.revoke_session(ctx.sid)
        resp = redirect("/console/login")
        resp.delete_cookie("sandman_session", path="/")
        return resp

    @router.get("/password")
    async def password_page(ctx: Ctx = Session) -> HTMLResponse:
        return render(ctx, "password.html", forced=ctx.must_change)

    @router.post("/password")
    async def password(request: Request, ctx: Ctx = Session) -> Response:
        data = await form(request, ctx)
        new = str(data.get("new", ""))
        error = None
        if len(new) < 12:
            error = "The new password needs at least 12 characters."
        elif new == data.get("current"):
            error = "The new password must differ from the current one."
        elif await asyncio.to_thread(store.verify_password, ctx.username, str(data.get("current", ""))) is None:
            error = "The current password is wrong."
        if error:
            return render(ctx, "password.html", 400, forced=ctx.must_change, message=error, error=True)
        await asyncio.to_thread(store.update_user, ctx.username, password=new, must_change=False)  # revokes sessions
        resp = redirect("/console/login")
        resp.delete_cookie("sandman_session", path="/")
        return resp

    # ------------------------------------------------------------ projects

    def readable(ctx: Ctx, op: str, project: str) -> None:
        svc.check(ctx.who, op, project)

    @router.get("/projects")
    async def projects(ctx: Ctx = Session) -> HTMLResponse:
        visits = store.last_visits(ctx.username)
        rows = []
        for p in await svc.projects(ctx.who):
            counts: dict[str, int] = {}
            new = 0
            workflow = None
            try:
                listing = parse(await svc.list_inconsistencies(ctx.who, p["id"], [("limit", "1")]))
                counts = listing["counts"]
                if p["id"] in visits:
                    since = [("changedSince", visits[p["id"]]), ("limit", "500")]
                    changed = parse(await svc.list_inconsistencies(ctx.who, p["id"], since))["items"]
                    new = sum(1 for i in changed if i["status"] in ("OPEN", "REOPENED"))
                flows = parse(await svc.list_workflows(ctx.who, p["id"], [("limit", "1")]))["items"]
                workflow = flows[0]["status"] if flows else None
            except HTTPException:
                workflow = "unavailable"  # an owner is down; the page still renders
            rows.append({**p, "counts": counts, "new": new, "workflow": workflow,
                         "can_upload": ctx.who.level(p["id"]) >= LEVEL["maintainer"]})  # fmt: skip
        return render(ctx, "projects.html", projects=rows)

    @router.get("/projects/{project}/inconsistencies")
    async def inconsistencies(project: str, request: Request, ctx: Ctx = Session) -> HTMLResponse:
        readable(ctx, "listInconsistencies", project)
        keys = ("status", "severity", "type", "artifactId")
        f = {k: request.query_params.get(k, "") for k in (*keys, "sort")}
        query = [(k, v) for k, v in request.query_params.multi_items() if k in (*keys, "cursor") and v]
        data = parse(await svc.list_inconsistencies(ctx.who, project, query))
        previous = store.last_visits(ctx.username).get(project)
        store.touch_visit(ctx.username, project)
        order = {"error": 0, "warning": 1, "info": 2}
        items = data["items"]
        if f["sort"] == "updated":
            items.sort(key=lambda i: i["updatedAt"], reverse=True)
        else:
            items.sort(key=lambda i: order.get(i["severity"], 3))
        for i in items:
            i["is_new"] = bool(previous and i["updatedAt"] > previous and i["status"] in ("OPEN", "REOPENED"))
        cursor = data.get("nextCursor")
        next_query = "&".join(f"{k}={v}" for k, v in [*query, ("cursor", cursor)]) if cursor else ""
        return render(ctx, "inconsistencies.html", project=project, items=items, counts=data["counts"], f=f,
                      statuses=STATUSES, next_cursor=cursor, next_query=next_query)  # fmt: skip

    async def detail_page(ctx: Ctx, iid: str, status: int = 200, **extra: Any) -> HTMLResponse:
        d = parse(await svc.inconsistency_response(ctx.who, iid, "getInconsistency"))
        evidence = json.dumps(d["evidence"], indent=2, ensure_ascii=False) if d.get("evidence") else ""
        can = ctx.who.level(d["projectId"]) >= LEVEL["triager"]
        ev = await svc.evidence(d["projectId"], d)
        return render(
            ctx, "inconsistency.html", status, d=d, evidence=evidence, ev=ev, can_transition=can, causes=CAUSES, **extra
        )

    @router.get("/inconsistencies/{iid}")
    async def inconsistency(iid: str, ctx: Ctx = Session) -> HTMLResponse:
        return await detail_page(ctx, iid)

    @router.post("/inconsistencies/{iid}/transition")
    async def transition(iid: str, request: Request, ctx: Ctx = Session) -> Response:
        data = await form(request, ctx)
        body = {"to": text(data, "to"), "expectedStatus": text(data, "expectedStatus")}
        if reason := text(data, "reason"):
            body["reason"] = reason
        if body["to"] in REASON_REQUIRED and "reason" not in body:
            return await detail_page(ctx, iid, 400, message=f"A reason is required for {body['to']}.", error=True)
        if (cause := text(data, "cause")) and body["to"] != "OPEN":
            body["cause"] = cause
        if body["to"] in REASON_REQUIRED and "cause" not in body:
            return await detail_page(ctx, iid, 400, message=f"A cause is required for {body['to']}.", error=True)
        resp = await svc.transition(ctx.who, iid, json.dumps(body).encode())
        if resp.status_code == 200:
            return redirect(f"/console/inconsistencies/{iid}")
        problem = json.loads(resp.body) if resp.body else {}
        if resp.status_code == 409:
            message = "The status was changed concurrently. Review the current state and try again."
        else:
            message = problem.get("detail") or problem.get("title") or "The transition failed."
        return await detail_page(ctx, iid, resp.status_code, message=message, error=True)

    # -------------------------------------------------------------- upload

    @router.get("/projects/{project}/upload")
    async def upload_page(project: str, ctx: Ctx = Session) -> HTMLResponse:
        readable(ctx, "createIngestion", project)
        return render(ctx, "upload.html", project=project, kinds=", ".join(KINDS), rules=DEFAULT_RULES)

    @router.post("/projects/{project}/upload")
    async def upload(project: str, request: Request, ctx: Ctx = Session) -> Response:
        readable(ctx, "createIngestion", project)
        data = await form(request, ctx)
        archive = data.get("archive")
        rules = text(data, "kindRules")

        def again(message: str, status: int = 400, result: str = "") -> HTMLResponse:
            return render(ctx, "upload.html", status, project=project, kinds=", ".join(KINDS), rules=rules,
                          message=message, error=status >= 400, result=result)  # fmt: skip

        if not isinstance(archive, UploadFile):
            return again("Choose an archive.")
        try:
            manifest: dict[str, Any] = {"mode": text(data, "mode") or "full", "files": archive_manifest(archive.file),
                                        "kindRules": kind_rules(rules)}  # fmt: skip
        except ValueError as e:
            return again(str(e))
        if revision := text(data, "sourceRevision"):
            manifest["sourceRevision"] = revision
        files = {"manifest": (None, json.dumps(manifest), "application/json"),
                 "archive": (archive.filename or "archive", archive.file, "application/octet-stream")}  # fmt: skip
        resp = await svc.forward("extraction-agent", "POST", f"/v1/projects/{project}/ingestions", ctx.who, project,
                                 files=files)  # fmt: skip
        body = json.loads(resp.content) if resp.content else {}
        if resp.status_code >= 400:
            return again(
                str(body.get("detail") or body.get("title") or "Upload failed."),
                resp.status_code if resp.status_code != 401 else 503,
            )
        return again("Upload completed.", 200, json.dumps(body, indent=2))

    # ----------------------------------------------------------------- ask

    @router.get("/projects/{project}/ask")
    async def ask_page(project: str, ctx: Ctx = Session) -> HTMLResponse:
        readable(ctx, "queryNl", project)
        return render(
            ctx, "ask.html", project=project, question="", strategy="auto", strategies=STRATEGIES, result=None,
            intents=", ".join(svc.intents.supported()),
        )  # fmt: skip

    @router.post("/projects/{project}/ask")
    async def ask(project: str, request: Request, ctx: Ctx = Session) -> HTMLResponse:
        readable(ctx, "queryNl", project)
        data = await form(request, ctx)
        question = text(data, "question")
        strategy = text(data, "strategy") or "auto"
        base = {"project": project, "question": question, "strategy": strategy, "strategies": STRATEGIES,
                "intents": ", ".join(svc.intents.supported())}  # fmt: skip
        try:
            if strategy not in STRATEGIES:
                raise HTTPException(400, "unknown strategy")
            result = await svc.nl.answer(ctx.who, project, question, strategy, "answer", True)
        except HTTPException as e:
            return render(ctx, "ask.html", e.status_code, result=None, message=str(e.detail), error=True, **base)
        rows = [[cell(v) for v in row] for row in result["rows"]]
        parts = answer_parts(result["answer"], rows) if result.get("answer") else []
        return render(ctx, "ask.html", result=result, rows=rows, parts=parts, **base)

    # ----------------------------------------------------------- workflows

    async def workflows_page(ctx: Ctx, project: str, status: int = 200, **extra: Any) -> HTMLResponse:
        items = parse(await svc.list_workflows(ctx.who, project, [("limit", "20")]))["items"]
        can = ctx.who.level(project) >= LEVEL["maintainer"]
        return render(ctx, "workflows.html", status, project=project, items=items, can_trigger=can, **extra)

    @router.get("/projects/{project}/workflows")
    async def workflows(project: str, ctx: Ctx = Session) -> HTMLResponse:
        readable(ctx, "listWorkflows", project)
        return await workflows_page(ctx, project)

    @router.post("/projects/{project}/workflows")
    async def trigger(project: str, request: Request, ctx: Ctx = Session) -> Response:
        readable(ctx, "createWorkflow", project)
        data = await form(request, ctx)
        try:
            body = WorkflowBody(type=text(data, "type"))  # type: ignore[arg-type]
        except ValueError:
            raise HTTPException(400, "unknown workflow type") from None
        resp = await svc.create_workflow(ctx.who, project, body)
        if resp.status_code >= 400:
            return await workflows_page(
                ctx, project, resp.status_code, message="Could not start the workflow.", error=True
            )
        return redirect(f"/console/projects/{project}/workflows")

    # -------------------------------------------------------------- tokens

    def tokens_page(ctx: Ctx, status: int = 200, **extra: Any) -> HTMLResponse:
        tokens = [svc.token_doc(t) for t in store.list_tokens(ctx.username)]
        return render(ctx, "tokens.html", status, tokens=tokens, **extra)

    @router.get("/tokens")
    async def tokens(ctx: Ctx = Session) -> HTMLResponse:
        return tokens_page(ctx)

    @router.post("/tokens")
    async def create_token(request: Request, ctx: Ctx = Session) -> HTMLResponse:
        data = await form(request, ctx)
        try:
            body = new_token(text(data, "name"), text(data, "projectId") or None, text(data, "maxRole") or None,
                                 text(data, "expiresAt") or None)  # fmt: skip
        except ValueError as e:
            return tokens_page(ctx, 400, message=str(e), error=True)
        _, secret = store.create_token(ctx.username, *body)
        return tokens_page(ctx, secret=secret)

    @router.post("/tokens/{token_id}/revoke")
    async def revoke_token(token_id: str, request: Request, ctx: Ctx = Session) -> Response:
        await form(request, ctx)
        store.delete_token(ctx.username, token_id)
        return redirect("/console/tokens")

    # --------------------------------------------------------------- admin

    def admin_only(ctx: Ctx) -> None:
        if not ctx.who.unrestricted_admin:
            raise HTTPException(403, "admin role required")

    def admin_page(ctx: Ctx, status: int = 200, **extra: Any) -> HTMLResponse:
        users = [svc.user_doc(u["username"]) for u in store.list_users()]
        return render(ctx, "admin.html", status, users=users, **extra)

    @router.get("/admin")
    async def admin(ctx: Ctx = Session) -> HTMLResponse:
        admin_only(ctx)
        return admin_page(ctx)

    @router.post("/admin/users")
    async def admin_create_user(request: Request, ctx: Ctx = Session) -> Response:
        admin_only(ctx)
        data = await form(request, ctx)
        name, pw = text(data, "username"), str(data.get("password", ""))
        if not valid_username(name) or len(pw) < 12:
            return admin_page(ctx, 400, message="User name or password (12+ characters) invalid.", error=True)
        try:
            await asyncio.to_thread(store.create_user, name, pw, ["admin"] if data.get("admin") == "true" else [])
        except Exists:
            return admin_page(ctx, 409, message="The user exists.", error=True)
        return redirect("/console/admin")

    @router.post("/admin/users/{username}/disabled")
    async def admin_disable(username: str, request: Request, ctx: Ctx = Session) -> Response:
        admin_only(ctx)
        data = await form(request, ctx)
        if not store.update_user(username, disabled=data.get("disabled") == "true"):
            raise HTTPException(404, "not found")
        return redirect("/console/admin")

    @router.post("/admin/users/{username}/password")
    async def admin_reset_password(username: str, request: Request, ctx: Ctx = Session) -> Response:
        admin_only(ctx)
        data = await form(request, ctx)
        pw = str(data.get("password", ""))
        if len(pw) < 12:
            return admin_page(ctx, 400, message="The password needs at least 12 characters.", error=True)
        if not await asyncio.to_thread(store.update_user, username, password=pw, must_change=username != ctx.username):
            raise HTTPException(404, "not found")
        return redirect("/console/admin")

    @router.post("/admin/members")
    async def admin_set_member(request: Request, ctx: Ctx = Session) -> Response:
        admin_only(ctx)
        data = await form(request, ctx)
        role = text(data, "role")
        if role not in ("viewer", "triager", "maintainer") or not valid_project(text(data, "project")):
            return admin_page(ctx, 400, message="Invalid project or role.", error=True)
        if not store.set_member(text(data, "project"), text(data, "username"), role):
            return admin_page(ctx, 404, message="Unknown user.", error=True)
        return redirect("/console/admin")

    @router.post("/admin/members/remove")
    async def admin_remove_member(request: Request, ctx: Ctx = Session) -> Response:
        admin_only(ctx)
        data = await form(request, ctx)
        store.delete_member(text(data, "project"), text(data, "username"))
        return redirect("/console/admin")

    graph_console.mount(router, svc, ctx_dep, render)
    return router

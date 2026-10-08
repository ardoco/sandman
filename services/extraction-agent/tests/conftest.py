"""Testcontainers Memgraph + NATS, an in-process fake extraction plugin and helpers (skipped without Docker)."""

import asyncio
import hashlib
import io
import json
import re
import tarfile
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import nats
import pytest
import yaml
from jsonschema import Draft202012Validator
from neo4j import AsyncGraphDatabase
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from sandman_agent import A2aClient, Settings, SkillContext, SkillError, SkillResult, TaskSnapshot, serve
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_extraction.app import create_app
from testcontainers.core.container import DockerContainer

FIXTURE = Path(__file__).parent / "fixtures" / "mini"
RENDERED = Path(__file__).parent / "fixtures" / "rendered"
LLM_MODELS = Path(__file__).parents[3] / "config" / "llm" / "models.yaml"
URL = "http://extraction-agent:8000"
TOKENS = {"orchestrator": "orch-token", "query-service": "query-token", "cli": "cli-token"}
KIND_RULES = [
    {"glob": "requirements/**", "kind": "requirements", "format": "markdown"},
    {"glob": "docs/**", "kind": "architecture-doc"},
    {"glob": "model/**", "kind": "architecture-model"},
    {"glob": "src/test/**", "kind": "test"},
    {"glob": "src/main/**", "kind": "code"},
]
CHECK_SCHEMA = (
    "#/paths/~1v1~1projects~1{projectId}~1ingestions~1check/post/responses/200/content/application~1json/schema"
)


def _docker_available() -> bool:
    try:
        import docker  # type: ignore[import-untyped]

        docker.from_env().ping()
        return True
    except Exception:  # noqa: BLE001 - no daemon reachable
        return False


DOCKER = _docker_available()


def _wait(url: str) -> str:
    async def ping() -> None:
        if url.startswith("bolt"):
            driver = AsyncGraphDatabase.driver(url)
            try:
                await driver.verify_connectivity()
            finally:
                await driver.close()
        else:
            await (await nats.connect(url, connect_timeout=1, max_reconnect_attempts=0)).close()

    deadline = time.monotonic() + 60
    while True:
        try:
            asyncio.run(ping())
            return url
        except Exception:  # noqa: BLE001 - not ready yet
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


@pytest.fixture(scope="session")
def memgraph() -> Iterator[str]:
    if not DOCKER:
        pytest.skip("no Docker daemon reachable")
    with (
        DockerContainer("memgraph/memgraph:3.13.1").with_command("--telemetry-enabled=false").with_exposed_ports(7687)
    ) as c:
        yield _wait(f"bolt://{c.get_container_host_ip()}:{c.get_exposed_port(7687)}")


@pytest.fixture(scope="session")
def nats_url() -> Iterator[str]:
    if not DOCKER:
        pytest.skip("no Docker daemon reachable")
    with DockerContainer("nats:2.15.0-alpine").with_command("-js").with_exposed_ports(4222) as c:
        yield _wait(f"nats://{c.get_container_host_ip()}:{c.get_exposed_port(4222)}")


class FakePlugin:
    """sandman.plugin.extract like the plugin stub: file element (localKey = path) + one element per class."""

    id = "sandman.plugin.extract"

    def __init__(self) -> None:
        self.fail = False
        self.extra = False  # adds one method element per class

    async def run(self, ctx: SkillContext, input: dict[str, Any]) -> SkillResult:
        if self.fail:
            raise SkillError("PLUGIN_DOWN", "fake plugin down", True)
        elements: list[dict[str, Any]] = []
        refs = []
        for a in input["artifacts"]:
            text = Path(a["storagePath"]).read_text(encoding="utf-8")
            label = "TestCase" if a["kind"] == "test" else "CodeUnit"
            elements.append({"path": a["path"], "localKey": a["path"], "label": label, "kind": "file",
                             "name": Path(a["path"]).name})  # fmt: skip
            for name in re.findall(r"class (\w+)", text):
                elements.append({"path": a["path"], "localKey": name, "label": label, "kind": "type", "name": name,
                                 "parentLocalKey": a["path"]})  # fmt: skip
                if self.extra:
                    elements.append({"path": a["path"], "localKey": f"{name}#run()", "label": label,
                                     "kind": "method", "name": "run", "parentLocalKey": name})  # fmt: skip
                for ref in re.findall(r"REQ-\d+", text):
                    refs.append({"source": {"path": a["path"], "localKey": name}, "referenceText": ref,
                                 "linkType": "verifies"})  # fmt: skip
        return SkillResult({"elements": elements, "relations": [], "explicitReferences": refs})


class FakeConverter:
    """document-converter stand-in: renders the stored blob as the Markdown Docling produced for the real fixture."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[tuple[dict[str, Any], str | None]] = []
        self.status = 200
        self.error: Exception | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append((body, request.headers.get("authorization")))
        if self.error is not None:
            raise self.error
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "converter says no"})
        name = "teastore" if b"teastore" in (self.root / body["path"]).read_bytes() else "structure"
        return httpx.Response(200, json={"markdown": (RENDERED / f"{name}.md").read_text(encoding="utf-8")})


def _retrieve(uri: str) -> Resource[Any]:
    path = DEFAULT_CONTRACTS_DIR / uri.rsplit("/contracts/", 1)[1]
    text = path.read_text(encoding="utf-8")
    contents = yaml.safe_load(text) if path.suffix == ".yaml" else json.loads(text)
    return Resource.from_contents(contents, default_specification=DRAFT202012)


REGISTRY: Registry[Any] = Registry(retrieve=_retrieve)  # type: ignore[call-arg]


def openapi_validate(ref: str, instance: Any) -> None:
    """Validate against a schema of extraction-agent.yaml, e.g. '#/components/schemas/Ingestion'."""
    uri = (DEFAULT_CONTRACTS_DIR / "openapi" / "extraction-agent.yaml").as_uri()
    if ref.startswith("shared:"):
        uri, ref = (DEFAULT_CONTRACTS_DIR / "openapi" / "shared.yaml").as_uri(), ref[7:]
    Draft202012Validator({"$ref": uri + ref}, registry=REGISTRY).validate(instance)


def tar_gz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, data in files.items():
            info = tarfile.TarInfo(path)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def mini() -> dict[str, bytes]:
    """Snapshot 1 of the fixture as path -> content."""
    return {p.relative_to(FIXTURE).as_posix(): p.read_bytes() for p in sorted(FIXTURE.rglob("*")) if p.is_file()}


@dataclass
class Env:
    http: httpx.AsyncClient
    app: Any
    plugin: FakePlugin
    nats_url: str
    config_hash: str
    converter: FakeConverter
    project: str = field(default_factory=lambda: f"p-{uuid.uuid4().hex[:10]}")
    # module helpers, reachable from tests (conftest is not importable under --import-mode=importlib)
    mini = staticmethod(mini)
    tar_gz = staticmethod(tar_gz)
    validate = staticmethod(openapi_validate)
    tokens = TOKENS

    @property
    def svc(self) -> Any:
        return self.app.state.service

    async def put_project(self, project: str | None = None) -> httpx.Response:
        resp = await self.http.put(
            f"/v1/projects/{project or self.project}", json={"name": "Mini", "llmPolicy": "local-only"}
        )
        assert resp.status_code == 200, resp.text
        return resp

    async def ingest(self, files: dict[str, bytes], mode: str = "full", **headers: str) -> httpx.Response:
        entries = [{"path": p, "sha256": hashlib.sha256(d).hexdigest()} for p, d in files.items()]
        manifest = {"mode": mode, "kindRules": KIND_RULES, "files": entries}
        check = await self.http.post(f"/v1/projects/{self.project}/ingestions/check", json=manifest)
        assert check.status_code == 200, check.text
        openapi_validate(CHECK_SCHEMA, check.json())
        missing = set(check.json()["missing"])
        upload = {p: d for p, d in files.items() if hashlib.sha256(d).hexdigest() in missing}
        parts: dict[str, Any] = {"manifest": (None, json.dumps(manifest), "application/json")}
        if upload:
            parts["archive"] = ("a.tar.gz", tar_gz(upload), "application/octet-stream")
        return await self.http.post(f"/v1/projects/{self.project}/ingestions", files=parts, headers=headers)

    async def events(self, type: str) -> list[dict[str, Any]]:
        """All events of this project and type in the stream."""
        nc = await nats.connect(self.nats_url)
        try:
            js = nc.jetstream()
            state = (await js.stream_info("SANDMAN")).state
            found = []
            for seq in range(state.first_seq, state.last_seq + 1):
                env = json.loads((await js.get_msg("SANDMAN", seq)).data or b"{}")
                if env.get("type") == type and env.get("subject") == self.project:
                    found.append(env)
            return found
        finally:
            await nc.close()

    async def extract(self, version_ids: list[str], options: dict[str, Any] | None = None) -> TaskSnapshot:
        client = A2aClient(TOKENS["orchestrator"], transport=httpx.ASGITransport(app=self.app))
        try:
            payload: dict[str, Any] = {"projectId": self.project, "artifactVersionIds": version_ids}
            if options is not None:
                payload["options"] = options
            task = await client.submit(URL, "extract-artifact-versions", payload)
            for _ in range(300):
                if task.terminal:
                    return task
                await asyncio.sleep(0.1)
                task = await client.get(URL, task.task_id)
            raise TimeoutError(task)
        finally:
            await client.close()


@pytest.fixture
async def env(memgraph: str, nats_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Env]:
    monkeypatch.setenv("SANDMAN_CONVERTER_URL", "http://document-converter:8000")
    converter = FakeConverter(tmp_path / "store")
    (tmp_path / "token").write_text("inbound")
    config = {
        "plugins": [{"id": "code-extractor", "transport": "a2a", "url": "http://plugin:8000",
                     "skill": "sandman.plugin.extract", "tokenFile": str(tmp_path / "token"),
                     "match": {"kinds": ["code", "test"]}, "timeout": "1m"}],
        "explicitReferences": {"patterns": [r"REQ-\d+"]},
    }  # fmt: skip
    (tmp_path / "config" / "extraction").mkdir(parents=True)
    raw = yaml.safe_dump(config).encode()
    (tmp_path / "config" / "extraction" / "plugins.yaml").write_bytes(raw)
    (tmp_path / "config" / "extraction" / "enrichment.yaml").write_text("enabled: false\nmaxElementsPerRun: 50\n")
    plugin = FakePlugin()
    plugin_app = serve(
        DEFAULT_CONTRACTS_DIR / "agent-cards" / "plugin-extract.template.json",
        [plugin],
        Settings(service_name="plugin", plugin_inbound_token="inbound"),
    )
    settings = Settings(
        service_name="extraction-agent",
        kg_uri=memgraph,
        nats_url=nats_url,
        artifact_store=tmp_path / "store",
        service_token="ea-token",
        config_dir=tmp_path / "config",
        llm_models_file=LLM_MODELS,
        llm_base_url="http://llm-replay:8000/openai",
        llm_key="cloud-key",
        llm_local_key="local-key",
        service_verify={name: hashlib.sha256(t.encode()).hexdigest() for name, t in TOKENS.items()},
    )
    app = create_app(settings, httpx.ASGITransport(app=plugin_app), converter_transport=httpx.MockTransport(converter))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {TOKENS['cli']}"}
        async with httpx.AsyncClient(transport=transport, base_url=URL, headers=headers) as http:
            yield Env(http, app, plugin, nats_url, hashlib.sha256(raw).hexdigest(), converter)

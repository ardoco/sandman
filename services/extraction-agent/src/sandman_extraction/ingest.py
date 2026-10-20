"""Artifact intake: manifest checks, safe archive reading, the versioning transaction, event (re-)publishing."""

import io
import re
import stat
import tarfile
import uuid
import zipfile
import zlib
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any, Literal, LiteralString

from neo4j import AsyncManagedTransaction
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sandman_agent import ArtifactStore, Contracts, EventSink, KgClient, stable_id
from sandman_agent.ids import sha256_hex
from sandman_agent.kg import now
from starlette.exceptions import HTTPException

from sandman_extraction import knowledge

MAX_FILES = 20_000
MAX_ENTRY_BYTES = 256 * 1024**2
SPECIAL_MODES = (stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK)
_DRIVE = re.compile(r"^[A-Za-z]:")
INGESTION_COMPLETED = "sandman.ingestion.completed"
RESERVED_PREFIX = ".sandman/"
KNOWLEDGE_PATH = "sandman-knowledge.yaml"
KNOWLEDGE_FORMAT = "sandman-knowledge"
Kind = Literal["requirements", "architecture-doc", "architecture-model", "code", "test", "other"]


def safe_path(path: str) -> str:
    """Relative path with forward slashes and no empty, '.' or '..' segments."""
    if not path or path.startswith("/") or "\\" in path or "\0" in path or _DRIVE.match(path):
        raise ValueError(f"unsafe path {path!r}")
    if any(s in ("", ".", "..") for s in path.split("/")):
        raise ValueError(f"unsafe path {path!r}")
    return path


class KindRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    glob: str
    kind: Kind
    format: str | None = None


class ManifestFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def _safe(cls, path: str) -> str:
        return safe_path(path)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["full", "partial"]
    sourceRevision: str | None = None
    kindRules: list[KindRule] = []
    files: list[ManifestFile] = Field(max_length=MAX_FILES)

    @field_validator("files")
    @classmethod
    def _unique(cls, files: list[ManifestFile]) -> list[ManifestFile]:
        if len({f.path for f in files}) != len(files):
            raise ValueError("duplicate paths")
        return files

    def kind_of(self, path: str) -> tuple[str, str | None]:
        """(kind, format) of the first matching kind rule, else ('other', None)."""
        for rule in self.kindRules:
            if PurePosixPath(path).full_match(rule.glob):
                return rule.kind, rule.format
        return "other", None


def reject_reserved(manifest: Manifest) -> None:
    """INVALID_INPUT for every path under the prefix reserved for derived artifacts."""
    reserved = sorted(f.path for f in manifest.files if f.path.startswith(RESERVED_PREFIX))
    if reserved:
        raise HTTPException(400, f"INVALID_INPUT: paths under {RESERVED_PREFIX} are reserved, e.g. {reserved[0]}")


def read_archive(
    data: bytes, max_bytes: int, max_entries: int = 2 * MAX_FILES, max_entry_bytes: int = MAX_ENTRY_BYTES
) -> dict[str, bytes]:
    """Regular files of a tar(.gz) or zip archive; rejects links, special files, unsafe paths and oversize.

    Entry data is read in bounded steps: at most the remaining total and per-entry budget plus one byte, so a lying
    zip header or a compression bomb never allocates more than the caps.
    """
    files: dict[str, bytes] = {}
    total = 0

    def add(name: str, open_entry: Any) -> None:
        nonlocal total
        try:
            path = safe_path(name.removeprefix("./"))
        except ValueError as e:
            raise HTTPException(400, f"archive: {e}") from None
        if path in files:
            raise HTTPException(400, f"archive: duplicate entry {path}")
        limit = min(max_entry_bytes, max_bytes - total)
        with open_entry() as f:
            content = f.read(limit + 1)
        if len(content) > max_entry_bytes:
            raise HTTPException(400, f"archive: entry {path} exceeds {max_entry_bytes} bytes")
        total += len(content)
        if total > max_bytes:
            raise HTTPException(400, f"archive content exceeds {max_bytes} bytes")
        files[path] = content

    if zipfile.is_zipfile(io.BytesIO(data)):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                infos = zf.infolist()
                if len(infos) > max_entries:
                    raise HTTPException(400, "archive has too many entries")
                for info in infos:
                    if stat.S_IFMT(info.external_attr >> 16) in SPECIAL_MODES:
                        raise HTTPException(400, f"archive: {info.filename} is not a regular file")
                    if not info.is_dir():
                        add(info.filename, lambda i=info: zf.open(i))
        except (zipfile.BadZipFile, zlib.error, NotImplementedError, EOFError, RuntimeError) as e:
            raise HTTPException(400, f"archive is corrupt: {e}") from None
        return files
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except tarfile.TarError:
        raise HTTPException(400, "archive is neither tar(.gz) nor zip") from None
    try:
        with tf:
            for n, member in enumerate(tf):
                if n >= max_entries:
                    raise HTTPException(400, "archive has too many entries")
                if member.isdir():
                    continue
                if not member.isreg() or member.issparse():
                    raise HTTPException(400, f"archive: {member.name} is not a regular file")
                try:
                    tarfile.data_filter(member, "/archive")
                except tarfile.FilterError as e:
                    raise HTTPException(400, f"archive: {e}") from None
                add(member.name, lambda m=member: tf.extractfile(m))
    except (tarfile.TarError, EOFError, OSError, zlib.error) as e:
        raise HTTPException(400, f"archive is corrupt: {e}") from None
    return files


def manifest_of(archive: dict[str, bytes]) -> Manifest:
    """Partial manifest of an archive uploaded without one: every file hashed, no kind rules, no deletions."""
    return Manifest(mode="partial", files=[ManifestFile(path=p, sha256=sha256_hex(d)) for p, d in archive.items()])


async def rows(tx: AsyncManagedTransaction, cypher: LiteralString, **params: Any) -> list[dict[str, Any]]:
    """Run one statement inside a transaction; rows as dicts."""
    return await (await tx.run(cypher, params)).data()


def missing_hashes(store: ArtifactStore, project_id: str, manifest: Manifest) -> list[str]:
    """Hashes of the manifest whose blobs the store does not hold."""
    hashes = sorted({f.sha256 for f in manifest.files})
    return [h for h in hashes if not store.path_for(f"artifact://{project_id}/{h}").exists()]


async def ingest(
    kg: KgClient,
    store: ArtifactStore,
    project_id: str,
    manifest: Manifest,
    archive: dict[str, bytes],
    actor: str,
    derived: Mapping[str, tuple[str, str]] | None = None,
    retire: Sequence[str] = (),
    contracts: Contracts | None = None,
) -> str:
    """Store blobs and apply the manifest in one transaction; return the ingestion id.

    Only the internal derivation passes `derived` (path -> source version id, derivation run id) and `retire`
    (artifact ids to mark deleted). A full snapshot never deletes an artifact with origin derived. An archive entry
    KNOWLEDGE_PATH is validated first when `contracts` is given (the HTTP route always gives it).
    """
    derived = derived or {}
    wanted = {f.path: f.sha256 for f in manifest.files}
    for path, data in archive.items():
        if path not in wanted:
            raise HTTPException(422, f"archive entry {path} is not in the manifest")
        if sha256_hex(data) != wanted[path]:
            raise HTTPException(422, f"sha256 mismatch for {path}")
    unknown = set(missing_hashes(store, project_id, manifest)) - {wanted[p] for p in archive}
    if unknown:
        raise HTTPException(422, f"archive lacks content for {len(unknown)} unknown hashes")
    if contracts is not None and KNOWLEDGE_PATH in archive:
        try:
            knowledge.validate(archive[KNOWLEDGE_PATH], contracts)
        except knowledge.KnowledgeError as e:
            raise HTTPException(400, f"INVALID_INPUT: {KNOWLEDGE_PATH}: {e}") from None
    for data in archive.values():
        store.put(project_id, data)

    async def apply(tx: AsyncManagedTransaction) -> str:
        seq_rows = await rows(
            tx,
            "MATCH (p:Project {id: $p}) SET p.ingestionSeq = p.ingestionSeq + 1 RETURN p.ingestionSeq AS seq",
            p=project_id,
        )
        if not seq_rows:
            raise HTTPException(404, f"project {project_id} not found")
        seq = int(seq_rows[0]["seq"])
        ing_id = stable_id("ing", project_id, str(seq))
        current = {
            r["id"]: (r["path"], r["current"], r["origin"], r["derivedFrom"])
            for r in await rows(
                tx,
                "MATCH (a:Artifact {projectId: $p}) OPTIONAL MATCH (a)-[:CURRENT_VERSION]->(v) "
                "RETURN a.id AS id, a.path AS path, v.id AS current, a.origin AS origin, v.derivedFrom AS derivedFrom",
                p=project_id,
            )
        }
        items, unchanged = [], 0
        for f in manifest.files:
            art = stable_id("art", project_id, f.path)
            ver = stable_id("ver", art, f.sha256)
            source, run = derived.get(f.path, (None, None))
            _, now_current, _, now_source = current.get(art, (None, None, None, None))
            if now_current == ver and now_source == source:
                unchanged += 1
                continue
            kind, fmt = ("other", KNOWLEDGE_FORMAT) if f.path == KNOWLEDGE_PATH else manifest.kind_of(f.path)
            uri = f"artifact://{project_id}/{f.sha256}"
            size = store.path_for(uri).stat().st_size
            items.append({"art": art, "ver": ver, "path": f.path, "kind": kind, "format": fmt, "hash": f.sha256,
                          "uri": uri, "size": size, "origin": "derived" if source else None,
                          "derivedFrom": source, "derivationRunId": run})  # fmt: skip
        deleted = (
            [a for a, (path, cur, origin, _) in current.items()
             if cur is not None and path not in wanted and origin != "derived"]
            if manifest.mode == "full"
            else []
        )  # fmt: skip
        deleted += [a for a in retire if current.get(a, (None, None))[1] is not None and a not in deleted]
        props = {"id": ing_id, "projectId": project_id, "seq": seq, "mode": manifest.mode,
                 "sourceRevision": manifest.sourceRevision, "actor": actor, "unchangedCount": unchanged,
                 "eventPublished": not (items or deleted), "createdAt": now()}  # fmt: skip
        await rows(
            tx,
            "MATCH (p:Project {id: $p}) CREATE (p)-[:HAS_INGESTION]->(g:Ingestion) SET g = $props",
            p=project_id,
            props={k: v for k, v in props.items() if v is not None},
        )
        await rows(
            tx,
            "MATCH (p:Project {id: $p}), (g:Ingestion {id: $g}) UNWIND $items AS i "
            "MERGE (a:Artifact {id: i.art}) "
            "ON CREATE SET a.projectId = $p, a.path = i.path, a.kind = i.kind, a.format = i.format, "
            "a.origin = i.origin "
            "MERGE (p)-[:HAS_ARTIFACT]->(a) "
            "MERGE (v:ArtifactVersion {id: i.ver}) "
            "ON CREATE SET v.artifactId = i.art, v.projectId = $p, v.contentHash = i.hash, v.storageUri = i.uri, "
            "v.sizeBytes = i.size, v.ingestionId = $g, v.ingestedAt = $now, v.sourceRevision = $rev "
            "SET v.derivedFrom = coalesce(i.derivedFrom, v.derivedFrom), "
            "v.derivationRunId = coalesce(i.derivationRunId, v.derivationRunId) "
            "MERGE (a)-[:HAS_VERSION]->(v) "
            "CREATE (g)-[:ACTIVATED]->(v) "
            "REMOVE a.deletedAt "
            "WITH a, v OPTIONAL MATCH (a)-[c:CURRENT_VERSION]->(:ArtifactVersion) DELETE c "
            "WITH a, v CREATE (a)-[:CURRENT_VERSION]->(v)",
            p=project_id, g=ing_id, items=items, now=props["createdAt"], rev=manifest.sourceRevision,
        )  # fmt: skip
        await rows(
            tx,
            "MATCH (g:Ingestion {id: $g}) UNWIND $ids AS aid MATCH (a:Artifact {id: aid})-[c:CURRENT_VERSION]->() "
            "DELETE c SET a.deletedAt = $now CREATE (g)-[:DELETED]->(a)",
            g=ing_id, ids=deleted, now=props["createdAt"],
        )  # fmt: skip
        return ing_id

    return await kg.write_tx(apply)


async def load_ingestion(kg: KgClient, project_id: str, ingestion_id: str) -> dict[str, Any] | None:
    """Ingestion properties plus the created/reactivated version and deleted artifact id lists."""
    found = await kg.read(
        "MATCH (g:Ingestion {id: $id, projectId: $p}) "
        "OPTIONAL MATCH (g)-[:ACTIVATED]->(v:ArtifactVersion) WITH g, collect(v) AS vs "
        "OPTIONAL MATCH (g)-[:DELETED]->(a:Artifact) "
        "RETURN properties(g) AS g, [v IN vs WHERE v.ingestionId = g.id | v.id] AS created, "
        "[v IN vs WHERE v.ingestionId <> g.id | v.id] AS reactivated, collect(a.id) AS deleted",
        id=ingestion_id,
        p=project_id,
    )
    if not found:
        return None
    r = found[0]
    return {**r["g"], "createdVersionIds": sorted(r["created"]), "reactivatedVersionIds": sorted(r["reactivated"]),
            "deletedArtifactIds": sorted(r["deleted"])}  # fmt: skip


def response(ing: dict[str, Any]) -> dict[str, Any]:
    """OpenAPI Ingestion."""
    keys = ("projectId", "seq", "mode", "sourceRevision", "createdVersionIds", "reactivatedVersionIds",
            "deletedArtifactIds", "unchangedCount", "createdAt")  # fmt: skip
    return {"ingestionId": ing["id"], **{k: ing[k] for k in keys if k in ing}}


async def publish(kg: KgClient, events: EventSink, ing: dict[str, Any]) -> None:
    """Publish ingestion.completed with an id derived from the ingestion id, then mark it published."""
    data = {k: v for k, v in response(ing).items() if k != "createdAt"} | {"actor": ing["actor"]}
    await events.publish(
        INGESTION_COMPLETED, ing["projectId"], data, event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, ing["id"]))
    )
    await kg.write("MATCH (g:Ingestion {id: $id}) SET g.eventPublished = true", id=ing["id"])


async def republish_pending(kg: KgClient, events: EventSink) -> None:
    """Publish events of ingestions whose publish ack never arrived (same event id, so JetStream dedupes)."""
    pending = await kg.read(
        "MATCH (g:Ingestion {eventPublished: false}) RETURN g.id AS id, g.projectId AS p ORDER BY g.projectId, g.seq"
    )
    for r in pending:
        ing = await load_ingestion(kg, r["p"], r["id"])
        if ing is not None:
            await publish(kg, events, ing)

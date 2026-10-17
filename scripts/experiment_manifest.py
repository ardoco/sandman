"""Writes experiments/<name>/experiment.yaml (Stage 1 §11.6): Git commit (+ dirty flag), image digests, sha256 of every
file under config/ and of deploy/gateway/config.json, gateway model routes, project snapshot hashes, LLM mode, seeds.

Run (stack up, Bolt reachable): uv run python scripts/experiment_manifest.py --name <name> --project <id>
  [--llm-mode record|replay|live (default $SANDMAN_LLM_MODE)] [--uri bolt://127.0.0.1:7687]

Image digests come from `docker compose ps` of the running stack, snapshot hashes from the current ArtifactVersions of
the project in the KG. `build()` takes both as arguments so it is testable without Docker or Bolt.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "deploy" / "docker-compose.yml"
GATEWAY_CONFIG = Path("deploy/gateway/config.json")
SNAPSHOT_QUERY = (
    "MATCH (:Project {id: $p})-[:HAS_ARTIFACT]->(a:Artifact)-[:CURRENT_VERSION]->(v:ArtifactVersion) "
    "RETURN a.path AS path, v.contentHash AS hash"
)


def sha256_file(path: Path) -> str:
    """Hex sha256 of the file content."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def config_hashes(root: Path) -> dict[str, str]:
    """sha256 of every file under config/ and of the gateway config, keyed by repo-relative posix path."""
    files = sorted(p for p in (root / "config").rglob("*") if p.is_file()) + [root / GATEWAY_CONFIG]
    return {p.relative_to(root).as_posix(): sha256_file(p) for p in files}


def seeds(root: Path) -> dict[str, Any]:
    """Every `seed` key found in config/**/*.yaml, keyed by file."""
    found: dict[str, Any] = {}

    def walk(node: Any, path: str, file: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "seed":
                    found[f"{file}:{path}seed"] = v
                walk(v, f"{path}{k}.", file)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}{i}.", file)

    for p in sorted((root / "config").rglob("*.y*ml")):
        walk(yaml.safe_load(p.read_text()), "", p.relative_to(root).as_posix())
    return found


def git_state(root: Path) -> tuple[str, bool]:
    """(HEAD commit, working tree dirty)."""
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True)
    return commit.stdout.strip(), bool(status.stdout.strip())


def docker_images(compose_file: Path = COMPOSE) -> dict[str, dict[str, Any]]:
    """Per running compose service: image reference, image id and repo digests."""

    def docker(*args: str) -> Any:
        return json.loads(subprocess.run(["docker", *args], capture_output=True, text=True, check=True).stdout)

    ps = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "--profile", "*", "ps", "-q"],
        capture_output=True,
        text=True,
        check=True,
    )
    out: dict[str, dict[str, Any]] = {}
    for cid in ps.stdout.split():
        c = docker("inspect", cid)[0]
        image = docker("image", "inspect", c["Image"])[0]
        service = c["Config"]["Labels"].get("com.docker.compose.service", c["Name"].lstrip("/"))
        out[service] = {"image": c["Config"]["Image"], "id": c["Image"], "repoDigests": sorted(image["RepoDigests"])}
    return dict(sorted(out.items()))


def snapshot_hashes(uri: str, project: str) -> dict[str, Any]:
    """Current ArtifactVersion content hashes of the project plus one hash over all of them."""
    from kg_canonical_export import connect

    with connect(uri) as driver:
        rows = driver.execute_query(SNAPSHOT_QUERY, p=project).records
    artifacts = {r["path"]: r["hash"] for r in sorted(rows, key=lambda r: r["path"])}
    digest = hashlib.sha256(json.dumps(artifacts, sort_keys=True).encode()).hexdigest()
    return {"project": project, "snapshotHash": digest, "artifacts": artifacts}


def evaluation(root: Path, gold: Sequence[Path]) -> dict[str, Any]:
    """experiments/PROTOCOL.md hash and version, plus sha256 of every gold standard file."""
    protocol = root / "experiments" / "PROTOCOL.md"
    text = protocol.read_text(encoding="utf-8")
    version = re.search(r"^Version: (\d+)", text, re.MULTILINE)
    return {
        "protocolSha256": sha256_file(protocol),
        "protocolVersion": int(version.group(1)) if version else None,
        "gold": {str(g): sha256_file(g) for g in gold},
    }


def build(
    root: Path,
    name: str,
    llm_mode: str,
    commit: str,
    dirty: bool,
    images: dict[str, Any],
    snapshot: dict[str, Any],
    gold: Sequence[Path] = (),
) -> dict[str, Any]:
    """The manifest document."""
    routes = yaml.safe_load((root / "config/llm/models.yaml").read_text())["routes"]
    return {
        "experiment": name,
        "git": {"commit": commit, "dirty": dirty},
        "images": images,
        "configFiles": config_hashes(root),
        "routes": routes,
        "snapshot": snapshot,
        "llmMode": llm_mode,
        "seeds": seeds(root),
        "evaluation": evaluation(root, gold),
    }


def write(root: Path, manifest: dict[str, Any]) -> Path:
    """Write experiments/<name>/experiment.yaml."""
    target = root / "experiments" / str(manifest["experiment"]) / "experiment.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(manifest, sort_keys=False))
    return target


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("--name", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--llm-mode", default=os.environ.get("SANDMAN_LLM_MODE"), choices=["live", "record", "replay"])
    ap.add_argument("--uri", default="bolt://127.0.0.1:7687")
    ap.add_argument("--gold", type=Path, action="append", default=[], help="gold standard file (repeatable)")
    args = ap.parse_args(argv)
    if not args.llm_mode:
        ap.error("--llm-mode or SANDMAN_LLM_MODE required")
    commit, dirty = git_state(ROOT)
    manifest = build(
        ROOT,
        args.name,
        args.llm_mode,
        commit,
        dirty,
        docker_images(),
        snapshot_hashes(args.uri, args.project),
        args.gold,
    )
    print(write(ROOT, manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

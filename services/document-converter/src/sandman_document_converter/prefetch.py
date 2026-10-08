"""Build-time download of the Docling models used by `worker` (layout and table structure; no OCR).

Models are fetched at pinned Hugging Face commits and every file is verified against a pinned sha256; any mismatch,
missing or extra file fails the build. Afterwards a bundled PDF is converted offline with an empty Hugging Face
cache, which proves the models come only from the models directory. Update the pins together with docling.

Models and licenses (V-13), see also THIRD-PARTY.md:
- docling-project/docling-layout-heron @ 8f39ad3c0b4c58e9c2d2c84a38465abf757272d8, Apache-2.0
- docling-project/docling-models (TableFormer accurate) @ fc0f2d45e2218ea24bce5045f58a389aed16dc23 (tag v2.3.0),
  CDLA-Permissive-2.0
"""

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from sandman_document_converter.worker import MODELS_ENV, models_dir

SELFCHECK = Path(__file__).with_name("selfcheck.pdf")
EXPECTED = "prefetch self-check"
HERON = "docling-project/docling-layout-heron"
TABLEFORMER = "docling-project/docling-models"
# repo id -> (commit, allow_patterns, {file relative to the repo folder: sha256})
PINS: dict[str, tuple[str, list[str], dict[str, str]]] = {
    HERON: (
        "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8",
        ["config.json", "preprocessor_config.json", "model.safetensors"],
        {
            "config.json": "fdea30805ce2f5666b147fca941dcdd27ad468e27d6ed21902207d3da056a97d",
            "preprocessor_config.json": "cd38cd59999e7a95d68e487fbe5132df3d4e5c32a0836add57e6126ba0c4eaf1",
            "model.safetensors": "00333a43451945aaf89db8ca9c0a17e75d1537c17db60fdb91aa95f4c7929e0c",
        },
    ),
    TABLEFORMER: (
        "fc0f2d45e2218ea24bce5045f58a389aed16dc23",
        ["model_artifacts/tableformer/accurate/*"],
        {
            "model_artifacts/tableformer/accurate/tableformer_accurate.safetensors":
                "2a7d6c924b3cd12fb99a09280ca9c33a89c5d60b93253617d2e088c1a40374d9",
            "model_artifacts/tableformer/accurate/tm_config.json":
                "984e122ceb8ccf84d84c9d2882f6f2302a44b4f1e577babd6289892c36f3cffd",
        },
    ),
}  # fmt: skip


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def verify(folder: Path, expected: dict[str, str]) -> None:
    """Raise unless `folder` contains exactly the expected files (ignoring the download cache) with these hashes."""
    found = {
        rel.as_posix(): p
        for p in folder.rglob("*")
        if p.is_file() and ".cache" not in (rel := p.relative_to(folder)).parts
    }
    if set(found) != set(expected):
        raise SystemExit(f"{folder}: files {sorted(found)} differ from pinned {sorted(expected)}")
    for name, digest in expected.items():
        if (actual := sha256(found[name])) != digest:
            raise SystemExit(f"{folder}/{name}: sha256 {actual} differs from pinned {digest}")


def download(target: Path) -> None:
    from huggingface_hub import snapshot_download

    for repo, (commit, patterns, files) in PINS.items():
        folder = target / repo.replace("/", "--")  # the layout docling reads from artifacts_path
        snapshot_download(repo_id=repo, revision=commit, allow_patterns=patterns, local_dir=folder)
        verify(folder, files)


def main() -> None:
    target = models_dir()
    target.mkdir(parents=True, exist_ok=True)
    download(target)
    with tempfile.TemporaryDirectory() as empty:
        offline = {
            **os.environ,
            MODELS_ENV: str(target),
            "HF_HUB_OFFLINE": "1",
            "HF_HOME": empty,
            "HF_HUB_CACHE": empty,
        }
        done = subprocess.run(
            [sys.executable, "-m", "sandman_document_converter.worker", str(SELFCHECK)],
            env=offline, capture_output=True, text=True, timeout=600, check=False,
        )  # fmt: skip
    if done.returncode != 0 or EXPECTED not in done.stdout:
        sys.exit(f"offline self-check failed ({done.returncode}): {done.stderr[-500:]}{done.stdout[:200]}")
    print(f"offline self-check ok: {done.stdout.strip()!r} from {target}")


if __name__ == "__main__":
    main()

"""Build-time download of the Docling models used by `documents` (layout and table structure; no OCR).

Run `python -m sandman_extraction.prefetch` while the network is available; afterwards a bundled PDF is converted
offline from the models directory as a self-check, so a build with missing models fails.
Licenses (V-13): layout docling-project/docling-layout-heron Apache-2.0; TableFormer docling-project/docling-models
CDLA-Permissive-2.0; docling MIT; torch BSD-3-Clause.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from sandman_extraction.documents import MODELS_ENV, models_dir

SELFCHECK = Path(__file__).with_name("selfcheck.pdf")
EXPECTED = "prefetch self-check"


def download(target: Path) -> None:
    from docling.utils.model_downloader import download_models

    download_models(
        target,
        with_layout=True,
        with_tableformer=True,
        with_code_formula=False,
        with_picture_classifier=False,
        with_rapidocr=False,
    )
    for unused in (*target.glob("*-onnx"), *target.glob("*/model_artifacts/tableformer/fast")):
        shutil.rmtree(unused)  # engines/modes the configured pipeline does not use


def main() -> None:
    target = models_dir()
    target.mkdir(parents=True, exist_ok=True)
    download(target)
    offline = {**os.environ, MODELS_ENV: str(target), "HF_HUB_OFFLINE": "1"}
    done = subprocess.run(
        [sys.executable, "-m", "sandman_extraction.documents", str(SELFCHECK)],
        env=offline, capture_output=True, text=True, timeout=600, check=False,
    )  # fmt: skip
    if done.returncode != 0 or EXPECTED not in done.stdout:
        sys.exit(f"offline self-check failed ({done.returncode}): {done.stderr[-500:]}{done.stdout[:200]}")
    print(f"offline self-check ok: {done.stdout.strip()!r} from {target}")


if __name__ == "__main__":
    main()

# Third-party components of the document-converter image (T1.4, V-13)

All licenses allow redistribution in a Docker image. Re-check on every Docling upgrade (models, pins in
`src/sandman_document_converter/prefetch.py`, lock in `uv.lock`).

## Models (fetched at image build, pinned commit and sha256, no OCR)

| Model | Pinned revision | License |
|---|---|---|
| `docling-project/docling-layout-heron` (layout) | `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8` | Apache-2.0 |
| `docling-project/docling-models` (TableFormer, accurate) | `fc0f2d45e2218ea24bce5045f58a389aed16dc23` (tag v2.3.0) | CDLA-Permissive-2.0 |

## Python packages (versions as locked)

| Package | License |
|---|---|
| docling 2.135.0, docling-core, docling-ibm-models, docling-parse | MIT |
| pypdfium2 | BSD-3-Clause / Apache-2.0 (bundles PDFium, BSD-3-Clause / Apache-2.0) |
| torch 2.14.1 (CPU wheel) | BSD-3-Clause (plus bundled Apache-2.0, MIT, BSL-1.0 components) |
| torchvision 0.29.1 (CPU wheel) | BSD-3-Clause |
| transformers, tokenizers, safetensors, huggingface-hub, accelerate, rapidocr (unused, OCR is off) | Apache-2.0 |
| opencv-python-headless | Apache-2.0; the wheel bundles FFmpeg libraries under LGPL-2.1 (dynamically linked, unmodified) |
| numpy | BSD-3-Clause (with 0BSD, MIT, Zlib, CC0 components) |
| scipy, pandas, sympy, mpmath, networkx, jinja2, shapely, lxml, omegaconf | BSD |
| pillow | MIT-CMU |
| python-docx, python-pptx, openpyxl, pydantic, typer, marko, beautifulsoup4, latex2mathml, tree-sitter | MIT |
| requests | Apache-2.0 |
| tqdm | MPL-2.0 / MIT (unmodified) |

import json
import shutil
from pathlib import Path

from validate_contracts import main

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"


def test_repository_contracts_are_valid() -> None:
    assert main(CONTRACTS) == []


def test_broken_schema_and_vector_are_reported(tmp_path: Path) -> None:
    copy = tmp_path / "contracts"
    shutil.copytree(CONTRACTS, copy)
    vectors = copy / "conformance/vectors.json"
    v = json.loads(vectors.read_text())
    v["fingerprint"]["cases"][0]["expected"] = "0" * 64
    vectors.write_text(json.dumps(v))
    assert any(e.startswith("fingerprint") for e in main(copy))

    common = copy / "common.schema.json"
    common.write_text(common.read_text().replace('"type": "string"', '"type": "strng"', 1))
    assert any(e.startswith("common.schema.json") for e in main(copy))

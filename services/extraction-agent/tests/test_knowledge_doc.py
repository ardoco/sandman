"""T7.2: the example in docs/user/knowledge-file.md is a valid knowledge file."""

import re
from pathlib import Path

from sandman_agent import Contracts
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_extraction import knowledge

DOC = Path(__file__).parents[3] / "docs" / "user" / "knowledge-file.md"


def test_doc_example_validates() -> None:
    example = re.search(r"```yaml\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)
    assert example
    data = knowledge.validate(example.group(1).encode(), Contracts(DEFAULT_CONTRACTS_DIR))
    assert {"elements", "aliases", "links"} <= data.keys()

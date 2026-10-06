from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_agent.contracts import Contracts
from sandman_agent.testing import run_conformance


def test_python_library_matches_conformance_vectors() -> None:
    # skillInvocation cases: libs/python/sandman-agent/tests/test_a2a.py (needs a served skill)
    contracts = Contracts(DEFAULT_CONTRACTS_DIR)
    assert run_conformance(DEFAULT_CONTRACTS_DIR / "conformance/vectors.json", contracts) == []

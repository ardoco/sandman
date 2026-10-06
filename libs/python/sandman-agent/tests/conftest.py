import hashlib

import pytest
from sandman_agent.config import DEFAULT_CONTRACTS_DIR, Settings
from sandman_agent.contracts import Contracts

TOKENS = {"orchestrator": "orch-token", "tlr-agent": "tlr-token", "plugin:plugin-stubs": "plugin-token"}


@pytest.fixture(scope="session")
def contracts() -> Contracts:
    return Contracts(DEFAULT_CONTRACTS_DIR)


@pytest.fixture
def settings() -> Settings:
    verify = {name: hashlib.sha256(token.encode()).hexdigest() for name, token in TOKENS.items()}
    return Settings(service_name="tlr-agent", service_verify=verify)

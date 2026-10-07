"""Fixtures; the fakes live in fakes.py (importlib import mode does not put tests/ on sys.path)."""

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fakes import CARDS, DEFAULT_CONTRACTS_DIR, Bus, Contracts, Env, FakeSkill, Router  # noqa: E402


@pytest.fixture(scope="session")
def contracts() -> Contracts:
    return Contracts(DEFAULT_CONTRACTS_DIR)


@pytest.fixture
async def env(contracts: Contracts, tmp_path: Path) -> AsyncIterator[Env]:
    bus = Bus(contracts, "fake-agent")
    e = Env(None, {s: FakeSkill(s) for s in CARDS}, Router(), bus, contracts, tmp_path / "orchestrator.db")  # type: ignore[arg-type]
    for skill in CARDS:
        e.restart_agent(skill)
    e.new_orchestrator(e.path)
    yield e
    bus.target = None
    for s in e.skills.values():
        s.release.set()
    e.orch.close()

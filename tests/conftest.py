import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


import pytest


@pytest.fixture(autouse=True)
def _isolated_proposer_health(tmp_path, monkeypatch):
    """Tests never touch agent_memory/proposer_health.json."""
    from agent import proposer_health
    monkeypatch.setattr(proposer_health, "PATH", tmp_path / "proposer_health.json")


@pytest.fixture(autouse=True)
def _no_key_probe(monkeypatch):
    """alpaca_keys.data_keys() never calls the network in tests; its cache starts empty."""
    import alpaca_keys
    alpaca_keys.clear_cache()
    monkeypatch.setattr(alpaca_keys, "works", lambda *a, **k: True)

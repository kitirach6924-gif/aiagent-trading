import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings  # noqa: E402


@pytest.fixture()
def fresh_db(tmp_path):
    from app.core.db import Database
    return Database(str(tmp_path / "test.db"))


@pytest.fixture()
def sim():
    from app.mt5.simulator import SimulatorConnector
    return SimulatorConnector(symbols=["XAUUSD"])


@pytest.fixture()
def registry(fresh_db):
    from app.core.strategies import StrategyRegistry
    reg = StrategyRegistry(fresh_db)
    reg.create(version="v1.0", name="base", status="APPROVED", rules=[
        {"type": "EMA_CROSS", "params": {"fast": 9, "slow": 21}},
        {"type": "RSI_FILTER", "params": {"period": 14, "oversold": 35, "overbought": 65}},
        {"type": "ATR_STOP", "params": {"period": 14, "mult": 2.0}},
    ], origin="test")
    reg.create(version="v1.1", name="with spread filter", status="PROPOSAL", rules=[
        {"type": "EMA_CROSS", "params": {"fast": 9, "slow": 21}},
        {"type": "SPREAD_FILTER", "params": {"max_points": 5}},
    ], base_version="v1.0", origin="test")
    reg.set_active_version("v1.0")
    return reg


@pytest.fixture()
def env_demo(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "DEMO")
    monkeypatch.setattr(settings, "trading_mode", "DEMO")
    monkeypatch.setattr(settings, "live_trading", False)
    monkeypatch.setattr(settings, "stop_loss_required", True)
    monkeypatch.setattr(settings, "max_lot", 0.1)
    monkeypatch.setattr(settings, "max_risk_percent", 5.0)
    monkeypatch.setattr(settings, "max_spread_points", 50.0)
    monkeypatch.setattr(settings, "max_open_positions", 3)
    monkeypatch.setattr(settings, "max_daily_loss_percent", 3.0)
    monkeypatch.setattr(settings, "max_drawdown_percent", 100.0)
    monkeypatch.setattr(settings, "max_consecutive_losses", 3)

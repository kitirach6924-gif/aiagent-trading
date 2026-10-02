import asyncio

import pytest

from app.core.agents import DecisionAgent, DecisionOutput, StrategyAgent
from app.core.journal import TradeJournal
from app.core.loop import TradingLoop
from app.core.risk import RiskGate
from app.core.strategy_engine import StrategyEngine
from app.mt5.base import Price, TradeResult
from app.mt5.simulator import SimulatorConnector


async def _always_buy(self, symbol, version=None):
    # SL tight enough to satisfy the default 1% risk cap on the 10k test account
    return DecisionOutput(decision="BUY", symbol=symbol, strategy_version=version or "v1.0",
                          reason="forced", lot=0.01, sl=2635.0, tp=2680.0)


async def _fixed_price(self, symbol):
    return Price(symbol=symbol, bid=2649.9, ask=2650.1, spread_points=15.0, time="now")


@pytest.mark.asyncio
async def test_duplicate_entry_suppressed_within_cooldown(fresh_db, registry, sim, env_demo, monkeypatch):
    """After an open, a second identical entry signal inside the cooldown must be skipped."""
    journal = TradeJournal(fresh_db, registry)
    gate = RiskGate(fresh_db)
    gate.set_day_start_balance(10_000.0)
    gate.update_equity_high_water(10_000.0)
    loop_ = TradingLoop(fresh_db, sim, registry, journal, gate)
    loop_.bind_agents(DecisionAgent(sim, StrategyAgent(StrategyEngine(registry), registry), registry))

    monkeypatch.setattr(DecisionAgent, "decide", _always_buy)
    monkeypatch.setattr(SimulatorConnector, "get_price", _fixed_price)

    counter = {"n": 0}

    async def fake_open(**kw):
        counter["n"] += 1
        return TradeResult(ok=True, ticket=f"t{counter['n']}", price=2650.0)

    monkeypatch.setattr(sim, "open_position", fake_open)

    await loop_._cycle()
    assert counter["n"] == 1, "first entry should fire"
    await loop_._cycle()
    await loop_._cycle()
    assert counter["n"] == 1, "duplicate entries inside cooldown must be suppressed"
    skipped = fresh_db.query("SELECT * FROM decisions WHERE risk_result='DUP-SKIP'")
    assert skipped, "suppressed duplicates must be recorded"


@pytest.mark.asyncio
async def test_entry_allowed_again_after_cooldown(fresh_db, registry, sim, env_demo, monkeypatch):
    journal = TradeJournal(fresh_db, registry)
    gate = RiskGate(fresh_db)
    gate.set_day_start_balance(10_000.0)
    loop_ = TradingLoop(fresh_db, sim, registry, journal, gate)
    loop_.bind_agents(DecisionAgent(sim, StrategyAgent(StrategyEngine(registry), registry), registry))
    monkeypatch.setattr(DecisionAgent, "decide", _always_buy)
    monkeypatch.setattr(SimulatorConnector, "get_price", _fixed_price)

    counter = {"n": 0}

    async def fake_open(**kw):
        counter["n"] += 1
        return TradeResult(ok=True, ticket=f"t{counter['n']}", price=2650.0)

    monkeypatch.setattr(sim, "open_position", fake_open)

    await loop_._cycle()
    # simulate cooldown expiry
    from datetime import datetime, timedelta, timezone
    key = loop_._fired_key("XAUUSD", "BUY", "v1.0")
    old = (datetime.now(timezone.utc) - timedelta(seconds=loop_.ENTRY_COOLDOWN_SECONDS + 5)).isoformat()
    fresh_db.kv_set(key, old)
    await loop_._cycle()
    assert counter["n"] == 2, "entry must be allowed again after cooldown expires"

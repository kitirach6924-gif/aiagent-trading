import asyncio

import pytest

from app.core.agents import DecisionAgent, StrategyAgent
from app.core.journal import StatisticsEngine, TradeJournal
from app.core.loop import TradingLoop
from app.core.risk import RiskGate
from app.core.strategy_engine import StrategyEngine


@pytest.mark.asyncio
async def test_full_cycle_opens_and_journals(fresh_db, registry, sim, env_demo):
    journal = TradeJournal(fresh_db, registry)
    gate = RiskGate(fresh_db)
    gate.set_day_start_balance(10_000.0)
    gate.update_equity_high_water(10_000.0)
    decision_agent = DecisionAgent(sim, StrategyAgent(StrategyEngine(registry), registry), registry)
    loop_ = TradingLoop(fresh_db, sim, registry, journal, gate)
    loop_.bind_agents(decision_agent)

    # force at least one cycle
    await loop_._cycle()
    status = loop_.status
    assert status["cycles"] >= 1
    # decisions recorded
    dec = fresh_db.query("SELECT * FROM decisions")
    assert dec, "each cycle must record decisions"
    # if a trade opened, it must be journaled
    trades = fresh_db.query("SELECT * FROM trades")
    opened = fresh_db.query("SELECT * FROM audit_log WHERE action='TRADE_OPEN'")
    assert (len(trades) > 0) == (len(opened) > 0)


@pytest.mark.asyncio
async def test_risk_block_logged_when_paused(fresh_db, registry, sim, env_demo):
    journal = TradeJournal(fresh_db, registry)
    gate = RiskGate(fresh_db)
    fresh_db.kv_set("paused", True)
    decision_agent = DecisionAgent(sim, StrategyAgent(StrategyEngine(registry), registry), registry)
    loop_ = TradingLoop(fresh_db, sim, registry, journal, gate)
    loop_.bind_agents(decision_agent)
    await loop_._cycle()
    # with pause on, decisions may exist but no trade opens
    trades = fresh_db.query("SELECT * FROM trades")
    assert not trades


@pytest.mark.asyncio
async def test_statistics_after_close(fresh_db, registry, sim, env_demo):
    journal = TradeJournal(fresh_db, registry)
    stats = StatisticsEngine(fresh_db)
    # simulate one closed trade
    ticket = "abc123"
    journal.record_open(symbol="XAUUSD", side="BUY", lot=0.1, entry_price=2650.0, sl=2640.0,
                        tp=2665.0, ticket=ticket, strategy_version="v1.0", mode="DEMO")
    journal.record_close(ticket=ticket, exit_price=2665.0, pnl=150.0, exit_reason="TP", mode="DEMO")
    s = stats.summary()
    assert s["trades"] == 1 and s["total_pnl"] == 150.0 and s["win_rate"] == 100.0
    per = fresh_db.query("SELECT * FROM strategy_performance WHERE version='v1.0'")
    assert per and per[0]["wins"] == 1

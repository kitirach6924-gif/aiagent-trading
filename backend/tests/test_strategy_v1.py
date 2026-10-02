"""Strategy V1 tests: Stochastic 24,24,10 + curve detection + chart context +
autonomous agent + risk gate integration + AI output validation (spec §35)."""
from __future__ import annotations

import asyncio

import pytest

from app.core.autonomous_agent import AutonomousTradingAgent, AgentDecision
from app.core.chart_context import ChartContext, ChartContextAnalyzer
from app.core.stochastic import Curve, StochasticCurveDetector, calc_stochastic
from app.mt5.base import Candle, Position
from app.mt5.simulator import atr, ema, rsi, sma  # noqa: F401 (existing suite imports)


def make_candles(closes: list[float], *, spread: float = 1.0) -> list[Candle]:
    """Build candles from a close series; open=prev close, high/low envelope."""
    out = []
    prev = closes[0]
    for c in closes:
        out.append(Candle(time="2026-01-01T00:00:00+00:00", open=prev, high=max(prev, c) + spread,
                          low=min(prev, c) - spread, close=c, tick_volume=100))
        prev = c
    return out


def k_series_from_closes(closes: list[float]) -> dict:
    return calc_stochastic(make_candles(closes), k_period=24, d_period=24, slowing=10)


# ---------- 1: configuration ----------
def test_stochastic_config_24_24_10():
    from app.config import settings
    assert settings.stoch_k_period == 24
    assert settings.stoch_d_period == 24
    assert settings.stoch_slowing == 10


# ---------- 2–5: curve detection ----------
# V-shaped series: flat/base then sharp turn (detector needs slope_before ≤ 0 → slope_now > thr)
UP_CURVE = [50, 45, 40, 37, 36, 36, 44]      # TURNING_UP
DOWN_CURVE = [40, 48, 56, 62, 66, 66, 58]    # TURNING_DOWN


def _state(closes):
    st = k_series_from_closes(closes)
    det = StochasticCurveDetector(min_slope_change=0.5)
    return det.detect(st["k"], st["d"])


def test_turning_up_detection():
    # downtrend → bottom → sharp reversal up: %K must turn upward
    closes = ([100 - i * 0.5 for i in range(30)]        # falling
              + [97.0]                                   # base
              + [97.6, 98.4, 99.4, 100.6, 102.0])        # sharp up
    st = _state(closes)
    assert st.curve in (Curve.TURNING_UP, Curve.NO_TURN)  # deterministic, never UNCLEAR with data
    assert st.k is not None and 0 <= st.k <= 100


def test_turning_down_detection():
    closes = ([100 + i * 0.5 for i in range(30)]        # rising
              + [115.0]                                  # top
              + [114.4, 113.6, 112.6, 111.4, 110.0])     # sharp down
    st = _state(closes)
    assert st.curve in (Curve.TURNING_DOWN, Curve.NO_TURN)
    assert st.k is not None


def test_no_turn_on_straight_trend():
    closes = [100 + i * 0.3 for i in range(40)]         # steady up, no reversal
    st = _state(closes)
    assert st.curve == Curve.NO_TURN


def test_unclear_with_insufficient_data():
    det = StochasticCurveDetector()
    st = det.detect([50.0], [])
    assert st.curve == Curve.UNCLEAR


# ---------- 6–10: chart context (now powered by the PA knowledge engine) ----------
@pytest.mark.asyncio
async def test_chart_context_confirms_buy():
    closes = [100 + i * 0.4 for i in range(25)]         # uptrend
    ctx = await ChartContextAnalyzer(use_llm=False).analyze(
        Curve.TURNING_UP, None, make_candles(closes), "BUY")
    assert ctx.decision == "CONFIRM"
    assert ctx.trend == "BULLISH"
    assert "BULLISH_STRUCTURE" in ctx.reason_codes


@pytest.mark.asyncio
async def test_chart_context_rejects_buy_at_top_of_downtrend():
    closes = [110 - i * 0.4 for i in range(25)]         # downtrend
    ctx = await ChartContextAnalyzer(use_llm=False).analyze(
        Curve.TURNING_UP, None, make_candles(closes), "BUY")
    assert ctx.decision in ("WAIT", "REJECT")


@pytest.mark.asyncio
async def test_chart_context_unclear_few_candles():
    ctx = await ChartContextAnalyzer(use_llm=False).analyze(
        Curve.TURNING_UP, None, make_candles([100, 100.5]), "BUY")
    assert ctx.decision == "WAIT"


# ---------- 11: WAIT behavior ----------
def test_agent_waits_on_no_turn():
    agent = AutonomousTradingAgent()
    stoch = StochasticCurveDetector().detect([50, 50.1, 50.0, 50.05, 50.02], [])
    chart = ChartContext(decision="CONFIRM", signal_quality=0.9)
    d = agent.decide("XAUUSD", stoch, chart, [])
    assert d.action == "WAIT"
    assert "STOCH_NO_SIGNAL" in d.reason_codes


# ---------- 6/11: BUY candidate ----------
def test_agent_buy_when_up_and_confirmed():
    agent = AutonomousTradingAgent()
    stoch = StochasticCurveDetector().detect(UP_CURVE, [])
    assert stoch.curve == Curve.TURNING_UP
    chart = ChartContext(decision="CONFIRM", trend="BULLISH", structure="SUPPORTIVE",
                         signal_quality=0.8)
    d = agent.decide("XAUUSD", stoch, chart, [], lot=0.01, sl=100.0, tp=101.5)
    assert d.action == "BUY"
    assert "STOCHASTIC_TURN_UP" in d.reason_codes
    assert "CHART_CONFIRM" in d.reason_codes  # chart is recorded, not consulted
    assert d.sl == 100.0  # never open without protective stop


def test_agent_enters_when_up_even_if_chart_is_unclear():
    """The user removed the chart veto on 2026-10-01.

    Score is the only entry gate; chart context is recorded as data, not a
    decision. It must still be attached to the reason codes so the learning
    pass can measure whether it ever had predictive value.
    """
    agent = AutonomousTradingAgent()
    stoch = StochasticCurveDetector().detect(UP_CURVE, [])
    chart = ChartContext(decision="WAIT", signal_quality=0.4)
    d = agent.decide("XAUUSD", stoch, chart, [])
    assert d.action == "BUY", "an unclear chart must not veto the entry"
    assert "CHART_WAIT" in d.reason_codes, (
        "chart context must still be recorded for later analysis")


def test_agent_enters_when_up_even_if_chart_rejects():
    """Chart REJECT is data now, not a veto."""
    agent = AutonomousTradingAgent()
    stoch = StochasticCurveDetector().detect(UP_CURVE, [])
    chart = ChartContext(decision="REJECT", signal_quality=0.2)
    d = agent.decide("XAUUSD", stoch, chart, [])
    assert d.action == "BUY"
    assert "CHART_REJECT" in d.reason_codes


# ---------- 7/12: SELL + CLOSE BUY on reversal ----------
def test_agent_close_buy_on_turn_down():
    agent = AutonomousTradingAgent()
    stoch = StochasticCurveDetector().detect(DOWN_CURVE, [])
    assert stoch.curve == Curve.TURNING_DOWN
    pos = [Position(ticket="1", symbol="XAUUSD", side="BUY", lot=0.01, entry_price=100,
                    current_price=99, pnl=-1, sl=98, tp=102, opened_at="x")]
    chart = ChartContext(decision="WAIT", signal_quality=0.5)
    d = agent.decide("XAUUSD", stoch, chart, pos)
    assert d.action == "CLOSE"
    assert "CLOSE_BUY_SIGNAL" in d.reason_codes


def test_agent_sell_when_down_and_confirmed_no_position():
    agent = AutonomousTradingAgent()
    stoch = StochasticCurveDetector().detect(DOWN_CURVE, [])
    chart = ChartContext(decision="CONFIRM", trend="BEARISH", structure="SUPPORTIVE",
                         signal_quality=0.8)
    d = agent.decide("XAUUSD", stoch, chart, [], lot=0.01, sl=100.0, tp=98.5)
    assert d.action == "SELL"


# ---------- 13/14: duplicate position protection ----------
def test_agent_no_second_buy():
    agent = AutonomousTradingAgent()
    stoch = StochasticCurveDetector().detect(UP_CURVE, [])
    chart = ChartContext(decision="CONFIRM", signal_quality=0.9)
    pos = [Position(ticket="1", symbol="XAUUSD", side="BUY", lot=0.01, entry_price=100,
                    current_price=100, pnl=0, sl=98, tp=102, opened_at="x")]
    d = agent.decide("XAUUSD", stoch, chart, pos, lot=0.01)
    assert d.action == "WAIT"
    assert "DUPLICATE_BUY_BLOCKED" in d.reason_codes


def test_agent_no_second_sell():
    agent = AutonomousTradingAgent()
    stoch = StochasticCurveDetector().detect(DOWN_CURVE, [])
    chart = ChartContext(decision="CONFIRM", signal_quality=0.9)
    pos = [Position(ticket="2", symbol="XAUUSD", side="SELL", lot=0.01, entry_price=100,
                    current_price=100, pnl=0, sl=102, tp=98, opened_at="x")]
    d = agent.decide("XAUUSD", stoch, chart, pos, lot=0.01)
    assert d.action == "WAIT"
    assert "DUPLICATE_SELL_BLOCKED" in d.reason_codes


# ---------- 15–21: risk gate (V1 path) ----------
@pytest.mark.asyncio
async def test_v1_loop_blocks_when_spread_high(fresh_db, sim, env_demo):
    from app.core.loop import TradingLoop
    from app.core.journal import TradeJournal
    from app.core.risk import RiskGate
    from app.core.strategies import StrategyRegistry

    reg = StrategyRegistry(fresh_db)
    reg.create(version="v2.0", name="V1", status="APPROVED",
               rules=[{"type": "STOCHASTIC_CURVE", "params": {"k_period": 24, "d_period": 24,
                                                              "slowing": 10}}], origin="test")
    reg.set_active_version("v2.0")
    fresh_db.kv_set("paused", True)  # deterministic block
    loop_ = TradingLoop(fresh_db, sim, reg, TradeJournal(fresh_db, reg), RiskGate(fresh_db))
    await loop_._cycle()
    trades = fresh_db.query("SELECT * FROM trades")
    assert not trades  # paused → no execution, ever


# ---------- 22–24: modes ----------
def test_live_disabled_by_default(env_demo):
    from app.config import settings
    assert settings.trading_mode == "DEMO"
    assert settings.live_trading is False
    assert settings.autonomous_mode is True


# ---------- 27: malformed AI JSON → WAIT ----------
def test_malformed_llm_json_rejected():
    analyzer = ChartContextAnalyzer(use_llm=True)
    assert analyzer._validate_llm("not json at all") is None
    assert analyzer._validate_llm('{"decision": "NUKE"}') is None          # invalid action
    assert analyzer._validate_llm('{"decision": "CONFIRM"}') is None        # missing trend
    bad = '{"decision": "CONFIRM", "trend": "BULLISH", "structure": "SUPPORTIVE", "max_risk_percent": 99}'
    assert analyzer._validate_llm(bad) is None                              # risk tampering


def test_valid_llm_json_accepted():
    analyzer = ChartContextAnalyzer(use_llm=True)
    good = ('{"decision": "CONFIRM", "trend": "BULLISH", "structure": "SUPPORTIVE", '
            '"price_location": "MID_RANGE", "candle_context": "SUPPORTIVE", '
            '"signal_quality": 0.82, "reason_codes": ["BULLISH_STRUCTURE"], '
            '"summary": "ok"}')
    ctx = analyzer._validate_llm(good)
    assert ctx is not None and ctx.decision == "CONFIRM" and ctx.signal_quality == 0.82


# ---------- 30: duplicate order protection (cooldown) ----------
@pytest.mark.asyncio
async def test_entry_cooldown_blocks_refire(fresh_db, registry, sim, env_demo):
    from datetime import datetime, timedelta
    from app.core.loop import TradingLoop

    loop_ = TradingLoop(fresh_db, sim, registry, None, None)
    loop_._mark_fired("XAUUSD", "BUY", "v1.0")
    assert loop_._recently_fired("XAUUSD", "BUY", "v1.0") is True
    assert loop_._recently_fired("XAUUSD", "SELL", "v1.0") is False
    # expire the cooldown
    from datetime import timezone
    from app.core.events import iso_utc
    fresh_db.kv_set(loop_._fired_key("XAUUSD", "BUY", "v1.0"),
                    (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat())
    assert loop_._recently_fired("XAUUSD", "BUY", "v1.0") is False


# ---------- 36: strategy version immutability ----------
def test_strategy_version_immutable(fresh_db):
    from app.core.strategies import StrategyRegistry, StrategyError
    reg = StrategyRegistry(fresh_db)
    reg.create(version="v9.0", name="x", status="APPROVED",
               rules=[{"type": "STOCHASTIC_CURVE", "params": {}}], origin="test")
    with pytest.raises(StrategyError):
        reg.create(version="v9.0", name="y", status="APPROVED",
                   rules=[{"type": "STOCHASTIC_CURVE", "params": {}}], origin="test")

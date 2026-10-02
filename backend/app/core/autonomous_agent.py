"""AutonomousTradingAgent (spec §10, §11–13).

Receives market/stochastic/chart-context/positions/account/risk state and
produces a structured autonomous decision (BUY/SELL/WAIT/CLOSE).
No human approval in DEMO. Risk Gate remains the final authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from app.config import settings
from app.core.chart_context import ChartContext
from app.core.stochastic import Curve, StochState, STRATEGY_V1_ID, STRATEGY_V1_VERSION
from app.mt5.base import Position

ACTIONS = ("BUY", "SELL", "WAIT", "CLOSE")


@dataclass
class AgentDecision:
    action: str = "WAIT"
    symbol: str = ""
    strategy_id: str = STRATEGY_V1_ID
    strategy_version: str = STRATEGY_V1_VERSION
    reason_codes: list[str] = field(default_factory=list)
    reasoning_summary: str = ""
    confidence: float = 0.0
    curve: str = "UNCLEAR"
    chart_decision: str = "WAIT"
    timeframe: str = "M15"
    lot: float = 0.0
    sl: float | None = None
    tp: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class AutonomousTradingAgent:
    """Autonomous decision layer for Strategy V1. Deterministic composition of
    Stochastic curve + chart context + position state."""

    def __init__(self, timeframe: str | None = None) -> None:
        self.timeframe = timeframe or settings.strategy_timeframe

    def decide(self, symbol: str, stoch: StochState, chart: ChartContext,
               positions: list[Position], *, lot: float = 0.0,
               sl: float | None = None, tp: float | None = None) -> AgentDecision:
        curve = stoch.curve
        curve_val = curve.value
        own = [p for p in positions if p.symbol == symbol]
        buy_open = any(p.side == "BUY" for p in own)
        sell_open = any(p.side == "SELL" for p in own)

        codes: list[str] = []
        summary_parts: list[str] = []
        confidence = 0.0

        def base(action: str, codes_extra: list[str], summary: str, conf: float) -> AgentDecision:
            return AgentDecision(action=action, symbol=symbol,
                                 reason_codes=codes + codes_extra,
                                 reasoning_summary=summary, confidence=conf,
                                 curve=curve_val, chart_decision=chart.decision,
                                 timeframe=self.timeframe, lot=lot, sl=sl, tp=tp)

        # ---- NO_TURN / UNCLEAR → pure monitoring ----
        if curve in (Curve.NO_TURN, Curve.UNCLEAR):
            codes_extra = ["STOCH_NO_SIGNAL"] if curve == Curve.NO_TURN else ["STOCH_DATA_UNCLEAR"]
            return base("WAIT", codes_extra,
                        f"Stochastic {curve_val}: {stoch.reason or 'no curve turn'}", 0.2)

        # ---- TURNING_UP ----
        # The user removed the chart veto on 2026-10-01: score is the only entry
        # gate, and chart context is recorded as DATA rather than deciding
        # anything. It is still attached to the reason codes so the learning
        # pass can later measure whether it had predictive value at all.
        if curve == Curve.TURNING_UP:
            codes.append("STOCHASTIC_TURN_UP")
            codes.append(f"CHART_{chart.decision}")
            confidence = min(0.95, 0.5 + chart.signal_quality * 0.45)

            # An exit is never gated on anything.
            if sell_open:
                codes.append("CLOSE_SELL_SIGNAL")
                summary = (f"Stochastic turned upward ({stoch.reason}); closing SELL per "
                           f"strategy rules. Chart: {chart.trend}/{chart.decision}")
                return base("CLOSE", [], summary, min(0.9, 0.45 + chart.signal_quality * 0.4))
            if buy_open:
                return base("WAIT", ["DUPLICATE_BUY_BLOCKED"],
                            f"BUY signal but {symbol} already has an open BUY "
                            "(max_same_direction_positions=1)", confidence)
            summary = (f"Stochastic turned upward ({stoch.reason}); chart context "
                       f"{chart.trend}/{chart.structure}/{chart.decision}: {chart.summary}")
            return base("BUY", [], summary, confidence)


        # ---- TURNING_DOWN ----
        # curve == Curve.TURNING_DOWN
        codes.append("STOCHASTIC_TURN_DOWN")
        codes.append(f"CHART_{chart.decision}")
        confidence = min(0.95, 0.5 + chart.signal_quality * 0.45)

        # An exit is never gated on anything.
        if buy_open:
            codes.append("CLOSE_BUY_SIGNAL")
            summary = (f"Stochastic turned downward ({stoch.reason}); closing BUY per "
                       f"strategy rules. Chart: {chart.trend}/{chart.decision}")
            return base("CLOSE", [], summary, min(0.9, 0.45 + chart.signal_quality * 0.4))
        if sell_open:
            return base("WAIT", ["DUPLICATE_SELL_BLOCKED"],
                        f"SELL signal but {symbol} already has an open SELL "
                        "(max_same_direction_positions=1)", confidence)
        summary = (f"Stochastic turned downward ({stoch.reason}); chart context "
                   f"{chart.trend}/{chart.structure}/{chart.decision}: {chart.summary}")
        return base("SELL", [], summary, confidence)
        codes.append("CHART_UNCLEAR")
        return base("WAIT", [], f"Stochastic turned down; chart unclear → WAIT: {chart.summary}", 0.35)

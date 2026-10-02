"""Logical agents: Market (read-only), Strategy (evaluate), Decision (combine)."""
from __future__ import annotations

import math

from dataclasses import asdict, dataclass, field

from app.config import settings
from app.core.events import iso_utc
from app.core.strategy_engine import StrategyEngine, Signal
from app.core.stochastic import Curve, StochState, StochasticCurveDetector, calc_stochastic
from app.mt5.base import Candle, MT5Connector, Price


class MarketAgent:
    """Read-only market data access. Must NEVER execute trades (enforced by interface)."""

    def __init__(self, connector: MT5Connector) -> None:
        self.connector = connector

    async def snapshot(self, symbol: str, timeframe: str = "M15", count: int = 120) -> dict:
        price = await self.connector.get_price(symbol)
        candles = await self.connector.get_candles(symbol, timeframe, count)
        rsi14 = await self.connector.get_indicator(symbol, timeframe, "RSI", 14)
        ema9 = await self.connector.get_indicator(symbol, timeframe, "EMA", 9)
        ema21 = await self.connector.get_indicator(symbol, timeframe, "EMA", 21)
        atr14 = await self.connector.get_indicator(symbol, timeframe, "ATR", 14)
        info = await self.connector.get_symbol_info(symbol)
        return {
            "symbol": symbol,
            "price": asdict(price),
            "last_candle": asdict(candles[-1]) if candles else None,
            "candles": [asdict(c) for c in candles[-30:]],
            "indicators": {
                "rsi14": rsi14[-1] if rsi14 else None,
                "ema9": ema9[-1] if ema9 else None,
                "ema21": ema21[-1] if ema21 else None,
                "atr14": atr14[-1] if atr14 else None,
            },
            "symbol_info": asdict(info),
        }


class StrategyAgent:
    """Evaluates the ACTIVE strategy deterministically and emits structured signals."""

    def __init__(self, engine: StrategyEngine, registry) -> None:
        self.engine = engine
        self.registry = registry

    async def evaluate(self, version: str, market: dict) -> Signal:
        candles = [c for c in (Candle(**c) for c in market["candles"])]
        price = Price(**market["price"])
        return self.engine.evaluate(version, price, candles)


@dataclass
class DecisionOutput:
    decision: str            # BUY / SELL / WAIT
    symbol: str
    strategy_version: str
    conditions: list = field(default_factory=list)
    market_state: dict = field(default_factory=dict)
    risk_state: dict = field(default_factory=dict)
    reason: str = ""
    timestamp: str = field(default_factory=iso_utc)
    lot: float = 0.0
    sl: float | None = None
    tp: float | None = None


class DecisionAgent:
    """Combines market + strategy + positions + risk state + session into a decision.

    Output is an auditable summary only — no hidden chain-of-thought is exposed.
    """

    def __init__(self, connector: MT5Connector, strategy_agent: StrategyAgent, registry) -> None:
        self.connector = connector
        self.strategy_agent = strategy_agent
        self.registry = registry

    async def decide(self, symbol: str, version: str | None = None,
                     timeframe: str | None = None) -> DecisionOutput:
        version = version or self.registry.active_version() or settings.default_strategy_id
        is_v1 = False
        try:
            is_v1 = any(r.get("type") == "STOCHASTIC_CURVE" for r in self.registry.rules(version))
        except Exception:  # noqa: BLE001
            is_v1 = False
        tf = timeframe or (settings.strategy_timeframe if is_v1 else "M15")
        market = await MarketAgent(self.connector).snapshot(symbol, timeframe=tf)
        sig = await self.strategy_agent.evaluate(version, market)
        positions = await self.connector.get_positions()
        account = await self.connector.get_account_status()

        market_state = {k: market["indicators"][k] for k in ("rsi14", "ema9", "ema21", "atr14")}
        if is_v1:
            # Strategy V1: attach stochastic state + raw candles for the autonomous agent
            # needs ≥34 bars for Stochastic(24,24,10) — fetch full history, not the 30-bar snapshot
            candles = await self.connector.get_candles(symbol, tf, 120)
            st = calc_stochastic(candles, k_period=settings.stoch_k_period,
                                 d_period=settings.stoch_d_period, slowing=settings.stoch_slowing)
            detector = StochasticCurveDetector(min_slope_change=settings.curve_min_slope_change,
                                               confirmation_bars=settings.curve_confirmation_bars,
                                               smoothing=settings.curve_smoothing)
            stoch_state = detector.detect(st["k"], st["d"])
            market_state.update({"stochastic_state": stoch_state, "curve": stoch_state.curve,
                                 "candles": candles, "timeframe": tf,
                                 "stoch_k": stoch_state.k, "stoch_d": stoch_state.d})

        decision = "WAIT"
        reason = sig.reason
        lot = sl = tp = None
        if sig.signal in ("BUY", "SELL"):
            positions_for_symbol = [p for p in positions if p.symbol == symbol]
            if positions_for_symbol:
                decision = "WAIT"
                reason = f"already holding {len(positions_for_symbol)} position(s) in {symbol}"
            else:
                decision = sig.signal
                lot = await self._lot_for(symbol)
                sl_dist = sig.context.get("sl_distance")
                if sl_dist:
                    entry = market["price"]["ask"] if decision == "BUY" else market["price"]["bid"]
                    sl = entry - sl_dist if decision == "BUY" else entry + sl_dist
                    tp = entry + 1.5 * sl_dist if decision == "BUY" else entry - 1.5 * sl_dist
                reason = sig.reason
        return DecisionOutput(
            decision=decision, symbol=symbol, strategy_version=version,
            conditions=sig.conditions_met + [f"✗ {c}" for c in sig.conditions_failed],
            market_state=market_state,
            risk_state={"open_positions": len(positions), "equity": account.equity},
            reason=reason, lot=lot or 0.0, sl=sl, tp=tp,
        )

    async def v1_order_params(self, symbol: str, side: str,
                              *, sl_lookback: int = 10, tp_rr: float = 1.5) -> tuple[float, float | None, float | None]:
        """Order sizing + protective stop for Strategy V1.

        SL comes from pure price structure (recent swing low/high) — NOT an extra
        indicator, keeping Strategy V1 = Stochastic + chart context only.
        TP = tp_rr × SL distance (configurable, spec §14).
        """
        price = await self.connector.get_price(symbol)
        candles = await self.connector.get_candles(symbol, settings.strategy_timeframe,
                                                   max(sl_lookback + 5, 30))
        if len(candles) < sl_lookback + 2:
            return 0.0, None, None
        entry = price.ask if side == "BUY" else price.bid
        window = candles[-sl_lookback - 1:-1]
        if side == "BUY":
            swing = min(c.low for c in window)
            sl_dist = max(entry - swing, price.spread_points * 2 if hasattr(price, "spread_points") else 0.5)
            sl = entry - sl_dist
            tp = entry + tp_rr * sl_dist
        else:
            swing = max(c.high for c in window)
            sl_dist = max(swing - entry, price.spread_points * 2 if hasattr(price, "spread_points") else 0.5)
            sl = entry + sl_dist
            tp = entry - tp_rr * sl_dist
        lot = await self._lot_for(symbol)
        return lot, round(sl, 2), round(tp, 2)

    async def _lot_for(self, symbol: str) -> float:
        """Broker-aware lot: the broker's minimum, capped at RISK_MAX_LOT.

        No notional/equity cap — the user removed risk calculations and manages
        position sizing from the platform. The broker will still reject an order it
        considers under-funded, which surfaces as a TRADE_OPEN_FAILED rather than a
        silently wrong lot.
        """
        info = await self.connector.get_symbol_info(symbol)
        lot = max(info.min_lot or info.lot_step or 0.01, info.lot_step or 0.01)
        lot = min(lot, settings.max_lot)
        if info.lot_step > 0:
            lot = math.floor(lot / info.lot_step) * info.lot_step
        lot = round(max(lot, 0.0), 2)

        return lot

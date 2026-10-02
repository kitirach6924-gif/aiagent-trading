"""Strategy Engine: deterministic rule interpreter.

Rules are plain JSON interpreted by THIS code (not by the LLM). Rule types:
  EMA_CROSS, RSI_FILTER, ATR_STOP, SPREAD_FILTER, SESSION_FILTER, RANGE_BREAKOUT

Each evaluation returns a structured signal — exactly the spec's format.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings
from app.core import sessions
from app.core.events import iso_utc
from app.core.stochastic import Curve, StochasticCurveDetector, calc_stochastic
from app.mt5.base import Candle, Price
from app.mt5.simulator import atr, ema, rsi, sma


@dataclass
class Signal:
    signal: str  # BUY / SELL / WAIT
    strategy_version: str
    conditions_met: list[str] = field(default_factory=list)
    conditions_failed: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""
    timestamp: str = field(default_factory=iso_utc)
    context: dict = field(default_factory=dict)  # SL/TP hints etc.


class StrategyEngine:
    def __init__(self, registry) -> None:
        self.registry = registry

    def evaluate(self, version: str, price: Price, candles: list[Candle],
                 indicators: dict[str, list[float]] | None = None,
                 spread_points: float | None = None,
                 now_hour_utc: int | None = None,
                 side: str | None = None,
                 account_equity: float | None = None) -> Signal:
        rules = self.registry.rules(version)
        indicators = indicators or {}
        closes = [c.close for c in candles]
        met: list[str] = []
        failed: list[str] = []
        ctx: dict = {}

        for rule in rules:
            rtype = rule["type"]
            p = rule.get("params", {})
            try:
                if rtype == "EMA_CROSS":
                    fast = ema(closes, int(p.get("fast", 9)))
                    slow = ema(closes, int(p.get("slow", 21)))
                    crossed_up = fast[-2] <= slow[-2] and fast[-1] > slow[-1]
                    crossed_down = fast[-2] >= slow[-2] and fast[-1] < slow[-1]
                    if crossed_up:
                        met.append("EMA_CROSS_UP")
                        ctx["bias"] = "BUY"
                    elif crossed_down:
                        met.append("EMA_CROSS_DOWN")
                        ctx["bias"] = "SELL"
                    else:
                        failed.append("EMA_CROSS")
                        ctx["bias"] = ctx.get("bias", "NONE")

                elif rtype == "RSI_FILTER":
                    series = indicators.get(f"RSI_{p.get('period', 14)}") or rsi(closes, int(p.get("period", 14)))
                    r = series[-1]
                    lo, hi = float(p.get("oversold", 35)), float(p.get("overbought", 65))
                    if lo <= r <= hi:
                        met.append(f"RSI_OK({r:.1f})")
                    else:
                        failed.append(f"RSI_OUT_OF_RANGE({r:.1f})")

                elif rtype == "ATR_STOP":
                    series = indicators.get(f"ATR_{p.get('period', 14)}") or atr(candles, int(p.get("period", 14)))
                    a = series[-1]
                    mult = float(p.get("mult", 2.0))
                    ctx["atr"] = a
                    ctx["sl_distance"] = a * mult
                    met.append(f"ATR_STOP_CALC(a={a:.5f})")

                elif rtype == "SPREAD_FILTER":
                    # Reported, not enforced — the user removed the spread cap
                    # deliberately. Leverage makes an equity-relative spread
                    # limit meaningless here; keep the number visible instead.
                    sp = spread_points if spread_points is not None else price.spread_points
                    met.append(f"SPREAD_INFO({sp}pts)")

                elif rtype == "SESSION_FILTER":
                    hour = now_hour_utc if now_hour_utc is not None else int(iso_utc()[11:13])
                    # Windows come from app/core/sessions.py — the same table the
                    # risk gate uses, so the two cannot disagree about which
                    # session is running at a given hour.
                    active = sessions.active_sessions(hour)
                    allowed = [s.upper() for s in p.get("allowed", ["LONDON", "NEWYORK"])]
                    if set(active) & set(allowed):
                        met.append(f"SESSION_OK({','.join(active)})")
                    else:
                        failed.append(f"SESSION_BLOCKED({','.join(active) or sessions.OFF_HOURS})")

                elif rtype == "STOCHASTIC_CURVE":
                    # Strategy V1 (spec §4–8): only indicator — Stochastic 24,24,10
                    detector = StochasticCurveDetector(
                        min_slope_change=float(p.get("min_slope_change", settings.curve_min_slope_change)),
                        confirmation_bars=int(p.get("confirmation_bars", settings.curve_confirmation_bars)),
                        smoothing=int(p.get("smoothing", settings.curve_smoothing)))
                    st = calc_stochastic(candles,
                                         k_period=int(p.get("k_period", settings.stoch_k_period)),
                                         d_period=int(p.get("d_period", settings.stoch_d_period)),
                                         slowing=int(p.get("slowing", settings.stoch_slowing)))
                    state = detector.detect(st["k"], st["d"])
                    ctx["stochastic"] = state.to_dict()
                    ctx["curve"] = state.curve
                    ctx["timeframe"] = getattr(settings, "strategy_timeframe", "M15")
                    if state.curve == Curve.TURNING_UP:
                        met.append("STOCHASTIC_TURN_UP")
                        ctx["bias"] = "BUY"
                    elif state.curve == Curve.TURNING_DOWN:
                        met.append("STOCHASTIC_TURN_DOWN")
                        ctx["bias"] = "SELL"
                    else:
                        failed.append(f"STOCH_{state.curve.value}({state.reason})")

                elif rtype == "RANGE_BREAKOUT":
                    lookback = int(p.get("lookback", 20))
                    window = candles[-lookback - 1:-1]
                    hi = max(c.high for c in window)
                    lo = min(c.low for c in window)
                    if candles[-1].close > hi:
                        met.append("RANGE_BREAK_UP")
                        ctx["bias"] = "BUY"
                    elif candles[-1].close < lo:
                        met.append("RANGE_BREAK_DOWN")
                        ctx["bias"] = "SELL"
                    else:
                        failed.append("RANGE_INSIDE")
            except Exception as e:  # noqa: BLE001 - any rule error = no trade (fail-closed)
                failed.append(f"{rtype}_ERROR({e})")

        bias = ctx.get("bias", "NONE")
        entry_signal = "BUY" if bias == "BUY" else "SELL" if bias == "SELL" else None
        if entry_signal and not failed:
            conf = min(0.95, 0.5 + 0.05 * len(met))
            sig = entry_signal
            reason = f"all {len(met)} conditions met; bias={bias}"
        else:
            conf = round(0.5 * len(met) / max(len(met) + len(failed), 1), 3)
            sig = "WAIT"
            if failed:
                reason = f"conditions failed: {', '.join(failed[:4])}"
            else:
                reason = "no directional bias yet"
        return Signal(signal=sig, strategy_version=version, conditions_met=met,
                      conditions_failed=failed, confidence=conf if sig != "WAIT" else round(conf * 0.6, 3),
                      reason=reason, context=ctx)

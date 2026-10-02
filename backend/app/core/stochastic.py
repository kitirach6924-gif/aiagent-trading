"""Strategy V1: Stochastic (24, 24, 10) + Curve Detection.

Spec §6: curve detection lives in THIS dedicated module — never hard-coded into
the general strategy engine. Deterministic and explainable.

MetaTrader Stochastic semantics (K period=24, D period=24, Slowing=10):
  raw_k[i]  = 100 * (close[i] - min(low, K)) / (max(high, K) - min(low, K))
  k[i]      = SMA(raw_k, slowing)          # slowing = 10
  d[i]      = SMA(k, d_period)             # d period = 24
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from app.mt5.base import Candle

STRATEGY_V1_ID = "STRATEGY_V1_STOCHASTIC_CURVE"
STRATEGY_V1_VERSION = "1.0.0"


class Curve(Enum):
    TURNING_UP = "TURNING_UP"
    TURNING_DOWN = "TURNING_DOWN"
    NO_TURN = "NO_TURN"
    UNCLEAR = "UNCLEAR"


CURVE_ARROW = {
    Curve.TURNING_UP: "↗ TURNING UP",
    Curve.TURNING_DOWN: "↘ TURNING DOWN",
    Curve.NO_TURN: "→ NO TURN",
    Curve.UNCLEAR: "? UNCLEAR",
}


def _sma(values: list[float], period: int) -> list[float]:
    if period <= 1 or len(values) < period:
        return list(values)
    out: list[float] = []
    for i in range(len(values)):
        if i + 1 < period:
            continue
        out.append(sum(values[i + 1 - period:i + 1]) / period)
    return out


def calc_stochastic(candles: list[Candle], k_period: int = 24, d_period: int = 24,
                    slowing: int = 10) -> dict:
    """Returns {'k': [...], 'd': [...], 'raw_k': [...]} (may be empty if data insufficient)."""
    if len(candles) < k_period + slowing:
        return {"k": [], "d": [], "raw_k": []}
    raw_k: list[float] = []
    for i in range(k_period - 1, len(candles)):
        window = candles[i - k_period + 1:i + 1]
        hh = max(c.high for c in window)
        ll = min(c.low for c in window)
        if hh - ll <= 0:
            raw_k.append(50.0)  # flat window → neutral, never divide by zero
        else:
            raw_k.append((window[-1].close - ll) / (hh - ll) * 100.0)
    k = _sma(raw_k, slowing)
    d = _sma(k, d_period)
    return {"k": k, "d": d, "raw_k": raw_k}


@dataclass
class StochState:
    k: float | None = None
    d: float | None = None
    prev_k: float | None = None
    prev_d: float | None = None
    curve: Curve = Curve.UNCLEAR
    reason: str = ""
    k_series: list[float] = field(default_factory=list)
    d_series: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"k": self.k, "d": self.d, "previous_k": self.prev_k, "previous_d": self.prev_d,
                "curve": self.curve.value, "reason": self.reason}


class StochasticCurveDetector:
    """Deterministic curve-turn detection on the smoothed %K line.

    TURNING_UP:   previous slope <= 0  and  current slope > +min_slope_change
    TURNING_DOWN: previous slope >= 0  and  current slope < -min_slope_change
    NO_TURN:      trend continues or movement below threshold
    UNCLEAR:      insufficient data / degenerate series
    Sensitivity is configurable (min_slope_change, confirmation_bars, smoothing).
    """

    def __init__(self, min_slope_change: float = 0.5, confirmation_bars: int = 1,
                 smoothing: int = 1) -> None:
        self.min_slope_change = max(0.0, float(min_slope_change))
        self.confirmation_bars = max(1, int(confirmation_bars))
        self.smoothing = max(1, int(smoothing))

    def detect(self, k_series: list[float], d_series: list[float] | None = None) -> StochState:
        n = 2 + self.confirmation_bars + 1  # slopes need 3+ points minimum
        if len(k_series) < n:
            return StochState(curve=Curve.UNCLEAR, reason=f"insufficient data ({len(k_series)} < {n})")
        k = _sma(k_series, self.smoothing) if self.smoothing > 1 else k_series
        k_last = k[-1]
        k_prev = k[-2 - (self.confirmation_bars - 1)]
        k_before = k[-3 - (self.confirmation_bars - 1)]
        if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in (k_last, k_prev, k_before)):
            return StochState(curve=Curve.UNCLEAR, reason="NaN in stochastic series")
        slope_now = k_last - k_prev
        slope_before = k_prev - k_before

        state = StochState(k=round(k_last, 2),
                           d=round(d_series[-1], 2) if d_series else None,
                           prev_k=round(k_prev, 2),
                           prev_d=round(d_series[-2], 2) if d_series and len(d_series) > 1 else None,
                           k_series=[round(v, 2) for v in k[-10:]],
                           d_series=[round(v, 2) for v in (d_series or [])[-10:]])

        thr = self.min_slope_change
        if slope_before <= 0 and slope_now > thr:
            state.curve = Curve.TURNING_UP
            state.reason = f"slope {slope_before:+.2f}→{slope_now:+.2f} (up turn > {thr})"
        elif slope_before >= 0 and slope_now < -thr:
            state.curve = Curve.TURNING_DOWN
            state.reason = f"slope {slope_before:+.2f}→{slope_now:+.2f} (down turn < -{thr})"
        else:
            state.curve = Curve.NO_TURN
            state.reason = f"slope {slope_before:+.2f}→{slope_now:+.2f} (no confirmed turn, thr={thr})"
        return state

"""Entry scoring 0–100.

The original gate was all-or-nothing: the single Stochastic rule either passed or
it did not, which in a ranging market produced a dense string of tiny losers — every
flip paid the spread. This module grades the setup instead so weak signals are
simply not taken.

Spread is deliberately NOT a points component but a hard gate. No amount of
technical confluence makes a 57-point spread on GOLD worth trading; it would let
a perfect-looking setup through and still lose money on entry and exit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.config import settings
from app.core.stochastic import Curve, StochState
from app.mt5.base import Candle
from app.mt5.simulator import ema

log = logging.getLogger(__name__)

# Only two criteria score the entry, per the user: turn strength and trend
# agreement. Zone, %D confirmation and spread were removed — measured against
# 7.5h of live data, `zone` and `d_cross` never co-occurred (oversold %K leaves
# %D still high), so keeping them made the threshold unreachable.
# Weights are renormalised to sum to 100 so "80%" is literally 80 of 100.
W_TURN_STRENGTH = 65    # %K turned decisively, not on a rounding error
W_TREND = 35            # price is not fighting us
W_ZONE = 0              # removed
W_D_CROSS = 0           # removed
W_SPREAD = 0            # reported only — the user removed the spread cap

# The user asked for 80% on 2026-09-30, then lowered it to 65 on 2026-10-01.
# 80 meant "a real turn AND price agreement with it". 65 means a decisive turn
# alone is enough, and trend agreement becomes a bonus rather than a hard
# requirement — the 80 gate rejected 134 live setups whose turn was perfect
# (turn=65) purely because price sat on the wrong side of EMA20.
ENTRY_THRESHOLD = 65

# The adaptive tuner moves ENTRY_THRESHOLD at runtime (user's request
# 2026-10-01). score_setup reads the module global on every call, so a live
# change takes effect immediately — no restart, no cached copy.
DEFAULT_ENTRY_THRESHOLD = 65   # what a fresh container boots with; never mutated
_MIN_ENTRY_THRESHOLD = 65      # the tuner may raise the bar but never lower it
#                              below a full-strength turn


def set_entry_threshold(value: float) -> float:
    """Move the live entry gate. Returns the value applied, for logging.

    Clamped to [_MIN_ENTRY_THRESHOLD, 100]: the tuner may raise the bar to be
    more selective, but it must never lower it below a full-strength turn, or
    setups with no real turn would be admitted.
    """
    global ENTRY_THRESHOLD
    wanted = float(value)
    ENTRY_THRESHOLD = max(_MIN_ENTRY_THRESHOLD, min(100.0, wanted))
    if ENTRY_THRESHOLD != wanted:
        log.warning("entry threshold %.2f clamped to %.2f", wanted, ENTRY_THRESHOLD)
    return ENTRY_THRESHOLD


def get_entry_threshold() -> float:
    return ENTRY_THRESHOLD
ZONE_OVERSOLD = 25.0    # %K below this → turning up is a mean-reversion buy
ZONE_OVERBOUGHT = 75.0  # %K above this → turning down is a mean-reversion sell
STRONG_TURN = 2.0       # slope multiple over the rule minimum to count as "strong"


@dataclass
class ScoreBreakdown:
    total: int = 0
    parts: dict = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    passed: bool = False
    spread_points: float | None = None
    spread_cost: float | None = None
    spread_cost_pct: float | None = None

    def to_dict(self) -> dict:
        return {"total": self.total, "parts": self.parts,
                "blockers": self.blockers, "passed": self.passed,
                "threshold": ENTRY_THRESHOLD,
                "weights": {"turn": W_TURN_STRENGTH, "trend": W_TREND},
                "spread_points": self.spread_points,
                "spread_cost": self.spread_cost,
                "spread_cost_pct": self.spread_cost_pct}

    def summary(self) -> str:
        bits = [f"{k}={v}" for k, v in self.parts.items() if v]
        s = f"score {self.total}/100 (threshold {ENTRY_THRESHOLD})"
        if bits:
            s += ": " + ", ".join(bits)
        if self.blockers:
            s += " | BLOCKED: " + ", ".join(self.blockers)
        return s


def score_setup(side: str, stoch: StochState | None, curve: Curve,
                candles: list[Candle], spread_points: float | None,
                lot_equity: float | None = None,
                assumed_lot: float = 0.01,
                assumed_contract: float = 100.0) -> ScoreBreakdown:
    """Grade an entry setup. Returns a breakdown; `passed` decides whether it trades."""
    b = ScoreBreakdown()
    k = float(getattr(stoch, "k", 0.0) or 0.0)
    d = float(getattr(stoch, "d", 0.0) or 0.0)
    curve_val = getattr(curve, "value", curve)

    # ---- spread: reported and scored, never a blocker ----
    # The user removed the spread cap: at 1:500 leverage an equity-relative
    # spread limit either never fires or fires constantly as the balance moves,
    # and there is no meaningful % of equity a spread "should" cost in a
    # leveraged market. Keep the cost visible; stop vetoing the trade on it.
    sp = spread_points
    equity = lot_equity if lot_equity else None
    lot = assumed_lot if assumed_lot else 0.01
    contract = assumed_contract if assumed_contract else 100.0
    if sp and sp > 0:
        # ---- spread: reported, never scored ----
        # The user removed the spread cap, so it can no longer block a trade.
        # It used to carry 20 points toward the entry score, which inflated the
        # total with a criterion that always passed. Report it, score it 0:
        # the threshold then means "this setup was good enough on the things
        # that can actually veto it".
        b.spread_points = sp
        b.spread_cost = round((sp / 100.0) * lot * contract, 4)
        b.spread_cost_pct = (round(b.spread_cost / equity * 100, 4)
                             if equity else None)

    # ---- direction must match the proposed side ----
    want = Curve.TURNING_UP if side == "BUY" else Curve.TURNING_DOWN
    if curve_val != want.value:
        b.blockers.append(f"CURVE_MISMATCH({curve_val}!={want.value})")
        b.total = sum(b.parts.values())
        return b

    # ---- 1. turn strength ----
    # Normalize the turn against the rule's own minimum so a quiet market does not
    # hand out full marks for a 0.6 slope that clears a 0.5 threshold.
    min_slope = settings.curve_min_slope_change or 0.5
    pk = getattr(stoch, "prev_k", None)
    slope = abs(k - float(pk)) if pk is not None else 0.0
    if slope <= 0:
        slope = abs(k - d)          # fall back to %K/%D separation
    ratio = slope / max(min_slope, 1e-6)
    b.parts["turn"] = round(W_TURN_STRENGTH * min(ratio / STRONG_TURN, 1.0))

    # ---- trend agreement (EMA20 vs price) ----
    # `zone` and `%D confirmation` were removed at the user's request; they are
    # kept above as zero-weight constants so history and reports stay readable.
    closes = [c.close for c in candles]
    if len(closes) >= 21:
        line = ema(closes, 20)[-1]
        px = closes[-1]
        if side == "BUY" and px > line:
            b.parts["trend"] = W_TREND
        elif side == "SELL" and px < line:
            b.parts["trend"] = W_TREND

    b.total = sum(b.parts.values())
    b.passed = b.total >= ENTRY_THRESHOLD and not b.blockers
    return b

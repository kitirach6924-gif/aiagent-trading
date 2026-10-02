"""PRICE_ACTION_CANDLE_CONTEXT_V1 (knowledge pack, v1.0.0).

Chart-context reasoning layer for STRATEGY_V1_STOCHASTIC_CURVE — NOT a standalone
strategy, can never trade directly, and never uses future candles (spec §30).

Layers (spec §35):
  A  OHLC → deterministic candle features (anatomy/behavior/patterns)
  B  features → deterministic structure (swings, HH/HL/LH/LL, S/R zones, range)
  C  structure + candles → context (breakout/false/retest, location, scoring,
     final contract §27)
  D  LLM synthesis lives in chart_context.py (numerical data always wins, §31)
  E/F  AutonomousTradingAgent + RiskGate (unchanged authorities)
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum

KNOWLEDGE_ID = "PRICE_ACTION_CANDLE_CONTEXT_V1"
KNOWLEDGE_VERSION = "1.0.0"

from app.mt5.base import Candle

# ---------- reason-code taxonomy (spec §28, exhaustive) ----------
REASON_CODES = {
    "BULLISH_STRUCTURE", "BEARISH_STRUCTURE", "SIDEWAYS_STRUCTURE", "UNCLEAR_STRUCTURE",
    "HIGHER_HIGH", "HIGHER_LOW", "LOWER_HIGH", "LOWER_LOW",
    "AT_SUPPORT", "AT_RESISTANCE", "NEAR_SUPPORT", "NEAR_RESISTANCE",
    "BULLISH_REJECTION", "BEARISH_REJECTION",
    "BULLISH_PIN_BAR", "BEARISH_PIN_BAR",
    "BULLISH_ENGULFING", "BEARISH_ENGULFING",
    "DOJI", "INSIDE_BAR", "OUTSIDE_BAR",
    "BULLISH_BREAKOUT", "BEARISH_BREAKOUT",
    "FALSE_BULLISH_BREAKOUT", "FALSE_BEARISH_BREAKOUT",
    "BULLISH_RETEST", "BEARISH_RETEST",
    "RANGE_MIDDLE", "RANGE_HIGH", "RANGE_LOW",
    "BULLISH_PULLBACK", "BEARISH_PULLBACK",
    "BULLISH_FOLLOW_THROUGH", "BEARISH_FOLLOW_THROUGH",   # §20 candle-sequence extension
    "BUY_CONTEXT_SUPPORTIVE", "SELL_CONTEXT_SUPPORTIVE",
    "STOCHASTIC_PRICE_ACTION_CONFLICT", "CONTEXT_UNCLEAR", "INSUFFICIENT_DATA",
    "STALE_MARKET_DATA",
}


class Ctx(Enum):
    SUPPORTIVE = "SUPPORTIVE"
    CONTRADICTORY = "CONTRADICTORY"
    NEUTRAL = "NEUTRAL"
    UNCLEAR = "UNCLEAR"


# ===================================================================
# Layer A — candle anatomy / behavior / patterns (spec §2–§4, §20)
# ===================================================================
@dataclass
class CandleFeature:
    open: float
    high: float
    low: float
    close: float
    body_size: float
    range: float
    upper_wick: float
    lower_wick: float
    body_ratio: float          # body / range (0 if range 0)
    close_location: float      # (close - low) / range (0..1)
    direction: str             # BULLISH / BEARISH / NEUTRAL
    behavior: str = "NEUTRAL"  # STRONG_BULLISH / STRONG_BEARISH / BULLISH_REJECTION /
                               # BEARISH_REJECTION / INDECISION / NEUTRAL
    pattern: str = "NONE"
    pattern_quality: float = 0.0
    index: int = -1

    def to_dict(self) -> dict:
        return asdict(self)


def candle_features(candles: list[Candle]) -> list[CandleFeature]:
    out: list[CandleFeature] = []
    for i, c in enumerate(candles):
        rng = c.high - c.low
        body = abs(c.close - c.open)
        upper = c.high - max(c.open, c.close)
        lower = min(c.open, c.close) - c.low
        body_ratio = body / rng if rng > 0 else 0.0
        close_loc = (c.close - c.low) / rng if rng > 0 else 0.5
        direction = "BULLISH" if c.close > c.open else "BEARISH" if c.close < c.open else "NEUTRAL"
        f = CandleFeature(open=c.open, high=c.high, low=c.low, close=c.close,
                          body_size=round(body, 6), range=round(rng, 6),
                          upper_wick=round(upper, 6), lower_wick=round(lower, 6),
                          body_ratio=round(body_ratio, 3), close_location=round(close_loc, 3),
                          direction=direction, index=i)
        f.behavior = _behavior(f)
        out.append(f)
    _annotate_patterns(out)
    return out


def _avg_range(feats: list[CandleFeature], up_to: int, lookback: int = 14) -> float:
    window = feats[max(0, up_to - lookback):up_to]
    if not window:
        return 0.0
    return sum(f.range for f in window) / len(window)


def _behavior(f: CandleFeature) -> str:
    if f.range <= 0:
        return "INDECISION"
    if f.body_ratio >= 0.65:
        if f.direction == "BULLISH" and f.close_location >= 0.7:
            return "STRONG_BULLISH"       # buyers showing control (context only)
        if f.direction == "BEARISH" and f.close_location <= 0.3:
            return "STRONG_BEARISH"       # sellers showing control (context only)
    # rejection: dominant wick + close away from the rejected extreme
    if f.lower_wick >= 2.0 * max(f.body_size, f.upper_wick) and f.close_location >= 0.6:
        return "BULLISH_REJECTION"
    if f.upper_wick >= 2.0 * max(f.body_size, f.lower_wick) and f.close_location <= 0.4:
        return "BEARISH_REJECTION"
    if f.body_ratio <= 0.1:
        return "INDECISION"               # doji — never auto-reversal (spec §7)
    return "NEUTRAL"


def _annotate_patterns(feats: list[CandleFeature]) -> None:
    """Structural (not pixel-perfect) pattern recognition — context only (spec §4).
    Single-candle patterns annotate from index 0; multi-candle patterns need prev."""
    for i, f in enumerate(feats):
        avg_range = _avg_range(feats, i) or f.range or 1e-9
        big = avg_range * 1.1
        # single-candle patterns (no neighbour required)
        if f.range > 0 and f.lower_wick / f.range >= 0.6 and f.close_location >= 0.6:
            f.pattern, f.pattern_quality = "BULLISH_PIN_BAR", round(min(1.0, f.lower_wick / big), 2)
        elif f.range > 0 and f.upper_wick / f.range >= 0.6 and f.close_location <= 0.4:
            f.pattern, f.pattern_quality = "BEARISH_PIN_BAR", round(min(1.0, f.upper_wick / big), 2)
        elif f.range > 0 and f.lower_wick / f.range >= 0.5 and f.body_ratio <= 0.35 and f.close_location >= 0.55:
            f.pattern, f.pattern_quality = "HAMMER", 0.6
        elif f.range > 0 and f.upper_wick / f.range >= 0.5 and f.body_ratio <= 0.35 and f.close_location <= 0.45:
            f.pattern, f.pattern_quality = "SHOOTING_STAR", 0.6
        elif f.body_ratio <= 0.1:
            f.pattern, f.pattern_quality = "DOJI", 0.4
        if i == 0:
            continue
        prev = feats[i - 1]
        # multi-candle patterns (structural similarity, not pixel matching)
        if (f.direction == "BULLISH" and prev.direction == "BEARISH"
                and f.body_size >= prev.body_size and f.close >= max(prev.open, prev.close)
                and f.open <= min(prev.open, prev.close)):
            f.pattern, f.pattern_quality = "BULLISH_ENGULFING", round(min(1.0, f.body_size / big), 2)
        elif (f.direction == "BEARISH" and prev.direction == "BULLISH"
                and f.body_size >= prev.body_size and f.open >= max(prev.open, prev.close)
                and f.close <= min(prev.open, prev.close)):
            f.pattern, f.pattern_quality = "BEARISH_ENGULFING", round(min(1.0, f.body_size / big), 2)
        elif f.high < prev.high and f.low > prev.low:
            f.pattern, f.pattern_quality = "INSIDE_BAR", 0.5   # compression (spec §8)
        elif f.high > prev.high and f.low < prev.low:
            f.pattern, f.pattern_quality = "OUTSIDE_BAR", 0.5
        # note: pattern ≠ signal; location weighting happens in Layer C


# ===================================================================
# Layer B — swings (no look-ahead, spec §9/§30), structure, zones, range
# ===================================================================
@dataclass
class Swing:
    type: str            # SWING_HIGH / SWING_LOW
    price: float
    timestamp: str
    strength: int        # bars on each side required
    confirmed: bool
    detected_at: str
    confirmed_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def detect_swings(candles: list[Candle], strength: int = 2) -> list[Swing]:
    """Fractal swings confirmed only AFTER `strength` later candles exist.

    Look-ahead protection: a swing at index i may only be *used* once
    i + strength < len(candles) — i.e. confirmation needs later bars, and the
    swing itself is derived from bars ≤ i only.
    """
    out: list[Swing] = []
    n = len(candles)
    for i in range(strength, n - strength):
        window = candles[i - strength:i + strength + 1]
        is_high = candles[i].high == max(c.high for c in window) and \
            candles[i].high > candles[i + 1].high   # tie-break: flat tops resolve on the later bar
        is_low = candles[i].low == min(c.low for c in window) and \
            candles[i].low < candles[i + 1].low
        if is_high:
            out.append(Swing(type="SWING_HIGH", price=candles[i].high,
                             timestamp=candles[i].time, strength=strength,
                             confirmed=True, detected_at=candles[i].time,
                             confirmed_at=candles[i + strength].time))
        elif is_low:
            out.append(Swing(type="SWING_LOW", price=candles[i].low,
                             timestamp=candles[i].time, strength=strength,
                             confirmed=True, detected_at=candles[i].time,
                             confirmed_at=candles[i + strength].time))
    return out


@dataclass
class StructureState:
    trend: str = "UNCLEAR"                # BULLISH / BEARISH / SIDEWAYS / UNCLEAR
    state: str = "UNCLEAR"                # HH_HL / LH_LL / RANGE / UNCLEAR
    last_swing_high: float | None = None
    last_swing_low: float | None = None
    previous_swing_high: float | None = None
    previous_swing_low: float | None = None
    sequence: list[str] = field(default_factory=list)   # HH / HL / LH / LL
    structure_strength: float = 0.0
    reason_codes: list[str] = field(default_factory=list)
    swing_points: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def market_structure(swings: list[Swing], min_sequence: int = 2) -> StructureState:
    """Classify HH/HL/LH/LL from the last 4 alternating swings (spec §10–§11)."""
    highs = [s for s in swings if s.type == "SWING_HIGH"]
    lows = [s for s in swings if s.type == "SWING_LOW"]
    st = StructureState()
    st.swing_points = [s.to_dict() for s in swings[-6:]]
    if len(highs) < 2 or len(lows) < 2:
        st.state = "NO_SWINGS"            # caller applies the slope fallback
        st.reason_codes.append("INSUFFICIENT_DATA")
        return st
    h1, h2 = highs[-2], highs[-1]     # h2 = most recent high
    l1, l2 = lows[-2], lows[-1]
    st.last_swing_high, st.previous_swing_high = round(h2.price, 4), round(h1.price, 4)
    st.last_swing_low, st.previous_swing_low = round(l2.price, 4), round(l1.price, 4)

    seq: list[str] = []
    if h2.price > h1.price:
        seq.append("HH")
    elif h2.price < h1.price:
        seq.append("LH")
    if l2.price > l1.price:
        seq.append("HL")
    elif l2.price < l1.price:
        seq.append("LL")
    st.sequence = seq
    if len(seq) < min_sequence:
        # Slope fallback (still pure price structure, §20): a steady monotonic move has
        # no local swings yet is a real trend — do not call it UNCLEAR.
        # Handled by caller via build_context (needs candles); mark for fallback.
        st.state = "NO_SWINGS"
        st.reason_codes.append("INSUFFICIENT_DATA")
        return st

    hh, lh = seq[0] == "HH", seq[0] == "LH"
    hl, ll = seq[1] == "HL", seq[1] == "LL"
    if hh and hl:
        st.trend, st.state = "BULLISH", "HH_HL"
        st.structure_strength = 0.9
        st.reason_codes += ["BULLISH_STRUCTURE", "HIGHER_HIGH", "HIGHER_LOW"]
    elif lh and ll:
        st.trend, st.state = "BEARISH", "LH_LL"
        st.structure_strength = 0.9
        st.reason_codes += ["BEARISH_STRUCTURE", "LOWER_HIGH", "LOWER_LOW"]
    elif hh and ll or lh and hl:
        st.trend, st.state = "SIDEWAYS", "RANGE"
        st.structure_strength = 0.4
        st.reason_codes.append("SIDEWAYS_STRUCTURE")
    else:
        st.trend, st.state = "UNCLEAR", "UNCLEAR"
        st.reason_codes.append("UNCLEAR_STRUCTURE")
    return st


@dataclass
class Zone:
    type: str            # SUPPORT / RESISTANCE / NONE / UNCLEAR
    zone_low: float
    zone_high: float
    strength: float = 0.0
    touch_count: int = 0
    freshness: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    def contains(self, price: float) -> bool:
        return self.zone_low <= price <= self.zone_high


def sr_zones(candles: list[Candle], swings: list[Swing], *,
             zone_frac: float = 0.0015, max_zones: int = 4) -> list[Zone]:
    """Cluster confirmed swing prices into zones (spec §13). Zones not lines."""
    if len(candles) < 10 or not swings:
        return []
    avg_range = sum(c.high - c.low for c in candles[-30:]) / min(30, len(candles))
    tol = max(avg_range * 0.4, sum(c.close for c in candles[-20:]) / 20 * zone_frac * 10)
    last = candles[-1]
    total = max(len(candles), 1)

    clusters: list[dict] = []
    for s in swings:
        for cl in clusters:
            if abs(s.price - cl["ref"]) <= tol:
                cl["prices"].append(s.price)
                cl["touches"] += 1
                break
        else:
            clusters.append({"ref": s.price, "prices": [s.price], "touches": 1,
                             "last_idx": candles.index(next(c for c in candles if c.time == s.timestamp))
                             if s.timestamp in [c.time for c in candles] else 0})
    zones: list[Zone] = []
    for cl in clusters:
        prices = cl["prices"]
        lo, hi = min(prices) - tol * 0.25, max(prices) + tol * 0.25
        touch = cl["touches"]
        fresh = 1.0 - min(1.0, cl["last_idx"] / total)
        zones.append(Zone(type="SUPPORT" if hi < last.close else "RESISTANCE",
                          zone_low=round(lo, 4), zone_high=round(hi, 4),
                          strength=round(min(1.0, 0.3 * touch + 0.2 * len(prices)), 2),
                          touch_count=touch, freshness=round(fresh, 2)))
    zones.sort(key=lambda z: z.strength, reverse=True)
    return zones[:max_zones]


@dataclass
class RangeState:
    in_range: bool = False
    upper_zone: dict | None = None
    lower_zone: dict | None = None
    range_width: float = 0.0
    range_quality: float = 0.0
    position_in_range: float = 0.5     # 0 = low boundary, 1 = high boundary

    def to_dict(self) -> dict:
        return asdict(self)


def detect_range(candles: list[Candle], swings: list[Swing]) -> RangeState:
    """Range = last 2 swing highs similar AND last 2 swing lows similar (spec §18)."""
    st = RangeState()
    highs = [s.price for s in swings if s.type == "SWING_HIGH"][-2:]
    lows = [s.price for s in swings if s.type == "SWING_LOW"][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return st
    avg_range = sum(c.high - c.low for c in candles[-30:]) / min(30, len(candles)) or 1e-9
    tol = avg_range * 0.6
    if abs(highs[1] - highs[0]) <= tol and abs(lows[1] - lows[0]) <= tol:
        upper = (min(highs) + max(highs)) / 2
        lower = (min(lows) + max(lows)) / 2
        width = upper - lower
        if width > 0:
            last = candles[-1].close
            st.in_range = True
            st.upper_zone = {"zone_low": round(min(highs) - tol * .25, 4), "zone_high": round(max(highs) + tol * .25, 4)}
            st.lower_zone = {"zone_low": round(min(lows) - tol * .25, 4), "zone_high": round(max(lows) + tol * .25, 4)}
            st.range_width = round(width, 4)
            st.range_quality = round(min(1.0, 1.0 - abs(highs[1] - highs[0]) / (2 * tol)), 2)
            st.position_in_range = round(max(0.0, min(1.0, (last - lower) / width)), 3)
    return st


# ===================================================================
# Layer C — breakout / false breakout / retest / location / scoring
# ===================================================================
def breakout_state(feats: list[CandleFeature], zones: list[Zone],
                   rng: RangeState) -> dict:
    """Breakout + false-breakout + retest evaluation (spec §15–§17).

    Decisive = close beyond zone with body; a wick alone is never a breakout.
    False breakout = price exceeded the zone but FAILED TO HOLD (closed back inside).
    Retest = price returned to the broken zone and resumed in breakout direction.
    Branches are mutually exclusive on where the CURRENT close sits.
    """
    out = {"state": "NONE", "confirmed": False, "zone_side": "NONE",
           "retest": {"state": "NONE", "confirmed": False}}
    if len(feats) < 3:
        return out
    cur, prev = feats[-1], feats[-2]

    def _recent_touch(z: Zone) -> bool:
        return any(f.low <= z.zone_high and f.high >= z.zone_low for f in feats[-4:-1])

    for z in zones:
        if z.type == "RESISTANCE":
            if cur.close > z.zone_high:
                if cur.direction == "BULLISH" and cur.body_ratio >= 0.5:
                    out.update(state="BULLISH", confirmed=True, zone_side="RESISTANCE")
                    if _recent_touch(z):                      # held after retest (§17)
                        out["retest"] = {"state": "BULLISH", "confirmed": True}
                    break
            elif cur.close < z.zone_high:
                # failure to hold: previously beyond the zone, now back inside
                if prev.close > z.zone_high or \
                   (prev.high > z.zone_high and prev.close < z.zone_high):
                    out.update(state="FALSE_BREAKOUT", confirmed=False, zone_side="RESISTANCE")
                    break
        elif z.type == "SUPPORT":
            if cur.close < z.zone_low:
                if cur.direction == "BEARISH" and cur.body_ratio >= 0.5:
                    out.update(state="BEARISH", confirmed=True, zone_side="SUPPORT")
                    if _recent_touch(z):
                        out["retest"] = {"state": "BEARISH", "confirmed": True}
                    break
            elif cur.close > z.zone_low:
                if prev.close < z.zone_low or \
                   (prev.low < z.zone_low and prev.close > z.zone_low):
                    out.update(state="FALSE_BREAKOUT", confirmed=False, zone_side="SUPPORT")
                    break

    # range-edge breakout fallback when no swing zones apply
    if out["state"] == "NONE" and rng.in_range:
        if rng.position_in_range >= 0.98 and cur.direction == "BULLISH" and cur.body_ratio >= 0.6:
            out.update(state="BULLISH", confirmed=True, zone_side="RANGE")
        elif rng.position_in_range <= 0.02 and cur.direction == "BEARISH" and cur.body_ratio >= 0.6:
            out.update(state="BEARISH", confirmed=True, zone_side="RANGE")
    return out


def price_location(cur: CandleFeature, zones: list[Zone], rng: RangeState,
                   brk: dict) -> tuple[str, float, list[str]]:
    """Where is price right now? (spec §19) → (zone, quality, reason_codes)."""
    codes: list[str] = []
    tol_frac = 0.25
    for z in zones:
        height = max(z.zone_high - z.zone_low, 1e-9)
        near_low, near_high = z.zone_low - height * tol_frac, z.zone_high + height * tol_frac
        if z.contains(cur.close):
            if z.type == "SUPPORT":
                codes.append("AT_SUPPORT")
                return "AT_SUPPORT", 0.8, codes
            codes.append("AT_RESISTANCE")
            return "AT_RESISTANCE", 0.8, codes
        if near_low <= cur.close <= near_high:
            if z.type == "SUPPORT":
                codes.append("NEAR_SUPPORT")
                return "NEAR_SUPPORT", 0.6, codes
            codes.append("NEAR_RESISTANCE")
            return "NEAR_RESISTANCE", 0.6, codes
    if brk["state"] == "BULLISH":
        return "AFTER_BULLISH_BREAKOUT", 0.7, codes
    if brk["state"] == "BEARISH":
        return "AFTER_BEARISH_BREAKOUT", 0.7, codes
    if brk["retest"]["state"] == "BULLISH":
        return "RETEST_SUPPORT", 0.7, codes
    if brk["retest"]["state"] == "BEARISH":
        return "RETEST_RESISTANCE", 0.7, codes
    if rng.in_range:
        pos = rng.position_in_range
        if pos >= 0.7:
            codes.append("RANGE_HIGH")
            return "NEAR_RANGE_HIGH", 0.5, codes
        if pos <= 0.3:
            codes.append("RANGE_LOW")
            return "NEAR_RANGE_LOW", 0.5, codes
        codes.append("RANGE_MIDDLE")
        return "RANGE_MIDDLE", 0.3, codes
    return "OPEN_SPACE", 0.2, codes


@dataclass
class PriceActionContext:
    market_structure: dict
    price_location: dict
    candlestick: dict
    breakout: dict
    retest: dict
    context: str
    support_score: float
    contradiction_score: float
    uncertainty_score: float
    reason_codes: list[str]
    summary: str
    knowledge_id: str = KNOWLEDGE_ID
    version: str = KNOWLEDGE_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


def structure_blocks_entry(swings: list[Swing], proposed_side: str) -> str | None:
    """Block reason when a classified structure opposes the proposed entry.

      BUY  blocked when the structure is BEARISH (LH_LL)
      SELL blocked when the structure is BULLISH (HH_HL)

    Split out from higher_tf_blocks_entry so the rule itself is testable without
    having to synthesise candles that survive detect_swings.
    """
    if not swings:
        return None
    st = market_structure(swings)
    if st.trend == "NO_SWINGS":
        return None
    if proposed_side == "BUY" and st.trend == "BEARISH":
        return f"M15_BEARISH({st.state})"
    if proposed_side == "SELL" and st.trend == "BULLISH":
        return f"M15_BULLISH({st.state})"
    return None


def higher_tf_blocks_entry(higher_candles: list[Candle], proposed_side: str) -> str | None:
    """Return a block reason when the higher timeframe opposes the entry.

    The user chose this on 2026-10-01: M15 gates NEW entries only, while exits
    stay on the entry timeframe (M5) because closing fast matters more than
    being directionally right.

    Only a clear opposition blocks. M15 UNCLEAR/NEUTRAL does NOT block, or noise
    on the higher timeframe would stop the system from ever trading.
    """
    if not higher_candles or len(higher_candles) < 12:
        return None
    return structure_blocks_entry(detect_swings(higher_candles, strength=2),
                                  proposed_side)


def build_context(candles: list[Candle], proposed_side: str, *, swing_strength: int = 2,
                  stale: bool = False) -> PriceActionContext:
    """Full deterministic pipeline A→B→C for the proposed side of the Stochastic signal."""
    feats = candle_features(candles)
    if len(candles) < 12 or stale:
        codes = ["STALE_MARKET_DATA"] if stale else ["INSUFFICIENT_DATA"]
        return PriceActionContext(
            market_structure={"trend": "UNCLEAR", "state": "UNCLEAR"},
            price_location={"zone": "UNCLEAR", "distance": None, "quality": 0.0},
            candlestick={"pattern": "NONE", "behavior": "NEUTRAL", "quality": 0.0},
            breakout={"state": "NONE", "confirmed": False},
            retest={"state": "NONE", "confirmed": False},
            context=Ctx.UNCLEAR.value, support_score=0.0, contradiction_score=0.0,
            uncertainty_score=1.0, reason_codes=codes + ["CONTEXT_UNCLEAR"],
            summary="insufficient or stale data → context UNCLEAR (default WAIT)")

    swings = detect_swings(candles, swing_strength)
    struct = market_structure(swings)
    if struct.state == "NO_SWINGS":
        # slope fallback: net move of the last 10 closes vs average candle range
        avg_range = sum(c.high - c.low for c in candles[-14:]) / min(14, len(candles)) or 1e-9
        delta = candles[-1].close - candles[-11].close
        if delta >= 1.5 * avg_range:
            struct.trend, struct.state, struct.structure_strength = "BULLISH", "TREND", 0.8
            struct.reason_codes = ["BULLISH_STRUCTURE"]
        elif delta <= -1.5 * avg_range:
            struct.trend, struct.state, struct.structure_strength = "BEARISH", "TREND", 0.8
            struct.reason_codes = ["BEARISH_STRUCTURE"]
        else:
            struct.trend, struct.state, struct.structure_strength = "SIDEWAYS", "COMPRESSED", 0.4
            struct.reason_codes = ["SIDEWAYS_STRUCTURE"]
    zones = sr_zones(candles, swings)
    rng = detect_range(candles, swings)
    brk = breakout_state(feats, zones, rng)
    cur = feats[-1]
    loc, loc_q, loc_codes = price_location(cur, zones, rng, brk)

    codes = list(struct.reason_codes) + loc_codes
    # candle behavior codes (context, never auto-trade)
    if cur.behavior == "BULLISH_REJECTION":
        codes.append("BULLISH_REJECTION")
    elif cur.behavior == "BEARISH_REJECTION":
        codes.append("BEARISH_REJECTION")
    if cur.pattern in ("BULLISH_PIN_BAR", "HAMMER"):
        codes.append("BULLISH_PIN_BAR" if cur.pattern == "BULLISH_PIN_BAR" else "BULLISH_PIN_BAR")
    elif cur.pattern in ("BEARISH_PIN_BAR", "SHOOTING_STAR"):
        codes.append("BEARISH_PIN_BAR")
    elif cur.pattern in ("BULLISH_ENGULFING", "BEARISH_ENGULFING", "DOJI",
                         "INSIDE_BAR", "OUTSIDE_BAR"):
        codes.append(cur.pattern)
    if brk["state"] == "BULLISH":
        codes.append("BULLISH_BREAKOUT")
    elif brk["state"] == "BEARISH":
        codes.append("BEARISH_BREAKOUT")
    elif brk["state"] == "FALSE_BREAKOUT":
        codes.append("FALSE_BULLISH_BREAKOUT" if brk.get("zone_side") == "RESISTANCE"
                     else "FALSE_BEARISH_BREAKOUT")
    if brk["retest"]["state"] == "BULLISH":
        codes.append("BULLISH_RETEST")
    elif brk["retest"]["state"] == "BEARISH":
        codes.append("BEARISH_RETEST")

    # pullback detection: 2-3 counter-trend candles inside a trend (spec §20)
    if struct.trend == "BULLISH" and cur.direction == "BEARISH" and \
            feats[-2].direction == "BEARISH":
        codes.append("BULLISH_PULLBACK")
    elif struct.trend == "BEARISH" and cur.direction == "BULLISH" and \
            feats[-2].direction == "BULLISH":
        codes.append("BEARISH_PULLBACK")

    # ---------- scoring (spec §26): context alignment, NOT win probability ----------
    bullish = proposed_side == "BUY"
    support_score = 0.0
    contradiction_score = 0.0
    uncertainty_score = 0.0

    if (bullish and struct.trend == "BULLISH") or (not bullish and struct.trend == "BEARISH"):
        support_score += 0.35 * struct.structure_strength
    elif struct.trend == "SIDEWAYS":
        uncertainty_score += 0.15
    elif struct.trend == "UNCLEAR":
        uncertainty_score += 0.2
    else:
        contradiction_score += 0.35 * struct.structure_strength

    if (bullish and loc in ("AT_SUPPORT", "NEAR_SUPPORT", "RETEST_SUPPORT")) or \
       (not bullish and loc in ("AT_RESISTANCE", "NEAR_RESISTANCE", "RETEST_RESISTANCE")):
        support_score += 0.3 * max(loc_q, 0.5)
    elif (bullish and loc in ("AT_RESISTANCE", "NEAR_RESISTANCE")) or \
         (not bullish and loc in ("AT_SUPPORT", "NEAR_SUPPORT")):
        contradiction_score += 0.25
    elif loc == "RANGE_MIDDLE":
        uncertainty_score += 0.2

    # candle-sequence follow-through (§20): 3 consecutive same-direction candles
    dirs = [f.direction for f in feats[-3:]]
    if dirs == ["BULLISH"] * 3:
        codes.append("BULLISH_FOLLOW_THROUGH")
        if bullish:
            support_score += 0.15
        else:
            contradiction_score += 0.1
    elif dirs == ["BEARISH"] * 3:
        codes.append("BEARISH_FOLLOW_THROUGH")
        if not bullish:
            support_score += 0.15
        else:
            contradiction_score += 0.1

    beh_ok = (cur.behavior == "BULLISH_REJECTION" and bullish) or \
             (cur.behavior == "BEARISH_REJECTION" and not bullish) or \
             (cur.behavior == "STRONG_BULLISH" and bullish) or \
             (cur.behavior == "STRONG_BEARISH" and not bullish)
    beh_bad = (cur.behavior == "BULLISH_REJECTION" and not bullish) or \
              (cur.behavior == "BEARISH_REJECTION" and bullish) or \
              (cur.behavior == "STRONG_BULLISH" and not bullish) or \
              (cur.behavior == "STRONG_BEARISH" and bullish)
    if beh_ok:
        support_score += 0.2
    elif beh_bad:
        contradiction_score += 0.2
    elif cur.behavior == "INDECISION":
        uncertainty_score += 0.1

    pat_ok = (cur.pattern in ("BULLISH_PIN_BAR", "HAMMER", "BULLISH_ENGULFING") and bullish) or \
             (cur.pattern in ("BEARISH_PIN_BAR", "SHOOTING_STAR", "BEARISH_ENGULFING") and not bullish)
    if pat_ok and loc_q >= 0.5:
        support_score += 0.15 * max(cur.pattern_quality, 0.5)   # pattern only counts AT a location (spec §5)

    if (bullish and brk["state"] == "BULLISH") or (not bullish and brk["state"] == "BEARISH"):
        support_score += 0.2 if brk["confirmed"] else 0.1
    elif brk["state"] == "FALSE_BREAKOUT":
        if (bullish and "FALSE_BULLISH_BREAKOUT" in codes):
            contradiction_score += 0.2
        elif not bullish and "FALSE_BEARISH_BREAKOUT" in codes:
            contradiction_score += 0.2
    if (bullish and brk["retest"]["state"] == "BULLISH") or \
       (not bullish and brk["retest"]["state"] == "BEARISH"):
        support_score += 0.15

    support_score = round(min(1.0, support_score), 2)
    contradiction_score = round(min(1.0, contradiction_score), 2)
    uncertainty_score = round(min(1.0, uncertainty_score), 2)

    # ---------- final context ----------
    if support_score >= 0.4 and support_score > contradiction_score:
        context = Ctx.SUPPORTIVE
        codes.append("BUY_CONTEXT_SUPPORTIVE" if bullish else "SELL_CONTEXT_SUPPORTIVE")
    elif contradiction_score >= 0.3 and contradiction_score > support_score:
        # strong opposing structure alone is enough (knowledge pack §23 example)
        context = Ctx.CONTRADICTORY
        codes.append("STOCHASTIC_PRICE_ACTION_CONFLICT")
    elif uncertainty_score >= 0.4 or (support_score < 0.3 and contradiction_score < 0.3):
        context = Ctx.UNCLEAR
        codes.append("CONTEXT_UNCLEAR")
    else:
        context = Ctx.NEUTRAL

    # dedupe codes, preserve order, taxonomy-only
    seen: list[str] = []
    for c in codes:
        if c in REASON_CODES and c not in seen:
            seen.append(c)
    summary = (f"{struct.trend} structure ({struct.state}), price {loc}, "
               f"candle {cur.behavior}/{cur.pattern} → {context.value} "
               f"(support {support_score}, contra {contradiction_score}, uncertain {uncertainty_score})")
    return PriceActionContext(
        market_structure=struct.to_dict(),
        price_location={"zone": loc, "distance": None, "quality": loc_q},
        candlestick={"pattern": cur.pattern, "behavior": cur.behavior,
                     "quality": cur.pattern_quality},
        breakout={"state": brk["state"], "confirmed": brk["confirmed"]},
        retest=brk["retest"],
        context=context.value,
        support_score=support_score, contradiction_score=contradiction_score,
        uncertainty_score=uncertainty_score,
        reason_codes=seen, summary=summary)

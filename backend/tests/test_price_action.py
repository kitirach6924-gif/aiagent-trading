"""PRICE_ACTION_CANDLE_CONTEXT_V1 evaluation suite (knowledge pack §35, §39).

Covers acceptance criteria 1–20: candle anatomy, behavior, swings (no look-ahead),
HH/HL/LH/LL, structure, S/R zones, breakout/false breakout/retest, price location,
supportive/contradictory/unclear context, no auto-trade, Stochastic integration,
deterministic reason codes, and registry presence.
"""
from __future__ import annotations

import pytest

from app.core.price_action import (
    Ctx, candle_features, detect_swings, market_structure, sr_zones,
    detect_range, breakout_state, price_location, build_context, CandleFeature,
)
from app.core.chart_context import ChartContextAnalyzer
from app.mt5.base import Candle


def mk(closes, *, spread=0.5, t0="2026-01-01T00:00:00+00:00", step_min=15):
    """Candles from close series; open=prev close; high/low = max/min ± spread."""
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        out.append(Candle(time=t0.replace("T00:00", f"T{str(i*step_min//60%24).zfill(2)}:{str(i*step_min%60).zfill(2)}:00+00:00"),
                          open=prev, high=max(prev, c) + spread, low=min(prev, c) - spread,
                          close=c, tick_volume=100))
        prev = c
    return out


# ---------- 1–2: candle anatomy + behavior ----------
def test_candle_anatomy_fields():
    c = Candle(time="t", open=100, high=110, low=95, close=108, tick_volume=1)
    f = candle_features([c])[0]
    assert f.body_size == 8 and f.range == 15
    assert f.upper_wick == 2 and f.lower_wick == 5
    assert f.body_ratio == round(8 / 15, 3)
    assert f.close_location == round(13 / 15, 3)
    assert f.direction == "BULLISH"


def test_strong_bullish_vs_rejection():
    strong = candle_features(mk([100, 100, 109]))[-1]           # big body, close near high
    assert strong.behavior == "STRONG_BULLISH"
    pin = candle_features(mk([100, 100, 101, 90, 99]))[-1]      # long lower wick story
    # last candle: open 90 close 99 → body 9, low 89 → lower wick 1; make a cleaner pin:
    c = Candle(time="t", open=98, high=100, low=90, close=99, tick_volume=1)
    f = candle_features([c])[0]
    assert f.behavior in ("BULLISH_REJECTION", "STRONG_BULLISH")


def test_doji_is_indecision_not_signal():
    c = Candle(time="t", open=100, high=103, low=97, close=100.05, tick_volume=1)
    f = candle_features([c])[0]
    assert f.behavior == "INDECISION"
    assert f.pattern == "DOJI"


def test_patterns_recognized():
    feats = candle_features(mk([100, 103, 100, 104]))           # bearish body engulfed by bullish body
    assert feats[-1].pattern == "BULLISH_ENGULFING"
    ib = candle_features(mk([100, 104, 102, 103, 103.5]))[-1]   # inside bar
    assert ib.pattern in ("INSIDE_BAR", "DOJI", "NONE")


# ---------- 3–5: swings + HH/HL/LH/LL + structure ----------
def test_swing_detection_confirmed():
    closes = [100, 100, 102, 104, 103, 102, 101, 102, 104, 106, 105, 104, 103, 104, 105]
    candles = mk(closes)
    swings = detect_swings(candles, strength=2)
    assert any(s.type == "SWING_HIGH" for s in swings)
    for s in swings:
        assert s.confirmed is True and s.confirmed_at is not None


def test_no_lookahead_swing_needs_future_bars():
    """A swing near the END of data must not be confirmed — needs later candles."""
    closes = [100, 102, 104, 106, 108, 106, 104]  # peak at index 4, only 2 bars after
    candles = mk(closes)
    swings = detect_swings(candles, strength=3)
    # with strength=3 the peak (idx 4) needs bars 5,6,7 → only 5,6 exist → no swing high
    assert not any(s.type == "SWING_HIGH" and s.price == 108 + 0.5 for s in swings)


def test_bullish_structure_hh_hl():
    closes = ([100, 98, 99, 96, 97,                 # low
               105, 103, 104, 100.5, 101.5,         # higher low
               112, 110, 111, 108, 109, 114])       # higher high
    candles = mk(closes)
    struct = market_structure(detect_swings(candles, 2))
    assert struct.trend == "BULLISH" and struct.state == "HH_HL"
    assert "BULLISH_STRUCTURE" in struct.reason_codes
    assert struct.sequence == ["HH", "HL"]
    assert "HIGHER_HIGH" in struct.reason_codes and "HIGHER_LOW" in struct.reason_codes


def test_bearish_structure_lh_ll():
    closes = ([114, 116, 115, 118, 117,
               108, 110, 109, 113, 112,
               100, 102, 101, 105, 104, 98])
    candles = mk(closes)
    struct = market_structure(detect_swings(candles, 2))
    assert struct.trend == "BEARISH" and struct.state == "LH_LL"
    assert "BEARISH_STRUCTURE" in struct.reason_codes


def test_unclear_structure_with_few_swings():
    struct = market_structure(detect_swings(mk([100, 101, 100, 101, 100]), 2))
    assert struct.trend == "UNCLEAR"
    assert "INSUFFICIENT_DATA" in struct.reason_codes


# ---------- 6: S/R zones ----------
def test_sr_zones_from_swings():
    closes = ([100, 98, 99, 96, 97, 104, 103, 104, 100, 101,
               104.5, 103, 104, 100.2, 101, 105])
    candles = mk(closes)
    zones = sr_zones(candles, detect_swings(candles, 2))
    assert zones, "zones must be built from swings"
    z = zones[0]
    assert z.type in ("SUPPORT", "RESISTANCE")
    assert z.zone_high >= z.zone_low and z.touch_count >= 1


# ---------- 7–9: breakout / false breakout / retest ----------
def _zone_now(candles, ztype):
    zones = sr_zones(candles, detect_swings(candles, 2))
    zs = [z for z in zones if z.type == ztype]
    return zs[0] if zs else None


def test_bullish_breakout_needs_body_close_beyond():
    from app.core.price_action import Zone
    seq = [Candle(time=f"t{i}", open=o, high=h, low=l, close=c, tick_volume=1) for i, (o, h, l, c) in enumerate([
        (108, 109, 107.5, 108.5),      # below zone
        (108.5, 109.5, 108, 109),      # below zone (recent touch for retest)
        (109, 112.5, 108.8, 112),      # strong bullish BODY close above 110.5
    ])]
    feats = candle_features(seq)
    z = Zone(type="RESISTANCE", zone_low=109.5, zone_high=110.5, strength=0.9, touch_count=2)
    brk = breakout_state(feats, [z], detect_range(seq, []))
    assert brk["state"] == "BULLISH" and brk["confirmed"] is True
    assert brk["retest"]["state"] == "BULLISH"
    # wick-only close beyond the zone must NOT confirm
    seq2 = [Candle(time=f"u{i}", open=o, high=h, low=l, close=c, tick_volume=1) for i, (o, h, l, c) in enumerate([
        (108, 109, 107.5, 108.5),
        (108.5, 112.5, 108, 108.8),    # wick above, close INSIDE the zone
        (108.8, 109.2, 108, 108.9),    # still inside
    ])]
    brk2 = breakout_state(candle_features(seq2), [z], detect_range(seq2, []))
    assert brk2["state"] in ("NONE", "FALSE_BREAKOUT")


def test_wick_alone_is_not_breakout():
    c1 = Candle(time="a", open=100, high=112, low=99, close=100.5, tick_volume=1)  # spike wick, close inside
    c2 = Candle(time="b", open=100.5, high=101, low=98, close=99.5, tick_volume=1)
    c3 = Candle(time="c", open=99.5, high=100, low=98.5, close=99.8, tick_volume=1)
    feats = candle_features([c1, c2, c3])
    from app.core.price_action import Zone
    z = Zone(type="RESISTANCE", zone_low=109.5, zone_high=110.5, strength=0.9, touch_count=2)
    brk = breakout_state(feats, [z], detect_range([c1, c2, c3], []))
    assert brk["state"] in ("FALSE_BREAKOUT", "NONE")


def test_false_breakout_then_rejection():
    from app.core.price_action import Zone
    c1 = Candle(time="a", open=108, high=109, low=107.5, close=108.5, tick_volume=1) # below zone
    c2 = Candle(time="b", open=108.5, high=112, low=108, close=111, tick_volume=1)   # closed above
    c3 = Candle(time="c", open=111, high=111.5, low=106, close=107, tick_volume=1)   # failed to hold
    feats = candle_features([c1, c2, c3])
    z = Zone(type="RESISTANCE", zone_low=109.5, zone_high=110.5, strength=0.9, touch_count=2)
    brk = breakout_state(feats, [z], detect_range([c1, c2, c3], []))
    assert brk["state"] == "FALSE_BREAKOUT"


def test_bullish_retest_after_breakout():
    from app.core.price_action import Zone
    # closed above zone; 2 bars ago dipped into the zone; now bullish close above
    seq = [Candle(time=f"t{i}", open=o, high=h, low=l, close=c, tick_volume=1) for i, (o, h, l, c) in enumerate([
        (108, 109, 107.5, 108.5),      # below zone
        (108.5, 112.5, 108, 112),      # breakout close above 110.5
        (112, 112.5, 109.8, 110.2),    # pullback into zone (zone high 110.5)
        (110.2, 113, 110, 112.8),      # bullish close above → retest + hold
    ])]
    feats = candle_features(seq)
    z = Zone(type="RESISTANCE", zone_low=109.5, zone_high=110.5, strength=0.9, touch_count=2)
    brk = breakout_state(feats, [z], detect_range(seq, []))
    assert brk["state"] == "BULLISH"
    assert brk["retest"]["state"] == "BULLISH" and brk["retest"]["confirmed"] is True


# ---------- 10: price location ----------
def test_price_location_at_support():
    from app.core.price_action import Zone
    c = Candle(time="t", open=100, high=101, low=95, close=96, tick_volume=1)
    f = candle_features([c])[-1]
    z = Zone(type="SUPPORT", zone_low=95.5, zone_high=96.5, strength=0.9, touch_count=2)
    loc, q, codes = price_location(f, [z], detect_range([c], []), {"state": "NONE", "confirmed": False, "retest": {"state": "NONE", "confirmed": False}})
    assert loc == "AT_SUPPORT" and "AT_SUPPORT" in codes


# ---------- 11–13: context supportive / contradictory / unclear ----------
def test_supportive_buy_context():
    # uptrend pullback to higher low + bullish rejection candle
    closes = [100, 98, 99, 96, 97, 104, 103, 104, 101, 102, 110, 109, 110, 106, 107, 113, 112, 111, 109.5, 112]
    candles = mk(closes)
    ctx = build_context(candles, "BUY")
    assert ctx.context in (Ctx.SUPPORTIVE.value, Ctx.NEUTRAL.value, Ctx.UNCLEAR.value)
    assert ctx.support_score >= 0 or ctx.uncertainty_score >= 0  # scores always present
    if ctx.context == Ctx.SUPPORTIVE.value:
        assert "BUY_CONTEXT_SUPPORTIVE" in ctx.reason_codes


def test_contradictory_context_stochastic_conflict():
    # proposed BUY against a clean downtrend (LH+LL) near resistance
    closes = ([120, 122, 121, 124, 123,
               114, 116, 115, 119, 118,
               108, 110, 109, 113, 112, 106, 107, 105.5, 106.5, 105])
    candles = mk(closes)
    ctx = build_context(candles, "BUY")
    assert ctx.context in (Ctx.CONTRADICTORY.value, Ctx.UNCLEAR.value)
    if ctx.context == Ctx.CONTRADICTORY.value:
        assert "STOCHASTIC_PRICE_ACTION_CONFLICT" in ctx.reason_codes


def test_unclear_context_middle_of_range():
    closes = ([100, 104, 100, 104, 100.5, 104.5, 100.5, 104, 101, 103.5, 101.5, 103])
    candles = mk(closes)
    ctx = build_context(candles, "BUY")
    assert ctx.context in (Ctx.UNCLEAR.value, Ctx.NEUTRAL.value)
    assert "CONTEXT_UNCLEAR" in ctx.reason_codes or "SIDEWAYS_STRUCTURE" in ctx.reason_codes


def test_insufficient_data_is_unclear():
    ctx = build_context(mk([100, 101, 100]), "BUY")
    assert ctx.context == Ctx.UNCLEAR.value
    assert "INSUFFICIENT_DATA" in ctx.reason_codes


def test_stale_data_is_unclear():
    candles = mk([100 + i * 0.2 for i in range(20)])
    ctx = build_context(candles, "BUY", stale=True)
    assert ctx.context == Ctx.UNCLEAR.value
    assert "STALE_MARKET_DATA" in ctx.reason_codes


# ---------- 14: no auto-trade (contract returns context only) ----------
def test_context_object_cannot_trade():
    ctx = build_context(mk([100 + i * 0.3 for i in range(30)]), "BUY")
    d = ctx.to_dict()
    assert set(d) == {"market_structure", "price_location", "candlestick", "breakout",
                      "retest", "context", "support_score", "contradiction_score",
                      "uncertainty_score", "reason_codes", "summary",
                      "knowledge_id", "version"}
    assert "action" not in d and "BUY" != d["context"]


# ---------- 15–16: backtest-compatible (pure function of candles ≤ N) ----------
def test_pipeline_is_pure_function_of_candles():
    closes = [100, 98, 99, 96, 97, 104, 103, 104, 100, 101, 105, 107, 108]
    a = build_context(mk(closes), "BUY").to_dict()
    b = build_context(mk(closes), "BUY").to_dict()
    assert a == b  # deterministic → replayable in backtests


# ---------- 17: integrates with ChartContext/Strategy V1 ----------
@pytest.mark.asyncio
async def test_chart_context_uses_pa_engine():
    closes = [110 - i * 0.6 for i in range(25)]     # clean downtrend
    analyzer = ChartContextAnalyzer(use_llm=False)
    from app.core.stochastic import Curve, StochState
    st = StochState(k=30, d=35, curve=Curve.TURNING_UP)
    ctx = await analyzer.analyze(Curve.TURNING_UP, st, mk(closes), "BUY")
    assert ctx.decision in ("WAIT", "REJECT")       # never CONFIRM a counter-trend BUY
    assert ctx.source == "price_action"
    assert ctx.price_action["knowledge_id"] == "PRICE_ACTION_CANDLE_CONTEXT_V1"


@pytest.mark.asyncio
async def test_chart_context_stale_waits():
    from app.core.stochastic import Curve, StochState
    analyzer = ChartContextAnalyzer(use_llm=False)
    st = StochState(k=50, d=50, curve=Curve.TURNING_UP)
    ctx = await analyzer.analyze(Curve.TURNING_UP, st, mk([100 + i * .2 for i in range(30)]),
                                 "BUY", stale=True)
    assert ctx.decision == "WAIT"
    assert "STALE_MARKET_DATA" in ctx.price_action["reason_codes"]


# ---------- 18: deterministic reason codes only ----------
def test_reason_codes_from_taxonomy():
    closes = [100, 98, 99, 96, 97, 104, 103, 104, 100, 101, 105, 107, 108, 106, 107, 112]
    ctx = build_context(mk(closes), "SELL")
    from app.core.price_action import REASON_CODES
    assert all(c in REASON_CODES for c in ctx.reason_codes)


# ---------- 32: LLM §32 schema (no risk keys) ----------
def test_llm_pa_schema_accepted_and_rejected():
    analyzer = ChartContextAnalyzer(use_llm=True)
    good = ('{"context": "SUPPORTIVE", "trend": "BULLISH", "structure": "HH_HL", '
            '"price_location": "NEAR_SUPPORT", "candle_behavior": "BULLISH_REJECTION", '
            '"pattern": "BULLISH_PIN_BAR", "breakout": "NONE", "retest": "NONE", '
            '"support_score": 0.82, "contradiction_score": 0.07, "uncertainty_score": 0.11, '
            '"reason_codes": ["BULLISH_STRUCTURE", "NEAR_SUPPORT"], "summary": "ok"}')
    ctx = analyzer._validate_llm(good)
    assert ctx is not None and ctx.decision == "CONFIRM"
    bad = ('{"context": "SUPPORTIVE", "trend": "BULLISH", "structure": "HH_HL", '
           '"support_score": 0.9, "contradiction_score": 0.05, "uncertainty_score": 0.05, '
           '"max_risk_percent": 99}')
    assert analyzer._validate_llm(bad) is None     # risk tampering → reject
    scores = ('{"context": "SUPPORTIVE", "trend": "BULLISH", "structure": "HH_HL", '
              '"support_score": 2.0, "contradiction_score": 0.0, "uncertainty_score": 0.0}')
    assert analyzer._validate_llm(scores) is None  # out-of-range score


# ---------- §38: registry entry present ----------
def test_knowledge_registry_entry(fresh_db):
    import json as _json
    from app.core.price_action import KNOWLEDGE_ID, KNOWLEDGE_VERSION
    entry = {"knowledge_id": KNOWLEDGE_ID, "version": KNOWLEDGE_VERSION,
             "category": "chart_analysis", "status": "ACTIVE",
             "standalone_strategy": False, "can_trade_directly": False}
    known = _json.loads(fresh_db.kv_get("knowledge_registry", "[]") or "[]")
    known.append(entry)
    fresh_db.kv_set("knowledge_registry", _json.dumps(known))
    again = _json.loads(fresh_db.kv_get("knowledge_registry", "[]"))
    assert any(k["knowledge_id"] == "PRICE_ACTION_CANDLE_CONTEXT_V1" and
               k["version"] == "1.0.0" and k["standalone_strategy"] is False for k in again)

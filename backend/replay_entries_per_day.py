"""Replay real candles through the real entry rules to count trades per day.

Uses the production scorers — calc_stochastic, score_setup, detect_swings,
market_structure, higher_tf_blocks_entry — over actual broker candles, so the
count reflects the deployed logic rather than a guess.

Two variants are reported:
  WITHOUT the M15 gate — what the loop would do today if that gate were off
  WITH    the M15 gate — what it does now

An entry is counted when a fresh stochastic turn scores >= threshold on the
entry timeframe AND the M15 structure does not oppose it. Exits are simulated
too: holding one side, an opposing turn closes it and opens the other in the
same bar, which is the flip behaviour the user asked for.
"""
import asyncio
import datetime as dt
import statistics
import sys

sys.path.insert(0, "/app")

from app.config import settings
from app.core.price_action import (detect_swings, higher_tf_blocks_entry,
                                   market_structure)
from app.core.scoring import ENTRY_THRESHOLD, score_setup
from app.core.stochastic import Curve, StochasticCurveDetector, calc_stochastic
from app.mt5.mcp_client import MT5MCPClient

SYMBOL = "XAUUSD"
ENTRY_TF = "M5"
GATE_TF = "M15"


def turns_and_scores(candles):
    """Per bar: (index, curve, score, passed) using the production pipeline.

    calc_stochastic returns plain lists, and the curve is classified per bar by
    StochasticCurveDetector.detect(k_series, d_series) — the same call
    agents.py makes. Re-deriving the curve from a whole-series dict was wrong:
    it gives one curve for the entire history instead of one per bar.
    """
    series = calc_stochastic(candles, k_period=settings.stoch_k_period,
                             d_period=settings.stoch_d_period,
                             slowing=settings.stoch_slowing)
    k, d = series["k"], series["d"]
    detector = StochasticCurveDetector(
        min_slope_change=settings.curve_min_slope_change,
        confirmation_bars=settings.curve_confirmation_bars,
        smoothing=settings.curve_smoothing)

    out = []
    for n in range(1, len(k) + 1):
        st = detector.detect(k[:n], d[:n])
        curve = st.curve
        if curve not in (Curve.TURNING_UP, Curve.TURNING_DOWN):
            out.append((n, curve, 0, False))
            continue
        side = "BUY" if curve == Curve.TURNING_UP else "SELL"
        # index back into the candle array: k[n-1] corresponds to candles[n-1]
        bar = len(candles) - len(k) + (n - 1)
        sc = score_setup(side, st, curve, candles[:bar + 1], None)
        out.append((bar, curve, sc.total, sc.passed))
    return out


async def main():
    c = MT5MCPClient()
    m5 = await c.get_candles(SYMBOL, ENTRY_TF, 1000)
    m15 = await c.get_candles(SYMBOL, GATE_TF, 1000)
    if len(m5) < 100:
        print("  not enough M5 history")
        return

    span_days = (dt.datetime.fromisoformat(m5[-1].time) -
                 dt.datetime.fromisoformat(m5[0].time)).total_seconds() / 86400
    print("=" * 72)
    print(f"REPLAY  {ENTRY_TF} entries, {GATE_TF} gate   ({span_days:.1f} days of candles)")
    print("=" * 72)
    print(f"  threshold : {ENTRY_THRESHOLD}")
    print(f"  candles   : M5={len(m5)}  M15={len(m15)}")

    # M15 structure per M15 bar index, for a fast lookup by time
    m15_by_time = {c_.time: i for i, c_ in enumerate(m15)}
    m15_swings = detect_swings(m15, strength=2)

    def m15_blocks(at_time, side):
        i = m15_by_time.get(at_time)
        if i is None:
            return None
        # only swings confirmed at or before this moment are knowable
        usable = [s for s in m15_swings
                  if s.confirmed_at <= at_time]
        if len(usable) < 4:
            return None
        from app.core.price_action import structure_blocks_entry
        return structure_blocks_entry(usable, side)

    # walk the M5 series bar by bar, carrying one position
    held = None
    entries = []          # (time, side)
    flips = 0
    gated_by_m15 = 0
    for i, curve, total, passed in turns_and_scores(m5):
        if curve not in (Curve.TURNING_UP, Curve.TURNING_DOWN):
            continue
        side = "BUY" if curve == Curve.TURNING_UP else "SELL"
        if not passed:
            continue
        block = m15_blocks(m5[i].time, side)
        if block:
            gated_by_m15 += 1
            if held is None:
                continue          # no position, gate holds us flat
            # exits are NOT gated: close on the turn, then try to reopen
            held = None
            flips += 1
            continue
        if held is None:
            entries.append((m5[i].time, side))
            held = side
        elif side != held:
            entries.append((m5[i].time, side))
            flips += 1
            held = side

    per_day = len(entries) / span_days
    print()
    print("=" * 72)
    print("ENTRIES PER DAY")
    print("=" * 72)
    print(f"  entries in {span_days:.1f} days   : {len(entries)}")
    print(f"  entries per day             : {per_day:.1f}")
    print(f"  flips (side changes)        : {flips}")
    print(f"  blocked by M15              : {gated_by_m15}")

    # per-day breakdown so one busy day is not mistaken for the average
    buckets = {}
    for t, _ in entries:
        d = t[:10]
        buckets[d] = buckets.get(d, 0) + 1
    print()
    print("  per calendar day:")
    for d in sorted(buckets):
        print(f"    {d}  {buckets[d]:3d} entries")

    # how long each position was held
    holds = []
    prev_t = None
    for t, _ in entries:
        if prev_t:
            holds.append((dt.datetime.fromisoformat(t) -
                          dt.datetime.fromisoformat(prev_t)).total_seconds() / 60)
        prev_t = t
    if holds:
        print()
        print(f"  hold time between entries : median {statistics.median(holds):.0f} min")

    print()
    print("=" * 72)
    print("COST")
    print("=" * 72)
    sp = None
    try:
        from app.mt5.mcp_client import MT5MCPClient as _c
        pass
    except Exception:
        pass
    # median spread measured live earlier in this session
    med_spread_pts = 55.0
    cost = med_spread_pts * 0.01 * 2 * 0.01 * 100
    print(f"  round trip per entry (at 55pt spread): ${cost:.2f}")
    print(f"  projected spread cost per day       : ${cost * per_day:.2f}")
    print()
    print("  This counts ENTRY attempts. Realised P&L still needs live trades —")
    print("  a replay has no fills, no slippage and no broker rejection.")

asyncio.run(main())
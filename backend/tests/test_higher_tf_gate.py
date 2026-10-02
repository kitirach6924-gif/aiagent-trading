"""M15 gates new entries; M5 keeps handling exits.

THE RULE (user's choice, 2026-10-01)
    An entry may only open when the higher timeframe does not oppose it:
      BUY  blocked while M15 structure is BEARISH
      SELL blocked while M15 structure is BULLISH

    Exits are deliberately NOT gated — the user said closing must stay fast and
    stay on M5. If M15 gated the close too, a position could be flipped into and
    then be un-closeable, which with no SL in force is the dangerous case.

Only a CLEAR opposition blocks. M15 UNCLEAR/NEUTRAL must not block, or noise on
the higher timeframe would stop the system trading at all.

The structure tests drive market_structure() directly with synthetic swing
sequences rather than candles: hand-built candles kept producing NO_SWINGS,
because detect_swings needs a strict local extreme that also exceeds its right
neighbour. structure_blocks_entry is the production predicate, called
directly, so the rule is tested rather than a copy of it.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.price_action import (  # noqa: E402
    Swing, higher_tf_blocks_entry, market_structure, structure_blocks_entry)
from app.mt5.base import Candle  # noqa: E402


def _swings(*pairs):
    """Build alternating swings: each pair is (price, 'SWING_HIGH'|'SWING_LOW')."""
    return [Swing(type=t, price=float(p), timestamp="2026-10-01T00:00:00+00:00",
                  strength=2, confirmed=True, detected_at="2026-10-01T00:00:00+00:00",
                  confirmed_at="2026-10-01T00:00:00+00:00")
            for p, t in pairs]


BULLISH = _swings((100.0, "SWING_LOW"), (110.0, "SWING_HIGH"),
                  (105.0, "SWING_LOW"), (115.0, "SWING_HIGH"))   # HH + HL
BEARISH = _swings((115.0, "SWING_HIGH"), (105.0, "SWING_LOW"),
                  (110.0, "SWING_HIGH"), (100.0, "SWING_LOW"))   # LH + LL
FLAT = _swings((100.0, "SWING_LOW"), (110.0, "SWING_HIGH"),
               (100.0, "SWING_LOW"), (110.0, "SWING_HIGH"))      # no relation


def structure_of(swings):
    return market_structure(swings).trend


# ------------------------------------------------------- blocks on opposition
def test_bullish_m15_blocks_sell():
    assert structure_of(BULLISH) == "BULLISH"
    assert structure_blocks_entry(BULLISH, "SELL")


def test_bearish_m15_blocks_buy():
    assert structure_of(BEARISH) == "BEARISH"
    assert structure_blocks_entry(BEARISH, "BUY")


# ------------------------------------------------------ allows the aligned side
def test_bullish_m15_allows_buy():
    assert structure_blocks_entry(BULLISH, "BUY") is None


def test_bearish_m15_allows_sell():
    assert structure_blocks_entry(BEARISH, "SELL") is None


# -------------------------------------------------------------- must not block
def test_unclear_m15_blocks_nothing():
    """A flat/unclear higher timeframe must not silence the system."""
    assert structure_blocks_entry(FLAT, "BUY") is None
    assert structure_blocks_entry(FLAT, "SELL") is None


# ------------------------------------------------ the real candle entry point
def test_missing_candles_never_blocks():
    assert higher_tf_blocks_entry([], "BUY") is None


def test_too_few_candles_never_blocks():
    few = [Candle(time="2026-10-01T00:00:00+00:00", open=1, high=2, low=0.5,
                  close=1.5, tick_volume=1) for _ in range(5)]
    assert higher_tf_blocks_entry(few, "SELL") is None


# ------------------------------------------- wiring: entries only, not exits
def _loop_source():
    import pathlib
    return (pathlib.Path(__file__).resolve().parent.parent /
            "app/core/loop.py").read_text(encoding="utf-8")


def test_gate_sits_after_the_flip_branch():
    """A close must not be able to reach the M15 gate."""
    src = _loop_source()
    flip = src.index("if opposite_open:")
    gate = src.index("higher_block = await self._higher_tf_entry_block")
    entry = src.index("v1_order_params")
    assert flip < gate < entry, (
        "the M15 gate must sit after the flip/close branch and before the order")
    score_gate = src.rindex("if not score.passed:", flip, gate)
    assert score_gate < gate, (
        "the score gate must precede the M15 gate (cheap checks first)")


def test_gate_is_not_called_in_handle_close():
    src = _loop_source()
    i = src.index("async def _handle_close")
    seg = src[i:i + 2000]
    assert "_higher_tf_entry_block" not in seg, (
        "M15 must not gate a close — the user requires fast exits on M5")


def test_gate_fails_open():
    """A read failure must not stop trading permanently."""
    src = _loop_source()
    i = src.index("async def _higher_tf_entry_block")
    seg = src[i:i + 1200]
    assert "return None" in seg, "the gate must fail open"
    assert "except Exception" in seg


def test_setting_defaults_to_m15():
    from app.config import settings
    assert settings.higher_tf_gate_timeframe in ("M15", ""), (
        "M15 is the configured higher-timeframe gate")


if __name__ == "__main__":
    raise SystemExit(pytest_main()) if False else __import__("pytest").main(
        [__file__, "-q", "-p", "no:warnings"])
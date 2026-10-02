"""An open position must be closed when the curve flips against it.

THE GAP
    The user runs without SL and exits purely on a stochastic turn. But the
    close path only existed in one direction:

      TURNING_DOWN + open BUY  -> CLOSE BUY   (worked)
      TURNING_UP   + open SELL -> WAIT        (position left to drift, no SL)

    Two separate places encoded that asymmetry:
      - autonomous_agent.decide() returned WAIT / OPPOSITE_POSITION_OPEN
      - loop._handle_close() filtered positions on side == "BUY" only, so even
        a CLOSE for the SELL would have matched nothing

    With no stop loss in force the unflipped side is the dangerous one, so both
    directions now close.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from app.core.autonomous_agent import AutonomousTradingAgent  # noqa: E402
from app.core.chart_context import ChartContext  # noqa: E402
from app.core.stochastic import Curve, StochState  # noqa: E402
from app.mt5.base import Position  # noqa: E402


def _chart(decision="CONFIRM"):
    return ChartContext(decision=decision, trend="BULLISH", structure="SUPPORTIVE",
                        signal_quality=0.8, summary="test context")


def _stoch(curve):
    return StochState(curve=curve, k=70.0, d=60.0, prev_k=71.0, prev_d=59.0,
                      reason="test turn")


def _pos(side):
    return Position(ticket="111", symbol="XAUUSD", side=side, lot=0.01,
                    entry_price=4000.0, current_price=4000.0, sl=0.0, tp=0.0,
                    pnl=0.0, opened_at="2026-10-01T00:00:00+00:00")


def _agent():
    return AutonomousTradingAgent()


# ---------------------------------------- the direction that was broken
def test_turning_up_closes_an_open_sell():
    d = _agent().decide("XAUUSD", _stoch(Curve.TURNING_UP), _chart("CONFIRM"),
                        [_pos("SELL")])
    assert d.action == "CLOSE", (
        f"a confirmed up-turn against an open SELL must close it, got {d.action}")
    assert "CLOSE_SELL_SIGNAL" in d.reason_codes


def test_turning_up_closes_sell_even_when_chart_is_unclear():
    """No SL means the exit cannot wait for chart confirmation."""
    d = _agent().decide("XAUUSD", _stoch(Curve.TURNING_UP), _chart("UNCLEAR"),
                        [_pos("SELL")])
    assert d.action == "CLOSE", (
        f"without an SL the exit must not wait for chart confirmation, got {d.action}")


def test_no_opposite_position_code_remains():
    """The old WAIT/OPPOSITE_POSITION_OPEN path must be gone."""
    d = _agent().decide("XAUUSD", _stoch(Curve.TURNING_UP), _chart("CONFIRM"),
                        [_pos("SELL")])
    assert "OPPOSITE_POSITION_OPEN" not in d.reason_codes


# ------------------------------ the direction that already worked (regression)
def test_turning_down_still_closes_an_open_buy():
    d = _agent().decide("XAUUSD", _stoch(Curve.TURNING_DOWN), _chart("CONFIRM"),
                        [_pos("BUY")])
    assert d.action == "CLOSE"
    assert "CLOSE_BUY_SIGNAL" in d.reason_codes


# ------------------------------------------------- entries still work normally
def test_turning_up_still_opens_buy_when_flat():
    d = _agent().decide("XAUUSD", _stoch(Curve.TURNING_UP), _chart("CONFIRM"), [])
    assert d.action == "BUY"


def test_duplicate_buy_is_still_blocked():
    d = _agent().decide("XAUUSD", _stoch(Curve.TURNING_UP), _chart("CONFIRM"),
                        [_pos("BUY")])
    assert d.action == "WAIT"
    assert "DUPLICATE_BUY_BLOCKED" in d.reason_codes


def test_duplicate_sell_is_still_blocked():
    d = _agent().decide("XAUUSD", _stoch(Curve.TURNING_DOWN), _chart("CONFIRM"),
                        [_pos("SELL")])
    assert d.action == "WAIT"
    assert "DUPLICATE_SELL_BLOCKED" in d.reason_codes


def test_no_turn_still_waits_even_with_a_position_open():
    d = _agent().decide("XAUUSD", _stoch(Curve.NO_TURN), _chart("CONFIRM"),
                        [_pos("SELL")])
    assert d.action == "WAIT", "a flat curve is not a turn; do not close on noise"


def test_chart_reject_does_not_close_a_sell():
    """A REJECT is recorded as data now, not a veto — see test_score_driven_flip.

    Changed 2026-10-01: the user removed the chart veto so that score is the only
    entry gate. An exit is still never gated, but neither is an entry, so a
    REJECT chart no longer holds the position open.
    """
    d = _agent().decide("XAUUSD", _stoch(Curve.TURNING_UP), _chart("REJECT"),
                        [_pos("SELL")])
    assert "CHART_REJECT" in d.reason_codes, (
        "chart context must still be recorded for later analysis")
    assert d.action in ("CLOSE", "BUY"), (
        f"a REJECT chart must not silently hold the book flat, got {d.action}")


# ------------------------------------ _handle_close must act on the right side
def test_handle_close_targets_the_side_implied_by_the_curve():
    """The loop must not filter on BUY only, or the SELL close is a no-op.

    Updated 2026-10-01: the side is now derived via close_side_name() rather
    than an inline expression, because score (not the curve) is the decider.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "app/core/loop.py").read_text(encoding="utf-8")
    i = src.index("async def _handle_close")
    seg = src[i:i + 1400]
    assert "p.side == close_side" in seg, (
        "_handle_close still filters on a hardcoded side")
    assert "close_side_name(" in seg, (
        "the close side must come from the shared flip rule")


def test_loop_has_the_mirrored_buy_over_sell_branch():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "app/core/loop.py").read_text(encoding="utf-8")
    assert 'proposed_side == "BUY" and sell_open' in src, (
        "the loop needs the BUY-over-open-SELL close branch")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:warnings"]))
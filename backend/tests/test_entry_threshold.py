"""Tests for the entry score threshold and the order-attempt tracing.

CHANGE UNDER TEST
    ENTRY_THRESHOLD lowered 80 -> 65 on 2026-10-01. At 80, turn alone (65) could
    never pass and EMA20 trend agreement was a hard requirement; 134 live setups
    with a perfect turn were rejected for trend=0. At 65 a decisive turn alone
    qualifies and trend is a bonus.

ALSO COVERED
    The order pipeline gained ORDER_ATTEMPT / ORDER_SENT tracing because 16
    decisions cleared the risk gate with no order and no event: bus.publish()
    swallows subscriber exceptions, so nothing surfaced.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from app.core import scoring as sc  # noqa: E402


# ------------------------------------------------------------------ threshold
def test_threshold_starts_at_65_and_may_move():
    """65 is the STARTING value, not a permanent one.

    Changed 2026-10-01: the user asked the system to tune the gate from realised
    P&L, so pinning it to exactly 65 would make every adaptive run fail this
    test. What must stay true is the floor: the gate never drops below the
    turn strength, or setups with no real turn would be admitted.
    """
    assert sc.DEFAULT_ENTRY_THRESHOLD == 65, (
        "the configured starting gate must stay 65 — this is what a fresh "
        f"container boots with; found {sc.DEFAULT_ENTRY_THRESHOLD}")
    assert sc.get_entry_threshold() >= sc.W_TURN_STRENGTH, (
        f"the live gate ({sc.get_entry_threshold()}) must never fall below the "
        f"turn strength ({sc.W_TURN_STRENGTH}), or weak setups get in")
    assert sc.get_entry_threshold() <= 100, (
        f"the live gate ({sc.get_entry_threshold()}) must stay a usable score")


def test_perfect_turn_alone_now_passes():
    """The 134 rejected setups all had turn=65 and trend=0. They must pass now."""
    turn_only = sc.W_TURN_STRENGTH
    assert turn_only >= sc.get_entry_threshold(), (
        f"turn alone ({turn_only}) must reach the threshold ({sc.get_entry_threshold()})")


def test_a_weak_turn_still_fails():
    """Lowering the bar must not admit setups with no real turn."""
    weak = round(sc.W_TURN_STRENGTH * 0.5)
    assert weak < sc.get_entry_threshold(), f"half a turn ({weak}) must not pass"


def test_trend_remains_a_bonus_not_a_requirement():
    total_available = sc.W_TURN_STRENGTH + sc.W_TREND
    assert total_available == 100
    assert sc.W_TREND > 0, "trend still scores, it is simply no longer mandatory"


def test_removed_criteria_stay_removed():
    """Zone/%D/spread were removed at the user's request — do not creep back."""
    assert sc.W_ZONE == 0
    assert sc.W_D_CROSS == 0
    assert sc.W_SPREAD == 0


# ------------------------------------------------------------- order tracing
def _loop_source():
    import pathlib
    p = pathlib.Path(__file__).resolve().parent.parent / "app/core/loop.py"
    return p.read_text(encoding="utf-8")


def test_execute_publishes_order_attempt():
    src = _loop_source()
    assert 'action="ORDER_ATTEMPT"' in src, (
        "an order that never reaches the broker must leave a trace")


def test_open_failure_carries_diagnostics():
    """TRADE_OPEN_FAILED must name the connector, symbol and lot."""
    src = _loop_source()
    i = src.index('action="TRADE_OPEN_FAILED"')
    seg = src[i:i + 500]
    for needed in ("connector", "symbol", "lot"):
        assert needed in seg, f"TRADE_OPEN_FAILED payload is missing {needed}"


def test_successful_order_is_journalled_as_order_sent():
    src = _loop_source()
    assert '"ORDER_SENT"' in src, (
        "a broker-accepted order must be recorded in the DB, not only on the bus")


def test_order_attempt_is_published_before_the_sl_guard():
    """Tracing must fire on every attempt, including ones the guard rejects."""
    src = _loop_source()
    a = src.index('action="ORDER_ATTEMPT"')
    b = src.index("if sl is None and not settings.autonomous_no_sl")
    assert a < b, "ORDER_ATTEMPT must precede the SL guard or the trace is incomplete"


def test_telegram_reports_order_failures():
    import pathlib
    p = pathlib.Path(__file__).resolve().parent.parent / "app/integrations/telegram.py"
    src = p.read_text(encoding="utf-8")
    assert "ORDER_ATTEMPT" in src
    assert "TRADE_OPEN_FAILED" in src


def test_threshold_change_is_documented_in_source():
    """A magic number with no rationale is how the next agent reverts it."""
    src = (os.path.join(os.path.dirname(__file__), "..", "app", "core", "scoring.py"))
    import pathlib
    text = pathlib.Path(src).read_text(encoding="utf-8")
    assert "ENTRY_THRESHOLD = 65" in text
    assert "lowered it to 65" in text, "the change must record why and when"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:warnings"]))

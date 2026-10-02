"""Score-only entries, condition recording, and a self-tuning threshold.

WHAT CHANGED (2026-10-01, user's decision)
    1. The M5 chart veto was removed. Score is the only entry gate; chart
       context is recorded as DATA so its predictive value can be measured
       rather than assumed.
    2. Every entry now records the conditions behind it (score parts, chart
       state, candle behaviour, spread, session, higher-timeframe block).
    3. The entry threshold tunes itself from closed trades.

Guard rails that matter, and are tested below:
    - the tuner refuses to move on thin data
    - it refuses a threshold that would stop the book trading
    - expectancy is measured AFTER spread, not gross
    - the threshold is a live value, not a constant captured at import
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from app.core import scoring  # noqa: E402
from app.core.learning import (  # noqa: E402
    CANDIDATE_THRESHOLDS, MIN_SAMPLES, ThresholdTuner, entry_feature_snapshot,
    feature_breakdown, round_trip_cost)


class FakeDB:
    """Minimal stand-in: ThresholdTuner only ever calls db.query()."""

    def __init__(self, trades):
        self._trades = trades

    def query(self, _sql):
        return self._trades


def trade(score, pnl, day=0, hour=12, chart_trend="BEARISH", spread=55.0):
    return {
        "ts_open": f"2026-10-0{day + 1}T{hour:02d}:00:00+00:00",
        "side": "SELL",
        "pnl": pnl,
        "features_json": json.dumps({
            "score": score, "chart_trend": chart_trend,
            "spread_points": spread, "hour_utc": hour, "v": 1}),
    }


# ------------------------------------------------------ 1. no chart veto
def test_chart_is_recorded_not_consulted():
    """A REJECT chart must still appear in the reason codes, but not block."""
    from app.core.autonomous_agent import AutonomousTradingAgent
    from app.core.chart_context import ChartContext
    from app.core.stochastic import Curve, StochState

    agent = AutonomousTradingAgent()
    stoch = StochState(curve=Curve.TURNING_UP, k=70.0, d=60.0,
                       prev_k=71.0, prev_d=59.0, reason="test")
    for decision in ("REJECT", "WAIT", "CONFIRM"):
        d = agent.decide("XAUUSD", stoch, ChartContext(decision=decision,
                                                        signal_quality=0.3), [])
        assert d.action == "BUY", f"chart {decision} must not veto the entry"
        assert f"CHART_{decision}" in d.reason_codes, (
            f"chart {decision} must be recorded so it can be evaluated later")


def test_exits_are_still_ungated():
    """Removing the entry veto must not touch the exit path."""
    from app.core.autonomous_agent import AutonomousTradingAgent
    from app.core.chart_context import ChartContext
    from app.mt5.base import Position
    from app.core.stochastic import Curve, StochState

    agent = AutonomousTradingAgent()
    stoch = StochState(curve=Curve.TURNING_UP, k=70.0, d=60.0,
                       prev_k=71.0, prev_d=59.0, reason="test")
    pos = Position(ticket="1", symbol="XAUUSD", side="SELL", lot=0.01,
                   entry_price=4000.0, current_price=4000.0, sl=0.0, tp=0.0,
                   pnl=0.0, opened_at="2026-10-01T00:00:00+00:00")
    d = agent.decide("XAUUSD", stoch, ChartContext(decision="REJECT"), [pos])
    assert d.action == "CLOSE", "an exit is never gated"


# ------------------------------------------------- 2. condition recording
def test_snapshot_captures_every_condition():
    f = entry_feature_snapshot(
        score=72.5, score_parts={"turn": 65.0, "trend": 7.5},
        chart_decision="REJECT", chart_trend="BEARISH", chart_structure="LH_LL",
        price_location="NEAR_RESISTANCE", candle_behavior="BEARISH_REJECTION",
        curve="TURNING_UP", stoch_k=71.2, stoch_d=60.1, spread_points=55.0,
        hour_utc=17, timeframe="M5")
    assert f["score"] == 72.5
    assert f["chart_trend"] == "BEARISH"
    assert f["spread_points"] == 55.0
    assert f["hour_utc"] == 17
    # must survive a JSON round trip — it goes straight into SQLite
    assert json.loads(json.dumps(f))["chart_structure"] == "LH_LL"


def test_snapshot_is_flat_and_queryable():
    f = entry_feature_snapshot(score=65.0)
    assert isinstance(f["score"], float)
    assert f["higher_tf"] == "NONE"
    assert f["v"] == 1


def test_loop_stores_the_snapshot_not_just_reason_codes():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "app/core/loop.py").read_text(encoding="utf-8")
    assert "features=entry_features or" in src, (
        "record_open must store the full snapshot")
    assert "self._entry_features(" in src, (
        "the snapshot must be built at the decision point")
    assert "entry_features=entry_features)" in src, (
        "it must be threaded through to _execute")


# ------------------------------------------------- 3. adaptive threshold
def test_threshold_is_a_live_value():
    """A constant captured at import would silently ignore every adjustment."""
    original = scoring.get_entry_threshold()
    try:
        scoring.set_entry_threshold(80)
        assert scoring.get_entry_threshold() == 80
    finally:
        scoring.set_entry_threshold(original)


def test_setter_refuses_to_lower_below_a_full_turn():
    """The floor is enforced in the setter, not left to the caller's good
    judgement — otherwise any future caller can quietly reopen the gate."""
    original = scoring.get_entry_threshold()
    try:
        assert scoring.set_entry_threshold(10) == scoring.W_TURN_STRENGTH, (
            "a gate below the turn strength would admit setups with no turn")
        assert scoring.set_entry_threshold(1000) == 100.0, (
            "a gate above the score range would stop the book entirely")
    finally:
        scoring.set_entry_threshold(original)


def test_score_setup_reads_the_live_threshold():
    original = scoring.get_entry_threshold()
    try:
        # A real StochState: passing None makes score_setup score 0, which
        # proves nothing about the gate.
        from app.core.stochastic import Curve, StochState
        stoch = StochState(curve=Curve.TURNING_UP, k=70.0, d=60.0,
                           prev_k=71.0, prev_d=59.0, reason="test")

        at_floor = scoring.score_setup("BUY", stoch, "TURNING_UP", [], None).total
        assert at_floor >= scoring.W_TURN_STRENGTH, (
            f"a perfect turn must reach the floor ({at_floor} < {scoring.W_TURN_STRENGTH})")

        # at the floor a perfect turn clears it; at the ceiling nothing does.
        # This pair is what proves the live gate is consulted at all.
        scoring.set_entry_threshold(scoring.W_TURN_STRENGTH)
        assert scoring.score_setup("BUY", stoch, "TURNING_UP", [], None).passed is True, (
            "at the floor a perfect turn must pass")
        scoring.set_entry_threshold(100)
        assert scoring.score_setup("BUY", stoch, "TURNING_UP", [], None).passed is False, (
            "a 100 gate must reject everything reachable")
    finally:
        scoring.set_entry_threshold(original)


def test_tuner_refuses_on_thin_data():
    """Three lucky trades must not be allowed to move the gate."""
    trades = [trade(90, 5.0, day=i % 5) for i in range(3)]
    rec = ThresholdTuner(FakeDB(trades), current_threshold=65).recommend()
    assert rec["recommend"] is None
    assert "need" in rec["reason"] and str(MIN_SAMPLES) in rec["reason"]


def test_tuner_moves_when_high_scores_do_better():
    """score 90 trades win, score 65 trades lose → the gate should rise."""
    trades = []
    for i in range(20):
        trades.append(trade(90, 4.0, day=i % 6, hour=8 + (i % 10)))
        trades.append(trade(65, -3.0, day=i % 6, hour=8 + (i % 10)))
    rec = ThresholdTuner(FakeDB(trades), current_threshold=65,
                         max_daily_entries=4.0).recommend()
    assert rec["recommend"] is not None, rec["reason"]
    assert rec["recommend"] > 65, "losing low-score entries should raise the bar"


def test_tuner_leaves_a_good_gate_alone():
    trades = [trade(70, 2.0, day=i % 6, hour=8 + (i % 10)) for i in range(40)]
    rec = ThresholdTuner(FakeDB(trades), current_threshold=65).recommend()
    assert rec["recommend"] is None, rec["reason"]
    assert "unchanged" in rec["reason"]


def test_round_trip_cost_matches_the_verified_instrument_spec():
    """55 points x $0.01 x 1 oz = $0.55 for a 0.01 lot on XMGlobal-MT5.

    An earlier version multiplied by 2 (charging the spread on entry AND
    exit), reporting $1.10. That doubled every cost in the tuner and made it
    over-filter. The spread is crossed once per round trip.
    """
    assert round_trip_cost() == pytest.approx(0.55), (
        "round trip on 0.01 lot must cost $0.55, not $1.10")
    assert round_trip_cost(lot=0.10) == pytest.approx(5.50), (
        "0.10 lot is 10x the exposure, so 10x the cost")
    # the break-even is always the same NUMBER OF POINTS whatever the size
    pts_needed = round_trip_cost(lot=0.01) / (0.01 * 0.01 * 100)
    pts_needed2 = round_trip_cost(lot=0.10) / (0.01 * 0.10 * 100)
    assert pts_needed == pytest.approx(pts_needed2) == pytest.approx(55)


def test_expectancy_is_net_of_spread():
    """A gate that looks good before cost can be a loser after it."""
    trades = [trade(70, 0.5, day=i % 6) for i in range(40)]   # +0.5 gross each
    ev = ThresholdTuner(FakeDB(trades), 65).evaluate(65)
    cost = round_trip_cost()
    assert ev["gross_avg"] == 0.5
    assert ev["net_avg"] == pytest.approx(0.5 - cost, abs=0.01)
    assert cost == pytest.approx(0.55), "cost must be the single-crossing figure"
    assert ev["net_avg"] < 0, "0.5 gross must be a loss once spread is charged"


def test_tuner_rejects_a_threshold_that_stops_the_book():
    """Only very high scores win, so every candidate starves the book."""
    # 40 trades spread over 4 days = 10/day, so a high gate must show a
    # measurable but LOW cadence. If entries_per_day is None the starvation
    # guard never fires and this test proves nothing.
    trades = [trade(95, 5.0, day=i % 4, hour=i % 8) for i in range(40)]
    rec = ThresholdTuner(FakeDB(trades), current_threshold=65,
                         max_daily_entries=26.0).recommend()
    assert rec["candidates"], rec["reason"]
    assert all(c["entries_per_day"] is not None for c in rec["candidates"]), (
        "every candidate must have a measurable cadence for this test to mean "
        f"anything: {rec['candidates']}")
    assert all(c["entries_per_day"] < 26.0 for c in rec["candidates"])
    assert rec["recommend"] is None, rec["reason"]
    assert "keep the book active" in rec["reason"]
    assert rec["recommend"] is None, rec["reason"]
    assert "keep the book active" in rec["reason"]


def test_candidates_are_evaluated():
    trades = [trade(65 + 5 * (i % 8), 1.0, day=i % 8) for i in range(60)]
    rec = ThresholdTuner(FakeDB(trades), 65).recommend()
    assert rec["candidates"], "candidates must be reported for inspection"
    assert all(c["threshold"] in CANDIDATE_THRESHOLDS for c in rec["candidates"])


def test_unscored_trades_are_ignored():
    trades = [{"ts_open": "2026-10-01T10:00:00+00:00", "pnl": 1.0,
               "features_json": json.dumps({"reason_codes": ["OLD"]})} for _ in range(50)]
    rec = ThresholdTuner(FakeDB(trades), 65).recommend()
    assert rec["samples"] == 0, "rows from before this feature existed must not count"


def test_malformed_features_do_not_crash():
    trades = [{"ts_open": "2026-10-01T10:00:00+00:00", "pnl": 1.0,
               "features_json": "{not json"} for _ in range(40)]
    rec = ThresholdTuner(FakeDB(trades), 65).recommend()
    assert rec["recommend"] is None


# --------------------------------------------------- learning read-outs
def test_feature_breakdown_groups_and_ranks():
    trades = [{"features": {"chart_trend": "BEARISH"}, "pnl": 2.0},
              {"features": {"chart_trend": "BEARISH"}, "pnl": 4.0},
              {"features": {"chart_trend": "BULLISH"}, "pnl": -1.0}]
    out = feature_breakdown(trades, "chart_trend")
    assert out["BEARISH"]["n"] == 2
    assert out["BEARISH"]["gross_avg"] == 3.0
    assert list(out)[0] == "BEARISH", "largest group first"


def test_tuning_is_rate_limited_and_off_switchable():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "app/core/loop.py").read_text(encoding="utf-8")
    assert "if not settings.adaptive_threshold:" in src, (
        "self-tuning must be switchable off")
    assert "< 3600" in src, "re-tuning must be rate-limited to hourly"
    assert "_maybe_retune_threshold()" in src, "it must actually be called"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:warnings"]))
"""Learning Engine: analyzes trade history + statistics and drafts proposals.

It can only PROPOSE. It can never approve, activate or change risk settings.
"""
from __future__ import annotations

import json
import statistics

from app.ai.model_router import router
from app.core.db import Database
from app.core.events import Event, bus
from app.core.journal import StatisticsEngine, TradeJournal
from app.core.strategies import StrategyRegistry, StrategyError


class LearningEngine:
    def __init__(self, db: Database, registry: StrategyRegistry, journal: TradeJournal,
                 stats: StatisticsEngine) -> None:
        self.db = db
        self.registry = registry
        self.journal = journal
        self.stats = stats

    def _rule_suggestions(self, summary: dict) -> list[dict]:
        suggestions: list[dict] = []
        if summary.get("trades", 0) < 10:
            return suggestions
        if summary.get("win_rate", 100) < 40:
            suggestions.append({"type": "RSI_FILTER",
                                "params": {"period": 14, "oversold": 40, "overbought": 60},
                                "_why": "win rate below 40% — tighten RSI entry band"})
        if summary.get("max_drawdown", 0) < -100:
            suggestions.append({"type": "ATR_STOP", "params": {"period": 14, "mult": 2.5},
                                "_why": "drawdown beyond threshold — widen stops"})
        if summary.get("avg_loss", 0) < -2 * summary.get("avg_win", 1):
            suggestions.append({"type": "ATR_STOP", "params": {"period": 14, "mult": 1.5},
                                "_why": "losses more than 2x wins — tighten stops"})
        return suggestions

    async def review_and_propose(self, created_by: str = "learning-engine") -> dict:
        summary = self.stats.summary()
        base_version = self.registry.active_version() or "v1.0"
        suggestions = self._rule_suggestions(summary)
        ai_note = ""
        ai = await router.complete(
            "strategy_generation",
            system=("You are a trading strategy reviewer. Given performance JSON, suggest at most 2 "
                    "concrete rule changes. Return a short rationale, no chain-of-thought."),
            user=json.dumps(summary, ensure_ascii=False))
        if ai:
            ai_note = ai["content"]

        if not suggestions and not ai_note:
            return {"status": "NO_CHANGE_NEEDED", "summary": summary}

        rules = self.registry.rules(base_version)
        existing_types = {r["type"] for r in rules}
        new_rules = [s for s in suggestions if s["type"] not in existing_types]
        if not new_rules:
            return {"status": "NO_CHANGE_NEEDED", "summary": summary, "ai_note": ai_note}
        new_rules = [{k: v for k, v in s.items() if not k.startswith("_")} | {"_why": s.get("_why", "")}
                     for s in new_rules]
        try:
            prop = self.registry.create_proposal(base_version, new_rules, created_by=created_by,
                                                 rationale=f"learning review: {ai_note[:200] or 'rule heuristics'}")
        except StrategyError as e:
            return {"status": "PROPOSAL_FAILED", "error": str(e), "summary": summary}
        await bus.publish(Event(action="STRATEGY_PROPOSAL_CREATED", result=prop["proposed_version"],
                                agent="learning", strategy_version=prop["proposed_version"], payload=prop))
        return {"status": "PROPOSAL_CREATED", "proposal": prop, "summary": summary, "ai_note": ai_note}


# =============================================================================
# Entry-condition recording + adaptive threshold (added 2026-10-01)
# =============================================================================
#
# The user removed the chart veto and asked the entry threshold to tune itself
# from realised results. That needs the conditions behind every entry to be
# recorded — trades.features_json previously held only `reason_codes`, which
# cannot answer "did score-80 entries beat score-65 entries?".

# Tuning policy. These are safety bounds, not strategy numbers: they stop the
# tuner from raising the bar on three lucky trades or starving the book.
MIN_SAMPLES = 30             # closed trades before the threshold may move at all
MIN_SAMPLES_PER_BUCKET = 8   # closed trades before a candidate threshold counts
MAX_DAILY_ENTRIES = 6.0      # a threshold that trades less than this is rejected
MIN_EXPECTANCY_MARGIN = 0.10  # a candidate must beat the live gate by this much
# Instrument constants — verified against XMGlobal-MT5 9 on 2026-10-01:
# contract_size 100, point 0.01, digits 2. SPREAD_POINTS sampled 12x live:
# median 55, min 54, max 56.
SPREAD_POINTS = 55.0
POINT_VALUE = 0.01            # 1 point = $0.01 of price
CONTRACT_SIZE = 100.0
LOT = 0.01
CANDIDATE_THRESHOLDS = [65, 70, 75, 80, 85, 90, 95, 100]


def round_trip_cost(points: float = SPREAD_POINTS, lot: float = LOT) -> float:
    """Dollar cost of one round trip: close one side and open the other.

    The spread is crossed ONCE per round trip, not twice. An earlier version
    multiplied by 2 and reported $1.10 for a 0.01 lot; the real figure is $0.55
    (55 points x $0.01 x 1 oz). Overstating it made every expectancy look
    twice as bad as it is, and biased the adaptive threshold toward
    over-filtering. P/L = points x point x lot x contract size.
    """
    return points * POINT_VALUE * lot * CONTRACT_SIZE


def entry_feature_snapshot(
    *,
    score: float,
    score_parts: dict | None = None,
    chart_decision: str = "",
    chart_trend: str = "",
    chart_structure: str = "",
    price_location: str = "",
    candle_behavior: str = "",
    curve: str = "",
    stoch_k: float | None = None,
    stoch_d: float | None = None,
    spread_points: float | None = None,
    higher_tf: str | None = None,
    hour_utc: int | None = None,
    timeframe: str = "",
    reason_codes: list[str] | None = None,
) -> dict:
    """The full set of conditions an entry was taken under.

    Flat and JSON-safe so it can be queried straight out of SQLite later without
    parsing nested blobs.
    """
    return {
        "score": round(float(score), 2),
        "score_parts": {k: round(float(v), 2) for k, v in (score_parts or {}).items()},
        "chart_decision": chart_decision,
        "chart_trend": chart_trend,
        "chart_structure": chart_structure,
        "price_location": price_location,
        "candle_behavior": candle_behavior,
        "curve": curve,
        "stoch_k": round(float(stoch_k), 2) if stoch_k is not None else None,
        "stoch_d": round(float(stoch_d), 2) if stoch_d is not None else None,
        "spread_points": round(float(spread_points), 2) if spread_points is not None else None,
        "higher_tf": higher_tf or "NONE",
        "hour_utc": hour_utc,
        "timeframe": timeframe,
        "reason_codes": list(reason_codes or []),
        "v": 1,
    }


class ThresholdTuner:
    """Recommend an entry threshold from closed trades.

    For each candidate, keep the trades that would still have been taken (entry
    score >= threshold) and measure expectancy AFTER the round-trip spread. Pick
    the best expectancy that still trades often enough — not simply the highest
    average, which on a handful of trades is noise.
    """

    def __init__(self, db: Database, current_threshold: float,
                 min_samples: int = MIN_SAMPLES,
                 max_daily_entries: float = MAX_DAILY_ENTRIES) -> None:
        self.db = db
        self.current = float(current_threshold)
        self.min_samples = min_samples
        self.max_daily_entries = max_daily_entries
        self._all: list[dict] | None = None

    def scored_trades(self) -> list[dict]:
        if getattr(self, "_all", None) is None:
            self._all = self._load_scored()
        return self._all

    def _load_scored(self) -> list[dict]:
        rows = self.db.query(
            "SELECT ts_open, side, pnl, features_json FROM trades "
            "WHERE pnl IS NOT NULL AND features_json IS NOT NULL AND features_json != '{}'")
        out = []
        for r in rows:
            try:
                f = json.loads(r["features_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            if f.get("score") is None:
                continue          # recorded before this feature existed
            out.append({**r, "score": float(f["score"]), "features": f})
        return out

    def evaluate(self, threshold: float) -> dict | None:
        trades = [t for t in self.scored_trades() if t["score"] >= threshold]
        if not trades:
            return None
        pnls = [float(t["pnl"] or 0.0) for t in trades]
        cost = round_trip_cost(trades[0]["features"].get("spread_points") or SPREAD_POINTS)
        net = [p - cost for p in pnls]
        return {
            "threshold": threshold,
            "samples": len(trades),
            "wins": sum(1 for p in pnls if p > 0),
            "gross_avg": round(statistics.fmean(pnls), 4),
            "net_avg": round(statistics.fmean(net), 4),
            "net_total": round(sum(net), 2),
            "spread_cost": round(cost, 4),
            "entries_per_day": self._per_day(trades),
        }

    def _per_day(self, trades: list[dict]):
        """Entries per day across the FULL recorded span.

        Measured over the surviving trades only, a high threshold appears to
        trade rarely purely because its own survivors are few — it would then
        look like it had headroom when in fact it is starving the book.
        """
        all_ts = [t["ts_open"] for t in self._all if t.get("ts_open")]
        ts = [t["ts_open"] for t in trades if t.get("ts_open")]
        if len(ts) < 2 or len(all_ts) < 2:
            return None
        span_ts = all_ts
        from datetime import datetime
        try:
            days = (datetime.fromisoformat(span_ts[-1]) -
                    datetime.fromisoformat(span_ts[0])).total_seconds() / 86400
        except (TypeError, ValueError):
            return None
        return round(len(trades) / days, 2) if days > 0 else None

    def recommend(self) -> dict:
        trades = self.scored_trades()
        result = {"current_threshold": self.current, "samples": len(trades),
                  "recommend": None, "reason": "", "candidates": []}
        if len(trades) < self.min_samples:
            result["reason"] = (
                f"only {len(trades)} closed trades carrying scores (need "
                f"{self.min_samples}) — threshold unchanged")
            return result

        rows = [ev for ev in (self.evaluate(c) for c in CANDIDATE_THRESHOLDS)
                if ev and ev["samples"] >= MIN_SAMPLES_PER_BUCKET]
        result["candidates"] = sorted(rows, key=lambda r: -r["net_avg"])
        if not rows:
            result["reason"] = "no candidate threshold has enough samples to compare"
            return result

        # `rows` is in CANDIDATE_THRESHOLDS order, so viable[0] was simply the
        # lowest threshold — which meant the "best" candidate was always the
        # current one and the tuner could never move. Sort by expectancy first.
        viable = sorted(
            (r for r in rows
             if r["entries_per_day"] is not None
             and r["entries_per_day"] >= self.max_daily_entries),
            key=lambda r: -r["net_avg"])
        if not viable:
            measurable = [r for r in rows if r["entries_per_day"] is not None]
            detail = (f"best measurable cadence is "
                      f"{max((r['entries_per_day'] for r in measurable), default=0)}/day"
                      ) if measurable else "cadence could not be measured"
            result["reason"] = (
                f"every candidate trades under {self.max_daily_entries}/day "
                f"({detail}) — threshold unchanged to keep the book active")
            return result

        best = viable[0]
        cur = next((r for r in rows if r["threshold"] == self.current), None)
        # An earlier version compared `cur >= best`, which meant a LOSING current
        # threshold (-0.60) beat a winning candidate (+2.90) and the tuner would
        # never move. The current value must lose on expectancy to be replaced,
        # and the margin has to be real rather than a rounding artefact.
        if cur and cur["net_avg"] >= best["net_avg"] - MIN_EXPECTANCY_MARGIN:
            result["reason"] = (f"current threshold {self.current:g} already has the best "
                                f"expectancy ({cur['net_avg']:+.2f}/trade) — unchanged")
            return result

        result["recommend"] = best["threshold"]
        result["reason"] = (f"threshold {best['threshold']:g} has expectancy "
                            f"{best['net_avg']:+.2f}/trade over {best['samples']} trades "
                            f"at {best['entries_per_day']}/day")
        return result


def feature_breakdown(trades: list[dict], key: str) -> dict:
    """Mean gross P&L grouped by one recorded feature.

    This is how the user finds out which conditions actually carried
    information — the ones that looked important may not have.
    """
    import statistics as _st
    groups: dict[str, list[float]] = {}
    for t in trades:
        v = t["features"].get(key)
        groups.setdefault(str(v) if v is not None else "NONE", []).append(
            float(t["pnl"] or 0.0))
    return {k: {"n": len(v), "gross_avg": round(_st.fmean(v), 3),
                "wins": sum(1 for x in v if x > 0)}
            for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))}

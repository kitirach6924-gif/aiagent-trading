"""Chart Context Analysis for Strategy V1 (spec §8–9 + knowledge pack
PRICE_ACTION_CANDLE_CONTEXT_V1 §29/§31–33).

Layer C output comes from the deterministic price_action engine (source of truth).
The LLM path (category `chart_analysis`) may only *annotate* — its JSON must match
the §32 contract, malformed → WAIT, risk tampering → REJECT + security audit,
and numerical MT5 data always takes precedence over any AI/vision claim (§31).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict

from app.ai.model_router import router
from app.config import settings
from app.core.events import Event, bus, iso_utc
from app.core.price_action import Ctx, PriceActionContext, build_context
from app.core.stochastic import Curve, StochState
from app.mt5.base import Candle

DECISIONS = ("CONFIRM", "WAIT", "REJECT")
TRENDS = ("BULLISH", "BEARISH", "SIDEWAYS", "UNCLEAR")
STRUCTURES = ("SUPPORTIVE", "NEUTRAL", "CONTRADICTORY", "UNCLEAR", "HH_HL", "LH_LL", "RANGE")
PA_CONTEXTS = ("SUPPORTIVE", "CONTRADICTORY", "NEUTRAL", "UNCLEAR")

# words that must NEVER appear as an AI instruction (spec §2/§18/§33)
_FORBIDDEN_KEYS = re.compile(
    r"^(max_risk|risk_|max_lot|lot_|sl_|tp_|trading_mode|live_trading|emergency|"
    r"bypass|disable|override|risk_gate|risk_config|max_daily|max_drawdown|"
    r"settings|config_)", re.I)


@dataclass
class ChartContext:
    decision: str = "WAIT"          # CONFIRM / WAIT / REJECT
    trend: str = "UNCLEAR"          # BULLISH / BEARISH / SIDEWAYS / UNCLEAR
    structure: str = "UNCLEAR"      # SUPPORTIVE / NEUTRAL / CONTRADICTORY / UNCLEAR / HH_HL...
    price_location: str = "UNCLEAR"
    candle_context: str = "UNCLEAR"
    signal_quality: float = 0.0
    reason_codes: list[str] = field(default_factory=list)
    summary: str = ""
    source: str = "deterministic"   # price_action / llm:<model> / invalid→WAIT
    ts: str = field(default_factory=iso_utc)
    price_action: dict = field(default_factory=dict)   # full §27 contract (auditable)

    def to_dict(self) -> dict:
        return asdict(self)


def _pa_to_chart(pa: PriceActionContext) -> ChartContext:
    """Map the deterministic PA context to the agent-facing chart decision."""
    if pa.context == Ctx.SUPPORTIVE.value:
        decision = "CONFIRM"
    elif pa.context == Ctx.CONTRADICTORY.value:
        decision = "REJECT"
    else:  # NEUTRAL / UNCLEAR → WAIT (never force a trade, spec §24/§29)
        decision = "WAIT"
    trend = pa.market_structure.get("trend", "UNCLEAR")
    return ChartContext(
        decision=decision, trend=trend,
        structure=pa.market_structure.get("state", "UNCLEAR"),
        price_location=pa.price_location.get("zone", "UNCLEAR"),
        candle_context=pa.candlestick.get("behavior", "NEUTRAL"),
        signal_quality=pa.support_score if decision == "CONFIRM" else
        round(1.0 - max(pa.contradiction_score, pa.uncertainty_score), 2),
        reason_codes=list(pa.reason_codes), summary=pa.summary,
        source="price_action", price_action=pa.to_dict())


class ChartContextAnalyzer:
    """Deterministic price-action baseline (source of truth). The LLM may only
    annotate through the same contract — never widen the action space."""

    def __init__(self, use_llm: bool | None = None) -> None:
        # LLM is opt-in via env (CHART_CONTEXT_USE_LLM); default deterministic (cost control)
        self.use_llm = settings.chart_context_use_llm if use_llm is None else use_llm

    async def analyze(self, curve: Curve, stoch: StochState, candles: list[Candle],
                      proposed_side: str, *, stale: bool = False) -> ChartContext:
        base = _pa_to_chart(build_context(candles, proposed_side, stale=stale))
        if not self.use_llm:
            return base
        llm = await self._llm_analyze(curve, stoch, candles, proposed_side, base)
        return llm or base

    # ---------- optional LLM path (annotation only, §31–§33) ----------
    SYSTEM_PROMPT = (
        "You are a price-action chart context analyst (knowledge pack "
        "PRICE_ACTION_CANDLE_CONTEXT_V1). You receive recent candles, market structure "
        "and a Stochastic curve signal. Judge structure, support/resistance, breakout/"
        "retest/rejection and candle behavior. You provide contextual reasoning ONLY. "
        "You must NOT: predict the next candle with certainty, claim guaranteed "
        "reversal or profit, invent candles/prices, use future candles, change "
        "Stochastic or Risk settings, or open trades. Respond ONLY with JSON: "
        "{context: SUPPORTIVE|CONTRADICTORY|NEUTRAL|UNCLEAR, trend: BULLISH|BEARISH|"
        "SIDEWAYS|UNCLEAR, structure: string, price_location: string, candle_behavior: "
        "string, pattern: string, breakout: NONE|BULLISH|BEARISH|FALSE_BREAKOUT, "
        "retest: NONE|BULLISH|BEARISH, support_score: 0..1, contradiction_score: 0..1, "
        "uncertainty_score: 0..1, reason_codes: [strings], summary: string}. "
        "If uncertain → context UNCLEAR."
    )

    async def _llm_analyze(self, curve: Curve, stoch: StochState, candles: list[Candle],
                           proposed_side: str, base: ChartContext) -> ChartContext | None:
        recent = [{"t": c.time[11:16], "o": c.open, "h": c.high, "l": c.low, "c": c.close}
                  for c in candles[-30:]]
        user = json.dumps({
            "proposed_side": proposed_side,
            "stochastic": {"k": stoch.k, "d": stoch.d, "curve": curve.value},
            "deterministic_context": {"trend": base.trend, "location": base.price_location,
                                      "support_score": base.signal_quality},
            "candles": recent,
        }, ensure_ascii=False)
        out = await router.complete("chart_analysis", self.SYSTEM_PROMPT, user,
                                    json_mode=True, max_tokens=500)
        if not out:
            return None
        ctx = self._validate_llm(out["content"])
        if ctx is None:
            await bus.publish(Event(action="AI_OUTPUT_INVALID", agent="chart_context",
                                    result="malformed chart context JSON → deterministic baseline kept",
                                    payload={"model": out.get("model")}))
            return None
        # §31: numerical market data wins — the LLM may downgrade to WAIT/REJECT
        # (conservative), never upgrade a REJECT into a CONFIRM.
        if base.decision == "REJECT" and ctx.decision == "CONFIRM":
            ctx.decision = "WAIT"
            ctx.summary = f"LLM/numerical conflict → conservative WAIT (numerical data wins). {ctx.summary}"
        ctx.source = f"llm:{out.get('model')}"
        return ctx

    def _validate_llm(self, content: str) -> ChartContext | None:
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        # security: any attempt to touch risk config → reject + audit (spec §18/§33)
        for key in data:
            if _FORBIDDEN_KEYS.match(str(key)):
                import asyncio
                try:
                    asyncio.get_event_loop().create_task(bus.publish(Event(
                        action="AI_SECURITY_REJECT", agent="chart_context",
                        result=f"attempted forbidden key: {key}")))
                except RuntimeError:
                    pass
                return None

        def _score(name: str, default: float) -> float | None:
            try:
                v = float(data.get(name, default))
            except (TypeError, ValueError):
                return None
            if v < 0.0 or v > 1.0:          # out-of-range scores → malformed (fail-closed)
                return None
            return v

        # ---- §32 contract ----
        if "context" in data and data.get("context") in PA_CONTEXTS:
            sup = _score("support_score", 0.0)
            con = _score("contradiction_score", 0.0)
            unc = _score("uncertainty_score", 0.0)
            if sup is None or con is None or unc is None:
                return None
            if sup + con + unc > 1.2:      # sanity: scores are complementary-ish
                return None
            codes = data.get("reason_codes")
            if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes):
                codes = []
            context = data["context"]
            decision = "CONFIRM" if context == "SUPPORTIVE" else \
                "REJECT" if context == "CONTRADICTORY" else "WAIT"
            trend = data.get("trend")
            if trend not in TRENDS:
                trend = "UNCLEAR"
            pa_like = {
                "market_structure": {"trend": trend, "state": str(data.get("structure", "UNCLEAR"))[:20]},
                "price_location": {"zone": str(data.get("price_location", "UNCLEAR"))[:40],
                                   "distance": None, "quality": sup},
                "candlestick": {"pattern": str(data.get("pattern", "NONE"))[:30],
                                "behavior": str(data.get("candle_behavior", "NEUTRAL"))[:30],
                                "quality": sup},
                "breakout": {"state": str(data.get("breakout", "NONE"))[:20], "confirmed": False},
                "retest": {"state": str(data.get("retest", "NONE"))[:20], "confirmed": False},
                "context": context,
                "support_score": sup, "contradiction_score": con, "uncertainty_score": unc,
                "reason_codes": codes[:10], "summary": str(data.get("summary", ""))[:300],
                "source": "llm",
            }
            return ChartContext(decision=decision, trend=trend,
                                structure=str(data.get("structure", "UNCLEAR"))[:20],
                                price_location=str(data.get("price_location", "UNCLEAR"))[:40],
                                candle_context=str(data.get("candle_behavior", "NEUTRAL"))[:40],
                                signal_quality=sup if decision == "CONFIRM" else 1.0 - max(con, unc),
                                reason_codes=codes[:10],
                                summary=str(data.get("summary", ""))[:300],
                                price_action=pa_like)

        # ---- legacy contract (back-compat with earlier tests/tools) ----
        decision = data.get("decision")
        if decision not in DECISIONS:
            return None
        trend = data.get("trend")
        if trend not in TRENDS:
            return None
        structure = data.get("structure")
        if structure not in STRUCTURES:
            return None
        quality = _score("signal_quality", 0.5)
        if quality is None:
            return None
        codes = data.get("reason_codes")
        if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes):
            codes = []
        return ChartContext(decision=decision, trend=trend, structure=structure,
                            price_location=str(data.get("price_location", "UNCLEAR"))[:40],
                            candle_context=str(data.get("candle_context", "UNCLEAR"))[:40],
                            signal_quality=quality, reason_codes=codes[:8],
                            summary=str(data.get("summary", ""))[:300])

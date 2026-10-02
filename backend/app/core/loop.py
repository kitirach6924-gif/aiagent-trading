"""24/7 Trading Loop (runs on the VPS, NOT in Firebase Hosting).

Flow per spec §7/§16:
  tick → deterministic strategy filter → (setup?) → chart context + autonomous
  agent (Strategy V1) → Risk Gate → MT5 execution → verify → journal →
  Telegram → statistics
LLM is only called when the cheap deterministic filter finds a potential setup.
One failure must NOT crash the autonomous agent (spec §16).
"""
from __future__ import annotations

import asyncio
import json
import logging
import traceback
from datetime import datetime

from app.ai.model_router import router
from app.config import settings
from app.core.agents import DecisionAgent
from app.core.autonomous_agent import AutonomousTradingAgent, AgentDecision
from app.core.chart_context import ChartContextAnalyzer
from app.core.events import Event, bus, iso_utc
from app.core.journal import TradeJournal
from app.core.risk import RiskGate
from app.core.price_action import higher_tf_blocks_entry
from app.core import scoring as _scoring_module  # noqa: F401
from app.core.scoring import get_entry_threshold, score_setup, set_entry_threshold
from app.core.strategies import StrategyRegistry
from app.core.stochastic import Curve, StochState

log = logging.getLogger("loop")


def close_side_name(proposed_side: str) -> str:
    """The position a flip in `proposed_side`'s direction would close.

    A BUY proposal closes an open SELL and vice versa. Kept as a function so
    the log text and the trade decision cannot drift apart.
    """
    return "SELL" if proposed_side == "BUY" else "BUY"


def scoring_threshold() -> float:
    """The live entry-score gate.

    Read through a function because the adaptive tuner moves it at runtime; a
    module constant captured at import time would ignore every adjustment.
    """
    return get_entry_threshold()


def set_scoring_threshold(value: float) -> float:
    return set_entry_threshold(value)


class TradingLoop:
    def __init__(self, db, connector, registry: StrategyRegistry, journal: TradeJournal,
                 risk_gate: RiskGate, notifier=None) -> None:
        self.db = db
        self.connector = connector
        self.registry = registry
        self.journal = journal
        self.risk_gate = risk_gate
        self.notifier = notifier
        self.strategy_agent = None
        self.decision_agent: DecisionAgent | None = None
        self.chart_analyzer = ChartContextAnalyzer()
        self.autonomous = AutonomousTradingAgent()
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_beat: str = ""
        self._cycles = 0

    def bind_agents(self, decision_agent: DecisionAgent) -> None:
        self.decision_agent = decision_agent

    # ---------- lifecycle ----------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.get_event_loop().create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    @property
    def status(self) -> dict:
        return {
            "running": self._running,
            "cycles": self._cycles,
            "last_beat": self._last_beat,
            "mode": settings.trading_mode,
            "paused": bool(self.db.kv_get("paused", False)),
            "emergency_stop": bool(self.db.kv_get("emergency_stop", False)),
            "active_strategy": self.registry.active_version() or None,
        }

    # ---------- main loop ----------
    async def _run(self) -> None:
        # initialize day-start balance for daily loss check
        try:
            acc = await self.connector.get_account_status()
            if self.risk_gate.day_start_balance() is None:
                self.risk_gate.set_day_start_balance(acc.balance)
            self.risk_gate.update_equity_high_water(acc.equity)
        except Exception:  # noqa: BLE001
            pass

        while self._running:
            try:
                await self._cycle()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                await bus.publish(Event(action="LOOP_ERROR", result=str(e)[:200],
                                        payload={"trace": traceback.format_exc()[-800:]}))
            await asyncio.sleep(settings.loop_interval_seconds)

    def _is_v1(self, version: str) -> bool:
        try:
            return any(r.get("type") == "STOCHASTIC_CURVE" for r in self.registry.rules(version))
        except Exception:  # noqa: BLE001
            return False

    async def _cycle(self) -> None:
        self._cycles += 1
        self._last_beat = iso_utc()
        version = self.registry.active_version() or settings.default_strategy_id

        for symbol in settings.symbols:
            try:
                if self._is_v1(version):
                    await self._v1_cycle(symbol, version)
                else:
                    await self._legacy_cycle(symbol, version)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - one symbol failing must not stop others
                await bus.publish(Event(action="LOOP_ERROR", result=f"{symbol}: {str(e)[:180]}",
                                        payload={"trace": traceback.format_exc()[-600:]}))

        # closing reconciliation: sync simulator/MT5 closes into the journal
        await self._reconcile_closes()
        # close positions left behind by an outage (only meaningful when SL is off)
        try:
            await self.recover_orphans()
        except Exception as e:  # noqa: BLE001
            log.warning("recovery watchdog error: %s", e)
        try:
            acc = await self.connector.get_account_status()
            self.risk_gate.update_equity_high_water(acc.equity)
        except Exception:  # noqa: BLE001
            pass
        # adaptive entry threshold — rate-limited to hourly inside the tuner
        await self._maybe_retune_threshold()

    # ---------- Strategy V1 autonomous path ----------
    async def _v1_cycle(self, symbol: str, version: str) -> None:
        assert self.decision_agent is not None
        timeframe = settings.strategy_timeframe
        out = await self.decision_agent.decide(symbol, version, timeframe=timeframe)
        stoch_state: StochState | None = out.market_state.get("stochastic_state")
        candles = out.market_state.get("candles") or []
        curve: Curve = out.market_state.get("curve", Curve.UNCLEAR)

        # record stochastic observation for the dashboard even on NO_TURN
        stoch_dict = stoch_state.to_dict() if stoch_state else {"curve": curve.value}

        if curve in (Curve.NO_TURN,):
            self._journal_decision(symbol, "WAIT", None, version, None,
                                   f"stochastic {curve.value}: {stoch_state.reason if stoch_state else ''}",
                                   {"stochastic": stoch_dict, "timeframe": timeframe})
            return

        if curve == Curve.UNCLEAR:
            self._journal_decision(symbol, "WAIT", None, version, None,
                                   f"stochastic UNCLEAR: {stoch_state.reason if stoch_state else 'no data'}",
                                   {"stochastic": stoch_dict, "timeframe": timeframe})
            return

        # chart context + autonomous decision (spec §8–10) — stale data → UNCLEAR context
        proposed_side = "BUY" if curve == Curve.TURNING_UP else "SELL"
        positions = await self.connector.get_positions()
        stale = self._candles_stale(candles, timeframe)
        chart = await self.chart_analyzer.analyze(curve, stoch_state, candles, proposed_side,
                                                  stale=stale)
        buy_open = any(p.side == "BUY" for p in positions if p.symbol == symbol)
        sell_open = any(p.side == "SELL" for p in positions if p.symbol == symbol)

        # Grade the setup before it can become an order. An exit (closing an open
        # position on the opposite turn) is never gated by score or spread — the
        # user explicitly accepts holding through losses in exchange for never
        # sitting unprotected on a position they asked to be flipped.
        price = await self.connector.get_price(symbol)
        equity = 0.0
        try:
            equity = (await self.connector.get_account_status()).equity
        except Exception:  # noqa: BLE001
            pass
        score = score_setup(proposed_side, stoch_state, curve, candles,
                            price.spread_points if price else None,
                            lot_equity=equity)
        score_dict = score.to_dict()

        # The opposite-side entry IS the exit: the user runs without SL, so the
        # signal to leave is "the other side now qualifies", not a stop order.
        # Both branches were previously gated on the curve alone and then
        # returned, which closed the position and only reconsidered the new
        # side 30s later — and that reconsideration could be refused by the
        # score gate, stranding the book flat after a valid flip.
        opposite_open = ((proposed_side == "SELL" and buy_open) or
                         (proposed_side == "BUY" and sell_open))

        if opposite_open:
            if not score.passed:
                # Weak turn: score is the only decider, so hold what we have.
                self._journal_decision(symbol, "WAIT", proposed_side, version, None,
                                       f"holding {close_side_name(proposed_side)}: "
                                       f"{score.summary()}",
                                       {"stochastic": stoch_dict, "timeframe": timeframe,
                                        "entry_score": score_dict})
                await bus.publish(Event(action="AGENT_DECISION",
                                        result=f"{symbol} WAIT holding "
                                               f"{close_side_name(proposed_side)} "
                                               f"({score.total}/100)",
                                        agent="autonomous", strategy_version=version,
                                        payload={"entry_score": score_dict}))
                return
            decision = self.autonomous.decide(symbol, stoch_state, chart, positions,
                                              lot=0.0, sl=None, tp=None)
            await self._act_on_decision(symbol, version, decision,
                                        extra_detail={"stochastic": stoch_dict,
                                                      "chart_context": chart.to_dict(),
                                                      "timeframe": timeframe,
                                                      "entry_score": score_dict,
                                                      "flip": True})
            # Re-read positions: the close above ran against the connector, so
            # the snapshot taken at the top of this cycle is stale and would
            # make the new entry look like a duplicate.
            positions = await self.connector.get_positions()

        if not score.passed:
            self._journal_decision(symbol, "WAIT", proposed_side, version, None,
                                   f"score gate: {score.summary()}",
                                   {"stochastic": stoch_dict, "timeframe": timeframe,
                                    "entry_score": score_dict})
            await bus.publish(Event(action="AGENT_DECISION",
                                    result=f"{symbol} WAIT ({score.total}/100)",
                                    agent="autonomous", strategy_version=version,
                                    payload={"entry_score": score_dict}))
            return

        # Higher-timeframe gate on NEW entries only (user's rule, 2026-10-01):
        # M15 must not oppose the direction. This sits BELOW the flip branch on
        # purpose — a close must never wait on M15, or the user would be left
        # holding a flipped-into position that the higher timeframe dislikes.
        higher_block = await self._higher_tf_entry_block(symbol, proposed_side,
                                                          timeframe, version)
        if higher_block:
            self._journal_decision(symbol, "WAIT", proposed_side, version, None,
                                   f"higher-timeframe gate: {higher_block}",
                                   {"stochastic": stoch_dict, "timeframe": timeframe,
                                    "entry_score": score_dict, "higher_tf": higher_block})
            await bus.publish(Event(action="AGENT_DECISION",
                                    result=f"{symbol} WAIT ({higher_block})",
                                    agent="autonomous", strategy_version=version,
                                    payload={"entry_score": score_dict,
                                             "higher_tf": higher_block}))
            return

        # Record every condition behind this entry so the adaptive threshold can
        # learn from realised results (user's request 2026-10-01). Without this
        # the tuner has nothing to compare — features_json used to hold only
        # reason_codes.
        entry_features = self._entry_features(
            symbol=symbol, proposed_side=proposed_side, score=score, chart=chart,
            curve=curve, stoch=stoch_state, price=price, timeframe=timeframe)

        lot, sl, tp = await self.decision_agent.v1_order_params(symbol, proposed_side)
        if settings.autonomous_no_sl:
            sl = None          # signal-driven flip only; a rogue position is caught
            tp = None          # by the recovery watchdog instead of a hard SL
        decision = self.autonomous.decide(symbol, stoch_state, chart, positions,
                                          lot=lot, sl=sl, tp=tp)

        await self._act_on_decision(symbol, version, decision,
                                    extra_detail={"stochastic": stoch_dict,
                                                  "chart_context": chart.to_dict(),
                                                  "timeframe": timeframe,
                                                  "entry_score": score_dict},
                                                                                      entry_features=entry_features)

    # ---------- legacy deterministic strategies (EMA cross etc.) ----------
    async def _legacy_cycle(self, symbol: str, version: str) -> None:
        assert self.decision_agent is not None
        decision = await self.decision_agent.decide(symbol, version)
        if decision.decision == "WAIT":
            self._journal_decision(symbol, "WAIT", None, version, None, decision.reason,
                                   {"conditions": decision.conditions})
            return

        # idempotency: never double-fire the same entry within the cooldown window
        if self._recently_fired(symbol, decision.decision, version):
            self._journal_decision(symbol, "WAIT", decision.decision, version, "DUP-SKIP",
                                   "duplicate entry suppressed (cooldown)", {})
            return

        await bus.publish(Event(action="TRADE_SIGNAL", result=f"{symbol} {decision.decision}",
                                agent="strategy", strategy_version=version,
                                payload={"reason": decision.reason, "confidence": decision.market_state}))

        risk = await self._risk_check(symbol, decision.decision, decision.lot,
                                      decision.sl, version)
        if risk is None:
            return
        await self._execute(symbol, version, decision.decision, decision.lot,
                            decision.sl, decision.tp, risk)

    # ---------- shared: act on an AgentDecision ----------
    async def _act_on_decision(self, symbol: str, version: str, d: AgentDecision,
                                   extra_detail: dict | None = None,
                                   entry_features: dict | None = None) -> None:
        # entry_features MUST be a parameter here: _execute() references it on
        # every order path. It was previously read as a free variable, so any
        # BUY/SELL that reached the risk gate raised NameError AFTER passing
        # every check - the order was never sent and the loop logged a generic
        # LOOP_ERROR. Pass-through (default None) keeps the CLOSE and flip
        # call sites working without a feature snapshot.
        detail = d.to_dict()
        if extra_detail:
            detail.update(extra_detail)

        if d.action == "WAIT":
            self._journal_decision(symbol, "WAIT", None, version, None,
                                   d.reasoning_summary, detail)
            await bus.publish(Event(action="AGENT_DECISION", result=f"{symbol} WAIT",
                                    agent="autonomous", strategy_version=version,
                                    payload={"reason_codes": d.reason_codes}))
            return

        if d.action == "CLOSE":
            await self._handle_close(symbol, version, d, detail)
            return

        # BUY/SELL → idempotency + risk gate (deterministic, fail-closed)
        if self._recently_fired(symbol, d.action, version):
            self._journal_decision(symbol, "WAIT", d.action, version, "DUP-SKIP",
                                   "duplicate entry suppressed (cooldown)", detail)
            return

        await bus.publish(Event(action="TRADE_SIGNAL", result=f"{symbol} {d.action}",
                                agent="autonomous", strategy_version=version,
                                payload={"reason_codes": d.reason_codes,
                                         "confidence": d.confidence,
                                         "curve": d.curve, "chart": d.chart_decision}))

        risk = await self._risk_check(symbol, d.action, d.lot, d.sl, version)
        if risk is None:
            return
        self._journal_decision(symbol, d.action, d.action, version,
                               "PASS" if risk.allowed else "BLOCK",
                               d.reasoning_summary, detail)
        if not risk.allowed:
            await bus.publish(Event(action="RISK_BLOCK", result=risk.reason, agent="risk",
                                    strategy_version=version,
                                    payload={"symbol": symbol, "failed": risk.checks_failed}))
            return
        await self._execute(symbol, version, d.action, d.lot, d.sl, d.tp, risk,
                            reason_codes=d.reason_codes, entry_features=entry_features,
                            detail=detail)

    def _entry_features(self, *, symbol: str, proposed_side: str, score, chart,
                        curve, stoch, price, timeframe: str) -> dict:
        """Snapshot every condition behind an entry, for the adaptive tuner.

        The user removed the chart veto on 2026-10-01 and asked the system to
        tune itself from realised results. Chart context is recorded but no
        longer decides anything — this snapshot is how we later find out whether
        it had any predictive value at all.
        """
        from datetime import datetime, timezone
        from app.core.learning import entry_feature_snapshot
        parts = dict(getattr(score, "parts", {}) or {})
        return entry_feature_snapshot(
            score=getattr(score, "total", 0.0),
            score_parts=parts,
            chart_decision=getattr(chart, "decision", ""),
            chart_trend=getattr(chart, "trend", ""),
            chart_structure=getattr(chart, "structure", ""),
            price_location=getattr(chart, "price_location", ""),
            candle_behavior=getattr(chart, "candle_context", ""),
            curve=getattr(curve, "value", str(curve)),
            stoch_k=getattr(stoch, "k", None),
            stoch_d=getattr(stoch, "d", None),
            spread_points=getattr(price, "spread_points", None),
            hour_utc=datetime.now(timezone.utc).hour,
            timeframe=timeframe,
            reason_codes=[],
        )

    async def _maybe_retune_threshold(self) -> None:
        """Re-tune the entry threshold from closed trades, at most hourly.

        Self-tuning was the user's explicit request. It is bounded on purpose:
        the tuner refuses to move on thin data and refuses any threshold that
        would stop the book trading, so this cannot quietly disable the system.
        """
        if not settings.adaptive_threshold:
            return
        try:
            from datetime import datetime, timezone
            last = self.db.kv_get("adaptive_threshold_last_run")
            now = datetime.now(timezone.utc)
            if last:
                if (now - datetime.fromisoformat(last)).total_seconds() < 3600:
                    return
            from app.core.learning import ThresholdTuner
            tuner = ThresholdTuner(self.db, scoring_threshold())
            rec = tuner.recommend()
            self.db.kv_set("adaptive_threshold_last_run", now.isoformat())
            self.db.kv_set("adaptive_threshold_last_recommendation", json.dumps(rec))
            if rec.get("recommend") is None:
                log.info("threshold tuning: unchanged — %s", rec.get("reason"))
                return
            new_t = float(rec["recommend"])
            if abs(new_t - scoring_threshold()) < 1e-9:
                return
            set_scoring_threshold(new_t)
            self.db.kv_set("adaptive_threshold_current", str(new_t))
            log.info("threshold tuned %.0f -> %.0f (%s)", rec["current_threshold"], new_t,
                     rec.get("reason"))
            await bus.publish(Event(action="THRESHOLD_TUNED",
                                    result=f"score gate {new_t:g} (was {rec['current_threshold']:g})",
                                    agent="learning", strategy_version=self.registry.active_version() or "",
                                    payload=rec))
        except Exception as e:  # noqa: BLE001
            log.warning("threshold tuning failed: %s", e)

    async def _higher_tf_entry_block(self, symbol: str, proposed_side: str,
                                     timeframe: str, version: str) -> str | None:
        """Check the higher timeframe against a proposed ENTRY.

        Configured by the user on 2026-10-01: M15 gates entries only, M5 keeps
        handling exits. Fails open — if the higher timeframe cannot be read the
        entry proceeds, because the alternative is the system never trading
        again after a transient MT5 error.
        """
        higher = settings.higher_tf_gate_timeframe
        if not higher:
            return None
        try:
            candles = await self.connector.get_candles(symbol, higher, 60)
        except Exception as e:  # noqa: BLE001
            log.warning("higher-tf candles unavailable (%s): %s", higher, e)
            return None
        if not candles:
            return None
        try:
            return higher_tf_blocks_entry(candles, proposed_side)
        except Exception as e:  # noqa: BLE001
            log.warning("higher-tf gate error (%s): %s", higher, e)
            return None

    # ---------- CLOSE BUY on TURNING_DOWN (spec §12: verify before anything else) ----------
    async def _handle_close(self, symbol: str, version: str, d: AgentDecision,
                            detail: dict) -> None:
        # Which side to close follows the flip: a BUY proposal closes the SELL
        # and a SELL proposal closes the BUY. Deriving it from the curve alone
        # broke once score became the decider, because a CLOSE for the other
        # side would filter to nothing and silently do nothing.
        close_side = close_side_name("BUY" if d.curve == Curve.TURNING_DOWN else "SELL")
        self._journal_decision(symbol, "CLOSE", close_side, version, None,
                               d.reasoning_summary, detail)
        positions = await self.connector.get_positions()
        own = [p for p in positions if p.symbol == symbol and p.side == close_side]
        if not own:
            await bus.publish(Event(action="AGENT_DECISION",
                                    result=f"{symbol} CLOSE skipped (no {close_side})",
                                    agent="autonomous", strategy_version=version))
            return
        for p in own:
            result = await self.connector.close_position(p.ticket)
            if not result.ok:
                await bus.publish(Event(action="TRADE_CLOSE_FAILED",
                                        result=f"ticket {p.ticket}: {result.error}",
                                        agent="executor", strategy_version=version))
                continue
            # verify execution from the broker, never assume
            verified = await self._verify_closed(p.ticket)
            self.journal.record_close(ticket=p.ticket, exit_price=result.price,
                                      pnl=result.pnl if hasattr(result, "pnl") else 0.0,
                                      exit_reason="AGENT_CLOSE", mode=settings.trading_mode)
            await bus.publish(Event(action="TRADE_CLOSE",
                                    result=f"ticket {p.ticket} {close_side} verified={verified}",
                                    agent="executor", strategy_version=version,
                                    payload={"ticket": p.ticket, "side": close_side,
                                             "verified": verified,
                                             "reason": f"STOCHASTIC_TURN_{'DOWN' if close_side == 'BUY' else 'UP'}"}))
        # NOTE: the opposite entry is evaluated on the NEXT cycle after close
        # verification, exactly per spec §12 ("close → verify → evaluate").

    _TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}

    def _candles_stale(self, candles: list, timeframe: str) -> bool:
        """Stale-data protection (spec §33/§37 of the platform spec): last candle much
        older than 2× its timeframe → treat context as stale (WAIT, never trade)."""
        if not candles:
            return True
        try:
            from datetime import datetime, timezone
            last = datetime.fromisoformat(candles[-1].time)
            age_min = (datetime.now(timezone.utc) - last).total_seconds() / 60
            return age_min > 2.5 * self._TF_MINUTES.get(timeframe.upper(), 15)
        except (ValueError, TypeError):
            return False

    async def _verify_closed(self, ticket: str) -> bool:
        try:
            positions = await self.connector.get_positions()
            return not any(str(p.ticket) == str(ticket) for p in positions)
        except Exception:  # noqa: BLE001
            return False

    # ---------- risk gate + execution (shared) ----------
    async def _risk_check(self, symbol: str, side: str, lot: float, sl: float | None,
                          version: str):
        price = await self.connector.get_price(symbol)
        account = await self.connector.get_account_status()
        positions = await self.connector.get_positions()
        return self.risk_gate.check(symbol=symbol, side=side, lot=lot, sl=sl,
                                    price=price, account=account, positions=positions,
                                    strategy_version=version)

    async def _execute(self, symbol: str, version: str, side: str, lot: float,
                       sl: float | None, tp: float | None, risk,
                       reason_codes: list[str] | None = None,
                       entry_features: dict | None = None,
                       detail: dict | None = None) -> None:
        # `detail` belongs here for the same reason as entry_features: the final
        # ORDER_SENT journal line read it as a free variable belonging to
        # _act_on_decision, so a successful order raised NameError AFTER the
        # broker had already filled it - the trade existed but was never recorded.
        detail = detail or {}
        await bus.publish(Event(action="ORDER_ATTEMPT",
                                result=f"{symbol} {side} lot={lot} sl={sl} tp={tp}",
                                agent="executor", strategy_version=version,
                                payload={"symbol": symbol, "side": side, "lot": lot,
                                         "connector": type(self.connector).__name__}))
        if sl is None and not settings.autonomous_no_sl:
            # strategy must provide SL; if absent, fail-closed (no trade)
            await bus.publish(Event(action="RISK_BLOCK", result="missing SL from strategy", agent="risk",
                                    strategy_version=version))
            return
        result = await self.connector.open_position(
            symbol=symbol, side=side, lot=lot, sl=sl, tp=tp,
            comment=f"agent:{version}")
        if not result.ok:
            await bus.publish(Event(action="TRADE_OPEN_FAILED", result=result.error, agent="executor",
                                    strategy_version=version,
                                    payload={"symbol": symbol, "side": side, "lot": lot,
                                             "sl": sl, "tp": tp,
                                             "connector": type(self.connector).__name__}))
            return
        # verify the position really exists at the broker (spec §36: no false success)
        verified = await self._verify_open(symbol, result.ticket)
        self.journal.record_open(
            symbol=symbol, side=side, lot=lot, entry_price=result.price,
            sl=sl, tp=tp, ticket=result.ticket, strategy_version=version, mode=settings.trading_mode,
            features=entry_features or {"reason_codes": reason_codes or []})
        self._mark_fired(symbol, side, version)
        await bus.publish(Event(action="TRADE_OPEN",
                                result=f"{symbol} {side} {lot} lots verified={verified}",
                                agent="executor", strategy_version=version,
                                payload={"ticket": result.ticket, "entry": result.price,
                                         "sl": sl, "tp": tp, "verified": verified,
                                         "reason_codes": reason_codes or []}))
        # Mirror to the DB so an order attempt is traceable even if the event
        # bus has no active subscriber. 16 decisions had cleared the risk gate
        # with no order and no event, which left the failure invisible.
        self._journal_decision(symbol, side, side, version, "ORDER_SENT",
                               f"order accepted ticket={result.ticket} price={result.price} "
                               f"verified={verified}", detail)

    async def _verify_open(self, symbol: str, ticket: str) -> bool:
        try:
            positions = await self.connector.get_positions()
            return any(str(p.ticket) == str(ticket) for p in positions)
        except Exception:  # noqa: BLE001
            return False

    # ---------- recovery watchdog: close positions orphaned by an outage ----------
    async def recover_orphans(self) -> int:
        """Close positions the agent can no longer manage.

        With no hard stop loss (AUTONOMOUS_NO_SL), a position that survives a bridge
        or VPS outage would otherwise sit unattended until someone noticed. If the
        loop has not evaluated a symbol recently, the position has no exit path, so
        close it and say so.
        """
        if not settings.autonomous_no_sl:
            return 0
        try:
            positions = await self.connector.get_positions()
        except Exception as e:  # noqa: BLE001
            log.warning("recovery watchdog could not read positions: %s", e)
            return 0
        if not positions:
            return 0

        now = datetime.now()
        closed = 0
        for p in positions:
            row = self.db.query_one(
                "SELECT id FROM trades WHERE ticket=? AND ts_close IS NULL", (str(p.ticket),))
            if not row:
                continue    # not ours (manual trade) — never touch it
            last = self._last_decision_at(p.symbol)
            if last is not None and (now - last).total_seconds() < settings.recovery_close_after:
                continue    # the loop is alive and managing this symbol
            result = await self.connector.close_position(p.ticket)
            if not result.ok:
                log.error("recovery close FAILED ticket=%s: %s", p.ticket, result.error)
                continue
            verified = await self._verify_closed(p.ticket)
            self.journal.record_close(
                ticket=p.ticket, exit_price=result.price,
                pnl=getattr(result, "pnl", 0.0),
                exit_reason="RECOVERY_WATCHDOG", mode=settings.trading_mode)
            closed += 1
            await bus.publish(Event(
                action="SUPERVISOR_CLOSE_ALL",
                result=(f"ปิด position {p.symbol} {p.side} (ticket {p.ticket}) "
                        f"อัตโนมัติ — loop ไม่ได้ควบคุม position นี้ได้ "
                        f"(verified={verified}, pnl={getattr(result, 'pnl', 0.0):.2f})"),
                agent="recovery", payload={"ticket": p.ticket, "verified": verified}))
        return closed

    def _last_decision_at(self, symbol: str):
        row = self.db.query_one(
            "SELECT ts FROM decisions WHERE symbol=? ORDER BY id DESC LIMIT 1", (symbol,))
        if not row:
            return None
        try:
            return datetime.fromisoformat(row["ts"]).replace(tzinfo=None)
        except (ValueError, TypeError, KeyError):
            return None

    def _journal_decision(self, symbol: str, decision: str, signal: str | None, version: str,
                          risk_result: str | None, reason: str, detail: dict | None) -> None:
        self.db.execute(
            "INSERT INTO decisions (ts, symbol, decision, signal, strategy_version, risk_result, reason, detail_json) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (iso_utc(), symbol, decision, signal, version, risk_result, reason,
             json.dumps(detail or {}, ensure_ascii=False)))

    async def _reconcile_closes(self) -> None:
        history = await self.connector.get_history(days=2)
        for h in history:
            ticket = str(h.get("ticket"))
            row = self.db.query_one("SELECT id FROM trades WHERE ticket=? AND ts_close IS NULL", (ticket,))
            if row:
                self.journal.record_close(ticket=ticket, exit_price=float(h.get("exit_price", 0)),
                                          pnl=float(h.get("pnl", 0)), exit_reason=str(h.get("exit_reason", "")),
                                          mode=settings.trading_mode)
                await bus.publish(Event(action="TRADE_CLOSE", result=f"ticket {ticket} pnl {h.get('pnl')}",
                                        agent="executor", payload={"ticket": ticket, "pnl": h.get("pnl")}))

    # ---------- idempotency / duplicate-order protection ----------
    ENTRY_COOLDOWN_SECONDS = 900  # same symbol+side+version max 1 entry per 15 min

    def _fired_key(self, symbol: str, side: str, version: str) -> str:
        return f"fired:{version}:{symbol}:{side}"

    def _recently_fired(self, symbol: str, side: str, version: str) -> bool:
        from app.core.events import utcnow
        last = self.db.kv_get(self._fired_key(symbol, side, version))
        if not last:
            return False
        try:
            elapsed = (utcnow() - datetime.fromisoformat(last)).total_seconds()
        except (ValueError, TypeError):
            return False
        return elapsed < self.ENTRY_COOLDOWN_SECONDS

    def _mark_fired(self, symbol: str, side: str, version: str) -> None:
        self.db.kv_set(self._fired_key(symbol, side, version), iso_utc())

"""Monitor snapshot for the Titan command platform.

One endpoint, one call: everything the dashboard renders. Pure read — it never
places, modifies, or closes anything. Trading decisions stay in the trading loop.

Design notes
------------
- `day_change_*` compares live price against the broker's own day reference
  (symbol_info.session_* when available), falling back to the day's first candle.
  This matters because the broker clock runs hours ahead of the host clock.
- `entry_signals` / `exit_signals` describe the LIVE strategy the agent is
  actually running, so the panel can never drift from reality.
- Data source is reported explicitly (REAL_MT5 vs SIMULATOR) — a simulator must
  never be presented as a live market.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.config import settings
from app.core.stochastic import Curve, calc_stochastic
from app.mt5 import providers
from app.mt5.base import Candle


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _candles_from_snapshot(snap_candles: list[dict]) -> list[Candle]:
    out = []
    for c in snap_candles or []:
        try:
            out.append(Candle(time=str(c["time"]), open=float(c["open"]), high=float(c["high"]),
                              low=float(c["low"]), close=float(c["close"]),
                              tick_volume=float(c.get("tick_volume", 0))))
        except (KeyError, TypeError, ValueError):
            continue
    return out


async def build_monitor_snapshot(connector, *, source: str, loop_status: dict,
                                  symbol: str | None = None,
                                  db=None) -> dict:
    """Assemble the full monitor view. Read-only."""
    symbol = symbol or (settings.symbols[0] if settings.symbols else "XAUUSD")
    broker_symbol = providers.symbol_for(symbol)
    tf = settings.strategy_timeframe

    # ---- account ----
    account: dict = {}
    positions: list[dict] = []
    account_error = None
    try:
        acc = await connector.get_account_status()
        account = {
            "balance": acc.balance, "equity": acc.equity, "margin_used": acc.margin_used,
            "currency": acc.currency, "leverage": acc.leverage, "server": acc.server,
            "mode": acc.mode, "free_margin": round(acc.equity - acc.margin_used, 2),
        }
        for p in await connector.get_positions():
            positions.append({
                "ticket": str(p.ticket), "symbol": p.symbol, "side": p.side, "lot": p.lot,
                "entry_price": p.entry_price, "current_price": p.current_price,
                "pnl": p.pnl, "pnl_percent": round((p.pnl / account["equity"]) * 100, 3)
                if account.get("equity") else 0.0,
                "sl": p.sl, "tp": p.tp, "opened_at": p.opened_at,
                "strategy_version": p.strategy_version,
                "has_stop": bool(p.sl), "has_target": bool(p.tp),
            })
    except Exception as e:  # noqa: BLE001
        account_error = f"{type(e).__name__}: {e}"

    # ---- price + session direction ----
    price: dict = {}
    day: dict = {}
    try:
        canonical = _to_canonical(broker_symbol)
        px = await connector.get_price(canonical)
        bid, ask = _f(px.bid), _f(px.ask)
        price = {"bid": bid, "ask": ask, "mid": round((bid + ask) / 2, 5) if bid and ask else None,
                 "spread_points": _f(px.spread_points), "time": px.time}
        day = _day_change(broker_symbol, bid)
        if day.get("direction") is None:
            day = await _day_change_fallback(connector, canonical, tf, bid)
    except Exception:  # noqa: BLE001
        pass

    # ---- indicators actually driving entry/exit ----
    indicators: dict = {"error": None}
    try:
        indicators = await _indicators(connector, broker_symbol, tf, db=db)
    except Exception as e:  # noqa: BLE001
        indicators = {"error": f"{type(e).__name__}: {e}"}

    # ---- signals the live strategy would act on ----
    signals = _signals(indicators, positions)
    signals["entry_score"] = await _entry_score(connector, broker_symbol, canonical, tf, signals)

    # ---- stats ----
    stats = {"today": {}, "all_time": {}}
    if db is not None:
        try:
            stats = _stats(db)
        except Exception:  # noqa: BLE001
            pass
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "broker_clock_note": "MT5 timestamps use broker server time (runs ahead of host UTC)",
        "data_source": source,
        "real_mt5_connected": source == "REAL_MT5",
        "simulator_connected": source == "SIMULATOR",
        "account": account,
        "account_error": account_error,
        "positions": positions,
        "position_count": len(positions),
        "exposure": round(sum(p["lot"] for p in positions), 4),
        "symbol": symbol,
        "broker_symbol": broker_symbol,
        "timeframe": tf,
        "price": price,
        "day": day,
        "indicators": indicators,
        "signals": signals,
        "strategy": {
            "id": settings.default_strategy_id,
            "version": loop_status.get("active_strategy"),
            "timeframe": tf,
            "params": {
                "stoch_k": settings.stoch_k_period, "stoch_d": settings.stoch_d_period,
                "stoch_slowing": settings.stoch_slowing,
                "curve_min_slope_change": settings.curve_min_slope_change,
                "curve_confirmation_bars": settings.curve_confirmation_bars,
                "chart_context_use_llm": settings.chart_context_use_llm,
            },
        },
        "agent": {
            "running": loop_status.get("running"), "paused": loop_status.get("paused"),
            "cycles": loop_status.get("cycles"), "last_beat": loop_status.get("last_beat"),
            "mode": loop_status.get("mode"), "emergency_stop": loop_status.get("emergency_stop"),
        },
        "trading": {
            "trading_mode": settings.trading_mode, "live_trading": settings.live_trading,
            "autonomous_mode": settings.autonomous_mode,
            "auto_strategy_deploy": settings.auto_strategy_deploy,
            "risk": {
                "max_lot": settings.max_lot, "max_risk_percent": settings.max_risk_percent,
                "max_open_positions": settings.max_open_positions,
                "max_spread_points": settings.max_spread_points,
                "stop_loss_required": settings.stop_loss_required,
                "max_daily_loss_percent": settings.max_daily_loss_percent,
            },
        },
        "stats": stats,
    }


# ---------- helpers ----------
def _trade_markers(db, candles: list[Candle]) -> list[dict]:
    """Past trades plotted on the %K line: offset back from the newest candle.

    Only closed trades are marked — an open position has no realized P&L to colour
    by, so showing it as profit or loss would be a lie. Unresolved candles are
    skipped rather than guessed at.
    """
    if db is None or not candles:
        return []
    try:
        rows = db.query(
            "SELECT ts_close, side, pnl FROM trades "
            "WHERE ts_close IS NOT NULL ORDER BY ts_close DESC LIMIT 40")
    except Exception:  # noqa: BLE001
        return []
    tf_min = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240,
              "D1": 1440}.get(settings.strategy_timeframe.upper(), 5)
    out = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(str(r["ts_close"]))
        except Exception:  # noqa: BLE001
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        newest = candles[-1].time
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=timezone.utc)
        age_min = (newest - ts).total_seconds() / 60.0
        bars = age_min / tf_min
        if not (0 <= bars < len(candles)):
            continue
        pnl = _f(r.get("pnl"), 0.0) or 0.0
        out.append({"offset": round(bars), "side": r.get("side"),
                    "pnl": round(pnl, 2), "win": pnl > 0,
                    "ts": str(r["ts_close"])})
    out.sort(key=lambda x: -x["offset"])
    return out


async def _affordability(connector, symbol: str) -> dict:
    """Can this balance open the broker's minimum lot at the current price?

    A small account (e.g. 52 USD) cannot open the 0.01 XAUUSD minimum, which
    needs ~4,190 USD of margin. Surfacing it here turns an opaque RISK_BLOCK
    ("lot 0.0 > max 0.1") into a stated reason.
    """
    out = {"ok": None}
    try:
        info = await connector.get_symbol_info(symbol)
        acc = await connector.get_account_status()
        px = await connector.get_price(symbol)
        price = px.ask if px else 0.0
        contract = info.contract_size or (
            100.0 if symbol.upper().find("XAU") >= 0 or symbol.upper().find("GOLD") >= 0
            else 100_000.0)
        min_lot = info.min_lot or info.lot_step or 0.01
        need = min_lot * price * contract
        out.update({"ok": acc.equity >= need, "min_lot": min_lot,
                    "price": price, "contract_size": contract,
                    "required_equity": round(need, 2),
                    "equity": acc.equity,
                    "note": (f"ขั้นต่ำ {min_lot} lot ต้องใช้ ~{need:,.0f} "
                             f"มี equity {acc.equity:,.2f}")})
    except Exception as e:  # noqa: BLE001
        out["note"] = f"ตรวจไม่สำเร็จ: {type(e).__name__}"
    return out


async def _indicators(connector, broker_symbol: str, tf: str, db=None) -> dict:
    canonical = _to_canonical(broker_symbol)
    candles = await connector.get_candles(canonical, tf, 200)
    stoch = calc_stochastic(candles, settings.stoch_k_period, settings.stoch_d_period,
                            settings.stoch_slowing)
    out: dict = {
        "timeframe": tf,
        "candle_count": len(candles),
        "last_candle": None,
        "stochastic": {
            "k": round(stoch["k"][-1], 2) if stoch["k"] else None,
            "d": round(stoch["d"][-1], 2) if stoch["d"] else None,
            "prev_k": round(stoch["k"][-2], 2) if len(stoch["k"]) > 1 else None,
            "prev_d": round(stoch["d"][-2], 2) if len(stoch["d"]) > 1 else None,
            "params": {"k": settings.stoch_k_period, "d": settings.stoch_d_period,
                       "slowing": settings.stoch_slowing},
            # Trade markers, aligned to the same %K axis the UI draws. `offset` counts
            # back from the newest candle so the UI needs no timestamp math.
            "trades": _trade_markers(db, candles),
            # Trimmed to keep the payload small; values are already 0-100.
            "series": {"k": [round(x, 2) for x in stoch["k"][-120:]],
                       "d": [round(x, 2) for x in stoch["d"][-120:]],
                       "total": len(candles),
                       "turns": [{"index": i, "dir": "up" if stoch["k"][i] > stoch["k"][i - 1] else "down"}
                                 for i in range(1, len(stoch["k"]))
                                 if (stoch["k"][i] - stoch["k"][i - 1]) > settings.curve_min_slope_change]},
            "zone": _stoch_zone(stoch["k"][-1]) if stoch["k"] else None,
        },
        "curve": _curve_now(candles),
        "supporting_indicators": {},
        "affordability": await _affordability(connector, broker_symbol),
    }
    if candles:
        last = candles[-1]
        out["last_candle"] = {"time": last.time, "open": last.open, "high": last.high,
                              "low": last.low, "close": last.close}
    # Contextual (NOT entry/exit drivers — the strategy does not trade on these)
    for name, period in (("EMA", 9), ("EMA", 21), ("RSI", 14), ("ATR", 14)):
        try:
            vals = await connector.get_indicator(canonical, tf, name, period)
            if vals:
                out["supporting_indicators"][f"{name}{period}"] = round(float(vals[-1]), 5)
        except Exception:  # noqa: BLE001
            continue
    return out


def _to_canonical(broker_symbol: str) -> str:
    rev = {v: k for k, v in providers.get_active_provider().symbol_map.items()}
    return rev.get(broker_symbol, broker_symbol)


def _stoch_zone(k) -> str:
    if k is None:
        return "UNKNOWN"
    if k >= 80:
        return "OVERBOUGHT"
    if k <= 20:
        return "OVERSOLD"
    return "NEUTRAL"


def _curve_now(candles: list[Candle]) -> dict:
    from app.core.stochastic import StochasticCurveDetector
    st = calc_stochastic(candles, settings.stoch_k_period, settings.stoch_d_period,
                         settings.stoch_slowing)
    if not st["k"]:
        return {"curve": "UNCLEAR", "reason": "insufficient candles", "slope_now": None,
                "slope_before": None, "threshold": settings.curve_min_slope_change}
    det = StochasticCurveDetector(settings.curve_min_slope_change,
                                 settings.curve_confirmation_bars, settings.curve_smoothing)
    state = det.detect(st["k"], st["d"])
    slope_now = round(state.k - state.prev_k, 2) if state.k is not None and state.prev_k is not None else None
    slope_before = round(state.prev_k - (state.k_series[-3] if len(state.k_series) > 2 else state.prev_k), 2) if slope_now is not None else None
    return {"curve": state.curve.value, "reason": state.reason, "slope_now": slope_now,
            "slope_before": slope_before, "threshold": settings.curve_min_slope_change}


def _day_change(broker_symbol: str, bid) -> dict:
    """Day direction from the broker's own session reference.

    `session_open/high/low` is the broker's session (usually the trading day), which
    is the right reference for "วันนี้ขึ้นหรือลง". If the broker does not expose it,
    fall back to the first candle of the current broker day so the panel is never
    blank — a silent null here reads as "no data" when the real answer is "up".
    """
    out = {"direction": None, "change": None, "change_percent": None,
           "day_open": None, "day_high": None, "day_low": None, "source": None}
    if bid is None:
        return out
    try:
        import MetaTrader5 as mt5  # noqa: PLC0415
        si = mt5.symbol_info(broker_symbol)
        o, h, l = _f(si.session_open), _f(si.session_high), _f(si.session_low)
        if o and o > 0:
            chg = bid - o
            out.update({"direction": "UP" if chg > 0 else ("DOWN" if chg < 0 else "FLAT"),
                        "change": round(chg, 5), "change_percent": round((chg / o) * 100, 3),
                        "day_open": o, "day_high": h, "day_low": l,
                        "source": "broker_session"})
            return out
    except Exception:  # noqa: BLE001
        pass
    return out


async def _day_change_fallback(connector, canonical: str, tf: str, bid) -> dict:
    """First candle of the current broker day — used when session_* is unavailable."""
    out = {"direction": None, "change": None, "change_percent": None,
           "day_open": None, "day_high": None, "day_low": None, "source": "first_candle_of_day"}
    if bid is None:
        return out
    try:
        candles = await connector.get_candles(canonical, "D1", 3)
        if not candles:
            return out
        last = candles[-1]
        o = last.open
        chg = bid - o
        out.update({"direction": "UP" if chg > 0 else ("DOWN" if chg < 0 else "FLAT"),
                    "change": round(chg, 5), "change_percent": round((chg / o) * 100, 3) if o else None,
                    "day_open": o, "day_high": last.high, "day_low": last.low})
    except Exception:  # noqa: BLE001
        return out
    return out


def _signals(ind: dict, positions: list[dict]) -> dict:
    """What the live strategy would do right now — advisory only, no execution here."""
    st = (ind or {}).get("stochastic") or {}
    curve = (ind or {}).get("curve") or {}
    k, d = st.get("k"), st.get("d")
    curve_state = curve.get("curve")
    reasons: list[str] = []
    entry: str = "WAIT"
    exit_side = None

    if k is None:
        reasons.append("no stochastic data yet")
    else:
        if curve_state == Curve.TURNING_UP.value:
            entry = "BUY"
            reasons.append(f"curve TURNING_UP (slope {curve.get('slope_before')} → {curve.get('slope_now')})")
        elif curve_state == Curve.TURNING_DOWN.value:
            entry = "SELL"
            reasons.append(f"curve TURNING_DOWN (slope {curve.get('slope_before')} → {curve.get('slope_now')})")
        else:
            reasons.append(f"curve {curve_state} — no confirmed turn")

        if d is not None and k is not None:
            if entry == "BUY" and k < d:
                reasons.append(f"%K {k} still below %D {d} — crossover not confirmed")
                entry = "WAIT"
            if entry == "SELL" and k > d:
                reasons.append(f"%K {k} still above %D {d} — crossover not confirmed")
                entry = "WAIT"

    for p in positions:
        if p["side"] == "BUY" and curve_state == Curve.TURNING_DOWN.value:
            exit_side = p["side"]
            reasons.append("existing BUY + curve turning down → exit candidate")
        if p["side"] == "SELL" and curve_state == Curve.TURNING_UP.value:
            exit_side = p["side"]
            reasons.append("existing SELL + curve turning up → exit candidate")

    missing_stops = [p["ticket"] for p in positions if not p["has_stop"]]

    # Why can this account not trade right now? The risk gate would otherwise be the
    # only place it shows up, as an opaque RISK_BLOCK.
    affordability = None
    try:
        acc = (ind or {}).get("affordability")
        if acc:
            affordability = acc
    except Exception:  # noqa: BLE001
        pass

    return {
        "entry": entry,
        "affordability": affordability,
        "entry_reasons": reasons,
        "exit_side": exit_side,
        "exit_reasons": [r for r in reasons if "exit candidate" in r],
        "k": k, "d": d, "k_zone": st.get("zone"),
        "k_prev": st.get("prev_k"), "d_prev": st.get("prev_d"),
        "curve": curve_state, "curve_reason": curve.get("reason"),
        "curve_threshold": curve.get("threshold"),
        "positions_without_stop": missing_stops,
        "note": "ADVISORY — the trading loop decides and executes; this panel never trades",
    }


async def _entry_score(connector, broker_symbol: str, canonical: str, tf: str,
                 signals: dict) -> dict:
    """Same 0–100 grade the trading loop applies, shown read-only in the panel.

    Uses the live spread and the same candles, so the number here is the number
    that gates the order — not a separate, optimistic estimate.
    """
    from app.core.scoring import (ENTRY_THRESHOLD, W_TREND, W_TURN_STRENGTH,
                                 score_setup)

    entry = signals.get("entry")
    curve_state = signals.get("curve")
    k, d = signals.get("k"), signals.get("d")
    try:
        candles = await connector.get_candles(canonical, tf, 60)
    except Exception as e:  # noqa: BLE001
        return {"error": f"candles unavailable: {type(e).__name__}", "total": None,
                "threshold": ENTRY_THRESHOLD,
                "weights": {"turn": W_TURN_STRENGTH, "trend": W_TREND}}

    spread = None
    equity = None
    try:
        px = await connector.get_price(broker_symbol)
        spread = px.spread_points
    except Exception:  # noqa: BLE001
        pass
    try:
        equity = (await connector.get_account_status()).equity
    except Exception:  # noqa: BLE001
        pass

    st = None
    if k is not None and d is not None:
        class _S:  # minimal stand-in: scoring only reads k/d/prev_k
            pass
        st = _S()
        st.k, st.d = k, d
        st.prev_k = signals.get("k_prev")
    if entry not in ("BUY", "SELL"):
        # No %K turn yet: there is nothing to score, but the live spread is still
        # worth showing. There is no spread cap any more, so it is never the
        # reason — report it as cost information only.
        cost = pct = None
        if spread and spread > 0:
            contract = 100.0
            try:
                si = await connector.get_symbol_info(broker_symbol)
                if si and si.contract_size:
                    contract = float(si.contract_size)
            except Exception:  # noqa: BLE001
                pass
            cost = round((spread / 100.0) * 0.01 * contract, 4)
            pct = round(cost / equity * 100, 4) if equity else None
        return {"total": 0, "parts": {}, "blockers": ["NO_DIRECTIONAL_SIGNAL"],
                "passed": False, "threshold": ENTRY_THRESHOLD, "curve": curve_state,
                # The panel reads weights from here; omitting them makes the UI
                # fall back to stale defaults next to a real score.
                "weights": {"turn": W_TURN_STRENGTH, "trend": W_TREND},
                "spread_points": spread, "spread_cost": cost,
                "spread_cost_pct": pct, "spread_block": None,
                "note": "ยังไม่มีสัญญาณพลิก %K — ยังไม่คิดคะแนน"}

    side = "BUY" if entry == "BUY" else "SELL"
    sc = score_setup(side, st, curve_state, candles, spread, lot_equity=equity)
    out = sc.to_dict()
    out.update({"curve": curve_state, "side": side, "spread_points": spread,
                "spread_cost": sc.spread_cost,
                "spread_cost_pct": sc.spread_cost_pct,
                "max_spread_points": settings.max_spread_points})
    return out


def _stats(db) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    row = db.query_one(
        "SELECT COUNT(*) trades, COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),0) wins, "
        "COALESCE(SUM(pnl),0) pnl FROM trades WHERE substr(ts_close,1,10) = ?", (today,)) or {}
    allrow = db.query_one(
        "SELECT COUNT(*) trades, COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),0) wins, "
        "COALESCE(SUM(pnl),0) pnl, COALESCE(AVG(pnl),0) avg_pnl, "
        "COALESCE(MAX(pnl),0) best, COALESCE(MIN(pnl),0) worst FROM trades") or {}
    open_n = db.query_one(
        "SELECT COUNT(*) n FROM trades WHERE ts_close IS NULL") or {}

    def pct(wins, trades):
        return round((wins / trades) * 100, 1) if trades else 0.0

    return {
        "today": {"date": today, "trades": row.get("trades", 0),
                  "wins": row.get("wins", 0), "pnl": round(row.get("pnl", 0.0), 2),
                  "win_rate": pct(row.get("wins", 0), row.get("trades", 0))},
        "all_time": {"trades": allrow.get("trades", 0), "wins": allrow.get("wins", 0),
                     "losses": max(0, allrow.get("trades", 0) - allrow.get("wins", 0)),
                     "win_rate": pct(allrow.get("wins", 0), allrow.get("trades", 0)),
                     "pnl": round(allrow.get("pnl", 0.0), 2),
                     "avg_pnl": round(allrow.get("avg_pnl", 0.0), 3),
                     "best": round(allrow.get("best", 0.0), 2),
                     "worst": round(allrow.get("worst", 0.0), 2),
                     "open_trades": open_n.get("n", 0)},
        "by_day": db.query(
            "SELECT substr(ts_close,1,10) date, COUNT(*) trades, "
            "COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),0) wins, "
            "COALESCE(SUM(pnl),0) pnl FROM trades WHERE ts_close IS NOT NULL "
            "GROUP BY date ORDER BY date DESC LIMIT 30"),
    }

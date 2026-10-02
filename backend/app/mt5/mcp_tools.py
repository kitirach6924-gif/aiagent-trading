"""Tool implementations for the MT5 MCP bridge (all READ or gated trade actions)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

from app.mt5.mt5_runtime import ACCOUNT_MODES, TF_MAP, ensure_mt5, round_price


def market_get_price(args: dict) -> dict:
    ensure_mt5()
    symbol = str(args["symbol"])
    t = mt5.symbol_info_tick(symbol)
    if t is None:
        raise ValueError(f"no tick for {symbol}")
    info = mt5.symbol_info(symbol)
    point = info.point if info else 0.01
    spread_points = (t.ask - t.bid) / point if (t.ask and t.bid and point) else 0.0
    return {"symbol": symbol, "bid": float(t.bid), "ask": float(t.ask),
            "spread_points": round(float(spread_points), 1),
            "time": datetime.fromtimestamp(t.time, tz=timezone.utc).isoformat()}


def market_get_candles(args: dict) -> dict:
    ensure_mt5()
    symbol = str(args["symbol"])
    tf = TF_MAP.get(str(args.get("timeframe", "M15")).upper())
    if tf is None:
        raise ValueError(f"unsupported timeframe {args.get('timeframe')}")
    count = int(args.get("count", 120))
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, max(5, min(count, 1000)))
    if rates is None:
        raise RuntimeError(f"copy_rates failed for {symbol}: {mt5.last_error()}")
    candles = [
        {"time": datetime.fromtimestamp(int(r["time"]), tz=timezone.utc).isoformat(),
         "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
         "close": float(r["close"]), "tick_volume": float(r["tick_volume"])}
        for r in rates
    ]
    return {"symbol": symbol, "candles": candles}


def market_get_indicator(args: dict) -> dict:
    candles = market_get_candles({"symbol": args["symbol"], "timeframe": args.get("timeframe", "M15"),
                                  "count": max(int(args.get("period", 14)) * 4 + 50, 120)})
    closes = [c["close"] for c in candles["candles"]]
    highs = [c["high"] for c in candles["candles"]]
    lows = [c["low"] for c in candles["candles"]]
    name = str(args.get("name", "EMA")).upper()
    period = int(args.get("period", 14))
    if name in ("EMA", "EXP"):
        return {"values": ema(closes, period)}
    if name == "SMA":
        return {"values": sma(closes, period)}
    if name == "RSI":
        return {"values": rsi(closes, period)}
    if name == "ATR":
        return {"values": atr(highs, lows, closes, period)}
    raise ValueError(f"unknown indicator {name}")


def market_get_symbol_info(args: dict) -> dict:
    ensure_mt5()
    symbol = str(args["symbol"])
    si = mt5.symbol_info(symbol)
    if si is None:
        raise ValueError(f"unknown symbol {symbol}")
    return {"symbol": symbol, "digits": int(si.digits), "point": float(si.point),
            "contract_size": float(si.trade_contract_size), "min_lot": float(si.volume_min),
            "lot_step": float(si.volume_step), "description": str(si.description)}


def account_get_status(args: dict) -> dict:
    ensure_mt5()
    acc = mt5.account_info()
    if acc is None:
        raise RuntimeError("account_info returned None (not logged in?)")
    mode = ACCOUNT_MODES.get(acc.trade_mode, "UNKNOWN")
    return {"login": str(acc.login), "name": str(acc.name), "currency": str(acc.currency),
            "balance": float(acc.balance), "equity": float(acc.equity),
            "margin_used": float(acc.margin), "leverage": int(acc.leverage),
            "server": str(acc.server), "mode": mode}


def account_get_positions(args: dict) -> dict:
    ensure_mt5()
    positions = mt5.positions_get() or []
    out = []
    for p in positions:
        out.append({"ticket": int(p.ticket), "symbol": str(p.symbol), "side": "BUY" if p.type == 0 else "SELL",
                    "lot": float(p.volume), "entry_price": float(p.price_open),
                    "current_price": float(p.price_current), "pnl": float(p.profit),
                    "sl": float(p.sl), "tp": float(p.tp),
                    "opened_at": datetime.fromtimestamp(p.time, tz=timezone.utc).isoformat(),
                    "strategy_version": str(p.comment or "")})
    return {"positions": out}


def account_get_history(args: dict) -> dict:
    ensure_mt5()
    days = int(args.get("days", 7))
    from_dt = datetime.now(timezone.utc) - timedelta(days=days)
    # The upper bound must not be tight: MT5 reports deal/position times on the BROKER
    # server clock, which runs hours ahead of this host's UTC clock. A `now + 1h` bound
    # silently truncated every recent deal (the newest visible row stopped hours before
    # "now"), which made a confirmed close look like a missing history entry. A generous
    # margin over-fetches history, which is harmless; a tight bound loses real trades.
    to_dt = datetime.now(timezone.utc) + timedelta(hours=24)
    deals = mt5.history_deals_get(from_dt, to_dt) or []
    out = []
    for d in deals:
        if d.entry in (1, 2, 3):  # DEAL_EXIT / DEAL_INOUT / DEAL_OUT_BY
            # For an exit deal, `d.type` is the CLOSING side. The position that was
            # closed ran the opposite way, so report the position side — otherwise every
            # closed BUY is listed as SELL.
            pos_side = "SELL" if d.type == 0 else "BUY"
            out.append({"ticket": int(d.position_id), "symbol": str(d.symbol),
                        "side": pos_side, "lot": float(d.volume),
                        "entry_price": float(d.price), "exit_price": float(d.price),
                        "close_side": "BUY" if d.type == 0 else "SELL",
                        "pnl": float(d.profit), "exit_reason": _deal_reason(d.reason),
                        "ts_open": "", "ts_close": datetime.fromtimestamp(d.time, tz=timezone.utc).isoformat(),
                        "mode": "MT5"})
    return {"history": out}


def _deal_reason(reason: int) -> str:
    # MetaTrader5.DEAL_REASON_* — note 0..3 are the ORIGIN (client/mobile/web/expert),
    # not an exit cause. The old table mapped 3 -> "SO", so every manual close performed
    # by this agent was reported as a stop-out. Only 4..6 are exit causes.
    return {0: "CLIENT", 1: "MOBILE", 2: "WEB", 3: "EXPERT",
            4: "SL", 5: "TP", 6: "SO"}.get(int(reason), f"REASON_{reason}")


def _opt_float(value, name: str) -> float | None:
    """Optional price level: None/''/absent -> None, else a float.

    MT5's order_send accepts 0.0 for 'no stop level', so an absent SL is a
    legitimate request, not a malformed one.
    """
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"invalid {name}: {value!r}") from e
    return f or None


def trade_open(args: dict) -> dict:
    ensure_mt5()
    symbol = str(args["symbol"])
    side = str(args["side"]).upper()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"invalid side {side}")
    lot = float(args["lot"])
    # sl/tp are optional. AUTONOMOUS_NO_SL=True makes the loop send None
    # deliberately (the recovery watchdog covers the stop), and this used to
    # raise TypeError on float(None) — so every order died at the bridge with
    # no TRADE_OPEN event, which is why 16 risk-PASS decisions produced no trade.
    sl = _opt_float(args.get("sl"), "sl")
    tp = _opt_float(args.get("tp"), "tp")
    si = mt5.symbol_info(symbol)
    if si is None:
        raise ValueError(f"unknown symbol {symbol}")
    if not si.visible:
        if not mt5.symbol_select(symbol, True):
            raise ValueError(f"cannot select symbol {symbol}")
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise ValueError(f"no tick for {symbol}")
    price = tick.ask if side == "BUY" else tick.bid
    order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
    deviation = 20
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": order_type,
        "price": price,
        # MT5 wants 0.0 for "no stop level", not None.
        "sl": round_price(symbol, sl) if sl else 0.0,
        "tp": round_price(symbol, tp) if tp else 0.0,
        "deviation": deviation,
        "magic": 20260929,
        "comment": str(args.get("comment", "ai-agent"))[:31],
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    res = mt5.order_send(req)
    if res is None:
        raise RuntimeError(f"order_send returned None: {mt5.last_error()}")
    if res.retcode != mt5.TRADE_RETCODE_DONE:
        return {"ok": False, "error": f"retcode={res.retcode} ({_retcode_name(res.retcode)}) comment={res.comment}"}
    return {"ok": True, "ticket": int(res.order), "deal": int(res.deal) if res.deal else 0,
            "price": float(res.price or price), "volume": float(res.volume or lot)}


def trade_close(args: dict) -> dict:
    ensure_mt5()
    ticket = int(args["ticket"])
    positions = mt5.positions_get(ticket=ticket) or []
    if not positions:
        return {"ok": False, "error": f"position {ticket} not found"}
    p = positions[0]
    side = "SELL" if p.type == 0 else "BUY"  # opposite to close
    tick = mt5.symbol_info_tick(p.symbol)
    price = tick.bid if side == "SELL" else tick.ask
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": p.symbol,
        "volume": float(p.volume),
        "type": mt5.ORDER_TYPE_SELL if side == "SELL" else mt5.ORDER_TYPE_BUY,
        "position": ticket,
        "price": price,
        "deviation": 20,
        "magic": 20260929,
        "comment": "ai-agent-close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    res = mt5.order_send(req)
    if res is None:
        raise RuntimeError(f"order_send returned None: {mt5.last_error()}")
    if res.retcode != mt5.TRADE_RETCODE_DONE:
        return {"ok": False, "error": f"retcode={res.retcode} ({_retcode_name(res.retcode)}) comment={res.comment}"}
    return {"ok": True, "ticket": ticket, "price": float(res.price or price)}


def trade_modify(args: dict) -> dict:
    ensure_mt5()
    ticket = int(args["ticket"])
    positions = mt5.positions_get(ticket=ticket) or []
    if not positions:
        return {"ok": False, "error": f"position {ticket} not found"}
    p = positions[0]
    req = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": p.symbol,
        "position": ticket,
        "sl": round_price(p.symbol, float(args["sl"])) if args.get("sl") is not None else float(p.sl),
        "tp": round_price(p.symbol, float(args["tp"])) if args.get("tp") is not None else float(p.tp),
    }
    res = mt5.order_send(req)
    if res is None:
        raise RuntimeError(f"order_send returned None: {mt5.last_error()}")
    if res.retcode != mt5.TRADE_RETCODE_DONE:
        return {"ok": False, "error": f"retcode={res.retcode} ({_retcode_name(res.retcode)}) comment={res.comment}"}
    return {"ok": True, "ticket": ticket, "sl": req["sl"], "tp": req["tp"]}


def system_get_status(args: dict) -> dict:
    ensure_mt5()
    ti = mt5.terminal_info()
    acc = mt5.account_info()
    mode = ACCOUNT_MODES.get(acc.trade_mode, "UNKNOWN") if acc else "UNKNOWN"
    return {"connected": bool(ti.connected) if ti else False,
            "mode": mode,
            "terminal": {"name": ti.name if ti else "", "build": ti.build if ti else 0,
                         "trade_allowed": bool(ti.trade_allowed) if ti else False},
            "account_mode": mode}


def _retcode_name(code: int) -> str:
    names = {10004: "REQUOTE", 10006: "REJECT", 10013: "INVALID", 10014: "INVALID_VOLUME",
             10015: "INVALID_PRICE", 10016: "INVALID_STOPS", 10018: "MARKET_CLOSED",
             10019: "NO_MONEY", 10027: "AUTOTRADING_DISABLED", 10030: "INVALID_FILL",
             10031: "CONNECTION"}
    return names.get(int(code), f"RET_{code}")


# ---- indicator math (shared, deterministic) ----


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def sma(values: list[float], period: int) -> list[float]:
    out = []
    for i in range(len(values)):
        window = values[max(0, i - period + 1): i + 1]
        out.append(sum(window) / len(window))
    return out


def rsi(values: list[float], period: int = 14) -> list[float]:
    out = [50.0] * len(values)
    gains = losses = 0.0
    avg_g = avg_l = 0.0
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gain, loss = max(change, 0.0), max(-change, 0.0)
        if i <= period:
            gains += gain
            losses += loss
            avg_g, avg_l = gains / i, losses / i
        else:
            avg_g = (avg_g * (period - 1) + gain) / period
            avg_l = (avg_l * (period - 1) + loss) / period
        out[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    out = []
    trs: list[float] = []
    prev_close = None
    for h, l, c in zip(highs, lows, closes):
        tr = h - l
        if prev_close is not None:
            tr = max(tr, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        out.append(sum(trs[-period:]) / len(trs[-period:]))
        prev_close = c
    return out

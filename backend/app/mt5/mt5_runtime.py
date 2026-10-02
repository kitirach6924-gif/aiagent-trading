"""Shared MT5 runtime helpers: connection guard, enums, price rounding."""
from __future__ import annotations

from datetime import datetime

import MetaTrader5 as mt5

from app.config import settings

ACCOUNT_MODES = {0: "DEMO", 1: "CONTEST", 2: "REAL"}
TF_MAP = {
    "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


def ensure_mt5() -> None:
    """Attach to the running MT5 terminal; fail loudly if unavailable.

    Safety: refuses REAL accounts unless live_trading is explicitly enabled
    (which Phase 2 forbids — the default stays DEMO-only).
    """
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    acc = mt5.account_info()
    if acc is None:
        raise RuntimeError("no account logged in to the MT5 terminal")
    mode = ACCOUNT_MODES.get(acc.trade_mode, "UNKNOWN")
    if mode == "REAL" and not settings.live_trading:
        raise PermissionError("REAL account detected while LIVE_TRADING=false — refusing (fail-closed)")


def round_price(symbol: str, price: float) -> float:
    si = mt5.symbol_info(symbol)
    if si is None or not price:
        return float(price)
    digits = int(si.digits)
    return round(float(price), digits)

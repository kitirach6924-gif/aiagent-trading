"""Simulated MT5 broker for development/tests. Random-walk prices, realistic fills, SL/TP.

This lets the whole platform (loop, risk gate, journal, stats, chat, backtest) run
end-to-end without a Windows MT5 terminal present.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

from app.mt5.base import (
    AccountStatus,
    Candle,
    Position,
    Price,
    SymbolInfo,
    TradeResult,
)
from app.mt5.base import MT5Connector  # noqa: F401  (Protocol for typing)

BASE_PRICES = {"XAUUSD": 2650.0, "EURUSD": 1.0850, "GBPUSD": 1.2700, "USDJPY": 152.0}
POINT_VALUES = {"XAUUSD": 0.01, "EURUSD": 0.00001, "GBPUSD": 0.00001, "USDJPY": 0.001}


class SimulatorConnector:
    """In-memory demo broker. Fills at ask/bid, applies spread, honours SL/TP."""

    def __init__(self, symbols: list[str] | None = None, start_balance: float = 10_000.0) -> None:
        self.symbols = symbols or ["XAUUSD"]
        self.balance = start_balance
        self.equity = start_balance
        self.mode = "SIMULATOR"
        self._prices: dict[str, float] = {s: BASE_PRICES.get(s, 100.0) for s in self.symbols}
        self._positions: dict[str, Position] = {}
        self._candles: dict[str, list[Candle]] = {}
        self._history: list[dict] = []
        self._tick_counter = 0

    # ---------- market data ----------
    def tick(self) -> None:
        """Advance the simulated market one step (random walk with mild drift)."""
        self._tick_counter += 1
        for s in self.symbols:
            drift = 0.00002 * BASE_PRICES.get(s, 100.0)
            change = random.gauss(0, 0.0008 * self._prices[s]) + (drift if self._tick_counter % 7 < 3 else 0)
            self._prices[s] = max(self._prices[s] + change, 0.0001)
            self._advance_candles(s)
        # update open positions & check SL/TP
        for p in list(self._positions.values()):
            price = self._current_for(p)
            p.current_price = price
            p.pnl = self._pnl(p, price)
            hit_sl = (p.side == "BUY" and p.sl and price <= p.sl) or (p.side == "SELL" and p.sl and price >= p.sl)
            hit_tp = (p.side == "BUY" and p.tp and price >= p.tp) or (p.side == "SELL" and p.tp and price <= p.tp)
            if hit_sl or hit_tp:
                self._close(p, exit_price=p.sl if hit_sl else p.tp, reason="SL" if hit_sl else "TP")

    def _advance_candles(self, symbol: str) -> None:
        """Fold the current price into the last candle; roll a new candle every 4 ticks
        so the streaming history behaves like live M15 bars."""
        if symbol not in self._candles:
            return
        candles = self._candles[symbol]
        price = self._prices[symbol]
        last = candles[-1]
        last.close = price
        last.high = max(last.high, price)
        last.low = min(last.low, price)
        if self._tick_counter % 4 == 0:
            candles.append(Candle(time=last.time, open=price, high=price, low=price, close=price,
                                  tick_volume=random.uniform(100, 5000)))
            del candles[0]

    def _current_for(self, p: Position) -> float:
        mid = self._prices[p.symbol]
        return mid - self._half_spread(p.symbol) if p.side == "SELL" else mid + self._half_spread(p.symbol)

    def _half_spread(self, symbol: str) -> float:
        pv = POINT_VALUES.get(symbol, 0.00001)
        return (random.uniform(12, 30) * pv) / 2

    def _pnl(self, p: Position, price: float) -> float:
        diff = (price - p.entry_price) if p.side == "BUY" else (p.entry_price - price)
        pv = POINT_VALUES.get(p.symbol, 0.00001)
        return diff * (p.lot * 100_000.0) * pv / POINT_VALUES.get(p.symbol, pv) * pv

    def _pnl(self, p: Position, price: float) -> float:
        diff = (price - p.entry_price) if p.side == "BUY" else (p.entry_price - price)
        contract = 100_000.0
        if p.symbol == "XAUUSD":
            contract = 100.0
        return diff * p.lot * contract

    def _price_data(self, symbol: str) -> Price:
        mid = self._prices[symbol]
        pv = POINT_VALUES.get(symbol, 0.00001)
        spread_pts = random.uniform(12, 30)
        half = spread_pts * pv / 2
        return Price(
            symbol=symbol,
            bid=round(mid - half, 5),
            ask=round(mid + half, 5),
            spread_points=round(spread_pts, 1),
            time=datetime.now(timezone.utc).isoformat(),
        )

    async def get_price(self, symbol: str) -> Price:
        return self._price_data(symbol)

    def _seed_candles(self, symbol: str) -> None:
        if symbol in self._candles:
            return
        candles: list[Candle] = []
        price = self._prices[symbol]
        t = datetime.now(timezone.utc) - timedelta(minutes=15 * 300)
        for i in range(300):
            o = price
            c = price + random.gauss(0, 0.0009 * price)
            h = max(o, c) + abs(random.gauss(0, 0.0004 * price))
            lo = min(o, c) - abs(random.gauss(0, 0.0004 * price))
            candles.append(Candle(time=(t + timedelta(minutes=15 * i)).isoformat(), open=o, high=h, low=lo, close=c,
                                  tick_volume=random.uniform(100, 5000)))
            price = c
        self._candles[symbol] = candles

    async def get_candles(self, symbol: str, timeframe: str, count: int) -> list[Candle]:
        self._seed_candles(symbol)
        candles = self._candles[symbol]
        candles[-1] = Candle(time=candles[-1].time, open=candles[-1].open, high=max(candles[-1].high, self._prices[symbol]),
                             low=min(candles[-1].low, self._prices[symbol]), close=self._prices[symbol],
                             tick_volume=candles[-1].tick_volume)
        return candles[-count:]

    async def get_indicator(self, symbol: str, timeframe: str, name: str, period: int) -> list[float]:
        candles = await self.get_candles(symbol, timeframe, 400)
        closes = [c.close for c in candles]
        if name.upper() in ("EMA", "EXP"):
            return ema(closes, period)
        if name.upper() == "SMA":
            return sma(closes, period)
        if name.upper() == "RSI":
            return rsi(closes, period)
        if name.upper() == "ATR":
            return atr(candles, period)
        raise ValueError(f"unknown indicator {name}")

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        pv = POINT_VALUES.get(symbol, 0.00001)
        return SymbolInfo(
            symbol=symbol,
            digits=5 if pv < 0.001 else 2,
            point=pv,
            contract_size=100.0 if symbol == "XAUUSD" else 100_000.0,
            min_lot=0.01,
            lot_step=0.01,
            description=f"{symbol} (simulated)",
        )

    # ---------- account ----------
    def _recompute_equity(self) -> None:
        self.equity = self.balance + sum(self._pnl(p, self._current_for(p)) for p in self._positions.values())

    async def get_account_status(self) -> AccountStatus:
        self._recompute_equity()
        return AccountStatus(login="sim-001", name="Simulated Demo", currency="USD", balance=self.balance,
                             equity=self.equity, margin_used=0.0, leverage=100, server="SIMULATOR", mode=self.mode)

    async def get_positions(self) -> list[Position]:
        self._recompute_equity()
        return list(self._positions.values())

    async def get_history(self, days: int = 7) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return [h for h in self._history if datetime.fromisoformat(h["ts_close"]) >= cutoff]

    # ---------- trading ----------
    async def open_position(self, symbol: str, side: str, lot: float, sl: float, tp: float, comment: str = "") -> TradeResult:
        price = self._price_data(symbol)
        fill = price.ask if side == "BUY" else price.bid
        ticket = uuid.uuid4().hex[:8]
        pos = Position(ticket=ticket, symbol=symbol, side=side, lot=lot, entry_price=fill,
                       current_price=fill, pnl=0.0, sl=sl, tp=tp,
                       opened_at=datetime.now(timezone.utc).isoformat(), comment=comment)
        self._positions[ticket] = pos
        return TradeResult(ok=True, ticket=ticket, price=fill)

    async def close_position(self, ticket: str) -> TradeResult:
        p = self._positions.get(ticket)
        if not p:
            return TradeResult(ok=False, error="position not found")
        self._close(p, exit_price=self._current_for(p), reason="MANUAL")
        return TradeResult(ok=True, ticket=ticket, price=p.current_price)

    async def modify_position(self, ticket: str, sl: float, tp: float) -> TradeResult:
        p = self._positions.get(ticket)
        if not p:
            return TradeResult(ok=False, error="position not found")
        p.sl, p.tp = sl, tp
        return TradeResult(ok=True, ticket=ticket)

    def _close(self, p: Position, exit_price: float, reason: str) -> None:
        pnl = self._pnl(p, exit_price)
        self.balance += pnl
        self._positions.pop(p.ticket, None)
        self._history.append({
            "ticket": p.ticket, "symbol": p.symbol, "side": p.side, "lot": p.lot,
            "entry_price": p.entry_price, "exit_price": exit_price, "sl": p.sl, "tp": p.tp,
            "pnl": round(pnl, 2), "exit_reason": reason,
            "ts_open": p.opened_at, "ts_close": datetime.now(timezone.utc).isoformat(),
            "strategy_version": p.strategy_version, "mode": self.mode,
        })

    async def get_status(self) -> dict:
        return {"connected": True, "mode": self.mode, "positions": len(self._positions)}


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def sma(values: list[float], period: int) -> list[float]:
    out: list[float] = []
    for i in range(len(values)):
        window = values[max(0, i - period + 1): i + 1]
        out.append(sum(window) / len(window))
    return out


def rsi(values: list[float], period: int = 14) -> list[float]:
    out: list[float] = [50.0] * len(values)
    gains, losses = 0.0, 0.0
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


def atr(candles: list[Candle], period: int = 14) -> list[float]:
    out: list[float] = []
    prev_close = None
    trs: list[float] = []
    for c in candles:
        tr = c.high - c.low
        if prev_close is not None:
            tr = max(tr, abs(c.high - prev_close), abs(c.low - prev_close))
        trs.append(tr)
        window = trs[-period:]
        out.append(sum(window) / len(window))
        prev_close = c.close
    return out

"""MT5 connector interface + data types.

The trading core only talks to this interface (never to MetaTrader5 directly),
so MCP and Simulator are interchangeable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class Price:
    symbol: str
    bid: float
    ask: float
    spread_points: float
    time: str


@dataclass
class Candle:
    time: str
    open: float
    high: float
    low: float
    close: float
    tick_volume: float


@dataclass
class Position:
    ticket: str
    symbol: str
    side: str  # BUY / SELL
    lot: float
    entry_price: float
    current_price: float
    pnl: float
    sl: float
    tp: float
    opened_at: str
    strategy_version: str = ""
    comment: str = ""


@dataclass
class AccountStatus:
    login: str
    name: str
    currency: str
    balance: float
    equity: float
    margin_used: float
    leverage: int
    server: str
    mode: str  # DEMO / LIVE / SIMULATOR


@dataclass
class SymbolInfo:
    symbol: str
    digits: int
    point: float
    contract_size: float
    min_lot: float
    lot_step: float
    description: str = ""


@dataclass
class TradeResult:
    ok: bool
    ticket: str = ""
    price: float = 0.0
    error: str = ""


class MT5Connector(Protocol):
    async def get_price(self, symbol: str) -> Price: ...
    async def get_candles(self, symbol: str, timeframe: str, count: int) -> list[Candle]: ...
    async def get_indicator(self, symbol: str, timeframe: str, name: str, period: int) -> list[float]: ...
    async def get_symbol_info(self, symbol: str) -> SymbolInfo: ...
    async def get_account_status(self) -> AccountStatus: ...
    async def get_positions(self) -> list[Position]: ...
    async def get_history(self, days: int = 7) -> list[dict]: ...
    async def open_position(self, symbol: str, side: str, lot: float, sl: float, tp: float, comment: str = "") -> TradeResult: ...
    async def close_position(self, ticket: str) -> TradeResult: ...
    async def modify_position(self, ticket: str, sl: float, tp: float) -> TradeResult: ...
    async def get_status(self) -> dict: ...

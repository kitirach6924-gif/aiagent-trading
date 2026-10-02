"""Trade Journal + Statistics Engine (SQLite)."""
from __future__ import annotations

import json

from app.core.db import Database
from app.core.events import iso_utc
from app.core.strategies import StrategyRegistry


class TradeJournal:
    def __init__(self, db: Database, registry: StrategyRegistry) -> None:
        self.db = db
        self.registry = registry

    def record_open(self, symbol: str, side: str, lot: float, entry_price: float, sl: float | None,
                    tp: float | None, ticket: str, strategy_version: str, mode: str,
                    features: dict | None = None) -> int:
        cur = self.db.query_one("SELECT COALESCE(MAX(id),0)+1 AS n FROM trades")
        self.db.execute(
            "INSERT INTO trades (ts_open, symbol, side, lot, entry_price, sl, tp, ticket, strategy_version, mode, features_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (iso_utc(), symbol, side, lot, entry_price, sl, tp, ticket, strategy_version, mode,
             json.dumps(features or {}, ensure_ascii=False)),
        )
        return cur["n"] if cur else 0

    def record_close(self, ticket: str, exit_price: float, pnl: float, exit_reason: str,
                     mode: str = "DEMO") -> int | None:
        row = self.db.query_one("SELECT id, strategy_version FROM trades WHERE ticket=? AND ts_close IS NULL", (ticket,))
        if not row:
            return None
        self.db.execute(
            "UPDATE trades SET ts_close=?, exit_price=?, pnl=?, exit_reason=? WHERE id=?",
            (iso_utc(), exit_price, pnl, exit_reason, row["id"]),
        )
        if row["strategy_version"]:
            self.registry.record_performance(row["strategy_version"], pnl, won=pnl >= 0)
        return row["id"]

    def recent(self, limit: int = 50) -> list[dict]:
        return self.db.query("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))

    def open_trades(self) -> list[dict]:
        return self.db.query("SELECT * FROM trades WHERE ts_close IS NULL ORDER BY id DESC")

    def losing_trades(self, limit: int = 20) -> list[dict]:
        return self.db.query(
            "SELECT * FROM trades WHERE pnl IS NOT NULL AND pnl < 0 ORDER BY id DESC LIMIT ?", (limit,))


class StatisticsEngine:
    def __init__(self, db: Database) -> None:
        self.db = db

    def summary(self, version: str | None = None, days: int = 30) -> dict:
        where = "WHERE pnl IS NOT NULL"
        args: list = []
        if version:
            where += " AND strategy_version=?"
            args.append(version)
        rows = self.db.query(
            f"SELECT pnl, ts_close, strategy_version, symbol FROM trades {where} ORDER BY id", tuple(args))
        closed = [r for r in rows if r["pnl"] is not None]
        if not closed:
            return {"trades": 0, "note": "no closed trades yet"}
        pnls = [r["pnl"] for r in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            equity += p
            peak = max(peak, equity)
            max_dd = min(max_dd, equity - peak)
        by_version: dict[str, dict] = {}
        for r in closed:
            v = r["strategy_version"] or "unknown"
            s = by_version.setdefault(v, {"trades": 0, "wins": 0, "pnl": 0.0})
            s["trades"] += 1
            s["wins"] += 1 if r["pnl"] > 0 else 0
            s["pnl"] = round(s["pnl"] + r["pnl"], 2)
        return {
            "trades": len(pnls),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(100 * len(wins) / len(pnls), 1),
            "total_pnl": round(sum(pnls), 2),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else None,
            "max_drawdown": round(max_dd, 2),
            "expectancy": round(sum(pnls) / len(pnls), 2),
            "by_strategy": by_version,
        }

    def today(self) -> dict:
        day = iso_utc()[:10]
        rows = self.db.query(
            "SELECT COUNT(*) AS n, COALESCE(SUM(pnl),0) AS pnl, "
            "SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) AS wins FROM trades "
            "WHERE ts_close LIKE ? AND pnl IS NOT NULL", (f"{day}%",))
        r = rows[0] if rows else {"n": 0, "pnl": 0, "wins": 0}
        return {"date": day, "trades": r["n"], "wins": r["wins"] or 0, "pnl": round(r["pnl"], 2)}

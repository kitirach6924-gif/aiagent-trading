"""Audit log. Append-only. The AI agent has NO API to delete or edit audit rows."""
from __future__ import annotations

from app.core.events import Event, iso_utc
from app.core.db import Database


class AuditLog:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self,
        action: str,
        result: str = "",
        payload: dict | None = None,
        user: str = "system",
        agent: str = "core",
        strategy_version: str = "",
        model: str = "",
        ts: str | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO audit_log (ts, user, agent, strategy_version, model, action, result, payload_json) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                ts or iso_utc(),
                user,
                agent,
                strategy_version,
                model,
                action,
                result,
                __import__("json").dumps(payload or {}, ensure_ascii=False),
            ),
        )

    def record_event(self, event: Event) -> None:
        self.record(
            action=event.action,
            result=event.result,
            payload=event.payload,
            user=event.user,
            agent=event.agent,
            strategy_version=event.strategy_version,
            model=event.model,
            ts=event.ts,
        )

    def query(self, limit: int = 100, action: str | None = None) -> list[dict]:
        sql = "SELECT * FROM audit_log"
        args: list = []
        if action:
            sql += " WHERE action = ?"
            args.append(action)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return self.db.query(sql, tuple(args))

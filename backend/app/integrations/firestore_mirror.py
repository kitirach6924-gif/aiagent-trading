"""Firestore mirror service.

Persists EVENTS (not ticks) to Firestore for the dashboard's realtime layer:
  trades (open/close)  → collections/trades
  risk events          → collection risk_events
  audit log            → collection audit_logs
  strategy versions    → collection strategy_versions
  system status        → doc system_status/current (throttled)
  ai messages          → collection ai_messages

High-frequency state (prices) deliberately stays on WebSocket only — per spec §22.
No-op when Firestore is not configured (FIRESTORE_ENABLED=false or no credentials).
"""
from __future__ import annotations

import asyncio
import json
import time

from app.config import settings
from app.core.events import Event


class FirestoreMirror:
    def __init__(self) -> None:
        self._db = None
        self._enabled = False
        self._last_status_push = 0.0

    def _lazy_init(self) -> bool:
        if self._enabled:
            return True
        if not settings.firestore_enabled:
            return False
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore

            if not firebase_admin._apps:
                cred = None
                if settings.google_application_credentials:
                    cred = credentials.Certificate(settings.google_application_credentials)
                else:
                    cred = credentials.ApplicationDefault()
                firebase_admin.initialize_app(cred, {"projectId": settings.firebase_project_id or None})
            # named database (Spark tier cannot create the "(default)" DB via API)
            self._db = firestore.client(database_id=settings.firestore_database_id)
            self._enabled = True
            return True
        except Exception as e:  # noqa: BLE001 - mirror must never break trading
            print(f"[firestore-mirror] disabled: {e}")
            self._enabled = False
            return False

    async def handle_event(self, event: Event) -> None:
        if not self._lazy_init():
            return
        try:
            await asyncio.get_event_loop().run_in_executor(None, self._write_event, event)
        except Exception as e:  # noqa: BLE001
            print(f"[firestore-mirror] write failed: {e}")

    def _write_event(self, event: Event) -> None:
        db = self._db
        ts = event.ts
        payload = {"ts": ts, "action": event.action, "result": event.result,
                   "agent": event.agent, "user": event.user,
                   "strategy_version": event.strategy_version}
        if event.action == "TRADE_OPEN":
            data = {**payload, **event.payload}
            db.collection("trades").document(str(event.payload.get("ticket"))).set(data)
            db.collection("audit_logs").add(payload)
        elif event.action == "TRADE_CLOSE":
            ticket = _extract_ticket(event)
            if ticket:
                db.collection("trades").document(str(ticket)).set(
                    {"ts_close": ts, "exit_price": event.payload.get("exit_price"),
                     "pnl": event.payload.get("pnl"), "exit_reason": event.payload.get("reason"),
                     "status": "closed"}, merge=True)
            db.collection("audit_logs").add(payload)
        elif event.action.startswith("RISK"):
            db.collection("risk_events").add({**payload, "payload": _safe_json(event.payload)})
            db.collection("audit_logs").add(payload)
        elif event.action in ("STRATEGY_PROPOSAL_CREATED", "STRATEGY_APPROVED",
                              "STRATEGY_REJECTED", "STRATEGY_ACTIVATED", "BACKTEST_COMPLETED"):
            db.collection("strategy_versions").document(event.strategy_version or "unknown").set(
                {**payload, "payload": _safe_json(event.payload)}, merge=True)
            db.collection("audit_logs").add(payload)
        elif event.action in ("EMERGENCY_STOP", "TRADING_PAUSED", "TRADING_RESUMED",
                              "LOOP_ERROR", "AI_MESSAGE_RECEIVED", "AI_CALL",
                              "AI_OUTPUT_INVALID", "AI_SECURITY_REJECT", "SUPERVISOR_CLOSE_ALL"):
            if event.action == "AI_MESSAGE_RECEIVED":
                db.collection("ai_messages").add({**payload, "text": event.result})
            db.collection("audit_logs").add(payload)
        elif event.action == "AGENT_DECISION":
            # strategy_signals (spec §25): signal/decision events incl. WAIT reasons
            db.collection("strategy_signals").add(
                {**payload, "payload": _safe_json(event.payload)})
        elif event.action == "TRADE_SIGNAL":
            db.collection("strategy_signals").add(
                {**payload, "payload": _safe_json(event.payload)})
            db.collection("audit_logs").add(payload)
        else:
            db.collection("audit_logs").add(payload)

        # throttled system_status doc (max 1 write / 5s)
        now = time.monotonic()
        if now - self._last_status_push > 5.0:
            self._last_status_push = now
            db.collection("system_status").document("current").set(
                {"last_event": event.action, "last_ts": ts,
                 "trading_mode": settings.trading_mode, "live_trading": settings.live_trading},
                merge=True)

    def push_system_status(self, status: dict) -> None:
        """Called periodically by the backend to publish agent/mt5/mcp state.
        Doc id is namespaced (FIRESTORE_STATUS_DOC_ID, default 'current') so a
        local runner and a Cloud Run runner don't overwrite each other."""
        if not self._lazy_init():
            return
        try:
            self._db.collection("system_status").document(
                settings.firestore_status_doc_id or "current").set(
                {**status, "updated_at": time.time()}, merge=True)
        except Exception as e:  # noqa: BLE001
            print(f"[firestore-mirror] status push failed: {e}")


def _extract_ticket(event: Event) -> str | None:
    t = event.payload.get("ticket")
    if t:
        return str(t)
    for source in (event.result, ""):
        if "ticket" in str(source):
            import re
            m = re.search(r"ticket (\w+)", str(source))
            if m:
                return m.group(1)
    return None


def _safe_json(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return "{}"


mirror = FirestoreMirror()

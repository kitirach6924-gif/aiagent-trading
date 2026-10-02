"""Model Router: task-category → model, OpenRouter-compatible, fully env-configurable.

Business logic never hard-codes model IDs — it always asks the router.
If no API key is configured, the router returns None and callers must fall back
to deterministic paths (cost control principle).
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

from app.config import settings
from app.core.events import Event, bus, iso_utc

CATEGORIES = [
    "market_classification", "strategy_evaluation", "trade_decision", "trade_analysis",
    "statistics", "research", "backtesting_analysis", "strategy_generation", "coding", "chat",
    "chart_analysis", "trade_reasoning", "trade_journal", "market_analysis", "strategy_analysis",
]

# Cheap/fast default mapping (used only when the specific env var is unset)
CHEAP_DEFAULTS = {
    "market_classification": "openai/gpt-4o-mini",
    "statistics": "openai/gpt-4o-mini",
    "chat": "openai/gpt-4o-mini",
    "trade_journal": os.environ.get("MODEL_TRADE_JOURNAL", "openai/gpt-4o-mini"),
}
STRONG_DEFAULTS = {
    "strategy_evaluation": "anthropic/claude-sonnet-4.5",
    "trade_decision": "anthropic/claude-sonnet-4.5",
    "trade_analysis": "anthropic/claude-sonnet-4.5",
    "trade_reasoning": "anthropic/claude-sonnet-4.5",
    "market_analysis": "anthropic/claude-sonnet-4.5",
    "strategy_analysis": "anthropic/claude-sonnet-4.5",
    "chart_analysis": os.environ.get("MODEL_CHART_ANALYSIS", "anthropic/claude-sonnet-4.5"),
    "research": "anthropic/claude-sonnet-4.5",
    "backtesting_analysis": "anthropic/claude-sonnet-4.5",
    "strategy_generation": "anthropic/claude-sonnet-4.5",
    "coding": "anthropic/claude-sonnet-4.5",
}


class ModelRouter:
    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        # Routes pinned at runtime through the platform UI, stored in the DB so a
        # model switch survives a container restart (env vars alone would not).
        self._db_routes: dict[str, str] = {}
        self._db = None

    def attach_db(self, db) -> None:
        self._db = db
        try:
            self._db_routes = db.kv_get("model_routes", {}) or {}
        except Exception:  # noqa: BLE001
            self._db_routes = {}

    def model_for(self, category: str) -> str | None:
        if category not in CATEGORIES:
            raise ValueError(f"unknown task category: {category}")
        if category in self._cache:
            return self._cache[category]
        # DB-pinned (platform selection) wins over env, which wins over defaults.
        pinned = self._db_routes.get(category)
        if pinned:
            self._cache[category] = pinned
            return pinned
        explicit = settings.model_routes.get(category) or ""
        if explicit:
            self._cache[category] = explicit
            return explicit
        if not settings.llm_api_key:
            return None
        fallback = CHEAP_DEFAULTS.get(category) or STRONG_DEFAULTS.get(category)
        return fallback

    async def complete(self, category: str, system: str, user: str, *,
                       json_mode: bool = False, max_tokens: int = 800) -> dict | None:
        """Returns {'content': str, 'model': str, 'usage': {...}} or None if unavailable."""
        model = self.model_for(category)
        if not model or not settings.llm_api_key:
            return None
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            settings.llm_base_url.rstrip("/") + "/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=settings.llm_timeout_seconds) as resp:
                data = json.loads(resp.read().decode())
            content = data["choices"][0]["message"]["content"]
            out = {"content": content, "model": model,
                   "usage": data.get("usage", {}), "latency_ms": int((time.monotonic() - t0) * 1000)}
            await bus.publish(Event(action="AI_CALL", result="OK",
                                    payload={"category": category, "model": model, **out.get("usage", {})}))
            return out
        except Exception as e:  # noqa: BLE001
            await bus.publish(Event(action="AI_CALL", result="ERROR", payload={"category": category, "model": model, "error": str(e)}))
            return None


router = ModelRouter()

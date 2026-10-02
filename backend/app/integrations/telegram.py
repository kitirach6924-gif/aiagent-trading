"""Telegram notifier. Fails silently if not configured (never blocks trading)."""
from __future__ import annotations

import time
import urllib.parse
import urllib.request

from app.config import settings
from app.core.events import Event, bus


class TelegramNotifier:
    # Identical action+reason pairs inside this window are folded into one
    # message. Set RISK_DEDUP_WINDOW_SECONDS=0 to restore every-message
    # delivery. The map is bounded so a busy loop cannot grow it forever.
    DEFAULT_DEDUP_WINDOW = 300.0
    DEDUP_MAX_KEYS = 200

    def __init__(self, token: str | None = None, chat_id: str | None = None,
                 dedup_window_seconds: float | None = None) -> None:
        self.token = token or settings.telegram_bot_token
        self.chat_id = chat_id or settings.telegram_chat_id
        if dedup_window_seconds is None:
            dedup_window_seconds = float(
                getattr(settings, "telegram_dedup_window_seconds",
                        self.DEFAULT_DEDUP_WINDOW))
        self.dedup_window_seconds = float(dedup_window_seconds)
        self._dedup_max_keys = self.DEDUP_MAX_KEYS
        # key -> [last_sent_monotonic, suppressed_count]
        self._recent: dict[tuple[str, str], list] = {}
        # Set by telegram_bot.build_bot so /quiet in the chat can silence pushed
        # events. None = never muted.
        self.mute_check = None

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.configured:
            return False
        if self.mute_check is not None:
            try:
                if self.mute_check():
                    return False
            except Exception:  # noqa: BLE001 - a mute-check bug must not block alerts
                pass
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}).encode()
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception:  # noqa: BLE001
            return False

    async def on_event(self, event: Event) -> None:
        important = {"TRADE_OPEN", "TRADE_CLOSE", "TRADE_CLOSE_FAILED", "RISK_BLOCK", "EMERGENCY_STOP",
                     "TRADING_PAUSED", "TRADING_RESUMED", "STRATEGY_APPROVED", "SUPERVISOR_CLOSE_ALL",
                     "BACKTEST_COMPLETED", "LOOP_ERROR", "AI_SECURITY_REJECT",
                     # Order pipeline. bus.publish() swallows subscriber
                     # exceptions silently, so an order that never reached the
                     # broker left no trace at all; these make it visible.
                     "ORDER_ATTEMPT", "TRADE_OPEN_FAILED",
                     # Bridge health: the platform is unreachable when these fire, so
                     # silence from the agent is itself a signal the user needs.
                     "MT5_BRIDGE_DOWN", "MT5_BRIDGE_UP", "MT5_BRIDGE_PROBE_FAIL"}
        if event.action not in important:
            return

        # A standing block re-fires on every loop iteration. Without this the
        # user gets the identical RISK_BLOCK line once per cycle, which buries
        # any new information in the channel. Suppress repeats of the same
        # action+reason inside the window, but report how many were folded in so
        # a persistent block never looks resolved.
        now = time.monotonic()
        key = (event.action, str(event.result))
        suppressed = 0
        if self.dedup_window_seconds > 0:
            last = self._recent.get(key)
            if last is not None and now - last[0] < self.dedup_window_seconds:
                # Inside the window: bump the timer, count it, send nothing.
                last[0], last[1] = now, last[1] + 1
                return
            # Window expired or first sighting: report what was folded in.
            if last is not None:
                suppressed = last[1]
            self._recent[key] = [now, 1]
            if len(self._recent) > self._dedup_max_keys:
                oldest = min(self._recent, key=lambda k: self._recent[k][0])
                del self._recent[oldest]

        suffix = f"\n<i>(+{suppressed} identical, suppressed)</i>" if suppressed else ""

        emoji = {"TRADE_OPEN": "🟢", "TRADE_CLOSE": "🏁", "RISK_BLOCK": "⛔",
                 "MT5_BRIDGE_DOWN": "🔴", "MT5_BRIDGE_UP": "🟢",
                 "MT5_BRIDGE_PROBE_FAIL": "🟡"}.get(event.action, "⚠️")
        self.send(f"{emoji} <b>{event.action}</b>\n{event.result}\n<code>{event.payload}</code>{suffix}")

    def subscribe(self) -> None:
        bus.subscribe("*", self.on_event)

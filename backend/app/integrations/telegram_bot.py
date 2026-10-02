"""Telegram INBOUND bot: long-polls Telegram and routes questions to ChatAgent.

This is the other half of TelegramNotifier. The notifier pushes events OUT; this
pulls questions IN, so the owner can ask the trading agent from a phone.

Safety: the bot is READ-ONLY by default. It can read state and explain, but a
CONTROL or STRATEGY intent is refused before it reaches the handler that would
flip paused/emergency_stop or touch the strategy registry. Flipping is opt-in
via TELEGRAM_BOT_READ_ONLY=false and only then, for a chat_id on the allowlist.

Nothing here raises into the trading loop — every failure is swallowed and
retried, because a broken Telegram must never stop the agent from trading.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from app.config import settings
from app.core.events import Event, bus

TELEGRAM_API = "https://api.telegram.org"

# Telegram rejects sendMessage bodies over 4096 chars; leave room for the
# chunk marker so a split message never fails the whole send.
MAX_MESSAGE = 3800

HELP_TEXT = (
    "🤖 AI Trading Agent\n"
    "ถามผมได้เลยครับ เช่น\n"
    "• สถานะตอนนี้เป็นอย่างไร\n"
    "• วันนี้เทรดไปกี่ครั้ง\n"
    "• เปิด position ไหม\n"
    "• ทำไมยังไม่เข้า BUY\n"
    "• ราคาทองตอนนี้\n"
    "• สถิติ / performance\n\n"
    "/report — สรุปวันนี้แบบสั้น\n"
    "/quiet — หยุดส่ง event แจ้งเตือนชั่วคราว\n"
    "/notify — กลับมาส่ง event\n"
    "/status — สถานะระบบ\n"
    "⚠️ บอทนี้อ่านอย่างเดียว สั่ง pause / emergency stop ไม่ได้ (ต้องทำที่หน้าเว็บ)"
)

QUIET_KEY = "telegram_bot_quiet"


class TelegramBot:
    """Long-polls getUpdates and answers via ChatAgent. Read-only by default."""

    def __init__(self, chat_agent, notifier=None, token: str | None = None,
                 allowed_chat_ids: set[str] | None = None,
                 read_only: bool | None = None) -> None:
        self.chat = chat_agent
        self.notifier = notifier
        self.token = token or settings.telegram_bot_token
        ids = allowed_chat_ids if allowed_chat_ids is not None else _parse_ids(
            settings.telegram_bot_allowed_chat_ids)
        if not ids and settings.telegram_chat_id:
            ids = {str(settings.telegram_chat_id).strip()}
        self.allowed_chat_ids: set[str] = ids
        self.read_only = (settings.telegram_bot_read_only if read_only is None
                          else read_only)
        self.offset: int = 0
        self._task: asyncio.Task | None = None
        self._offset_key = "telegram_bot_offset"

    # ---------- config ----------
    @property
    def configured(self) -> bool:
        return bool(self.token and self.allowed_chat_ids)

    # ---------- HTTP ----------
    def _call(self, method: str, payload: dict | None = None, timeout: int = 30) -> dict:
        """Blocking Telegram API call. Runs in a thread via asyncio.to_thread."""
        url = f"{TELEGRAM_API}/bot{self.token}/{method}"
        data = urllib.parse.urlencode(payload).encode() if payload else None
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    async def _api(self, method: str, payload: dict | None = None,
                   timeout: int = 30) -> dict | None:
        try:
            return await asyncio.to_thread(self._call, method, payload, timeout)
        except Exception as e:  # noqa: BLE001 - polling must never crash the app
            print(f"[telegram_bot] {method} failed: {e}")
            return None

    async def send(self, chat_id: str, text: str) -> bool:
        """Send text to one chat, split at 4096 chars. Plain text, no HTML."""
        ok = False
        for chunk in _split(text):
            res = await self._api("sendMessage", {"chat_id": chat_id, "text": chunk})
            ok = ok or bool(res and res.get("ok"))
        return ok

    # ---------- offset persistence ----------
    def _load_offset(self) -> None:
        """Resume where we left off. Without this a restart re-processes the
        backlog and the bot greets the user with old questions twice."""
        try:
            raw = self.chat.db.kv_get(self._offset_key, 0) or 0
            self.offset = int(raw)
        except Exception:  # noqa: BLE001
            self.offset = 0

    def _save_offset(self) -> None:
        try:
            self.chat.db.kv_set(self._offset_key, self.offset)
        except Exception:  # noqa: BLE001
            pass

    # ---------- polling loop ----------
    async def run(self) -> None:
        if not self.configured:
            print("[telegram_bot] not configured (need TELEGRAM_BOT_TOKEN + allowed chat id) — off")
            return
        # Drop any stale webhook: getUpdates fails with 409 if one is registered.
        await self._api("deleteWebhook", {"drop_pending_updates": "false"})
        await self._api("getMe")
        self._load_offset()
        print(f"[telegram_bot] online · read_only={self.read_only} · "
              f"allowed={sorted(self.allowed_chat_ids)}")
        backoff = 2.0
        while True:
            try:
                res = await self._api(
                    "getUpdates",
                    {"offset": self.offset, "timeout": 25, "allowed_updates": '["message"]'},
                    timeout=40)
                if not res or not res.get("ok"):
                    backoff = min(backoff * 1.5, 60.0)
                    await asyncio.sleep(backoff)
                    continue
                backoff = 2.0
                updates = res.get("result") or []
                for upd in updates:
                    self.offset = int(upd.get("update_id", self.offset)) + 1
                    await self._handle_update(upd)
                if updates:
                    self._save_offset()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                print(f"[telegram_bot] poll error: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 60.0)

    async def _handle_update(self, upd: dict) -> None:
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            return
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id") or "")
        text = (msg.get("text") or "").strip()
        if not chat_id or not text:
            return
        if chat.get("type") != "private":
            return
        # Allowlist BEFORE any processing. Without it anyone who finds the bot
        # can read the account and, if read_only were off, stop trading.
        if chat_id not in self.allowed_chat_ids:
            await self.send(chat_id, "⛔ ไม่ได้รับอนุญาต — chat นี้ไม่อยู่ใน allowlist")
            await bus.publish(Event(action="TELEGRAM_REJECT", result="chat not allowed",
                                    user=f"tg:{chat_id}", agent="telegram_bot"))
            return
        if text.startswith("/"):
            await self._handle_command(chat_id, text)
            return
        await self._handle_question(chat_id, text)

    # ---------- commands ----------
    async def _handle_command(self, chat_id: str, text: str) -> None:
        cmd = text.split()[0].split("@")[0].lower()
        if cmd in ("/start", "/help"):
            await self.send(chat_id, _plain(HELP_TEXT))
        elif cmd == "/status":
            snap = await self._safe_context()
            await self.send(chat_id, _plain(f"🟢 บอททำงาน\n{snap or '(อ่านสถานะไม่ได้)'}\n\n{_plain(HELP_TEXT)}"))
        elif cmd == "/report":
            await self.send(chat_id, _plain(await self._report()))
        elif cmd == "/quiet":
            self.chat.db.kv_set(QUIET_KEY, True)
            await self.send(chat_id, "🔕 ปิดการแจ้งเตือน event แล้ว — ยังถามรายงานได้ปกติครับ\n"
                                     "สั่ง /notify เพื่อเปิดคืน")
        elif cmd == "/notify":
            self.chat.db.kv_set(QUIET_KEY, False)
            await self.send(chat_id, "🔔 เปิดการแจ้งเตือน event แล้วครับ")
        else:
            await self.send(chat_id, _plain(HELP_TEXT))

    # ---------- questions ----------
    async def _handle_question(self, chat_id: str, text: str) -> None:
        # Pre-flight refusal. classify() is the same deterministic router the
        # web chat uses, so this blocks exactly the intents that would mutate
        # state — and it runs before handle(), not after.
        if self.read_only:
            intent, _kind = self.chat.classify(text)
            if intent in ("CONTROL", "STRATEGY"):
                await self.send(chat_id,
                                f"🔒 คำสั่งนี้เป็นการ{'คุมการเทรด' if intent == 'CONTROL' else 'แก้กลยุทธ์'}"
                                " — บอท Telegram เป็นโหมดอ่านอย่างเดียวครับ\n"
                                "pause / resume / emergency stop / แก้กฎ ต้องทำที่หน้าเว็บหรือสั่ง Titan โดยตรง\n"
                                "ถามเรื่องสถานะ ราคา สถิติ หรือ 'ทำไมยังไม่เข้า' ได้ตามปกติ")
                await bus.publish(Event(action="TELEGRAM_REJECT",
                                        result=f"{intent} blocked (read-only)",
                                        user=f"tg:{chat_id}", agent="telegram_bot"))
                return
        try:
            out = await self.chat.handle(text, user=f"tg:{chat_id}")
            reply = out.get("reply", "(ไม่มีคำตอบ)")
        except Exception as e:  # noqa: BLE001
            reply = f"❌ ผิดพลาด: {e}"
        await self.send(chat_id, _plain(reply))

    # ---------- report ----------
    async def _report(self) -> str:
        ctx = await self._safe_context()
        try:
            today = self.chat.stats.today()
            summary = self.chat.stats.summary()
        except Exception as e:  # noqa: BLE001
            return f"📊 รายงานวันนี้: อ่านสถิติไม่สำเร็จ ({e})"
        wr = summary.get("win_rate")
        pf = summary.get("profit_factor")
        lines = [
            f"📊 รายงานวันนี้ · {today.get('date','')}",
            f"ไม้วันนี้: {today.get('trades',0)} · ชนะ {today.get('wins',0)} · "
            f"PnL {today.get('pnl',0)}",
            f"ทั้งหมด: {summary.get('trades',0)} ไม้ · WR "
            f"{f'{wr:.1f}%' if isinstance(wr,(int,float)) else '—'} · "
            f"PF {f'{pf:.2f}' if isinstance(pf,(int,float)) else '—'} · "
            f"PnL {summary.get('total_pnl',0)}",
            f"Max DD {summary.get('max_drawdown','—')} · Expectancy "
            f"{summary.get('expectancy','—')}",
        ]
        if ctx:
            lines.append(ctx)
        return "\n".join(lines)

    async def _safe_context(self) -> str:
        try:
            return await self.chat.live_context()
        except Exception:  # noqa: BLE001
            return ""

    # ---------- lifecycle ----------
    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


# ---------- helpers ----------
def _parse_ids(raw: str) -> set[str]:
    """TELEGRAM_BOT_ALLOWED_CHAT_IDS="1,2,3" or space/newline separated."""
    if not raw:
        return set()
    return {p.strip() for p in re.split(r"[,\s]+", str(raw)) if p.strip()}


def _split(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split long replies on newlines first, then hard-split the remainder."""
    text = (text or "").strip()
    if not text:
        return ["(ว่าง)"]
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    buf = ""
    for line in text.splitlines():
        if len(buf) + len(line) + 1 > limit:
            if buf:
                out.append(buf)
            buf = ""
            while len(line) > limit:
                out.append(line[:limit])
                line = line[limit:]
        buf = f"{buf}\n{line}" if buf else line
    if buf:
        out.append(buf)
    return out


def _plain(text: str) -> str:
    """ChatAgent replies are markdown-ish and can contain < > from broker
    strings. Sent without parse_mode they would render as literal tags."""
    return (text or "").replace("<", "‹").replace(">", "›")


def _quiet(db) -> bool:
    try:
        return bool(db.kv_get(QUIET_KEY, False))
    except Exception:  # noqa: BLE001
        return False


def build_bot(chat_agent, notifier=None) -> TelegramBot:
    bot = TelegramBot(chat_agent, notifier=notifier)
    if notifier is not None and bot.configured:
        # /quiet must silence pushed events too, otherwise it only looks like it
        # worked. The notifier asks the DB flag before every send.
        notifier.mute_check = lambda: _quiet(chat_agent.db)
    return bot
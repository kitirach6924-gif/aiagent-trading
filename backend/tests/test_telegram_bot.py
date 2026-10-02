"""Telegram inbound bot: allowlist, read-only refusal, splitting, commands."""
from __future__ import annotations

import asyncio

import pytest

from app.integrations.telegram_bot import TelegramBot, _parse_ids, _plain, _split


class FakeDB:
    def __init__(self) -> None:
        self.kv: dict[str, object] = {}

    def kv_get(self, key: str, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key: str, value) -> None:
        self.kv[key] = value


class FakeChat:
    """Minimal stand-in: the bot only needs db, classify, handle, live_context."""

    def __init__(self) -> None:
        self.db = FakeDB()
        self.handled: list[str] = []
        self.released = False

    @staticmethod
    def classify(text: str) -> tuple[str, str]:
        from app.ai.chat_agent import ChatAgent
        return ChatAgent.classify(text)

    async def handle(self, text: str, user: str = "user") -> dict:
        self.handled.append(text)
        self.released = True
        return {"reply": f"ตอบ: {text}", "intent": "READ"}

    async def live_context(self) -> str:
        return "ctx line"


def make_bot(chat: FakeChat, read_only: bool = True, ids: set[str] | None = None) -> TelegramBot:
    sent: list[tuple[str, str]] = []

    bot = TelegramBot(chat, token="TOKEN", allowed_chat_ids=ids or {"111"},
                      read_only=read_only)

    async def fake_send(chat_id: str, text: str) -> bool:
        sent.append((chat_id, text))
        return True

    bot.send = fake_send          # type: ignore[assignment]
    bot.sent = sent               # type: ignore[attr-defined]
    return bot


# ---------- allowlist ----------
@pytest.mark.asyncio
async def test_chat_outside_allowlist_is_rejected_and_never_answers():
    chat = FakeChat()
    bot = make_bot(chat, ids={"111"})
    await bot._handle_update({"message": {"chat": {"id": 999, "type": "private"},
                                          "text": "วันนี้ PnL เท่าไหร่"}})
    assert chat.handled == []                       # agent never touched
    assert "ไม่ได้รับอนุญาต" in bot.sent[-1][1]      # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_group_messages_are_ignored():
    chat = FakeChat()
    bot = make_bot(chat)
    await bot._handle_update({"message": {"chat": {"id": 111, "type": "group"},
                                          "text": "สถานะ?"}})
    assert chat.handled == []
    assert bot.sent == []                           # type: ignore[attr-defined]


# ---------- read-only guard ----------
@pytest.mark.asyncio
async def test_control_intent_refused_and_does_not_reach_handler():
    chat = FakeChat()
    bot = make_bot(chat, read_only=True)
    await bot._handle_update({"message": {"chat": {"id": 111, "type": "private"},
                                          "text": "emergency stop"}})
    assert chat.handled == []                       # never executed
    assert "อ่านอย่างเดียว" in bot.sent[-1][1]      # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_strategy_intent_refused():
    chat = FakeChat()
    bot = make_bot(chat, read_only=True)
    await bot._handle_update({"message": {"chat": {"id": 111, "type": "private"},
                                          "text": "เพิ่มกฎ rsi ใหม่ให้ v3.0"}})
    assert chat.handled == []


@pytest.mark.asyncio
async def test_read_question_is_answered():
    chat = FakeChat()
    bot = make_bot(chat, read_only=True)
    await bot._handle_update({"message": {"chat": {"id": 111, "type": "private"},
                                          "text": "วันนี้เทรดไปกี่ครั้ง"}})
    assert chat.handled == ["วันนี้เทรดไปกี่ครั้ง"]
    assert "ตอบ:" in bot.sent[-1][1]                 # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_read_only_off_lets_control_through():
    chat = FakeChat()
    bot = make_bot(chat, read_only=False)
    await bot._handle_update({"message": {"chat": {"id": 111, "type": "private"},
                                          "text": "emergency stop"}})
    assert chat.handled == ["emergency stop"]


# ---------- commands ----------
@pytest.mark.asyncio
async def test_start_sends_help_without_calling_agent():
    chat = FakeChat()
    bot = make_bot(chat)
    await bot._handle_update({"message": {"chat": {"id": 111, "type": "private"},
                                          "text": "/start"}})
    assert chat.handled == []
    assert "AI Trading Agent" in bot.sent[-1][1]    # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_quiet_toggles_the_flag():
    chat = FakeChat()
    bot = make_bot(chat)
    await bot._handle_update({"message": {"chat": {"id": 111, "type": "private"},
                                          "text": "/quiet"}})
    assert chat.db.kv_get("telegram_bot_quiet") is True
    await bot._handle_update({"message": {"chat": {"id": 111, "type": "private"},
                                          "text": "/notify"}})
    assert chat.db.kv_get("telegram_bot_quiet") is False


@pytest.mark.asyncio
async def test_command_with_bot_name_suffix_is_recognised():
    chat = FakeChat()
    bot = make_bot(chat)
    await bot._handle_update({"message": {"chat": {"id": 111, "type": "private"},
                                          "text": "/help@MyBot"}})
    assert "AI Trading Agent" in bot.sent[-1][1]    # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_non_text_update_is_ignored():
    chat = FakeChat()
    bot = make_bot(chat)
    await bot._handle_update({"message": {"chat": {"id": 111, "type": "private"},
                                          "photo": [{"file_id": "x"}]}})
    assert bot.sent == []                           # type: ignore[attr-defined]


# ---------- helpers ----------
def test_split_keeps_short_text_intact():
    assert _split("สั้นๆ") == ["สั้นๆ"]


def test_split_breaks_on_newlines_within_limit():
    text = "\n".join(["x" * 100 for _ in range(50)])
    chunks = _split(text, limit=300)
    assert len(chunks) > 1
    assert all(len(c) <= 300 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_split_hard_splits_a_single_huge_line():
    chunks = _split("y" * 1000, limit=250)
    assert all(len(c) <= 250 for c in chunks)
    assert "".join(chunks) == "y" * 1000


def test_split_empty_returns_placeholder():
    assert _split("") == ["(ว่าง)"]


def test_parse_ids_accepts_commas_spaces_newlines():
    assert _parse_ids("1, 2\n3") == {"1", "2", "3"}
    assert _parse_ids("") == set()


def test_plain_neutralises_tags_from_broker_strings():
    assert "<" not in _plain("SL <none>")
    assert "‹none›" in _plain("SL <none>")


# ---------- offset persistence ----------
def test_offset_is_persisted_so_restart_does_not_replay_backlog():
    chat = FakeChat()
    bot = make_bot(chat)
    bot.offset = 4242
    bot._save_offset()
    # Same DB, fresh bot: this is the restart path.
    bot2 = make_bot(chat)
    bot2.offset = 0
    bot2._load_offset()
    assert bot2.offset == 4242


def test_bot_not_configured_without_ids():
    bot = TelegramBot(FakeChat(), token="", allowed_chat_ids=set())
    assert bot.configured is False
"""Acceptance tests for: "make the chat agent actually answer questions".

ROOT CAUSE
    `_handle_chat` called `router.complete("chat", ...)` and, on None, returned a
    fixed menu string ("ถามเรื่อง market/positions/statistics ได้ครับ"). With no
    LLM key the router short-circuits (`model_for` returns None), so every free-
    form question returned that menu. The agent already held the live context and
    discarded it. A second defect sat in `_handle_analysis`: its regex required a
    side word (buy/sell/ขาย/ซื้อ), so "ทำไมวันนี้ถึงไม่เข้าเทรด" fell through to
    another ask-back string.

CONTRACT
    A. A question gets an answer grounded in real state, not a menu.
    B. A follow-up keeps working across the session (history + live context).
    C. When the model fails, the fallback path is correct and says so — and a
       genuinely empty state is not disguised as an answer.

The router is stubbed at the seam so these tests are deterministic and make no
network call.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from app.ai import chat_agent as chat_mod  # noqa: E402
from app.ai.chat_agent import ChatAgent  # noqa: E402

CTX = (
    "🧭 บริบทปัจจุบัน · XAUUSD · M5 · simulator\n"
    "💰 DEMO balance 10000.00 equity 10000.00 margin 0 free 10000.00\n"
    "📈 ราคา bid 2650.0 / ask 2650.2 · วันนี้ ▲ UP 0.45%\n"
    "🎯 Indicator: %K 78.2 / %D 74.9 (overbought) · curve turning_down\n"
    "🧠 สัญญาณ: เข้า=WAIT · ออก=SELL · เหตุผล: %K overbought; curve turning_down\n"
    "📋 positions 0 · วันนี้ 2 trades PnL 12.5"
)

# The exact menu string the old code returned.
OLD_MENU = "ถามเรื่อง market/positions/statistics"
OLD_ANALYSIS_MENU = "ให้ข้อมูลหน่อยสิ"


@pytest.fixture
def agent_llm_down(monkeypatch):
    """ChatAgent with the LLM seam stubbed to return None (model unavailable)."""

    async def _none(*a, **k):
        return None

    monkeypatch.setattr(chat_mod.router, "complete", _none)
    a = ChatAgent.__new__(ChatAgent)
    return a


@pytest.fixture
def agent_llm_up(monkeypatch):
    """ChatAgent with the LLM returning a real answer."""

    async def _ok(*a, **k):
        return {"content": "คำตอบจากโมเดล: %K overbought จึงยังไม่เข้า", "model": "stub"}

    monkeypatch.setattr(chat_mod.router, "complete", _ok)
    a = ChatAgent.__new__(ChatAgent)
    return a


def _menu_strings(*replies):
    """Any reply that still asks the user to pick a topic instead of answering."""
    out = []
    for r in replies:
        if OLD_MENU in r or OLD_ANALYSIS_MENU in r:
            out.append(r[:80])
    return out


# ---------------------------------------------------------------- Case A
@pytest.mark.parametrize("q", [
    "สวัสดี",
    "ตอนนี้พอร์ตเปิดกี่อัน?",
    "วันนี้พั้นธุกิจเป็นอย่างไร",
    "อธิบายให้ฟังหน่อยว่าระบบทำงานยังไง",
    "balance เท่าไหร่",
])
def test_A_freeform_question_answers_from_live_state(agent_llm_down, q):
    """A normal question must produce an answer, not a menu."""
    reply = asyncio.run(agent_llm_down._handle_chat(q, ctx=CTX))
    assert not _menu_strings(reply), f"still returning a menu: {reply[:120]}"
    # Grounded in the real numbers it was given.
    assert "10000" in reply, "answer must be grounded in the live context, not generic"
    assert "XAUUSD" in reply
    # It acknowledges the degraded mode honestly instead of pretending.
    assert "LLM_API_KEY" in reply, "must disclose that the LLM is not connected"


def test_A_answer_covers_the_state_not_just_a_pointer(agent_llm_down):
    """Acceptance: 'does not ask back' alone is not enough — quality matters."""
    reply = asyncio.run(agent_llm_down._handle_chat("เป็นอย่างไรบ้าง", ctx=CTX))
    # Balance AND price AND signal: a real state summary, not a one-liner.
    assert "2650" in reply, "must include the current price"
    assert "WAIT" in reply or "overbought" in reply, "must include the signal/indicator state"
    assert len(reply) > len(CTX), "reply must add value beyond echoing the context"


def test_A_no_context_no_fabrication(agent_llm_down):
    """With no state at all, say so. Do not invent numbers."""
    reply = asyncio.run(agent_llm_down._handle_chat("สวัสดี", ctx=""))
    assert "LLM_API_KEY" in reply
    assert not any(ch.isdigit() for ch in reply), f"must not invent numbers: {reply}"


# ---------------------------------------------------------------- Case B
def test_B_llm_answer_is_returned_verbatim(agent_llm_up):
    """When the model works, its answer must not be replaced by a fallback."""
    reply = asyncio.run(agent_llm_up._handle_chat("ทำไมไม่เข้า?", ctx=CTX))
    assert reply == "คำตอบจากโมเดล: %K overbought จึงยังไม่เข้า"


def test_B_empty_llm_response_is_treated_as_failure(agent_llm_down, monkeypatch):
    """A 200 with empty content is not an answer — must fall back, not return ''."""

    async def _empty(*a, **k):
        return {"content": "   ", "model": "stub"}

    monkeypatch.setattr(chat_mod.router, "complete", _empty)
    reply = asyncio.run(agent_llm_down._handle_chat("สวัสดี", ctx=CTX))
    assert reply.strip(), "must not return an empty reply"
    assert "10000" in reply, "must fall back to the live-context answer"


def test_B_followup_uses_fresh_context(agent_llm_down):
    """Case B: the second question must see current state, not a cached menu."""
    ctx2 = CTX.replace("balance 10000.00", "balance 10500.00")
    r1 = asyncio.run(agent_llm_down._handle_chat("balance?", ctx=CTX))
    r2 = asyncio.run(agent_llm_down._handle_chat("แล้วตอนนี้ล่ะ", ctx=ctx2))
    assert "10000" in r1
    assert "10500" in r2, "follow-up must reflect the context it was given"


# ---------------------------------------------------------------- Case C
def test_C_analysis_without_side_word_no_longer_asks_back(monkeypatch):
    """"ทำไมวันนี้ถึงไม่เข้าเทรด" has no side word; it used to hit the ask-back."""
    intent, _ = ChatAgent.classify("ทำไมวันนี้ถึงไม่เข้าเทรด")
    assert intent == "ANALYSIS"

    a = ChatAgent.__new__(ChatAgent)

    class _Sig:
        signal, reason, conditions_failed = "WAIT", "curve turning_down", ["%K overbought"]

    class _Agent:
        def __init__(self, *a, **k):
            pass

        async def snapshot(self, symbol):
            return {"symbol": symbol, "candles": [], "indicators": {}}

    class _Strategy:
        def __init__(self, *a, **k):
            pass

        async def evaluate(self, version, snap):
            return _Sig()

    monkeypatch.setattr("app.core.agents.MarketAgent", _Agent)
    monkeypatch.setattr("app.core.agents.StrategyAgent", _Strategy)
    a.connector = type("C", (), {"get_positions": staticmethod(lambda: _async([]))})()
    a.registry = type("R", (), {"active_version": staticmethod(lambda: "v1.0")})()
    a.engine = None
    a._engine = None
    a.db = type("D", (), {"query_one": staticmethod(lambda *a, **k: None)})()

    reply = asyncio.run(a._handle_analysis("ทำไมวันนี้ถึงไม่เข้าเทรด", ctx=CTX))
    assert OLD_ANALYSIS_MENU not in reply, f"still asks back: {reply[:120]}"
    assert "WAIT" in reply, "must report the live signal"
    assert "overbought" in reply, "must list the failing conditions"


def test_C_unknown_analysis_shows_state_not_menu(agent_llm_down):
    a = ChatAgent.__new__(ChatAgent)
    reply = asyncio.run(a._handle_analysis("วิเคราะห์ประวัติศาสตร์ยาวมาก", ctx=CTX))
    assert OLD_ANALYSIS_MENU not in reply
    assert "10000" in reply, "must fall back to showing real state"


def test_C_no_silent_failure(agent_llm_down):
    """The degraded path must be visible, never silently empty."""
    reply = asyncio.run(agent_llm_down._handle_chat("ทำอะไรได้บ้าง", ctx=CTX))
    assert len(reply.strip()) > 50, "fallback must be substantive, not a stub"
    assert "LLM" in reply, "must name the cause (LLM not connected)"


async def _async(v):
    return v


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:warnings"]))

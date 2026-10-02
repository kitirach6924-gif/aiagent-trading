"""Reproduce: the chat agent answers with a menu instead of an answer.

GOAL under test: "make the chat agent actually answer questions."

Evidence path being checked:
    router.complete("chat", ...) -> None      (no llm_api_key, or a call failure)
    _handle_chat(...)          -> the menu string, not an answer

The menu string is the behaviour the user reported. Run this and read which
branch each question takes.

Run:  python repro_chat_no_answer.py
"""
import asyncio
import sys

sys.path.insert(0, r"E:\forex\aiagent\backend")

from app.ai.chat_agent import ChatAgent, router  # noqa: E402
from app.config import settings  # noqa: E402

MENU_MARKERS = ("ถามเรื่อง", "LLM ยังไม่ได้ตั้งค่า", "สั่งงาน strategy ได้ครับ")

QUESTIONS = [
    "สวัสดี",
    "ตอนนี้พอร์ตเปิดกี่อัน?",
    "ทำไมวันนี้ถึงไม่เข้าเทรด?",
    "วันนี้พั้นธุกิจเป็นอย่างไร",
    "อธิบายให้ฟังหน่อยว่าระบบทำงานยังไง",
    "balance เท่าไหร่",
]

print("=" * 66)
print("STATE")
print("=" * 66)
print(f"  llm_api_key configured : {bool(settings.llm_api_key)}")
print(f"  model_for('chat')      : {router.model_for('chat')}")
print(f"  => router.complete() will short-circuit and return None"
      if not settings.llm_api_key else "  => router will attempt a live call")
print()

agent = ChatAgent.__new__(ChatAgent)  # no DB needed for the chat branch


async def main():
    print("=" * 66)
    print("REPRODUCTION — does the agent answer, or hand back a menu?")
    print("=" * 66)
    menu_hits = 0
    for q in QUESTIONS:
        intent, _ = ChatAgent.classify(q)
        reply = await agent._handle_chat(q, ctx="[balance=10000 equity=10000 price=2650]")
        is_menu = any(m in reply for m in MENU_MARKERS)
        menu_hits += is_menu
        label = "MENU (asks back)" if is_menu else "ANSWER"
        print(f"\n  Q: {q}")
        print(f"     intent={intent}  ->  {label}")
        print(f"     A: {reply[:150]}")

    print()
    print("=" * 66)
    print(f"RESULT: {menu_hits}/{len(QUESTIONS)} questions got a menu, not an answer")
    print("=" * 66)
    if menu_hits == len(QUESTIONS):
        print("REPRODUCED: every question returns the same menu string.")
        print("Root cause is NOT the prompt — it is that router.complete() returns")
        print("None, and the only fallback is a menu. The user gets zero information")
        print("even though the agent already has live context in hand.")
        return 0
    print("NOT REPRODUCED — behaviour differs; investigate before fixing.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

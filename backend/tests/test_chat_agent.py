import pytest

from app.ai.chat_agent import ChatAgent
from app.integrations.telegram import TelegramNotifier


@pytest.fixture()
def chat_agent(fresh_db, registry, sim):
    from app.core.journal import StatisticsEngine, TradeJournal
    journal = TradeJournal(fresh_db, registry)
    stats = StatisticsEngine(fresh_db)
    return ChatAgent(fresh_db, sim, registry, journal, stats, TelegramNotifier())


def test_classification(chat_agent):
    assert ChatAgent.classify("หยุดเปิดออเดอร์ใหม่")[0] == "CONTROL"
    assert ChatAgent.classify("resume trading")[0] == "CONTROL"
    assert ChatAgent.classify("emergency stop")[0] == "CONTROL"
    assert ChatAgent.classify("เพิ่มกฎว่า spread สูงให้หยุดเทรด")[0] == "STRATEGY"
    assert ChatAgent.classify("ทดสอบ v1.1")[0] == "STRATEGY"
    assert ChatAgent.classify("ช่วยวิเคราะห์ trade ที่แพ้")[0] == "ANALYSIS"
    assert ChatAgent.classify("สถานะตลาดตอนนี้เป็นอย่างไร")[0] == "READ"
    assert ChatAgent.classify("วันนี้เทรดไปกี่ครั้ง")[0] == "READ"
    assert ChatAgent.classify("hello")[0] == "READ"


@pytest.mark.asyncio
async def test_read_market(chat_agent):
    out = await chat_agent.handle("สถานะตลาดตอนนี้เป็นอย่างไร", user="tester")
    assert "XAUUSD" in out["reply"]
    assert out["intent"] == "READ"


@pytest.mark.asyncio
async def test_proposal_flow(chat_agent, fresh_db):
    out = await chat_agent.handle("เพิ่มกฎว่า ถ้า spread สูงให้หยุดเทรด", user="tester")
    assert "PROPOSAL" in out["reply"]
    props = fresh_db.query("SELECT * FROM proposals")
    assert props and props[0]["status"] == "PROPOSAL"
    # nothing silently became a trading rule: strategy v1.0 unchanged
    assert fresh_db.query_one("SELECT * FROM strategies WHERE version='v1.0'")


@pytest.mark.asyncio
async def test_pause_resume(chat_agent, fresh_db):
    out = await chat_agent.handle("หยุดเปิดออเดอร์ใหม่", user="tester")
    assert "paused" in out["reply"].lower() or "⏸" in out["reply"]
    assert fresh_db.kv_get("paused") is True
    await chat_agent.handle("Resume Trading", user="tester")
    assert fresh_db.kv_get("paused") is False


@pytest.mark.asyncio
async def test_emergency_stop(chat_agent, fresh_db):
    await chat_agent.handle("emergency stop", user="tester")
    assert fresh_db.kv_get("emergency_stop") is True


@pytest.mark.asyncio
async def test_chat_history_persisted(chat_agent):
    await chat_agent.handle("hello", user="tester")
    hist = chat_agent.history(10)
    assert len(hist) >= 2  # user + agent

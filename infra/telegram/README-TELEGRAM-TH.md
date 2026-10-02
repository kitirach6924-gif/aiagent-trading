# ตั้ง Telegram Bot สำหรับ AI_MT5 (5 นาที)

## ขั้น 1 — สร้างบอทกับ BotFather

1. เปิดแชทกับ **@BotFather** ใน Telegram (บัญชีทางการ: มีตรา verified สีน้ำเงิน)
2. พิมพ์ `/newbot` แล้วตอบ:
   - **ชื่อบอท** (แสดงในโปรไฟล์): แนะนำ `AI MT5 Agent`
   - **username** (ต้องลงท้าย `bot`): เช่น `ai_mt5_agent_bot`
3. BotFather ตอบข้อความมาพร้อม **token** รูปแบบ:
   ```text
   1234567890:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
   ⚠ Token = รหัสลับเทียบเท่ารหัสผ่าน — ห้ามส่งให้ใคร/ห้ามขึ้น git

## ขั้น 2 — เอา chat_id ของคุณ (ใช้สคริปต์ที่เตรียมไว้)

1. กด **Start** ในแชทกับบอทที่เพิ่งสร้าง (สำคัญ — Telegram ห้ามบอททักผู้ใช้ก่อน)
2. รัน (ใส่ token ของคุณ):
   ```bash
   cd E:/forex/aiagent && python infra/telegram/get_chat_id.py 1234567890:AAHxxx...
   ```
   ผลลัพธ์จะได้ประมาณ `"chat": {"id": 812345678}` → เลขนั้นคือ **chat_id**
   (แจ้งเตือนแบบ channel/group ก็ได้: เพิ่มบอทเข้า group แล้วรันสคริปต์เห็น id ของ group เช่น `-100xxxx`)

## ขั้น 3 — ใส่ค่าใน env ของ backend

แก้ `backend/.env` (หรือ env ตอนรัน):
```text
TELEGRAM_BOT_TOKEN=1234567890:AAHxxx...
TELEGRAM_CHAT_ID=812345678
```

## ขั้น 4 — ทดสอบส่งจริง

ทางเลือก A (dashboard): หน้า **System → Telegram** → ใส่ token/chat_id ช่องทดสอบ (ไม่บันทึกถาวร) → กด **ส่งข้อความทดสอบ** → ต้องได้แชท "✅ AI_MT5 test"

ทางเลือก B (command line):
```bash
curl -X POST http://127.0.0.1:8000/api/telegram/test -H "Content-Type: application/json" -d "{}"
```
ต้องตอบ `{"ok": true, ...}` และมีข้อความเข้าแชทของคุณ

## ระบบจะแจ้งเตือนอะไรบ้าง (อัตโนมัติเมื่อ token พร้อม)

`TRADE_OPEN` 🟢 · `TRADE_CLOSE` 🏁 · `TRADE_CLOSE_FAILED` · `RISK_BLOCK` ⛔ · `EMERGENCY_STOP` 🚨 · `TRADING_PAUSED/RESUMED` ⏸▶ · `SUPERVISOR_CLOSE_ALL` 🧹 · `STRATEGY_APPROVED` · `BACKTEST_COMPLETED` · `LOOP_ERROR` · `AI_SECURITY_REJECT` — ทุกข้อความมีโหมด `[DEMO][AUTONOMOUS]` นำหน้าเมื่อระบบรันโหมด demo autonomous

## เปลี่ยน/เพิกถอน token

- สร้างใหม่ทั้งชุด: แชท BotFather → `/revoke` → เอา token ใหม่ใส่แทน
- บอทหาย/สูญหาย: `/deletebot` แล้วสร้างใหม่ตามขั้น 1

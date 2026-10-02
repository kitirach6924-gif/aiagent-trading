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

## ขั้น 3.5 — เปิดโหมด "ถามบอทได้" (inbound bot)

ข้างบนคือการ **ส่ง event ออก** (notifier) ส่วนนี้คือการ **ถามเข้ามา** —
บอทจะ long-poll `getUpdates` แล้วส่งคำถามเข้า ChatAgent ตัวเดียวกับที่หน้าเว็บใช้
(อ่านสถิติ/ราคา/position/strategy สดจาก MT5) แล้วตอบกลับมา

ตั้งค่าเพิ่มใน `backend/.env`:
```text
TELEGRAM_BOT_ALLOWED_CHAT_IDS=812345678
TELEGRAM_BOT_READ_ONLY=true
```
แล้ว restart backend — ดูสถานะบอทได้ที่ `GET /api/telegram/status` (`bot.read_only`,
`bot.running`) หรือ log ตอนบูตจะขึ้น `[telegram_bot] online · read_only=True`

### ถามได้อะไร
- คำถามธรรมดา: `สถานะตอนนี้`, `วันนี้เทรดกี่ครั้ง`, `เปิด position ไหม`,
  `ทำไมยังไม่เข้า BUY`, `ราคาทองตอนนี้`, `สถิติ/performance`
- คำสั่ง: `/start` `/help` · `/report` (สรุปวันนี้) · `/status` ·
  `/quiet` (หยุด event) · `/notify` (เปิดคืน)
- ทุกคำตอบขึ้นต้นด้วยบล็อก "บริบทปัจจุบัน" (balance/equity/ราคา/%K-%D/สัญญาณ/positions)
  ด้วย — ถ้าไม่เห็นบล็อกนี้ แปลว่า MT5 bridge ล่ม

### ข้อจำกัดด้านความปลอดภัย (ตั้งใจทำ)
1. **allowlist** — ข้อความจาก chat นอกรายการถูกปฏิเสธก่อนถึง agent ใดๆ และ group ถูกมองข้าม
2. **read-only** — คำสั่งที่ classify เป็น CONTROL/STRATEGY ถูกปฏิเสธก่อนถึง handler
   ที่จะเซ็ต `paused`/`emergency_stop` หรือแตะ strategy registry ดังนั้นข้อความจากมือถือ
   **หยุดเทรดไม่ได้** ต้องที่หน้าเว็บหรือสั่ง Titan ตรงๆ
   (ถ้าจะเปิดสิทธิ์คุม ให้ตั้ง `TELEGRAM_BOT_READ_ONLY=false` — และรับความเสี่ยงเอง)
3. **ไม่กระทบ trading** — ทุก error ใน loop ถูกกลืนและ retry กลับ บอทล่มก็ไม่ทำให้ agent หยุด

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

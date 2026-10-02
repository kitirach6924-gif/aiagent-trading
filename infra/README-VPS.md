# VPS Deployment Guide

## 1) Windows machine ที่รัน MT5 (bridge host)

- ติดตั้ง MetaTrader 5 + login **Demo account**
- รัน MT5 MCP bridge ที่ expose tools:
  `market.get_price, market.get_candles, market.get_indicator, market.get_symbol_info,
  account.get_status, account.get_positions, account.get_history,
  trade.open, trade.close, trade.modify, system.get_status`
- เปิดพอร์ตเช่น `8765` ให้ VPS เข้าถึงได้ (หรือรัน bridge บน VPS ผ่าน Wine ก็ได้ แต่ยังไม่แนะนำ)

## 2) VPS (Linux)

```bash
git clone <repo> && cd <repo>
cp .env.example .env      # แก้ค่า: MT5_SERVER_HOST=<IP เครื่อง MT5>, MT5_SERVER_PORT=8765
                          # TELEGRAM_BOT_TOKEN/CHAT_ID, LLM_API_KEY (ถ้ามี)
                          # FIREBASE_PROJECT_ID
cd infra
docker compose up -d --build
```

ตรวจสถานะ:

```bash
curl http://localhost:8000/api/health
```

ถ้า bridge ยังไม่พร้อม ระบบจะ fail-closed เป็นโหมด SIMULATOR — **ห้ามใช้กับ LIVE**
(`LIVE_TRADING=true` จะบังคับให้ connector ล้มเหลวทันทีถ้า MCP ใช้ไม่ได้ ไม่มี fallback เงียบๆ)

## 3) Firebase Dashboard

```bash
cd frontend
cp ../.env.example .env   # หรือสร้าง frontend/.env ด้วย VITE_FIREBASE_* จาก Firebase console
npm run build
firebase deploy --only hosting
```

- เปิดใช้ Authentication (Google + Email/Password) ใน Firebase console
- ตั้ง `FIREBASE_PROJECT_ID` ฝั่ง backend ให้ตรง เพื่อ verify Firebase ID token
- Backend URL: ชี้ reverse proxy หรือตั้ง vite proxy ใน production build

## 4) ลำดับการเปิดใช้งานจริง (ภายหลัง — ยังล็อกไว้)

1. Demo run ≥ 2 สัปดาห์, สถิติผ่านเกณฑ์ที่ตกลงกัน
2. Walk-forward + backtest evidence ครบ
3. ตั้ง risk limits ที่เห็นพ้อง (env) — AI แก้ไม่ได้
4. มีผู้ดูแล confirm แล้วจึงตั้ง `TRADING_MODE=LIVE`, `LIVE_TRADING=true` ใน env ด้วยตนเอง
   (ระบบไม่มี API ใดให้ AI/Chat เปิดได้)

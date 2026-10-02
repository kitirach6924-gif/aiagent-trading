# คู่มือย้าย AI_MT5 ขึ้น Windows VPS (24/7)

> เป้าหมาย: backend + MT5 terminal รันบน VPS ตลอดเวลา, dashboard บน Firebase Hosting เชื่อมมาที่ VPS, ความปลอดภัยเหมือนเดิม (DEMO, fail-closed)

## สิ่งที่ต้องมี
- Windows VPS (แนะนำ 2 vCPU / 4 GB RAM / Windows Server 2022) + RDP
- ไฟล์โปรเจ็คทั้งหมด (zip ขึ้น RDP หรือ git clone) ไปไว้ที่ `C:\ai-mt5`
- ติดตั้ง: [MetaTrader 5](https://www.metatrader5.com/) + Python 3.13 (ติด "Add python.exe to PATH")

## ขั้นตอน

### 1) MT5 terminal (บน VPS)
1. เปิด MT5 → login **XM demo** (account 336943116 หรือ demo ใหม่) เหมือนเครื่องเดิม
2. เปิด Tools → Options → Expert Advisors → ติ๊ก **Allow algorithmic trading**
3. เขียง Market Watch: ให้แสดง **GOLD** (คลิกขวา → Symbols → ค้น GOLD → Show)
4. ค้าง terminal เปิดไว้ (อย่าล็อกหน้าจอแบบ disconnect session — ใช้ disconnect แบบ console หรือปล่อย session RDP แบบ reconnect ได้)

### 2) โค้ด + environment
1. ก๊อปไฟล์ `dist\ai-mt5-vps.zip` (สร้างด้วย `infra\vps\windows\pack-for-vps.ps1` จากเครื่องเดิม — มีโค้ด + frontend build + service account key ครบในไฟล์เดียว) ขึ้น VPS แล้วแตกเป็น `C:\ai-mt5`
2. คัดลอก `infra\vps\windows\.env.example` → `backend\.env` แล้วแก้ค่า (CORS มีโดเมน hosting ให้แล้ว)
3. *(ถ้าแพ็กเองโดยไม่เอา key ใน zip)* คัดลอก **service account key** `backend\ai-mt5-backend-key.json` จากเครื่องเดิมมาด้วย (ไฟล์นี้ห้ามขึ้น git อยู่แล้ว)

### 3) รันสคริปต์ติดตั้ง (PowerShell Admin)
```powershell
cd C:\ai-mt5\infra\vps\windows
Set-ExecutionPolicy -Scope Process Bypass -Force
.\setup-windows-vps.ps1
```
สคริปต์ทำให้อัตโนมัติ: venv + dependencies → เช็ค MT5 → ติดตั้ง **บริการ `ai-mt5-core`** (NSSM: auto-start หลัง reboot, ถ้าพังรีสตาร์ทเองใน 5 วิ) → health check
> NSSM: สคริปต์ลอง `winget install nssm` ก่อน ถ้า VPS ไม่มี winget ให้ดาวน์โหลดจาก https://nssm.cc/release/nssm-2.24.zip แตกไฟล์เก็บ `nssm.exe` ไว้ที่ `C:\ai-mt5\tools\nssm-2.24\win64\` แล้วรันสคริปต์ใหม่

### 4) HTTPS ให้ API (จำเป็น — หน้า hosting เป็น https)
1. สร้างโดเมนฟรี เช่น duckdns.org ชี้ IP ของ VPS
2. แก้ `infra/vps/windows/Caddyfile` บรรทัด `yourname.duckdns.org` → โดเมนของคุณ
3. เปิด firewall/NSG พอร์ต 80+443 (แนะนำจำกัด source เป็นของคุณถ้าทำได้; อย่างน้อยปิด 8000 จากภายนอก — Caddy ต่อแบบ local เท่านั้น)
4. `winget install CaddyServer.Caddy` แล้ว `caddy start --config C:\ai-mt5\infra\vps\windows\Caddyfile` (ขอใบ HTTPS ให้เอง + ต่ออายุอัตโนมัติ)
5. เปิดบริการ Caddy ตอนบูต: `caddy start` ตอน login ครั้งแรก หรือติดตั้งเป็น service ด้วย `sc create caddy ...`

### 5) Dashboard ชี้มาที่ VPS
สร้าง `frontend/.env.production`:
```
VITE_API_BASE=https://โดเมนของคุณ.duckdns.org
VITE_WS_BASE=wss://โดเมนของคุณ.duckdns.org
```
แล้ว build + deploy จากเครื่องไหนก็ได้:
```bash
cd frontend && npm run build
cd .. && npx firebase deploy --only hosting
```

### 6) ตรวจว่าระบบรอดตายจริง
```powershell
# บน VPS
Get-Service ai-mt5-core                          # Running
Get-Content C:\ai-mt5\backend\data\service.log -Tail 50
curl.exe http://127.0.0.1:8000/api/health
curl.exe https://โดเมนของคุณ.duckdns.org/api/health   # ผ่าน Caddy
```
- เปิด https://my-first-project-24042.web.app → login → dashboard ต้องมีราคาไหล + dots เขียว (AGENT/MT5/MCP/LIVE)
- **ทดสอบ resilience:** Restart VPS 1 ครั้ง → บริการต้องกลับมาเอง, agent โหลด positions/state ใหม่แล้ว resume (ระบบไม่ส่ง order เก่าซ้ำ — idempotency key ใน loop)
- Firestore: doc `system_status/current` ต้องอัปเดตทุก ≤30 วิ (ตรวจใน Console หรือ dashboard)

## ข้อควรระวังสำคัญ
- **จอ MT5 ต้อง "ตื่น"**: บาง VPS logoff RDP แล้ว MT5 หยุดรับข้อมูล — ใช้ session แบบ console หรือเครื่องมือ keep-session (เช่น `tscon` ไป console) ทดสอบปล่อยค้างคืนก่อนใช้จริง
- **เปลี่ยนรหัส admin@ai-mt5.local** ก่อนเปิดใช้งานจริง (Console → Authentication → Users → reset password)
- API ของ VPS อย่าเปิดพอร์ต 8000 ตรงสู่อินเทอร์เน็ต — ให้ผ่าน Caddy (HTTPS) เท่านั้น
- LIVE ยังล็อกอยู่: ต้องผ่านเกณฑ์ DEMO ตาม README แล้วแก้ env เองด้วยมือ (AI เปลี่ยนไม่ได้)

## ถ้าอยากย้ายไป Linux VPS ภายหลัง
backend รัน Docker ได้เลย (infra/docker-compose.yml) แต่ MT5 bridge ต้องอยู่บน Windows ตัวหนึ่งเสมอ (VPS นี้หรือเครื่องบ้านผ่าน HTTP bridge + Tailscale) — คุยกันต่อได้เมื่อพร้อม

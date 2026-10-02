# ตั้งโดเมน DuckDNS (ฟรี) + Firewall + Caddy บน Windows VPS — ทีละขั้น

> เป้าหมาย: ได้ `https://<ชื่อ>.duckdns.org` ที่ยิงเข้า backend บน VPS (พอร์ต 8000 อยู่หลัง Caddy เท่านั้น ไม่เปิดสู่อินเทอร์เน็ต)
> ใช้ตัวอย่างโดเมน `aimt5.duckdns.org` — เปลี่ยนเป็นชื่อที่คุณจดทุกครั้ง

---

## ขั้นที่ 1 — จดโดเมนฟรีกับ DuckDNS (3 นาที)

1. บนเครื่องไหนก็ได้ เปิด https://www.duckdns.org → กด **Login** (ใช้ GitHub/Google/Reddit ก็ได้)
2. ช่อง **add domain** พิมพ์ชื่อที่อยากได้ เช่น `aimt5` → กด **add domain**
3. จดสิ่งเหล่านี้จากหน้าเว็บ:
   - โดเมนเต็ม: `aimt5.duckdns.org`
   - **token** สำหรับอัปเดต IP (ตัวอักษรยาว ๆ ในหัวมุมหน้า เช่น `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`)
4. ในช่อง **current ip** กด **update ip** ให้มันจับ IP ของ VPS (หรือพิมพ์ IP สาธารณะของ VPS เอง — ดู IP ได้จากหน้า Azure Portal → VPS → Overview → Public IP)
5. แถวของโดเมนต้องขึ้น IP ของ VPS ถูกต้อง

> DuckDNS อัปเดต DNS เร็วมาก (ทันทีถึง ~1 นาที) และให้ใบ Let's Encrypt ได้ปกติ

---

## ขั้นที่ 2 — เปิด Firewall/NSG ให้พอร์ต 80 + 443 (ปิด 8000 ไว้)

ทำ **2 ชั้น** — ข้ามชั้นไหนไป Caddy จะขอใบรับรองไม่ได้:

### 2.1 Cloud-level (Azure Portal → NSG หรือ AWS Security Group)
1. Azure Portal → หา VM ของคุณ → **Networking** → **Network settings** (หรือ NSG ที่ผูกกับ NIC)
2. **Add inbound port rule**:
   - Source: `Any`
   - Source port ranges: `*`
   - Destination: `Any`
   - Destination port ranges: `80`
   - Protocol: `TCP`, Action: **Allow**, Priority: `310`, Name: `HTTP-Caddy`
3. เพิ่มอีก rule แบบเดียวกันสำหรับพอร์ต `443` (Name: `HTTPS-Caddy`, Priority: `311`)
4. **ตรวจว่าไม่มี rule Allow พอร์ต 8000** จาก Any (ถ้ามี ลบทิ้งหรือจำกัด Source เป็น IP คุณ)

### 2.2 Windows Firewall บนตัว VPS (รันใน PowerShell **Admin**)
```powershell
New-NetFirewallRule -DisplayName "AI_MT5 HTTP (Caddy)" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow | Out-Null
New-NetFirewallRule -DisplayName "AI_MT5 HTTPS (Caddy)" -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow | Out-Null
# เผื่อใช้งานตรวจสอบจากภายนอกผ่าน Caddy เท่านั้น — ไม่เปิด 8000
Write-Host "firewall rules OK"
```

### 2.3 ทดสอบ DNS จากเครื่องคุณ
```powershell
nslookup aimt5.duckdns.org
# ต้องตอบ IP สาธารณะของ VPS
```

---

## ขั้นที่ 3 — ติดตั้ง Caddy บน VPS

**ทางเลือก A — winget (แนะนำ):**
```powershell
winget install CaddyServer.Caddy
# ปิดเปิด PowerShell ใหม่ให้ PATH ขึ้น แล้วตรวจ:
caddy version
```

**ทางเลือก B — ถ้า VPS ไม่มี winget:** ดาวน์โหลด https://caddyserver.com/download (Windows amd64) → เก็บ `caddy.exe` ไว้ที่ `C:\ai-mt5\tools\caddy\` แล้วเรียกแบบเต็ม path แทน

---

## ขั้นที่ 4 — ตั้งค่า Caddyfile

1. แก้ไฟล์ `C:\ai-mt5\infra\vps\windows\Caddyfile` บรรทัดแรกของ site:
   ```
   aimt5.duckdns.org {
   ```
   (ส่วน handle /api/* และ /ws ใช้ตามเดิมได้เลย)
2. ทดสอบ config และรันครั้งแรก:
   ```powershell
   cd C:\ai-mt5\infra\vps\windows
   caddy validate --config .\Caddyfile
   caddy start --config .\Caddyfile
   ```
   - ครั้งแรก Caddy จะ**ขอใบรับรอง HTTPS จาก Let's Encrypt ให้เอง** — ต้องผ่านพอร์ต 80/443 ตามขั้นที่ 2
   - ดู log ที่หน้าจอ ถ้าขึ้น `certificate obtained successfully` = สำเร็จ

## ขั้นที่ 5 — ทดสอบผ่าน HTTPS จริง

```powershell
# บน VPS
curl.exe https://aimt5.duckdns.org/api/health
# ต้องได้ {"ok":true,"mode":"DEMO",...}
```
```powershell
# บนเครื่องคุณ (ทดสอบข้ามเครื่อง)
curl.exe https://aimt5.duckdns.org/api/health
```

---

## ขั้นที่ 6 — ให้ Caddy เริ่มเองหลัง reboot (Windows Service)

ใช้ NSSM ที่มีจากขั้นติดตั้งโปรเจ็ค (หรือดาวน์โหลด nssm.cc):
```powershell
$nssm = "C:\ai-mt5\tools\nssm-2.24\win64\nssm.exe"
& $nssm install ai-mt5-caddy "C:\Program Files\Caddy\caddy.exe" 'run --config "C:\ai-mt5\infra\vps\windows\Caddyfile"'
& $nssm set ai-mt5-caddy Start SERVICE_AUTO_START
& $nssm set ai-mt5-caddy AppStdout C:\ai-mt5\backend\data\caddy-service.log
& $nssm set ai-mt5-caddy AppStderr C:\ai-mt5\backend\data\caddy-service.log
Start-Service ai-mt5-caddy
Get-Service ai-mt5-caddy
```
> ถ้าเคย `caddy start` ไว้ ให้ `caddy stop` ก่อนติดตั้ง service (กันชนกันบนพอร์ต)

---

## ขั้นที่ 7 — ชี้ dashboard มาที่ VPS (ผมทำให้ได้)

สร้าง `frontend\.env.production`:
```
VITE_API_BASE=https://aimt5.duckdns.org
VITE_WS_BASE=wss://aimt5.duckdns.org
```
แล้ว build + deploy:
```bash
cd frontend && npm run build
cd .. && npx firebase deploy --only hosting
```
เปิด https://my-first-project-24042.web.app → login → กราฟสด + AI Chat + ปุ่มควบคุมใช้ได้เต็มรูปแบบจากทุกที่

---

## แก้ปัญหาที่เจอบ่อย

| อาการ | สาเหตุ/ทางแก้ |
|---|---|
| Caddy ขอ cert ไม่ได้ (`connection refused` จาก Let's Encrypt) | NSG/Firewall ยังไม่เปิด 80 หรือ 443 — กลับไปขั้นที่ 2 |
| `dns problem: NXDOMAIN` | DuckDNS ยังชี้ IP ผิด — กลับไปขั้นที่ 1.4 (`nslookup` ตรวจ) |
| cert ได้แต่ /api 502 | backend ยังไม่รัน — ตรวจ `Get-Service ai-mt5-core`, `curl.exe http://127.0.0.1:8000/api/health` |
| เว็บเข้าได้แต่ WS ตาย | ตรวจว่า Caddyfile มี `handle /ws` และ frontend ตั้ง `VITE_WS_BASE=wss://...` แล้ว |
| ต่ออายุ cert | ไม่ต้องทำอะไร — Caddy ต่ออายุอัตโนมัติ (บริการต้องรันอยู่) |

## ความปลอดภัย (ทำครบ)
- ✅ พอร์ต 8000 **ห้าม**เปิดจากภายนอก — API ผ่าน Caddy เท่านั้น
- ✅ API ทุกตัว (ยกเว้น /api/health) ต้องมี Firebase ID token — ใครไม่มีสิทธิ์เข้า dashboard จะยิง API ตรง ๆ ก็ถูกปฏิเสธ
- ✅ เปลี่ยนรหัส `admin@ai-mt5.local` ก่อนเปิดใช้งานจริง
- ✅ RDP: จำกัด Source IP หรือใช้พอร์ตไม่มาตรฐาน (ขั้นสูง — เพิ่มเติมภายหลังได้)

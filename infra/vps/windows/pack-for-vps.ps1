# ===== AI_MT5 — แพ็กโปรเจ็คเป็น zip เพื่อก๊อปขึ้น Windows VPS =====
# รันที่เครื่องนี้ (PowerShell): .\infra\vps\windows\pack-for-vps.ps1
# ได้ไฟล์: dist\ai-mt5-vps.zip  → ก๊อปไปวางที่ C:\ บน VPS แล้วแตกไฟล์เป็น C:\ai-mt5

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSCommandPath)))
Set-Location $ROOT

$dist = Join-Path $ROOT "dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$zip = Join-Path $dist "ai-mt5-vps.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }

$staging = Join-Path $env:TEMP ("ai-mt5-pack-" + [guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Force -Path $staging | Out-Null

Write-Host "คัดลอกไฟล์โปรเจ็ค (ตัดส่วน dev)..."
robocopy $ROOT $staging /E /NFL /NDL /NJH /NJS /NP /XD ".git" "node_modules" ".venv" "venv" "__pycache__" ".pytest_cache" "dist" ".freebuff" "tools" /XF "*.pyc" ".env" "*.log" | Out-Null

Write-Host "ใส่ frontend/dist (build สำเร็จล่าสุด) และ .env.example เพิ่ม..."
Copy-Item -Recurse -Force (Join-Path $ROOT "frontend\dist") (Join-Path $staging "frontend\dist")
Copy-Item -Force (Join-Path $ROOT "infra\vps\windows\.env.example") (Join-Path $staging "backend\.env.example")
Copy-Item -Force (Join-Path $ROOT "firebase.json"), (Join-Path $ROOT ".firebaserc") $staging

# แจ้งเตือนเรื่อง service account key (ต้องมีติดไปเพื่อให้ Firestore ทำงาน — ลบทิ้งจาก zip เมื่อไม่ต้องการ)
$key = Join-Path $staging "backend\ai-mt5-backend-key.json"
if (Test-Path $key) {
  Write-Warning "zip นี้มี backend\ai-mt5-backend-key.json (จำเป็นสำหรับ Firestore) — เก็บ zip ให้ปลอดภัย อย่าแชร์สาธารณะ"
}

Write-Host "บีบอัดเป็น $zip ..."
Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $zip -Force
Remove-Item -Recurse -Force $staging

$sizeMB = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host ""
Write-Host "เสร็จ: $zip ($sizeMB MB)" -ForegroundColor Green
Write-Host "ขั้นตอนต่อไปอ่านที่ infra\vps\windows\README-VPS-TH.md (หัวข้อ 1-3)"

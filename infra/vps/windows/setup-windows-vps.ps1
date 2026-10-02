# ===== AI_MT5 — Windows VPS one-shot setup =====
# รันใน PowerShell (Admin):
#   Set-ExecutionPolicy -Scope Process Bypass -Force
#   .\setup-windows-vps.ps1
#
# สมมติโครงสร้าง: C:\ai-mt5\  (repo นี้)  |  MT5 terminal ติดตั้งแล้ว + login XM demo แล้ว
# ทำ: ตรวจ Python/MT5 → venv + deps → เช็ค bridge → ติดตั้งบริการ NSSM (auto-restart)

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSCommandPath)))  # infra/vps/windows -> repo root
Set-Location $ROOT

Write-Host "== 1) ตรวจ Python 3.13 ==" -ForegroundColor Cyan
python --version

Write-Host "== 2) สร้าง venv + ติดตั้ง dependencies ==" -ForegroundColor Cyan
python -m venv backend\.venv
& backend\.venv\Scripts\python -m pip install --upgrade pip
& backend\.venv\Scripts\pip install -r backend\requirements.txt
& backend\.venv\Scripts\pip install firebase-admin cryptography

Write-Host "== 3) เช็ค MT5 bridge (terminal ต้องเปิด + login demo แล้ว) ==" -ForegroundColor Cyan
& backend\.venv\Scripts\python -c "import MetaTrader5 as mt5; ok = mt5.initialize(); print('MT5 initialize:', ok); mt5.shutdown()"
if ($LASTEXITCODE -ne 0) { Write-Warning "MT5 ยังต่อไม่ได้ — เปิด MT5 terminal แล้ว login demo ก่อน แล้วรันสคริปต์ใหม่" }

Write-Host "== 4) สร้างบริการ ai-mt5-core ด้วย NSSM (auto-start + auto-restart) ==" -ForegroundColor Cyan
$nssm = Get-Command nssm -ErrorAction SilentlyContinue
if (-not $nssm) {
  $nssmDir = Join-Path $ROOT "tools\nssm-2.24\win64"
  if (Test-Path (Join-Path $nssmDir "nssm.exe")) {
    Write-Host "ใช้ NSSM ที่แถมมากับโปรเจ็ค: $nssmDir\nssm.exe"
    $env:Path = "$nssmDir;$env:Path"
  } else {
    Write-Host "ติดตั้ง NSSM ด้วย winget (ถ้าไม่ได้ ให้ดาวน์โหลด nssm.cc เก็บไว้ที่ tools\nssm-2.24\win64\)"
    winget install --id nssm -e --accept-source-agreements --accept-package-agreements
  }
}
$nssmExe = (Get-Command nssm).Source
$pyExe = Join-Path $ROOT "backend\.venv\Scripts\pythonw.exe"
& $nssmExe install ai-mt5-core $pyExe (Join-Path $ROOT "backend\run_dev.py")
& $nssmExe set ai-mt5-core AppDirectory (Join-Path $ROOT "backend")
& $nssmExe set ai-mt5-core AppStdout (Join-Path $ROOT "backend\data\service.log")
& $nssmExe set ai-mt5-core AppStderr (Join-Path $ROOT "backend\data\service.log")
& $nssmExe set ai-mt5-core AppRotateFiles 1
& $nssmExe set ai-mt5-core Start SERVICE_AUTO_START
& $nssmExe set ai-mt5-core AppExit Default Restart
& $nssmExe set ai-mt5-core AppRestartDelay 5000
Start-Service ai-mt5-core

Write-Host "== 5) Health check ==" -ForegroundColor Cyan
Start-Sleep -Seconds 8
try { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/health -TimeoutSec 10).Content } catch { Write-Warning "health ยังไม่ตอบ: $_" }

Write-Host "== เสร็จ! ขั้นถัดไป ==" -ForegroundColor Green
Write-Host "1) แก้ Caddyfile (โดเมนของคุณ) แล้วรัน: caddy start --config .\infra\vps\windows\Caddyfile"
Write-Host "2) frontend: ตั้ง VITE_API_BASE + VITE_WS_BASE ชี้ https://<โดเมน> แล้ว npm run build + firebase deploy --only hosting"
Write-Host "3) ดู log: Get-Content backend\data\service.log -Tail 50 หรือ Services (services.msc)"

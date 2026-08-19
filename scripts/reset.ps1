# 一鍵重置(Windows / PowerShell):清掉整個資料 volume,重新建置啟動。
# 重啟後依 .env.docker 的 DEMO_SEED 重種。想從「試用」轉「正式空白」:
# 先把 .env.docker 的 DEMO_SEED 改成 blank,再跑這支。
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "清除資料 volume(gv-data)並重新建置..." -ForegroundColor Yellow
docker compose down -v
docker compose up -d --build

Write-Host "完成 — 開 http://localhost:8787" -ForegroundColor Green

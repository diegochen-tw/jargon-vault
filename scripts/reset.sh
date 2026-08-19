#!/bin/sh
# 一鍵重置(Linux / macOS):清掉整個資料 volume,重新建置啟動。
# 重啟後依 .env.docker 的 DEMO_SEED 重種。想從「試用」轉「正式空白」:
# 先把 .env.docker 的 DEMO_SEED 改成 blank,再跑這支。
set -e
cd "$(dirname "$0")/.."

echo "清除資料 volume(gv-data)並重新建置..."
docker compose down -v
docker compose up -d --build

echo "完成 — 開 http://localhost:8787"

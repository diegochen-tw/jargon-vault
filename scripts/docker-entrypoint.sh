#!/bin/sh
# 容器進入點:先跑冪等的 demo 種子(依 DEMO_SEED),再啟動 app。
# app 啟動時 create_app() 會對每個現有使用者重建索引 / 補齊設定,所以種子腳本
# 只要把使用者與 .md/tags 落地即可。
set -e

python scripts/seed_demo.py "${DEMO_SEED:-sample}"

exec python main.py

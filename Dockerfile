# Jargon Vault — 無 build step,image 很薄:裝依賴 + 複製原始碼即可。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GLOSSARY_HOST=0.0.0.0 \
    GLOSSARY_PORT=8787 \
    GLOSSARY_DATA_DIR=/data

WORKDIR /app

# 先只複製依賴清單,讓這層在原始碼變動時仍能命中快取。
COPY requirements.txt ./
RUN pip install -r requirements.txt

# 只複製執行需要的東西。data/ 是私有使用者資料,刻意不進 image(見 .dockerignore)。
# official_plugins/ 是官方外掛封裝的型錄——漏了它,啟動時每個帳號的外掛遷移會在
# log 報「官方封裝目錄缺少」,外掛頁也會是空的(app/plugin_catalog.py)。
COPY app/ ./app/
COPY static/ ./static/
COPY official_plugins/ ./official_plugins/
COPY demo/ ./demo/
COPY scripts/ ./scripts/
COPY main.py ./

# 所有使用者資料 / 索引 / session 金鑰都落在這個 volume,重置時清掉它即可。
VOLUME ["/data"]
EXPOSE 8787

# ── OCI image metadata ─────────────────────────────────────────────────────
# ⚠ 刻意放在檔案最後:ARG 一旦出現在 RUN pip install 之前,每次改版本號都會炸掉
#    pip 那一層的 cache,而 arm64 是在 QEMU 模擬下 build 的,重跑一次很貴。
# ⚠ APP_VERSION **不是**版本號的真相來源(真相在 app/config.py:APP_VERSION),
#    這裡只是 image 標籤。.github/workflows/release.yml 會先驗證 git tag、
#    app/config.py、CHANGELOG.md 三者一致,對不上就中止,不會走到這一步。
ARG APP_VERSION=dev
LABEL org.opencontainers.image.title="Jargon Vault" \
      org.opencontainers.image.description="A tiny, fast, self-hosted vault for the jargon, acronyms and cryptic codes you meet at work." \
      org.opencontainers.image.source="https://github.com/diegochen-tw/jargon-vault" \
      org.opencontainers.image.url="https://happenlist.com" \
      org.opencontainers.image.documentation="https://github.com/diegochen-tw/jargon-vault#readme" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${APP_VERSION}"

ENTRYPOINT ["sh", "/app/scripts/docker-entrypoint.sh"]

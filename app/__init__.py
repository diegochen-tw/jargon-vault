"""
Jargon Vault — 多使用者的極速名詞筆記(email+password 或 Google 登入,白名單制;
每位使用者資料完全獨立)。

架構總覽(依賴由上往下,單向):

    routers/   HTTP 介面,薄殼:參數解析 + 呼叫下層
    service    跨層複合操作(寫檔+同步索引)
    search     查詢策略
    sanitize   輸入清理
    storage    .md 檔案讀寫(真相來源)
    trash      回收桶(真相來源 <使用者目錄>/trash/,保留 30 天後自動清掉)
    tags       標籤登記簿讀寫(真相來源,<使用者目錄>/tags.json)
    templates  欄位樣板登記簿讀寫(真相來源,<使用者目錄>/templates.json)
    ai_settings AI 連線設定的讀寫入口(站台唯一一組,真相在 site_settings 的 ai 區塊)
    site_settings 全域站台設定讀寫(真相來源,data/site_settings.json:註冊/白名單/OAuth/AI)
    indexer    SQLite FTS5 索引(可拋棄快取)
    paths      每使用者路徑組合(唯一來源)
    users      全域使用者登記簿讀寫(真相來源,data/users.json)
    auth       session 簽章/密碼雜湊/白名單/get_current_user、get_user_paths dependencies
    migration  一次性舊資料遷移(改多使用者之前的扁平 data/ → 第一個使用者)
    models     Pydantic 請求模型
    config     全域路徑與常數(唯一來源)

詳細說明與擴充指引見 repo 根目錄的 CLAUDE.md。
"""
import logging
import time
import uuid
from logging.handlers import RotatingFileHandler

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .backup import run_auto_backup_if_due
from . import plugin_catalog
from .plugins import ensure_plugins
from .auth import admin_emails, get_current_admin, get_current_user
from . import invites, publish, share_links  # 模組限定名:.trash 已 from-import 了同名的 purge_expired
from .config import APP_LOG_PATH, APP_VERSION, STATIC_DIR, ensure_dirs
from .site_settings import backup_config, ensure_site_settings
from .indexer import rebuild_index
from .migration import migrate_ai_settings_to_site
from .paths import all_existing_user_ids, ensure_user_dirs, user_paths
from .service import migrate_categories_to_groups
from .tags import bootstrap_from_notes
from .templates import ensure_templates
from .trash import purge_expired
from .users import ensure_users, promote_admins_by_email


LOGGER_NAME = "jargon_vault"


def _setup_logging() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        APP_LOG_PATH,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    logger.propagate = False
    return logger


def create_app() -> FastAPI:
    ensure_dirs()
    logger = _setup_logging()
    ensure_users()  # 建立 data/users.json(全域使用者登記簿)
    ensure_site_settings()  # 建立 data/site_settings.json(全域站台設定;首次從 env 種子)

    # ADMIN_EMAILS 的「移轉」語意:啟動時把 env 命中的既有帳號永久升成 store admin。
    # 讓「部署在 admin 機制加入之前、只有 OAuth 帳號、沒有任何 store admin」的既有站台,
    # 能設一次 env + 重啟就把某個既有帳號轉正成 admin(之後拿掉 env 仍是 admin)。
    promoted = promote_admins_by_email(admin_emails())
    if promoted:
        logger.info("promoted to admin via ADMIN_EMAILS: %s", ", ".join(promoted))

    # AI 連線設定從「每使用者一份」收斂成站台唯一一組時,把管理者原本的設定搬進來。
    # ⚠ 必須排在 ensure_site_settings() 與 promote_admins_by_email() 之後:前者保證
    # 檔案存在,後者保證「誰是 admin」已經定案(種子優先挑 admin 的舊設定)。
    migrate_ai_settings_to_site()

    # 外掛封裝型錄:掃 official_plugins/(repo)與 data/plugins/(站台)建快取。
    # ⚠ 必須排在下面的 per-user 迴圈之前——ensure_plugins() 依賴型錄(遷移要查
    # default_config)。壞封裝不會讓掃描失敗(逐目錄收集錯誤),這裡只把錯誤報進 log。
    plugin_catalog.refresh_catalog()
    for err in plugin_catalog.catalog_errors():
        logger.warning("plugin package rejected: %s — %s", err["dir"], err["reason"])

    # 個人/家人規模的使用情境,使用者數量很小:啟動時把每個現有使用者的
    # 資料都跑一次 ensure/rebuild,比每個 request 判斷「是不是第一次見到
    # 這個使用者」的懶惰初始化簡單,也更貼近既有 create_app() 一次做完
    # 全部初始化的風格。
    for uid in all_existing_user_ids():
        paths = user_paths(uid)
        ensure_user_dirs(paths)
        ensure_templates(paths)  # 建立 templates.json 或補回被誤刪的內建樣板
        ensure_plugins(paths)  # 建立 plugins.json;舊的 article-keywords 內建樣板一次性搬成外掛
        migrate_categories_to_groups(paths)  # 舊「分類」一次性轉成標籤+群組(分類已砍掉)
        purge_expired(paths)  # 回收桶裡超過保留天數的名詞,永久刪除
        share_links.purge_expired(paths)  # 過期 48h 的分享連結;另一個清理點在 GET /api/shares
        rebuild_index(paths)  # 索引是可拋棄快取,每次啟動從 .md 檔全量重建
        bootstrap_from_notes(paths)  # 把舊資料裡還沒登記時間戳記的標籤補進 tags.json

    invites.purge_expired()  # 站台註冊邀請:過期/用完的順手清掉(另一個清理點在 GET /api/admin/invites)
    publish.purge_stale_tmp()  # 公開筆記:發佈到一半死掉的 staging 孤兒目錄

    # 自動備份的第一個檢查點。啟動時同步跑(此時還沒有人在用,慢一點沒關係),
    # 之後由 GET /api/auth/me 順便檢查(見 routers/auth.py:api_me)——伺服器可能
    # 連跑好幾個月,只靠啟動檢查等於實際上不會備份,跟回收桶的 purge_expired
    # 同時掛在啟動與請求兩處是同一個理由。
    try:
        if run_auto_backup_if_due(backup_config()):
            logger.info("auto backup created")
    except OSError:
        logger.exception("auto backup failed at startup")  # 備份失敗絕不能擋住啟動

    application = FastAPI(title="Jargon Vault", version=APP_VERSION)

    @application.middleware("http")
    async def request_logging_middleware(request: Request, call_next):
        req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed = int((time.perf_counter() - start) * 1000)
            logger.exception(
                "request failed request_id=%s method=%s path=%s elapsed_ms=%s",
                req_id,
                request.method,
                request.url.path,
                elapsed,
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "伺服器發生錯誤", "request_id": req_id},
            )

        elapsed = int((time.perf_counter() - start) * 1000)
        status = response.status_code
        level = logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO
        logger.log(
            level,
            "request finished request_id=%s method=%s path=%s status=%s elapsed_ms=%s",
            req_id,
            request.method,
            request.url.path,
            status,
            elapsed,
        )
        response.headers["X-Request-ID"] = req_id
        return response

    # /assets 沒有全域掛載——資產是每使用者的,改成 files.py 裡需要登入的
    # 動態路由(GET /assets/{nid}/{filename}),URL 形狀跟以前一模一樣。
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    from .routers import ADMIN_ROUTERS, ALL_ROUTERS, AUTH_ROUTER, PUBLIC_ROUTERS
    application.include_router(AUTH_ROUTER)
    for r in PUBLIC_ROUTERS:
        application.include_router(r)
    for r in ALL_ROUTERS:
        application.include_router(r, dependencies=[Depends(get_current_user)])
    for r in ADMIN_ROUTERS:
        application.include_router(r, dependencies=[Depends(get_current_admin)])
    return application

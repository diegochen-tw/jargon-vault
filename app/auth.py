"""
驗證核心:session cookie 簽章/驗證、密碼雜湊、白名單檢查、FastAPI dependencies。

Session 用簽章 cookie(不是 JWT-in-header)——同源應用,瀏覽器自動帶 cookie,
前端 fetch 呼叫完全不用改。密碼用 bcrypt,session 用 itsdangerous。

環境變數:
    SESSION_SECRET   session 簽章金鑰。沒設就自動產生並落地到 data/.session_secret
                     (視同機器金鑰),讓 session 不會因為沒設環境變數而每次重啟就失效。
    ALLOWED_EMAILS   逗號分隔的白名單 email,只有名單內的人能註冊/用 Google 登入。
"""
import os
import secrets

import bcrypt
from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import atomic, site_settings
from .config import DATA_DIR
from .paths import VaultPaths, user_paths
from .users import find_by_id

SESSION_COOKIE_NAME = "gv_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 天

_SESSION_SECRET_PATH = DATA_DIR / ".session_secret"


def _session_secret() -> str:
    env = os.environ.get("SESSION_SECRET")
    if env:
        return env
    if _SESSION_SECRET_PATH.exists():
        return _SESSION_SECRET_PATH.read_text(encoding="utf-8").strip()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(32)
    # 原子寫入:半截的金鑰檔會讓全站所有 session 一起失效。
    atomic.write_text(_SESSION_SECRET_PATH, secret)
    return secret


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_session_secret(), salt="gv-session")


def create_session_cookie(user_id: str) -> str:
    return _serializer().dumps({"user_id": user_id})


def verify_session_cookie(token: str) -> str | None:
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("user_id")


def create_state_cookie(payload: str) -> str:
    """Google OAuth 的 CSRF state,短效(用同一把金鑰簽,不同 salt)。"""
    return URLSafeTimedSerializer(_session_secret(), salt="gv-oauth-state").dumps(payload)


def verify_state_cookie(token: str, expected: str) -> bool:
    try:
        data = URLSafeTimedSerializer(_session_secret(), salt="gv-oauth-state").loads(token, max_age=600)
    except (BadSignature, SignatureExpired):
        return False
    return data == expected


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def allowed_emails() -> set[str]:
    """白名單:委派給 site_settings(store 名單 ∪ 環境變數 ALLOWED_EMAILS)。"""
    return site_settings.allowed_emails()


def is_whitelisted(email: str) -> bool:
    """是否允許此 email 註冊/首次 Google 登入(依站台的註冊模式判斷)。"""
    return site_settings.is_email_allowed(email)


def admin_emails() -> set[str]:
    """ADMIN_EMAILS 環境變數:即時的 admin 救援名單(不依賴 users.json 的 is_admin)。"""
    raw = os.environ.get("ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_admin(user: dict) -> bool:
    return user.get("is_admin") is True or user.get("email", "").strip().lower() in admin_emails()


def get_current_user(request: Request) -> dict:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user_id = verify_session_cookie(token) if token else None
    user = find_by_id(user_id) if user_id else None
    if not user:
        raise HTTPException(401, "請先登入")
    return user


def get_current_admin(user: dict = Depends(get_current_user)) -> dict:
    if not is_admin(user):
        raise HTTPException(403, "需要管理者權限")
    return user


def get_user_paths(user: dict = Depends(get_current_user)) -> VaultPaths:
    return user_paths(user["id"])


# ── 庫的解析 ────────────────────────────────────────────────────────
#
# 團隊庫拆除(2026-08-19)後只剩私人庫,三支 dependency 全部退化成
# get_user_paths 的別名。**名字刻意保留**:51 個 handler 的 Depends 一行都
# 不用改,而 read/write/admin 三個名字仍然標記著各端點的破壞力等級——
# 將來任何「不只自己的庫」的功能回來時,收斂點就在這裡。


def get_vault_read(user: dict = Depends(get_current_user)) -> VaultPaths:
    """讀取自己的庫。"""
    return user_paths(user["id"])


def get_vault_write(user: dict = Depends(get_current_user)) -> VaultPaths:
    """寫入名詞內容。"""
    return user_paths(user["id"])


def get_vault_admin(user: dict = Depends(get_current_user)) -> VaultPaths:
    """會造成不可逆破壞的操作(改欄位樣板、永久刪除…)。"""
    return user_paths(user["id"])

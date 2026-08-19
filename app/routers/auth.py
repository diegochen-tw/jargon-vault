"""
使用者驗證:email+密碼註冊/登入、Google OAuth、session 管理。

註冊/Google 首次登入都受白名單限制(ALLOWED_EMAILS)。系統裡第一個
成功建立的使用者會自動接收改多使用者之前的舊資料(見 migration.py)。

不做 email 驗證信、不做忘記密碼——專案沒有寄信基礎設施,刻意從簡。
"""
import os
import secrets
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..backup import maybe_run_auto_backup
from ..plugins import ensure_plugins
from .. import invites, ratelimit, service, site_settings
from ..auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    create_session_cookie,
    create_state_cookie,
    get_current_user,
    hash_password,
    is_admin,
    is_whitelisted,
    verify_password,
    verify_state_cookie,
)
from ..config import APP_VERSION, DEMO_SITE_URL, SEED_DEMO_ON_REGISTER
from ..demo import seed_vault
from ..indexer import rebuild_index
from ..migration import migrate_legacy_data_if_needed
from ..models import LangIn, LoginIn, PasswordChangeIn, RegisterIn
from ..paths import ensure_user_dirs, user_paths
from ..service import migrate_categories_to_groups
from ..tags import bootstrap_from_notes
from ..templates import ensure_templates
from ..users import (
    SUPPORTED_LANGS,
    create_user,
    find_by_email,
    find_by_google_sub,
    link_google_sub,
    load_users,
    set_demo_seeded,
    set_lang,
    set_password_hash,
    unlink_google_sub,
)

router = APIRouter()

STATE_COOKIE_NAME = "gv_oauth_state"
# 帳號不存在時拿來跑「假比對」的雜湊,讓失敗路徑的耗時跟真的驗密碼一樣
# (見 api_login 裡的說明)。載入時現算而不是寫死一串字面值:寫死的字串一旦
# 有一個字元不對,verify_password 會走 ValueError 分支**立刻**回 False——
# 那正好把它要消除的時間差原封不動加回來,而且完全不會報錯。
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def _google_redirect_uri(request: Request) -> str:
    """算出送給 Google 的回呼網址。優先用 PUBLIC_BASE_URL env var(部署在
    Cloudflare Tunnel/nginx 反向代理後面時,app 收到的是內部的 plain HTTP
    請求,request.url_for() 猜出來的 scheme/host 常常是錯的,跟 Google
    Console 登記的網址對不上、觸發 redirect_uri_mismatch);本機直接跑
    (無反向代理)才退回用 request.url_for() 動態算。"""
    base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if base:
        return f"{base}/api/auth/google/callback"
    return str(request.url_for("google_callback"))


def _set_session(response, user_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME, create_session_cookie(user_id),
        max_age=SESSION_MAX_AGE, httponly=True, samesite="lax",
    )


def _provision_new_user(*, email: str, password_hash: str | None = None,
                         google_sub: str | None = None) -> dict:
    """白名單檢查通過後的新使用者建立流程:若是系統第一個使用者,先搬舊資料,
    並讓他自動成為 admin(第一位建立者 = 管理者)。"""
    new_id = uuid.uuid4().hex[:12]
    first_user = len(load_users()) == 0
    if first_user:
        migrate_legacy_data_if_needed(new_id)
    user = create_user(id=new_id, email=email, password_hash=password_hash,
                       google_sub=google_sub, is_admin=first_user)
    paths = user_paths(user["id"])
    ensure_user_dirs(paths)
    ensure_templates(paths)
    ensure_plugins(paths)
    # 範例資料:讓開箱不是一片空白。⚠ 位置有三個條件缺一不可——
    #   在 migrate_legacy_data_if_needed() **之後**(那支用 shutil.move 搬整個
    #   notes/ 目錄,先種會靜默擋掉搬遷;seed_vault 的「空庫才種」是第二道保險),
    #   在 ensure_templates() **之後**(要打開範例用到的樣板),
    #   在 rebuild_index() **之前**(索引看不到就得等下次重啟才搜得到)。
    if SEED_DEMO_ON_REGISTER and seed_vault(paths):
        set_demo_seeded(user["id"], True)
    # 全新使用者(或剛接收完舊資料遷移)都要立刻把索引建起來,不能等下次伺服器重啟——
    # 沒有這一步,這個使用者的 index.db 會缺 notes/fts 資料表,首次搜尋直接 500。
    migrate_categories_to_groups(paths)
    rebuild_index(paths)
    bootstrap_from_notes(paths)
    return user


@router.post("/api/auth/register")
def api_register(body: RegisterIn):
    email = body.email.strip().lower()
    password = body.password
    if not email or "@" not in email:
        raise HTTPException(400, "email 格式不正確")
    if len(password) < 8:
        raise HTTPException(400, "密碼至少需要 8 個字元")
    # 系統裡還沒有任何使用者時,這一位注定成為第一個 admin(見 _provision_new_user
    # 的 first_user 判斷)——不管站台的註冊模式/白名單設什麼都直接放行,讓完全沒碰過
    # 環境變數/PowerShell 的使用者,裝好後也能直接在瀏覽器完成「第一次註冊」。
    # 一旦有了第一個使用者,這個分支就不會再走到,後續註冊照舊受註冊模式/白名單限制。
    first_user = len(load_users()) == 0
    # 站台邀請:持有有效連結就**繞過註冊模式與白名單**。不繞過的話 admin 要同時
    # 做兩件事(填白名單 + 發連結),邀請連結想解決的漏斗又長回來(見 app/invites.py)。
    # ⚠ 這裡只 peek 不 consume——註冊還可能失敗(email 重複、密碼太短),
    # 先扣掉次數會讓一次性連結白白燒掉。真正的 consume 在建好帳號之後。
    invited = invites.peek(body.invite) is not None if body.invite else False
    if not first_user and not invited:
        if site_settings.registration_mode() == "closed":
            raise HTTPException(403, "目前未開放註冊")
        if not is_whitelisted(email):
            raise HTTPException(403, "此 email 未獲授權使用本系統")
    if find_by_email(email):
        raise HTTPException(400, "此 email 已經註冊過")

    user = _provision_new_user(email=email, password_hash=hash_password(password))
    if invited:
        invites.consume(body.invite)
    resp = JSONResponse({"id": user["id"], "email": user["email"],
                         "is_admin": is_admin(user)})
    _set_session(resp, user["id"])
    return resp


def _rl_identity(request: Request, email: str) -> tuple[str, str, dict]:
    """(計數用的 IP, 正規化 email, 目前的速率限制設定)。

    email 的正規化方式必須跟 `users.find_by_email()` 一模一樣(strip + lower)。
    不一致的話 `A@B.c` 與 `a@b.c` 會落在兩個不同的計數器上,但查到的是同一個
    帳號——攻擊者只要輪流變換大小寫,per-email 的門檻就被放大好幾倍。
    """
    cfg = site_settings.rate_limit_config()
    ip = ratelimit.client_ip(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for"),
        trust_proxy=cfg["trust_forwarded_for"],
    )
    return ip, email.strip().lower(), cfg


@router.post("/api/auth/login")
def api_login(body: LoginIn, request: Request):
    """登入。**先查鎖定、再驗密碼**——順序反過來的話,被鎖的攻擊者照樣每次都能
    讓伺服器跑一輪 bcrypt(那是刻意設計成慢的運算),登入端點本身就變成 CPU
    耗盡的施力點。"""
    ip, email, cfg = _rl_identity(request, body.email)
    retry_after = ratelimit.check(ip, email, cfg)
    if retry_after:
        # 429 帶 Retry-After,讓前端說得出「還要等多久」而不是只能說「錯誤」。
        raise HTTPException(
            429, f"嘗試次數過多,請於 {max(1, retry_after // 60)} 分鐘後再試",
            headers={"Retry-After": str(retry_after)},
        )

    user = find_by_email(email)
    if not user or not user.get("password_hash") or not verify_password(body.password, user["password_hash"]):
        # ⚠ 帳號不存在時也要跑一次假的雜湊比對。少了這一步,「查無此人」會比
        # 「密碼錯誤」快上一個數量級(bcrypt 本來就慢),回應時間本身就洩漏了
        # 這個 email 有沒有註冊——攻擊者能先免費列舉出有效帳號再集中火力。
        if not user or not user.get("password_hash"):
            verify_password(body.password, DUMMY_PASSWORD_HASH)
        ratelimit.record_failure(ip, email, cfg)
        raise HTTPException(401, "帳號或密碼錯誤")

    ratelimit.record_success(ip, email)
    resp = JSONResponse({"id": user["id"], "email": user["email"]})
    _set_session(resp, user["id"])
    return resp


@router.post("/api/auth/logout")
def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE_NAME)
    return resp


@router.get("/api/auth/me")
def api_me(user: dict = Depends(get_current_user)):
    """boot 的第一支請求。順帶回兩個站台開關與站台版本號,讓前端不必為了「共用庫/
    公開分享有沒有開」「現在是哪一版」再多打設定 API(比照 is_admin 決定要不要顯示
    管理分頁的做法)。

    也順便當自動備份的定期檢查點:伺服器可能連跑好幾個月,只在啟動時檢查等於
    實際上不會備份。這裡只做一次很便宜的時間比較,真的要備份才會開執行緒——
    絕大多數請求連執行緒都不會開,而且**永遠不會擋住這支回應**
    (見 backup.maybe_run_auto_backup 的說明)。
    """
    maybe_run_auto_backup(site_settings.backup_config())
    return {
        "id": user["id"], "email": user["email"], "is_admin": is_admin(user),
        "public_share_enabled": site_settings.public_share_enabled(),
        "public_notebook_enabled": site_settings.public_notebook_enabled(),
        # 站台版本號:比照上面兩個站台開關搭這班車捎回來(設定 → 關於本專案 顯示用)。
        # 真相在 app/config.py:APP_VERSION,這裡只是轉發。
        "version": APP_VERSION,
        # 登入方式現況(設定 → 帳號 顯示用),同樣搭 /me 這班車,不另開設定 API。
        # 只回有/沒有,絕不回雜湊本身。
        "has_password": bool(user.get("password_hash")),
        "has_google": bool(user.get("google_sub")),
        # 介面語言跟著帳號走(缺 key = 未設定 = 跟隨裝置)。同一台裝置換帳號登入
        # 時,語言不該被上一個帳號留在 localStorage 的值汙染——真相在這裡,
        # 前端 boot 拿到後與本機快取對帳(見 app.js:boot)。
        "lang": user.get("lang"),
        # 範例資料的置頂行:註冊時種過、且使用者還沒按下刪除,就顯示。
        # 一個旗標同時代表「範例還在」與「橫幅要顯示」,兩者不會分歧
        # (見 users.set_demo_seeded)。網址一併捎回,前端不寫死站台網址。
        "demo_banner": bool(user.get("demo_seeded")),
        "demo_site_url": DEMO_SITE_URL,
    }


@router.put("/api/auth/lang")
def api_set_lang(body: LangIn, user: dict = Depends(get_current_user)):
    """介面語言跟著帳號走(設定 → 偏好設定 → 介面語言)。真相存 users.json 的
    lang 欄位、/api/auth/me 帶回;lang=null = 清除(回到跟隨裝置語言)。"""
    if body.lang is not None and body.lang not in SUPPORTED_LANGS:
        raise HTTPException(400, "不支援的語言代碼")
    set_lang(user["id"], body.lang)
    return {"ok": True, "lang": body.lang}


@router.get("/api/auth/config")
def api_auth_config():
    """公開端點:登入畫面用來決定是否顯示「註冊」切換與 Google 登入按鈕。"""
    return {
        "registration_open": site_settings.registration_mode() != "closed",
        "google_enabled": site_settings.google_enabled(),
    }


@router.put("/api/auth/password")
def api_change_password(body: PasswordChangeIn, request: Request,
                        user: dict = Depends(get_current_user)):
    """變更(或首次設定)email 登入的密碼。設定 → 帳號 的入口。

    - 帳號已有密碼 → current_password 必填且必須驗過:session cookie 可能在
      共用電腦上被留下,改密碼是奪回帳號的動作,不能只憑 session 就放行。
    - Google-only 帳號(password_hash 為 null)→ 沒有舊密碼可驗,直接設定,
      等於「啟用 email 登入」這種登入方式。
    - 驗舊密碼走與登入相同的速率限制帳本(per-IP + per-email):它跟登入猜的
      是同一組憑證,不共用帳本的話這支端點就是繞過登入鎖定的第二個猜密碼入口。
    """
    if len(body.new_password) < 8:
        raise HTTPException(400, "密碼至少需要 8 個字元")
    if user.get("password_hash"):
        ip, email, cfg = _rl_identity(request, user["email"])
        retry_after = ratelimit.check(ip, email, cfg)
        if retry_after:
            raise HTTPException(
                429, f"嘗試次數過多,請於 {max(1, retry_after // 60)} 分鐘後再試",
                headers={"Retry-After": str(retry_after)},
            )
        if not verify_password(body.current_password, user["password_hash"]):
            ratelimit.record_failure(ip, email, cfg)
            raise HTTPException(403, "目前密碼不正確")
        ratelimit.record_success(ip, email)
    set_password_hash(user["id"], hash_password(body.new_password))
    return {"ok": True, "has_password": True}


@router.delete("/api/auth/password")
def api_remove_password(user: dict = Depends(get_current_user)):
    """停用 email 密碼登入(password_hash 清成 null)。**必須還留著 Google 登入**,
    否則這個帳號從此誰都進不來——「不能移除最後一種登入方式」跟 admin 那邊
    「不能刪最後一個 admin」是同一類防呆。"""
    if not user.get("google_sub"):
        raise HTTPException(400, "這是帳號唯一的登入方式,不能停用")
    set_password_hash(user["id"], None)
    return {"ok": True, "has_password": False}


@router.delete("/api/auth/google")
def api_unlink_google(user: dict = Depends(get_current_user)):
    """解除 Google 登入連結。防呆同上:必須還留著密碼登入。
    (反向操作「連結 Google」不需要專屬端點——登入狀態下走一次
    /api/auth/google/login,callback 依 email 比對就會自動 link_google_sub。)"""
    if not user.get("password_hash"):
        raise HTTPException(400, "這是帳號唯一的登入方式,不能解除連結")
    unlink_google_sub(user["id"])
    return {"ok": True, "has_google": False}


@router.get("/api/auth/google/login")
def google_login(request: Request):
    client_id = site_settings.google_oauth_config().get("client_id", "")
    if not client_id:
        raise HTTPException(500, "伺服器未設定 GOOGLE_CLIENT_ID")
    redirect_uri = _google_redirect_uri(request)
    state = uuid.uuid4().hex
    query = str(httpx.QueryParams({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }))
    resp = RedirectResponse(f"{GOOGLE_AUTH_URL}?{query}")
    resp.set_cookie(STATE_COOKIE_NAME, create_state_cookie(state),
                     max_age=600, httponly=True, samesite="lax")
    return resp


@router.get("/api/auth/google/callback", name="google_callback")
async def google_callback(request: Request, code: str = "", state: str = ""):
    _oauth = site_settings.google_oauth_config()
    client_id = _oauth.get("client_id", "")
    client_secret = _oauth.get("client_secret", "")
    if not client_id or not client_secret:
        raise HTTPException(500, "伺服器未設定 GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET")

    state_cookie = request.cookies.get(STATE_COOKIE_NAME, "")
    if not code or not state or not verify_state_cookie(state_cookie, state):
        return RedirectResponse("/?error=oauth_state")

    redirect_uri = _google_redirect_uri(request)
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            token_r = await client.post(GOOGLE_TOKEN_URL, data={
                "code": code, "client_id": client_id, "client_secret": client_secret,
                "redirect_uri": redirect_uri, "grant_type": "authorization_code",
            })
            token_r.raise_for_status()
            access_token = token_r.json()["access_token"]
            info_r = await client.get(GOOGLE_USERINFO_URL,
                                       headers={"Authorization": f"Bearer {access_token}"})
            info_r.raise_for_status()
            info = info_r.json()
        except httpx.HTTPError:
            return RedirectResponse("/?error=oauth_failed")

    sub = str(info.get("id") or "")
    email = str(info.get("email") or "").strip().lower()
    if not sub or not email:
        return RedirectResponse("/?error=oauth_failed")

    user = find_by_google_sub(sub)
    if not user:
        user = find_by_email(email)
        if user:
            link_google_sub(user["id"], sub)
        else:
            # 比照 api_register:系統裡還沒有任何使用者時,這一位注定成為第一個
            # admin,不受白名單限制(見那裡的完整說明)。
            first_user = len(load_users()) == 0
            if not first_user and not is_whitelisted(email):
                return RedirectResponse("/?error=not_whitelisted")
            user = _provision_new_user(email=email, google_sub=sub)

    resp = RedirectResponse("/")
    resp.delete_cookie(STATE_COOKIE_NAME)
    _set_session(resp, user["id"])
    return resp

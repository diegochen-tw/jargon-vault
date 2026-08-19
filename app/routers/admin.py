"""
管理者專用 API(/api/admin/*)。

整包 router 在 app/__init__.py 掛載時套 dependencies=[Depends(get_current_admin)],
所以這裡每個 handler 都已保證呼叫者是 admin,不用重複檢查。

管理範圍:站台設定(註冊模式 / email 白名單 / Google OAuth / 公開分享總開關 /
註冊邀請連結)與使用者清單(檢視、升降 admin、刪除連同其資料)。站台設定
的真相在 app/site_settings.py,使用者登記簿的真相在 app/users.py;刪使用者的
跨層操作走 app/service.py。

安全約定:
  - 絕不回傳 Google client_secret 明文,只回 has_secret 布林。
  - 不能把最後一個 admin 降級或刪除(避免鎖死沒有人能管理)。
  - 含 {user_id} 的路由先 valid_id()。
"""
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import backup, invites, plugin_catalog, publish, ratelimit, site_settings
from ..auth import get_current_admin
from ..config import valid_id
from ..indexer import rebuild_index
from ..models import (
    AdminFlagIn, BackupSettingsIn, InviteIn, OAuthConfigIn, RateLimitIn,
    RegistrationModeIn, SharingFlagsIn, WhitelistIn,
)
from ..paths import all_existing_user_ids, ensure_user_dirs, user_paths
from ..service import delete_user_and_data
from ..users import count_admins, find_by_id, load_users, set_user_admin

router = APIRouter(prefix="/api/admin")


def _public_settings() -> dict:
    """回給前端的站台設定(遮蔽 secret)。

    ⚠ 這是**白名單**不是黑名單:新增站台設定區塊時要自己加進來,漏加只會少顯示
    一個欄位(安全的失敗方向)。`ai` 區塊刻意不在這裡——它有自己的一組端點
    (GET/PUT /api/ai/settings),而且裡面有 api_key,那支端點會另外遮掉。
    """
    s = site_settings.load_site_settings()
    g = s["google_oauth"]
    return {
        "registration_mode": s["registration_mode"],
        "allowed_emails": s["allowed_emails"],
        "google_oauth": {
            "enabled": g.get("enabled", False),
            "client_id": g.get("client_id", ""),
            "has_secret": bool(g.get("client_secret")),
        },
        "public_share_enabled": s["public_share_enabled"],
        "public_notebook_enabled": s["public_notebook_enabled"],
        "rate_limit": s["rate_limit"],
        "backup": s["backup"],
    }


def _auth_kind(u: dict) -> str:
    has_pw = bool(u.get("password_hash"))
    has_google = bool(u.get("google_sub"))
    if has_pw and has_google:
        return "both"
    if has_google:
        return "google"
    return "password"


@router.get("/settings")
def get_settings():
    return _public_settings()


@router.put("/settings/registration")
def set_registration(body: RegistrationModeIn):
    if body.mode not in site_settings.REGISTRATION_MODES:
        raise HTTPException(400, "不合法的註冊模式")
    s = site_settings.load_site_settings()
    s["registration_mode"] = body.mode
    site_settings.save_site_settings(s)
    return _public_settings()


@router.put("/settings/whitelist")
def set_whitelist(body: WhitelistIn):
    s = site_settings.load_site_settings()
    s["allowed_emails"] = [e for e in body.emails]  # save_site_settings 會正規化小寫去重
    site_settings.save_site_settings(s)
    return _public_settings()


@router.put("/settings/oauth")
def set_oauth(body: OAuthConfigIn):
    s = site_settings.load_site_settings()
    g = s["google_oauth"]
    g["enabled"] = bool(body.enabled)
    g["client_id"] = body.client_id.strip()
    # 空字串 = 沿用既有 secret(讓 UI 不必回傳 secret 也能改其他欄位)
    if body.client_secret.strip():
        g["client_secret"] = body.client_secret.strip()
    s["google_oauth"] = g
    site_settings.save_site_settings(s)
    return _public_settings()


@router.put("/settings/sharing")
def set_sharing_flags(body: SharingFlagsIn):
    """公開分享連結與公開筆記的站台總開關。關掉會讓**所有既有連結/快照立刻失效**
    (不是只擋新建),但登記簿/快照保留,重新開啟即恢復。"""
    s = site_settings.load_site_settings()
    s["public_share_enabled"] = bool(body.public_share_enabled)
    s["public_notebook_enabled"] = bool(body.public_notebook_enabled)
    site_settings.save_site_settings(s)
    return _public_settings()


@router.put("/settings/ratelimit")
def set_rate_limit(body: RateLimitIn):
    """登入失敗鎖定的門檻。存檔後順手清空計數器——調寬門檻卻還被舊帳鎖著,
    是這種設定最典型的「我明明改了為什麼沒用」;而放寬本來就意味著解鎖。"""
    s = site_settings.load_site_settings()
    s["rate_limit"] = body.model_dump()  # save_site_settings 會夾住上下界
    site_settings.save_site_settings(s)
    ratelimit.reset()
    return _public_settings()


@router.post("/ratelimit/reset")
def reset_rate_limit():
    """立刻解開所有鎖定。管理者把自己或家人鎖在門外時的救援按鈕——
    沒有這個,唯一的辦法是等鎖定時間過去或重啟伺服器。"""
    ratelimit.reset()
    return {"ok": True, **ratelimit.snapshot()}


# ── 備份與還原 ──────────────────────────────────────────────────────────────
# 這一組是**整站**的(所有使用者的資料 + 全域帳號與站台設定),所以整個放在
# admin router 底下。與 routers/transfer.py 的匯出入是完全不同的東西,兩邊的
# 取捨方向相反,理由見 app/backup.py 檔頭那張對照表。

@router.get("/backups")
def list_backups():
    cfg = site_settings.backup_config()
    return {
        "backups": backup.list_backups(),
        "settings": cfg,
        "last_auto": backup.last_auto_run(),
        "due": backup.auto_backup_due(cfg),
    }


@router.put("/settings/backup")
def set_backup_settings(body: BackupSettingsIn):
    s = site_settings.load_site_settings()
    s["backup"] = body.model_dump()  # save_site_settings 會夾住上下界
    site_settings.save_site_settings(s)
    return _public_settings()


@router.post("/backups")
def create_backup_now():
    """手動備份。**同步跑**(不像自動備份丟背景執行緒):使用者按了按鈕就是在等
    結果,背景跑會讓他不知道好了沒、也拿不到失敗訊息。"""
    try:
        path = backup.create_backup(backup.KIND_MANUAL)
    except OSError as e:
        raise HTTPException(500, f"備份失敗:{e}")
    return {"name": path.name, "size": path.stat().st_size}


def _require_backup(name: str) -> Path:
    """檔名先過白名單正規表示式再組路徑。`name` 來自網址,是不可信輸入——
    少了這關,`../../users.json` 就能被下載或刪除(同 config.valid_id 的用意)。"""
    p = backup.backup_path(name)
    if not p:
        raise HTTPException(404, "找不到這個備份檔")
    return p


@router.get("/backups/{name}/download")
def download_backup(name: str):
    return FileResponse(_require_backup(name), media_type="application/zip", filename=name)


@router.delete("/backups/{name}")
def delete_backup(name: str):
    _require_backup(name)
    backup.delete_backup(name)
    return {"ok": True}


@router.get("/backups/{name}/inspect")
def inspect_backup(name: str):
    """還原前先看看這包裡有什麼(幾個使用者、含不含站台設定)。
    UI 拿它做二次確認,避免「按下去才發現還原錯了一包」。"""
    try:
        return backup.inspect_backup(_require_backup(name))
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e))


def _reindex_all() -> int:
    """還原後重建所有使用者的索引。index.db 刻意不進備份(可拋棄快取),
    所以還原完每個人的索引都不存在,不重建的話第一次搜尋就 500。"""
    n = 0
    for uid in all_existing_user_ids():
        paths = user_paths(uid)
        ensure_user_dirs(paths)
        rebuild_index(paths)
        n += 1
    return n


@router.post("/backups/{name}/restore")
def restore_backup(name: str):
    """整包還原。回傳裡的 snapshot 是「還原前的自動快照」,按錯了拿它還原回去。"""
    path = _require_backup(name)
    try:
        result = backup.restore_backup(path)
    except (ValueError, OSError) as e:
        raise HTTPException(400, f"還原失敗:{e}")
    result["reindexed"] = _reindex_all()
    # 還原的 users.json 可能沒有目前這個 admin,或 .session_secret 換了 →
    # 現有 session 全部失效。前端據此把使用者踢回登入畫面。
    return result


@router.post("/backups/upload")
async def upload_backup(file: UploadFile = File(...), restore: int = 0):
    """上傳一包 ZIP。restore=1 才順便還原,否則只是存進備份清單。"""
    content = await file.read()
    try:
        path = backup.save_uploaded(content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not restore:
        return {"name": path.name, "size": path.stat().st_size, "restored": False}
    try:
        result = backup.restore_backup(path)
    except (ValueError, OSError) as e:
        raise HTTPException(400, f"還原失敗:{e}")
    result["reindexed"] = _reindex_all()
    result["name"] = path.name
    result["restored"] = True
    return result


@router.get("/users")
def list_users():
    out = []
    for u in load_users():
        out.append({
            "id": u["id"],
            "email": u["email"],
            "is_admin": u.get("is_admin") is True,
            "created": u.get("created", 0),
            "auth": _auth_kind(u),
        })
    out.sort(key=lambda x: x["created"])
    return {"users": out}


@router.put("/users/{user_id}/admin")
def update_user_admin(user_id: str, body: AdminFlagIn):
    if not valid_id(user_id):
        raise HTTPException(400, "不合法的 ID")
    user = find_by_id(user_id)
    if not user:
        raise HTTPException(404, "找不到這個使用者")
    # 防呆:不能把最後一個 admin 降級
    if not body.is_admin and user.get("is_admin") is True and count_admins() <= 1:
        raise HTTPException(400, "不能移除最後一個管理者")
    set_user_admin(user_id, body.is_admin)
    return {"id": user_id, "is_admin": body.is_admin}


@router.delete("/users/{user_id}")
def delete_user(user_id: str):
    if not valid_id(user_id):
        raise HTTPException(400, "不合法的 ID")
    user = find_by_id(user_id)
    if not user:
        raise HTTPException(404, "找不到這個使用者")
    # 防呆:不能刪掉最後一個 admin
    if user.get("is_admin") is True and count_admins() <= 1:
        raise HTTPException(400, "不能刪除最後一個管理者")
    delete_user_and_data(user_id)
    return {"ok": True}


# ── 站台註冊邀請連結 ────────────────────────────────────────────────
#
# 產生/列出/撤銷收站台 admin:邀請會**繞過註冊模式與 email 白名單**,
# 本質是註冊控制,權限收在管註冊的人手上(見 app/invites.py 檔頭)。
# **預覽**(還沒有帳號的人看「這條連結還有效嗎」)在 routers/invite.py,
# 那是 PUBLIC_ROUTERS 裡的東西,完全沒有身分。


@router.get("/invites")
def admin_list_invites():
    invites.purge_expired()   # 列表時順手清過期的,不需要排程器
    rows = [{"nonce": n, "url": f"/invite/{n}", **m}
            for n, m in invites.load_invites().items()]
    return {"invites": sorted(rows, key=lambda r: -r["created"])}


@router.post("/invites")
def admin_create_invite(body: InviteIn, user: dict = Depends(get_current_admin)):
    nonce = invites.create_invite(created_by=user["id"],
                                  uses=body.uses, ttl_days=body.ttl_days)
    # url 回**相對路徑**,絕對網址由前端拼 location.origin(同分享連結的取捨:
    # 後端不能假設 PUBLIC_BASE_URL 一定有設,硬猜會在反向代理後面算錯 scheme/host)。
    return {"nonce": nonce, "url": f"/invite/{nonce}",
            **invites.load_invites()[nonce]}


@router.delete("/invites/{nonce}")
def admin_revoke_invite(nonce: str):
    """撤銷。舊網址立刻失效——這是「連結外流」唯一的補救手段,所以它必須真的即時。"""
    if not invites.revoke_invite(nonce):
        raise HTTPException(404, "找不到這條邀請")
    return {"ok": True}


# ── 公開筆記快照(data/published/)────────────────────────────────
#
# admin 可列出/刪除**任何**發佈:快照存在使用者目錄之外、帳號刪除後仍在
# (那是刻意的,見 app/publish.py),孤兒快照只有 admin 清得掉。
# 這**不**引入內容讀取權的問題——快照本來就是公開的東西。


@router.get("/published")
def admin_list_published():
    """全站快照清單。orphan = 擁有者帳號已不存在(owner_label 是凍結值照樣顯示)。"""
    rows = []
    for m in publish.list_all():
        rows.append({**m, "orphan": find_by_id(m.get("owner_id", "")) is None})
    return {"publications": rows}


@router.delete("/published/{pid}")
def admin_delete_published(pid: str):
    if not publish.manifest_of(pid):
        raise HTTPException(404, "找不到這份公開筆記")
    publish.revoke(pid)
    return {"ok": True}


# ── 站台外掛封裝(data/plugins/):上傳與移除 ──────────────────────
# 驗證/落地/移除的邏輯全在 app/plugin_catalog.py,這裡只是薄殼。
# zip slip、大小上限、id 衝突官方贏、Windows 換包順序,防線都寫在那一層原地。

@router.post("/plugins/upload")
async def upload_plugin_package(file: UploadFile = File(...)):
    """上傳一個外掛封裝 zip 到站台目錄。驗證全過才落地,壞封裝 400 附人話理由。"""
    from ..config import MAX_UPLOAD_BYTES

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"檔案超過 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 上限")
    try:
        entry = plugin_catalog.install_site_package(content)
    except plugin_catalog.PluginPackageError as e:
        raise HTTPException(400, str(e))
    return {"id": entry["id"], "version": entry["version"], "category": entry["category"],
            "source": entry["source"]}


@router.delete("/plugins/{pid}")
def delete_plugin_package(pid: str):
    """移除站台封裝(官方封裝 400)。只刪封裝檔,不動任何人的 plugins.json——
    使用者的安裝狀態與設定由 load_plugins 的保留政策留著,重新上傳即復活。"""
    try:
        plugin_catalog.remove_site_package(pid)
    except plugin_catalog.PluginPackageError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}

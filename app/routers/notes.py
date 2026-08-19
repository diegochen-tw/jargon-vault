"""名詞本體的 CRUD 與版本回復,外加單筆的公開分享連結管理。

分享連結的**建立/查詢/撤銷**放在這裡(擁有者視角,需要登入);拿著連結
實際開啟內容的那三支公開端點在 routers/share.py。
"""
import copy
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException

from .. import share_links, site_settings
from ..auth import get_user_paths, get_vault_admin, get_vault_read, get_vault_write
from ..config import (
    DEFAULT_TEMPLATE_ID, HISTORY_LIMIT, SHARE_LINK_TTL_HOURS, UNNAMED_TAG,
    UNTITLED_RE, valid_id,
)
from ..models import MarkIn, NoteIn, RestoreIn
from ..paths import VaultPaths
from ..sanitize import clean_attachments, clean_fields, clean_tags
from ..service import (
    delete_all_notes, delete_note, delete_notes_in_group, load_note_or_404,
    next_untitled_name, persist_note, save_progress_entry,
)
from ..storage import note_path, snapshot_of

router = APIRouter()


@router.post("/api/notes")
def api_create(body: NoteIn, paths: VaultPaths = Depends(get_vault_write)):
    nid = body.id.strip()
    if nid:
        if not valid_id(nid):
            raise HTTPException(400, "不合法的 ID")
        if note_path(paths, nid).exists():
            raise HTTPException(400, "此 ID 已存在")
    else:
        nid = uuid.uuid4().hex[:12]
    # 名詞留空 → 自動給流水號佔位名稱,並掛上「未輸入」標籤供事後補名
    name = body.name.strip()
    tags = body.tags
    if not name:
        name = next_untitled_name(paths)
        tags = tags + [UNNAMED_TAG]
    now = time.time()
    # 樣板 id 不驗證存在性:樣板可能事後被刪,引用它的名詞仍要能存
    note = {
        "id": nid, "name": name,
        "description": body.description,
        "template": body.template.strip() or DEFAULT_TEMPLATE_ID,
        "fields": clean_fields(body.fields),
        "tags": clean_tags(tags),
        "attachments": clean_attachments(body.attachments),
        "marked": False,  # 書籤標記由 PUT /api/notes/{nid}/mark 另外切換,建立時一律未標記
        # SRS:None = 從沒複習過。不寫進 .md(見 storage.dump_note),讀回時
        # srs_due 退回 updated,新名詞因此天然排在複習佇列末端——你才剛寫完它。
        "srs_box": None, "srs_due": None,
        "created": now, "updated": now, "history": [],
    }
    persist_note(paths, note)
    return note


def _check_not_stale(old: dict, base_updated: float | None) -> None:
    """樂觀鎖:呼叫端讀到這筆時的 updated 必須還是磁碟上的現值。

    沒有這一關的話,`load → mutate → persist` 中間另一個分頁存過的內容會被
    整包蓋掉,而且**完全沒有訊號**——寫的人以為存好了,先前的段落就消失了。

    base_updated 為 None = 呼叫端沒帶(MCP server / 舊前端),跳過檢查以維持相容。
    比對留 1 微秒容差:updated 一路是 float64(YAML ↔ JSON ↔ JS)本該精確往返,
    容差純粹是防禦——萬一哪一段格式化掉了精度,寧可漏擋極短時間內的衝突,
    也不要讓每一次存檔都 409 而全站存不了東西。
    """
    if base_updated is None:
        return
    if abs(float(old.get("updated", 0)) - float(base_updated)) > 1e-6:
        raise HTTPException(409, "這筆名詞已被其他人或另一個分頁修改過")


@router.put("/api/notes/{nid}")
def api_update(nid: str, body: NoteIn, paths: VaultPaths = Depends(get_vault_write)):
    old = load_note_or_404(paths, nid)
    _check_not_stale(old, body.base_updated)
    history = ([snapshot_of(old)] + old.get("history", []))[:HISTORY_LIMIT]
    new_name = body.name.strip() or old["name"]
    tags = clean_tags(body.tags)
    # 佔位名稱被補上真名 → 自動摘掉「未輸入」標籤
    if UNTITLED_RE.match(old["name"]) and not UNTITLED_RE.match(new_name):
        tags = [t for t in tags if t != UNNAMED_TAG]
    old.update(
        name=new_name,
        description=body.description,
        template=body.template.strip() or DEFAULT_TEMPLATE_ID,
        fields=clean_fields(body.fields),
        tags=tags,
        attachments=clean_attachments(body.attachments),
        updated=time.time(),
        history=history,
    )
    persist_note(paths, old)
    return old


@router.get("/api/notes/{nid}")
def api_get(nid: str, paths: VaultPaths = Depends(get_vault_read)):
    return load_note_or_404(paths, nid)


@router.post("/api/notes/{nid}/restore")
def api_restore(nid: str, body: RestoreIn, paths: VaultPaths = Depends(get_vault_write)):
    old = load_note_or_404(paths, nid)
    _check_not_stale(old, body.base_updated)
    history = old.get("history", [])
    if body.index < 0 or body.index >= len(history):
        raise HTTPException(400, "版本不存在")
    target = history[body.index]
    new_history = ([snapshot_of(old)] + history)[:HISTORY_LIMIT]
    old.update(
        name=target["name"], template=target["template"], fields=dict(target["fields"]),
        tags=list(target["tags"]),
        attachments=copy.deepcopy(target["attachments"]), description=target["description"],
        updated=time.time(), history=new_history,
    )
    persist_note(paths, old)
    return old


@router.put("/api/notes/{nid}/mark")
def api_set_mark(nid: str, body: MarkIn, paths: VaultPaths = Depends(get_vault_read)):
    """切換書籤標記。刻意不寫歷史版本、不動 updated——書籤不是內容編輯,
    讓它 bump updated 會把「依上次編輯時間」的列表排序整個打亂。

    書籤存在**個人側**的 progress.json(見 app/progress.py),所以這裡連 .md
    都不碰。權限走 get_vault_read(不是 write):理由與孿生的 srs.py:api_srs_review
    一模一樣——書籤是「我跟名詞的關係」,不是對內容的修改。
    """
    old = load_note_or_404(paths, nid)  # 順帶擋掉不合法 id 與不存在的名詞
    save_progress_entry(paths, nid, marked=body.marked)
    old["marked"] = body.marked
    return old


def _share_payload(paths: VaultPaths, nid: str) -> dict:
    nonce = share_links.find_by_note(paths, nid)
    token = f"{paths.vault_id}.{nonce}" if nonce else None
    # url 回**相對路徑**,絕對網址由前端拼 location.origin。後端不能假設
    # PUBLIC_BASE_URL 一定有設,硬猜會在反向代理後面算錯 scheme/host
    # (Google OAuth 的 redirect_uri 已經踩過這個坑)。
    return {
        "enabled": site_settings.public_share_enabled(),
        "token": token,
        "url": f"/s/{token}" if token else None,
        # 前端的「最多保留 N 小時」提示用這個值,不在 i18n 裡寫死 48
        "ttl_hours": SHARE_LINK_TTL_HOURS,
    }


@router.get("/api/notes/{nid}/share")
def api_get_share(nid: str, paths: VaultPaths = Depends(get_vault_read)):
    """查目前的分享狀態。站台開關關閉時仍照實回報既有 token——使用者至少要看得到
    自己曾經分享過什麼、並且還能撤銷它。"""
    load_note_or_404(paths, nid)
    return _share_payload(paths, nid)


@router.post("/api/notes/{nid}/share")
def api_create_share(nid: str, paths: VaultPaths = Depends(get_vault_write)):
    """建立(或重新產生)分享連結。重新產生會讓舊連結立刻失效,見 share_links.create_share。"""
    if not site_settings.public_share_enabled():
        raise HTTPException(403, "站台未啟用公開分享連結")
    load_note_or_404(paths, nid)  # 順帶擋掉不合法 id 與不存在的名詞
    share_links.create_share(paths, nid)
    return _share_payload(paths, nid)


@router.delete("/api/notes/{nid}/share")
def api_revoke_share(nid: str, paths: VaultPaths = Depends(get_vault_write)):
    """撤銷分享連結。刻意**不**檢查站台開關:開關關掉之後使用者仍該能清掉
    自己留下的連結,擋住這件事沒有任何好處。"""
    if not valid_id(nid):
        raise HTTPException(400, "不合法的 ID")
    share_links.revoke_share(paths, nid)
    return _share_payload(paths, nid)


@router.get("/api/shares")
def api_share_stats(paths: VaultPaths = Depends(get_user_paths)):
    """全部分享連結的統計(設定 → 整理與清理 的撤銷卡用)。

    順手 purge 過期登記——這裡與啟動迴圈是 share 版「掛兩處」的清理點;刻意不掛在
    GET /api/notes/{nid}/share(讀取路徑上不寫檔),過濾靠 find_by_note 的 _alive 即可。"""
    share_links.purge_expired(paths)
    return {
        "count": share_links.count_active(paths),
        "ttl_hours": SHARE_LINK_TTL_HOURS,
    }


@router.delete("/api/shares")
def api_revoke_all_shares(paths: VaultPaths = Depends(get_user_paths)):
    """一鍵撤銷自己的全部分享連結。比照單筆撤銷,刻意不檢查站台開關。"""
    return {"revoked": share_links.revoke_all(paths)}


@router.delete("/api/notes/{nid}")
def api_delete(nid: str, paths: VaultPaths = Depends(get_vault_write)):
    if not valid_id(nid):
        raise HTTPException(400, "不合法的 ID")
    delete_note(paths, nid)
    return {"ok": True}


@router.delete("/api/notes")
def api_delete_bulk(group: str = "", paths: VaultPaths = Depends(get_vault_admin)):
    """批次刪除名詞:不帶 group 刪全部;帶 group 只刪掛有該群組任一標籤的名詞。

    永久刪除(含圖片/附件與索引),不可復原;標籤/樣板定義保留。
    集合層的 DELETE /api/notes 與單筆 DELETE /api/notes/{nid} 路徑不同,不衝突。
    """
    group = group.strip()
    deleted = delete_notes_in_group(paths, group) if group else delete_all_notes(paths)
    return {"deleted": deleted}

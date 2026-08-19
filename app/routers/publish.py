"""
公開筆記快照的**擁有者面**(/api/publish/*)——需要登入,掛在 ALL_ROUTERS。

發佈 = 把自己庫的整庫(或 tags/group 篩選子集)凍結成 data/published/<pid>/
底下的一份快照(見 app/publish.py 檔頭)。名詞的**選集**在這一層做——
規則刻意與 GET /api/export 完全一致(tags OR 聯集、group 展開、group 不存在
400),使用者在匯出學會的範圍語意直接搬過來用。

權限:
  POST 在站台開關關閉時回 **403**(建立面可以講清楚「功能沒開」——呼叫者是
  登入的使用者,不存在枚舉問題;讀取面才 404,見 routers/published.py)。
  DELETE 對「不是自己的 pid」一律 **404** 不是 403:403 等於承認那份快照存在,
  可拿來枚舉全站的 pid。
"""
from fastapi import APIRouter, Depends, HTTPException

from .. import publish, site_settings
from ..auth import get_current_user, get_user_paths
from ..indexer import PROGRESS_COLS, db, row_to_note
from ..models import PublishIn
from ..paths import VaultPaths
from ..public_share import owner_label
from ..tags import load_tags

router = APIRouter()


def _select_notes(paths: VaultPaths, tags: str, group: str) -> tuple[list[dict], dict]:
    """發佈範圍的選集,規則同 transfer.api_export(tags OR;group 展開成標籤集)。

    走使用者自己的索引(登入操作,索引就在手邊);快照本身之後是純檔案,
    公開面完全不會再查這裡。
    """
    want = {t.strip() for t in tags.split(",") if t.strip()}
    group = group.strip()
    if group:
        in_group = {name for name, meta in load_tags(paths).items()
                    if meta.get("group", "") == group}
        if not in_group:
            raise HTTPException(400, "群組不存在或沒有標籤")
        want |= in_group

    conn = db(paths)
    notes = [row_to_note(r) for r in conn.execute(
        f"SELECT n.*, {PROGRESS_COLS} FROM notes n "
        "LEFT JOIN progress p ON p.vault_id=? AND p.note_id=n.id ORDER BY n.name",
        (paths.vault_id,)).fetchall()]
    conn.close()
    if want:
        notes = [n for n in notes if want & set(n["tags"])]
    return notes, {"tags": sorted(want) if tags.strip() else [], "group": group}


@router.get("/api/publish")
def api_list_publications(user: dict = Depends(get_current_user)):
    """自己的發佈清單。開關關著也照樣列出(讓擁有者看得到「我有哪些快照、
    要不要撤掉」),公開讀取面才受開關管。"""
    return {"publications": publish.list_by(user["id"]),
            "enabled": site_settings.public_notebook_enabled()}


@router.post("/api/publish")
def api_create_publication(body: PublishIn, user: dict = Depends(get_current_user),
                           paths: VaultPaths = Depends(get_user_paths)):
    if not site_settings.public_notebook_enabled():
        raise HTTPException(403, "站台尚未啟用公開筆記")
    pid = (body.pid or "").strip()
    if pid:
        # 重新發佈:pid 必須是自己既有的快照。查不到/不是自己的一律 404(防枚舉)。
        m = publish.manifest_of(pid)
        if not m or m.get("owner_id") != user["id"]:
            raise HTTPException(404, "找不到這份公開筆記")
    else:
        pid = publish.new_pid()

    notes, scope = _select_notes(paths, body.tags, body.group)
    if not notes:
        raise HTTPException(400, "選定範圍內沒有任何名詞")
    m = publish.create_publication(paths, notes, pid=pid, owner_id=user["id"],
                                   owner_label=owner_label(user["id"]),
                                   title=body.title, scope=scope)
    return {"pid": pid, "url": f"/p/{pid}", "note_count": m["note_count"],
            "title": m["title"]}


@router.delete("/api/publish/{pid}")
def api_revoke_publication(pid: str, user: dict = Depends(get_current_user)):
    """撤銷自己的一份發佈。刻意**不看站台開關**(比照單筆分享的 DELETE):
    開關關掉的期間,使用者仍然有權把自己的快照從磁碟上拿掉。"""
    m = publish.manifest_of(pid)
    if not m or m.get("owner_id") != user["id"]:
        raise HTTPException(404, "找不到這份公開筆記")
    publish.revoke(pid)
    return {"ok": True}

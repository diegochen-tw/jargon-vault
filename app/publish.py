"""
公開筆記快照(真相目錄 `data/published/<pid>/`,每份發佈一個目錄)。

## 這是什麼

使用者把整庫(或 tags/group 篩選子集)發佈成一份**凍結複本**,任何人免登入
可讀、可下載 v3 匯出檔匯入自己的庫自己維護。它取代了團隊庫(2026-08-19 拆除):
知識的傳承不靠共同持有,靠「發佈的複本在使用者目錄之外,帳號刪了它還在;
接手的人匯入就成了新維護者」。

## 凍結投影,不是活庫

目錄裡放的是**發佈當下算好的投影**,不是 .md、也沒有 index.db:

    manifest.json   {pid, owner_id, owner_label, title, created, note_count, scope}
    notes.json      [ project_note() 投影 + id … ](資產 URL 已改寫)
    assets/<nid>/<檔名>
    export.zip      v3 匯出檔(脫敏,見下),匯入端一行都不用改

公開端點因此是**純檔案讀取**:不經 user_paths()、不查 SQLite、不讀活的使用者
目錄——啟動迴圈/搜尋/索引完全不用知道 published 的存在。重新發佈 = 同 pid
整包覆蓋(連結穩定);撤銷 = rmtree。目錄本身就是登記簿。

## 兩道紅線

⚠ **export.zip 的脫敏鍵清單**:notes.json 走 project_note() 天然乾淨,但
export.zip 為了讓匯入端零改動,走的是 v3 的原始欄位形狀——tags / marked /
srs_box / srs_due / history 是**另外 pop 掉的**(_EXPORT_DROP_KEYS),漏一鍵
就把內部標籤或個人狀態送出登入牆;templates 的 ai_prompt 同理清空(使用者
自訂的 prompt 可能含內部脈絡)。守門測試:
tests/test_publish_api.py:test_publication_never_contains_progress_tags_or_id。

⚠ **staging + rename**:發佈先寫進 `.tmp-<pid>` 再一次 rename 到定位——
寫到一半失敗不會留下半份「看起來能讀」的快照。重新發佈在 rmtree 舊目錄與
rename 之間有極短的 404 視窗,接受:發佈是低頻操作,失敗重按一次就修好,
比引入鎖便宜。啟動時 purge_stale_tmp() 清掉 rename 前就死掉的孤兒。

pid 用 `secrets.token_urlsafe(16)`(22 字元/128 bits,不可由 uid 推導——
可推導的話「撤銷 → 重新發佈」會給出同一條網址,撤銷就是假的)。字母表
恰好落在 `config.ID_RE` 內,所以路由層直接用 `valid_id()` 驗。

本模組只碰檔案系統,不碰 SQLite、不碰 HTTP;名詞的選集由 router 負責
(那需要查使用者自己的索引)。
"""
import json
import secrets
import shutil
import time
import zipfile

from . import atomic
from .config import PUBLISHED_DIR, valid_id
from .paths import VaultPaths
from .public_share import project_note
from .templates import load_templates

PID_BYTES = 16   # token_urlsafe(16) → 22 字元 / 128 bits

MANIFEST_NAME = "manifest.json"
NOTES_NAME = "notes.json"
EXPORT_NAME = "export.zip"

# export.zip 的 notes 要 pop 掉的鍵(紅線,見檔頭):
#   tags               內部分類代號,對站外讀者是純粹的洩漏
#   marked/srs_box/srs_due  個人狀態(發佈者自己的書籤與複習進度)
#   history            歷史版本可能含發佈者不想公開的舊內容
_EXPORT_DROP_KEYS = ("tags", "marked", "srs_box", "srs_due", "history")


def new_pid() -> str:
    pid = secrets.token_urlsafe(PID_BYTES)
    while (PUBLISHED_DIR / pid).exists():  # 機率極低,但撞上就是覆蓋別人的發佈
        pid = secrets.token_urlsafe(PID_BYTES)
    return pid


def pub_root(pid: str):
    """pid → 快照目錄。⚠ pid 來自網址,一定要先過 valid_id()——這是把「來自
    網址的 id」組進路徑的模組,少了這關就是路徑穿越(同 config.py 檔內慣例)。"""
    if not valid_id(pid):
        raise ValueError(f"invalid publication id: {pid!r}")
    return PUBLISHED_DIR / pid


def create_publication(paths: VaultPaths, notes: list[dict], *, pid: str,
                       owner_id: str, owner_label: str, title: str,
                       scope: dict) -> dict:
    """把一批名詞(router 已選好、已含完整內容)凍結成一份快照。回傳 manifest。

    notes 是 transfer 匯出形狀的完整 dict(含 tags/marked/…),這裡分兩路投影:
    notes.json 走 project_note()(給公開頁),export.zip 走 v3 形狀再脫敏
    (給下載匯入)。
    """
    root = pub_root(pid)
    staging = PUBLISHED_DIR / f".tmp-{pid}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)

    # 公開頁投影:比單筆分享多帶 id(列表頁要有 key 可以展開單筆),
    # 其餘同 project_note 的窄投影。
    projected = []
    for n in notes:
        row = project_note(paths, n, f"p/{pid}/assets/{n['id']}/", owner_label)
        row["id"] = n["id"]
        projected.append(row)
    atomic.write_json(staging / NOTES_NAME, projected)

    # 資產:整目錄複製(圖片 + 附件同一個目錄,見 routers/files.py)
    for n in notes:
        src = paths.assets_dir / n["id"]
        if src.is_dir():
            shutil.copytree(src, staging / "assets" / n["id"])

    # export.zip:v3 形狀,匯入端(POST /api/import)一行都不用改。
    # ⚠ 脫敏是**獨立的一步**(_EXPORT_DROP_KEYS,理由見檔頭紅線)。
    # tag_groups 固定空(tags 都拿掉了,群組結構自然也不帶);
    # templates 只帶被引用的,且 ai_prompt 清空。
    used_tpl = {n.get("template", "") for n in notes}
    templates = []
    for t in load_templates(paths):
        if t.get("id") in used_tpl:
            templates.append({**t, "ai_prompt": ""})
    clean_notes = []
    for n in notes:
        row = {k: v for k, v in n.items() if k not in _EXPORT_DROP_KEYS}
        row["tags"] = []   # v3 形狀有這個鍵,匯入端 clean_tags 吃空陣列
        clean_notes.append(row)
    payload = json.dumps({"version": 3, "notes": clean_notes, "tag_groups": {},
                          "templates": templates}, ensure_ascii=False, indent=2)
    with zipfile.ZipFile(staging / EXPORT_NAME, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("notes.json", payload)
        for n in notes:
            src = paths.assets_dir / n["id"]
            if src.is_dir():
                for f in sorted(p for p in src.iterdir() if p.is_file()):
                    z.write(f, f"assets/{n['id']}/{f.name}")

    manifest = {
        "pid": pid,
        "owner_id": owner_id,
        "owner_label": owner_label,   # 凍結值:帳號刪除後照樣顯示
        "title": (title or "").strip(),
        "created": time.time(),
        "note_count": len(notes),
        "scope": scope,
    }
    atomic.write_json(staging / MANIFEST_NAME, manifest)

    # 原子換位:先 rmtree 舊的(重新發佈)再 rename。中間的極短 404 視窗是
    # 接受的取捨(見檔頭紅線)。
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    staging.rename(root)
    return manifest


def manifest_of(pid: str) -> dict | None:
    """讀一份快照的 manifest。不合法/不存在/壞檔一律 None(呼叫端一律 404)。"""
    if not valid_id(pid):
        return None
    try:
        data = json.loads((PUBLISHED_DIR / pid / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) and data.get("pid") == pid else None


def load_notes(pid: str) -> list[dict] | None:
    """讀快照的投影陣列(公開頁用)。壞檔一律 None。"""
    if not valid_id(pid):
        return None
    try:
        data = json.loads((PUBLISHED_DIR / pid / NOTES_NAME).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, list) else None


def list_all() -> list[dict]:
    """全站快照清單(admin 用)。目錄即登記簿:掃 manifest,壞的略過。"""
    if not PUBLISHED_DIR.exists():
        return []
    out = []
    for p in sorted(PUBLISHED_DIR.iterdir()):
        if not p.is_dir() or p.name.startswith(".tmp-"):
            continue
        m = manifest_of(p.name)
        if m:
            out.append(m)
    return sorted(out, key=lambda m: -m.get("created", 0))


def list_by(owner_id: str) -> list[dict]:
    """某個使用者自己的發佈清單。"""
    return [m for m in list_all() if m.get("owner_id") == owner_id]


def revoke(pid: str) -> bool:
    """撤銷一份發佈:整個目錄 rmtree,舊網址立刻 404。"""
    root = pub_root(pid)
    if not root.exists():
        return False
    shutil.rmtree(root, ignore_errors=True)
    return True


def purge_stale_tmp() -> int:
    """清掉 rename 前就死掉的 staging 孤兒(啟動時呼叫)。"""
    if not PUBLISHED_DIR.exists():
        return 0
    n = 0
    for p in PUBLISHED_DIR.iterdir():
        if p.is_dir() and p.name.startswith(".tmp-"):
            shutil.rmtree(p, ignore_errors=True)
            n += 1
    return n

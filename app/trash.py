"""
回收桶:被刪除的名詞在永久消失前的暫存區(真相來源 <使用者目錄>/trash/)。

刪除 = 把 <使用者目錄>/notes/<id>.md 搬到 <使用者目錄>/trash/<id>.md、資產目錄
notes/assets/<id>/ 搬到 trash/assets/<id>/,並在 frontmatter 補一個 deleted
時間戳記。**刪除時間存在檔案自己的 frontmatter 裡**,不另外維護一份登記簿——
還原、列表、過期清理都只要這一個檔案,不會有「登記簿與檔案對不上」的狀態。

回收桶目錄刻意放在 notes_dir 之外(見 app/paths.py 的註解):indexer/service
到處都是 notes_dir.glob("*.md"),只要待在那個 glob 掃不到的地方,就不會漏改
某一處而讓刪掉的名詞從搜尋/標籤統計/匯出裡冒出來。

保留 config.TRASH_RETENTION_DAYS 天,過期的在啟動時與每次列出回收桶時清掉。

本模組只碰檔案系統,不碰 SQLite、不碰 HTTP——索引同步與 404 判斷在 service.py。
"""
import shutil
import time
from pathlib import Path

from . import atomic
from .config import TRASH_RETENTION_DAYS
from .paths import VaultPaths
from .storage import dump_note, read_trashed_note

RETENTION_SECONDS = TRASH_RETENTION_DAYS * 86400


def trash_path(paths: VaultPaths, nid: str) -> Path:
    return paths.trash_dir / f"{nid}.md"


def move_to_trash(paths: VaultPaths, note: dict) -> None:
    """把一筆名詞搬進回收桶:先寫回收桶的檔案,成功後才刪原檔與搬資產目錄。

    順序是刻意的——先寫再刪,中途出錯寧可留下兩份(下次刪除會覆蓋),
    也不要刪掉原檔卻沒寫成回收桶檔而真的弄丟資料。
    """
    paths.trash_dir.mkdir(parents=True, exist_ok=True)
    nid = note["id"]
    atomic.write_text(trash_path(paths, nid), dump_note(note, {"deleted": time.time()}))
    (paths.notes_dir / f"{nid}.md").unlink(missing_ok=True)
    _move_dir(paths.assets_dir / nid, paths.trash_assets_dir / nid)


def read_trashed(paths: VaultPaths, nid: str) -> dict | None:
    return read_trashed_note(trash_path(paths, nid))


def list_trashed(paths: VaultPaths) -> list[dict]:
    """回收桶內容,刪除時間由新到舊。回傳列表用的精簡欄位(不含內文與歷史版本)。"""
    out = []
    if not paths.trash_dir.exists():
        return out
    for p in paths.trash_dir.glob("*.md"):
        note = read_trashed_note(p)
        if not note:
            continue
        out.append({
            "id": note["id"], "name": note["name"], "tags": note["tags"],
            "template": note["template"], "deleted": note["deleted"],
        })
    out.sort(key=lambda x: x["deleted"], reverse=True)
    return out


def restore_assets(paths: VaultPaths, nid: str) -> None:
    """把回收桶裡的資產目錄搬回 notes/assets/<nid>/(還原名詞時呼叫)。"""
    _move_dir(paths.trash_assets_dir / nid, paths.assets_dir / nid)


def drop(paths: VaultPaths, nid: str) -> bool:
    """永久刪除回收桶裡的一筆(.md + 資產目錄)。回傳是否真的有東西被刪掉。"""
    p = trash_path(paths, nid)
    existed = p.exists()
    p.unlink(missing_ok=True)
    shutil.rmtree(paths.trash_assets_dir / nid, ignore_errors=True)
    return existed


def drop_all(paths: VaultPaths) -> int:
    """清空回收桶。回傳永久刪除的筆數。"""
    if not paths.trash_dir.exists():
        return 0
    count = 0
    for p in list(paths.trash_dir.glob("*.md")):
        drop(paths, p.stem)  # 檔名去掉 .md 即 note id
        count += 1
    return count


def purge_expired(paths: VaultPaths) -> int:
    """清掉超過保留期限的項目。回傳永久刪除的筆數。

    deleted 讀不到(0)時視為過期立刻清掉不太好——那是手動塞檔案或寫壞的情況,
    寧可留著等使用者自己處理,所以只清「有時間戳記且確實過期」的。
    """
    if not paths.trash_dir.exists():
        return 0
    cutoff = time.time() - RETENTION_SECONDS
    count = 0
    for p in list(paths.trash_dir.glob("*.md")):
        note = read_trashed_note(p)
        if note and 0 < note["deleted"] < cutoff:
            drop(paths, p.stem)
            count += 1
    return count


def _move_dir(src: Path, dst: Path) -> None:
    """搬移一個資產目錄。目的地已存在時逐檔搬進去(shutil.move 對已存在的目錄
    會變成搬成子目錄,不是合併),搬完把空的來源目錄收掉。"""
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.move(str(src), str(dst))
        return
    for f in src.iterdir():
        target = dst / f.name
        if target.exists():
            continue  # 同名檔留在來源,由 shutil.rmtree 帶走;附件檔名是 uuid,實務上撞不到
        shutil.move(str(f), str(target))
    shutil.rmtree(src, ignore_errors=True)

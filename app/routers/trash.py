"""回收桶:列出、還原、永久刪除(設定 → 內容)。"""
from fastapi import APIRouter, Depends

from ..auth import get_user_paths, get_vault_admin, get_vault_read, get_vault_write
from ..config import TRASH_RETENTION_DAYS
from ..paths import VaultPaths
from ..service import purge_trash_note, restore_from_trash
from ..trash import drop_all, list_trashed, purge_expired

router = APIRouter()


@router.get("/api/trash")
def api_list(paths: VaultPaths = Depends(get_vault_read)):
    """回收桶內容(刪除時間由新到舊)+ 保留天數(前端算剩幾天用)。

    列出前先清一次過期項目:啟動時清過一輪,但伺服器可能連續跑好幾個月,
    只靠啟動清理會讓早該消失的東西一直躺在清單裡。
    """
    purge_expired(paths)
    return {"items": list_trashed(paths), "retention_days": TRASH_RETENTION_DAYS}


@router.post("/api/trash/{nid}/restore")
def api_restore(nid: str, paths: VaultPaths = Depends(get_vault_write)):
    return restore_from_trash(paths, nid)


@router.delete("/api/trash/{nid}")
def api_purge_one(nid: str, paths: VaultPaths = Depends(get_vault_admin)):
    purge_trash_note(paths, nid)
    return {"ok": True}


@router.delete("/api/trash")
def api_empty(paths: VaultPaths = Depends(get_vault_admin)):
    """清空回收桶(永久刪除,不可復原)。"""
    return {"deleted": drop_all(paths)}

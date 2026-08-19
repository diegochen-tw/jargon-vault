"""名詞之間的 `[[連結]]`:某筆指向誰、誰指向某筆,以及全部名稱的對照表。

語法規則在 app/links.py、查詢在 app/search.py,這裡只是薄殼。
"""
from fastapi import APIRouter, Depends

from ..auth import get_user_paths, get_vault_admin, get_vault_read, get_vault_write
from ..paths import VaultPaths
from ..search import all_names, links_of
from ..service import load_note_or_404

router = APIRouter()


@router.get("/api/names")
def api_names(paths: VaultPaths = Depends(get_vault_read)):
    """所有名詞的 {id, name}。

    前端拿它建「名稱 → 名詞」對照表,好在渲染 `[[連結]]` 的當下就知道目標存不存在
    (存在 = 可點,不存在 = 畫成待建立的樣式並提供建立入口)。逐個連結打 API 問
    是不可行的,所以整份撈。
    """
    return {"names": all_names(paths)}


@router.get("/api/notes/{nid}/links")
def api_note_links(nid: str, paths: VaultPaths = Depends(get_vault_read)):
    """一筆名詞的連結兩端:outgoing(它指向誰,含尚未建立的)與 backlinks(誰指向它)。"""
    note = load_note_or_404(paths, nid)
    return links_of(paths, note)

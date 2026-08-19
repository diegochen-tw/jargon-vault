"""重複偵測與合併:找出同一個詞被記了不只一次的名詞,並把它們收成一筆。

偵測規則在 app/dedup.py(只讀索引)、合併寫入在 app/service.py:merge_notes
(先寫檔再更新索引,被併掉的走一般刪除進回收桶),這裡只是薄殼。
"""
from fastapi import APIRouter, Depends

from ..auth import get_user_paths, get_vault_admin, get_vault_read, get_vault_write
from ..dedup import find_duplicate_groups, find_similar
from ..models import MergeIn, SimilarIn
from ..paths import VaultPaths
from ..service import merge_notes

router = APIRouter()


@router.post("/api/similar")
def api_similar(body: SimilarIn, paths: VaultPaths = Depends(get_vault_write)):
    """編輯器即時提示:跟目前正在輸入的名詞疑似重複的既有名詞。"""
    return {"similar": find_similar(paths, body.name, body.fields, body.exclude_id)}


@router.get("/api/duplicates")
def api_duplicates(paths: VaultPaths = Depends(get_vault_read)):
    """全庫掃描:互為重複的名詞收成一組一組(設定 → 內容的「重複偵測」)。"""
    return {"groups": find_duplicate_groups(paths)}


@router.post("/api/notes/merge")
def api_merge(body: MergeIn, paths: VaultPaths = Depends(get_vault_write)):
    """把 source_ids 併進 target_id。

    路徑跟 `PUT /api/notes/{nid}` 不衝突(方法與路徑都不同),但因為長得像
    `{nid}`,掛載順序仍以本 router 排在 notes.router 之後為準——FastAPI 依註冊
    順序比對,notes 那邊沒有 `POST /api/notes/{nid}` 這條路由,所以不會被吃掉。
    """
    return merge_notes(paths, body.target_id.strip(), [s.strip() for s in body.source_ids])

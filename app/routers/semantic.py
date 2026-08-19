"""語意檢索 API。邏輯在 app/semantic.py,這裡只做參數解析與錯誤轉換。

三支管理端點(status / reindex / index)給設定 → 語意檢索分頁用;
一支查詢端點(search)參數與 /api/search 完全一致,好讓前端只換 URL 就能切換。

嵌入連線設定走 `ai_settings.load_ai_settings()`——那是**全站唯一一組**、由站台
管理者管理的設定(見 app/ai_settings.py)。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from .. import llm, semantic
from ..ai_settings import load_ai_settings
from ..auth import get_vault_admin, get_vault_read, get_vault_write
from ..models import SemanticReindexIn
from ..paths import VaultPaths
from ..search import PAGE_SIZE
from ..tags import load_tags

router = APIRouter()

log = logging.getLogger("jargon_vault")


@router.get("/api/semantic/status")
def api_semantic_status(paths: VaultPaths = Depends(get_vault_read)):
    return semantic.status(paths, load_ai_settings())


@router.post("/api/semantic/reindex")
async def api_semantic_reindex(body: SemanticReindexIn,
                               paths: VaultPaths = Depends(get_vault_write)):
    """補上落後的向量。limit>0 時只做一批,由前端跑迴圈顯示進度。"""
    settings = load_ai_settings()
    if not settings.get("enabled"):
        raise HTTPException(400, "AI 功能未啟用")
    try:
        out = await semantic.reindex(paths, settings, max(0, body.limit))
    except llm.LLMError as e:
        # 中介層只記「status=502」,不記原因;真正的診斷線索(Ollama 的錯誤原文)
        # 在這裡,不寫下來的話 server 端永遠只看得到三位數字。
        log.warning("semantic reindex failed: %s", e)
        raise HTTPException(502, str(e))
    if out.get("failed"):
        log.warning("semantic reindex skipped %d note(s): %s; first error: %s",
                    len(out["failed"]),
                    ",".join(f["id"] for f in out["failed"]),
                    out["failed"][0]["error"])
    return out


@router.delete("/api/semantic/index")
def api_semantic_clear(paths: VaultPaths = Depends(get_vault_admin)):
    return semantic.clear(paths)


@router.get("/api/semantic/search")
async def api_semantic_search(q: str = "", tags: str = "", group: str = "", template: str = "",
                              days: int = 0, since: float = 0, until: float = 0,
                              sort: str = "updated", marked: int = 0,
                              date_field: str = "updated",
                              paths: VaultPaths = Depends(get_vault_read)):
    """語意 + 關鍵字的混合檢索。

    **沒有 offset,has_more 恆為 False**:RRF 名次在 Python 端算,跨兩臂的全域
    OFFSET 無法下推(理由見 semantic.search_hybrid)。

    索引還沒建好時回 **200 + needs_index**,不是錯誤——空列表會讓人以為庫裡沒
    東西,而真正的原因是還沒建索引,UI 要說得出這件事。
    """
    settings = load_ai_settings()
    empty = {"results": [], "has_more": False}
    if not settings.get("enabled") or not (settings.get("embed_model") or "").strip():
        return {**empty, "needs_index": True}
    if not q.strip():
        return {**empty, "needs_index": False}

    tag_list = [t for t in tags.split(",") if t.strip()]
    any_tags: list[str] = []
    if group.strip():
        group = group.strip()
        any_tags = [name for name, meta in load_tags(paths).items()
                    if meta.get("group", "") == group]
        if not any_tags:
            return {**empty, "needs_index": False}  # 群組不存在或沒有標籤

    # ⚠ 用 quick_status() 不是 status():後者要讀全部 .md 算 hash,掛在查詢路徑上
    # 等於每查一次就全庫掃一遍,而查詢是跟著搜尋框的 debounce 一直發生的。
    indexed, unindexed = semantic.quick_status(paths)
    if not indexed:
        return {**empty, "needs_index": True}

    try:
        rows = await semantic.search_hybrid(
            paths, settings, q, tags=tag_list, any_tags=any_tags, days=days,
            since=since, until=until, template=template.strip(), marked=bool(marked),
            sort=sort, limit=PAGE_SIZE, date_field=date_field)
    except llm.LLMError as e:
        raise HTTPException(502, str(e))
    return {"results": rows, "has_more": False, "needs_index": False,
            "unindexed": unindexed}

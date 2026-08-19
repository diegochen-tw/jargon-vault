"""搜尋 API。查詢邏輯在 app/search.py,這裡只做參數解析。

group 參數是「標籤群組」篩選:在這一層展開成群組內的標籤集合
(search.py 不碰檔案系統,拿不到 tags.json),名詞掛有其中任一標籤即命中。

marked=1 只回有書籤標記的名詞(統計列的 💾 開關)。用 int 而非 bool 是比照
既有的 days/since 風格,前端直接送 marked=1 / marked=0。

offset 是「載入更多」分頁的游標(目前已載入筆數)。has_more 用「多要一筆」的
技巧算——呼叫 search_notes 時 limit 多要 1 筆(PAGE_SIZE+1),拿回 PAGE_SIZE+1
筆代表後面還有、丟掉多要的那一筆再回給前端;≤PAGE_SIZE 筆代表這就是全部。
這樣 search_notes 本身完全不用改變回傳型別(仍是 list[dict])。
"""
from fastapi import APIRouter, Depends

from ..auth import get_vault_read
from ..paths import VaultPaths
from ..search import PAGE_SIZE, search_notes
from ..tags import load_tags

router = APIRouter()


@router.get("/api/search")
def api_search(q: str = "", tags: str = "", group: str = "", template: str = "", days: int = 0,
               since: float = 0, until: float = 0, sort: str = "updated", marked: int = 0,
               date_field: str = "updated", offset: int = 0,
               paths: VaultPaths = Depends(get_vault_read)):
    tag_list = [t for t in tags.split(",") if t.strip()]
    any_tags: list[str] = []
    if group.strip():
        group = group.strip()
        any_tags = [name for name, meta in load_tags(paths).items()
                    if meta.get("group", "") == group]
        if not any_tags:
            return {"results": [], "has_more": False}  # 群組不存在或沒有標籤 → 沒有東西可命中
    rows = search_notes(paths, q, tag_list, any_tags, days, since, until, template.strip(),
                        limit=PAGE_SIZE + 1, sort=sort, marked=bool(marked), offset=offset,
                        date_field=date_field)
    has_more = len(rows) > PAGE_SIZE
    return {"results": rows[:PAGE_SIZE], "has_more": has_more}

"""SRS 複習 API:抽卡與自評。

排程規則在 app/srs.py(純算術),候選池查詢在 app/search.py:due_notes()——
這裡只做參數解析與「在池子裡隨機挑幾張」。

**抽卡的篩選維度與 /api/search 完全一致**(tags/group/template/days/since/
until/marked),因為複習範圍就是「側欄現在圈出來的東西」:拖日期軌道 = 複習
那段時間記下的名詞,選標籤 = 只複習那一組。刻意少兩個:

  offset —— 一輪就是一輪,沒有第二頁。
  q      —— filtered_ids()/due_notes() 走的是 _filters(),沒有關鍵字維度
            (關鍵字要動到 search_notes 的三個查詢分支)。**已知缺口**:
            搜尋框打了字再開複習,關鍵字不算數。所以回傳的 scope 誠實列出
            實際生效的維度,讓前端把範圍寫在彈窗頂端而不是讓人自己猜。
"""
import random

from fastapi import APIRouter, Depends

from ..auth import get_user_paths, get_vault_read
from ..models import SrsReviewIn
from ..paths import VaultPaths
from ..search import due_notes, filtered_ids
from ..service import load_note_or_404, save_progress_entry
from ..srs import POOL_FACTOR, clamp_draw_size, next_state
from ..tags import load_tags

router = APIRouter()


@router.get("/api/srs/draw")
def api_srs_draw(tags: str = "", group: str = "", template: str = "", days: int = 0,
                 since: float = 0, until: float = 0, marked: int = 0,
                 date_field: str = "updated", all_scope: int = 0, size: int = 0,
                 paths: VaultPaths = Depends(get_user_paths)):
    """抽一輪複習卡。

    all_scope=1 時忽略全部篩選,改抽整個庫(彈窗頂端的「改用全庫」切換)。
    size 是「這一輪要幾張」(使用者在偏好設定調的),0/沒帶 = 預設,超出範圍會被夾住。

    取按 srs_due 升冪的前 size * POOL_FACTOR 筆當池子 → 隨機打散取 size 張:
    最該複習的一定在池子裡,但連續兩天打開不會拿到一模一樣的卡序。
    篩選、排序、筆數一律在 due_notes() 下推 SQL,這裡只做隨機挑選。
    """
    size = clamp_draw_size(size)
    pool_size = size * POOL_FACTOR
    scope: dict = {"all": bool(all_scope)}
    tag_list: list[str] = []
    any_tags: list[str] = []

    if not all_scope:
        tag_list = [t for t in tags.split(",") if t.strip()]
        template = template.strip()
        group = group.strip()
        if group:
            any_tags = [name for name, meta in load_tags(paths).items()
                        if meta.get("group", "") == group]
            if not any_tags:
                # 群組不存在或沒有標籤 → 沒有東西可命中(比照 routers/search.py)
                return {"cards": [], "pool": 0, "scope": {**scope, "group": group}}
        scope.update(tags=tag_list, group=group, template=template,
                     days=days, since=since, until=until, marked=bool(marked))
    else:
        template, days, since, until, marked = "", 0, 0, 0, 0
        date_field = "updated"   # 全庫模式沒有日期維度,欄位選擇也就沒有意義

    args = (tag_list, any_tags, days, since, until, template, bool(marked))
    dkw = {"date_field": date_field}   # 日期比 updated 還是 created,兩支查詢要一致
    # 池子大小走 filtered_ids():它本來就是「套完篩選、無 LIMIT 的完整 id
    # 集合」,拿它的長度當計數不必再寫一支 COUNT 查詢。
    pool = len(filtered_ids(paths, *args, **dkw))
    rows = due_notes(paths, *args, limit=pool_size, **dkw)
    cards = random.sample(rows, min(size, len(rows)))
    return {"cards": cards, "pool": pool, "scope": scope}


@router.post("/api/srs/{nid}/review")
def api_srs_review(nid: str, body: SrsReviewIn,
                   paths: VaultPaths = Depends(get_vault_read)):
    """記錄一次複習,推進 Leitner 盒序與下次到期日。

    形狀完全比照 notes.py:api_set_mark——**不寫歷史版本、不動 updated**。
    複習不是內容編輯:讓它 bump updated 會把「依上次編輯時間」的列表排序整個
    打亂,而 HISTORY_LIMIT 只有 3,寫進歷史等於複習幾輪就把真正的編輯歷史沖光。

    進度寫進**個人側**的 progress.json 而不是名詞的 .md(見 app/progress.py)——
    複習從來就不是對名詞內容的修改,所以這裡連 .md 都不碰。
    """
    note = load_note_or_404(paths, nid)  # 內含 valid_id 檢查(不合法 → 400)
    box, due = next_state(note.get("srs_box"), body.remembered)
    save_progress_entry(paths, nid, srs_box=box, srs_due=due)
    note["srs_box"], note["srs_due"] = box, due
    return note

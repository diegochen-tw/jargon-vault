"""
搜尋層:查詢策略。

策略選擇(決定 WHERE 前半段怎麼找候選):
  - 空查詢       → 不加關鍵字條件,直接列出
  - 全部 token ≥3 字元 → FTS5 trigram 子字串比對
  - 有過短 token  → LIKE 全掃(個人量級毫秒可完成)

**篩選、排序、分頁全部在 SQL 做,Python 端不再後置處理任何一項。**
tag / any_tags / template / days / since / until / marked 每一個維度都下推成
WHERE 條件(`_filters()`),排序是 ORDER BY(`_order()`),分頁是 LIMIT/OFFSET。
新增篩選維度請加進 `_filters()`,三個分支自動共用。

⚠ 為什麼一定要下推:以前這裡是「SQL 撈候選前 500 筆 → Python 端篩選」,
超過 500 筆之後會產生**沒有任何提示的靜默錯誤**——篩一個冷門標籤時,命中的
名詞只要排不進那 500 筆就被靜默漏掉;`sort=created` 的候選卻是依 updated 取的;
`offset >= 500` 的「載入更多」永遠回空。別為了圖方便把任何一個維度搬回 Python。

any_tags 是「標籤群組」篩選(OR 聯集,名詞掛有其中任一標籤即命中);
群組 → 標籤集合的展開在 router 層做(本層不碰檔案系統,拿不到 tags.json)。
tag / any_tags 都靠 indexer 的 note_tags 正規化表比對,不是對 notes.tags 那個
JSON 字串做 LIKE(那要處理萬用字元跳脫與標籤含引號的邊界)。

days 是「從現在往前推 N 天」的滾動區間(本日/本週/半個月/本月);
since/until 是精確的 unix 秒數區間,給「昨天」「前天」這種精確日曆日用——
日曆日的日界要看使用者的本地時區,前端(actions.js)算好才送進來,
後端只單純比大小,不猜時區。三者比哪一欄由 `date_field` 決定
("updated"=上次編輯時間,預設 / "created"=建立時間;見 `date_col()`),
**三個時間維度一起換,不會只換一半**。⚠ `date_field` 與 `sort` 是兩件事:
前者是「篩哪一段時間」,後者是「排序依據」,可以各自獨立設定。

sort 決定排序依據("updated"=上次編輯時間 / "created"=建立時間,預設 updated,
無效值一律退回 updated)。有搜尋關鍵字時,「名稱直接命中者」排在最前面
(FTS 分支因此不再 ORDER BY bm25——它算出來的相關性順序本來就會被這條規則
整個蓋掉,留著只是死碼)。

offset 是「載入更多」分頁用的游標,直接下推成 SQL 的 OFFSET,沒有筆數上限。
是否還有下一頁(has_more)不在這裡算,由 routers/search.py 用「多要一筆」
的技巧推算,search_notes 本身仍只回傳 list[dict],不改變回傳型別。
"""
import time

from .indexer import PROGRESS_COLS, db, row_to_note
from .links import link_targets
from .paths import VaultPaths
from .sanitize import norm_key

PAGE_SIZE = 50  # 卡片列表一頁的筆數(初次載入與「載入更多」都用這個值;router 層引用當單一真相來源)


def _pjoin(paths: VaultPaths) -> tuple[str, list]:
    """把個人狀態(progress)LEFT JOIN 進來的片段 + 參數。

    ⚠ 這是**接在名詞表之後**的一段,不是完整的 FROM——FTS 分支的
    `JOIN notes n ON n.id=f.id` 必須先閉合它自己的 ON,這一段才能接上去。
    (寫成「整個 FROM」再讓 FTS 分支補 ON,組出來的 SQL 會是
    `JOIN notes n LEFT JOIN progress p ON … ON n.id=f.id`,直接語法錯誤。)

    marked 篩選與 srs_due 排序都靠這個 JOIN,所以**每個查詢分支都得接上**——
    少接一處,那個分支的書籤篩選就會安靜地全篩不到。

    ⚠ 這個 JOIN 是切片 1「進度外移」的產物(書籤/複習進度住在 progress 表,
    不在名詞上),跟已拆除的團隊庫無關——**絕不能順手拔掉**。
    """
    return (" LEFT JOIN progress p ON p.vault_id=? AND p.note_id=n.id", [paths.vault_id])


def date_col(date_field: str) -> str:
    """日期篩選要比哪一欄:預設「上次編輯時間」,`created` 才切成「建立時間」。

    ⚠ 這是**白名單映射不是驗證**:任何不是 "created" 的值一律落回 "updated",
    所以回傳值永遠是這兩個字面值之一,內插進 SQL 是安全的(同 `_order()` 的
    `sort_field`)。絕對不要改成把參數直接串進 SQL——那個字串來自網址。
    """
    return "created" if date_field == "created" else "updated"


def _filters(tags: list[str], any_tags: list[str] | None, days: int, since: float,
             until: float, template: str, marked: bool,
             date_field: str = "updated") -> tuple[list[str], list]:
    """把所有篩選維度組成 WHERE 條件片段與參數(三個查詢分支共用)。

    名詞資料表一律以別名 `n` 引用,片段直接用 AND 串進各分支的 WHERE。

    `date_field` 決定 days/since/until 三個時間維度比的是哪一欄(見 `date_col()`)。
    三者一起切換是刻意的:它們是同一個「時間軸」的三種指定方式,只切一部分會讓
    「最近 7 天」與拖曳出來的區間指向不同的時間概念。
    """
    dcol = date_col(date_field)
    conds: list[str] = []
    params: list = []

    if marked:
        # 書籤住在 progress 表(個人狀態),靠 _from() 的 LEFT JOIN 帶進來;
        # 沒有進度記錄 = 沒被標記,所以要 COALESCE 而不是直接比 p.marked。
        conds.append("COALESCE(p.marked,0)=1")

    if tags:  # 多重 tag 篩選(AND:必須同時掛有全部標籤)
        uniq = list(dict.fromkeys(tags))  # 去重後才能拿長度當 HAVING 的比較值
        conds.append(
            f"n.id IN (SELECT note_id FROM note_tags WHERE tag IN ({','.join('?' * len(uniq))})"
            " GROUP BY note_id HAVING COUNT(DISTINCT tag)=?)")
        params += uniq + [len(uniq)]

    if any_tags:  # 標籤群組篩選(OR:掛有群組內任一標籤即命中)
        uniq_any = list(dict.fromkeys(any_tags))
        conds.append(
            f"n.id IN (SELECT note_id FROM note_tags WHERE tag IN ({','.join('?' * len(uniq_any))}))")
        params += uniq_any

    if template:  # 欄位樣板篩選
        conds.append("n.template=?")
        params.append(template)

    if days > 0:  # 滾動區間篩選:從現在往前推 N 天
        conds.append(f"n.{dcol}>=?")
        params.append(time.time() - days * 86400)

    if since > 0:  # 精確日曆日篩選(如「昨天」「前天」),區間由前端算好帶入
        conds.append(f"n.{dcol}>=?")
        params.append(since)
    if until > 0:
        conds.append(f"n.{dcol}<?")
        params.append(until)

    return conds, params


def filtered_ids(paths: VaultPaths, tags: list[str], any_tags: list[str] | None = None,
                 days: int = 0, since: float = 0, until: float = 0, template: str = "",
                 marked: bool = False, date_field: str = "updated") -> set[str]:
    """套用所有篩選維度後的**完整** id 集合(沒有 LIMIT)。

    給語意檢索的向量臂用:向量比對發生在 SQLite 之外,沒辦法把 WHERE 條件跟
    KNN 寫在同一句 SQL 裡,所以改成「先在 SQL 把合格的 id 全部撈出來,向量臂
    只在這個集合內比」。

    ⚠ 這**不是**把篩選搬回 Python。每個維度仍然是 `_filters()` 組出來的 SQL
    條件、由 SQLite 執行;而且刻意不設上限——回傳的集合對篩選維度是**完備的**,
    所以不可能發生「命中的名詞排不進候選集就被靜默漏掉」那種舊 bug。被禁的那個
    寫法是「SQL 撈固定 500 筆 → Python 端**才篩**標籤」,性質完全不同。

    個人量級下這就是一句索引掃描;真的大到會痛的時候,痛的也不會是這裡。
    """
    conds, params = _filters(tags, any_tags, days, since, until, template, marked, date_field)
    pjoin, from_params = _pjoin(paths)
    sql = f"SELECT n.id FROM notes n{pjoin}" + ((" WHERE " + " AND ".join(conds)) if conds else "")
    conn = db(paths)
    try:
        return {r["id"] for r in conn.execute(sql, from_params + params).fetchall()}
    finally:
        conn.close()


def notes_by_ids(paths: VaultPaths, ids: list[str]) -> list[dict]:
    """依傳入的 id 順序取回名詞本體(排序已經在別處決定好了)。

    給語意檢索用:RRF 融合算完名次才需要真正的內容,一次把該頁的幾十筆撈回來
    比逐筆查省事。查不到的 id(剛被刪掉)安靜略過。
    """
    if not ids:
        return []
    pjoin, from_params = _pjoin(paths)
    conn = db(paths)
    try:
        rows = conn.execute(
            f"SELECT n.*, {PROGRESS_COLS} FROM notes n{pjoin} "
            f"WHERE n.id IN ({','.join('?' * len(ids))})", from_params + list(ids)).fetchall()
    finally:
        conn.close()
    by_id = {r["id"]: r for r in rows}
    return [row_to_note(by_id[i]) for i in ids if i in by_id]


def due_notes(paths: VaultPaths, tags: list[str], any_tags: list[str] | None = None,
              days: int = 0, since: float = 0, until: float = 0, template: str = "",
              marked: bool = False, limit: int = PAGE_SIZE,
              date_field: str = "updated") -> list[dict]:
    """SRS 抽卡的候選池:套用與 `/api/search` **完全相同**的 `_filters()`,
    按 srs_due 升冪取前 limit 筆。

    排序軸是 srs_due 而不是 updated:它是「下次該複習的時間」,而從沒複習過的
    名詞在讀檔時就退回 updated(見 storage.py / srs.effective_due)。所以
    「久未觸碰的優先」是這條 ORDER BY 的自然結果,不需要第二個模式、不需要
    第二條查詢路徑,舊 .md 也一行都不用改。

    篩選、排序、筆數一律下推 SQL——上層只負責在這個池子裡隨機挑幾張,
    **不做任何後置篩選**(那條禁令見模組頂端)。
    """
    conds, params = _filters(tags, any_tags, days, since, until, template, marked, date_field)
    pjoin, from_params = _pjoin(paths)
    sql = (f"SELECT n.*, {PROGRESS_COLS} FROM notes n{pjoin}"
           + ((" WHERE " + " AND ".join(conds)) if conds else "")
           + " ORDER BY COALESCE(p.srs_due, n.updated) ASC LIMIT ?")
    conn = db(paths)
    try:
        rows = conn.execute(sql, from_params + params + [max(0, limit)]).fetchall()
    finally:
        conn.close()
    return [row_to_note(r) for r in rows]


def _order(tokens: list[str], q: str, sort_field: str) -> tuple[str, list]:
    """ORDER BY 片段與參數。sort_field 是白名單字面值(created/updated),內插安全。

    有搜尋關鍵字時名稱直接命中者優先。⚠ SQLite 的 lower() 只處理 ASCII,帶重音的
    拉丁字母(如 É)不會被小寫化,這種查詢在「名稱命中優先」上可能與 Python 的
    str.lower() 判斷不同——只影響同一批結果的先後,不影響是否命中。
    """
    if tokens:
        return (f"CASE WHEN instr(lower(n.name), lower(?))>0 THEN 0 ELSE 1 END, "
                f"n.{sort_field} DESC", [q.lower()])
    return f"n.{sort_field} DESC", []


def search_notes(paths: VaultPaths, q: str, tags: list[str], any_tags: list[str] | None = None,
                 days: int = 0, since: float = 0, until: float = 0, template: str = "",
                 limit: int = PAGE_SIZE, sort: str = "updated", marked: bool = False,
                 offset: int = 0, date_field: str = "updated") -> list[dict]:
    sort_field = "created" if sort == "created" else "updated"
    q = q.strip()
    tokens = [t for t in q.split() if t]

    conds, cond_params = _filters(tags, any_tags, days, since, until, template, marked, date_field)
    order, order_params = _order(tokens, q, sort_field)
    pjoin, from_params = _pjoin(paths)
    where_tail = "".join(" AND " + c for c in conds)
    # SQLite 的 LIMIT -1 是「不限筆數」,負數不能漏過去
    page_params = [max(0, limit), max(0, offset)]

    conn = db(paths)
    if not tokens:
        sql = (f"SELECT n.*, {PROGRESS_COLS} FROM notes n{pjoin}"
               f"{(' WHERE ' + ' AND '.join(conds)) if conds else ''} "
               f"ORDER BY {order} LIMIT ? OFFSET ?")
        params = from_params + cond_params + order_params + page_params
    elif all(len(t) >= 3 for t in tokens):
        # FTS5 trigram:子字串比對,名詞沒打完也能命中。
        # ⚠ 參數順序:FROM 的 JOIN 條件排在 WHERE 之前,所以 from_params 要放最前面。
        match = " AND ".join('"' + t.replace('"', '""') + '"' for t in tokens)
        sql = (f"SELECT n.*, {PROGRESS_COLS} FROM fts f JOIN notes n ON n.id=f.id{pjoin} "
               f"WHERE fts MATCH ?{where_tail} ORDER BY {order} LIMIT ? OFFSET ?")
        params = from_params + [match] + cond_params + order_params + page_params
    else:
        # 過短(如 2 個中文字 / 2 個英文字母)→ LIKE 全掃,個人量級毫秒可完成
        # fields 欄是 JSON 字串,LIKE 會連 key 一起比對——個人量級的雜訊可接受,
        # 換得不用逐列 json.loads
        like = " AND ".join(
            ["(n.name LIKE ? OR n.description LIKE ? OR n.fields LIKE ? OR n.tags LIKE ?)"]
            * len(tokens))
        like_params: list = []
        for t in tokens:
            like_params += [f"%{t}%"] * 4
        sql = (f"SELECT n.*, {PROGRESS_COLS} FROM notes n{pjoin} WHERE {like}{where_tail} "
               f"ORDER BY {order} LIMIT ? OFFSET ?")
        params = from_params + like_params + cond_params + order_params + page_params

    # try/finally 而不是直接 close():查詢拋例外時連線也一定要還回去。漏掉的話
    # 在 Windows 上那個檔會一直被鎖住(連刪都刪不掉),而「index.db 壞了就刪掉
    # 重建」正是本專案對索引損壞的標準處置。
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [row_to_note(r) for r in rows]


# ── `[[名詞]]` 連結的查詢 ───────────────────────────────────────────
#
# 語法與抽取規則在 app/links.py(純文字、無 DB);這裡只負責問索引。
# 放在搜尋層而不是自成一個模組,是因為它就是查詢:以下幾支都只讀 indexer
# 的表,不碰檔案系統,跟本檔既有的約定一致。


def all_names(paths: VaultPaths) -> list[dict]:
    """所有名詞的 {id, name}(依名稱排序)。

    給前端建「名稱 → 名詞」的對照表用:`[[連結]]` 要在渲染當下就知道目標存不存在
    (才畫得出「尚未建立」的樣式),不可能每個連結打一次 API。個人量級一次撈完
    比分頁簡單得多(同 service.all_attachments 的取捨)。
    """
    conn = db(paths)
    rows = conn.execute("SELECT id, name FROM notes ORDER BY name").fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"]} for r in rows]


def resolve_names(paths: VaultPaths, names: list[str]) -> dict[str, str]:
    """一批名稱 → {norm_key: note_id}(查不到的 key 不會出現在結果裡)。

    同 norm_key 有多筆名詞時取任一筆——那本身就是「重複的名詞」,該由重複偵測
    (app/dedup.py)處理,不是連結解析要煩惱的事。
    """
    keys = list({norm_key(n) for n in names if norm_key(n)})
    if not keys:
        return {}
    conn = db(paths)
    rows = conn.execute(
        f"SELECT id, name_key FROM notes WHERE name_key IN ({','.join('?' * len(keys))})",
        keys).fetchall()
    conn.close()
    return {r["name_key"]: r["id"] for r in rows}


def links_of(paths: VaultPaths, note: dict) -> dict:
    """一筆名詞的連結兩端。

    outgoing:這筆內容裡寫下的 `[[名稱]]`,附上解析到的 id(還沒建立的目標 id 為
             None——這是刻意保留的資訊,前端據此提供「建立這個名詞」的入口)。
    backlinks:哪些名詞指著這筆(依名稱比對,所以連結寫在對方建立之前也算數)。
    自己指向自己不列入 backlinks(沒有資訊量,只會佔版面)。
    """
    targets = link_targets(note)
    resolved = resolve_names(paths, targets)
    outgoing = [{"name": name, "id": resolved.get(norm_key(name))} for name in targets]

    conn = db(paths)
    rows = conn.execute(
        "SELECT DISTINCT n.id, n.name, n.updated FROM note_links l JOIN notes n ON n.id=l.src_id "
        "WHERE l.target_key=? AND l.src_id<>? ORDER BY n.updated DESC",
        (norm_key(note["name"]), note["id"])).fetchall()
    conn.close()
    return {
        "outgoing": outgoing,
        "backlinks": [{"id": r["id"], "name": r["name"]} for r in rows],
    }


def notes_linking_to(paths: VaultPaths, key: str) -> list[str]:
    """指向某個 norm_key 的所有名詞 id。合併名詞時用來決定要改寫哪幾筆。"""
    conn = db(paths)
    rows = conn.execute("SELECT DISTINCT src_id FROM note_links WHERE target_key=?", (key,)).fetchall()
    conn.close()
    return [r["src_id"] for r in rows]

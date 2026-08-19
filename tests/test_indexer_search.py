"""
app/indexer.py(SQLite FTS5 可拋棄快取)+ app/search.py(查詢策略)。

這兩層天生適合測試:GLOSSARY_DATA_DIR 隔離模式讓每個測試都能起一個全新、
真實的 SQLite 檔案(不是 mock),索引又是「從 .md 全量重建」的可拋棄快取,
測試只要準備好 .md 檔案、呼叫 rebuild_index() 建索引,再對 search_notes()
斷言查詢結果——完全不需要碰 HTTP 層。
"""
import time

from app.indexer import db, index_delete, rebuild_index
from app.progress import save_progress
from app.search import search_notes
from app.storage import write_note_file

NOW = time.time()


def _note(nid, name, description="", template="jargon-default", fields=None,
          tags=None, updated=None, created=None) -> dict:
    return {
        "id": nid, "name": name, "description": description, "template": template,
        "fields": fields or {}, "tags": tags or [], "attachments": [],
        "created": created if created is not None else (updated if updated is not None else NOW),
        "updated": updated if updated is not None else NOW, "history": [],
    }


def _seed(paths, *notes):
    for n in notes:
        write_note_file(paths, n)
    # 書籤與複習進度**不在 .md 裡**(見 app/progress.py):它們是「我跟名詞的
    # 關係」,不是名詞的屬性。write_note_file 帶不走,要另外寫進個人進度檔,
    # rebuild_index 才會把它們投影成可 JOIN 的 progress 表。
    entries = {n["id"]: {"marked": bool(n.get("marked")),
                         "srs_box": n.get("srs_box"),
                         "srs_due": n.get("srs_due")}
               for n in notes if n.get("marked") or n.get("srs_box") is not None}
    if entries:
        save_progress(paths, {paths.vault_id: entries})
    rebuild_index(paths)


def test_empty_query_returns_all_notes_sorted_by_updated_desc(paths):
    _seed(
        paths,
        _note("n1", "apple", updated=NOW - 200),
        _note("n2", "banana", updated=NOW - 100),
        _note("n3", "cherry", updated=NOW),
    )
    results = search_notes(paths, "", [])
    assert [r["id"] for r in results] == ["n3", "n2", "n1"]


def test_query_branches_hit_name_description_and_fields(paths):
    """FTS trigram(≥3 字元)要命中名稱/說明/欄位值;短 token 走 LIKE 全掃;
    沒命中回空列表——三個查詢分支共用同一份種子資料各驗一遍。"""
    _seed(
        paths,
        _note("n1", "quicksort", description="a sorting algorithm"),
        _note("n2", "apple", template="english-word",
              fields={"translation": "蘋果", "phonetic": "/ˈæp.əl/"},
              description="蘋果是一種水果"),
        _note("n3", "banana", description="黃色的水果"),
    )
    # FTS trigram:名稱與說明的子字串
    assert [r["id"] for r in search_notes(paths, "quick", [])] == ["n1"]
    assert [r["id"] for r in search_notes(paths, "sorting", [])] == ["n1"]
    # 欄位值(fts 的 fields_text 欄)
    assert [r["id"] for r in search_notes(paths, "æp", [])] == ["n2"]
    # 少於 3 字元(中文 2 字)走 LIKE 全掃
    assert [r["id"] for r in search_notes(paths, "蘋果", [])] == ["n2"]
    # 沒命中回空列表
    assert search_notes(paths, "nonexistent-keyword", []) == []


def test_tags_filter_is_and_any_tags_is_or_and_duplicates_still_match(paths):
    _seed(
        paths,
        _note("n1", "apple", tags=["fruit", "fresh"]),
        _note("n2", "banana", tags=["fruit"]),
        _note("n3", "quicksort", tags=["algorithm"]),
    )
    # 單一標籤
    assert {r["id"] for r in search_notes(paths, "", ["fruit"])} == {"n1", "n2"}
    # AND:同時掛兩者的才命中
    assert [r["id"] for r in search_notes(paths, "", ["fruit", "fresh"])] == ["n1"]
    # AND 的重複輸入不該讓條件永遠不成立
    assert {r["id"] for r in search_notes(paths, "", ["fruit", "fruit"])} == {"n1", "n2"}
    # any_tags:OR
    assert {r["id"] for r in search_notes(paths, "", [], any_tags=["algorithm", "fresh"])} \
        == {"n1", "n3"}


def test_template_filter_alone_and_combined_with_tags(paths):
    _seed(
        paths,
        _note("n1", "apple", template="english-word", tags=["fruit"]),
        _note("n2", "quicksort", template="code-snippet"),
        _note("n3", "avocado", template="english-word", tags=["vegetable"]),
    )
    assert [r["id"] for r in search_notes(paths, "", [], template="code-snippet")] == ["n2"]
    assert {r["id"] for r in search_notes(paths, "", [], template="english-word")} == {"n1", "n3"}
    assert [r["id"] for r in search_notes(paths, "", ["fruit"], template="english-word")] == ["n1"]


def test_days_since_until_windows(paths):
    _seed(
        paths,
        _note("recent", "apple", updated=NOW - 1000),
        _note("stale", "banana", updated=NOW - 30 * 86400),
    )
    assert [r["id"] for r in search_notes(paths, "", [], days=7)] == ["recent"]
    assert [r["id"] for r in search_notes(paths, "", [], since=NOW - 2000)] == ["recent"]
    assert [r["id"] for r in search_notes(paths, "", [], until=NOW - 2000)] == ["stale"]


def test_date_field_switches_which_timestamp_is_compared(paths):
    """側欄「日期」旁的切換鈕:同一個時間窗,比 updated 與比 created 要選到不同的名詞。

    只斷言「有結果」不夠——參數沒接上時兩次都會回傳 updated 的答案,而那完全不
    報錯。所以刻意造兩筆 created/updated 錯開的名詞,要求兩次的答案互斥。
    """
    _seed(
        paths,
        # 半年前寫下、昨天才改
        _note("old_new", "apple", created=NOW - 180 * 86400, updated=NOW - 86400),
        # 昨天才建、但 updated 停在半年前(人為,只為了把兩欄分開)
        _note("new_old", "banana", created=NOW - 86400, updated=NOW - 180 * 86400),
    )
    win = {"since": NOW - 7 * 86400, "until": NOW + 86400}

    assert [r["id"] for r in search_notes(paths, "", [], **win)] == ["old_new"]  # 預設 updated
    assert [r["id"] for r in search_notes(paths, "", [], **win, date_field="updated")] == ["old_new"]
    assert [r["id"] for r in search_notes(paths, "", [], **win, date_field="created")] == ["new_old"]

    # days 是同一條時間軸的另一種指定方式,必須跟著換欄位(見 _filters 的 docstring)
    assert [r["id"] for r in search_notes(paths, "", [], days=7)] == ["old_new"]
    assert [r["id"] for r in search_notes(paths, "", [], days=7, date_field="created")] == ["new_old"]


def test_unknown_date_field_falls_back_to_updated(paths):
    """date_field 來自網址,是被內插進 SQL 的字面值——白名單以外一律退回 updated。

    ⚠ 這支守的是注入面:`date_col()` 一旦改成「把參數直接串進 SQL」,下面那幾個
    值就會變成可執行的片段,而且正常操作永遠不會踩到、測不出來。
    """
    _seed(
        paths,
        _note("old_new", "apple", created=NOW - 180 * 86400, updated=NOW - 86400),
        _note("new_old", "banana", created=NOW - 86400, updated=NOW - 180 * 86400),
    )
    win = {"since": NOW - 7 * 86400, "until": NOW + 86400}
    for bad in ("", "CREATED", "name", "created; DROP TABLE notes;--", "n.created"):
        assert [r["id"] for r in search_notes(paths, "", [], **win, date_field=bad)] == ["old_new"], bad
    # 表還在(注入沒有生效)
    assert len(search_notes(paths, "", [])) == 2


def test_index_delete_removes_note_from_search_results(paths):
    _seed(paths, _note("n1", "apple"), _note("n2", "banana"))
    conn = db(paths)
    index_delete(conn, "n1")
    conn.commit()
    conn.close()
    results = search_notes(paths, "", [])
    assert [r["id"] for r in results] == ["n2"]


def _marked(nid, name, **kw):
    n = _note(nid, name, **kw)
    n["marked"] = True
    return n


def test_marked_filter_applies_in_all_three_query_branches(paths):
    """書籤篩選要接上空查詢/FTS/LIKE 三個查詢分支(_pjoin 三處都得接)。"""
    _seed(paths, _marked("n1", "quicksort"), _note("n2", "quickselect"),
          _marked("n3", "go"), _note("n4", "golang"))
    # 空查詢分支;不帶 marked 時全部都要回(篩選預設關閉)
    assert {r["id"] for r in search_notes(paths, "", [], marked=True)} == {"n1", "n3"}
    assert len(search_notes(paths, "", [])) == 4
    # 3 字元以上走 FTS5 trigram 分支
    assert [r["id"] for r in search_notes(paths, "quick", [], marked=True)] == ["n1"]
    # 2 字元的短 token 走 LIKE 全掃分支
    assert [r["id"] for r in search_notes(paths, "go", [], marked=True)] == ["n3"]


# --- 大量資料:篩選/排序/分頁都必須在 SQL 端做,不能只看「最近的一批」 -------------
#
# 這幾個測試守著整套搜尋最危險的失敗模式:靜默給出殘缺結果。舊版實作是「SQL 撈候選
# 前 500 筆 → Python 端後置篩選」,一旦名詞超過 500 筆,冷門標籤/樣板篩不到舊資料、
# sort=created 排錯、offset≥500 翻不到頁——全都不報錯,使用者不會知道。

BULK = 520  # 略多於舊版那個 500 筆候選上限


def _seed_large(paths, target, filler_name="term"):
    """target(通常是最舊的一筆)+ BULK 筆比它新的填充名詞。

    filler_name 讓填充名詞也能一起命中搜尋關鍵字,好把 FTS/LIKE 分支的候選數量
    一樣推過 500(否則那兩個分支的候選太少,測不到上限)。
    """
    fillers = [_note(f"n{i}", f"{filler_name}{i}", updated=NOW - i) for i in range(BULK)]
    _seed(paths, *fillers, target)


def test_every_filter_finds_a_note_far_beyond_the_first_500(paths):
    target = _marked("old", "最舊的那一筆", template="code-snippet",
                     tags=["rare-a", "rare-b"], updated=NOW - 9999)
    _seed_large(paths, target)

    assert [r["id"] for r in search_notes(paths, "", ["rare-a"])] == ["old"]
    assert [r["id"] for r in search_notes(paths, "", ["rare-a", "rare-b"])] == ["old"]
    assert [r["id"] for r in search_notes(paths, "", [], any_tags=["rare-b"])] == ["old"]
    assert [r["id"] for r in search_notes(paths, "", [], template="code-snippet")] == ["old"]
    assert [r["id"] for r in search_notes(paths, "", [], until=NOW - 5000)] == ["old"]
    assert [r["id"] for r in search_notes(paths, "", [], marked=True)] == ["old"]


def test_filters_still_reach_old_notes_in_the_fts_and_like_branches(paths):
    # 填充名詞也一起命中關鍵字,讓這兩個分支的候選數量同樣超過 500
    target = _note("old", "quicksort 最舊的一筆", tags=["rare"], updated=NOW - 9999)
    _seed_large(paths, target, filler_name="quicksort最舊")
    assert [r["id"] for r in search_notes(paths, "quicksort", ["rare"])] == ["old"]  # FTS 分支
    assert [r["id"] for r in search_notes(paths, "最舊", ["rare"])] == ["old"]       # LIKE 分支


def test_sort_by_created_is_not_skewed_by_the_updated_order(paths):
    # 建立時間最新、但很久沒編輯的名詞:依 updated 取候選會漏掉它
    target = _note("fresh-but-stale", "很久沒動的新名詞", updated=NOW - 9999, created=NOW + 1)
    _seed_large(paths, target)
    assert search_notes(paths, "", [], sort="created")[0]["id"] == "fresh-but-stale"
    # 依 updated 排的話它是最後一筆,不該冒到前面
    assert search_notes(paths, "", [])[0]["id"] != "fresh-but-stale"


def test_pagination_reaches_past_the_500th_note_and_pages_stay_disjoint(paths):
    _seed(paths, *[_note(f"n{i}", f"term{i}", updated=NOW - i) for i in range(BULK)])
    page = search_notes(paths, "", [], limit=50, offset=500)
    assert [r["id"] for r in page] == [f"n{i}" for i in range(500, BULK)]
    # 小 offset 的翻頁:順序穩定、互斥、超出範圍回空
    assert [r["id"] for r in search_notes(paths, "", [], limit=2, offset=0)] == ["n0", "n1"]
    assert [r["id"] for r in search_notes(paths, "", [], limit=2, offset=2)] == ["n2", "n3"]
    assert search_notes(paths, "", [], limit=2, offset=BULK) == []


def test_name_hit_sorts_before_description_only_hit(paths):
    # 名稱直接命中者優先,其餘才依日期新到舊(ORDER BY 下推到 SQL 之後仍要成立)
    _seed(
        paths,
        _note("desc-hit", "banana", description="a quicksort variant", updated=NOW),
        _note("name-hit", "quicksort", updated=NOW - 100),
    )
    assert [r["id"] for r in search_notes(paths, "quicksort", [])] == ["name-hit", "desc-hit"]

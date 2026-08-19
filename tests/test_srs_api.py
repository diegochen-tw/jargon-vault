"""SRS 走 HTTP 的部分(app/routers/srs.py)。

證明各層真的接起來,外加四件只有在 router 層才驗得到的事:
  * 複習是**不動 updated、不寫歷史版本**的專用端點(理由同書籤標記)
  * 抽卡池不可能繞過篩選(對應 test_semantic.py 那支同名精神的測試)
  * 使用者之間的複習進度是隔離的
  * `/api/search` 完全不受影響
"""
import time

from app.indexer import db, index_upsert
from app.srs import DRAW_SIZE, INTERVALS, MAX_DRAW_SIZE, MIN_DRAW_SIZE
from app.storage import dump_note, note_path, read_note_file


def _make(u, nid, name, tags=None, template="jargon-default"):
    r = u["client"].post("/api/notes", json={
        "id": nid, "name": name, "description": f"{name} 的說明", "template": template,
        "fields": {}, "tags": tags or [], "attachments": [],
    })
    assert r.status_code == 200, r.text
    return r.json()


def _age(u, nid, days):
    """把一筆名詞的 updated 往回推,並同步索引——用來造出「久未觸碰」的資料。

    刻意繞過 API:沒有端點可以偽造過去的編輯時間(那正是它該有的樣子)。
    """
    p = u["paths"]
    note = read_note_file(note_path(p, nid))
    note["updated"] = time.time() - days * 86400
    note_path(p, nid).write_text(dump_note(note), encoding="utf-8")
    conn = db(p)
    try:
        index_upsert(conn, note)
        conn.commit()
    finally:
        conn.close()


# ── 抽卡 ────────────────────────────────────────────────────────────

def test_draw_basics_empty_vault_and_trash(register_user):
    """抽卡的基本形狀:空庫不是錯誤、回 cards+pool、未複習的卡不帶盒序;
    刪除(搬進回收桶)的名詞跟搜尋一樣看不到,還原後又回來。"""
    u = register_user()
    d = u["client"].get("/api/srs/draw").json()
    assert d == {"cards": [], "pool": 0, "scope": d["scope"]}

    for i in range(3):
        _make(u, f"n{i}", f"名詞{i}")
    d = u["client"].get("/api/srs/draw").json()
    assert d["pool"] == 3
    assert sorted(c["name"] for c in d["cards"]) == ["名詞0", "名詞1", "名詞2"]
    assert all(c["srs_box"] is None for c in d["cards"])

    u["client"].delete("/api/notes/n1")
    d = u["client"].get("/api/srs/draw").json()
    assert sorted(c["id"] for c in d["cards"]) == ["n0", "n2"]
    assert d["pool"] == 2
    u["client"].post("/api/trash/n1/restore")
    assert sorted(c["id"] for c in u["client"].get("/api/srs/draw").json()["cards"]) \
        == ["n0", "n1", "n2"]


def test_draw_is_capped_and_size_is_clamped(register_user):
    """零負債:一輪就是一輪,不會把整庫倒出來。一輪幾張由使用者決定
    (設定 → 偏好設定),但夾在上下限之間——可調的是「一輪多長」,
    零負債的三個「不」(不催、不累積、不顯示欠款數字)沒有被放寬。"""
    u = register_user()
    for i in range(MAX_DRAW_SIZE + 10):
        _make(u, f"n{i:03d}", f"名詞{i}")

    d = u["client"].get("/api/srs/draw").json()
    assert d["pool"] == MAX_DRAW_SIZE + 10
    assert len(d["cards"]) == DRAW_SIZE

    assert len(u["client"].get("/api/srs/draw?size=5").json()["cards"]) == 5
    # 0 / 壞值 → 預設值(絕不能變成 0 張,那看起來就是壞掉)
    assert len(u["client"].get("/api/srs/draw?size=0").json()["cards"]) == DRAW_SIZE
    # 超出範圍一律夾住,不是拒絕(這是偏好設定不是輸入驗證)
    assert len(u["client"].get("/api/srs/draw?size=999").json()["cards"]) == MAX_DRAW_SIZE
    assert len(u["client"].get("/api/srs/draw?size=1").json()["cards"]) == MIN_DRAW_SIZE


# ── ★ 篩選絕不被抽卡排序繞過 ────────────────────────────────────────

def test_filters_are_never_bypassed_by_srs_ordering(register_user):
    """srs_due 再早的名詞,只要被篩選擋掉就**絕不**出現在抽卡結果裡。

    這是這個檔案裡最重要的一支。抽卡的排序軸(srs_due)跟篩選是兩件獨立的事,
    很容易寫成「先按到期排好再篩」——那樣候選集不保證含正確答案,而且完全
    不會報錯,只會表現成「複習到不該複習的東西」。
    """
    u = register_user()
    _make(u, "ancient", "很久沒碰", tags=["別的"])
    _make(u, "recent", "剛記的", tags=["要複習"])
    _age(u, "ancient", 999)

    d = u["client"].get("/api/srs/draw?tags=要複習").json()
    assert [c["id"] for c in d["cards"]] == ["recent"]
    assert d["pool"] == 1


def test_group_filter_expands_to_its_tags_and_unknown_group_draws_nothing(register_user):
    u = register_user()
    _make(u, "a", "甲", tags=["前端"])
    _make(u, "b", "乙", tags=["後端"])
    _make(u, "c", "丙", tags=["雜"])
    u["client"].put("/api/tag-groups", json={"group": "技術", "tags": ["前端", "後端"]})

    d = u["client"].get("/api/srs/draw?group=技術").json()
    assert sorted(c["id"] for c in d["cards"]) == ["a", "b"]
    assert u["client"].get("/api/srs/draw?group=不存在").json()["cards"] == []


def test_template_and_marked_filters_apply(register_user):
    u = register_user()
    _make(u, "a", "甲", template="english-word")
    _make(u, "b", "乙")
    u["client"].put("/api/notes/b/mark", json={"marked": True})

    assert [c["id"] for c in u["client"].get(
        "/api/srs/draw?template=english-word").json()["cards"]] == ["a"]
    assert [c["id"] for c in u["client"].get("/api/srs/draw?marked=1").json()["cards"]] == ["b"]


# ── 自評 ────────────────────────────────────────────────────────────

def test_review_advances_the_box_and_forgetting_resets_to_zero(register_user):
    u = register_user()
    _make(u, "n1", "甲")
    before = time.time()
    got = u["client"].post("/api/srs/n1/review", json={"remembered": True}).json()
    assert got["srs_box"] == 1
    assert got["srs_due"] >= before + INTERVALS[1] * 86400

    for _ in range(2):
        u["client"].post("/api/srs/n1/review", json={"remembered": True})
    assert u["client"].post("/api/srs/n1/review", json={"remembered": False}).json()["srs_box"] == 0


def test_review_does_not_touch_updated_or_write_history(register_user):
    """複習不是內容編輯。bump updated 會把「依上次編輯時間」的排序整個打亂,
    寫歷史版本則會用複習紀錄把 HISTORY_LIMIT=3 的真實編輯歷史沖光。
    順帶守著:`/api/search` 完全不受複習影響。"""
    u = register_user()
    before = _make(u, "n1", "甲")
    search_before = u["client"].get("/api/search").json()

    after = u["client"].post("/api/srs/n1/review", json={"remembered": True}).json()
    assert after["updated"] == before["updated"]
    assert after["history"] == []

    search_after = u["client"].get("/api/search").json()
    assert [r["id"] for r in search_after["results"]] == \
        [r["id"] for r in search_before["results"]]
    assert search_after["results"][0]["updated"] == search_before["results"][0]["updated"]


def test_editing_a_note_preserves_its_review_progress(register_user):
    """NoteIn 不含 srs 欄位,api_update 的 old.update() 因此原樣保留進度——
    不必每個前端呼叫端各自記得帶(漏一個就靜默清掉半年的複習)。"""
    u = register_user()
    _make(u, "n1", "甲")
    u["client"].post("/api/srs/n1/review", json={"remembered": True})
    r = u["client"].put("/api/notes/n1", json={
        "name": "甲改", "description": "改過了", "template": "jargon-default",
        "fields": {}, "tags": [], "attachments": [],
    })
    assert r.json()["srs_box"] == 1


# ── 使用者隔離與登入 ────────────────────────────────────────────────

def test_review_progress_is_isolated_between_users(register_user):
    a, b = register_user(), register_user()
    _make(a, "same", "同 id")
    _make(b, "same", "同 id")
    a["client"].post("/api/srs/same/review", json={"remembered": True})

    assert a["client"].get("/api/srs/draw").json()["cards"][0]["srs_box"] == 1
    assert b["client"].get("/api/srs/draw").json()["cards"][0]["srs_box"] is None


def test_endpoints_require_login_and_reject_bad_ids(client, register_user):
    assert client.get("/api/srs/draw").status_code == 401
    assert client.post("/api/srs/x/review", json={"remembered": True}).status_code == 401

    u = register_user()
    assert u["client"].post("/api/srs/../etc/review",
                            json={"remembered": True}).status_code in (400, 404)
    assert u["client"].post("/api/srs/nope/review",
                            json={"remembered": True}).status_code == 404

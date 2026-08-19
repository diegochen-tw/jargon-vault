"""
個人狀態外移(app/progress.py + indexer 的 progress 投影表)。

`marked` / `srs_box` / `srs_due` 從名詞的 `.md` frontmatter 搬到個人側的
`progress.json`,以 `(vault_id, note_id)` 為鍵。守的是四件事:

  1. **`.md` 絕不再帶這三個 key**——團隊庫的 .md 是全隊共讀共寫的,
     寫進去 = 我的書籤全隊看得到、我複習一輪改掉全隊的排程
  2. **對外形狀完全不變**——API 回傳的名詞照樣有這三個欄位,前端一行都不用改
  3. **舊 .md 的值不會消失**——啟動時一次性收進 progress.json(懶遷移,不改寫舊檔)
  4. **回收桶還原後進度要回來**——刪除不是永久刪除
"""
import json
import time

import pytest

from app import progress
from app.indexer import db, rebuild_index
from app.paths import user_paths
from app.service import load_note_or_404, persist_note, save_progress_entry
from app.search import due_notes, search_notes
from app.storage import note_path


@pytest.fixture(autouse=True)
def _indexed(paths):
    rebuild_index(paths)


def _note(nid, name="apple", **kw):
    ts = kw.pop("updated", time.time())
    return {"id": nid, "name": name, "description": "desc", "template": "jargon-default",
            "fields": {}, "tags": kw.pop("tags", []), "attachments": [], "marked": False,
            "srs_box": None, "srs_due": None,
            "created": ts, "updated": ts, "history": [], **kw}


# ── 真相檔本身 ──────────────────────────────────────────────────────

def test_roundtrip_and_missing_file(paths):
    """檔案不存在 = 還沒有任何進度,不是錯誤,也不建空檔;存了讀得回來。"""
    assert progress.load_progress(paths) == {}
    assert not paths.progress_path.exists()
    assert progress.get_entry(paths, "v1", "nope") == progress.blank_entry()

    progress.save_progress(paths, {"v1": {"n1": {"marked": True, "srs_box": 2, "srs_due": 9.0}}})
    assert progress.get_entry(paths, "v1", "n1") == {"marked": True, "srs_box": 2, "srs_due": 9.0}


def test_entries_with_nothing_in_them_are_not_stored(paths):
    """★ 沒有進度的名詞**不留一筆全預設值的殼**。

    留了就等於把 srs_due 物化成具體值,那筆名詞會永遠卡在複習佇列最前面——
    與 storage.dump_note() 當初條件式寫出 srs 欄位是同一個理由。
    """
    save_progress_entry(paths, "n1", marked=True)
    save_progress_entry(paths, "n1", marked=False)
    assert progress.load_progress(paths) == {}


def test_garbage_entries_are_skipped_not_fatal(paths):
    """一筆壞掉不該讓使用者失去其他幾百筆的進度;而且沒複習過(srs_box 缺)
    就不該有到期時間——否則等於物化 srs_due。"""
    paths.progress_path.write_text(
        '{"v1": {"ok": {"marked": true}, "bad": "not-a-dict", "worse": {"srs_box": "x"}}}',
        encoding="utf-8")
    data = progress.load_progress(paths)
    assert data["v1"]["ok"]["marked"] is True
    assert "bad" not in data["v1"]
    assert "worse" not in data["v1"]  # srs_box 壞掉 + 沒 marked → 整筆沒內容

    progress.save_progress(paths, {"v1": {"n1": {"marked": True, "srs_box": None, "srs_due": 5.0}}})
    assert progress.get_entry(paths, "v1", "n1")["srs_due"] is None


def test_progress_is_keyed_per_vault(paths):
    """同一個 note id 在不同庫底下是兩筆進度——團隊庫要靠這個維度隔離。"""
    save_progress_entry(paths, "n1", marked=True)
    progress.save_progress(paths, progress.load_progress(paths) |
                           {"team-x": {"n1": {"marked": False, "srs_box": 3, "srs_due": 1.0}}})
    assert progress.get_entry(paths, paths.vault_id, "n1")["marked"] is True
    assert progress.get_entry(paths, paths.vault_id, "n1")["srs_box"] is None
    assert progress.get_entry(paths, "team-x", "n1")["srs_box"] == 3


# ── 與名詞的接合 ────────────────────────────────────────────────────

def test_md_never_contains_the_three_keys(paths):
    """★ 本次改動的核心不變式。"""
    n = _note("n1", marked=True, srs_box=3, srs_due=time.time() + 999)
    persist_note(paths, n)
    raw = note_path(paths, "n1").read_text(encoding="utf-8")
    for key in ("marked", "srs_box", "srs_due"):
        assert key not in raw


def test_api_shape_is_unchanged(paths):
    """對外形狀不變:讀回來的名詞照樣有這三個欄位,前端一行都不用改;
    從沒複習過的則退回 srs_due == updated(缺值退回,不是第二種模式)。"""
    persist_note(paths, _note("n1"))
    persist_note(paths, _note("n2"))
    save_progress_entry(paths, "n1", marked=True, srs_box=2, srs_due=123.0)
    got = load_note_or_404(paths, "n1")
    assert got["marked"] is True
    assert got["srs_box"] == 2
    assert got["srs_due"] == 123.0

    virgin = load_note_or_404(paths, "n2")
    assert virgin["srs_box"] is None
    assert virgin["srs_due"] == virgin["updated"]


def test_marked_filter_works_through_the_join(paths):
    """★ 書籤篩選改走 progress 表的 LEFT JOIN,三個查詢分支都得接上——
    少接一處,那個分支的篩選會安靜地全篩不到。"""
    persist_note(paths, _note("n1", name="quicksort"))
    persist_note(paths, _note("n2", name="quickselect"))
    save_progress_entry(paths, "n1", marked=True)

    assert [n["id"] for n in search_notes(paths, "", [], marked=True)] == ["n1"]     # 空查詢
    assert [n["id"] for n in search_notes(paths, "quick", [], marked=True)] == ["n1"]  # FTS
    assert [n["id"] for n in search_notes(paths, "qu", [], marked=True)] == ["n1"]     # LIKE


def test_due_ordering_uses_progress(paths):
    now = time.time()
    persist_note(paths, _note("old", updated=now - 100 * 86400))
    persist_note(paths, _note("new", updated=now))
    # 把最舊的那筆複習過 → 它的 due 推到未來,應該沉到隊尾
    save_progress_entry(paths, "old", srs_box=3, srs_due=now + 30 * 86400)
    assert [n["id"] for n in due_notes(paths, [])] == ["new", "old"]


# ── 懶遷移:舊 .md 的值不會消失 ────────────────────────────────────

def test_legacy_md_values_are_migrated_on_rebuild(paths):
    """★ 既有使用者的書籤與複習進度不能因為這次改動而歸零。
    進度檔才是新的真相:舊 .md 裡的殘留值只補、不覆蓋;而且冪等。"""
    note_path(paths, "legacy").write_text(
        "---\nid: legacy\nname: 舊檔\ntemplate: jargon-default\nfields: {}\n"
        "tags: []\nattachments: []\nmarked: true\nsrs_box: 3\nsrs_due: 777.0\n"
        "created: 1.0\nupdated: 2.0\nhistory: []\n---\n\n內文\n", encoding="utf-8")
    note_path(paths, "legacy2").write_text(
        "---\nid: legacy2\nname: 舊檔2\ntemplate: jargon-default\nfields: {}\n"
        "tags: []\nattachments: []\nmarked: true\nsrs_box: 0\nsrs_due: 1.0\n"
        "created: 1.0\nupdated: 2.0\nhistory: []\n---\n\n內文\n", encoding="utf-8")
    save_progress_entry(paths, "legacy2", srs_box=5, srs_due=999.0)
    rebuild_index(paths)

    entry = progress.get_entry(paths, paths.vault_id, "legacy")
    assert entry["marked"] is True
    assert entry["srs_box"] == 3
    assert entry["srs_due"] == 777.0
    assert load_note_or_404(paths, "legacy")["srs_box"] == 3
    # 只補不覆蓋:進度檔既有的值贏過 .md 的殘留值
    assert progress.get_entry(paths, paths.vault_id, "legacy2")["srs_box"] == 5
    # 冪等:再跑一次不變
    before = progress.load_progress(paths)
    rebuild_index(paths)
    assert progress.load_progress(paths) == before


# ── 孤兒回收與刪除語意 ──────────────────────────────────────────────

def test_trashed_notes_keep_their_progress(paths):
    """★ 回收桶裡的名詞是**可以還原**的,重啟不能把它的複習進度清掉——
    只對照 notes/ 而不管 trash/ 的話,還原回來就變成從沒複習過。
    指不到任何檔案的孤兒進度則被回收;危險區的批次刪除是**永久**刪除
    (不經回收桶),進度該當場一起清掉。"""
    from app import service
    persist_note(paths, _note("n1"))
    save_progress_entry(paths, "n1", srs_box=4, srs_due=888.0)
    save_progress_entry(paths, "ghost", marked=True)   # 沒有對應檔案的孤兒
    service.delete_note(paths, "n1")          # 搬進回收桶

    rebuild_index(paths)
    assert progress.get_entry(paths, paths.vault_id, "n1")["srs_box"] == 4
    assert progress.get_entry(paths, paths.vault_id, "ghost") == progress.blank_entry()

    service.restore_from_trash(paths, "n1")
    assert load_note_or_404(paths, "n1")["srs_box"] == 4

    service.delete_all_notes(paths)
    assert progress.get_entry(paths, paths.vault_id, "n1") == progress.blank_entry()


# ── 合併 ────────────────────────────────────────────────────────────

def test_merge_entries_takes_the_least_familiar_side():
    """合併取最低/最早;None(從沒複習過)在「取最低」的語意下就是最低。"""
    merged = progress.merge_entries([
        {"marked": False, "srs_box": 4, "srs_due": 900.0},
        {"marked": True, "srs_box": 1, "srs_due": 300.0},
    ])
    assert merged == {"marked": True, "srs_box": 1, "srs_due": 300.0}

    merged = progress.merge_entries([
        {"marked": False, "srs_box": 5, "srs_due": 900.0},
        progress.blank_entry(),
    ])
    assert merged["srs_box"] is None
    assert merged["srs_due"] is None


# ── 使用者隔離 ──────────────────────────────────────────────────────

def test_progress_never_leaks_between_users(register_user):
    a, b = register_user(), register_user()
    r = a["client"].post("/api/notes", json={"name": "SFC"})
    nid = r.json()["id"]
    assert a["client"].put(f"/api/notes/{nid}/mark", json={"marked": True}).status_code == 200

    assert progress.get_entry(a["paths"], a["id"], nid)["marked"] is True
    assert progress.get_entry(b["paths"], b["id"], nid) == progress.blank_entry()
    assert not user_paths(b["id"]).progress_path.exists()


# ── 走 HTTP:兩支專用端點都不碰 .md ────────────────────────────────

def test_mark_and_review_endpoints_do_not_rewrite_the_md(register_user):
    """兩支專用端點都不碰 .md;而一般編輯的 payload 不帶進度,
    也不該把它清掉(理由同 marked 不進 NoteIn)。"""
    u = register_user()
    c = u["client"]
    nid = c.post("/api/notes", json={"name": "SFC"}).json()["id"]
    before = note_path(u["paths"], nid).read_text(encoding="utf-8")

    r = c.put(f"/api/notes/{nid}/mark", json={"marked": True})
    assert r.status_code == 200
    assert r.json()["marked"] is True
    r = c.post(f"/api/srs/{nid}/review", json={"remembered": True})
    assert r.status_code == 200
    assert r.json()["srs_box"] == 1
    assert note_path(u["paths"], nid).read_text(encoding="utf-8") == before

    # 一般編輯保留進度
    c.put(f"/api/notes/{nid}", json={"name": "SFC", "description": "改內容"})
    got = c.get(f"/api/notes/{nid}").json()
    assert got["srs_box"] == 1
    assert got["marked"] is True


def test_progress_survives_export_import_roundtrip(register_user):
    """複習進度不在 .md 裡,匯出入要另外帶——不帶就是換機一次歸零。"""
    u = register_user()
    c = u["client"]
    nid = c.post("/api/notes", json={"name": "SFC"}).json()["id"]
    c.put(f"/api/notes/{nid}/mark", json={"marked": True})
    c.post(f"/api/srs/{nid}/review", json={"remembered": True})

    payload = c.get("/api/export?format=json").json()
    exported = next(n for n in payload["notes"] if n["id"] == nid)
    assert exported["marked"] is True
    assert exported["srs_box"] == 1

    c.delete(f"/api/notes/{nid}")
    c.delete(f"/api/trash/{nid}")
    blob = json.dumps(payload).encode("utf-8")
    r = c.post("/api/import", files={"file": ("backup.json", blob, "application/json")})
    assert r.status_code == 200, r.text
    got = c.get(f"/api/notes/{nid}").json()
    assert got["marked"] is True
    assert got["srs_box"] == 1


def test_index_and_file_read_agree_after_a_review(register_user):
    """索引(JOIN)與讀檔(attach_progress)必須給同一個答案。"""
    u = register_user()
    c = u["client"]
    nid = c.post("/api/notes", json={"name": "SFC"}).json()["id"]
    c.post(f"/api/srs/{nid}/review", json={"remembered": True})

    from_api = c.get(f"/api/notes/{nid}").json()
    from_search = next(n for n in c.get("/api/search").json()["results"] if n["id"] == nid)
    assert from_api["srs_box"] == from_search["srs_box"] == 1
    assert from_api["srs_due"] == pytest.approx(from_search["srs_due"])

    conn = db(u["paths"])
    try:
        row = conn.execute("SELECT * FROM progress WHERE vault_id=? AND note_id=?",
                           (u["id"], nid)).fetchone()
    finally:
        conn.close()
    assert row["srs_box"] == 1

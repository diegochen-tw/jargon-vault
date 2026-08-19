"""SRS 間隔重複複習的純函式層與檔案層(app/srs.py + storage/indexer/service 的落點)。

走 HTTP 的部分在 test_srs_api.py。這裡守的是四件「壞了不會報錯,只會安靜地
給出爛行為」的事:
  1. 缺值退回 updated 的懶遷移(舊 .md 一行都不用改)
  2. **複習進度不進 .md**——它是「我跟名詞的關係」,住在個人側的 progress.json
     (見 app/progress.py)。寫進 .md 的話,團隊庫裡我複習一輪就改掉全隊的排程
  3. 複習進度不是版本化內容(不進歷史快照、不被版本回復覆寫)
  4. 索引與「重新讀一次檔」必須給同一個答案
"""
import time

import pytest

from app import progress, srs
from app.indexer import db, rebuild_index
from app.service import load_note_or_404, merge_notes, persist_note, save_progress_entry
from app.search import due_notes
from app.storage import dump_note, note_path, read_note_file, snapshot_of


@pytest.fixture(autouse=True)
def _indexed(paths):
    rebuild_index(paths)


def _note(nid, name="apple", tags=None, age_days=0.0, box=None, due=None) -> dict:
    ts = time.time() - age_days * 86400
    return {
        "id": nid, "name": name, "description": "desc", "template": "jargon-default",
        "fields": {}, "tags": tags or [], "attachments": [], "marked": False,
        "srs_box": box, "srs_due": due,
        "created": ts, "updated": ts, "history": [],
    }


def _persist(paths, note):
    """寫一筆名詞,並把它的複習進度落到個人進度檔。

    `persist_note` 只寫 .md 與索引;srs_box/srs_due **不在 .md 裡**(見模組
    docstring 第 2 點),所以要另外走 save_progress_entry——那正是
    `/api/srs/{nid}/review` 走的同一條路。
    """
    persist_note(paths, note)
    if note.get("srs_box") is not None or note.get("marked"):
        save_progress_entry(paths, note["id"], marked=bool(note.get("marked")),
                            srs_box=note.get("srs_box"), srs_due=note.get("srs_due"))
    return note


def _effective(paths, nid) -> dict:
    """使用者實際會看到的那一筆(讀檔 + 貼回個人狀態),等同 API 的回傳。"""
    return load_note_or_404(paths, nid)


# ── Leitner 排程規則 ────────────────────────────────────────────────

def test_leitner_transitions_are_capped_and_clamped():
    """記得升一格用新格間隔;box=None 不是特例就是 box 0;封頂在最後一格;
    .md 是使用者可以拿文字編輯器改的真相檔,亂填的 box 夾住不炸。"""
    now = 1_000_000.0
    box, due = srs.next_state(0, True, now)
    assert box == 1
    assert due == now + srs.INTERVALS[1] * 86400
    # None(從沒複習過)就是 box 0
    assert srs.next_state(None, True, 0.0) == srs.next_state(0, True, 0.0)
    # 封頂
    box, due = srs.next_state(srs.MAX_BOX, True, 0.0)
    assert box == srs.MAX_BOX
    assert due == srs.INTERVALS[srs.MAX_BOX] * 86400
    # 超界夾住不爆
    assert srs.next_state(999, True, 0.0)[0] == srs.MAX_BOX
    assert srs.next_state(-5, True, 0.0)[0] == 1


def test_forgot_resets_to_box_zero_and_asks_again_tomorrow():
    """答錯一律回 box 0,不做「降一格」的漸進退讓——想不起來就是想不起來。"""
    box, due = srs.next_state(srs.MAX_BOX, False, 0.0)
    assert box == 0
    assert due == srs.INTERVALS[0] * 86400


# ── 懶遷移:缺值退回 updated ────────────────────────────────────────

def test_never_reviewed_note_writes_no_srs_keys_and_falls_back_to_updated(paths):
    """沒複習過就不該在 .md 裡留下痕跡——既是為了檔案好讀,也是為了正確性:
    物化成當下的 updated 之後,每次編輯 updated 前進、srs_due 卻凍住,那筆
    名詞會永遠卡在複習佇列最前面(見 test_editing_pushes_note_to_back_of_queue)。
    讀檔端的補值:srs_due 缺值退回 updated。"""
    raw = dump_note(_note("n1"))
    assert "srs_box" not in raw
    assert "srs_due" not in raw

    persist_note(paths, _note("n1", age_days=30))
    got = read_note_file(note_path(paths, "n1"))
    assert got["srs_box"] is None
    assert got["srs_due"] == got["updated"]


def test_reviewed_state_and_marked_live_in_progress_not_in_the_md(paths):
    """★ 複習進度與書籤**絕不寫進 .md**。

    團隊庫的 .md 是全隊共讀共寫的:進度寫進去 = 我複習一輪就改掉全隊的排程、
    兩個人同時複習互相覆蓋;書籤寫進去 = 我的書籤全隊看得到。真相在個人側的
    progress.json(見 app/progress.py),兩者是同一類東西、走同一條路。
    """
    n = _note("n1")
    n["srs_box"], n["srs_due"] = srs.next_state(None, True)
    n["marked"] = True
    _persist(paths, n)

    raw = note_path(paths, "n1").read_text(encoding="utf-8")
    assert "srs_box" not in raw
    assert "srs_due" not in raw
    assert "marked" not in raw

    entry = progress.get_entry(paths, paths.vault_id, "n1")
    assert entry["srs_box"] == 1
    assert entry["srs_due"] > n["updated"]
    assert entry["marked"] is True

    # 對外的形狀完全不變:API 回傳的那一筆照樣帶著進度
    got = _effective(paths, "n1")
    assert got["srs_box"] == 1
    assert got["srs_due"] > got["updated"]
    assert got["marked"] is True


# ── 排序:久未觸碰優先,是 ORDER BY srs_due 的自然結果 ──────────────

def test_queue_orders_by_how_long_untouched_and_reviewed_drop_behind(paths):
    """未複習的依「多久沒碰」排;複習過的與沒複習過的在同一條時間軸上比,
    不需要任何特判。"""
    for nid, age in [("fresh", 1), ("old", 100), ("mid", 50)]:
        persist_note(paths, _note(nid, name=nid, age_days=age))
    reviewed = _note("done", age_days=200)
    reviewed["srs_box"], reviewed["srs_due"] = srs.next_state(None, True)
    _persist(paths, reviewed)
    assert [n["id"] for n in due_notes(paths, [])] == ["old", "mid", "fresh", "done"]


def test_editing_pushes_note_to_back_of_queue(paths):
    """編輯過的名詞你才剛看過,不該還排在「最該複習」的第一位。

    這是條件式寫出換來的性質:未複習的 srs_due 始終跟著 updated 走。
    """
    for nid, age in [("a", 100), ("b", 90), ("c", 80)]:
        persist_note(paths, _note(nid, age_days=age))
    assert [n["id"] for n in due_notes(paths, [])][0] == "a"

    edited = read_note_file(note_path(paths, "a"))
    edited.update(description="edited", updated=time.time())
    persist_note(paths, edited)
    assert [n["id"] for n in due_notes(paths, [])] == ["b", "c", "a"]


def test_index_srs_due_never_disagrees_with_a_fresh_file_read(paths):
    """索引與「重新讀一次檔」必須給同一個答案。

    寫入路徑上的 note dict 可能剛被 old.update(updated=now) 換過,dict 裡的
    srs_due 還是上次讀檔留下的舊值——只在其中一邊算一次的話,兩邊會不一致。

    外移之後這條規則變成:索引端是 `COALESCE(p.srs_due, n.updated)`、
    讀檔端是 `service.attach_progress()`,兩邊各算一次,答案必須相同。
    """
    _persist(paths, _note("n1", age_days=100))
    edited = read_note_file(note_path(paths, "n1"))
    edited.update(updated=time.time())
    persist_note(paths, edited)

    conn = db(paths)
    try:
        row = conn.execute(
            "SELECT COALESCE(p.srs_due, n.updated) AS srs_due, n.updated FROM notes n "
            "LEFT JOIN progress p ON p.vault_id=? AND p.note_id=n.id WHERE n.id='n1'",
            (paths.vault_id,)).fetchone()
    finally:
        conn.close()
    assert row["srs_due"] == row["updated"] == _effective(paths, "n1")["srs_due"]


def test_filters_still_apply_to_the_draw_pool(paths):
    """srs_due 再早的名詞,只要被標籤篩掉就絕不出現。"""
    persist_note(paths, _note("ancient", age_days=999, tags=["other"]))
    persist_note(paths, _note("recent", age_days=1, tags=["want"]))
    assert [n["id"] for n in due_notes(paths, ["want"])] == ["recent"]


# ── 複習進度不是版本化內容 ──────────────────────────────────────────

def test_snapshot_excludes_srs_state(paths):
    """版本回復不該把複習進度一起倒退回去(理由與 marked 相同)。"""
    n = _note("n1", box=3, due=time.time() + 999)
    snap = snapshot_of(n)
    assert "srs_box" not in snap
    assert "srs_due" not in snap


# ── 合併名詞:box 取最低、due 取最早 ────────────────────────────────

def test_merge_keeps_the_least_familiar_side_and_preserves_source_progress(paths):
    """box 取最低、due 取最早;被併掉的來源是搬進**回收桶**(可還原),
    進度不該當場消失——還原回來卻變成從沒複習過,等於靜默丟掉半年的進度。"""
    a = _note("a", name="A", box=4, due=time.time() + 30 * 86400)
    b = _note("b", name="B", box=1, due=time.time() + 3 * 86400)
    _persist(paths, a)
    _persist(paths, b)
    merge_notes(paths, "a", ["b"])
    got = _effective(paths, "a")
    assert got["srs_box"] == 1
    assert got["srs_due"] == pytest.approx(b["srs_due"])
    # 來源的進度原封不動
    assert progress.get_entry(paths, paths.vault_id, "b")["srs_box"] == 1


def test_merging_in_a_never_reviewed_note_resets_the_target(paths):
    """任一邊沒複習過 → 整筆回到未複習。None 在「取最低」的語意下就是最低。"""
    persist_note(paths, _note("a", name="A", box=5, due=time.time() + 90 * 86400))
    persist_note(paths, _note("b", name="B"))
    merge_notes(paths, "a", ["b"])
    got = read_note_file(note_path(paths, "a"))
    assert got["srs_box"] is None
    assert got["srs_due"] == got["updated"]
    assert "srs_box" not in note_path(paths, "a").read_text(encoding="utf-8")

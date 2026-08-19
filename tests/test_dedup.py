"""重複偵測(app/dedup.py)與合併(app/service.py:merge_notes)。

合併守著的核心性質是「不靜默丟資料」:被放棄的欄位值要被回報、被併掉的名詞
進回收桶而不是永久消失、指向它的連結要跟著改指過來。
"""
import time

import pytest

from app import dedup, search, service
from app import trash as trash_mod
from app.indexer import rebuild_index


def _note(nid, name, description="", fields=None, tags=None, attachments=None, marked=False,
          created=None):
    now = created or time.time()
    return {"id": nid, "name": name, "description": description,
            "template": "jargon-default", "fields": fields or {}, "tags": tags or [],
            "attachments": attachments or [], "marked": marked,
            "created": now, "updated": time.time(), "history": []}


@pytest.fixture
def vault(paths):
    rebuild_index(paths)

    def add(nid, name, **kw):
        n = _note(nid, name, **kw)
        service.persist_note(paths, n)
        # 書籤與複習進度不在 .md 裡(見 app/progress.py),persist_note 帶不走
        if n.get("marked") or n.get("srs_box") is not None:
            service.save_progress_entry(paths, nid, marked=bool(n.get("marked")),
                                        srs_box=n.get("srs_box"), srs_due=n.get("srs_due"))
        return n
    return paths, add


def _names(group):
    return sorted(n["name"] for n in group["notes"])


# ── 偵測 ──────────────────────────────────────────────────────────

def test_case_width_and_spacing_differences_are_the_same_name(vault):
    paths, add = vault
    add("a", "cSSFI123")
    add("b", "CSSFI 123")
    add("c", "ｃＳＳＦＩ１２３")
    groups = dedup.find_duplicate_groups(paths)
    assert len(groups) == 1
    assert groups[0]["reason"] == "same-name"
    assert _names(groups[0]) == ["CSSFI 123", "cSSFI123", "ｃＳＳＦＩ１２３"]


def test_punctuation_differences_are_reported_as_near_not_same(vault):
    paths, add = vault
    add("a", "cSSFI-123")
    add("b", "cSSFI.123")
    groups = dedup.find_duplicate_groups(paths)
    assert [g["reason"] for g in groups] == ["near"]


def test_a_term_recorded_under_someone_elses_alias_is_found(vault):
    """使用者最痛的那種重複:A 記了縮寫、B 用全稱記了同一件事,只比 name 抓不到。"""
    paths, add = vault
    add("a", "cSSFI123")
    add("b", "上料站前端程式", fields={"alias": "cSSFI123、LDR"})
    groups = dedup.find_duplicate_groups(paths)
    assert len(groups) == 1
    assert groups[0]["reason"] == "alias"
    assert _names(groups[0]) == ["cSSFI123", "上料站前端程式"]


def test_unrelated_notes_are_not_grouped(vault):
    paths, add = vault
    add("a", "cSSFI123")
    add("b", "cSSFI124")   # 差一個字在行話裡通常是不同的東西,不該報
    add("c", "完全無關")
    assert dedup.find_duplicate_groups(paths) == []


def test_a_group_reports_every_relation_that_holds_inside_it(vault):
    paths, add = vault
    add("a", "Loader")
    add("b", "LOADER")                                   # 同名
    add("c", "上料站", fields={"synonymy": "loader"})     # 靠別名連進來
    groups = dedup.find_duplicate_groups(paths)
    assert len(groups) == 1
    assert groups[0]["reasons"] == ["same-name", "alias"]
    assert len(groups[0]["notes"]) == 3


def test_similar_ignores_the_edited_note_and_meaninglessly_short_names(vault):
    paths, add = vault
    add("a", "Loader")
    add("b", "A")
    assert [s["id"] for s in dedup.find_similar(paths, "loader")] == ["a"]
    assert dedup.find_similar(paths, "loader", exclude_id="a") == []
    assert dedup.find_similar(paths, "a") == []   # 太短的名稱不比,沒有意義


# ── 合併 ──────────────────────────────────────────────────────────

def test_merge_combines_content_without_losing_anything(vault):
    paths, add = vault
    old = time.time() - 86400
    add("a", "Loader", description="上料站的程式", fields={"alias": "LDR"}, tags=["產線"])
    add("b", "loader", description="負責把料盤送進機台", fields={"en_term": "Loader"},
        tags=["設備"], created=old, marked=True)
    out = service.merge_notes(paths, "a", ["b"])

    note = out["note"]
    assert out["merged"] == 1
    assert note["description"] == "上料站的程式\n\n負責把料盤送進機台"
    assert note["fields"] == {"alias": "LDR", "en_term": "Loader"}
    assert note["tags"] == ["產線", "設備"]
    assert out["dropped_fields"] == []
    # created 取所有來源裡最早的(同一個詞第一次被記下來的時間)、書籤任一邊有就保留
    assert note["created"] == pytest.approx(old)
    assert note["marked"] is True
    # 而且合併前的內容寫進一筆歷史快照,判斷錯了可以回復
    assert service.load_note_or_404(paths, "a")["history"][0]["description"] == "上料站的程式"


def test_merge_never_silently_drops_a_conflicting_field_value(vault):
    paths, add = vault
    add("a", "Loader", fields={"alias": "LDR"})
    add("b", "loader", fields={"alias": "上料機"})
    out = service.merge_notes(paths, "a", ["b"])
    assert out["note"]["fields"]["alias"] == "LDR"          # 保留的那筆優先
    assert out["dropped_fields"] == [
        {"id": "b", "name": "loader", "key": "alias", "value": "上料機"}]


def test_merge_repoints_links_without_bumping_the_relinked_notes(vault):
    """指著被併掉那筆的名詞並沒有被使用者編輯過,不該因此跳到列表最前面。"""
    paths, add = vault
    add("a", "Loader")
    add("b", "上料站")
    add("c", "別的名詞", description="細節見 [[上料站]]")
    before = service.load_note_or_404(paths, "c")["updated"]
    out = service.merge_notes(paths, "a", ["b"])

    assert out["relinked"] == 1
    c = service.load_note_or_404(paths, "c")
    assert c["description"] == "細節見 [[Loader]]"
    # 而且反向連結真的接到留下來的那筆
    assert search.links_of(paths, service.load_note_or_404(paths, "a"))["backlinks"] == \
        [{"id": "c", "name": "別的名詞"}]
    # 只被改寫連結:不 bump updated、不寫歷史版本
    assert c["updated"] == before
    assert c["history"] == []


def test_merged_sources_go_to_the_recycle_bin_with_their_assets(vault):
    paths, add = vault
    (paths.assets_dir / "b").mkdir(parents=True, exist_ok=True)
    (paths.assets_dir / "b" / "pic.png").write_bytes(b"png")
    add("a", "Loader")
    add("b", "loader", description="判斷錯了要救得回來 ![](assets/b/pic.png)",
        attachments=[{"name": "pic.png", "path": "assets/b/pic.png", "description": ""}])
    note = service.merge_notes(paths, "a", ["b"])["note"]

    # 資產是**複製**到 target 而不是搬走
    assert note["attachments"][0]["path"] == "assets/a/pic.png"
    assert "assets/a/pic.png" in note["description"]
    assert (paths.assets_dir / "a" / "pic.png").is_file()
    # 來源走一般刪除進回收桶,不是永久消失;檔案是複製過去的,還原出來不會是破圖
    assert search.all_names(paths) == [{"id": "a", "name": "Loader"}]
    trashed = trash_mod.read_trashed(paths, "b")
    assert "判斷錯了要救得回來" in trashed["description"]
    assert (paths.trash_assets_dir / "b" / "pic.png").is_file()


def test_merge_rejects_merging_a_note_into_itself(vault):
    from fastapi import HTTPException
    paths, add = vault
    add("a", "Loader")
    with pytest.raises(HTTPException) as e:
        service.merge_notes(paths, "a", ["a"])
    assert e.value.status_code == 400


# ── 走 HTTP 的煙霧測試(只證明各層真的接起來)────────────────────────

def _create(client, name, **kw):
    r = client.post("/api/notes", json={"name": name, **kw})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_similar_and_duplicates_and_merge_over_http(register_user):
    c = register_user()["client"]
    keep = _create(c, "Loader", description="留下來的")
    dupe = _create(c, "loader", description="重複的")
    _create(c, "引用者", description="見 [[loader]]")

    similar = c.post("/api/similar", json={"name": "LOADER"}).json()["similar"]
    assert sorted(s["id"] for s in similar) == sorted([keep, dupe])
    groups = c.get("/api/duplicates").json()["groups"]
    assert len(groups) == 1 and len(groups[0]["notes"]) == 2

    r = c.post("/api/notes/merge", json={"target_id": keep, "source_ids": [dupe]})
    assert r.status_code == 200, r.text
    assert r.json()["merged"] == 1
    assert c.get("/api/duplicates").json()["groups"] == []
    # 這一組是「同名」重複,`[[loader]]` 本來就跟留下來的那筆同名,不需要改寫連結
    # (relinked=0),但合併後那條連結仍然指得到——反向連結接到留下來的那筆。
    assert r.json()["relinked"] == 0
    assert [b["name"] for b in c.get(f"/api/notes/{keep}/links").json()["backlinks"]] == ["引用者"]


def test_duplicates_are_isolated_between_users(register_user):
    alice, bob = register_user(), register_user()
    _create(alice["client"], "Loader")
    _create(alice["client"], "loader")
    assert len(bob["client"].get("/api/duplicates").json()["groups"]) == 0
    assert len(alice["client"].get("/api/duplicates").json()["groups"]) == 1

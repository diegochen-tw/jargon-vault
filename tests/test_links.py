"""`[[名詞]]` 連結:語法抽取(app/links.py)、反向索引與查詢(indexer + search)。

守著的核心性質:連結**依名稱解析、不存 id**,所以
  - 指向還不存在的名詞不會出錯,那筆一建立就自動接上
  - 反向連結是索引算出來的,刪掉 index.db 重建也要一模一樣
"""
import time

import pytest

from app import links, search, service
from app.indexer import rebuild_index


def _note(nid, name, description="", fields=None, tags=None):
    now = time.time()
    return {"id": nid, "name": name, "description": description,
            "template": "jargon-default", "fields": fields or {}, "tags": tags or [],
            "attachments": [], "marked": False, "created": now, "updated": now, "history": []}


@pytest.fixture
def vault(paths):
    """空索引 + 一個 helper:建一筆名詞並落地(檔案 + 索引)。"""
    rebuild_index(paths)

    def add(nid, name, description="", fields=None, tags=None):
        n = _note(nid, name, description, fields, tags)
        service.persist_note(paths, n)
        return n
    return paths, add


# ── 語法層(純文字,不碰 DB)────────────────────────────────────────

def test_link_targets_reads_description_and_fields_and_dedupes():
    # 空白/大小寫差異算同一個目標(norm_key),保留第一個出現的寫法
    n = _note("a", "A", "見 [[B]] 也見 [[C]] 又見 [[ b ]] 與 [[C]]",
              {"synonymy": "同義於 [[D]]"})
    assert links.link_targets(n) == ["B", "C", "D"]


@pytest.mark.parametrize("text", [
    "[[]]",                      # 空目標
    "[[跨\n行]]",                 # 跨行
    "[[外[[內]]層]]" .replace("內", "x" * 130),  # 超長
])
def test_link_targets_ignores_malformed_links(text):
    assert links.link_targets(_note("a", "A", text)) == []


def test_retarget_rewrites_only_the_matching_target_in_description_and_fields():
    md = "[[舊名]] 與 [[別的]] 與 [[ 舊 名 ]]"
    # 空白差異算同一個目標(norm_key),別的名詞原樣不動
    assert links.retarget(md, "舊名", "新名") == "[[新名]] 與 [[別的]] 與 [[新名]]"

    n = _note("a", "A", "見 [[舊]]", {"alias": "[[舊]] 的別名"})
    assert links.retarget_note(n, "舊", "新") is True
    assert n["description"] == "見 [[新]]"
    assert n["fields"]["alias"] == "[[新]] 的別名"
    assert links.retarget_note(n, "沒人指著它", "X") is False


# ── 查詢層(索引)───────────────────────────────────────────────────

def test_links_resolve_by_name_not_id(vault):
    paths, add = vault
    add("a", "cSSFI123", "就是 [[上料站前端程式]]")
    add("b", "上料站前端程式")
    out = search.links_of(paths, service.load_note_or_404(paths, "a"))
    assert out["outgoing"] == [{"name": "上料站前端程式", "id": "b"}]


def test_link_to_a_note_that_does_not_exist_yet_resolves_when_it_appears(vault):
    paths, add = vault
    add("a", "A", "之後再補 [[還沒建立的名詞]]")
    before = search.links_of(paths, service.load_note_or_404(paths, "a"))
    assert before["outgoing"] == [{"name": "還沒建立的名詞", "id": None}]

    add("b", "還沒建立的名詞")
    after = search.links_of(paths, service.load_note_or_404(paths, "a"))
    assert after["outgoing"] == [{"name": "還沒建立的名詞", "id": "b"}]
    # 而且反向連結也同時成立了——連結寫在目標建立之前一樣算數
    assert search.links_of(paths, service.load_note_or_404(paths, "b"))["backlinks"] == \
        [{"id": "a", "name": "A"}]


def test_backlinks_ignore_case_width_and_never_report_self_links(vault):
    paths, add = vault
    add("a", "A", "[[ｌｏａｄｅｒ]] 自己指自己 [[A]]")  # 全形+自我連結
    add("b", "Loader")
    assert search.links_of(paths, service.load_note_or_404(paths, "b"))["backlinks"] == \
        [{"id": "a", "name": "A"}]
    # 自己指自己不算反向連結
    assert search.links_of(paths, service.load_note_or_404(paths, "a"))["backlinks"] == []


def test_editing_replaces_links_and_deleting_removes_them(vault):
    paths, add = vault
    add("a", "A", "[[B]]")
    add("b", "B")
    add("c", "C")
    note = service.load_note_or_404(paths, "a")
    note["description"] = "[[C]]"
    service.persist_note(paths, note)
    # 編輯是「取代」不是「累積」
    assert search.links_of(paths, service.load_note_or_404(paths, "b"))["backlinks"] == []
    assert search.links_of(paths, service.load_note_or_404(paths, "c"))["backlinks"] == \
        [{"id": "a", "name": "A"}]
    # 刪掉來源,它寫下的連結一併消失
    service.delete_note(paths, "a")
    assert search.links_of(paths, service.load_note_or_404(paths, "c"))["backlinks"] == []


def test_link_index_survives_a_full_rebuild(vault):
    """索引是可拋棄快取:刪掉重建必須回到一模一樣的狀態(真相在 .md 裡)。"""
    paths, add = vault
    add("a", "Beta", "[[Alpha]]")
    add("b", "Alpha")
    before = search.links_of(paths, service.load_note_or_404(paths, "b"))
    assert before["backlinks"] == [{"id": "a", "name": "Beta"}]
    rebuild_index(paths)
    assert search.links_of(paths, service.load_note_or_404(paths, "b")) == before
    # 名稱對照表也一樣(依名稱排序)
    assert search.all_names(paths) == [{"id": "b", "name": "Alpha"}, {"id": "a", "name": "Beta"}]


# ── 走 HTTP 的煙霧測試(只證明各層真的接起來)────────────────────────

def test_links_api_roundtrip(register_user):
    c = register_user()["client"]
    src = c.post("/api/notes", json={"name": "cSSFI123", "description": "見 [[上料站]]"}).json()["id"]
    dst = c.post("/api/notes", json={"name": "上料站"}).json()["id"]

    assert c.get(f"/api/notes/{src}/links").json()["outgoing"] == [{"name": "上料站", "id": dst}]
    assert c.get(f"/api/notes/{dst}/links").json()["backlinks"] == [{"id": src, "name": "cSSFI123"}]
    assert {n["name"] for n in c.get("/api/names").json()["names"]} == {"cSSFI123", "上料站"}


def test_links_are_isolated_between_users(register_user):
    alice, bob = register_user(), register_user()
    alice["client"].post("/api/notes", json={"name": "A", "description": "[[B]]"})
    bid = bob["client"].post("/api/notes", json={"name": "B"}).json()["id"]
    assert bob["client"].get(f"/api/notes/{bid}/links").json()["backlinks"] == []

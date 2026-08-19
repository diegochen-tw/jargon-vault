"""標籤相似度重複偵測(app/dedup.py:find_duplicate_tag_groups)與合併
(app/service.py:merge_tags)。

這個功能守著的核心性質有兩條:
  1. **零偽陽性**——只報「同一把尺算出來的鍵相同」,不做模糊字串相似度。偽陽性一多,
     使用者就學會忽略整個功能,那等於沒做(同 app/health.py 檔頭的警告)。
  2. **合併不是編輯**——改標籤名不動 `updated`、不寫歷史版本,而且不靜默丟掉群組。
"""
import time

import pytest

from app import dedup, service
from app.indexer import rebuild_index
from app.storage import read_note_file, note_path
from app.tags import assign_group, load_tags


def _note(nid, name, tags=None, created=None):
    now = created or time.time()
    return {"id": nid, "name": name, "description": "", "template": "jargon-default",
            "fields": {}, "tags": tags or [], "attachments": [], "marked": False,
            "created": now, "updated": now, "history": []}


@pytest.fixture
def vault(paths):
    rebuild_index(paths)

    def add(nid, name, tags, **kw):
        service.persist_note(paths, _note(nid, name, tags, **kw))
    return paths, add


def _names(group):
    return [t["name"] for t in group["tags"]]


# ── 偵測 ──────────────────────────────────────────────────────────

def test_case_width_and_spacing_differences_are_the_same_tag(vault):
    paths, add = vault
    add("n1", "甲", ["MES"])
    add("n2", "乙", ["MES"])
    add("n3", "丙", ["Mes"])
    add("n4", "丁", ["ＭＥＳ"])
    assign_group(paths, ["Mes"], "製造")
    groups = dedup.find_duplicate_tag_groups(paths)
    assert len(groups) == 1
    assert groups[0]["reason"] == "same"
    assert sorted(_names(groups[0])) == ["MES", "Mes", "ＭＥＳ"]
    # 用得多的排第一,當預設保留者
    assert groups[0]["tags"][0]["name"] == "MES"
    assert groups[0]["tags"][0]["count"] == 2
    # 群組中繼資料一起帶出來(前端要顯示「合併會不會動到分類」)
    by_name = {t["name"]: t for t in groups[0]["tags"]}
    assert by_name["Mes"]["group"] == "製造"
    assert by_name["MES"]["group"] == ""


def test_punctuation_differences_are_near_and_ordered_after_same(vault):
    paths, add = vault
    add("n1", "甲", ["SFIS-1", "MES"])
    add("n2", "乙", ["SFIS 1", "Mes"])
    groups = dedup.find_duplicate_tag_groups(paths)
    assert [g["reason"] for g in groups] == ["same", "near"]   # same 一定排前面
    assert sorted(_names(groups[1])) == ["SFIS 1", "SFIS-1"]


def test_two_letter_tags_are_still_compared(vault):
    """MIN_KEY_LEN 只擋近似比對,不擋同名。

    `AI`/`QA`/`PM` 都是合法標籤,而它們的大小寫變體正是這個功能最該抓到的東西——
    norm_key 完全相等不是巧合,是同一個字。
    """
    paths, add = vault
    add("n1", "甲", ["AI"])
    add("n2", "乙", ["ai"])
    groups = dedup.find_duplicate_tag_groups(paths)
    assert len(groups) == 1
    assert groups[0]["reason"] == "same"
    assert sorted(_names(groups[0])) == ["AI", "ai"]


def test_unrelated_tags_are_not_grouped(vault):
    """偽陽性是這個功能的死因,負向測試是一等公民。"""
    paths, add = vault
    add("n1", "甲", ["cSSFI123", "cSSFI124"])   # 差一個字在行話裡通常是不同的東西
    add("n2", "乙", ["SMT", "SMD"])             # 只差一個字母,但是兩種製程
    add("n3", "丙", ["完全無關"])
    assert dedup.find_duplicate_tag_groups(paths) == []


# ── 合併 ──────────────────────────────────────────────────────────

def test_merge_rewrites_every_note_and_absorbs_variants_in_one_pass(vault):
    paths, add = vault
    add("n1", "甲", ["MES"])
    add("n2", "乙", ["Mes", "其他"])
    add("n3", "丙", ["ＭＥＳ"])
    assert service.merge_tags(paths, "MES", ["Mes", "ＭＥＳ"]) == 2
    assert read_note_file(note_path(paths, "n2"))["tags"] == ["MES", "其他"]
    assert read_note_file(note_path(paths, "n1"))["tags"] == ["MES"]   # 本來就是,沒被動到
    assert read_note_file(note_path(paths, "n3"))["tags"] == ["MES"]
    reg = load_tags(paths)
    assert "Mes" not in reg and "ＭＥＳ" not in reg
    assert dedup.find_duplicate_tag_groups(paths) == []
    # 併進自己(或空白)是 no-op
    assert service.merge_tags(paths, "MES", ["MES", "  "]) == 0
    assert read_note_file(note_path(paths, "n1"))["tags"] == ["MES"]


def test_a_note_carrying_both_variants_ends_up_with_one_tag(vault):
    """同一筆名詞同時掛著兩種寫法時,合併後不可以變成重複的兩個一樣的標籤。"""
    paths, add = vault
    add("n1", "甲", ["Mes", "MES", "其他"])
    service.merge_tags(paths, "MES", ["Mes"])
    assert read_note_file(note_path(paths, "n1"))["tags"] == ["MES", "其他"]


def test_merge_does_not_bump_updated_or_write_history(vault):
    """標籤改名不是使用者對名詞內容的編輯——bump `updated` 會把「依上次編輯時間」的
    列表排序整個打亂(同 rename_tag/delete_tag 的決定)。"""
    paths, add = vault
    add("n1", "甲", ["Mes"])
    before = read_note_file(note_path(paths, "n1"))
    time.sleep(0.01)
    service.merge_tags(paths, "MES", ["Mes"])
    after = read_note_file(note_path(paths, "n1"))
    assert after["updated"] == before["updated"]
    assert after["history"] == []


def test_merge_keeps_the_earliest_created_time(vault):
    paths, add = vault
    add("n1", "甲", ["Mes"], created=100.0)
    add("n2", "乙", ["MES"], created=200.0)
    reg = load_tags(paths)
    assert reg["Mes"]["created"] < reg["MES"]["created"]
    service.merge_tags(paths, "MES", ["Mes"])
    reg = load_tags(paths)
    assert "Mes" not in reg
    assert reg["MES"]["created"] == 100.0   # 這個詞第一次被記下來的時間


def test_merge_adopts_the_group_but_never_overwrites_an_existing_one(vault):
    """rename_tag 會把被併掉那邊的 group 靜默丟掉,合併不可以——把「製造」群組裡的
    Mes 併進沒有分組的 MES,結果整組從側欄的分類樹消失,使用者只會覺得
    「合併一下標籤,分類就不見了」。反過來,保留者已有群組時絕不被蓋掉。"""
    paths, add = vault
    add("n1", "甲", ["MES", "SYS"])
    add("n2", "乙", ["Mes", "sys"])
    assign_group(paths, ["Mes"], "製造")
    assign_group(paths, ["SYS"], "系統")
    assign_group(paths, ["sys"], "別的")

    service.merge_tags(paths, "MES", ["Mes"])       # 保留者沒有群組 → 採納
    assert load_tags(paths)["MES"]["group"] == "製造"
    service.merge_tags(paths, "SYS", ["sys"])       # 保留者已有群組 → 不覆蓋
    assert load_tags(paths)["SYS"]["group"] == "系統"


# ── 走 HTTP 的煙霧測試(只證明各層真的接起來)────────────────────────

def _create(client, name, tags):
    r = client.post("/api/notes", json={"name": name, "tags": tags})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_scan_and_merge_over_http(register_user):
    c = register_user()["client"]
    _create(c, "甲", ["MES"])
    _create(c, "乙", ["Mes"])

    groups = c.get("/api/tag-duplicates").json()["groups"]
    assert len(groups) == 1 and groups[0]["reason"] == "same"

    # 空的 keep 直接 400,不動任何東西
    assert c.post("/api/tags/merge", json={"keep": "  ", "absorb": ["MES"]}).status_code == 400

    r = c.post("/api/tags/merge", json={"keep": "MES", "absorb": ["Mes"]})
    assert r.status_code == 200, r.text
    assert r.json()["affected"] == 1
    assert c.get("/api/tag-duplicates").json()["groups"] == []
    assert [t["name"] for t in c.get("/api/tags").json()["tags"]] == ["MES"]


def test_tag_duplicates_are_isolated_between_users(register_user):
    alice, bob = register_user(), register_user()
    _create(alice["client"], "甲", ["MES"])
    _create(alice["client"], "乙", ["Mes"])
    assert bob["client"].get("/api/tag-duplicates").json()["groups"] == []
    assert len(alice["client"].get("/api/tag-duplicates").json()["groups"]) == 1


def test_both_endpoints_require_login(client):
    assert client.get("/api/tag-duplicates").status_code == 401
    assert client.post("/api/tags/merge", json={"keep": "a", "absorb": ["b"]}).status_code == 401

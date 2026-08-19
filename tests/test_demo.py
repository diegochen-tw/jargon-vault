"""
範例資料的種子與清除(app/demo.py + app/service.py:delete_demo_notes)。

最重要的那支是 `test_purge_never_touches_the_users_own_notes`:置頂行的刪除鈕
出現在使用者已經開始建立自己的名詞之後,只要哪天有人圖方便把它接到
delete_all_notes(),使用者按一下就會失去全部資料而且完全不會有錯誤訊息。

第二重要的是 `test_seed_refuses_a_non_empty_vault`:那條「空庫才種」的防線同時
在保護 migration.migrate_legacy_data_if_needed()——那支用 shutil.move 搬整個
notes/ 目錄,而且是 `if not dest.exists()`,種子先把目錄填出來就會**靜默**擋掉
舊資料搬遷。

⚠ conftest 把 GLOSSARY_SEED_DEMO 關掉了(否則所有整合測試的「空庫」斷言都會壞),
所以這裡一律直接呼叫 app.demo 的函式,不依賴註冊流程會不會種。
"""
import time

from app.demo import demo_note_ids, orphan_demo_tags, purge_vault, seed_vault
from app.indexer import rebuild_index
from app.plugins import ensure_plugins
from app.service import delete_demo_notes, persist_note
from app.storage import read_note_file
from app.tags import load_tags
from app.templates import ensure_templates, load_templates


def _fresh(paths):
    ensure_templates(paths)
    ensure_plugins(paths)
    rebuild_index(paths)  # persist_note 要有索引表可寫(註冊流程也一定會建)
    return paths


def _own_note(paths, nid="mine0001", tags=("Mine",)):
    persist_note(paths, {
        "id": nid, "name": f"own {nid}", "description": "written by the user",
        "tags": list(tags), "template": "jargon-default", "fields": {},
        "attachments": [], "created": time.time(), "updated": time.time(),
        "history": [],
    })


# ── 種子 ────────────────────────────────────────────────────────────────────

def test_seed_copies_notes_tags_and_assets(paths):
    _fresh(paths)

    n = seed_vault(paths)

    assert n == len(demo_note_ids()) > 0
    assert {p.stem for p in paths.notes_dir.glob("*.md")} == demo_note_ids()
    assert load_tags(paths)  # 標籤登記簿(含群組)一起帶進來
    # 附圖解說那筆帶了一張圖:資產目錄要跟著複製,不然範例一進來就是破圖
    assert (paths.assets_dir / "demo-graph-yield").is_dir()


def test_every_seeded_note_parses(paths):
    """範例是隨 repo 發佈的靜態檔,壞掉不會有人發現——直到新使用者第一次開啟。"""
    _fresh(paths)
    seed_vault(paths)
    for md in sorted(paths.notes_dir.glob("*.md")):
        assert read_note_file(md) is not None, md.name


def test_seeded_notes_only_reference_existing_templates(paths):
    """範例不能引用已經退場的樣板(如降級成外掛的 english-word):
    那會讓新使用者一開箱就看到欄位標題退化成 key 的孤兒名詞。"""
    _fresh(paths)
    seed_vault(paths)
    known = {t["id"] for t in load_templates(paths)}
    used = {read_note_file(md)["template"] for md in paths.notes_dir.glob("*.md")}
    assert used <= known, used - known


def test_seed_enables_the_templates_the_samples_use(paths):
    """範例用到的樣板要打開,否則使用者看得到範例、卻在新建下拉裡找不到那個樣板。"""
    _fresh(paths)
    seed_vault(paths)
    enabled = {t["id"] for t in load_templates(paths) if t["enabled"]}
    used = {read_note_file(md)["template"] for md in paths.notes_dir.glob("*.md")}
    assert used <= enabled, used - enabled


def test_seed_refuses_a_non_empty_vault(paths):
    """⚠ 守門:非空庫一律不種。冪等只是順帶的好處,真正的理由是別讓種子擋掉
    legacy 遷移的整個目錄搬遷(見檔頭)。"""
    _fresh(paths)
    _own_note(paths)

    assert seed_vault(paths) == 0
    assert {p.stem for p in paths.notes_dir.glob("*.md")} == {"mine0001"}


def test_seed_is_idempotent(paths):
    _fresh(paths)
    first = seed_vault(paths)
    assert first > 0
    assert seed_vault(paths) == 0
    assert len(list(paths.notes_dir.glob("*.md"))) == first


def test_seeded_notes_are_searchable_after_index_rebuild(paths):
    """種子必須排在 rebuild_index() **之前**(見 routers/auth.py:_provision_new_user)。
    順序反了不會報錯,只是新使用者搜什麼都搜不到,要等下次重啟才補回來。"""
    from app.search import search_notes
    _fresh(paths)
    seed_vault(paths)
    rebuild_index(paths)

    got = search_notes(paths, q="REST", tags=[])
    assert any(n["id"] == "demo-rest" for n in got)


# ── 清除 ────────────────────────────────────────────────────────────────────

def test_purge_never_touches_the_users_own_notes(paths):
    """⚠ 本檔最重要的守門測試:置頂行的刪除鈕只刪範例。

    改成呼叫 delete_all_notes() 會讓這支失敗——那正是它存在的理由。"""
    _fresh(paths)
    seed_vault(paths)
    rebuild_index(paths)
    _own_note(paths, "mine0001")
    _own_note(paths, "mine0002", tags=("Mine", "Concept"))

    deleted = delete_demo_notes(paths)

    assert deleted == len(demo_note_ids())
    assert {p.stem for p in paths.notes_dir.glob("*.md")} == {"mine0001", "mine0002"}


def test_purge_removes_assets_and_orphan_demo_tags_only(paths):
    """範例帶進來的標籤沒人用了就清掉;使用者自己的標籤即使 0 筆也不動。"""
    _fresh(paths)
    seed_vault(paths)
    rebuild_index(paths)
    _own_note(paths, "mine0001", tags=("Mine", "Concept"))

    delete_demo_notes(paths)

    tags = set(load_tags(paths))
    assert "Mine" in tags          # 使用者自己的標籤
    assert "Concept" in tags       # 範例帶來的,但使用者的名詞還在用
    assert "Networking" not in tags  # 範例帶來的,已經沒人用
    assert not any(paths.assets_dir.iterdir())  # 範例的圖跟著走


def test_purge_on_an_already_empty_vault_is_a_no_op(paths):
    """使用者可能早就手動刪光範例。那時按下橫幅的唯一意義是把橫幅關掉,
    所以這條路徑必須成功回 0,而不是丟例外。"""
    _fresh(paths)
    assert delete_demo_notes(paths) == 0
    assert purge_vault(paths) == []


def test_purge_is_idempotent(paths):
    _fresh(paths)
    seed_vault(paths)
    rebuild_index(paths)

    assert delete_demo_notes(paths) > 0
    assert delete_demo_notes(paths) == 0


def test_orphan_demo_tags_only_reports_seeded_names(paths):
    """回報範圍**只有** demo/tags.json 帶進來的那些名字。

    使用者自己建的標籤即使目前 0 筆也絕不能出現在裡面——按一下「刪除範例」就把
    使用者整理好的標籤一起清掉,是不會有錯誤訊息的那種災難。"""
    import json

    from app.config import DEMO_DIR
    seeded = set(json.loads((DEMO_DIR / "tags.json").read_text(encoding="utf-8")))

    # 全部沒人用:回報的正好是範例標籤的全集,一個不多
    assert set(orphan_demo_tags(paths, set())) == seeded
    # 使用者自己的標籤不在範例清單裡,所以永遠不會被回報
    assert "Mine" not in orphan_demo_tags(paths, set())
    # 已被使用的範例標籤不算孤兒
    assert "Concept" in seeded
    assert "Concept" not in orphan_demo_tags(paths, {"Concept"})


# ── 旗標 ────────────────────────────────────────────────────────────────────

def test_demo_flag_roundtrip_and_me_reports_it(register_user):
    """demo_seeded 是一個旗標同時管兩件事(資料在 / 橫幅顯示),
    所以 /api/auth/me 的 demo_banner 必須直接跟著它走。"""
    from app.users import set_demo_seeded
    u = register_user()
    c = u["client"]

    # conftest 關掉了註冊時的種子,所以新帳號預設沒有橫幅
    assert c.get("/api/auth/me").json()["demo_banner"] is False

    set_demo_seeded(u["id"], True)
    me = c.get("/api/auth/me").json()
    assert me["demo_banner"] is True
    assert me["demo_site_url"]  # 網址由後端給,前端不寫死

    set_demo_seeded(u["id"], False)
    assert c.get("/api/auth/me").json()["demo_banner"] is False


def test_delete_demo_endpoint_clears_the_flag_even_when_nothing_to_delete(register_user):
    from app.users import set_demo_seeded
    u = register_user()
    c = u["client"]
    set_demo_seeded(u["id"], True)

    r = c.delete("/api/demo")

    assert r.status_code == 200 and r.json()["deleted"] == 0
    assert c.get("/api/auth/me").json()["demo_banner"] is False


def test_delete_demo_requires_login(client):
    assert client.delete("/api/demo").status_code == 401


def test_delete_demo_only_affects_the_callers_own_vault(register_user):
    """範例只種在個人庫;端點收 get_current_user 自己組 user_paths,
    不該因為另一個帳號也有範例就跨庫刪除。"""
    a, b = register_user(), register_user()
    seed_vault(_fresh(b["paths"]))
    rebuild_index(b["paths"])

    assert a["client"].delete("/api/demo").json()["deleted"] == 0
    assert len(list(b["paths"].notes_dir.glob("*.md"))) == len(demo_note_ids())

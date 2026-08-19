"""回收桶(app/trash.py + app/service.py 的搬移/還原 + /api/trash)。

重點在「刪除不是真的刪掉、但對搜尋而言就是不見了」這件事兩邊都要成立:
檔案還在 trash/ 底下,可是 notes_dir 的 glob 與索引都掃不到它。
"""
import time

import pytest
from fastapi import HTTPException

from app import trash
from app.config import TRASH_RETENTION_DAYS
from app.indexer import rebuild_index
from app.search import search_notes
from app.service import delete_note, persist_note, purge_trash_note, restore_from_trash
from app.storage import note_path
from app.tags import load_tags


@pytest.fixture(autouse=True)
def _indexed(paths):
    rebuild_index(paths)


def _note(nid, name="apple", tags=None) -> dict:
    now = time.time()
    return {
        "id": nid, "name": name, "description": "desc", "template": "jargon-default",
        "fields": {}, "tags": tags or [], "attachments": [],
        "created": now, "updated": now, "history": [],
    }


def _with_asset(paths, nid, filename="pic.png") -> None:
    d = paths.assets_dir / nid
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_bytes(b"fake")


def test_delete_moves_note_and_assets_into_trash_instead_of_erasing(paths):
    persist_note(paths, _note("n1", name="apple", tags=["fruit"]))
    _with_asset(paths, "n1")
    before = time.time()
    delete_note(paths, "n1")

    assert not note_path(paths, "n1").exists()
    assert not (paths.assets_dir / "n1").exists()
    assert search_notes(paths, "", []) == []          # 索引也拿掉了
    assert trash.trash_path(paths, "n1").exists()     # 但檔案還在回收桶
    assert (paths.trash_assets_dir / "n1" / "pic.png").read_bytes() == b"fake"
    assert [x["id"] for x in trash.list_trashed(paths)] == ["n1"]

    # 內容與刪除時間都在檔案自己的 frontmatter 裡,不另外維護登記簿
    item = trash.read_trashed(paths, "n1")
    assert item["name"] == "apple" and item["tags"] == ["fruit"]
    assert item["description"] == "desc"
    assert item["deleted"] >= before


def test_restore_brings_back_file_assets_and_index(paths):
    persist_note(paths, _note("n1", name="apple", tags=["fruit"]))
    _with_asset(paths, "n1")
    delete_note(paths, "n1")

    restored = restore_from_trash(paths, "n1")

    assert restored["name"] == "apple"
    assert "deleted" not in restored          # 回收桶專用欄位不跟著回到 notes/
    assert note_path(paths, "n1").exists()
    assert (paths.assets_dir / "n1" / "pic.png").exists()
    assert [r["id"] for r in search_notes(paths, "", [])] == ["n1"]
    assert not trash.trash_path(paths, "n1").exists()
    assert "fruit" in load_tags(paths)


def test_restore_refuses_when_same_id_already_exists(paths):
    persist_note(paths, _note("n1"))
    delete_note(paths, "n1")
    persist_note(paths, _note("n1", name="另一筆同 id"))  # 例如匯入把同 id 帶回來了

    with pytest.raises(HTTPException) as exc:
        restore_from_trash(paths, "n1")
    assert exc.value.status_code == 400
    assert trash.trash_path(paths, "n1").exists()  # 沒還原成功就不能把回收桶那份弄掉


def test_missing_ids_raise_404(paths):
    for op in (delete_note, restore_from_trash, purge_trash_note):
        with pytest.raises(HTTPException) as exc:
            op(paths, "does-not-exist")
        assert exc.value.status_code == 404


def test_purge_one_and_drop_all_remove_files_and_assets(paths):
    for nid in ("n1", "n2", "n3"):
        persist_note(paths, _note(nid))
    _with_asset(paths, "n1")
    for nid in ("n1", "n2", "n3"):
        delete_note(paths, nid)

    purge_trash_note(paths, "n1")
    assert not trash.trash_path(paths, "n1").exists()
    assert not (paths.trash_assets_dir / "n1").exists()

    assert trash.drop_all(paths) == 2
    assert trash.list_trashed(paths) == []


def test_purge_expired_only_removes_items_past_retention(paths):
    persist_note(paths, _note("old"))
    persist_note(paths, _note("fresh"))
    delete_note(paths, "old")
    delete_note(paths, "fresh")
    # 直接改寫回收桶檔案裡的時間戳記,模擬「刪掉超過保留天數」
    p = trash.trash_path(paths, "old")
    stale = time.time() - (TRASH_RETENTION_DAYS + 1) * 86400
    p.write_text(p.read_text(encoding="utf-8").replace(
        f"deleted: {trash.read_trashed(paths, 'old')['deleted']}", f"deleted: {stale}"),
        encoding="utf-8")

    assert trash.purge_expired(paths) == 1
    assert [x["id"] for x in trash.list_trashed(paths)] == ["fresh"]


def test_broken_note_file_is_deleted_outright(paths):
    """frontmatter 壞掉的檔案寫不進回收桶,只能直接刪——否則使用者連清掉它都做不到。"""
    note_path(paths, "bad").write_text("這不是合法的 frontmatter", encoding="utf-8")
    delete_note(paths, "bad")
    assert not note_path(paths, "bad").exists()
    assert not trash.trash_path(paths, "bad").exists()


# ── HTTP 層(只證明各層真的接起來,細節分支留給上面的單元測試) ──

def test_trash_api_roundtrip_purge_and_empty(register_user):
    c = register_user()["client"]
    nid = c.post("/api/notes", json={"name": "apple", "tags": ["fruit"]}).json()["id"]
    assert c.delete(f"/api/notes/{nid}").status_code == 200
    assert c.get("/api/search").json()["results"] == []

    r = c.get("/api/trash").json()
    assert r["retention_days"] == TRASH_RETENTION_DAYS
    assert [x["name"] for x in r["items"]] == ["apple"]

    assert c.post(f"/api/trash/{nid}/restore").status_code == 200
    assert [n["name"] for n in c.get("/api/search").json()["results"]] == ["apple"]
    assert c.get("/api/trash").json()["items"] == []

    a = c.post("/api/notes", json={"name": "a"}).json()["id"]
    b = c.post("/api/notes", json={"name": "b"}).json()["id"]
    c.delete(f"/api/notes/{a}")
    c.delete(f"/api/notes/{b}")
    assert c.delete(f"/api/trash/{a}").status_code == 200   # 永久刪除單筆
    assert c.delete("/api/trash").json()["deleted"] == 1    # 清空剩下的
    assert c.get("/api/trash").json()["items"] == []


def test_trash_is_isolated_between_users(register_user):
    alice, bob = register_user(), register_user()
    nid = alice["client"].post("/api/notes", json={"name": "alice-secret"}).json()["id"]
    alice["client"].delete(f"/api/notes/{nid}")

    assert bob["client"].get("/api/trash").json()["items"] == []
    assert bob["client"].post(f"/api/trash/{nid}/restore").status_code == 404


def test_bulk_purge_bypasses_the_trash(register_user):
    """設定 → 內容的「刪除內容」是永久刪除工具,刻意不進回收桶(見 service.delete_all_notes)。"""
    c = register_user()["client"]
    c.post("/api/notes", json={"name": "apple"})
    assert c.delete("/api/notes").json()["deleted"] == 1
    assert c.get("/api/trash").json()["items"] == []

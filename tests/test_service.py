"""app/service.py:跨檔案層與索引層的複合操作(先寫檔、再更新索引的約定實作處)。"""
import time

import pytest
from fastapi import HTTPException

from app.indexer import rebuild_index
from app.search import search_notes
from app.service import (
    category_to_tag_group,
    delete_note,
    delete_tag,
    load_note_or_404,
    migrate_categories_to_groups,
    persist_note,
    rename_tag,
)
from app.storage import note_path, read_note_file, write_note_file
from app.tags import load_tags


@pytest.fixture(autouse=True)
def _indexed(paths):
    # persist_note() 假設 notes/fts 資料表已存在(真實流程裡由啟動時或
    # 註冊時的 rebuild_index() 保證);單元測試繞過啟動流程,所以要自己補這一步。
    rebuild_index(paths)


def _note(nid, name="apple", tags=None, template="jargon-default") -> dict:
    now = time.time()
    return {
        "id": nid, "name": name, "description": "desc", "template": template,
        "fields": {}, "tags": tags or [], "attachments": [],
        "created": now, "updated": now, "history": [],
    }


def test_persist_note_writes_file_updates_index_and_registers_tags(paths):
    note = _note("n1", tags=["fruit"])
    persist_note(paths, note)

    assert read_note_file(note_path(paths, "n1"))["name"] == "apple"
    assert "fruit" in load_tags(paths)
    assert [r["id"] for r in search_notes(paths, "", [])] == ["n1"]


def test_delete_note_removes_file_assets_and_index_and_404s_missing(paths):
    persist_note(paths, _note("n1"))
    asset_dir = paths.assets_dir / "n1"
    asset_dir.mkdir(parents=True)
    (asset_dir / "pic.png").write_bytes(b"fake")

    delete_note(paths, "n1")
    assert not note_path(paths, "n1").exists()
    assert not asset_dir.exists()
    assert search_notes(paths, "", []) == []

    with pytest.raises(HTTPException) as exc:
        delete_note(paths, "does-not-exist")
    assert exc.value.status_code == 404


def test_load_note_or_404_rejects_missing_and_invalid_ids(paths):
    with pytest.raises(HTTPException) as exc:
        load_note_or_404(paths, "does-not-exist")
    assert exc.value.status_code == 404

    # 路徑穿越:壞 id 是 400,不是拿去組路徑
    with pytest.raises(HTTPException) as exc:
        load_note_or_404(paths, "../escape")
    assert exc.value.status_code == 400


def test_rename_tag_updates_notes_and_registry(paths):
    persist_note(paths, _note("n1", tags=["old"]))
    persist_note(paths, _note("n2", tags=["old", "other"]))
    persist_note(paths, _note("n3", tags=["other"]))

    affected = rename_tag(paths, "old", "new")

    assert affected == 2
    assert read_note_file(note_path(paths, "n1"))["tags"] == ["new"]
    assert set(read_note_file(note_path(paths, "n2"))["tags"]) == {"new", "other"}
    reg = load_tags(paths)
    assert "old" not in reg and "new" in reg


def test_rename_tag_merges_created_time_when_target_already_exists(paths):
    persist_note(paths, _note("n1", tags=["old"]))
    time.sleep(0.01)
    persist_note(paths, _note("n2", tags=["existing"]))
    reg = load_tags(paths)
    old_created = reg["old"]["created"]
    existing_created = reg["existing"]["created"]
    assert old_created < existing_created

    rename_tag(paths, "old", "existing")

    reg = load_tags(paths)
    assert reg["existing"]["created"] == old_created  # 取兩者較早的時間


def test_rename_tag_rejects_empty_name_and_noops_on_same_name(paths):
    persist_note(paths, _note("n1", tags=["old"]))
    with pytest.raises(HTTPException) as exc:
        rename_tag(paths, "old", "   ")
    assert exc.value.status_code == 400
    assert rename_tag(paths, "old", "old") == 0


def test_delete_tag_removes_from_notes_and_registry(paths):
    persist_note(paths, _note("n1", tags=["fruit", "fresh"]))
    persist_note(paths, _note("n2", tags=["fresh"]))

    affected = delete_tag(paths, "fresh")

    assert affected == 2
    assert read_note_file(note_path(paths, "n1"))["tags"] == ["fruit"]
    assert read_note_file(note_path(paths, "n2"))["tags"] == []
    assert "fresh" not in load_tags(paths)


@pytest.mark.parametrize("category,expected", [
    ("前端/框架", ("框架", "前端")),
    ("框架", ("框架", "")),
    ("", ("", "")),
    ("  ", ("", "")),
    ("a/b/c", ("c", "a")),
])
def test_category_to_tag_group_conversion_rules(category, expected):
    assert category_to_tag_group(category) == expected


def test_migrate_categories_to_groups_converts_legacy_notes_and_is_idempotent(paths):
    p = note_path(paths, "legacy1")
    p.write_text(
        "---\n"
        "id: legacy1\n"
        "name: React\n"
        "category: 前端/框架\n"
        "tags: []\n"
        "created: 1.0\n"
        "updated: 1.0\n"
        "---\n\n"
        "說明\n",
        encoding="utf-8",
    )

    migrated = migrate_categories_to_groups(paths)

    assert migrated == 1
    note = read_note_file(p)
    assert note["tags"] == ["框架"]
    assert note["category"] == ""  # 新格式不再寫出 category
    assert load_tags(paths)["框架"]["group"] == "前端"

    # 已轉換過(不再帶 category)的庫,再跑一次是 noop
    assert migrate_categories_to_groups(paths) == 0


def test_migrate_categories_to_groups_does_not_override_existing_group_assignment(paths):
    # 標籤已經被使用者手動分過組時,遷移不能覆蓋
    persist_note(paths, _note("n0", tags=["框架"]))
    from app.tags import assign_group
    assign_group(paths, ["框架"], "使用者自己的分類")

    p = note_path(paths, "legacy1")
    p.write_text(
        "---\nid: legacy1\nname: React\ncategory: 前端/框架\ntags: []\n"
        "created: 1.0\nupdated: 1.0\n---\n\n說明\n",
        encoding="utf-8",
    )
    migrate_categories_to_groups(paths)

    assert load_tags(paths)["框架"]["group"] == "使用者自己的分類"

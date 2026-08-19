"""app/tags.py:標籤登記簿(<使用者目錄>/tags.json)讀寫與群組操作。"""
import pytest

from app.storage import note_path, write_note_file
from app.tags import (
    assign_group,
    bootstrap_from_notes,
    dissolve_all_groups,
    dissolve_group,
    load_tags,
    register_new_tags,
    rename_group,
    save_tags,
)


def test_register_new_tags_writes_created_once_and_never_overwrites(paths):
    assert load_tags(paths) == {}   # 檔案還不存在時回空 dict
    register_new_tags(paths, ["python", "go"], created=100.0)
    reg = load_tags(paths)
    assert reg["python"] == {"created": 100.0, "group": ""}
    assert reg["go"] == {"created": 100.0, "group": ""}
    # 標籤第一次出現的時間只在「首次登記」時寫入,之後再遇到同一個標籤不能覆蓋
    register_new_tags(paths, ["python"], created=999.0)
    assert load_tags(paths)["python"]["created"] == 100.0


def test_load_tags_backfills_missing_group_key_for_old_files(paths):
    save_tags(paths, {"python": {"created": 1.0}})  # 模擬舊檔沒有 group 欄位
    reg = load_tags(paths)
    assert reg["python"]["group"] == ""


def test_assign_group_sets_group_and_validates_input(paths):
    register_new_tags(paths, ["python", "go"], created=1.0)
    assert assign_group(paths, ["python", "go"], "程式語言") == 2
    reg = load_tags(paths)
    assert reg["python"]["group"] == "程式語言"
    assert reg["go"]["group"] == "程式語言"
    # 已經在該群組 → 0;沒登記過的標籤 → ValueError
    assert assign_group(paths, ["python"], "程式語言") == 0
    with pytest.raises(ValueError):
        assign_group(paths, ["never-seen"], "群組")


def test_dissolve_group_then_dissolve_all(paths):
    register_new_tags(paths, ["python", "go", "react"], created=1.0)
    assign_group(paths, ["python", "go"], "程式語言")
    assign_group(paths, ["react"], "前端")

    # 打散單一群組只動那一組
    assert dissolve_group(paths, "程式語言") == 2
    reg = load_tags(paths)
    assert reg["python"]["group"] == "" and reg["go"]["group"] == ""
    assert reg["react"]["group"] == "前端"

    # 全部打散把剩下的也清掉
    assert dissolve_all_groups(paths) == 1
    assert all(meta["group"] == "" for meta in load_tags(paths).values())


def test_rename_group_only_touches_that_group(paths):
    register_new_tags(paths, ["python", "go", "react"], created=1.0)
    assign_group(paths, ["python", "go"], "程式語言")
    assign_group(paths, ["react"], "前端")

    assert rename_group(paths, "程式語言", "後端語言") == 2
    reg = load_tags(paths)
    assert reg["python"]["group"] == "後端語言" and reg["go"]["group"] == "後端語言"
    assert reg["react"]["group"] == "前端"


def test_rename_group_onto_an_existing_name_merges_them(paths):
    """刻意允許合併——「把這兩組併起來」是分組整理時的正常動作,擋掉只會逼
    使用者先打散再重新勾選一次。"""
    register_new_tags(paths, ["python", "react"], created=1.0)
    assign_group(paths, ["python"], "程式語言")
    assign_group(paths, ["react"], "前端")

    assert rename_group(paths, "前端", "程式語言") == 1
    reg = load_tags(paths)
    assert reg["python"]["group"] == reg["react"]["group"] == "程式語言"


def test_rename_group_rejects_empty_noop_and_unknown_names(paths):
    register_new_tags(paths, ["python"], created=1.0)
    assign_group(paths, ["python"], "程式語言")

    assert rename_group(paths, "程式語言", "   ") == 0
    assert rename_group(paths, "程式語言", "程式語言") == 0
    assert rename_group(paths, "不存在的群組", "新名字") == 0
    assert load_tags(paths)["python"]["group"] == "程式語言"


def _write_bare_note(paths, nid, tags, created):
    write_note_file(paths, {
        "id": nid, "name": nid, "description": "", "template": "jargon-default",
        "fields": {}, "tags": tags, "attachments": [], "created": created,
        "updated": created, "history": [],
    })
    return note_path(paths, nid)


def test_bootstrap_registers_missing_tags_with_the_earliest_created(paths):
    _write_bare_note(paths, "n1", ["python"], created=200.0)
    _write_bare_note(paths, "n2", ["python"], created=50.0)  # 較早
    _write_bare_note(paths, "n3", ["python"], created=300.0)
    assert load_tags(paths) == {}  # 還沒登記
    bootstrap_from_notes(paths)
    reg = load_tags(paths)
    # 用「目前掛著此標籤的名詞中最早的 created」推估首次出現時間
    assert reg["python"]["created"] == 50.0
    assert reg["python"]["group"] == ""


def test_bootstrap_from_notes_does_not_touch_already_registered_tags(paths):
    register_new_tags(paths, ["python"], created=1.0)
    assign_group(paths, ["python"], "程式語言")
    _write_bare_note(paths, "n1", ["python"], created=9999.0)  # created 比登記的晚很多
    bootstrap_from_notes(paths)
    reg = load_tags(paths)
    assert reg["python"]["created"] == 1.0  # 沒被較晚的 note created 蓋掉
    assert reg["python"]["group"] == "程式語言"  # 分組沒被清掉

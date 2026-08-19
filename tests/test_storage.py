"""app/storage.py:.md 檔案讀寫,真相來源。純檔案操作,搭配 paths fixture 即可測。"""
from app.storage import note_path, read_note_file, snapshot_of, write_note_file


def _note(**overrides) -> dict:
    base = {
        "id": "n1", "name": "apple", "description": "一種水果",
        "template": "english-word", "fields": {"translation": "蘋果", "phonetic": "/ˈæp.əl/"},
        "tags": ["fruit"], "attachments": [], "created": 1000.0, "updated": 1000.0, "history": [],
    }
    base.update(overrides)
    return base


def test_write_then_read_round_trips_all_fields(paths):
    note = _note()
    write_note_file(paths, note)
    got = read_note_file(note_path(paths, "n1"))
    assert got["id"] == "n1"
    assert got["name"] == "apple"
    assert got["description"] == "一種水果"
    assert got["template"] == "english-word"
    assert got["fields"] == {"translation": "蘋果", "phonetic": "/ˈæp.əl/"}
    assert got["tags"] == ["fruit"]
    assert got["created"] == 1000.0
    assert got["updated"] == 1000.0


def test_read_note_file_missing_or_malformed_returns_none(paths):
    assert read_note_file(note_path(paths, "does-not-exist")) is None
    p = note_path(paths, "broken")
    p.write_text("no frontmatter here at all", encoding="utf-8")
    assert read_note_file(p) is None


def test_legacy_top_level_fields_migrate_into_fields_dict_on_read(paths):
    # 改版前 alias/synonymy/polysemy 是 frontmatter 頂層 key,不在 fields dict 裡;
    # 懶遷移:讀入時折進 fields,樣板視為預設樣板(jargon-default)。
    p = note_path(paths, "legacy1")
    p.write_text(
        "---\n"
        "id: legacy1\n"
        "name: 舊格式名詞\n"
        "alias: 別名A\n"
        "synonymy: 同義字A\n"
        "polysemy: ''\n"
        "tags: []\n"
        "created: 500.0\n"
        "updated: 500.0\n"
        "---\n\n"
        "說明內容\n",
        encoding="utf-8",
    )
    got = read_note_file(p)
    assert got["template"] == "jargon-default"
    assert got["fields"] == {"alias": "別名A", "synonymy": "同義字A", "polysemy": ""}


def test_new_format_fields_dict_is_used_as_is_when_present(paths):
    p = note_path(paths, "n2")
    p.write_text(
        "---\n"
        "id: n2\n"
        "name: quicksort\n"
        "template: code-snippet\n"
        "fields:\n"
        "  language: Python\n"
        "tags: []\n"
        "created: 1.0\n"
        "updated: 1.0\n"
        "---\n\n"
        "說明\n",
        encoding="utf-8",
    )
    got = read_note_file(p)
    assert got["template"] == "code-snippet"
    assert got["fields"] == {"language": "Python"}


def test_history_round_trips_through_snapshot_and_write(paths):
    note = _note()
    note["history"] = [snapshot_of(note)]
    write_note_file(paths, note)
    got = read_note_file(note_path(paths, "n1"))
    assert len(got["history"]) == 1
    assert got["history"][0]["name"] == "apple"
    assert got["history"][0]["fields"] == {"translation": "蘋果", "phonetic": "/ˈæp.əl/"}


def test_snapshot_of_deep_copies_mutable_fields_so_later_mutation_does_not_leak():
    note = _note()
    snap = snapshot_of(note)
    note["fields"]["translation"] = "changed"
    note["tags"].append("mutated")
    assert snap["fields"]["translation"] == "蘋果"
    assert snap["tags"] == ["fruit"]


def test_category_is_read_only_for_legacy_migration_detection(paths):
    p = note_path(paths, "legacy2")
    p.write_text(
        "---\n"
        "id: legacy2\n"
        "name: 舊分類名詞\n"
        "category: 前端/框架\n"
        "tags: []\n"
        "created: 1.0\n"
        "updated: 1.0\n"
        "---\n\n"
        "說明\n",
        encoding="utf-8",
    )
    got = read_note_file(p)
    assert got["category"] == "前端/框架"
    # write_note_file 的 front dict 沒有 category 這個 key,新格式不會再寫出它
    write_note_file(paths, got)
    rewritten = note_path(paths, "legacy2").read_text(encoding="utf-8")
    assert "category" not in rewritten


def test_marked_is_never_written_into_the_md_nor_versioned(paths):
    """★ 書籤**不寫進 .md**,也不是版本化內容。

    它是「我跟這筆名詞的關係」,不是名詞的屬性——真相在個人側的 progress.json
    (見 app/progress.py)。團隊庫的 .md 是全隊共讀共寫的,寫進去就等於我的
    書籤全隊看得到。讀檔端一律回 False,實際值由 service.attach_progress() 貼回;
    快照也不帶它:回復舊版本不該把書籤狀態一起回復。
    """
    write_note_file(paths, _note(marked=True))
    assert "marked" not in note_path(paths, "n1").read_text(encoding="utf-8")
    assert read_note_file(note_path(paths, "n1"))["marked"] is False
    # note dict 沒帶 marked(既有呼叫端的形狀)→ 一樣不炸
    write_note_file(paths, _note(id="n2"))
    assert read_note_file(note_path(paths, "n2"))["marked"] is False
    # 書籤不進歷史快照
    assert "marked" not in snapshot_of(_note(marked=True))


def test_legacy_md_reads_marked_when_present_and_false_when_absent(paths):
    """舊檔裡殘留的 marked 仍要讀得出來——啟動時 rebuild_index 靠它把值
    一次性收進 progress.json(懶遷移,不改寫舊檔);這個欄位出現之前的
    .md 沒有 marked key,讀出來是 False。"""
    p = note_path(paths, "legacy-marked")
    p.write_text(
        "---\nid: legacy-marked\nname: 舊檔\ntemplate: jargon-default\n"
        "fields: {}\ntags: []\nattachments: []\nmarked: true\n"
        "created: 1.0\nupdated: 2.0\nhistory: []\n---\n\n內文\n",
        encoding="utf-8")
    assert read_note_file(p)["marked"] is True

    p2 = note_path(paths, "legacy3")
    p2.write_text(
        "---\n"
        "id: legacy3\n"
        "name: 舊名詞\n"
        "tags: []\n"
        "created: 1.0\n"
        "updated: 1.0\n"
        "---\n\n"
        "說明\n",
        encoding="utf-8",
    )
    assert read_note_file(p2)["marked"] is False

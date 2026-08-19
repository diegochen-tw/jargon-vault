"""app/sanitize.py:純函式,不碰檔案系統/資料庫,是整套系統裡最容易測的一層。"""
import pytest

from app.sanitize import clean_attachments, clean_fields, clean_tags, clean_template_fields


def test_clean_tags_strips_dedupes_drops_empty_and_keeps_first_occurrence_order():
    assert clean_tags([" python ", "python", "", "  ", "go"]) == ["python", "go"]
    assert clean_tags(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]


def test_clean_fields_drops_reserved_keys():
    # id/name/tags/template/fields 等是核心欄位保留字,樣板欄位不能撞名
    out = clean_fields({"alias": "foo", "id": "should-be-dropped", "template": "x"})
    assert out == {"alias": "foo"}


def test_clean_fields_drops_invalid_key_format():
    # 大寫開頭、以數字開頭、含連字號都不合法(FIELD_KEY_RE 要求小寫字母開頭)
    out = clean_fields({"Alias": "a", "1field": "b", "my-field": "c", "ok_field2": "d"})
    assert out == {"ok_field2": "d"}


def test_clean_fields_strips_and_stringifies_values():
    out = clean_fields({"alias": "  padded  ", "phonetic": None})
    assert out == {"alias": "padded", "phonetic": ""}


def test_clean_template_fields_fills_defaults_with_fallback_chain():
    # label 缺值退回 key;placeholder 缺值退回 label
    out = clean_template_fields([{"key": "language"}])
    assert out == [{"key": "language", "label": "language",
                    "placeholder": "language", "enabled": True}]
    out = clean_template_fields([{"key": "lang", "label": "程式語言"}])
    assert out == [{"key": "lang", "label": "程式語言",
                    "placeholder": "程式語言", "enabled": True}]


def test_clean_template_fields_keeps_disabled_flag_and_order():
    """欄位的啟用旗標與清單順序都是使用者的編排,原樣帶過去(順序 = 顯示順序)。"""
    out = clean_template_fields([
        {"key": "b", "enabled": False},
        {"key": "a"},
    ])
    assert [(f["key"], f["enabled"]) for f in out] == [("b", False), ("a", True)]


@pytest.mark.parametrize("fields", [
    [{"key": "Not Valid"}],               # 不合法的 key 格式
    [{"key": "template"}],                # 保留字
    [{"key": "lang"}, {"key": "lang"}],   # 重複 key
])
def test_clean_template_fields_rejects_bad_keys(fields):
    with pytest.raises(ValueError):
        clean_template_fields(fields)


def test_clean_attachments_drops_entries_missing_name_or_path():
    out = clean_attachments([
        {"name": "a.png", "path": "/assets/x/a.png"},
        {"name": "", "path": "/assets/x/b.png"},
        {"name": "c.png", "path": ""},
        "not-a-dict",
    ])
    assert out == [{"name": "a.png", "path": "/assets/x/a.png", "description": ""}]

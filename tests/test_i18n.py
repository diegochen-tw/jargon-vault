"""前端 i18n 字典的一致性(static/js/i18n.js)。

前端沒有測試框架,但「新增 UI 文字要 12 種語言都補」這條規則靠人眼守不住:
缺譯會靜默退回英文,畫面上看得懂、不會報錯,所以漏了不會有人發現。
這裡直接讀 i18n.js 的原始碼對齊各語系的 key 清單——純文字解析,不需要跑 JS。
"""
import re
from pathlib import Path

import pytest

I18N = Path(__file__).resolve().parent.parent / "static" / "js" / "i18n.js"

# 語系區塊開頭:  "zh-Hant": {  或  en: {
_BLOCK_HEAD = re.compile(r'^  "?([A-Za-z-]+)"?: \{$')
_KEY = re.compile(r'^    "([^"]+)":')


def _dicts() -> dict[str, list[str]]:
    """{語系: [key, ...]},保留出現順序以便驗重複。"""
    out: dict[str, list[str]] = {}
    lang = None
    for line in I18N.read_text(encoding="utf-8").splitlines():
        head = _BLOCK_HEAD.match(line)
        if head:
            lang = head.group(1)
            out[lang] = []
            continue
        if line == "  },":
            lang = None
            continue
        if lang:
            k = _KEY.match(line)
            if k:
                out[lang].append(k.group(1))
    return out


ADVERTISED = ["zh-Hant", "zh-Hans", "en", "ja", "fr", "de",
              "it", "pt", "es", "ko", "id", "hi"]


def test_all_advertised_languages_are_present():
    assert sorted(_dicts()) == sorted(ADVERTISED)


@pytest.mark.parametrize("lang", ADVERTISED)
def test_language_has_the_same_keys_as_the_reference(lang):
    """以 zh-Hant(維護時第一手改的那份)為基準,其餘語系不得多也不得少。"""
    d = _dicts()
    missing = set(d["zh-Hant"]) - set(d[lang])
    extra = set(d[lang]) - set(d["zh-Hant"])
    assert not missing, f"{lang} 缺少:{sorted(missing)}"
    assert not extra, f"{lang} 多出來(zh-Hant 沒有):{sorted(extra)}"


@pytest.mark.parametrize("lang", ADVERTISED)
def test_language_has_no_duplicate_keys(lang):
    """同一個 key 寫兩次時後面那筆會靜默覆蓋前面,很難用肉眼看出來。"""
    keys = _dicts()[lang]
    dupes = {k for k in keys if keys.count(k) > 1}
    assert not dupes, f"{lang} 有重複的 key:{sorted(dupes)}"


def test_zh_hans_is_not_a_copy_of_zh_hant():
    """簡中若整份跟繁中一樣,代表轉換沒跑到(或被還原掉了)。"""
    src = I18N.read_text(encoding="utf-8")

    def block(name: str) -> str:
        i = src.index(f'  "{name}": {{')
        return src[i:src.index("\n  },", i)]

    hant, hans = block("zh-Hant"), block("zh-Hans")
    assert hant != hans
    # 幾個一定會被轉掉的用詞:確認簡體那份用的是大陸慣例
    assert "樣板" in hant and "模板" in hans and "樣板" not in hans
    assert "匯出" in hant and "导出" in hans and "匯出" not in hans
    assert "預設" in hant and "默认" in hans


def test_language_select_offers_every_language():
    """設定 → 偏好設定的下拉選單漏一個語系的話,使用者就只能靠系統語言碰運氣。"""
    html = (I18N.parent.parent / "index.html").read_text(encoding="utf-8-sig")
    options = set(re.findall(r'<option value="([A-Za-z-]+)"', html))
    assert set(ADVERTISED) <= options, f"下拉選單缺少:{sorted(set(ADVERTISED) - options)}"

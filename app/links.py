"""
名詞之間的連結:`[[名詞]]` wiki 連結的**純文字規則**。

行話天生是網狀的(縮寫 → 全稱 → 上位概念 → 同義詞),所以名詞內容裡可以直接
寫 `[[另一個名詞]]` 指過去。連結**依名稱解析,不存 id**:
  - 目標還不存在也寫得下去(先寫再補建,符合「capture now, organize later」)
  - 目標改名時不需要回頭改所有引用它的名詞……反過來說,改名會讓舊連結斷掉,
    這是刻意的取捨:id 連結要在編輯器裡塞不可見的識別碼,那就不再是「檔案為真」
    的純文字了,使用者拿文字編輯器打開 .md 也看不懂。

本模組**只碰字串**,不碰 SQLite、不碰檔案系統、不碰 HTTP——indexer 要 import 它
來建反向索引,所以它不能反過來 import indexer(會變成循環)。查詢(解析目標
id、反向連結)在 app/search.py,寫入(合併名詞時改寫連結目標)在 app/service.py。

連結是內文的一部分,所以真相仍然在 .md 檔裡;indexer 的 note_links 表跟 fts 一樣
只是可拋棄的反向索引,刪掉 index.db 重啟就會重算。
"""
import re

from .sanitize import norm_key

# `[[名稱]]`:名稱不得跨行、不得再含中括號(巢狀沒有意義,而且會讓貪婪比對出錯),
# 長度上限 120 是防呆——超過就不像名詞,比較可能是使用者打錯的括號吃掉整段文字。
WIKILINK_RE = re.compile(r"\[\[([^\[\]\r\n]{1,120})\]\]")


def link_texts(note: dict) -> list[str]:
    """一筆名詞裡「可能含連結」的所有文字段:說明欄 + 所有樣板欄位值。

    欄位值也要掃:同義詞/別名/多義這些欄位本來就是拿來寫「跟哪個名詞有關」的,
    只掃說明欄的話,那幾欄就仍然是死字串。
    """
    return [str(note.get("description") or "")] + [
        str(v or "") for v in (note.get("fields") or {}).values()
    ]


def link_targets(note: dict) -> list[str]:
    """抽出一筆名詞指向的所有名稱(依出現順序,以 norm_key 去重,保留原始寫法)。"""
    out: list[str] = []
    seen: set[str] = set()
    for text in link_texts(note):
        for m in WIKILINK_RE.finditer(text):
            name = m.group(1).strip()
            key = norm_key(name)
            if key and key not in seen:
                seen.add(key)
                out.append(name)
    return out


def retarget(text: str, old_key: str, new_name: str) -> str:
    """把 text 裡指向 old_key(norm_key)的連結改指到 new_name,其餘原樣不動。

    合併名詞時用:被併掉的那筆名字即將消失,指著它的連結要跟著改指到留下來的
    那筆,不然合併完會留下一堆斷掉的連結。
    """
    return WIKILINK_RE.sub(
        lambda m: f"[[{new_name}]]" if norm_key(m.group(1)) == old_key else m.group(0),
        str(text or ""),
    )


def retarget_note(note: dict, old_key: str, new_name: str) -> bool:
    """對一筆名詞(說明欄 + 所有欄位值)就地套用 retarget()。有改動回傳 True。"""
    changed = False
    desc = retarget(note.get("description", ""), old_key, new_name)
    if desc != note.get("description", ""):
        note["description"] = desc
        changed = True
    for k, v in (note.get("fields") or {}).items():
        nv = retarget(v, old_key, new_name)
        if nv != v:
            note["fields"][k] = nv
            changed = True
    return changed

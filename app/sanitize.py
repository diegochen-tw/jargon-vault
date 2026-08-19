"""
輸入清理層:把外部輸入(API body / 匯入檔)正規化成內部一致的形狀。

routers 收到的使用者輸入一律先過這裡,storage / indexer 假設拿到的都是乾淨資料。
"""
import re
import unicodedata

from .config import FIELD_KEY_RE, RESERVED_FIELD_KEYS

_WS_RE = re.compile(r"\s+")
# 近似比對要拿掉的標點/連字號(全形已先被 NFKC 折成半形,所以這裡只列半形)
_LOOSE_PUNCT_RE = re.compile(r"""[-_.·/\\()\[\]{}:;,!?'"]""")


def norm_key(s: str) -> str:
    """名詞的正規化比對鍵:NFKC(全形→半形)+ 小寫 + 去掉所有空白。

    同一把尺同時服務兩個功能,兩邊的「算不算同一個名詞」必須一致:
      1. `[[名詞]]` 連結的目標解析(app/links.py + app/search.py:resolve_names)
      2. 重複偵測的「同名」判定(app/dedup.py)

    前端 static/js/utils.js:normKey() 是同一套規則的 JS 版——連結要在渲染當下就
    知道指到的名詞存不存在(才畫得出「尚未建立」的樣式),不可能每個連結打一次
    API,所以規則必須兩邊各寫一份。**改這裡記得改那裡**。
    用 lower() 而不是 casefold() 也是為了對齊 JS 的 toLowerCase()。
    """
    return _WS_RE.sub("", unicodedata.normalize("NFKC", str(s or "")).lower())


def loose_key(s: str) -> str:
    """比 norm_key 再寬鬆一級:連標點與連字號都拿掉。

    只給重複偵測的「近似同名」用(cSSFI-123 / cSSFI 123 / ｃＳＳＦＩ123 視為同一個)。
    **刻意不拿來解析連結**——連結要指得精確,用這把尺會讓 [[A-1]] 誤連到 [[A1]]。
    """
    return _LOOSE_PUNCT_RE.sub("", norm_key(s))


def clean_tags(tags: list[str]) -> list[str]:
    seen, out = set(), []
    for t in tags:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def clean_fields(fields: dict) -> dict[str, str]:
    """
    樣板欄位值:key 必須合法且非保留字,value 一律轉字串後 strip。
    刻意不按樣板白名單過濾——樣板事後增減欄位時,note 裡的既有資料
    保留不丟(顯不顯示由顯示層決定),資料永不靜默消失。
    """
    out = {}
    for k, v in (fields or {}).items():
        k = str(k)
        if k in RESERVED_FIELD_KEYS or not FIELD_KEY_RE.match(k):
            continue
        out[k] = str(v or "").strip()
    return out


def clean_template_fields(defs: list) -> list[dict]:
    """
    樣板定義的欄位清單:驗證 key 格式/唯一/非保留字,不合法 raise ValueError
    (router 轉 400)。label 預設用 key,placeholder 預設用 label,
    enabled 沒帶就當啟用(舊的呼叫端/匯入檔不會因為少一個鍵而被關掉欄位)。
    清單順序即顯示順序,原樣保留——前端拖曳排序就是靠送上來的順序生效。
    """
    seen, out = set(), []
    for d in (defs or []):
        if not isinstance(d, dict):
            raise ValueError("欄位定義必須是物件")
        key = str(d.get("key") or "").strip()
        if not FIELD_KEY_RE.match(key):
            raise ValueError(f"欄位 key「{key}」不合法(小寫英文開頭,限 a-z0-9_,32 字內)")
        if key in RESERVED_FIELD_KEYS:
            raise ValueError(f"欄位 key「{key}」是保留字,不可使用")
        if key in seen:
            raise ValueError(f"欄位 key「{key}」重複")
        seen.add(key)
        label = str(d.get("label") or "").strip() or key
        placeholder = str(d.get("placeholder") or "").strip() or label
        out.append({"key": key, "label": label, "placeholder": placeholder,
                    "enabled": d.get("enabled", True) is not False})
    return out


def clean_attachments(attachments: list) -> list[dict]:
    out = []
    for a in attachments:
        if not isinstance(a, dict):
            continue
        name, path = str(a.get("name") or "").strip(), str(a.get("path") or "").strip()
        description = str(a.get("description") or "").strip()
        if name and path:
            out.append({"name": name, "path": path, "description": description})
    return out

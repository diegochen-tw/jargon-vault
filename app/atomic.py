"""
原子寫入:所有「真相來源」檔案的唯一寫入方式。

## 為什麼需要這一層

CLAUDE.md 把資料分成三類,第一類「真相來源」(`.md`、`tags.json`、
`templates.json`…)的定義就是**刪掉會資料永久消失**。但在這個模組出現之前,
它們全都是直接 `path.write_text(...)`——而那不是原子的:

    寫到一半斷電 / 程序被砍 / 磁碟滿  →  檔案變成截斷的半個 YAML / JSON

`index.db` 壞了有 `rebuild_index()` 兜底,`vectors.db` 壞了有自我修復,
唯獨最貴的那一類沒有任何防護。這個模組就是那層防護。

## 作法

先寫到**同一個目錄**底下的暫存檔,`fsync` 之後再 `os.replace()` 換過去。
`os.replace` 在 POSIX 與 Windows 都是原子的(不像 `os.rename`,後者在
Windows 上目標存在就會失敗)。任何時刻的讀取者看到的要嘛是舊的完整內容、
要嘛是新的完整內容,不會有第三種狀態。

暫存檔**必須跟目標同目錄**:跨檔案系統的 rename 不是原子的,而 `tempfile`
的預設目錄很可能在另一顆磁碟上。

## 邊界(誠實說明)

只 fsync 檔案本身,不 fsync 目錄項。POSIX 上完整的持久性保證需要後者,
但它在 Windows 沒有等價寫法,而本專案是單機自架、個人量級——
「內容不會半截」已經解決了實際會遇到的問題,「rename 本身在斷電後是否落盤」
留給檔案系統。這跟「暴力比對,不引入 numpy」是同一套取捨。
"""
import json
import os
import time
import uuid
from pathlib import Path

# Windows 專屬:防毒軟體/索引服務可能短暫持有目標檔的控制代碼,讓 os.replace
# 拋 PermissionError。這不是邏輯錯誤,重試幾次就過了。POSIX 上不會發生,
# 重試迴圈也就不會被觸發(第一次就成功)。
_REPLACE_RETRIES = 3
_REPLACE_BACKOFF = 0.05


def write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """原子地把 text 寫進 path。目標目錄必須已存在。

    ⚠ 暫存檔名必須**每次呼叫都不同**,不能只加 pid:FastAPI 的同步 handler 跑在
    threadpool 上,同一個行程裡兩條執行緒同時寫同一個檔時,固定檔名會讓
    B 在 A 寫到一半時把暫存檔截斷,A 再 replace 過去——結果就是這個模組
    要防的那種半截檔案,而且更難查(看起來「有原子寫入」卻還是壞了)。
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding=encoding, newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        _replace(tmp, path)
    finally:
        # 上面任何一步失敗都不能留下垃圾暫存檔;成功的話 tmp 已經不存在了。
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def write_json(path: Path, obj, *, indent: int = 2, sort_keys: bool = False) -> None:
    """原子地寫出 JSON。ensure_ascii 固定為 False——這個專案的資料是中文的,
    escape 成 \\uXXXX 會讓真相來源沒辦法用文字編輯器直接讀,違反「檔案為真」。"""
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=indent,
                                sort_keys=sort_keys))


def _replace(tmp: Path, path: Path) -> None:
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(_REPLACE_BACKOFF * (attempt + 1))

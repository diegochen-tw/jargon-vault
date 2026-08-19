"""
個人狀態登記層:「我和某筆名詞的關係」的持久化真相(`<使用者目錄>/progress.json`)。

## 為什麼要有這個模組

`marked`(書籤)與 `srs_box`/`srs_due`(複習排程)以前住在名詞的 `.md`
frontmatter 裡。在個人庫那沒有問題——檔案是我的,狀態也是我的,兩者無法區分。

團隊庫讓這個區分變成必須:同一個 `.md` 會被全隊共讀共寫,把我的書籤與複習
進度寫進去,結果就是**我的書籤全隊看得到、我複習一輪改掉全隊的排程、
兩個人同時複習互相覆蓋**。

    這三個欄位從來就不是「名詞」的屬性,是「**我和這個名詞的關係**」。

所以它們搬到個人側,以 `(vault_id, note_id)` 為鍵——同一個人對自己的庫與
對每個團隊庫各有一份進度,互不干擾。個人庫與團隊庫走**同一條路**
(兩套機制遲早漂移,見 CLAUDE.md 反覆出現的那條教訓)。

## ⚠ 這是第一類資料(真相來源),不是快取

刪掉 = 書籤與複習進度**永久消失**,沒辦法從 `.md` 重建。所以:

  - 寫入一律走 `atomic.write_json`(同其他真相來源)
  - 整站備份必須帶走它(它就在使用者目錄底下,`backup.py` 的既有規則自然涵蓋)
  - **不要**因為「進度聽起來像暫存資料」就把它當成可拋棄的東西

真正可拋棄的是 `index.db` 裡那張 `progress` 表——那是本檔的投影,啟動時
從這裡全量重建(見 `indexer.rebuild_index`)。**真相在 JSON,SQL 只是為了
讓 `ORDER BY` 能下推**;那張表的存在不改變「SQLite 一律可拋棄」這條規則。

## 形狀

    {
      "<vault_id>": {
        "<note_id>": {"marked": true, "srs_box": 2, "srs_due": 1785772909.5}
      }
    }

**沒有進度的名詞不會有 entry**(不是寫一筆全預設值)。理由與
`storage.dump_note()` 條件式寫出 srs 欄位完全相同:未複習過的 `srs_due` 要能
自然退回 `updated`,物化成具體值會讓那筆名詞永遠卡在複習佇列最前面。

本模組只碰檔案系統,不碰 SQLite、不碰 HTTP。
"""
import json

from . import atomic
from .paths import VaultPaths

_EMPTY = {"marked": False, "srs_box": None, "srs_due": None}


def blank_entry() -> dict:
    """「沒有任何進度」的預設值。呼叫端拿它當 fallback,不要各自寫字面值。"""
    return dict(_EMPTY)


def load_progress(paths: VaultPaths) -> dict[str, dict[str, dict]]:
    """讀整份進度。檔案不存在 = 還沒有任何進度,回空 dict 但**不建檔**
    (預設值就是空的,建一個空檔沒有意義)。"""
    try:
        data = json.loads(paths.progress_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, dict]] = {}
    for vault_id, notes in data.items():
        if not isinstance(notes, dict):
            continue
        clean: dict[str, dict] = {}
        for note_id, raw in notes.items():
            entry = _normalize(raw)
            if entry is not None:
                clean[str(note_id)] = entry
        if clean:
            out[str(vault_id)] = clean
    return out


def _normalize(raw) -> dict | None:
    """壞資料一律當成「沒有進度」略過,而不是讓整份檔案讀不出來——
    一筆壞掉不該讓使用者失去其他幾百筆的複習進度。"""
    if not isinstance(raw, dict):
        return None
    entry = blank_entry()
    entry["marked"] = raw.get("marked") is True
    box = raw.get("srs_box")
    if isinstance(box, bool) or not isinstance(box, int) or box < 0:
        entry["srs_box"] = None
    else:
        entry["srs_box"] = box
    try:
        due = raw.get("srs_due")
        entry["srs_due"] = float(due) if due is not None else None
    except (TypeError, ValueError):
        entry["srs_due"] = None
    # 沒複習過就不該有到期時間(否則等於把 srs_due 物化,見模組頂端說明)
    if entry["srs_box"] is None:
        entry["srs_due"] = None
    return entry if (entry["marked"] or entry["srs_box"] is not None) else None


def save_progress(paths: VaultPaths, data: dict[str, dict[str, dict]]) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    atomic.write_json(paths.progress_path, data, sort_keys=True)


def entries_for(paths: VaultPaths, vault_id: str) -> dict[str, dict]:
    """某個庫底下的全部進度({note_id: entry})。給 indexer 重建那張投影表用。"""
    return load_progress(paths).get(vault_id, {})


def get_entry(paths: VaultPaths, vault_id: str, note_id: str) -> dict:
    return load_progress(paths).get(vault_id, {}).get(note_id) or blank_entry()


def _update(paths: VaultPaths, vault_id: str, note_id: str, **changes) -> dict:
    data = load_progress(paths)
    per_vault = data.setdefault(vault_id, {})
    entry = per_vault.get(note_id) or blank_entry()
    entry.update(changes)
    # 回到「什麼都沒有」時就把整筆拿掉,不留一筆全預設值的殼:
    # 進度檔只記真的有進度的名詞(見模組頂端)。
    if entry["marked"] or entry["srs_box"] is not None:
        per_vault[note_id] = entry
    else:
        per_vault.pop(note_id, None)
    if not per_vault:
        data.pop(vault_id, None)
    save_progress(paths, data)
    return entry


def set_marked(paths: VaultPaths, vault_id: str, note_id: str, marked: bool) -> dict:
    return _update(paths, vault_id, note_id, marked=bool(marked))


def set_srs(paths: VaultPaths, vault_id: str, note_id: str,
            srs_box: int | None, srs_due: float | None) -> dict:
    return _update(paths, vault_id, note_id,
                   srs_box=None if srs_box is None else int(srs_box),
                   srs_due=None if srs_box is None else float(srs_due or 0))


def drop_note(paths: VaultPaths, vault_id: str, note_id: str) -> None:
    """名詞被**永久**刪除時清掉它的進度。

    ⚠ 搬進回收桶時**不要**呼叫這支:回收桶是可還原的,還原後複習進度應該跟著
    回來。這跟「刪除名詞 → 分享連結 404,還原 → 又能用」是同一個道理。
    """
    data = load_progress(paths)
    per_vault = data.get(vault_id)
    if not per_vault or note_id not in per_vault:
        return
    per_vault.pop(note_id, None)
    if not per_vault:
        data.pop(vault_id, None)
    save_progress(paths, data)


def merge_entries(entries: list[dict]) -> dict:
    """合併名詞時把多筆進度收成一筆:box 取最低(最不熟的那個決定進度)、
    due 取最早(該複習的時間不能因為合併而往後延)、marked 任一有標記就算。

    ⚠ **None(從沒複習過)在「取最低」的語意下就是最低**,不是「沒有值所以略過」:
    只要任一邊沒複習過,整筆就回到未複習。合併後的名詞含有兩邊的內容,其中一邊
    你根本沒看過,就不該因為另一邊已經爬到 box 5 而接下來 90 天都不再出現。
    """
    out = blank_entry()
    out["marked"] = any(e.get("marked") for e in entries)
    if not entries or any(e.get("srs_box") is None for e in entries):
        return out
    out["srs_box"] = min(e["srs_box"] for e in entries)
    dues = [e["srs_due"] for e in entries if e.get("srs_due") is not None]
    out["srs_due"] = min(dues) if dues else None
    return out


def seed_from_notes(paths: VaultPaths, vault_id: str, notes: list[dict]) -> int:
    """一次性遷移:把還留在 `.md` frontmatter 裡的 marked/srs 收進進度檔。

    只補、不覆蓋——進度檔裡已經有的那筆一律以進度檔為準(它才是新的真相)。
    回傳實際搬進來的筆數。冪等:搬過之後 `.md` 下次被寫回時就不再帶那些 key,
    重跑也只會是 0。

    ⚠ 刻意**不改寫 `.md`**:本專案對舊格式一律採懶遷移(見 storage.py 的
    `_parse_template_fields`),全量改寫幾百個檔只為了拿掉三個 key,風險遠大於好處。
    """
    data = load_progress(paths)
    per_vault = data.setdefault(vault_id, {})
    moved = 0
    for n in notes:
        nid = n.get("id")
        if not nid or nid in per_vault:
            continue
        entry = blank_entry()
        entry["marked"] = n.get("marked") is True
        box = n.get("srs_box")
        entry["srs_box"] = None if box is None else int(box)
        entry["srs_due"] = None if box is None else float(n.get("srs_due") or 0)
        if entry["marked"] or entry["srs_box"] is not None:
            per_vault[nid] = entry
            moved += 1
    if not per_vault:
        data.pop(vault_id, None)
    if moved:
        save_progress(paths, data)
    return moved

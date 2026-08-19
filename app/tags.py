"""
標籤登記層:標籤中繼資料(created 時間戳記、group 群組)的持久化真相。

標籤不像名詞有獨立內文與歷史,只有輕量的 key→中繼資料,所以每個使用者
整庫共用一份 data/users/<uid>/tags.json,不比照 notes 一個標籤一個檔案。
這份檔案不是可拋棄快取——「標籤第一次出現的時間」無法單靠重新掃描 notes
精確還原(標籤可能是名詞建立後才透過編輯加上的,且 note 的歷史只留
HISTORY_LIMIT 筆),所以要和 data/notes/ 一樣進版控、不能刪掉重建。

本模組只碰檔案系統,不碰 SQLite、不碰 HTTP。
"""
import json

from . import atomic
from .paths import VaultPaths
from .storage import read_note_file


def load_tags(paths: VaultPaths) -> dict[str, dict]:
    try:
        reg = json.loads(paths.tags_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    for meta in reg.values():  # 舊檔向後相容:補上 group 預設值(下次寫回才落盤)
        meta.setdefault("group", "")
    return reg


def save_tags(paths: VaultPaths, reg: dict[str, dict]) -> None:
    atomic.write_json(paths.tags_path, reg, sort_keys=True)


def merge_new_tags(reg: dict[str, dict], tag_names: list[str], created: float) -> bool:
    """把尚未登記的標籤補進 reg(就地修改),已登記的不動。回傳是否有變動。"""
    changed = False
    for t in tag_names:
        if t not in reg:
            reg[t] = {"created": created, "group": ""}
            changed = True
    return changed


def register_new_tags(paths: VaultPaths, tag_names: list[str], created: float) -> None:
    """單筆寫入路徑用:讀取、補登、有變動才寫回。"""
    reg = load_tags(paths)
    if merge_new_tags(reg, tag_names, created):
        save_tags(paths, reg)


def assign_group(paths: VaultPaths, tag_names: list[str], group: str) -> int:
    """
    把複數標籤的群組設為 group(建立群組與加入既有群組是同一件事;
    group 為空字串即「移出群組」)。未登記的標籤 raise ValueError。
    回傳受影響的標籤數。
    """
    group = group.strip()
    reg = load_tags(paths)
    missing = [t for t in tag_names if t not in reg]
    if missing:
        raise ValueError(f"標籤未登記:{'、'.join(missing)}")
    changed = 0
    for t in tag_names:
        if reg[t].get("group", "") != group:
            reg[t]["group"] = group
            changed += 1
    if changed:
        save_tags(paths, reg)
    return changed


def dissolve_group(paths: VaultPaths, group: str) -> int:
    """打散單一群組:把群組內所有標籤恢復成未分組。回傳受影響的標籤數。"""
    reg = load_tags(paths)
    changed = 0
    for meta in reg.values():
        if meta.get("group", "") == group:
            meta["group"] = ""
            changed += 1
    if changed:
        save_tags(paths, reg)
    return changed


def rename_group(paths: VaultPaths, old: str, new: str) -> int:
    """群組改名。回傳受影響的標籤數。

    群組沒有獨立記錄——它只是每個標籤的 group 字串(見檔頭),所以改名就是
    單一檔案的字串置換:**不動 .md、不動 SQLite、不用重建索引**,比 service
    層的 rename_tag 便宜得多。

    改成一個已存在的群組名 = **合併**,這裡刻意允許(呼叫端負責提醒使用者)——
    「把這兩組併起來」本來就是分組整理時的正常動作,而擋掉它只會逼使用者
    改成先打散再重新勾選。
    """
    old = old.strip()
    new = new.strip()
    if not new or new == old:
        return 0
    reg = load_tags(paths)
    changed = 0
    for meta in reg.values():
        if meta.get("group", "") == old:
            meta["group"] = new
            changed += 1
    if changed:
        save_tags(paths, reg)
    return changed


def dissolve_all_groups(paths: VaultPaths) -> int:
    """全部打散:所有標籤恢復成只有標籤的狀態。回傳受影響的標籤數。"""
    reg = load_tags(paths)
    changed = 0
    for meta in reg.values():
        if meta.get("group", ""):
            meta["group"] = ""
            changed += 1
    if changed:
        save_tags(paths, reg)
    return changed


def bootstrap_from_notes(paths: VaultPaths) -> None:
    """
    啟動時的自我修復:把已存在於 .md 檔、但登記簿裡還沒有的標籤補上。
    時間戳記用「目前掛著此標籤的筆記中最早的 created」推估——這是舊資料
    唯一能拿到的線索,不是精確的「標籤第一次被使用」時間,但這個功能上線
    後新加的標籤,時間戳記都會是精準值。
    """
    reg = load_tags(paths)
    changed = False
    for p in paths.notes_dir.glob("*.md"):
        note = read_note_file(p)
        if not note:
            continue
        for t in note["tags"]:
            if t not in reg:
                reg[t] = {"created": note["created"], "group": ""}
                changed = True
            elif note["created"] < reg[t]["created"]:
                reg[t]["created"] = note["created"]
                changed = True
    if changed:
        save_tags(paths, reg)

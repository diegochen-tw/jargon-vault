"""
檔案層:名詞的持久化真相(source of truth)。

每筆名詞一個 data/notes/<id>.md,格式為 YAML frontmatter + Markdown 內文。
SQLite 索引(indexer.py)只是這些檔案的可拋棄快取,任何寫入都必須先落地到檔案。

本模組只碰檔案系統,不碰資料庫、不碰 HTTP。
"""
import copy
import re
from pathlib import Path

import yaml

from . import atomic
from .config import DEFAULT_TEMPLATE_ID, HISTORY_LIMIT
from .paths import VaultPaths


def note_path(paths: VaultPaths, nid: str) -> Path:
    return paths.notes_dir / f"{nid}.md"


def dump_note(note: dict, extra: dict | None = None) -> str:
    """把名詞序列化成 .md 內容(YAML frontmatter + 內文)。

    extra 是額外附加在 frontmatter 尾端的欄位,目前只有回收桶的 deleted
    時間戳記在用(見 app/trash.py)——檔案格式的知識集中在這個模組,
    回收桶不自己拼 frontmatter。
    """
    front = {
        "id": note["id"],
        "name": note["name"],
        "template": note.get("template", DEFAULT_TEMPLATE_ID),  # 欄位樣板 id
        "fields": note.get("fields", {}),          # 樣板欄位值({key: value})
        "tags": note.get("tags", []),              # 標籤(多選;分組資訊在 tags.json)
        "attachments": note.get("attachments", []),  # 附件([{name,path,description}])
        "created": note["created"],
        "updated": note["updated"],
        "history": note.get("history", []),         # 前次版本快照,最多 HISTORY_LIMIT 筆
    }
    # ⚠ marked / srs_box / srs_due **刻意不寫進 .md**(舊版寫在這裡)。
    # 那三個是「我跟這筆名詞的關係」,不是名詞的屬性——真相搬到個人側的
    # progress.json(見 app/progress.py 檔頭)。團隊庫的 .md 是全隊共讀共寫的,
    # 把個人狀態寫進去就等於我的書籤全隊看得到、我複習一輪改掉全隊的排程。
    # 舊檔裡殘留的那三個 key 仍然**讀**得出來(見 _read_note),啟動時一次性
    # 收進 progress.json;不改寫舊檔,比照本專案一貫的懶遷移。
    front.update(extra or {})
    fm = yaml.safe_dump(front, allow_unicode=True, sort_keys=False).strip()
    body = note.get("description", "").rstrip()
    return f"---\n{fm}\n---\n\n{body}\n"


def write_note_file(paths: VaultPaths, note: dict) -> None:
    # 原子寫入:.md 是第一類資料(真相來源),半截的 YAML 就是永久遺失。見 app/atomic.py
    atomic.write_text(note_path(paths, note["id"]), dump_note(note))


def _parse_template_fields(front: dict) -> tuple[str, dict[str, str]]:
    """
    解析 frontmatter(或歷史快照)的樣板與欄位值,含舊格式懶遷移:
    改版前 alias/synonymy/polysemy 是頂層 key,讀入時折進 fields dict、
    樣板視為預設樣板;下次寫回自然落成新格式,不需要一次性全量改寫。
    """
    raw = front.get("fields")
    if isinstance(raw, dict):
        template = str(front.get("template") or DEFAULT_TEMPLATE_ID)
        fields = {str(k): str(v or "") for k, v in raw.items()}
    else:
        template = DEFAULT_TEMPLATE_ID
        fields = {
            "alias": str(front.get("alias") or ""),
            "synonymy": str(front.get("synonymy") or ""),
            "polysemy": str(front.get("polysemy") or ""),
        }
    return template, fields


def read_note_file(path: Path) -> dict | None:
    parsed = _read_note(path)
    return parsed[0] if parsed else None


def read_trashed_note(path: Path) -> dict | None:
    """讀回收桶裡的 .md:同 read_note_file,另外把 deleted 時間戳記帶進 note dict。

    刪除時間存在檔案自己的 frontmatter 裡(不另外維護一份登記簿),所以還原、
    列表、過期清理都只需要這一個檔案。deleted 缺漏(手動塞進來的檔案)回 0。
    """
    parsed = _read_note(path)
    if not parsed:
        return None
    note, front = parsed
    note["deleted"] = float(front.get("deleted", 0) or 0)
    return note


def _read_note(path: Path) -> tuple[dict, dict] | None:
    """解析一個名詞 .md,回傳 (note dict, 原始 frontmatter dict)。

    回傳原始 frontmatter 是為了讓回收桶讀得到 deleted 這種不屬於名詞本體的欄位,
    又不必讓每個一般名詞的 dict 都多帶一個永遠是 0 的 key。
    """
    try:
        text = path.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
        if not m:
            return None
        front = yaml.safe_load(m.group(1)) or {}
        template, fields = _parse_template_fields(front)
        return {
            "id": str(front.get("id", path.stem)),
            "name": str(front.get("name", "")),
            "template": template,
            "fields": fields,
            "tags": [str(t) for t in (front.get("tags") or [])],
            # category 是已淘汰的舊欄位:只讀不寫,供啟動遷移(service.py)辨識舊檔
            "category": str(front.get("category") or ""),
            "attachments": _parse_attachments(front.get("attachments")),
            # 書籤標記:這個欄位出現之前的舊檔沒有這個 key,讀成 False 即可(懶遷移,不全量改寫)
            "marked": bool(front.get("marked", False)),
            # SRS 複習排程(懶遷移,同樣不全量改寫):沒有 srs_box 這個 key 就是
            # 「從沒複習過」,srs_due 退回 updated——於是「久未觸碰的優先」變成
            # ORDER BY srs_due 的自然結果,不需要第二條查詢路徑,舊 .md 一行都不用改。
            "srs_box": None if front.get("srs_box") is None else int(front["srs_box"]),
            "srs_due": float(front.get("srs_due") or front.get("updated", 0)),
            "created": float(front.get("created", 0)),
            "updated": float(front.get("updated", 0)),
            "description": m.group(2).strip(),
            "history": parse_history(front.get("history")),
        }, front
    except Exception:
        return None


def _parse_attachments(raw) -> list[dict]:
    return [
        {"name": str(a.get("name", "")), "path": str(a.get("path", "")),
         "description": str(a.get("description") or "")}
        for a in (raw or []) if isinstance(a, dict)
    ]


def parse_history(raw) -> list[dict]:
    out = []
    for h in (raw or []):
        if not isinstance(h, dict):
            continue
        template, fields = _parse_template_fields(h)  # 舊格式快照走同一條懶遷移
        out.append({
            "name": str(h.get("name", "")),
            "template": template,
            "fields": fields,
            "tags": [str(t) for t in (h.get("tags") or [])],
            "attachments": _parse_attachments(h.get("attachments")),
            "description": str(h.get("description") or ""),
            "updated": float(h.get("updated", 0)),
        })
    return out[:HISTORY_LIMIT]


def snapshot_of(note: dict) -> dict:
    # 更新前把當前內容存成一筆快照,供回溯使用
    # ⚠ marked 與 srs_box/srs_due 刻意不進快照:兩者都不是版本化「內容」,
    #   放進來會讓「版本回復」把書籤與複習進度一起倒退回去。
    # 深拷貝 list/dict 欄位,避免快照與原欄位共用同一物件參照
    # (PyYAML 遇到共用參照會輸出 anchor/alias,讀回後仍是共用物件,
    #  日後若對其中一份做 in-place 修改會意外污染另一份)
    return {
        "name": note["name"], "template": note["template"], "fields": dict(note["fields"]),
        "tags": list(note["tags"]),
        "attachments": copy.deepcopy(note["attachments"]), "description": note["description"],
        "updated": note["updated"],
    }

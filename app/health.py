"""
內容健康度檢查:一次掃出「已經壞掉的」與「該回頭整理的」內容。

為什麼需要:這個專案的前提是「先記下來,之後再整理」(capture now, organize
later),而「之後再整理」目前只兌現了重複偵測(dedup.py)與標籤分組。真正會
**無聲壞掉**的那一類完全沒人看顧——附件的實體檔不見了、說明欄的內嵌圖片變成
破圖、`[[連結]]` 指向的名詞被改名了。這些都不會拋例外、不會出現在任何畫面上,
使用者是在幾個月後打開某筆名詞看到一個破掉的圖示才發現,而那時候原始檔案通常
已經找不回來了。整個 app 的價值建立在「記下來的東西之後真的還在」,所以這一層
不是錦上添花。

分三級,分級的依據是「要不要現在處理」而不是「技術上多嚴重」:

    error  真的壞了——內容已經有一部分讀不到了(破圖、附件檔案不存在、
           .md 解析不出來)。這一類會永久失去資料,愈早發現愈可能救得回來。
    warn   沒壞,但在浪費空間或藏著誤會(過大的檔案、沒有名詞引用的孤兒檔、
           指向不存在名詞的連結)。
    info   內容品質(沒有關鍵字、沒有說明)。**不是錯誤**,是「之後再整理」
           的待辦清單——所以刻意排在最後,不讓它把真正壞掉的東西淹掉。

`scan()` **只讀**:讀 .md、讀索引、stat 檔案大小,不寫任何東西,也不碰 HTTP。
`cleanup()` 會寫,所以整個模組的層級等同 semantic.py(複合層),不是 dedup.py。

「檢查」與「修」仍然是分開的兩支,而且**只有兩類問題可以自動修**
(missing_asset / missing_embed / orphan_file),理由是只有這三類的正確修法是
唯一的——「把已經指不到東西的引用拿掉」與「把沒人引用的檔案清掉」。其餘各類
一律不提供自動修:斷連結是打錯字還是還沒建、沒關鍵字該掛什麼、過大的檔案要不要
留,都**只有使用者知道**,猜錯就是破壞內容。

⚠ 自動刪除的三條防線,少一條都不可以:

  1. **先打包、再刪除**。ZIP 完整落地(含 fsync + 改名)之後才動任何一個檔案;
     打包途中出任何錯就整個中止,一個位元組都不刪。這是「孤兒檔判斷天生有
     偽陽性」的唯一解——正在新建、還沒按儲存的名詞,它的附件已經上傳到
     assets/<新 id>/ 了,從檔案看起來就是個沒人引用的孤兒,而後端**沒有辦法**
     知道誰的編輯器正開著。所以誤刪不是「可能發生」而是「一定會發生」,
     整個功能能不能做,就取決於誤刪之後救不救得回來。
  2. **伺服器自己重掃,絕不接受用戶端送來的路徑清單**。請求主體只帶「要清哪
     幾類」,不帶任何檔名。接受路徑的話這支端點就是一個「刪除任意檔案」的洞,
     而且它跑在已登入的身分底下,連 valid_id() 都擋不住(那些是相對路徑不是 id)。
     順帶解決另一個問題:使用者按下按鈕時看到的那份報告可能已經過期了。
  3. **改名詞內容不 bump `updated`、不寫歷史版本**。清掉一個指不到東西的引用是
     修復不是編輯:bump updated 會把「依上次編輯時間」的列表排序整個打亂,而
     HISTORY_LIMIT 只有 3,一次批次清理就會把使用者真正的編輯歷史沖光
     (理由與 service.replace_note_asset 完全相同)。復原路徑是那包 ZIP,不是歷史版本。

**截斷政策**:每一類最多回傳 HEALTH_MAX_PER_KIND 筆明細,但 `count` 一律是
精確值。這跟 dedup.py 那條「撈全表、不設上限」的規則沒有衝突——那條守的是
「『沒有重複』這種**結論**不能是被截斷截出來的」,而這裡被截斷的只有明細,
結論(有沒有問題、有幾個)永遠是完整掃完之後算出來的。
"""
import json
import re
import threading
import zipfile
from datetime import datetime
from pathlib import Path

from .config import HEALTH_MAX_PER_KIND, LARGE_ASSET_BYTES
from .indexer import db
from .paths import VaultPaths
from .storage import read_note_file
from .templates import load_templates

# 說明欄裡的內嵌圖片 `![alt](src)`。只認本站資產(assets/…),http(s):// 的外連圖
# 不驗——那會讓「掃描」變成一連串對外的網路請求(慢、會逾時、還會把使用者的
# 閱讀紀錄送給第三方站台),而且對方掛掉也不是這個庫的健康問題。
_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

# 各類問題的嚴重度。新增一類就在這裡登記——前端的分組、排序、顏色全部讀這張表,
# 沒登記的類別會被當成 info(不會壞掉,但顏色會不對)。
SEVERITY = {
    "unreadable": "error",      # .md 解析不出來(YAML 壞了/檔案截斷)
    "missing_asset": "error",   # 附件登記在案,實體檔不在
    "missing_embed": "error",   # 說明欄的內嵌圖片指向不存在的檔案(破圖)
    "large_asset": "warn",      # 檔案過大
    "orphan_file": "warn",      # 磁碟上有,但沒有任何名詞(含歷史版本)引用
    "broken_link": "warn",      # [[連結]] 指向不存在的名詞
    "missing_template": "warn", # template 指向不存在的樣板(外掛解除/樣板誤刪的孤兒)
    "no_tags": "info",          # 沒有任何關鍵字
    "empty_desc": "info",       # 沒有說明內容
}

# 顯示順序:先壞掉的,再浪費的,最後才是內容品質。同一級之內依這張表的順序。
_KIND_ORDER = list(SEVERITY)
_SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}


def _asset_rel(src: str) -> str:
    """內嵌圖片的 src → notes_dir 底下的相對路徑;不是本站資產一律回空字串。

    既有筆記裡兩種寫法都有(`/assets/…` 與 `assets/…`,見 routers/files.py 檔頭),
    所以一律去掉開頭的斜線再比對。
    """
    s = (src or "").strip().split("?")[0].split("#")[0].strip()
    if not s or "://" in s or s.startswith(("//", "data:")):
        return ""
    s = s.lstrip("/")
    return s if s.startswith("assets/") else ""


def _rel_of(att: dict) -> str:
    """附件登記的 path → notes_dir 底下的相對路徑(去掉開頭斜線)。"""
    return str(att.get("path") or "").strip().lstrip("/")


def scan(paths: VaultPaths) -> dict:
    """掃一個庫,回傳 {notes, assets, groups}。

    只走一趟 .md(附件、歷史快照、說明欄、標籤全部在同一份 note dict 裡拿得到),
    加一次 SQL(斷掉的連結走 note_links 的反向索引,不必自己再解析一次內文)。
    """
    issues: list[dict] = []
    referenced: set[str] = set()  # 被「任何一個版本」引用到的資產相對路徑
    note_count = 0

    def add(kind: str, note_id: str, name: str, detail: str = "", size: int = 0) -> None:
        issues.append({"kind": kind, "severity": SEVERITY.get(kind, "info"),
                       "note_id": note_id, "name": name, "detail": detail, "size": size})

    for p in sorted(paths.notes_dir.glob("*.md")):
        note = read_note_file(p)
        if not note:
            # read_note_file 把所有例外吞成 None(懶遷移要能容忍怪檔案),所以
            # 「讀不出來」在別的路徑上都是靜默跳過——這裡是唯一會講出來的地方。
            add("unreadable", p.stem, p.name)
            continue
        note_count += 1
        nid, name = note["id"], note["name"]

        for att in note.get("attachments", []):
            rel = _rel_of(att)
            if not rel:
                continue
            referenced.add(rel)
            f = paths.notes_dir / rel
            if not f.is_file():
                add("missing_asset", nid, name, att.get("name") or rel)
            else:
                size = f.stat().st_size
                if size > LARGE_ASSET_BYTES:
                    add("large_asset", nid, name, att.get("name") or rel, size)

        for m in _IMG_RE.finditer(note.get("description") or ""):
            rel = _asset_rel(m.group(1))
            if not rel:
                continue
            referenced.add(rel)
            if not (paths.notes_dir / rel).is_file():
                add("missing_embed", nid, name, rel)

        # 歷史快照引用到的檔案只登記、不檢查:版本回復要回復得出來,那些舊路徑
        # 就**不是**孤兒(見 service.replace_note_asset「寧可留下少數孤兒檔」)。
        # 但它們指向的檔案早就可能被清掉了,對著使用者看不到的舊版本報破圖只是雜訊。
        for h in note.get("history", []):
            for att in h.get("attachments", []):
                if rel := _rel_of(att):
                    referenced.add(rel)
            for m in _IMG_RE.finditer(h.get("description") or ""):
                if rel := _asset_rel(m.group(1)):
                    referenced.add(rel)

        if not note.get("tags"):
            add("no_tags", nid, name)
        if not (note.get("description") or "").strip():
            add("empty_desc", nid, name)

    asset_count, orphans = _orphans(paths, referenced)
    for rel, size in orphans:
        add("orphan_file", "", rel.rsplit("/", 1)[-1], rel, size)

    issues += _broken_links(paths)
    issues += _missing_templates(paths)
    return {"notes": note_count, "assets": asset_count, "groups": _group(issues)}


def _referenced_rels(paths: VaultPaths) -> set[str]:
    """被**任何一個版本**(現行內容 + 歷史快照)引用到的資產相對路徑。

    scan() 沒有用這一支——它在走 .md 做其他檢查時順手就把這個集合建起來了,
    拆出來會變成走兩趟檔案。cleanup() 需要獨立取得,才有這一支。
    「什麼算被引用」的規則只有這裡與 scan() 兩處,兩邊必須一致,否則
    「檢查說是孤兒、清理卻不刪」(或更糟,反過來)。
    """
    referenced: set[str] = set()
    for p in sorted(paths.notes_dir.glob("*.md")):
        note = read_note_file(p)
        if not note:
            continue
        for versions in ([note], note.get("history", [])):
            for v in versions:
                for att in v.get("attachments", []):
                    if rel := _rel_of(att):
                        referenced.add(rel)
                for m in _IMG_RE.finditer(v.get("description") or ""):
                    if rel := _asset_rel(m.group(1)):
                        referenced.add(rel)
    return referenced


def _orphans(paths: VaultPaths, referenced: set[str]) -> tuple[int, list[tuple[str, int]]]:
    """(資產檔總數, [(相對路徑, 位元組數)])。**孤兒的定義只有這一處**——
    scan() 用它報告、cleanup() 用它決定刪什麼,兩者必然一致。"""
    count = 0
    out: list[tuple[str, int]] = []
    if not paths.assets_dir.is_dir():
        return 0, out
    for f in sorted(paths.assets_dir.rglob("*")):
        if not f.is_file():
            continue
        count += 1
        rel = f.relative_to(paths.notes_dir).as_posix()
        if rel not in referenced:
            out.append((rel, f.stat().st_size))
    return count, out


def _broken_links(paths: VaultPaths) -> list[dict]:
    """`[[名詞]]` 指向不存在的名詞。

    走 note_links 的反向索引(target_key 是目標名稱的 norm_key,目標不必存在),
    LEFT JOIN 回 notes 撈不到就是斷的——一次 SQL 掃完全庫,不必在 Python 端
    重新解析每一筆的內文。

    ⚠ 這不必然是錯誤:「目標還不存在也寫得下去」是刻意的設計(見 app/links.py),
    所以嚴重度是 warn 不是 error。它同時涵蓋兩種情況——還沒建立的名詞,以及
    目標被改名之後斷掉的舊連結——而**哪一種只有使用者知道**,所以照實列出來,
    不猜。
    """
    conn = db(paths)
    rows = conn.execute(
        "SELECT l.src_id AS src_id, l.target_name AS target_name, n.name AS src_name "
        "FROM note_links l JOIN notes n ON n.id = l.src_id "
        "LEFT JOIN notes t ON t.name_key = l.target_key "
        "WHERE t.id IS NULL ORDER BY n.name, l.target_name"
    ).fetchall()
    conn.close()
    return [{"kind": "broken_link", "severity": SEVERITY["broken_link"],
             "note_id": r["src_id"], "name": r["src_name"],
             "detail": r["target_name"], "size": 0} for r in rows]


def _missing_templates(paths: VaultPaths) -> list[dict]:
    """`template` 指向不存在的樣板——欄位樣板類外掛解除安裝(或 MCP 刪樣板)
    留下的孤兒。資料一個位元組都沒少(欄位值在名詞自身上,顯示層會用 key 合成),
    但欄位標題退化成機器字串、新建下拉也選不到,所以列出來讓使用者決定:
    重新安裝外掛(定義復活)或轉到別的樣板(設定頁的批次轉換走
    POST /api/templates/retarget)。detail 放那個不存在的樣板 id。

    ⚠ 不進 CLEANABLE:自動改 template 是在改內容,而「該轉去哪個樣板」只有
    使用者知道(見檔頭——只有正確修法唯一的才自動修)。
    """
    known = {t.get("id") for t in load_templates(paths)}
    conn = db(paths)
    rows = conn.execute(
        "SELECT id, name, template FROM notes "
        "WHERE template NOT IN (%s) ORDER BY template, name"
        % ",".join("?" * len(known)), list(known)
    ).fetchall() if known else []
    conn.close()
    return [{"kind": "missing_template", "severity": SEVERITY["missing_template"],
             "note_id": r["id"], "name": r["name"], "detail": r["template"],
             "size": 0} for r in rows]


def _group(issues: list[dict]) -> list[dict]:
    """依 kind 收成一組一組,每組帶精確的 count 與(可能被截斷的)明細。"""
    by_kind: dict[str, list[dict]] = {}
    for it in issues:
        by_kind.setdefault(it["kind"], []).append(it)

    def order(kind: str) -> tuple[int, int]:
        sev = SEVERITY.get(kind, "info")
        idx = _KIND_ORDER.index(kind) if kind in _KIND_ORDER else len(_KIND_ORDER)
        return (_SEVERITY_ORDER[sev], idx)

    return [{
        "kind": kind,
        "severity": SEVERITY.get(kind, "info"),
        "count": len(items),
        "items": items[:HEALTH_MAX_PER_KIND],
    } for kind, items in sorted(by_kind.items(), key=lambda kv: order(kv[0]))]


# ── 清理(先打包成 ZIP,再刪)──────────────────────────────────────────────

# 只有這三類的正確修法是唯一的,所以只有這三類可以自動修(見檔頭)。
CLEANABLE = ("missing_asset", "missing_embed", "orphan_file")

CLEANUP_NAME_RE = re.compile(r"^health-cleanup-\d{8}-\d{6}(_\d+)?\.zip$")
CLEANUP_KEEP = 10          # 保留最近幾包,超過的自動清掉(同備份的保留策略)
MANIFEST_NAME = "manifest.json"

# 同一個使用者同一秒連按兩次會挑到同一個檔名,後寫的把先寫的蓋掉——
# 而那一包裡是**剛剛被刪掉的東西**,蓋掉就等於復原點沒了(同 backup.py 的鎖)。
_cleanup_lock = threading.Lock()


def valid_cleanup_name(name: str) -> bool:
    """檔名白名單。理由同 backup.valid_backup_name():名字來自網址,
    少了這一關 `../../users.json` 就能被下載。"""
    return bool(CLEANUP_NAME_RE.match(name))


def _img_ref_re(rel: str) -> re.Pattern:
    """比對指向 rel 這個資產的 `![alt](src)`(src 帶不帶開頭斜線都認)。"""
    return re.compile(r"!\[[^\]]*\]\(\s*/?" + re.escape(rel) + r"\s*\)")


def cleanup(paths: VaultPaths, kinds: list[str]) -> dict:
    """清掉指不到東西的圖片引用與沒人引用的孤兒檔,**先打包成 ZIP 存證**。

    回傳 {name, removed_files, removed_refs, bytes, notes_touched}。
    name 是那包 ZIP 的檔名(給前端接著下載);沒有任何東西可清時 name 為 ""。

    ⚠ kinds 只用來決定「清哪幾類」,**要清哪些檔案是這裡自己重掃決定的**
    (見檔頭第 2 條)。
    """
    from .service import persist_note   # 延後 import:模組層級會與 service 形成循環

    kinds = [k for k in kinds if k in CLEANABLE]
    if not kinds:
        return {"name": "", "removed_files": 0, "removed_refs": 0,
                "bytes": 0, "notes_touched": 0}

    # ⚠ 這裡**重掃**,而且不是讀 scan() 的 items——那份有 HEALTH_MAX_PER_KIND
    # 上限,照著它刪只會清掉前 50 筆,使用者按了「全部清掉」卻沒清乾淨。
    orphans: list[Path] = []
    if "orphan_file" in kinds:
        _, rels = _orphans(paths, _referenced_rels(paths))
        orphans = [paths.notes_dir / rel for rel, _ in rels]

    # 名詞裡指不到東西的引用:{nid: (note, [附件相對路徑], [內嵌相對路徑])}
    broken: dict[str, tuple[dict, list[str], list[str]]] = {}
    for kind in ("missing_asset", "missing_embed"):
        if kind not in kinds:
            continue
        for it in _all_broken_refs(paths, kind):
            entry = broken.setdefault(it["note_id"], (it["note"], [], []))
            (entry[1] if kind == "missing_asset" else entry[2]).append(it["rel"])

    if not orphans and not broken:
        return {"name": "", "removed_files": 0, "removed_refs": 0,
                "bytes": 0, "notes_touched": 0}

    total_bytes = sum(f.stat().st_size for f in orphans if f.is_file())
    ref_count = sum(len(a) + len(b) for _, a, b in broken.values())

    # ── 1. 先打包 ───────────────────────────────────────────────────────
    # 這一步失敗就整個中止,一個位元組都不刪(檔頭第 1 條)。
    paths.cleanup_dir.mkdir(parents=True, exist_ok=True)
    with _cleanup_lock:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = paths.cleanup_dir / f"health-cleanup-{stamp}.zip"
        n = 1
        while dest.exists():
            dest = paths.cleanup_dir / f"health-cleanup-{stamp}_{n}.zip"
            n += 1
        tmp = dest.with_suffix(".zip.part")
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                for f in orphans:
                    # 孤兒檔本體:照 notes_dir 底下的相對路徑放,還原時原樣複製回去即可
                    z.write(f, f"orphan-files/{f.relative_to(paths.notes_dir).as_posix()}")
                for nid, (note, atts, embeds) in broken.items():
                    # 破圖那兩類沒有檔案可存(檔案本來就不在了),要存的是
                    # **改動前的 .md 全文**——那才是「被刪掉的東西」。
                    src = paths.notes_dir / f"{nid}.md"
                    if src.is_file():
                        z.write(src, f"notes-before/{nid}.md")
                z.writestr(MANIFEST_NAME, json.dumps({
                    "created": datetime.now().isoformat(timespec="seconds"),
                    "vault": paths.vault_id,
                    "kinds": kinds,
                    "orphan_files": [f.relative_to(paths.notes_dir).as_posix() for f in orphans],
                    "broken_refs": [
                        {"note_id": nid, "name": note.get("name", ""),
                         "attachments": atts, "embedded": embeds}
                        for nid, (note, atts, embeds) in broken.items()
                    ],
                }, ensure_ascii=False, indent=2))
            # 先寫 .part 再改名:中途當掉不會留下一包「看起來完整、其實是半包」的
            # ZIP,而使用者是在那包完整的前提下才准我們刪東西的。
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)

    # ── 2. ZIP 已經完整落地,現在才刪 ───────────────────────────────────
    for f in orphans:
        f.unlink(missing_ok=True)

    for nid, (note, atts, embeds) in broken.items():
        if atts:
            drop = set(atts)
            note["attachments"] = [a for a in note.get("attachments", [])
                                   if _rel_of(a) not in drop]
        for rel in embeds:
            note["description"] = _img_ref_re(rel).sub("", note["description"])
        # ⚠ 不動 updated、不寫歷史版本(檔頭第 3 條)。persist_note 原樣寫回,
        # updated 由呼叫端決定——這裡刻意不碰它。
        persist_note(paths, note)

    _prune_old(paths)
    return {"name": dest.name, "removed_files": len(orphans),
            "removed_refs": ref_count, "bytes": total_bytes,
            "notes_touched": len(broken)}


def _all_broken_refs(paths: VaultPaths, kind: str) -> list[dict]:
    """完整的破圖清單,並附上那筆名詞的 dict(等一下要就地改)。"""
    out = []
    for p in sorted(paths.notes_dir.glob("*.md")):
        note = read_note_file(p)
        if not note:
            continue
        if kind == "missing_asset":
            for att in note.get("attachments", []):
                rel = _rel_of(att)
                if rel and not (paths.notes_dir / rel).is_file():
                    out.append({"note_id": note["id"], "note": note, "rel": rel})
        else:
            for m in _IMG_RE.finditer(note.get("description") or ""):
                rel = _asset_rel(m.group(1))
                if rel and not (paths.notes_dir / rel).is_file():
                    out.append({"note_id": note["id"], "note": note, "rel": rel})
    # 同一筆名詞要共用同一份 note dict,否則兩類各改各的、後寫的會蓋掉先寫的
    seen: dict[str, dict] = {}
    for it in out:
        it["note"] = seen.setdefault(it["note_id"], it["note"])
    return out


def list_cleanups(paths: VaultPaths) -> list[dict]:
    """存證 ZIP 清單(新到舊)。"""
    if not paths.cleanup_dir.is_dir():
        return []
    out = []
    for f in sorted(paths.cleanup_dir.glob("health-cleanup-*.zip"), reverse=True):
        if valid_cleanup_name(f.name):
            out.append({"name": f.name, "size": f.stat().st_size,
                        "created": f.stat().st_mtime})
    return out


def _prune_old(paths: VaultPaths) -> None:
    """只保留最近 CLEANUP_KEEP 包。它們是復原點不是真相,留太多只是佔空間。"""
    for f in [paths.cleanup_dir / c["name"] for c in list_cleanups(paths)][CLEANUP_KEEP:]:
        f.unlink(missing_ok=True)

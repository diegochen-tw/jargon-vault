"""
範例資料種子:把 repo 裡的 demo/ 那一小包名詞複製進一個使用者的庫。

為什麼要有這一層:非 Docker 安裝(python main.py、桌面啟動器)註冊完拿到的是
完全空白的庫,新使用者沒有任何東西可看、也不知道欄位樣板長什麼樣。註冊時種一份
範例進去,配上前端可自行關閉的置頂行(按下 = 只刪這些範例,見 routers/demo.py),
讓「開箱有東西」與「隨時清乾淨」兩件事同時成立。

三條紅線:

1. **只在庫是空的時候動作**。`seed_vault()` 看到 notes_dir 已有任何 .md 就整支放棄。
   這不只是防重複——`migration.migrate_legacy_data_if_needed()` 用
   `shutil.move` + `if not dest.exists()` 搬舊版的整個 notes/ 目錄,種子先把
   目錄填出來就會**靜默**擋掉那次搬遷。順序上種子排在 legacy 遷移之後,
   這條防線是第二道保險。

2. **種進去的就是使用者的資料**。demo/ 是種子不是真相:之後改 repo 裡的檔案不會
   回頭影響已經種過的帳號,也絕不會有「重新同步範例」這種動作去覆蓋使用者的修改。

3. **不碰 SQLite、不碰 HTTP、不呼叫 AI**。只複製檔案與啟用樣板;索引由呼叫端在
   種完之後 rebuild(順序反了會搜不到,要等下次重啟才補回來)。

`demo_note_ids()` 是「哪些名詞算範例」的唯一定義,從 demo/notes/ 的檔名推出——
刪除端點與測試都問它,不另外維護一份寫死的清單。
"""
import json
import logging
import shutil

from .config import DEMO_DIR
from .paths import VaultPaths
from .templates import load_templates, save_templates

log = logging.getLogger("jargon_vault")


def demo_note_ids() -> set[str]:
    """範例名詞的 id 集合(= demo/notes/ 的檔名去掉副檔名)。

    刻意從檔案系統推,不寫死清單:demo bundle 增刪名詞時不會有第二個地方要同步。
    demo/ 目錄不存在(壞部署、或刻意不發佈範例)就回空集合,呼叫端一律當「沒有範例」。
    """
    notes_dir = DEMO_DIR / "notes"
    if not notes_dir.is_dir():
        return set()
    return {p.stem for p in notes_dir.glob("*.md")}


def _demo_templates() -> set[str]:
    """範例名詞用到的樣板 id(從 frontmatter 的 template: 那一行掃出來)。

    只讀 frontmatter 的前幾行、不做完整 YAML 解析:這裡要的只是「哪些樣板要打開」,
    真正的讀檔在 storage.py。掃不出來就當沒有,樣板保持原本的啟用狀態。
    """
    out = set()
    for md in sorted((DEMO_DIR / "notes").glob("*.md")):
        try:
            for line in md.read_text(encoding="utf-8").splitlines()[:20]:
                if line.startswith("template:"):
                    out.add(line.split(":", 1)[1].strip())
                    break
        except OSError:
            continue
    return out


def seed_vault(paths: VaultPaths) -> int:
    """把 demo/ 的範例名詞、資產與標籤複製進這個庫,回傳種入的名詞筆數。

    庫裡已經有任何 .md,或 demo/ 目錄不存在 → 什麼都不做、回 0(理由見檔頭第 1 條)。
    ⚠ 呼叫端必須在這之後才 rebuild_index(),否則種進去的名詞搜不到。
    """
    notes_src = DEMO_DIR / "notes"
    if not notes_src.is_dir():
        log.warning("找不到範例資料目錄 %s——demo/ 是不是沒跟著部署?", notes_src)
        return 0
    if any(paths.notes_dir.glob("*.md")):
        return 0  # 非空庫一律不碰(也保護 legacy 遷移的搬遷路徑)

    seeded = 0
    for md in sorted(notes_src.glob("*.md")):
        shutil.copy(str(md), str(paths.notes_dir / md.name))
        seeded += 1
    if not seeded:
        return 0

    # 附件:demo/assets/<note_id>/ → <庫>/notes/assets/<note_id>/
    assets_src = DEMO_DIR / "assets"
    if assets_src.is_dir():
        for sub in sorted(assets_src.iterdir()):
            if sub.is_dir():
                shutil.copytree(str(sub), str(paths.assets_dir / sub.name), dirs_exist_ok=True)

    # 標籤(含群組結構)。庫是空的才會走到這裡,所以直接覆蓋不會蓋掉使用者的東西。
    src_tags = DEMO_DIR / "tags.json"
    if src_tags.exists():
        shutil.copy(str(src_tags), str(paths.tags_path))

    # 範例名詞用到的樣板要打開,否則使用者看得到範例卻在新建下拉裡找不到那個樣板。
    # 只開、不關:使用者/種子既有的啟用狀態不動。
    wanted = _demo_templates()
    if wanted:
        templates = load_templates(paths)
        changed = False
        for t in templates:
            if t.get("id") in wanted and t.get("enabled") is not True:
                t["enabled"] = True
                changed = True
        if changed:
            save_templates(paths, templates)

    return seeded


def purge_vault(paths: VaultPaths) -> list[str]:
    """刪掉這個庫裡的範例名詞,回傳實際刪掉的 id 清單。

    ⚠ 只認 demo_note_ids() 裡的 id——絕不可以退回 service.delete_all_notes(),
    那會把使用者自己建立的名詞一起炸掉。實際的刪除(索引、資產、進度)由呼叫端
    走既有的永久刪除路徑,這裡只負責「哪些該刪」。

    使用者可能早就手動刪光了範例,回空清單是正常結果不是錯誤。
    """
    ids = demo_note_ids()
    return sorted(nid for nid in ids if (paths.notes_dir / f"{nid}.md").exists())


def orphan_demo_tags(paths: VaultPaths, remaining_tags: set[str]) -> list[str]:
    """範例標籤裡已經沒有名詞在用的那些(刪除範例後用來收尾)。

    只清「範例自己帶進來的」標籤:使用者後來自己建的標籤即使目前沒名詞在用,
    也不該因為按了刪除範例而消失。
    """
    src_tags = DEMO_DIR / "tags.json"
    if not src_tags.exists():
        return []
    try:
        seeded = json.loads(src_tags.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(seeded, dict):
        return []
    return sorted(name for name in seeded if name not in remaining_tags)

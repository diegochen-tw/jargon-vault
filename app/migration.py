"""
一次性舊資料遷移。目前有兩支,彼此獨立:

1. `migrate_legacy_data_if_needed()` —— 把改多使用者之前扁平的 data/notes、
   tags.json、templates.json、ai_settings.json 整批搬進系統裡第一個註冊的使用者的
   私有目錄底下。只執行一次(用標記檔 data/.legacy_migrated 保證冪等),由
   routers/auth.py 在「系統一個使用者都還沒有」時的第一次註冊/Google 登入呼叫。
   index.db 是可拋棄快取不搬——讓 rebuild_index(paths) 對搬完的內容重建即可。

2. `migrate_ai_settings_to_site()` —— 每使用者一份的 AI 連線設定收斂成站台唯一
   一組(見 app/ai_settings.py)時,把管理者原本的設定搬進 site_settings.json。
   啟動時跑,見該函式的 docstring。
"""
import json
import shutil

from . import atomic, site_settings, users
from .config import DATA_DIR
from .paths import all_existing_user_ids, user_paths

LEGACY_MIGRATED_MARKER = DATA_DIR / ".legacy_migrated"


def migrate_legacy_data_if_needed(target_user_id: str) -> None:
    if LEGACY_MIGRATED_MARKER.exists():
        return
    if not (DATA_DIR / "notes").exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        atomic.write_text(LEGACY_MIGRATED_MARKER, "no_legacy_data\n")
        return

    target = user_paths(target_user_id)
    target.root.mkdir(parents=True, exist_ok=True)
    for name, dest in [
        ("notes", target.notes_dir),
        ("tags.json", target.tags_path),
        ("templates.json", target.templates_path),
        ("ai_settings.json", target.ai_settings_path),
    ]:
        src = DATA_DIR / name
        if src.exists() and not dest.exists():
            shutil.move(str(src), str(dest))

    atomic.write_text(LEGACY_MIGRATED_MARKER, f"migrated_to={target_user_id}\n")


def _seed_candidate_uids() -> list[str]:
    """種子的挑選順序:store admin 優先(AI 連線本來就是他在設定的),再來是任何
    一個使用者。掃目錄而不是只看登記簿,理由同 paths.all_existing_user_ids()。"""
    existing = set(all_existing_user_ids())
    try:
        registry = users.load_users()
    except Exception:      # 登記簿壞掉也不該讓遷移擋住啟動
        registry = []
    admins = [u["id"] for u in registry if u.get("is_admin") and u["id"] in existing]
    others = [u["id"] for u in registry if u["id"] in existing and u["id"] not in admins]
    return admins + others


def migrate_ai_settings_to_site() -> None:
    """把某位管理者的舊 `<使用者目錄>/ai_settings.json` 種進站台設定的 `ai` 區塊。

    **只在 site_settings.json 裡還沒有 "ai" 這個鍵時動作**,所以天生冪等,不需要
    標記檔——寫完之後那個鍵就存在了。

    ⚠ 沒有這一步的話,既有站台升級後 `_normalize()` 會直接填出廠預設,admin 原本
    設好的服務位址、模型與金鑰全部被無聲換掉,而且不會有任何錯誤訊息——只會表現成
    「升級之後 AI 突然連不上」。舊檔留在原地不刪(備份照樣帶走,要回頭救得回來)。
    """
    try:
        raw = json.loads(site_settings.SITE_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return          # 檔案還不存在 → ensure_site_settings() 會用預設值建一份,沒有舊值可搬
    if not isinstance(raw, dict) or "ai" in raw:
        return

    seed = None
    for uid in _seed_candidate_uids():
        try:
            data = json.loads(user_paths(uid).ai_settings_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict) and data:
            seed = data
            break

    raw["ai"] = seed if seed else site_settings._default_ai()
    site_settings.save_site_settings(raw)   # _clean_ai() 會把舊檔的欄位正規化

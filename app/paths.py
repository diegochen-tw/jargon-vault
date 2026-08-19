"""
每個「庫」(vault)路徑組合的唯一來源。

一個 vault 是一個自成一體的名詞庫——notes/ + tags.json + templates.json +
index.db + … 全部在同一個目錄底下,住在 data/users/<uid>/。

底下每個模組(storage / indexer / search / tags / templates / trash …)都只收
`paths: VaultPaths` 當第一個參數,不自行拼路徑。這個間接層刻意保留:
它曾讓團隊庫以極小改動接上(2026-08 拆除),也是將來任何「不只一個庫」的
功能回來時的接縫——dataclass 除了 `vault_id` 之外,沒有任何欄位在語意上
綁定「使用者」。

(user vault 的佈局完全比照改多使用者之前扁平的 data/ 結構,只是巢狀了
一層——notes/tags.json/templates.json/index.db 的相對位置一模一樣。)

    ai_settings_path                  **已經沒有人讀它了**
        AI 連線設定收斂成站台唯一一組(data/site_settings.json 的 ai 區塊,
        見 app/ai_settings.py)。這個欄位留著,只是為了讓既有使用者目錄底下
        那份舊檔仍在備份範圍內、遷移時也找得到(migration.migrate_ai_settings_to_site)。
        **新的程式碼不要再用它存任何東西。**
"""
from dataclasses import dataclass
from pathlib import Path

from .config import DATA_DIR

USERS_DIR = DATA_DIR / "users"


@dataclass(frozen=True)
class VaultPaths:
    vault_id: str
    root: Path              # <資料目錄>/
    notes_dir: Path         # <資料目錄>/notes/
    assets_dir: Path        # <資料目錄>/notes/assets/
    # 回收桶刻意放在 notes_dir 之外:indexer/service 到處都是 notes_dir.glob("*.md"),
    # 只要待在那個 glob 掃不到的地方,刪掉的名詞就不可能從搜尋/統計/匯出裡冒出來。
    trash_dir: Path         # <資料目錄>/trash/
    trash_assets_dir: Path  # <資料目錄>/trash/assets/
    tags_path: Path         # <資料目錄>/tags.json
    templates_path: Path    # <資料目錄>/templates.json
    ai_settings_path: Path  # <資料目錄>/ai_settings.json   ※ 舊檔,已無人讀取(見檔頭)
    plugins_path: Path      # <資料目錄>/plugins.json
    # 公開分享連結登記簿。是使用者本人的意圖,所以放在自己的目錄底下
    # (隔離模型不變),而不是做成全域檔;刪帳號時隨 rmtree 一起消失。
    shares_path: Path       # <資料目錄>/shares.json
    # 個人狀態(書籤 + 複習排程),以 (vault_id, note_id) 為鍵。
    # ⚠ **第一類資料**:刪掉 = 進度永久消失,無法從 .md 重建。
    progress_path: Path     # <資料目錄>/progress.json
    db_path: Path           # <資料目錄>/index.db
    # 語意檢索的向量快取。**刻意跟 index.db 分開**:rebuild_index() 每次啟動都
    # DROP TABLE 全量重建,而每一筆向量都要打一次模型——放進 index.db 等於每次
    # 重啟把整個庫重新嵌入一遍,還是同步阻塞 create_app()。它是第三種資料:
    # 可從 .md 完整重建,但**絕不自動重建**(見 app/vectors.py 檔頭)。
    vectors_path: Path      # <資料目錄>/vectors.db
    # 內容健康度檢查「清理」動作的存證 ZIP(見 app/health.py:cleanup)。
    # ⚠ 這**不是第四個全域真相檔**、也不是新的一類資料:性質完全比照
    # data/backups/*.zip ——它是**被刪掉那些東西的副本**,不是真相本身,
    # 刪光只是失去復原點,不會有任何現存資料消失。放在使用者自己的目錄底下,
    # 所以刪帳號時隨 rmtree 一起消失,隔離模型不變。
    cleanup_dir: Path       # <資料目錄>/cleanup/


def _compose(vault_id: str, root: Path) -> VaultPaths:
    notes_dir = root / "notes"
    trash_dir = root / "trash"
    return VaultPaths(
        vault_id=vault_id,
        root=root,
        notes_dir=notes_dir,
        assets_dir=notes_dir / "assets",
        trash_dir=trash_dir,
        trash_assets_dir=trash_dir / "assets",
        tags_path=root / "tags.json",
        templates_path=root / "templates.json",
        ai_settings_path=root / "ai_settings.json",
        plugins_path=root / "plugins.json",
        shares_path=root / "shares.json",
        progress_path=root / "progress.json",
        db_path=root / "index.db",
        vectors_path=root / "vectors.db",
        cleanup_dir=root / "cleanup",
    )


def user_paths(user_id: str) -> VaultPaths:
    # uid 一律來自已簽章的 session cookie(可信),所以這裡不驗 valid_id;
    # 任何把「來自網址的 id」組進路徑的新工廠都必須自己驗(先例:app/publish.py)。
    return _compose(user_id, USERS_DIR / user_id)


def ensure_user_dirs(paths: VaultPaths) -> None:
    paths.notes_dir.mkdir(parents=True, exist_ok=True)
    paths.assets_dir.mkdir(parents=True, exist_ok=True)
    paths.trash_dir.mkdir(parents=True, exist_ok=True)
    paths.trash_assets_dir.mkdir(parents=True, exist_ok=True)


def all_existing_user_ids() -> list[str]:
    """啟動時列舉現有使用者目錄,用來逐一跑 ensure/rebuild。"""
    return _ids_under(USERS_DIR)


def _ids_under(base: Path) -> list[str]:
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir())

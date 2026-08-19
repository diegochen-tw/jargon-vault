"""
組態層:全域路徑與常數的唯一來源。

每使用者的資料路徑(notes/tags/templates/progress/index.db)不在這裡——
那些搬到 app/paths.py 的 VaultPaths,因為每個使用者各自一份,不是全域常數。
這裡只留真正跨使用者共用的東西:資料根目錄、使用者登記簿存放的地方、
靜態檔案目錄、log、id 格式等純規則。

環境變數:
    GLOSSARY_DATA_DIR  覆寫資料目錄(預設 <repo>/data)。
                       測試時指向臨時目錄即可完全隔離真實資料。
"""
import os
import re
import sys
from pathlib import Path

def _bundle_root() -> Path:
    """唯讀資源(static/、official_plugins/、demo/)的根目錄。
    PyInstaller 凍結後原始碼被解到 sys._MEIPASS;原始碼佈局下就是 repo 根。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def _default_data_dir() -> Path:
    """真相來源的落腳處。⚠ 凍結執行檔絕不可以用 ROOT/data——onefile 的 ROOT 是
    每次啟動重建、離開就刪掉的臨時目錄,寫進去 = 關掉程式資料全部消失。"""
    if not getattr(sys, "frozen", False):
        return _bundle_root() / "data"          # 原始碼 / Docker:維持現況
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return base / "JargonVault" / "data"


ROOT = _bundle_root()

# 版本號的**唯一真相來源**。三個地方讀它:
#   1. app/__init__.py 的 FastAPI(version=...) → /docs 與 OpenAPI schema
#   2. GET /api/auth/me → 前端「設定 → 關於本專案」顯示(boot 的第一支請求,
#      比照 is_admin/public_share_enabled 搭便車,不為它再多打一支 API)
#   3. .github/workflows/release.yml → 驗證 git tag、這個常數、CHANGELOG.md
#      三者一致才准 build(image 一旦推上 ghcr 就收不回來,所以擋在 build 之前)
# 版次規則(完整說明寫在 CHANGELOG.md 的 Versioning 段):
#   0.x.y = Beta(公開測試),1.0.0 = 正式版。Beta 期間新功能或破壞性變更 bump
#   minor、只修 bug bump patch;`0.` 開頭的 tag 一律被 release.yml 標成 pre-release。
# ⚠ Dockerfile 的 `ARG APP_VERSION` **不是**第二個真相來源,那只是餵給 OCI LABEL
#   的中繼資料;對帳在 build 之前就做完了,不會有兩個版本號各自漂移的狀態。
# 放在 config.py 而不是別處的關鍵理由:這個模組只 import os/re/pathlib,
# CI 可以用一行 sed 抽出版本號對帳,不必先 pip install 整包相依。
APP_VERSION = "0.9.1"

DATA_DIR = Path(os.environ["GLOSSARY_DATA_DIR"]) if os.environ.get("GLOSSARY_DATA_DIR") else _default_data_dir()
LOG_DIR = DATA_DIR / "logs"
APP_LOG_PATH = LOG_DIR / "app.log"
# 整站備份的 ZIP 存放處(見 app/backup.py)。它**不是**真相來源,是真相的副本;
# 打包時必須排除自己,否則每一包會把前面所有包含進去。
BACKUP_DIR = DATA_DIR / "backups"

# 外掛封裝目錄(見 app/plugin_catalog.py)。兩個來源,合併成一份型錄、id 衝突官方贏:
#   OFFICIAL_PLUGINS_DIR:隨 repo 發佈的官方封裝(進版控,GitHub PR 即維護管道)。
#     刻意不叫 `plugins/`——repo root 在 sys.path 上,那個名字會與 Python 套件
#     `import plugins` 產生 namespace package 陰影。
#   SITE_PLUGINS_DIR:站台級第三方封裝(admin 上傳或直接放檔)。⚠ 這是**站台級真相**
#     ——上傳的封裝檔刪了無法重建,比照 site_settings.json 的地位,必須進整站備份
#     (app/backup.py 的還原白名單也要認得它)。
OFFICIAL_PLUGINS_DIR = ROOT / "official_plugins"
SITE_PLUGINS_DIR = DATA_DIR / "plugins"

# 公開筆記快照(見 app/publish.py):使用者把整庫或篩選子集發佈成**凍結複本**,
# 任何人免登入可讀。⚠ 這是**站台級真相目錄**(先例:SITE_PLUGINS_DIR)——
# 快照放在使用者目錄**之外**,帳號刪除後仍然存在(知識傳承是這個功能的核心敘事);
# 必須進整站備份(app/backup.py 的還原白名單也要認得它),但**不進**使用者匯出
# (方向同 shares.json:匯出帶走等於讓已撤下的公開頁在另一台復活)。
# 目錄本身就是登記簿(比照 data/plugins/),沒有第二份「發佈清單」檔可以對不上。
PUBLISHED_DIR = DATA_DIR / "published"

# 範例資料(見 app/demo.py):隨 repo 發佈的一小包名詞 + 標籤,註冊新帳號時複製進
# 使用者自己的庫,讓開箱不是一片空白。⚠ 它是**種子**不是真相:種進去之後那份副本
# 就是使用者的資料,改這裡的檔案不會回頭影響已經種過的帳號。
DEMO_DIR = ROOT / "demo"
# 註冊時是否種入範例資料。測試把它關掉(tests/conftest.py),否則每個 register_user
# fixture 都會多出一批名詞,所有「空庫」斷言全部失效。
SEED_DEMO_ON_REGISTER = os.environ.get("GLOSSARY_SEED_DEMO", "1").strip().lower() not in ("0", "false", "no", "off")
# 置頂行裡「官方 demo 網站」的連結。⚠ 佔位值,待正式網址確定後替換。
DEMO_SITE_URL = "https://jargon-vault.example.com/demo"

STATIC_DIR = ROOT / "static"

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # 合法 id 格式,防止路徑穿越(名詞 id、使用者 id 共用)

HISTORY_LIMIT = 3  # 每筆名詞保留的歷史版本數上限

# 回收桶保留天數:刪掉的名詞先進 <使用者目錄>/trash/,超過這個天數就永久刪除
# (啟動時與每次列出回收桶時清理,見 app/trash.py:purge_expired)
TRASH_RETENTION_DAYS = 30

# 公開分享連結的存活時數:建立(或重新產生)後超過這個時數自動失效。
# 過期判斷一律用 created + TTL **現算**(app/share_links.py:_alive),刻意不像
# invites.py 那樣在登記時物化 expires——這是站台常數不是使用者參數,現算讓
# 改這個數字對所有既有連結立即生效,不會留下一批照舊值到期的殭屍。
SHARE_LINK_TTL_HOURS = 48

UNNAMED_TAG = "未輸入"  # 未命名名詞自動掛上的標籤,補上真名後自動移除
UNTITLED_RE = re.compile(r"^Untitle\((\d+)\)$")  # 未命名名詞的佔位名稱格式

DEFAULT_TEMPLATE_ID = "jargon-default"  # 舊資料懶遷移與未指定樣板時的預設樣板
ARTICLE_PLUGIN_ID = "article-keywords"  # 文章>關鍵字外掛:批次生成入口(產出的名詞存成預設樣板;見 app/plugins.py)
FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")  # 樣板欄位 key 的合法格式
# 樣板欄位 key 不得使用的保留字:核心欄位與 frontmatter 結構欄,
# 保證 CSV 匯出把 fields 攤平成動態欄時不會撞到核心欄
RESERVED_FIELD_KEYS = {
    "id", "name", "description", "tags", "category", "attachments",
    "created", "updated", "history", "template", "fields", "marked",
    "srs_box", "srs_due",
}

# 重複偵測時被當成「同一個東西的另一個叫法」的樣板欄位 key(見 app/dedup.py)。
# 只列內建樣板裡語意上就是別名的那幾個——B 用別名記下 A 已經記過的詞,是這個
# 專案最常見的重複來源,只比對 name 抓不到。
ALIAS_FIELD_KEYS = ("alias", "synonymy", "en_term")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# 單檔上傳大小上限。圖片在前端就會先壓縮(static/js/imagecomp.js),這條只是
# 繞過前端時的防護,所以設得寬鬆——擋的是「整台硬碟被塞爆」不是「圖片太大」。
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# 內容健康度檢查(app/health.py)裡「這個檔案過大」的門檻。
# ⚠ 這**不是**上傳限制(那是 MAX_UPLOAD_BYTES,寬鬆得多),而是「值得回頭看一眼」
# 的提示門檻:圖片壓縮的產出是長邊 2000px 的 WebP,正常落在幾百 KB,所以超過 2MB
# 幾乎一定是繞過壓縮上傳的原圖或非圖片附件。設得比上傳上限低一個數量級是刻意的
# ——健康度檢查要在東西變成問題之前就講,而不是等它撞到硬上限。
LARGE_ASSET_BYTES = 2 * 1024 * 1024

# 健康度檢查每一類問題最多回傳幾筆明細。**count 一律是精確值、不受這條影響**
# (見 app/health.py 檔頭):截斷的是「明細」不是「結論」,所以「有沒有問題、
# 有幾個」永遠是對的——這跟 dedup.py 那條「不設上限」的規則沒有衝突。
HEALTH_MAX_PER_KIND = 50


def ensure_dirs() -> None:
    """建立全域資料目錄(首次啟動或指向新資料目錄時)。使用者專屬目錄由
    app/paths.py 的 ensure_user_dirs() 另外建立。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    SITE_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)


def valid_id(nid: str) -> bool:
    return bool(ID_RE.match(nid))

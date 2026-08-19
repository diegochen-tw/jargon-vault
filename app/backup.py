"""
整站備份與還原(真相來源 data/ → ZIP,存在 data/backups/)。

## 備份 ≠ 匯出,這是本模組存在的理由

`routers/transfer.py` 的匯出**刻意不帶** `shares.json`,CLAUDE.md
把理由寫得很清楚:分享 token 綁本站網址,搬到另一台毫無意義,更糟的是「匯入會讓
已撤銷的連結復活」。那條規則
**沒有被放寬,也不該被放寬**。

備份是完全不同的東西:

  | | 匯出(transfer) | 備份(本模組) |
  |---|---|---|
  | 目的 | 搬到**另一台**站台 | **同一台**的災難復原 |
  | 內容 | 使用者自己的名詞 | 整個 `data/`,含全站帳號與站台設定 |
  | 誰能用 | 每個使用者對自己的庫 | 只有 admin |
  | 還原語意 | 只補不覆蓋(合併) | 完整取代(回到那個時間點) |

「已撤銷的連結復活」在備份的語境下不是 bug 而是**正確行為**:還原到上週的狀態,
本來就該包含「上週那些連結是有效的」。使用者若不要,他要的是不還原,不是還原一半。

⚠ 所以:**絕不要把本模組的打包清單抄去 transfer.py**,反過來也不要因為
transfer.py 排除了什麼就跟著排除。兩邊的取捨方向是相反的。

## 打包什麼、不打包什麼

帶走全部三種真相來源 + 昂貴快取以外的一切:

  data/users.json            全域使用者登記簿(含 bcrypt 密碼雜湊)
  data/site_settings.json    全域站台設定(白名單/OAuth/速率限制/備份設定/AI 連線設定)
  data/.session_secret       session 簽章金鑰
  data/users/<uid>/          notes/(含 assets)、trash/、tags.json、templates.json、
                             progress.json、plugins.json、shares.json,以及已經沒人
                             讀的舊 ai_settings.json(AI 設定已上移到站台層,
                             舊檔照樣帶走——它是可以回頭的那條路)

排除的東西各有理由,不是漏掉:

  index.db     可拋棄索引,還原後 rebuild_index() 從 .md 全量重建(帶著它反而可能
               把一個壞掉的索引一起還原回來)。
  vectors.db   昂貴快取。**這是有代價的取捨**:還原後語意檢索要重新嵌入一次。
               但它動輒是整包 ZIP 裡最大的東西,而且 CLAUDE.md 已經把「向量可從
               .md 重算」定義成它的性質。要改成包含的話,記得它跟嵌入模型綁定。
  logs/        執行紀錄不是資料。
  backups/     ⚠ 必須排除,否則第 N 次備份會把前 N-1 包 ZIP 包進去,大小指數成長。

## 已知缺口:瀏覽器端的偏好帶不走

字級(`gv-fontscale`)、Logo/標語文字(`gv-logo-*`)、圖片壓縮開關
(`gv-imgcompress`)、只看書籤(`gv-markedonly`)、瀏覽模式等等,全部存在
**瀏覽器的 localStorage**,根本不在 `data/` 底下,所以整站備份結構上就帶不走。

這不是漏掉,是那些設定刻意做成**每台裝置各自獨立**的(字級本來就該因裝置而異)。
要讓它們進備份,得先把它們搬到伺服器端的每使用者設定檔,那是另一個決策,
不該由備份功能順手決定。管理 UI 的 `admin.bkHint` 有把這件事講明白——**不要**讓
使用者以為按了還原就會回到一模一樣的畫面。

例外:**介面語言已於 2026-08-17 搬到帳號上**(`users.json` 的 `lang` 欄位,
`PUT /api/auth/lang`)——同一台裝置換帳號時語言不該被上一個帳號汙染,所以它
從裝置偏好改成了帳號偏好,自然跟著 users.json 進備份;localStorage 的 `gv-lang`
降級成首繪快取。
"""
import json
import re
import shutil
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

from .config import BACKUP_DIR, DATA_DIR, PUBLISHED_DIR, SITE_PLUGINS_DIR
from .paths import USERS_DIR

# 檔名格式:backup-<kind>-<YYYYmmdd-HHMMSS>.zip
# kind 有三種,auto 這個字是「上次自動備份是什麼時候」的唯一依據(見 last_auto_run)。
KIND_AUTO, KIND_MANUAL, KIND_PRERESTORE = "auto", "manual", "prerestore"
BACKUP_NAME_RE = re.compile(r"^backup-(auto|manual|prerestore)-\d{8}-\d{6}\.zip$")

# 打包時要跳過的頂層項目(相對 DATA_DIR)
_SKIP_TOP = {"backups", "logs"}
# 打包時要跳過的檔名(任何層級)
_SKIP_NAMES = {"index.db", "vectors.db"}
# 每個庫底下要跳過的目錄(users/<id>/<這個>/…)。
# `cleanup` 是內容健康度檢查的存證 ZIP(app/health.py):它跟 data/backups 同一類
# ——**被刪掉那些東西的副本**,不是真相。不排除的話,使用者清掉 500MB 孤兒檔
# 騰出來的空間,會在下一次整站備份時原封不動地被吃回去,而且往後每一包都帶著。
# ⚠ 比對的是**第三層**而不是「任何一層叫 cleanup 的目錄」:資產目錄是
# assets/<note_id>/,而 "cleanup" 剛好是合法的 note id,寫寬了會誤殺那筆名詞的附件。
_SKIP_VAULT_DIRS = {"cleanup"}

MANIFEST_NAME = "backup-manifest.json"

# 允許還原的頂層檔案。⚠ 這份清單必須跟 _iter_data_files() 實際打包進去的頂層
# 檔案一致——只打包不還原的檔案會變成「備份看起來有、還原卻少一樣」的靜默缺口。
# `.legacy_migrated` 是舊資料遷移的一次性標記,漏了它,還原後的站台會以為
# 遷移還沒做過。
_RESTORABLE_TOP_FILES = {"users.json", "site_settings.json", "invites.json",
                         ".session_secret", ".legacy_migrated"}

# 自動備份在背景執行緒跑,這把鎖保證同一時間只有一包在打(手動與自動共用)。
# ⚠ 必須是 RLock 不是 Lock:run_auto_backup_if_due() 先拿鎖、再呼叫 create_backup(),
# 而 create_backup() 自己也要拿(手動備份不經過前者,但一樣需要保護命名那段)。
# 用一般 Lock 的話同一條執行緒第二次 acquire 會直接死鎖,而且是那種「自動備份
# 從此再也不會完成、卻什麼錯誤都不噴」的無聲卡死。
_backup_lock = threading.RLock()


# ── 打包 ────────────────────────────────────────────────────────────────────

def _should_skip(rel: Path) -> bool:
    parts = rel.parts
    if not parts:
        return False
    if parts[0] in _SKIP_TOP or rel.name in _SKIP_NAMES:
        return True
    return (len(parts) > 2 and parts[0] == "users"
            and parts[2] in _SKIP_VAULT_DIRS)


def _iter_data_files():
    """走訪 data/ 底下所有要進備份的檔案,yield (絕對路徑, ZIP 內相對路徑)。"""
    if not DATA_DIR.exists():
        return
    for path in sorted(DATA_DIR.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(DATA_DIR)
        if _should_skip(rel):
            continue
        yield path, rel.as_posix()


def _manifest(file_count: int, kind: str) -> dict:
    users = sorted(p.name for p in USERS_DIR.iterdir() if p.is_dir()) if USERS_DIR.exists() else []
    return {
        "version": 1,
        "kind": kind,
        "created": time.time(),
        "created_display": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user_ids": users,
        "file_count": file_count,
        # 明講排除了什麼,讓拿到 ZIP 的人不必猜「怎麼沒有 index.db」
        "excluded": sorted(_SKIP_TOP | _SKIP_NAMES | _SKIP_VAULT_DIRS),
    }


def create_backup(kind: str = KIND_MANUAL) -> Path:
    """打一包 ZIP 回傳路徑。同一時間只會有一包在打(鎖)。

    ⚠ 這是**熱備份**:打包期間別人可能正在寫 .md。單檔是 write_text 一次寫完,
    所以壞的最多是「某一筆名詞是打包瞬間的舊版」,不會出現半個檔案;沒有跨檔
    交易需要保護(SQLite 那兩個檔本來就排除了)。要完全一致就得停機,而為了
    個人規模的筆記停機不划算。
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    # 整段包在鎖裡:自動備份(背景執行緒)與手動備份(請求執行緒)可能同時進來,
    # 而「挑一個沒被用過的檔名」到「真的建立那個檔」之間有空隙——兩條執行緒在
    # 同一秒各自看到 dest 不存在,就會挑到同一個名字,後寫的把先寫的蓋掉。
    with _backup_lock:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = BACKUP_DIR / f"backup-{kind}-{stamp}.zip"
        n = 1
        while dest.exists():
            dest = BACKUP_DIR / f"backup-{kind}-{stamp}_{n}.zip"
            n += 1

        tmp = dest.with_suffix(".zip.part")
        count = 0
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                for abs_path, arc in _iter_data_files():
                    try:
                        z.write(abs_path, arc)
                    except OSError:
                        # 單一檔案讀不到(被鎖住/剛好被刪)不該讓整包備份失敗
                        continue
                    count += 1
                z.writestr(MANIFEST_NAME,
                           json.dumps(_manifest(count, kind), ensure_ascii=False, indent=2))
            # 先寫 .part 再改名:中途當掉不會留下一包「看起來完整、其實是半包」的
            # ZIP 讓人日後拿去還原。改名在同一個檔案系統上是原子操作。
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
    return dest


# ── 清單與保留 ──────────────────────────────────────────────────────────────

def _read_manifest(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as z:
            return json.loads(z.read(MANIFEST_NAME).decode("utf-8"))
    except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
        return {}


def list_backups() -> list[dict]:
    """由新到舊。壞掉/不合命名規則的檔案直接不列(它們不是我們產的備份)。"""
    if not BACKUP_DIR.exists():
        return []
    out = []
    for p in BACKUP_DIR.glob("backup-*.zip"):
        try:
            st = p.stat()
        except OSError:
            continue
        m = _read_manifest(p)
        out.append({
            "name": p.name,
            "kind": m.get("kind") or _kind_of(p.name),
            "size": st.st_size,
            "created": m.get("created") or st.st_mtime,
            "user_ids": m.get("user_ids", []),
            "file_count": m.get("file_count", 0),
            "valid": bool(m),  # 讀不到 manifest = 不是完整的備份包,UI 要標出來
        })
    out.sort(key=lambda x: x["created"], reverse=True)
    return out


def _kind_of(name: str) -> str:
    parts = name.split("-")
    return parts[1] if len(parts) > 2 else KIND_MANUAL


def valid_backup_name(name: str) -> bool:
    """檔名一律先過這關再拿去組路徑。`name` 來自 HTTP,是不可信輸入——
    沒有這層,`../../users.json` 就能讓下載端點把任何檔案吐出來、
    讓刪除端點刪掉任何檔案。正規表示式已經排除了 `/`、`\\` 與 `..`。"""
    return bool(BACKUP_NAME_RE.match(name)) or bool(
        re.match(r"^backup-(auto|manual|prerestore)-\d{8}-\d{6}_\d+\.zip$", name)
    )


def backup_path(name: str) -> Path | None:
    if not valid_backup_name(name):
        return None
    p = BACKUP_DIR / name
    return p if p.is_file() else None


def delete_backup(name: str) -> bool:
    p = backup_path(name)
    if not p:
        return False
    p.unlink(missing_ok=True)
    return True


def purge_old(keep: int) -> int:
    """只清自動備份。手動與還原前快照是使用者/系統刻意留下的,不該被自動清掉——
    尤其 prerestore 那包,它存在的唯一理由就是「還原按錯了要能救回來」。"""
    autos = [b for b in list_backups() if b["kind"] == KIND_AUTO]
    removed = 0
    for b in autos[max(0, keep):]:
        if delete_backup(b["name"]):
            removed += 1
    return removed


# ── 自動備份 ────────────────────────────────────────────────────────────────

def last_auto_run() -> float:
    """上次自動備份的時間。**從最新一包 backup-auto-* 推算,不另外存狀態檔**——
    多一個「上次執行時間」的狀態就多一種「狀態與實際檔案對不上」的壞法(例如
    使用者手動刪光備份,狀態卻說剛備份過,於是再也不備份)。從檔案推算則是
    自我修復的:備份不見了,下一次檢查就會判定該跑了。"""
    autos = [b for b in list_backups() if b["kind"] == KIND_AUTO]
    return autos[0]["created"] if autos else 0.0


def _parse_at_time(raw) -> tuple[int, int] | None:
    """"HH:MM" → (時, 分);空值或格式壞掉一律回 None(= 沒設門檻)。

    ⚠ 壞掉時**絕不可以**退化成「永遠不備份」——那是無聲的失敗:UI 顯示自動備份
    已啟用,實際上一份都不會產生,而使用者要到需要還原的那天才發現。
    """
    s = str(raw or "").strip()
    try:
        hh, mm = (int(x) for x in s.split(":", 1))
    except ValueError:
        return None
    return (hh, mm) if 0 <= hh <= 23 and 0 <= mm <= 59 else None


def auto_backup_due(cfg: dict) -> bool:
    """該不該備份了。

    **沒設 `at_time`(預設)**:純粹的「距上次超過 N 天」,與這個鍵出現之前完全相同。

    **設了 `at_time`(伺服器本地時間 "HH:MM")**:語意是**「不早於」而不是「準時在」**。
    這個 app 刻意沒有排程器(沒有常駐執行緒、沒有佇列,見 maybe_run_auto_backup 的
    docstring),整個系統只有兩個評估點:程序啟動,以及 `GET /api/auth/me`。所以設
    03:00 的實際行為是「隔了 N 天之後,當天過了 03:00 的第一次有人使用網站時才跑」
    ——那天沒人開就順延到下一次有人開。管理 UI 的提示文字必須把這件事講出來,
    不然使用者只會當成 bug。

    ⚠ 設了時刻時,間隔改用**日曆天**比較而不是 24 小時的秒數差。用秒數差的話,
    上一包剛好在 23:50 跑完,隔天 03:00 只過了 3 小時 10 分、遠不到一天,要等到
    隔天 23:50 才到期——而那時 03:00 這道門檻早就過了。結果就是**設定的時刻永遠
    不會生效**,備份時間停在最初那個偶然的時刻上,而且完全不會報錯。使用者去設一個
    時刻,要的就是「每天那個時候」,不是「每 24 小時一次,順便不早於某個鐘點」。

    ⚠ 用 `cfg.get("at_time")` 而不是 `cfg["at_time"]`:呼叫端(含測試)可能拿的是
    只有三個鍵的舊 dict,用 `[]` 會直接 KeyError 把整個備份機制打死。
    """
    if not cfg.get("auto_enabled"):
        return False
    last = last_auto_run()
    at = _parse_at_time(cfg.get("at_time"))
    if at is None:
        return (time.time() - last) >= cfg["interval_days"] * 86400

    now = datetime.now()
    if (now.hour, now.minute) < at:
        return False
    # last=0(沒有任何自動備份)時 fromtimestamp(0) 是 1970,天數差極大 → 直接到期
    days = (now.date() - datetime.fromtimestamp(last).date()).days
    return days >= cfg["interval_days"]


def run_auto_backup_if_due(cfg: dict) -> bool:
    """該備份就備份 + 清掉超出保留份數的舊自動備份。回傳有沒有真的跑。

    這是同步版本(啟動時用)。從請求路徑呼叫請用 maybe_run_auto_backup()。
    """
    if not auto_backup_due(cfg):
        return False
    if not _backup_lock.acquire(blocking=False):
        return False  # 已經有一包在打,這次跳過
    try:
        if not auto_backup_due(cfg):  # 拿到鎖後再確認一次(前一包剛好打完)
            return False
        create_backup(KIND_AUTO)
        purge_old(cfg["keep"])
        return True
    finally:
        _backup_lock.release()


def maybe_run_auto_backup(cfg: dict) -> None:
    """從請求路徑呼叫的版本:**丟到背景執行緒,絕不擋住請求**。

    整站打包可能要好幾秒;掛在 `GET /api/auth/me`(boot 的第一支請求)上同步跑,
    使用者會看到一次莫名其妙的長白畫面,而且每 7 天才發生一次——那種偶發的卡頓
    最難被回報也最難重現。

    這不是「引入背景工作者」:沒有排程器、沒有佇列、沒有常駐執行緒,就是一次性的
    deferred task,而且 `auto_backup_due()` 是先在呼叫端同步判斷過的(絕大多數
    請求連執行緒都不會開)。
    """
    if not auto_backup_due(cfg):
        return
    threading.Thread(target=run_auto_backup_if_due, args=(cfg,), daemon=True).start()


# ── 還原 ────────────────────────────────────────────────────────────────────

def _safe_members(z: zipfile.ZipFile) -> list[str]:
    """回傳可以安全解開的成員清單,擋掉 zip slip。

    ZIP 內的路徑是完全不可信的外部輸入(這包 ZIP 可能是使用者上傳的)。
    絕對路徑、`..`、Windows 磁碟機代號、反斜線,任何一種都能讓解壓寫到
    `data/` 之外——在整站還原這個情境下那等於任意檔案覆寫。逐段自己驗,
    不依賴 zipfile 的任何隱含行為。
    """
    safe = []
    for name in z.namelist():
        if name.endswith("/") or name == MANIFEST_NAME:
            continue
        if name.startswith("/") or "\\" in name or ":" in name:
            continue
        # ⚠ 一定要用原始的 "/" 切,**不能用 Path(name).parts**:pathlib 會把 "." 這一
        # 段直接吃掉(Path("./users/x").parts == ("users","x")),於是「檢查有沒有 .
        # 這一段」永遠不會命中,而且不同平台對分隔符的解讀還不一樣。ZIP 規格明定
        # 分隔符就是 "/",照規格自己切才看得到路徑真正長什麼樣。
        parts = name.split("/")
        if not parts or any(p in ("", ".", "..") for p in parts):
            continue
        # 只收我們自己會打包的東西:全域檔、users/ 底下、站台外掛封裝 plugins/ 底下。
        # 白名單而不是黑名單——黑名單漏一種寫法就是任意檔案覆寫,白名單漏一種只是
        # 少還原一個檔。
        if parts[0] in ("users", "plugins", "published") and len(parts) >= 2:
            safe.append(name)
        elif len(parts) == 1 and parts[0] in _RESTORABLE_TOP_FILES:
            safe.append(name)
    return safe


def inspect_backup(path: Path) -> dict:
    """還原前的檢視:這包裡有什麼。壞檔在這裡就擋下來,不要進到已經刪了東西的階段。"""
    with zipfile.ZipFile(path) as z:
        bad = z.testzip()
        if bad is not None:
            raise ValueError(f"備份檔已損毀:{bad}")
        members = _safe_members(z)
        m = _read_manifest(path)
    if not members:
        raise ValueError("這個 ZIP 裡沒有任何可還原的內容,可能不是本系統的備份檔")
    uids = sorted({Path(n).parts[1] for n in members if Path(n).parts[0] == "users" and len(Path(n).parts) > 1})
    return {
        "manifest": m,
        "member_count": len(members),
        "user_ids": uids,
        "has_users_json": "users.json" in members,
        "has_site_settings": "site_settings.json" in members,
    }


def restore_backup(path: Path) -> dict:
    """整包還原:先存還原前快照 → 清掉目標範圍 → 解開 → 呼叫端重建索引。

    順序不能換。先解開再刪會兩份混在一起(舊使用者的殘留檔案留在新資料裡);
    先刪再驗證則是刪完才發現 ZIP 是壞的,而那時候原始資料已經沒了——所以
    `inspect_backup()` 一定在動任何東西之前跑完。
    """
    info = inspect_backup(path)  # 壞檔在這裡就丟例外,還沒動到任何資料

    snapshot = create_backup(KIND_PRERESTORE)

    with zipfile.ZipFile(path) as z:
        members = _safe_members(z)

        # 清掉目標範圍:整個 users/、站台外掛封裝 plugins/、與全域檔。
        # **不是 rmtree(DATA_DIR)**——那會連 backups/ 一起刪掉,包含剛剛才寫好的
        # 還原前快照,以及使用者手上唯一的其他備份。刪除範圍嚴格對齊「備份會打包
        # 的東西」。plugins/ 比照 users/ 整個換掉:還原=回到那個時間點,備份裡
        # 沒有的封裝本來就該消失。
        if USERS_DIR.exists():
            shutil.rmtree(USERS_DIR, ignore_errors=True)
        if SITE_PLUGINS_DIR.exists():
            shutil.rmtree(SITE_PLUGINS_DIR, ignore_errors=True)
        # published/ 比照 users/ 整個換掉:還原=回到那個時間點,備份裡沒有的
        # 快照本來就該消失(否則已撤銷的公開頁在還原後復活)。
        if PUBLISHED_DIR.exists():
            shutil.rmtree(PUBLISHED_DIR, ignore_errors=True)
        # 全域檔只刪「這包備份等一下會寫回來的那幾個」。備份裡沒有的就別動——
        # 例如舊備份沒有 .session_secret,刪掉它只會讓所有人的 session 失效,
        # 而且沒有任何東西補回來,是純粹的損失。users/ 則相反:整個換掉才符合
        # 「回到那個時間點」,備份裡沒有的使用者本來就該消失。
        for fname in {n for n in members if "/" not in n}:
            (DATA_DIR / fname).unlink(missing_ok=True)

        for name in members:
            dest = DATA_DIR / name
            # 再保險一次:解析後的絕對路徑一定要還在 DATA_DIR 底下
            if not _within(dest, DATA_DIR):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)

    return {
        "restored_files": info["member_count"],
        "user_ids": info["user_ids"],
        "snapshot": snapshot.name,
    }


def _within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def save_uploaded(content: bytes) -> Path:
    """把上傳的 ZIP 落地成一包備份(之後才談還原)。

    先寫檔再驗證是刻意的:驗證要開 ZipFile,而落地之後不管還原成不成功,
    使用者上傳的那包都留在備份清單裡看得到——上傳完卻因為某個欄位不合就整包
    消失,是最讓人不知所措的失敗。
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"backup-{KIND_MANUAL}-{stamp}.zip"
    n = 1
    while dest.exists():
        dest = BACKUP_DIR / f"backup-{KIND_MANUAL}-{stamp}_{n}.zip"
        n += 1
    dest.write_bytes(content)
    try:
        with zipfile.ZipFile(dest) as z:
            z.namelist()
    except zipfile.BadZipFile:
        dest.unlink(missing_ok=True)
        raise ValueError("上傳的檔案不是合法的 ZIP")
    return dest

"""
全域站台設定讀寫(真相來源,data/site_settings.json)。

跟 data/users.json 一樣是全域(跨使用者共用)的真相檔:放「誰能註冊、白名單、
Google OAuth 連線、公開分享連結的總開關、登入失敗鎖定的門檻、自動備份的
週期」這類站台層設定,讓 admin 能在 UI 管理,而不是只能改環境變數重啟。

`rate_limit`、`backup` 與 `ai` 三個區塊同樣是「塞進既有的全域檔,而不是新開第三個
全域真相檔」——CLAUDE.md 對新增全域真相檔有明文限制,而這三者本來就是站台層
政策(admin 的權力),語意上跟註冊模式、公開分享開關完全同一類。

`ai` 是 AI 連線層設定的真相來源(位址/金鑰/生成模型/嵌入模型),**全站唯一一組**。
它以前是每使用者一份(`<使用者目錄>/ai_settings.json`)、外加團隊庫各一份,現在
統一由站台管理者管理:本機模型服務本來就只有一台,連線參數是基礎設施不是個人
偏好;而且金鑰散在每個人的檔案裡本身就是問題。順帶消掉了「不同庫用不同嵌入模型
會互相把向量整包作廢」那個無聲的失敗模式——現在每個庫嵌入用的都是同一個模型。
讀寫入口仍然是 `app/ai_settings.py`(那一層才是對外的 API,別直接讀這裡的 dict)。

env 的定位:
  - 第一次啟動(檔案不存在)時,把 ALLOWED_EMAILS / GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
    「種」進這份檔,之後以檔案為真、admin 從 UI 改。
  - ALLOWED_EMAILS 另外仍作為**即時補充/救援白名單**:whitelist 模式的允許判斷會把
    env 名單與 store 名單聯集(語意等同 auth.py 的 ADMIN_EMAILS 即時救援)。既有 env-based
    部署升級後行為不變,測試用 monkeypatch.setenv 設白名單也照樣有效。
  - google_oauth 的 client_id/secret 若 store 留空,讀取時 fallback 回 env。

本模組只碰檔案系統,不碰 HTTP、不碰 SQLite。
"""
import json
import os
import re

from . import atomic
from .config import DATA_DIR

SITE_SETTINGS_PATH = DATA_DIR / "site_settings.json"

REGISTRATION_MODES = ("open", "whitelist", "closed")
DEFAULT_REGISTRATION_MODE = "whitelist"


def _env_allowed_emails() -> set[str]:
    raw = os.environ.get("ALLOWED_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _default_settings() -> dict:
    """首次建檔時的內容:白名單/OAuth 從 env 種子,註冊模式預設 whitelist(維持既有行為)。

    公開分享開關**刻意沒有對應的 env var**:它不像白名單有「第一次啟動就得有值
    否則沒人能註冊」的需求,一律預設關閉、由 admin 在 UI 開啟。預設關是因為它會
    把內容送出登入牆之外,預設值必須是保守的那一邊。
    """
    cid = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    return {
        "registration_mode": DEFAULT_REGISTRATION_MODE,
        "allowed_emails": sorted(_env_allowed_emails()),
        "google_oauth": {
            "enabled": bool(cid and secret),
            "client_id": cid,
            "client_secret": secret,
        },
        "public_share_enabled": False,
        # 公開筆記快照(app/publish.py)的總開關。與單筆分享分開:兩者面向不同
        # (一筆 vs 一整份),關其中一個不該連坐另一個。預設關——會把內容送出
        # 登入牆的開關,出廠值必須保守;也刻意不給環境變數,由 admin 在 UI 開。
        "public_notebook_enabled": False,
        "rate_limit": _default_rate_limit(),
        "backup": _default_backup(),
        "ai": _default_ai(),
    }


def _default_ai() -> dict:
    """AI 連線層設定的出廠值(見 app/ai_settings.py)。

    ⚠ 既有站台升級時**不可以**直接落到這組預設值——admin 原本設好的位址與模型
    會被無聲換掉。`app/migration.py:migrate_ai_settings_to_site()` 負責在 "ai"
    這個鍵還不存在時,從第一位管理者的舊 ai_settings.json 種進來。
    """
    return {
        # 預設不啟用:AI 需要一台本機模型服務,新站台裝完不會有——預設開等於
        # 每個新使用者都看得到一排按不動的 AI 按鈕(按了只換來連線錯誤)。
        # 由 admin 在 設定 → AI 功能 → 連線設定 自行開啟。
        "enabled": False,
        # "ollama" = Ollama 原生 API;"openai" = OpenAI 相容(LM Studio / llama.cpp / vLLM)
        "api_style": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "api_key": "",           # 本機服務通常留空
        "model": "qwen2.5:14b",
        "embed_model": "",       # 語意檢索用;空 = 尚未設定,語意搜尋會回 needs_index
        # 生成說明欄的字數上限:寫進 prompt 的**指示**,不是硬截斷(截斷會切在
        # 句子中間)。與樣板自帶的長度要求(如「一段話解讀」的三段各 150~250 字)
        # 可能打架——UI 的提示文字有講明可調高或關閉。
        "desc_limit_enabled": True,
        "desc_max_chars": 250,
    }


def _default_rate_limit() -> dict:
    """登入失敗鎖定的預設值(見 app/ratelimit.py)。

    **預設就是開的**——這是安全機制,預設關等於沒做。門檻設得比一般人會誤觸的
    次數寬:per-email 5 次是「打錯密碼」的合理上限,per-IP 20 次讓一家人共用同
    一個對外 IP 也不會互相波及,而暴力破解需要的是幾千幾萬次,20 跟 20 萬對攻擊
    者的差別是「這條路不通」。

    `trust_forwarded_for` 從 PUBLIC_BASE_URL 種子:那個 env 存在就代表站台掛在
    反向代理/Cloudflare Tunnel 後面(CLAUDE.md 就是這樣說明它的),而那正是
    「不看 X-Forwarded-For 就會把全世界收斂成同一個 IP」的情境。種錯了不會靜默
    出事,admin 在管理 UI 看得到也改得掉。
    """
    return {
        "enabled": True,
        "ip_max_attempts": 20,
        "email_max_attempts": 5,
        "window_minutes": 15,
        "lockout_minutes": 15,
        "trust_forwarded_for": bool(os.environ.get("PUBLIC_BASE_URL", "").strip()),
    }


def _default_backup() -> dict:
    """自動備份預設**開啟**、每 7 天一次,保留最近 10 份(見 app/backup.py)。

    預設開的理由跟 rate_limit 一樣:備份是那種「需要的時候才發現沒開」的東西,
    預設關掉等於大多數人永遠不會有備份。每 7 天 + 保留 10 份 = 大約兩個月的
    復原視窗,對個人/家庭規模的資料量來說磁碟成本可以忽略。

    `at_time` 預設**空字串**(不指定時刻,到期就備份)——那是這個鍵出現之前的
    行為,既有站台升級後一切照舊。填了值的語意見 backup.auto_backup_due()。
    """
    return {"auto_enabled": True, "interval_days": 7, "keep": 10, "at_time": ""}


def _normalize(data: dict) -> dict:
    """讀入時補齊缺鍵,讓舊檔/手改檔向後相容。"""
    data.setdefault("registration_mode", DEFAULT_REGISTRATION_MODE)
    if data["registration_mode"] not in REGISTRATION_MODES:
        data["registration_mode"] = DEFAULT_REGISTRATION_MODE
    emails = data.get("allowed_emails")
    data["allowed_emails"] = sorted({str(e).strip().lower() for e in (emails or []) if str(e).strip()})
    g = data.get("google_oauth")
    if not isinstance(g, dict):
        g = {}
    g.setdefault("enabled", False)
    g.setdefault("client_id", "")
    g.setdefault("client_secret", "")
    data["google_oauth"] = g
    data["public_share_enabled"] = data.get("public_share_enabled") is True
    data["public_notebook_enabled"] = data.get("public_notebook_enabled") is True
    data["rate_limit"] = _clean_rate_limit(data.get("rate_limit"))
    data["backup"] = _clean_backup(data.get("backup"))
    data["ai"] = _clean_ai(data.get("ai"))
    return data


def _int_in(raw, default: int, lo: int, hi: int) -> int:
    """把外部輸入夾進合法區間。上下界不是防呆而是防自傷:
    門檻填 0 會讓沒失敗過的人也算「已達上限」而全站鎖死,鎖定時間填一年等於
    永久封鎖且沒有解鎖 UI。夾住之後任何輸入都還原得回可用狀態。"""
    try:
        return max(lo, min(hi, int(raw)))
    except (TypeError, ValueError):
        return default


def _clean_rate_limit(raw) -> dict:
    d = _default_rate_limit()
    if not isinstance(raw, dict):
        return d
    return {
        "enabled": raw.get("enabled", d["enabled"]) is True,
        "ip_max_attempts": _int_in(raw.get("ip_max_attempts"), d["ip_max_attempts"], 1, 10_000),
        "email_max_attempts": _int_in(raw.get("email_max_attempts"), d["email_max_attempts"], 1, 10_000),
        "window_minutes": _int_in(raw.get("window_minutes"), d["window_minutes"], 1, 1440),
        "lockout_minutes": _int_in(raw.get("lockout_minutes"), d["lockout_minutes"], 1, 1440),
        # 這個沒有 env fallback:種子只在首次建檔時看 PUBLIC_BASE_URL,之後以檔案為真。
        "trust_forwarded_for": raw.get("trust_forwarded_for") is True,
    }


def _clean_backup(raw) -> dict:
    d = _default_backup()
    if not isinstance(raw, dict):
        return d
    return {
        "auto_enabled": raw.get("auto_enabled", d["auto_enabled"]) is True,
        "interval_days": _int_in(raw.get("interval_days"), d["interval_days"], 1, 365),
        # 保留份數至少 1:設 0 等於「備份完立刻刪掉」,那是最惡劣的失敗模式
        # ——UI 顯示成功、實際上什麼都沒留下。
        "keep": _int_in(raw.get("keep"), d["keep"], 1, 500),
        # 不合法的時刻一律當成「沒設」而不是報錯:這個鍵的空值本來就是合法狀態
        # (= 到期就備份),把爛輸入收斂到那裡,永遠不會有「備份因為設定壞掉而停擺」。
        "at_time": _clean_at_time(raw.get("at_time")),
    }


_AT_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _clean_at_time(raw) -> str:
    s = str(raw or "").strip()
    return s if _AT_TIME_RE.match(s) else ""


def _clean_ai(raw) -> dict:
    d = _default_ai()
    if not isinstance(raw, dict):
        return d
    style = str(raw.get("api_style", d["api_style"])).strip().lower()
    return {
        "enabled": raw.get("enabled", d["enabled"]) is True,
        "api_style": style if style in ("ollama", "openai") else d["api_style"],
        # 位址要填到 /v1 為止的那件事**刻意不自動補**(vLLM 與部分 gateway 掛在
        # 根路徑,猜錯只會換來更難懂的 404),這裡只去掉結尾斜線。
        "base_url": str(raw.get("base_url", d["base_url"])).strip().rstrip("/") or d["base_url"],
        "api_key": str(raw.get("api_key", "")).strip(),
        "model": str(raw.get("model", d["model"])).strip() or d["model"],
        # 允許清空:空字串是「沒有嵌入模型」這個有意義的狀態,不能退回預設
        "embed_model": str(raw.get("embed_model", "")).strip(),
        "desc_limit_enabled": raw.get("desc_limit_enabled", d["desc_limit_enabled"]) is True,
        "desc_max_chars": _int_in(raw.get("desc_max_chars"), d["desc_max_chars"], 50, 2000),
    }


def load_site_settings() -> dict:
    try:
        data = json.loads(SITE_SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return _normalize(data)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return _default_settings()


def save_site_settings(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    atomic.write_json(SITE_SETTINGS_PATH, _normalize(data))


def ensure_site_settings() -> None:
    """建檔用:不存在就用 env 種子寫入一份。"""
    if not SITE_SETTINGS_PATH.exists():
        save_site_settings(_default_settings())


# ── 讀取 helper(給 auth / router 用)──────────────────────────────────────

def registration_mode() -> str:
    return load_site_settings()["registration_mode"]


def allowed_emails() -> set[str]:
    """store 名單 ∪ 環境變數 ALLOWED_EMAILS(env 為即時補充/救援)。"""
    return set(load_site_settings()["allowed_emails"]) | _env_allowed_emails()


def is_email_allowed(email: str) -> bool:
    mode = registration_mode()
    if mode == "open":
        return True
    if mode == "closed":
        return False
    return email.strip().lower() in allowed_emails()


def google_oauth_config() -> dict:
    """回傳 {enabled, client_id, client_secret};store 為空時 fallback 回 env。"""
    g = dict(load_site_settings()["google_oauth"])
    if not g.get("client_id"):
        g["client_id"] = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    if not g.get("client_secret"):
        g["client_secret"] = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    return g


def google_enabled() -> bool:
    g = google_oauth_config()
    return bool(g.get("enabled") and g.get("client_id") and g.get("client_secret"))


def public_share_enabled() -> bool:
    return load_site_settings()["public_share_enabled"] is True


def public_notebook_enabled() -> bool:
    return load_site_settings()["public_notebook_enabled"] is True


def rate_limit_config() -> dict:
    return load_site_settings()["rate_limit"]


def backup_config() -> dict:
    return load_site_settings()["backup"]


def ai_config() -> dict:
    """AI 連線設定。**不要直接呼叫這支**——對外入口是 `ai_settings.load_ai_settings()`,
    那一層才是模組地圖上「AI 設定的讀寫入口」。"""
    return load_site_settings()["ai"]

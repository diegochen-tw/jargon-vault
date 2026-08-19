"""
使用者登記簿讀寫(真相來源,data/users.json)。

本專案唯一真正全域(跨使用者共用)的真相檔——其餘所有資料都在
app/paths.py 的 VaultPaths 底下,各使用者互不相通。

本模組只碰檔案系統,不碰 HTTP、不碰密碼雜湊/session(那些在 app/auth.py)。
"""
import json
import time
import uuid

from . import atomic
from .config import DATA_DIR

USERS_PATH = DATA_DIR / "users.json"

# 介面語言的合法語碼——與前端 static/js/i18n.js 的 12 種語言字典是同一份清單,
# 新增語言時兩邊要一起加。
SUPPORTED_LANGS = {
    "zh-Hant", "zh-Hans", "en", "ja", "fr", "de",
    "it", "pt", "es", "ko", "id", "hi",
}


def load_users() -> list[dict]:
    try:
        data = json.loads(USERS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []


def save_users(users: list[dict]) -> None:
    atomic.write_json(USERS_PATH, users)


def ensure_users() -> None:
    """建檔用,不存在就寫入空陣列。"""
    if not USERS_PATH.exists():
        save_users([])


def find_by_email(email: str) -> dict | None:
    email = email.strip().lower()
    for u in load_users():
        if u["email"] == email:
            return u
    return None


def find_by_id(user_id: str) -> dict | None:
    for u in load_users():
        if u["id"] == user_id:
            return u
    return None


def find_by_google_sub(sub: str) -> dict | None:
    for u in load_users():
        if u.get("google_sub") == sub:
            return u
    return None


def create_user(*, email: str, password_hash: str | None = None,
                 google_sub: str | None = None, id: str | None = None,
                 is_admin: bool = False) -> dict:
    """新增一筆使用者記錄並落地。id 可由呼叫端預先指定(遷移舊資料時需要
    先知道新使用者的 id 才能把舊檔案搬過去);不指定就自動產生。
    白名單檢查由呼叫端(routers/auth.py)負責,這裡不重複檢查。"""
    users = load_users()
    record = {
        "id": id or uuid.uuid4().hex[:12],
        "email": email.strip().lower(),
        "password_hash": password_hash,
        "google_sub": google_sub,
        "is_admin": bool(is_admin),
        "created": time.time(),
    }
    users.append(record)
    save_users(users)
    return record


def link_google_sub(user_id: str, google_sub: str) -> None:
    """既有密碼帳號第一次用 Google 登入同一個 email 時,補上 google_sub。"""
    users = load_users()
    for u in users:
        if u["id"] == user_id:
            u["google_sub"] = google_sub
            break
    save_users(users)


def set_password_hash(user_id: str, password_hash: str | None) -> None:
    """設定或清除(None)某使用者的密碼雜湊。「不能清掉最後一種登入方式」的
    防呆由呼叫端(routers/auth.py)負責——這裡只做落地,跟 create_user 不重複
    檢查白名單是同一個分工。"""
    users = load_users()
    for u in users:
        if u["id"] == user_id:
            u["password_hash"] = password_hash
            break
    save_users(users)


def unlink_google_sub(user_id: str) -> None:
    """解除 Google 連結(google_sub 清成 None)。防呆同樣在呼叫端。"""
    users = load_users()
    for u in users:
        if u["id"] == user_id:
            u["google_sub"] = None
            break
    save_users(users)


def set_lang(user_id: str, lang: str | None) -> None:
    """設定(或清除,None)某使用者的介面語言。合法語碼的驗證在呼叫端
    (routers/auth.py),比照 create_user 不重複檢查白名單的分工。
    「從未設定」與「清除」都是沒有 lang 這個 key——跟隨裝置語言。"""
    users = load_users()
    for u in users:
        if u["id"] == user_id:
            if lang is None:
                u.pop("lang", None)
            else:
                u["lang"] = lang
            break
    save_users(users)


def set_demo_seeded(user_id: str, seeded: bool) -> None:
    """標記/清除「這個帳號註冊時種過範例資料」(見 app/demo.py)。

    這一個旗標同時管兩件事:範例資料還在 ⇔ 置頂行要顯示。使用者按下置頂行的
    刪除鈕時,刪範例與清旗標是同一個動作,所以不會出現「資料刪了橫幅還在」或
    「橫幅關了資料還在」的分歧狀態。

    比照 set_lang:False 是把 key **拿掉**而不是寫 False,讓「從沒種過」與
    「種過但已清掉」都是缺鍵,users.json 不留一堆用不到的 false。
    """
    users = load_users()
    for u in users:
        if u["id"] == user_id:
            if seeded:
                u["demo_seeded"] = True
            else:
                u.pop("demo_seeded", None)
            break
    save_users(users)


def set_user_admin(user_id: str, is_admin: bool) -> None:
    users = load_users()
    for u in users:
        if u["id"] == user_id:
            u["is_admin"] = bool(is_admin)
            break
    save_users(users)


def delete_user_record(user_id: str) -> None:
    """只刪登記簿裡的記錄;使用者的資料目錄由 service 層另外清(跨層操作)。"""
    save_users([u for u in load_users() if u["id"] != user_id])


def count_admins() -> int:
    """目前 is_admin=True 的使用者數(用來防呆:不能降/刪最後一個 admin)。
    注意:ADMIN_EMAILS env 的即時救援不計入這裡——救援名單本就不依賴登記簿。"""
    return sum(1 for u in load_users() if u.get("is_admin") is True)


def promote_admins_by_email(emails: set[str]) -> list[str]:
    """把登記簿裡 email 命中 `emails` 的既有使用者升成 admin(is_admin=True)並落地,
    回傳實際被升級(原本不是 admin)的 email 清單。

    這是 ADMIN_EMAILS env 的「移轉」語意:env 不只是即時救援(auth.is_admin 的執行期
    判斷),啟動時也把命中的既有帳號永久寫成 store admin——讓「部署在 admin 機制加入
    之前、只有 OAuth 帳號、沒有任何 store admin」的既有站台,能靠設一次 env + 重啟
    把某個既有帳號轉正成 admin,之後即使拿掉 env 也仍是 admin。冪等:已是 admin 的
    不動、也不回報。"""
    if not emails:
        return []
    emails = {e.strip().lower() for e in emails if e.strip()}
    users = load_users()
    promoted: list[str] = []
    for u in users:
        if u.get("email", "").strip().lower() in emails and u.get("is_admin") is not True:
            u["is_admin"] = True
            promoted.append(u["email"])
    if promoted:
        save_users(users)
    return promoted

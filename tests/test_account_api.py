"""
帳號安全(設定 → 帳號)的整合測試:變更/設定密碼、停用密碼登入、解除 Google 連結、
介面語言跟著帳號走。

最重要的兩支:
- test_cannot_remove_the_only_login_method —— 停用唯一的登入方式會把帳號永遠鎖死,
  防呆跟「不能刪最後一個 admin」同一類。
- test_wrong_current_password_shares_the_login_ledger —— 改密碼驗舊密碼走的是與
  登入**同一本**速率限制帳本;不共用的話這支端點就是繞過登入鎖定的第二個猜密碼入口。
"""
import pytest

from app import ratelimit
from app.users import find_by_google_sub, link_google_sub, set_password_hash

PASSWORD = "testpass123"


@pytest.fixture(autouse=True)
def _clean_ratelimit():
    """速率限制狀態是行程內記憶體(模組層),不清的話測試之間互相污染。"""
    ratelimit.reset()
    yield
    ratelimit.reset()


def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


# ── 登入方式現況(/me) ─────────────────────────────────────────────

def test_me_reports_login_methods(register_user):
    u = register_user()
    me = u["client"].get("/api/auth/me").json()
    assert me["has_password"] is True
    assert me["has_google"] is False
    assert "password_hash" not in me, "/me 絕不外洩雜湊"

    link_google_sub(u["id"], "sub-123")
    me = u["client"].get("/api/auth/me").json()
    assert me["has_google"] is True


# ── 變更密碼 ────────────────────────────────────────────────────────

def test_change_password_roundtrip(register_user, client):
    u = register_user()
    # 錯的舊密碼被拒,而且原密碼原封不動
    r = u["client"].put("/api/auth/password", json={
        "current_password": "not-my-password", "new_password": "brand-new-pw-1",
    })
    assert r.status_code == 403
    assert _login(client, u["email"], PASSWORD).status_code == 200, "原密碼必須原封不動"
    # 太短的新密碼 400
    assert u["client"].put("/api/auth/password", json={
        "current_password": PASSWORD, "new_password": "short",
    }).status_code == 400
    # 正確變更:舊密碼失效、新密碼可登入(client 是另一個獨立 cookie jar)
    r = u["client"].put("/api/auth/password", json={
        "current_password": PASSWORD, "new_password": "brand-new-pw-1",
    })
    assert r.status_code == 200, r.text
    assert _login(client, u["email"], PASSWORD).status_code == 401
    assert _login(client, u["email"], "brand-new-pw-1").status_code == 200


def test_google_only_account_sets_password_without_current(register_user, client):
    """Google-only 帳號(password_hash 為 null)沒有舊密碼可驗,session 本身
    就是身分證明——設定密碼 = 啟用 email 登入。"""
    u = register_user()
    link_google_sub(u["id"], "sub-g1")
    set_password_hash(u["id"], None)  # 模擬 Google 首登建立的帳號
    assert _login(client, u["email"], PASSWORD).status_code == 401

    r = u["client"].put("/api/auth/password", json={"new_password": "google-user-pw1"})
    assert r.status_code == 200, r.text
    assert _login(client, u["email"], "google-user-pw1").status_code == 200


def test_wrong_current_password_shares_the_login_ledger(register_user, client):
    """連續猜錯「目前密碼」要跟登入失敗記在同一本帳:到門檻(預設 5 次/帳號)後,
    改密碼端點與登入端點都要回 429——包括**正確**密碼的登入,比照登入鎖定的語意。"""
    u = register_user()
    for _ in range(5):
        r = u["client"].put("/api/auth/password", json={
            "current_password": "guess-wrong", "new_password": "whatever-pw-1",
        })
    assert r.status_code == 403
    r = u["client"].put("/api/auth/password", json={
        "current_password": PASSWORD, "new_password": "whatever-pw-1",
    })
    assert r.status_code == 429, "鎖定期間連正確的舊密碼也要擋"
    assert _login(client, u["email"], PASSWORD).status_code == 429, "登入端點要看得到同一本帳"


# ── 停用登入方式 ────────────────────────────────────────────────────

def test_cannot_remove_the_only_login_method(register_user):
    u = register_user()
    # 密碼是唯一方式 → 不能停用密碼
    assert u["client"].delete("/api/auth/password").status_code == 400
    # 換成 Google-only → 不能解除 Google
    link_google_sub(u["id"], "sub-g2")
    set_password_hash(u["id"], None)
    assert u["client"].delete("/api/auth/google").status_code == 400


def test_remove_one_login_method_when_another_remains(register_user, client):
    # 密碼 + Google 並存 → 可停用密碼登入
    a = register_user()
    link_google_sub(a["id"], "sub-g3")
    assert a["client"].delete("/api/auth/password").status_code == 200
    assert _login(client, a["email"], PASSWORD).status_code == 401
    me = a["client"].get("/api/auth/me").json()
    assert me["has_password"] is False and me["has_google"] is True

    # 反向:密碼還在 → 可解除 Google 連結
    b = register_user()
    link_google_sub(b["id"], "sub-g4")
    assert b["client"].delete("/api/auth/google").status_code == 200
    assert find_by_google_sub("sub-g4") is None
    me = b["client"].get("/api/auth/me").json()
    assert me["has_password"] is True and me["has_google"] is False


# ── 介面語言跟著帳號走 ──────────────────────────────────────────────

def test_lang_roundtrip_and_isolation(register_user):
    """語言存進帳號、/me 帶回;兩個帳號互不影響(修「A 選英文、B 也變英文」——
    這正是把語言從裝置 localStorage 搬到帳號上的理由)。lang=null 清除後
    /me 回 None(跟隨裝置)。"""
    a, b = register_user(), register_user()
    assert a["client"].get("/api/auth/me").json()["lang"] is None, "未設定 = 跟隨裝置"

    r = a["client"].put("/api/auth/lang", json={"lang": "en"})
    assert r.status_code == 200, r.text
    assert a["client"].get("/api/auth/me").json()["lang"] == "en"
    assert b["client"].get("/api/auth/me").json()["lang"] is None, "B 帳號不受 A 影響"

    assert a["client"].put("/api/auth/lang", json={"lang": None}).status_code == 200
    assert a["client"].get("/api/auth/me").json()["lang"] is None


def test_lang_rejects_unsupported_code(register_user):
    u = register_user()
    assert u["client"].put("/api/auth/lang", json={"lang": "xx-Nope"}).status_code == 400
    assert u["client"].get("/api/auth/me").json()["lang"] is None, "壞值不落地"


# ── 驗證防線 ────────────────────────────────────────────────────────

def test_all_account_endpoints_require_login(client):
    assert client.put("/api/auth/password", json={"new_password": "x" * 10}).status_code == 401
    assert client.delete("/api/auth/password").status_code == 401
    assert client.delete("/api/auth/google").status_code == 401
    assert client.put("/api/auth/lang", json={"lang": "en"}).status_code == 401

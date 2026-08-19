"""
Admin API 整合測試(走 HTTP)。

隔離:site_settings.json 與 users.json 都是 session 共用的全域檔。每個測試前
清掉 site_settings.json(讓它依當下 env 重新種子),admin_ctx 另外把 users.json
清空,好讓「第一位註冊者自動成為 admin」這條規則可被穩定驗證,也提供一個
真正的 admin client 給其他測試用。
"""
import pytest
from fastapi.testclient import TestClient

from app import site_settings as ss
from app.paths import user_paths
from app.users import save_users, set_user_admin


@pytest.fixture(autouse=True)
def _clean_site_settings():
    ss.SITE_SETTINGS_PATH.unlink(missing_ok=True)
    yield
    ss.SITE_SETTINGS_PATH.unlink(missing_ok=True)


@pytest.fixture
def admin_ctx(app_instance, monkeypatch):
    """清空登記簿 → 註冊第一位使用者(自動 admin)→ 回傳其 client 與 id。"""
    save_users([])
    monkeypatch.setenv("ALLOWED_EMAILS", "admin@example.com")
    ss.SITE_SETTINGS_PATH.unlink(missing_ok=True)
    ss.ensure_site_settings()
    c = TestClient(app_instance)
    r = c.post("/api/auth/register", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return {"client": c, "id": r.json()["id"]}


def test_first_registered_user_is_admin_and_bypasses_whitelist(app_instance, monkeypatch):
    """裝好後完全沒設 ALLOWED_EMAILS(不用碰環境變數/PowerShell),只要系統裡
    還沒有任何使用者,註冊照樣放行——這一位注定成為第一個 admin(見
    app/routers/auth.py 的 first_user 判斷),不受預設 whitelist 模式限制。"""
    save_users([])
    ss.SITE_SETTINGS_PATH.unlink(missing_ok=True)
    monkeypatch.delenv("ALLOWED_EMAILS", raising=False)
    ss.ensure_site_settings()  # 預設 whitelist 模式、空白名單
    c = TestClient(app_instance)
    r = c.post("/api/auth/register",
               json={"email": "nobody-whitelisted@example.com", "password": "testpass123"})
    assert r.status_code == 200, r.text
    assert r.json()["is_admin"] is True
    assert c.get("/api/auth/me").json()["is_admin"] is True


def test_admin_emails_env_promotes_existing_user_on_startup(register_user, monkeypatch):
    """ADMIN_EMAILS 的移轉語意:啟動時把 env 命中的既有帳號永久寫成 store admin。
    模擬「站台已有 OAuth 帳號、但沒有 store admin」→ 設 env + 重建 app → 該帳號被轉正。"""
    from app import create_app
    from app.users import find_by_id, set_user_admin
    from fastapi.testclient import TestClient

    u = register_user()
    set_user_admin(u["id"], False)  # 確保起始不是 admin
    assert find_by_id(u["id"]).get("is_admin") is not True

    monkeypatch.setenv("ADMIN_EMAILS", u["email"].upper())  # 大小寫不敏感
    create_app()  # 啟動時應把這個既有帳號升成 store admin

    assert find_by_id(u["id"])["is_admin"] is True  # 已永久落地
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    # 拿掉 env 後仍是 admin(真的轉移了,不只是執行期救援)
    c = TestClient(create_app())
    c.cookies.update(u["client"].cookies)
    assert c.get("/api/auth/me").json()["is_admin"] is True


def test_non_admin_gets_403(register_user):
    u = register_user()
    set_user_admin(u["id"], False)  # 確保不是 admin(避免剛好是全場第一位)
    r = u["client"].get("/api/admin/settings")
    assert r.status_code == 403


def test_admin_settings_read_update_and_clamp(admin_ctx):
    c = admin_ctx["client"]
    assert c.get("/api/admin/settings").json()["registration_mode"] == "whitelist"
    assert c.put("/api/admin/settings/registration", json={"mode": "open"}).json()["registration_mode"] == "open"
    assert c.put("/api/admin/settings/registration", json={"mode": "bogus"}).status_code == 400
    body = c.put("/api/admin/settings/whitelist", json={"emails": ["A@x.com", "a@x.com", "b@y.com"]}).json()
    assert body["allowed_emails"] == ["a@x.com", "b@y.com"]  # 小寫去重

    # 備份設定收每日時刻;不合法的時刻收斂成「不指定」,不是報錯——空值本來就是合法狀態
    body = c.put("/api/admin/settings/backup", json={
        "auto_enabled": True, "interval_days": 7, "keep": 10, "at_time": "03:30",
    }).json()
    assert body["backup"]["at_time"] == "03:30"
    assert c.put("/api/admin/settings/backup", json={
        "auto_enabled": True, "interval_days": 7, "keep": 10, "at_time": "99:99",
    }).json()["backup"]["at_time"] == ""


def test_oauth_secret_is_never_returned(admin_ctx):
    c = admin_ctx["client"]
    body = c.put("/api/admin/settings/oauth",
                 json={"enabled": True, "client_id": "cid", "client_secret": "topsecret"}).json()
    g = body["google_oauth"]
    assert g["enabled"] is True and g["client_id"] == "cid"
    assert "client_secret" not in g and g["has_secret"] is True
    # 空 secret = 沿用既存(不清掉)
    body2 = c.put("/api/admin/settings/oauth",
                  json={"enabled": True, "client_id": "cid2", "client_secret": ""}).json()
    assert body2["google_oauth"]["has_secret"] is True
    assert ss.google_oauth_config()["client_secret"] == "topsecret"


def test_ai_api_key_never_appears_in_the_admin_settings_payload(admin_ctx):
    """AI 連線設定住在 site_settings 的 ai 區塊,而 _public_settings() 是**白名單**
    ——那個區塊(含 api_key)刻意不在裡面,它有自己的一組端點。"""
    c = admin_ctx["client"]
    c.put("/api/ai/settings", json={"api_key": "sk-super-secret"})

    body = c.get("/api/admin/settings")
    assert "sk-super-secret" not in body.text
    assert "ai" not in body.json()


def test_registration_mode_gates_register_and_is_publicly_visible(admin_ctx, app_instance, monkeypatch):
    admin_ctx["client"].put("/api/admin/settings/registration", json={"mode": "closed"})
    monkeypatch.setenv("ALLOWED_EMAILS", "newbie@example.com")
    anon = TestClient(app_instance)  # 未登入
    r = anon.post("/api/auth/register", json={"email": "newbie@example.com", "password": "pw123456"})
    assert r.status_code == 403  # closed → 擋下,即使在白名單裡
    # /api/auth/config 公開、反映設定,且不外洩其他設定
    cfg = anon.get("/api/auth/config").json()
    assert cfg["registration_open"] is False
    assert set(cfg.keys()) == {"registration_open", "google_enabled"}


def test_user_management_and_last_admin_guards(admin_ctx, register_user):
    c = admin_ctx["client"]
    other = register_user()  # admin_ctx 已先跑,登記簿非空 → 這位不是 admin

    users = {u["email"]: u for u in c.get("/api/admin/users").json()["users"]}
    assert users["admin@example.com"]["is_admin"] is True
    assert users[other["email"]]["is_admin"] is False

    assert c.put(f"/api/admin/users/{other['id']}/admin", json={"is_admin": True}).status_code == 200
    assert c.put(f"/api/admin/users/{other['id']}/admin", json={"is_admin": False}).status_code == 200

    # 刪使用者連同資料目錄
    root = user_paths(other["id"]).root
    assert root.exists()
    assert c.delete(f"/api/admin/users/{other['id']}").status_code == 200
    assert not root.exists()
    assert c.get(f"/api/admin/users/{other['id']}").status_code in (404, 405)  # 已不存在

    # 現在 admin 是唯一 admin:降自己與刪自己都要被擋
    assert c.put(f"/api/admin/users/{admin_ctx['id']}/admin",
                 json={"is_admin": False}).status_code == 400
    assert c.delete(f"/api/admin/users/{admin_ctx['id']}").status_code == 400

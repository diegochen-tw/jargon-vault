"""
站台註冊邀請連結(無團隊版,登記簿在 data/invites.json)。

守的是三件事:
  1. **漏斗真的被縮短**——沒有帳號的人拿著連結就能註冊,
     不需要 admin 另外去填白名單(繞過 registration_mode 是**刻意**的)
  2. **撤銷是真的**——nonce 隨機、撤銷後舊網址立刻失效、一次性連結用完就沒了
  3. **失敗註冊不燒邀請**——先 peek、建好帳號才 consume
     (`test_failed_registration_does_not_burn_the_invite` 是這檔最重要的一支)
"""
import time

import pytest

from app import invites
from app.users import set_user_admin


@pytest.fixture(autouse=True)
def _clean_invites():
    """邀請登記簿是站台級全域檔,不隨 register_user 的 paths 隔離——比照
    test_share_links_api.py 對 site_settings 的做法,前後各清一次。"""
    invites.INVITES_PATH.unlink(missing_ok=True)
    yield
    invites.INVITES_PATH.unlink(missing_ok=True)


def _make_admin(register_user):
    """users.json 是 session 共用的,「第一位註冊者自動 admin」在這裡不可靠——
    直接把新帳號升成站台 admin(同 test_admin_api.py 對隔離問題的處理方向)。"""
    u = register_user()
    set_user_admin(u["id"], True)
    return u


def _invite(admin, **kw):
    r = admin["client"].post("/api/admin/invites", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


# ── 產生與撤銷 ──────────────────────────────────────────────────────

def test_defaults_are_strict(register_user):
    """一次性、7 天。連結外流的最壞情況要小,這兩個預設值是防線。"""
    admin = _make_admin(register_user)
    inv = _invite(admin)
    assert inv["uses_left"] == 1
    assert 6.9 * 86400 < inv["expires"] - inv["created"] < 7.1 * 86400


def test_token_is_random_not_derived(client, register_user):
    """★ 撤銷後舊網址立刻失效;撤銷 → 重新產生必須給出不同的字串——
    內容若是決定性的,存過舊網址的人立刻恢復存取,撤銷就是假的。"""
    admin = _make_admin(register_user)
    a = _invite(admin)
    assert admin["client"].delete(f"/api/admin/invites/{a['nonce']}").status_code == 200
    assert client.get(f"/api/invite/{a['nonce']}").json() == {"valid": False}
    new = _invite(admin)
    assert a["nonce"] != new["nonce"]


def test_only_site_admin_manages_invites(client, register_user):
    """邀請會繞過白名單,本質是註冊控制——產生/列出/撤銷收站台 admin。"""
    admin, member = _make_admin(register_user), register_user()
    inv = _invite(admin)
    assert member["client"].post("/api/admin/invites", json={}).status_code == 403
    assert member["client"].get("/api/admin/invites").status_code == 403
    assert member["client"].delete(f"/api/admin/invites/{inv['nonce']}").status_code == 403
    assert client.get("/api/admin/invites").status_code == 401


# ── 預覽(公開,不需要登入)────────────────────────────────────────

def test_preview_is_public_narrow_and_does_not_consume(client, register_user):
    """預覽不需登入;回應刻意只有 valid 一個布林;
    看幾眼都不會把一次性連結燒掉;邀請頁 noindex。"""
    admin = _make_admin(register_user)
    inv = _invite(admin)
    for _ in range(3):
        r = client.get(f"/api/invite/{inv['nonce']}")
        assert r.status_code == 200
        assert r.json() == {"valid": True}
    assert invites.peek(inv["nonce"]) is not None

    page = client.get(f"/invite/{inv['nonce']}")
    assert page.status_code == 200
    assert "noindex" in page.headers.get("x-robots-tag", "")


@pytest.mark.parametrize("bad", ["nonsense", "a.b.c", "x.", ".y", "%2e%2e", "..%2Fx"])
def test_malformed_tokens_report_invalid(client, bad):
    """形狀不對的 token:走到 handler 就是 200 + valid:false;帶路徑分隔符的
    在路由層就不匹配(404)——測的是**結果**:任何情況都不能回出有效邀請、
    不能 500。token 只當 dict 的 key 查表,從不組進任何路徑,所以沒有穿越面。"""
    r = client.get(f"/api/invite/{bad}")
    assert r.status_code in (200, 404), f"{bad} → {r.status_code}"
    if r.status_code == 200:
        assert r.json() == {"valid": False}


def test_unknown_and_expired_look_identical(client, register_user):
    """區分開來只是把登記簿內部狀態洩漏給拿著亂猜 token 的人。"""
    admin = _make_admin(register_user)
    inv = _invite(admin)
    data = invites.load_invites()
    data[inv["nonce"]]["expires"] = time.time() - 1
    invites.save_invites(data)
    assert client.get(f"/api/invite/{inv['nonce']}").json() == {"valid": False}
    assert client.get("/api/invite/doesnotexist").json() == {"valid": False}


# ── 註冊(繞過白名單)────────────────────────────────────────────

def test_register_with_invite_bypasses_the_whitelist(app_instance, register_user):
    """★ 這是整條功能存在的理由:admin 不必先去填白名單——
    連 registration_mode=closed 也擋不住持有連結的人。"""
    from fastapi.testclient import TestClient
    from app import site_settings
    admin = _make_admin(register_user)
    inv = _invite(admin)

    fresh = TestClient(app_instance)
    r = fresh.post("/api/auth/register",
                   json={"email": "newbie@example.com", "password": "testpass123",
                         "invite": inv["nonce"]})
    assert r.status_code == 200, r.text
    # 而且立刻是一個能用的帳號
    assert fresh.post("/api/notes", json={"name": "SFC"}).status_code == 200

    # 註冊整個關閉時,持有連結照樣進得來
    inv2 = _invite(admin)
    s = site_settings.load_site_settings()
    s["registration_mode"] = "closed"
    site_settings.save_site_settings(s)
    try:
        fresh2 = TestClient(app_instance)
        r = fresh2.post("/api/auth/register",
                        json={"email": "closed-ok@example.com", "password": "testpass123",
                              "invite": inv2["nonce"]})
        assert r.status_code == 200, r.text
    finally:
        s["registration_mode"] = "whitelist"
        site_settings.save_site_settings(s)


def test_register_without_invite_is_still_blocked(app_instance, register_user):
    """沒有邀請的人照舊受白名單擋著——繞過的權限來自**持有連結**,不是端點放寬了。"""
    from fastapi.testclient import TestClient
    register_user()  # 先有第一個使用者,否則會走 first_user 的免檢查分支
    fresh = TestClient(app_instance)
    r = fresh.post("/api/auth/register",
                   json={"email": "stranger@example.com", "password": "testpass123"})
    assert r.status_code == 403


def test_one_time_invite_is_consumed_and_multi_use_counts(app_instance, register_user):
    """一次性連結放一個人進來就失效;uses=2 的連結放兩個人。"""
    from fastapi.testclient import TestClient
    admin = _make_admin(register_user)
    one = _invite(admin)
    assert TestClient(app_instance).post(
        "/api/auth/register",
        json={"email": "first@example.com", "password": "testpass123",
              "invite": one["nonce"]}).status_code == 200
    assert TestClient(app_instance).post(
        "/api/auth/register",
        json={"email": "second@example.com", "password": "testpass123",
              "invite": one["nonce"]}).status_code == 403

    multi = _invite(admin, uses=2)
    for mail in ("c@example.com", "d@example.com"):
        assert TestClient(app_instance).post(
            "/api/auth/register",
            json={"email": mail, "password": "testpass123",
                  "invite": multi["nonce"]}).status_code == 200
    assert invites.peek(multi["nonce"]) is None


def test_failed_registration_does_not_burn_the_invite(app_instance, register_user):
    """★ 註冊失敗(密碼太短、email 重複)不該燒掉一次性連結——
    所以註冊路徑是先 peek、建好帳號才 consume。"""
    from fastapi.testclient import TestClient
    admin = _make_admin(register_user)
    inv = _invite(admin)
    fresh = TestClient(app_instance)
    assert fresh.post("/api/auth/register",
                      json={"email": "short@example.com", "password": "123",
                            "invite": inv["nonce"]}).status_code == 400
    assert invites.peek(inv["nonce"]) is not None
    # email 已被 admin 用掉 → 400,邀請同樣不能被燒掉
    assert fresh.post("/api/auth/register",
                      json={"email": admin["email"], "password": "testpass123",
                            "invite": inv["nonce"]}).status_code == 400
    assert invites.peek(inv["nonce"]) is not None


def test_broken_invites_file_denies_everything(client, register_user):
    """授權的預設值必須是拒絕。"""
    admin = _make_admin(register_user)
    inv = _invite(admin)
    invites.INVITES_PATH.write_text("{ not json", encoding="utf-8")
    assert client.get(f"/api/invite/{inv['nonce']}").json() == {"valid": False}

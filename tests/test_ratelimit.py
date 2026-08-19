"""
登入失敗鎖定(app/ratelimit.py)的單元測試 + 走 HTTP 的整合測試。

最重要的幾支:
  - test_ip_and_email_are_counted_independently:兩個維度必須各自獨立,
    少了任一個就有一整類攻擊擋不住。
  - test_forwarded_for_and_client_ip_fallbacks:沒有代理卻採信 XFF,
    per-IP 這條線等於不存在(攻擊者每次換一個假 IP)。
  - test_locked_out_login_rejects_even_the_correct_password:鎖定期間連正確
    密碼都擋——否則攻擊者猜中就直接通過,鎖定形同虛設。
"""
import time

import pytest

from app import ratelimit


def cfg(**over):
    base = {
        "enabled": True, "ip_max_attempts": 3, "email_max_attempts": 2,
        "window_minutes": 15, "lockout_minutes": 15, "trust_forwarded_for": False,
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _clean():
    ratelimit.reset()
    yield
    ratelimit.reset()


# ── client_ip:兩個方向都會壞的那個判斷 ────────────────────────────────────

def test_forwarded_for_and_client_ip_fallbacks():
    """沒開 trust_proxy 就一律用 remote_addr。X-Forwarded-For 是用戶端可以任意
    填的標頭——採信它等於讓攻擊者每次換一個假 IP,per-IP 計數形同虛設。
    信任時要取**最左邊**那一跳(XFF 慣例是 `client, proxy1, proxy2`),取錯邊
    會把所有人收斂成最後一台代理的位址,等於全世界共用一個計數器。"""
    assert ratelimit.client_ip("10.0.0.1", "1.2.3.4", trust_proxy=False) == "10.0.0.1"
    assert ratelimit.client_ip("10.0.0.1", "1.2.3.4", trust_proxy=True) == "1.2.3.4"
    assert ratelimit.client_ip("10.0.0.1", "1.2.3.4, 10.0.0.9, 10.0.0.8",
                               trust_proxy=True) == "1.2.3.4"
    assert ratelimit.client_ip(None, None, trust_proxy=False) == "unknown"
    assert ratelimit.client_ip("", "   ", trust_proxy=True) == "unknown"


# ── 計數與鎖定 ──────────────────────────────────────────────────────────────

def test_thresholds_window_and_lockout_expiry(monkeypatch):
    """門檻要精確(n-1 不鎖、n 鎖);超出時間窗的舊失敗不算數;鎖定時間過了
    要自己解開。"""
    c = cfg(email_max_attempts=2, ip_max_attempts=99)
    assert ratelimit.check("ip1", "a@b.c", c) == 0
    ratelimit.record_failure("ip1", "a@b.c", c)
    assert ratelimit.check("ip1", "a@b.c", c) == 0, "還沒到門檻就不該鎖"
    ratelimit.record_failure("ip1", "a@b.c", c)
    assert ratelimit.check("ip1", "a@b.c", c) > 0, "達到門檻就要鎖"

    ratelimit.reset()
    c = cfg(email_max_attempts=2, window_minutes=15, lockout_minutes=1)
    now = time.time()
    monkeypatch.setattr(ratelimit, "_now", lambda: now - 3600)  # 一小時前失敗兩次
    ratelimit.record_failure("ip1", "a@b.c", c)
    ratelimit.record_failure("ip1", "a@b.c", c)
    monkeypatch.setattr(ratelimit, "_now", lambda: now)
    assert ratelimit.check("ip1", "a@b.c", c) == 0, "超出時間窗的舊失敗不該再算數"

    ratelimit.reset()
    c = cfg(email_max_attempts=2, window_minutes=60, lockout_minutes=10)
    monkeypatch.setattr(ratelimit, "_now", lambda: now)
    ratelimit.record_failure("ip1", "a@b.c", c)
    ratelimit.record_failure("ip1", "a@b.c", c)
    assert ratelimit.check("ip1", "a@b.c", c) > 0
    monkeypatch.setattr(ratelimit, "_now", lambda: now + 11 * 60)
    assert ratelimit.check("ip1", "a@b.c", c) == 0, "鎖定時間過了要自己解開"


def test_ip_and_email_are_counted_independently():
    """per-IP 擋『一台機器噴很多帳號』,per-email 擋『很多機器噴一個帳號』。
    只有一個維度時,另一類攻擊完全不受影響。"""
    c = cfg(ip_max_attempts=3, email_max_attempts=99)
    # 同一個 IP 對三個不同帳號各失敗一次 → per-email 都沒到門檻,但 per-IP 到了
    for email in ("a@x.c", "b@x.c", "c@x.c"):
        ratelimit.record_failure("attacker", email, c)
    assert ratelimit.check("attacker", "never-seen@x.c", c) > 0, "per-IP 應該擋下"
    assert ratelimit.check("someone-else", "a@x.c", c) == 0, "不該波及其他 IP"

    ratelimit.reset()
    c = cfg(ip_max_attempts=99, email_max_attempts=3)
    # 三台不同機器打同一個帳號 → per-IP 都沒到門檻,但 per-email 到了
    for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
        ratelimit.record_failure(ip, "victim@x.c", c)
    assert ratelimit.check("4.4.4.4", "victim@x.c", c) > 0, "per-email 應該擋下"
    assert ratelimit.check("4.4.4.4", "other@x.c", c) == 0, "不該波及其他帳號"


def test_success_clears_counters():
    """成功登入清空計數——連已達門檻、已鎖定的舊帳也一樣(『開著→累積失敗→
    關掉→再打開』不可以讓人被舊帳鎖住,清除永遠安全)。"""
    c = cfg(email_max_attempts=2)
    ratelimit.record_failure("ip1", "a@b.c", c)
    ratelimit.record_success("ip1", "a@b.c")
    ratelimit.record_failure("ip1", "a@b.c", c)
    assert ratelimit.check("ip1", "a@b.c", c) == 0, "成功登入後計數要歸零"
    # 已達門檻後,成功一樣要清得掉
    ratelimit.record_failure("ip1", "a@b.c", c)
    assert ratelimit.check("ip1", "a@b.c", c) > 0
    ratelimit.record_success("ip1", "a@b.c")
    assert ratelimit.check("ip1", "a@b.c", c) == 0


def test_disabled_never_locks_and_never_records():
    c = cfg(enabled=False, email_max_attempts=1)
    for _ in range(10):
        ratelimit.record_failure("ip1", "a@b.c", c)
    assert ratelimit.check("ip1", "a@b.c", c) == 0
    # 關閉期間不記帳,所以打開之後也不會突然拿舊帳鎖人
    assert ratelimit.check("ip1", "a@b.c", cfg(email_max_attempts=1)) == 0


def test_tracked_keys_are_bounded(monkeypatch):
    """信任 XFF 時,IP 是攻擊者可控的外部輸入——沒有上限就是記憶體耗盡攻擊。"""
    monkeypatch.setattr(ratelimit, "MAX_TRACKED_KEYS", 50)
    c = cfg()
    for i in range(500):
        ratelimit.record_failure(f"10.0.0.{i}", f"u{i}@x.c", c)
    assert ratelimit.snapshot()["tracked_keys"] <= 50 + 2


# ── 走 HTTP ─────────────────────────────────────────────────────────────────
# users.json 與 site_settings.json 都是 session 共用的全域檔,所以要沿用
# test_admin_api.py 的做法:清空登記簿後註冊第一位使用者(自動成為 admin)。
# 少了這步,register_user() 拿到的永遠不是 admin,所有管理端點都會 403。

ADMIN_EMAIL, ADMIN_PW = "admin@example.com", "adminpass1"


@pytest.fixture(autouse=True)
def _clean_site_settings():
    from app import site_settings as ss
    ss.SITE_SETTINGS_PATH.unlink(missing_ok=True)
    yield
    ss.SITE_SETTINGS_PATH.unlink(missing_ok=True)


@pytest.fixture
def admin_ctx(app_instance, monkeypatch):
    from fastapi.testclient import TestClient
    from app import site_settings as ss
    from app.users import save_users
    save_users([])
    monkeypatch.setenv("ALLOWED_EMAILS", ADMIN_EMAIL)
    ss.SITE_SETTINGS_PATH.unlink(missing_ok=True)
    ss.ensure_site_settings()
    c = TestClient(app_instance)
    r = c.post("/api/auth/register", json={"email": ADMIN_EMAIL, "password": ADMIN_PW})
    assert r.status_code == 200, r.text
    return {"client": c, "id": r.json()["id"], "app": app_instance}


def _set_rate_limit(admin_client, **over):
    body = {"enabled": True, "ip_max_attempts": 20, "email_max_attempts": 2,
            "window_minutes": 15, "lockout_minutes": 15, "trust_forwarded_for": False}
    body.update(over)
    r = admin_client.put("/api/admin/settings/ratelimit", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _bad_login(c, times=1):
    out = None
    for _ in range(times):
        out = c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"})
    return out


def test_login_locks_out_and_disabling_never_locks(admin_ctx):
    c = admin_ctx["client"]
    _set_rate_limit(c, email_max_attempts=2)
    for _ in range(2):
        assert _bad_login(c).status_code == 401
    r = _bad_login(c)
    assert r.status_code == 429
    assert "Retry-After" in r.headers, "429 要說得出還要等多久,不能只說『錯誤』"
    # 停用後失敗只會是 401,永遠不該出現 429
    _set_rate_limit(c, enabled=False, email_max_attempts=1)
    for _ in range(6):
        assert _bad_login(c).status_code == 401, "停用時只會是 401,永遠不該出現 429"


def test_locked_out_login_rejects_even_the_correct_password(admin_ctx):
    """鎖定期間連正確密碼都擋——否則攻擊者猜中就直接通過,鎖定形同虛設。"""
    c = admin_ctx["client"]
    _set_rate_limit(c, email_max_attempts=1)
    _bad_login(c, 2)
    r = c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PW})
    assert r.status_code == 429


def test_admin_reset_and_saving_settings_clear_lockouts(admin_ctx):
    """兩條救援路徑:reset 是「把自己鎖在門外」時的逃生門(沒有它就只能等或
    重啟伺服器);存設定順帶清鎖則是為了避免『調寬門檻卻還被舊帳鎖著』——
    這種設定最典型的『我明明改了為什麼沒用』。"""
    c = admin_ctx["client"]
    _set_rate_limit(c, email_max_attempts=1)
    assert _bad_login(c, 2).status_code == 429
    assert c.post("/api/admin/ratelimit/reset").status_code == 200
    assert c.post("/api/auth/login",
                  json={"email": ADMIN_EMAIL, "password": ADMIN_PW}).status_code == 200

    # 再鎖一次,這次靠「存設定」解鎖
    assert _bad_login(c, 2).status_code == 429
    _set_rate_limit(c, email_max_attempts=50)
    assert c.post("/api/auth/login",
                  json={"email": ADMIN_EMAIL, "password": ADMIN_PW}).status_code == 200


def test_out_of_range_values_are_clamped_not_rejected(admin_ctx):
    """門檻填 0 會讓沒失敗過的人也算『已達上限』而全站鎖死;鎖定時間填一年
    等於永久封鎖。夾住而不是拒絕,任何輸入都還原得回可用狀態。"""
    s = _set_rate_limit(admin_ctx["client"], ip_max_attempts=0, lockout_minutes=999999)
    rl = s["rate_limit"]
    assert rl["ip_max_attempts"] >= 1
    assert rl["lockout_minutes"] <= 1440


def test_rate_limit_settings_require_admin(admin_ctx, register_user):
    other = register_user()  # 不是第一位 → 不是 admin
    assert other["client"].put("/api/admin/settings/ratelimit",
                               json={"enabled": True}).status_code == 403
    assert other["client"].post("/api/admin/ratelimit/reset").status_code == 403
    assert admin_ctx["client"].post("/api/admin/ratelimit/reset").status_code == 200


def test_email_case_variants_share_one_counter(admin_ctx):
    """email 的正規化必須與 users.find_by_email() 一致(strip + lower)。
    不一致的話大小寫變體會落在不同計數器上、卻查到同一個帳號——攻擊者輪流
    變換大小寫就能把 per-email 的門檻放大好幾倍。"""
    c = admin_ctx["client"]
    _set_rate_limit(c, email_max_attempts=2)
    for variant in (ADMIN_EMAIL, ADMIN_EMAIL.upper()):
        assert c.post("/api/auth/login",
                      json={"email": variant, "password": "wrong"}).status_code == 401
    r = c.post("/api/auth/login",
               json={"email": "  " + ADMIN_EMAIL.title() + " ", "password": "wrong"})
    assert r.status_code == 429, "大小寫/空白變體必須共用同一個計數器"

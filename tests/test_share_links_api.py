"""
單筆公開分享連結的整合測試(走 HTTP)。

這是全站唯一「沒有帳號也讀得到內容」的路徑,所以測試的重心是**邊界**:
站台預設關閉、撤銷要立刻生效、重新產生要打死舊網址、token 只授權那一筆名詞
與它的資產、不能被搜尋引擎收錄、48h 自動失效。
"""
import time

import pytest

from app import share_links
from app import site_settings as ss
from app.config import SHARE_LINK_TTL_HOURS


@pytest.fixture(autouse=True)
def _clean_site_settings():
    ss.SITE_SETTINGS_PATH.unlink(missing_ok=True)
    yield
    ss.SITE_SETTINGS_PATH.unlink(missing_ok=True)


def _enable_public_share(enabled: bool = True) -> None:
    s = ss.load_site_settings()
    s["public_share_enabled"] = enabled
    ss.save_site_settings(s)


def _make_note(u, name: str, description: str = "", tags=None) -> str:
    r = u["client"].post("/api/notes", json={
        "name": name, "description": description, "tags": tags or [],
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _share(u, nid: str) -> str:
    r = u["client"].post(f"/api/notes/{nid}/share")
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _attach_image(u, nid: str, filename: str = "pic.png") -> str:
    r = u["client"].post(f"/api/notes/{nid}/attachments",
                         files={"file": (filename, b"\x89PNG-fake-bytes", "image/png")})
    assert r.status_code == 200, r.text
    return r.json()["path"]


# ── 站台開關 ──────────────────────────────────────────────────────

def test_public_share_is_off_by_default(register_user):
    """預設關閉是刻意的:這個功能會把內容送出登入牆之外,預設值必須保守。"""
    ss.ensure_site_settings()
    assert ss.public_share_enabled() is False
    a = register_user()
    nid = _make_note(a, "termA")
    assert a["client"].post(f"/api/notes/{nid}/share").status_code == 403


def test_turning_site_switch_off_kills_existing_links(register_user, client):
    """總開關要是真的總開關——不只是「不能產生新的」,既有連結也立刻失效。
    但關掉之後仍該能撤銷自己留下的連結,擋住這件事沒有任何好處。"""
    _enable_public_share()
    a = register_user()
    nid = _make_note(a, "termB")
    token = _share(a, nid)
    assert client.get(f"/api/s/{token}").status_code == 200

    _enable_public_share(False)
    assert client.get(f"/api/s/{token}").status_code == 404

    # 重新開啟 → 同一條連結恢復(登記簿沒被清掉)
    _enable_public_share()
    assert client.get(f"/api/s/{token}").status_code == 200

    # 開關關掉時撤銷仍要能動
    _enable_public_share(False)
    r = a["client"].delete(f"/api/notes/{nid}/share")
    assert r.status_code == 200
    assert r.json()["token"] is None


# ── 不需登入就打得開 ───────────────────────────────────────────────

def test_anonymous_visitor_can_read_shared_note(register_user, client):
    """`client` 是完全沒有 cookie 的 TestClient——這一條就是整個功能的重點。
    JSON 內容與 HTML 外殼兩支都要開得起來。"""
    _enable_public_share()
    a = register_user()
    nid = _make_note(a, "cSSFI123", description="這是一個行話")
    token = _share(a, nid)

    r = client.get(f"/api/s/{token}")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "cSSFI123"
    assert body["description"] == "這是一個行話"

    page = client.get(f"/s/{token}")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]


def test_share_responses_are_noindex(register_user, client):
    """三支公開端點都要有:HTML 外殼、JSON 內容、資產。內容本身一樣不該被索引
    或被中介快取住,只擋 HTML 那一支是不夠的。robots.txt 也要擋 /s/。"""
    _enable_public_share()
    a = register_user()
    nid = _make_note(a, "termE")
    fname = _attach_image(a, nid).split("/")[-1]
    token = _share(a, nid)
    for path in [f"/s/{token}", f"/api/s/{token}", f"/s/{token}/assets/{fname}"]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert "noindex" in r.headers["x-robots-tag"], path
        assert r.headers["cache-control"] == "no-store", path
        assert r.headers["referrer-policy"] == "no-referrer", path

    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert "Disallow: /s/" in r.text


def test_bogus_tokens_are_404(client):
    _enable_public_share()
    for bad in ["", "nope", "no.dot.here", "../../etc.passwd", "abc.", ".abc",
                "a" * 200 + ".x"]:
        assert client.get(f"/api/s/{bad}").status_code in (404, 405), bad
    # HTML 外殼一律 200,有效性由前端顯示——不要用 HTTP 狀態碼把「這個 token
    # 存在」洩漏給掃描的人
    assert client.get("/s/deadbeefdead.nope").status_code == 200


# ── 投影:公開頁比登入後更嚴格 ──────────────────────────────────────

def test_public_projection_is_strict_and_self_contained(register_user, client):
    """投影不含 tags/id/history/marked(標籤常是公司內部代號,對站外讀者是純粹
    的洩漏);欄位標題在後端就攤平好(公開頁沒有 state.allTemplates,label 是
    英文基準的種子值——對全世界的匿名讀者,英文正是正確的預設);資產路徑在
    投影時就改寫成 s/<token>/assets/,前端一行都不用改。"""
    _enable_public_share()
    a = register_user()
    r = a["client"].post("/api/notes", json={
        "name": "termF", "description": "第一版", "tags": ["客戶A", "PCBA線"],
        "template": "jargon-default", "fields": {"alias": "別名甲"},
    })
    nid = r.json()["id"]
    path = _attach_image(a, nid)
    a["client"].put(f"/api/notes/{nid}", json={
        "name": "termF", "description": f"第二版 ![圖](/{path})", "tags": ["客戶A"],
        "template": "jargon-default", "fields": {"alias": "別名甲"},
        "attachments": [{"name": "pic.png", "path": path, "description": ""}],
    })
    a["client"].put(f"/api/notes/{nid}/mark", json={"marked": True})
    token = _share(a, nid)

    r = client.get(f"/api/s/{token}")
    body = r.json()
    for key in ("tags", "id", "history", "marked"):
        assert key not in body, key
    assert "客戶A" not in r.text
    assert "第一版" not in r.text
    assert {"label": "Alias", "value": "別名甲"} in body["fields"]
    assert body["attachments"][0]["path"].startswith(f"s/{token}/assets/")
    assert f"s/{token}/assets/" in body["description"]


# ── 撤銷與重新產生 ─────────────────────────────────────────────────

def test_revoked_link_dies_immediately(register_user, client):
    """撤銷立刻生效:JSON 與資產一起死,擁有者面板的狀態同步歸零。
    面板 payload 帶 ttl_hours 供前端提示文字用,不在 i18n 寫死 48。"""
    _enable_public_share()
    a = register_user()
    nid = _make_note(a, "termI")
    body = a["client"].get(f"/api/notes/{nid}/share").json()
    assert body["token"] is None
    assert body["ttl_hours"] == SHARE_LINK_TTL_HOURS

    fname = _attach_image(a, nid).split("/")[-1]
    token = _share(a, nid)
    body = a["client"].get(f"/api/notes/{nid}/share").json()
    assert body["token"] == token
    assert body["url"] == f"/s/{token}"
    assert body["enabled"] is True
    assert client.get(f"/api/s/{token}").status_code == 200
    assert client.get(f"/s/{token}/assets/{fname}").status_code == 200

    a["client"].delete(f"/api/notes/{nid}/share")
    assert client.get(f"/api/s/{token}").status_code == 404
    assert client.get(f"/s/{token}/assets/{fname}").status_code == 404
    assert a["client"].get(f"/api/notes/{nid}/share").json()["token"] is None


def test_regenerating_kills_the_old_link(register_user, client):
    """⚠ 這一條就是 nonce 必須隨機的理由:token 若由 note id 決定性推導,
    重新產生會得出同一個字串,存過舊網址的人立刻恢復存取。"""
    _enable_public_share()
    a = register_user()
    nid = _make_note(a, "termJ")
    old = _share(a, nid)
    new = _share(a, nid)

    assert old != new
    assert client.get(f"/api/s/{old}").status_code == 404
    assert client.get(f"/api/s/{new}").status_code == 200


def test_deleting_note_kills_link_and_restoring_revives_it(register_user, client):
    """名詞刪掉(搬進回收桶)→ 連結 404;還原 → 又能用。

    這是「檔案為真」的自然結果,是**預期行為不是 bug**——連結指的是那個檔案,
    檔案不在原位就讀不到。別把它「修掉」。
    """
    _enable_public_share()
    a = register_user()
    nid = _make_note(a, "termK")
    token = _share(a, nid)
    assert client.get(f"/api/s/{token}").status_code == 200

    a["client"].delete(f"/api/notes/{nid}")
    assert client.get(f"/api/s/{token}").status_code == 404

    a["client"].post(f"/api/trash/{nid}/restore")
    assert client.get(f"/api/s/{token}").status_code == 200


# ── 資產只跟著同一個 token 走 ───────────────────────────────────────

def test_shared_asset_is_scoped_to_the_shared_note(register_user, client):
    """nid 完全不出現在 URL、只從 token 解出來,所以分享一筆就只授權那一筆。"""
    _enable_public_share()
    a = register_user()
    shared_id = _make_note(a, "shared")
    other_id = _make_note(a, "notshared")
    shared_file = _attach_image(a, shared_id).split("/")[-1]
    other_file = _attach_image(a, other_id).split("/")[-1]
    token = _share(a, shared_id)

    assert client.get(f"/s/{token}/assets/{shared_file}").status_code == 200
    # 同一位使用者的另一筆名詞的檔案,拿這個 token 也讀不到
    assert client.get(f"/s/{token}/assets/{other_file}").status_code == 404


def test_shared_asset_path_traversal_is_blocked(register_user, client):
    """⚠ 只測**真的會抵達端點**的形式。

    未編碼的 `../../x` 會被 HTTP client 在送出前就摺疊掉,根本到不了這支路由
    (那是在測 httpx 不是測我們);百分比編碼的 %2F 才會被 Starlette 解碼進
    path 參數,那才是這層防護的實際攻擊面。
    """
    from app.paths import user_paths
    _enable_public_share()
    a = register_user()
    nid = _make_note(a, "termL")
    _attach_image(a, nid)
    token = _share(a, nid)
    secret = user_paths(a["id"]).tags_path
    secret.write_text('{"CANARY-TAG": {}}', encoding="utf-8")

    for evil in ["..%2F..%2Ftags.json", "%2e%2e%2f%2e%2e%2ftags.json", "..%5C..%5Ctags.json"]:
        r = client.get(f"/s/{token}/assets/{evil}")
        assert r.status_code in (400, 404), evil
        assert "CANARY-TAG" not in r.text, evil


# ── 使用者隔離 ────────────────────────────────────────────────────

def test_cannot_share_or_revoke_someone_elses_note(register_user):
    """paths 來自 session cookie 而不是 URL,所以 B 打 A 的 nid 只會在自己的
    庫裡找不到那筆 → 404。天然隔離,但要有測試釘住。"""
    _enable_public_share()
    a, b = register_user(), register_user()
    nid = _make_note(a, "termN")

    assert b["client"].post(f"/api/notes/{nid}/share").status_code == 404
    assert b["client"].get(f"/api/notes/{nid}/share").status_code == 404
    # A 的連結沒有被 B 動到
    assert a["client"].get(f"/api/notes/{nid}/share").json()["token"] is None


def test_share_endpoints_require_login(client):
    assert client.get("/api/notes/abc/share").status_code == 401
    assert client.post("/api/notes/abc/share").status_code == 401
    assert client.delete("/api/notes/abc/share").status_code == 401
    assert client.get("/api/shares").status_code == 401
    assert client.delete("/api/shares").status_code == 401


# ── 48 小時自動失效與一鍵撤銷 ────────────────────────────────────

def _backdate_shares(u, hours: float) -> None:
    """把這個使用者**目前所有**分享登記的 created 往回撥(照 test_backup 的慣例:
    直接改磁碟上的時間戳記,不 monkeypatch time)。"""
    data = share_links.load_shares(u["paths"])
    for meta in data.values():
        meta["created"] = time.time() - hours * 3600
    share_links.save_shares(u["paths"], data)


def test_expired_link_is_dead_but_a_recent_one_is_not(register_user, client):
    """TTL 是真的失效,不只是提示文字;到期前一小時仍然要開得起來。
    釘住 find_by_note 也要過濾過期:擁有者查狀態不經 resolve_nonce,漏了這支
    就會「公開端點 404」與「面板顯示還有效」兩個答案並存。"""
    _enable_public_share()
    a = register_user()
    nid = _make_note(a, "termTTL")
    token = _share(a, nid)

    _backdate_shares(a, SHARE_LINK_TTL_HOURS - 1)
    assert client.get(f"/api/s/{token}").status_code == 200

    _backdate_shares(a, SHARE_LINK_TTL_HOURS + 1)
    assert client.get(f"/api/s/{token}").status_code == 404
    # 擁有者面板同步歸零
    assert a["client"].get(f"/api/notes/{nid}/share").json()["token"] is None


def test_share_stats_and_revoke_all_count_only_alive_links(register_user, client):
    """GET /api/shares 只計有效並順手清過期;DELETE /api/shares 殺光有效連結,
    revoked 不計本來就打不開的(過期)那些——數字要跟使用者的認知對得上。"""
    _enable_public_share()
    a = register_user()
    n1, n2, n3 = _make_note(a, "termS1"), _make_note(a, "termS2"), _make_note(a, "termS3")
    _share(a, n1)
    _backdate_shares(a, SHARE_LINK_TTL_HOURS + 1)   # n1 過期

    body = a["client"].get("/api/shares").json()
    assert body["count"] == 0
    assert body["ttl_hours"] == SHARE_LINK_TTL_HOURS
    # 「列出時順手清理」:過期登記已真的從檔案消失
    assert share_links.load_shares(a["paths"]) == {}

    _share(a, n1)
    _backdate_shares(a, SHARE_LINK_TTL_HOURS + 1)   # n1 又過期(這次檔案裡還在)
    t2, t3 = _share(a, n2), _share(a, n3)
    r = a["client"].delete("/api/shares")
    assert r.status_code == 200
    assert r.json()["revoked"] == 2                  # 過期那條不算「撤銷了它」
    assert client.get(f"/api/s/{t2}").status_code == 404
    assert client.get(f"/api/s/{t3}").status_code == 404
    assert a["client"].get("/api/shares").json()["count"] == 0


def test_revoke_all_is_per_user(register_user, client):
    _enable_public_share()
    a, b = register_user(), register_user()
    nid = _make_note(a, "termR4")
    token = _share(a, nid)

    assert b["client"].get("/api/shares").json()["count"] == 0
    b["client"].delete("/api/shares")
    assert client.get(f"/api/s/{token}").status_code == 200  # A 的連結還活著

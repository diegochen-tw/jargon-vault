"""app/plugin_catalog.py + 外掛封裝相關的 HTTP 端點。

重點:manifest 驗證各種壞法都被拒且理由可讀(parametrize)、官方/站台合併規則
(官方贏)、多語 fallback、停用三態(含 article-note gating 與樣板 enabled 連動)、
load_plugins 對「不在型錄的 stored entry」的保留政策(round-trip 不丟)、
admin 上傳(zip slip / 413 / 升級 / 官方衝突)、資產端點三關、備份來回。

⚠ SITE_PLUGINS_DIR 是 session 共用臨時目錄下的**站台級**目錄,型錄快取也是
行程層級——每個測試結束都要清掉自己放進去的封裝並 refresh,否則污染別的測試
(_clean_site_plugins autouse fixture)。
"""
import io
import json
import shutil
import zipfile

import pytest

from app import backup, plugin_catalog
from app.config import ARTICLE_PLUGIN_ID, SITE_PLUGINS_DIR
from app.plugins import is_active, load_plugins, save_plugins
from app.templates import load_templates, save_templates


@pytest.fixture(autouse=True)
def _clean_site_plugins():
    yield
    if SITE_PLUGINS_DIR.exists():
        shutil.rmtree(SITE_PLUGINS_DIR, ignore_errors=True)
    SITE_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    plugin_catalog.refresh_catalog()


def _manifest(pid="test-pack", **over):
    m = {
        "manifest_version": 1, "id": pid, "version": "1.0.0",
        "category": "field-template",
        "name": {"en": "Test Pack", "zh-Hant": "測試包"},
        "description": {"en": "desc"},
        "template": {"name": "Test Pack", "ai_input_mode": "name", "ai_prompt": "",
                     "fields": [{"key": "note_x", "label": "X", "placeholder": ""}]},
    }
    m.update(over)
    return m


def _write_pkg(root, manifest, assets=None):
    d = root / manifest.get("id", "broken")
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    for fn, content in (assets or {}).items():
        p = d / "assets" / fn
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(content)
    return d


def _zip_bytes(manifest, assets=None, wrap=False, extra_member=None):
    buf = io.BytesIO()
    placeholder = None
    if extra_member:
        # ⚠ 新版 zipfile(3.14)在**寫入端**就把成員名裡的反斜線正規化成 "/",
        # 連改 ZipInfo.filename 都攔——正常寫法根本做不出惡意成員名,測試就
        # 打不到那條防線。所以先用同長度的佔位名稱寫入,再對 zip 位元組把
        # 佔位名稱補丁成惡意名稱(local header 與 central directory 各一份,
        # replace 會一次換掉),做出「惡意工具會做出來的」原始 ZIP。
        name_bytes = extra_member.encode("utf-8")
        placeholder = b"Z" * len(name_bytes)
    with zipfile.ZipFile(buf, "w") as z:
        prefix = f"{manifest['id']}/" if wrap else ""
        z.writestr(prefix + "plugin.json", json.dumps(manifest, ensure_ascii=False))
        for fn, content in (assets or {}).items():
            z.writestr(prefix + "assets/" + fn, content)
        if placeholder:
            z.writestr(placeholder.decode("ascii"), b"x")
    data = buf.getvalue()
    if placeholder:
        assert placeholder in data
        data = data.replace(placeholder, extra_member.encode("utf-8"))
    return data


GIF = b"GIF89a" + b"\x00" * 30


# ── manifest 驗證(單元,tmp_path) ──────────────────────────────────

@pytest.mark.parametrize("mutate, reason_part", [
    (lambda m: m.update(id="Bad_ID"), "id"),
    (lambda m: m.update(manifest_version=2), "manifest_version"),
    (lambda m: m.update(category="widget"), "category"),
    (lambda m: m.update(category="ai-tool", template=None), "主程式"),   # ai-tool id 不在 CORE_IMPL_IDS
    (lambda m: m.update(name={"zh-Hant": "沒有英文"}), "en"),
    (lambda m: m.update(version=""), "version"),
    (lambda m: m["template"].update(fields=[{"key": "Bad Key"}]), "不合法"),
    (lambda m: m["template"].update(fields=[{"key": "name"}]), "保留字"),
    (lambda m: m["template"].update(fields=[]), "fields"),
    (lambda m: m.update(images=["nope.png"]), "不存在"),
    (lambda m: m.update(images=["evil.exe"]), "不合法"),
    (lambda m: m.update(enhances="Bad!"), "enhances"),
    # manifest 是第三方輸入:一段長字串當 icon 會把整個樣板下拉撐爛
    (lambda m: m["template"].update(icon="這是一整句話不是圖示"), "icon"),
])
def test_bad_manifests_are_rejected_with_readable_reason(tmp_path, mutate, reason_part):
    m = _manifest()
    mutate(m)
    d = _write_pkg(tmp_path, m)
    # 目錄名以 manifest 裡的 id 建,id 本身壞掉時目錄名也一樣,錯誤會落在 id 那一關
    with pytest.raises(plugin_catalog.PluginPackageError) as ei:
        plugin_catalog.load_package(d, "site")
    assert reason_part in str(ei.value)


def test_load_package_normalizes_template_and_requires_matching_dir(tmp_path):
    """樣板 id 強制蓋成外掛 id(manifest 亂寫也沒用)、builtin 一律 False;
    icon 的真相在封裝 manifest(沒帶就是空字串,前端自己 fallback);
    目錄名與 manifest id 不一致整包拒絕。"""
    m = _manifest()
    m["template"]["id"] = "something-else"
    m["template"]["icon"] = "🧪"
    entry = plugin_catalog.load_package(_write_pkg(tmp_path, m), "site")
    assert entry["template"]["id"] == "test-pack"
    assert entry["template"]["builtin"] is False
    assert entry["template"]["icon"] == "🧪"

    plain = plugin_catalog.load_package(_write_pkg(tmp_path, _manifest(pid="no-icon")), "site")
    assert plain["template"]["icon"] == ""

    renamed = tmp_path / "other-name"
    (tmp_path / "test-pack").rename(renamed)
    with pytest.raises(plugin_catalog.PluginPackageError) as ei:
        plugin_catalog.load_package(renamed, "site")
    assert "目錄名" in str(ei.value)


# ── 合併規則:官方先掃、站台後掃、官方贏 ────────────────────────────

def test_official_wins_id_conflict_and_new_site_id_joins_catalog():
    _write_pkg(SITE_PLUGINS_DIR, _manifest(pid="mba-term"))
    _write_pkg(SITE_PLUGINS_DIR, _manifest(pid="site-pack"))
    cat, errors = plugin_catalog.scan_catalog()
    assert cat["mba-term"]["source"] == "official"  # 官方那份,不是站台的
    assert any("mba-term" in e["reason"] for e in errors)
    assert cat["site-pack"]["source"] == "site"            # 新 id 正常加入
    assert all("site-pack" not in e["dir"] for e in errors)


def test_broken_site_package_never_breaks_the_catalog():
    from app.config import OFFICIAL_PLUGINS_DIR
    d = SITE_PLUGINS_DIR / "broken-pack"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text("{not json", encoding="utf-8")
    cat, errors = plugin_catalog.scan_catalog()
    assert "broken-pack" not in cat
    # 官方封裝照常一個不少(拿 repo 目錄實數對照,不寫死數字)
    official_dirs = {p.name for p in OFFICIAL_PLUGINS_DIR.iterdir() if p.is_dir()}
    assert official_dirs <= set(cat)
    assert any("broken-pack" in e["dir"] for e in errors)


# ── load_plugins 的保留政策(round-trip 不丟) ───────────────────────

def test_stored_entry_not_in_catalog_survives_load_save_roundtrip(paths):
    plugins = load_plugins(paths)
    plugins["ghost-pack"] = {"installed": True, "config": {"ai_prompt": "留著"}}
    # 舊版 plugins.json 沒有 enabled 鍵 → 缺鍵視為開啟,零遷移
    plugins[ARTICLE_PLUGIN_ID] = {"installed": True, "config": {}}
    save_plugins(paths, plugins)

    reloaded = load_plugins(paths)   # 型錄裡沒有 ghost-pack
    save_plugins(paths, reloaded)    # 舊版會在這裡把它洗掉

    raw = json.loads(paths.plugins_path.read_text(encoding="utf-8"))
    assert raw["ghost-pack"]["config"]["ai_prompt"] == "留著"
    assert is_active(paths, ARTICLE_PLUGIN_ID)


# ── 停用三態(HTTP) ────────────────────────────────────────────────

TPL_PID = "mba-term"


def test_disable_field_template_plugin_turns_template_off_and_back(register_user):
    u = register_user()
    c = u["client"]
    # 未安裝就切 enabled → 400
    assert c.put(f"/api/plugins/{TPL_PID}/enabled",
                 json={"enabled": False}).status_code == 400
    c.post(f"/api/plugins/{TPL_PID}/install")

    # 使用者改過的樣板,停用/啟用都不能動到修改
    templates = load_templates(u["paths"])
    tpl = next(t for t in templates if t["id"] == TPL_PID)
    tpl["name"] = "我的攝影筆記"
    save_templates(u["paths"], templates)

    r = c.put(f"/api/plugins/{TPL_PID}/enabled", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    tpl = next(t for t in load_templates(u["paths"]) if t["id"] == TPL_PID)
    assert tpl["enabled"] is False
    assert tpl["name"] == "我的攝影筆記"

    c.put(f"/api/plugins/{TPL_PID}/enabled", json={"enabled": True})
    tpl = next(t for t in load_templates(u["paths"]) if t["id"] == TPL_PID)
    assert tpl["enabled"] is not False
    assert tpl["name"] == "我的攝影筆記"


def test_disabled_article_plugin_blocks_article_note(register_user, monkeypatch):
    """停用要擋在**端點**,不能只藏前端入口。AI 已啟用、外掛已安裝但停用 → 400。"""
    from app.ai_settings import save_ai_settings
    from app.routers import ai as ai_router

    async def fake(settings, system_prompt, user_content):
        return {"name": "X", "description": "", "fields": {}, "keywords": []}
    monkeypatch.setattr(ai_router, "_chat_json", fake)

    u = register_user()
    c = u["client"]
    save_ai_settings({"enabled": True, "base_url": "http://x", "model": "m"})
    c.post(f"/api/plugins/{ARTICLE_PLUGIN_ID}/install")
    assert c.post("/api/ai/article-note", json={"keyword": "X", "article": "文"}).status_code == 200

    c.put(f"/api/plugins/{ARTICLE_PLUGIN_ID}/enabled", json={"enabled": False})
    r = c.post("/api/ai/article-note", json={"keyword": "X", "article": "文"})
    assert r.status_code == 400 and "外掛" in r.json()["detail"]

    c.put(f"/api/plugins/{ARTICLE_PLUGIN_ID}/enabled", json={"enabled": True})
    assert c.post("/api/ai/article-note", json={"keyword": "X", "article": "文"}).status_code == 200


def test_template_enhancement_cannot_be_installed(register_user):
    _write_pkg(SITE_PLUGINS_DIR, {
        "manifest_version": 1, "id": "pron-pack", "version": "0.1.0",
        "category": "template-enhancement", "enhances": "english-word",
        "name": {"en": "AI Pronunciation"},
    })
    u = register_user()
    c = u["client"]
    plugins = {p["id"]: p for p in c.get("/api/plugins").json()["plugins"]}
    assert plugins["pron-pack"]["enhances"] == "english-word"
    assert c.post("/api/plugins/pron-pack/install").status_code == 400
    assert c.put("/api/plugins/pron-pack/enabled", json={"enabled": True}).status_code == 400


# ── 清單與詳情(多語)+ scan_errors 只給 admin ───────────────────────

def test_list_localizes_and_scan_errors_only_for_admin(register_user, monkeypatch):
    # 多語查值的單元行為:缺語言 fallback en
    entry = {"name": {"en": "Fallback", "ja": "日本語"}}
    assert plugin_catalog.localized(entry, "name", "ja") == "日本語"
    assert plugin_catalog.localized(entry, "name", "fr") == "Fallback"
    assert plugin_catalog.intro_localized({"description": {"en": "d"}}, "ko") == "d"

    d = SITE_PLUGINS_DIR / "broken-pack"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text("{not json", encoding="utf-8")

    u = register_user()  # 共用 DATA_DIR:不是全站第一位使用者,不是 admin
    resp = u["client"].get("/api/plugins?lang=zh-Hant").json()
    byid = {p["id"]: p for p in resp["plugins"]}
    assert byid[TPL_PID]["name"] == "MBA 管理術語"
    assert "scan_errors" not in resp  # 壞封裝清單只給 admin 看

    resp = u["client"].get("/api/plugins?lang=nope").json()  # 未知語言退回 en
    assert {p["id"]: p for p in resp["plugins"]}[TPL_PID]["name"] == "MBA / Business Terms"

    monkeypatch.setenv("ADMIN_EMAILS", u["email"])
    resp = u["client"].get("/api/plugins").json()
    assert any("broken-pack" in e["dir"] for e in resp["scan_errors"])


# ── 資產端點三關 + 詳細頁 ──────────────────────────────────────────

def test_asset_endpoint_gates_and_detail_returns_intro(register_user, client):
    _write_pkg(SITE_PLUGINS_DIR, _manifest(pid="site-pack", images=["demo.gif"],
                                           intro={"en": "line1\nline2"}),
               assets={"demo.gif": GIF, "secret.png": b"png"})
    plugin_catalog.refresh_catalog()

    assert client.get("/api/plugins/site-pack/assets/demo.gif").status_code == 401  # 無 cookie

    u = register_user()
    c = u["client"]
    d = c.get("/api/plugins/site-pack").json()
    assert d["intro"] == "line1\nline2"
    assert d["images"] == ["demo.gif"]

    r = c.get("/api/plugins/site-pack/assets/demo.gif")
    assert r.status_code == 200 and r.content.startswith(b"GIF89a")
    # 檔案存在但不在 manifest images 名單 → 404(白名單,不是「檔案在就給」)
    assert c.get("/api/plugins/site-pack/assets/secret.png").status_code == 404
    assert c.get("/api/plugins/site-pack/assets/..%2Fplugin.json").status_code in (400, 404)
    assert c.get("/api/plugins/nope/assets/demo.gif").status_code == 404


# ── admin 上傳/移除 ────────────────────────────────────────────────

def _upload(c, content, name="p.zip"):
    return c.post("/api/admin/plugins/upload", files={"file": (name, content, "application/zip")})


def test_upload_requires_admin(register_user):
    u = register_user()
    assert _upload(u["client"], _zip_bytes(_manifest())).status_code == 403
    assert u["client"].delete("/api/admin/plugins/test-pack").status_code == 403


def test_upload_both_zip_layouts_and_same_id_is_upgrade(register_user, monkeypatch):
    u = register_user()
    monkeypatch.setenv("ADMIN_EMAILS", u["email"])
    c = u["client"]

    r = _upload(c, _zip_bytes(_manifest(pid="flat-pack", version="1.0.0")))
    assert r.status_code == 200 and r.json()["source"] == "site"
    assert _upload(c, _zip_bytes(_manifest(pid="wrapped-pack"), wrap=True)).status_code == 200

    # 同 id 再上傳 = 升級
    assert _upload(c, _zip_bytes(_manifest(pid="flat-pack", version="2.0.0"))).status_code == 200

    byid = {p["id"]: p for p in c.get("/api/plugins").json()["plugins"]}
    assert "wrapped-pack" in byid
    assert byid["flat-pack"]["version"] == "2.0.0"


# ⚠ 反斜線成員(a\b.txt)刻意不在清單裡:Python 3.14 的 zipfile 連**讀取端**都會
# 把 "\" 正規化成 "/",惡意成員名經過 stdlib API 就變成無害的相對路徑,測試根本
# 觀察不到原始名稱。程式裡的 `"\\" in name` 檢查仍保留——那是給舊版 Python 的保險。
@pytest.mark.parametrize("bad", [
    "../evil.txt", "/abs.txt", "c:evil.txt", "a/../../b.txt", "./x.txt",
])
def test_upload_zip_slip_variants_all_rejected(register_user, monkeypatch, bad):
    u = register_user()
    monkeypatch.setenv("ADMIN_EMAILS", u["email"])
    r = _upload(u["client"], _zip_bytes(_manifest(), extra_member=bad))
    assert r.status_code == 400


def test_upload_rejects_oversize_garbage_and_official_id(register_user, monkeypatch):
    from app.config import MAX_UPLOAD_BYTES
    u = register_user()
    monkeypatch.setenv("ADMIN_EMAILS", u["email"])
    c = u["client"]
    assert _upload(c, b"\x00" * (MAX_UPLOAD_BYTES + 1)).status_code == 413
    assert _upload(c, b"not a zip at all").status_code == 400
    # 撞官方 id 一律拒絕(否則站台封裝可以頂替官方封裝)
    r = _upload(c, _zip_bytes(_manifest(pid=TPL_PID)))
    assert r.status_code == 400 and "官方" in r.json()["detail"]


def test_remove_site_package_keeps_user_state_and_official_is_protected(register_user, monkeypatch):
    """移除封裝只動型錄不動 plugins.json:重新上傳同 id,安裝狀態原樣復活。
    官方封裝則不可移除。"""
    u = register_user()
    monkeypatch.setenv("ADMIN_EMAILS", u["email"])
    c = u["client"]
    assert _upload(c, _zip_bytes(_manifest(pid="keep-pack"))).status_code == 200
    c.post("/api/plugins/keep-pack/install")

    assert c.delete("/api/admin/plugins/keep-pack").status_code == 200
    assert "keep-pack" not in [p["id"] for p in c.get("/api/plugins").json()["plugins"]]

    assert _upload(c, _zip_bytes(_manifest(pid="keep-pack"))).status_code == 200
    byid = {p["id"]: p for p in c.get("/api/plugins").json()["plugins"]}
    assert byid["keep-pack"]["installed"] is True

    r = c.delete(f"/api/admin/plugins/{TPL_PID}")
    assert r.status_code == 400 and "官方" in r.json()["detail"]


# ── 備份來回:站台封裝是站台級真相,備得到也還得回來 ─────────────────

def test_site_packages_survive_backup_and_restore():
    _write_pkg(SITE_PLUGINS_DIR, _manifest(pid="backup-pack"), assets={})
    zip_path = backup.create_backup()
    try:
        shutil.rmtree(SITE_PLUGINS_DIR / "backup-pack")
        backup.restore_backup(zip_path)
        assert (SITE_PLUGINS_DIR / "backup-pack" / "plugin.json").is_file()
        plugin_catalog.refresh_catalog()
        assert "backup-pack" in plugin_catalog.catalog()
    finally:
        zip_path.unlink(missing_ok=True)

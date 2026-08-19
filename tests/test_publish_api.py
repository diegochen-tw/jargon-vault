"""
公開筆記快照(app/publish.py + routers/publish.py + routers/published.py)。

這是繼單筆分享之後第二條「沒有帳號也讀得到內容」的路徑,測試重心同樣是**邊界**:
站台預設關閉、總開關殺全部讀取、pid 不可枚舉、快照凍結(改原稿不影響公開頁)、
撤銷立刻 404、export.zip 的脫敏(最重要的一支:
`test_publication_never_contains_progress_tags_or_id`——notes.json 走投影天然
乾淨,export.zip 的 tags/marked/srs_*/history 是**另外 pop 的**,漏一鍵就把
內部標籤或個人狀態送出登入牆)。
"""
import io
import json
import shutil
import zipfile

import pytest

from app import site_settings as ss
from app.config import PUBLISHED_DIR
from app.users import set_user_admin


@pytest.fixture(autouse=True)
def _clean_site_state():
    """site_settings 與 published/ 都是站台級共用狀態,前後各清一次。"""
    ss.SITE_SETTINGS_PATH.unlink(missing_ok=True)
    if PUBLISHED_DIR.exists():
        shutil.rmtree(PUBLISHED_DIR, ignore_errors=True)
    yield
    ss.SITE_SETTINGS_PATH.unlink(missing_ok=True)
    if PUBLISHED_DIR.exists():
        shutil.rmtree(PUBLISHED_DIR, ignore_errors=True)


def _enable_notebook(enabled: bool = True) -> None:
    s = ss.load_site_settings()
    s["public_notebook_enabled"] = enabled
    ss.save_site_settings(s)


def _make_note(u, name, description="", tags=None):
    r = u["client"].post("/api/notes", json={
        "name": name, "description": description, "tags": tags or [],
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _attach_image(u, nid, filename="pic.png"):
    r = u["client"].post(f"/api/notes/{nid}/attachments",
                         files={"file": (filename, b"\x89PNG-fake-bytes", "image/png")})
    assert r.status_code == 200, r.text
    return r.json()["path"]


def _publish(u, **kw):
    r = u["client"].post("/api/publish", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


# ── 站台開關 ──────────────────────────────────────────────────────

def test_notebook_is_off_by_default(register_user):
    """預設關閉是刻意的:這個功能會把內容送出登入牆之外,預設值必須保守。"""
    ss.ensure_site_settings()
    assert ss.public_notebook_enabled() is False
    a = register_user()
    _make_note(a, "termA")
    assert a["client"].post("/api/publish", json={}).status_code == 403


def test_disabled_flag_kills_all_published_reads(register_user, client):
    """★ 總開關要是真的總開關:API/資產/下載檔全部 404(HTML 殼照樣 200,
    不用狀態碼洩漏 pid 存在);重新開啟即恢復(快照沒被清掉);
    開關關掉時擁有者仍能撤銷自己的快照。"""
    _enable_notebook()
    a = register_user()
    nid = _make_note(a, "termB", description="說明")
    apath = _attach_image(a, nid)
    pub = _publish(a)
    pid = pub["pid"]
    fname = apath.rsplit("/", 1)[-1]

    assert client.get(f"/api/p/{pid}").status_code == 200
    assert client.get(f"/p/{pid}/export.zip").status_code == 200
    assert client.get(f"/p/{pid}/assets/{nid}/{fname}").status_code == 200

    _enable_notebook(False)
    assert client.get(f"/api/p/{pid}").status_code == 404
    assert client.get(f"/p/{pid}/export.zip").status_code == 404
    assert client.get(f"/p/{pid}/assets/{nid}/{fname}").status_code == 404
    assert client.get(f"/p/{pid}").status_code == 200   # HTML 殼一律 200

    _enable_notebook()
    assert client.get(f"/api/p/{pid}").status_code == 200

    _enable_notebook(False)
    assert a["client"].delete(f"/api/publish/{pid}").status_code == 200


def test_pid_not_enumerable(register_user, client):
    """★ 不存在/不合法/別人的 pid 一律同一句 404——區分開來就是把登記簿內部
    狀態洩漏給拿著亂猜 pid 的人;非 owner 的撤銷同理 404 不是 403。"""
    _enable_notebook()
    a, b = register_user(), register_user()
    _make_note(a, "termC")
    pid = _publish(a)["pid"]

    r_missing = client.get("/api/p/doesnotexist")
    r_bad = client.get("/api/p/%2e%2e")
    assert r_missing.status_code == 404
    assert r_bad.status_code == 404
    assert r_missing.json() == r_bad.json()

    # 非 owner:讀得到(公開的)但撤銷不了,且拿到的失敗跟「不存在」長一樣
    assert b["client"].delete(f"/api/publish/{pid}").status_code == 404
    assert client.delete(f"/api/publish/{pid}").status_code == 401
    # owner 自己當然可以
    assert a["client"].delete(f"/api/publish/{pid}").status_code == 200


# ── 脫敏(守門)────────────────────────────────────────────────────

def test_publication_never_contains_progress_tags_or_id(register_user, client):
    """★ 這檔最重要的一支:notes.json 投影與 export.zip 都不得含內部標籤與
    個人狀態。兩邊**分開斷言**——notes.json 走 project_note 天然乾淨,
    export.zip 的脫敏是獨立的 pop,漏一鍵只有這裡抓得到。"""
    _enable_notebook()
    a = register_user()
    nid = _make_note(a, "SFC", description="說明文字", tags=["內部代號", "客戶A"])
    a["client"].put(f"/api/notes/{nid}/mark", json={"marked": True})
    a["client"].post(f"/api/srs/{nid}/review", json={"remembered": True})
    pid = _publish(a)["pid"]

    # 公開頁投影:id 刻意保留(列表頁的 key),tags/marked/srs/history 不得出現
    notes = client.get(f"/api/p/{pid}").json()["notes"]
    assert len(notes) == 1
    for bad in ("tags", "marked", "srs_box", "srs_due", "history", "template"):
        assert bad not in notes[0], bad

    # export.zip:v3 形狀,但 tags 必須是空的、個人狀態與歷史鍵不得存在
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/p/{pid}/export.zip").content))
    payload = json.loads(z.read("notes.json"))
    assert payload["version"] == 3
    assert payload["tag_groups"] == {}
    for n in payload["notes"]:
        assert n["tags"] == []
        for bad in ("marked", "srs_box", "srs_due", "history"):
            assert bad not in n, bad
    # 樣板帶得走(欄位標題要還原得回來),但 ai_prompt 一律清空
    assert all(t.get("ai_prompt") == "" for t in payload["templates"])


def test_export_zip_reimports_into_another_account(register_user, client):
    """★ round-trip:下載 export.zip → 另一個帳號匯入 → 名詞與資產都齊。
    這就是「讀者把公開筆記接回自己庫維護」的完整動線。"""
    _enable_notebook()
    a, b = register_user(), register_user()
    nid = _make_note(a, "MES", description="製造執行系統")
    _attach_image(a, nid)
    pid = _publish(a)["pid"]

    blob = client.get(f"/p/{pid}/export.zip").content
    r = b["client"].post("/api/import",
                         files={"file": ("pub.zip", blob, "application/zip")})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["imported"] == 1 and d["assets"] == 1 and d["errors"] == []
    got = b["client"].get(f"/api/notes/{nid}").json()
    assert got["name"] == "MES"


# ── 凍結、覆蓋與撤銷 ───────────────────────────────────────────────

def test_snapshot_frozen_after_source_edit(register_user, client):
    """★ 快照是凍結複本:發佈後改原稿,公開頁**不變**——這正是它跟
    「即時視圖」的本質差別,也是公開面能純檔案讀取的前提。"""
    _enable_notebook()
    a = register_user()
    nid = _make_note(a, "PMFM", description="第一版")
    pid = _publish(a)["pid"]

    note = a["client"].get(f"/api/notes/{nid}").json()
    r = a["client"].put(f"/api/notes/{nid}", json={
        "name": "PMFM", "description": "改過的第二版", "template": note["template"],
        "fields": note["fields"], "tags": note["tags"], "attachments": note["attachments"],
    })
    assert r.status_code == 200

    pub_desc = client.get(f"/api/p/{pid}").json()["notes"][0]["description"]
    assert pub_desc == "第一版"


def test_republish_same_pid_overwrites(register_user, client):
    """重新發佈 = 同 pid 覆蓋,網址不變、內容換新;pid 不屬於自己時 404。"""
    _enable_notebook()
    a, b = register_user(), register_user()
    nid = _make_note(a, "PDA", description="第一版")
    pid = _publish(a)["pid"]

    note = a["client"].get(f"/api/notes/{nid}").json()
    a["client"].put(f"/api/notes/{nid}", json={
        "name": "PDA", "description": "第二版", "template": note["template"],
        "fields": note["fields"], "tags": note["tags"], "attachments": note["attachments"],
    })
    again = _publish(a, pid=pid)
    assert again["pid"] == pid
    assert client.get(f"/api/p/{pid}").json()["notes"][0]["description"] == "第二版"

    # 別人不能拿我的 pid 重新發佈(404,不洩漏它存在)
    _make_note(b, "x")
    assert b["client"].post("/api/publish", json={"pid": pid}).status_code == 404


def test_revoke_removes_directory(register_user, client):
    _enable_notebook()
    a = register_user()
    _make_note(a, "termD")
    pid = _publish(a)["pid"]
    assert (PUBLISHED_DIR / pid).is_dir()
    assert a["client"].delete(f"/api/publish/{pid}").status_code == 200
    assert not (PUBLISHED_DIR / pid).exists()
    assert client.get(f"/api/p/{pid}").status_code == 404


def test_admin_can_delete_any_publication(register_user, client):
    """快照存在使用者目錄之外、帳號刪除後仍在——孤兒快照只有 admin 清得掉。"""
    _enable_notebook()
    a, boss = register_user(), register_user()
    set_user_admin(boss["id"], True)
    _make_note(a, "termE")
    pid = _publish(a)["pid"]

    listed = boss["client"].get("/api/admin/published").json()["publications"]
    assert any(m["pid"] == pid for m in listed)
    assert boss["client"].delete(f"/api/admin/published/{pid}").status_code == 200
    assert client.get(f"/api/p/{pid}").status_code == 404


# ── 範圍與資產防線 ─────────────────────────────────────────────────

def test_scope_tags_filters_publication(register_user, client):
    """tags OR 聯集(語意同 GET /api/export);空集合 400。"""
    _enable_notebook()
    a = register_user()
    _make_note(a, "in-scope", tags=["發佈"])
    _make_note(a, "out-of-scope", tags=["私人"])
    pid = _publish(a, tags="發佈")["pid"]
    names = [n["name"] for n in client.get(f"/api/p/{pid}").json()["notes"]]
    assert names == ["in-scope"]
    assert a["client"].post("/api/publish", json={"tags": "沒有這個標籤"}).status_code == 400


def test_asset_filename_traversal_rejected(register_user, client):
    """filename 釘死成單一檔名;nid 過 valid_id。百分比編碼的穿越也擋
    (未編碼的 ../ 在路由層就不匹配,測那個是在測 httpx)。"""
    _enable_notebook()
    a = register_user()
    nid = _make_note(a, "termF")
    _attach_image(a, nid)
    pid = _publish(a)["pid"]

    assert client.get(f"/p/{pid}/assets/{nid}/%2e%2e%2fmanifest.json").status_code in (400, 404)
    assert client.get(f"/p/{pid}/assets/%2e%2e/whatever.png").status_code in (400, 404)
    # 快照目錄裡的非資產檔絕不能經資產端點流出
    assert client.get(f"/p/{pid}/assets/{nid}/manifest.json").status_code == 404

"""
router 層的整合式煙霧測試(smoke test):走真正的 HTTP 介面(TestClient),
用來證明各層真的有正確接起來——不是每個分支都在這裡覆蓋,細節分支的
測試留給下層各自的單元測試(test_storage/test_tags/test_search 等)。

留下來的每一支都是「每個資源各一筆證明接起來了」+ 使用者隔離 + 少數
只存在於 router 層的行為(群組→標籤的展開、分頁 has_more、路徑穿越)。
"""


def test_home_html_is_never_cached_without_revalidation(client):
    """index.html 必須 no-cache:`?v=` 版號只保護 .css/.js,保護不到 HTML 本身。
    少了這個標頭,「只改了 HTML 才會出現的 UI」會被瀏覽器的啟發式快取黏住,
    手機上又沒有硬重整可按——曾表現成語意搜尋開關在手機版「不見了」。"""
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


def test_auth_session_lifecycle_over_http(client, register_user):
    """無 session 一律 401 → 註冊(帶 cookie)→ /me 回 email 與版本 →
    錯密碼 401 → 登出後回到 401。版本斷言對 APP_VERSION 而不是寫死字串。"""
    from app.config import APP_VERSION
    assert client.get("/api/tags").status_code == 401
    assert client.get("/api/auth/me").status_code == 401

    u = register_user()
    r = u["client"].get("/api/auth/me")
    assert r.status_code == 200
    me = r.json()
    assert me["email"] == u["email"]
    assert me["version"] == APP_VERSION

    r = client.post("/api/auth/login", json={"email": u["email"], "password": "wrong-password"})
    assert r.status_code == 401

    u["client"].post("/api/auth/logout")
    assert u["client"].get("/api/auth/me").status_code == 401


def test_register_with_non_whitelisted_email_is_403(client, register_user, monkeypatch):
    # 系統裡還沒有任何使用者的那一位會豁免白名單(見 test_admin_api.py 的
    # test_first_user_registration_bypasses_whitelist),先讓別人完成第一次
    # 註冊消耗掉這個特例,才能驗證「非第一位」真的受白名單限制。
    register_user()
    monkeypatch.setenv("ALLOWED_EMAILS", "someone-else@example.com")
    r = client.post("/api/auth/register",
                     json={"email": "not-on-list@example.com", "password": "testpass123"})
    assert r.status_code == 403


def test_create_search_delete_note_roundtrip(register_user):
    c = register_user()["client"]
    r = c.post("/api/notes", json={"name": "apple", "description": "a fruit", "tags": ["fruit"]})
    assert r.status_code == 200
    nid = r.json()["id"]

    results = c.get("/api/search", params={"q": "apple"}).json()["results"]
    assert [n["name"] for n in results] == ["apple"]

    c.delete(f"/api/notes/{nid}")
    assert c.get("/api/search", params={"q": "apple"}).json()["results"] == []


def test_tag_group_search_and_rename(register_user):
    """群組→標籤的展開在 router 層做(search.py 只認 tags),所以這條必須在
    HTTP 層驗;群組改名只動 tags.json,名詞一個字都不用改——改完照樣搜得到。"""
    c = register_user()["client"]
    c.post("/api/notes", json={"name": "python", "tags": ["python"]})
    c.post("/api/notes", json={"name": "go", "tags": ["go"]})
    c.post("/api/notes", json={"name": "react", "tags": ["react"]})
    c.put("/api/tag-groups", json={"group": "程式語言", "tags": ["python", "go"]})

    names = {n["name"] for n in c.get("/api/search", params={"group": "程式語言"}).json()["results"]}
    assert names == {"python", "go"}
    # 不存在的群組:空結果 + has_more False,不是錯誤
    assert c.get("/api/search", params={"group": "不存在的群組"}).json() == {
        "results": [], "has_more": False}

    r = c.put("/api/tag-groups/程式語言", json={"name": "後端語言"})
    assert r.status_code == 200 and r.json()["affected"] == 2
    assert [g["name"] for g in c.get("/api/tags").json()["groups"]] == ["後端語言"]
    assert {n["name"] for n in c.get("/api/search", params={"group": "後端語言"}
                                     ).json()["results"]} == {"python", "go"}
    assert c.get("/api/search", params={"group": "程式語言"}).json()["results"] == []


def test_templates_enabled_flags_counts_and_toggle(register_user):
    c = register_user()["client"]
    c.post("/api/notes", json={"name": "apple", "template": "graph"})
    c.post("/api/notes", json={"name": "avocado", "template": "graph"})

    by_id = {t["id"]: t for t in c.get("/api/templates").json()["templates"]}
    # 出廠預設:只有 jargon-default 啟用
    assert by_id["jargon-default"]["enabled"] is True
    assert by_id["graph"]["enabled"] is False
    assert by_id["code-snippet"]["enabled"] is False
    # 清單帶每個樣板的名詞計數
    assert by_id["graph"]["count"] == 2
    assert by_id["code-snippet"]["count"] == 0

    # 開關來回;預設樣板不可停用
    r = c.put("/api/templates/graph/enabled", json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    by_id = {t["id"]: t for t in c.get("/api/templates").json()["templates"]}
    assert by_id["graph"]["enabled"] is True
    assert c.put("/api/templates/jargon-default/enabled",
                 json={"enabled": False}).status_code == 400


def test_template_and_field_default_flags_track_user_edits(register_user):
    """name_is_default 與欄位層級的 label_is_default/ph_is_default(顯示層 i18n 的依據):
    出廠全 true → 改一欄 label 與名稱後只有改過的變 false;自訂樣板沒有種子,
    旗標一律 False——前端因此永遠顯示儲存值,不會查 i18n。"""
    c = register_user()["client"]
    tpl = {t["id"]: t for t in c.get("/api/templates").json()["templates"]}["jargon-default"]
    assert tpl["name_is_default"] is True
    assert all(f["label_is_default"] and f["ph_is_default"] for f in tpl["fields"])

    fields = [{k: f[k] for k in ("key", "label", "placeholder", "enabled") if k in f}
              for f in tpl["fields"]]
    fields[0]["label"] = "自訂標題"
    c.put("/api/templates/jargon-default",
          json={"name": "我的術語", "fields": fields,
                "ai_input_mode": tpl["ai_input_mode"], "ai_prompt": tpl["ai_prompt"]})

    tpl = {t["id"]: t for t in c.get("/api/templates").json()["templates"]}["jargon-default"]
    assert tpl["name_is_default"] is False and tpl["name"] == "我的術語"
    assert tpl["fields"][0]["label_is_default"] is False
    assert tpl["fields"][0]["ph_is_default"] is True       # placeholder 沒動
    assert tpl["fields"][1]["label_is_default"] is True    # 其他欄不受影響

    tid = c.post("/api/templates", json={
        "name": "自訂", "fields": [{"key": "foo", "label": "自訂欄", "placeholder": "x"}],
    }).json()["id"]
    ctpl = {t["id"]: t for t in c.get("/api/templates").json()["templates"]}[tid]
    assert ctpl["name_is_default"] is False
    assert ctpl["fields"][0]["label_is_default"] is False
    assert ctpl["fields"][0]["ph_is_default"] is False


def test_reset_template_roundtrip_over_http(register_user):
    """改亂 → POST /reset → 回出廠(含旗標);自訂樣板不可 reset(旗標與端點
    兩層各自成立)、未知 id 404;外掛樣板(builtin: False 但有種子)也能恢復
    預設——不再需要解除+重裝兩步。"""
    c = register_user()["client"]
    c.put("/api/templates/jargon-default",
          json={"name": "亂改", "fields": [{"key": "junk", "label": "x", "placeholder": ""}],
                "ai_input_mode": "paste", "ai_prompt": "亂改的指示"})

    assert c.post("/api/templates/jargon-default/reset").status_code == 200
    tpl = {t["id"]: t for t in c.get("/api/templates").json()["templates"]}["jargon-default"]
    assert tpl["name_is_default"] is True          # 名稱回種子 → 前端又顯示 i18n 標準名
    assert tpl["resettable"] is True
    keys = [f["key"] for f in tpl["fields"]]
    assert "junk" not in keys and "alias" in keys  # 欄位整包回出廠
    assert all(f["label_is_default"] and f["ph_is_default"] for f in tpl["fields"])

    tid = c.post("/api/templates", json={"name": "自訂", "fields": []}).json()["id"]
    by_id = {t["id"]: t for t in c.get("/api/templates").json()["templates"]}
    assert by_id[tid]["resettable"] is False  # 前端不畫按鈕
    assert c.post(f"/api/templates/{tid}/reset").status_code == 400  # 端點也擋
    assert c.post("/api/templates/no-such-id/reset").status_code == 404

    pid = "mba-term"
    assert c.post(f"/api/plugins/{pid}/install").status_code == 200
    c.put(f"/api/templates/{pid}",
          json={"name": "亂改外掛樣板", "fields": [], "ai_input_mode": "name", "ai_prompt": ""})
    assert c.post(f"/api/templates/{pid}/reset").status_code == 200

    from app.plugins import template_seed
    seed = template_seed(pid)
    got = {t["id"]: t for t in c.get("/api/templates").json()["templates"]}[pid]
    assert got["name"] == seed["name"]
    assert [f["key"] for f in got["fields"]] == [f["key"] for f in seed["fields"]]
    assert got["resettable"] is True


def test_delete_notes_by_group_and_delete_all(register_user):
    c = register_user()["client"]
    c.post("/api/notes", json={"name": "python", "tags": ["python"]})
    c.post("/api/notes", json={"name": "go", "tags": ["go"]})
    c.post("/api/notes", json={"name": "apple", "tags": ["fruit"]})
    c.put("/api/tag-groups", json={"group": "程式語言", "tags": ["python", "go"]})

    # 未知群組:什麼都不刪——絕不可以被當成「刪全部」
    r = c.delete("/api/notes", params={"group": "no-such-group"})
    assert r.status_code == 200 and r.json()["deleted"] == 0
    assert len(c.get("/api/search").json()["results"]) == 3

    # 依群組刪:只刪該群組的名詞
    r = c.delete("/api/notes", params={"group": "程式語言"})
    assert r.status_code == 200 and r.json()["deleted"] == 2
    assert {n["name"] for n in c.get("/api/search").json()["results"]} == {"apple"}

    # 全部刪光:標籤在統計裡自然歸零而不再出現
    r = c.delete("/api/notes")
    assert r.status_code == 200 and r.json()["deleted"] == 1
    assert c.get("/api/search").json()["results"] == []
    assert c.get("/api/tags").json()["tags"] == []


def test_users_data_is_isolated_between_accounts(register_user):
    alice = register_user()
    bob = register_user()
    alice["client"].post("/api/notes", json={"name": "alice-only-note"})

    r = bob["client"].get("/api/search", params={"q": "alice-only-note"})
    assert r.json()["results"] == []


def test_upload_attachment_and_size_limit(register_user):
    from app.config import MAX_UPLOAD_BYTES
    u = register_user()
    r = u["client"].post("/api/notes/n1/attachments",
                          files={"file": ("hello.txt", b"hi there", "text/plain")})
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "hello.txt"
    assert (u["paths"].assets_dir / "n1" / d["path"].split("/")[-1]).read_bytes() == b"hi there"

    # 超過上限 413,而且寫到一半的半成品要被清掉,不能佔著空間
    big = b"x" * (MAX_UPLOAD_BYTES + 1)
    r = u["client"].post("/api/notes/n2/attachments",
                          files={"file": ("big.bin", big, "application/octet-stream")})
    assert r.status_code == 413
    assert list((u["paths"].assets_dir / "n2").glob("*")) == []


def _note_with_attachment(client, name="有附件的名詞", body=b"original-bytes"):
    """建一筆帶一個附件的名詞,回傳 (nid, 附件 dict)。"""
    nid = client.post("/api/notes", json={"name": name}).json()["id"]
    up = client.post(f"/api/notes/{nid}/attachments",
                     files={"file": ("shot.png", body, "image/png")}).json()
    client.put(f"/api/notes/{nid}", json={
        "name": name, "description": "說明",
        "attachments": [{"name": up["name"], "path": up["path"], "description": ""}]})
    return nid, up


def test_replace_asset_rewrites_path_without_touching_updated_or_history(register_user):
    # 這支端點存在的全部理由:改到路徑、不動 updated、不長歷史(重壓圖片不是
    # 內容編輯,比照 marked)。順帶驗:被取代的舊檔要刪掉、路徑穿越與未知檔名擋在門口。
    u = register_user()
    c = u["client"]
    nid, up = _note_with_attachment(c)
    before = c.get(f"/api/notes/{nid}").json()
    old_name = up["path"].split("/")[-1]
    old_file = u["paths"].notes_dir / up["path"]
    assert old_file.is_file()

    r = c.put(f"/api/notes/{nid}/assets/{old_name}",
              files={"file": ("shot.webp", b"smaller", "image/webp")})
    assert r.status_code == 200
    new_path = r.json()["path"]
    assert new_path != up["path"] and new_path.endswith(".webp")

    after = c.get(f"/api/notes/{nid}").json()
    assert [a["path"] for a in after["attachments"]] == [new_path]
    assert after["updated"] == before["updated"]
    assert len(after["history"]) == len(before["history"])
    # 顯示名沿用原本的,不要被上傳的檔名蓋掉
    assert after["attachments"][0]["name"] == up["name"]
    assert (u["paths"].notes_dir / new_path).read_bytes() == b"smaller"
    # 沒有歷史快照引用舊檔 → 被取代的實體檔一併刪除
    assert not old_file.exists()

    # 檔名帶路徑分隔 → 匹配不到路由(404),不會穿越出去;未知檔名 404
    assert c.put(f"/api/notes/{nid}/assets/..%2F..%2Fsecret.md",
                 files={"file": ("x.webp", b"x", "image/webp")}).status_code in (400, 404)
    assert c.put(f"/api/notes/{nid}/assets/nope.png",
                 files={"file": ("x.webp", b"x", "image/webp")}).status_code == 404


def test_replace_asset_keeps_old_file_when_a_history_snapshot_still_uses_it(register_user):
    # snapshot_of() 會把 attachments 深拷貝進 history,舊路徑還留在版本快照裡。
    # 這時刪掉實體檔會讓「版本回復」回復出破圖,寧可留孤兒檔。
    u = register_user()
    c = u["client"]
    nid, up = _note_with_attachment(c)
    # 再編輯一次 → 帶著這個附件的快照進 history
    c.put(f"/api/notes/{nid}", json={
        "name": "改名觸發歷史", "description": "新說明",
        "attachments": [{"name": up["name"], "path": up["path"], "description": ""}]})
    assert c.get(f"/api/notes/{nid}").json()["history"]

    old_file = u["paths"].notes_dir / up["path"]
    c.put(f"/api/notes/{nid}/assets/{up['path'].split('/')[-1]}",
          files={"file": ("shot.webp", b"smaller", "image/webp")})
    assert old_file.is_file()


def test_list_assets_across_notes_and_isolated_per_user(register_user):
    alice = register_user()
    c = alice["client"]
    nid1, up1 = _note_with_attachment(c, name="第一筆")
    nid2, up2 = _note_with_attachment(c, name="第二筆")
    got = {(i["note_id"], i["path"]) for i in c.get("/api/assets").json()["items"]}
    assert (nid1, up1["path"]) in got
    assert (nid2, up2["path"]) in got

    bob = register_user()
    assert bob["client"].get("/api/assets").json()["items"] == []


def test_bookmark_toggle_edit_preservation_and_filter(register_user):
    c = register_user()["client"]
    nid = c.post("/api/notes", json={"name": "書籤名詞", "description": "內容"}).json()["id"]
    c.post("/api/notes", json={"name": "沒標記"})
    before = c.get(f"/api/notes/{nid}").json()
    assert before["marked"] is False

    assert c.put(f"/api/notes/{nid}/mark", json={"marked": True}).status_code == 200
    after = c.get(f"/api/notes/{nid}").json()
    assert after["marked"] is True
    # 書籤不是內容編輯:不寫歷史版本、不動 updated(否則列表排序會被打亂)
    assert after["updated"] == before["updated"]
    assert after["history"] == []

    # NoteIn 刻意不含 marked,一般編輯的 payload 不帶它——api_update 的 old.update()
    # 要原樣保留書籤,否則使用者每編輯一次就掉一次標記
    c.put(f"/api/notes/{nid}", json={"name": "改過的名字", "description": "新內容"})
    got = c.get(f"/api/notes/{nid}").json()
    assert got["name"] == "改過的名字"
    assert got["marked"] is True

    # marked=1 走後端篩選,只回有標記的
    assert {n["name"] for n in c.get("/api/search").json()["results"]} == {"改過的名字", "沒標記"}
    marked = c.get("/api/search", params={"marked": 1}).json()["results"]
    assert [n["name"] for n in marked] == ["改過的名字"]

    c.put(f"/api/notes/{nid}/mark", json={"marked": False})
    assert c.get(f"/api/notes/{nid}").json()["marked"] is False


def test_search_pagination_has_more_and_offset(register_user):
    c = register_user()["client"]
    created = [c.post("/api/notes", json={"name": f"分頁名詞{i:02d}"}).json()["id"]
               for i in range(51)]

    first = c.get("/api/search").json()
    assert len(first["results"]) == 50
    assert first["has_more"] is True

    second = c.get("/api/search", params={"offset": 50}).json()
    assert len(second["results"]) == 1
    assert second["has_more"] is False

    # 兩頁合起來的 id 沒有重複、剛好是全部 51 筆
    ids = {n["id"] for n in first["results"]} | {n["id"] for n in second["results"]}
    assert len(ids) == 51

    # 剛好一頁(50 筆)時 has_more 不可誤報 True(「多要一筆」的技巧)
    c.delete(f"/api/notes/{created[0]}")
    r = c.get("/api/search").json()
    assert len(r["results"]) == 50
    assert r["has_more"] is False

    # 沒有結果時同樣 has_more False
    r = c.get("/api/search", params={"q": "沒有這個名詞"}).json()
    assert r["results"] == [] and r["has_more"] is False

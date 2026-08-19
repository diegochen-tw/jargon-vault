"""匯出/匯入裡「無法從 notes 重建」的東西有沒有真的被帶走。

三類:
  - 附件與說明欄貼的圖片(二進位檔案,只有 zip 帶得走)
  - 欄位樣板(notes 只存 template id 與 fields 值,少了樣板定義就只剩 key 當標題)
  - 個人狀態(書籤 marked 與複習進度 srs_*,住在 progress.json)
驗的都是同一件事:換一台主機匯入之後,東西真的回來了。
守門:zip slip 防護、匯出→刪除→匯入的來回、v1/v2 舊格式相容、只補不覆蓋。
"""
import io
import json
import zipfile

from app.config import DATA_DIR
from app.templates import DEFAULT_TEMPLATES


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def _export_zip(c, **params) -> tuple[bytes, zipfile.ZipFile]:
    r = c.get("/api/export", params={"format": "zip", **params})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    return r.content, zipfile.ZipFile(io.BytesIO(r.content))


def _make_note_with_image(c, name="文殊蘭", image=b"\x89PNG-fake-bytes"):
    nid = c.post("/api/notes", json={"name": name}).json()["id"]
    up = c.post(f"/api/notes/{nid}/images",
                files={"file": ("shot.png", image, "image/png")})
    assert up.status_code == 200, up.text
    path = up.json()["path"]  # assets/<nid>/<隨機檔名>.png
    # 說明欄內嵌圖片的寫法,跟前端貼上圖片後存下來的內容一致
    c.put(f"/api/notes/{nid}", json={"name": name, "description": f"![](/{path})"})
    return nid, path


def test_export_zip_bundles_notes_json_and_assets(client, register_user):
    c = register_user()["client"]
    nid, path = _make_note_with_image(c)

    _, z = _export_zip(c)
    assert "notes.json" in z.namelist()
    assert path in z.namelist()  # assets/<nid>/<檔名>

    payload = json.loads(z.read("notes.json").decode("utf-8"))
    assert payload["version"] == 3
    assert [n["id"] for n in payload["notes"]] == [nid]
    assert z.read(path) == b"\x89PNG-fake-bytes"


def test_export_zip_only_includes_assets_of_selected_notes(client, register_user):
    """依標籤選擇性匯出時,沒被選到的名詞的圖片不該被打包進去。"""
    c = register_user()["client"]
    keep, keep_path = _make_note_with_image(c, "keep")
    c.put(f"/api/notes/{keep}", json={"name": "keep", "tags": ["wanted"]})
    _, other_path = _make_note_with_image(c, "other")

    _, z = _export_zip(c, tags="wanted")
    assert keep_path in z.namelist()
    assert other_path not in z.namelist()


def test_zip_roundtrip_restores_note_and_image_file(client, register_user):
    """匯出 → 刪掉名詞與圖片 → 匯入同一份 zip,名詞與圖片實體檔都回來。"""
    user = register_user()
    c, paths = user["client"], user["paths"]
    nid, path = _make_note_with_image(c)
    backup, _ = _export_zip(c)

    c.delete(f"/api/notes/{nid}")
    c.delete(f"/api/notes/{nid}/assets")
    assert not (paths.assets_dir / nid).exists()
    assert c.get(f"/{path}").status_code == 404

    r = c.post("/api/import", files={"file": ("backup.zip", backup, "application/zip")})
    assert r.status_code == 200, r.text
    # templates:0 = 內建樣板本機都已存在,不重複加
    assert r.json() == {"imported": 1, "errors": [], "assets": 1, "templates": 0}

    # 名詞回來了,說明欄裡寫死的 /assets/... 路徑也還取得到圖片
    assert c.get(f"/api/notes/{nid}").status_code == 200
    got = c.get(f"/{path}")
    assert got.status_code == 200
    assert got.content == b"\x89PNG-fake-bytes"


def test_import_rejects_junk(client, register_user):
    c = register_user()["client"]
    # zip 沒有 notes.json → 400,錯誤訊息講得出缺什麼
    bad = _zip_bytes({"readme.txt": b"nope"})
    r = c.post("/api/import", files={"file": ("bad.zip", bad, "application/zip")})
    assert r.status_code == 400
    assert "notes.json" in r.json()["detail"]
    # 樣板匯入:csv / 壞 JSON / 沒有 templates 的 JSON 一律 400
    assert c.post("/api/import/templates",
                  files={"file": ("x.csv", b"a,b", "text/csv")}).status_code == 400
    assert c.post("/api/import/templates",
                  files={"file": ("x.json", b"not json", "application/json")}).status_code == 400
    r = c.post("/api/import/templates",
               files={"file": ("x.json", b'{"version":3}', "application/json")})
    assert r.status_code == 400 and "templates" in r.json()["detail"]


def test_import_zip_ignores_paths_outside_the_assets_layout(client, register_user):
    """ZIP 內的檔名是外部輸入:`..`、絕對路徑、未匯入的 note id 一律不落地(zip slip)。"""
    user = register_user()
    c, paths = user["client"], user["paths"]
    nid = "abc123def456"
    notes = json.dumps({"version": 2, "notes": [
        {"id": nid, "name": "ok", "template": "jargon-default",
         "fields": {}, "tags": [], "attachments": []}], "tag_groups": {}}).encode("utf-8")
    evil = _zip_bytes({
        "notes.json": notes,
        f"assets/{nid}/good.png": b"good",
        f"assets/{nid}/../../escaped.png": b"evil",   # 跳出資產目錄
        "assets/../../escaped2.png": b"evil",
        "assets/not-imported-id/x.png": b"evil",      # 不在這次匯入的名詞裡
        "../escaped3.png": b"evil",
    })
    r = c.post("/api/import", files={"file": ("evil.zip", evil, "application/zip")})
    assert r.status_code == 200, r.text
    assert r.json()["assets"] == 1  # 只有 good.png

    assert (paths.assets_dir / nid / "good.png").read_bytes() == b"good"
    for stray in ("escaped.png", "escaped2.png", "escaped3.png"):
        assert not list(DATA_DIR.rglob(stray)), f"{stray} 不該被寫出來"
    assert not (paths.assets_dir / "not-imported-id").exists()


# ── 欄位樣板(v3 起隨匯出檔一起帶走)────────────────────────────────

CUSTOM_TPL = {"name": "植物圖鑑", "ai_input_mode": "name", "ai_prompt": "描述這種植物",
              "fields": [{"key": "family", "label": "科別", "placeholder": "如 石蒜科"},
                         {"key": "bloom", "label": "花期", "placeholder": "如 夏季"}]}


def _make_custom_template(c, tpl=None):
    r = c.post("/api/templates", json=tpl or CUSTOM_TPL)
    assert r.status_code == 200, r.text
    return r.json()


def test_export_carries_field_templates_and_formats(client, register_user):
    """自訂樣板無法從 notes 重建(notes 只有 template id 與 fields 值),必須隨
    匯出檔帶走;zip 的 notes.json 與 json 匯出同一份 payload。zip 只是多一種
    格式:json/csv 的既有行為不變,未知格式 400。"""
    c = register_user()["client"]
    tpl = _make_custom_template(c)
    c.post("/api/notes", json={"name": "apple", "tags": ["fruit"]})

    r = c.get("/api/export", params={"format": "json"})
    assert r.status_code == 200
    payload = r.json()
    assert payload["version"] == 3
    exported = {t["id"]: t for t in payload["templates"]}
    assert tpl["id"] in exported
    assert exported[tpl["id"]]["fields"] == tpl["fields"]
    assert exported[tpl["id"]]["ai_prompt"] == "描述這種植物"

    _, z = _export_zip(c)
    assert tpl["id"] in {t["id"] for t in json.loads(z.read("notes.json"))["templates"]}

    assert c.get("/api/export", params={"format": "csv"}).status_code == 200
    assert c.get("/api/export", params={"format": "xml"}).status_code == 400


def test_import_restores_templates_so_fields_keep_their_labels(client, register_user):
    """換一台主機:匯入後自訂樣板與它的欄位定義都要在,名詞的 template id 才對得上。"""
    src = register_user()["client"]
    tpl = _make_custom_template(src)
    src.post("/api/notes", json={"name": "文殊蘭", "template": tpl["id"],
                                 "fields": {"family": "石蒜科", "bloom": "夏季"}})
    backup = src.get("/api/export", params={"format": "json"}).content

    dst = register_user()["client"]           # 全新的另一個使用者 = 另一台主機
    assert tpl["id"] not in {t["id"] for t in dst.get("/api/templates").json()["templates"]}

    r = dst.post("/api/import", files={"file": ("backup.json", backup, "application/json")})
    assert r.status_code == 200, r.text
    assert r.json()["templates"] == 1

    got = {t["id"]: t for t in dst.get("/api/templates").json()["templates"]}
    assert got[tpl["id"]]["name"] == "植物圖鑑"
    assert [f["label"] for f in got[tpl["id"]]["fields"]] == ["科別", "花期"]
    assert got[tpl["id"]]["ai_prompt"] == "描述這種植物"
    note = dst.get("/api/search", params={"q": "文殊蘭"}).json()["results"][0]
    assert note["template"] == tpl["id"] and note["fields"]["family"] == "石蒜科"


def test_import_never_overwrites_an_existing_template(client, register_user):
    """比照 tag_groups 的還原政策:匯入備份不該蓋掉手上已經改過的樣板(含內建樣板)。"""
    user = register_user()
    c = user["client"]
    tpl = _make_custom_template(c)
    # 本機把樣板改過名字與欄位標籤
    c.put(f"/api/templates/{tpl['id']}", json={
        "name": "我改過的名字", "ai_input_mode": "name", "ai_prompt": "本機的指示",
        "fields": [{"key": "family", "label": "本機標籤", "placeholder": ""}]})
    # 匯入一份「還是舊定義」的備份
    backup = json.dumps({"version": 3, "notes": [], "tag_groups": {},
                         "templates": [dict(CUSTOM_TPL, id=tpl["id"], builtin=False)]},
                        ensure_ascii=False).encode("utf-8")
    r = c.post("/api/import", files={"file": ("backup.json", backup, "application/json")})
    assert r.status_code == 200, r.text
    assert r.json()["templates"] == 0

    got = {t["id"]: t for t in c.get("/api/templates").json()["templates"]}
    assert got[tpl["id"]]["name"] == "我改過的名字"
    assert got[tpl["id"]]["ai_prompt"] == "本機的指示"
    assert [f["label"] for f in got[tpl["id"]]["fields"]] == ["本機標籤"]


def test_import_skips_malformed_templates_without_failing_the_whole_import(client, register_user):
    """壞掉的樣板整筆略過,名詞照樣匯入——一顆爛 key 不該讓整包備份進不來。"""
    c = register_user()["client"]
    backup = json.dumps({"version": 3, "tag_groups": {},
                         "notes": [{"id": "keepthisone", "name": "ok", "tags": [],
                                    "fields": {}, "attachments": []}],
                         "templates": [
                             {"id": "bad-key", "name": "壞的", "fields": [{"key": "9nope"}]},
                             {"id": "no-name", "name": "", "fields": []},
                             {"id": "good-one", "name": "好的",
                              "fields": [{"key": "ok_key", "label": "可以"}]},
                         ]}, ensure_ascii=False).encode("utf-8")
    r = c.post("/api/import", files={"file": ("backup.json", backup, "application/json")})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1
    assert r.json()["templates"] == 1

    ids = {t["id"] for t in c.get("/api/templates").json()["templates"]}
    assert "good-one" in ids
    assert "bad-key" not in ids and "no-name" not in ids


def test_templates_only_export_import_roundtrip(client, register_user):
    """設定 → 欄位樣板的單獨匯出/匯入:只帶樣板、不帶名詞。"""
    src = register_user()["client"]
    tpl = _make_custom_template(src)
    src.post("/api/notes", json={"name": "不該被帶走的名詞", "template": tpl["id"]})

    r = src.get("/api/export/templates")
    assert r.status_code == 200, r.text
    assert "jargon-vault-templates.json" in r.headers["content-disposition"]
    payload = r.json()
    assert payload["version"] == 3
    assert "notes" not in payload  # 樣板匯出檔不夾帶任何名詞
    assert tpl["id"] in {t["id"] for t in payload["templates"]}

    dst = register_user()["client"]
    body = r.content
    builtins = len(DEFAULT_TEMPLATES)  # 對面早就有內建樣板 → 全數略過
    got = dst.post("/api/import/templates",
                   files={"file": ("templates.json", body, "application/json")})
    assert got.status_code == 200, got.text
    # 只有自訂的那個是新增,內建的一律略過
    assert got.json() == {"added": 1, "skipped": builtins}
    tpls = {t["id"]: t for t in dst.get("/api/templates").json()["templates"]}
    assert [f["label"] for f in tpls[tpl["id"]]["fields"]] == ["科別", "花期"]
    assert dst.get("/api/search").json()["results"] == []  # 名詞一筆都沒進來

    # 同一份檔案再匯一次:全部略過,added 為 0(前端據此提示,不是沒反應)
    again = dst.post("/api/import/templates",
                     files={"file": ("templates.json", body, "application/json")})
    assert again.json() == {"added": 0, "skipped": builtins + 1}


def test_import_endpoints_accept_each_others_files(client, register_user):
    """按錯按鈕也不該卡住:樣板匯入吃整包備份與純樣板陣列(只取 templates、
    名詞不進來);反過來,整包「匯入…」也吃只有 templates 沒有 notes 的樣板檔。"""
    c = register_user()["client"]
    full = json.dumps({"version": 3, "tag_groups": {}, "notes": [
        {"id": "shouldnotload", "name": "名詞", "tags": [], "fields": {}, "attachments": []}],
        "templates": [{"id": "from-full", "name": "整包帶來的",
                       "fields": [{"key": "k1", "label": "欄位一"}]}]}).encode("utf-8")
    r = c.post("/api/import/templates", files={"file": ("backup.json", full, "application/json")})
    assert r.json() == {"added": 1, "skipped": 0}
    assert c.get("/api/search").json()["results"] == []  # 只取樣板,名詞不進來

    bare = json.dumps([{"id": "from-array", "name": "陣列帶來的",
                        "fields": [{"key": "k2", "label": "欄位二"}]}]).encode("utf-8")
    r = c.post("/api/import/templates", files={"file": ("tpls.json", bare, "application/json")})
    assert r.json() == {"added": 1, "skipped": 0}

    only = json.dumps({"version": 3, "templates": [
        {"id": "tpl-only-001", "name": "只有樣板",
         "fields": [{"key": "k", "label": "欄"}]}]}).encode("utf-8")
    r = c.post("/api/import", files={"file": ("templates.json", only, "application/json")})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 0 and r.json()["templates"] == 1

    ids = {t["id"] for t in c.get("/api/templates").json()["templates"]}
    assert {"from-full", "from-array", "tpl-only-001"} <= ids


def test_import_still_accepts_v2_and_v1_files_without_templates(client, register_user):
    """舊的匯出檔(v1 純陣列 / v2 沒有 templates key)照吃,不能因為新欄位就壞掉。"""
    c = register_user()["client"]
    v2 = json.dumps({"version": 2, "tag_groups": {"分類": ["水果"]},
                     "notes": [{"id": "v2note00000a", "name": "apple", "tags": ["水果"],
                                "fields": {}, "attachments": []}]}).encode("utf-8")
    r = c.post("/api/import", files={"file": ("old.json", v2, "application/json")})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1 and r.json()["templates"] == 0

    v1 = json.dumps([{"id": "v1note00000a", "name": "banana", "tags": []}]).encode("utf-8")
    r = c.post("/api/import", files={"file": ("older.json", v1, "application/json")})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1 and r.json()["templates"] == 0


# ── 個人狀態(marked / srs_*)──────────────────────────────────────


def test_personal_state_survives_json_export_delete_import(register_user):
    """書籤與複習進度都是「無法從別處重建」的東西,要跟著匯出檔走一趟。

    (刻意與 shares.json 相反——分享 token 綁本站網址,搬到別台
    無意義且會讓已撤銷的連結復活;書籤與複習進度搬過去完全有意義。)
    """
    c = register_user()["client"]
    marked_id = c.post("/api/notes", json={"name": "書籤名詞"}).json()["id"]
    c.put(f"/api/notes/{marked_id}/mark", json={"marked": True})
    reviewed = c.post("/api/notes", json={"name": "複習過的"}).json()["id"]
    c.post(f"/api/srs/{reviewed}/review", json={"remembered": True})
    c.post("/api/notes", json={"name": "普通名詞"})
    due_before = {n["name"]: n["srs_due"] for n in c.get("/api/search").json()["results"]}

    payload = c.get("/api/export").content
    c.delete("/api/notes")  # 全部刪掉,模擬換一台主機
    assert c.get("/api/search").json()["results"] == []

    r = c.post("/api/import", files={"file": ("export.json", payload, "application/json")})
    assert r.status_code == 200, r.text
    got = {n["name"]: n for n in c.get("/api/search").json()["results"]}
    assert got["書籤名詞"]["marked"] is True
    assert got["普通名詞"]["marked"] is False
    assert got["複習過的"]["srs_box"] == 1
    assert got["複習過的"]["srs_due"] == due_before["複習過的"]
    # 沒複習過的仍然沒有盒序,srs_due 照樣退回 updated
    assert got["普通名詞"]["srs_box"] is None


def test_csv_roundtrip_keeps_personal_state_columns_honest(register_user):
    """CSV 的布林欄位是 `True`/`False` 文字,bool("False") 是 True——直接轉會把
    整批名詞都變成已標記(見 transfer.py:_truthy);srs 空欄是空字串,要對應回
    「從沒複習過」而不是 box 0——box 0 是「答錯過、明天再問」,是完全不同的狀態。
    匯出端:CSV 要帶 marked/srs_box/srs_due 欄。"""
    c = register_user()["client"]
    csv_text = ("id,name,template,tags,description,marked,srs_box,srs_due,created,updated\n"
                "c1,未標記沒複習,jargon-default,,說明,False,,,1000,1000\n"
                "c2,已標記複習過,jargon-default,,說明,True,2,99999,1000,1000\n")
    r = c.post("/api/import", files={"file": ("in.csv", csv_text.encode("utf-8"), "text/csv")})
    assert r.status_code == 200, r.text
    got = {n["name"]: n for n in c.get("/api/search").json()["results"]}
    assert got["未標記沒複習"]["marked"] is False
    assert (got["未標記沒複習"]["srs_box"], got["未標記沒複習"]["srs_due"]) == (None, 1000)
    assert got["已標記複習過"]["marked"] is True
    assert (got["已標記複習過"]["srs_box"], got["已標記複習過"]["srs_due"]) == (2, 99999)

    text = c.get("/api/export", params={"format": "csv"}).content.decode("utf-8-sig")
    header = text.splitlines()[0].split(",")
    assert "marked" in header and "srs_box" in header and "srs_due" in header
    row_marked = next(line for line in text.splitlines()[1:] if "已標記複習過" in line)
    assert "True" in row_marked

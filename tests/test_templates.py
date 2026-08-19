"""app/templates.py:欄位樣板登記簿(<使用者目錄>/templates.json)。"""
from app.templates import (
    DEFAULT_TEMPLATES,
    enabled_fields,
    ensure_templates,
    get_template,
    load_templates,
    save_templates,
)


def test_load_defaults_and_ensure_creates_file(paths):
    got = load_templates(paths)
    assert [t["id"] for t in got] == [t["id"] for t in DEFAULT_TEMPLATES]
    assert not paths.templates_path.exists()  # load 不寫檔,只有 ensure/save 才寫

    ensure_templates(paths)
    assert paths.templates_path.exists()
    ids = {t["id"] for t in load_templates(paths)}
    # 內建只留四個:article-keywords/process-sop/english-word/plant-id 都已改成外掛,
    # passage-decoder 反過來從外掛升成內建(見 app/templates.py 檔尾的進出紀錄)
    assert ids == {"jargon-default", "passage-decoder", "graph", "code-snippet"}


def test_ensure_templates_restores_deleted_builtin_and_keeps_custom(paths):
    ensure_templates(paths)
    templates = [t for t in load_templates(paths) if t["id"] != "code-snippet"]  # 模擬誤刪
    templates.append({"id": "my-custom", "name": "自訂樣板", "builtin": False,
                      "fields": [], "ai_input_mode": "name", "ai_prompt": ""})
    save_templates(paths, templates)

    ensure_templates(paths)

    ids = {t["id"] for t in load_templates(paths)}
    assert "code-snippet" in ids   # 內建補回來了
    assert "my-custom" in ids      # 自訂樣板不受波及


def test_ensure_templates_does_not_overwrite_user_customization_of_builtin(paths):
    """回填只看 key:使用者改過的名稱/欄位標題、自己加的欄位都不能被動到,
    官方新欄位仍照補。"""
    ensure_templates(paths)
    templates = load_templates(paths)
    for t in templates:
        if t["id"] == "jargon-default":
            t["name"] = "我的自訂名稱"
            t["fields"] = [{"key": "alias", "label": "我改過的標題", "placeholder": "我的提示"},
                           {"key": "mine", "label": "我自己加的", "placeholder": ""}]
    save_templates(paths, templates)

    ensure_templates(paths)  # 再跑一次自我修復

    tpl = get_template(paths, "jargon-default")
    assert tpl["name"] == "我的自訂名稱"
    fields = {f["key"]: f for f in tpl["fields"]}
    assert fields["alias"]["label"] == "我改過的標題"
    assert fields["alias"]["placeholder"] == "我的提示"
    assert fields["mine"]["label"] == "我自己加的"
    assert "en_term" in fields  # 官方新欄位仍然補上了


def test_ensure_templates_backfills_ai_fields_and_icon_on_old_accounts(paths):
    """⚠ 既有帳號的 templates.json 是 ai_prompt/icon 出現之前寫的,ensure 不補的話
    只有新註冊的人拿得到——而既有帳號正是絕大多數使用者。"""
    templates = load_templates(paths)
    for t in templates:
        t.pop("ai_prompt", None)
        t.pop("ai_input_mode", None)
        t.pop("icon", None)
    save_templates(paths, templates)

    ensure_templates(paths)

    jargon = get_template(paths, "jargon-default")
    assert jargon["ai_prompt"]
    assert jargon["ai_input_mode"] == "name"
    assert jargon["icon"] == "📖"


def test_builtin_icons_custom_icon_fallback_and_unknown_id(paths):
    """代表 icon 是編輯器樣板下拉的辨識線索:內建樣板一個都不能漏;
    自訂樣板沒有 icon 也不能缺鍵(fallback 由前端決定,後端不塞預設圖示)。"""
    ensure_templates(paths)
    for t in load_templates(paths):
        assert t.get("icon"), t["id"]
    assert get_template(paths, "does-not-exist") is None

    save_templates(paths, [{"id": "mine", "name": "自訂", "fields": []}])
    assert load_templates(paths)[0]["icon"] == ""


def test_builtin_default_enabled_state_for_new_user(paths):
    # 新使用者:只有預設樣板啟用,其餘內建樣板一律預設停用(使用者可自行開啟),
    # 免得新建名詞的樣板下拉一開始就塞滿用不到的選項
    ensure_templates(paths)
    by_id = {t["id"]: t for t in load_templates(paths)}
    assert by_id["jargon-default"]["enabled"] is True
    for tid in ("passage-decoder", "graph", "code-snippet"):
        assert by_id[tid]["enabled"] is False, tid
    # 欄位層級:預設樣板出廠只開「別名/異名同義/一詞多義」這三個
    fields = {f["key"]: f for f in by_id["jargon-default"]["fields"]}
    for key in ("alias", "synonymy", "polysemy"):
        assert fields[key]["enabled"] is True, key
    for key in ("en_term", "domain", "source"):
        assert fields[key]["enabled"] is False, key


def test_load_templates_defaults_missing_enabled_to_true(paths):
    """舊檔沒有 enabled 鍵(樣板層與欄位層都可能缺)→ 一律視為啟用。

    沒有這條的話,種子裡新加的「預設關閉」會讓既有帳號畫面上的欄位無聲消失。"""
    save_templates(paths, [{"id": "english-word", "name": "英文單字", "builtin": True,
                            "fields": [{"key": "en_term", "label": "英文原文",
                                        "placeholder": ""}],
                            "ai_input_mode": "name", "ai_prompt": ""}])
    got = load_templates(paths)
    assert got[0]["enabled"] is True
    assert got[0]["fields"][0]["enabled"] is True


def test_enabled_fields_filters_disabled_and_keeps_order(paths):
    tpl = {"fields": [{"key": "a", "enabled": False}, {"key": "b"},
                      {"key": "c", "enabled": True}]}
    assert [f["key"] for f in enabled_fields(tpl)] == ["b", "c"]
    assert enabled_fields(None) == []


def test_ensure_templates_backfills_new_official_fields_and_is_idempotent(paths):
    """官方樣板加欄位時,既有帳號也要拿得到——否則改 DEFAULT_TEMPLATES 只有新註冊
    的人受惠。跑第二次不能又補一輪(每次啟動都會呼叫,重複附加會讓欄位無限增生)。"""
    # 模擬「舊版」帳號:jargon-default 只有最早的三個欄位
    save_templates(paths, [{
        "id": "jargon-default", "name": "Jargon (Default)", "builtin": True,
        "enabled": True, "ai_input_mode": "name", "ai_prompt": "舊的指示",
        "fields": [{"key": "alias", "label": "別名", "placeholder": ""},
                   {"key": "synonymy", "label": "Synonymy", "placeholder": ""},
                   {"key": "polysemy", "label": "Polysemy", "placeholder": ""}],
    }])

    ensure_templates(paths)

    jargon = get_template(paths, "jargon-default")
    keys = [f["key"] for f in jargon["fields"]]
    seed_keys = [f["key"] for f in DEFAULT_TEMPLATES[0]["fields"]]
    assert set(seed_keys) <= set(keys)                    # 新欄位都補進來了
    assert keys[:3] == ["alias", "synonymy", "polysemy"]  # 既有欄位順序不動
    assert jargon["ai_prompt"] == "舊的指示"               # 已填的指示不覆寫

    ensure_templates(paths)  # 冪等
    ensure_templates(paths)
    assert [f["key"] for f in get_template(paths, "jargon-default")["fields"]] == keys


def test_every_builtin_field_key_is_valid_and_unique(paths):
    """種子欄位必須通過後端存檔時的同一套驗證,否則使用者一按儲存就 400。"""
    from app.config import RESERVED_FIELD_KEYS
    from app.sanitize import clean_template_fields
    for seed in DEFAULT_TEMPLATES:
        cleaned = clean_template_fields(seed["fields"])       # 不合法會 raise
        keys = [f["key"] for f in seed["fields"]]
        assert len(keys) == len(set(keys)), seed["id"]
        assert not (set(keys) & RESERVED_FIELD_KEYS), seed["id"]
        assert len(cleaned) == len(keys)


# ── 恢復預設值(reset_template):與 ensure_templates「只補不覆蓋」相反的明確動作 ──

def _seed(tid):
    return next(t for t in DEFAULT_TEMPLATES if t["id"] == tid)


def test_reset_template_restores_factory_and_does_not_mutate_seed(paths):
    from app.templates import reset_template
    ensure_templates(paths)
    templates = load_templates(paths)
    for t in templates:
        if t["id"] == "jargon-default":  # 模擬使用者改得亂七八糟
            t["name"] = "亂改的名字"
            t["fields"] = [{"key": "junk", "label": "垃圾欄位", "placeholder": ""}]
            t["ai_prompt"] = "亂改的指示"
    save_templates(paths, templates)

    got = reset_template(paths, "jargon-default", _seed("jargon-default"))

    seed = _seed("jargon-default")
    assert got["name"] == seed["name"]
    assert [f["key"] for f in got["fields"]] == [f["key"] for f in seed["fields"]]
    assert got["ai_prompt"] == seed["ai_prompt"]
    # 落盤了,不是只改記憶體
    assert get_template(paths, "jargon-default")["name"] == seed["name"]

    # 種子必須 deepcopy 進樣板——之後使用者再編輯樣板欄位時,絕不能改到
    # DEFAULT_TEMPLATES 這個模組層常數(改到就是全站所有人的出廠定義被污染)
    got["fields"][0]["label"] = "mutated"
    assert _seed("jargon-default")["fields"][0]["label"] != "mutated"


def test_reset_template_preserves_enabled_choice_and_unknown_id_none(paths):
    """graph 種子預設停用;使用者啟用後按恢復預設,不該被關回去——
    恢復的是「定義」(名稱/欄位/AI 指示),不是使用者的啟用選擇。"""
    from app.templates import reset_template
    ensure_templates(paths)
    templates = load_templates(paths)
    for t in templates:
        if t["id"] == "graph":
            t["enabled"] = True
            t["name"] = "改過的名字"
    save_templates(paths, templates)

    got = reset_template(paths, "graph", _seed("graph"))

    assert got["name"] == _seed("graph")["name"]  # 定義回出廠
    assert got["enabled"] is True                        # 啟用選擇保留

    assert reset_template(paths, "no-such-id", _seed("jargon-default")) is None


# ── POST /api/templates/retarget:孤兒樣板的批次轉換 ─────────────────────

def _mk_note(c, nid, name, template):
    r = c.post("/api/notes", json={"id": nid, "name": name, "description": "說明",
                                   "tags": [], "attachments": [],
                                   "template": template, "fields": {}})
    assert r.status_code == 200
    return r.json()


def test_retarget_moves_notes_without_touching_updated_or_history(register_user):
    """轉換後 template 改了,但 updated 不變、history 不增——理由同批次圖片壓縮:
    使用者沒有編輯內容,bump 會毀掉排序、寫歷史會沖光 HISTORY_LIMIT=3。"""
    c = register_user()["client"]
    before = _mk_note(c, "orphan01", "孤兒一", "gone-tpl")
    _mk_note(c, "keeper01", "無關", "jargon-default")

    r = c.post("/api/templates/retarget",
               json={"from_id": "gone-tpl", "to_id": "jargon-default"})
    assert r.status_code == 200
    assert r.json()["count"] == 1

    after = c.get("/api/notes/orphan01").json()
    assert after["template"] == "jargon-default"
    assert after["updated"] == before["updated"]
    assert after.get("history", []) == []
    # 無關的名詞原樣
    assert c.get("/api/notes/keeper01").json()["template"] == "jargon-default"


def test_retarget_rejects_bad_source_target(register_user):
    c = register_user()["client"]
    # 轉到不存在的樣板只是製造新孤兒;來源=目標則是原地轉換,都 400 擋掉
    assert c.post("/api/templates/retarget",
                  json={"from_id": "gone-tpl", "to_id": "also-gone"}).status_code == 400
    assert c.post("/api/templates/retarget",
                  json={"from_id": "jargon-default",
                        "to_id": "jargon-default"}).status_code == 400


def test_retarget_is_isolated_per_user(register_user):
    """A 的轉換絕不動到 B 的名詞。"""
    a = register_user()["client"]
    b = register_user()["client"]
    _mk_note(a, "orphana1", "A的孤兒", "gone-tpl")
    _mk_note(b, "orphanb1", "B的孤兒", "gone-tpl")

    r = a.post("/api/templates/retarget",
               json={"from_id": "gone-tpl", "to_id": "jargon-default"})
    assert r.status_code == 200 and r.json()["count"] == 1
    assert b.get("/api/notes/orphanb1").json()["template"] == "gone-tpl"


def test_retarget_requires_login(client):
    assert client.post("/api/templates/retarget",
                       json={"from_id": "a", "to_id": "b"}).status_code == 401


def test_retarget_with_empty_from_id_sweeps_all_orphans(register_user):
    """from_id 留空 = 轉換所有孤兒(健康度明細有 50 筆截斷,前端湊不齊全部
    孤兒 id,差集必須由後端算)。掛在既有樣板上的名詞絕不受波及。"""
    c = register_user()["client"]
    _mk_note(c, "orphanx01", "孤兒甲", "gone-a")
    _mk_note(c, "orphanx02", "孤兒乙", "gone-b")
    _mk_note(c, "keeperx01", "正常", "graph")

    r = c.post("/api/templates/retarget", json={"to_id": "jargon-default"})
    assert r.status_code == 200 and r.json()["count"] == 2
    assert c.get("/api/notes/orphanx01").json()["template"] == "jargon-default"
    assert c.get("/api/notes/orphanx02").json()["template"] == "jargon-default"
    assert c.get("/api/notes/keeperx01").json()["template"] == "graph"

"""app/plugins.py + routers/plugins.py:外掛登記簿與管理 API。

重點:預設未安裝、安裝/解除循環(解除保留 config)、設定更新只收白名單 key,
舊版「article-keywords 內建樣板」→ 外掛的一次性遷移(自訂 prompt 保留、
視為已安裝、樣板清單移除、冪等),以及 field-template 分類的外掛:
安裝=註冊樣板(只補不覆蓋)、解除=移除樣板但名詞資料不消失、
種子欄位 key 必須合法且不撞保留字。
"""
from app import plugin_catalog
from app.config import ARTICLE_PLUGIN_ID, FIELD_KEY_RE, RESERVED_FIELD_KEYS
from app.plugins import (
    DEMOTED_PLUGIN_IDS,
    PROMOTED_TEMPLATE_ID,
    default_config,
    ensure_plugins,
    get_plugin_config,
    install_plugin,
    is_installed,
    load_plugins,
    plugin_ids,
    uninstall_plugin,
)
from app.templates import ensure_templates, load_templates, save_templates


def test_fresh_user_has_plugins_uninstalled_with_default_config(paths):
    ensure_templates(paths)
    ensure_plugins(paths)
    assert paths.plugins_path.exists()
    assert not is_installed(paths, ARTICLE_PLUGIN_ID)
    # 預設指示的真相在官方封裝 manifest(official_plugins/article-keywords/plugin.json)
    assert get_plugin_config(paths, ARTICLE_PLUGIN_ID)["ai_prompt"] == \
        default_config(ARTICLE_PLUGIN_ID)["ai_prompt"]
    assert default_config(ARTICLE_PLUGIN_ID)["ai_prompt"].strip()
    # 降級成外掛的兩個同樣預設未安裝,樣板不出現在新帳號的清單裡
    for pid in DEMOTED_PLUGIN_IDS:
        assert pid not in {t["id"] for t in load_templates(paths)}, pid
        assert not is_installed(paths, pid), pid


def test_legacy_article_template_migrates_to_installed_plugin(paths):
    # 模擬舊版使用者:templates.json 裡還有 article-keywords 內建樣板(自訂過 prompt)
    ensure_templates(paths)
    templates = load_templates(paths)
    templates.append({"id": ARTICLE_PLUGIN_ID, "name": "文章>關鍵字", "builtin": True,
                      "ai_input_mode": "article", "ai_prompt": "使用者自訂的指示", "fields": []})
    save_templates(paths, templates)

    ensure_plugins(paths)

    assert is_installed(paths, ARTICLE_PLUGIN_ID)  # 既有使用者功能不中斷
    assert get_plugin_config(paths, ARTICLE_PLUGIN_ID)["ai_prompt"] == "使用者自訂的指示"
    assert ARTICLE_PLUGIN_ID not in {t["id"] for t in load_templates(paths)}

    ensure_plugins(paths)  # 冪等:再跑一次不會改變狀態
    assert is_installed(paths, ARTICLE_PLUGIN_ID)


def test_builtin_template_demotes_to_installed_plugin(paths):
    """英文單字/植物辨識從內建樣板改成外掛(2026-08-18,process-sop 2026-08-06 走過
    同一條路):既有帳號的樣板**原地保留**(含使用者修改),只把 builtin 降成 False
    讓它可編輯可刪除,並把外掛標記為已安裝——跟 article-keywords 的「整個搬走」
    不同,資料一個位元組都不動。

    ⚠ 這是本檔最重要的守門測試之一:降級遷移一旦把使用者改過的名稱/指示洗掉,
    使用者是不會收到任何錯誤訊息的。"""
    ensure_templates(paths)
    templates = load_templates(paths)
    for pid in DEMOTED_PLUGIN_IDS:
        templates.append({"id": pid, "name": f"我的 {pid}", "builtin": True,
                          "enabled": True, "ai_input_mode": "name",
                          "ai_prompt": "使用者自訂的指示",
                          "fields": [{"key": "kept", "label": "使用者加的欄位"}]})
    save_templates(paths, templates)

    ensure_plugins(paths)

    for pid in DEMOTED_PLUGIN_IDS:
        tpl = next(t for t in load_templates(paths) if t["id"] == pid)
        assert tpl["builtin"] is False               # 從此可編輯可刪除
        assert tpl["name"] == f"我的 {pid}"          # 使用者的修改原樣保留
        assert tpl["ai_prompt"] == "使用者自訂的指示"
        assert [f["key"] for f in tpl["fields"]] == ["kept"]
        assert is_installed(paths, pid)              # 「解除安裝」成為正常的移除路徑

    ensure_plugins(paths)  # 冪等:再跑一次不會改變狀態
    for pid in DEMOTED_PLUGIN_IDS:
        tpl = next(t for t in load_templates(paths) if t["id"] == pid)
        assert tpl["builtin"] is False
        assert tpl["name"] == f"我的 {pid}"


def test_installed_plugin_template_promotes_back_to_builtin(paths):
    """passage-decoder 反過來從外掛升成內建(2026-08-18)——唯一一次反方向搬遷。

    裝過這個外掛的帳號,templates.json 裡那份是 builtin=False;ensure_templates()
    的「只補不覆蓋」不會動它,結果名稱不會跟著介面語言走(name_is_default 只對
    內建成立),而且它還留在可刪除清單裡。遷移把 builtin 補回 True 收斂成與新帳號
    一致,但**不碰使用者改過的內容**。"""
    ensure_templates(paths)
    templates = [t for t in load_templates(paths) if t["id"] != PROMOTED_TEMPLATE_ID]
    templates.append({"id": PROMOTED_TEMPLATE_ID, "name": "Passage Decoder",
                      "builtin": False, "enabled": True, "ai_input_mode": "paste",
                      "ai_prompt": "我改過的指示",
                      "fields": [{"key": "gist", "label": "我的標題"}]})
    save_templates(paths, templates)

    ensure_plugins(paths)

    tpl = next(t for t in load_templates(paths) if t["id"] == PROMOTED_TEMPLATE_ID)
    assert tpl["builtin"] is True
    assert tpl["ai_prompt"] == "我改過的指示"          # 使用者的修改不動
    assert tpl["fields"][0]["label"] == "我的標題"

    ensure_plugins(paths)  # 冪等
    tpl = next(t for t in load_templates(paths) if t["id"] == PROMOTED_TEMPLATE_ID)
    assert tpl["builtin"] is True


def test_plugin_api_list_install_uninstall_cycle(register_user):
    u = register_user()
    c = u["client"]

    plugins = c.get("/api/plugins").json()["plugins"]
    assert [p["id"] for p in plugins] == plugin_ids()
    article = next(p for p in plugins if p["id"] == ARTICLE_PLUGIN_ID)
    assert article["installed"] is False
    assert article["category"] == "ai-tool"

    r = c.post(f"/api/plugins/{ARTICLE_PLUGIN_ID}/install")
    assert r.status_code == 200 and r.json()["installed"] is True

    # 自訂設定 → 解除安裝 → config 保留,重新安裝後還在
    r = c.put(f"/api/plugins/{ARTICLE_PLUGIN_ID}/config",
              json={"config": {"ai_prompt": "自訂", "unknown_key": "忽略我"}})
    assert r.status_code == 200
    assert r.json()["config"]["ai_prompt"] == "自訂"
    assert "unknown_key" not in r.json()["config"]

    r = c.delete(f"/api/plugins/{ARTICLE_PLUGIN_ID}")
    assert r.status_code == 200 and r.json()["installed"] is False
    assert r.json()["config"]["ai_prompt"] == "自訂"

    r = c.post(f"/api/plugins/{ARTICLE_PLUGIN_ID}/install")
    assert r.json()["config"]["ai_prompt"] == "自訂"

    # 未知 id 一律 404
    assert c.post("/api/plugins/nope/install").status_code == 404
    assert c.delete("/api/plugins/nope").status_code == 404


def test_new_user_template_list_equals_default_templates(register_user):
    """article-keywords 不再是樣板、外掛樣板不裝不出現——新帳號的樣板清單
    就是 DEFAULT_TEMPLATES,一個不多一個不少(以 DEFAULT_TEMPLATES 為準比對,
    之後新增官方樣板時不會誤爆)。"""
    from app.templates import DEFAULT_TEMPLATES

    u = register_user()
    ids = {t["id"] for t in u["client"].get("/api/templates").json()["templates"]}
    assert "article-keywords" not in ids
    assert ids == {t["id"] for t in DEFAULT_TEMPLATES}


def test_article_note_uses_plugin_prompt(register_user, monkeypatch):
    """article-note 的內容指示改從外掛設定讀,不再讀樣板。"""
    from app.ai_settings import save_ai_settings
    from app.routers import ai as ai_router

    u = register_user()
    save_ai_settings({"enabled": True, "base_url": "http://x", "model": "m"})
    c = u["client"]
    c.post(f"/api/plugins/{ARTICLE_PLUGIN_ID}/install")
    c.put(f"/api/plugins/{ARTICLE_PLUGIN_ID}/config",
          json={"config": {"ai_prompt": "外掛專屬指示"}})

    captured = {}

    async def fake(settings, system_prompt, user_content):
        captured["system"] = system_prompt
        return {"name": "X", "description": "", "fields": {}, "keywords": []}

    monkeypatch.setattr(ai_router, "_chat_json", fake)
    r = c.post("/api/ai/article-note", json={"keyword": "X", "article": "文"})
    assert r.status_code == 200
    assert captured["system"].startswith("外掛專屬指示")


# ── field-template 分類:安裝=註冊樣板、解除=移除樣板 ──

TPL_PID = "mba-term"  # 任挑一個 field-template 外掛當代表


def test_catalog_every_plugin_has_a_category():
    """迭代掃描出來的型錄(official_plugins/)——官方封裝壞一個,這裡就會少一個,
    所以拿 repo 目錄實數對照,不寫死數字(封裝數會隨版本成長)。"""
    from app.config import OFFICIAL_PLUGINS_DIR
    official_dirs = {d.name for d in OFFICIAL_PLUGINS_DIR.iterdir() if d.is_dir()}
    cat = plugin_catalog.catalog()
    assert official_dirs <= set(cat)  # 壞封裝不進型錄,少了就是有封裝壞了
    assert {e["category"] for e in cat.values()} <= plugin_catalog.KNOWN_CATEGORIES
    assert all(e.get("category") for e in cat.values())


def test_template_seeds_have_legal_field_keys():
    """種子欄位 key 必須符合 FIELD_KEY_RE 且不撞保留字。封裝載入端
    (plugin_catalog._clean_template)已經走 clean_template_fields 同一把尺,
    這裡是對「repo 裡實際發佈的官方封裝」的整批驗收。"""
    seeds = [e["template"] for e in plugin_catalog.catalog().values()
             if e["category"] == "field-template"]
    assert seeds  # 官方封裝裡一定有樣板類外掛
    for seed in seeds:
        assert seed["builtin"] is False  # 外掛樣板是自訂樣板,使用者可自由編輯
        keys = [f["key"] for f in seed["fields"]]
        assert len(keys) == len(set(keys)), seed["id"]  # 不得重複
        for key in keys:
            assert FIELD_KEY_RE.match(key), f"{seed['id']}.{key}"
            assert key not in RESERVED_FIELD_KEYS, f"{seed['id']}.{key}"


def test_template_plugin_install_registers_template_and_uninstall_removes(paths):
    ensure_templates(paths)
    install_plugin(paths, TPL_PID)
    assert is_installed(paths, TPL_PID)
    tpl = next(t for t in load_templates(paths) if t["id"] == TPL_PID)
    assert tpl["name"] == "MBA 管理術語" and tpl["builtin"] is False

    # 只補不覆蓋:樣板還在(使用者改過)時重複安裝,修改必須原樣保留
    templates = load_templates(paths)
    tpl = next(t for t in templates if t["id"] == TPL_PID)
    tpl["name"] = "我的攝影筆記"
    tpl["fields"].append({"key": "my_field", "label": "自訂", "placeholder": ""})
    save_templates(paths, templates)

    install_plugin(paths, TPL_PID)  # 冪等重裝
    tpl = next(t for t in load_templates(paths) if t["id"] == TPL_PID)
    assert tpl["name"] == "我的攝影筆記"
    assert any(f["key"] == "my_field" for f in tpl["fields"])

    uninstall_plugin(paths, TPL_PID)
    assert not is_installed(paths, TPL_PID)
    assert TPL_PID not in {t["id"] for t in load_templates(paths)}


def test_template_plugin_cycle_via_api_and_notes_survive(register_user):
    """走 HTTP 的完整循環:安裝後樣板出現在 /api/templates、可建名詞;
    解除後樣板消失,但名詞與欄位值原封不動(資料永不靜默消失)。"""
    u = register_user()
    c = u["client"]

    r = c.post(f"/api/plugins/{TPL_PID}/install")
    assert r.status_code == 200 and r.json()["category"] == "field-template"
    assert TPL_PID in {t["id"] for t in c.get("/api/templates").json()["templates"]}

    r = c.post("/api/notes", json={"name": "追焦", "description": "跟著移動主體平移相機",
                                   "template": TPL_PID,
                                   "fields": {"en_term": "Panning"}, "tags": []})
    assert r.status_code == 200
    nid = r.json()["id"]

    assert c.delete(f"/api/plugins/{TPL_PID}").status_code == 200
    assert TPL_PID not in {t["id"] for t in c.get("/api/templates").json()["templates"]}
    note = c.get(f"/api/notes/{nid}").json()
    assert note["fields"]["en_term"] == "Panning"  # 值在名詞自身上,樣板移除不影響
    assert note["template"] == TPL_PID

    # 重新安裝 = 樣板回到預設定義(也是誤刪樣板的復原路徑)
    assert c.post(f"/api/plugins/{TPL_PID}/install").status_code == 200
    assert TPL_PID in {t["id"] for t in c.get("/api/templates").json()["templates"]}

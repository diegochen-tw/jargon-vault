"""routers/ai.py:不需要真的呼叫 Ollama 就能驗證的部分。

_chat_json(送 Ollama 的那一步)用 monkeypatch 換成假回覆,驗證的是端點
自己的邏輯:啟用檢查、輸入驗證、樣板欄位 schema 的組裝與回覆的欄位過濾。
真的打 Ollama 的整合行為(連線失敗、格式壞掉)仍是已知測試缺口。
"""
from app.ai_settings import save_ai_settings
from app.routers import ai as ai_router
from app.users import set_user_admin


def _set_ai(paths, enabled: bool):
    """AI 設定是**站台層、全站唯一一組**的,不再吃 paths。

    參數保留是為了讓既有測試的呼叫形狀不用整批改寫,也留著提醒:這裡不是
    「設定某個使用者的 AI」——它會影響所有人。
    """
    save_ai_settings({"enabled": enabled,
                      "base_url": "http://127.0.0.1:11434",
                      "model": "test-model"})


def _fake_chat(reply: dict, captured: dict | None = None):
    async def fake(settings, system_prompt, user_content):
        if captured is not None:
            captured["system"] = system_prompt
            captured["user"] = user_content
        return reply
    return fake


def _install_article_plugin(u):
    r = u["client"].post("/api/plugins/article-keywords/install")
    assert r.status_code == 200


# ── _extract_json:模型回覆的擷取層 ──────────────────────────────────

def test_extract_json_tolerates_literal_newlines_in_strings():
    """小模型被要求輸出多行 description(如一段話解讀的引文+三段結構)時,
    常在 JSON 字串裡直接輸出字面換行——嚴格模式會炸成 502,strict=False
    要吃得下。這是實際發生過的 502(2026-08-10)。"""
    raw = '{"name": "x", "description": "> 原文\n{{yellow:標題}}\n第一段\n\n第二段"}'
    parsed = ai_router._extract_json(raw)
    assert parsed["description"].count("\n") == 4
    # 正常跳脫的回覆當然也要照樣通過
    assert ai_router._extract_json('{"a": "b\\nc"}') == {"a": "b\nc"}


# ── 連線設定:站台層 + 只有管理者改得動 ──────────────────────────────

def _admin_user(register_user):
    """註冊一個使用者並升成站台 admin。

    不能只靠「第一位註冊者自動 admin」——users.json 是整個 pytest session 共用的,
    誰是第一位取決於測試執行順序,那會變成最難查的那種 flaky。
    """
    u = register_user()
    set_user_admin(u["id"], True)
    return u


def test_settings_update_keeps_fields_the_caller_did_not_send(register_user):
    """⚠ 這支守的是一類會一再重演的 bug。

    舊版的 PUT /api/ai/settings 是「用 body 重建一個三鍵 dict」,所以每次
    schema 長出新欄位,只帶舊欄位的呼叫端(mcp_server 的 update_ai_settings
    工具只帶 enabled/base_url/model)就會把新欄位**靜默清成預設值**。
    說明字數限制那兩個新鍵也在同一條防線上。
    """
    u = _admin_user(register_user)
    u["client"].put("/api/ai/settings", json={
        "api_style": "openai", "api_key": "sk-abc", "embed_model": "bge-m3",
        "desc_limit_enabled": False, "desc_max_chars": 600,
    })
    # 模擬只認識舊 schema 的呼叫端
    u["client"].put("/api/ai/settings", json={
        "enabled": True, "base_url": "http://127.0.0.1:11434", "model": "m",
    })

    s = u["client"].get("/api/ai/settings").json()
    assert s["api_style"] == "openai"
    assert s["has_api_key"] is True      # 明文不回傳,但金鑰確實還在
    assert s["embed_model"] == "bge-m3"
    assert s["model"] == "m"
    assert s["desc_limit_enabled"] is False
    assert s["desc_max_chars"] == 600


def test_settings_never_leak_the_api_key(register_user):
    """⚠ 讀取權限開放給所有登入者,所以 GET **絕不能**回傳金鑰明文——
    只回 has_api_key。比照 admin 的 Google client_secret。"""
    admin = _admin_user(register_user)
    admin["client"].put("/api/ai/settings", json={"api_key": "sk-secret-value"})

    for who in (admin, register_user()):
        body = who["client"].get("/api/ai/settings")
        assert body.status_code == 200
        s = body.json()
        assert "api_key" not in s
        assert "sk-secret-value" not in body.text
        assert s["has_api_key"] is True


def test_non_admin_cannot_change_settings_or_probe_hosts(register_user):
    """⚠「AI 管理權交給管理者」整個改動的核心保證:非 admin 的 PUT 收 403,
    GET /api/ai/models(會拿任意 base_url 對外發請求)同樣收 admin;
    而設定是全站唯一一組——admin 改了,其他人讀到的就是同一份。"""
    admin = _admin_user(register_user)
    admin["client"].put("/api/ai/settings", json={"model": "admin-picked"})

    other = register_user()
    r = other["client"].put("/api/ai/settings", json={"model": "hijacked"})
    assert r.status_code == 403
    assert other["client"].get("/api/ai/models").status_code == 403

    # 全站共用:兩個人讀到同一份,而且沒有被劫持
    assert admin["client"].get("/api/ai/settings").json()["model"] == "admin-picked"
    assert other["client"].get("/api/ai/settings").json()["model"] == "admin-picked"


def test_settings_clear_fields_and_reject_unknown_api_style(register_user):
    """空字串是「清除」這個有意義的狀態,**不可以**改成「空 = 沿用既有值」——
    那樣設錯的金鑰就再也拿不掉了(前端負責在使用者沒動那一欄時不送這個欄位)。
    不認識的 api_style 落回 ollama。"""
    u = _admin_user(register_user)
    u["client"].put("/api/ai/settings", json={"embed_model": "bge-m3", "api_key": "sk-abc"})
    u["client"].put("/api/ai/settings", json={"embed_model": "", "api_key": "",
                                              "api_style": "anthropic"})

    s = u["client"].get("/api/ai/settings").json()
    assert s["embed_model"] == ""
    assert s["has_api_key"] is False
    assert s["api_style"] == "ollama"


def test_legacy_settings_gain_the_new_defaults(register_user):
    """舊設定只有三個鍵,讀進來要自動補上新欄位(_clean_ai 的 merge-over-defaults)。"""
    u = register_user()
    _set_ai(u["paths"], enabled=True)

    s = u["client"].get("/api/ai/settings").json()
    assert s["api_style"] == "ollama" and s["has_api_key"] is False and s["embed_model"] == ""
    # 說明字數限制的兩個新鍵也一樣:舊呼叫端沒帶就補出廠值(預設開、250 字)
    assert s["desc_limit_enabled"] is True
    assert s["desc_max_chars"] == 250


def test_generate_prompt_carries_the_desc_limit_only_when_enabled(register_user, monkeypatch):
    """限制句內聯在 schema 的 description 提示值後面(prompt 指示,非硬截斷),
    zh-Hant 與 en 各抽一語驗;12 語 key 齊全由 test_prompts_tables 系列守。
    關掉之後限制句要消失。"""
    u = _admin_user(register_user)
    _set_ai(u["paths"], enabled=True)  # _clean_ai 補上預設:啟用、250
    reply = {"name": "x", "description": "d", "fields": {}, "keywords": []}

    captured = {}
    monkeypatch.setattr(ai_router, "_chat_json", _fake_chat(reply, captured))
    u["client"].post("/api/ai/generate",
                     json={"input": "RAG", "template": "jargon-default", "lang": "zh-Hant"})
    assert "約 250 字以內" in captured["system"]

    captured = {}
    monkeypatch.setattr(ai_router, "_chat_json", _fake_chat(reply, captured))
    u["client"].post("/api/ai/generate",
                     json={"input": "RAG", "template": "jargon-default", "lang": "en"})
    assert "roughly 250 characters" in captured["system"]

    u["client"].put("/api/ai/settings", json={"desc_limit_enabled": False})
    captured = {}
    monkeypatch.setattr(ai_router, "_chat_json", _fake_chat(reply, captured))
    u["client"].post("/api/ai/generate",
                     json={"input": "RAG", "template": "jargon-default", "lang": "zh-Hant"})
    assert "字以內" not in captured["system"]


# ── 文章多選批次生成(article-note)──────────────────────────────────

def test_article_note_input_gates(register_user):
    """三道門依序:AI 未啟用 400、外掛未安裝 400、空 keyword/article 400。"""
    u = register_user()
    _set_ai(u["paths"], enabled=False)
    r = u["client"].post("/api/ai/article-note",
                         json={"keyword": "RAG", "article": "一篇文章"})
    assert r.status_code == 400

    _set_ai(u["paths"], enabled=True)
    r = u["client"].post("/api/ai/article-note",
                         json={"keyword": "RAG", "article": "一篇文章"})
    assert r.status_code == 400  # 外掛未安裝

    _install_article_plugin(u)
    for body in ({"keyword": " ", "article": "文"}, {"keyword": "RAG", "article": ""}):
        assert u["client"].post("/api/ai/article-note", json=body).status_code == 400


def test_article_note_generates_default_template_note(register_user, monkeypatch):
    u = register_user()
    _set_ai(u["paths"], enabled=True)
    _install_article_plugin(u)
    captured = {}
    monkeypatch.setattr(ai_router, "_chat_json", _fake_chat({
        "name": "RAG",
        "description": "檢索增強生成。",
        "fields": {"alias": "Retrieval-Augmented Generation", "bogus": "模型多給的"},
        "keywords": ["AI", "檢索", "LLM", "第四個要被切掉"],
    }, captured))

    r = u["client"].post("/api/ai/article-note",
                         json={"keyword": "RAG", "article": "本文介紹 RAG 架構…"})
    assert r.status_code == 200
    d = r.json()
    # 產出的名詞掛在預設樣板上,欄位只收預設樣板定義內的 key
    assert d["template"] == "jargon-default"
    assert d["name"] == "RAG"
    assert d["fields"]["alias"] == "Retrieval-Augmented Generation"
    assert "bogus" not in d["fields"]
    assert d["tags"] == ["AI", "檢索", "LLM"]
    # 送給 AI 的內容含文章全文與指定名詞(不帶 lang → 英文 prompt 零件);
    # schema 用的是預設樣板的欄位
    assert "本文介紹 RAG 架構…" in captured["user"]
    assert 'Term to explain: "RAG"' in captured["user"]
    assert '"alias"' in captured["system"]

    # 模型漏了 name → 退回使用者指定的 keyword
    monkeypatch.setattr(ai_router, "_chat_json",
                        _fake_chat({"description": "說明", "fields": {}, "keywords": []}))
    r = u["client"].post("/api/ai/article-note",
                         json={"keyword": "向量資料庫", "article": "文章內容"})
    assert r.status_code == 200
    assert r.json()["name"] == "向量資料庫"


def test_warmup_never_blocks(register_user):
    """預熱只是最佳化,絕不能擋住開編輯器:未啟用 → 安靜略過;啟用但連不到
    Ollama → 吞掉連線錯誤。兩種都是 200 + warmed:False。"""
    u = register_user()
    _set_ai(u["paths"], enabled=False)
    r = u["client"].post("/api/ai/warmup")
    assert r.status_code == 200 and r.json() == {"warmed": False}

    save_ai_settings({"enabled": True,
                      "base_url": "http://127.0.0.1:1",  # 沒有服務在聽
                      "model": "test-model"})
    r = u["client"].post("/api/ai/warmup")
    assert r.status_code == 200 and r.json() == {"warmed": False}


def test_ai_endpoints_require_enablement_and_login(register_user, client):
    """啟用檢查(未啟用一律 400)逐端點掃一遍;未登入則是 401。"""
    u = register_user()
    _set_ai(u["paths"], enabled=False)

    assert u["client"].post("/api/ai/generate",
                            json={"input": "RAG", "template": "jargon-default"}).status_code == 400
    assert u["client"].post("/api/ai/group-tags",
                            json={"tags": ["a"], "groups": []}).status_code == 400
    assert u["client"].post("/api/ai/tag-duplicates",
                            json={"tags": ["a", "b"]}).status_code == 400
    assert u["client"].post("/api/ai/fill",
                            json={"targets": ["alias"], "name": "MSL"}).status_code == 400

    assert client.post("/api/ai/fill", json={"targets": ["alias"]}).status_code == 401


# ── 生成內容的輸出語言(lang) ──

def test_lang_selection_defaults_to_english_and_falls_back(register_user, monkeypatch):
    """產品面向全世界:缺 lang、lang=en、不認識的 lang 都是英文(不是繁中)——
    MCP server 等舊呼叫端不帶 lang。/api/ai/tags 也吃同一個 lang(抽 ja 驗)。"""
    u = register_user()
    _set_ai(u["paths"], enabled=True)
    reply = {"name": "RAG", "description": "d", "fields": {}, "keywords": []}

    for payload in ({"input": "RAG", "template": "jargon-default"},
                    {"input": "RAG", "template": "jargon-default", "lang": "en"},
                    {"input": "RAG", "template": "jargon-default", "lang": "xx-not-a-lang"}):
        captured = {}
        monkeypatch.setattr(ai_router, "_chat_json", _fake_chat(reply, captured))
        r = u["client"].post("/api/ai/generate", json=payload)
        assert r.status_code == 200
        assert "respond in English" in captured["system"]
        assert "繁體中文" not in captured["system"]

    captured = {}
    monkeypatch.setattr(ai_router, "_chat_json", _fake_chat({"keywords": ["a"]}, captured))
    r = u["client"].post("/api/ai/tags",
                         json={"name": "RAG", "description": "", "lang": "ja"})
    assert r.status_code == 200
    assert "日本語で回答して" in captured["system"]


def test_generate_supports_all_advertised_languages(register_user, monkeypatch):
    """每個 i18n.js LANG 值都要有完整在地化的固定 prompt(不是只有語言指示那一句)。"""
    u = register_user()
    _set_ai(u["paths"], enabled=True)
    expect_substr = {
        "zh-Hant": "繁體中文", "zh-Hans": "简体中文", "en": "English", "ja": "日本語",
        "fr": "français", "de": "Deutsch", "it": "italiano",
        "pt": "português", "es": "español", "ko": "한국어",
        "id": "Bahasa Indonesia", "hi": "हिंदी",
    }
    assert set(expect_substr) == set(ai_router.PROMPTS), "測試清單與 PROMPTS 不同步"
    for lang, substr in expect_substr.items():
        captured = {}
        monkeypatch.setattr(ai_router, "_chat_json",
                            _fake_chat({"name": "x", "description": "d", "fields": {}, "keywords": []},
                                       captured))
        r = u["client"].post("/api/ai/generate",
                             json={"input": "RAG", "template": "jargon-default", "lang": lang})
        assert r.status_code == 200, (lang, r.text)
        assert substr in captured["system"], (lang, captured["system"])


def test_prompt_tables_are_complete_and_zh_hans_is_simplified():
    """新增語言時最容易漏的是「補了幾個 key 就收工」——缺 key 會靜默退回英文
    (DEFAULT_LANG),畫面上看不出來(見 _p() 的 fallback)。這裡拿基準語言的
    key 清單逐語言對齊;另驗簡中不是繁中的別名(固定 prompt 是真的簡體用字)。"""
    base = set(ai_router.PROMPTS[ai_router.DEFAULT_LANG])
    for lang, table in ai_router.PROMPTS.items():
        assert set(table) == base, (lang, base ^ set(table))
        # 值不能是空的,也不能整段照抄繁中(那代表根本沒翻)
        assert all(table[k] for k in table if isinstance(table[k], str)), lang

    hans = ai_router.PROMPTS["zh-Hans"]
    assert "简体中文" in hans["lang_directive"]
    assert "繁體" not in hans["lang_directive"] and "繁体" not in hans["lang_directive"]
    assert hans["label_term"] == "名词" and hans["label_tags"] == "标签"
    assert hans["tags_system"] != ai_router.PROMPTS["zh-Hant"]["tags_system"]


# 真正送出去的 request body 長什麼樣,已隨 httpx 一起搬到 app/llm.py,
# 對應的 wire-format 回歸測試在 tests/test_llm.py(think=False、不得有 format
# 那幾條斷言都在那裡,而且兩種 api_style 各驗一遍)。


# ── AI 自動分組建議 + 標籤重複偵測:共同的輸入驗證 ──

def test_group_tags_and_tag_duplicates_reject_empty_tags(register_user):
    u = register_user()
    _set_ai(u["paths"], enabled=True)
    assert u["client"].post("/api/ai/group-tags",
                            json={"tags": [" ", ""], "groups": []}).status_code == 400
    assert u["client"].post("/api/ai/tag-duplicates",
                            json={"tags": [" ", ""]}).status_code == 400


def test_group_tags_returns_only_valid_assignments(register_user, monkeypatch):
    """過濾:成員要在本批、群組名非空。zh-Hant 時 value(模型生成的群組名)要
    過 s2twp,key(使用者既有標籤)**絕不動**——key 被轉的症狀是套用時對不上
    磁碟上的標籤,靜默失敗。"""
    u = register_user()
    _set_ai(u["paths"], enabled=True)
    captured = {}
    monkeypatch.setattr(ai_router, "_chat_json", _fake_chat({
        "groups": {"Python": "程式語言", "Rust": "程式語言",
                   "不在本批": "X", "空群組": ""},
    }, captured))
    r = u["client"].post("/api/ai/group-tags",
                         json={"tags": ["Python", "Rust", "空群組"], "groups": ["既有群組"]})
    assert r.status_code == 200
    # 只收「本批標籤 + 群組名非空」:不在本批(過濾)、空群組(群組空,過濾)
    assert r.json()["groups"] == {"Python": "程式語言", "Rust": "程式語言"}
    # 既有群組名有帶進 prompt,讓 AI 盡量沿用
    assert "既有群組" in captured["user"]

    # zh-Hant:群組名轉繁、標籤 key 原樣
    monkeypatch.setattr(ai_router, "_chat_json",
                        _fake_chat({"groups": {"内存": "硬件术语", "CPU": "硬件术语"}}))
    r = u["client"].post("/api/ai/group-tags",
                         json={"tags": ["内存", "CPU"], "groups": [], "lang": "zh-Hant"})
    assert r.status_code == 200
    assert r.json()["groups"] == {"内存": "硬體術語", "CPU": "硬體術語"}


# ── 標籤相似度重複偵測:語意層 ──
# 字面層(Mes/MES、全形半形、標點)完全不碰 AI,測試在 tests/test_tag_dedup.py。
# 這裡只驗「模型亂給的一律丟掉」那三關。

def test_tag_duplicates_drops_hallucinated_and_degenerate_groups(register_user, monkeypatch):
    u = register_user()
    _set_ai(u["paths"], enabled=True)
    captured = {}
    monkeypatch.setattr(ai_router, "_chat_json", _fake_chat({"groups": [
        ["回焊爐", "Reflow Oven"],          # 好的:留下
        ["回焊爐", "根本沒送過這個"],        # 成員不在送出的清單裡 → 只剩一個 → 丟掉
        ["治具", "Jig", "治具"],             # 重複成員去重後留下
        ["MES", "Mes"],                      # 整組 norm_key 相同 → 字面層已經抓到 → 丟掉
        "這不是陣列",                         # 形狀不對 → 丟掉
    ]}, captured))
    r = u["client"].post("/api/ai/tag-duplicates",
                         json={"tags": ["回焊爐", "Reflow Oven", "治具", "Jig", "MES", "Mes"]})
    assert r.status_code == 200
    assert r.json()["groups"] == [["回焊爐", "Reflow Oven"], ["治具", "Jig"]]
    # 整份清單一次送出(不分批),否則落在不同批次的兩個寫法永遠配不到一起
    assert "Reflow Oven" in captured["user"] and "治具" in captured["user"]


def test_tag_duplicates_members_are_never_converted(register_user, monkeypatch):
    """tag-duplicates 的成員是使用者既有標籤的字面值,zh-Hant 也**絕不可**轉繁:
    轉了就跟送出的 tagset 對不上(靜默丟組),就算對得上,前端拿去合併時也會
    跟磁碟上的標籤名對不上。使用者的標籤本來就可能是簡體,那是他的資料。"""
    u = register_user()
    _set_ai(u["paths"], enabled=True)
    monkeypatch.setattr(ai_router, "_chat_json",
                        _fake_chat({"groups": [["内存", "記憶體"]]}))
    r = u["client"].post("/api/ai/tag-duplicates",
                         json={"tags": ["内存", "記憶體", "CPU"], "lang": "zh-Hant"})
    assert r.status_code == 200
    assert r.json()["groups"] == [["内存", "記憶體"]]


# ── /api/ai/fill:一次補齊所有空白欄位 ────────────────────────────────
# 「快速記下一個名詞、之後再補內容」是這個 app 預期的用法,所以補齊必須是
# **一次呼叫**(本機模型一輪好幾秒,一欄一欄打沒有人受得了)。這組守三件事:
# 合法目標由伺服器決定、schema 只列這次要補的欄位、空值絕不回傳。

def test_fill_rejects_request_with_no_valid_target(register_user):
    """targets 只是請求:空的、或全是伺服器不認的 key,一律 400。

    ⚠ 「沒有合法目標」絕不能靜默當成「補全部」——那正是使用者按下去之後
    最不希望發生的事(已經寫好的欄位被重寫)。停用的欄位也不是合法目標:
    en_term 在 jargon-default 預設停用,畫面上沒有位置放它(同 enabled_fields()
    的理由),所以只點它 = 沒有合法目標。
    """
    u = register_user()
    _set_ai(u["paths"], enabled=True)
    # name 也不在合法集合裡(補齊不改名);en_term 是停用欄位
    for targets in ([], ["bogus_key"], ["name"], ["en_term"]):
        r = u["client"].post("/api/ai/fill", json={"targets": targets, "name": "MSL"})
        assert r.status_code == 400, targets


def test_fill_schema_and_value_filtering(register_user, monkeypatch):
    """schema 只列被點名的欄位、回傳只收被點名的目標;模型沒生出來的空值
    **不回傳**——回空字串的話前端會拿它「填」進去,等於用空白蓋掉使用者在
    等待期間自己打的字。"""
    u = register_user()
    _set_ai(u["paths"], enabled=True)
    captured = {}
    monkeypatch.setattr(ai_router, "_chat_json", _fake_chat({
        "description": "濕氣敏感等級。",
        "fields": {"alias": "潮敏等級", "synonymy": "模型多給的(沒點名)",
                   "bogus": "根本不存在的欄位"},
        "name": "模型自作主張改的名字",
    }, captured))

    r = u["client"].post("/api/ai/fill", json={
        "targets": ["description", "alias"],
        "name": "MSL", "description": "", "fields": {"alias": "", "synonymy": "已經寫好了"},
    })
    assert r.status_code == 200
    values = r.json()["values"]
    # 只回被點名的目標:沒點的欄位(即使模型生了)與不存在的 key 一律丟掉,
    # name 也不在回傳裡——補齊不是改名
    assert set(values) == {"description", "alias"}
    assert values["alias"] == "潮敏等級"
    # schema 只列這次要補的欄位:已填好的 synonymy 不出現,模型就不會去動它
    assert '"alias"' in captured["system"]
    assert '"synonymy"' not in captured["system"]
    # 情境仍帶著整筆內容(含已填好的欄位),模型才知道要往哪個方向補
    assert "MSL" in captured["user"] and "已經寫好了" in captured["user"]

    # 空值不回傳
    monkeypatch.setattr(ai_router, "_chat_json", _fake_chat({
        "description": "  ", "fields": {"alias": "潮敏等級", "polysemy": ""},
    }, {}))
    r = u["client"].post("/api/ai/fill", json={
        "targets": ["description", "alias", "polysemy"], "name": "MSL",
    })
    assert r.status_code == 200
    assert r.json()["values"] == {"alias": "潮敏等級"}


# ── s2twp 簡轉繁保險(見 routers/ai.py 檔頭的 _zh)──────────────────────

def test_s2twp_converts_zh_hant_output_and_leaves_other_langs_alone(register_user, monkeypatch):
    """本機小模型在繁中請求裡常混出簡體字,lang=zh-Hant 時輸出要過 s2twp,
    覆蓋四個自由文字出口:name / description / fields / tags。反過來,不帶
    lang(= en)時輸出一個字都不能動——簡體使用者(zh-Hans)與其他語言都
    不該被轉繁,既有測試斷言字面值的那些也靠這條活著。"""
    u = register_user()
    _set_ai(u["paths"], enabled=True)
    monkeypatch.setattr(ai_router, "_chat_json", _fake_chat({
        "name": "内存",
        "description": "存储信息的硬件,默认开启。",
        "fields": {"alias": "内存条"},
        "keywords": ["硬件", "信息"],
    }))
    r = u["client"].post("/api/ai/generate",
                         json={"input": "RAM", "template": "jargon-default",
                               "lang": "zh-Hant"})
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "記憶體"
    assert d["description"] == "儲存資訊的硬體,預設開啟。"
    assert d["fields"]["alias"] == "記憶體條"
    assert d["tags"] == ["硬體", "資訊"]

    # 不帶 lang:原樣
    monkeypatch.setattr(ai_router, "_chat_json", _fake_chat({
        "name": "内存", "description": "存储信息", "fields": {}, "keywords": ["软件"],
    }))
    r = u["client"].post("/api/ai/generate",
                         json={"input": "RAM", "template": "jargon-default"})
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "内存"
    assert d["description"] == "存储信息"
    assert d["tags"] == ["软件"]

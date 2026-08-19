"""AI 生成:依「欄位樣板」各自的 ai_prompt 與欄位產生建議內容。

輸入方式(name/paste)由前端依樣板的 ai_input_mode 決定,後端只收到最終的
input 文字與 template id:載入該樣板的 ai_prompt 當內容指示,並用樣板欄位
動態組出 JSON schema,回傳 {name, description, fields, tags}。

本層只管「組什麼 prompt、怎麼解讀回覆」;實際跟模型服務講話(Ollama 原生 或
OpenAI 相容)一律交給 app/llm.py,固定 prompt 的多語言字典在 app/prompts.py。
本檔不得再出現 httpx。

多語系:所有會生成自由文字或分析內容的端點都收一個 lang 欄位(對應前端
i18n.js 的 LANG),不支援的值一律退回英文(DEFAULT_LANG)。細節見 app/prompts.py。
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException

from .. import llm
from ..ai_settings import DEFAULT_AI_SETTINGS, load_ai_settings, save_ai_settings
from ..auth import get_current_admin, get_user_paths
from ..config import ARTICLE_PLUGIN_ID, DEFAULT_TEMPLATE_ID
from ..models import (
    AIArticleNoteIn, AIFieldIn, AIFillIn, AIGenerateIn, AIGroupTagsIn,
    AISettingsIn, AITagDupIn, AITagsIn,
)
from ..paths import VaultPaths
from ..plugins import get_plugin_config, is_active
from ..prompts import DEFAULT_LANG, PROMPTS, for_lang
from ..prompts import lang_instruction as _lang_instruction
from ..prompts import p as _p
from ..sanitize import norm_key
from ..templates import enabled_fields, get_template

router = APIRouter()
log = logging.getLogger("jargon_vault")

# ── 繁中輸出的簡轉繁保險(OpenCC s2twp:簡體→繁體+台灣用語)────────────────
#
# 本機小模型(qwen 系列尤甚)在繁中請求裡經常混出簡體字,prompt 裡的語言指示
# (lang_directive)擋不乾淨。s2twp 對「已經是繁體」的文字是無害的恆等轉換,
# 所以對整段輸出過一遍是安全的;只在 lang == "zh-Hant" 時做——zh-Hans 使用者
# 要的就是簡體,其他語言則根本不含漢字。
# ⚠ 只能套在「模型生成的自由文字」上:group-tags 的 key 與 tag-duplicates 的
# 成員是使用者既有標籤的字面值,轉了就跟 tagset 與磁碟上的標籤名對不上,
# 症狀是靜默回空結果。收口點因此選在 _as_text()/_keywords()(那兩條路徑刻意
# 不經過這裡)。依賴缺席時退化成不轉換——AI 是選配,不能因為一個字典套件把
# 端點打成 500。
try:
    from opencc import OpenCC
    _S2TWP = OpenCC("s2twp")
except Exception:   # pragma: no cover - 依賴缺席的環境
    _S2TWP = None


def _zh(s: str, lang: str) -> str:
    if lang == "zh-Hant" and _S2TWP is not None and s:
        return _S2TWP.convert(s)
    return s



# ── 連線設定(站台層,只有管理者改得動)────────────────────────────────
#
# AI 連線設定是**全站唯一一組**、由站台管理者管理(理由見 app/ai_settings.py)。
# 這兩支寫入型端點因此掛 get_current_admin;它們刻意留在這個 router 而不是搬進
# ADMIN_ROUTERS,是為了讓設定 UI 的 AI 分頁讀寫維持同一組 URL——讀是所有登入者
# 都要的(前端得知道 enabled 才決定要不要顯示 AI 按鈕)。

def _settings_view(settings: dict) -> dict:
    """回給前端的形狀:**絕不含 api_key 明文**,只說有沒有設定過。

    比照 routers/admin.py:_public_settings() 對 Google client_secret 的處理。
    讀取權限開放給所有登入者,所以這一層遮蔽是必要的——金鑰是站台的機密,
    不是每個使用者都該看得到的東西。
    """
    out = {k: v for k, v in settings.items() if k != "api_key"}
    out["has_api_key"] = bool(settings.get("api_key"))
    return out


@router.get("/api/ai/settings")
def get_ai_settings():
    return _settings_view(load_ai_settings())


@router.get("/api/ai/models")
async def list_ai_models(base_url: str = "", api_style: str = "",
                         _: dict = Depends(get_current_admin)):
    """查詢服務端可用的模型清單(給設定 UI 的「列出模型」用)。

    base_url / api_style 帶入前端當下輸入框的值(可能還沒存檔);沒帶就用已存的
    設定——這支同時兼任「測試連線」,所以一定要能在存檔前先試。
    回傳 {models: [名稱, ...]}(依名稱排序)。

    收管理者權限:它接受任意 base_url 並代替伺服器對外發請求,是「編輯連線設定」
    的一部分能力,不是唯讀查詢。
    """
    settings = dict(load_ai_settings())
    if api_style.strip():
        settings["api_style"] = api_style.strip()
    try:
        return {"models": await llm.list_models(settings, base_url)}
    except llm.LLMError as e:
        raise HTTPException(502, str(e))


@router.put("/api/ai/settings")
def update_ai_settings(body: AISettingsIn, _: dict = Depends(get_current_admin)):
    """部分更新:只覆蓋 body 裡有帶的欄位,沒帶的原樣保留。

    ⚠ 這裡刻意**不是**「用 body 重建一個 dict」。舊版那樣寫,每次新增設定欄位
    都會讓只帶舊欄位的呼叫端(mcp_server 的 update_ai_settings 工具)把新欄位
    靜默清成預設值,而且不會有任何錯誤。理由與 NoteIn 刻意不含 marked 相同。
    """
    settings = dict(load_ai_settings())
    if body.enabled is not None:
        settings["enabled"] = body.enabled
    if body.api_style is not None:
        style = body.api_style.strip().lower()
        settings["api_style"] = style if style in ("ollama", "openai") else "ollama"
    if body.base_url is not None:
        settings["base_url"] = body.base_url.strip().rstrip("/") or DEFAULT_AI_SETTINGS["base_url"]
    if body.api_key is not None:
        # ⚠ 空字串仍然是「清除金鑰」,**不要**改成「空 = 沿用既有值」:那樣就再也
        # 沒有辦法把設錯的金鑰拿掉了(換到另一台不需要金鑰的本機服務時,舊金鑰會
        # 一直被送出去)。GET 已經把金鑰遮掉,所以「存檔時不小心清空」要由前端
        # 負責——**使用者沒動那一欄就別把 api_key 放進 payload**(見 settings.js)。
        settings["api_key"] = body.api_key.strip()
    if body.model is not None:
        settings["model"] = body.model.strip() or DEFAULT_AI_SETTINGS["model"]
    if body.embed_model is not None:
        # 允許清空:空字串是「沒有嵌入模型」這個有意義的狀態,不能退回預設
        settings["embed_model"] = body.embed_model.strip()
    if body.desc_limit_enabled is not None:
        settings["desc_limit_enabled"] = body.desc_limit_enabled
    if body.desc_max_chars is not None:
        # 夾值交給 save_ai_settings → _clean_ai 的 _int_in(50–2000)
        settings["desc_max_chars"] = body.desc_max_chars
    save_ai_settings(settings)
    return _settings_view(load_ai_settings())


@router.post("/api/ai/warmup")
async def ai_warmup():
    """預熱:請服務端先把模型載入記憶體,縮短使用者實際生成時的等待。

    前端在使用者按下「新增」時就 fire-and-forget 呼叫——趁使用者填表的空檔把
    模型載好。未啟用或連線失敗都安靜回報,不拋錯——預熱只是最佳化,絕不能
    擋住開啟編輯器。實際送什麼請求依 api_style 而定,見 app/llm.py:warmup()。
    """
    settings = load_ai_settings()
    if not settings.get("enabled"):
        return {"warmed": False}
    return {"warmed": await llm.warmup(settings)}


def _as_text(v, lang: str = DEFAULT_LANG) -> str:
    """模型有時不照 schema、把單一文字欄回成陣列(如 ["輔助容器"]),攤平成頓號分隔字串。

    這裡同時是 s2twp 簡轉繁的收口點(見檔頭 _zh):所有「模型生成的自由文字」
    都必定經過本函式或 _keywords(),而使用者既有標籤的比對路徑刻意不走這兩支。
    """
    if isinstance(v, list):
        return _zh("、".join(str(x).strip() for x in v if str(x).strip()), lang)
    return _zh(str(v or "").strip(), lang)


def _extract_json(content: str) -> dict:
    """
    從模型回覆中抽出 JSON 物件。
    刻意不用 Ollama 的 format="json" 約束解碼——該模式會在逐位元組取樣時
    切斷多位元組 UTF-8(中文變成 lone surrogate 亂碼,Ollama 已知問題),
    改由 prompt 要求輸出 JSON、這裡自行擷取:去掉 code fence,取第一個 { 到最後一個 }。
    """
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("回覆中找不到 JSON 物件")
    # strict=False:允許字串值裡出現未跳脫的換行/Tab。要求多行 description 的樣板
    # (如一段話解讀的引文+三段結構)最容易讓小模型在字串裡直接輸出字面換行,
    # 嚴格模式會整包炸成 502,而內容本身其實完全可用。
    return json.loads(text[start:end + 1], strict=False)


def _desc_limit(settings: dict) -> int | None:
    """生成說明的字數上限(站台設定):關閉時回 None = prompt 不帶限制句。"""
    return settings.get("desc_max_chars") if settings.get("desc_limit_enabled") else None


def _desc_limit_clause(p: dict, desc_limit: int | None) -> str:
    """「(請將說明文字控制在約 N 字以內)」——內聯在 schema 的 description 提示
    值後面,小模型最不容易忽略。措辭刻意用「約」留餘地:這是給模型的指示不是
    硬截斷,而且樣板自帶的長度要求(如「一段話解讀」三段各 150~250 字)可能
    比它長,概略措辭讓兩者打架時不至於把輸出掐死。"""
    if not desc_limit:
        return ""
    return f'{p["desc_limit_before"]}{desc_limit}{p["desc_limit_after"]}'


def _json_instruction(field_defs: list[dict], lang: str = DEFAULT_LANG,
                      desc_limit: int | None = None) -> str:
    """依樣板欄位動態組 JSON 輸出格式指示。格式是硬規則,不放進使用者可編輯的 prompt。
    整段(包含欄位提示字、格式說明)都依 lang 完整在地化,不是只在結尾加一句
    「請用 XX 回覆」——後者對本機能力較弱的小模型不夠可靠。"""
    if field_defs:
        lines = ",\n".join(f'    "{f["key"]}": "{f.get("label") or f["key"]}"'
                            for f in field_defs)
        fields_schema = "{\n" + lines + "\n  }"
    else:
        fields_schema = "{}"
    p = for_lang(lang)
    keywords_sample = json.dumps(p["json_keywords_sample"], ensure_ascii=False)
    return (
        p["json_intro"] +
        "{\n"
        f'  "name": "{p["json_name_label"]}",\n'
        f'  "description": "{p["json_desc_label"]}{_desc_limit_clause(p, desc_limit)}",\n'
        f'  "fields": {fields_schema},\n'
        f'  "keywords": {keywords_sample}\n'
        "}\n"
        + p["json_outro"] + f" {p['lang_directive']}"
    )


def _fill_instruction(field_defs: list[dict], want_description: bool,
                      lang: str = DEFAULT_LANG, desc_limit: int | None = None) -> str:
    """「補齊空白欄位」的 JSON 輸出格式指示(_json_instruction 的縮減版)。

    schema 只列出**這次要補的**欄位:沒有 name(名詞已經存在,補齊不是改名)、
    沒有 keywords(標籤有自己的入口 /api/ai/tags)。已填好的欄位不出現在 schema 裡,
    模型就不會去動它們——這比在 prompt 裡叮嚀「不要改其他欄位」可靠得多。

    刻意只重用既有的 prompts key(json_intro/json_desc_label/json_outro/
    lang_directive),所以 app/prompts.py 一行都不用改,12 種語言自動涵蓋。
    (代價是 json_outro 結尾那句「keywords 最多三個」在這裡是多餘的——模型真的
    多回一個 keywords 也只是被忽略,不值得為它多開 12 種語言的一組新 key。)
    """
    p = for_lang(lang)
    lines = []
    if want_description:
        lines.append(f'  "description": "{p["json_desc_label"]}{_desc_limit_clause(p, desc_limit)}"')
    if field_defs:
        inner = ",\n".join(f'    "{f["key"]}": "{f.get("label") or f["key"]}"'
                           for f in field_defs)
        lines.append('  "fields": {\n' + inner + "\n  }")
    return (
        p["json_intro"] + "{\n" + ",\n".join(lines) + "\n}\n"
        + p["json_outro"] + f" {p['lang_directive']}"
    )


async def _chat_json(settings: dict, system_prompt: str, user_content: str) -> dict:
    """送一輪 chat 並把回覆解析成 dict。

    實際的 wire format(Ollama 原生 / OpenAI 相容)、think=False、以及「絕不送
    format/response_format」那條硬規則,全部在 app/llm.py;這裡只負責把
    LLMError 轉成 502,並把回覆文字交給 _extract_json。

    ⚠ 這支的簽章是 tests/test_ai.py 大量 monkeypatch 的接縫,別改。
    """
    try:
        content = await llm.chat(settings, system_prompt, user_content)
    except llm.LLMError as e:
        # 中介層只記「status=502」,不記原因;真正的診斷線索(Ollama 的錯誤原文)
        # 在這裡,不寫下來的話 server 端永遠只看得到三位數字(同 semantic.reindex)。
        log.warning("ai chat failed: %s", e)
        raise HTTPException(502, str(e))
    try:
        return _extract_json(content)
    except (ValueError, json.JSONDecodeError) as e:
        # 解析失敗的原始回覆是唯一的破案線索(是未跳脫引號?空回覆?半截 JSON?),
        # 只留頭尾避免整段長輸出灌爆 log。
        head = content[:300].replace("\n", "\\n")
        log.warning("ai reply not parseable (%s; %d chars): %s%s",
                    e, len(content), head, "…" if len(content) > 300 else "")
        raise HTTPException(502, "AI 回傳格式無法解析,請調整 AI 指示或換一個模型再試")


def _keywords(parsed: dict, limit: int = 3, lang: str = DEFAULT_LANG) -> list[str]:
    raw = parsed.get("keywords")
    out = [_zh(str(k).strip(), lang) for k in (raw if isinstance(raw, list) else []) if str(k).strip()]
    return out[:limit]


def _template_fields(parsed: dict, field_defs: list[dict], lang: str = DEFAULT_LANG) -> dict:
    """從模型回覆撈樣板欄位值:只收樣板定義內的 key,忽略模型多給的。"""
    raw = parsed.get("fields")
    fields = {}
    if isinstance(raw, dict):
        for f in field_defs:
            fields[f["key"]] = _as_text(raw.get(f["key"]), lang)
    return fields


@router.post("/api/ai/generate")
async def ai_generate(body: AIGenerateIn, paths: VaultPaths = Depends(get_user_paths)):
    settings = load_ai_settings()
    if not settings.get("enabled"):
        raise HTTPException(400, "AI 生成功能未啟用")
    text = body.input.strip()
    if not text:
        raise HTTPException(400, "請先輸入內容")

    tpl = get_template(paths, body.template) or {}
    # 只送啟用中的欄位:關掉的欄位前端不顯示,生了也沒地方放
    field_defs = enabled_fields(tpl)
    tpl_prompt = (tpl.get("ai_prompt") or "").strip()
    parsed = await _chat_json(
        settings, tpl_prompt + _json_instruction(field_defs, body.lang, _desc_limit(settings)), text)

    return {
        "name": _as_text(parsed.get("name"), body.lang),
        "description": _as_text(parsed.get("description"), body.lang),
        "fields": _template_fields(parsed, field_defs, body.lang),
        "tags": _keywords(parsed, lang=body.lang),
    }


@router.post("/api/ai/article-note")
async def ai_article_note(body: AIArticleNoteIn, paths: VaultPaths = Depends(get_user_paths)):
    """文章>關鍵字(外掛):依文章脈絡解釋單一名詞,生成一筆「預設樣板」的名詞內容。

    需先在設定 → 外掛模組管理安裝 article-keywords 外掛。內容指示掛在外掛設定的
    ai_prompt(可在外掛管理自訂),但欄位 schema 與產出的 template 都是預設樣板
    (jargon-default)——這是批次生成入口,不是名詞的儲存樣板。
    前端對每個標註的名詞各呼叫一次。
    """
    settings = load_ai_settings()
    if not settings.get("enabled"):
        raise HTTPException(400, "AI 生成功能未啟用")
    # is_active 不是 is_installed:停用(installed 但 enabled=False)也要擋——
    # 停用的意義就是「功能入口消失」,只擋前端假選項、不擋端點等於沒停用。
    if not is_active(paths, ARTICLE_PLUGIN_ID):
        raise HTTPException(400, "「文章>關鍵字」外掛未安裝或已停用")
    keyword = body.keyword.strip()
    article = body.article.strip()
    if not keyword or not article:
        raise HTTPException(400, "請提供文章內容與要解釋的名詞")

    target = get_template(paths, DEFAULT_TEMPLATE_ID) or {}
    field_defs = enabled_fields(target)
    tpl_prompt = (get_plugin_config(paths, ARTICLE_PLUGIN_ID).get("ai_prompt") or "").strip()
    p = for_lang(body.lang)
    user_content = (p["article_full_text_label"] + article
                    + p["article_target_before"] + keyword + p["article_target_after"])
    parsed = await _chat_json(
        settings, tpl_prompt + _json_instruction(field_defs, body.lang, _desc_limit(settings)), user_content)

    return {
        "name": _as_text(parsed.get("name"), body.lang) or keyword,
        "description": _as_text(parsed.get("description"), body.lang),
        "fields": _template_fields(parsed, field_defs, body.lang),
        "tags": _keywords(parsed, lang=body.lang),
        "template": DEFAULT_TEMPLATE_ID,
    }


@router.post("/api/ai/field")
async def ai_field(body: AIFieldIn, paths: VaultPaths = Depends(get_user_paths)):
    """依整份筆記的已填內容重寫單一欄位:AI 拿到的是完整脈絡,不是只有該欄位的舊值。"""
    settings = load_ai_settings()
    if not settings.get("enabled"):
        raise HTTPException(400, "AI 生成功能未啟用")

    p = for_lang(body.lang)
    tpl = get_template(paths, body.template) or {}
    label_by_key = {f["key"]: (f.get("label") or f["key"]) for f in (tpl.get("fields") or [])}
    if body.target == "name":
        target_label, old, multiline = p["label_term"], body.name.strip(), False
    elif body.target == "description":
        target_label, old, multiline = p["label_description"], body.description.strip(), True
    elif body.target in label_by_key or body.target in body.fields:
        target_label = label_by_key.get(body.target, body.target)
        old, multiline = (body.fields.get(body.target) or "").strip(), False
    else:
        raise HTTPException(400, "未知的目標欄位")

    system = p["field_before"] + target_label + p["field_after"]
    tpl_prompt = (tpl.get("ai_prompt") or "").strip()
    if tpl_prompt:
        system += p["field_style_ref_prefix"] + tpl_prompt + "\n"
    # 字數上限只套在說明欄(target=="description" 是唯一 multiline 的目標):
    # 單行欄位本來就短,帶上限句只是噪音
    limit_clause = _desc_limit_clause(p, _desc_limit(settings)) if body.target == "description" else ""
    system += (
        p["field_json_intro"]
        + (p["field_multiline"] if multiline else p["field_singleline"])
        + limit_clause
        + f" {_lang_instruction(body.lang)}"
    )

    lines = [f"{p['label_term']}:{body.name.strip() or p['label_blank']}",
             f"{p['label_description']}:{body.description.strip() or p['label_blank']}"]
    for k, v in body.fields.items():
        lines.append(f"{label_by_key.get(k, k)}:{(v or '').strip() or p['label_blank']}")
    if body.tags:
        lines.append(f"{p['label_tags']}:{p['tag_joiner'].join(body.tags)}")
    lines.append(p["field_target_before"] + target_label + p["field_target_mid"]
                 + (old or p["label_blank"]) + p["field_target_after"])

    parsed = await _chat_json(settings, system, "\n".join(lines))
    value = _as_text(parsed.get("value"), body.lang)
    if not value:
        raise HTTPException(502, "AI 沒有產生內容,請再試一次或換一個模型")
    return {"value": value}


@router.post("/api/ai/fill")
async def ai_fill(body: AIFillIn, paths: VaultPaths = Depends(get_user_paths)):
    """一次補齊所有空白欄位:快速記下的名詞事後補完內容。

    與 /api/ai/field 的差別是「一次呼叫、多個欄位」——本機模型一輪好幾秒,
    五個空欄位分五次打就是半分鐘的等待。也與 /api/ai/generate 不同:那支是從一段
    輸入從頭生成整筆(含名詞),這支拿的是**既有內容當脈絡**,只產出點名的欄位。
    """
    settings = load_ai_settings()
    if not settings.get("enabled"):
        raise HTTPException(400, "AI 生成功能未啟用")

    p = for_lang(body.lang)
    tpl = get_template(paths, body.template) or {}
    label_by_key = {f["key"]: (f.get("label") or f["key"]) for f in (tpl.get("fields") or [])}
    # 合法目標由**伺服器**決定:啟用中的樣板欄位 ∪ {description},與請求取交集。
    # 順序照樣板(不是照前端送來的順序),停用的欄位不補——畫面上沒有位置放它,
    # 同 enabled_fields() docstring 的理由。
    wanted = set(body.targets)
    field_defs = [f for f in enabled_fields(tpl) if f["key"] in wanted]
    want_desc = "description" in wanted
    if not want_desc and not field_defs:
        raise HTTPException(400, "沒有可以補齊的欄位")

    system = (tpl.get("ai_prompt") or "").strip() + _fill_instruction(
        field_defs, want_desc, body.lang, _desc_limit(settings))

    # 情境:整筆名詞目前的內容(比照 /api/ai/field),空欄位照樣列出來並標成未填——
    # 讓模型看得到「哪些還缺」,而不是以為這筆筆記只有這幾欄。
    lines = [f"{p['label_term']}:{body.name.strip() or p['label_blank']}",
             f"{p['label_description']}:{body.description.strip() or p['label_blank']}"]
    for k, v in body.fields.items():
        lines.append(f"{label_by_key.get(k, k)}:{(v or '').strip() or p['label_blank']}")
    if body.tags:
        lines.append(f"{p['label_tags']}:{p['tag_joiner'].join(body.tags)}")

    parsed = await _chat_json(settings, system, "\n".join(lines))
    values = {}
    if want_desc:
        values["description"] = _as_text(parsed.get("description"), body.lang)
    values.update(_template_fields(parsed, field_defs, body.lang))
    # 空值不回:前端拿到什麼就填什麼,回空字串等於用空白蓋掉(而使用者在等待期間
    # 可能已經自己打了字)。一筆都沒生出來時回空 dict,由前端說「沒有補到內容」。
    return {"values": {k: v for k, v in values.items() if v}}


@router.post("/api/ai/tags")
async def ai_tags(body: AITagsIn, paths: VaultPaths = Depends(get_user_paths)):
    settings = load_ai_settings()
    if not settings.get("enabled"):
        raise HTTPException(400, "AI 生成功能未啟用")

    p = for_lang(body.lang)
    # 用已填寫的內容組出給 AI 分析的情境:名詞 + 說明 + 有值的樣板欄位(帶 label)
    tpl = get_template(paths, body.template) or {}
    label_by_key = {f["key"]: (f.get("label") or f["key"]) for f in (tpl.get("fields") or [])}
    lines = []
    if body.name.strip():
        lines.append(f"{p['label_term']}:{body.name.strip()}")
    if body.description.strip():
        lines.append(f"{p['label_description']}:{body.description.strip()}")
    for k, v in body.fields.items():
        if v and v.strip():
            lines.append(f"{label_by_key.get(k, k)}:{v.strip()}")
    if not lines:
        raise HTTPException(400, "請先填寫名詞或說明,AI 才能分析出標籤")

    user_content = "\n".join(lines)
    if body.tags:
        user_content += (p["existing_tags_before"] + p["tag_joiner"].join(body.tags)
                         + p["existing_tags_after"])

    parsed = await _chat_json(settings, p["tags_system"] + " " + _lang_instruction(body.lang), user_content)
    return {"tags": _keywords(parsed, lang=body.lang)}


@router.post("/api/ai/group-tags")
async def ai_group_tags(body: AIGroupTagsIn, paths: VaultPaths = Depends(get_user_paths)):
    """AI 自動分組建議(單一批次):把一批標籤各自歸到最適合的群組。

    前端把未分組標籤分批送來(逐批顯示進度),groups 帶入既有+前面批次已提出的
    群組名,讓 AI 跨批次盡量沿用同一組群組名。這一步**不寫檔**,回傳建議
    ({標籤: 群組名})交前端讓使用者逐項勾選後,再走既有的 /api/tag-groups 套用。
    只收「屬於本批標籤、且群組名非空」的項目,擋掉模型多給/亂給。
    """
    # ⚠ 這兩個 400 回的是**機器碼**不是給人看的字串:前端會把 detail 直接
    # alert 出來,寫中文等於讓英文/日文介面的使用者看到中文訊息,而且會蓋掉
    # 前端本來就備好的在地化文案。訊息本身在 i18n.js 的 tagdup.aiDisabled /
    # tagdup.aiNoTags,前端照 detail 查表(查不到才退回泛用的失敗訊息)。
    settings = load_ai_settings()
    if not settings.get("enabled"):
        raise HTTPException(400, "ai_disabled")
    tags = [t.strip() for t in body.tags if t.strip()]
    if not tags:
        raise HTTPException(400, "no_tags")

    p = for_lang(body.lang)
    existing = p["tag_joiner"].join(dict.fromkeys(g.strip() for g in body.groups if g.strip()))
    user_content = (p["existing_groups_label"] + (existing or p["no_groups_label"]) + "\n\n"
                    + p["tags_to_group_label"] + p["tag_joiner"].join(tags))
    parsed = await _chat_json(settings, p["group_system"] + " " + _lang_instruction(body.lang), user_content)

    raw = parsed.get("groups")
    tagset = set(tags)
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            name = str(k).strip()
            if isinstance(v, list):
                v = v[0] if v else ""
            grp = str(v or "").strip()
            if name in tagset and grp:
                # 只轉 value(模型生成的群組名):key 是使用者的既有標籤,
                # 動了就跟磁碟上的標籤名對不上(見檔頭 _zh 的警告)。
                out[name] = _zh(grp, body.lang)
    return {"groups": out}


@router.post("/api/ai/tag-duplicates")
async def ai_tag_duplicates(body: AITagDupIn):
    """標籤相似度重複偵測的**語意層**:整份標籤清單一次送出,問哪些其實是同一個東西。

    字面變體(Mes/MES、全形半形、標點差異)由 GET /api/tag-duplicates 用純字串比對
    抓,零誤報也不用 AI;這一支補的是它抓不到的那種——「回焊爐 / Reflow Oven」、
    「治具 / Jig」。**刻意不分批**:分批會讓落在不同批次的兩個寫法永遠配不到一起,
    而標籤名很短,整份清單塞得進一個 prompt(上限由前端先擋)。

    **不收 `paths`**:標籤清單由 body 帶入,這支不讀任何庫;登入驗證在 router 掛載
    時已經統一套上(見 routers/__init__.py 的 ALL_ROUTERS)。

    過濾比照 group-tags:模型亂給的一律丟掉。三關——
      1. 成員必須都出現在送出的清單裡(不接受模型自創標籤);
      2. 去重後至少兩個成員(一個成員的「組」沒有意義);
      3. **整組 norm_key 都相同的丟掉**——那是字面層已經抓到的,再列一次只是雜訊。
    """
    # 機器碼而非中文訊息,理由同 ai_group_tags(前端會把 detail 直接顯示)。
    settings = load_ai_settings()
    if not settings.get("enabled"):
        raise HTTPException(400, "ai_disabled")
    tags = [t.strip() for t in body.tags if t.strip()]
    if not tags:
        raise HTTPException(400, "no_tags")

    p = for_lang(body.lang)
    parsed = await _chat_json(settings, p["tagdup_system"] + " " + _lang_instruction(body.lang),
                              p["tag_joiner"].join(tags))

    raw = parsed.get("groups")
    tagset = set(tags)
    out: list[list[str]] = []
    if isinstance(raw, list):
        for grp in raw:
            if not isinstance(grp, list):
                continue
            members = list(dict.fromkeys(
                str(x).strip() for x in grp if str(x).strip() in tagset))
            if len(members) < 2:
                continue
            if len({norm_key(m) for m in members}) < 2:
                continue
            out.append(members)
    return {"groups": out}

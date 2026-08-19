"""
樣板登記層:欄位樣板定義的持久化真相(data/templates.json)。

名詞的「額外欄位」(如別名/Synonymy/Polysemy)不再寫死在程式碼,
改由樣板定義驅動:每個樣板是 {id, name, icon, builtin, fields, ai_input_mode, ai_prompt}。
icon 是編輯器樣板下拉用的代表圖示(emoji),內建樣板的真相在下方種子、外掛樣板的
真相在封裝 manifest——比照「外掛名稱/描述的真相在 manifest,不在 i18n」那條規則,
不做前端寫死的 id → emoji 對照表(那樣第三方封裝永遠拿不到自己的圖示)。
fields 是 [{key, label, placeholder, enabled}] 的有序清單;順序即顯示順序
(使用者可在設定 UI 拖曳調整),enabled=False 的欄位不顯示、也不送給 AI,
但**不刪值**——既有名詞裡該 key 的值原樣保留,重新啟用就回來了。
核心欄位(name/description/tags/attachments)不受樣板控制。

AI 生成的指示(ai_prompt)與輸入方式(ai_input_mode:name=用名詞欄位、
paste=貼上一段內容)也掛在樣板上,讓每個樣板各自決定怎麼請 AI 生成。

這份檔案跟 tags.json 一樣是真相來源、要進版控,不是可拋棄快取
(使用者自訂的樣板無法從 notes 重建)。

本模組只碰檔案系統,不碰 SQLite、不碰 HTTP。
"""
import copy
import json

from . import atomic
from .paths import VaultPaths

# 內建樣板種子:首次啟動建檔用;若使用者誤刪 builtin,啟動時會補回。
# ai_prompt 只寫「內容指示」,JSON 輸出格式的硬規則由 routers/ai.py 另外附加。
# 欄位設計的共同規則(改這份種子時請一併遵守):
#   1. fields 只放「可篩選的短事實」,論述一律回到 description——欄位在 UI 上
#      就是單行輸入框,塞段落進去必然沒人願意填。
#   2. 一個欄位只回答一個問題,不允許兩個欄位回答同一件事。
#   3. key 一旦發布就不能改:既有名詞的值是靠 key 對回欄位定義的,改 key 會讓
#      那些值變成沒有標題的殘留欄位(見 static/js/fields.js:fieldDefsFor)。
#      要調整只能改 label/placeholder,或新增欄位。
#   4. enabled 省略 = True(見 load_templates 的 setdefault)。只有「想預設關掉」
#      的欄位才需要明寫 False——這只影響新帳號與新補上的欄位,既有帳號的既有
#      欄位一律沿用檔案裡的值(沒有這個鍵就當 True),不會被改版偷偷關掉。
DEFAULT_TEMPLATES = [
    {
        "id": "jargon-default",
        # ⚠ 這個字串是**改名偵測的錨點**,不是給人看的顯示名:routers/templates.py 的
        # name_is_default 拿它跟使用者存檔的 name 逐字比對,不相等就當「使用者改過名」
        # 而停止在地化。既有帳號的 templates.json 存的是 "Glossary",所以這裡改字
        # 等於讓全部既有帳號的預設樣板從此顯示英文原字、不再跟著介面語言走。
        # 顯示名 2026-08-18 由 Glossary 改成 Jargon,改的是 i18n 的
        # tpl.builtin.jargon-default,不是這裡。
        "name": "Glossary",
        "icon": "📖",
        "builtin": True,
        "enabled": True,  # 預設樣板永遠啟用(不可停用)
        "ai_input_mode": "name",
        "ai_prompt": (
            "Explain the technical term provided by the user, for a professional who has "
            "the background but is unfamiliar with this specific term.\n\n"
            "The description field has two paragraphs separated by a blank line:\n"
            "Paragraph 1 (under 150 words): explain in plain language what it is and what "
            "problem it solves; do not explain jargon with more jargon.\n"
            "Paragraph 2 (under 150 words): give the precise definition, scope and boundary "
            "conditions, pointing out common misuses when relevant.\n\n"
            "fields are single-line phrases (not paragraphs); leave blank rather than "
            "making things up:\n"
            "- en_term: the English term; expand acronyms, e.g. \"MSL (Moisture Sensitivity Level)\".\n"
            "- domain: the field this term belongs to, used to disambiguate from same-named "
            "terms, e.g. \"SMT process\", \"statistics\".\n"
            "- alias: other names or informal terms for the same concept.\n"
            "- synonymy: terms that can be used interchangeably.\n"
            "- polysemy: other meanings of this term in different contexts; state the context.\n"
            "- source: authoritative source of the definition, e.g. standard number, "
            "specification, classic textbook; leave blank if unsure — never fabricate a source.\n\n"
            "keywords: three keywords best suited for classification and retrieval."
        ),
        # 預設只開「別名/異名同義/一詞多義」這三個(樣板機制出現前就有的原始欄位);
        # 其餘欄位定義照樣帶著,使用者要用時在設定 → 欄位樣板打開即可。
        # ⚠ label/placeholder 是**英文基準值**(比照 name):未修改時前端依介面語言
        # 查 i18n(tplf.<tid>.<key>.*,見 static/js/fields.js),zh 翻譯在 i18n.js。
        "fields": [
            {"key": "en_term", "label": "English term", "enabled": False,
             "placeholder": "Original term; expand acronyms, e.g. MSL (Moisture Sensitivity Level)"},
            {"key": "domain", "label": "Domain", "enabled": False,
             "placeholder": "Field this term belongs to, to disambiguate, e.g. SMT process, statistics"},
            {"key": "alias", "label": "Alias",
             "placeholder": "Other names or informal terms for the same concept"},
            {"key": "synonymy", "label": "Synonyms",
             "placeholder": "Terms that can be used interchangeably"},
            {"key": "polysemy", "label": "Polysemy",
             "placeholder": "Other meanings in different contexts; state the context"},
            {"key": "source", "label": "Source", "enabled": False,
             "placeholder": "Authoritative source, e.g. standard number, specification, textbook"},
        ],
    },
    {
        # 2026-08-18:從官方外掛升成內建(唯一一次「外掛 → 內建」的反方向搬遷)。
        # ⚠ name 必須逐字等於這裡的 "Passage Decoder":routers/templates.py 的
        # name_is_default 靠字串比對,不相等前端就不會去查 tpl.builtin.passage-decoder,
        # 12 語名稱會整組失效。搬過來時放棄了 manifest 的 intro 長文(內建樣板沒有
        # 介紹頁機制),那份文字保留在 CHANGELOG 的紀錄裡。
        "id": "passage-decoder",
        "name": "Passage Decoder",
        "icon": "💬",
        "builtin": True,
        "enabled": False,  # 預設不啟用,由使用者在「欄位樣板」自行開啟
        "ai_input_mode": "paste",
        "ai_prompt": (
            "The user will paste a short passage (roughly 50–500 characters) full of "
            "jargon — a remark from a meeting, a definition from a paper, a technical "
            "paragraph from an article — that they could not understand as a whole even "
            "though they know the individual words.\n\n"
            "name field: state in one sentence what the passage is actually saying, as "
            "the title; do not just copy the passage's opening words.\n\n"
            "description field has a fixed four-part structure, in this exact order:\n"
            "1. The original passage copied verbatim as a quote block: prefix every line "
            "of it with \"> \" (a greater-than sign and a space). Do not translate, "
            "correct or shorten it.\n"
            "2. A header line **🌱 For beginners** followed by one paragraph: explain "
            "with everyday-life analogies, zero technical terms.\n"
            "3. A header line **💬 In plain words** followed by one paragraph: explain "
            "for an adult outside the field; when a technical term is unavoidable, gloss "
            "it in parentheses on the spot.\n"
            "4. A header line **🎓 For professionals** followed by one paragraph: precise "
            "terminology, the underlying mechanism and the cause-and-effect, written for "
            "a peer.\n"
            "Each of the three paragraphs is 150–250 characters in CJK languages, or "
            "80–150 words in other languages. Keep the ** ** bold markers and the emojis "
            "exactly as shown; the header text after each emoji is written in the output "
            "language. Leave one blank line between sections.\n\n"
            "fields are single-line phrases:\n"
            "- gist: if the reader can remember only one sentence, the passage says …\n"
            "- domain: the field the passage belongs to, e.g. \"semiconductor process\", "
            "\"financial accounting\".\n"
            "- key_terms: the jargon terms appearing in the passage, comma-separated; "
            "write a term as [[term]] when it deserves its own glossary entry.\n"
            "- source: where it was seen or heard; leave blank if unknown — never "
            "fabricate.\n\n"
            "keywords: three keywords (domain, topic, and the scene or system involved)."
        ),
        "fields": [
            {"key": "gist", "label": "One-line takeaway",
             "placeholder": "If you can only remember one sentence, this passage says: …"},
            {"key": "domain", "label": "Domain",
             "placeholder": "The field this passage belongs to, e.g. semiconductor process, accounting"},
            {"key": "key_terms", "label": "Key terms",
             "placeholder": "Jargon inside the passage, comma-separated; use [[term]] to link entries"},
            {"key": "source", "label": "Source",
             "placeholder": "Where you saw or heard it, e.g. weekly meeting, a paper, customer email"},
        ],
    },
    {
        # 2026-08-18 新增。名詞的主角是**附件裡那張圖**(圖表/示意圖/流程圖),
        # 欄位負責的是「怎麼讀這張圖」而不是重述圖的內容。
        # ⚠ llm.py 沒有 vision:AI 看不到附件,只讀得到使用者貼進來的文字描述——
        # ai_prompt 第一句就要講清楚,否則模型會開始編造圖上有什麼。
        "id": "graph",
        "name": "Graph",
        "icon": "📈",
        "builtin": True,
        "enabled": False,  # 預設不啟用,由使用者在「欄位樣板」自行開啟
        "ai_input_mode": "paste",
        "ai_prompt": (
            "The user will paste a description of a figure — a chart, diagram, schematic "
            "or flowchart — typically its caption, axis labels, legend and the numbers or "
            "steps it shows. You cannot see the image itself; the figure is attached to "
            "the entry separately and only the pasted text is available to you. Never "
            "describe visual details that are not in the pasted text, and never invent "
            "numbers.\n\n"
            "name field: state in one sentence what the figure demonstrates, as the "
            "title; do not just repeat the caption.\n\n"
            "description field has two paragraphs separated by a blank line:\n"
            "Paragraph 1 (under 150 words): how to read the figure — what is plotted "
            "against what, what a single point or box represents, and which direction "
            "means \"more\" or \"better\".\n"
            "Paragraph 2 (under 150 words): what it actually shows and why that matters, "
            "including what the figure does NOT establish.\n\n"
            "fields are single-line phrases; leave blank rather than making things up:\n"
            "- figure_type: kind of figure, e.g. \"line chart\", \"box plot\", "
            "\"block diagram\", \"swimlane flowchart\".\n"
            "- axes: what each axis or dimension represents, with units, e.g. "
            "\"x: time (weeks), y: defect rate (ppm)\"; for non-plot diagrams, the "
            "dimension it is organised by, e.g. \"left to right = process order\".\n"
            "- takeaway: the single thing the figure is there to show.\n"
            "- pitfall: how the figure is most easily misread, e.g. \"y-axis does not "
            "start at zero\", \"log scale\", \"n=12\", \"correlation only\".\n"
            "- source: where the figure came from — paper, report, dashboard, or your "
            "own measurement; leave blank if unsure, never fabricate a citation.\n\n"
            "keywords: three keywords (subject, figure type, and the metric or system shown)."
        ),
        "fields": [
            {"key": "figure_type", "label": "Figure type",
             "placeholder": "e.g. line chart, box plot, block diagram, swimlane flowchart"},
            {"key": "axes", "label": "Axes / dimensions",
             "placeholder": "e.g. x: time (weeks), y: defect rate (ppm); or left to right = process order"},
            {"key": "takeaway", "label": "Takeaway",
             "placeholder": "The single thing this figure is here to show"},
            {"key": "pitfall", "label": "Reading pitfall",
             "placeholder": "How it is most easily misread, e.g. y-axis not from zero, log scale, n=12"},
            {"key": "source", "label": "Source",
             "placeholder": "Paper, report, dashboard, or your own measurement"},
        ],
    },
    {
        "id": "code-snippet",
        "name": "Code snippet",
        "icon": "💻",
        "builtin": True,
        "enabled": False,  # 預設不啟用,由使用者在「欄位樣板」自行開啟
        "ai_input_mode": "paste",
        "ai_prompt": (
            "The user will paste a piece of code.\n\n"
            "name field: describe in one sentence what problem this code solves, as the "
            "title; do not just write the function name.\n\n"
            "description field (under 150 words): explain how it works and where the key "
            "logic is, and state clearly what goes in and what comes out. Do not repeat "
            "the source code.\n\n"
            "fields are single-line phrases:\n"
            "- language: programming language, with a minimum version when relevant, "
            "e.g. \"Python 3.10+\".\n"
            "- dependencies: packages that must be installed, comma-separated; write "
            "\"none\" if standard library only.\n"
            "- usecase: when to use it, and when not to.\n"
            "- caveat: known pitfalls, edge cases or performance limits, e.g. \"empty "
            "string not handled\", \"O(n²) complexity\".\n"
            "- source: origin or license; leave blank if self-written.\n\n"
            "keywords: three keywords (language, purpose, key technique or function)."
        ),
        "fields": [
            {"key": "language", "label": "Language",
             "placeholder": "e.g. Python 3.10+, JavaScript, SQL"},
            {"key": "dependencies", "label": "Dependencies",
             "placeholder": "e.g. requests, pandas; none if standard library only"},
            {"key": "usecase", "label": "Use case",
             "placeholder": "When to use it, and when not to"},
            {"key": "caveat", "label": "Caveats",
             "placeholder": "Known pitfalls, edge cases or performance limits"},
            {"key": "source", "label": "Source / license",
             "placeholder": "Origin link or license; leave blank if self-written"},
        ],
    },
]
# 註:內建樣板的進出紀錄(遷移實作一律在 app/plugins.py 的 ensure_plugins())——
#   出:「文章>關鍵字」(article-keywords)整包搬去外掛,樣板從 templates.json 移除;
#       「標準作業程序」(process-sop)、「英文單字」(english-word)、
#       「植物辨識」(plant-id)原地降級成外掛(builtin → False,樣板留在原地不刪值)。
#   入:「一段話」(passage-decoder)2026-08-18 從外掛升成內建——唯一一次反方向搬遷。
# ⚠ 出去過的 id 不要再拿來當新樣板的 id:既有帳號的 templates.json 還留著同 id 的舊定義。


def load_templates(paths: VaultPaths) -> list[dict]:
    try:
        data = json.loads(paths.templates_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            for t in data:
                if isinstance(t, dict):  # 舊檔沒有 AI 欄位時給預設,前端顯示才不會缺鍵
                    t.setdefault("ai_input_mode", "name")
                    t.setdefault("ai_prompt", "")
                    # 代表 icon(編輯器樣板下拉用):舊檔與自訂樣板沒有這個鍵,
                    # 給空字串讓前端自己決定 fallback,不在這裡塞預設圖示。
                    t.setdefault("icon", "")
                    # 舊檔/自訂樣板一律視為啟用(此欄位是後來才加的);既有內建樣板
                    # 因此保持開啟,不會因為新的「預設不啟用」政策而被關掉——不破壞現有使用者。
                    t.setdefault("enabled", True)
                    # 欄位層級的啟用旗標同理:舊檔沒有這個鍵 = 全部啟用,
                    # 不會因為種子裡新加的「預設關閉」而把既有帳號的欄位關掉。
                    for f in t.get("fields") or []:
                        if isinstance(f, dict):
                            f["enabled"] = f.get("enabled", True) is not False
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return copy.deepcopy(DEFAULT_TEMPLATES)


def save_templates(paths: VaultPaths, templates: list[dict]) -> None:
    atomic.write_json(paths.templates_path, templates)


def enabled_fields(tpl: dict | None) -> list[dict]:
    """樣板目前啟用中的欄位(順序照樣板)。

    AI 生成的 schema 一律走這裡:關掉的欄位不該出現在 JSON 指示裡,
    不然模型還是會生出值,而畫面上沒有欄位可以顯示它。
    """
    return [f for f in ((tpl or {}).get("fields") or [])
            if isinstance(f, dict) and f.get("enabled", True) is not False]


def get_template(paths: VaultPaths, tid: str) -> dict | None:
    for t in load_templates(paths):
        if t["id"] == tid:
            return t
    return None


def reset_template(paths: VaultPaths, tid: str, seed: dict) -> dict | None:
    """把樣板恢復成出廠定義(設定 → 欄位樣板 的 ↺ 按鈕)。

    seed 是出廠定義(DEFAULT_TEMPLATES 或外掛樣板種子),由呼叫端查好傳入——
    templates.py 不 import plugins,維持 plugins → templates 的單向依賴。

    覆寫 name/icon/fields/ai_prompt/ai_input_mode(欄位層級的 enabled 跟著種子回出廠,
    那正是「恢復預設」的語意);保留現有 id/builtin 與**樣板層級**的 enabled——
    使用者剛啟用的樣板不該因為按了恢復預設就被關回種子的預設停用。
    這是與 ensure_templates()「只補不覆蓋」相反的明確動作,所以是獨立函式,
    絕不併進啟動流程。找不到 tid 回 None。
    """
    templates = load_templates(paths)
    for tpl in templates:
        if tpl["id"] == tid:
            fresh = copy.deepcopy(seed)
            tpl["name"] = fresh["name"]
            tpl["fields"] = fresh.get("fields", [])
            tpl["ai_prompt"] = fresh.get("ai_prompt", "")
            tpl["ai_input_mode"] = fresh.get("ai_input_mode", "name")
            tpl["icon"] = fresh.get("icon", "")
            save_templates(paths, templates)
            return tpl
    return None


def ensure_templates(paths: VaultPaths) -> None:
    """
    啟動時的自我修復:建檔、補回被誤刪的內建樣板、補上內建樣板缺少的新欄位
    (ai_prompt/ai_input_mode),以及把官方樣板後來新增的 fields 補進既有帳號。
    一律只補不覆蓋,不動使用者對既有內建樣板的任何修改。
    """
    templates = load_templates(paths)
    by_id = {t["id"]: t for t in templates}
    changed = not paths.templates_path.exists()
    for seed in DEFAULT_TEMPLATES:
        existing = by_id.get(seed["id"])
        if existing is None:
            templates.append(copy.deepcopy(seed))
            changed = True
            continue
        # 版本升級:內建樣板缺少新加的欄位時補回種子預設值(既有值不動)。
        # icon 也走這裡——沒有這一步,代表 icon 只有新註冊的人拿得到,
        # 既有帳號的樣板下拉永遠是空的。
        for key in ("ai_prompt", "ai_input_mode", "icon"):
            if not existing.get(key) and seed.get(key):
                existing[key] = seed[key]
                changed = True
        # 官方樣板改版後新增的欄位,補到既有帳號的樣板尾端。
        # 沒有這段的話,改 DEFAULT_TEMPLATES 只有「新註冊的使用者」拿得到新欄位,
        # 既有帳號永遠停在舊版本。以 key 判斷有無,所以:使用者改過的 label/
        # placeholder、自己加的欄位、既有欄位順序都不會被動到,只會多出新欄位
        # (排在最後,而不是種子裡的位置——重排等於覆寫使用者的編排)。
        existing_fields = existing.setdefault("fields", [])
        have = {f.get("key") for f in existing_fields if isinstance(f, dict)}
        for field in seed["fields"]:
            if field["key"] not in have:
                existing_fields.append(copy.deepcopy(field))
                changed = True
    if changed:
        save_templates(paths, templates)

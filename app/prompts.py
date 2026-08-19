"""固定 prompt 字典:12 種語言各自的完整 prompt 全文與零件。

這裡放的是**寫死在程式碼、非使用者自訂**的那部分 prompt:格式規則(JSON 輸出
長什麼樣)與固定分析任務(標籤建議、標籤分組、標籤重分類)。使用者在各欄位樣板
自訂的 `ai_prompt` 內容本身不受這裡影響,仍照使用者寫的語言。

「用哪種語言寫指示」與「請 AI 用哪種語言回覆」都直接鎖定同一種語言——整段固定
prompt 都用該語言撰寫,不是只在使用者自訂的 ai_prompt 後面加一句「請用 XX 回覆」。
後者對本機能力較弱的小模型不夠可靠。每段固定 prompt 最後仍會補一句 lang_directive
當保險,蓋過使用者自訂 ai_prompt 裡可能提到的語言;這條硬規則使用者不能覆寫。

新增語言時,PROMPTS 要補滿一整組 key(拿 en 或 zh-Hant 的 key 清單核對,不要漏);
tests/test_ai.py 有一支測試會逐語言比對 key 集合。

缺語言或缺 key 一律退回 DEFAULT_LANG(英文)——產品面向全世界,使用者以英文
為主,fallback 不該是繁中;前端 i18n.js 的 FALLBACK 也是 en,兩邊一致。

本模組是純資料 + 三支查表函式,不 import 任何其他模組。
"""

DEFAULT_LANG = "en"

# 每種語言各自的固定 prompt 全文與零件(非使用者自訂,寫死在程式碼)。
# 鍵值說明:
#   lang_directive        追加在每個 prompt 最後一句的「請用此語言回覆」硬規則
#   tags_system/group_system/tagdup_system      三個固定分析任務的完整 system prompt
#   json_intro/json_name_label/json_desc_label/json_keywords_sample/json_outro
#                          /api/ai/generate 與 /api/ai/article-note 共用的 JSON 格式規則
#   field_*                /api/ai/field 組 system prompt 用的各片段
#   label_term/label_description/label_tags/label_blank
#                          組「名詞:.../說明:.../標籤:...」情境行時共用的欄位標籤
#   existing_tags_before/after/tag_joiner
#                          /api/ai/tags 附加「已有標籤」提示、與一般標籤列表的分隔符
#   article_full_text_label/article_target_before/after
#                          /api/ai/article-note 組 user_content 用
#   existing_groups_label/no_groups_label/tags_to_group_label
#                          /api/ai/group-tags 組 user_content 用
PROMPTS: dict[str, dict] = {
    "zh-Hant": {
        "lang_directive": "請一律用繁體中文回覆(不管以上指示是用什麼語言寫的)。",
        "tags_system": (
            "你是標籤分類助手。請根據使用者提供的名詞內容,分析出最適合用來歸類與檢索它的"
            "關鍵字,最多三個,越精華越好。關鍵字要是簡短的主題詞(不是句子或說明)。\n"
            "請只回覆一個 JSON 物件,不要加任何額外文字、不要用 markdown code block 包住,"
            '格式:{"keywords": ["關鍵字1", "關鍵字2", "關鍵字3"]}\n'
            "keywords 最多三個;若內容不足以歸納,可以少於三個。"
        ),
        "group_system": (
            "你是標籤分類助手。使用者會提供一組名詞標籤,以及目前已存在的群組名稱。\n"
            "請把每一個標籤歸到最適合的群組:能沿用既有群組就沿用,不適合再提出新的群組名。\n"
            "群組名要精簡(2~6 字的主題詞)、數量盡量少、能涵蓋多個相關標籤。\n"
            "只回覆一個 JSON 物件,不要加任何額外文字、不要用 markdown code block 包住。\n"
            '格式:{"groups": {"標籤A": "群組名", "標籤B": "群組名"}}\n'
            "每個提供的標籤都要給一個群組名;真的無法歸類的標籤給空字串。"
        ),
        "tagdup_system": (
            "你是標籤去重助手。使用者會提供一份技術術語庫的標籤清單。\n"
            "請找出其中**其實指同一個東西、只是寫法不同**的標籤,把它們收成一組。\n"
            "常見的情況:中英文對照(回焊爐 / Reflow Oven)、全稱與縮寫、同義的不同叫法。\n"
            "規則:\n"
            "- 只能使用清單裡原原本本出現過的標籤名稱,絕對不可自創、改寫或修正錯字。\n"
            "- 每一組至少要有兩個標籤;一組都沒找到就回空陣列。\n"
            "- **寧可漏報,也不要把只是「相關」或「屬於同一個主題」的標籤湊成一組**——\n"
            "  那些是不同的東西,合併會讓使用者永久失去它們之間的區別。\n"
            "- 只回覆一個 JSON 物件,不要加任何額外文字、不要用 markdown code block 包住。\n"
            '格式:{"groups": [["標籤A", "標籤B"], ["標籤C", "標籤D"]]}'
        ),
        "json_intro": "\n\n請只回覆一個 JSON 物件,不要加任何額外文字、不要用 markdown code block 包住,格式如下:\n",
        "json_name_label": "名詞或標題",
        "json_desc_label": "說明文字,可用 \\n 換行分段",
        "desc_limit_before": "(請將說明文字控制在約 ",
        "desc_limit_after": " 字以內)",
        "json_keywords_sample": ["關鍵字1", "關鍵字2", "關鍵字3"],
        "json_outro": "fields 內只填上面列出的 key,沒有內容的就給空字串。keywords 最多三個。",
        "field_before": "你是名詞筆記的內容改寫助手。使用者會提供一則名詞筆記目前已填寫的完整內容,請你只針對「",
        "field_after": "」這個欄位產生一個更好的新版本:參考整份筆記的脈絡來寫,不要只看該欄位的舊值;若舊值為空就依脈絡直接生成;維持該欄位原本的用途與語言。\n",
        "field_style_ref_prefix": "這份筆記所屬樣板的內容指示(風格請參考):",
        "field_json_intro": '請只回覆一個 JSON 物件,不要加任何額外文字、不要用 markdown code block 包住,格式:{"value": "新的欄位內容"}\n',
        "field_multiline": "value 可用 \\n 換行分段。",
        "field_singleline": "value 必須是單行的簡短文字。",
        "field_target_before": "\n請重新生成的欄位:「",
        "field_target_mid": "」(目前內容:",
        "field_target_after": ")",
        "label_term": "名詞",
        "label_description": "說明",
        "label_tags": "標籤",
        "label_blank": "(未填)",
        "existing_tags_before": "\n(已有標籤:",
        "existing_tags_after": ";請盡量補上不同角度的關鍵字,不要重複已有標籤)",
        "tag_joiner": "、",
        "article_full_text_label": "文章全文:\n",
        "article_target_before": "\n\n請解釋的名詞:「",
        "article_target_after": "」",
        "existing_groups_label": "目前已存在的群組:",
        "no_groups_label": "(目前沒有群組)",
        "tags_to_group_label": "請分組的標籤:\n",
    },
    "zh-Hans": {
        "lang_directive": "请一律用简体中文回复(不管以上指示是用什么语言写的)。",
        "tags_system": (
            "你是标签分类助手。请根据用户提供的名词内容,分析出最适合用来归类与检索它的"
            "关键字,最多三个,越精华越好。关键字要是简短的主题词(不是句子或说明)。\n"
            "请只回复一个 JSON 对象,不要加任何额外文本、不要用 markdown code block 包住,"
            '格式:{"keywords": ["关键字1", "关键字2", "关键字3"]}\n'
            "keywords 最多三个;若内容不足以归纳,可以少于三个。"
        ),
        "group_system": (
            "你是标签分类助手。用户会提供一组名词标签,以及目前已存在的群组名称。\n"
            "请把每一个标签归到最适合的群组:能沿用既有群组就沿用,不适合再提出新的群组名。\n"
            "群组名要精简(2~6 字的主题词)、数量尽量少、能涵盖多个相关标签。\n"
            "只回复一个 JSON 对象,不要加任何额外文本、不要用 markdown code block 包住。\n"
            '格式:{"groups": {"标签A": "群组名", "标签B": "群组名"}}\n'
            "每个提供的标签都要给一个群组名;真的无法归类的标签给空字符串。"
        ),
        "tagdup_system": (
            "你是标签去重助手。用户会提供一份技术术语库的标签清单。\n"
            "请找出其中**其实指同一个东西、只是写法不同**的标签,把它们收成一组。\n"
            "常见的情况:中英文对照(回流焊炉 / Reflow Oven)、全称与缩写、同义的不同叫法。\n"
            "规则:\n"
            "- 只能使用清单里原原本本出现过的标签名称,绝对不可自创、改写或修正错字。\n"
            "- 每一组至少要有两个标签;一组都没找到就回空数组。\n"
            "- **宁可漏报,也不要把只是「相关」或「属于同一个主题」的标签凑成一组**——\n"
            "  那些是不同的东西,合并会让用户永久失去它们之间的区别。\n"
            "- 只回复一个 JSON 对象,不要加任何额外文本、不要用 markdown code block 包住。\n"
            '格式:{"groups": [["标签A", "标签B"], ["标签C", "标签D"]]}'
        ),
        "json_intro": "\n\n请只回复一个 JSON 对象,不要加任何额外文本、不要用 markdown code block 包住,格式如下:\n",
        "json_name_label": "名词或标题",
        "json_desc_label": "说明文本,可用 \\n 换行分段",
        "desc_limit_before": "(请将说明文本控制在约 ",
        "desc_limit_after": " 字以内)",
        "json_keywords_sample": ["关键字1", "关键字2", "关键字3"],
        "json_outro": "fields 内只填上面列出的 key,没有内容的就给空字符串。keywords 最多三个。",
        "field_before": "你是名词笔记的内容改写助手。用户会提供一则名词笔记目前已填写的完整内容,请你只针对“",
        "field_after": "”这个字段产生一个更好的新版本:参考整份笔记的脉络来写,不要只看该字段的旧值;若旧值为空就依脉络直接生成;维持该字段原本的用途与语言。\n",
        "field_style_ref_prefix": "这份笔记所属模板的内容指示(风格请参考):",
        "field_json_intro": '请只回复一个 JSON 对象,不要加任何额外文本、不要用 markdown code block 包住,格式:{"value": "新的字段内容"}\n',
        "field_multiline": "value 可用 \\n 换行分段。",
        "field_singleline": "value 必须是单行的简短文本。",
        "field_target_before": "\n请重新生成的字段:“",
        "field_target_mid": "”(目前内容:",
        "field_target_after": ")",
        "label_term": "名词",
        "label_description": "说明",
        "label_tags": "标签",
        "label_blank": "(未填)",
        "existing_tags_before": "\n(已有标签:",
        "existing_tags_after": ";请尽量补上不同角度的关键字,不要重复已有标签)",
        "tag_joiner": "、",
        "article_full_text_label": "文章全文:\n",
        "article_target_before": "\n\n请解释的名词:“",
        "article_target_after": "”",
        "existing_groups_label": "目前已存在的群组:",
        "no_groups_label": "(目前没有群组)",
        "tags_to_group_label": "请分组的标签:\n",
    },
    "en": {
        "lang_directive": "Always respond in English, regardless of what language the instructions above are written in.",
        "tags_system": (
            "You are a tagging assistant. Based on the term content the user provides, identify the "
            "keywords best suited for categorizing and searching it — at most three, as focused as "
            "possible. Keywords should be short topic words, not sentences or explanations.\n"
            "Reply with a single JSON object only — no extra text, no markdown code fences. "
            'Format: {"keywords": ["keyword1", "keyword2", "keyword3"]}\n'
            "At most three keywords; fewer is fine if the content doesn't support three."
        ),
        "group_system": (
            "You are a tag-grouping assistant. The user will provide a set of note tags along with "
            "the names of groups that already exist.\n"
            "Assign each tag to the group it fits best: reuse an existing group where it fits, "
            "otherwise propose a new group name.\n"
            "Group names should be concise (2-4 word topic phrases), as few as possible, and cover "
            "multiple related tags.\n"
            "Reply with a single JSON object only — no extra text, no markdown code fences.\n"
            'Format: {"groups": {"TagA": "Group name", "TagB": "Group name"}}\n'
            "Every tag provided must get a group name; use an empty string only if a tag truly can't "
            "be classified."
        ),
        "tagdup_system": (
            "You are a tag de-duplication assistant. The user gives you the tag list of a technical glossary.\n"
            "Find the tags that actually refer to the SAME thing and merely differ in how they are written, and group them.\n"
            "Typical cases: a term and its translation (Reflow Oven / 回焊爐), a full name and its abbreviation, synonyms.\n"
            "Rules:\n"
            "- Use only tag names exactly as they appear in the list. Never invent, rewrite or fix typos.\n"
            "- Every group must contain at least two tags. Return an empty array if you find none.\n"
            "- **Prefer missing a pair over grouping tags that are merely related or share a topic** --\n"
            "  those are different things, and merging them permanently destroys a distinction the user made.\n"
            "- Reply with a single JSON object only. No extra text, no markdown code block.\n"
            'Format: {"groups": [["tagA", "tagB"], ["tagC", "tagD"]]}'
        ),
        "json_intro": "\n\nReply with a single JSON object only — no extra text, no markdown code fences. Format:\n",
        "json_name_label": "term or title",
        "json_desc_label": "description text; use \\n for paragraph breaks",
        "desc_limit_before": " (keep the description to roughly ",
        "desc_limit_after": " characters)",
        "json_keywords_sample": ["keyword1", "keyword2", "keyword3"],
        "json_outro": "Under fields, only fill in the keys listed above; use an empty string for anything with no content. At most three keywords.",
        "field_before": "You are a content-rewriting assistant for jargon notes. The user provides a note's complete current content; please generate a better version of only the \"",
        "field_after": "\" field: write it based on the whole note's context, not just that field's old value; if the old value is empty, generate directly from context; keep the field's original purpose and language.\n",
        "field_style_ref_prefix": "This note's template content instructions (for style reference): ",
        "field_json_intro": 'Reply with a single JSON object only — no extra text, no markdown code fences. Format: {"value": "new field content"}\n',
        "field_multiline": "value may use \\n for paragraph breaks.",
        "field_singleline": "value must be a short single line.",
        "field_target_before": "\nField to regenerate: \"",
        "field_target_mid": "\" (current content: ",
        "field_target_after": ")",
        "label_term": "Term",
        "label_description": "Description",
        "label_tags": "Tags",
        "label_blank": "(empty)",
        "existing_tags_before": "\n(Existing tags: ",
        "existing_tags_after": "; add keywords from different angles, don't repeat existing tags)",
        "tag_joiner": ", ",
        "article_full_text_label": "Full article:\n",
        "article_target_before": "\n\nTerm to explain: \"",
        "article_target_after": "\"",
        "existing_groups_label": "Existing groups: ",
        "no_groups_label": "(no groups yet)",
        "tags_to_group_label": "Tags to group:\n",
    },
    "ja": {
        "lang_directive": "上記の指示がどの言語で書かれていても、常に日本語で回答してください。",
        "tags_system": (
            "あなたはタグ分類アシスタントです。ユーザーが提供する用語の内容をもとに、分類や検索に"
            "最も適したキーワードを最大3つ、できるだけ的確に抽出してください。キーワードは短い"
            "トピック語にしてください(文や説明文ではなく)。\n"
            "JSONオブジェクト1つだけを返してください。余計な文章やmarkdownのコードブロックは"
            '不要です。形式:{"keywords": ["キーワード1", "キーワード2", "キーワード3"]}\n'
            "keywordsは最大3つ。内容が乏しい場合は3つ未満でも構いません。"
        ),
        "group_system": (
            "あなたはタグ分類アシスタントです。ユーザーは用語タグの集合と、既に存在するグループ名を"
            "提供します。\n"
            "各タグを最も適したグループに割り当ててください:既存のグループに合うものはそれを使い、"
            "合わなければ新しいグループ名を提案してください。\n"
            "グループ名は簡潔に(2〜6文字程度のトピック語)、数はできるだけ少なく、複数の関連タグを"
            "まとめられるものにしてください。\n"
            "JSONオブジェクト1つだけを返してください。余計な文章やmarkdownのコードブロックは"
            "不要です。\n"
            '形式:{"groups": {"タグA": "グループ名", "タグB": "グループ名"}}\n'
            "提供された各タグに必ずグループ名を付けてください。本当に分類できないタグのみ"
            "空文字列にしてください。"
        ),
        "tagdup_system": (
            "あなたはタグの重複整理アシスタントです。技術用語集のタグ一覧が渡されます。\n"
            "その中から**実際には同じものを指していて、書き方だけが違う**タグを見つけ、グループにまとめてください。\n"
            "よくある例:日英の対応(リフロー炉 / Reflow Oven)、正式名称と略称、同義の別称。\n"
            "ルール:\n"
            "- 一覧にそのまま出てくるタグ名だけを使ってください。創作・書き換え・誤字修正は一切禁止です。\n"
            "- 各グループは必ず 2 つ以上。1 組も見つからなければ空の配列を返してください。\n"
            "- **見逃すほうがましです。単に「関連する」「同じ分野」というだけのタグをまとめないでください**——\n"
            "  それらは別物であり、統合するとユーザーが付けた区別が永久に失われます。\n"
            "- JSON オブジェクトだけを返してください。余計な文字も markdown code block も不要です。\n"
            '形式:{"groups": [["タグA", "タグB"], ["タグC", "タグD"]]}'
        ),
        "json_intro": "\n\nJSONオブジェクト1つだけを返してください。余計な文章やmarkdownのコードブロックは不要です。形式は以下の通り:\n",
        "json_name_label": "用語またはタイトル",
        "json_desc_label": "説明文。\\n で段落を分けられます",
        "desc_limit_before": "(説明文はおよそ ",
        "desc_limit_after": " 字以内に収めてください)",
        "json_keywords_sample": ["キーワード1", "キーワード2", "キーワード3"],
        "json_outro": "fieldsには上記に列挙したkeyのみを入力し、内容が無いものは空文字列にしてください。keywordsは最大3つです。",
        "field_before": "あなたは用語ノートの内容を書き直すアシスタントです。ユーザーは用語ノートの現在の完全な内容を提供します。「",
        "field_after": "」フィールドについてのみ、より良い新しいバージョンを生成してください。ノート全体の文脈を参考にし、そのフィールドの古い値だけを見ないでください。古い値が空の場合は文脈から直接生成してください。そのフィールド本来の用途と言語は維持してください。\n",
        "field_style_ref_prefix": "このノートのテンプレートの内容指示(スタイルの参考に):",
        "field_json_intro": 'JSONオブジェクト1つだけを返してください。余計な文章やmarkdownのコードブロックは不要です。形式:{"value": "新しいフィールドの内容"}\n',
        "field_multiline": "valueは \\n で改行できます。",
        "field_singleline": "valueは1行の簡潔なテキストにしてください。",
        "field_target_before": "\n再生成するフィールド:「",
        "field_target_mid": "」(現在の内容:",
        "field_target_after": ")",
        "label_term": "用語",
        "label_description": "説明",
        "label_tags": "タグ",
        "label_blank": "(未入力)",
        "existing_tags_before": "\n(既存のタグ:",
        "existing_tags_after": ";異なる視点のキーワードを補ってください。既存のタグとの重複は避けてください)",
        "tag_joiner": "、",
        "article_full_text_label": "記事全文:\n",
        "article_target_before": "\n\n説明する用語:「",
        "article_target_after": "」",
        "existing_groups_label": "既存のグループ:",
        "no_groups_label": "(グループはまだありません)",
        "tags_to_group_label": "分類するタグ:\n",
    },
    "fr": {
        "lang_directive": "Répondez toujours en français, quelle que soit la langue des instructions ci-dessus.",
        "tags_system": (
            "Vous êtes un assistant de classification par étiquettes. À partir du contenu du terme "
            "fourni par l'utilisateur, identifiez les mots-clés les plus adaptés pour le classer et le "
            "retrouver — trois au maximum, aussi précis que possible. Les mots-clés doivent être de "
            "courts termes thématiques, pas des phrases ni des explications.\n"
            "Répondez uniquement avec un objet JSON, sans texte supplémentaire ni bloc de code "
            'markdown. Format : {"keywords": ["motclé1", "motclé2", "motclé3"]}\n'
            "Trois mots-clés au maximum ; moins si le contenu ne permet pas d'en dégager trois."
        ),
        "group_system": (
            "Vous êtes un assistant de classification par étiquettes. L'utilisateur fournit un "
            "ensemble d'étiquettes de termes ainsi que les noms des groupes déjà existants.\n"
            "Affectez chaque étiquette au groupe le plus adapté : réutilisez un groupe existant si "
            "possible, sinon proposez un nouveau nom de groupe.\n"
            "Les noms de groupe doivent être concis (2 à 4 mots thématiques), aussi peu nombreux que "
            "possible, et regrouper plusieurs étiquettes liées.\n"
            "Répondez uniquement avec un objet JSON, sans texte supplémentaire ni bloc de code "
            "markdown.\n"
            'Format : {"groups": {"ÉtiquetteA": "Nom du groupe", "ÉtiquetteB": "Nom du groupe"}}\n'
            "Chaque étiquette fournie doit recevoir un nom de groupe ; ne renvoyez une chaîne vide que "
            "si une étiquette est vraiment impossible à classer."
        ),
        "tagdup_system": (
            "Vous êtes un assistant de dédoublonnage d'étiquettes. L'utilisateur fournit la liste des étiquettes d'un glossaire technique.\n"
            "Trouvez celles qui désignent en réalité LA MÊME chose et ne diffèrent que par l'écriture, puis regroupez-les.\n"
            "Cas typiques : un terme et sa traduction (Four à refusion / Reflow Oven), un nom complet et son sigle, des synonymes.\n"
            "Règles :\n"
            "- N'utilisez que les noms d'étiquettes exactement tels qu'ils figurent dans la liste. N'inventez rien, ne réécrivez rien, ne corrigez aucune faute.\n"
            "- Chaque groupe compte au moins deux étiquettes. Renvoyez un tableau vide si vous n'en trouvez aucun.\n"
            "- **Mieux vaut en manquer que de regrouper des étiquettes simplement liées ou du même domaine** --\n"
            "  ce sont des choses différentes, et les fusionner détruit définitivement une distinction voulue par l'utilisateur.\n"
            "- Répondez uniquement par un objet JSON. Aucun texte supplémentaire, pas de bloc de code markdown.\n"
            'Format : {"groups": [["étiquetteA", "étiquetteB"], ["étiquetteC", "étiquetteD"]]}'
        ),
        "json_intro": "\n\nRépondez uniquement avec un objet JSON, sans texte supplémentaire ni bloc de code markdown. Format :\n",
        "json_name_label": "terme ou titre",
        "json_desc_label": "texte de description ; utilisez \\n pour séparer les paragraphes",
        "desc_limit_before": " (limitez la description à environ ",
        "desc_limit_after": " caractères)",
        "json_keywords_sample": ["motclé1", "motclé2", "motclé3"],
        "json_outro": "Dans fields, ne renseignez que les clés listées ci-dessus ; laissez une chaîne vide si aucun contenu. keywords : trois au maximum.",
        "field_before": "Vous êtes un assistant de réécriture de contenu pour des notes de glossaire. L'utilisateur fournit le contenu actuel complet d'une note ; générez uniquement une meilleure version du champ « ",
        "field_after": " » : appuyez-vous sur le contexte de toute la note, pas seulement sur l'ancienne valeur de ce champ ; si l'ancienne valeur est vide, générez directement à partir du contexte ; conservez l'objectif et la langue d'origine du champ.\n",
        "field_style_ref_prefix": "Instructions de contenu du modèle de cette note (à titre de référence de style) : ",
        "field_json_intro": 'Répondez uniquement avec un objet JSON, sans texte supplémentaire ni bloc de code markdown. Format : {"value": "nouveau contenu du champ"}\n',
        "field_multiline": "value peut utiliser \\n pour séparer les paragraphes.",
        "field_singleline": "value doit être un texte court sur une seule ligne.",
        "field_target_before": "\nChamp à régénérer : « ",
        "field_target_mid": " » (contenu actuel : ",
        "field_target_after": ")",
        "label_term": "Terme",
        "label_description": "Description",
        "label_tags": "Étiquettes",
        "label_blank": "(vide)",
        "existing_tags_before": "\n(Étiquettes existantes : ",
        "existing_tags_after": " ; ajoutez des mots-clés sous des angles différents, sans répéter les étiquettes existantes)",
        "tag_joiner": ", ",
        "article_full_text_label": "Article complet :\n",
        "article_target_before": "\n\nTerme à expliquer : « ",
        "article_target_after": " »",
        "existing_groups_label": "Groupes existants : ",
        "no_groups_label": "(aucun groupe pour l'instant)",
        "tags_to_group_label": "Étiquettes à regrouper :\n",
    },
    "de": {
        "lang_directive": "Antworte immer auf Deutsch, unabhängig davon, in welcher Sprache die obigen Anweisungen verfasst sind.",
        "tags_system": (
            "Du bist ein Tag-Klassifizierungsassistent. Ermittle anhand des vom Benutzer "
            "bereitgestellten Begriffsinhalts die am besten geeigneten Schlüsselwörter zur Einordnung "
            "und Suche — höchstens drei, so treffend wie möglich. Schlüsselwörter sollen kurze "
            "Themenwörter sein, keine Sätze oder Erklärungen.\n"
            "Antworte nur mit einem einzigen JSON-Objekt, ohne zusätzlichen Text und ohne "
            'Markdown-Codeblock. Format: {"keywords": ["Schlüsselwort1", "Schlüsselwort2", '
            '"Schlüsselwort3"]}\n'
            "Höchstens drei Schlüsselwörter; weniger ist in Ordnung, wenn der Inhalt nicht für drei "
            "ausreicht."
        ),
        "group_system": (
            "Du bist ein Tag-Klassifizierungsassistent. Der Benutzer stellt eine Reihe von "
            "Begriffs-Tags sowie die Namen bereits vorhandener Gruppen bereit.\n"
            "Ordne jedes Tag der am besten passenden Gruppe zu: verwende eine vorhandene Gruppe, wenn "
            "sie passt, andernfalls schlage einen neuen Gruppennamen vor.\n"
            "Gruppennamen sollen prägnant sein (2–4 Wörter als Themenbegriff), möglichst wenige an der "
            "Zahl, und mehrere verwandte Tags abdecken.\n"
            "Antworte nur mit einem einzigen JSON-Objekt, ohne zusätzlichen Text und ohne "
            "Markdown-Codeblock.\n"
            'Format: {"groups": {"TagA": "Gruppenname", "TagB": "Gruppenname"}}\n'
            "Jedes bereitgestellte Tag muss einen Gruppennamen erhalten; gib nur dann einen leeren "
            "String zurück, wenn ein Tag wirklich nicht einzuordnen ist."
        ),
        "tagdup_system": (
            "Du bist ein Assistent zur Entdopplung von Schlagwörtern. Der Benutzer übergibt die Schlagwortliste eines Fachglossars.\n"
            "Finde die Schlagwörter, die tatsächlich DASSELBE bezeichnen und sich nur in der Schreibweise unterscheiden, und fasse sie zu Gruppen zusammen.\n"
            "Typische Fälle: Begriff und Übersetzung (Reflowofen / Reflow Oven), Vollform und Abkürzung, Synonyme.\n"
            "Regeln:\n"
            "- Verwende ausschließlich Schlagwortnamen genau so, wie sie in der Liste stehen. Nichts erfinden, umschreiben oder Tippfehler korrigieren.\n"
            "- Jede Gruppe enthält mindestens zwei Schlagwörter. Gib ein leeres Array zurück, wenn du keine findest.\n"
            "- **Lieber ein Paar übersehen, als Schlagwörter zu gruppieren, die nur verwandt sind oder zum selben Thema gehören** --\n"
            "  das sind verschiedene Dinge, und eine Zusammenführung zerstört dauerhaft eine Unterscheidung des Benutzers.\n"
            "- Antworte nur mit einem JSON-Objekt. Kein zusätzlicher Text, kein Markdown-Codeblock.\n"
            'Format: {"groups": [["SchlagwortA", "SchlagwortB"], ["SchlagwortC", "SchlagwortD"]]}'
        ),
        "json_intro": "\n\nAntworte nur mit einem einzigen JSON-Objekt, ohne zusätzlichen Text und ohne Markdown-Codeblock. Format:\n",
        "json_name_label": "Begriff oder Titel",
        "json_desc_label": "Beschreibungstext; verwende \\n für Absätze",
        "desc_limit_before": " (halte die Beschreibung bei ungefähr ",
        "desc_limit_after": " Zeichen)",
        "json_keywords_sample": ["Schlüsselwort1", "Schlüsselwort2", "Schlüsselwort3"],
        "json_outro": "Fülle unter fields nur die oben aufgeführten Schlüssel aus; verwende einen leeren String, wenn kein Inhalt vorhanden ist. keywords: höchstens drei.",
        "field_before": "Du bist ein Assistent zum Umschreiben von Inhalten für Fachbegriffs-Notizen. Der Benutzer stellt den vollständigen aktuellen Inhalt einer Notiz bereit; erzeuge ausschließlich eine bessere Version des Feldes „",
        "field_after": "“: orientiere dich am Kontext der gesamten Notiz, nicht nur am alten Wert dieses Feldes; ist der alte Wert leer, generiere direkt aus dem Kontext; behalte Zweck und Sprache des Feldes bei.\n",
        "field_style_ref_prefix": "Inhaltliche Vorgaben der Vorlage dieser Notiz (als Stilreferenz): ",
        "field_json_intro": 'Antworte nur mit einem einzigen JSON-Objekt, ohne zusätzlichen Text und ohne Markdown-Codeblock. Format: {"value": "neuer Feldinhalt"}\n',
        "field_multiline": "value darf \\n für Absätze verwenden.",
        "field_singleline": "value muss ein kurzer einzeiliger Text sein.",
        "field_target_before": "\nNeu zu erzeugendes Feld: „",
        "field_target_mid": "“ (aktueller Inhalt: ",
        "field_target_after": ")",
        "label_term": "Begriff",
        "label_description": "Beschreibung",
        "label_tags": "Tags",
        "label_blank": "(leer)",
        "existing_tags_before": "\n(Vorhandene Tags: ",
        "existing_tags_after": "; ergänze Schlüsselwörter aus anderen Blickwinkeln, wiederhole keine vorhandenen Tags)",
        "tag_joiner": ", ",
        "article_full_text_label": "Vollständiger Artikel:\n",
        "article_target_before": "\n\nZu erklärender Begriff: „",
        "article_target_after": "“",
        "existing_groups_label": "Vorhandene Gruppen: ",
        "no_groups_label": "(noch keine Gruppen)",
        "tags_to_group_label": "Zu gruppierende Tags:\n",
    },
    "it": {
        "lang_directive": "Rispondi sempre in italiano, indipendentemente dalla lingua in cui sono scritte le istruzioni sopra.",
        "tags_system": (
            "Sei un assistente per la classificazione dei tag. In base al contenuto del termine "
            "fornito dall'utente, individua le parole chiave più adatte per classificarlo e cercarlo "
            "— al massimo tre, il più mirate possibile. Le parole chiave devono essere brevi termini "
            "tematici, non frasi o spiegazioni.\n"
            "Rispondi solo con un oggetto JSON, senza testo aggiuntivo né blocchi di codice markdown. "
            'Formato: {"keywords": ["parolachiave1", "parolachiave2", "parolachiave3"]}\n'
            "Al massimo tre parole chiave; meno va bene se il contenuto non ne giustifica tre."
        ),
        "group_system": (
            "Sei un assistente per la classificazione dei tag. L'utente fornirà un insieme di tag di "
            "termini e i nomi dei gruppi già esistenti.\n"
            "Assegna ogni tag al gruppo più adatto: riusa un gruppo esistente se pertinente, "
            "altrimenti proponi un nuovo nome di gruppo.\n"
            "I nomi dei gruppi devono essere concisi (2-4 parole tematiche), il più possibile pochi di "
            "numero, e in grado di coprire più tag correlati.\n"
            "Rispondi solo con un oggetto JSON, senza testo aggiuntivo né blocchi di codice markdown.\n"
            'Formato: {"groups": {"TagA": "Nome gruppo", "TagB": "Nome gruppo"}}\n'
            "Ogni tag fornito deve ricevere un nome di gruppo; restituisci una stringa vuota solo se un "
            "tag è davvero impossibile da classificare."
        ),
        "tagdup_system": (
            "Sei un assistente per la deduplicazione delle etichette. L'utente ti fornisce l'elenco delle etichette di un glossario tecnico.\n"
            "Individua quelle che indicano in realtà LA STESSA cosa e differiscono solo nella scrittura, e raggruppale.\n"
            "Casi tipici: un termine e la sua traduzione (Forno di rifusione / Reflow Oven), nome per esteso e sigla, sinonimi.\n"
            "Regole:\n"
            "- Usa solo i nomi delle etichette esattamente come compaiono nell'elenco. Non inventare, non riscrivere, non correggere refusi.\n"
            "- Ogni gruppo deve contenere almeno due etichette. Restituisci un array vuoto se non ne trovi.\n"
            "- **Meglio perderne qualcuna che raggruppare etichette solo correlate o dello stesso ambito** --\n"
            "  sono cose diverse, e unirle distrugge per sempre una distinzione voluta dall'utente.\n"
            "- Rispondi solo con un oggetto JSON. Nessun testo aggiuntivo, nessun blocco di codice markdown.\n"
            'Formato: {"groups": [["etichettaA", "etichettaB"], ["etichettaC", "etichettaD"]]}'
        ),
        "json_intro": "\n\nRispondi solo con un oggetto JSON, senza testo aggiuntivo né blocchi di codice markdown. Formato:\n",
        "json_name_label": "termine o titolo",
        "json_desc_label": "testo descrittivo; usa \\n per separare i paragrafi",
        "desc_limit_before": " (mantieni la descrizione entro circa ",
        "desc_limit_after": " caratteri)",
        "json_keywords_sample": ["parolachiave1", "parolachiave2", "parolachiave3"],
        "json_outro": "In fields, compila solo le chiavi elencate sopra; usa una stringa vuota se non c'è contenuto. keywords: al massimo tre.",
        "field_before": "Sei un assistente per la riscrittura dei contenuti delle note di glossario. L'utente fornisce il contenuto attuale completo di una nota; genera solo una versione migliore del campo \"",
        "field_after": "\": basati sul contesto dell'intera nota, non solo sul vecchio valore di quel campo; se il vecchio valore è vuoto, genera direttamente dal contesto; mantieni lo scopo e la lingua originali del campo.\n",
        "field_style_ref_prefix": "Istruzioni di contenuto del modello di questa nota (come riferimento di stile): ",
        "field_json_intro": 'Rispondi solo con un oggetto JSON, senza testo aggiuntivo né blocchi di codice markdown. Formato: {"value": "nuovo contenuto del campo"}\n',
        "field_multiline": "value può usare \\n per separare i paragrafi.",
        "field_singleline": "value deve essere un breve testo su una sola riga.",
        "field_target_before": "\nCampo da rigenerare: \"",
        "field_target_mid": "\" (contenuto attuale: ",
        "field_target_after": ")",
        "label_term": "Termine",
        "label_description": "Descrizione",
        "label_tags": "Tag",
        "label_blank": "(vuoto)",
        "existing_tags_before": "\n(Tag esistenti: ",
        "existing_tags_after": "; aggiungi parole chiave da angolazioni diverse, senza ripetere i tag esistenti)",
        "tag_joiner": ", ",
        "article_full_text_label": "Articolo completo:\n",
        "article_target_before": "\n\nTermine da spiegare: \"",
        "article_target_after": "\"",
        "existing_groups_label": "Gruppi esistenti: ",
        "no_groups_label": "(nessun gruppo ancora)",
        "tags_to_group_label": "Tag da raggruppare:\n",
    },
    "pt": {
        "lang_directive": "Responda sempre em português, independentemente do idioma em que as instruções acima estejam escritas.",
        "tags_system": (
            "Você é um assistente de classificação de tags. Com base no conteúdo do termo fornecido "
            "pelo usuário, identifique as palavras-chave mais adequadas para categorizá-lo e "
            "pesquisá-lo — no máximo três, as mais precisas possível. As palavras-chave devem ser "
            "termos temáticos curtos, não frases ou explicações.\n"
            "Responda apenas com um objeto JSON, sem texto adicional nem blocos de código markdown. "
            'Formato: {"keywords": ["palavrachave1", "palavrachave2", "palavrachave3"]}\n'
            "No máximo três palavras-chave; menos está ok se o conteúdo não permitir três."
        ),
        "group_system": (
            "Você é um assistente de classificação de tags. O usuário fornecerá um conjunto de tags "
            "de termos e os nomes dos grupos já existentes.\n"
            "Atribua cada tag ao grupo mais adequado: reaproveite um grupo existente quando fizer "
            "sentido, caso contrário proponha um novo nome de grupo.\n"
            "Os nomes de grupo devem ser concisos (2 a 4 palavras temáticas), o menor número possível, "
            "e abranger várias tags relacionadas.\n"
            "Responda apenas com um objeto JSON, sem texto adicional nem blocos de código markdown.\n"
            'Formato: {"groups": {"TagA": "Nome do grupo", "TagB": "Nome do grupo"}}\n'
            "Cada tag fornecida deve receber um nome de grupo; use uma string vazia apenas se uma tag "
            "realmente não puder ser classificada."
        ),
        "tagdup_system": (
            "Você é um assistente de eliminação de etiquetas duplicadas. O usuário fornece a lista de etiquetas de um glossário técnico.\n"
            "Encontre as que na verdade se referem à MESMA coisa e apenas são escritas de forma diferente, e agrupe-as.\n"
            "Casos típicos: um termo e sua tradução (Forno de refusão / Reflow Oven), nome completo e sigla, sinônimos.\n"
            "Regras:\n"
            "- Use apenas nomes de etiquetas exatamente como aparecem na lista. Nunca invente, reescreva ou corrija erros de digitação.\n"
            "- Cada grupo deve ter pelo menos duas etiquetas. Retorne um array vazio se não encontrar nenhum.\n"
            "- **É melhor deixar passar do que agrupar etiquetas apenas relacionadas ou do mesmo tema** --\n"
            "  são coisas diferentes, e juntá-las destrói permanentemente uma distinção feita pelo usuário.\n"
            "- Responda apenas com um objeto JSON. Sem texto adicional, sem bloco de código markdown.\n"
            'Formato: {"groups": [["etiquetaA", "etiquetaB"], ["etiquetaC", "etiquetaD"]]}'
        ),
        "json_intro": "\n\nResponda apenas com um objeto JSON, sem texto adicional nem blocos de código markdown. Formato:\n",
        "json_name_label": "termo ou título",
        "json_desc_label": "texto de descrição; use \\n para separar parágrafos",
        "desc_limit_before": " (mantenha a descrição em cerca de ",
        "desc_limit_after": " caracteres)",
        "json_keywords_sample": ["palavrachave1", "palavrachave2", "palavrachave3"],
        "json_outro": "Em fields, preencha apenas as chaves listadas acima; use string vazia quando não houver conteúdo. keywords: no máximo três.",
        "field_before": "Você é um assistente de reescrita de conteúdo para notas de glossário. O usuário fornece o conteúdo atual completo de uma nota; gere apenas uma versão melhor do campo \"",
        "field_after": "\": baseie-se no contexto de toda a nota, não apenas no valor antigo desse campo; se o valor antigo estiver vazio, gere diretamente a partir do contexto; mantenha o propósito e o idioma originais do campo.\n",
        "field_style_ref_prefix": "Instruções de conteúdo do modelo desta nota (como referência de estilo): ",
        "field_json_intro": 'Responda apenas com um objeto JSON, sem texto adicional nem blocos de código markdown. Formato: {"value": "novo conteúdo do campo"}\n',
        "field_multiline": "value pode usar \\n para separar parágrafos.",
        "field_singleline": "value deve ser um texto curto de uma linha.",
        "field_target_before": "\nCampo a regenerar: \"",
        "field_target_mid": "\" (conteúdo atual: ",
        "field_target_after": ")",
        "label_term": "Termo",
        "label_description": "Descrição",
        "label_tags": "Tags",
        "label_blank": "(vazio)",
        "existing_tags_before": "\n(Tags existentes: ",
        "existing_tags_after": "; adicione palavras-chave de ângulos diferentes, sem repetir as tags existentes)",
        "tag_joiner": ", ",
        "article_full_text_label": "Artigo completo:\n",
        "article_target_before": "\n\nTermo a explicar: \"",
        "article_target_after": "\"",
        "existing_groups_label": "Grupos existentes: ",
        "no_groups_label": "(ainda não há grupos)",
        "tags_to_group_label": "Tags a agrupar:\n",
    },
    "es": {
        "lang_directive": "Responde siempre en español, sin importar en qué idioma estén escritas las instrucciones anteriores.",
        "tags_system": (
            "Eres un asistente de clasificación de etiquetas. A partir del contenido del término "
            "proporcionado por el usuario, identifica las palabras clave más adecuadas para "
            "categorizarlo y buscarlo — un máximo de tres, lo más precisas posible. Las palabras "
            "clave deben ser términos temáticos breves, no frases ni explicaciones.\n"
            "Responde solo con un objeto JSON, sin texto adicional ni bloques de código markdown. "
            'Formato: {"keywords": ["palabraclave1", "palabraclave2", "palabraclave3"]}\n'
            "Máximo tres palabras clave; menos está bien si el contenido no da para tres."
        ),
        "group_system": (
            "Eres un asistente de clasificación de etiquetas. El usuario proporcionará un conjunto de "
            "etiquetas de términos junto con los nombres de los grupos ya existentes.\n"
            "Asigna cada etiqueta al grupo más adecuado: reutiliza un grupo existente si encaja, o "
            "propone un nuevo nombre de grupo si no.\n"
            "Los nombres de grupo deben ser concisos (2 a 4 palabras temáticas), los menos posibles, y "
            "abarcar varias etiquetas relacionadas.\n"
            "Responde solo con un objeto JSON, sin texto adicional ni bloques de código markdown.\n"
            'Formato: {"groups": {"EtiquetaA": "Nombre del grupo", "EtiquetaB": "Nombre del grupo"}}\n'
            "Cada etiqueta proporcionada debe recibir un nombre de grupo; usa una cadena vacía solo si "
            "una etiqueta realmente no se puede clasificar."
        ),
        "tagdup_system": (
            "Eres un asistente de eliminación de etiquetas duplicadas. El usuario te da la lista de etiquetas de un glosario técnico.\n"
            "Encuentra las que en realidad se refieren a LO MISMO y solo se escriben de forma distinta, y agrúpalas.\n"
            "Casos típicos: un término y su traducción (Horno de refusión / Reflow Oven), nombre completo y sigla, sinónimos.\n"
            "Reglas:\n"
            "- Usa solo nombres de etiquetas exactamente como aparecen en la lista. Nunca inventes, reescribas ni corrijas erratas.\n"
            "- Cada grupo debe tener al menos dos etiquetas. Devuelve un array vacío si no encuentras ninguno.\n"
            "- **Mejor pasar por alto un par que agrupar etiquetas solo relacionadas o del mismo tema** --\n"
            "  son cosas distintas, y fusionarlas destruye para siempre una distinción que hizo el usuario.\n"
            "- Responde solo con un objeto JSON. Sin texto adicional, sin bloque de código markdown.\n"
            'Formato: {"groups": [["etiquetaA", "etiquetaB"], ["etiquetaC", "etiquetaD"]]}'
        ),
        "json_intro": "\n\nResponde solo con un objeto JSON, sin texto adicional ni bloques de código markdown. Formato:\n",
        "json_name_label": "término o título",
        "json_desc_label": "texto de descripción; usa \\n para separar párrafos",
        "desc_limit_before": " (mantén la descripción en torno a ",
        "desc_limit_after": " caracteres)",
        "json_keywords_sample": ["palabraclave1", "palabraclave2", "palabraclave3"],
        "json_outro": "En fields, completa solo las claves listadas arriba; usa una cadena vacía si no hay contenido. keywords: máximo tres.",
        "field_before": "Eres un asistente de reescritura de contenido para notas de glosario. El usuario proporciona el contenido actual completo de una nota; genera solo una versión mejorada del campo \"",
        "field_after": "\": básate en el contexto de toda la nota, no solo en el valor anterior de ese campo; si el valor anterior está vacío, genera directamente a partir del contexto; conserva el propósito y el idioma originales del campo.\n",
        "field_style_ref_prefix": "Instrucciones de contenido de la plantilla de esta nota (como referencia de estilo): ",
        "field_json_intro": 'Responde solo con un objeto JSON, sin texto adicional ni bloques de código markdown. Formato: {"value": "nuevo contenido del campo"}\n',
        "field_multiline": "value puede usar \\n para separar párrafos.",
        "field_singleline": "value debe ser un texto breve de una sola línea.",
        "field_target_before": "\nCampo a regenerar: \"",
        "field_target_mid": "\" (contenido actual: ",
        "field_target_after": ")",
        "label_term": "Término",
        "label_description": "Descripción",
        "label_tags": "Etiquetas",
        "label_blank": "(vacío)",
        "existing_tags_before": "\n(Etiquetas existentes: ",
        "existing_tags_after": "; agrega palabras clave desde ángulos distintos, sin repetir las etiquetas existentes)",
        "tag_joiner": ", ",
        "article_full_text_label": "Artículo completo:\n",
        "article_target_before": "\n\nTérmino a explicar: \"",
        "article_target_after": "\"",
        "existing_groups_label": "Grupos existentes: ",
        "no_groups_label": "(todavía no hay grupos)",
        "tags_to_group_label": "Etiquetas a agrupar:\n",
    },
    "ko": {
        "lang_directive": "위 지침이 어떤 언어로 쓰여 있든 관계없이 항상 한국어로 답변하세요.",
        "tags_system": (
            "당신은 태그 분류 도우미입니다. 사용자가 제공한 용어 내용을 바탕으로, 분류와 검색에 "
            "가장 적합한 키워드를 최대 3개까지 최대한 핵심적으로 골라주세요. 키워드는 짧은 "
            "주제어여야 하며 문장이나 설명이 아니어야 합니다.\n"
            "JSON 객체 하나만 응답하세요. 다른 텍스트나 markdown 코드 블록은 포함하지 마세요. "
            '형식: {"keywords": ["키워드1", "키워드2", "키워드3"]}\n'
            "keywords는 최대 3개이며, 내용이 부족하면 3개보다 적어도 됩니다."
        ),
        "group_system": (
            "당신은 태그 분류 도우미입니다. 사용자는 용어 태그 모음과 현재 존재하는 그룹 이름을 "
            "제공합니다.\n"
            "각 태그를 가장 적합한 그룹에 배정하세요: 기존 그룹에 맞으면 그것을 사용하고, 맞지 "
            "않으면 새 그룹 이름을 제안하세요.\n"
            "그룹 이름은 간결해야 하며(2~4단어의 주제어), 개수는 최대한 적게, 여러 관련 태그를 "
            "아우를 수 있어야 합니다.\n"
            "JSON 객체 하나만 응답하세요. 다른 텍스트나 markdown 코드 블록은 포함하지 마세요.\n"
            '형식: {"groups": {"태그A": "그룹명", "태그B": "그룹명"}}\n'
            "제공된 모든 태그에는 그룹명을 지정해야 합니다. 정말로 분류할 수 없는 태그만 빈 "
            "문자열로 남기세요."
        ),
        "tagdup_system": (
            "당신은 태그 중복 정리 도우미입니다. 사용자가 기술 용어집의 태그 목록을 제공합니다.\n"
            "그중에서 **실제로는 같은 것을 가리키며 표기만 다른** 태그를 찾아 하나의 그룹으로 묶어 주세요.\n"
            "흔한 경우: 용어와 번역어(리플로우 오븐 / Reflow Oven), 정식 명칭과 약어, 동의어.\n"
            "규칙:\n"
            "- 목록에 그대로 나온 태그 이름만 사용하세요. 창작·재작성·오타 수정은 절대 하지 마세요.\n"
            "- 각 그룹에는 태그가 최소 두 개 있어야 합니다. 하나도 없으면 빈 배열을 반환하세요.\n"
            "- **놓치는 편이 낫습니다. 단지 「관련 있다」거나 「같은 분야」라는 이유로 묶지 마세요** --\n"
            "  그것들은 서로 다른 것이며, 합치면 사용자가 만든 구분이 영구히 사라집니다.\n"
            "- JSON 객체 하나만 반환하세요. 추가 텍스트도, markdown code block도 넣지 마세요.\n"
            '형식: {"groups": [["태그A", "태그B"], ["태그C", "태그D"]]}'
        ),
        "json_intro": "\n\nJSON 객체 하나만 응답하세요. 다른 텍스트나 markdown 코드 블록은 포함하지 마세요. 형식은 다음과 같습니다:\n",
        "json_name_label": "용어 또는 제목",
        "json_desc_label": "설명 텍스트, \\n으로 문단을 나눌 수 있음",
        "desc_limit_before": "(설명은 약 ",
        "desc_limit_after": "자 이내로 작성)",
        "json_keywords_sample": ["키워드1", "키워드2", "키워드3"],
        "json_outro": "fields에는 위에 나열된 key만 채우고, 내용이 없으면 빈 문자열로 두세요. keywords는 최대 3개입니다.",
        "field_before": "당신은 용어 노트의 내용을 다시 작성하는 도우미입니다. 사용자는 용어 노트의 현재 전체 내용을 제공합니다. \"",
        "field_after": "\" 필드에 대해서만 더 나은 새 버전을 생성해 주세요. 해당 필드의 이전 값만 보지 말고 노트 전체의 맥락을 참고하세요. 이전 값이 비어 있으면 맥락에서 바로 생성하세요. 해당 필드 본래의 용도와 언어는 유지하세요.\n",
        "field_style_ref_prefix": "이 노트가 속한 템플릿의 내용 지침(스타일 참고용): ",
        "field_json_intro": 'JSON 객체 하나만 응답하세요. 다른 텍스트나 markdown 코드 블록은 포함하지 마세요. 형식: {"value": "새 필드 내용"}\n',
        "field_multiline": "value에는 \\n으로 문단을 나눌 수 있습니다.",
        "field_singleline": "value는 한 줄의 짧은 텍스트여야 합니다.",
        "field_target_before": "\n다시 생성할 필드: \"",
        "field_target_mid": "\" (현재 내용: ",
        "field_target_after": ")",
        "label_term": "용어",
        "label_description": "설명",
        "label_tags": "태그",
        "label_blank": "(비어 있음)",
        "existing_tags_before": "\n(기존 태그: ",
        "existing_tags_after": "; 다른 관점의 키워드를 추가하고 기존 태그와 중복되지 않게 하세요)",
        "tag_joiner": ", ",
        "article_full_text_label": "기사 전문:\n",
        "article_target_before": "\n\n설명할 용어: \"",
        "article_target_after": "\"",
        "existing_groups_label": "기존 그룹: ",
        "no_groups_label": "(아직 그룹 없음)",
        "tags_to_group_label": "분류할 태그:\n",
    },
    "id": {
        "lang_directive": "Selalu balas dalam Bahasa Indonesia, apa pun bahasa instruksi di atas.",
        "tags_system": (
            "Anda adalah asisten klasifikasi tag. Berdasarkan konten istilah yang diberikan pengguna, "
            "identifikasi kata kunci yang paling cocok untuk mengelompokkan dan mencarinya — maksimal "
            "tiga, seringkas mungkin. Kata kunci harus berupa kata topik singkat, bukan kalimat atau "
            "penjelasan.\n"
            "Balas hanya dengan satu objek JSON, tanpa teks tambahan atau blok kode markdown. "
            'Format: {"keywords": ["katakunci1", "katakunci2", "katakunci3"]}\n'
            "Maksimal tiga kata kunci; boleh kurang jika kontennya tidak cukup untuk tiga."
        ),
        "group_system": (
            "Anda adalah asisten klasifikasi tag. Pengguna akan memberikan sekumpulan tag istilah "
            "beserta nama grup yang sudah ada.\n"
            "Tetapkan setiap tag ke grup yang paling sesuai: gunakan grup yang sudah ada jika cocok, "
            "jika tidak usulkan nama grup baru.\n"
            "Nama grup harus ringkas (2-4 kata topik), sesedikit mungkin jumlahnya, dan mencakup "
            "beberapa tag yang berkaitan.\n"
            "Balas hanya dengan satu objek JSON, tanpa teks tambahan atau blok kode markdown.\n"
            'Format: {"groups": {"TagA": "Nama grup", "TagB": "Nama grup"}}\n'
            "Setiap tag yang diberikan harus mendapat nama grup; berikan string kosong hanya jika "
            "sebuah tag benar-benar tidak dapat dikelompokkan."
        ),
        "tagdup_system": (
            "Anda adalah asisten penghapus duplikat label. Pengguna memberikan daftar label sebuah glosarium teknis.\n"
            "Temukan label yang sebenarnya merujuk pada HAL YANG SAMA dan hanya berbeda penulisannya, lalu kelompokkan.\n"
            "Kasus umum: istilah dan terjemahannya (Oven reflow / Reflow Oven), nama lengkap dan singkatannya, sinonim.\n"
            "Aturan:\n"
            "- Gunakan hanya nama label persis seperti yang ada di daftar. Jangan mengarang, menulis ulang, atau memperbaiki salah ketik.\n"
            "- Setiap kelompok harus berisi minimal dua label. Kembalikan array kosong jika tidak menemukan satu pun.\n"
            "- **Lebih baik terlewat daripada mengelompokkan label yang sekadar berkaitan atau setema** --\n"
            "  itu hal yang berbeda, dan menggabungkannya menghapus selamanya pembedaan yang dibuat pengguna.\n"
            "- Balas hanya dengan satu objek JSON. Tanpa teks tambahan, tanpa markdown code block.\n"
            'Format: {"groups": [["labelA", "labelB"], ["labelC", "labelD"]]}'
        ),
        "json_intro": "\n\nBalas hanya dengan satu objek JSON, tanpa teks tambahan atau blok kode markdown. Format:\n",
        "json_name_label": "istilah atau judul",
        "json_desc_label": "teks deskripsi; gunakan \\n untuk memisahkan paragraf",
        "desc_limit_before": " (batasi deskripsi sekitar ",
        "desc_limit_after": " karakter)",
        "json_keywords_sample": ["katakunci1", "katakunci2", "katakunci3"],
        "json_outro": "Pada fields, isi hanya kunci yang tercantum di atas; gunakan string kosong jika tidak ada isi. keywords: maksimal tiga.",
        "field_before": "Anda adalah asisten penulisan ulang konten untuk catatan istilah. Pengguna memberikan seluruh konten catatan saat ini; hasilkan hanya versi yang lebih baik untuk kolom \"",
        "field_after": "\": tulis berdasarkan konteks seluruh catatan, bukan hanya nilai lama kolom tersebut; jika nilai lama kosong, hasilkan langsung dari konteks; pertahankan tujuan dan bahasa asli kolom tersebut.\n",
        "field_style_ref_prefix": "Instruksi konten dari templat catatan ini (sebagai referensi gaya): ",
        "field_json_intro": 'Balas hanya dengan satu objek JSON, tanpa teks tambahan atau blok kode markdown. Format: {"value": "konten kolom baru"}\n',
        "field_multiline": "value boleh menggunakan \\n untuk memisahkan paragraf.",
        "field_singleline": "value harus berupa teks singkat satu baris.",
        "field_target_before": "\nKolom yang akan dihasilkan ulang: \"",
        "field_target_mid": "\" (konten saat ini: ",
        "field_target_after": ")",
        "label_term": "Istilah",
        "label_description": "Deskripsi",
        "label_tags": "Tag",
        "label_blank": "(kosong)",
        "existing_tags_before": "\n(Tag yang sudah ada: ",
        "existing_tags_after": "; tambahkan kata kunci dari sudut pandang berbeda, jangan mengulang tag yang sudah ada)",
        "tag_joiner": ", ",
        "article_full_text_label": "Artikel lengkap:\n",
        "article_target_before": "\n\nIstilah yang akan dijelaskan: \"",
        "article_target_after": "\"",
        "existing_groups_label": "Grup yang sudah ada: ",
        "no_groups_label": "(belum ada grup)",
        "tags_to_group_label": "Tag yang akan dikelompokkan:\n",
    },
    "hi": {
        "lang_directive": "ऊपर के निर्देश चाहे किसी भी भाषा में लिखे हों, हमेशा हिंदी में जवाब दें।",
        "tags_system": (
            "आप एक टैग वर्गीकरण सहायक हैं। उपयोगकर्ता द्वारा दी गई शब्द सामग्री के आधार पर, उसे "
            "वर्गीकृत करने और खोजने के लिए सबसे उपयुक्त कीवर्ड पहचानें — अधिकतम तीन, जितने सटीक हो "
            "सकें। कीवर्ड छोटे विषय-शब्द होने चाहिए, वाक्य या स्पष्टीकरण नहीं।\n"
            "केवल एक JSON ऑब्जेक्ट के साथ जवाब दें, कोई अतिरिक्त टेक्स्ट या markdown कोड ब्लॉक नहीं। "
            'फ़ॉर्मेट: {"keywords": ["कीवर्ड1", "कीवर्ड2", "कीवर्ड3"]}\n'
            "अधिकतम तीन कीवर्ड; यदि सामग्री तीन के लिए पर्याप्त नहीं है तो कम भी ठीक है।"
        ),
        "group_system": (
            "आप एक टैग वर्गीकरण सहायक हैं। उपयोगकर्ता शब्द टैग का एक समूह और पहले से मौजूद समूहों के "
            "नाम देगा।\n"
            "हर टैग को सबसे उपयुक्त समूह में रखें: यदि कोई मौजूदा समूह फिट बैठता है तो उसका उपयोग "
            "करें, अन्यथा एक नया समूह नाम सुझाएं।\n"
            "समूह के नाम संक्षिप्त होने चाहिए (2-4 शब्दों का विषय-शब्द), जितने कम हो सकें उतने ही हों, "
            "और कई संबंधित टैग को शामिल कर सकें।\n"
            "केवल एक JSON ऑब्जेक्ट के साथ जवाब दें, कोई अतिरिक्त टेक्स्ट या markdown कोड ब्लॉक नहीं।\n"
            '''फ़ॉर्मेट: {"groups": {"टैगA": "समूह नाम", "टैगB": "समूह नाम"}}\n'''
            "दिए गए हर टैग को एक समूह नाम मिलना चाहिए; केवल तभी खाली स्ट्रिंग दें जब कोई टैग वास्तव "
            "में वर्गीकृत न किया जा सके।"
        ),
        "tagdup_system": (
            "आप टैग डिडुप्लिकेशन सहायक हैं। उपयोगकर्ता एक तकनीकी शब्दावली की टैग सूची देगा।\n"
            "उनमें से वे टैग खोजें जो **वास्तव में एक ही चीज़ को दर्शाते हैं और केवल लिखने का तरीका अलग है**, और उन्हें एक समूह में रखें।\n"
            "सामान्य स्थितियाँ: शब्द और उसका अनुवाद (रीफ़्लो ओवन / Reflow Oven), पूरा नाम और संक्षिप्त रूप, पर्यायवाची।\n"
            "नियम:\n"
            "- केवल वही टैग नाम उपयोग करें जो सूची में ठीक वैसे ही मौजूद हैं। कुछ भी गढ़ें नहीं, दोबारा न लिखें, वर्तनी न सुधारें।\n"
            "- हर समूह में कम से कम दो टैग होने चाहिए। कोई न मिले तो खाली सूची लौटाएँ।\n"
            "- **छूट जाना बेहतर है, बजाय उन टैगों को जोड़ने के जो केवल संबंधित हैं या एक ही विषय के हैं** --\n"
            "  वे अलग-अलग चीज़ें हैं, और उन्हें मिलाने से उपयोगकर्ता का बनाया भेद हमेशा के लिए मिट जाता है।\n"
            "- केवल एक JSON ऑब्जेक्ट लौटाएँ। कोई अतिरिक्त पाठ नहीं, कोई markdown code block नहीं।\n"
            'प्रारूप: {"groups": [["टैगA", "टैगB"], ["टैगC", "टैगD"]]}'
        ),
        "json_intro": "\n\nकेवल एक JSON ऑब्जेक्ट के साथ जवाब दें, कोई अतिरिक्त टेक्स्ट या markdown कोड ब्लॉक नहीं। फ़ॉर्मेट इस प्रकार है:\n",
        "json_name_label": "शब्द या शीर्षक",
        "json_desc_label": "विवरण टेक्स्ट; पैराग्राफ अलग करने के लिए \\n का उपयोग करें",
        "desc_limit_before": " (विवरण लगभग ",
        "desc_limit_after": " अक्षरों के भीतर रखें)",
        "json_keywords_sample": ["कीवर्ड1", "कीवर्ड2", "कीवर्ड3"],
        "json_outro": "fields में केवल ऊपर सूचीबद्ध keys भरें; जिनमें सामग्री नहीं है उन्हें खाली स्ट्रिंग दें। keywords अधिकतम तीन।",
        "field_before": "आप शब्दावली नोट्स के लिए सामग्री फिर से लिखने वाले सहायक हैं। उपयोगकर्ता एक नोट की पूरी मौजूदा सामग्री देगा; केवल \"",
        "field_after": "\" फ़ील्ड के लिए एक बेहतर नया संस्करण तैयार करें: पूरे नोट के संदर्भ के आधार पर लिखें, केवल उस फ़ील्ड के पुराने मान को न देखें; यदि पुराना मान खाली है तो संदर्भ से सीधे तैयार करें; फ़ील्ड का मूल उद्देश्य और भाषा बनाए रखें।\n",
        "field_style_ref_prefix": "इस नोट के टेम्पलेट का सामग्री निर्देश (शैली संदर्भ के लिए): ",
        "field_json_intro": 'केवल एक JSON ऑब्जेक्ट के साथ जवाब दें, कोई अतिरिक्त टेक्स्ट या markdown कोड ब्लॉक नहीं। फ़ॉर्मेट: {"value": "नई फ़ील्ड सामग्री"}\n',
        "field_multiline": "value में पैराग्राफ अलग करने के लिए \\n का उपयोग किया जा सकता है।",
        "field_singleline": "value एक पंक्ति का संक्षिप्त टेक्स्ट होना चाहिए।",
        "field_target_before": "\nफिर से तैयार करने वाली फ़ील्ड: \"",
        "field_target_mid": "\" (मौजूदा सामग्री: ",
        "field_target_after": ")",
        "label_term": "शब्द",
        "label_description": "विवरण",
        "label_tags": "टैग",
        "label_blank": "(खाली)",
        "existing_tags_before": "\n(मौजूदा टैग: ",
        "existing_tags_after": "; अलग-अलग नज़रिए से कीवर्ड जोड़ें, मौजूदा टैग न दोहराएं)",
        "tag_joiner": ", ",
        "article_full_text_label": "पूरा लेख:\n",
        "article_target_before": "\n\nसमझाने वाला शब्द: \"",
        "article_target_after": "\"",
        "existing_groups_label": "मौजूदा समूह: ",
        "no_groups_label": "(अभी कोई समूह नहीं)",
        "tags_to_group_label": "समूह में डालने वाले टैग:\n",
    },
}


def p(lang: str, key: str) -> str:
    """查單一 key,語言不支援或缺 key 都退回 DEFAULT_LANG。"""
    return PROMPTS.get(lang, PROMPTS[DEFAULT_LANG]).get(key, PROMPTS[DEFAULT_LANG][key])


def for_lang(lang: str) -> dict:
    """取整份語言表,**疊在 DEFAULT_LANG 之上**再回傳。

    呼叫端常常一次要用同一種語言的好幾個 key,直接寫
    `PROMPTS.get(lang, PROMPTS[DEFAULT_LANG])` 比較省事,但那樣繞過了 p() 的
    逐 key fallback——某個語言表少一個 key 就會 KeyError,而缺譯本來的設計是
    「安靜退回預設語言」而不是把端點打成 500。這裡先合併再回傳,兩者兼得。
    """
    if lang == DEFAULT_LANG or lang not in PROMPTS:
        return PROMPTS[DEFAULT_LANG]
    return {**PROMPTS[DEFAULT_LANG], **PROMPTS[lang]}


def lang_instruction(lang: str) -> str:
    return p(lang, "lang_directive")

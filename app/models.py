"""API 層的 Pydantic 請求模型。"""
from pydantic import BaseModel

from .config import DEFAULT_TEMPLATE_ID
from .prompts import DEFAULT_LANG


class NoteIn(BaseModel):
    id: str = ""  # 建立時可由前端預先指定(貼圖需要先有 id 才能歸檔圖片)
    name: str
    description: str = ""
    template: str = DEFAULT_TEMPLATE_ID  # 欄位樣板 id
    fields: dict[str, str] = {}          # 樣板欄位值 {key: value}
    tags: list[str] = []
    attachments: list[dict] = []
    # 樂觀鎖:前端讀到這筆時看到的 updated。api_update 拿它跟磁碟上的現值比對,
    # 不同就回 409,不會靜默用舊內容蓋掉別人(或另一個分頁)剛存的東西。
    #
    # ⚠ 這個欄位**不會被存進 note**——api_update 是逐欄位 old.update(...),
    # 不是把 body 整包倒進去,所以它不會漏進 .md 的 frontmatter。
    # None = 呼叫端沒有帶(MCP server、舊版前端),此時跳過檢查以維持相容。
    base_updated: float | None = None


class MarkIn(BaseModel):
    """書籤標記切換。刻意不放進 NoteIn:標記不是內容編輯,不寫歷史版本、不動 updated,
    而且 NoteIn 少帶這個欄位反而是保護——一般編輯的 payload 不帶 marked,
    api_update 的 old.update() 就會原樣保留使用者的書籤,不必每個呼叫端各自記得帶。"""
    marked: bool


class SrsReviewIn(BaseModel):
    """SRS 複習的自評結果(二元:記得 / 忘了)。

    與 MarkIn 同一個道理,刻意不放進 NoteIn:複習不是內容編輯,不寫歷史版本、
    不動 updated;而 NoteIn 不帶這些欄位,api_update 的 old.update() 就會原樣
    保留複習進度,不必每個前端呼叫端各自記得帶(漏一個就靜默清掉半年的進度)。
    """
    remembered: bool


class RestoreIn(BaseModel):
    index: int
    base_updated: float | None = None  # 樂觀鎖,語意同 NoteIn.base_updated


class InviteIn(BaseModel):
    """產生一條站台註冊邀請連結。預設從嚴:一次性、7 天。"""
    uses: int = 1
    ttl_days: int = 7


class MergeIn(BaseModel):
    """把重複的名詞合併成一筆(見 app/service.py:merge_notes)。
    target_id 是要保留的那筆,source_ids 會被併進去並移進回收桶。"""
    target_id: str
    source_ids: list[str] = []


class SimilarIn(BaseModel):
    """編輯器即時提示用:拿還沒存檔的內容問「你可能已經記過這個」。
    走 POST 而不是 GET,因為 fields 是巢狀資料,塞進 query string 只會更難讀。"""
    name: str
    fields: dict[str, str] = {}
    exclude_id: str = ""  # 正在編輯的那筆(編輯既有名詞時當然會跟自己同名)


class RegisterIn(BaseModel):
    email: str
    password: str
    # 站台邀請 token(純 nonce)。持有它就**繞過註冊模式與 email 白名單**——
    # 不繞過的話 admin 要同時做兩件事,邀請連結想解決的漏斗又長回來(見 app/invites.py)。
    invite: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


class PasswordChangeIn(BaseModel):
    """變更/設定 email 登入的密碼(設定 → 帳號)。current_password 在帳號已有
    密碼時必填(要證明是本人,不能只憑一個可能被劫走的 session);Google-only
    帳號(password_hash 為 null)第一次設定密碼時沒有舊密碼可驗,留空即可。"""
    new_password: str
    current_password: str = ""


class LangIn(BaseModel):
    """介面語言(設定 → 偏好設定)。lang=None = 清除,回到跟隨裝置語言;
    合法語碼集合在 users.SUPPORTED_LANGS,由端點驗證。"""
    lang: str | None = None


class RegistrationModeIn(BaseModel):
    mode: str  # "open" | "whitelist" | "closed"


class WhitelistIn(BaseModel):
    emails: list[str] = []  # 整份取代白名單(後端會正規化小寫去重)


class OAuthConfigIn(BaseModel):
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""  # 空字串 = 沿用已存的 secret(不覆蓋)


class AdminFlagIn(BaseModel):
    is_admin: bool


class SharingFlagsIn(BaseModel):
    """公開分享連結與公開筆記的站台總開關。關閉 = 所有既有的連結/快照立刻失效
    (不只是不能產生新的)。"""
    public_share_enabled: bool = False
    public_notebook_enabled: bool = False


class PublishIn(BaseModel):
    """發佈(或重新發佈)一份公開筆記快照。

    tags(逗號分隔,OR 聯集)/ group 決定範圍,兩者皆空 = 整庫(語意同
    GET /api/export)。pid 帶值 = 重新發佈:覆蓋同一份快照、網址不變——
    後端會驗那份快照確實屬於呼叫者。
    """
    title: str = ""
    tags: str = ""
    group: str = ""
    pid: str = ""


class TagRenameIn(BaseModel):
    name: str


class TemplateIn(BaseModel):
    name: str
    fields: list[dict] = []  # [{key, label, placeholder}]
    ai_input_mode: str = "name"  # name=用名詞欄位生成、paste=貼上一段內容生成
    ai_prompt: str = ""          # 這個樣板的 AI 生成指示(內容指示,不含 JSON 格式規則)


class TemplateEnabledIn(BaseModel):
    """切換內建樣板的啟用狀態(停用的樣板不出現在新建名詞的樣板下拉)。"""
    enabled: bool


class TemplateRetargetIn(BaseModel):
    """孤兒樣板的批次轉換(見 service.retarget_template):把掛在 from_id 上的
    名詞整批改掛到 to_id。to_id 必須存在,from_id 不必(它通常正是已被解除的
    外掛樣板 id);from_id 留空 = 轉換**所有**孤兒(template 不在登記簿的名詞)
    ——健康度檢查的明細每類最多列 50 筆,前端不可能湊得齊全部孤兒 id,
    「哪些是孤兒」由後端自己算才完備。"""
    from_id: str = ""
    to_id: str


class TagGroupIn(BaseModel):
    group: str
    tags: list[str]


class TagMergeIn(BaseModel):
    """把 absorb 裡的標籤全部併進 keep(標籤重複偵測的套用動作)。"""
    keep: str
    absorb: list[str] = []


class AISettingsIn(BaseModel):
    """AI 連線設定(**站台層,只有管理者改得動**——見 app/ai_settings.py)。

    ⚠ 每個欄位都是 `| None = None`,語意是「沒帶的欄位就不要動」。刻意不用
    Pydantic 預設值:預設值會讓「使用者沒填」與「使用者明確填了預設值」變得
    無法區分,於是任何只帶部分欄位的呼叫端(mcp_server/server.py 的
    update_ai_settings 只帶三個)都會把沒帶的欄位**靜默打回預設**。這個 schema
    以後還會長,那條 bug 會一再重演,所以在型別上就把它擋掉。
    """
    enabled: bool | None = None
    api_style: str | None = None          # "ollama" | "openai"
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    embed_model: str | None = None        # 語意檢索用;空字串 = 未設定
    desc_limit_enabled: bool | None = None  # 生成說明的字數上限開關(prompt 指示,非硬截斷)
    desc_max_chars: int | None = None       # 上限值;_clean_ai 夾 50–2000


class SemanticReindexIn(BaseModel):
    """limit=0 表示一次做完;>0 表示最多做這麼多筆(前端跑迴圈顯示進度用)。"""
    limit: int = 0


class AIGenerateIn(BaseModel):
    input: str
    template: str = DEFAULT_TEMPLATE_ID  # 依樣板的 ai_prompt 與欄位動態組生成 schema
    lang: str = DEFAULT_LANG  # 生成內容的語言,對應前端 i18n.js 的 LANG(見 app/prompts.py);不支援的值退回英文


class AIArticleNoteIn(BaseModel):
    """文章>關鍵字:提供文章全文與文中一個名詞,請 AI 生成一筆預設樣板的名詞內容。
    前端對每個標註的名詞各呼叫一次(逐筆生成才能顯示進度、部分失敗不拖垮全部)。"""
    keyword: str
    article: str
    lang: str = DEFAULT_LANG


class PluginConfigIn(BaseModel):
    """外掛設定更新:key/value 都是字串,後端只收該外掛預設設定裡存在的 key。"""
    config: dict[str, str] = {}


class PluginEnabledIn(BaseModel):
    """外掛停用/啟用(不動安裝狀態與設定)。"""
    enabled: bool


class AIFieldIn(BaseModel):
    """依名詞目前已填寫的完整內容,請 AI 重新生成指定的單一欄位(新舊由前端比較選用)。"""
    target: str  # "name" | "description" | 樣板欄位 key
    name: str = ""
    description: str = ""
    fields: dict[str, str] = {}
    tags: list[str] = []
    template: str = DEFAULT_TEMPLATE_ID
    lang: str = DEFAULT_LANG


class AIFillIn(BaseModel):
    """一次補齊多個空白欄位(「當初只是很快記下一個名詞」的事後補救)。

    形狀比照 AIFieldIn,只把 target 換成 targets:一次呼叫補完所有空欄位,
    不是每欄各打一次模型(本機模型一輪好幾秒,五個空欄位就是半分鐘)——
    比照 /api/ai/tag-duplicates「整份清單一次送出、刻意不分批」的取向。
    targets 只是**請求**,真正合法的目標由伺服器用 enabled_fields() 交集決定。
    """
    targets: list[str] = []  # "description" | 樣板欄位 key
    name: str = ""
    description: str = ""
    fields: dict[str, str] = {}
    tags: list[str] = []
    template: str = DEFAULT_TEMPLATE_ID
    lang: str = DEFAULT_LANG


class AITagsIn(BaseModel):
    """依名詞目前已填寫的內容,請 AI 分析出最多三個關鍵字標籤。"""
    name: str = ""
    description: str = ""
    fields: dict[str, str] = {}
    tags: list[str] = []  # 已有標籤(讓 AI 避免重複、補不同角度)
    template: str = DEFAULT_TEMPLATE_ID  # 只為了拿欄位 label,讓 AI 的分析情境更清楚
    lang: str = DEFAULT_LANG


class AIGroupTagsIn(BaseModel):
    """AI 自動分組建議(單一批次):把一批標籤各自歸到最適合的群組。
    前端把未分組標籤分批送來以顯示進度;groups 帶入既有+前面批次已提出的群組名,
    讓 AI 跨批次盡量沿用同一組群組名。這一步只回建議、不寫檔。"""
    tags: list[str] = []
    groups: list[str] = []  # 已存在或前面批次已提出的群組名(讓 AI 盡量沿用)
    lang: str = DEFAULT_LANG


class AITagDupIn(BaseModel):
    """標籤相似度重複偵測的語意層:整份標籤清單一次送給 AI,問哪些其實是同一個東西。

    字面變體(Mes/MES、全形半形、標點)由 GET /api/tag-duplicates 用純字串比對抓,
    完全不需要 AI;這一支補的是它抓不到的「回焊爐 / Reflow Oven」那種。
    刻意**不分批**:分批會讓落在不同批次的兩個寫法永遠配不到一起,而標籤名很短,
    整份清單塞得進一個 prompt(超過上限由前端先擋下來)。
    """
    tags: list[str] = []
    lang: str = DEFAULT_LANG


class RateLimitIn(BaseModel):
    """登入失敗鎖定的門檻(設定 → 管理 → 登入保護)。數值的上下界由
    site_settings._clean_rate_limit() 夾住,這裡不重複驗證。"""
    enabled: bool = True
    ip_max_attempts: int = 20
    email_max_attempts: int = 5
    window_minutes: int = 15
    lockout_minutes: int = 15
    trust_forwarded_for: bool = False


class BackupSettingsIn(BaseModel):
    """自動備份設定(設定 → 備份與還原)。"""
    auto_enabled: bool = True
    interval_days: int = 7
    keep: int = 10
    # 每日時刻門檻 "HH:MM"(伺服器本地時間);空 = 不指定,到期就備份。
    # 語意是「不早於」而不是「準時在」——見 backup.auto_backup_due()。
    at_time: str = ""


class HealthCleanupIn(BaseModel):
    """內容健康度檢查的「清理」動作:要清哪幾類問題。

    ⚠ **刻意只有 kinds,沒有任何檔案路徑欄位**,將來也不要加。這支端點會刪檔,
    而且跑在已登入的身分底下;一旦接受用戶端送來的路徑,它就是一個「刪除任意
    檔案」的洞(那些是相對路徑,連 valid_id() 都擋不住)。要刪什麼一律由
    health.cleanup() 自己重掃決定——理由與完整說明見 app/health.py 檔頭第 2 條。

    合法值見 health.CLEANABLE;不認得的值一律被忽略(不報錯,因為多送一個
    未知類別是相容性問題,不是攻擊)。
    """
    kinds: list[str] = []

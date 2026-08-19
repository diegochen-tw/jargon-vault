"""Jargon Vault 的 MCP server:讓 Claude Code / Claude Desktop 等 MCP 用戶端
可以透過標準工具呼叫,完整操作一套「正在跑」的 Jargon Vault 實例
(新增/查詢/更新/刪除名詞、管理標籤與群組、欄位樣板、外掛、AI 生成、匯出入等)。

這支程式**不是** Jargon Vault 主程式(app/)的一部分,是獨立的小工具,對外只透過
HTTP 呼叫 Jargon Vault 的既有 /api/* 端點(見 client.py),不直接碰檔案系統或
SQLite——所有「檔案為真」「先寫檔再更新索引」等既有的一致性保證完全沿用主程式,
這支 server 只是多一個「用戶端」。

啟動方式與設定見同目錄 README.md。環境變數:
    JARGON_BASE_URL       Jargon Vault 網址,預設 http://127.0.0.1:8787
    JARGON_EMAIL          登入用 email(與 JARGON_PASSWORD 成對使用)
    JARGON_PASSWORD       登入用密碼
    JARGON_SESSION_COOKIE 直接提供已登入的 gv_session cookie 值(可取代帳密)
"""
from __future__ import annotations

import base64
from typing import Any

from mcp.server.fastmcp import FastMCP

from client import get_client

mcp = FastMCP("jargon-vault")

# ---------------------------------------------------------------------------
# 帳號
# ---------------------------------------------------------------------------


@mcp.tool()
async def whoami() -> dict:
    """確認目前 MCP server 是用哪個帳號連線到 Jargon Vault。回傳 {id, email}。"""
    resp = await get_client().get("/api/auth/me")
    return resp.json()


# ---------------------------------------------------------------------------
# 名詞(notes)CRUD 與搜尋
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_notes(
    q: str = "",
    tags: str = "",
    group: str = "",
    template: str = "",
    days: int = 0,
) -> dict:
    """搜尋名詞。q 是全文關鍵字;tags 是逗號分隔的標籤(AND,需同時掛有全部標籤);
    group 是標籤群組名稱(OR,掛有群組內任一標籤即命中,跟 tags 是互斥的兩種篩選方式);
    template 是欄位樣板 id(如 jargon-default/english-word/code-snippet);
    days 限制只找最近 N 天內更新過的名詞(0 = 不限制)。回傳 {results: [名詞...]}。"""
    resp = await get_client().get(
        "/api/search",
        params={"q": q, "tags": tags, "group": group, "template": template, "days": days},
    )
    return resp.json()


@mcp.tool()
async def get_note(id: str) -> dict:
    """取得單一名詞的完整內容(含歷史版本 history)。"""
    resp = await get_client().get(f"/api/notes/{id}")
    return resp.json()


@mcp.tool()
async def create_note(
    name: str,
    description: str = "",
    template: str = "jargon-default",
    fields: dict[str, str] | None = None,
    tags: list[str] | None = None,
    id: str = "",
) -> dict:
    """新增一筆名詞。

    - description 是「自訂精簡 markdown」而非標準 markdown,支援:`**粗體**`、
      `` `行內程式碼` ``、三個反引號圍籬的程式碼區塊(可標語言,如 ```python）、
      `![說明](圖片網址)`、`{{color:文字}}` 畫重點、以及一般換行 `\\n` 分段。
    - template 決定 fields 可用的 key(內建樣板:jargon-default 無額外欄位、
      english-word 有別名/例句等單行欄位、code-snippet 有語言等欄位;自訂樣板
      用 list_templates 查詢目前有哪些欄位可填)。不確定欄位 key 時可以只填
      name/description/tags,fields 留空。
    - id 通常留空讓系統自動產生;只有在需要指定固定 id 時才手動帶。
    """
    payload: dict[str, Any] = {
        "id": id,
        "name": name,
        "description": description,
        "template": template,
        "fields": fields or {},
        "tags": tags or [],
    }
    resp = await get_client().post("/api/notes", json=payload)
    return resp.json()


@mcp.tool()
async def create_notes_bulk(notes: list[dict]) -> dict:
    """批次新增多筆名詞(適合「分析完程式碼後一次塞入多筆術語」的情境)。

    notes 是一個陣列,每個元素跟 create_note 的參數同名同義:
    {name, description?, template?, fields?, tags?, id?}。
    逐筆呼叫 /api/notes,單筆失敗不影響其他筆,回傳 {created: [...], errors: [...]}。
    """
    created: list[dict] = []
    errors: list[dict] = []
    client = get_client()
    for i, n in enumerate(notes):
        payload: dict[str, Any] = {
            "id": n.get("id", ""),
            "name": n.get("name", ""),
            "description": n.get("description", ""),
            "template": n.get("template") or "jargon-default",
            "fields": n.get("fields") or {},
            "tags": n.get("tags") or [],
        }
        try:
            resp = await client.post("/api/notes", json=payload)
            created.append(resp.json())
        except Exception as e:  # noqa: BLE001 - 蒐集每筆錯誤,不中斷整批
            errors.append({"index": i, "name": n.get("name", ""), "error": str(e)})
    return {"created": created, "errors": errors}


@mcp.tool()
async def update_note(
    id: str,
    name: str,
    description: str = "",
    template: str = "jargon-default",
    fields: dict[str, str] | None = None,
    tags: list[str] | None = None,
) -> dict:
    """更新既有名詞(整份覆蓋,不是局部合併)。更新前的內容會自動存進歷史版本
    (上限 3 筆,見 restore_note_version)。description 的 markdown 語法規則同 create_note。"""
    payload: dict[str, Any] = {
        "name": name,
        "description": description,
        "template": template,
        "fields": fields or {},
        "tags": tags or [],
    }
    resp = await get_client().put(f"/api/notes/{id}", json=payload)
    return resp.json()


@mcp.tool()
async def delete_note(id: str) -> dict:
    """刪除一筆名詞(連同它的歷史版本與附件/圖片一起刪除,無法復原)。"""
    resp = await get_client().delete(f"/api/notes/{id}")
    return resp.json()


@mcp.tool()
async def restore_note_version(id: str, index: int) -> dict:
    """把名詞回復到某個歷史版本。index 是 get_note 回傳的 history 陣列索引(0 = 最近一次覆蓋前的版本)。
    回復前的當前狀態也會被存進歷史版本,所以這個操作也是可逆的。"""
    resp = await get_client().post(f"/api/notes/{id}/restore", json={"index": index})
    return resp.json()


@mcp.tool()
async def delete_note_assets(id: str) -> dict:
    """清空某筆名詞的所有已上傳圖片/附件檔案(不影響名詞本身的文字內容)。"""
    resp = await get_client().delete(f"/api/notes/{id}/assets")
    return resp.json()


@mcp.tool()
async def upload_attachment(note_id: str, filename: str, content_base64: str) -> dict:
    """替某筆名詞上傳一個附件檔案(任意檔案類型)。content_base64 是檔案內容的 Base64 編碼。
    回傳 {name, path, url},url 可以貼進 description 裡的連結語法引用。"""
    data = base64.b64decode(content_base64)
    files = {"file": (filename, data)}
    resp = await get_client().post(f"/api/notes/{note_id}/attachments", files=files)
    return resp.json()


@mcp.tool()
async def upload_image(note_id: str, filename: str, content_base64: str) -> dict:
    """替某筆名詞上傳一張圖片(供 description 用 `![說明](url)` 內嵌)。
    content_base64 是圖片內容的 Base64 編碼。回傳 {path, url}。"""
    data = base64.b64decode(content_base64)
    files = {"file": (filename, data)}
    resp = await get_client().post(f"/api/notes/{note_id}/images", files=files)
    return resp.json()


# ---------------------------------------------------------------------------
# 標籤與標籤群組
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_tags() -> dict:
    """列出所有標籤(含所屬群組與掛載數量)與所有標籤群組(含群組內名詞數)。
    回傳 {tags: [{name, count, created, group}], groups: [{name, count}]}。"""
    resp = await get_client().get("/api/tags")
    return resp.json()


@mcp.tool()
async def rename_tag(name: str, new_name: str) -> dict:
    """重新命名標籤(所有掛有此標籤的名詞會一併更新)。回傳 {ok, affected} affected 是受影響的名詞數。"""
    resp = await get_client().put(f"/api/tags/{name}", json={"name": new_name})
    return resp.json()


@mcp.tool()
async def delete_tag(name: str) -> dict:
    """刪除標籤(從所有名詞上移除,不影響名詞其他內容)。"""
    resp = await get_client().delete(f"/api/tags/{name}")
    return resp.json()


@mcp.tool()
async def set_tag_group(group: str, tags: list[str]) -> dict:
    """把指定的標籤加入某個群組。group 傳空字串等同把這些標籤移出目前所在的群組。"""
    resp = await get_client().put("/api/tag-groups", json={"group": group, "tags": tags})
    return resp.json()


@mcp.tool()
async def rename_tag_group(name: str, new_name: str) -> dict:
    """標籤群組改名(群組內的標籤本身不變)。新名稱若是既有群組,兩組會合併。"""
    resp = await get_client().put(f"/api/tag-groups/{name}", json={"name": new_name})
    return resp.json()


@mcp.tool()
async def dissolve_tag_group(name: str) -> dict:
    """打散單一標籤群組(群組內的標籤變回未分組,標籤本身不會被刪除)。"""
    resp = await get_client().delete(f"/api/tag-groups/{name}")
    return resp.json()


@mcp.tool()
async def dissolve_all_tag_groups() -> dict:
    """打散全部標籤群組,恢復到只有標籤、沒有群組的狀態。"""
    resp = await get_client().delete("/api/tag-groups")
    return resp.json()


# ---------------------------------------------------------------------------
# 欄位樣板(templates)
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_templates() -> dict:
    """列出所有欄位樣板(內建:jargon-default/english-word/code-snippet,以及使用者自訂樣板),
    每個樣板附帶目前掛著的名詞數與欄位定義(fields: [{key, label, placeholder}])。"""
    resp = await get_client().get("/api/templates")
    return resp.json()


@mcp.tool()
async def create_template(
    name: str,
    fields: list[dict] | None = None,
    ai_input_mode: str = "name",
    ai_prompt: str = "",
) -> dict:
    """建立自訂欄位樣板。fields 是 [{key, label, placeholder}] 的陣列,key 需符合
    `^[a-z][a-z0-9_]{0,31}$` 且不可使用保留字(id/name/description/tags/category/
    attachments/created/updated/history/template/fields)。ai_input_mode 是
    "name"(用名詞欄位生成)或 "paste"(貼上一段內容生成),配合 ai_prompt(AI 生成的內容指示)使用。"""
    payload = {"name": name, "fields": fields or [], "ai_input_mode": ai_input_mode, "ai_prompt": ai_prompt}
    resp = await get_client().post("/api/templates", json=payload)
    return resp.json()


@mcp.tool()
async def update_template(
    id: str,
    name: str,
    fields: list[dict] | None = None,
    ai_input_mode: str = "name",
    ai_prompt: str = "",
) -> dict:
    """更新既有欄位樣板(含內建樣板的欄位/AI 指示皆可修改,但內建樣板不可刪除)。參數規則同 create_template。"""
    payload = {"name": name, "fields": fields or [], "ai_input_mode": ai_input_mode, "ai_prompt": ai_prompt}
    resp = await get_client().put(f"/api/templates/{id}", json=payload)
    return resp.json()


@mcp.tool()
async def delete_template(id: str) -> dict:
    """刪除自訂欄位樣板(內建樣板會回錯誤)。刪除不影響已引用此樣板的名詞,只是欄位定義消失。"""
    resp = await get_client().delete(f"/api/templates/{id}")
    return resp.json()


# ---------------------------------------------------------------------------
# 匯出 / 匯入
# ---------------------------------------------------------------------------


@mcp.tool()
async def export_notes(format: str = "json", tags: str = "", group: str = "") -> dict:
    """匯出名詞。format 是 "json" 或 "csv";tags(逗號分隔,OR 聯集)或 group 可縮小匯出範圍,
    兩者都留空則匯出全部。回傳 {filename, content},content 是檔案的完整文字內容
    (json 是 {version:2, notes, tag_groups} 包裝;csv 帶 UTF-8 BOM 方便 Excel 開啟)。"""
    resp = await get_client().get("/api/export", params={"format": format, "tags": tags, "group": group})
    disposition = resp.headers.get("content-disposition", "")
    filename = f"export.{format}"
    if "filename=" in disposition:
        filename = disposition.split('filename="')[-1].split('"')[0]
    return {"filename": filename, "content": resp.text}


@mcp.tool()
async def import_notes(filename: str, content: str) -> dict:
    """匯入名詞。filename 的副檔名決定解析格式(.json 或 .csv),content 是檔案的完整文字內容
    (相容 v1 純陣列 JSON 與 v2 {notes, tag_groups} 包裝格式;CSV 需含 name 欄)。
    id 已存在的名詞會被覆蓋、其餘新建。回傳 {imported, errors}。"""
    files = {"file": (filename, content.encode("utf-8"))}
    resp = await get_client().post("/api/import", files=files)
    return resp.json()


# ---------------------------------------------------------------------------
# 外掛模組
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_plugins() -> dict:
    """列出所有外掛模組與各自的安裝狀態/設定(目前只有「文章>關鍵字」article-keywords 一個外掛)。"""
    resp = await get_client().get("/api/plugins")
    return resp.json()


@mcp.tool()
async def install_plugin(id: str) -> dict:
    """安裝指定外掛。"""
    resp = await get_client().post(f"/api/plugins/{id}/install")
    return resp.json()


@mcp.tool()
async def uninstall_plugin(id: str) -> dict:
    """解除安裝指定外掛(設定保留,重新安裝時使用者自訂的設定還在)。"""
    resp = await get_client().delete(f"/api/plugins/{id}")
    return resp.json()


@mcp.tool()
async def update_plugin_config(id: str, config: dict[str, str]) -> dict:
    """更新外掛設定。只有該外掛預設設定裡本來就存在的 key 會被接受,其餘忽略。"""
    resp = await get_client().put(f"/api/plugins/{id}/config", json={"config": config})
    return resp.json()


# ---------------------------------------------------------------------------
# AI 生成(本機 Ollama,可選——需要 Jargon Vault 那台機器本身有跑 Ollama)
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_ai_settings() -> dict:
    """取得 Jargon Vault 的 AI 連線設定(啟用開關/API 風格/服務位址/生成模型/嵌入模型)。

    這是**站台層、全站唯一一組**的設定,所有使用者共用。回傳裡沒有 api_key 明文,
    只有 has_api_key 表示有沒有設定過。
    """
    resp = await get_client().get("/api/ai/settings")
    return resp.json()


@mcp.tool()
async def update_ai_settings(
    enabled: bool | None = None,
    api_style: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    embed_model: str | None = None,
) -> dict:
    """更新 AI 連線設定。只送有給的欄位,沒給的原樣保留。

    ⚠ **需要站台管理者權限**:這是全站唯一一組設定,改了所有使用者都會跟著變。
    非管理者的 API key 會收到 403。

    api_style: "ollama"(原生 API)或 "openai"(OpenAI 相容,如 LM Studio /
    llama.cpp / vLLM)。embed_model 是語意檢索用的嵌入模型。
    api_key 傳空字串 = 清除既有金鑰(不是「保持不變」——不帶這個參數才是不變)。

    ⚠ 每個參數都預設 None 而不是各自的預設值:帶了預設值的話,呼叫端只想改
    一個欄位就會把其他欄位一起覆寫掉,而且完全沒有提示。
    """
    payload = {k: v for k, v in {
        "enabled": enabled, "api_style": api_style, "base_url": base_url,
        "api_key": api_key, "model": model, "embed_model": embed_model,
    }.items() if v is not None}
    resp = await get_client().put("/api/ai/settings", json=payload)
    return resp.json()


@mcp.tool()
async def ai_generate(input: str, template: str = "jargon-default", lang: str = "") -> dict:
    """請 Jargon Vault 呼叫它設定好的本機模型服務,依指定樣板的 AI 生成指示與欄位,把 input
    這段文字生成成一筆名詞內容。回傳 {name, description, fields, tags}(建議值,呼叫端仍可再
    修改後才用 create_note 存檔)。需要 Jargon Vault 那台機器已啟用 AI 且模型服務正在跑。
    lang 指定生成內容的語言(zh-Hant/zh-Hans/en/ja/fr/de/it/pt/es/ko/id/hi),不傳 = 英文。"""
    payload = {"input": input, "template": template}
    if lang:
        payload["lang"] = lang
    resp = await get_client().post("/api/ai/generate", json=payload)
    return resp.json()


if __name__ == "__main__":
    mcp.run()

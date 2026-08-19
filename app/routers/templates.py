"""欄位樣板的 CRUD(<使用者目錄>/templates.json)。

樣板只是欄位「定義」,不碰 notes 檔案與搜尋索引,所以不經 service.py
(該層管的是「寫檔+同步索引」的複合約定)——直接讀寫樣板登記簿即可。
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_user_paths, get_vault_admin, get_vault_read, get_vault_write
from ..config import DEFAULT_TEMPLATE_ID
from ..indexer import db
from ..models import TemplateEnabledIn, TemplateIn, TemplateRetargetIn
from ..paths import VaultPaths
from ..plugins import template_seed
from ..sanitize import clean_template_fields
from ..service import retarget_template
from ..templates import DEFAULT_TEMPLATES, load_templates, reset_template, save_templates

router = APIRouter()

# 內建樣板的種子預設名稱:用來判斷使用者是否改過內建樣板的名字(改過就顯示自訂名,否則前端用 i18n)
_DEFAULT_NAME_BY_ID = {t["id"]: t["name"] for t in DEFAULT_TEMPLATES}

# 出廠定義查詢:內建樣板查 DEFAULT_TEMPLATES,查不到再問外掛樣板種子。
# 跨模組查種子放 router 層——templates.py 不能 import plugins(依賴是 plugins → templates 單向)。
_SEED_BY_ID = {t["id"]: t for t in DEFAULT_TEMPLATES}


def _seed_of(tid: str) -> dict | None:
    return _SEED_BY_ID.get(tid) or template_seed(tid)


@router.get("/api/templates")
def api_templates(paths: VaultPaths = Depends(get_vault_read)):
    """樣板清單,附帶每個樣板目前掛著的名詞數(側欄「標籤分類」用)。"""
    templates = load_templates(paths)
    conn = db(paths)
    count = dict(conn.execute("SELECT template, COUNT(*) FROM notes GROUP BY template").fetchall())
    conn.close()
    for t in templates:
        t["count"] = count.get(t["id"], 0)
        # name_is_default:內建樣板且名稱仍是種子預設名(未被使用者改名)。
        # 前端據此決定顯示 i18n 標準名(true)還是使用者的自訂名(false)。
        t["name_is_default"] = bool(t.get("builtin")) and t["name"] == _DEFAULT_NAME_BY_ID.get(t["id"])
        # resettable:有出廠定義(內建或外掛種子)才顯示「恢復預設值」按鈕。
        # 由後端判斷——前端不該自己知道外掛種子清單。
        seed = _seed_of(t["id"])
        t["resettable"] = seed is not None
        # 欄位層級的「還是出廠值嗎」旗標(比照 name_is_default):label/placeholder
        # 的種子是英文基準,未修改時前端依介面語言查 i18n(tplf.<tid>.<key>.*,
        # 見 static/js/fields.js);使用者改過的欄位旗標為 False,顯示自訂值。
        # 計算欄位、不落盤。
        seed_fields = {f["key"]: f for f in (seed or {}).get("fields", [])}
        for f in t.get("fields", []):
            sf = seed_fields.get(f.get("key"))
            f["label_is_default"] = bool(sf) and f.get("label") == sf.get("label")
            f["ph_is_default"] = bool(sf) and f.get("placeholder") == sf.get("placeholder")
    return {"templates": templates}


def _clean_ai_mode(mode: str) -> str:
    # "article" 模式已隨「文章>關鍵字」改成外掛而移除(見 app/plugins.py)
    return mode if mode in ("name", "paste") else "name"


@router.post("/api/templates")
def api_create_template(body: TemplateIn, paths: VaultPaths = Depends(get_vault_admin)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "樣板名稱不可為空")
    try:
        fields = clean_template_fields(body.fields)
    except ValueError as e:
        raise HTTPException(400, str(e))
    tpl = {"id": uuid.uuid4().hex[:12], "name": name, "builtin": False, "fields": fields,
           "ai_input_mode": _clean_ai_mode(body.ai_input_mode), "ai_prompt": body.ai_prompt}
    templates = load_templates(paths)
    templates.append(tpl)
    save_templates(paths, templates)
    return tpl


@router.put("/api/templates/{tid}")
def api_update_template(tid: str, body: TemplateIn, paths: VaultPaths = Depends(get_vault_admin)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "樣板名稱不可為空")
    try:
        fields = clean_template_fields(body.fields)
    except ValueError as e:
        raise HTTPException(400, str(e))
    templates = load_templates(paths)
    for tpl in templates:
        if tpl["id"] == tid:
            tpl["name"] = name
            tpl["fields"] = fields
            tpl["ai_input_mode"] = _clean_ai_mode(body.ai_input_mode)
            tpl["ai_prompt"] = body.ai_prompt
            save_templates(paths, templates)
            return tpl
    raise HTTPException(404, "找不到這個樣板")


@router.put("/api/templates/{tid}/enabled")
def api_set_template_enabled(tid: str, body: TemplateEnabledIn,
                            paths: VaultPaths = Depends(get_vault_admin)):
    """切換內建樣板的啟用狀態。只有內建樣板適用(自訂樣板一律啟用、可直接刪除);
    預設樣板不可停用(新建名詞的預設落點)。"""
    templates = load_templates(paths)
    for tpl in templates:
        if tpl["id"] == tid:
            if not tpl.get("builtin"):
                raise HTTPException(400, "只有內建樣板可以切換啟用狀態")
            if tid == DEFAULT_TEMPLATE_ID and not body.enabled:
                raise HTTPException(400, "預設樣板不可停用")
            tpl["enabled"] = body.enabled
            save_templates(paths, templates)
            return tpl
    raise HTTPException(404, "找不到這個樣板")


@router.post("/api/templates/{tid}/reset")
def api_reset_template(tid: str, paths: VaultPaths = Depends(get_vault_admin)):
    """把內建/外掛樣板恢復成出廠定義(名稱/欄位/AI 指示都覆寫回種子;
    樣板層級的啟用狀態保留,理由見 templates.reset_template)。
    使用者自建樣板沒有出廠定義 → 400。"""
    seed = _seed_of(tid)
    if seed is None:
        if any(t["id"] == tid for t in load_templates(paths)):
            raise HTTPException(400, "這個樣板沒有出廠定義")
        raise HTTPException(404, "找不到這個樣板")
    tpl = reset_template(paths, tid, seed)
    if tpl is None:  # 種子存在但樣板不在庫裡(外掛樣板未安裝)
        raise HTTPException(404, "找不到這個樣板")
    return tpl


@router.post("/api/templates/retarget")
def api_retarget_template(body: TemplateRetargetIn,
                          paths: VaultPaths = Depends(get_vault_write)):
    """把掛在 from_id 上的名詞整批改掛到 to_id(孤兒樣板的批次轉換,
    健康度檢查 missing_template 那一組的「全部轉為預設樣板」按鈕)。

    這支動的是 notes 不是樣板定義,所以走 service 層(檔頭「樣板 CRUD 不經
    service」的宣告不涵蓋它);權限收 write 不收 admin——它改的是名詞內容,
    跟一般編輯同級。to_id 必須存在(轉到不存在的樣板只是製造新孤兒);
    from_id 刻意不驗——它通常正是已被解除安裝的樣板 id。
    """
    to_id = body.to_id.strip()
    from_id = body.from_id.strip()
    if not to_id:
        raise HTTPException(400, "請指定目標樣板")
    known = {t["id"] for t in load_templates(paths)}
    if to_id not in known:
        raise HTTPException(400, "目標樣板不存在")
    if from_id:
        if from_id == to_id:
            raise HTTPException(400, "來源與目標樣板相同")
        return {"count": retarget_template(paths, from_id, to_id)}
    # from_id 留空 = 全部孤兒:健康度的明細每類最多 50 筆,前端湊不齊全部
    # 孤兒 id,「哪些樣板不存在」由這裡對登記簿取差集才完備。
    conn = db(paths)
    rows = conn.execute("SELECT DISTINCT template FROM notes").fetchall()
    conn.close()
    orphans = [r[0] for r in rows if r[0] not in known]
    return {"count": sum(retarget_template(paths, o, to_id) for o in orphans)}


@router.delete("/api/templates/{tid}")
def api_delete_template(tid: str, paths: VaultPaths = Depends(get_vault_admin)):
    templates = load_templates(paths)
    for tpl in templates:
        if tpl["id"] == tid:
            if tpl.get("builtin"):
                raise HTTPException(400, "內建樣板不可刪除")
            # 引用此樣板的名詞不受影響:前端會用名詞自身的 fields 合成欄位定義
            templates.remove(tpl)
            save_templates(paths, templates)
            return {"ok": True}
    raise HTTPException(404, "找不到這個樣板")

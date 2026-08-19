"""外掛模組管理(<使用者目錄>/plugins.json + 站台層封裝型錄)。

外掛的型錄(名稱/描述/版本/介紹/圖片)來自封裝 manifest(app/plugin_catalog.py),
每使用者狀態只有「已安裝 + 停用與否 + 設定」;field-template 分類的安裝/解除/停用
會順帶註冊/移除/開關欄位樣板(templates.json),那段邏輯在 app/plugins.py,
router 維持薄殼。樣板不碰 notes 檔案與搜尋索引,所以仍不經 service.py。

GET /api/plugins 每次先 refresh_catalog():管理者把封裝目錄放進 data/plugins/,
使用者開外掛頁就看得到,不用重啟(幾十個小 JSON,成本可忽略);其他端點只讀快取。
scan_errors 只給 admin——壞封裝的理由要說得出來,但只說給能處理的人聽。

資產端點 GET /api/plugins/{pid}/assets/{filename} 掛在 ALL_ROUTERS 防線內
(需登入),**絕不進 PUBLIC_ROUTERS**;filename 的三關驗證在
plugin_catalog.asset_path()(RE 白名單 + 必須列在 manifest images + resolve
後仍在封裝目錄內),理由同 backup.valid_backup_name():檔名來自網址。
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from .. import plugin_catalog
from ..auth import get_current_user, get_user_paths, is_admin
from ..models import PluginConfigIn, PluginEnabledIn
from ..paths import VaultPaths
from ..plugins import (
    category_of,
    default_config,
    install_plugin,
    load_plugins,
    plugin_ids,
    save_plugins,
    set_plugin_enabled,
    uninstall_plugin,
)

router = APIRouter()


def _require_known(pid: str) -> None:
    if pid not in plugin_catalog.catalog():
        raise HTTPException(404, "找不到這個外掛")


def _lang(lang: str | None) -> str:
    return lang if lang in plugin_catalog.LANGS else plugin_catalog.DEFAULT_LANG


def _out(pid: str, entry: dict, lang: str) -> dict:
    """對外形狀:每使用者狀態(installed/enabled/config)+ 型錄中繼資料
    (category/version/enhances/多語名稱與描述)。中繼資料來自封裝 manifest,
    不存 plugins.json——它是外掛的固有屬性,不是使用者狀態。"""
    cat = plugin_catalog.catalog().get(pid, {})
    return {
        "id": pid,
        "category": category_of(pid),
        "version": cat.get("version", ""),
        "enhances": cat.get("enhances"),
        "source": cat.get("source", ""),
        "name": plugin_catalog.localized(cat, "name", lang),
        "description": plugin_catalog.localized(cat, "description", lang),
        "images": len(cat.get("images") or []),
        **entry,
    }


@router.get("/api/plugins")
def api_plugins(lang: str = "", user: dict = Depends(get_current_user),
                paths: VaultPaths = Depends(get_user_paths)):
    plugin_catalog.refresh_catalog()
    lang = _lang(lang)
    plugins = load_plugins(paths)
    out = {"plugins": [_out(pid, plugins[pid], lang) for pid in plugin_ids()]}
    if is_admin(user):
        out["scan_errors"] = plugin_catalog.catalog_errors()
    return out


@router.get("/api/plugins/{pid}")
def api_plugin_detail(pid: str, lang: str = "",
                      paths: VaultPaths = Depends(get_user_paths)):
    """詳細頁:多回 intro(fallback 鏈見 plugin_catalog.intro_localized)與圖片檔名清單。"""
    _require_known(pid)
    lang = _lang(lang)
    cat = plugin_catalog.catalog()[pid]
    entry = load_plugins(paths)[pid]
    out = _out(pid, entry, lang)
    out["intro"] = plugin_catalog.intro_localized(cat, lang)
    out["images"] = list(cat.get("images") or [])
    return out


@router.get("/api/plugins/{pid}/assets/{filename}")
def api_plugin_asset(pid: str, filename: str,
                     paths: VaultPaths = Depends(get_user_paths)):
    """封裝的介紹圖片/GIF。需登入(ALL_ROUTERS 防線),三關驗證見 asset_path()。"""
    p = plugin_catalog.asset_path(pid, filename)
    if p is None:
        raise HTTPException(404, "找不到這個檔案")
    return FileResponse(p)


@router.post("/api/plugins/{pid}/install")
def api_install_plugin(pid: str, lang: str = "",
                       paths: VaultPaths = Depends(get_user_paths)):
    """安裝。field-template 外掛順帶把樣板種子註冊進 templates.json(只補不覆蓋)。"""
    _require_known(pid)
    if category_of(pid) == "template-enhancement":
        raise HTTPException(400, "此分類的外掛尚未支援安裝")
    return _out(pid, install_plugin(paths, pid), _lang(lang))


@router.delete("/api/plugins/{pid}")
def api_uninstall_plugin(pid: str, lang: str = "",
                         paths: VaultPaths = Depends(get_user_paths)):
    """解除安裝。config 保留不清掉——重新安裝時使用者自訂的設定還在;
    field-template 外掛順帶把樣板從 templates.json 移除(名詞不受影響)。"""
    _require_known(pid)
    return _out(pid, uninstall_plugin(paths, pid), _lang(lang))


@router.put("/api/plugins/{pid}/enabled")
def api_set_plugin_enabled(pid: str, body: PluginEnabledIn, lang: str = "",
                           paths: VaultPaths = Depends(get_user_paths)):
    """停用/啟用(不動安裝狀態與 config)。未安裝或 template-enhancement → 400。"""
    _require_known(pid)
    if category_of(pid) == "template-enhancement":
        raise HTTPException(400, "此分類的外掛尚未支援")
    plugins = load_plugins(paths)
    if not plugins.get(pid, {}).get("installed"):
        raise HTTPException(400, "外掛尚未安裝")
    return _out(pid, set_plugin_enabled(paths, pid, body.enabled), _lang(lang))


@router.put("/api/plugins/{pid}/config")
def api_update_plugin_config(pid: str, body: PluginConfigIn, lang: str = "",
                             paths: VaultPaths = Depends(get_user_paths)):
    """更新外掛設定。只收該外掛預設設定裡存在的 key,忽略其他。"""
    _require_known(pid)
    plugins = load_plugins(paths)
    allowed = default_config(pid)
    for k, v in body.config.items():
        if k in allowed and isinstance(v, str):
            plugins[pid]["config"][k] = v
    save_plugins(paths, plugins)
    return _out(pid, plugins[pid], _lang(lang))

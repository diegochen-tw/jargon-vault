"""
外掛模組登記層:每使用者已安裝外掛與其設定的持久化真相(<使用者目錄>/plugins.json)。

外掛「型錄」不再寫死在這裡——由 app/plugin_catalog.py 掃描封裝目錄
(repo 的 official_plugins/ + 站台的 data/plugins/)建立;本模組只管
「這個使用者裝了哪些、開著沒、各自的設定」:
    {"article-keywords": {"installed": true, "enabled": true, "config": {"ai_prompt": "..."}}}

三種分類(category,來自封裝 manifest,前端外掛管理頁依此分區):
- "ai-tool":AI 工具,程式在主程式、封裝只帶型錄中繼資料與 default_config。
  停用(enabled=False)= 功能入口消失(article-note 端點與編輯器假選項都走
  is_active()),config 保留。
- "field-template":欄位樣板。安裝 = 把封裝自帶的樣板種子註冊進使用者的
  templates.json(**只補不覆蓋**:同 id 已存在就不動,使用者的修改不會被蓋掉);
  解除安裝 = 從 templates.json 移除該樣板(引用它的名詞不受影響——前端
  fields.js:fieldDefsFor() 會用名詞自身的 fields key 合成欄位定義,資料不消失;
  重新安裝會回到封裝的預設樣板定義)。停用 = 把該樣板的 enabled 寫成 False
  (定義與使用者修改**原地保留**,只從新建下拉消失)——與「解除=移除」形成
  清楚的三態。註冊進去的樣板是一般自訂樣板(builtin: False),使用者可在
  設定 → 欄位樣板 自由改欄位與 ai_prompt——也因此可以在那裡被直接刪除;
  刪了之後外掛仍顯示已安裝,解除再重新安裝即可復原。這是刻意不擋的:
  樣板登記層不需要知道外掛的存在,依賴保持單向(plugins → templates,不反向)。
- "template-enhancement":既有樣板的功能強化(承載格式,尚未有實作;
  install_plugin() 對它回錯,router 轉 400)。

顯示名稱/說明的真相在封裝 manifest(12 語,fallback en),**不在前端 i18n.js**
——名稱是封裝的資料,不是 UI 文案。

解除安裝時保留 config 不清掉——重新安裝時使用者自訂的指示還在。

⚠ load_plugins() 對「不在型錄的 stored entry」**原樣保留**(round-trip 不丟):
型錄是動態掃出來的,站台封裝被移除(或官方目錄暫時讀不到)時,下一次 save
若把未知 id 丟掉,就把使用者的安裝狀態與 config 靜默洗掉了。API 層只列型錄
內的 id,保留的 entry 只是不顯示,封裝回來後原樣復活。

本模組只碰檔案系統,不碰 SQLite、不碰 HTTP、不呼叫 Ollama。
"""
import copy
import json
import logging

from . import atomic, plugin_catalog
from .config import ARTICLE_PLUGIN_ID
from .paths import VaultPaths

log = logging.getLogger("jargon_vault")

# 2026-08-18 的內建樣板改組(見 app/templates.py 檔尾的進出紀錄):
# 這兩個從內建降級成外掛,升級路徑在 ensure_plugins() 走「原地降級」。
DEMOTED_PLUGIN_IDS = ("english-word", "plant-id")
# 反方向的那一個:從外掛升成內建,既有帳號裝過的副本要把 builtin 補回 True。
PROMOTED_TEMPLATE_ID = "passage-decoder"


# --- 型錄查詢(委派 plugin_catalog 的快取,簽章與舊版寫死型錄時代完全相同) ---

def plugin_ids() -> list[str]:
    return list(plugin_catalog.catalog().keys())


def category_of(pid: str) -> str:
    entry = plugin_catalog.catalog().get(pid)
    return entry["category"] if entry else ""


def template_seed(pid: str) -> dict | None:
    """field-template 外掛自帶的樣板種子;其他分類回 None。
    種子的 id 在封裝載入時已強制蓋成外掛 id(plugin_catalog._clean_template)。"""
    entry = plugin_catalog.catalog().get(pid)
    return entry["template"] if entry else None


def default_config(pid: str) -> dict:
    entry = plugin_catalog.catalog().get(pid)
    return dict(entry["default_config"]) if entry else {}


# --- 每使用者登記簿 ---

def _normalized(entry: dict | None, pid: str) -> dict:
    entry = entry if isinstance(entry, dict) else {}
    cfg = default_config(pid)
    stored_cfg = entry.get("config")
    if isinstance(stored_cfg, dict):
        cfg.update({k: v for k, v in stored_cfg.items() if k in cfg})
    return {
        "installed": bool(entry.get("installed")),
        # 舊檔沒有 enabled 鍵:一律視為開啟(零遷移,既有使用者升級後行為不變)
        "enabled": entry.get("enabled", True) is not False,
        "config": cfg,
    }


def load_plugins(paths: VaultPaths) -> dict:
    """回傳 {plugin_id: {"installed", "enabled", "config"}}。
    型錄內的 id 一律有 entry(正規化過);不在型錄的 stored entry 原樣保留
    (理由見檔頭),呼叫端要列清單請以 plugin_ids() 為準。"""
    try:
        data = json.loads(paths.plugins_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    out = {}
    known = set(plugin_ids())
    for pid in plugin_ids():
        out[pid] = _normalized(data.get(pid), pid)
    for pid, entry in data.items():
        if pid not in known and isinstance(entry, dict):
            out[pid] = entry  # 原樣保留,不正規化——它的型錄定義現在不在場
    return out


def save_plugins(paths: VaultPaths, plugins: dict) -> None:
    atomic.write_json(paths.plugins_path, plugins)


def is_installed(paths: VaultPaths, pid: str) -> bool:
    return bool(load_plugins(paths).get(pid, {}).get("installed"))


def is_active(paths: VaultPaths, pid: str) -> bool:
    """已安裝且未停用。功能入口(article-note 端點、編輯器假選項)一律看這個,
    不看 is_installed()——停用的意義就是「裝著但入口消失」。"""
    entry = load_plugins(paths).get(pid, {})
    return bool(entry.get("installed")) and entry.get("enabled", True) is not False


def get_plugin_config(paths: VaultPaths, pid: str) -> dict:
    return load_plugins(paths).get(pid, {}).get("config", default_config(pid))


def install_plugin(paths: VaultPaths, pid: str) -> dict:
    """安裝外掛。field-template 外掛順帶把樣板種子註冊進 templates.json——
    **只補不覆蓋**:同 id 樣板已存在(使用者改過、或樣板還在)就原樣不動。
    冪等:重複安裝不會產生第二份樣板;「解除 → 重新安裝」也是樣板被誤刪或
    被改壞時回到預設定義的復原路徑。安裝一律重設 enabled=True(停用後解除、
    再重裝,拿到的是可用狀態,不是繼承上一輪的停用)。"""
    from .templates import load_templates, save_templates  # 延遲 import 避免循環

    if category_of(pid) == "template-enhancement":
        raise ValueError("此分類的外掛尚未支援安裝")
    plugins = load_plugins(paths)
    entry = plugins.setdefault(pid, _normalized(None, pid))
    entry["installed"] = True
    entry["enabled"] = True
    save_plugins(paths, plugins)
    seed = template_seed(pid)
    if seed is not None:
        templates = load_templates(paths)
        if all(t.get("id") != pid for t in templates):
            templates.append(copy.deepcopy(seed))
            save_templates(paths, templates)
    return entry


def uninstall_plugin(paths: VaultPaths, pid: str) -> dict:
    """解除安裝。config 保留不清掉——重新安裝時使用者自訂的設定還在。
    field-template 外掛順帶把樣板從 templates.json 移除:引用它的名詞不受影響
    (fields 的值存在名詞自身上,顯示層會合成欄位定義),資料不消失。"""
    from .templates import load_templates, save_templates  # 延遲 import 避免循環

    plugins = load_plugins(paths)
    entry = plugins.setdefault(pid, _normalized(None, pid))
    entry["installed"] = False
    save_plugins(paths, plugins)
    if template_seed(pid) is not None:
        templates = load_templates(paths)
        kept = [t for t in templates if t.get("id") != pid]
        if len(kept) != len(templates):
            save_templates(paths, kept)
    return entry


def set_plugin_enabled(paths: VaultPaths, pid: str, enabled: bool) -> dict:
    """停用/啟用(不動安裝狀態與 config)。field-template 外掛順帶把
    templates.json 裡該樣板的 enabled 寫成對應值——樣板定義與使用者修改
    **原地保留**,只是從新建下拉消失/回來(依賴方向仍是 plugins → templates 單向)。"""
    from .templates import load_templates, save_templates  # 延遲 import 避免循環

    plugins = load_plugins(paths)
    entry = plugins.setdefault(pid, _normalized(None, pid))
    entry["enabled"] = bool(enabled)
    save_plugins(paths, plugins)
    if template_seed(pid) is not None:
        templates = load_templates(paths)
        tpl = next((t for t in templates if t.get("id") == pid), None)
        if tpl is not None and tpl.get("enabled", True) != bool(enabled):
            tpl["enabled"] = bool(enabled)
            save_templates(paths, templates)
    return entry


def ensure_plugins(paths: VaultPaths) -> None:
    """
    啟動/註冊時的自我修復與一次性遷移:
    1. plugins.json 不存在就建檔(全部未安裝、預設設定)。
    2. 舊版把「文章>關鍵字」當內建欄位樣板放在 templates.json——若還在,
       將其 ai_prompt 搬進外掛設定並標記為已安裝(既有使用者功能不中斷),
       然後從 templates.json 移除。冪等:搬過一次後樣板已不在,不會重複執行。
    3. 「英文單字」(english-word)與「植物辨識」(plant-id)從內建樣板改成
       field-template 外掛(2026-08-18):既有帳號的樣板若還掛著 builtin=True,
       改成 False 並把外掛標記為已安裝——樣板本身(含使用者的修改)**原樣留在
       templates.json**,不像 article-keywords 是整個搬走;降 builtin 是讓它從此
       可編輯可刪除,標記已安裝是讓「解除安裝」成為正常的移除路徑。
       冪等:降過一次就不再是 True。
       (「標準作業程序」(process-sop)2026-08-06 也走過同一條路;封裝已於
        2026-08-18 整包退場,既有帳號的那份樣板留在原地當一般自訂樣板。)
    4. 「一段話」(passage-decoder)從外掛升成內建(2026-08-18)——唯一一次反方向
       搬遷:裝過這個外掛的帳號,templates.json 裡那份是 builtin=False,
       ensure_templates() 的「只補不覆蓋」不會去動它,於是名稱不會跟著介面語言走
       (name_is_default 依賴 builtin 的種子比對)、還會出現在可刪除清單裡。
       這裡把 builtin 補回 True 收斂成與新帳號一致。plugins.json 裡殘留的
       stored entry 不必清:它已不在型錄,load_plugins() 會原樣保留但不顯示。
       冪等:補過一次就已經是 True。

    ⚠ 這些遷移引用的 pid 來自官方封裝目錄;官方目錄缺失(壞部署)時型錄裡
    沒有它們,這裡的 setdefault 護欄保證不會 KeyError 把全站啟動打死——
    但那是不正常的狀態,要在 log 大聲報。
    """
    from .templates import load_templates, save_templates  # 延遲 import 避免循環

    plugins = load_plugins(paths)
    changed = not paths.plugins_path.exists()

    known = plugin_catalog.catalog()
    for pid in (ARTICLE_PLUGIN_ID, *DEMOTED_PLUGIN_IDS):
        if pid not in known:
            log.error("官方封裝目錄缺少「%s」——official_plugins/ 是不是沒跟著部署?", pid)
        plugins.setdefault(pid, _normalized(None, pid))

    templates = load_templates(paths)
    templates_changed = False

    legacy = next((t for t in templates if t.get("id") == ARTICLE_PLUGIN_ID), None)
    if legacy is not None:
        prompt = (legacy.get("ai_prompt") or "").strip()
        if prompt:
            plugins[ARTICLE_PLUGIN_ID]["config"]["ai_prompt"] = prompt
        plugins[ARTICLE_PLUGIN_ID]["installed"] = True
        templates.remove(legacy)
        templates_changed = True
        changed = True

    for pid in DEMOTED_PLUGIN_IDS:
        tpl = next((t for t in templates if t.get("id") == pid), None)
        if tpl is not None and tpl.get("builtin"):
            tpl["builtin"] = False
            plugins[pid]["installed"] = True
            templates_changed = True
            changed = True

    promoted = next((t for t in templates if t.get("id") == PROMOTED_TEMPLATE_ID), None)
    if promoted is not None and not promoted.get("builtin"):
        promoted["builtin"] = True
        templates_changed = True

    if templates_changed:
        save_templates(paths, templates)
    if changed:
        save_plugins(paths, plugins)

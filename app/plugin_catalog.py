"""
外掛封裝型錄(站台層):掃描、驗證、合併兩個封裝來源成一份型錄。

一個封裝 = `<plugin-id>/` 目錄,內含 `plugin.json`(manifest)+ 選配 `assets/`
(介紹圖片/GIF)。**資料型封裝、零程式碼**:功能型外掛(ai-tool 分類)的程式
仍在主程式,封裝只提供型錄中繼資料與預設設定——所以 ai-tool 封裝的 id 必須
出現在 CORE_IMPL_IDS,否則整包拒絕(型錄不能出現裝了沒反應的功能)。

兩個來源(常數在 app/config.py):
  - OFFICIAL_PLUGINS_DIR(repo 內,進版控):官方封裝,GitHub PR 即維護管道。
  - SITE_PLUGINS_DIR(data/plugins/,站台級真相):admin 上傳或直接放檔。
合併規則:官方先掃、站台後掃,**id 衝突官方贏**——允許覆蓋等於讓上傳的檔案
無聲替換官方外掛的種子與預設指示;要改官方封裝,正道就是發 PR。

壞封裝完全不進型錄(授權/資料層的「壞檔一律拒絕」慣例),但逐目錄 try/except
收集 (目錄, 理由) 清單——單一壞封裝絕不讓整份型錄或任何 API 失敗;理由只在
`GET /api/plugins` 對 admin 揭露(說得出原因,但只說給能處理的人聽)。

分層:與 site_settings.py 同層。只 import config/sanitize;不碰 SQLite、不碰
HTTP、不碰使用者目錄。快取是模組層 dict,`refresh_catalog()` 重掃——呼叫點是
create_app() 啟動、GET /api/plugins、admin 上傳/刪除封裝;其餘讀取(含
article-note 的 gating)只讀快取,不付掃描成本。
"""
import io
import json
import re
import shutil
import uuid
import zipfile
from pathlib import Path

from .config import (ARTICLE_PLUGIN_ID, IMAGE_EXTS, OFFICIAL_PLUGINS_DIR,
                     SITE_PLUGINS_DIR)
from .sanitize import clean_template_fields

MANIFEST_NAME = "plugin.json"

# id 同時就是路徑元件(封裝目錄名、資產 URL 的一段),這條正規表示式就是
# 路徑穿越防線——比照 config.ID_RE 之於名詞 id 的角色。
PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
# 介紹圖片檔名白名單:副檔名對齊 config.IMAGE_EXTS(gif 本來就在裡面,動畫直接支援)
PLUGIN_ASSET_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\.(png|jpe?g|gif|webp)$", re.IGNORECASE)

KNOWN_CATEGORIES = {"ai-tool", "field-template", "template-enhancement"}
# ai-tool 分類的「主程式有實作」名單:封裝只帶型錄中繼資料,功能碼在 app/ 裡。
# 未來新增功能型外掛(如英文單字 AI 發音)= 主程式加功能碼 + 這裡加 id + 一份封裝。
CORE_IMPL_IDS = {ARTICLE_PLUGIN_ID}

MAX_TEMPLATE_ICON_LEN = 4  # 樣板代表 icon:一個 emoji(含 ZWJ 組合)綽綽有餘

MAX_MANIFEST_BYTES = 256 * 1024
MAX_ASSET_BYTES = 8 * 1024 * 1024  # 單張介紹圖片/GIF 的上限,GIF 動畫要留空間

LANGS = ("zh-Hant", "zh-Hans", "en", "ja", "fr", "de", "it", "pt", "es", "ko", "id", "hi")
DEFAULT_LANG = "en"

_AI_INPUT_MODES = {"name", "paste"}


class PluginPackageError(ValueError):
    """封裝驗證失敗,message 是給人看的理由(admin 的 scan_errors / 上傳 400)。"""


def _clean_lang_map(raw, *, required: bool, what: str) -> dict:
    """多語欄位:{語言代碼: 字串}。required 時 en 必填非空;未知語言代碼原樣保留
    (寬進:未來加語言時舊封裝不用改),非字串值一律拒絕(嚴出)。"""
    if raw is None and not required:
        return {}
    if not isinstance(raw, dict):
        raise PluginPackageError(f"{what} 必須是 {{語言代碼: 字串}} 物件")
    out = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise PluginPackageError(f"{what} 的鍵與值都必須是字串")
        if v.strip():
            out[k] = v.strip()
    if required and not out.get(DEFAULT_LANG):
        raise PluginPackageError(f"{what} 缺少必填的 en(英文是全部語言的 fallback)")
    return out


def _clean_template(raw, pid: str) -> dict:
    """field-template 封裝的樣板種子:欄位走與樣板 CRUD 同一把尺
    (sanitize.clean_template_fields),種子 id **強制蓋成外掛 id**(外掛與樣板
    一對一的不變量),builtin 強制 False(外掛樣板一律是使用者可編輯的自訂樣板)。"""
    if not isinstance(raw, dict):
        raise PluginPackageError("field-template 封裝必須帶 template 物件")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise PluginPackageError("template.name 不可為空")
    # 選填的代表 icon(編輯器樣板下拉用)。長度上限不是龜毛:manifest 是第三方輸入,
    # 一段長字串塞進 <option> 會把整個下拉撐爛,而那時已經沒有人擋得住它了。
    icon = str(raw.get("icon") or "").strip()
    if len(icon) > MAX_TEMPLATE_ICON_LEN:
        raise PluginPackageError(f"template.icon 太長(限 {MAX_TEMPLATE_ICON_LEN} 個字元,建議一個 emoji)")
    mode = raw.get("ai_input_mode", "name")
    if mode not in _AI_INPUT_MODES:
        raise PluginPackageError(f"template.ai_input_mode「{mode}」不合法(name/paste)")
    try:
        fields = clean_template_fields(raw.get("fields"))
    except ValueError as e:
        raise PluginPackageError(f"template.fields 不合法:{e}") from e
    if not fields:
        raise PluginPackageError("template.fields 不可為空")
    return {
        "id": pid,
        "name": name,
        "icon": icon,
        "builtin": False,
        "enabled": raw.get("enabled", True) is not False,
        "ai_input_mode": mode,
        "ai_prompt": str(raw.get("ai_prompt") or ""),
        "fields": fields,
    }


def load_package(pkg_dir: Path, source: str) -> dict:
    """讀取並驗證一個封裝目錄,回傳型錄 entry。任何不合法 → PluginPackageError。"""
    manifest_path = pkg_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise PluginPackageError(f"缺少 {MANIFEST_NAME}")
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise PluginPackageError(f"{MANIFEST_NAME} 超過 {MAX_MANIFEST_BYTES // 1024}KB 上限")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PluginPackageError(f"{MANIFEST_NAME} 不是合法的 UTF-8 JSON:{e}") from e
    if not isinstance(raw, dict):
        raise PluginPackageError(f"{MANIFEST_NAME} 頂層必須是物件")

    if raw.get("manifest_version") != 1:
        raise PluginPackageError(f"不支援的 manifest_version:{raw.get('manifest_version')!r}(目前只有 1)")

    pid = raw.get("id")
    if not isinstance(pid, str) or not PLUGIN_ID_RE.match(pid):
        raise PluginPackageError(f"id「{pid}」不合法(小寫英文開頭,限 a-z0-9-,64 字內)")
    if pid != pkg_dir.name:
        # id 就是路徑元件:manifest 說自己是誰、目錄名說它在哪,兩者對不上就拒絕,
        # 不猜哪個才對——猜錯的那個就是路徑穿越或型錄錯位。
        raise PluginPackageError(f"id「{pid}」與目錄名「{pkg_dir.name}」不一致")

    category = raw.get("category")
    if category not in KNOWN_CATEGORIES:
        raise PluginPackageError(f"category「{category}」不合法(允許:{'/'.join(sorted(KNOWN_CATEGORIES))})")
    if category == "ai-tool" and pid not in CORE_IMPL_IDS:
        raise PluginPackageError("ai-tool 分類需要主程式實作,此 id 不在支援名單內")

    version = str(raw.get("version") or "").strip()
    if not version or len(version) > 32:
        raise PluginPackageError("version 必須是非空字串(32 字內)")

    enhances = raw.get("enhances")
    if enhances is not None:
        if not isinstance(enhances, str) or not re.match(r"^[a-z][a-z0-9-]{0,63}$", enhances):
            raise PluginPackageError(f"enhances「{enhances}」不合法(樣板/外掛 id 格式)")

    name = _clean_lang_map(raw.get("name"), required=True, what="name")
    description = _clean_lang_map(raw.get("description"), required=False, what="description")
    intro = _clean_lang_map(raw.get("intro"), required=False, what="intro")

    images = raw.get("images") or []
    if not isinstance(images, list):
        raise PluginPackageError("images 必須是檔名陣列")
    for fn in images:
        if not isinstance(fn, str) or not PLUGIN_ASSET_RE.match(fn):
            raise PluginPackageError(f"圖片檔名「{fn}」不合法(英數/_/-,副檔名 {'/'.join(sorted(e.lstrip('.') for e in IMAGE_EXTS))})")
        p = pkg_dir / "assets" / fn
        if not p.is_file():
            raise PluginPackageError(f"images 列了不存在的檔案「assets/{fn}」")
        if p.stat().st_size > MAX_ASSET_BYTES:
            raise PluginPackageError(f"圖片「{fn}」超過 {MAX_ASSET_BYTES // (1024 * 1024)}MB 上限")

    template = None
    if category == "field-template":
        template = _clean_template(raw.get("template"), pid)
    elif raw.get("template") is not None:
        raise PluginPackageError(f"{category} 分類不可帶 template")

    default_config = raw.get("default_config") or {}
    if not isinstance(default_config, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in default_config.items()):
        raise PluginPackageError("default_config 必須是 {字串: 字串} 物件")

    return {
        "id": pid,
        "version": version,
        "category": category,
        "enhances": enhances,
        "name": name,
        "description": description,
        "intro": intro,
        "images": list(images),
        "template": template,
        "default_config": dict(default_config),
        "source": source,
        "dir": pkg_dir,
    }


def _scan_dir(root: Path, source: str, into: dict, errors: list) -> None:
    if not root.is_dir():
        return
    for d in sorted(root.iterdir(), key=lambda p: p.name):
        # `.` 開頭的目錄是上傳流程的暫存/殘骸(.tmp-*/.old-*),一律跳過
        if not d.is_dir() or d.name.startswith("."):
            continue
        try:
            entry = load_package(d, source)
        except PluginPackageError as e:
            errors.append({"dir": f"{source}/{d.name}", "reason": str(e)})
            continue
        except Exception as e:  # 讀檔權限、超長路徑…壞封裝絕不讓整份型錄失敗
            errors.append({"dir": f"{source}/{d.name}", "reason": f"讀取失敗:{e}"})
            continue
        if entry["id"] in into:
            errors.append({"dir": f"{source}/{d.name}",
                           "reason": f"id「{entry['id']}」與官方外掛衝突(官方贏;要改官方封裝請發 PR)"})
            continue
        into[entry["id"]] = entry


def scan_catalog() -> tuple[dict, list]:
    """重掃兩個來源,回 (id→entry, errors)。官方先、站台後,插入順序即顯示順序。"""
    into, errors = {}, []
    _scan_dir(OFFICIAL_PLUGINS_DIR, "official", into, errors)
    _scan_dir(SITE_PLUGINS_DIR, "site", into, errors)
    return into, errors


# 模組層快取:請求路徑(is_installed/article-note gating…)只讀這裡,不付掃描成本
_catalog_cache: dict | None = None
_errors_cache: list = []


def refresh_catalog() -> None:
    global _catalog_cache, _errors_cache
    _catalog_cache, _errors_cache = scan_catalog()


def catalog() -> dict:
    if _catalog_cache is None:
        refresh_catalog()
    return _catalog_cache


def catalog_errors() -> list:
    if _catalog_cache is None:
        refresh_catalog()
    return list(_errors_cache)


def localized(entry: dict, field: str, lang: str) -> str:
    """多語欄位查值:缺語言 fallback en(比照 prompts.for_lang() 的精神)。"""
    d = entry.get(field) or {}
    return d.get(lang) or d.get(DEFAULT_LANG) or ""


def intro_localized(entry: dict, lang: str) -> str:
    """詳細頁介紹的 fallback 鏈:intro[lang] → intro.en → description[lang] → description.en。"""
    return localized(entry, "intro", lang) or localized(entry, "description", lang)


# ── 站台封裝的上傳落地與移除(admin;router 是薄殼,邏輯在這裡) ──

# 解壓後總大小上限:擋 zip bomb。單檔上限(manifest/asset)在 load_package 另外驗。
MAX_PACKAGE_BYTES = 64 * 1024 * 1024


def _safe_zip_entries(z: zipfile.ZipFile) -> list[tuple[str, list[str]]]:
    """zip slip 防護,比照 backup._safe_members() 的教訓:成員路徑照 ZIP 規格用
    "/" 自己切(**不能用 Path.parts**,pathlib 會把 "." 段吃掉),逐段驗。
    備份那邊是白名單「跳過不收」,這裡是**整包拒絕**——備份的成員來自我們自己
    打包,壞成員是雜訊;上傳封裝的成員全是外部輸入,有一個壞的就沒有理由信其他的。"""
    out = []
    total = 0
    for info in z.infolist():
        name = info.filename
        if name.endswith("/"):
            continue
        if name.startswith("/") or "\\" in name or ":" in name:
            raise PluginPackageError(f"封裝內含不合法路徑:{name}")
        parts = name.split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise PluginPackageError(f"封裝內含不合法路徑:{name}")
        total += info.file_size
        if total > MAX_PACKAGE_BYTES:
            raise PluginPackageError(f"封裝解壓後超過 {MAX_PACKAGE_BYTES // (1024 * 1024)}MB 上限")
        out.append((name, parts))
    if not out:
        raise PluginPackageError("ZIP 是空的")
    return out


def install_site_package(content: bytes) -> dict:
    """驗證並落地一個上傳的封裝 zip 到 data/plugins/。回傳新型錄 entry;
    任何不合法 → PluginPackageError,且**不留半套**——驗證全過之前不碰正式位置。

    佈局接受兩種:plugin.json 在 zip 根,或整包包在單一頂層目錄底下(常見的
    「把資料夾右鍵壓縮」產物)。

    ⚠ Windows 上 os.replace/rename 不能覆蓋**既有目錄**,同 id 升級必須
    「舊目錄先 rename 挪開 → 新目錄 rename 進位 → 刪舊」;中途失敗寧可留下
    `.old-*` 殘骸也不留半套(掃描端一律跳過 `.` 開頭目錄,殘骸不進型錄)。"""
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
        bad = z.testzip()
    except zipfile.BadZipFile as e:
        raise PluginPackageError(f"不是合法的 ZIP 檔:{e}") from e
    if bad is not None:
        raise PluginPackageError(f"ZIP 已損毀:{bad}")
    entries = _safe_zip_entries(z)

    # 佈局判定:根目錄有 plugin.json → strip 0;否則必須是單一頂層目錄包住
    if any(parts == [MANIFEST_NAME] for _, parts in entries):
        strip = 0
    else:
        roots = {parts[0] for _, parts in entries}
        if len(roots) != 1 or not any(parts[1:] == [MANIFEST_NAME] for _, parts in entries):
            raise PluginPackageError(
                f"ZIP 佈局不對:{MANIFEST_NAME} 要在根目錄,或整包包在單一目錄底下")
        strip = 1

    SITE_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    tmp_root = SITE_PLUGINS_DIR / f".tmp-{uuid.uuid4().hex}"
    pkg_tmp = tmp_root / "pkg"
    pkg_tmp.mkdir(parents=True)
    try:
        for name, parts in entries:
            rel = parts[strip:]
            if not rel:
                continue
            dest = pkg_tmp.joinpath(*rel)
            # 再保險一次:解析後一定要還在暫存目錄底下(逐段驗過理論上到不了這裡)
            try:
                dest.resolve().relative_to(pkg_tmp.resolve())
            except ValueError:
                raise PluginPackageError(f"封裝內含不合法路徑:{name}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)

        # 先讀 id 把暫存目錄改成正名(load_package 要求 id == 目錄名),再整包驗證
        try:
            raw = json.loads((pkg_tmp / MANIFEST_NAME).read_text(encoding="utf-8"))
            pid = raw.get("id") if isinstance(raw, dict) else None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pid = None
        if not isinstance(pid, str) or not PLUGIN_ID_RE.match(pid):
            raise PluginPackageError(f"{MANIFEST_NAME} 的 id 缺失或不合法")
        pkg_dir = tmp_root / pid
        pkg_tmp.rename(pkg_dir)
        entry = load_package(pkg_dir, "site")

        # 官方贏:同 id 的站台封裝上傳等於無聲替換官方外掛,直接拒絕(要改官方請發 PR)
        if (OFFICIAL_PLUGINS_DIR / pid).is_dir():
            raise PluginPackageError(f"id「{pid}」與官方外掛衝突,不可覆蓋(要改官方封裝請發 PR)")

        target = SITE_PLUGINS_DIR / pid
        old = None
        if target.exists():
            old = SITE_PLUGINS_DIR / f".old-{uuid.uuid4().hex}"
            target.rename(old)  # 同 id 再上傳 = 升級:舊包先挪開
        pkg_dir.rename(target)
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
        refresh_catalog()
        return catalog()[pid]
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def remove_site_package(pid: str) -> None:
    """移除站台封裝(只刪封裝檔,不動任何人的 plugins.json——使用者的安裝狀態
    與 config 由 load_plugins 的保留政策原樣留著,封裝回來即復活)。
    官方封裝不能移除(它在 repo 裡,也輪不到執行期刪)。"""
    if not PLUGIN_ID_RE.match(pid or ""):
        raise PluginPackageError("id 不合法")
    if (OFFICIAL_PLUGINS_DIR / pid).is_dir():
        raise PluginPackageError("官方外掛的封裝不能從站台移除")
    target = SITE_PLUGINS_DIR / pid
    if not target.is_dir():
        raise PluginPackageError("站台沒有這個封裝")
    shutil.rmtree(target)
    refresh_catalog()


def asset_path(pid: str, filename: str) -> Path | None:
    """介紹圖片的實體路徑,三關:pid 在型錄 → 檔名過 RE **且列在 manifest images**
    → resolve 後仍在封裝目錄內。任一關不過回 None(router 轉 404)。"""
    entry = catalog().get(pid)
    if entry is None:
        return None
    if not PLUGIN_ASSET_RE.match(filename or "") or filename not in entry["images"]:
        return None
    p = (entry["dir"] / "assets" / filename).resolve()
    try:
        p.relative_to(entry["dir"].resolve())
    except ValueError:
        return None
    return p if p.is_file() else None

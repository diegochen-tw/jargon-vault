"""
公開分享連結:**不需登入**的單筆名詞讀取。

前身是 `shared_library.py`(跨使用者的共用庫唯讀查詢視圖),已整個移除。共用庫
是**活視圖**,所以有一個必然失敗的模式:發佈者離職、帳號一刪,那份視圖立刻變空,
接手的人什麼都拿不到。取代它的是公開筆記(`publish.py`)的**凍結快照**——複本在
發佈當下就算好了,活得比帳號久。

留下來的另一條路就是這裡:對**單一**名詞產生 `<uid>.<nonce>` 票券,不需登入即可讀。
它解的是完全不同的問題(給沒有帳號的人看一個行話的解釋),所以不隨共用庫一起消失。

⚠ 這是全 app/ 底下**唯一**會用「不是 session 裡那個 uid」去呼叫 `user_paths()`
的地方,而且只在 `resolve_share_token()` 的六關全部通過之後。其他任何模組都不得
這樣做——那句話就是 code review 的檢查點。

本模組不寫任何檔案、不碰 HTTP。
"""
from . import share_links, site_settings
from .config import valid_id
from .paths import VaultPaths, user_paths
from .storage import note_path, read_note_file
from .templates import enabled_fields, get_template
from .users import find_by_id


def owner_label(user_id: str) -> str:
    """公開分享頁上顯示的作者名稱:email 的 local part。

    把完整 email 攤給站外的讀者是不必要的洩漏。共用庫時代這裡還會先讀
    `sharing.json` 的自訂顯示名稱,那份設定隨共用庫一起移除了。
    """
    user = find_by_id(user_id)
    email = (user or {}).get("email", "")
    return email.split("@", 1)[0] if email else user_id


def _rewrite_assets(text: str, nid: str, prefix: str) -> str:
    """把 assets/<nid>/ 改寫成公開頁專用的資產路徑。

    前端所有取圖的地方(list.js:cardImages、detail.js 附件區、markdown.js 的
    ![](…))都是「'/' + path 去掉開頭斜線」直接拼,所以**在後端投影時改寫完,
    前端一行都不用改**。反過來讓前端特判 base url,markdown.js 就得多一個
    「這份內容屬於誰」的概念,那條「連結/圖片語法只在兩個地方」的規則會被撐破。
    """
    return (text or "").replace(f"assets/{nid}/", prefix)


def project_note(paths: VaultPaths, note: dict, prefix: str, label: str) -> dict:
    """公開投影的共同核心(單筆分享頁與公開筆記快照共用)。刻意很窄:

    剔除 tags 與 id:標籤常常是公司內部代號(「客戶A」「PCBA線」),對站外
    的讀者是純粹的洩漏,而且對「解釋一個行話」這個用途毫無幫助;id 也不回
    (公開筆記的 notes.json 由呼叫端自行附上,單筆分享則完全用不到)。

    fields 在這裡就攤平成 [{label, value}]:前端的 fields.js:fieldDefsFor() 需要
    state.allTemplates,而公開頁刻意不載入那一整套;後端攤好則前端零邏輯,
    順便也不必把樣板 id 洩漏出去。

    prefix 是資產 URL 的改寫目標(`assets/<nid>/` 換成它):單筆分享是
    `s/<token>/assets/`(nid 不出現在 URL,由 token 解出);公開筆記是
    `p/<pid>/assets/<nid>/`(一份快照多筆名詞,nid 必須在路徑上)。
    """
    nid = note["id"]
    values = note.get("fields") or {}
    rows = []
    tpl = get_template(paths, note.get("template", ""))
    used = set()
    for d in enabled_fields(tpl):
        key = d.get("key", "")
        if values.get(key):
            rows.append({"label": d.get("label") or key, "value": values[key]})
            used.add(key)
    # 樣板被改過之後殘留的 key:有值就附在後面,比照 fields.js 的處理,資料不靜默消失
    for key, val in values.items():
        if key not in used and val:
            rows.append({"label": key, "value": val})
    return {
        "owner_label": label,
        "name": note["name"],
        "description": _rewrite_assets(note.get("description", ""), nid, prefix),
        "fields": rows,
        "attachments": [
            {"name": a.get("name", ""),
             "path": _rewrite_assets(a.get("path", ""), nid, prefix),
             "description": a.get("description", "")}
            for a in (note.get("attachments") or [])
        ],
        "created": note.get("created", 0),
        "updated": note.get("updated", 0),
    }


def public_share_view(paths: VaultPaths, note: dict, token: str, label: str) -> dict:
    """單筆分享頁的投影:project_note 的薄殼,只補上分享頁專用的資產前綴。"""
    return project_note(paths, note, f"s/{token}/assets/", label)


def resolve_share_token(token: str) -> tuple[VaultPaths, dict] | None:
    """公開分享連結的**唯一入口**。回 None 代表無效,呼叫端一律 404。

    token 形狀是 `<uid>.<nonce>`,兩段都是明碼——刻意不簽章:驗證本來就得拿
    nonce 去 owner 的 shares.json 查表,而 nonce 本身就是那個秘密(72 bits 隨機),
    簽章擋不住任何簽章以外的東西,卻會讓 SESSION_SECRET 一輪替就打死所有既有
    連結,URL 也長四倍。

    六關:
      1. 站台總開關關閉 —— 關掉就是**所有既有連結立刻失效**,不是只擋新建
      2. token 形狀不對 / uid 不合法
      3. 帳號已刪
      4. nonce 不在登記簿(已撤銷或重新產生過),**或已過期**——建立超過
         config.SHARE_LINK_TTL_HOURS(48h)的連結由 share_links.resolve_nonce
         視為不存在,對這裡是同一關
      5. 檔案讀不到 —— 名詞被刪(搬進回收桶)時連結自動 404;還原後又能用。
         這是「檔案為真」的自然結果,是預期行為不是 bug,別把它「修掉」。
      6. 通過
    """
    if not site_settings.public_share_enabled():
        return None
    uid, _, nonce = (token or "").partition(".")
    if not uid or not nonce or not valid_id(uid):
        return None
    if not find_by_id(uid):
        return None
    paths = user_paths(uid)
    nid = share_links.resolve_nonce(paths, nonce)
    if not nid:
        return None
    note = read_note_file(note_path(paths, nid))
    if not note:
        return None
    return paths, note

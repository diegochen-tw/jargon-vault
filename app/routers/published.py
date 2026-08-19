"""
公開筆記快照的**公開面**(/p/* 與 /api/p/*)——**不需要登入**。

這是 PUBLIC_ROUTERS 的第四個成員。⚠ 這裡每一支端點都完全沒有身分,往裡面
加東西要格外小心;也**絕不要**為了免登入去改 auth.get_current_user 加
anonymous 分支(理由見 routers/share.py 檔頭)。

與單筆分享(/s/*)的關鍵差別:快照是**凍結複本**,住在 data/published/<pid>/
——這裡的每一支端點都是**純檔案讀取**,完全不經 user_paths()、不查任何
使用者目錄。帳號刪了快照照樣能讀,那是刻意的(知識傳承,見 app/publish.py)。

授權只有兩關,每次請求重算、不快取:
  1. 站台總開關(public_notebook_enabled)關閉 —— 所有快照立刻 404
     (刻意 404 不是 403:同「失敗一律 404」的枚舉防護慣例)
  2. pid 過 valid_id() 且 manifest 讀得到

開關關/pid 不合法/快照不存在,一律**同一句 404 文案**——區分開來只是把
登記簿內部狀態洩漏給拿著亂猜 pid 的人。
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from .. import publish, site_settings
from ..config import PUBLISHED_DIR, STATIC_DIR, valid_id
from .share import NOINDEX_HEADERS

router = APIRouter()

_NOT_FOUND = "找不到這份公開筆記"


def _require_publication(pid: str) -> dict:
    """兩關驗證(見檔頭)。通過回 manifest,失敗一律同文案 404。"""
    if not site_settings.public_notebook_enabled():
        raise HTTPException(404, _NOT_FOUND)
    m = publish.manifest_of(pid)   # 內含 valid_id(不合法回 None)
    if not m:
        raise HTTPException(404, _NOT_FOUND)
    return m


@router.get("/p/{pid}")
def published_page(pid: str):
    """公開筆記頁的外殼。內容由 static/js/publish.js 打 /api/p/{pid} 取得。

    刻意不在這裡判斷 pid 有效性:HTML 一律回 200,無效由前端顯示「找不到」。
    這樣才不會用 HTTP 狀態碼把「這個 pid 存在」洩漏給掃描的人(同 /s/ 的取捨)。
    """
    return FileResponse(STATIC_DIR / "publish.html", headers=NOINDEX_HEADERS)


@router.get("/api/p/{pid}")
def api_published(pid: str):
    m = _require_publication(pid)
    notes = publish.load_notes(pid)
    if notes is None:
        raise HTTPException(404, _NOT_FOUND)
    return JSONResponse({
        "title": m.get("title") or "",
        "owner_label": m.get("owner_label", ""),
        "created": m.get("created", 0),
        "note_count": m.get("note_count", len(notes)),
        "notes": notes,
    }, headers=NOINDEX_HEADERS)


@router.get("/p/{pid}/export.zip")
def api_published_export(pid: str):
    _require_publication(pid)
    p = PUBLISHED_DIR / pid / publish.EXPORT_NAME
    if not p.is_file():
        raise HTTPException(404, _NOT_FOUND)
    return FileResponse(p, media_type="application/zip", headers={
        **NOINDEX_HEADERS,
        "Content-Disposition": 'attachment; filename="jargon-vault-published.zip"',
    })


@router.get("/p/{pid}/assets/{nid}/{filename}")
def api_published_asset(pid: str, nid: str, filename: str):
    """快照資產。三段防穿越:pid 過 _require_publication(內含 valid_id)、
    nid 過 valid_id、filename 釘死成單一檔名,最後 resolve 再保險一次。"""
    _require_publication(pid)
    if not valid_id(nid):
        raise HTTPException(404, _NOT_FOUND)
    if Path(filename).name != filename:  # 防路徑穿越(比照 share.py)
        raise HTTPException(400, "不合法的檔名")
    assets_root = PUBLISHED_DIR / pid / "assets"
    p = assets_root / nid / filename
    try:
        if not p.resolve().is_relative_to(assets_root.resolve()):
            raise HTTPException(404, _NOT_FOUND)
    except OSError:
        raise HTTPException(404, _NOT_FOUND)
    if not p.is_file():
        raise HTTPException(404, _NOT_FOUND)
    return FileResponse(p, headers=NOINDEX_HEADERS)

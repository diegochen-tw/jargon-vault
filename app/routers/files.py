"""圖片、附件上傳、資產目錄清理與存取。實體檔案存於 <使用者目錄>/notes/assets/<note_id>/。

/assets/<nid>/<filename> 原本是無驗證的 StaticFiles 掛載,改多使用者後
換成這裡的動態路由(需登入),但 URL 形狀完全不變——既有筆記說明欄裡
直接寫死的 `![](/assets/<nid>/<file>)` 內嵌圖片路徑因此不用重寫。
"""
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..auth import get_user_paths, get_vault_admin, get_vault_read, get_vault_write
from ..config import IMAGE_EXTS, MAX_UPLOAD_BYTES, valid_id
from ..paths import VaultPaths
from ..service import all_attachments, replace_note_asset

router = APIRouter()

CHUNK = 1 << 20  # 分塊讀取的大小(1MB)


def _require_valid_id(nid: str) -> None:
    if not valid_id(nid):
        raise HTTPException(400, "不合法的 ID")


async def _save_upload(paths: VaultPaths, nid: str, file: UploadFile, ext: str) -> str:
    """存檔並回傳相對路徑(assets/<nid>/<隨機檔名>)。

    分塊寫入而不是一次 read() 全部:大檔不整包進記憶體,而且超過
    MAX_UPLOAD_BYTES 時可以立刻中斷、把寫到一半的檔案刪掉。
    """
    fname = f"{uuid.uuid4().hex[:10]}{ext}"
    dest_dir = paths.assets_dir / nid
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / fname
    written = 0
    try:
        with dest.open("wb") as fh:
            while chunk := await file.read(CHUNK):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f"檔案太大(上限 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")
                fh.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)  # 不留寫到一半的半成品
        raise
    return f"assets/{nid}/{fname}"


@router.post("/api/notes/{nid}/images")
async def api_upload_image(nid: str, file: UploadFile = File(...),
                            paths: VaultPaths = Depends(get_vault_write)):
    _require_valid_id(nid)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in IMAGE_EXTS:
        ext = ".png"
    rel = await _save_upload(paths, nid, file, ext)
    return {"path": rel, "url": f"/{rel}"}


@router.post("/api/notes/{nid}/attachments")
async def api_upload_attachment(nid: str, file: UploadFile = File(...),
                                 paths: VaultPaths = Depends(get_vault_write)):
    _require_valid_id(nid)
    orig_name = file.filename or "file"
    rel = await _save_upload(paths, nid, file, Path(orig_name).suffix)
    return {"name": orig_name, "path": rel, "url": f"/{rel}"}


def _require_bare_filename(filename: str) -> None:
    # 防路徑穿越(StaticFiles 原本內建這個防護,手動路由要自己補)
    if Path(filename).name != filename:
        raise HTTPException(400, "不合法的檔名")


@router.get("/api/assets")
def api_list_assets(paths: VaultPaths = Depends(get_vault_read)):
    """列出所有名詞的所有附件,給設定裡的「批次壓縮既有圖片」列舉用。"""
    return {"items": all_attachments(paths)}


@router.put("/api/notes/{nid}/assets/{filename}")
async def api_replace_asset(nid: str, filename: str, file: UploadFile = File(...),
                            paths: VaultPaths = Depends(get_vault_write)):
    """置換一個既有附件的實體檔,並把名詞裡的附件路徑改指到新檔。

    給「批次壓縮既有圖片」用:壓縮在前端做(後端沒有影像處理相依),這裡只負責
    存新檔 + 改路徑。附件檔名一律是隨機 uuid,沒辦法原地覆寫,所以必然產生新
    路徑;而改路徑若走一般的 PUT /api/notes/{nid},就會 bump updated 又寫一筆
    歷史版本——重壓圖片不是內容編輯,不該有那些副作用,故走這支專用端點
    (理由與實作細節見 service.replace_note_asset)。

    刻意不驗證附件是不是圖片:要壓什麼由前端的 isImageFile() 決定(唯一真相),
    後端只做路徑與大小的防護。
    """
    _require_valid_id(nid)
    _require_bare_filename(filename)
    old_rel = f"assets/{nid}/{filename}"
    if not (paths.notes_dir / old_rel).is_file():
        raise HTTPException(404, "找不到這個檔案")
    new_rel = await _save_upload(paths, nid, file, Path(file.filename or "").suffix)
    note = replace_note_asset(paths, nid, old_rel, new_rel)
    name = next((a["name"] for a in note["attachments"] if a["path"] == new_rel), "")
    return {"path": new_rel, "name": name}


@router.delete("/api/notes/{nid}/assets")
def api_delete_assets(nid: str, paths: VaultPaths = Depends(get_vault_write)):
    # 清掉整個 assets/{nid}/ 資料夾——貼上的圖片與附件共用同一個目錄,
    # 取消新建時兩者要一起清掉,不需要分開兩支 API
    _require_valid_id(nid)
    shutil.rmtree(paths.assets_dir / nid, ignore_errors=True)
    return {"ok": True}


@router.get("/assets/{nid}/{filename}")
def api_get_asset(nid: str, filename: str, paths: VaultPaths = Depends(get_vault_read)):
    _require_valid_id(nid)
    _require_bare_filename(filename)
    p = paths.assets_dir / nid / filename
    if not p.is_file():
        raise HTTPException(404, "找不到這個檔案")
    return FileResponse(p)

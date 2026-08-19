"""內容健康度檢查:一次掃出壞掉的附件、破圖、孤兒檔、斷連結與待整理的內容。

規則全在 app/health.py(只讀,不寫檔、不碰 HTTP),這裡只是薄殼。

⚠ 端點刻意叫 `/api/content-health` 而不是 `/api/health`:後者在 web 服務的慣例
裡是「伺服器活著嗎」的探針(k8s liveness、反向代理的健康檢查),兩者撞名會讓
之後想加真正的探針時沒有名字可用,也可能讓維運人員把這支需要登入、會掃全庫的
端點誤接成監控目標。

比照 /api/duplicates 與 /api/assets 走 get_vault_read:讀取權即可(它不改任何東西)。
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..auth import get_vault_read, get_vault_write
from ..health import cleanup, list_cleanups, scan, valid_cleanup_name
from ..models import HealthCleanupIn
from ..paths import VaultPaths

router = APIRouter()


@router.get("/api/content-health")
def api_content_health(paths: VaultPaths = Depends(get_vault_read)):
    """全庫掃描:回傳 {notes, assets, groups}(設定 → 整理與清理的「內容健康度檢查」)。"""
    return scan(paths)


@router.post("/api/content-health/cleanup")
def api_content_health_cleanup(body: HealthCleanupIn,
                               paths: VaultPaths = Depends(get_vault_write)):
    """清掉指不到東西的圖片引用與孤兒檔,**先打包成 ZIP 存證**。

    收 write 權(它會刪東西),而 GET /api/content-health 只收 read。

    ⚠ body 只帶 `kinds`(要清哪幾類),**沒有、也絕不可以有檔案路徑**:
    這支端點跑在已登入的身分底下,接受路徑就等於開一個「刪除任意檔案」的洞
    (那些是相對路徑,連 valid_id() 都擋不住)。要刪什麼由 health.cleanup()
    自己重掃決定,理由見 app/health.py 檔頭第 2 條。
    """
    return cleanup(paths, body.kinds)


@router.get("/api/content-health/cleanup")
def api_list_cleanups(paths: VaultPaths = Depends(get_vault_read)):
    """存證 ZIP 清單(新到舊)。下載失敗時的第二次機會——那包裡是已經被刪掉的東西。"""
    return {"items": list_cleanups(paths)}


@router.get("/api/content-health/cleanup/{name}/download")
def api_download_cleanup(name: str, paths: VaultPaths = Depends(get_vault_read)):
    """下載存證 ZIP。

    ⚠ 一定要先過 valid_cleanup_name()(白名單正規表示式),理由同
    backup.valid_backup_name():檔名來自網址,少了這一關 `../../users.json`
    就能被下載。
    """
    if not valid_cleanup_name(name):
        raise HTTPException(400, "不合法的檔名")
    f = paths.cleanup_dir / name
    if not f.is_file():
        raise HTTPException(404, "找不到這個檔案")
    return FileResponse(f, media_type="application/zip", filename=name)

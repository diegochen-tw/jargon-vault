"""
公開分享連結(/s/* 與 /api/s/*)——**不需要登入**。

這是 PUBLIC_ROUTERS 的第二個成員(第一個是 pages.py 的首頁)。

⚠ 為什麼是「另一組不套 dependency 的 router」而不是在 auth.get_current_user
裡加一個 anonymous 分支:那支現在是「cookie 缺 / 壞 / 過期 / 查無人 → 一律 401」
的鐵板,整個 ALL_ROUTERS 的防線就靠它。在上面鑿一個洞,就等於讓每一支需要登入
的端點都多一條要重新驗證的路徑。往 PUBLIC_ROUTERS 加東西要格外小心。

授權完全委派給 public_share.resolve_share_token()(六關,見該函式)。這裡只
負責:HTTP 狀態碼、防爬蟲標頭、以及資產路徑的防穿越。

⚠ 資產端點的 nid **完全不出現在 URL**,只從 token 解出來——路徑裡可被操控的
只剩 filename 一段,而它已被釘死成單一檔名。所以這支公開端點的攻擊面比登入後
的 /assets/{nid}/{filename} 還小。
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from .. import public_share
from ..config import STATIC_DIR

router = APIRouter()

# 分享頁與其資產一律不給搜尋引擎收錄。連結是「拿到的人看得到」,不是「公開發表」。
NOINDEX_HEADERS = {
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    # 全站唯一一支 robots.txt,公開面的路徑都在這裡列:/s/ 是單筆分享,
    # /p/ 是公開筆記快照(routers/published.py)。
    return "User-agent: *\nDisallow: /s/\nDisallow: /p/\n"


@router.get("/s/{token}")
def share_page(token: str):
    """分享頁的外殼。內容由 static/js/share.js 打 /api/s/{token} 取得。

    刻意不在這裡就判斷 token 有效性:HTML 一律回 200,無效由前端顯示「找不到
    這個分享連結」。這樣才不會用 HTTP 狀態碼把「這個 token 存在」洩漏給掃描的人。
    """
    return FileResponse(STATIC_DIR / "share.html", headers=NOINDEX_HEADERS)


@router.get("/api/s/{token}")
def api_share_note(token: str):
    resolved = public_share.resolve_share_token(token)
    if not resolved:
        raise HTTPException(404, "找不到這個分享連結")
    paths, note = resolved
    label = public_share.owner_label(paths.vault_id)
    # 回 JSONResponse 而不是裸 dict,才掛得上防爬蟲標頭——三支公開端點都要有,
    # 內容本身(JSON)一樣不該被索引或被中介快取住。
    return JSONResponse(public_share.public_share_view(paths, note, token, label),
                        headers=NOINDEX_HEADERS)


@router.get("/s/{token}/assets/{filename}")
def api_share_asset(token: str, filename: str):
    resolved = public_share.resolve_share_token(token)
    if not resolved:
        raise HTTPException(404, "找不到這個檔案")
    paths, note = resolved
    if Path(filename).name != filename:  # 防路徑穿越(比照 files.py:_require_bare_filename)
        raise HTTPException(400, "不合法的檔名")
    # nid 來自 token 解出來的那筆名詞,不是 URL——分享一筆名詞就只授權那一筆的資產
    p = paths.assets_dir / note["id"] / filename
    try:
        if not p.resolve().is_relative_to(paths.assets_dir.resolve()):
            raise HTTPException(404, "找不到這個檔案")
    except OSError:
        raise HTTPException(404, "找不到這個檔案")
    if not p.is_file():
        raise HTTPException(404, "找不到這個檔案")
    return FileResponse(p, headers=NOINDEX_HEADERS)

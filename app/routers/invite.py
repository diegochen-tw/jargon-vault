"""
站台註冊邀請連結的**公開**面:邀請頁與預覽 API。

⚠ 這個 router 在 `PUBLIC_ROUTERS` 裡——**這裡每一支端點都完全沒有身分**。
往裡面加東西要格外小心。會寫入的動作(註冊)一律不在這裡:

    註冊(帶 invite 欄位) → POST /api/auth/register

這裡只回答一件事:「這條連結還有效嗎」——那是收到連結的人在決定
要不要註冊之前必須看得到的資訊。

**回應刻意很窄**:只有 valid 一個布林。無效與過期也不區分(一律「無效或
已過期」)——區分開來只是把登記簿內部狀態洩漏給拿著亂猜 token 的人。
"""
from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from .. import invites
from ..config import STATIC_DIR

router = APIRouter()

# 分享頁那幾支公開端點已經在用的同一組防爬蟲標頭:邀請連結同樣不該被索引,
# 也不該被中介快取住(它是一次性的秘密)。
_NOINDEX = {"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store"}


@router.get("/invite/{token}")
def invite_page(token: str):
    """邀請頁的外殼。

    刻意直接送 index.html 而不是另做一頁(與公開分享 `share.html` 的取捨相反):
    這一頁**需要**登入/註冊表單,而那整套 UI 就在 index.html 裡。再做一份等於
    把登入表單、密碼規則、Google 按鈕、錯誤訊息全部複製一次,兩份遲早漂移。
    前端 boot() 看到路徑是 /invite/… 就先打下面那支預覽 API(見 app.js)。
    """
    return FileResponse(STATIC_DIR / "index.html", headers=_NOINDEX)


@router.get("/api/invite/{token}")
def api_peek_invite(token: str):
    """預覽:這條連結還有效嗎。**不消耗**它。

    無效一律回 200 + `{valid: false}` 而不是 404——前端要能在同一個頁面上
    把「這條連結無效或已過期」講清楚,而 404 在 fetch 端只會變成一個沒有訊息的失敗。
    """
    return JSONResponse({"valid": invites.peek(token) is not None}, headers=_NOINDEX)

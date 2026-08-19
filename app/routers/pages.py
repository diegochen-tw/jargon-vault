"""首頁。"""
from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..config import STATIC_DIR

router = APIRouter()


@router.get("/")
def home():
    # no-cache = 每次都要帶 ETag/Last-Modified 回來重驗(沒改版就是便宜的 304),
    # 不是不快取。少了它,瀏覽器與反向代理會對 HTML 做啟發式快取——`?v=` 版號
    # 機制只保護 .css/.js,保護不到 index.html 本身,結果就是「只改了 HTML 才會
    # 出現的 UI」(例如搜尋列裡的語意開關)在手機上永遠看不到,又沒有硬重整可按。
    return FileResponse(STATIC_DIR / "index.html",
                        headers={"Cache-Control": "no-cache"})

"""
路由層:依資源切分,每個檔案一種資源。

    auth.py      /api/auth/*               註冊/登入/登出/Google OAuth(不需要登入)
    pages.py     GET /                     首頁(不需要登入,前端自己判斷登入狀態)
    share.py     /s/*、/api/s/*            單筆名詞的公開分享連結(**不需要登入**)
    invite.py    /invite/*、/api/invite/*  站台註冊邀請的預覽(**不需要登入**)
    published.py /p/*、/api/p/*            公開筆記快照的讀取面(**不需要登入**)
    publish.py   /api/publish              公開筆記快照的發佈/撤銷(擁有者面)
    search.py    GET /api/search           搜尋
    semantic.py  /api/semantic/*           語意檢索(向量索引管理 + 混合檢索)
    srs.py       /api/srs/*                間隔重複複習(抽卡 + 自評;Leitner 排程)
    taxonomy.py  GET /api/tags             標籤/群組統計與標籤群組管理
    notes.py     /api/notes CRUD + restore 名詞本體
    links.py     /api/names、/api/notes/{id}/links   `[[名詞]]` 連結與反向連結
    dedup.py     /api/similar|duplicates、POST /api/notes/merge  重複偵測與合併
    health.py    GET /api/content-health   內容健康度檢查(破圖/孤兒檔/斷連結,唯讀)
    trash.py     /api/trash                回收桶(列出/還原/永久刪除;保留 30 天)
    files.py     /api/notes/{id}/images|attachments|assets、GET /assets/{nid}/{file}  檔案上傳/存取
    transfer.py  /api/export|import        批次匯出入(可依標籤/群組選擇性匯出)
    templates.py /api/templates CRUD       欄位樣板
    plugins.py   /api/plugins              外掛模組管理(安裝/解除/設定)
    ai.py        /api/ai/settings|generate 本機 Ollama 串接(AI 生成)

除了 auth/pages/share,其餘 router 掛載時都套 dependencies=[Depends(get_current_user)]
(見 app/__init__.py),所以這裡每個 handler 只管拿 Depends(get_user_paths)
的每使用者路徑,不用重複檢查登入態。

新增一組 API 時:建新檔案 → 在這裡的 ALL_ROUTERS 掛上即可。

⚠ PUBLIC_ROUTERS 現在有四個成員(首頁、公開分享連結、邀請預覽、公開筆記),往裡面加東西要格外
小心——那裡的每一支端點都是完全沒有身分的。也**絕不要**為了讓某支端點免登入
而去改 auth.get_current_user 加 anonymous 分支:整個 ALL_ROUTERS 的防線就靠
那支「沒有有效 session 一律 401」的鐵板,正確做法永遠是另開一個 public router。
"""
from . import (admin, ai, auth, dedup, demo, files, health, invite, links, notes,
               pages, plugins, publish, published, search, semantic, share, srs,
               taxonomy, templates, transfer, trash)

AUTH_ROUTER = auth.router
PUBLIC_ROUTERS = [pages.router, share.router, invite.router, published.router]
ALL_ROUTERS = [
    search.router,
    semantic.router,
    publish.router,
    srs.router,
    taxonomy.router,
    notes.router,
    links.router,
    dedup.router,
    health.router,
    trash.router,
    files.router,
    transfer.router,
    templates.router,
    plugins.router,
    ai.router,
    demo.router,
]
# 管理者專用:掛載時另套 Depends(get_current_admin)(見 app/__init__.py)。
ADMIN_ROUTERS = [admin.router]

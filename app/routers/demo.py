"""
範例資料的清除端點(置頂行的「點此刪除所有 demo 資料」)。

只有一支 DELETE /api/demo。獨立成一個檔案而不是併進 notes.py,是因為它要同時
動兩層真相:使用者的名詞(vault)與 users.json 的旗標——notes.py 不該認識
使用者登記簿。

⚠ 兩件不可放寬:

1. **只刪範例、不刪全部**。實作走 service.delete_demo_notes(),刪除範圍由
   demo/notes/ 的檔名決定。絕不可以圖方便改呼叫 delete_all_notes()——這顆按鈕
   出現在使用者已經開始建立自己的名詞之後。
2. **只作用在自己的個人庫**。範例只種在個人庫,團隊庫沒有也不該有;所以這裡收
   get_current_user 自己組 user_paths,不走 get_vault_* 那套(那套會依網址上的
   tid 換成團隊庫)。

刪 0 筆也回 200 並清掉旗標:使用者可能早就自己把範例刪光了,那時按下橫幅的
唯一意義就是把橫幅關掉。
"""
from fastapi import APIRouter, Depends

from ..auth import get_current_user
from ..paths import user_paths
from ..service import delete_demo_notes
from ..users import set_demo_seeded

router = APIRouter()


@router.delete("/api/demo")
def api_delete_demo(user: dict = Depends(get_current_user)):
    deleted = delete_demo_notes(user_paths(user["id"]))
    set_demo_seeded(user["id"], False)
    return {"deleted": deleted}

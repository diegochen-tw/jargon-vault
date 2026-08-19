"""
間隔重複複習(SRS)的**排程規則**:Leitner 盒。

README 承諾把行話「turning them into actual knowledge」,但存與查之間缺一整個
回想層。這個模組就是那一層的核心規則——刻意做得極小:一張間隔查表 + 一個
二元轉移函式,沒有 SM-2 的 ease factor、沒有 lapses、沒有浮點數調參。

為什麼是 Leitner 不是 SM-2:複習採「零負債抽卡」(打開就抽 DRAW_SIZE 張,不
顯示「今天到期 N 張」、不累積欠款),排程器的工作因此從「產生精確的到期佇列」
降格成「決定抽卡優先序」。既然不催,間隔差個一兩天不會改變今天抽到誰,
ease factor 的精度就是白付的複雜度——跟「不引入 numpy、不引入 ORM、
暴力比對就好」是同一套取捨。

本模組**只做算術**,不碰檔案/SQLite/HTTP。缺值退回 updated 的懶遷移規則在
storage.py(那是檔案格式的知識),抽卡的候選池查詢在 search.py:due_notes()。
"""
import time

# 各盒的間隔天數。box 是索引:答對就往後爬一格,答錯直接回 box 0。
INTERVALS = (1, 3, 7, 14, 30, 90)
MAX_BOX = len(INTERVALS) - 1

# 一輪抽幾張(預設值)。候選池取 size * POOL_FACTOR 筆再隨機打散,
# 讓「最該複習的一定在池子裡」與「連續兩天打開不會是一模一樣的卡序」並存。
#
# 使用者可在 設定 → 偏好設定 調整,由前端當查詢參數送上來(存在瀏覽器的
# localStorage,同介面語言/字級那一類的裝置偏好)。⚠ 這**不是**放寬零負債:
# 零負債的本質是「不催、不累積、不顯示欠款數字」,不是 20 這個數字本身——
# 一輪仍然固定張數、抽完就結束,只是那個張數由使用者決定。
# 上下限存在的理由:填 0 會讓每次打開都抽不到卡(看起來像壞掉),
# 填 9999 則會讓「一輪」失去意義並拖垮查詢。
DRAW_SIZE = 10
MIN_DRAW_SIZE = 5
MAX_DRAW_SIZE = 50
POOL_FACTOR = 3

DAY = 86400.0


def clamp_draw_size(n) -> int:
    """把使用者送上來的張數夾進合法範圍;0/None/壞值一律退回預設。

    夾範圍的邏輯只有這一份(router 與測試都用它),照 invites.py 的既有寫法。
    """
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    return max(MIN_DRAW_SIZE, min(n or DRAW_SIZE, MAX_DRAW_SIZE))


def effective_due(note: dict) -> float:
    """一筆名詞實際的到期時間:沒複習過就退回 updated。

    這條規則有兩個呼叫端,而且**不能只算一次**:storage 寫檔時要算,indexer 寫
    索引時也要算。因為寫入路徑上的 note dict 可能剛被 `old.update(updated=now)`
    換過(見 routers/notes.py 的 api_update),dict 裡的 srs_due 還是上一次讀檔
    留下的舊值——只在 storage 算的話,索引與「重新讀一次檔」會給出不同答案。
    """
    if note.get("srs_box") is None:
        return float(note.get("updated", 0))
    return float(note.get("srs_due") or note.get("updated", 0))


def next_state(box: int | None, remembered: bool, now: float | None = None) -> tuple[int, float]:
    """算出複習後的 (box, due)。

    box 傳 None 表示這筆從沒複習過(見 storage.py 的懶遷移),等同 box 0。
    答錯一律回 box 0 並在**明天**再問一次,不做「降一格」那種漸進退讓——
    行話裡想不起來就是想不起來,沒有半記得。
    """
    now = time.time() if now is None else now
    cur = 0 if box is None else max(0, min(MAX_BOX, int(box)))
    nxt = min(cur + 1, MAX_BOX) if remembered else 0
    return nxt, now + INTERVALS[nxt] * DAY

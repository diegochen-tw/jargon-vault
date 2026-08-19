"""向量儲存層:語意檢索的嵌入向量快取(<使用者目錄>/vectors.db)。

──────────────────────────────────────────────────────────────────────
它是第三種資料,不是前兩種
──────────────────────────────────────────────────────────────────────

本專案原本只有兩種資料:

  真相來源      .md / tags.json / templates.json …    不可重建
  可拋棄索引    index.db                              啟動時無條件全量重建

vectors.db 是第三種——**昂貴快取**:內容可以從 .md 完整重建,所以刪掉不會損失
任何資料;但重建的代價是每一筆名詞打一次嵌入模型,所以**絕不自動重建**。

這就是它為什麼不能併進 index.db:indexer.rebuild_index() 每次啟動都 DROP TABLE
再從 .md 重掃,而那個迴圈是同步阻塞 create_app() 的。向量放進去 = 每次重啟把
全站每個人的整個庫重新嵌入一遍。所以這裡的 DDL 一律 `IF NOT EXISTS`,**沒有
任何 DROP**,而且沒有任何啟動流程會呼叫它。

──────────────────────────────────────────────────────────────────────
三種必須整包作廢的情況
──────────────────────────────────────────────────────────────────────

向量只有在「同一個模型、同一個維度、同一種位元組序」之下才可以互相比較。
任何一項對不上都必須整包丟掉重建,因為混用的症狀是**靜默的爛排序**——不會有
例外、不會有錯誤訊息,只會表現成「語意搜尋結果莫名其妙」。這跟舊版搜尋
「SQL 撈 500 筆再篩」是同一類無聲錯誤,而那個教訓貴得很。

  model      換嵌入模型 → 兩批向量根本不在同一個空間
  dim        同名模型換版本也可能改維度
  byteorder  array("f").tobytes() 是原生位元組序,而本專案明確支援整包搬 data/

──────────────────────────────────────────────────────────────────────
為什麼是暴力比對,不是 sqlite-vec / numpy
──────────────────────────────────────────────────────────────────────

寫入時先做 L2 正規化,cosine 相似度就等於內積,一個 `sum(map(mul, …))` 解決。
個人量級的實測:225 筆 × 768 維約 20ms。誠實的天花板大約是 1 萬筆(~1 秒),
超過就該換 sqlite-vec 的 vec0 虛擬表或 numpy 的單次矩陣點積。

在那之前,不引入二進位擴充套件與 15MB 的相依,跟本專案「無 bundler、無
framework、無 ORM、無背景工作者」是同一套取捨。

本模組不碰 HTTP、不碰 .md 檔、不呼叫模型服務。
"""
import math
import sqlite3
import sys
from array import array
from operator import mul

from .paths import VaultPaths

# float32。用 "f" 而不是 "d":維度動輒 768~1024,存 double 等於檔案大一倍、
# 內積慢一倍,而嵌入模型本來就只輸出 float32 等級的精度。
_TYPECODE = "f"
_ITEMSIZE = array(_TYPECODE).itemsize

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS vecs(
    note_id TEXT PRIMARY KEY,
    hash    TEXT NOT NULL,
    vec     BLOB NOT NULL
);
"""


def db(paths: VaultPaths) -> sqlite3.Connection:
    """開連線並確保 schema 存在。

    壞檔自我修復照抄 indexer.rebuild_index():探測一下,DatabaseError 就砍掉重建。
    理由一模一樣——它是快取,而且一個人的壞檔不該變成別人的 500。
    """
    conn = sqlite3.connect(paths.vectors_path)
    try:
        conn.execute("SELECT 1").fetchone()
    except sqlite3.DatabaseError:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        paths.vectors_path.unlink(missing_ok=True)
        conn = sqlite3.connect(paths.vectors_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def reset(paths: VaultPaths) -> None:
    """整包丟掉。刪檔而不是 DELETE FROM——順便把 SQLite 佔的空間還給檔案系統。"""
    paths.vectors_path.unlink(missing_ok=True)


# ── meta ────────────────────────────────────────────────────────────

def meta(paths: VaultPaths) -> dict:
    conn = db(paths)
    try:
        return {r["k"]: r["v"] for r in conn.execute("SELECT k, v FROM meta")}
    finally:
        conn.close()


def set_meta(conn: sqlite3.Connection, model: str, dim: int) -> None:
    conn.executemany(
        "INSERT INTO meta(k, v) VALUES(?, ?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        [("model", model), ("dim", str(dim)), ("byteorder", sys.byteorder)],
    )


def is_compatible(paths: VaultPaths, model: str) -> bool:
    """現存的向量還能不能跟 `model` 新算出來的向量放在一起比。

    空的庫視為相容(還沒有任何向量,談不上混用)。維度不在這裡驗——第一次嵌入
    之前不知道新模型的維度,由 upsert 那側擋。
    """
    m = meta(paths)
    if not m:
        return True
    return m.get("model") == model and m.get("byteorder") == sys.byteorder


# ── 讀寫 ────────────────────────────────────────────────────────────

def fingerprints(paths: VaultPaths) -> dict[str, str]:
    """{note_id: hash},給 semantic.reindex() 差分用。"""
    conn = db(paths)
    try:
        return {r["note_id"]: r["hash"] for r in conn.execute("SELECT note_id, hash FROM vecs")}
    finally:
        conn.close()


def count(paths: VaultPaths) -> int:
    conn = db(paths)
    try:
        return conn.execute("SELECT COUNT(*) FROM vecs").fetchone()[0]
    finally:
        conn.close()


def normalize(vec) -> array:
    """L2 正規化成 float32。之後的 cosine 就只是一個內積。

    零向量(模型偶爾對空輸入會這樣回)原樣留著:除以 0 會炸,而全零跟任何東西
    的內積都是 0,自然排在最後,不需要特別處理。
    """
    a = array(_TYPECODE, (float(x) for x in vec))
    norm = math.sqrt(sum(x * x for x in a))
    if norm > 0:
        for i in range(len(a)):
            a[i] = a[i] / norm
    return a


def upsert(conn: sqlite3.Connection, note_id: str, hash_: str, vec) -> None:
    conn.execute(
        "INSERT INTO vecs(note_id, hash, vec) VALUES(?, ?, ?) "
        "ON CONFLICT(note_id) DO UPDATE SET hash=excluded.hash, vec=excluded.vec",
        (note_id, hash_, normalize(vec).tobytes()),
    )


def delete(conn: sqlite3.Connection, note_id: str) -> None:
    conn.execute("DELETE FROM vecs WHERE note_id=?", (note_id,))


# ── 查詢 ────────────────────────────────────────────────────────────

def knn(paths: VaultPaths, query_vec, allowed: set[str] | None = None,
        k: int = 50) -> list[tuple[str, float]]:
    """回傳最相近的 (note_id, 分數),分數由高到低。

    allowed 是**已經套完所有篩選條件**的完整 id 集合(來自 search.filtered_ids(),
    沒有 LIMIT)。先篩再比,而不是先比再篩——後者會讓「向量最近的前 K 筆剛好都
    被篩掉」變成空結果。allowed=None 表示沒有任何篩選。

    維度對不上的列直接跳過:那代表 meta 檢查沒攔住的殘留(例如使用者手動改過
    設定檔),寧可少幾筆也不要拿長度不同的向量硬算內積。
    """
    q = normalize(query_vec)
    dim = len(q)
    if not dim or (allowed is not None and not allowed):
        return []

    conn = db(paths)
    try:
        rows = conn.execute("SELECT note_id, vec FROM vecs").fetchall()
    finally:
        conn.close()

    want_bytes = dim * _ITEMSIZE
    scored: list[tuple[str, float]] = []
    for r in rows:
        nid, blob = r["note_id"], r["vec"]
        if allowed is not None and nid not in allowed:
            continue
        if len(blob) != want_bytes:
            continue
        # 兩邊都已正規化,內積 == cosine。map(mul, …) 走的是 C 層迴圈,
        # 比 Python 的 for/zip 快好幾倍,而且不需要任何相依。
        scored.append((nid, sum(map(mul, q, array(_TYPECODE, blob)))))

    # 分數相同時用 id 排,讓結果穩定可測
    scored.sort(key=lambda t: (-t[1], t[0]))
    return scored[:max(0, k)]

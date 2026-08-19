"""向量儲存層(app/vectors.py)。

重點不在「存得進去讀得出來」,而在**幾種會靜默給出爛結果的情況有沒有被擋掉**:
換模型、換維度、換位元組序。那些情況不會拋例外、不會有錯誤訊息,只會表現成
「語意搜尋結果莫名其妙」——跟舊版搜尋「SQL 撈 500 筆再篩」是同一類無聲錯誤。
"""
import math
import sys

from app import vectors


def _store(paths, rows: dict[str, list[float]], model: str = "m", dim: int | None = None):
    conn = vectors.db(paths)
    try:
        for nid, vec in rows.items():
            vectors.upsert(conn, nid, f"h-{nid}", vec)
        vectors.set_meta(conn, model, dim if dim is not None else len(next(iter(rows.values()))))
        conn.commit()
    finally:
        conn.close()


# ── 存取 ────────────────────────────────────────────────────────────

def test_round_trip_upsert_replaces_and_delete_removes(paths):
    _store(paths, {"a": [3.0, 4.0]})
    assert vectors.count(paths) == 1
    assert vectors.fingerprints(paths) == {"a": "h-a"}

    # 同 id 再存是取代不是累加
    _store(paths, {"a": [0.0, 1.0]})
    assert vectors.count(paths) == 1

    _store(paths, {"b": [1.0, 0.0]})
    conn = vectors.db(paths)
    try:
        vectors.delete(conn, "a")
        conn.commit()
    finally:
        conn.close()
    assert set(vectors.fingerprints(paths)) == {"b"}


def test_vectors_are_stored_normalized(paths):
    """存的時候先 L2 正規化,cosine 就退化成一個內積。
    全零向量不動它:除以 0 會炸,而全零跟任何東西的內積都是 0,自然排最後。"""
    a = vectors.normalize([3.0, 4.0])
    assert math.isclose(sum(x * x for x in a), 1.0, rel_tol=1e-6)
    assert math.isclose(a[0], 0.6, rel_tol=1e-6)
    assert list(vectors.normalize([0.0, 0.0])) == [0.0, 0.0]


# ── KNN ─────────────────────────────────────────────────────────────

def test_knn_ranks_by_cosine_similarity_and_respects_k(paths):
    _store(paths, {"same": [1.0, 0.0], "half": [1.0, 1.0], "orth": [0.0, 1.0]})
    hits = vectors.knn(paths, [1.0, 0.0], k=3)

    assert [nid for nid, _ in hits] == ["same", "half", "orth"]
    assert math.isclose(hits[0][1], 1.0, rel_tol=1e-5)
    assert math.isclose(hits[2][1], 0.0, abs_tol=1e-6)
    assert len(vectors.knn(paths, [1.0, 0.0], k=2)) == 2


def test_knn_only_looks_inside_the_allowed_set(paths):
    """allowed 是已經套完所有篩選的完整 id 集合。**先篩再比**——反過來的話,
    「向量最近的前 K 筆剛好都被篩掉」就會變成空結果。空集合就是空結果。"""
    _store(paths, {"a": [1.0, 0.0], "b": [0.9, 0.1], "c": [0.0, 1.0]})
    hits = vectors.knn(paths, [1.0, 0.0], allowed={"c"}, k=10)

    assert [nid for nid, _ in hits] == ["c"]
    assert vectors.knn(paths, [1.0, 0.0], allowed=set(), k=10) == []


def test_knn_skips_rows_whose_dimension_does_not_match(paths):
    """meta 檢查沒攔住的殘留(手改設定檔之類):寧可少幾筆,也不要拿長度不同的
    向量硬算內積。"""
    _store(paths, {"two": [1.0, 0.0]})
    conn = vectors.db(paths)
    try:
        vectors.upsert(conn, "three", "h", [1.0, 0.0, 0.0])
        conn.commit()
    finally:
        conn.close()

    assert [nid for nid, _ in vectors.knn(paths, [1.0, 0.0], k=10)] == ["two"]


def test_knn_empty_store_and_stable_ties(paths):
    assert vectors.knn(paths, [1.0, 0.0], k=10) == []

    _store(paths, {"b": [1.0, 0.0], "a": [1.0, 0.0]})
    assert [nid for nid, _ in vectors.knn(paths, [1.0, 0.0], k=10)] == ["a", "b"]


# ── 相容性:三種必須整包作廢的情況 ──────────────────────────────────

def test_changing_the_embedding_model_invalidates_everything(paths):
    """不同模型的向量根本不在同一個空間,混用只會安靜地給出爛排序。
    空庫則跟任何模型相容(沒有東西可作廢)。"""
    assert vectors.is_compatible(paths, "any-model") is True

    _store(paths, {"a": [1.0, 0.0]}, model="nomic")
    assert vectors.is_compatible(paths, "nomic") is True
    assert vectors.is_compatible(paths, "bge-m3") is False


def test_a_foreign_byteorder_invalidates_everything(paths):
    """array('f').tobytes() 是原生位元組序,而本專案明確支援整包搬 data/ 目錄。"""
    _store(paths, {"a": [1.0, 0.0]}, model="m")
    conn = vectors.db(paths)
    try:
        other = "big" if sys.byteorder == "little" else "little"
        conn.execute("UPDATE meta SET v=? WHERE k='byteorder'", (other,))
        conn.commit()
    finally:
        conn.close()

    assert vectors.is_compatible(paths, "m") is False


def test_meta_records_model_dim_and_byteorder(paths):
    _store(paths, {"a": [1.0, 0.0, 0.0]}, model="nomic")
    m = vectors.meta(paths)

    assert m["model"] == "nomic"
    assert m["dim"] == "3"
    assert m["byteorder"] == sys.byteorder


def test_reset_removes_the_file(paths):
    _store(paths, {"a": [1.0, 0.0]})
    assert paths.vectors_path.exists()

    vectors.reset(paths)
    assert not paths.vectors_path.exists()
    assert vectors.count(paths) == 0


# ── 壞檔 ────────────────────────────────────────────────────────────

def test_a_corrupt_file_is_deleted_and_rebuilt(paths):
    """向量庫是快取,壞了就砍掉重來——一個人的壞檔不該變成別人的 500。"""
    paths.vectors_path.write_bytes(b"this is not a sqlite database at all")

    assert vectors.count(paths) == 0        # 不拋例外
    _store(paths, {"a": [1.0, 0.0]})        # 而且之後照常能用
    assert vectors.count(paths) == 1


def test_the_schema_never_drops_existing_rows(paths):
    """⚠ 這支守的是本模組存在的理由。

    index.db 每次啟動 DROP TABLE 重建;向量庫如果照抄那個做法,每次重啟就是把
    全站每個人的整個庫重新嵌入一遍。所以這裡的 DDL 一律 IF NOT EXISTS。
    """
    _store(paths, {"a": [1.0, 0.0]})
    for _ in range(3):
        vectors.db(paths).close()

    assert vectors.count(paths) == 1

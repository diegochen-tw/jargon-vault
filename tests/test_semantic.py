"""語意檢索的複合層(app/semantic.py)。

嵌入模型用假的:給每個字串指定一個固定向量,這樣「哪一筆比較近」是測試自己
決定的,不依賴任何模型的實際行為,也不需要 Ollama 在跑。

這裡最重要的一支是 test_filters_are_never_bypassed_by_vector_similarity——
它守的是「篩選一律下推 SQL」那條鐵律在混合檢索底下仍然成立。
"""
import asyncio

import pytest

from app import llm, semantic, vectors
from app.indexer import rebuild_index
from app.service import persist_note

SETTINGS = {"enabled": True, "api_style": "ollama", "base_url": "http://x",
            "model": "m", "embed_model": "e"}


@pytest.fixture(autouse=True)
def _indexed(paths):
    # persist_note() 假設 notes/fts 資料表已存在(真實流程裡由啟動時或註冊時的
    # rebuild_index() 保證);單元測試繞過啟動流程,所以要自己補這一步。
    rebuild_index(paths)


def _note(paths, nid, name, description="", tags=None, fields=None, template="jargon-default"):
    persist_note(paths, {
        "id": nid, "name": name, "description": description, "template": template,
        "fields": fields or {}, "tags": tags or [], "attachments": [],
        "marked": False, "created": 1.0, "updated": 1.0, "history": [],
    })


def _fake_embed(monkeypatch, table: dict[str, list[float]], default=(0.0, 0.0, 1.0), calls=None):
    """把 llm.embed 換掉:表裡有的字串給指定向量,其餘給 default。"""
    async def fake(settings, texts):
        if calls is not None:
            calls.append(list(texts))
        out = []
        for t in texts:
            hit = next((v for k, v in table.items() if k in t), None)
            out.append(list(hit if hit is not None else default))
        return out
    monkeypatch.setattr(semantic.llm, "embed", fake)


def _run(coro):
    return asyncio.run(coro)


# ── 嵌入來源文字 ────────────────────────────────────────────────────

def test_embed_text_and_fingerprint():
    """嵌入文字涵蓋 name/tags/fields/description,超長截斷;fingerprint 只跟
    文字內容走。**刻意不查樣板**:查了的話,使用者在設定裡勾掉一個欄位就會讓
    全庫 hash 一起變、整個庫要重嵌一遍——而那個欄位的值其實還在名詞裡。"""
    text = semantic.embed_text({
        "name": "上料防呆", "tags": ["產線", "SFC"],
        "fields": {"alias": "poka-yoke"}, "description": "避免裝錯料",
    })
    assert "上料防呆" in text and "產線" in text
    assert "poka-yoke" in text and "避免裝錯料" in text

    # 樣板已不認得的欄位值仍在嵌入文字裡
    assert "還在的值" in semantic.embed_text({"name": "x", "fields": {"legacy_key": "還在的值"}})

    # 截斷
    long = semantic.embed_text({"name": "n", "description": "字" * 5000})
    assert len(long) == semantic.EMBED_TEXT_MAX

    # fingerprint:同文同 hash、異文異 hash
    a = semantic.embed_text({"name": "x", "description": "d"})
    b = semantic.embed_text({"name": "x", "description": "d"})
    c = semantic.embed_text({"name": "x", "description": "d2"})
    assert semantic.fingerprint(a) == semantic.fingerprint(b)
    assert semantic.fingerprint(a) != semantic.fingerprint(c)


# ── 建索引與差分 ────────────────────────────────────────────────────

def test_reindex_embeds_skips_unchanged_and_deletes_orphans(paths, monkeypatch):
    """首建全嵌;內容沒變的第二輪一筆都不重嵌(rename_tag / retarget_links /
    書籤標記那些「重新 upsert 但內容沒變」的路徑不該浪費嵌入呼叫);名詞刪了
    向量跟著清。"""
    _note(paths, "a", "甲")
    _note(paths, "b", "乙")
    calls: list = []
    _fake_embed(monkeypatch, {}, calls=calls)

    out = _run(semantic.reindex(paths, SETTINGS))
    assert out["embedded"] == 2 and out["remaining"] == 0
    assert vectors.count(paths) == 2

    calls.clear()
    out = _run(semantic.reindex(paths, SETTINGS))
    assert out["embedded"] == 0
    assert calls == []

    (paths.notes_dir / "b.md").unlink()
    out = _run(semantic.reindex(paths, SETTINGS))
    assert out["deleted"] == 1
    assert set(vectors.fingerprints(paths)) == {"a"}


def test_reindex_re_embeds_a_note_whose_tags_changed(paths, monkeypatch):
    """標籤在嵌入文字裡,所以改標籤**應該**重嵌——這是刻意的,不是浪費。"""
    _note(paths, "a", "甲", tags=["舊"])
    _fake_embed(monkeypatch, {})
    _run(semantic.reindex(paths, SETTINGS))

    _note(paths, "a", "甲", tags=["新"])
    assert _run(semantic.reindex(paths, SETTINGS))["embedded"] == 1


def test_reindex_honors_the_limit_and_splits_embed_batches(paths, monkeypatch):
    """limit 是給前端跑進度迴圈用的分批;一個請求內部又以 EMBED_BATCH 為單位
    分次打嵌入模型,不是把整批一次塞過去(單次呼叫太大會撐爆本機模型或
    反向代理逾時)。"""
    for i in range(5):
        _note(paths, f"n{i}", f"名詞{i}")
    calls: list = []
    _fake_embed(monkeypatch, {}, calls=calls)
    monkeypatch.setattr(semantic, "EMBED_BATCH", 2)

    first = _run(semantic.reindex(paths, SETTINGS, limit=2))
    assert first["embedded"] == 2 and first["remaining"] == 3

    last = _run(semantic.reindex(paths, SETTINGS))
    assert last["embedded"] == 3 and last["remaining"] == 0
    assert vectors.count(paths) == 5
    assert all(len(c) <= 2 for c in calls)


def test_time_budget_stops_between_chunks_but_always_does_one(paths, monkeypatch):
    """時間預算到了就提前收工(讓每個 HTTP 請求遠短於反向代理逾時),但至少
    做完一個 chunk——一個都不做的話 remaining 不減,前端迴圈會零進度空轉。

    預算設成 -1 讓「elapsed 超標」恆成立,不用注入假時鐘就能逼出
    「每個請求恰好一個 chunk」的行為;連跑三次驗證仍然收斂。
    """
    for i in range(3):
        _note(paths, f"n{i}", f"名詞{i}")
    _fake_embed(monkeypatch, {})
    monkeypatch.setattr(semantic, "EMBED_BATCH", 1)
    monkeypatch.setattr(semantic, "REINDEX_TIME_BUDGET", -1.0)

    first = _run(semantic.reindex(paths, SETTINGS))
    assert first["embedded"] == 1 and first["remaining"] == 2

    _run(semantic.reindex(paths, SETTINGS))
    last = _run(semantic.reindex(paths, SETTINGS))
    assert last["remaining"] == 0 and vectors.count(paths) == 3


def test_committed_chunks_survive_a_connection_failure_that_raises(paths, monkeypatch):
    """兩件事在同一個劇本裡:(1) 連線失敗(status=None,服務根本沒回應)不做
    逐筆隔離、原樣拋——斷線被逐筆重試會誤報成「每一筆名詞都壞掉」;
    (2) 每個 chunk 就 commit,已經算好的向量要留著,下次 reindex 從斷點續建。"""
    _note(paths, "a", "甲")
    _note(paths, "b", "乙")
    n_calls = 0

    async def fail_second(settings, texts):
        nonlocal n_calls
        n_calls += 1
        if n_calls >= 2:
            raise llm.LLMError("連線 AI 服務失敗")
        return [[0.0, 0.0, 1.0] for _ in texts]

    monkeypatch.setattr(semantic.llm, "embed", fail_second)
    monkeypatch.setattr(semantic, "EMBED_BATCH", 1)

    with pytest.raises(llm.LLMError):
        _run(semantic.reindex(paths, SETTINGS))

    assert vectors.count(paths) == 1


def test_a_poison_note_is_skipped_and_batch_failures_recover_via_singles(paths, monkeypatch):
    """服務有回應、報 500 → 逐筆隔離。先驗隔離重試本身有效(整批一起送會爆、
    逐筆送就過的批次層級限制要能全部建成,failed 是空的);再驗真正的毒藥名詞
    被記名跳過並由 failed 大聲報告,整條建索引不再永久卡在同一個地方。"""
    for i in range(3):
        _note(paths, f"n{i}", f"名詞{i}")

    async def batch_shy(settings, texts):
        if len(texts) > 1:
            raise llm.LLMError("連線 AI 服務失敗:500", status=500)
        return [[0.0, 0.0, 1.0]]

    monkeypatch.setattr(semantic.llm, "embed", batch_shy)
    out = _run(semantic.reindex(paths, SETTINGS))
    assert out["embedded"] == 3 and out["failed"] == [] and out["remaining"] == 0
    assert vectors.count(paths) == 3

    # 真毒藥:單獨送也爆 → 記名跳過、其餘照建
    _note(paths, "p", "毒藥")
    _note(paths, "q", "丙")

    async def poison(settings, texts):
        if any("毒藥" in t for t in texts):
            raise llm.LLMError("連線 AI 服務失敗:500;伺服器回應:boom", status=500)
        return [[0.0, 0.0, 1.0] for _ in texts]

    monkeypatch.setattr(semantic.llm, "embed", poison)
    out = _run(semantic.reindex(paths, SETTINGS))

    assert out["embedded"] == 1 and out["remaining"] == 0
    assert [f["id"] for f in out["failed"]] == ["p"]
    assert out["failed"][0]["name"] == "毒藥"
    assert "boom" in out["failed"][0]["error"]
    assert vectors.count(paths) == 4
    # 失敗的那筆仍是 stale:status() 誠實、下次重按 reindex 會再試
    assert semantic.status(paths, SETTINGS)["stale"] == 1


def test_reindex_raises_on_missing_model_or_empty_vector(paths, monkeypatch):
    """沒設嵌入模型是錯誤;空向量也要大聲失敗——以前只是安靜跳過,該筆永遠
    stale、remaining 永遠不減,前端的進度迴圈會無限狂打後端且不報錯。"""
    with pytest.raises(llm.LLMError):
        _run(semantic.reindex(paths, {**SETTINGS, "embed_model": ""}))

    _note(paths, "a", "甲")
    _fake_embed(monkeypatch, {}, default=())
    with pytest.raises(llm.LLMError):
        _run(semantic.reindex(paths, SETTINGS))

    assert vectors.count(paths) == 0
    assert semantic.status(paths, SETTINGS)["stale"] == 1


def test_changing_the_embedding_model_wipes_and_rebuilds(paths, monkeypatch):
    _note(paths, "a", "甲")
    _fake_embed(monkeypatch, {})
    _run(semantic.reindex(paths, SETTINGS))
    assert vectors.meta(paths)["model"] == "e"

    out = _run(semantic.reindex(paths, {**SETTINGS, "embed_model": "e2"}))

    assert out["embedded"] == 1          # 全部重算,不是「沒有變所以跳過」
    assert vectors.meta(paths)["model"] == "e2"


def test_trashed_notes_are_not_indexed(paths, monkeypatch):
    """回收桶在 notes_dir 之外,所以 glob 掃不到——這裡把那個前提釘住。"""
    from app.service import delete_note
    _note(paths, "a", "甲")
    _note(paths, "b", "乙")
    delete_note(paths, "b")
    _fake_embed(monkeypatch, {})

    _run(semantic.reindex(paths, SETTINGS))
    assert set(vectors.fingerprints(paths)) == {"a"}


# ── 狀態 ────────────────────────────────────────────────────────────

def test_status_reports_lag_and_model_mismatch(paths, monkeypatch):
    """status 誠實報「落後多少」;換模型後現存向量全部不可比,老實回報 0,
    不要讓人以為還有索引可用。"""
    _note(paths, "a", "甲")
    _fake_embed(monkeypatch, {})
    _run(semantic.reindex(paths, SETTINGS))
    _note(paths, "b", "乙")

    s = semantic.status(paths, SETTINGS)
    assert s["total"] == 2 and s["indexed"] == 1 and s["stale"] == 1

    s = semantic.status(paths, {**SETTINGS, "embed_model": "other"})
    assert s["model_mismatch"] is True
    assert s["indexed"] == 0 and s["stale"] == 2


def test_quick_status_counts_without_parsing_any_file(paths, monkeypatch):
    """⚠ 查詢路徑用的是這支,不是 status()。

    status() 要把每一筆 .md 讀進來算 hash;掛在搜尋端點上等於每查一次就全庫掃一遍,
    而查詢是跟著搜尋框的 debounce 一直發生的。這裡直接把 read_note_file 換成地雷,
    確保 quick_status 一個檔案都沒讀。
    """
    _note(paths, "a", "甲")
    _note(paths, "b", "乙")
    _fake_embed(monkeypatch, {})
    _run(semantic.reindex(paths, SETTINGS, limit=1))

    def boom(*a, **kw):
        raise AssertionError("quick_status 不該讀取任何 .md")
    monkeypatch.setattr(semantic, "read_note_file", boom)

    assert semantic.quick_status(paths) == (1, 1)


# ── 混合檢索 ────────────────────────────────────────────────────────

def test_semantic_search_finds_a_note_that_shares_no_characters(paths, monkeypatch):
    """整個功能的存在理由:字面上完全沒有交集,但語意上是同一件事。"""
    _note(paths, "a", "Poka-Yoke 治具", description="裝錯料就插不進去的機構")
    _note(paths, "b", "出貨排程", description="每日排定出貨順序")
    _fake_embed(monkeypatch, {
        "Poka-Yoke": [1.0, 0.0, 0.0],
        "出貨排程": [0.0, 1.0, 0.0],
        "上料防呆": [1.0, 0.0, 0.0],      # 查詢字串
    })
    _run(semantic.reindex(paths, SETTINGS))

    rows = _run(semantic.search_hybrid(paths, SETTINGS, "上料防呆", tags=[]))

    assert rows and rows[0]["id"] == "a"


def test_filters_are_never_bypassed_by_vector_similarity(paths, monkeypatch):
    """⚠ 本檔最重要的一支。

    「篩選一律下推 SQL」那條鐵律在混合檢索底下仍然成立:向量臂只在
    search.filtered_ids() 算出來的**完整** id 集合內比對,所以再怎麼相近的名詞
    只要被標籤篩掉就絕不會出現。反過來寫(先比再篩)的話,這裡會回傳 a。
    """
    _note(paths, "a", "最相近的", tags=["別的"])
    _note(paths, "b", "沒那麼近的", tags=["要的"])
    _fake_embed(monkeypatch, {
        "最相近的": [1.0, 0.0, 0.0],
        "沒那麼近的": [0.3, 0.9, 0.0],
        "查詢": [1.0, 0.0, 0.0],
    })
    _run(semantic.reindex(paths, SETTINGS))

    rows = _run(semantic.search_hybrid(paths, SETTINGS, "查詢", tags=["要的"]))

    assert [r["id"] for r in rows] == ["b"]


def test_hybrid_fuses_both_arms(paths, monkeypatch):
    """RRF 融合的三件事:兩臂都命中的排最前;字面上打中的不因向量不近而掉出
    結果(混合的另一半);空查詢回空。外加 _rrf 本身的兩個小性質。"""
    _note(paths, "both", "防呆治具")
    _note(paths, "lex", "防呆說明")
    _note(paths, "vec", "Poka-Yoke")
    _fake_embed(monkeypatch, {
        "防呆治具": [1.0, 0.0, 0.0],
        "Poka-Yoke": [1.0, 0.0, 0.0],
        "防呆說明": [0.0, 0.0, 1.0],
        "防呆": [1.0, 0.0, 0.0],
    })
    _run(semantic.reindex(paths, SETTINGS))

    rows = _run(semantic.search_hybrid(paths, SETTINGS, "防呆", tags=[]))
    assert rows[0]["id"] == "both"
    assert "lex" in [r["id"] for r in rows]     # 關鍵字臂的命中保得住

    assert _run(semantic.search_hybrid(paths, SETTINGS, "   ", tags=[])) == []

    # _rrf:兩臂都出現的優先;同分時排序穩定
    assert semantic._rrf(["x", "a"], ["y", "a"])[0] == "a"
    assert semantic._rrf(["b"], ["a"]) == ["a", "b"]

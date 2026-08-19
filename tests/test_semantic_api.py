"""語意檢索走 HTTP 的部分(app/routers/semantic.py)。

證明各層真的接起來,外加兩件只有在 router 層才驗得到的事:使用者之間的向量
是隔離的,以及「索引還沒建好」要說得出口而不是回一個空列表。細節分支
(差分、毒藥隔離、RRF……)留在 tests/test_semantic.py 的複合層測試。
"""
from app import semantic
from app.ai_settings import save_ai_settings


def _enable(paths, embed_model="e"):
    save_ai_settings({"enabled": True, "api_style": "ollama",
                      "base_url": "http://x", "api_key": "",
                      "model": "m", "embed_model": embed_model})


def _fake_embed(monkeypatch, table: dict[str, list[float]], default=(0.0, 0.0, 1.0)):
    async def fake(settings, texts):
        return [list(next((v for k, v in table.items() if k in t), default)) for t in texts]
    monkeypatch.setattr(semantic.llm, "embed", fake)


def _make(u, nid, name, tags=None):
    r = u["client"].post("/api/notes", json={
        "id": nid, "name": name, "description": "", "template": "jargon-default",
        "fields": {}, "tags": tags or [], "attachments": [],
    })
    assert r.status_code == 200, r.text


# ── 狀態與建索引 ────────────────────────────────────────────────────

def test_status_and_reindex_round_trip(register_user, monkeypatch):
    u = register_user()
    _enable(u["paths"])
    _make(u, "a", "甲")
    _fake_embed(monkeypatch, {})

    s = u["client"].get("/api/semantic/status").json()
    assert s["total"] == 1 and s["indexed"] == 0 and s["stale"] == 1

    out = u["client"].post("/api/semantic/reindex", json={"limit": 0}).json()
    assert out["embedded"] == 1 and out["remaining"] == 0

    s = u["client"].get("/api/semantic/status").json()
    assert s["indexed"] == 1 and s["stale"] == 0


def test_reindex_error_codes(register_user):
    """AI 未啟用 → 400;啟用但沒設嵌入模型 → 502。"""
    u = register_user()
    save_ai_settings({"enabled": False, "embed_model": "e"})
    assert u["client"].post("/api/semantic/reindex", json={}).status_code == 400

    _enable(u["paths"], embed_model="")
    assert u["client"].post("/api/semantic/reindex", json={}).status_code == 502


def test_clearing_the_index_removes_the_file(register_user, monkeypatch):
    u = register_user()
    _enable(u["paths"])
    _make(u, "a", "甲")
    _fake_embed(monkeypatch, {})
    u["client"].post("/api/semantic/reindex", json={})
    assert u["paths"].vectors_path.exists()

    u["client"].delete("/api/semantic/index")

    assert not u["paths"].vectors_path.exists()
    assert u["client"].get("/api/semantic/status").json()["indexed"] == 0


# ── 搜尋 ────────────────────────────────────────────────────────────

def test_search_says_it_needs_an_index_instead_of_returning_nothing(register_user):
    """空列表會讓人以為庫裡沒東西,而真正的原因是還沒建索引——
    沒建過索引與沒設嵌入模型兩種情況都要說得出口。"""
    u = register_user()
    _enable(u["paths"])
    _make(u, "a", "甲")

    body = u["client"].get("/api/semantic/search?q=甲").json()
    assert body["results"] == [] and body["needs_index"] is True

    _enable(u["paths"], embed_model="")
    assert u["client"].get("/api/semantic/search?q=x").json()["needs_index"] is True


def test_search_returns_the_closest_and_reports_lag_without_pagination(register_user, monkeypatch):
    """回傳語意最相近的排序;存檔不碰嵌入所以索引一定會落後,落後多少要報
    (unindexed);RRF 名次在 Python 端算,跨兩臂的全域 OFFSET 無法下推,
    所以 has_more 恆為 false;空查詢回空。"""
    u = register_user()
    _enable(u["paths"])
    _make(u, "a", "Poka-Yoke 治具")
    _make(u, "b", "出貨排程")
    _fake_embed(monkeypatch, {"Poka-Yoke": [1.0, 0.0, 0.0], "出貨排程": [0.0, 1.0, 0.0],
                              "上料防呆": [1.0, 0.0, 0.0]})
    u["client"].post("/api/semantic/reindex", json={})

    body = u["client"].get("/api/semantic/search?q=上料防呆").json()
    assert [r["id"] for r in body["results"]] == ["a", "b"]
    assert body["needs_index"] is False
    assert body["has_more"] is False

    _make(u, "c", "丙")          # 新增後沒有重建索引
    assert u["client"].get("/api/semantic/search?q=上料防呆").json()["unindexed"] == 1

    assert u["client"].get("/api/semantic/search?q=").json()["results"] == []


def test_search_applies_the_group_filter(register_user, monkeypatch):
    """群組在 router 層展開成標籤集合;不存在的群組就是空結果。"""
    u = register_user()
    _enable(u["paths"])
    _make(u, "a", "甲", tags=["生產"])
    _make(u, "b", "乙", tags=["行政"])
    u["client"].put("/api/tag-groups", json={"group": "廠務", "tags": ["生產"]})
    _fake_embed(monkeypatch, {})
    u["client"].post("/api/semantic/reindex", json={})

    body = u["client"].get("/api/semantic/search?q=甲&group=廠務").json()
    assert [r["id"] for r in body["results"]] == ["a"]

    body = u["client"].get("/api/semantic/search?q=甲&group=不存在的群組").json()
    assert body["results"] == []


# ── 隔離與驗證 ──────────────────────────────────────────────────────

def test_vectors_are_isolated_between_users(register_user, monkeypatch):
    a, b = register_user(), register_user()
    _enable(a["paths"])
    _enable(b["paths"])
    _make(a, "secret", "甲的機密名詞")
    _fake_embed(monkeypatch, {})
    a["client"].post("/api/semantic/reindex", json={})

    assert a["paths"].vectors_path.exists()
    assert not b["paths"].vectors_path.exists()
    assert b["client"].get("/api/semantic/search?q=甲的機密名詞").json()["results"] == []


def test_every_semantic_endpoint_requires_a_session(client):
    assert client.get("/api/semantic/status").status_code == 401
    assert client.post("/api/semantic/reindex", json={}).status_code == 401
    assert client.delete("/api/semantic/index").status_code == 401
    assert client.get("/api/semantic/search?q=x").status_code == 401


def test_plain_search_is_completely_unaffected(register_user, monkeypatch):
    """語意檢索是另一支端點,/api/search 的行為一個字都不該變。"""
    u = register_user()
    _enable(u["paths"])
    _make(u, "a", "甲")

    body = u["client"].get("/api/search?q=甲").json()
    assert [r["id"] for r in body["results"]] == ["a"]
    assert "needs_index" not in body

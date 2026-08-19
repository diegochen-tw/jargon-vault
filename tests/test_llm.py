"""模型服務連線層(app/llm.py)的 wire-format 契約。

這裡攔在 httpx 那一層,看**真正送出去的 request body** 長什麼樣。兩種 api_style
各驗一遍——同一份上層程式碼要能在 Ollama 原生與 OpenAI 相容兩邊都講對話,而
講錯的症狀多半不是報錯,是安靜地給出爛結果。
"""
import asyncio

import pytest

from app import llm

OLLAMA = {"api_style": "ollama", "base_url": "http://x", "model": "m", "embed_model": "e"}
OPENAI = {"api_style": "openai", "base_url": "http://x/v1", "model": "m", "embed_model": "e"}


def _fake_http(monkeypatch, reply: dict) -> dict:
    """把 httpx.AsyncClient 換掉,回傳一個會被填上 url/body/headers 的 dict。"""
    sent: dict = {}

    class _Resp:
        def raise_for_status(self):
            pass

        @staticmethod
        def json():
            return reply

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            sent.update(url=url, body=json, headers=headers or {})
            return _Resp()

        async def get(self, url, headers=None):
            sent.update(url=url, body=None, headers=headers or {})
            return _Resp()

    monkeypatch.setattr(llm.httpx, "AsyncClient", _Client)
    return sent


# ── chat ────────────────────────────────────────────────────────────

def test_ollama_chat_disables_model_thinking(monkeypatch):
    """思考型模型(如 gemma4)吐在 message.thinking 的推理這裡完全用不到,卻要逐
    token 生成——實測佔掉四分之三的時間(20.8s → 5.0s)。送 think=False 關掉它。
    順帶驗 messages 的順序:system 先、user 後。"""
    sent = _fake_http(monkeypatch, {"message": {"content": "hi"}})
    out = asyncio.run(llm.chat(OLLAMA, "SYS", "USR"))

    assert sent["url"] == "http://x/api/chat"
    assert sent["body"]["think"] is False
    assert sent["body"]["stream"] is False
    assert sent["body"]["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]
    assert out == "hi"


def test_openai_chat_uses_chat_completions_and_omits_think(monkeypatch):
    """think 是 Ollama 專屬參數,送給 OpenAI 相容端點可能直接 400。
    另外 /v1 **絕不自動補**——vLLM 與部分 gateway 掛在根路徑,猜錯只會換來
    更難懂的 404。"""
    sent = _fake_http(monkeypatch, {"choices": [{"message": {"content": "hi"}}]})
    out = asyncio.run(llm.chat(OPENAI, "sys", "usr"))

    assert sent["url"] == "http://x/v1/chat/completions"
    assert "think" not in sent["body"]
    assert sent["body"]["stream"] is False
    assert out == "hi"

    # base_url 沒帶 /v1 就不補
    sent = _fake_http(monkeypatch, {"choices": [{"message": {"content": "x"}}]})
    asyncio.run(llm.chat({**OPENAI, "base_url": "http://x"}, "s", "u"))
    assert sent["url"] == "http://x/chat/completions"


@pytest.mark.parametrize("settings", [OLLAMA, OPENAI])
def test_chat_never_constrains_json_decoding(monkeypatch, settings):
    """⚠ 這一條是整個檔案裡最重要的斷言。

    Ollama 的 format="json" 與 OpenAI 相容側的 response_format={"type":"json_object"}
    都是 grammar 約束解碼,會在逐位元組取樣時切斷多位元組 UTF-8——中文變成
    lone surrogate 亂碼。JSON 一律靠 prompt 要求 + _extract_json 自行擷取。
    """
    reply = ({"message": {"content": "{}"}} if settings is OLLAMA
             else {"choices": [{"message": {"content": "{}"}}]})
    sent = _fake_http(monkeypatch, reply)
    asyncio.run(llm.chat(settings, "sys", "usr"))

    assert "format" not in sent["body"]
    assert "response_format" not in sent["body"]


# ── 金鑰 ────────────────────────────────────────────────────────────

def test_api_key_is_only_sent_when_set(monkeypatch):
    """本機服務通常不需要金鑰,有些實作收到空的 Bearer 反而會 401。"""
    sent = _fake_http(monkeypatch, {"message": {"content": "x"}})
    asyncio.run(llm.chat(OLLAMA, "s", "u"))
    assert "Authorization" not in sent["headers"]

    sent = _fake_http(monkeypatch, {"choices": [{"message": {"content": "x"}}]})
    asyncio.run(llm.chat({**OPENAI, "api_key": "sk-abc"}, "s", "u"))
    assert sent["headers"]["Authorization"] == "Bearer sk-abc"


# ── embeddings ──────────────────────────────────────────────────────

def test_embed_returns_vectors_in_input_order(monkeypatch):
    """⚠ OpenAI 規格不保證 data 的順序。不依 index 排回來的話,向量會配到錯的
    名詞上——而且不會有任何錯誤,只會表現成「語意搜尋結果莫名其妙」。"""
    sent = _fake_http(monkeypatch, {"embeddings": [[1.0, 2.0], [3.0, 4.0]]})
    out = asyncio.run(llm.embed(OLLAMA, ["a", "b"]))
    assert sent["url"] == "http://x/api/embed"
    assert sent["body"] == {"model": "e", "input": ["a", "b"]}
    assert out == [[1.0, 2.0], [3.0, 4.0]]

    # OpenAI 側要依 data[].index 重排
    _fake_http(monkeypatch, {"data": [
        {"index": 1, "embedding": [3.0, 4.0]},
        {"index": 0, "embedding": [1.0, 2.0]},
    ]})
    out = asyncio.run(llm.embed(OPENAI, ["a", "b"]))
    assert out == [[1.0, 2.0], [3.0, 4.0]]


@pytest.mark.parametrize("settings,reply", [
    (OLLAMA, {"embeddings": [[1.0]]}),
    (OPENAI, {"data": [{"index": 0, "embedding": [1.0]}]}),
])
def test_embed_rejects_a_short_reply(monkeypatch, settings, reply):
    """筆數對不上就是配錯,寧可整批失敗也不要靜默存進錯的向量。"""
    _fake_http(monkeypatch, reply)
    with pytest.raises(llm.LLMError):
        asyncio.run(llm.embed(settings, ["a", "b"]))


def test_embed_edge_cases(monkeypatch):
    """沒設嵌入模型是錯誤(不是靜默的空結果);空輸入根本不發請求。"""
    sent = _fake_http(monkeypatch, {})
    with pytest.raises(llm.LLMError):
        asyncio.run(llm.embed({**OLLAMA, "embed_model": ""}, ["a"]))

    sent = _fake_http(monkeypatch, {})
    assert asyncio.run(llm.embed(OLLAMA, [])) == []
    assert sent == {}


def test_http_error_includes_the_service_response_body(monkeypatch):
    """服務端 4xx/5xx 的 body 才是真正的原因(如 Ollama 的
    {"error":"input is too large to process"});httpx 的例外字串只有狀態行,
    把 body 丟掉的話,使用者與 log 只看得到「500」三個字。
    同時驗 LLMError.status:有 HTTP 狀態 = 服務有回應,semantic.reindex 的
    毒藥隔離靠這個欄位跟「根本沒連上」(status=None)區分。"""
    class _Resp:
        status_code = 500
        text = '{"error":"input is too large to process"}'

        def raise_for_status(self):
            raise llm.httpx.HTTPStatusError("Server error '500'",
                                            request=None, response=self)

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            return _Resp()

    monkeypatch.setattr(llm.httpx, "AsyncClient", _Client)

    with pytest.raises(llm.LLMError) as ei:
        asyncio.run(llm.embed(OLLAMA, ["x"]))

    assert "input is too large" in str(ei.value)
    assert ei.value.status == 500


def test_connection_error_has_no_status(monkeypatch):
    """連線失敗(服務根本沒回應)的 LLMError 必須維持 status=None——
    毒藥隔離看到 None 會立刻停,不然斷線會被誤報成「每一筆名詞都壞掉」。"""
    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            raise llm.httpx.ConnectError("All connection attempts failed")

    monkeypatch.setattr(llm.httpx, "AsyncClient", _Client)

    with pytest.raises(llm.LLMError) as ei:
        asyncio.run(llm.embed(OLLAMA, ["x"]))

    assert ei.value.status is None


# ── 模型清單 / 預熱 ─────────────────────────────────────────────────

def test_list_models_on_both_styles_and_with_an_unsaved_base_url(monkeypatch):
    """設定 UI 的「列出模型」兼任「測試連線」,要能在存檔前先試新位址。"""
    sent = _fake_http(monkeypatch, {"models": [{"name": "b"}, {"name": "a"}]})
    assert asyncio.run(llm.list_models(OLLAMA)) == ["a", "b"]
    assert sent["url"] == "http://x/api/tags"

    sent = _fake_http(monkeypatch, {"data": [{"id": "b"}, {"id": "a"}]})
    assert asyncio.run(llm.list_models(OPENAI)) == ["a", "b"]
    assert sent["url"] == "http://x/v1/models"

    sent = _fake_http(monkeypatch, {"models": []})
    asyncio.run(llm.list_models(OLLAMA, "http://other:1234/"))
    assert sent["url"] == "http://other:1234/api/tags"


def test_warmup_uses_preload_on_ollama_and_a_one_token_chat_on_openai(monkeypatch):
    sent = _fake_http(monkeypatch, {})
    assert asyncio.run(llm.warmup(OLLAMA)) is True
    assert sent["url"] == "http://x/api/generate"
    assert sent["body"] == {"model": "m", "keep_alive": "5m"}

    sent = _fake_http(monkeypatch, {})
    assert asyncio.run(llm.warmup(OPENAI)) is True
    assert sent["url"] == "http://x/v1/chat/completions"
    assert sent["body"]["max_tokens"] == 1


def test_warmup_never_raises():
    """預熱只是最佳化,絕不能擋住開啟編輯器。"""
    assert asyncio.run(llm.warmup({**OLLAMA, "base_url": "http://127.0.0.1:1"})) is False
    assert asyncio.run(llm.warmup({**OLLAMA, "model": ""})) is False


# ── 位址處理 ────────────────────────────────────────────────────────

def test_base_url_handling(monkeypatch):
    """結尾斜線剝掉;缺位址是錯誤;api_style 被手改壞掉時退回 ollama 而不是炸掉。"""
    sent = _fake_http(monkeypatch, {"message": {"content": "x"}})
    asyncio.run(llm.chat({**OLLAMA, "base_url": "http://x/"}, "s", "u"))
    assert sent["url"] == "http://x/api/chat"

    with pytest.raises(llm.LLMError):
        asyncio.run(llm.chat({**OLLAMA, "base_url": ""}, "s", "u"))

    sent = _fake_http(monkeypatch, {"message": {"content": "x"}})
    asyncio.run(llm.chat({**OLLAMA, "api_style": "nonsense"}, "s", "u"))
    assert sent["url"] == "http://x/api/chat"

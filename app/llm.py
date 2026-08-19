"""模型服務連線層:全 app/ 唯一對 LLM 服務講 HTTP 的地方。

支援兩種 wire format,由站台設定的 `api_style` 選擇(site_settings.json 的 ai 區塊,
全站唯一一組、只有管理者改得動——見 app/ai_settings.py):

  "ollama"(預設)  Ollama 原生 API。/api/chat、/api/embed、/api/tags、/api/generate
  "openai"          OpenAI 相容 API。/chat/completions、/embeddings、/models
                    ——LM Studio、llama.cpp(llama-server)、vLLM、Ollama 自己的
                    /v1 相容層都吃這一組,所以「本機模型」不再只能是 Ollama。

隱私承諾不受影響:位址一樣是使用者自己填的本機/區網 URL,本模組不預設任何
雲端端點,api_key 也只在使用者自己填了才送。

──────────────────────────────────────────────────────────────────────
四件不能弄錯的事
──────────────────────────────────────────────────────────────────────

1. **`think: False` 只有 ollama 分支送。**
   所有端點都只讀回覆的 content,思考型模型(gemma4、deepseek-r1…)另外吐在
   message.thinking 的推理會被整段丟掉,但它照樣要逐 token 生成。實測
   gemma4:12b 生一筆名詞要 ~2000 字思考換 ~600 字 JSON,關掉後 20.8s → 5.0s
   且輸出品質沒變差。非思考型模型會忽略這個參數,不需要先問 /api/show。
   OpenAI 相容規格沒有等價參數,選 openai style 就是自願放棄這個加速——
   設定 UI 的提示文字要講明,不要讓人以為只是換個網址。

2. **兩個分支都絕不送 `format` / `response_format`。**
   Ollama 的 format="json" 在逐位元組取樣時會切斷多位元組 UTF-8,中文變成
   lone surrogate 亂碼(Ollama 已知問題);OpenAI 相容側的
   response_format={"type":"json_object"} 在 llama.cpp / LM Studio 是同一套
   grammar 約束解碼,**同一顆雷**。JSON 一律靠 prompt 要求輸出 +
   routers/ai.py:_extract_json() 自行擷取。tests/test_llm.py 有回歸測試守著。

3. **OpenAI 的 embeddings 回應必須依 data[].index 重排再取 embedding。**
   規格不保證回傳順序。不排的話向量會對錯名詞,而且**不會有任何錯誤訊息**,
   只會表現成「語意搜尋結果莫名其妙」。

4. **api_key 只在非空時才加 Authorization。**
   本機服務通常不需要金鑰,有些實作收到空的 Bearer 反而會 401。

本模組不 import fastapi(它在 routers 底下一層),失敗一律拋 LLMError,
由 router 決定要轉成 502 還是安靜忽略。
"""
import httpx

# 逾時分開設:列模型只是探測(要快點失敗好讓 UI 有反應),生成則可能真的很久。
TIMEOUT_MODELS = 10
TIMEOUT_WARMUP = 120
TIMEOUT_CHAT = 180
TIMEOUT_EMBED = 120

DEFAULT_STYLE = "ollama"


class LLMError(RuntimeError):
    """連線失敗或回覆格式不符。router 層負責轉成 HTTP 狀態碼。

    `status`:服務端回的 HTTP 狀態碼;**None = 根本沒連上**(連線失敗/逾時)。
    這個區分是 semantic.reindex 毒藥隔離的依據——服務有回應但對特定內容報錯
    (有 status)可以逐筆跳過,服務不通(無 status)就該立刻停,不然斷線會被
    誤報成「每一筆名詞都壞掉」。
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _style(settings: dict) -> str:
    return (settings.get("api_style") or DEFAULT_STYLE).strip() or DEFAULT_STYLE


def _base(settings: dict, override: str = "") -> str:
    """組 base url。結尾斜線一律剝掉——底下所有路徑都是用 f-string 直接接的。

    openai style 的使用者通常會填到 `/v1` 為止(LM Studio 預設
    http://localhost:1234/v1)。刻意**不**自動補 /v1:vLLM 與部分 gateway 掛在
    根路徑,猜錯只會換來一個更難懂的 404。設定 UI 的 placeholder 講清楚即可。
    """
    url = (override or "").strip().rstrip("/") or (settings.get("base_url") or "").strip().rstrip("/")
    if not url:
        raise LLMError("尚未設定 AI 服務位址")
    return url


def _headers(settings: dict) -> dict:
    key = (settings.get("api_key") or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _model(settings: dict) -> str:
    m = (settings.get("model") or "").strip()
    if not m:
        raise LLMError("尚未設定生成模型")
    return m


def _body_snippet(e: httpx.HTTPStatusError) -> str:
    """服務端錯誤回應的內文摘要。

    Ollama/LM Studio 的 4xx/5xx body 就是真正的原因(例如
    {"error":"input is too large to process"});httpx 的例外字串只有狀態行,
    丟掉內文的話,使用者與 log 都只看得到「500」三個字,查不到為什麼。
    """
    try:
        text = (e.response.text or "").strip()
    except Exception:
        text = ""
    return f";伺服器回應:{text[:200]}" if text else ""


async def _post(url: str, payload: dict, *, headers: dict, timeout: float) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise LLMError(f"連線 AI 服務失敗:{e}{_body_snippet(e)}",
                       status=e.response.status_code)
    except httpx.HTTPError as e:
        raise LLMError(f"連線 AI 服務失敗:{e}")
    return r.json()


async def _get(url: str, *, headers: dict, timeout: float) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise LLMError(f"連線 AI 服務失敗:{e}{_body_snippet(e)}",
                       status=e.response.status_code)
    except httpx.HTTPError as e:
        raise LLMError(f"連線 AI 服務失敗:{e}")
    return r.json()


# ── chat ────────────────────────────────────────────────────────────

async def chat(settings: dict, system_prompt: str, user_content: str) -> str:
    """送一輪 chat,回**原始的 content 字串**(不解析 JSON,那是上層的事)。"""
    base, model = _base(settings), _model(settings)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    if _style(settings) == "openai":
        data = await _post(
            f"{base}/chat/completions",
            # 沒有 response_format:見檔頭第 2 點
            {"model": model, "messages": messages, "stream": False},
            headers=_headers(settings), timeout=TIMEOUT_CHAT,
        )
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("AI 服務沒有回傳任何內容")
        return ((choices[0].get("message") or {}).get("content")) or ""

    data = await _post(
        f"{base}/api/chat",
        # 沒有 format:見檔頭第 2 點;think:False:見第 1 點
        {"model": model, "messages": messages, "stream": False, "think": False},
        headers=_headers(settings), timeout=TIMEOUT_CHAT,
    )
    return (data.get("message") or {}).get("content", "")


# ── embeddings ──────────────────────────────────────────────────────

async def embed(settings: dict, texts: list[str]) -> list[list[float]]:
    """把多段文字一次嵌入,回傳與輸入**同順序**的向量清單。

    同順序是呼叫端的硬前提(app/semantic.py 靠位置把向量配回 note id),
    所以 openai 分支一定要依 data[].index 重排——見檔頭第 3 點。
    """
    if not texts:
        return []
    base = _base(settings)
    model = (settings.get("embed_model") or "").strip()
    if not model:
        raise LLMError("尚未設定嵌入模型(設定 → AI 連線 → 嵌入模型)")

    if _style(settings) == "openai":
        data = await _post(
            f"{base}/embeddings", {"model": model, "input": texts},
            headers=_headers(settings), timeout=TIMEOUT_EMBED,
        )
        rows = data.get("data") or []
        if len(rows) != len(texts):
            # status=200:服務有回應、只是行為異常(如安靜丟掉某筆輸入)——
            # 這一類 semantic.reindex 可以逐筆隔離,跟「根本沒連上」不同
            raise LLMError(f"嵌入模型回傳 {len(rows)} 筆向量,與送出的 {len(texts)} 筆不符",
                           status=200)
        # ⚠ 規格不保證順序,一定要依 index 排回來
        rows = sorted(rows, key=lambda d: d.get("index", 0))
        return [list(d.get("embedding") or []) for d in rows]

    data = await _post(
        f"{base}/api/embed", {"model": model, "input": texts},
        headers=_headers(settings), timeout=TIMEOUT_EMBED,
    )
    vecs = data.get("embeddings") or []
    if len(vecs) != len(texts):
        # status=200 的理由同 openai 分支
        raise LLMError(f"嵌入模型回傳 {len(vecs)} 筆向量,與送出的 {len(texts)} 筆不符",
                       status=200)
    return [list(v) for v in vecs]


# ── 模型清單 / 預熱 ─────────────────────────────────────────────────

async def list_models(settings: dict, base_url_override: str = "") -> list[str]:
    """列出服務端可用的模型(依名稱排序)。

    設定 UI 的「列出模型」同時兼任「測試連線」,所以 override 收前端輸入框裡
    還沒存檔的位址。
    """
    base = _base(settings, base_url_override)
    if _style(settings) == "openai":
        data = await _get(f"{base}/models", headers=_headers(settings), timeout=TIMEOUT_MODELS)
        names = [m.get("id") for m in (data.get("data") or [])]
    else:
        data = await _get(f"{base}/api/tags", headers=_headers(settings), timeout=TIMEOUT_MODELS)
        names = [m.get("name") for m in (data.get("models") or [])]
    return sorted(n for n in names if n)


async def warmup(settings: dict) -> bool:
    """請服務端先把模型載進記憶體。純最佳化,失敗一律安靜回 False。"""
    try:
        base, model = _base(settings), _model(settings)
        if _style(settings) == "openai":
            # OpenAI 相容規格沒有 preload 端點,用一個一個 token 就結束的假請求代替
            await _post(
                f"{base}/chat/completions",
                {"model": model, "messages": [{"role": "user", "content": "hi"}],
                 "max_tokens": 1, "stream": False},
                headers=_headers(settings), timeout=TIMEOUT_WARMUP,
            )
        else:
            # 不帶 prompt 的 preload 請求,不產生內容;keep_alive 用 Ollama 預設的 5 分鐘
            await _post(
                f"{base}/api/generate", {"model": model, "keep_alive": "5m"},
                headers=_headers(settings), timeout=TIMEOUT_WARMUP,
            )
    except LLMError:
        return False
    return True

"""開發用的假嵌入服務(OpenAI 相容 + Ollama 原生兩種介面都有)。

沒有 Ollama / LM Studio 在跑時,用它就能端到端驗語意檢索的整條路:建索引的
分批迴圈、混合排序、結果渲染。向量是**決定性的字元雜湊**,不是真的語意——
所以它證明的是「管線接得對」,不是「找得準」。

    python scripts/fake_embed_server.py            # 預設 8801 埠

設定 → AI 連線填 http://127.0.0.1:8801/v1(OpenAI 相容)或
http://127.0.0.1:8801(Ollama 原生),嵌入模型隨便填一個名字即可。
"""
import hashlib
import json
import math
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIM = 64


def vec(text: str) -> list[float]:
    """把文字攤成固定維度的向量:每個字元雜湊到一個維度上累加,再 L2 正規化。

    共用字元多的兩段文字會比較接近——夠像語意相似度到可以驗管線,但不要拿它
    的排序結果當作模型品質的判斷依據。
    """
    v = [0.0] * DIM
    for ch in text:
        h = int(hashlib.sha1(ch.encode("utf-8")).hexdigest()[:8], 16)
        v[h % DIM] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.endswith("/models"):
            self._send({"data": [{"id": "fake-embed"}, {"id": "fake-chat"}]})
        elif self.path.endswith("/api/tags"):
            self._send({"models": [{"name": "fake-embed"}, {"name": "fake-chat"}]})
        else:
            self.send_error(404)

    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.loads(raw or b"{}")
        texts = body.get("input")
        if isinstance(texts, str):
            texts = [texts]

        if self.path.endswith("/v1/embeddings") or self.path.endswith("/embeddings"):
            # 刻意**打亂順序**回傳:OpenAI 規格不保證順序,app/llm.py 必須依
            # data[].index 排回來。順序給對的話這個 bug 永遠不會被發現。
            rows = [{"index": i, "embedding": vec(t)} for i, t in enumerate(texts or [])]
            self._send({"data": list(reversed(rows))})
        elif self.path.endswith("/api/embed"):
            self._send({"embeddings": [vec(t) for t in (texts or [])]})
        elif "chat" in self.path or "generate" in self.path:
            self._send({"message": {"content": "{}"},
                        "choices": [{"message": {"content": "{}"}}]})
        else:
            self.send_error(404)

    def log_message(self, *a):  # 安靜一點
        pass


if __name__ == "__main__":
    port = int(os.environ.get("FAKE_EMBED_PORT", "8801"))
    print(f"fake embed server on http://127.0.0.1:{port}  (dim={DIM})", flush=True)
    # ⚠ 一定要 Threading:單執行緒的 HTTPServer 只要有一條連線掛著(瀏覽器的
    # keep-alive、或建索引時重疊的請求)就整個卡死,症狀是「明明在 listen 卻
    # 連不上」,很難一眼看出來。
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()

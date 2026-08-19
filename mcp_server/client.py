"""Jargon Vault 的 HTTP 客戶端封裝。

注意:這不是 Jargon Vault 主程式的一部分,是給 mcp_server/ 這個獨立小工具用的——
專門對「正在跑的」 Jargon Vault 後端(預設 http://127.0.0.1:8787)發 HTTP 請求,
模擬前端 fetch 呼叫同一組 /api/* 端點。

Jargon Vault 的驗證機制是簽章 httpOnly cookie(見 app/auth.py),不是 API金鑰/JWT
in header,所以這裡要先呼叫 /api/auth/login 換到 session cookie,之後每次請求
httpx 的 cookie jar 會自動帶上,行為跟瀏覽器一致。
"""
from __future__ import annotations

import os

import httpx


class JargonVaultError(RuntimeError):
    """呼叫 Jargon Vault API 失敗時丟出,訊息已整理成人類可讀的錯誤說明。"""


class JargonVaultClient:
    def __init__(self) -> None:
        self.base_url = os.environ.get("JARGON_BASE_URL", "http://127.0.0.1:8787").rstrip("/")
        self.email = os.environ.get("JARGON_EMAIL", "").strip()
        self.password = os.environ.get("JARGON_PASSWORD", "")
        session_cookie = os.environ.get("JARGON_SESSION_COOKIE", "").strip()
        # httpx.AsyncClient 內建 cookie jar:同一個 client 實例的所有請求都會
        # 自動帶上登入後拿到的 gv_session cookie,不用手動管理 headers。
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)
        self._logged_in = False
        if session_cookie:
            # 允許直接帶入既有的 session cookie(例如從瀏覽器 devtools 複製),
            # 這樣就不用把密碼放進 MCP 設定檔。
            self._http.cookies.set("gv_session", session_cookie)
            self._logged_in = True

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _login(self) -> None:
        if not self.email or not self.password:
            raise JargonVaultError(
                "尚未登入 Jargon Vault。請設定環境變數 JARGON_EMAIL 與 JARGON_PASSWORD"
                "(或直接提供已登入取得的 JARGON_SESSION_COOKIE)。"
            )
        resp = await self._http.post(
            "/api/auth/login", json={"email": self.email, "password": self.password}
        )
        if resp.status_code != 200:
            raise JargonVaultError(f"登入 Jargon Vault 失敗({resp.status_code}):{resp.text}")
        self._logged_in = True

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """統一的請求入口:確保已登入、401 時自動重新登入一次再重試、非 2xx 一律轉成 JargonVaultError。"""
        if not self._logged_in:
            await self._login()
        resp = await self._http.request(method, path, **kwargs)
        if resp.status_code == 401 and self.email and self.password:
            # session 可能已過期(30 天)或伺服器重啟後金鑰不同,重新登入一次再試
            self._logged_in = False
            await self._login()
            resp = await self._http.request(method, path, **kwargs)
        if resp.status_code >= 400:
            raise JargonVaultError(f"{method} {path} 失敗({resp.status_code}):{resp.text}")
        return resp

    async def get(self, path: str, **kwargs) -> httpx.Response:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs) -> httpx.Response:
        return await self.request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs) -> httpx.Response:
        return await self.request("PUT", path, **kwargs)

    async def delete(self, path: str, **kwargs) -> httpx.Response:
        return await self.request("DELETE", path, **kwargs)


_client: JargonVaultClient | None = None


def get_client() -> JargonVaultClient:
    """整個 MCP server 只建立一次 client,讓登入後拿到的 session cookie 能重複使用。"""
    global _client
    if _client is None:
        _client = JargonVaultClient()
    return _client

"""
Jargon Vault — 桌面版啟動器(PyInstaller 打包的進入點)。

與 main.py 的差別只有三件事,其餘一律共用同一個 create_app():
  1. 綁定 127.0.0.1(桌面版沒有「對外服務」的情境,預設就不該監聽 0.0.0.0)
  2. 埠被佔用時往後找一個能用的,而不是丟 traceback 給不會看的人
  3. 起好之後自動開瀏覽器

⚠ Windows 若用 --windowed(無主控台)打包,sys.stdout/sys.stderr 會是 None,
  logging.StreamHandler() 會在啟動當下就爆掉。所以這裡在最前面把兩者導到檔案,
  在 import app 之前做完——app/__init__.py 的 _setup_logging() 會用到它們。
"""
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path


def _force_utf8_streams() -> None:
    """Windows 主控台在輸出被重新導向時,預設編碼是系統 ANSI codepage
    (繁體中文 Windows 是 cp950),印出任何非 ASCII 字元會直接
    UnicodeEncodeError 把程式打死——而使用者看到的是「雙擊沒反應」。
    ⚠ 這不是理論風險:2026-08-09 在 Wine 的 cp1252 主控台實測重現,
      一個 '→' 就足以讓整支程式起不來。Linux 上永遠測不到這個。"""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _redirect_std_streams() -> None:
    if sys.stdout is not None and sys.stderr is not None:
        _force_utf8_streams()
        return
    from app.config import LOG_DIR  # 先確保資料目錄邏輯已生效
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    f = open(LOG_DIR / "console.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or f
    sys.stderr = sys.stderr or f
    _force_utf8_streams()


def _free_port(preferred: int, tries: int = 20) -> int:
    for port in range(preferred, preferred + tries):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def main() -> None:
    _redirect_std_streams()
    import uvicorn
    from app import create_app

    host = os.environ.get("GLOSSARY_HOST", "127.0.0.1")
    port = _free_port(int(os.environ.get("GLOSSARY_PORT", "8787")))
    url = f"http://{host}:{port}"

    if os.environ.get("GV_NO_BROWSER") != "1":
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    print(f"Jargon Vault → {url}")
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

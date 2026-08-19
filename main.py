"""
Jargon Vault — 本機極速名詞筆記
架構:檔案為真(data/notes/*.md,YAML frontmatter + Markdown 內文)
      SQLite FTS5(trigram)為可拋棄索引,啟動時自動重建
啟動:python main.py   →  http://127.0.0.1:8787

程式碼在 app/ 套件內,模組地圖見 CLAUDE.md。
"""
import os

import uvicorn

from app import create_app

app = create_app()

if __name__ == "__main__":
    # host 預設 127.0.0.1(本機開發不變);容器內用 GLOSSARY_HOST=0.0.0.0 才連得到。
    host = os.environ.get("GLOSSARY_HOST", "127.0.0.1")
    port = int(os.environ.get("GLOSSARY_PORT", "8787"))
    uvicorn.run(app, host=host, port=port)

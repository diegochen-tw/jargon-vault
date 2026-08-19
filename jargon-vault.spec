# -*- mode: python ; coding: utf-8 -*-
"""
Jargon Vault 桌面版打包設定(PyInstaller)。

⚠ 一定要在**乾淨的 venv**(只裝 requirements.txt + pyinstaller)裡跑。
   PyInstaller 會把環境裡搆得到的東西一起捲進去:在額外裝了 numpy/pillow 的
   環境實測產物 41MB,同一份程式碼在乾淨 venv 是 18MB。
⚠ upx 一律 False:加殼是防毒啟發式最典型的特徵之一,省幾 MB 換一堆誤判不划算。
⚠ demo/ 一定要打包(2026-08-18 起):註冊新帳號時 app/demo.py 會從那裡複製範例
   資料進使用者的庫。漏了它,桌面版註冊完拿到的是完全空白的庫——而且不會報錯,
   seed_vault() 找不到目錄只會在 log 留一行 warning。
⚠ hiddenimports 刻意留空:實測 uvicorn / fastapi / bcrypt / pyyaml 全部被自動
   偵測到,不需要任何一項。真的要加之前先確認是不是別的問題。
"""
import os

from PyInstaller.utils.hooks import collect_data_files

datas = [
    ("static", "static"),                      # 前端(無 build step,原樣複製)
    ("official_plugins", "official_plugins"),  # 官方外掛封裝型錄;漏了它外掛頁會是空的
    ("demo", "demo"),                          # 範例資料種子(app/demo.py);漏了它新帳號是空白的
]
# opencc(AI 輸出的 s2twp 簡轉繁)的字典與 config 是套件內的 .txt/.json 資料檔,
# PyInstaller 的 import 偵測收不到——漏了它,打包版的 OpenCC("s2twp") 會在啟動時
# 拋例外,被 routers/ai.py 的 try 吃掉,症狀是「打包版永遠不轉換」而且無聲。
datas += collect_data_files("opencc")

excludes = [
    "tkinter", "unittest", "pydoc", "doctest", "lib2to3", "test",
    "pytest", "numpy", "PIL", "pandas", "matplotlib",
]

a = Analysis(
    ["desktop_launcher.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,          # onedir:啟動快、防毒誤判機率較低
    name="JargonVault",
    console=True,                   # 第一版留著主控台:出事看得見,勝過「雙擊沒反應」
    icon=os.path.join("packaging", "icon.ico"),
    version=os.path.join("packaging", "version_info.txt"),
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.datas, name="JargonVault", upx=False)

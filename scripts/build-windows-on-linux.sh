#!/usr/bin/env bash
# =============================================================================
# 在 Linux 上交叉打包出 Windows 版 JargonVault.exe
#
# PyInstaller **不能**跨平台編譯——它是把「當下這個直譯器」連同相依一起封裝,
# 所以要產出 Windows 執行檔,就必須有一個真的在跑的 Windows Python。這支腳本
# 的做法是:Wine(提供 Windows API)+ python-build-standalone 的 Windows 版
# CPython(提供真正的 Windows 直譯器與 .pyd),PyInstaller 因此會抓到 Windows
# 版的 bootloader,產出的就是貨真價實的 PE32+ 執行檔。
#
# 這條路線是「沒有 Windows 機器時的備案」。正規做法仍然是 GitHub Actions 開一台
# windows-latest runner——那裡有真正的 Windows,不需要 Wine,也不會有下面那些
# Wine 專屬的怪毛病。
#
# 實測環境:Ubuntu 24.04 + wine 9.0 + CPython 3.12.11(Windows x86-64)
# =============================================================================
set -euo pipefail

SRC="${1:-$PWD}"                       # Jargon Vault repo 根目錄
WINPY_TAG="20251007"
WINPY="cpython-3.12.11+${WINPY_TAG}-x86_64-pc-windows-msvc-install_only.tar.gz"
export WINEPREFIX="${WINEPREFIX:-$HOME/.wine64}" WINEARCH=win64 WINEDEBUG=-all
export PYTHONLEGACYWINDOWSSTDIO=1

echo "==> 1/5 安裝 wine 與 xvfb"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
     wine wine64 xvfb librsvg2-bin

echo "==> 2/5 建立 64 位元 wine prefix"
xvfb-run -a wineboot -u

echo "==> 3/5 取得 Windows 版 CPython"
if [ ! -d "$WINEPREFIX/drive_c/py" ]; then
  curl -fsSL -o /tmp/winpy.tar.gz \
    "https://github.com/astral-sh/python-build-standalone/releases/download/${WINPY_TAG}/${WINPY}"
  mkdir -p /tmp/winpy && tar xzf /tmp/winpy.tar.gz -C /tmp/winpy
  mkdir -p "$WINEPREFIX/drive_c/py"
  cp -r /tmp/winpy/python/. "$WINEPREFIX/drive_c/py/"
fi

echo "==> 4/5 在 Windows Python 裡裝相依"
cp "$SRC/requirements.txt" "$WINEPREFIX/drive_c/py/"
# ⚠ 一定要走 xvfb-run,而且輸出要用管線不要用 > 檔案重導向:
#    Wine 下把 stdout 導進檔案會讓 CPython 的 init_sys_streams 拿到 invalid
#    handle 而啟動失敗(WinError 6)。這是 Wine 的毛病,不是程式的。
xvfb-run -a wine 'C:\py\python.exe' -m pip install --no-warn-script-location \
  --only-binary=:all: -r 'C:\py\requirements.txt' pyinstaller 2>&1 | tail -3

echo "==> 5/5 打包"
rm -rf "$WINEPREFIX/drive_c/src"
mkdir -p "$WINEPREFIX/drive_c/src"
cp -r "$SRC"/{app,static,official_plugins,packaging,desktop_launcher.py,jargon-vault.spec} \
      "$WINEPREFIX/drive_c/src/"
find "$WINEPREFIX/drive_c/src" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

xvfb-run -a wine 'C:\py\python.exe' -m PyInstaller --noconfirm \
  --distpath 'C:\src\dist' --workpath 'C:\src\build' 'C:\src\jargon-vault.spec' 2>&1 | tail -3

OUT="$WINEPREFIX/drive_c/src/dist/JargonVault"
file "$OUT/JargonVault.exe"
( cd "$(dirname "$OUT")" && zip -qr9 "$SRC/JargonVault-windows-x64.zip" JargonVault )
echo "完成 → $SRC/JargonVault-windows-x64.zip"

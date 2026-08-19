#!/usr/bin/env python3
"""
Demo 種子腳本(冪等)。由 Docker entrypoint 在啟動 app 之前呼叫。

用法:  python scripts/seed_demo.py [sample|blank|off]
環境:  DEMO_EMAIL / DEMO_PASSWORD(預設 demo@example.com / demo1234)
        GLOSSARY_DATA_DIR(資料根目錄;容器內為 /data)

模式:
  sample  建立 demo 帳號 + 範例名詞 + 標籤群組(試用)
  blank   只建立 demo 帳號,無任何範例資料(決定正式用時的乾淨起點)
  off     完全不建立

已存在同 email 的帳號就整支略過(重啟不會重種)。索引 / plugins / ai_settings
一律交給隨後啟動的 app(create_app 會對每個現有使用者補齊),這裡不重造輪子。

⚠ 複製範例檔案的邏輯**不在這裡**:唯一實作在 app/demo.py 的 seed_vault(),
註冊流程(app/routers/auth.py)走的是同一支。這支腳本只多做「建一個共用的
demo 帳號」這件容器專屬的事。
"""
import os
import sys
from pathlib import Path

# 腳本在 scripts/ 底下,把 repo 根目錄加進 import 路徑才能 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import hash_password  # noqa: E402
from app.config import ensure_dirs  # noqa: E402
from app.demo import seed_vault  # noqa: E402
from app.paths import ensure_user_dirs, user_paths  # noqa: E402
from app.templates import ensure_templates  # noqa: E402
from app.users import create_user, ensure_users, find_by_email, set_demo_seeded  # noqa: E402

DEMO_ID = "demo"


def main() -> None:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "sample").strip().lower()
    if mode == "off":
        print("[seed] DEMO_SEED=off — 不建立 demo 帳號")
        return
    if mode not in ("sample", "blank"):
        mode = "sample"

    email = os.environ.get("DEMO_EMAIL", "demo@example.com").strip().lower()
    password = os.environ.get("DEMO_PASSWORD", "demo1234")

    ensure_dirs()  # 種子在 app 之前跑,先確保資料根目錄存在(容器內 /data 是掛載點)
    ensure_users()
    if find_by_email(email):
        print(f"[seed] demo 帳號已存在({email}),略過。")
        return

    # demo 帳號預設就是 admin,開箱即可進「設定 → 管理」管理註冊/白名單/OAuth/使用者。
    user = create_user(id=DEMO_ID, email=email, password_hash=hash_password(password),
                       is_admin=True)
    paths = user_paths(user["id"])
    ensure_user_dirs(paths)

    if mode == "sample":
        # 樣板要先建檔,seed_vault() 才有東西可以打開範例用到的那幾個。
        ensure_templates(paths)
        n = seed_vault(paths)
        set_demo_seeded(user["id"], True)
        print(f"[seed] 已建立 demo 帳號 + {n} 筆範例名詞 + 標籤群組。")
    else:
        print("[seed] 已建立空白 demo 帳號(無範例資料)。")

    print("=" * 52)
    print(f"[seed]   Demo 登入帳號:{email}")
    print(f"[seed]   Demo 登入密碼:{password}")
    print("=" * 52)


if __name__ == "__main__":
    main()

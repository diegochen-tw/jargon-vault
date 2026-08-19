"""
Pytest 共用 fixture。

關鍵前提:app/config.py 的 DATA_DIR 是「模組第一次被 import 時」就綁定
的常數(讀 GLOSSARY_DATA_DIR env var),之後任何人 import app.config 拿到
的都是同一個值,不會因為事後改 env var 而改變。所以 GLOSSARY_DATA_DIR
必須在本檔案最上面、任何 `import app...` 之前就設好——這也是為什麼
下面對 app.* 的 import 特意放在設定 env var 之後,並加 noqa: E402。

隔離分兩層:
  1. Session 層:整個 pytest 執行期間共用一個臨時目錄當 GLOSSARY_DATA_DIR,
     絕對不會碰到 repo 底下真正的 data/。
  2. 測試層:每個測試各自用一個隨機 user_id(paths fixture)在這個共用臨時
     目錄下開自己的 data/users/<uid>/,測試之間不會互相污染,不需要每個
     測試都開一個新的臨時目錄那麼重。
"""
import os
import shutil
import tempfile
import uuid

import bcrypt
import pytest

_TEST_DATA_DIR = tempfile.mkdtemp(prefix="jargon-vault-pytest-")
os.environ["GLOSSARY_DATA_DIR"] = _TEST_DATA_DIR
os.environ.setdefault("SESSION_SECRET", "pytest-fixed-secret-not-for-prod")
# 註冊時的範例資料種子預設是開的(app/config.py:SEED_DEMO_ON_REGISTER)。整套測試
# 一律關掉:register_user fixture 是幾乎每個整合測試的起點,種進 11 筆範例名詞會讓
# 所有「新帳號的庫是空的」斷言失效。要測種子本身的請看 tests/test_demo.py——那支
# 直接呼叫 app.demo 的函式,不依賴這個 env var。
# 順序約束同 GLOSSARY_DATA_DIR:config.py 在 import 當下就把它讀成常數。
os.environ["GLOSSARY_SEED_DEMO"] = "0"

# bcrypt 壓到最低輪數(4):bcrypt 是刻意慢的,預設 12 輪一次 ~150ms、4 輪
# 不到 1ms。每個走 HTTP 的整合測試註冊/登入都付一次,全套約 300 次呼叫,
# 累積約一分鐘的純雜湊(佔比不大——大頭是 conftest 檔頭說的二次方索引重建,
# 但這一分鐘是零風險白拿的)。
# 只 patch gensalt 就夠——checkpw 的成本跟著「雜湊字串裡存的輪數」走,所以
# 用 4 輪產生的雜湊,驗證也自動是 4 輪。這裡跟 GLOSSARY_DATA_DIR 一樣有
# 順序約束:必須在任何 app.* 被 import 之前生效,因為 app/routers/auth.py
# 的 DUMMY_PASSWORD_HASH(防時間側信道的假比對)是模組 import 當下就算好的,
# patch 晚了它就是一顆 12 輪的雜湊,test_ratelimit 那些「查無此人」的登入
# 測試會每次照付全額。
_real_gensalt = bcrypt.gensalt
bcrypt.gensalt = lambda rounds=12, prefix=b"2b": _real_gensalt(4, prefix)

from app.paths import VaultPaths, ensure_user_dirs, user_paths  # noqa: E402


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clear_rate_limit():
    """每個測試前後清空登入失敗計數(app/ratelimit.py 的狀態是**行程層級**的
    模組字典,不隨 DATA_DIR 隔離)。

    沒有這層,整套測試會有一顆定時炸彈:TestClient 的來源位址全都是 "testclient",
    所以每一個測試的登入失敗都累加到**同一個 per-IP 計數器**上。累積超過門檻之後,
    後面所有測試的登入都會拿到 429——而且症狀會隨測試執行順序漂移,是最難查的
    那種 flaky。
    """
    from app import ratelimit
    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture
def paths() -> VaultPaths:
    """一組獨立、乾淨的 VaultPaths——每個測試各自的假使用者,彼此不互相污染。
    只建目錄,不建 templates.json/tags.json/index.db,測試各自視需要呼叫
    ensure_templates()/rebuild_index() 等函式,確保每個測試對「起始狀態」的
    假設是明確寫在測試裡的,不是隱含在 fixture 裡。"""
    uid = f"t{uuid.uuid4().hex[:12]}"
    p = user_paths(uid)
    ensure_user_dirs(p)
    return p


@pytest.fixture
def app_instance():
    """建一次 FastAPI app(同一個測試內若註冊多個使用者,都掛在同一個 app 上,
    也就是同一個隔離 DATA_DIR),function-scoped:每個測試都是全新的 app,
    測試之間不會共用任何狀態。"""
    from app import create_app
    return create_app()


@pytest.fixture
def client(app_instance):
    """單一使用者視角的 TestClient(獨立 cookie jar)。"""
    from fastapi.testclient import TestClient
    return TestClient(app_instance)


@pytest.fixture
def register_user(app_instance, monkeypatch):
    """回傳一個 helper function:每呼叫一次就註冊一個新使用者,並給它自己
    獨立的 TestClient(獨立 cookie jar——模擬不同瀏覽器/裝置各自登入),
    這樣同一個測試裡註冊多個帳號時才不會互搶同一份 session cookie
    (見 test_api.py 的 test_users_data_is_isolated_between_accounts)。
    回傳 {id, email, client, paths}。"""
    from fastapi.testclient import TestClient
    allowed: set[str] = set()

    def _register(email: str | None = None, password: str = "testpass123"):
        email = email or f"t{uuid.uuid4().hex[:10]}@example.com"
        allowed.add(email.strip().lower())
        monkeypatch.setenv("ALLOWED_EMAILS", ",".join(allowed))
        c = TestClient(app_instance)
        r = c.post("/api/auth/register", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        body = r.json()
        return {"id": body["id"], "email": body["email"], "client": c,
                "paths": user_paths(body["id"])}
    return _register

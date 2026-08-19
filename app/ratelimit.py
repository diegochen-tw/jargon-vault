"""
登入失敗的速率限制與鎖定(記憶體內,行程層級)。

為什麼需要:cookie 有 httponly + samesite=lax,CSRF 風險低;但 `POST /api/auth/login`
本身可以無限次試。bcrypt 只讓每次嘗試變慢,擋不住有人整夜刷——而 CLAUDE.md 花了
一整段講 PUBLIC_BASE_URL 與 Cloudflare Tunnel,意思是這東西預期會被掛到公網。

**兩個維度各自獨立計數**,任一命中就鎖:
  - per-IP:擋「同一台機器對很多帳號噴密碼」(credential stuffing)。
  - per-email:擋「很多台機器對同一個帳號噴密碼」(分散式暴力破解)。
單看 IP 擋不住第二種,單看 email 擋不住第一種,所以兩個都要。

刻意的取捨:

  - **狀態存在記憶體,不落地**。`main.py` 是單行程 uvicorn(`uvicorn.run(app)`,
    沒有 workers 參數),所以一份行程內字典就是完整的真相。重啟會清空計數——
    這是可接受的:攻擊者無法讓伺服器重啟,而管理者重啟時本來就該解開誤鎖。
    ⚠ 哪天改成多 worker 或多台,這個模組必須換成共享儲存(Redis/SQLite),
    否則 N 個 worker 各自計數 = 實際門檻變成 N 倍。
  - **不做全域請求速率限制**,只做登入失敗計數。全域限流要嘛擋不到真正的攻擊
    (攻擊者放慢就好),要嘛誤傷正常使用(前端一次載入就好幾支 API)。
  - **不寫進 site_settings.json 的 last_* 狀態**:計數是易失的執行期狀態,不是設定。

本模組不 import fastapi(在 routers 底下一層),也不碰檔案系統。取用者 IP 的解析
做成純函式 `client_ip()`,由 router 把 `request.client.host` 與 X-Forwarded-For
標頭餵進來。
"""
import threading
import time

# 記憶體上限:避免被灌爆。信任 X-Forwarded-For 時,標頭是攻擊者可控的外部輸入,
# 他可以每次送一個不同的假 IP —— 沒有上限的話字典會無限長大變成記憶體耗盡攻擊。
# 超過就把「最舊一次失敗」的鍵先丟掉(那些本來也快過期了)。
MAX_TRACKED_KEYS = 4096

_lock = threading.Lock()
_failures: dict[str, list[float]] = {}


def _now() -> float:
    return time.time()


def client_ip(remote_addr: str | None, forwarded_for: str | None, *, trust_proxy: bool) -> str:
    """算出這次請求要用哪個字串當 per-IP 的計數鍵。

    ⚠ 這裡是整個模組最容易搞錯的地方,兩個方向都會壞:

      - **反向代理後面卻不看 X-Forwarded-For**:所有請求的 remote_addr 都是代理
        自己那一個 IP,全世界共用同一個計數器——任何人失敗幾次就把**所有人**鎖在
        門外。這不是限流,是自助 DoS。
      - **沒有代理卻信任 X-Forwarded-For**:標頭由用戶端任意填,攻擊者每次換一個
        假 IP,per-IP 這條線等於不存在(per-email 那條還在,所以不是全破)。

    所以是否採信一律由 `trust_proxy` 明確決定(站台設定裡的開關),絕不自動猜。
    取最左邊那一段:XFF 的慣例是 `client, proxy1, proxy2`,最左邊才是原始來源。
    """
    if trust_proxy and forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first
    return (remote_addr or "").strip() or "unknown"


def _prune_locked(cutoff: float) -> None:
    """丟掉整串都已過期的鍵。呼叫端必須已持有 _lock。"""
    dead = [k for k, ts in _failures.items() if not ts or ts[-1] < cutoff]
    for k in dead:
        del _failures[k]
    if len(_failures) > MAX_TRACKED_KEYS:
        # 依「最後一次失敗」由舊到新排,砍掉最舊的那批
        victims = sorted(_failures, key=lambda k: _failures[k][-1])[: len(_failures) - MAX_TRACKED_KEYS]
        for k in victims:
            del _failures[k]


def _retry_after(key: str, limit: int, window: float, lockout: float, now: float) -> int:
    """這個鍵還要鎖幾秒(0 = 沒鎖)。

    判定方式:window 內的失敗次數達到 limit 就算觸發,鎖到「最後一次失敗 + lockout」
    為止。所以鎖定期間再試會把時間往後推——攻擊者持續刷就持續鎖著,而正常使用者
    停手等一下就自然解開,不需要管理者介入。
    """
    stamps = [t for t in _failures.get(key, ()) if t >= now - window]
    if len(stamps) < limit:
        return 0
    remain = stamps[-1] + lockout - now
    return max(0, int(remain) + 1) if remain > 0 else 0


def check(ip: str, email: str, cfg: dict) -> int:
    """還要鎖幾秒(0 = 放行)。**必須在驗證密碼之前呼叫**——鎖定時要連 bcrypt
    都不跑,否則攻擊者照樣能用登入端點把 CPU 燒滿(bcrypt 本來就是刻意慢的)。"""
    if not cfg.get("enabled", True):
        return 0
    now = _now()
    window = cfg["window_minutes"] * 60
    lockout = cfg["lockout_minutes"] * 60
    with _lock:
        _prune_locked(now - max(window, lockout))
        return max(
            _retry_after(f"ip:{ip}", cfg["ip_max_attempts"], window, lockout, now),
            _retry_after(f"email:{email}", cfg["email_max_attempts"], window, lockout, now),
        )


def record_failure(ip: str, email: str, cfg: dict) -> None:
    """記一次失敗。停用時仍然不記——避免「關掉再打開」時突然拿一堆舊帳來鎖人。"""
    if not cfg.get("enabled", True):
        return
    now = _now()
    window = cfg["window_minutes"] * 60
    lockout = cfg["lockout_minutes"] * 60
    keep = now - max(window, lockout)
    with _lock:
        for key in (f"ip:{ip}", f"email:{email}"):
            stamps = [t for t in _failures.get(key, ()) if t >= keep]
            stamps.append(now)
            _failures[key] = stamps
        _prune_locked(keep)


def record_success(ip: str, email: str) -> None:
    """登入成功就把這個 IP 與這個 email 的失敗紀錄清掉。

    ⚠ 不加 `enabled` 判斷:功能停用時也要能清,否則「開著→累積失敗→關掉→
    再打開」會讓使用者莫名被舊帳鎖住。清除永遠是安全的方向。
    """
    with _lock:
        _failures.pop(f"ip:{ip}", None)
        _failures.pop(f"email:{email}", None)


def reset() -> None:
    """清空全部計數。給測試用,也給 admin 改設定後「解開所有鎖」用。"""
    with _lock:
        _failures.clear()


def snapshot() -> dict:
    """目前追蹤中的鍵數量(給管理 UI 顯示,不外洩實際的 IP/email)。"""
    with _lock:
        now = _now()
        return {
            "tracked_keys": len(_failures),
            "recent_failures": sum(len([t for t in ts if t >= now - 3600]) for ts in _failures.values()),
        }

"""
站台註冊邀請連結登記簿(真相來源,`data/invites.json`)。

## 為什麼這件事比任何功能深度都重要

自架開源專案的死法幾乎都一樣:**裝起來了,但第二個人從來沒進來**。
沒有邀請連結時,把同事拉進來要四步,其中兩步在系統外(口頭給網址)、
一步不做就完全加不了人(`registration_mode` 預設是 whitelist):

    admin 註冊 → 進管理填白名單 → 站外告知網址 → 對方註冊

邀請連結把中間兩步收成一條 URL。在公開筆記的動線裡它是收尾的那一段:
公開筆記負責把知識送出去,邀請連結負責把讀者接進來成為新維護者。

## 為什麼是第三個全域真相檔(`data/invites.json`)

CLAUDE.md 規定「全域真相檔只有 users.json 與 site_settings.json,要加第三個
必須寫下理由」。理由:邀請登記簿是**動態登記簿不是設定**——`consume()` 在每次
註冊時都要扣次數落盤,塞進 `site_settings.json` 會讓每一次註冊都重寫整份
含 `api_key` 的站台設定;而且 `site_settings._clean_*()` 「從零重建 dict」的
慣例與「任意 nonce 為 key」的形狀天生打架。

## 形狀

    {"<nonce>": {"created": 1785.0, "expires": 2385.0,
                 "uses_left": 1, "created_by": "<uid>"}}

token 就是 nonce 本身。nonce 必須**隨機**(`secrets.token_urlsafe`)——理由與
`share_links.py` 完全相同:內容若是決定性的,「撤銷 → 重新產生」會給出
一模一樣的字串,存過舊網址的人立刻恢復存取,撤銷就是假的。

同樣刻意**不用 itsdangerous 簽章**:驗證本來就要拿 nonce 去查表,nonce 本身
就是秘密,簽章擋不住任何東西,卻會讓 `SESSION_SECRET` 一輪替就打死所有既有連結。

## 預設從嚴

一次性、7 天。持有連結**會繞過** `registration_mode` 與 email 白名單
(不繞過的話 admin 要同時做兩件事,漏斗又長回來)——代價是連結外流等於陌生人
能註冊一個帳號(空庫的新帳號,不含任何人的資料),所以那兩個預設值與
「隨時可撤銷」是配套的防線,不是可有可無的裝飾。建立/撤銷只收站台 admin
(繞過白名單=註冊控制,權限收在管註冊的人手上)。

本模組只碰檔案系統,不碰 SQLite、不碰 HTTP。
"""
import json
import secrets
import time

from . import atomic
from .config import DATA_DIR

INVITES_PATH = DATA_DIR / "invites.json"

NONCE_BYTES = 12          # token_urlsafe(12) → 16 字元 / 96 bits
DEFAULT_TTL_DAYS = 7
DEFAULT_USES = 1
MAX_USES = 200
MAX_TTL_DAYS = 90


def load_invites() -> dict[str, dict]:
    """讀邀請登記簿。檔案不存在 = 沒有任何邀請,回空 dict 但不建檔。

    ⚠ 壞檔一律當成「沒有任何有效邀請」。授權的預設值必須是拒絕——
    讀不出來就放行等於一個手滑寫壞的 JSON 讓所有舊連結復活。
    """
    try:
        data = json.loads(INVITES_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for nonce, meta in data.items():
        if not isinstance(meta, dict):
            continue
        try:
            out[str(nonce)] = {
                "created": float(meta.get("created") or 0),
                "expires": float(meta.get("expires") or 0),
                "uses_left": int(meta.get("uses_left") or 0),
                "created_by": str(meta.get("created_by") or ""),
            }
        except (TypeError, ValueError):
            continue
    return out


def save_invites(data: dict[str, dict]) -> None:
    atomic.write_json(INVITES_PATH, data, sort_keys=True)


def create_invite(*, created_by: str,
                  uses: int = DEFAULT_USES, ttl_days: int = DEFAULT_TTL_DAYS) -> str:
    """產生一條新邀請,回傳 nonce。既有的邀請不受影響(可以同時發好幾條)。"""
    uses = max(1, min(int(uses or DEFAULT_USES), MAX_USES))
    ttl_days = max(1, min(int(ttl_days or DEFAULT_TTL_DAYS), MAX_TTL_DAYS))
    data = load_invites()
    nonce = secrets.token_urlsafe(NONCE_BYTES)
    while nonce in data:  # 機率極低,但碰撞會讓兩條邀請互相干擾
        nonce = secrets.token_urlsafe(NONCE_BYTES)
    now = time.time()
    data[nonce] = {"created": now, "expires": now + ttl_days * 86400,
                   "uses_left": uses, "created_by": created_by}
    save_invites(data)
    return nonce


def revoke_invite(nonce: str) -> bool:
    data = load_invites()
    if nonce not in data:
        return False
    data.pop(nonce)
    save_invites(data)
    return True


def purge_expired() -> int:
    """清掉過期或用完的邀請。列表時順手做,不需要排程器。"""
    data = load_invites()
    alive = {n: m for n, m in data.items() if _alive(m)}
    if len(alive) == len(data):
        return 0
    save_invites(alive)
    return len(data) - len(alive)


def _alive(meta: dict) -> bool:
    return meta["uses_left"] > 0 and (meta["expires"] <= 0 or meta["expires"] > time.time())


def peek(nonce: str) -> dict | None:
    """查一條邀請是否還有效(**不消耗**它)。給預覽頁用。"""
    meta = load_invites().get((nonce or "").strip())
    return meta if meta and _alive(meta) else None


def consume(nonce: str) -> dict | None:
    """用掉一次。回傳當時的 meta;已失效回 None。

    ⚠ 先檢查有效性再扣次數,而且扣完立刻落盤——不然同一條一次性連結
    被兩個人同時打開時,兩個人都會進得來。
    """
    nonce = (nonce or "").strip()
    data = load_invites()
    meta = data.get(nonce)
    if not meta or not _alive(meta):
        return None
    meta["uses_left"] -= 1
    if meta["uses_left"] <= 0:
        data.pop(nonce)
    save_invites(data)
    return meta

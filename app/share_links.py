"""
公開分享連結的登記層(真相來源,data/users/<uid>/shares.json)。

形狀:{nonce: {note_id, created}}。一筆名詞最多一條有效連結。
對外的 token 是 `<uid>.<nonce>`(組裝與驗證在 app/public_share.py)。

⚠ nonce 必須是隨機的,**絕不能**由 note id 推導。若 token 的內容是決定性的,
「撤銷 → 重新分享」會產生一模一樣的 token,任何存過舊網址的人立刻恢復存取——
撤銷就變成假的。create_share() 因此每次都產新 nonce 並把舊的刪掉。

⚠ 這份檔案**刻意不進匯出/入**(routers/transfer.py 不碰它):token 綁的是本站
網址,搬到另一台毫無意義;更糟的是匯入會讓已經撤銷的連結復活。

連結有存活時限(config.SHARE_LINK_TTL_HOURS,預設 48 小時):過期判斷用
created + TTL 現算(_alive()),**find_by_note() 與 resolve_nonce() 都把過期
視為不存在**——前者是擁有者面板的狀態查詢、後者是公開端點的解析,漏任何一支
都會讓「已過期」與「看起來還有效」兩個答案並存。實際清檔交給 purge_expired()
(掛啟動迴圈與 GET /api/shares,比照 trash/invites「列表時順手清」的慣例)。
缺 created 的舊登記(load_shares 補 0)一律視同已過期——保守方向才正確:
分享的預設是「不可及」,壞資料不該換來一條永遠有效的連結。

放在使用者目錄而不是全域檔:擁有權對齊、刪帳號自動
清乾淨、沒有跨使用者的讀-改-寫競爭)。

本模組只碰檔案系統,不碰 SQLite、不碰 HTTP。
"""
import json
import secrets
import time

from . import atomic
from .config import SHARE_LINK_TTL_HOURS
from .paths import VaultPaths

NONCE_BYTES = 9  # secrets.token_urlsafe(9) → 12 字元 / 72 bits,猜不到


def _alive(meta: dict) -> bool:
    """這條登記是否還在存活時限內。缺 created(舊資料補 0)視同已過期,見檔頭。"""
    return time.time() - float(meta.get("created") or 0) < SHARE_LINK_TTL_HOURS * 3600


def load_shares(paths: VaultPaths) -> dict[str, dict]:
    try:
        data = json.loads(paths.shares_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for nonce, meta in data.items():
        if not isinstance(meta, dict):
            continue
        nid = str(meta.get("note_id") or "").strip()
        if not nid:
            continue
        out[str(nonce)] = {"note_id": nid, "created": meta.get("created", 0)}
    return out


def save_shares(paths: VaultPaths, data: dict[str, dict]) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    atomic.write_json(paths.shares_path, data, sort_keys=True)


def find_by_note(paths: VaultPaths, nid: str) -> str | None:
    """這筆名詞目前**仍有效**的 nonce(沒有或已過期就 None)。"""
    for nonce, meta in load_shares(paths).items():
        if meta["note_id"] == nid and _alive(meta):
            return nonce
    return None


def create_share(paths: VaultPaths, nid: str) -> str:
    """為某筆名詞產生新的分享 nonce,並讓它舊的那條立刻失效。

    「重新產生」與「第一次產生」是同一條路徑——都是先清掉這筆既有的 nonce
    再寫新的,所以使用者按「重新產生」時,舊網址一定當場作廢。
    """
    data = {n: m for n, m in load_shares(paths).items() if m["note_id"] != nid}
    nonce = secrets.token_urlsafe(NONCE_BYTES)
    while nonce in data:  # 機率極低,但碰撞會讓兩筆名詞共用一條連結,不能不擋
        nonce = secrets.token_urlsafe(NONCE_BYTES)
    data[nonce] = {"note_id": nid, "created": time.time()}
    save_shares(paths, data)
    return nonce


def revoke_share(paths: VaultPaths, nid: str) -> bool:
    """撤銷某筆名詞的分享連結。回傳是否真的有一條被撤掉。"""
    data = load_shares(paths)
    remain = {n: m for n, m in data.items() if m["note_id"] != nid}
    if len(remain) == len(data):
        return False
    save_shares(paths, remain)
    return True


def resolve_nonce(paths: VaultPaths, nonce: str) -> str | None:
    """nonce → note_id(查不到**或已過期**就 None)。"""
    meta = load_shares(paths).get(nonce)
    return meta["note_id"] if meta and _alive(meta) else None


def purge_expired(paths: VaultPaths) -> int:
    """清掉已過期的登記。列表時順手做,不需要排程器(同 invites.purge_expired)。"""
    data = load_shares(paths)
    alive = {n: m for n, m in data.items() if _alive(m)}
    if len(alive) == len(data):
        return 0
    save_shares(paths, alive)
    return len(data) - len(alive)


def count_active(paths: VaultPaths) -> int:
    """目前仍有效的連結條數(不含已過期)。"""
    return sum(1 for m in load_shares(paths).values() if _alive(m))


def revoke_all(paths: VaultPaths) -> int:
    """一鍵撤銷全部分享連結。回傳撤銷當下**仍有效**的條數(過期的不算撤銷,
    它們本來就已經打不開了);檔案則整份清空,順手把過期殘骸一起帶走。"""
    n = count_active(paths)
    save_shares(paths, {})
    return n

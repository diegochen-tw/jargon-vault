"""
整站備份與還原(app/backup.py)。

隔離很重要:還原會**刪掉整個 users/**,而 conftest 的 DATA_DIR 是整個 pytest
session 共用的。所以模組層的測試一律 monkeypatch `backup.DATA_DIR` /
`backup.BACKUP_DIR` / `backup.USERS_DIR` 到各自的 tmp_path——真的在共用目錄上
跑一次還原,後面所有測試的資料都會被抹掉,而且症狀會隨執行順序漂移。

最重要的幾支:
  - test_zip_slip_paths_are_never_extracted:ZIP 內的路徑是不可信輸入,
    在「整站還原」這個情境下等於任意檔案覆寫。
  - test_disposable_caches_and_logs_are_excluded:可拋棄快取不進備份(帶著它
    反而可能把壞掉的索引一起還原回來)。
  - test_shares_are_included:這正是備份與匯出的分野,
    CLAUDE.md 禁止 transfer.py 帶走它們,而備份**必須**帶走。
  - test_backups_dir_is_never_packed_into_itself:漏了就是指數成長。
  - test_restore_writes_a_snapshot_first:按錯了要救得回來。
  - test_bad_archives_are_rejected_before_anything_is_touched:先驗證再刪除。
  - test_at_time_uses_calendar_days_so_the_hour_actually_takes_effect:
    「設了時刻卻永遠不生效」那個無聲的失敗。
"""
import json
import time
import zipfile

import pytest

from app import backup


@pytest.fixture
def bdir(tmp_path, monkeypatch):
    """把 backup 模組看到的目錄常數換到 tmp_path,徹底離開共用的 DATA_DIR。
    ⚠ PUBLISHED_DIR 也要換:restore_backup 會 rmtree 它,漏了就是把共用測試
    目錄裡別的測試發佈的快照抹掉,症狀隨執行順序漂移。"""
    data = tmp_path / "data"
    users = data / "users"
    backups = data / "backups"
    users.mkdir(parents=True)
    backups.mkdir(parents=True)
    monkeypatch.setattr(backup, "DATA_DIR", data)
    monkeypatch.setattr(backup, "USERS_DIR", users)
    monkeypatch.setattr(backup, "BACKUP_DIR", backups)
    monkeypatch.setattr(backup, "PUBLISHED_DIR", data / "published")
    return data


def _seed(data, uid="u1"):
    """造一份看起來像真的資料目錄:兩個全域檔 + 一個使用者的完整內容。"""
    (data / "users.json").write_text(json.dumps([{"id": uid, "email": "a@b.c"}]), encoding="utf-8")
    (data / "site_settings.json").write_text('{"registration_mode":"open"}', encoding="utf-8")
    (data / ".session_secret").write_text("s3cr3t", encoding="utf-8")
    (data / ".legacy_migrated").write_text("1", encoding="utf-8")
    u = data / "users" / uid
    (u / "notes" / "assets" / "n1").mkdir(parents=True)
    (u / "trash").mkdir(parents=True)
    (u / "notes" / "n1.md").write_text("---\nid: n1\nname: 測試\n---\n內文", encoding="utf-8")
    (u / "notes" / "assets" / "n1" / "img.webp").write_bytes(b"\x00img")
    (u / "trash" / "n2.md").write_text("---\nid: n2\n---\n刪掉的", encoding="utf-8")
    for f in ("tags.json", "templates.json", "ai_settings.json", "plugins.json",
              "shares.json"):
        (u / f).write_text("{}", encoding="utf-8")
    (u / "index.db").write_bytes(b"FAKE-SQLITE")
    (u / "vectors.db").write_bytes(b"FAKE-VECTORS")
    return u


def _names(path):
    with zipfile.ZipFile(path) as z:
        return set(z.namelist())


# ── 打包內容 ────────────────────────────────────────────────────────────────

def test_backup_includes_every_source_of_truth(bdir):
    _seed(bdir)
    z = backup.create_backup()
    names = _names(z)
    for expected in (
        "users.json", "site_settings.json", ".session_secret", ".legacy_migrated",
        "users/u1/notes/n1.md", "users/u1/notes/assets/n1/img.webp",
        "users/u1/trash/n2.md", "users/u1/tags.json", "users/u1/templates.json",
        "users/u1/ai_settings.json", "users/u1/plugins.json",
    ):
        assert expected in names, f"備份漏了 {expected}"
    # 打包中途當掉不該留下「看起來完整、其實是半包」的檔案讓人日後拿去還原
    assert not list(backup.BACKUP_DIR.glob("*.part"))


def test_shares_are_included(bdir):
    """備份 ≠ 匯出。CLAUDE.md 明文禁止 transfer.py 帶走這兩個檔(token 綁本站
    網址、匯入會讓已撤銷的連結復活);但備份是**同一台**的災難復原,漏了它們
    就不是「回到那個時間點」。兩邊取捨方向相反,不可以互相抄。"""
    _seed(bdir)
    names = _names(backup.create_backup())
    assert "users/u1/shares.json" in names


def test_published_snapshots_round_trip_through_backup(bdir):
    """公開筆記快照(data/published/)必須進備份**而且還原得回來**——打包端是
    黑名單自動涵蓋,還原端的 _safe_members 白名單漏了 "published" 就是
    「備份看起來有、還原卻少一樣」的靜默缺口(backup.py 檔頭警告的那種)。"""
    _seed(bdir)
    pub = bdir / "published" / "abc123pid"
    (pub / "assets" / "n1").mkdir(parents=True)
    (pub / "manifest.json").write_text('{"pid":"abc123pid"}', encoding="utf-8")
    (pub / "notes.json").write_text("[]", encoding="utf-8")
    (pub / "assets" / "n1" / "img.webp").write_bytes(b"\x00img")
    z = backup.create_backup()
    names = _names(z)
    assert "published/abc123pid/manifest.json" in names
    assert "published/abc123pid/assets/n1/img.webp" in names

    backup.restore_backup(z)
    assert (bdir / "published" / "abc123pid" / "manifest.json").is_file()
    assert (bdir / "published" / "abc123pid" / "assets" / "n1" / "img.webp").is_file()


def test_disposable_caches_and_logs_are_excluded(bdir):
    """index.db 是可拋棄索引(還原後 rebuild_index 全量重建,帶著它反而可能把
    一個壞掉的索引一起還原);vectors.db 是昂貴快取,可從 .md 重算;logs 是噪音。
    manifest 要誠實記下排除了什麼。"""
    _seed(bdir)
    (bdir / "logs").mkdir()
    (bdir / "logs" / "app.log").write_text("noise" * 1000, encoding="utf-8")
    z = backup.create_backup()
    names = _names(z)
    assert not [n for n in names if n.endswith("index.db") or n.endswith("vectors.db")]
    assert not [n for n in names if n.startswith("logs/")]
    with zipfile.ZipFile(z) as zf:
        m = json.loads(zf.read(backup.MANIFEST_NAME))
    assert m["user_ids"] == ["u1"]
    assert "index.db" in m["excluded"] and "backups" in m["excluded"]


def test_backups_dir_is_never_packed_into_itself(bdir):
    """漏了這條,第 N 包會含前 N-1 包,大小指數成長直到塞爆硬碟。"""
    _seed(bdir)
    first = backup.create_backup()
    second = backup.create_backup()
    assert not [n for n in _names(second) if n.startswith("backups/")]
    assert second.stat().st_size < first.stat().st_size * 3


# ── 還原 ────────────────────────────────────────────────────────────────────

def test_restore_roundtrip(bdir):
    """完整取代的語意:改壞的檔案復原、備份裡沒有的使用者移除(還原到上週,
    這週才建立的帳號就該消失——它在還原前快照裡救得回來)。清除範圍必須嚴格
    對齊「備份會打包的東西」:寫成 rmtree(DATA_DIR) 會連 backups/ 一起刪掉,
    包含剛寫好的還原前快照與使用者僅有的其他備份。"""
    u = _seed(bdir)
    keep = backup.create_backup()
    z = backup.create_backup()
    (u / "notes" / "n1.md").write_text("被改壞了", encoding="utf-8")
    (u / "notes" / "assets" / "n1" / "img.webp").unlink()
    _seed(bdir, "u2")  # 備份之後才出現的使用者
    (bdir / "users.json").write_text("[]", encoding="utf-8")

    result = backup.restore_backup(z)

    assert "內文" in (u / "notes" / "n1.md").read_text(encoding="utf-8")
    assert (u / "notes" / "assets" / "n1" / "img.webp").read_bytes() == b"\x00img"
    assert json.loads((bdir / "users.json").read_text(encoding="utf-8"))[0]["id"] == "u1"
    assert result["user_ids"] == ["u1"]
    assert not (bdir / "users" / "u2").exists(), "備份裡沒有的使用者要被移除(完整取代)"
    assert keep.exists(), "還原不可以刪掉其他備份"


def test_restore_writes_a_snapshot_first(bdir):
    """按錯了要救得回來——這是選擇『完整取代』的前提。"""
    _seed(bdir)
    z = backup.create_backup()
    result = backup.restore_backup(z)
    snap = backup.BACKUP_DIR / result["snapshot"]
    assert snap.exists()
    assert result["snapshot"].startswith("backup-prerestore-")


def test_restore_keeps_session_secret_when_backup_lacks_it(bdir):
    """備份裡沒有的全域檔不該被刪:刪掉 .session_secret 只會讓所有人的 session
    失效,而且沒有任何東西補回來,是純粹的損失。"""
    _seed(bdir)
    z = backup.BACKUP_DIR / "backup-manual-20260101-000000.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("users.json", "[]")
        zf.writestr("users/u1/notes/n1.md", "x")
    backup.restore_backup(z)
    assert (bdir / ".session_secret").read_text(encoding="utf-8") == "s3cr3t"


def test_zip_slip_paths_are_never_extracted(bdir, tmp_path):
    """ZIP 內的路徑是完全不可信的外部輸入(這包可能是使用者上傳的)。
    絕對路徑、`..`、`.`、反斜線、磁碟機代號,任何一種漏掉都是任意檔案覆寫。
    ⚠ 判斷必須用原始的 "/" 切,不能用 Path().parts——pathlib 會把 "." 這一段
    直接吃掉,於是「有沒有 . 這一段」的檢查永遠不會命中。"""
    _seed(bdir)
    z = backup.BACKUP_DIR / "backup-manual-20260101-000000.zip"
    attacks = [
        "../../pwned.txt", "../pwned.txt", "/etc/passwd", "C:/windows/evil.txt",
        "users/../../escape.txt", "./users/u1/notes/dot.md",
        "users" + chr(92) + "u1" + chr(92) + "back.md",
    ]
    with zipfile.ZipFile(z, "w") as zf:
        for a in attacks:
            zf.writestr(a, "X")
        zf.writestr("users/u1/notes/ok.md", "legit")
    with zipfile.ZipFile(z) as zf:
        safe = backup._safe_members(zf)
    for a in attacks:
        assert a not in safe, f"zip slip 沒擋住:{a}"

    backup.restore_backup(z)
    assert (bdir / "users" / "u1" / "notes" / "ok.md").read_text(encoding="utf-8") == "legit"
    for leaked in (tmp_path / "pwned.txt", bdir / "pwned.txt", bdir / "escape.txt"):
        assert not leaked.exists()


def test_bad_archives_are_rejected_before_anything_is_touched(bdir):
    """先驗證再刪除。順序反過來就是刪完才發現 ZIP 是壞的,而那時原始資料已經沒了。
    上傳的非 zip 同理:拒絕且不留半成品。"""
    _seed(bdir)
    # 上傳非 zip → 拒絕,不留半成品
    with pytest.raises(ValueError):
        backup.save_uploaded(b"definitely not a zip")
    assert not list(backup.BACKUP_DIR.glob("*.zip"))
    # 壞 zip → 還原在動任何資料之前就被擋下
    bad = backup.BACKUP_DIR / "backup-manual-20260101-000000.zip"
    bad.write_bytes(b"this is not a zip at all")
    with pytest.raises(Exception):
        backup.restore_backup(bad)
    assert (bdir / "users" / "u1" / "notes" / "n1.md").exists(), "驗證失敗不該動到資料"
    # 內容全是不認識的成員 → inspect 拒絕
    z = backup.BACKUP_DIR / "backup-manual-20260101-000001.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("random/thing.txt", "X")
    with pytest.raises(ValueError):
        backup.inspect_backup(z)
    assert (bdir / "users" / "u1" / "notes" / "n1.md").exists()


# ── 檔名驗證 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,ok", [
    # 檔名來自網址,是不可信輸入——少了這關,`../../users.json` 就能被下載或刪除
    ("../../users.json", False), ("..\\users.json", False), ("/etc/passwd", False),
    ("backup-manual-x.zip", False), ("backup-evil-20260101-000000.zip", False),
    ("notes.md", False), ("", False), ("backup-auto-2026-01-01.zip", False),
    ("backup-auto-20260101-000000.zip", True),
    ("backup-manual-20260101-000000.zip", True),
    ("backup-prerestore-20260101-000000.zip", True),
    ("backup-manual-20260101-000000_2.zip", True),
])
def test_backup_name_validation(name, ok):
    assert bool(backup.valid_backup_name(name)) is ok


def test_backup_path_refuses_to_escape(bdir):
    assert backup.backup_path("../../users.json") is None
    assert backup.delete_backup("../../users.json") is False


# ── 自動備份的排程 ──────────────────────────────────────────────────────────

def test_auto_backup_due_is_derived_from_files_not_stored_state(bdir):
    """刻意不存「上次執行時間」:多一份狀態就多一種對不上的壞法(使用者手動刪光
    備份、狀態卻說剛備份過,於是再也不備份)。從檔案推算則是自我修復的。"""
    _seed(bdir)
    cfg = {"auto_enabled": True, "interval_days": 7, "keep": 10}
    assert backup.auto_backup_due({**cfg, "auto_enabled": False}) is False, "停用開關要生效"
    assert backup.auto_backup_due(cfg), "沒有任何備份就該到期"
    assert backup.run_auto_backup_if_due(cfg) is True
    assert backup.auto_backup_due(cfg) is False, "剛備份完就不再到期"
    assert backup.run_auto_backup_if_due(cfg) is False
    assert backup.last_auto_run() > 0
    for b in backup.list_backups():
        backup.delete_backup(b["name"])
    assert backup.last_auto_run() == 0
    assert backup.auto_backup_due(cfg) is True, "備份被刪光就該重新備份"


# ── 每日時刻門檻 at_time ────────────────────────────────────────────────────
# 語意是「不早於 HH:MM」而不是「準時在 HH:MM」——本站刻意沒有排程器,
# 只在啟動與 GET /api/auth/me 兩處評估(見 backup.auto_backup_due 的 docstring)。

def _cfg(**kw):
    return {"auto_enabled": True, "interval_days": 7, "keep": 10, **kw}


def _freeze_clock(monkeypatch, hh: int, mm: int):
    """把 backup.datetime.now() 固定在某個時刻。不看真實時鐘,不然測試會依
    「這一輪幾點跑」而時好時壞——那是最難查的那種 flaky。"""
    real = backup.datetime

    class _Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 8, 6, hh, mm)

    monkeypatch.setattr(backup, "datetime", _Frozen)


def test_broken_or_absent_at_time_never_blocks_backups(bdir):
    """兩個退回方向都不可以變成「永遠不備份」:
    - 既有呼叫端拿的是只有三個鍵的 dict。⚠ 用 cfg["at_time"] 會 KeyError,
      把整個備份機制打死——所以那裡一定要是 cfg.get()。
    - 設定壞掉時退回「沒設門檻」。變成「永遠不備份」會是無聲的失敗:
      UI 顯示自動備份已啟用,實際上一份都不會產生。
    設定端 _clean_backup 則把不合法的時刻收斂成空字串。"""
    _seed(bdir)
    assert backup.auto_backup_due({"auto_enabled": True, "interval_days": 7, "keep": 10})
    assert backup.auto_backup_due(_cfg(at_time="")) is True
    assert backup.auto_backup_due(_cfg(at_time="not-a-time")) is True

    from app import site_settings as ss
    assert ss._clean_backup({"at_time": "25:00"})["at_time"] == ""
    assert ss._clean_backup({"at_time": "3:5"})["at_time"] == ""
    assert ss._clean_backup({"at_time": None})["at_time"] == ""
    assert ss._clean_backup({"at_time": " 03:30 "})["at_time"] == "03:30"
    assert ss._clean_backup({})["at_time"] == ""


def test_at_time_gates_on_top_of_the_interval(bdir, monkeypatch):
    """時刻是「不早於」的門檻,疊在間隔之上:間隔到期但今天還沒到時刻 → 先不
    備份;時刻到了也不代表要備份——間隔(含多天)那一關仍然要過。"""
    from datetime import datetime as real_dt

    _seed(bdir)
    # 間隔已到期(還沒有任何備份),但今天還沒到指定時刻 → 先不備份
    _freeze_clock(monkeypatch, 2, 59)
    assert backup.auto_backup_due(_cfg(at_time="03:00")) is False
    _freeze_clock(monkeypatch, 3, 0)
    assert backup.auto_backup_due(_cfg(at_time="03:00")) is True
    _freeze_clock(monkeypatch, 9, 30)
    assert backup.auto_backup_due(_cfg(at_time="03:00")) is True
    # 時刻到了也不代表要備份——間隔那一關仍然要過
    cfg = _cfg(at_time="00:00")
    assert backup.run_auto_backup_if_due(cfg) is True
    assert backup.auto_backup_due(cfg) is False
    # 多天間隔:日曆天數還沒滿 interval_days 就不備份
    monkeypatch.setattr(backup, "last_auto_run",
                        lambda: real_dt(2026, 8, 5, 3, 0).timestamp())
    _freeze_clock(monkeypatch, 9, 0)      # 今天 2026-08-06,距上次才 1 天
    assert backup.auto_backup_due(_cfg(interval_days=7, at_time="03:00")) is False
    assert backup.auto_backup_due(_cfg(interval_days=1, at_time="03:00")) is True


def test_at_time_uses_calendar_days_so_the_hour_actually_takes_effect(bdir, monkeypatch):
    """⚠ 這支守的是「設了時刻卻永遠不生效」那個無聲的失敗。

    上一包在 23:50 跑完,at_time=03:00、interval=1。用 24 小時的秒數差比較的話,
    隔天 03:00 才過了 3 小時 10 分、遠不到一天 → 不到期;要等到隔天 23:50 才到期,
    而那時 03:00 早就過了。結果備份時間永遠停在最初那個偶然的時刻,設定形同虛設,
    而且完全不會報錯。設了時刻就改用**日曆天**比較,才會是使用者要的「每天那時候」。
    """
    from datetime import datetime as real_dt

    _seed(bdir)
    # 上一包:昨天 23:50
    yesterday_2350 = real_dt(2026, 8, 5, 23, 50).timestamp()
    monkeypatch.setattr(backup, "last_auto_run", lambda: yesterday_2350)
    cfg = _cfg(interval_days=1, at_time="03:00")

    _freeze_clock(monkeypatch, 2, 59)     # 今天 02:59 → 還沒到時刻
    assert backup.auto_backup_due(cfg) is False
    _freeze_clock(monkeypatch, 3, 0)      # 今天 03:00 → 到期(秒數差只有 3h10m)
    assert backup.auto_backup_due(cfg) is True


def test_purge_only_removes_old_autos(bdir):
    """保留份數只清自動備份、只清最舊的。手動與還原前快照是刻意留下的——尤其
    prerestore,它存在的唯一理由就是『還原按錯了要能救回來』。"""
    _seed(bdir)
    manual = backup.create_backup(backup.KIND_MANUAL)
    pre = backup.create_backup(backup.KIND_PRERESTORE)
    autos = []
    for _ in range(4):
        autos.append(backup.create_backup(backup.KIND_AUTO))
        time.sleep(0.01)
    removed = backup.purge_old(keep=2)
    assert removed == 2
    assert manual.exists() and pre.exists()
    survivors = {b["name"] for b in backup.list_backups()}
    assert autos[-1].name in survivors and autos[-2].name in survivors
    assert autos[0].name not in survivors


# ── 走 HTTP ─────────────────────────────────────────────────────────────────
# ⚠ 這一段**不能**用上面的 bdir fixture:那是 monkeypatch 模組常數,而 app 走的是
# 真正的 DATA_DIR。所以這裡只做非破壞性的操作,唯一一支還原測試還原的是「同一個
# 測試裡剛打的那包」——內容與現況相同,還原完等於原地踏步,不會抹掉共用目錄。

ADMIN_EMAIL, ADMIN_PW = "admin@example.com", "adminpass1"


@pytest.fixture
def admin_ctx(app_instance, monkeypatch):
    from fastapi.testclient import TestClient
    from app import site_settings as ss
    from app.users import save_users
    save_users([])
    monkeypatch.setenv("ALLOWED_EMAILS", ADMIN_EMAIL)
    ss.SITE_SETTINGS_PATH.unlink(missing_ok=True)
    ss.ensure_site_settings()
    c = TestClient(app_instance)
    r = c.post("/api/auth/register", json={"email": ADMIN_EMAIL, "password": ADMIN_PW})
    assert r.status_code == 200, r.text
    return {"client": c, "id": r.json()["id"]}


def test_api_create_list_delete_settings_and_upload(admin_ctx):
    c = admin_ctx["client"]
    made = c.post("/api/admin/backups")
    assert made.status_code == 200, made.text
    name = made.json()["name"]

    listed = c.get("/api/admin/backups").json()
    assert any(b["name"] == name for b in listed["backups"])
    assert listed["settings"]["auto_enabled"] is True, "自動備份預設就該是開的"
    assert listed["settings"]["interval_days"] == 7, "預設七天"

    dl = c.get(f"/api/admin/backups/{name}/download")
    assert dl.status_code == 200 and dl.content[:2] == b"PK"

    assert c.get(f"/api/admin/backups/{name}/inspect").status_code == 200
    assert c.delete(f"/api/admin/backups/{name}").status_code == 200
    assert c.get(f"/api/admin/backups/{name}/download").status_code == 404

    # 設定夾住上下界:保留 0 份等於備份完立刻刪掉——UI 說成功、實際什麼都沒留
    r = c.put("/api/admin/settings/backup",
              json={"auto_enabled": True, "interval_days": 0, "keep": 0})
    assert r.status_code == 200
    b = r.json()["backup"]
    assert b["interval_days"] >= 1 and b["keep"] >= 1

    # 上傳非 zip → 400
    r = c.post("/api/admin/backups/upload",
               files={"file": ("evil.zip", b"not a zip", "application/zip")})
    assert r.status_code == 400


def test_api_rejects_path_traversal_in_the_name(admin_ctx):
    """檔名來自網址。少了 valid_backup_name(),這幾個請求就能下載或刪掉
    data/ 底下的任何檔案。"""
    c = admin_ctx["client"]
    for bad in ("..%2F..%2Fusers.json", "backup-evil-20260101-000000.zip", "users.json"):
        assert c.get(f"/api/admin/backups/{bad}/download").status_code in (404, 400)
        assert c.delete(f"/api/admin/backups/{bad}").status_code in (404, 400)
    from app.config import DATA_DIR
    assert (DATA_DIR / "users.json").exists(), "users.json 不可以被刪掉"


def test_api_restore_roundtrip_and_reindex(admin_ctx):
    """還原同一個測試裡剛打的那包:內容與現況相同,所以是安全的原地還原。
    重點在驗證端點有跑完整條路——含還原後重建索引(index.db 不進備份,
    不重建的話還原完第一次搜尋就 500)。"""
    c = admin_ctx["client"]
    name = c.post("/api/admin/backups").json()["name"]
    r = c.post(f"/api/admin/backups/{name}/restore")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["snapshot"].startswith("backup-prerestore-")
    assert body["reindexed"] >= 1
    assert c.get("/api/search").status_code == 200, "還原後搜尋必須還能用"


def test_backup_api_requires_admin_and_login(admin_ctx, register_user, client):
    assert client.get("/api/admin/backups").status_code == 401, "未登入要 401"
    other = register_user()
    for method, path in (("get", "/api/admin/backups"),
                         ("post", "/api/admin/backups"),
                         ("put", "/api/admin/settings/backup")):
        r = getattr(other["client"], method)(path, **({"json": {}} if method == "put" else {}))
        assert r.status_code == 403, f"{method} {path} 應該擋下非 admin"

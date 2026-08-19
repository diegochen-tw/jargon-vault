"""內容健康度檢查(app/health.py)。

這一層要守的核心性質是「**報得準,而且不會嚇人**」:

  - 報得準:每一類問題只在真的成立時才出現。誤報的代價特別高——使用者看到
    「孤兒檔」會想去刪,而刪錯就是資料永久消失。
  - 不嚇人:乾淨的庫要真的回零。任何一類只要有偽陽性,每次檢查都會有一堆
    紅字,使用者很快就學會忽略整個功能,那真正壞掉的那一筆就永遠不會被看到。

最重要的兩支是 test_history_referenced_files_are_not_orphans(版本回復要回復得
出來,舊路徑不是垃圾)與 test_counts_stay_exact_when_items_are_truncated
(結論不能被截斷截歪)。
"""
import time

import pytest

from app import health, service
from app.config import HEALTH_MAX_PER_KIND, LARGE_ASSET_BYTES
from app.indexer import rebuild_index
from app.storage import read_note_file


# ⚠ 用哨符而不是 `tags or [...]`:測試要能表達「**刻意**沒有標籤」,
# 而 `[] or ["標籤"]` 會把那個意圖悄悄換成預設值(第一版就是這樣寫錯的,
# 症狀是 no_tags 那兩支測試找不到分組)。
_DEFAULT = object()


def _note(nid, name, description="", tags=_DEFAULT, attachments=None, history=None):
    now = time.time()
    return {"id": nid, "name": name, "description": description,
            "template": "jargon-default", "fields": {},
            "tags": ["標籤"] if tags is _DEFAULT else tags,
            "attachments": attachments or [], "created": now, "updated": now,
            "history": history or []}


def _att(path, name=None):
    return {"name": name or path.split("/")[-1], "path": path, "description": ""}


@pytest.fixture
def vault(paths):
    rebuild_index(paths)

    def add(nid, name, **kw):
        n = _note(nid, name, **kw)
        service.persist_note(paths, n)
        return n

    def put_file(rel, size=16):
        """在 notes_dir 底下實際放一個檔(rel 形如 assets/<nid>/<file>)。"""
        f = paths.notes_dir / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x" * size)
        return f

    return paths, add, put_file


def _group(report, kind):
    return next((g for g in report["groups"] if g["kind"] == kind), None)


def _kinds(report):
    return {g["kind"] for g in report["groups"]}


# ── 乾淨的庫 ────────────────────────────────────────────────────────

def test_a_healthy_vault_reports_nothing(vault):
    paths, add, put_file = vault
    put_file("assets/n1/pic.png")
    add("n1", "正常名詞", description="說明文字", tags=["系統"],
        attachments=[_att("assets/n1/pic.png")])

    report = health.scan(paths)
    # 回零也涵蓋 missing_template:掛著內建樣板的名詞絕不能被報成孤兒
    assert report["groups"] == []
    assert report["notes"] == 1
    # 掃過的量要如實回報:空列表在「庫是空的」時也會出現,不講掃了什麼的話
    # 使用者沒辦法分辨「真的很健康」與「根本沒掃到東西」。
    assert report["assets"] == 1

    # 沒東西可清就不該留下一包空 ZIP 讓人以為刪過東西
    r = health.cleanup(paths, list(health.CLEANABLE))
    assert r["name"] == ""
    assert health.list_cleanups(paths) == []


# ── 壞掉的東西(error)──────────────────────────────────────────────

def test_broken_things_are_reported_and_valid_ones_are_not(vault):
    """缺附件/破內嵌圖/讀不出來的 .md 都要報;存在的內嵌圖(帶不帶開頭斜線
    兩種寫法)與外連/data: 圖絕不列入——http(s):// 外連圖不驗:那會讓「掃描」
    變成一連串對外的網路請求,而且對方站台掛掉也不是這個庫的健康問題。"""
    paths, add, put_file = vault
    put_file("assets/n1/ok.webp")
    add("n1", "破圖", description=(
        "![a](/assets/n1/ok.webp) ![b](assets/n1/ok.webp) "
        "![x](/assets/n1/nope.webp) "
        "![y](https://example.com/nope.png) ![z](data:image/png;base64,AAAA)"),
        attachments=[_att("assets/n1/gone.png"), _att("assets/n1/ok.webp")])
    # read_note_file 把所有例外吞成 None(懶遷移要能容忍怪檔案),所以「讀不出來」
    # 在其他路徑上都是靜默跳過——健康度檢查是唯一會講出來的地方。
    (paths.notes_dir / "broken.md").write_text("這不是 frontmatter", encoding="utf-8")

    report = health.scan(paths)
    g = _group(report, "missing_asset")
    assert g["severity"] == "error" and g["count"] == 1
    assert g["items"][0]["note_id"] == "n1"
    g = _group(report, "missing_embed")
    assert g["severity"] == "error" and g["count"] == 1        # 只有真的缺的那張
    assert g["items"][0]["detail"] == "assets/n1/nope.webp"
    g = _group(report, "unreadable")
    assert g["severity"] == "error" and g["count"] == 1
    # 壞檔不算進掃過的筆數(它根本沒被解析成一筆名詞)
    assert report["notes"] == 1


# ── 浪費空間的東西(warn)與分組排序 ─────────────────────────────────

def test_large_assets_and_orphans_are_reported_worst_first(vault):
    paths, add, put_file = vault
    put_file("assets/n1/huge.bin", LARGE_ASSET_BYTES + 1)
    put_file("assets/n9/orphan.bin")
    add("n1", "什麼都不對", description="", tags=[],
        attachments=[_att("assets/n1/huge.bin"), _att("assets/n1/gone.png")])

    report = health.scan(paths)
    g = _group(report, "large_asset")
    assert g["count"] == 1
    assert g["items"][0]["note_id"] == "n1"
    assert g["items"][0]["size"] == LARGE_ASSET_BYTES + 1
    g = _group(report, "orphan_file")
    assert g["severity"] == "warn" and g["count"] == 1
    # 孤兒檔不屬於任何名詞,note_id 必須是空的——前端據此決定不畫「跳過去」的按鈕
    assert g["items"][0]["note_id"] == ""
    # 壞掉的一定排在待整理的前面:真正壞掉的那一筆不能被一堆「沒關鍵字」淹掉
    assert {"missing_asset", "no_tags", "empty_desc"} <= _kinds(report)
    sevs = [grp["severity"] for grp in report["groups"]]
    assert sevs == sorted(sevs, key=["error", "warn", "info"].index)


def test_history_referenced_files_are_not_orphans(vault):
    """**最重要的那支之一**:歷史快照引用的舊檔不是孤兒。

    批次圖片壓縮會產生新路徑、把舊路徑留在最多 3 個版本快照裡(service.py 的
    「寧可留下少數孤兒檔」)。把它們報成孤兒,使用者照著刪掉之後,「版本回復」
    就會回復出一堆破圖——健康度檢查反而製造了它要防的那種資料遺失。
    """
    paths, add, put_file = vault
    put_file("assets/n1/new.webp")
    put_file("assets/n1/old.png")       # 只有歷史版本還指著它
    put_file("assets/n1/old-inline.png")  # 只有歷史版本的說明欄內嵌它
    add("n1", "壓縮過的名詞",
        description="現在的說明",
        attachments=[_att("assets/n1/new.webp")],
        history=[{"name": "壓縮過的名詞", "template": "jargon-default", "fields": {},
                  "tags": ["標籤"], "attachments": [_att("assets/n1/old.png")],
                  "description": "舊說明 ![x](/assets/n1/old-inline.png)",
                  "updated": time.time()}])

    assert "orphan_file" not in _kinds(health.scan(paths))


def test_broken_wikilink_is_reported_until_the_target_is_created(vault):
    paths, add, _ = vault
    add("n1", "MES", description="要先懂 [[SFIS]] 跟 [[根本沒建的詞]]")
    add("n2", "SFIS", description="製造執行系統")

    g = _group(health.scan(paths), "broken_link")
    assert g["count"] == 1
    assert g["items"][0]["note_id"] == "n1"
    assert g["items"][0]["detail"] == "根本沒建的詞"

    # 「目標還不存在也寫得下去」是刻意的設計(app/links.py),補建之後就不該再報
    add("n3", "根本沒建的詞", description="現在有了")
    assert "broken_link" not in _kinds(health.scan(paths))


# ── 待整理的內容(info)─────────────────────────────────────────────

def test_notes_without_tags_or_description_are_reported(vault):
    paths, add, _ = vault
    add("n1", "沒關鍵字", description="有說明", tags=[])
    add("n2", "沒說明", description="   ", tags=["有標籤"])

    report = health.scan(paths)
    assert _group(report, "no_tags")["items"][0]["note_id"] == "n1"
    assert _group(report, "no_tags")["severity"] == "info"
    assert _group(report, "empty_desc")["items"][0]["note_id"] == "n2"


# ── template 指向不存在的樣板(missing_template)─────────────────────

def test_missing_template_is_reported_but_never_cleanable(vault):
    """外掛解除安裝(或樣板誤刪)後的孤兒名詞要被列出來,detail 帶那個
    不存在的樣板 id——使用者才知道該重裝哪個外掛或轉去哪。
    但自動改 template 是在改內容,「該轉去哪」只有使用者知道——
    絕不可以進 CLEANABLE(cleanup 對它必須是 no-op)。"""
    paths, add, put_file = vault
    n = _note("n1", "孤兒名詞", description="內容")
    n["template"] = "gone-plugin-tpl"
    service.persist_note(paths, n)
    add("n2", "正常名詞", description="內容")  # jargon-default,不該被報(偽陽性防護)

    report = health.scan(paths)
    g = _group(report, "missing_template")
    assert g is not None and g["severity"] == "warn"
    assert g["count"] == 1
    assert g["items"][0]["note_id"] == "n1"
    assert g["items"][0]["detail"] == "gone-plugin-tpl"
    assert "missing_template" not in health.CLEANABLE


# ── 截斷 ────────────────────────────────────────────────────────────

def test_counts_stay_exact_when_items_are_truncated(vault):
    """**最重要的那支**:明細可以截斷,結論不行。

    dedup.py 那條「撈全表、不設上限」守的是「『沒有重複』這種**結論**不能是
    被截斷截出來的」。這裡沿用同一個精神:items 為了不讓回應爆掉而截斷,但
    count 一律是完整掃完之後算出來的精確值。
    """
    paths, add, _ = vault
    over = HEALTH_MAX_PER_KIND + 7
    for i in range(over):
        add(f"n{i:03d}", f"無標籤 {i}", description="有說明", tags=[])

    g = _group(health.scan(paths), "no_tags")
    assert g["count"] == over
    assert len(g["items"]) == HEALTH_MAX_PER_KIND


# ── 清理:先打包成 ZIP,再刪 ────────────────────────────────────────
#
# 這一組守的是「刪錯了救得回來」。孤兒檔的判斷天生有偽陽性(正在新建、還沒
# 儲存的名詞,它的附件看起來就是孤兒),所以整個功能能不能做,取決於誤刪之後
# 救不救得回來——ZIP 就是那個救。

@pytest.fixture
def dirty(vault):
    """一個同時有三類可清問題、外加「不可以被刪」的對照組的庫。"""
    paths, add, put_file = vault
    put_file("assets/n1/good.png", 50)          # 好的附件,不可以被碰
    put_file("assets/n2/orphan.bin", 1234)      # 孤兒檔,要刪
    put_file("assets/n3/old.png", 77)           # 只有歷史版本引用,**不可以刪**
    put_file("assets/n3/new.png", 77)
    add("n1", "有破附件", description="說明 ![x](/assets/n1/missing.png) 尾巴",
        attachments=[_att("assets/n1/gone.png"), _att("assets/n1/good.png")])
    add("n3", "壓縮過", description="現在",
        attachments=[_att("assets/n3/new.png")],
        history=[{"name": "壓縮過", "template": "jargon-default", "fields": {},
                  "tags": ["標籤"], "attachments": [_att("assets/n3/old.png")],
                  "description": "舊", "updated": time.time()}])
    return paths


def test_cleanup_packs_a_zip_before_deleting_anything(dirty):
    import zipfile
    r = health.cleanup(dirty, list(health.CLEANABLE))
    z = dirty.cleanup_dir / r["name"]
    assert z.is_file()
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        # 孤兒檔本體 + 改動前的 .md 全文 + 清單
        assert "orphan-files/assets/n2/orphan.bin" in names
        assert "notes-before/n1.md" in names
        assert health.MANIFEST_NAME in names
        # 存進去的必須是**改動前**的內容,否則這包救不回任何東西
        before = zf.read("notes-before/n1.md").decode("utf-8")
        assert "assets/n1/gone.png" in before
        assert "assets/n1/missing.png" in before


def test_cleanup_removes_orphans_and_refs_without_bumping_updated(dirty):
    """修復不是編輯:bump updated 會打亂列表排序,寫歷史會沖光真正的編輯歷史
    (HISTORY_LIMIT 只有 3)。復原路徑是那包 ZIP,不是歷史版本。"""
    before = read_note_file(dirty.notes_dir / "n1.md")
    r = health.cleanup(dirty, list(health.CLEANABLE))
    assert r["removed_files"] == 1
    assert r["removed_refs"] == 2       # 一個壞附件 + 一個壞內嵌
    assert r["bytes"] == 1234
    assert not (dirty.notes_dir / "assets/n2/orphan.bin").exists()

    note = read_note_file(dirty.notes_dir / "n1.md")
    assert [a["path"] for a in note["attachments"]] == ["assets/n1/good.png"]
    assert "missing.png" not in note["description"]
    assert "尾巴" in note["description"]   # 只拿掉圖片語法,不動旁邊的字
    assert note["updated"] == before["updated"]
    assert len(note["history"]) == len(before["history"])

    assert _kinds(health.scan(dirty)) == set()


def test_cleanup_never_touches_files_a_history_version_still_needs(dirty):
    """**最重要的那支**:歷史快照引用的舊檔絕不能被清掉。

    清掉的話「版本回復」會回復出一堆破圖——而清理功能的存在理由正是修破圖。
    """
    health.cleanup(dirty, list(health.CLEANABLE))
    assert (dirty.notes_dir / "assets/n3/old.png").exists()
    assert (dirty.notes_dir / "assets/n1/good.png").exists()


def test_cleanup_only_touches_requested_kinds_and_ignores_unknown(dirty):
    # 不認得的類別一律忽略——多送一個未知類別是相容性問題,不是攻擊。
    # ⚠ 更要緊的是它**不能**被當成「清全部」。
    r = health.cleanup(dirty, ["no_tags", "broken_link", "../../etc/passwd"])
    assert r["name"] == ""
    assert r["removed_files"] == 0
    assert (dirty.notes_dir / "assets/n2/orphan.bin").exists()

    # 只清被要求的那一類,破圖那兩類沒被要求就原封不動
    r = health.cleanup(dirty, ["orphan_file"])
    assert r["removed_files"] == 1
    assert r["removed_refs"] == 0
    note = read_note_file(dirty.notes_dir / "n1.md")
    assert len(note["attachments"]) == 2
    assert "missing.png" in note["description"]


def test_cleanup_name_whitelist_rejects_traversal():
    assert health.valid_cleanup_name("health-cleanup-20260805-011306.zip")
    assert health.valid_cleanup_name("health-cleanup-20260805-011306_1.zip")
    for bad in ("../../users.json", "health-cleanup-x.zip", "backup-auto-20260805-010101.zip",
                "health-cleanup-20260805-011306.zip/../x", "", "health-cleanup-.zip"):
        assert not health.valid_cleanup_name(bad), bad


def test_cleanup_archives_stay_out_of_site_backups(dirty):
    """存證 ZIP 不進整站備份。

    它跟 data/backups 同一類——被刪掉那些東西的**副本**,不是真相。不排除的話,
    使用者清掉的空間會在下一次備份時原封不動被吃回去,而且往後每一包都帶著。
    """
    from app.backup import _should_skip
    from pathlib import Path

    r = health.cleanup(dirty, list(health.CLEANABLE))
    assert r["name"]
    assert _should_skip(Path("users") / dirty.vault_id / "cleanup" / r["name"])
    # ⚠ 只擋第三層。"cleanup" 是合法的 note id,那筆名詞的附件不可以被誤殺。
    assert not _should_skip(Path("users") / "u1" / "notes" / "assets" / "cleanup" / "pic.png")
    assert not _should_skip(Path("users") / "u1" / "notes" / "n1.md")


# ── 走 HTTP ─────────────────────────────────────────────────────────

def test_endpoints_require_login(client):
    assert client.get("/api/content-health").status_code == 401
    assert client.post("/api/content-health/cleanup", json={"kinds": []}).status_code == 401
    assert client.get("/api/content-health/cleanup").status_code == 401
    assert client.get("/api/content-health/cleanup/x.zip/download").status_code == 401


def test_report_and_cleanup_round_trip_over_http(register_user):
    u = register_user()
    c, paths = u["client"], u["paths"]
    r = c.post("/api/notes", json={"name": "沒關鍵字的", "description": "",
                                   "template": "jargon-default", "fields": {}, "tags": []})
    assert r.status_code == 200, r.text
    (paths.assets_dir / "zz").mkdir(parents=True, exist_ok=True)
    (paths.assets_dir / "zz" / "orphan.bin").write_bytes(b"x" * 99)

    body = c.get("/api/content-health").json()
    assert body["notes"] == 1
    assert {"no_tags", "empty_desc", "orphan_file"} <= {g["kind"] for g in body["groups"]}

    r = c.post("/api/content-health/cleanup", json={"kinds": ["orphan_file"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["removed_files"] == 1
    assert not (paths.assets_dir / "zz" / "orphan.bin").exists()

    listed = c.get("/api/content-health/cleanup").json()["items"]
    assert [i["name"] for i in listed] == [body["name"]]

    dl = c.get(f"/api/content-health/cleanup/{body['name']}/download")
    assert dl.status_code == 200
    assert dl.content[:2] == b"PK"      # 真的是一包 ZIP
    # 下載路由先過檔名白名單:路徑穿越與別家的 ZIP 一律擋下
    assert c.get("/api/content-health/cleanup/..%2F..%2Fusers.json/download"
                 ).status_code in (400, 404)
    assert c.get("/api/content-health/cleanup/backup-auto-20260101-000000.zip/download"
                 ).status_code == 400


def test_scan_and_cleanup_never_cross_users(register_user):
    """A 的問題絕不出現在 B 的報告裡;A 按下清理,也絕不能碰到 B 的孤兒檔。"""
    a, b = register_user(), register_user()
    for u in (a, b):
        (u["paths"].assets_dir / "zz").mkdir(parents=True, exist_ok=True)
        (u["paths"].assets_dir / "zz" / "orphan.bin").write_bytes(b"x" * 10)

    b_report = b["client"].get("/api/content-health").json()
    assert _group(b_report, "orphan_file")["count"] == 1   # 只有 B 自己那一個

    a["client"].post("/api/content-health/cleanup", json={"kinds": ["orphan_file"]})
    assert not (a["paths"].assets_dir / "zz" / "orphan.bin").exists()
    assert (b["paths"].assets_dir / "zz" / "orphan.bin").exists()

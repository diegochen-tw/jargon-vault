"""
索引層:SQLite FTS5(trigram)搜尋索引。

索引是「可拋棄」的:啟動時從 data/notes/*.md 全量重建,
損毀或 schema 改版時直接刪掉 index.db 重啟即可,不存在遷移問題。

五張表:
  - notes     名詞本體(row_to_note 從這裡還原整筆),tags 是 JSON 字串;
              name_key 是名稱的正規化比對鍵(sanitize.norm_key),給 `[[名詞]]`
              連結解析與重複偵測用,不進 row_to_note(它是索引自己的欄)。
              ⚠ **不含 marked / srs_box / srs_due**——那是「我跟名詞的關係」,
              不是名詞的屬性,住在 progress 表(見下)。
  - progress  個人狀態的投影(書籤 + 複習排程),鍵是 (vault_id, note_id)。
              真相在 `<使用者目錄>/progress.json`(app/progress.py),這張表
              啟動時從那裡全量重建,所以「SQLite 一律可拋棄」這條規則不變。
  - fts       FTS5 trigram 全文索引
  - note_tags 標籤的正規化投影(一筆名詞一個標籤一列),只給 search.py 的
              標籤 AND/OR 篩選下推到 SQL 用。notes.tags 那個 JSON 欄仍是
              還原名詞與「關鍵字比對到標籤文字」的來源,兩者並存不衝突。
  - note_links `[[名詞]]` 連結的反向索引(一條連結一列):src_id 是寫下連結的
              名詞,target_key 是目標名稱的 norm_key(目標不必存在)。反向連結
              就是對這張表的一個 SELECT(見 search.py:backlinks_of)。

寫入順序約定:先寫檔案(storage),再更新索引(index_upsert),
索引落後於檔案最多一次重啟就會追平。
"""
import json
import sqlite3

from . import progress as progress_store
from .links import link_targets
from .paths import VaultPaths
from .sanitize import norm_key
from .storage import read_note_file

# 個人狀態的投影欄:notes 表沒有這三欄,一律由 progress 表 LEFT JOIN 補上。
# srs_due 的 COALESCE 就是 srs.effective_due() 的 SQL 版本——沒有進度記錄
# (從沒複習過)就退回 updated,於是「久未觸碰的優先」仍是 ORDER BY 的自然結果。
PROGRESS_COLS = ("COALESCE(p.marked,0) AS marked, "
                 "p.srs_box AS srs_box, "
                 "COALESCE(p.srs_due, n.updated) AS srs_due")


def db(paths: VaultPaths) -> sqlite3.Connection:
    conn = sqlite3.connect(paths.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def rebuild_index(paths: VaultPaths) -> None:
    """從 .md 檔全量重建索引。

    檔案毀損(斷電寫到一半、被別的程式覆寫)時直接刪掉重來:索引本來就是
    可拋棄快取,真相在 .md 檔裡,砍掉重建不會損失任何資料。⚠ 沒有這一層的話,
    單一使用者的壞檔會讓 create_app() 的初始化迴圈整個拋例外——**全站所有人
    都啟動不了**,而且因為連啟動都失敗,也沒機會看出是誰的檔壞掉。
    """
    try:
        conn = db(paths)
        conn.execute("SELECT 1").fetchone()
    except sqlite3.DatabaseError:
        try:
            conn.close()
        except (sqlite3.Error, NameError, UnboundLocalError):
            pass
        paths.db_path.unlink(missing_ok=True)
        conn = db(paths)
    conn.executescript(
        """
        DROP TABLE IF EXISTS notes;
        DROP TABLE IF EXISTS fts;
        DROP TABLE IF EXISTS note_tags;
        DROP TABLE IF EXISTS note_links;
        DROP TABLE IF EXISTS progress;
        CREATE TABLE notes(
            id TEXT PRIMARY KEY, name TEXT, name_key TEXT, description TEXT,
            template TEXT, fields TEXT, tags TEXT,
            attachments TEXT, created REAL, updated REAL
        );
        CREATE VIRTUAL TABLE fts USING fts5(
            id UNINDEXED, name, description, fields_text, tags,
            tokenize='trigram'
        );
        CREATE TABLE note_tags(note_id TEXT, tag TEXT);
        CREATE TABLE note_links(src_id TEXT, target_key TEXT, target_name TEXT);
        -- 標籤篩選(search.py 的 IN 子查詢)
        CREATE INDEX idx_note_tags_tag ON note_tags(tag);
        CREATE INDEX idx_note_tags_note ON note_tags(note_id);
        -- 反向連結(target_key)與「這筆指向誰」(src_id)兩個方向都會查
        CREATE INDEX idx_note_links_target ON note_links(target_key);
        CREATE INDEX idx_note_links_src ON note_links(src_id);
        -- 連結解析與重複偵測都是拿 name_key 找名詞
        CREATE INDEX idx_notes_name_key ON notes(name_key);
        -- 列表排序:空查詢+無篩選時 ORDER BY … LIMIT/OFFSET 直接吃索引,不必排全表
        CREATE INDEX idx_notes_updated ON notes(updated);
        CREATE INDEX idx_notes_created ON notes(created);
        """
    )
    notes = []
    for p in sorted(paths.notes_dir.glob("*.md")):
        note = read_note_file(p)
        if note:
            notes.append(note)
            index_upsert(conn, note)
    _rebuild_progress(conn, paths, notes)
    conn.commit()
    conn.close()


def _rebuild_progress(conn: sqlite3.Connection, paths: VaultPaths, notes: list[dict]) -> None:
    """把 progress.json 投影成一張可 JOIN 的表。

    順便做舊 `.md` 的一次性遷移:frontmatter 裡還帶著 marked/srs 的名詞,
    把值收進 progress.json(**只補不覆蓋**,進度檔才是新的真相)。
    ⚠ 刻意不改寫 `.md`——本專案對舊格式一律懶遷移,全量改寫幾百個檔只為了
    拿掉三個 key,風險遠大於好處。
    """
    conn.executescript(
        """
        CREATE TABLE progress(
            vault_id TEXT NOT NULL, note_id TEXT NOT NULL,
            marked INTEGER NOT NULL DEFAULT 0, srs_box INTEGER, srs_due REAL,
            PRIMARY KEY (vault_id, note_id)
        );
        -- SRS 抽卡的排序軸(見 search.py 的 due_notes)。⚠ ORDER BY 用的是
        -- COALESCE(p.srs_due, n.updated),那是運算式、吃不到這個索引;
        -- 它服務的是「先用 vault_id 收斂到這個庫」那一半。個人量級(誠實的
        -- 天花板約 1 萬筆)掃描+排序是毫秒級,跟「暴力比對不引入 numpy」同一套取捨。
        CREATE INDEX idx_progress_due ON progress(vault_id, srs_due);
        """
    )
    progress_store.seed_from_notes(paths, paths.vault_id, notes)
    _gc_progress(paths, notes)
    rows = progress_store.entries_for(paths, paths.vault_id)
    conn.executemany(
        "INSERT INTO progress(vault_id,note_id,marked,srs_box,srs_due) VALUES(?,?,?,?,?)",
        [(paths.vault_id, nid, 1 if e["marked"] else 0, e["srs_box"], e["srs_due"])
         for nid, e in rows.items()],
    )


def _gc_progress(paths: VaultPaths, notes: list[dict]) -> None:
    """清掉「名詞已經不存在了」的孤兒進度。

    為什麼用回收式 GC 而不是在每個刪除路徑上各刪一次:回收桶裡的名詞是**可以
    還原**的,還原後複習進度該跟著回來,所以刪除當下不能清;而回收桶的永久刪除
    有三條路(單筆永久刪除、清空回收桶、過期自動清理),要在每一條上都記得清一次,
    漏掉任何一條都不會有錯誤訊息。改成啟動時對照「notes/ ∪ trash/」一次算清楚,
    是自我修復的——同 backup.py 那條「多一份狀態就多一種對不上的壞法」。

    ⚠ 一定要把 trash/ 算進來。只對照 notes/ 的話,任何一次重啟都會把回收桶裡
    那些名詞的複習進度清掉,還原回來就變成從沒複習過。
    """
    alive = {n["id"] for n in notes}
    if paths.trash_dir.exists():
        alive |= {p.stem for p in paths.trash_dir.glob("*.md")}
    entries = progress_store.entries_for(paths, paths.vault_id)
    orphans = [nid for nid in entries if nid not in alive]
    if not orphans:
        return
    data = progress_store.load_progress(paths)
    per_vault = data.get(paths.vault_id, {})
    for nid in orphans:
        per_vault.pop(nid, None)
    if not per_vault:
        data.pop(paths.vault_id, None)
    progress_store.save_progress(paths, data)


def progress_upsert(conn: sqlite3.Connection, vault_id: str, note_id: str, entry: dict) -> None:
    """把單筆進度寫進投影表(真相已經由 app/progress.py 落盤了)。

    entry 回到「什麼都沒有」時就刪列,不留一筆全預設值的殼——那會讓
    srs_due 被物化成具體值,那筆名詞就永遠卡在複習佇列最前面(見 progress.py)。
    """
    conn.execute("DELETE FROM progress WHERE vault_id=? AND note_id=?", (vault_id, note_id))
    if entry.get("marked") or entry.get("srs_box") is not None:
        conn.execute(
            "INSERT INTO progress(vault_id,note_id,marked,srs_box,srs_due) VALUES(?,?,?,?,?)",
            (vault_id, note_id, 1 if entry.get("marked") else 0,
             entry.get("srs_box"), entry.get("srs_due")))


def index_upsert(conn: sqlite3.Connection, n: dict) -> None:
    tags_text = " ".join(n["tags"])
    fields_text = " ".join(v for v in n["fields"].values() if v)  # 樣板欄位值合併進全文搜尋
    conn.execute("DELETE FROM notes WHERE id=?", (n["id"],))
    conn.execute("DELETE FROM fts WHERE id=?", (n["id"],))
    conn.execute("DELETE FROM note_tags WHERE note_id=?", (n["id"],))
    conn.execute("DELETE FROM note_links WHERE src_id=?", (n["id"],))
    # 位置參數:順序必須跟 rebuild_index 的建表欄序一字不差(name_key 緊接在 name 之後)。
    # ⚠ 這裡**不碰 marked/srs**:它們是個人狀態、住在 progress 表,而且寫入名詞內容
    # (編輯、匯入、還原…)絕不該動到任何人的書籤與複習進度。
    conn.execute(
        "INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?,?)",
        (n["id"], n["name"], norm_key(n["name"]), n["description"], n["template"],
         json.dumps(n["fields"], ensure_ascii=False),
         json.dumps(n["tags"], ensure_ascii=False),
         json.dumps(n["attachments"], ensure_ascii=False),
         n["created"], n["updated"]),
    )
    conn.execute(
        "INSERT INTO fts(id,name,description,fields_text,tags) VALUES(?,?,?,?,?)",
        (n["id"], n["name"], n["description"], fields_text, tags_text),
    )
    conn.executemany(
        "INSERT INTO note_tags(note_id,tag) VALUES(?,?)",
        [(n["id"], t) for t in n["tags"]],
    )
    # `[[名詞]]` 連結:存目標「名稱」的 norm_key 而不是 id——目標可以還不存在,
    # 之後那筆名詞一建立,反向連結自然就接上了(見 app/links.py 的說明)。
    conn.executemany(
        "INSERT INTO note_links(src_id,target_key,target_name) VALUES(?,?,?)",
        [(n["id"], norm_key(name), name) for name in link_targets(n)],
    )


def index_delete(conn: sqlite3.Connection, nid: str) -> None:
    """把名詞從索引移除。

    ⚠ 刻意**不刪 progress 的列**:一般刪除是搬進回收桶(可還原),還原之後
    複習進度理應跟著回來。永久刪除才由呼叫端另外呼叫 progress.drop_note()。
    """
    conn.execute("DELETE FROM notes WHERE id=?", (nid,))
    conn.execute("DELETE FROM fts WHERE id=?", (nid,))
    conn.execute("DELETE FROM note_tags WHERE note_id=?", (nid,))
    conn.execute("DELETE FROM note_links WHERE src_id=?", (nid,))


def row_to_note(r: sqlite3.Row) -> dict:
    """索引列 → 名詞 dict。

    marked/srs_box/srs_due 來自 progress 表的 LEFT JOIN(欄位別名見 PROGRESS_COLS),
    沒 JOIN 進來時退回「沒有進度」的預設值——`notes_by_ids` 這種只認 id 的查詢
    走的就是這條路。回傳形狀與外移前完全一致,所以 API 與前端一行都不用改。
    """
    keys = r.keys()
    marked = bool(r["marked"]) if "marked" in keys else False
    box = r["srs_box"] if "srs_box" in keys else None
    due = r["srs_due"] if "srs_due" in keys else r["updated"]
    return {
        "id": r["id"], "name": r["name"], "description": r["description"],
        "template": r["template"], "fields": json.loads(r["fields"]),
        "tags": json.loads(r["tags"]),
        "attachments": json.loads(r["attachments"]),
        "marked": marked,
        "srs_box": None if box is None or box < 0 else box,
        "srs_due": due if due is not None else r["updated"],
        "created": r["created"], "updated": r["updated"],
    }

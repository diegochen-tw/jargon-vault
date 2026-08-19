"""
服務層:跨檔案層與索引層的複合操作。

routers 對名詞的任何寫入都走這裡,保證「先寫檔案、再更新索引」的順序約定
只實作在一個地方。所有函式第一個參數都收 paths: VaultPaths,操作僅限於
該使用者自己的資料目錄。
"""
import json
import shutil
import time
import uuid

from fastapi import HTTPException

from . import links, progress, trash
from .config import HISTORY_LIMIT, UNTITLED_RE, valid_id
from .demo import orphan_demo_tags, purge_vault
from .indexer import db, index_delete, index_upsert, progress_upsert
from .paths import VaultPaths, user_paths
from .sanitize import clean_tags, norm_key
from .search import notes_linking_to
from .storage import note_path, read_note_file, snapshot_of, write_note_file
from .tags import load_tags, merge_new_tags, register_new_tags, save_tags
from .users import delete_user_record


def category_to_tag_group(category: str) -> tuple[str, str]:
    """
    舊「分類」概念的轉換規則(分類已由標籤群組取代):
    路徑末層變標籤、第一層變該標籤的群組;單層分類變未分組標籤。
    回傳 (tag, group),category 為空時回傳 ("", "")。
    啟動遷移與匯入舊資料共用這一條規則。
    """
    parts = [p.strip() for p in category.split("/") if p.strip()]
    if not parts:
        return "", ""
    return parts[-1], (parts[0] if len(parts) >= 2 else "")


def migrate_categories_to_groups(paths: VaultPaths) -> int:
    """
    啟動時的一次性遷移:把還帶著舊 category 的 .md 轉成標籤+群組並重寫檔案。
    群組只在該標籤目前未分組時套用(不覆蓋使用者手動整理過的分組)。
    在 rebuild_index() 之前執行,索引直接建在遷移後的內容上。
    回傳遷移的名詞數。
    """
    reg = load_tags(paths)
    reg_changed = False
    migrated = 0
    for p in paths.notes_dir.glob("*.md"):
        note = read_note_file(p)
        if not note or not note.get("category"):
            continue
        tag, group = category_to_tag_group(note["category"])
        if tag:
            note["tags"] = clean_tags(note["tags"] + [tag])
            if merge_new_tags(reg, [tag], note["created"]):
                reg_changed = True
            if group and not reg[tag].get("group", ""):
                reg[tag]["group"] = group
                reg_changed = True
        write_note_file(paths, note)  # 新格式不再寫出 category
        migrated += 1
    if reg_changed:
        save_tags(paths, reg)
    return migrated


def persist_note(paths: VaultPaths, note: dict) -> None:
    """寫入單筆名詞:檔案落地後同步索引與標籤登記簿。"""
    write_note_file(paths, note)
    register_new_tags(paths, note["tags"], note["updated"])
    conn = db(paths)
    index_upsert(conn, note)
    conn.commit()
    conn.close()


def next_untitled_name(paths: VaultPaths) -> str:
    """未命名存檔的流水號佔位名稱:掃索引中現有的 Untitle(n) 取最大值 +1。"""
    conn = db(paths)
    rows = conn.execute("SELECT name FROM notes").fetchall()
    conn.close()
    top = 0
    for r in rows:
        m = UNTITLED_RE.match(r["name"])
        if m:
            top = max(top, int(m.group(1)))
    return f"Untitle({top + 1})"


def attach_progress(paths: VaultPaths, note: dict) -> dict:
    """把個人狀態(書籤 + 複習排程)貼回名詞 dict,就地修改後回傳同一個物件。

    ⚠ 這一步不能省:`.md` 已經不存 marked/srs(見 storage.dump_note),舊檔裡
    殘留的那幾個 key 也不再是真相。少了它,任何「讀檔 → 回傳」的路徑都會把
    **過期的 frontmatter 值**當成現況回給前端,而搜尋結果(走索引 JOIN)卻是對的
    ——同一筆名詞在列表跟詳細頁顯示不同的書籤狀態,而且不會有任何錯誤訊息。
    """
    entry = progress.get_entry(paths, paths.vault_id, note["id"])
    note["marked"] = entry["marked"]
    note["srs_box"] = entry["srs_box"]
    # 沒複習過就退回 updated,與 indexer 的 COALESCE(p.srs_due, n.updated) 同一個規則。
    # ⚠ 兩邊各算一次是刻意的(srs.effective_due 的檔頭寫過同一件事):寫入路徑上的
    # note dict 可能剛被換過 updated,只算一次會讓索引與重新讀檔給出不同答案。
    note["srs_due"] = entry["srs_due"] if entry["srs_box"] is not None else note["updated"]
    return note


def save_progress_entry(paths: VaultPaths, nid: str, **changes) -> dict:
    """更新個人狀態:先寫真相檔(progress.json),再同步索引的投影表。

    順序與 persist_note 一致——先檔案、再索引。
    """
    data = progress.load_progress(paths)
    entry = (data.get(paths.vault_id, {}).get(nid) or progress.blank_entry()) | changes
    if "srs_box" in changes and changes["srs_box"] is None:
        entry["srs_due"] = None
    progress.save_progress(paths, _merged_progress(data, paths.vault_id, nid, entry))
    conn = db(paths)
    progress_upsert(conn, paths.vault_id, nid, entry)
    conn.commit()
    conn.close()
    return entry


def _merged_progress(data: dict, vault_id: str, nid: str, entry: dict) -> dict:
    per_vault = data.setdefault(vault_id, {})
    if entry["marked"] or entry["srs_box"] is not None:
        per_vault[nid] = entry
    else:
        per_vault.pop(nid, None)
    if not per_vault:
        data.pop(vault_id, None)
    return data


def load_note_or_404(paths: VaultPaths, nid: str) -> dict:
    if not valid_id(nid):
        raise HTTPException(400, "不合法的 ID")
    note = read_note_file(note_path(paths, nid))
    if not note:
        raise HTTPException(404, "找不到這筆名詞")
    return attach_progress(paths, note)


def replace_note_asset(paths: VaultPaths, nid: str, old_rel: str, new_rel: str) -> dict:
    """把某筆名詞的附件路徑從 old_rel 換成 new_rel,並在安全時刪掉舊檔。

    刻意不動 updated、不寫歷史版本——重新壓縮圖片不是內容編輯,比照 marked
    書籤標記的先例(見 routers/notes.py:api_set_mark)。讓它 bump updated 會把
    「依上次編輯時間」的列表排序整個打亂;而且每壓一張就吃掉一個歷史版本額度
    (HISTORY_LIMIT 只有 3),一次批次壓縮就會把使用者真正的編輯歷史沖光。

    舊檔只有在沒被任何歷史快照引用時才刪:snapshot_of() 會把 attachments 深拷貝
    進 history,舊路徑還留在最多 3 個版本快照裡,無條件刪檔會讓「版本回復」
    回復出一堆破圖。寧可留下少數孤兒檔,也不要弄壞回復功能。
    """
    note = load_note_or_404(paths, nid)
    hit = next((a for a in note.get("attachments", []) if a.get("path") == old_rel), None)
    if not hit:
        raise HTTPException(404, "這筆名詞沒有這個附件")
    hit["path"] = new_rel
    persist_note(paths, note)

    referenced = any(a.get("path") == old_rel
                     for snap in note.get("history", [])
                     for a in snap.get("attachments", []))
    if not referenced:
        (paths.notes_dir / old_rel).unlink(missing_ok=True)
    return note


def retarget_template(paths: VaultPaths, from_id: str, to_id: str) -> int:
    """把所有 template == from_id 的名詞改掛到 to_id,回傳改了幾筆。

    給「孤兒樣板」的批次轉換用(健康度檢查的 missing_template,見 app/health.py):
    欄位樣板類外掛解除安裝後,引用它的名詞的欄位標題會退化成機器字串,這支讓
    使用者一鍵把它們搬回某個還存在的樣板;欄位值原地保留(變成殘留欄位,顯示層
    本來就會附列,資料不消失)。

    刻意不動 updated、不寫歷史版本——理由與 replace_note_asset 完全相同:
    使用者並沒有編輯內容,批次寫入 bump updated 會把「依上次編輯時間」的排序
    整個打亂,寫歷史則是 N 筆各吃一格 HISTORY_LIMIT(只有 3),一次轉換就把
    真正的編輯歷史沖光。to_id 是否存在由 router 驗(這一層不 import templates,
    維持 service 只管「寫檔+同步索引」)。
    """
    count = 0
    for p in sorted(paths.notes_dir.glob("*.md")):
        note = read_note_file(p)
        if not note or note.get("template") != from_id:
            continue
        note["template"] = to_id
        persist_note(paths, note)
        count += 1
    return count


def all_attachments(paths: VaultPaths) -> list[dict]:
    """列出所有名詞的所有附件({note_id, path, name})。

    給「批次壓縮既有圖片」用。不走 /api/search:那邊一次只回一頁(PAGE_SIZE 筆),
    要列舉全部名詞就得一頁一頁翻完。這裡直接走檔案(跟 indexer/service 其他地方
    一樣的 glob 慣例),一次拿完。

    不預先過濾圖片:「什麼副檔名算圖片」的唯一真相在前端 utils.js:isImageFile()
    (後端的 IMAGE_EXTS 較窄,而且只服務另一支沒人用的 /images 端點)。
    """
    out: list[dict] = []
    for p in sorted(paths.notes_dir.glob("*.md")):
        note = read_note_file(p)
        if not note:
            continue
        for a in note.get("attachments", []):
            if a.get("path"):
                out.append({"note_id": note["id"], "path": a["path"], "name": a.get("name", "")})
    return out


def _forget_progress(paths: VaultPaths, conn, nid: str) -> None:
    """**永久**刪除一筆名詞時,連同它的書籤與複習進度一起清掉。

    ⚠ 只有真正的永久刪除才呼叫這支(危險區的批次刪除、回收桶裡的永久刪除)。
    一般刪除是搬進回收桶、可以還原,還原後複習進度理應跟著回來——
    這跟「名詞刪除 → 分享連結 404,還原 → 又能用」是同一個道理。
    """
    progress.drop_note(paths, paths.vault_id, nid)
    conn.execute("DELETE FROM progress WHERE vault_id=? AND note_id=?", (paths.vault_id, nid))


def delete_note(paths: VaultPaths, nid: str) -> None:
    """刪除名詞:搬進回收桶(保留 TRASH_RETENTION_DAYS 天)並移出索引。

    對外的行為跟以前一樣——.md 與資產目錄都離開 notes/、搜尋也找不到了——
    只是東西還在 <使用者目錄>/trash/ 裡等著被還原或過期清掉(見 app/trash.py)。
    讀不出來的壞檔沒辦法寫進回收桶(frontmatter 都解析不了),只好直接刪掉,
    否則使用者連清掉它都做不到。
    """
    p = note_path(paths, nid)
    if not p.exists():
        raise HTTPException(404, "找不到這筆名詞")
    note = read_note_file(p)
    if note:
        trash.move_to_trash(paths, note)
    else:
        p.unlink()
        shutil.rmtree(paths.assets_dir / nid, ignore_errors=True)
    conn = db(paths)
    index_delete(conn, nid)
    conn.commit()
    conn.close()


def restore_from_trash(paths: VaultPaths, nid: str) -> dict:
    """把回收桶裡的一筆名詞還原回 notes/(檔案+資產目錄),並補回索引與標籤登記。

    順序:先搬資產、再寫檔(persist_note 會同步索引),確定名詞已經在 notes/
    落地之後才刪回收桶那一份——中途出錯寧可兩邊都留著,也不要兩邊都沒有。
    """
    if not valid_id(nid):
        raise HTTPException(400, "不合法的 ID")
    note = trash.read_trashed(paths, nid)
    if not note:
        raise HTTPException(404, "回收桶裡找不到這筆名詞")
    if note_path(paths, nid).exists():
        raise HTTPException(400, "同 ID 的名詞已經存在,無法還原")
    note.pop("deleted", None)  # 回收桶專用欄位,回到 notes/ 就不該再帶著
    trash.restore_assets(paths, nid)
    persist_note(paths, note)
    trash.drop(paths, nid)
    return note


def purge_trash_note(paths: VaultPaths, nid: str) -> None:
    """永久刪除回收桶裡的一筆(不可復原)。"""
    if not valid_id(nid):
        raise HTTPException(400, "不合法的 ID")
    if not trash.drop(paths, nid):
        raise HTTPException(404, "回收桶裡找不到這筆名詞")


def retarget_links(paths: VaultPaths, old_name: str, new_name: str) -> int:
    """把所有名詞裡指向 old_name 的 `[[連結]]` 改指到 new_name。回傳受影響的名詞數。

    刻意**不動 updated、不寫歷史版本**——理由同 rename_tag / 書籤標記 / 批次圖片
    壓縮:被合併掉的是別人的名詞,指著它的那幾筆並沒有被使用者編輯過,讓它們
    bump updated 會把「依上次編輯時間」的列表排序打亂,寫歷史版本更會把 HISTORY_LIMIT
    只有 3 個的編輯歷史沖掉。

    只走「索引說有指向它」的那幾筆,不掃全部檔案(合併是互動操作,要即時)。
    """
    old_key = norm_key(old_name)
    if not old_key or old_key == norm_key(new_name):
        return 0
    conn = db(paths)
    affected = 0
    for nid in notes_linking_to(paths, old_key):
        note = read_note_file(note_path(paths, nid))
        if not note or not links.retarget_note(note, old_key, new_name):
            continue
        write_note_file(paths, note)
        index_upsert(conn, note)
        affected += 1
    conn.commit()
    conn.close()
    return affected


def _absorb_assets(paths: VaultPaths, src_id: str, dst_id: str) -> dict[str, str]:
    """把 src 名詞的資產檔**複製**一份到 dst 底下,回傳 {舊相對路徑: 新相對路徑}。

    複製而不是搬移:被併掉的那筆會進回收桶(30 天內可還原),搬走檔案會讓還原
    出來的名詞只剩破圖。多出來的那份磁碟佔用換「還原永遠是完整的」,值得。

    檔名撞名時改用新的隨機名(附件檔名本來就是隨機 uuid,撞名機率極低,但撞到
    就會靜默蓋掉別人的圖,不能不處理)。整個目錄都複製,不只 attachments 列到的
    ——說明欄內嵌的 `![](assets/<id>/x.png)` 不一定在 attachments 裡。
    """
    src_dir, dst_dir = paths.assets_dir / src_id, paths.assets_dir / dst_id
    if not src_dir.is_dir():
        return {}
    dst_dir.mkdir(parents=True, exist_ok=True)
    mapping = {}
    for f in sorted(src_dir.iterdir()):
        if not f.is_file():
            continue
        name = f.name
        if (dst_dir / name).exists():
            name = f"{uuid.uuid4().hex[:10]}{f.suffix}"
        shutil.copy2(f, dst_dir / name)
        mapping[f"assets/{src_id}/{f.name}"] = f"assets/{dst_id}/{name}"
    return mapping


def _apply_asset_mapping(text: str, mapping: dict[str, str]) -> str:
    for old, new in mapping.items():
        text = text.replace(old, new)
    return text


def merge_notes(paths: VaultPaths, target_id: str, source_ids: list[str]) -> dict:
    """把 source_ids 幾筆名詞合併進 target_id 那一筆。

    「先記下來、之後再整理」的後半段:同一個詞被記了好幾次時,把它們收成一筆。
    合併的原則是**不靜默丟資料**——
      - 說明欄:依序接起來(去掉完全相同的重複段落),中間空一行
      - 樣板欄位:target 空著的欄位由來源補上;target 已經有值的保留 target 的,
        被放棄的值原樣回報給呼叫端(dropped_fields)顯示,不是悄悄消失
      - 標籤/附件:聯集
      - 書籤標記:任一筆有標記,合併後就有
      - created:取最早的一筆——同一個詞第一次被記下來的時間才是它的建立時間
      - `[[連結]]`:指向被併掉那些名字的連結,一律改指到 target(見 retarget_links);
        不做的話合併完會留下一堆斷掉的連結,等於把剛建好的網又拆了
      - 被併掉的那幾筆走一般刪除**進回收桶**,不是永久刪除(合併判斷錯了要救得回來)

    回傳 {note, merged, dropped_fields, relinked}。
    """
    if not valid_id(target_id):
        raise HTTPException(400, "不合法的 ID")
    target = load_note_or_404(paths, target_id)
    sources = []
    for sid in dict.fromkeys(source_ids):  # 去重,順序保留
        if sid == target_id:
            raise HTTPException(400, "不能把名詞合併進它自己")
        sources.append(load_note_or_404(paths, sid))
    if not sources:
        raise HTTPException(400, "沒有要合併的來源名詞")

    history = ([snapshot_of(target)] + target.get("history", []))[:HISTORY_LIMIT]
    descs = [target["description"].strip()]
    dropped: list[dict] = []
    tags = list(target["tags"])
    attachments = list(target["attachments"])
    seen_paths = {a.get("path") for a in attachments}

    for src in sources:
        mapping = _absorb_assets(paths, src["id"], target_id)
        desc = _apply_asset_mapping(src["description"].strip(), mapping)
        if desc and desc not in descs:
            descs.append(desc)
        for key, value in src["fields"].items():
            value = (value or "").strip()
            if not value:
                continue
            if not target["fields"].get(key):
                target["fields"][key] = value
            elif target["fields"][key] != value:
                dropped.append({"id": src["id"], "name": src["name"], "key": key, "value": value})
        tags += src["tags"]
        for a in src["attachments"]:
            new_path = mapping.get(a.get("path", ""), a.get("path", ""))
            if new_path and new_path not in seen_paths:
                seen_paths.add(new_path)
                attachments.append({**a, "path": new_path})
        target["created"] = min(target["created"], src["created"])

    target.update(
        description="\n\n".join(d for d in descs if d),
        tags=clean_tags(tags),
        attachments=attachments,
        updated=time.time(),
        history=history,
    )
    persist_note(paths, target)

    # 個人狀態(書籤 + 複習排程)另外合併——它不在 .md 裡,persist_note 帶不走。
    # 盒序取最低、到期日取最早、書籤取 OR:與 created 取最早同一種保守側直覺。
    # 合併後的名詞含有兩邊的內容,其中一邊你可能根本不熟,就照最不熟的那邊算;
    # 取 target 的話,被併掉那筆若是 box 0(完全想不起來)會被 target 的 box 5
    # 蓋掉,那個你不熟的概念接下來 90 天不會再出現。None(從沒複習過)在這裡
    # 就是最低,一旦有任一邊沒複習過就整筆回到未複習。
    # ⚠ 來源的進度**不清掉**:它們是搬進回收桶(可還原),還原後進度該跟著回來。
    merged_entry = progress.merge_entries(
        [progress.get_entry(paths, paths.vault_id, n["id"]) for n in [target] + sources])
    save_progress_entry(paths, target_id, **merged_entry)

    # 順序:先落地 target,再改寫連結,最後才刪來源。中途出錯的話,最壞情況是
    # 「合併好了但來源還在」——使用者看得見、可以自己再刪;反過來先刪就沒得救。
    relinked = 0
    for src in sources:
        relinked += retarget_links(paths, src["name"], target["name"])
    for src in sources:
        delete_note(paths, src["id"])

    # 連結改寫可能也動到 target 自己(它本來就指著被併掉的那筆),重讀一次回傳最新內容
    return {"note": load_note_or_404(paths, target_id), "merged": len(sources),
            "dropped_fields": dropped, "relinked": relinked}


def rename_tag(paths: VaultPaths, old: str, new: str) -> int:
    """把所有名詞裡的標籤 old 改名為 new,並同步標籤登記簿。回傳受影響的名詞數。"""
    old, new = old.strip(), new.strip()
    if not new:
        raise HTTPException(400, "標籤名稱不可為空")
    if old == new:
        return 0
    conn = db(paths)
    affected = 0
    for p in paths.notes_dir.glob("*.md"):
        note = read_note_file(p)
        if not note or old not in note["tags"]:
            continue
        note["tags"] = clean_tags([new if t == old else t for t in note["tags"]])
        write_note_file(paths, note)
        index_upsert(conn, note)
        affected += 1
    conn.commit()
    conn.close()

    reg = load_tags(paths)
    old_meta = reg.pop(old, None)
    if old_meta:
        if new in reg:
            reg[new]["created"] = min(reg[new]["created"], old_meta["created"])
        else:
            reg[new] = old_meta
        save_tags(paths, reg)
    return affected


def merge_tags(paths: VaultPaths, keep: str, absorb: list[str]) -> int:
    """把 absorb 裡的標籤全部併進 keep(標籤重複偵測的套用動作)。回傳受影響的名詞數。

    等價於逐個 rename_tag(x, keep),但**只掃一遍 .md**——一組三個變體用 rename_tag
    要掃三遍全庫,而合併通常是一次處理好幾組。

    三件事跟 rename_tag 一致、刻意不改:
      - `clean_tags()` 負責去重:一筆名詞同時掛著 Mes 與 MES 時收成一個,不會變兩個一樣的。
      - **不 bump `updated`、不寫歷史版本**。標籤改名不是使用者對名詞內容的編輯,
        bump 會把「依上次編輯時間」的列表排序整個打亂(被刪掉的舊功能
        apply_tag_replacements 當初有 bump,那是它自己的不一致,別跟著抄)。
      - 登記簿的 `created` 取所有成員裡最早的:那才是「這個詞第一次被記下來」的時間。

    多做的一件事:**keep 沒有群組時,採納某個被併掉標籤的群組**。rename_tag 會把
    被併掉那邊的 group 靜默丟掉,而「不靜默丟資料」是這個 repo 的核心承諾——把
    Mes(在「製造」群組)併進沒有分組的 MES,結果整組從側欄的分類樹消失,
    使用者只會覺得「合併一下標籤,分類就不見了」。
    """
    keep = keep.strip()
    if not keep:
        # 機器碼而非中文訊息:前端會把 detail 直接 alert(理由同 routers/ai.py
        # 的 ai_disabled)。這條是防呆不是使用者會遇到的常態,前端一律顯示
        # actions.tagMergeFailed。
        raise HTTPException(400, "empty_keep")
    sources = {t.strip() for t in absorb if t.strip()} - {keep}
    if not sources:
        return 0

    conn = db(paths)
    affected = 0
    for p in paths.notes_dir.glob("*.md"):
        note = read_note_file(p)
        if not note or not sources.intersection(note["tags"]):
            continue
        note["tags"] = clean_tags([keep if t in sources else t for t in note["tags"]])
        write_note_file(paths, note)
        index_upsert(conn, note)
        affected += 1
    conn.commit()
    conn.close()

    reg = load_tags(paths)
    metas = [reg.pop(s) for s in sources if s in reg]
    if metas or keep in reg:
        meta = reg.get(keep) or {"created": 0.0, "group": ""}
        created = [m["created"] for m in metas if m.get("created")]
        if meta.get("created"):
            created.append(meta["created"])
        meta["created"] = min(created) if created else 0.0
        if not meta.get("group"):
            meta["group"] = next((m.get("group") for m in metas if m.get("group")), "")
        reg[keep] = meta
        save_tags(paths, reg)
    return affected


def delete_all_notes(paths: VaultPaths) -> int:
    """刪除所有名詞:.md 檔、每筆的資產目錄、索引全清。回傳刪除筆數。

    永久刪除、不可復原,**刻意不經過回收桶**——這是設定裡的危險區工具(雙重確認),
    使用者按它就是要把東西清乾淨(常見動機之一正是騰出空間),搬進回收桶再放 30 天
    等於什麼都沒清掉。要「刪了還能反悔」請用單筆刪除(delete_note)。
    標籤/樣板登記簿(tags.json/templates.json)保留——
    與單筆 delete_note 一致(標籤定義的存廢跟目前是否被名詞使用是兩件事);
    刪光名詞後這些標籤在 /api/tags 自然變成 0 筆而不再顯示。
    """
    conn = db(paths)
    count = 0
    for p in list(paths.notes_dir.glob("*.md")):
        p.unlink()
        index_delete(conn, p.stem)  # 檔名去掉 .md 即 note id
        _forget_progress(paths, conn, p.stem)
        count += 1
    conn.commit()
    conn.close()
    # 清掉所有名詞的圖片/附件目錄(assets_dir 底下每個 note_id 一層;assets_dir 本身保留)
    if paths.assets_dir.exists():
        for child in paths.assets_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
    return count


def delete_demo_notes(paths: VaultPaths) -> int:
    """只刪掉註冊時種進來的範例名詞(見 app/demo.py),回傳刪除筆數。

    ⚠ **絕不可以**改成呼叫 delete_all_notes():置頂行的刪除鈕出現在使用者已經
    開始建立自己的名詞之後,那樣會把他的東西一起炸掉。刪除範圍的唯一定義是
    demo.purge_vault()——以 demo/notes/ 的檔名為準,只認那些 id。

    永久刪除、不經過回收桶(理由同 delete_all_notes:範例本來就是可拋棄的)。
    範例帶進來的標籤若已經沒有任何名詞在用就一併清掉,但使用者自己建的標籤
    一律不動,即使目前是 0 筆(見 demo.orphan_demo_tags)。

    使用者早就手動刪光範例時回 0——那不是錯誤,呼叫端照樣要清掉旗標。
    """
    ids = purge_vault(paths)
    conn = db(paths)
    count = 0
    for nid in ids:
        p = paths.notes_dir / f"{nid}.md"
        if not p.exists():
            continue
        p.unlink()
        shutil.rmtree(paths.assets_dir / nid, ignore_errors=True)
        index_delete(conn, nid)
        _forget_progress(paths, conn, nid)
        count += 1
    conn.commit()
    conn.close()
    if count:
        # 剩下的名詞還在用哪些標籤 → 範例標籤裡沒被用到的那些才刪。
        still_used = set()
        for p in paths.notes_dir.glob("*.md"):
            note = read_note_file(p)
            if note:
                still_used.update(note["tags"])
        reg = load_tags(paths)
        orphans = [name for name in orphan_demo_tags(paths, still_used) if name in reg]
        if orphans:
            for name in orphans:
                reg.pop(name, None)
            save_tags(paths, reg)
    return count


def delete_notes_in_group(paths: VaultPaths, group: str) -> int:
    """刪除掛有指定群組任一標籤的名詞(選取邏輯同匯出/篩選的群組展開)。回傳刪除筆數。

    永久刪除、不可復原、不經過回收桶;標籤/樣板登記簿保留(理由同 delete_all_notes)。
    群組不存在或底下沒有標籤時回 0(不動任何檔案)。
    """
    group = group.strip()
    if not group:
        return 0
    group_tags = {name for name, meta in load_tags(paths).items()
                  if meta.get("group", "") == group}
    if not group_tags:
        return 0
    conn = db(paths)
    count = 0
    for p in list(paths.notes_dir.glob("*.md")):
        note = read_note_file(p)
        if not note or not group_tags.intersection(note["tags"]):
            continue
        p.unlink()
        shutil.rmtree(paths.assets_dir / note["id"], ignore_errors=True)
        index_delete(conn, note["id"])
        _forget_progress(paths, conn, note["id"])
        count += 1
    conn.commit()
    conn.close()
    return count


def delete_tag(paths: VaultPaths, name: str) -> int:
    """把標籤 name 從所有名詞移除,並清掉登記簿裡的紀錄。回傳受影響的名詞數。"""
    conn = db(paths)
    affected = 0
    for p in paths.notes_dir.glob("*.md"):
        note = read_note_file(p)
        if not note or name not in note["tags"]:
            continue
        note["tags"] = [t for t in note["tags"] if t != name]
        write_note_file(paths, note)
        index_upsert(conn, note)
        affected += 1
    conn.commit()
    conn.close()

    reg = load_tags(paths)
    if name in reg:
        del reg[name]
        save_tags(paths, reg)
    return affected


def delete_user_and_data(user_id: str) -> None:
    """刪除一個使用者:先移除全域登記簿的記錄,再整個砍掉他的私有資料目錄
    (notes/tags/templates/index.db…)。跨層操作放在服務層。防呆(不能刪最後一個
    admin)由呼叫端 routers/admin.py 負責。"""
    delete_user_record(user_id)
    root = user_paths(user_id).root
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)

"""批次匯出(json/csv/zip)與匯入,可依標籤/群組選擇性匯出。

zip 是「完整備份」格式:notes.json(內容同 json 匯出)+ assets/<note_id>/<檔名>
的實體檔案(說明欄貼上的圖片與附件共用同一個目錄,見 routers/files.py)。
json/csv 只有 metadata,附件的二進位內容不在裡面。
"""
import csv
import io
import json
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from ..auth import get_user_paths
from ..config import DEFAULT_TEMPLATE_ID, valid_id
from ..indexer import PROGRESS_COLS, db, index_upsert, progress_upsert, row_to_note
from ..paths import VaultPaths
from ..progress import load_progress, save_progress
from ..sanitize import clean_attachments, clean_fields, clean_tags, clean_template_fields
from ..service import category_to_tag_group
from ..storage import note_path, read_note_file, write_note_file
from ..tags import load_tags, merge_new_tags, save_tags
from ..templates import load_templates, save_templates

router = APIRouter()

# CSV 的核心欄。樣板欄位(fields)攤平成動態欄接在後面——CSV 的存在意義是
# Excel 可讀可編,儲存格塞 JSON 兩者皆失;欄位 key 的保留字規則(sanitize)
# 保證動態欄永遠不會撞到這些核心欄。
EXPORT_FIELDS = ["id", "name", "template", "tags",
                 "description", "marked", "srs_box", "srs_due", "created", "updated"]
# category 已淘汰(由標籤群組取代),仍列入核心欄:匯入舊檔時該欄走
# 標籤+群組轉換,不可被當成樣板欄位吸收
CORE_COLUMNS = set(EXPORT_FIELDS) | {"attachments", "history", "fields", "category"}


def _truthy(v) -> bool:
    """匯入資料的布林欄位解析(目前只有 marked)。不能直接 bool()——CSV 匯出寫出的是
    `True`/`False` 文字,`bool("False")` 是 True,會把整批名詞都變成已標記。
    JSON 匯入帶的是真的 bool,`str(True).lower() == "true"` 也吃得下,一個 helper 兩種格式通用。"""
    return str(v).strip().lower() in ("1", "true", "yes")


def _opt_num(v, cast):
    """匯入資料的選填數值欄位解析(SRS 的 srs_box/srs_due)。回 None 表示「沒有值」。

    同樣不能直接 cast:CSV 沒複習過的名詞那兩欄是空字串,而 JSON 是 null,
    兩者都得對應回「從沒複習過」而不是 0——srs_box=0 是「答錯過、明天再問」,
    跟「還沒進過複習」是完全不同的狀態。"""
    if v is None or str(v).strip() == "":
        return None
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


def _attachment_disposition(filename: str) -> str:
    # HTTP header 值只能 latin-1:檔名含中文(如群組名)要用 RFC 5987 的
    # filename* 百分比編碼,另附 ASCII fallback 給老派 client
    return (f'attachment; filename="export{Path(filename).suffix}"; '
            f"filename*=UTF-8''{quote(filename, safe='')}")


def _note_assets(paths: VaultPaths, nid: str) -> list[Path]:
    """某筆名詞底下的所有實體資產檔(貼上的圖片 + 附件,同一個目錄)。"""
    d = paths.assets_dir / nid
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file())


@router.get("/api/export")
def api_export(format: str = "json", tags: str = "", group: str = "",
               paths: VaultPaths = Depends(get_user_paths)):
    """匯出全部名詞;帶 tags(逗號分隔,OR 聯集)或 group 時只匯出掛有這些標籤的名詞。"""
    want = {t.strip() for t in tags.split(",") if t.strip()}
    if group.strip():
        group = group.strip()
        in_group = {name for name, meta in load_tags(paths).items()
                    if meta.get("group", "") == group}
        if not in_group:
            raise HTTPException(400, "群組不存在或沒有標籤")
        want |= in_group

    conn = db(paths)
    # 書籤與複習進度住在 progress 表(個人狀態,見 app/progress.py),
    # 要 JOIN 進來匯出檔才帶得走——不帶的話換一台機器複習進度就歸零。
    notes = [row_to_note(r) for r in conn.execute(
        f"SELECT n.*, {PROGRESS_COLS} FROM notes n "
        "LEFT JOIN progress p ON p.vault_id=? AND p.note_id=n.id ORDER BY n.name",
        (paths.vault_id,)).fetchall()]
    conn.close()
    if want:
        notes = [n for n in notes if want & set(n["tags"])]

    stem = "jargon-vault-export"
    if group:
        stem += f"-{group}"
    elif want:
        stem += "-tags"

    if format == "csv":
        buf = io.StringIO()
        field_cols = sorted({k for n in notes for k in n["fields"]})
        # extrasaction="ignore":note dict 還有 attachments 等 CSV 不收錄的欄位
        # (附件是二進位檔案,攤平成表格沒有意義,只有 JSON 匯出才帶完整附件資訊)
        w = csv.DictWriter(buf, fieldnames=EXPORT_FIELDS + field_cols,
                           extrasaction="ignore")
        w.writeheader()
        for n in notes:
            row = dict(n)
            row["tags"] = "; ".join(row["tags"])
            row.update(row.pop("fields"))  # 樣板欄位攤平成動態欄
            w.writerow(row)
        return Response(
            content="﻿" + buf.getvalue(),  # BOM,讓 Excel 正確辨識 UTF-8
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": _attachment_disposition(f"{stem}.csv")},
        )

    if format not in ("json", "zip"):
        raise HTTPException(400, "format 僅支援 json、csv 或 zip")

    # v2 包裝:附上匯出範圍內標籤的群組結構,讓匯入端能還原分組。
    # 匯入端同時相容 v1 的純陣列格式。
    reg = load_tags(paths)
    exported_tags = {t for n in notes for t in n["tags"]}
    tag_groups: dict[str, list[str]] = {}
    for t in sorted(exported_tags):
        g = reg.get(t, {}).get("group", "")
        if g:
            tag_groups.setdefault(g, []).append(t)
    # v3 起一併帶走欄位樣板。樣板跟 tag_groups 一樣是「無法從 notes 重建」的真相來源
    # (見 templates.py 檔頭):notes 只存 template id 與 fields 值,少了樣板定義,
    # 換一台機器就只剩 key 當欄位標題,欄位順序/placeholder/AI 指示全都回不來。
    payload = json.dumps({"version": 3, "notes": notes, "tag_groups": tag_groups,
                          "templates": load_templates(paths)},
                         ensure_ascii=False, indent=2)

    if format == "zip":
        # 檔名/目錄結構刻意跟 <使用者目錄>/notes/assets 一致,匯入端照著還原即可;
        # 圖片本身已是壓縮格式,壓縮率有限,用 ZIP_DEFLATED 主要是為了打包成單一檔案。
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("notes.json", payload)
            for n in notes:
                for f in _note_assets(paths, n["id"]):
                    z.write(f, f"assets/{n['id']}/{f.name}")
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": _attachment_disposition(f"{stem}.zip")},
        )

    return Response(
        content=payload,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": _attachment_disposition(f"{stem}.json")},
    )


def _record_template_fields(rec: dict) -> tuple[str, dict[str, str]]:
    """匯入資料的樣板/欄位解析,同時吃新舊兩種格式(比照 storage 的懶遷移)。"""
    template = str(rec.get("template") or "").strip() or DEFAULT_TEMPLATE_ID
    raw = rec.get("fields")
    if isinstance(raw, dict):  # 新版 JSON
        return template, clean_fields(raw)
    # 舊版 JSON 的 alias/synonymy/polysemy 是頂層 key;
    # CSV 則是「非核心欄」一律視為樣板欄位(新舊 CSV 因此走同一條規則)
    fields = {k: str(v).strip() for k, v in rec.items()
              if k not in CORE_COLUMNS and v is not None and str(v).strip()}
    return template, clean_fields(fields)


def _restore_templates(paths: VaultPaths, incoming: list) -> tuple[int, int]:
    """匯入檔帶來的欄位樣板:只補本機沒有的 id,不覆蓋既有樣板。回傳 (新增, 略過)。

    政策比照 tag_groups 的還原(「只套用到目前未分組的標籤,不覆蓋現行分組」)——
    匯入一份備份不該蓋掉手上已經改過的樣板,尤其是使用者自己調過的內建樣板。
    欄位定義走 clean_template_fields 驗過再收,壞掉的整筆略過而不是讓匯入整包失敗。
    略過數要回報:同一份樣板檔匯入第二次會一個都沒新增,不講的話使用者只會覺得壞了。
    """
    if not isinstance(incoming, list) or not incoming:
        return 0, 0
    templates = load_templates(paths)
    known = {t["id"] for t in templates}
    added = skipped = 0
    for raw in incoming:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        tid = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not tid or not name or tid in known:
            skipped += 1
            continue
        try:
            fields = clean_template_fields(raw.get("fields") or [])
        except ValueError:
            skipped += 1
            continue
        templates.append({
            "id": tid, "name": name,
            "builtin": bool(raw.get("builtin")),
            "enabled": raw.get("enabled", True) is not False,
            "ai_input_mode": raw.get("ai_input_mode") if raw.get("ai_input_mode") in ("name", "paste") else "name",
            "ai_prompt": str(raw.get("ai_prompt") or ""),
            "fields": fields,
        })
        known.add(tid)
        added += 1
    if added:
        save_templates(paths, templates)
    return added, skipped


@router.get("/api/export/templates")
def api_export_templates(paths: VaultPaths = Depends(get_user_paths)):
    """只匯出欄位樣板。刻意做成完整匯出檔的子集(`{version, templates}`),
    所以這份檔案能直接餵給 /api/import,完整備份也能餵給 /api/import/templates。"""
    payload = json.dumps({"version": 3, "templates": load_templates(paths)},
                         ensure_ascii=False, indent=2)
    return Response(
        content=payload,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": _attachment_disposition("jargon-vault-templates.json")},
    )


@router.post("/api/import/templates")
async def api_import_templates(file: UploadFile = File(...),
                               paths: VaultPaths = Depends(get_user_paths)):
    """只匯入欄位樣板:吃樣板匯出檔、完整備份、或純樣板陣列,都只取 templates。"""
    if Path(file.filename or "").suffix.lower() != ".json":
        raise HTTPException(400, "僅支援 .json")
    try:
        data = json.loads((await file.read()).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(400, "JSON 內容不合法")
    incoming = data if isinstance(data, list) else (data or {}).get("templates")
    if not isinstance(incoming, list):
        raise HTTPException(400, "找不到欄位樣板(需要含 templates 的匯出檔)")
    added, skipped = _restore_templates(paths, incoming)
    return {"added": added, "skipped": skipped}


def _apply_imported_progress(paths: VaultPaths, conn, pending: dict[str, dict]) -> None:
    """把匯入檔帶進來的書籤/複習進度落地(真相檔 + 索引投影)。

    語意與 tag_groups/templates 的還原一致——**只補不覆蓋**:本機已經有進度的
    名詞維持原樣,匯入一份舊備份不該把手上正在跑的複習排程倒退回去。
    """
    if not pending:
        return
    data = load_progress(paths)
    per_vault = data.setdefault(paths.vault_id, {})
    for nid, entry in pending.items():
        if nid in per_vault:
            continue
        per_vault[nid] = entry
        progress_upsert(conn, paths.vault_id, nid, entry)
    if not per_vault:
        data.pop(paths.vault_id, None)
    save_progress(paths, data)


def _restore_assets(paths: VaultPaths, bundle: zipfile.ZipFile,
                    imported_ids: set[str]) -> int:
    """把 ZIP 內 assets/<note_id>/<檔名> 的實體檔案還原到使用者的資產目錄。

    只還原這次真的匯入成功的名詞(id 被略過的就不留下孤兒檔案),並且逐段自己
    驗證路徑——ZIP 內的檔名是外部輸入,`..` 或絕對路徑會寫到資料目錄外(zip slip)。
    同名檔直接覆蓋:資產檔名是上傳當下的隨機碼,同名即同一個檔。
    """
    restored = 0
    for name in bundle.namelist():
        if name.endswith("/"):
            continue
        parts = name.split("/")
        if len(parts) != 3 or parts[0] != "assets":
            continue
        nid, fname = parts[1], parts[2]
        if nid not in imported_ids or not valid_id(nid):
            continue
        if not fname or Path(fname).name != fname:  # 同 files.py:api_get_asset 的防護
            continue
        dest_dir = paths.assets_dir / nid
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / fname).write_bytes(bundle.read(name))
        restored += 1
    return restored


@router.post("/api/import")
async def api_import(file: UploadFile = File(...), paths: VaultPaths = Depends(get_user_paths)):
    ext = Path(file.filename or "").suffix.lower()
    content = await file.read()

    tag_groups: dict[str, list[str]] = {}
    incoming_templates: list = []  # v3 起匯出檔會帶欄位樣板
    bundle: zipfile.ZipFile | None = None  # .zip 匯入時保留著,名詞寫完後再還原附件
    if ext == ".csv":
        text = content.decode("utf-8-sig")
        records = list(csv.DictReader(io.StringIO(text)))
        for r in records:
            r["tags"] = [t.strip() for t in (r.get("tags") or "").split(";") if t.strip()]
    elif ext in (".json", ".zip"):
        if ext == ".zip":
            try:
                bundle = zipfile.ZipFile(io.BytesIO(content))
                raw = bundle.read("notes.json")
            except (zipfile.BadZipFile, KeyError):
                raise HTTPException(400, "ZIP 內容不合法(需含 notes.json 的匯出檔)")
        else:
            raw = content
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, list):  # v1:純名詞陣列
            records = data
        # v2/v3 包裝。只有 templates 沒有 notes 的樣板匯出檔也照收——使用者按錯
        # 「匯入…」還是「匯入樣板」不該換來一個錯誤訊息,兩邊都吃得下對方的檔案。
        elif isinstance(data, dict) and (isinstance(data.get("notes"), list)
                                         or isinstance(data.get("templates"), list)):
            records = data["notes"] if isinstance(data.get("notes"), list) else []
            if isinstance(data.get("tag_groups"), dict):
                tag_groups = data["tag_groups"]
            if isinstance(data.get("templates"), list):  # v3;v2 沒有這個 key,略過即可
                incoming_templates = data["templates"]
        else:
            raise HTTPException(400, "JSON 內容必須是名詞陣列或含 notes/templates 的匯出檔")
    else:
        raise HTTPException(400, "僅支援 .json、.csv 或 .zip")

    # 樣板先補起來,名詞寫進去時 template id 才有對應的欄位定義可查
    templates_added, _ = _restore_templates(paths, incoming_templates)

    imported = 0
    imported_ids: set[str] = set()
    pending_progress: dict[str, dict] = {}
    errors: list[str] = []
    # 批次匯入共用一條連線與一份標籤登記簿,最後一次寫回
    # (不走 service.persist_note 的單筆寫入)
    conn = db(paths)
    tag_reg = load_tags(paths)
    tag_reg_changed = False
    for rec in records:
        name = str(rec.get("name") or "").strip()
        if not name:
            errors.append(f"略過:缺少名詞名稱(id={rec.get('id','')})")
            continue
        nid = str(rec.get("id") or "").strip() or uuid.uuid4().hex[:12]
        if not valid_id(nid):
            errors.append(f"略過:不合法的 ID「{nid}」")
            continue
        # 同 id 已存在時是「覆蓋」——若匯入資料沒帶 updated,不該蓋成匯入當下的
        # 時間,要保留既有那筆的 updated 不動;全新 id 才用 now 當預設值
        existing = read_note_file(note_path(paths, nid))
        now = time.time()
        try:
            created = float(rec.get("created") or now)
        except (TypeError, ValueError):
            created = now
        updated_fallback = existing["updated"] if existing else now
        try:
            updated = float(rec.get("updated") or updated_fallback)
        except (TypeError, ValueError):
            updated = updated_fallback
        template, fields = _record_template_fields(rec)
        tags = clean_tags(rec.get("tags") or [])
        # 舊檔的 category 欄轉成標籤+群組(與啟動遷移同一條規則)
        legacy_tag, legacy_group = category_to_tag_group(str(rec.get("category") or ""))
        if legacy_tag:
            tags = clean_tags(tags + [legacy_tag])
        note = {
            "id": nid, "name": name,
            "description": str(rec.get("description") or ""),
            "template": template,
            "fields": fields,
            "tags": tags,
            "attachments": clean_attachments(rec.get("attachments") or []),
            "marked": _truthy(rec.get("marked")),
            # SRS 複習進度隨匯出檔搬走(與 shares.json 不同:分享 token 綁本站網址,
            # 搬到別台無意義;複習進度搬過去完全有意義,不帶就是換機一次歸零)。
            # 沒有值 → None,storage 的懶遷移會讓 srs_due 退回 updated。
            "srs_box": _opt_num(rec.get("srs_box"), int),
            "srs_due": _opt_num(rec.get("srs_due"), float),
            "created": created, "updated": updated,
        }
        write_note_file(paths, note)
        if merge_new_tags(tag_reg, note["tags"], note["updated"]):
            tag_reg_changed = True
        if legacy_tag and legacy_group and not tag_reg[legacy_tag].get("group", ""):
            tag_reg[legacy_tag]["group"] = legacy_group
            tag_reg_changed = True
        index_upsert(conn, note)
        # 書籤與複習進度不在 .md 裡(見 app/progress.py),write_note_file 帶不走,
        # 要另外收進個人進度檔。只在真的有值時才記,免得替每一筆匯入的名詞都
        # 物化一個 srs_due——那會讓它們全部卡在複習佇列最前面。
        if note["marked"] or note["srs_box"] is not None:
            pending_progress[nid] = {
                "marked": note["marked"],
                "srs_box": note["srs_box"],
                "srs_due": note["srs_due"] if note["srs_box"] is not None else None,
            }
        imported += 1
        imported_ids.add(nid)
    _apply_imported_progress(paths, conn, pending_progress)
    conn.commit()
    conn.close()

    assets = _restore_assets(paths, bundle, imported_ids) if bundle else 0

    # 還原匯出檔內的標籤群組——只套用到目前「未分組」的標籤,
    # 不覆蓋現行分組(匯入舊備份不該打亂手上的整理)
    for g, names in tag_groups.items():
        g = str(g).strip()
        if not g or not isinstance(names, list):
            continue
        for t in names:
            t = str(t)
            if t in tag_reg and not tag_reg[t].get("group", ""):
                tag_reg[t]["group"] = g
                tag_reg_changed = True
    if tag_reg_changed:
        save_tags(paths, tag_reg)
    return {"imported": imported, "errors": errors, "assets": assets,
            "templates": templates_added}

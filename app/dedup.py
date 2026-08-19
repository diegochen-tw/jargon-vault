"""
重複偵測:找出「同一個詞被記了不只一次」的名詞。

為什麼需要:這個專案的前提是「先記下來,之後再整理」(write it down now,
organize it later)。快速捕捉的必然後果就是同一個詞被記三次——大小寫不同、
全形半形不同、加不加空格、或者是用別名記的。不做去重的快速捕捉等於鼓勵
使用者製造垃圾,「之後再整理」那一半就只兌現了標籤分組。

判定分三級,都建立在 sanitize 的兩把尺上(名詞的正規化在那裡集中定義):
  same-name  norm_key 相同 —— 大小寫/全形半形/空白差異(最確定的重複)
  near       loose_key 相同但 norm_key 不同 —— 再加上標點與連字號的差異
             (cSSFI-123 / cSSFI 123)
  alias      一方的名稱出現在另一方的別名類欄位裡(config.ALIAS_FIELD_KEYS),
             也就是「B 用別名記了 A 已經記過的詞」

刻意**不做**模糊字串相似度(編輯距離之類):行話裡差一個字往往就是不同的東西
(cSSFI123 與 cSSFI124 是兩支不同的程式),寧可漏報也不要讓使用者去審一堆假陽性。

同一組尺也用來偵測**標籤**的重複(find_duplicate_tag_groups):快速捕捉製造最多的
垃圾其實不是重複的名詞,而是同一個標籤的好幾種寫法(Mes / MES / ＭＥＳ)——名詞至少
還會在搜尋時撞在一起,標籤變體則是安靜地把同一疊東西拆成兩疊,側欄看起來就是
兩個不相干的分類。判定只有前兩級(標籤沒有別名欄位)。

本模組只讀索引與標籤登記簿(同 search.py 的層級),不寫檔、不碰 HTTP。合併的寫入
動作在 app/service.py:merge_notes(名詞)與 app/service.py:merge_tags(標籤)。
"""
import json
import re

from .config import ALIAS_FIELD_KEYS
from .indexer import db
from .paths import VaultPaths
from .sanitize import loose_key, norm_key
from .tags import load_tags

# 別名欄位常一次寫好幾個叫法:「上料站、Loader、LDR」。依常見分隔符切開各自比對,
# 不然整欄當一個字串比,永遠比不中。
_ALIAS_SPLIT_RE = re.compile(r"[,、,;;/|]+")

# 太短的名稱不參與近似比對:兩個字母的縮寫撞在一起的機率太高,提示只會變雜訊。
MIN_KEY_LEN = 2


def _alias_keys(fields: dict) -> set[str]:
    """一筆名詞的別名比對鍵集合(norm_key 化,已去空)。"""
    out = set()
    for key in ALIAS_FIELD_KEYS:
        for part in _ALIAS_SPLIT_RE.split(str(fields.get(key) or "")):
            k = norm_key(part)
            if len(k) >= MIN_KEY_LEN:
                out.add(k)
    return out


def _rows(paths: VaultPaths) -> list[dict]:
    """所有名詞的比對用摘要。

    刻意撈全表、不設上限:重複偵測要是漏看幾筆,結論就是錯的(「沒有重複」這種
    答案不能是被截斷截出來的)。這跟 search.py 那條「絕不在 Python 端後置篩選」
    的禁令不衝突——那條禁的是**分頁查詢**先截斷再篩,這裡根本沒有分頁。
    """
    conn = db(paths)
    sql = ("SELECT id, name, name_key, template, tags, fields, updated, description "
           "FROM notes ORDER BY created")
    rows = conn.execute(sql).fetchall()
    conn.close()
    out = []
    for r in rows:
        fields = json.loads(r["fields"])
        out.append({
            "id": r["id"], "name": r["name"], "key": r["name_key"],
            "loose": loose_key(r["name"]), "aliases": _alias_keys(fields),
            "template": r["template"], "tags": json.loads(r["tags"]),
            "updated": r["updated"], "description": r["description"],
        })
    return out


def _summary(row: dict) -> dict:
    """回給前端的單筆摘要(說明欄截短,重複清單只需要辨認得出是哪一筆)。"""
    desc = (row["description"] or "").strip().replace("\n", " ")
    return {"id": row["id"], "name": row["name"], "template": row["template"],
            "tags": row["tags"], "updated": row["updated"],
            "excerpt": desc[:120] + ("…" if len(desc) > 120 else "")}


def _reason(a: dict, b: dict) -> str:
    """a、b 兩筆的重複理由,不重複則回空字串。順序由確定性高到低。"""
    if a["key"] and a["key"] == b["key"]:
        return "same-name"
    if len(a["loose"]) >= MIN_KEY_LEN and a["loose"] == b["loose"]:
        return "near"
    if (len(a["key"]) >= MIN_KEY_LEN and a["key"] in b["aliases"]) or \
       (len(b["key"]) >= MIN_KEY_LEN and b["key"] in a["aliases"]):
        return "alias"
    return ""


def find_similar(paths: VaultPaths, name: str, fields: dict | None = None,
                 exclude_id: str = "") -> list[dict]:
    """編輯器即時提示用:跟(還沒存檔的)name/fields 疑似重複的既有名詞。

    exclude_id 是正在編輯的那一筆——編輯既有名詞時當然會跟自己同名。
    """
    probe = {"id": "", "name": name, "key": norm_key(name), "loose": loose_key(name),
             "aliases": _alias_keys(fields or {})}
    if len(probe["key"]) < MIN_KEY_LEN:
        return []
    out = []
    for row in _rows(paths):
        if row["id"] == exclude_id:
            continue
        reason = _reason(probe, row)
        if reason:
            out.append({**_summary(row), "reason": reason})
    return out


def find_duplicate_groups(paths: VaultPaths) -> list[dict]:
    """全庫掃描:把互為重複的名詞收成一組一組。

    用 union-find 傳遞地合併(A≈B、B≈C 就收成同一組),避免同一筆名詞出現在兩組
    裡讓使用者分兩次合併。組內依 updated 由新到舊排序,第一筆就是預設要保留的
    那筆(最近編輯過的通常內容最完整)。

    邊是用「相同的鍵分桶」找出來的,不是兩兩比對:三種理由每一種都可以化約成
    「同一把尺算出來的鍵相同」,所以整趟是線性的。名詞多起來之後 O(n²) 逐對比
    會讓這個掃描慢到沒人想按。
    """
    rows = _rows(paths)
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # 一組裡可能同時存在好幾種關係(同名的兩筆,加上第三筆是靠別名連進來的),
    # 所以整組的理由收成集合全部回報,不是只留一個——只顯示「同名」會讓使用者
    # 看不懂那筆名字完全不同的怎麼會在這一組裡。
    reasons: dict[int, set[str]] = {}
    order = {"same-name": 0, "near": 1, "alias": 2}

    def union(i: int, j: int, reason: str) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri
        root = find(i)
        merged = reasons.pop(ri, set()) | reasons.pop(rj, set()) | {reason}
        reasons[root] = merged

    # 1) 同名:norm_key 相同
    by_key: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        if len(row["key"]) >= MIN_KEY_LEN:
            by_key.setdefault(row["key"], []).append(i)
    for members in by_key.values():
        for j in members[1:]:
            union(members[0], j, "same-name")

    # 2) 近似同名:loose_key 相同。只在 norm_key **不同**時才算一條 near 邊——
    #    同名的兩筆 loose_key 當然也一樣,不排除的話每一組同名都會被多貼一個
    #    「近似」標籤,使用者看到的理由就沒有鑑別度了。
    loose_buckets: dict[str, dict[str, int]] = {}
    for i, row in enumerate(rows):
        if len(row["loose"]) >= MIN_KEY_LEN:
            loose_buckets.setdefault(row["loose"], {}).setdefault(row["key"], i)
    for by_norm in loose_buckets.values():
        reps = list(by_norm.values())
        for j in reps[1:]:
            union(reps[0], j, "near")

    # 3) 別名:某筆的名稱鍵出現在另一筆的別名欄位裡
    for i, row in enumerate(rows):
        for alias in row["aliases"]:
            for j in by_key.get(alias, []):
                if i != j:
                    union(i, j, "alias")

    groups: dict[int, list[dict]] = {}
    for i, row in enumerate(rows):
        root = find(i)
        if root in reasons:
            groups.setdefault(root, []).append(row)

    out = []
    for root, members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda r: r["updated"], reverse=True)
        kinds = sorted(reasons[root], key=lambda r: order[r])
        out.append({"reason": kinds[0], "reasons": kinds,
                    "notes": [_summary(m) for m in members]})
    # 最確定的理由排前面(最該處理的),其次組員多的
    out.sort(key=lambda g: (order[g["reason"]], -len(g["notes"])))
    return out


# ── 標籤的重複偵測 ────────────────────────────────────────────────────
# 上面那套是名詞的,這一套是標籤的。共用的是 sanitize 的**兩把尺**(那才是「算不算
# 同一個東西」的定義,全站只能有一份);union-find 那十行各寫各的,那是教科書演算法,
# 抽成共用 helper 反而要去動一段已經有測試覆蓋的既有程式。

_TAG_ORDER = {"same": 0, "near": 1}


def _tag_counts(paths: VaultPaths) -> dict[str, int]:
    """每個標籤掛在幾筆名詞上(與 routers/taxonomy.py:api_tags 同一招:掃索引的 tags 欄)。

    刻意撈全表、不設上限,理由同 _rows():「沒有重複」這種答案不能是被截斷截出來的。
    """
    conn = db(paths)
    count: dict[str, int] = {}
    for (tj,) in conn.execute("SELECT tags FROM notes"):
        for t in json.loads(tj):
            count[t] = count.get(t, 0) + 1
    conn.close()
    return count


def find_duplicate_tag_groups(paths: VaultPaths) -> list[dict]:
    """全庫掃描:其實是同一個東西、卻被寫成好幾個標籤的,收成一組一組。

    兩級判定,結構同 find_duplicate_groups(分桶 + union-find,不是兩兩比對):
      same  norm_key 相同 —— 大小寫/全形半形/空白差異(Mes / MES / ＭＥＳ)
      near  loose_key 相同但 norm_key 不同 —— 再加上標點與連字號(SFIS-1 / SFIS 1)

    ⚠ **MIN_KEY_LEN 只套在 near 那一級,same 不設下限**。那個下限的用意是「兩個
    字母的縮寫撞在一起的機率太高」——那是對**近似**比對說的。`AI`/`QA`/`PM` 都是
    合法標籤,而它們的大小寫變體(`ai` vs `AI`)正是這個功能最該抓到的東西;
    norm_key 完全相等不是巧合,是同一個字。
    """
    counts = _tag_counts(paths)
    reg = load_tags(paths)
    names = sorted(counts)
    parent = list(range(len(names)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    reasons: dict[int, set[str]] = {}

    def union(i: int, j: int, reason: str) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri
        root = find(i)
        reasons[root] = reasons.pop(ri, set()) | reasons.pop(rj, set()) | {reason}

    keys = [norm_key(n) for n in names]
    looses = [loose_key(n) for n in names]

    # 1) 同名:norm_key 相同(空字串鍵跳過——那是純空白的標籤,湊在一起沒有意義)
    by_key: dict[str, list[int]] = {}
    for i, k in enumerate(keys):
        if k:
            by_key.setdefault(k, []).append(i)
    for members in by_key.values():
        for j in members[1:]:
            union(members[0], j, "same")

    # 2) 近似:loose_key 相同。只在 norm_key **不同**時才算一條 near 邊——同名的兩個
    #    loose_key 當然也一樣,不排除的話每一組同名都會被多貼一個「近似」而失去鑑別度。
    loose_buckets: dict[str, dict[str, int]] = {}
    for i, lk in enumerate(looses):
        if len(lk) >= MIN_KEY_LEN:
            loose_buckets.setdefault(lk, {}).setdefault(keys[i], i)
    for by_norm in loose_buckets.values():
        reps = list(by_norm.values())
        for j in reps[1:]:
            union(reps[0], j, "near")

    groups: dict[int, list[int]] = {}
    for i in range(len(names)):
        root = find(i)
        if root in reasons:
            groups.setdefault(root, []).append(i)

    out = []
    for root, members in groups.items():
        if len(members) < 2:
            continue
        # 用得最多的排第一 = 預設保留的那個;同票取最早出現的(它是這個寫法的本尊)
        members.sort(key=lambda i: (-counts[names[i]], reg.get(names[i], {}).get("created", 0)))
        kinds = sorted(reasons[root], key=lambda r: _TAG_ORDER[r])
        out.append({
            "reason": kinds[0], "reasons": kinds,
            "tags": [{"name": names[i], "count": counts[names[i]],
                      "group": reg.get(names[i], {}).get("group", ""),
                      "created": reg.get(names[i], {}).get("created", 0)}
                     for i in members],
        })
    out.sort(key=lambda g: (_TAG_ORDER[g["reason"]], -len(g["tags"])))
    return out

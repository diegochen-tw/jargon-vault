"""語意檢索:把「檔案為真」的名詞庫接上本機嵌入模型。

解決的是一種關鍵字搜尋幫不上忙的場景:使用者記得「有個講上料防呆的詞」,但
想不起來它叫什麼,而那筆名詞的內文裡根本沒有「防呆」兩個字。fts5(trigram)
只認字面,對這種問題完全無能為力。

本模組是複合層:往下用 storage 讀 .md、用 llm 打
嵌入模型、用 vectors 存取向量、用 search 問索引;往上只給 routers/semantic.py。

──────────────────────────────────────────────────────────────────────
寫入路徑絕不碰嵌入
──────────────────────────────────────────────────────────────────────

service.persist_note() 維持同步、不呼叫任何 AI。存檔絕不能因為模型服務掛掉
而變慢或失敗——AI 在本專案一律是選配,不是相依。

代價是向量會落後於 .md,而那正是 content hash 差分存在的理由:落後多少由
status() 誠實報出來(「待更新 N 筆」),使用者自己按更新。附帶好處是
rename_tag / retarget_links / 書籤標記那些「重新 upsert 但內容沒變」的路徑
完全不會浪費嵌入呼叫。

──────────────────────────────────────────────────────────────────────
混合檢索為什麼不違反「篩選排序分頁一律下推 SQL」
──────────────────────────────────────────────────────────────────────

見 search_hybrid() 的 docstring——那條鐵律有血淚史,必須正面回應。
"""
import hashlib
import time

from . import llm, vectors
from .paths import VaultPaths
from .search import PAGE_SIZE, filtered_ids, notes_by_ids, search_notes
from .storage import read_note_file

# 嵌入來源文字的字元上限。多數嵌入模型的 context 都在 512~8192 token 之間,
# 超過的部分不是報錯而是**安靜截斷**,所以自己先截,截在哪裡至少是已知的。
# 名稱與欄位值排在說明欄前面,確保最有辨識度的部分一定進得去。
EMBED_TEXT_MAX = 2000

# 一次送幾筆給嵌入模型。太大時本機模型的記憶體會吃緊,太小則來回次數多。
# 16×2000 字元在 CPU 跑 bge-m3 這種大模型,單一 Ollama 呼叫就可能超過反向代理的
# 逾時(Cloudflare origin 100s、nginx 預設 60s);縮到 8 讓最壞單次呼叫時間減半,
# REINDEX_TIME_BUDGET 的檢查粒度才有效。嵌入運算佔絕對主導,多幾次來回可忽略。
EMBED_BATCH = 8

# 每個 reindex 請求的自我時限(秒)。TIMEOUT_EMBED=120 > Cloudflare 100s >
# nginx 60s——代理一定比後端先斷,斷了回的是 HTML 錯誤頁,前端只能顯示一句
# 泛用的「建立索引失敗」。所以每個 HTTP 請求必須遠短於任何代理逾時:超過預算
# 就提前收工,剩下的由 remaining 誠實回報,前端的進度迴圈本來就會繼續打。
REINDEX_TIME_BUDGET = 20.0

# 兩臂各取多深來做融合。與 PAGE_SIZE 一致:有界的只有「排序深度」,
# 而那個界跟 /api/search 的 limit 本來就是同一件事。
RRF_DEPTH = PAGE_SIZE

# RRF 的平滑常數。60 是這個演算法原始論文就在用的值,刻意不做成可調參數——
# 兩臂的分數尺度本來就不可比,能調的旋鈕只會招來沒有依據的微調。
RRF_K = 60


# ── 嵌入什麼 ────────────────────────────────────────────────────────

def embed_text(note: dict) -> str:
    """一筆名詞要拿去嵌入的文字。**「什麼東西被嵌入」的唯一真相。**

    hash 就是對這個字串算的,所以「內容有沒有變」的定義完全明確。

    刻意收**全部** fields 值(含已停用的欄位與樣板改版後的殘留 key),不去查
    樣板:查了的話,使用者在設定裡勾掉一個欄位就會讓全庫的 hash 一起變,整個
    庫要重嵌一遍——而那個欄位的值其實還在名詞裡,語意也沒有變。
    """
    parts = [note.get("name") or ""]
    tags = note.get("tags") or []
    if tags:
        parts.append(" ".join(tags))
    parts += [v for v in (note.get("fields") or {}).values() if v]
    parts.append(note.get("description") or "")
    return "\n".join(p for p in parts if p).strip()[:EMBED_TEXT_MAX]


def fingerprint(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _scan(paths: VaultPaths) -> dict[str, tuple[str, str]]:
    """{note_id: (嵌入文字, hash)}。讀不出來的 .md 安靜略過(同 rebuild_index)。

    回收桶在 notes_dir 之外,所以這個 glob 掃不到已刪除的名詞——不必特別排除。
    """
    out: dict[str, tuple[str, str]] = {}
    for f in sorted(paths.notes_dir.glob("*.md")):
        note = read_note_file(f)
        if not note:
            continue
        text = embed_text(note)
        if text:
            out[note["id"]] = (text, fingerprint(text))
    return out


# ── 建索引 ──────────────────────────────────────────────────────────

def quick_status(paths: VaultPaths) -> tuple[int, int]:
    """(已建立向量數, 還沒有向量的名詞數)。**只數檔案,不解析任何 .md。**

    ⚠ 查詢路徑一定要用這支,不能用 status()——後者要把每一筆 .md 讀進來算 hash,
    在搜尋端點上等於每查一次就全庫掃一遍,而查詢是跟著搜尋框的 debounce 一直發生的。

    刻意跟 status() 的 `stale` 用不同的詞:
      unindexed = 完全沒有向量的名詞(數目錄就知道,O(檔案數) 但不讀檔)
      stale     = 沒有向量 **或** 內容改過的名詞(要算 hash,只在設定頁算)
    兩個是不同的東西,不是同一個數字的兩種精度——別把它們合成一個名字。
    """
    indexed = vectors.count(paths)
    total = sum(1 for _ in paths.notes_dir.glob("*.md"))
    return indexed, max(0, total - indexed)


def status(paths: VaultPaths, settings: dict) -> dict:
    """索引現況(精確版,要讀全部 .md 算 hash)。給設定頁顯示「已建立 N / 共 M / 待更新 S」。"""
    model = (settings.get("embed_model") or "").strip()
    current = _scan(paths)
    stored = vectors.fingerprints(paths)
    m = vectors.meta(paths)
    compatible = vectors.is_compatible(paths, model) if model else True
    stale = [nid for nid, (_, h) in current.items() if stored.get(nid) != h]
    return {
        "enabled": bool(settings.get("enabled")),
        "embed_model": model,
        "model": m.get("model", ""),
        "dim": int(m.get("dim") or 0),
        "total": len(current),
        # 模型不相容時現存的向量等於全部作廢,老實回報 0,不要讓人以為還有索引
        "indexed": 0 if not compatible else len(stored),
        "stale": len(current) if not compatible else len(stale),
        "model_mismatch": not compatible,
    }


async def reindex(paths: VaultPaths, settings: dict, limit: int = 0) -> dict:
    """把落後的向量補上,回傳這一批做了什麼。

    limit=0 表示一次做完;>0 表示最多做這麼多筆,剩下的由回傳的 remaining 告訴
    呼叫端。分批是為了讓前端跑進度迴圈——照抄設定 → 內容那個「批次壓縮既有
    圖片」的既有做法,所以不需要背景工作者、不需要串流、不需要任何新基礎建設。
    """
    model = (settings.get("embed_model") or "").strip()
    if not model:
        raise llm.LLMError("尚未設定嵌入模型(設定 → AI 連線 → 嵌入模型)")

    # 換模型 / 換位元組序 → 現存向量全部不可比,整包丟掉重來。
    # 混用的症狀是靜默的爛排序,不會有任何錯誤訊息,所以這裡不能「盡量沿用」。
    if not vectors.is_compatible(paths, model):
        vectors.reset(paths)

    current = _scan(paths)
    stored = vectors.fingerprints(paths)
    orphans = [nid for nid in stored if nid not in current]
    stale = sorted(nid for nid, (_, h) in current.items() if stored.get(nid) != h)

    batch = stale if limit <= 0 else stale[:limit]
    embedded = 0
    failed: list[dict] = []   # [{"id", "name", "error"}] 這一批嵌不動的名詞
    conn = vectors.db(paths)
    try:
        for nid in orphans:
            vectors.delete(conn, nid)
        conn.commit()

        start = time.monotonic()
        for i in range(0, len(batch), EMBED_BATCH):
            # 至少做完第一個 chunk 才檢查預算:一個都不做的話 remaining 不減,
            # 前端的進度迴圈會零進度空轉——那正是這條時限要防的事的另一種寫法。
            if i and time.monotonic() - start > REINDEX_TIME_BUDGET:
                break
            chunk = batch[i:i + EMBED_BATCH]
            try:
                # llm.embed 保證回傳與輸入同順序(openai 分支會依 data[].index
                # 重排),這裡才敢用位置把向量配回 note id
                vecs = await llm.embed(settings, [current[nid][0] for nid in chunk])
            except llm.LLMError as batch_err:
                # 整批失敗 → 逐筆隔離。一批 8 筆裡只要有一筆讓模型爆掉(實例:
                # bge-m3 在舊版 Ollama 對特定輸入報 500),整批拋錯的舊寫法會讓
                # 整條建索引永久卡在同一個地方,後面的名詞全都建不了。
                # 逐筆重試把元凶揪出來:好的照建,壞的記名跳過、由回傳的
                # failed 大聲報告(UI 與 log 都看得到是哪幾筆、服務端說了什麼)。
                #
                # ⚠ 只隔離「服務有回應但報錯」(LLMError.status 非 None)的失敗。
                # 連線類失敗(斷線/逾時,status=None)一律原樣拋——服務掛了時
                # 逐筆重試只會把斷線誤報成「每一筆名詞都壞掉」。
                if batch_err.status is None:
                    raise
                vecs = []
                for nid in chunk:
                    try:
                        vecs.append((await llm.embed(settings, [current[nid][0]]))[0])
                    except llm.LLMError as e:
                        if e.status is None:
                            raise   # 隔離到一半服務斷線,同樣立刻停
                        note = read_note_file(paths.notes_dir / f"{nid}.md") or {}
                        failed.append({"id": nid, "name": note.get("name", ""),
                                       "error": str(e)})
                        vecs.append(None)
            for nid, vec in zip(chunk, vecs):
                if vec is None:
                    continue   # 已記在 failed
                if not vec:
                    # 空向量 = 服務端異常。只 continue 的話這筆永遠 stale、
                    # remaining 永遠不減,前端會無限迴圈狂打後端——寧可大聲失敗。
                    raise llm.LLMError(
                        f"嵌入模型回傳空向量(名詞 {nid}),請確認嵌入模型設定")
                vectors.upsert(conn, nid, current[nid][1], vec)
                # 維度跟著第一個真的算出來的向量登記。⚠ 一定要在確定有向量時才寫,
                # 空批次寫進 dim=0 會讓下次的相容性檢查拿一個假維度去比。
                vectors.set_meta(conn, model, len(vec))
                embedded += 1
            # 每批就 commit:中途失敗時已經算好的向量不用重算
            conn.commit()
    finally:
        conn.close()

    return {
        "embedded": embedded,
        "deleted": len(orphans),
        # 失敗的不算在 remaining:它們仍是 stale(status() 誠實、下次重按會再試),
        # 但算進 remaining 的話,同一輪的下一個請求又會從它們開頭重撞一次,
        # 前端的進度迴圈就永遠走不完。
        "remaining": max(0, len(stale) - embedded - len(failed)),
        "failed": failed,
        "total": len(current),
        "indexed": vectors.count(paths),
    }


def clear(paths: VaultPaths) -> dict:
    vectors.reset(paths)
    return {"cleared": True}


# ── 查詢 ────────────────────────────────────────────────────────────

def _rrf(*ranked: list[str]) -> list[str]:
    """Reciprocal Rank Fusion:把 N 個各自已排序的 id 序列融成一個。

    score(id) = Σ 1/(RRF_K + 名次)。刻意不做分數正規化——關鍵字臂根本沒有分數
    (它的順序是「名稱命中優先 + 時間新的優先」),向量臂的 cosine 又跟它不可比,
    硬要正規化只是把兩個不同單位的東西加起來然後假裝有意義。名次是兩邊都有的
    共同語言。
    """
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for arm in ranked:
        for rank, nid in enumerate(arm):
            scores[nid] = scores.get(nid, 0.0) + 1.0 / (RRF_K + rank)
            first_seen.setdefault(nid, rank)
    # 分數相同時用「在任一臂裡出現的最好名次」再用 id 打平,讓結果穩定可測
    return sorted(scores, key=lambda nid: (-scores[nid], first_seen[nid], nid))


async def search_hybrid(paths: VaultPaths, settings: dict, q: str, *,
                        tags: list[str], any_tags: list[str] | None = None,
                        days: int = 0, since: float = 0, until: float = 0,
                        template: str = "", marked: bool = False,
                        sort: str = "updated", limit: int = PAGE_SIZE,
                        date_field: str = "updated") -> list[dict]:
    """關鍵字 + 語意的混合檢索。

    ⚠ 這裡的 Python 端融合**不違反**「篩選/排序/分頁一律下推 SQL」那條鐵律:

      關鍵字臂  search_notes() 原封不動,每一個維度都還是 SQL 條件。
      向量臂    tags/any_tags/template/days/since/until/marked 由
                search.filtered_ids() 用**同一份 _filters()** 組出一個**完整、
                無 LIMIT** 的 id 集合,KNN 只在那個集合內比對。

    Python 只做「把兩個各自已篩選、各自已排序的序列合併」,那是 hybrid retrieval
    的標準 fusion 步驟。被禁的那個舊寫法是「SQL 撈固定 500 筆 → Python 端**才篩**
    標籤」,那裡的候選集**不保證**含正確答案;這裡的候選集對**每一個篩選維度都
    是完備的**,有界的只有排序深度 K,而那個界跟 /api/search 的 limit 是同一件事。

    由此推論的一個刻意缺口:**不做第二頁**。RRF 名次在 Python 端算,全域 OFFSET
    無法下推(要正確就得改成 seek 游標,那得動 _order())。router 一律回
    has_more=False,UI 老實說「這是語意最相近的 N 筆」,不假裝翻得完。
    """
    q = (q or "").strip()
    if not q:
        return []

    has_filter = bool(tags or any_tags or template or days or since or until or marked)
    allowed = filtered_ids(paths, tags, any_tags, days, since, until, template,
                           marked, date_field=date_field) if has_filter else None

    lexical = [n["id"] for n in search_notes(
        paths, q, tags, any_tags, days, since, until, template,
        limit=RRF_DEPTH, sort=sort, marked=marked, date_field=date_field)]

    qvec = (await llm.embed(settings, [q]))[0]
    semantic = [nid for nid, _ in vectors.knn(paths, qvec, allowed, k=RRF_DEPTH)]

    return notes_by_ids(paths, _rrf(lexical, semantic)[:max(0, limit)])

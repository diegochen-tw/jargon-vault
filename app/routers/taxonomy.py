"""標籤與群組的統計清單(側欄用)、標籤管理(重新命名/刪除/分組/重複偵測與合併)。"""
import json

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_user_paths, get_vault_admin, get_vault_read, get_vault_write
from ..dedup import find_duplicate_tag_groups
from ..indexer import db
from ..models import TagGroupIn, TagMergeIn, TagRenameIn
from ..paths import VaultPaths
from ..service import delete_tag, merge_tags, rename_tag
from ..tags import assign_group, dissolve_all_groups, dissolve_group, load_tags, rename_group

router = APIRouter()


@router.get("/api/tags")
def api_tags(paths: VaultPaths = Depends(get_vault_read)):
    """標籤統計(含群組歸屬)與群組統計(掛有群組內任一標籤的名詞數,不重複計)。"""
    conn = db(paths)
    count: dict[str, int] = {}
    reg = load_tags(paths)
    group_count: dict[str, int] = {}
    for (tj,) in conn.execute("SELECT tags FROM notes"):
        note_tags = json.loads(tj)
        for t in note_tags:
            count[t] = count.get(t, 0) + 1
        # 同一筆名詞掛多個同群組標籤時,群組只計一次
        for g in {reg.get(t, {}).get("group", "") for t in note_tags} - {""}:
            group_count[g] = group_count.get(g, 0) + 1
    conn.close()
    return {
        "tags": sorted(
            [{"name": k, "count": v,
              "created": reg.get(k, {}).get("created", 0),
              "group": reg.get(k, {}).get("group", "")}
             for k, v in count.items()],
            key=lambda x: (-x["count"], x["name"])),
        "groups": sorted(
            [{"name": g, "count": c} for g, c in group_count.items()],
            key=lambda x: x["name"]),
    }


@router.put("/api/tags/{name}")
def api_rename_tag(name: str, body: TagRenameIn, paths: VaultPaths = Depends(get_vault_write)):
    affected = rename_tag(paths, name, body.name)
    return {"ok": True, "affected": affected}


@router.delete("/api/tags/{name}")
def api_delete_tag(name: str, paths: VaultPaths = Depends(get_vault_write)):
    affected = delete_tag(paths, name)
    return {"ok": True, "affected": affected}


# ⚠ 這兩條要排在 `/api/tags/{name}` **前面**看起來像個問題,其實不是:
# `/api/tag-duplicates` 與 `/api/tags/merge` 的方法/路徑都不會被 {name} 那兩條吃掉
# (GET/DELETE vs POST,而且前者根本不在 /api/tags/ 底下)。位置放在這裡只是因為
# 它們在概念上是「標籤管理」的延伸。
@router.get("/api/tag-duplicates")
def api_tag_duplicates(paths: VaultPaths = Depends(get_vault_read)):
    """全庫掃描:其實是同一個東西、卻被寫成好幾個標籤的,收成一組一組。

    純字串比對、不打 AI,所以沒有分頁也沒有進度回報——按下去就有答案。
    「意思相同但寫法完全不同」那一層是另一支(POST /api/ai/tag-duplicates)。
    """
    return {"groups": find_duplicate_tag_groups(paths)}


@router.post("/api/tags/merge")
def api_merge_tags(body: TagMergeIn, paths: VaultPaths = Depends(get_vault_write)):
    """把 absorb 裡的標籤全部併進 keep。形狀比照 POST /api/notes/merge。"""
    return {"ok": True, "affected": merge_tags(paths, body.keep, body.absorb)}


@router.put("/api/tag-groups")
def api_assign_group(body: TagGroupIn, paths: VaultPaths = Depends(get_vault_write)):
    """把所選標籤設為某群組(建立=加入;group 空字串=移出群組)。"""
    try:
        affected = assign_group(paths, body.tags, body.group)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "affected": affected}


@router.put("/api/tag-groups/{name}")
def api_rename_group(name: str, body: TagRenameIn, paths: VaultPaths = Depends(get_vault_write)):
    """群組改名(body 沿用 TagRenameIn:同樣只有一個新名稱)。

    改成既有的群組名 = 合併兩組,見 tags.rename_group。
    """
    if not body.name.strip():
        raise HTTPException(400, "群組名稱不可為空")
    return {"ok": True, "affected": rename_group(paths, name, body.name)}


@router.delete("/api/tag-groups/{name}")
def api_dissolve_group(name: str, paths: VaultPaths = Depends(get_vault_write)):
    """打散單一群組。"""
    return {"ok": True, "affected": dissolve_group(paths, name)}


@router.delete("/api/tag-groups")
def api_dissolve_all_groups(paths: VaultPaths = Depends(get_vault_write)):
    """全部打散,恢復到只有標籤的狀態。"""
    return {"ok": True, "affected": dissolve_all_groups(paths)}

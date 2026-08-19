"""
樂觀鎖:PUT /api/notes/{nid} 與 POST /api/notes/{nid}/restore 的 409。

守的是「靜默覆蓋」——`load → mutate → persist` 中間別人存過的內容會被整包
蓋掉,而且**完全沒有訊號**:寫的人以為存好了,另一個人的段落就消失了。

看起來像「自己開兩個分頁才會撞」的小機率事件,所以很容易被當成可以晚點再做的
東西——但它是**靜默**的資料遺失,沒有任何一層會替你發現,補做的成本遠高於一開始
就做。手機開一個分頁、桌機開一個分頁,就已經是兩個寫入者了。
"""


def _create(client, name="SFC", **extra):
    r = client.post("/api/notes", json={"name": name, "description": "第一版", **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_save_without_base_updated_still_works(client, register_user):
    """base_updated 沒帶 = 跳過檢查。MCP server 與舊版前端都不帶,
    相容性不能因為這個功能而斷掉。"""
    u = register_user()
    c = u["client"]
    n = _create(c)
    r = c.put(f"/api/notes/{n['id']}", json={"name": n["name"], "description": "第二版"})
    assert r.status_code == 200
    assert r.json()["description"] == "第二版"


def test_stale_base_updated_is_rejected_with_409(client, register_user):
    """★ 核心那一支:A 與 B 同時打開同一筆,A 先存(基準相符 → 通過),
    B 再存 → B 必須被擋下,不可以把 A 的內容靜默蓋掉。"""
    u = register_user()
    c = u["client"]
    n = _create(c)
    a_saw = n["updated"]
    b_saw = n["updated"]

    ra = c.put(f"/api/notes/{n['id']}",
               json={"name": n["name"], "description": "A 寫的內容", "base_updated": a_saw})
    assert ra.status_code == 200
    # updated 必須往前走,否則下一次比對會拿舊值過關
    assert ra.json()["updated"] >= n["updated"]

    rb = c.put(f"/api/notes/{n['id']}",
               json={"name": n["name"], "description": "B 寫的內容", "base_updated": b_saw})
    assert rb.status_code == 409

    # A 的內容必須還在——被擋下的那次不可以有任何副作用
    assert c.get(f"/api/notes/{n['id']}").json()["description"] == "A 寫的內容"


def test_rejected_save_does_not_write_a_history_version(client, register_user):
    """409 必須是完全沒有副作用的:HISTORY_LIMIT 只有 3,失敗的嘗試若還寫
    歷史版本,幾次衝突就把真正的編輯歷史沖光了。"""
    u = register_user()
    c = u["client"]
    n = _create(c)
    c.put(f"/api/notes/{n['id']}",
          json={"name": n["name"], "description": "A", "base_updated": n["updated"]})
    before = len(c.get(f"/api/notes/{n['id']}").json()["history"])

    c.put(f"/api/notes/{n['id']}",
          json={"name": n["name"], "description": "B", "base_updated": n["updated"]})

    assert len(c.get(f"/api/notes/{n['id']}").json()["history"]) == before


def test_second_save_works_after_refetching(client, register_user):
    """衝突後重新讀一次、拿新的 updated 當基準,就該存得進去——
    409 是「請重來」,不是「這筆從此鎖死」。"""
    u = register_user()
    c = u["client"]
    n = _create(c)
    c.put(f"/api/notes/{n['id']}",
          json={"name": n["name"], "description": "A", "base_updated": n["updated"]})

    fresh = c.get(f"/api/notes/{n['id']}").json()
    r = c.put(f"/api/notes/{n['id']}",
              json={"name": n["name"], "description": "B", "base_updated": fresh["updated"]})
    assert r.status_code == 200
    assert c.get(f"/api/notes/{n['id']}").json()["description"] == "B"


def test_base_updated_never_leaks_into_the_note(client, register_user):
    """base_updated 是請求欄位,不是名詞的內容。它若漏進 old.update() 就會被
    寫進 .md 的 frontmatter,變成一個沒人認得的殘留 key。"""
    u = register_user()
    c = u["client"]
    n = _create(c)
    r = c.put(f"/api/notes/{n['id']}",
              json={"name": n["name"], "description": "x", "base_updated": n["updated"]})
    assert "base_updated" not in r.json()

    from app.paths import user_paths
    raw = (user_paths(u["id"]).notes_dir / f"{n['id']}.md").read_text(encoding="utf-8")
    assert "base_updated" not in raw


def test_marked_is_still_preserved_across_a_locked_update(client, register_user):
    """樂觀鎖不能破壞既有的保證:NoteIn 不帶 marked,一般編輯必須原樣保留書籤。
    而 PUT /mark 不動 updated,所以它也不該讓後續的 base_updated 失效。"""
    u = register_user()
    c = u["client"]
    n = _create(c)
    assert c.put(f"/api/notes/{n['id']}/mark", json={"marked": True}).status_code == 200

    r = c.put(f"/api/notes/{n['id']}",
              json={"name": n["name"], "description": "改內容", "base_updated": n["updated"]})
    assert r.status_code == 200, "PUT /mark 不動 updated,基準不該因此失效"
    assert r.json()["marked"] is True


def test_restore_is_also_guarded(client, register_user):
    """版本回復同樣是 load → mutate → persist,同一類靜默覆蓋;
    但不帶 base_updated 的 restore 照樣通過(相容性同 PUT)。"""
    u = register_user()
    c = u["client"]
    n = _create(c)
    c.put(f"/api/notes/{n['id']}", json={"name": n["name"], "description": "第二版"})
    stale = n["updated"]

    r = c.post(f"/api/notes/{n['id']}/restore", json={"index": 0, "base_updated": stale})
    assert r.status_code == 409

    fresh = c.get(f"/api/notes/{n['id']}").json()
    r = c.post(f"/api/notes/{n['id']}/restore",
               json={"index": 0, "base_updated": fresh["updated"]})
    assert r.status_code == 200
    assert r.json()["description"] == "第一版"

    # 不帶 base_updated → 跳過檢查(MCP/舊前端相容)
    c.put(f"/api/notes/{n['id']}", json={"name": n["name"], "description": "第三版"})
    assert c.post(f"/api/notes/{n['id']}/restore", json={"index": 0}).status_code == 200

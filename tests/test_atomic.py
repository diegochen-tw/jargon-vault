"""
原子寫入(app/atomic.py):真相來源檔案的唯一寫入方式。

守的是這件事:第一類資料的定義是「刪掉會永久消失」,而
`path.write_text()` 寫到一半失敗會留下**半截的檔案**——那跟刪掉一樣糟,
甚至更糟(半截的 YAML 讀得出來、但內容是錯的)。

`index.db` 壞了有 rebuild_index() 兜底、`vectors.db` 壞了有自我修復,
只有第一類沒有,所以這一層必須被測試守住。
"""
import json

import pytest

from app import atomic


def test_text_roundtrip_keeps_utf8_and_never_translates_newlines(tmp_path):
    p = tmp_path / "note.md"
    s = "---\nname: 混合檢索 (Hybrid Search)\n---\n\n中文內文\n"
    atomic.write_text(p, s)
    assert p.read_text(encoding="utf-8") == s
    # Windows 上絕不能被轉成 CRLF——YAML frontmatter 與 .md 內文都會多出 \r,
    # 而 .md 是使用者會拿文字編輯器直接開的真相來源。
    atomic.write_text(p, "a\nb\n")
    assert p.read_bytes() == b"a\nb\n"


def test_json_does_not_escape_non_ascii_and_passes_sort_keys(tmp_path):
    """tags.json / templates.json 是真相來源,要能被人直接讀。
    ensure_ascii=True 會把中文變成 \\uXXXX,違反「檔案為真」。"""
    p = tmp_path / "tags.json"
    reg = {"運籌一課": {"created": 1.0, "group": "SFC"}}
    atomic.write_json(p, reg)
    raw = p.read_text(encoding="utf-8")
    assert "\\u" not in raw
    assert "運籌一課" in raw
    assert json.loads(raw) == reg

    p2 = tmp_path / "a.json"
    atomic.write_json(p2, {"b": 1, "a": 2}, sort_keys=True)
    raw2 = p2.read_text(encoding="utf-8")
    assert raw2.index('"a"') < raw2.index('"b"')


def test_overwrite_replaces_whole_content(tmp_path):
    """新內容比舊內容短時,不可以留下舊內容的尾巴(那正是 open(w) 沒 truncate
    或寫一半失敗會出現的症狀)。"""
    p = tmp_path / "a.md"
    atomic.write_text(p, "很長很長的舊內容" * 50)
    atomic.write_text(p, "短")
    assert p.read_text(encoding="utf-8") == "短"


def test_failed_write_leaves_original_untouched(tmp_path):
    """★ 這支是整個模組存在的理由:寫入途中爆掉,原檔必須原封不動。
    舊的 write_text 在這個情境下會留下一個被清空或半截的檔案。"""
    p = tmp_path / "note.md"
    atomic.write_text(p, "原本的完整內容\n")

    class Boom:
        def __str__(self):
            raise RuntimeError("模擬序列化到一半爆掉")

    with pytest.raises(Exception):
        atomic.write_text(p, Boom())  # type: ignore[arg-type]

    assert p.read_text(encoding="utf-8") == "原本的完整內容\n"


def test_no_temp_files_left_behind(tmp_path):
    """成功與失敗都不能留下暫存檔——它們跟 .md 同目錄,而 indexer/service
    到處都是 notes_dir.glob('*.md'),留下垃圾遲早會被當成資料掃進去。"""
    p = tmp_path / "note.md"
    atomic.write_text(p, "ok")

    class Boom:
        def __str__(self):
            raise RuntimeError("boom")

    with pytest.raises(Exception):
        atomic.write_text(p, Boom())  # type: ignore[arg-type]

    assert sorted(f.name for f in tmp_path.iterdir()) == ["note.md"]


def test_concurrent_writers_do_not_share_a_temp_file(tmp_path):
    """★ FastAPI 的同步 handler 跑在 threadpool 上,同一個行程裡兩條執行緒會同時
    寫同一個檔。暫存檔名若只帶 pid,B 會在 A 寫到一半時把它截斷,A 再 replace
    過去——結果正是這個模組要防的半截檔案,而且更難查。

    兩個斷言各守一個平台的症狀(實測過舊寫法會被抓到):
      Windows → 兩條執行緒搶同一個暫存檔控制代碼,直接 PermissionError(`errors`)
      POSIX   → 沒有檔案鎖,安靜地寫出混合/截斷的內容(`final in (...)`)
    """
    import threading

    p = tmp_path / "tags.json"
    long_a, long_b = "A" * 200_000, "B" * 200_000
    errors = []

    def writer(payload):
        try:
            for _ in range(20):
                atomic.write_text(p, payload)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(s,)) for s in (long_a, long_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    # 最終內容必須是**其中一個完整的值**,不能是兩者的混合或被截斷的片段
    final = p.read_text(encoding="utf-8")
    assert final in (long_a, long_b), f"檔案被寫花了:長度 {len(final)}"
    assert [f.name for f in tmp_path.iterdir()] == ["tags.json"]


def test_temp_file_lives_in_the_same_directory(tmp_path, monkeypatch):
    """暫存檔必須跟目標同目錄:跨檔案系統的 rename 不是原子的,
    而 tempfile 的預設目錄很可能在另一顆磁碟上。"""
    seen = []
    real_replace = atomic.os.replace

    def spy(src, dst):
        seen.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(atomic.os, "replace", spy)
    p = tmp_path / "sub" / "note.md"
    p.parent.mkdir()
    atomic.write_text(p, "x")

    assert len(seen) == 1
    src, dst = seen[0]
    from pathlib import Path
    assert Path(src).parent == Path(dst).parent == p.parent

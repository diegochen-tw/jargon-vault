"""
VaultPaths(app/paths.py):路徑組合的唯一來源。

守的是「底下每個模組都只收 paths,不自行拼路徑」與既有磁碟佈局的相對關係。
"""
import pytest

from app.paths import USERS_DIR, user_paths


def test_user_vault_layout_is_unchanged():
    """改多使用者之前的扁平佈局,只是巢狀了一層——這個相對關係不能動,
    既有資料目錄就是照這個結構躺在磁碟上的。"""
    p = user_paths("abc123")
    assert p.vault_id == "abc123"
    assert p.root == USERS_DIR / "abc123"
    assert p.notes_dir == p.root / "notes"
    assert p.assets_dir == p.root / "notes" / "assets"
    assert p.tags_path == p.root / "tags.json"
    assert p.templates_path == p.root / "templates.json"
    assert p.db_path == p.root / "index.db"
    assert p.vectors_path == p.root / "vectors.db"


def test_trash_stays_outside_notes_dir():
    """indexer/service 到處都是 notes_dir.glob('*.md');回收桶只要待在那個
    glob 掃不到的地方,刪掉的名詞就不可能從搜尋/統計/匯出裡冒出來。"""
    p = user_paths("abc123")
    assert p.trash_dir.parent == p.root
    assert p.trash_dir != p.notes_dir
    assert p.notes_dir not in p.trash_dir.parents


def test_vault_paths_is_frozen():
    """路徑組合是唯一來源,不該有人在半路改掉某個欄位。"""
    p = user_paths("abc123")
    with pytest.raises(Exception):
        p.notes_dir = USERS_DIR  # type: ignore[misc]

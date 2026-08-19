"""版本號的一致性:app/config.py(唯一真相)、FastAPI metadata、CHANGELOG.md、
Windows exe 的版本資訊,以及 0.x 一定要被標成 pre-release。

發佈時版本號會出現在三個地方——git tag `vX.Y.Z`、`app/config.py:APP_VERSION`、
`CHANGELOG.md` 的 `## [X.Y.Z]` 段落。`.github/workflows/release.yml` 的第一個 step
也會對帳這三者,但那時候 tag 已經推出去了;這裡先在 CI 擋一次,便宜得多。

⚠ 最重要的那支是 `test_packaging_version_matches_app_version`:packaging/ 底下的
複本沒人守,所以它從 1.0.0 一路漂到主版本都 1.2.0 了還停在原地,沒有任何測試紅燈。
版本規則本身(0.x = Beta、1.0.0 = 正式版)寫在 CHANGELOG.md 的 Versioning 段。
"""
import re
from pathlib import Path

from app import create_app
from app.config import APP_VERSION

ROOT = Path(__file__).resolve().parent.parent


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", APP_VERSION), APP_VERSION


def test_fastapi_metadata_uses_the_single_source():
    """/docs 與 openapi.json 顯示的版本必須就是那個常數,不是 FastAPI 的預設 0.1.0。"""
    assert create_app().version == APP_VERSION


def test_changelog_has_an_entry_for_the_current_version():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{APP_VERSION}]" in text, f"CHANGELOG.md 缺少 ## [{APP_VERSION}] 的段落"


def test_ci_workflow_runs_every_test_file():
    """ci.yml 的 shard 是寫死的檔案清單(刻意不引入 pytest-split/xdist,見該檔註解),
    所以新增一支 tests/test_xxx.py 卻忘了掛進去,就會安靜地永遠不被跑到。
    這支用純文字比對守住它——跟本檔上面那幾支一樣,零額外相依。"""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    missing = [p.name for p in sorted((ROOT / "tests").glob("test_*.py"))
               if f"tests/{p.name}" not in ci]
    assert not missing, f"這些測試檔沒有被 ci.yml 的任何一個 job 涵蓋:{missing}"


def test_packaging_version_matches_app_version():
    """packaging/version_info.txt 是 Windows exe 的「內容 → 詳細資料」會顯示的版本,
    是 APP_VERSION 的**複本**。它沒有任何自動產生機制(檔頭曾經聲稱由
    scripts/gen_version_info.py 產生,但那支檔案不存在),所以只能靠這支測試對帳。"""
    text = (ROOT / "packaging" / "version_info.txt").read_text(encoding="utf-8")
    major, minor, patch = APP_VERSION.split("-")[0].split(".")
    tup = f"({major}, {minor}, {patch}, 0)"
    for field in ("filevers", "prodvers"):
        assert f"{field}={tup}" in text, f"version_info.txt 的 {field} 不是 {tup}"
    for field in ("FileVersion", "ProductVersion"):
        msg = f"version_info.txt 的 {field} 不是 {APP_VERSION}"
        assert f"StringStruct('{field}', '{APP_VERSION}')" in text, msg


def test_zero_major_is_released_as_a_prerelease():
    """0.x = Beta,GitHub Release 一定要掛 Pre-release 標記。這條規則寫死在
    release.yml 的 case 分支裡,不是靠發佈的人記得勾——所以這裡守住那個分支還在。

    ⚠ 同時守著 prerelease 與 rc 是兩個獨立輸出:prerelease 決定 GitHub 標記,
    rc 決定 Docker 要不要跳過 latest。合成一個的話,整個 0.x Beta 期間都不會推
    latest,README 的 docker pull 會 404。"""
    rel = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'echo "prerelease=true"' in rel
    assert "0.*)" in rel, "release.yml 沒有把 0.x 判成 prerelease"
    assert 'echo "rc=true"' in rel, "release.yml 缺少獨立的 rc 輸出"
    assert "steps.ver.outputs.rc" in rel, "Docker tag 的判斷沒有改用 rc"

"""
site_settings 單元測試:env 種子、註冊模式判斷、白名單 env 即時聯集、OAuth fallback。

site_settings.json 是 session 共用的全域檔(在共用的 GLOSSARY_DATA_DIR 底下),
所以每個測試前後都把它刪掉,確保起始狀態乾淨、也不污染其他測試檔。
"""
import pytest

from app import site_settings as ss


@pytest.fixture(autouse=True)
def _clean_site_settings(monkeypatch):
    # 清掉可能殘留的檔與相關 env,讓每個測試自己決定起始狀態
    monkeypatch.delenv("ALLOWED_EMAILS", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    ss.SITE_SETTINGS_PATH.unlink(missing_ok=True)
    yield
    ss.SITE_SETTINGS_PATH.unlink(missing_ok=True)


def test_ensure_seeds_from_env_and_defaults_without(monkeypatch):
    # 無 env → 預設 whitelist、空名單(維持既有行為)
    ss.ensure_site_settings()
    assert ss.registration_mode() == "whitelist"
    assert ss.load_site_settings()["allowed_emails"] == []

    # 有 env → 首次啟動種子進檔案
    ss.SITE_SETTINGS_PATH.unlink(missing_ok=True)
    monkeypatch.setenv("ALLOWED_EMAILS", "A@x.com, b@y.com")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    ss.ensure_site_settings()
    s = ss.load_site_settings()
    assert s["registration_mode"] == "whitelist"
    assert s["allowed_emails"] == ["a@x.com", "b@y.com"]  # 小寫去重排序
    assert s["google_oauth"] == {"enabled": True, "client_id": "cid", "client_secret": "sec"}


def test_is_email_allowed_across_modes(monkeypatch):
    ss.save_site_settings({"registration_mode": "open"})
    assert ss.is_email_allowed("anyone@nowhere.com") is True
    ss.save_site_settings({"registration_mode": "closed", "allowed_emails": ["a@x.com"]})
    assert ss.is_email_allowed("a@x.com") is False  # closed 連名單內的也擋
    # whitelist:store 與 env 即時聯集(env 是救援/補充名單)
    ss.save_site_settings({"registration_mode": "whitelist", "allowed_emails": ["store@x.com"]})
    assert ss.is_email_allowed("store@x.com") is True
    assert ss.is_email_allowed("env@y.com") is False
    monkeypatch.setenv("ALLOWED_EMAILS", "env@y.com")
    assert ss.is_email_allowed("env@y.com") is True


def test_google_oauth_config_falls_back_to_env(monkeypatch):
    # store 的 client_id/secret 留空時,讀取要 fallback 回 env
    ss.save_site_settings({"google_oauth": {"enabled": True, "client_id": "", "client_secret": ""}})
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "envcid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "envsec")
    g = ss.google_oauth_config()
    assert g["client_id"] == "envcid" and g["client_secret"] == "envsec"
    assert ss.google_enabled() is True


def test_save_normalizes_and_rejects_bad_mode():
    ss.save_site_settings({
        "registration_mode": "bogus",
        "allowed_emails": ["  MixEd@Case.com ", "mixed@case.com", ""],
    })
    s = ss.load_site_settings()
    assert s["registration_mode"] == "whitelist"      # 不合法 mode 退回預設
    assert s["allowed_emails"] == ["mixed@case.com"]  # trim/小寫/去重/濾空


# ── AI 連線設定(站台層)與一次性搬移 ─────────────────────────────────────

def test_ai_settings_normalize_and_clamp():
    ss.save_site_settings({"ai": {
        "enabled": True, "api_style": "ANTHROPIC", "base_url": " http://box:11434/ ",
        "api_key": " sk-x ", "model": "  ", "embed_model": " bge-m3 ",
    }})
    ai = ss.ai_config()
    assert ai["api_style"] == "ollama"              # 不認識的風格退回預設
    assert ai["base_url"] == "http://box:11434"     # trim + 去掉結尾斜線
    assert ai["api_key"] == "sk-x"
    assert ai["model"] == ss._default_ai()["model"]  # 空白退回預設
    assert ai["embed_model"] == "bge-m3"

    # ⚠ 但嵌入模型允許空:空字串是「沒有嵌入模型」這個有意義的狀態,不能退回預設
    ss.save_site_settings({"ai": {"embed_model": ""}})
    assert ss.ai_config()["embed_model"] == ""


def test_ai_desc_limit_clamps_and_defaults():
    """說明字數上限走 _int_in 夾 50–2000;壞值退回預設 250、缺鍵補預設(開、250)。"""
    for raw, expect in ((0, 50), (99999, 2000), ("abc", 250), (600, 600)):
        ss.save_site_settings({"ai": {"desc_max_chars": raw}})
        assert ss.ai_config()["desc_max_chars"] == expect, raw

    ss.save_site_settings({"ai": {"enabled": True}})  # 舊 dict 缺兩鍵
    ai = ss.ai_config()
    assert ai["desc_limit_enabled"] is True
    assert ai["desc_max_chars"] == 250


def test_ai_migration_seeds_from_an_admin_and_is_idempotent(paths, monkeypatch):
    """⚠ 這支守的是升級當下最容易出事的一步。

    既有站台的 site_settings.json 沒有 "ai" 這個鍵。不搬的話 _normalize() 會直接
    填出廠預設,admin 原本設好的位址與模型被無聲換掉——症狀只會表現成「升級之後
    AI 突然連不上」,而且沒有任何錯誤訊息。
    """
    import json

    from app import migration

    # 舊世界:使用者目錄下各自一份 ai_settings.json
    paths.ai_settings_path.write_text(json.dumps({
        "enabled": True, "base_url": "http://gpu-box:11434", "model": "old-model",
        "embed_model": "bge-m3",
    }), encoding="utf-8")
    monkeypatch.setattr(migration, "_seed_candidate_uids", lambda: [paths.root.name])

    # 沒有 "ai" 鍵的舊站台設定檔
    ss.SITE_SETTINGS_PATH.write_text(json.dumps({"registration_mode": "open"}),
                                     encoding="utf-8")
    migration.migrate_ai_settings_to_site()

    ai = ss.ai_config()
    assert ai["base_url"] == "http://gpu-box:11434" and ai["model"] == "old-model"
    assert ai["embed_model"] == "bge-m3"

    # 冪等:再跑一次不可以把管理者之後改過的值蓋回去
    ss.save_site_settings({**ss.load_site_settings(), "ai": {**ai, "model": "changed"}})
    migration.migrate_ai_settings_to_site()
    assert ss.ai_config()["model"] == "changed"


def test_ai_migration_falls_back_to_defaults_without_a_seed(monkeypatch):
    import json

    from app import migration

    monkeypatch.setattr(migration, "_seed_candidate_uids", lambda: [])
    ss.SITE_SETTINGS_PATH.write_text(json.dumps({"registration_mode": "open"}),
                                     encoding="utf-8")
    migration.migrate_ai_settings_to_site()

    assert ss.ai_config() == ss._default_ai()

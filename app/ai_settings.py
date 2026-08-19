"""
AI 設定層:模型服務「連線層」參數的讀寫入口。

只放連線層設定(是否啟用、API 風格、服務位址、金鑰、生成模型、嵌入模型);
生成的「內容指示」(prompt)與輸入方式改掛在各欄位樣板上(見 templates.py 的
ai_prompt / ai_input_mode),讓每個樣板各自決定怎麼請 AI 生成。

**這是全站唯一一組設定,由站台管理者管理**(真相存在 data/site_settings.json
的 `ai` 區塊,見 site_settings.py 檔頭)。以前是每使用者一份、團隊庫再各一份,
改成站台層的理由:

  1. 本機模型服務本來就只有一台,連線位址與金鑰是**基礎設施**不是個人偏好,
     不該讓每個使用者各自摸索;金鑰散在每個人的檔案裡本身也是問題。
  2. 順帶消掉了「不同庫用不同嵌入模型會互相把向量整包作廢」那個無聲的失敗
     模式(切片 4 §3.3 當初為此在團隊目錄放了一份 ai_settings.json)——
     現在每個庫嵌入用的都是同一個模型,問題從根本上不存在了。

所以這裡**不收 `paths`**:AI 設定與「哪一個庫」無關。舊的
`<使用者目錄>/ai_settings.json` 留在原地不刪(備份照樣帶走,萬一要回頭救得回來),
但已經不再是讀取來源。

本模組只透過 site_settings 碰檔案系統,不碰 HTTP、不呼叫模型服務(那是 app/llm.py
的事)。依賴方向 ai_settings → site_settings 是單向的,site_settings 不得反向 import。
"""
from . import site_settings

# 給 routers 當「欄位留空時退回什麼」用;真正的定義在 site_settings._default_ai()
DEFAULT_AI_SETTINGS = site_settings._default_ai()


def load_ai_settings() -> dict:
    return site_settings.ai_config()


def save_ai_settings(settings: dict) -> None:
    s = site_settings.load_site_settings()
    s["ai"] = settings
    site_settings.save_site_settings(s)   # _clean_ai() 會正規化與夾住不合法的值

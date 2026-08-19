// 設定 modal view:左側選單切換分頁。分頁依「這個設定作用在什麼上」分成四群
// (內容 / AI 功能 / 分享與備份 / 系統),群組標題只是 nav 上的 div,沒有狀態,
// 這裡照舊只認 data-tab。匯出/入沿用既有的檔案下載/上傳流程;標籤管理訂閱
// tags-changed 重繪清單(含勾選分組與依群組/標籤選擇性匯出);欄位樣板訂閱
// templates-changed。
import * as actions from "../actions.js?v=20260820a";
import * as api from "../api.js?v=20260820a";
import {emit, on} from "../bus.js?v=20260820a";
import {SRS_SIZES, srsSize} from "../config.js?v=20260820a";
import {locField, tplLabel} from "../fields.js?v=20260820a";
import {LANG, t} from "../i18n.js?v=20260820a";
import {popModalState, pushModalState} from "../nav.js?v=20260820a";
import {state} from "../store.js?v=20260820a";
import {$, esc, fmtBytes, fmtDate, isImageFile} from "../utils.js?v=20260820a";
import {compressImage} from "../imagecomp.js?v=20260820a";
import * as theme from "../theme.js?v=20260820a";

const DEFAULT_TEMPLATE_ID = "jargon-default";  // 預設樣板 id(永遠啟用、不可停用)
let activeTab = "templates";  // 開啟設定時預設落在「內容」群組的第一頁:標籤管理
const checkedTags = new Set();  // 標籤管理分頁勾選中的標籤(跨重繪保留)
let tagFilter = "";  // 標籤管理搜尋欄的關鍵字(輸入框在 #tagManagerList 外,跨重繪保留)

export function initSettings() {
  $("#menuSettings").onclick = () => openSettings();  // 不把 click event 當成 tab 傳進去
  $("#settingsModalClose").onclick = closeSettings;
  $("#settingsModal").addEventListener("click", e => { if (e.target.id === "settingsModal") closeSettings(); });
  $("#settingsTabs").querySelectorAll(".settings-tab").forEach(b => b.onclick = () => switchTab(b.dataset.tab));

  $("#menuImport").onclick = () => { closeSettings(); $("#importFile").click(); };
  $("#settingsModal").addEventListener("click", async e => {
    // closest 而不是 e.target.dataset:匯出列現在是「標題 + 說明」兩個子 span,
    // 點在字上時 e.target 是 span,只看 target 會讓整列失效(而且完全不報錯)。
    const fmt = e.target.closest?.("[data-fmt]")?.dataset.fmt; if (!fmt) return;
    try { await api.downloadExport(fmt); } catch { alert(t("transfer.exportFailed")); return; }
    closeSettings();
  });
  $("#importFile").addEventListener("change", async e => {
    const file = e.target.files[0]; e.target.value = "";
    if (!file) return;
    if (!confirm(t("transfer.importConfirm", {name: file.name}))) return;
    await actions.importFile(file);
  });

  bindTagToolbar();
  // 「＋新增樣板」入口已移除:新樣板統一由外掛模組安裝取得(POST /api/templates 保留給 MCP)。
  bindTemplateTransfer();
  bindAISettings();
  bindSemanticSettings();
  bindContentSettings();
  bindTrash();
  bindDedup();
  bindTagDup();
  bindHealth();
  bindAccount();
  bindLanguageSettings();
  bindThemeSettings();
  bindBackgroundSettings();
  bindBrandingSettings();
  bindSrsSettings();
  bindAdmin();
  bindBackups();   // 備份是獨立分頁了,不再掛在 bindAdmin() 底下
  bindAbout();

  // 編輯器關掉了 → 若是從健康度清單點進去編輯的,把使用者送回那份清單
  // (見 bus.js 的 settings-resume;由 list.js 盯著「編輯器從開變關」發)。
  on("settings-resume", resumeSettings);

  on("tags-changed", () => {
    if (!isOpen()) return;
    if (activeTab === "tags") renderTagManager();
    if (activeTab === "cleanup") renderPurgeScope();  // 刪除範圍下拉的群組清單跟著刷新
  });
  on("templates-changed", () => { if (isOpen() && activeTab === "templates") renderTemplateManager(); });
}

export const isOpen = () => $("#settingsModal").classList.contains("show");

// tab 省略時沿用上次停留的分頁;外部入口(如未分組彈窗的「標籤管理」)可指定要落在哪一頁。
export function openSettings(tab) {
  // 自己重新打開設定 = 不需要「編輯器關掉後自動回來」了。沒清這個旗標的話,
  // 之後關掉編輯器會把設定又彈回來一次(而使用者早就自己開過又關掉了)。
  state.settingsPaused = false;
  if (!isOpen()) pushModalState(closeSettings);
  $("#settingsModal").classList.add("show");
  switchTab(tab || activeTab);
}

export function closeSettings() {
  if (!isOpen()) return;
  $("#settingsModal").classList.remove("show");
  popModalState();
}

function switchTab(tab) {
  if (tab === "content") tab = "cleanup";  // 舊分頁 id 相容(content 已拆成 trash + cleanup)
  activeTab = tab;
  $("#settingsTabs").querySelectorAll(".settings-tab").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
  $("#settingsPanels").querySelectorAll(".settings-panel").forEach(p => p.classList.toggle("show", p.dataset.panel === tab));
  if (tab === "tags") renderTagManager();
  if (tab === "templates") renderTemplateManager();
  if (tab === "ai") renderAISettings();
  if (tab === "semantic") renderSemanticSettings();
  // 外掛頁每次進來先重載清單:GET /api/plugins 會順帶重掃封裝目錄,管理者剛放進
  // data/plugins/ 的封裝(或剛修好的壞封裝)不用重啟就看得到。先畫手上的舊資料,
  // 載回來再重畫一次,不讓網路延遲把分頁切換卡住。
  if (tab === "plugins") {
    renderPluginManager();
    actions.loadPlugins().then(() => { if (activeTab === "plugins") renderPluginManager(); });
  }
  if (tab === "publish") renderPublishManager();
  if (tab === "backup") renderBackups();
  if (tab === "trash") renderTrash();
  // 整理與清理:健康度檢查(診斷)→ 重複偵測 → 圖片壓縮 → 刪除內容(危險區)。
  // 掃描結果都不保留:切走再回來看到的可能是好幾分鐘前的庫況,而這一頁的每個
  // 動作都會改變那份結果(合併、壓縮、刪除),留著等於誤導。
  if (tab === "cleanup") { renderCleanupSettings(); renderPurgeScope(); resetDedup(); resetHealth(); }
  if (tab === "account") renderAccount();
  // 偏好設定分頁整併了語系 + 主題配色 + Logo/標語三區
  if (tab === "preference") {
    renderLanguageSettings(); renderThemeSettings(); renderBackgroundSettings();
    renderBrandingSettings(); renderSrsSettings();
  }
  if (tab === "admin") renderAdmin();
  if (tab === "about") renderAbout();
}

/* ── 主題配色:兩排色票(淺色/深色各自的變體),點了立即套用。點非當前模式那排
   會連模式一起切過去。header ⚙️ 按鈕的文字由 app.js 在開選單時重算,這裡不碰它
   (views 不 import 組裝根)。 ── */

function swatchHTML(mode, v, on) {
  const name = t("theme.v" + v.id.charAt(0).toUpperCase() + v.id.slice(1));
  return `<button type="button" class="theme-swatch${on ? " on" : ""}"
    data-mode="${mode}" data-variant="${v.id}" style="background:${v.bg};color:${v.text}">
    <span class="theme-swatch-sample">Aa</span>
    <span class="theme-swatch-name">${esc(name)}</span></button>`;
}

function renderThemeSettings() {
  const rows = [
    {el: $("#themeSwatchLight"), mode: "light", variants: theme.LIGHT_VARIANTS},
    {el: $("#themeSwatchDark"), mode: "dark", variants: theme.DARK_VARIANTS},
  ];
  for (const {el, mode, variants} of rows) {
    const cur = theme.variantOf(mode);  // 每排標各自模式記住的變體
    el.innerHTML = variants.map(v => swatchHTML(mode, v, v.id === cur)).join("");
  }
}

function bindThemeSettings() {
  for (const id of ["#themeSwatchLight", "#themeSwatchDark"]) {
    $(id).addEventListener("click", e => {
      const btn = e.target.closest(".theme-swatch");
      if (!btn) return;
      theme.setVariant(btn.dataset.mode, btn.dataset.variant);
      // 點的是另一個模式那排 → 連模式一起切過去(色票是「我要這個樣子」,不是預約)
      if (btn.dataset.mode !== theme.currentMode()) theme.applyTheme(btn.dataset.mode);
      renderThemeSettings();
    });
  }
}

/* ── 背景圖片(裝置本地偏好,套用邏輯在 theme.js,這裡只有 UI)────── */

function renderBackgroundSettings() {
  $("#bg_remove").disabled = !theme.hasBackground();
  const v = theme.bgIntensity();
  $("#bg_dim").value = v;
  $("#bg_dim_val").textContent = v + "%";
}

function bindBackgroundSettings() {
  $("#bg_upload").onclick = () => $("#bg_file").click();
  $("#bg_file").addEventListener("change", async e => {
    const file = e.target.files[0]; e.target.value = "";
    if (!file) return;
    // 重用上傳附件那條壓縮路(長邊 2000px + WebP;GIF/SVG/AVIF 會原檔返回)
    const {file: out} = await compressImage(file);
    if (out.size > theme.BG_MAX_BYTES) { alert(t("bg.tooBig")); return; }
    const dataUrl = await new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result);
      r.onerror = reject;
      r.readAsDataURL(out);
    });
    // localStorage 塞不下(dataURL 比檔案再大 ~37%,配額還被其他偏好分掉)
    // 會拋 QuotaExceededError——對使用者來說跟「圖太大」是同一件事。
    try { theme.setBackgroundImage(dataUrl); } catch { alert(t("bg.tooBig")); return; }
    renderBackgroundSettings();
  });
  $("#bg_remove").onclick = () => { theme.clearBackground(); renderBackgroundSettings(); };
  $("#bg_dim").addEventListener("input", e => {
    theme.setBackgroundIntensity(parseInt(e.target.value, 10));
    $("#bg_dim_val").textContent = e.target.value + "%";
  });
}

/* ── 語系設定:選擇後寫入 localStorage(auto = 移除覆寫、回到自動判別)並重載頁面 ── */

/* ── 帳號:登入方式與變更密碼 ──────────────────────────────────────
   狀態來源是 state.me 的 has_password/has_google(搭 /api/auth/me 回來)。
   「至少留一種登入方式」的真正防線在後端;這裡的顯示/隱藏只是把按不動的
   按鈕先藏起來。「連結 Google」不是 API 呼叫——就是走一次一般的 Google
   登入(callback 依 email 比對自動 link),所以它是個 <a>。 */

async function renderAccount() {
  const me = state.me || {};
  $("#acct_email").value = me.email || "";
  const hasPw = !!me.has_password, hasGoogle = !!me.has_google;
  $("#acct_pw_state").textContent = t(hasPw ? "account.stateOn" : "account.stateOff");
  $("#acct_google_state").textContent = t(hasGoogle ? "account.stateOn" : "account.stateOff");
  // 停用鈕只在「另一種方式還在」時出現(後端反正會擋,藏起來少一次無效點擊)
  $("#acct_pw_disable").style.display = (hasPw && hasGoogle) ? "" : "none";
  $("#acct_google_unlink").style.display = (hasPw && hasGoogle) ? "" : "none";
  // 沒有舊密碼可驗(Google-only)→ 目前密碼欄整組藏起來,標題換成「設定密碼」
  $("#acct_pw_current_wrap").style.display = hasPw ? "" : "none";
  $("#acct_pw_save").textContent = t(hasPw ? "account.save" : "account.setSave");
  // 「連結 Google」要看站台有沒有開 Google 登入(公開設定,登入畫面也在用)
  const linkBtn = $("#acct_google_link");
  linkBtn.style.display = "none";
  if (!hasGoogle) {
    const cfg = await api.getAuthConfig();
    if (activeTab !== "account") return;  // await 期間使用者可能已切走
    if (cfg.google_enabled) linkBtn.style.display = "";
  }
}

function bindAccount() {
  $("#acct_pw_save").onclick = async () => {
    const cur = $("#acct_pw_current").value;
    const nw = $("#acct_pw_new").value, cf = $("#acct_pw_confirm").value;
    if (nw.length < 8) { alert(t("account.pwHint")); return; }
    if (nw !== cf) { alert(t("account.mismatch")); return; }
    const r = await api.changePassword(cur, nw);
    if (r.ok) {
      state.me = {...state.me, has_password: true};
      $("#acct_pw_current").value = $("#acct_pw_new").value = $("#acct_pw_confirm").value = "";
      alert(t("account.changed"));
      renderAccount();
    } else if (r.status === 403) {
      alert(t("account.wrongCurrent"));
    } else {
      alert((await r.json().catch(() => ({}))).detail || t("account.failed"));
    }
  };
  $("#acct_pw_disable").onclick = async () => {
    if (!confirm(t("account.disableConfirm"))) return;
    const r = await api.removePassword();
    if (r.ok) { state.me = {...state.me, has_password: false}; renderAccount(); }
    else alert((await r.json().catch(() => ({}))).detail || t("account.failed"));
  };
  $("#acct_google_unlink").onclick = async () => {
    if (!confirm(t("account.unlinkConfirm"))) return;
    const r = await api.unlinkGoogle();
    if (r.ok) { state.me = {...state.me, has_google: false}; renderAccount(); }
    else alert((await r.json().catch(() => ({}))).detail || t("account.failed"));
  };
}

function renderLanguageSettings() {
  $("#lang_select").value = localStorage.getItem("gv-lang") || "auto";
  $("#ai_lang_select").value = localStorage.getItem("gv-ai-lang") || "auto";
}

function bindLanguageSettings() {
  // 介面語言跟著**帳號**走(2026-08-17):真相在伺服器(users.json 的 lang),
  // localStorage 的 gv-lang 降級成首繪快取(boot 時與伺服器對帳,見 app.js)。
  // 先存伺服器、成功才動本地——反過來的話存檔失敗會讓兩邊分岔,
  // 下次 boot 對帳又被伺服器改回去,使用者只會覺得「設定不會保存」。
  $("#lang_select").onchange = async e => {
    const v = e.target.value;
    try {
      const r = await api.putLang(v === "auto" ? null : v);
      if (!r.ok) throw new Error();
    } catch {
      alert(t("actions.saveFailed"));
      renderLanguageSettings();  // 下拉退回原值
      return;
    }
    if (v === "auto") localStorage.removeItem("gv-lang");
    else localStorage.setItem("gv-lang", v);
    location.reload();  // 整頁字串(含 views 動態渲染)最單純的全面套用方式
  };
  // AI 生成語言(i18n.js:aiLang()):不用 reload——這個值在每次 AI 請求當下才被讀取,
  // 沒有整頁字串要重套。auto = 移除覆寫,跟隨介面語言。
  $("#ai_lang_select").onchange = e => {
    const v = e.target.value;
    if (v === "auto") localStorage.removeItem("gv-ai-lang");
    else localStorage.setItem("gv-ai-lang", v);
  };
}

/* ── 複習一輪的張數:選了就存,不 reload——值在每次抽卡當下才讀(同 AI 生成語言)。
   選項與預設值的真相在 config.js(SRS_SIZES / srsSize),後端 app/srs.py 才是最後
   夾範圍的那一關;這裡只負責讓使用者選。 ── */

function renderSrsSettings() {
  const sel = $("#srs_size_select");
  const cur = srsSize();
  sel.innerHTML = SRS_SIZES.map(n =>
    `<option value="${n}"${n === cur ? " selected" : ""}>${t("pref.srsSizeN", {n})}</option>`).join("");
}

function bindSrsSettings() {
  $("#srs_size_select").onchange = e => {
    localStorage.setItem("gv-srs-size", e.target.value);
  };
}

/* ── Logo/標語:文字覆寫或整個隱藏,跟主題/字體/語系同層級的個人化設定(存 localStorage,重載頁面套用) ── */

function renderBrandingSettings() {
  $("#brand_hide").checked = localStorage.getItem("gv-logo-hidden") === "1";
  $("#brand_text").value = localStorage.getItem("gv-logo-text") || "";
  $("#brand_sub").value = localStorage.getItem("gv-logo-sub") || "";
}

function bindBrandingSettings() {
  $("#brand_save").onclick = () => {
    if ($("#brand_hide").checked) localStorage.setItem("gv-logo-hidden", "1");
    else localStorage.removeItem("gv-logo-hidden");
    const text = $("#brand_text").value.trim();
    if (text) localStorage.setItem("gv-logo-text", text); else localStorage.removeItem("gv-logo-text");
    const sub = $("#brand_sub").value.trim();
    if (sub) localStorage.setItem("gv-logo-sub", sub); else localStorage.removeItem("gv-logo-sub");
    location.reload();
  };
}

/* ── 外掛模組管理:型錄由後端封裝 manifest 提供(名稱/描述/版本已依介面語言
   挑好,不再查前端 i18n 字典)。依分類分區,區內左右兩欄卡片(窄螢幕收成單欄,
   見 main.css 的 .plugmgr-grid)。點卡片本體開詳細頁(介紹/圖片/GIF);
   admin 另有上傳封裝與壞封裝清單。 ── */

// 分區的呈現順序;後端若出現不在這份清單裡的新分類,附列在最後(不吞掉)
const PLUGIN_CATEGORIES = ["ai-tool", "field-template", "template-enhancement"];

function pluginCategoryOf(p) { return p.category || "ai-tool"; }

// template-enhancement 是承載格式先行的分類(manifest 已支援、主程式還沒有實作),
// 型錄照列但安裝鈕鎖住——比起藏起來,「即將推出」才說得清楚這個分類是什麼。
const isEnhancement = p => pluginCategoryOf(p) === "template-enhancement";

function pluginBadges(p) {
  let out = "";
  if (p.installed) out += `<span class="tplmgr-badge">${esc(t("plugin.installed"))}</span>`;
  if (p.installed && p.enabled === false)
    out += `<span class="tplmgr-badge plugmgr-badge-off">${esc(t("plugin.disabled"))}</span>`;
  return out;
}

function pluginCardHTML(p) {
  const hasPrompt = "ai_prompt" in (p.config || {});
  return `<div class="plugmgr-card" data-plugin="${esc(p.id)}">
    <div class="plugmgr-head">
      <span class="tplmgr-name plugmgr-title" role="button" tabindex="0" data-act="detail"
        >${esc(p.name || p.id)}<span class="plugmgr-ver">v${esc(p.version || "?")}</span>${pluginBadges(p)}</span>
      ${isEnhancement(p)
        ? `<button type="button" class="btn" disabled title="${esc(t("plugin.comingSoon"))}">${esc(t("plugin.install"))}</button>`
        : `<button type="button" class="btn ${p.installed ? "danger" : "primary"}" data-act="toggle">
             ${esc(p.installed ? t("plugin.uninstall") : t("plugin.install"))}</button>`}
    </div>
    <div class="hint plugmgr-desc">${esc(p.description || "")}</div>
    ${p.enhances ? `<div class="hint plugmgr-desc">${esc(t("plugin.enhancesLabel", {name: p.enhances}))}</div>` : ""}
    ${p.installed && !isEnhancement(p) ? `<div class="tplmgr-editor-actions">
      <button type="button" class="btn" data-act="enable">
        ${esc(p.enabled === false ? t("plugin.enable") : t("plugin.disable"))}</button>
      <span class="spacer"></span>
      ${p.source === "site" && state.me?.is_admin
        ? `<button type="button" class="btn danger" data-act="del-pkg">${esc(t("plugin.deletePkg"))}</button>` : ""}
    </div>` : ""}
    ${p.installed && hasPrompt ? `<div class="tplmgr-editor" data-plugin-cfg="${esc(p.id)}">
      <label class="aimgr-label">${esc(t("plugin.aiPrompt"))}</label>
      <textarea class="tpl-ai-prompt" rows="5" placeholder="${esc(t("tplmgr.aiPromptPh"))}">${esc(p.config.ai_prompt || "")}</textarea>
      <div class="tplmgr-editor-actions">
        <span class="spacer"></span>
        <button type="button" class="btn primary" data-act="save-cfg">${esc(t("tplmgr.save"))}</button>
      </div>
    </div>` : ""}
  </div>`;
}

/* ── 「自己做一個外掛」教學 ────────────────────────────────────────
   對象是**不會寫程式**的使用者,所以整篇的骨幹是「別手寫 JSON」:欄位樣板可以
   在 UI 裡調好再用 設定 → 欄位樣板 → 匯出樣板 拿到現成的 JSON,貼進 manifest 的
   template 就好。這條路能成立是因為 `_clean_template()` 只挑它認得的鍵、忽略其餘
   (匯出檔多帶的 id/builtin 不會讓驗證失敗),而 id 一律強制蓋成外掛 id。

   收在 <details> 裡預設收合:它是一次性的閱讀,天天用這一頁的人不該每次都被
   它推開型錄。範例 manifest **不進 i18n**——那是資料不是介面文字,而且 12 份
   翻譯裡各存一份 JSON 一定會漂移(同「外掛名稱的真相在 manifest 不在 i18n」)。

   ⚠ 範例裡的三個值跟後端驗證是綁死的,改程式時要一起改:
   id 必須等於資料夾名(load_package)、name.en 必填(_clean_lang_map 的 fallback)、
   欄位 key 限 ^[a-z][a-z0-9_]{0,31}$(sanitize.clean_template_fields)。 */
const PLUGIN_REPO = "https://github.com/diegochen-tw/jargon-vault";

const SAMPLE_MANIFEST = `{
  "manifest_version": 1,
  "id": "coffee-note",
  "version": "1.0.0",
  "category": "field-template",
  "name": {
    "en": "Coffee Tasting Notes",
    "zh-Hant": "咖啡風味筆記"
  },
  "description": {
    "en": "Record beans, roast level and tasting notes.",
    "zh-Hant": "記錄豆種、烘焙度與風味描述。"
  },
  "template": {
    "name": "咖啡風味筆記",
    "ai_input_mode": "name",
    "ai_prompt": "使用者會給一支咖啡豆的名字,請填寫各欄位。",
    "fields": [
      {"key": "origin", "label": "產地", "placeholder": "例如 衣索比亞 耶加雪菲"},
      {"key": "roast",  "label": "烘焙度", "placeholder": "淺焙 / 中焙 / 深焙"},
      {"key": "flavor", "label": "風味", "placeholder": "柑橘、莓果、花香…"}
    ]
  }
}`;

function pluginGuideHTML() {
  const step = (n, title, desc) => `<div class="plugguide-step">
    <span class="plugguide-n">${n}</span>
    <div><b>${esc(title)}</b><div class="hint">${esc(desc)}</div></div>
  </div>`;
  return `<details class="plugguide">
    <summary>${esc(t("plugin.guideTitle"))}</summary>
    <div class="plugguide-body">
      <div class="hint">${esc(t("plugin.guideDiyHint"))}</div>
      ${step(1, t("plugin.guideStep1"), t("plugin.guideStep1Desc"))}
      ${step(2, t("plugin.guideStep2"), t("plugin.guideStep2Desc"))}
      <div class="plugguide-code">
        <button type="button" class="btn outline" id="plugGuideCopy">${esc(t("plugin.guideCopy"))}</button>
        <pre>${esc(SAMPLE_MANIFEST)}</pre>
      </div>
      ${step(3, t("plugin.guideStep3"), t("plugin.guideStep3Desc"))}
      ${step(4, t("plugin.guideStep4"), t("plugin.guideStep4Desc"))}
      <div class="tplmgr-editor-actions">
        <a class="btn outline" href="${PLUGIN_REPO}/tree/main/official_plugins"
           target="_blank" rel="noopener noreferrer">${esc(t("plugin.guideSamples"))}</a>
        <a class="btn outline" href="${PLUGIN_REPO}/pulls"
           target="_blank" rel="noopener noreferrer">${esc(t("plugin.guidePr"))}</a>
      </div>
      <div class="hint plugguide-rules">${esc(t("plugin.guideRules"))}</div>
    </div>
  </details>`;
}

// admin 專屬:上傳封裝(zip)+ 壞封裝清單。scan_errors 只有 admin 拿得到
// (後端就不會回給一般使用者),所以這一段對非 admin 自然不渲染。
function pluginAdminHTML() {
  if (!state.me?.is_admin) return "";
  const errs = state.pluginScanErrors || [];
  return `<div class="plugmgr-admin">
    <div class="tplmgr-toolbar">
      <button type="button" class="btn outline" id="plugUploadBtn">${esc(t("plugin.upload"))}</button>
      <input type="file" id="plugUploadFile" accept=".zip" style="display:none">
    </div>
    ${errs.length ? `<div class="hint plugmgr-errs">
      <b>${esc(t("plugin.scanErrTitle"))}</b>
      ${errs.map(e => `<div>・${esc(e.dir)} — ${esc(e.reason)}</div>`).join("")}
    </div>` : ""}
  </div>`;
}

async function toggleInstall(p) {
  if (p.installed) {
    // 樣板類外掛的解除會把樣板從欄位樣板清單移除,確認文案要講清楚後果。
    // 有名詞正掛在這個樣板上時再多講一句「有 N 筆使用中」——那些名詞會變成
    // 孤兒(欄位標題退化;資料不消失,健康度檢查會列出、也能批次轉換)。
    // count 來自 GET /api/templates(state.allTemplates),樣板類外掛的
    // 樣板 id 就是外掛 id。
    const isTpl = pluginCategoryOf(p) === "field-template";
    const confirmKey = isTpl ? "plugin.uninstallTplConfirm" : "plugin.uninstallConfirm";
    let msg = t(confirmKey, {name: p.name || p.id});
    const inUse = isTpl ? (state.allTemplates.find(x => x.id === p.id)?.count || 0) : 0;
    if (inUse > 0) msg += "\n\n" + t("plugin.uninstallInUse", {n: inUse});
    if (!confirm(msg)) return false;
  }
  return actions.setPluginInstalled(p.id, !p.installed);
}

/* 詳細頁 modal:介紹(純文字,esc 後只把換行轉 <br>——第三方封裝的 intro 是
   不可信輸入,**絕不走 markdown.js**,也別讓渲染器多出「這段內容屬於誰」的概念)
   + 圖片/GIF(<img> 直排,GIF 原生會動)+ 安裝/停用操作。節點動態建、關閉即移除,
   不在 index.html 佔一個常駐單例——這一頁只有進外掛頁才用得到。 */
function closePluginDetail() {
  const ov = $("#pluginDetailModal");
  if (ov) { ov.remove(); popModalState(); }
}

async function openPluginDetail(id) {
  const d = await api.getPluginDetail(id);
  if (!d) return;
  const ov = document.createElement("div");
  ov.className = "modal-overlay show";
  ov.id = "pluginDetailModal";
  const imgs = (d.images || []).map(fn =>
    `<img class="plugmgr-detail-img" loading="lazy"
       src="/api/plugins/${encodeURIComponent(id)}/assets/${encodeURIComponent(fn)}" alt="">`).join("");
  ov.innerHTML = `<div class="modal-box plugmgr-detail">
    <button type="button" class="modal-close" data-act="close">✕</button>
    <h3>${esc(d.name || d.id)} <span class="plugmgr-ver">v${esc(d.version || "?")}</span>${pluginBadges(d)}</h3>
    <div class="hint">${esc(t(`plugin.cat.${pluginCategoryOf(d)}`))}${
      d.enhances ? ` · ${esc(t("plugin.enhancesLabel", {name: d.enhances}))}` : ""}</div>
    <div class="plugmgr-intro">${esc(d.intro || d.description || "").replace(/\n/g, "<br>")}</div>
    ${imgs}
    <div class="tplmgr-editor-actions">
      ${isEnhancement(d)
        ? `<button type="button" class="btn" disabled title="${esc(t("plugin.comingSoon"))}">${esc(t("plugin.install"))}</button>`
        : `<button type="button" class="btn ${d.installed ? "danger" : "primary"}" data-act="toggle">
             ${esc(d.installed ? t("plugin.uninstall") : t("plugin.install"))}</button>
           ${d.installed ? `<button type="button" class="btn" data-act="enable">
             ${esc(d.enabled === false ? t("plugin.enable") : t("plugin.disable"))}</button>` : ""}`}
      <span class="spacer"></span>
    </div>
  </div>`;
  document.body.appendChild(ov);
  pushModalState(closePluginDetail);
  ov.onclick = e => { if (e.target === ov) closePluginDetail(); };
  ov.querySelector('[data-act="close"]').onclick = closePluginDetail;
  const toggleBtn = ov.querySelector('[data-act="toggle"]');
  if (toggleBtn) toggleBtn.onclick = async () => {
    if (await toggleInstall(d)) { closePluginDetail(); renderPluginManager(); }
  };
  const enableBtn = ov.querySelector('[data-act="enable"]');
  if (enableBtn) enableBtn.onclick = async () => {
    if (await actions.setPluginEnabled(id, d.enabled === false)) { closePluginDetail(); renderPluginManager(); }
  };
}

function renderPluginManager() {
  const box = $("#pluginList");
  const present = [...new Set(state.plugins.map(pluginCategoryOf))];
  const cats = [...PLUGIN_CATEGORIES.filter(c => present.includes(c)),
                ...present.filter(c => !PLUGIN_CATEGORIES.includes(c))];
  // 順序:型錄 → 教學 → admin 的上傳區。進這一頁的人十次有九次是要裝/停用某個外掛,
  // 型錄擺第一;製作教學與上傳是「看完教學才會用到」的動作,所以照那個先後排在最後。
  // (教學是收合的 <details>,對所有人都顯示;上傳區只有 admin 渲染得出來。)
  box.innerHTML = cats.map(cat => `<div class="plugmgr-cat">
    <div class="plugmgr-cat-title">${esc(t(`plugin.cat.${cat}`))}</div>
    <div class="plugmgr-grid">
      ${state.plugins.filter(p => pluginCategoryOf(p) === cat).map(pluginCardHTML).join("")}
    </div>
  </div>`).join("") + pluginGuideHTML() + pluginAdminHTML();

  box.querySelectorAll(".plugmgr-card[data-plugin]").forEach(card => {
    const id = card.dataset.plugin;
    const p = state.plugins.find(x => x.id === id);
    const bind = (sel, fn) => { const el = card.querySelector(sel); if (el) el.onclick = fn; };
    bind('[data-act="toggle"]', async () => {
      if (await toggleInstall(p)) renderPluginManager();
    });
    bind('[data-act="enable"]', async () => {
      if (await actions.setPluginEnabled(id, p.enabled === false)) renderPluginManager();
    });
    bind('[data-act="del-pkg"]', async () => {
      if (!confirm(t("plugin.deletePkgConfirm", {name: p.name || p.id}))) return;
      if (await actions.deleteSitePlugin(id)) renderPluginManager();
    });
    const title = card.querySelector('[data-act="detail"]');
    title.onclick = () => openPluginDetail(id);
    title.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openPluginDetail(id); } };
  });
  box.querySelectorAll("[data-plugin-cfg]").forEach(ed => {
    const id = ed.dataset.pluginCfg;
    ed.querySelector('[data-act="save-cfg"]').onclick = async () => {
      const ok = await actions.savePluginConfig(id, {ai_prompt: ed.querySelector("textarea").value});
      if (ok) alert(t("plugin.saved"));
    };
  });
  // 複製範例 manifest。clipboard API 在非 HTTPS 會失敗 → 退回「把 <pre> 選起來」
  // 讓使用者自己按 Ctrl+C(同團隊庫邀請網址的取捨,不用 alert 打斷)。
  const copyBtn = $("#plugGuideCopy");
  if (copyBtn) copyBtn.onclick = async () => {
    try {
      await navigator.clipboard.writeText(SAMPLE_MANIFEST);
      const orig = copyBtn.textContent;
      copyBtn.textContent = t("plugin.guideCopied");
      setTimeout(() => { copyBtn.textContent = orig; }, 1200);
    } catch {
      const pre = copyBtn.parentElement.querySelector("pre");
      const r = document.createRange();
      r.selectNodeContents(pre);
      const sel = getSelection();
      sel.removeAllRanges();
      sel.addRange(r);
    }
  };
  const upBtn = $("#plugUploadBtn");
  if (upBtn) {
    const fileInput = $("#plugUploadFile");
    upBtn.onclick = () => fileInput.click();
    fileInput.onchange = async e => {
      const file = e.target.files[0]; e.target.value = "";
      if (!file) return;
      if (await actions.uploadPluginPackage(file)) { alert(t("plugin.uploadOk")); renderPluginManager(); }
    };
  }
}

/* ── 標籤管理:勾選分組、群組分節、選擇性匯出 ── */

/* 加入群組/移出群組住在 renderTagManager 畫出來的區段標題列上(各自貼著它作用的
   那一區),所以綁定不能只在 bindTagToolbar 做一次——每次重繪都是新節點。 */
function bindTagGroupActions() {
  $("#tagmgrGroup").onclick = async () => {
    const tags = [...checkedTags];
    if (!tags.length) { alert(t("tagmgr.checkFirstGroup")); return; }
    const groups = [...new Set(state.allTags.map(x => x.group).filter(Boolean))];
    const hint = groups.length ? t("tagmgr.existingGroups", {list: groups.join("、")}) : "";
    const g = prompt(t("tagmgr.groupPrompt", {hint, n: tags.length}));
    if (g === null) return;
    if (!g.trim()) { alert(t("tagmgr.groupEmpty")); return; }
    if (await actions.assignTagGroup(g.trim(), tags)) checkedTags.clear();
  };
  $("#tagmgrUngroup").onclick = async () => {
    const tags = [...checkedTags];
    if (!tags.length) { alert(t("tagmgr.checkFirstUngroup")); return; }
    if (await actions.assignTagGroup("", tags)) checkedTags.clear();
  };
}

function bindTagToolbar() {
  const exportChecked = async fmt => {
    const tags = [...checkedTags];
    if (!tags.length) { alert(t("tagmgr.checkFirstExport")); return; }
    try { await api.downloadExport(fmt, {tags}); } catch { alert(t("transfer.exportFailed")); }
  };
  $("#tagmgrExportJson").onclick = () => exportChecked("json");
  $("#tagmgrExportCsv").onclick = () => exportChecked("csv");
  // 純前端過濾(state.allTags 已整份在手上),每個字元即時重繪;
  // type="search" 的原生清除鈕(✕)只發 input 事件,同一條路就涵蓋到。
  $("#tagmgrSearch").oninput = e => { tagFilter = e.target.value; renderTagManager(); };
  $("#tagmgrDissolveAll").onclick = () => actions.dissolveAllGroups();
  $("#tagdupScan").onclick = runTagDupScan;
  $("#autogroupRun").onclick = runAutogroup;
  $("#purgeRun").onclick = runPurge;
}

/* ── 刪除內容(危險區):刪全部名詞,或只刪某個群組的名詞。永久、不可復原 ── */

// 範圍下拉:第一項「全部名詞」(value=""),其餘為各群組。跟著標籤清單一起刷新,
// 盡量保留使用者原本選的範圍。
function renderPurgeScope() {
  const sel = $("#purgeScope");
  if (!sel) return;
  const groups = [...new Set(state.allTags.map(x => x.group).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b));
  const prev = sel.value;
  sel.innerHTML = `<option value="">${esc(t("tagmgr.purgeScopeAll"))}</option>`
    + groups.map(g => `<option value="${esc(g)}">${esc(t("tagmgr.purgeScopeGroup", {g}))}</option>`).join("");
  if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
}

async function runPurge() {
  const scope = $("#purgeScope").value;  // "" = 全部名詞
  const label = scope ? t("tagmgr.purgeScopeGroup", {g: scope}) : t("tagmgr.purgeScopeAll");
  if (!confirm(t("tagmgr.purgeConfirm", {label}))) return;
  // 刪「全部名詞」再多擋一道(不可復原,誤點代價最大)
  if (!scope && !confirm(t("tagmgr.purgeConfirmAll"))) return;
  const btn = $("#purgeRun");
  const result = $("#purgeResult");
  btn.disabled = true;
  try {
    const deleted = await actions.purgeNotes(scope);
    if (deleted === null) return;  // 失敗已在 actions 層 alert
    result.style.display = "block";
    result.innerHTML = `<div class="reclass-status ok">${esc(t("tagmgr.purgeDone", {n: deleted}))}</div>`;
    // 刪掉的名詞不能還留在同頁的健康度/重複偵測清單上——點了只會換來
    // 「名詞不存在」。顯示中的結果才重掃,沒掃過的不動。
    if (cleanupTabStillOpen()) {
      if (healthScanned) await runHealthScan();
      if (dedupScanned) await runDedupScan();
    }
  } finally {
    btn.disabled = false;
  }
}

/* ── 資源回收桶:刪掉的名詞先躺在這裡,保留天數由後端決定,過期自動永久刪除 ── */

// 清空的確認要帶筆數:回收桶平常是收起來看不到內容的地方,講清楚會沒掉幾筆才有意義。
// 保留天數不寫死在前端,一律用後端回的 retention_days(app/config.py 才是唯一來源)。
let trashRetention = 30;

function bindTrash() {
  $("#trashEmpty").onclick = async () => {
    const n = $("#trashList").querySelectorAll("[data-trash]").length;
    if (!n) { alert(t("trash.alreadyEmpty")); return; }
    if (!confirm(t("trash.emptyAllConfirm", {n}))) return;
    if (await actions.emptyTrash() !== null) renderTrash();
  };
}

async function renderTrash() {
  const box = $("#trashList");
  const d = await api.getTrash();
  trashRetention = d.retention_days || trashRetention;
  $("#trashDesc").textContent = t("trash.desc", {days: trashRetention});
  if (!trashTabStillOpen()) return;  // await 期間使用者可能已經切走
  if (!d.items.length) {
    box.innerHTML = `<div class="empty">${esc(t("trash.empty"))}</div>`;
    return;
  }
  box.innerHTML = d.items.map(it => {
    const tags = it.tags.length ? `<span class="hint">${esc(it.tags.join("、"))}</span>` : "";
    return `<div class="tplmgr-row" data-trash="${esc(it.id)}">
      <span class="tplmgr-name">${esc(it.name)} ${tags}</span>
      <span class="tagmgr-count">${esc(trashWhen(it.deleted))}</span>
      <button type="button" class="btn primary" data-act="restore">${esc(t("trash.restore"))}</button>
      <button type="button" class="btn danger" data-act="purge">${esc(t("trash.purge"))}</button>
    </div>`;
  }).join("");

  box.querySelectorAll("[data-trash]").forEach(row => {
    const id = row.dataset.trash;
    row.querySelector('[data-act="restore"]').onclick = async () => {
      if (await actions.restoreTrashNote(id)) renderTrash();
    };
    row.querySelector('[data-act="purge"]').onclick = async () => {
      if (!confirm(t("trash.purgeConfirm"))) return;
      if (await actions.purgeTrashNote(id)) renderTrash();
    };
  });
}

// 「刪除於 x/x/x・剩 n 天」:剩餘天數無條件進位,還有半天也算 1 天(顯示 0 會讓人
// 以為已經沒了,但它其實還在;真的過期是後端清掉,前端就不會再列出來)。
function trashWhen(deleted) {
  const secsLeft = deleted + trashRetention * 86400 - Date.now() / 1000;
  const days = Math.max(0, Math.ceil(secsLeft / 86400));
  return t("trash.deletedAt", {d: fmtDate(deleted)}) + "・" + t("trash.daysLeft", {n: days});
}

/* ── 重複偵測與合併 ──
   「先記下來、之後再整理」的後半段:掃出同一個詞被記了好幾次的名詞,一組一組
   讓使用者挑一筆保留、把其餘的併進去。合併只是把東西收成一筆——被併掉的走
   一般刪除進回收桶(判斷錯了 30 天內救得回來),不是永久刪除。 */

// 畫面上有沒有一份掃描結果:同頁的破壞性動作(壓縮/刪除內容)完成後,顯示中的
// 結果已經過期,要自動重掃——旗標存 JS 不猜 DOM(比照審核清單的勾選狀態)。
// 暫離編輯回來「刻意不重掃」是另一回事(單筆、使用者自己修的),互不影響。
let dedupScanned = false;
let healthScanned = false;

function resetDedup() {
  const box = $("#dedupResult");
  box.style.display = "none";
  box.innerHTML = "";
  dedupScanned = false;
}

function bindDedup() {
  $("#dedupScan").onclick = runDedupScan;
}

async function runDedupScan() {
  const btn = $("#dedupScan"), box = $("#dedupResult");
  const orig = btn.textContent;
  btn.disabled = true; btn.textContent = t("dedup.scanning");
  let groups;
  try {
    groups = await api.getDuplicates();
  } finally {
    btn.disabled = false; btn.textContent = orig;
  }
  if (!cleanupTabStillOpen()) return;
  box.style.display = "";
  dedupScanned = true;
  if (!groups.length) {
    box.innerHTML = `<div class="empty">${esc(t("dedup.none"))}</div>`;
    return;
  }
  renderDedupGroups(groups);
}

// 一組 = 一張卡:單選要保留的那筆(預設最近編輯過的,通常內容最完整),
// 其餘打勾的會被併進去。刻意不預設全勾——合併是會動到內容的操作,要使用者
// 自己確認每一筆真的是同一個東西。
function renderDedupGroups(groups) {
  const box = $("#dedupResult");
  box.innerHTML = `<div class="dedup-summary">${esc(t("dedup.found", {n: groups.length}))}</div>` +
    groups.map((g, gi) => `<div class="dedup-group" data-g="${gi}">
      <div class="dedup-group-head">${g.reasons.map(r =>
        `<span class="dedup-why">${esc(t("dedup.reason." + r))}</span>`).join("")}</div>
      ${g.notes.map((n, ni) => `<div class="dedup-item" data-id="${esc(n.id)}">
        <label class="dedup-keep" title="${esc(t("dedup.keepTitle"))}">
          <input type="radio" name="dedup-keep-${gi}" value="${esc(n.id)}" ${ni === 0 ? "checked" : ""}>
        </label>
        <label class="dedup-take" title="${esc(t("dedup.mergeTitle"))}">
          <input type="checkbox" value="${esc(n.id)}">
        </label>
        <button type="button" class="dedup-name" data-open="${esc(n.id)}">${esc(n.name)}</button>
        <span class="dedup-excerpt">${esc(n.excerpt)}</span>
        <span class="tagmgr-count">${esc(fmtDate(n.updated))}</span>
      </div>`).join("")}
      <div class="dedup-actions">
        <span class="hint">${esc(t("dedup.legend"))}</span>
        <span class="spacer"></span>
        <button type="button" class="btn primary" data-merge="${gi}">${esc(t("dedup.merge"))}</button>
      </div>
    </div>`).join("");

  box.querySelectorAll("[data-open]").forEach(b => b.onclick = () => {
    // views 之間不互相 import:id 放進 store,由組裝根(app.js)轉給詳細頁
    state.pendingNoteId = b.dataset.open;
    closeSettings();
    emit("open-note");
  });
  box.querySelectorAll("[data-merge]").forEach(b => b.onclick = () => mergeGroup(b, groups));
}

async function mergeGroup(btn, groups) {
  const groupEl = btn.closest(".dedup-group");
  const keep = groupEl.querySelector('.dedup-keep input:checked')?.value;
  const take = [...groupEl.querySelectorAll(".dedup-take input:checked")]
    .map(i => i.value).filter(id => id !== keep);
  if (!keep || !take.length) { alert(t("dedup.pickFirst")); return; }
  const nameOf = id => groups.flatMap(g => g.notes).find(n => n.id === id)?.name || id;
  if (!confirm(t("dedup.mergeConfirm", {keep: nameOf(keep), n: take.length}))) return;

  btn.disabled = true;
  const report = await actions.mergeNotes(keep, take);
  btn.disabled = false;
  if (!report) return;
  let msg = t("dedup.mergeDone", {n: report.merged});
  if (report.relinked) msg += t("dedup.mergeRelinked", {n: report.relinked});
  // 被放棄的欄位值一定要講出來:合併的承諾是「不靜默丟資料」,靜靜地選了一邊
  // 就違背了這個承諾(內容本身還在回收桶那份裡,但使用者得先知道有這回事)
  if (report.dropped_fields.length) {
    msg += "\n\n" + t("dedup.mergeDropped", {n: report.dropped_fields.length}) + "\n"
      + report.dropped_fields.map(d => `${d.name} / ${d.key}:${d.value}`).join("\n");
  }
  alert(msg);
  await runDedupScan();
}

/* ── 內容健康度檢查 ──────────────────────────────────────────────────
   規則全在後端(app/health.py),這裡只負責把 groups 畫出來。整段**唯讀**:
   每一類問題的修法都不一樣,而且多半只有使用者知道要修成什麼樣,所以這裡
   只提供「跳到那筆名詞」的入口,不做任何一鍵修復(理由見 app/health.py 檔頭)。 */

function resetHealth() {
  const box = $("#healthResult");
  box.style.display = "none";
  box.innerHTML = "";
  healthScanned = false;
}

function bindHealth() {
  $("#healthScan").onclick = runHealthScan;
}

async function runHealthScan() {
  const btn = $("#healthScan"), box = $("#healthResult");
  const orig = btn.textContent;
  btn.disabled = true; btn.textContent = t("health.scanning");
  let report;
  try {
    report = await api.getContentHealth();
  } finally {
    btn.disabled = false; btn.textContent = orig;
  }
  if (!cleanupTabStillOpen()) return;
  box.style.display = "";
  if (!report) {
    box.innerHTML = `<div class="reclass-status err">${esc(t("health.failed"))}</div>`;
    return;
  }
  healthScanned = true;
  renderHealthReport(report);
}

// 掃過的量一定要寫出來:「沒有發現問題」在庫是空的時候也會出現,不講掃了什麼
// 的話,使用者沒辦法分辨「真的很健康」與「根本沒掃到東西」。
function renderHealthReport(report) {
  const box = $("#healthResult");
  const total = report.groups.reduce((s, g) => s + g.count, 0);
  const scanned = t("health.scanned", {notes: report.notes, assets: report.assets});
  if (!total) {
    box.innerHTML = `<div class="reclass-status ok">${esc(t("health.none"))}</div>
      <div class="dedup-summary">${esc(scanned)}</div>`;
    return;
  }
  box.innerHTML = `<div class="dedup-summary">${esc(scanned)}　${
    esc(t("health.found", {n: total}))}</div>` + report.groups.map(healthGroupHTML).join("");

  // 點一筆有問題的名詞 = 直接開編輯器修它(不是開唯讀的詳細頁)——這一頁的
  // 每一列都是「這裡壞了」,使用者接下來要做的事只有一件。孤兒檔沒有 note_id,
  // 那一類不會產生這種按鈕。
  box.querySelectorAll("[data-open]").forEach(b => b.onclick = () => editNoteFromSettings(b.dataset.open));
  box.querySelectorAll("[data-clean]").forEach(b => b.onclick = () => runHealthClean(b));
  box.querySelectorAll("[data-retarget]").forEach(b => b.onclick = () => runHealthRetarget(b));
}

// 孤兒樣板的批次轉換:把「template 指向不存在樣板」的名詞整批轉回預設樣板。
// 欄位值原地保留(變成殘留欄位照樣顯示),不動 updated、不寫歷史(後端保證)。
async function runHealthRetarget(btn) {
  const n = Number(btn.closest(".health-group").querySelector(".health-count").textContent) || 0;
  if (!confirm(t("health.retargetConfirm", {n}))) return;
  btn.disabled = true;
  let r;
  try {
    r = await api.retargetTemplate("", "jargon-default");
  } finally {
    btn.disabled = false;
  }
  if (!r || !r.ok) { alert(t("health.retargetFailed")); return; }
  const d = await r.json();
  alert(t("health.retargetDone", {n: d.count}));
  await actions.refreshAll();
  if (cleanupTabStillOpen()) await runHealthScan();
}

/* ── 從健康度清單直接編輯:開編輯器,關掉再原樣回到這份清單 ──────────
   「哪一筆壞了」與「修好它」中間本來隔著:關設定 → 回列表找到那筆 → 開編輯器 →
   修完再從頭把設定走一遍,而清單上通常不只一筆。所以這裡比照複習彈窗的暫離編輯
   (views/srs.js:editCurrent):收起設定 modal 但**什麼都不清掉**,編輯器一關掉就
   回來,接著點下一筆。
   ⚠ 回來時刻意**不重掃**:重掃會把整份清單換掉、捲動位置歸零,而使用者正打算
   沿著清單一筆一筆往下修。已經修好的那幾筆會留在清單上,直到他自己再按一次
   「開始檢查」——留著一筆已修好的,比把他正在走的那份清單抽掉好得多。 */

let pausedScroll = 0;   // 收起設定前的捲動位置(.modal-box 才是捲動的那一層)

async function editNoteFromSettings(id) {
  // ⚠ 整筆內容要自己讀回來交給 list.js:那筆名詞不保證出現在目前的搜尋結果裡
  // (健康度掃的是全庫,列表只有這一頁的篩選結果),只設 state.editing 的話
  // 編輯器會**靜默打不開**——按了沒反應也不報錯,同 srs.js:editCurrent 那個坑。
  const note = await actions.reloadNote(id);
  if (!note) { alert(t("health.gone")); return; }
  state.settingsPaused = true;
  pausedScroll = $(".settings-box").scrollTop;
  $("#settingsModal").classList.remove("show");
  popModalState();
  state.editing = id;
  state.creating = false;
  state.editingNote = note;
  emit("results-changed");   // 編輯器由 list.js 依 state 渲染,不通知它根本不會出現
}

// 編輯器關掉 → 原樣回到設定。清單、掃描結果、分頁都還在 DOM 裡(收起來時只拿掉
// 了 .show),唯一救不回來的是捲動位置:display:none 會把它歸零,所以自己存一份。
function resumeSettings() {
  if (!state.settingsPaused) return;
  state.settingsPaused = false;
  // 手機版的篩選抽屜 z-index 高過 modal,不收起來會蓋住彈窗(抄複習彈窗)
  $("#dirSidebar").classList.remove("show");
  $("#dirBackdrop").classList.remove("show");
  pushModalState(closeSettings);
  $("#settingsModal").classList.add("show");
  $(".settings-box").scrollTop = pausedScroll;
}

// 可以一鍵清掉的類別。**只有這三類**——它們的正確修法是唯一的(把指不到東西的
// 引用拿掉、把沒人引用的檔案清掉)。其餘各類只有使用者知道該怎麼修,猜錯就是
// 破壞內容,所以不提供按鈕(後端的 health.CLEANABLE 是同一份清單的真相來源,
// 這裡多送/少送都不會出事:後端只認它自己那份)。
const CLEANABLE_KINDS = ["missing_asset", "missing_embed", "orphan_file"];

// ⚠ 每一類都**必須**有 health.hint.<kind>:t() 沒有「找不到就當空字串」的退路
// (缺 key 會把 key 本身印出來),而且這一頁的每一列都需要一句「這是什麼、
// 該怎麼辦」——少了它,使用者看到「孤兒檔 3」只會不知道能不能刪。
function healthGroupHTML(g) {
  // 後端已經按「壞掉 → 浪費 → 待整理」排好序,前端不再自己排一次
  const hidden = g.count - g.items.length;
  let clean = CLEANABLE_KINDS.includes(g.kind)
    ? `<button type="button" class="btn health-clean" data-clean="${esc(g.kind)}">${
        esc(t("health.clean"))}</button>` : "";
  // missing_template 專屬:批次轉回預設樣板。不走 CLEANABLE 的自動清理
  // (那是「正確修法唯一」的類別),這是一個明確的使用者動作——
  // POST /api/templates/retarget,from_id 留空 = 全部孤兒(明細有 50 筆截斷,
  // 前端湊不齊 id,由後端算差集)。
  if (g.kind === "missing_template") {
    clean = `<button type="button" class="btn health-clean" data-retarget="1">${
      esc(t("health.retargetAll"))}</button>`;
  }
  return `<div class="health-group">
      <div class="health-group-head">
        <span class="health-sev ${esc(g.severity)}">${esc(t("health.sev." + g.severity))}</span>
        <span class="health-kind">${esc(t("health.kind." + g.kind))}</span>
        <span class="health-count">${g.count}</span>
      </div>
      <div class="health-hint">${esc(t("health.hint." + g.kind))}</div>
      ${g.items.map(it => healthItemHTML(it, g.kind)).join("")}
      ${hidden > 0 ? `<div class="health-more">${esc(t("health.more", {n: hidden}))}</div>` : ""}
      ${clean ? `<div class="health-actions">${clean}</div>` : ""}
    </div>`;
}

// 清理一類問題:先打包成 ZIP 存證 → 後端刪除 → 前端立刻下載那包 → 重掃。
// ⚠ 確認對話框**一定要講出那個偽陽性**:正在新建、還沒儲存的名詞,它的附件
// 看起來就是孤兒。使用者是唯一知道「我剛剛是不是正在開著編輯器」的人。
async function runHealthClean(btn) {
  const kind = btn.dataset.clean;
  const n = Number(btn.closest(".health-group").querySelector(".health-count").textContent) || 0;
  if (!confirm(t("health.cleanConfirm", {n, kind: t("health.kind." + kind)})
               + "\n\n" + t("health.cleanNote"))) return;

  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = t("health.cleaning");
  let r;
  try {
    r = await api.cleanupContentHealth([kind]);
  } finally {
    btn.disabled = false; btn.textContent = orig;
  }
  if (!r) { alert(t("health.cleanFailed")); return; }
  if (!r.name) { await runHealthScan(); return; }   // 重掃後發現沒東西可清

  // 下載存證。用臨時 <a download> 而不是 location.href:後者在某些瀏覽器會被
  // 當成離開頁面,設定 modal 的狀態就沒了。ZIP 仍留在伺服器上,這次下載失敗
  // 也還救得回來(那包裡是**已經被刪掉**的東西)。
  const a = document.createElement("a");
  a.href = api.healthCleanupDownloadUrl(r.name);
  a.download = r.name;
  document.body.appendChild(a);
  a.click();
  a.remove();

  alert(t("health.cleanDone", {
    files: r.removed_files, refs: r.removed_refs,
    size: fmtBytes(r.bytes), name: r.name,
  }));
  // 附件路徑變了,卡片縮圖要跟著刷新(同批次圖片壓縮的理由)
  await actions.refreshAll();
  if (cleanupTabStillOpen()) await runHealthScan();
}

function healthItemHTML(it, kind) {
  // 孤兒檔不屬於任何名詞(note_id 是空的),名稱就只是檔名、點了也沒地方可去
  const label = it.note_id
    ? `<button type="button" class="dedup-name" data-open="${esc(it.note_id)}">${esc(it.name)}</button>`
    : `<span class="health-name">${esc(it.name)}</span>`;
  // 斷連結的 detail 是「指向誰」,寫成 [[…]] 才看得出那是連結不是檔名
  const detail = kind === "broken_link" ? `[[${it.detail}]]` : it.detail;
  return `<div class="health-item">
      ${label}
      <span class="dedup-excerpt">${esc(detail)}</span>
      ${it.size ? `<span class="tagmgr-count">${esc(fmtBytes(it.size))}</span>` : ""}
    </div>`;
}

/* ── 圖片壓縮:上傳前壓縮的開關,以及事後補壓既有圖片附件 ── */

// 開關存 localStorage(比照瀏覽模式/行寬等顯示偏好),預設啟用——
// 未設定過時 getItem 回 null,!== "0" 即為 true(同 detail.js 的 gv-detailnarrow)。
function bindContentSettings() {
  state.imgCompress = localStorage.getItem("gv-imgcompress") !== "0";
  $("#imgCompressToggle").onchange = e => {
    state.imgCompress = e.target.checked;
    localStorage.setItem("gv-imgcompress", state.imgCompress ? "1" : "0");
  };
  $("#imgCompressRun").onclick = runImageCompress;
  $("#shareRevokeAll").onclick = runRevokeAllShares;
}

function renderCleanupSettings() {
  $("#imgCompressToggle").checked = state.imgCompress;
  refreshShareStats();
}

/* ── 公開分享連結:數量與一鍵撤銷(卡片在刪除內容上方)── */

async function refreshShareStats() {
  const line = $("#shareStatsLine");
  line.textContent = "…";
  const s = await api.getShareStats();
  if (!cleanupTabStillOpen()) return;
  const count = s?.count ?? 0;
  line.textContent = t("sharemgr.count", {n: count});
  $("#shareRevokeAll").disabled = count === 0;
}

async function runRevokeAllShares() {
  if (!confirm(t("sharemgr.confirm"))) return;
  const btn = $("#shareRevokeAll");
  btn.disabled = true;
  let d = null;
  try {
    const r = await api.revokeAllShares();
    if (r.ok) d = await r.json();
  } catch { /* 網路錯誤走下面同一條失敗路徑 */ }
  if (!d) {
    btn.disabled = false;
    alert(t("sharemgr.failed"));
    return;
  }
  if (!cleanupTabStillOpen()) return;
  const result = $("#shareRevokeResult");
  result.style.display = "block";
  result.innerHTML = `<div class="reclass-status ok">${esc(t("sharemgr.done", {n: d.revoked}))}</div>`;
  refreshShareStats();  // 歸零後由它把按鈕 disable 住,不在這裡重新啟用
}

// 執行到一半使用者關掉設定或切走分頁 → 中止逐筆迴圈(同 tagsTabStillOpen 的用意)。
// ⚠ 回收桶與清理工具分屬兩個分頁,守衛必須各一支:共用一支的話,await 回來時
// activeTab 一定不等於另一頁的 id,那一頁的內容會靜默地永遠渲染不出來。
function cleanupTabStillOpen() {
  return isOpen() && activeTab === "cleanup";  // 重複偵測、圖片壓縮
}

function trashTabStillOpen() {
  return isOpen() && activeTab === "trash";
}

// 事後補壓既有的圖片附件。壓縮在前端做(後端沒有影像處理相依),存回去走
// api.replaceAsset ——那支端點不動 updated、不寫歷史版本,否則跑一次批次就會
// 把所有名詞的「上次編輯時間」推成現在、順便沖掉真正的編輯歷史。
async function runImageCompress() {
  const btn = $("#imgCompressRun");
  const result = $("#imgCompressResult");
  btn.disabled = true;
  result.style.display = "block";
  result.innerHTML = progressHTML(0, 1, t("imgcomp.running"));
  try {
    // 已經是 .webp 的一律跳過:imagecomp.js 的 SKIP_TYPES 不含 webp,重跑會把
    // 同一張圖再編碼一次(世代損失)。壓過的產出一定是 .webp,使用者自己上傳的
    // webp 本來也不該再壓,用副檔名擋掉剛好兩者都涵蓋,順便讓這個動作可重複執行。
    const todo = (await api.listAssets())
      .filter(a => isImageFile(a.path) && !/\.webp$/i.test(a.path));
    if (!todo.length) {
      result.innerHTML = `<div class="reclass-status ok">${esc(t("imgcomp.none"))}</div>`;
      return;
    }
    let done = 0, changedCount = 0, saved = 0;
    for (const a of todo) {
      if (!cleanupTabStillOpen()) break;
      result.innerHTML = progressHTML(done, todo.length,
        t("imgcomp.running") + ` (${done + 1}/${todo.length})`);
      try {
        const src = "/" + a.path.replace(/^\/+/, "");
        const resp = await fetch(src);
        if (resp.ok) {
          const blob = await resp.blob();
          // compressImage() 讀 file.type 決定要不要動它,包 File 時一定要帶 type
          const file = new File([blob], a.name || a.path.split("/").pop(), {type: blob.type});
          const {file: out, before, after, changed} = await compressImage(file);
          if (changed) {
            const fd = new FormData();
            fd.append("file", out, out.name);
            const r = await api.replaceAsset(a.note_id, a.path.split("/").pop(), fd);
            if (r.ok) { changedCount++; saved += before - after; }
          }
        }
      } catch { /* 單張失敗不中斷整批:壓縮/網路問題不該讓其他圖片也做不成 */ }
      done++;
    }
    // 附件路徑變了,state.results 裡還是舊路徑,不刷新卡片縮圖會變破圖
    await actions.refreshAll();
    result.innerHTML = `<div class="reclass-status ok">${
      esc(t("imgcomp.done", {n: changedCount, size: fmtBytes(Math.max(0, saved))}))}</div>`;
    // 壓縮改變了庫況:健康度的「檔案過大」若還顯示著壓縮前的計數與明細,
    // 要跟著重掃——那一類的 hint 文案正是把使用者導向這顆按鈕的,照著做完
    // 數字卻不動,看起來就像沒生效。沒掃過就不掃(別多跑一趟)。
    if (cleanupTabStillOpen() && healthScanned) await runHealthScan();
  } finally {
    btn.disabled = false;
  }
}

// AI 動作的進度條(重組逐筆、自動分組逐批共用):done/total 已完成的比例 + 一行說明。
function progressHTML(done, total, label) {
  const pct = total ? Math.round(done / total * 100) : 0;
  return `<div class="ai-progress"><div class="ai-progress-bar" style="width:${pct}%"></div></div>
    <div class="ai-progress-label">${esc(label)}</div>`;
}

// 執行到一半使用者關掉設定或切走分頁 → 中止逐筆迴圈(避免對著已關閉的 UI 繼續打 AI)。
// ⚠ 這支守的分頁跟著功能搬家過兩次(標籤管理 → 內容管理 → 標籤管理,2026-08-08
// 搬回來時「內容管理」整個分頁一併移除)。搬家忘了跟著改的話,await 回來時
// activeTab 永遠不等於這裡寫的值,迴圈第一圈就 return,表現成「按了開始分析卻
// 什麼都沒發生」且完全不報錯(同 cleanupTabStillOpen 上方那段註解講的失敗模式)。
function tagsTabStillOpen() {
  return isOpen() && activeTab === "tags";
}

/* ── 標籤相似度重複偵測 ─────────────────────────────────────────────
   「先記下來、之後再整理」在標籤上的後半段:快速捕捉製造最多的垃圾其實不是重複的
   名詞,而是同一個標籤的好幾種寫法(Mes / MES / ＭＥＳ)——名詞至少還會在搜尋時撞在
   一起,標籤變體則是安靜地把同一疊東西拆成兩疊,側欄看起來就是兩個不相干的分類。

   兩層,成本與確定性都不同,所以入口也刻意分開:
     字面層  純字串比對(後端 dedup.find_duplicate_tag_groups),零誤報、瞬間有答案;
     語意層  另一顆按鈕,整份清單送 AI 問「哪些其實是同一個東西」(回焊爐 / Reflow Oven)。
   把語意層綁在同一顆按鈕上的話,只想要那個瞬間答案的人每次都得等 AI 跑完。 */

const TAGDUP_AI_MAX = 800;   // 超過這個數量就不送 AI 了:prompt 會長到模型裝不下

// 掃描結果與展開狀態。⚠ 存在模組層而不是 DOM:合併掉一組之後要把那一組從清單裡
// 拿掉再重繪,而**不是重新掃描**——重掃會把使用者正在逐組處理的清單整個抽掉,
// 語意層那幾組更是重掃就沒了(那是一次真的 AI 呼叫)。同健康度檢查那條決定。
let tagDupGroups = null;

function bindTagDup() {
  $("#tagdupScan").onclick = runTagDupScan;
}

async function runTagDupScan() {
  const btn = $("#tagdupScan"), box = $("#tagdupResult");
  const orig = btn.textContent;
  btn.disabled = true; btn.textContent = t("tagdup.scanning");
  let groups;
  try {
    groups = await api.getTagDuplicates();
  } finally {
    btn.disabled = false; btn.textContent = orig;
  }
  if (!tagsTabStillOpen()) return;
  box.style.display = "";
  tagDupGroups = groups;
  paintTagDup();
}

// 一組 = 一張卡,整套沿用名詞重複偵測的 .dedup-* 視覺語彙(同一件事的兩個層級,
// 長得一樣才不會被當成另一種東西);零新 CSS。
// **checkbox 刻意不預設勾選**,理由同名詞那邊:合併會動到內容,要使用者自己確認
// 每一筆真的是同一個東西。語意層那幾組更需要這一關——那是模型猜的。
function paintTagDup() {
  const box = $("#tagdupResult");
  // 掃完之後使用者可能就在底下的標籤管理把標籤改名/刪掉了,剔除已不存在的
  // (同 paintAutogroupReview 的 live Set);整組只剩一個成員就沒有合併的意義了。
  const live = new Set(state.allTags.map(x => x.name));
  tagDupGroups = (tagDupGroups || [])
    .map(g => ({...g, tags: g.tags.filter(x => live.has(x.name))}))
    .filter(g => g.tags.length > 1);

  const aiBtn = state.aiSettings?.enabled
    ? `<button type="button" class="btn" id="tagdupAiRun">${esc(t("tagdup.aiRun"))}</button>`
    : `<button type="button" class="btn" disabled title="${esc(t("tagdup.aiNeedAi"))}">${
        esc(t("tagdup.aiRun"))}</button><span class="hint">${esc(t("tagdup.aiNeedAi"))}</span>`;
  const aiRow = `<div class="dedup-actions" id="tagdupAiRow"><span class="spacer"></span>${aiBtn}</div>`;

  if (!tagDupGroups.length) {
    box.innerHTML = `<div class="empty">${esc(t("tagdup.none"))}</div>` + aiRow;
    bindTagDupAi();
    return;
  }
  box.innerHTML = `<div class="dedup-summary">${esc(t("tagdup.found", {n: tagDupGroups.length}))}</div>` +
    tagDupGroups.map((g, gi) => `<div class="dedup-group" data-g="${gi}">
      <div class="dedup-group-head">${g.reasons.map(r =>
        `<span class="dedup-why">${esc(t("tagdup.reason." + r))}</span>`).join("")}</div>
      ${g.tags.map((x, xi) => `<div class="dedup-item" data-t="${esc(x.name)}">
        <label class="dedup-keep" title="${esc(t("tagdup.keepTitle"))}">
          <input type="radio" name="tagdup-keep-${gi}" value="${esc(x.name)}" ${xi === 0 ? "checked" : ""}>
        </label>
        <label class="dedup-take" title="${esc(t("tagdup.mergeTitle"))}">
          <input type="checkbox" value="${esc(x.name)}">
        </label>
        <span class="dedup-name">${esc(x.name)}</span>
        <span class="dedup-excerpt">${x.group ? "🗂 " + esc(x.group) : ""}</span>
        <span class="tagmgr-count">${t("tagmgr.noteCount", {n: x.count})}</span>
      </div>`).join("")}
      <div class="dedup-actions">
        <span class="hint">${esc(t("tagdup.legend"))}</span>
        <span class="spacer"></span>
        <button type="button" class="btn primary" data-merge="${gi}">${esc(t("tagdup.merge"))}</button>
      </div>
    </div>`).join("") + aiRow;

  box.querySelectorAll("[data-merge]").forEach(b => b.onclick = () => mergeTagGroup(b));
  bindTagDupAi();
}

function bindTagDupAi() {
  const b = $("#tagdupAiRun");
  if (b) b.onclick = () => runTagDupAi(b);
}

async function mergeTagGroup(btn) {
  const groupEl = btn.closest(".dedup-group");
  const keep = groupEl.querySelector(".dedup-keep input:checked")?.value;
  const absorb = [...groupEl.querySelectorAll(".dedup-take input:checked")]
    .map(i => i.value).filter(n => n !== keep);
  if (!keep || !absorb.length) { alert(t("tagdup.pickFirst")); return; }
  if (!confirm(t("tagdup.mergeConfirm", {keep, n: absorb.length}))) return;

  btn.disabled = true;
  const ok = await actions.mergeTags(keep, absorb);
  btn.disabled = false;
  if (!ok) return;
  // 只把被併掉的那些從手上這份結果裡拿掉再重繪,**不重新掃描**(見模組層變數的註解)。
  // refreshAll 已經把 state.allTags 換新了,paintTagDup 的 live 過濾會順手收掉空組。
  const gone = new Set(absorb);
  tagDupGroups = tagDupGroups.map(g => ({...g, tags: g.tags.filter(x => !gone.has(x.name))}));
  paintTagDup();
}

// 後端錯誤 detail → 要顯示的文字。⚠ 不要退回 `d.detail || t(...)`:標籤管理這兩支
// AI 端點的 detail 是**機器碼**(見 app/routers/ai.py 的 ai_group_tags /
// ai_tag_duplicates),直接顯示出來使用者會看到 "ai_disabled"。
// 已知的碼查在地化訊息;沒對到的(502 模型錯誤帶的是原始技術訊息)接在該流程自己的
// 泛用失敗訊息後面而不是取代它——技術細節對回報問題有用,但不該是使用者唯一看到的。
// 兩支共用同一份對照表:它們的前置檢查一模一樣,各寫一份必然有一天只改到其中一邊。
const AI_TAG_ERR = {ai_disabled: "tagdup.aiDisabled", no_tags: "tagdup.aiNoTags"};
function aiTagError(detail, fallbackKey) {
  const key = AI_TAG_ERR[detail];
  if (key) return t(key);
  return detail ? `${t(fallbackKey)}

${detail}` : t(fallbackKey);
}

// 語意層:整份標籤清單一次送出。回來的組以 reason "ai" 追加在字面層那幾組後面——
// 刻意不跟字面層混在一起,那兩種的確定性差很多,使用者要看得出哪些是模型猜的。
async function runTagDupAi(btn) {
  const names = state.allTags.map(x => x.name);
  if (!names.length) { alert(t("tagdup.aiNone")); return; }
  if (names.length > TAGDUP_AI_MAX) {
    alert(t("tagdup.aiTooMany", {n: names.length, max: TAGDUP_AI_MAX}));
    return;
  }
  const orig = btn.textContent;
  btn.disabled = true; btn.textContent = t("tagdup.aiRunning");
  let groups = null;
  try {
    const r = await api.aiTagDuplicates(names);
    const d = await r.json();
    if (!r.ok) { alert(aiTagError(d.detail, "tagdup.aiFailed")); return; }
    groups = d.groups || [];
  } catch {
    alert(t("tagdup.aiFailed"));
    return;
  } finally {
    btn.disabled = false; btn.textContent = orig;
  }
  if (!tagsTabStillOpen()) return;
  // 字面層已經收在同一組裡的配對不再重複列出(後端也擋過一次整組同鍵的)
  const covered = new Set((tagDupGroups || []).flatMap(g => g.tags.map(x => x.name)));
  const byName = new Map(state.allTags.map(x => [x.name, x]));
  const fresh = groups
    .filter(g => !g.every(n => covered.has(n)))
    .map(g => ({
      reason: "ai", reasons: ["ai"],
      tags: g.map(n => byName.get(n)).filter(Boolean)
        .map(x => ({name: x.name, count: x.count, group: x.group}))
        .sort((a, b) => b.count - a.count),
    }))
    .filter(g => g.tags.length > 1);
  if (!fresh.length) { alert(t("tagdup.aiNone")); return; }
  tagDupGroups = [...(tagDupGroups || []), ...fresh];
  $("#tagdupResult").style.display = "";
  paintTagDup();
}

/* ── AI 自動分組建議:分析「未分組」標籤,逐批請 AI 建議群組,使用者逐項勾選後套用 ── */

const AUTOGROUP_BATCH = 20;  // 每批送給 AI 的標籤數(分批才能顯示進度,也避免單次 prompt 過長)

// 待審核的建議與勾選狀態。⚠ **不放在 DOM 裡**,理由有兩層,而且是版面推出來的不是偏好:
// (1) 三欄格子要求群組標題與標籤列是 .tagmgr-list 的**平鋪兄弟節點**(grid-column:1/-1
//     只對直接子元素有效),所以 DOM 上根本沒有「群組」這個容器——每組的全選按鈕
//     沒辦法用 closest() 找同組成員,只能從資料反查;
// (2) 改建議的群組名要重繪整份清單,而重繪不能弄丟使用者已經調過的勾選。
let autogroupAssign = null;       // {標籤: 建議群組名};null = 目前沒有待審核的建議
let autogroupPicked = new Set();  // 勾選中的標籤名(改名重繪時靠它保住使用者的選擇)

// 第一階段:對未分組標籤逐批請 AI 建議群組,顯示進度,累積成 {標籤: 群組名} 後渲染審核清單。
// 跨批次把「已知群組(既有 + 前面批次已提出)」一併帶入,讓 AI 盡量沿用同一組群組名。
async function runAutogroup() {
  if (!state.aiSettings || !state.aiSettings.enabled) { alert(t("tagmgr.autogroupNeedAi")); return; }
  const ungrouped = state.allTags.filter(x => !x.group).map(x => x.name);
  if (!ungrouped.length) { alert(t("tagmgr.autogroupNoTags")); return; }
  const known = new Set(state.allTags.map(x => x.group).filter(Boolean));
  const btn = $("#autogroupRun");
  const result = $("#autogroupResult");
  btn.disabled = true;
  result.style.display = "block";
  const batches = [];
  for (let i = 0; i < ungrouped.length; i += AUTOGROUP_BATCH) batches.push(ungrouped.slice(i, i + AUTOGROUP_BATCH));
  const assignments = {};  // 標籤 → 建議群組名
  try {
    for (let b = 0; b < batches.length; b++) {
      if (!tagsTabStillOpen()) return;
      result.innerHTML = progressHTML(b, batches.length,
        t("tagmgr.autogroupProgress", {i: b + 1, n: batches.length}));
      const r = await api.groupTags(batches[b], [...known]);
      const d = await r.json();
      if (!r.ok) {
        result.innerHTML = `<div class="reclass-status err">${esc(aiTagError(d.detail, "tagmgr.autogroupError"))}</div>`;
        return;
      }
      for (const [tag, grp] of Object.entries(d.groups || {})) {
        assignments[tag] = grp;
        known.add(grp);
      }
    }
    renderAutogroupReview(assignments);
  } catch {
    result.innerHTML = `<div class="reclass-status err">${esc(t("tagmgr.autogroupError"))}</div>`;
  } finally {
    btn.disabled = false;
  }
}

// 收下一輪建議並畫出來。**這是 autogroupAssign/autogroupPicked 唯一的提交點**——
// ⚠ runAutogroup() 的批次迴圈刻意只寫區域變數 assignments,絕不可以改成邊跑邊寫模組
// 變數:那個迴圈會因為「切走分頁」「HTTP 失敗」中途 return,而畫面上的舊清單不會被
// 清掉,於是留下「狀態是跑到一半的新資料、DOM 是完整的舊資料」這種中間態——接著按
// 群組改名會靜默把舊建議換成半成品,按套用就是套用半成品,而且全程不報錯。
// 在這裡一次換掉,就沒有那個中間態可言。
function renderAutogroupReview(assignments) {
  if (!Object.keys(assignments).length) {
    autogroupAssign = null;
    autogroupPicked.clear();
    $("#autogroupResult").innerHTML = `<div class="reclass-status">${esc(t("tagmgr.autogroupNoResult"))}</div>`;
    return;
  }
  autogroupAssign = assignments;
  // **整包取代,不 merge**:留著上一輪的勾選,會讓這輪沒被建議到的標籤殘留在 Set 裡,
  // 套用時 autogroupAssign[tag] 是 undefined,那些標籤會被歸進一個叫「undefined」的群組。
  autogroupPicked = new Set(Object.keys(assignments));
  paintAutogroupReview();
}

// 建議依群組收攏,群組名照字母序。兩個呼叫端(畫面與套用)共用同一份,
// 免得「畫出來的分組」與「實際套用的分組」有機會不一致。
function autogroupGroups() {
  const byGroup = new Map();
  for (const [tag, grp] of Object.entries(autogroupAssign || {})) {
    if (!byGroup.has(grp)) byGroup.set(grp, []);
    byGroup.get(grp).push(tag);
  }
  return new Map([...byGroup.entries()].sort((a, b) => a[0].localeCompare(b[0])));
}

// 依模組狀態重畫審核清單(改名之後也走這一支)。
// 版面沿用 標籤管理 的三欄格子(.tagmgr-list 那一整套 class),**不另外複製一份格線規則**:
// 這兩份東西在這個 app 裡是同一個物件——「一份依群組分節的標籤清單」,兩份 CSS 一定會漂移
// (同 CLAUDE.md「渲染器絕不複製一份」那條)。響應式(900px→2 欄、560px→1 欄)、
// max-height 的內捲、圓角裁切全部免費繼承;代價是雙向耦合,以後改 .tagmgr-list 的欄數
// 或高度兩邊會一起變——那是目的不是意外。
function paintAutogroupReview() {
  const result = $("#autogroupResult");
  // 分析完之後使用者可能跑去 標籤管理 把某些標籤刪掉/改名了。留著那些標籤的話,
  // 套用時後端 assign_group 會對未登記的標籤整包 raise → 400 → **那一組一個都沒分到**。
  // 先剔除是 renderTagManager() 已經在用的同一招(見那裡的 live Set)。
  const live = new Set(state.allTags.map(x => x.name));
  for (const tag of Object.keys(autogroupAssign)) if (!live.has(tag)) delete autogroupAssign[tag];
  for (const tag of [...autogroupPicked]) if (!live.has(tag)) autogroupPicked.delete(tag);

  const byGroup = autogroupGroups();
  const total = Object.keys(autogroupAssign).length;
  if (!total) {
    result.innerHTML = `<div class="reclass-status">${esc(t("tagmgr.autogroupNoResult"))}</div>`;
    autogroupAssign = null;
    autogroupPicked.clear();
    return;
  }
  // 改名會整段重寫 innerHTML,而 .tagmgr-list 是有 max-height 的捲動容器——不還原的話,
  // 捲到第 8 組去改名,一按確定就被彈回頂端(同設定暫離編輯那個坑)。
  const scrollTop = $("#autogroupList")?.scrollTop || 0;

  let html = `<div class="reclass-status ok">${esc(t("tagmgr.autogroupReviewHint", {n: total, g: byGroup.size}))}</div>`;
  // 總開關留在格子**外面**:進去就變成格子裡的一格了
  html += `<label class="reclass-item reclass-selectall"><input type="checkbox" id="autogroupSelectAll">
    <span class="reclass-note">${esc(t("tagmgr.selectAll"))}</span></label>`;
  html += `<div class="tagmgr-list" id="autogroupList">`;
  for (const [g, tags] of byGroup) {
    // ⚠ 群組頭是 <div> 不是 <label>,而且**不可以**幫每個群組包一層 wrapper:
    // (1) <label> 裡不得巢狀 <button>——不是合法 HTML,而且點「群組改名」會被 label
    //     轉給 checkbox,變成「開了 prompt 順便把整組勾選反轉」,按取消副作用還在;
    // (2) grid-column:1/-1 只對 .tagmgr-list 的直接子元素有效,包一層就會同時毀掉
    //     「標題跨整列」與「標籤三欄流動」。
    html += `<div class="tagmgr-group-head" data-g="${esc(g)}">
      <input type="checkbox" class="tagmgr-check" data-gpick="${esc(g)}" title="${esc(t("tagmgr.selectAll"))}">
      <span class="tagmgr-group-name">🗂 ${esc(g)}</span>
      <span class="tagmgr-count">${t("tagmgr.tagCount", {n: tags.length})}</span>
      <button type="button" class="btn" data-act="rename-group">${esc(t("tagmgr.renameGroup"))}</button>
    </div>`;
    // 標籤列裡只有 checkbox + span,用 <label> 才能點整格都勾得到。
    // 尾巴那個「→ 群組名」已移除:群組名就在上面那一列,同一件事不講兩遍
    // (而且三欄的格子寬度也放不下)。
    html += tags.map(tag => `<label class="tagmgr-row" data-t="${esc(tag)}">
      <input type="checkbox" class="tagmgr-check autogroup-pick" data-tag="${esc(tag)}">
      <span class="tagmgr-name" title="${esc(tag)}">${esc(tag)}</span>
    </label>`).join("");
  }
  html += `</div><div class="reclass-apply-row">
      <button type="button" class="btn primary" id="autogroupApply">${esc(t("tagmgr.autogroupApply"))}</button>
    </div>`;
  result.innerHTML = html;
  $("#autogroupList").scrollTop = scrollTop;

  // 三個勾選 handler 一律「只改 Set,再無條件全量 sync」,不做增量推算——
  // 半勾狀態靠推算一定會漏掉某條路徑,而漏掉的症狀是永遠卡在錯的三態且完全不報錯。
  $("#autogroupSelectAll").onchange = e => {
    autogroupPicked = e.target.checked ? new Set(Object.keys(autogroupAssign)) : new Set();
    syncAutogroupChecks();
  };
  result.querySelectorAll("[data-gpick]").forEach(box => box.onchange = e => {
    for (const tag of autogroupGroups().get(box.dataset.gpick) || []) {
      if (e.target.checked) autogroupPicked.add(tag); else autogroupPicked.delete(tag);
    }
    syncAutogroupChecks();
  });
  result.querySelectorAll(".autogroup-pick").forEach(c => c.onchange = e => {
    if (e.target.checked) autogroupPicked.add(c.dataset.tag); else autogroupPicked.delete(c.dataset.tag);
    syncAutogroupChecks();
  });
  result.querySelectorAll('[data-act="rename-group"]').forEach(btn =>
    btn.onclick = () => renameAutogroupGroup(btn.closest(".tagmgr-group-head").dataset.g));
  $("#autogroupApply").onclick = applyAutogroup;
  syncAutogroupChecks();
}

// 勾選狀態的唯一寫入者(所以 HTML 那邊一律不輸出 checked 屬性)。
// ⚠ indeterminate 是 property 不是 attribute:寫不進 HTML 字串,每次重繪與每次
// change 之後都要重設——從半勾變全勾時要主動設回 false,它不會自己消失。
function syncAutogroupChecks() {
  const result = $("#autogroupResult");
  const byGroup = autogroupGroups();
  const tri = (box, n, total) => {
    box.checked = n > 0 && n === total;
    box.indeterminate = n > 0 && n < total;
  };
  result.querySelectorAll("[data-gpick]").forEach(box => {
    const tags = byGroup.get(box.dataset.gpick) || [];
    tri(box, tags.filter(x => autogroupPicked.has(x)).length, tags.length);
  });
  result.querySelectorAll(".autogroup-pick").forEach(c => { c.checked = autogroupPicked.has(c.dataset.tag); });
  tri($("#autogroupSelectAll"), autogroupPicked.size, Object.keys(autogroupAssign).length);
}

// 改建議的群組名。**純前端、不打任何後端**:群組沒有獨立記錄,它只是每個標籤上的
// group 字串(見 app/tags.py:rename_group 的 docstring),所以這裡改的只是「之後才會
// 傳給 assignTagGroup 的那個字串」。
function renameAutogroupGroup(g) {
  // 提示要同時列出**庫裡既有的真群組**與本輪其他建議:AI 常提「設計工具」而庫裡
  // 已經有「設計」,把建議改名成既有群組正是這功能最有價值的一步(套用時
  // assign_group 的「建立」與「加入」本來就是同一件事),不列出來使用者不會知道。
  const names = [...new Set([
    ...state.allTags.map(x => x.group).filter(Boolean),
    ...Object.values(autogroupAssign),
  ])].filter(x => x !== g);
  const hint = names.length ? t("tagmgr.existingGroups", {list: names.join("、")}) : "";
  const next = prompt(hint + t("tagmgr.renameGroupPrompt", {name: g}), g);
  if (next === null) return;
  const trimmed = next.trim();
  if (!trimmed || trimmed === g) return;
  // 改成另一個建議群組名 = 合併,**刻意不 confirm**:標籤管理那顆確認存在的理由是
  // 真群組改名會跨 N 個標籤寫檔且不可逆,而這裡一個位元組都還沒寫進 tags.json,
  // 想反悔再改回來就好。合併也不會無聲發生——頂端那行的群組數會少一個。
  for (const [tag, grp] of Object.entries(autogroupAssign)) if (grp === g) autogroupAssign[tag] = trimmed;
  paintAutogroupReview();
}

// 第二階段:把勾選的標籤依群組分組,對每個群組呼叫既有的 assignTagGroup(這步才寫檔)。
async function applyAutogroup() {
  if (!autogroupAssign || !autogroupPicked.size) { alert(t("tagmgr.autogroupPickNone")); return; }
  const byGroup = new Map();
  for (const tag of autogroupPicked) {
    const g = autogroupAssign[tag];
    if (!g) continue;
    if (!byGroup.has(g)) byGroup.set(g, []);
    byGroup.get(g).push(tag);
  }
  const btn = $("#autogroupApply");
  btn.disabled = true;
  btn.textContent = t("tagmgr.autogroupApplying");
  let applied = 0;
  for (const [g, tags] of byGroup) {
    if (await actions.assignTagGroup(g, tags)) applied += tags.length;
  }
  // DOM 換成摘要了,狀態也要跟著清——留著就成了沒有畫面的幽靈狀態
  autogroupAssign = null;
  autogroupPicked.clear();
  $("#autogroupResult").innerHTML =
    `<div class="reclass-status ok">${esc(t("tagmgr.autogroupApplied", {n: applied, g: byGroup.size}))}</div>`;
}

// 重新命名/刪除做成 icon:一列三欄的格子寬度有限,兩顆文字按鈕會把標籤名擠到只剩幾個字
function tagRowHTML(tag) {
  const act = (name, icon, extra = "") => `<button type="button" class="btn icon tagmgr-act ${extra}"
      data-act="${name}" title="${esc(t(`tagmgr.${name}`))}" aria-label="${esc(t(`tagmgr.${name}`))}">${icon}</button>`;
  return `
    <div class="tagmgr-row" data-t="${esc(tag.name)}">
      <input type="checkbox" class="tagmgr-check" ${checkedTags.has(tag.name) ? "checked" : ""}>
      <span class="tagmgr-name" title="${esc(tag.name)}">${esc(tag.name)}</span>
      <span class="tagmgr-count">${t("tagmgr.noteCount", {n: tag.count})}</span>
      ${act("rename", "✎")}${act("delete", "🗑", "danger")}
    </div>`;
}

function renderTagManager() {
  const box = $("#tagManagerList");
  // 勾選集合先清掉已不存在的標籤(改名/刪除後)
  const live = new Set(state.allTags.map(t => t.name));
  for (const t of [...checkedTags]) if (!live.has(t)) checkedTags.delete(t);

  if (!state.allTags.length) {
    box.innerHTML = `<div class="empty">${t("sidebar.noTags")}</div>`;
    return;
  }

  // 搜尋欄過濾:比對標籤名**或**群組名(不分大小寫的 includes,同 components/select.js)。
  // 只影響顯示——上面 checkedTags 的清理必須用全集判斷,搜尋不可以把勾選洗掉;
  // 沒有任何成員命中的群組,標頭自然不出現;全空落入既有的 .tagmgr-empty。
  const q = tagFilter.trim().toLowerCase();
  const shown = q
    ? state.allTags.filter(tg => tg.name.toLowerCase().includes(q)
        || (tg.group || "").toLowerCase().includes(q))
    : state.allTags;

  // 已分組與未分組拆成兩個獨立區塊(各自一個格子容器),不再只靠一條分隔標頭區分——
  // 未分組通常是最大宗,混在同一份清單裡會讓「還有哪些沒分組」很難一眼看出來。
  const groups = new Map();
  const ungrouped = [];
  for (const tag of shown) {
    if (tag.group) {
      if (!groups.has(tag.group)) groups.set(tag.group, []);
      groups.get(tag.group).push(tag);
    } else ungrouped.push(tag);
  }
  // 分組動作住在它作用的那一區的標題列上:「移出群組」在已分組、「加入群組」在未分組。
  // ⚠ 兩區一律都畫(空的就顯示一行空狀態),不再「有東西才畫」——那兩顆按鈕作用在
  // **勾選的標籤**上,跟該區有沒有內容無關;全部標籤都分好組時若不畫未分組那一區,
  // 「加入群組」會整顆消失,於是再也改不了已分組標籤的群組。
  const section = (title, n, act, body) => `<div class="tagmgr-section">
      <div class="tagmgr-section-head"><span class="tagmgr-section-title">${esc(title)}</span>
        <span class="tagmgr-count">${t("tagmgr.tagCount", {n})}</span>
        <span class="spacer"></span>${act}</div>
      <div class="tagmgr-list">${body || `<div class="tagmgr-empty">${esc(t("tagmgr.sectionEmpty"))}</div>`}</div>
    </div>`;
  const actBtn = (id, key, titleKey) =>
    `<button type="button" class="btn outline" id="${id}" title="${esc(t(titleKey))}">${esc(t(key))}</button>`;

  let grouped = "";
  for (const g of [...groups.keys()].sort((a, b) => a.localeCompare(b))) {
    const members = groups.get(g);
    grouped += `<div class="tagmgr-group-head" data-g="${esc(g)}">
      <span class="tagmgr-group-name">🗂 ${esc(g)}</span>
      <span class="tagmgr-count">${t("tagmgr.tagCount", {n: members.length})}</span>
      <button type="button" class="btn" data-act="rename-group">${esc(t("tagmgr.renameGroup"))}</button>
      <button type="button" class="btn" data-act="export-group">${esc(t("tagmgr.export"))}</button>
      <button type="button" class="btn danger" data-act="dissolve">${esc(t("tagmgr.dissolve"))}</button>
    </div>`;
    grouped += members.map(tagRowHTML).join("");
  }
  box.innerHTML =
    section(t("tagmgr.grouped"), shown.length - ungrouped.length,
      actBtn("tagmgrUngroup", "tagmgr.ungroup", "tagmgr.ungroupTitle"), grouped) +
    section(t("tagmgr.ungrouped"), ungrouped.length,
      actBtn("tagmgrGroup", "tagmgr.addGroup", "tagmgr.addGroupTitle"),
      ungrouped.map(tagRowHTML).join(""));
  bindTagGroupActions();   // 兩顆按鈕每次重繪都是新節點,綁定跟著重畫走

  box.querySelectorAll(".tagmgr-group-head[data-g]").forEach(head => {
    const g = head.dataset.g;
    head.querySelector('[data-act="rename-group"]').onclick = async () => {
      const next = prompt(t("tagmgr.renameGroupPrompt", {name: g}), g);
      if (next === null) return;
      const trimmed = next.trim();
      if (!trimmed || trimmed === g) return;
      // 改成已存在的群組名是合併(後端允許),但那是不可逆的整組動作,先問一次
      if (groups.has(trimmed) && !confirm(t("tagmgr.renameGroupMerge", {from: g, to: trimmed}))) return;
      await actions.renameGroup(g, trimmed);  // loadTags() 的 tags-changed 會重繪這份清單
    };
    head.querySelector('[data-act="export-group"]').onclick = async () => {
      const fmt = confirm(t("tagmgr.exportGroupConfirm", {g})) ? "json" : "csv";
      try { await api.downloadExport(fmt, {group: g}); } catch { alert(t("transfer.exportFailed")); }
    };
    head.querySelector('[data-act="dissolve"]').onclick = () => actions.dissolveGroup(g);
  });
  box.querySelectorAll(".tagmgr-row").forEach(row => {
    const name = row.dataset.t;
    row.querySelector(".tagmgr-check").onchange = e => {
      if (e.target.checked) checkedTags.add(name); else checkedTags.delete(name);
    };
    row.querySelector('[data-act="rename"]').onclick = async () => {
      const next = prompt(t("tagmgr.renamePrompt", {name}), name);
      if (next === null) return;
      const trimmed = next.trim();
      if (!trimmed || trimmed === name) return;
      await actions.renameTag(name, trimmed);
    };
    row.querySelector('[data-act="delete"]').onclick = () => actions.deleteTag(name);
  });
}

/* ── 欄位樣板管理 ── */

// 樣板單獨匯出/匯入:跟「匯出/入」分頁的整包備份分開,只帶樣板定義、不含名詞。
// 不關設定 modal(使用者還在整理樣板,不該被踢出去),這點跟整包匯入不同。
function bindTemplateTransfer() {
  $("#tplmgrExport").onclick = async () => {
    try { await api.downloadTemplatesExport(); } catch { alert(t("transfer.exportFailed")); }
  };
  $("#tplmgrImport").onclick = () => $("#importTemplatesFile").click();
  $("#importTemplatesFile").addEventListener("change", async e => {
    const file = e.target.files[0]; e.target.value = "";
    if (!file) return;
    await actions.importTemplateFile(file);
  });
}

let editingTpl = null;  // 展開編輯中的樣板(deep copy;id 空字串 = 新建)

// draft 傳入時進入編輯模式(新增樣板從工具列進來)
function renderTemplateManager(draft) {
  if (draft !== undefined) editingTpl = draft ? JSON.parse(JSON.stringify(draft)) : null;
  const box = $("#templateManagerList");
  if (!state.allTemplates.length && !editingTpl) {
    box.innerHTML = `<div class="empty">${t("tplmgr.none")}</div>`;
    return;
  }
  let html = state.allTemplates.map(tpl => {
    if (editingTpl && editingTpl.id === tpl.id) return tplEditorHTML(editingTpl);
    const disabled = tpl.enabled === false;
    // 內建樣板(預設樣板除外)可啟用/停用;自訂樣板一律啟用、只能刪除
    const toggleable = tpl.builtin && tpl.id !== DEFAULT_TEMPLATE_ID;
    return `<div class="tplmgr-row${disabled ? " is-disabled" : ""}" data-id="${esc(tpl.id)}">
      <span class="tplmgr-name">${esc(tplLabel(tpl))}${tpl.builtin ? `<span class="tplmgr-badge">${esc(t("tplmgr.builtin"))}</span>` : ""}${disabled ? `<span class="tplmgr-badge off">${esc(t("tplmgr.disabled"))}</span>` : ""}</span>
      <span class="tagmgr-count">${t("tplmgr.fieldCount", {n: tpl.fields.length})}</span>
      <button type="button" class="btn" data-act="edit">${esc(t("tplmgr.edit"))}</button>
      ${tpl.resettable ? `<button type="button" class="btn" data-act="reset" title="${esc(t("tplmgr.reset"))}">↺</button>` : ""}
      ${toggleable ? `<button type="button" class="btn ${disabled ? "primary" : ""}" data-act="toggle-enabled">${esc(disabled ? t("tplmgr.enable") : t("tplmgr.disable"))}</button>` : ""}
      ${tpl.builtin ? "" : `<button type="button" class="btn danger" data-act="delete">${esc(t("tplmgr.delete"))}</button>`}
    </div>`;
  }).join("");
  if (editingTpl && !editingTpl.id) html += tplEditorHTML(editingTpl);  // 新建的編輯區放最後
  box.innerHTML = html;
  bindTemplateManager(box);
}

function tplEditorHTML(tpl) {
  // 內建樣板只鎖「已經存檔的」欄位 key(改掉會讓既有名詞的 fields 對不上),
  // 新增的欄位一定要能輸入 key。判斷依據是 state 裡已存檔的樣板,不是編輯區當下的
  // 值——否則使用者打完 key 再按「新增欄位」觸發重繪時,剛打的 key 會立刻被鎖住。
  const saved = tpl.builtin && state.allTemplates.find(x => x.id === tpl.id);
  const lockedKeys = new Set(saved ? (saved.fields || []).map(f => f.key) : []);
  // 一列 = 拖曳握把 + 啟用勾選 + key/label/placeholder + 刪除。
  // 停用只是「不顯示、不送 AI」,既有名詞的值一律留著(見 app/templates.py 檔頭)。
  // label/placeholder/樣板名稱顯示**在地化值**(locField/tplLabel,同閱讀頁),但
  // 翻譯絕不能寫進 templates.json——寫進去 *_is_default 旗標翻 false,切語言就再也
  // 不跟著變。所以每個輸入框帶三個屬性給 collect() 比對:data-raw(原始儲存值)、
  // data-disp(渲染當下顯示的值)、data-def(渲染當下的旗標)——值沒被改過就寫回
  // data-raw 並沿用旗標;真的改過才存輸入值、旗標歸 false。旗標本身只是前端重繪
  // 期間的暫態(後端 PUT 會把它濾掉、下次 GET 重算)。
  const fieldRow = (f, i) => {
    const lf = locField(tpl.id, f);
    return `<div class="tplmgr-field${f.enabled === false ? " is-off" : ""}" data-i="${i}">
      <span class="tpl-f-drag" title="${esc(t("tplmgr.dragField"))}" aria-hidden="true">⠿</span>
      <label class="tpl-f-on" title="${esc(t("tplmgr.fieldEnabled"))}">
        <input type="checkbox" class="tpl-f-enabled" ${f.enabled === false ? "" : "checked"}>
      </label>
      <input type="text" class="tpl-f-key" placeholder="${esc(t("tplmgr.keyPh"))}" value="${esc(f.key)}" ${lockedKeys.has(f.key) ? "readonly" : ""}>
      <input type="text" class="tpl-f-label" placeholder="${esc(t("tplmgr.labelPh"))}" value="${esc(lf.label)}"
        data-raw="${esc(f.label)}" data-disp="${esc(lf.label)}" data-def="${f.label_is_default ? 1 : 0}">
      <input type="text" class="tpl-f-ph" placeholder="${esc(t("tplmgr.phPh"))}" value="${esc(lf.placeholder || "")}"
        data-raw="${esc(f.placeholder || "")}" data-disp="${esc(lf.placeholder || "")}" data-def="${f.ph_is_default ? 1 : 0}">
      <button type="button" class="btn danger tpl-f-del" title="${esc(t("tplmgr.delField"))}">✕</button>
    </div>`;
  };
  const dispName = tplLabel(tpl);
  return `<div class="tplmgr-editor" data-id="${esc(tpl.id)}">
    <input type="text" id="tpl_name" placeholder="${esc(t("tplmgr.namePh"))}" value="${esc(dispName)}"
      data-raw="${esc(tpl.name)}" data-disp="${esc(dispName)}" data-def="${tpl.name_is_default ? 1 : 0}">
    <div class="tplmgr-fields">${tpl.fields.map(fieldRow).join("")}</div>
    <div class="hint">${esc(t("tplmgr.fieldsHint"))}</div>
    <div class="tplmgr-editor-actions">
      <button type="button" class="btn" id="tpl_addfield">${esc(t("tplmgr.addField"))}</button>
      <span class="spacer"></span>
      <button type="button" class="btn primary" id="tpl_save">${esc(t("tplmgr.save"))}</button>
      <button type="button" class="btn" id="tpl_cancel">${esc(t("tplmgr.cancel"))}</button>
    </div>
    <label class="aimgr-label">${esc(t("tplmgr.aiMode"))}</label>
    <select id="tpl_ai_mode" class="tpl-ai-mode">
      <option value="name"${tpl.ai_input_mode !== "paste" && tpl.ai_input_mode !== "article" ? " selected" : ""}>${esc(t("tplmgr.aiModeName"))}</option>
      <option value="paste"${tpl.ai_input_mode === "paste" ? " selected" : ""}>${esc(t("tplmgr.aiModePaste"))}</option>
    </select>
    <label class="aimgr-label">${esc(t("tplmgr.aiPrompt"))}</label>
    <textarea id="tpl_ai_prompt" class="tpl-ai-prompt" rows="5" placeholder="${esc(t("tplmgr.aiPromptPh"))}">${esc(tpl.ai_prompt || "")}</textarea>
    <div class="hint">${esc(t("tplmgr.aiPromptLangHint"))}</div>
    ${tpl.builtin ? `<div class="hint">${esc(t("tplmgr.builtinHint"))}</div>` : ""}
  </div>`;
}

function bindTemplateManager(box) {
  box.querySelectorAll(".tplmgr-row").forEach(row => {
    const id = row.dataset.id;
    row.querySelector('[data-act="edit"]').onclick = () => {
      renderTemplateManager(state.allTemplates.find(x => x.id === id));
    };
    const toggle = row.querySelector('[data-act="toggle-enabled"]');
    if (toggle) toggle.onclick = () => {
      const tpl = state.allTemplates.find(x => x.id === id);
      actions.setTemplateEnabled(id, tpl.enabled === false);  // 目前停用 → 啟用,反之亦然
    };
    const del = row.querySelector('[data-act="delete"]');
    if (del) del.onclick = () => actions.deleteTemplate(id);
    const reset = row.querySelector('[data-act="reset"]');
    if (reset) reset.onclick = () => actions.resetTemplate(id);
  });
  const ed = box.querySelector(".tplmgr-editor");
  if (!ed) return;
  // 收集編輯區目前的輸入(增刪欄位重繪前先收,使用者打到一半的值不丟)。
  // 輸入框顯示的是在地化值(見 tplEditorHTML 註解):值沒被改過就寫回 data-raw
  // 的原始儲存值並沿用 *_is_default 旗標(重繪後才會再次在地化),改過才存輸入值。
  const rawOr = el => {
    const v = el.value.trim();
    return v === el.dataset.disp ? el.dataset.raw : v;
  };
  const stillDefault = el => el.value.trim() === el.dataset.disp && el.dataset.def === "1";
  const collect = () => {
    const nameEl = ed.querySelector("#tpl_name");
    editingTpl.name = rawOr(nameEl);
    editingTpl.name_is_default = stillDefault(nameEl);
    editingTpl.fields = [...ed.querySelectorAll(".tplmgr-field")].map(r => {
      const labEl = r.querySelector(".tpl-f-label"), phEl = r.querySelector(".tpl-f-ph");
      return {
        key: r.querySelector(".tpl-f-key").value.trim(),
        label: rawOr(labEl),
        label_is_default: stillDefault(labEl),
        placeholder: rawOr(phEl),
        ph_is_default: stillDefault(phEl),
        enabled: r.querySelector(".tpl-f-enabled").checked,
      };
    });
    editingTpl.ai_input_mode = ed.querySelector("#tpl_ai_mode").value;
    editingTpl.ai_prompt = ed.querySelector("#tpl_ai_prompt").value;
  };
  ed.querySelector("#tpl_addfield").onclick = () => {
    collect();
    editingTpl.fields.push({key: "", label: "", placeholder: "", enabled: true});
    renderTemplateManager(editingTpl);
  };
  ed.querySelectorAll(".tpl-f-del").forEach(btn => btn.onclick = () => {
    collect();
    editingTpl.fields.splice(+btn.closest(".tplmgr-field").dataset.i, 1);
    renderTemplateManager(editingTpl);
  });
  // 勾選只改樣式(灰掉那一列);真正的值在 collect() 時一起讀,不用單獨存
  ed.querySelectorAll(".tpl-f-enabled").forEach(cb => cb.onchange = () =>
    cb.closest(".tplmgr-field").classList.toggle("is-off", !cb.checked));

  // ── 拖曳排序 ──
  // 整列 draggable 會搶掉輸入框裡的文字選取,所以平常關著,壓在握把上才打開。
  // 放開後直接搬 DOM 節點,再 collect() 依「目前 DOM 順序」重讀成陣列——
  // 順序的真相就是畫面本身,不用另外維護索引。
  const rows = [...ed.querySelectorAll(".tplmgr-field")];
  let dragging = null;
  rows.forEach(row => {
    const handle = row.querySelector(".tpl-f-drag");
    handle.addEventListener("mousedown", () => { row.draggable = true; });
    row.addEventListener("dragstart", e => {
      dragging = row;
      row.classList.add("is-dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", "");  // Firefox 沒有資料就不會啟動拖曳
    });
    row.addEventListener("dragend", () => {
      row.draggable = false;
      row.classList.remove("is-dragging");
      dragging = null;
      collect();
      renderTemplateManager(editingTpl);  // 重繪讓 data-i 跟新順序對齊
    });
    row.addEventListener("dragover", e => {
      if (!dragging || dragging === row) return;
      e.preventDefault();
      // 以游標落在該列上半或下半決定插在前面還是後面
      const box = row.getBoundingClientRect();
      row.parentNode.insertBefore(dragging,
        e.clientY < box.top + box.height / 2 ? row : row.nextSibling);
    });
  });
  ed.querySelector("#tpl_save").onclick = async () => {
    collect();
    const ok = await actions.saveTemplate(editingTpl.id, {
      name: editingTpl.name, fields: editingTpl.fields,
      ai_input_mode: editingTpl.ai_input_mode, ai_prompt: editingTpl.ai_prompt,
    });
    if (ok) renderTemplateManager(null);  // 收合編輯區並以最新樣板清單重繪
  };
  ed.querySelector("#tpl_cancel").onclick = () => renderTemplateManager(null);
}

/* ── AI 連線設定(Ollama 原生 或 OpenAI 相容的本機服務)──────────────────
   這是**站台層、全站唯一一組**的設定,只有管理者改得動(見 app/ai_settings.py)。
   一般使用者仍然讀得到——前端要靠 enabled 決定要不要顯示各處的 AI 按鈕——但看到
   的是唯讀畫面,而且後端回傳裡沒有 api_key 明文,只有 has_api_key。 */

const AI_FIELDS = ["#ai_enabled", "#ai_api_style", "#ai_base_url", "#ai_api_key",
                   "#ai_model", "#ai_embed_model", "#ai_desc_limit", "#ai_desc_max"];

function renderAISettings() {
  const s = state.aiSettings;
  if (!s) return;  // 尚未載入完成(app 啟動初期極短暫的空窗)
  const canEdit = !!state.me?.is_admin;
  $("#ai_enabled").checked = !!s.enabled;
  $("#ai_api_style").value = s.api_style || "ollama";
  $("#ai_base_url").value = s.base_url || "";
  $("#ai_model").value = s.model || "";
  $("#ai_embed_model").value = s.embed_model || "";
  // 金鑰不會回傳明文,所以輸入框永遠是空的、只用 placeholder 說有沒有設定過
  // (照抄管理分頁對 Google client_secret 的做法)。
  const key = $("#ai_api_key");
  key.value = "";
  key.placeholder = s.has_api_key ? t("ai.apiKeySet") : t("ai.apiKeyPh");

  AI_FIELDS.forEach(sel => { $(sel).disabled = !canEdit; });
  $("#ai_save").style.display = canEdit ? "" : "none";
  $("#ai_list_models").style.display = canEdit ? "" : "none";
  $("#ai_clear_key").style.display = canEdit && s.has_api_key ? "" : "none";
  $("#ai_admin_only").style.display = canEdit ? "none" : "block";
  $("#ai_desc_limit").checked = !!s.desc_limit_enabled;
  $("#ai_desc_max").value = s.desc_max_chars || 250;
  syncAIStyleHint();
  syncDescLimitRow();
}

// OpenAI 相容模式有兩個使用者一定會踩到的差異(位址要帶 /v1、沒辦法關掉思考型
// 模型的推理),只在選到那個模式時才把提示顯示出來。
function syncAIStyleHint() {
  $("#ai_style_hint").style.display = $("#ai_api_style").value === "openai" ? "block" : "none";
}

// 字數上限的數字欄只在開關打開時顯示(同 syncAIStyleHint 的做法)
function syncDescLimitRow() {
  $("#ai_desc_limit_row").style.display = $("#ai_desc_limit").checked ? "" : "none";
}

function bindAISettings() {
  $("#ai_api_style").onchange = syncAIStyleHint;
  $("#ai_desc_limit").onchange = syncDescLimitRow;
  $("#ai_save").onclick = async () => {
    const payload = {
      enabled: $("#ai_enabled").checked,
      api_style: $("#ai_api_style").value,
      base_url: $("#ai_base_url").value.trim(),
      model: $("#ai_model").value.trim(),
      embed_model: $("#ai_embed_model").value.trim(),
      desc_limit_enabled: $("#ai_desc_limit").checked,
      desc_max_chars: parseInt($("#ai_desc_max").value, 10) || 250,
    };
    // ⚠ 使用者沒動金鑰欄就**不要**把 api_key 放進 payload:後端的空字串語意是
    // 「清除金鑰」(刻意保留這個語意,否則設錯的金鑰再也拿不掉),而 GET 回傳
    // 已經把金鑰遮掉了,每次都送空字串等於每存一次檔就清空一次。
    const key = $("#ai_api_key").value.trim();
    if (key) payload.api_key = key;
    if (!await actions.saveAISettings(payload)) return;
    // 存完一定要重繪:金鑰欄要清空(明文不該繼續留在 DOM 裡)、「清除金鑰」按鈕
    // 要跟著 has_api_key 出現,後端夾過的值也才會顯示成真正存進去的那個。
    renderAISettings();
    alert(t("ai.saved"));
  };
  $("#ai_clear_key").onclick = async () => {
    if (!confirm(t("ai.clearKeyConfirm"))) return;
    if (await actions.saveAISettings({api_key: ""})) renderAISettings();
  };
  $("#ai_list_models").onclick = listAIModels;
}

// 查詢服務端可用的模型,填進 datalist 供 model / embed_model 兩欄挑選,
// 並在下方渲染勾選清單(radio → 寫回輸入框;輸入框仍是存檔的唯一真相)。
// 這支同時兼任「測試連線」,所以刻意送當下輸入框的值而不是已存檔的設定。
async function listAIModels() {
  const btn = $("#ai_list_models");
  const status = $("#ai_models_status");
  const showStatus = msg => { status.textContent = msg; status.style.display = "block"; };
  btn.disabled = true;
  $("#ai_models_picker").style.display = "none";
  showStatus(t("ai.modelsLoading"));
  try {
    const r = await api.listAIModels($("#ai_base_url").value.trim(), $("#ai_api_style").value);
    if (!r.ok) { showStatus((await r.json().catch(() => ({}))).detail || t("ai.modelsError")); return; }
    const models = (await r.json()).models || [];
    const list = $("#ai_model_list");
    list.innerHTML = models.map(m => `<option value="${esc(m)}"></option>`).join("");
    if (!models.length) { showStatus(t("ai.modelsEmpty")); return; }
    showStatus(t("ai.modelsCount", {n: models.length}));
    renderModelPicker(models);
  } catch (e) {
    showStatus(t("ai.modelsError"));
  } finally {
    btn.disabled = false;
  }
}

// 模型勾選清單:一列一個模型、右側「聊天/嵌入」兩欄 radio。API 不會標明哪些是
// 嵌入模型(兩種 api_style 都只回名稱字串),所以兩欄都列全部模型,由 hint 提醒
// 使用者依名稱判斷。第一列是嵌入專屬的「不使用」——空字串是有意義的狀態
// (停用語意檢索),後端存檔時也刻意不把空的 embed_model 退回預設。
function renderModelPicker(models) {
  const box = $("#ai_models_list_box");
  const chatNow = $("#ai_model").value.trim();
  const embedNow = $("#ai_embed_model").value.trim();
  const row = (label, value, {noChat = false} = {}) => `
    <div class="ai-mp-row">
      <span class="ai-mp-name">${label}</span>
      <span class="ai-mp-cell">${noChat ? "" :
        `<input type="radio" name="ai_pick_chat" value="${esc(value)}"${value === chatNow ? " checked" : ""}>`}</span>
      <span class="ai-mp-cell">
        <input type="radio" name="ai_pick_embed" value="${esc(value)}"${value === embedNow ? " checked" : ""}>
      </span>
    </div>`;
  box.innerHTML = `
    <div class="ai-mp-row ai-mp-head">
      <span></span>
      <span class="ai-mp-cell">${t("ai.pickChat")}</span>
      <span class="ai-mp-cell">${t("ai.pickEmbed")}</span>
    </div>
    ${row(`<span class="ai-mp-none">${t("ai.pickNone")}</span>`, "", {noChat: true})}
    ${models.map(m => row(esc(m), m)).join("")}`;
  $("#ai_models_picker").style.display = "block";

  box.querySelectorAll('input[name="ai_pick_chat"]').forEach(rd => {
    rd.onchange = () => { $("#ai_model").value = rd.value; };
  });
  box.querySelectorAll('input[name="ai_pick_embed"]').forEach(rd => {
    rd.onchange = () => { $("#ai_embed_model").value = rd.value; };
  });
  // 手動改字時把不匹配的 radio 取消勾選,別讓 UI 說謊
  const unpick = (name, input) => () => {
    const v = input.value.trim();
    box.querySelectorAll(`input[name="${name}"]`).forEach(rd => { rd.checked = rd.value === v; });
  };
  $("#ai_model").oninput = unpick("ai_pick_chat", $("#ai_model"));
  $("#ai_embed_model").oninput = unpick("ai_pick_embed", $("#ai_embed_model"));
}

/* ── 語意檢索分頁:向量索引的狀態與建立 ─────────────────────────────── */

// 一批的筆數。夠小到進度看得出來在動,夠大到不會被 HTTP 來回吃掉時間。
const SEM_BATCH = 20;

async function renderSemanticSettings() {
  const box = $("#semStatus");
  box.textContent = t("sem.loading");
  const s = await actions.semanticStatus();
  if (!s) { box.textContent = t("sem.loadFailed"); return; }

  const lines = [];
  if (!s.embed_model) lines.push(t("sem.noModel"));
  else lines.push(t("sem.model", {model: esc(s.embed_model)}));
  lines.push(t("sem.counts", {indexed: s.indexed, total: s.total, stale: s.stale}));
  // 換了嵌入模型 = 現存向量全部不可比,老實說「要整個重建」,不要讓人以為
  // 只差幾筆(app/vectors.py 的相容性檢查會直接砍掉重來)。
  if (s.model_mismatch) lines.push(t("sem.modelChanged"));
  box.innerHTML = lines.map(l => `<div>${l}</div>`).join("");

  $("#semReindex").disabled = !s.embed_model;
}

function bindSemanticSettings() {
  $("#semReindex").onclick = reindexLoop;
  $("#semClear").onclick = async () => {
    if (!confirm(t("sem.clearConfirm"))) return;
    if (await actions.clearSemanticIndex()) renderSemanticSettings();
  };
}

// 建索引結束但有幾筆被逐筆隔離跳過時的總結訊息:指名是哪幾筆(前三筆名稱)、
// 服務端說了什麼(第一筆的錯誤原文)。名稱與錯誤原文是資料,不進 i18n。
function semFailureSummary(done, failed) {
  const names = failed.slice(0, 3).map(f => f.name || f.id).join("、")
    + (failed.length > 3 ? "…" : "");
  return t("sem.doneWithFailures", {done, n: failed.length, names})
    + " — " + (failed[0].error || "");
}

// 前端迴圈跑批次,照抄「批次壓縮既有圖片」的既有做法:不需要背景工作者、
// 不需要串流,而且中途關掉視窗也只是停在那裡,已經算好的向量不會白算。
async function reindexLoop() {
  const btn = $("#semReindex");
  const prog = $("#semProgress");
  const show = msg => { prog.textContent = msg; prog.style.display = "block"; };
  btn.disabled = true;
  let done = 0;
  let stalled = 0;
  const failed = [];   // 後端逐筆隔離跳過的名詞(依 id 去重:同一筆會在後續批次再失敗一次)
  try {
    for (;;) {
      const r = await actions.semanticReindexBatch(SEM_BATCH);
      if (r.error) { show(r.error); return; }
      done += r.embedded;
      for (const f of r.failed || []) {
        if (!failed.some(x => x.id === f.id)) failed.push(f);
      }
      show(t("sem.progress", {done, total: done + r.remaining}));
      if (r.done) break;
      // 連續零進度就停:任何「remaining 不減」的路徑都不該變成無聲的請求風暴
      if (r.embedded === 0 && !(r.failed || []).length) {
        if (++stalled >= 3) { show(t("sem.stuck")); return; }
      } else stalled = 0;
    }
    show(failed.length ? semFailureSummary(done, failed) : t("sem.progressDone", {done}));
  } finally {
    btn.disabled = false;
    renderSemanticSettings();
  }
}

/* ── 管理者分頁:站台設定(註冊/白名單/OAuth/公開分享)+ 使用者管理 ──────── */

async function renderAdmin() {
  let s;
  try { s = await api.getAdminSettings(); } catch { return; }
  if (!s || !s.registration_mode) return;  // 非 admin 誤入時回傳的是 {detail:...},保護一下
  $("#admin_public_share_enabled").checked = !!s.public_share_enabled;
  $("#admin_public_notebook_enabled").checked = !!s.public_notebook_enabled;
  $("#admin_reg_mode").value = s.registration_mode;
  $("#admin_whitelist").value = (s.allowed_emails || []).join("\n");
  const g = s.google_oauth || {};
  $("#admin_oauth_enabled").checked = !!g.enabled;
  $("#admin_oauth_client_id").value = g.client_id || "";
  const sec = $("#admin_oauth_secret");
  sec.value = "";
  sec.placeholder = g.has_secret ? t("admin.secretSet") : "";
  const rl = s.rate_limit || {};
  $("#admin_rl_enabled").checked = rl.enabled !== false;
  $("#admin_rl_ip_max").value = rl.ip_max_attempts ?? 20;
  $("#admin_rl_email_max").value = rl.email_max_attempts ?? 5;
  $("#admin_rl_window").value = rl.window_minutes ?? 15;
  $("#admin_rl_lockout").value = rl.lockout_minutes ?? 15;
  $("#admin_rl_xff").checked = !!rl.trust_forwarded_for;
  renderAdminUsers();
  renderAdminInvites();
  renderAdminPublished();
}

/* ── 全站公開筆記快照(管理分頁)──────────────────────────────────
   孤兒(擁有者帳號已刪)只有這裡清得掉——快照存在使用者目錄之外,
   帳號刪除後仍在,那是刻意的(見 app/publish.py 檔頭)。 */

async function renderAdminPublished() {
  const box = $("#adminPublishedList");
  if (!box) return;
  const rows = await api.adminListPublished();
  if (!rows.length) {
    box.innerHTML = `<div class="hint">${esc(t("admin.pubNone"))}</div>`;
    return;
  }
  box.innerHTML = rows.map(m => `<div class="invmgr-row" data-pid="${esc(m.pid)}">
      <input class="invmgr-url" readonly value="${esc(location.origin + "/p/" + m.pid)}">
      <button type="button" class="btn danger" data-del="${esc(m.pid)}">${esc(t("admin.pubDelete"))}</button>
      <span class="hint invmgr-meta">${esc(m.title || m.pid)} · ${esc(t("publish.count", {n: m.note_count}))} ·
        ${esc(m.owner_label)}${m.orphan ? ` · ${esc(t("admin.pubOrphan"))}` : ""} · ${esc(fmtDate(m.created))}</span>
    </div>`).join("");
  box.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
    if (!confirm(t("admin.pubDeleteConfirm"))) return;
    await api.adminDeletePublished(b.dataset.del);
    renderAdminPublished();
  });
}

/* ── 公開筆記快照(設定 → 公開筆記)──────────────────────────────
   發佈/更新/撤下自己的凍結快照。站台開關關著時只顯示提示——擁有者仍看得到
   自己既有的快照清單並可撤下(後端 DELETE 刻意不看開關)。 */

async function renderPublishManager() {
  const list = $("#pubMgrList");
  if (!list) return;
  const enabled = state.publicNotebookEnabled;
  $("#pubMgrForm").style.display = enabled ? "" : "none";
  $("#pubMgrDisabled").style.display = enabled ? "none" : "";
  $("#pub_create").onclick = async () => {
    const btn = $("#pub_create");
    btn.disabled = true;
    try {
      const r = await api.createPublication({
        title: $("#pub_title").value.trim(),
        tags: $("#pub_tags").value.trim(),
        group: $("#pub_group").value.trim(),
      });
      if (!r.ok) {
        alert((await r.json().catch(() => ({}))).detail || t("publish.failed"));
        return;
      }
      $("#pub_title").value = "";
      drawPublications();
    } finally {
      btn.disabled = false;
    }
  };
  drawPublications();
}

async function drawPublications() {
  const list = $("#pubMgrList");
  const d = await api.listPublications();
  const rows = d.publications || [];
  if (!rows.length) {
    list.innerHTML = `<div class="hint">${esc(t("publish.none"))}</div>`;
    return;
  }
  list.innerHTML = rows.map(m => {
    const url = location.origin + "/p/" + m.pid;
    return `<div class="invmgr-row" data-pid="${esc(m.pid)}">
      <input class="invmgr-url" readonly value="${esc(url)}">
      <button type="button" class="btn outline" data-copy="${esc(url)}">${esc(t("publish.copy"))}</button>
      <button type="button" class="btn" data-republish="${esc(m.pid)}">${esc(t("publish.republish"))}</button>
      <button type="button" class="btn danger" data-revoke="${esc(m.pid)}">${esc(t("publish.revoke"))}</button>
      <span class="hint invmgr-meta">${esc(m.title || "")}${m.title ? " · " : ""}${
        esc(t("publish.count", {n: m.note_count}))} · ${esc(fmtDate(m.created))}</span>
    </div>`;
  }).join("");
  list.querySelectorAll("[data-copy]").forEach(b => b.onclick = async () => {
    try {
      await navigator.clipboard.writeText(b.dataset.copy);
      const orig = b.textContent;
      b.textContent = t("publish.copied");
      setTimeout(() => { b.textContent = orig; }, 1200);
    } catch {
      // clipboard API 在非 HTTPS 會失敗——選起來讓使用者自己複製(同邀請連結)。
      b.closest(".invmgr-row")?.querySelector(".invmgr-url")?.select();
    }
  });
  // 重新發佈:同 pid、同範圍(存在 manifest.scope 裡)覆蓋內容
  list.querySelectorAll("[data-republish]").forEach(b => b.onclick = async () => {
    if (!confirm(t("publish.republishConfirm"))) return;
    const m = (await api.listPublications()).publications
      .find(x => x.pid === b.dataset.republish);
    if (!m) return drawPublications();
    const r = await api.createPublication({
      pid: m.pid, title: m.title,
      tags: (m.scope?.tags || []).join(","), group: m.scope?.group || "",
    });
    if (!r.ok) alert((await r.json().catch(() => ({}))).detail || t("publish.failed"));
    drawPublications();
  });
  list.querySelectorAll("[data-revoke]").forEach(b => b.onclick = async () => {
    if (!confirm(t("publish.revokeConfirm"))) return;
    await api.revokePublication(b.dataset.revoke);
    drawPublications();
  });
}

/* ── 站台註冊邀請連結(管理分頁)──────────────────────────────────────
   持有連結就能註冊、繞過註冊模式與白名單,所以產生/撤銷收站台 admin。
   它是公開筆記動線的收尾:公開筆記把知識送出去,邀請連結把讀者接進來。 */

async function renderAdminInvites() {
  const list = $("#adminInvitesList");
  if (!list) return;
  $("#admin_inv_create").onclick = async () => {
    const r = await api.createInvite({
      uses: parseInt($("#admin_inv_uses").value, 10) || 1,
      ttl_days: parseInt($("#admin_inv_ttl").value, 10) || 7,
    });
    if (!r.ok) { alert((await r.json().catch(() => ({}))).detail || t("admin.invFailed")); return; }
    drawAdminInvites();
  };
  drawAdminInvites();
}

async function drawAdminInvites() {
  const list = $("#adminInvitesList");
  const rows = await api.listInvites();
  if (!rows.length) { list.innerHTML = `<div class="hint">${esc(t("admin.invNone"))}</div>`; return; }
  list.innerHTML = rows.map(iv => {
    const url = location.origin + iv.url;
    return `<div class="invmgr-row">
      <input class="invmgr-url" readonly value="${esc(url)}">
      <button type="button" class="btn outline" data-copy="${esc(url)}">${esc(t("admin.invCopy"))}</button>
      <button type="button" class="btn danger" data-revoke="${esc(iv.nonce)}">${esc(t("admin.invRevoke"))}</button>
      <span class="hint invmgr-meta">${esc(t("admin.invUsesLeft", {n: iv.uses_left}))} · ${
        esc(t("admin.invExpires", {date: fmtDate(iv.expires)}))}</span>
    </div>`;
  }).join("");
  list.querySelectorAll("[data-copy]").forEach(b => b.onclick = async () => {
    try {
      await navigator.clipboard.writeText(b.dataset.copy);
      const orig = b.textContent;
      b.textContent = t("admin.invCopied");
      setTimeout(() => { b.textContent = orig; }, 1200);
    } catch {
      // clipboard API 在非 HTTPS 會失敗。網址就在旁邊的唯讀輸入框裡,
      // 選起來讓使用者自己複製即可,不用 alert 打斷(同詳細頁複製程式碼的取捨)。
      b.closest(".invmgr-row")?.querySelector(".invmgr-url")?.select();
    }
  });
  list.querySelectorAll("[data-revoke]").forEach(b => b.onclick = async () => {
    if (!confirm(t("admin.invRevokeConfirm"))) return;
    await api.revokeInvite(b.dataset.revoke);
    drawAdminInvites();
  });
}

/* ── 整站備份與還原(獨立分頁,只有 admin 看得到)────────────────────────
   跟同一個群組裡的「匯出/入」是兩件事:那個是使用者把自己的庫搬到另一台
   (刻意不帶 sharing.json/shares.json),這個是整站的災難復原(必須帶)。
   理由見 app/backup.py 檔頭的對照表——別把兩邊的清單互相抄。 */

async function renderBackups() {
  let d;
  try { d = await api.getBackups(); } catch { return; }
  if (!d || !d.settings) return;
  const cfg = d.settings;
  $("#admin_bk_auto").checked = cfg.auto_enabled !== false;
  $("#admin_bk_interval").value = cfg.interval_days ?? 7;
  $("#admin_bk_keep").value = cfg.keep ?? 10;
  $("#admin_bk_at").value = cfg.at_time || "";   // 空 = 不指定時刻(到期就備份)
  $("#admin_bk_status").textContent = d.last_auto
    ? t("admin.bkLast", {when: fmtDate(d.last_auto)})
    : t("admin.bkNever");

  const box = $("#adminBackupList");
  if (!d.backups.length) {
    box.innerHTML = `<div class="hint">${esc(t("admin.bkEmpty"))}</div>`;
    return;
  }
  box.innerHTML = d.backups.map(b => `
    <div class="tplmgr-row" data-bk="${esc(b.name)}">
      <span class="tplmgr-name">${esc(fmtDate(b.created))}
        <span class="tplmgr-badge">${esc(t("admin.bkKind_" + b.kind))}</span>
        <span class="hint">${esc(fmtBytes(b.size))}${
          b.user_ids.length ? " · " + esc(t("admin.bkUsers", {n: b.user_ids.length})) : ""}${
          b.valid ? "" : " · " + esc(t("admin.bkInvalid"))}</span></span>
      <a class="btn" href="${esc(api.backupDownloadUrl(b.name))}" download>${esc(t("admin.bkDownload"))}</a>
      <button type="button" class="btn" data-act="restore">${esc(t("admin.bkRestore"))}</button>
      <button type="button" class="btn danger" data-act="del">${esc(t("admin.bkDelete"))}</button>
    </div>`).join("");

  box.querySelectorAll("[data-bk]").forEach(row => {
    const name = row.dataset.bk;
    row.querySelector('[data-act="del"]').onclick = async () => {
      if (!confirm(t("admin.bkDeleteConfirm"))) return;
      if ((await api.deleteBackup(name)).ok) renderBackups();
    };
    row.querySelector('[data-act="restore"]').onclick = () => doRestore(name);
  });
}

// 還原是整站取代,所以確認流程做兩段:先跟後端要這包的內容摘要(幾個使用者、
// 含不含站台設定),把實際會發生的事講出來再要第二次確認。只寫「確定要還原嗎」
// 的話,使用者無從判斷自己選到的是哪一包。
async function doRestore(name) {
  let info;
  try { info = await api.inspectBackup(name); }
  catch (e) { alert(e.message || t("admin.bkRestoreFailed")); return; }
  const detail = t("admin.bkRestoreConfirm", {
    name, n: info.user_ids.length,
    settings: info.has_site_settings ? t("admin.bkYes") : t("admin.bkNo"),
  });
  if (!confirm(detail)) return;
  if (!confirm(t("admin.bkRestoreConfirm2"))) return;

  const r = await api.restoreBackup(name);
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    alert(d.detail || t("admin.bkRestoreFailed"));
    return;
  }
  const body = await r.json();
  // 還原過的 users.json 可能沒有目前這個帳號,.session_secret 也可能換了 →
  // 現在這個 session 隨時會失效。與其讓使用者按下一個按鈕才莫名其妙跳登入,
  // 不如講清楚並主動重新載入。
  alert(t("admin.bkRestoreDone", {n: body.restored_files, snapshot: body.snapshot}));
  location.reload();
}

function bindBackups() {
  $("#admin_bk_save").onclick = async () => {
    // `|| 預設值` 是刻意的(不要「順手」改成 `??`):欄位清空時 Number("") 是 0,
    // 而 0 在這裡沒有意義——間隔 0 天等於每次請求都備份、保留 0 份等於備份完
    // 立刻刪掉。用 || 讓 0 與空字串都退回預設值;後端的 _int_in() 仍會再夾一次
    // 上下界,兩層都在。
    const r = await api.setBackupSettings({
      auto_enabled: $("#admin_bk_auto").checked,
      interval_days: Number($("#admin_bk_interval").value) || 7,
      keep: Number($("#admin_bk_keep").value) || 10,
      // 這一個相反,要原樣送出:空字串是「不指定時刻」這個有意義的狀態,
      // 用 || 塞預設值就再也清不掉了。後端 _clean_at_time() 會擋掉爛格式。
      at_time: $("#admin_bk_at").value,
    });
    adminSaveFeedback(r, renderBackups);
  };
  $("#admin_bk_now").onclick = async () => {
    const btn = $("#admin_bk_now"), orig = btn.textContent;
    btn.disabled = true; btn.textContent = t("admin.bkWorking");
    try {
      const r = await api.createBackup();
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        alert(d.detail || t("admin.bkFailed"));
      } else {
        await renderBackups();
      }
    } finally { btn.disabled = false; btn.textContent = orig; }
  };
  $("#admin_bk_upload").onclick = () => $("#admin_bk_file").click();
  $("#admin_bk_file").onchange = async e => {
    const file = e.target.files[0];
    e.target.value = "";  // 同一個檔案連選兩次也要觸發
    if (!file) return;
    const restore = confirm(t("admin.bkUploadRestore"));
    const r = await api.uploadBackup(file, restore);
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      alert(d.detail || t("admin.bkFailed"));
      return;
    }
    const body = await r.json();
    if (body.restored) {
      alert(t("admin.bkRestoreDone", {n: body.restored_files, snapshot: body.snapshot}));
      location.reload();
    } else {
      await renderBackups();
    }
  };
}

async function adminSaveFeedback(resp, after) {
  if (resp.ok) {
    alert(t("admin.saved"));
    if (after) await after();
  } else {
    const d = await resp.json().catch(() => ({}));
    alert(d.detail || t("admin.saveFailed"));
  }
}



function bindAdmin() {
  $("#admin_sharing_save").onclick = async () => {
    const r = await api.setSharingFlags($("#admin_public_share_enabled").checked,
                                        $("#admin_public_notebook_enabled").checked);
    // 開關改了要同步 state:詳細頁的分享鈕與公開筆記分頁看它,不同步的話
    // admin 自己得重新整理才看得到效果。
    if (r.ok) {
      const d = await r.json();
      state.publicShareEnabled = !!d.public_share_enabled;
      state.publicNotebookEnabled = !!d.public_notebook_enabled;
    }
    adminSaveFeedback(r, renderAdmin);
  };
  $("#admin_reg_save").onclick = async () =>
    adminSaveFeedback(await api.setRegistrationMode($("#admin_reg_mode").value));
  $("#admin_whitelist_save").onclick = async () => {
    const emails = $("#admin_whitelist").value.split("\n").map(x => x.trim()).filter(Boolean);
    adminSaveFeedback(await api.setWhitelist(emails), renderAdmin);
  };
  $("#admin_rl_save").onclick = async () => {
    const r = await api.setRateLimit({
      enabled: $("#admin_rl_enabled").checked,
      ip_max_attempts: Number($("#admin_rl_ip_max").value) || 20,
      email_max_attempts: Number($("#admin_rl_email_max").value) || 5,
      window_minutes: Number($("#admin_rl_window").value) || 15,
      lockout_minutes: Number($("#admin_rl_lockout").value) || 15,
      trust_forwarded_for: $("#admin_rl_xff").checked,
    });
    // 存檔後重繪:後端會把超出範圍的值夾回合法區間,不重繪的話畫面上還是
    // 使用者剛打的那個非法值,他會以為存進去了。
    adminSaveFeedback(r, renderAdmin);
  };
  $("#admin_rl_reset").onclick = async () => {
    const r = await api.resetRateLimit();
    alert(r.ok ? t("admin.rlResetDone") : t("admin.saveFailed"));
  };
  $("#admin_oauth_save").onclick = async () => {
    const r = await api.setOAuth({
      enabled: $("#admin_oauth_enabled").checked,
      client_id: $("#admin_oauth_client_id").value.trim(),
      client_secret: $("#admin_oauth_secret").value,  // 空字串 = 沿用已存的 secret
    });
    adminSaveFeedback(r, renderAdmin);
  };
}

/* ── 團隊庫救援(§3.8)──────────────────────────────────────────────
   站台 admin 對團隊庫只有成員管理層面的權力:指派既有成員為團隊 admin、
   刪除整個團隊。看不到、也拿不到任何內容(後端的救援端點完全不碰 notes_dir)。 */
async function renderAdminUsers() {
  const box = $("#adminUsersList");
  let users;
  try { users = await api.getAdminUsers(); } catch { return; }
  const meId = state.me && state.me.id;
  box.innerHTML = users.map(u => {
    const you = u.id === meId ? ` <span class="tplmgr-badge">${esc(t("admin.you"))}</span>` : "";
    const adminBadge = u.is_admin ? `<span class="tplmgr-badge">admin</span>` : "";
    return `<div class="tplmgr-row" data-uid="${esc(u.id)}">
      <span class="tplmgr-name">${esc(u.email)}${adminBadge}${you}
        <span class="hint">${esc(u.auth)}</span></span>
      <button type="button" class="btn" data-act="toggle">${esc(u.is_admin ? t("admin.demote") : t("admin.promote"))}</button>
      <button type="button" class="btn danger" data-act="delete">${esc(t("admin.delete"))}</button>
    </div>`;
  }).join("");

  box.querySelectorAll(".tplmgr-row[data-uid]").forEach(row => {
    const id = row.dataset.uid;
    const u = users.find(x => x.id === id);
    row.querySelector('[data-act="toggle"]').onclick = async () => {
      const r = await api.setUserAdmin(id, !u.is_admin);
      if (r.ok) renderAdminUsers();
      else { const d = await r.json().catch(() => ({})); alert(d.detail || t("admin.saveFailed")); }
    };
    row.querySelector('[data-act="delete"]').onclick = async () => {
      if (!confirm(t("admin.deleteConfirm", {email: u.email}))) return;
      const r = await api.deleteUser(id);
      if (r.ok) renderAdminUsers();
      else { const d = await r.json().catch(() => ({})); alert(d.detail || t("admin.saveFailed")); }
    };
  });
}

/* ── 關於本專案:版本號 + 回報管道。除了版本號與「複製環境資訊」之外全是
      index.html 的靜態文字(applyI18n 開機時已套好),所以這裡只有兩件事。 ── */

// 版本號來自 state.me.version —— GET /api/auth/me 是 boot 的第一支請求,app.js 已經
// 把整個回應存進 state.me 了,不再打第二次 API(比照 is_admin / sharedEnabled 的做法)。
// 刻意不展開成 state.appVersion:同一份資料存兩份就是兩個真相,而它只有這裡讀。
function renderAbout() {
  const v = state.me?.version || "";
  // 0.x = Beta,1.0.0 起才是正式版(規則寫在 CHANGELOG.md 的 Versioning 段)。
  // 從版號字串自己推導,不另外傳一個 is_beta 欄位——那會變成第二個真相,
  // 而且必然有一天跟版號對不起來。
  $("#about_version").textContent = v ? (v.startsWith("0.") ? `${v} ${t("about.beta")}` : v) : "—";
}

function bindAbout() {
  $("#about_copy_env").onclick = async () => {
    const btn = $("#about_copy_env");
    // 刻意只帶版本 / 介面語言 / UA:這段文字的用途是貼進**公開** issue,
    // 網址(可能是內網主機名稱)與帳號 email 不該跟著出去。
    const info = [
      `Jargon Vault ${state.me?.version || "unknown"}`,
      `UI language: ${LANG}`,
      `User agent: ${navigator.userAgent}`,
    ].join("\n");
    let key = "about.copied";
    try {
      await navigator.clipboard.writeText(info);
    } catch {
      // clipboard API 在非 HTTPS 或權限受限的環境會失敗。用按鈕文字回饋就好,
      // 不 alert(比照詳細頁程式碼區塊與分享連結的複製鈕)。
      key = "about.copyFailed";
    }
    btn.textContent = t(key);
    btn.disabled = true;
    setTimeout(() => { btn.textContent = t("about.copyEnv"); btn.disabled = false; }, 1500);
  };
}

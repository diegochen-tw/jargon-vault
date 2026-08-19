// 組裝根(composition root):初始化各 view、綁定全域 UI(搜尋列、快捷鍵、
// 主題切換、手機版篩選抽屜、新建按鈕),最後觸發首次載入。
// 模組依賴地圖見 CLAUDE.md;新增 view 時在這裡 init。
import * as actions from "./actions.js?v=20260820a";
import * as api from "./api.js?v=20260820a";
// applyTheme(t) 的參數名撞到 t(),這裡取別名 i18nT
import {applyI18n, t as i18nT} from "./i18n.js?v=20260820a";
import {emit, on} from "./bus.js?v=20260820a";
import {state} from "./store.js?v=20260820a";
import {$, newId} from "./utils.js?v=20260820a";
import {initAuth, showAuthGate, hideAuthGate} from "./views/auth.js?v=20260820a";
import {closeUngrouped, initSidebar, isUngroupedOpen} from "./views/sidebar.js?v=20260820a";
import {initList} from "./views/list.js?v=20260820a";
import {closeDetail, initDetail, isDetailOpen, openNoteById} from "./views/detail.js?v=20260820a";
import {closeSettings, initSettings, isOpen as isSettingsOpen, openSettings} from "./views/settings.js?v=20260820a";
import {closeSrs, initSrs, isSrsOpen} from "./views/srs.js?v=20260820a";
import {initDemoBanner} from "./views/demobanner.js?v=20260820a";
import {closeLightbox, initLightbox, isLightboxOpen} from "./components/lightbox.js?v=20260820a";
import * as theme from "./theme.js?v=20260820a";

/* ── 多國語系:依瀏覽器/作業系統語言套用靜態文字(不支援的語言退回英文) ── */
applyI18n();

/* ── 搜尋列行為 ── */
let timer = null;
function updateClearBtn() {
  $("#btnClearQ").style.display = state.q ? "" : "none";
}
$("#q").addEventListener("input", e => {
  state.q = e.target.value;
  updateClearBtn();
  clearTimeout(timer); timer = setTimeout(actions.search, 120);
});
$("#q").addEventListener("keydown", async e => {
  if (e.key === "Enter" && state.q.trim()) {
    // 沒有命中時,Enter 直接把關鍵字建成新名詞
    clearTimeout(timer);
    await actions.search();
    if (!state.results.length) {
      state.creating = true; state.draftId = newId(); state.focusDescOnOpen = true;
      if (state.aiSettings?.enabled) api.warmupAI().catch(() => {});
      emit("results-changed");
    }
  }
  if (e.key === "Escape") { state.q = ""; e.target.value = ""; updateClearBtn(); actions.search(); }
});
$("#btnClearQ").onclick = () => {
  state.q = ""; $("#q").value = ""; updateClearBtn(); actions.search(); $("#q").focus();
};

/* ── LOGO 品牌自訂:文字/標語可在設定 → Logo/標語 覆寫,或整個隱藏(存 localStorage,
   跟主題/字體/語系同層級的個人化設定;預設值取自套用 i18n 後的原始 DOM 內容) ── */
{
  const h1 = $("#btnLogo h1"), sub = $("#btnLogo .sub");
  const defaultText = h1.textContent, defaultSub = sub.textContent;
  if (localStorage.getItem("gv-logo-hidden") === "1") {
    $("#btnLogo").style.display = "none";
  } else {
    h1.textContent = localStorage.getItem("gv-logo-text") || defaultText;
    sub.textContent = localStorage.getItem("gv-logo-sub") || defaultSub;
  }
}

/* ── LOGO:點下去清空所有篩選條件(關鍵字/標籤/群組/日期/標籤分類),回到未篩選狀態 ── */
$("#btnLogo").onclick = () => {
  actions.resetFilters();  // 內含 emit("filters-reset"),側欄(日期/標籤分類/群組樹)重繪選取狀態
  actions.search();
};
// 搜尋框的 DOM 同步:篩選可能不是從這裡清掉的(LOGO、或新建名詞存檔後
// actions.saveNote 一律清),統一訂閱事件處理,actions 層才不用碰 DOM。
on("filters-reset", () => { $("#q").value = state.q; updateClearBtn(); });

/* ── 全域快捷鍵 ── */
document.addEventListener("keydown", e => {
  // 修飾鍵組合(Ctrl+/ 之類)與 IME 組字中不攔;焦點在任何可輸入處(input/textarea/
  // contenteditable——編輯器說明欄就是 contenteditable)也不攔,否則打字打到一半
  // 會被搶去搜尋框。
  if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
  const el = document.activeElement;
  if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) return;
  e.preventDefault(); $("#q").focus();
});
document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  // 圖片檢視器排第一:它的 z-index 高於所有彈窗,從詳細頁裡開起來時 Esc 必須
  // 先收掉它,而不是把底下的詳細頁一起關掉。
  if (isLightboxOpen()) { closeLightbox(); return; }
  if (isDetailOpen()) { closeDetail(); return; }
  // 設定可以疊在未分組彈窗上面(彈窗右上角的「標籤管理」),所以先關設定再關彈窗
  if (isSettingsOpen()) { closeSettings(); return; }
  if (isSrsOpen()) { closeSrs(); return; }
  if (isUngroupedOpen()) { closeUngrouped(); return; }
  if ($("#dirSidebar").classList.contains("show")) { closeFilterDrawer(); return; }
});

// 未分組彈窗右上角的「標籤管理」:直接把設定疊開在標籤管理分頁。這條線由組裝根
// 接起來——views 之間不互相 import(見 CLAUDE.md 的前端模組地圖)。
$("#ungroupedManage").onclick = () => openSettings("tags");

// 同上:編輯器的「你可能已經記過這個」與設定裡的重複偵測要能點開某筆名詞的詳細頁,
// 但它們都不能 import detail view,所以走 bus(id 放在 state.pendingNoteId)。
on("open-note", () => { if (state.pendingNoteId) openNoteById(state.pendingNoteId); });

/* ── 新建 ── */
$("#btnNew").onclick = () => {
  state.creating = true; state.editing = null; state.draftId = newId();
  // AI 啟用時,趁使用者填表的空檔先叫 Ollama 把模型載進記憶體(fire-and-forget)
  if (state.aiSettings?.enabled) api.warmupAI().catch(() => {});
  emit("results-changed");
};

/* ── 設定選單(⚙️ 下拉:設定/字體縮放/佈景切換/登出),預設收起 ── */
function toggleMenu(open) {
  const show = open ?? !$("#headerMenu").classList.contains("show");
  $("#headerMenu").classList.toggle("show", show);
  $("#btnMenu").setAttribute("aria-expanded", show ? "true" : "false");
  // 模式可能在設定頁的色票被切走(views 不 import 組裝根),開選單時重算按鈕文字
  if (show) syncThemeButton();
}
$("#btnMenu").onclick = e => { e.stopPropagation(); toggleMenu(); };
// 點選單以外的地方(或按 Esc)收起;選單內的字體/主題操作保持開啟,設定/登出各自關閉
document.addEventListener("click", e => {
  if (!e.target.closest(".menu-wrap")) toggleMenu(false);
});
document.addEventListener("keydown", e => { if (e.key === "Escape") toggleMenu(false); });
$("#menuSettings").addEventListener("click", () => toggleMenu(false));

/* ── 主題切換(收進設定選單的一項,預設淺色):核心的 data-theme/data-variant
   與 localStorage 在 theme.js(設定頁的色票也走同一份),這裡只負責 ⚙️ 選單
   按鈕的 icon/文字。 ── */
function syncThemeButton() {
  const t = theme.currentMode();
  const toLight = t !== "light";  // 目前是深色 → 下一步切回淺色
  $("#btnThemeIcon").textContent = toLight ? "☀️" : "🌙";
  $("#btnThemeLabel").textContent = i18nT(toLight ? "menu.themeLight" : "menu.themeDark");
  $("#btnTheme").title = t === "light" ? i18nT("header.themeToDark") : i18nT("header.themeToLight");
}
function applyTheme(t) {
  theme.applyTheme(t);
  syncThemeButton();
}
applyTheme(theme.currentMode());
theme.applyBackground();  // 背景圖是裝置本地偏好,boot 套一次;簾幕色寫 var(--bg),切主題自動跟
$("#btnTheme").onclick = () => applyTheme(theme.currentMode() === "light" ? "dark" : "light");

/* ── 字體縮放:html 的 --font-scale 乘上全站 rem 字級(CSS 預設桌機 1、手機 1.8)。
   A−/A+ 以 0.1 為級距微調(0.7–3),寫成 html 的 inline 自訂屬性覆寫 CSS 預設,
   並記在 localStorage;沒存過就讓 CSS 預設值生效(依螢幕寬度自動 1 或 1.8)。 ── */
const FONT_MIN = 0.7, FONT_MAX = 3, FONT_STEP = 0.1;
function currentFontScale() {
  const v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--font-scale"));
  return v || 1;
}
function showFontScale() {
  $("#fontScaleVal").textContent = Math.round(currentFontScale() * 100) + "%";
}
function applyFontScale(v) {
  document.documentElement.style.setProperty("--font-scale", v);
  localStorage.setItem("gv-fontscale", v);
  showFontScale();
}
{
  const saved = parseFloat(localStorage.getItem("gv-fontscale"));
  if (saved) document.documentElement.style.setProperty("--font-scale", saved);
  showFontScale();
}
$("#btnFontUp").onclick = () =>
  applyFontScale(Math.min(FONT_MAX, Math.round((currentFontScale() + FONT_STEP) * 10) / 10));
$("#btnFontDown").onclick = () =>
  applyFontScale(Math.max(FONT_MIN, Math.round((currentFontScale() - FONT_STEP) * 10) / 10));

/* ── 手機版篩選抽屜(分類目錄/標籤/日期,寬螢幕下這顆按鈕本身就隱藏) ── */
function openFilterDrawer() {
  $("#dirSidebar").classList.add("show");
  $("#dirBackdrop").classList.add("show");
}
function closeFilterDrawer() {
  $("#dirSidebar").classList.remove("show");
  $("#dirBackdrop").classList.remove("show");
}
$("#btnFilter").onclick = openFilterDrawer;
$("#dirSidebarClose").onclick = closeFilterDrawer;
$("#dirBackdrop").onclick = closeFilterDrawer;
// 篩選後(標籤/分類點擊會觸發 search)自動收起抽屜,回到結果列表。
// ⚠ 例外:日期/複習那一塊(.sidebar-h3row → #dateBar → .srs-row)是「調整型」
// 操作——拖日期軌道、切天數、換比較欄位常常連按好幾下,每一下都收抽屜等於
// 每一下都要重開。capture 相先記下這次互動落在哪,results-changed 進來時來自
// 那一塊就不收;flag 每次 pointerdown 重新判定,點標籤/群組照舊收。側欄以外
// 的觸發(header 搜尋列等)不經過這個監聽,flag 殘留舊值時抽屜早已關閉,
// close 本來就是 no-op,無害。
let keepDrawerOnSearch = false;
$("#dirSidebar").addEventListener("pointerdown", e => {
  keepDrawerOnSearch = !!e.target.closest(".sidebar-h3row,#dateBar,.srs-row");
}, true);
on("results-changed", () => {
  if (window.innerWidth < 1024 && !keepDrawerOnSearch) closeFilterDrawer();
});

/* ── 登出 ── */
$("#btnLogout").onclick = async () => {
  await api.logout();
  location.reload();
};

/* ── 初始化:先探測登入態,未登入就顯示登入畫面,不呼叫任何要驗證的 API ── */
let appInited = false;  // views 的 init*() 只能跑一次(重複 init 會重複綁事件)
// 走 /invite/<token> 進來:先問一下這條連結還有沒有效。
// 未登入 → 把資訊顯示在登入畫面上(收到連結的人在決定要不要註冊之前必須看得到);
// 已登入 → 邀請只在註冊時有意義,把網址收乾淨即可。
async function resolveInvite() {
  const m = location.pathname.match(/^\/invite\/(.+)$/);
  if (!m) return;
  state.inviteToken = decodeURIComponent(m[1]);
  state.inviteInfo = await api.peekInvite(state.inviteToken);
}

function renderInviteBanner() {
  const box = $("#authInvite");
  if (!box) return;
  const info = state.inviteInfo;
  if (!info) { box.style.display = "none"; return; }
  box.style.display = "";
  box.className = "auth-invite" + (info.valid ? "" : " invalid");
  box.textContent = info.valid ? i18nT("invite.banner") : i18nT("invite.invalid");
}

async function boot() {
  await resolveInvite();
  const me = await api.getMe();
  state.me = me;
  if (!me) {
    hideApp();
    showAuthGate();
    renderInviteBanner();
    return;
  }
  // 介面語言跟著帳號走:真相在伺服器(me.lang;缺值 = 未設定 = 跟隨裝置),
  // localStorage 的 gv-lang 只是首繪快取。同一台裝置換帳號登入時,快取裡留著
  // 上一個帳號的語言——對不上就把快取校正成帳號的值,reload 一次全面重套
  // (LANG 是模組載入時凍結的常數,reload 是既有的全面套用方式,見 settings.js)。
  // reload 之後兩邊已一致,不會迴圈;一致時這段零成本。
  {
    const want = me.lang || null;
    const cached = localStorage.getItem("gv-lang");
    if (want !== cached) {
      if (want) localStorage.setItem("gv-lang", want);
      else localStorage.removeItem("gv-lang");
      location.reload();
      return;
    }
  }
  // 已登入的人點邀請連結:邀請只在註冊時有意義(繞過白名單),這裡只把
  // 網址收乾淨,不讓 /invite/... 留在網址列。
  if (state.inviteToken) {
    state.inviteToken = ""; state.inviteInfo = null;
    history.replaceState(null, "", "/");
  }
  hideAuthGate();
  showApp();
  // 管理與備份分頁只對 admin 顯示(每次 boot 都更新,登入者換人時跟著變)。
  // 備份是整站的(所有人的資料 + 帳號登記簿),所以跟管理同一個門檻。
  $("#settingsTabAdmin").style.display = me.is_admin ? "" : "none";
  $("#settingsTabBackup").style.display = me.is_admin ? "" : "none";
  // 站台開關搭 /api/auth/me 一起回來(boot 的第一支請求),不必為它再多打一支
  // 設定 API。詳細頁的分享鈕看這個值。
  state.publicShareEnabled = !!me.public_share_enabled;
  state.publicNotebookEnabled = !!me.public_notebook_enabled;
  // 範例資料的置頂行,同樣搭 /me 那班車。⚠ 放在 appInited 那塊**之前**且每次
  // boot 都跑:同一台裝置換帳號登入時,上一個帳號的橫幅狀態必須跟著換掉。
  state.demoBanner = !!me.demo_banner;
  state.demoSiteUrl = me.demo_site_url || "";
  initDemoBanner();
  if (!appInited) {
    appInited = true;
    initSidebar();
    initList();
    initDetail();
    initSettings();
    initSrs();
    initLightbox();
    actions.loadAISettings();  // AI 生成設定只在明確操作時變動,不進 refreshAll
    actions.loadPlugins();     // 外掛清單同理:只在外掛管理頁操作時變動
    actions.refreshAll();
  }
}
function showApp() { document.querySelector(".wrap").style.display = ""; }
function hideApp() { document.querySelector(".wrap").style.display = "none"; }
initAuth(boot);  // 綁定登入表單一次;登入/註冊成功時呼叫 boot() 重新探測登入態
on("auth-expired", () => { hideApp(); showAuthGate(); });
boot();

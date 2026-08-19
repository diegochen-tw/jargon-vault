// 協調層:api(資料) + store(狀態) + bus(通知重繪)的組合動作。
// views 的事件處理一律呼叫這裡,不直接打 API,確保「資料變動後該重繪什麼」只寫在一個地方。
import * as api from "./api.js?v=20260820a";
import {emit} from "./bus.js?v=20260820a";
import {srsSize} from "./config.js?v=20260820a";
import {t} from "./i18n.js?v=20260820a";
import {state} from "./store.js?v=20260820a";
import {normKey} from "./utils.js?v=20260820a";

// Session 中途失效(cookie 過期/被登出)時,寫入動作會收到 401——攔在這裡
// emit 一個事件讓 app.js 切回登入畫面,不用每個呼叫端各自判斷。
function authExpired(r) {
  if (r.status === 401) { emit("auth-expired"); return true; }
  return false;
}

// 日期橫條篩選:dateDays 天的區間,結尾距今天 dateOffset 天,以本地時區日界計算。
// dateOffset=0 → [今天-(dateDays-1) 00:00, 明天 00:00),◀ 步進一次 offset += dateDays。
// search() 與 loadMore() 都要送同一組 since/until,抽出來避免兩處各自維護一份計算。
function _dateRangeParams() {
  // dateField 不受 dateActive 影響:不篩日期時它沒有作用,但一起回傳可以讓
  // 每個呼叫端只解構一次,不必各自去記得補上第三個值。
  if (!state.dateActive) return {since: 0, until: 0, dateField: state.dateField};
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const untilMs = startOfToday.getTime() + 86400000 - state.dateOffset * 86400000;
  const until = untilMs / 1000;
  const since = (untilMs - state.dateDays * 86400000) / 1000;
  return {since, until, dateField: state.dateField};
}

export async function search() {
  const {since, until, dateField} = _dateRangeParams();
  // 語意檢索是另一支端點,回傳形狀刻意做成 /api/search 的超集({results,
  // has_more} 再多兩個欄位),所以這裡只換 URL,list.js 的卡片渲染一行都不用動。
  const semantic = state.semantic && !!state.q.trim();
  const data = semantic
    ? await api.semanticSearch(state.q, state.tags, state.group, since, until, state.template, state.sort, state.markedOnly, dateField)
    : await api.searchNotes(state.q, state.tags, state.group, 0, since, until, state.template, state.sort, state.markedOnly, 0, dateField);
  state.results = data.results;
  state.hasMore = data.has_more;
  state.semanticNeedsIndex = semantic && !!data.needs_index;
  state.semanticFailed = semantic && !!data.failed;
  state.semanticUnindexed = semantic ? (data.unindexed || 0) : 0;
  emit("results-changed");
}

// 公開分享連結。刻意不 emit 任何事件、也不碰 state.results:分享狀態不是名詞
// 內容的一部分(後端也不寫歷史版本、不動 updated),重繪列表毫無意義。
export async function createShareLink(id) {
  const r = await api.createShareLink(id);
  if (!r.ok) {
    if (authExpired(r)) return null;
    alert((await r.json().catch(() => ({}))).detail || t("share.createFailed"));
    return null;
  }
  return r.json();
}

export async function revokeShareLink(id) {
  const r = await api.revokeShareLink(id);
  if (!r.ok) {
    if (authExpired(r)) return null;
    alert((await r.json().catch(() => ({}))).detail || t("share.createFailed"));
    return null;
  }
  return r.json();
}

// 「載入更多」:再多載入一頁(PAGE_SIZE=50),疊加在既有結果後面,不是取代。
// offset 直接用「目前已載入筆數」——這就是正確的分頁游標,不用另外維護計數器,
// 也不會跟 state.results 實際長度不同步。loadingMore 防連點造成重複載入/重複 concat。
export async function loadMore() {
  // 語意檢索刻意不分頁:RRF 名次在 Python 端算,跨兩臂的全域 OFFSET 無法下推
  // (見 app/semantic.py:search_hybrid)。後端一律回 has_more=false,這裡再擋
  // 一層是因為「按鈕不顯示」跟「按了不會做錯事」是兩件事。
  if (state.semantic || state.loadingMore || !state.hasMore) return;
  state.loadingMore = true;
  try {
    const {since, until, dateField} = _dateRangeParams();
    const offset = state.results.length;
    const data = await api.searchNotes(state.q, state.tags, state.group, 0, since, until,
      state.template, state.sort, state.markedOnly, offset, dateField);
    state.results = state.results.concat(data.results);
    state.hasMore = data.has_more;
    emit("results-appended");
  } finally {
    state.loadingMore = false;
  }
}

// 書籤標記切換。標記不是內容編輯,所以不走 saveNote/refreshAll(那會寫歷史版本、
// 重載標籤與樣板):就地改 state.results 那一筆再 emit,讓卡片徽章與詳細頁刷新即可。
// 只有「只顯示有標記」篩選開著時才要重查——這時取消標記代表那筆該從列表消失。
// silent=true:只更新資料,不 emit、也不重查。給複習彈窗用——複習中重繪主列表
// 只會白白銷毀縮圖(這個 view 的通則見 views/srs.js 檔頭),而且抽到的卡多半
// 根本不在 state.results 裡,重繪也反映不出任何東西。呼叫端自己翻按鈕的 .on。
export async function setNoteMarked(id, marked, {silent = false} = {}) {
  const r = await api.setNoteMarked(id, marked);
  if (!r.ok) {
    if (authExpired(r)) return false;
    alert((await r.json().catch(() => ({}))).detail || t("actions.markFailed"));
    return false;
  }
  const n = state.results.find(x => x.id === id);
  if (n) n.marked = marked;
  if (silent) return true;
  if (state.markedOnly) await search(); else emit("results-changed");
  return true;
}

// 重新讀一筆的最新內容。**刻意不碰任何 state、不 emit**:呼叫端(複習彈窗暫離
// 編輯後的恢復)要的就是「這一筆現在長什麼樣」,不是把它塞回列表。
// 讀不到(已被刪、或不是我的)一律回 null,不 alert——呼叫端對「不見了」有自己
// 的處置(把那張卡從這一輪拿掉),彈一個錯誤訊息只會多一次打斷。
export async function reloadNote(id) {
  const r = await api.getNote(id);
  if (!r.ok) { authExpired(r); return null; }
  return r.json().catch(() => null);
}

// ── SRS 複習 ──────────────────────────────────────────────────────
// 抽一輪卡。範圍就是側欄現在圈出來的東西(標籤/群組/樣板/日期/書籤),
// 所以直接沿用 search() 那組參數與同一支 _dateRangeParams()——兩份算法遲早漂移。
// 關鍵字刻意不送(後端 _filters() 沒有這個維度,見 app/routers/srs.py 檔頭)。
export async function drawSrs() {
  state.srsLoading = true;
  const {since, until, dateField} = _dateRangeParams();
  const data = await api.srsDraw(state.tags, state.group, 0, since, until,
                                 state.template, state.markedOnly, state.srsScopeAll, dateField,
                                 srsSize());
  state.srsLoading = false;
  if (!data) { alert(t("srs.drawFailed")); return false; }
  state.srsCards = data.cards || [];
  state.srsPool = data.pool || 0;
  state.srsIndex = 0;
  state.srsRevealed = false;
  return true;
}

// 自評一張。**刻意不 emit 任何事件**:複習不動 updated,主列表的排序與內容
// 都沒變,重繪只會白白銷毀縮圖、清掉編輯器裡打到一半的內容。就地更新手上那
// 張卡的排程狀態,讓彈窗自己顯示即可。
export async function reviewSrs(id, remembered) {
  const r = await api.srsReview(id, remembered);
  if (!r.ok) {
    if (authExpired(r)) return false;
    alert((await r.json().catch(() => ({}))).detail || t("srs.reviewFailed"));
    return false;
  }
  const updated = await r.json().catch(() => null);
  const card = state.srsCards.find(x => x.id === id);
  if (card && updated) { card.srs_box = updated.srs_box; card.srs_due = updated.srs_due; }
  return true;
}

// 點卡片或詳細內容裡的標籤 chip:切換成「只看這個標籤」,比照點側欄群組
// 節點的邏輯清掉其他篩選維度,從乾淨狀態開始瀏覽;若目前已經是只篩選這一個
// 標籤,再點一次代表取消(回到未篩選),此時不動 q/group——那兩個是
// 「進入篩選」才需要清空,取消篩選不該連帶清掉使用者可能之後另外打的字。
export async function filterByTag(tag) {
  if (state.tags.length === 1 && state.tags[0] === tag) {
    state.tags = [];
  } else {
    state.q = "";
    state.group = "";
    state.tags = [tag];
  }
  emit("tags-changed");  // 側欄群組樹的選取樣式沒有其他管道可以刷新,借用既有事件重繪
  await search();
}

export async function loadTags() {
  const d = await api.getTags();
  state.allTags = d.tags;
  state.allGroups = d.groups;
  emit("tags-changed");
}

export async function loadTemplates() {
  state.allTemplates = await api.getTemplates();
  emit("templates-changed");
}

export async function loadAISettings() {
  state.aiSettings = await api.getAISettings();
  emit("ai-settings-changed");
}

export async function loadPlugins() {
  const d = await api.getPlugins();
  state.plugins = d.plugins || [];
  state.pluginScanErrors = d.scan_errors || [];  // 只有 admin 拿得到這個欄位
}

export function pluginInstalled(id) {
  return state.plugins.some(p => p.id === id && p.installed);
}

// 已安裝**且未停用**。功能入口(編輯器的文章批次生成假選項)一律看這個——
// 停用的意義就是「裝著但入口消失」,對應後端的 plugins.is_active()。
export function pluginActive(id) {
  return state.plugins.some(p => p.id === id && p.installed && p.enabled !== false);
}

// 外掛安裝/解除/設定。成功後重載外掛清單;失敗 alert 並回傳 false。
export async function setPluginInstalled(id, installed) {
  const r = installed ? await api.installPlugin(id) : await api.uninstallPlugin(id);
  if (!r.ok) {
    if (authExpired(r)) return false;
    alert((await r.json()).detail || t("plugin.opFailed"));
    return false;
  }
  await loadPlugins();
  // field-template 外掛的安裝/解除會註冊/移除欄位樣板:重載樣板並 emit
  // templates-changed,編輯器的樣板下拉與設定 → 欄位樣板才會跟著更新
  if ((state.plugins.find(p => p.id === id) || {}).category === "field-template") {
    await loadTemplates();
  }
  return true;
}

// 停用/啟用(不動安裝狀態與設定)。field-template 外掛的停用會把樣板的
// enabled 一起關掉,重載樣板讓編輯器下拉跟著變——與 setPluginInstalled 同理。
export async function setPluginEnabled(id, enabled) {
  const r = await api.setPluginEnabled(id, enabled);
  if (!r.ok) {
    if (authExpired(r)) return false;
    alert((await r.json()).detail || t("plugin.opFailed"));
    return false;
  }
  await loadPlugins();
  if ((state.plugins.find(p => p.id === id) || {}).category === "field-template") {
    await loadTemplates();
  }
  return true;
}

export async function savePluginConfig(id, config) {
  const r = await api.savePluginConfig(id, config);
  if (!r.ok) {
    if (authExpired(r)) return false;
    alert((await r.json()).detail || t("plugin.saveFailed"));
    return false;
  }
  await loadPlugins();
  return true;
}

// 站台封裝管理(admin)。成功後重載外掛清單(型錄變了)。
export async function uploadPluginPackage(file) {
  const r = await api.uploadPluginPackage(file);
  if (!r.ok) {
    if (authExpired(r)) return false;
    alert((await r.json()).detail || t("plugin.uploadFail"));
    return false;
  }
  await loadPlugins();
  return true;
}

export async function deleteSitePlugin(id) {
  const r = await api.deleteSitePlugin(id);
  if (!r.ok) {
    if (authExpired(r)) return false;
    alert((await r.json()).detail || t("plugin.opFailed"));
    return false;
  }
  await loadPlugins();
  return true;
}

// AI 設定存檔。成功後更新 state.aiSettings;失敗 alert 並回傳 false。
export async function saveAISettings(payload) {
  const r = await api.saveAISettings(payload);
  if (!r.ok) {
    if (authExpired(r)) return false;
    alert((await r.json()).detail || t("actions.aiSaveFailed"));
    return false;
  }
  state.aiSettings = await r.json();
  emit("ai-settings-changed");   // 語意 icon 等入口要即時跟著開/關
  return true;
}

// ── 語意檢索索引 ────────────────────────────────────────────────────

export function semanticStatus() {
  return api.semanticStatus();
}

// 一批一批做,讓呼叫端顯示進度——比照設定 → 內容的批次圖片壓縮,不需要背景
// 工作者也不需要串流。回 {done, embedded, remaining, total} 或 {error}。
export async function semanticReindexBatch(limit) {
  const r = await api.semanticReindex(limit);
  if (!r.ok) {
    if (authExpired(r)) return {error: t("sem.reindexFailed")};
    // 回應不是 JSON = 應用程式前面有東西先斷了(反向代理逾時回 HTML 錯誤頁),
    // 附上狀態碼,「HTTP 524」比一句泛用的失敗訊息有診斷價值得多
    const d = await r.json().catch(() => null);
    return {error: (d && d.detail) || `${t("sem.reindexFailed")} (HTTP ${r.status})`};
  }
  const d = await r.json();
  return {...d, done: d.remaining === 0};
}

export async function clearSemanticIndex() {
  const r = await api.clearSemanticIndex();
  if (!r.ok) { authExpired(r); return false; }
  return true;
}

// `[[名詞]]` 連結的解析對照表。名詞增刪改名都會改變它,所以跟著 refreshAll 走。
// 同 normKey 撞在一起時後蓋前——那本身就是重複的名詞,該由重複偵測處理,
// 不是連結解析要煩惱的事(後端 search.resolve_names 也是同一個取捨)。
export async function loadNoteNames() {
  const names = await api.getNames();
  state.noteIndex = new Map(names.map(n => [normKey(n.name), n]));
}

export function refreshAll() {
  // loadTemplates() 附帶的 count(側欄「標籤分類」用)會隨名詞增刪變動,故一併刷新
  return Promise.all([search(), loadTags(), loadTemplates(), loadNoteNames()]);
}

// 合併重複的名詞:sourceIds 併進 targetId,來源進回收桶。
// 成功回傳後端報告({note, merged, dropped_fields, relinked}),失敗 alert 並回傳 null。
export async function mergeNotes(targetId, sourceIds) {
  const r = await api.mergeNotes(targetId, sourceIds);
  if (!r.ok) {
    if (authExpired(r)) return null;
    alert((await r.json().catch(() => ({}))).detail || t("dedup.mergeFailed"));
    return null;
  }
  const report = await r.json();
  await refreshAll();
  return report;
}

// 清空所有篩選維度(關鍵字/標籤/群組/標籤分類/日期),回到未篩選狀態。
// dateDays(區間天數)是使用者的偏好設定,不是篩選條件,刻意保留。
// 不碰 DOM——搜尋框的同步由 app.js 訂閱 filters-reset 處理。
export function resetFilters() {
  state.q = "";
  state.tags = []; state.group = ""; state.template = "";
  state.dateActive = false; state.dateOffset = 0;
  emit("filters-reset");
}

// 儲存(新建或更新)。成功時關閉編輯器並全面刷新、回傳存下去的那一筆;
// 失敗時 alert 並回傳 false(**不動任何 state**——使用者打到一半的內容必須留著)。
export async function saveNote(id, payload) {
  const r = await api.saveNote(id, payload);
  if (!r.ok) {
    if (authExpired(r)) return false;
    // 409 = 樂觀鎖擋下:這筆在別處被改過。後端的 detail 只講事實,這裡改用
    // i18n 訊息,因為使用者需要的是「你的內容還在,先複製起來」這個指引。
    if (r.status === 409) { alert(t("actions.saveConflict")); return false; }
    alert((await r.json().catch(() => ({}))).detail || t("actions.saveFailed"));
    return false;
  }
  // 新建/更新都回傳整筆。新建時記下新 id,下面用來確認它有沒有被篩選擋掉;
  // 更新時呼叫端要拿回新的 updated 當下一次樂觀鎖的基準(見 detail.js 的畫重點存回)。
  const saved = await r.json().catch(() => null);
  const newId = id ? null : (saved || {}).id;
  state.editing = null;
  state.creating = false;
  state.draftId = null;
  state.newName = "";
  // 新建後一律清掉所有篩選回到全部列表:用搜尋框關鍵字建的新名詞必然命中 state.q,
  // 舊的「不在結果裡才清」判斷在這條最常見的路徑上永遠不會觸發,搜尋框的字就一直留著。
  // 只針對新建:編輯既有名詞時使用者正在依某個條件瀏覽,篩選不該憑空消失。
  if (newId) resetFilters();
  await refreshAll();
  return saved ?? true;
}

export async function removeNote(id) {
  if (!confirm(t("actions.deleteNoteConfirm"))) return;
  await api.deleteNote(id);
  state.editing = null;
  await refreshAll();
}

// 回收桶還原:名詞回到列表、標籤統計也會變,所以整個刷新。
// 永久刪除/清空不用刷新——那些東西早就不在列表裡了(刪除當下就移出索引)。
export async function restoreTrashNote(id) {
  const r = await api.restoreTrashNote(id);
  if (!r.ok) {
    if (authExpired(r)) return false;
    alert((await r.json().catch(() => ({}))).detail || t("trash.restoreFailed"));
    return false;
  }
  await refreshAll();
  return true;
}

export async function purgeTrashNote(id) {
  const r = await api.purgeTrashNote(id);
  if (!r.ok) {
    if (authExpired(r)) return false;
    alert((await r.json().catch(() => ({}))).detail || t("trash.purgeFailed"));
    return false;
  }
  return true;
}

export async function emptyTrash() {
  const r = await api.emptyTrash();
  if (!r.ok) {
    if (authExpired(r)) return null;
    alert((await r.json().catch(() => ({}))).detail || t("trash.purgeFailed"));
    return null;
  }
  return (await r.json()).deleted;
}

export async function renameTag(name, newName) {
  const r = await api.renameTag(name, newName);
  if (!r.ok) {
    if (authExpired(r)) return false;
    alert((await r.json()).detail || t("actions.renameFailed"));
    return false;
  }
  await refreshAll();
  return true;
}

export async function deleteTag(name) {
  if (!confirm(t("actions.deleteTagConfirm", {name}))) return;
  await api.deleteTag(name);
  await refreshAll();
}

// 把 absorb 裡的標籤全部併進 keep(標籤相似度重複偵測的套用動作)。
// 走 refreshAll 而不是只有 loadTags:名詞身上的標籤真的被改寫了,列表與側欄都要跟著更新。
export async function mergeTags(keep, absorb) {
  const r = await api.mergeTags(keep, absorb);
  if (!r.ok) {
    if (authExpired(r)) return false;
    // detail 是機器碼(app/service.py:merge_tags),不拿來當顯示文字。
    alert(t("actions.tagMergeFailed"));
    return false;
  }
  await refreshAll();
  return true;
}

// 樣板存檔(id 空 = 新建)。成功後重載樣板;失敗 alert 並回傳 false。
export async function saveTemplate(id, payload) {
  const r = await api.saveTemplate(id, payload);
  if (!r.ok) {
    if (authExpired(r)) return false;
    alert((await r.json()).detail || t("actions.tplSaveFailed"));
    return false;
  }
  await loadTemplates();
  return true;
}

// 切換內建樣板啟用狀態。成功後重載樣板(側欄分類/編輯器下拉一起刷新);失敗 alert 回 false。
export async function setTemplateEnabled(id, enabled) {
  const r = await api.setTemplateEnabled(id, enabled);
  if (!r.ok) {
    if (authExpired(r)) return false;
    alert((await r.json().catch(() => ({}))).detail || t("actions.tplSaveFailed"));
    return false;
  }
  await loadTemplates();
  return true;
}

export async function deleteTemplate(id) {
  if (!confirm(t("actions.tplDeleteConfirm"))) return;
  const r = await api.deleteTemplate(id);
  if (!r.ok) {
    if (authExpired(r)) return;
    alert((await r.json()).detail || t("actions.tplDeleteFailed"));
    return;
  }
  await loadTemplates();
}

// 恢復內建/外掛樣板的出廠定義(名稱/欄位/AI 指示整包覆寫回種子;啟用狀態保留)
export async function resetTemplate(id) {
  if (!confirm(t("actions.tplResetConfirm"))) return;
  const r = await api.resetTemplate(id);
  if (!r.ok) {
    if (authExpired(r)) return;
    alert((await r.json().catch(() => ({}))).detail || t("actions.tplResetFailed"));
    return;
  }
  await loadTemplates();
}

// 把所選標籤設為某群組(group 空字串 = 移出群組)
export async function assignTagGroup(group, tags) {
  const r = await api.setTagGroup(group, tags);
  if (!r.ok) {
    if (authExpired(r)) return false;
    alert((await r.json()).detail || t("actions.groupSetFailed"));
    return false;
  }
  await loadTags();
  return true;
}

// 群組改名。成功回 true;改成既有群組名 = 合併兩組(確認由呼叫端負責)。
export async function renameGroup(name, newName) {
  const r = await api.renameGroup(name, newName);
  if (!r.ok) {
    if (authExpired(r)) return false;
    alert((await r.json().catch(() => ({}))).detail || t("actions.renameFailed"));
    return false;
  }
  // 側欄的群組篩選若正停在舊名稱上,不跟著改名就會停在一個已不存在的群組,
  // 列表整個變空——使用者只會覺得「改個名字資料就不見了」。
  if (state.group === name) state.group = newName;
  await loadTags();
  await refreshAll();
  return true;
}

export async function dissolveGroup(name) {
  if (!confirm(t("actions.dissolveConfirm", {name}))) return;
  await api.dissolveGroup(name);
  await loadTags();
}

// 批次刪除名詞(group 空 = 全部)。成功回傳刪除筆數並全面刷新;失敗 alert 回傳 null。
// 確認由呼叫端(settings)負責——這是永久且不可復原的動作。
export async function purgeNotes(group) {
  const r = await api.purgeNotes(group);
  if (!r.ok) {
    if (authExpired(r)) return null;
    alert((await r.json().catch(() => ({}))).detail || t("actions.purgeFailed"));
    return null;
  }
  const deleted = (await r.json()).deleted;
  await refreshAll();
  return deleted;
}

// 刪掉範例資料(置頂行)。成功回傳刪除筆數、清掉 state 的旗標並全面刷新;
// 失敗 alert 回傳 null。確認由呼叫端負責(永久刪除、不進回收桶)。
// ⚠ 不是 purgeNotes("")——那支會把使用者自己建的名詞一起刪掉。
export async function purgeDemo() {
  const r = await api.purgeDemo();
  if (!r.ok) {
    if (authExpired(r)) return null;
    alert(t("actions.purgeFailed"));
    return null;
  }
  const deleted = (await r.json()).deleted;
  state.demoBanner = false;
  await refreshAll();
  return deleted;
}

export async function dissolveAllGroups() {
  if (!confirm(t("actions.dissolveAllConfirm"))) return;
  await api.dissolveAllGroups();
  await loadTags();
}

// 只匯入欄位樣板(設定 → 欄位樣板)。同 id 的樣板不覆蓋,略過數一併回報——
// 同一份檔案匯入第二次會一個都沒新增,不講的話使用者只會覺得沒反應。
export async function importTemplateFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  const r = await api.importTemplates(fd);
  if (authExpired(r)) return;
  const d = await r.json().catch(() => ({}));
  if (!r.ok) {
    alert(d.detail || t("transfer.importFailed"));
    return;
  }
  let msg = t("tplmgr.importDone", {n: d.added});
  if (d.skipped) msg += t("tplmgr.importSkipped", {n: d.skipped});
  alert(msg);
  await loadTemplates();
}

export async function importFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  const r = await api.importNotes(fd);
  if (authExpired(r)) return;
  const d = await r.json();
  if (!r.ok) {
    alert(d.detail || t("transfer.importFailed"));
    return;
  }
  let msg = t("transfer.importDone", {n: d.imported});
  if (d.templates) msg += t("transfer.importTemplates", {n: d.templates});  // v3 匯出檔才有
  if (d.assets) msg += t("transfer.importAssets", {n: d.assets});  // ZIP 匯入才有(圖片/附件實體檔)
  if (d.errors.length) msg += t("transfer.importSkipped", {n: d.errors.length}) + "\n" + d.errors.join("\n");
  alert(msg);
  await refreshAll();
}

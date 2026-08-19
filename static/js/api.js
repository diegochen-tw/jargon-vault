// API 層:對後端的所有 HTTP 呼叫都集中在這裡。
// 純資料進出——不碰 DOM、不碰 state、不 alert;錯誤處理交給呼叫端(actions/views)。
// 端點對應的後端實作見 app/routers/。

import { aiLang, LANG } from "./i18n.js?v=20260820a";

export function register(email, password, invite = "") {
  return fetch("/api/auth/register", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({email, password, invite}),
  });
}

export function login(email, password) {
  return fetch("/api/auth/login", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({email, password}),
  });
}

export function logout() {
  return fetch("/api/auth/logout", {method: "POST"});
}

/* ── 帳號安全(設定 → 帳號):登入方式與密碼 ─────────────────────── */

// 變更/首次設定密碼。帳號已有密碼時 current_password 必填(後端驗)。
export function changePassword(currentPassword, newPassword) {
  return fetch("/api/auth/password", {
    method: "PUT", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({current_password: currentPassword, new_password: newPassword}),
  });
}

// 停用 email 密碼登入(後端擋「唯一的登入方式」)。
export function removePassword() {
  return fetch("/api/auth/password", {method: "DELETE"});
}

// 解除 Google 連結(後端擋「唯一的登入方式」)。
export function unlinkGoogle() {
  return fetch("/api/auth/google", {method: "DELETE"});
}

// 介面語言跟著帳號走(null = 清除,回到跟隨裝置語言)。真相在伺服器,
// localStorage 的 gv-lang 只是首繪快取,boot 時對帳(見 app.js)。
export function putLang(lang) {
  return fetch("/api/auth/lang", {
    method: "PUT", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({lang}),
  });
}

// 探測目前登入狀態:已登入回傳 {id, email, is_admin},未登入回傳 null(不 throw)。
export async function getMe() {
  const r = await fetch("/api/auth/me");
  return r.ok ? r.json() : null;
}

// 公開:登入畫面用來決定是否顯示「註冊」切換與 Google 按鈕。
export async function getAuthConfig() {
  const r = await fetch("/api/auth/config");
  return r.ok ? r.json() : {registration_open: true, google_enabled: false};
}

/* ── 管理者 API(僅 admin 可用;非 admin 會收到 403)──────────────── */

export async function getAdminSettings() {
  const r = await fetch("/api/admin/settings");
  return r.json();
}

export function setRegistrationMode(mode) {
  return fetch("/api/admin/settings/registration", {
    method: "PUT", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({mode}),
  });
}

export function setWhitelist(emails) {
  return fetch("/api/admin/settings/whitelist", {
    method: "PUT", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({emails}),
  });
}

export function setOAuth(cfg) {
  return fetch("/api/admin/settings/oauth", {
    method: "PUT", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(cfg),  // {enabled, client_id, client_secret}
  });
}

export async function getAdminUsers() {
  const r = await fetch("/api/admin/users");
  return (await r.json()).users;
}

export function setUserAdmin(id, isAdmin) {
  return fetch(`/api/admin/users/${encodeURIComponent(id)}/admin`, {
    method: "PUT", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({is_admin: isAdmin}),
  });
}

export function deleteUser(id) {
  return fetch(`/api/admin/users/${encodeURIComponent(id)}`, {method: "DELETE"});
}

// ── admin:登入保護(失敗鎖定的門檻)────────────────────────────────
export function setRateLimit(cfg) {
  return fetch("/api/admin/settings/ratelimit", {
    method: "PUT", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(cfg),
  });
}

export function resetRateLimit() {
  return fetch("/api/admin/ratelimit/reset", {method: "POST"});
}

// ── admin:整站備份與還原 ──────────────────────────────────────────
// 跟「匯出/入」(setting → 分享與備份)是兩件不同的事:那個是使用者搬自己的庫
// 到另一台,這個是整站(所有人的資料 + 帳號 + 站台設定)的災難復原,只有 admin
// 能用。後端的取捨對照表見 app/backup.py 檔頭。
export async function getBackups() {
  const r = await fetch("/api/admin/backups");
  return r.json();  // {backups, settings, last_auto, due}
}

export function setBackupSettings(cfg) {
  return fetch("/api/admin/settings/backup", {
    method: "PUT", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(cfg),
  });
}

export function createBackup() {
  return fetch("/api/admin/backups", {method: "POST"});
}

export function deleteBackup(name) {
  return fetch(`/api/admin/backups/${encodeURIComponent(name)}`, {method: "DELETE"});
}

// 下載走瀏覽器的一般導覽(不是 fetch):FileResponse 帶著 Content-Disposition,
// 交給瀏覽器處理才會出現正常的「另存新檔」,而不是把整包 ZIP 讀進記憶體。
export function backupDownloadUrl(name) {
  return `/api/admin/backups/${encodeURIComponent(name)}/download`;
}

export async function inspectBackup(name) {
  const r = await fetch(`/api/admin/backups/${encodeURIComponent(name)}/inspect`);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "inspect failed");
  return r.json();
}

export function restoreBackup(name) {
  return fetch(`/api/admin/backups/${encodeURIComponent(name)}/restore`, {method: "POST"});
}

export function uploadBackup(file, restore) {
  const fd = new FormData();
  fd.append("file", file);
  return fetch(`/api/admin/backups/upload?restore=${restore ? 1 : 0}`,
               {method: "POST", body: fd});
}

// 回傳整個解析後的物件({results, has_more}),不只 results——「載入更多」要
// 靠 has_more 決定按鈕顯不顯示,呼叫端(actions.js)自己決定怎麼用兩個欄位。
export async function searchNotes(q, tags, group, days, since = 0, until = 0, template = "", sort = "updated", marked = false, offset = 0, dateField = "updated") {
  const u = `/api/search?q=${encodeURIComponent(q)}&tags=${encodeURIComponent(tags.join(","))}`
    + `&group=${encodeURIComponent(group)}&template=${encodeURIComponent(template)}`
    + `&days=${days}&since=${since}&until=${until}&sort=${encodeURIComponent(sort)}`
    + `&date_field=${encodeURIComponent(dateField)}`
    + `&marked=${marked ? 1 : 0}&offset=${offset}`;
  const r = await fetch(u);
  if (!r.ok) return {results: [], has_more: false};
  return r.json();
}

// ── 語意檢索 ──────────────────────────────────────────────────────
// 參數與 searchNotes 一致(少一個 offset:語意檢索刻意不分頁,見
// app/semantic.py:search_hybrid),所以 actions.search() 只要換一支就好。
export async function semanticSearch(q, tags, group, since = 0, until = 0, template = "", sort = "updated", marked = false, dateField = "updated") {
  const u = `/api/semantic/search?q=${encodeURIComponent(q)}&tags=${encodeURIComponent(tags.join(","))}`
    + `&group=${encodeURIComponent(group)}&template=${encodeURIComponent(template)}`
    + `&since=${since}&until=${until}&sort=${encodeURIComponent(sort)}&marked=${marked ? 1 : 0}`
    + `&date_field=${encodeURIComponent(dateField)}`;
  const r = await fetch(u);
  // ⚠ 失敗**不能**當成 needs_index:模型服務連不上跟索引沒建好是兩件事,
  // 混為一談會把使用者指去設定頁按「建立索引」,而那顆按鈕當然也會失敗。
  if (!r.ok) return {results: [], has_more: false, needs_index: false, failed: true};
  return r.json();
}

export async function semanticStatus() {
  const r = await fetch("/api/semantic/status");
  if (!r.ok) return null;
  return r.json();
}

// limit>0 時只做一批,由呼叫端跑迴圈顯示進度(比照批次圖片壓縮的做法)。
export function semanticReindex(limit) {
  return fetch("/api/semantic/reindex", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({limit}),
  });
}

export function clearSemanticIndex() {
  return fetch("/api/semantic/index", {method: "DELETE"});
}

// ── SRS 複習 ──────────────────────────────────────────────────────
// 篩選維度與 searchNotes 一致但少兩個:沒有 offset(一輪就是一輪,沒有第二頁)、
// 沒有 q(後端的 _filters() 沒有關鍵字維度,見 app/routers/srs.py 的檔頭)。
// allScope=true 時後端忽略全部篩選,改抽全庫。
// size = 這一輪要幾張(設定 → 偏好設定);0 = 用後端預設,超出範圍後端會夾住。
export async function srsDraw(tags, group, days, since = 0, until = 0, template = "", marked = false, allScope = false, dateField = "updated", size = 0) {
  const u = `/api/srs/draw?tags=${encodeURIComponent(tags.join(","))}`
    + `&group=${encodeURIComponent(group)}&template=${encodeURIComponent(template)}`
    + `&days=${days}&since=${since}&until=${until}&marked=${marked ? 1 : 0}`
    + `&date_field=${encodeURIComponent(dateField)}&all_scope=${allScope ? 1 : 0}`
    + `&size=${size}`;
  const r = await fetch(u);
  if (!r.ok) return null;
  return r.json();
}

// 自評。專用端點:不寫歷史版本、不動 updated(理由同 setNoteMarked)。
export function srsReview(id, remembered) {
  return fetch(`/api/srs/${encodeURIComponent(id)}/review`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({remembered}),
  });
}

// ── 站台註冊邀請連結 ──────────────────────────────────────────────
// 預覽不需要登入(公開端點);產生/列出/撤銷收站台 admin。
export async function peekInvite(token) {
  const r = await fetch(`/api/invite/${encodeURIComponent(token)}`);
  if (!r.ok) return {valid: false};
  return r.json();  // {valid}
}

export async function listInvites() {
  const r = await fetch("/api/admin/invites");
  if (!r.ok) return [];
  return (await r.json()).invites;
}

export function createInvite(cfg) {
  return fetch("/api/admin/invites", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(cfg),  // {uses, ttl_days}
  });
}

export function revokeInvite(nonce) {
  return fetch(`/api/admin/invites/${encodeURIComponent(nonce)}`, {method: "DELETE"});
}

// ── 公開筆記快照(擁有者面;公開讀取端點 /api/p/* 不需登入,只有 publish.js 用)──
export async function listPublications() {
  const r = await fetch("/api/publish");
  if (!r.ok) return {publications: [], enabled: false};
  return r.json();  // {publications: [manifest…], enabled}
}

export function createPublication(cfg) {
  return fetch("/api/publish", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(cfg),  // {title, tags, group, pid?(重新發佈)}
  });
}

export function revokePublication(pid) {
  return fetch(`/api/publish/${encodeURIComponent(pid)}`, {method: "DELETE"});
}

export async function adminListPublished() {
  const r = await fetch("/api/admin/published");
  if (!r.ok) return [];
  return (await r.json()).publications;  // [{…manifest, orphan}]
}

export function adminDeletePublished(pid) {
  return fetch(`/api/admin/published/${encodeURIComponent(pid)}`, {method: "DELETE"});
}

// ── 單筆公開分享連結(擁有者視角;實際開啟內容的公開端點不需登入)──────
export async function getShareLink(id) {
  const r = await fetch(`/api/notes/${encodeURIComponent(id)}/share`);
  if (!r.ok) return null;
  return r.json();  // {enabled, token, url}
}

export function createShareLink(id) {
  return fetch(`/api/notes/${encodeURIComponent(id)}/share`, {method: "POST"});
}

export function revokeShareLink(id) {
  return fetch(`/api/notes/${encodeURIComponent(id)}/share`, {method: "DELETE"});
}

// 全部分享連結的統計與一鍵撤銷(設定 → 整理與清理)。
export async function getShareStats() {
  const r = await fetch("/api/shares");
  if (!r.ok) return null;
  return r.json();  // {count, ttl_hours}
}

export function revokeAllShares() {
  return fetch("/api/shares", {method: "DELETE"});
}

// ── admin:公開分享連結與公開筆記的站台總開關 ────────────────────
export function setSharingFlags(publicShareEnabled, publicNotebookEnabled) {
  return fetch("/api/admin/settings/sharing", {
    method: "PUT", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({public_share_enabled: publicShareEnabled,
                          public_notebook_enabled: publicNotebookEnabled}),
  });
}

// 書籤標記切換。專用端點(不是一般的 saveNote):後端不寫歷史版本、不動 updated。
export function setNoteMarked(id, marked) {
  return fetch(`/api/notes/${encodeURIComponent(id)}/mark`, {
    method: "PUT", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({marked}),
  });
}

export async function getTags() {
  const r = await fetch("/api/tags");
  if (!r.ok) return {tags: [], groups: []};
  return r.json();  // {tags, groups}
}

export function renameTag(name, newName) {
  return fetch(`/api/tags/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name: newName}),
  });
}

export function deleteTag(name) {
  return fetch(`/api/tags/${encodeURIComponent(name)}`, {method: "DELETE"});
}

// 標籤相似度重複偵測(字面層):純字串比對,不打 AI,所以按下去就有答案。
export async function getTagDuplicates() {
  const r = await fetch("/api/tag-duplicates");
  if (!r.ok) return [];
  return (await r.json()).groups;
}

// 把 absorb 裡的標籤全部併進 keep。回傳原始 Response 讓呼叫端判斷 ok 與錯誤訊息。
export function mergeTags(keep, absorb) {
  return fetch("/api/tags/merge", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({keep, absorb}),
  });
}

// 標籤群組:建立=加入=把所選標籤的 group 設為某字串;group 空字串 = 移出群組
export function setTagGroup(group, tags) {
  return fetch("/api/tag-groups", {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({group, tags}),
  });
}

// 群組改名。改成既有群組名 = 合併兩組(後端刻意允許,見 app/tags.py:rename_group)
export function renameGroup(name, newName) {
  return fetch(`/api/tag-groups/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name: newName}),
  });
}

export function dissolveGroup(name) {
  return fetch(`/api/tag-groups/${encodeURIComponent(name)}`, {method: "DELETE"});
}

export function dissolveAllGroups() {
  return fetch("/api/tag-groups", {method: "DELETE"});
}

export async function getTemplates() {
  const r = await fetch("/api/templates");
  if (!r.ok) return [];
  return (await r.json()).templates;
}

// id 為空 → POST 新建;有 id → PUT 更新。回傳原始 Response 讓呼叫端判斷 ok 與錯誤訊息。
export function saveTemplate(id, payload) {
  return fetch(id ? `/api/templates/${id}` : "/api/templates", {
    method: id ? "PUT" : "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
}

export function deleteTemplate(id) {
  return fetch(`/api/templates/${id}`, {method: "DELETE"});
}

// 切換內建樣板的啟用狀態(停用的樣板不出現在新建名詞的樣板下拉)。回傳原始 Response。
export function setTemplateEnabled(id, enabled) {
  return fetch(`/api/templates/${id}/enabled`, {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({enabled}),
  });
}

// 把內建/外掛樣板恢復成出廠定義(自建樣板沒有出廠定義,後端回 400)。回傳原始 Response。
// 孤兒樣板的批次轉換(健康度檢查的 missing_template):fromId 留空 = 全部孤兒。
export function retargetTemplate(fromId, toId) {
  return fetch("/api/templates/retarget", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({from_id: fromId, to_id: toId}),
  });
}

export function resetTemplate(id) {
  return fetch(`/api/templates/${id}/reset`, {method: "POST"});
}

export function getNote(id) {
  return fetch(`/api/notes/${id}`);
}

// id 為空 → POST 新建;有 id → PUT 更新。回傳原始 Response 讓呼叫端判斷 ok 與錯誤訊息。
export function saveNote(id, payload) {
  return fetch(id ? `/api/notes/${id}` : "/api/notes", {
    method: id ? "PUT" : "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
}

export function deleteNote(id) {
  return fetch(`/api/notes/${id}`, {method: "DELETE"});
}

/* ── 名詞連結 [[名詞]] ─────────────────────────────────────────────── */

// 所有名詞的 {id, name}:前端拿來建 normKey → 名詞的對照表,好在渲染連結的當下
// 就知道目標存不存在(逐個連結打 API 問是不可行的)。
export async function getNames() {
  const r = await fetch("/api/names");
  return r.ok ? (await r.json()).names : [];
}

// 一筆名詞的連結兩端:{outgoing:[{name,id|null}], backlinks:[{id,name}]}
export async function getNoteLinks(id) {
  const r = await fetch(`/api/notes/${encodeURIComponent(id)}/links`);
  return r.ok ? r.json() : {outgoing: [], backlinks: []};
}

/* ── 重複偵測與合併 ───────────────────────────────────────────────── */

// 編輯器即時提示:拿還沒存檔的內容問「你可能已經記過這個」。走 POST 是因為
// fields 是巢狀資料(見 app/models.py:SimilarIn)。
export async function findSimilar(payload) {
  const r = await fetch("/api/similar", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  return r.ok ? (await r.json()).similar : [];
}

export async function getDuplicates() {
  const r = await fetch("/api/duplicates");
  return r.ok ? (await r.json()).groups : [];
}

// 內容健康度檢查:破圖、孤兒檔、斷連結、待整理的內容(設定 → 整理與清理)。
// 端點叫 content-health 不叫 health——後者在慣例上是「伺服器活著嗎」的探針。
export async function getContentHealth() {
  const r = await fetch("/api/content-health");
  return r.ok ? await r.json() : null;
}

// 清理:先把要刪的東西打包成 ZIP 存證,再刪。
// ⚠ 只送 kinds(要清哪幾類),**絕不送檔案路徑**——要刪什麼由後端自己重掃決定
// (見 app/health.py 檔頭第 2 條)。回 {name, removed_files, removed_refs, bytes,
// notes_touched};name 是存證 ZIP 的檔名,空字串代表沒有東西可清。
export async function cleanupContentHealth(kinds) {
  const r = await fetch("/api/content-health/cleanup", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({kinds}),
  });
  return r.ok ? await r.json() : null;
}

// 下載走瀏覽器的一般導覽(不是 fetch),理由同 backupDownloadUrl。
export function healthCleanupDownloadUrl(name) {
  return `/api/content-health/cleanup/${encodeURIComponent(name)}/download`;
}

// 把 sourceIds 幾筆併進 targetId(來源會進回收桶)。回傳原始 Response 讓呼叫端判斷錯誤。
export function mergeNotes(targetId, sourceIds) {
  return fetch("/api/notes/merge", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({target_id: targetId, source_ids: sourceIds}),
  });
}

// 批次刪除名詞:group 空 = 刪全部;帶 group = 只刪該群組的名詞。回傳原始 Response;body {deleted}。
export function purgeNotes(group) {
  const u = group ? `/api/notes?group=${encodeURIComponent(group)}` : "/api/notes";
  return fetch(u, {method: "DELETE"});
}

// 只刪掉註冊時種進來的範例名詞(置頂行的刪除鈕)。⚠ 與 purgeNotes 是兩支不同的
// 端點,不可互相取代:那支會刪光使用者自己建立的名詞。回傳原始 Response;body {deleted}。
export function purgeDemo() {
  return fetch("/api/demo", {method: "DELETE"});
}

// 回收桶:列出(含保留天數)、還原、永久刪除單筆、清空。刪除的名詞先進這裡,
// 保留天數由後端決定(app/config.py:TRASH_RETENTION_DAYS),前端不寫死。
export async function getTrash() {
  const r = await fetch("/api/trash");
  return r.ok ? await r.json() : {items: [], retention_days: 0};
}

export function restoreTrashNote(id) {
  return fetch(`/api/trash/${id}/restore`, {method: "POST"});
}

export function purgeTrashNote(id) {
  return fetch(`/api/trash/${id}`, {method: "DELETE"});
}

export function emptyTrash() {
  return fetch("/api/trash", {method: "DELETE"});
}

export function restoreNote(id, index, baseUpdated) {
  return fetch(`/api/notes/${id}/restore`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({index, base_updated: baseUpdated ?? null}),
  });
}

export function uploadImage(noteId, formData) {
  return fetch(`/api/notes/${noteId}/images`, {method: "POST", body: formData});
}

export function uploadAttachment(noteId, formData) {
  return fetch(`/api/notes/${noteId}/attachments`, {method: "POST", body: formData});
}

// 取消新建時清掉草稿期間上傳的圖片/附件
export function deleteAssets(noteId) {
  return fetch(`/api/notes/${noteId}/assets`, {method: "DELETE"});
}

// 「批次壓縮既有圖片」用:列出所有名詞的所有附件(不受 /api/search 的 500 筆上限)
export async function listAssets() {
  const r = await fetch("/api/assets");
  return r.ok ? (await r.json()).items : [];
}

// 置換一個既有附件的實體檔並改指路徑。刻意不動 updated、不寫歷史版本
// (見 app/service.py:replace_note_asset)
export function replaceAsset(noteId, filename, formData) {
  return fetch(`/api/notes/${noteId}/assets/${encodeURIComponent(filename)}`,
    {method: "PUT", body: formData});
}

// 外掛模組:清單(含安裝狀態與設定)、詳細頁、安裝/解除、停用/啟用、設定更新。
// 名稱/描述的真相在封裝 manifest(後端依 lang 挑語言),不再查前端 i18n 字典;
// 帶 LANG 是既有的「api.js 依賴 i18n.js」方向(前例:aiLang)。
export async function getPlugins() {
  const r = await fetch(`/api/plugins?lang=${encodeURIComponent(LANG)}`);
  const d = await r.json();
  return d;  // {plugins, scan_errors?(admin 才有)}——呼叫端自取
}

export async function getPluginDetail(id) {
  const r = await fetch(`/api/plugins/${encodeURIComponent(id)}?lang=${encodeURIComponent(LANG)}`);
  return r.ok ? r.json() : null;
}

export function installPlugin(id) {
  return fetch(`/api/plugins/${encodeURIComponent(id)}/install`, {method: "POST"});
}

export function uninstallPlugin(id) {
  return fetch(`/api/plugins/${encodeURIComponent(id)}`, {method: "DELETE"});
}

export function setPluginEnabled(id, enabled) {
  return fetch(`/api/plugins/${encodeURIComponent(id)}/enabled`, {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({enabled}),
  });
}

export function savePluginConfig(id, config) {
  return fetch(`/api/plugins/${encodeURIComponent(id)}/config`, {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({config}),
  });
}

// 站台封裝管理(admin):上傳 zip、移除站台封裝
export function uploadPluginPackage(file) {
  const fd = new FormData();
  fd.append("file", file);
  return fetch("/api/admin/plugins/upload", {method: "POST", body: fd});
}

export function deleteSitePlugin(id) {
  return fetch(`/api/admin/plugins/${encodeURIComponent(id)}`, {method: "DELETE"});
}

export async function getAISettings() {
  const r = await fetch("/api/ai/settings");
  return r.json();
}

// 查詢服務端可用的模型清單。base_url/api_style 帶當下輸入框的值(可能還沒存檔)
// ——這支同時兼任「測試連線」,一定要能在存檔前先試。
// 回傳原始 Response 讓呼叫端判斷 ok 與錯誤訊息。
export function listAIModels(baseUrl, apiStyle) {
  return fetch("/api/ai/models?base_url=" + encodeURIComponent(baseUrl || "")
    + "&api_style=" + encodeURIComponent(apiStyle || ""));
}

export function saveAISettings(payload) {
  return fetch("/api/ai/settings", {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
}

// 預熱:請後端叫 Ollama 先把模型載入記憶體(使用者按「新增」時觸發),
// 縮短之後實際生成的等待。fire-and-forget——呼叫端不必等待,也不必處理錯誤。
export function warmupAI() {
  return fetch("/api/ai/warmup", {method: "POST"});
}

// 依樣板呼叫本機 Ollama 產生建議內容。回傳原始 Response 讓呼叫端判斷 ok 與錯誤訊息。
// lang 帶 aiLang()(預設跟隨介面語言,可在 偏好設定 → AI 生成語言 覆寫)送出,
// 後端據此決定生成內容要用哪種語言回覆。
export function generateAI(input, template) {
  return fetch("/api/ai/generate", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({input, template, lang: aiLang()}),
  });
}

// 文章>關鍵字:提供文章全文與文中一個名詞,請 AI 生成一筆預設樣板的名詞內容。
// 回傳 {name, description, fields, tags, template};呼叫端逐名詞呼叫以顯示進度。
export function generateArticleNote(keyword, article) {
  return fetch("/api/ai/article-note", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({keyword, article, lang: aiLang()}),
  });
}

// 依整份筆記已填內容請 AI 重寫單一欄位(target=name/description/欄位 key),回傳 {value}。
export function generateField(payload) {
  return fetch("/api/ai/field", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({...payload, lang: aiLang()}),
  });
}

// 一次補齊多個空白欄位(targets=["description", 欄位 key…]),回傳 {values:{目標:值}}。
// 空欄位不會出現在 values 裡(後端過濾掉空字串)。刻意一次呼叫補完所有欄位,
// 不是每欄各打一次 generateField——本機模型一輪好幾秒。
export function fillFields(payload) {
  return fetch("/api/ai/fill", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({...payload, lang: aiLang()}),
  });
}

// AI 自動分組建議(單一批次):送一批標籤 + 已知群組名,回傳 {groups:{標籤:群組名}}。
// 前端分批呼叫以顯示進度;回傳原始 Response 讓呼叫端判斷 ok 與錯誤訊息。
export function groupTags(tags, groups) {
  return fetch("/api/ai/group-tags", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({tags, groups, lang: aiLang()}),
  });
}

// 標籤相似度重複偵測的**語意層**:整份標籤清單一次送出,回傳 {groups:[[名稱,…],…]}。
// 刻意不分批——分批會讓落在不同批次的兩個寫法永遠配不到一起。
// 字面層(Mes/MES、全形半形、標點)是另一支 getTagDuplicates(),不需要 AI。
export function aiTagDuplicates(tags) {
  return fetch("/api/ai/tag-duplicates", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({tags, lang: aiLang()}),
  });
}

// 依已填寫的內容(name/description/fields/tags/template)請 AI 建議標籤(最多三個)。
export function generateTags(payload) {
  return fetch("/api/ai/tags", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({...payload, lang: aiLang()}),
  });
}

export function importNotes(formData) {
  return fetch("/api/import", {method: "POST", body: formData});
}

// 選擇性匯出:tags(OR 聯集)或 group 擇一帶入,都不帶 = 全部匯出
export function exportUrl(fmt, {tags = [], group = ""} = {}) {
  let u = `/api/export?format=${fmt}`;
  if (tags.length) u += `&tags=${encodeURIComponent(tags.join(","))}`;
  if (group) u += `&group=${encodeURIComponent(group)}`;
  return u;
}

// 用 fetch+blob 觸發下載,不動 window.location.href——避免跟 SPA 自己的
// history.pushState/back()(見 nav.js)搶跑導致下載被瀏覽器悄悄中斷。
// 檔名以伺服器的 Content-Disposition 為準(RFC 5987 的 filename* 優先,含中文群組名)。
async function download(url, fallbackName) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`匯出失敗(HTTP ${r.status})`);
  const blob = await r.blob();
  const cd = r.headers.get("Content-Disposition") || "";
  const m = /filename\*=UTF-8''([^;]+)/i.exec(cd) || /filename="([^"]+)"/i.exec(cd);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = m ? decodeURIComponent(m[1]) : fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}

export function downloadExport(fmt, opts) {
  return download(exportUrl(fmt, opts), `export.${fmt}`);
}

/* ── 欄位樣板單獨匯出/匯入(不含任何名詞)────────────────────────── */

export function downloadTemplatesExport() {
  return download("/api/export/templates", "jargon-vault-templates.json");
}

export function importTemplates(formData) {
  return fetch("/api/import/templates", {method: "POST", body: formData});
}

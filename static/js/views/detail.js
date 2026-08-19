// 詳細內容彈窗 view:完整渲染、版本記錄回復、畫重點工具列、名詞連結(相關名詞)。
// 畫重點直接改彈窗內的 DOM,再用 htmlToMd 轉回儲存格式即時存檔。
import * as actions from "../actions.js?v=20260820a";
import * as api from "../api.js?v=20260820a";
import {emit} from "../bus.js?v=20260820a";
import {fieldDefsFor, templateName} from "../fields.js?v=20260820a";
import {t} from "../i18n.js?v=20260820a";
import {popModalState, pushModalState} from "../nav.js?v=20260820a";
import {state} from "../store.js?v=20260820a";
import {$, esc, fmtDate, isImageFile, isNarrowScreen, newId, tagClass} from "../utils.js?v=20260820a";
import {htmlToMd, mdField, mdFull} from "../markdown.js?v=20260820a";

// 目前開著的那筆名詞本體。刻意存整個物件而不是只存 id 再回頭 state.results.find()——
// 沿著 [[連結]] 跳過去的名詞不一定在目前的搜尋結果裡,靠 results 找會找不到,
// 畫重點與書籤就會靜默失效(按了沒反應,也不報錯)。
let currentNote = null;
let hlArmedColor = null;  // 目前上膛的畫重點顏色(null = 未啟用)

// 畫重點的「手機條件」。與 positionHlToolbar() 共用同一把尺(utils.js:isNarrowScreen,
// 含橫放矮螢幕)——兩處各寫一份的話,將來改斷點會改掉一處、另一處默默不同步
// (工具列已經橫排了,色票卻還在)。
const isNarrow = () => isNarrowScreen();

// 畫重點存檔的防抖窗。連續畫幾筆只送一次 PUT——每筆各存一次會寫進一筆歷史版本,
// 而 HISTORY_LIMIT 只有 3,連畫三筆就把使用者真正的編輯歷史沖光了。
const HL_SAVE_DELAY = 1500;
let saveTimer = null;
// ⚠ payload 在改完 DOM 的**當下就同步算好**存進這裡,計時器只負責送。改成
// 「到期時才去 querySelector 讀 DOM」的話,彈窗這時可能已經關了、#detailModalBody
// 已被重建 → 讀到 null → 早退 → 使用者剛畫的那幾筆重點靜默消失。
let pendingSave = null;  // {note, desc}

export function initDetail() {
  $("#detailModalClose").onclick = closeDetail;
  $("#detailModal").addEventListener("click", e => { if (e.target.id === "detailModal") closeDetail(); });
  window.addEventListener("resize", () => {
    // 在桌機寬度上膛某個顏色之後把視窗縮窄(或手機橫轉直):色票被 CSS 藏起來了,
    // 但 hlArmedColor 還留著,一次 mouseup 照樣畫得下去。跨進窄螢幕就解除上膛。
    if (isNarrow() && hlArmedColor) { hlArmedColor = null; renderHlToolbar(); }
    if ($("#hlToolbar").classList.contains("show")) positionHlToolbar();
  });
  document.querySelectorAll(".hl-swatch").forEach(b => {
    b.onclick = () => {
      hlArmedColor = hlArmedColor === b.dataset.color ? null : b.dataset.color;
      renderHlToolbar();
    };
  });
  document.addEventListener("mouseup", onMouseUpHighlight);
  // 防抖存檔的保險網:切走分頁/切到別的 app 時把還沒送出去的那批重點補送。
  // 用 visibilitychange 而不是 beforeunload——後者在行動裝置上不保證會觸發,
  // 而「切走 app」正是手機最常見的離開方式。
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushDetailSave();
  });
  $("#detailModalBody").addEventListener("click", onCopyCodeClick);
  $("#detailModalBody").addEventListener("click", onWikilinkClick);

  // 行寬切換:偏好記在 localStorage(比照瀏覽模式/隱藏說明),純靠容器 class 控制。
  // 預設窄版(42 字)——未設定過時 getItem 回 null,!== "0" 即為 true。
  state.detailNarrow = localStorage.getItem("gv-detailnarrow") !== "0";
  applyDetailWidth();
  $("#hlWidthBtn").onclick = () => {
    // 手機不提供行寬切換:彈窗滿版,.wide 無效。CSS 藏(main.css 手機版總成的
    // #hlWidthBtn)+ 這裡的行為擋,兩層都要——藏起來的節點還在 DOM 裡。
    if (isNarrow()) return;
    state.detailNarrow = !state.detailNarrow;
    localStorage.setItem("gv-detailnarrow", state.detailNarrow ? "1" : "0");
    applyDetailWidth();
  };

  $("#hlShareBtn").onclick = () => toggleShareBox();

  $("#hlMarkBtn").onclick = async () => {
    const n = currentNote;
    if (!n) return;
    // 先算好目標值再指派(不是再翻一次面):從卡片開啟時 currentNote 跟 state.results
    // 裡那筆是同一個物件,actions 已經改過了,再翻一次就翻回去了。
    const next = !n.marked;
    if (await actions.setNoteMarked(n.id, next)) {
      n.marked = next;  // 沿著連結跳過來的名詞不在 results 裡,actions 改不到它
      renderMarkBtn(next);
    }
  };
}

// 詳細頁行寬:窄版(預設)外框 720px(約 42 全形字)、寬版 920px(約 62 字)。
// class 掛在 #detailModal 上而不是內容區——現在是「外框」跟著行寬變,不是只縮內欄
// (見 main.css 的 #detailModal.wide .modal-box)。
function applyDetailWidth() {
  $("#detailModal").classList.toggle("wide", !state.detailNarrow);
  $("#hlWidthBtn").classList.toggle("on", state.detailNarrow);
  // 外框寬度變了,貼在右緣外側的工具列要跟著移
  if ($("#hlToolbar").classList.contains("show")) positionHlToolbar();
}

function renderMarkBtn(marked) {
  $("#hlMarkBtn").classList.toggle("on", !!marked);
}

// 程式碼區塊的複製按鈕(見 markdown.js:richCodeBlockHtml)。掛在 #detailModalBody
// 上做事件委派,因為 openDetail() 每次都整段重建 innerHTML,按鈕是動態產生的。
async function onCopyCodeClick(e) {
  const btn = e.target.closest("[data-copy-code]");
  if (!btn) return;
  e.stopPropagation();
  const code = btn.closest(".cb-wrap")?.querySelector("code");
  if (!code) return;
  const orig = btn.textContent;
  try {
    await navigator.clipboard.writeText(code.textContent);
    btn.textContent = t("detail.copied");
  } catch {
    // clipboard API 在非 HTTPS/權限受限的瀏覽器環境可能會失敗;用按鈕文字
    // 本身回饋就好,不用 alert()——alert 是會整頁卡住的原生對話框,
    // 複製這種小動作失敗不需要這麼重的打斷。
    btn.textContent = t("detail.copyFailed");
  }
  btn.disabled = true;
  setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 1200);
}

export function openDetail(n) {
  const tags = n.tags.map(t => {
    const selected = state.tags.length === 1 && state.tags[0] === t;
    return `<span class="tag ${tagClass(t)} on ${selected ? "selected" : ""}" data-t="${esc(t)}">${esc(t)}</span>`;
  }).join("");
  // 欄位值走 mdField 而不是 esc:別名/同義詞/多義這幾欄本來就是拿來寫「跟哪個
  // 名詞有關」的,寫在裡面的 [[名詞]] 要跟說明欄一樣是可點的邊,不是死字串。
  const wl = "link";
  const fSm = (k, v) => v ? `<div class="field-sm"><span class="k">${esc(k)}</span><div class="v">${mdField(v, wl)}</div></div>` : "";
  // 樣板欄位泛用渲染:非空欄位兩兩一組(順序照樣板定義)
  const defs = fieldDefsFor(n).filter(d => n.fields[d.key]);
  let fieldRows = "";
  for (let i = 0; i < defs.length; i += 2) {
    fieldRows += `<div class="field-pair">${defs.slice(i, i + 2)
      .map(d => fSm(d.label, n.fields[d.key])).join("")}</div>`;
  }
  const meta = `<div class="detail-meta">${t("detail.meta",
    {created: fmtDate(n.created), updated: fmtDate(n.updated), tpl: esc(templateName(n.template))})}</div>`;
  // 圖片附件顯示成可點開原圖的縮圖(貼上的圖片現在存成附件,見 editor.js 的
  // paste;不這樣做的話,以前直接內嵌在說明欄裡看得到的圖,現在只會剩一行檔名)。
  // 其他檔案維持 📎 檔名連結。
  const attachHtml = (n.attachments && n.attachments.length) ? `<div class="detail-attach">
      <h3>${esc(t("detail.attachments"))}</h3>
      ${n.attachments.map(a => {
        const href = "/" + esc((a.path || "").replace(/^\/+/, ""));
        const isImg = isImageFile(a.name) || isImageFile(a.path);
        const head = isImg
          ? `<a class="attach-thumb" href="${href}" target="_blank" rel="noopener">
               <img src="${href}" alt="${esc(a.name)}" title="${esc(a.name)}"></a>`
          : `<a class="attach-item" href="${href}" target="_blank" rel="noopener">📎 ${esc(a.name)}</a>`;
        return `<div class="attach-block${isImg ? " is-image" : ""}">${head}
          ${a.description ? `<div class="attach-desc-view">${esc(a.description)}</div>` : ""}
        </div>`;
      }).join("")}
    </div>` : "";
  const tagsHtml = tags ? `<div class="detail-tags"><h3>${esc(t("detail.tags"))}</h3><div class="tags">${tags}</div></div>` : "";
  const tail = `<div class="detail-links" id="detailLinks"></div>
    <div class="detail-history" id="detailHistory"><h3>${esc(t("detail.history"))}</h3>
      ${meta}
      <div class="hist-body">${esc(t("detail.loading"))}</div>
    </div>`;
  $("#detailModalBody").innerHTML = `<h2>${esc(n.name)}</h2>
    ${fieldRows}
    <div class="detail-desc">${mdFull(n.description, wl)}</div>
    ${attachHtml}
    ${tagsHtml}
    ${tail}`;
  // 讀程式碼的名詞:第一個程式碼區塊捲到頂就釘在彈窗上緣(main.css 的 .cb-pin,
  // sticky + 區塊內部自己可捲)。只釘第一個——多個都 sticky 會互相疊。
  // 這個 class 不影響畫重點存檔:htmlToMd 用 classList.contains("cb-wrap") 判斷,
  // 轉回圍籬語法時 class 不參與,所以不會漏進 .md。
  const firstCb = document.querySelector("#detailModalBody .detail-desc .cb-wrap");
  if (firstCb) firstCb.classList.add("cb-pin");
  if (!isDetailOpen()) pushModalState(closeDetail);
  $("#detailModal").classList.add("show");
  currentNote = n;
  hlArmedColor = null;
  $("#hlToolbar").classList.add("show");
  renderHlToolbar();
  applyDetailWidth();
  renderMarkBtn(n.marked);
  renderShareBtn();
  positionHlToolbar();
  requestAnimationFrame(positionHlToolbar);
  loadHistory(n.id);
  loadLinks(n.id);
  document.querySelectorAll("#detailModalBody .detail-tags .tag[data-t]").forEach(t => t.onclick = () => {
    closeDetail(); actions.filterByTag(t.dataset.t);
  });
}

export function closeDetail() {
  if (!isDetailOpen()) return;
  // ⚠ 必須排在拆狀態**之前**:防抖窗還沒到期就關窗是最常見的路徑(畫完最後一筆
  // 就順手關掉),順序反了等於丟掉那一批重點。不用 await——payload 早就算好了,
  // flush 只是把它送出去。
  flushDetailSave();
  $("#detailModal").classList.remove("show");
  $("#hlToolbar").classList.remove("show");
  currentNote = null;
  hlArmedColor = null;
  renderHlToolbar();
  renderMarkBtn(false);
  popModalState();
}

export const isDetailOpen = () => $("#detailModal").classList.contains("show");

// 工具列釘在彈窗右側外緣,不隨內容捲動,位置依 modal-box 動態校正。
// 窄螢幕下 modal 滿版,兩側都沒有空間放浮動工具列,改成貼在畫面底部置中。
function positionHlToolbar() {
  // 明確指定是詳細頁的 box:原本寫 document.querySelector(".modal-box"),抓的是
  // 文件裡第一個 .modal-box(設定/未分組標籤也用這個 class),只因 DOM 順序碰巧
  // 是詳細頁排在最前面才對——不要靠這種運氣。
  const box = document.querySelector("#detailModal .modal-box");
  const toolbar = $("#hlToolbar");
  if (!box || !toolbar || !toolbar.classList.contains("show")) return;
  if (isNarrow()) {
    toolbar.style.top = ""; toolbar.style.right = "";
    toolbar.style.left = "50%"; toolbar.style.bottom = "18px";
    toolbar.style.transform = "translateX(-50%)";
    return;
  }
  toolbar.style.right = ""; toolbar.style.bottom = ""; toolbar.style.transform = "translateY(-50%)";
  const rect = box.getBoundingClientRect();
  toolbar.style.top = (rect.top + rect.height / 2) + "px";
  // 貼在彈窗右緣外側。右邊不能溢出視窗——overlay 自己的右緣就是可用區的右界,
  // 夾住就不會被切掉。桌機版彈窗最寬 920px、overlay 滿版,右邊本來就剩很多空間,
  // 這個夾制只是視窗被縮到接近 641px 時的保險。
  // 一律用 left 定位(不是 right):上面已把 style.right 清空,兩個一起設會把
  // fixed 元素左右撐開,寬度就不再由內容決定了。
  const band = $("#detailModal").getBoundingClientRect();
  toolbar.style.left = Math.min(band.right - 8 - toolbar.offsetWidth, rect.right + 12) + "px";
}

// 版本紀錄區塊現在也放了 meta(建立/編輯時間、樣板),所以不再整塊隱藏——
// 沒有歷史版本時只讓 hist-body 淨空,標題與 meta 照樣顯示。
async function loadHistory(id) {
  const body = document.querySelector("#detailHistory .hist-body");
  if (!body) return;
  let full;
  try {
    const r = await api.getNote(id);
    if (!r.ok) { body.innerHTML = ""; return; }
    full = await r.json();
  } catch (e) { body.innerHTML = ""; return; }
  const hist = full.history || [];
  if (!hist.length) { body.innerHTML = ""; return; }
  body.innerHTML = hist.map((h, i) => `<div class="hist-item">
      <span class="hist-date">${fmtDate(h.updated)}</span>
      <span class="hist-name">${esc(h.name)}</span>
      <button type="button" class="btn hist-restore" data-i="${i}">${esc(t("detail.restore"))}</button>
    </div>`).join("");
  body.querySelectorAll(".hist-restore").forEach(btn => btn.onclick = async () => {
    const i = +btn.dataset.i, h = hist[i];
    if (!confirm(t("detail.restoreConfirm", {name: h.name, date: fmtDate(h.updated)}))) return;
    const rr = await api.restoreNote(id, i, currentNote?.updated);
    if (!rr.ok) {
      alert(rr.status === 409 ? t("actions.saveConflict") : t("detail.restoreFailed"));
      return;
    }
    const restored = await rr.json();
    await actions.refreshAll();
    openDetail(restored);
  });
}

/* ── 公開分享連結 ── */

// 分享鈕只在站台有開公開分享時出現。站台沒開就完全不提這件事,
// 免得使用者按了才被 403 打回來。
function renderShareBtn() {
  $("#hlShareBtn").style.display = state.publicShareEnabled ? "" : "none";
  $("#hlShareBtn").classList.remove("on");
}

// 就地插在 #detailModalBody 底部,**不開新的 modal**:.modal-box 那組共用 class
// 的定位坑很多(見 CLAUDE.md),而且彈窗疊彈窗還要處理 nav.js 的 pushModalState 巢狀。
async function toggleShareBox() {
  const n = currentNote;
  if (!n) return;
  const existing = document.querySelector("#detailModalBody .share-box");
  if (existing) {
    existing.remove();
    $("#hlShareBtn").classList.remove("on");
    return;
  }
  $("#hlShareBtn").classList.add("on");
  const box = document.createElement("div");
  box.className = "share-box";
  box.innerHTML = `<h3>${esc(t("share.title"))}</h3><div class="share-body">${esc(t("detail.loading"))}</div>`;
  $("#detailModalBody").appendChild(box);
  let info = await api.getShareLink(n.id);
  // 一鍵到位:還沒有連結就直接建立,不再多按一次「建立」。
  // 失敗時(actions 已 alert)info 維持 GET 結果,照舊畫出「建立」按鈕當退路。
  if (info && info.enabled && !info.token) {
    const created = await actions.createShareLink(n.id);
    if (created?.token) info = created;
  }
  renderShareBox(info);
}

function renderShareBox(info) {
  const box = document.querySelector("#detailModalBody .share-box");
  if (!box) return;
  const body = box.querySelector(".share-body");
  if (!info) { body.textContent = t("share.createFailed"); return; }
  const ttlHint = `<p class="share-hint">${esc(t("share.ttlHint", {n: info.ttl_hours || 48}))}</p>`;
  if (!info.token) {
    body.innerHTML = `<p class="share-hint">${esc(t("share.hint"))}</p>
      ${ttlHint}
      <button type="button" class="btn primary" data-act="create">${esc(t("share.create"))}</button>`;
  } else {
    // 後端只回相對路徑(它不能假設 PUBLIC_BASE_URL 有設),絕對網址在這裡拼
    const url = location.origin + info.url;
    body.innerHTML = `<div class="share-url-row">
        <input type="text" class="share-url" readonly value="${esc(url)}">
        <button type="button" class="btn" data-act="copy">${esc(t("share.copy"))}</button>
      </div>
      <p class="share-hint">${esc(t("share.hint"))}</p>
      ${ttlHint}
      ${info.enabled ? "" : `<p class="share-hint warn">${esc(t("share.disabled"))}</p>`}
      <div class="share-acts">
        <button type="button" class="btn" data-act="regen">${esc(t("share.regenerate"))}</button>
        <button type="button" class="btn danger" data-act="revoke">${esc(t("share.revoke"))}</button>
      </div>`;
  }
  bindShareBox(body);
}

function bindShareBox(body) {
  const n = currentNote;
  const act = sel => body.querySelector(`[data-act="${sel}"]`);
  const create = act("create");
  if (create) create.onclick = async () => {
    create.disabled = true;
    renderShareBox(await actions.createShareLink(n.id));
  };
  const copy = act("copy");
  if (copy) copy.onclick = async () => {
    const input = body.querySelector(".share-url");
    try {
      await navigator.clipboard.writeText(input.value);
      copy.textContent = t("share.copied");
    } catch {
      // clipboard API 在非 HTTPS 環境會失敗——選起來讓使用者自己複製,
      // 用按鈕文字回饋就好(比照程式碼區塊的複製鈕,不用 alert)
      input.select();
      copy.textContent = t("share.copyFailed");
    }
    setTimeout(() => { copy.textContent = t("share.copy"); }, 1500);
  };
  const regen = act("regen");
  if (regen) regen.onclick = async () => {
    if (!confirm(t("share.regenConfirm"))) return;
    renderShareBox(await actions.createShareLink(n.id));
  };
  const revoke = act("revoke");
  if (revoke) revoke.onclick = async () => {
    if (!confirm(t("share.revokeConfirm"))) return;
    renderShareBox(await actions.revokeShareLink(n.id));
  };
}

/* ── 名詞連結(相關名詞)── */

// 兩個方向都列出來:這筆指向誰(outgoing,含還沒建立的),以及誰指向這筆
// (backlinks)。反向連結是行話網路真正的價值——記下「cSSFI123」時不必預先知道
// 之後會有誰引用它,那些引用會自己浮上來。
async function loadLinks(id) {
  const box = $("#detailLinks");
  if (!box) return;
  const d = await api.getNoteLinks(id);
  if (currentNote?.id !== id) return;  // await 期間使用者可能已經跳去別筆
  const chip = (label, noteId, missing) =>
    `<button type="button" class="wl-chip${missing ? " missing" : ""}"
       ${noteId ? `data-goto="${esc(noteId)}"` : `data-create="${esc(label)}"`}
       title="${esc(t(missing ? "wl.createTitle" : "wl.gotoTitle", {name: label}))}">${esc(label)}</button>`;
  const section = (titleKey, items) => items.length
    ? `<div class="wl-group"><h3>${esc(t(titleKey))}</h3><div class="wl-chips">${items.join("")}</div></div>` : "";

  const out = section("wl.outgoing",
    d.outgoing.map(l => chip(l.name, l.id, !l.id)));
  const back = section("wl.backlinks",
    d.backlinks.map(l => chip(l.name, l.id, false)));
  box.innerHTML = (out || back)
    ? `<h3 class="wl-head">${esc(t("wl.related"))}</h3>${out}${back}`
    : "";
  box.querySelectorAll("[data-goto]").forEach(b => b.onclick = () => openNoteById(b.dataset.goto));
  box.querySelectorAll("[data-create]").forEach(b => b.onclick = () => createLinked(b.dataset.create));
}

// 說明欄/欄位值裡的 [[連結]] 被點:存在就跳過去,不存在就去建立它。
function onWikilinkClick(e) {
  const a = e.target.closest("a.wikilink");
  if (!a) return;
  e.preventDefault();
  e.stopPropagation();
  const id = a.dataset.wlId;
  if (id) openNoteById(id);
  else createLinked(a.dataset.wl || a.textContent);
}

// 沿著連結跳到另一筆名詞。刻意重新打 API 拿完整內容(含 history),不從
// state.results 找——被連到的名詞很可能根本不在目前的搜尋結果裡。
export async function openNoteById(id) {
  const r = await api.getNote(id);
  if (!r.ok) { alert(t("wl.gotoFailed")); return; }
  openDetail(await r.json());
}

// 連到還沒建立的名詞:關掉彈窗、直接開一個帶好名稱的新建表單。
// 「先寫下 [[之後要補的名詞]]、之後再補建」是這個功能的主要用法,把補建的路
// 做成一鍵,不然使用者得自己記住剛剛那個名字再去搜尋列打一次。
function createLinked(name) {
  closeDetail();
  state.newName = name;
  state.creating = true;
  state.editing = null;
  state.draftId = newId();
  emit("results-changed");
}

/* ── 畫重點 ── */
function renderHlToolbar() {
  document.querySelectorAll(".hl-swatch").forEach(b => {
    b.classList.toggle("armed", b.dataset.color === hlArmedColor);
  });
}

function unwrapHighlightsInRange(container, range) {
  let changed = false;
  container.querySelectorAll('span[class^="hl-"]').forEach(sp => {
    if (!range.intersectsNode(sp)) return;
    const parent = sp.parentNode;
    while (sp.firstChild) parent.insertBefore(sp.firstChild, sp);
    parent.removeChild(sp);
    changed = true;
  });
  return changed;
}

// 把「現在的說明欄內容」同步收進 pendingSave,並重新起算防抖窗。
// 讀 DOM 的只有這一支,flushDetailSave() 完全不碰 DOM(見 pendingSave 的宣告)。
function scheduleDetailSave(container) {
  const n = currentNote;
  if (!n) return;
  pendingSave = {note: n, desc: htmlToMd(container)};
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushDetailSave, HL_SAVE_DELAY);
}

async function flushDetailSave() {
  const p = pendingSave;
  if (!p) return;
  clearTimeout(saveTimer);
  saveTimer = null;
  pendingSave = null;      // 先清掉:送出期間又畫一筆會排新的一批,不該被這次覆寫
  const n = p.note;
  const saved = await actions.saveNote(n.id, {
    name: n.name, description: p.desc, template: n.template, fields: n.fields,
    tags: n.tags, attachments: n.attachments, base_updated: n.updated,
  });
  if (saved) {
    // 彈窗拿著的這份要跟著更新:下一批重點是拿它去比對/存回的,不同步的話
    // 會用舊內容蓋掉剛存的(沿著連結跳過來的名詞不在 results 裡,沒人幫它更新)。
    n.description = p.desc;
    // updated 同樣要跟上,否則下一批重點會拿已經過期的 base_updated 去存,
    // 被自己剛剛那一次存檔擋成 409。
    if (saved.updated) n.updated = saved.updated;
  }
}

function onMouseUpHighlight() {
  // 手機版沒有畫重點:互動綁死在 mouseup,而行動瀏覽器的長按選字被原生 selection
  // UI 接管、不合成這個事件(拖曳選取把手調整範圍更不會)。色票在窄螢幕被 CSS
  // 藏起來,這行是行為層那一半——兩層都要,理由同 list.js 的 .card-toolbar。
  if (isNarrow()) return;
  if (!hlArmedColor) return;
  const container = document.querySelector("#detailModalBody .detail-desc");
  if (!container) return;
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return;
  const range = sel.getRangeAt(0);
  if (!container.contains(range.commonAncestorContainer)) return;
  let changed;
  if (hlArmedColor === "clear") {
    changed = unwrapHighlightsInRange(container, range);
  } else {
    const span = document.createElement("span");
    // 只掛 class,不設 inline style——顏色要隨深/淺主題換 alpha,交給 main.css 負責
    span.className = "hl-" + hlArmedColor;
    span.appendChild(range.extractContents());
    range.insertNode(span);
    changed = true;
  }
  sel.removeAllRanges();
  if (changed) scheduleDetailSave(container);
}

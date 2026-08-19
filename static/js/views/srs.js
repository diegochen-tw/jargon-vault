// SRS 複習彈窗(側欄日期區塊下方的「複習」)。
//
// README 承諾把行話 turning them into actual knowledge,但存與查之間缺一整個
// 回想層。這裡就是那一層的 UI:抽一輪卡 → 遮住說明只看名詞 → 翻牌 → 記得/忘了。
//
// 三個刻意的設計,改之前先讀:
//
// 1. **零負債**。抽完這一輪就結束,不顯示「今天到期 N 張」、不累積。
//    側欄那顆按鈕因此也不帶數字——池子多大只在彈窗頂端講一次。
//    (一輪幾張由使用者在 設定 → 偏好設定 決定,預設 10;可調的是「一輪多長」,
//    不可放寬的是那三個「不」。)
//    (唯一的例外是**暫離**時的「繼續複習 3/10」,見下方第 6 點:那不是欠款
//    數字,是「你剛剛離開的地方」,不講的話那一輪就等於憑空消失了。)
// 2. **範圍必須明講**。抽卡池吃側欄現在生效的篩選(標籤/群組/樣板/日期/書籤),
//    不寫出來的話「為什麼只有 3 張」永遠是個謎。⚠ 關鍵字**不在**篩選維度裡
//    (見 app/routers/srs.py 檔頭),所以 scope 那行只列真的生效的東西。
// 3. **正面不能洩題**。只顯示名詞與樣板名:標籤常常就是答案的一半,縮圖更是
//    直接把答案畫出來。兩者都等翻面才出現。
//
// 後來補上的三件事(都是「回想失敗當下」該有的出口):
//
// 4. **漸進式提示**(hintHTML)。想不起來時,全有全無的翻牌會逼人直接看答案,
//    而看了答案的那一次回想等於沒發生。所以第 3 點鬆綁成**使用者主動要**的
//    兩級:第一級只給關鍵字(那是「屬於哪一類」,不是答案本身),第二級把除了
//    說明欄以外的東西全攤開。**說明欄永遠留給翻面**——提示要是連它都給,
//    提示就只是翻牌的別名。沒有內容可給的等級自動跳過(見 hintLevels),
//    免得按了提示卻什麼都沒出現。
// 5. **暫離編輯**(editCurrent/resumeSrs)。翻面看到自己當初寫得太爛,是這個
//    app 最該把握的一刻——那是唯一一次你確知這筆內容不合格。但編輯器是列表
//    那邊的 modal,不可能塞進這個彈窗,所以改成「收起彈窗但**保留這一輪**」:
//    state.srsPaused 撐住卡序與進度,編輯器一關掉就回到原本那張卡。
//    ⚠ 恢復時**重新讀一次檔**而不是沿用手上那份:暫離就是為了改內容,
//    回來還看到舊的等於白改;讀不回來(在編輯器裡刪掉了)就跳過那張。
// 6. **翻面後可標書籤**。同樣是「回想失敗當下」的出口:這筆之後要再看,
//    但現在不想中斷這一輪。走靜默版的 setNoteMarked(不 emit)。
//
// 複習不動 updated、不寫歷史版本,所以這個 view **原則上不 emit 任何 bus 事件**——
// 主列表的內容與排序都沒變,重繪只會白白銷毀縮圖、清掉編輯器裡打到一半的內容。
// **唯一的例外是暫離編輯**:那是使用者明確要求離開複習去開編輯器,而編輯器由
// list.js 依 state 渲染,不 emit 它根本不會出現(見 editCurrent 原地的說明)。
import * as actions from "../actions.js?v=20260820a";
import {emit, on} from "../bus.js?v=20260820a";
import {fieldDefsFor, templateName} from "../fields.js?v=20260820a";
import {t} from "../i18n.js?v=20260820a";
import {mdField, mdFull} from "../markdown.js?v=20260820a";
import {popModalState, pushModalState} from "../nav.js?v=20260820a";
import {state} from "../store.js?v=20260820a";
import {$, esc, isImageFile, tagClass} from "../utils.js?v=20260820a";

export function initSrs() {
  $("#srsOpen").onclick = openSrs;
  $("#srsModalClose").onclick = closeSrs;
  $("#srsModal").addEventListener("click", e => { if (e.target.id === "srsModal") closeSrs(); });
  $("#srsScopeToggle").onclick = async () => {
    state.srsScopeAll = !state.srsScopeAll;
    await draw();
  };
  // 編輯器關掉了 → 回到暫離前那張卡。**由 list.js 發**而不是編輯器自己發:
  // 關閉路徑有五條(存檔、取消、Esc、點外面、刪除),盯著「編輯器 modal 從開變關」
  // 這一件事才全部涵蓋得到,靠每條路徑各補一行 emit 遲早漏一條。
  on("srs-resume", resumeSrs);
  // 翻面**不靠自訂快捷鍵**:正面那張卡本身就是 <button> 且會被 focus,所以
  // 空白鍵/Enter 走瀏覽器原生的「按下聚焦按鈕」行為——鍵盤使用者與讀螢幕軟體
  // 都拿得到,不需要我們攔 keydown(攔了反而要處理 focus 在別處時的各種例外)。
  // 提示鈕同理:要了提示之後焦點就留在它身上,連按空白 = 逐級要提示,
  // 提示用完才把焦點交還給卡片(見 render 的 focusHintBtn)。
  // 這裡只留評分的數字鍵;Esc 交給 app.js 那條關閉鏈。
  // ⚠ 評分鈕刻意**不 focus**:那會讓空白鍵變成「隨手評一個記得」。
  document.addEventListener("keydown", e => {
    if (!isSrsOpen() || !state.srsRevealed) return;
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.isContentEditable) return;
    if (e.key === "1") { e.preventDefault(); grade(false); }
    if (e.key === "2") { e.preventDefault(); grade(true); }
  });
}

export const isSrsOpen = () => $("#srsModal").classList.contains("show");

// 開複習彈窗時抽屜是不是開著的(手機):開窗被迫先收抽屜(z-index 高過 modal,
// 不收會蓋住彈窗),但複習入口就住在抽屜裡——關窗時要開回來,使用者才算「回到
// 原處」。⚠ 只在看到開著時設 true、由 closeSrs 統一清:暫離編輯(editCurrent →
// resumeSrs)期間抽屜是關的,resumeSrs 若無條件覆寫會把這個記憶洗掉。
let drawerWasOpen = false;

async function openSrs() {
  // 手機版的篩選抽屜 z-index 高過 modal,不收起來會蓋住彈窗(抄未分組彈窗)。
  if ($("#dirSidebar").classList.contains("show")) drawerWasOpen = true;
  $("#dirSidebar").classList.remove("show");
  $("#dirBackdrop").classList.remove("show");
  // 有暫離中的一輪 → 接著複習,不重抽。重抽等於把使用者剛才修好內容的那一輪
  // 丟掉,而他按下編輯的動機正是「這一輪的這一張要修」。
  if (state.srsPaused) { await resumeSrs(); return; }
  pushModalState(closeSrs);
  $("#srsModal").classList.add("show");
  state.srsScopeAll = false;  // 每次開窗都回到「跟著側欄篩選」,不記憶上次的切換
  await draw();
}

export function closeSrs() {
  if (!isSrsOpen()) return;
  $("#srsModal").classList.remove("show");
  state.srsCards = [];
  state.srsIndex = 0;
  state.srsRevealed = false;
  state.srsHint = 0;
  // 主動關窗 = 放棄這一輪(零負債:沒複習完不會變成明天的債),
  // 所以暫離狀態也一併清掉,側欄按鈕要跟著變回「複習」。
  state.srsPaused = false;
  renderOpenBtn();
  popModalState();
  // 手機:把開窗時被迫收起的抽屜開回來(見 drawerWasOpen 的註解)。
  // 寬螢幕不開——那裡側欄常駐,.show 沒有作用,加了只是留下髒 class。
  if (drawerWasOpen && window.innerWidth < 1024) {
    $("#dirSidebar").classList.add("show");
    $("#dirBackdrop").classList.add("show");
  }
  drawerWasOpen = false;
}

async function draw() {
  // 先進入載入狀態再畫,別讓彈窗開起來是一片空白或上一輪的殘影。
  // ⚠ 旗標要在這裡設:actions.drawSrs() 內部也會設,但那是在 await 之後才輪到,
  // 這一次 render() 早就用舊值畫完了,「抽卡中…」永遠不會出現。
  state.srsLoading = true;
  state.srsCards = [];
  state.srsIndex = 0;
  state.srsRevealed = false;
  state.srsHint = 0;
  render();
  await actions.drawSrs();
  render();
}

const currentCard = () => state.srsCards[state.srsIndex] || null;

function canEditCard(n) {
  return !!n;
}

// 防連點:srsIndex 要等 await 回來才前進,期間再按一次(滑鼠雙擊、或數字鍵
// 連按)會對**同一張卡**再送一次自評,然後 index 跳兩格——等於漏掉一張。
// 同 actions.loadMore() 的 loadingMore 旗標。純 UI 時序,不進 store。
let grading = false;

async function grade(remembered) {
  const card = currentCard();
  if (!card || grading) return;
  grading = true;
  let ok;
  try {
    ok = await actions.reviewSrs(card.id, remembered);
  } finally {
    grading = false;
  }
  if (!ok) return;  // 失敗時停在原地(actions 已經 alert 過),不要靜默跳下一張
  state.srsIndex += 1;
  state.srsRevealed = false;
  state.srsHint = 0;   // 提示等級跟著卡片走,下一張要從「什麼都沒給」開始
  render();
}

/* ── 暫離編輯 ─────────────────────────────────────────────────────── */

// 收起彈窗但**不清空這一輪**,並把編輯器開在這張卡上。
// ⚠ 抽到的卡多半**不在** state.results 裡(抽卡池與列表篩選是兩套範圍),
// 所以要連同整筆內容一起交給 list.js(state.editingNote),否則編輯器會靜默打不開。
function editCurrent() {
  const card = currentCard();
  if (!card || !canEditCard(card)) return;
  state.srsPaused = true;
  $("#srsModal").classList.remove("show");
  popModalState();
  state.editing = card.id;
  state.creating = false;
  state.editingNote = card;
  renderOpenBtn();
  // 這個 view 唯一會 emit 的地方(檔頭第 6 點):編輯器由 list.js 依 state 渲染,
  // 不通知它就什麼都不會發生。此時彈窗已經收起來,重繪清不掉任何東西。
  emit("results-changed");
}

// 編輯器關掉 → 回到原本那張卡。內容**重新讀一次檔**:暫離就是為了改內容,
// 回來還看到舊的等於白改。讀不回來(在編輯器裡把它刪了)就把那張從這一輪拿掉——
// 停在一張已經不存在的名詞上,自評只會換來一個 404 的 alert。
async function resumeSrs() {
  if (!state.srsPaused) return;
  state.srsPaused = false;
  renderOpenBtn();
  if ($("#dirSidebar").classList.contains("show")) drawerWasOpen = true;
  $("#dirSidebar").classList.remove("show");
  $("#dirBackdrop").classList.remove("show");
  pushModalState(closeSrs);
  $("#srsModal").classList.add("show");
  render();   // 先用手上這份畫出來,別讓彈窗開起來是一片空白
  const card = currentCard();
  if (!card) return;
  const fresh = await actions.reloadNote(card.id);
  if (fresh) {
    state.srsCards[state.srsIndex] = fresh;
  } else {
    state.srsCards.splice(state.srsIndex, 1);
    state.srsRevealed = false;
    state.srsHint = 0;
  }
  render();
}

// 側欄那顆按鈕。平常是「複習」;有暫離中的一輪時改成「繼續複習 3/10」——
// 那不是欠款數字(零負債禁的是「今天到期 N 張」那種會累積的債),
// 而是「你剛剛離開的地方」,不寫出來那一輪就等於憑空消失了。
function renderOpenBtn() {
  const btn = $("#srsOpen");
  if (!btn) return;
  const total = state.srsCards.length;
  const paused = state.srsPaused && total > 0;
  btn.textContent = paused
    ? `${t("srs.resume")} ${Math.min(state.srsIndex + 1, total)}/${total}`
    : t("srs.open");
  // .resuming 不是 .on:那顆按鈕是動作鈕不是開關,借 .on 的語意會讓人以為
  // 「複習模式已啟用」(見 main.css 的 .srs-open 註解)。
  btn.classList.toggle("resuming", paused);
}

/* ── 渲染 ───────────────────────────────────────────────────────── */

// 頂端那行:範圍 + 進度。範圍要講得夠具體才有用——「MES · 42 筆」比「已篩選」
// 有用得多,因為使用者要判斷的正是「這個範圍是不是我想複習的」。
function renderHead() {
  const parts = [];
  if (state.srsScopeAll) {
    parts.push(t("srs.scopeAll"));
  } else {
    if (state.tags.length) parts.push(state.tags.join(" + "));
    if (state.group) parts.push(state.group);
    if (state.template) parts.push(templateName(state.template));
    if (state.dateActive) parts.push(dateLabel());
    if (state.markedOnly) parts.push(t("srs.scopeMarked"));
    if (!parts.length) parts.push(t("srs.scopeAllNotes"));
  }
  // 載入中不報筆數:那是上一輪的數字,寫出來比不寫更糟(範圍才剛換掉)
  if (!state.srsLoading) parts.push(t("srs.scopeCount", {n: state.srsPool}));
  $("#srsScope").textContent = parts.join(" · ");

  const toggle = $("#srsScopeToggle");
  toggle.textContent = state.srsScopeAll ? t("srs.useFilters") : t("srs.useAll");
  // 沒有任何篩選時,「改用全庫」跟現況一樣,按了什麼都不會發生 → 藏起來
  const filtered = state.tags.length || state.group || state.template
    || state.dateActive || state.markedOnly;
  toggle.style.display = (filtered || state.srsScopeAll) ? "" : "none";

  const total = state.srsCards.length;
  $("#srsProgress").textContent = total
    ? `${Math.min(state.srsIndex + 1, total)} / ${total}` : "";
}

// 日期區間的顯示字串。⚠ sidebar.js 有自己的一份(那份是軌道下方的標籤),
// 這裡刻意不 import——views 之間不互相 import(list → editor/detail 是唯一例外)。
function dateLabel() {
  const DAY = 86400000;
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const untilMs = start.getTime() + DAY - state.dateOffset * DAY;
  const fmt = ms => {
    const d = new Date(ms);
    return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
  };
  const range = `${fmt(untilMs - state.dateDays * DAY)} – ${fmt(untilMs - DAY)}`;
  // 切到「建立時間」時要把欄位講出來:同一段日期在兩種時間軸下圈到的是完全不同
  // 的一批名詞,只寫日期會變成這個彈窗最忌諱的「不明講的隱式範圍」。預設的
  // 「上次編輯」不加註,免得常態情況多一段雜訊。
  return state.dateField === "created" ? `${t("date.fieldCreated")} ${range}` : range;
}

// 空白鍵/Enter 在**聚焦的按鈕**上會觸發 click,那會蓋掉我們的翻牌與評分:
// 開窗後焦點還在側欄的「複習」鈕上(按空白 = 再抽一輪),按過「記得」之後焦點
// 留在那顆鈕上(按空白 = 又評一次)。每次重繪就把彈窗相關的按鈕焦點放掉,
// 鍵盤事件才會乾淨地落到 document 上的處理器。
function dropStickyFocus() {
  const el = document.activeElement;
  if (!el || el === document.body) return;
  if (el.id === "srsOpen" || el.closest("#srsModal")) el.blur();
}

// 剛按過提示 → 這次重繪把焦點留在提示鈕上(連按空白 = 逐級要提示)。
// 純 UI 時序,不進 store。
let focusHintBtn = false;

function render() {
  renderHead();
  dropStickyFocus();
  const stage = $("#srsStage");

  if (state.srsLoading) {
    stage.innerHTML = `<div class="srs-empty">${esc(t("srs.loading"))}</div>`;
    return;
  }
  if (!state.srsCards.length) {
    stage.innerHTML = `<div class="srs-empty">${esc(t("srs.emptyPool"))}</div>`;
    return;
  }
  const card = currentCard();
  if (!card) { renderDone(stage); return; }

  stage.innerHTML = state.srsRevealed ? backHTML(card) : frontHTML(card);

  if (!state.srsRevealed) {
    const btn = stage.querySelector(".srs-front");
    btn.onclick = () => { state.srsRevealed = true; focusHintBtn = false; render(); };
    const hintBtn = stage.querySelector(".srs-hint-btn");
    if (hintBtn) hintBtn.onclick = () => {
      state.srsHint = Number(hintBtn.dataset.level) || 0;
      focusHintBtn = true;
      render();
    };
    // 讓空白鍵/Enter 走原生按鈕行為(見 initSrs 的說明)
    (focusHintBtn && hintBtn ? hintBtn : btn).focus();
    focusHintBtn = false;
    return;
  }
  focusHintBtn = false;
  const markBtn = stage.querySelector(".srs-mark");
  if (markBtn) markBtn.onclick = () => toggleMark(markBtn);
  const editBtn = stage.querySelector(".srs-edit");
  if (editBtn) editBtn.onclick = editCurrent;
  // ⚠ querySelectorAll:自評鈕上下各一組(見 gradeHTML),用 querySelector 只會綁到
  // 上面那組,下面那組按了完全沒反應——而那是使用者原本就在用的那一組。
  stage.querySelectorAll(".srs-grade-no").forEach(b => { b.onclick = () => grade(false); });
  stage.querySelectorAll(".srs-grade-yes").forEach(b => { b.onclick = () => grade(true); });
}

// 翻面後標書籤:「這筆之後要再看,但現在不想中斷這一輪」。
// 走靜默版(不 emit):複習中重繪主列表只會白白銷毀縮圖,而書籤本來就不是
// 名詞內容的一部分(不寫歷史版本、不動 updated,見 app/routers/notes.py)。
// ⚠ 也刻意**不整段重繪**這張卡:重繪會把捲動位置拉回頂端,而使用者多半
// 正看到說明欄的中段。只翻按鈕自己的 .on 就夠了。
async function toggleMark(btn) {
  const card = currentCard();
  if (!card) return;
  const next = !card.marked;
  if (!await actions.setNoteMarked(card.id, next, {silent: true})) return;
  card.marked = next;
  btn.classList.toggle("on", next);
}

// 正面:名詞 + 樣板名。刻意不放標籤與縮圖——那是答案的一部分,
// 要看得**主動按提示**(見下方 hintHTML)。
// 用 <button> 而不是可點的 <div>:整張卡就是「翻面」這一個動作,做成真按鈕才能
// 被 Tab 走到、被讀螢幕軟體念成按鈕,空白鍵/Enter 也直接是原生行為。
// ⚠ 提示區必須是這顆 <button> 的**兄弟節點**,不能包在裡面:巢狀按鈕不是合法
// 的 HTML,瀏覽器會自行把內層拆出去,拆完的位置與事件都不受我們控制。
function frontHTML(n) {
  return `<button type="button" class="srs-card srs-front">
      <div class="srs-term">${esc(n.name)}</div>
      <div class="srs-tpl">${esc(templateName(n.template))}</div>
      <div class="srs-hint">${esc(t("srs.revealHint"))}</div>
    </button>
    ${hintHTML(n)}`;
}

// 這張卡有內容的提示等級。**空的等級要跳過**——按了提示卻什麼都沒出現,
// 使用者只會以為壞掉。1 = 關鍵字,2 = 除說明欄外的其餘(樣板欄位 + 圖片)。
function hintLevels(n) {
  const lv = [];
  if ((n.tags || []).length) lv.push(1);
  const hasFields = fieldDefsFor(n).some(d => n.fields[d.key]);
  const hasImages = (n.attachments || []).some(a => isImageFile(a.name) || isImageFile(a.path));
  if (hasFields || hasImages) lv.push(2);
  return lv;
}

// 漸進式提示。**說明欄永遠不在提示裡**——它是答案本身,給了就等於翻牌
// (見檔頭第 4 點)。已給過的等級要繼續顯示,不是只顯示最後那一級。
function hintHTML(n) {
  const levels = hintLevels(n);
  const lv = state.srsHint;
  let body = "";
  if (lv >= 1 && levels.includes(1)) {
    const tags = (n.tags || []).map(x =>
      `<span class="tag ${tagClass(x)} on">${esc(x)}</span>`).join("");
    body += `<div class="tags srs-tags">${tags}</div>`;
  }
  if (lv >= 2 && levels.includes(2)) {
    const defs = fieldDefsFor(n).filter(d => n.fields[d.key]);
    // `[[名詞]]` 走 flat:彈窗裡點連結跳走會打斷這一輪(同背面的處理)
    const fields = defs.map(d =>
      `<div class="field-sm"><span class="k">${esc(d.label)}</span>
         <div class="v">${mdField(n.fields[d.key], "flat")}</div></div>`).join("");
    if (fields) body += `<div class="srs-fields">${fields}</div>`;
    const images = (n.attachments || []).filter(a => isImageFile(a.name) || isImageFile(a.path));
    if (images.length) body += `<div class="srs-gallery">${images.map(a => {
      const href = "/" + esc((a.path || "").replace(/^\/+/, ""));
      return `<img src="${href}" alt="${esc(a.name)}" title="${esc(a.name)}">`;
    }).join("")}</div>`;
  }
  // 下一個「還有東西可給」的等級。沒有了就不畫按鈕——留一顆按了沒反應的鈕
  // 比沒有更糟(使用者會反覆按它,以為自己漏看了什麼)。
  const next = levels.find(x => x > lv);
  const btn = next
    ? `<button type="button" class="btn srs-hint-btn" data-level="${next}">${
        esc(t(lv === 0 ? "srs.hint" : "srs.hintMore"))}</button>`
    : "";
  if (!btn && !body) return "";
  return `<div class="srs-hints">${body}${btn}</div>`;
}

// 兩檔自評鈕。背面**上下各放一組**:說明欄可以很長,只放在最底下等於「必須讀完
// 整篇才能評分」,而回想的答案在翻牌那一瞬間就已經知道了。
// ⚠ 一定要抽成函式而不是複製兩份 markup——兩份一定會漂移(class 名、文字、順序),
// 而這兩顆是整個複習流程唯一的出口。上面那組不畫分隔線(它本來就在最上面)。
function gradeHTML(top) {
  return `<div class="srs-grade${top ? " srs-grade-top" : ""}">
      <button type="button" class="btn srs-grade-no">${esc(t("srs.forgot"))}</button>
      <button type="button" class="btn primary srs-grade-yes">${esc(t("srs.remembered"))}</button>
    </div>`;
}

function backHTML(n) {
  const defs = fieldDefsFor(n).filter(d => n.fields[d.key]);
  // 翻面之後就是完整內容:`[[名詞]]` 走 flat(彈窗裡點連結跳走會打斷這一輪),
  // 樣板欄位、標籤、圖片附件全部攤開,不用再開詳細頁確認自己記對沒有。
  const fields = defs.map(d =>
    `<div class="field-sm"><span class="k">${esc(d.label)}</span>
       <div class="v">${mdField(n.fields[d.key], "flat")}</div></div>`).join("");
  const images = (n.attachments || []).filter(a => isImageFile(a.name) || isImageFile(a.path));
  const gallery = images.length ? `<div class="srs-gallery">${images.map(a => {
    const href = "/" + esc((a.path || "").replace(/^\/+/, ""));
    return `<img src="${href}" alt="${esc(a.name)}" title="${esc(a.name)}">`;
  }).join("")}</div>` : "";
  const tags = (n.tags || []).map(x =>
    `<span class="tag ${tagClass(x)} on">${esc(x)}</span>`).join("");
  // 動作列:書籤(這筆之後要再看)+ 編輯(這筆當初寫得太爛)。兩者都是
  // 「回想失敗當下」才有的資訊,擺在自評鈕**下方**並以一條虛線隔開——這一輪的重點
  // 動作是評分,這兩個是次要的補救動作,隔開才不會被誤讀成評分那一組的第三、四個選項。
  // 兩顆都是「icon 在前、文字在後」:書籤沿用詳細頁那顆鏤空書籤 SVG(icon 慣例
  // 的唯一 SVG 例外:開關型按鈕的狀態表達,Unicode 沒有鏤空書籤字形),編輯用
  // 既有的 ✎ 慣例字元(同 list.js 卡片工具列)。⚠ 掛了 inline SVG 的按鈕不能加
  // data-i18n(applyI18n 對它是寫 textContent,會把 SVG 整個清掉)——這裡是
  // srs.js 自己組字串呼叫 t(),不受這條限制。有了可見文字之後 title/aria-label
  // 就不重複掛了,免得螢幕報讀軟體念出跟畫面上不一樣的字。
  const editBtn = canEditCard(n)
    ? `<button type="button" class="btn srs-act srs-edit">✎<span>${esc(t("srs.edit"))}</span></button>` : "";
  return `${gradeHTML(true)}
    <div class="srs-card srs-back">
      <div class="srs-term">${esc(n.name)}</div>
      ${fields ? `<div class="srs-fields">${fields}</div>` : ""}
      <div class="srs-desc">${mdFull(n.description, "flat")}</div>
      ${gallery}
      ${tags ? `<div class="tags srs-tags">${tags}</div>` : ""}
      <div class="srs-box-hint">${esc(boxLabel(n))}</div>
    </div>
    ${gradeHTML(false)}
    <div class="srs-acts">
      <button type="button" class="srs-act srs-mark${n.marked ? " on" : ""}"><svg class="bm-ic"
        viewBox="0 0 14 18" aria-hidden="true"><path d="M2.4 1.4h9.2v15.2L7 12.4l-4.6 4.2z"/></svg><span>${
        esc(t("srs.mark"))}</span></button>
      ${editBtn}
    </div>`;
}

// 目前在第幾盒。null = 從沒複習過(見 app/srs.py:「盒」是 Leitner 的間隔階梯)。
function boxLabel(n) {
  return n.srs_box === null || n.srs_box === undefined
    ? t("srs.boxNew") : t("srs.box", {n: n.srs_box + 1});
}

function renderDone(stage) {
  stage.innerHTML = `<div class="srs-done">
      <div class="srs-done-title">${esc(t("srs.doneTitle"))}</div>
      <div class="srs-done-sub">${esc(t("srs.doneSub", {n: state.srsCards.length}))}</div>
      <div class="srs-done-acts">
        <button type="button" class="btn primary" id="srsAgain">${esc(t("srs.again"))}</button>
        <button type="button" class="btn" id="srsClose2">${esc(t("srs.close"))}</button>
      </div>
    </div>`;
  $("#srsAgain").onclick = draw;
  $("#srsClose2").onclick = closeSrs;
}

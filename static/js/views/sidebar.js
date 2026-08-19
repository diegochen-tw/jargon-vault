// 側欄 view:標籤分類(欄位樣板)、標籤群組樹(群組 → 標籤)、日期篩選。
// 點擊行為:改 state 篩選條件 → 自己重繪 → actions.search() 更新結果。
//   點群組 = 篩選整個群組(掛有群組內任一標籤的名詞,OR);進入時清掉關鍵字與標籤篩選
//   點標籤 = 預設單選(點新的取代舊的,再點一次取消);勾選「複選標籤」後改回
//           toggle 多選 AND 篩選,偏好記在 localStorage,跟主題切換同一套模式
import * as actions from "../actions.js?v=20260820a";
import {on} from "../bus.js?v=20260820a";
import {srsSize} from "../config.js?v=20260820a";
import {tplLabel} from "../fields.js?v=20260820a";
import {t} from "../i18n.js?v=20260820a";
import {popModalState, pushModalState} from "../nav.js?v=20260820a";
import {state} from "../store.js?v=20260820a";
import {$, esc, tagClass} from "../utils.js?v=20260820a";

export function initSidebar() {
  initDateField();
  $("#srsHelp").onclick = openSrsHelp;
  on("tags-changed", () => { renderGroupTree(); if (isUngroupedOpen()) renderUngroupedTags(); });
  $("#ungroupedModalClose").onclick = closeUngrouped;
  $("#ungroupedModal").addEventListener("click", e => { if (e.target.id === "ungroupedModal") closeUngrouped(); });
  on("templates-changed", renderTemplateList);
  on("filters-reset", () => { renderDateBar(); renderGroupTree(); renderTemplateList(); });
  // 核取方塊太突兀,改用跟隱藏說明同款的鏤空按鈕(.pill-toggle),靠 .on class
  // 呈現啟用狀態。
  state.multiSelect = localStorage.getItem("gv-multiselect") === "1";
  $("#multiSelectToggle").classList.toggle("on", state.multiSelect);
  $("#multiSelectToggle").onclick = () => {
    state.multiSelect = !state.multiSelect;
    $("#multiSelectToggle").classList.toggle("on", state.multiSelect);
    localStorage.setItem("gv-multiselect", state.multiSelect ? "1" : "0");
    // 關閉複選時,若目前選了一個以上的標籤,選取狀態已經不符合「單選」語意
    // (哪個才是「唯一」選取的?),乾脆清空重新開始,不猜使用者想留哪一個
    if (!state.multiSelect && state.tags.length > 1) {
      state.tags = [];
      renderGroupTree(); actions.search();
    }
  };
  renderDateBar();
}

// 標籤分類:欄位樣板當作粗分類篩選,獨立於標籤/群組維度(不互相清空,行為比照日期篩選)。
function renderTemplateList() {
  const list = $("#templateList"); if (!list) return;
  if (!state.allTemplates.length) {
    list.innerHTML = `<div class="empty">${t("sidebar.noTemplates")}</div>`;
    return;
  }
  const allOn = !state.template;
  let html = `<div class="dir-item ${allOn ? "on" : ""}" data-all-tpl="1"><span class="ico">🗂️</span><span>${t("sidebar.all")}</span></div>`;
  // 停用且沒有任何名詞的樣板不列出;仍掛著名詞的樣板即使停用也保留(可篩選)
  html += state.allTemplates
    .filter(tpl => tpl.enabled !== false || tpl.count > 0)
    .map(tpl => {
      const on_ = state.template === tpl.id;
      return `<div class="dir-item ${on_ ? "on" : ""}" data-tpl="${esc(tpl.id)}">
        <span class="ico">📄</span><span>${esc(tplLabel(tpl))}</span><span class="n">${tpl.count ?? ""}</span>
      </div>`;
    }).join("");
  list.innerHTML = html;

  list.querySelector("[data-all-tpl]").onclick = () => {
    state.template = "";
    renderTemplateList(); actions.search();
  };
  list.querySelectorAll("[data-tpl]").forEach(el => el.onclick = () => {
    const id = el.dataset.tpl;
    state.template = state.template === id ? "" : id;
    renderTemplateList(); actions.search();
  });
}

/* ── 日期橫條篩選 ──
   一條時間軌道(右端=今天),上面一個可拖曳的視窗 bar:
     bar 寬度 = 區間天數(中間的數字,純顯示)
     ◀ = 往過去步進一個區間、▶ = 往現在步進一個區間(offset 到 0 為止)
     拖曳 bar = 沿軌道平移區間;下方 datebar-label 列顯示日期範圍,
     右側 +/− 步進區間天數,啟用時再多一顆 ✕ 清除鈕
   軌道代表的總天數(span)依目前狀態自適應,拖曳期間凍結,放開後才重算。 */
const DAY = 86400000;

/* 日期篩選比哪一個時間:updated(上次編輯,預設)或 created(建立)。
   偏好存 localStorage,跟主題/複選標籤同一套「記在這台裝置」的模式。

   ⚠ 按鈕上顯示的是**目前生效的欄位**,不是「按下去會變成什麼」。兩種寫法都有人
   用,但這顆鈕旁邊就是它作用的那條軌道,而軌道圈出來的東西必須跟按鈕文字對得起來
   ——寫成「切換到建立時間」會讓人以為現在篩的就是建立時間。 */
function dateFieldLabel() {
  return t(state.dateField === "created" ? "date.fieldCreated" : "date.fieldUpdated");
}

function renderDateField() {
  const btn = $("#dateFieldToggle"); if (!btn) return;
  btn.textContent = dateFieldLabel();
  btn.title = t("date.fieldToggleTitle");
  btn.classList.toggle("on", state.dateField === "created");
}

function initDateField() {
  const btn = $("#dateFieldToggle"); if (!btn) return;
  state.dateField = localStorage.getItem("gv-datefield") === "created" ? "created" : "updated";
  renderDateField();
  btn.onclick = () => {
    state.dateField = state.dateField === "created" ? "updated" : "created";
    localStorage.setItem("gv-datefield", state.dateField);
    renderDateField();
    // 沒啟用日期篩選時切換不影響任何結果,不必白跑一趟查詢
    if (state.dateActive) actions.search();
  };
}

function dateWindow() {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const untilMs = start.getTime() + DAY - state.dateOffset * DAY;
  return {sinceMs: untilMs - state.dateDays * DAY, untilMs};
}

const fmtDay = ms => {
  const d = new Date(ms);
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
};

function activateDate() {
  if (!state.dateActive) { state.dateActive = true; return true; }
  return false;
}

// +/− 增減區間天數(1–365),順便啟用日期篩選並重新搜尋
function stepDays(delta) {
  state.dateDays = Math.max(1, Math.min(365, state.dateDays + delta));
  activateDate();
  renderDateBar();
  actions.search();
}

function renderDateBar() {
  const box = $("#dateBar"); if (!box) return;
  const days = state.dateDays, active = state.dateActive;
  // 軌道總天數:至少 14 天、至少能容納 3 個區間、至少涵蓋目前推到的位置
  const span = Math.max(14, days * 3, state.dateOffset + days);
  const widthPct = Math.min(100, days / span * 100);
  const rightPct = Math.min(100 - widthPct, state.dateOffset / span * 100);
  const w = dateWindow();
  const label = active ? `${fmtDay(w.sinceMs)} – ${fmtDay(w.untilMs - DAY)}` : t("date.noFilter");

  box.innerHTML = `
    <div class="datebar ${active ? "on" : ""}">
      <button type="button" class="datebar-arrow" data-dir="back" title="${esc(t("date.stepBack"))}">◀</button>
      <div class="datebar-track">
        <div class="datebar-win" style="width:${widthPct}%;right:${rightPct}%">
          <span class="datebar-num">${days}</span>
        </div>
      </div>
      <button type="button" class="datebar-arrow" data-dir="fwd" title="${esc(t("date.stepFwd"))}"
        ${active && state.dateOffset === 0 ? "disabled" : ""}>▶</button>
    </div>
    <div class="datebar-label">
      <span class="datebar-label-text">${esc(label)}</span>
      ${active ? `<button type="button" class="datebar-clear" title="${esc(t("date.clear"))}">✕</button>` : ""}
      <span class="date-steppers">
        <button type="button" class="date-step" data-step="-1" title="${esc(t("date.rangeLess"))}">−</button>
        <button type="button" class="date-step" data-step="1" title="${esc(t("date.rangeMore"))}">＋</button>
      </span>
    </div>`;

  const apply = () => { renderDateBar(); actions.search(); };

  // 箭頭步進:未啟用時第一下先啟用(顯示以今天收尾的區間),之後每下步進一個區間
  box.querySelectorAll(".datebar-arrow").forEach(btn => btn.onclick = () => {
    const first = activateDate();
    if (btn.dataset.dir === "back") {
      if (!first) state.dateOffset += days;
    } else if (!first) {
      state.dateOffset = Math.max(0, state.dateOffset - days);
    }
    apply();
  });

  // 清除:回到不限日期
  const clearBtn = box.querySelector(".datebar-clear");
  if (clearBtn) clearBtn.onclick = () => { state.dateActive = false; state.dateOffset = 0; apply(); };

  // +/− 區間天數(datebar-label 右側,每次重繪都要重新綁,DOM 是整段重建的)
  box.querySelectorAll(".date-step").forEach(btn => btn.onclick = () => stepDays(+btn.dataset.step));

  // 拖曳:pointer capture,水平位移換算成天數(span 於拖曳期間凍結),放開才觸發搜尋
  // (區間天數改由標題列的 +/− 步進,中間數字純顯示,整條 bar 都可抓著拖)
  const win = box.querySelector(".datebar-win");
  const track = box.querySelector(".datebar-track");
  let dragX = null, startOffset = 0, moved = false;
  win.addEventListener("pointerdown", e => {
    dragX = e.clientX; startOffset = state.dateOffset; moved = false;
    win.setPointerCapture(e.pointerId);
  });
  win.addEventListener("pointermove", e => {
    if (dragX === null) return;
    const rect = track.getBoundingClientRect();
    if (rect.width < 40) return;  // 版面尚未完成佈局(寬度趨近 0)時,換算會爆炸,直接忽略
    const deltaDays = Math.round((dragX - e.clientX) / rect.width * span);
    const next = Math.max(0, startOffset + deltaDays);
    if (next !== state.dateOffset) {
      state.dateOffset = next; moved = true;
      win.style.right = Math.min(100 - widthPct, next / span * 100) + "%";
    }
  });
  win.addEventListener("pointerup", () => {
    if (dragX === null) return;
    dragX = null;
    if (moved || !state.dateActive) { activateDate(); apply(); }
  });
}

function renderGroupTree() {
  const list = $("#groupTree"); if (!list) return;
  if (!state.allTags.length) {
    list.innerHTML = `<div class="empty">${t("sidebar.noTags")}</div>`;
    return;
  }
  const tagItem = (t, depth) => {
    const on_ = state.tags.includes(t.name);
    return `<div class="dir-item tagitem depth${depth} ${tagClass(t.name)} ${on_ ? "on" : ""}" data-t="${esc(t.name)}">
      <span class="tagdot"></span><span>${esc(t.name)}</span><span class="n">${t.count}</span>
    </div>`;
  };
  const byGroup = new Map();
  const ungrouped = [];
  for (const t of state.allTags) {
    if (t.group) {
      if (!byGroup.has(t.group)) byGroup.set(t.group, []);
      byGroup.get(t.group).push(t);
    } else ungrouped.push(t);
  }
  const groupCount = new Map(state.allGroups.map(g => [g.name, g.count]));

  const allOn = !state.group && !state.tags.length;
  let html = `<div class="dir-item ${allOn ? "on" : ""}" data-all="1"><span class="ico">🗂️</span><span>${t("sidebar.all")}</span></div>`;
  for (const g of [...byGroup.keys()].sort((a, b) => a.localeCompare(b))) {
    const on_ = state.group === g;
    html += `<div class="dir-item groupitem ${on_ ? "on" : ""}" data-g="${esc(g)}">
      <span class="ico">📁</span><span>${esc(g)}</span><span class="n">${groupCount.get(g) ?? ""}</span>
    </div>`;
    html += byGroup.get(g).map(t => tagItem(t, 1)).join("");
  }
  // 未分組標籤通常遠多於已分組的,全部攤在側欄只會讓使用者一路往下滑;側欄只留
  // 一個入口,清單本身改由彈窗把標籤攤成一片 chip(一列多個),見 openUngrouped()
  if (ungrouped.length) {
    const on_ = ungrouped.some(x => state.tags.includes(x.name));
    html += `<div class="dir-item ungrouped-entry ${on_ ? "on" : ""}" data-ungrouped="1">
      <span class="ico">📂</span><span>${t("sidebar.ungrouped")}</span><span class="n">${ungrouped.length}</span>
    </div>`;
  }
  list.innerHTML = html;

  const ungroupedEntry = list.querySelector("[data-ungrouped]");
  if (ungroupedEntry) ungroupedEntry.onclick = openUngrouped;

  list.querySelector("[data-all]").onclick = () => {
    state.group = ""; state.tags = [];
    renderGroupTree(); actions.search();
  };
  list.querySelectorAll(".groupitem").forEach(el => el.onclick = () => {
    const g = el.dataset.g;
    // 進入群組時清掉關鍵字與標籤篩選,讓群組瀏覽從乾淨狀態開始;再點一次取消
    const entering = state.group !== g;
    state.group = entering ? g : "";
    if (entering) {
      state.q = ""; $("#q").value = ""; state.tags = [];
    }
    renderGroupTree(); actions.search();
  });
  list.querySelectorAll(".tagitem").forEach(el => el.onclick = () => {
    toggleTag(el.dataset.t);
    renderGroupTree(); actions.search();
  });
}

// 側欄標籤與未分組彈窗共用同一套選取語意,不要各寫一份
function toggleTag(name) {
  if (state.multiSelect) {
    state.tags = state.tags.includes(name) ? state.tags.filter(x => x !== name) : [...state.tags, name];
  } else {
    // 單選:點新標籤直接取代舊選取;再點一次目前唯一選取的標籤 = 取消篩選
    state.tags = (state.tags.length === 1 && state.tags[0] === name) ? [] : [name];
  }
}

/* ── 未分組標籤彈窗 ──
   側欄那條清單一長就只能慢慢滑;這裡把未分組標籤攤成一片 chip,一列多個、
   一眼掃完。右上角的「標籤管理」直接疊開設定分頁(見 app.js),讓「看到一堆
   沒分組的標籤」到「動手分組」之間不用先關彈窗再翻選單。 */

/* ── 複習說明(側欄「複習」旁的 ? ) ──────────────────────────────
   節點動態建、關閉即移除,不在 index.html 佔一個常駐單例(同 settings.js 的
   外掛詳細頁)。放在 sidebar.js 而不是 srs.js:按鈕是側欄的元件,而 views
   之間唯一允許的橫向 import 是 list → editor, detail——為了一個說明彈窗
   去開第二條橫向依賴不划算,而這裡不需要 srs.js 的任何狀態。

   ⚠ 內容要跟 app/srs.py 的常數對得上(INTERVALS),改那邊記得改 i18n 的
   srs.helpBoxes。張數不寫死在字串裡:它現在可由使用者設定(設定 → 偏好設定),
   所以 srs.helpZeroT / srs.helpZero 用 {n} 佔位,由 config.js:srsSize() 帶入。 */
// 開說明彈窗時抽屜是不是開著的(手機):? 就住在抽屜的複習列裡,關窗要開回來
// ——同 srs.js 的 drawerWasOpen(那邊的註解有完整理由)。
let helpDrawerWasOpen = false;

function openSrsHelp() {
  helpDrawerWasOpen = $("#dirSidebar").classList.contains("show");
  $("#dirSidebar").classList.remove("show");   // 同 openUngrouped:手機抽屜會蓋住彈窗
  $("#dirBackdrop").classList.remove("show");
  const ov = document.createElement("div");
  ov.className = "modal-overlay show";
  ov.id = "srsHelpModal";
  const row = (title, body) => `<div class="srshelp-item">
    <b>${esc(title)}</b><div class="hint">${esc(body)}</div></div>`;
  ov.innerHTML = `<div class="modal-box srshelp-box">
    <button type="button" class="btn icon modal-close" data-close>✕</button>
    <h2>${esc(t("srs.helpHeading"))}</h2>
    ${row(t("srs.helpWhatT"), t("srs.helpWhat"))}
    ${row(t("srs.helpBoxesT"), t("srs.helpBoxes"))}
    ${row(t("srs.helpZeroT", {n: srsSize()}), t("srs.helpZero", {n: srsSize()}))}
    ${row(t("srs.helpScopeT"), t("srs.helpScope"))}
    <div class="tplmgr-editor-actions">
      <span class="spacer"></span>
      <button type="button" class="btn primary" data-close>${esc(t("srs.helpClose"))}</button>
    </div>
  </div>`;
  document.body.appendChild(ov);
  pushModalState(closeSrsHelp);
  ov.querySelectorAll("[data-close]").forEach(b => b.onclick = closeSrsHelp);
  ov.addEventListener("click", e => { if (e.target === ov) closeSrsHelp(); });
}

function closeSrsHelp() {
  const ov = $("#srsHelpModal");
  if (ov) { ov.remove(); popModalState(); }
  // 手機:把開窗時被迫收起的抽屜開回來(寬螢幕側欄常駐,.show 沒作用,不加)。
  if (helpDrawerWasOpen && window.innerWidth < 1024) {
    $("#dirSidebar").classList.add("show");
    $("#dirBackdrop").classList.add("show");
  }
  helpDrawerWasOpen = false;
}

export const isUngroupedOpen = () => $("#ungroupedModal").classList.contains("show");

function openUngrouped() {
  // 手機版的篩選抽屜 z-index 高過 modal,不收起來會蓋住彈窗(連帶蓋住從彈窗開的設定)。
  // 只動 class:抽屜本來就不佔 nav 的歷史紀錄(見 app.js 的 closeFilterDrawer)。
  $("#dirSidebar").classList.remove("show");
  $("#dirBackdrop").classList.remove("show");
  pushModalState(closeUngrouped);
  $("#ungroupedModal").classList.add("show");
  renderUngroupedTags();
}

export function closeUngrouped() {
  if (!isUngroupedOpen()) return;
  $("#ungroupedModal").classList.remove("show");
  popModalState();
}

function renderUngroupedTags() {
  const box = $("#ungroupedTagList");
  const ungrouped = state.allTags.filter(x => !x.group);
  if (!ungrouped.length) {
    box.innerHTML = `<div class="empty">${t("sidebar.noTags")}</div>`;
    return;
  }
  box.innerHTML = ungrouped.map(x => {
    const on_ = state.tags.includes(x.name);
    return `<span class="tag ${tagClass(x.name)} on ${on_ ? "selected" : ""}" data-t="${esc(x.name)}">
      ${esc(x.name)}<span class="n">${x.count}</span></span>`;
  }).join("");

  box.querySelectorAll(".tag").forEach(el => el.onclick = () => {
    toggleTag(el.dataset.t);
    renderGroupTree(); renderUngroupedTags(); actions.search();
    // 單選模式選完就該回到結果列表;複選模式留著彈窗,讓使用者一次挑好幾個
    if (!state.multiSelect) closeUngrouped();
  });
}

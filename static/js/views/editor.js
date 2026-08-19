// 編輯器 view:新建/編輯名詞的表單,含所見即所得說明欄(contenteditable)、
// 圖片貼上上傳、附件管理、分類/標籤選單。
// 儲存時用 htmlToMd 把 DOM 轉回儲存格式(見 markdown.js 檔頭的語法說明)。
import * as actions from "../actions.js?v=20260820a";
import * as api from "../api.js?v=20260820a";
import {emit} from "../bus.js?v=20260820a";
import {mountSelect} from "../components/select.js?v=20260820a";
import {fieldDefsFor, templateById, tplIcon, tplLabel} from "../fields.js?v=20260820a";
import {t} from "../i18n.js?v=20260820a";
import {state} from "../store.js?v=20260820a";
import {$, esc, fmtBytes, isImageFile, isNarrowScreen, tagClass} from "../utils.js?v=20260820a";
import {compressImage} from "../imagecomp.js?v=20260820a";
import {SUPPORTED_LANGS} from "../highlight.js?v=20260820a";
import {cleanPastedText, htmlToMd, htmlTableToMd, langFromHtml, looksLikeCode, lookupNote,
  mdToHtml, safeUrl, tsvToMd} from "../markdown.js?v=20260820a";

const DEFAULT_TEMPLATE_ID = "jargon-default";
// 「文章>關鍵字」外掛(見 app/plugins.py):樣板下拉的假選項值,不是真的樣板 id,
// 選到它就進入文章批次生成模式,永遠不會被存進名詞。
const ARTICLE_PLUGIN_ID = "article-keywords";
const ARTICLE_OPTION_VALUE = "__article__";

// 點編輯器以外的空白處自動取消(僅限尚未輸入任何內容時,見 bindEditor 內的
// isEditorEmpty)。bindEditor() 每次重繪都會呼叫,用模組層級變數記住上一個
// handler 才能在重掛前先移除,否則每次重繪都會疊加一個新的 document 監聽。
let outsideClickHandler = null;

// 這次上傳有被壓縮的圖片:assets 路徑 → 「壓縮前 → 壓縮後」文字。純粹是給
// 使用者看的暫存提示(附件縮圖的 title),不進 attachments 物件、不進 payload。
// 放模組層級是因為 bindEditor() 每次重繪都會重跑,區域變數會被清掉。
const compressNotes = new Map();

// 樣板欄位區塊:依欄位定義兩兩一排產生 input(切換樣板時單獨重繪這一塊)。
// 每個欄位帶 ✕ 清空圖示;AI 啟用時再帶 ✨ 重寫圖示(點擊由編輯器根節點事件委派處理,
// 重繪不用重綁)。
// 桌機:標題常駐,長提示收進標題右邊那顆 ?(原生 title,滑鼠移上去才顯示)。
// 提示塞在 input 的 placeholder 裡會佔滿整個輸入框、看起來像已經填了值,而它一打字
// 就消失、本來就留不住;? 是零高度、零 JS 的揭露方式。⚠ 用 <span> 不是 <button>:
// 它沒有可執行的動作,做成按鈕只會進 Tab 迴圈、被讀螢幕軟體念成「按鈕」卻按了沒事。
// 已知取捨:原生 title 只在 hover 出現,鍵盤使用者拿不到——欄位標題本身仍是常駐線索。
// ⚠ 窄螢幕的排版以「壓低新建視窗高度」為最優先:不輸出 .f-lab 那一列,改把
// **欄位名稱**(不是長提示)當 input 的 placeholder——每個欄位從「標題一行 +
// 輸入一行」壓成一行,✨/✕ 維持在 input 右側。代價是打了字之後欄位就沒有可見的
// 名稱、長提示在手機上完全看不到,這是使用者明確選擇的取捨(高度 > 提示)。
// CSS 拿不掉 placeholder 屬性,只能在渲染當下決定要輸出哪一種——判斷走 utils.js
// 的 isNarrowScreen(與 main.css 的「手機條件」同一把尺,含橫放矮螢幕)。
// 刻意不掛 matchMedia 監聽:轉螢幕方向要等下次重繪才變,而重繪(drawFields)會把
// 使用者正在輸入的游標位置弄掉,那個代價比這個邊緣情境大。
function fieldsHTML(defs, values) {
  const ai = !!state.aiSettings?.enabled;
  const narrow = isNarrowScreen();
  let html = "";
  for (let i = 0; i < defs.length; i += 2) {
    const pair = defs.slice(i, i + 2).map(d => `<div class="field-cell" data-cell="${esc(d.key)}">
      ${!narrow && d.label ? `<span class="f-lab">${esc(d.label)}${
        d.placeholder && d.placeholder !== d.label
          ? `<span class="f-hint" title="${esc(d.placeholder)}">?</span>` : ""
      }</span>` : ""}
      <input type="text" class="e-field" data-key="${esc(d.key)}"
        placeholder="${esc(narrow ? d.label : "")}" value="${esc(values[d.key] || "")}">
      ${ai ? `<button type="button" class="field-icon ai-regen" data-target="${esc(d.key)}" title="${esc(t("editor.aiRewriteField"))}">✨</button>` : ""}
      <button type="button" class="field-icon field-clear" data-target="${esc(d.key)}" title="${esc(t("editor.clear"))}">✕</button>
    </div>`).join("");
    html += `<div class="row2">${pair}</div>`;
  }
  return html;
}

function templateOptionsHTML(current) {
  // 只列出啟用中的樣板;若正在編輯的名詞用了已停用的樣板,仍保留該選項不強制切換
  let opts = state.allTemplates
    .filter(tpl => tpl.enabled !== false || tpl.id === current)
    // 名稱前面帶樣板自己的代表 icon(原生 <option> 只吃純文字,所以 icon 只能是
    // emoji/Unicode 字元——正好也是專案「不用 icon font、不用圖檔」的既有慣例)
    .map(tpl => `<option value="${esc(tpl.id)}" ${tpl.id === current ? "selected" : ""}>${esc(tplIcon(tpl) + " " + tplLabel(tpl))}</option>`).join("");
  if (current && !templateById(current)) {
    // 樣板已刪:保留原 id 的選項,讓使用者可以不動它繼續編輯
    opts += `<option value="${esc(current)}" selected>${esc(t("editor.tplDeleted"))}</option>`;
  }
  return opts;
}

export function editorHTML(n) {
  const creating = !n;
  // 記住本次 session 新建時最後選過的樣板(state.lastNewTemplateId),下次「新建」
  // 直接帶入,不用每次都重選;樣板已被刪除/停用時退回預設值。
  const defaultTemplate = (state.lastNewTemplateId && templateById(state.lastNewTemplateId))
    ? state.lastNewTemplateId : DEFAULT_TEMPLATE_ID;
  // 新建時的預設名稱:點了尚未建立的 [[連結]] 會先把名稱放進 state.newName
  // (見 detail.js:createLinked),否則沿用搜尋列打到一半的關鍵字。
  n = n || {name: state.newName || state.q.trim(), description: "", template: defaultTemplate,
    fields: {}, attachments: [], tags: [...state.tags]};
  // 文章>關鍵字外掛:只在「新建 + AI 啟用 + 外掛已安裝**且未停用**」時,
  // 樣板下拉出現入口選項(pluginActive 不是 pluginInstalled——停用=入口消失)。
  // 選項文字讀 state.plugins 那筆的 name:名稱的真相在封裝 manifest,不在 i18n。
  const articleAvailable = creating && state.aiSettings?.enabled
    && actions.pluginActive(ARTICLE_PLUGIN_ID);
  const articlePlugin = state.plugins.find(p => p.id === ARTICLE_PLUGIN_ID);
  const articleOption = articleAvailable
    ? `<option value="${ARTICLE_OPTION_VALUE}">${esc(articlePlugin?.name || ARTICLE_PLUGIN_ID)}</option>` : "";
  return `<div class="editor" data-eid="${n.id || ""}"
      data-updated="${n.updated || ""}"
      data-tags="${esc(JSON.stringify(n.tags))}"
      data-template="${esc(n.template || DEFAULT_TEMPLATE_ID)}"
      data-fields="${esc(JSON.stringify(n.fields || {}))}"
      data-attachments="${esc(JSON.stringify(n.attachments || []))}">
    <!-- 頂端那一列:樣板下拉。刻意**不加**領域 icon:選項本身已經帶著
         📖/🔤/💻/🌿(見 templateOptionsHTML),前面再放一顆就是兩個圖示相鄰。 -->
    <div class="e-head-row">
      <select id="e_template" title="${esc(t("editor.tplTitle"))}">${templateOptionsHTML(n.template || DEFAULT_TEMPLATE_ID)}${articleOption}</select>
    </div>
    <button type="button" class="editor-close" id="e_close" title="${esc(t("editor.cancel"))}" aria-label="${esc(t("editor.cancel"))}">✕</button>
    ${!n.id && state.aiSettings?.enabled ? `
    <div class="aigen-box" id="e_ai_paste" style="display:none">
      <textarea id="e_ai_input" rows="4" placeholder="${esc(t("editor.aiPastePh"))}"></textarea>
      <button type="button" class="btn" id="e_ai_paste_btn">${t("editor.aiGenBtn")}</button>
    </div>
    ${articleAvailable ? `
    <div class="aigen-box" id="e_article_box" style="display:none">
      <textarea id="e_article_text" rows="8" placeholder="${esc(t("editor.articlePh"))}"></textarea>
      <div class="article-hint">${t("editor.articleHint")}</div>
      <div class="article-preview" id="e_article_preview" style="display:none"></div>
      <input type="text" id="e_article_kw" placeholder="${esc(t("editor.articleKwPh"))}">
      <div class="article-kws" id="e_article_kws"></div>
      <div class="article-actions">
        <button type="button" class="btn primary" id="e_article_gen" disabled>${t("editor.articleGen", {n: 0})}</button>
        <span class="hint" id="e_article_progress"></span>
      </div>
    </div>` : ""}` : ""}
    <!-- 「你可能已經記過這個」:名詞欄輸入後即時比對既有名詞,見 bindEditor 的 checkSimilar -->
    <div class="dupwarn" id="e_dupwarn" style="display:none"></div>
    <!-- ⚠ 樣板下拉搬到頂端那一列之後,這個 .row2 只剩一個孩子(名詞欄滿寬)——
         但**外層包裝不能拿掉**:runAiField() 靠 .name-cell 的 closest(".row2")
         當新舊比較盒的插入錨點,少了它名詞欄的 ✨ 會對 null 呼叫 .after() 而整支爆掉。 -->
    <div class="row2">
      <div class="name-cell">
        <input type="text" id="e_name" placeholder="${esc(t("editor.namePh"))}" value="${esc(n.name)}">
        ${state.aiSettings?.enabled
          // 同一顆 🤖 兩種語意:新建 = 依名詞生成整筆(由 updateAiMode 決定顯不顯示,
          // 所以要帶 display:none 當初始狀態);編輯既有名詞 = 一次補齊所有空白欄位
          // (updateAiMode 在編輯狀態直接 return,不會有人來把它打開,所以不能藏)。
          ? `<button type="button" class="ai-icon-btn" id="e_ai_name_btn" title="${esc(t(n.id ? "editor.aiFillMissing" : "editor.aiGenByName"))}"${n.id ? "" : ` style="display:none"`}>🤖</button>` : ""}
        ${state.aiSettings?.enabled
          ? `<button type="button" class="field-icon ai-regen" data-target="name" title="${esc(t("editor.aiRewriteName"))}">✨</button>` : ""}
        <button type="button" class="field-icon field-clear" data-target="name" title="${esc(t("editor.clearName"))}">✕</button>
      </div>
    </div>
    <div class="mdeditor" id="e_desc_wrap">
      <!-- collapsed 永遠掛著:收合只由 main.css 的 @media (max-width:640px) 實作,
           桌機那條 :not(.mdtoolbar-toggle) 規則根本不存在,所以完全不受影響——
           不需要任何 JS 斷點判斷,也不會因為轉螢幕方向而卡在錯的狀態。 -->
      <div class="mdtoolbar collapsed">
        <button type="button" class="mdbtn mdtoolbar-toggle" data-act="toolbar" title="${esc(t("editor.toolbarToggle"))}">▸</button>
        <button type="button" class="mdbtn" data-act="bold" title="${esc(t("editor.bold"))}"><b>B</b></button>
        <button type="button" class="mdbtn" data-act="italic" title="${esc(t("editor.italic"))}"><i>I</i></button>
        <button type="button" class="mdbtn" data-act="underline" title="${esc(t("editor.underline"))}"><u>U</u></button>
        <button type="button" class="mdbtn" data-act="link" title="${esc(t("editor.link"))}">🔗</button>
        <button type="button" class="mdbtn" data-act="wikilink" title="${esc(t("editor.wikilink"))}">[[ ]]</button>
        <button type="button" class="mdbtn" data-act="code" title="${esc(t("editor.inlineCode"))}">&lt;/&gt;</button>
        <button type="button" class="mdbtn" data-act="codeblock" title="${esc(t("editor.codeBlock"))}">{ }</button>
        <select class="mdlang" id="e_code_lang" title="${esc(t("editor.codeLang"))}">
          <option value="">${esc(t("editor.codeLangPlain"))}</option>
          ${SUPPORTED_LANGS.map(l => `<option value="${esc(l)}">${esc(l)}</option>`).join("")}
        </select>
        <button type="button" class="mdbtn" data-act="ul" title="${esc(t("editor.bulletList"))}">☰</button>
        <button type="button" class="mdbtn" data-act="ol" title="${esc(t("editor.numberedList"))}">☰¹</button>
        <span class="spacer"></span>
        <button type="button" class="mdbtn" data-act="mdsource" title="${esc(t("editor.mdSourceShow"))}">md</button>
        ${state.aiSettings?.enabled
          ? `<button type="button" class="mdbtn ai-regen" data-target="description" title="${esc(t("editor.aiRewriteDesc"))}">✨ AI</button>` : ""}
        <button type="button" class="mdbtn field-clear" data-target="description" title="${esc(t("editor.clearDesc"))}">✕</button>
      </div>
      <div class="mdcontent" id="e_desc" contenteditable="true"
        data-placeholder="${esc(t("editor.descPh"))}">${mdToHtml(n.description)}</div>
      <textarea class="mdsource" id="e_desc_src" style="display:none"
        placeholder="${esc(t("editor.descPh"))}"></textarea>
    </div>
    <!-- 名詞連結挑選器:工具列的 [[ ]] 或在說明欄打出 "[[" 都會叫出來。
         刻意放在 .mdeditor **外面**——它有 overflow:hidden(給圓角切邊用),
         放裡面會被裁掉。定位基準是 .editor(position:relative),開啟時由 JS
         算好 top 貼在說明欄工具列下方。 -->
    <div class="wl-picker" id="e_wl_picker" style="display:none">
      <input type="text" id="e_wl_search" placeholder="${esc(t("editor.wlSearchPh"))}">
      <div class="wl-picker-list" id="e_wl_list"></div>
    </div>
    <div id="e_fields"></div>
    <div class="row2">
      <div class="tags-cell">
        <div class="selectfield" id="e_tags_field"></div>
        ${state.aiSettings?.enabled
          ? `<button type="button" class="ai-icon-btn" id="e_ai_tags_btn" title="${esc(t("editor.aiSuggestTags"))}">🏷️</button>` : ""}
      </div>
    </div>
    <div class="attach-row">
      <button type="button" class="btn" id="e_attach_add">${t("editor.addAttach")}</button>
      <input type="file" id="e_attach_input" style="display:none">
      <div class="attach-editor" id="e_attach_list"></div>
    </div>
    <div class="actions">
      <button class="btn primary" id="e_save">${esc(t("editor.save"))}</button>
      <button class="btn" id="e_cancel">${esc(t("editor.cancel"))}</button>
      <span class="hint">${esc(t("editor.shortcutHint"))}</span>
      <span class="spacer"></span>
      ${n.id ? `<button class="btn danger" id="e_del">${esc(t("editor.delete"))}</button>` : ""}
    </div>
  </div>`;
}

export function bindEditor() {
  const box = $(".editor"); if (!box) return;
  // 同一個編輯器 DOM 只綁一次:重複呼叫會讓所有 addEventListener 的監聽疊加
  // (貼上會插入兩份、附件會上傳兩次、粗體按兩次等於沒按)。重繪會產生新的
  // .editor 節點,沒有這個標記,所以不影響正常的重繪重綁。
  if (box.dataset.bound === "1") return;
  box.dataset.bound = "1";
  const eid = box.dataset.eid;
  // 樂觀鎖的基準:編輯器打開的當下這筆名詞的 updated。存檔時送回去比對,
  // 對不上就 409——不會拿編輯器裡的舊內容蓋掉別人(或另一個分頁)剛存的東西。
  const baseUpdated = parseFloat(box.dataset.updated) || null;
  const initTags = JSON.parse(box.dataset.tags || "[]");

  // 樣板欄位:fieldValues 以 key 累積所有輸入過的值——切換樣板時同 key 的值
  // 保留不丟,payload 只送「目前樣板欄位定義內」的 key
  const fieldValues = JSON.parse(box.dataset.fields || "{}");
  let currentDefs = [];
  function collectFieldInputs() {
    box.querySelectorAll(".e-field").forEach(inp => { fieldValues[inp.dataset.key] = inp.value; });
  }
  function drawFields() {
    currentDefs = fieldDefsFor({template: $("#e_template").value, fields: fieldValues});
    $("#e_fields").innerHTML = fieldsHTML(currentDefs, fieldValues);
  }
  // AI 生成的輸入方式:樣板的 ai_input_mode 決定 name(名詞旁的圖示)或
  // paste(貼上框);article(文章標註批次生成,收起一般表單只留標籤欄)改由
  // 「文章>關鍵字」外掛的假選項觸發,不再是樣板屬性。
  const aiEnabled = !eid && !!state.aiSettings?.enabled;
  function currentAiMode() {
    const v = $("#e_template").value;
    if (v === ARTICLE_OPTION_VALUE) return "article";
    return templateById(v)?.ai_input_mode === "paste" ? "paste" : "name";
  }
  function updateAiMode() {
    if (!aiEnabled) return;
    const mode = currentAiMode();
    if ($("#e_ai_paste")) $("#e_ai_paste").style.display = mode === "paste" ? "" : "none";
    if ($("#e_ai_name_btn")) $("#e_ai_name_btn").style.display = mode === "name" ? "" : "none";
    if ($("#e_article_box")) $("#e_article_box").style.display = mode === "article" ? "" : "none";
    // article 模式是批次生成入口,不是可儲存的單筆表單:收起一般表單與儲存鈕
    const normal = mode === "article" ? "none" : "";
    box.querySelector(".name-cell").style.display = normal;
    $("#e_desc_wrap").style.display = normal;
    $("#e_fields").style.display = normal;
    box.querySelector(".attach-row").style.display = normal;
    $("#e_save").style.display = normal;
  }
  $("#e_template").addEventListener("change", () => {
    collectFieldInputs(); drawFields(); updateAiMode();
    // 只在「新建」時記,且排除文章外掛的假選項(不是真的樣板 id)
    if (!eid && $("#e_template").value !== ARTICLE_OPTION_VALUE) state.lastNewTemplateId = $("#e_template").value;
  });
  drawFields();

  const tagField = mountSelect($("#e_tags_field"), {
    multi: true, value: initTags, grid: true,
    // 依使用次數多的在前(後端 /api/tags 本來就這樣排,這裡再排一次把契約釘在
    // 呼叫端——挑標籤時常用的就在最上面,不用捲也不用搜)
    options: () => [...state.allTags].sort((a, b) => (b.count || 0) - (a.count || 0)).map(t => t.name),
    placeholder: t("editor.tagsPh"),
    searchPlaceholder: t("editor.tagsSearchPh"),
    chipHTML: v => `<span class="tag ${tagClass(v)} on" data-v="${esc(v)}">${esc(v)}<button class="x" data-v="${esc(v)}" title="${esc(t("editor.remove"))}">✕</button></span>`,
    onChange: () => {},
  });

  const imgTargetId = eid || state.draftId;
  const descEl = $("#e_desc");
  const srcEl = $("#e_desc_src");
  document.execCommand("defaultParagraphSeparator", false, "br");
  // 「檢視/編輯 Markdown 原始碼」切換:所見即所得的 contenteditable 換成純
  // textarea 顯示/編輯儲存格式本身。切到原始碼模式時,其餘工具列按鈕(粗體/
  // 清單…)對 contenteditable 做 DOM 操作沒有意義,一併停用;getDescMd/
  // setDescMd 是說明欄內容讀寫的唯一入口,所有其餘存取(儲存/AI 生成/清空)
  // 都改走這兩個 helper,不直接碰 descEl,才不會在原始碼模式下互相蓋掉。
  let sourceMode = false;
  const mdsourceBtn = $("#e_desc_wrap").querySelector('[data-act="mdsource"]');
  function getDescMd() { return sourceMode ? srcEl.value : htmlToMd(descEl); }
  function setDescMd(md) { if (sourceMode) srcEl.value = md; else descEl.innerHTML = mdToHtml(md); }
  function toggleSourceMode() {
    const md = getDescMd();
    sourceMode = !sourceMode;
    if (sourceMode) { srcEl.value = md; descEl.style.display = "none"; srcEl.style.display = ""; srcEl.focus(); }
    else { descEl.innerHTML = mdToHtml(md); srcEl.style.display = "none"; descEl.style.display = ""; descEl.focus(); }
    // toolbar(手機版的收合小三角)要跟 mdsource 一樣排除:它不是對 contenteditable
    // 做 DOM 操作的按鈕,停用它會讓原始碼模式下展開的工具列再也收不回去。
    $("#e_desc_wrap").querySelectorAll('.mdbtn[data-act]:not([data-act="mdsource"]):not([data-act="toolbar"])')
      .forEach(btn => btn.disabled = sourceMode);
    $("#e_code_lang").disabled = sourceMode;
    if (mdsourceBtn) {
      mdsourceBtn.classList.toggle("active", sourceMode);
      mdsourceBtn.title = sourceMode ? t("editor.mdSourceHide") : t("editor.mdSourceShow");
    }
  }
  // 插入超連結:網址用 prompt() 收集(跟畫面上其他「單一值輸入」互動一致,
  // 不另外做彈窗),送進 safeUrl() 擋掉 javascript:/data: 等可執行 scheme——
  // 這份白名單跟渲染時(markdown.js)共用同一份判斷,避免兩邊标准不一致。
  function insertLink() {
    const sel = window.getSelection();
    if (!sel.rangeCount || !descEl.contains(sel.getRangeAt(0).commonAncestorContainer)) return;
    const range = sel.getRangeAt(0);
    const label = range.collapsed ? "" : range.toString();
    const input = prompt(t("editor.linkPrompt"), "https://");
    if (!input) return;
    const safe = safeUrl(input.trim());
    if (!safe) { alert(t("editor.linkInvalid")); return; }
    const a = document.createElement("a");
    a.href = safe; a.target = "_blank"; a.rel = "noopener noreferrer";
    a.textContent = label || safe;
    if (!range.collapsed) range.deleteContents();
    range.insertNode(a);
    range.setStartAfter(a); range.setEndAfter(a);
    sel.removeAllRanges(); sel.addRange(range);
  }
  $("#e_desc_wrap").querySelectorAll(".mdbtn[data-act]").forEach(b => {
    b.addEventListener("mousedown", e => {
      e.preventDefault();
      const act = b.dataset.act;
      if (act === "mdsource") { toggleSourceMode(); return; }
      // 收合鈕跟 mdsource 一樣要排在 descEl.focus() 之前:它不動內容,
      // 而在手機上把鍵盤叫出來只會把剛展開的工具列頂出畫面。
      if (act === "toolbar") {
        const bar = b.closest(".mdtoolbar");
        const open = bar.classList.toggle("collapsed") === false;
        b.textContent = open ? "▾" : "▸";
        return;
      }
      descEl.focus();
      if (act === "wikilink") openWlPicker(0);
      else if (act === "bold") document.execCommand("bold");
      else if (act === "italic") document.execCommand("italic");
      else if (act === "underline") document.execCommand("underline");
      else if (act === "link") insertLink();
      else if (act === "code") wrapSelectionWithCode();
      else if (act === "codeblock") insertCodeBlock();
      else if (act === "ul") document.execCommand("insertUnorderedList");
      else if (act === "ol") document.execCommand("insertOrderedList");
    });
  });
  // 語言下拉:游標已經在某個程式碼區塊裡就改那一塊的語言,否則只是決定
  // 「下一次插入/貼上的區塊」用哪個語言(不用先插入再回頭改)。
  const langSel = $("#e_code_lang");
  langSel.addEventListener("change", () => {
    const pre = currentPre();
    if (pre) setPreLang(pre, langSel.value);
    descEl.focus();
  });
  // 游標移進/移出程式碼區塊時,下拉跟著顯示該區塊目前的語言
  descEl.addEventListener("keyup", syncLangSelect);
  descEl.addEventListener("mouseup", syncLangSelect);
  function syncLangSelect() {
    const pre = currentPre();
    if (pre) langSel.value = pre.getAttribute("data-lang") || "";
  }
  // 游標所在的程式碼區塊(不在區塊裡就回 null)
  function currentPre() {
    const sel = window.getSelection();
    if (!sel.rangeCount) return null;
    let n = sel.getRangeAt(0).commonAncestorContainer;
    if (n.nodeType !== Node.ELEMENT_NODE) n = n.parentNode;
    const pre = n && n.closest ? n.closest("pre") : null;
    return pre && descEl.contains(pre) ? pre : null;
  }
  function setPreLang(pre, lang) {
    // data-lang 是 htmlToMd 讀回圍籬語言的依據,空字串要整個拿掉而不是留空屬性
    if (lang) pre.setAttribute("data-lang", lang);
    else pre.removeAttribute("data-lang");
  }
  // 產生一個編輯器用的程式碼區塊。刻意用 <pre class="cb"><code>純文字</code></pre>
  // 這個「乾淨形狀」——htmlToMd 只認得它(詳細頁那種帶語言標籤/複製鈕的
  // 完整版一旦進了 contenteditable,那些 UI 文字會被當成內容存進說明欄)。
  function makeCodeBlock(lang, code) {
    const pre = document.createElement("pre");
    pre.className = "cb";
    setPreLang(pre, lang);
    const c = document.createElement("code");
    c.textContent = code;
    pre.appendChild(c);
    return pre;
  }
  // 插入(或把選取的文字包成)程式碼區塊;已經在區塊裡就只套用目前選的語言
  function insertCodeBlock() {
    const pre = currentPre();
    if (pre) { setPreLang(pre, langSel.value); return; }
    const sel = window.getSelection();
    const picked = sel && sel.rangeCount && !sel.isCollapsed
      && descEl.contains(sel.getRangeAt(0).commonAncestorContainer) ? sel.toString() : "";
    if (picked) sel.getRangeAt(0).deleteContents();
    const block = makeCodeBlock(langSel.value, picked);
    insertNodeAtCaret(block);
    caretIntoCode(block);
  }
  // 區塊插進來之後把游標移進 <code>,並確保它後面留得下東西——
  // <pre> 落在內容最後時,contenteditable 會夾不到游標,使用者就再也打不出
  // 區塊外的文字了,所以補一個 <br> 當出口。
  function caretIntoCode(pre) {
    if (!pre.nextSibling) pre.after(document.createElement("br"));
    const code = pre.querySelector("code");
    const range = document.createRange();
    range.selectNodeContents(code);
    range.collapse(false);
    const sel = window.getSelection();
    sel.removeAllRanges(); sel.addRange(range);
    langSel.value = pre.getAttribute("data-lang") || "";
  }
  function wrapSelectionWithCode() {
    const sel = window.getSelection();
    if (!sel.rangeCount) return;
    const range = sel.getRangeAt(0);
    if (range.collapsed || !descEl.contains(range.commonAncestorContainer)) return;
    const code = document.createElement("code");
    code.appendChild(range.extractContents());
    range.insertNode(code);
    sel.removeAllRanges();
    const r2 = document.createRange(); r2.selectNodeContents(code);
    sel.addRange(r2);
  }
  function insertNodeAtCaret(node) {
    const sel = window.getSelection();
    if (!sel.rangeCount || !descEl.contains(sel.getRangeAt(0).commonAncestorContainer)) {
      descEl.appendChild(node); return;
    }
    const range = sel.getRangeAt(0);
    range.deleteContents(); range.insertNode(node);
    range.setStartAfter(node); range.setEndAfter(node);
    sel.removeAllRanges(); sel.addRange(range);
  }
  // ── 名詞連結 [[名詞]] ──
  // 兩個入口:工具列的 [[ ]] 按鈕,以及在說明欄直接打出 "[["(Obsidian/Notion 的
  // 肌肉記憶)。挑選器一律插入「乾淨形狀」的 <a class="wikilink" data-wl="名稱">——
  // htmlToMd 認的就是這個形狀,名稱以 data-wl 為準,不靠顯示文字。
  const wlPicker = $("#e_wl_picker"), wlSearch = $("#e_wl_search"), wlList = $("#e_wl_list");
  let wlRange = null;      // 叫出挑選器前的游標位置(焦點移到搜尋框後 selection 就沒了)
  let wlEatChars = 0;      // 插入前要往回吃掉幾個字元(打 "[[" 觸發時是 2)
  let wlHl = -1;           // 鍵盤上下鍵選到第幾列

  // 連結在**目標庫**內解析(切片 4):存進團隊庫的名詞,[[連結]] 指的是那個
  // 團隊的名詞——挑選器列的、missing 判斷查的,都要是目標庫自己的名稱表。
  const wlIndex = () => {
    return state.noteIndex || new Map();
  };
  function makeWikilinkEl(name) {
    const hit = lookupNote(name);
    const a = document.createElement("a");
    // 目標不存在也照插:「先寫下之後要補的名詞」正是這個語法存在的理由,
    // .missing 只是把它畫成待建立的樣子,不是錯誤。
    a.className = "wikilink" + (hit ? "" : " missing");
    a.setAttribute("data-wl", name);
    if (hit) a.setAttribute("data-wl-id", hit.id);
    a.textContent = name;
    return a;
  }
  function renderWlOptions() {
    const q = wlSearch.value.trim().toLowerCase();
    const all = [...wlIndex().values()].map(x => x.name);
    const hits = (q ? all.filter(nm => nm.toLowerCase().includes(q)) : all).slice(0, 30);
    let html = hits.map(nm => `<div class="wl-opt" data-name="${esc(nm)}">${esc(nm)}</div>`).join("");
    // 打了字但沒有完全相同的既有名詞 → 仍可插入,連到還沒建立的名詞是允許的用法
    if (q && !all.some(nm => nm.toLowerCase() === q)) {
      html += `<div class="wl-opt create" data-name="${esc(wlSearch.value.trim())}">${
        t("editor.wlCreate", {q: esc(wlSearch.value.trim())})}</div>`;
    }
    wlList.innerHTML = html || `<div class="wl-opt empty">${esc(t("editor.wlNoMatch"))}</div>`;
    wlHl = -1;
    wlList.querySelectorAll(".wl-opt[data-name]").forEach(el => el.onmousedown = ev => {
      ev.preventDefault();
      pickWikilink(el.dataset.name);
    });
  }
  function openWlPicker(eatChars) {
    const sel = window.getSelection();
    if (!sel.rangeCount || !descEl.contains(sel.getRangeAt(0).commonAncestorContainer)) return;
    wlRange = sel.getRangeAt(0).cloneRange();
    wlEatChars = eatChars;
    // 貼在說明欄工具列正下方(定位基準是 .editor,見 editorHTML 裡的註解)
    wlPicker.style.top = ($("#e_desc_wrap").offsetTop + $("#e_desc_wrap").querySelector(".mdtoolbar").offsetHeight) + "px";
    wlPicker.style.display = "";
    wlSearch.value = "";
    renderWlOptions();
    wlSearch.focus();
  }
  function closeWlPicker(refocus = true) {
    if (wlPicker.style.display === "none") return;
    wlPicker.style.display = "none";
    wlRange = null; wlEatChars = 0;
    if (refocus) descEl.focus();
  }
  function pickWikilink(name) {
    if (!name || !wlRange) { closeWlPicker(); return; }
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(wlRange);
    const range = sel.getRangeAt(0);
    // 打 "[[" 觸發時,那兩個字元是觸發器不是內容,插入前先吃掉
    if (wlEatChars && range.startContainer.nodeType === Node.TEXT_NODE
        && range.startOffset >= wlEatChars) {
      range.setStart(range.startContainer, range.startOffset - wlEatChars);
    }
    range.deleteContents();
    const el = makeWikilinkEl(name);
    range.insertNode(el);
    // 連結落在內容最後時 contenteditable 夾不到游標,使用者就再也打不出連結外的
    // 文字了,所以補一個出口。用 <br> 而不是空白字元:<br> 收在結尾會被 htmlToMd
    // 結尾的換行修剪規則吃掉,存出來乾淨(同 caretIntoCode 補 <br> 的先例)。
    // (瀏覽器另外會自己在行內元素後面塞 NBSP 佔位,那個由 htmlToMd 統一折回空白。)
    if (!el.nextSibling) el.after(document.createElement("br"));
    range.setStartAfter(el); range.setEndAfter(el);
    sel.removeAllRanges(); sel.addRange(range);
    closeWlPicker();
  }
  wlSearch.addEventListener("input", renderWlOptions);
  wlSearch.addEventListener("keydown", e => {
    const rows = [...wlList.querySelectorAll(".wl-opt[data-name]")];
    if (e.key === "ArrowDown" && rows.length) {
      wlHl = (wlHl + 1) % rows.length; e.preventDefault();
    } else if (e.key === "ArrowUp" && rows.length) {
      wlHl = (wlHl - 1 + rows.length) % rows.length; e.preventDefault();
    } else if (e.key === "Enter") {
      e.preventDefault();
      const pick = wlHl >= 0 ? rows[wlHl] : rows[0];
      pickWikilink(pick ? pick.dataset.name : wlSearch.value.trim());
      return;
    } else if (e.key === "Escape") {
      e.preventDefault(); e.stopPropagation(); closeWlPicker(); return;
    } else return;
    rows.forEach((r, i) => r.classList.toggle("hl", i === wlHl));
    rows[wlHl]?.scrollIntoView({block: "nearest"});
  });
  wlSearch.addEventListener("blur", () => closeWlPicker(false));
  // 打 "[[" 自動叫出挑選器。程式碼區塊裡不觸發——那裡的中括號是內容。
  descEl.addEventListener("input", () => {
    if (currentPre()) return;
    const sel = window.getSelection();
    if (!sel.rangeCount) return;
    const r = sel.getRangeAt(0);
    if (r.startContainer.nodeType !== Node.TEXT_NODE) return;
    if (r.startContainer.textContent.slice(0, r.startOffset).endsWith("[[")) openWlPicker(2);
  });

  // 貼上的處理順序是有意義的,不能重排:每一關都比下一關更「確定」自己認得的
  // 是什麼,先讓確定的先接手,剩下的才往下丟。
  descEl.addEventListener("paste", async e => {
    e.preventDefault();
    const cd = e.clipboardData;
    const items = [...(cd?.items || [])];
    const html = cd?.getData("text/html") || "";
    const plain = (cd?.getData("text/plain") || "").replace(/\r\n?/g, "\n");

    // 1) 游標已經在程式碼區塊裡:原文照貼,不做任何 markdown/表格解讀——
    //    在程式碼區塊裡,縮排與換行本身就是內容。
    if (currentPre() && plain) {
      insertNodeAtCaret(document.createTextNode(plain));
      return;
    }

    // 2) 圖片:上傳成「附件」,不再內嵌進說明欄。
    //    (既有筆記裡已經內嵌的圖片照常顯示與存回,只是新貼的不再走那條路)
    const imgItem = items.find(it => it.kind === "file" && it.type.startsWith("image/"));
    if (imgItem) {
      const file = imgItem.getAsFile();
      if (file) await addPastedImage(file, imgItem.type);
      return;
    }

    // 3) 程式碼:剪貼簿 HTML 是 <pre>,或純文字帶縮排 → 原文一字不動包成
    //    程式碼區塊。這關一定要排在表格前面——Tab 縮排的程式碼每行都含 Tab,
    //    落到表格那關會被切成一張爛表格,內容就救不回來了。
    const codeText = plain.replace(/\n$/, "");
    if (codeText && (/<pre[\s>]/i.test(html) || looksLikeCode(codeText))) {
      const block = makeCodeBlock(langFromHtml(html) || langSel.value, codeText);
      insertNodeAtCaret(block);
      caretIntoCode(block);
      return;
    }

    // 4) 從 Excel/Google Sheets/Word/Notion 貼表格時,text/html 通常帶 <table>
    //    (只有 text/plain 的話退而求其次認 Tab 分隔的 TSV)——偵測到就直接轉成
    //    表格語法插入,不要落到下面的一般文字路徑被拆散成一堆孤立的儲存格文字。
    const tableMd = (html && /<table[\s>]/i.test(html)) ? htmlTableToMd(html) : tsvToMd(plain);
    if (tableMd) { document.execCommand("insertHTML", false, mdToHtml(tableMd)); return; }

    // 5) 一般文字:**原文照貼**,不再當 markdown 解析(2026-08-12 改版)。
    //    舊行為是過一遍 mdToHtml,好讓貼進來的 **粗體** 語法直接變格式——代價是
    //    數學/一般文字裡的 * _ ` [[ ]] 行首 - 全被語法吃掉(2*3*4 的星號消失、
    //    3 變斜體),而且完全無聲。所見即所得編輯器的貼上應該是字面的;存檔時
    //    htmlToMd 會自動補反斜線跳脫(見 markdown.js 檔頭),重開也不會被解析。
    //    程式碼與表格不受影響——它們在第 3、4 關就被接走了。
    //    換行用 <br>(對齊 defaultParagraphSeparator=br 的慣例),esc 擋掉 HTML。
    if (plain) {
      const literal = esc(cleanPastedText(plain)).replace(/\n/g, "<br>");
      document.execCommand("insertHTML", false, literal);
    }
  });

  let attachments = JSON.parse(box.dataset.attachments || "[]");
  function drawAttachments() {
    const list = $("#e_attach_list");
    // 圖片附件顯示縮圖(移除鈕疊在縮圖右上角),其他檔案維持 📎 檔名的 chip;
    // 兩者都各配一個說明輸入框,所以外層列的結構保持一致。
    list.innerHTML = attachments.map((a, i) => {
      // 剛壓縮過的圖片在 title 附上「壓縮前 → 壓縮後」,讓使用者知道上傳的不是原檔
      const saved = compressNotes.get(a.path);
      const title = a.name + (saved ? `(${saved})` : "");
      const head = isImageFile(a.name) || isImageFile(a.path)
        ? `<div class="attach-thumb">
             <img src="/${esc((a.path || "").replace(/^\/+/, ""))}" alt="${esc(a.name)}" title="${esc(title)}">
             <button type="button" class="x" title="${esc(t("editor.remove"))}" aria-label="${esc(t("editor.remove"))}">✕</button>
           </div>`
        : `<span class="attach-item" title="${esc(title)}">📎 ${esc(a.name)}<button type="button" class="x" title="${esc(t("editor.remove"))}">✕</button></span>`;
      return `<div class="attach-row-item" data-i="${i}">${head}
        <input type="text" class="attach-desc" data-i="${i}" placeholder="${esc(t("editor.attachDescPh"))}" value="${esc(a.description || "")}">
      </div>`;
    }).join("");
    // 移除鈕用 data-i 定位而不是靠 querySelectorAll 的順序:縮圖與 chip 是兩種
    // 不同的節點,混在一起時「第 n 個 .x」不保證對應第 n 筆附件。
    list.querySelectorAll(".attach-row-item .x").forEach(btn => btn.onclick = () => {
      attachments.splice(+btn.closest(".attach-row-item").dataset.i, 1);
      drawAttachments();
    });
    list.querySelectorAll(".attach-desc").forEach(inp => inp.addEventListener("input", () => {
      attachments[+inp.dataset.i].description = inp.value;
    }));
  }
  // 貼上的圖片與「新增附件」選的檔共用這條路:預設先過 compressImage()
  // (非圖片、GIF/SVG、夠小的圖都會原檔回來,見 imagecomp.js),再上傳。
  // 使用者可在設定 → 內容關掉壓縮(state.imgCompress,存 gv-imgcompress),
  // 關掉時直接照原檔上傳——這是這裡唯一的壓縮呼叫點,所以開關只要擋這一處。
  // baseName 用來覆寫檔名主體(貼上的截圖一律叫 image.png,不改名的話
  // 附件清單上好幾個同名項目分不出誰是誰),副檔名一律用壓縮後的實際格式。
  async function uploadAsAttachment(file, baseName, failMsg, mimeHint) {
    const {file: out, before, after, changed} = state.imgCompress
      ? await compressImage(file)
      : {file, before: file.size, after: file.size, changed: false};
    const ext = (out.name.match(/\.[^.]+$/) || [])[0]
      || "." + ((out.type || mimeHint || "").split("/")[1] || "png").replace("jpeg", "jpg");
    const fd = new FormData();
    fd.append("file", out, baseName ? baseName + ext : out.name);
    const r = await api.uploadAttachment(imgTargetId, fd);
    if (!r.ok) {
      // 後端的 detail(如超過上傳大小上限)是資料不是 UI 字串,直接顯示原文
      const d = await r.json().catch(() => ({}));
      alert(d.detail || failMsg);
      return;
    }
    const d = await r.json();
    if (changed) compressNotes.set(d.path, `${fmtBytes(before)} → ${fmtBytes(after)}`);
    attachments.push({name: d.name, path: d.path, description: ""});
    drawAttachments();
  }
  const addPastedImage = (file, mime) => {
    const named = file.name && !/^image\.\w+$/i.test(file.name);
    return uploadAsAttachment(file, named ? "" : `paste-${attachments.length + 1}`,
      t("editor.uploadImgFailed"), mime);
  };
  $("#e_attach_add").onclick = () => $("#e_attach_input").click();
  $("#e_attach_input").addEventListener("change", async e => {
    const file = e.target.files[0]; e.target.value = ""; if (!file) return;
    await uploadAsAttachment(file, "", t("editor.uploadAttachFailed"));
  });
  drawAttachments();

  // 兩種觸發口共用的生成流程:inputText 為輸入(名詞或貼上的內容),btn 用來顯示忙碌狀態
  async function runAiGenerate(inputText, btn) {
    const text = (inputText || "").trim();
    if (!text) { alert(currentAiMode() === "paste" ? t("editor.pasteFirst") : t("editor.nameFirst")); return; }
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = t("editor.generating");
    try {
      const r = await api.generateAI(text, $("#e_template").value);
      const d = await r.json();
      if (!r.ok) { alert(d.detail || t("editor.aiGenFailed")); return; }
      if (d.name) $("#e_name").value = d.name;
      collectFieldInputs();
      for (const [k, v] of Object.entries(d.fields || {})) if (v) fieldValues[k] = v;
      drawFields();
      // paste 模式(如程式片段):把貼上的原文保留成程式碼區塊,接在 AI 說明前面。
      // 但不是每個 paste 樣板都該包圍籬——判斷分兩層:
      // (1) 樣板定義含 language 欄(= 程式碼類,如 code-snippet)→ 一律包;
      //     查的是**樣板定義**不是 currentDefs(後者含殘留 key,同一次編輯先選過
      //     code-snippet 再切樣板,殘留的 language 會誤觸發)。
      // (2) 否則只在「AI 說明沒把原文帶回來」時才包當保底:一段話解讀這類樣板的
      //     prompt 已要求把原文照抄成 "> " 引文,再包一層圍籬就是同一段話出現兩次
      //     (還被畫成程式碼);但小模型沒照抄時,圍籬是原文唯一的存活路徑——
      //     重複總比靜默丟資料好。比對前去掉引文前綴、所有空白**與所有標點**:
      //     模型「照抄」時很常動標點(實例:原文的「;」被換成引文的換行、全形逗號
      //     變半形),只比空白會把這種照抄誤判成沒帶回來,圍籬又被包回去。
      //     剝掉標點後剩下的字元(漢字/字母/數字)相同,就當作原文有帶回來。
      let desc = d.description || "";
      if (currentAiMode() === "paste") {
        const tplDef = templateById($("#e_template").value);
        const hasLangField = (tplDef?.fields || []).some(f => f.key === "language" && f.enabled !== false);
        const squash = s => (s || "").replace(/^>\s?/gm, "").replace(/[\p{P}\p{S}\s]+/gu, "");
        if (hasLangField || !squash(desc).includes(squash(text))) {
          const lang = (d.fields?.language || "").trim();
          desc = "```" + lang + "\n" + text + "\n```" + (desc ? "\n\n" + desc : "");
        }
      }
      if (desc) setDescMd(desc);
      if (d.tags?.length) tagField.setValue([...new Set([...tagField.getValue(), ...d.tags])]);
    } finally {
      btn.disabled = false; btn.textContent = orig;
    }
  }
  // 編輯既有名詞時的 🤖:一次補齊所有空白欄位。
  // 「當初只是很快記下一個名詞」是這個 app 預期的用法(先記下來、之後再整理),
  // 所以事後補內容必須跟當初記下來一樣省事——一欄一欄按 ✨ 的話,本機模型
  // 五個空欄位就是半分鐘,沒有人會這樣做。**只填當下仍為空的欄位**,已經寫好的
  // 內容一律不碰(所以也不需要新舊比較盒:空的沒有舊值可比)。
  async function runAiFill(btn) {
    collectFieldInputs();
    const fields = {};
    for (const d of currentDefs) fields[d.key] = fieldValues[d.key] || "";
    const name = $("#e_name").value.trim(), description = getDescMd().trim();
    if (!name && !description && !Object.values(fields).some(v => v.trim())) {
      alert(t("editor.aiNeedContext")); return;
    }
    const targets = currentDefs.filter(d => !(fields[d.key] || "").trim()).map(d => d.key);
    if (!description) targets.unshift("description");
    if (!targets.length) { alert(t("editor.aiFillNothing")); return; }
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = "…";
    try {
      const r = await api.fillFields({targets, name, description, fields,
        tags: tagField.getValue(), template: $("#e_template").value});
      const d = await r.json();
      if (!r.ok) { alert(d.detail || t("editor.aiGenFailed")); return; }
      const values = d.values || {};
      // 等待期間使用者可能自己打了字:再確認一次「現在還是空的」才填進去。
      collectFieldInputs();
      let filled = 0;
      if (values.description && !getDescMd().trim()) { setDescMd(values.description); filled++; }
      for (const [k, v] of Object.entries(values)) {
        if (k === "description" || !v || (fieldValues[k] || "").trim()) continue;
        fieldValues[k] = v; filled++;
      }
      if (filled) drawFields();
      else alert(t("editor.aiNoContent"));
    } finally {
      btn.disabled = false; btn.textContent = orig;
    }
  }
  if (aiEnabled) {
    updateAiMode();
    if ($("#e_ai_paste_btn")) $("#e_ai_paste_btn").onclick = e => runAiGenerate($("#e_ai_input").value, e.currentTarget);
    if ($("#e_ai_name_btn")) $("#e_ai_name_btn").onclick = e => runAiGenerate($("#e_name").value, e.currentTarget);
  } else if (eid && $("#e_ai_name_btn")) {
    $("#e_ai_name_btn").onclick = e => runAiFill(e.currentTarget);
  }

  // ── article 模式(文章>關鍵字):貼文章 → 標註名詞 → 逐名詞生成建檔 ──
  // 每個名詞各自呼叫 /api/ai/article-note + POST /api/notes:可顯示進度,
  // 單一名詞失敗不拖垮其他已成功建立的名詞。
  let articleKeywords = [];
  let articleRunning = false;
  function drawArticleKws() {
    $("#e_article_kws").innerHTML = articleKeywords.map(k =>
      `<span class="tag on">${esc(k)}<button type="button" class="x" data-kw="${esc(k)}" title="${esc(t("editor.remove"))}">✕</button></span>`).join("");
    const btn = $("#e_article_gen");
    btn.textContent = t("editor.articleGen", {n: articleKeywords.length});
    btn.disabled = articleRunning || !articleKeywords.length;
  }
  function addArticleKw(kw) {
    kw = (kw || "").trim();
    if (!kw || kw.length > 80 || kw.includes("\n") || articleKeywords.includes(kw)) return;
    articleKeywords.push(kw);
    drawArticleKws();
  }
  async function runArticleGenerate() {
    const article = $("#e_article_text").value.trim();
    if (!article) { alert(t("editor.articleTextFirst")); return; }
    if (!articleKeywords.length) { alert(t("editor.articleKwFirst")); return; }
    const prog = $("#e_article_progress");
    const baseTags = tagField.getValue();
    const failed = [];
    let created = 0;
    articleRunning = true;
    drawArticleKws();
    try {
      for (let i = 0; i < articleKeywords.length; i++) {
        // 編輯器已被關閉 → 中止,但要用 break 而不是 return:已經成功建好的幾筆
        // 還是得走到下面的 refreshAll(),否則列表與側欄完全看不到剛建的資料。
        if (!document.body.contains(box)) break;
        const kw = articleKeywords[i];
        prog.textContent = t("editor.articleProgress", {i: i + 1, n: articleKeywords.length, kw});
        try {
          const r = await api.generateArticleNote(kw, article);
          const d = await r.json();
          if (!r.ok) throw new Error(d.detail || t("editor.aiGenFailed"));
          const s = await api.saveNote(null, {
            id: "", name: d.name || kw, description: d.description || "",
            template: d.template || DEFAULT_TEMPLATE_ID, fields: d.fields || {},
            tags: [...new Set([...baseTags, ...(d.tags || [])])], attachments: [],
          });
          if (!s.ok) throw new Error((await s.json()).detail || t("editor.saveFailed"));
          created++;
        } catch (err) {
          failed.push(`${kw}:${err.message}`);
        }
      }
    } finally {
      articleRunning = false;
      if (document.body.contains(box)) { drawArticleKws(); prog.textContent = ""; }
    }
    // 先刷新再 alert:alert 會阻塞,先把列表與側欄更新好,關掉訊息就看得到新資料
    if (created) {
      state.editing = null; state.creating = false; state.draftId = null;
      await actions.refreshAll();
    }
    let msg = t("editor.articleDone", {n: created});
    if (failed.length) msg += t("editor.articleFailedPart", {n: failed.length}) + "\n" + failed.join("\n");
    alert(msg);
  }
  if ($("#e_article_box")) {
    const textEl = $("#e_article_text"), prevEl = $("#e_article_preview");
    textEl.addEventListener("input", () => {
      prevEl.textContent = textEl.value;  // textContent:文章原文不當 HTML 解析
      prevEl.style.display = textEl.value.trim() ? "" : "none";
    });
    prevEl.addEventListener("mouseup", () => {
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed && prevEl.contains(sel.anchorNode)) {
        addArticleKw(sel.toString());
        sel.removeAllRanges();
      }
    });
    $("#e_article_kw").addEventListener("keydown", e => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      addArticleKw(e.currentTarget.value);
      e.currentTarget.value = "";
    });
    $("#e_article_kws").addEventListener("click", e => {
      const x = e.target.closest(".x");
      if (!x || articleRunning) return;
      articleKeywords = articleKeywords.filter(k => k !== x.dataset.kw);
      drawArticleKws();
    });
    $("#e_article_gen").onclick = runArticleGenerate;
  }
  // 標籤 AI:依目前已填寫的內容分析出最多三個關鍵字,併入既有標籤(不論新建/編輯都可用)
  if ($("#e_ai_tags_btn")) {
    $("#e_ai_tags_btn").onclick = async e => {
      const btn = e.currentTarget;
      collectFieldInputs();
      const fields = {};
      for (const d of currentDefs) fields[d.key] = fieldValues[d.key] || "";
      const name = $("#e_name").value.trim(), description = getDescMd().trim();
      if (!name && !description && !Object.values(fields).some(v => v.trim())) {
        alert(t("editor.tagsNeedContent")); return;
      }
      const orig = btn.textContent;
      btn.disabled = true; btn.textContent = "…";
      try {
        const r = await api.generateTags({
          name, description, fields,
          tags: tagField.getValue(), template: $("#e_template").value,
        });
        const d = await r.json();
        if (!r.ok) { alert(d.detail || t("editor.aiTagsFailed")); return; }
        if (d.tags?.length) tagField.setValue([...new Set([...tagField.getValue(), ...d.tags])]);
        else alert(t("editor.aiNoTags"));
      } finally {
        btn.disabled = false; btn.textContent = orig;
      }
    };
  }

  // ── 單一欄位的 AI 重寫與清空(名詞/說明/樣板欄位共用)──
  // 點擊用事件委派掛在編輯器根節點:樣板欄位重繪(drawFields)後不用重綁。
  function clearTarget(target) {
    if (target === "name") { $("#e_name").value = ""; $("#e_name").focus(); }
    else if (target === "description") { setDescMd(""); (sourceMode ? srcEl : descEl).focus(); }
    else {
      fieldValues[target] = "";
      const inp = box.querySelector(`.e-field[data-key="${CSS.escape(target)}"]`);
      if (inp) { inp.value = ""; inp.focus(); }
    }
  }
  function removeCompare() { box.querySelectorAll(".ai-compare").forEach(el => el.remove()); }
  // 新舊比較盒:插在欄位下方,一鍵選擇要用哪個;isDesc 時用 markdown 渲染預覽
  function showCompare(anchorEl, label, oldVal, newVal, apply, isDesc = false) {
    removeCompare();
    const cmp = document.createElement("div");
    cmp.className = "ai-compare";
    const render = v => isDesc ? mdToHtml(v) : esc(v);
    cmp.innerHTML = `<div class="ai-compare-head">${t("editor.compareHead", {label: esc(label)})}</div>
      <div class="ai-compare-cols">
        <div class="ai-compare-col">
          <div class="ai-compare-label">${esc(t("editor.compareOld"))}</div>
          <div class="ai-compare-val">${render(oldVal) || `<span class="faint">${esc(t("editor.blank"))}</span>`}</div>
          <button type="button" class="btn" data-pick="old">${esc(t("editor.keepOld"))}</button>
        </div>
        <div class="ai-compare-col is-new">
          <div class="ai-compare-label">${esc(t("editor.compareNew"))}</div>
          <div class="ai-compare-val">${render(newVal)}</div>
          <button type="button" class="btn primary" data-pick="new">${esc(t("editor.useNew"))}</button>
        </div>
      </div>`;
    anchorEl.after(cmp);
    cmp.querySelector('[data-pick="old"]').onclick = () => cmp.remove();
    cmp.querySelector('[data-pick="new"]').onclick = () => { apply(newVal); cmp.remove(); };
    cmp.scrollIntoView({block: "nearest"});
  }
  async function runAiField(target, btn) {
    collectFieldInputs();
    const fields = {};
    for (const d of currentDefs) fields[d.key] = fieldValues[d.key] || "";
    const name = $("#e_name").value.trim(), description = getDescMd().trim();
    if (!name && !description && !Object.values(fields).some(v => v.trim())) {
      alert(t("editor.aiNeedContext")); return;
    }
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = "…";
    try {
      const r = await api.generateField({target, name, description, fields,
        tags: tagField.getValue(), template: $("#e_template").value});
      const d = await r.json();
      if (!r.ok) { alert(d.detail || t("editor.aiGenFailed")); return; }
      const newVal = (d.value || "").trim();
      if (!newVal) { alert(t("editor.aiNoContent")); return; }
      if (target === "name") {
        showCompare(box.querySelector(".name-cell").closest(".row2"), t("editor.nameLabel"), name, newVal,
          v => { $("#e_name").value = v; });
      } else if (target === "description") {
        showCompare($("#e_desc_wrap"), t("editor.descLabel"), description, newVal,
          v => setDescMd(v), true);
      } else {
        const label = currentDefs.find(d2 => d2.key === target)?.label || target;
        const cell = box.querySelector(`.field-cell[data-cell="${CSS.escape(target)}"]`);
        showCompare(cell ? cell.closest(".row2") : $("#e_fields"), label, fields[target] || "", newVal,
          v => {
            fieldValues[target] = v;
            const inp = box.querySelector(`.e-field[data-key="${CSS.escape(target)}"]`);
            if (inp) inp.value = v;
          });
      }
    } finally {
      btn.disabled = false; btn.textContent = orig;
    }
  }
  box.addEventListener("click", e => {
    const clearBtn = e.target.closest(".field-clear");
    if (clearBtn) { clearTarget(clearBtn.dataset.target); return; }
    const aiBtn = e.target.closest(".ai-regen");
    if (aiBtn && !aiBtn.disabled) runAiField(aiBtn.dataset.target, aiBtn);
  });

  // ── 「你可能已經記過這個」──
  // 快速捕捉的必然後果就是同一個詞被記好幾次(大小寫、全形半形、加不加空格、
  // 或用別名記)。存檔後才發現就晚了,所以在打名詞的當下就比對既有名詞。
  // 這裡**只提示、不阻擋**:真的要記兩筆是使用者的自由(合併的入口在設定 → 內容)。
  let dupTimer = null;
  async function checkSimilar() {
    const box = $("#e_dupwarn");
    if (!box) return;
    const name = $("#e_name").value.trim();
    if (name.length < 2) { box.style.display = "none"; return; }
    collectFieldInputs();
    const fields = {};
    for (const d of currentDefs) fields[d.key] = fieldValues[d.key] || "";
    const hits = await api.findSimilar({name, fields, exclude_id: eid || ""});
    // await 期間編輯器可能已經被關掉/重繪,或使用者又改了名字
    if (!document.body.contains(box) || $("#e_name").value.trim() !== name) return;
    if (!hits.length) { box.style.display = "none"; return; }
    box.style.display = "";
    box.innerHTML = `<span class="dupwarn-head">${esc(t("dedup.maybeDuplicate"))}</span>
      ${hits.map(h => `<button type="button" class="dupwarn-chip" data-open="${esc(h.id)}"
          title="${esc(h.excerpt || h.name)}">${esc(h.name)}
          <span class="dupwarn-why">${esc(t("dedup.reason." + h.reason))}</span></button>`).join("")}`;
    box.querySelectorAll("[data-open]").forEach(b => b.onclick = () => {
      // views 之間不互相 import:把 id 放進 store,由組裝根(app.js)轉給詳細頁
      state.pendingNoteId = b.dataset.open;
      emit("open-note");
    });
  }
  const scheduleSimilarCheck = () => { clearTimeout(dupTimer); dupTimer = setTimeout(checkSimilar, 400); };
  $("#e_name").addEventListener("input", scheduleSimilarCheck);
  if ($("#e_name").value.trim()) scheduleSimilarCheck();  // 帶著名稱開啟時(如點了尚未建立的連結)也查

  const payload = () => {
    collectFieldInputs();
    const fields = {};
    for (const d of currentDefs) fields[d.key] = fieldValues[d.key] || "";
    // name 要 trim:同檔其他讀值處(重複偵測、AI 生成)全都 trim 過,唯獨
    // payload 漏掉會讓「比對用的值」與「送出的值」不一致(後端存檔時也會再
    // strip 一次,但前端顯示/快取的是這一份)。
    return {id: eid ? "" : (state.draftId || ""), name: $("#e_name").value.trim(),
      description: getDescMd(), template: $("#e_template").value, fields,
      tags: tagField.getValue(), attachments, base_updated: baseUpdated};
  };
  function cancelEdit() {
    if (state.creating && state.draftId) {
      // 取消新建 → 草稿期間貼的圖與附件一併清掉
      api.deleteAssets(state.draftId).catch(() => {});
    }
    state.editing = null; state.creating = false; state.draftId = null; state.newName = "";
    emit("results-changed");
  }
  // 名詞/說明/欄位/標籤/附件都還沒填過,才視為「尚未輸入任何內容」——
  // 有填過东西的話,點旁邊空白不該悄悄丟掉使用者的輸入。
  function isEditorEmpty() {
    return !$("#e_name").value.trim()
      && !getDescMd().trim()
      && ![...box.querySelectorAll(".e-field")].some(inp => inp.value.trim())
      && tagField.getValue().length === 0
      && attachments.length === 0
      // 文章>關鍵字模式的輸入不在上面那些欄位裡,漏算的話貼完文章點一下旁邊
      // 空白就會被當成「還沒輸入」而整個關掉
      && !($("#e_article_text")?.value || "").trim()
      && articleKeywords.length === 0;
  }
  $("#e_save").onclick = () => actions.saveNote(eid || null, payload());
  $("#e_cancel").onclick = cancelEdit;
  $("#e_close").onclick = cancelEdit;
  if ($("#e_del")) $("#e_del").onclick = () => actions.removeNote(eid);
  // 點編輯器以外的空白處:內容還是空的就視同取消。用 setTimeout 延後掛上,
  // 避免打開編輯器的那次點擊(同一個事件正在往 document 冒泡)被立刻誤判成
  // 「點外面」而剛開就關掉。
  if (outsideClickHandler) document.removeEventListener("click", outsideClickHandler);
  outsideClickHandler = e => {
    if (!state.creating && !state.editing) return;
    // 批次生成進行中絕不能關掉編輯器:box 一離開 DOM,生成迴圈就會中止
    if (articleRunning) return;
    if (e.target.closest(".editor")) return;
    if (!isEditorEmpty()) return;
    cancelEdit();
  };
  setTimeout(() => document.addEventListener("click", outsideClickHandler), 0);
  box.onkeydown = e => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      // article 模式沒有可儲存的單筆表單(儲存鈕也已收起),快捷鍵同樣不觸發
      if (!(aiEnabled && currentAiMode() === "article")) actions.saveNote(eid || null, payload());
    }
    if (e.key === "Escape") { e.stopPropagation(); cancelEdit(); }
  };
  if (state.focusDescOnOpen) { state.focusDescOnOpen = false; descEl.focus(); }
  else { $("#e_name").focus(); }
}

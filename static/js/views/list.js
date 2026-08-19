// 結果列表 view:卡片渲染、統計列,以及編輯器/新建表單(置中 modal,見 #editorModal)的開關。
// 監聽 results-changed 重繪;卡片點擊開詳細彈窗,工具列走編輯/刪除。
// 「載入更多」是唯一不整包重繪的例外(見 results-appended 訂閱與 appendCards()):
// 只 append 新增的卡片,避免已載入卡片的縮圖被銷毀重建造成重新請求與畫面跳動。
import * as actions from "../actions.js?v=20260820a";
import {emit, on} from "../bus.js?v=20260820a";
import {t} from "../i18n.js?v=20260820a";
import {state} from "../store.js?v=20260820a";
import {fieldDefsFor, templateName} from "../fields.js?v=20260820a";
import {$, esc, isImageFile, tagClass} from "../utils.js?v=20260820a";
import {codePeekHtml, extractImages, firstCodeFence, hilite, mdInline, stripCodeFences} from "../markdown.js?v=20260820a";
import {openLightbox} from "../components/lightbox.js?v=20260820a";
import {bindEditor, editorHTML} from "./editor.js?v=20260820a";
import {openDetail} from "./detail.js?v=20260820a";

// 卡片上程式碼縮圖收合時顯示的行數(超過就出現展開鈕)。
// cardHTML 產生初始狀態、toggleCode 就地重繪都要用同一個值,故拉成常數。
const CODE_PEEK_LINES = 8;

// 卡片圖庫要輪播的圖片,依序是:說明欄內嵌的圖片 + 圖片附件。
// 兩個來源都要:新貼的圖片現在存成附件(見 editor.js 的 paste),但既有筆記
// 說明欄裡直接寫死的 ![](…) 內嵌圖片照樣要顯示,不能因為改了貼上流程就不見。
function cardImages(n) {
  return [
    ...extractImages(n.description),
    ...(n.attachments || [])
      .filter(a => isImageFile(a.name) || isImageFile(a.path))
      .map(a => ({alt: a.description || a.name, src: a.path})),
  ];
}

export function initList() {
  on("results-changed", render);
  on("results-appended", appendCards);
  $("#loadMoreBtn").onclick = async () => {
    const btn = $("#loadMoreBtn");
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = t("list.loadingMore");
    await actions.loadMore();
    btn.disabled = false;
    // hasMore 若已變 false,updateLoadMoreButton() 已經把按鈕藏起來,這行不影響外觀
    btn.textContent = label;
  };
  // 卡片右側的樣板名稱來自 state.allTemplates;boot 時 search 常比 loadTemplates 先
  // 完成(refreshAll 併發跑),故也在 templates-changed 時重繪讓樣板名補上/更新。
  // 編輯/新建中不重繪,避免清掉使用者打到一半的內容(比照既有的重繪約定)。
  on("templates-changed", () => { if (!state.editing && !state.creating) render(); });

  // 瀏覽模式:偏好記在 localStorage(比照主題/字級/複選標籤)。
  // 一顆鈕在 detail ↔ grid 之間互換(預設 detail),不是兩顆各自獨立的按鈕。
  // ⚠ 這裡就要先畫一次:圖示是 JS 寫進去的,不先畫的話按鈕會空白到第一次
  // renderStat()(要等搜尋回來)才長出來。
  const saved = localStorage.getItem("gv-viewmode");
  if (saved === "grid" || saved === "detail") state.viewMode = saved;
  renderViewMode();
  $("#viewModeToggle").onclick = () => {
    state.viewMode = state.viewMode === "grid" ? "detail" : "grid";
    localStorage.setItem("gv-viewmode", state.viewMode);
    render();   // renderStat() 會順帶把按鈕重畫成新模式(見 renderViewMode)
  };

  // 隱藏說明:偏好同樣記在 localStorage(比照瀏覽模式),純靠容器 class 控制顯示。
  // 按鈕本身比照鏤空標籤樣式,靠 .on class 呈現啟用狀態(不是核取方塊)。
  state.hideDesc = localStorage.getItem("gv-hidedesc") === "1";
  $("#hideDescToggle").classList.toggle("on", state.hideDesc);
  $("#hideDescToggle").onclick = () => {
    state.hideDesc = !state.hideDesc;
    $("#hideDescToggle").classList.toggle("on", state.hideDesc);
    localStorage.setItem("gv-hidedesc", state.hideDesc ? "1" : "0");
    render();
  };

  // 隱藏圖片:偏好同樣記在 localStorage,同樣純靠容器 class 控制顯示。
  // ⚠ 這裡刻意**不呼叫 render()**(上面的隱藏說明有呼叫):render() 會 innerHTML
  // 整包重建,把每一張 <img> 銷毀後重新請求一遍——而那正是這顆按鈕要省掉的東西
  // (同 results-appended 之所以是 append-only 的理由)。只翻容器 class 就夠了,
  // 順帶保住圖庫 idx、code-peek 展開狀態與正在編輯的內容。
  state.hideImg = localStorage.getItem("gv-hideimg") === "1";
  $("#hideImgToggle").classList.toggle("on", state.hideImg);
  $("#hideImgToggle").onclick = () => {
    state.hideImg = !state.hideImg;
    $("#hideImgToggle").classList.toggle("on", state.hideImg);
    localStorage.setItem("gv-hideimg", state.hideImg ? "1" : "0");
    applyImgMode();
  };
  applyImgMode();

  // 只顯示有標記:跟瀏覽模式/隱藏說明不同,這是真的篩選條件,要重新打 API——
  // 後端只回前 50 筆(候選 500),純前端過濾會漏掉沒進前 50 名的已標記名詞,
  // 統計數字也會跟著對不上(同下面排序那段的理由)。
  state.markedOnly = localStorage.getItem("gv-markedonly") === "1";
  $("#markedToggle").classList.toggle("on", state.markedOnly);
  $("#markedToggle").onclick = () => {
    state.markedOnly = !state.markedOnly;
    $("#markedToggle").classList.toggle("on", state.markedOnly);
    localStorage.setItem("gv-markedonly", state.markedOnly ? "1" : "0");
    actions.search();
  };

  // 語意檢索開關:也是真的換一支端點,所以要重新打 API。刻意做成明確的開關而
  // 不是自動——搜尋框有 120ms debounce,自動的話等於每次按鍵停頓都打一次嵌入模型。
  state.semantic = localStorage.getItem("gv-semantic") === "1";
  $("#semanticToggle").classList.toggle("on", state.semantic);
  $("#semanticToggle").onclick = () => {
    state.semantic = !state.semantic;
    $("#semanticToggle").classList.toggle("on", state.semantic);
    localStorage.setItem("gv-semantic", state.semantic ? "1" : "0");
    actions.search();
  };
  // AI 未啟用時整顆藏起來:語意搜尋要靠嵌入模型,AI 關著時按下去只會得到
  // 「尚未建立語意索引」的誤導訊息。init 當下 state.aiSettings 還是 null
  // (boot 的 loadAISettings 沒有 await、排在 initList 之後),所以先藏、
  // 事件到了再決定;admin 在設定改 AI 開關也走同一個事件即時反映。
  // localStorage 的 gv-semantic 刻意保留——AI 重新開啟時使用者的偏好還在,
  // 但藏起來的期間 state.semantic 要關掉,不然搜尋還是打語意端點。
  $("#semanticToggle").style.display = "none";
  on("ai-settings-changed", syncSemanticToggle);

  // 排序依據:預設依更新時間,可切換成依建立時間(見 app/search.py 的 sort 參數)。
  // 跟瀏覽模式/隱藏說明不同,這個要重新打 API——目前結果只是伺服器已經依 limit
  // 截斷後的前 N 筆,單純在前端重排序無法還原「真正該排前 N 名」的正確結果。
  // 不做獨立選單,直接嵌在 #statText 句子裡當超連結(見 renderStat),點下去互換。
  const savedSort = localStorage.getItem("gv-sort");
  state.sort = savedSort === "created" ? "created" : "updated";
}

// 語意 icon 顯不顯示,跟著 AI 啟用狀態走(見 initList 裡的說明)。
// 重新啟用時從 localStorage 把使用者上次的偏好接回來。
function syncSemanticToggle() {
  const enabled = !!state.aiSettings?.enabled;
  $("#semanticToggle").style.display = enabled ? "" : "none";
  if (enabled) {
    state.semantic = localStorage.getItem("gv-semantic") === "1";
  } else if (state.semantic) {
    state.semantic = false;   // 藏起來的開關不能還在改變搜尋走哪支端點
  }
  $("#semanticToggle").classList.toggle("on", state.semantic);
}

function render() {
  renderList();
  renderStat();
}

// 「隱藏圖片」模式的套用點。
function applyImgMode() {
  $("#list").classList.toggle("hide-img", state.hideImg);
  // 重新進入隱藏模式時要把逐卡展開全部收回:.img-shown 是上一輪隱藏模式留下的,
  // 不清的話「剛按下隱藏」的當下,上次展開過的那幾張會直接露出圖片。
  if (state.hideImg) {
    document.querySelectorAll(".card.img-shown").forEach(c => c.classList.remove("img-shown"));
  }
}

function renderStat() {
  const n = state.results.length;
  const active = state.q || state.tags.length || state.group || state.dateActive;
  // 排序依據直接嵌在句子裡當超連結:顯示目前的排序依據,點下去換成另一個,
  // 不做獨立選單控制項。內容只有固定字典字串(無使用者輸入),innerHTML 安全。
  const sortLabel = `<button type="button" class="sort-link" id="sortLink">${esc(t(state.sort === "created" ? "sort.created" : "sort.updated"))}</button>`;
  if (state.semantic && state.q.trim()) {
    // 語意模式的句子不一樣:它沒有第二頁(見 app/semantic.py:search_hybrid),
    // 所以不能講「共 N 筆」那種暗示可以翻完的說法。索引還沒建、或落後了,
    // 都要說得出口——空列表會讓人以為庫裡沒東西。
    if (state.semanticFailed) $("#statText").innerHTML = t("stat.semanticFailed");
    else if (state.semanticNeedsIndex) $("#statText").innerHTML = t("stat.semanticNeedsIndex");
    else if (state.semanticUnindexed) $("#statText").innerHTML = t("stat.semanticStale", {n, stale: state.semanticUnindexed});
    else $("#statText").innerHTML = t("stat.semanticHits", {n});
  } else {
    $("#statText").innerHTML = active ? t("stat.hits", {n, sortLabel}) : t("stat.total", {n, sortLabel});
  }
  // 語意模式的句子裡沒有排序連結(那幾句沒有 sortLabel),所以不能無條件抓
  const sortLink = $("#sortLink");
  if (sortLink) sortLink.onclick = () => {
    state.sort = state.sort === "created" ? "updated" : "created";
    localStorage.setItem("gv-sort", state.sort);
    actions.search();
  };
  renderViewMode();
}

// 瀏覽模式切換鈕的外觀。⚠ 圖示顯示的是**目前生效的模式**(detail=☰ / grid=▦),
// 不是「按下去會變成什麼」——理由見 sidebar.js:dateFieldLabel 的註解(寫成目標會讓
// 人以為現在就是那個模式)。title 把模式名稱講出來,因為 icon 本身認不出來的人
// 需要文字;.on 給非預設的 grid,比照日期欄位切換鈕對 created 的處理。
function renderViewMode() {
  const btn = $("#viewModeToggle"); if (!btn) return;
  const grid = state.viewMode === "grid";
  btn.textContent = grid ? "▦" : "☰";
  btn.title = t("viewmode.toggleTitle", {mode: t(grid ? "viewmode.grid" : "viewmode.detail")});
  btn.classList.toggle("on", grid);
}

function cardHTML(n) {
  const wl = "plain";
  const tags = n.tags.map(t => {
    const selected = state.tags.length === 1 && state.tags[0] === t;
    return `<span class="tag ${tagClass(t)} on ${selected ? "selected" : ""}" data-t="${esc(t)}">${esc(t)}</span>`;
  }).join("");
  const fSm = (k, v) => v ? `<div class="field-sm"><span class="k">${esc(k)}</span><div class="v">${mdInline(v, wl)}</div></div>` : "";
  // 樣板欄位泛用渲染:非空欄位兩兩一組;alias 特例內嵌在標題旁,不重複列出
  const defs = fieldDefsFor(n).filter(d => d.key !== "alias" && n.fields[d.key]);
  let fieldRows = "";
  for (let i = 0; i < defs.length; i += 2) {
    fieldRows += `<div class="field-pair">${defs.slice(i, i + 2)
      .map(d => fSm(d.label, n.fields[d.key])).join("")}</div>`;
  }
  // 📎 徽章只算「非圖片」的附件:圖片附件已經以縮圖呈現在右側圖庫,
  // 再數一次只是把同一件事講兩遍(貼圖改存附件後,幾乎每張卡都會多這個徽章)。
  const files = (n.attachments || []).filter(a => !isImageFile(a.name) && !isImageFile(a.path));
  const attachBadge = files.length
    ? `<span class="attach-badge" title="${esc(t("card.attachTitle", {n: files.length}))}">📎 ${files.length}</span>` : "";
  // 別名走 mdInline(不是 hilite):別名欄常寫 [[另一個名詞]],要跟其他欄位一樣
  // 顯示成名詞連結的樣子而不是原始的中括號(卡片上不掛 <a>,卡片本身就是可點的)
  const aliasInline = n.fields.alias ? `<span class="alias-inline">(${mdInline(n.fields.alias, wl)})</span>` : "";
  // 說明欄含程式碼區塊時,卡片上不把整段程式碼塞進被 line-clamp 限制的說明文字裡
  // (會擠掉文字、截斷處也容易斷在字元中間很難看)。改成:文字部分正常顯示,
  // 程式碼另外用有自己行數預算的迷你縮圖呈現(codePeekHtml)。多欄(grid)模式靠
  // CSS 縮短 line-clamp,不需要另外的 JS 分支。
  // 說明欄不渲染「說明」標頭:卡片上只有這一欄是整段文字,標頭沒有辨識價值卻要
  // 吃掉固定的 32px 欄寬+12px 間距(其他欄位的 .k 保留)。
  const fence = firstCodeFence(n.description);
  const descText = fence ? stripCodeFences(n.description) : n.description;
  const descHTML = descText ? `<div class="field desc">
      <div class="v">${mdInline(descText, wl)}</div>
    </div>` : "";
  const codePeek = fence ? codePeekHtml(fence.lang, fence.code, CODE_PEEK_LINES) : "";
  const tplName = esc(templateName(n.template));
  // 多欄(grid)模式把樣板名稱從標題旁移到標籤上方,故另外多渲染一份放在這裡;
  // 兩份靠 CSS 依模式互斥顯示(.card-tpl / .card-tpl-grid),不是重複資訊。
  const tplGrid = `<div class="card-tpl-grid">${tplName}</div>`;
  const tagsFoot = tags ? `<div class="card-foot"><div class="tags">${tags}</div></div>` : "";
  // 圖片(說明欄內嵌 + 圖片附件)獨立呈現成卡片右側的小圖庫,不跟著 mdInline
  // 塞進文字段落(mdInline 本來就會把圖片語法拿掉)。多張圖時疊左右箭頭切換,
  // 索引狀態就近存在 DOM(見 renderList 的事件綁定),不進 store——重繪即重置無妨。
  // 縮圖本身是「開圖片檢視器」的按鈕(卡片其餘部分才是開詳細頁,見 bindCardEvents),
  // 所以要能被鍵盤操作:role+tabindex 讓它進 Tab 順序,Enter/空白鍵在事件層處理。
  // 不包一層 <button>——那會讓 .card-gallery img 的 100%/100% 尺寸規則失去依附的框,
  // 手機出血、方格模式的三處覆寫都得跟著改。
  const images = cardImages(n);
  const galleryHTML = images.length ? `<div class="card-gallery">
      <img src="/${esc(images[0].src.replace(/^\/+/, ""))}" alt="${esc(images[0].alt)}"
        role="button" tabindex="0" title="${esc(t("card.openViewer"))}">
      ${images.length > 1 ? `
        <button type="button" class="gallery-nav prev" aria-label="${esc(t("card.prevImage"))}">‹</button>
        <button type="button" class="gallery-nav next" aria-label="${esc(t("card.nextImage"))}">›</button>
        <span class="gallery-count">1 / ${images.length}</span>` : ""}
    </div>` : "";
  // 關鍵字列(.card-foot)刻意是 .card 的直接子元素、不在 .card-main 裡:留在裡面
  // 就只能靠到文字欄的右緣,離卡片內容區右緣還差一整個縮圖欄(150+16px),看起來
  // 像被縮排。升一層 + CSS 的 flex:0 0 100% 讓它自己佔一整行,右緣才與縮圖齊平。
  // **必須排在 galleryHTML 之後**:桌機詳細模式靠 DOM 順序換行,排在前面會把縮圖
  // 擠到第三行(手機/方格模式另有 order 覆寫決定順序,見 main.css)。
  const toolbar = `<div class="card-toolbar">
      <button class="ctbtn" data-act="edit" title="${esc(t("card.edit"))}" aria-label="${esc(t("card.edit"))}">✎</button>
      <button class="ctbtn danger" data-act="delete" title="${esc(t("card.delete"))}" aria-label="${esc(t("card.delete"))}">🗑</button>
    </div>`;
  // 「隱藏圖片」模式下,標題列補一個計數 chip 取代右側圖庫(平常 display:none)。
  // 全部藏掉會連「這筆有圖」都看不出來,chip 把那個資訊留著,並且是逐卡的展開/收合
  // 逃生口(切換 .img-shown,見 bindCardEvents)。排在 📎 之前 → 視覺順序 🖼 3 📎 1。
  const imgBadge = images.length
    ? `<button type="button" class="img-badge" title="${esc(t("card.toggleImages"))}">🖼 ${images.length}</button>` : "";
  return `<div class="card" data-id="${n.id}" tabindex="0">
    <div class="card-main">
      <div class="top"><h2>${hilite(n.name)}${aliasInline}</h2>${imgBadge}${attachBadge}<span class="card-tpl">${tplName}</span></div>
      ${fieldRows}${descHTML}${codePeek}${tplGrid}
    </div>
    ${galleryHTML}
    ${tagsFoot}
    ${toolbar}
  </div>`;
}

// 「上次渲染到第幾筆」的游標,給 appendCards() 算出載入更多新增了哪些
// (state.results.slice(renderedCount))。純模組層變數,不進 store——
// 每次整包重繪(renderList)都會回頭同步成 state.results.length。
let renderedCount = 0;

// 單張卡片的事件綁定,renderList()(整包重繪)與 appendCards()(載入更多追加)
// 共用同一份,確保新舊卡片行為一致。
function bindCardEvents(el) {
  const source = () => state.results;
  const openThisDetail = () => {
    const n = source().find(x => x.id === el.dataset.id);
    if (n) openDetail(n);
  };
  // 程式碼縮圖的展開/收合:就地重繪那一塊,不開詳細頁。展開狀態存在這個閉包裡,
  // 不進 store——results-changed 會重建整份 DOM,重置回收合狀態無妨(比照圖庫)。
  let codeOpen = false;
  const toggleCode = () => {
    const peek = el.querySelector(".code-peek");
    const n = source().find(x => x.id === el.dataset.id);
    const fence = n && firstCodeFence(n.description);
    if (!peek || !fence) return;
    codeOpen = !codeOpen;
    peek.outerHTML = codePeekHtml(fence.lang, fence.code, CODE_PEEK_LINES, codeOpen);
  };
  // 展開鈕的判斷要排在開詳細頁前面:兩者都掛在同一個卡片點擊上,
  // stopPropagation 對「同一個節點上的另一個 handler」沒有用。
  el.onclick = e => {
    if (e.target.closest(".card-toolbar")) return;
    if (e.target.closest("[data-code-toggle]")) { toggleCode(); return; }
    openThisDetail();
  };
  el.onkeydown = e => {
    if (e.key !== "Enter" || e.target.closest(".card-toolbar")) return;
    if (e.target.closest("[data-code-toggle]")) { toggleCode(); return; }
    openThisDetail();
  };
  const editBtn = el.querySelector('.ctbtn[data-act="edit"]');
  if (editBtn) editBtn.onclick = e => {
    e.stopPropagation();
    // 只呼叫 render():renderList() 結尾已經會 bindEditor()。這裡再補一次會
    // 讓編輯器上所有 addEventListener 的監聽(貼上、工具列、附件…)各掛兩份,
    // 貼上就會插入兩份內容。
    state.editing = el.dataset.id; state.creating = false;
    render();
  };
  const delBtn = el.querySelector('.ctbtn[data-act="delete"]');
  if (delBtn) delBtn.onclick = e => { e.stopPropagation(); actions.removeNote(el.dataset.id); };
  el.querySelectorAll(".tags .tag").forEach(t => t.onclick = e => {
    e.stopPropagation(); actions.filterByTag(t.dataset.t);
  });
  // 標題列的 🖼 N chip:逐卡展開/收合這張卡的圖(只在隱藏圖片模式下看得到,見 CSS)。
  // 純翻自己這張卡的 class,不查 source()、不重繪——展開狀態跟圖庫 idx、code-peek
  // 一樣是就近的暫時狀態,整包重繪時重置無妨。stopPropagation 有效的理由同下方縮圖。
  const imgBadgeEl = el.querySelector(".img-badge");
  if (imgBadgeEl) {
    const toggleImg = e => { e.stopPropagation(); el.classList.toggle("img-shown"); };
    imgBadgeEl.onclick = toggleImg;
    imgBadgeEl.onkeydown = e => {
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault(); toggleImg(e);
    };
  }
  // 圖庫:左右切換(只有多張圖才有箭頭,見 cardHTML)+ 點縮圖開圖片檢視器。
  // 目前索引就近存在閉包裡,不進 state——每次 results-changed 重繪都會重建 DOM,
  // 重置回第一張無妨;檢視器要從「使用者現在看的那一張」接著開,所以兩件事共用它。
  const gallery = el.querySelector(".card-gallery");
  if (gallery) {
    const n = source().find(x => x.id === el.dataset.id);
    const images = n ? cardImages(n) : [];
    const imgEl = gallery.querySelector("img");
    const countEl = gallery.querySelector(".gallery-count");
    let idx = 0;
    const show = i => {
      idx = (i + images.length) % images.length;
      imgEl.src = "/" + images[idx].src.replace(/^\/+/, "");
      imgEl.alt = images[idx].alt;
      countEl.textContent = `${idx + 1} / ${images.length}`;
    };
    const prevBtn = gallery.querySelector(".gallery-nav.prev");
    if (prevBtn) {
      prevBtn.onclick = e => { e.stopPropagation(); show(idx - 1); };
      gallery.querySelector(".gallery-nav.next").onclick = e => { e.stopPropagation(); show(idx + 1); };
    }
    // 點縮圖 = 看圖,點卡片其他地方 = 開詳細頁。stopPropagation 在這裡是有效的:
    // 卡片的 onclick 掛在**祖先**節點上(不是同一個節點,所以跟上面程式碼展開鈕
    // 那個「同節點另一個 handler」的情況不同,不需要靠判斷順序閃避)。
    if (imgEl && images.length) {
      const open = e => { e.stopPropagation(); openLightbox(images, idx, {title: n ? n.name : ""}); };
      imgEl.onclick = open;
      imgEl.onkeydown = e => {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();  // 空白鍵預設會捲動頁面
        open(e);
      };
    }
  }
}

// 「載入更多內容」按鈕:單純依 state.hasMore 決定顯示與否,不在 index.html
// 之外另外拼 HTML(它是靜態 markup,見 index.html)。
function updateLoadMoreButton() {
  $("#loadMoreBtn").style.display = state.hasMore ? "" : "none";
}

// results-appended 的處理:唯一不整包重繪的路徑,只把新增的那批卡片 append 進
// #list,舊卡片的 DOM(尤其縮圖 <img>)原封不動——整包重繪的話舊卡片會被銷毀
// 重建,圖片重新請求、畫面也會跳動。
function appendCards() {
  const newNotes = state.results.slice(renderedCount);
  if (!newNotes.length) return;
  const wrap = document.createElement("div");
  wrap.innerHTML = newNotes.map(n => cardHTML(n)).join("");
  [...wrap.children].forEach(el => { $("#list").appendChild(el); bindCardEvents(el); });
  renderedCount = state.results.length;
  renderStat();
  updateLoadMoreButton();
}

function renderList() {
  const list = $("#list");
  list.classList.toggle("view-grid", state.viewMode === "grid");  // 多欄版面靠容器 class 切換
  list.classList.toggle("hide-desc", state.hideDesc);  // 隱藏說明同樣靠容器 class 切換
  applyImgMode();                                      // 隱藏圖片同上,但容器有兩個
  let html = "";
  if (!state.results.length) {
    html += state.q
      ? `<div class="empty">${t("list.emptySearch", {q: esc(state.q)})}</div>`
      : `<div class="empty">${t("list.emptyAll")}</div>`;
  }
  html += state.results.map(n => cardHTML(n)).join("");
  list.innerHTML = html;
  document.querySelectorAll(".card").forEach(bindCardEvents);
  renderedCount = state.results.length;
  updateLoadMoreButton();
  renderEditorModal();
}

// 編輯中那筆的內容。⚠ 只查 state.results 的話,複習彈窗暫離編輯的卡會找不到,
// 而症狀是**編輯器靜默打不開**(按了沒反應也不報錯)——最難被回報的那種壞法。
// state.editingNote 是最後的來源:抽卡池與列表篩選是兩套範圍,複習抽到的卡
// 本來就不保證出現在任何一份結果裡(見 views/srs.js:editCurrent)。
function editingNoteOf() {
  if (!state.editing) return null;
  return state.results.find(x => x.id === state.editing)
    || (state.editingNote?.id === state.editing ? state.editingNote : null);
}

// 「編輯器上一次渲染時是開著的嗎」。純模組層變數,不進 store——它只服務下面
// 那個「從開變關」的邊緣偵測,沒有第二個讀者。
let editorWasOpen = false;

// 編輯器改成置中的 modal(獨立於 #list 之外的 #editorModal),不再佔用列表版面裡的
// 一格。新建傳 null;編輯中的名詞見 editingNoteOf()。
function renderEditorModal() {
  const modal = $("#editorModal");
  const editingNote = editingNoteOf();
  const show = state.creating || !!editingNote;
  // 從開變關 → 把使用者送回他離開的地方:複習彈窗暫離中的那張卡(srs-resume),
  // 或設定裡健康度檢查那份清單(settings-resume)。盯著「開變關」而不是各條關閉
  // 路徑各補一行:存檔、取消、Esc、點外面、刪除,五條路徑最後都收斂到這裡,漏不掉。
  if (editorWasOpen && !show) {
    if (state.srsPaused) emit("srs-resume");
    if (state.settingsPaused) emit("settings-resume");
  }
  editorWasOpen = show;
  modal.classList.toggle("show", show);
  if (!show) {
    modal.innerHTML = "";
    state.editingNote = null;   // 用完就清,別讓下一次編輯撿到上一筆的殘影
    return;
  }
  modal.innerHTML = editorHTML(state.creating ? null : editingNote);
  bindEditor();
}

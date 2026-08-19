// 圖片檢視器(lightbox):卡片右側縮圖點下去打開的簡易圖片瀏覽器。
// 敘述 + 左右切換 + 縮放 + 關閉——它仍然不是相簿管理,不做旋轉/裁切/下載。
//
// **縮放是後來補的,原本刻意不做**(舊註解寫的是「不做縮放/旋轉/下載」)。
// 改變主意的理由:這個庫裝的是行話,而行話的截圖有大量是**看細節才有意義**的
// 東西——設定畫面、錯誤訊息、電路板絲印、報表欄位。整張縮到視窗裡看不清楚,
// 使用者就得把圖存下來另外開,那一刻筆記就輸給檔案總管了。旋轉與下載沒有這種
// 「不做就用不了」的性質,所以那兩個仍然不做。
//
// 縮放的四個入口做同一件事(scale/tx/ty 三個變數):滾輪、雙擊、+/-/0 鍵、
// 左上角那排按鈕,外加手機的雙指捏合。⚠ 按鈕不能省——手勢不會自己告訴使用者
// 它存在,沒有可見的控制項,這個功能對多數人等於不存在。
//
// 放在 components/ 而不是 views/ 是刻意的:views 之間唯一允許的橫向 import 是
// list → editor, detail(見 CLAUDE.md 的前端模組地圖),而這個東西 list.js 要用、
// 將來詳細頁的附件縮圖也會想用。它跟 select.js 一樣沒有任何狀態依賴——呼叫端注入
// 一個 {src, alt} 陣列與起始索引,元件不知道那些圖是誰的、也不去 store 裡撈。
// 因此共用庫的唯讀卡片可以原樣使用:後端在投影時就把資產 URL 改寫好了,
// 這裡只是「/」+ 去掉開頭斜線,跟 list.js/detail.js 取圖的寫法完全一致。
//
// DOM 是 index.html 裡的 #imageViewer(單例),不自己 new 節點——比照其他彈窗,
// 樣式與 i18n 屬性都留在 HTML/CSS 裡。
import {t} from "../i18n.js?v=20260820a";
import {$} from "../utils.js?v=20260820a";
import {popModalState, pushModalState} from "../nav.js?v=20260820a";

let images = [];
let idx = 0;

// 縮放狀態。純 UI、不進 store(store 只放資料;這跟捲動位置是同一類東西)。
// tx/ty 是**未縮放**座標系裡的位移,因為 transform 寫成 translate(...) scale(...)
// ——順序反過來位移量就會跟著倍率放大,拖曳手感會隨倍率變得愈來愈飄。
const MIN_SCALE = 1, MAX_SCALE = 6, STEP = 1.4, DBLCLICK_SCALE = 2.5;
let scale = 1, tx = 0, ty = 0;

export const isLightboxOpen = () => $("#imageViewer").classList.contains("show");

// list:[{src, alt}](形狀同 list.js:cardImages() 的產出);start:起始索引;
// title:圖片所屬的名詞名稱,顯示在敘述上方(可省略)。
export function openLightbox(list, start = 0, {title = ""} = {}) {
  if (!list || !list.length) return;
  images = list;
  const multi = images.length > 1;
  $("#viewerTitle").textContent = title || "";
  $("#viewerTitle").style.display = title ? "" : "none";
  // 單張圖時左右鈕與計數整組收起來:留著會讓人以為還有下一張,按了卻是同一張。
  $("#viewerPrev").style.display = multi ? "" : "none";
  $("#viewerNext").style.display = multi ? "" : "none";
  $("#viewerCount").style.display = multi ? "" : "none";
  show(start);
  if (!isLightboxOpen()) pushModalState(closeLightbox);
  $("#imageViewer").classList.add("show");
  $("#viewerClose").focus();
}

export function closeLightbox() {
  if (!isLightboxOpen()) return;
  $("#imageViewer").classList.remove("show");
  // 用 removeAttribute 而不是 src="":空字串會被當成相對網址,瀏覽器會回頭再請求
  // 一次目前這一頁。清掉的理由是下次開啟時不要先閃一格上一張圖(舊圖已在快取裡,
  // 新的 src 要等一次 layout 才換上),順帶讓大圖不必一直佔著記憶體。
  $("#viewerImg").removeAttribute("src");
  resetZoom();   // 別讓下次開啟時先套著上一張的倍率與位移閃一格
  images = [];
  popModalState();
}

/* ── 縮放與平移 ────────────────────────────────────────────────────── */

// 把 scale/tx/ty 寫進 DOM。scale 剛好 1 時**整個拿掉 transform** 而不是寫
// scale(1):留著會讓瀏覽器一直把圖片丟在合成層上重繪,大圖在低階手機會鈍掉,
// 而且 transform 會建立新的 containing block,影響某些瀏覽器的圖片抗鋸齒。
function applyTransform() {
  const img = $("#viewerImg");
  img.style.transform = scale === 1 ? "" : `translate(${tx}px, ${ty}px) scale(${scale})`;
  img.classList.toggle("zoomed", scale > 1);
  $("#viewerZoomVal").textContent = `${Math.round(scale * 100)}%`;
  $("#viewerZoomOut").disabled = scale <= MIN_SCALE + 1e-6;
  $("#viewerZoomIn").disabled = scale >= MAX_SCALE - 1e-6;
}

export function resetZoom() {
  scale = 1; tx = 0; ty = 0;
  applyTransform();
}

// 不讓圖片被拖到完全離開畫面(拖丟了就只能靠還原鈕救,而使用者未必找得到它)。
// ⚠ 尺寸要用 offsetWidth/Height(**未套 transform 的版面尺寸**),不能用
// getBoundingClientRect()——那個已經含了目前的縮放,拿它再乘一次 scale 會愈算愈大。
function clampPan() {
  const img = $("#viewerImg");
  const maxX = Math.max(0, (img.offsetWidth * scale - window.innerWidth) / 2);
  const maxY = Math.max(0, (img.offsetHeight * scale - window.innerHeight) / 2);
  tx = Math.min(maxX, Math.max(-maxX, tx));
  ty = Math.min(maxY, Math.max(-maxY, ty));
}

// 縮放到 next,並讓 (px, py) 這個螢幕座標下的那一點**留在原地**。
// 沒有這個的話,滾輪縮放永遠以圖片中心為準,使用者想放大的右下角會愈縮愈遠。
//
// 推導:螢幕位移 = t + scale × (該點在未縮放座標系裡離圖片中心的距離 p),
// 令縮放前後同一個 p 對應到同一個螢幕位移 c,即得 t' = c − next × (c − t) / scale。
function zoomAt(next, px, py) {
  next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, next));
  if (Math.abs(next - scale) < 1e-6) return;
  const img = $("#viewerImg");
  const r = img.getBoundingClientRect();
  // 未變形時的圖片中心:目前這個(已變形的)矩形中心扣掉目前的位移
  const cx = px - (r.left + r.width / 2 - tx);
  const cy = py - (r.top + r.height / 2 - ty);
  tx = cx - next * (cx - tx) / scale;
  ty = cy - next * (cy - ty) / scale;
  scale = next;
  if (scale === MIN_SCALE) { tx = 0; ty = 0; } else { clampPan(); }
  applyTransform();
}

// 按鈕與鍵盤沒有游標位置可用,一律以視窗中心為錨點
function zoomByStep(factor) {
  zoomAt(scale * factor, window.innerWidth / 2, window.innerHeight / 2);
}

function show(i) {
  idx = (i + images.length) % images.length;
  const im = images[idx];
  const img = $("#viewerImg");
  img.src = "/" + String(im.src || "").replace(/^\/+/, "");
  img.alt = im.alt || "";
  // 換圖一定要還原倍率:上一張放大到 400% 的位移套在新圖上,多半是一片空白,
  // 使用者會以為圖破了。
  resetZoom();
  // 敘述來源就是 alt:圖片附件是「附件敘述或檔名」、說明欄內嵌圖片是 markdown 的
  // alt(見 list.js:cardImages())。沒有敘述時整行收掉,不留一條空白。
  $("#viewerDesc").textContent = im.alt || "";
  $("#viewerDesc").style.display = im.alt ? "" : "none";
  $("#viewerCount").textContent = `${idx + 1} / ${images.length}`;
}

export function initLightbox() {
  const overlay = $("#imageViewer");
  const img = $("#viewerImg");
  $("#viewerClose").onclick = closeLightbox;
  $("#viewerPrev").onclick = e => { e.stopPropagation(); show(idx - 1); };
  $("#viewerNext").onclick = e => { e.stopPropagation(); show(idx + 1); };
  $("#viewerZoomIn").onclick = e => { e.stopPropagation(); zoomByStep(STEP); };
  $("#viewerZoomOut").onclick = e => { e.stopPropagation(); zoomByStep(1 / STEP); };
  $("#viewerZoomVal").onclick = e => { e.stopPropagation(); resetZoom(); };
  // 點背景關閉;點到圖片本身不關(常見的誤觸來源是想看圖細節而點在圖上)。
  // ⚠ 縮放控制列在 .viewer-stage 之外,少了這一關,按 ＋ 會順手把檢視器關掉。
  // 拖曳平移放開時也要擋:那一下的 mouseup 會冒泡成 click,圖片被拖出畫面之外時
  // 放開的位置就落在背景上,一拖就關。
  overlay.onclick = e => {
    if (dragMoved) { dragMoved = false; return; }
    if (!e.target.closest(".viewer-stage") && !e.target.closest(".viewer-zoom")) closeLightbox();
  };

  // 滾輪縮放,以游標位置為錨點。passive:false 才擋得掉頁面捲動。
  overlay.addEventListener("wheel", e => {
    if (!isLightboxOpen()) return;
    e.preventDefault();
    zoomAt(scale * (e.deltaY < 0 ? STEP : 1 / STEP), e.clientX, e.clientY);
  }, {passive: false});

  // 雙擊在 1x 與 DBLCLICK_SCALE 之間切換(以游標為錨點)——看圖最快的那個手勢。
  img.addEventListener("dblclick", e => {
    e.preventDefault(); e.stopPropagation();
    if (scale > MIN_SCALE) resetZoom();
    else zoomAt(DBLCLICK_SCALE, e.clientX, e.clientY);
  });

  // 放大後用滑鼠拖曳平移。⚠ 監聽掛在 window 上而不是圖片上:拖到圖片邊界外面
  // 才放開的話,掛在圖片上的 mouseup 收不到,滑鼠已經放開了畫面卻還黏著游標跑。
  let dragging = false, dragX = 0, dragY = 0;
  let dragMoved = false;   // 這一次拖曳有沒有真的移動過(用來擋掉尾隨的 click)
  img.addEventListener("mousedown", e => {
    if (scale <= MIN_SCALE || e.button !== 0) return;
    e.preventDefault();          // 不要觸發瀏覽器原生的「拖曳圖片」
    dragging = true; dragMoved = false;
    dragX = e.clientX - tx; dragY = e.clientY - ty;
  });
  window.addEventListener("mousemove", e => {
    if (!dragging) return;
    tx = e.clientX - dragX; ty = e.clientY - dragY;
    dragMoved = true;
    clampPan();
    applyTransform();
  });
  window.addEventListener("mouseup", () => { dragging = false; });

  // 左右鍵切換 + 縮放快捷鍵。Esc 不在這裡處理——全站的 Esc 疊層順序統一由 app.js
  // 那條鏈決定(檢視器排在最前面,它永遠是最上層的那一個)。
  document.addEventListener("keydown", e => {
    if (!isLightboxOpen()) return;
    // 縮放鍵先判:它跟圖片張數無關(只有一張圖也要能放大)
    if (e.key === "+" || e.key === "=") { e.preventDefault(); zoomByStep(STEP); return; }
    if (e.key === "-" || e.key === "_") { e.preventDefault(); zoomByStep(1 / STEP); return; }
    if (e.key === "0") { e.preventDefault(); resetZoom(); return; }
    if (images.length < 2) return;
    if (e.key === "ArrowLeft") { e.preventDefault(); show(idx - 1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); show(idx + 1); }
  });

  // 觸控:雙指捏合縮放;單指在未放大時左右滑動切換圖片、放大後改成平移。
  // 手機上左右鈕只有 ~36px,滑動才是自然的操作。
  let sx = 0, sy = 0, pinchDist = 0, pinchScale = 1, panning = false;
  const dist = ts => Math.hypot(ts[0].clientX - ts[1].clientX, ts[0].clientY - ts[1].clientY);
  const mid = ts => [(ts[0].clientX + ts[1].clientX) / 2, (ts[0].clientY + ts[1].clientY) / 2];

  overlay.addEventListener("touchstart", e => {
    if (e.touches.length === 2) {
      pinchDist = dist(e.touches); pinchScale = scale; panning = false;
    } else if (e.touches.length === 1) {
      sx = e.touches[0].clientX; sy = e.touches[0].clientY;
      panning = scale > MIN_SCALE;
      dragX = sx - tx; dragY = sy - ty;
    }
  }, {passive: true});

  overlay.addEventListener("touchmove", e => {
    if (e.touches.length === 2 && pinchDist > 0) {
      e.preventDefault();
      const [mx, my] = mid(e.touches);
      zoomAt(pinchScale * (dist(e.touches) / pinchDist), mx, my);
    } else if (panning && e.touches.length === 1) {
      e.preventDefault();   // 放大後單指是平移,不能讓它同時捲動底下的頁面
      tx = e.touches[0].clientX - dragX; ty = e.touches[0].clientY - dragY;
      clampPan();
      applyTransform();
    }
  }, {passive: false});

  overlay.addEventListener("touchend", e => {
    if (e.touches.length === 0) pinchDist = 0;
    // 放大狀態下的滑動是平移,不是換圖——否則想看圖右半邊會直接跳到下一張
    if (panning || scale > MIN_SCALE) { panning = false; return; }
    if (images.length < 2) return;
    const dx = e.changedTouches[0].clientX - sx, dy = e.changedTouches[0].clientY - sy;
    if (Math.abs(dx) < 40 || Math.abs(dx) < Math.abs(dy)) return;
    show(dx < 0 ? idx + 1 : idx - 1);
  }, {passive: true});

  // 圖片載不出來(附件被刪、路徑改過)時要看得出是壞掉,不是白畫面一片。
  $("#viewerImg").onerror = () => {
    if (isLightboxOpen()) $("#viewerDesc").textContent = t("viewer.loadFailed");
  };
}

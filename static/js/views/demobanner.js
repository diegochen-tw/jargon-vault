// 範例資料的置頂行:註冊時種了範例(見 app/demo.py)且使用者還沒清掉時,
// 貼在頁面最上方的一列。按下 = 只刪範例名詞,不動使用者自己建的東西。
//
// ⚠ 兩件容易踩到的事:
//
// 1. **它是第二個 sticky top:0 的元素**。`.scanner` 早就佔著 top:0(z-index:5),
//    兩者都貼頂會疊在一起、搜尋列被蓋住。解法是把橫幅的實際高度量出來寫進
//    CSS 變數 --demo-banner-h,`.scanner` 的 top 讀它(見 main.css)。高度會隨
//    視窗寬度換行而變,所以 resize 要重量一次。橫幅收掉時要把變數清成 0px,
//    否則搜尋列會永遠留一條空隙。
// 2. **顯示與否只看 state.demoBanner 一個旗標**,那是伺服器的
//    users.json:demo_seeded 投影過來的(GET /api/auth/me)。不要另外用「清單裡
//    還有沒有 demo- 開頭的名詞」去猜——使用者手動刪掉幾筆範例不代表他想關掉橫幅。
import * as actions from "../actions.js?v=20260820a";
import {t} from "../i18n.js?v=20260820a";
import {state} from "../store.js?v=20260820a";
import {$} from "../utils.js?v=20260820a";

let bound = false;

function setOffset(px) {
  document.documentElement.style.setProperty("--demo-banner-h", `${px}px`);
}

function measure() {
  const box = $("#demoBanner");
  setOffset(box && box.style.display !== "none" ? box.offsetHeight : 0);
}

function hide() {
  const box = $("#demoBanner");
  if (box) box.style.display = "none";
  setOffset(0);
}

async function runDelete() {
  if (!confirm(t("demo.deleteConfirm"))) return;
  const n = await actions.purgeDemo();
  if (n === null) return;          // 失敗:actions 已經 alert 過,橫幅留著讓人重試
  hide();
  alert(t("demo.deleted", {n}));
}

export function renderDemoBanner() {
  const box = $("#demoBanner");
  if (!box) return;
  if (!state.demoBanner) { hide(); return; }

  // 網址由後端給(app/config.py:DEMO_SITE_URL);沒有的話就不放連結,
  // 而不是塞一個壞掉的 href。
  const url = state.demoSiteUrl;
  const site = url
    ? `<a href="${encodeURI(url)}" target="_blank" rel="noopener noreferrer">${t("demo.bannerSite")}</a>`
    : `<span>${t("demo.bannerSite")}</span>`;
  box.innerHTML =
    `<span class="demo-banner-text">${t("demo.bannerText")} ${site}</span>` +
    `<button type="button" class="btn tiny" id="demoBannerDelete">${t("demo.bannerDelete")}</button>`;
  box.style.display = "";

  if (!bound) {
    bound = true;
    // 事件掛在容器上(innerHTML 每次重寫會換掉按鈕節點,綁在按鈕上會失效)
    box.addEventListener("click", e => {
      if (e.target.closest("#demoBannerDelete")) runDelete();
    });
    window.addEventListener("resize", measure);
    // ⚠ 首繪時 web font 還沒下載完,量到的是 fallback 字型的行高——實測比最終
    // 高度少 2px,搜尋列就會被橫幅壓掉那 2px。字型就緒後補量一次。
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(measure);
  }
  measure();
}

export function initDemoBanner() {
  renderDemoBanner();
}

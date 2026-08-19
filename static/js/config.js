// 前端全域常數(唯一來源)。

// 畫重點顏色:{{color:text}} 語法 ↔ <span class="hl-color">,背景上色(不是文字色)。
// 實際顏色在 main.css 的 .hl-yellow/.hl-green/.hl-pink(要隨深/淺主題換 alpha,
// 所以不能用 inline style);這裡的 hex 只是工具列色票圓點的參考色(index.html 的 --sw)。
export const HL_COLORS = {yellow: "#EAB308", green: "#10B981", pink: "#F43F5E"};

// 舊四色制 → 新三色的對應。舊筆記裡的 {{red:…}}/{{orange:…}}/{{blue:…}} 照樣顯示得出
// 顏色,而且 hlToHtml 產出的 class 已經是對應後的新色名——htmlToMd 讀的就是 class,
// 所以那些筆記下次在詳細頁上色存回時就自動寫成新色名。不需要一次性全量改寫,
// 也不會有任何內容消失(比照專案對舊格式一貫的「懶遷移」做法)。
export const HL_LEGACY = {red: "pink", orange: "yellow", blue: "green"};

// SRS 一輪抽幾張。真相在後端(app/srs.py 的 DRAW_SIZE / MIN / MAX,它才是最後夾範圍
// 的那一關),這裡是「使用者選了什麼」的裝置偏好——同介面語言/字級,存 localStorage,
// 因此不會跟著整站備份走(見 CLAUDE.md 備份那段的已知缺口)。
// 三個消費端共用這一份:actions.drawSrs()(抽卡時送出)、settings.js(下拉)、
// sidebar.js(「這是什麼?」說明裡的張數)——散成三份一定漂移。
export const SRS_SIZE_DEFAULT = 10;
export const SRS_SIZES = [5, 10, 15, 20, 30, 50];

// 目前設定的張數。沒設過/壞值一律回預設,絕不回 0——0 會讓每次打開都抽不到卡。
export function srsSize() {
  const n = parseInt(localStorage.getItem("gv-srs-size") || "", 10);
  return SRS_SIZES.includes(n) ? n : SRS_SIZE_DEFAULT;
}

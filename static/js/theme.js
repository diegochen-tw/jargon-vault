// 主題核心:模式(light/dark)× 變體(每個模式各自記住的配色)兩軸。
// app.js(⚙️ 選單的深淺切換)與 views/settings.js(偏好設定的色票)都要套主題,
// 而 views 不能 import 組裝根,所以抽在這層(零依賴,層級同 imagecomp.js)。
// 這裡保證 <html> 的 data-variant 永遠屬於當前 data-theme 那個模式——CSS 端
// 因此不會出現「dark 模式掛著 sepia」的組合,main.css 的深色變體選擇器才能
// 安心寫成 [data-theme="dark"][data-variant="…"] 雙屬性。

// bg/text 是設定頁色票的預覽色(「這個選項長什麼樣」的縮圖,刻意寫死、
// 不跟著當前主題變);變體的完整變數組在 main.css 的對應區塊,兩邊要同步改。
export const LIGHT_VARIANTS = [
  {id: "default", bg: "#FBFAF7", text: "#22201C"},  // 微暖紙白(出廠淺色)
  {id: "sepia",   bg: "#F4ECD8", text: "#332C27"},  // 紙張:書頁米黃+深棕黑
  {id: "white",   bg: "#FAFAFA", text: "#1A1A1A"},  // 清爽白:中性灰白高清晰
];
export const DARK_VARIANTS = [
  {id: "default", bg: "#16181C", text: "#E8E9EC"},  // 石墨深灰(出廠深色)
  {id: "black",   bg: "#000000", text: "#C8C8C8"},  // 純黑:OLED 省電,文字降亮度防光暈
  {id: "navy",    bg: "#0F172A", text: "#E2E8F0"},  // 冷調深藍
];

const variantKey = mode => (mode === "dark" ? "gv-theme-dark" : "gv-theme-light");

export function currentMode() {
  return localStorage.getItem("gv-theme") === "dark" ? "dark" : "light";
}

// 某模式記住的變體;沒存過或存了不認得的值(未來變體改名/移除)一律回 default
export function variantOf(mode) {
  const v = localStorage.getItem(variantKey(mode));
  const list = mode === "dark" ? DARK_VARIANTS : LIGHT_VARIANTS;
  return list.some(x => x.id === v) ? v : "default";
}

export function applyTheme(mode) {
  document.documentElement.setAttribute("data-theme", mode);
  const v = variantOf(mode);
  if (v === "default") document.documentElement.removeAttribute("data-variant");
  else document.documentElement.setAttribute("data-variant", v);
  localStorage.setItem("gv-theme", mode);
}

// 存某模式的變體偏好(default 比照 gv-lang 的哨兵慣例:removeItem 不存字串);
// 改的是當前模式就立即套用,另一個模式的偏好等下次切過去才生效。
export function setVariant(mode, v) {
  if (v === "default") localStorage.removeItem(variantKey(mode));
  else localStorage.setItem(variantKey(mode), v);
  if (mode === currentMode()) applyTheme(mode);
}

/* ── 背景圖片(裝置本地偏好,與主題配色/Logo 文字同一慣例)──────────
   圖存 localStorage 的 dataURL(gv-bgimage),顯示強度存 gv-bgdim(%)。
   簾幕用 color-mix 疊主題底色在圖上——顏色寫的是 var(--bg),切主題/換變體
   時瀏覽器自己重算,所以這裡刻意不掛進 applyTheme()。color-mix 不支援的
   舊瀏覽器會讓整條 backgroundImage 失效 = 功能安靜消失,不影響其他版面。 */

const BG_KEY = "gv-bgimage";
const BG_DIM_KEY = "gv-bgdim";
export const BG_DIM_DEFAULT = 30;   // 預設顯示強度(%)
export const BG_MAX_BYTES = 3 * 1024 * 1024;  // 壓縮後仍超過就拒收(dataURL 會再膨脹 ~1.37 倍)

// 目前的顯示強度(%),夾在 10–100;沒存過或存了壞值回預設
export function bgIntensity() {
  const v = parseInt(localStorage.getItem(BG_DIM_KEY), 10);
  return Number.isFinite(v) ? Math.min(100, Math.max(10, v)) : BG_DIM_DEFAULT;
}

export const hasBackground = () => !!localStorage.getItem(BG_KEY);

export function applyBackground() {
  const url = localStorage.getItem(BG_KEY);
  const s = document.body.style;
  // gv-hasbg 給 CSS 端做「背景圖啟用時」的差異化(目前只有 .scanner 鏤空);
  // 三條路(boot/上傳/移除)都走這支,class 的加與清不會漏。
  document.body.classList.toggle("gv-hasbg", !!url);
  if (!url) {
    s.backgroundImage = s.backgroundSize = s.backgroundPosition = s.backgroundAttachment = "";
    return;
  }
  const veil = `color-mix(in srgb, var(--bg) ${100 - bgIntensity()}%, transparent)`;
  s.backgroundImage = `linear-gradient(${veil}, ${veil}), url("${url}")`;
  s.backgroundSize = "cover";
  s.backgroundPosition = "center";
  s.backgroundAttachment = "fixed";
}

// 存不下(QuotaExceededError)原樣往上拋,由呼叫端提示「圖太大」——
// 這層不碰 alert/i18n(零依賴層)。
export function setBackgroundImage(dataUrl) {
  localStorage.setItem(BG_KEY, dataUrl);
  applyBackground();
}

export function clearBackground() {
  localStorage.removeItem(BG_KEY);
  applyBackground();
}

export function setBackgroundIntensity(v) {
  localStorage.setItem(BG_DIM_KEY, String(v));
  applyBackground();
}

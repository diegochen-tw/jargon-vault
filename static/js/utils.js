// 無依賴的純工具函式。這裡不得 import 其他模組、不得存取全域狀態。
export const $ = s => document.querySelector(s);

export const esc = s => (s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

// 名詞的正規化比對鍵:NFKC(全形→半形)+ 小寫 + 去掉所有空白。
// 這是 app/sanitize.py:norm_key() 的 JS 版,兩邊必須是同一套規則——
// `[[連結]]` 要在渲染當下就知道目標存不存在(才畫得出「尚未建立」的樣式),
// 不可能每個連結打一次 API,所以規則得兩邊各寫一份。**改一邊記得改另一邊。**
export const normKey = s => (s ?? "").normalize("NFKC").toLowerCase().replace(/\s+/g, "");

// 依標籤文字雜湊出固定色票 class(c0~c5),同名標籤永遠同色
export const tagClass = t => {
  let h = 0;
  for (const c of t) h = (h * 31 + c.codePointAt(0)) >>> 0;
  return "c" + (h % 6);
};

export const fmtDate = ts => {
  if (!ts) return "";
  const d = new Date(ts * 1000), p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
};

// 位元組數 → 人看得懂的大小(用於上傳壓縮前後的提示)
export const fmtBytes = n => {
  if (!n && n !== 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
};

// 附件是不是圖片(決定要顯示縮圖還是 📎 檔名):只看副檔名,跟後端存檔時
// 認得的清單一致(app/config.py:IMAGE_EXTS),多帶幾個瀏覽器同樣顯示得出來的格式。
const IMAGE_EXT_RE = /\.(png|jpe?g|gif|webp|bmp|avif|svg)$/i;
export const isImageFile = nameOrPath => IMAGE_EXT_RE.test(nameOrPath || "");

export const newId = () =>
  (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "") : Math.random().toString(16).slice(2) + Date.now().toString(16)).slice(0, 12);

// 「手機條件」= 窄螢幕 或 橫放矮螢幕(手機橫拿:寬過了 640 但高只剩 ~360,
// 桌機版面擺不下)。這是 main.css 同名條件的 JS 版(字級/lightbox/手機版總成
// 三個 @media 用它),跟 normKey 一樣是「兩邊各寫一份的同一把尺」——
// **改一邊記得改另一邊**,否則 CSS 已切手機版、JS 還把工具列定位在桌機位置。
export const NARROW_MQ = "(max-width:640px),((max-height:500px) and (orientation:landscape))";
export const isNarrowScreen = () => window.matchMedia(NARROW_MQ).matches;

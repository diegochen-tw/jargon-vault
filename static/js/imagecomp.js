// 上傳前的圖片壓縮:長邊限縮 + WebP 重新編碼(順帶剝掉 EXIF,含 GPS)。
// 無依賴的純模組,只用瀏覽器 API,不 import 其他模組、不碰 DOM 以外的狀態。
//
// 最高原則:壓縮永遠不能擋住上傳。任何一步失敗(瀏覽器不支援、解碼失敗、
// 編碼回 null)都直接回原檔,呼叫端不必分辨成功與否。

export const MAX_EDGE = 2000;        // 長邊上限(px)
export const QUALITY = 0.82;         // 有損編碼品質
export const SKIP_BYTES = 300 * 1024; // 小於這個大小且尺寸未超標就不動它

// 不該碰的圖片格式:GIF 會被壓成單張(動畫沒了)、SVG 是向量(轉點陣是劣化)、
// AVIF 本來就比 WebP 更省。
const SKIP_TYPES = /^image\/(gif|svg\+xml|avif)$/i;

// WebP 編碼支援偵測只跑一次,結果快取在模組變數(null = 還沒測)
let webpOK = null;
function supportsWebp() {
  if (webpOK === null) {
    try {
      const c = document.createElement("canvas");
      c.width = c.height = 1;
      webpOK = c.toDataURL("image/webp").startsWith("data:image/webp");
    } catch { webpOK = false; }
  }
  return webpOK;
}

// 解碼成可繪製的來源。createImageBitmap 一定要帶 imageOrientation:"from-image",
// 否則 iPhone 直拍照片的 EXIF Orientation 在重繪後會遺失、圖片變成躺著的;
// 舊瀏覽器不支援這個選項時退回 <img>(瀏覽器對 <img> 本來就會套 EXIF 方向)。
async function decode(file) {
  if (typeof createImageBitmap === "function") {
    try {
      return await createImageBitmap(file, {imageOrientation: "from-image"});
    } catch { /* 落到 <img> 那條路 */ }
  }
  const url = URL.createObjectURL(file);
  try {
    return await new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = reject;
      img.src = url;
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

function encode(canvas, type, quality) {
  if (canvas.convertToBlob) return canvas.convertToBlob({type, quality});
  return new Promise(resolve => canvas.toBlob(resolve, type, quality));
}

const baseName = name => (name || "image").replace(/\.[^.]+$/, "");

/**
 * 壓縮一張圖片,回傳 {file, before, after, changed}。
 * changed=false 代表原檔照舊(格式不適合、圖太小、或壓完反而更大)。
 */
export async function compressImage(file) {
  const keep = {file, before: file?.size || 0, after: file?.size || 0, changed: false};
  if (!file || !file.type?.startsWith("image/") || SKIP_TYPES.test(file.type)) return keep;

  let src = null;
  try {
    src = await decode(file);
    const w = src.width || src.naturalWidth, h = src.height || src.naturalHeight;
    if (!w || !h) return keep;

    const scale = Math.min(1, MAX_EDGE / Math.max(w, h));
    // 尺寸沒超標的小圖再壓省不到什麼,還可能變大——直接原檔上傳
    if (scale === 1 && file.size < SKIP_BYTES) return keep;

    // 不支援 WebP 的極舊瀏覽器只壓 JPEG 來源:JPEG 沒有 alpha,
    // 把 PNG 轉過去會讓透明底變成黑塊。
    const webp = supportsWebp();
    if (!webp && file.type !== "image/jpeg") return keep;
    const type = webp ? "image/webp" : "image/jpeg";
    const ext = webp ? ".webp" : ".jpg";

    const dw = Math.max(1, Math.round(w * scale)), dh = Math.max(1, Math.round(h * scale));
    const canvas = typeof OffscreenCanvas === "function"
      ? new OffscreenCanvas(dw, dh)
      : Object.assign(document.createElement("canvas"), {width: dw, height: dh});
    const ctx = canvas.getContext("2d");
    if (!ctx) return keep;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(src, 0, 0, dw, dh);

    const blob = await encode(canvas, type, QUALITY);
    // 壓完反而更大就不要了(截圖類 PNG 偶爾會這樣),絕不上傳比原本更大的東西
    if (!blob || blob.size >= file.size) return keep;

    const out = new File([blob], baseName(file.name) + ext, {type, lastModified: Date.now()});
    return {file: out, before: file.size, after: out.size, changed: true};
  } catch {
    return keep;
  } finally {
    src?.close?.();
  }
}

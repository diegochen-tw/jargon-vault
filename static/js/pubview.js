/**
 * 公開頁(單筆分享 /s/、公開筆記 /p/)共用的渲染片段。
 *
 * 抽出來的理由與 share.js 直接 import mdFull 相同:第二份複製就會漂移。
 * 後端已把 fields 攤平成 [{label, value}]、資產 URL 也改寫好了,
 * 這裡零樣板邏輯、零 base-url 特判。
 *
 * ⚠ 這個模組只給**公開頁**用(share.js / publish.js):不碰 store、不打 API。
 * 後台的詳細頁(views/detail.js)有自己的一份——那邊的 fields 是原始 dict、
 * 連結要可點(link 模式),兩者形狀不同,不要硬併。
 */
import {mdFull} from "./markdown.js?v=20260820a";
import {t} from "./i18n.js?v=20260820a";
import {esc, isImageFile} from "./utils.js?v=20260820a";

export function attachmentsHTML(list) {
  if (!list || !list.length) return "";
  const blocks = list.map(a => {
    const href = "/" + esc((a.path || "").replace(/^\/+/, ""));
    const isImg = isImageFile(a.name) || isImageFile(a.path);
    const head = isImg
      ? `<a class="attach-thumb" href="${href}" target="_blank" rel="noopener noreferrer">
           <img src="${href}" alt="${esc(a.name)}" title="${esc(a.name)}"></a>`
      : `<a class="attach-item" href="${href}" target="_blank" rel="noopener noreferrer">📎 ${esc(a.name)}</a>`;
    return `<div class="attach-block${isImg ? " is-image" : ""}">${head}
      ${a.description ? `<div class="attach-desc-view">${esc(a.description)}</div>` : ""}
    </div>`;
  }).join("");
  return `<div class="detail-attach"><h3>${esc(t("detail.attachments"))}</h3>${blocks}</div>`;
}

// fields 已由後端攤平成 [{label, value}]:公開頁沒有 state.allTemplates,
// 攤平在後端做則這裡零邏輯,順便也不必把樣板 id 洩漏出去。
export function fieldsHTML(fields) {
  if (!fields || !fields.length) return "";
  let out = "";
  for (let i = 0; i < fields.length; i += 2) {
    out += `<div class="field-pair">${fields.slice(i, i + 2).map(f =>
      `<div class="field-sm"><span class="k">${esc(f.label)}</span>
         <div class="v">${esc(f.value)}</div></div>`).join("")}</div>`;
  }
  return out;
}

// 一筆名詞的公開投影本體(欄位 + 說明 + 附件)。連結一律 flat 模式:
// 公開頁的 state.noteIndex 是空的,不強制 flat 的話每個 [[名詞]] 都會渲染成
// 「尚未建立」的樣式,讀者只會覺得這頁壞掉了。
export function noteBodyHTML(note) {
  return `${fieldsHTML(note.fields)}
    <div class="detail-desc">${mdFull(note.description, "flat")}</div>
    ${attachmentsHTML(note.attachments)}`;
}

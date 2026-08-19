/**
 * 公開分享頁的進入點(/s/<token>)。
 *
 * 這一頁的使用者**沒有帳號**,所以這裡只准打一支端點:GET /api/s/<token>。
 * 不要在這個檔案裡呼叫任何需要登入的 API——那會讓一條公開網址變成跳板。
 *
 * 渲染刻意重用 markdown.js 的 mdFull(而不是另寫一份簡化版):說明欄存的是本專案
 * 自訂的精簡 markdown(圍籬程式碼、{{color:}} 畫重點…),複製一份渲染器就等著兩邊漂移。
 * 連結一律用 flat 模式:state.noteIndex 在這頁是空的,不強制 flat 的話每個
 * [[名詞]] 都會渲染成「尚未建立」的樣式,讀者只會覺得這頁壞掉了。
 */
import {applyI18n, t} from "./i18n.js?v=20260820a";
import {noteBodyHTML} from "./pubview.js?v=20260820a";
import {esc, fmtDate} from "./utils.js?v=20260820a";

const $ = s => document.querySelector(s);

// /s/<token> → token。用 pathname 而不是 query string:網址要看起來像一份文件。
function tokenFromPath() {
  const m = location.pathname.match(/^\/s\/([^/]+)\/?$/);
  return m ? decodeURIComponent(m[1]) : "";
}

function render(note) {
  $("#shareBody").innerHTML = `<h2>${esc(note.name)}</h2>
    <div class="share-by">${esc(t("sharepage.by", {name: note.owner_label}))}</div>
    ${noteBodyHTML(note)}
    <div class="detail-meta">${esc(t("sharepage.updated", {date: fmtDate(note.updated)}))}</div>`;
  document.title = `${note.name} — Jargon Vault`;
  $("#shareCard").style.display = "";
  $("#shareFoot").style.display = "";
}

async function boot() {
  applyI18n();
  const token = tokenFromPath();
  if (!token) return void ($("#shareMissing").style.display = "");
  let r;
  try {
    r = await fetch(`/api/s/${encodeURIComponent(token)}`);
  } catch {
    return void ($("#shareMissing").style.display = "");
  }
  if (!r.ok) return void ($("#shareMissing").style.display = "");
  render(await r.json());
}

boot();

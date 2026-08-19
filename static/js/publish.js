/**
 * 公開筆記快照頁的進入點(/p/<pid>)。
 *
 * 這一頁的使用者**沒有帳號**,所以這裡只准打一支端點:GET /api/p/<pid>。
 * 不要在這個檔案裡呼叫任何需要登入的 API——那會讓一條公開網址變成跳板。
 *
 * 兩個狀態:列表(全部名詞的名稱)⇄ 單筆(pubview.noteBodyHTML)。
 * 內容在第一次載入就整份在手上(快照本來就是凍結的一包),切換純前端、零請求。
 */
import {applyI18n, t} from "./i18n.js?v=20260820a";
import {noteBodyHTML} from "./pubview.js?v=20260820a";
import {esc, fmtDate} from "./utils.js?v=20260820a";

const $ = s => document.querySelector(s);

let data = null;   // GET /api/p/<pid> 的整包回應
let pid = "";

function pidFromPath() {
  const m = location.pathname.match(/^\/p\/([^/]+)\/?$/);
  return m ? decodeURIComponent(m[1]) : "";
}

function renderHead() {
  const title = data.title || t("pubpage.footerTitle") || "Jargon Vault";
  $("#pubHead").innerHTML = `<h2>${esc(data.title || "Jargon Vault")}</h2>
    <div class="share-by">${esc(t("pubpage.by", {name: data.owner_label}))} ·
      ${esc(t("pubpage.count", {n: data.note_count}))} ·
      ${esc(t("pubpage.updated", {date: fmtDate(data.created)}))}</div>
    <div class="pub-toolbar">
      <a class="btn primary" href="/p/${encodeURIComponent(pid)}/export.zip" download>${esc(t("pubpage.download"))}</a>
    </div>`;
  document.title = `${data.title || "Jargon Vault"} — Jargon Vault`;
}

function showList() {
  $("#pubBody").style.display = "none";
  $("#pubBody").innerHTML = "";
  const list = $("#pubList");
  list.style.display = "";
  list.innerHTML = data.notes.map((n, i) =>
    `<button type="button" class="pub-item" data-i="${i}">${esc(n.name)}</button>`).join("");
  list.querySelectorAll(".pub-item").forEach(b => b.onclick = () => showNote(+b.dataset.i));
}

function showNote(i) {
  const n = data.notes[i];
  if (!n) return;
  $("#pubList").style.display = "none";
  const body = $("#pubBody");
  body.style.display = "";
  body.innerHTML = `<button type="button" class="btn pub-back">${esc(t("pubpage.back"))}</button>
    <h2>${esc(n.name)}</h2>
    ${noteBodyHTML(n)}
    <div class="detail-meta">${esc(t("pubpage.updated", {date: fmtDate(n.updated)}))}</div>`;
  body.querySelector(".pub-back").onclick = showList;
  body.scrollIntoView({block: "start"});
}

async function boot() {
  applyI18n();
  pid = pidFromPath();
  if (!pid) return void ($("#pubMissing").style.display = "");
  let r;
  try {
    r = await fetch(`/api/p/${encodeURIComponent(pid)}`);
  } catch {
    return void ($("#pubMissing").style.display = "");
  }
  if (!r.ok) return void ($("#pubMissing").style.display = "");
  data = await r.json();
  renderHead();
  showList();
  $("#pubCard").style.display = "";
  $("#pubFoot").style.display = "";
}

boot();

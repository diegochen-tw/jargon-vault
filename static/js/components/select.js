// 可搜尋、可新增選項的下拉選單元件(分類單選/標籤複選共用)。
// 無外部狀態依賴:選項與變更都透過 cfg 注入,可在任何表單重複使用。
//
// cfg:
//   multi              true=複選(value 為 array),false=單選(value 為 string)
//   value              初始值
//   options()          回傳目前可選清單(每次開啟重新取得)
//   placeholder        未選時顯示的提示
//   searchPlaceholder  搜尋框提示
//   chipHTML(v)        已選項目的 chip HTML(需含 .x 按鈕供移除)
//   onChange(value)    值變動回呼
//   grid               true=選項排成多欄格狀(桌機 5 欄、手機 3 欄,見 main.css
//                      .selectopts.grid);選項多時(如全站標籤)一列一項要捲很久
// 回傳 {getValue, setValue}
import {t} from "../i18n.js?v=20260820a";
import {esc} from "../utils.js?v=20260820a";

export function mountSelect(root, cfg) {
  let value = cfg.multi ? [...cfg.value] : (cfg.value || "");
  const extra = new Set();  // 本次編輯期間新增、尚未存檔的選項
  let open = false, hl = -1;
  root.innerHTML = `<div class="selectbox" tabindex="0"></div>
    <div class="selectdrop" style="display:none">
      <input class="selectsearch" placeholder="${esc(cfg.searchPlaceholder || t("select.searchPh"))}">
      <div class="selectopts${cfg.grid ? " grid" : ""}"></div>
    </div>`;
  const box = root.querySelector(".selectbox"), drop = root.querySelector(".selectdrop"),
    search = root.querySelector(".selectsearch"), optsEl = root.querySelector(".selectopts");

  const allOptions = () => {
    const base = cfg.options(), have = new Set(base);
    return [...base, ...[...extra].filter(x => !have.has(x))];
  };
  const isSelected = v => cfg.multi ? value.includes(v) : value === v;

  function renderChips() {
    const items = cfg.multi ? value : (value ? [value] : []);
    box.innerHTML = items.length ? items.map(v => cfg.chipHTML(v)).join("") : `<span class="ph">${esc(cfg.placeholder)}</span>`;
    box.querySelectorAll(".x").forEach(btn => btn.onclick = e => { e.stopPropagation(); selectValue(btn.dataset.v); });
  }
  function selectValue(v) {
    if (cfg.multi) value = value.includes(v) ? value.filter(x => x !== v) : [...value, v];
    else value = value === v ? "" : v;
    extra.add(v);
    cfg.onChange(value);
    renderChips();
    if (cfg.multi) { search.value = ""; renderOpts(); search.focus(); }
    else closeDrop();
  }
  function renderOpts() {
    const q = search.value.trim();
    const all = allOptions();
    const filtered = q ? all.filter(v => v.toLowerCase().includes(q.toLowerCase())) : all;
    const exact = all.some(v => v.toLowerCase() === q.toLowerCase());
    let html = filtered.map(v => `<div class="selectopt ${isSelected(v) ? "on" : ""}" data-v="${esc(v)}">
      <span class="chk">✓</span><span>${esc(v)}</span></div>`).join("");
    if (q && !exact) html += `<div class="selectopt create" data-create="${esc(q)}">${t("select.create", {q: esc(q)})}</div>`;
    optsEl.innerHTML = html || `<div class="selectopt empty">${esc(t("select.noMatch"))}</div>`;
    hl = -1;
    optsEl.querySelectorAll(".selectopt[data-v]").forEach(el => el.onclick = () => selectValue(el.dataset.v));
    optsEl.querySelectorAll(".selectopt[data-create]").forEach(el => el.onclick = () => selectValue(el.dataset.create));
  }
  function highlightRow(rows) { rows.forEach((r, i) => r.classList.toggle("hl", i === hl)); rows[hl]?.scrollIntoView({block: "nearest"}); }
  function openDrop() {
    if (open) return; open = true;
    box.classList.add("open"); drop.style.display = "block";
    search.value = ""; renderOpts();
    requestAnimationFrame(() => search.focus());
    document.addEventListener("mousedown", onDocClick, true);
  }
  function closeDrop() {
    if (!open) return; open = false;
    box.classList.remove("open"); drop.style.display = "none";
    document.removeEventListener("mousedown", onDocClick, true);
  }
  function onDocClick(e) { if (!root.contains(e.target)) closeDrop(); }

  box.addEventListener("click", e => { if (!e.target.closest(".x")) (open ? closeDrop() : openDrop()); });
  box.addEventListener("keydown", e => {
    if ((e.key === "Enter" || e.key === " ") && !open) { e.preventDefault(); openDrop(); }
  });
  search.addEventListener("input", renderOpts);
  search.addEventListener("keydown", e => {
    const rows = [...optsEl.querySelectorAll(".selectopt[data-v],.selectopt[data-create]")];
    if (e.key === "ArrowDown" && rows.length) { hl = (hl + 1) % rows.length; highlightRow(rows); e.preventDefault(); }
    else if (e.key === "ArrowUp" && rows.length) { hl = (hl - 1 + rows.length) % rows.length; highlightRow(rows); e.preventDefault(); }
    else if (e.key === "Enter") { e.preventDefault(); (hl >= 0 ? rows[hl] : rows[0])?.click(); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeDrop(); box.focus(); }
    else if (e.key === "Backspace" && !search.value && cfg.multi && value.length) {
      value = value.slice(0, -1); cfg.onChange(value); renderChips();
    }
  });

  // 外部程式化設值(如 AI 生成帶入建議標籤),不觸發 cfg.onChange——呼叫端自己知道值變了
  function setValue(v) {
    value = cfg.multi ? [...v] : (v || "");
    for (const x of (cfg.multi ? value : [value])) if (x) extra.add(x);
    renderChips();
  }

  renderChips();
  return {getValue: () => value, setValue};
}

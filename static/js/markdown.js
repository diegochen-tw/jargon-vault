// 迷你 markdown 引擎:自訂精簡語法 ↔ HTML 的雙向轉換。
//
// 支援語法(儲存格式,存在 .md 檔的 description):
//   **bold**            粗體
//   *italic*            斜體
//   __underline__       底線(非標準 markdown,本專案自訂——雙底線在一般文字裡
//                        極少自然出現,衝突風險低)
//   [text](url)         超連結(url 只信任 http(s)/mailto/相對路徑,見 safeUrl())
//   `code`              行內程式碼
//   ![alt](src)         圖片
//   [[名詞]]            名詞連結(指向庫裡的另一筆名詞,依**名稱**解析不是 id;
//                        目標還沒建立也寫得下去,見 app/links.py 的取捨說明)
//   {{color:text}}      畫重點,背景上色(color: yellow|green|pink;舊筆記的
//                        red/orange/blue 照樣讀得進來,見 config.js:HL_LEGACY)
//   ```lang\n…\n```     圍籬程式碼區塊(保留縮排,等寬字型;lang 選填)
//   | a | b |\n| --- | --- |\n| c | d |   表格(GFM 精簡版:僅左對齊,不支援
//                                          跨欄;儲存格內 | 用 \| 逸出、換行
//                                          存成空白)
//   - item\n- item      項目符號清單(連續行都以 "- " 開頭算一個清單區塊)
//   1. item\n2. item    編號清單(連續行都以 "數字. " 開頭算一個清單區塊;
//                        數字本身不影響顯示順序,渲染一律重新編號)
//   > 引文行            引文區塊(連續行都以 "> " 開頭算一個區塊,渲染成
//                        <blockquote class="mdquote">;引文內的行內語法照常轉換。
//                        「一段話解讀」外掛用它呈現被解讀的原文)
//   \X                  反斜線跳脫:X 以字面呈現、不觸發語法(X ∈ ESCAPABLE:
//                        \ ` * _ [ > - .)。存在的理由是「貼上原文照貼」:數學
//                        式子裡的 2*3*4、[[0,1]]、行首 - 這些字元不是語法,
//                        htmlToMd 存檔時自動補跳脫、渲染時還原,使用者不用知道
//                        它的存在。其餘字元後面的反斜線維持字面(舊筆記的 \n、
//                        \d 這類內容不受影響)
//   \n                  換行(對應 <br>)
//
// 三個轉 HTML 的函式差在用途:
//   mdInline  卡片預覽:不渲染圖片(直接拿掉),程式碼區塊收成小標,帶搜尋關鍵字高亮
//   mdFull    詳細彈窗:完整渲染,帶搜尋關鍵字高亮
//   mdToHtml  編輯器載入:完整渲染,不帶高亮(高亮標記會污染編輯內容)
// 反向(htmlToMd)只有一個:把 contenteditable 的 DOM 轉回儲存格式。
import {HL_LEGACY} from "./config.js?v=20260820a";
import {highlightCode} from "./highlight.js?v=20260820a";
// 本檔多處以 t 當區域參數名,i18n 的 t() 取別名避免遮蔽
import {t as i18nT} from "./i18n.js?v=20260820a";
import {state} from "./store.js?v=20260820a";
import {esc, normKey} from "./utils.js?v=20260820a";

// 圍籬程式碼區塊。程式碼區塊必須跟行內語法/換行轉換完全隔離(區塊內的 \n 不能
// 變成 <br>、內容不能被高亮污染),所以先把字串切成「一般文字段」與「程式碼段」,
// 只對一般文字段做行內轉換,程式碼段獨立渲染成 <pre>。
const CODE_FENCE_RE = /```([\w+#.-]*)\n?([\s\S]*?)```/g;

// 連結網址白名單:只信任 http(s)/mailto/相對路徑,擋掉 javascript:/data: 等
// 可執行的 scheme——這些字面上就存在使用者自己的 markdown 內容裡(貼上/匯入/
// AI 生成都可能夾帶),渲染成 <a href> 或編輯器插入連結前都先過這關。
// 編輯器(editor.js:insertLink)跟渲染(下方 mdInline/mdFull/mdToHtml)共用同一份判斷。
export function safeUrl(u) {
  const s = (u || "").trim();
  if (!s || /^(javascript|data|vbscript):/i.test(s)) return null;
  return s;
}

function codeBlockHtml(lang, code) {
  const l = (lang || "").trim();
  return `<pre class="cb"${l ? ` data-lang="${esc(l)}"` : ""}><code>${esc(code.replace(/\n$/, ""))}</code></pre>`;
}

// 詳細頁用的「完整版」程式碼區塊:語言標籤 + 複製按鈕 + 依語言上色。
// 刻意跟上面的 codeBlockHtml 分開——編輯器(mdToHtml)一定要用 codeBlockHtml
// 那個乾淨版本,因為 htmlToMd 讀回 contenteditable 內容時只認
// `<pre><code>純文字</code></pre>` 這個形狀,複製按鈕/語言標籤這些額外的
// DOM 一旦被讀進 contenteditable,htmlToMd 不知道怎麼跳過它們,存檔會把
// 這些 UI 文字也當成內容存進去。
function richCodeBlockHtml(lang, code) {
  const l = (lang || "").trim();
  const body = code.replace(/\n$/, "");
  const label = l ? esc(l) : "text";
  const langAttr = l ? ` data-lang="${esc(l)}"` : "";
  const gutter = body.split("\n").map((_, i) => i + 1).join("\n");
  // data-lang 刻意同時放在外層 wrap 跟 <pre> 上:<pre> 那份是給 htmlToMd
  // 讀回語言用(既有的 PRE 分支邏輯不用改),wrap 那份純粹給 CSS 用。
  // 行號跟 codePeekHtml 一樣獨立成一個 <pre>(不是插在每行前面):框選程式碼
  // 複製時不會連行號一起選走,兩欄的 padding/line-height 必須跟 pre.cb 一致
  // 才不會逐行錯開(見 main.css .cb-ln)。
  return `<div class="cb-wrap"${langAttr}>
    <div class="cb-head"><span class="cb-lang">${label}</span>
      <button type="button" class="cb-copy" data-copy-code title="${esc(i18nT("md.copyCodeTitle"))}">${esc(i18nT("md.copy"))}</button></div>
    <div class="cb-body">
      <pre class="cb-ln" aria-hidden="true">${gutter}</pre>
      <pre class="cb"${langAttr}><code>${highlightCode(body, l)}</code></pre>
    </div>
  </div>`;
}

// 卡片預覽用:抓說明欄裡「第一個」圍籬程式碼區塊(卡片只給一眼印象,
// 不需要展示全部區塊),回傳 {lang, code},沒有就回傳 null。
export function firstCodeFence(s) {
  CODE_FENCE_RE.lastIndex = 0;
  const m = CODE_FENCE_RE.exec(s);
  return m ? {lang: m[1], code: m[2].replace(/\n$/, "")} : null;
}

// 卡片預覽用:把說明欄裡的圍籬程式碼區塊整段拿掉,只留文字段落——
// 程式碼改用 codePeekHtml() 獨立呈現(有自己的行數預算),不跟文字擠在
// 同一個被 line-clamp 限制行數的容器裡搶版面。
export function stripCodeFences(s) {
  CODE_FENCE_RE.lastIndex = 0;
  return s.replace(CODE_FENCE_RE, "").trim();
}

// 卡片預覽用的「迷你程式碼縮圖」:依語言上色並帶行號欄,預設只取前 maxLines 行,
// 超過的部分用漸層淡出收尾(不是生硬地切在某個字元中間)並附一個展開鈕——
// expanded=true 就在卡片上直接攤開完整原始碼,不必開詳細頁。
//
// 行號刻意獨立成一個 <pre>(不是在每行前面塞字):跟原始碼分開才複製得乾淨,
// 使用者框選程式碼時不會連行號一起選走。
export function codePeekHtml(lang, code, maxLines = 8, expanded = false) {
  const lines = code.replace(/\n$/, "").split("\n");
  const shown = expanded ? lines : lines.slice(0, maxLines);
  const more = lines.length - shown.length;
  const l = (lang || "").trim();
  const gutter = shown.map((_, i) => i + 1).join("\n");
  const toggle = lines.length > maxLines
    ? `<button type="button" class="code-peek-toggle" data-code-toggle>${
        esc(expanded ? i18nT("md.collapseCode") : i18nT("md.expandCode", {n: lines.length}))}</button>`
    : "";
  return `<div class="code-peek${expanded ? " is-open" : ""}"${l ? ` data-lang="${esc(l)}"` : ""}>
    <div class="code-peek-body">
      <pre class="code-peek-ln" aria-hidden="true">${gutter}</pre>
      <pre class="code-peek-src"><code>${highlightCode(shown.join("\n"), l)}</code></pre>
      ${more > 0 ? `<div class="code-peek-fade"><span class="code-peek-more">${esc(i18nT("md.moreLines", {n: more}))}</span></div>` : ""}
    </div>
    ${toggle}
  </div>`;
}

// ── 表格 ──
// 偵測邏輯比照 GFM:連續 ≥2 行都含 "|",且第二行是分隔列(只有 -/:/|/空白)。
// 只支援基本格線(無跨欄/對齊),夠應付「貼上表格」的需求就好,不做完整 GFM。
const TABLE_SEP_CELL_RE = /^:?-{2,}:?$/;

// 一列拆成儲存格:去頭尾的 "|",用「非逸出的 |」切開,\| 還原成 |。
function splitTableRow(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map(c => c.trim().replace(/\\\|/g, "|"));
}

function isTableSepRow(line) {
  if (!line.includes("|") && !line.includes("-")) return false;
  const cells = splitTableRow(line);
  return cells.length > 0 && cells.every(c => TABLE_SEP_CELL_RE.test(c));
}

// 清單項目一行的判斷:項目符號只認 "- "(不認 "* ",避免跟行內斜體語法的單星號
// 混淆);編號清單認 "數字. ",數字本身不影響渲染順序(一律依項目順序重新編號)。
const BULLET_ITEM_RE = /^-\s+(.+)$/;
const NUM_ITEM_RE = /^\d+\.\s+(.+)$/;

// s 依表格/清單/引文切段:回傳 [{type:"text", value} | {type:"table", rows} |
// {type:"list", ordered, items} | {type:"quote", value}],table 的 rows[0] 是表頭、
// 其餘是資料列(都已拆成儲存格陣列);list 的 items 是每行去掉標記後的原文;
// quote 的 value 是每行去掉 "> " 前綴後以 \n 接回的原文。
// 連續且同一種標記(全項目符號或全編號)的行合併成一個清單區塊,換了標記種類
// 就斷開算另一個區塊——這樣兩種清單相鄰時渲染出來仍是兩個獨立的 <ul>/<ol>。
const QUOTE_LINE_RE = /^>\s?(.*)$/;

function extractBlocks(s) {
  const lines = s.split("\n");
  const out = [];
  let buf = [];
  const flush = () => { if (buf.length) { out.push({type: "text", value: buf.join("\n")}); buf = []; } };
  for (let i = 0; i < lines.length; i++) {
    const quoteM = QUOTE_LINE_RE.exec(lines[i]);
    if (quoteM) {
      flush();
      const q = [];
      let j = i, m;
      while (j < lines.length && (m = QUOTE_LINE_RE.exec(lines[j]))) { q.push(m[1]); j++; }
      out.push({type: "quote", value: q.join("\n")});
      i = j - 1;
      continue;
    }
    if (lines[i].includes("|") && i + 1 < lines.length && isTableSepRow(lines[i + 1])) {
      flush();
      const rows = [splitTableRow(lines[i])];
      let j = i + 2;
      while (j < lines.length && lines[j].trim() !== "" && lines[j].includes("|")) {
        rows.push(splitTableRow(lines[j]));
        j++;
      }
      out.push({type: "table", rows});
      i = j - 1;
      continue;
    }
    const bulletM = BULLET_ITEM_RE.exec(lines[i]);
    const numM = !bulletM ? NUM_ITEM_RE.exec(lines[i]) : null;
    if (bulletM || numM) {
      flush();
      const ordered = !!numM;
      const re = ordered ? NUM_ITEM_RE : BULLET_ITEM_RE;
      const items = [];
      let j = i, m;
      while (j < lines.length && (m = re.exec(lines[j]))) { items.push(m[1]); j++; }
      out.push({type: "list", ordered, items});
      i = j - 1;
      continue;
    }
    buf.push(lines[i]);
  }
  flush();
  return out;
}

// 表格儲存格/清單項目共用的行內轉換:跟一般文字共用 bold/italic/underline/
// code/link,useHilite 決定要不要疊搜尋高亮(mdFull 要、mdToHtml 不要——邏輯
// 對齊 mdFull/mdToHtml 對一般文字的既有處理)。plainLink 為 true 時連結只留
// 顯示文字、不輸出 <a>(卡片預覽用,比照圖片被整個拿掉的處理)。
function inlineCell(s, useHilite, plainLink = false) {
  const t = protectEscapes(s);   // \X 先佔位,收尾 restore(見跳脫那段的說明)
  // plainLink 同時決定名詞連結要不要掛 <a>(卡片預覽裡兩種連結的處理一致)
  const base = wikiToHtml(useHilite ? hlToHtml(hilite(t)) : esc(t), plainLink);
  const linked = plainLink
    ? base.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    : base.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, text, url) => {
        const safe = safeUrl(url);
        return safe ? `<a href="${esc(safe)}" target="_blank" rel="noopener noreferrer">${text}</a>` : text;
      });
  return restoreEscapes(linked
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\*([^*]+)\*/g, "<i>$1</i>")
    .replace(/__([^_]+)__/g, "<u>$1</u>"));
}

// 卡片預覽用:清單項目退化成一行文字,前面加項目符號/編號,用 <br> 接續——
// 卡片是被 line-clamp 限制行數的 inline 排版,不適合塞真正的 <ul>/<ol>。
function listInlineHtml(ordered, items, cellFn) {
  return items.map((it, i) => `${ordered ? `${i + 1}. ` : "• "}${cellFn(it)}`).join("<br>");
}

// 詳細頁/編輯器用:渲染成真正的 <ul>/<ol><li>。
function listBlockHtml(ordered, items, cellFn) {
  const tag = ordered ? "ol" : "ul";
  return `<${tag} class="mdlist">${items.map(it => `<li>${cellFn(it)}</li>`).join("")}</${tag}>`;
}

function tableToHtml(rows, cellFn) {
  const [head, ...body] = rows;
  const theadHtml = `<tr>${head.map(c => `<th>${cellFn(c)}</th>`).join("")}</tr>`;
  const tbodyHtml = body.map(r => `<tr>${r.map(c => `<td>${cellFn(c)}</td>`).join("")}</tr>`).join("");
  // 外層包一層可橫向捲動的容器,避免欄位多的表格把整頁擠出水平捲軸
  return `<div class="mdtable-wrap"><table class="mdtable"><thead>${theadHtml}</thead>${
    tbodyHtml ? `<tbody>${tbodyHtml}</tbody>` : ""}</table></div>`;
}

// 卡片預覽用:表格不展開(容器有限高度撐不下),收成跟程式碼縮圖一樣的小標。
const tableChipHtml = () => `<code class="cb-chip">${esc(i18nT("md.tableChip"))}</code>`;

// rows(二維字串陣列,rows[0] 是表頭)組回儲存格式的表格語法。
function rowsToTableMd(rows) {
  if (!rows.length) return null;
  const colCount = Math.max(...rows.map(r => r.length));
  if (colCount < 1) return null;
  const pad = r => { const p = r.slice(); while (p.length < colCount) p.push(""); return p; };
  const padded = rows.map(pad);
  let out = "| " + padded[0].join(" | ") + " |\n";
  out += "| " + Array(colCount).fill("---").join(" | ") + " |\n";
  for (let i = 1; i < padded.length; i++) out += "| " + padded[i].join(" | ") + " |\n";
  return out.replace(/\n$/, "");
}

// 從外部來源(Excel/Google Sheets/Word/Notion 等)貼上時,剪貼簿的 text/html
// 通常帶一個 <table>——不是本編輯器自己產生的乾淨 HTML,不能假設形狀,只信任
// 每格的純文字內容(不保留字型/顏色等樣式),轉成本檔的表格語法。
export function htmlTableToMd(html) {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const table = doc.querySelector("table");
  if (!table) return null;
  const rows = [...table.querySelectorAll("tr")]
    .map(tr => [...tr.querySelectorAll("th,td")]
      .map(c => c.textContent.replace(/\s+/g, " ").trim().replace(/\|/g, "\\|")))
    .filter(r => r.length);
  return rowsToTableMd(rows);
}

// 有些來源(或系統剪貼簿限制)貼上時只有 text/plain,但仍是 Excel/Sheets 常見的
// Tab 分隔格式。判斷條件刻意收得很緊,因為誤判的代價很不對稱:一般文字被當成
// 表格會整段被拆散重組,救不回來。要全部成立才算表格——
//   1. 至少兩行,每行都有 Tab
//   2. 沒有任何一行以空白/Tab 開頭(以縮排開頭的是程式碼,不是表格。Tab 縮排的
//      程式碼每行都含 Tab,舊的判斷會把它整段變成一張爛表格)
//   3. 每一行切出來的欄數一致(表格是矩形的,程式碼不是)
export function tsvToMd(text) {
  const lines = text.replace(/\r\n?/g, "\n").split("\n").filter(l => l.trim() !== "");
  if (lines.length < 2 || !lines.every(l => l.includes("\t"))) return null;
  if (lines.some(l => /^[ \t]/.test(l))) return null;
  const rows = lines.map(l => l.split("\t").map(c => c.trim().replace(/\|/g, "\\|")));
  if (rows[0].length < 2 || rows.some(r => r.length !== rows[0].length)) return null;
  return rowsToTableMd(rows);
}

// 貼上的純文字看起來是不是程式碼:多行、而且有行以縮排開頭。
// 一般文章不會這樣縮排,誤判率低;就算真的誤判,結果也只是多包一層程式碼區塊
// (內容原封不動、使用者刪掉圍籬即可),不像被當成表格那樣把內容拆散。
export function looksLikeCode(text) {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  if (lines.length < 2) return false;
  return lines.some(l => /^\t+\S/.test(l) || /^ {2,}\S/.test(l));
}

// 剪貼簿 HTML 裡的語言提示:多數程式碼區塊(highlight.js/Prism/GitHub/VS Code)
// 都會在 class 上留 language-xxx / lang-xxx。撈不到就回空字串(退回不上色)。
export function langFromHtml(html) {
  const m = /class="[^"]*\b(?:language|lang|highlight|brush:)[-\s]([\w+#.]+)/i.exec(html || "");
  return m ? m[1] : "";
}

// s 依程式碼圍籬切段:一般文字段套用 textFn(行內轉換),程式碼段套用 blockFn;
// 每個一般文字段再依表格/清單切段,表格段改套用 tableFn、清單段改套用 listFn
// (未傳 tableFn 就不偵測表格/清單,保留給其餘還沒跟這兩者搭配過的呼叫端)。
function withCodeBlocks(s, textFn, blockFn = codeBlockHtml, tableFn = null, listFn = null) {
  // 引文區塊直接重用 textFn:引文內容就是一般文字(行內語法、[[連結]]、\n→<br>
  // 都照常),只是外面多包一層 <blockquote>。三個渲染器因此不用各傳一個 quoteFn。
  const renderText = tableFn
    ? seg => extractBlocks(seg).map(part =>
        part.type === "table" ? tableFn(part.rows)
        : part.type === "list" ? listFn(part.ordered, part.items)
        : part.type === "quote" ? `<blockquote class="mdquote">${textFn(part.value)}</blockquote>`
        : textFn(part.value)).join("")
    : textFn;
  const out = [];
  let last = 0, m;
  CODE_FENCE_RE.lastIndex = 0;
  while ((m = CODE_FENCE_RE.exec(s)) !== null) {
    out.push(renderText(s.slice(last, m.index)));
    out.push(blockFn(m[1], m[2]));
    last = m.index + m[0].length;
  }
  out.push(renderText(s.slice(last)));
  return out.join("");
}

// 搜尋關鍵字高亮:先用控制字元佔位,esc 之後再換成 <mark>,避免跟使用者文字混淆
export function hilite(s) {
  let h = esc(s);
  for (const t of state.q.trim().split(/\s+/).filter(Boolean)) {
    const re = new RegExp(t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
    h = h.replace(re, m => `\x01${m}\x02`);
  }
  return h.replace(/\x01/g, "<mark>").replace(/\x02/g, "</mark>");
}

// ── 反斜線跳脫(見檔頭語法表)──
// 這一組是「貼上原文照貼」的另一半:editor.js 貼上時插的是字面文字,但儲存格式
// 是 markdown——沒有跳脫的話,`2*3*4` 存檔重開就會被行內語法吃掉(星號消失、
// 3 變斜體)。htmlToMd 存檔時自動補(escapeMdText),渲染時還原(protect/restore),
// 兩把尺共用同一份 ESCAPABLE,**改一邊記得改另一邊**。
//
// 做法:行內轉換前先把 \X 換成私用區(PUA)佔位字元,讓後面所有 regex 都看不到
// 那個字元;全部轉換跑完再還原成 esc() 過的字面字元。區塊層(引文/清單/編號)
// 不需要佔位——`\>`/`\- `/`1\. ` 的行首是 `\` 或點前有 `\`,QUOTE_LINE_RE 那幾條
// regex 天然不命中,行內 pass 再把 `\` 拿掉就好。程式碼圍籬同理:反引號逐一跳脫
// 之後,字面 ``` 的連續三個反引號中間隔著 `\`,CODE_FENCE_RE 不會把它當圍籬。
const ESCAPABLE = "\\`*_[>-.";
const ESCAPE_RE = /\\([\\`*_[>\-.])/g;
const PUA_BASE = 0xE100;
const PUA_RE = /[\uE100-\uE107]/g;

function protectEscapes(s) {
  return s.replace(ESCAPE_RE, (m, c) => String.fromCharCode(PUA_BASE + ESCAPABLE.indexOf(c)));
}

function restoreEscapes(s) {
  return s.replace(PUA_RE, ch => esc(ESCAPABLE[ch.charCodeAt(0) - PUA_BASE]));
}

// htmlToMd 的文字節點跳脫(行內那半;行首的區塊語法在 TEXT_NODE 分支另外處理,
// 那裡才知道自己是不是在一行的開頭)。反斜線自己要先跳——不跳的話,使用者字面
// 的 `\*` 存回再渲染會變成 `*`,少了一個字元。
// `_` 只跳成對的(單一底線不是語法,snake_case 不用弄髒)。
function escapeMdText(s) {
  return s
    .replace(/\\/g, "\\\\")
    .replace(/([`*[])/g, "\\$1")
    .replace(/_(?=_)/g, "\\_");
}

// ── 名詞連結 [[名詞]] ──
// 依**名稱**解析(不是 id),對照表是 state.noteIndex(actions.loadNoteNames 建起來的
// normKey → {id,name} Map)。目標不存在時仍然渲染得出來,只是掛 .missing——那是刻意的:
// 「先寫下 [[之後要補的名詞]]」是這個專案的核心用法,連結不該因為目標還沒建立就消失。
//
// ⚠ 呼叫點的字串「已經被 esc() 過」(甚至可能被 hilite() 插進 <mark>),所以:
//   - 顯示文字直接沿用原捕獲值(保住搜尋高亮)
//   - 屬性值另外把標籤剝掉(<mark> 進 attribute 會壞掉)
//   - 查表前要把實體還原回去,不然 &amp; 這種字算出來的 normKey 對不上
const WIKI_RE = /\[\[([^\[\]\r\n]{1,120})\]\]/g;
const stripTags = s => s.replace(/<[^>]*>/g, "");
const unesc = s => s.replace(/&lt;/g, "<").replace(/&gt;/g, ">")
  .replace(/&quot;/g, '"').replace(/&amp;/g, "&");

// 名稱 → 庫裡的名詞({id, name});查不到回 null。
// 對照表由 actions.loadNoteNames 建(normKey → {id, name})。
export const lookupNote = (name) => state.noteIndex?.get(normKey(name)) || null;

// 三種模式:
//   "link"  (預設)完整可點的連結,目標不存在時掛 .missing
//   "plain" 卡片預覽用:只留樣式不掛 <a>(卡片本身就是可點的,巢狀連結會打架),
//           比照圖片在 mdInline 被整個拿掉、一般連結被降級成純文字的既有處理
//   "flat"  公開分享/公開筆記頁用:**完全不查表**,不可點、不標 missing
//
// ⚠ flat 存在的理由是隔離:wikiToHtml 查的 state.noteIndex 是**我自己的**名稱表。
// 拿它去渲染別人的內容,兩種結果都不能接受——我剛好有同名的名詞就渲染成可點連結,
// 點下去跳到我自己的那筆(把兩個人的知識網默默接在一起,但 A 講的 MES 未必是我的);
// 我沒有就渲染成「尚未建立」並引導我去建自己的筆記(在瀏覽別人的內容時更莫名其妙)。
// 連 plain 都不夠:它仍然會查表決定要不要掛 .missing,等於把「我有沒有這個名詞」
// 洩漏到樣式上。而真正「正確」的跨使用者解析會直接破壞隔離模型,不是這裡該做的事。
function wikiToHtml(s, mode = "link") {
  return s.replace(WIKI_RE, (m, raw) => {
    const label = raw.trim();
    if (!label) return m;
    if (mode === "flat") return `<span class="wikilink flat">${label}</span>`;
    const attrName = stripTags(label);
    const hit = lookupNote(unesc(attrName));
    const cls = `wikilink${hit ? "" : " missing"}`;
    if (mode === "plain") return `<span class="${cls}">${label}</span>`;
    const title = hit ? "" : ` title="${esc(i18nT("wl.missingTitle"))}"`;
    return `<a class="${cls}" data-wl="${attrName}"${hit ? ` data-wl-id="${esc(hit.id)}"` : ""}${title}>${label}</a>`;
  });
}

// 顏色不再寫成 inline style:改成背景螢光筆後,同一個色名在深/淺主題要用不同的
// alpha,只有 CSS class 做得到(見 main.css 的 .hl-yellow/.hl-green/.hl-pink)。
// regex 同時認舊三色與新三色,但吐出來的 class 一律是對應後的新色名——htmlToMd
// 讀的就是 class,舊筆記因此會在下次上色存回時自動遷移。
export const hlToHtml = s => s.replace(/\{\{(red|orange|yellow|green|blue|pink):([\s\S]*?)\}\}/g,
  (m, c, t) => `<span class="hl-${HL_LEGACY[c] || c}">${t}</span>`);

// 單行欄位值(別名/同義詞/多義…)的渲染:這些欄位本來就是拿來寫「跟哪個名詞
// 有關」的,只 esc() 出來就永遠是死字串,所以也讓 [[名詞]] 在這裡變成可點的邊。
// 只認名詞連結,不跑整套行內語法——欄位值是單行短文字,粗體/程式碼那些沒有意義。
export const mdField = (s, mode = "link") => wikiToHtml(esc(s), mode);

// 卡片圖庫用:抓說明欄裡「依序」所有圖片語法的 {alt, src},程式碼區塊先拿掉
// 避免圍籬內文字巧合長得像圖片語法被誤判。
export function extractImages(s) {
  const text = stripCodeFences(s);
  const out = [];
  const re = /!\[([^\]]*)\]\(([^)]+)\)/g;
  let m;
  while ((m = re.exec(text)) !== null) out.push({alt: m[1], src: m[2]});
  return out;
}

export const mdInline = (s, mode = "plain") => withCodeBlocks(s, t => restoreEscapes(wikiToHtml(hlToHtml(hilite(protectEscapes(t))), mode)
  .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
  .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
  .replace(/`([^`]+)`/g, "<code>$1</code>")
  .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
  .replace(/\*([^*]+)\*/g, "<i>$1</i>")
  .replace(/__([^_]+)__/g, "<u>$1</u>")
  .replace(/\n/g, "<br>")),
  lang => `<code class="cb-chip">${lang ? esc(lang) + " " : ""}${esc(i18nT("md.codeChip"))}</code>`,
  tableChipHtml,
  (ordered, items) => listInlineHtml(ordered, items, it => inlineCell(it, true, true)));

export const mdFull = (s, mode = "link") => withCodeBlocks(s, t => restoreEscapes(wikiToHtml(hlToHtml(hilite(protectEscapes(t))), mode)
  .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (m, alt, src) =>
    `<img alt="${esc(alt)}" src="/${esc(src).replace(/^\/+/, "")}">`)
  .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, text, url) => {
    const safe = safeUrl(url);
    return safe ? `<a href="${esc(safe)}" target="_blank" rel="noopener noreferrer">${text}</a>` : text;
  })
  .replace(/`([^`]+)`/g, "<code>$1</code>")
  .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
  .replace(/\*([^*]+)\*/g, "<i>$1</i>")
  .replace(/__([^_]+)__/g, "<u>$1</u>")
  .replace(/\n/g, "<br>")),
  richCodeBlockHtml,
  rows => tableToHtml(rows, c => inlineCell(c, true, false)),
  (ordered, items) => listBlockHtml(ordered, items, it => inlineCell(it, true, false)));

export const mdToHtml = (s) => withCodeBlocks(s, t => restoreEscapes(wikiToHtml(hlToHtml(esc(protectEscapes(t))), "link")
  .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (m, alt, src) =>
    `<img alt="${esc(alt)}" src="/${esc(src).replace(/^\/+/, "")}">`)
  .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, text, url) => {
    const safe = safeUrl(url);
    return safe ? `<a href="${esc(safe)}" target="_blank" rel="noopener noreferrer">${text}</a>` : text;
  })
  .replace(/`([^`]+)`/g, "<code>$1</code>")
  .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
  .replace(/\*([^*]+)\*/g, "<i>$1</i>")
  .replace(/__([^_]+)__/g, "<u>$1</u>")
  .replace(/\n/g, "<br>")),
  codeBlockHtml,
  rows => tableToHtml(rows, c => inlineCell(c, false, false)),
  (ordered, items) => listBlockHtml(ordered, items, it => inlineCell(it, false, false)));

// 從 Notion 等來源複製時,純文字剪貼簿常帶有 \r\n 換行、NBSP(不斷行空格)、
// 零寬字元,以及區塊之間堆疊出的多個空行;這些在編輯器裡不易察覺,
// 但轉成 markdown 存檔後會變成一堆多餘的換行,故貼上前先正規化。
export function cleanPastedText(s) {
  return s
    .replace(/\r\n?/g, "\n")
    .replace(/[​‌‍﻿]/g, "")
    .replace(/ /g, " ")
    .replace(/\n{3,}/g, "\n\n");
}

// 程式碼區塊內容 → 純文字。不能直接用 textContent:在 contenteditable 裡按
// Enter,瀏覽器是插入 <br>(或把行拆成 <div>)來換行的,而這兩者在 textContent
// 裡都不留任何痕跡——用 textContent 讀回來,使用者在區塊裡新打的每一行都會被
// 黏成同一行。所以這裡自己走一遍,把 <br> 與區塊邊界還原成 \n。
function preTextOf(node) {
  let s = "";
  const walk = n => {
    if (n.nodeType === Node.TEXT_NODE) { s += n.textContent; return; }
    if (n.nodeType !== Node.ELEMENT_NODE) return;
    if (n.tagName === "BR") { s += "\n"; return; }
    if (n.tagName === "DIV" || n.tagName === "P") {
      if (s && !s.endsWith("\n")) s += "\n";
      n.childNodes.forEach(walk);
      return;
    }
    n.childNodes.forEach(walk);
  };
  node.childNodes.forEach(walk);
  return s;
}

// contenteditable DOM → 儲存格式。認得的標籤逐一轉換,其餘只取文字內容。
export function htmlToMd(root) {
  let out = "";
  function walk(node) {
    // NBSP(U+00A0)一律折回普通空白:contenteditable 會自己產生它——結尾的空白、
    // 連續兩個空白、以及在行內元素(如剛插入的名詞連結)後面補的游標佔位,
    // 都會變成 NBSP。這個專案把 NBSP 當髒資料(貼上時 cleanPastedText 就是這樣
    // 換掉的),不該由編輯器自己把它寫進 .md。
    // 程式碼區塊不受影響:PRE 分支走 preTextOf() 另外處理,不會落到這裡。
    if (node.nodeType === Node.TEXT_NODE) {
      let s = escapeMdText(node.textContent.replace(/\u00A0/g, " "));
      // 行首的區塊語法跳脫(引文/清單/編號):只有這裡知道「這段文字是不是
      // 接在一個換行後面」——out 以換行收尾(或還是空的)就是行首。
      // 編號那條跳的是點(1\. )不是行首:NUM_ITEM_RE 要的是
      // 「數字緊接著點」,在中間斷開即可。
      if (!out || out.endsWith("\n")) {
        s = s.replace(/^>/, "\\>").replace(/^-(\s)/, "\\-$1").replace(/^(\d+)\.(\s)/, "$1\\.$2");
      }
      out += s;
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const tag = node.tagName;
    if (tag === "BR") { out += "\n"; return; }
    if (tag === "DIV" && node.classList.contains("cb-wrap")) {
      // 詳細頁的「完整版」程式碼區塊(見 richCodeBlockHtml):語言標籤/複製
      // 按鈕只是顯示用 UI,不是內容,只有裡面的 <pre> 才要轉回圍籬語法——
      // 直接跳過 .cb-head,避免「Python複製」這種 UI 文字被誤存進說明欄
      // (畫重點功能會對整個 .detail-desc 容器跑 htmlToMd,見 detail.js)。
      // 選 pre.cb 而不是任意 pre:.cb-body 底下還有一個 .cb-ln 行號欄
      // (也是 <pre>),選錯會把行號當成程式碼內容存進去。
      const pre = node.querySelector("pre.cb");
      if (pre) walk(pre);
      return;
    }
    if (tag === "PRE") {
      // 程式碼區塊:取 <code> 的純文字(保留內部換行),轉回 ```lang 圍籬。
      // 收尾刻意不強制補換行——區塊後面原本有幾個換行,已經完整由後面的
      // <br> 兄弟節點各自貢獻一個 \n 還原,這裡如果再多加一個會重複計算,
      // 每次「畫重點」觸發的重新存檔都會多長出一行空行,越存越長。
      const codeEl = node.querySelector("code");
      const codeText = preTextOf(codeEl || node).replace(/\n$/, "");
      const lang = node.getAttribute("data-lang") || "";
      if (out && !out.endsWith("\n")) out += "\n";
      out += "```" + lang + "\n" + codeText + "\n```";
      return;
    }
    if (tag === "IMG") {
      const src = (node.getAttribute("src") || "").replace(/^\/+/, "");
      out += `![${node.getAttribute("alt") || ""}](${src})`;
      return;
    }
    if (tag === "TABLE") {
      // 每格內容各自借用 out 暫存(walk 透過閉包讀寫外層 out,存完整段落語法
      // 如 bold/code),換行折成空白——pipe 語法一格只能一行,存不了字面換行。
      const rows = [...node.querySelectorAll("tr")].map(tr =>
        [...tr.querySelectorAll("th,td")].map(cell => {
          const saved = out; out = "";
          cell.childNodes.forEach(walk);
          const cellMd = out.trim().replace(/\n+/g, " ").replace(/\|/g, "\\|");
          out = saved;
          return cellMd;
        }));
      const tableMd = rowsToTableMd(rows);
      if (!tableMd) return;
      if (out && !out.endsWith("\n")) out += "\n";
      out += tableMd + "\n";
      return;
    }
    if (tag === "B" || tag === "STRONG") { out += "**"; node.childNodes.forEach(walk); out += "**"; return; }
    if (tag === "I" || tag === "EM") { out += "*"; node.childNodes.forEach(walk); out += "*"; return; }
    if (tag === "U") { out += "__"; node.childNodes.forEach(walk); out += "__"; return; }
    if (tag === "CODE") { out += "`"; node.childNodes.forEach(walk); out += "`"; return; }
    if (tag === "A") {
      // 名詞連結存回 [[名稱]]:名稱以 data-wl 為準而不是顯示文字——詳細頁的畫重點
      // 會把 <mark>/<span class="hl-…"> 插進連結文字裡,拿 textContent 存回去就會
      // 把那些標記文字一起吃進名稱。這一支必須排在一般連結前面(它沒有 href)。
      if (node.classList.contains("wikilink")) {
        out += `[[${node.getAttribute("data-wl") || node.textContent}]]`;
        return;
      }
      const href = node.getAttribute("href") || "";
      out += "["; node.childNodes.forEach(walk); out += `](${href})`;
      return;
    }
    if (tag === "UL" || tag === "OL") {
      // 每個 <li> 各自借用 out 暫存(比照 TABLE 分支),讓 li 內的粗體/連結等
      // 行內語法照樣能透過遞迴 walk 正確轉換;li 內部換行折成空白,清單語法
      // 一個項目只能佔一行,存不了字面換行。
      const ordered = tag === "OL";
      const items = [...node.children].filter(li => li.tagName === "LI");
      if (out && !out.endsWith("\n")) out += "\n";
      items.forEach((li, i) => {
        const saved = out; out = "";
        li.childNodes.forEach(walk);
        const itemMd = out.trim().replace(/\n+/g, " ");
        out = saved;
        out += (ordered ? `${i + 1}. ` : "- ") + itemMd + "\n";
      });
      return;
    }
    if (tag === "BLOCKQUOTE") {
      // 引文區塊:借用 out 暫存(比照 TABLE/UL 分支)讓內部的行內語法照常轉換,
      // 再逐行補回 "> " 前綴。內部換行保留(引文可以是多行的,跟清單項目不同)。
      const saved = out; out = "";
      node.childNodes.forEach(walk);
      const inner = out.replace(/\n+$/, "");
      out = saved;
      if (out && !out.endsWith("\n")) out += "\n";
      out += inner.split("\n").map(l => "> " + l).join("\n") + "\n";
      return;
    }
    if (tag === "SPAN") {
      // 只認新三色:hlToHtml 保證吐出來的一定是對應後的新色名,DOM 裡不會有 hl-red。
      // 維持 ^…$ 全字串比對(span 只能掛這一個 class),多一個 class 就不算畫重點。
      const hm = /^hl-(yellow|green|pink)$/.exec(node.className || "");
      if (hm) { out += `{{${hm[1]}:`; node.childNodes.forEach(walk); out += "}}"; return; }
    }
    if (tag === "DIV" || tag === "P") {
      // 區塊前後各補一個換行,但子節點若已以換行收尾(如 <div>x<br></div>)不重複疊加
      if (out && !out.endsWith("\n")) out += "\n";
      node.childNodes.forEach(walk);
      if (!out.endsWith("\n")) out += "\n";
      return;
    }
    node.childNodes.forEach(walk);
  }
  root.childNodes.forEach(walk);
  return out.replace(/\n+$/, "");
}

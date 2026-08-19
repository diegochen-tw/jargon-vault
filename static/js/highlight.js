// 極簡語法高亮:regex tokenizer,依語言關鍵字表 + 通用字串/數字/註解規則。
// 刻意不引入 highlight.js/Prism 之類的外部套件(專案無 build step,不想為此
// 多背一個 vendor 檔的更新/授權負擔)——這裡用一個 combined regex 單一 pass
// 掃過原始碼,回傳可直接塞進 <code> 的 HTML(已跳脫)。
//
// 單一 pass 是關鍵:如果對同一段字串依序跑好幾次 comment.replace()、
// string.replace()、keyword.replace()…,後面的 regex 可能會誤吃進前面
// 已經插入的 <span> 標籤,把高亮結果搞爛。改成一個大 alternation regex
// (comment|string|number|keyword|fn),每個位置只會被最先對到的那組吃掉,
// 沒對到的字元原樣跳脫輸出,順序決定優先權(comment/string 一定要排在
// number/keyword 前面,否則字串裡的英文字也會被誤判成關鍵字)。
//
// 認不得的語言(或沒填語言)一律退回 GENERIC:只認字串/數字/註解,
// 沒有關鍵字表,寧可少上色也不要亂上色。
import {esc} from "./utils.js?v=20260820a";

const escRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const kwRe = list => `\\b(?:${list.trim().split(/\s+/).map(escRe).join("|")})\\b`;

const JS_KW = "break case catch class const continue debugger default delete do else export extends finally for function if import in instanceof let new return static super switch this throw try typeof var void while yield async await of as from get set null true false undefined NaN this";
const TS_KW = JS_KW + " interface type implements enum namespace declare readonly public private protected abstract satisfies keyof infer";
const PY_KW = "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield self match case";
const JAVA_KW = "abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public return short static strictfp super switch this throw throws transient try void volatile while var record sealed permits true false null";
const C_KW = "auto break case char const continue default do double else enum extern float for goto if int long register return short signed sizeof static struct switch typedef union unsigned void volatile while inline true false nullptr";
const CPP_KW = C_KW + " class namespace template public private protected virtual friend new delete this operator using try catch throw explicit override final constexpr noexcept";
const CSHARP_KW = "using namespace class public private protected internal static readonly virtual override abstract sealed new delegate event async await var string bool int double decimal float long short byte char object null true false get set this base try catch finally throw if else for foreach while switch case return void";
const GO_KW = "break default func interface select case defer go map struct chan else goto package switch const fallthrough if range type continue for import return var true false nil iota";
const RUST_KW = "as break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where async await";
const PHP_KW = "abstract and array as break callable case catch class clone const continue declare default do echo else elseif empty enddeclare endfor endforeach endif endswitch endwhile extends final finally fn for foreach function global goto if implements include include_once instanceof insteadof interface isset list namespace new or print private protected public require require_once return static switch throw trait try unset use var while xor yield true false null";
const RUBY_KW = "BEGIN END alias and begin break case class def defined do else elsif end ensure false for if in module next nil not or redo rescue retry return self super then true undef unless until when while yield";
const SQL_KW = "SELECT FROM WHERE AND OR NOT INSERT INTO VALUES UPDATE SET DELETE CREATE TABLE ALTER DROP INDEX VIEW JOIN INNER LEFT RIGHT OUTER ON GROUP BY ORDER HAVING LIMIT OFFSET AS DISTINCT UNION ALL NULL IS IN LIKE BETWEEN EXISTS CASE WHEN THEN ELSE END PRIMARY KEY FOREIGN REFERENCES DEFAULT UNIQUE CHECK CONSTRAINT DATABASE SCHEMA WITH RETURNING";
const BASH_KW = "if then else elif fi for while until do done case esac function in return exit export local readonly declare source alias set unset shift break continue eval exec trap true false";
const YAML_KW = "true false null yes no on off";
const PASCAL_KW = "and array asm begin case class const constructor destructor div do downto else end except exports file finalization finally for function goto if implementation in inherited initialization interface is label library mod nil not object of on or overload override packed private procedure program property protected public published raise record repeat set shl shr string then threadvar to try type unit until uses var virtual while with xor";

// 每個語言:comment(?:xxx)非 named 內層 alternation 字串(留空 = 不認註解)、
// string(是否認 backtick 樣板字串,只有 JS/TS 需要)、keywords(關鍵字清單,
// 留空 = 不上色關鍵字,只有字串/數字/註解)、ci(關鍵字比對是否忽略大小寫)。
const LANGS = {
  javascript: {comment: String.raw`/\*[\s\S]*?\*/|//[^\n]*`, template: true, kw: JS_KW},
  typescript: {comment: String.raw`/\*[\s\S]*?\*/|//[^\n]*`, template: true, kw: TS_KW},
  python: {comment: String.raw`#[^\n]*`, triple: true, kw: PY_KW},
  java: {comment: String.raw`/\*[\s\S]*?\*/|//[^\n]*`, kw: JAVA_KW},
  c: {comment: String.raw`/\*[\s\S]*?\*/|//[^\n]*`, kw: C_KW},
  cpp: {comment: String.raw`/\*[\s\S]*?\*/|//[^\n]*`, kw: CPP_KW},
  csharp: {comment: String.raw`/\*[\s\S]*?\*/|//[^\n]*`, kw: CSHARP_KW},
  go: {comment: String.raw`/\*[\s\S]*?\*/|//[^\n]*`, kw: GO_KW},
  rust: {comment: String.raw`/\*[\s\S]*?\*/|//[^\n]*`, kw: RUST_KW},
  php: {comment: String.raw`/\*[\s\S]*?\*/|//[^\n]*|#[^\n]*`, kw: PHP_KW},
  ruby: {comment: String.raw`#[^\n]*`, kw: RUBY_KW},
  sql: {comment: String.raw`/\*[\s\S]*?\*/|--[^\n]*`, kw: SQL_KW, ci: true},
  bash: {comment: String.raw`#[^\n]*`, kw: BASH_KW},
  json: {comment: "", kw: "true false null"},
  yaml: {comment: String.raw`#[^\n]*`, kw: YAML_KW},
  css: {comment: String.raw`/\*[\s\S]*?\*/`, kw: ""},
  html: {comment: String.raw`<!--[\s\S]*?-->`, kw: ""},
  pascal: {comment: String.raw`\{[\s\S]*?\}|\(\*[\s\S]*?\*\)|//[^\n]*`, kw: PASCAL_KW, ci: true},
};

// 語言標籤的別名(``` 之後那個字通常是這些變體)→ LANGS 的 key
const ALIASES = {
  js: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript",
  ts: "typescript", tsx: "typescript",
  py: "python", py3: "python",
  c: "c", h: "c",
  "c++": "cpp", cc: "cpp", cxx: "cpp", "c#": "csharp", cs: "csharp",
  golang: "go", rs: "rust", rb: "ruby",
  sh: "bash", shell: "bash", zsh: "bash", bat: "bash", powershell: "bash", ps1: "bash",
  xml: "html", htm: "html", vue: "html", svelte: "html",
  yml: "yaml", jsonc: "json",
  scss: "css", less: "css", sass: "css",
  pas: "pascal", dpr: "pascal", dpk: "pascal", lpr: "pascal", delphi: "pascal",
};

export function normalizeLang(lang) {
  const l = (lang || "").trim().toLowerCase();
  if (!l) return "";
  return ALIASES[l] || l;
}

function buildRegex(cfg) {
  const alts = [];
  if (cfg.triple) alts.push(`(?<string>'''[\\s\\S]*?'''|"""[\\s\\S]*?"""|"(?:\\\\.|[^"\\\\\\n])*"|'(?:\\\\.|[^'\\\\\\n])*')`);
  else if (cfg.template) alts.push('(?<string>"(?:\\\\.|[^"\\\\\\n])*"|\'(?:\\\\.|[^\'\\\\\\n])*\'|`(?:\\\\.|[^`\\\\])*`)');
  else alts.push(`(?<string>"(?:\\\\.|[^"\\\\\\n])*"|'(?:\\\\.|[^'\\\\\\n])*')`);
  if (cfg.comment) alts.push(`(?<comment>${cfg.comment})`);
  alts.push(String.raw`(?<number>\b0[xX][0-9a-fA-F]+\b|\b\d+\.?\d*(?:[eE][+-]?\d+)?\b)`);
  if (cfg.kw) alts.push(`(?<keyword>${kwRe(cfg.kw)})`);
  alts.push(String.raw`(?<fn>[A-Za-z_$][\w$]*(?=\())`);
  return new RegExp(alts.join("|"), cfg.ci ? "gi" : "g");
}

const _regexCache = new Map();

function regexFor(lang) {
  const cfg = LANGS[lang];
  if (!cfg) return null;
  if (!_regexCache.has(lang)) _regexCache.set(lang, buildRegex(cfg));
  const re = _regexCache.get(lang);
  re.lastIndex = 0;
  return re;
}

// GENERIC 退回規則:語言不明時只認雙/單引號字串跟數字,不猜關鍵字/註解
// (寧可少上色,不要把一般文字誤判成程式碼結構而上錯顏色)。
const GENERIC_RE = new RegExp(
  String.raw`(?<string>"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|` +
  String.raw`(?<number>\b0[xX][0-9a-fA-F]+\b|\b\d+\.?\d*(?:[eE][+-]?\d+)?\b)`,
  "g",
);

// 回傳可直接塞進 <code> 的 HTML(已跳脫);code 是原始碼(未跳脫)。
export function highlightCode(code, lang) {
  const norm = normalizeLang(lang);
  const re = regexFor(norm);
  const pattern = re || (GENERIC_RE.lastIndex = 0, GENERIC_RE);
  let out = "", last = 0, m;
  while ((m = pattern.exec(code)) !== null) {
    out += esc(code.slice(last, m.index));
    const g = m.groups || {};
    const cls = g.comment ? "tok-com" : g.string ? "tok-str" : g.keyword ? "tok-kw"
      : g.number ? "tok-num" : g.fn ? "tok-fn" : "";
    out += cls ? `<span class="${cls}">${esc(m[0])}</span>` : esc(m[0]);
    last = m.index + m[0].length;
    if (m[0] === "") pattern.lastIndex++;  // 保險:避免零寬匹配造成無窮迴圈
  }
  out += esc(code.slice(last));
  return out;
}

export const SUPPORTED_LANGS = Object.keys(LANGS);

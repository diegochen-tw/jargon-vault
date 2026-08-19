// 樣板欄位工具:把「名詞 + 樣板定義」解析成畫面要渲染的欄位清單。
// 只 import store 與 i18n(views → fields → store,維持單向依賴)。
import {t as i18nT} from "./i18n.js?v=20260820a";
import {state} from "./store.js?v=20260820a";

export function templateById(tid) {
  return state.allTemplates.find(t => t.id === tid) || null;
}

// 樣板物件 → 顯示名稱。內建樣板:名稱維持種子預設(未被使用者改名,name_is_default)
// 時顯示 i18n 標準名(隨介面語言);使用者改過名就顯示自訂名。自訂樣板一律用存檔名稱。
export function tplLabel(tpl) {
  if (!tpl) return i18nT("editor.tplDeleted");
  if (tpl.builtin && tpl.name_is_default) {
    const key = `tpl.builtin.${tpl.id}`;
    const label = i18nT(key);
    if (label !== key) return label;  // 有對應翻譯就用,否則退回存檔名稱
  }
  return tpl.name;
}

// 樣板的代表 icon(編輯器樣板下拉用)。真相在後端:內建樣板在 DEFAULT_TEMPLATES 的
// 種子、外掛樣板在封裝 manifest,前端只負責顯示與 fallback——刻意不做 id → emoji 的
// 前端對照表(第三方封裝永遠不會出現在那份表裡)。
// 沒有 icon 的自訂樣板退回中性圖示,讓下拉每一列的文字對齊。
// ⚠ 不要把它併進 tplLabel():那支被卡片、側欄、設定頁共用,併進去等於把 icon
// 帶到所有顯示樣板名的地方,那不是這次要的範圍。
export function tplIcon(tpl) {
  return (tpl && tpl.icon) || "📄";
}

// 顯示用的樣板名稱(以 id 查);樣板已刪時回傳佔位文字
export function templateName(tid) {
  return tplLabel(templateById(tid));
}

// 欄位定義的顯示層在地化(tplLabel 的欄位版):label/placeholder 仍是出廠值
// (後端 GET /api/templates 逐欄比對種子後給的 label_is_default/ph_is_default 旗標,
// 種子是英文基準)時,依介面語言查 i18n `tplf.<樣板id>.<欄位key>.label|.ph`;
// 使用者改過、或該樣板/語言還沒建翻譯(如外掛樣板)就照顯示儲存值。
// ⚠ 回傳**副本**,絕不就地改 def——那是 state.allTemplates 裡的樣板物件本身,
// 改了它等於把翻譯塞回「儲存值」,設定 → 欄位樣板的編輯器會存出被污染的資料。
// settings.js 的樣板編輯表單也用它顯示在地化值,靠 data-raw/data-disp 比對守住
// 同一條線:使用者沒改動的欄位在 collect() 時寫回原始儲存值,翻譯絕不落地。
export function locField(tid, d) {
  let {label, placeholder} = d;
  if (d.label_is_default) {
    const k = `tplf.${tid}.${d.key}.label`, v = i18nT(k);
    if (v !== k) label = v;
  }
  if (d.ph_is_default) {
    const k = `tplf.${tid}.${d.key}.ph`, v = i18nT(k);
    if (v !== k) placeholder = v;
  }
  return {...d, label, placeholder};
}

// 名詞要顯示/編輯的欄位定義清單 [{key,label,placeholder}]:
//   1. 以樣板定義中「啟用」的欄位為主(順序照樣板,即使用者拖曳出來的順序)
//   2. 樣板已刪 → 用名詞自身 fields 的 key 合成定義(label = key)
//   3. 停用的樣板欄位若在這筆名詞裡還有值 → 補在後面(關掉欄位不等於刪資料,
//      同「殘留 key」的處理:資料不隱形,永不靜默消失)
//   4. 樣板外的殘留 key(樣板事後改過欄位)→ 有值的一樣補在後面
export function fieldDefsFor(note) {
  const tpl = templateById(note.template);
  const all = tpl ? tpl.fields : [];
  const values = note.fields || {};
  const defs = all.filter(d => d.enabled !== false);
  for (const d of all) if (d.enabled === false && values[d.key]) defs.push(d);
  const known = new Set(all.map(d => d.key));
  const out = defs.map(d => locField(note.template, d));
  for (const key of Object.keys(values)) {
    if (known.has(key)) continue;
    if (!tpl || values[key]) out.push({key, label: key, placeholder: key});
  }
  return out;
}

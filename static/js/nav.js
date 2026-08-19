// 行動裝置返回手勢(如 Pixel 系列的邊緣滑動)在瀏覽器裡等同於「上一頁」。
// 開啟 modal 時先 pushState 佔一筆歷史紀錄,讓該手勢只關掉最上層的 modal,
// 不會真的離開頁面。純瀏覽器 API 包裝,無其他模組依賴。
const closers = [];
let handlingPop = false;
// popModalState() 自己呼叫 history.back() 也會觸發 popstate。那一發是「收掉自己佔的
// 歷史紀錄」,不是使用者要再關一層,必須跳過——modal 疊起來時(如未分組彈窗上面
// 再開設定)沒擋掉的話,關掉上層會把下層一起關掉。
let selfPops = 0;

window.addEventListener("popstate", () => {
  if (selfPops > 0) { selfPops--; return; }
  const fn = closers.pop();
  if (!fn) return;
  handlingPop = true;
  fn();
  handlingPop = false;
});

// 開啟 modal 時呼叫:push 一筆歷史紀錄,closer 是該 modal 對應的關閉函式。
export function pushModalState(closer) {
  closers.push(closer);
  history.pushState({gvModal: closers.length}, "");
}

// 由 UI(X 按鈕、背景點擊、Esc)主動關閉時呼叫,把佔位的歷史紀錄退掉。
// 若是由 popstate 觸發的關閉(handlingPop=true),這一層已經被上面的 handler 從
// closers 取走、歷史紀錄也已經被瀏覽器退掉了,這裡什麼都不能再做——多 pop 一次
// 會把下面那層 modal 的 closer 也吃掉。
export function popModalState() {
  if (handlingPop || !closers.length) return;
  closers.pop();
  selfPops++;
  history.back();
}

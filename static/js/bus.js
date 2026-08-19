// 極簡事件匯流排:讓 actions(資料變動)與 views(畫面重繪)解耦,避免模組循環依賴。
//
// 事件一覽:
//   results-changed     搜尋結果或編輯狀態(editing/creating)變了 → list view 重繪
//   tags-changed        標籤統計/群組變了                        → sidebar 群組樹重繪
//   templates-changed   欄位樣板定義變了                         → 設定的樣板管理分頁、側欄標籤分類重繪
//   filters-reset       所有篩選條件被外部清空(點 LOGO、或新建的名詞被篩選擋住時
//                       actions.saveNote 自動清空)             → 側欄日期/標籤分類/群組樹重繪選取狀態
//                                                               + app.js 同步搜尋框 DOM
//   results-appended    「載入更多」多載入一頁,疊加進 state.results → list view 只
//                       append 新增的卡片,不整包重繪既有的(避免圖片重新請求/畫面跳動)
//   open-note           要求打開某筆名詞的詳細彈窗,id 放在 state.pendingNoteId
//                       → app.js 轉給 detail view。存在的理由:編輯器的「你可能已經
//                       記過這個」要能點開那筆既有名詞,但 views 之間不互相 import
//                       (見 CLAUDE.md 的前端模組地圖),只有組裝根 app.js 接得起來。
//   auth-expired        session 中途失效(寫入動作收到 401)→ app.js 切回登入畫面
//   srs-resume          編輯器 modal 關掉了,而複習彈窗正處在「暫離」狀態(state.srsPaused)
//                       → srs view 回到暫離前那張卡(並重新讀一次那筆的內容)。
//                       ⚠ **由 list.js 發**,不是由編輯器自己發:關閉路徑有五條
//                       (存檔、取消、Esc、點編輯器外面、刪除),盯著「編輯器從開變關」
//                       這一件事才全部涵蓋得到,靠每條路徑各補一行 emit 遲早漏一條。
//   settings-resume     編輯器 modal 關掉了,而設定 modal 正處在「暫離」狀態
//                       (state.settingsPaused:從 整理與清理 → 內容健康度檢查 的清單
//                       點了某一筆去修它)→ settings view 原樣回到那份清單。
//                       發送端與 srs-resume 同一處、同一個理由(見上一條);兩者
//                       刻意是兩個事件而不是一個泛用的「編輯器關了」——各自的暫離
//                       旗標由各自的 view 擁有,合成一個只是把判斷搬去別人家。
//   ai-settings-changed state.aiSettings 載入完成或被 admin 改掉 → list view 決定
//                       語意搜尋 icon(#semanticToggle)顯不顯示。存在的理由:
//                       boot 裡 loadAISettings() 沒有 await、排在 initList() 之後,
//                       init 當下 state.aiSettings 還是 null,只能事件到了再同步。
//
// emit() 刻意不帶 payload:要傳的東西一律先放進 store,事件只負責說「有東西變了」。
const listeners = new Map();

export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
}

export function emit(event) {
  for (const fn of listeners.get(event) || []) fn();
}

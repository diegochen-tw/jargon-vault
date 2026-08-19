// 全域 UI 狀態(唯一來源)。只放資料,不放行為;
// 變動狀態後要觸發重繪時,呼叫 actions 或直接 emit 對應事件(見 bus.js)。
export const state = {
  me: null,              // 目前登入的使用者 {id, email, is_admin, version}(null = 未登入;boot() 探測後填入)
  q: "",                 // 搜尋關鍵字
  tags: [],              // 篩選中的標籤(multiSelect=false 時最多 1 個,單選;true 時可複選,AND)
  multiSelect: false,    // 標籤是否可複選(false = 單選,點新標籤取代舊選取;預設關閉,見 sidebar.js)
  group: "",             // 篩選中的標籤群組("" = 全部;OR:掛群組內任一標籤即命中)
  template: "",          // 篩選中的欄位樣板 id("標籤分類",見 sidebar.js;"" = 全部)
  dateActive: false,     // 日期橫條篩選是否啟用(見 sidebar.js 的 renderDateBar)
  dateDays: 3,           // 日期區間天數(bar 中間顯示,由標題列 +/− 步進)
  dateOffset: 0,         // 區間結尾距今天幾天(0 = 區間以今天收尾;◀ 往過去步進一個區間)
  // 日期篩選比哪一個時間:updated=上次編輯(預設)/ created=建立時間。
  // 存 gv-datefield;與 sort 是兩件事(那個是排序依據,這個是篩哪一段時間)。
  dateField: "updated",
  results: [],           // 目前搜尋結果
  viewMode: "detail",    // 結果瀏覽模式:detail=單欄詳細列表 / grid=多欄流動方格(見 list.js;存 gv-viewmode)
  hideDesc: false,       // 是否隱藏卡片列表的「說明」內容(見 list.js;存 gv-hidedesc)
  hideImg: false,        // 是否隱藏卡片列表的圖片(見 list.js 的 applyImgMode;存 gv-hideimg;逐卡可就地展開)
  markedOnly: false,     // 是否只顯示有書籤標記的名詞(見 list.js;存 gv-markedonly;真的篩選條件,走後端)
  detailNarrow: true,    // 詳細頁彈窗是否用窄行寬(true=720px 約 42 字 / false=920px 約 62 字)(見 detail.js;存 gv-detailnarrow)
  imgCompress: true,     // 上傳圖片前是否先壓縮(見 settings.js 的「內容」分頁;存 gv-imgcompress;預設啟用)
  hasMore: false,        // 是否還有更多可分頁載入(見 list.js 的「載入更多」按鈕;每次 search() 依回應更新)
  loadingMore: false,    // 「載入更多」是否正在打 API,防止連點造成重複載入/重複 concat
  sort: "updated",       // 卡片列表排序依據:updated=上次編輯時間 / created=建立時間(見 list.js;存 gv-sort)
  noteIndex: new Map(),  // normKey(名稱) → {id,name}:`[[名詞]]` 連結的解析對照表(見 actions.loadNoteNames)
  allTags: [],           // 全站標籤統計 [{name,count,created,group}]
  allGroups: [],         // 全站群組統計 [{name,count}](count = 掛群組內任一標籤的名詞數)
  allTemplates: [],      // 欄位樣板定義 [{id,name,builtin,fields:[{key,label,placeholder}]}]
  aiSettings: null,      // AI 連線設定 {enabled,api_style,base_url,api_key,model,embed_model,desc_limit_enabled,desc_max_chars}(null = 尚未載入)
  // ── 語意檢索 ──────────────────────────────────────────────────
  // 開關是**明確的使用者動作**,刻意不做成自動:搜尋框有 120ms debounce,
  // 掛上去等於每次按鍵停頓都打一次嵌入模型。
  semantic: false,         // 是否用語意檢索取代關鍵字搜尋(存 gv-semantic)
  semanticNeedsIndex: false, // 後端回報「還沒建索引」——要說得出口,不能只顯示空列表
  semanticFailed: false,   // 查詢失敗(多半是模型服務連不上)。**刻意跟 needsIndex 分開**:
                           // 混為一談會把使用者指去按「建立索引」,而那顆按鈕當然也會失敗
  semanticUnindexed: 0,    // 還沒有向量的名詞數(存檔不碰嵌入,所以一定會落後)
                           // 刻意不是設定頁那個精確的 stale:查詢路徑只數檔案不讀檔,
                           // 兩者是不同的東西(見 app/semantic.py:quick_status)
  plugins: [],           // 外掛清單 [{id,category,version,name,description,installed,enabled,config,…}]
                         // 名稱/描述來自封裝 manifest(後端依 lang 挑好),不查前端 i18n
  pluginScanErrors: [],  // 壞封裝清單 [{dir,reason}]——只有 admin 拿得到(GET /api/plugins)

  // ── SRS 複習(側欄日期區塊下方的「複習」彈窗;見 views/srs.js)──────
  // 彈窗開關**不放這裡**:比照 detail/settings,真相是 DOM 的 .show class。
  // 一輪抽完的卡就丟掉,不做持久化——零負債:沒複習完不會變成明天的債。
  srsCards: [],          // 本輪抽到的卡(後端已按 srs_due 排序後隨機打散)
  srsIndex: 0,           // 目前第幾張(= 已完成的張數)
  srsRevealed: false,    // 目前這張是否已翻面
  srsScopeAll: false,    // 是否忽略側欄篩選、改抽全庫(彈窗頂端的切換;只在記憶體)
  srsPool: 0,            // 目前範圍內的名詞總數(彈窗頂端那行要誠實講出範圍多大)
  srsLoading: false,     // 抽卡請求進行中
  srsHint: 0,            // 這張卡已給到第幾級提示(0=未給 / 1=關鍵字 / 2=除說明欄外全給;
                         // 見 views/srs.js:hintHTML。翻面與換卡都歸零——提示等級跟著卡片走)
  srsPaused: false,      // 是否有一輪「暫離中」:使用者在複習途中跳去編輯那筆名詞。
                         // 撐住 srsCards/srsIndex 不被清掉,編輯器一關就回到原本那張。
                         // ⚠ 這是彈窗開關的**唯一例外**:平常「開著沒」的真相是 DOM 的
                         // .show class,但暫離時彈窗是關的、這一輪卻還活著,DOM 講不出來
  settingsPaused: false, // 同上,但暫離的是**設定 modal**:從 整理與清理 → 內容健康度
                         // 檢查 的清單點某一筆去修它。編輯器一關就回到那份清單(掃描
                         // 結果與捲動位置都保留),接著修下一筆。見 views/settings.js
  editing: null,         // 編輯中的名詞 id(null = 沒有)
  editingNote: null,     // 編輯中那筆的完整內容,**只在它不在任何列表結果裡時**才用得到:
                         // 複習彈窗暫離編輯時,抽到的卡多半不在 state.results(抽卡池與
                         // 列表篩選是兩套範圍),靠 results.find 會找不到,編輯器就靜默
                         // 打不開。list.js:renderEditorModal 拿它當最後的來源
  creating: false,       // 是否開著「新建」編輯器
  draftId: null,         // 新建時預先配發的 id(貼圖歸檔用)
  focusDescOnOpen: false, // 開編輯器時直接聚焦說明欄(搜尋列 Enter 建新名詞的流程)
  newName: "",           // 新建時要預先填進名詞欄的名稱(點了尚未建立的 [[連結]] 時帶入;關閉編輯器即清空)
  pendingNoteId: null,   // 要求打開詳細彈窗的名詞 id(配合 bus 的 open-note 事件;emit 不帶 payload)
  lastNewTemplateId: null, // 本次 session 內「新建」時最後選過的欄位樣板 id(只在記憶體,重新整理/登出即重置回預設值;不進 localStorage)

  // 走 /invite/<token> 進來時的邀請 token 與預覽結果({valid})。
  // 放 store 而不是 app.js 的模組變數:登入畫面(views/auth.js)註冊時要把 token
  // 一起送出去,而 views 之間不互相 import(見 CLAUDE.md 的前端模組地圖)。
  inviteToken: "",
  inviteInfo: null,

  // ── 公開分享連結 ──────────────────────────────────────────────
  // 站台開關由 GET /api/auth/me 帶回(boot 的第一支請求,順便捎回來,不為了
  // 它再多打一支設定 API;比照 is_admin 決定要不要顯示管理分頁)。
  publicShareEnabled: false, // 站台是否啟用公開分享連結
  publicNotebookEnabled: false, // 站台是否啟用公開筆記快照(設定 → 公開筆記 分頁看這個)

  // ── 範例資料的置頂行 ──────────────────────────────────────────
  // 同樣搭 GET /api/auth/me 回來。demoBanner 是**單一旗標**:它同時代表
  // 「範例資料還在」與「橫幅要顯示」,按下刪除鈕時兩件事一起結束
  // (見 app/users.py:set_demo_seeded)。網址由後端給,前端不寫死。
  demoBanner: false,
  demoSiteUrl: "",
};

# 📈 CHANGELOG - 版本變更日誌

> **唯一事實來源**：任何功能增刪、修改或技術架構調整，必須第一時間喺呢度同步更新（見 `AGENTS.MD` §1）。格式：倒序（最新喺上面）。

---

## 2026-10-09

### MINOR：i18n 文案全部移出代碼 → `gateway/i18n_strings.json` / `gui_kline_strings.json`
檔案：`gateway/i18n_strings.json`（新，367 鍵）、`gateway/pages/gui_kline_strings.json`（新，24 鍵）、`gateway/i18n.py`（699 行 → 39 行）、`gateway/pages/gui_kline.py`、`.scratch/t_i18n_json.py`（一次性遷移腳本 `gen_i18n_json.py` 與 `i18n_snapshot.json` 驗證後已按 Auto-Purge 刪走）
- **動機**：三語表（367 鍵 × 3）同 K 線測試頁兩語表（24 鍵）原本硬編碼喺 `i18n.py` / `gui_kline.py` 嘅 dict 入面 → 改一句文案要喺幾千行代碼搵位、仲要郁 code 檔。文案本質係**資料**，唔係邏輯。
- **做法**：一次性腳本 `gen_i18n_json.py` 將兩份表原樣 dump 出 JSON（key 順序照功能頁面分組，唔重新排序 → diff 可對照），代碼檔淨低 `json.loads(pathlib.Path(__file__).with_name(...))` + 取字。**`t()` 契約零改**：三語 `t(key, lang)` 同兩語 `t(lang, key, **kw)`（含 `{kw}` 狀態模板 format）簽名、行為、fail-fast（未知 key 即刻 `KeyError`）全部照舊 → 所有 call site 零改動。
- **新增守門（外部資料必須核對）**：JSON 變咗外部檔案，缺語言唔再係 `SyntaxError` 而係「上咗 UI 先發現某頁冇譯文」→ 載入時一次過核對三語齊全，缺就 `ValueError` 並**列明邊啲 key**；兩份 JSON 都唔准有多餘語言欄。
- **邊啲唔入 JSON**：語言自稱 endonym（`lang_labels` / `lang_short`，「繁體中文 / EN」唔跟 UI 語言變）照樣入 JSON 但**語義上唔翻譯**；每頁 `_TEXT`（objectName → key 嘅映射）屬排版契約，留喺 code（見 `ui-separation-required`）。
- ⚠️ **零改動證明（遷移一次性，已 Auto-Purge）**：dump 前先 snapshot 兩份表，逐 key 逐語言對比 → 三語 367 鍵 + 兩語 24 鍵**逐字相同**、key 集合一致、無多餘語言欄（唔係「睇落一樣」）。驗證過後 snapshot 同一次性 generator 已刪走，避免日後正常改文案時測試假失敗。
- **驗證**：`.scratch/t_i18n_json.py`（長期回歸）→ 「所有功能測試成功 ✅ (13 項)」：① 代碼冇殘留文案表（`STRINGS = {` / `_s(` 已消失）、兩份 JSON 喺位可 parse、路徑一律 `pathlib.with_name()`（冇 hardcode 斜線）② 兩份表結構齊全（367 鍵三語 / 24 鍵兩語，冇多餘語言欄；代碼入面嘅表 == JSON）③ 取字行為唔變（三語取字 / 未知 key 炸 / 兩語模板 format / theme helper 經 JSON）④ 故意刪走 `nav_quotes` 嘅 zh_cn+en → 子進程 `import i18n` 即刻 `ValueError` 並列明 key ✅。全量回歸 21 支測試（5 支單元 + `t_ui_infra` / `t_p1` / `t_p2` / `t_p3` + 11 支 GUI E2E）**全部 exit 0 全綠**。

### MINOR：K 線 stream 基建抽成 `gateway/kline_stream.py`（`LoopThreadBase` + `ClientHolderMixin`，兩頁變 subclass）
檔案：`gateway/kline_stream.py`（新）、`gateway/pages/gui_kline.py`、`gateway/pages/quotes_page.py`、`.scratch/t_p2_cache_switch.py`
- **動機**：用戶第 ③ 項「模組化共用」真正重覆咗兩份嘅唔係繪畫（已經只有一套 `KlineChart`），而係 **thread/worker 樣板** — `gui_kline.Worker`+`LoopThread` 同 `quotes_page.GridWorker`+`_LoopThread` 逐字寫多一次 loop 生命週期同 client holder。
- **新契約**：`LoopThreadBase(QThread)` = 起 loop、**喺自己條 thread** 建 worker（signal affinity → emit 自動 queue 去 GUI）、`worker_cls` 俾 subclass 指定、`worker_ready`、`request_shutdown()`（取 K 線頁嗰份 **superset**：2s deadline poll，應付「`start()` 之後 worker/loop 未起好就收線」嘅 race；行情頁舊版冇 poll，呢度係淨改善）、finally 收 pending task + `loop.close()`。`ClientHolderMixin` = `init_client_holder()` + `ensure_client()`（`client_factory()` 返 None → lazy import 真 `BrokerClient`）+ 模板 `shutdown()` = `_cancel_work()`（**subclass hook**：cancel 自己啲 consumer task，等 broker 端 cleanup 完成）→ `release_client()`（`__aexit__` → 釋放 IB clientId / futu 共享 ctx）→ `loop.stop()`。
- **取捨（用戶已定）**：worker 嘅 **payload 形狀照樣各管各**（K 線頁 send records 俾 ResultPanel、行情頁 send df 俾 chart）→ 用 **mixin** 而唔係共用 `QObject` base（base 會逼埋 signal 形狀一齊改，兩頁 E2E 都要大改）。呢檔 **唔准 import `gateway.pages.*`**（兩頁都 import 佢 → 循環）。
- 🐞 **連鎖改動（ refactor 先暴露到）**：`ensure_client` 離開 `gui_kline` → 舊 E2E 嘅 `gk.BrokerClient = FakeClient` patch **無聲失效**（真 import 已改喺 `kline_stream` 面 lazy 行）。一律經 `MainWindow(client_factory=...)` 注入 fake（`MainWindow` 新增呢個參數，同 `QuotesPage` 同一入口）。教訓：**用 patch module attribute 注入依賴嘅測試，一旦代碼搬咗家就會靜默失效** → 注入一律經顯式 factory 參數。
- **驗證**（純搬移、行為零改）：`t_p2_cache_switch` 35 ✅ · `t_p1_overlay` 20 ✅ · `t_p3_shared_conn` 30 綠（另有 1 項真 push 斷言因收市無成交自動 SKIP，非回歸）✅ · `e2e_gui_quotes` / `e2e_gui_p8` / `e2e_gui_indicators` / `e2e_gui_shell` / `e2e_gui_home` / `e2e_gui_fulltest` / `e2e_gui_connection` / `e2e_gui_futu_trade` 全部 exit 0 ✅。⏳ `gateway/pages/gui_fulltest.py` 仲有第三份 `LoopThread`/`TestWorker` 複製未落返嚟（超出呢輪範圍）。

### MINOR：futu 所有 stream 共用**一條** OpenD 連線（訂閱 refcount + 單 handler 按 (code,k_type) 路由）
檔案：`modules/futu_client.py`、`.scratch/t_p3_shared_conn.py`、`.scratch/bench_kline_switch.py`、`.scratch/bench_futu_concurrency.py`、`.scratch/bench_futu_setup_path.py`
- **動機**：`_new_ctx` 原本刻意「每條 stream / 每次 `get_kline` 各開一條 `OpenQuoteContext`」。行情頁 6 格 = **13 次 handshake**，全部串行排喺同一個 event loop 前面 → 六格同時切要 5.8 秒。呢度改嘅係一段刻意寫低嘅設計，故 docstring 同步講明新契約。
- **新契約**：`_open_ctx()` = 全檔唯一開 ctx 嘅位；`_ensure_ctx()` lazy 開**一條**共享 ctx 並掛上**一個** `KlineRouter`；`_subs: dict[(code,ktype) -> int]` 做 refcount（0→1 先真 `subscribe`、歸零先 `unsubscribe`）；`_queues: dict[(code,ktype) -> list[queue.Queue]]` = 每條 stream 各有一個**獨立** queue（兩格設同一標的同一週期都唔會搶走對方嘅 push）；`_acquire`/`_release` 係同步函數，外層一律 `await asyncio.to_thread(...)`。
- **生命週期**：stream 結束只退返自己嗰份訂閱、**唔 close 共享 ctx**；真正收線歸 `FutuClient.disconnect()`（`BrokerClient.__aexit__` 已經會循環調每個 client 嘅 `disconnect()` → 上層零改動）。`get_kline` 一次性取數照用獨立連線：唔需要 push、唔同 stream 搶訂閱配額、用完即斷最簡單。
- 🤖 **實證（之前只係推測）**：futu 推落嚟嘅 K 線 DataFrame **真的帶 `code` / `k_type` 欄** → 所以路由必須喺 `_normalize_kline` **之前**做（佢會 drop `code`）。`KlineRouter` 用 `groupby(['code','k_type'])` 分派、每組只派 `tail(1)`；push 冇路由欄就照實唔派（唔會亂派錯格）。
- **失敗隔離**：只有 **transport 層異常**先標 `_shared_dead` 並即刻清走共享連線（下次取數自我修復重開）；`subscribe` 回 `RET_ERROR`（權限／參數類）**唔標 dead** — 亂標會連其他 live stream 一併拆走。兩條路徑都照原有 `(False, None, msg)` 契約如實回報。
- 📊 **量度**（真 OpenD，`.scratch/bench_kline_switch.py` 連跑三次）：開連線次數 **13 → 1** ✅ · 六格 K_1M 全部上圖 **839 →(P2) 1142 / 786 / 523 ms** · 六格同時切 **717 →(P2) 1549 / 562 / 283 ms**。⚠️ 第一次 1549 ms 係**冷啟動離群值**（成個進程第一次同 OpenD 握手），同一代碼連跑即回落 → P3 係淨改善。教訓：呢個 bench 必須連跑多次先可以比較。
- 🔬 **顯微鏡核對**（證明剩低嘅成本唔喺 futu 層）：`bench_futu_concurrency.py` → 一條 ctx 串行 4 ms / 開 6 個 thread 3 ms / 六條 ctx 54 ms，`get_cur_kline` 每次 ~1 ms（訂閱之後 SDK 有本地快取）→ **共用連線唔會令取數串行化**；`bench_futu_setup_path.py` → 六條 stream 起 591 ms、六格同時切只需 **33 ms**（acquire 1–20 / fetch 2–9 / release ~0 ms）。
- 🐞 **踩坑**：`__init__` 入面 `self._shared_ctx = None` 會 **shadow 掉同名方法** `_shared_ctx()` → 首跑即 `'NoneType' object is not callable`。方法改名 `_ensure_ctx()`。規則：方法名唔好同 instance 屬性同名。
- **驗證**：`t_p3_shared_conn.py` → 「所有功能測試成功 ✅ (31 項，31 綠)」：A 共享連線／refcount／fan-out／stream 結束唔 close／disconnect 先收線／斷後重開；B transport 異常自我修復 + RET_ERROR 唔拆連線；C router 分派（跨標的、同 key 兩條、已 normalize、`tail(1)`、冇路由欄、未訂閱 key）；D 真 OpenD 兩條 stream 共用**一條**連線 + 真 push 經 router 落到自己 queue。回歸 `t_p2_cache_switch.py` 35 項 / `t_p1_overlay.py` 20 項 / `e2e_gui_quotes` / `e2e_gui_p8` / `e2e_gui_indicators` 全綠 ✅。

### MINOR：K 線切換週期加速（程序層快取 + 兩段式取數）+ 加載態收落 `KlineChart` + K 線頁即切週期
檔案：`modules/kline_cache.py`（新）、`gateway/ui/kline_overlay.ui`（新）、`modules/futu_client.py`、`modules/config.json`（`futu.kline_num_first`）、`gateway/pages/gui_kline.py`、`gateway/pages/quotes_page.py`、`gateway/i18n.py`（`chart_loading` / `chart_loading_slow` 三語）、`gateway/pages/kline_page.py`（theme restyle）、`.scratch/bench_kline_switch.py`、`.scratch/t_p1_overlay.py`、`.scratch/t_p2_cache_switch.py`、`.scratch/e2e_gui_quotes.py`（Part 4b）
- **要求**（用戶三項）：① 所有 K 線圖切換週期會加載，能加速嗎 ② 切換期間要加載效果（加暗 + LOADING）③ K 線圖要模組化共用。查證：五個慢因全部結構性（冇 cache / 取 1000 根但 `MAX_DRAW=300` / 每條 stream 一條 OpenD 連線 / futu 同步呼叫阻塞共享 loop / 完全冇加載態）→ 冇一個係「調個參」能解決。
- **③ 模組化嘅实情**：繪畫**已經只有一套**（`KlineChart` + 唯一子类 `IndicatorKlineChart`）→ 加載態寫落 base class，K 線頁 1 個圖 + 行情頁 6 格自動全有，零新增狀態機。（真正重覆三份嘅係 stream/thread 樣板 → 排 P4，行為穩定先 refactor。）
- **加載態契約**：`KlineChart.set_busy(bool)` + `_ensure_overlay()` **惰性** `load_ui('kline_overlay', QWidget)`（⚠️ 唔喺 `__init__` 行 `apply_ui` — 行情格 chart 本身由 `chart_cell.ui` promote 出嚟）、`WA_TransparentForMouseEvents`（加載期間 pan/zoom 照樣郁）、加暗用 `rgba()` 由 `C_SURFACE` 現算（**唔用 `QGraphicsOpacityEffect`** — 會 rasterize 成個 matplotlib canvas）、`resizeEvent` 只改 geometry 唔 redraw、`_restyle_overlay()` 喺 theme 切換點被 call、8s watchdog 轉慢文案。
- **快取**：`modules/kline_cache.py` = LRU 32 + TTL 按 ktype（分鐘 30s／時鐘 120s／日週月 600s），key `(code.upper(), ktype)`，value 存 `set_bars` 形狀 row tuple（唔 store DataFrame）；只喺 GUI thread 用 → 冇 lock；唔寫空 rows。兩頁讀寫：起 stream 前 hit → 即刻上圖，真 baseline 覆蓋；收到 baseline/tick → put。
- **兩段式取數**：`stream_kline` 第一段只取 `kline_num_first`（300，同 `MAX_DRAW` 對齊）→ yield baseline 即刻上圖；背景補取完整 `kline_num` 再 yield 一次快照。🤖 **對 GUI 零契約改動** — consumer 現有嘅 `n == 1 → baseline，其後 → tick` 自動當第二次係一次重繪；呢個就係揀兩段式而唔係直接砍到 300 嘅原因（`MAX_VIEW` 縮放深度唔減）。補取失敗只 log，唔折騰整條 stream。順帶 futu 全部同步呼叫（`subscribe` / `get_cur_kline` / 分頁）一律 `asyncio.to_thread`。
- **即切週期**：`ktype_combo` 由「跑緊 disable」清單移出 + `currentTextChanged` → `_on_ktype_changed`。⚠️ GUI 側 `stop_stream(); start_stream()` 係 **no-op**（`start_stream` 見 `_consumer_task` 未 done 就 early-return，`stop_stream` 嘅 cancel 係异步排喺後面）→ 必須一次過交俾 loop：`LoopThread.restart_stream` → `Worker._restart`（`asyncio.shield` + `_silent_cancel`），被取代 run 嘅 `done` payload 壓住，否則會收返 button 文案／輸入框狀態並顯示 ❌。
- 📊 **前後量度**（`.scratch/bench_kline_switch.py`，真 OpenD）：首次切未睇過嘅週期 **609 → 533 ms**（rows 1000 → 300 = 兩段式生效；改善有限 → 主成本係 OpenD round-trip 而唔係根數）· 切返睇過嘅週期 **323/163 → 0 ms**（目標 <50 ms ✅）· 六格 K_1M 全部上圖 **2412 → 839 ms**（`to_thread` 先至真並發）· 六格同時切 **5797 → 717 ms**。⚠️ 舊 bench 指標係「逐格等下一次 mark」→ 無成交時段會 block 長 timeout，已改成「六格全部有第一幀」呢個用戶感知指標。OpenD 連線數未變（4 / 13）= P3 目標 → 1。
- **驗證**：`t_p1_overlay.py`（20 項：惰性 / 可見性 / objectName / 文案 / geometry 跟 resize / 滑鼠穿透 / rgba 跟 palette / 三語 / watchdog / 冪等 / 子类繼承）、`t_p2_cache_switch.py`（35 項：cache hit·key 隔離·TTL·LRU·唔寫空 / 兩段式 `[300,1000]` + `kline_num_first=0` 關兩段 / 即切即刻 set_busy + 第二組 start + button 文案唔變 + 兩條週期各自入 cache + 快取命中即刻上圖）→ 全部「所有功能測試成功 ✅」。`e2e_gui_quotes.py` 再補 **Part 4b**（11 項，純 GUI 端）：換週期即刻加暗 + LOADING（`kline_loading` 有文字）/ 快取命中 → 即刻上返睇過嘅圖（零 network，唔係空白）/ 快取上圖期間照樣 LOADING / 真 baseline 到 → 覆蓋快取圖 + 疊層收返 / baseline 寫返入快取 / 兩段式 3→12 根喺 GUI 只係多一次重繪 / 快取跟住更新成最新快照 / 行情頁一律唔傳 `kline_num`（根數歸 config）/ 切返睇過嘅週期即刻上快取圖 → 79 項全綠 ✅。⚠️ 測試要訣：呢啲斷言一律放喺 `click()` 之後**即刻**做 — 讀快取 + `set_busy` 全程同步喺 GUI thread，一 `processEvents()` 就可能已被真 baseline 蓋咗。回歸 `e2e_gui_p8` / `e2e_gui_indicators` 全綠 ✅。

### MINOR：UI 分離 — 全部排版入 `gateway/ui/*.ui`（QUiLoader 執行期載入、冇 codegen）
檔案：`gateway/ui/loader.py`、`gateway/ui/bind.py`、`gateway/ui/*.ui`（11 頁 + `app_shell` + `standalone_window` + `popup_page` + 子模板 `chart_cell` / `strategy_rule_row`）、`gateway/app.py`、`gateway/pages/base_page.py`、`gateway/pages/`（11 頁全部改寫；`gui_kline.py` / `gui_fulltest.py` **零改動**）、`.scratch/t_ui_infra.py`、`.scratch/e2e_gui_shell.py`、`.scratch/e2e_gui_connection.py`、其餘各頁 E2E
- **要求**（用戶，長期規則）：所有 UI 一律入 `.ui`，Qt Designer 可直接調 margin / 間距 / 欄位比例；執行期載入，唔准 codegen（uic 產生 py）。
- **基礎設施**：`loader.apply_ui(page, name)` 將 `.ui` 砌喺**現有實例**之上（root = 第一個 `parent is None` 嘅 createWidget，**唔靠 class name** — Designer 對 QMainWindow form 會寫 `<widget class="QMainWindow">`）；`load_ui(name, root_cls)` 起新實例 = 重複子模板；`register_custom()` 俾 promote 嘅自繪 widget 返真 class；`.ui` 經 QBuffer 讀（跨平台）。`bind.stamp(root, registry)` 按 objectName 注入 QSS dynamic property（Designer 會 drop 自訂 property）；`bind.apply_text(root, table, lang)` = table-driven retranslate。
- **契約**：objectName == Python attribute name（QUiLoader 將子 widget 掛做 root attribute，`self.navSlot` / `row.rule_lbl` 直接可用）== QSS `#objectName` == E2E 鉤子 → 舊 objectName 一個冇改。**數量屬資料、排版屬 UI**：`.ui` 留空 layout slot（`navSlot`/`menuSlot`/`langSlot`/`paramSlot`/`rulesSlot`/`gridSlot`/`detailNoteSlot`/`periodSlot`…），由 registry（`PAGE_KEYS`/`NAV_MENUS`/`LANGS`/`LAYOUTS`/`KTYPES`/`INDICATOR_DEFS`/`CONDITION_DEFS`）喺運行期填 → 加頁 / 加參數唔使改 `.ui`。signal 接駁一律留喺 code（`.ui` 嘅 `<connections>` 空）。
- **命令式建檔清零**：全 repo 掃描 `QVBoxLayout(`/`QHBoxLayout(`/`QGridLayout(`/`setContentsMargins(`/`.setSpacing(` → 得返 `gui_kline.py` / `gui_fulltest.py`（零改動契約，靠 `takeCentralWidget()` 嵌入）。retranslate 全部轉 `_TEXT` 表；剩低嘅 `setText(t(...))` 全部屬**資料或狀態**（registry 生成嘅 nav/menu/QAction、status/result/crumb、逐格 placeholder、`_PH` 循環）。
- 🐞 **`.ui` 靜默陷阱 ×3**（futu_trade 等價實證逼出）：① QGridLayout 間距屬性名必須 `horizontalSpacing`/`verticalSpacing`（寫 `hspacing`/`vspacing` uic 直接丟掉 → 實測變 6、欄位差 12px）；② card `vsizetype="Fixed"` 會令 `QWidgetItem` 將 item 最小高度抬到 sizeHint（成頁 minimumSizeHint +28px，細窗縮唔落）；③ `.ui` 冇 `<item stretch>` 可用 → root 級「剩餘高度歸某區」必須包 wrapper（`cols_wrap` verstretch 1）；sizePolicy 嘅 horstretch/verstretch 就真係生效（`layout.stretch(i)` 報 0 只係 reporting，geometry 實證先準）。
- 🐞 **`WA_StyledBackground` 只對 `type(w) is QWidget` 生效** → 純 QWidget subclass 嘅 page root 一直食唔到頁面級 QSS；`stamp` 改用 `type(w).paintEvent is QWidget.paintEvent` 判斷後全部補返。
- 🔎 **兩個新發現**：`apply_text` 原本只 call `setText` → QGroupBox 冇呢個方法，組標題入唔到表，故加 `isinstance(w, QGroupBox) → setTitle` 分支；QUiLoader 對 QMainWindow form 即使 root 係注入嘅實例，都照樣接好 central widget（`StandaloneWindow` / `_PopupPageWindow` 因此唔使再自己 `setCentralWidget` / `resize`，root `geometry` 由 `.ui` 管）。
- 🧩 **重複子模板**：`chart_cell.ui`（行情頁單格，首次喺真頁用 `<customwidget>` + `register_custom`）與 `strategy_rule_row.ui`（策略每條條件一行）。⚠️ 逐條要獨有 objectName（俾 E2E / 刪除用）→ `stamp` 必須排喺改名**之前**（stamp 按模板 objectName 解析）。
- **驗證**：`t_ft_geom.py` 等價實證 — `git show HEAD:` 起舊版 futu_trade 頁，1280×860 / 1024×700 / 1920×900 三尺寸逐 45 個 widget 比對絕對 geometry → 全部一致（已滾動清理）。`e2e_gui_shell`（56+7 項，含 standalone 外殼）、`e2e_gui_connection`（33 項）、home / favorites / fulltest / indicators / strategies / symbol_list / quotes / futu_trade（起**真** `OneGateWindow()` + live OpenD）全部全綠 ✅。

## 2026-10-08

### MINOR：策略 slim model（刪標的/生效期）+ BUFFER 內訊號改純文字（B紅字/S綠字冇底色）
檔案：`gateway/strategies.py`、`gateway/pages/strategies_page.py`、`gateway/pages/quotes_page.py`、`gateway/indicators.py`、`gateway/i18n.py`、`.scratch/test_strategy_conditions.py`、`.scratch/test_fade_scale.py`、`.scratch/e2e_gui_strategies.py`、`.scratch/e2e_gui_quotes.py`
- **要求**（用戶）：「策略內容刪除標的和生效期」「BUFFER 來的買賣SIGNAL 由淺色改為背景透明純文字..B紅色 S綠字 取消背景圓形及底色」。
- **模型**：entry 刪 `code`/`validity`（連 `created`/`is_active` 狀態鏈一併走 — #30 已全格套用，標的欄本來冇作用）；`add/update/_validate` 跟 slim；舊檔多餘欄 `_sanitize` 容忍 load 直接忽略 = 零遷移。策略頁表單刪標的輸入 + 生效期 combo、表刪 標的/生效期/狀態/創建日（剩 名稱/買/賣 三欄）；i18n 相鍵清理；行情頁下拉 label 由「name · code」改「name」。
- **繪畫**：`_draw_marks` BUFFER 內（對上一個 ≤`mark_buffer` 條，#31 語義唔動）→ 冇 bbox 純文字，B=`gk.C_UP` 紅 / S=`gk.C_DOWN` 綠；組外照舊圓形徽章白字。`MARK_FADE_ALPHA` 退役。
- **驗證**：`test_fade_scale` 加逐個樣式斷言（純文字冇 bbox + 紅/綠字 vs 徽章同色白字）；`e2e_gui_strategies` 改 slim entry／三欄表／容忍 load（52 i18n 鍵 ×3）；`e2e_gui_quotes` Part 6.6 改 @4/@7 純文字紅綠、@2 徽章 + listener 改 BUFFER 即時全徽章；`test_strategy_conditions` 刪生效期段 + 加多餘欄忽略斷言 — 7 個測試全綠 ✅。

### PATCH：BUFFER 語義更正 — 淡化「對上一個訊號相隔 ≤N 條 bar」嘅後續 B/S（cluster）
檔案：`gateway/indicators.py`、`gateway/strategies.py`、`gateway/pages/strategies_page.py`（註釋）、`.scratch/e2e_gui_quotes.py`、`.scratch/test_fade_scale.py`
- **要求**（用戶，screenshot）：「這個B S B..在BUFFER 之內..，後的SB 應該要變淺色」。對返 #29 原文「10條BAR內**出現買賣訊號**，將買賣訊號的顏要淺一點」— 語義係**訊號之間**相隔 N 條 bar，唔係「對最新 bar 計返 N 條」。
- **診斷**：舊實裝 `i >= len(rows) - buf`；真實 app 每格 ~1000 bars（futu `kline_num` 預設）→ 淡化區 = 全資料最後 N 條，zoom 睇中間段永遠全部實色 = 用戶兩度反映嘅根因。
- **改法**：`_draw_marks` 升冪掃 marks 序列：對上一個相隔 ≤`mark_buffer` 條 → 後續訊號 alpha 淡化；組內首個照實色；0 = 全部實色。只睇訊號間距，同 view 位置／資料長度無關 → zoom／平移／串流任何位置都穩定。entry 欄、clamp、UI spinbox、listener 全部唔動。
- **驗證**：`test_fade_scale` 重寫為 cluster 回歸（Part A 200 bars 逐個 alpha = 規則；Part B 合成 marks 證明兩個方向：中間段會淡、最後 N 條內但距遠照實）；`e2e_gui_quotes` Part 6.6 斷言改 @4/@7 淡、@2 實（buffer=3）；`e2e_gui_strategies` / `test_strategy_conditions` / 指標三套回歸全綠 ✅。

### MINOR：策略 B/S 套用全部 K 圖（1/2/4/6）+ 策略改動即時同步（listener）
檔案：`gateway/strategies.py`、`gateway/pages/quotes_page.py`、`.scratch/e2e_gui_quotes.py`、`.scratch/e2e_gui_strategies.py`
- **要求**（用戶）：「BUFFER 的買賣訊號在BUFFER 中没有變淺色」「買賣訊號及指標要同時套用在行程的全部K綫圖1,2,4,6图」。
- **診斷**：淡化機制本身 E2E 已驗證（得最新 bar 對開 N 條內先淡化）；真正缺口 = ①策略只注入標的 match 嘅格 → 其他圖完全冇訊號 ②策略頁做彈出窗時行情頁唔會 showEvent → 改 BUFFER 冇反應（舊 entry 副本）。指標本來已全格注入，唔使改。
- **改法**：`_apply_strategy_to_cells` 去 match gate → 揀咗策略即注入全部 6 格，每格用自己數據計 B/S = 同一套規則逐格同時套用（冇數據嘅格自然空）。`StrategyManager` 加 `add_listener/_notify`（照 `IndicatorManager` 模式，add/update/remove 成功後通知）；行情頁註冊 `_on_strat_config` → 任何 origin（含彈出策略頁）改規則/BUFFER 即時 rebuild 下拉 + re-apply 全部格；改標的唔再需要 re-apply。
- **驗證**：`e2e_gui_quotes` Part 6.6 反轉 cell1 斷言（唔同標的都有 B@2）+ listener 檢查（`update mark_buffer=0` → 唔使重新揀，cell0/cell1 即時全實色）；`e2e_gui_strategies` +2（add/update/remove 通知、失敗唔通知）全綠 ✅；`test_strategy_conditions` / `e2e_gui_indicators` / `test_indicators_common` / `test_ict_suite` 回歸全綠。

### MINOR：策略訊號 BUFFER — 最近 N 條 bar 內嘅 B/S 標記淡化（N 喺策略頁可調）
檔案：`gateway/strategies.py`、`gateway/pages/strategies_page.py`、`gateway/indicators.py`、`gateway/i18n.py`、`.scratch/test_strategy_conditions.py`、`.scratch/e2e_gui_strategies.py`、`.scratch/e2e_gui_quotes.py`
- **要求**（用戶）：「買賣訊號要加一個BUFFER …10條BAR內出現買賣訊號，將買賣訊號的顏要淺一點…呢個數字可以在策略里調整」。
- **模型**：entry 新欄 `mark_buffer`（int 0–200，預設 10，0 = 全部實色）；`clamp_mark_buffer` 單一 clamp 點；`_sanitize` 容忍舊檔自動補預設 = 零遷移；`add/update` 加可選參數（update None = 唔改該欄）。
- **UI**：策略頁表單加「訊號BUFFER(條)」QSpinBox（`str_buffer_spin`，0–200）— 新增/回填/清空/套用全部跟現有表單鏈；i18n `str_buffer_lbl` 三語。
- **繪畫**：`_draw_marks` 現讀 `len(rows)` → `i >= len(rows) - buf` 嘅徽章 Text+bbox 一齊 `alpha=MARK_FADE_ALPHA(0.45)`；只影響繪畫，marks cache 契約唔動 → 串流加 bar 舊標記自動變實色。順手修正：`set_strategy(None)` 殘留舊 marks（而家清返空）。
- **驗證**：`test_strategy_conditions` +2（clamp／舊檔補欄）；`e2e_gui_strategies` +5（預設 10、update roundtrip/clamp、spinbox 存在、JSON mark_buffer、回填+跟改）；`e2e_gui_quotes` Part 6.6 改 mark_buffer=3 → 斷言得 @7 淡化（text+bbox alpha）、@2/@4 實色，徽章色斷言改比較 fc rgb 部分（alpha 喺 a 通道）全綠 ✅；`e2e_gui_indicators` / `test_indicators_common` / `test_ict_suite` 回歸全綠。

### PATCH：B/S 標記改圓形徽章（紅/綠底白字）
檔案：`gateway/indicators.py`、`.scratch/e2e_gui_quotes.py`
- **要求**（用戶）：「B 和S 標記改為圓形紅色白字，圓形綠色白字圖案」。
- **做法**：`_draw_marks` 喺 Text 加 `bbox=dict(boxstyle='circle,pad=0.3', fc=…, ec='none')`，字色改 `#FFFFFF`；底色直接用 `gk.C_UP`（紅）/`gk.C_DOWN`（綠）語義色，唔引入新 hex。位置/偏移/清除鏈完全唔動（bbox 隨 Text artist 一齊走）。
- **驗證**：`e2e_gui_quotes` Part 6.6 新增 #29 斷言（B/S 白字 + bbox fc == C_UP/C_DOWN）；`test_indicators_common` / `test_strategy_conditions` 回歸全綠 ✅。

### MINOR：MA 逐條線顯示開關（CHECKBOX）+ 行情頁「指標選項」menu 逐個揀
檔案：`gateway/indicators.py`、`gateway/pages/indicators_page.py`、`gateway/pages/quotes_page.py`、`gateway/i18n.py`、`.scratch/test_indicators_common.py`、`.scratch/e2e_gui_indicators.py`、`.scratch/e2e_gui_quotes.py`
- **要求**（用戶）：「MA 四個週期每個加CHECKBOX 可以切換是否顯示，行中顯示指標要個別選擇」。
- **單一事實來源，唔新增狀態層**：MA 逐線可見性 = 執行個體參數 `show1..4`（`ParamSpec.is_bool` 新欄，預設 1）→ 管理頁參數欄自動出 QCheckBox；`compute_ma` 對 showN=0 **唔輸出該 key** → 唔畫、唔入 Y-fit（`_plot_ma` 用 `.get`）；`_params_summary` skip bool → 摘要/panel 標題照舊「5/10/20/60」；`_sanitize` 自動補預設 = 舊存檔實例零遷移。改參數 → config_version bump → cache 失效 = 串流照同步。
- **行情頁**：頂欄加「指標選項」menu（QToolButton+QMenu，aboutToShow 每次重建）— 逐個指標 checkable→`set_enabled`（食 manager 嘅 enabled，同 K線頁掣列／管理頁 checkbox 天然雙向同步）；MA 實例 sub-menu 四條線→`update(params={'showN'})`。總開關保留＝頁面級 all-off，唔碰全局；總開關關住 menu 灰咗。i18n 新增 `ind_p_show1..4`/`ind_n_ma_show`/`quotes_ind_menu` 三語。
- **驗證**：`test_indicators_common` +5 項（showN=0→key 缺席／缺 key 當顯示／is_bool clamp／摘要 skip）；`e2e_gui_indicators` +5 項（管理頁 checkbox 新增/回填/套用 + 摘要唔變）；`e2e_gui_quotes` 新增 Part 6.7（menu 逐實例 checkable→enabled 同源、MA sub-menu show2=0→cache 冇 ma2 + artist 少一條、開返返 cache）全綠 ✅；`test_ict_suite` / `e2e_gui_strategies` 回歸全綠。

### MINOR：行情頁 — 顯示指標總開關 + 策略 B/S 買賣標記（串流自動同步）
檔案：`gateway/pages/quotes_page.py`、`gateway/indicators.py`、`gateway/strategies.py`、`gateway/i18n.py`、`.scratch/test_strategy_conditions.py`、`.scratch/e2e_gui_quotes.py`
- **要求**（用戶）：「在行程頁面增加顯示指標選項，另外增加一個策略選項，點選那個策略，就在K圖中的買點賣點做B 和 S 的標記，因為K綫是串流的，K綫刷新了也要將指標和策略買賣點同步更新」。
- **同步靠結構**：6 格 chart 換 `IndicatorKlineChart`；指標 cache 與新增 marks cache 都以 `_data_seq` 為 key，`set_bars`（串流必經）先 bump → 任何 K 線刷新自動重算，唔需要手動 refresh。
- **B/S 契約**：`strategies.trade_marks(entry, ohlc)` 純函數 = `score_series`→`trigger_indices`（同 live 觸發一模一樣），價＝觸發根收盤，輸出升冪 `[(bar, 價, 'B'/'S')]`；chart `set_strategy(entry|None)` 畫標記（B 喺低點下面、S 喺高點上面），指標關咗照畫、artist 清除同一條鏈。
- **頁面**：頂欄「顯示指標」checkable 掣（開＝注入 manager，跟指標管理頁配置，listener 即時跟）+ 策略下拉（無策略＋清單）；逐格標的 canonical == 策略 code 先注入；`ind_shown`/`strategy` 入 state_store 'quotes' 記憶；i18n 3 鍵三語。順帶 FIX：改標的時清 `_pending_df` — 舊標的 pending rows 唔會 250ms 後蓋住錯誤 label（原本 e2e flaky 根源）。
- **驗證**：`test_strategy_conditions` +6 項 trade_marks；`e2e_gui_quotes` 新增 Part 6.6（開關砌/拆 panel、dropdown match、B@2→串流 8 bars 自動變 B@2 S@4 B@7、關指標 B/S 照留、記憶還原）全綠 ✅；`e2e_gui_indicators` / `e2e_gui_strategies` / `test_ict_suite` / `test_indicators_common` 回歸全綠；`e2e_gui_fulltest` 4 項 stream 檢查為 live 網絡偶發延遲（重跑全過，路徑冇改動）。

### MINOR：指標管理 — 常用指標 MA（主圖 4 條 SMA）/ KDJ / RSI（副圖）
檔案：`gateway/indicators.py`、`gateway/i18n.py`、`.scratch/test_indicators_common.py`、`.scratch/test_ict_suite.py`
- **要求**（用戶）：「指標加一個MA、MACD KDJ 等常用指標」。MACD 已內置，實際新增 MA / KDJ / RSI；管理頁由 registry 自動生成 def combo／參數欄，**零 UI 改動**。
- **MA**：一個 def、4 個週期參數（預設 5/10/20/60，尊重總 6 上限），同 BOLL mid 同一 pandas `rolling` 契約；**唔入 `FAR_OVERLAYS`**（貼價線照舊全量參與 Y-fit）。**KDJ**：華語慣例 — RSV=(c−LLV)/(HHV−LLV)×100（平穩段=50），K/D 遞推 `K+=(RSV−K)/m` seed 50（唔係 simple mean／ewm），J=3K−2D。**RSI**：Wilder seed+遞推（同 `compute_atr` 風格），只升=100／全平=50 如實。圖色只用現有 palette 常量。
- **i18n**：`ind_desc/use_ma·kdj·rsi` + 新 `ind_p_p1..p4/n/m1/m2/period` + `ind_n_*`（三語）；指標名語種中立 acronym，唔使 i18n。
- **驗證**：新增 `test_indicators_common` 16 項（MA/KDJ/RSI 逐個手砌期望值 + MA==BOLL mid 同契約 + J 恆等式 + registry 掛鉤）；`test_ict_suite` registry 斷言跟更新（17 def、FAR_OVERLAYS 排除 MA）；`e2e_gui_indicators` 全通過 ✅。

### One Gate 策略管理頁（Page 8）— 分數制條件（MA/BOLL/VOB 累加 ≥100 觸發）+ BACKTEST 兼容儲存
檔案：`gateway/strategies.py`、`gateway/pages/strategies_page.py`、`gateway/i18n.py`、`gateway/app.py`、`.scratch/test_strategy_conditions.py`、`.scratch/e2e_gui_strategies.py`
- **要求**（用戶，兩輪）：主選單加「策略」頁 — 新增/修改/刪除、本地保存、兼容日後 BACKTEST；標的、買入條件（如 MA20>MA120）、賣出條件、VOB/BOLL 穿線、買入價=市價、數量=最低一手、生效期 1日/7日/永久。第二輪改做**分數制**：「買賣每個條件按不同的分數，累加起來超過買100分或賣100分就交易」。
- **領域層** `gateway/strategies.py`：`CONDITION_DEFS`（ma_cross / boll_cross / vob_break，`CondParamSpec` 範圍 + 計算直接重用 indicators `compute_boll`/`compute_vob`）；**分數契約** `rule_signal`（per-bar bool）→ `score_series`（分數累加）→ `trigger_indices`（≥100 rising edge）— rules 純 JSON，live 與 backtest 同一份；`entry_price:'market'` / `qty:'min_lot'` 存 mode enum（一手量執行時先解析）；生效期 `is_active`；`StrategyManager`（dedupe / 部分 update / 容忍 load）→ state_store section `strategies`。
- **頁面** `gateway/pages/strategies_page.py`：表格（含語言中立條件摘要 `MA20>MA120 (+60)` / 生效期 / 狀態即時）+ `_CondEditor`（類型/方向/動態參數欄隨 spec 生成/分數 SpinBox，draft 行 + ✕ 移除）+ 標的模糊輸入 + 三語；app.py 註冊 PAGE_KEYS / NAV_DIRECT。
- **驗證**：`test_strategy_conditions` 21 項 + `e2e_gui_strategies` 33 項（hermetic：Manager CRUD / 頁面 CRUD / JSON 結構 / 過期顯示 / 三語 + 65 i18n 鍵 × 3 語言 / shell 註冊）全部通過 ✅。

### FIX：FVG 區塊重寫 — 近邊填平 + 同向取代（唔准斷續、唔准拖太長）
檔案：`gateway/indicators.py`、`gateway/i18n.py`、`.scratch/test_ict_suite.py`
- **要求**（用戶，兩輪）：「FVG 有斷續」→「不應讓間斷也不應該那麼長」。真數據（HSI 1M）診斷：① 同向區塊重疊落 `_zones_to_arrays` 被「較新者覆蓋」逐 bar 切走 → 一個區塊砌成幾截；② 填平條件係「完全填平」（low ≤ 區塊底 / high ≥ 區塊頂），單邊行情下區塊一直畫到最後一根（實測 26-28 根）。
- **修法**（對齊 OB 家族已確立嘅語義）：① **近邊填平** — 價格返身入缺口即結束（睇多 `low < 區塊頂` / 睇空 `high > 區塊底`），掃描一律由確認根（i+2）開始，三根形態本身唔算填平；② **同向取代** — `_supersede`：較新同向 FVG 出現即終止舊者 → 同向必然唔重疊 → 每個區塊完整一個方塊。i18n `ind_use_fvg` 文案同步。
- **驗證**：`test_ict_suite` [FV] 改近邊語義 + 新增 [FVS]（兩個同向缺口 → 兩截完整無斷續）；真數據 dump：所有區塊 1-5 根、零切割（舊版：5 截碎片 + 26 根長條）；`e2e_gui_indicators` 全通過（Part 9 斷言在新語義下陣列值不變）。

### MINOR：K 線圖平移順暢度 — 拖動凍結 Y 尺度 + 逐幀平滑平移
檔案：`gateway/pages/gui_kline.py`、`.scratch/e2e_gui_indicators.py`、`.scratch/bench_kline_pan.py`
- **要求**（用戶）：「縮放是流暢了，平移還是不順。」
- **先量度（`.scratch/bench_kline_pan.py`：1200 根、1400×700、模擬 125 Hz 拖動 1.25 s）**：跟手滯後 **0 根**、幀率 **54.8 fps**（已係 Agg 底線，見上一條）→ **唔係 lag、亦唔係幀率**。🤖 **真正元兇：拖動期間 Y 軸逐幀重 fit** —— 垂直尺度每 25 個 event 變 0.933× / 0.952× / 1.047×，橫移 + 同時垂直缩放 = 成張圖「郁郁下彈下」；縮放之所以順，係因為 #23 已經將變化攤平喺 220 ms。對照：`_apply_view` 行咗 **137 次但只上屏 75 次** → 每個 mouse event 都重算一次 limits/Y/ticks，一半白做。
- **Y 凍結**：`_lock_y()` 喺 `_on_press` snapshot `(ax.ylim, axv.ylim)`，`_apply_view` 拖動／滑行期間一律用鎖定值；松手（無滑行）即刻 `_unlock_y()` 一次過 refit，有滑行就喺 `_anim_tick` 終止先 refit。`_on_scroll` / `set_bars` / `clear` 一律解除（縮放／換數據 = 明確要重縮放）。
- **平滑平移**：`_on_motion` 唔再 1:1 直接改 `_view`，改設 `_pan_target`；`_pan_tick()` 每幀指數追近（`PAN_EASE = 0.55` → ~2 幀收歛 ≈ 30 ms，感覺唔到滯後，但每幀位移細 = 滑動而唔係跳格）。🤖 用「逐幀比例」而**唔係時鐘插值**：拖動係連續 input，時鐘插值會越拖越落後。任何要讀取「而家視窗」嘅動作（松手 / 縮放 / 再起 drag / 過渡）之前一律 `_flush_pan()` 貼實，唔然會由一個落後咗嘅位置開始計。
- **順帶修正**：`_apply_view` 嘅 out-of-range guard 由「丟返跟隨模式」改做 `_clamp_view()`（**保留用戶嘅縮放級別**）—— 舊做法喺數據變少／取消過渡後會突然將用戶拉返最新 + 換尺度；手勢進行中（`_anim` / `_pan_target`）一律跳過 guard（#23 同一個坑，平移都會撞右緣 gutter）。每一條幀路徑一律經 `_frame()`（唔能直接 `_apply_view()`：子类靠呢個 hook 重畫指標疊加）。
- **效果**：`_apply_view` 137 → **75**（= 上屏次數，零白做）、拖動期間 Y 範圍 **1.000× 恒定**、幀率 54.8 → **56.0 fps**；成本係穩態跟手落後 **中位 3.3 根 / 最差 5.3 根**（~60 ms，`PAN_EASE` 係調校掣）。
- **驗證**：e2e 新增 Part 14（11 項）— 拖動中（連同逐幀追近）Y 範圍逐個位恒定 + 每個 event 唔再重算（`_apply_view` 次數 == 幀數）、松手後一次過 refit 返新窗口、逐幀單調追近 + 零 overshoot + ≥3 幀收歛 + 最終誤差 0、每幀都行 `_frame()`、中途滾輪／`set_bars` 即刻解除凍結、未追完嘅平移喺下一個手勢之前必貼實。`e2e_gui_indicators` / `test_ict_suite` / `e2e_gui_quotes` / `e2e_gui_home` / `e2e_gui_fulltest` / `cli_indicators_live`（真數據）/ `diag_vob_zones` 全部 exit 0 ✅。
- 🤖 **測試手法**：假時鐘必須用返程式碼同一個 clock —— 呢度先用咗 `time.monotonic()` 砌 25% 進度，但程式碼計時係 `perf_counter()`，兩個 clock 差咗 ~26 ms → 「縮到全部數據」嗰項一直 flaky；另外位移閾值要喺**距離大嗰邊**量（xmin 軸 = 成幾百根），喺 xmax 量得幾個位一定 flaky。

### MINOR：K 線圖過渡動畫（eased 縮放/復位 + 松手慣性）+ 每幀成本再削 → 18.9-20.4 ms/幀
檔案：`gateway/pages/gui_kline.py`、`gateway/indicators.py`、`.scratch/e2e_gui_indicators.py`、`.scratch/bench_kline_redraw.py`
- **要求**（用戶）：「是快了，但有方法可以過渡更順暢嗎？偵率高點流暢，類似動畫效果」。
- **先量度再拍板**：一幀 31 ms = 幾何 8 ms + **axes chrome（grid/tick 文字）23 ms** + figure 1.2 ms。🤖 **matplotlib Agg 喺 1400×700 有 ~20-25 ms 底線（≈40-50 fps），淨係優化繪畫去唔到 60fps**；真 60fps 要換 renderer（QPainter / OpenGL / mplcairo）= 大工程，是次唔做。所以改做「動畫感」：每幀位移細 + 有加減速。
- **過渡**：`_animate_to(target)` / `_anim_tick()` — ease-out cubic、`ANIM_MS=220 ms`、16 ms tick；滾輪縮放、右鍵/雙擊復位（`follow_at_end` 終止先 `_view=None`）、慣性滑行全部行呢條路；連續滾輪由「而家顯示緊嘅位置」接續 → 唔會跳；`_on_press` 即刻取消（跟手優先）。
- **慣性**：`_inertia()` 取最近 ≤120 ms 嘅拖動樣本算速度，滑行量 = v×0.2，clamp 喺數據範圍內；慢拖／停低先松手 → 速度自然接近 0，唔會亂郁。
- **每幀再削**：同色（漲/跌）合併成**一個 compound Path**（`_rect_path`/`_segs_path` → 全圖只有 3 個 `PathCollection`）；指標區塊 `_zone_rects` 同樣合併（47 個 collection → 1，4 個 ICT 疊加 −9 ms/幀）；`MaxNLocator(6)` 主圖 y 刻度 + x 時間標籤 7 → 5（`X_TICKS`，子类跟返）。
- **效果**：純 K 線一幀 28.7 → **18.9-20.4 ms**（≈50 fps）、4 個 ICT 疊加 64 → **21.5-42.1 ms**、hover 照舊 **0 ms**；對照「只改 `set_xlim`」= 17.1-19.2 ms → 已貼住 renderer 底線。
- 🤖 **本次引入嘅 regression（用戶抓到：「OB 變三角形了，不是方形」）**：compound Path 用 4 個頂點配 `[M,L,L,CLOSE]` → **`CLOSEPOLY` 唔會用自己嗰個頂點**，實測填到面積 5050/10000 px = 一半，即係**三角形**。蠟燭實體、成交量柱、ICT 區塊全部中招（細個嘅燭身睇唔出，大塊 OB 變楔形好明顯）。修法：一律 5 點（尾點 = 起點重複）+ `[M,L,L,L,CLOSE]`；`_rect_path` / `_zone_rects` 兩處同步。🤖 教訓：合併 path 嘅測試淨係數 artist 數同頂點數係唔夠，要斷言**幾何本身**（e2e 新增：每個 sub-path 必須 5 點、尾點 == 起點、4 個唔同角）。改完 benchmark 冇變（18.9-20.1 ms/幀）。
- 🤖 **測試逼出三個真 bug（全部已修，唔止係測試期望）**：① 出界嘅 x 時間刻度會**撐開 axes limits**（實測 ±1 根）→ 過渡終點永遠收唔到 target，新增 `_view_ticks()` 統一 clamp（`_apply_view` 同子类標籤遷移共用）；② `_apply_view` 嘅 out-of-range guard 逐幀將 `_view` 打返 `None`（跟隨模式起點 xmax > n−0.5 = 現價 tag gutter）→ 「縮到全部數據」成段動畫等於冇郁，動畫進行中跳過 guard；③ **Windows 嘅 `time.monotonic()` 實測只有 ~16 ms 分辨率**（`perf_counter` ~30 µs）→ 拖動樣本 `dt` 恒為 0、慣性永遠唔會觸發，手勢/動畫計時一律改 `time.perf_counter()`。
- **驗證**：e2e 新增 Part 13（6 項）— 插值進度嚴格單調且每段都比線性快（ease-out）、終點逐個位 == target 並真係上到屏（`xlim == target`）、動畫期間靜態層冇重建、press 即刻取消、由跟隨模式縮到全部數據真係有中間態、慣性目標 clamp 喺數據範圍內 + 終點 == 目標 + 方向跟住拖動、慢拖唔滑。Part 9 嘅區塊斷言改跟 compound Path 契約（計 `CLOSEPOLY` sub-path，唔再計 path 物件數）。`e2e_gui_indicators` / `test_ict_suite` / `e2e_gui_quotes` / `e2e_gui_fulltest` / `e2e_gui_home` / `diag_vob_zones`（#21 Y-fit 閘 1.12× 冇變）全部 exit 0 ✅。
- 🤖 **測試手法教訓**：呢個 e2e 進程好重（一次 `pump()` ≈ 200 ms 實時）→ 時序契約要用「假時鐘」直接 drive `_anim_tick`，唔好靠真 QTimer 取樣。

### MINOR：K 線圖繪畫層重構（兩層）— 縮放/平移 241 ms/幀 → ~29 ms
檔案：`gateway/pages/gui_kline.py`、`gateway/indicators.py`、`.scratch/e2e_gui_indicators.py`、`.scratch/bench_kline_redraw.py`
- **要求**（用戶）：「K 綫圖的縮放和平移都非常不順」→ 拍板 A+B+C 一齊做。
- **成因（量度，唔係估）**：`_redraw` 每幀 `ax.clear()` + `ax.bar` → **每根 bar 一個 Rectangle patch**（300 根 = 600 patches、1000 根 = 2000）。基線（1200 根合成、offscreen 同步 `canvas.draw()`）：300 根可見 = **241 ms/幀**、1000 根 = **765 ms**、郁吓滑鼠 = **181 ms**；對照「只改 `set_xlim`」= 47 ms → bottleneck 係**每幀重建 artist**。指標疊加唔係主因（4 個 ICT 只多 ~35 ms）。
- **A 兩層繪畫**：`set_bars` 一次轉全長度 numpy 陣列；`_rebuild_static()` 把**全部** bar 一次過畫成 **3 個 collection**（影線 `LineCollection` 逐段上色、實體／成交量 `PolyCollection` 逐 bar facecolor）+ 預建現價線/標注/crosshair，只有數據、axes 或 **theme 配色**變咗先重建；`_apply_view()` 做視窗層（xlim + 由可見 slice 重算 Y fit + ticks + tag 位置）。x 一律用**絕對 index**（`_s` 恒為 0 → 子类 slice 契約 `arr[_s+i0:_s+i1]` 照樣啱）。
- **B hover**：crosshair 預建，郁滑鼠只 `set_xdata` + readout + `draw_idle()`，一次 `_redraw` 都唔叫；指標 panel 嗰啲 crosshair 同樣預建跟隨（`_panel_cross`）。
- **C 手勢節流**：`_request_redraw()` — 狀態 + limits 即時更新（手勢數學永遠跟到最新、測試照舊同步），上屏用 pending flag + `QTimer.singleShot(16ms)` 合併到 ~60fps。
- **子类同步**：parent 唔再 `ax.clear()` → `IndicatorKlineChart` 自己追蹤 `_ind_artists` 逐個 `remove()`；改寫 `_frame` hook（而唔係 `_redraw`）先至食到手勢節流；`_build_axes()` 後標記 `_static_dirty`。
- **效果**：一幀 241 → **28.7 ms**（300 根）、765 → **28.9 ms**（1000 根）、hover 181 → **0 ms**；開 4 個 ICT 疊加 775 → 64 ms。而家等同「只改 xlim」嘅純渲染底線，而且**同可見/總根數近乎無關**（總 300 根 21 ms、3000 根 38 ms → 剩低嘅係 axes chrome 固定開支）。
- 🤖 **未做**：超過 ~1000 根唔做抽稀/聚合（`MAX_VIEW` 照舊）— 實測 3000 根先 38 ms，未值得加複雜度。
- **驗證**：e2e 新增 Part 12 — pan+zoom 後靜態層 artist **逐個同一個物件**、`_static_dirty` 保持 False、Y fit 跟住可見 slice；指標 artist 全部喺新可見窗內 + 連續 6 幀數量恒定（無疊加洩漏）；hover 零 `_redraw`、主圖 + panel crosshair 一齊跟、離開收埋；改 `gk.C_*` + `_redraw()` → 靜態層識得自己重建（theme recipe 唔破）；`set_bars` 換數據 → 重砌且畫晒全部 200 根。Part 11 嘅 `collections > 0` 收緊為**增量 == 1**（蠟燭本身都係 collection，唔准再睇絕對數量）。`test_ict_suite` / `e2e_gui_indicators` / `e2e_gui_quotes` / `e2e_gui_fulltest` / `e2e_gui_home` / `cli_indicators_live`（真數據）/ `diag_vob_zones`（Y-fit 閘 1.66→1.12 冇變）全部 exit 0 ✅。

### MINOR：OB 家族新參數 `supersede` — 同方向出現更新嘅 OB 即取代舊區塊
檔案：`gateway/indicators.py`、`gateway/i18n.py`、`.scratch/test_ict_suite.py`、`.scratch/e2e_gui_indicators.py`、`.scratch/diag_vob_zones.py`
- **要求**（用戶，附截圖）：「同一類 OB 出現了，之前的 OB 是不是應該消失」——圖上兩個同價位嘅空頭區塊並排出現。
- **語義交代**：ICT 本身**唔係**咁規定的 — OB 係被價格消耗（即 `pen`）判死，唔係被新 OB 取代；但 TradingView 嗰類 OB indicator 普遍係「每邊只顯示最新一個」，而用戶要嘅正係呢個。所以做參數、預設 1（可關 = 舊行為 / OB map）。
- **實作**：`_supersede(zones)` — 按 start 排序後，較新者出現即把之前所有**同向**區塊 `end = start − 1`，終止得太短嘅剔除；唔同方向互不相干。`_ob_zones(..., supersede=True)` 同 `compute_breaker` 一併接入（OB / VOB / BRK 三個 def 同時暴露，規則一致）。
- 🤖 **順帶解決咗之前未拍板嘅「OB 中途冇穿但不見了」**：`_zones_to_arrays` 係「每根每邊一個值」，舊區塊被較新者覆蓋 → 睇落中途斷。`supersede=1` 令同向**必然唔重疊** → 覆蓋呢條路永遠行唔到，陣列模型變成準確，**唔需要做多 slot `(n, S)`**（呢個選項正式取消）。
- **效果（真 HSI 300 根 K_15M）**：`空 197..299` + `空 221..299`（兩個重疊、都拖到最後一根）→ `空 194..194`、`空 195..218`、`空 219..299`（各自完整、新者出現即止）；多頭 `267..278` → `279..297` 同樣接力。同向重疊對數：1 → **0**。
- **驗證**：`test_ict_suite.py` 新增 [I] — 用 flat 陣列（永遠唔會被消耗）直接喂 `_ob_zones`：關 = 三個各自畫到結尾並重疊；開 = 舊多頭喺新者前一根（11）終止、新者照畫到結尾、空頭唔受影響、同向零重疊；落陣列 bar11 仍係舊 level、bar12 變新 level；隨機 400 根（25 候選 → 15 區塊）零重疊；registry 斷言三個 def 嘅 `supersede` 範圍/預設一致。e2e 參數欄 7 → 8 個、摘要改 `14/1/5/5/3/50/1/15`。i18n +2 key（名 + 解釋 × 三語，共 69 key）。`test_ict_suite` / `e2e_gui_indicators` / `cli_indicators_live`（真數據 300 根，嚴格斷言全開）全部 exit 0 ✅。

### MINOR：主圖 Y-fit 距離閘 — 離價好遠嘅 ICT 區塊唔再撐大 Y 軸
檔案：`gateway/indicators.py`、`.scratch/e2e_gui_indicators.py`、`.scratch/test_ict_suite.py`、`.scratch/diag_vob_zones.py`
- **要求**（用戶，附截圖）：「你睇下有啲 VOB 係獨立出來，跟 K 綫冇連接同關係嘅」。
- **成因**：區塊/水平位畫嘅係**歷史價位** — 早期形成、收盤從未進入嘅 OB 永遠有效（`end = n-1`），價位可以離可見窗好遠；而 `_draw_indicators` 嘅主圖 Y-fit 會將疊加嘅**全部**可見值計進去。真 HSI 300 根 K_15M、睇最後 25 根：兩個 `24278–24295` 空頭區塊離可見 K 線 **154 點 = 可見範圍 57%**，Y 軸被撐到 K 線範圍嘅 **1.66 倍** → 蠟燭縮晒 + 中間一大片空白。
- **實作**：`FAR_OVERLAYS`（11 個 ICT main 疊加）+ `FIT_PAD = 0.25` + `_fit_vals(arr, lo, hi, pad)` — 呢啲疊加只有喺「可見價格範圍 ±25%」內嘅值先參與 Y-fit。🤖 唔係唔畫、亦唔改偵測：方塊照樣砌（兩張截圖 artists 數量一致），只係唔再為佢擠細 K 線；往左平移返去嗰個年代，區塊自然出現喺佢應該喺嘅位置。貼價指標（BOLL/ATR/MACD）完全唔入閘，行為唔變。
- **效果（真 HSI 300 根 K_15M，睇最後 25 根）**：Y 軸 1.66 倍 → **1.12 倍**（即 parent 自帶 6% 邊距），2 個離譜區塊被隔走。對比截圖 `.scratch/vob_far_off.png`（舊）vs `.scratch/vob_boxes_live.png`（新）。
- **驗證**：e2e 新增 Part 11 — 臨時 def 砌「成個可見窗都係同一個價位」嘅區塊：開閘 → **Y 軸逐個位唔變**但方塊照樣畫出（collections > 0）；閘關咗 → 同一個區塊確實撐大 Y 軸（證明係距離閘做功）；喺 FIT_PAD 內（+10%）嘅區塊仍然參與 fit；移除臨時 def 後還原。`test_ict_suite.py` 新增 [S] — `_fit_vals` 邊界（入閘 / 剔除 / 全 NaN / 唔改範圍內值）、`FIT_PAD ∈ (0,1]`、`FAR_OVERLAYS` 恰好 = 全部 ICT main 疊加且唔含 BOLL/ATR/MACD。兩套全綠 ✅。
- 🤖 **未處理**：用戶另一條「OB 中途冇穿但不見了」係 `_zones_to_arrays` 嘅「較新者覆蓋」（同向重疊區塊淨低最新嗰個嘅 level）→ 要逐個保留自己完整有效期需要將陣列做**多 slot 並存** `(n, S)`，未做。

### MINOR：所有 K 線圖 — 左鍵雙擊還原縮放 + 即刻返到最新 K 柱
檔案：`gateway/pages/gui_kline.py`、`.scratch/e2e_gui_indicators.py`
- **要求**（用戶）：所有 K 線圖連擊兩下可以還原縮放大小，並移到最新嘅 K 柱。
- **實作**：`KlineChart._on_press` 收 `ev.dblclick` → `_view = None`（跟隨最新，即右鍵復位嗰條路）+ 清 `_drag`/`_hover_idx`。🤖 已核對 `backend_qt` 源碼：Qt 嘅 `mouseDoubleClickEvent` 會以 `dblclick=True` 派 `button_press_event`，唔使自己計時；🤖 分支必須喺設 `_drag` 之前，唔然第一次 click 已開始嘅拖動狀態會留低；用 `getattr(ev, 'dblclick', False)` 係為咗手砌 fake event 嘅測試照樣行得。
- **覆蓋面**：改喺 base class → 獨立 K 線頁、K線頁 `IndicatorKlineChart`（連指標 panel 都計，子类 `_on_press` 已改寫 inaxes）、行情頁 6 格全部自動一樣。
- **驗證**：e2e 新增 3 條 — 主圖雙擊（先滾輪縮放令 `_view` 有值 → 雙擊後 `None`）、指標 panel 上雙擊同樣有效、單擊 `dblclick=False` 唔會復位（照樣拖動）；`e2e_gui_indicators` 全綠 ✅。

### MINOR：指標管理 — ICT 全套（BOS/CHoCH/LIQ/EQHL/PD/OTE/BRK/BPR）+ 每個指標/參數一行說明（可摺疊）
檔案：`gateway/indicators.py`、`gateway/i18n.py`、`gateway/pages/indicators_page.py`、`gateway/pages/kline_page.py`、`.scratch/test_ict_suite.py`（新）、`.scratch/e2e_gui_indicators.py`、`.scratch/cli_indicators_live.py`

- **要求**（用戶）：增加 ICT 所有常用指標，一樣可參數化；所有指標都要有一行描寫同點樣用，喺指標管理列表顯示；每個參數詳細解一行，可摺疊顯示全部。
- **新增 8 個 def（合共 14 個）**：`BOS`/`CHoCH`（收盤穿過最近已確認拐點；方向逆住上一個突破者 = CHoCH）、`LIQ`（影線穿過前高/低但收盤返返入面 = Stop Hunt；收盤都出界即「位已消費」，唔再當掃蕩）、`EQHL`（兩個相距 ≤ `tol`×ATR 嘅同類拐點 = 流動性池）、`PD`（回看 `lookback` 根 dealing range + 50% 均衡線）、`OTE`（推進段 `fib_lo`–`fib_hi` 帶，預設 0.62–0.79）、`BRK`（OB 偵測完全重用 `_ob_candidates`，只畫被收盤着穿之後嗰段並反轉方向）、`BPR`（兩個反向 FVG 嘅重疊部分）。全部參數化（拐點半徑 / 最多水平位 / 等高容差 / fib 上下緣 / 回看根數…）。
- **共用 helper**：`_swings`（fractal 拐點）、`_structure_breaks`、`_levels_to_arrays`/`_level_spans`、`_fvg_candidates`（FVG 同 BPR 共用，抽走重複）。
- **契約延伸（照 #20 同一個 compute 契約）**：水平位 = `*_top == *_bottom` == 價位 + `mark_bull/mark_bear`（標記嗰根嘅價位）；PD = `range_hi/equilibrium/range_lo`。🤖 一律用價格值，mark 陣列都唔准用旗標 — 主圖 Y-fit 照樣啱。繪畫 `_plot_levels`（逐根 `hlines` 自然連成橫線 + `^`/`v` 三角 + BOS/CHoCH tag）、`_plot_band`（淡色 dealing range + hi/lo 細線 + 均衡虛線）。
- **說明**：`IndicatorDef.desc_key`（一行描寫）/ `usage_key`（點樣用）+ `ParamSpec.note_key`（每個參數一行）。管理頁表格加「**說明**」欄（Stretch 欄、一行、跟語言；完整用法喺 tooltip）+ **可摺疊詳情面板**（`ind_detail_toggle` 預設收起；展開 = 完整用法 + 每個參數一行 `ind_detail_note_<key>`，永遠跟頂欄類型 — 新增時未有任何行，所以唔跟表格所選）。K線頁開關掣 tooltip 帶一行描寫（`_ind_tooltip()` 合併咗原本兩處重複拼接）。i18n 新增 63 個 key × 三語。
- 🤖 **事實與限制**：① 拐點要 k 根之後先確認到 → 結構類指標天然滯後 k 根（ICT 本身係事後確認，唔係預測）；② 第一個突破永遠唔算 CHoCH（冇參照方向）；③ PD 需要 ≥ `lookback` 根先有值 — 預設 100，得 60 根嘅 live 串流必然全 NaN（正確行為，測試改用 `lookback=30`）；④ CHoCH 喺一段純趨勢 60 根可以合法為 0 → live 斷言只驗結構一致（標記 ⊆ 位），唔驗數量；⑤ 時間類 ICT（Kill Zones / 亞洲盤 / 日開高低）冇做 — `compute` 只收 o/h/l/c，要再加時間欄 + x 範圍繪畫形態。
- **驗證**：`.scratch/test_ict_suite.py`（新）55 checks 全綠 — 7 個手砌 K 線 fixture 逐條斷言（拐點清單 / BOS+CHoCH 邊個算逆勢 / 掃蕩 vs 真破位 / 等高等低 tol 過濾 / dealing range 與均衡 / OTE fib 帶起訖與 fib 反轉防呆 / BRK 只畫失效之後 / BPR 重疊部分）+ registry（14 個 def 全部有 desc/usage/note、63 key × 三語非空、參數預設喺範圍內、隨機 400 根全部唔炸）。e2e 新增 Part 10 全綠（8 個新 def 逐個加入 → 主圖 artists 增量**逐個等於按 `_PLOTTERS` 契約算出嘅期望**：bos = 2 橫線 + 2 三角 + 10 tag、pd = 1 fill + 3 線…；main 唔砌 panel；說明欄/tooltip/展開收起/轉類型即時跟/三語）。live smoke 全綠（真 HSI K_1M：BOS 2 段、LIQ 10 段、EQHL 2 段、PD 171 根、OTE/BRK/BPR 都有區塊；截圖 `.scratch/ict_structure_live.png`）。舊 suite indicators/futu_trade/p8/home/favorites/quotes/symbol_list/fulltest/test_symbol_search_fuzzy 全部 exit 0。
- **跟進（用戶反映「VOB 區域連續、唔係獨立方塊」）**：成因確認 — 同向區塊重疊時陣列係「較新者覆蓋」，level 喺中途跳，但舊繪畫只按 NaN 斷段 → 真 HSI 200 根上 `[94..199]` 一段內含 6 個唔同 level 砌成一大片。修法 = `_zone_boxes()`：喺 level 變化位切段，每段 top/bot 必然恆定 → 每個方塊一個 `fill_between` 矩形（±0.5 覆蓋自己嗰啲根，單根區塊都睇到）。🤖 陣列契約唔動（純繪畫層）；順帶移除舊 `np.where(m, bot, 0.0)` NaN hack。e2e 期望同步改為按方塊數計（`exp_boxes` 獨立實作）+ 新增一個手砌重疊 fixture 斷言切成 2 個方塊；live 可見窗 11 個方塊（之前 4 大片），截圖 `.scratch/vob_boxes_live.png`。
- **OB 家族兩個新參數（用戶拍板）**：`max_size`（區塊最大高度 ×ATR，預設 3；OB 燭本身超過即屬位移燭、唔算訂單塊；0 = 唔過濾）+ `pen`（失效深度 %區塊，預設 50 — 收盤進入區塊超過幾多比例即當被消耗（mitigation）、區塊終止；100 = 舊行為「完全穿過對面邊」，0 = 一入即死）。🤖 用**收盤**唔用影線（影線碰到區塊太常見，會搞到區塊一出現就死）。OB / VOB / BRK 三個 def 同時暴露、同一套規則（`_ob_candidates` / `_ob_zones` 共用，唔可以三個指標唔同規則）；`compute` 入面 `params.get('pen', 100)` / `get('max_size', 0)` 只係手砌 dict 嘅兜底，實際經 `_new_item` 一定填滿 def 預設。i18n 新增 4 個 key（名 + 解釋 × 三語），`ind_use_ob` 用法文案跟住改（「收盤着穿區塊即失效」→「超過失效深度即被消耗」）。
- **效果（真 HSI 200 根 K_1M 實測）**：區塊由 12 個 → 10 個，最大 高/ATR 6.48 → 2.31（76 點嗰舊位移燭消失），多數區塊唔再拖到最後一根（例：`多 121..199` → `121..170`）；剩低嘅「畫到最後一根」係真未被穿透，屬正確。
- **驗證**：新增 Fixture H — 手砌 K 線令 pen=0 / 50 / 100 各喺唔同根失效（j=7 / j=8 / j=10），逐條斷言 + 「影線插穿成個區塊但收盤未過 → 未死」+ max_size 以 fixture 自己嘅 高度/ATR 比例（0.80）前後 ±0.1 斷言；BRK 同步斷言 pen=50 → Breaker 由 j=8 開始、pen=100 → 只能 j=10；registry 斷言三個 def 嘅 max_size/pen 範圍與預設一致。e2e 參數欄斷言改做 7 個 + 參數摘要 `14/1/5/5/3/50/15`。
- 🤖 **live 斷言修正**：EQHL 喺 200 根窗口可以合法為 0（同一日兩次實測 0 段 對 2 段）→ `cli_indicators_live.py` 把 `choch/eqhl/brk/bpr` 歸入 `MAY_BE_ZERO`：只斷言陣列全長度 + 幾何正確，數量改為報告（BOS/LIQ/OTE/OB 家族仍然要求 > 0）。連跑兩次全部 exit 0。

### MINOR：主菜單刪預留言面 + 指標管理新增 ICT 區塊指標（OB / FVG / VOB）
檔案：`gateway/app.py`、`gateway/i18n.py`、`gateway/indicators.py`、`.scratch/e2e_gui_indicators.py`、`.scratch/cli_indicators_live.py`、`.scratch/e2e_gui_futu_trade.py`

- **要求**（用戶）：主菜單刪除預留言面；指標管理增加 3 個 ICT 指標 — OB、FVG 同有效 OB = VOB。
- **預留位刪除**：`app.py` 嘅 disabled `nav_reserved` 佔位按鈕連同 i18n `nav_reserved`/`nav_reserved_tip` 一併移除（nav 一直由 `NAV_DIRECT`/`NAV_MENUS` 生成，呢個係唯一硬佔位）。
- **零改契約**：三個 ICT 指標全部 `position='main'`，計算照樣 `compute(ohlc, params) → dict[str, ndarray 全長度]` — 區塊用 `bull_top/bull_bottom/bear_top/bear_bottom` 四條**價格**陣列（區塊外 NaN）。🤖 唔准用 0/1 方向旗標：主圖 Y-fit 會 concat 晒所有陣列 finite 值，旗標會把 Y 範圍炸晒。現有 `_slice`／Y-fit／cache／開關掣列全部自動啱，`IndicatorKlineChart` 冇改一行。
- **定義**（寫落 docstring，參數全部可调）：`FVG` = 三根缺口（`low[i+2] > high[i]` → [high[i], low[i+2]]；反向同理），由確認根畫到第一次被填平；`OB` = 結構突破前最後一根反向燭（之後 `confirm` 根內收盤突破佢嘅 high/low + 位移幅度 ≥ `strength`×ATR），區塊 = OB 燭全範圍，畫到收盤穿過對面邊為止；`VOB`（有效 OB）= OB + 兩條有效性過濾（OB 燭影線掃走之前 `sweep` 根嘅流動性 + 位移窗內有同向 FVG）。`max_zones` 只畫最近 N 個保圖表可讀。
- **繪畫**：`_plot_zones` = `fill_between(where=mask)`，睇多用 `C_UP`、睇空用 `C_DOWN`（draw-time 跟 theme）；NaN 位置先換 0 再交 mask — `fill_between` 對 NaN 嘅處理唔一致，mask 先係可靠嘅斷段方式。
- **驗證**：`.scratch/e2e_gui_indicators.py` 全綠 57 checks（新增 Part 9：手砌 6 根 K 線逐條斷言區塊起訖與價格、`min_size`/`max_zones`/`strength`/`sweep` 過濾、隨機 400 根三個指標都有區塊且 top≥bottom、主圖真的多 6 個 PolyCollection 且 main 唔砌新 panel、管理頁 def combo/參數欄/三語）。live smoke `.scratch/cli_indicators_live.py` 全綠（真 HSI K_1M：OB 2 段 / FVG 4 段 / VOB 5 段 + 開關掣列 5 個；截圖 `.scratch/ict_zones_live.png`）。舊 suite futu_trade（含「冇 reserved_btn」斷言）/home/favorites/quotes/p8/symbol_list/fulltest 全部 exit 0。

### MAJOR：指標管理 — 指標計算 + 疊加 K 線主圖/副圖 + 管理頁 + 顯示開關
檔案：`gateway/indicators.py`（新）、`gateway/pages/indicators_page.py`（新）、`gateway/pages/kline_page.py`、`gateway/app.py`、`gateway/i18n.py`、`.scratch/e2e_gui_indicators.py`（新）、`.scratch/e2e_gui_futu_trade.py`

- **要求**（用戶）：主菜單新增「指標管理」（頂層直按按鈕），可新增／修改／移除主圖及副圖指標；用一個 CLASS／檔案專門負責指標計算＋疊加 K 線；K 線圖分主圖指標／副圖指標兩部分；首批主圖 = BOLL、副圖 = ATR（MACD 保留做副圖選項）；參數（計算日數）可調；有掣切換每個指標是否顯示。**零改動 `gui_kline.py`**（用戶明確要求）。
- **單一事實來源 `gateway/indicators.py`**：`INDICATOR_DEFS` registry（ParamSpec 範圍 + 准入位置；BOLL 只准 main、ATR/MACD 只准 sub）+ 純計算（numpy 全長度輸出 NaN warm-up — 無左緣失真；ATR = Wilder RMA SMA seed；MACD hist = `HIST_SCALE(2.0)×(DIF−DEA)` 富途慣例，e2e 鎖住定義）+ `IndicatorManager`（state_store section `indicators`，首次 seed BOLL+ATR；CRUD + clamp + 容忍 load（unknown def drop / 位置退回 / id 防撞）+ 上限總 6 / 副圖 4 + `config_version`/`layout_version` 分級 + listener 帶 `origin` 防 re-entrancy）。
- **子类疊加（零改動 gui_kline 嘅關鍵）**：`IndicatorKlineChart(gk.KlineChart)` — parent `_redraw()` 讀實例屬性 `self.ax/axv` → 子类 `fig.clear()` 重新砌 gridspec（ratios `[3,1]+[1]*n_sub`：主圖→volume→指標 panel，sharex）再重新綁定 + `super()._redraw()` 照畫蠟燭；Figure 物件身份保留。per-chart cache key=`(data_seq, config_version)`：hover/平移零重算、4Hz tick 先重算一次；`set_enabled` 只 bump layout_version（開關唔重算數值）。x 時間標籤遷移最底軸、主圖 Y-fit 合併 overlay 值、panel 手勢薄 override 改寫 `ev.inaxes`。
- **K線頁**：chart 實例喺 layout 同 index 替換（必須喺 `_apply_embedded_theme` 之前）+ 開關掣列插喺圖上面（`og="indtoggle"` checkable 掣，text = acronym+參數摘要語言中立，`_EXTRA_QSS_TPL` 跟 theme）；管理頁↔K線頁經 listener(origin 過濾) 雙向即時同步。
- **管理頁 `indicators_page`**：表格（啟用 checkbox / 名 / 位置 / 參數摘要）+ 新增（def/位置 combo 只准入 + SpinBox 範圍經 ParamSpec）/ 揀行編輯套用 / 移除所選。
- **驗證**：`.scratch/e2e_gui_indicators.py` 全綠 44 checks（純計算對照 pandas rolling/Wilder/ewm、Manager 全路徑、axes 增減、用戶 `_view` 開關後保持、cache 計數、panel 手勢、K線頁嵌入 + theme QSS、管理頁 CRUD、shell 註冊；全 hermetic：tmp state ×4 + FakeDir）。舊 suite quotes/p8/home/symbol_list/fulltest/favorites/futu_trade 全部 exit 0。🤖 教訓：futu_trade e2e 退出 exit=127 = 有 C++ QThread 未停（C-level exit(127)）— shell 加了 quotes 頁後其 `_LoopThread` 漏出 e2e 手工 cleanup 清單，已補；`_new_item` 要即刻 increment `_next_id`（否則 seed 與新增撞 id）。
檔案：`modules/symbol_search.py`、`gateway/symbol_input.py`（CAND_LIMIT 20→50）、`.scratch/test_symbol_search_fuzzy.py`（新）

- **要求**（用戶）：「打 NVD 或英偉都要找出 US.NVDA；700 要看到 HK.00700，打『訊』都要看到 — 任何一個字母或中文字都能找出相對應的。」
- **根因**：舊 ranking 淨計 `ql in code` → 用戶打嘅係 **ticker**（唔帶 `US.` 前綴），所以 `NVD` 食最低級 contains 分，再按 code 字母序排 → 目標俾幾千條淹沒；跳字（`NVA`）完全 match 唔到。
- **分層計分**（`_S_*` 常量 + `_score()`）：code/ticker exact 100 > **ticker prefix** 90 > code prefix 80 > name prefix 75 > ticker/code contains 70 > name contains 55 > **跳字 subsequence** 40/35。加 `_pos_bonus()`（match 越靠前加分越多，最多 +9）→ 打 `700` 係 HK.00700 唔係 HK.02700。HK 代碼 5 位零填充 → ticker 加「去零版本」一齊計（打 `700`/`0700` 都係 00700，排第一）。
- **成本**：全表 40,585 條含跳字 ≈ 45ms/次（實測）→ 經 GUI 200ms debounce，唔卡。
- **驗證**：`.scratch/test_symbol_search_fuzzy.py`（打真實 index cache，唔使 OpenD/GUI）全綠 — 用戶例子逐條斷言（`英偉`/`700`/`0700`/`0070`/`HK.007`/`nvda`/`騰`/`訊控`/`英偉達`/`HSImain`/`HSI` → rank 1；`NVD`/`訊`/`NVA` → 喺前 50 候選內）；單個字母/中文字一律有結果；CORE_TYPES 零污染。五份 GUI e2e（favorites/symbol_list/quotes/p8/futu_trade）全綠。
- **⚠️ 資料限制（非排序問題）**：OpenD `get_stock_basicinfo` 回傳 `name` 係**單語言**（實測：港股/中概/美股大市值全部中文名，`name_en` 淨返部分冷門美股）→ 打 `tencent`/`apple` 呢類英文公司名**無資料可 match**。要支持必須引入第二份數據源。

### 全域標的代碼輸入：模糊輸入集中化（`gateway/symbol_input.py`）
檔案：`gateway/symbol_input.py`（新）、`gateway/pages/{gui_kline,quotes_page,futu_trade_page,favorites_page}.py`、`.scratch/e2e_gui_{favorites,quotes,p8}.py`

- **動機**（用戶）：收藏頁代碼輸入要模糊輸入 — 記低：**全域**代碼輸入都要有，呢個功能 GLOBAL 使用、集中處理。
- **根因**：歷時砌咗**三份** completer。`favorites_page` 嗰份係壞嘅 — model 淨返 code + `MatchContains`，QCompleter 用輸入去 filter 個 model，打中文名（「騰訊」）model 入面根本冇中文 → popup 永遠空。
- **中央化**：`gateway/symbol_input.py` = 標的代碼輸入單一事實來源 — `FuzzyCompleter`（item =「CODE  名稱（跟語言）」、`filterAcceptsRow` 全放行交返 search 排序、準確 code / 冇 hit → 唔彈窗 pass-through、非 focus 唔彈窗）、`make_search(directory, types, limit)`（本地 index 標準搜尋 fn）、`attach_symbol_input(edit, search_fn, on_activate)`（debounce + activated → 欄入面淨返乾淨 CODE）、`set_hits()`（異步來源共用同一 item 格式）、`display_name()`（display_for 同一把尺，頁唔准再寫語言分支）。
- **四頁全部改經呢度**：K線頁 / 行情 6 格 / FUTU 交易（worker `stock_search` 異步照 feed `set_hits`）/ 標的收藏（`types=None` 全種類含窩輪）。刪走 `gui_kline.FuzzyCompleter`、交易頁自營 `_display_name`、收藏頁壞 completer。行情頁由「每按鍵即 search」改返統一 debounce 200ms；`retranslate` 統一改 `completer.lang`（🤖 行情頁之前 zh_cn 會被轉做繁體名）。
- **驗證**：`e2e_gui_favorites` 全綠 30 checks（新增：中文名 → 有候選 + item 連名、揀咗淨返 CODE、準確 code 清空候選）；`e2e_gui_futu_trade` 全綠（Part 5 模糊輸入全綠）；`e2e_gui_quotes` / `e2e_gui_p8` 補 debounce 等待後全綠。

### 首頁「全球市場脈搏」：全球指數卡 dashboard（新預設頁）
檔案：`gateway/pages/home_page.py`、`modules/market_pulse.py`、`gateway/app.py`、`gateway/i18n.py`、`.scratch/e2e_gui_home.py`

- **預設頁換檔**：`PAGE_KEYS[0]='home'`（行情退第二位）；nav 直按最前加「首頁」。
- **內容**（用戶：所有市場重要指數、密集、專業直觀、能量圖）：三區 13 張卡 — 港股（恒指 HK.800000/國企/恒生科技）、A股（上證/深成指/滬深300/創業板/科創50）、**美股 ETF 代理**（🤖 實測呢個 OpenD 對 US..SPX 等「暫不支援美股指數」，snapshot+K線都食唔到；JP/KS/TW/EU 代碼唔認識 → SPY/QQQ/DIA/IWM/VIXY 代理，分區 tooltip 如實講局限）。清單 = `HOME_INDICES` 一個常量，用戶改呢度就得。
- **卡面**（自畫 QPainter，唔靠 QSS）：名（三語）+ code、大字價格紅漲綠跌（跟 gui_kline C_UP/C_DOWN）、變動+%，**60 日走勢漸變面積 sparkline**、**當日區間能量條**（low→high + 最新價 marker）、footer 開/高/低/前收/成交額（zh 萬/億、en K/M/B 壓縮）。
- **數據**：`modules/market_pulse.fetch_pulse` — 短連線 batch subscribe K_DAY（唔 subscribe 直接 get_cur_kline 會拒）→ 逐個日K + snapshot；🤖 get_market_snapshot 一批有一隻不支援會拖爆成批 ret=-1 → 逐個 call、失敗只標該卡。手動刷新 + 60 秒自動刷新 + 更新時間。
- **驗證**：e2e 全綠 15 checks（hermetic fake pulse）；live 真 OpenD **13/13 張卡**全部有價 + 60 日走勢（截圖 `.scratch/home_shot.png`）；quotes/futu_trade e2e 註冊斷言同步後全綠。

### 標的收藏管理頁（Page 6）：主菜單直按 + 本地 JSON + 市場/種類 FILTER
檔案：`gateway/favorites.py`、`gateway/pages/favorites_page.py`、`gateway/app.py`、`gateway/i18n.py`、`.scratch/e2e_gui_favorites.py`

- **收藏儲存**（用戶：本地 JSON、為未來功能預備）：`gateway/favorites.py` = 唯一事實來源，包 state_store section `favorites`（統一 JSON 檔）— entry `{code, market, type, added}`，code 大寫 dedupe；**name 唔存**，顯示名永遠跟 UI 語言由本地 index 即時解析（index 冇就如實 '—'）。其他功能（watchlist / 捷徑）以後 `load_items()` 即用。
- **頁**：主菜單**直按按鈕**（NAV_DIRECT）— 表格（代碼/名稱跟語言/市場/種類/加入日，欄頭排序、行多選）+ 市場×種類 FILTER（共用標的列表頁常量 + 記憶）+ 新增（QCompleter 本地 index 模糊候選；index 有 → snapshot 市場/種類 + canonical code 還原，冇但似 code → prefix 兜底 + UNKNOWN，唔似 → ❌ 如實）+ 刪除所選。
- **驗證**：e2e 全綠 26 checks（hermetic：fake directory + tmp state — 新增四路徑 / FILTER 交集 / UNKNOWN 只喺 ALL / 刪除 / 記憶重載 / JSON 結構 / i18n）；`e2e_gui_futu_trade` Part 5.5 nav 分組斷言同步（直按 = 行情/FUTU 交易/標的收藏）全綠。🤖 PySide6 `headerData` 唔食 raw int orientation → e2e 要用 `Qt.Horizontal`。live smoke（真 index 40,585）抓出 `favorites.add` upper() 整爛期貨主連 canonical（HK.HSImain→HSIMAIN）→ 改原樣保留，dedupe 先大細階唔敏感，e2e 補 Part 9 斷言。

### One Gate 導航重組：子選單「測試/設定」+ 語言三按鈕（MINOR CHANGE）
檔案：`gateway/app.py`、`gateway/i18n.py`、`gateway/theme.py`、`.scratch/e2e_gui_futu_trade.py`

- **導航分組**（用戶要求）：`NAV_DIRECT = (quotes, futu_trade)` 維持直按按鈕；`NAV_MENUS = {'test': (kline, fulltest, symbol_list), 'settings': (connection,)}` 用 `QPushButton.setMenu`（左鍵開單）+ checkable `QAction`（objectName 仍 `nav_<page>`，trigger → `_select_page`）。當前頁喺選單內打勾、所屬組按鈕高亮（`_sync_nav_checked` 統一處理）。
- **語言改三個互斥按鈕**（用戶要求唔准 dropdown）：`lang_btns`（QButtonGroup exclusive，objectName `lang_zh_hk|zh_cn|en`），文字用 `LANG_SHORT`（繁體/简体/EN — endonym 恆定，唔跟 UI 語言變）；`lang_combo` / `lang_lbl` 移除。
- **右鍵彈窗保留**：nav 同選單按鈕 context menu 逐頁提供「彈出 · 頁」→ `_popup_page`（同一實例 reparent，行為不變）。
- **theme.py**：`QPushButton[og="langbtn"]`（checked = accent）+ `QMenu` 三 theme 配色。
- **驗證**：offscreen smoke（分組 / 選單 trigger 切頁 + 打勾 / 三語按鈕切換）+ `e2e_gui_futu_trade` Part 5.5 重寫斷言（nav 分組、語言三按鈕 exclusive、子選單項切頁、彈窗回归）全綠。

### 標的列表下鑽：窩輪三級 + 期權二級（即時期權鏈）
檔案：`gateway/pages/symbol_list_page.py`、`modules/symbol_search.py`、`gateway/i18n.py`、`.scratch/e2e_gui_symbol_list.py`

- **窩輪三級**（用戶要求）：WARRANT filter → **L1** 有窩輪嘅標的（按 `owner` 分組 + 認購/認沽/牛/熊計數）→ **L2** 該標的嘅類別行 → **L3** 該類別窩輪列表（行使價 / 到期日）。index schema 擴展：WARRANT entry 存 `owner`（stock_owner）/ `wtype`（stock_child_type）/ `expiry` / `strike` — 實測 HK 100% 有值（334 個標的）；**US 窩輪兩者皆 'N/A.'** → 一筆「美股窩輪」偽行直接落 L3 平鋪（如實，唔扮有分組）。
- **期權二級**：OPTION filter → **L1** 股票/ETF 候選（冇任何接口能枚舉「邊個有期權」— tooltip 如實講）→ 點擊 → **L2** 即時期權鏈：`symbol_search.fetch_option_chain(code)`（QThread；get_option_expiration_date 日期喺 strike_time 欄 → get_option_chain **逐到期日 call**，跨度 ≤30 日；progress 如實、session cache、失敗 ❌ 留喺 L1 唔入空頁）。
- **UI**：mode 狀態機（flat/w1/w2/w3/o1/o2）+ generic model（SCHEMAS 隨 mode 換欄）+ 面包屑 + 「◀ 返回」逐級退；下鑽頁模糊輸入 = 本地 substring（兩邊 t2s 正規化 — 🤖 實測 opencc s2t 將「汇丰」顯示做港式「滙豐」，用戶打「匯豐」要 match 到 → 新增 `to_simplified()` helper）。
- **Fix（e2e 抓出）**：① QTableView 冇 `cellClicked`（QTableWidget 先有）→ 用 `clicked(QModelIndex)`；② 鏈失敗嘅 ❌ status 又被 `_refresh → _update_counts` 蓋住 — 同 #10 同一教訓，set status 必須喺 refresh 之後。
- **驗證**：e2e 重寫全綠 43 checks（三級導航 / 偽行 / cache 唔重 fetch / 失敗如實 / 繁簡通配 / WARRANT 重載直接入 w1）；真 OpenD live：re-fetch 帶 owner/wtype、NVDA + 00700 期權鏈。

### 移除 `test/` folder（用戶指示）：app 組件入 gateway，測試腳本入 .scratch
檔案：`gateway/pages/gui_kline.py`、`gateway/pages/gui_fulltest.py`、`.scratch/e2e_*.py`、`.scratch/cli_kline.py`、`gateway/pages/{kline,fulltest,quotes}_page.py`

- `gui_kline.py` / `gui_fulltest.py` 其實係 **app 組件**（kline/fulltest/quotes 三頁 import 佢哋）→ `git mv` 入 `gateway/pages/`，import 改 `from gateway.pages import gui_kline as gk`（刪走 TEST_DIR sys.path hack；bootstrap 深度同步）。
- e2e ×5 + `cli_kline.py`（測試腳本）→ `git mv` 入 `.scratch/`（AGENTS.md：測試腳本統一喺 .SCRATCH 執行）。
- **驗證**：五個 e2e 全部 PASS（futu_trade exit 127 = futu SDK shutdown 已知 quirk，腳本註釋已記錄，斷言全綠）。

### One Gate 標的列表頁（Page 5）— 全 index 表格 + 市場/種類 FILTER + 一鍵更新（含窩輪 ~15.7k）
檔案：`gateway/pages/symbol_list_page.py`、`modules/symbol_search.py`、`gateway/app.py`、`gateway/i18n.py`、`test/e2e_gui_symbol_list.py`、`README.md`

- **功能**：導航新增「標的列表」頁 — QTableView 顯示全部標的（代碼 / 名稱跟語言 / 市場 / 種類，欄頭排序）；**FILTER 兩組 exclusive 按鈕**（市場 全部/HK/US × 種類 全部/股票/ETF/指數/期貨/期權/窩輪，選擇記憶 → state_store 'symbol_list'）；頂部模糊輸入（debounce 200ms，全種類含窩輪）；**一鍵更新** = QThread 行 `directory.fetch(US+HK 全 plan)`，progress 逐段如實回報；底部 = 市場×種類計數 + 顯示筆數 + 更新時間。
- **窩輪入 index**（用戶要求窩輪 filter；實測 HK 14,929 + US 828）：`MARKET_PLAN` 加 WARRANT + 跳 `delisting`；**`search(types=)` 預設 CORE_TYPES 排除窩輪** — 交易/行情頁 autocomplete 行為完全唔變，只有列表頁傳 `types=None` 攞全量（40,585 entries）。
- **期權老實講 0**：OpenD `get_stock_basicinfo` 唔支援期權枚舉（DRVT → interface not supported）、IB 唔可以無標的枚舉 → 期權 FILTER 按鈕恒 0 + tooltip 如實解釋；打準確期權 code 照樣 pass-through。牛熊證（BWRT）呢個 OpenD 回 0。
- **Fix（e2e 抓出）**：更新完成的 ✅ 訊息被 `_refresh → _update_counts` 嘅 status 重寫蓋住 — done 順序改成先 refresh、後 set result（下次 filter 操作先蓋返）。
- **驗證**：`test/e2e_gui_symbol_list.py` 全 hermetic 全綠；`e2e_gui_p8` 無 regression（search 簽名向後兼容）；真 OpenD live：fetch 40,585（窩輪 15,757）、`search("腾讯")` 預設 0 窩輪 / `types=None` 744 窩輪、shell 六頁構造 smoke + model 40,585 rows。

### One Gate 行情頁（Page 0，預設頁）— 多格 K 圖 grid + 11 週期按鈕 + 多路串流 + 統一本地狀態
檔案：`gateway/pages/quotes_page.py`、`gateway/state_store.py`、`gateway/app.py`、`gateway/i18n.py`、`test/e2e_gui_quotes.py`、`README.md`

- **功能**：表單最前新增「行情」頁（預設頁，nav 右鍵彈出視窗照舊可用）。右上 4 個 layout 按鈕 **1×1 / 1×2 / 2×2 / 2×3**：固定 6 個 ChartCell，切換只 hide/show + reposition — **cells 永不銷毀**，換 layout 唔中斷 stream、唔丟狀態。
- **每格**：標的欄模糊輸入（FuzzyCompleter 同 P8 共用本地 index + canonical 大細階還原）+ **11 個 checkable 週期按鈕**（futu KLType 全集 K_1M/K_3M/K_5M/K_15M/K_30M/K_60M/K_DAY/K_WEEK/K_MON/K_QUARTER/K_YEAR，文字全顯示、唔准 dropdown）+ 最新價 label（紅漲綠跌）+ KlineChart（重用 gui_kline 純 widget，零改動原檔）。
- **串流**：單 QThread + asyncio loop，每格一條 `stream_kline` 並發（futu 每條自開連線）— first yield = baseline 即刻上圖，其後 live tick 價即時更新 + 250ms throttle redraw（照 fulltest 已驗證 pattern）。
- **本地記憶**：新增 `gateway/state_store.py` 統一 JSON 狀態儲存（`gateway/ui_state.json` gitignored）— 按 section（`quotes` 首個；以後記其他嘢加 section 就得）、atomic 寫（tmp+os.replace）、missing/corrupt 容忍。所有格標的/週期 + layout 邊改邊 save、啟動 load。
- **Fix（e2e 抓出）**：① page objectName 冇 set → QSS 全部唔食；② client_factory 雙重包裝 — GridWorker 攞到嘅係 factory 函數本身當 client，`__aenter__` 炸 → 全部 stream 死；③ **token 改由 GUI 派**（每次 start/stop bump）— 之前 worker 派，換無效標的後舊 stream 遲到 baseline 會蓋住 ❌ 錯誤 label。
- **驗證**：`test/e2e_gui_quotes.py` 全 hermetic 全綠（shell 註冊 / layout / 串流 / throttle / stale 作廢 / canonical / 模糊 / 記憶重載 / 三語 / 清理）；真 OpenD live：4 路並發 baseline + 15 秒價即時跳動（騰訊 420.60 / NVDA / 恒指主連 23,959 / AAPL），`hk.hsimain` 細階正確還原。
- **Follow-up（用戶：彈出窗改大細、返多圖時 K 圖高度異變）**：根因 = QGridLayout stretch 全 0 時靠 sizeHint 分配，而 matplotlib canvas 嘅 `sizeHint()` = figure 像素尺寸（各格 stream/彈出 resize 歷史唔同 → row 高度偏）。**改**：`_apply_layout` 對可見 row/col 一律 `setRowStretch/setColumnStretch = 1`（隱藏清 0，換 layout 唔殘留）— 均分由 stretch 保證，同窗口大細 / 彈出與否完全無關。e2e Part 6.5 加斷言：2×2 縮細 700×450 / 放大 1500×950 四格 chart 高度都 ±1px 內。

### One Gate nav 右鍵「彈出視窗」— 頁面搬入獨立視窗（同一實例，關窗搬返）
檔案：`gateway/app.py`、`gateway/i18n.py`、`test/e2e_gui_futu_trade.py`、`README.md`

- **功能**：任何 nav 按鈕右鍵 → context menu「彈出視窗」（QMenu 標準行為：唔揳自動消失）→ 該頁 **同一實例** reparent 入 `_PopupPageWindow`（頂層 QWidget，objectName 沿用 oneGateRoot 食 shell QSS；theme 本身 app 級自動跟隨）。
- **設計**：唔開第二份 page 實例 — K線/交易頁有自己的 worker thread，雙實機會雙跑雙連線。關閉彈出窗 → closeEvent 搬返 stack **原 index**；`_select_page` 改用 `stack.indexOf(page)`，導航 index mapping 唔會爛。
- **Fix 1（用戶實測「彈出窗空白」）**：Qt 嘅 reparent 會將 widget **自動隱藏** → `_PopupPageWindow` 搬入後要主動 `page.show()`。e2e 加 `kline.isVisible()` 斷言。
- **Fix 2（用戶：左鍵應該正常切換，得右鍵先彈出）**：`_select_page` 而家若該頁正在彈出緊 → 先 `_return_page` 搬返入 shell 再正常切換（之前係喚返彈出窗 — 語義唔啱）。e2e 斷言：left-click → 窗關閉 + shell 顯示該頁。
- **i18n**：`nav_popup` × 三語。
- **e2e（Part 5.5）**：context-menu policy / 彈出窗建立 / reparent 出 stack（同一實例）/ 再撳 nav 唔多開 / 關窗搬返原 index / 搬返後可以再揀返呢頁。
- **驗證**：e2e 全綠（hermetic + live）+ p8 無 regression。

### FUTU 交易頁 dropdown 連中英文名 + 期貨主連行情修復（canonical 大細階）
檔案：`gateway/pages/futu_trade_page.py`、`modules/symbol_search.py`、`test/e2e_gui_futu_trade.py`、`README.md`

- **dropdown 連名**（用戶要求）：completer model item 由純 code 改為「CODE  名稱（跟語言）」— MatchContains 連名都 match（打中文名都有候選）；`_on_code_activated` 揀咗之後淨返 CODE 入欄（QCompleter 會先插入成串 item 文字 → singleShot(0) 喺事件尾蓋返，欄入面永遠係乾淨 code）。
- **期貨主連攞唔到價根因**：`subscribe`/`place_order` 之前一律 `upper()` — `HK.HSImain` 變 `HK.HSIMAIN` → 真 OpenD 回「未知股票 HSIMAIN」（股票冇事，得期貨主連炸）。**修復**：worker `_canonical_code()` — 經 index 還原 canonical 大細階（`symbol_search` 加 public `get()` O(1) exact lookup）；撳唔到先 fallback upper()。snapshot / K_1M 訂閱 / 落單全部經呢把尺；quote 結果 guard 全部改大細階唔敏感。
- **e2e**：FakeQuoteCtx snapshot 改大細階敏感（同真 OpenD 一致）+ FakeSearchDir 加 `get()`；新增斷言：dropdown item 連名 / 揀咗淨返 CODE / HK.HSImain subscribe 保 canonical + snapshot 預填 23941/50 + price_tick 收得到。
- **驗證**：e2e 全綠（hermetic + live）+ p8 無 regression；真連線實測：打「恒指」dropdown 出「HK.HSImain  恒指期貨主連」、HSImain 預填 23977/50。
- **Follow-up（用戶：HSImain「價格更新但沒有串流」）**：實測 SDK 層 + page 層 HSImain K_1M push 都流緊（夜市 25s 約 1–3 tick，價冇郁時欄冇變化 → 錯覺冇串流）。**改**：`_op_subscribe` result 加 `subscribed` 旗、hint 如實顯示「| 串流中 ✅」（訂閱生效就照示，換 code/斷線即清）— 用戶分得清「串流中但價冇郁」同「冇串流」。e2e 加斷言。

### FUTU 交易頁市價改用 KLINE_STREAM 方式（K_1M push 全流市價，取代 10s poll）
檔案：`gateway/pages/futu_trade_page.py`、`test/e2e_gui_futu_trade.py`、`README.md`

- **改動**：完整 code → `subscribe` op = snapshot 起步預填（last_price/lot_size）+ **訂閱 K_1M push**（同 `futu_client.stream_kline` 同一 pattern：`subscribe([code],[KLType.K_1M], subscribe_push=True, session=Session.ALL)` + `CurKlineHandlerBase`），最新 bar close = 現價 → `price_tick` 即時更新 price 欄（手動改過唔覆蓋）+ hint。換 code 自動 unsubscribe 舊嘅（一次只跟一個）；`_auto_probe` 刪走 quote 10s poll。
- **實測根因（點解唔用 QUOTE subtype push）**：呢個 futu 10.x / OpenD 組合 — `QuoteHandlerBase` 冇（要 `StockQuoteHandlerBase`）、`subscribe_quote` 已廢（統一 `subscribe()`）；訂閱 QUOTE 成功（quota 計數、query_subscription 見 sub_list）但 **push 永遠唔到**（美股開市 NVDA 15 秒 0 tick），同一連線 **K_1M push 正常流**（幾十 tick）→ 市價行 K 線 push，正正係用戶要求嘅「KLINE_STREAM 方式」。
- **Fix（用戶實測「價格數量自動填寫没有了」）**：離線搜尋修好後，用戶會**先打 code 後連線** — 之前連線成功唔會補 trigger → connect 成功分支現在若 code 欄已係完整 code（含「.」）即自動 `submit('subscribe')`。真連線 repro：US.NVDA 預填 237.175/1 → push tick → 237.194 自動更新。
- **e2e**：FakeQuoteCtx 加 `subscribe/unsubscribe/set_handler` 記帳；斷言：訂閱生效（subscribed == 当前 code）、`_price_handler._emit` 注入 tick → price 自動更新、textEdited 後唔覆蓋。
- **驗證**：e2e_gui_futu_trade 全綠（hermetic + live）；e2e_gui_p8 無 regression；真連線 page-level repro 收到 price_tick。

### FUTU 交易頁代碼搜尋改用本地 symbol index（symbol_search）— 含期貨主連 + 中文名，落單驗證 MARKET.CODE
檔案：`gateway/pages/futu_trade_page.py`、`gateway/i18n.py`、`test/e2e_gui_futu_trade.py`、`README.md`

- **改動**：`_op_stock_search` 由「OpenQuoteContext.get_stock_basicinfo per-market cache」改為直接用 **`modules/symbol_search.get_directory()`**（同 P8 共用 singleton index，純本地唔打網絡）— 即用戶要求：打「HK」見到期貨主連（HK.HSImain 恒指主連）、打「NVDA」見 US.NVDA 英偉達（name_zh 欄）。
- **細節**：search limit 5000（前綴 query match 幾千股票，唔然期貨截唔到）；純市場前綴（HK/US/SH/SZ）→ FUTURE 排前（stable sort）；**IDX 過濾**（指數唔可落單）；顯示名改用 entry 三欄（`_display_name`：en→name_en、zh_cn→name_zh canonical、zh_hk→`display_for` s2t）— 刪 page 自營 opencc block（統一用 symbol_search 把尺）。
- **落單安全**：`_CODE_RE`（`^(HK|US|SH|SZ)\.[A-Z0-9]...`）喺 `_on_place` 驗證 — 用戶留低中文名（恒指主連）按買入 → `trade_invalid_code` 三語提示，唔會送入 place_order。
- **e2e**：`FakeSearchDir`（注入 `_worker._search_dir`，複製 SymbolDirectory 契約 entries+search）取代 FakeQuoteCtx basicinfo 路徑；新增斷言：HK→HSImain 排第一 / IDX 排除 / 繁體「騰訊」t2s hit / NVDA 中文名三語 hint / 搜尋唔打網絡 / 中文 code 被拒。真 index CLI 驗證 HK/US/恒指 全部啱。
- **Fix（用戶實測「還是冇反應」，e2e 綠但 GUI 冇反應嘅 gap）**：
  1. **QCompleter popup 只喺 key event 嗰刻彈** — 我哋嘅 model 係 debounce+worker 異步之後先填好，永遠冇 key event 觸發 → 結果返嚟主動 `complete()` 強制彈 popup（e2e 加 `popup().isVisible()` 斷言）。
  2. **第一次搜尋等 10s+**（worker lazy start + symbol_search import futu + load 25k index 全堆喺首鍵）→ page 建立即 `_ensure_worker()` + `warmup` op 背景預熱；搜尋都 lazy start（唔再要求連線先搜到 — 本地 index 離線都得）。真 page 端到端重現：首鍵反應 0.9s。
  3. **市場前綴 quota**：HK 有 169 隻期貨主連，全排前會淹沒股票、50 cap 又截走 HSImain（排 79）→ 改為期貨主連頭 10 + 股票 40 混合；搵特定主連打 HSI/恒指 即首位（真 index 實測）。
- **驗證**：e2e_gui_futu_trade 全綠（hermetic + live，含 popup 可見斷言）；e2e_gui_p8 無 regression；真 page + 真 index 離線端到端重現 HK/HSI/恒指/NVDA 全部即時有候選。

### FUTU 交易頁五項體驗增強 — 解鎖狀態自動探測 / 欄名三語 / 代碼繁簡英模糊 / 價格數量預填 / TIF 三語
檔案：`gateway/pages/futu_trade_page.py`、`gateway/i18n.py`、`test/e2e_gui_futu_trade.py`、`README.md`

- **解鎖狀態自動更新**（唔使等落單）：worker 新增 `unlock_probe` op — `modify_order(CANCEL, 999999999)`（唔存在 id，**實測無副作用**：未解鎖時解鎖檢查優先 → 回解鎖類錯誤；已解鎖回「订单不存在」）。揀 REAL 帳戶即時探測 + QTimer 10s 定時探測；落單/撤單成敗仍被動推斷（雙重信號）。
- **表格欄名三語 + 美化**：i18n 加 23 個 `col_*` keys；`_apply_headers` 喺 fill/retranslate 時套返當前語言；數字欄（qty/price/market_val…）右對齊；row 高度統一。
- **代碼模糊輸入繁簡英通配**：`_op_stock_search` cache 加 opencc t2s 正規化欄（同 symbol_search 同一 canonical pattern）— 簡體「腾讯」hit 繁體 entry；顯示名稱跟 GUI 語言（`_display_name`：zh_cn→t2s、zh_hk→s2t(t2s)、en→原生；opencc 缺席 no-op）。
- **Fix（用戶實測「打 HK 冇候選」）**：搜尋改以**完整 code**（`HK.00700`）匹配 — 之前 match 緊 basicinfo 嘅 raw code（`00700`），市場前綴 query 永遠 0 hits；含「.」亦照做模糊（`HK.007` → 收窄到 `HK.00700`），quote 並行試行情，唔完整/停牌（last_price 0）當失敗靜默處理。
- **價格/數量預填**：完整 code → worker `quote` op（`get_market_snapshot` → last_price/lot_size，quote ctx lazy 共用）→ price 預填最新價、qty 預填每手；`textEdited` 先計手動值，其後自動刷新唔覆蓋；stale code guard（結果 code 同當前唔符即丟）。
- **TIF 三語**：combo `addItem(三語文字, userData=enum)`，提交用 `currentData()`；語言切換保留選中項（findData）。
- **驗證**：e2e_gui_futu_trade 全綠（Part 4 重寫為自動探測流：選帳戶→即時未解鎖→模擬 GUI 解鎖→即時已解鎖，全程唔靠落單；Part 5 加簡體模糊/hint 跟語言/預填/手動唔覆蓋；Part 1 加欄名+TIF 三語斷言）+ live read-only OK；e2e_gui_p8 無 regression。

### FUTU 交易頁移除 SDK 解鎖 — 改解鎖狀態顯示（被動推斷）
檔案：`gateway/pages/futu_trade_page.py`、`gateway/i18n.py`、`test/e2e_gui_futu_trade.py`、`README.md`

- **根因**：官方 opend-skills 確認新版 OpenD **禁止 SDK `unlock_trade` 解鎖**（安全起見，必須喺 OpenD GUI 手動「解鎖交易」）— 舊設計（密碼欄 + 解鎖按鈕 + REAL auto-unlock pending chain）無論密碼啱唔啱都失敗（用戶實測「密碼正確但解鎖失敗」）。舊註釋「GUI 解鎖只對 OpenD 自己 session 有效」屬舊版行為，已過時。
- **改動**：刪密碼欄 / 解鎖按鈕 / `_op_unlock` / pending chain；下單唔再預檢解鎖。新增**解鎖狀態欄**（只 REAL 顯示）：冇查詢 API → 狀態由落單/撤單成敗被動推斷 — 成功=已解鎖；worker `_is_unlock_error` 將解鎖類錯誤歸 `reason_code='unlock_needed'`（沿用 reason_code 契約）→ 未解鎖 + 提示去 OpenD GUI；其他情況=未知（如實）。SIM 成敗唔污染狀態。
- **i18n**：刪 6 個解鎖 keys，加 5 個狀態 keys × 三語。
- **驗證**：e2e_gui_futu_trade 全綠（hermetic Part 4 重寫為三態流：未知→未解鎖→模擬 GUI 解鎖→已解鎖；FakeCtx 未解鎖時 REAL 落單/撤單回 unlock 錯誤）+ live read-only connect OK；e2e_gui_p8 無 regression。

### Fix symbol_search 繁簡 mismatch — index name load/fetch 時統一正規化做簡體
檔案：`modules/symbol_search.py`、`CHANGELOG.md`

- **根因**：code 假設 OpenD 回傳嘅港股中文名永遠係簡體（search 將 query t2s 正規化後 match），但實測 OpenD 回傳繁簡視版本/locale 而定 — 本機 `symbol_index.json` 存咗繁體（`騰訊控股`）→ CJK fuzzy search 0 hits + EN fallback 顯示繁體（e2e_gui_p8 2 FAIL，pre-existing）。
- **Fix**：新 `_normalize_cjk(entry)` — `_load()` / `fetch()` 時將 `name`/`name_zh`/`name_en` 嘅 CJK 欄統一 t2s 正規化做簡體（canonical 存簡體）；~0.2s / 25k entries；opencc 缺席 no-op（保持原行為）。正規化後：search 繁/簡輸入都 match、EN fallback 顯示原生簡體、zh 顯示 s2t 轉返繁體（round-trip 實測 `恒指期貨主連` 精確）。
- **自愈**：舊 cache 檔唔改動；下次 FETCH 會將全份 cache 以正規化形式 persist。
- **驗證**：e2e_gui_p8 14/14 PASS（之前 2 FAIL）；CLI search 繁/簡輸入都 hit HK.00700；futu trade page E2E 26/26 無 regression。

### One Gate Page 4 FUTU 交易 — OpenSecTradeContext 下單 / 今日訂單 / 持倉 / 帳戶資金（新頁）
檔案：`gateway/pages/futu_trade_page.py`（新）、`gateway/app.py`、`gateway/i18n.py`、`test/e2e_gui_futu_trade.py`（新）、`README.md`

- **範圍**：OpenD 交易連線 + 帳戶列表（`get_acc_list`）+ 下單（`place_order`，NORMAL/MARKET/AUCTION_LIMIT/LIMIT_IF_TOUCHED × DAY/GTC/IOC）+ 今日訂單（`order_list_query`）+ 撤選定 / 全數撤（`modify_order(CANCEL)` / `cancel_all_order`）+ 持倉同帳戶資金（`position_list_query` + `accinfo_query`，同一 op batched）+ 解鎖交易（`unlock_trade`）。
- **線程模型**：全部 SDK 調用行單一 QThread worker（queue 串行 — futu OpenD 連線唔係 thread-safe，唔似 quote 邊每 request 開獨立連線）；signal 自動 queue 返 GUI thread。`futu` lazy import 喺 worker method 入面（page module top-level 唔 import futu → E2E 可以 stub `sys.modules['futu']` 做 hermetic test）。
- **安全**：REAL 帳戶下單要 QMessageBox 確認 + 先解鎖；SIMULATE 直接行；全數撤有確認框。選定 REAL 帳戶時 label 用 accent 色提醒。
- **市場過濾 client-side**（filter `trdmarket_auth`，唔使重連）；connect 後自動拉帳戶列表、下單成功後自動刷新訂單表。
- **i18n**：40 個新 keys × 三語（fail-fast `t()`）；theme 跟隨外殼 listener registry（同其他頁同一 pattern）。單獨運行：`python gateway/pages/futu_trade_page.py`。
- **驗證**：E2E 26/26 PASS — offscreen hermetic 完整 flow（FakeCtx：connect→accounts / 市場過濾 / SIM 下單 / orders 自動刷新 / 撤選定 / positions+accinfo / REAL 解鎖錯密碼+確認框 path / disconnect ctx.close）+ i18n 三語 retranslate + theme 雙向；live read-only（真 OpenD 127.0.0.1:11111：connect + accounts + positions/accinfo，**絕不落單 / 撤單 / unlock**）。

## 2026-10-06

### Bump numpy / matplotlib pins — Python 3.14（cp314）wheel 兼容
檔案：`requirements.txt`

- numpy==1.26.4 → **2.5.3**、matplotlib==3.10.1 → **3.11.2**：舊版冇 cp314 win_amd64 wheel → pip fallback sdist build（numpy 撞 Meson `[WinError 4551]` fail）；新 pin = 最新有 cp314 wheel，cp312 一樣兼容。
- 驗證：Python 3.14.8 `pip install -r requirements.txt` 成功（futu-api sdist-only 但 pure-Python build 得）；全數 import + offscreen QApplication PASS。

### Fulltest stream rows → 持續 live（baseline 收到即 PASS，live tick 持續更新到 Stop）
檔案：`test/gui_fulltest.py`、`README.md`

- **根因**：用戶反映 fulltest_page HK.HSImain stream_kline「不會刷新」— 唔係 bug，`_bounded_consume` 本來就係 5s bounded window by design（kline_page 先係無界連續）；用戶確認改持續 live。
- **新語義**：第一次 yield = 歷史 baseline → 收到即刻出 verdict（✅ PASS；0 tick 唔算 fail — 可能冇成交時段 / IB 無 RTUS）；之後 live tick 持續更新結果格（`live_updates=N`）+ 數據表，到 Stop / 重跑為止。
- **實作**：`_bounded_consume` → `_start_live`（baseline wait 30s timeout；monotonic tick token — GUI 端 `_tick_gen` guard 直接丟舊 run 嘅 stale tick；重跑同一行先 cancel 舊 live task 並 `gather(return_exceptions=True)` 等 broker cleanup）；新 `stream_tick(idx, token, payload)` signal（worker thread emit → 自動 queue 去 GUI thread）；數據表 repaint throttle `TICK_UI_INTERVAL=0.25s`（4 Hz），label 文字每 tick 更新；Stop / `_clear_results` 清 `_tick_gen`。
- **驗證**：offscreen smoke 12/12 PASS（fake stream generator：baseline → 即刻 PASS、live_updates 計上、數據表 newest bar 刷新、重跑新 token + stale tick 被丟、Stop 清晒結果 + fake stream ctx cleanup、post-stop tick 被丟、get row regression、thread 乾淨退出）；真 E2E `test/e2e_gui_fulltest.py`（OpenD 11111）全 PASS — 包括 parallel 3 rows 0.2s、Stop → broker 端收到 K 線停止指令 + thread 乾淨退出。

### 新增 `requirements.txt` — 匯合全專案第三方依賴（單一安裝入口）
檔案：`requirements.txt`（新）、`README.md`

- 由全專案 import 分析匯出 + pip 實測版本 pin：PySide6==6.11.2、pandas==3.0.2、numpy==1.26.4、matplotlib==3.10.1、futu-api==10.5.6508、ib_async==2.1.0；opencc-python-reimplemented==0.1.7 標明 optional（symbol_search 有 ImportError fallback）。
- PySide6_Essentials/Addons/shiboken6 自動隨 PySide6 裝，唔重複 pin。

### One Gate Page 2 全功能測試 — 嵌入 gui_fulltest 全部功能（takeCentralWidget，零改動 gui_fulltest.py）
檔案：`gateway/pages/fulltest_page.py`（placeholder → 真頁）、`README.md`

- **嵌入模式**：同 K綫頁同一 pattern — 建隱藏 top-level `gui_fulltest.MainWindow()` 保留引用 alive（LoopThread / TestWorker lifecycle 綁定喺個 instance）；`takeCentralWidget()` 搬入頁面 layout，QSS 喺**頁面層級**套用。
- **退出清理鏈**：`aboutToQuit` → `self._win.close()` — hidden top-level 一樣 deliver QCloseEvent → 跑原 closeEvent 完整條鏈（request_shutdown → thread.wait(3000)，shutdown 會先釋放 IB clientId=99），idempotent。
- **theme 傳播**：gui_fulltest 冇 C_* 常數 / QSS template（同 gui_kline 唔同）→ 本頁自帶 string.Template QSS（palette 值由 `gateway.theme.THEMES` 注入，經 listener registry 跟隨外殼切換）+ 運行時重新指派 `gf.STATE_STYLE` QColor — light 用原檔色值（本身為淺底設計），dark 用提亮變體；label 字串保留。section header 有明確 item role bg/fg → 覆蓋 QSS，雙主題本來就清晰。
- **i18n**：gui_fulltest 冇自己嘅語言 combo → `retranslate()` no-op（外殼三語切換唔影響嵌入頁內容）。
- **單獨運行**：`python gateway/pages/fulltest_page.py` → standalone window（Run All / Stop 全部可用）。
- **驗證**：in-process smoke 39/39 PASS（objectNames ×5、table 20×8 結構、worker-ready 啟用 Run All/Stop、dark→light→dark theme 雙向 QSS + STATE_STYLE 顏色斷言、One Gate shell 入面嵌入頁 objectNames + nav 切換 + **Run All wiring enqueue 18 indices**、shell theme toggle 傳播、三語 retranslate no-crash、quit chain ×2 + idempotent、StandaloneWindow entry）；2 個入口點（gateway.py / fulltest_page.py standalone）offscreen sanity EXIT=124 無 traceback。

### One Gate Page 1 K綫測試 — 嵌入 gui_kline 全部功能（takeCentralWidget，零改動 gui_kline.py）
檔案：`gateway/pages/kline_page.py`（placeholder → 真頁）、`gateway/theme.py`（加 listener registry）

- **嵌入模式**：建隱藏 top-level `gui_kline.MainWindow()` 並保留 Python 引用 alive（thread lifecycle 綁定喺個 instance）；`takeCentralWidget()` 搬入頁面 layout，QSS 喺**頁面層級**套用（reparent 之後 central widget 已唔係原 window descendant）。
- **退出清理鏈**：`aboutToQuit` → `self._win.close()` — hidden top-level 一樣 deliver QCloseEvent → 跑原 closeEvent 完整條鏈（fetch wait(3000) → request_shutdown → thread.wait(3000）），idempotent。
- **theme 傳播**：運行時重新指派 gui_kline 模組級 C_* 常數 + `inspect.getsource` 提取 QSS f-string statement 用新值 exec 重建（唔 copy template，源碼改咗自動跟住變）+ restyle 建檔時 bake 咗嘅 spots（figure facecolor / readout / panel btn·info / sym·time label / price color）+ `chart._redraw()`。C_UP/C_DOWN 紅漲/綠跌語義色跟 theme 不變。
- **theme listener registry**（`gateway/theme.py`）：PySide6 6.11.2 **冇綁定** `QApplication.styleSheetChanged` → 改由 theme module 統一通知：`add_listener(fn)`，apply_theme 完成 setStyleSheet 後同步調用 `fn(name)`（callback 直接收 name 參數，避免 stale import binding）。
- **i18n sync**：外殼三語切換 → 嵌入頁自己嘅語言 combo（繁中/EN；簡中 fallback 返繁中字串）。
- **單獨運行**：`python gateway/pages/kline_page.py` → standalone window（模糊搜尋、get/stream 全部可用）。
- **驗證**：in-process smoke 31/31 PASS（objectNames ×6、central taken、dark→light→dark theme 雙向 QSS/figure/baked-spot 顏色斷言、i18n sync 雙向、hermetic 3s pump thread 活緊 + 零 fetch thread、quit chain wait(6000) + idempotent、One Gate shell 入面嵌入頁 objectNames + nav 切換）；2 個入口點（gateway.py / kline_page.py standalone）offscreen sanity EXIT=124 無 traceback。

### One Gate 外殼 — 多券商 gateway 匯合主窗口（頂部導航 + QStackedWidget + 三語 + 暗/淺色 theme）
檔案：`gateway.py`（新）、`gateway/` package（新，9 檔）

- **主入口** `python gateway.py`：thin launcher → `gateway/app.py::OneGateWindow`。頂部導航欄 = brand + 3 個 checkable nav 按鈕（`nav_kline` / `nav_fulltest` / `nav_connection`）+ disabled 預留位（`nav_reserved`，其餘頁面以後再加）+ 語言 combo + theme 按鈕；QStackedWidget（`page_stack`）切換三頁。
- **組件化**：每頁係獨立組件（`gateway/pages/` kline / fulltest / connection，本 ticket 先 placeholder），每個檔底層都有 `if __name__ == '__main__':` → `run_standalone()` 單獨開視窗（帶自己嘅語言/theme 控制）；合體入外殼只係 PAGE_KEYS registry 一行。
- **i18n 單一入口**（`gateway/i18n.py`）：STRINGS dict + `t(key, lang)`，支援 zh_hk / zh_cn / en — 三語全部明確字串（唔係 opencc 轉換）；unknown key 即刻 KeyError（fail-fast）。外殼全部文字（window title、nav、預留位 tooltip、語言 label、theme 按鈕）跟隨切換。
- **theme 模組**（`gateway/theme.py`）：dark = gui_kline 現有配色（#1E1F22 / #26282C / accent #F1553B…）、light = 新專業 palette（#E8EAED / #FFFFFF / accent #E5492F）；QSS 由 string.Template 統一生成，一鍵切換即時生效。
- **QSS scoping**：全部規則用 `[og="..."]` property selector + objectName — 之後 #03/#04 嵌入 gui_kline / gui_fulltest 時，佢哋程式化設定嘅顏色唔會俾 app-level QSS 覆蓋。
- **純新增**：test/ 同 modules/ 零改動。
- **驗證**：in-process smoke 87/87 PASS（objectNames ×13、nav exclusive 切換、三語 retranslate、theme 雙向 toggle + QSS 斷言、3 頁 standalone window）；4 個入口點 offscreen subprocess sanity 全部 EXIT=124（活到俾 kill = 無 startup crash）、log 零 traceback。

### 綜合測試矩陣 GUI — broker 分區 + 隱藏 EXPECTED FAIL toggle + 數據表時間反向排序 + 串流 UPDATE 核實（用戶實測後改）
檔案：`test/gui_fulltest.py`、E2E `test/e2e_gui_fulltest.py`

- **Broker 分區更明顯**（用戶要求「不同BROKER 的TEST 要分區更明顯」）：table 加 2 條 section header row（`▍FUTU · 富途 OpenD — 11 tests` / `▍IB · Interactive Brokers (TWS/Gateway) — 7 tests`），col 0 `setSpan(1, 8)` 橫跨全欄、粗體 +2pt、futu/ib 各用獨立底色（#2f4858 / #5d4178）；table 由 18 rows → **20 rows**，`_test_to_row` / `_row_to_test` mapping 令 worker signal handler 完全唔使改。
- **隱藏 EXPECTED FAIL toggle**（用戶要求「右上角加上按鍵切換是否隱蔵EXPECTED FAIL」）：toolbar checkable 按鈕 `hide_expected_fail` — **display-only**：只 `setRowHidden(expect=False rows)`，Run All / Stop / 單行 ▶ 邏輯完全唔受影響（E2E monkeypatch enqueue 驗證隱藏狀態下 Run All 仍然 enqueue 全部 18）。
- **結果數據表按時間反向排序**（用戶要求「結果的DF 要按時間反向排序」）：`_fill_data_table` 改 `df.tail(50).iloc[::-1]` — 最新 bar 喺第一行（仍係最後 50 rows）。
- **串流 UPDATE 核實結論**（用戶反映「串流的UPDATE 好像不對没有串流」）：**pipeline 無 bug**。結果 cell 係 static text by design（跑完先寫 `snapshots=N live_updates=M`，冇 live tick 顯示）；`live_updates=0` 可能係無成交時段 / IB 無 RTUS。開市時 live Run All 實測：3 條 futu stream 全部 `snapshots=2 live_updates=1`（tick 有流就有 update）。n≤1 時 detail 加 hint「5s 內無新 tick — 可能係無成交時段 / IB 無 RTUS；baseline 已收到即算成功」。
- **E2E fix**：PySide6 6.11.2 冇綁定 `spanAt()`（`Qt.ItemDataRole.SpanRole` enum 都唔存在）→ 改斷言 `(rowSpan(r,0), columnSpan(r,0)) == (1, 8)`。
- **驗證**：E2E `test/e2e_gui_fulltest.py` 39/39 PASS EXIT=0（含 section span、hide_ef display-only、newest-first）；live Run All（OpenD + TWS 雙開）**pass=10 / expected_fail=8 / fail=0**，同 baseline 完全一致（34.8s）。

### 綜合測試矩陣 GUI — Run All 全並發 + 參數拆多欄（用戶實測後改）
檔案：`test/gui_fulltest.py`、E2E `test/e2e_gui_fulltest.py`

- **Run All = 全部同時並發**（用戶要求「RUNALL 時不要順序，要全部同時TEST」）：
  - `TestWorker` 由「單 runner task 順序消化 asyncio.Queue」改做**每行一個 `asyncio.Task` 即刻開跑**。
  - futu：每行各開獨立 `OpenQuoteContext`（OpenD 支援多條並發連線，handler 各自獨立）。
  - IB：7 個 request 共用同一條 TWS session、多 reqId 並行 — per-request error 歸屬靠 `bars.reqId` + `_error_log` sink；`_connect_lock` 防雙重連線（idempotent connect）；stream state 係 closure-local，互不干涉。
  - `_client_lock` 序列化首次 `BrokerClient()` 建立（IB clientId=99 exclusivity — 同一個 loop 只可以有一條連線）。
  - `_pending` 計數器 → 歸零先 emit `run_finished` summary（並發完成順序任意都正確）。
  - Stop / shutdown 共用一條 `_cancel_all()` 路徑：cancel 所有 row + consume task → await broker cleanup（finally）→ 重置 pending。
  - **實測**：全 18 rows（11 futu contexts + 7 IB requests）真撳 Run All 按鈕，**22.8s 完成**、PASS=10 / EXPECTED_FAIL=8 / FAIL=0（順序跑要 ~90s+）。
- **參數拆多欄顯示**（用戶要求「參數的顯示分為多列，比較清晰」）：table 由三欄改做**八欄** `[測試 | code | market | ktype | num | broker | method | 結果]`；參數欄 `ResizeToContents`、結果欄 `Stretch`。
- **Bug fix（用戶實測「RUN ALL 没有用」）**：兩個 toolbar 按鈕建咗但漏咗 `.clicked.connect()` → 撳落去冇反應。補上 connect（Run All + Stop）。
- **Stop 語義**：cancel 所有運行中 row/consume task + **即刻清理所有結果**（GUI `stop()` 先清一次；worker cleanup 完成後 `stopped` signal 再清一次 catch late paint）。
- **成功行數據拆疊**：結果下方「▸ 數據（N rows）」按鈕 → mini `QTableWidget`（最後 50 rows，maxHeight 180）；stream 行都有數據（`_bounded_consume` 返 `(n, last_df)`）。
- **矩陣內容**：18 rows = futu/ib × {US.NVDA, HK.00700, US.NQmain, HK.HSI, HK.HSImain, US.NONEXIST123} × get_kline/stream_kline；expect True / False（誠實失敗 + must_contain）/ None（ℹ️INFO 只顯示）。期望值全部 live-verified。
- **結果色**：✅PASS / ⚠️EXPECTED FAIL（橙，預期內誠實失敗）/ ❌FAIL / ℹ️INFO。
- **驗證**：E2E `test/e2e_gui_fulltest.py` 20/20 PASS EXIT=0（只需 OpenD；含 3 rows 真並發計時、Run All wiring、Stop 清理運行中行）。

### P8 — GUI 模糊搜尋 + i18n 繁中/EN + FETCH
檔案：`modules/symbol_search.py`（新）、`test/gui_kline.py`、E2E `test/e2e_gui_p8.py`（新）

- **Canonical symbol language = Futu quote code**（用戶用 Futu app 對照；覆蓋全球股票/期權/期貨/指數/窩輪/牛熊證 — 窩輪牛熊證就係 SEHK 上市證券，行港股同一條翻譯路）。
- `modules/symbol_search.py`：本地 symbol index + fuzzy search。
  - **SDK reality（probe 實測）**：futu-api 10.05.6508 **冇** `search_quote` / `get_stock_screen` → remote fallback 死咗，local index only + pass-through（autocomplete 永遠唔 block 輸入）。`get_stock_basicinfo(market, stock_type)` 唔帶 code_list = 枚舉該 market 全部 code。
  - Index = `modules/symbol_index.json`（gitignored，~3.7MB）：US+HK 股票/ETF/指數/期貨 main only；24,819 entries；`fetched_at` aware ISO；stale >24h → startup auto-fetch + GUI FETCH 按鈕手動 refresh。
  - Name language reality：`name` 欄 per-row 單語言、同一市場都混（出名美股返中文、冷門留英文）→ per-row `_has_cjk()` fill name_zh/name_en，顯示缺邊個語言 fallback 原生名（honest，唔 fake translate）。
  - `opencc-python-reimplemented`：`t2s` 將用戶繁體 input 正規化做簡體先 match；`s2t` zh 模式顯示轉返繁體。非硬依賴（ImportError → None graceful fallback）。
  - **期貨**：只留 `main_contract=True` row + regex 由 live data 合成 canonical 'XXmain'（`HK.HSI2610→HK.HSImain`）— 永遠唔 hardcode 月份。
- `test/gui_kline.py`：標的欄 fuzzy autocomplete（中/英文名、繁簡通配）+ i18n 繁中/EN 切換（STRINGS dict + `t(lang,key)`，標題/label/symbol name 跟隨切換）。
  - **FuzzyCompleter trap**：`complete()` 唔係 C++ virtual → Python override 攔唔到內部 call；正確 pattern = textChanged→set_query 重建 `QStringListModel`（scored hits）再 call 真 `complete()` + override `filterAcceptsRow`（virtual）。**唔好 override activated signal**（Python method shadow C++ signal descriptor 會炸 .connect）。
- **驗證**：E2E `test/e2e_gui_p8.py` 14/14 PASS EXIT=0（hermetic FakeDir）+ cli_kline regression CONTRACT futu=PASS ib=PASS。

### P7 — resolve_symbol + config alias 層
檔案：`modules/broker_base.py`、`modules/ib_client.py`、`test/cli_kline.py`

- `resolve_symbol()` per client（L2 規則，純函數唔打網絡）：futu = identity（base default）；ib = L0 upper + L1 config `ib.symbol_aliases`（IB-only pin）+ L2 `'X.YYmain'→'YY:FUT'`。
- IB `_make_contract` 入口經 resolve_symbol；MARKET.SYMBOL fallback 用 **INDEX-before-FUTURE**（canonical code 無 main 永遠唔係期貨語義 — 防 HK.HSI 喺有權限 account 靜默變 HSI 期貨）。
- `future_exchanges` 改 (ex,ccy) tuples + SEHK/HKD（probe-verified：SEHK 係 TWS 接受嘅 HK destination；HKEX/CFES/PKE = Invalid destination）。
- **本 account permission facts（probe 實測）**：無 HK derivatives permission（HSI futures/index 全 venue × 月份 × currency 都 Error 200）→ ib `HK.HSImain` honest fail「無法解析 HSI（試過: FUTURE）」就係 contract test case。
- **驗證**：cli_kline MAPPING_CASES — futu `HK.HSImain` PASS rows=1000 / ib honest fail；CONTRACT futu=PASS ib=PASS EXIT=0。

### P6 — IB error honesty（如實返回錯誤）
檔案：`modules/ib_client.py`、probe 腳本（已清理，findings 喺呢度 + memory）

- **用戶要求**：「尽量status data fail 返回，比如IB 没有標的，或者没有PERMISSION 要如實返回錯誤」。
- IB 最大缺口 = ib_async 靜默回空 list，generic "timeout 或 error" 混埋三種原因。解法：**per-request `errorEvent` capture**（scoping 靠 `bars.reqId` + `_error_log` sink）+ code→人話 message 映射表 + stream fail-fast（permission error 即刻返 False），120s watchdog 留做 backstop。
- **probe-first 實測本 account 實際收到嘅 code**：162（HMDS no data，唔係記憶入面嘅 354）/ 200 / 10314；warning band 2100–2200 唔算 fatal。
- **Error 200 nuance**：TWS 對「冇權限嘅 security」同「唔存在嘅 symbol」都回 200（隱藏 access）→ hint 明講兩者；`_resolve_contract` fail message 由 `_error_log` 10s window scan 帶最後錯誤 hint，唔再只講「無法解析」。
- stream 有 3s explicit-error grace window（第一筆 live bar 提前離開）。

### P5 — print → logging
檔案：`modules/__init__.py`、各 client

- app-level log config 集中喺 `modules/__init__.py`（root INFO + 時間戳 format）；所有 module 由 print 轉用 `logging.getLogger(__name__)`。

### P4 — 失敗路徑統一 triple 契約
檔案：`test/cli_kline.py`、各 client

- 壞代號 / 無權限 / timeout **必須回 `(False, None, message)`，唔好噴 exception**（統一 triple 契約）；三種原因要三句唔同 message。

### P3 — K 線 schema 單一來源
檔案：`modules/kline_schema.py`（新）、各 client

- `KLINE_COLUMNS = ['time_key','open','high','low','close','volume']` + `reorder_kline()` + `validate_kline()`；所有 broker client 輸出成功前必驗 schema — 唔符合當失敗回報，壞形狀唔會流出到 caller。
- futu `time_key` 統一 parse 成 datetime（同 IB）— 之前 str/datetime 兩制令 schema 唔一致。

### P2 — registry：券商名單單一來源
檔案：`modules/registry.py`（新）、`modules/broker.py`

- `BROKERS = {cls.NAME: cls}` + `get_broker_class()` fail-fast；**加新 client 只改 registry.py 一行** → dispatch / GUI combo / contract test 自動覆蓋。
- config-driven instantiation：每個 client 按自己 NAME 讀 `config.json` 對應 section；`source.*` 值開機時驗證（打錯字即刻炸）。

### P1 — 統一契約 BrokerBase
檔案：`modules/broker_base.py`（新）、各 client

- 所有券商 client 繼承 `BrokerBase`：聲明 `NAME` + 實作 `get_kline()` / `stream_kline()`，一律回 `(status, data, message)` triple；`disconnect()` 預設 no-op。
- futu 改做**每條 stream / 每次 get_kline 獨立 OpenQuoteContext**（廢共享 context）— OpenD 支援多條並發連線，handler 各自獨立互不干擾。

---

## 2026-10-05

### Futu streaming K 線成功
檔案：`modules/futu_client.py`、`test/cli_kline.py`、`test/gui_kline.py`

- `stream_kline()` = baseline（歷史快照）+ live push（handler → queue buffer → drain 拼接），回 `(True, async_gen, None)`；consumer cancel task / `.aclose()` 停止，finally 斷呢條 stream 專用連線。
- B1–B5 fixes：drain 晒 buffer（高頻唔會無界增長）/ 亂序 bar 跳過 / 同 bar 原地覆蓋 / NaN volume 填 0 / normalize 先 copy 再改。

## 2026-10-04

### Stream K 線原型
檔案：`modules/futu_client.py`、`test/gui_kline.py`

- `stream_kline` 初版（handler + subscribe push + 歷史底表）；GUI 即時更新。

# K 線圖：切換週期加速 + 加載態 + 模組化共用（plan: hidden-sparking-origami）

0. [X] Step 0 量 baseline：`.scratch/bench_kline_switch.py`（真 OpenD，2026-10-09 01:05）
    - 單格：首次切 K_5M **609 ms**（1000 根）· 切返睇過嘅週期 **323 / 163 ms**（冇 cache → 每次都全取）
    - 2×3 六格：全部上圖 **2412 ms** · 六格同時切 **5797 ms**
    - 開 `OpenQuoteContext` 次數：單格情境 **4** · 六格情境 **13**（= 每條 stream 一條連線，P3 目標 → 1）
    - 目標：切返睇過嘅週期 < 50 ms（cache 命中）、六格連線數 → 1
1. [X] P1 加載態入 `KlineChart`：`gateway/ui/kline_overlay.ui`（惰性 `load_ui`）+ `set_busy/set_lang/_restyle_overlay/_on_slow_loading`（8s watchdog）+ i18n `chart_loading(_slow)` 三語 + 兩頁接點（`gui_kline.on_test_clicked/_on_update`、`quotes_page._start_cell_stream/_on_cell_update/_on_cell_symbol`）+ theme restyle（`kline_page._apply_embedded_theme`、`quotes_page._apply_theme_qss`）
    - 驗證：`.scratch/t_p1_overlay.py` → 「所有功能測試成功 ✅ (20 項)」（惰性 / 可見性 / objectName / 文案 / geometry 跟 resize / 滑鼠穿透 / rgba 跟 palette / 三語 / watchdog / 冪等 / 子类繼承）
2. [X] P2 cache + 兩段式取數 + K 線頁即切：`modules/kline_cache.py`（LRU 32 + TTL 按 ktype）、`config.json futu.kline_num_first=300`、`futu_client.stream_kline` 兩段（300 即刻上圖 → 背景補 1000）、futu 同步呼叫一律 `asyncio.to_thread`、`ktype_combo` 即切（`LoopThread.restart_stream` → loop 上 cancel-then-start，舊 run 嘅 `done` 被 `_silent_cancel` 壓住）
    - 驗證：`.scratch/t_p2_cache_switch.py` → 「所有功能測試成功 ✅ (35 項)」（cache hit/key 隔離/TTL/LRU/唔寫空 · 兩段式 `[300,1000]` + `kline_num_first=0` 關兩段 · 即切即刻 set_busy + 第二組 start + button 文案唔變 + 兩條週期各自入 cache + 快取命中即刻上圖）
    - 前後對比（真 OpenD，2026-10-09，`.scratch/bench_out.txt`）：
      · ① 首次切未睇過嘅週期 **609 → 533 ms**（rows 1000 → 300 = 兩段式生效；改善有限，因為主成本係 OpenD round-trip 而唔係根數）
      · ② 切返睇過嘅週期 **323/163 → 0 ms**（目標 <50 ms ✅ 快取命中）
      · 六格 K_1M 全部上圖 **2412 → 839 ms**（`to_thread` 先至可以真並發）
      · 六格同時切 K_5M **5797 → 717 ms**（各格 [0,164,172,180,296,717]；⚠️ 第一格 0 ms 係情境 1 留低嘅快取，其餘先係真取數）
      · 開連線次數 **4 / 13 未變** → P3 目標 → 1
    - 回歸：`e2e_gui_quotes.py` / `e2e_gui_p8.py` / `e2e_gui_indicators.py` / `t_p1_overlay.py` 全綠
3. [X] P3 共用 OpenD 連線：`futu_client._open_ctx()`（全檔唯一開 ctx 嘅位）+ `_ensure_ctx()` 共享一條 + `_subs` 訂閱 refcount + `_queues` 每條 stream 專屬 queue + `KlineRouter` 按 `(code,k_type)` 分派（normalize **之前**）+ `_acquire`/`_release`（同步、外層 `asyncio.to_thread`）+ `disconnect()` 先收線（stream 結束只退訂閱）；`get_kline` 照用一次性連線；transport 異常先標 `_shared_dead`，訂閱 RET_ERROR 唔連累其他 live stream
    - 驗證：`.scratch/t_p3_shared_conn.py` → 「所有功能測試成功 ✅ (31 項，31 綠)」（A 共享連線/refcount/fan-out/唔 close · B 失敗隔離 + 自我修復 · C router 分派（跨標的、同 key 兩條、已 normalize、tail(1)、冇路由欄、未訂閱 key）· D 真 OpenD：兩條 stream 共用**一條**連線 + 真 push 經 router 落到自己 queue ⇒ **實證 push 帶 `code`/`k_type`**）
    - ⚠️ 途中發現：`__init__` 嘅 `self._shared_ctx = None` 曾 shadow 咗同名方法 `_shared_ctx()` → 方法改名 `_ensure_ctx()`。教訓：方法名唔好同 instance 屬性同名。
    - 前後對比（真 OpenD，`.scratch/bench_out*.txt` 三次）：
      · 開連線次數 **13 → 1**（六格情境再無新連線）✅ 目標達成
      · 六格 K_1M 全部上圖 **2412 →(P2) 839 → 1142 / 786 / 523 ms**
      · 六格同時切 K_5M **5797 →(P2) 717 → 1549 / 562 / 283 ms**
      · ⚠️ 第一次 1549 係**冷啟動離群值**（成個 bench 進程第一次同 OpenD 握手）；同一代碼連跑三次即回落 562 / 283 → P3 係淨改善，唔係倒退。教訓：呢個 bench 一定要連跑多次先可以比較。
    - 顯微鏡核對（證明成本唔喺 futu 層）：`.scratch/bench_futu_concurrency.py` → 一條 ctx 串行 4 ms / 開 6 thread 3 ms / 六條 ctx 54 ms，`get_cur_kline` 每次 ~1 ms（訂閱後 SDK 有本地快取）→ 共用連線唔會令取數串行化；`.scratch/bench_futu_setup_path.py` → 六條 stream 起 591 ms、**六格同時切 33 ms**（acquire 1–20 / fetch 2–9 / release ~0 ms）
    - 回歸：`t_p2_cache_switch.py` 35 項 / `t_p1_overlay.py` 20 項 / `e2e_gui_quotes.py` / `e2e_gui_p8.py` / `e2e_gui_indicators.py` 全綠
4. [X] P4 抽 `gateway/kline_stream.py`（`LoopThreadBase` + `ClientHolderMixin`），兩頁變 subclass（純搬移）
    - `LoopThreadBase`：起 loop / 喺 worker thread 建 worker（`worker_cls`）/ `worker_ready` / `request_shutdown`（2s deadline poll，取 K 線頁嘅 superset）/ finally 收 task + close loop
    - `ClientHolderMixin`：`init_client_holder` + `ensure_client`（`client_factory` 返 None → 真 `BrokerClient`，lazy import）+ 模板 `shutdown()` = `_cancel_work()`（subclass hook）→ `release_client()` → `loop.stop()`
    - **payload 形狀照樣各管各**（K 線頁 records / 行情頁 df）→ 用 mixin 唔係共用 `QObject` base（用戶已定呢個取捨）
    - 兩頁變 subclass：`gui_kline.LoopThread(LoopThreadBase)` + `Worker(QObject, ClientHolderMixin)`；`quotes_page._LoopThread(LoopThreadBase)` + `GridWorker(QObject, ClientHolderMixin)`；`MainWindow` 新增 `client_factory=None`（同 `QuotesPage` 同一注入口）
    - ⚠️ 連鎖改動：`ensure_client` 離開 `gui_kline` → `.scratch/t_p2_cache_switch.py` 舊嘅 `gk.BrokerClient = FakeClient` 失效，改經 `MainWindow(client_factory=...)`
    - 驗證（純搬移、行為零改）：`t_p2_cache_switch` 35 ✅ / `t_p1_overlay` 20 ✅ / `t_p3_shared_conn` 30 綠（+1 項因收市無 push 自動 SKIP）✅ / `e2e_gui_quotes` / `e2e_gui_p8` / `e2e_gui_indicators` / `e2e_gui_shell` / `e2e_gui_home` / `e2e_gui_fulltest` / `e2e_gui_connection` / `e2e_gui_futu_trade` 全部 exit 0
    - ⏳ 未處理（超出 P4 範圍）：`gateway/pages/gui_fulltest.py` 仲有第三份 `LoopThread`/`TestWorker` 複製 → 之後可一併落返呢個 base
5. [X] E2E 全跑（quotes / kline 即切 / theme / i18n）+ README・CHANGELOG 同步
    - `e2e_gui_quotes.py` 新增 **Part 4b**（11 項，純 GUI 端）：換週期即刻加暗 + LOADING（`findChild(QLabel,'kline_loading')` 有文字）· 快取命中即刻上返睇過嘅圖（零 network）· 快取上圖期間照樣 LOADING · 真 baseline 覆蓋 + 疊層收返 · baseline 寫返入快取 · 兩段式 3→12 根 GUI 只多一次重繪 · 快取跟住更新 · 行情頁一律唔傳 `kline_num`（根數歸 config）· 切返睇過嘅週期即刻上快取圖 → **79 項全綠**
    - ⚠️ 修正 plan 嘅斷言 (c)：「切返同一週期 → fake call 次數唔再增長」唔啷 — 設計上 stream 照起，快取只係**唔使空白等**。正確斷言 = 先 `kline_cache.put` seed 一份明顯唔同嘅 rows → click 後**即刻**（唔 pump）見到 seed 圖，真 baseline 到咗先覆蓋
    - ⚠️ 測試要訣：讀快取 + `set_busy` 全程同步喺 GUI thread → 斷言要放喺 `click()` 之後即刻做，一 `processEvents()` 就可能已被真 baseline 蓋咗；`check()` 加咗可選 `detail` 參數方便定位
    - K 線頁即切 / theme / i18n 冇重複造：即切 + 快取 = `t_p2_cache_switch` Part C；加載態惰性/三語文案/rgba 跟 palette/watchdog = `t_p1_overlay`
    - 全量回歸（全部 exit 0）：`t_p1_overlay` 20 ✅ · `t_p2_cache_switch` 35 ✅ · `t_p3_shared_conn` 30 綠（+1 項收市無 push 自動 SKIP）✅ · `e2e_gui_quotes` 79 ✅ · `e2e_gui_p8` / `e2e_gui_indicators` 126 ✅ / `e2e_gui_shell` / `e2e_gui_home` 21 ✅ / `e2e_gui_fulltest` / `e2e_gui_connection` / `e2e_gui_futu_trade` / `e2e_gui_favorites` / `e2e_gui_symbol_list` / `e2e_gui_strategies` / `t_ui_infra` ✅ · `test_indicators_common` / `test_strategy_conditions` / `test_ict_suite` / `test_symbol_search_fuzzy` / `test_fade_scale` ✅
    - 文件：README 核心功能 ①–⑤ + 檔案地圖（`kline_cache.py` / `kline_stream.py` / `kline_overlay.ui`）已同步；CHANGELOG 三條 MINOR（加載態+快取+兩段式+即切 / 共用 OpenD 連線 / stream 基建）連驗證一齊寫低

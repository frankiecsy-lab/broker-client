# 📊 Broker Client — 多券商 K 線客戶端

統一接口取 / 流式接收多家券商嘅 K 線數據（目前：**富途 Futu** + **Interactive Brokers IB**），配 PySide6 GUI。所有 broker 一律回 `(status, data, message)` triple，失敗如實回報原因（無標的 / 無權限 / timeout 三句唔同）。

## ✨ 核心功能

- **統一 K 線契約**：`get_kline()` / `stream_kline()` 全部 broker 同一形狀；K 線 DataFrame schema 單一來源（`time_key, open, high, low, close, volume`），成功前自動驗證。
- **並發安全**：futu 每條 stream / 每次 get 各開獨立 OpenD 連線；IB 多 request 共用一條 TWS session、per-request error 歸屬（reqId scoping）— Run All 全矩陣可同時跑。
- **Symbol mapping L0–L4**：canonical = **Futu quote code**（用戶用 Futu app 對照）。L0 normalize → L1 config alias（IB-only pin）→ L2 per-client `resolve_symbol()`（IB 自動解析 front month，per-session cache）→ L3 broker 原生 fallback → L4 honest fail。永遠唔 hardcode 合約月份。
- **Error honesty**：IB errorEvent per-request capture + code→人話映射；「無標的 / 無 permission / timeout」如實分開講。
- **GUI 模糊搜尋 + i18n + FETCH**（`gateway/pages/gui_kline.py`）：標的欄 autocomplete（中/英文名、繁簡通配，local index ~24,800 entries）、繁中/EN 一鍵切換（symbol name 跟隨）、FETCH 手動 refresh index（stale >24h 自動 fetch）。
- **綜合測試矩陣 GUI**（`gateway/pages/gui_fulltest.py`）：18 rows = futu/ib × 6 類標的 × get/stream；FUTU / IB **broker 分區 header**、每行獨立跑、**Run All 全部同時並發**、Stop cancel + 清理所有結果、**stream 行持續 live**（第一次 yield baseline 收到即 PASS，之後 live tick 持續更新到 Stop）、成功行數據可拆疊顯示（**最新 bar 喺第一行**）、右上角 toggle **隱藏 EXPECTED FAIL** rows（display-only，唔影響 Run All）。
- **標的列表頁**（`gateway/pages/symbol_list_page.py`）：全本地 index 表格（代碼 / 名稱跟語言 / 市場 / 種類，欄頭可排序），**FILTER 按鈕兩組**（市場 全部/HK/US × 種類 全部/股票/ETF/指數/期貨/期權/窩輪）、頂部模糊輸入（全種類，含窩輪）、**一鍵更新**（QThread 經 Futu OpenD 重新枚舉全部市場，progress 如實回報）、底部顯示市場×種類計數 + 更新時間。**衍生下鑽**：窩輪 **三級**（有窩輪嘅標的 → 認購/認沽/牛/熊類別 → 窩輪列表連行使價/到期日，US 無所屬數據如實偽行平鋪）；期權 **二級**（股票/ETF 候選 → 點擊即時期權鏈 — QThread 逐到期日攞、10 次/30 秒配額節流、progress 如實、session cache）。窩輪（HK+US ~15.7k）只喺呢頁出現 — 其他頁 autocomplete 預設 CORE_TYPES 排除，唔受污染。
- **標的收藏頁**（`gateway/pages/favorites_page.py`）：主菜單直按頁 — 收藏標的表格（代碼 / 名稱跟語言 / 市場 / 種類 / 加入日，欄頭排序、行多選），**市場 × 種類 FILTER**（同標的列表頁同一組常量）+ 記憶；**新增** = 輸入框模糊輸入（gateway/symbol_input — 代碼前綴 / 中英文名都得，全種類含窩輪），index 有就 snapshot 市場/種類、冇就 code prefix 兜底 + UNKNOWN（如實）；**刪除所選** 一鍵管理。儲存 = `gateway/favorites.py` → state_store section `favorites`（統一 JSON；entry 只存 code/market/type/added，name 永遠跟語言即時解析）— 其他功能（watchlist / 捷徑）以後 `load_items()` 即用。
- **首頁「全球市場脈搏」**（`gateway/pages/home_page.py`，預設進入頁）：三區指數卡 dashboard — 港股（恒指/國企/恒生科技）、A股（上證/深成指/滬深300/創業板/科創50）、**美股 = ETF 代理**（🤖 實測呢個 OpenD 唔支援美股指數報價 → SPY/QQQ/DIA/IWM/VIXY，tooltip 如實講）。每卡自畫（QPainter）：大字價格紅漲綠跌 + 變動%、**60 日走勢漸變 sparkline**、**當日區間能量條**（low→high marker）、開/高/低/前收/成交額。數據 = `modules/market_pulse.py`（subscribe K_DAY → 日K + snapshot，逐個失敗如實）；手動 + 60 秒自動刷新。指數清單 = home_page.py 頂部 `HOME_INDICES` 一個常量。
- **指標管理**（`gateway/indicators.py` + `gateway/pages/indicators_page.py`）：主菜單直按頁 — 新增／修改／移除**主圖／副圖指標**（內置 BOLL 主圖；ATR / MACD 副圖；**ICT 區塊 OB / FVG / VOB 主圖**（OB = 結構突破前最後一根反向燭、FVG = 三根缺口、VOB = 有效 OB = OB + 掃流動性 + 位移段有 FVG；位移強度/確認根數/最多區塊等可調）；週期等參數可調 + clamp；上限總 6 / 副圖 4）；K 線圖正上方每個指標一個**顯示開關掣**。計算＋疊加單一事實來源：`IndicatorKlineChart(gui_kline.KlineChart)` 子类動態 gridspec（主圖→volume→指標 panel，**零改動 gui_kline**）+ per-chart 計算 cache（hover/平移零重算，數據/參數先至重算）+ 顏色 draw-time 跟 theme + 管理頁↔K線頁開關雙向同步。儲存 = state_store section `indicators`（首次 seed BOLL+ATR）。
- **One Gate 多券商匯合外殼**（`python gateway.py`）：頂部導航 + QStackedWidget 九頁 — 直按（首頁 / 行情 / FUTU 交易 / 標的收藏 / 指標管理）＋ 子選單「測試」（K綫測試 / 全功能測試 / 標的列表）與「設定」（連綫測試），選單項打勾標示當前頁、組按鈕高亮跟隨；語言改用 **繁體/简体/EN 三個互斥按鈕**（endonym 唔跟 UI 變）、暗/淺色 theme 一鍵切換；**nav 右鍵 →「彈出視窗」**（該頁同一實例搬入獨立視窗 — 唔開第二份 thread；關窗搬返；左鍵永遠正常切換 — 彈出緊都先搬返入 shell）；每頁獨立組件可單獨開視窗 Debug。
- **行情頁（Page 0，預設頁）**（`gateway/pages/quotes_page.py`）：多格 K 圖 grid，右上 **1×1 / 1×2 / 2×2 / 2×3** 四個 layout 按鈕（固定 6 格，切換只 hide/show — cells 永不銷毀、stream 唔中斷）；每格 = 標的模糊輸入（同 P8 共用本地 index）+ **11 個週期按鈕**（futu KLType 全集 K_1M…K_YEAR，文字全顯示冇 dropdown）+ 最新價（紅漲綠跌）+ K 圖；**多路 `stream_kline` 並發自動串流**（baseline 即刻上圖、live tick 價即時 + 250ms throttle redraw）；所有格標的/週期 + layout **本地記憶** → `gateway/state_store.py` 統一 JSON 一個檔（以後記其他嘢加 section 就得）。

## 🚀 安裝與運行

```bash
pip install -r requirements.txt
# opencc 係非硬依賴（冇就 fallback 唔做繁簡轉換，requirements.txt 內有標明）
```

前置：OpenD（Futu，port 11111）+ TWS / IB Gateway（IB，port 4001）開緊。配置喺 `modules/config.json`（host/port、kline_num、source 預設 broker）。

| 入口 | 用途 |
|---|---|
| `python gateway.py` | **One Gate** 主窗口：九頁導航（直按 首頁 / 行情 / FUTU 交易 / 標的收藏 / 指標管理 ＋ 子選單「測試」「設定」）+ 三語按鈕 + 暗/淺色 theme（預設頁 = 首頁全球市場脈搏）|
| `OneGate.bat`（雙擊） | Windows launcher，等同 `python gateway.py`（防手滑指住資料夾跑） |
| `python gateway/pages/gui_kline.py` | 主 GUI（app 組件；K綫頁嵌入同一份）：模糊搜尋標的 + get/stream K 線 + i18n + FETCH |
| `python gateway/pages/gui_fulltest.py` | 綜合測試矩陣（全並發 Run All；全功能頁嵌入同一份） |
| `python .scratch/cli_kline.py` | CLI 取數 + contract / mapping regression tests（測試腳本統一喺 .scratch） |

## 🗺️ 檔案地圖

- `AGENTS.MD` - **[AI 開發規範]**：定義 Agent 溝通語言（廣東話）、CHANGELOG/README 維護責任、環境清理與切片式讀取原則。
- `CHANGELOG.md` - **[版本變更日誌]**：所有功能增刪 / Bug 修復 / 架構調整嘅唯一事實來源（倒序）。
- `requirements.txt` - **[依賴清單]**：全專案第三方依賴匯合（PySide6/pandas/numpy/matplotlib/futu-api/ib_async + opencc optional），版本 = 實測 pin；`pip install -r requirements.txt`。
- `modules/__init__.py` - **[package 入口 + logging config]**：app-level log format；匯出 BrokerClient / BROKERS / KLINE_COLUMNS。依賴：registry、broker、kline_schema。
- `modules/broker.py` - **[BrokerClient 統一 dispatch]**：config-driven 實例化所有 registry client；`get_kline(broker=)` / `stream_kline(broker=)` 按 broker 參數 > config.source 分發；`__aexit__` 斷晒所有連線。依賴：registry、config.json。
- `modules/broker_base.py` - **[統一契約 BrokerBase]**：所有 client 嘅 ABC — NAME + get_kline/stream_kline triple 形狀 + `resolve_symbol()`（L2 hook，預設 identity）+ disconnect no-op。
- `modules/registry.py` - **[券商名單單一來源]**：`BROKERS = {NAME: cls}`；加新 client 只改呢度一行。依賴：futu_client、ib_client。
- `modules/kline_schema.py` - **[K 線 schema 契約]**：標準 6 欄定義 + reorder_kline + validate_kline（成功前驗證）。被兩個 client 共用。
- `modules/futu_client.py` - **[富途 client]**：每次 get / 每條 stream 獨立 OpenQuoteContext；>1000 bars 自動分頁；handler→queue buffer live push。依賴：futu SDK、broker_base、kline_schema。
- `modules/ib_client.py` - **[IB client]**：共享 TWS session（clientId=99，lock-guarded idempotent connect）；per-request errorEvent capture + code 映射；期貨 front month 自動解析（reqContractDetails，session cache）。依賴：ib_async、broker_base、kline_schema。
- `modules/market_pulse.py` - **[首頁指數取數層]**：fetch_pulse(codes) — 短連線 batch subscribe K_DAY → 逐個 get_cur_kline（sparkline）+ get_market_snapshot（🤖 一批有一隻不支援會拖爆成批 → 逐個 call 逐個如實）；row = last/prev/open/high/low/turnover/spark/err。美股指數呢個 OpenD 不支援 → 清單用 ETF 代理。CLI 除錯可單獨跑。
- `modules/symbol_search.py` - **[本地 symbol index + fuzzy search]**：US+HK 股票/ETF/指數/期貨 main + 窩輪（~40,600 entries，跳 delisting；窩輪存 owner/wtype/expiry/strike 俾下鑽）；繁簡正規化 match + **分層計分 ranking**（`_S_*`：code/ticker exact > ticker prefix > code prefix > name prefix > contains > 跳字 subsequence；position bonus + HK 零填充去零變體 → 打 `NVD`/`英偉`/`700`/`訊` 都直達目標。⚠️ OpenD name 單語言 → 英文公司名無資料可 match）+ stale auto-fetch；`search(types=)` 預設 CORE_TYPES 排除窩輪（autocomplete 唔污染），列表頁傳 None 攞全量；`fetch_option_chain()` 即時期權鏈（逐到期日、10 次/30 秒配額節流、失敗如實）；`to_simplified()` 繁簡通配 helper。依賴：futu SDK、opencc（可選）。
- `modules/symbol_index.json` - **[symbol index cache]**：gitignored，由 symbol_search fetch 寫入（~3.7MB）。
- `modules/config.json` - **[運行配置]**：host/port、kline_num、source 預設 broker、ib.symbol_aliases。
- `gateway.py` - **[One Gate 主入口（thin launcher）]**：`python gateway.py` → `gateway/app.py::main()`；同同名 `gateway/` package 安全共存（CPython FileFinder 先查目錄）。依賴：gateway.app。
- `OneGate.bat` - **[Windows 雙擊 launcher]**：等同 `python gateway.py`（防手滑指住資料夾跑報錯）。
- `gateway/__init__.py` - **[package 入口]**：版本號 + 用法說明，無重 import。
- `gateway/i18n.py` - **[三語 i18n 單一入口]**：STRINGS dict（zh_hk/zh_cn/en 明確字串）+ `t()` fail-fast + `LANG_SHORT`（語言按鈕 endonym）+ theme 按鈕文字 helper。無依賴。
- `gateway/theme.py` - **[theme QSS 中央生成]**：dark（= gui_kline 配色）/ light palette + string.Template QSS；`apply_theme()` 一鍵切換 + listener registry（`add_listener(fn)`，嵌入頁跟隨 theme）。依賴：PySide6（lazy import）。
- `gateway/app.py` - **[One Gate 主外殼]**：OneGateWindow — 頂部導航欄（brand + NAV_DIRECT 直按按鈕 + NAV_MENUS 子選單「測試/設定」（QMenu checkable actions）+ 語言三按鈕（QButtonGroup exclusive）/theme）+ QStackedWidget；PAGE_KEYS registry 生成頁面（home 排最前 = 預設頁）；nav 右鍵 context「彈出 · 頁」（page 同一實例 reparent 出入 _PopupPageWindow，關窗搬返 stack 原位置）。依賴：i18n、theme、pages/*。
- `gateway/state_store.py` - **[統一本地狀態儲存]**：一個 JSON 檔（gateway/ui_state.json，gitignored）按 section 記所有頁嘅 UI 狀態 — `load_section/save_section`；atomic 寫（tmp+os.replace）、missing/corrupt 容忍返 default。以後記其他嘢加 section 就得。無依賴。
- `gateway/favorites.py` - **[標的收藏儲存（唯一事實來源）]**：state_store section `favorites` — entry {code, market, type, added}（code 大寫 dedupe；name 唔存，顯示跟語言由 index 解析）；`load_items/add/remove/load_filter/save_filter`。其他功能（watchlist/捷徑）直接 load_items() 即用。依賴：state_store。
- `gateway/indicators.py` - **[指標領域單一事實來源]**：`INDICATOR_DEFS` registry（BOLL 主圖；ATR / MACD 副圖；OB / FVG / VOB 主圖區塊 — ParamSpec 範圍 + 准入位置 + 純計算函數，numpy 全長度輸出 NaN warm-up；MACD hist = `HIST_SCALE×(DIF−DEA)` 富途慣例；🤖 ICT 區塊一律用 `bull_top/bull_bottom/bear_top/bear_bottom` 四條**價格**陣列（區塊外 NaN）— 同一個計算契約，主圖 Y-fit 自動啱，唔准用 0/1 方向旗標）+ `IndicatorManager`（執行個體 CRUD / clamp / 容忍 load / 上限總6副4 / config_version+layout_version 分級 / listener(origin)，state_store section `indicators`，首次 seed BOLL+ATR）+ `IndicatorKlineChart(gui_kline.KlineChart)` 子类（動態 gridspec 主圖→volume→指標 panel、per-chart cache key=(data_seq,config_version)、x 時間標籤遷移最底軸、panel 手勢 inaxes 改寫；⚠️ 依賴 parent 嘅 ax/axv/_s/_view 結構 — 改 gui_kline 內部要同步檢查呢度）。依賴：numpy、state_store、gui_kline。
- `gateway/symbol_input.py` - **[全域標的代碼輸入（模糊輸入單一事實來源）]**：`FuzzyCompleter`（item =「CODE  名稱（跟語言）」、filterAcceptsRow 全放行交返 search 排序、準確 code/冇 hit → 唔彈窗 pass-through）+ `make_search(directory, types, limit)`（本地 index 標準搜尋 fn）+ `attach_symbol_input(edit, search_fn, on_activate)`（debounce + 揀咗淨返乾淨 CODE 入欄）+ `set_hits()`（異步來源共用）+ `display_name()`（display_for 同一把尺）。所有頁嘅代碼輸入一律經呢度，唔准自己砌 QCompleter。依賴：PySide6、symbol_search（lazy）。
- `gateway/pages/__init__.py` - **[pages package 入口]**：加新頁說明（檔 + PAGE_KEYS 一行）。無 import。
- `gateway/pages/base_page.py` - **[standalone window 基類 + run_standalone()]**：StandaloneWindow 包任何 page 組件成獨立視窗（標題欄 + 語言 combo + theme 按鈕）；各頁底層調用佢單獨 Debug。依賴：i18n、theme。
- `gateway/pages/home_page.py` - **[首頁 全球市場脈搏（預設頁）]**：HOME_INDICES 常量清單（13 指數/ETF 代理，改呢度就得）→ 三區 grid；_IndexCard 全自畫 QPainter（大字價紅漲綠跌跟 gk.C_UP/C_DOWN、60 日 sparkline 漸變面積、當日區間能量條 marker、開高低前收成交）；_PulseWorker 行 modules.market_pulse + 60s 自動刷新 + 逐卡失敗如實；卡面文字/顏色 paint 時即時取 t()/theme palette。可單獨運行。
- `gateway/pages/quotes_page.py` - **[Page 0 行情]**：多格 K 圖 grid（固定 6 ChartCell，右上 1×1/1×2/2×2/2×3 layout 按鈕只 hide/show — cells 永不銷毀；可見 row/col 一律 stretch=1 保證任何窗口大細/彈出與否都均分）；每格 = 標的模糊輸入（gateway/symbol_input 共用本地 index + canonical 大細階）+ 11 週期 checkable 按鈕（futu KLType 全集）+ 最新價 label（紅漲綠跌）+ KlineChart（重用 gui_kline）；單 QThread+asyncio 多路 `stream_kline` 並發（GUI 派 token 作廢遲到 update、tick 250ms throttle）；標的/週期/layout → state_store 'quotes' section。可單獨運行。
- `gateway/pages/kline_page.py` - **[Page 1 K綫測試]**：嵌入 gui_kline 全部功能（takeCentralWidget，零改動原檔）— theme/i18n 跟隨外殼、退出觸發原 closeEvent 清理鏈；chart 實例換成 `indicators.IndicatorKlineChart`（同 index 替換）+ 指標開關掣列（`og="indtoggle"`，QSS 跟 theme）+ manager listener 雙向同步。可單獨運行。
- `gateway/pages/fulltest_page.py` - **[Page 2 全功能測試]**：嵌入 gui_fulltest 全部功能（takeCentralWidget，零改動原檔）— theme 傳播（頁面級 QSS + STATE_STYLE 換色）、退出觸發原 closeEvent 清理鏈。可單獨運行。
- `gateway/pages/connection_page.py` - **[Page 3 連綫測試]**：config.json 參數編輯（futu host/port、kline_num、source、IB clientId/symbol aliases）+ FUTU OpenD / IB Gateway 連綫速度測試（probe，QThread worker）。可單獨運行。
- `gateway/pages/futu_trade_page.py` - **[Page 4 FUTU 交易]**：OpenSecTradeContext 連線 + 帳戶列表（client-side 市場過濾）+ 下單（REAL 確認框；解鎖狀態**自動更新** — 新版 OpenD 只容許 GUI 解鎖，用無副作用 `unlock_probe`（modify_order fake id）定時/選帳戶即時探測 + 落單成敗被動推斷）+ 今日訂單（撤選定 / 全數撤）+ 持倉同帳戶資金 + 代碼模糊輸入（completer = gateway/symbol_input 全域共用，搜尋經 worker `stock_search` 異步 feed `set_hits`；**本地 symbol index** — modules/symbol_search 同 P8 共用：代碼/繁中/簡中/英文，含期貨主連 HK.HSImain；**dropdown item =「CODE  名稱（跟語言）」**，揀咗淨返 CODE 入欄；code 大細階經 index canonical 還原 — HSImain 細階 main，upper 變未知股票；落單前驗證 MARKET.CODE 格式，中文唔送入 place_order）+ 價格預填最新價、數量預填每手（手動改過唔覆蓋）+ **全流市價**（KLINE_STREAM 方式 — 訂閱 K_1M push，最新 close 即時更新，換 code 自動改訂閱，hint 顯示「串流中 ✅」如實狀態；呢個 OpenD 實測 QUOTE subtype push 永遠唔到、K 線 push 正常）+ 表格欄名/TIF 三語；SDK 調用全部行單一 QThread worker（queue 串行，futu lazy import）。可單獨運行。
- `gateway/pages/symbol_list_page.py` - **[Page 5 標的列表]**：全 index 表格（QTableView + generic model — 欄隨 mode，欄頭排序）+ 市場/種類兩組 exclusive FILTER 按鈕 + 模糊輸入（debounce；下鑽頁內 = 本地 substring 兩邊 t2s 通配）+ 一鍵更新（QThread fetch US+HK 全 plan + progress signal）+ 底部計數/顯示筆數/更新時間 + filter 記憶（state_store 'symbol_list'，WARRANT/OPTION 重載直接入下鑽起點）。**下鑽 mode 狀態機**：窩輪 w1（owner 分組計數）→w2（認購/認沽/牛/熊）→w3（列表連行使價/到期日；US 無 owner → 偽行直接落 w3）；期權 o1（股票/ETF 候選，如實冇枚舉接口）→o2（QThread 即時期權鏈 + cache + 面包屑/返回）。可單獨運行。
- `gateway/pages/favorites_page.py` - **[Page 6 標的收藏]**：收藏表格（code/名/市場/種類/加入日，欄頭排序、行多選）+ 市場/種類 FILTER（共用 symbol_list_page 常量 + 記憶）+ 新增（模糊輸入 = gateway/symbol_input，types=None 全種類；index 冇就 code prefix 兜底 + UNKNOWN）+ 刪除所選 + 計數/status。資料全部經 gateway/favorites.py。可單獨運行。
- `gateway/pages/indicators_page.py` - **[Page 7 指標管理]**：表格（QTableView + model — 啟用 checkbox / 指標名（acronym 語言中立）/ 位置 / 參數摘要）+ 新增（def combo / 位置 combo 只准入位置 / 參數 SpinBox 範圍經 ParamSpec）+ 揀行編輯 → 套用修改 + 移除所選 + 計數/status；經 IndicatorManager listener(origin) 同 K線頁開關掣列雙向即時同步。可單獨運行。
- `gateway/pages/gui_kline.py` - **[主 GUI（app 組件，K綫頁嵌入同一份）]**：模糊搜尋標的欄（completer = gateway/symbol_input）+ get/stream K 線圖表 + i18n 繁中/EN + FETCH。依賴：modules、symbol_search、symbol_input。
- `gateway/pages/gui_fulltest.py` - **[綜合測試矩陣 GUI（app 組件，全功能頁嵌入同一份）]**：18 rows 無輸入欄位 + FUTU/IB broker 分區 header（setSpan）；每行 ▶ / 雙擊獨立跑、Run All 全並發（per-row task）、stream 行持續 live（baseline → 即刻 PASS + tick 持續更新到 Stop，token 作廢 stale tick）、Stop cancel+清理、成功行數據拆疊（最新 bar 喺第一行）、隱藏 EXPECTED FAIL toggle（display-only）。依賴：modules。
- `.scratch/cli_kline.py` - **[CLI + contract tests]**：取數 CLI + CONTRACT / MAPPING_CASES regression（futu/ib 雙邊）。
- `.scratch/e2e_gui_p8.py` - **[P8 E2E]**：in-process QApplication 驗 fuzzy/i18n/FETCH（hermetic FakeDir，唔打網絡）。
- `.scratch/e2e_gui_fulltest.py` - **[fulltest GUI E2E]**：UI 結構 + 真 worker 端到端（futu rows）+ 3 rows 並發計時 + Run All wiring + Stop 清理。前置：OpenD。
- `.scratch/e2e_gui_futu_trade.py` - **[FUTU 交易頁 E2E]**：offscreen hermetic（stub futu FakeCtx — connect→accounts / SIM 下單 / orders 刷新 / 撤單 / positions+accinfo / REAL 解鎖狀態自動探測（未解鎖→模擬 GUI 解鎖→已解鎖，唔使落單）+ 確認框 path / 代碼模糊（FakeSearchDir 本地 index — 期貨主連排前/繁簡通配/中文名跟語言/dropdown 連名/HSImain canonical 大細階行情）+ subscribe 全流市價（K_1M push — handler `_emit` 注入 tick 驗 price 自動更新/手動唔覆蓋）預填 price/qty + 中文 code 落單被拒 / 欄名與 TIF 三語 retranslate / disconnect）+ nav 右鍵彈出視窗（同一實例搬出/喚返唔多開/關窗搬返 stack）+ i18n 三語 + theme；port 11111 開緊自動加跑 live read-only（絕不落單/撤單）。
- `.scratch/e2e_gui_quotes.py` - **[行情頁 E2E]**：offscreen 全 hermetic（fake broker stream_kline + fake symbol index + tmp state 檔）— shell 註冊（quotes 最前）/ 6 cells + 11 週期按鈕 / baseline→價→tick→throttle / stale token 作廢 / 換週期 exclusive / canonical 大細階 / 模糊輸入連名 / 無效代碼 ❌ / layout 切換 cells 實例不變 + 縮放均分斷言 / 本地記憶重載 / i18n 三語 / 清理。
- `.scratch/e2e_gui_symbol_list.py` - **[標的列表頁 E2E]**：offscreen 全 hermetic（fake directory + fake option fetcher + tmp state 檔）— shell 註冊 / 表格內容（type 翻譯 + name 跟語言）/ FILTER exclusive / 模糊輸入 types=None + 下鑽內繁簡通配 / 窩輪三級導航（分組計數→類別→列表、US 偽行直接落 L3、面包屑、逐級返回）/ 期權二級（o1 候選→o2 fake 鏈、失敗如實留 o1、cache 唔重 fetch）/ 計數 / 一鍵更新 / 記憶重載（WARRANT 直接入 w1）/ i18n 三語。
- `.scratch/e2e_gui_home.py` - **[首頁 E2E]**：offscreen 全 hermetic（fake pulse fetcher）— 註冊（PAGE_KEYS[0]=home / NAV_DIRECT 最前）/ 13 卡 + 三分區 / 數字格式（zh 萬億億、en B/M/K）/ 逐個失敗如實 / 刷新重入 / i18n 三語。
- `.scratch/e2e_gui_favorites.py` - **[標的收藏頁 E2E]**：offscreen 全 hermetic（fake directory + tmp state 檔）— shell 註冊（PAGE_KEYS + NAV_DIRECT）/ 空頁如實 / 新增（index snapshot + canonical code 還原、prefix 兜底 UNKNOWN、重複 ⚠️、唔似 code ❌）/ completer 本地候選 / FILTER 交集 + UNKNOWN 只喺 ALL / 刪除所選 / 記憶重載（items + filter）/ JSON 檔結構 / i18n 三語 / canonical 大細階保留（HK.HSImain）。
- `.scratch/e2e_gui_indicators.py` - **[指標管理 E2E]**：offscreen 全 hermetic（tmp state 檔 ×6 + FakeDir 包住真 index）— 純計算（BOLL rolling / ATR Wilder / MACD ewm 對照 + hist 定義；ICT：手砌 6 根 K 線逐條斷言 OB/FVG/VOB 區塊起訖與價格 + min_size/max_zones/sweep/strength 過濾）/ 主圖區塊真的砌出 PolyCollection / 管理頁 def combo + 參數欄 + 三語 / Manager（seed / CRUD / clamp / 上限 / 容忍 load / listener）/ IndicatorKlineChart（裸子类==parent、axes 增減、_view 保持、x 標籤遷移、panel 手勢、cache 計數 hover 0 重算）/ K線頁嵌入（換 chart、開關掣列、跨頁同步、theme QSS）/ 管理頁 CRUD + checkbox / shell 註冊。
- `.scratch/one-gate/issues/` - **[Ticket 流水線]**：複雜工作的斷點續傳 Checkpoint（完成即 [X] + 滾動清理）。

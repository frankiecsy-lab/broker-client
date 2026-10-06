# 📈 CHANGELOG - 版本變更日誌

> **唯一事實來源**：任何功能增刪、修改或技術架構調整，必須第一時間喺呢度同步更新（見 `AGENTS.MD` §1）。格式：倒序（最新喺上面）。

---

## 2026-10-06

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

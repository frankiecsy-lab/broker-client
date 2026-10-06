# 📊 Broker Client — 多券商 K 線客戶端

統一接口取 / 流式接收多家券商嘅 K 線數據（目前：**富途 Futu** + **Interactive Brokers IB**），配 PySide6 GUI。所有 broker 一律回 `(status, data, message)` triple，失敗如實回報原因（無標的 / 無權限 / timeout 三句唔同）。

## ✨ 核心功能

- **統一 K 線契約**：`get_kline()` / `stream_kline()` 全部 broker 同一形狀；K 線 DataFrame schema 單一來源（`time_key, open, high, low, close, volume`），成功前自動驗證。
- **並發安全**：futu 每條 stream / 每次 get 各開獨立 OpenD 連線；IB 多 request 共用一條 TWS session、per-request error 歸屬（reqId scoping）— Run All 全矩陣可同時跑。
- **Symbol mapping L0–L4**：canonical = **Futu quote code**（用戶用 Futu app 對照）。L0 normalize → L1 config alias（IB-only pin）→ L2 per-client `resolve_symbol()`（IB 自動解析 front month，per-session cache）→ L3 broker 原生 fallback → L4 honest fail。永遠唔 hardcode 合約月份。
- **Error honesty**：IB errorEvent per-request capture + code→人話映射；「無標的 / 無 permission / timeout」如實分開講。
- **GUI 模糊搜尋 + i18n + FETCH**（`test/gui_kline.py`）：標的欄 autocomplete（中/英文名、繁簡通配，local index ~24,800 entries）、繁中/EN 一鍵切換（symbol name 跟隨）、FETCH 手動 refresh index（stale >24h 自動 fetch）。
- **綜合測試矩陣 GUI**（`test/gui_fulltest.py`）：18 rows = futu/ib × 6 類標的 × get/stream；FUTU / IB **broker 分區 header**、每行獨立跑、**Run All 全部同時並發**、Stop cancel + 清理所有結果、成功行數據可拆疊顯示（**最新 bar 喺第一行**）、右上角 toggle **隱藏 EXPECTED FAIL** rows（display-only，唔影響 Run All）。
- **One Gate 多券商匯合外殼**（`python gateway.py`）：頂部導航 + QStackedWidget 三頁（K綫測試 / 全功能測試 / 連綫測試，其餘頁面預留位）、繁中/簡中/EN 三語明確字串切換、暗/淺色 theme 一鍵切換；每頁獨立組件可單獨開視窗 Debug。

## 🚀 安裝與運行

```bash
pip install PySide6 pandas futu-api ibapi opencc-python-reimplemented
# opencc 係非硬依賴（冇就 fallback 唔做繁簡轉換）
```

前置：OpenD（Futu，port 11111）+ TWS / IB Gateway（IB，port 4001）開緊。配置喺 `modules/config.json`（host/port、kline_num、source 預設 broker）。

| 入口 | 用途 |
|---|---|
| `python gateway.py` | **One Gate** 主窗口：三頁導航（K綫 / 全功能 / 連綫）+ 三語 + 暗/淺色 theme |
| `python test/gui_kline.py` | 主 GUI：模糊搜尋標的 + get/stream K 線 + i18n + FETCH |
| `python test/gui_fulltest.py` | 綜合測試矩陣（全並發 Run All） |
| `python test/cli_kline.py` | CLI 取數 + contract / mapping regression tests |

## 🗺️ 檔案地圖

- `AGENTS.MD` - **[AI 開發規範]**：定義 Agent 溝通語言（廣東話）、CHANGELOG/README 維護責任、環境清理與切片式讀取原則。
- `CHANGELOG.md` - **[版本變更日誌]**：所有功能增刪 / Bug 修復 / 架構調整嘅唯一事實來源（倒序）。
- `modules/__init__.py` - **[package 入口 + logging config]**：app-level log format；匯出 BrokerClient / BROKERS / KLINE_COLUMNS。依賴：registry、broker、kline_schema。
- `modules/broker.py` - **[BrokerClient 統一 dispatch]**：config-driven 實例化所有 registry client；`get_kline(broker=)` / `stream_kline(broker=)` 按 broker 參數 > config.source 分發；`__aexit__` 斷晒所有連線。依賴：registry、config.json。
- `modules/broker_base.py` - **[統一契約 BrokerBase]**：所有 client 嘅 ABC — NAME + get_kline/stream_kline triple 形狀 + `resolve_symbol()`（L2 hook，預設 identity）+ disconnect no-op。
- `modules/registry.py` - **[券商名單單一來源]**：`BROKERS = {NAME: cls}`；加新 client 只改呢度一行。依賴：futu_client、ib_client。
- `modules/kline_schema.py` - **[K 線 schema 契約]**：標準 6 欄定義 + reorder_kline + validate_kline（成功前驗證）。被兩個 client 共用。
- `modules/futu_client.py` - **[富途 client]**：每次 get / 每條 stream 獨立 OpenQuoteContext；>1000 bars 自動分頁；handler→queue buffer live push。依賴：futu SDK、broker_base、kline_schema。
- `modules/ib_client.py` - **[IB client]**：共享 TWS session（clientId=99，lock-guarded idempotent connect）；per-request errorEvent capture + code 映射；期貨 front month 自動解析（reqContractDetails，session cache）。依賴：ib_async、broker_base、kline_schema。
- `modules/symbol_search.py` - **[本地 symbol index + fuzzy search]**：US+HK 股票/ETF/指數/期貨 main 目錄（~24,800 entries）；繁簡正規化 match + scored ranking + stale auto-fetch。依賴：futu SDK、opencc（可選）。
- `modules/symbol_index.json` - **[symbol index cache]**：gitignored，由 symbol_search fetch 寫入（~3.7MB）。
- `modules/config.json` - **[運行配置]**：host/port、kline_num、source 預設 broker、ib.symbol_aliases。
- `gateway.py` - **[One Gate 主入口（thin launcher）]**：`python gateway.py` → `gateway/app.py::main()`；同同名 `gateway/` package 安全共存（CPython FileFinder 先查目錄）。依賴：gateway.app。
- `gateway/__init__.py` - **[package 入口]**：版本號 + 用法說明，無重 import。
- `gateway/i18n.py` - **[三語 i18n 單一入口]**：STRINGS dict（zh_hk/zh_cn/en 明確字串）+ `t()` fail-fast + theme 按鈕文字 helper。無依賴。
- `gateway/theme.py` - **[theme QSS 中央生成]**：dark（= gui_kline 配色）/ light palette + string.Template QSS；`apply_theme()` 一鍵切換 + listener registry（`add_listener(fn)`，嵌入頁跟隨 theme）。依賴：PySide6（lazy import）。
- `gateway/app.py` - **[One Gate 主外殼]**：OneGateWindow — 頂部導航欄（brand + 3 nav 按鈕 + disabled 預留位 + 語言/theme）+ QStackedWidget；PAGE_KEYS registry 生成頁面。依賴：i18n、theme、pages/*。
- `gateway/pages/__init__.py` - **[pages package 入口]**：加新頁說明（檔 + PAGE_KEYS 一行）。無 import。
- `gateway/pages/base_page.py` - **[standalone window 基類 + run_standalone()]**：StandaloneWindow 包任何 page 組件成獨立視窗（標題欄 + 語言 combo + theme 按鈕）；各頁底層調用佢單獨 Debug。依賴：i18n、theme。
- `gateway/pages/kline_page.py` - **[Page 1 K綫測試]**：嵌入 gui_kline 全部功能（takeCentralWidget，零改動原檔）— theme/i18n 跟隨外殼、退出觸發原 closeEvent 清理鏈。可單獨運行。
- `gateway/pages/fulltest_page.py` - **[Page 2 全功能測試]**：目前 placeholder 卡片；#04 將嵌入 gui_fulltest 全部功能。可單獨運行。
- `gateway/pages/connection_page.py` - **[Page 3 連綫測試]**：目前 placeholder 卡片；#05 將實裝 config.json 參數編輯 + FUTU OpenD / IB Gateway 連綫速度測試。可單獨運行。
- `test/gui_kline.py` - **[主 GUI]**：模糊搜尋標的欄 + get/stream K 線圖表 + i18n 繁中/EN + FETCH。依賴：modules、symbol_search。
- `test/gui_fulltest.py` - **[綜合測試矩陣 GUI]**：18 rows 無輸入欄位 + FUTU/IB broker 分區 header（setSpan）；每行 ▶ / 雙擊獨立跑、Run All 全並發（per-row task）、Stop cancel+清理、成功行數據拆疊（最新 bar 喺第一行）、隱藏 EXPECTED FAIL toggle（display-only）。依賴：modules。
- `test/cli_kline.py` - **[CLI + contract tests]**：取數 CLI + CONTRACT / MAPPING_CASES regression（futu/ib 雙邊）。
- `test/e2e_gui_p8.py` - **[P8 E2E]**：in-process QApplication 驗 fuzzy/i18n/FETCH（hermetic FakeDir，唔打網絡）。
- `test/e2e_gui_fulltest.py` - **[fulltest GUI E2E]**：UI 結構 + 真 worker 端到端（futu rows）+ 3 rows 並發計時 + Run All wiring + Stop 清理。前置：OpenD。

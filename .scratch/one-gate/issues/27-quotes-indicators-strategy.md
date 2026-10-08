# 27 — 行情頁：顯示指標 + 策略 B/S 標記（串流同步）

需求（用戶）：行情頁加「顯示指標」選項；加策略下拉，點選策略 → K 圖買點賣點畫 B / S；K 線串流刷新時指標與 B/S 必須同步更新。

設計：
- 指標 = 重用 `IndicatorManager` singleton + `IndicatorKlineChart`（行情頁 6 格 chart 換子类；頂欄一個總開關，開 = 注入 manager，所有格跟指標管理頁配置）。
- B/S = `strategies.trade_marks(entry, ohlc)` 純函數（score_series→trigger_indices 同一契約，價=觸發根收盤）；chart `set_strategy(entry|None)`，marks cache 以 (data_seq, 規則指紋) 為 key → set_bars 即自動失效重算 = 串流同步由結構保證。
- 策略下拉喺頂欄（無策略 + 策略清單）；cell 標的 == 策略 code 先注入（逐格 match）。

1. [X] strategies.py：`trade_marks` 純函數
2. [X] indicators.py：IndicatorKlineChart `set_strategy` + marks cache + B/S 繪畫（指標關咗都要畫）
3. [X] quotes_page：chart 換 IndicatorKlineChart + 頂欄開關/策略下拉 + 標的 match + 狀態記憶 + i18n 三語
4. [X] 測試：test_strategy_conditions 加 trade_marks + e2e_gui_quotes 加 Part（開關 / B/S / 串流同步）
5. [X] README / CHANGELOG 同步

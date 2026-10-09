# 33 — 回測頁（主菜單直按）

需求（用戶）：主菜單加「回測」— 標的／策略／週期／根數 → **非常詳細**嘅結果。
用戶明確取捨：
- **唔准復利**：要「策略客觀每次嘅表現」→ 每筆**等額注碼**（固定名義 = 初始資金），淨值曲線 = 累加 P&L（單利）。
- 成本：手续费% + 滑價%，**參數可調**（兩邊均計）。
- 持倉處理要**喺結果入面體現**：訊號統計（含被忽略）、覆蓋率（喺市場時間%）、逐筆持倉根數/日數、未平倉 mark-to-market 如實。
- 指標四組（收益／風險／綜合風險回報／交易執行）全部要有，並盡量補充。

持倉模型 v1（已擵定）：長倉單倉 — 空倉遇 B 開倉、持倉遇 S 全平；持倉中 B / 空倉 S = 忽略並如實計數。
成交價 = 訊號根收盤（同 `strategies.trade_marks` 現有語義，冇未來函數）。

## Checkpoints

1. [X] 33a 領域層 `gateway/backtest.py`（純計算、冇 Qt、冇 broker）— `.scratch/test_backtest_engine.py` 全綠
2. [X] 33b 頁 `gateway/pages/backtest_page.py` + `gateway/ui/backtest_page.ui` + i18n 三語 + 外殼 registry
3. [X] 33c 淨值曲線（QPainter 自繪，疊 Buy&Hold + 回撤帶，跟 theme）+ K 圖 B/S 覆核（重用 `IndicatorKlineChart.set_strategy`）
       — `.scratch/e2e_gui_backtest.py` **79 項全綠**（持倉處理三處體現、成本逐邊計、token 作廢遲到結果、
         髒 state clamp、曲線 = 是次 `curve` 原樣、圖上 B/S == 交易明細+被忽略表、失敗即收圖）
4. [X] 33d README（核心功能 + 十一頁導航 + 檔案地圖 5 條）/ CHANGELOG（MINOR 一則）同步
       — 逐項測試出示 Log：`test_backtest_engine.py` **70 項全綠**、`e2e_gui_backtest.py` **79 項全綠**（合共 149）

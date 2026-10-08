# 30 — 策略訊號套用到全部 K 線圖（1/2/4/6）+ BUFFER 即時同步

需求（用戶）：「BUFFER 的買賣訊號在BUFFER 中没有變淺色」「買賣訊號及指標要同時套用在行程的全部K綫圖1,2,4,6图」。

診斷：
- 淡化機制 E2E 已驗證（同路徑）；真正缺口 = ①策略只注入標的 match 嘅格 → 其他圖完全冇訊號 ②策略頁改 mark_buffer 後，行情頁靠 showEvent 先 re-apply → 策略頁做彈出窗時行情頁唔會 showEvent → 舊 entry 副本照用（改 BUFFER 冇反應）。
- 語義重申：得最新 bar 對開 N 條內嘅 mark 先淡化；超過 N 條照實色。

設計：
- `_apply_strategy_to_cells`：移除標的 match gate → 描咗策略即注入全部 6 格（每格用自己數據計 B/S = 同一套規則逐格計）。指標本來已全格注入。
- `StrategyManager` 加 listener（照 `IndicatorManager.add_listener/_notify` 模式）：add/update/remove 成功後 `_notify(kind)`；行情頁註冊 → 即時 rebuild combo + re-apply（任何 origin，包括彈出策略頁）。

1. [X] strategies.py：_listeners + add/remove/_notify + add/update/remove 觸發
2. [X] quotes_page：去 match gate（全格注入）+ 註冊 listener `_on_strat_config`；改標的唔再 re-apply
3. [X] 測試：e2e_gui_quotes（cell1 反轉 = 都有 B@2；listener → update mark_buffer=0 即時全實色）+ e2e_gui_strategies（listener 觸發 add/update/remove）
4. [X] README / CHANGELOG 同步

# 34 — 量化交易頁（主菜單直按）

需求（用戶）：主菜單加「量化交易」— 策略驅動嘅**即時／自動**交易。
用戶明確取捨（AskUserQuestion 原話）：
- 落單模式：「兩者都要，頁上有開關」→ 全自動 + 半自動（半自動 = 訊號入「待執行」清單，人確認先落單）。
- 交易環境：「實盤模擬都可以FUTU IB 都要」→ REAL / SIMULATE 都要。
- 券商：「FUTU + IB」→ 兩邊都要有交易能力。
- 持倉模型：「長短雙向（支援沽空）」。
- 回測口徑：「回測試交易都可以選，長倉，短倉，雙向，BACKTEST 都可以選」→
  長倉/短倉/雙向 enum **回測頁同量化頁都要可揀**，共用一份領域層定義。
- 風控（全自動）：「最小三項（推薦）」= 最大同時持倉數、每日最大筆數、同一標的訊號冷卻根數，全部可調。

## Checkpoints

1. [X] 34a 持倉模型單一事實來源 `gateway/position_model.py`（long/short/both 狀態機、純計算）
       + 接入 `gateway/backtest.py`（`norm_opts.mode`、`_settle` 方向、sgn 序列、+2 指標、meta.mode）
       — `.scratch/test_position_model.py` 全綠 + `test_backtest_engine.py` 零回歸
2. [X] 34b 券商交易能力 `modules/trade_base.py`（capability contract，**唔改 `BrokerBase`**）
       + `FutuClient.TRADE` / `IBClient.TRADE` 實作 + `BrokerClient` 能力分派（`trade_supported` 唔靜默轉券商）
       + `config.json source.trade` + `futu_trade_page` 欄位 tuple 改指向契約（一來源）
       ⚠️ 期間修咗真 bug：`clamp_env` 用 uppercase 查細階 key alias 表 → REAL 永遠變 SIMULATE（會錯錢）
       — `.scratch/test_trade_contract.py` 57 項全綠 + `e2e_gui_futu_trade.py`/`e2e_gui_shell.py` 零回歸
3. [X] 34c 執行層 `gateway/quant_exec.py`（純計算、冇 Qt：訊號→訊號去重／baseline watermark／
       未收根過濾／風控 RiskGuard／半自動待執行佇列）
       口徑同源：訊號 = `strategies.trade_marks`、持倉 = `position_model.step`（同回測完全一致）
       即時三道閘：watermark（略過歷史訊號並計數）、`bar_idx<=n-2`（未收根唔落單）、去重 key=(binding,bar,side)
       全自動每次 poll 只行一個訊號（其餘 deferred 留返下一輪，唔會漏）；半自動入 PendingQueue、唔推進狀態
       ⚠️ 期間修咗真 bug：`ohlc.get('c') or []` 對 ndarray 會直接 ValueError 炸（truth value ambiguous）
       — `.scratch/test_quant_exec.py` 75 項全綠 + `test_position_model`/`test_backtest_engine`/`test_trade_contract` 零回歸
4. [X] 34d 頁 `gateway/pages/quant_page.py` + `gateway/ui/quant_page.ui` + i18n 三語 + 外殼 registry
       （綁定表／風控卡／待執行表／持倉表／訂單表／事件日志、全自動⇄半自動開關、REAL/SIMULATE）
       ⚠️ 期間修咗真 bug：狀態行只喺 `watching` 轉嗰陣先改 → 監控中加綁後「監控中(N)」嘅 N 留低訂閱時嘅數
         （如實口徑缺陷）；`_watch_shown` 改做 `(watching, 綁定數)` 元組
       — `.scratch/e2e_gui_quant.py` 129 項全綠
5. [X] 34e 回測頁加「持倉模式」下拉（長倉/短倉/雙向）+ 交易明細方向欄
       ⚠️ 期間發現：表 model `set_rows` 只 alias 傳入 list、`sort()` 原位排 → 撳過表頭會改到
         測試手上嘅 `res['trades']` 順序（斷言逐筆對數要用 entry_idx 做 key）
       — `.scratch/e2e_gui_backtest.py` 101 項全綠
6. [X] 34f README（核心功能 + 十二頁導航 + 檔案地圖）/ CHANGELOG 同步
       — 逐項測試出示 Log（含全部零回歸 suite）
       README 核對過全部數字（指標 62 = ret18/risk14/adj8/exec22、i18n 613 鍵、mode_*/bt_ig_* key）→ 已準確，無需改
       CHANGELOG 加 #34 條目；⚠️ 期間修咗測試基建 leak：外殼級 E2E 收工清理 hardcode 頁名 → 新頁 thread 無停 → exit(127)

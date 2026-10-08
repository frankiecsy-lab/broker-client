# 25 — 策略管理頁（分數制條件）

需求：主選單加「策略」頁 — 新增/修改/刪除策略、本地保存、兼容日後 BACKTEST。
內容：標的、買入條件（MA 交叉 / BOLL 穿線 / VOB 穿線，每條有分數，累加 ≥100 分觸發）、
賣出條件同上、買入價=市價、數量=最低一手、生效期 1日/7日/永久。

1. [X] 領域層 gateway/strategies.py（CONDITION_DEFS registry + 分數契約 + StrategyManager + state_store section 'strategies'）
2. [X] i18n 三語字串（nav_strategies + str_*）
3. [X] gateway/pages/strategies_page.py（表格 + 條件編輯表單含分數欄）
4. [X] app.py 註冊（PAGE_KEYS / NAV_DIRECT）
5. [X] 測試：.scratch/test_strategy_conditions.py（21 項）+ .scratch/e2e_gui_strategies.py（33 項）全部通過 ✅
6. [X] README / CHANGELOG 同步

✅ 完成 — 分數制策略頁落地；條件契約（rule_signal / score_series / trigger_indices）live 與 BACKTEST 共用。

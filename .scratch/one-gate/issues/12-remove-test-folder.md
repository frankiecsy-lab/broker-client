# 12 — 移除 test/ folder（用戶指示：需要嘅 test 檔放 .SCRATCH，目標刪走 test/）

1. [X] `gui_kline.py` / `gui_fulltest.py`（app 組件）→ `git mv` 入 `gateway/pages/`；bootstrap 深度改返（2→3 層 dirname）
2. [X] `kline_page` / `fulltest_page` / `quotes_page` import 改 `from gateway.pages import gui_kline as gk`（刪 TEST_DIR sys.path hack）
3. [X] e2e ×5 + `cli_kline.py` → `git mv` 入 `.scratch/`；p8/fulltest e2e 嘅 gui import 改新 path；docstring 運行路徑同步
4. [X] 全部 e2e 重跑全綠（symbol_list 已 ✅ 43 checks；quotes/p8/futu_trade/fulltest 跑緊）
5. [X] README（入口表 + FILE_MAP）/ CHANGELOG / theme.py comment 同步

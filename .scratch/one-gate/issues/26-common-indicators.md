# 26 — 常用指標（MA / KDJ / RSI）

需求：指標管理加常用指標 — MA（主圖）、KDJ / RSI（副圖）。MACD 已內置，唔重複。

1. [X] indicators.py：compute_ma / compute_kdj / compute_rsi + INDICATOR_DEFS + _PLOTTERS
2. [X] i18n：ind_desc/use_ma·kdj·rsi + 新 ind_p_* / ind_n_*（三語）
3. [X] 測試：.scratch/test_indicators_common.py（純計算）+ test_ict_suite registry 斷言跟更新
4. [X] README / CHANGELOG 同步

✅ 完成 — MA（主圖 4 條 SMA）/ KDJ / RSI（副圖）入 INDICATOR_DEFS，三語 i18n 齊，純計算 16 項 + ICT registry + 指標頁 E2E 全通過。

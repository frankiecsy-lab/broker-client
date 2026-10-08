# 19 — MAJOR FEATURE：指標管理（指標計算 + 疊加 K 線主圖/副圖 + 管理頁 + 顯示開關）

用戶：主菜單新增「指標管理」，可以新增、修改、移除主圖及副圖指標；用一個 CLASS／檔案專門負責指標計算＋疊加 K 線；K 線圖顯示主圖指標同副圖指標兩部分；首批指標 MACD／ATR（後改：主圖示範 = BOLL，MACD 保留做副圖選項）；參數（計算日數）喺指標管理可調；有掣切換每個指標是否顯示喺 K 線圖上。入口 = 頂層直按按鈕。零改動 gui_kline.py（用戶明確要求）。

設計：`gateway/indicators.py` = 指標領域單一事實來源（INDICATOR_DEFS registry BOLL/ATR/MACD + 純計算 + IndicatorManager → state_store section 'indicators' + listener(origin)，照 theme/favorites pattern）+ `IndicatorKlineChart(gk.KlineChart)` 子类（動態 gridspec：主圖→volume→指標 panel；per-chart 計算 cache：data_seq+config_version；parent `_redraw` 讀實例屬性 → 重新綁定 ax/axv 後 super() 照畫）。`kline_page` 換 chart 實例為子类 + 開關掣列（三語經 i18n）+ `_EXTRA_QSS`。`indicators_page` 管理頁 CRUD。app.py：PAGE_KEYS/NAV_DIRECT 加 'indicators'。

1. [X] gateway/indicators.py：INDICATOR_DEFS（boll/atr/macd）+ ParamSpec + 純計算函數（numpy，無 Qt；全長度輸出 NaN warm-up）
      驗證：.scratch/test_indicators_calc.py 全綠（含 Wilder/ewm/rolling 對照、hist 定義）
2. [X] IndicatorManager：CRUD + clamp/容忍 load + config_version/layout_version + add_listener(origin) + get_manager()/reset_manager_for_test() + seed BOLL(main)+ATR(sub)
      驗證：.scratch/test_indicators_calc.py 全綠（seed/add/update/remove/上限/降級/listener）
3. [X] IndicatorKlineChart 子类：_build_axes()（fig.clear 重新綁定 ax/axv/ax_ind，sharex）+ _redraw override（super 後畫指標：slice 由 _s+xlim 推出、主圖 Y fit 擴張、panel 標題、x 標籤遷移最底軸、crosshair 補畫）+ set_bars override（_data_seq）+ 手勢 override（_on_motion/_on_press/_on_scroll inaxes 改寫）+ cache
      驗證：.scratch/test_indicators_chart.py 全綠（axes 增減、_view 保持、hover 零重算、panel 手勢、x 標籤遷移）
4. [X] kline_page：換 chart 實例（layout 同 index 替換，喺 _apply_embedded_theme 之前）+ 注入 get_manager() + 開關掣列 insertWidget + _EXTRA_QSS（og="indtoggle"）+ listener（origin 過濾）+ retranslate
      驗證：.scratch/test_kline_page_ind.py 全綠（掣列增減/跨頁同步/theme QSS/retranslate）
5. [X] indicators_page（表格 + 新增/修改/移除 + 跨頁同步）+ i18n（nav_indicators/page_indicators_title/ind_* 三語）+ app.py 註冊（PAGE_KEYS/_PAGE_CLASSES/NAV_DIRECT）
      驗證：.scratch/test_indicators_page.py 全綠（CRUD/checkbox/跨頁同步/retranslate/註冊）；e2e_gui_futu_trade nav 分組斷言已同步加 indicators
6. [X] e2e 新檔 .scratch/e2e_gui_indicators.py（Part 1–8 全綠）+ 同步舊 e2e nav 斷言 + 跑晒舊 suite
      驗證：quotes/p8/home/symbol_list/fulltest/favorites/futu_trade 全部 exit 0；futu_trade 曾 exit=127 — 根因係 cleanup 清單漏咗 quotes 頁 LoopThread（既有問題，已補 'quotes' 入清單）；4 個暫測腳本已滾動刪除
7. [X] README（核心功能 + 檔案地圖）+ CHANGELOG + live smoke
      驗證：.scratch/cli_indicators_live.py 全綠（真 OpenD HK.HSImain K_1M 串流 200 bars → BOLL/ATR 末端有值、panel 標題、light theme draw-time 跟色、開關 _view 保持）。🤖 教訓：parent 對出界 `_view` 會主動返跟隨模式（設計如此），smoke 嘅 view 必須設喺數據範圍內

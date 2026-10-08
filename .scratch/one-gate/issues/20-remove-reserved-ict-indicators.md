# 20 — 主菜單刪預留言面 + 指標管理新增 3 個 ICT 指標（OB / FVG / VOB）

用戶：主菜單刪除預留言面；指標管理增加 3 個 ICT 指標 — OB、FVG 同有效 OB = VOB。

設計：預留位 = `app.py` 嘅 disabled `nav_reserved` 按鈕（i18n `nav_reserved`/`nav_reserved_tip`）→ 全刪。
ICT 三個全部 `position='main'`（價格軸區塊），**沿用現有純計算契約**：`compute() → dict[str, ndarray 全長度]`，
區塊用 `bull_top/bull_bottom/bear_top/bear_bottom` 四條陣列（區塊外 NaN）→ 主圖 Y-fit 同 `_slice` 自動啱；
唔引入第二種資料形態（🤖 唔准用 0/1 方向旗標 — 會炸大 Y-fit 範圍）。繪畫 = `fill_between(where=mask)`，
bull 用 `gk.C_UP`、bear 用 `gk.C_DOWN`（draw-time 跟 theme）。

1. [X] app.py 刪 `reserved_btn`（建檔 + retranslate + docstring）+ i18n 刪 `nav_reserved`/`nav_reserved_tip`
      驗證：e2e_gui_futu_trade nav 斷言補「冇 reserved_btn」→ exit 0；home/favorites/quotes/p8/symbol_list/fulltest 全部 exit 0
2. [X] indicators.py：`compute_fvg` / `_ob_candidates` + `compute_ob` / `compute_vob`（VOB = OB + 掃流動性 + 位移段有 FVG）
      + `_zones_to_arrays` + INDICATOR_DEFS 加 ob/fvg/vob（ParamSpec：period/strength/confirm/sweep/min_size/max_zones）
      驗證：手砌 6 根 K 線 fixture 逐條斷言區塊起訖與價格（e2e Part 9 全綠；🤖 一次過全綠，無改算法）
3. [X] `_plot_zones` + `_PLOTTERS` 註冊；i18n 加 `ind_p_*` 五個參數名三語
      驗證：主圖多 6 個 PolyCollection（每指標 bull/bear 各一）+ main 唔砌 panel；管理頁 def combo / 5 個參數欄 / 三語 label 全綠
4. [X] e2e 擴展 `.scratch/e2e_gui_indicators.py`（Part 9，57 checks 全綠）+ live smoke 擴展 + README/CHANGELOG 同步
      驗證：`.scratch/cli_indicators_live.py` 真 HSI K_1M 全綠（OB 2 段 / FVG 4 段 / VOB 5 段、掣列 5 個、截圖 ict_zones_live.png）；暫測 test_ict_zones.py 已滾動刪除
      ⚠️ 三個區塊指標同時開（各 max_zones=15）喺 1 分鐘圖會好密 — 如實反映，要清爽就調細 max_zones 或關掉其中一個

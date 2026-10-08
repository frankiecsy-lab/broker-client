# 21 — ICT 全套常用指標 + 每個指標/參數一行說明（可摺疊）

用戶：增加 ICT 所有常用指標，一樣可參數化；所有指標都要有一行描寫同點樣用，喺指標管理列表顯示；每個參數都要詳細解一行，但可以拆叠顯示全部。

**ICT 清單（全部價格可判定、全部參數化）**：既有 OB / FVG / VOB 之外新增
`BOS`（結構突破）、`CHoCH`（Character 改變）、`LIQ`（流動性掃蕩/Stop Hunt）、`EQHL`（等高等低流動性池）、
`PD`（溢價/折讓 dealing range + 50% 均衡）、`OTE`（0.62–0.79 最佳入場帶）、`BRK`（Breaker Block = 失效 OB 反轉用）、
`BPR`（Balanced Price Range = 兩個反向重疊 FVG）。
🤖 時間類（Kill Zones / 亞洲盤 / 日開高低）暫不做：compute 只收 o/h/l/c，要再加時間欄 + x 範圍繪畫形態。

**契約**：沿用 `compute(ohlc, params) → dict[str, ndarray 全長度]`。
- 區塊類（EQHL/OTE/BRK/BPR）→ `_zones_to_arrays`（bull/bear top/bottom，區塊外 NaN）。
- 水平位類（BOS/CHoCH/LIQ）→ `_levels_to_arrays`（top==bottom=價位），繪畫 `_plot_levels`（hlines + 起點三角 + 標籤）。
- 帶狀（PD）→ 自訂 key `hi/mid/lo`，繪畫 `_plot_band`。
🤖 一律用價格值，唔准 0/1 旗標（主圖 Y-fit 會 concat 晒所有陣列）。

**說明**：`IndicatorDef.desc_key`（一行）+ `usage_key`（點樣用）；`ParamSpec.note_key`（每個參數一行）。
管理頁：表格加「說明」欄（一行，跟語言；完整用法喺 tooltip）+ 可摺疊詳情面板（預設收起只睇一行，展開顯示完整用法 + 每個參數一行）。K線頁開關掣 tooltip 帶 desc。

1. [X] indicators.py：`_swings` / `_structure_breaks` / `_levels_to_arrays` / `_level_spans` / `_fvg_candidates`（抽出去俾 FVG+BPR 共用）
      + compute_bos / compute_choch / compute_liq / compute_eqhl / compute_pd / compute_ote / compute_breaker / compute_bpr + 8 個 INDICATOR_DEFS（共 14 個）
      驗證：`.scratch/test_ict_suite.py` 7 個手砌 fixture（A 結構 / B 掃蕩 / C EQHL / D PD / E OTE / F BRK / G BPR）+ registry = 55 checks 全綠 exit 0
      🤖 事實：向下突破（CHoCH 轉空）用 bear_* 陣列 → 語義色同 OB 家族一致；重疊水平位較新者覆蓋（BOS 兩條喺 j=8 處覆蓋）
2. [X] `_plot_levels`（逐根 hlines 砌橫線 + ^/v 三角 + BOS/CHoCH tag）/ `_plot_band`（dealing range 淡色填充 + hi/lo 細線 + 均衡虛線）
      + `_PLOTTERS` 註冊（`partial(_plot_levels, tag=...)`）
      驗證：e2e Part 10 — 8 個新 def 逐個加入，主圖 artists 增量 **逐個等於按契約算出嘅期望**
      （brk/bpr/eqhl/ote = 2 個 fill；bos = 2 線 + 2 三角 + 10 tag；choch = 2/2/6；liq = 2/2/0；pd = 1 fill + 3 線）；axes 唔變（main 唔砌 panel）
3. [X] i18n：14 個 `ind_desc_*` + 14 個 `ind_use_*` + 17 個 `ind_n_*` 參數解釋 + 7 個 `ind_p_*` 參數名 + 5 個詳情面板 key（三語）
      驗證：test_ict_suite [R] — 63 個 key × 3 語言全部非空、無 KeyError
4. [X] indicators_page：表格「說明」欄（Stretch 欄 + tooltip 帶完整用法）+ 可摺疊詳情（`ind_detail_toggle`，預設收起）；
      kline_page `_ind_tooltip()`（掣 tooltip 帶一行描寫，兩處原本重複嘅拼接合併咗）
      驗證：e2e Part 10 — 表頭三語 / 說明欄文字 / 展開收起 / 轉類型即時跟 / 三語 / 掣 tooltip 全綠
5. [X] e2e 擴展（Part 10）+ live smoke 擴展 + README/CHANGELOG 同步（repo 冇 FILE_MAP.md，已確認）
      驗證：e2e_gui_indicators 全綠 ✅；舊 suite（indicators/futu_trade/p8/home/favorites/quotes/
      symbol_list/test_symbol_search_fuzzy/fulltest）全部 exit 0；真 HSI K_1M live 全部通過 ✅
      （bos 2 段/3 標記、choch 7/7、liq 10/10、eqhl 2 段、pd 171 根、ote 2、brk 3、bpr 4、掣列 6 個）
      🤖 如實記錄：PD 預設 lookback=100 > 60 根 → 全 NaN 係正確行為，live 改用 lookback=30；
      CHoCH 喺 60 根趨勢可以合法為 0 → live 只斷言結構一致（mark ⊆ 位）
6. [X] 跟進：用戶反映「VOB 區域連續、唔係獨立方塊」→ `_zone_boxes()` 喺 level 變化位切段，每個方塊獨立一個 fill
      成因（`.scratch/diag_vob_zones.py` 真數據印證）：同向區塊重疊 → 陣列「較新者覆蓋」level 中途跳，
      舊繪畫只按 NaN 斷段 → 真 HSI 200 根 `[94..199]` 一段內含 6 個 level 砌成一大片
      驗證：e2e 期望改按方塊數計（`exp_boxes` 獨立實作）+ 手砌重疊 fixture 斷言切成 2 個方塊（x 邊界 ±0.5）
      → e2e / test_ict_suite / cli_indicators_live / fulltest 全部 exit 0；live 可見窗 11 個方塊（之前 4 大片），
      截圖 `.scratch/vob_boxes_live.png`
      🤖 未改（等用戶拍板）：OB/VOB 冇擋超大燭 — 真數據有 76 點 = 6.5×ATR 嘅「OB」（位移燭，唔係訂單塊）
7. [X] 用戶拍板 → OB 家族（OB/VOB/BRK）加 `max_size`（區塊最大高度 ×ATR，預設 3）+ `pen`（失效深度 %區塊，預設 50；
      100 = 舊行為）；`_ob_candidates` 過濾位移燭、`_ob_zones`/`compute_breaker` 按 pen 判「被消耗」；i18n +4 key、`ind_use_ob` 文案跟住改
      驗證：Fixture H（pen 0/50/100 各喺 j=7/j=8/j=10 失效 + 影線插穿但未死 + max_size 用 fixture 自己嘅 0.80 比例 ±0.1）
      + registry 三個 def 一致 + e2e 參數欄 7 個 / 摘要 `14/1/5/5/3/50/15` → test_ict_suite / e2e / live ×2 / fulltest 全部 exit 0
      效果（真 HSI 200 根）：區塊 12→10、最大 高/ATR 6.48→2.31、多數唔再拖到最後一根
      🤖 live 修正：EQHL 短窗可以合法為 0（同一日兩次 0 段 對 2 段）→ `MAY_BE_ZERO = choch/eqhl/brk/bpr` 只斷言結構+幾何，數量改為報告
8. [X] 跟進：用戶反映「有啲 VOB 獨立出嚟，同 K 線冇連接同關係」→ 主圖 Y-fit 距離閘（`FAR_OVERLAYS` + `FIT_PAD=0.25` + `_fit_vals`）
      成因（真 HSI 300 根 K_15M、睇最後 25 根）：早期形成、收盤從未進入嘅 OB 永遠有效（end=n-1），
      兩個 24278–24295 區塊離可見 K 線 154 點 = 可見範圍 57%，Y-fit 照全計 → Y 軸 = K 線範圍 1.66 倍，蠟燭縮晒
      修法：ICT 疊加只有喺可見價格範圍 ±25% 內嘅值先參與 fit（照樣畫、唔改偵測，只係唔撐軸；平移返去嗰年代自然見到）；
      貼價指標 BOLL/ATR/MACD 唔入閘
      驗證：e2e Part 11（臨時 def 砌恒定價位區塊：開閘 → Y 軸逐個位唔變 + 方塊照樣畫；關閘 → 確實撐大；+10% 內仍然 fit；移除後還原）
      + test_ict_suite [S]（_fit_vals 邊界 / FIT_PAD 範圍 / FAR_OVERLAYS 恰好 = 11 個 ICT main）→ 兩套全綠 ✅
      效果：Y 軸 1.66 倍 → 1.12 倍；對比截圖 `.scratch/vob_far_off.png` vs `.scratch/vob_boxes_live.png`
9. [X] 用戶反映「OB 中途冇穿但不見了」+「同一類 OB 出現了，之前嘅 OB 是不是應該消失」→ 用 `supersede`（預設 1）一次過解決；
      **多 slot `(n, S)` 方案正式取消** — `_supersede()` 令同向必然唔重疊 → `_zones_to_arrays` 嘅覆蓋永遠行唔到，陣列模型變成準確。
      語義如實記錄：ICT 本身靠「被價格消耗」判死 OB，唔係靠新 OB 取代 → 所以做參數（0 = 保留全部 = OB map）。
      驗證：test_ict_suite [I]（flat 陣列直喂 `_ob_zones`：關 = 重疊拖到結尾 / 開 = 喺新者前一根終止、唔同方向唔影響、隨機 400 根零重疊）
      + registry 三個 def 一致 + e2e 參數欄 8 個 / 摘要 `14/1/5/5/3/50/1/15`；真 HSI 300 根：重疊 1 對 → 0，
      `空 197..299`+`空 221..299` → `194..194`/`195..218`/`219..299`。i18n +2 key（共 69）。全部 suite exit 0 ✅

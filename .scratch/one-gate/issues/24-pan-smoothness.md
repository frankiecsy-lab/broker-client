# #24 平移仲係唔順 — 拖動凍結 Y 尺度 + 平滑平移

**要求**（用戶）：「縮放是流暢了，平移還是不順。」

**量度（`.scratch/bench_kline_pan.py`，1200 根、1400×700 offscreen、模擬 125 Hz 拖動 1.25 s）**：
- 跟手滯後 = **0 根**（`_view` 同步更新，狀態跟到最新）→ 唔係 lag 問題。
- 實際幀率 **54.8 fps**、每幀 draw 中位 20.2 ms / 最差 23.3 ms → 已經係 Agg 底線（#23 量過）。
- 🤖 **真正嘅元兇：拖動期間 Y 軸逐幀重 fit** — 垂直尺度每 25 個 event 變 0.933× / 0.952× / 1.047×。
  橫移 + 同時垂直缩放 = 成張圖「郁郁下彈下」；縮放之所以順，係因為動畫將變化攤平喺 220 ms。
- 對照：`_apply_view` 行咗 137 次但只上屏 75 次 → 每個 mouse event 都重算一次 limits/Y/ticks，一半係白做。

1. [X] A：拖動凍結 Y — `_on_press` snapshot `(ax.get_ylim(), axv.get_ylim())` → `_apply_view` 用返鎖定值；
      松手（冇滑行）即刻 refit 一次；有滑行就喺動畫完結先 refit；`_on_scroll` / `set_bars` / `clear` 一律解除鎖定（縮放=明確要重縮放）
2. [X] B：平滑平移 — `_on_motion` 唔再 1:1 直接改 `_view`，改設 `_pan_target`；`_pan_tick()` 每幀指數追近（`PAN_EASE=0.55`，
      ~2 幀收歛 ≈ 30 ms → 感覺唔到滯後，但每幀位移細 = 滑動而唔係跳格）；順帶唔再逐 event 行 `_apply_view`（137→75）
      🤖 用「逐幀比例」而唔係時鐘插值：拖動係連續 input，時鐘插值會越拖越落後。
      未追完之前任何要讀取「而家視窗」嘅動作（松手/縮放/再起 drag/過渡）一律先 `_flush_pan()` 貼實。
3. [X] C：手勢進行中（`_anim` / `_pan_target`）一律唔碰 out-of-range guard（#23 同一個坑，平移都會撞右緣 gutter）；
      guard 命中都由「丟返跟隨模式」改做 `_clamp_view()` — **保留用戶嘅縮放級別**（舊做法會突然將用戶拉返最新 + 換尺度，#24 測試暴露）
4. [X] D：測試（e2e Part 14，11 項）+ `.scratch/bench_kline_pan.py` 前後。
      **前後對照**：`_apply_view` 137 → **75**（= 上屏次數，零白做）、拖動期間 Y 範圍 **1.000× 恒定**、幀率 54.8 → **56.0 fps**；
      成本 = 穩態跟手落後中位 **3.3 根** / 最差 **5.3 根**（~60 ms；`PAN_EASE` 係調校掣）。
      🤖 **測試逼出/修正**：① 幀路徑（`_pan_tick` / `_anim_tick` / `_unlock_y`）直接行 `_apply_view()` 會繞過子类 `_frame()` hook → 平移／動畫期間指標疊加唔跟住重畫；一律經 `_frame()`。
      ② 假時鐘必須用返程式碼同一個 clock：測試用 `time.monotonic()` 砌進度、程式碼用 `perf_counter()` → 兩個 clock 差 ~26 ms，「縮到全部數據」嗰項一直 flaky。
      ③ 位移閾值要喺距離大嗰邊量（xmin 軸 = 成幾百根）；喺 xmax 量得幾個位一定 flaky。
      全部套件 exit 0：`e2e_gui_indicators` / `test_ict_suite` / `e2e_gui_quotes` / `e2e_gui_home` / `e2e_gui_fulltest` / `cli_indicators_live`（真數據）/ `diag_vob_zones` ✅
5. [X] E：README（gui_kline / e2e 兩行）+ CHANGELOG 同步

🤖 **唔做**：換 renderer（真 60fps+ 先需要）；超過 ~1000 根嘅抽稀。
⏳ **待用戶手感確認**：`python gateway/pages/gui_kline.py` 拖一拖。若覺得「跟唔切手」→ 調大 `PAN_EASE`（0.7 更跟手、平滑少啲）；若覺得仲係跳格 → 調細（0.4）。

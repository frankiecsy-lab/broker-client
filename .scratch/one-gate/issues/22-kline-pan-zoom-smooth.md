# #22 K 線圖縮放／平移唔順 — 繪畫層重構（A+B+C）

**要求**（用戶）：「K 線圖嘅縮放同平移都非常唔順，咩原因？有解決方法嗎？」→ 拍板「ABC 一齊做」。

**基線實測**（`.scratch/bench_kline_redraw.py`，1200 根合成，offscreen，同步 `canvas.draw()`）：
300 根可見 = 砌 artist 179 ms / 一幀 **241 ms**；1000 根 = **765 ms**；郁滑鼠 = **181 ms/次**；
對照「只改 `set_xlim` + draw」= 47 ms → bottleneck = **每幀重建 artist**（`ax.clear()` + `ax.bar` → 每根 bar 一個 Rectangle，300 根 = 600 patches）。
指標疊加唔係主因（4 個 ICT 只多 ~35 ms）。

1. [X] A：兩層繪畫 — `set_bars` 一次轉全長度 numpy 陣列；`_rebuild_static()` 全部 bar 一次過畫成 **3 個 collection**
      （影線 `LineCollection` 逐段上色、實體/成交量 `PolyCollection` 逐 bar facecolor）+ 預建現價線/tag/crosshair；
      `_apply_view()` = 視窗層（xlim + 由可見 slice 重算 Y fit + ticks + tag 位置）；x 一律**絕對 index**（`_s` 恒 0 → 子类 slice 契約照樣啱）。
      🤖 配色砌進 collection 就固定咗 → 加 theme 簽名檢查（改 `gk.C_*` + `_redraw()` 嘅 recipe 唔破）
2. [X] B：hover 唔再 full redraw — 預建 crosshair `set_xdata` + readout + `draw_idle()`；指標 panel 同樣預建（`_panel_cross`）一齊跟
3. [X] C：`_request_redraw()` — 狀態 + limits 即時更新（手勢數學跟到最新、測試照舊同步），上屏 pending flag + `QTimer.singleShot(16ms)` 合併到 ~60fps
4. [X] `IndicatorKlineChart`：重寫 `_frame` hook（而唔係 `_redraw`）先至食到手勢節流；`_ind_artists` 追蹤 + 逐個 `remove()`（parent 唔再 `ax.clear()`）；`_build_axes()` 後標記 `_static_dirty`
5. [X] 測試 + benchmark 前後對照 → **一幀 241 → 28.7 ms（300 根）、765 → 28.9 ms（1000 根）、hover 181 → 0 ms**；
      開 4 個 ICT 疊加 775 → 64 ms。已等同「只改 xlim」嘅純渲染底線，而且同根數近乎無關（總 300 根 21 ms / 3000 根 38 ms = axes chrome 固定開支）
      驗證：e2e 新增 Part 12（pan+zoom 後靜態層 artist 逐個同一個物件 + `_static_dirty` False + Y fit 跟住可見 slice；
      指標 artist 全部喺新可見窗內 + 連續 6 幀數量恒定；hover 零 `_redraw` + 主圖/panel crosshair 一齊跟 + 離開收埋；
      theme 換色自動重建；`set_bars` 換數據 → 重砌且畫晒全部 200 根）；Part 11 `collections > 0` 收緊為**增量 == 1**
      🤖 教訓：砌 panel 會 `fig.clear()` 重建 axes → 測試用嘅 fake event 要重新取 `ch.ax` 先至過得到 guard
      全 suite exit 0：`test_ict_suite` / `e2e_gui_indicators` / `e2e_gui_quotes` / `e2e_gui_fulltest` / `e2e_gui_home` /
      `cli_indicators_live`（真數據）/ `diag_vob_zones`（#21 Y-fit 閘 1.66→1.12 冇變）
6. [X] README（gui_kline / indicators / e2e 三條 FILE_MAP）+ CHANGELOG 同步

🤖 **未做（唔值得）**：超過 ~1000 根嘅抽稀/聚合 — 實測 3000 根先 38 ms，`MAX_VIEW` 上限照舊。

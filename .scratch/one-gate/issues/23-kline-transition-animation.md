# #23 K 線圖過渡動畫（eased zoom/reset + 慣性平移）+ 每幀成本再削

**要求**（用戶）：「是快了，但有方法可以過渡更順暢嗎？偵率（幀率）高點流暢，類似動畫效果」。

**量度（`.scratch/bench_kline_redraw.py` + 一次性 profile，1400×700 offscreen）**：
一幀 31 ms = 蠟燭/成交量幾何 **8 ms** + axes chrome（grid/tick/文字）**23 ms** + figure 背景 1.2 ms；
同色合併 compound path（3600 path → 6）= −4 ms；y ticks 9→4 = −3 ms；x ticks 7→4 = −3.5 ms；y 標籤空白 = −4.7 ms。
🤖 **結論（如實）**：matplotlib Agg 喺呢個尺寸有 ~20-25 ms 底線（≈40-50 fps），**淨係優化繪畫去唔到 60fps**；
要真 60fps 要換 renderer（QPainter / OpenGL / mplcairo）= 大工程，唔做。
→ 所以走「動畫感」：eased 過渡 + 慣性，令每幀位移細、有加減速。

1. [X] A：`_animate_to(target)` + `_anim_tick()` — ease-out cubic、`ANIM_MS=220ms`、16ms tick；`_on_press` / `set_bars` 可取消
2. [X] B：滾輪縮放 → 計算 target 再 animate（連續滾輪由「目前動畫位置」接續 → 唔會跳）；右鍵 / 雙擊復位 → animate 返跟隨，終止先 `_view=None`
3. [X] C：拖動松手 → 慣性滑行（取最近 ≤120 ms 嘅游標速度，滑行量 = v×0.2，clamp 喺數據範圍內；太慢就唔滑）
4. [X] D：每幀成本再削 — 同色（漲/跌）合併成**一個 compound Path**（`_rect_path`/`_segs_path` → 3 個 `PathCollection`）；
      指標區塊 `_zone_rects` 同樣合併（47 個 collection → 1）；`MaxNLocator(6)` 主圖 y 刻度 + x 時間標籤 7 → 5（`X_TICKS`，子类跟返）
5. [X] E：e2e Part 13（6 項）+ benchmark 前後。一幀：純 K 線 28.7 → **18.9-20.4 ms**（≈50 fps）、4 個 ICT 疊加 64 → **21.5-42.1 ms**、
      hover 照舊 0 ms；對照「只改 xlim」= 17.1-19.2 ms → 已經貼住 renderer 底線。
      🤖 **測試逼出三個真 bug（全部已修）**：
      ① 出界嘅 x 時間刻度會**撐開 axes limits**（實測 ±1 根）→ 過渡終點永遠收唔到 target：新增 `_view_ticks()` 統一 clamp 喺 limits 之內（`_apply_view` 同子类標籤遷移都用）；
      ② `_apply_view` 個 out-of-range guard 逐幀將 `_view` 打返 `None`（跟隨模式起點 xmax > n−0.5 = tag gutter）→ 縮到「全部數據」成段動畫等於冇郁：動畫進行中跳過 guard（target 本身已 clamp 喺当前 n）；
      ③ **Windows 嘅 `time.monotonic()` 實測只有 ~16 ms 分辨率**（`perf_counter` 先至有 ~30 µs）→ 拖動樣本 dt 恒為 0，慣性永遠唔會觸發：手勢/動畫計時一律改 `time.perf_counter()`。
      🤖 測試手法：呢個 e2e 進程好重（一次 `pump()` ≈ 200 ms 實時）→ 插值契約用「假時鐘」直接 drive `_anim_tick`，唔好靠真 QTimer 取樣。
      ⚠️ **本次引入嘅 regression（用戶抓到「OB 變三角形了，不是方形」）**：compound Path 4 頂點配 `[M,L,L,CLOSE]` → `CLOSEPOLY` 唔用自己嗰個頂點 = **三角形**（實測面積 5050/10000 px）。
      蠟燭實體／成交量／ICT 區塊全部中招。修：一律 5 點（尾點 = 起點）+ `[M,L,L,L,CLOSE]`（`_rect_path` + `_zone_rects`）。
      🤖 教訓：合併 path 淨係數 artist 數 / 頂點數唔夠，要斷言幾何本身（sub-path 5 點、尾點 == 起點、4 個唔同角）。benchmark 冇變。
6. [X] F：README / CHANGELOG 同步（連同「60fps 要換 renderer」呢個事實）

🤖 **未做（唔值得）**：超過 ~1000 根嘅抽稀/聚合 — 實測 3000 根先 38 ms，`MAX_VIEW` 上限照舊。

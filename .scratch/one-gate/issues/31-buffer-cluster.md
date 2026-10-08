# 31 — BUFFER 語義更正：淡化「對上一個訊號 ≤N 條 bar」嘅後續 B/S（cluster）

需求（用戶，screenshot）：「這個B S B..在BUFFER 之內..，後的SB 應該要變淺色」。#29 原文係「10條BAR內**出現買賣訊號**…顏要淺一點」= 訊號之間相隔 N 條，唔係對最新 bar 計 N 條。

診斷：舊實裝 `i >= len(rows) - buf`；真實 app 每格 ~1000 bars → 淡化區喺全資料末端，zoom 中間段永遠實色（用戶兩度反映嘅根因）。

1. [X] indicators `_draw_marks`：升冪掃 marks，對上一個 ≤buf → 後續淡化、首個實色；0 = 全實色（strategies/page 註釋跟改）
2. [X] 測試：test_fade_scale 重寫（Part A 逐個 alpha=規則 + Part B 合成 marks 兩方向）；e2e_gui_quotes 斷言改 @4/@7 淡、@2 實；全部回歸綠
3. [X] README / CHANGELOG 同步

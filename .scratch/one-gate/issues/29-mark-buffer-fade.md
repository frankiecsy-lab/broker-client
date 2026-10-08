# 29 — 買賣訊號 BUFFER：最近 N 條 BAR 嘅 B/S 淡化（N 喺策略可調）

需求（用戶）：「買賣訊號要加一個BUFFER 比如買賣訊號設定10條BAR內出現買賣訊號，將買賣訊號的顏要淺一點，10條BAR..这數字可以在策略里調整」。

設計：
- 策略 entry 新欄 `mark_buffer`（int，預設 10，clamp 0–200；0 = 全部實色）。儲存跟 strategies section。
- 繪畫端 `_draw_marks`：mark bar index `i >= len(rows) - buf` → Text + bbox 一齊 `set_alpha(0.45)`（淡化 = 淺色，任何 theme 安全）。只影響繪畫，marks cache 契約唔動；串流加 bar → 舊標記自動變實色（data_seq 已喺 cache key）。
- 策略頁表單加 QSpinBox（0–200）；`add/update` 加可選參數；i18n 三語。

1. [X] strategies.py：mark_buffer 欄（_sanitize 容忍 + clamp + add/update 參數）
2. [X] strategies_page：buffer spinbox（表單/回填/清空/collect/retranslate）+ i18n
3. [X] indicators.py：_draw_marks 淡化（alpha 隨 Text+bbox）
4. [X] 測試：test_strategy_conditions（clamp/roundtrip）+ e2e_gui_strategies（spinbox 流程）+ e2e_gui_quotes（buffer=3 → @7 淡、@2/@4 實）
5. [X] README / CHANGELOG 同步

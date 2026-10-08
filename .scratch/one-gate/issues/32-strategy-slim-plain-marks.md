# 32 — 策略刪標的/生效期 + BUFFER 訊號改純文字（B紅字/S綠字、冇圓形底色）

需求（用戶）：「策略內容刪除標的和生效期」「BUFFER 來的買賣SIGNAL 由淺色改為背景透明純文字..B紅色 S綠字 取消背景圓形及底色」。

設計：
- entry 模型刪 `code` / `validity`（連同 `created` 狀態鏈 `is_active`／已過期一併走 — 冇生效期即冇狀態欄）；#30 已全格套用，標的欄本來冇作用。容忍 load：舊檔多嘅欄直接忽略（零遷移）。
- 策略頁：表單刪標的輸入 + 生效期 combo；表刪 標的/生效期/狀態 三欄；i18n 相鍵清理。
- 行情頁：策略下拉 label 由「name · code」改「name」。
- `_draw_marks`：BUFFER 內（對上一個 ≤N 條）→ 冇 bbox、純文字 B=C_UP 紅 / S=C_DOWN 綠；組外照舊圓形徽章白字。MARK_FADE_ALPHA 退役。

1. [X] strategies.py：entry/add/update/_sanitize 刪 code/validity；is_active/VALIDITIES 移除
2. [X] strategies_page：表單/表欄/摘要/狀態鏈刪；quotes_page combo label；i18n 相鍵清理
3. [X] indicators `_draw_marks` 淡化 = 純文字紅/綠；MARK_FADE_ALPHA 移除
4. [X] 測試：e2e_gui_strategies / e2e_gui_quotes / test_strategy_conditions / test_fade_scale 跟改，全套回歸
5. [X] README / CHANGELOG 同步

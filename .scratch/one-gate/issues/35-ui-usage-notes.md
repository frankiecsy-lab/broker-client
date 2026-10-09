# 35 — 介面使用說明備注（三語、書面語）+ 既有中文轉書面語

需求（用戶原話）：
- 「所有界面尽可能加上使用方式的備注，要用書面語和三語」
- 「將所有中文轉做書面語，簡潔專業，不要廣東話」

既定事實（已核對）：
- 三語 = `zh_hk` / `zh_cn` / `en`；`gateway/i18n.py` 載入時 fail-fast 檢查三語齊全 → 每個新 key 必須三語一齊寫。
- 現成 pattern：`.ui` 宣告 QLabel（objectName `*_note` / `*_hint`）→ 頁面 `_STAMP` 標 `{'og': 'qtnote'}` → `_TEXT` 綁 i18n key → `apply_text()` 套用。量化頁已有 5 個（`qnt_hint` / `qnt_risk_zero_hint` / `qnt_confirm_hint` / `qnt_pos_note` / `qnt_orders_note`）。
- 頁級說明已有先例：`connection_page` / `futu_trade_page` 有 `body_lbl` + `role="pagebody"`（`gateway/theme.py` 已 style 淡色 14px）。其餘 10 頁冇。
- 各頁嘅 `qtnote` 樣式喺自己檔嘅 QSS template 入面逐個定義 → 應提升到 `gateway/theme.py` 共用。

## Checkpoints

1. [X] T1 共用樣式：`gateway/theme.py` 加 `QLabel[role="usagehint"]`（淡色、小字）；
       各頁 `_STAMP` 改用 `{'role': 'usagehint'}`，刪走逐頁重複嘅 QSS
       （quant `og="qtnote"` / indicators `og="indnote"` / backtest `QLabel#bt_note,#bt_chart_hint`）
       — ✅ `.scratch/t_usage_hint_style.py` 17 項 PASS（含「頁級 QSS 之下仍生效」層疊實測）
       ⚠️ 教訓：`fulltest_page.py:53` 有 bare `QLabel { color: $text; }` → 嗰頁加 note 时要留意層疊
2. [X] T2 量化頁 + 回測頁：頁級使用說明 + 補齊缺嘅分區備注（半自動流程、持倉模式、
       風控 0 值語義、訊號基準線、未收根過濾、被忽略訊號）
       — ✅ e2e_gui_quant 132 / e2e_gui_backtest 105 全綠（各 +3/+4 斷言：三語齊全、
         已套用文案且帶 role、切 EN/zh_cn 照跟語言）；新增 7 條 i18n key（三語、書面語）
       ⚠️ 教訓：`.ui` 新增 QLabel → quant E2E 有「objectName 冇漏網」守門，要同步補 NEEDED；
         `bt_chart_hint` 對應嘅 key 係 `bt_chart_empty`（objectName ≠ key，斷言要照實際映射）
3. [X] T3 行情頁 / K 線頁 / 指標頁 / 策略頁：同上
       — ✅ e2e_gui_quotes 83 / e2e_gui_strategies 45 / e2e_gui_indicators 134 全綠
         （各 +3~4 斷言：三語齊全、已套用文案且帶 role、切 EN/zh_cn 照跟語言）；
         新增 6 條 i18n key（4 頁級 pagebody + 2 區塊級 usagehint）；
         `t_usage_hint_style.py` 17 → 43 項（覆蓋 6 頁 role 標記 + 兩級 role 層疊實測）
       🐞 揪出真 bug：K 線頁頁級 QSS 有裸 `QWidget { color: $text }`（gui_kline），
         Qt 層疊下近處規則勝過 app 級 → `role="pagebody"` 解析成 #E6E6E6（備注變正文色）。
         修法：`theme.note_qss(name)` 暴露唯一定義片段，設了頁級 QSS 嘅頁自行 append
4. [X] T4 期貨交易頁 / 連接頁 / 收藏頁 / 標的列表頁 / 全測試頁 / 首頁：同上
       — ✅ 新增 4 條頁級 key（三語、書面語）：`fav_page_note` / `sl_page_note` /
         `ft_page_note` / `home_page_note`（`strings` 626 → 630）；
         收藏頁 / 標的列表頁 / 首頁 / 全測試頁 `.ui` 加頁級 QLabel + `_STAMP` role + `_TEXT`；
         `fulltest_page.retranslate()` 由 no-op 改為套用頁級說明（嵌入的 gui_fulltest 無語言概念）
       — ✅ 驗證：`t_usage_hint_style.py` 43 → 144 項 PASS（新增第 4 節：`_TEXT` 漏接即為空白、
         三語非空；`bt_chart_hint` 屬 code 覆寫 → 以 `CODE_TEXT` 顯式標明）；
         e2e_gui_favorites / e2e_gui_symbol_list / e2e_gui_home / e2e_gui_fulltest /
         e2e_gui_connection / e2e_gui_futu_trade 全部 exit 0（各 +3~4 斷言：三語齊全、
         已套用文案且帶 role、切 EN/zh_cn 照跟語言）；t_i18n_json 13 項、e2e_gui_shell 零回歸
       🐞 修正 T1/T4 的推測：只有 `fulltest_page` 的頁級 QSS 含裸 `QLabel` 規則需要 append
         `note_qss()`；`connection_page` / `futu_trade_page` / `favorites_page` /
         `symbol_list_page` / `home_page` 的頁級 QSS 全部以 objectName／property 限定，
         app 級 role 規則不會被蓋走 — 由守門第 3 節逐頁實測，而非憑推測加程式碼
5. [X] T5 i18n 現有 `zh_hk` / `zh_cn` 文案轉書面語（口語殘留：呢度／唔准／唔係／先會／邊個…）
       — 工具化：`.scratch/t_scan_colloquial.py`（只讀掃描：高／低置信粵語殘留 + `zh_cn` 繁體殘留）
         → `.scratch/_patch_t5_*.json`（逐 key 覆寫，保留 `\n` 與 `{n}`/`{code}` 佔位符）
         → `.scratch/t_apply_i18n.py`（保持「一行一 key」排版，改完重讀守門：可解析、三語非空、
         `zh_cn` 無繁體字形）
       — ✅ 分四批改寫 89 個 key（bt 15 / qt 17 / ind 36 / rest 21）；殘留 106 → **0**
         （高置信 0、低置信 0、`zh_cn` 繁體殘留 0），`strings` 仍 630 key、排版未變
       — ✅ 驗證：`t_i18n_json.py` 13 項 PASS；e2e_gui_backtest 106 / e2e_gui_quant 133 /
         e2e_gui_indicators 134 / e2e_gui_strategies 45 / e2e_gui_symbol_list 56 /
         e2e_gui_favorites 40 / e2e_gui_home 25 / e2e_gui_connection 37 / e2e_gui_futu_trade 87
         全部 exit 0
       — 同步更新的斷言（僅兩處硬編碼舊文案）：`e2e_gui_strategies.py:152`（`str_empty`）、
         `e2e_gui_favorites.py:232`（`fav_no_sel`，check 名改 ASCII）
       🐞 守門揪出：`bt_warmup_hint` 的 `zh_cn` 漏了「為→为」；`型`／`藏` 兩字形繁簡同形 →
         誤報 11 條，已從字形表移除（`模型`／`收藏` 屬正常簡體）
6. [X] T6 README（核心功能 + 使用說明）/ CHANGELOG / FILE_MAP 同步 — 逐項測試出示 Log
       — ✅ README 全面轉繁體書面語（無粵語殘留，掃描 0 命中）；核心功能新增「介面使用說明
         （三語、書面語）」條目；檔案地圖 76 條逐條保留（diff 核對：missing = 0）+ 新增
         `t_usage_hint_style` / `t_scan_colloquial` / `t_apply_i18n` 三條；修正滯後事實
         （i18n 613 → 630 鍵、AGENTS.MD 地圖描述不再聲稱「對話廣東話」）
       — ✅ CHANGELOG 新增 2026-10-09 一條（兩級說明 + 一份樣式、Qt 層疊教訓、89 key 改寫、
         守門揪出的 5 個真問題、逐項 Log）；頂部說明行改書面語並註明回溯條目不重寫
       — ℹ️ 無獨立 `FILE_MAP.md`：檔案地圖即 README `## 🗺️ 檔案地圖`，故 T6 = README + CHANGELOG

（T7 另列：程式碼註釋／Docstring／Log 轉書面語 — 屬大規模機械式掃描，待 T1–T6 完成後另開 ticket 分批做。）

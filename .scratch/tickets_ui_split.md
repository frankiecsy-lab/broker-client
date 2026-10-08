# UI 分離（.ui + QUiLoader）— Ticket Pipeline

契約（詳見 memory `ui-separation-required`）：
- `.ui` 放 `gateway/ui/`，執行期 `QUiLoader` 載入，**冇 codegen**。
- Designer 會 drop 自訂 dynamic property → 所以 `og` / `role` 一律 load 後由 **stamp registry** 注入（每頁一張 `{objectName: {prop: value}}` 表）。
- QSS 契約唔改：`theme.py` / 各頁 `_QSS_TPL` 嘅 `[og=...]` `[role=...]` selector 照用。
- 自繪 widget 用 Designer promote + QUiLoader `createWidget` factory。
- 現有 objectName 唔改（E2E + QSS `#objectName` selector 靠佢）。
- signal 接駁一律留喺 code（`.ui` 嘅 `<connections>` 留空）；page `__init__` 順序：
  `apply_ui` → `stamp`（連 `WA_StyledBackground` 一併補）→ 建表/填 slot → `_connect_signals` →
  `_apply_theme_qss` → `_retranslate_widgets()` → `_refresh()`。
- QUiLoader 會將子 widget 掛做 root attribute（`self.futu_host` / `self.navSlot` 直接可用），唔使 findChild。
- `.ui` 入面嘅文字只係俾 Designer 睇；`retranslate()` 先係文字來源（app shell 開動時一定 call）。
- **重複控件嘅「數量」屬資料、「排版」屬 UI**：nav / lang / 參數列呢類由 registry 生成嘅控件，喺 `.ui` 留空
  layout slot（`navSlot` / `paramSlot`…），Python 只往 slot 填 → 加頁 / 加參數唔使改 `.ui`。
- root 認「第一個 `parent is None` 嘅 createWidget」，唔好靠 class name（Designer 對 QMainWindow form 會寫 `<widget class="QMainWindow">`）。

## 狀態：15 / 15 全部完成 ✅
（逐 ticket 細節與踩過嘅坑已滾動清理 → 見 CHANGELOG `2026-10-09`；以下淨返一行路線圖）

- **Phase 0** 1–3 ✅ 基礎設施：`gateway/ui/loader.py`（`apply_ui` / `load_ui` / `register_custom`）+ `gateway/ui/bind.py`（`stamp` / `apply_text`）→ 煙霧測試 `.scratch/t_ui_infra.py`
- **Phase 1** 4–6 ✅ 試點：`connection_page` → `.ui` + `_STAMP`/`_TEXT`/`_PH` 三張表 → `e2e_gui_connection.py`
- **Phase 2** 7–10 ✅ 外殼 + 簡單頁：`app_shell.ui`（`navSlot`/`menuSlot`/`langSlot`/`page_stack` 由 registry 填）+ `home` / `favorites` / `fulltest` / `indicators` / `strategies` / `symbol_list` → 各頁 E2E（含 `e2e_gui_shell.py`）
- **Phase 3** 11–13 ✅ 重頁：`kline_page`（0 margin 外殼 + `embeddedSlot`；`gui_kline.py` 屬零改動 → `takeCentralWidget()`）、`quotes_page` + `chart_cell.ui`（首次喺真頁 promote `IndicatorKlineChart`）、`futu_trade_page`（三尺寸 × 45 widget geometry 等價實證）
- **Phase 4** 14–15 ✅ 收尾：命令式建檔清零（得返 `gui_kline.py` / `gui_fulltest.py`）、retranslate 全部入 `_TEXT`、新增 `strategy_rule_row.ui` / `standalone_window.ui` / `popup_page.ui`；README（核心功能 + 檔案地圖）與 CHANGELOG 已同步。⚠️ repo 內冇 `FILE_MAP.md`（git 歷史都冇）→ 檔案地圖一直喺 README § 🗺️ 檔案地圖，照嗰度同步。

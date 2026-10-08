# 13 — MINOR CHANGE：nav 子選單（測試/設定）+ 語言三按鈕

用戶：右上語言唔准 dropdown，用三個 BUTTON；K綫測試/全功能測試/標的列表 收埋「測試」子選單，連綫測試 收埋「設定」子選單。

1. [X] i18n：`menu_test`/`menu_settings` 三語 + `LANG_SHORT`（繁體/简体/EN，endonym 唔跟 UI 變）
2. [X] app.py：NAV_DIRECT=(quotes,futu_trade) + NAV_MENUS（setMenu 左鍵開單、項 trigger→_select_page、右鍵 context「彈出 · 頁」逐頁保留彈窗功能）；lang_btns exclusive QButtonGroup（checked=當前語言）；_sync_nav_checked 分組高亮 + action 打勾
3. [X] theme.py QSS：langbtn（checked=accent）+ QMenu 三 theme 配色
4. [X] smoke 全綠（分組/選單 trigger/三語按鈕）+ e2e_gui_futu_trade Part 5.5 重寫斷言全綠（exit 0）
5. [X] README / CHANGELOG 同步

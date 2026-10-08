# 14 — 標的收藏管理頁（主菜單 + 本地 JSON + 市場/種類 FILTER）

用戶：主菜單加「標的收藏」— 本地 JSON 儲存，顯示收藏標的（全種類：指數/股票/期貨…），有市場/種類 FILTER，可新增刪除管理，為未來功能預留。

設計：storage = `gateway/favorites.py`（包 state_store section 'favorites' — 統一 JSON 檔，其他功能以後直接讀）；entry = `{code, market, type, added}`（name 唔存 — 顯示時跟語言從本地 index 解析，index 冇就如實 '—'）。頁 = `gateway/pages/favorites_page.py`（表格 + 兩組 FILTER 照標的列表 recipe + 新增輸入（QCompleter 本地 index）+ 刪除所選）。nav = NAV_DIRECT 直按按鈕。

1. [X] `gateway/favorites.py`：load/add/remove（dedupe、code 大寫、market 由 prefix 兜底）
2. [X] i18n：`nav_favorites` + `fav_*` 三語
3. [X] `favorites_page.py`：表格（code/名/市場/種類/加入日）+ FILTER + 新增（completer）+ 刪除所選 + 計數/狀態 + 記憶 filter + theme/retranslate + standalone
4. [X] app.py：PAGE_KEYS/_PAGE_CLASSES/NAV_DIRECT 註冊（🤖 e2e_gui_futu_trade Part 5.5 nav 斷言要同步加 favorites）
5. [X] e2e `.scratch/e2e_gui_favorites.py`（hermetic）全綠 26 checks（🤖 PySide6 headerData 唔食 raw int orientation → 用 Qt.Horizontal）
6. [X] README / CHANGELOG 同步
7. [X] live smoke（真 index 40,585，唔打網絡）：名三語解析 ✅；🤖 抓出 store upper() 整爛期貨主連 canonical → `favorites.add` 改原樣保留（dedupe 先大細階唔敏感）+ e2e Part 9 補斷言（HK.HSImain 保留）全綠 27 checks

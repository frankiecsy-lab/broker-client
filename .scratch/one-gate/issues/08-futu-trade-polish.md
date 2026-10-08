# 08: FUTU 交易頁五項體驗增強（解鎖狀態自動更新 / 表格美化+欄名三語 / 代碼繁簡英模糊 / 價格預填最新+數量預填手數 / TIF 三語）

**What to build:** 用戶對 Page 4 提出五項：
(a) 解鎖狀態要**自動更新**，唔可以等到落單先更新 → 需要被動探測機制（無查詢 API — 用無副作用嘅 SDK 調用探測 unlock 錯誤）
(b) 顯示嘅 DF（帳戶/訂單/持倉表）要美化，**欄名要三語**
(c) 代碼模糊輸入要支援 **代碼 / 繁中 / 簡中 / 英文**；顯示嘅中文跟主 GUI 語言（zh_hk→繁、zh_cn→簡、en→英）
(d) 下單價格預設**自動跟最新價格**；數量預設**最低手數（lot_size）**
(e) TIF 選項要按繁/簡/英顯示（提交仍用 enum）

**Blocked by:** None

**Status:** done (2026-10-08)

- [X] T0 實測驗證（真 OpenD）：modify_order fake id 未解鎖回 unlock 錯誤（解鎖檢查優先、無副作用）；snapshot 回 last_price/lot_size ✅
- [X] T1 解鎖狀態自動更新：`unlock_probe` op + 選帳戶即時探測 + QTimer 10s ✅
- [X] T2 表格欄名 i18n 三語（col_* × 23）+ 數字欄右對齊 + 表頭跟 retranslate ✅
- [X] T3 搜尋 CJK 正規化繁簡通配 + `_display_name` 跟 GUI 語言顯示 ✅
- [X] T4 `quote` op → price 預填最新價 / qty 預填每手（textEdited 手動值唔覆蓋、stale guard）✅
- [X] T5 TIF combo 三語顯示、currentData 提交 enum、切換保留選中 ✅
- [X] e2e 全綠（hermetic + live）+ e2e_gui_p8 無 regression；README/CHANGELOG 同步 ✅

# 01: Fulltest 頁四項增強（隱藏 EXPECTED FAIL / broker 分區 / 串流核實 / DF 反向排序）

**What to build:** 全功能測試矩陣 GUI（gui_fulltest）一次搞掂用戶列嘅四件事：(a) 右上角加一個按鍵，一鍵切換隱藏/顯示所有 EXPECTED FAIL 行；(b) futu 同 ib 兩組測試嘅分區要視覺上更明顯（而家只係靠 broker 欄文字分辨）；(c) 核實串流 row 嘅 UPDATE 有冇真係收到 live tick — 用戶觀察到「好像不對没有串流」，要查明係 bug 定係設計上靜態結果格（5s bounded window 後先填一次），bug 就修、設計就講清楚；(d) 成功行拆開嘅數據表 DF 要按時間反向排序（最新喺上面）。

**Blocked by:** None (can start immediately)

**Status:** done (2026-10-06)

- [x] 右上角 toggle 按鍵可隱藏/顯示所有 EXPECTED FAIL 行，切換唔影響 Run All / Stop / 單行 ▶ 邏輯（display-only setRowHidden；E2E monkeypatch enqueue 驗證 Run All 仍 enqueue 全部 18）
- [x] futu block 同 ib block 有明顯視覺分隔（group header row 或分隔線），18 rows 內容與順序不變（2 條 section header row + setSpan(1,8)；table 20 rows，_test_to_row/_row_to_test mapping）
- [x] 串流 UPDATE 核實結論寫入 CHANGELOG：bug → 修復 + E2E 覆蓋；設計使然 → 講明原因（注意 [[stream-e2e-market-timing]]：0 tick 可能係無成交時段）— 結論：pipeline 無 bug，static result cell by design + market timing / IB 無 RTUS；開市 live run snapshots=2 live_updates=1
- [x] 成功行數據表按時間反向排序顯示（最新 bar 喺第一行，仍係最後 50 rows）— df.tail(50).iloc[::-1]
- [x] e2e_gui_fulltest.py 同步更新並全數 PASS EXIT=0；真機 Run All 驗證結果同 baseline 一致（PASS=10 / EXPECTED_FAIL=8 / FAIL=0）— E2E 39/39 PASS；live Run All pass=10 expected_fail=8 fail=0 (34.8s)

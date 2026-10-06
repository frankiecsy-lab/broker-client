# 05: 第三頁 連綫測試（config editor + probe）

**What to build:** One Gate 第三頁 = 連綫測試：可以編輯並保存 config.json 嘅參數（futu host/port、ib host/port、kline_num），保留 JSON 結構同其他欄位；「FUTU OpenD 連綫測試」按鍵量度連線時間 + get_global_state RTT；「IB GATEWAY 連綫測試」按鍵用獨立 clientId=98（避開 app 主 session 嘅 99，兩邊可以同時跑）量度 handshake timing + reqCurrentTime server-time RTT；結果以人話顯示狀態同速度。

**Blocked by:** #02 One Gate 骨架（外殼 + i18n + theme）

**Status:** ready-for-agent

- [ ] config editor 載入 modules/config.json 現值，save 保留結構 — source / ib.symbol_aliases 等其他欄位原封不動
- [ ] Futu probe 行 QThread 唔 block UI：回報連線時間 + RTT，失敗時如實講原因（port 冇開 / timeout），唔 fake success
- [ ] IB probe 用 clientId=98（絕非 99）：handshake timing + server-time RTT；與 app 主 session 同時在線唔衝突
- [ ] `if __name__ == '__main__':` 單獨開視窗可用
- [ ] E2E：config save round-trip（改值 → save → reload → restore 原檔）

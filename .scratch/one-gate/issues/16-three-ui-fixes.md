# 16 — 三項 UI 修正：標的名三語 / 全功能測試按鈕+默認隱藏 / FUTU 交易自動連線

用戶：① 標的列表名稱要繁中/簡中/英文自動切換；② 全功能測試默認隱藏 EXPECTED FAIL，RUN/STOP/RUN ALL 未開始要灰；③ FUTU 交易 OpenD 默認自動連線、取消連線/斷開按鈕，下單價格數量不變。

探測：🤖 真 index 實測 zh_cn 顯示咗繁體 — 根因 = `display_for` 只識 'zh'/'en'，symbol_list/favorites 把 zh_cn collapse 做 'zh'（s2t 轉繁體）。futu_trade 自己已三語正確。全功能測試：run_all/stop 喺 worker ready 後永遠 enabled；hide_ef 默認 off。futu_trade：connect/disconnect 按鈕 + 手動連線；e2e 用 `_on_connect()` 直接 call（唔靠按鈕）→ 可安全移除按鈕。

1. [ ] `symbol_search.display_for` 直接食 'zh_hk'/'zh_cn'/'en'（legacy 'zh'=zh_hk）；symbol_list_page / favorites_page 傳 self._lang（删 collapse）；e2e 補三語斷言
2. [ ] gui_fulltest：hide_ef 默認 checked（連文字反轉）；按鈕狀態機 — idle: RunAll 亮 / Stop 灰；運行中: RunAll 灰 / Stop 亮；e2e_gui_fulltest 同步
3. [ ] futu_trade_page：刪 connect/disconnect 按鈕 + retranslate 引用；構造後自動連線（失敗 15s 自動重試，如實 status）；e2e 改（自動連線斷言 + factory 注入時機 + retry timer 控制）
4. [ ] 全部受影響 e2e 重跑全綠（symbol_list / favorites / fulltest / futu_trade）+ live 驗證三語名
5. [ ] README / CHANGELOG 同步

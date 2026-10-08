# 11 — 標的列表：期權二級下鑽 + 窩輪三級下鑽

探測結論：HK 窩輪 stock_child_type=CALL 6208/PUT 1208/BULL 3855/BEAR 3651、stock_owner 100%（334 個標的）；US 窩輪無 owner/分類。期權鏈可即時攞：get_option_expiration_date（到期日喺 strike_time 欄）→ get_option_chain 每次 ≤30 日跨度（逐到期日 call）。

1. [X] `symbol_search.py`：WARRANT entry 加 `owner`/`wtype`/`expiry`/`strike` 欄；`fetch_option_chain(code, progress_cb)`（短連線、逐到期日、失敗如實）；`to_simplified()` helper（🤖 發現 s2t「汇丰」→「滙豐」港式，兩邊 t2s 先 match 到）
2. [X] `symbol_list_page.py`：mode 狀態機（flat/w1/w2/w3/o1/o2）+ generic model（SCHEMAS 隨 mode）+ 面包屑/返回 + US 偽行直接落 L3 + 鏈 QThread/cache（修咗：❌ status 要喺 _refresh 之後 set，同 #10 同一教訓；QTableView 冇 cellClicked → clicked(QModelIndex)）
3. [X] i18n keys（類別/行使價/到期日/面包屑/鏈進度）+ e2e 重寫全綠 43 checks（三級導航/偽行/cache/失敗如實/繁簡通配過濾/重載入 w1）
4. [X] 真 OpenD live：re-fetch 帶 owner/wtype → 三級導航 + NVDA 期權鏈
5. [X] README / CHANGELOG 同步

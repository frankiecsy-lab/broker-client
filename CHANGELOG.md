# 📈 CHANGELOG - 版本變更日誌

> **唯一事實來源**：任何功能增刪、修改或技術架構調整，必須第一時間喺呢度同步更新（見 `AGENTS.MD` §1）。格式：倒序（最新喺上面）。

---

## 2026-10-08

### MINOR：主菜單刪預留言面 + 指標管理新增 ICT 區塊指標（OB / FVG / VOB）
檔案：`gateway/app.py`、`gateway/i18n.py`、`gateway/indicators.py`、`.scratch/e2e_gui_indicators.py`、`.scratch/cli_indicators_live.py`、`.scratch/e2e_gui_futu_trade.py`

- **要求**（用戶）：主菜單刪除預留言面；指標管理增加 3 個 ICT 指標 — OB、FVG 同有效 OB = VOB。
- **預留位刪除**：`app.py` 嘅 disabled `nav_reserved` 佔位按鈕連同 i18n `nav_reserved`/`nav_reserved_tip` 一併移除（nav 一直由 `NAV_DIRECT`/`NAV_MENUS` 生成，呢個係唯一硬佔位）。
- **零改契約**：三個 ICT 指標全部 `position='main'`，計算照樣 `compute(ohlc, params) → dict[str, ndarray 全長度]` — 區塊用 `bull_top/bull_bottom/bear_top/bear_bottom` 四條**價格**陣列（區塊外 NaN）。🤖 唔准用 0/1 方向旗標：主圖 Y-fit 會 concat 晒所有陣列 finite 值，旗標會把 Y 範圍炸晒。現有 `_slice`／Y-fit／cache／開關掣列全部自動啱，`IndicatorKlineChart` 冇改一行。
- **定義**（寫落 docstring，參數全部可调）：`FVG` = 三根缺口（`low[i+2] > high[i]` → [high[i], low[i+2]]；反向同理），由確認根畫到第一次被填平；`OB` = 結構突破前最後一根反向燭（之後 `confirm` 根內收盤突破佢嘅 high/low + 位移幅度 ≥ `strength`×ATR），區塊 = OB 燭全範圍，畫到收盤穿過對面邊為止；`VOB`（有效 OB）= OB + 兩條有效性過濾（OB 燭影線掃走之前 `sweep` 根嘅流動性 + 位移窗內有同向 FVG）。`max_zones` 只畫最近 N 個保圖表可讀。
- **繪畫**：`_plot_zones` = `fill_between(where=mask)`，睇多用 `C_UP`、睇空用 `C_DOWN`（draw-time 跟 theme）；NaN 位置先換 0 再交 mask — `fill_between` 對 NaN 嘅處理唔一致，mask 先係可靠嘅斷段方式。
- **驗證**：`.scratch/e2e_gui_indicators.py` 全綠 57 checks（新增 Part 9：手砌 6 根 K 線逐條斷言區塊起訖與價格、`min_size`/`max_zones`/`strength`/`sweep` 過濾、隨機 400 根三個指標都有區塊且 top≥bottom、主圖真的多 6 個 PolyCollection 且 main 唔砌新 panel、管理頁 def combo/參數欄/三語）。live smoke `.scratch/cli_indicators_live.py` 全綠（真 HSI K_1M：OB 2 段 / FVG 4 段 / VOB 5 段 + 開關掣列 5 個；截圖 `.scratch/ict_zones_live.png`）。舊 suite futu_trade（含「冇 reserved_btn」斷言）/home/favorites/quotes/p8/symbol_list/fulltest 全部 exit 0。

### MAJOR：指標管理 — 指標計算 + 疊加 K 線主圖/副圖 + 管理頁 + 顯示開關
檔案：`gateway/indicators.py`（新）、`gateway/pages/indicators_page.py`（新）、`gateway/pages/kline_page.py`、`gateway/app.py`、`gateway/i18n.py`、`.scratch/e2e_gui_indicators.py`（新）、`.scratch/e2e_gui_futu_trade.py`

- **要求**（用戶）：主菜單新增「指標管理」（頂層直按按鈕），可新增／修改／移除主圖及副圖指標；用一個 CLASS／檔案專門負責指標計算＋疊加 K 線；K 線圖分主圖指標／副圖指標兩部分；首批主圖 = BOLL、副圖 = ATR（MACD 保留做副圖選項）；參數（計算日數）可調；有掣切換每個指標是否顯示。**零改動 `gui_kline.py`**（用戶明確要求）。
- **單一事實來源 `gateway/indicators.py`**：`INDICATOR_DEFS` registry（ParamSpec 範圍 + 准入位置；BOLL 只准 main、ATR/MACD 只准 sub）+ 純計算（numpy 全長度輸出 NaN warm-up — 無左緣失真；ATR = Wilder RMA SMA seed；MACD hist = `HIST_SCALE(2.0)×(DIF−DEA)` 富途慣例，e2e 鎖住定義）+ `IndicatorManager`（state_store section `indicators`，首次 seed BOLL+ATR；CRUD + clamp + 容忍 load（unknown def drop / 位置退回 / id 防撞）+ 上限總 6 / 副圖 4 + `config_version`/`layout_version` 分級 + listener 帶 `origin` 防 re-entrancy）。
- **子类疊加（零改動 gui_kline 嘅關鍵）**：`IndicatorKlineChart(gk.KlineChart)` — parent `_redraw()` 讀實例屬性 `self.ax/axv` → 子类 `fig.clear()` 重新砌 gridspec（ratios `[3,1]+[1]*n_sub`：主圖→volume→指標 panel，sharex）再重新綁定 + `super()._redraw()` 照畫蠟燭；Figure 物件身份保留。per-chart cache key=`(data_seq, config_version)`：hover/平移零重算、4Hz tick 先重算一次；`set_enabled` 只 bump layout_version（開關唔重算數值）。x 時間標籤遷移最底軸、主圖 Y-fit 合併 overlay 值、panel 手勢薄 override 改寫 `ev.inaxes`。
- **K線頁**：chart 實例喺 layout 同 index 替換（必須喺 `_apply_embedded_theme` 之前）+ 開關掣列插喺圖上面（`og="indtoggle"` checkable 掣，text = acronym+參數摘要語言中立，`_EXTRA_QSS_TPL` 跟 theme）；管理頁↔K線頁經 listener(origin 過濾) 雙向即時同步。
- **管理頁 `indicators_page`**：表格（啟用 checkbox / 名 / 位置 / 參數摘要）+ 新增（def/位置 combo 只准入 + SpinBox 範圍經 ParamSpec）/ 揀行編輯套用 / 移除所選。
- **驗證**：`.scratch/e2e_gui_indicators.py` 全綠 44 checks（純計算對照 pandas rolling/Wilder/ewm、Manager 全路徑、axes 增減、用戶 `_view` 開關後保持、cache 計數、panel 手勢、K線頁嵌入 + theme QSS、管理頁 CRUD、shell 註冊；全 hermetic：tmp state ×4 + FakeDir）。舊 suite quotes/p8/home/symbol_list/fulltest/favorites/futu_trade 全部 exit 0。🤖 教訓：futu_trade e2e 退出 exit=127 = 有 C++ QThread 未停（C-level exit(127)）— shell 加了 quotes 頁後其 `_LoopThread` 漏出 e2e 手工 cleanup 清單，已補；`_new_item` 要即刻 increment `_next_id`（否則 seed 與新增撞 id）。
檔案：`modules/symbol_search.py`、`gateway/symbol_input.py`（CAND_LIMIT 20→50）、`.scratch/test_symbol_search_fuzzy.py`（新）

- **要求**（用戶）：「打 NVD 或英偉都要找出 US.NVDA；700 要看到 HK.00700，打『訊』都要看到 — 任何一個字母或中文字都能找出相對應的。」
- **根因**：舊 ranking 淨計 `ql in code` → 用戶打嘅係 **ticker**（唔帶 `US.` 前綴），所以 `NVD` 食最低級 contains 分，再按 code 字母序排 → 目標俾幾千條淹沒；跳字（`NVA`）完全 match 唔到。
- **分層計分**（`_S_*` 常量 + `_score()`）：code/ticker exact 100 > **ticker prefix** 90 > code prefix 80 > name prefix 75 > ticker/code contains 70 > name contains 55 > **跳字 subsequence** 40/35。加 `_pos_bonus()`（match 越靠前加分越多，最多 +9）→ 打 `700` 係 HK.00700 唔係 HK.02700。HK 代碼 5 位零填充 → ticker 加「去零版本」一齊計（打 `700`/`0700` 都係 00700，排第一）。
- **成本**：全表 40,585 條含跳字 ≈ 45ms/次（實測）→ 經 GUI 200ms debounce，唔卡。
- **驗證**：`.scratch/test_symbol_search_fuzzy.py`（打真實 index cache，唔使 OpenD/GUI）全綠 — 用戶例子逐條斷言（`英偉`/`700`/`0700`/`0070`/`HK.007`/`nvda`/`騰`/`訊控`/`英偉達`/`HSImain`/`HSI` → rank 1；`NVD`/`訊`/`NVA` → 喺前 50 候選內）；單個字母/中文字一律有結果；CORE_TYPES 零污染。五份 GUI e2e（favorites/symbol_list/quotes/p8/futu_trade）全綠。
- **⚠️ 資料限制（非排序問題）**：OpenD `get_stock_basicinfo` 回傳 `name` 係**單語言**（實測：港股/中概/美股大市值全部中文名，`name_en` 淨返部分冷門美股）→ 打 `tencent`/`apple` 呢類英文公司名**無資料可 match**。要支持必須引入第二份數據源。

### 全域標的代碼輸入：模糊輸入集中化（`gateway/symbol_input.py`）
檔案：`gateway/symbol_input.py`（新）、`gateway/pages/{gui_kline,quotes_page,futu_trade_page,favorites_page}.py`、`.scratch/e2e_gui_{favorites,quotes,p8}.py`

- **動機**（用戶）：收藏頁代碼輸入要模糊輸入 — 記低：**全域**代碼輸入都要有，呢個功能 GLOBAL 使用、集中處理。
- **根因**：歷時砌咗**三份** completer。`favorites_page` 嗰份係壞嘅 — model 淨返 code + `MatchContains`，QCompleter 用輸入去 filter 個 model，打中文名（「騰訊」）model 入面根本冇中文 → popup 永遠空。
- **中央化**：`gateway/symbol_input.py` = 標的代碼輸入單一事實來源 — `FuzzyCompleter`（item =「CODE  名稱（跟語言）」、`filterAcceptsRow` 全放行交返 search 排序、準確 code / 冇 hit → 唔彈窗 pass-through、非 focus 唔彈窗）、`make_search(directory, types, limit)`（本地 index 標準搜尋 fn）、`attach_symbol_input(edit, search_fn, on_activate)`（debounce + activated → 欄入面淨返乾淨 CODE）、`set_hits()`（異步來源共用同一 item 格式）、`display_name()`（display_for 同一把尺，頁唔准再寫語言分支）。
- **四頁全部改經呢度**：K線頁 / 行情 6 格 / FUTU 交易（worker `stock_search` 異步照 feed `set_hits`）/ 標的收藏（`types=None` 全種類含窩輪）。刪走 `gui_kline.FuzzyCompleter`、交易頁自營 `_display_name`、收藏頁壞 completer。行情頁由「每按鍵即 search」改返統一 debounce 200ms；`retranslate` 統一改 `completer.lang`（🤖 行情頁之前 zh_cn 會被轉做繁體名）。
- **驗證**：`e2e_gui_favorites` 全綠 30 checks（新增：中文名 → 有候選 + item 連名、揀咗淨返 CODE、準確 code 清空候選）；`e2e_gui_futu_trade` 全綠（Part 5 模糊輸入全綠）；`e2e_gui_quotes` / `e2e_gui_p8` 補 debounce 等待後全綠。

### 首頁「全球市場脈搏」：全球指數卡 dashboard（新預設頁）
檔案：`gateway/pages/home_page.py`、`modules/market_pulse.py`、`gateway/app.py`、`gateway/i18n.py`、`.scratch/e2e_gui_home.py`

- **預設頁換檔**：`PAGE_KEYS[0]='home'`（行情退第二位）；nav 直按最前加「首頁」。
- **內容**（用戶：所有市場重要指數、密集、專業直觀、能量圖）：三區 13 張卡 — 港股（恒指 HK.800000/國企/恒生科技）、A股（上證/深成指/滬深300/創業板/科創50）、**美股 ETF 代理**（🤖 實測呢個 OpenD 對 US..SPX 等「暫不支援美股指數」，snapshot+K線都食唔到；JP/KS/TW/EU 代碼唔認識 → SPY/QQQ/DIA/IWM/VIXY 代理，分區 tooltip 如實講局限）。清單 = `HOME_INDICES` 一個常量，用戶改呢度就得。
- **卡面**（自畫 QPainter，唔靠 QSS）：名（三語）+ code、大字價格紅漲綠跌（跟 gui_kline C_UP/C_DOWN）、變動+%，**60 日走勢漸變面積 sparkline**、**當日區間能量條**（low→high + 最新價 marker）、footer 開/高/低/前收/成交額（zh 萬/億、en K/M/B 壓縮）。
- **數據**：`modules/market_pulse.fetch_pulse` — 短連線 batch subscribe K_DAY（唔 subscribe 直接 get_cur_kline 會拒）→ 逐個日K + snapshot；🤖 get_market_snapshot 一批有一隻不支援會拖爆成批 ret=-1 → 逐個 call、失敗只標該卡。手動刷新 + 60 秒自動刷新 + 更新時間。
- **驗證**：e2e 全綠 15 checks（hermetic fake pulse）；live 真 OpenD **13/13 張卡**全部有價 + 60 日走勢（截圖 `.scratch/home_shot.png`）；quotes/futu_trade e2e 註冊斷言同步後全綠。

### 標的收藏管理頁（Page 6）：主菜單直按 + 本地 JSON + 市場/種類 FILTER
檔案：`gateway/favorites.py`、`gateway/pages/favorites_page.py`、`gateway/app.py`、`gateway/i18n.py`、`.scratch/e2e_gui_favorites.py`

- **收藏儲存**（用戶：本地 JSON、為未來功能預備）：`gateway/favorites.py` = 唯一事實來源，包 state_store section `favorites`（統一 JSON 檔）— entry `{code, market, type, added}`，code 大寫 dedupe；**name 唔存**，顯示名永遠跟 UI 語言由本地 index 即時解析（index 冇就如實 '—'）。其他功能（watchlist / 捷徑）以後 `load_items()` 即用。
- **頁**：主菜單**直按按鈕**（NAV_DIRECT）— 表格（代碼/名稱跟語言/市場/種類/加入日，欄頭排序、行多選）+ 市場×種類 FILTER（共用標的列表頁常量 + 記憶）+ 新增（QCompleter 本地 index 模糊候選；index 有 → snapshot 市場/種類 + canonical code 還原，冇但似 code → prefix 兜底 + UNKNOWN，唔似 → ❌ 如實）+ 刪除所選。
- **驗證**：e2e 全綠 26 checks（hermetic：fake directory + tmp state — 新增四路徑 / FILTER 交集 / UNKNOWN 只喺 ALL / 刪除 / 記憶重載 / JSON 結構 / i18n）；`e2e_gui_futu_trade` Part 5.5 nav 分組斷言同步（直按 = 行情/FUTU 交易/標的收藏）全綠。🤖 PySide6 `headerData` 唔食 raw int orientation → e2e 要用 `Qt.Horizontal`。live smoke（真 index 40,585）抓出 `favorites.add` upper() 整爛期貨主連 canonical（HK.HSImain→HSIMAIN）→ 改原樣保留，dedupe 先大細階唔敏感，e2e 補 Part 9 斷言。

### One Gate 導航重組：子選單「測試/設定」+ 語言三按鈕（MINOR CHANGE）
檔案：`gateway/app.py`、`gateway/i18n.py`、`gateway/theme.py`、`.scratch/e2e_gui_futu_trade.py`

- **導航分組**（用戶要求）：`NAV_DIRECT = (quotes, futu_trade)` 維持直按按鈕；`NAV_MENUS = {'test': (kline, fulltest, symbol_list), 'settings': (connection,)}` 用 `QPushButton.setMenu`（左鍵開單）+ checkable `QAction`（objectName 仍 `nav_<page>`，trigger → `_select_page`）。當前頁喺選單內打勾、所屬組按鈕高亮（`_sync_nav_checked` 統一處理）。
- **語言改三個互斥按鈕**（用戶要求唔准 dropdown）：`lang_btns`（QButtonGroup exclusive，objectName `lang_zh_hk|zh_cn|en`），文字用 `LANG_SHORT`（繁體/简体/EN — endonym 恆定，唔跟 UI 語言變）；`lang_combo` / `lang_lbl` 移除。
- **右鍵彈窗保留**：nav 同選單按鈕 context menu 逐頁提供「彈出 · 頁」→ `_popup_page`（同一實例 reparent，行為不變）。
- **theme.py**：`QPushButton[og="langbtn"]`（checked = accent）+ `QMenu` 三 theme 配色。
- **驗證**：offscreen smoke（分組 / 選單 trigger 切頁 + 打勾 / 三語按鈕切換）+ `e2e_gui_futu_trade` Part 5.5 重寫斷言（nav 分組、語言三按鈕 exclusive、子選單項切頁、彈窗回归）全綠。

### 標的列表下鑽：窩輪三級 + 期權二級（即時期權鏈）
檔案：`gateway/pages/symbol_list_page.py`、`modules/symbol_search.py`、`gateway/i18n.py`、`.scratch/e2e_gui_symbol_list.py`

- **窩輪三級**（用戶要求）：WARRANT filter → **L1** 有窩輪嘅標的（按 `owner` 分組 + 認購/認沽/牛/熊計數）→ **L2** 該標的嘅類別行 → **L3** 該類別窩輪列表（行使價 / 到期日）。index schema 擴展：WARRANT entry 存 `owner`（stock_owner）/ `wtype`（stock_child_type）/ `expiry` / `strike` — 實測 HK 100% 有值（334 個標的）；**US 窩輪兩者皆 'N/A.'** → 一筆「美股窩輪」偽行直接落 L3 平鋪（如實，唔扮有分組）。
- **期權二級**：OPTION filter → **L1** 股票/ETF 候選（冇任何接口能枚舉「邊個有期權」— tooltip 如實講）→ 點擊 → **L2** 即時期權鏈：`symbol_search.fetch_option_chain(code)`（QThread；get_option_expiration_date 日期喺 strike_time 欄 → get_option_chain **逐到期日 call**，跨度 ≤30 日；progress 如實、session cache、失敗 ❌ 留喺 L1 唔入空頁）。
- **UI**：mode 狀態機（flat/w1/w2/w3/o1/o2）+ generic model（SCHEMAS 隨 mode 換欄）+ 面包屑 + 「◀ 返回」逐級退；下鑽頁模糊輸入 = 本地 substring（兩邊 t2s 正規化 — 🤖 實測 opencc s2t 將「汇丰」顯示做港式「滙豐」，用戶打「匯豐」要 match 到 → 新增 `to_simplified()` helper）。
- **Fix（e2e 抓出）**：① QTableView 冇 `cellClicked`（QTableWidget 先有）→ 用 `clicked(QModelIndex)`；② 鏈失敗嘅 ❌ status 又被 `_refresh → _update_counts` 蓋住 — 同 #10 同一教訓，set status 必須喺 refresh 之後。
- **驗證**：e2e 重寫全綠 43 checks（三級導航 / 偽行 / cache 唔重 fetch / 失敗如實 / 繁簡通配 / WARRANT 重載直接入 w1）；真 OpenD live：re-fetch 帶 owner/wtype、NVDA + 00700 期權鏈。

### 移除 `test/` folder（用戶指示）：app 組件入 gateway，測試腳本入 .scratch
檔案：`gateway/pages/gui_kline.py`、`gateway/pages/gui_fulltest.py`、`.scratch/e2e_*.py`、`.scratch/cli_kline.py`、`gateway/pages/{kline,fulltest,quotes}_page.py`

- `gui_kline.py` / `gui_fulltest.py` 其實係 **app 組件**（kline/fulltest/quotes 三頁 import 佢哋）→ `git mv` 入 `gateway/pages/`，import 改 `from gateway.pages import gui_kline as gk`（刪走 TEST_DIR sys.path hack；bootstrap 深度同步）。
- e2e ×5 + `cli_kline.py`（測試腳本）→ `git mv` 入 `.scratch/`（AGENTS.md：測試腳本統一喺 .SCRATCH 執行）。
- **驗證**：五個 e2e 全部 PASS（futu_trade exit 127 = futu SDK shutdown 已知 quirk，腳本註釋已記錄，斷言全綠）。

### One Gate 標的列表頁（Page 5）— 全 index 表格 + 市場/種類 FILTER + 一鍵更新（含窩輪 ~15.7k）
檔案：`gateway/pages/symbol_list_page.py`、`modules/symbol_search.py`、`gateway/app.py`、`gateway/i18n.py`、`test/e2e_gui_symbol_list.py`、`README.md`

- **功能**：導航新增「標的列表」頁 — QTableView 顯示全部標的（代碼 / 名稱跟語言 / 市場 / 種類，欄頭排序）；**FILTER 兩組 exclusive 按鈕**（市場 全部/HK/US × 種類 全部/股票/ETF/指數/期貨/期權/窩輪，選擇記憶 → state_store 'symbol_list'）；頂部模糊輸入（debounce 200ms，全種類含窩輪）；**一鍵更新** = QThread 行 `directory.fetch(US+HK 全 plan)`，progress 逐段如實回報；底部 = 市場×種類計數 + 顯示筆數 + 更新時間。
- **窩輪入 index**（用戶要求窩輪 filter；實測 HK 14,929 + US 828）：`MARKET_PLAN` 加 WARRANT + 跳 `delisting`；**`search(types=)` 預設 CORE_TYPES 排除窩輪** — 交易/行情頁 autocomplete 行為完全唔變，只有列表頁傳 `types=None` 攞全量（40,585 entries）。
- **期權老實講 0**：OpenD `get_stock_basicinfo` 唔支援期權枚舉（DRVT → interface not supported）、IB 唔可以無標的枚舉 → 期權 FILTER 按鈕恒 0 + tooltip 如實解釋；打準確期權 code 照樣 pass-through。牛熊證（BWRT）呢個 OpenD 回 0。
- **Fix（e2e 抓出）**：更新完成的 ✅ 訊息被 `_refresh → _update_counts` 嘅 status 重寫蓋住 — done 順序改成先 refresh、後 set result（下次 filter 操作先蓋返）。
- **驗證**：`test/e2e_gui_symbol_list.py` 全 hermetic 全綠；`e2e_gui_p8` 無 regression（search 簽名向後兼容）；真 OpenD live：fetch 40,585（窩輪 15,757）、`search("腾讯")` 預設 0 窩輪 / `types=None` 744 窩輪、shell 六頁構造 smoke + model 40,585 rows。

### One Gate 行情頁（Page 0，預設頁）— 多格 K 圖 grid + 11 週期按鈕 + 多路串流 + 統一本地狀態
檔案：`gateway/pages/quotes_page.py`、`gateway/state_store.py`、`gateway/app.py`、`gateway/i18n.py`、`test/e2e_gui_quotes.py`、`README.md`

- **功能**：表單最前新增「行情」頁（預設頁，nav 右鍵彈出視窗照舊可用）。右上 4 個 layout 按鈕 **1×1 / 1×2 / 2×2 / 2×3**：固定 6 個 ChartCell，切換只 hide/show + reposition — **cells 永不銷毀**，換 layout 唔中斷 stream、唔丟狀態。
- **每格**：標的欄模糊輸入（FuzzyCompleter 同 P8 共用本地 index + canonical 大細階還原）+ **11 個 checkable 週期按鈕**（futu KLType 全集 K_1M/K_3M/K_5M/K_15M/K_30M/K_60M/K_DAY/K_WEEK/K_MON/K_QUARTER/K_YEAR，文字全顯示、唔准 dropdown）+ 最新價 label（紅漲綠跌）+ KlineChart（重用 gui_kline 純 widget，零改動原檔）。
- **串流**：單 QThread + asyncio loop，每格一條 `stream_kline` 並發（futu 每條自開連線）— first yield = baseline 即刻上圖，其後 live tick 價即時更新 + 250ms throttle redraw（照 fulltest 已驗證 pattern）。
- **本地記憶**：新增 `gateway/state_store.py` 統一 JSON 狀態儲存（`gateway/ui_state.json` gitignored）— 按 section（`quotes` 首個；以後記其他嘢加 section 就得）、atomic 寫（tmp+os.replace）、missing/corrupt 容忍。所有格標的/週期 + layout 邊改邊 save、啟動 load。
- **Fix（e2e 抓出）**：① page objectName 冇 set → QSS 全部唔食；② client_factory 雙重包裝 — GridWorker 攞到嘅係 factory 函數本身當 client，`__aenter__` 炸 → 全部 stream 死；③ **token 改由 GUI 派**（每次 start/stop bump）— 之前 worker 派，換無效標的後舊 stream 遲到 baseline 會蓋住 ❌ 錯誤 label。
- **驗證**：`test/e2e_gui_quotes.py` 全 hermetic 全綠（shell 註冊 / layout / 串流 / throttle / stale 作廢 / canonical / 模糊 / 記憶重載 / 三語 / 清理）；真 OpenD live：4 路並發 baseline + 15 秒價即時跳動（騰訊 420.60 / NVDA / 恒指主連 23,959 / AAPL），`hk.hsimain` 細階正確還原。
- **Follow-up（用戶：彈出窗改大細、返多圖時 K 圖高度異變）**：根因 = QGridLayout stretch 全 0 時靠 sizeHint 分配，而 matplotlib canvas 嘅 `sizeHint()` = figure 像素尺寸（各格 stream/彈出 resize 歷史唔同 → row 高度偏）。**改**：`_apply_layout` 對可見 row/col 一律 `setRowStretch/setColumnStretch = 1`（隱藏清 0，換 layout 唔殘留）— 均分由 stretch 保證，同窗口大細 / 彈出與否完全無關。e2e Part 6.5 加斷言：2×2 縮細 700×450 / 放大 1500×950 四格 chart 高度都 ±1px 內。

### One Gate nav 右鍵「彈出視窗」— 頁面搬入獨立視窗（同一實例，關窗搬返）
檔案：`gateway/app.py`、`gateway/i18n.py`、`test/e2e_gui_futu_trade.py`、`README.md`

- **功能**：任何 nav 按鈕右鍵 → context menu「彈出視窗」（QMenu 標準行為：唔揳自動消失）→ 該頁 **同一實例** reparent 入 `_PopupPageWindow`（頂層 QWidget，objectName 沿用 oneGateRoot 食 shell QSS；theme 本身 app 級自動跟隨）。
- **設計**：唔開第二份 page 實例 — K線/交易頁有自己的 worker thread，雙實機會雙跑雙連線。關閉彈出窗 → closeEvent 搬返 stack **原 index**；`_select_page` 改用 `stack.indexOf(page)`，導航 index mapping 唔會爛。
- **Fix 1（用戶實測「彈出窗空白」）**：Qt 嘅 reparent 會將 widget **自動隱藏** → `_PopupPageWindow` 搬入後要主動 `page.show()`。e2e 加 `kline.isVisible()` 斷言。
- **Fix 2（用戶：左鍵應該正常切換，得右鍵先彈出）**：`_select_page` 而家若該頁正在彈出緊 → 先 `_return_page` 搬返入 shell 再正常切換（之前係喚返彈出窗 — 語義唔啱）。e2e 斷言：left-click → 窗關閉 + shell 顯示該頁。
- **i18n**：`nav_popup` × 三語。
- **e2e（Part 5.5）**：context-menu policy / 彈出窗建立 / reparent 出 stack（同一實例）/ 再撳 nav 唔多開 / 關窗搬返原 index / 搬返後可以再揀返呢頁。
- **驗證**：e2e 全綠（hermetic + live）+ p8 無 regression。

### FUTU 交易頁 dropdown 連中英文名 + 期貨主連行情修復（canonical 大細階）
檔案：`gateway/pages/futu_trade_page.py`、`modules/symbol_search.py`、`test/e2e_gui_futu_trade.py`、`README.md`

- **dropdown 連名**（用戶要求）：completer model item 由純 code 改為「CODE  名稱（跟語言）」— MatchContains 連名都 match（打中文名都有候選）；`_on_code_activated` 揀咗之後淨返 CODE 入欄（QCompleter 會先插入成串 item 文字 → singleShot(0) 喺事件尾蓋返，欄入面永遠係乾淨 code）。
- **期貨主連攞唔到價根因**：`subscribe`/`place_order` 之前一律 `upper()` — `HK.HSImain` 變 `HK.HSIMAIN` → 真 OpenD 回「未知股票 HSIMAIN」（股票冇事，得期貨主連炸）。**修復**：worker `_canonical_code()` — 經 index 還原 canonical 大細階（`symbol_search` 加 public `get()` O(1) exact lookup）；撳唔到先 fallback upper()。snapshot / K_1M 訂閱 / 落單全部經呢把尺；quote 結果 guard 全部改大細階唔敏感。
- **e2e**：FakeQuoteCtx snapshot 改大細階敏感（同真 OpenD 一致）+ FakeSearchDir 加 `get()`；新增斷言：dropdown item 連名 / 揀咗淨返 CODE / HK.HSImain subscribe 保 canonical + snapshot 預填 23941/50 + price_tick 收得到。
- **驗證**：e2e 全綠（hermetic + live）+ p8 無 regression；真連線實測：打「恒指」dropdown 出「HK.HSImain  恒指期貨主連」、HSImain 預填 23977/50。
- **Follow-up（用戶：HSImain「價格更新但沒有串流」）**：實測 SDK 層 + page 層 HSImain K_1M push 都流緊（夜市 25s 約 1–3 tick，價冇郁時欄冇變化 → 錯覺冇串流）。**改**：`_op_subscribe` result 加 `subscribed` 旗、hint 如實顯示「| 串流中 ✅」（訂閱生效就照示，換 code/斷線即清）— 用戶分得清「串流中但價冇郁」同「冇串流」。e2e 加斷言。

### FUTU 交易頁市價改用 KLINE_STREAM 方式（K_1M push 全流市價，取代 10s poll）
檔案：`gateway/pages/futu_trade_page.py`、`test/e2e_gui_futu_trade.py`、`README.md`

- **改動**：完整 code → `subscribe` op = snapshot 起步預填（last_price/lot_size）+ **訂閱 K_1M push**（同 `futu_client.stream_kline` 同一 pattern：`subscribe([code],[KLType.K_1M], subscribe_push=True, session=Session.ALL)` + `CurKlineHandlerBase`），最新 bar close = 現價 → `price_tick` 即時更新 price 欄（手動改過唔覆蓋）+ hint。換 code 自動 unsubscribe 舊嘅（一次只跟一個）；`_auto_probe` 刪走 quote 10s poll。
- **實測根因（點解唔用 QUOTE subtype push）**：呢個 futu 10.x / OpenD 組合 — `QuoteHandlerBase` 冇（要 `StockQuoteHandlerBase`）、`subscribe_quote` 已廢（統一 `subscribe()`）；訂閱 QUOTE 成功（quota 計數、query_subscription 見 sub_list）但 **push 永遠唔到**（美股開市 NVDA 15 秒 0 tick），同一連線 **K_1M push 正常流**（幾十 tick）→ 市價行 K 線 push，正正係用戶要求嘅「KLINE_STREAM 方式」。
- **Fix（用戶實測「價格數量自動填寫没有了」）**：離線搜尋修好後，用戶會**先打 code 後連線** — 之前連線成功唔會補 trigger → connect 成功分支現在若 code 欄已係完整 code（含「.」）即自動 `submit('subscribe')`。真連線 repro：US.NVDA 預填 237.175/1 → push tick → 237.194 自動更新。
- **e2e**：FakeQuoteCtx 加 `subscribe/unsubscribe/set_handler` 記帳；斷言：訂閱生效（subscribed == 当前 code）、`_price_handler._emit` 注入 tick → price 自動更新、textEdited 後唔覆蓋。
- **驗證**：e2e_gui_futu_trade 全綠（hermetic + live）；e2e_gui_p8 無 regression；真連線 page-level repro 收到 price_tick。

### FUTU 交易頁代碼搜尋改用本地 symbol index（symbol_search）— 含期貨主連 + 中文名，落單驗證 MARKET.CODE
檔案：`gateway/pages/futu_trade_page.py`、`gateway/i18n.py`、`test/e2e_gui_futu_trade.py`、`README.md`

- **改動**：`_op_stock_search` 由「OpenQuoteContext.get_stock_basicinfo per-market cache」改為直接用 **`modules/symbol_search.get_directory()`**（同 P8 共用 singleton index，純本地唔打網絡）— 即用戶要求：打「HK」見到期貨主連（HK.HSImain 恒指主連）、打「NVDA」見 US.NVDA 英偉達（name_zh 欄）。
- **細節**：search limit 5000（前綴 query match 幾千股票，唔然期貨截唔到）；純市場前綴（HK/US/SH/SZ）→ FUTURE 排前（stable sort）；**IDX 過濾**（指數唔可落單）；顯示名改用 entry 三欄（`_display_name`：en→name_en、zh_cn→name_zh canonical、zh_hk→`display_for` s2t）— 刪 page 自營 opencc block（統一用 symbol_search 把尺）。
- **落單安全**：`_CODE_RE`（`^(HK|US|SH|SZ)\.[A-Z0-9]...`）喺 `_on_place` 驗證 — 用戶留低中文名（恒指主連）按買入 → `trade_invalid_code` 三語提示，唔會送入 place_order。
- **e2e**：`FakeSearchDir`（注入 `_worker._search_dir`，複製 SymbolDirectory 契約 entries+search）取代 FakeQuoteCtx basicinfo 路徑；新增斷言：HK→HSImain 排第一 / IDX 排除 / 繁體「騰訊」t2s hit / NVDA 中文名三語 hint / 搜尋唔打網絡 / 中文 code 被拒。真 index CLI 驗證 HK/US/恒指 全部啱。
- **Fix（用戶實測「還是冇反應」，e2e 綠但 GUI 冇反應嘅 gap）**：
  1. **QCompleter popup 只喺 key event 嗰刻彈** — 我哋嘅 model 係 debounce+worker 異步之後先填好，永遠冇 key event 觸發 → 結果返嚟主動 `complete()` 強制彈 popup（e2e 加 `popup().isVisible()` 斷言）。
  2. **第一次搜尋等 10s+**（worker lazy start + symbol_search import futu + load 25k index 全堆喺首鍵）→ page 建立即 `_ensure_worker()` + `warmup` op 背景預熱；搜尋都 lazy start（唔再要求連線先搜到 — 本地 index 離線都得）。真 page 端到端重現：首鍵反應 0.9s。
  3. **市場前綴 quota**：HK 有 169 隻期貨主連，全排前會淹沒股票、50 cap 又截走 HSImain（排 79）→ 改為期貨主連頭 10 + 股票 40 混合；搵特定主連打 HSI/恒指 即首位（真 index 實測）。
- **驗證**：e2e_gui_futu_trade 全綠（hermetic + live，含 popup 可見斷言）；e2e_gui_p8 無 regression；真 page + 真 index 離線端到端重現 HK/HSI/恒指/NVDA 全部即時有候選。

### FUTU 交易頁五項體驗增強 — 解鎖狀態自動探測 / 欄名三語 / 代碼繁簡英模糊 / 價格數量預填 / TIF 三語
檔案：`gateway/pages/futu_trade_page.py`、`gateway/i18n.py`、`test/e2e_gui_futu_trade.py`、`README.md`

- **解鎖狀態自動更新**（唔使等落單）：worker 新增 `unlock_probe` op — `modify_order(CANCEL, 999999999)`（唔存在 id，**實測無副作用**：未解鎖時解鎖檢查優先 → 回解鎖類錯誤；已解鎖回「订单不存在」）。揀 REAL 帳戶即時探測 + QTimer 10s 定時探測；落單/撤單成敗仍被動推斷（雙重信號）。
- **表格欄名三語 + 美化**：i18n 加 23 個 `col_*` keys；`_apply_headers` 喺 fill/retranslate 時套返當前語言；數字欄（qty/price/market_val…）右對齊；row 高度統一。
- **代碼模糊輸入繁簡英通配**：`_op_stock_search` cache 加 opencc t2s 正規化欄（同 symbol_search 同一 canonical pattern）— 簡體「腾讯」hit 繁體 entry；顯示名稱跟 GUI 語言（`_display_name`：zh_cn→t2s、zh_hk→s2t(t2s)、en→原生；opencc 缺席 no-op）。
- **Fix（用戶實測「打 HK 冇候選」）**：搜尋改以**完整 code**（`HK.00700`）匹配 — 之前 match 緊 basicinfo 嘅 raw code（`00700`），市場前綴 query 永遠 0 hits；含「.」亦照做模糊（`HK.007` → 收窄到 `HK.00700`），quote 並行試行情，唔完整/停牌（last_price 0）當失敗靜默處理。
- **價格/數量預填**：完整 code → worker `quote` op（`get_market_snapshot` → last_price/lot_size，quote ctx lazy 共用）→ price 預填最新價、qty 預填每手；`textEdited` 先計手動值，其後自動刷新唔覆蓋；stale code guard（結果 code 同當前唔符即丟）。
- **TIF 三語**：combo `addItem(三語文字, userData=enum)`，提交用 `currentData()`；語言切換保留選中項（findData）。
- **驗證**：e2e_gui_futu_trade 全綠（Part 4 重寫為自動探測流：選帳戶→即時未解鎖→模擬 GUI 解鎖→即時已解鎖，全程唔靠落單；Part 5 加簡體模糊/hint 跟語言/預填/手動唔覆蓋；Part 1 加欄名+TIF 三語斷言）+ live read-only OK；e2e_gui_p8 無 regression。

### FUTU 交易頁移除 SDK 解鎖 — 改解鎖狀態顯示（被動推斷）
檔案：`gateway/pages/futu_trade_page.py`、`gateway/i18n.py`、`test/e2e_gui_futu_trade.py`、`README.md`

- **根因**：官方 opend-skills 確認新版 OpenD **禁止 SDK `unlock_trade` 解鎖**（安全起見，必須喺 OpenD GUI 手動「解鎖交易」）— 舊設計（密碼欄 + 解鎖按鈕 + REAL auto-unlock pending chain）無論密碼啱唔啱都失敗（用戶實測「密碼正確但解鎖失敗」）。舊註釋「GUI 解鎖只對 OpenD 自己 session 有效」屬舊版行為，已過時。
- **改動**：刪密碼欄 / 解鎖按鈕 / `_op_unlock` / pending chain；下單唔再預檢解鎖。新增**解鎖狀態欄**（只 REAL 顯示）：冇查詢 API → 狀態由落單/撤單成敗被動推斷 — 成功=已解鎖；worker `_is_unlock_error` 將解鎖類錯誤歸 `reason_code='unlock_needed'`（沿用 reason_code 契約）→ 未解鎖 + 提示去 OpenD GUI；其他情況=未知（如實）。SIM 成敗唔污染狀態。
- **i18n**：刪 6 個解鎖 keys，加 5 個狀態 keys × 三語。
- **驗證**：e2e_gui_futu_trade 全綠（hermetic Part 4 重寫為三態流：未知→未解鎖→模擬 GUI 解鎖→已解鎖；FakeCtx 未解鎖時 REAL 落單/撤單回 unlock 錯誤）+ live read-only connect OK；e2e_gui_p8 無 regression。

### Fix symbol_search 繁簡 mismatch — index name load/fetch 時統一正規化做簡體
檔案：`modules/symbol_search.py`、`CHANGELOG.md`

- **根因**：code 假設 OpenD 回傳嘅港股中文名永遠係簡體（search 將 query t2s 正規化後 match），但實測 OpenD 回傳繁簡視版本/locale 而定 — 本機 `symbol_index.json` 存咗繁體（`騰訊控股`）→ CJK fuzzy search 0 hits + EN fallback 顯示繁體（e2e_gui_p8 2 FAIL，pre-existing）。
- **Fix**：新 `_normalize_cjk(entry)` — `_load()` / `fetch()` 時將 `name`/`name_zh`/`name_en` 嘅 CJK 欄統一 t2s 正規化做簡體（canonical 存簡體）；~0.2s / 25k entries；opencc 缺席 no-op（保持原行為）。正規化後：search 繁/簡輸入都 match、EN fallback 顯示原生簡體、zh 顯示 s2t 轉返繁體（round-trip 實測 `恒指期貨主連` 精確）。
- **自愈**：舊 cache 檔唔改動；下次 FETCH 會將全份 cache 以正規化形式 persist。
- **驗證**：e2e_gui_p8 14/14 PASS（之前 2 FAIL）；CLI search 繁/簡輸入都 hit HK.00700；futu trade page E2E 26/26 無 regression。

### One Gate Page 4 FUTU 交易 — OpenSecTradeContext 下單 / 今日訂單 / 持倉 / 帳戶資金（新頁）
檔案：`gateway/pages/futu_trade_page.py`（新）、`gateway/app.py`、`gateway/i18n.py`、`test/e2e_gui_futu_trade.py`（新）、`README.md`

- **範圍**：OpenD 交易連線 + 帳戶列表（`get_acc_list`）+ 下單（`place_order`，NORMAL/MARKET/AUCTION_LIMIT/LIMIT_IF_TOUCHED × DAY/GTC/IOC）+ 今日訂單（`order_list_query`）+ 撤選定 / 全數撤（`modify_order(CANCEL)` / `cancel_all_order`）+ 持倉同帳戶資金（`position_list_query` + `accinfo_query`，同一 op batched）+ 解鎖交易（`unlock_trade`）。
- **線程模型**：全部 SDK 調用行單一 QThread worker（queue 串行 — futu OpenD 連線唔係 thread-safe，唔似 quote 邊每 request 開獨立連線）；signal 自動 queue 返 GUI thread。`futu` lazy import 喺 worker method 入面（page module top-level 唔 import futu → E2E 可以 stub `sys.modules['futu']` 做 hermetic test）。
- **安全**：REAL 帳戶下單要 QMessageBox 確認 + 先解鎖；SIMULATE 直接行；全數撤有確認框。選定 REAL 帳戶時 label 用 accent 色提醒。
- **市場過濾 client-side**（filter `trdmarket_auth`，唔使重連）；connect 後自動拉帳戶列表、下單成功後自動刷新訂單表。
- **i18n**：40 個新 keys × 三語（fail-fast `t()`）；theme 跟隨外殼 listener registry（同其他頁同一 pattern）。單獨運行：`python gateway/pages/futu_trade_page.py`。
- **驗證**：E2E 26/26 PASS — offscreen hermetic 完整 flow（FakeCtx：connect→accounts / 市場過濾 / SIM 下單 / orders 自動刷新 / 撤選定 / positions+accinfo / REAL 解鎖錯密碼+確認框 path / disconnect ctx.close）+ i18n 三語 retranslate + theme 雙向；live read-only（真 OpenD 127.0.0.1:11111：connect + accounts + positions/accinfo，**絕不落單 / 撤單 / unlock**）。

## 2026-10-06

### Bump numpy / matplotlib pins — Python 3.14（cp314）wheel 兼容
檔案：`requirements.txt`

- numpy==1.26.4 → **2.5.3**、matplotlib==3.10.1 → **3.11.2**：舊版冇 cp314 win_amd64 wheel → pip fallback sdist build（numpy 撞 Meson `[WinError 4551]` fail）；新 pin = 最新有 cp314 wheel，cp312 一樣兼容。
- 驗證：Python 3.14.8 `pip install -r requirements.txt` 成功（futu-api sdist-only 但 pure-Python build 得）；全數 import + offscreen QApplication PASS。

### Fulltest stream rows → 持續 live（baseline 收到即 PASS，live tick 持續更新到 Stop）
檔案：`test/gui_fulltest.py`、`README.md`

- **根因**：用戶反映 fulltest_page HK.HSImain stream_kline「不會刷新」— 唔係 bug，`_bounded_consume` 本來就係 5s bounded window by design（kline_page 先係無界連續）；用戶確認改持續 live。
- **新語義**：第一次 yield = 歷史 baseline → 收到即刻出 verdict（✅ PASS；0 tick 唔算 fail — 可能冇成交時段 / IB 無 RTUS）；之後 live tick 持續更新結果格（`live_updates=N`）+ 數據表，到 Stop / 重跑為止。
- **實作**：`_bounded_consume` → `_start_live`（baseline wait 30s timeout；monotonic tick token — GUI 端 `_tick_gen` guard 直接丟舊 run 嘅 stale tick；重跑同一行先 cancel 舊 live task 並 `gather(return_exceptions=True)` 等 broker cleanup）；新 `stream_tick(idx, token, payload)` signal（worker thread emit → 自動 queue 去 GUI thread）；數據表 repaint throttle `TICK_UI_INTERVAL=0.25s`（4 Hz），label 文字每 tick 更新；Stop / `_clear_results` 清 `_tick_gen`。
- **驗證**：offscreen smoke 12/12 PASS（fake stream generator：baseline → 即刻 PASS、live_updates 計上、數據表 newest bar 刷新、重跑新 token + stale tick 被丟、Stop 清晒結果 + fake stream ctx cleanup、post-stop tick 被丟、get row regression、thread 乾淨退出）；真 E2E `test/e2e_gui_fulltest.py`（OpenD 11111）全 PASS — 包括 parallel 3 rows 0.2s、Stop → broker 端收到 K 線停止指令 + thread 乾淨退出。

### 新增 `requirements.txt` — 匯合全專案第三方依賴（單一安裝入口）
檔案：`requirements.txt`（新）、`README.md`

- 由全專案 import 分析匯出 + pip 實測版本 pin：PySide6==6.11.2、pandas==3.0.2、numpy==1.26.4、matplotlib==3.10.1、futu-api==10.5.6508、ib_async==2.1.0；opencc-python-reimplemented==0.1.7 標明 optional（symbol_search 有 ImportError fallback）。
- PySide6_Essentials/Addons/shiboken6 自動隨 PySide6 裝，唔重複 pin。

### One Gate Page 2 全功能測試 — 嵌入 gui_fulltest 全部功能（takeCentralWidget，零改動 gui_fulltest.py）
檔案：`gateway/pages/fulltest_page.py`（placeholder → 真頁）、`README.md`

- **嵌入模式**：同 K綫頁同一 pattern — 建隱藏 top-level `gui_fulltest.MainWindow()` 保留引用 alive（LoopThread / TestWorker lifecycle 綁定喺個 instance）；`takeCentralWidget()` 搬入頁面 layout，QSS 喺**頁面層級**套用。
- **退出清理鏈**：`aboutToQuit` → `self._win.close()` — hidden top-level 一樣 deliver QCloseEvent → 跑原 closeEvent 完整條鏈（request_shutdown → thread.wait(3000)，shutdown 會先釋放 IB clientId=99），idempotent。
- **theme 傳播**：gui_fulltest 冇 C_* 常數 / QSS template（同 gui_kline 唔同）→ 本頁自帶 string.Template QSS（palette 值由 `gateway.theme.THEMES` 注入，經 listener registry 跟隨外殼切換）+ 運行時重新指派 `gf.STATE_STYLE` QColor — light 用原檔色值（本身為淺底設計），dark 用提亮變體；label 字串保留。section header 有明確 item role bg/fg → 覆蓋 QSS，雙主題本來就清晰。
- **i18n**：gui_fulltest 冇自己嘅語言 combo → `retranslate()` no-op（外殼三語切換唔影響嵌入頁內容）。
- **單獨運行**：`python gateway/pages/fulltest_page.py` → standalone window（Run All / Stop 全部可用）。
- **驗證**：in-process smoke 39/39 PASS（objectNames ×5、table 20×8 結構、worker-ready 啟用 Run All/Stop、dark→light→dark theme 雙向 QSS + STATE_STYLE 顏色斷言、One Gate shell 入面嵌入頁 objectNames + nav 切換 + **Run All wiring enqueue 18 indices**、shell theme toggle 傳播、三語 retranslate no-crash、quit chain ×2 + idempotent、StandaloneWindow entry）；2 個入口點（gateway.py / fulltest_page.py standalone）offscreen sanity EXIT=124 無 traceback。

### One Gate Page 1 K綫測試 — 嵌入 gui_kline 全部功能（takeCentralWidget，零改動 gui_kline.py）
檔案：`gateway/pages/kline_page.py`（placeholder → 真頁）、`gateway/theme.py`（加 listener registry）

- **嵌入模式**：建隱藏 top-level `gui_kline.MainWindow()` 並保留 Python 引用 alive（thread lifecycle 綁定喺個 instance）；`takeCentralWidget()` 搬入頁面 layout，QSS 喺**頁面層級**套用（reparent 之後 central widget 已唔係原 window descendant）。
- **退出清理鏈**：`aboutToQuit` → `self._win.close()` — hidden top-level 一樣 deliver QCloseEvent → 跑原 closeEvent 完整條鏈（fetch wait(3000) → request_shutdown → thread.wait(3000）），idempotent。
- **theme 傳播**：運行時重新指派 gui_kline 模組級 C_* 常數 + `inspect.getsource` 提取 QSS f-string statement 用新值 exec 重建（唔 copy template，源碼改咗自動跟住變）+ restyle 建檔時 bake 咗嘅 spots（figure facecolor / readout / panel btn·info / sym·time label / price color）+ `chart._redraw()`。C_UP/C_DOWN 紅漲/綠跌語義色跟 theme 不變。
- **theme listener registry**（`gateway/theme.py`）：PySide6 6.11.2 **冇綁定** `QApplication.styleSheetChanged` → 改由 theme module 統一通知：`add_listener(fn)`，apply_theme 完成 setStyleSheet 後同步調用 `fn(name)`（callback 直接收 name 參數，避免 stale import binding）。
- **i18n sync**：外殼三語切換 → 嵌入頁自己嘅語言 combo（繁中/EN；簡中 fallback 返繁中字串）。
- **單獨運行**：`python gateway/pages/kline_page.py` → standalone window（模糊搜尋、get/stream 全部可用）。
- **驗證**：in-process smoke 31/31 PASS（objectNames ×6、central taken、dark→light→dark theme 雙向 QSS/figure/baked-spot 顏色斷言、i18n sync 雙向、hermetic 3s pump thread 活緊 + 零 fetch thread、quit chain wait(6000) + idempotent、One Gate shell 入面嵌入頁 objectNames + nav 切換）；2 個入口點（gateway.py / kline_page.py standalone）offscreen sanity EXIT=124 無 traceback。

### One Gate 外殼 — 多券商 gateway 匯合主窗口（頂部導航 + QStackedWidget + 三語 + 暗/淺色 theme）
檔案：`gateway.py`（新）、`gateway/` package（新，9 檔）

- **主入口** `python gateway.py`：thin launcher → `gateway/app.py::OneGateWindow`。頂部導航欄 = brand + 3 個 checkable nav 按鈕（`nav_kline` / `nav_fulltest` / `nav_connection`）+ disabled 預留位（`nav_reserved`，其餘頁面以後再加）+ 語言 combo + theme 按鈕；QStackedWidget（`page_stack`）切換三頁。
- **組件化**：每頁係獨立組件（`gateway/pages/` kline / fulltest / connection，本 ticket 先 placeholder），每個檔底層都有 `if __name__ == '__main__':` → `run_standalone()` 單獨開視窗（帶自己嘅語言/theme 控制）；合體入外殼只係 PAGE_KEYS registry 一行。
- **i18n 單一入口**（`gateway/i18n.py`）：STRINGS dict + `t(key, lang)`，支援 zh_hk / zh_cn / en — 三語全部明確字串（唔係 opencc 轉換）；unknown key 即刻 KeyError（fail-fast）。外殼全部文字（window title、nav、預留位 tooltip、語言 label、theme 按鈕）跟隨切換。
- **theme 模組**（`gateway/theme.py`）：dark = gui_kline 現有配色（#1E1F22 / #26282C / accent #F1553B…）、light = 新專業 palette（#E8EAED / #FFFFFF / accent #E5492F）；QSS 由 string.Template 統一生成，一鍵切換即時生效。
- **QSS scoping**：全部規則用 `[og="..."]` property selector + objectName — 之後 #03/#04 嵌入 gui_kline / gui_fulltest 時，佢哋程式化設定嘅顏色唔會俾 app-level QSS 覆蓋。
- **純新增**：test/ 同 modules/ 零改動。
- **驗證**：in-process smoke 87/87 PASS（objectNames ×13、nav exclusive 切換、三語 retranslate、theme 雙向 toggle + QSS 斷言、3 頁 standalone window）；4 個入口點 offscreen subprocess sanity 全部 EXIT=124（活到俾 kill = 無 startup crash）、log 零 traceback。

### 綜合測試矩陣 GUI — broker 分區 + 隱藏 EXPECTED FAIL toggle + 數據表時間反向排序 + 串流 UPDATE 核實（用戶實測後改）
檔案：`test/gui_fulltest.py`、E2E `test/e2e_gui_fulltest.py`

- **Broker 分區更明顯**（用戶要求「不同BROKER 的TEST 要分區更明顯」）：table 加 2 條 section header row（`▍FUTU · 富途 OpenD — 11 tests` / `▍IB · Interactive Brokers (TWS/Gateway) — 7 tests`），col 0 `setSpan(1, 8)` 橫跨全欄、粗體 +2pt、futu/ib 各用獨立底色（#2f4858 / #5d4178）；table 由 18 rows → **20 rows**，`_test_to_row` / `_row_to_test` mapping 令 worker signal handler 完全唔使改。
- **隱藏 EXPECTED FAIL toggle**（用戶要求「右上角加上按鍵切換是否隱蔵EXPECTED FAIL」）：toolbar checkable 按鈕 `hide_expected_fail` — **display-only**：只 `setRowHidden(expect=False rows)`，Run All / Stop / 單行 ▶ 邏輯完全唔受影響（E2E monkeypatch enqueue 驗證隱藏狀態下 Run All 仍然 enqueue 全部 18）。
- **結果數據表按時間反向排序**（用戶要求「結果的DF 要按時間反向排序」）：`_fill_data_table` 改 `df.tail(50).iloc[::-1]` — 最新 bar 喺第一行（仍係最後 50 rows）。
- **串流 UPDATE 核實結論**（用戶反映「串流的UPDATE 好像不對没有串流」）：**pipeline 無 bug**。結果 cell 係 static text by design（跑完先寫 `snapshots=N live_updates=M`，冇 live tick 顯示）；`live_updates=0` 可能係無成交時段 / IB 無 RTUS。開市時 live Run All 實測：3 條 futu stream 全部 `snapshots=2 live_updates=1`（tick 有流就有 update）。n≤1 時 detail 加 hint「5s 內無新 tick — 可能係無成交時段 / IB 無 RTUS；baseline 已收到即算成功」。
- **E2E fix**：PySide6 6.11.2 冇綁定 `spanAt()`（`Qt.ItemDataRole.SpanRole` enum 都唔存在）→ 改斷言 `(rowSpan(r,0), columnSpan(r,0)) == (1, 8)`。
- **驗證**：E2E `test/e2e_gui_fulltest.py` 39/39 PASS EXIT=0（含 section span、hide_ef display-only、newest-first）；live Run All（OpenD + TWS 雙開）**pass=10 / expected_fail=8 / fail=0**，同 baseline 完全一致（34.8s）。

### 綜合測試矩陣 GUI — Run All 全並發 + 參數拆多欄（用戶實測後改）
檔案：`test/gui_fulltest.py`、E2E `test/e2e_gui_fulltest.py`

- **Run All = 全部同時並發**（用戶要求「RUNALL 時不要順序，要全部同時TEST」）：
  - `TestWorker` 由「單 runner task 順序消化 asyncio.Queue」改做**每行一個 `asyncio.Task` 即刻開跑**。
  - futu：每行各開獨立 `OpenQuoteContext`（OpenD 支援多條並發連線，handler 各自獨立）。
  - IB：7 個 request 共用同一條 TWS session、多 reqId 並行 — per-request error 歸屬靠 `bars.reqId` + `_error_log` sink；`_connect_lock` 防雙重連線（idempotent connect）；stream state 係 closure-local，互不干涉。
  - `_client_lock` 序列化首次 `BrokerClient()` 建立（IB clientId=99 exclusivity — 同一個 loop 只可以有一條連線）。
  - `_pending` 計數器 → 歸零先 emit `run_finished` summary（並發完成順序任意都正確）。
  - Stop / shutdown 共用一條 `_cancel_all()` 路徑：cancel 所有 row + consume task → await broker cleanup（finally）→ 重置 pending。
  - **實測**：全 18 rows（11 futu contexts + 7 IB requests）真撳 Run All 按鈕，**22.8s 完成**、PASS=10 / EXPECTED_FAIL=8 / FAIL=0（順序跑要 ~90s+）。
- **參數拆多欄顯示**（用戶要求「參數的顯示分為多列，比較清晰」）：table 由三欄改做**八欄** `[測試 | code | market | ktype | num | broker | method | 結果]`；參數欄 `ResizeToContents`、結果欄 `Stretch`。
- **Bug fix（用戶實測「RUN ALL 没有用」）**：兩個 toolbar 按鈕建咗但漏咗 `.clicked.connect()` → 撳落去冇反應。補上 connect（Run All + Stop）。
- **Stop 語義**：cancel 所有運行中 row/consume task + **即刻清理所有結果**（GUI `stop()` 先清一次；worker cleanup 完成後 `stopped` signal 再清一次 catch late paint）。
- **成功行數據拆疊**：結果下方「▸ 數據（N rows）」按鈕 → mini `QTableWidget`（最後 50 rows，maxHeight 180）；stream 行都有數據（`_bounded_consume` 返 `(n, last_df)`）。
- **矩陣內容**：18 rows = futu/ib × {US.NVDA, HK.00700, US.NQmain, HK.HSI, HK.HSImain, US.NONEXIST123} × get_kline/stream_kline；expect True / False（誠實失敗 + must_contain）/ None（ℹ️INFO 只顯示）。期望值全部 live-verified。
- **結果色**：✅PASS / ⚠️EXPECTED FAIL（橙，預期內誠實失敗）/ ❌FAIL / ℹ️INFO。
- **驗證**：E2E `test/e2e_gui_fulltest.py` 20/20 PASS EXIT=0（只需 OpenD；含 3 rows 真並發計時、Run All wiring、Stop 清理運行中行）。

### P8 — GUI 模糊搜尋 + i18n 繁中/EN + FETCH
檔案：`modules/symbol_search.py`（新）、`test/gui_kline.py`、E2E `test/e2e_gui_p8.py`（新）

- **Canonical symbol language = Futu quote code**（用戶用 Futu app 對照；覆蓋全球股票/期權/期貨/指數/窩輪/牛熊證 — 窩輪牛熊證就係 SEHK 上市證券，行港股同一條翻譯路）。
- `modules/symbol_search.py`：本地 symbol index + fuzzy search。
  - **SDK reality（probe 實測）**：futu-api 10.05.6508 **冇** `search_quote` / `get_stock_screen` → remote fallback 死咗，local index only + pass-through（autocomplete 永遠唔 block 輸入）。`get_stock_basicinfo(market, stock_type)` 唔帶 code_list = 枚舉該 market 全部 code。
  - Index = `modules/symbol_index.json`（gitignored，~3.7MB）：US+HK 股票/ETF/指數/期貨 main only；24,819 entries；`fetched_at` aware ISO；stale >24h → startup auto-fetch + GUI FETCH 按鈕手動 refresh。
  - Name language reality：`name` 欄 per-row 單語言、同一市場都混（出名美股返中文、冷門留英文）→ per-row `_has_cjk()` fill name_zh/name_en，顯示缺邊個語言 fallback 原生名（honest，唔 fake translate）。
  - `opencc-python-reimplemented`：`t2s` 將用戶繁體 input 正規化做簡體先 match；`s2t` zh 模式顯示轉返繁體。非硬依賴（ImportError → None graceful fallback）。
  - **期貨**：只留 `main_contract=True` row + regex 由 live data 合成 canonical 'XXmain'（`HK.HSI2610→HK.HSImain`）— 永遠唔 hardcode 月份。
- `test/gui_kline.py`：標的欄 fuzzy autocomplete（中/英文名、繁簡通配）+ i18n 繁中/EN 切換（STRINGS dict + `t(lang,key)`，標題/label/symbol name 跟隨切換）。
  - **FuzzyCompleter trap**：`complete()` 唔係 C++ virtual → Python override 攔唔到內部 call；正確 pattern = textChanged→set_query 重建 `QStringListModel`（scored hits）再 call 真 `complete()` + override `filterAcceptsRow`（virtual）。**唔好 override activated signal**（Python method shadow C++ signal descriptor 會炸 .connect）。
- **驗證**：E2E `test/e2e_gui_p8.py` 14/14 PASS EXIT=0（hermetic FakeDir）+ cli_kline regression CONTRACT futu=PASS ib=PASS。

### P7 — resolve_symbol + config alias 層
檔案：`modules/broker_base.py`、`modules/ib_client.py`、`test/cli_kline.py`

- `resolve_symbol()` per client（L2 規則，純函數唔打網絡）：futu = identity（base default）；ib = L0 upper + L1 config `ib.symbol_aliases`（IB-only pin）+ L2 `'X.YYmain'→'YY:FUT'`。
- IB `_make_contract` 入口經 resolve_symbol；MARKET.SYMBOL fallback 用 **INDEX-before-FUTURE**（canonical code 無 main 永遠唔係期貨語義 — 防 HK.HSI 喺有權限 account 靜默變 HSI 期貨）。
- `future_exchanges` 改 (ex,ccy) tuples + SEHK/HKD（probe-verified：SEHK 係 TWS 接受嘅 HK destination；HKEX/CFES/PKE = Invalid destination）。
- **本 account permission facts（probe 實測）**：無 HK derivatives permission（HSI futures/index 全 venue × 月份 × currency 都 Error 200）→ ib `HK.HSImain` honest fail「無法解析 HSI（試過: FUTURE）」就係 contract test case。
- **驗證**：cli_kline MAPPING_CASES — futu `HK.HSImain` PASS rows=1000 / ib honest fail；CONTRACT futu=PASS ib=PASS EXIT=0。

### P6 — IB error honesty（如實返回錯誤）
檔案：`modules/ib_client.py`、probe 腳本（已清理，findings 喺呢度 + memory）

- **用戶要求**：「尽量status data fail 返回，比如IB 没有標的，或者没有PERMISSION 要如實返回錯誤」。
- IB 最大缺口 = ib_async 靜默回空 list，generic "timeout 或 error" 混埋三種原因。解法：**per-request `errorEvent` capture**（scoping 靠 `bars.reqId` + `_error_log` sink）+ code→人話 message 映射表 + stream fail-fast（permission error 即刻返 False），120s watchdog 留做 backstop。
- **probe-first 實測本 account 實際收到嘅 code**：162（HMDS no data，唔係記憶入面嘅 354）/ 200 / 10314；warning band 2100–2200 唔算 fatal。
- **Error 200 nuance**：TWS 對「冇權限嘅 security」同「唔存在嘅 symbol」都回 200（隱藏 access）→ hint 明講兩者；`_resolve_contract` fail message 由 `_error_log` 10s window scan 帶最後錯誤 hint，唔再只講「無法解析」。
- stream 有 3s explicit-error grace window（第一筆 live bar 提前離開）。

### P5 — print → logging
檔案：`modules/__init__.py`、各 client

- app-level log config 集中喺 `modules/__init__.py`（root INFO + 時間戳 format）；所有 module 由 print 轉用 `logging.getLogger(__name__)`。

### P4 — 失敗路徑統一 triple 契約
檔案：`test/cli_kline.py`、各 client

- 壞代號 / 無權限 / timeout **必須回 `(False, None, message)`，唔好噴 exception**（統一 triple 契約）；三種原因要三句唔同 message。

### P3 — K 線 schema 單一來源
檔案：`modules/kline_schema.py`（新）、各 client

- `KLINE_COLUMNS = ['time_key','open','high','low','close','volume']` + `reorder_kline()` + `validate_kline()`；所有 broker client 輸出成功前必驗 schema — 唔符合當失敗回報，壞形狀唔會流出到 caller。
- futu `time_key` 統一 parse 成 datetime（同 IB）— 之前 str/datetime 兩制令 schema 唔一致。

### P2 — registry：券商名單單一來源
檔案：`modules/registry.py`（新）、`modules/broker.py`

- `BROKERS = {cls.NAME: cls}` + `get_broker_class()` fail-fast；**加新 client 只改 registry.py 一行** → dispatch / GUI combo / contract test 自動覆蓋。
- config-driven instantiation：每個 client 按自己 NAME 讀 `config.json` 對應 section；`source.*` 值開機時驗證（打錯字即刻炸）。

### P1 — 統一契約 BrokerBase
檔案：`modules/broker_base.py`（新）、各 client

- 所有券商 client 繼承 `BrokerBase`：聲明 `NAME` + 實作 `get_kline()` / `stream_kline()`，一律回 `(status, data, message)` triple；`disconnect()` 預設 no-op。
- futu 改做**每條 stream / 每次 get_kline 獨立 OpenQuoteContext**（廢共享 context）— OpenD 支援多條並發連線，handler 各自獨立互不干擾。

---

## 2026-10-05

### Futu streaming K 線成功
檔案：`modules/futu_client.py`、`test/cli_kline.py`、`test/gui_kline.py`

- `stream_kline()` = baseline（歷史快照）+ live push（handler → queue buffer → drain 拼接），回 `(True, async_gen, None)`；consumer cancel task / `.aclose()` 停止，finally 斷呢條 stream 專用連線。
- B1–B5 fixes：drain 晒 buffer（高頻唔會無界增長）/ 亂序 bar 跳過 / 同 bar 原地覆蓋 / NaN volume 填 0 / normalize 先 copy 再改。

## 2026-10-04

### Stream K 線原型
檔案：`modules/futu_client.py`、`test/gui_kline.py`

- `stream_kline` 初版（handler + subscribe push + 歷史底表）；GUI 即時更新。

# 36 — 交易帳戶頁（主菜單直接按鈕）+ 帳戶模組

需求（用戶原話）：
- 「增加一頁，交易帳戶／里面包括 FUTU 和 IB 的所有帳號列表，類型資金明細」
- 「按BROKER 數量加不同欄位，水平排列」
- 「將交易帳戶模組化，在量化交易、FUTU交易頁面共用」
- 「每個BROKER 的帳號每一個做卡片排列清楚，實盤用紅色主色，模擬帳號用藍色主色，每個帳號可以有別名設置，
  以後全域可以用別名顯示來選擇的帳戶，比如富途實盤(4654894524)」
- 「富途將STATUS 不是ACTIVE帳號的屏敝，每個帳號資金等每10秒更新一次，增加手動刷新按鍵」

既定事實（已核對）：
- 兩條互不相干的帳戶管線：`futu_trade_page` 自持 `OpenSecTradeContext`、直接 `get_acc_list()`；
  `quant_page` 經 `BrokerClient.trade_accounts()`，只填下拉、不做過濾。無跨券商視圖、無資金週期更新、無別名。
- `modules/trade_base.py`：`ACC_COLS`（acc_id/trd_env/acc_type/trdmarket_auth/acc_status）、
  `ACCINFO_KEYS`（6 項資金）。無跨券商聚合方法；`BrokerClient` 交易分派每次只針對一個 broker。
- futu-api 10.5.6508 `get_acc_list()` 回 `acc_status` ∈ ACTIVE/DISABLED/'N/A'、`trdmarket_auth` 係 list；
  **SDK 完全沒有帳戶顯示名字段** → 別名無法由券商資料預填。
- ib_async 2.1.0：`managedAccounts` 係 **method**（`ib.py:497`）→ `ib_client.py:482` 現有寫法
  `list(<bound method>)` 對真實 TWS 會 `TypeError`（契約測試用假 list 掩住了）。無 `IB.isPaper`；
  `reqAccountSummaryAsync('Global','AccountType',…)` 是取得帳戶類型唯一真實路徑。
  `account_info()` 的 `acct = str(account or '')` → `ib.accountValues('')` 會合併所有帳戶。
- 全app 只有 8 個 palette token，無藍色；`gui_kline.C_UP='#F23645'` 是既有紅色、`C_DOWN` 是青綠。
  語義色政策見 `futu_trade_page.py:512`（不入 palette，兩 theme 通用）。
- 卡片網格先例：`backtest_page.py:618-653`（`.ui` 宣告空 grid slot、缺 slot 即 `RuntimeError`、
  runtime registry 餵 `stamp()`）；10 秒定時器先例：`futu_trade_page.py:678`；
  自動刷新 + 重入保護先例：`home_page.py:59,267`。

## Checkpoints

1. [X] 36a 領域層 `gateway/accounts.py`（`ROW_KEYS`／`acc_key`（int-str 歸一）、`normalise_rows`、
       `active_only`、`display_name`、別名儲存 `SECTION='accounts'`、`FUND_LABEL_KEYS`、`fund_rows`、
       async `collect_accounts`／`refresh_funds`（client 為參數，不 import modules））
       — ✅ `.scratch/test_accounts.py` 55 項全綠；零回歸：`test_trade_contract` 58 /
         `test_quant_exec` 76 / `test_position_model` 41 / `t_i18n_json` 13 /
         `t_scan_colloquial` 0 命中，全部 exit 0
       ⚠️ 教訓：生成名不能由 `broker_label + env_label` 直接相接 —— 中文相接得「富途實盤」，
         英文會得「FutuREAL」。分隔符本身屬語言 → 新增 `ta_label_fmt`（zh `{broker}{env}` /
         en `{broker} {env}`），措辭與括號全由 i18n 決定
       ℹ️ 環境標籤沿用既有 `trade_env_real`／`trade_env_sim`（en 為「REAL」／「SIMULATE」），
         不另設 title-case 版本：同一概念在全app 讀法一致
       ℹ️ 未實作 `ta_type_na`／`ta_status_na`／`ta_markets_fmt`：改用單一 `ta_field_na`（36c 加），
         市場以 `・` 直接串接，不需要格式 key
2. [X] 36b 供應商如實化：`ib_client.trade_accounts` 的 `managedAccounts` **是 method**（真 bug）+
       best-effort `AccountType` → `acc_type`（失敗照常留空）+ `account_info` 帶回 `currency`；
       富途**不加** ACTIVE 過濾、**不改** currency
       — ✅ `.scratch/test_trade_contract.py` 66 項全綠（原 58，新增 8）；零回歸：`test_accounts` /
         `test_quant_exec` 75 / `test_position_model` 全部 exit 0
       ⚠️ 教訓：假物件的形狀必須照真 API。`managedAccounts` 給假 list → `list(<bound method>)`
         的 TypeError 在契約測試全綠下存活；同理 `accountValues` 的假 `SimpleNamespace` 從前沒有
         `currency`，就測不到「幣種一直在手但被丟棄」
       ℹ️ 實測 ib_async：`reqAccountSummaryAsync()` **不帶參數**（內部固定 tag 清單已含 AccountType），
         結果讀 `ib.accountSummary()`；無 `AccountSummary` 類別。權限不足時 future **永不 resolve**
         （TWS Error 321）→ 必須 `asyncio.wait_for`；三條失敗路徑（擲出／卡住／讀取失敗）都只留空
       ℹ️ `acc_status='ACTIVE'` 對 IB 是推論（TWS 只回可交易的 managed account），已在碼內標明
3. [X] 36c 頁 `gateway/pages/accounts_page.py` + `gateway/ui/accounts_page.ui`（每券商一欄＝`.ui` 按
       `registry.BROKERS` 宣告、缺 slot 即 `RuntimeError`；每帳戶一卡；`[env=REAL/SIMULATE]` 語義色；
       10 秒 QTimer（show/hide 起停）+ 手動刷新；token + 重入保護；`_on_app_quit`）
       + i18n 三語 + `app.py` 四處註冊
       — ✅ `.scratch/e2e_gui_accounts.py` 94 項全綠；零回歸：`e2e_gui_shell` / `e2e_gui_home` /
         `e2e_gui_quotes` 全部 exit 0
       ⚠️ 教訓（真 bug，測試揪出三個）：
       ① **執行期建的控件不會被父控件的 `show()` 帶出來** → 卡片 `addWidget` 後必須 `frame.show()`，
          否則整頁卡片永遠看不見（`isHidden()` True、尺寸停在 100x30）。`home_page` 之所以沒事，
          因為它的卡在 `__init__`（頁面顯示前）建立。只斷言文字會完全漏掉這一類失敗。
       ② 卡上欄位／資金／別名標籤只在 `_retranslate_widgets` 賦值 → 新建卡片一片空白，
          要等使用者換語言才出現。抽出 `_sync_card_captions(card)`，`_build_card` 結尾即呼叫一次
       ③ 資金一輪失敗後合併值仍有資料，`state` 卻仍為 `ok` → 數值照樣顯示為最新。
          條件必須是「有資料且本輪無錯誤」才 `ok`，否則 `stale`
       ④ 欄上的「已屏蔽 N 個」與券商原文只在 `_on_accounts` 寫入 → 換語言不跟隨。
          改存 `_col_errors`／`_col_filtered`，`retranslate` 時重放；未取過任何一輪時不得聲稱
          「沒有帳戶」（根本還沒問）
       ℹ️ 測試手法：先 `_timer.stop()` 再手動 `_on_tick()` 驅動，否則 10 秒定時器會在多步斷言中途
         令 token 過期；重入段結束後要等 `worker._listing` 釋放，下一節才從空閒狀態開始；
         「卡片數量不變」會假過（上一輪被擋掉時卡片也不會少）→ 改等 `_last_update` 推進
4. [X] 36d 別名：卡上即時儲存、衝突如實擋（不靜默覆蓋）、重設回生成名、生成名**永不入檔**
       （只存用戶別名 → 換語言照常正確）
       — ✅ e2e 斷言全綠：即時生效／寫入儲存／acc_id 保留在括號內／撞名擋下並用對方顯示名講清楚／
         超長擋下不截斷／重設回落／生成名不在檔（直接 grep 狀態檔）／設別名後換語言：別名原樣保留、
         未設別名的帳戶改用該語言的生成名／第二個頁面實例從檔重載
       ℹ️ en 的 `ta_name_fmt` 是「{label} ({acc_id})」（括號前有空格）→ 斷言一律由 i18n 生成期望值，
         不寫死中文形式；期望值也不經 `acc.display_name`，避免自證
5. [X] 36e 兩頁共用同一模組：量化頁帳戶下拉改用 `display_name`（`retranslate` 重填）、
       富途交易頁帳戶表加顯示名欄 + 選中標籤改用 `display_name` + ACTIVE 規則改指 `accounts.active_only`
       — ✅ `e2e_gui_futu_trade.py` 95 項全綠／`e2e_gui_quant.py` 137 項全綠；零回歸：
         `test_accounts.py`／`test_trade_contract.py`（66 項）／`e2e_gui_accounts.py`（94 項）全部 exit 0
       ⚠️ 教訓：
       ① 富途頁的帳戶行原本不帶來源券商 → 顯示名無法生成（不知道是富途還是 IB）。改在 worker
          `_op_connect` 內 `normalise_rows(..., 'futu')`；原始行仍存 `_accounts_all`，生成文字只進
          顯示行 `_acc_rows`（真相與顯示分開，斷言直接檢查這一點）
       ② 量化頁的券商由 `BrokerClient` 解析（`broker=None` 跟 `config.source.trade`）→ 在 GUI 再推
          一次會漂移。改由 worker 以 `trade_supported(broker)` 的第二個回傳值（已解析的券商 key）
          一併回報，顯示名與 acc_key 都用它
       ③ 生成文字必须由新語言重新生成，不能改舊 cell／itemText：兩頁各加 `_refill_accounts()`／
          `_refill_account_combo()`，由 `retranslate` 重填。`selectRow` 對已選行不發 signal →
          需自行把 `_acc` 指向新行，否則留低舊語言的 dict
       ④ 未取過帳戶前不得聲稱「無帳戶」：`_acc_rows_last = None` 作哨兵（與 36c ④ 同一立場）
       ⑤ 環境語義色統一為 QSS property（`bind.set_prop` + `[env="REAL"/"SIMULATE"]`），富途頁原本的
          inline `setStyleSheet` 已刪除；`$real`/`$sim` 由 `theme.C_REAL/C_SIM` 代入 → 全站一種紅
       ℹ️ 兩處刻意收窄範圍（記錄取捨，非遺漏）：
       · 量化頁下拉不做 `active_only` 過濾 —— 該頁沒有能解釋「已屏蔽 N 個」的位置，靜默少一個帳戶
         比多顯示一個非 ACTIVE 帳戶更難察覺（交易帳戶頁有欄位註明、富途頁帳戶表有 acc_status 欄）
       · 下拉項目文字只含顯示名，不再附 `acc_status`：狀態在交易帳戶頁卡片與富途頁帳戶表均可見
       ℹ️ 測試手法：期望值一律由 i18n 重組（`expect_name()`），不呼叫被測的 `display_name`；
         fake 的 `trade_supported` 改回傳小寫券商 key（真實即 registry key），否則 acc_key 形狀與
         真環境不同；「未取過帳戶前下拉留空」要在第一次按刷新鈕之前斷言
6. [X] 36f 守門與文案：`t_usage_hint_style.py` PAGES 新增本頁 + 「`ta_grid_*` == BROKERS」守門；
       `t_i18n_json.py` 三語齊全；`t_scan_colloquial.py` 0 命中
       — ✅ `t_usage_hint_style.py` 160 項全綠（含本頁 `accounts_page_note`/`ta_usage` 的 role 與三語
         文案，以及新增第 5 節：`ta_grid_*` 正好等於 `registry.BROKERS`、逐券商 `getattr(root,
         'ta_grid_<b>')` 可取用）；`t_i18n_json.py` 13 項全綠（674 鍵三語齊全）；
         `t_scan_colloquial.py` 高置信／低置信／zh_cn 繁體殘留／zh_hk 簡體殘留全部 0 條
       ℹ️ 守門放在 `t_usage_hint_style.py` 而非 `t_ui_infra.py`：同一個 `.ui` 已在此載入並餵給
         `stamp()`，slot 檢查順帶覆蓋同一份載入路徑；斷言用 `getattr(root, ...)`（與頁面
         `_grid()` 同一條取用路徑），不是只 grep `.ui` 字串
       ℹ️ `_STAMP` 已按 `BROKER_REGISTRY` 生成 `ta_col_<b>_err`，故第 2 節的 missing 檢查已隱式
         守住「每券商的錯誤標籤」；grid 屬 QLayout、不在 `_STAMP`，因此需要顯式守門
7. [X] 36g README（核心功能 + 十三頁 ×2 + 檔案地圖）／CHANGELOG 同步 — 逐項測試出示全綠 Log
       — ✅ README：核心功能新增本頁條目、「十二頁」→「十三頁」兩處、`gateway/ui/*.ui` 清單加
         `accounts`、計數更正（144 → 160 項、630 → 674 鍵）、檔案地圖新增 `gateway/accounts.py`／
         `pages/accounts_page.py`／`test_accounts.py`／`e2e_gui_accounts.py`，共用改動寫入既有
         `futu_trade_page`／`quant_page`／`test_trade_contract` 條目
         ⚠️ 檔案地圖的舊「Page N」標籤自 `symbol_list` 起已錯位 → 本頁只標「[交易帳戶頁]」，
           不逐條重編號（重編號會掩住本次改動的 diff）
       — ✅ CHANGELOG：`## 2026-10-09` 下新增 #36 條目（書面語，含要求原話、🔑 結構決定、
         🐞 八個真 bug、⚠️ 五條測試基建教訓、驗證逐 suite 計數）
       — ✅ 全綠 Log（全部 exit 0）：`test_position_model`／`test_quant_exec` 75／`test_accounts`／
         `test_trade_contract` 66／`t_i18n_json` 13／`t_scan_colloquial` 0 命中／`t_ui_infra`／
         `e2e_gui_shell`／`e2e_gui_home`／`e2e_gui_quotes`／`e2e_gui_connection`／
         `e2e_gui_accounts` 94／`e2e_gui_futu_trade` 95／`e2e_gui_quant` 137／`e2e_gui_backtest` 105／
         `e2e_gui_favorites`／`e2e_gui_indicators`／`e2e_gui_strategies`／`e2e_gui_symbol_list`／
         `e2e_gui_fulltest`（獨立執行，PASS）

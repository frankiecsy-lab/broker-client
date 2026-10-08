"""One Gate i18n — 單一入口：STRINGS dict + t()。

三語（zh_hk / zh_cn / en）全部係**明確字串**，唔係 opencc 轉換 —
每種語言嘅文案由人手寫定，避免自動轉換出錯嘅專業術語。
"""

LANGS = ('zh_hk', 'zh_cn', 'en')
DEFAULT_LANG = 'zh_hk'

# 各語言自稱（endonym）— combo 顯示用固定字串，唔跟隨 UI 語言切換
LANG_LABELS = {'zh_hk': '繁體中文', 'zh_cn': '简体中文', 'en': 'English'}
# 語言三按鈕短標籤（用戶：唔准 dropdown）— 照 endonym 原則唔跟 UI 語言變
LANG_SHORT = {'zh_hk': '繁體', 'zh_cn': '简体', 'en': 'EN'}


def _s(zh_hk, zh_cn, en):
    return {'zh_hk': zh_hk, 'zh_cn': zh_cn, 'en': en}


STRINGS = {
    # ── 外殼（nav / toolbar / window title）──
    'app_title': _s(
        'One Gate — 多券商 Gateway 匯合',
        'One Gate — 多券商 Gateway 汇聚',
        'One Gate — Multi-Broker Gateway Hub'),
    'nav_quotes': _s('行情', '行情', 'Quotes'),
    'menu_test': _s('測試', '测试', 'Tests'),            # nav 子選單：K線/全功能/標的列表
    'menu_settings': _s('設定', '设置', 'Settings'),      # nav 子選單：連綫測試
    'page_quotes_title': _s('行情 — 多格 K 圖', '行情 — 多格 K 图', 'Quotes — Multi-Chart Grid'),
    'quotes_symbol_ph': _s(
        '標的：HK.00700 / 騰訊 / NVDA',
        '标的：HK.00700 / 腾讯 / NVDA',
        'Symbol: HK.00700 / Tencent / NVDA'),
    'quotes_invalid_code': _s('❌ 無效代碼', '❌ 无效代码', '❌ Invalid code'),
    # ── Page 5 標的列表 ──
    'nav_symbol_list': _s('標的列表', '标的列表', 'Symbol List'),
    'page_symbol_list_title': _s('標的列表', '标的列表', 'Symbol List'),
    'sl_search_ph': _s(
        '模糊搜尋：代碼 / 中文名 / 英文名（全部種類）',
        '模糊搜索：代码 / 中文名 / 英文名（全部种类）',
        'Fuzzy search: code / name (all types)'),
    'sl_update': _s('一鍵更新', '一键更新', 'Refresh All'),
    'sl_updating': _s('更新中（Futu OpenD 枚舉，全部市場）…', '更新中（Futu OpenD 枚举，全部市场）…',
                      'Refreshing (Futu OpenD enumeration, all markets)…'),
    'sl_total': _s('共', '共', 'Total'),
    'sl_showing': _s('顯示', '显示', 'Showing'),
    'sl_updated_at': _s('更新於', '更新于', 'Updated at'),
    'sl_option_note': _s(
        '期權：冇接口可以一次枚舉「邊個有期權」→ 呢度列股票/ETF 候選，點擊即查該標的期權鏈（二級）',
        '期权：没有接口可以一次枚举“谁有期权”→ 这里列股票/ETF 候选，点击即查该标的期权链（二级）',
        'Options: no API enumerates which underlyings have options → stocks/ETFs listed as '
        'candidates; click one to fetch its option chain (level 2)'),
    'sl_mkt_all': _s('全部市場', '全部市场', 'All Markets'),
    'sl_mkt_hk': _s('港股', '港股', 'HK'),
    'sl_mkt_us': _s('美股', '美股', 'US'),
    'sl_type_all': _s('全部種類', '全部种类', 'All Types'),
    'sl_type_stock': _s('股票', '股票', 'Stocks'),
    'sl_type_etf': _s('ETF', 'ETF', 'ETFs'),
    'sl_type_idx': _s('指數', '指数', 'Indexes'),
    'sl_type_future': _s('期貨', '期货', 'Futures'),
    'sl_type_option': _s('期權', '期权', 'Options'),
    'sl_type_warrant': _s('窩輪', '窝轮', 'Warrants'),
    'sl_head_code': _s('代碼', '代码', 'Code'),
    'sl_head_name': _s('名稱', '名称', 'Name'),
    'sl_head_market': _s('市場', '市场', 'Market'),
    'sl_head_type': _s('種類', '种类', 'Type'),
    # ── Page 5 下鑽（期權二級 / 窩輪三級）──
    'sl_back': _s('◀ 返回', '◀ 返回', '◀ Back'),
    'sl_drill_hint': _s('點擊行進入下一級', '点击行进入下一级', 'Click a row to drill down'),
    'sl_w_head_total': _s('窩輪數', '窝轮数', 'Warrants'),
    'sl_w_head_count': _s('數量', '数量', 'Count'),
    'sl_w_head_wtype': _s('類別', '类别', 'Category'),
    'sl_w_head_strike': _s('行使價', '行使价', 'Strike'),
    'sl_w_head_expiry': _s('到期日', '到期日', 'Expiry'),
    'sl_w_head_lot': _s('每手', '每手', 'Lot size'),
    'sl_w_head_otype': _s('期權類型', '期权类型', 'Option type'),
    'sl_w_call': _s('認購證', '认购证', 'Call'),
    'sl_w_put': _s('認沽證', '认沽证', 'Put'),
    'sl_w_bull': _s('牛證', '牛证', 'Bull'),
    'sl_w_bear': _s('熊證', '熊证', 'Bear'),
    'sl_w_inline': _s('界內證', '界内证', 'Inline'),
    'sl_w_other': _s('其他', '其他', 'Other'),
    'sl_opt_call': _s('認購', '认购', 'CALL'),
    'sl_opt_put': _s('認沽', '认沽', 'PUT'),
    'sl_us_warrant': _s('美股窩輪（無所屬標的數據）', '美股窝轮（无所属标的数据）',
                        'US warrants (no underlying data)'),
    'sl_chain_loading': _s('查詢期權鏈', '查询期权链', 'Fetching option chain'),
    'sl_chain_none': _s('呢個標的冇期權鏈', '这个标的没有期权链', 'No option chain for this underlying'),
    # ── Page 6 標的收藏（favorites_page / gateway.favorites）──
    'nav_favorites': _s('標的收藏', '标的收藏', 'Favorites'),
    'page_favorites_title': _s('標的收藏', '标的收藏', 'Favorites'),
    'fav_add_ph': _s(
        '新增收藏：代碼 / 中文名 / 英文名（本地 index 模糊）',
        '新增收藏：代码 / 中文名 / 英文名（本地 index 模糊）',
        'Add favorite: code / name (local index fuzzy)'),
    'fav_add': _s('＋ 新增收藏', '＋ 新增收藏', '+ Add favorite'),
    'fav_remove': _s('🗑 刪除所選', '🗑 删除所选', '🗑 Remove selected'),
    'fav_head_added': _s('加入日', '加入日', 'Added'),
    'fav_empty': _s('未有收藏 — 上方輸入代碼新增', '未有收藏 — 上方输入代码新增',
                    'No favorites yet — add a code above'),
    'fav_added': _s('已收藏', '已收藏', 'Added to favorites'),
    'fav_dup': _s('已經收藏過', '已经收藏过', 'Already favorited'),
    'fav_removed': _s('已刪除', '已删除', 'Removed'),
    'fav_no_sel': _s('冇選中任何行', '没有选中任何行', 'No rows selected'),
    'fav_bad_code': _s('❌ 搵唔到呢個代碼（試完整 code 或由候選揀）',
                       '❌ 找不到这个代码（试完整 code 或由候选选）',
                       '❌ Code not found (try full code or pick from suggestions)'),
    'fav_count': _s('收藏', '收藏', 'Favorites'),
    'fav_showing': _s('顯示', '显示', 'Showing'),
    # ── Page 8 指標管理（indicators_page / gateway.indicators；指標名 BOLL/ATR/MACD/OB/FVG/VOB 語言中立唔入 i18n）──
    'nav_indicators': _s('指標管理', '指标管理', 'Indicators'),
    'page_indicators_title': _s('指標管理', '指标管理', 'Indicator Management'),
    'ind_show_label': _s('指標顯示：', '指标显示：', 'Indicators:'),
    'ind_add': _s('＋ 新增指標', '＋ 新增指标', '+ Add indicator'),
    'ind_save': _s('✓ 套用修改', '✓ 套用修改', '✓ Apply changes'),
    'ind_remove': _s('🗑 移除所選', '🗑 移除所选', '🗑 Remove selected'),
    'ind_position': _s('位置', '位置', 'Position'),
    'ind_params': _s('參數', '参数', 'Parameters'),
    'ind_pos_main': _s('主圖', '主图', 'Main chart'),
    'ind_pos_sub': _s('副圖', '副图', 'Sub chart'),
    'ind_head_enabled': _s('顯示', '显示', 'Shown'),
    'ind_head_name': _s('指標', '指标', 'Indicator'),
    'ind_head_position': _s('位置', '位置', 'Position'),
    'ind_head_params': _s('參數', '参数', 'Parameters'),
    'ind_added': _s('已新增指標', '已新增指标', 'Indicator added'),
    'ind_saved': _s('已儲存修改', '已储存修改', 'Changes saved'),
    'ind_removed': _s('已移除指標', '已移除指标', 'Indicator removed'),
    'ind_no_sel': _s('冇選中任何行', '没有选中任何行', 'No rows selected'),
    'ind_bad_def': _s('❌ 未知指標類型', '❌ 未知指标类型', '❌ Unknown indicator type'),
    'ind_limit': _s('❌ 已達指標上限（最多 6 個；副圖最多 4 個 panel）',
                    '❌ 已达指标上限（最多 6 个；副图最多 4 个 panel）',
                    '❌ Indicator limit reached (max 6; max 4 sub panels)'),
    'ind_empty': _s('暫無指標 — 上方揀類型新增', '暂无指标 — 上方选类型新增',
                    'No indicators — add one above'),
    'ind_count': _s('指標', '指标', 'Indicators'),
    'ind_showing': _s('顯示中', '显示中', 'shown'),
    'ind_p_period': _s('計算週期', '计算周期', 'Period'),
    'ind_p_dev': _s('標準差倍數', '标准差倍数', 'Std dev'),
    'ind_p_fast': _s('快線 EMA', '快线 EMA', 'Fast EMA'),
    'ind_p_slow': _s('慢線 EMA', '慢线 EMA', 'Slow EMA'),
    'ind_p_signal': _s('信號線', '信号线', 'Signal'),
    # ICT 區塊指標（OB / FVG / VOB）參數名（指標名 acronym 同樣語言中立）
    'ind_p_strength': _s('位移強度（×ATR）', '位移强度（×ATR）', 'Displacement (×ATR)'),
    'ind_p_confirm': _s('確認根數', '确认根数', 'Confirm bars'),
    'ind_p_max_zones': _s('最多區塊', '最多区块', 'Max zones'),
    'ind_p_min_size': _s('最小缺口（×ATR）', '最小缺口（×ATR）', 'Min gap (×ATR)'),
    'ind_p_sweep': _s('掃流動性（回看根數）', '扫流动性（回看根数）', 'Liquidity sweep lookback'),
    # ── 首頁（預設頁，home_page / modules.market_pulse）──
    'nav_home': _s('首頁', '首页', 'Home'),
    'page_home_title': _s('全球市場脈搏', '全球市场脉搏', 'Global Market Pulse'),
    'home_refresh': _s('⟳ 刷新', '⟳ 刷新', '⟳ Refresh'),
    'home_loading': _s('讀取全球指數中…', '读取全球指数中…', 'Loading global indices…'),
    'home_updated_at': _s('更新於', '更新于', 'Updated'),
    'home_grp_hk': _s('🇭🇰 港股', '🇭🇰 港股', '🇭🇰 HK'),
    'home_grp_cn': _s('🇨🇳 A股', '🇨🇳 A股', '🇨🇳 China A'),
    'home_grp_us': _s('🇺🇸 美股（ETF 代理）', '🇺🇸 美股（ETF 代理）', '🇺🇸 US (ETF proxies)'),
    'home_us_proxy_note': _s(
        '呢個 OpenD 唔支援美股指數報價（實測「暫不支援美股指數」）→ 用對應 ETF 代理：\n'
        'SPY≈標普500、QQQ≈納指100、DIA≈道瓊斯、IWM≈羅素2000、VIXY≈VIX 期貨短倉。\n'
        '走勢貼近但唔等同指數，亦唔計匯率影響 — 如實參考。',
        '这个 OpenD 不支持美股指数报价（实测「暂不支持美股指数」）→ 用对应 ETF 代理：\n'
        'SPY≈标普500、QQQ≈纳指100、DIA≈道琼斯、IWM≈罗素2000、VIXY≈VIX 期货短仓。\n'
        '走势贴近但不等同指数，也不计汇率影响 — 如实参考。',
        'This OpenD does not quote US indices (verified "not supported") → ETF proxies:\n'
        'SPY≈S&P 500, QQQ≈Nasdaq-100, DIA≈Dow, IWM≈Russell 2000, VIXY≈short VIX futures.\n'
        'Tracks closely but is not the index; no FX adjustment — treat as indicative.'),
    'home_open': _s('開', '开', 'Open'),
    'home_high': _s('高', '高', 'High'),
    'home_low': _s('低', '低', 'Low'),
    'home_prev': _s('前收', '前收', 'Prev'),
    'home_turnover': _s('成交', '成交', 'Turnover'),
    'idx_hsi': _s('恒生指數', '恒生指数', 'Hang Seng Index'),
    'idx_hscei': _s('國企指數', '国企指数', 'HSCEI'),
    'idx_hktech': _s('恒生科技', '恒生科技', 'Hang Seng Tech'),
    'idx_sh': _s('上證指數', '上证指数', 'SSE Composite'),
    'idx_sz': _s('深成指', '深成指', 'SZSE Component'),
    'idx_csi300': _s('滬深300', '沪深300', 'CSI 300'),
    'idx_cyb': _s('創業板指', '创业板指', 'ChiNext'),
    'idx_kc50': _s('科創50', '科创50', 'STAR 50'),
    'idx_spx': _s('標普500 (SPY代理)', '标普500 (SPY代理)', 'S&P 500 (SPY proxy)'),
    'idx_ndx': _s('納指100 (QQQ代理)', '纳指100 (QQQ代理)', 'Nasdaq-100 (QQQ proxy)'),
    'idx_dji': _s('道瓊斯 (DIA代理)', '道琼斯 (DIA代理)', 'Dow Jones (DIA proxy)'),
    'idx_rut': _s('羅素2000 (IWM代理)', '罗素2000 (IWM代理)', 'Russell 2000 (IWM proxy)'),
    'idx_vix': _s('VIX 波動 (VIXY代理)', 'VIX 波动 (VIXY代理)', 'VIX vol (VIXY proxy)'),
    'nav_kline': _s('K綫測試', 'K线测试', 'K-Line Test'),
    'nav_fulltest': _s('全功能測試', '全功能测试', 'Full Test Matrix'),
    'nav_connection': _s('連綫測試', '连线测试', 'Connection Test'),
    'nav_popup': _s('彈出視窗', '弹出窗口', 'Pop out window'),   # nav 右鍵：該頁搬入獨立視窗
    'language_label': _s('語言', '语言', 'Language'),
    # theme toggle 顯示「目標模式」（撳落去會切去邊個）
    'theme_to_light': _s('☀️ 淺色模式', '☀️ 浅色模式', '☀️ Light Mode'),
    'theme_to_dark': _s('🌙 暗色模式', '🌙 深色模式', '🌙 Dark Mode'),

    # ── Page 1：K綫測試（placeholder → ticket #03 嵌入 gui_kline）──
    'page_kline_title': _s('K綫測試', 'K线测试', 'K-Line Test'),
    'page_kline_body': _s(
        '呢頁會嵌入 K 綫測試（gateway/pages/gui_kline.py）：模糊搜尋標的 + get/stream K 線圖表 + i18n + FETCH。\n目前係 placeholder — ticket #03 實裝。',
        '本页会嵌入 K 线测试（gateway/pages/gui_kline.py）：模糊搜索标的 + get/stream K 线图 + i18n + FETCH。\n目前是占位页 — ticket #03 实装。',
        'This page will embed the K-Line Test (gateway/pages/gui_kline.py): fuzzy symbol search +\nget/stream chart + i18n + FETCH. Currently a placeholder — implemented in ticket #03.'),

    # ── Page 2：全功能測試（placeholder → ticket #04 嵌入 gui_fulltest）──
    'page_fulltest_title': _s('全功能測試', '全功能测试', 'Full Test Matrix'),
    'page_fulltest_body': _s(
        '呢頁會嵌入綜合測試矩陣（gateway/pages/gui_fulltest.py）：18 rows futu/ib × get/stream、Run All 全並發、Stop 清理。\n目前係 placeholder — ticket #04 實裝。',
        '本页会嵌入综合测试矩阵（gateway/pages/gui_fulltest.py）：18 rows futu/ib × get/stream、Run All 全并发、Stop 清理。\n目前是占位页 — ticket #04 实装。',
        'This page will embed the Full Test Matrix (gateway/pages/gui_fulltest.py): 18 rows futu/ib x\nget/stream, fully-parallel Run All, Stop cleanup. Currently a placeholder — implemented in ticket #04.'),

    # ── Page 3：連綫測試（ticket #05 config editor + probes）──
    'page_connection_title': _s('連綫測試', '连线测试', 'Connection Test'),
    'page_connection_body': _s(
        '編輯 config.json 參數（保留 JSON 結構與其他欄位）+ FUTU OpenD / IB Gateway 連綫測試按鈕（狀態及速度）。',
        '编辑 config.json 参数（保留 JSON 结构与其他字段）+ Futu OpenD / IB Gateway 连线测试按钮（状态及速度）。',
        'Edit config.json parameters (preserving JSON structure and other fields) +\nFutu OpenD / IB Gateway connection test buttons (status and speed).'),

    # ── Page 3：config editor ──
    'conn_cfg_title': _s('config.json 參數', 'config.json 参数', 'Config Parameters'),
    'conn_futu_group': _s('富途 Futu OpenD', '富途 Futu OpenD', 'Futu OpenD'),
    'conn_ib_group': _s('IB Gateway（TWS）', 'IB Gateway（TWS）', 'IB Gateway (TWS)'),
    'conn_host': _s('Host', 'Host', 'Host'),
    'conn_port': _s('Port', 'Port', 'Port'),
    'conn_kline_num': _s('K 線數量 (kline_num)', 'K线数量 (kline_num)', 'K-line count (kline_num)'),
    'conn_save': _s('保存 config.json', '保存 config.json', 'Save config.json'),
    'conn_saved_ok': _s(
        '✅ 已保存（JSON 結構與其他欄位原封不動）',
        '✅ 已保存（JSON 结构与其他字段原封不动）',
        '✅ Saved (JSON structure and other fields untouched)'),
    'conn_save_err': _s('❌ 保存失敗：{err}', '❌ 保存失败：{err}', '❌ Save failed: {err}'),
    'conn_int_err': _s(
        'port / kline_num 必須係整數',
        'port / kline_num 必须是整数',
        'port / kline_num must be integers'),

    # ── Page 3：probes ──
    'conn_probe_title': _s('連線狀態及速度測試', '连接状态及速度测试', 'Connection Status & Speed Test'),
    'conn_probe_futu_btn': _s(
        'FUTU OpenD 連綫測試', 'FUTU OpenD 连线测试', 'Test Futu OpenD Connection'),
    'conn_probe_ib_btn': _s(
        'IB Gateway 連綫測試（clientId=98）', 'IB Gateway 连线测试（clientId=98）',
        'Test IB Gateway Connection (clientId=98)'),
    'conn_testing': _s('⏳ 測試中…', '⏳ 测试中…', 'Testing…'),
    'conn_futu_ok': _s(
        '✅ FUTU OpenD 連線成功\n   • 連線時間：{connect_ms} ms\n   • get_global_state RTT：{rtt_ms} ms',
        '✅ Futu OpenD 连接成功\n   • 连接时间：{connect_ms} ms\n   • get_global_state RTT：{rtt_ms} ms',
        '✅ Futu OpenD connected\n   • Connect time: {connect_ms} ms\n   • get_global_state RTT: {rtt_ms} ms'),
    'conn_futu_fail': _s(
        '❌ FUTU OpenD 連線失敗：{reason}', '❌ Futu OpenD 连接失败：{reason}',
        '❌ Futu OpenD connection failed: {reason}'),
    'conn_ib_ok': _s(
        '✅ IB Gateway 連線成功（獨立 clientId=98，與主 session 唔衝突）\n   • handshake 時間：{connect_ms} ms\n   • server-time RTT (reqCurrentTime)：{rtt_ms} ms',
        '✅ IB Gateway 连接成功（独立 clientId=98，与主 session 不冲突）\n   • handshake 时间：{connect_ms} ms\n   • server-time RTT (reqCurrentTime)：{rtt_ms} ms',
        '✅ IB Gateway connected (independent clientId=98, no conflict with main session)\n   • Handshake time: {connect_ms} ms\n   • Server-time RTT (reqCurrentTime): {rtt_ms} ms'),
    'conn_ib_fail': _s(
        '❌ IB Gateway 連線失敗：{reason}', '❌ IB Gateway 连接失败：{reason}',
        '❌ IB Gateway connection failed: {reason}'),
    # probe 失敗原因（如實分類，唔 fake success）
    'conn_reason_refused': _s(
        'port 冇開或服務未啟動（Connection refused）',
        '端口未开或服务未启动（Connection refused）',
        'Port not open or service not running (connection refused)'),
    'conn_reason_timeout': _s(
        'timeout — host 不可達或服務無回應',
        '超时 — host 不可达或服务无响应',
        'Timeout — host unreachable or no response from service'),

    # ── Page 4：FUTU 交易（futu_trade_page）──
    'nav_futu_trade': _s('FUTU 交易', 'FUTU 交易', 'Futu Trade'),
    'page_futu_trade_title': _s('FUTU 交易管理', 'FUTU 交易管理', 'Futu Trade Management'),
    'page_futu_trade_body': _s(
        '富途 OpenD 交易：左欄今日訂單 + 持倉資金，右欄連線帳戶（模擬/實盤切換、只顯示 ACTIVE）+ 下單（買入/賣出）。'
        '所有資料跟隨環境切換，唔混模擬同實盤；REAL 帳戶操作彈確認框。',
        '富途 OpenD 交易：左栏今日订单 + 持仓资金，右栏连线账户（模拟/实盘切换、只显示 ACTIVE）+ 下单（买入/卖出）。'
        '所有资料跟随环境切换，不混模拟和实盘；REAL 账户操作弹确认框。',
        'Futu OpenD trading: left = today\'s orders + positions/funds, right = connection & accounts\n'
        '(SIMULATE/REAL toggle, ACTIVE accounts only) + order form (Buy/Sell). All data follows the selected\n'
        'environment — sim and real are never mixed; REAL-account actions require confirmation.'),

    # Page 4：連線同帳戶
    'trade_conn_title': _s('連線同帳戶', '连接和账户', 'Connection & Accounts'),
    'trade_market': _s('市場過濾', '市场过滤', 'Market filter'),
    'trade_conn_ok': _s(
        '✅ 已連線（{ms} ms）— {n} 個帳戶',
        '✅ 已连接（{ms} ms）— {n} 个账户',
        '✅ Connected ({ms} ms) — {n} accounts'),
    'trade_conn_fail': _s('❌ 連線失敗：{err}', '❌ 连接失败：{err}', '❌ Connection failed: {err}'),
    'trade_conn_retry': _s('（15 秒後自動重試…）', '（15 秒后自动重试…）', '(auto-retry in 15s…)'),
    'trade_env_sim': _s('模擬盤', '模拟盘', 'SIMULATE'),
    'trade_env_real': _s('實盤', '实盘', 'REAL'),
    'trade_acc_selected': _s(
        '當前帳戶：{acc_id}（{env} / {type}）',
        '当前账户：{acc_id}（{env} / {type}）',
        'Current account: {acc_id} ({env} / {type})'),
    'trade_no_account': _s(
        '未選帳戶 — 喺右欄帳戶表撳一行',
        '未选账户 — 在右栏账户表点一行',
        'No account selected — click a row in the accounts table (right)'),
    'trade_no_active_acc': _s(
        '冇 ACTIVE 帳戶（此環境 / 市場）— 切換模擬/實盤或市場過濾',
        '无 ACTIVE 账户（此环境 / 市场）— 切换模拟/实盘或市场过滤',
        'No ACTIVE account (this env / market) — switch SIMULATE/REAL or the market filter'),
    # Page 4：下單（買入/賣出兩鍵；方向由按鍵決定）
    'trade_order_title': _s('下單', '下单', 'Place Order'),
    'trade_code': _s('代碼 (code)', '代码 (code)', 'Code'),
    'trade_otype': _s('訂單類型', '订单类型', 'Order type'),
    'trade_price': _s('價格', '价格', 'Price'),
    'trade_qty': _s('數量', '数量', 'Quantity'),
    'trade_tif': _s('TIF', 'TIF', 'TIF'),
    'trade_buy_btn': _s('買入 BUY', '买入 BUY', 'BUY'),
    'trade_sell_btn': _s('賣出 SELL', '卖出 SELL', 'SELL'),
    'trade_busy': _s('⏳ 處理中…', '⏳ 处理中…', 'Processing…'),
    'trade_need_account': _s(
        '請先連線並選擇帳戶',
        '请先连接并选择账户',
        'Please connect and select an account first'),

    # 解鎖狀態（實盤）— 只 REAL env 顯示；新版 OpenD 禁止 SDK 解鎖，必須喺 OpenD GUI 手動解鎖。
    # 冇查詢 API → 狀態由落單/撤單成敗被動推斷（unknown / unlocked / locked）。
    'trade_unlock_status': _s('交易解鎖狀態：', '交易解锁状态：', 'Trade unlock status: '),
    'trade_unlock_state_unknown': _s(
        '未知 — 落單/撤單時自動偵測（解鎖請喺 OpenD GUI 操作）',
        '未知 — 下单/撤单时自动检测（解锁请在 OpenD GUI 操作）',
        'unknown — detected automatically on order/cancel (unlock in the OpenD GUI)'),
    'trade_unlock_state_unlocked': _s('✅ 已解鎖', '✅ 已解锁', '✅ Unlocked'),
    'trade_unlock_state_locked': _s(
        '❌ 未解鎖 — 請喺 OpenD GUI 點擊「解鎖交易」輸入交易密碼，然後重新落單',
        '❌ 未解锁 — 请在 OpenD GUI 点击"解锁交易"输入交易密码，然后重新下单',
        '❌ Locked — click "Unlock Trading" in the OpenD GUI, enter the trade password, then place again'),
    'trade_unlock_needed': _s(
        '交易未解鎖 — 請先喺 OpenD GUI 點擊「解鎖交易」輸入交易密碼，再重新落單',
        '交易未解锁 — 请先在 OpenD GUI 点击"解锁交易"输入交易密码，再重新下单',
        'Trade not unlocked — click "Unlock Trading" in the OpenD GUI and enter the trade password, then place again'),
    'trade_invalid_form': _s(
        '❌ 表單有誤：code 必填、qty 正整數、非 MARKET 單 price > 0',
        '❌ 表单有误：code 必填、qty 正整数、非 MARKET 单 price > 0',
        '❌ Invalid form: code required, qty positive int, price > 0 for non-MARKET orders'),
    'trade_invalid_code': _s(
        '❌ 代碼無效 — 請從候選揀完整代碼（例如 HK.00700 / US.NVDA / HK.HSImain），唔好直接用中文名稱落單',
        '❌ 代码无效 — 请从候选选完整代码（例如 HK.00700 / US.NVDA / HK.HSImain），不要直接用中文名称下单',
        '❌ Invalid code — pick a full symbol from the suggestions (e.g. HK.00700 / US.NVDA / HK.HSImain); do not order by Chinese name'),
    'trade_place_ok': _s(
        '✅ 下單成功 — order_id={id}',
        '✅ 下单成功 — order_id={id}',
        '✅ Order placed — order_id={id}'),
    'trade_place_fail': _s('❌ 下單失敗：{err}', '❌ 下单失败：{err}', '❌ Place order failed: {err}'),

    # Page 4：今日訂單
    'trade_orders_title': _s('今日訂單', '今日订单', "Today's Orders"),
    'trade_refresh': _s('刷新', '刷新', 'Refresh'),
    'trade_cancel_sel': _s('撤選定單', '撤选定单', 'Cancel Selected'),
    'trade_cancel_all': _s('全數撤單', '全部撤单', 'Cancel All'),
    'trade_no_selection': _s(
        '請先喺訂單表撳一行',
        '请先在订单表点一行',
        'Please select an order row first'),
    'trade_cancel_ok': _s(
        '✅ 已撤單（order_id={id}）',
        '✅ 已撤单（order_id={id}）',
        '✅ Order cancelled (order_id={id})'),
    'trade_cancel_all_ok': _s('✅ 已全部撤單', '✅ 已全部撤单', '✅ All orders cancelled'),
    'trade_op_fail': _s('❌ {err}', '❌ {err}', '❌ {err}'),

    # Page 4：持倉同帳戶資金
    'trade_pos_title': _s('持倉同帳戶資金', '持仓和账户资金', 'Positions & Account Funds'),
    'trade_accinfo_lbl': _s(
        '帳戶資金：{kv}',
        '账户资金：{kv}',
        'Account funds: {kv}'),

    # Page 4：表格欄名（三語 — 欄位 key 對住 SDK 回傳欄名，顯示用呢度）
    'col_acc_id': _s('帳戶 ID', '账户 ID', 'Account ID'),
    'col_trd_env': _s('環境', '环境', 'Env'),
    'col_acc_type': _s('類型', '类型', 'Type'),
    'col_trdmarket_auth': _s('授權市場', '授权市场', 'Markets'),
    'col_acc_status': _s('狀態', '状态', 'Status'),
    'col_order_id': _s('訂單號', '订单号', 'Order ID'),
    'col_code': _s('代碼', '代码', 'Code'),
    'col_stock_name': _s('名稱', '名称', 'Name'),
    'col_trd_side': _s('方向', '方向', 'Side'),
    'col_order_type': _s('訂單類型', '订单类型', 'Order type'),
    'col_qty': _s('數量', '数量', 'Qty'),
    'col_dealt_qty': _s('成交數量', '成交数量', 'Filled'),
    'col_price': _s('價格', '价格', 'Price'),
    'col_dealt_avg_price': _s('成交均價', '成交均价', 'Avg price'),
    'col_order_status': _s('狀態', '状态', 'Status'),
    'col_create_time': _s('時間', '时间', 'Time'),
    'col_position_market': _s('市場', '市场', 'Market'),
    'col_can_sell_qty': _s('可賣數量', '可卖数量', 'Sellable'),
    'col_cost_price': _s('成本價', '成本价', 'Cost'),
    'col_market_val': _s('市值', '市值', 'Mkt val'),
    'col_pl_val': _s('盈虧', '盈亏', 'P&L'),
    'col_pl_ratio': _s('盈虧 %', '盈亏 %', 'P&L %'),
    'col_currency': _s('幣種', '币种', 'Currency'),

    # Page 4：TIF 顯示（三語；提交仍用 enum 名 — combo userData 帶 enum）
    'tif_DAY': _s('當日有效 (DAY)', '当日有效 (DAY)', 'Day (DAY)'),
    'tif_GTC': _s('取消前有效 (GTC)', '取消前有效 (GTC)', 'Good-til-cancelled (GTC)'),
    'tif_IOC': _s('立即成交否則取消 (IOC)', '立即成交否则取消 (IOC)', 'Immediate-or-cancel (IOC)'),

    # Page 4：代碼 hint 帶行情（最新價 + 每手 — 預填 price/qty 同步顯示）
    'trade_code_quote': _s(
        '{hint} | 最新 {price} | 每手 {lot}',
        '{hint} | 最新 {price} | 每手 {lot}',
        '{hint} | last {price} | lot {lot}'),

    # Page 4：全流市價訂閱生效中（K_1M push active — 價冇郁都照顯示，唔會當冇串流）
    'trade_stream_on': _s(' | 串流中 ✅', ' | 流式中 ✅', ' | streaming ✅'),

    # Page 4：REAL 確認框
    'trade_real_confirm_title': _s(
        '⚠️ REAL 帳戶操作確認',
        '⚠️ REAL 账户操作确认',
        '⚠️ REAL Account Confirmation'),
    'trade_confirm_place': _s(
        '即將喺 REAL 帳戶下單：\n{code} {side}\n數量 = {qty}，價格 = {price}（{otype} / {tif}）\n\n確認執行？',
        '即将在 REAL 账户下单：\n{code} {side}\n数量 = {qty}，价格 = {price}（{otype} / {tif}）\n\n确认执行？',
        'About to place an order on a REAL account:\n{code} {side}\nQty = {qty}, Price = {price} ({otype} / {tif})\n\nConfirm?'),
    'trade_confirm_cancel': _s(
        '即將喺 REAL 帳戶撤單 order_id={id}，確認？',
        '即将在 REAL 账户撤单 order_id={id}，确认？',
        'About to cancel order {id} on a REAL account. Confirm?'),
    'trade_confirm_cancel_all': _s(
        '即將喺 REAL 帳戶全數撤單（{acc_id}），確認？',
        '即将在 REAL 账户全部撤单（{acc_id}），确认？',
        'About to cancel ALL orders on REAL account {acc_id}. Confirm?'),
}


def t(key, lang=DEFAULT_LANG):
    """取字串：STRINGS[key][lang]。未知 key 即刻炸（開發期 fail-fast）。"""
    entry = STRINGS.get(key)
    if entry is None:
        raise KeyError(f"i18n key not found: {key}")
    return entry.get(lang, entry[DEFAULT_LANG])


def theme_toggle_text(current_theme, lang=DEFAULT_LANG):
    """Theme toggle 按鈕文字 — 顯示目標模式（dark → 「淺色模式」）。"""
    key = 'theme_to_light' if current_theme == 'dark' else 'theme_to_dark'
    return t(key, lang)

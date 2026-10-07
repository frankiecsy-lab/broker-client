"""One Gate i18n — 單一入口：STRINGS dict + t()。

三語（zh_hk / zh_cn / en）全部係**明確字串**，唔係 opencc 轉換 —
每種語言嘅文案由人手寫定，避免自動轉換出錯嘅專業術語。
"""

LANGS = ('zh_hk', 'zh_cn', 'en')
DEFAULT_LANG = 'zh_hk'

# 各語言自稱（endonym）— combo 顯示用固定字串，唔跟隨 UI 語言切換
LANG_LABELS = {'zh_hk': '繁體中文', 'zh_cn': '简体中文', 'en': 'English'}


def _s(zh_hk, zh_cn, en):
    return {'zh_hk': zh_hk, 'zh_cn': zh_cn, 'en': en}


STRINGS = {
    # ── 外殼（nav / toolbar / window title）──
    'app_title': _s(
        'One Gate — 多券商 Gateway 匯合',
        'One Gate — 多券商 Gateway 汇聚',
        'One Gate — Multi-Broker Gateway Hub'),
    'nav_kline': _s('K綫測試', 'K线测试', 'K-Line Test'),
    'nav_fulltest': _s('全功能測試', '全功能测试', 'Full Test Matrix'),
    'nav_connection': _s('連綫測試', '连线测试', 'Connection Test'),
    'nav_reserved': _s('預留頁面…', '预留页面…', 'Reserved…'),
    'nav_reserved_tip': _s(
        '位置預留 — 以後再加新頁面',
        '位置预留 — 以后再加新页面',
        'Slot reserved for future pages'),
    'language_label': _s('語言', '语言', 'Language'),
    # theme toggle 顯示「目標模式」（撳落去會切去邊個）
    'theme_to_light': _s('☀️ 淺色模式', '☀️ 浅色模式', '☀️ Light Mode'),
    'theme_to_dark': _s('🌙 暗色模式', '🌙 深色模式', '🌙 Dark Mode'),

    # ── Page 1：K綫測試（placeholder → ticket #03 嵌入 gui_kline）──
    'page_kline_title': _s('K綫測試', 'K线测试', 'K-Line Test'),
    'page_kline_body': _s(
        '呢頁會嵌入 K 綫測試（test/gui_kline.py）：模糊搜尋標的 + get/stream K 線圖表 + i18n + FETCH。\n目前係 placeholder — ticket #03 實裝。',
        '本页会嵌入 K 线测试（test/gui_kline.py）：模糊搜索标的 + get/stream K 线图 + i18n + FETCH。\n目前是占位页 — ticket #03 实装。',
        'This page will embed the K-Line Test (test/gui_kline.py): fuzzy symbol search +\nget/stream chart + i18n + FETCH. Currently a placeholder — implemented in ticket #03.'),

    # ── Page 2：全功能測試（placeholder → ticket #04 嵌入 gui_fulltest）──
    'page_fulltest_title': _s('全功能測試', '全功能测试', 'Full Test Matrix'),
    'page_fulltest_body': _s(
        '呢頁會嵌入綜合測試矩陣（test/gui_fulltest.py）：18 rows futu/ib × get/stream、Run All 全並發、Stop 清理。\n目前係 placeholder — ticket #04 實裝。',
        '本页会嵌入综合测试矩阵（test/gui_fulltest.py）：18 rows futu/ib × get/stream、Run All 全并发、Stop 清理。\n目前是占位页 — ticket #04 实装。',
        'This page will embed the Full Test Matrix (test/gui_fulltest.py): 18 rows futu/ib x\nget/stream, fully-parallel Run All, Stop cleanup. Currently a placeholder — implemented in ticket #04.'),

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
        '富途 OpenD 交易：連線 + 帳戶列表、下單（place_order）、今日訂單（查詢 / 撤選定 / 全數撤）、持倉同帳戶資金。'
        '所有 SDK 調用行獨立 QThread；REAL 帳戶操作彈確認框。',
        '富途 OpenD 交易：连线 + 账户列表、下单（place_order）、今日订单（查询 / 撤选定 / 全部撤）、持仓和账户资金。'
        '所有 SDK 调用走独立 QThread；REAL 账户操作弹确认框。',
        'Futu OpenD trading: connect + account list, place orders (place_order), today\'s orders\n'
        '(query / cancel selected / cancel all), positions and account funds. All SDK calls run on a dedicated\n'
        'QThread; REAL-account actions require confirmation.'),

    # Page 4：連線同帳戶
    'trade_conn_title': _s('連線同帳戶', '连接和账户', 'Connection & Accounts'),
    'trade_market': _s('市場過濾', '市场过滤', 'Market filter'),
    'trade_connect': _s('連線 OpenD', '连线 OpenD', 'Connect OpenD'),
    'trade_disconnect': _s('斷開', '断开', 'Disconnect'),
    'trade_conn_ok': _s(
        '✅ 已連線（{ms} ms）— {n} 個帳戶',
        '✅ 已连接（{ms} ms）— {n} 个账户',
        '✅ Connected ({ms} ms) — {n} accounts'),
    'trade_conn_fail': _s('❌ 連線失敗：{err}', '❌ 连接失败：{err}', '❌ Connection failed: {err}'),
    'trade_acc_selected': _s(
        '當前帳戶：{acc_id}（{env} / {type}）',
        '当前账户：{acc_id}（{env} / {type}）',
        'Current account: {acc_id} ({env} / {type})'),
    'trade_no_account': _s(
        '未選帳戶 — 喺上面表格撳一行',
        '未选账户 — 在上方表格点一行',
        'No account selected — click a row above'),

    # Page 4：下單
    'trade_order_title': _s('下單', '下单', 'Place Order'),
    'trade_code': _s('代碼 (code)', '代码 (code)', 'Code'),
    'trade_side': _s('方向', '方向', 'Side'),
    'trade_otype': _s('訂單類型', '订单类型', 'Order type'),
    'trade_price': _s('價格', '价格', 'Price'),
    'trade_qty': _s('數量', '数量', 'Quantity'),
    'trade_tif': _s('TIF', 'TIF', 'TIF'),
    'trade_unlock_pwd': _s('交易解鎖密碼', '交易解锁密码', 'Trade unlock password'),
    'trade_unlock_btn': _s('解鎖交易', '解锁交易', 'Unlock Trade'),
    'trade_place_btn': _s('下單', '下单', 'Place Order'),
    'trade_busy': _s('⏳ 處理中…', '⏳ 处理中…', 'Processing…'),
    'trade_unlock_ok': _s(
        '✅ 已解鎖（本連線有效）',
        '✅ 已解锁（本连接有效）',
        '✅ Unlocked (valid for this connection)'),
    'trade_unlock_fail': _s('❌ 解鎖失敗：{err}', '❌ 解锁失败：{err}', '❌ Unlock failed: {err}'),
    'trade_need_account': _s(
        '請先連線並選擇帳戶',
        '请先连接并选择账户',
        'Please connect and select an account first'),
    'trade_need_unlock': _s(
        'REAL 帳戶下單要先解鎖交易',
        'REAL 账户下单要先解锁交易',
        'Unlock trade required before placing orders on a REAL account'),
    'trade_invalid_form': _s(
        '❌ 表單有誤：code 必填、qty 正整數、非 MARKET 單 price > 0',
        '❌ 表单有误：code 必填、qty 正整数、非 MARKET 单 price > 0',
        '❌ Invalid form: code required, qty positive int, price > 0 for non-MARKET orders'),
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

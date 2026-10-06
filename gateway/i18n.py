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

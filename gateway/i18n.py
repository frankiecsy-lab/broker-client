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

    # ── Page 3：連綫測試（placeholder → ticket #05 config editor + probes）──
    'page_connection_title': _s('連綫測試', '连线测试', 'Connection Test'),
    'page_connection_body': _s(
        '呢頁會提供 config.json 參數編輯（保留 JSON 結構）+ FUTU OpenD / IB Gateway 連綫測試按鈕（狀態及速度）。\n目前係 placeholder — ticket #05 實裝。',
        '本页会提供 config.json 参数编辑（保留 JSON 结构）+ FUTU OpenD / IB Gateway 连线测试按钮（状态及速度）。\n目前是占位页 — ticket #05 实装。',
        'This page will provide a config.json parameter editor (preserving JSON structure) +\nFUTU OpenD / IB Gateway connection test buttons (status and speed). Currently a placeholder — implemented in ticket #05.'),
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

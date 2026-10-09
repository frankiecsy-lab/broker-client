"""使用說明備注的樣式守門（ticket #35）：頁級 `role="pagebody"`、區塊級 `role="usagehint"`。

只檢查會靜默失敗的位置：
1. 各頁 `_STAMP` 的 role 標記在真實 `.ui` 載入後確實落到目標控件
   （objectName 寫錯 → QSS 無聲失效，頁面照常運行）。
2. 淡色小字樣式全站只有一份定義（theme.note_qss），且在設了頁級 QSS 的頁之下仍然生效
   （Qt 的層疊規則：近處 stylesheet 的規則勝過 app 級，故必須實測生效）。
3. 每個備注控件都有接到文案來源（`_TEXT` 或 code 覆寫），且三語均非空
   （漏接 → 標籤永遠空白，界面照常運行，無任何報錯）。
4. 交易帳戶頁的券商欄 slot（`ta_grid_*`）與 `modules.registry.BROKERS` 一一對應
   （「按券商數量加欄位」由 `.ui` 宣告，缺 slot 只會在接上新券商時才炸）。

Run: python .scratch/t_usage_hint_style.py
"""
import importlib
import os
import string
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

FAILURES = []
TOTAL = 0


def check(name, ok, detail=''):
    global TOTAL
    TOTAL += 1
    print(('  ✅ ' if ok else '  ❌ ') + name + (f'  [{detail}]' if not ok and detail else ''))
    if not ok:
        FAILURES.append(name)


# 頁 → (.ui 檔名, page module, {objectName: 應帶的 role})
#   pagebody = 頁級使用說明；usagehint = 區塊級提示
PAGES = [
    ('回測頁', 'backtest_page.ui', 'gateway.pages.backtest_page',
     {'bt_note': 'usagehint', 'bt_chart_hint': 'usagehint',
      'bt_page_note': 'pagebody', 'bt_trades_note': 'usagehint', 'bt_ig_note': 'usagehint'}),
    ('量化交易頁', 'quant_page.ui', 'gateway.pages.quant_page',
     {'qnt_hint': 'usagehint', 'qnt_risk_zero_hint': 'usagehint',
      'qnt_confirm_hint': 'usagehint', 'qnt_pos_note': 'usagehint',
      'qnt_orders_note': 'usagehint'}),
    ('行情頁', 'quotes_page.ui', 'gateway.pages.quotes_page',
     {'quotes_page_note': 'pagebody'}),
    ('K 線頁', 'kline_page.ui', 'gateway.pages.kline_page',
     {'kline_page_note': 'pagebody'}),
    ('指標頁', 'indicators_page.ui', 'gateway.pages.indicators_page',
     {'ind_page_note': 'pagebody', 'ind_table_note': 'usagehint'}),
    ('策略頁', 'strategies_page.ui', 'gateway.pages.strategies_page',
     {'str_page_note': 'pagebody', 'str_score_note': 'usagehint'}),
    ('連接頁', 'connection_page.ui', 'gateway.pages.connection_page',
     {'body_lbl': 'pagebody'}),
    ('期貨交易頁', 'futu_trade_page.ui', 'gateway.pages.futu_trade_page',
     {'body_lbl': 'pagebody'}),
    ('收藏頁', 'favorites_page.ui', 'gateway.pages.favorites_page',
     {'fav_page_note': 'pagebody'}),
    ('標的列表頁', 'symbol_list_page.ui', 'gateway.pages.symbol_list_page',
     {'sl_page_note': 'pagebody'}),
    ('全測試頁', 'fulltest_page.ui', 'gateway.pages.fulltest_page',
     {'ft_page_note': 'pagebody'}),
    ('首頁', 'home_page.ui', 'gateway.pages.home_page',
     {'home_page_note': 'pagebody'}),
    ('交易帳戶頁', 'accounts_page.ui', 'gateway.pages.accounts_page',
     {'accounts_page_note': 'pagebody', 'ta_usage': 'usagehint'}),
]
# 只要求「舊 selector 已移除」的頁（指標頁的備注在運行期建立，斷言在 e2e_gui_indicators）
OLD_SELECTORS = {
    'gateway.pages.backtest_page': ['QLabel#bt_note', 'QLabel#bt_chart_hint'],
    'gateway.pages.quant_page': ['og="qtnote"'],
    'gateway.pages.indicators_page': ['og="indnote"'],
}


# 文案由 code 覆寫嘅備注（唔經 `_TEXT` 表）：例如未執行時先顯示、有圖時收埋
CODE_TEXT = {'gateway.pages.backtest_page': {'bt_chart_hint': 'bt_chart_empty'}}


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    import gateway.theme as theme
    theme.apply_theme('dark')
    muted = theme.THEMES['dark']['muted']

    print('── 1. 共用樣式只有一份定義 ──')
    qss = theme.qss('dark')
    for role in ('usagehint', 'pagebody'):
        check(f'theme.py 定義 QLabel[role="{role}"]（淡色，全站唯一）',
              f'QLabel[role="{role}"]' in qss and muted in qss)
    check('note_qss() 片段齊兩條 role 規則（設了頁級 QSS 嘅頁靠呢段先食到）',
          all(f'QLabel[role="{r}"]' in theme.note_qss('dark') for r in ('usagehint', 'pagebody')))

    # Qt 層疊：頁級 stylesheet 勝過 app 級 → 頁級 QSS 有裸 QWidget/QLabel 規則嘅頁，
    # 唔 append note_qss() 就會無聲失效（K 線頁曾被 gui_kline 嘅裸 QWidget 規則蓋走）
    import inspect
    # 呢兩頁嘅頁級 QSS 含裸 QLabel 規則 → 一定要 append note_qss() 先食到共用樣式
    for mod_name in ('gateway.pages.kline_page', 'gateway.pages.fulltest_page'):
        src = inspect.getsource(importlib.import_module(mod_name))
        check(f'{mod_name.split(".")[-1]}：頁級 QSS append 咗 note_qss()', 'note_qss(' in src)
    for mod_name, needles in OLD_SELECTORS.items():
        mod = importlib.import_module(mod_name)
        src = mod._PAGE_QSS
        check(f'{mod_name.split(".")[-1]}：頁級 QSS 無殘留舊 selector',
              not any(n in src for n in needles),
              '殘留：' + ', '.join(n for n in needles if n in src))

    print('── 2. `.ui` 載入後 role 標記確實落到控件 ──')
    # 用專案 own loader：promoted widget（ChartCell / KlineChart…）要靠 _CUSTOM registry 先起返
    from gateway.ui.loader import load_ui
    for label, ui_name, mod_name, roles in PAGES:
        mod = importlib.import_module(mod_name)
        try:
            root = load_ui(ui_name.replace('.ui', ''), QWidget)
        except Exception as e:
            root = None
            check(f'{label}：`.ui` 可載入', False, str(e))
        if root is None:
            continue
        from gateway.ui.bind import stamp
        missing = stamp(root, mod._STAMP)
        check(f'{label}：_STAMP 全部 objectName 都存在于 `.ui`', not missing,
              '找不到：' + ', '.join(missing))
        for obj, want in roles.items():
            w = root.findChild(QWidget, obj)
            got = w.property('role') if w is not None else None
            check(f'{label}：{obj} 帶 role="{want}"',
                  w is not None and str(got) == want, f'property={got!r}')
        root.deleteLater()

    print('── 3. 頁級 QSS 之下共用樣式仍然生效（層疊實測）──')
    pal = dict(theme.THEMES['dark'])
    from gateway.pages import gui_kline as gk   # 部分頁另有語義色（up/down）placeholder
    pal.update(up=gk.C_UP, down=gk.C_DOWN)
    for label, ui_name, mod_name, roles in PAGES:
        mod = importlib.import_module(mod_name)
        # 頁級 QSS 有兩種命名（_PAGE_QSS / _QSS_TPL）；K 線頁屬運行期重建 → 由 e2e_gui_indicators 實測
        page_qss = getattr(mod, '_PAGE_QSS', None) or getattr(mod, '_QSS_TPL', None)
        if page_qss is None:
            continue
        host = QWidget()
        host.setObjectName(ui_name.replace('.ui', ''))
        # 有頁用 str、有頁用 Template → 兩種都直接食佢嘅原文；
        # 頁自己 append 咗 note_qss() 嘅（K 線／全測試）照做，先係佢哋實際落到 Qt 嘅 QSS
        qss_text = string.Template(getattr(page_qss, 'template', page_qss)).safe_substitute(pal)
        if 'note_qss(' in inspect.getsource(mod):
            qss_text += theme.note_qss('dark')
        host.setStyleSheet(qss_text)
        plain = QLabel('plain', host)
        plain.setObjectName('probe_plain')
        plain.ensurePolished()
        for role in sorted(set(roles.values())):
            note = QLabel('note', host)
            note.setObjectName(f'probe_{role}')
            note.setProperty('role', role)
            note.ensurePolished()
            got = note.palette().color(QPalette.WindowText).name().upper()
            base = plain.palette().color(QPalette.WindowText).name().upper()
            check(f'{label}：role="{role}" 解析為 {muted}（未被頁級 QSS 蓋走）',
                  got == muted.upper(), f'實測 {got}，同頁內普通 QLabel {base} 對照')

    print('── 4. 備注文案：`_TEXT` 有接、三語都有內容（漏接 → 空白無聲）──')
    from gateway.i18n import LANGS, t
    for label, ui_name, mod_name, roles in PAGES:
        mod = importlib.import_module(mod_name)
        text_map = getattr(mod, '_TEXT', {})
        code_map = CODE_TEXT.get(mod_name, {})
        for obj in roles:
            if obj in code_map:
                check(f'{label}：{obj} 唔經 _TEXT（文案由 code 覆寫）', obj not in text_map)
                key = code_map[obj]
            else:
                key = text_map.get(obj)
                check(f'{label}：{obj} 喺 _TEXT 有接 i18n key', bool(key), '未接 → 常空')
            if not key:
                continue
            for lang in LANGS:
                try:
                    v = str(t(key, lang)).strip()
                except Exception as e:
                    check(f'{label}：{obj} → {key} [{lang}]', False, str(e))
                    continue
                check(f'{label}：{obj} → {key} [{lang}] 非空', len(v) > 0)

    print('── 5. 券商欄位由結構保證：`.ui` 宣告的 ta_grid_* == registry.BROKERS ──')
    # 「按券商數量加欄位」不是運行期動態生成版面，而是 `.ui` 按 registry 逐個宣告 slot，
    # 缺 slot 時頁面一開即 RuntimeError（accounts_page._grid）。守門要在測試期就指出來：
    # 只靠「頁面能開」會漏掉——新增券商而忘記宣告 slot，是下一次接券商時才會踩到。
    from PySide6.QtWidgets import QGridLayout
    import modules.registry as registry
    try:
        root = load_ui('accounts_page', QWidget)
    except Exception as e:
        root = None
        check('交易帳戶頁：`.ui` 可載入', False, str(e))
    if root is not None:
        brokers = list(registry.BROKERS)
        got = {g.objectName() for g in root.findChildren(QGridLayout)
               if str(g.objectName()).startswith('ta_grid_')}
        check(f'ta_grid_* 正好等於 BROKERS {brokers}（無缺漏、無多餘）',
              got == {f'ta_grid_{b}' for b in brokers}, f'實測 {sorted(got)}')
        for b in brokers:
            # 與頁面同一條取用路徑（getattr(self, f'ta_grid_{broker}')），不是只查 XML 字串
            check(f'券商 {b}：getattr(root, "ta_grid_{b}") 可取用',
                  getattr(root, f'ta_grid_{b}', None) is not None)
        root.deleteLater()

    print()
    if FAILURES:
        print(f'USAGE_HINT_STYLE FAIL({len(FAILURES)}/{TOTAL}): ' + ' | '.join(FAILURES))
        return 1
    print(f'USAGE_HINT_STYLE PASS ({TOTAL} checks)')
    return 0


if __name__ == '__main__':
    sys.exit(main())

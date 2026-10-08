"""Ticket #07 E2E — `app.py` 外殼改由 `gateway/ui/app_shell.ui` 砌，registry 邏輯照舊。

覆蓋：`.ui` 骨架（objectName / QSS property / layout margin / slot 位置）+ nav 切換 +
子選單 + 右鍵彈出窗 reparent / 搬返 + 語言三按鈕 + theme 切換 + i18n
+ **standalone 外殼**（`gateway/ui/standalone_window.ui`：central / 頂欄 / pageSlot / 三語 / theme）。
Hermetic：`_PAGE_CLASSES` 全部換成 stub（唔起真 IB / OpenD session）。

行法：python -u .scratch/e2e_gui_shell.py   （exit 0 = pass）
"""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import QMargins, Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QLabel, QPushButton, QSpacerItem,
                               QWidget)

import gateway.app as app_mod
import gateway.theme as theme_mod
from gateway.i18n import LANGS, LANG_LABELS, LANG_SHORT, t, theme_toggle_text

FAILS = []


def check(name, cond):
    print(('  OK   ' if cond else '  FAIL ') + name)
    if not cond:
        FAILS.append(name)


def pump(times=3):
    app = QApplication.instance()
    for _ in range(times):
        app.processEvents()


class StubPage(QWidget):
    """頂真 page：外殼只食 `retranslate(lang)` 同 widget 身份。"""

    def __init__(self, key):
        super().__init__()
        self.setObjectName(f'page_{key}')
        self.page_key = key
        self.langs = []

    def retranslate(self, lang):
        self.langs.append(lang)


STUBS = {k: (lambda k=k: StubPage(k)) for k in app_mod.PAGE_KEYS}


def build_window():
    old = dict(app_mod._PAGE_CLASSES)
    app_mod._PAGE_CLASSES.update(STUBS)
    try:
        return app_mod.OneGateWindow(), old
    finally:
        app_mod._PAGE_CLASSES.update(old)   # 起完即還原（page 實例已經入面）


def main():
    qapp = QApplication.instance() or QApplication(sys.argv)
    theme_mod.apply_theme('dark')

    win, orig_classes = build_window()
    win.resize(1400, 900)
    win.show()
    pump()

    # ── 1. 骨架嚟自 .ui ──
    check('root = OneGateWindow 本身（冇嵌套 wrapper）',
          isinstance(win, app_mod.OneGateWindow)
          and win.objectName() == 'oneGateWindow'
          and win.findChild(type(win), 'oneGateWindow') is None)
    root = win.centralWidget()
    check('centralWidget 係 .ui 嗰個 oneGateRoot（唔係 centralwidget）',
          root is not None and root.objectName() == 'oneGateRoot' and type(root) is QWidget)
    check('oneGateRoot QSS property + WA_StyledBackground 已注入',
          root.property('og') == 'shell' and root.testAttribute(Qt.WA_StyledBackground))
    navbar = win.findChild(QWidget, 'navBar')
    check('navBar 存在 + og=navbar + WA_StyledBackground',
          navbar is not None and navbar.property('og') == 'navbar'
          and navbar.testAttribute(Qt.WA_StyledBackground))
    check('brandLbl 係 QLabel（文字由 i18n / Designer，唔靠 code 起）',
          isinstance(win.findChild(QLabel, 'brandLbl'), QLabel))
    check('page_stack 係 QStackedWidget（嚟自 .ui，唔係 code new）',
          win.stack is win.page_stack
          and win.page_stack.metaObject().className() == 'QStackedWidget')

    # ── 2. 排版契約（Designer 可調嘅嘢，逐項 lock 住）──
    rl = root.layout()
    check('root layout = QVBoxLayout, margins 0 / spacing 0',
          rl is not None and rl.metaObject().className() == 'QVBoxLayout'
          and rl.spacing() == 0
          and (rl.contentsMargins().left(), rl.contentsMargins().top(),
               rl.contentsMargins().right(), rl.contentsMargins().bottom()) == (0, 0, 0, 0))
    nl = navbar.layout()
    check('nav layout = QHBoxLayout, margins 16/8/16/8',
          nl is not None and nl.metaObject().className() == 'QHBoxLayout'
          and (nl.contentsMargins().left(), nl.contentsMargins().top(),
               nl.contentsMargins().right(), nl.contentsMargins().bottom()) == (16, 8, 16, 8))

    def token_at(layout, i):
        it = layout.itemAt(i)
        if it.widget() is not None:
            return f'widget:{it.widget().objectName()}'
        if it.layout() is not None:
            return f'layout:{it.layout().objectName()}'
        if isinstance(it, QSpacerItem) or it.spacerItem() is not None:
            return 'spacer'
        return 'other'

    seq = [token_at(nl, i) for i in range(nl.count())]
    check('navbar 順序：brand → gap → navSlot → menuSlot → stretch → langSlot → theme_btn',
          seq == ['widget:brandLbl', 'spacer', 'layout:navSlot', 'layout:menuSlot',
                  'spacer', 'layout:langSlot', 'widget:theme_btn'])
    check('三個 layout slot 可以當 attribute 直接用',
          all(getattr(win, s, None) is not None for s in ('navSlot', 'menuSlot', 'langSlot')))

    # ── 3. registry 填 slot（加頁 = 改 registry，唔使改 .ui）──
    for key in app_mod.NAV_DIRECT:
        b = win.nav_btns.get(key)
        check(f'nav 按鈕 {key}',
              isinstance(b, QPushButton) and b.objectName() == f'nav_{key}'
              and b.property('og') == 'navbtn' and b.isCheckable()
              and b.contextMenuPolicy() == Qt.CustomContextMenu)
    for mk, pages in app_mod.NAV_MENUS.items():
        b = win.menu_btns.get(mk)
        check(f'子選單按鈕 {mk}',
              isinstance(b, QPushButton) and b.objectName() == f'nav_menu_{mk}'
              and b.property('og') == 'navbtn' and b.menu() is not None)
        for pk in pages:
            act = win.page_actions.get(pk)
            check(f'子選單項 {pk}',
                  act is not None and act.objectName() == f'nav_{pk}'
                  and act.parent() is b.menu())
    for code in app_mod.LANGS:
        b = win.lang_btns.get(code)
        check(f'語言按鈕 {code}',
              isinstance(b, QPushButton) and b.objectName() == f'lang_{code}'
              and b.property('og') == 'langbtn' and b.isCheckable())
    check('語言按鈕全部 checkable（exclusive 由 QButtonGroup + _retranslate 雙重保證）',
          all(b.isCheckable() for b in win.lang_btns.values()))
    check('theme_btn 嚟自 .ui 且 checkable',
          isinstance(win.theme_btn, QPushButton) and win.theme_btn.isCheckable())

    # ── 4. QSS 契約真係通（objectName + stamp → theme.py selector）──
    sheet = qapp.styleSheet()
    check('app 級 QSS 含 shell / navbar selector',
          'QWidget#oneGateRoot' in sheet and 'QWidget[og="navbar"]' in sheet)
    dark = theme_mod.THEMES['dark']
    brand = win.findChild(QLabel, 'brandLbl')
    check('brandLbl 食到 theme 色（QLabel#brandLbl → $text）',
          brand.palette().color(brand.foregroundRole()).name().upper() == dark['text'].upper())

    # ── 5. page stack 由 PAGE_KEYS 填 ──
    check(f'stack 頁數 == len(PAGE_KEYS)（{len(app_mod.PAGE_KEYS)}）',
          win.stack.count() == len(app_mod.PAGE_KEYS))
    check('stack 順序 == PAGE_KEYS 順序',
          [win.stack.widget(i).page_key for i in range(win.stack.count())]
          == list(app_mod.PAGE_KEYS))
    check('預設頁 = 首頁', win.stack.currentWidget() is win.pages['home']
          and win.nav_btns['home'].isChecked())

    # ── 6. nav 切換（直接按鈕）──
    win.nav_btns['quotes'].click()
    pump()
    check('撳 nav → 切換 page', win.stack.currentWidget() is win.pages['quotes'])
    check('nav checked 狀態同步',
          win.nav_btns['quotes'].isChecked() and not win.nav_btns['home'].isChecked())

    # ── 7. 子選單項切換 + 分組高亮 ──
    win.page_actions['kline'].trigger()
    pump()
    check('子選單項 → 切換 page', win.stack.currentWidget() is win.pages['kline'])
    check('分組按鈕 + menu 項高亮',
          win.menu_btns['test'].isChecked() and win.page_actions['kline'].isChecked()
          and not win.page_actions['fulltest'].isChecked()
          and not win.menu_btns['settings'].isChecked())

    # ── 8. 右鍵彈出窗：同一實例搬出 / 關閉搬返 ──
    win._set_lang('en')
    pump()
    page = win.pages['kline']
    win._popup_page('kline')
    pump()
    win2 = win._popups.get('kline')
    check('彈出窗存在且係同一 page 實例（唔開第二份）',
          isinstance(win2, app_mod._PopupPageWindow) and page.parentWidget() is win2)
    check('彈出窗骨架喺 popup_page.ui：root objectName 沿用 oneGateRoot（食 shell theme QSS）'
          '、page 填進空 pageSlot',
          win2.objectName() == 'oneGateRoot'
          and win2.pageSlot.count() == 1 and win2.pageSlot.itemAt(0).widget() is page)
    check('彈出窗標題跟語言', win2.windowTitle() == t('nav_kline', 'en'))
    check('page 已离开 stack', win.stack.indexOf(page) < 0)
    check('彈出後主窗切去其他頁', win.stack.currentWidget() is not page)
    win._return_page('kline')
    pump()
    check('關閉 → 搬返入 stack 原位置（PAGE_KEYS 順序）',
          win.stack.indexOf(page) == app_mod.PAGE_KEYS.index('kline')
          and page.parentWidget() is win.stack)
    check('彈出窗已 unregister', 'kline' not in win._popups)
    win._popup_page('kline')
    win._popup_page('kline')   # 已彈緊 → 喚返，唔開第二個窗
    pump()
    check('重複彈出唔會開第二個窗', len(win._popups) == 1)
    win._return_page('kline')
    pump()

    # ── 9. 語言 ──
    win.lang_btns['en'].click()
    pump()
    check('en 已生效', win._lang == 'en')
    check('window title 跟語言', win.windowTitle() == t('app_title', 'en'))
    check('nav / 子選單文字跟語言',
          win.nav_btns['home'].text() == t('nav_home', 'en')
          and win.menu_btns['test'].text() == t('menu_test', 'en')
          and win.page_actions['fulltest'].text() == t('nav_fulltest', 'en'))
    check('語言按鈕保持 endonym（唔跟 UI 語言變）',
          all(win.lang_btns[c].text() == LANG_SHORT[c] for c in app_mod.LANGS))
    check('当前語言按鈕打勾', win.lang_btns['en'].isChecked()
          and not win.lang_btns['zh_hk'].isChecked())
    check('每張 page 都收到 retranslate',
          all(win.pages[k].langs and win.pages[k].langs[-1] == 'en'
              for k in app_mod.PAGE_KEYS))
    win.lang_btns['zh_cn'].click()
    pump()
    check('再轉 zh_cn 都通', win._lang == 'zh_cn'
          and win.nav_btns['home'].text() == t('nav_home', 'zh_cn'))

    # ── 10. theme ──
    win.theme_btn.click()
    pump()
    light = theme_mod.THEMES['light']
    check('theme_btn → apply_theme(light)', theme_mod.CURRENT == 'light')
    check('app QSS 換咗 light 底色', light['window'] in qapp.styleSheet())
    check('brandLbl 即時跟 light 色',
          brand.palette().color(brand.foregroundRole()).name().upper() == light['text'].upper())
    check('theme 按鈕文字跟 theme + 語言',
          win.theme_btn.text() == theme_toggle_text('light', 'zh_cn'))
    win.theme_btn.click()
    pump()
    check('再撳返 dark', theme_mod.CURRENT == 'dark'
          and dark['window'] in qapp.styleSheet())

    # ── 11. 還原（唔污染其他 E2E）──
    app_mod._PAGE_CLASSES.update(orig_classes)
    theme_mod.apply_theme('dark')
    win.close()

    # ── 12. standalone 外殼（gateway/ui/standalone_window.ui，各頁 `__main__` 用呢條路）──
    from gateway.pages.base_page import StandaloneWindow
    sw = StandaloneWindow(lambda: StubPage('home'), 'page_home_title')
    pump()
    check('`.ui` 骨架：central = oneGateRoot、頂欄控件齊、page 填進空 pageSlot',
          sw.centralWidget().objectName() == 'oneGateRoot'
          and isinstance(sw.standalone_title, QLabel)
          and isinstance(sw.standalone_langlbl, QLabel)
          and isinstance(sw.standalone_lang, QComboBox)
          and isinstance(sw.standalone_theme, QPushButton)
          and sw.pageSlot.count() == 1 and sw.pageSlot.itemAt(0).widget() is sw.page)
    check('Designer margin/spacing 照載入（root 0/0、bar 16,8,16,8）',
          sw.centralWidget().layout().contentsMargins() == QMargins(0, 0, 0, 0)
          and sw.standalone_bar.layout().contentsMargins() == QMargins(16, 8, 16, 8))
    check('og / WA_StyledBackground 由 _STAMP 補返（同主外殼同一 QSS scope）',
          sw.centralWidget().property('og') == 'shell'
          and sw.centralWidget().testAttribute(Qt.WA_StyledBackground)
          and sw.standalone_bar.property('og') == 'navbar'
          and sw.standalone_bar.testAttribute(Qt.WA_StyledBackground))
    check('語言 item 由 LANGS 生成（加語言唔使改 `.ui`）',
          [sw.standalone_lang.itemData(i) for i in range(sw.standalone_lang.count())] == list(LANGS)
          and sw.standalone_lang.itemText(0) == LANG_LABELS[LANGS[0]])
    _en = [sw.standalone_lang.itemData(i) for i in range(sw.standalone_lang.count())].index('en')
    sw.standalone_lang.setCurrentIndex(_en)
    pump()
    check('語言切換 → window title / 頂欄標題 / 語言 label / page 全部跟語言',
          sw._lang == 'en' and sw.windowTitle() == t('page_home_title', 'en')
          and sw.standalone_title.text() == t('page_home_title', 'en')
          and sw.standalone_langlbl.text() == t('language_label', 'en')
          and sw.page.langs and sw.page.langs[-1] == 'en')
    sw.standalone_theme.toggle()
    pump()
    check('theme 掣 → apply_theme(light) + 文字跟 theme/語言',
          theme_mod.CURRENT == 'light'
          and sw.standalone_theme.text() == theme_toggle_text('light', 'en'))
    sw.standalone_theme.toggle()
    pump()
    check('再撳返 dark', theme_mod.CURRENT == 'dark')
    sw.close()

    print('\nFAILURES: ' + (', '.join(FAILS) if FAILS else 'none'))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())

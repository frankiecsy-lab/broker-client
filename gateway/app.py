"""One Gate 主外殼 — 頂部導航欄（按鈕 + QStackedWidget）+ 語言 / theme toolbar。

- nav 按鈕同 `gateway/pages/` 嘅 page 一一对應；PAGE_KEYS 係頁面 registry，
  加新頁 = 呢度加一行 + pages/ 加一個檔。
- **nav 分組**（用戶要求）：直接按鈕 = 首頁 / 行情 / FUTU 交易 / 標的收藏 / 指標管理；**「測試」子選單** =
  K綫測試 / 全功能測試 / 標的列表；**「設定」子選單** = 連綫測試。
  子選單按鈕左鍵 = 開 menu（setMenu 原生）；menu 項 = 正常切換；
  子選單按鈕**右鍵** = context menu「彈出 <頁>」（分組內每頁一個）。
- **nav 右鍵 → 「彈出視窗」**：將該頁 **同一實例**搬入獨立頂層視窗（唔開第二份 —
  K線/交易頁有自己的 worker thread，雙實例會雙跑）；關閉彈出窗 → 搬返入 stack 原位置。
  **左鍵永遠正常切換**：呢頁若正在彈出緊，撳 nav 會先搬返入 shell 再切換（得右鍵先彈出）。
  QMenu 標準行為：唔揳自動消失。
- **語言 = 三個 exclusive 按鈕**（繁體 / 简体 / EN，用戶：唔准 dropdown）；theme 按鈕照舊。
- i18n 單一入口 `gateway/i18n.py`（t()）；theme QSS 由 `gateway/theme.py` 統一生成
  （app 級 setStyleSheet → 彈出窗自動食同一套 theme）。
"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QButtonGroup, QHBoxLayout, QLabel,
                               QMainWindow, QMenu, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

from gateway.i18n import DEFAULT_LANG, LANGS, LANG_SHORT, t, theme_toggle_text
from gateway.pages.connection_page import ConnectionPage
from gateway.pages.favorites_page import FavoritesPage
from gateway.pages.home_page import HomePage
from gateway.pages.fulltest_page import FulltestPage
from gateway.pages.futu_trade_page import FutuTradePage
from gateway.pages.indicators_page import IndicatorsPage
from gateway.pages.kline_page import KlinePage
from gateway.pages.quotes_page import QuotesPage
from gateway.pages.strategies_page import StrategiesPage
from gateway.pages.symbol_list_page import SymbolListPage
from gateway.theme import apply_theme

# ── 頁面 registry：nav 按鈕 + QStackedWidget 全部由呢個 list 生成 ──
PAGE_KEYS = ('home', 'quotes', 'kline', 'fulltest', 'connection', 'futu_trade',
             'symbol_list', 'favorites', 'indicators', 'strategies')
_PAGE_CLASSES = {
    'home': HomePage,
    'quotes': QuotesPage,
    'kline': KlinePage,
    'fulltest': FulltestPage,
    'connection': ConnectionPage,
    'futu_trade': FutuTradePage,
    'symbol_list': SymbolListPage,
    'favorites': FavoritesPage,
    'indicators': IndicatorsPage,
    'strategies': StrategiesPage,
}
# ── nav 分組（用戶要求）：直接按鈕 vs 子選單（收藏 = 功能頁 → 直接按鈕；指標管理 = 用戶指定頂層直按）──
NAV_DIRECT = ('home', 'quotes', 'futu_trade', 'favorites', 'indicators', 'strategies')
NAV_MENUS = {'test': ('kline', 'fulltest', 'symbol_list'),
             'settings': ('connection',)}


class _PopupPageWindow(QWidget):
    """彈出視窗 — 承載由 stack 搬出嘅 page 同一實例；關閉 → 通知 shell 搬返入 stack。
    objectName 沿用 'oneGateRoot'（theme QSS 嘅 shell 背景 selector）；theme 本身 app 級自動跟隨。"""

    def __init__(self, shell, key):
        super().__init__()
        self._shell = shell
        self._key = key
        self.setObjectName('oneGateRoot')
        self.setWindowTitle(t(f'nav_{key}', shell._lang))
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(shell.pages[key], 1)   # reparent：stack → 呢個窗
        shell.pages[key].show()            # Qt 坑：reparent 之後 widget 自動隱藏 → 唔 show 就空白窗
        self.resize(1200, 800)

    def closeEvent(self, e):
        self._shell._return_page(self._key)   # 唔 destroy page（冇 WA_DeleteOnClose）— 搬返入 stack
        super().closeEvent(e)


class OneGateWindow(QMainWindow):
    """One Gate 主視窗：navBar（brand + nav 按鈕 + 語言/theme）+ page_stack。"""

    def __init__(self):
        super().__init__()
        self.setObjectName('oneGateWindow')
        self._lang = DEFAULT_LANG
        self._theme_name = 'dark'

        root = QWidget()
        root.setObjectName('oneGateRoot')
        root.setProperty('og', 'shell')
        root.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── 頂部導航欄 ──
        navbar = QWidget()
        navbar.setObjectName('navBar')
        navbar.setProperty('og', 'navbar')
        navbar.setAttribute(Qt.WA_StyledBackground, True)
        h = QHBoxLayout(navbar)
        h.setContentsMargins(16, 8, 16, 8)

        brand = QLabel('One Gate')
        brand.setObjectName('brandLbl')
        h.addWidget(brand)
        h.addSpacing(24)

        self.nav_btns = {}     # 直接頁 key → QPushButton
        self.menu_btns = {}    # 子選單 key（test/settings）→ QPushButton
        self.page_actions = {} # 子選單內頁 key → QAction（e2e 可 trigger / 斷言）
        self._popups = {}      # key → _PopupPageWindow（每頁最多一個彈出窗）
        for key in NAV_DIRECT:
            btn = QPushButton()
            btn.setObjectName(f'nav_{key}')
            btn.setProperty('og', 'navbtn')
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked=False, k=key: self._select_page(k))
            # 右鍵 → context menu（「彈出視窗」）；QMenu 標準行為：唔揳 / 撳隔離自動消失
            btn.setContextMenuPolicy(Qt.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, k=key, b=btn: self._nav_context_menu(b, pos, k))
            h.addWidget(btn)
            self.nav_btns[key] = btn

        # ── 子選單（用戶要求：測試 / 設定 集中）──
        for mk, pages in NAV_MENUS.items():
            btn = QPushButton()
            btn.setObjectName(f'nav_menu_{mk}')
            btn.setProperty('og', 'navbtn')
            btn.setCheckable(True)          # 分組內有頁喺前面 → 高亮
            menu = QMenu(btn)
            for pk in pages:
                act = menu.addAction('')
                act.setObjectName(f'nav_{pk}')
                act.setCheckable(True)
                act.triggered.connect(lambda _c=False, k=pk: self._select_page(k))
                self.page_actions[pk] = act
            btn.setMenu(menu)               # 左鍵 = 開 menu（Qt 原生）
            btn.setContextMenuPolicy(Qt.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, b=btn, ps=pages: self._menu_context_menu(b, pos, ps))
            h.addWidget(btn)
            self.menu_btns[mk] = btn

        h.addStretch(1)

        # ── 語言三按鈕（用戶：唔准 dropdown；endonym 短標籤唔跟 UI 語言變）──
        self.lang_btns = {}
        grp_lang = QButtonGroup(self)
        grp_lang.setExclusive(True)
        for code in LANGS:
            b = QPushButton(LANG_SHORT[code])
            b.setObjectName(f'lang_{code}')
            b.setProperty('og', 'langbtn')
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, c=code: self._set_lang(c))
            grp_lang.addButton(b)
            h.addWidget(b)
            self.lang_btns[code] = b

        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName('theme_btn')
        self.theme_btn.setCheckable(True)  # checked = light，unchecked = dark（預設）
        h.addWidget(self.theme_btn)

        v.addWidget(navbar)

        # ── page stack ──
        self.stack = QStackedWidget()
        self.stack.setObjectName('page_stack')
        self.pages = {}
        for key in PAGE_KEYS:
            page = _PAGE_CLASSES[key]()
            self.pages[key] = page
            self.stack.addWidget(page)
        v.addWidget(self.stack, 1)

        self.setCentralWidget(root)

        self.theme_btn.toggled.connect(self._on_theme_toggled)

        self._select_page(PAGE_KEYS[0])   # 預設頁 = 首頁（用戶：首頁第一個進入）
        self._retranslate()

    # ── nav / 語言 / theme ──

    def _select_page(self, key):
        # 左鍵 = 正常切換（用戶要求：得右鍵先彈出）— 呢頁若正在彈出緊，先搬返入 shell 再切換
        if key in self._popups:
            self._return_page(key)
        idx = self.stack.indexOf(self.pages[key])
        if idx < 0:
            return
        self.stack.setCurrentIndex(idx)
        self._sync_nav_checked()

    # ── 彈出視窗（nav 右鍵）──

    def _nav_context_menu(self, btn, pos, key):
        menu = QMenu(self)
        act = menu.addAction(t('nav_popup', self._lang))
        act.triggered.connect(lambda: self._popup_page(key))
        menu.exec(btn.mapToGlobal(pos))   # 標準 context menu：唔揳 / 撳隔離自動消失

    def _menu_context_menu(self, btn, pos, pages):
        """子選單按鈕右鍵 → 「彈出 <頁>」逐頁（分組內每頁一個，用戶嘅彈出功能唔 loss）。"""
        menu = QMenu(self)
        for pk in pages:
            act = menu.addAction(f'{t("nav_popup", self._lang)} · {t(f"nav_{pk}", self._lang)}')
            act.setObjectName(f'popup_{pk}')
            act.triggered.connect(lambda _c=False, k=pk: self._popup_page(k))
        menu.exec(btn.mapToGlobal(pos))

    def _popup_page(self, key):
        if key in self._popups:   # 已彈緊 → 喚返（唔開第二個窗）
            win = self._popups[key]
            win.showNormal(); win.raise_(); win.activateWindow()
            return
        was_current = self.stack.currentWidget() is self.pages[key]
        win = _PopupPageWindow(self, key)   # reparent page 出 stack
        self._popups[key] = win
        if was_current:   # 主窗而家冇咗呢頁 → 切去剩低嘅第一頁
            if self.stack.count():
                self.stack.setCurrentIndex(0)
            self._sync_nav_checked()
        win.show()

    def _return_page(self, key):
        win = self._popups.pop(key, None)
        page = self.pages[key]
        if self.stack.indexOf(page) < 0:   # 搬返入 stack 原位置（PAGE_KEYS 順序）
            self.stack.insertWidget(min(PAGE_KEYS.index(key), self.stack.count()), page)
        if self.stack.currentWidget() is None:
            self.stack.setCurrentIndex(self.stack.indexOf(page))
        self._sync_nav_checked()
        if win is not None:
            win.deleteLater()

    def _sync_nav_checked(self):
        cur = self.stack.currentWidget()
        cur_key = next((k for k, p in self.pages.items() if p is cur), None)
        for k, btn in self.nav_btns.items():
            btn.setChecked(k == cur_key)
        for mk, pages in NAV_MENUS.items():   # 分組內有頁喺前面 → menu 按鈕高亮 + 項打勾
            self.menu_btns[mk].setChecked(cur_key in pages)
            for pk in pages:
                self.page_actions[pk].setChecked(pk == cur_key)

    def _set_lang(self, code):
        if code in LANG_SHORT and code != self._lang:
            self._lang = code
            self._retranslate()

    def _on_theme_toggled(self, checked):
        name = 'light' if checked else 'dark'
        if name != self._theme_name:
            self._theme_name = name
            apply_theme(name)
            self.theme_btn.setText(theme_toggle_text(name, self._lang))

    def _retranslate(self):
        lang = self._lang
        self.setWindowTitle(t('app_title', lang))
        for key in NAV_DIRECT:
            self.nav_btns[key].setText(t(f'nav_{key}', lang))
        for mk, pages in NAV_MENUS.items():
            self.menu_btns[mk].setText(t(f'menu_{mk}', lang))
            for pk in pages:
                self.page_actions[pk].setText(t(f'nav_{pk}', lang))
        for code, b in self.lang_btns.items():   # endonym 固定字串；checked = 當前語言
            b.setText(LANG_SHORT[code])
            b.setChecked(code == lang)
        self.theme_btn.setText(theme_toggle_text(self._theme_name, lang))
        for win in self._popups.values():   # 彈出窗標題跟語言
            win.setWindowTitle(t(f'nav_{win._key}', lang))
        for page in self.pages.values():
            page.retranslate(lang)
        self._sync_nav_checked()


def main():
    app = QApplication(sys.argv)
    apply_theme('dark')
    win = OneGateWindow()
    win.showMaximized()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()

"""One Gate 主外殼 — 頂部導航欄（按鈕 + QStackedWidget）+ 語言 / theme toolbar。

- nav 按鈕同 `gateway/pages/` 嘅 page 一一对應；PAGE_KEYS 係頁面 registry，
  加新頁 = 呢度加一行 + pages/ 加一個檔。
- i18n 單一入口 `gateway/i18n.py`（t()）；theme QSS 由 `gateway/theme.py` 統一生成。
"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel,
                               QMainWindow, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

from gateway.i18n import DEFAULT_LANG, LANGS, LANG_LABELS, t, theme_toggle_text
from gateway.pages.connection_page import ConnectionPage
from gateway.pages.fulltest_page import FulltestPage
from gateway.pages.futu_trade_page import FutuTradePage
from gateway.pages.kline_page import KlinePage
from gateway.theme import apply_theme

# ── 頁面 registry：nav 按鈕 + QStackedWidget 全部由呢個 list 生成 ──
PAGE_KEYS = ('kline', 'fulltest', 'connection', 'futu_trade')
_PAGE_CLASSES = {
    'kline': KlinePage,
    'fulltest': FulltestPage,
    'connection': ConnectionPage,
    'futu_trade': FutuTradePage,
}


class OneGateWindow(QMainWindow):
    """One Gate 主視窗：navBar（brand + nav 按鈕 + 預留位 + 語言/theme）+ page_stack。"""

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

        self.nav_btns = {}
        for key in PAGE_KEYS:
            btn = QPushButton()
            btn.setObjectName(f'nav_{key}')
            btn.setProperty('og', 'navbtn')
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked=False, k=key: self._select_page(k))
            h.addWidget(btn)
            self.nav_btns[key] = btn

        # 預留位 — 「其餘頁面預留位置」嘅具體佔位（disabled，E2E 可斷言存在）
        self.reserved_btn = QPushButton()
        self.reserved_btn.setObjectName('nav_reserved')
        self.reserved_btn.setProperty('og', 'navbtn')
        self.reserved_btn.setEnabled(False)
        h.addWidget(self.reserved_btn)

        h.addStretch(1)

        self.lang_lbl = QLabel()
        h.addWidget(self.lang_lbl)
        self.lang_combo = QComboBox()
        self.lang_combo.setObjectName('lang_combo')
        for code in LANGS:
            self.lang_combo.addItem(LANG_LABELS[code], userData=code)
        h.addWidget(self.lang_combo)

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

        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        self.theme_btn.toggled.connect(self._on_theme_toggled)

        self._select_page('kline')
        self._retranslate()

    # ── nav / 語言 / theme ──

    def _select_page(self, key):
        idx = PAGE_KEYS.index(key)
        self.stack.setCurrentIndex(idx)
        for k, btn in self.nav_btns.items():
            btn.setChecked(k == key)

    def _on_lang_changed(self, idx):
        code = self.lang_combo.itemData(idx)
        if code and code != self._lang:
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
        for key in PAGE_KEYS:
            self.nav_btns[key].setText(t(f'nav_{key}', lang))
        self.reserved_btn.setText(t('nav_reserved', lang))
        self.reserved_btn.setToolTip(t('nav_reserved_tip', lang))
        self.lang_lbl.setText(t('language_label', lang))
        self.theme_btn.setText(theme_toggle_text(self._theme_name, lang))
        for page in self.pages.values():
            page.retranslate(lang)


def main():
    app = QApplication(sys.argv)
    apply_theme('dark')
    win = OneGateWindow()
    win.showMaximized()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()

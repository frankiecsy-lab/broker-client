"""One Gate standalone runner — 每個 page 都可以單獨開視窗 Debug。

用法（page 檔底）：
    if __name__ == '__main__':
        from gateway.pages.base_page import run_standalone
        run_standalone(MyPage, 'page_xxx_title')

Standalone window 有最小 toolbar（標題 + 語言切換 + theme toggle），
同主外殼（gateway/app.py）共用同一套 i18n / theme 模組，行為一致。
"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel,
                               QMainWindow, QPushButton, QVBoxLayout, QWidget)

from gateway.i18n import DEFAULT_LANG, LANGS, LANG_LABELS, t, theme_toggle_text
from gateway.theme import apply_theme


class StandaloneWindow(QMainWindow):
    """最小外殼：頂欄（標題 + 語言 + theme）+ page 本體。"""

    def __init__(self, page_cls, title_key):
        super().__init__()
        self.setObjectName('standalone_window')
        self._lang = DEFAULT_LANG
        self._theme_name = 'dark'
        self._title_key = title_key

        root = QWidget()
        root.setObjectName('oneGateRoot')
        root.setProperty('og', 'shell')
        root.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── 頂欄（同主外殼 navBar 同一 QSS scope）──
        bar = QWidget()
        bar.setObjectName('standalone_bar')
        bar.setProperty('og', 'navbar')
        bar.setAttribute(Qt.WA_StyledBackground, True)
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 8, 16, 8)

        self.title_lbl = QLabel()
        self.title_lbl.setObjectName('standalone_title')
        h.addWidget(self.title_lbl)
        h.addStretch(1)

        self.lang_lbl = QLabel()
        h.addWidget(self.lang_lbl)
        self.lang_combo = QComboBox()
        self.lang_combo.setObjectName('standalone_lang')
        for code in LANGS:
            self.lang_combo.addItem(LANG_LABELS[code], userData=code)
        h.addWidget(self.lang_combo)

        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName('standalone_theme')
        self.theme_btn.setCheckable(True)  # checked = light，unchecked = dark（預設）
        h.addWidget(self.theme_btn)

        v.addWidget(bar)

        # ── page 本體 ──
        self.page = page_cls()
        v.addWidget(self.page, 1)

        self.setCentralWidget(root)

        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        self.theme_btn.toggled.connect(self._on_theme_toggled)
        self._retranslate()

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
        title = t(self._title_key, lang)
        self.setWindowTitle(title)
        self.title_lbl.setText(title)
        self.lang_lbl.setText(t('language_label', lang))
        self.theme_btn.setText(theme_toggle_text(self._theme_name, lang))
        self.page.retranslate(lang)


def run_standalone(page_cls, title_key):
    """開呢個 page 嘅 standalone window（Debug 用）。"""
    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme('dark')
    win = StandaloneWindow(page_cls, title_key)
    win.resize(1080, 720)
    win.show()
    sys.exit(app.exec())

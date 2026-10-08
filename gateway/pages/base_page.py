"""One Gate standalone runner — 每個 page 都可以單獨開視窗 Debug。

用法（page 檔底）：
    if __name__ == '__main__':
        from gateway.pages.base_page import run_standalone
        run_standalone(MyPage, 'page_xxx_title')

Standalone window 有最小 toolbar（標題 + 語言切換 + theme toggle），
同主外殼（gateway/app.py）共用同一套 i18n / theme 模組，行為一致。
排版喺 `gateway/ui/standalone_window.ui`（同 `app_shell.ui` 同一個 QSS scope）；本檔只剩行為：
語言 item（= `LANGS`）、theme toggle、page 本體填進 `pageSlot`。
"""
import sys

from PySide6.QtWidgets import QApplication, QMainWindow

from gateway.i18n import DEFAULT_LANG, LANGS, LANG_LABELS, t, theme_toggle_text
from gateway.theme import apply_theme
from gateway.ui.bind import apply_text, stamp
from gateway.ui.loader import apply_ui

# 頂欄固定文案（`.ui` 內嗰句只係俾 Designer 睇）；標題 = 構造參數 title_key、theme 掣 = 狀態 → 留 code
_TEXT = {'standalone_langlbl': 'language_label'}
_STAMP = {'oneGateRoot': {'og': 'shell'}, 'standalone_bar': {'og': 'navbar'}}


class StandaloneWindow(QMainWindow):
    """最小外殼：頂欄（標題 + 語言 + theme）+ page 本體。控件全部由 `.ui` 建出。"""

    def __init__(self, page_cls, title_key):
        super().__init__()
        apply_ui(self, 'standalone_window')   # root objectName = standalone_window，central = oneGateRoot
        stamp(self, _STAMP)                   # og / WA_StyledBackground：Designer 帶唔住 dynamic property
        self._lang = DEFAULT_LANG
        self._theme_name = 'dark'
        self._title_key = title_key

        # 語言 item 屬資料（加語言唔使改 `.ui`）
        for code in LANGS:
            self.standalone_lang.addItem(LANG_LABELS[code], userData=code)
        self.standalone_theme.setCheckable(True)   # checked = light，unchecked = dark（預設）

        self.page = page_cls()               # page 本體 = 構造參數 → 填進 `.ui` 預留嘅空 slot
        self.pageSlot.addWidget(self.page, 1)

        self.standalone_lang.currentIndexChanged.connect(self._on_lang_changed)
        self.standalone_theme.toggled.connect(self._on_theme_toggled)
        self._retranslate()

    def _on_lang_changed(self, idx):
        code = self.standalone_lang.itemData(idx)
        if code and code != self._lang:
            self._lang = code
            self._retranslate()

    def _on_theme_toggled(self, checked):
        name = 'light' if checked else 'dark'
        if name != self._theme_name:
            self._theme_name = name
            apply_theme(name)
            self.standalone_theme.setText(theme_toggle_text(name, self._lang))

    def _retranslate(self):
        lang = self._lang
        title = t(self._title_key, lang)
        self.setWindowTitle(title)
        apply_text(self, _TEXT, lang)
        self.standalone_title.setText(title)   # 標題文字跟構造參數 title_key → 屬資料，唔入表
        self.standalone_theme.setText(theme_toggle_text(self._theme_name, lang))
        self.page.retranslate(lang)


def run_standalone(page_cls, title_key):
    """開呢個 page 嘅 standalone window（Debug 用）。"""
    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme('dark')
    win = StandaloneWindow(page_cls, title_key)
    win.resize(1080, 720)
    win.show()
    sys.exit(app.exec())

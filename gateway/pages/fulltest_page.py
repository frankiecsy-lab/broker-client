"""One Gate Page 2 — 全功能測試（嵌入 `gateway/pages/gui_fulltest.py` 全部功能）。

嵌入模式（ticket #04，**零改動 gui_fulltest.py 本身**）：
- 建一個隱藏 top-level `gui_fulltest.MainWindow()` 並保留 Python 引用 alive（`self._win`）—
  佢嘅 LoopThread / TestWorker lifecycle 綁定喺個 instance；One Gate 退出時 `aboutToQuit` →
  `self._win.close()`，觸發原 closeEvent 清理鏈（request_shutdown → thread.wait(3000)，
  shutdown 會先釋放 IB clientId=99）。
- `takeCentralWidget()` 將全部內容搬入本頁 layout；QSS 喺**頁面層級**套用（reparent 之後
  central widget 已唔係原 window 嘅 descendant，套落隱藏 window 會冇 cascade）。
- gui_fulltest 冇 C_* 常數 / QSS template（同 gui_kline 唔同）→ theme 傳播 = 本頁自帶
  string.Template QSS（palette 值由 gateway.theme.THEMES 注入）+ 運行時重新指派
  `gf.STATE_STYLE` 嘅 QColor（`_apply_state` 係 paint-time 讀 → 改完即刻生效）。
  light 用原檔色值（本身為淺底設計，保證清晰可讀），dark 用提亮變體。

單獨運行：`python gateway/pages/fulltest_page.py`（standalone window，帶語言/theme 控制）。
"""
import os
import sys
from string import Template

# ── standalone bootstrap：直接跑呢個檔時將 project root 放落 sys.path（package mode 下 no-op）──
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QTableWidget, QVBoxLayout, QWidget  # noqa: E402

from gateway.pages import gui_fulltest as gf  # noqa: E402 — 同目錄 app 組件（本檔對佢零改動）
import gateway.theme as theme_mod  # noqa: E402 — module 引用（唔係 from-import，避免 stale value binding）

# ── 結果狀態色：light = gui_fulltest 原值（淺底設計），dark = 提亮變體（深底可讀）──
_STATE_COLORS = {
    'light': {'pass': (0, 130, 0), 'expected_fail': (204, 120, 0), 'fail': (200, 0, 0),
              'info_ok': (0, 90, 170), 'info_fail': (96, 96, 96)},
    'dark': {'pass': (63, 185, 80), 'expected_fail': (210, 153, 34), 'fail': (248, 81, 73),
             'info_ok': (88, 166, 255), 'info_fail': (139, 148, 158)},
}

# ── 頁面級 QSS template：cascade 入嵌入子 widget，scope 唔會漏出頁面外 ──
# （section header item 有明確 bg/fg role → 覆蓋 QSS，雙主題本來就清晰；
#   結果 label / running label 係 paint-time inline stylesheet → 優先級更高，照樣生效）
_QSS_TPL = Template('''
QWidget#fulltest_page { background-color: $window; }

QLabel { color: $text; }

QPushButton {
    background-color: $card; color: $text;
    border: 1px solid $border; border-radius: 5px; padding: 3px 8px;
}
QPushButton:hover { background-color: $border; }
QPushButton:checked { background-color: $accent; color: #FFFFFF; }
QPushButton:disabled { background-color: $surface; color: $muted; }

QTableWidget {
    background-color: $surface; alternate-background-color: $card;
    color: $text; gridline-color: $border; border: 1px solid $border;
}
QTableWidget::item:selected { background-color: $accent_pressed; color: #FFFFFF; }

QHeaderView::section {
    background-color: $card; color: $muted;
    border: none; border-bottom: 2px solid $border; padding: 4px; font-weight: bold;
}
''')


class FulltestPage(QWidget):
    """全功能測試頁 — 嵌入 gui_fulltest.MainWindow 全部功能（takeCentralWidget 模式）。

    - 保留原 MainWindow 引用 alive（`self._win`）— thread lifecycle 綁定喺佢；退出時經
      `aboutToQuit` → `self._win.close()` 觸發原 closeEvent 清理鏈。
    - theme 傳播 = 頁面級 QSS template + 重新指派 gf.STATE_STYLE QColor（零改動 gui_fulltest.py）。
    """

    def __init__(self):
        super().__init__()
        self.setObjectName('fulltest_page')
        self.setAttribute(Qt.WA_StyledBackground, True)   # bare QWidget 要呢個先會畫頁面級 QSS background
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)

        # ── 嵌入 gui_fulltest.MainWindow（隱藏 top-level；保留引用 alive 俾 thread lifecycle）──
        self._win = gf.MainWindow()          # 唔 show — 只係 take 佢嘅 central widget
        v.addWidget(self._win.takeCentralWidget(), 1)

        self._quit_done = False
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_app_quit)
        theme_mod.add_listener(self._on_theme_changed)   # apply_theme 完成後同步通知（PySide6 冇 styleSheetChanged）

        self._apply_embedded_theme(theme_mod.CURRENT)    # 初始 theme（跟外殼開機狀態，預設 dark；讀 live module attr）

    # ── theme 傳播 ────────────────────────────────────────────────
    def _apply_embedded_theme(self, name: str):
        """重新指派 gf.STATE_STYLE QColor + 套用頁面級 QSS（palette 值由 THEMES[name] 注入）。"""
        pal = theme_mod.THEMES[name]
        # STATE_STYLE：paint-time 讀 → 運行時換色即刻生效；label 字串保留唔變
        for state, (label, _c) in list(gf.STATE_STYLE.items()):
            r, g, b = _STATE_COLORS[name][state]
            gf.STATE_STYLE[state] = (label, QColor(r, g, b))

        self.setStyleSheet(_QSS_TPL.substitute(pal))   # 頁面級 scope：cascade 入嵌入子 widget，唔會漏出頁面外

    def _on_theme_changed(self, name: str):
        """外殼 / standalone window theme 切換（apply_theme listener）→ 嵌入頁跟住換。"""
        self._apply_embedded_theme(name)

    # ── 退出清理 ──────────────────────────────────────────────────
    def _on_app_quit(self):
        """One Gate / standalone window 退出 → 觸發原 closeEvent 清理鏈（idempotent）。"""
        if self._quit_done:
            return
        self._quit_done = True
        # hidden top-level widget 嘅 close() 一樣會 deliver QCloseEvent → gui_fulltest.closeEvent 跑完整條鏈：
        # thread.request_shutdown() → thread.wait(3000)（shutdown 會先釋放 client / IB clientId=99）
        self._win.close()

    # ── i18n ──────────────────────────────────────────────────────
    def retranslate(self, lang: str):
        """外殼 / standalone window 語言切換時調用 — gui_fulltest 冇自己嘅語言 combo，no-op。"""


if __name__ == '__main__':
    from gateway.pages.base_page import run_standalone
    run_standalone(FulltestPage, 'page_fulltest_title')

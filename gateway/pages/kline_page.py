"""One Gate Page 1 — K綫測試（嵌入 `test/gui_kline.py` 全部功能）。

嵌入模式（ticket #03，**零改動 gui_kline.py 本身**）：
- 建一個隱藏 top-level `gui_kline.MainWindow()` 並保留 Python 引用 alive（`self._win`）—
  佢嘅 LoopThread / Worker lifecycle 綁定喺個 instance；One Gate 退出時 `aboutToQuit` →
  `self._win.close()`，觸發原 closeEvent 清理鏈（fetch wait(3000) → request_shutdown → thread.wait(3000））。
- `takeCentralWidget()` 將全部內容搬入本頁 layout；QSS 喺**頁面層級**套用（reparent 之後
  central widget 已唔係原 window 嘅 descendant，套落隱藏 window 會冇 cascade）。
- theme 傳播 = 運行時重新指派 gui_kline 模組級 C_* 顏色常數 + 由源碼重建 QSS（exec）+
  restyle 建檔時 bake 咗嘅 stylesheet + chart redraw — 暗/淺色切換即時跟隨外殼。

單獨運行：`python gateway/pages/kline_page.py`（standalone window，帶語言/theme 控制）。
"""
import inspect
import os
import re
import sys

# ── standalone bootstrap：直接跑呢個檔時將 project root 放落 sys.path（package mode 下 no-op）──
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# gui_kline 住喺 test/ — 加埋該目錄入 import path（同 e2e script 同一 convention；
# gui_kline 本身 import 時會將 project root 放落 sys.path，佢嘅 modules.* import 自然通）
_TEST_DIR = os.path.join(_ROOT, 'test')
if _TEST_DIR not in sys.path:
    sys.path.insert(0, _TEST_DIR)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

import gui_kline as gk  # noqa: E402 — test/gui_kline.py（本檔對佢零改動）
import gateway.theme as theme_mod  # noqa: E402 — module 引用（唔係 from-import，避免 stale value binding）

# gui_kline QSS template 引用嘅 C_* 常數（C_UP/C_DOWN 係紅漲/綠跌語義色，跟 theme 不變）
_C_KEYS = ('C_WINDOW', 'C_SURFACE', 'C_CARD', 'C_BORDER', 'C_TEXT',
           'C_MUTED', 'C_ACCENT', 'C_ACCENT_PRESSED')

_QSS_RE = re.compile(r'^QSS\s*=\s*f"""[\s\S]*?"""', re.M)


def _rebuild_qss() -> str:
    """由 gui_kline 源碼重新提取 QSS f-string statement，用當前 C_* 值 exec → 新 QSS。

    唔 copy 份 template（避免兩份 source 走樣）：運行時讀 live source — 日後有人改咗
    嗰段 QSS，本頁自動跟住變。
    """
    m = _QSS_RE.search(inspect.getsource(gk))
    if not m:
        raise RuntimeError('gui_kline.py QSS template 格式有變 — 請更新 kline_page._rebuild_qss()')
    ns = {k: getattr(gk, k) for k in _C_KEYS + ('C_UP', 'C_DOWN')}
    exec(m.group(0), ns)   # f-string 喺呢一刻用 ns 入面嘅 C_* 求值
    return ns['QSS']


class KlinePage(QWidget):
    """K綫測試頁 — 嵌入 gui_kline.MainWindow 全部功能（takeCentralWidget 模式）。

    - 保留原 MainWindow 引用 alive（`self._win`）— thread lifecycle 綁定喺佢；退出時經
      `aboutToQuit` → `self._win.close()` 觸發原 closeEvent 清理鏈。
    - theme 傳播 = 重新指派 gk.C_* + 重建 QSS + restyle baked spots + chart redraw（零改動 gui_kline.py）。
    """

    def __init__(self):
        super().__init__()
        self.setObjectName('kline_page')
        self.setAttribute(Qt.WA_StyledBackground, True)   # bare QWidget 要呢個先會畫頁面級 QSS background
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)

        # ── 嵌入 gui_kline.MainWindow（隱藏 top-level；保留引用 alive 俾 thread lifecycle）──
        self._win = gk.MainWindow()          # 唔 show — 只係 take 佢嘅 central widget
        v.addWidget(self._win.takeCentralWidget(), 1)

        self._quit_done = False
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_app_quit)
        theme_mod.add_listener(self._on_theme_changed)   # apply_theme 完成後同步通知（PySide6 冇 styleSheetChanged）

        self._apply_embedded_theme(theme_mod.CURRENT)    # 初始 theme（跟外殼開機狀態，預設 dark；讀 live module attr）

    # ── theme 傳播 ────────────────────────────────────────────────
    def _apply_embedded_theme(self, name: str):
        """重新指派 gui_kline 模組級 C_* → 重建 QSS → restyle baked spots → redraw chart。"""
        pal = theme_mod.THEMES[name]
        gk.C_WINDOW, gk.C_SURFACE = pal['window'], pal['surface']
        gk.C_CARD, gk.C_BORDER = pal['card'], pal['border']
        gk.C_TEXT, gk.C_MUTED = pal['text'], pal['muted']
        gk.C_ACCENT, gk.C_ACCENT_PRESSED = pal['accent'], pal['accent_pressed']
        # C_UP/C_DOWN 係語義色（紅漲/綠跌 HK convention）— 跟 theme 不變

        self.setStyleSheet(_rebuild_qss())   # 頁面級 scope：cascade 入嵌入子 widget，唔會漏出頁面外

        win = self._win
        # ── 建檔時 bake 咗嘅 stylesheet（f-string 喺 __init__ 求值，C_* 重新指派唔會自動更新 — 逐個 restyle）──
        win.chart.canvas.figure.set_facecolor(gk.C_SURFACE)   # Figure facecolor 係 KlineChart.__init__ bake
        win.chart.readout.setStyleSheet(
            f"color: {gk.C_MUTED}; font-size: 12px; background: transparent;")
        win.panel.btn.setStyleSheet(
            f"color: {gk.C_ACCENT}; font-weight: bold; text-align: left; border: none;"
            f" background: transparent; padding: 2px;")
        win.panel.info.setStyleSheet(f"color: {gk.C_MUTED}; font-size: 11px; background: transparent;")
        win.sym_label.setStyleSheet(
            f"color: {gk.C_MUTED}; font-size: 14px; font-weight: bold; background: transparent;")
        win.time_label.setStyleSheet(f"color: {gk.C_MUTED}; font-size: 14px; background: transparent;")
        win._price_color = gk.C_TEXT          # 重置回 base text color（方向色會喺下次數據更新時重設）
        win._style_price()                    # 重用原方法 — 保留 30px 大字體
        win.chart._redraw()                   # chart 本體全部 C_* 都係 draw-time 讀 → redraw 一次即全 repaint

    def _on_theme_changed(self, name: str):
        """外殼 / standalone window theme 切換（apply_theme listener）→ 嵌入頁跟住換。"""
        self._apply_embedded_theme(name)

    # ── 退出清理 ──────────────────────────────────────────────────
    def _on_app_quit(self):
        """One Gate / standalone window 退出 → 觸發原 closeEvent 清理鏈（idempotent）。"""
        if self._quit_done:
            return
        self._quit_done = True
        # hidden top-level widget 嘅 close() 一樣會 deliver QCloseEvent → gui_kline.closeEvent 跑完整條鏈：
        # fetch_thread.wait(3000) → thread.request_shutdown() → thread.wait(3000）
        self._win.close()

    # ── i18n ──────────────────────────────────────────────────────
    def retranslate(self, lang: str):
        """外殼 / standalone window 語言切換時調用 — 同步入嵌入頁自己嘅語言 combo（繁中/EN）。"""
        idx = 1 if lang == 'en' else 0   # gui_kline 只有 zh/en 兩語；簡中 fallback 返繁中字串
        if self._win.lang_combo.currentIndex() != idx:
            self._win.lang_combo.setCurrentIndex(idx)


if __name__ == '__main__':
    from gateway.pages.base_page import run_standalone
    run_standalone(KlinePage, 'page_kline_title')

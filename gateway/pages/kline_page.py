"""One Gate Page 1 — K綫測試（嵌入 `gateway/pages/gui_kline.py` 全部功能）。

嵌入模式（ticket #03，**零改動 gui_kline.py 本身**）：
- 建一個隱藏 top-level `gui_kline.MainWindow()` 並保留 Python 引用 alive（`self._win`）—
  佢嘅 LoopThread / Worker lifecycle 綁定喺個 instance；One Gate 退出時 `aboutToQuit` →
  `self._win.close()`，觸發原 closeEvent 清理鏈（fetch wait(3000) → request_shutdown → thread.wait(3000））。
- `takeCentralWidget()` 將全部內容搬入本頁 layout；QSS 喺**頁面層級**套用（reparent 之後
  central widget 已唔係原 window 嘅 descendant，套落隱藏 window 會冇 cascade）。
- theme 傳播 = 運行時重新指派 gui_kline 模組級 C_* 顏色常數 + 由源碼重建 QSS（exec）+
  restyle 建檔時 bake 咗嘅 stylesheet + chart redraw — 暗/淺色切換即時跟隨外殼。

指標管理（ticket #19，仍然**零改動 gui_kline.py**）：
- 換 chart 實例：layout 原位置以 `indicators.IndicatorKlineChart`（KlineChart 子类）取代原
  `KlineChart`，再 `set_indicator_manager()` 注入配置 — `MainWindow._on_update` 讀 `self.chart`
  實例屬性，自動用新實例。
- 開關掣列 `ind_bar`：每個已配置指標一個 checkable 掣（objectName `ind_toggle_<id>` +
  property `og="indtoggle"`），插喺 K 線圖正上方；樣式經 `_EXTRA_QSS_TPL`（頁面級，跟 theme）。
- **排版**：`gateway/ui/kline_page.ui`（Designer 可調）— 0 margin 外殼 + 空 `embeddedSlot`（填嵌入內容）、
  `ind_bar` 掣列（`ind_bar_lbl` + 空 `indToggleSlot`，掣數量 = 已配置指標 → 屬資料）。
  🤖 `gui_kline.py` 本身照**零改動**：佢個 central layout 喺佢自己 code 砌 → 唔 promote、唔入 `.ui`；
  掣列「插邊」屬行為（要插進嗰個 foreign layout 嘅 K 線圖上方），所以 `.ui` 只定義控件、本檔負責插位。
- 配置變更雙向同步：本頁掣 → `mgr.set_enabled(origin='kline_page')`；管理頁改 → listener
  rebuild 掣列 + chart `_redraw`（sig/cfg 比對自動重建 panel 與重算）。

單獨運行：`python gateway/pages/kline_page.py`（standalone window，帶語言/theme 控制）。
"""
import inspect
import os
import re
import sys
from string import Template

# ── standalone bootstrap：直接跑呢個檔時將 project root 放落 sys.path（package mode 下 no-op）──
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from gateway import indicators  # noqa: E402 — 指標單一事實來源（DEFS + Manager + IndicatorKlineChart）
from gateway.i18n import DEFAULT_LANG, t  # noqa: E402 — 掣列文案三語（指標名 acronym 語言中立）
from gateway.pages import gui_kline as gk  # noqa: E402 — 同目錄 app 組件（本檔對佢零改動）
import gateway.theme as theme_mod  # noqa: E402 — module 引用（唔係 from-import，避免 stale value binding）
from gateway.ui.bind import apply_text, stamp  # noqa: E402
from gateway.ui.loader import apply_ui  # noqa: E402

# gui_kline QSS template 引用嘅 C_* 常數（C_UP/C_DOWN 係紅漲/綠跌語義色，跟 theme 不變）
_C_KEYS = ('C_WINDOW', 'C_SURFACE', 'C_CARD', 'C_BORDER', 'C_TEXT',
           'C_MUTED', 'C_ACCENT', 'C_ACCENT_PRESSED')

_QSS_RE = re.compile(r'^QSS\s*=\s*f"""[\s\S]*?"""', re.M)

# 指標開關掣列樣式（ticket #19）— 頁面級 scope（property selector，唔 bake 落 widget，
# 所以 _apply_embedded_theme 嘅 restyle 清單唔使加嘢）；唔入 gui_kline.QSS（standalone 冇呢啲 widget）
_EXTRA_QSS_TPL = Template("""\
QWidget#ind_bar { background: transparent; }
QLabel#ind_bar_lbl { color: $muted; font-size: 12px; background: transparent; }
QPushButton[og="indtoggle"] {
    background: $card; color: $muted;
    border: 1px solid $border; border-radius: 4px; padding: 3px 10px; font-size: 12px;
}
QPushButton[og="indtoggle"]:hover { color: $text; border-color: $accent; }
QPushButton[og="indtoggle"]:checked { background: $accent; color: #FFFFFF; border-color: $accent; font-weight: bold; }
""")

# `.ui` 入面嘅靜態 widget：QSS property（Designer 帶唔住）+ 文字來源（見 gateway/ui/bind.py）
_STAMP = {'kline_page': {}}   # 純 QWidget root → 補 WA_StyledBackground，頁面級 QSS 先食到
_TEXT = {'ind_bar_lbl': 'ind_show_label'}


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
    """K綫測試頁 — 嵌入 gui_kline.MainWindow 全部功能（takeCentralWidget 模式）+ 指標疊加。

    - 保留原 MainWindow 引用 alive（`self._win`）— thread lifecycle 綁定喺佢；退出時經
      `aboutToQuit` → `self._win.close()` 觸發原 closeEvent 清理鏈。
    - theme 傳播 = 重新指派 gk.C_* + 重建 QSS + restyle baked spots + chart redraw（零改動 gui_kline.py）。
    - 指標（ticket #19）：chart 實例換成 `IndicatorKlineChart`（同 layout 位置）+ 開關掣列 +
      IndicatorManager listener 雙向同步。
    """

    def __init__(self):
        super().__init__()
        apply_ui(self, 'kline_page')   # 排版（0 margin 外殼 + 指標掣列 ind_bar）全部喺 `.ui`
        stamp(self, _STAMP)            # og / WA_StyledBackground：Designer 帶唔住 dynamic property
        self._lang = DEFAULT_LANG

        # ── 嵌入 gui_kline.MainWindow（隱藏 top-level；保留引用 alive 俾 thread lifecycle）──
        #    內容屬資料（gui_kline 零改動）→ 填進 `.ui` 預留嘅 embeddedSlot
        self._win = gk.MainWindow()          # 唔 show — 只係 take 佢嘅 central widget
        central = self._win.takeCentralWidget()
        self.embeddedSlot.addWidget(central, 1)

        # ── 指標（ticket #19）：換 chart 實例為 IndicatorKlineChart（零改動 gui_kline）──
        #    MainWindow._on_update 讀 self.chart 實例屬性 → 換咗即自動用新實例
        lay = central.layout()
        old_chart = self._win.chart
        idx = lay.indexOf(old_chart)
        lay.removeWidget(old_chart)
        old_chart.setParent(None)
        chart = indicators.IndicatorKlineChart()
        lay.insertWidget(idx if idx >= 0 else 0, chart, 3)   # stretch 同原 addWidget(self.chart, stretch=3)
        self._win.chart = chart

        # ── 開關掣列：控件喺 `.ui`（ind_bar / ind_bar_lbl / indToggleSlot）；呢度只負責**插位** —
        #    插進嵌入 central layout 嘅 K 線圖正上方（gui_kline 個 layout 喺佢自己 code 砌）──
        self._ind_toggles = {}
        bar_idx = (idx if idx >= 0 else 0)
        lay.insertWidget(bar_idx, self.ind_bar)          # chart 剛喺 idx → 插喺 idx 即掣列喺圖上面

        self._mgr = indicators.get_manager()
        chart.set_indicator_manager(self._mgr)
        self._mgr.add_listener(self._on_indicators_changed)
        self._retranslate_widgets()   # `.ui` 內嘅文字屬裝飾 → 一律跟語言覆寫
        self._rebuild_toggle_bar()    # 掣數量 = 已配置指標（屬資料）→ 填進 indToggleSlot

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

        # 頁面級 scope：cascade 入嵌入子 widget，唔會漏出頁面外；+ 指標掣列樣式（同用新 palette → 跟 theme）
        self.setStyleSheet(_rebuild_qss() + _EXTRA_QSS_TPL.substitute(pal))

        win = self._win
        # ── 建檔時 bake 咗嘅 stylesheet（f-string 喺 __init__ 求值，C_* 重新指派唔會自動更新 — 逐個 restyle）──
        win.chart.canvas.figure.set_facecolor(gk.C_SURFACE)   # Figure facecolor 係 KlineChart.__init__ bake
        win.chart.readout.setStyleSheet(
            f"color: {gk.C_MUTED}; font-size: 12px; background: transparent;")
        win.chart._restyle_overlay()   # ⏳ 加載 scrim 跟 palette（未砌疊層就唔使理）
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

    # ── 指標（ticket #19）：開關掣列 + 配置變更同步 ──────────────────
    def _rebuild_toggle_bar(self):
        """全拆全砌：每個已配置指標一個 checkable 掣（text = acronym + 參數摘要，語言中立）。"""
        for btn in self._ind_toggles.values():
            btn.setParent(None)
        self._ind_toggles = {}
        items = self._mgr.items()
        self.ind_bar.setVisible(bool(items))
        for e in items:
            d = indicators.INDICATOR_DEFS.get(e['def'])
            if d is None:
                continue
            btn = QPushButton(f"{d.label} {indicators._params_summary(d, e['params'])}")
            btn.setObjectName(f"ind_toggle_{e['id']}")     # E2E hook
            btn.setProperty('og', 'indtoggle')             # 樣式經 _EXTRA_QSS_TPL（唔 bake，跟 theme）
            btn.setCheckable(True)
            btn.setChecked(e['enabled'])
            btn.setToolTip(self._ind_tooltip(d, e['position'], self._lang))
            btn.toggled.connect(lambda on, iid=e['id']: self._on_ind_toggle(iid, on))
            self.indToggleSlot.addWidget(btn)   # `.ui` 嘅空 slot（label 之後、stretch 之前）
            self._ind_toggles[e['id']] = btn

    @staticmethod
    def _ind_tooltip(d, position, lang):
        """開關掣 tooltip：指標名 · 位置 + 一行描寫（完整用法喺指標管理頁嘅可摺疊詳情）。"""
        pos_key = 'ind_pos_main' if position == 'main' else 'ind_pos_sub'
        return '%s · %s\n%s' % (d.label, t(pos_key, lang), t(d.desc_key, lang))

    def _on_ind_toggle(self, inst_id, on):
        self._mgr.set_enabled(inst_id, on, origin='kline_page')
        # listener 對 origin==自己 跳過掣列 rebuild（掣狀態已係用戶撳出嚟）；chart 即時重建 panel
        self._win.chart._redraw()

    def _on_indicators_changed(self, origin, kind):
        """管理頁新增/修改/移除/開關 → 掣列同步 + chart 即時生效（sig/cfg 比對自動重建與重算）。"""
        if origin != 'kline_page':
            self._rebuild_toggle_bar()
        self._win.chart._redraw()

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
    def _retranslate_widgets(self):
        apply_text(self, _TEXT, self._lang)   # 掣列前綴 label（objectName → i18n key）

    def retranslate(self, lang: str):
        """外殼 / standalone window 語言切換時調用 — 同步入嵌入頁自己嘅語言 combo（繁中/EN）
        + 指標掣列文案（三語；指標名 acronym 語言中立）。
        ⚠️ 掣列文案三語、嵌入嘅 gui_kline chrome 得兩語 — 語言能力唔對等係預期。"""
        idx = 1 if lang == 'en' else 0   # gui_kline 只有 zh/en 兩語；簡中 fallback 返繁中字串
        if self._win.lang_combo.currentIndex() != idx:
            self._win.lang_combo.setCurrentIndex(idx)
        self._lang = lang
        self._retranslate_widgets()
        for e in self._mgr.items():
            btn = self._ind_toggles.get(e['id'])
            d = indicators.INDICATOR_DEFS.get(e['def'])
            if btn is not None and d is not None:
                btn.setToolTip(self._ind_tooltip(d, e['position'], lang))


if __name__ == '__main__':
    from gateway.pages.base_page import run_standalone
    run_standalone(KlinePage, 'page_kline_title')

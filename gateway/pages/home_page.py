"""One Gate 首頁 — 全球市場脈搏：指數卡 dashboard（預設進入頁）。

- **內容**（用戶：所有市場重要指數、密集、專業直觀、可加能量圖）：三區 —
  港股（恒指/國企/恒生科技）、A股（上證/深成指/滬深300/創業板/科創50）、
  美股（**ETF 代理** — 🤖 實測呢個 OpenD 唔支援美股指數報價，SPY/QQQ/DIA/IWM/VIXY 代理，
  分區 header tooltip 如實講）。清單 = 下面 HOME_INDICES 一個常量，改呢度就得。
- **每張卡**（自設 QPainter，唔靠 QSS）：名（三語）+ code、大字價格（紅漲綠跌，跟
  gui_kline C_UP/C_DOWN 慣例）、變動 + %、**走勢 sparkline（漸變面積）**、
  **當日區間能量條**（low→high，marker 喺最新價）、footer 開/高/低/前收/成交額。
- **數據**：modules/market_pulse.fetch_pulse（QThread；subscribe K_DAY → 日K + snapshot；
  逐個失敗如實）。手動刷新 + 60 秒自動刷新 + 更新時間。
- **Theme/i18n**：卡面顏色直接食 theme palette（listener 重畫）；文字全部 t()。

單獨運行：`python gateway/pages/home_page.py`。
"""
import datetime
import os
import string
import sys

# ── standalone bootstrap（同其他頁同一 convention；package mode 下 no-op）──
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import QPointF, Qt, QThread, QTimer, Signal  # noqa: E402
from PySide6.QtGui import (QColor, QFont, QLinearGradient, QPainter, QPen,  # noqa: E402
                           QPolygonF)
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QPushButton,  # noqa: E402
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from gateway import theme as theme_mod  # noqa: E402
from gateway.i18n import DEFAULT_LANG, t  # noqa: E402
from gateway.pages import gui_kline as gk  # noqa: E402  # 紅漲綠跌常數單一來源（gk.C_UP/C_DOWN）

# ── 首页指數清單（用戶要改就改呢度：code + 分區 + 三語名 key）──
HOME_INDICES = (
    {'code': 'HK.800000', 'group': 'HK', 'key': 'idx_hsi'},
    {'code': 'HK.800100', 'group': 'HK', 'key': 'idx_hscei'},
    {'code': 'HK.800700', 'group': 'HK', 'key': 'idx_hktech'},
    {'code': 'SH.000001', 'group': 'CN', 'key': 'idx_sh'},
    {'code': 'SZ.399001', 'group': 'CN', 'key': 'idx_sz'},
    {'code': 'SH.000300', 'group': 'CN', 'key': 'idx_csi300'},
    {'code': 'SZ.399006', 'group': 'CN', 'key': 'idx_cyb'},
    {'code': 'SH.000688', 'group': 'CN', 'key': 'idx_kc50'},
    {'code': 'US.SPY', 'group': 'US', 'key': 'idx_spx'},
    {'code': 'US.QQQ', 'group': 'US', 'key': 'idx_ndx'},
    {'code': 'US.DIA', 'group': 'US', 'key': 'idx_dji'},
    {'code': 'US.IWM', 'group': 'US', 'key': 'idx_rut'},
    {'code': 'US.VIXY', 'group': 'US', 'key': 'idx_vix'},
)
GROUPS = ('HK', 'CN', 'US')
GROUP_KEYS = {'HK': 'home_grp_hk', 'CN': 'home_grp_cn', 'US': 'home_grp_us'}
COLS = 4                      # 每區每行卡數（密集）
AUTO_REFRESH_MS = 60_000      # 60 秒自動刷新
KLINE_NUM = 60                # sparkline 日K 根數


def _fmt_num(v, dec=2):
    if v is None:
        return '—'
    return f'{v:,.{dec}f}'


def _fmt_big(v, lang):
    """成交額壓縮：zh 萬/億/萬億；en K/M/B。"""
    if v is None:
        return '—'
    v = float(v)
    if lang == 'en':
        for div, suf in ((1e9, 'B'), (1e6, 'M'), (1e3, 'K')):
            if v >= div:
                return f'{v / div:,.2f}{suf}'
        return f'{v:,.0f}'
    for div, suf in ((1e12, '萬億'), (1e8, '億'), (1e4, '萬')):
        if v >= div:
            return f'{v / div:,.2f}{suf}'
    return f'{v:,.0f}'


class _PulseWorker(QThread):
    """fetch_pulse 喺後台行；結果一次過返（逐個失敗已喺 row.err 如實）。"""
    done = Signal(bool, list, str)

    def __init__(self, fetcher, codes, parent=None):
        super().__init__(parent)
        self._fetcher = fetcher
        self._codes = codes

    def run(self):
        try:
            ok, rows, msg = self._fetcher(self._codes, KLINE_NUM)
        except Exception as e:   # 如實，唔炸 UI
            ok, rows, msg = False, [], f'❌ {type(e).__name__}: {e}'
        self.done.emit(ok, rows, msg)


class _IndexCard(QWidget):
    """一張指數卡：全部自畫（背景/價格/sparkline 漸變/區間能量條/footer）。"""

    def __init__(self, page, entry):
        super().__init__()
        self._page = page
        self._entry = entry
        self._row = None
        self.setObjectName('home_card')
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_row(self, row):
        self._row = row
        self.update()

    # ── 繪畫 ──
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self._page._pal
        w, h = self.width(), self.height()
        up_c, down_c = gk.C_UP, gk.C_DOWN

        # 卡底
        p.setPen(QPen(QColor(pal['border'])))
        p.setBrush(QColor(pal['card']))
        p.drawRoundedRect(0, 0, w - 1, h - 1, 8, 8)

        lang = self._page._lang
        name = t(self._entry['key'], lang)
        code = self._entry['code']
        row = self._row

        # 名 + code
        p.setPen(QColor(pal['text']))
        p.setFont(QFont(self.font().family(), 11, QFont.Bold))
        p.drawText(14, 26, name)
        p.setPen(QColor(pal['muted']))
        p.setFont(QFont(self.font().family(), 8))
        p.drawText(0, 14, w - 14, 16, int(Qt.AlignRight | Qt.AlignVCenter), code)

        last = (row or {}).get('last')
        prev = (row or {}).get('prev_close')
        up = last is not None and prev not in (None, 0) and last >= prev
        dir_c = up_c if up else down_c

        if last is None:
            # 數據未到 / 失敗 — 如實
            p.setPen(QColor(pal['muted']))
            p.setFont(QFont(self.font().family(), 9))
            err = (row or {}).get('err') or t('home_loading', lang)
            p.drawText(14, 60, w - 28, h - 90,
                       int(Qt.TextWordWrap | Qt.AlignLeft), str(err))
            p.end()
            return

        # 大字價格 + 變動
        chg = last - (prev or last)
        pct = (chg / prev * 100.0) if prev else 0.0
        p.setPen(QColor(dir_c))
        p.setFont(QFont(self.font().family(), 19, QFont.Bold))
        p.drawText(14, 62, _fmt_num(last))
        p.setFont(QFont(self.font().family(), 9, QFont.Bold))
        p.drawText(0, 44, w - 14, 18, int(Qt.AlignRight | Qt.AlignVCenter),
                   f'{"+" if chg >= 0 else ""}{_fmt_num(chg)}  '
                   f'{"+" if chg >= 0 else ""}{pct:.2f}%')

        # 走勢 sparkline（漸變面積）
        spark = (row or {}).get('spark') or []
        self._paint_spark(p, 14, 74, w - 28, 66, spark, dir_c)

        # 當日區間能量條
        lo, hi = (row or {}).get('low'), (row or {}).get('high')
        self._paint_range(p, 14, 150, w - 28, lo, hi, last, dir_c, pal)

        # footer：開/高/低/前收/成交
        p.setPen(QColor(pal['muted']))
        p.setFont(QFont(self.font().family(), 8))
        foot = (f"{t('home_open', lang)} {_fmt_num(row.get('open'))}  "
                f"{t('home_high', lang)} {_fmt_num(hi)}  "
                f"{t('home_low', lang)} {_fmt_num(lo)}  "
                f"{t('home_prev', lang)} {_fmt_num(prev)}  "
                f"{t('home_turnover', lang)} {_fmt_big(row.get('turnover'), lang)}")
        p.drawText(14, h - 22, w - 28, 16, int(Qt.AlignLeft | Qt.TextWordWrap), foot)
        p.end()

    def _paint_spark(self, p, x, y, w, h, closes, dir_c):
        p.setPen(QPen(QColor(self._page._pal['border']), 1, Qt.DotLine))
        p.drawLine(x, y + h, x + w, y + h)   # 底軸
        if len(closes) < 2:
            return
        lo, hi = min(closes), max(closes)
        rng = (hi - lo) or 1.0
        n = len(closes)
        pts = [QPointF(x + i * w / (n - 1), y + h - (c - lo) * (h - 6) / rng - 3)
               for i, c in enumerate(closes)]
        col = QColor(dir_c)
        # 漸變面積
        grad = QLinearGradient(0, y, 0, y + h)
        g1, g2 = QColor(col), QColor(col)
        g1.setAlpha(110); g2.setAlpha(8)
        grad.setColorAt(0.0, g1); grad.setColorAt(1.0, g2)
        area = QPolygonF([QPointF(x, y + h)] + pts + [QPointF(x + w, y + h)])
        p.setPen(Qt.NoPen); p.setBrush(grad)
        p.drawPolygon(area)
        # 線
        p.setPen(QPen(col, 1.8)); p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF(pts))
        # 最新價端點
        p.setBrush(col); p.setPen(Qt.NoPen)
        p.drawEllipse(pts[-1], 2.6, 2.6)

    def _paint_range(self, p, x, y, w, lo, hi, last, dir_c, pal):
        p.setPen(QColor(pal['muted']))
        p.setFont(QFont(self.font().family(), 7))
        p.drawText(x, y - 2, 60, 10, int(Qt.AlignLeft), _fmt_num(lo))
        p.drawText(x + w - 60, y - 2, 60, 10, int(Qt.AlignRight), _fmt_num(hi))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal['border']))
        p.drawRoundedRect(x, y + 8, w, 5, 2, 2)
        if lo is None or hi is None or hi <= lo or last is None:
            return
        pos = max(0.0, min(1.0, (last - lo) / (hi - lo)))
        col = QColor(dir_c); col.setAlpha(160)
        p.setBrush(col)
        p.drawRoundedRect(x, y + 8, w * pos, 5, 2, 2)
        p.setBrush(QColor(dir_c))
        p.drawRect(int(x + w * pos) - 1, y + 5, 2, 11)   # marker


class HomePage(QWidget):
    def __init__(self, pulse_fetcher=None):
        super().__init__()
        self.setObjectName('home_page')
        self._lang = DEFAULT_LANG
        self._pulse_fetcher = pulse_fetcher   # e2e 注入 fake；None → lazy market_pulse
        self._worker = None
        self._pal = dict(theme_mod.THEMES[theme_mod.CURRENT])
        self._cards = {}                      # code → _IndexCard

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(6)

        # ── 頂欄：標題 + 刷新 + 更新時間/狀態 ──
        top = QHBoxLayout()
        self.title_lbl = QLabel(t('page_home_title', self._lang))
        self.title_lbl.setObjectName('home_title')
        top.addWidget(self.title_lbl)
        top.addStretch(1)
        self.updated_lbl = QLabel()
        self.updated_lbl.setObjectName('home_updated')
        top.addWidget(self.updated_lbl)
        self.refresh_btn = QPushButton(t('home_refresh', self._lang))
        self.refresh_btn.setObjectName('home_refresh_btn')
        self.refresh_btn.setProperty('og', 'homebtn')
        self.refresh_btn.clicked.connect(self._refresh_data)
        top.addWidget(self.refresh_btn)
        v.addLayout(top)
        self.status_lbl = QLabel()
        self.status_lbl.setObjectName('home_status')
        v.addWidget(self.status_lbl)

        # ── 內容：ScrollArea + 分區 grid ──
        scroll = QScrollArea()
        scroll.setObjectName('home_scroll')
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        content.setObjectName('home_content')
        cv = QVBoxLayout(content)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(10)
        self._grp_lbls = {}
        for g in GROUPS:
            hdr = QLabel(t(GROUP_KEYS[g], self._lang))
            hdr.setObjectName('home_grp')
            if g == 'US':
                hdr.setToolTip(t('home_us_proxy_note', self._lang))
            cv.addWidget(hdr)
            self._grp_lbls[g] = hdr
            grid = QGridLayout()
            grid.setSpacing(10)
            for i, entry in enumerate(e for e in HOME_INDICES if e['group'] == g):
                card = _IndexCard(self, entry)
                self._cards[entry['code']] = card
                grid.addWidget(card, i // COLS, i % COLS)
            for c in range(COLS):
                grid.setColumnStretch(c, 1)
            cv.addLayout(grid)
        cv.addStretch(1)
        scroll.setWidget(content)
        v.addWidget(scroll, 1)

        # ── 自動刷新 ──
        self._timer = QTimer(self)
        self._timer.setInterval(AUTO_REFRESH_MS)
        self._timer.timeout.connect(self._refresh_data)
        self._timer.start()

        theme_mod.add_listener(self._on_theme_changed)
        self._apply_theme_qss(theme_mod.CURRENT)
        self._refresh_data()

    def _fetcher(self):
        if self._pulse_fetcher is None:
            from modules.market_pulse import fetch_pulse
            self._pulse_fetcher = fetch_pulse
        return self._pulse_fetcher

    # ── 數據 ──
    def _refresh_data(self):
        if self._worker is not None and self._worker.isRunning():
            return
        self.status_lbl.setText(t('home_loading', self._lang))
        codes = [e['code'] for e in HOME_INDICES]
        self._worker = _PulseWorker(self._fetcher(), codes, self)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_done(self, ok, rows, msg):
        by_code = {r.get('code', ''): r for r in rows}
        for code, card in self._cards.items():
            card.set_row(by_code.get(code))
        now = datetime.datetime.now().strftime('%H:%M:%S')
        self.updated_lbl.setText(f'{t("home_updated_at", self._lang)} {now}')
        if not ok:
            self.status_lbl.setText(msg)   # 🤖 set status 要喺 refresh 之後（一貫教訓）
        else:
            self.status_lbl.setText('')

    # ── theme / i18n ──
    def _apply_theme_qss(self, name):
        self._pal = dict(theme_mod.THEMES[name])
        pal = self._pal
        self.setStyleSheet(string.Template(_PAGE_QSS).substitute(
            window=pal['window'], surface=pal['surface'], card=pal['card'],
            border=pal['border'], text=pal['text'], muted=pal['muted'],
            accent=pal['accent'], accent_pressed=pal['accent_pressed']))
        for card in self._cards.values():
            card.update()

    def _on_theme_changed(self, name):
        self._apply_theme_qss(name)

    def retranslate(self, lang):
        self._lang = lang
        self.title_lbl.setText(t('page_home_title', lang))
        self.refresh_btn.setText(t('home_refresh', lang))
        for g, lbl in self._grp_lbls.items():
            lbl.setText(t(GROUP_KEYS[g], lang))
            if g == 'US':
                lbl.setToolTip(t('home_us_proxy_note', lang))
        for card in self._cards.values():
            card.update()   # 卡面文字喺 paintEvent 即時 t()


_PAGE_QSS = """
QWidget#home_page { background-color: $window; }
QWidget#home_content, QScrollArea#home_scroll { background-color: $window; border: none; }
QLabel#home_title { color: $text; font-size: 16px; font-weight: bold; }
QLabel#home_grp { color: $text; font-size: 13px; font-weight: bold; padding: 2px; }
QLabel#home_updated, QLabel#home_status { color: $muted; font-size: 11px; }
QPushButton[og="homebtn"] { color: $muted; background: transparent; border: 1px solid $border;
    border-radius: 4px; padding: 5px 12px; font-size: 12px; }
QPushButton[og="homebtn"]:hover { color: $text; border-color: $accent; }
QPushButton[og="homebtn"]:pressed { background-color: $accent_pressed; color: #FFFFFF; }
"""


if __name__ == '__main__':
    from gateway.pages.base_page import run_standalone
    run_standalone(HomePage, 'page_home_title')

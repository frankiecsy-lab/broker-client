"""One Gate Page — 回測：策略 × 標的 × 週期 → 非常詳細嘅「持倉表現」報告。

用戶明確取捨（全部已落碼）：
- **唔准復利**：要「策略客觀每次嘅表現」→ 每筆**等額注碼**（固定名義 = 名義資金），淨值 = 累加 P&L。
  呢頁喺輸入行旁邊常駐一句 `bt_note_no_compound`，口徑唔使人去估。
- **成本參數可調**：手续费% + 滑價%（每邊）、名義資金、無風險利率全部輸入欄；範圍屬業務資料 →
  一律跟 `gateway/backtest.py` 嘅 `clamp_*` / `*_LO/HI`（唔喺呢度或 `.ui` hardcode 數字）。
- **持倉處理要喺結果入面體現**：持倉模式可揀（長倉/短倉/雙向，選項 = `position_model.MODES`，同量化
  交易頁同一份 enum）+ 指標組「交易執行（持倉處理）」（買/賣訊號數、持倉中/空倉被忽略數、
  平均/最長持倉、喺市場時間覆蓋率、未平倉 mark-to-market）+ 交易明細表（**逐筆持倉方向**、
  持倉根數/日數/平倉原因）+ 被忽略訊號表（逐條時間/訊號/價格/原因）。

架構：
- **口徑全部喺 `gateway/backtest.py`**（純計算、冇 Qt）：呢頁只做輸入 + 渲染，唔再計一次數。
  指標四組（收益／風險／綜合風險回報／交易執行）嘅**分組、小數位、i18n label key** 一律跟
  `backtest.METRIC_DEFS` → 加指標唔使改 `.ui` 亦唔使改呢度嘅排版。
- **排版**喺 `gateway/ui/backtest_page.ui`（輸入行、四個 tab、四個 GroupBox + 空 grid slot、兩個表、
  覆核圖 slot `bt_chartSlot`）。**數量屬資料**：指標卡 = `METRIC_DEFS`、券商 = `modules.registry.BROKERS`、
  週期 = quotes `KTYPES`、策略清單 = `StrategyManager` → 由呢度填進 `.ui` 預留嘅 slot。
- **覆核圖（「呢個結果睇落係咪真」）**：`EquityChart`（本檔自繪 QPainter，唔用 matplotlib → 零新依賴、
  唔同 K 圖搶記憶體）畫淨值 + Buy & Hold 對照 + 回撤帶；K 圖 B/S 覆核直接重用
  `indicators.IndicatorKlineChart.set_strategy` → 標記同回測用同一個 `strategies.trade_marks`，
  一致性由結構保證（唔存在第二份訊號邏輯）。兩張圖都跟 theme / 語言；冇結果時如實收圖、返還提示句。
- **取數**：QThread + asyncio worker（`gateway/kline_stream` 基建，同行情/K線頁共用一份）。
  失敗如實三句唔同：攞 K 線失敗（連 broker message）／冇收到 K 線／回測本身出錯。
  每次執行派 token → 遲到結果一律作廢（改參數再跑唔會被舊結果蓋住）。
- **本地記憶**：state_store section `backtest`（標的/策略/券商/週期/根數/資金/成本/持倉模式全部記住）。
- **Theme/i18n**：照其他頁 recipe（`gateway/ui/bind.py` 嘅 `stamp` / `apply_text`）。
  tab 標題唔係 widget → 用 `setTabText`；紅漲綠跌語義色跟 `gui_kline.C_UP/C_DOWN`（單一來源）。

單獨運行：`python gateway/pages/backtest_page.py`。
"""
import asyncio
import logging
import os
import string
import sys

# ── standalone bootstrap（同其他頁同一 convention；package mode 下 no-op）──
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import (QAbstractTableModel, QObject, QPointF, QRectF,  # noqa: E402
                            Qt, Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QPainter, QPen,  # noqa: E402
                           QPolygonF)
from PySide6.QtWidgets import (QAbstractItemView, QApplication,  # noqa: E402
                               QDoubleSpinBox, QFrame, QHeaderView, QLabel, QSizePolicy,
                               QSpinBox, QVBoxLayout, QWidget)

import gateway.backtest as bt  # noqa: E402 — 回測口徑單一來源
import gateway.strategies as strategies  # noqa: E402
import gateway.theme as theme_mod  # noqa: E402
from gateway import indicators, state_store  # noqa: E402 — K 圖覆核重用 IndicatorKlineChart
from gateway import position_model as pm  # noqa: E402 — 持倉模式 enum 單一來源（同量化頁共用）
from gateway.i18n import DEFAULT_LANG, t  # noqa: E402
from gateway.kline_stream import ClientHolderMixin, LoopThreadBase  # noqa: E402 — 同行情頁共用 stream 基建
from gateway.pages import gui_kline as gk  # noqa: E402 — 紅漲綠跌常數單一來源（gk.C_UP/C_DOWN）
from gateway.pages.quotes_page import KTYPES, _rows_from_df  # noqa: E402 — 週期清單 / df→K線 tuples 同行情頁共用
from gateway.symbol_input import attach_symbol_input, make_search  # noqa: E402 — 全域模糊輸入
from gateway.ui.bind import apply_text, stamp  # noqa: E402
from gateway.ui.loader import apply_ui  # noqa: E402
from modules.registry import BROKERS as BROKER_REGISTRY  # noqa: E402 — 券商名單一來源

# ── 渲染常量（排版／口徑邊界，唔屬業務數字）──
CARD_COLS = 5          # 每組指標卡每行幾多張（純排版 → 數字改呢度就得）
DASH = '—'             # 冇值一律如實顯示 '—'（bt.fmt_value 返 '' → 呢度轉）

# 得返「個數字本身即賺/蝕」嘅指標先上色（紅漲綠跌）；回撤/波動/成本/持倉日數呢類
# 正數唔等於賺 → 一律唔上色，避免睇錯色以為賺咗。
_SIGNED_KEYS = frozenset(('total_ret_pct', 'ann_ret_pct', 'total_pnl', 'avg_trade_pct',
                          'expectancy_pct', 'median_trade_pct', 'best_trade_pct',
                          'worst_trade_pct', 'excess_ret_pct', 'buy_hold_ret_pct', 'alpha_pct'))

# 表欄 spec = (row key, 欄頭 i18n key, 格式) — 欄頭/格式/排序一处過（唔使第二份欄名）
# 持倉方向放最前：一入眼就知呢筆係多定空（長倉模式永遠「多」、雙向先會混「空」）
TRADE_COLS = (('dir', 'bt_head_dir', 'dir'),
              ('entry_time', 'bt_head_entry_time', 'text'),
              ('exit_time', 'bt_head_exit_time', 'text'),
              ('entry_raw', 'bt_head_entry_price', 'money'),
              ('exit_raw', 'bt_head_exit_price', 'money'),
              ('bars', 'bt_head_bars', 'int'),
              ('days', 'bt_head_days', 'days'),
              ('gross_pct', 'bt_head_gross', 'pct'),
              ('cost_pct', 'bt_head_cost', 'pct'),
              ('net_pct', 'bt_head_net', 'pct'),
              ('pnl', 'bt_head_pnl', 'money'),
              ('exit_reason', 'bt_head_exit', 'exit'))
IG_COLS = (('time', 'bt_ig_head_time', 'text'),
           ('side', 'bt_ig_head_side', 'side'),
           ('price', 'bt_ig_head_price', 'money'),
           ('reason', 'bt_ig_head_reason', 'reason'))
_IG_REASON_KEYS = {'bt_ig_inpos': 'bt_ig_inpos', 'bt_ig_badprice': 'bt_ig_badprice',
                   'bt_ig_flat': 'bt_ig_flat'}
_BPY_SOURCE_KEYS = {'measured': 'bt_bpy_measured', 'ktype_table': 'bt_bpy_ktype_table',
                    'default': 'bt_bpy_default'}
_TRADE_WIDTHS = {0: 56, 1: 130, 2: 130, 3: 78, 4: 78, 5: 62, 6: 62, 7: 78, 8: 70, 9: 78, 10: 96}

# `.ui` 入面嘅靜態 widget：QSS property（Designer 帶唔住）+ 文字來源（見 gateway/ui/bind.py）
_STAMP = {
    'backtest_page': {},   # bare QWidget 要 WA_StyledBackground 先食到頁面級背景 QSS
    'bt_symbol_lbl': {'og': 'btlbl'}, 'bt_strategy_lbl': {'og': 'btlbl'},
    'bt_broker_lbl': {'og': 'btlbl'}, 'bt_ktype_lbl': {'og': 'btlbl'},
    'bt_bars_lbl': {'og': 'btlbl'}, 'bt_capital_lbl': {'og': 'btlbl'},
    'bt_fee_lbl': {'og': 'btlbl'}, 'bt_slip_lbl': {'og': 'btlbl'}, 'bt_rf_lbl': {'og': 'btlbl'},
    'bt_mode_lbl': {'og': 'btlbl'},
    'bt_run_btn': {'og': 'btrun'},
    'bt_note': {'role': 'usagehint'}, 'bt_chart_hint': {'role': 'usagehint'},
    'bt_page_note': {'role': 'pagebody'},
    'bt_trades_note': {'role': 'usagehint'}, 'bt_ig_note': {'role': 'usagehint'},
}
_TEXT = {'bt_symbol_lbl': 'bt_symbol_lbl', 'bt_strategy_lbl': 'bt_strategy_lbl',
         'bt_broker_lbl': 'bt_broker_lbl', 'bt_ktype_lbl': 'bt_ktype_lbl',
         'bt_bars_lbl': 'bt_bars_lbl', 'bt_capital_lbl': 'bt_capital_lbl',
         'bt_fee_lbl': 'bt_fee_lbl', 'bt_slip_lbl': 'bt_slip_lbl', 'bt_rf_lbl': 'bt_rf_lbl',
         'bt_mode_lbl': 'mode_lbl',   # 同一個概念 → 同量化頁共用 `mode_lbl`（唔開第二份字串）
         'bt_run_btn': 'bt_run_btn', 'bt_note': 'bt_note_no_compound',
         'bt_grp_ret': 'bt_grp_ret', 'bt_grp_risk': 'bt_grp_risk',
         'bt_grp_adj': 'bt_grp_adj', 'bt_grp_exec': 'bt_grp_exec',
         'bt_page_note': 'bt_page_note', 'bt_trades_note': 'bt_trades_note',
         'bt_ig_note': 'bt_ig_note'}
_PH = {'bt_symbol': 'bt_symbol_ph'}
_TAB_KEYS = (('tab_metrics', 'bt_tab_metrics'), ('tab_trades', 'bt_tab_trades'),
             ('tab_ignored', 'bt_tab_ignored'), ('tab_chart', 'bt_tab_chart'))


def _chart_rows(df):
    """K 線 df → `KlineChart.set_bars` 嘅 tuples（同行情頁同一個轉換器）。
       缺欄（例如某券商唔返 `time_key` → 引擎退回 ktype 對照表照跑）→ 如實返 []：
       回測結果照樣睇，得返 K 圖唔畫，唔為咗畫圖而令回測失敗。"""
    if any(c not in df.columns for c in ('time_key', 'open', 'high', 'low', 'close', 'volume')):
        return []
    return _rows_from_df(df)


class BacktestWorker(QObject, ClientHolderMixin):
    """住喺 worker thread：攞 K 線 → 直接餵 `backtest.run_backtest` → 一次過 emit 結果。
       只發 signal，唔碰 widget；loop/client 生命週期喺 `gateway/kline_stream`。"""

    result = Signal(object)   # {'ok':..., 'reason':..., 'detail':..., 'token':int, ...回測結果}}

    def __init__(self, loop, client_factory=None):
        super().__init__()
        self.init_client_holder(loop, client_factory)
        self._task = None

    def submit(self, token, code, ktype, broker, bars, entry, opts):
        """GUI-thread facade：一次回測。token 由 GUI 派 → 遲到結果一律作廢。"""
        try:
            asyncio.run_coroutine_threadsafe(
                self._run(token, code, ktype, broker, bars, entry, opts), self._loop)
        except RuntimeError as e:   # loop 剛好停咗（收工中）
            self._emit_fail(token, 'bt_err_worker', f'{type(e).__name__}: {e}')

    async def _cancel_work(self):
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self, token, code, ktype, broker, bars, entry, opts):
        self._task = asyncio.current_task()
        try:
            client = await self.ensure_client()
            status, df, message = await client.get_kline(code=code, ktype=ktype,
                                                         broker=broker, kline_num=bars)
        except asyncio.CancelledError:
            raise
        except Exception as e:   # 連線/權限/協定錯 → 如實同「冇 K 線」分開
            logging.exception('backtest: get_kline failed')
            self._emit_fail(token, 'bt_err_worker', f'{type(e).__name__}: {e}')
            return
        if not status or df is None:
            self._emit_fail(token, 'bt_err_kline',
                            str(message) if message else 'status=False 但冇返錯誤 message')
            return
        if len(df) == 0:
            self._emit_fail(token, 'bt_err_kline_empty', '')
            return
        try:
            res = bt.run_backtest(
                entry, strategies.ohlc_from_kline(df),
                times=(df['time_key'] if 'time_key' in df.columns else None),
                ktype=ktype, opts=opts)
        except Exception as e:
            logging.exception('backtest: run_backtest failed')
            self._emit_fail(token, 'bt_err_worker', f'{type(e).__name__}: {e}')
            return
        res['token'] = token
        res['code'] = code   # 狀態行要顯示實際用咗嘅 canonical code（模糊輸入可能改寫過）
        # 覆核圖餵料：bars = 同一次回測用開嘅 K 線（唔再取第二次）；entry = 是次執行嘅策略
        # （中途改咗下拉都唔會畫錯標記）
        res['entry'] = entry
        res['bars'] = _chart_rows(df)
        self.result.emit(res)

    def _emit_fail(self, token, reason, detail):
        self.result.emit({'ok': False, 'token': token, 'reason': reason, 'detail': detail})


class _LoopThread(LoopThreadBase):
    worker_cls = BacktestWorker


class _ResultModel(QAbstractTableModel):
    """(cols spec, rows) → 表格。欄頭、格式、排序全部跟 spec（唔存在第二份欄名）。"""

    def __init__(self, page, cols):
        super().__init__()
        self._page = page
        self._cols = cols
        self._rows = []

    def set_rows(self, rows):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent=None):
        return len(self._rows)

    def columnCount(self, parent=None):
        return len(self._cols)

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and index.isValid():
            key, _, fmt = self._cols[index.column()]
            return self._page._cell(self._rows[index.row()], key, fmt)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return t(self._cols[section][1], self._page._lang)
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        key, _, fmt = self._cols[column]
        self._rows.sort(key=lambda r: _sort_key(r.get(key), fmt),
                        reverse=(order == Qt.DescendingOrder))
        self.layoutChanged.emit()


def _sort_key(v, fmt):
    """數字欄按數值排（None 排最前 = 如實當佢最低）；文字欄按字串。"""
    if fmt in ('exit', 'side', 'reason'):
        return str(v or '')
    if fmt == 'text':
        return str(v or '')
    try:
        f = float(v)
    except (TypeError, ValueError):
        return float('-inf')
    return f


class EquityChart(QWidget):
    """淨值曲線覆核圖（QPainter 自繪）：策略淨值 + Buy & Hold 對照 + 回撤帶。

    🎨 得返兩條線 + 一個帶 → 直接 QPainter 畫：零新依賴、theme 切換即刻 repaint，
       亦唔會同 K 圖嗰份 FigureCanvas 搶記憶體。
    🤖 呢度唔計數：`curve` 原樣由 `backtest.run_backtest` 餵入（等額注碼、累加 P&L、未平倉
       mark-to-market 全部已喺領域層定咗口徑）；`bench` 空 = 起始價算唔到 → 如實唔畫對照線。
    淨值同回撤% 兩套 y 尺度 → 分上下兩帶畫、各帶自己刻度，唔混軸。
    """

    MAIN = 0.72          # 上帶（淨值）高度比例；餘低 = 回撤帶
    GAP = 16             # 兩帶之間：放回撤刻度
    TOP = 22             # 標題 + 圖例
    BOTTOM = 18          # 時間軸
    LEFT = 66            # y 刻度標籤欄
    RIGHT = 8
    Y_TICKS = 4
    X_TICKS = 5
    MAX_PTS = 1200       # 降采樣上限：兩萬根照樣畫得郁，形狀唔變

    def __init__(self, parent=None):
        super().__init__(parent)
        self._curve = {}
        self._capital = 0.0
        self._pal = {}
        self._lang = DEFAULT_LANG
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_curve(self, curve, capital):
        """結果嘅 `curve` 原樣入（空 → 如實畫「未執行」提示，唔留返舊曲線）。"""
        self._curve = curve or {}
        try:
            self._capital = float(capital)
        except (TypeError, ValueError):
            self._capital = 0.0
        self.update()

    def set_palette(self, pal):
        self._pal = pal or {}
        self.update()

    def set_lang(self, lang):
        self._lang = lang
        self.update()

    def _sample(self, n):
        step = max(1, n // self.MAX_PTS)
        idx = list(range(0, n, step))
        if idx[-1] != n - 1:
            idx.append(n - 1)   # 最後一根一定要畫到（期末 mark-to-market 屬語義）
        return idx

    def _draw_header(self, p, pal, fm, has_bench):
        p.setPen(QColor(pal['text']))
        p.drawText(QRectF(self.LEFT, 2, max(1, self.width() - self.LEFT - self.RIGHT), self.TOP - 4),
                   Qt.AlignLeft, t('bt_curve_title', self._lang))
        items = [(pal['accent'], t('bt_curve_legend_strategy', self._lang))]
        if has_bench:
            items.append((pal['muted'], t('bt_curve_legend_bh', self._lang)))
        items.append((gk.C_DOWN, t('bt_curve_legend_dd', self._lang)))
        sw, gap = 14, 5
        widths = [fm.horizontalAdvance(txt) for _c, txt in items]
        x = self.width() - self.RIGHT - sum(widths) - len(items) * (sw + gap)
        for (color, txt), tw in zip(items, widths):
            p.fillRect(QRectF(x, 7, sw, 7), QColor(color))
            p.setPen(QColor(pal['muted']))
            p.drawText(QRectF(x + sw + gap, 2, tw + 4, self.TOP - 4), Qt.AlignLeft, txt)
            x += sw + gap + tw

    def paintEvent(self, _e):
        pal = self._pal or theme_mod.THEMES[theme_mod.CURRENT]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(pal['surface']))
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.0))   # 刻度/圖例屬次要資訊 → 收一號
        p.setFont(font)
        fm = p.fontMetrics()

        eq = list(self._curve.get('equity') or [])
        bench = list(self._curve.get('bench') or [])
        dd = list(self._curve.get('dd_pct') or [])
        self._draw_header(p, pal, fm, bool(bench))
        n = len(eq)
        if n < 2:
            p.setPen(QColor(pal['muted']))
            p.drawText(QRectF(self.LEFT, self.TOP, max(1, self.width() - self.LEFT - self.RIGHT),
                              max(1, self.height() - self.TOP - self.BOTTOM)),
                       Qt.AlignCenter, t('bt_chart_empty', self._lang))
            return

        x0 = self.LEFT
        x1 = max(x0 + 10, self.width() - self.RIGHT)
        w = x1 - x0
        top = self.TOP
        main_h = max(20, int((self.height() - self.TOP - self.BOTTOM) * self.MAIN))
        bot = top + main_h
        vals = eq + bench
        lo, hi = min(vals), max(vals)
        if hi - lo <= 0:
            lo, hi = lo - 1.0, hi + 1.0   # 淨值完全平 → 照樣要有高度先睇到條線
        pad = (hi - lo) * 0.06
        lo, hi = lo - pad, hi + pad
        span = max(1, n - 1)
        idx = self._sample(n)

        def X(i):
            return x0 + w * i / span

        def Y(v):
            return bot - (bot - top) * (v - lo) / (hi - lo)

        for k in range(self.Y_TICKS + 1):   # 淨值刻度
            v = lo + (hi - lo) * k / self.Y_TICKS
            y = Y(v)
            p.setPen(QPen(QColor(pal['border']), 1, Qt.DotLine))
            p.drawLine(int(x0), int(y), x1, int(y))
            p.setPen(QColor(pal['muted']))
            p.drawText(QRectF(2, y - 7, self.LEFT - 6, 13), Qt.AlignRight,
                       bt.fmt_value(v, 'money') or DASH)

        if self._capital and lo <= self._capital <= hi:
            # 起始名義資金 = 賺/蝕分界。標籤直接標金額（語言中立，唔使第二套文案）
            y = Y(self._capital)
            p.setPen(QPen(QColor(pal['muted']), 1, Qt.DashLine))
            p.drawLine(int(x0), int(y), int(x1), int(y))
            p.setPen(QColor(pal['muted']))
            p.drawText(QRectF(x1 - 110, y - 15, 108, 13), Qt.AlignRight,
                       bt.fmt_value(self._capital, 'money') or DASH)

        dd_top = bot + self.GAP
        dd_h = max(12, self.height() - self.BOTTOM - dd_top)
        if len(dd) == n and min(dd) < 0:    # 回撤帶：自己嘅 % 尺度（0 喺帶頂）
            lo_dd = min(min(dd), 0.0)
            scale = dd_h / max(1e-9, -lo_dd)

            def YD(v):
                return dd_top - v * scale

            poly = QPolygonF([QPointF(X(idx[0]), dd_top)])
            for i in idx:
                poly << QPointF(X(i), YD(dd[i]))
            poly << QPointF(X(idx[-1]), dd_top)
            fill = QColor(gk.C_DOWN)
            fill.setAlpha(48)
            p.setBrush(QBrush(fill))
            p.setPen(Qt.NoPen)
            p.drawPolygon(poly)
            p.setPen(QColor(pal['muted']))
            p.drawText(QRectF(2, dd_top - 7, self.LEFT - 6, 13), Qt.AlignRight,
                       bt.fmt_value(0.0, 'pct'))
            p.drawText(QRectF(2, dd_top + dd_h - 13, self.LEFT - 6, 13), Qt.AlignRight,
                       bt.fmt_value(lo_dd, 'pct'))

        if bench and len(bench) == n:       # Buy & Hold 對照（虛線）：策略有冇跑贏「買住唔郁」
            p.setPen(QPen(QColor(pal['muted']), 1.2, Qt.DashLine))
            poly = QPolygonF()
            for i in idx:
                poly << QPointF(X(i), Y(bench[i]))
            p.drawPolyline(poly)
        p.setPen(QPen(QColor(pal['accent']), 1.6))
        poly = QPolygonF()
        for i in idx:
            poly << QPointF(X(i), Y(eq[i]))
        p.drawPolyline(poly)

        times = list(self._curve.get('times') or [])   # 時間軸：標籤按實際 bar 位置放，太闊就 elide
        p.setPen(QColor(pal['muted']))
        slot = w / self.X_TICKS
        for k in range(self.X_TICKS):
            i = round(span * k / max(1, self.X_TICKS - 1))
            if len(times) <= i:
                continue
            p.drawText(QRectF(X(i) - slot / 2, self.height() - self.BOTTOM, slot, self.BOTTOM - 2),
                       Qt.AlignHCenter, fm.elidedText(str(times[i]), Qt.ElideMiddle, int(slot)))

        p.setPen(QPen(QColor(pal['border']), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(x0, top, w, main_h))
        p.drawRect(QRectF(x0, dd_top, w, dd_h))


class BacktestPage(QWidget):
    def __init__(self, client_factory=None):
        super().__init__()
        apply_ui(self, 'backtest_page')   # 排版（輸入行/四個 tab/四個 GroupBox/兩個表）全部喺 `.ui`
        stamp(self, _STAMP)               # og / WA_StyledBackground：Designer 帶唔住 dynamic property
        self._lang = DEFAULT_LANG
        self._client_factory = client_factory
        self._thread = None
        self._worker = None
        self._directory = None            # lazy：標的 index（含 futu import）唔拖慢開頁
        self._search_fn = None
        self._token = 0
        self._last = None                 # 最近一次成功結果（覆核圖 33c 由呢度攞數）
        self._cards = {}                  # metric key → (value label, caption label, MetricDef)

        self._restore_state()
        self._setup_inputs()
        self._setup_tables()
        self._setup_charts()
        self._build_cards()
        self._connect_signals()

        self._retranslate_widgets()
        theme_mod.add_listener(self._on_theme_changed)
        self._apply_theme_qss(theme_mod.CURRENT)
        self._set_status(t('bt_status_idle', self._lang))
        self._start_thread()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_app_quit)

    # ── 本地記憶（state_store section `backtest`）──
    def _restore_state(self):
        s = state_store.load_section(bt.SECTION, {}) or {}
        self._symbol_raw = str(s.get('symbol', '') or '')
        self._strategy_id = str(s.get('strategy', '') or '')
        self._broker_key = str(s.get('broker', '') or '').upper()
        ktype = str(s.get('ktype', '') or '')
        self._ktype = ktype if ktype in KTYPES else 'K_DAY'
        self._bars = bt.clamp_bars(s.get('bars'))
        self._capital = bt.clamp_capital(s.get('capital'))
        self._fee = bt.clamp_cost(s.get('fee_pct'))
        self._slip = bt.clamp_cost(s.get('slip_pct'))
        self._rf = bt.clamp_rf(s.get('rf_pct'))
        self._mode = pm.clamp_mode(s.get('mode'))   # 持倉模式同量化頁共用一份 enum

    def _save_state(self):
        state_store.save_section(bt.SECTION, {
            'symbol': self.bt_symbol.text().strip(),
            'strategy': self.bt_strategy.currentData() or '',
            'broker': self.bt_broker.currentData() or '',
            'ktype': self.bt_ktype.currentText(),
            'bars': self.bt_bars.value(),
            'capital': self.bt_capital.value(),
            'fee_pct': self.bt_fee.value(),
            'slip_pct': self.bt_slip.value(),
            'rf_pct': self.bt_rf.value(),
            'mode': self.bt_mode.currentData() or pm.MODE_DEFAULT,
        })

    # ── 輸入欄（選項內容屬資料 → 全部由 registry / domain 填）──
    def _setup_inputs(self):
        self.bt_symbol.setText(self._symbol_raw)
        attach_symbol_input(self.bt_symbol, self._symbol_search, lang=self._lang,
                            on_activate=lambda _code: self._on_run())   # 回車 = 執行

        self.bt_broker.addItem(t('bt_broker_default', self._lang), '')   # 空 = broker=None → 跟設定
        for name in BROKER_REGISTRY:
            self.bt_broker.addItem(name.upper(), name.upper())
        i = self.bt_broker.findData(self._broker_key)
        self.bt_broker.setCurrentIndex(max(0, i))

        for k in KTYPES:
            self.bt_ktype.addItem(k)   # 週期係 futu enum 字串（語言中立，同行情頁同一份）
        self.bt_ktype.setCurrentIndex(max(0, KTYPES.index(self._ktype)))

        # spin 範圍屬業務口徑 → 一律跟 backtest.clamp_*（`.ui` 得返空 spin）
        self.bt_bars.setRange(bt.BARS_LO, bt.BARS_HI)
        self.bt_bars.setValue(self._bars)
        self.bt_capital.setDecimals(0)
        self.bt_capital.setRange(bt.CAPITAL_LO, bt.CAPITAL_HI)
        self.bt_capital.setSingleStep(10000)
        self.bt_capital.setValue(self._capital)
        # setRange 必須喺 setValue 之前：setRange 會即刻 clamp 現有值，順序倒轉就攞錯值
        for spin, lo, hi, step, val in ((self.bt_fee, bt.COST_LO, bt.COST_HI, 0.05, self._fee),
                                        (self.bt_slip, bt.COST_LO, bt.COST_HI, 0.05, self._slip),
                                        (self.bt_rf, bt.RF_LO, bt.RF_HI, 0.1, self._rf)):
            spin.setDecimals(2)
            spin.setRange(lo, hi)
            spin.setSingleStep(step)
            spin.setValue(val)

        # 持倉模式 = `position_model.MODES`（用戶：「長倉，短倉，雙向，BACKTEST 都可以選」）
        # → 同量化交易頁同一份 enum、同一套 `mode_*` 文案，兩頁不可能講兩套口徑
        for m in pm.MODES:
            self.bt_mode.addItem(t(f'mode_{m}', self._lang), m)
        self.bt_mode.setCurrentIndex(max(0, self.bt_mode.findData(self._mode)))

        self._rebuild_strategy_combo()

    def _rebuild_strategy_combo(self):
        self.bt_strategy.blockSignals(True)
        self.bt_strategy.clear()
        self.bt_strategy.addItem(t('bt_no_strategy_yet', self._lang), '')
        for e in strategies.get_manager().items():
            self.bt_strategy.addItem(e['name'], e['id'])
        self.bt_strategy.setCurrentIndex(max(0, self.bt_strategy.findData(self._strategy_id)))
        self._strategy_id = self.bt_strategy.currentData() or ''
        self.bt_strategy.blockSignals(False)

    def _symbol_search(self, q):
        """模糊搜尋來源：第一次真搜索先至起 index（`get_directory()` 係 singleton，唔會重複 load）。"""
        if self._search_fn is None:
            self._search_fn = make_search(self._get_directory())
        return self._search_fn(q)

    def _get_directory(self):
        if self._directory is None:
            import modules.symbol_search as ss
            self._directory = ss.get_directory()
        return self._directory

    def _canonical_code(self, raw):
        """index canonical（同行情/交易頁同一把尺）；撳唔到 → upper()，交俾 broker 如實答。"""
        try:
            e = self._get_directory().get(raw)
            if e:
                return e['code']
        except Exception:
            pass
        return raw.upper()

    # ── 兩個表（控件喺 `.ui`，呢度只配 model 同欄行為）──
    def _setup_tables(self):
        self.trade_model = _ResultModel(self, TRADE_COLS)
        self.ig_model = _ResultModel(self, IG_COLS)
        for table, model, widths in ((self.bt_trade_table, self.trade_model, _TRADE_WIDTHS),
                                     (self.bt_ig_table, self.ig_model, None)):
            table.setModel(model)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setSortingEnabled(True)
            table.verticalHeader().setVisible(False)
            hdr = table.horizontalHeader()
            hdr.setSectionResizeMode(QHeaderView.Interactive)
            hdr.setStretchLastSection(True)
            for col, w in (widths or {}).items():
                table.setColumnWidth(col, w)

    # ── 覆核圖：`.ui` 得返 bt_chartSlot / bt_chart_hint，控件屬渲染組件 → 呢度砌 ──
    def _setup_charts(self):
        self.bt_chart = EquityChart(self.bt_tabs)
        self.bt_chart.setObjectName('bt_equity_chart')
        self.bt_chartSlot.addWidget(self.bt_chart, 3)
        # K 圖 B/S 覆核：標記由 `IndicatorKlineChart.set_strategy` 計 → 同回測用同一個
        # `strategies.trade_marks`，即「圖上嘅 B/S == 交易明細表」係結構保證，冇第二份訊號邏輯
        self.bt_kline = indicators.IndicatorKlineChart(self.bt_tabs)
        self.bt_kline.setObjectName('bt_kline_chart')
        self.bt_chartSlot.addWidget(self.bt_kline, 2)
        self.bt_kline.setVisible(False)   # 未執行 → 得返 bt_chart_hint 一句話，唔擺空白圖

    def _render_charts(self, payload):
        """結果 → 兩張覆核圖。失敗/未執行 → 清圖並返還提示句（唔留返舊曲線當新結果）。"""
        if payload is None:
            self.bt_chart.set_curve({}, 0)
            self.bt_kline.clear()
            self.bt_kline.set_strategy(None)
            self.bt_kline.setVisible(False)
            self.bt_chart_hint.setVisible(True)
            return
        bars = payload.get('bars') or []
        self.bt_chart.set_curve(payload.get('curve') or {},
                                (payload.get('meta') or {}).get('capital'))
        self.bt_kline.set_bars(bars)
        self.bt_kline.set_strategy(payload.get('entry'))
        self.bt_kline.setVisible(bool(bars))   # 缺欄（冇 time_key 呢類）→ 如實唔畫 K 圖，回測照樣睇
        self.bt_chart_hint.setVisible(False)

    # ── 指標卡：四組分組渲染，數量/分組/格式/label 全部跟 backtest.METRIC_DEFS ──
    def _build_cards(self):
        by_group = bt.metrics_by_group()
        registry = {}
        for group in bt.GROUPS:
            grid = getattr(self, f'bt_grid_{group}', None)
            if grid is None:   # `.ui` 改咗 slot 名 → 如實炸出嚟，唔好靜默少一组
                raise RuntimeError(f'backtest_page: 搵唔到 bt_grid_{group}（檢查 gateway/ui/backtest_page.ui）')
            for i, d in enumerate(by_group[group]):
                card = QFrame(self.bt_cards_host)
                card.setObjectName(f'bt_card_{d.key}')
                col = QVBoxLayout(card)
                col.setContentsMargins(6, 4, 6, 4)
                col.setSpacing(1)
                val = QLabel(DASH, card)
                val.setObjectName(f'bt_cardval_{d.key}')
                val.setProperty('sign', 'na')
                cap = QLabel('', card)
                cap.setObjectName(f'bt_cardlbl_{d.key}')
                cap.setWordWrap(True)
                col.addWidget(val)
                col.addWidget(cap)
                grid.addWidget(card, i // CARD_COLS, i % CARD_COLS)
                self._cards[d.key] = (val, cap, d)
                registry[card.objectName()] = {'og': 'btcard'}
                registry[val.objectName()] = {'og': 'btcardval'}
                registry[cap.objectName()] = {'og': 'btcardlbl'}
        missing = stamp(self, registry)   # 動態起嘅控件照樣行同一套 QSS 契約（搵唔到會如實回報）
        if missing:
            logging.warning('backtest_page: stamp 搵唔到 %s', missing)

    def _render_cards(self, metrics):
        for key, (val, cap, d) in self._cards.items():
            v = (metrics or {}).get(key)
            val.setText(bt.fmt_value(v, d.fmt) or DASH)
            _set_sign(val, _sign_of(key, v))
            cap.setText(t(d.label_key, self._lang))

    def _cell(self, row, key, fmt):
        """表cell：格式 → 字串（同指標卡同一把尺 `bt.fmt_value`；冇值如實 '—'）。"""
        v = row.get(key)
        if fmt == 'exit':
            if row.get('open'):
                return t('bt_exit_open', self._lang)
            return t('bt_exit_signal' if v == 'signal' else 'bt_exit_end', self._lang)
        if fmt == 'side':
            return t('bt_side_b' if v == 'B' else 'bt_side_s', self._lang)
        if fmt == 'dir':   # 持倉方向（引擎已帶 `dir`）→ 長倉模式一律「多」，雙向先會見到「空」
            return t('bt_dir_long' if v == pm.DIR_LONG else 'bt_dir_short', self._lang)
        if fmt == 'reason':
            return t(_IG_REASON_KEYS.get(v, 'bt_ig_flat'), self._lang)
        if fmt == 'text':
            return str(v) if v else DASH
        return bt.fmt_value(v, fmt) or DASH

    # ── signals ──
    def _connect_signals(self):
        self.bt_run_btn.clicked.connect(self._on_run)
        self.bt_strategy.currentIndexChanged.connect(self._on_strategy_choice)
        # 輸入完成先寫盤（唔逐鍵寫）；combo 唔可編輯 → editingFinished 永遠唔會 fire，要用 activated
        self.bt_symbol.editingFinished.connect(self._save_state)
        for spin in (self.bt_bars, self.bt_capital, self.bt_fee, self.bt_slip, self.bt_rf):
            spin.editingFinished.connect(self._save_state)
        for combo in (self.bt_broker, self.bt_ktype, self.bt_mode):
            combo.activated.connect(lambda _i: self._save_state())   # 只使用者揀先寫盤
        strategies.get_manager().add_listener(self._on_strat_config)

    def _start_thread(self):
        self._thread = _LoopThread(
            client_factory=lambda: self._client_factory() if self._client_factory else None)
        self._thread.worker_ready.connect(self._on_worker_ready)
        self._thread.start()

    def _on_worker_ready(self, worker):
        self._worker = worker
        worker.result.connect(self._on_result)

    def _on_strategy_choice(self):
        self._strategy_id = self.bt_strategy.currentData() or ''
        self._save_state()

    def _on_strat_config(self, origin, kind):
        """策略變（任何 origin，包括策略頁）→ 下拉即時跟。"""
        self._rebuild_strategy_combo()

    # ── 執行 ──
    def _opts(self):
        return {'capital': self.bt_capital.value(), 'fee_pct': self.bt_fee.value(),
                'slip_pct': self.bt_slip.value(), 'rf_pct': self.bt_rf.value(),
                'mode': self.bt_mode.currentData() or pm.MODE_DEFAULT}

    def _on_run(self):
        if self._worker is None:
            self._set_status(t('bt_worker_wait', self._lang))
            return
        raw = self.bt_symbol.text().strip()
        if not raw:
            self._set_status(t('bt_err_no_symbol', self._lang))
            return
        sid = self.bt_strategy.currentData() or ''
        entry = strategies.get_manager().get(sid) if sid else None
        if entry is None:
            self._set_status(t('bt_err_no_strategy', self._lang))
            return
        self._save_state()
        self._token += 1   # 先作廢所有 in-flight 結果（改參數再跑唔會俾舊結果蓋）
        self.bt_run_btn.setEnabled(False)
        self.bt_run_btn.setText(t('bt_running_btn', self._lang))
        self._set_status(t('bt_fetching', self._lang))
        self._worker.submit(self._token, self._canonical_code(raw), self.bt_ktype.currentText(),
                            self.bt_broker.currentData() or None, self.bt_bars.value(),
                            entry, self._opts())

    def _on_result(self, payload):
        if payload.get('token') != self._token:   # 遲到結果一律作廢
            return
        self.bt_run_btn.setEnabled(True)
        self.bt_run_btn.setText(t('bt_run_btn', self._lang))
        self._last = payload if payload.get('ok') else None   # 失敗 → 冇圖可畫（唔留返舊數）
        self._render_charts(self._last)
        metrics = payload.get('metrics') or {}
        self._render_cards(metrics)
        trades = payload.get('trades') or []
        ignored = payload.get('ignored') or []
        self.trade_model.set_rows(trades)
        self.ig_model.set_rows(ignored)
        if not payload.get('ok'):
            reason = t(payload.get('reason') or 'bt_err_worker', self._lang)
            detail = payload.get('detail') or ''
            self._set_status(f'{reason}{detail}' if detail else reason)
            return
        self._set_status(self._summary(payload, trades, ignored))

    def _summary(self, res, trades, ignored):
        """狀態行 = 完成 + 用咗乗參數 + 時間軸口徑（bpy 來源如實標籤）+ 暖機提示 + 空表提示。"""
        lang = self._lang
        meta = res.get('meta') or {}
        sig = res.get('signals') or {}
        mode_txt = t('mode_' + pm.clamp_mode(meta.get('mode')), lang)
        parts = [t('bt_done', lang),
                 f'{meta.get("strategy_name", "")} · {res.get("code", "")} · {meta.get("ktype", "")}',
                 # 持倉口徑照樣寫入結果（同一份結果換模式就係另一個策略，唔可以唔講）
                 f'{t("mode_lbl", lang)} {mode_txt}',
                 f'{t("bt_m_n_bars", lang)} {meta.get("n_bars", 0):,}',
                 f'{meta.get("first_time", "")} → {meta.get("last_time", "")}',
                 (f'{t("bt_m_bpy", lang)} {bt.fmt_value(meta.get("bars_per_year"), "rate") or DASH}'
                  f' ({t(_BPY_SOURCE_KEYS.get(meta.get("bpy_source"), "bt_bpy_default"), lang)})')]
        if sig.get('warmup'):
            parts.append(t('bt_warmup_hint', lang).format(n=sig['warmup']))
        if not trades:
            parts.append(t('bt_empty_trades', lang))
        if not ignored:
            parts.append(t('bt_empty_ignored', lang))
        return ' · '.join(p for p in parts if p)

    def _set_status(self, text):
        self.bt_status.setText(text)

    # ── theme / i18n ──
    def _apply_theme_qss(self, name):
        pal = theme_mod.THEMES[name]
        self.setStyleSheet(string.Template(_PAGE_QSS).substitute(
            window=pal['window'], surface=pal['surface'], card=pal['card'],
            border=pal['border'], text=pal['text'], muted=pal['muted'],
            accent=pal['accent'], accent_pressed=pal['accent_pressed'],
            up=gk.C_UP, down=gk.C_DOWN))
        self.bt_chart.set_palette(pal)   # 自繪圖食 palette（唔經 QSS）
        # K 圖（gui_kline 契約，同 kline_page 同一 recipe）：C_* 屬 draw-time 讀 → 重新指派 +
        # redraw 即全跟 theme；figure facecolor / readout 係 __init__ bake → 逐個 restyle
        gk.C_WINDOW, gk.C_SURFACE = pal['window'], pal['surface']
        gk.C_CARD, gk.C_BORDER = pal['card'], pal['border']
        gk.C_TEXT, gk.C_MUTED = pal['text'], pal['muted']
        gk.C_ACCENT, gk.C_ACCENT_PRESSED = pal['accent'], pal['accent_pressed']
        # C_UP/C_DOWN 屬語義色（紅漲綠跌）→ 跟 theme 不變
        self.bt_kline.canvas.figure.set_facecolor(gk.C_SURFACE)
        self.bt_kline.readout.setStyleSheet(
            f"color: {gk.C_MUTED}; font-size: 12px; background: transparent;")
        self.bt_kline._restyle_overlay()
        self.bt_kline._redraw()

    def _on_theme_changed(self, name):
        self._apply_theme_qss(name)

    def _retranslate_widgets(self):
        lang = self._lang
        apply_text(self, _TEXT, lang)
        for name, key in _PH.items():   # placeholder 唔屬 setText
            getattr(self, name).setPlaceholderText(t(key, lang))
        for i, (_tab, key) in enumerate(_TAB_KEYS):   # tab 標題唔係 widget → setTabText
            self.bt_tabs.setTabText(i, t(key, lang))
        self.bt_broker.setItemText(0, t('bt_broker_default', lang))   # 得返第一項屬文案
        for i, m in enumerate(pm.MODES):   # 持倉模式選項文案跟語言（data 唔郁 → 揀咗嘅照留）
            self.bt_mode.setItemText(i, t(f'mode_{m}', lang))
        self.bt_chart_hint.setText(t('bt_chart_empty', lang))
        self.bt_chart.set_lang(lang)      # 自繪圖嘅標題/圖例屬文案 → 語言切換即時 repaint
        self.bt_kline.set_lang(lang)
        self._rebuild_strategy_combo()
        for key, (_val, cap, d) in self._cards.items():   # 指標卡 caption 跟語言
            cap.setText(t(d.label_key, lang))

    def retranslate(self, lang):
        self._lang = lang
        self._retranslate_widgets()
        for model in (self.trade_model, self.ig_model):   # 欄頭跟語言
            model.headerDataChanged.emit(Qt.Horizontal, 0, model.columnCount() - 1)

    # ── 收工 ──
    def _on_app_quit(self):
        try:
            self._thread.request_shutdown()
            self._thread.wait(3000)
        except Exception:
            pass


def _sign_of(key, v):
    """QSS `sign` property：得返 `_SIGNED_KEYS`（純賺蝕性質）先上色，其餘一律 neutral。"""
    if key not in _SIGNED_KEYS:
        return 'na'
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 'na'
    if f > 0:
        return 'pos'
    if f < 0:
        return 'neg'
    return 'zero'


def _set_sign(label, sign):
    """改 `sign` property 後要 unpolish/polish，先會食到新 QSS（見 gateway/ui/bind.py 契約）。"""
    if label.property('sign') == sign:
        return
    label.setProperty('sign', sign)
    label.style().unpolish(label)
    label.style().polish(label)


_PAGE_QSS = """
QWidget#backtest_page { background-color: $window; }
QLabel[og="btlbl"] { color: $muted; font-size: 11px; }
QLineEdit#bt_symbol { background-color: $card; color: $text; border: 1px solid $border;
    border-radius: 4px; padding: 4px 6px; font-size: 13px; }
QComboBox, QSpinBox, QDoubleSpinBox { background-color: $card; color: $text;
    border: 1px solid $border; border-radius: 4px; padding: 3px 6px; font-size: 12px; }
QPushButton[og="btrun"] { color: #FFFFFF; background-color: $accent; border: 1px solid $accent;
    border-radius: 4px; padding: 5px 12px; font-size: 12px; font-weight: bold; }
QPushButton[og="btrun"]:hover { background-color: $accent_pressed; }
QPushButton[og="btrun"]:disabled { color: $muted; background: transparent; border-color: $border; }
QLabel#bt_status { color: $muted; font-size: 11px; }
QTabWidget::pane { border: 1px solid $border; background-color: $surface; }
QTabBar::tab { color: $muted; background: $card; border: 1px solid $border;
    padding: 5px 12px; font-size: 12px; }
QTabBar::tab:selected { color: $text; background: $surface; }
QScrollArea { border: none; background: transparent; }
QGroupBox { color: $muted; border: 1px solid $border; border-radius: 4px;
    margin-top: 8px; font-size: 11px; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; }
QFrame[og="btcard"] { background-color: $card; border: 1px solid $border; border-radius: 4px; }
QLabel[og="btcardval"] { color: $text; background: transparent; border: none;
    font-size: 15px; font-weight: bold; }
QLabel[og="btcardval"][sign="pos"] { color: $up; }
QLabel[og="btcardval"][sign="neg"] { color: $down; }
QLabel[og="btcardlbl"] { color: $muted; background: transparent; border: none; font-size: 10px; }
QTableView#bt_trade_table, QTableView#bt_ig_table { background-color: $surface;
    alternate-background-color: $card; color: $text; border: 1px solid $border;
    gridline-color: $border; font-size: 12px; selection-background-color: $accent;
    selection-color: #FFFFFF; }
QHeaderView::section { background-color: $card; color: $muted; border: 1px solid $border;
    padding: 4px; font-weight: bold; }
"""


if __name__ == '__main__':
    from gateway.pages.base_page import run_standalone
    run_standalone(BacktestPage, 'page_backtest_title')

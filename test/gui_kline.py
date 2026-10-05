"""stream_kline 單檔測試 GUI（PySide6）— 持續實時 K 線串流

運行：python test/stream_kline_gui.py

版面（上 → 下）：
  0. 大字現價列：標的 · 週期 | 較上一根 bar 漲跌 | 大字現價（每次資料刷新閃一下白）| 右上角時間（最後推送 wall clock + 最新 K 線時間 — 一眼睇出推送有冇行緊）
  1. 巨大窗口：K 綫圖（蠟燭 + 成交量，默認顯示最後 300 根；現價水平虛線 + 右側標注跟隨最新 close；滑鼠懸停看 OHLCV 明細）
     🖱️ 手勢：左鍵拖動 = 左右平移 · 滾輪 = 以游標為中心縮放 · 右鍵 = 復位跟隨最新
  2. 中間控制列：標的代碼 / K 綫週期 / KLINE 數量 / 券商（futu/ib）/ 測試按鍵（開始/停止串流）+ 狀態標籤
  3. 下方可折疊窗口：DF 結果表 — 每次更新前必先清空；時間反向排序（最新 bar 喺最上面）

券商由控制列 combo 選擇（默認 futu），override config.json → source.stream_kline；
默認標的 HK.HSImain（恒指期貨主連，Futu "main" 尾碼代碼）。
stream_kline 係持續串流：第一次 yield 係歷史底表（baseline），之後每筆推送
都即時拼接並刷新圖 + 表，顯示實時 K 綫；UI 更新節流到 ~4Hz。
"""
import asyncio
import logging
import math
import os
import sys
import time

# 🤖 由任何工作目錄直接運行都得：加 project root 先 import 到 broker.py
BROKER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BROKER_DIR)

from PySide6.QtCore import QObject, QThread, Qt, Signal, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QHBoxLayout,
                               QLabel, QLineEdit, QMainWindow, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
from modules.broker import BrokerClient
from modules.futu_client import FutuClient
from modules.ib_client import IBClient


# 🎨 主題跟 stock_client/app/theme/palette.py（dark）；蠟燭色對已過 CVD 驗證（deutan ΔE 11.1）
C_WINDOW, C_SURFACE = "#1E1F22", "#26282C"
C_CARD, C_BORDER = "#2E3136", "#3A3D42"
C_TEXT, C_MUTED = "#E6E6E6", "#9AA0A6"
C_ACCENT, C_ACCENT_PRESSED = "#F1553B", "#D6452C"
C_UP, C_DOWN = "#F23645", "#089981"   # 📈 紅漲 / 📉 綠跌（港股慣例；想轉西方風格就對調呢兩個常數）

KTYPES = ["K_1M", "K_5M", "K_15M", "K_60M", "K_DAY", "K_WEEK"]
COLUMNS = ["time_key", "open", "high", "low", "close", "volume"]
BROKERS = ["futu", "ib"]        # 券商 combo 選項；默認 futu
DEFAULT_CODE = "HK.HSImain"     # 恒指期貨主連（Futu "main" 尾碼代碼）

QSS = f"""
QWidget {{ background: {C_WINDOW}; color: {C_TEXT}; font-size: 13px; }}
QLabel {{ background: transparent; }}
QLineEdit, QComboBox, QSpinBox {{
    background: {C_SURFACE}; border: 1px solid {C_BORDER}; border-radius: 4px; padding: 3px 6px;
}}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView, QSpinBox QAbstractItemView {{
    background: {C_SURFACE}; color: {C_TEXT}; selection-background-color: {C_ACCENT};
}}
QPushButton {{
    background: {C_ACCENT}; color: white; border: none; border-radius: 4px;
    padding: 5px 14px; font-weight: bold;
}}
QPushButton:hover {{ background: {C_ACCENT_PRESSED}; }}
QPushButton:disabled {{ background: {C_BORDER}; color: {C_MUTED}; }}
QTableWidget {{ background: {C_SURFACE}; border: 1px solid {C_BORDER}; gridline-color: {C_BORDER}; }}
QHeaderView::section {{
    background: {C_CARD}; color: {C_TEXT}; border: none; border-bottom: 1px solid {C_BORDER}; padding: 3px;
}}
"""


def _plain(value):
    """pandas/numpy 標量 → native Python type（跨線程邊界只准 plain types）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


def _fmt_price(v):
    """readout 用嘅精簡價格格式（去尾零）。"""
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return s if "." in s else s + ".0"


def _short_time(s):
    """X 軸標籤：'2026-10-05 14:30:00' → '10-05 14:30'（日期型保持原樣）。"""
    s = str(s)
    return s[5:16] if len(s) >= 19 and s[10] == " " else s


def _fmt_big(v):
    """大字現價格式：≥1000 用千分位 + 1 位小數，否則 2 位小數。"""
    v = float(v)
    if abs(v) >= 1000:
        return f"{v:,.1f}"
    return f"{v:.2f}"


# ─────────────────────────── K 綫圖（上方巨大窗口） ───────────────────────────

class KlineChart(QWidget):
    """蠟燭圖（上 3/4）+ 成交量（下 1/4），matplotlib 嵌入 Qt。

    set_bars(rows) 推數據入嚟；widget 唔 poll、唔擁有任何 broker 狀態。
    🖱️ 手勢：左鍵拖動 = 左右平移 · 滾輪 = 以游標為中心縮放 · 右鍵 = 復位跟隨最新。
    """

    MAX_DRAW = 300   # 跟隨模式畫最後 N 根 — 保持實時重繪流暢 + 蠟燭夠寬睇得清
    MIN_VIEW = 20    # 🖱️ 縮放下限：最少可見 bar 數
    MAX_VIEW = 1000  # 🖱️ 縮放上限：最多可見 bar 數（同時係繪製數量上限，保性能）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple] = []      # (time_str, o, h, l, c, v)，舊 → 新
        self._hover_idx: int | None = None
        self._view: tuple[float, float] | None = None   # 🖱️ 用戶平移/縮放後嘅視窗範圍（絕對 index 座標）；None = 跟隨最新
        self._drag: tuple[float, float, float] | None = None  # 🖱️ 拖緊：(按下時游標絕對 x、視窗 xmin、xmax)
        self._s = 0                        # 上次繪製 slice 嘅絕對起始 index（本地→絕對座標換算）

        fig = Figure(facecolor=C_SURFACE, edgecolor="none")
        gs = fig.add_gridspec(2, 1, height_ratios=[3, 1], hspace=0.06,
                              left=0.055, right=0.985, top=0.97, bottom=0.09)
        self.ax = fig.add_subplot(gs[0])
        self.axv = fig.add_subplot(gs[1], sharex=self.ax)
        self.canvas = FigureCanvasQTAgg(fig)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 2)
        self.readout = QLabel("—")
        self.readout.setStyleSheet(f"color: {C_MUTED}; font-size: 12px; background: transparent;")
        lay.addWidget(self.readout)
        lay.addWidget(self.canvas, stretch=1)

        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("figure_leave_event", self._on_leave)
        self.canvas.mpl_connect("button_press_event", self._on_press)
        self.canvas.mpl_connect("button_release_event", self._on_release)
        self.canvas.mpl_connect("scroll_event", self._on_scroll)

    # --- 數據入口 -------------------------------------------------------------
    def set_bars(self, rows):
        self._rows = list(rows or [])
        self._hover_idx = None
        # 🖱️ 保留用戶平移/縮放位置（新 bar 接喺尾端，絕對 index 唔會移位）；clear() 先復位
        self._redraw()

    def clear(self):
        self._view = None
        self._drag = None
        self.set_bars([])

    # --- 繪製 -----------------------------------------------------------------
    @staticmethod
    def _style_ax(ax):
        ax.set_facecolor(C_SURFACE)
        for spine in ax.spines.values():
            spine.set_color(C_BORDER)
        ax.tick_params(colors=C_MUTED, labelsize=8)
        ax.grid(True, color=C_BORDER, linewidth=0.6, alpha=0.5)

    def _redraw(self):
        ax, axv = self.ax, self.axv
        ax.clear()
        axv.clear()
        self._style_ax(ax)
        self._style_ax(axv)
        rows_all = self._rows
        n = len(rows_all)
        if n == 0:
            self.readout.setText("—")
            self.canvas.draw_idle()
            return

        # 🖱️ 視窗範圍（絕對 index 座標）：None = 跟隨最新（原行為）；否則用戶平移/縮放後嘅位置
        if self._view is not None and self._view[1] > n - 0.5 + 1e-9:
            self._view = None   # 數據變少咗（新串流 bar 數較少）→ 返跟隨模式
        if self._view is None:
            m = min(n, self.MAX_DRAW)
            s = n - m
            xmin_a, xmax_a = s - 0.6, n - 1 + max(8.0, m * 0.04)   # 右側現價 tag 空間（原行為）
        else:
            xmin_a, xmax_a = self._view
            s = max(0, int(math.floor(xmin_a)))
        e = min(n, int(math.ceil(xmax_a)) + 1)
        if e <= s:   # 防禦：視窗完全出界（理論上唔會發生）→ 返跟隨範圍
            s, e = max(0, n - self.MAX_DRAW), n
            xmin_a, xmax_a = s - 0.6, n - 0.5
        rows = rows_all[s:e]
        k = e - s
        self._s = s

        x = np.arange(k, dtype=float)
        o = np.array([r[1] for r in rows], dtype=float)
        h = np.array([r[2] for r in rows], dtype=float)
        l = np.array([r[3] for r in rows], dtype=float)
        c = np.array([r[4] for r in rows], dtype=float)
        v = np.array([float(r[5]) for r in rows])
        up_i = np.where(c >= o)[0]
        dn_i = np.where(c < o)[0]

        # 影線（細線）+ 實體（矩形），按方向分組向量化繪製（只畫可見 slice）
        ax.vlines(x[up_i], l[up_i], h[up_i], color=C_UP, linewidth=1.0)
        ax.vlines(x[dn_i], l[dn_i], h[dn_i], color=C_DOWN, linewidth=1.0)
        body_w = 0.7 if k > 2 else 0.6
        ax.bar(x[up_i], np.abs(c[up_i] - o[up_i]), bottom=np.minimum(o[up_i], c[up_i]),
               width=body_w, color=C_UP)
        ax.bar(x[dn_i], np.abs(c[dn_i] - o[dn_i]), bottom=np.minimum(o[dn_i], c[dn_i]),
               width=body_w, color=C_DOWN)

        # Y 軸自動 fit 可見範圍（平移去睇舊 bar 時唔會俾全表 min/max 壓扁）
        lo, hi = float(l.min()), float(h.max())
        pad = (hi - lo) * 0.06 or abs(hi) * 0.01 or 1.0
        ax.set_ylim(lo - pad, hi + pad)

        # 📍 現價水平虛線 + 標注：跟隨最新一根 bar 嘅 close；平移/縮放模式下貼住視窗右緣
        last_close = float(rows_all[-1][4])
        ax.axhline(last_close, color=C_ACCENT, linewidth=0.9, linestyle="--", alpha=0.85)
        if self._view is None:
            tag_x_a = n - 1 + max(8.0, m * 0.04) / 2   # gutter 中間（原行為）
        else:
            tag_x_a = xmax_a - max(2.5, (xmax_a - xmin_a) * 0.04)   # 視窗右緣內側
        ax.set_xlim(xmin_a - s, xmax_a - s)
        ax.text(tag_x_a - s, last_close, _fmt_price(last_close),
                ha="center", va="center", fontsize=8, color="#FFFFFF", fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.25", fc=C_ACCENT, ec="none"))

        # 成交量 subplot（同 X 軸）— 只畫可見 slice
        axv.bar(x, v, width=body_w, color=np.where(c >= o, C_UP, C_DOWN), alpha=0.55)
        vmax = float(v.max()) or 1.0
        axv.set_ylim(0, vmax * 1.15)

        def _vol_fmt(val, _pos):
            for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
                if abs(val) >= div:
                    return f"{val / div:.0f}{suf}"
            return f"{val:.0f}"

        axv.yaxis.set_major_formatter(FuncFormatter(_vol_fmt))

        # X 軸：整數 index + 稀疏時間標籤（時間刻度由下方 volume 軸帶）— 用絕對 index 取 label
        tick_i = sorted(set(np.linspace(s, e - 1, min(7, k)).astype(int).tolist()))
        axv.set_xticks([i - s for i in tick_i])   # 絕對 index → 本地座標（tick_i 係 list，逐個減）
        axv.set_xticklabels([_short_time(rows_all[i][0]) for i in tick_i], fontsize=8)
        ax.tick_params(labelbottom=False)

        # crosshair + readout（無懸停時顯示最新一根）— hover idx 係絕對 index
        if self._hover_idx is not None and 0 <= self._hover_idx < n:
            i = self._hover_idx
            ax.axvline(i - s, color=C_MUTED, linewidth=0.8, linestyle="--", alpha=0.8)
            self._set_readout(rows_all[i])
        else:
            self._set_readout(rows_all[-1])
        self.canvas.draw_idle()

    def _set_readout(self, row):
        t, o, h, l, c, v = row
        color = C_UP if c >= o else C_DOWN
        self.readout.setText(
            f"<span style='color:{C_MUTED}'>{t}</span>　"
            f"O <b>{_fmt_price(o)}</b>&nbsp;&nbsp;H <b>{_fmt_price(h)}</b>&nbsp;&nbsp;"
            f"L <b>{_fmt_price(l)}</b>&nbsp;&nbsp;"
            f"C <span style='color:{color}'><b>{_fmt_price(c)}</b></span>"
            f"&nbsp;&nbsp;V {int(v):,}"
        )

    # --- 懸停 / 手勢（拖動平移 · 滾輪縮放 · 右鍵復位） ---------------------------
    def _view_abs(self):
        """目前生效嘅視窗範圍（絕對 index 座標）；跟隨模式 = 最後 MAX_DRAW 根 + 右側現價 tag 空間。"""
        n = len(self._rows)
        if self._view is None:
            m = min(n, self.MAX_DRAW)
            s = n - m
            return s - 0.6, n - 1 + max(8.0, m * 0.04)
        return self._view

    def _on_motion(self, ev):
        if not self._rows or ev.inaxes not in (self.ax, self.axv) or ev.xdata is None:
            return
        # 🖱️ 拖緊 → 左右平移（唔做 hover crosshair）
        if self._drag is not None:
            n = len(self._rows)
            w = self._drag[2] - self._drag[1]
            dx = ev.xdata + self._s - self._drag[0]   # 游標絕對 x 位移（每次 redraw 後 _s 會變，用最新值換算）
            xmin, xmax = self._drag[1] + dx, self._drag[2] + dx
            if w >= n:                    # 視窗闊過全部數據 → 顯示全部
                self._view = (-0.5, max(n - 0.5, 0.5))
            elif xmin < -0.5:             # clamp 住數據左緣
                self._view = (-0.5, -0.5 + w)
            elif xmax > n - 0.5:          # clamp 住數據右緣
                self._view = (n - 0.5 - w, n - 0.5)
            else:
                self._view = (xmin, xmax)
            self._hover_idx = None
            self._redraw()
            return
        # hover crosshair：本地 x → 絕對 index
        n = len(self._rows)
        idx = max(0, min(n - 1, int(round(ev.xdata + self._s))))
        if idx != self._hover_idx:
            self._hover_idx = idx
            self._redraw()

    def _on_leave(self, ev):
        if self._hover_idx is not None:
            self._hover_idx = None
            self._redraw()

    def _on_press(self, ev):
        if not self._rows or ev.inaxes not in (self.ax, self.axv) or ev.xdata is None:
            return
        if ev.button == 3:                # 🖱️ 右鍵 → 復位跟隨最新
            self._view = None
            self._hover_idx = None
            self._redraw()
            return
        if ev.button != 1:
            return
        xmin_a, xmax_a = self._view_abs()
        self._drag = (ev.xdata + self._s, xmin_a, xmax_a)

    def _on_release(self, ev):
        self._drag = None

    def _on_scroll(self, ev):
        if not self._rows or ev.inaxes not in (self.ax, self.axv) or ev.xdata is None:
            return
        n = len(self._rows)
        xmin_a, xmax_a = self._view_abs()
        x0 = ev.xdata + self._s                       # 🖱️ 縮放中心 = 游標位置（絕對座標）
        w = max(self.MIN_VIEW, min((xmax_a - xmin_a) * 1.3 ** (-ev.step), self.MAX_VIEW))
        if w >= n:                                    # 已經縮到全部數據 → 顯示全部
            self._view = (-0.5, max(n - 0.5, 0.5))
            self._hover_idx = None
            self._redraw()
            return
        frac = (x0 - xmin_a) / (xmax_a - xmin_a) if xmax_a > xmin_a else 0.5
        xmin2, xmax2 = x0 - frac * w, x0 + (1 - frac) * w
        if xmin2 < -0.5:                              # 縮放後 clamp 返數據範圍內
            self._view = (-0.5, -0.5 + w)
        elif xmax2 > n - 0.5:
            self._view = (n - 0.5 - w, n - 0.5)
        else:
            self._view = (xmin2, xmax2)
        self._hover_idx = None
        self._redraw()


# ─────────────────────── DF 結果（下方可折疊窗口） ───────────────────────

class CollapsiblePanel(QWidget):
    """標題列（折疊按鍵 + 行數 info）+ QTableWidget 顯示 DF 結果。"""

    TABLE_MAX = 200  # 只顯示最後 N 行 — 保持實時更新流暢

    def __init__(self, columns, parent=None):
        super().__init__(parent)
        self.columns = list(columns)
        self._collapsed = False

        head = QHBoxLayout()
        self.btn = QPushButton("▼ DF 結果")
        self.btn.setFlat(True)
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setStyleSheet(
            f"color: {C_ACCENT}; font-weight: bold; text-align: left; border: none;"
            f" background: transparent; padding: 2px;")
        self.info = QLabel("")
        self.info.setStyleSheet(f"color: {C_MUTED}; font-size: 11px; background: transparent;")
        head.addWidget(self.btn)
        head.addStretch(1)
        head.addWidget(self.info)

        self.table = QTableWidget(0, len(self.columns))
        self.table.setHorizontalHeaderLabels(self.columns)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMinimumHeight(140)
        self.table.setMaximumHeight(280)
        for i, w in enumerate([190, 95, 95, 95, 95, 110][:len(self.columns)]):
            self.table.setColumnWidth(i, w)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addLayout(head)
        lay.addWidget(self.table)
        self.btn.clicked.connect(self.toggle)

    def toggle(self):
        self._collapsed = not self._collapsed
        self.table.setVisible(not self._collapsed)
        self.btn.setText(("▲ " if self._collapsed else "▼ ") + "DF 結果")

    def set_dataframe(self, records: list[dict]):
        """顯示 DF 結果 — 🧹 每次更新前必先清空（規格要求）；⏪ 時間反向排序，最新放上面。"""
        self.clear()
        show = list(reversed(records[-self.TABLE_MAX:]))   # ⏪ 最新 bar 喺最上面
        self.table.setRowCount(len(show))
        for r, rec in enumerate(show):
            for ccol, col in enumerate(self.columns):
                it = QTableWidgetItem(str(rec.get(col, "")))
                if ccol > 0:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, ccol, it)
        total = len(records)
        self.info.setText(f"共 {total} 行 · ⏪ 最新在上" + (f"（顯示最後 {len(show)} 行）" if total > len(show) else ""))

    def clear(self):
        self.table.setRowCount(0)
        self.info.setText("")


# ─────────────────────── asyncio ↔ Qt 橋（worker thread） ───────────────────────

class Worker(QObject):
    """住喺 worker thread；只發 signal，絕不碰 widget。

    stream_kline 同 get_kline 一樣回傳 (status, data, message)：成功時 data 係 async generator，
    consumer task 做 `async for df in gen` — 第一次 yield = baseline，之後每筆 = live tick。
    停止 = cancel consumer — CancelledError 會傳入 broker generator 自己嘅 except/finally（IB 除 updateEvent /
    Futu close context），所以唔會漏訂閱；setup 失敗（權限/代號解析）則直接經 message 回報。
    """

    update = Signal(object)  # {"phase": "baseline"|"tick"|"done", "error","ticks","n_rows","records"}

    UI_INTERVAL = 0.25  # ~4Hz：推送可以密好多，UI 更新節流到呢個間隔

    def __init__(self, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self._loop = loop
        self._client = None            # BrokerClient（lazy，喺呢個 loop 上建立）
        self._consumer_task: asyncio.Task | None = None
        self._shutting_down = False

    async def ensure_client(self):
        if self._client is None:
            self._client = BrokerClient()
            await self._client.__aenter__()
        return self._client

    # --- GUI-thread 入口 --------------------------------------------------------
    def start_stream(self, code: str, ktype: str, num: int, broker: str):
        t = self._consumer_task
        if t is not None and not t.done():
            return  # 已經串流緊
        try:
            asyncio.run_coroutine_threadsafe(self._run_stream(code, ktype, num, broker), self._loop)
        except RuntimeError as e:  # loop 剛好停咗
            logging.warning("schedule rejected (loop not running): %s", e)

    def stop_stream(self):
        t = self._consumer_task
        if t is None or t.done():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._do_stop(t), self._loop)
        except RuntimeError as e:
            logging.warning("stop rejected (loop not running): %s", e)

    async def _do_stop(self, t: asyncio.Task):
        if not t.done():
            t.cancel()
        try:
            await t  # 等 broker 端清理（IB updateEvent / Futu ctx.close）完成先算數
        except BaseException:
            pass  # CancelledError 係預期；其他錯誤已經透過 update signal 報咗

    # --- consumer（跑喺 worker loop） --------------------------------------------
    async def _run_stream(self, code: str, ktype: str, num: int, broker: str):
        self._consumer_task = asyncio.current_task()
        st = {"ticks": 0, "baseline": False, "n_rows": 0, "records": None, "error": None}
        cancelled = False
        try:
            client = await self.ensure_client()
            # 🤖 新形狀（同 get_kline）：(status, data, message)；成功時 data 係 async generator
            status, gen, message = await client.stream_kline(code=code, ktype=ktype, kline_num=num, broker=broker)
            if not status or gen is None:
                # 🤖 setup 失敗（權限/代號解析/訂閱）→ 直接經 done phase 嘅 error 欄顯示喺狀態列
                st["error"] = f"stream_kline 啟動失敗：{message}"
            else:
                last_emit = 0.0
                async for df in gen:
                    if not st["baseline"]:
                        # 1️⃣ 第一次 yield：歷史底表快照，即刻發（唔等節流）
                        st["baseline"] = True
                        st.update(n_rows=len(df), records=self._records(df))
                        self.update.emit(self._payload("baseline", st))
                        last_emit = time.monotonic()
                    else:
                        # 2️⃣ live tick：計數 + 節流後先轉 records / emit（推送率可以高好多）
                        st["ticks"] += 1
                        now = time.monotonic()
                        if now - last_emit >= self.UI_INTERVAL:
                            st.update(n_rows=len(df), records=self._records(df))
                            self.update.emit(self._payload("tick", st))
                            last_emit = now
        except asyncio.CancelledError:
            cancelled = True
            raise  # 🤖 保留取消語義：上層 task.cancel() 先可以正確結束
        except Exception as e:
            logging.exception("stream_kline test failed")
            st["error"] = f"{type(e).__name__}: {e}"
        finally:
            self._consumer_task = None
            if not st["baseline"] and not st["error"] and not cancelled:
                st["error"] = "未收到 baseline（歷史 K 綫取得失敗或串流即時結束）"
            # done 一律發：GUI 靠呢個收復按鍵 / 輸入框
            self.update.emit(self._payload("done", st))

    @staticmethod
    def _records(df):
        return [{k: _plain(v) for k, v in row.items()} for row in df.to_dict("records")]

    @staticmethod
    def _payload(phase: str, st: dict):
        return {"phase": phase, "error": st["error"], "ticks": st["ticks"],
                "n_rows": st["n_rows"], "records": st["records"]}

    async def shutdown(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        t = self._consumer_task
        if t is not None and not t.done():
            t.cancel()
            try:
                await t  # 等 broker 端清理完成先釋放 client
            except BaseException:
                pass
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)  # 釋放 IB clientId 99（如用過）
            except Exception as e:
                logging.warning("BrokerClient cleanup failed: %s", e)
            self._client = None
        if self._loop.is_running():
            self._loop.stop()


class LoopThread(QThread):
    """擁有 event loop + Worker；GUI thread 嘅 facade。"""

    worker_ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Worker | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_requested = False

    @property
    def worker(self) -> Worker | None:
        return self._worker

    # --- QThread body（跑喺 worker thread） ------------------------------------
    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        # Worker 喺呢度建立 → signal affinity == 呢個 thread → emit 自動 queue 去 GUI
        self._worker = Worker(loop)
        try:
            self.worker_ready.emit(self._worker)
            if self._stop_requested:
                loop.run_until_complete(self._worker.shutdown())
                return
            loop.run_forever()
        finally:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                try:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
            loop.close()

    # --- GUI-thread facade ------------------------------------------------------
    def start_stream(self, code: str, ktype: str, num: int, broker: str):
        w = self._worker
        if w is not None and self._loop is not None and self._loop.is_running():
            w.start_stream(code, ktype, num, broker)

    def stop_stream(self):
        w = self._worker
        if w is not None and self._loop is not None and self._loop.is_running():
            w.stop_stream()

    def request_shutdown(self):
        """GUI-thread 入口；pair with wait()。"""
        deadline = time.monotonic() + 2.0
        while True:
            w, loop = self._worker, self._loop
            if w is not None and loop is not None and loop.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(w.shutdown(), loop)
                except RuntimeError as e:
                    logging.warning("request_shutdown rejected: %s", e)
                return
            if not self.isRunning():
                return
            self._stop_requested = True  # run() 會喺入 run_forever 前檢查
            if time.monotonic() >= deadline:
                logging.warning("request_shutdown timed out waiting for the bridge")
                return
            time.sleep(0.01)


# ─────────────────────────────── 主窗口 ───────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("stream_kline 測試 — 持續實時 K 綫串流")
        self.resize(1150, 780)
        self._streaming = False   # E2E / 狀態用：有冇串流進行中

        central = QWidget()
        self.setCentralWidget(central)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # ── 最上方：大字現價列（標的 · 週期 | 漲跌 | 大字現價；資料刷新閃一下）──
        price_row = QHBoxLayout()
        self.sym_label = QLabel(f"{DEFAULT_CODE} · K_1M")
        self.sym_label.setStyleSheet(
            f"color: {C_MUTED}; font-size: 14px; font-weight: bold; background: transparent;")
        self.delta_label = QLabel("")
        self.price_label = QLabel("—")
        self._price_color = C_TEXT   # 方向色（紅漲/綠跌）；閃白結束後還原呢個色
        self._style_price()
        # 🕒 右上角時間：最後推送嘅 wall clock + 最新 K 線時間 — 推送計數行緊但 DF 看似唔變時，
        #    睇呢度即知係「同一根 bar 盤中覆蓋」（正常）定真係冇數據行
        self.time_label = QLabel("—")
        self.time_label.setStyleSheet(
            f"color: {C_MUTED}; font-size: 14px; background: transparent;")
        price_row.addWidget(self.sym_label)
        price_row.addStretch(1)
        price_row.addWidget(self.delta_label)
        price_row.addWidget(self.price_label)
        price_row.addWidget(self.time_label)
        lay.addLayout(price_row)

        # ── 上方：巨大 K 綫圖窗口 ──
        self.chart = KlineChart()
        lay.addWidget(self.chart, stretch=3)

        # ── 中間：控制列（標的代碼 / K 綫週期 / KLINE 數量 / 券商 / 測試按鍵）+ 狀態 ──
        ctrl = QHBoxLayout()
        self.code_edit = QLineEdit(DEFAULT_CODE)
        self.ktype_combo = QComboBox()
        self.ktype_combo.addItems(KTYPES)
        self.ktype_combo.setCurrentText("K_1M")
        self.num_spin = QSpinBox()
        self.num_spin.setRange(1, 5000)
        self.num_spin.setValue(200)
        self.broker_combo = QComboBox()
        self.broker_combo.addItems(BROKERS)
        self.broker_combo.setCurrentText("futu")   # 🏦 默認 futu
        self.test_btn = QPushButton("▶ 開始串流")
        self.test_btn.setMinimumWidth(120)
        ctrl.addWidget(QLabel("標的代碼"))
        ctrl.addWidget(self.code_edit, 1)
        ctrl.addWidget(QLabel("K綫週期"))
        ctrl.addWidget(self.ktype_combo)
        ctrl.addWidget(QLabel("KLINE數量"))
        ctrl.addWidget(self.num_spin)
        ctrl.addWidget(QLabel("券商"))
        ctrl.addWidget(self.broker_combo)
        ctrl.addWidget(self.test_btn)
        lay.addLayout(ctrl)

        self.status_label = QLabel(f"就緒 · 默認 {BROKERS[0]} / {DEFAULT_CODE} — 撳 [開始串流] 訂閱實時 K 綫")
        self._set_status(self.status_label.text(), "muted")
        lay.addWidget(self.status_label)

        # ── 下方：可折疊 DF 結果窗口 ──
        self.panel = CollapsiblePanel(COLUMNS)
        lay.addWidget(self.panel)

        self.thread = LoopThread()
        self.thread.worker_ready.connect(self._on_worker_ready)
        self.thread.start()
        self.test_btn.clicked.connect(self.on_test_clicked)

    def _set_status(self, text: str, kind: str):
        color = {"ok": "#3FB950", "err": "#F85149", "muted": C_MUTED}.get(kind, C_TEXT)
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; font-size: 12px; background: transparent;")

    # --- 大字現價（每次資料刷新閃一下白） ----------------------------------------
    def _style_price(self, color: str | None = None):
        self.price_label.setStyleSheet(
            f"font-size: 30px; font-weight: bold; color: {color or self._price_color};"
            f" background: transparent;")

    def _update_price(self, records: list[dict]):
        """大字現價 + 較上一根 bar close 嘅漲跌（方向色）；📢 刷新閃一下。"""
        if not records:
            return
        last = records[-1]
        c = float(last["close"])
        prev_c = float(records[-2]["close"]) if len(records) >= 2 else float(last["open"])
        up = c >= prev_c
        self._price_color = C_UP if up else C_DOWN
        d = c - prev_c
        pct = (d / prev_c * 100.0) if prev_c else 0.0
        sign = "+" if d >= 0 else ""
        self.sym_label.setText(
            f"{self.code_edit.text().strip()} · {self.ktype_combo.currentText()}")
        self.delta_label.setText(f"{sign}{d:.2f} / {sign}{pct:.2f}%")
        self.delta_label.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {self._price_color}; background: transparent;")
        # 📢 閃一下：先轉白，250ms 後還原方向色（期間有新刷新就重新計時）
        self.price_label.setText(_fmt_big(c))
        self._style_price("#FFFFFF")
        QTimer.singleShot(250, lambda: self._style_price())

    def _clear_price(self):
        self.sym_label.setText("—")
        self.delta_label.setText("")
        self.price_label.setText("—")
        self._price_color = C_TEXT
        self._style_price()

    def _on_worker_ready(self, worker: Worker):
        worker.update.connect(self._on_update)

    # --- 測試按鍵（開始 / 停止串流） ---------------------------------------------
    def on_test_clicked(self):
        if self._streaming:
            self.thread.stop_stream()
            return
        if not self.thread.worker:
            self._set_status("⏳ 橋接初始化中，請稍候再試", "muted")
            return
        code = self.code_edit.text().strip()
        if not code:
            self._set_status("❌ 請輸入標的代碼（格式 MARKET.SYMBOL，如 HK.00700）", "err")
            return
        ktype = self.ktype_combo.currentText()
        num = self.num_spin.value()
        broker = self.broker_combo.currentText().lower()
        self._streaming = True
        for w in (self.code_edit, self.ktype_combo, self.num_spin, self.broker_combo):
            w.setEnabled(False)
        self.test_btn.setText("⏹ 停止串流")
        self._set_status(f"⏳ 訂閱 {code} {ktype} ×{num}（{broker}）…", "muted")
        self.thread.start_stream(code, ktype, num, broker)

    def _on_update(self, p: dict):
        phase = p["phase"]
        if phase in ("baseline", "tick"):
            records = p.get("records") or []
            rows = [(r["time_key"], r["open"], r["high"], r["low"], r["close"], r["volume"])
                    for r in records]
            self.chart.set_bars(rows)
            self.panel.set_dataframe(records)   # 🧹 內部先清空再填
            self._update_price(records)         # 📢 大字現價 + 刷新閃一下
            last_t = str(records[-1]["time_key"]) if records else "—"
            # 🕒 右上角時間：呢次刷新處理到嘅 wall clock + 最新 K 線時間（每次推送都更新）
            self.time_label.setText(
                f"最後推送 {time.strftime('%H:%M:%S')} · 最新K線 {_short_time(last_t)}")
            if phase == "baseline":
                self._set_status(f"🟢 baseline 已載入 · 共 {p['n_rows']} 根 — 等待實時推送…", "ok")
            else:
                self._set_status(
                    f"串流中 · 收到 {p['ticks']} 次推送 · 共 {p['n_rows']} 根 · 最後 bar {last_t}", "muted")
        elif phase == "done":
            self.time_label.setText("—")
            self._streaming = False
            for w in (self.code_edit, self.ktype_combo, self.num_spin, self.broker_combo):
                w.setEnabled(True)
            self.test_btn.setText("▶ 開始串流")
            if p.get("error"):
                self._set_status(
                    f"❌ {p['error']}（檢查：代碼格式 · OpenD/IB 連線 · 行情權限）", "err")
            else:
                self._set_status(
                    f"⏹ 已停止 · 共收到 {p['ticks']} 次推送（baseline 外）· 最後 {p['n_rows']} 根", "ok")

    def closeEvent(self, e):
        # 統一清理：cancel consumer → broker 端拔線 → BrokerClient.__aexit__ → stop loop
        self.thread.request_shutdown()
        self.thread.wait(3000)
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

"""stream_kline 單檔測試 GUI（PySide6）— 持續實時 K 線串流

運行：python gateway/pages/gui_kline.py

版面（上 → 下）：
  0. 大字現價列：標的 · 週期 | 較上一根 bar 漲跌 | 大字現價（每次資料刷新閃一下白）| 右上角時間（最後推送 wall clock + 最新 K 線時間 — 一眼睇出推送有冇行緊）
  1. 巨大窗口：K 綫圖（蠟燭 + 成交量，默認顯示最後 300 根；現價水平虛線 + 右側標注跟隨最新 close；滑鼠懸停看 OHLCV 明細）
     🖱️ 手勢：左鍵拖動 = 左右平移 · 滾輪 = 以游標為中心縮放 · 右鍵 或 左鍵雙擊 = 還原縮放並跟隨最新
  2. 中間控制列：標的代碼（🤖 P8 模糊輸入 — Futu code/中文名/英文名，本地 index + pass-through）
     / K 綫週期 / KLINE 數量 / 券商（futu/ib）/ 測試按鍵 + 語言切換（繁中/EN）+ FETCH 掣 + 狀態標籤
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
from datetime import datetime

# 🤖 由任何工作目錄直接運行都得：加 project root 先 import 到 broker.py（本檔喺 gateway/pages/，上三層）
BROKER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BROKER_DIR)

from PySide6.QtCore import QObject, QThread, Qt, Signal, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.collections import PathCollection
from matplotlib.figure import Figure
from matplotlib.path import Path
from matplotlib.ticker import FuncFormatter, MaxNLocator
from modules.broker import BrokerClient
from modules.registry import BROKERS as BROKER_REGISTRY   # 🤖 P2：券商名單一來源（唔再硬編碼）
from modules.symbol_search import get_directory          # 🤖 P8：本地 symbol index（fuzzy 輸入 + FETCH）
from gateway.symbol_input import attach_symbol_input, make_search  # 🤖 全域模糊輸入（ticket #17 集中處理）


# 🎨 主題跟 stock_client/app/theme/palette.py（dark）；蠟燭色對已過 CVD 驗證（deutan ΔE 11.1）
C_WINDOW, C_SURFACE = "#1E1F22", "#26282C"
C_CARD, C_BORDER = "#2E3136", "#3A3D42"
C_TEXT, C_MUTED = "#E6E6E6", "#9AA0A6"
C_ACCENT, C_ACCENT_PRESSED = "#F1553B", "#D6452C"
C_UP, C_DOWN = "#F23645", "#089981"   # 📈 紅漲 / 📉 綠跌（港股慣例；想轉西方風格就對調呢兩個常數）

KTYPES = ["K_1M", "K_5M", "K_15M", "K_60M", "K_DAY", "K_WEEK"]
COLUMNS = ["time_key", "open", "high", "low", "close", "volume"]
BROKERS = list(BROKER_REGISTRY.keys())   # 🤖 P2：讀 registry（加新 client 自動出現）；順序 = 註冊順序，默認第一個（futu）
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


# 🤖 P8 i18n：繁中/英文切換 — 所有 UI label / status template 都經 t()（key → (zh, en)）。
#    語言名本身（"繁中"/"EN"）唔翻譯 — 用戶揀嘅就係佢自己嘅語言。
STRINGS = {
    'title':          ("stream_kline 測試 — 持續實時 K 綫串流", "stream_kline test - live K-line streaming"),
    'lbl_symbol':     ("標的代碼", "Symbol"),
    'lbl_interval':   ("K綫週期", "Interval"),
    'lbl_bars':       ("KLINE數量", "Bars"),
    'lbl_broker':     ("券商", "Broker"),
    'btn_start':      ("▶ 開始串流", "Start stream"),
    'btn_stop':       ("⏹ 停止串流", "Stop stream"),
    'panel_btn':      ("DF 結果", "Data frame"),
    'rows_info':      ("共 {total} 行 · ⏪ 最新在上", "{total} rows - newest on top"),
    'rows_trunc':     ("（顯示最後 {n} 行）", "(showing last {n})"),
    'ready':          ("就緒 · 默認 {broker} / {code} — 撳 [開始串流] 訂閱實時 K 綫",
                       "Ready - default {broker} / {code} - press [Start stream] to subscribe"),
    'index_fresh':    ("就緒 · symbol index {n} 條（{age}h 前更新）· 默認 {broker} / {code}",
                       "Ready - symbol index {n} entries (updated {age}h ago) - default {broker} / {code}"),
    'bridge_wait':    ("⏳ 橋接初始化中，請稍候再試", "Bridge initializing, please retry in a moment"),
    'need_code':      ("❌ 請輸入標的代碼（格式 MARKET.SYMBOL，如 HK.00700）",
                       "Enter a symbol (format MARKET.SYMBOL, e.g. HK.00700)"),
    'subscribing':    ("⏳ 訂閱 {code} {ktype} ×{num}（{broker}）…",
                       "Subscribing {code} {ktype} x{num} ({broker})..."),
    'baseline_ok':    ("🟢 baseline 已載入 · 共 {n} 根 — 等待實時推送…",
                       "Baseline loaded - {n} bars, waiting for live ticks..."),
    'streaming':      ("串流中 · 收到 {ticks} 次推送 · 共 {n} 根 · 最後 bar {last_t}",
                       "Streaming - {ticks} ticks received - {n} bars - last bar {last_t}"),
    'stopped':        ("⏹ 已停止 · 共收到 {ticks} 次推送（baseline 外）· 最後 {n} 根",
                       "Stopped - {ticks} ticks (excl. baseline) - last {n} bars"),
    'err_suffix':     ("（檢查：代碼格式 · OpenD/IB 連線 · 行情權限）",
                       "(check: code format, OpenD/IB connection, quote permission)"),
    'last_push':      ("最後推送 {now} · 最新K線 {bar}", "Last push {now} - latest bar {bar}"),
    'fetch_start':    ("⏳ FETCH symbol index…", "Fetching symbol index..."),
    'fetch_progress': ("⏳ FETCH {label}（{n}）…", "FETCH {label} ({n})..."),
    'fetch_ok':       ("✅ FETCH 完成 · {msg}", "FETCH done - {msg}"),
    'fetch_fail':     ("❌ FETCH 失敗：{msg}", "FETCH failed: {msg}"),
}


def t(lang, key, **kw):
    """STRINGS[key] → lang 對應 template + format（**kw）。GUI 所有可翻譯文字唯一入口。"""
    s = STRINGS[key][0] if lang == 'zh' else STRINGS[key][1]
    return s.format(**kw) if kw else s


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


def _fmt_volume(val, _pos):
    """成交量軸標籤（K/M/B）。🤖 模塊級：formatter 只喺重建靜態層時設一次。"""
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(val) >= div:
            return f"{val / div:.0f}{suf}"
    return f"{val:.0f}"


# ─────────────────────────── K 綫圖（上方巨大窗口） ───────────────────────────

class KlineChart(QWidget):
    """蠟燭圖（上 3/4）+ 成交量（下 1/4），matplotlib 嵌入 Qt。

    set_bars(rows) 推數據入嚟；widget 唔 poll、唔擁有任何 broker 狀態。
    🖱️ 手勢：左鍵拖動 = 左右平移 · 滾輪 = 以游標為中心縮放 · 右鍵 或 左鍵雙擊 = 還原縮放並跟隨最新。

    🎨 繪畫分兩層（#22）：`_rebuild_static`（全部 bar 一次過畫成 3 個 collection，只有數據/axes 變咗先做）
    · `_apply_view`（pan·zoom·hover 淨係改 limits + 重算可見 Y fit）。x 一律用絕對 index（`_s` 恒 0）。
    🎞️ 過渡（#23）：縮放 / 復位 / 松手慣性一律經 `_animate_to`（ease-out，`ANIM_MS`）—
    🤖 唔係為咗幀率：Agg 喺 1400×700 有 ~20-25 ms 底線（≈40-50 fps），順唔順係靠每幀位移細 + 有加減速。
    """

    MAX_DRAW = 300   # 跟隨模式畫最後 N 根 — 保持實時重繪流暢 + 蠟燭夠寬睇得清
    MIN_VIEW = 20    # 🖱️ 縮放下限：最少可見 bar 數
    MAX_VIEW = 1000  # 🖱️ 縮放上限：最多可見 bar 數（同時係繪製數量上限，保性能）
    ANIM_MS = 220    # 🖱️ 過渡時長：縮放 / 復位 / 慣性滑行嘅 ease-out 插值（#23）
    ANIM_STEP = 16   # ~60fps 插值步進
    PAN_EASE = 0.55  # 🖱️ 平滑平移（#24）：每幀追近游標位置嘅比例 → ~2 幀（≈30 ms）收歛，感覺唔到滯後，但每幀位移細
    Y_TICKS = 6      # 🤖 刻度文字係每幀最大開支之一（9 個 → 6 個 = −3 ms/幀），少而清晰就夠
    X_TICKS = 5      # 同上（時間標籤）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple] = []      # (time_str, o, h, l, c, v)，舊 → 新
        self._hover_idx: int | None = None
        self._view: tuple[float, float] | None = None   # 🖱️ 用戶平移/縮放後嘅視窗範圍（絕對 index 座標）；None = 跟隨最新
        self._drag: tuple[float, float, float] | None = None  # 🖱️ 拖緊：(按下時游標絕對 x、視窗 xmin、xmax)
        self._s = 0                        # 🤖 繪畫一律用絕對 index → 呢個基準恒為 0；保留欄位係為咗子类嘅 slice 契約
        # 🖱️ 順暢度（#22）：分兩層 —— 靜態層（全部 bar 一次過畫，3 個 collection）/ 視窗層（pan·zoom 只改 limits）
        self._ohlcv: dict[str, np.ndarray] = {}   # 全長度 o/h/l/c/v 陣列（set_bars 轉一次，之後唔再逐幀重砌）
        self._static_dirty = True          # 數據/axes 變咗 → 下一幀重建靜態層
        self._theme: tuple = ()            # 靜態層用咗嘅配色（theme 換咗要自動重建，見 _rebuild_static）
        self._arts: list = []              # 靜態層 artist（只有重建時先清）
        self._tag_txt = None               # 現價標注（預建，pan/zoom 只改位置）
        self._cross = None                 # 🖱️ hover crosshair（預建，郁滑鼠只 set_xdata）
        self._paint_pending = False        # 🖱️ 手勢節流旗標
        self._anim = None                  # 🖱️ 進行中嘅過渡：(t0, 起始 view, 目標 view, 終止後否返跟隨)
        self._anim_running = False         # 🖱️ 插值 timer 鏈喺唔喺度（唔使砌第二條）
        self._drag_hist = []               # 🖱️ 拖動樣本 [(t, 絕對 x), ...] → 松手算慣性
        self._pan_target = None            # 🖱️ 平滑平移（#24）：游標要求嘅 view，逐幀追近
        self._pan_running = False          # 🖱️ 追近 timer 鏈喺唔喺度
        self._y_lock = None                # 🖱️ 拖動期間鎖定嘅 (ax.ylim, axv.ylim) → 唔會邊拖邊重縮放

        fig = Figure(facecolor=C_SURFACE, edgecolor="none")
        gs = fig.add_gridspec(2, 1, height_ratios=[3, 1], hspace=0.06,
                              left=0.055, right=0.985, top=0.97, bottom=0.09)
        self.ax = fig.add_subplot(gs[0])
        self.axv = fig.add_subplot(gs[1], sharex=self.ax)
        self.canvas = FigureCanvasQTAgg(fig)
        self._style_ax(self.ax)
        self._style_ax(self.axv)

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
        # 🤖 一次過轉全長度陣列：之後 pan/zoom/hover 都唔再逐幀 from rows 砌 numpy（#22 A）
        if self._rows:
            cols = list(zip(*self._rows))
            self._ohlcv = {k: np.array(cols[i], dtype=float)
                           for i, k in enumerate('ohlcv', start=1)}
        else:
            self._ohlcv = {}
        self._static_dirty = True
        self._y_lock = None              # 🖱️ 換數據 = 尺度要重 fit
        self._redraw()

    def clear(self):
        self._view = None
        self._drag = None
        self._anim = None
        self._pan_target = None
        self._drag_hist = []
        self.set_bars([])

    # --- 繪製 -----------------------------------------------------------------
    @staticmethod
    def _style_ax(ax):
        ax.set_facecolor(C_SURFACE)
        for spine in ax.spines.values():
            spine.set_color(C_BORDER)
        ax.tick_params(colors=C_MUTED, labelsize=8)
        ax.grid(True, color=C_BORDER, linewidth=0.6, alpha=0.5)

    # --- 兩層繪畫（#22：縮放/平移順唔順嘅關鍵） --------------------------------
    # 🤖 舊做法：每幀 `ax.clear()` + `ax.bar` → 每根 bar 一個 Rectangle patch（300 根 = 600 patches），
    #    實測一幀 241 ms、郁吓滑鼠 181 ms → 用戶感受到「非常唔順」。
    # 家陣：靜態層（全部 bar 一次過畫 = 3 個 collection）只喺數據/axes 變咗先重建；
    #       pan·zoom·hover 行視窗層（只改 limits + 由可見 slice 重算 Y fit）→ 對照實測 47 ms。
    def _redraw(self):
        """完整一幀（數據 / 指標配置變化用）。手勢請用 `_request_redraw()`。"""
        self._frame()
        self.canvas.draw_idle()

    def _frame(self):
        """一幀嘅主體（唔計上屏）。子类重寫呢個 hook 就自動接到同一套手勢節流。"""
        self._rebuild_static()
        self._apply_view()

    def _request_redraw(self):
        """🖱️ C：手勢節流 —— 一次拖動/滾輪 Qt 會送幾十個 event。
        狀態 + limits 即時更新（手勢數學永遠跟到最新、測試都係同步），上屏合併到 ~60fps。"""
        self._frame()
        if self._paint_pending:
            return
        self._paint_pending = True
        QTimer.singleShot(16, self._flush_paint)

    def _flush_paint(self):
        self._paint_pending = False
        self.canvas.draw_idle()

    # --- 🖱️ 平滑過渡（#23）：縮放 / 復位 / 慣性滑行都行呢條路 ----------------------------
    # 🤖 唔係為咗「快」：Agg 喺 1400×700 有 ~20-25 ms 底線（≈40-50 fps；真 60fps 要換 renderer）。
    #    過渡嘅價值係**每幀位移細 + 有加減速** → 同樣幀率下睇落係動畫，唔係跳格。
    def _animate_to(self, target, follow_at_end=False):
        """由目前生效嘅視窗 ease-out 插值去 target。進行中就由「而家顯示緊嘅位置」接續 → 連續滾輪唔會跳。"""
        self._flush_pan()                 # 🖱️ 過渡要由實際顯示緊嘅位置接續
        start = self._view if self._view is not None else self._view_abs()
        self._anim = (time.perf_counter(), (float(start[0]), float(start[1])),   # 🤖 手勢/動畫計時一律 perf_counter：Windows 嘅 monotonic 實測只有 ~16 ms 分辨率（會 dt=0）
                      (float(target[0]), float(target[1])), follow_at_end)
        if not self._anim_running:
            self._anim_running = True
            QTimer.singleShot(self.ANIM_STEP, self._anim_tick)

    def _anim_tick(self):
        self._anim_running = False
        if self._anim is None or not self._rows:
            return
        t0, start, target, follow_at_end = self._anim
        p = (time.perf_counter() - t0) * 1000.0 / self.ANIM_MS
        if p >= 1.0:
            self._anim = None
            self._view = None if follow_at_end else target   # 跟隨模式要還原返 None，新 bar 先至跟到
            self._y_lock = None                              # 🖱️ #24 滑行完結 → 一次過重縮放
            self._frame()
            self.canvas.draw_idle()
            return
        e = 1.0 - (1.0 - min(p, 1.0)) ** 3                   # ease-out cubic
        self._view = (start[0] + (target[0] - start[0]) * e,
                      start[1] + (target[1] - start[1]) * e)
        self._frame()
        self.canvas.draw_idle()
        self._anim_running = True
        QTimer.singleShot(self.ANIM_STEP, self._anim_tick)

    def _cancel_anim(self):
        self._anim = None

    # --- 🖱️ 平滑平移（#24）：拖動唔再 1:1 跳格，而係逐幀追近游標 ---------------------------
    # 🤖 同 #23 同一個道理：55 fps 下快速拖動每幀跳幾根 → 睇落跳格。逐幀指數追近令位移細而連續。
    #    用「逐幀比例」而唔係時鐘：拖動係連續 input，時鐘插值會越拖越落後。
    def _pan_to(self, target):
        self._pan_target = (float(target[0]), float(target[1]))
        if not self._pan_running:
            self._pan_running = True
            QTimer.singleShot(self.ANIM_STEP, self._pan_tick)

    def _pan_tick(self):
        self._pan_running = False
        if self._pan_target is None or not self._rows:
            return
        cx, cy = self._view if self._view is not None else self._view_abs()
        tx, ty = self._pan_target
        if abs(tx - cx) < 0.02 and abs(ty - cy) < 0.02:      # 追到就即刻貼實，唔留殘差
            self._view = self._pan_target
            self._pan_target = None
            self._frame()          # 🤖 一定要行 _frame（子类靠呢個 hook 重畫指標疊加）
            self.canvas.draw_idle()
            return
        self._view = (cx + (tx - cx) * self.PAN_EASE, cy + (ty - cy) * self.PAN_EASE)
        self._frame()
        self.canvas.draw_idle()
        self._pan_running = True
        QTimer.singleShot(self.ANIM_STEP, self._pan_tick)

    def _flush_pan(self):
        """🖱️ 未追完嘅平移即刻貼實。任何要讀取「而家視窗」嘅動作（松手 / 縮放 / 再起 drag / 過渡）
        之前都要先咁做，否則會由一個落後咗嘅位置開始計。"""
        if self._pan_target is not None:
            self._view = self._pan_target
            self._pan_target = None

    def _lock_y(self):
        """🖱️ #24 手勢開始 → 凍結而家嘅 Y 尺度（拖動／滑行期間唔再 refit）。"""
        self._y_lock = (tuple(self.ax.get_ylim()), tuple(self.axv.get_ylim()))

    def _unlock_y(self):
        """解除凍結並一次過重縮放（松手 / 縮放 / 換數據）。"""
        if self._y_lock is None:
            return
        self._y_lock = None
        self._frame()
        self.canvas.draw_idle()

    @staticmethod
    def _rect_path(x, half, y0, y1):
        """一批矩形 → **一個** compound Path（每塊 MOVETO + LINETO×3 + CLOSEPOLY）。
        🤖 一定要 5 個頂點（第 5 個 = 起點重複）：`CLOSEPOLY` 唔會用自己嗰個頂點，
        4 點配 [M,L,L,CLOSE] 實測得返一半面積 = **三角形**（用戶抓到：OB 變咗楔形）。"""
        v = np.stack([np.column_stack([x - half, y0]), np.column_stack([x + half, y0]),
                      np.column_stack([x + half, y1]), np.column_stack([x - half, y1]),
                      np.column_stack([x - half, y0])], axis=1)
        return Path(v.reshape(-1, 2),
                    np.tile([Path.MOVETO, Path.LINETO, Path.LINETO, Path.LINETO,
                             Path.CLOSEPOLY], x.shape[0]))

    @staticmethod
    def _segs_path(x, y0, y1):
        """一批豎直段（影線）→ **一個** compound Path（MOVETO/LINETO 成對）。"""
        verts = np.empty((x.shape[0] * 2, 2))
        verts[0::2] = np.column_stack([x, y0])
        verts[1::2] = np.column_stack([x, y1])
        return Path(verts, np.tile([Path.MOVETO, Path.LINETO], x.shape[0]))

    def _rebuild_static(self):
        """靜態層：一次過畫全部 bar（x = 絕對 index）+ 預建現價線/標注/crosshair。"""
        # 🤖 配色（C_*）係 draw-time 先讀嘅模組級常數，而家砌進 collection 就固定咗 →
        #    theme 換色（kline_page / quotes_page recipe 都係「改 gk.C_* + _redraw()」）要識得自己重建
        theme = (C_UP, C_DOWN, C_SURFACE, C_BORDER, C_ACCENT, C_MUTED)
        if not self._static_dirty and theme == self._theme:
            return
        self._theme = theme
        self._static_dirty = False
        for a in self._arts:
            try:
                a.remove()           # 只清自己砌嘅；指標 artist 由子类管
            except Exception:        # axes 已被重建（子类 _build_axes）→ 唔喺度，唔使清
                pass
        self._arts = []
        self._tag_txt = self._cross = None
        ax, axv = self.ax, self.axv
        self._style_ax(ax)
        self._style_ax(axv)
        n = len(self._rows)
        if n == 0:
            self.readout.setText("—")
            return

        x = np.arange(n, dtype=float)
        o, h, l, c, v = (self._ohlcv[k] for k in 'ohlcv')
        half = 0.35 if n > 2 else 0.6                  # 實體闊 0.7（同舊 ax.bar width 一致）
        # 🤖 得兩種顏色（漲/跌）→ 每種顏色砌成**一個** compound Path：path 數由 O(N) 變 6 個
        #    （1200 根實測 −4 ms/幀）。逐 bar 顏色本来就等於逐方向，冇損失。
        up_i, dn_i = np.where(c >= o)[0], np.where(c < o)[0]
        lo_b, hi_b = np.minimum(o, c), np.maximum(o, c)

        def _add(target, mk, fill, **kw):
            specs = [(mk(i), col) for i, col in ((up_i, C_UP), (dn_i, C_DOWN)) if i.size]
            if not specs:
                return
            cols = [col for _p, col in specs]
            pc = PathCollection([p for p, _ in specs],
                                facecolors=cols if fill else "none",
                                edgecolors="none" if fill else cols, **kw)
            target.add_collection(pc)
            self._arts.append(pc)

        _add(ax, lambda i: self._segs_path(x[i], l[i], h[i]), False, linewidths=1.0)
        _add(ax, lambda i: self._rect_path(x[i], half, lo_b[i], hi_b[i]), True)
        _add(axv, lambda i: self._rect_path(x[i], half, np.zeros(i.size), v[i]), True, alpha=0.55)

        # 📍 現價水平虛線 + 標注 + hover crosshair：預建，之後只改位置/可見性
        last_close = float(c[-1])
        self._arts.append(ax.axhline(last_close, color=C_ACCENT, linewidth=0.9,
                                     linestyle="--", alpha=0.85))
        self._tag_txt = ax.text(0.0, last_close, _fmt_price(last_close),
                                ha="center", va="center", fontsize=8, color="#FFFFFF",
                                fontweight="bold",
                                bbox=dict(boxstyle="round,pad=0.25", fc=C_ACCENT, ec="none"))
        self._arts.append(self._tag_txt)
        self._cross = ax.axvline(0, color=C_MUTED, linewidth=0.8, linestyle="--",
                                 alpha=0.8, visible=False)
        self._arts.append(self._cross)
        axv.yaxis.set_major_formatter(FuncFormatter(_fmt_volume))
        # 🤖 刻度文字係每幀最大開支之一（實測 y 標籤 9 → 6 個 = −3 ms/幀）：少而清晰就夠
        ax.yaxis.set_major_locator(MaxNLocator(self.Y_TICKS, prune='both'))
        axv.yaxis.set_major_locator(MaxNLocator(3, prune='both'))

    def _apply_view(self):
        """視窗層：pan/zoom/hover 全部只行呢度 —— limits + 由可見 slice 重算 Y fit + ticks + 現價 tag。"""
        ax, axv = self.ax, self.axv
        rows_all = self._rows
        n = len(rows_all)
        if n == 0:
            return

        # 🖱️ 視窗範圍（絕對 index 座標）：None = 跟隨最新（原行為）；否則用戶平移/縮放後嘅位置
        # 🤖 動畫進行中唔好碰 `_view`：跟隨模式嘅起點本身 xmax > n-0.5（現價 tag gutter），
        #    逐幀插值都會撞中呢個 guard → 成段過渡被還原返跟隨（實測：縮到全部數據 = 郁吓唔郁）。
        #    target 本身已經 clamp 喺当前 n 之內，所以動畫期間可以安全跳過。
        # 🤖 view 出界（數據變少咗 / 取消過渡留低嘅插值中間態）→ clamp 返入數據範圍、**保留用戶嘅縮放級別**。
        #    舊做法係整個丟返跟隨模式：用戶會突然被拉返最新 + 換咗尺度（#24 測試暴露）。
        #    動畫／平滑平移進行中唔碰：跟隨模式嘅起點本身 xmax > n-0.5（現價 tag gutter），逐幀都會撞中。
        if (self._view is not None and self._anim is None and self._pan_target is None
                and (self._view[1] > n - 0.5 + 1e-9 or self._view[0] < -0.5 - 1e-9)):
            self._view = self._clamp_view(self._view, n)
        if self._view is None:
            xmin_a, xmax_a, s = self._follow_target()              # 右側現價 tag 空間（原行為）
            tag_x = (xmax_a + n - 1) / 2                           # gutter 中間（原行為）
        else:
            xmin_a, xmax_a = self._view
            s = max(0, int(math.floor(xmin_a)))
            tag_x = xmax_a - max(2.5, (xmax_a - xmin_a) * 0.04)    # 視窗右緣內側
        e = min(n, int(math.ceil(xmax_a)) + 1)
        if e <= s:   # 防禦：視窗完全出界（理論上唔會發生）→ 返跟隨範圍
            s, e = max(0, n - self.MAX_DRAW), n
            xmin_a, xmax_a = s - 0.6, n - 0.5
            tag_x = (xmin_a + xmax_a) / 2
        self._s = 0    # 🤖 絕對 index 繪畫 → 本地 == 絕對（子类 slice 契約照樣啱）

        ax.set_xlim(xmin_a, xmax_a)
        if self._y_lock is not None:
            # 🖱️ #24 拖動／滑行進行中凍結 Y 尺度：邊拖邊 refit = 橫移 + 同時垂直缩放（實測每 25 個
            #    event 變 0.93–1.05×）→ 成張圖「郁郁下彈下」，呢個先係「平移唔順」嘅主因，唔係幀率。
            ax.set_ylim(*self._y_lock[0])
            axv.set_ylim(*self._y_lock[1])
        else:
            # Y 軸自動 fit 可見範圍（平移去睇舊 bar 時唔會俾全表 min/max 壓扁）
            l, h, v = (self._ohlcv[k2] for k2 in 'lhv')
            lo, hi = float(l[s:e].min()), float(h[s:e].max())
            pad = (hi - lo) * 0.06 or abs(hi) * 0.01 or 1.0
            ax.set_ylim(lo - pad, hi + pad)
            axv.set_ylim(0, (float(v[s:e].max()) or 1.0) * 1.15)

        if self._tag_txt is not None:
            close = float(self._ohlcv['c'][-1])
            self._tag_txt.set_text(_fmt_price(close))
            self._tag_txt.set_position((tag_x, close))

        # X 軸：整數 index + 稀疏時間標籤（時間刻度由下方 volume 軸帶）— 絕對 index 即座標
        tick_i = self._view_ticks(xmin_a, xmax_a, n)
        axv.set_xticks(tick_i)
        axv.set_xticklabels([_short_time(rows_all[i][0]) for i in tick_i], fontsize=8)
        ax.tick_params(labelbottom=False)

        # crosshair + readout（無懸停時顯示最新一根）— hover idx 係絕對 index
        self._update_hover()
        i = self._hover_idx
        self._set_readout(rows_all[i] if i is not None and 0 <= i < n else rows_all[-1])

    def _update_hover(self):
        """B：crosshair 只改一條預建線嘅 x —— 郁滑鼠唔再觸發任何 artist 重建（舊做法 = full redraw）。"""
        if self._cross is None:
            return
        i = self._hover_idx
        if i is None or not 0 <= i < len(self._rows):
            self._cross.set_visible(False)
        else:
            self._cross.set_visible(True)
            self._cross.set_xdata([i, i])

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

    # --- 懸停 / 手勢（拖動平移 · 滾輪縮放 · 右鍵/雙擊復位） -----------------------
    def _follow_target(self):
        """跟隨最新嘅生效範圍（絕對 index）：最後 MAX_DRAW 根 + 右側現價 tag 空間。回 (xmin, xmax, s)。"""
        n = len(self._rows)
        m = min(n, self.MAX_DRAW)
        s = n - m
        return s - 0.6, n - 1 + max(8.0, m * 0.04), s

    def _view_abs(self):
        """目前生效嘅視窗範圍（絕對 index 座標）。"""
        return self._view if self._view is not None else self._follow_target()[:2]

    @staticmethod
    def _clamp_view(view, n):
        """view 平移返入 `[-0.5, n-0.5]`（保留寬度 = 用戶嘅縮放級別）；寬度大過全部數據 → 顯示全部。"""
        w = view[1] - view[0]
        if w >= n:
            return (-0.5, max(n - 0.5, 0.5))
        xmin = max(-0.5, min(view[0], n - 0.5 - w))
        return (xmin, xmin + w)

    def _view_ticks(self, xmin_a, xmax_a, n):
        """可見窗內嘅整數 x 刻度（絕對 index）。
        🤖 必須 clamp 喺 limits 之內：`set_xticks` 會將出界嘅刻度撐開 axes limits（實測 ±1 根），
        過渡動畫每幀都俾呢一下抖動拖歪 → 終點永遠收唔到 target（#23）。"""
        t0 = max(0, int(math.ceil(xmin_a)))
        t1 = min(n - 1, int(math.floor(xmax_a)))
        if t1 < t0:
            return []
        return sorted(set(np.linspace(t0, t1, min(self.X_TICKS, t1 - t0 + 1)).astype(int).tolist()))

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
                target = (-0.5, max(n - 0.5, 0.5))
            elif xmin < -0.5:             # clamp 住數據左緣
                target = (-0.5, -0.5 + w)
            elif xmax > n - 0.5:          # clamp 住數據右緣
                target = (n - 0.5 - w, n - 0.5)
            else:
                target = (xmin, xmax)
            self._hover_idx = None
            now = time.perf_counter()                    # 🖱️ 攞樣算松手嗰陣嘅速度（慣性）
            self._drag_hist = [(t, xx) for t, xx in self._drag_hist if now - t <= 0.12][-6:]
            self._drag_hist.append((now, ev.xdata + self._s))
            self._pan_to(target)                         # 🖱️ #24：設目標，逐幀追近（唔再逐 event 重算 limits/Y/ticks）
            return
        # 🖱️ B：hover crosshair —— 只改預建線嘅 x + readout，唔返嚟重建任何 artist
        n = len(self._rows)
        idx = max(0, min(n - 1, int(round(ev.xdata + self._s))))
        if idx != self._hover_idx:
            self._hover_idx = idx
            self._update_hover()
            self._set_readout(self._rows[idx])
            self.canvas.draw_idle()

    def _on_leave(self, ev):
        if self._hover_idx is not None:
            self._hover_idx = None
            self._update_hover()
            self.canvas.draw_idle()

    def _on_press(self, ev):
        if not self._rows or ev.inaxes not in (self.ax, self.axv) or ev.xdata is None:
            return
        if ev.button == 3:                # 🖱️ 右鍵 → 平滑復位跟隨最新
            self._hover_idx = None
            self._animate_to(self._follow_target()[:2], follow_at_end=True)
            return
        if ev.button == 1 and getattr(ev, 'dblclick', False):
            # 🖱️ 左鍵連擊兩下 → 還原縮放 + 返到最新 K 柱（用戶要求：所有 K 線圖都要有）
            # 🤖 必須喺開始拖動之前處理：第一次 click 已經設咗 _drag，唔清掉會變到「復位完郁咗一下」
            self._drag = None
            self._drag_hist = []
            self._hover_idx = None
            self._animate_to(self._follow_target()[:2], follow_at_end=True)
            return
        if ev.button != 1:
            return
        self._cancel_anim()               # 🖱️ 手郁咗就即刻停返過渡（跟手優先）
        self._flush_pan()                 # 🖱️ drag 要由實際視窗開始
        self._drag_hist = []
        self._lock_y()                    # 🖱️ #24 拖動期間唔再邊拖邊重縮放
        xmin_a, xmax_a = self._view_abs()
        self._drag = (ev.xdata + self._s, xmin_a, xmax_a)

    def _on_release(self, ev):
        self._drag = None
        self._flush_pan()                 # 🖱️ 松手要企喺用戶實際拖到嘅位置，慣性由呢度開始
        self._inertia()
        if self._anim is None:            # 🖱️ 冇滑行 → 松手即刻一次過重縮放（有滑行就等滑行完先 refit）
            self._unlock_y()

    def _inertia(self):
        """🖱️ 松手後順住拖動方向滑行一段（都係 ease-out）— 拖動先至有「甩」嘅手感，唔係急停。
        🤖 只係用最近 ≤120 ms 嘅樣本（慢拖 / 停低先至松手 → 速度自然接近 0，唔會滑）。"""
        now = time.perf_counter()
        smp = [(t, xx) for t, xx in self._drag_hist if now - t <= 0.12]
        self._drag_hist = []
        if len(smp) < 2 or self._view is None:
            return
        dt = smp[-1][0] - smp[0][0]
        if dt <= 0:
            return
        v = (smp[-1][1] - smp[0][1]) / dt          # 絕對 index / 秒
        if abs(v) < 3.0:
            return
        xmin, xmax = self._view
        w = xmax - xmin
        n = len(self._rows)
        d = v * 0.20                               # 滑行量 ≈ 200 ms 速度（ease-out 會再衰減）
        xmin2, xmax2 = xmin + d, xmax + d
        if xmin2 < -0.5:
            xmin2, xmax2 = -0.5, -0.5 + w
        elif xmax2 > n - 0.5:
            xmin2, xmax2 = n - 0.5 - w, n - 0.5
        self._animate_to((xmin2, xmax2))

    def _on_scroll(self, ev):
        if not self._rows or ev.inaxes not in (self.ax, self.axv) or ev.xdata is None:
            return
        n = len(self._rows)
        self._flush_pan()                             # 🖱️ 縮放要由實際視窗計，唔係追緊緊嘅位置
        self._y_lock = None                           # 🖱️ 縮放 = 明確要重縮放 → 解除拖動凍結
        xmin_a, xmax_a = self._view_abs()
        x0 = ev.xdata + self._s                       # 🖱️ 縮放中心 = 游標位置（絕對座標）
        w = max(self.MIN_VIEW, min((xmax_a - xmin_a) * 1.3 ** (-ev.step), self.MAX_VIEW))
        self._hover_idx = None
        if w >= n:                                    # 已經縮到全部數據 → 顯示全部
            self._animate_to((-0.5, max(n - 0.5, 0.5)))
            return
        frac = (x0 - xmin_a) / (xmax_a - xmin_a) if xmax_a > xmin_a else 0.5
        xmin2, xmax2 = x0 - frac * w, x0 + (1 - frac) * w
        if xmin2 < -0.5:                              # 縮放後 clamp 返數據範圍內
            self._animate_to((-0.5, -0.5 + w))
        elif xmax2 > n - 0.5:
            self._animate_to((n - 0.5 - w, n - 0.5))
        else:
            self._animate_to((xmin2, xmax2))          # 🖱️ 平滑縮放（連續滾輪由目前位置接續）


# ─────────────────────── DF 結果（下方可折疊窗口） ───────────────────────

class CollapsiblePanel(QWidget):
    """標題列（折疊按鍵 + 行數 info）+ QTableWidget 顯示 DF 結果。"""

    TABLE_MAX = 200  # 只顯示最後 N 行 — 保持實時更新流暢

    def __init__(self, columns, parent=None):
        super().__init__(parent)
        self.columns = list(columns)
        self._collapsed = False
        self.lang = 'zh'          # 🤖 P8 i18n：set_lang() 切換（MainWindow._retranslate）
        self._info_total = 0      # 行數 info 重算用（語言切換時要重新翻譯）
        self._info_shown = 0

        head = QHBoxLayout()
        self.btn = QPushButton("▼ " + t('zh', 'panel_btn'))
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
        self._refresh_btn_text()

    # 🤖 P8 i18n：語言切換 → 按鍵 + info 重新翻譯（行數狀態保留喺 _info_total/_info_shown）
    def set_lang(self, lang):
        self.lang = lang
        self._refresh_btn_text()
        self._refresh_info()

    def _refresh_btn_text(self):
        self.btn.setText(("▲ " if self._collapsed else "▼ ") + t(self.lang, 'panel_btn'))

    def _refresh_info(self):
        total, shown = self._info_total, self._info_shown
        if not total:
            self.info.setText("")
            return
        s = t(self.lang, 'rows_info', total=total)
        if total > shown:
            s += t(self.lang, 'rows_trunc', n=shown)
        self.info.setText(s)

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
        self._info_total, self._info_shown = len(records), len(show)
        self._refresh_info()

    def clear(self):
        self.table.setRowCount(0)
        self._info_total = 0
        self._info_shown = 0
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


# ─────────────────────── 🤖 P8：FETCH（本地 symbol index — 模糊輸入見 gateway/symbol_input） ───────────────────────

class FetchThread(QThread):
    """🤖 P8 FETCH：background 跑 SymbolDirectory.fetch（blocking OpenD 枚舉 ~5s）— GUI thread 唔會卡。
       progress/done 都係 signal（跨線程只准 plain types）。"""

    progress = Signal(str, int)   # (label e.g. "US/STOCK", rows kept)
    done = Signal(bool, str)      # (ok, message)

    def __init__(self, directory, parent=None):
        super().__init__(parent)
        self._dir = directory

    def run(self):
        try:
            ok, msg = self._dir.fetch(markets=('US', 'HK'),
                                      progress_cb=lambda label, n: self.progress.emit(label, int(n)))
            self.done.emit(bool(ok), str(msg))
        except Exception as e:   # 🤖 任何異常 → honest done(False)（GUI 靠 signal 收復狀態）
            logging.exception("symbol index fetch failed")
            self.done.emit(False, f"{type(e).__name__}: {e}")


# ─────────────────────────────── 主窗口 ───────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1150, 780)
        self._streaming = False   # E2E / 狀態用：有冇串流進行中
        self.lang = 'zh'          # 🤖 P8 i18n：繁中/英文（lang_combo → _retranslate）
        self.directory = get_directory()   # 🤖 P8：本地 symbol index（disk cache）

        central = QWidget()
        self.setCentralWidget(central)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # ── 最上方：大字現價列（標的 · 週期 | 漲跌 | 大字現價；資料刷新閃一下）──
        price_row = QHBoxLayout()
        self.sym_label = QLabel("—")   # 🤖 實際文字由 _retranslate()/_sym_text() 填（要等 code_edit 建立）
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

        # ── 中間：控制列（標的代碼 / K 綫週期 / KLINE 數量 / 券商 / 測試按鍵 / 語言 / FETCH）+ 狀態 ──
        ctrl = QHBoxLayout()
        self.lbl_symbol = QLabel(t('zh', 'lbl_symbol'))   # 🤖 存 attribute — _retranslate 要改文字
        self.code_edit = QLineEdit(DEFAULT_CODE)
        self.code_edit.setObjectName("code_edit")         # E2E hook
        # 🤖 P8 模糊輸入：一律經 gateway/symbol_input（本地 index + pass-through；揀咗淨返 CODE 入欄）
        self.completer = attach_symbol_input(self.code_edit, make_search(self.directory),
                                             lang=self.lang)
        self.ktype_combo = QComboBox()
        self.ktype_combo.addItems(KTYPES)
        self.ktype_combo.setCurrentText("K_1M")
        self.num_spin = QSpinBox()
        self.num_spin.setRange(1, 5000)
        self.num_spin.setValue(200)
        self.lbl_interval = QLabel(t('zh', 'lbl_interval'))
        self.lbl_bars = QLabel(t('zh', 'lbl_bars'))
        self.broker_combo = QComboBox()
        self.broker_combo.addItems(BROKERS)
        self.broker_combo.setCurrentText("futu")   # 🏦 默認 futu
        self.lbl_broker = QLabel(t('zh', 'lbl_broker'))
        self.test_btn = QPushButton(t('zh', 'btn_start'))
        self.test_btn.setMinimumWidth(120)
        self.lang_combo = QComboBox()              # 🤖 P8 i18n：語言名本身唔翻譯（繁中/EN）
        self.lang_combo.setObjectName("lang_combo")
        self.lang_combo.addItems(["繁中", "EN"])
        self.fetch_btn = QPushButton("⟳ FETCH")    # 🤖 P8：手動 refresh symbol index（兩語同文）
        self.fetch_btn.setObjectName("fetch_btn")
        ctrl.addWidget(self.lbl_symbol)
        ctrl.addWidget(self.code_edit, 1)
        ctrl.addWidget(self.lbl_interval)
        ctrl.addWidget(self.ktype_combo)
        ctrl.addWidget(self.lbl_bars)
        ctrl.addWidget(self.num_spin)
        ctrl.addWidget(self.lbl_broker)
        ctrl.addWidget(self.broker_combo)
        ctrl.addWidget(self.test_btn)
        ctrl.addWidget(self.lang_combo)
        ctrl.addWidget(self.fetch_btn)
        lay.addLayout(ctrl)

        self.status_label = QLabel("")
        lay.addWidget(self.status_label)

        # ── 下方：可折疊 DF 結果窗口 ──
        self.panel = CollapsiblePanel(COLUMNS)
        lay.addWidget(self.panel)

        self.thread = LoopThread()
        self.thread.worker_ready.connect(self._on_worker_ready)
        self.thread.start()
        self.test_btn.clicked.connect(self.on_test_clicked)
        self.lang_combo.currentIndexChanged.connect(self.on_lang_changed)
        self.fetch_btn.clicked.connect(self.start_fetch)
        self.code_edit.textChanged.connect(self._on_code_changed)

        # 🤖 P8 startup：cache 新鮮 → 顯示 index 狀態；stale/冇 cache → background auto-fetch
        self.fetch_thread = None
        if self.directory.entries and not self.directory.is_stale:
            age = self._index_age_hours()
            self._set_status(t(self.lang, 'index_fresh', n=len(self.directory.entries),
                               age=age if age is not None else '?',
                               broker=BROKERS[0], code=DEFAULT_CODE), "muted")
        else:
            self._set_status(t(self.lang, 'ready', broker=BROKERS[0], code=DEFAULT_CODE), "muted")
            self.start_fetch()

        self._retranslate()   # 🤖 title / labels / sym_label（要等上面所有 widget 建立完）

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
        self.sym_label.setText(self._sym_text())   # 🤖 P8：+ 本地 index 顯示名（跟語言）
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

    # --- 🤖 P8：symbol index（fuzzy 輸入 + FETCH + i18n） ------------------------
    def _sym_text(self):
        """大字現價列左邊：CODE · KTYPE + 本地 index 顯示名（跟 GUI 語言 — 用戶要求名字跟隨切換）。"""
        code = self.code_edit.text().strip()
        ktype = self.ktype_combo.currentText()
        name = self.directory.display_name(code, self.lang) if code else ''
        return f"{code} · {ktype}" + (f"  {name}" if name else "")

    def _on_code_changed(self, text):
        self.sym_label.setText(self._sym_text())   # 🤖 模糊輸入由 attach_symbol_input 接手（debounce → search）

    def on_lang_changed(self, _idx):
        self.lang = 'zh' if self.lang_combo.currentIndex() == 0 else 'en'
        self._retranslate()

    def _retranslate(self):
        L = self.lang
        self.setWindowTitle(t(L, 'title'))
        self.lbl_symbol.setText(t(L, 'lbl_symbol'))
        self.lbl_interval.setText(t(L, 'lbl_interval'))
        self.lbl_bars.setText(t(L, 'lbl_bars'))
        self.lbl_broker.setText(t(L, 'lbl_broker'))
        self.test_btn.setText(t(L, 'btn_stop' if self._streaming else 'btn_start'))
        self.panel.set_lang(L)
        self.completer.lang = L
        self.sym_label.setText(self._sym_text())

    def _index_age_hours(self):
        fa = self.directory.fetched_at
        if not fa:
            return None
        try:
            return int((datetime.now().astimezone() - datetime.fromisoformat(fa)).total_seconds() // 3600)
        except ValueError:
            return None

    def start_fetch(self):
        """🤖 P8 FETCH：background refresh symbol index（startup auto-fetch + 手動 FETCH 掣共用）。"""
        if self.fetch_thread is not None and self.fetch_thread.isRunning():
            return   # 已經 fetch 緊 — 唔好開第二條 OpenD 連線
        self.fetch_btn.setEnabled(False)
        self._set_status(t(self.lang, 'fetch_start'), "muted")
        self.fetch_thread = FetchThread(self.directory)
        self.fetch_thread.progress.connect(self._on_fetch_progress)
        self.fetch_thread.done.connect(self._on_fetch_done)
        self.fetch_thread.start()

    def _on_fetch_progress(self, label, n):
        self._set_status(t(self.lang, 'fetch_progress', label=label, n=n), "muted")

    def _on_fetch_done(self, ok, msg):
        self.fetch_btn.setEnabled(True)
        if ok:
            self._set_status(t(self.lang, 'fetch_ok', msg=msg), "ok")
            self.sym_label.setText(self._sym_text())   # 🤖 index 更新咗 → 顯示名可能變
        else:
            self._set_status(t(self.lang, 'fetch_fail', msg=msg), "err")

    # --- 測試按鍵（開始 / 停止串流） ---------------------------------------------
    def on_test_clicked(self):
        if self._streaming:
            self.thread.stop_stream()
            return
        if not self.thread.worker:
            self._set_status(t(self.lang, 'bridge_wait'), "muted")
            return
        code = self.code_edit.text().strip()
        if not code:
            self._set_status(t(self.lang, 'need_code'), "err")
            return
        ktype = self.ktype_combo.currentText()
        num = self.num_spin.value()
        broker = self.broker_combo.currentText().lower()
        self._streaming = True
        for w in (self.code_edit, self.ktype_combo, self.num_spin, self.broker_combo):
            w.setEnabled(False)
        self.test_btn.setText(t(self.lang, 'btn_stop'))
        self._set_status(
            t(self.lang, 'subscribing', code=code, ktype=ktype, num=num, broker=broker), "muted")
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
                t(self.lang, 'last_push', now=time.strftime('%H:%M:%S'), bar=_short_time(last_t)))
            if phase == "baseline":
                self._set_status(t(self.lang, 'baseline_ok', n=p['n_rows']), "ok")
            else:
                self._set_status(
                    t(self.lang, 'streaming', ticks=p['ticks'], n=p['n_rows'], last_t=last_t), "muted")
        elif phase == "done":
            self.time_label.setText("—")
            self._streaming = False
            for w in (self.code_edit, self.ktype_combo, self.num_spin, self.broker_combo):
                w.setEnabled(True)
            self.test_btn.setText(t(self.lang, 'btn_start'))
            if p.get("error"):
                self._set_status(f"❌ {p['error']}" + t(self.lang, 'err_suffix'), "err")
            else:
                self._set_status(
                    t(self.lang, 'stopped', ticks=p['ticks'], n=p['n_rows']), "ok")

    def closeEvent(self, e):
        # 統一清理：FETCH（如跑緊）→ cancel consumer → broker 端拔線 → BrokerClient.__aexit__ → stop loop
        if self.fetch_thread is not None and self.fetch_thread.isRunning():
            self.fetch_thread.wait(3000)   # 🤖 等 fetch 寫完 cache（atomic replace，唔會留半份）
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

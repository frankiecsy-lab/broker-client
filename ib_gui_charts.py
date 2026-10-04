# =====================================================================
# ib_gui_charts.py — 8 圖表 K線 LIVE 儀表板（NVDA 螢幕讀取器友善）
#
# 用法：
#   python ib_gui_charts.py            # 開啟視窗（需 IB Gateway 跑在 127.0.0.1:4001）
#   python ib_gui_charts.py --smoke    # 冒煙測試：自動開關視窗，不需互動
#
# 設計說明：
# - 8 個圖表共用「同一條」IBClient 連線（單一 clientId），按「啟動全部」後
#   並行跑 8 個 get_kline_live 串流；空白代號的圖表自動跳過。
# - 背景 QThread 內跑常駐 asyncio event loop（與 ib_gui_test.py 同架構）：
#   GUI 執行緒用 call_soon_threadsafe / run_coroutine_threadsafe 提交工作，
#   UI 不被阻塞；串流資料經 Qt signal（排隊）送進各圖表。
# - K線用自繪 QPainter 畫蠟燭（不用 matplotlib）：每個圖表是獨立可聚焦的
#   QWidget，NVDA 靠「文字」而非像素讀取內容 ——
#     * 所有控制項都有 setAccessibleName（含圖表編號前綴，Tab 時不混淆）；
#     * 每張圖下方有狀態列：最新收盤價 ▲/▼、K線根數、最後時間；
#     * 聚焦在圖表上按 Enter / Space → 朗讀現況（按需朗讀）；新 K線「收盤」時
#       自動朗報（未收盤的即時跳動不朗讀，避免刷屏）。
#   朗報機制：優先用 QAccessible.announceMessage（不搶焦點）；本機 PySide6
#   build 未暴露該 API → 退回「聚焦該圖表狀態列」（NVDA 在焦點移動時朗讀），
#   且使用者正在輸入框打字/選取時自動略過、不搶焦點。
# - 每張圖有獨立「檢視狀態」(_view_start/_view_count，縮放平移互不干擾)：
#   滑鼠滾輪 = 以游標位置為錨點縮放；左鍵拖曳 / 單指滑動 = 前後平移；
#   雙指捏合 = 縮放（觸控）；雙擊或 Home 鍵 = 重置。新 K線收盤且檢視停在右緣
#   → 自動跟隨（右緣釘住最新 bar）；使用者已平移或縮放離開右緣則不打擾。
# - 成交量副圖：每張圖底部 ~24% 畫量柱（與蠟燭同漲跌色，標籤以 K/M 顯示最大值）；
#   全零時（如外匯）不畫該區、價格佔滿整幅。
# - 「停止全部」= cancel session task：asyncio.gather 會連帶取消所有圖表
#   task，get_kline_live 自己接住 CancelledError、清理訂閱並印 🛑（已驗證
#   的模式），然後連線關閉。
# - 配色經 dataviz validate_palette.js 驗證（light mode）：
#   漲 #51cf66 / 跌 #b02525，CVD ΔE 25.3（deutan）—— 色盲可辨；
#   且狀態列以 ▲/▼ 文字標示方向（身份不只靠顏色）。
# =====================================================================

import asyncio
import contextlib
import math
import os
import re
import sys
import threading
import time
import traceback
from datetime import date, datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QThread, Signal, QTimer, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, Qt, QAccessible
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QLineEdit, QComboBox, QSpinBox, QPushButton, QCheckBox,
    QPlainTextEdit, QMessageBox,
)

from ib_client import IBClient

# ---- 參數選項（合法值清單見 ib_test.py 檔頭註解）----
SEC_TYPES = ['STK', 'FUT', 'CRYPTO', 'CASH']
DURATIONS = ['30 S', '1 D', '2 D', '5 D', '1 W', '1 M', '2 M', '3 M', '1 Y']
BARSIZES = [
    '1 sec', '5 secs', '15 secs', '30 secs',
    '1 min', '2 mins', '3 mins', '5 mins', '10 mins', '15 mins', '20 mins', '30 mins',
    '1 hour', '4 hours', '8 hours',
    '1 day', '1 week', '1 month',
]

N_PANELS = 8          # 圖表數量（4 欄 × 2 列）
MAX_VISIBLE_BARS = 120   # 預設檢視：每張圖顯示最近幾根 K線（可縮放，下限見 MIN_VIEW_BARS）
MIN_VIEW_BARS = 5        # 縮放下限：至少可見幾根 K線

# ---- 配色（已驗證，見檔頭；文字一律用 ink token、不用系列色）----
UP = '#51cf66'        # 漲（陽燭）
DOWN = '#b02525'      # 跌（陰燭）
INK = '#1f2937'       # 主要文字
MUTED = '#6b7280'     # 次要文字 / 最新價虛線
GRID = '#e5e7eb'      # 網格線（退讓、不干擾數據）
SURFACE = '#fcfcfb'   # 圖表底色

# 預填的示範組合（空白 = 不啟動；可全改）。
# 📌 只放「已驗證能解析合約」的代號：本帳號 _get_contract 把 FUT 固定映射到
#    CME 交易所，且 IB 會依帳號權限過濾合約清單 —— 非 CME 掛牌期貨（CL=NYMEX、
#    GC=COMEX）與部分 CME Group 產品（YM）在本帳號都解析不到（Error 200）。
DEFAULTS = [
    ('MNQ', 'FUT', '1 D', '15 mins'),
    ('ES', 'FUT', '1 D', '1 hour'),
    ('NQ', 'FUT', '1 D', '1 hour'),
    ('RTY', 'FUT', '1 D', '1 hour'),
    ('MES', 'FUT', '1 D', '1 hour'),
    ('USDJPY', 'CASH', '1 D', '1 hour'),
    (None, None, None, None),   # 空白圖表：示範「跳過」行為
    (None, None, None, None),
]

# ANSI 逸出序列過濾（與 ib_gui_test.py 相同）
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')


def _announce(text, target=None):
    """NVDA 即時朗讀。優先用 announceMessage（不搶焦點）；本機 PySide6 build
    未暴露該 API → 退回「聚焦狀態列」法（NVDA 在焦點移動時會朗讀），
    且使用者正在輸入框打字/選取時不搶焦點。"""
    try:
        QAccessible.announceMessage(text)   # 有暴露此靜態方法的 Qt build：免搶焦點
        return
    except Exception:
        pass
    if target is None:
        return
    fw = QApplication.focusWidget()
    if isinstance(fw, (QLineEdit, QComboBox)):   # 使用者正在輸入/選取 → 不搶焦點
        return
    target.setFocus()


def _fmt_price(v):
    if v >= 1000:
        return f'{v:,.0f}'
    if v >= 100:
        return f'{v:,.1f}'
    return f'{v:,.2f}'


def _fmt_vol(v):
    """成交量標籤：K/M/B 縮寫（副圖最大值用）"""
    if v >= 1e9:
        return f'{v / 1e9:.1f}B'
    if v >= 1e6:
        return f'{v / 1e6:.1f}M'
    if v >= 1e3:
        return f'{v / 1e3:.0f}K'
    return f'{v:.0f}'


def _to_dt(ts):
    """把 K線 index 的時間戳正規化成 datetime。
    📌 ib_async parseIBDatetime：日/週/月 bar（IB 只回 YYYYMMDD）會得到 datetime.date，
       內盤 bar 是 Timestamp/datetime —— 混用時比較/格式化會出錯，統一轉成 datetime。"""
    if isinstance(ts, pd.Timestamp):
        return ts.to_pydatetime()
    if isinstance(ts, datetime):      # datetime 是 date 的子類 → 要先判斷
        return ts
    if isinstance(ts, date):
        return datetime(ts.year, ts.month, ts.day)
    return pd.Timestamp(ts).to_pydatetime()


class CandleChart(QWidget):
    """QPainter 自繪蠟燭圖：只負責畫 + NVDA 朗讀，資料由外部 merge 進來"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bars = []          # [(datetime, open, high, low, close, volume), ...]
        self.announcer = None   # callable(text) — 由 ChartPanel 提供（聚焦該面板狀態列）
        self.setFocusPolicy(Qt.StrongFocus)   # NVDA：可 Tab 聚焦到圖表本身
        self.setMinimumHeight(150)
        # ---- 每張圖獨立的檢視狀態（縮放/平移；float 讓捏合縮放平滑）----
        self._view_start = 0.0                     # 第一根可見 bar 的 index（float）
        self._view_count = float(MAX_VISIBLE_BARS)  # 視窗內 bar 數（float）
        # ---- 互動狀態：滑鼠拖曳 / 觸控手勢 ----
        self._dragging = False       # 左鍵拖曳平移中
        self._drag_x = 0.0           # 拖曳起點 x
        self._touch_active = False   # 觸控進行中 → 忽略 Qt 合成的滑鼠事件
        self._swallow_mouse = False  # 觸控剛結束 → 短暫吞掉合成滑鼠事件（防誤觸）
        self._pinch_dist = None      # 雙指捏合：上一幀兩指距離
        self._pinch_mid_x = 0.0      # 雙指捏合：上一幀中點 x
        self._touch_last_x = None    # 單指平移：上一幀 x
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)

    def merge(self, rows):
        """合併進來的 bars（初始歷史 / 單根新 bar）；依時間戳去重：
        同時間 = 取代（未收盤 bar 的即時更新）、較舊 = 跳過（重連補發）。
        回傳「新增了」幾根 bar（0 = 只有原地更新）— 用於決定要不要 NVDA announce。
        📌 跟隨模式：新 bar 進來且檢視停在右緣 → 視窗同步前移，最新 bar 恆可見；
           使用者已平移/縮放離開右緣 → 不打擾（每張圖獨立）。"""
        if not self.bars:
            self.bars = list(rows)
            n = len(self.bars)
            self._view_count = min(float(MAX_VISIBLE_BARS), float(n))
            self._view_start = max(0.0, n - self._view_count)   # 預設檢視：右緣釘住
            self.update()
            return n
        last_ts = self.bars[-1][0]
        n_before = len(self.bars)
        was_following = (self._view_start + self._view_count >= n_before - 1e-9)
        added = 0
        for r in rows:
            ts = r[0]
            if ts == last_ts:
                self.bars[-1] = r          # 同一根 bar 的更新（未收盤跳動）
            elif ts > last_ts:
                self.bars.append(r)
                last_ts = ts
                added += 1
            # ts < last_ts：歷史重複（斷線重連後補發整段歷史）→ 跳過
        if added and was_following:
            # 跟隨模式：右緣釘住最新 bar。原本就是「預設檢視」（從第一根看到最近
            # min(120,n) 根）→ 視窗跟著變寬（直到上限），最舊的 bar 不會被擠出畫面；
            # 已縮放/平移過 → 只前移 start，保持縮放狀態。
            default_count = min(float(MAX_VISIBLE_BARS), float(n_before))
            if self._view_start <= 1e-9 and abs(self._view_count - default_count) < 1e-9:
                n_after = len(self.bars)
                self._view_count = min(float(MAX_VISIBLE_BARS), float(n_after))
                self._view_start = max(0.0, n_after - self._view_count)
            else:
                self._view_start += added
        self._clamp_view()
        self.update()
        return added

    def _clamp_view(self):
        """把檢視狀態夾回合法範圍（不能顯示比實際更多的 bar、不能早於第一根）"""
        n = len(self.bars)
        if n == 0:
            self._view_start, self._view_count = 0.0, float(MAX_VISIBLE_BARS)
            return
        lo_c = min(float(MIN_VIEW_BARS), float(n))
        hi_c = max(float(MIN_VIEW_BARS), float(n))
        self._view_count = max(lo_c, min(self._view_count, hi_c))
        self._view_start = max(0.0, min(self._view_start, n - self._view_count))

    def reset_view(self):
        """重置為預設檢視：最近 min(MAX_VISIBLE_BARS, n) 根、右緣釘住"""
        n = len(self.bars)
        if not n:
            return
        self._view_count = min(float(MAX_VISIBLE_BARS), float(n))
        self._view_start = max(0.0, n - self._view_count)
        self.update()

    def zoom_at(self, factor, anchor_frac=1.0):
        """縮放：factor<1 = 放大（可見 bar 變少）。
        錨點 = 圖面寬度 anchor_frac 處的 bar（游標/雙指中點位置）保持不動。"""
        if not self.bars:
            return
        n = len(self.bars)
        old_count = self._view_count
        new_count = max(float(MIN_VIEW_BARS), min(float(n), old_count * factor))
        anchor_idx = self._view_start + old_count * anchor_frac   # 錨點下的資料 index
        self._view_count = new_count
        self._view_start = anchor_idx - new_count * anchor_frac
        self._clamp_view()
        self.update()

    def pan_by(self, d_bars):
        """平移：正 = 往前（較新）、負 = 往後（較舊）"""
        if not self.bars:
            return
        self._view_start += d_bars
        self._clamp_view()
        self.update()

    def summary(self, title):
        """給狀態列 / NVDA 讀的文字摘要（含目前檢視範圍 — 縮放/平移後可讀出位置）"""
        if not self.bars:
            return f'{title}：尚無數據'
        ts, o, h, l, c, v = self.bars[-1]
        arrow = '▲' if c >= o else '▼'     # 方向用文字標示（不只靠顏色）
        n = len(self.bars)
        s0 = int(max(0.0, self._view_start)) + 1
        e0 = min(n, int(math.ceil(self._view_start + self._view_count)))
        view_note = '' if (s0 == 1 and e0 == n) else f'，目前顯示第 {s0}~{e0} 根'
        return (f'{title}：最新收盤 {c:,.2f} {arrow}，'
                f'共 {n} 根K線，最後時間 {ts:%m-%d %H:%M}{view_note}')

    def keyPressEvent(self, ev):
        # NVDA / 鍵盤：Enter/Space = 朗讀現況；←/→ = 前後平移；+/− = 縮放；Home = 重置檢視
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            text = self.summary(self.accessibleName())
            if self.announcer is not None:
                self.announcer(text)
            else:
                _announce(text)
            return
        step = max(1, int(self._view_count * 0.1))   # 平移一步 ≈ 視窗寬度 10%
        if ev.key() == Qt.Key_Left:
            self.pan_by(-step)
            return
        if ev.key() == Qt.Key_Right:
            self.pan_by(step)
            return
        if ev.key() in (Qt.Key_Plus, Qt.Key_Equal):      # + / =（含數字鍵區）
            self.zoom_at(0.8)
            return
        if ev.key() in (Qt.Key_Minus, Qt.Key_Underscore):  # - / _
            self.zoom_at(1.25)
            return
        if ev.key() == Qt.Key_Home:
            self.reset_view()
            return
        super().keyPressEvent(ev)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(SURFACE))
        w, h = self.width(), self.height()
        ml, mr, mt, mb = 64, 10, 10, 22   # 左：價格標籤；下：時間標籤
        pw, ph = w - ml - mr, h - mt - mb
        if pw < 40 or ph < 30:
            p.end()
            return

        # ---- 依檢視狀態切片（float → floor/ceil，縮放平移中也能畫出邊界 bar）----
        n_all = len(self.bars)
        s0 = int(max(0.0, math.floor(self._view_start)))
        e0 = min(n_all, int(math.ceil(self._view_start + self._view_count)))
        bars = self.bars[s0:e0]
        if not bars:
            p.setPen(QColor(MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, '（無數據 — 輸入代號後按「啟動全部」）')
            p.end()
            return

        # ---- 圖面分割：價格區（上）+ 成交量副圖（下 ~24%；全零如外匯 → 不畫）----
        has_vol = any(b[5] > 0 for b in bars)
        gap = 12 if has_vol else 0
        vol_h = int(ph * 0.24) if has_vol else 0
        price_h = ph - vol_h - gap

        lo = min(b[3] for b in bars)
        hi = max(b[2] for b in bars)
        if hi - lo < 1e-9:
            hi += 1.0
            lo -= 1.0
        pad = (hi - lo) * 0.08
        lo -= pad
        hi += pad

        def y(price):
            return mt + price_h * (hi - price) / (hi - lo)

        # ---- 網格 + 價格標籤（退讓：細線、次要色文字；只畫在價格區）----
        p.setFont(QFont('Consolas', 8))
        n_grid = 5
        for i in range(n_grid + 1):
            price = hi - (hi - lo) * i / n_grid
            yy = int(y(price))
            p.setPen(QPen(QColor(GRID), 1))
            p.drawLine(ml, yy, ml + pw, yy)
            p.setPen(QColor(MUTED))
            p.drawText(0, mt, ml - 6, price_h, Qt.AlignRight | Qt.AlignVCenter, _fmt_price(price))

        # ---- 蠟燭（細 wick、實體寬隨視窗自適應；位置依資料 index → 縮放/平移平滑）----
        slot = pw / self._view_count
        body_w = max(2.0, min(slot * 0.65, 18.0))

        def cx_of(gi):
            return ml + (gi + 0.5 - self._view_start) * slot

        for gi in range(s0, e0):
            ts, o, h_, l_, c, v = self.bars[gi]
            cx = cx_of(gi)
            color = UP if c >= o else DOWN
            p.setPen(QPen(QColor(color), 1))
            p.drawLine(int(cx), int(y(h_)), int(cx), int(y(l_)))     # wick
            top, bot = y(max(o, c)), y(min(o, c))
            rect_h = max(1.0, bot - top)
            p.setBrush(QColor(color))
            p.drawRect(QRectF(cx - body_w / 2, top, body_w, rect_h))

        # ---- 成交量副圖（與蠟燭同漲跌色；左上標籤顯示最大值 K/M）----
        if has_vol:
            vmax = max(b[5] for b in bars) or 1.0
            vt = mt + price_h + gap      # 量區頂
            vb = mt + ph                 # 量區底（= h - mb）
            label_h = 12
            p.setPen(Qt.NoPen)
            for gi in range(s0, e0):
                ts, o, h_, l_, c, v = self.bars[gi]
                if v <= 0:
                    continue
                cx = cx_of(gi)
                bh = max(1.0, (v / vmax) * (vol_h - label_h))
                p.setBrush(QColor(UP if c >= o else DOWN))
                p.drawRect(QRectF(cx - body_w / 2, vb - bh, body_w, bh))
            p.setPen(QColor(MUTED))
            p.drawText(ml, vt, pw, label_h, Qt.AlignLeft | Qt.AlignTop, f'Vol max {_fmt_vol(vmax)}')

        # ---- 最新價：虛線 + 直接標籤（▲/▼ 收盤價）— 僅當右緣停在最新 bar ----
        if e0 == n_all:
            ts_last, o_l, h_l, l_l, c_l, v_l = self.bars[-1]
            yy = int(y(c_l))
            p.setPen(QPen(QColor(MUTED), 1, Qt.DashLine))
            p.drawLine(ml, yy, ml + pw, yy)
            arrow = '▲' if c_l >= o_l else '▼'
            label = f'{arrow} {c_l:,.2f}'
            tw = p.fontMetrics().horizontalAdvance(label) + 10
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(INK))
            p.drawRect(ml + pw - tw, yy - 9, tw, 18)
            p.setPen(QColor('#ffffff'))
            p.drawText(ml + pw - tw, yy - 9, tw, 18, Qt.AlignCenter, label)

        # ---- 時間標籤（底部、約 5 個等距；位置依資料 index）----
        p.setPen(QColor(MUTED))
        span = (self.bars[e0 - 1][0] - self.bars[s0][0]).total_seconds()
        fmt = '%m-%d %H:%M' if span > 86400 * 1.5 else '%H:%M'
        n_t = min(5, e0 - s0)
        for k in range(n_t):
            gi = round(s0 + k * (e0 - s0 - 1) / max(1, n_t - 1))
            cx = cx_of(gi)
            cx = max(ml + 24, min(cx, ml + pw - 24))   # 防邊緣標籤被裁切
            p.drawText(int(cx) - 40, h - mb + 3, 80, 16, Qt.AlignCenter, f'{self.bars[gi][0]:{fmt}}')

    # ================= 縮放 / 平移互動（每張圖獨立）=================
    def wheelEvent(self, ev):
        """滑鼠滾輪：以游標 x 位置為錨點縮放（上 = 放大、下 = 縮小）"""
        if not self.bars:
            return
        pw = max(1.0, float(self.width() - 64 - 10))   # 與 paintEvent 同邊界（ml=64, mr=10）
        frac = min(1.0, max(0.0, (ev.position().x() - 64) / pw))
        self.zoom_at(0.8 if ev.angleDelta().y() > 0 else 1.25, frac)

    def mousePressEvent(self, ev):
        if self._touch_active or self._swallow_mouse:
            return   # 觸控合成的滑鼠事件 → 忽略（防捏合/平移結束後誤觸拖曳）
        if ev.button() == Qt.LeftButton and self.bars:
            self._dragging = True
            self._drag_x = ev.position().x()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, ev):
        if not self._dragging:
            return
        pw = max(1.0, float(self.width() - 64 - 10))
        d_bars = (self._drag_x - ev.position().x()) / pw * self._view_count
        # 往左拖 → 往前（較新）；往右拖 → 往後（較舊）
        if abs(d_bars) > 1e-9:
            self.pan_by(d_bars)
        self._drag_x = ev.position().x()

    def mouseReleaseEvent(self, ev):
        if self._touch_active or self._swallow_mouse:
            return
        if self._dragging and ev.button() == Qt.LeftButton:
            self._dragging = False
            self.unsetCursor()

    def mouseDoubleClickEvent(self, ev):
        """雙擊：重置為預設檢視"""
        if self._touch_active or self._swallow_mouse:
            return
        self.reset_view()

    def touchEvent(self, ev):
        """觸控手勢：單指滑動 = 前後平移；雙指捏合 = 縮放（每張圖獨立）。
        📌 觸控結束後 Qt 可能補發合成滑鼠事件 → _swallow_mouse 短暫吞掉，防誤觸拖曳/雙擊。"""
        pts = [t for t in ev.touches()
               if t.state() in (Qt.TouchPointPressed, Qt.TouchPointMoved)]
        released = any(t.state() == Qt.TouchPointReleased for t in ev.touches())

        if not pts:
            if self._touch_active or released:
                self._touch_active = False
                self._pinch_dist = None
                self._touch_last_x = None
                self._swallow_mouse = True
                QTimer.singleShot(150, lambda: setattr(self, '_swallow_mouse', False))
            return

        if not self.bars:
            return
        self._touch_active = True
        pw = max(1.0, float(self.width() - 64 - 10))

        if len(pts) >= 2:   # ---- 雙指捏合縮放（錨點 = 兩指中點）----
            a, b = pts[0], pts[1]
            dist = math.hypot(a.position().x() - b.position().x(),
                              a.position().y() - b.position().y())
            mid_x = (a.position().x() + b.position().x()) / 2.0
            if self._pinch_dist is not None and self._pinch_dist > 1e-9:
                frac = min(1.0, max(0.0, (mid_x - 64) / pw))
                self.zoom_at(self._pinch_dist / dist, frac)   # 張開 → dist↑ → factor<1 → 放大
                d_bars = (self._pinch_mid_x - mid_x) / pw * self._view_count
                if abs(d_bars) > 1e-9:
                    self.pan_by(d_bars)
            self._pinch_dist = dist
            self._pinch_mid_x = mid_x
            self._touch_last_x = None
        else:               # ---- 單指平移 ----
            x = pts[0].position().x()
            if self._touch_last_x is not None and abs(x - self._touch_last_x) > 1e-9:
                d_bars = (self._touch_last_x - x) / pw * self._view_count
                self.pan_by(d_bars)
            self._touch_last_x = x
            self._pinch_dist = None


class ChartPanel(QGroupBox):
    """一個圖表面板：參數列（代號/類型/Duration/BarSize）+ 蠟燭圖 + 狀態列"""

    def __init__(self, idx, default=None, parent=None):
        super().__init__(f'圖表 {idx}', parent)
        self.idx = idx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)

        row1 = QHBoxLayout()
        self.sym_edit = QLineEdit()
        self.sym_edit.setPlaceholderText('e.g. MNQ / NVDA / USDJPY（空白＝不啟動）')
        self.sec_combo = QComboBox()
        self.sec_combo.addItems(SEC_TYPES)
        row1.addWidget(QLabel('代號'))
        row1.addWidget(self.sym_edit, 1)
        row1.addWidget(QLabel('類型'))
        row1.addWidget(self.sec_combo)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        self.dur_combo = QComboBox()
        self.dur_combo.addItems(DURATIONS)
        self.bar_combo = QComboBox()
        self.bar_combo.addItems(BARSIZES)
        row2.addWidget(QLabel('Duration'))
        row2.addWidget(self.dur_combo, 1)
        row2.addWidget(QLabel('BarSize'))
        row2.addWidget(self.bar_combo, 1)
        lay.addLayout(row2)

        self.chart = CandleChart()
        # NVDA：在圖表上按 Enter/Space → 朗讀現況（焦點移到本面板狀態列）
        self.chart.announcer = lambda text: _announce(text, self.status_label)
        lay.addWidget(self.chart, 1)

        self.status_label = QLabel('待命')
        f = QFont()
        f.setPointSize(9)
        self.status_label.setFont(f)
        # 📌 關鍵：QLabel 預設 NoFocus，setFocus() 會靜默失敗 → NVDA「聚焦朗讀」
        #    fallback 完全失效。必須讓狀態列可被程式聚焦（NVDA 焦點移動時即朗讀）。
        self.status_label.setFocusPolicy(Qt.StrongFocus)
        lay.addWidget(self.status_label)

        # ---- NVDA：每個控制項都有「圖表 N」前綴的 accessible name（Tab 時不混淆）----
        for wgt, name in ((self.sym_edit, f'圖表 {idx} 代號'),
                          (self.sec_combo, f'圖表 {idx} 證券類型'),
                          (self.dur_combo, f'圖表 {idx} Duration 歷史長度'),
                          (self.bar_combo, f'圖表 {idx} BarSize K線週期')):
            wgt.setAccessibleName(name)
        self.chart.setAccessibleName(f'K線圖表 {idx}')
        self.status_label.setAccessibleName(f'圖表 {idx} 狀態')

        if default and default[0]:
            sym, sec, dur, bar = default
            self.sym_edit.setText(sym)
            self.sec_combo.setCurrentText(sec)
            self.dur_combo.setCurrentText(dur)
            self.bar_combo.setCurrentText(bar)

    def params(self):
        """回傳該圖表的 get_kline_live 參數 dict；代號空白 → None（跳過）"""
        sym = self.sym_edit.text().strip()
        if not sym:
            return None
        return {
            'symbol': sym,
            'security_type': self.sec_combo.currentText(),
            'durationStr': self.dur_combo.currentText().strip() or '1 D',
            'barSizeSetting': self.bar_combo.currentText().strip() or '15 mins',
        }

    def set_status(self, text):
        """更新狀態列，並同步到圖表的 accessible description（NVDA 聚焦可讀）"""
        self.status_label.setText(text)
        self.chart.setAccessibleDescription(text)


class _LogWriter:
    """redirect_stdout 目標：把 print 輸出轉發到 GUI 日誌面板（與 ib_gui_test.py 相同）"""

    def __init__(self, engine):
        self.engine = engine

    def write(self, s):
        if s:
            self.engine.sig_log.emit(s)

    def flush(self):
        pass


class ChartsEngine(QThread):
    """背景執行緒：常駐 asyncio loop + 單一共用 IBClient（8 圖表共用一個 clientId）"""

    sig_log = Signal(str)             # 日誌文字
    sig_bars = Signal(int, object)    # (圖表 idx, DataFrame) — 初始歷史或單根新 bar
    sig_panel_state = Signal(int, str)   # (idx, 狀態文字) — 某圖表串流提前結束/出錯
    sig_all_done = Signal()           # 整個 session 結束（全部完成或被 Stop）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loop = None
        self._ready = threading.Event()
        self._session_task = None
        self._client = None
        self._stop_requested = False
        self._active = {}             # {idx: params} — 本次 session 要啟動的圖表
        self._only_new_bar = False
        self._conn = ('127.0.0.1', 4001, 200)

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            # 收尾：取消所有殘留 task，讓 get_kline_live 的 finally 清理（🛑）跑完
            tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in tasks:
                t.cancel()
            if tasks:
                try:
                    loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                except Exception:
                    pass
            loop.close()

    def start_all(self, params_list, only_new_bar, conn):
        """GUI 執行緒呼叫。params_list：8 個 dict（None=跳過）；conn=(host,port,cid)。
        回傳 False = 沒有可啟動的圖表。"""
        if not self._ready.wait(5) or self._loop is None:
            raise RuntimeError('async engine 尚未就緒')
        active = [(i, p) for i, p in enumerate(params_list) if p]
        if not active:
            return False

        def _do():
            # 全部在 loop 執行緒做 → 無需鎖
            if self._session_task is not None and not self._session_task.done():
                return   # 已在跑（GUI 端也停用按鈕雙重護欄）
            self._stop_requested = False
            self._only_new_bar = only_new_bar
            self._conn = conn
            self._active = dict(active)
            self._session_task = self._loop.create_task(self._session())

        self._loop.call_soon_threadsafe(_do)
        return True

    async def _session(self):
        """一個 session：建立共用連線 → 並行跑所有圖表串流 → 全部結束後關連線"""
        writer = _LogWriter(self)
        try:
            with contextlib.redirect_stdout(writer):
                host, port, cid = self._conn
                names = ', '.join(f"{p['security_type']}:{p['symbol']}" for p in self._active.values())
                print(f"\n{'=' * 64}\n▶️  K線 LIVE 儀表板啟動（clientId={cid}）— {names}")
                async with IBClient(host=host, port=port, client_id=cid) as client:
                    self._client = client
                    tasks = {i: asyncio.create_task(self._panel(i, p))
                             for i, p in self._active.items()}
                    await asyncio.gather(*tasks.values(), return_exceptions=True)
                    print("✅ 所有 K線串流已結束")
        except asyncio.CancelledError:
            # Stop：gather 已先取消並等完所有圖表 task（各自 🛑 清理），這裡只收尾
            with contextlib.redirect_stdout(writer):
                print("\n⏹ 已依使用者要求停止（Stop）")
        except Exception:
            with contextlib.redirect_stdout(writer):
                traceback.print_exc()
        finally:
            self._client = None
            self.sig_all_done.emit()

    async def _panel(self, idx, p):
        """一個圖表的串流 task：把每筆 DataFrame 經 signal 送進 GUI"""
        try:
            stream = self._client.get_kline_live(
                symbol=p['symbol'], security_type=p['security_type'],
                durationStr=p['durationStr'], barSizeSetting=p['barSizeSetting'],
                currency='USD', only_new_bar=self._only_new_bar)
            async for df in stream:
                self.sig_bars.emit(idx, df)
            # 串流「正常」結束（IB 錯誤 / 無權限 → get_kline_live 印 ❌ 後 return）
            if not self._stop_requested:
                self.sig_panel_state.emit(idx, '⚠️ 串流提前結束（原因見下方日誌）')
        except Exception as e:
            self.sig_panel_state.emit(idx, f'❌ 錯誤：{e}')

    def stop_all(self):
        """GUI 執行緒呼叫安全。cancel session task → gather 連帶取消所有圖表 task"""
        if self._loop is None:
            return

        def _do():
            t = self._session_task
            if t is not None and not t.done():
                self._stop_requested = True
                t.cancel()

        self._loop.call_soon_threadsafe(_do)

    @property
    def running(self):
        return self._session_task is not None and not self._session_task.done()

    def shutdown(self):
        """關窗時呼叫：停 loop 並等執行緒結束"""
        if self._loop is not None:
            def _stop():
                t = self._session_task
                if t is not None and not t.done():
                    t.cancel()
                self._loop.stop()
            try:
                self._loop.call_soon_threadsafe(_stop)
            except RuntimeError:
                pass
        self.wait(3000)


class ChartsWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('IB K線 LIVE 儀表板 — 8 圖表（NVDA 友善）')
        self.resize(1760, 1000)
        self.setMinimumSize(1400, 820)

        # ---- async engine（背景執行緒 + 常駐 event loop）----
        self.engine = ChartsEngine(self)
        self.engine.sig_log.connect(self._append_log)
        self.engine.sig_bars.connect(self._on_bars)
        self.engine.sig_panel_state.connect(self._on_panel_state)
        self.engine.sig_all_done.connect(self._on_all_done)
        self.engine.start()
        self._running = False    # GUI 端防雙擊護欄（sig_all_done 是跨執行緒排隊信號）
        self._active_idx = set()

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ---------- 頂欄：連線設定 + 全域控制 ----------
        top = QHBoxLayout()
        self.host_edit = QLineEdit('127.0.0.1')
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(4001)
        self.cid_spin = QSpinBox()
        self.cid_spin.setRange(1, 9999)
        self.cid_spin.setValue(200)   # 📌 同時跑其他腳本請改（clientId 不可重複）
        top.addWidget(QLabel('Host'))
        top.addWidget(self.host_edit)
        top.addWidget(QLabel('Port'))
        top.addWidget(self.port_spin)
        top.addWidget(QLabel('clientId'))
        top.addWidget(self.cid_spin)
        self.chk_live = QCheckBox('含未收盤即時更新')
        self.chk_live.setChecked(True)   # 勾選 = only_new_bar=False（每次價格跳動都推送）
        self.chk_live.setAccessibleName('含未收盤K線即時更新')
        top.addWidget(self.chk_live)
        self.chk_auto_read = QCheckBox('新K線收盤時自動朗報（NVDA）')
        self.chk_auto_read.setChecked(True)   # 焦點移到該圖表狀態列 → NVDA 讀出（打字時不搶焦點）
        self.chk_auto_read.setAccessibleName('新K線收盤時自動聚焦狀態列，供NVDA朗讀')
        top.addWidget(self.chk_auto_read)
        top.addStretch(1)
        self.btn_start = QPushButton('▶ 啟動全部')
        self.btn_stop = QPushButton('⏹ 停止全部')
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet(
            'QPushButton { background-color: #c0392b; color: white; font-weight: bold; }'
            'QPushButton:hover { background-color: #a93226; }'
            'QPushButton:disabled { background-color: #d5dbdb; color: #7f8c8d; }')
        top.addWidget(self.btn_start)
        top.addWidget(self.btn_stop)
        root.addLayout(top)

        hint = QLabel(
            '縮放/平移（每圖表獨立）：滑鼠滾輪＝以游標位置縮放；左鍵拖曳／單指滑動＝前後移動；雙指捏合＝觸控縮放；'
            '雙擊或 Home 鍵＝重置檢視。鍵盤（聚焦在圖表上）：←/→ 平移、+/− 縮放、Home 重置。每圖底部為成交量副圖（全零如外匯則不顯示）。'
            'NVDA：Tab 在各圖表間移動；聚焦在圖表上按 Enter / Space 朗讀該圖最新狀態（焦點移到狀態列，含目前檢視範圍）。'
            '新 K線收盤時自動聚焦該圖狀態列朗報（輸入框打字時不搶焦點，可取消勾選關閉）。')
        hf = QFont()
        hf.setPointSize(8)
        hint.setFont(hf)
        hint.setStyleSheet(f'color: {MUTED};')
        hint.setAccessibleName('使用說明')
        root.addWidget(hint)

        # ---------- 8 圖表網格（4 欄 × 2 列）----------
        grid = QGridLayout()
        grid.setSpacing(6)
        self.panels = []
        for i in range(N_PANELS):
            panel = ChartPanel(i + 1, default=DEFAULTS[i])
            self.panels.append(panel)
            grid.addWidget(panel, (i // 4), (i % 4))
        root.addLayout(grid, 1)

        # ---------- 系統日誌（串流的 📡/✅/🛑/❌ 訊息）----------
        log_box = QGroupBox('系統日誌')
        llay = QVBoxLayout(log_box)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(12000)
        self.log.setFixedHeight(120)
        lf = QFont('Consolas', 9)
        lf.setStyleHint(QFont.TypeWriter)
        self.log.setFont(lf)
        self.log.setAccessibleName('系統日誌')
        llay.addWidget(self.log)
        root.addWidget(log_box)

        self.setCentralWidget(central)
        self.statusBar().showMessage('待命 — 設定各圖表代號後按「啟動全部」')

        # ---- 接線 ----
        self.btn_start.clicked.connect(self._start_all)
        self.btn_stop.clicked.connect(self._stop_all)

    def _conn_tuple(self):
        return (self.host_edit.text().strip() or '127.0.0.1',
                int(self.port_spin.value()),
                int(self.cid_spin.value()))

    # ================= 啟動 / 停止 =================
    def _start_all(self):
        if self._running:
            return
        params = [p.params() for p in self.panels]
        n = sum(1 for x in params if x)
        if n == 0:
            QMessageBox.warning(self, '缺少參數', '請至少為一個圖表輸入代號')
            return
        self._running = True
        self._active_idx = {i for i, x in enumerate(params) if x}
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)   # 📌 立即啟用（GUI 執行緒直接做，不等跨執行緒信號）
        self.statusBar().showMessage(f'執行中：{n} 個圖表（共用連線 clientId={self.cid_spin.value()}）')
        for i in self._active_idx:
            self.panels[i].set_status('📡 連線中 / 載入歷史數據…')
        try:
            ok = self.engine.start_all(params, only_new_bar=not self.chk_live.isChecked(),
                                       conn=self._conn_tuple())
            if not ok:   # 理論上不會（上面已檢查 n>0），防線
                self._on_all_done()
        except RuntimeError as e:
            QMessageBox.critical(self, '引擎錯誤', str(e))
            self._on_all_done()

    def _stop_all(self):
        if not self._running:
            return
        self.statusBar().showMessage('停止中…')
        self.engine.stop_all()

    # ================= 來自 engine（背景執行緒）的信號 =================
    def _on_bars(self, idx, df):
        panel = self.panels[idx]
        rows = []
        for ts, r in df.iterrows():
            v = float(r['volume']) if 'volume' in r.index else 0.0
            if v != v or v < 0:      # NaN 護欄（📌 不能用 `v or 0` — nan 是 truthy）
                v = 0.0
            rows.append((_to_dt(ts), float(r['open']), float(r['high']),
                         float(r['low']), float(r['close']), v))
        title = f"圖表 {idx + 1} {panel.sym_edit.text().strip()}"
        new_bar = panel.chart.merge(rows)
        panel.set_status(panel.chart.summary(title))
        if new_bar and self.chk_auto_read.isChecked():
            # NVDA：只在「新 K線收盤」時朗報（未收盤的即時跳動不朗讀，避免刷屏）；
            # 焦點移到該圖表狀態列 → NVDA 讀出（使用者正在打字時自動略過）
            _announce(f"{title}：新K線收盤 {rows[-1][4]:,.2f}", panel.status_label)

    def _on_panel_state(self, idx, text):
        panel = self.panels[idx]
        panel.set_status(text)
        if text.startswith(('❌', '⚠️')):   # 錯誤要讓 NVDA 使用者知道
            _announce(f"圖表 {idx + 1}：{text}")

    def _on_all_done(self):
        self._running = False
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.statusBar().showMessage('待命')
        # Stop 後還在「執行中」的圖表統一標記（正常結束/出錯的已由各自信號更新）
        for i in self._active_idx:
            if '📡' in self.panels[i].status_label.text():
                self.panels[i].set_status('⏹ 已停止')
        self._active_idx = set()

    def _append_log(self, s):
        s = _ANSI_RE.sub('', s)   # 過濾 ANSI 序列（GUI 裡清屏無意義）
        self.log.insertPlainText(s)
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def closeEvent(self, ev):
        self.engine.shutdown()
        ev.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    win = ChartsWindow()
    if '--smoke' in sys.argv:
        QTimer.singleShot(2500, app.quit)   # 冒煙測試：2.5 秒後自動關閉
    win.show()
    sys.exit(app.exec())

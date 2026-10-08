# -*- coding: utf-8 -*-
"""量度「拖動平移」實際幀率 / 每幀成本 / 跟手滯後（#24）。
用真 event loop + 真時鐘模擬 125 Hz 鼠标拖動（1 s），數實際上咗幾多幀。"""
import os
import sys
import tempfile
import time
import types
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gateway.state_store as state_store  # noqa: E402
state_store.STATE_PATH = Path(tempfile.mkdtemp()) / 'ui_state.json'

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

app = QApplication.instance() or QApplication([])

from gateway.pages import gui_kline as gk  # noqa: E402


def make_rows(n=1200, seed=7):
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(0, 1, n))
    rows = []
    for i in range(n):
        ci = float(c[i])
        o = ci + rng.normal(0, 0.3)
        rows.append((f"2026-01-01 {i // 60:02d}:{i % 60:02d}:00", o,
                     max(o, ci) + abs(rng.normal(0, 0.5)), min(o, ci) - abs(rng.normal(0, 0.5)),
                     ci, 1000 + i))
    return rows


rows = make_rows()
ch = gk.KlineChart()
ch.set_bars(rows)
ch._redraw()
ch.resize(1400, 700) if hasattr(ch, 'resize') else None
ch.canvas.draw()

# ── 計數：每次真上屏 + 每次 `_apply_view` ─────────────────────────────────
stats = {'paint': 0, 'apply': 0, 'paint_ms': []}
_real_draw = ch.canvas.draw


def counted_draw(*a, **k):
    stats['paint'] += 1
    t = time.perf_counter()
    r = _real_draw(*a, **k)
    stats['paint_ms'].append((time.perf_counter() - t) * 1000.0)
    return r


ch.canvas.draw = counted_draw
_real_apply = ch._apply_view


def counted_apply(*a, **k):
    stats['apply'] += 1
    return _real_apply(*a, **k)


ch._apply_view = counted_apply

# ── 模擬拖動：125 Hz（8 ms）× 125 個 event，每步 -2 根（向左拖，唔好撞 clamp）──
EV_HZ = 125
N_EV = 125
STEP = -2.0
x0 = 900.0
ch._view = (700.0, 1000.0)          # 由中間位置開始（跟隨模式一拖就撞右緣，量唔到）
ch._redraw()
ch._on_press(types.SimpleNamespace(inaxes=ch.ax, xdata=x0, button=1, ydata=5.0))

lag = []
t_start = time.perf_counter()
for i in range(N_EV):
    xx = x0 + STEP * (i + 1)
    ch._on_motion(types.SimpleNamespace(inaxes=ch.ax, xdata=xx, button=1, ydata=5.0))
    want = t_start + (i + 1) * (1.0 / EV_HZ)
    d = want - time.perf_counter()
    if d > 0:
        QTest.qWait(int(d * 1000))
    app.processEvents()
    # 跟手滯後：游標應該對應嘅 view vs 而家 axes 實際嘅 view
    tgt = ch._drag[1] + (xx - ch._drag[0]) if ch._drag else None
    if tgt is not None:
        lag.append(abs(ch.ax.get_xlim()[0] - tgt))
wall = time.perf_counter() - t_start
ch._on_release(types.SimpleNamespace(inaxes=ch.ax, xdata=x0 + STEP * N_EV, button=1, ydata=5.0))
QTest.qWait(400)
app.processEvents()

pm = sorted(stats['paint_ms'])
print(f'拖動 {N_EV} 個 event @ {EV_HZ} Hz（模擬 {wall * 1000:.0f} ms 實時）')
print(f'  `_apply_view` 次數 = {stats["apply"]}   真上屏（draw）次數 = {stats["paint"]}')
print(f'  實際幀率 ≈ {stats["paint"] / wall:.1f} fps（要求 {EV_HZ} fps）')
if pm:
    print(f'  每幀 draw：中位 {pm[len(pm) // 2]:.1f} ms / 最差 {pm[-1]:.1f} ms')
print(f'  跟手滯後（|實際 xlim − 游標應有位置|，單位=根）：中位 {np.median(lag):.2f} / 最差 {max(lag):.2f}')

# ── 對照：每幀 Y fit 有冇變（拖動期間 Y 軸郁唔郁）──────────────────────────
print('\n拖動期間 Y 軸範圍变化（每 25 個 event 取樣）：')
ys = []
ch._view = (700.0, 1000.0)
ch._redraw()
ch._on_press(types.SimpleNamespace(inaxes=ch.ax, xdata=x0, button=1, ydata=5.0))
for i in range(N_EV):
    ch._on_motion(types.SimpleNamespace(inaxes=ch.ax, xdata=x0 + STEP * (i + 1), button=1, ydata=5.0))
    if i % 25 == 0:
        ys.append(ch.ax.get_ylim())
    QTest.qWait(2)
ch._on_release(types.SimpleNamespace(inaxes=ch.ax, xdata=x0 + STEP * N_EV, button=1, ydata=5.0))
for a, b in zip(ys, ys[1:]):
    print(f'   [{a[0]:.2f}, {a[1]:.2f}] → [{b[0]:.2f}, {b[1]:.2f}]   Δ高 {(b[1]-b[0])/(a[1]-a[0]):.3f}×')

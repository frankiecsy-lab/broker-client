"""量度 K 線圖縮放/平移嘅成本（用戶：「縮放同平移非常唔順」）— 睇邊一步慢、慢幾多。

分三個口量：
  A) `_redraw()` 嘅 Python 部分（砌 artist，draw_idle 只排隊）
  B) `_redraw()` + `canvas.draw()` = 一幀真正上屏（用戶感受到嘅卡顿）
  C) 懸停 `_on_motion`（郁吓滑鼠就 full redraw）
再數每幀砌咗幾多個 artist（patches vs collections）。

Run: python .scratch/bench_kline_redraw.py   （無 broker、無 OpenD；Qt offscreen）
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile  # noqa: E402

import gateway.state_store as state_store  # noqa: E402

state_store.STATE_PATH = Path(tempfile.mkdtemp()) / 'ui_state.json'

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])  # noqa: E402

from gateway import indicators as ind  # noqa: E402
from gateway.pages import gui_kline as gk  # noqa: E402

N = 1200
rng = np.random.default_rng(7)
c = 100 + np.cumsum(rng.normal(0, 1, N))
rows = []
for i in range(N):
    ci = float(c[i])
    o = ci + rng.normal(0, 0.3)
    hi = max(o, ci) + abs(rng.normal(0, 0.5))
    lo = min(o, ci) - abs(rng.normal(0, 0.5))
    rows.append(('2026-01-01 %02d:%02d:00' % (i // 60, i % 60), o, hi, lo, ci, 1000 + i))


def artists(chart):
    n_p = sum(len(a.patches) for a in (chart.ax, chart.axv))
    n_c = sum(len(a.collections) for a in (chart.ax, chart.axv))
    n_l = sum(len(a.lines) for a in (chart.ax, chart.axv))
    return n_p, n_c, n_l


def bench(chart, widths, reps=12):
    print('  可見根數 |  A 砌 artist |  B 完整一幀 | artist（patches/collections/lines）')
    for w in widths:
        chart._view = (float(N - w), float(N - 1))
        chart._redraw()                       # 暖底（第一次砌 axes/字體 cache）
        t0 = time.perf_counter()
        for _ in range(reps):
            chart._redraw()
        app.processEvents()                   # 唔計 draw_idle 排隊嘅渲染
        t_a = (time.perf_counter() - t0) / reps * 1000
        t0 = time.perf_counter()
        for _ in range(max(3, reps // 3)):
            chart._redraw()
            chart.canvas.draw()
        t_b = (time.perf_counter() - t0) / max(3, reps // 3) * 1000
        p, cc, ln = artists(chart)
        print('  %8d | %10.1f ms | %12.1f ms | %d / %d / %d' % (w, t_a, t_b, p, cc, ln))


class FakeEv:
    inaxes = None
    xdata = None
    ydata = 1.0
    button = 1
    step = 1
    dblclick = False


def bench_hover(chart, reps=40):
    ev = FakeEv()
    ev.inaxes = chart.ax
    chart._view = (float(N - 300), float(N - 1))
    chart._redraw()
    t0 = time.perf_counter()
    for i in range(reps):
        ev.xdata = 10.0 + i * 3.0            # 郁滑鼠 → hover index 每次唔同 → full redraw
        chart._on_motion(ev)
    dt = (time.perf_counter() - t0) / reps * 1000
    print('  懸停移動（郁滑鼠）：%6.1f ms / 次' % dt)


print('=== 1) 純 K 線圖（gui_kline.KlineChart，冇指標）===')
ch = gk.KlineChart()
ch.set_bars(rows)
bench(ch, [60, 300, 1000])
bench_hover(ch)

print('=== 2) 開咗 4 個 ICT 疊加（OB / FVG / VOB / BOS）===')
ind.reset_manager_for_test()
mgr = ind.get_manager()
for e in list(mgr.items()):
    mgr.remove(e['id'], origin='bench')
for k in ('ob', 'fvg', 'vob', 'bos'):
    mgr.add(k, 'main', {p.key: p.default for p in ind.INDICATOR_DEFS[k].params}, origin='bench')
ch2 = ind.IndicatorKlineChart()
ch2.set_indicator_manager(mgr)
ch2.set_bars(rows)
ch2._redraw()
bench(ch2, [60, 300, 1000])
bench_hover(ch2)

print('=== 3) 對照：只改 xlim（唔重建 artist）===')
for w in (300, 1000):
    ch._view = (float(N - w), float(N - 1))
    ch._redraw()
    t0 = time.perf_counter()
    for i in range(60):
        ch.ax.set_xlim(ch.ax.get_xlim()[0] + 0.5, ch.ax.get_xlim()[1] + 0.5)
        ch.canvas.draw()
    print('  %d 根：set_xlim + draw = %.1f ms / 幀' % (w, (time.perf_counter() - t0) / 60 * 1000))

sys.exit(0)

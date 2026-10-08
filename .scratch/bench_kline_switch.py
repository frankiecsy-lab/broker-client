# -*- coding: utf-8 -*-
"""Step 0 baseline：量「撳週期掣 → baseline 上圖」實時（真 OpenD、真 GUI 路徑）。

三個情境：① 首次切去未睇過嘅週期 ② 切返啱啱睇過嘅週期 ③ 2×3 六格同時切。
同時數 `FutuClient._open_ctx` 被開咗幾次 = 實際開幾多條 OpenD 連線（P3 前後對比用）。
跑法：python .scratch/bench_kline_switch.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gateway.state_store as state_store  # noqa: E402
state_store.STATE_PATH = Path(tempfile.mkdtemp()) / 'ui_state.json'

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

app = QApplication.instance() or QApplication([])

from modules import futu_client as fc  # noqa: E402
from gateway.pages import quotes_page as qp  # noqa: E402

# ── 數連線：wrap `_open_ctx`（全檔唯一開 ctx 嘅位 → 唯一可靠嘅計法，query_subscription 唔分得返邊條連線）──
CONNS = {'n': 0}
_orig_open_ctx = fc.FutuClient._open_ctx


def _counting_open_ctx(self):
    CONNS['n'] += 1
    return _orig_open_ctx(self)


fc.FutuClient._open_ctx = _counting_open_ctx

page = qp.QuotesPage()
page.show()

MARKS = []   # 每次 show_rows 都記 (格 idx, 時刻, 行數)


def instrument(cid, cell):
    orig = cell.show_rows

    def wrapped(rows):
        MARKS.append((cid, time.perf_counter(), len(rows)))
        return orig(rows)

    cell.show_rows = wrapped


for i, c in enumerate(page.cells):
    instrument(i, c)


def pump_until(pred, timeout=60.0):
    """等 condition 成立（照 e2e 嘅 wait_for pattern，offscreen 都要 processEvents）。"""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        app.processEvents()
        QTest.qWait(20)
        if pred():
            return True
    return False


def measure(trigger, label, timeout=60.0):
    """trigger() 撳掣 → 等第一個新 baseline → 印 ms。"""
    MARKS.clear()
    t0 = time.perf_counter()
    trigger()
    ok = pump_until(lambda: bool(MARKS), timeout)
    ms = (MARKS[0][1] - t0) * 1000.0 if ok else float('nan')
    n = MARKS[0][2] if ok else 0
    print(f'  {label:<34} {ms:8.0f} ms   rows={n:<5} {"OK" if ok else "❌ TIMEOUT"}')
    return ms


SYM1 = 'HK.00700'
print('=== 情境 1/2：單格（1×1）切換週期 ===')
page._apply_layout('1x1', save=False)
page.cells[0].set_state(SYM1, 'K_1M')
CONNS['n'] = 0
page._start_cell_stream(0)
MARKS.clear()
pump_until(lambda: bool(MARKS))
print(f'  首次啟動 {SYM1} K_1M                {(MARKS[-1][1] if MARKS else 0)} 根，連線數 = {CONNS["n"]}')

measure(lambda: page._on_cell_period(0, 'K_5M'), '① 首次切 K_5M（未睇過）')
measure(lambda: page._on_cell_period(0, 'K_1M'), '② 切返 K_1M（啱啱睇過）')
measure(lambda: page._on_cell_period(0, 'K_5M'), '②b 再切返 K_5M（睇過两次）')
print(f'  累計開連線次數 = {CONNS["n"]}')

print('\n=== 情境 3：2×3 六格同時切 ===')
SYMS = ['HK.00700', 'HK.00005', 'HK.00001', 'HK.00388', 'HK.09988', 'HK.HSImain']
page._apply_layout('2x3', save=False)
for i, s in enumerate(SYMS):
    page.cells[i].set_state(s, 'K_1M')
CONNS['n'] = 0
t0 = time.perf_counter()
page._restart_visible_cells()
ok = pump_until(lambda: all(page.cells[i].chart._rows for i in range(6)), 120.0)
print(f'  六格 K_1M 全部上圖：{(time.perf_counter() - t0) * 1000:8.0f} ms   {"OK" if ok else "❌ TIMEOUT"}')

# 📏 用戶感知嘅指標：六格「全部有第一幀」要幾耐（唔係逐格等下一次 mark）
MARKS.clear()
t0 = time.perf_counter()
for i in range(6):
    page._on_cell_period(i, 'K_5M')
first = {}


def _seen():   # 每格第一次上圖嘅時刻先記低（其後嘅兩段式快照 / tick 唔計）
    for cid, ts, n in MARKS:
        first.setdefault(cid, ts)
    return len(first) == 6


ok = pump_until(_seen, 120.0)
worst = max(first.values()) if len(first) == 6 else None
print(f'  六格同時切 K_5M：全部上圖 {(worst - t0) * 1000 if worst else float("nan"):8.0f} ms'
      f'   各格 {[round((t - t0) * 1000) for t in sorted(first.values())]} ms'
      f' {"OK" if ok else "❌ TIMEOUT"}')
print(f'  累計開連線次數 = {CONNS["n"]}')

page._thread.request_shutdown()
page._thread.wait(3000)
print('\n=== 目標核對 ===')
print(f'  連線數 → 1：{"✅" if CONNS["n"] <= 1 else "❌ 仲多過一條"}'
      f'（六格情境累計 {CONNS["n"]} 條）')

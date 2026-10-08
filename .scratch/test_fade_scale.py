"""#31+#32 回歸 — BUFFER cluster 語義 + 純文字樣式（B紅字/S綠字、冇圓形底色）。

背景：#31 更正語義 = 對上一個訊號相隔 ≤N 條 → 後續轉純文字，組內首個照圓形徽章
（同 view 位置／資料長度無關）。#32 樣式更正：純文字 = 背景透明冇 bbox，B=C_UP 紅 /
S=C_DOWN 綠；徽章 = 圓形 bbox（fc 同色）白字。MARK_FADE_ALPHA 已退役。

Part A：200 bars 正弦（MA2x3 自然交叉）→ 逐個樣式 = cluster 規則（由 marks 序列獨立推導）。
Part B：合成 marks 擺明位置（5/10/100/195，BUF=20）→ 精確斷言兩個方向：
  @10（距 @5 組、喺 index < N-BUF）純文字 = 舊「最後 N 條」語義下必徽章 → 證明已改；
  @195（距 @100 遠、喺最後 N 條內）徽章 = 證明唔再係「最後 N 條」。

Run: python .scratch/test_fade_scale.py
"""
import math
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])  # noqa: E402

FAILURES = []


def check(name, ok):
    print(('  ✅ ' if ok else '  ❌ ') + name, flush=True)
    if not ok:
        FAILURES.append(name)


N, BUF = 200, 20
rows = []
for i in range(N):   # 正弦 series → MA2/MA3 持續交叉，marks 分佈全表
    c = 100.0 + 10.0 * math.sin(i / 5.0)
    o = 100.0 + 10.0 * math.sin((i - 1) / 5.0)
    hi, lo = max(o, c) + 0.5, min(o, c) - 0.5
    rows.append((f't{i}', o, hi, lo, c, 1000.0))

from matplotlib.colors import to_rgb  # noqa: E402
from gateway.indicators import IndicatorKlineChart  # noqa: E402
from gateway.pages import gui_kline as gk  # noqa: E402

STRAT = {'id': 's-diag', 'name': 'diag',
         'buy': [{'type': 'ma_cross', 'side': 'above',
                  'params': {'fast': 2, 'slow': 3}, 'score': 100}],
         'sell': [{'type': 'ma_cross', 'side': 'below',
                   'params': {'fast': 2, 'slow': 3}, 'score': 100}],
         'mark_buffer': BUF}

WHITE = to_rgb('#FFFFFF')


def same(c1, c2):
    try:
        return to_rgb(c1) == to_rgb(c2)
    except ValueError:
        return False


def drawn_marks(chart):
    """→ [(bar_idx, side, is_plain, text_color, bbox_fc_or_None)]（#32：冇 bbox = 純文字）。"""
    out = []
    for t in chart.ax.texts:
        if t.get_text() not in ('B', 'S'):
            continue
        bb = t.get_bbox_patch()
        out.append((int(t.get_position()[0]), t.get_text(), bb is None,
                    t.get_color(), None if bb is None else bb.get_facecolor()))
    return sorted(out)


def style_ok(m):
    """逐個樣式斷言：純文字 = 冇 bbox + B紅/S綠；徽章 = 圓形同色底 + 白字。"""
    i, side, plain, col, fc = m
    want = gk.C_UP if side == 'B' else gk.C_DOWN
    if plain:
        return fc is None and same(col, want)
    return same(col, WHITE) and fc is not None and to_rgb(fc[:4] if len(fc) == 4 else fc) \
        == to_rgb(want)


# ── Part A：自然交叉 — 逐個樣式必須等於 cluster 規則（測試自己推導 expected，唔抄實裝）
chart = IndicatorKlineChart()
chart.set_bars(rows)
chart.set_strategy(STRAT)
chart._redraw()
marks = drawn_marks(chart)
check('A: 200 bars 有足夠 marks（唔係空斷言）', len(marks) > 5)
expected, prev = {}, None
for i, s, _p, _c, _f in marks:
    expected[i] = prev is not None and i - prev <= BUF   # True = 純文字
    prev = i
bad_style = [m for m in marks if not style_ok(m)]
check('A: 逐個樣式 = 純文字(冇bbox紅/綠字)／徽章(圓形同色白字)（0 錯）', not bad_style)
if bad_style:
    print('    例:', bad_style[:5], flush=True)
bad_rule = [(i, s, p) for i, s, p, _c, _f in marks if p != expected[i]]
check(f'A: 純文字/徽章 分組 = cluster 規則（對上一個 ≤{BUF} → 純文字，否則徽章）（0 錯）', not bad_rule)
if bad_rule:
    print('    例:', bad_rule[:5], flush=True)
check('A: 首個訊號必徽章', marks and not marks[0][2])

# ── Part B：合成 marks 控制位置（繞過 _ensure_marks：cache 指紋對齊 data_seq）
SYN = [(5, 101.0, 'B'), (10, 99.0, 'S'), (100, 101.0, 'B'), (195, 99.0, 'S')]
chart._marks = list(SYN)
chart._marks_seq = chart._data_seq
chart._redraw()
got = {i: (s, p) for i, s, p, _c, _f in drawn_marks(chart)}
check('B: 合成 4 marks 全畫到', set(got) == {5, 10, 100, 195})
check('B: @5 首個徽章、@10 距 @5 得 5 條 → 純文字（index < N-BUF，舊語義必徽章 → 已更正）',
      got.get(5) == ('B', False) and got.get(10) == ('S', True))
check('B: @100 距 @10 遠 → 徽章；@195 喺最後 N 條內但距 @100 遠 → 照徽章（舊語義必純文字 → 唔再係「最後 N 條」）',
      got.get(100) == ('B', False) and got.get(195) == ('S', False))
check('B: 合成 marks 樣式全部正確（純文字紅/綠、徽章同色白字）',
      all(style_ok(m) for m in drawn_marks(chart)))

print('\n❌ 有失敗' if FAILURES else
      '\n✅ 全部通過 — BUFFER = 訊號間距語義（cluster）+ 純文字紅/綠冇底色（#32）')
sys.exit(1 if FAILURES else 0)

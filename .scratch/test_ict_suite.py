"""純計算測試 — ICT 全套（ticket #21）：BOS / CHoCH / LIQ / EQHL / PD / OTE / BRK / BPR。

Run: python .scratch/test_ict_suite.py   (from project root)
全部用**手砌 K 線 fixture**，逐條斷言「邊一根開始、畫到邊一根、價位幾多」— 唔用隨機數，
因為結構類指標（拐點/突破/掃蕩）一旦用隨機數就冇辦法口算對錯。
無 Qt、無 broker、無 state 檔。

Exit code 0 = all pass; non-zero = at least one check failed.
"""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from gateway.i18n import LANGS, t  # noqa: E402
from gateway.indicators import (  # noqa: E402
    FAR_OVERLAYS, FIT_PAD, INDICATOR_DEFS, _fit_vals, _fvg_candidates, _ob_candidates, _ob_zones,
    _swings, _structure_breaks, _supersede, _zones_to_arrays,
    compute_atr, compute_bos, compute_bpr, compute_choch, compute_eqhl, compute_fvg,
    compute_liq, compute_ote, compute_pd, compute_breaker, compute_ob, compute_vob,
)

FAILURES = []


def check(name, ok):
    print(('  ✅ ' if ok else '  ❌ ') + name, flush=True)
    if not ok:
        FAILURES.append(name)


def bars(seq):
    """[(o,h,l,c), ...] → ohlc dict（同 compute 契約一樣）。"""
    a = np.asarray(seq, dtype=float)
    return {'o': a[:, 0], 'h': a[:, 1], 'l': a[:, 2], 'c': a[:, 3]}


def idxs(arr):
    return [i for i in range(arr.shape[0]) if np.isfinite(arr[i])]


def approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


def flat(arr, span, val):
    """span 入面全部 ≈ val，其餘全 NaN。"""
    return idxs(arr) == list(span) and all(approx(arr[i], val) for i in span)


def same_breaks(got, exp):
    return len(got) == len(exp) and all(
        g[0] == e[0] and g[2] == e[2] and g[3] == e[3] and g[4] == e[4] and approx(g[1], e[1])
        for g, e in zip(got, exp))


# ══════════ Fixture A：上升結構 + 一次轉勢（BOS ×2 + CHoCH ×1）══════════
# 只做 h/l/c：l = h−0.2、c = h−0.15 → 收盤永遠喺高低中間，突破只由 h 序列決定。
HA = [10.0, 10.5, 11.0, 10.6, 10.2, 9.8, 10.3, 10.8, 11.3, 10.9, 10.5, 10.1,
      10.6, 11.1, 11.6, 11.2, 10.8, 10.4, 10.0, 10.5, 11.0, 11.5, 11.1, 10.7, 10.3]
A = bars([(h - 0.1, h, h - 0.2, h - 0.15) for h in HA])
NA = len(HA)

print('\n[A] 拐點 / 結構突破（k=2）')
hs, ls = _swings(A['h'], A['l'], 2)
check('A 拐點：swing high = [2,8,14,21]、swing low = [5,11,18]', hs == [2, 8, 14, 21] and ls == [5, 11, 18])
br = _structure_breaks(A['h'], A['l'], A['c'], 2)
check('A 突破 = 2 個順勢 BOS + 1 個逆勢 CHoCH（j=18）',
      same_breaks(br, [(2, 11.0, 8, 1, False), (8, 11.3, 14, 1, False), (11, 9.9, 18, -1, True)]))
check('第一個突破永遠唔算 CHoCH（冇參照方向）', br[0][4] is False)
check('最後 k 根永遠唔會係拐點（結構類指標天然滯後 k 根）', max(hs + ls) <= NA - 1 - 2)
check('swing 半徑越大 → 拐點越少（k=4 得返 [8,14]）', _swings(A['h'], A['l'], 4)[0] == [8, 14])

print('\n[A] compute_bos')
PA = {'swing': 2, 'max_levels': 10}
zb = compute_bos(A, PA)
check('BOS 只畫順勢位：[2..7] @ 11.0、[8..15] @ 11.3（重疊處較新嘅位覆蓋）',
      idxs(zb['bull_top']) == list(range(2, 16))
      and all(approx(zb['bull_top'][i], 11.0) for i in range(2, 8))
      and all(approx(zb['bull_top'][i], 11.3) for i in range(8, 16)))
check('BOS 位 = top==bottom（水平位，唔係區塊）',
      np.array_equal(zb['bull_top'], zb['bull_bottom'], equal_nan=True))
check('起點三角標記喺突破根 j=8 / j=14', idxs(zb['mark_bull']) == [8, 14]
      and approx(zb['mark_bull'][8], 11.0) and approx(zb['mark_bull'][14], 11.3))
check('上升結構 → 睇空陣列全空', all(np.isnan(zb['bear_top'])) and all(np.isnan(zb['mark_bear'])))
check('位畫到第一次被回踩為止（j=9 / j=15 係最後一根，之後 NaN）',
      np.isfinite(zb['bull_top'][9]) and np.isfinite(zb['bull_top'][15]) and np.isnan(zb['bull_top'][16]))
z1 = compute_bos(A, {'swing': 2, 'max_levels': 1})
check('max_levels=1 → 只留最近一個位', idxs(z1['mark_bull']) == [14])

print('\n[A] compute_choch')
zc = compute_choch(A, PA)
# 向下突破 = 睇空方向（d=−1）→ 用 bear_* 陣列（照 OB 家族同一套語義色）
check('CHoCH 只有轉勢嗰一個位（向下 → bear，level 9.9，由 swing 11 畫到 j=19）',
      flat(zc['bear_top'], range(11, 20), 9.9) and flat(zc['mark_bear'], [18], 9.9))
check('CHoCH 唔會重複 BOS 嘅位', np.isnan(zc['bear_top'][2]) and np.isnan(zc['bull_top'][2]))
check('BOS 與 CHoCH 互斥（每個突破只屬一邊）',
      len(idxs(zc['mark_bull'])) + len(idxs(zc['mark_bear']))
      + len(idxs(zb['mark_bull'])) + len(idxs(zb['mark_bear'])) == len(br))

# ══════════ Fixture B：影線掃走流動性（LIQ）══════════
# j=7 上影線出界收返入 = 掃走買方流動性；j=9 收盤都出界 = 真破位（唔算掃蕩）；
# j=13 下影線出界收返入 = 掃走賣方流動性；j=15 收盤出界 = 真破位。
B = bars([
    (9.8, 10.0, 9.8, 9.9), (10.4, 10.6, 10.4, 10.5), (11.0, 11.2, 11.0, 11.1),
    (11.6, 11.8, 11.6, 11.7), (11.9, 12.0, 11.8, 11.9), (11.6, 11.6, 11.4, 11.5),
    (11.2, 11.2, 11.0, 11.1), (11.9, 12.3, 11.5, 11.6), (11.5, 11.5, 11.3, 11.4),
    (11.0, 11.0, 10.8, 10.9), (10.4, 10.4, 10.2, 10.3), (10.8, 10.8, 10.6, 10.7),
    (11.2, 11.2, 11.0, 11.1), (11.4, 11.4, 9.6, 11.0), (11.6, 11.6, 11.4, 11.5),
    (12.4, 12.6, 12.4, 12.5),
])
print('\n[B] 流動性掃蕩 / Stop Hunt（k=2）')
check('B 拐點 = swing high [4,7]、swing low [6,10,13]', _swings(B['h'], B['l'], 2) == ([4, 7], [6, 10, 13]))
zl = compute_liq(B, {'swing': 2, 'max_marks': 10})
check('掃走買方流動性（上影線出界收返入）→ 睇空標記 j=7 @ 12.0', idxs(zl['mark_bear']) == [7]
      and approx(zl['mark_bear'][7], 12.0))
check('掃走賣方流動性（下影線出界收返入）→ 睇多標記 j=13 @ 10.2', idxs(zl['mark_bull']) == [13]
      and approx(zl['mark_bull'][13], 10.2))
check('收盤都出界 = 真破位（BOS 嘅事），呢個位即消費、唔再當掃蕩',
      np.isnan(zl['mark_bear'][9]) and np.isnan(zl['mark_bear'][15]) and np.isnan(zl['mark_bull'][15]))
check('水平位由被掃嘅 swing（4）畫到被掃嗰根（7）',
      np.isfinite(zl['bear_top'][4]) and np.isfinite(zl['bear_top'][7]) and np.isnan(zl['bear_top'][8]))
zm = compute_liq(B, {'swing': 2, 'max_marks': 1})
check('max_marks=1 → 只留最近一個（賣方嗰次）', idxs(zm['mark_bull']) == [13] and not idxs(zm['mark_bear']))

# ══════════ Fixture C：等高等低（EQHL）══════════
C = bars([
    (9.8, 10.0, 9.8, 9.9), (10.4, 10.6, 10.4, 10.5), (11.0, 11.2, 11.0, 11.1),
    (11.5, 11.7, 11.5, 11.6), (11.9, 12.0, 11.8, 11.9), (11.5, 11.5, 11.3, 11.4),
    (11.0, 11.0, 10.8, 10.9), (10.5, 10.5, 10.3, 10.4), (10.8, 11.0, 10.8, 10.9),
    (11.4, 11.6, 11.4, 11.5), (11.9, 12.02, 11.8, 11.9), (11.5, 11.6, 11.4, 11.5),
    (11.7, 11.8, 11.6, 11.7), (12.3, 12.5, 12.3, 12.4), (12.8, 13.0, 12.8, 12.9),
])
print('\n[C] Equal Highs / Lows（k=2，period=2）')
check('C 有兩個差 0.02 嘅 swing high（4 同 10）、只有單個 swing low',
      _swings(C['h'], C['l'], 2) == ([4, 10], [7]))
ze = compute_eqhl(C, {'period': 2, 'swing': 2, 'tol': 0.5, 'max_zones': 10})
check('等高位 = 上方流動性 → 標記做睇空，區塊 [12.0, 12.02] 由 j=4 開始',
      flat(ze['bear_top'], range(4, 14), 12.02) and flat(ze['bear_bottom'], range(4, 14), 12.0))
check('池畫到收盤穿過為止（j=13 着陸 = 最後一根，之後 NaN）',
      np.isfinite(ze['bear_top'][13]) and np.isnan(ze['bear_top'][14]))
check('等高位唔會砌出睇多區塊 / 單個 swing low 砌唔成等低池',
      all(np.isnan(ze['bull_top'])) and all(np.isnan(ze['bull_bottom'])))
check('tol=0 → 連 0.02 嘅差距都算唔同價 → 冇池',
      all(np.isnan(compute_eqhl(C, {'period': 2, 'swing': 2, 'tol': 0.0, 'max_zones': 10})['bear_top'])))

# ══════════ Fixture D：Premium / Discount（PD）══════════
D = bars([(10.0, 10.0 + i * 0.5, 9.6 + i * 0.5, 9.8 + i * 0.5) for i in range(10)])
print('\n[D] Premium / Discount（lookback=5）')
zp = compute_pd(D, {'lookback': 5})
h, l = D['h'], D['l']
check('前 lookback−1 根 NaN（warm-up）', all(np.isnan(zp['range_hi'][i]) and np.isnan(zp['range_lo'][i])
                                             for i in range(4)))
check('range_hi/lo = 回看窗最高/最低',
      all(approx(zp['range_hi'][i], h[i]) and approx(zp['range_lo'][i], l[i - 4]) for i in range(4, 10)))
check('equilibrium = dealing range 嘅 50%',
      all(approx(zp['equilibrium'][i], (h[i] + l[i - 4]) / 2) for i in range(4, 10)))
check('三條陣列都全長度（n 一樣）', all(v.shape[0] == 10 for v in zp.values()))
check('均衡線永遠喺區間中間', all(zp['range_lo'][i] < zp['equilibrium'][i] < zp['range_hi'][i]
                                  for i in range(4, 10)))

# ══════════ Fixture E：OTE 回調帶（推進 10.0 → 14.0）══════════
E = bars([
    (10.9, 11.0, 10.8, 10.9), (10.5, 10.6, 10.4, 10.5), (10.1, 10.2, 10.0, 10.1),
    (10.5, 10.6, 10.4, 10.5), (10.9, 11.0, 10.8, 10.9), (11.5, 11.6, 11.4, 11.5),
    (12.1, 12.2, 12.0, 12.1), (12.9, 13.0, 12.8, 12.9), (13.9, 14.0, 13.8, 13.9),
    (13.5, 13.6, 13.4, 13.5), (13.1, 13.2, 13.0, 13.1), (12.5, 12.6, 12.4, 12.5),
    (11.9, 12.0, 11.8, 11.9), (11.5, 11.6, 11.4, 11.5), (11.3, 11.4, 11.2, 11.3),
    (11.7, 11.8, 11.6, 11.7), (14.0, 14.2, 14.0, 14.1), (14.1, 14.5, 14.1, 14.4),
])
PE = {'swing': 2, 'fib_lo': 0.62, 'fib_hi': 0.79, 'max_zones': 5}
print('\n[E] Optimal Trade Entry')
zo = compute_ote(E, PE)
check('E 推進段 = swing low 2 → swing high 8（回調途中再有一個低拐点 14）',
      _swings(E['h'], E['l'], 2) == ([8], [2, 14]))
check('向上推進嘅 OTE 帶 = 14 − 0.79R … 14 − 0.62R = [10.84, 11.52]',
      approx(zo['bull_bottom'][10], 14.0 - 0.79 * 4.0) and approx(zo['bull_top'][10], 14.0 - 0.62 * 4.0))
check('帶由推進終點（j=8）先開始，之前 NaN', np.isnan(zo['bull_top'][7]) and np.isfinite(zo['bull_top'][8]))
check('帶畫到收盤返返出推進終點（j=16 延續 = 最後一根），之後 NaN',
      np.isfinite(zo['bull_top'][16]) and np.isnan(zo['bull_top'][17]))
check('回調途中出現反向推進 → 都有向下 OTE 帶',
      approx(zo['bear_bottom'][15], 11.2 + 0.62 * 2.8) and approx(zo['bear_top'][15], 11.2 + 0.79 * 2.8))
zs = compute_ote(E, {'swing': 2, 'fib_lo': 0.79, 'fib_hi': 0.62, 'max_zones': 5})
check('fib_lo/fib_hi 反轉輸入 → 自動調返（結果一樣）',
      np.array_equal(np.nan_to_num(zs['bull_top']), np.nan_to_num(zo['bull_top'])))
zn = compute_ote(E, {'swing': 2, 'fib_lo': 0.0, 'fib_hi': 1.0, 'max_zones': 5})
check('fib 0–1 = 成段推進都係帶（範圍最大）', approx(zn['bull_bottom'][10], 10.0) and approx(zn['bull_top'][10], 14.0))

# ══════════ Fixture F：Breaker Block（失效 OB 反轉用）══════════
F = bars([
    (100.0, 100.5, 99.5, 100.2), (100.2, 100.6, 99.8, 99.9), (99.9, 100.0, 99.4, 99.6),
    (99.6, 101.5, 99.5, 101.3), (101.3, 101.8, 101.0, 101.6), (101.6, 101.9, 101.2, 101.5),
    (101.5, 101.6, 100.8, 101.0), (101.0, 101.2, 99.0, 99.2), (99.2, 99.6, 98.8, 99.4),
    (99.4, 100.5, 99.3, 100.3), (100.3, 101.0, 100.1, 100.8),
])
PF = {'period': 2, 'strength': 0.0, 'confirm': 1, 'max_zones': 10}
print('\n[F] Breaker Block（OB 喺 j=2，收盤着穿喺 j=7）')
zk = compute_breaker(F, PF)
check('OB 仲未失效 → 唔會畫 Breaker（j=7 之前全 NaN）', all(np.isnan(zk['bear_top'][i]) for i in range(7)))
check('着穿之後 → Breaker = 同一個價格區 [99.4, 100.0]，方向反轉做睇空',
      approx(zk['bear_top'][7], 100.0) and approx(zk['bear_bottom'][7], 99.4))
check('Breaker 畫到收盤返返入區塊對面（j=9）為止', np.isfinite(zk['bear_top'][9]) and np.isnan(zk['bear_top'][10]))
check('Breaker 唔會同時畫做睇多 OB', all(np.isnan(zk['bull_top'])))

# ══════════ Fixture G：Balanced Price Range（兩個反向重疊 FVG）══════════
G = bars([
    (9.9, 10.0, 9.8, 9.9), (10.3, 10.5, 10.2, 10.4), (10.8, 11.0, 10.7, 10.9),
    (10.9, 10.9, 10.6, 10.7), (10.6, 10.8, 10.5, 10.6), (10.5, 10.7, 10.4, 10.45),
    (10.3, 10.3, 10.0, 10.35), (10.2, 10.45, 10.15, 10.4), (10.35, 10.4, 10.1, 10.35),
    (10.35, 10.6, 10.3, 10.55), (10.6, 10.8, 10.6, 10.7), (10.8, 11.0, 10.8, 10.9),
])
print('\n[G] Balanced Price Range（min_size=0）')
ga = compute_atr(G, {'period': 2})
cand = _fvg_candidates(G['h'], G['l'], ga['atr'], 0.0)
check('G 有向上缺口（start 2）同向下缺口（start 4）做素材',
      (2, 10.0, 10.7, 1) in cand and (4, 10.3, 10.5, -1) in cand)
zr = compute_bpr(G, {'period': 2, 'min_size': 0.0, 'max_zones': 10})
check('重疊部分 = [10.3, 10.5]，由第二個缺口出現（j=4）開始',
      approx(zr['bear_top'][4], 10.5) and approx(zr['bear_bottom'][4], 10.3) and np.isnan(zr['bear_top'][3]))
check('帶畫到價格完全穿過任一邊（j=9 收盤 ≥ 10.5 = 最後一根）為止',
      np.isfinite(zr['bear_top'][9]) and np.isnan(zr['bear_top'][10]))
check('min_size 好大 → 細缺口全過濾 → 冇 BPR',
      all(np.isnan(compute_bpr(G, {'period': 2, 'min_size': 99.0, 'max_zones': 10})['bear_top'])))

# ══════════ Fixture FV：FVG 近邊填平（🤖 價格返身入缺口即填平；掃描由確認根開始，三根形態本身唔算）══════════
FV = bars([
    (9.8, 10.0, 9.5, 9.9), (9.4, 9.45, 9.2, 9.3), (9.1, 9.1, 8.8, 8.9),
    (8.9, 9.2, 8.7, 9.1), (9.1, 9.3, 9.0, 9.2), (9.2, 9.4, 9.1, 9.3),
    (9.3, 9.45, 9.2, 9.4), (9.4, 9.6, 9.35, 9.55), (9.5, 9.45, 9.3, 9.4),
])
print('\n[FV] FVG：近邊填平 — 價格返身入缺口即結束，起點/確認根唔會自填')
fva = compute_atr(FV, {'period': 2})
check('FV 有睇空缺口（start 0，區間 [9.1, 9.5]）做素材',
      (0, 9.1, 9.5, -1) in _fvg_candidates(FV['h'], FV['l'], fva['atr'], 0.0))
zf = compute_fvg(FV, {'period': 2, 'min_size': 0.0, 'max_zones': 15})
check('起點根（0，high 10.0）同確認根（2，high = 缺口底 9.1）都唔算自填',
      np.isfinite(zf['bear_top'][0]) and np.isfinite(zf['bear_top'][2]))
check('價格返身入缺口（j=3 high 9.2 > 缺口底 9.1）即填平，j=4 起 NaN（唔再拖到完全填平先死）',
      idxs(zf['bear_top']) == [0, 1, 2, 3] and flat(zf['bear_top'], range(4), 9.5)
      and flat(zf['bear_bottom'], range(4), 9.1))

# 睇多方向對照：同一近邊語義（low < 缺口頂即填平）
FVU = bars([
    (10.0, 10.2, 9.8, 10.1), (10.4, 10.6, 10.3, 10.5), (10.7, 10.9, 10.6, 10.8),
    (10.7, 10.8, 10.5, 10.6), (10.5, 10.6, 10.1, 10.2), (10.0, 10.1, 9.9, 10.0),
])
zu = compute_fvg(FVU, {'period': 2, 'min_size': 0.0, 'max_zones': 15})
check('睇多 FVG：由確認根（2）畫到 low < 缺口頂（j=3：10.5 < 10.6）為止',
      idxs(zu['bull_top']) == [2, 3] and flat(zu['bull_top'], [2, 3], 10.6)
      and flat(zu['bull_bottom'], [2, 3], 10.2))

# ══════════ Fixture FVS：同向兩個 FVG — 較新者終止舊者（🤖 用戶：「不應讓間斷」→ 唔准重疊切割）══════════
FVS = bars([
    (9.8, 10.0, 9.5, 9.9), (9.4, 9.45, 9.0, 9.3), (9.1, 9.1, 8.8, 8.9),
    (8.9, 9.05, 8.7, 8.8), (8.8, 8.9, 8.6, 8.7), (8.7, 8.75, 8.5, 8.6),
    (8.4, 8.4, 8.2, 8.3), (8.3, 8.5, 8.1, 8.2),
])
zs = compute_fvg(FVS, {'period': 2, 'min_size': 0.0, 'max_zones': 15})
print('\n[FVS] FVG：同向較新者終止舊者 → 每個區塊完整一個方塊')
check('FVS 得返兩個睇空缺口做素材：A [9.1,9.5]（start 0）同 B [8.4,8.6]（start 4）',
      {(0, 9.1, 9.5, -1), (4, 8.4, 8.6, -1)}
      == set(_fvg_candidates(FVS['h'], FVS['l'], compute_atr(FVS, {'period': 2})['atr'], 0.0)))
check('A 冇被自己填平（0..3 high 全部 ≤ 9.1）但俾 B 終止；B 近邊填平喺 7 → 兩截完整、無斷續',
      idxs(zs['bear_top']) == list(range(8))
      and all(approx(zs['bear_top'][i], 9.5) and approx(zs['bear_bottom'][i], 9.1) for i in range(4))
      and all(approx(zs['bear_top'][i], 8.6) and approx(zs['bear_bottom'][i], 8.4) for i in range(4, 8)))

# ══════════ Fixture H：OB 家族新參數 max_size（位移燭唔算 OB）/ pen（被消耗幾深即失效）══════════
# OB = j=2 陰燭 [99.4, 100.0]（H=0.6），j=3 收盤突破。之後收盤依次 99.8（j=7，但影線低 99.3 已插穿區塊）
# → 99.5（j=8）→ 99.4（j=9）→ 99.2（j=10）：三個 pen 各喺唔同根死，正好逐條斷言
H = bars([
    (100.0, 100.5, 99.5, 100.2), (100.2, 100.6, 99.8, 99.9), (99.9, 100.0, 99.4, 99.6),
    (99.6, 101.5, 99.5, 101.3), (101.3, 101.8, 101.0, 101.6), (101.6, 101.9, 101.2, 101.5),
    (101.5, 101.6, 100.8, 101.0), (101.0, 101.2, 99.3, 99.8), (99.8, 100.4, 99.4, 99.5),
    (99.5, 100.2, 99.3, 99.4), (99.4, 99.9, 99.0, 99.2),
])
PH = {'period': 2, 'strength': 0.0, 'confirm': 1, 'max_zones': 10}
print('\n[H] OB 家族：max_size + pen')
z100 = compute_ob(H, dict(PH, max_size=0.0, pen=100))
check('OB 喺 j=2（[99.4, 100.0]），j=3 收盤突破 → 區塊由 j=2 開始',
      np.isnan(z100['bull_top'][1]) and approx(z100['bull_top'][2], 100.0)
      and approx(z100['bull_bottom'][2], 99.4))
check('pen=100（舊行為）：收盤完全穿過區塊底先死 → 畫到最後一根 j=10',
      np.isfinite(z100['bull_top'][9]) and np.isfinite(z100['bull_top'][10]))
z50 = compute_ob(H, dict(PH, max_size=0.0, pen=50))
check('pen=50：收盤穿過中點（99.5 < 99.7）即當被消耗 → 畫到嗰根（j=8）為止，j=9 起 NaN',
      np.isfinite(z50['bull_top'][8]) and np.isnan(z50['bull_top'][9]))
z0 = compute_ob(H, dict(PH, max_size=0.0, pen=0))
check('pen=0：一入區塊即死（j=7 收盤 99.8 < 區塊頂 100.0）→ 畫到 j=7 為止，j=8 起 NaN',
      np.isfinite(z0['bull_top'][7]) and np.isnan(z0['bull_top'][8]))
check('pen 用收盤唔用影線：j=7 影線 99.3 已插穿成個區塊，但收盤 99.8 → pen=50 照樣未死',
      np.isfinite(z50['bull_top'][7]))
a2 = compute_atr(H, {'period': 2})['atr'][2]
ratio = (H['h'][2] - H['l'][2]) / a2            # OB 燭高度 ÷ 當時 ATR
zb = compute_ob(H, dict(PH, max_size=ratio - 0.1, pen=100))
zk2 = compute_ob(H, dict(PH, max_size=ratio + 0.1, pen=100))
check('max_size < 區塊高度/ATR（%.2f）→ 唔算訂單塊（呢啲係位移燭）' % ratio, all(np.isnan(zb['bull_top'])))
check('max_size 大過個比例 → 照樣有區塊；0 = 唔過濾',
      np.isfinite(zk2['bull_top'][2]) and np.isfinite(z100['bull_top'][2]))
bk50 = compute_breaker(H, dict(PH, max_size=0.0, pen=50))
bk100 = compute_breaker(H, dict(PH, max_size=0.0, pen=100))
check('BRK 跟同一套 pen：pen=50 → OB 喺 j=8 已算失效 → Breaker 由 j=8 開始',
      np.isnan(bk50['bear_top'][7]) and approx(bk50['bear_top'][8], 100.0)
      and approx(bk50['bear_bottom'][8], 99.4))
check('pen=100 → 要 j=10 先失效 → Breaker 只能由 j=10 開始',
      np.isnan(bk100['bear_top'][9]) and np.isfinite(bk100['bear_top'][10]))

# ══════════ 隨機數據煙測：14 個 compute 全部唔炸、陣列全長度 ══════════
def _random_ok():
    rng = np.random.default_rng(11)
    c = 100 + np.cumsum(rng.normal(0, 0.8, 400))
    h = c + np.abs(rng.normal(0, 0.4, 400))
    l = c - np.abs(rng.normal(0, 0.4, 400))
    o = np.r_[c[0], c[:-1]]
    ohlc = {'o': o, 'h': h, 'l': l, 'c': c}
    for d in INDICATOR_DEFS.values():
        try:
            out = d.compute(ohlc, {p.key: p.default for p in d.params})
        except Exception as exc:                      # noqa: BLE001
            print('     ❌ %s 炸：%r' % (d.key, exc))
            return False
        if not out or any(v.shape[0] != 400 for v in out.values()):
            print('     ❌ %s 陣列唔係全長度' % d.key)
            return False
    return True


# ══════════ Registry / i18n：每個指標都要有一行描寫 + 用法 + 每個參數一行解釋 ══════════
print('\n[R] INDICATOR_DEFS / i18n（三語 fail-fast）')
DEFS = list(INDICATOR_DEFS.values())
check('17 個 def（3 舊 + 3 常用 MA/KDJ/RSI + 3 OB 家族 + 8 新 ICT）', len(DEFS) == 17)
check('全部 def 都有 desc_key + usage_key', all(d.desc_key and d.usage_key for d in DEFS))
check('全部參數都有 note_key', all(p.note_key for d in DEFS for p in d.params))
keys = [k for d in DEFS for k in (d.desc_key, d.usage_key)] + \
       [p.label_key for d in DEFS for p in d.params] + \
       [p.note_key for d in DEFS for p in d.params]
missing = []
for k in sorted(set(keys)):
    for lang in LANGS:
        try:
            if not t(k, lang).strip():
                missing.append('%s/%s 空' % (k, lang))
        except KeyError:
            missing.append('%s/%s' % (k, lang))
check('每個 desc / 用法 / 參數名 / 參數解釋三語都有非空字串（%d 個 key）' % len(set(keys)), not missing)
if missing:
    print('     ❌ 缺：' + ', '.join(missing[:12]))
check('ICT def 全部 position=main（貼價/擺蕩指標除外）',
      all(d.positions == ('main',) for d in DEFS
          if d.key not in ('boll', 'atr', 'macd', 'kdj', 'rsi')))
check('全部 def 都有 callable compute 且 warmup ≥ 0', all(callable(d.compute) and d.warmup >= 0 for d in DEFS))
check('全部參數預設喺 lo/hi 範圍內', all(p.lo <= p.default <= p.hi for d in DEFS for p in d.params))


def _pspec(key, pk):
    return next(p for p in INDICATOR_DEFS[key].params if p.key == pk)


check('OB 家族（OB/VOB/BRK）三個都暴露 max_size + pen + supersede，且範圍/預設一致（同一套偵測唔可以三個指標唔同規則）',
      all(_pspec(k, 'max_size').default == 3.0 and _pspec(k, 'max_size').lo == 0.0
          and _pspec(k, 'pen').default == 50 and _pspec(k, 'pen').hi == 100
          and _pspec(k, 'supersede').default == 1 and _pspec(k, 'supersede').lo == 0
          and _pspec(k, 'supersede').hi == 1
          for k in ('ob', 'vob', 'brk')))
check('新 def 嘅 compute 喺 400 根隨機數據都唔會炸、陣列全長度',
      _random_ok())


# ══════════ I：supersede — 同向新 OB 取代舊（用戶：「同一類 OB 出現了，之前嘅 OB 是不是應該消失」）══════════
print('\n[I] supersede：同方向出現更新嘅 OB → 舊嗰個即刻終止')
nI = 30
hI = np.full(nI, 105.0)
lI = np.full(nI, 95.0)
cI = np.full(nI, 100.0)          # 收盤永遠喺中間 → 永遠唔會「被消耗」，淨係測 supersede 呢條規則
candI = [(3, 99.0, 100.0, 1, 6), (12, 98.0, 99.0, 1, 15), (7, 101.0, 102.0, -1, 9)]
z_on = _ob_zones(nI, hI, lI, cI, candI, 15, 1.0, True)
z_off = _ob_zones(nI, hI, lI, cI, candI, 15, 1.0, False)
zI = {z[0]: z for z in z_on}


def _overlaps(zs):
    bad = []
    for a in range(len(zs)):
        for b in range(a + 1, len(zs)):
            if zs[a][4] == zs[b][4] and zs[a][0] <= zs[b][1] and zs[b][0] <= zs[a][1]:
                bad.append((zs[a][0], zs[b][0]))
    return bad


check('關咗 supersede → 三個區塊各自畫到最後一根（nI-1）、互相重疊',
      all(z[1] == nI - 1 for z in z_off) and len(_overlaps(z_off)) == 1)
check('開咗 supersede → 舊嘅多頭區塊喺新嗰個出現前一根（11）終止，新嘅照畫到結尾',
      zI[3][1] == 11 and zI[12][1] == nI - 1)
check('…空頭區塊唔受影響（唔同方向唔互相取代）', zI[7][1] == nI - 1 and zI[7][4] == -1)
check('…開咗之後同向必然唔重疊（呢個先係 `_zones_to_arrays` 唔會切短任何區塊嘅原因）',
      not _overlaps(z_on))
a_on = _zones_to_arrays(nI, z_on)
check('落陣列：bar11 仍然係舊區塊（99.0）、bar12 開始變新區塊（98.0）、之後冇再切短',
      a_on['bull_bottom'][11] == 99.0 and a_on['bull_bottom'][12] == 98.0
      and a_on['bull_bottom'][nI - 1] == 98.0)
check('`_supersede` 對空清單 / 單個區塊都唔會炸', _supersede([]) == [] and len(_supersede([(1, 5, 1.0, 2.0, 1)])) == 1)


def _random_no_overlap():
    rng = np.random.default_rng(11)
    c = 100 + np.cumsum(rng.normal(0, 1, 400))
    o = c + rng.normal(0, 0.3)
    hh = np.maximum(o, c) + abs(rng.normal(0, 0.5, 400))
    ll = np.minimum(o, c) - abs(rng.normal(0, 0.5, 400))
    ohlc = {'o': o, 'h': hh, 'l': ll, 'c': c}
    atr = compute_atr(ohlc, {'period': 14})['atr']
    cand = _ob_candidates(o, hh, ll, c, atr, 1.0, 3, 5, True, 3.0)
    zs = _ob_zones(400, hh, ll, c, cand, 15, 0.5, True)
    return not _overlaps(zs), len(cand), len(zs)


ok_no, n_cand, n_z = _random_no_overlap()
check('真隨機 400 根：supersede 之後同向零重疊（%d 個候選 → %d 個區塊）' % (n_cand, n_z), ok_no)


# ══════════ S：主圖 Y-fit 距離閘（用戶：「有啲 VOB 獨立出嚟，同 K 線冇連接同關係」）══════════
print('\n[S] Y-fit 距離閘（離價好遠嘅歷史區塊唔可以撐大條 Y 軸）')
check('FIT_PAD 係 0–1 之間嘅比例', 0.0 < FIT_PAD <= 1.0)
kept = _fit_vals(np.array([np.nan, 100.0, 104.9, 105.1, 150.0, np.nan]), 100.0, 104.0, 1.0)
check('_fit_vals 只保留 [lo−pad, hi+pad] 內嘅有限值（100/104.9 入閘，105.1/150/NaN 剔除）',
      kept.size == 2 and approx(float(kept[0]), 100.0) and approx(float(kept[1]), 104.9))
check('_fit_vals 全 NaN → 返回空陣列（唔會炸）', _fit_vals(np.full(5, np.nan), 0.0, 1.0, 0.1).size == 0)
check('_fit_vals 唔改可視範圍內嘅值（貼價指標照樣全量參與 fit）',
      _fit_vals(np.array([99.0, 100.0, 104.0]), 100.0, 104.0, 1.0).size == 3)
ICT_MAIN = {d.key for d in DEFS if d.positions == ('main',) and d.key not in ('boll', 'ma')}
check('FAR_OVERLAYS = 全部 ICT main 疊加（%d 個），貼價線 BOLL/MA/ATR/MACD 一律唔入閘' % len(ICT_MAIN),
      set(FAR_OVERLAYS) == ICT_MAIN and not ({'boll', 'ma', 'atr', 'macd'} & set(FAR_OVERLAYS)))

print('\n' + ('❌ FAILURES: %d — %s' % (len(FAILURES), FAILURES) if FAILURES else '✅ 全部通過'))
sys.exit(1 if FAILURES else 0)

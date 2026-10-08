"""純計算測試 — 常用指標 MA / KDJ / RSI（ticket #26）。

Run: python .scratch/test_indicators_common.py   （from project root）
無 Qt、無網絡：手砌期望值逐個斷言（SMA / 華語 KDJ 遞推 / Wilder RSI），
加埋 MA == BOLL mid 同一 rolling 契約 + registry / plotter 掛鉤斷言。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from gateway import indicators as ind  # noqa: E402

FAILURES = []


def check(name, ok):
    print(('  ✅ ' if ok else '  ❌ ') + name, flush=True)
    if not ok:
        FAILURES.append(name)


def approx(a, b, tol=1e-9):
    return abs(float(a) - float(b)) <= tol


def ohlc(c):
    c = np.asarray(c, dtype=float)
    return {'o': c, 'h': c + 0.1, 'l': c - 0.1, 'c': c}


nan = float('nan')

# ══ 1. MA：SMA 逐個手砌 + warm-up NaN + 不足長度全 NaN ═══════════════════
print('── MA ──')
o4 = ohlc([1, 2, 3, 4])
m = ind.compute_ma(o4, {'p1': 2, 'p2': 3, 'p3': 4, 'p4': 5})
check('p1=2 → [nan,1.5,2.5,3.5]（前 1 個 NaN）',
      np.isnan(m['ma1'][0]) and all(approx(m['ma1'][i], v) for i, v in ((1, 1.5), (2, 2.5), (3, 3.5))))
check('p2=3 → [nan,nan,2,3]；p3=4 → [nan,nan,nan,2.5]',
      np.isnan(m['ma2'][:2]).all() and approx(m['ma2'][2], 2) and approx(m['ma2'][3], 3)
      and np.isnan(m['ma3'][:3]).all() and approx(m['ma3'][3], 2.5))
check('n < p → 全 NaN（p4=5 > 4 根，如實冇值）', np.isnan(m['ma4']).all())
rng = np.random.default_rng(7)
c = 100 + np.cumsum(rng.normal(0, 1.0, 120))
oc = {'o': c, 'h': c + 0.5, 'l': c - 0.5, 'c': c}
mm = ind.compute_ma(oc, {'p1': 20, 'p2': 20, 'p3': 20, 'p4': 20})
bb = ind.compute_boll(oc, {'period': 20, 'dev': 2.0})
check('MA(20) == BOLL mid（同一 rolling 契約，唔會兩邊數值唔同）',
      np.allclose(mm['ma1'][19:], bb['mid'][19:], equal_nan=True))

# ══ 2. KDJ：華語遞推逐個手砌（n=3, m1=m2=2）══════════════════════════════
print('── KDJ ──')
kp = {'n': 3, 'm1': 2, 'm2': 2}
ku = ind.compute_kdj(ohlc([1, 2, 3, 4]), kp)
check('升市：RSV=100 → bar2 K=75 D=62.5 J=100；bar3 K=87.5 D=75 J=112.5（seed 50 遞推）',
      np.isnan(ku['k'][:2]).all()
      and approx(ku['k'][2], 75) and approx(ku['d'][2], 62.5) and approx(ku['j'][2], 100)
      and approx(ku['k'][3], 87.5) and approx(ku['d'][3], 75) and approx(ku['j'][3], 112.5))
kd = ind.compute_kdj(ohlc([4, 3, 2, 1]), kp)
check('跌市：RSV=0 → bar2 K=25 D=37.5 J=0；bar3 K=12.5 D=25 J=−12.5',
      approx(kd['k'][2], 25) and approx(kd['d'][2], 37.5) and approx(kd['j'][2], 0)
      and approx(kd['k'][3], 12.5) and approx(kd['d'][3], 25) and approx(kd['j'][3], -12.5))
kf = ind.compute_kdj(ohlc([5.0] * 10), {'n': 3, 'm1': 3, 'm2': 3})
check('平穩段：HHV==LLV → RSV 定義為 50 → K=D=J=50（唔炸、唔係 NaN）',
      np.isnan(kf['k'][:2]).all()
      and all(approx(kf['k'][i], 50) and approx(kf['d'][i], 50) and approx(kf['j'][i], 50)
              for i in range(2, 10)))
check('J 恆等式 J = 3K−2D 逐根成立（隨機 120 根）',
      all(approx(kf2['j'][i], 3 * kf2['k'][i] - 2 * kf2['d'][i])
          for kf2 in [ind.compute_kdj(oc, {'n': 9, 'm1': 3, 'm2': 3})]
          for i in range(8, 120)))

# ══ 3. RSI：Wilder seed + 遞推逐個手砌（period=2）═════════════════════════
print('── RSI ──')
r = ind.compute_rsi(ohlc([10, 11, 10, 11, 10, 11]), {'period': 2})
# ch=[+1,−1,+1,−1,+1]：seed ag=al=0.5 → rsi[2]=50；RMA 遞推 → 75 / 37.5 / 68.75
check('漲跌交替 period=2：[nan,nan,50,75,37.5,68.75]（Wilder RMA 逐個對應）',
      np.isnan(r['rsi'][:2]).all()
      and all(approx(r['rsi'][i], v) for i, v in ((2, 50), (3, 75), (4, 37.5), (5, 68.75))))
ru = ind.compute_rsi(ohlc(np.arange(1.0, 11.0)), {'period': 3})
check('只升不跌 → 100（al=0 如實，唔係 NaN）', (ru['rsi'][3:] == 100.0).all())
rf = ind.compute_rsi(ohlc([7.0] * 6), {'period': 3})
check('全平 → 50（無漲無跌嘅如實定義）', (rf['rsi'][3:] == 50.0).all())
rd = ind.compute_rsi(ohlc(np.arange(10.0, 0.0, -1.0)), {'period': 3})
check('只跌不升 → 0', (rd['rsi'][3:] == 0.0).all())

# ══ 4. Registry / plotter 掛鉤 ═════════════════════════════════════════════
print('── Registry ──')
D = ind.INDICATOR_DEFS
check('ma/kdj/rsi 已入 INDICATOR_DEFS（main / sub / sub）',
      D['ma'].positions == ('main',) and D['kdj'].positions == ('sub',)
      and D['rsi'].positions == ('sub',))
check('_PLOTTERS 三個都有掛鉤', all(k in ind._PLOTTERS for k in ('ma', 'kdj', 'rsi')))
check('MA 唔入 FAR_OVERLAYS（貼價線照舊全量參與 Y-fit）',
      not ({'ma', 'kdj', 'rsi'} & set(ind.FAR_OVERLAYS)))
check('輸出 key 同 plotter 讀嘅 key 一致',
      set(ind.compute_ma(oc, {p.key: p.default for p in D['ma'].params})) ==
      {'ma1', 'ma2', 'ma3', 'ma4'}
      and set(ind.compute_kdj(oc, {p.key: p.default for p in D['kdj'].params})) == {'k', 'd', 'j'}
      and set(ind.compute_rsi(oc, {p.key: p.default for p in D['rsi'].params})) == {'rsi'})

# ══ 5. #28：MA 逐條線顯示開關（is_bool / show1..4 / 摘要 skip）════════════
print('── #28 MA 逐條線開關 ──')
ms = ind.compute_ma(o4, {'p1': 2, 'p2': 3, 'p3': 4, 'p4': 5, 'show2': 0, 'show4': 0})
check('show2=0 / show4=0 → 只輸出 ma1/ma3（隱藏線唔輸出 key = 唔畫、唔入 Y-fit）',
      set(ms) == {'ma1', 'ma3'})
check('缺 show key 當顯示（舊實例 load 都照畫四條）',
      set(ind.compute_ma(o4, {'p1': 2, 'p2': 3, 'p3': 4, 'p4': 5})) == {'ma1', 'ma2', 'ma3', 'ma4'})
sp = {p.key: p for p in D['ma'].params}
check('show1..4 係 is_bool、預設 1',
      all(sp['show%d' % i].is_bool and sp['show%d' % i].default == 1 for i in (1, 2, 3, 4)))
check('_clamp_param bool 清洗：True→1 / False→0 / 2→clamp 1 / 垃圾→default 1',
      ind._clamp_param(sp['show1'], True) == 1 and ind._clamp_param(sp['show1'], False) == 0
      and ind._clamp_param(sp['show1'], 2) == 1 and ind._clamp_param(sp['show1'], 'x') == 1)
check('摘要 skip bool：MA 摘要照舊 = 週期串（panel 標題唔變）',
      ind._params_summary(D['ma'], {'p1': 5, 'p2': 10, 'p3': 20, 'p4': 60,
                                    'show1': 0, 'show2': 1, 'show3': 0, 'show4': 1}) == '5/10/20/60')

print('\n' + ('全部通過 ✅' if not FAILURES else f'失敗 {len(FAILURES)} 項：{FAILURES}'))
sys.exit(0 if not FAILURES else 1)

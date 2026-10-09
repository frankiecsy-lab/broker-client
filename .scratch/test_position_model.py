# -*- coding: utf-8 -*-
"""純計算測試 — gateway/position_model.py（長倉／短倉／雙向狀態機）+ 接入 backtest.py（ticket #34a）。

Run: python .scratch/test_position_model.py   （from project root）
無 Qt、無網絡：手砌合成 K 線 + 注入臨時 condition def 精確控制 B/S 位置。
斷言：truth table／模式收斂／短倉 = 長倉鏡像／雙向反手 = 兩筆四邊成本／
長短指標（n_short_trades、short_exposure_pct）／淨值曲線空頭方向／恆等式 total_ret == Σ pnl／
被忽略計數（含 bt_ig_badprice 獨立計數）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from gateway import backtest as bt  # noqa: E402
from gateway import position_model as pm  # noqa: E402
from gateway import strategies as st  # noqa: E402

FAILURES = []


def check(name, ok):
    print(('  ✅ ' if ok else '  ❌ ') + name, flush=True)
    if not ok:
        FAILURES.append(name)


def close_enough(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) <= tol


def mk(c):
    c = np.asarray(c, dtype=float)
    return {'o': c, 'h': c + 0.1, 'l': c - 0.1, 'c': c, 'v': np.full(c.shape[0], 100.0)}


def fake_cond(key, state):
    st.CONDITION_DEFS[key] = st.ConditionDef(
        key, key, (), 'test', lambda ohlc, p, side: np.asarray(state, dtype=bool))


def entry_of(buy_key, sell_key, name='測試策略'):
    r = lambda k: {'type': k, 'side': 'above', 'params': {}, 'score': 100}  # noqa: E731
    return {'id': 't-1', 'name': name, 'buy': [r(buy_key)], 'sell': [r(sell_key)],
            'entry_price': 'market', 'qty': 'min_lot', 'mark_buffer': 10}


def state(n, idxs):
    a = [False] * n
    for i in idxs:
        a[i] = True
    return a


CAP = 10000.0
OPTS0 = {'capital': CAP, 'fee_pct': 0.0, 'slip_pct': 0.0, 'rf_pct': 0.0}


def opts(mode, fee=0.0, slip=0.0):
    return {'capital': CAP, 'fee_pct': fee, 'slip_pct': slip, 'rf_pct': 0.0, 'mode': mode}


# ══ 1. 狀態機 truth table（三模式 × 持倉方向 × 訊號方向）══════════════════
print('── 狀態機 truth table ──')
T = [
    # (mode, cur, side, action, new dir, reason)
    (pm.MODE_LONG, None, 'B', pm.ACT_OPEN, 'long', None),
    (pm.MODE_LONG, None, 'S', pm.ACT_IGNORE, None, pm.IG_FLAT),
    (pm.MODE_LONG, 'long', 'B', pm.ACT_IGNORE, 'long', pm.IG_INPOS),
    (pm.MODE_LONG, 'long', 'S', pm.ACT_CLOSE, None, None),
    # 模式容納唔到嘅持倉（喺回測唔會出現；即時轉模式先至有可能）→ 一律先離場，唔會靜靜留低
    (pm.MODE_LONG, 'short', 'B', pm.ACT_FLIP, 'long', None),
    (pm.MODE_LONG, 'short', 'S', pm.ACT_CLOSE, None, None),
    (pm.MODE_SHORT, None, 'S', pm.ACT_OPEN, 'short', None),
    (pm.MODE_SHORT, None, 'B', pm.ACT_IGNORE, None, pm.IG_FLAT),
    (pm.MODE_SHORT, 'short', 'S', pm.ACT_IGNORE, 'short', pm.IG_INPOS),
    (pm.MODE_SHORT, 'short', 'B', pm.ACT_CLOSE, None, None),
    (pm.MODE_SHORT, 'long', 'S', pm.ACT_FLIP, 'short', None),
    (pm.MODE_SHORT, 'long', 'B', pm.ACT_CLOSE, None, None),
    (pm.MODE_BOTH, None, 'B', pm.ACT_OPEN, 'long', None),
    (pm.MODE_BOTH, None, 'S', pm.ACT_OPEN, 'short', None),
    (pm.MODE_BOTH, 'long', 'B', pm.ACT_IGNORE, 'long', pm.IG_INPOS),
    (pm.MODE_BOTH, 'short', 'S', pm.ACT_IGNORE, 'short', pm.IG_INPOS),
    (pm.MODE_BOTH, 'long', 'S', pm.ACT_FLIP, 'short', None),
    (pm.MODE_BOTH, 'short', 'B', pm.ACT_FLIP, 'long', None),
]
bad = []
for mode, cur, side, want_act, want_dir, want_r in T:
    act, nxt, reason = pm.step(pm.new_state() if cur is None else {'dir': cur}, side, mode)
    if (act, nxt.get('dir'), reason) != (want_act, want_dir, want_r):
        bad.append((mode, cur, side, act, nxt.get('dir'), reason))
check(f'{len(T)} 條 truth table 全部對（含模式容納唔到嘅持倉 → 一律先離場）', not bad)
for b in bad:
    print('     ↳ ' + repr(b))
check('step() 唔改入參 state（調用方唔 commit 就唔會變）',
      (lambda s: (pm.step(s, 'S', 'both'), s == {'dir': 'long'})[1])({'dir': 'long'}))
check('step() 食 None 狀態都唔炸（等同空倉）',
      pm.step(None, 'B', 'long') == (pm.ACT_OPEN, {'dir': 'long'}, None))

# ══ 2. 模式收斂／方向工具 ═══════════════════════════════════════════════
print('── 模式收斂／方向工具 ──')
check('clamp_mode：合法值原樣（大小寫／空格唔理）',
      [pm.clamp_mode(v) for v in ('long', 'SHORT', ' Both ', 'both')]
      == ['long', 'short', 'both', 'both'])
check('clamp_mode：垃圾／None／空字串 → 預設 long',
      all(pm.clamp_mode(v) == 'long' for v in (None, '', 'neutral', 'leverage', 0, 3)))
check('norm_opts 對 mode 同樣收斂（頁俾乜都唔會炸）',
      bt.norm_opts({'mode': 'SHORT'})['mode'] == 'short'
      and bt.norm_opts({})['mode'] == 'long'
      and bt.norm_opts({'mode': 'whatever'})['mode'] == 'long')
check('sign / dir_of_side：多 +1、空 −1、無倉 0',
      pm.sign('long') == 1 and pm.sign('short') == -1 and pm.sign(None) == 0
      and pm.dir_of_side('B') == 'long' and pm.dir_of_side('S') == 'short')

# ══ 3. 短倉 = 長倉鏡像（零成本；同一個持倉窗口、報酬反號）════════════════
print('── 短倉鏡像 ──')
N = 10
C1 = list(range(100, 110))                     # 100..109 單邊升
fake_cond('tB1', state(N, [2, 5, 7]))
fake_cond('tS1', state(N, [4, 6, 9]))
L = entry_of('tB1', 'tS1')                     # 買 B、賣 S
S = entry_of('tS1', 'tB1')                     # 買 S、賣 B（同一組訊號反轉）
rl = bt.run_backtest(L, mk(C1), ktype='K_DAY', opts=opts('long'))
rs = bt.run_backtest(S, mk(C1), ktype='K_DAY', opts=opts('short'))
win = lambda ts: [(t['entry_idx'], t['exit_idx']) for t in ts]  # noqa: E731
check('長倉窗口 (2→4)(5→6)(7→9)', win(rl['trades']) == [(2, 4), (5, 6), (7, 9)])
check('短倉用同一組訊號 → 窗口一模一樣', rs['ok'] and win(rs['trades']) == win(rl['trades']))
check('短倉每筆 dir 全部 = short、長倉全部 = long',
      all(t['dir'] == 'short' for t in rs['trades']) and all(t['dir'] == 'long' for t in rl['trades']))
check('零成本下：短倉淨% == −長倉毛%（逐筆鏡像）',
      all(close_enough(a['net_pct'], -b['gross_pct'])
          for a, b in zip(rs['trades'], rl['trades'])))
check('短倉每筆 pnl == −長倉每筆 pnl（等額注碼、可直接比較）',
      all(close_enough(a['pnl'], -b['pnl']) for a, b in zip(rs['trades'], rl['trades'])))
check('長倉賺（升市）→ 短倉蝕，總報酬正好相反',
      rl['metrics']['total_ret_pct'] > 0
      and close_enough(rs['metrics']['total_ret_pct'], -rl['metrics']['total_ret_pct']))
check('短倉指標反映：n_short_trades=3、short_exposure = coverage、長倉兩者為 0',
      rs['metrics']['n_short_trades'] == 3
      and close_enough(rs['metrics']['short_exposure_pct'], rs['metrics']['coverage_pct'])
      and rl['metrics']['n_short_trades'] == 0 and rl['metrics']['short_exposure_pct'] == 0.0)

# ══ 4. 短倉淨值曲線：升市持空倉 → 淨值逐根下跌（mark-to-market 方向啱）══
print('── 短倉淨值曲線 ──')
fake_cond('tBx', state(N, []))
fake_cond('tSy', state(N, [3]))
ro = bt.run_backtest(entry_of('tBx', 'tSy'), mk(C1), ktype='K_DAY', opts=opts('short'))
eq = ro['curve']['equity']
check('空倉遇 S → 開空倉（bar3），樣本完結未平倉 MTM',
      len(ro['trades']) == 1 and ro['trades'][0]['entry_idx'] == 3
      and ro['trades'][0]['open'] and ro['trades'][0]['dir'] == 'short')
check('開倉嗰根淨值未變（成交 = 收盤價，冇未來函數），之後逐根下跌',
      close_enough(eq[3], CAP) and all(eq[k] < eq[k - 1] for k in range(4, N)) and eq[9] < eq[4])
check('未平倉空倉 MTM：只扣開倉一邊成本（fee=0 → 淨值 = cap + pnl）',
      close_enough(eq[9], CAP + ro['trades'][0]['pnl']))
check('恆等式 total_ret == Σ pnl / cap（短倉未平倉）',
      close_enough(ro['metrics']['total_ret_pct'], sum(t['pnl'] for t in ro['trades']) / CAP * 100.0))

# ══ 5. 雙向：反手 = 兩筆（先平後開，同一根）+ 四邊成本 ══════════════════
print('── 雙向反手 ──')
fake_cond('tB2', state(N, [2, 7]))
fake_cond('tS2', state(N, [5]))
rb = bt.run_backtest(entry_of('tB2', 'tS2'), mk(C1), ktype='K_DAY', opts=opts('both'))
check('三筆：(2→5 多)(5→7 空)(7→9 多，未平倉 MTM)',
      rb['ok'] and win(rb['trades']) == [(2, 5), (5, 7), (7, 9)]
      and [t['dir'] for t in rb['trades']] == ['long', 'short', 'long'])
check('反手喺同一根完成：平倉 exit_idx == 新倉 entry_idx（冇未來函數）',
      rb['trades'][0]['exit_idx'] == rb['trades'][1]['entry_idx'] == 5
      and rb['trades'][1]['exit_idx'] == rb['trades'][2]['entry_idx'] == 7)
check('升市雙向：第一段賺、第二段（反手做空）蝕',
      rb['trades'][0]['net_pct'] > 0 and rb['trades'][1]['net_pct'] < 0)
check('指標：n_short_trades=1、n_open=1、n_trades=2',
      rb['metrics']['n_short_trades'] == 1 and rb['metrics']['n_open'] == 1
      and rb['metrics']['n_trades'] == 2)
# fee=1%、slip=0 → 每筆兩邊 = 2%；反手多咗一筆 → 合共四邊 = 8%
rf4 = bt.run_backtest(entry_of('tB2', 'tS2'), mk(C1), ktype='K_DAY', opts=opts('both', fee=1.0))
check('反手 = 四邊成本：兩筆已平倉 fee_pct 各 2%、總成本 = 4 × 1%',
      all(close_enough(t['fee_pct'], 2.0) for t in rf4['trades'] if not t['open'])
      and close_enough(sum(t['fee_pct'] for t in rf4['trades'] if not t['open']), 4.0))
check('每筆 cost_pct == gross − net 恆等（含反手、含成本）',
      all(close_enough(t['cost_pct'], t['gross_pct'] - t['net_pct']) for t in rf4['trades']))
check('雙向覆蓋率 = 長+空總覆蓋、空頭覆蓋 < 總覆蓋（兩者唔混淆）',
      rb['metrics']['short_exposure_pct'] > 0
      and rb['metrics']['short_exposure_pct'] < rb['metrics']['coverage_pct']
      and close_enough(rb['metrics']['coverage_pct'],
                       rb['metrics']['short_exposure_pct'] + 100.0 * 5 / (N - 1)))
check('恆等式 total_ret == Σ pnl / cap（雙向、含成本）',
      close_enough(rf4['metrics']['total_ret_pct'], sum(t['pnl'] for t in rf4['trades']) / CAP * 100.0))

# ══ 6. 同一策略喺三種模式嘅持倉行為必須唔同（模式真係生效）════════════
print('── 模式生效（唔係掛名）──')
fake_cond('tB3', state(N, [1, 3]))
fake_cond('tS3', state(N, [5, 8]))
P = entry_of('tB3', 'tS3')
r_long = bt.run_backtest(P, mk(C1), ktype='K_DAY', opts=opts('long'))
r_short = bt.run_backtest(P, mk(C1), ktype='K_DAY', opts=opts('short'))
r_both = bt.run_backtest(P, mk(C1), ktype='K_DAY', opts=opts('both'))
# 訊號：B@1 B@3 S@5 S@8（bar8 同向 → 短倉/雙向一律忽略，唔會加倉）
check('長倉：1 筆 (1→5 多)；B@3 持倉中 → inpos、S@8 空倉 → flat',
      win(r_long['trades']) == [(1, 5)] and not r_long['trades'][0]['open']
      and [(i['idx'], i['reason']) for i in r_long['ignored']] == [(3, 'bt_ig_inpos'), (8, 'bt_ig_flat')])
check('短倉：得 1 筆 (5→結尾 空，MTM)；B@1/B@3 空倉 → flat、S@8 同向 → inpos',
      win(r_short['trades']) == [(5, N - 1)] and r_short['trades'][0]['open']
      and r_short['trades'][0]['dir'] == 'short'
      and [(i['idx'], i['reason']) for i in r_short['ignored']]
      == [(1, 'bt_ig_flat'), (3, 'bt_ig_flat'), (8, 'bt_ig_inpos')])
check('雙向：(1→5 多) 反手 → (5→結尾 空，MTM)；S@8 同向照樣忽略（單倉模型、唔加倉）',
      win(r_both['trades']) == [(1, 5), (5, N - 1)]
      and [t['dir'] for t in r_both['trades']] == ['long', 'short']
      and r_both['trades'][1]['open']
      and [(i['idx'], i['reason']) for i in r_both['ignored']]
      == [(3, 'bt_ig_inpos'), (8, 'bt_ig_inpos')])
check('同一策略三模式 → 持倉行為三個結果（模式真係驅動，唔係標籤）',
      win(r_long['trades']) != win(r_short['trades']) != win(r_both['trades']))
check('預設（冇 mode key）== 長倉（零回歸 #33）',
      win(bt.run_backtest(P, mk(C1), ktype='K_DAY', opts=OPTS0)['trades'])
      == win(r_long['trades']))

# ══ 7. 被忽略計數：價 ≤ 0 獨立計數（唔再混入 ignored_in_pos）════════════
print('── 被忽略計數 ──')
C0 = [100.0, 101.0, 0.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0]
fake_cond('tB4', state(N, [2, 4]))
fake_cond('tS4', state(N, [6]))
rq = bt.run_backtest(entry_of('tB4', 'tS4'), mk(C0), ktype='K_DAY', opts=opts('long'))
check('價 0 → 唔開倉、獨立計數 ignored_badprice=1（ignored_in_pos 唔受污染）',
      rq['signals']['ignored_badprice'] == 1 and rq['signals']['ignored_in_pos'] == 0
      and [(i['idx'], i['reason']) for i in rq['ignored']] == [(2, 'bt_ig_badprice')])
check('指標卡同步反映 ignored_badprice', rq['metrics']['ignored_badprice'] == 1)
check('被忽略三類計數加總 == 被忽略明細筆數（無遺漏）',
      rq['signals']['ignored_in_pos'] + rq['signals']['ignored_flat'] + rq['signals']['ignored_badprice']
      == len(rq['ignored']))
check('訊號計數 = 成交 + 被忽略（三模式都成立）',
      all(r['signals']['n_buy'] == sum(1 for t in r['trades'] if t['dir'] == 'long')
          + sum(1 for i in r['ignored'] if i['side'] == 'B') for r in (r_long, r_short, r_both)))

# ══ 8. 指標 registry 一致性（頁靠呢度渲染，唔准有孤兒 key）═════════════
print('── registry 一致性 ──')
new_keys = ('n_short_trades', 'short_exposure_pct', 'ignored_badprice')
check('METRIC_DEFS 新增三個 key（全部喺 exec 組）',
      all(any(d.key == k and d.group == 'exec' for d in bt.METRIC_DEFS) for k in new_keys))
check('三個新指標喺三模式結果入面都有值（唔係 None）',
      all(r['metrics'][k] is not None for r in (r_long, r_short, r_both) for k in new_keys))
check('METRIC_DEFS 無重複 key', len({d.key for d in bt.METRIC_DEFS}) == len(bt.METRIC_DEFS))
check('每份結果嘅 metrics key 集合 == METRIC_DEFS（無孤兒、無缺）',
      set(r_both['metrics']) == {d.key for d in bt.METRIC_DEFS})
check('meta 如實回報是次 mode', r_both['meta']['mode'] == 'both' and r_long['meta']['mode'] == 'long')

print()
if FAILURES:
    print(f'❌ {len(FAILURES)} 項失敗：')
    for f in FAILURES:
        print('   - ' + f)
    sys.exit(1)
print('✅ 全部通過')

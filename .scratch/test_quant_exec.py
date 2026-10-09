# -*- coding: utf-8 -*-
"""執行層 #34c 測試 — 冇 Qt、冇 broker、冇網絡：淨測「訊號 → 落單意圖」嘅規則。

重點唔係 count，係**即時先有嘅三件事**（去重 / watermark / 未收根）同**風控 + 狀態誠實度**：
持倉狀態只可以喺落單成功後推進，失敗或過時都要如實反映，唔可以靜默當成交。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from gateway import position_model as pm  # noqa: E402
from gateway import quant_exec as qe  # noqa: E402
from gateway.strategies import trade_marks  # noqa: E402

FAILURES, PASSED = [], 0


def check(label, cond, detail=''):
    global PASSED
    if cond:
        PASSED += 1
        print(f'  ✅ {label}')
    else:
        FAILURES.append(label)
        print(f'  ❌ {label} {detail}')


# ── 固定策略 + 固定行情（ma_cross fast=2 slow=3，兩側各 100 分 → 一定觸發）──
ENTRY = {'id': 't1', 'name': '測試', 'mark_buffer': 0,
         'buy': [{'type': 'ma_cross', 'side': 'above', 'score': 100,
                  'params': {'fast': 2, 'slow': 3}}],
         'sell': [{'type': 'ma_cross', 'side': 'below', 'score': 100,
                   'params': {'fast': 2, 'slow': 3}}]}


def ohlc_of(closes):
    c = np.asarray(closes, dtype=float)
    return {'o': c, 'h': c, 'l': c, 'c': c, 'v': np.ones_like(c)}


# c 設計到 mark 一定喺 idx 3（B @12）同 idx 6（S @9）— 手工核對過 SMA2/SMA3
CLOSES = [10, 10, 10, 12, 12, 12, 9, 9, 9]
OHLC = ohlc_of(CLOSES)
MARKS = [(3, 12.0, 'B'), (6, 9.0, 'S')]
# 第二份：mark 喺 idx 3 嗰根收盤 = 0（停牌/缺數）→ 開唔到倉
CLOSES_BAD = [10, 10, 10, 0, 12, 12, 12, 9, 9]
OHLC_BAD = ohlc_of(CLOSES_BAD)
TIMES = ['2026-01-0%d 09:30' % (i + 1) for i in range(len(CLOSES))]


def binding(mode='both', bid='b1', code='HK.00700'):
    return qe.new_binding(binding_id=bid, code=code, ktype='K_1M', mode=mode, entry=ENTRY)


print('\n[0] 基準行情 sanity（策略計算冇改動）')
mk = trade_marks(ENTRY, OHLC)
check('marks 如預期 = idx3 B / idx6 S', mk == MARKS, mk)

print('\n[1] 風控參數清洗（0 = 最嚴，唔係無上限）')
check('clamp_max_positions 垃圾 → 預設', qe.clamp_max_positions('abc') == qe.MAX_POSITIONS_DEFAULT)
check('clamp_max_positions 0 保留 0', qe.clamp_max_positions(0) == 0)
check('clamp_max_positions 超界 clamp', qe.clamp_max_positions(99999) == qe.POS_HI)
check('clamp_max_trades_day 負數 clamp 到 0', qe.clamp_max_trades_day(-5) == 0)
check('clamp_cooldown_bars 字串數字都食', qe.clamp_cooldown_bars('2') == 2)
r = qe.norm_risk(None)
check('norm_risk(None) → 三項都有預設值',
      set(r) == {'max_positions', 'max_trades_day', 'cooldown_bars'}
      and r == {'max_positions': qe.MAX_POSITIONS_DEFAULT,
                'max_trades_day': qe.MAX_TRADES_DAY_DEFAULT,
                'cooldown_bars': qe.COOLDOWN_DEFAULT}, r)
g = qe.RiskGuard({'max_positions': 1})
g.update({'max_positions': 5})
check('RiskGuard.update 即時生效（唔使重建）', g.risk['max_positions'] == 5)

print('\n[2] 訊號閘：未收根 / watermark / 去重')
gate = qe.new_gate('b1')
check('未 arm baseline 都唔會行動最後一根（未收根）',
      [s['bar_idx'] for s in qe.collect_signals(gate, ENTRY, OHLC, TIMES)] == [3, 6])
check('訊號帶 price=訊號根收盤 + time（同回測成交價語義一致）',
      all(s['price'] == CLOSES[s['bar_idx']] and s['time'] == TIMES[s['bar_idx']]
          for s in qe.collect_signals(qe.new_gate('b1'), ENTRY, OHLC, TIMES)))
check('times 缺 → time 空字串，唔炸',
      all(s['time'] == '' for s in qe.collect_signals(qe.new_gate('x'), ENTRY, OHLC, None)))
g2 = qe.new_gate('b1')
qe.arm_baseline(g2, 5)      # 訂閱時得 5 根 → idx<=4 全部算歷史
sigs = qe.collect_signals(g2, ENTRY, OHLC, TIMES)
check('watermark：訂閱前嘅歷史訊號一律唔行動', [s['bar_idx'] for s in sigs] == [6])
check('watermark 如實計數（唔靜默吃咗）', g2['skipped_history'] == 1, g2['skipped_history'])
qe.arm_baseline(g2, 3)      # re-subscribe：bar index 重數 → 舊 seen 必須清
check('re-arm 清 seen（唔會錯手擋新訊號）',
      [s['bar_idx'] for s in qe.collect_signals(g2, ENTRY, OHLC, TIMES)] == [3, 6])
check('re-arm 重設 skipped_history 計數', g2['skipped_history'] == 0)
g3 = qe.new_gate('b1')
qe.arm_baseline(g3, 3)
qe.collect_signals(g3, ENTRY, OHLC, TIMES)
qe._consume(g3, {'binding_id': 'b1', 'bar_idx': 3, 'side': 'B'})
check('已 consume 嘅訊號唔會再出現（唔重覆落單）',
      [s['bar_idx'] for s in qe.collect_signals(g3, ENTRY, OHLC, TIMES)] == [6])
check('未 consume 嘅訊號下一輪會再見到（唔會漏）',
      len(qe.collect_signals(qe.new_gate('b1'), ENTRY, OHLC, TIMES)) == 2)
check('唔同 binding 各自去重（key 含 binding_id）',
      qe.sig_key({'binding_id': 'a', 'bar_idx': 3, 'side': 'B'})
      != qe.sig_key({'binding_id': 'b', 'bar_idx': 3, 'side': 'B'}))
check('FIRED_CAP 唔會無上限膨脹', len(qe.new_gate('z')['seen']) == 0 and qe.FIRED_CAP > 0)

print('\n[3] 落單方向（長短雙向 — 用戶：「支援沽空」）')
check('開多 = BUY', qe.order_side(pm.DIR_LONG, False) == 'BUY')
check('平多 = SELL', qe.order_side(pm.DIR_LONG, True) == 'SELL')
check('開空 = SELL', qe.order_side(pm.DIR_SHORT, False) == 'SELL')
check('平空 = BUY', qe.order_side(pm.DIR_SHORT, True) == 'BUY')
ords = qe.orders_for(code='HK.00700', prev={'dir': None}, nxt={'dir': 'long'}, price=12.0, qty=100)
check('開倉 → 一張單', len(ords) == 1 and ords[0]['side'] == 'BUY' and ords[0]['closing'] is False)
ords = qe.orders_for(code='HK.00700', prev={'dir': 'long'}, nxt={'dir': 'short'}, price=9.0, qty=100)
check('反手 → 兩張、先平後開（順序如實）',
      len(ords) == 2 and ords[0]['closing'] is True and ords[1]['closing'] is False)
check('反手兩張都係 SELL（平多 + 開空）', [o['side'] for o in ords] == ['SELL', 'SELL'])
check('n_orders_of：開/平 = 1、反手 = 2（每日筆數口徑）',
      qe.n_orders_of({'dir': None}, {'dir': 'long'}) == 1
      and qe.n_orders_of({'dir': 'long'}, {'dir': None}) == 1
      and qe.n_orders_of({'dir': 'long'}, {'dir': 'short'}) == 2)

print('\n[4] 全自動：持倉模式跟 position_model（同回測同一口徑）')
b = binding('both')
qe.arm_baseline(b['gate'], 3)
res = qe.on_bars(b, ENTRY, OHLC, TIMES, qty=100)
check('空倉遇 B → open，一張 BUY',
      len(res['orders']) == 1 and res['orders'][0]['action'] == pm.ACT_OPEN
      and [o['side'] for o in res['orders'][0]['orders']] == ['BUY'])
check('一次 poll 只行動一個訊號，其餘 deferred（唔會漏）',
      res['deferred'] == 1 and b['state'] == {'dir': 'long'})
res2 = qe.on_bars(b, ENTRY, OHLC, TIMES, qty=100)
check('下一輪先處理剩低嘅 S（deferred 唔會消失）',
      len(res2['orders']) == 1 and res2['orders'][0]['action'] == pm.ACT_FLIP)
check('雙向反手 → 持倉轉空', b['state'] == {'dir': 'short'})
res3 = qe.on_bars(b, ENTRY, OHLC, TIMES, qty=100)
check('全部訊號已處理 → 之後再無動作（去重有效）',
      res3['signals'] == [] and res3['orders'] == [])

bl = binding('long')
qe.arm_baseline(bl['gate'], 3)
r1 = qe.on_bars(bl, ENTRY, OHLC, TIMES, qty=100)
check('長倉模式：B 開多倉', bl['state'] == {'dir': 'long'} and len(r1['orders']) == 1)
r2 = qe.on_bars(bl, ENTRY, OHLC, TIMES, qty=100)
check('長倉模式：S 只平倉、唔反手',
      r2['orders'][0]['action'] == pm.ACT_CLOSE
      and [o['side'] for o in r2['orders'][0]['orders']] == ['SELL'] and bl['state'] == {'dir': None})
bs = binding('short')
qe.arm_baseline(bs['gate'], 3)
s1 = qe.on_bars(bs, ENTRY, OHLC, TIMES, qty=100)
check('短倉模式：B 被忽略並如實舉原因（bt_ig_flat）',
      any(i['reason_key'] == pm.IG_FLAT for i in s1['ignored']))
check('短倉模式：S 開空倉（SELL）',
      s1['orders'] and s1['orders'][0]['action'] == pm.ACT_OPEN
      and [o['side'] for o in s1['orders'][0]['orders']] == ['SELL'] and bs['state'] == {'dir': 'short'})
check('被忽略嘅訊號唔食行動名額（同一批照樣行動下一條）', len(s1['orders']) == 1)
check('處理過嘅訊號唔會再行動（單倉模型：唔會加倉、唔會重覆落單）',
      qe.on_bars(bs, ENTRY, OHLC, TIMES, qty=100)['signals'] == [])

print('\n[5] 資料誠實：價 ≤ 0 唔開倉、唔推進狀態')
bb = binding('both')
qe.arm_baseline(bb['gate'], 3)
rb = qe.on_bars(bb, ENTRY, OHLC_BAD, TIMES, qty=100)
check('收盤 ≤ 0 → 如實忽略（bt_ig_badprice）',
      rb['ignored'] and rb['ignored'][0]['reason_key'] == pm.IG_BADPRICE
      and rb['ignored'][0]['sig']['bar_idx'] == 3)
check('價唔啱嘅訊號唔會變成落單（唔扮成交）',
      not any(a['sig']['bar_idx'] == 3 for a in rb['orders']))
check('價唔啱唔阻同一批後續有效訊號（開倉嚟自 idx5 嗰條有效訊號）',
      len(rb['orders']) == 1 and rb['orders'][0]['sig']['bar_idx'] == 5
      and bb['state'] == {'dir': 'long'})

print('\n[6] 風控三項（用戶：「最小三項」）')
gr = qe.RiskGuard({'max_positions': 0, 'max_trades_day': 99, 'cooldown_bars': 0})
ok, rk = gr.check(code='X', binding_id='b1', bar_idx=3, prev={'dir': None},
                  nxt={'dir': 'long'}, open_dirs=[], today_count=0)
check('最大同時持倉數 = 0 → 開倉被擋', ok is False and rk == qe.RK_POSITIONS)
ok, _ = gr.check(code='X', binding_id='b1', bar_idx=3, prev={'dir': 'long'},
                 nxt={'dir': 'short'}, open_dirs=['long'], today_count=0)
check('反手唔增加持倉數 → 唔受 max_positions 擋', ok is True)
gr2 = qe.RiskGuard({'max_positions': 1, 'max_trades_day': 99, 'cooldown_bars': 0})
ok, rk = gr2.check(code='X', binding_id='b1', bar_idx=3, prev={'dir': None},
                   nxt={'dir': 'long'}, open_dirs=['short'], today_count=0)
check('已持 1 倉 + max=1 → 新開倉被擋', ok is False and rk == qe.RK_POSITIONS)
gr3 = qe.RiskGuard({'max_positions': 99, 'max_trades_day': 2, 'cooldown_bars': 0})
check('反手計 2 筆 → 超過每日上限就擋',
      gr3.check(code='X', binding_id='b1', bar_idx=3, prev={'dir': 'long'},
                nxt={'dir': 'short'}, open_dirs=[], today_count=1) == (False, qe.RK_TRADES))
check('未滿每日上限 → 過',
      gr3.check(code='X', binding_id='b1', bar_idx=3, prev={'dir': None},
                nxt={'dir': 'long'}, open_dirs=[], today_count=1)[0] is True)
gr4 = qe.RiskGuard({'max_positions': 99, 'max_trades_day': 99, 'cooldown_bars': 2})
gr4.note(code='X', binding_id='b1', bar_idx=3)
check('冷卻未夠根數 → 擋',
      gr4.check(code='X', binding_id='b1', bar_idx=4, prev={'dir': None},
                nxt={'dir': 'long'}, open_dirs=[], today_count=0) == (False, qe.RK_COOLDOWN))
check('夠冷卻 → 過',
      gr4.check(code='X', binding_id='b1', bar_idx=5, prev={'dir': None},
                nxt={'dir': 'long'}, open_dirs=[], today_count=0)[0] is True)
check('冷卻按 (code,binding) 計 — 唔同週期唔互相污染',
      gr4.check(code='X', binding_id='b2', bar_idx=4, prev={'dir': None},
                nxt={'dir': 'long'}, open_dirs=[], today_count=0)[0] is True)
check('唔同 code 唔互相污染',
      gr4.check(code='Y', binding_id='b1', bar_idx=4, prev={'dir': None},
                nxt={'dir': 'long'}, open_dirs=[], today_count=0)[0] is True)
gr5 = qe.RiskGuard({'cooldown_bars': 5})
check('冷卻 = 0 → 永不擋', qe.RiskGuard({'cooldown_bars': 0}).check(
    code='X', binding_id='b1', bar_idx=0, prev={'dir': None}, nxt={'dir': 'long'},
    open_dirs=[], today_count=0)[0] is True)
gr5.note(code='X', binding_id='b1', bar_idx=3)
gr5.reset()
check('reset 清冷卻記錄', gr5.check(code='X', binding_id='b1', bar_idx=4, prev={'dir': None},
                                   nxt={'dir': 'long'}, open_dirs=[], today_count=0)[0] is True)

b2 = binding('both')
qe.arm_baseline(b2['gate'], 3)
g6 = qe.RiskGuard({'max_positions': 0, 'max_trades_day': 99, 'cooldown_bars': 0})
rg = qe.on_bars(b2, ENTRY, OHLC, TIMES, g6, qty=100)
check('全自動被風控擋 → blocked 帶原因 key（俾頁如實講）',
      rg['blocked'] and rg['blocked'][0]['reason_key'] == qe.RK_POSITIONS and b2['state'] == {'dir': None})
check('被擋嘅訊號算已處理（唔會每 poll 重覆報告）',
      qe.on_bars(b2, ENTRY, OHLC, TIMES, g6, qty=100)['blocked'] == [])
b3 = binding('both')
qe.arm_baseline(b3['gate'], 3)
rq = qe.on_bars(b3, ENTRY, OHLC, TIMES, qe.RiskGuard(), qty=0)
check('數量唔合法（qty=0）→ 如實擋，唔落單',
      rq['blocked'] and rq['blocked'][0]['reason_key'] == qe.ERR_QTY and b3['state'] == {'dir': None})
check('open_dirs() 只數真正持有嘅倉',
      qe.open_dirs([{'state': {'dir': 'long'}}, {'state': {'dir': None}}, {'state': {}}]) == ['long'])

print('\n[7] 狀態誠實：commit / requeue')
b4 = binding('both')
qe.arm_baseline(b4['gate'], 3)
ra = qe.on_bars(b4, ENTRY, OHLC, TIMES, qty=100)
act = ra['orders'][0]
check('全自動：on_bars 已投機推進', b4['state'] == act['next_state'])
check('落單成功 → commit 確認（冪等，唔改狀態）', qe.commit(b4, act) is True
      and b4['state'] == act['next_state'])
check('落單失敗 → requeue 還原狀態', qe.requeue(b4, act) is True and b4['state'] == {'dir': None})
check('已還原過 → requeue 係冪等（唔使改，照回 True）', qe.requeue(b4, act) is True)
b4['state'] = {'dir': 'short'}      # 中途俾人改過（例如人手改持倉 / 另一條訊號）
check('狀態同 prev/next 都唔夾 → requeue 如實回 False，唔亂改',
      qe.requeue(b4, act) is False and b4['state'] == {'dir': 'short'})
b4['state'] = {'dir': None}
gc = qe.RiskGuard({'cooldown_bars': 5})
b5 = binding('both')
qe.arm_baseline(b5['gate'], 3)
rp = qe.on_bars(b5, ENTRY, OHLC, TIMES, auto=False, qty=100)
check('半自動：訊號入 pending 並附「會做乜」+ 落單內容',
      len(rp['pending']) == 2 and rp['pending'][0]['action'] == pm.ACT_OPEN
      and rp['pending'][0]['orders'][0]['side'] == 'BUY')
check('半自動：唔推進持倉狀態（人未確認）', b5['state'] == {'dir': None})
check('半自動：唔行風控（人就係風控）',
      qe.on_bars(binding('both'), ENTRY, OHLC, TIMES,
                 qe.RiskGuard({'max_positions': 0}), auto=False, qty=100)['pending'] != [])
check('半自動確認成功 → commit 先推進狀態', qe.commit(b5, rp['pending'][0], gc) is True
      and b5['state'] == {'dir': 'long'})
check('commit 同時記錄冷卻（成功先計）',
      gc.check(code='HK.00700', binding_id='b1', bar_idx=4, prev={'dir': 'long'},
               nxt={'dir': 'short'}, open_dirs=['long'], today_count=0) == (False, qe.RK_COOLDOWN))
stale = dict(rp['pending'][1], prev_state={'dir': 'short'})
check('行動已過時（狀態唔夾）→ commit 回 False 並唔亂改',
      qe.commit(b5, stale) is False and b5['state'] == {'dir': 'long'})

print('\n[8] 待執行佇列（半自動）')
pq = qe.PendingQueue(cap=2)
mk_act = lambda i, side: {'binding_id': 'b1', 'sig': {'binding_id': 'b1', 'bar_idx': i, 'side': side},
                          'action': pm.ACT_OPEN, 'prev_state': {'dir': None}, 'next_state': {'dir': 'long'},
                          'orders': [{'code': 'X', 'side': 'BUY', 'qty': 1, 'price': 1.0}]}
check('add 成功', pq.add(mk_act(3, 'B')) is True and len(pq) == 1)
check('同一訊號唔會入兩次', pq.add(mk_act(3, 'B')) is False and len(pq) == 1)
pq.add(mk_act(6, 'S'))
pq.add(mk_act(9, 'B'))
check('過量 → 丟最舊並計數（唔靜默吃咗）', len(pq) == 2 and pq.dropped == 1)
check('佇列內容係剩低嘅兩個', [a['sig']['bar_idx'] for a in pq.items()] == [6, 9])
key = qe.sig_key({'binding_id': 'b1', 'bar_idx': 6, 'side': 'S'})
taken = pq.take(key)
check('take 攞走（等人落單）', taken is not None and len(pq) == 1 and pq.get(key) is None)
check('落單失敗 → add 返入待執行', pq.add(taken) is True and len(pq) == 2)
rej = pq.reject(qe.sig_key({'binding_id': 'b1', 'bar_idx': 9, 'side': 'B'}), '人手拒絕')
check('reject 帶原因返嚟（俾事件日志）', rej is not None and rej['reject_reason'] == '人手拒絕')
check('clear 講明清咗幾多', pq.clear() == 1 and len(pq) == 0)

print('\n' + '=' * 60)
if FAILURES:
    print(f'❌ {len(FAILURES)} 項失敗：')
    for f in FAILURES:
        print('   - ' + f)
    raise SystemExit(1)
print(f'✅ 全部執行層測試通過（{PASSED} 項）— 量化執行層 34c 交付')

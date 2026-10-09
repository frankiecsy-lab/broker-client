# -*- coding: utf-8 -*-
"""純計算測試 — gateway/backtest.py 回測引擎（ticket #33a）。

Run: python .scratch/test_backtest_engine.py   （from project root）
無 Qt、無網絡：手砌合成 K 線 + 手砌訊號（注入臨時 condition def 精確控制 B/S 位置），
斷言：持倉狀態機／被忽略訊號／未平倉 MTM／成本可調且完全可加／淨值曲線逐根估值／
指標手算對照／時間軸年數實測 vs fallback／暖機回報／字串格式。
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from gateway import backtest as bt  # noqa: E402
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
    """注入一個只返指定 bool 陣列嘅 condition → 精確控制訊號位置（測狀態機，唔測條件計算）。"""
    st.CONDITION_DEFS[key] = st.ConditionDef(
        key, key, (), 'test', lambda ohlc, p, side: np.asarray(state, dtype=bool))


def entry_of(buy_key, sell_key, name='測試策略'):
    r = lambda k: {'type': k, 'side': 'above', 'params': {}, 'score': 100}
    return {'id': 't-1', 'name': name, 'buy': [r(buy_key)], 'sell': [r(sell_key)],
            'entry_price': 'market', 'qty': 'min_lot', 'mark_buffer': 10}


def state(n, idxs):
    a = [False] * n
    for i in idxs:
        a[i] = True
    return a


CAP = 10000.0
OPTS0 = {'capital': CAP, 'fee_pct': 0.0, 'slip_pct': 0.0, 'rf_pct': 0.0}


# ══ 1. 持倉狀態機：開倉→平倉→再開→再平（冇被忽略訊號）════════════════════
print('── 持倉狀態機 ──')
N = 10
C1 = list(range(100, 110))          # 100..109 單邊升
fake_cond('tB1', state(N, [2, 5, 7]))
fake_cond('tS1', state(N, [4, 6, 9]))
r1 = bt.run_backtest(entry_of('tB1', 'tS1'), mk(C1), times=None, ktype='K_DAY', opts=OPTS0)
check('3 筆交易：(2→4)(5→6)(7→9)',
      r1['ok'] and [(t['entry_idx'], t['exit_idx']) for t in r1['trades']]
      == [(2, 4), (5, 6), (7, 9)])
check('訊號計數 B=3 S=3、被忽略 = 0',
      r1['signals']['n_buy'] == 3 and r1['signals']['n_sell'] == 3
      and r1['signals']['ignored_in_pos'] == 0 and r1['signals']['ignored_flat'] == 0)
check('全部已平倉（n_open=0、n_trades=3）',
      r1['metrics']['n_open'] == 0 and r1['metrics']['n_trades'] == 3)
check('持倉根數逐筆 = [2,1,2]', [t['bars'] for t in r1['trades']] == [2, 1, 2])

# ══ 2. 被忽略訊號：持倉中 B / 空倉 S 如實計數（持倉處理喺結果入面現形）═════
print('── 被忽略訊號 ──')
fake_cond('tB2', state(N, [1, 3]))
fake_cond('tS2', state(N, [5, 8]))
r2 = bt.run_backtest(entry_of('tB2', 'tS2'), mk(C1), ktype='K_DAY', opts=OPTS0)
check('得 1 筆：(1→5)', [(t['entry_idx'], t['exit_idx']) for t in r2['trades']] == [(1, 5)])
check('ignored_in_pos=1（bar3 持倉中 B）、ignored_flat=1（bar8 空倉 S）',
      r2['signals']['ignored_in_pos'] == 1 and r2['signals']['ignored_flat'] == 1)
check('被忽略明細兩條 + 原因 key 齊（bt_ig_inpos / bt_ig_flat）',
      [(i['idx'], i['reason']) for i in r2['ignored']] == [(3, 'bt_ig_inpos'), (8, 'bt_ig_flat')])
check('指標卡同步反映（ignored_in_pos/ignored_flat 唔係 None）',
      r2['metrics']['ignored_in_pos'] == 1 and r2['metrics']['ignored_flat'] == 1)

# ══ 3. 未平倉倉：樣本完結 mark-to-market 如實結算 ═══════════════════════
print('── 未平倉倉 MTM ──')
fake_cond('tB3', state(N, [3]))
fake_cond('tS3', state(N, []))
r3 = bt.run_backtest(entry_of('tB3', 'tS3'), mk(C1), ktype='K_DAY',
                     opts={'capital': CAP, 'fee_pct': 0.3, 'slip_pct': 0.2, 'rf_pct': 0.0})
t3 = r3['trades'][0]
check('1 筆 open=True、exit_idx = 最後一根、exit_reason=end',
      len(r3['trades']) == 1 and t3['open'] and t3['exit_idx'] == N - 1
      and t3['exit_reason'] == 'end')
check('未平倉只計開倉一邊成本（fee_pct=0.3，唔預扣平倉邊）',
      close_enough(t3['fee_pct'], 0.3))
check('未平倉 exit_eff = 最後一根收盤原價（冇平倉滑價）',
      close_enough(t3['exit_eff'], float(C1[-1])))
check('n_open=1、n_trades（已平倉）=0',
      r3['metrics']['n_open'] == 1 and r3['metrics']['n_trades'] == 0)
check('恆等式：total_ret_pct == Σ 每筆淨盈虧（含未平倉）',
      close_enough(r3['metrics']['total_ret_pct'], sum(t['net_pct'] for t in r3['trades']), 1e-9))
check('恆等式：淨值曲線最後一點 == 初始資金 + Σ 每筆盈虧',
      close_enough(r3['curve']['equity'][-1], CAP + sum(t['pnl'] for t in r3['trades']), 1e-6))

# ══ 4. 指標手算對照（無成本、單筆、有時間軸）════════════════════════════
print('── 指標手算對照 ──')
C4 = [100.0, 102.0, 101.0, 105.0, 104.0, 108.0]
n4 = len(C4)
ts4 = pd.date_range('2024-01-01', periods=n4, freq='D')
fake_cond('tB4', state(n4, [1]))
fake_cond('tS4', state(n4, [3]))
r4 = bt.run_backtest(entry_of('tB4', 'tS4'), mk(C4), times=ts4, ktype='K_DAY', opts=OPTS0)
m4, tr4 = r4['metrics'], r4['trades'][0]
exp_net = (105.0 / 102.0 - 1.0) * 100.0
check('單筆：入 102 → 出 105，淨% = 2.9411765', close_enough(tr4['net_pct'], exp_net, 1e-9))
check('總報酬率 == 該筆淨%（等額注碼、唔復利）', close_enough(m4['total_ret_pct'], exp_net, 1e-9))
check('總盈虧金額 = 10000 × 淨% = 294.1176', close_enough(m4['total_pnl'], CAP * exp_net / 100.0, 1e-9))
check('勝率 100%、最大連續獲利 1、最大連續虧損 0',
      close_enough(m4['win_rate'], 100.0) and m4['max_consec_wins'] == 1
      and m4['max_consec_losses'] == 0)
check('無虧損 → 總虧損 0、獲利因子 None（如實，唔扮 0/inf）',
      close_enough(m4['gross_loss'], 0.0) and m4['profit_factor'] is None)
check('平均每筆 = 中位數 = 最佳 = 最差 = 該筆淨%',
      all(close_enough(m4[k], exp_net, 1e-9) for k in
          ('avg_trade_pct', 'median_trade_pct', 'best_trade_pct', 'worst_trade_pct', 'expectancy_pct')))
# 淨值曲線逐根 mark-to-market
eq4 = r4['curve']['equity']
check('淨值曲線：持倉中按市價估值（bar2 = 10000 + 10000×(101/102−1)）',
      close_enough(eq4[2], CAP + CAP * (101.0 / 102.0 - 1.0), 1e-9))
check('淨值曲線：開倉嗰根未動（= 初始資金）、平倉嗰根轉已實現',
      close_enough(eq4[1], CAP, 1e-9) and close_enough(eq4[3], CAP + CAP * exp_net / 100.0, 1e-9))
check('最大回撤 = 持倉中嗰段（−0.98039%）、以固定 base 計',
      close_enough(m4['max_dd_pct'], (CAP * (101.0 / 102.0 - 1.0)) / CAP * 100.0, 1e-9))
check('回撤事件 1 次、已修復', m4['n_drawdowns'] == 1 and m4['dd_unrecovered_days'] is None)
check('Buy&Hold 對照 = 100→108 = +8%、超額報酬 = 淨% − 8%',
      close_enough(m4['buy_hold_ret_pct'], 8.0, 1e-9)
      and close_enough(m4['excess_ret_pct'], exp_net - 8.0, 1e-9))
check('覆蓋率 = 持倉 2 根 / 可計 5 根 = 40%', close_enough(m4['coverage_pct'], 40.0, 1e-9))
check('平均/最長持倉 = 2 根、平均持倉日數 = 2.0 日',
      close_enough(m4['avg_hold_bars'], 2.0) and m4['max_hold_bars'] == 2
      and close_enough(m4['avg_hold_days'], 2.0, 1e-9))
check('時間軸實測：跨度 5 日、每年根數 365.25、來源 = measured',
      close_enough(m4['span_days'], 5.0, 1e-9) and close_enough(m4['bars_per_year'], 365.25, 1e-9)
      and r4['meta']['bpy_source'] == 'measured')
check('年化（單利）= 總% ÷ 年數（5/365.25 年）',
      close_enough(m4['ann_ret_pct'], exp_net / (5.0 / 365.25), 1e-6))
check('夏普/索提諾/卡瑪/貝塔/資訊比率/恢復因子全部算得出（唔係 None）',
      all(m4[k] is not None and math.isfinite(m4[k]) for k in
          ('sharpe', 'sortino', 'calmar', 'beta', 'info_ratio', 'recovery_factor')))
# per-bar 報酬序列（無成本、持倉 1→3）：r = [0,0,101/102−1,105/101−1,0,0]
r_manual = [0.0, 0.0, 101.0 / 102.0 - 1.0, 105.0 / 101.0 - 1.0, 0.0, 0.0]
shp = float(np.mean(r_manual)) / float(np.std(r_manual, ddof=1)) * math.sqrt(365.25)
check('無風險利率 0 → 夏普 = mean/std×√bpy（手算對照；空倉根計 0）',
      close_enough(m4['sharpe'], shp, 1e-9))
check('年化波動度 = std×√bpy（含空倉根 → 如實反映喺市場時間）',
      close_enough(m4['ann_vol_pct'], float(np.std(r_manual, ddof=1)) * math.sqrt(365.25) * 100.0, 1e-9))
check('貝塔 vs Buy&Hold 算得出且 < 1（只喺 2 根有倉）',
      m4['beta'] is not None and 0.0 < m4['beta'] < 1.0)

# ══ 5. 成本可調：手续费% + 滑價% 兩邊均計，且 gross − cost == net 完全可加 ══
print('── 成本參數 ──')
OPTS_C = {'capital': CAP, 'fee_pct': 1.0, 'slip_pct': 0.5, 'rf_pct': 0.0}
r5 = bt.run_backtest(entry_of('tB4', 'tS4'), mk(C4), times=ts4, ktype='K_DAY', opts=OPTS_C)
t5, m5 = r5['trades'][0], r5['metrics']
check('開倉價 ×(1+slip)、平倉價 ×(1−slip)',
      close_enough(t5['entry_eff'], 102.0 * 1.005) and close_enough(t5['exit_eff'], 105.0 * 0.995))
check('手续费兩邊 = 2.0%', close_enough(t5['fee_pct'], 2.0))
check('可加恆等式：gross_pct − cost_pct == net_pct',
      close_enough(t5['gross_pct'] - t5['cost_pct'], t5['net_pct'], 1e-12))
check('成本合計 = 該筆 cost_pct、金額 = base × cost%、滑價 = cost − fee',
      close_enough(m5['total_cost_pct'], t5['cost_pct'])
      and close_enough(m5['total_cost_amount'], CAP * t5['cost_pct'] / 100.0)
      and close_enough(m5['total_slip_pct'], t5['cost_pct'] - t5['fee_pct']))
check('成本佔毛利% = cost/gross×100',
      close_enough(m5['cost_to_gross_pct'], t5['cost_pct'] / t5['gross_pct'] * 100.0))
check('成本把一筆由贏變蝕（勝率 0%、獲利因子 0）',
      t5['net_pct'] < 0 and close_enough(m5['win_rate'], 0.0) and close_enough(m5['profit_factor'], 0.0))
check('成本 = 0 時同無成本結果一致',
      close_enough(bt.run_backtest(entry_of('tB4', 'tS4'), mk(C4), times=ts4, ktype='K_DAY',
                                   opts=OPTS0)['metrics']['total_ret_pct'], m4['total_ret_pct']))
check('參數 clamp：資金/成本/無風險利率越界一律收返範圍',
      bt.clamp_capital(1) == bt.CAPITAL_LO and bt.clamp_capital(1e12) == bt.CAPITAL_HI
      and bt.clamp_cost(-3) == 0.0 and bt.clamp_cost(99) == bt.COST_HI
      and bt.clamp_rf(-1) == 0.0 and bt.clamp_bars('abc') == bt.BARS_DEFAULT)

# ══ 6. 混合勝負：勝率／獲利因子／盈虧比／連續虧損 ═════════════════════════
print('── 勝負統計 ──')
C6 = [100.0, 100.0, 110.0, 100.0, 100.0, 95.0, 100.0, 100.0, 90.0, 100.0,
      100.0, 120.0, 100.0, 100.0, 98.0, 100.0]
n6 = len(C6)
fake_cond('tB6', state(n6, [1, 4, 7, 10, 13]))
fake_cond('tS6', state(n6, [2, 5, 8, 11, 14]))
r6 = bt.run_backtest(entry_of('tB6', 'tS6'), mk(C6), ktype='K_DAY', opts=OPTS0)
m6, tr6 = r6['metrics'], r6['trades']
nets = [t['net_pct'] for t in tr6]
check('5 筆（1→2 贏、4→5 蝕、7→8 蝕、10→11 贏、13→14 蝕）',
      len(tr6) == 5 and [t['net_pct'] > 0 for t in tr6] == [True, False, False, True, False])
check('勝率 = 2/5 = 40%', close_enough(m6['win_rate'], 40.0, 1e-9))
check('最大連續虧損 = 2、最大連續獲利 = 1',
      m6['max_consec_losses'] == 2 and m6['max_consec_wins'] == 1)
gp = sum(p for p in (t['pnl'] for t in tr6) if p > 0)
gl = -sum(p for p in (t['pnl'] for t in tr6) if p < 0)
check('獲利因子 = 總盈利/總虧損', close_enough(m6['profit_factor'], gp / gl, 1e-9))
aw = np.mean([t['net_pct'] for t in tr6 if t['pnl'] > 0])
al = np.mean([t['net_pct'] for t in tr6 if t['pnl'] < 0])
check('盈虧比 = |平均獲利/平均虧損|', close_enough(m6['payoff_ratio'], abs(aw / al), 1e-9))
check('總盈虧 = Σ 每筆（唔復利、等額注碼）', close_enough(m6['total_pnl'], sum(t['pnl'] for t in tr6), 1e-9))
check('換手率 = 筆數/年（K_DAY fallback 252）',
      close_enough(m6['turnover_per_year'], 5.0 / ((n6 - 1) / 252.0), 1e-9))

# ══ 7. 時間軸不可用 → ktype fallback（來源如實標明）═══════════════════════
print('── 年數來源 fallback ──')
r7 = bt.run_backtest(entry_of('tB4', 'tS4'), mk(C4), times=None, ktype='K_DAY', opts=OPTS0)
check('冇時間軸 → bpy 用 K_DAY 對照表 252、bpy_source=ktype_table',
      close_enough(r7['metrics']['bars_per_year'], 252.0) and r7['meta']['bpy_source'] == 'ktype_table')
check('冇時間軸 → 持倉日數/跨度如實 None（唔估）',
      r7['metrics']['avg_hold_days'] is None and r7['metrics']['span_days'] is None
      and r7['trades'][0]['days'] is None)
r7b = bt.run_backtest(entry_of('tB4', 'tS4'), mk(C4), times=[None] * n4, ktype='K_5M', opts=OPTS0)
check('未知/缺時間軸 + K_5M → 66×252', close_enough(r7b['metrics']['bars_per_year'], 66.0 * 252))

# ══ 8. 暖機根數（唔好將未夠暖機當冇訊號）═════════════════════════════════
print('── 暖機回報 ──')
check('rule_warmup：MA slow=120 → 120',
      bt.rule_warmup({'type': 'ma_cross', 'params': {'fast': 20, 'slow': 120}}) == 120)
check('rule_warmup：BOLL period=20 → 20',
      bt.rule_warmup({'type': 'boll_cross', 'params': {'period': 20, 'dev': 2}}) == 20)
check('rule_warmup：VOB period+confirm+sweep+1',
      bt.rule_warmup({'type': 'vob_break', 'params': {'period': 14, 'confirm': 3, 'sweep': 5}}) == 23)
check('warmup_bars = 各條件最大；真策略（MA20/MA120）→ 120 入指標卡',
      bt.warmup_bars([{'type': 'ma_cross', 'params': {'fast': 20, 'slow': 120}},
                      {'type': 'boll_cross', 'params': {'period': 20}}]) == 120
      and bt.run_backtest(
          {'buy': [{'type': 'ma_cross', 'side': 'above',
                    'params': {'fast': 20, 'slow': 120}, 'score': 100}], 'sell': []},
          mk(C1), ktype='K_DAY', opts=OPTS0)['metrics']['warmup_bars'] == 120)

# ══ 9. 邊界：短樣本 / 零訊號 / 壞價 ══════════════════════════════════════
print('── 邊界如實回報 ──')
r9 = bt.run_backtest(entry_of('tB4', 'tS4'), mk([100.0]), opts=OPTS0)
check('得 1 根 → ok=False + reason=bt_err_short（唔炸）',
      r9['ok'] is False and r9['reason'] == 'bt_err_short')
check('壞數據（NaN 收盤）→ ok=False + reason=bt_err_data',
      bt.run_backtest(entry_of('tB4', 'tS4'), mk([100.0, float('nan'), 101.0]),
                      opts=OPTS0)['reason'] == 'bt_err_data')
fake_cond('tB4', state(n4, []))
fake_cond('tS4', state(n4, []))
r9c = bt.run_backtest(entry_of('tB4', 'tS4'), mk(C4), ktype='K_DAY', opts=OPTS0)
check('零訊號 → ok=True、0 筆、關鍵指標 None（唔扮 0）、樣本資訊照列',
      r9c['ok'] and r9c['metrics']['n_trades'] == 0 and r9c['metrics']['sharpe'] is None
      and r9c['metrics']['max_dd_pct'] == 0.0 and r9c['metrics']['n_bars'] == n4)
check('零訊號 → 覆蓋率 0%、總報酬 0%（淨值曲線恆等初始資金）',
      close_enough(r9c['metrics']['coverage_pct'], 0.0)
      and close_enough(r9c['metrics']['total_ret_pct'], 0.0)
      and all(close_enough(v, CAP, 1e-9) for v in r9c['curve']['equity']))
check('價 ≤ 0 唔開倉（如實忽略 bt_ig_badprice）',
      (lambda c: (fake_cond('tB9', state(4, [1])), fake_cond('tS9', state(4, [])),
                  'bt_ig_badprice' in [i['reason'] for i in
                                      bt.run_backtest(entry_of('tB9', 'tS9'), mk(c),
                                                      ktype='K_DAY', opts=OPTS0)['ignored']]
                  and bt.run_backtest(entry_of('tB9', 'tS9'), mk(c), ktype='K_DAY',
                                      opts=OPTS0)['metrics']['n_open'] == 0))([100.0, 0.0, 101.0, 102.0]))

# ══ 10. 渲染契約：METRIC_DEFS / 分組 / 字串格式 ═══════════════════════════
print('── 渲染契約 ──')
keys = [d.key for d in bt.METRIC_DEFS]
check('METRIC_DEFS 無重複 key', len(keys) == len(set(keys)))
check('結果 metrics 覆蓋全部 METRIC_DEFS key', all(k in r6['metrics'] for k in keys))
grp = bt.metrics_by_group()
check('四組齊（ret/risk/adj/exec）且總數 == METRIC_DEFS',
      set(grp) == set(bt.GROUPS) and sum(len(v) for v in grp.values()) == len(keys))
check('用戶指定指標全部喺度（夏普/索提諾/卡瑪/阿爾法/貝塔/資訊比率/回撤修復…）',
      all(k in keys for k in ('sharpe', 'sortino', 'calmar', 'alpha_pct', 'beta', 'info_ratio',
                              'max_dd_recovery_days', 'max_consec_losses', 'ann_vol_pct',
                              'avg_hold_days', 'total_cost_pct', 'profit_factor', 'win_rate')))
check('每條都有 label_key（三語 i18n 來源）+ 合法 fmt',
      all(d.label_key.startswith('bt_m_') and d.fmt in bt._FMT_DIGITS for d in bt.METRIC_DEFS))
check('fmt_value：None → 空字串（頁顯示 —）、非有限 → 空字串',
      bt.fmt_value(None, 'pct') == '' and bt.fmt_value(float('nan'), 'pct') == ''
      and bt.fmt_value(float('inf'), 'ratio') == '')
check('fmt_value：pct 2 位、int 千分位、days 1 位',
      bt.fmt_value(12.3456, 'pct') == '12.35' and bt.fmt_value(1234567, 'int') == '1,234,567'
      and bt.fmt_value(2.0, 'days') == '2.0')
check('曲線長度 == 樣本根數、回撤全為 ≤0',
      len(r6['curve']['equity']) == n6 and len(r6['curve']['bench']) == n6
      and len(r6['curve']['dd_pct']) == n6 and max(r6['curve']['dd_pct']) <= 1e-12)
check('交易明細欄位齊（時間/價格/根數/日數/成本/淨%/open）',
      all(k in r6['trades'][0] for k in ('entry_time', 'entry_raw', 'exit_raw', 'bars', 'days',
                                         'gross_pct', 'fee_pct', 'slip_pct', 'cost_pct',
                                         'net_pct', 'pnl', 'open', 'exit_reason')))

for k in ('tB1', 'tS1', 'tB2', 'tS2', 'tB3', 'tS3', 'tB4', 'tS4', 'tB6', 'tS6', 'tB9', 'tS9'):
    st.CONDITION_DEFS.pop(k, None)

print()
if FAILURES:
    print(f'❌ {len(FAILURES)} 項失敗：{FAILURES}')
    sys.exit(1)
print(f'✅ 全部通過（{len(FAILURES)} 失敗）— 回測引擎 33a 交付')

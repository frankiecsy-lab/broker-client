"""純計算測試 — gateway/strategies.py 分數制條件契約（ticket #25）。

Run: python .scratch/test_strategy_conditions.py   （from project root）
無 Qt、無網絡：手砌合成 K 線，斷言 MA／BOLL／VOB 條件 signal、分數累加 score_series、
trigger_indices（rising edge）、清洗 sanitize、摘要格式。
#32：生效期／is_active 已退役 — 策略 = 純規則集（舊檔多餘欄 _sanitize 忽略）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from gateway import strategies as st  # noqa: E402

FAILURES = []


def check(name, ok):
    print(('  ✅ ' if ok else '  ❌ ') + name, flush=True)
    if not ok:
        FAILURES.append(name)


def mk(c):
    c = np.asarray(c, dtype=float)
    return {'o': c, 'h': c + 0.1, 'l': c - 0.1, 'c': c, 'v': np.full(c.shape[0], 100.0)}


# ══ 1. MA 交叉（fast=2 slow=3，升市 bar5 金叉）════════════════════════════
print('── MA 交叉 signal ──')
up = mk([1, 1, 1, 1, 1, 2, 3])
r_ma_up = {'type': 'ma_cross', 'side': 'above', 'params': {'fast': 2, 'slow': 3}, 'score': 100}
sig = st.rule_signal(r_ma_up, up)
check('升市：bar5 金叉（SMA2 1.5 > SMA3 1.33）→ [F,F,F,F,F,T,T]（相等/NaN = False）',
      sig.dtype == bool and sig.tolist() == [False] * 5 + [True, True])
dn = mk([3, 3, 3, 3, 3, 2, 1])
sig_b = st.rule_signal({'type': 'ma_cross', 'side': 'below',
                        'params': {'fast': 2, 'slow': 3}, 'score': 100}, dn)
check('跌市 below：bar5 死叉（SMA2 2.5 < SMA3 2.67）→ [F,F,F,F,F,T,T]',
      sig_b.tolist() == [False] * 5 + [True, True])

# ══ 2. BOLL 穿線（dev=0.5 先穿到：n=5 單尖峰 dev=2 數學上永遠唔會穿）══════
print('── BOLL 穿線 signal ──')
boll_c = [10.0] * 20 + [31.0]
bo = mk(boll_c)
r_boll = {'type': 'boll_cross', 'side': 'above',
          'params': {'period': 5, 'dev': 0.5, 'line': 'upper'}, 'score': 60}
sb = st.rule_signal(r_boll, bo)
check('C>upper：只有尖峰 bar20 成立（flat 段全部 False）',
      sb.shape[0] == 21 and bool(sb[20]) and not sb[:20].any())
r_mid = {'type': 'boll_cross', 'side': 'below',
         'params': {'period': 5, 'dev': 2.0, 'line': 'mid'}, 'score': 40}
sm = st.rule_signal(r_mid, bo)
check('C<mid：flat 段相等=False、尖峰 bar20 都唔成立 → 全 False（如實）', not sm.any())

# ══ 3. VOB 穿線（照 e2e_gui_indicators Part 9 手砌 fixture）═══════════════
print('── VOB 穿線 signal ──')
FIX = [(105.0, 105.5, 104.0, 104.5), (104.5, 105.0, 103.5, 104.0), (104.0, 104.2, 102.5, 103.0),
       (103.0, 105.5, 102.8, 105.2), (105.2, 107.0, 104.5, 106.8), (106.8, 108.0, 106.0, 107.5)]
vo = {'o': np.array([b[0] for b in FIX]), 'h': np.array([b[1] for b in FIX]),
      'l': np.array([b[2] for b in FIX]), 'c': np.array([b[3] for b in FIX]),
      'v': np.full(6, 100.0)}
vob_params = {'period': 1, 'strength': 1.0, 'confirm': 3, 'sweep': 2,
              'max_size': 3.0, 'pen': 50, 'supersede': 1, 'max_zones': 15}
r_vob = {'type': 'vob_break', 'side': 'above', 'params': vob_params, 'score': 50}
sv = st.rule_signal(r_vob, vo)
check('VOB above：區塊 bar2 起（top≈104.2）→ bar3 收盤 105.2 穿咗先至 True（無區塊段 = False）',
      sv.shape[0] == 6 and sv.dtype == bool and sv[3:].all() and not sv[:3].any())
r_vob_d = {'type': 'vob_break', 'side': 'below', 'params': vob_params, 'score': 50}
check('VOB below：呢個升市 fixture 收盤由未跌穿區塊底 → 全 False',
      not st.rule_signal(r_vob_d, vo).any())

# ══ 4. 分數契約：累加 + rising edge 觸發（用戶核心要求）═══════════════════
print('── score_series / trigger_indices ──')
r1 = {'type': 'ma_cross', 'side': 'above', 'params': {'fast': 2, 'slow': 3}, 'score': 60}
r2 = {'type': 'boll_cross', 'side': 'above',
      'params': {'period': 5, 'dev': 0.5, 'line': 'mid'}, 'score': 50}
sc = st.score_series([r1, r2], up)
check('60+50 兩條同時成立 → 累加 110；未夠分段 = 0/60',
      sc[5] == 110 and sc[6] == 110 and sc[4] == 0)
check('trigger_indices：≥100 嘅第一根 = bar5（之後持續成立唔再觸發 — rising edge）',
      list(st.trigger_indices(sc)) == [5])
sc1 = st.score_series([r1], up)
check('單條 60 分 → 永遠未夠 100 → 冇觸發', list(st.trigger_indices(sc1)) == [])
sc2 = st.score_series([r1, r2], up)
check('自訂 threshold=60 → bar5 觸發（threshold 參數有效）',
      list(st.trigger_indices(sc2, threshold=60)) == [5])
check('未知 type → 全 False（如實，唔炸）',
      not st.rule_signal({'type': 'nope', 'side': 'above', 'params': {}, 'score': 9}, up).any())

# ══ 5. 清洗 sanitize_rule ══════════════════════════════════════════════════
print('── sanitize_rule ──')
check('未知 type / 唔係 dict → None',
      st.sanitize_rule({'type': 'nope'}) is None and st.sanitize_rule('x') is None)
clean = st.sanitize_rule({'type': 'ma_cross', 'side': 'x', 'params': {'fast': 1, 'slow': 9999},
                          'score': 999})
check('side 唔合法→above / 參數 clamp（1→2、9999→400）/ score clamp（999→100）',
      clean == {'type': 'ma_cross', 'side': 'above', 'params': {'fast': 2, 'slow': 400},
                'score': 100})
check('缺 params → 用 default；score 唔係數 → clamp 到下限 1',
      st.sanitize_rule({'type': 'ma_cross'}) == {'type': 'ma_cross', 'side': 'above',
                                                 'params': {'fast': 20, 'slow': 120}, 'score': 1}
      and st.sanitize_rule({'type': 'boll_cross', 'params': {'line': 'zzz'}}
                           )['params']['line'] == 'upper')

# ══ 6. （#32 刪：生效期／is_active 退役）════════════════════════════════

# ══ 7. 摘要（語言中立，表欄／backtest report 同一份）═════════════════════
print('── 摘要格式 ──')
check('MA/BOLL/VOB 摘要 + 分數',
      st.rule_summary({'type': 'ma_cross', 'side': 'above',
                       'params': {'fast': 20, 'slow': 120}, 'score': 60}) == 'MA20>MA120 (+60)'
      and st.rule_summary({'type': 'boll_cross', 'side': 'below',
                           'params': {'period': 20, 'dev': 2.0, 'line': 'lower'},
                           'score': 40}) == 'C<BOLL.L (+40)'
      and st.rule_summary({'type': 'vob_break', 'side': 'above', 'params': {},
                           'score': 30}) == 'C>VOB (+30)')
check('多條用 ＋ 連接', st.rules_summary([
    {'type': 'ma_cross', 'side': 'above', 'params': {'fast': 20, 'slow': 120}, 'score': 60},
    {'type': 'vob_break', 'side': 'above', 'params': {}, 'score': 40}]) == 'MA20>MA120 (+60) ＋ C>VOB (+40)')

# ══ 8. ohlc_from_kline（backtest 入口契約）════════════════════════════════
print('── ohlc_from_kline ──')
import pandas as pd  # noqa: E402
df = pd.DataFrame({'open': [1.0, 2.0], 'high': [1.5, 2.5], 'low': [0.5, 1.5],
                   'close': [1.2, 2.2], 'volume': [10, 20]})
o = st.ohlc_from_kline(df)
check('DataFrame → {o,h,l,c,v} ndarray 逐個對應',
      set(o) == {'o', 'h', 'l', 'c', 'v'} and np.allclose(o['c'], [1.2, 2.2])
      and o['v'].dtype == float)

# ══ 9. trade_marks（#27 行情頁 B/S 標記契約：同 live 觸發一模一樣的純函數）══
print('── trade_marks ──')
both = mk([1, 1, 1, 1, 1, 2, 3, 3, 3, 2, 1])   # bar5 金叉（SMA2 1.5>1.33）、bar9 死叉（2.5<2.67）
entry = {'id': 's-test', 'name': 'x',   # #32：entry 冇 code/validity
         'buy': [{'type': 'ma_cross', 'side': 'above', 'params': {'fast': 2, 'slow': 3}, 'score': 100}],
         'sell': [{'type': 'ma_cross', 'side': 'below', 'params': {'fast': 2, 'slow': 3}, 'score': 100}]}
marks = st.trade_marks(entry, both)
check('升→跌：B=bar5、S=bar9（各一個 rising edge，持續成立唔重複標）',
      [(i, s) for i, _p, s in marks] == [(5, 'B'), (9, 'S')])
check('價格 = 觸發根收盤（entry_price=market 語義）',
      all(p == float(both['c'][i]) for i, p, _s in marks))
check('輸出升冪交錯（B,S 按 bar 排序）', [i for i, _p, _s in marks] == sorted(i for i, _p, _s in marks))
check('冇規則 / 空 entry → 空列表（唔炸）',
      st.trade_marks({'buy': [], 'sell': []}, both) == [] and st.trade_marks({}, both) == [])
check('淨買入：sell 冇規則 → 得 B', [s for _i, _p, s in st.trade_marks(
    {'buy': entry['buy'], 'sell': []}, both)] == ['B'])

# ══ 10. #29 mark_buffer（B/S 淡化範圍：策略可調）══
print('── mark_buffer ──')
check('clamp_mark_buffer：None/垃圾→預設10、-5→0、9999→200、"7"→7',
      st.clamp_mark_buffer(None) == 10 and st.clamp_mark_buffer('x') == 10
      and st.clamp_mark_buffer(-5) == 0 and st.clamp_mark_buffer(9999) == 200
      and st.clamp_mark_buffer('7') == 7 and st.clamp_mark_buffer(2.9) == 2)
check('_sanitize：舊檔冇 mark_buffer → 自動補預設 10；舊檔 code/validity/created 多餘欄 = 忽略（#32 零遷移）',
      st._sanitize({'id': 's-x', 'name': 'n', 'code': 'HK.00700', 'validity': '1d',
                    'created': 'garbage'})['mark_buffer'] == 10
      and 'code' not in st._sanitize({'id': 's-x', 'name': 'n', 'code': 'HK.00700'})
      and 'validity' not in st._sanitize({'id': 's-x', 'validity': '7d'})
      and st._sanitize({'id': 's-y', 'mark_buffer': '999'})['mark_buffer'] == 200)

print('\n' + ('全部通過 ✅' if not FAILURES else f'失敗 {len(FAILURES)} 項：{FAILURES}'))
sys.exit(0 if not FAILURES else 1)

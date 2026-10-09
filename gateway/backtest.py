# -*- coding: utf-8 -*-
"""回測領域層 — 食 `gateway/strategies.py` 嘅分數／訊號契約，加「持倉 + 計量」呢一层。

純計算：冇 Qt、冇 broker、冇 I/O。輸入 = 策略 entry + ohlc ndarray + 時間軸 + 參數；
輸出 = trades / signals / metrics / curve / meta。

## 計量口徑（用戶明確要求「唔准復利」）
* **每筆等額注碼**：名義資金恆等 = `capital`（初始資金）。淨值曲線 =
  初始資金 + Σ已平倉淨盈虧 + 未平倉 mark-to-market（**單利累加**）。所有 % 嘅分母都係
  呢個固定 base → 每筆表現互相獨立，唔會因為早期贏蝕而失真。
* 年化 = 總報酬% ÷ 年數（**單利年化**，同唔復利口徑一致）。年數優先由**真實時間軸**量度
  （`bars_per_year` 實測），時間軸不可用才退回 ktype 對照表（`meta.bpy_source` 如實標明邊種）。
* 波動度／夏普／索提諾／貝塔／資訊比率用 **per-bar 報酬序列**（空倉根 = 0，即「喐唔喐市場」
  如實反映）；偏度／峰度／VaR 用**持倉中**嘅根（先至係「呢個策略持倉時」嘅分布）。
  覆蓋率（喺市場時間%）另行列出，兩者唔會混淆。
* 成本 = 手续费% + 滑價%，**兩邊均計**、參數可調。滑價直接落價，永遠朝**唔利自己**嗰邊
  （開倉 ×(1+s·slip)、平倉 ×(1−s·slip)，s = 倉位方向：多 +1 / 空 −1），
  手续费按名義 % 計。逐筆用**精確**式；per-bar 序列用一階近似（開／平倉根各扣 (fee+slip)），
  兩者相差屬二階小量 — 呢度如實標明，唔扮到完全一致。

## 持倉模型（長倉／短倉／雙向，可揀；如實計數 → 持倉處理喺結果入面睇到）
* 狀態機唔喺呢度寫：一律行 `gateway/position_model.step()`（同量化交易頁**共用一份定義**）。
  呢檔只負責成交、成本、結算同計量。
* 長倉：空倉遇 B 開倉、持倉遇 S 全平。短倉：反向。雙向：反向訊號 = **反手兩筆**（先平後開，
  成本四邊如實計）；同向訊號 → **忽略**，但如實計數（`ignored_in_pos` / `ignored_flat` /
  `ignored_badprice` + 明細表）。
* 樣本完結仍持倉 → 以最後一根收盤 mark-to-market 平倉（`open=True`、`exit_reason='end'`）。
* 成交價 = 訊號根收盤（同 `strategies.trade_marks` 語義一致，冇未來函數）。
* 統計（勝率／獲利因子／連續虧損等）計**全部交易含未平倉 MTM**；`n_trades`（已平倉）同
  `n_open` 分開列，唔混水。
* 指標暖機根數（`warmup_bars`）如實回報 — 前面冇訊號可能只係暖機，唔好當「策略冇訊號」。
"""
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from gateway import position_model as pm

SECTION = 'backtest'

# ── 參數範圍（頁同引擎共用；敏感值一律由頁輸入，呢度唔 hardcode 資金）──
CAPITAL_DEFAULT = 100_000.0
CAPITAL_LO, CAPITAL_HI = 1_000.0, 100_000_000.0
COST_LO, COST_HI = 0.0, 5.0          # 手续费% / 滑價%（每邊，0 = 不計成本）
RF_LO, RF_HI = 0.0, 20.0             # 年化無風險利率 %
BARS_DEFAULT = 1000
BARS_LO, BARS_HI = 50, 20000

# 時間軸不可用先至用呢個 fallback（每年 bar 數，港股日數 ~252 為基準）
_BARS_PER_YEAR_FALLBACK = {
    'K_1M': 330.0 * 252, 'K_3M': 110.0 * 252, 'K_5M': 66.0 * 252,
    'K_15M': 22.0 * 252, 'K_30M': 11.0 * 252, 'K_60M': 6.0 * 252,
    'K_DAY': 252.0, 'K_WEEK': 52.0, 'K_MON': 12.0, 'K_QUARTER': 4.0, 'K_YEAR': 1.0,
}
TRADING_DAYS_PER_YEAR = 252.0


def _clamp(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(v):
        return float(default)
    return max(float(lo), min(float(hi), v))


def clamp_capital(v):
    return _clamp(v, CAPITAL_LO, CAPITAL_HI, CAPITAL_DEFAULT)


def clamp_cost(v):
    return _clamp(v, COST_LO, COST_HI, 0.0)


def clamp_rf(v):
    return _clamp(v, RF_LO, RF_HI, 0.0)


def clamp_bars(v):
    try:
        v = int(float(v))
    except (TypeError, ValueError):
        return BARS_DEFAULT
    return max(BARS_LO, min(BARS_HI, v))


def norm_opts(opts):
    """opts（頁傳入）→ 乾淨參數小數（fee/slip/rf 轉 decimal；mode 過 `position_model` 收斂）。"""
    o = opts or {}
    return {'capital': clamp_capital(o.get('capital')),
            'fee': clamp_cost(o.get('fee_pct')) / 100.0,
            'slip': clamp_cost(o.get('slip_pct')) / 100.0,
            'rf': clamp_rf(o.get('rf_pct')) / 100.0,
            'mode': pm.clamp_mode(o.get('mode'))}


# ─────── 暖機根數（如實回報，唔好將「未夠暖機」當「冇訊號」）───────

def rule_warmup(rule):
    """一條 rule 需要嘅最少 bar 數（之後先有意義）；未知 type → 0。"""
    t_, p = rule.get('type'), rule.get('params') or {}
    try:
        if t_ == 'ma_cross':
            return int(p.get('slow', 0))
        if t_ == 'boll_cross':
            return int(p.get('period', 0))
        if t_ == 'vob_break':
            return int(p.get('period', 0)) + int(p.get('confirm', 0)) + int(p.get('sweep', 0)) + 1
    except (TypeError, ValueError):
        return 0
    return 0


def warmup_bars(rules):
    return max([rule_warmup(r) for r in (rules or [])] or [0])


# ─────── 指標 registry（分組／格式／i18n label 單一來源 — 頁只照呢度渲染）───────

GROUPS = ('ret', 'risk', 'adj', 'exec')


@dataclass(frozen=True)
class MetricDef:
    key: str
    group: str          # ret 收益表現 / risk 風險控制 / adj 綜合風險回報 / exec 交易執行
    fmt: str            # pct | money | ratio | int | days | bars | rate
    label_key: str      # i18n key（三語；單位寫喺 label 入面，數值本身語言中立）


METRIC_DEFS = (
    # ── 收益表現 ──
    MetricDef('total_ret_pct', 'ret', 'pct', 'bt_m_total_ret'),
    MetricDef('ann_ret_pct', 'ret', 'pct', 'bt_m_ann_ret'),
    MetricDef('total_pnl', 'ret', 'money', 'bt_m_total_pnl'),
    MetricDef('gross_profit', 'ret', 'money', 'bt_m_gross_profit'),
    MetricDef('gross_loss', 'ret', 'money', 'bt_m_gross_loss'),
    MetricDef('win_rate', 'ret', 'pct', 'bt_m_win_rate'),
    MetricDef('profit_factor', 'ret', 'ratio', 'bt_m_profit_factor'),
    MetricDef('payoff_ratio', 'ret', 'ratio', 'bt_m_payoff'),
    MetricDef('avg_trade_pct', 'ret', 'pct', 'bt_m_avg_trade'),
    MetricDef('avg_win_pct', 'ret', 'pct', 'bt_m_avg_win'),
    MetricDef('avg_loss_pct', 'ret', 'pct', 'bt_m_avg_loss'),
    MetricDef('expectancy_pct', 'ret', 'pct', 'bt_m_expectancy'),
    MetricDef('median_trade_pct', 'ret', 'pct', 'bt_m_median_trade'),
    MetricDef('std_trade_pct', 'ret', 'pct', 'bt_m_std_trade'),
    MetricDef('best_trade_pct', 'ret', 'pct', 'bt_m_best_trade'),
    MetricDef('worst_trade_pct', 'ret', 'pct', 'bt_m_worst_trade'),
    MetricDef('buy_hold_ret_pct', 'ret', 'pct', 'bt_m_bh_ret'),
    MetricDef('excess_ret_pct', 'ret', 'pct', 'bt_m_excess_ret'),
    # ── 風險控制 ──
    MetricDef('max_dd_pct', 'risk', 'pct', 'bt_m_max_dd'),
    MetricDef('max_dd_amount', 'risk', 'money', 'bt_m_max_dd_amt'),
    MetricDef('ann_vol_pct', 'risk', 'pct', 'bt_m_ann_vol'),
    MetricDef('downside_vol_pct', 'risk', 'pct', 'bt_m_downside_vol'),
    MetricDef('max_consec_losses', 'risk', 'int', 'bt_m_max_consec_loss'),
    MetricDef('max_consec_wins', 'risk', 'int', 'bt_m_max_consec_win'),
    MetricDef('max_dd_recovery_days', 'risk', 'days', 'bt_m_dd_recovery'),
    MetricDef('dd_unrecovered_days', 'risk', 'days', 'bt_m_dd_unrecovered'),
    MetricDef('max_dd_duration_bars', 'risk', 'bars', 'bt_m_dd_duration'),
    MetricDef('n_drawdowns', 'risk', 'int', 'bt_m_n_drawdowns'),
    MetricDef('var95_bar_pct', 'risk', 'pct', 'bt_m_var95'),
    MetricDef('skew', 'risk', 'ratio', 'bt_m_skew'),
    MetricDef('kurtosis', 'risk', 'ratio', 'bt_m_kurtosis'),
    MetricDef('buy_hold_max_dd_pct', 'risk', 'pct', 'bt_m_bh_max_dd'),
    # ── 綜合風險回報 ──
    MetricDef('sharpe', 'adj', 'ratio', 'bt_m_sharpe'),
    MetricDef('sortino', 'adj', 'ratio', 'bt_m_sortino'),
    MetricDef('calmar', 'adj', 'ratio', 'bt_m_calmar'),
    MetricDef('recovery_factor', 'adj', 'ratio', 'bt_m_recovery_factor'),
    MetricDef('beta', 'adj', 'ratio', 'bt_m_beta'),
    MetricDef('alpha_pct', 'adj', 'pct', 'bt_m_alpha'),
    MetricDef('info_ratio', 'adj', 'ratio', 'bt_m_info_ratio'),
    MetricDef('buy_hold_sharpe', 'adj', 'ratio', 'bt_m_bh_sharpe'),
    # ── 交易執行（持倉處理如實在呢度現形）──
    MetricDef('n_trades', 'exec', 'int', 'bt_m_n_trades'),
    MetricDef('n_open', 'exec', 'int', 'bt_m_n_open'),
    MetricDef('n_short_trades', 'exec', 'int', 'bt_m_n_short'),
    MetricDef('n_buy_signals', 'exec', 'int', 'bt_m_n_buy'),
    MetricDef('n_sell_signals', 'exec', 'int', 'bt_m_n_sell'),
    MetricDef('ignored_in_pos', 'exec', 'int', 'bt_m_ignored_inpos'),
    MetricDef('ignored_flat', 'exec', 'int', 'bt_m_ignored_flat'),
    MetricDef('ignored_badprice', 'exec', 'int', 'bt_m_ignored_badprice'),
    MetricDef('avg_hold_bars', 'exec', 'bars', 'bt_m_avg_hold_bars'),
    MetricDef('avg_hold_days', 'exec', 'days', 'bt_m_avg_hold_days'),
    MetricDef('max_hold_bars', 'exec', 'bars', 'bt_m_max_hold_bars'),
    MetricDef('coverage_pct', 'exec', 'pct', 'bt_m_coverage'),
    MetricDef('short_exposure_pct', 'exec', 'pct', 'bt_m_short_exposure'),
    MetricDef('turnover_per_year', 'exec', 'rate', 'bt_m_turnover'),
    MetricDef('total_cost_pct', 'exec', 'pct', 'bt_m_total_cost'),
    MetricDef('total_cost_amount', 'exec', 'money', 'bt_m_total_cost_amt'),
    MetricDef('total_slip_pct', 'exec', 'pct', 'bt_m_total_slip'),
    MetricDef('cost_to_gross_pct', 'exec', 'pct', 'bt_m_cost_to_gross'),
    MetricDef('n_bars', 'exec', 'int', 'bt_m_n_bars'),
    MetricDef('span_days', 'exec', 'days', 'bt_m_span_days'),
    MetricDef('bars_per_year', 'exec', 'rate', 'bt_m_bpy'),
    MetricDef('warmup_bars', 'exec', 'bars', 'bt_m_warmup'),
)

_FMT_DIGITS = {'pct': 2, 'money': 2, 'ratio': 3, 'rate': 2, 'days': 1, 'bars': 0, 'int': 0}


def fmt_value(v, fmt):
    """指標值 → 語言中立數字字串（單位已寫喺 label）；None/非有限 → ''（頁顯示 '—'，如實）。"""
    if v is None:
        return ''
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ''
    if not math.isfinite(f):
        return ''
    if fmt == 'int' or fmt == 'bars':
        return f'{int(round(f)):,d}'
    return f'{f:,.{_FMT_DIGITS.get(fmt, 2)}f}'


def metrics_by_group():
    """→ {group: [MetricDef...]}（渲染順序跟 METRIC_DEFS）。"""
    out = {g: [] for g in GROUPS}
    for d in METRIC_DEFS:
        out[d.group].append(d)
    return out


# ─────── 時間軸 ───────

def _as_times(times, n):
    """任何時間軸輸入 → 長度 n 嘅 list[ pd.Timestamp | None ]。"""
    try:
        arr = pd.to_datetime(pd.Series(list(times)), errors='coerce').to_numpy()
    except Exception:
        arr = np.full(n, np.datetime64('NaT'), dtype='datetime64[ns]')
    if arr.shape[0] != n:
        arr = np.full(n, np.datetime64('NaT'), dtype='datetime64[ns]')
    out = []
    for x in arr:
        try:
            out.append(None if pd.isna(x) else pd.Timestamp(x))
        except (TypeError, ValueError):
            out.append(None)
    return out


def _ts_str(t):
    return '' if t is None else t.strftime('%Y-%m-%d %H:%M')


def _days_between(a, b):
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 86400.0


def _year_span(ts, n):
    """由真實時間軸量度年數 + 每年 bar 數 → (years, bpy) 或 (None, None)。"""
    valid = [i for i, t in enumerate(ts) if t is not None]
    if len(valid) < 2:
        return None, None
    span = _days_between(ts[valid[0]], ts[valid[-1]])
    if span is None or span <= 0:
        return None, None
    years = span / 365.25
    if years <= 0:
        return None, None
    return years, (n - 1) / years


def _san(v):
    """非有限 / 不可轉數 → None（頁如實顯示 '—'）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _std(a):
    a = np.asarray(a, dtype=float)
    return float(np.std(a, ddof=1)) if a.shape[0] >= 2 else None


def _moments(a):
    """(偏度, 峰度) — 樣本不足或零方差 → (None, None)。"""
    a = np.asarray(a, dtype=float)
    if a.shape[0] < 3:
        return None, None
    m = float(np.mean(a))
    s2 = float(np.mean((a - m) ** 2))
    if s2 <= 0:
        return None, None
    return _san(float(np.mean((a - m) ** 3) / s2 ** 1.5)), \
        _san(float(np.mean((a - m) ** 4) / s2 ** 2 - 3.0))


# ─────── 主入口 ───────

def _empty_result(reason=None):
    return {'ok': reason is None, 'reason': reason, 'trades': [], 'ignored': [],
            'signals': {}, 'metrics': {d.key: None for d in METRIC_DEFS},
            'curve': {'times': [], 'equity': [], 'bench': [], 'dd_pct': []},
            'meta': {}}


def run_backtest(entry, ohlc, times=None, ktype='', opts=None):
    """策略 entry + 行情 → 完整回測結果。

    entry  = strategies 嘅策略（buy/sell rule 列表）；ohlc = strategies.ohlc_from_kline(df)；
    times  = df['time_key']（可缺 → 年數退回 ktype 對照表）；ktype = 週期字串（僅 fallback 用）；
    opts   = {capital, fee_pct, slip_pct, rf_pct, mode}（mode = long/short/both，見 position_model）。
    """
    from gateway.strategies import trade_marks

    c = np.asarray(ohlc.get('c'), dtype=float) if ohlc else np.zeros(0)
    n = int(c.shape[0])
    o = norm_opts(opts)
    cap = o['capital']
    if n < 2:
        return _empty_result('bt_err_short')
    if not np.all(np.isfinite(c)):
        return _empty_result('bt_err_data')

    ts = _as_times(times, n)
    buy, sell = entry.get('buy') or [], entry.get('sell') or []
    marks = trade_marks(entry, ohlc)

    # ── 持倉狀態機（方向由 `gateway/position_model.py` 決定 — 同量化交易頁共用；被忽略訊號如實計數）──
    trades, ignored = [], []
    sgn = np.zeros(n, dtype=int)            # sgn[t] = 第 t 根收盤時嘅倉位方向（+1 多 / −1 空 / 0 無倉）
    state = pm.new_state()
    pos = None
    n_buy = n_sell = ig_inpos = ig_flat = ig_bad = 0
    for (i, price, side) in marks:
        if i >= n:
            continue
        if side == pm.SIDE_B:
            n_buy += 1
        else:
            n_sell += 1
        action, nxt, reason = pm.step(state, side, o['mode'])
        if action in (pm.ACT_OPEN, pm.ACT_FLIP) and not price > 0:
            # 價 <= 0（停牌/缺數）→ 算唔到百分比，如實忽略；反手都一齊唔做，state 唔 commit
            action, reason = pm.ACT_IGNORE, pm.IG_BADPRICE
        if action == pm.ACT_IGNORE:
            if reason == pm.IG_INPOS:
                ig_inpos += 1
            elif reason == pm.IG_FLAT:
                ig_flat += 1
            else:
                ig_bad += 1
            ignored.append({'idx': int(i), 'time': _ts_str(ts[i]), 'side': side,
                            'price': float(price), 'reason': reason})
            continue
        if action in (pm.ACT_CLOSE, pm.ACT_FLIP):
            trades.append(_settle(pos, int(i), float(price), ts, False, o, cap))
            pos = None
        if action in (pm.ACT_OPEN, pm.ACT_FLIP):
            d = pm.dir_of_side(side)        # 反手 = 同一根先平後開（兩筆，成本四邊如實計）
            pos = {'i': int(i), 'raw': float(price), 'dir': d,
                   'eff': float(price) * (1.0 + pm.sign(d) * o['slip']), 't': ts[i]}
        state = nxt
    if pos is not None:                     # 樣本完結仍持倉 → mark-to-market 如實結算
        trades.append(_settle(pos, n - 1, float(c[n - 1]), ts, True, o, cap))

    for t in trades:                        # 持倉區間回填（覆蓋率 / per-bar 序列都要）
        sgn[t['entry_idx']:(n if t['open'] else t['exit_idx'])] = pm.sign(t['dir'])

    # ── 淨值曲線（等額注碼、單利累加；持倉中一律 mark-to-market）──
    # 持倉中：已實現 + 按當前收盤估值（開倉成本已扣）；平倉嗰根起轉為已實現。
    # 未平倉倉以最後一根收盤結算，**只計已發生嘅開倉成本**（平倉成本未付出 → 唔預扣），
    # 所以 total_ret_pct == Σ 每筆淨盈虧 呢個恆等式永遠成立，報告內部唔會自相矛盾。
    open_at = [None] * n
    for t in trades:
        for k in range(t['entry_idx'], t['exit_idx']):
            open_at[k] = t
    realized_at = {}
    for t in trades:
        realized_at[t['exit_idx']] = realized_at.get(t['exit_idx'], 0.0) + t['pnl']
    equity = np.empty(n)
    realized = 0.0
    for k in range(n):
        realized += realized_at.get(k, 0.0)
        t = open_at[k]
        unreal = 0.0 if t is None else cap * pm.sign(t['dir']) * (c[k] / t['entry_eff'] - 1.0) \
            - cap * t['fee_pct'] / 100.0
        equity[k] = cap + realized + unreal

    # Buy & Hold 對照（同口徑、等額注碼、冇成本）；起始價不可用 → 基準如實「算唔到」（唔扮 0）
    bench_ok = c[0] > 0
    bench = (cap + cap * (c / c[0] - 1.0)) if bench_ok else np.full(n, np.nan)
    dd = equity - np.maximum.accumulate(equity)              # ≤0
    dd_pct = dd / cap * 100.0
    bench_dd = (bench - np.maximum.accumulate(bench)) if bench_ok else np.full(n, np.nan)

    # ── per-bar 報酬序列（開倉根／平倉根各扣一邊成本，一階近似）──
    with np.errstate(divide='ignore', invalid='ignore'):
        r = np.zeros(n)
        r[1:] = sgn[:-1] * (c[1:] / c[:-1] - 1.0)   # 空倉根 = 0；空頭根 = 行情報酬反號
        rb = np.zeros(n)
        rb[1:] = c[1:] / c[:-1] - 1.0
    # 上一根收盤 = 0（缺數/停牌）→ 呢根報酬算唔到 = 0（如實，唔炸、唔扮有波動）
    r = np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)
    rb = np.nan_to_num(rb, nan=0.0, posinf=0.0, neginf=0.0)
    cost_side = o['fee'] + o['slip']
    for t in trades:
        r[t['entry_idx']] -= cost_side
        r[t['exit_idx']] -= cost_side

    # ── 年數 / 每年根數（優先實測，退回 ktype 表 — 來源如實標明）──
    years, bpy = _year_span(ts, n)
    bpy_src = 'measured'
    if bpy is None or bpy <= 0:
        bpy = _BARS_PER_YEAR_FALLBACK.get(str(ktype).upper())
        bpy_src = 'ktype_table'
        if bpy is None:
            bpy = TRADING_DAYS_PER_YEAR
            bpy_src = 'default'
        years = (n - 1) / bpy if n > 1 else None

    sig = {'n_buy': n_buy, 'n_sell': n_sell,
           'ignored_in_pos': ig_inpos, 'ignored_flat': ig_flat, 'ignored_badprice': ig_bad,
           'warmup': warmup_bars(list(buy) + list(sell))}
    metrics = _compute_metrics(trades, equity, bench, dd, bench_dd, r, rb, sgn,
                               cap, o, years, bpy, n, ts, buy, sell, sig)

    first = next((t for t in ts if t is not None), None)
    last = next((t for t in reversed(ts) if t is not None), None)
    return {
        'ok': True, 'reason': None,
        'trades': trades, 'ignored': ignored, 'signals': sig,
        'metrics': metrics,
        'curve': {'times': [_ts_str(t) for t in ts],
                  'equity': equity.tolist(),
                  'bench': ([] if not bench_ok else bench.tolist()),   # 空 = 冇基準，曲線唔畫疊加線
                  'dd_pct': dd_pct.tolist()},
        'meta': {'n_bars': n, 'capital': cap, 'fee_pct': o['fee'] * 100.0,
                 'slip_pct': o['slip'] * 100.0, 'rf_pct': o['rf'] * 100.0, 'mode': o['mode'],
                 'ktype': str(ktype or ''), 'years': _san(years), 'bars_per_year': _san(bpy),
                 'bpy_source': bpy_src,
                 'first_time': _ts_str(first), 'last_time': _ts_str(last),
                 'strategy_name': str(entry.get('name') or '')},
    }


def _settle(pos, j, price, ts, is_open, o, cap):
    """平倉／未平倉結算 → 一筆交易。

    口徑**完全可加**：`gross_pct`（純行情，raw→raw，已乘倉位方向）− `cost_pct` = `net_pct`，
    其中 `cost_pct` 用殘差定義（= gross − net），所以滑價、手续费同兩者嘅交互項一律如實入成本，
    唔會漏亦唔會重複計。未平倉倉（`is_open`）：以最後一根收盤估值、**平倉嗰邊嘅滑價／手续费未付出
    → 唔預扣**（fee 只計開倉一邊），呢先至係「而家嘅賬」。
    空頭（`dir='short'`）同一式，只係方向 s = −1：滑價永遠朝唔利自己嗰邊、報酬反號、
    注碼照樣等額（`pnl = cap × net_pct`）→ 長短兩邊嘅每筆表現可以直接比較。
    """
    s = pm.sign(pos['dir'])
    exit_raw = float(price)
    exit_eff = exit_raw if is_open else exit_raw * (1.0 - s * o['slip'])
    fee_pct = o['fee'] * (1.0 if is_open else 2.0) * 100.0
    gross_pct = s * (exit_raw / pos['raw'] - 1.0) * 100.0
    net_pct = s * (exit_eff / pos['eff'] - 1.0) * 100.0 - fee_pct
    cost_pct = gross_pct - net_pct
    days = _days_between(pos['t'], ts[j])
    return {'entry_idx': pos['i'], 'entry_time': _ts_str(pos['t']), 'dir': pos['dir'],
            'entry_raw': pos['raw'], 'entry_eff': pos['eff'],
            'exit_idx': int(j), 'exit_time': _ts_str(ts[j]),
            'exit_raw': exit_raw, 'exit_eff': exit_eff,
            'bars': int(j - pos['i']), 'days': _san(days),
            'gross_pct': gross_pct, 'fee_pct': fee_pct,
            'slip_pct': cost_pct - fee_pct, 'cost_pct': cost_pct,
            'net_pct': net_pct, 'pnl': cap * net_pct / 100.0,
            'open': bool(is_open), 'exit_reason': 'end' if is_open else 'signal'}


def _compute_metrics(trades, equity, bench, dd, bench_dd, r, rb, sgn,
                     cap, o, years, bpy, n, ts, buy, sell, sig):
    """全部指標（None = 呢個樣本算唔出，如實，唔扮零）。"""
    m = {d.key: None for d in METRIC_DEFS}
    hold = sgn != 0     # 「喺市場時間」：持多倉定空倉都算，唔區分方向（coverage = 總覆蓋）
    closed = [t for t in trades if not t['open']]
    nets = [t['net_pct'] for t in trades]
    pnls = [t['pnl'] for t in trades]
    k = len(trades)

    # ── 收益表現 ──
    final_pnl = float(equity[-1] - cap)
    m['total_ret_pct'] = final_pnl / cap * 100.0
    m['total_pnl'] = final_pnl
    m['ann_ret_pct'] = _san(m['total_ret_pct'] / years) if years else None
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    m['gross_profit'] = float(sum(wins)) if wins else 0.0
    m['gross_loss'] = float(-sum(losses)) if losses else 0.0
    if k:
        m['win_rate'] = len(wins) / k * 100.0
        m['avg_trade_pct'] = float(np.mean(nets))
        m['expectancy_pct'] = m['avg_trade_pct']
        m['median_trade_pct'] = float(np.median(nets))
        m['std_trade_pct'] = _std(nets)
        m['best_trade_pct'] = float(np.max(nets))
        m['worst_trade_pct'] = float(np.min(nets))
        m['avg_win_pct'] = float(np.mean([t['net_pct'] for t in trades if t['pnl'] > 0])) if wins else None
        m['avg_loss_pct'] = float(np.mean([t['net_pct'] for t in trades if t['pnl'] < 0])) if losses else None
        m['payoff_ratio'] = _san(abs(m['avg_win_pct'] / m['avg_loss_pct'])) \
            if m['avg_win_pct'] and m['avg_loss_pct'] else None
        m['profit_factor'] = _san(m['gross_profit'] / m['gross_loss']) if m['gross_loss'] > 0 else None
        run_l = run_w = cl = cw = 0
        for t in trades:
            cl = cl + 1 if t['pnl'] < 0 else 0
            cw = cw + 1 if t['pnl'] > 0 else 0
            run_l = max(run_l, cl)
            run_w = max(run_w, cw)
        m['max_consec_losses'] = run_l
        m['max_consec_wins'] = run_w

    # 起始價不可用 → 基準相關指標如實 None（唔扮 0）；per-bar 基準報酬（rb）照樣可用
    bh_ret = _san((bench[-1] - cap) / cap * 100.0)
    m['buy_hold_ret_pct'] = bh_ret
    m['excess_ret_pct'] = _san(m['total_ret_pct'] - bh_ret) \
        if bh_ret is not None and m['total_ret_pct'] is not None else None
    m['buy_hold_max_dd_pct'] = _san(bench_dd.min() / cap * 100.0)

    # ── 風險控制 ──
    m['max_dd_pct'] = float(dd.min()) / cap * 100.0
    m['max_dd_amount'] = float(dd.min())
    episodes = _dd_episodes(equity, ts)
    rec = [e['days'] for e in episodes if e['recovered'] and e['days'] is not None]
    m['max_dd_recovery_days'] = max(rec) if rec else None
    unre = [e['days'] for e in episodes if not e['recovered'] and e['days'] is not None]
    m['dd_unrecovered_days'] = max(unre) if unre else None
    m['max_dd_duration_bars'] = max(e['bars'] for e in episodes) if episodes else None
    m['n_drawdowns'] = len(episodes)

    rf_bar = o['rf'] / bpy if bpy else None
    sd = _std(r)
    m['ann_vol_pct'] = _san(sd * math.sqrt(bpy) * 100.0) if sd and bpy else None
    dvol = _downside_dev(r, rf_bar or 0.0)
    m['downside_vol_pct'] = _san(dvol * math.sqrt(bpy) * 100.0) if dvol and bpy else None
    mkt = r[hold]
    if mkt.shape[0] >= 5:
        m['var95_bar_pct'] = float(np.percentile(mkt, 5)) * 100.0
        m['skew'], m['kurtosis'] = _moments(mkt)

    # ── 綜合風險回報（基準 = 同一時段 Buy & Hold）──
    if k and sd and bpy and rf_bar is not None:
        m['sharpe'] = _san((float(np.mean(r)) - rf_bar) / sd * math.sqrt(bpy))
        m['sortino'] = _san((float(np.mean(r)) - rf_bar) / dvol * math.sqrt(bpy)) if dvol else None
        m['calmar'] = _san(m['ann_ret_pct'] / abs(m['max_dd_pct'])) \
            if m['ann_ret_pct'] is not None and m['max_dd_pct'] < 0 else None
        m['recovery_factor'] = _san(final_pnl / abs(m['max_dd_amount'])) \
            if m['max_dd_amount'] < 0 else None
    sdb = _std(rb)
    if sdb and bpy:
        cov = float(np.cov(r, rb, ddof=1)[0, 1])
        m['beta'] = _san(cov / (sdb ** 2))
        if m['beta'] is not None and rf_bar is not None:
            m['alpha_pct'] = _san((float(np.mean(r)) - rf_bar
                                   - m['beta'] * (float(np.mean(rb)) - rf_bar)) * bpy * 100.0)
        m['buy_hold_sharpe'] = _san((float(np.mean(rb)) - rf_bar) / sdb * math.sqrt(bpy)) \
            if rf_bar is not None else None
        d = r - rb
        sd_d = _std(d)
        if sd_d and bpy:
            m['info_ratio'] = _san(float(np.mean(d)) / sd_d * math.sqrt(bpy))

    # ── 交易執行（持倉處理如實現形）──
    m['n_trades'] = len(closed)
    m['n_open'] = len(trades) - len(closed)
    m['n_short_trades'] = sum(1 for t in trades if t['dir'] == pm.DIR_SHORT)
    m['n_buy_signals'] = sig['n_buy']
    m['n_sell_signals'] = sig['n_sell']
    m['ignored_in_pos'] = sig['ignored_in_pos']
    m['ignored_flat'] = sig['ignored_flat']
    m['ignored_badprice'] = sig['ignored_badprice']
    m['avg_hold_bars'] = _san(float(np.mean([t['bars'] for t in trades]))) if trades else None
    m['max_hold_bars'] = int(max([t['bars'] for t in trades])) if trades else None
    dvals = [t['days'] for t in trades if t['days'] is not None]
    m['avg_hold_days'] = _san(float(np.mean(dvals))) if dvals else None
    m['coverage_pct'] = float(hold[:-1].sum()) / (n - 1) * 100.0
    m['short_exposure_pct'] = float((sgn[:-1] < 0).sum()) / (n - 1) * 100.0
    m['turnover_per_year'] = _san(len(trades) / years) if years else None
    tot_cost_pct = float(sum(t['cost_pct'] for t in trades))
    m['total_cost_pct'] = tot_cost_pct
    m['total_cost_amount'] = cap * tot_cost_pct / 100.0
    m['total_slip_pct'] = float(sum(t['slip_pct'] or 0.0 for t in trades))
    gross_raw = float(sum(t['gross_pct'] for t in trades))
    m['cost_to_gross_pct'] = _san(tot_cost_pct / gross_raw * 100.0) if gross_raw > 0 else None
    m['n_bars'] = n
    m['span_days'] = _days_between(next((t for t in ts if t is not None), None),
                                   next((t for t in reversed(ts) if t is not None), None))
    m['bars_per_year'] = _san(bpy)
    m['warmup_bars'] = warmup_bars(list(buy) + list(sell))
    return m


def _downside_dev(r, rf_bar):
    a = np.asarray(r, dtype=float) - rf_bar
    neg = np.minimum(a, 0.0)
    if a.shape[0] < 2:
        return None
    v = float(np.mean(neg ** 2))
    return math.sqrt(v) if v > 0 else None


def _dd_episodes(equity, ts):
    """回撤事件（peak → 低點 → 修復／未修復）→ 持續根數 + 修復日數。"""
    eps = []
    peak = equity[0]
    peak_i = 0
    in_dd = False
    for t in range(equity.shape[0]):
        if equity[t] >= peak:
            if in_dd:
                eps.append(_ep(peak_i, t, True, ts))
                in_dd = False
            peak = equity[t]
            peak_i = t
        else:
            in_dd = True
    if in_dd:
        eps.append(_ep(peak_i, equity.shape[0] - 1, False, ts))
    return eps


def _ep(peak_i, end_i, recovered, ts):
    return {'peak_idx': int(peak_i), 'end_idx': int(end_i), 'recovered': bool(recovered),
            'bars': int(end_i - peak_i), 'days': _days_between(ts[peak_i], ts[end_i])}

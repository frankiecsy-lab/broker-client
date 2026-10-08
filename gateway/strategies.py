"""策略管理 — 領域單一事實來源（state_store section 'strategies'）+ 分數制條件契約。

- **分數制**（用戶要求）：每條買／賣條件帶分數 score（1–100）；每根 bar 將「成立中」條件
  嘅分數累加（`score_series`），**≥ TRIGGER_SCORE(100) 即觸發**；觸發 = 由未夠分變夠分嗰根
  （`trigger_indices` = rising edge）— live 同 backtest 同一語義。
- **條件 = 純 JSON + 可計算契約**（同 gateway/indicators.py 嘅 compute 契約完全一致）：
  rule = {type, side ∈ above/below, params, score}；`rule_signal(rule, ohlc)` → bool ndarray
  （state：呢根 bar 成唔成立）。日後 BACKTEST 直接食同一份 JSON + 同一批計算函數 —
  BOLL／VOB 條件直接 reuse indicators.compute_boll / compute_vob，MA 用同 compute_boll mid
  一樣嘅 rolling mean。
- entry = {id, name, buy:[rule...], sell:[rule...], entry_price:'market', qty:'min_lot',
  mark_buffer(0–200，預設 10：對上一個訊號相隔 ≤N 條 bar 嘅後續 B/S 轉純文字)}。
  🤖 價位／數量存 **mode enum 唔存解析後實數** — 市價同每手股數（futu lot_size）要執行／
  backtest 時先 resolve，存死數會令 backtest 唔啱。
  #32：刪 `code`／`validity`／`created` — 策略 = 純規則集，套用返晒全部格（#30），標的／生效期
  冇作用；舊檔多嘅欄 _sanitize 直接忽略 = 零遷移。
- 儲存照 favorites：state_store 一個 JSON 檔加 section，容忍 missing/corrupt（壞 rule 剔除、
  參數 clamp — 唔炸 UI）。
"""
import uuid
from dataclasses import dataclass

SECTION = 'strategies'
TRIGGER_SCORE = 100
SIDES = ('above', 'below')
SCORE_LO, SCORE_HI = 1, 100
MARK_BUFFER_DEFAULT = 10          # #29/#31/#32：對上一個訊號 ≤N 條 bar 嘅後續訊號轉純文字紅/綠（組內首個照徽章）；0 = 全部徽章
MARK_BUFFER_LO, MARK_BUFFER_HI = 0, 200


def clamp_mark_buffer(v):
    try:
        v = int(float(v))
    except (TypeError, ValueError):
        return MARK_BUFFER_DEFAULT
    return max(MARK_BUFFER_LO, min(MARK_BUFFER_HI, v))


@dataclass(frozen=True)
class CondParamSpec:
    key: str
    label_key: str          # i18n key（參數名三語；類型名本身語言中立 acronym）
    default: object
    lo: float = 0
    hi: float = 100
    is_int: bool = True
    choices: tuple = ()     # 有 choices → 值只能係其中一個（頁用 combo）


@dataclass(frozen=True)
class ConditionDef:
    key: str
    label: str              # acronym（語言中立，combo 顯示）
    params: tuple           # CondParamSpec 序列
    desc_key: str           # 一行描寫（combo tooltip）
    signal: object          # (ohlc dict, params dict, side) → bool ndarray（全長度 state）


# ─────── 條件計算（契約同 indicators.compute：ohlc dict of ndarray → 全長度陣列）───────

def _sma(c, period):
    """rolling mean（同 compute_boll 嘅 mid 計法一致）。前 period−1 個 NaN。"""
    import numpy as np
    import pandas as pd
    out = np.full(c.shape[0], np.nan)
    if c.shape[0] >= period:
        out = pd.Series(c).rolling(int(period)).mean().to_numpy()
    return out


def _sig_ma_cross(ohlc, params, side):
    """MA 交叉：state = SMA(fast) 高於（above）／低於（below）SMA(slow)。NaN 比較 = False。"""
    import numpy as np
    c = ohlc['c']
    f = _sma(c, int(params['fast']))
    s = _sma(c, int(params['slow']))
    with np.errstate(invalid='ignore'):
        return (f > s) if side == 'above' else (f < s)


def _sig_boll_cross(ohlc, params, side):
    """BOLL 穿線：state = 收盤價高於（above）／低於（below）所選線（upper/mid/lower）。"""
    import numpy as np
    from gateway.indicators import compute_boll
    line = compute_boll(ohlc, {'period': int(params['period']),
                               'dev': float(params['dev'])})[params['line']]
    with np.errstate(invalid='ignore'):
        return (ohlc['c'] > line) if side == 'above' else (ohlc['c'] < line)


def _sig_vob_break(ohlc, params, side):
    """VOB 穿線：state = 收盤價升穿／跌穿當前有效 VOB 區塊邊界。
    邊界 = fmax(bull_top, bear_top) / fmin(bull_bottom, bear_bottom)（NaN 容忍 → 無區塊 = False）。"""
    import numpy as np
    from gateway.indicators import compute_vob
    z = compute_vob(ohlc, params)
    top = np.fmax(z['bull_top'], z['bear_top'])
    bot = np.fmin(z['bull_bottom'], z['bear_bottom'])
    with np.errstate(invalid='ignore'):
        return (ohlc['c'] > top) if side == 'above' else (ohlc['c'] < bot)


# vob_break 參數 = INDICATOR_DEFS['vob'] 同一組（label 直接 reuse ind_p_* i18n key）。
_VOB_PARAMS = (
    CondParamSpec('period', 'ind_p_period', 14, 1, 200),
    CondParamSpec('strength', 'ind_p_strength', 1.0, 0.0, 5.0, is_int=False),
    CondParamSpec('confirm', 'ind_p_confirm', 3, 1, 20),
    CondParamSpec('sweep', 'ind_p_sweep', 5, 1, 50),
    CondParamSpec('max_size', 'ind_p_max_size', 3.0, 0.0, 10.0, is_int=False),
    CondParamSpec('pen', 'ind_p_pen', 50, 0, 100),
    CondParamSpec('supersede', 'ind_p_supersede', 1, 0, 1),
    CondParamSpec('max_zones', 'ind_p_max_zones', 15, 1, 100),
)

CONDITION_DEFS = {
    'ma_cross': ConditionDef(
        'ma_cross', 'MA',
        (CondParamSpec('fast', 'str_p_fast', 20, 2, 200),
         CondParamSpec('slow', 'str_p_slow', 120, 3, 400)),
        'str_desc_ma_cross', _sig_ma_cross),
    'boll_cross': ConditionDef(
        'boll_cross', 'BOLL',
        (CondParamSpec('period', 'ind_p_period', 20, 2, 200),
         CondParamSpec('dev', 'ind_p_dev', 2.0, 0.5, 5.0, is_int=False),
         CondParamSpec('line', 'str_p_line', 'upper', choices=('upper', 'mid', 'lower'))),
        'str_desc_boll_cross', _sig_boll_cross),
    'vob_break': ConditionDef(
        'vob_break', 'VOB', _VOB_PARAMS,
        'str_desc_vob_break', _sig_vob_break),
}


# ─────── 清洗（容忍 load：未知 type 剔除、參數 clamp、分數 clamp 1–100）───────

def _clamp_param(spec, value):
    if spec.choices:
        return value if value in spec.choices else spec.default
    try:
        v = float(value)
    except (TypeError, ValueError):
        return spec.default
    v = max(float(spec.lo), min(float(spec.hi), v))
    return int(round(v)) if spec.is_int else v


def sanitize_rule(raw):
    """raw dict → 乾淨 rule {type, side, params, score}；未知 type / 唔係 dict → None。"""
    if not isinstance(raw, dict):
        return None
    d = CONDITION_DEFS.get(raw.get('type'))
    if d is None:
        return None
    side = raw.get('side') if raw.get('side') in SIDES else 'above'
    rawp = raw.get('params') if isinstance(raw.get('params'), dict) else {}
    params = {p.key: _clamp_param(p, rawp.get(p.key, p.default)) for p in d.params}
    try:
        score = int(raw.get('score', 0))
    except (TypeError, ValueError):
        score = 0
    return {'type': d.key, 'side': side, 'params': params,
            'score': max(SCORE_LO, min(SCORE_HI, score))}


def _sanitize(raw):
    if not isinstance(raw, dict) or not raw.get('id'):
        return None
    rules = lambda k: [r for r in map(sanitize_rule, raw.get(k) or []) if r] \
        if isinstance(raw.get(k), list) else []
    return {'id': str(raw['id']), 'name': str(raw.get('name') or ''),
            'buy': rules('buy'), 'sell': rules('sell'),
            'entry_price': 'market', 'qty': 'min_lot',   # mode enum（v1 固定；backtest 先 resolve）
            'mark_buffer': clamp_mark_buffer(raw.get('mark_buffer')),   # #29：缺欄 → 預設 10（舊檔零遷移）
            }   # #32：舊檔 code/validity/created 等多餘欄 = 直接忽略


# ─────── 分數契約（backtest 入口）───────

def ohlc_from_kline(df):
    """kline DataFrame（KLINE_COLUMNS）→ compute 契約 ohlc dict（ndarray）。"""
    return {k: df[v].to_numpy(dtype=float) for k, v in
            (('o', 'open'), ('h', 'high'), ('l', 'low'), ('c', 'close'), ('v', 'volume'))}


def rule_signal(rule, ohlc):
    """rule → bool ndarray（state）。未知 type → 全 False（如實，唔炸）。"""
    import numpy as np
    d = CONDITION_DEFS.get(rule.get('type'))
    if d is None:
        return np.zeros(ohlc['c'].shape[0], dtype=bool)
    return d.signal(ohlc, rule.get('params') or {}, rule.get('side', 'above'))


def score_series(rules, ohlc):
    """每根 bar = 「成立中」條件嘅分數總和（int ndarray）。"""
    import numpy as np
    out = np.zeros(ohlc['c'].shape[0], dtype=int)
    for r in rules:
        out += rule_signal(r, ohlc).astype(int) * int(r.get('score', 0))
    return out


def trigger_indices(scores, threshold=TRIGGER_SCORE):
    """分數由 <threshold 變 ≥threshold 嘅 bar idx（rising edge）→ ndarray。"""
    import numpy as np
    hit = scores >= threshold
    prev = np.concatenate(([False], hit[:-1])) if hit.shape[0] else hit
    return np.nonzero(hit & ~prev)[0]


def trade_marks(entry, ohlc):
    """策略 entry → 買賣觸發點 [(bar idx, 價格, 'B'/'S')]（升冪）。
    同 live/backtest 同一契約：score_series 累加 → trigger_indices rising edge；
    價格 = 觸發根收盤（entry_price='market' 嘅語義）。行情頁 B/S 標記即用呢個。"""
    out = []
    for side, rules in (('B', entry.get('buy') or []), ('S', entry.get('sell') or [])):
        for i in trigger_indices(score_series(rules, ohlc)):
            out.append((int(i), float(ohlc['c'][i]), side))
    out.sort()
    return out


# ─────── 摘要（符號化、語言中立 — 表欄 / 日後 backtest report 同一份）───────

_LINE_SHORT = {'upper': 'U', 'mid': 'M', 'lower': 'L'}


def rule_summary(rule):
    """一條 rule → 一行符號摘要，例如 'MA20>MA120 (+60)'。"""
    t_, p, op = rule.get('type'), rule.get('params') or {}, \
        '>' if rule.get('side') == 'above' else '<'
    if t_ == 'ma_cross':
        body = f"MA{p.get('fast')}{op}MA{p.get('slow')}"
    elif t_ == 'boll_cross':
        body = f"C{op}BOLL.{_LINE_SHORT.get(p.get('line'), '?')}"
    elif t_ == 'vob_break':
        body = f"C{op}VOB"
    else:
        body = str(t_)
    return f'{body} (+{rule.get("score", 0)})'


def rules_summary(rules):
    return ' ＋ '.join(rule_summary(r) for r in rules)


# ─────── Manager（state_store section 'strategies'）───────

class StrategyManager:
    """策略 CRUD + 驗證；name 大細階唔敏感 dedupe；每次改動即時 save（atomic）。"""

    def __init__(self):
        self._items = self._load()
        self._listeners = []         # #30：照 IndicatorManager 模式，改策略 → 頁面即時跟

    def _load(self):
        from gateway import state_store
        raw = state_store.load_section(SECTION, {}).get('items')
        if not isinstance(raw, list):
            return []
        return [e for e in map(_sanitize, raw) if e]

    def _save(self):
        from gateway import state_store
        state_store.save_section(SECTION, {'items': self._items})

    # --- #30：變更 listener（行情頁即時跟 BUFFER / 規則改動，包括策略頁做彈出窗時）---
    def add_listener(self, fn):
        """註冊變更 callback（簽名 `fn(origin, kind)`，kind ∈ add/update/remove；同指標 manager 同模式）。"""
        if fn not in self._listeners:
            self._listeners.append(fn)

    def remove_listener(self, fn):
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _notify(self, kind):
        for fn in list(self._listeners):   # copy — listener 入面 add/remove 唔會炸 iteration
            fn('strategies', kind)

    def items(self):
        return [dict(e) for e in self._items]

    def get(self, sid):
        return next((dict(e) for e in self._items if e['id'] == sid), None)

    def _validate(self, name, buy, sell):
        if not str(name or '').strip():
            return False, 'str_bad_name'
        if not buy or not sell:
            return False, 'str_no_rules'
        return True, None

    def add(self, name, buy, sell, mark_buffer=None):
        """→ (ok, msg_key, entry)。buy/sell = raw rule 列表（經 sanitize）；#29 mark_buffer None = 預設。
        #32：冇 code／validity — 策略 = 純規則集，套用全部格。"""
        name = str(name or '').strip()
        buy = [r for r in map(sanitize_rule, buy or []) if r]
        sell = [r for r in map(sanitize_rule, sell or []) if r]
        ok, msg = self._validate(name, buy, sell)
        if not ok:
            return False, msg, None
        if any(str(e['name']).upper() == name.upper() for e in self._items):
            return False, 'str_dup_name', None
        entry = {'id': 's-' + uuid.uuid4().hex[:12], 'name': name,
                 'buy': buy, 'sell': sell,
                 'entry_price': 'market', 'qty': 'min_lot',
                 'mark_buffer': clamp_mark_buffer(mark_buffer)}
        self._items.append(entry)
        self._save()
        self._notify('add')
        return True, 'str_added', dict(entry)

    def update(self, sid, name=None, buy=None, sell=None, mark_buffer=None):
        """改一個策略（None = 唔改該欄；mark_buffer 同 — #29）。→ (ok, msg_key)。"""
        e = next((x for x in self._items if x['id'] == sid), None)
        if e is None:
            return False, 'str_gone'
        name = str(name).strip() if name is not None else e['name']
        nb = [r for r in map(sanitize_rule, buy) if r] if buy is not None else e['buy']
        ns = [r for r in map(sanitize_rule, sell) if sell] if sell is not None else e['sell']
        ok, msg = self._validate(name, nb, ns)
        if not ok:
            return False, msg
        if any(x['id'] != sid and str(x['name']).upper() == name.upper() for x in self._items):
            return False, 'str_dup_name'
        e.update({'name': name, 'buy': nb, 'sell': ns,
                  'mark_buffer': clamp_mark_buffer(mark_buffer) if mark_buffer is not None
                  else clamp_mark_buffer(e.get('mark_buffer'))})
        self._save()
        self._notify('update')
        return True, 'str_updated'

    def remove(self, ids):
        drop = set(ids)
        self._items = [e for e in self._items if e['id'] not in drop]
        self._save()
        self._notify('remove')
        return self.items()


_MGR = None


def get_manager():
    global _MGR
    if _MGR is None:
        _MGR = StrategyManager()
    return _MGR


def reset_manager_for_test():
    global _MGR
    _MGR = None

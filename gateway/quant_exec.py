# -*- coding: utf-8 -*-
"""執行層：策略訊號 → 落單意圖（ticket #34c）。純計算、冇 Qt、冇 broker — 所以可單獨測。

## 點解要有呢層
即時落單同回測必須**同一個持倉口徑**，否則「回測表現」同「實際落單」會講兩套數。所以：
* 訊號來源 = `strategies.trade_marks`（同回測、同行情頁標記完全同一個函數）
* 持倉方向 = `gateway/position_model.step`（同回測共用單一事實來源）
呢層只多負責三件**即時先有**嘅嘢：去重、風控、半自動待執行。

## 三道即時閘（回測冇，即時必須有）
1. **baseline watermark**：訂閱時已存在嘅歷史訊號**一律唔落單**（否則一開頁就追落幾十張單）。
   只計數並如實回報「已略過 N 個歷史訊號」。每次重新訂閱都要 re-baseline（bar index 會重數）。
2. **未收根過濾**：只行動喺 `bar_idx <= n-2`。最後一根仲喺度形成，收盤價未定 → 唔可以當成交價。
3. **去重**：key = `(binding_id, bar_idx, side)`。每次 poll 都會重算 marks（marks 係因果、穩定），
   所以**只有被 consume 咗嘅訊號**先唔會再出現；未行動嘅留返下一輪，唔會漏亦唔會重覆落單。

## 全自動 vs 半自動（用戶：「兩者都要，頁上有開關」）
* 全自動：每次 poll 最多行動**一個**訊號（其餘留返下一輪 — 避免同一批互相依賴嘅行動落晒落去），
  行風控。`on_bars` 會推進持倉狀態；**落單失敗必須調 `requeue()` 還原**，否則狀態會同實際成交脫節。
* 半自動：訊號連同「會做乜」（open/close/flip）一齊入 `PendingQueue`，**唔推進狀態、唔行風控**
  （人就係風控）。人確認並成功落單後調 `commit()` 先推進狀態；`commit()` 會偵測狀態已變（stale）。

## 風控（用戶：「最小三項」）
最大同時持倉數 / 每日最大筆數 / 同一標的訊號冷卻根數。
* `0` = 最嚴（唔准），唔係「無上限」— 頁要照呢個意思講。
* 冷卻以 `(code, binding_id)` 計，即「該綁定嘅 K 線根數」；同一標的唔同週期各自計（bar index 唔同把尺，
  混埋會錯，所以寧可如實分開）。
* 反手 = 兩筆（先平後開），所以計「每日筆數」時算 2 筆；反手**唔增加**持倉數，所以唔受最大持倉數擋。
"""
from collections import OrderedDict

from gateway import position_model as pm
from gateway.strategies import trade_marks

# ── 風控參數（全部可調；0 = 最嚴）──
MAX_POSITIONS_DEFAULT, MAX_TRADES_DAY_DEFAULT, COOLDOWN_DEFAULT = 3, 10, 1
POS_LO, POS_HI = 0, 999
TRADES_LO, TRADES_HI = 0, 9999
COOL_LO, COOL_HI = 0, 9999
# 每筆數量：0 = 未設定（頁會用 qt_err_no_qty 擋喺前面；執行層一律以 ERR_QTY 如實擋，唔亂落單）
QTY_LO, QTY_HI, QTY_DEFAULT = 0, 100000, 1

# 風控/資料問題嘅原因 key（i18n 三語由頁翻譯；呢層只出 key，唔扮似懂人話）
RK_POSITIONS, RK_TRADES, RK_COOLDOWN = 'qt_rk_positions', 'qt_rk_trades', 'qt_rk_cooldown'
ERR_QTY = 'qt_err_qty'

FIRED_CAP = 4000      # 已處理訊號 key 上限（超過就丟最舊 — watermark 已保證唔會重落舊單）
PENDING_CAP = 200     # 待執行佇列上限（丟最舊並計數，唔靜默吃咗）

SECTION = 'quant'     # state_store section 名（同 backtest/strategies/favorites 一樣：名屬領域層，唔放喺頁）


def _clamp_int(v, lo, hi, default):
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def clamp_max_positions(v):
    return _clamp_int(v, POS_LO, POS_HI, MAX_POSITIONS_DEFAULT)


def clamp_max_trades_day(v):
    return _clamp_int(v, TRADES_LO, TRADES_HI, MAX_TRADES_DAY_DEFAULT)


def clamp_cooldown_bars(v):
    return _clamp_int(v, COOL_LO, COOL_HI, COOLDOWN_DEFAULT)


def clamp_qty(v):
    return _clamp_int(v, QTY_LO, QTY_HI, QTY_DEFAULT)


def norm_risk(risk):
    """頁傳入嘅風控值 → 乾淨整數（乜都唔會炸）。"""
    r = risk or {}
    return {'max_positions': clamp_max_positions(r.get('max_positions')),
            'max_trades_day': clamp_max_trades_day(r.get('max_trades_day')),
            'cooldown_bars': clamp_cooldown_bars(r.get('cooldown_bars'))}


# ─────── 訊號閘（watermark / 未收根 / 去重）───────

def new_gate(binding_id):
    """一個綁定（標的 × 週期 × 策略）一條閘。"""
    return {'binding_id': str(binding_id), 'baseline': None,
            'seen': set(), 'order': [], 'skipped_history': 0}


def arm_baseline(gate, n_bars):
    """訂閱/重新訂閱時調：而家見到嘅最後一根之前嘅一切訊號都算「歷史」→ 唔落單。
       ⚠️ 每次 re-subscribe 都要再 arm：新 df 嘅 bar index 由 0 重數，舊 seen 會錯手擋新訊號。"""
    gate['baseline'] = int(n_bars) - 1
    gate['seen'].clear()
    gate['order'] = []
    gate['skipped_history'] = 0


def _consume(gate, sig):
    key = sig_key(sig)
    if key in gate['seen']:
        return False
    gate['seen'].add(key)
    gate['order'].append(key)
    while len(gate['seen']) > FIRED_CAP:      # 只丟最舊 — 邊個需要 remember 邊個唔需要，watermark 已管住
        gate['seen'].discard(gate['order'].pop(0))
    return True


def sig_key(sig):
    """去重 key — 一個綁定同一根同一方向只可以行動一次。"""
    return (sig.get('binding_id'), sig.get('bar_idx'), sig.get('side'))


def collect_signals(gate, entry, ohlc, times=None):
    """行情 → 呢一刻**可以行動**嘅新訊號（未 consume）。唔改持倉狀態。

    返回升冪 `[{binding_id, bar_idx, side, price, time}]`。
    price = 訊號根收盤（同回測成交價語義一致）；time 只俾頁顯示/log。
    """
    raw = (ohlc or {}).get('c')
    c = [] if raw is None else list(raw)   # ⚠️ 唔可以用 `or []`：ndarray 嘅 truth value 會直接炸
    n = len(c)
    if n < 2:
        return []
    out = []
    for (i, price, side) in trade_marks(entry, ohlc):
        if i >= n or i > n - 2:      # 未收根一律唔行動
            continue
        if gate['baseline'] is not None and i <= gate['baseline']:
            gate['skipped_history'] += 1     # 如實計數，唔靜默
            continue
        key = (gate['binding_id'], i, side)
        if key in gate['seen']:
            continue
        t = ''
        if times is not None and i < len(times):
            t = str(times[i])
        out.append({'binding_id': gate['binding_id'], 'bar_idx': int(i), 'side': side,
                    'price': float(price), 'time': t})
    return out


# ─────── 風控 ───────

def n_orders_of(prev, nxt):
    """一個行動會變成幾張單：平/開 = 1，反手 = 2（先平後開 — 同回測計成本口徑一致）。"""
    c = (prev or {}).get('dir')
    w = (nxt or {}).get('dir')
    return (1 if c is not None else 0) + (1 if w is not None else 0)


class RiskGuard:
    """三項風控。狀態只有「邊個幾時開過倉」，所以頁改參數即時生效、唔使重建。"""

    def __init__(self, risk=None):
        self.risk = norm_risk(risk)
        self._last_bar = {}     # (code, binding_id) -> 上次行動嘅 bar_idx

    def update(self, risk):
        self.risk = norm_risk(risk)

    def check(self, *, code, binding_id, bar_idx, prev, nxt, open_dirs, today_count):
        """→ (可唔可以行動, 原因 key 或 None)。"""
        r = self.risk
        cool = r['cooldown_bars']
        if cool > 0:
            last = self._last_bar.get((str(code), str(binding_id)))
            if last is not None and int(bar_idx) - last < cool:
                return False, RK_COOLDOWN
        if (nxt or {}).get('dir') is not None and (prev or {}).get('dir') is None:
            # 只有「由空倉開倉」先增加持倉數；反手唔算
            if len(open_dirs or []) + 1 > r['max_positions']:
                return False, RK_POSITIONS
        if today_count + n_orders_of(prev, nxt) > r['max_trades_day']:
            return False, RK_TRADES
        return True, None

    def note(self, *, code, binding_id, bar_idx):
        """行動成功後先 record（失敗唔該計冷卻 — 冇成交就冇冷卻）。"""
        self._last_bar[(str(code), str(binding_id))] = int(bar_idx)

    def reset(self):
        self._last_bar = {}


# ─────── 綁定（一個 標的×週期×策略 嘅即時執行個體）───────

def new_binding(*, binding_id, code, ktype, mode, entry=None):
    return {'binding_id': str(binding_id), 'code': str(code), 'ktype': str(ktype),
            'mode': pm.clamp_mode(mode), 'entry': entry,
            'state': pm.new_state(), 'gate': new_gate(binding_id)}


def open_dirs(bindings):
    """而家持緊嘅方向清單（風控 max_positions 食呢個）。"""
    return [b['state'].get('dir') for b in bindings if (b.get('state') or {}).get('dir')]


def order_side(direction, closing):
    """倉位方向 + 平/開 → 券商方向 BUY/SELL（沽空：開空 = SELL、平空 = BUY）。"""
    return 'BUY' if (direction == pm.DIR_LONG) != bool(closing) else 'SELL'


def orders_for(*, code, prev, nxt, price, qty):
    """一個行動 → 一張或兩張落單意圖（反手 = 先平後開，順序如實）。"""
    out = []
    cur = (prev or {}).get('dir')
    want = (nxt or {}).get('dir')
    if cur is not None:
        out.append({'code': str(code), 'side': order_side(cur, True), 'qty': int(qty),
                    'price': float(price), 'dir': cur, 'closing': True})
    if want is not None:
        out.append({'code': str(code), 'side': order_side(want, False), 'qty': int(qty),
                    'price': float(price), 'dir': want, 'closing': False})
    return out


def on_bars(b, entry, ohlc, times=None, guard=None, *, auto=True, qty=0,
            open_dirs_=None, today_count=0):
    """一次行情更新 → 呢輪應該做乜。

    全自動：最多行動一個訊號（其餘留返下一輪），行風控，**會**推進 `b['state']`。
    半自動：全部可行動訊號入 `pending`（附「會做乜」），**唔**推進狀態、**唔**行風控。

    返回 `{'signals','orders','pending','blocked','ignored','deferred','skipped_history'}`；
    每個 action 都帶 `prev_state`/`next_state` 俾 `commit()`/`requeue()` 用。
    """
    sigs = collect_signals(b['gate'], entry or b.get('entry'), ohlc, times)
    res = {'signals': sigs, 'orders': [], 'pending': [], 'blocked': [], 'ignored': [],
           'deferred': 0, 'skipped_history': b['gate']['skipped_history']}
    mode = pm.clamp_mode(b['mode'])
    state = b['state']
    acted = False
    for sig in sigs:
        if acted:
            res['deferred'] += 1        # 留返未 consume → 下一輪會再見到（唔會漏）
            continue
        action, nxt, reason = pm.step(state, sig['side'], mode)
        if action == pm.ACT_IGNORE:
            _consume(b['gate'], sig)
            res['ignored'].append({'sig': sig, 'action': action, 'reason_key': reason})
            state = nxt                 # 忽略唔改方向，照跟
            continue
        if not sig['price'] > 0:        # 價 ≤ 0（停牌/缺數）→ 開唔到倉，state 唔 commit
            _consume(b['gate'], sig)
            res['ignored'].append({'sig': sig, 'action': action, 'reason_key': pm.IG_BADPRICE})
            continue
        act = {'binding_id': b['binding_id'], 'sig': sig, 'action': action,
               'prev_state': state, 'next_state': nxt}
        if not auto:
            _consume(b['gate'], sig)
            act['orders'] = orders_for(code=b['code'], prev=state, nxt=nxt,
                                       price=sig['price'], qty=qty)
            res['pending'].append(act)
            continue
        ok, rk = (guard.check(code=b['code'], binding_id=b['binding_id'], bar_idx=sig['bar_idx'],
                              prev=state, nxt=nxt, open_dirs=open_dirs_ or [],
                              today_count=today_count) if guard else (True, None))
        if not ok:
            _consume(b['gate'], sig)    # 已被處理（如實記錄一次），唔好每 poll 重覆報告
            res['blocked'].append(dict(act, reason_key=rk))
            continue
        if not int(qty) > 0:            # 數量唔合法 → 如實擋，唔落單
            _consume(b['gate'], sig)
            res['blocked'].append(dict(act, reason_key=ERR_QTY))
            continue
        _consume(b['gate'], sig)
        act['orders'] = orders_for(code=b['code'], prev=state, nxt=nxt,
                                   price=sig['price'], qty=qty)
        res['orders'].append(act)
        b['state'] = state = nxt        # 全自動：即行即推進；落單失敗要 requeue() 還原
        acted = True
    return res


def commit(b, act, guard=None):
    """落單成功 → 確認呢個行動（並先計冷卻 — 冇成交就冇冷卻）。
       全自動：`on_bars` 已投機推進 → 呢度只確認；半自動：呢度先推進。
       狀態既唔係 prev 亦唔係 next = 中途俾人改過 → 回 False，如實唔亂改。"""
    st = b['state']
    if st == act['next_state']:
        pass
    elif st == act['prev_state']:
        b['state'] = act['next_state']
    else:
        return False
    if guard is not None:
        guard.note(code=b['code'], binding_id=b['binding_id'], bar_idx=act['sig']['bar_idx'])
    return True


def requeue(b, act):
    """落單失敗 → 撤回投機推進（半自動本來未推進 → 唔使改，照回 True）。
       狀態同兩者都唔夾 = 已俾人改過 → 如實回 False。"""
    st = b['state']
    if st == act['prev_state']:
        return True
    if st == act['next_state']:
        b['state'] = act['prev_state']
        return True
    return False


# ─────── 半自動待執行佇列 ───────

class PendingQueue:
    """人確認清單。過量會丟最舊並計數（`dropped`）— 唔靜默吃咗。"""

    def __init__(self, cap=PENDING_CAP):
        self.cap = int(cap)
        self._items = OrderedDict()
        self.dropped = 0

    def add(self, act):
        key = sig_key(act['sig'])
        if key in self._items:
            return False
        self._items[key] = act
        while len(self._items) > self.cap:
            self._items.popitem(last=False)
            self.dropped += 1
        return True

    def items(self):
        return list(self._items.values())

    def get(self, key):
        return self._items.get(tuple(key))

    def take(self, key):
        """俾人落單（成功 commit / 失敗 requeue 後再 add 返）。"""
        return self._items.pop(tuple(key), None)

    def reject(self, key, reason=''):
        act = self._items.pop(tuple(key), None)
        return dict(act, reject_reason=reason) if act else None

    def clear(self):
        n = len(self._items)
        self._items.clear()
        return n

    def __len__(self):
        return len(self._items)

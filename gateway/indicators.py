"""指標管理 — 指標領域單一事實來源：INDICATOR_DEFS（BOLL/ATR/MACD + ICT 區塊 OB/FVG/VOB）+ 純計算 + IndicatorManager + IndicatorKlineChart（ticket #19 / #20）。

設計契約：
- **零改動 gui_kline.py**：指標疊加經 `IndicatorKlineChart(gk.KlineChart)` 子类實現。子类依賴 parent 內部
  （`_redraw` 讀實例屬性 `self.ax/self.axv` → 重新綁定即自動畫到新 axes；`self._s` 係絕對→本地座標基準；
  手勢 guard 硬編碼 `(self.ax, self.axv)` → 三個薄 override 改寫 `ev.inaxes`）。改 gui_kline.KlineChart 內部要同步檢查呢度。
- 計算係純函數：ndarray in → **全長度** ndarray out（warm-up 段 NaN）；繪製先 slice 可見窗 → 無左緣 warm-up 失真。
- 計算 cache 住喺 chart（per-chart）— rows 屬每張圖（行情頁 6 格各自有 rows）；Manager 只管配置，永遠唔收 rows。
- 配置持久化：state_store section 'indicators'（照 favorites.py 模式）；變更經 add_listener(origin, kind) 通知，
  origin 參數俾 listener 過濾自己（防 re-entrancy）。
- 顏色全部 draw-time 讀 gk.C_*（theme 切換自動跟）；只用 8 色 palette + C_UP/C_DOWN 語義色，唔引入新 hex。
- 指標名（BOLL/ATR/MACD）係 acronym，語言中立，唔入 i18n；參數名先經 i18n key。
"""
import math
from dataclasses import dataclass

import numpy as np

from gateway import state_store
from gateway.pages import gui_kline as gk

# ───────────────────────────── 純計算（無 Qt、無狀態） ─────────────────────────────

HIST_SCALE = 2.0   # 📏 MACD 柱 = 2×(DIF−DEA)（華語/富途 app 慣例；TradingView 用 1×，改呢個常量就得）


def compute_boll(ohlc, params):
    """保力加：mid = SMA(period)，upper/lower = mid ± dev×STD（總體 std，TA 慣例）。前 period−1 個 NaN。"""
    c = ohlc['c']
    period, dev = int(params['period']), float(params['dev'])
    n = c.shape[0]
    mid = np.full(n, np.nan)
    up = np.full(n, np.nan)
    dn = np.full(n, np.nan)
    if n >= period:
        import pandas as pd
        s = pd.Series(c)
        m = s.rolling(period).mean().to_numpy()
        sd = s.rolling(period).std(ddof=0).to_numpy()
        mid, up, dn = m, m + dev * sd, m - dev * sd
    return {'mid': mid, 'upper': up, 'lower': dn}


def compute_atr(ohlc, params):
    """ATR：TR → Wilder RMA（seed = 前 period 根 TR 嘅 SMA，之後 rma[i]=rma[i−1]+(tr[i]−rma[i−1])/period）。
    🤖 標準 Wilder 定義，唔係 ewm(alpha=1/period) 無 seed 變體（兩者前段數值唔同）。前 period−1 個 NaN。"""
    h, l, c = ohlc['h'], ohlc['l'], ohlc['c']
    period = int(params['period'])
    n = c.shape[0]
    tr = np.empty(n)
    tr[0] = h[0] - l[0]
    if n > 1:
        pc = c[:-1]
        tr[1:] = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - pc), np.abs(l[1:] - pc)))
    atr = np.full(n, np.nan)
    if n >= period:
        rma = np.empty(n)
        rma[period - 1] = tr[:period].mean()
        for i in range(period, n):
            rma[i] = rma[i - 1] + (tr[i] - rma[i - 1]) / period
        atr[period - 1:] = rma[period - 1:]
    return {'atr': atr}


def _ema(x, span):
    """EMA，adjust=False 語義（首值 seed，逐點遞推）— 唔經 pandas，避開 API 面。"""
    alpha = 2.0 / (span + 1.0)
    out = np.empty(x.shape[0])
    out[0] = x[0]
    for i in range(1, x.shape[0]):
        out[i] = out[i - 1] + alpha * (x[i] - out[i - 1])
    return out


def compute_macd(ohlc, params):
    """MACD：DIF = EMA(c,fast)−EMA(c,slow)；DEA = EMA(DIF,signal)；柱 = HIST_SCALE×(DIF−DEA)。
    EMA 由第一根 seed，全長度有值（標準行為）；前期數值未穩定期，warmup 僅做顯示參考。"""
    c = ohlc['c']
    fast, slow, signal = int(params['fast']), int(params['slow']), int(params['signal'])
    dif = _ema(c, fast) - _ema(c, slow)
    dea = _ema(dif, signal)
    return {'dif': dif, 'dea': dea, 'hist': HIST_SCALE * (dif - dea)}


# ─────────────── ICT 區塊指標（OB / FVG / VOB）───────────────
# 🤖 區塊一律用 top/bottom **價格**陣列（區塊外 NaN）— 唔准用 0/1 方向旗標：
#    `_draw_indicators` 嘅主圖 Y-fit 會 concat 晒所有陣列嘅 finite 值，旗標會把 Y 範圍炸晒。

def _zones_to_arrays(n, zones):
    """zones = [(start, end, lo, hi, d)]（含 end，d=+1 睇多 / −1 睇空）→ 4 條全長度陣列。
    重疊區塊：後面（較新）嘅覆蓋前面 — 視覺上新區塊優先可見。"""
    bt = np.full(n, np.nan)
    bb = np.full(n, np.nan)
    st = np.full(n, np.nan)
    sb = np.full(n, np.nan)
    for start, end, lo, hi, d in zones:
        t, b = (bt, bb) if d > 0 else (st, sb)
        t[start:end + 1] = hi
        b[start:end + 1] = lo
    return {'bull_top': bt, 'bull_bottom': bb, 'bear_top': st, 'bear_bottom': sb}


def compute_fvg(ohlc, params):
    """FVG（三根缺口）：睇多 = low[i+2] > high[i] → 區塊 [high[i], low[i+2]]；睇空 = high[i+2] < low[i] → [high[i+2], low[i]]。
    區塊由確認根（i+2）畫到第一次被填平（睇多：low ≤ 區塊底；睇空：high ≥ 區塊頂），之後 NaN。
    參數：period = 計 ATR 嘅週期（只俾 min_size 做尺）；min_size = 缺口最少幾多倍 ATR（0 = 全收）；max_zones = 只畫最近 N 個。"""
    h, l = ohlc['h'], ohlc['l']
    period = int(params['period'])
    min_size = float(params['min_size'])
    max_zones = int(params['max_zones'])
    n = h.shape[0]
    atr = compute_atr(ohlc, {'period': period})['atr']

    cand = []
    for i in range(n - 2):
        # min_size = 0 → 唔使 ATR；> 0 而 ATR 仲喺 warm-up（NaN）→ 比較必然 False，即係自然過濾走
        ref = atr[i] if min_size > 0 else 0.0
        up = l[i + 2] - h[i]
        if up > 0 and up >= min_size * ref:
            cand.append((i + 2, h[i], l[i + 2], 1))
        dn = l[i] - h[i + 2]
        if dn > 0 and dn >= min_size * ref:
            cand.append((i, h[i + 2], l[i], -1))

    zones = []
    for start, lo, hi, d in cand[-max_zones:]:
        end = n - 1
        for j in range(start, n):
            if (d > 0 and l[j] <= lo) or (d < 0 and h[j] >= hi):
                end = j
                break
        zones.append((start, end, lo, hi, d))
    return _zones_to_arrays(n, zones)


def _has_fvg(h, l, i, j1, d):
    """位移窗 [i, j1] 內有同向三根缺口（不平衡）？"""
    for t in range(i, j1 - 1):
        if (d > 0 and l[t + 2] > h[t]) or (d < 0 and h[t + 2] < l[t]):
            return True
    return False


def _ob_valid(h, l, i, j1, d, sweep):
    """VOB（有效 OB）額外兩條：① OB 燭影線掃走之前 sweep 根嘅流動性（低/高被取走先至反轉）② 位移窗內有同向 FVG。"""
    if i < sweep:
        return False
    if d > 0 and l[i] >= l[i - sweep:i].min():
        return False
    if d < 0 and h[i] <= h[i - sweep:i].max():
        return False
    return _has_fvg(h, l, i, j1, d)


def _ob_candidates(o, h, l, c, atr, strength, confirm, sweep, valid_only):
    """OB 候選：反向燭 i（睇多 OB = 陰燭）→ 之後 confirm 根內收盤突破 high[i]/low[i]（結構突破）+ 位移幅度 ≥ strength×ATR。
    valid_only（VOB）再過 `_ob_valid`。→ list[(i, lo, hi, d, k)]，k = 第一根確認燭。"""
    n = c.shape[0]
    out = []
    for i in range(n - 1):
        ref = atr[i]
        j1 = min(n - 1, i + confirm)
        if np.isnan(ref) or j1 <= i:
            continue
        if c[i] < o[i]:
            d, brk = 1, np.where(c[i + 1:j1 + 1] > h[i])[0]
            if brk.size == 0 or h[i + 1:j1 + 1].max() - l[i] < strength * ref:
                continue
            k = i + 1 + int(brk[0])
            if valid_only and not _ob_valid(h, l, i, j1, 1, sweep):
                continue
        elif c[i] > o[i]:
            d, brk = -1, np.where(c[i + 1:j1 + 1] < l[i])[0]
            if brk.size == 0 or h[i] - l[i + 1:j1 + 1].min() < strength * ref:
                continue
            k = i + 1 + int(brk[0])
            if valid_only and not _ob_valid(h, l, i, j1, -1, sweep):
                continue
        else:
            continue
        out.append((i, l[i], h[i], d, k))
    return out


def _ob_zones(n, h, l, c, cand, max_zones):
    """OB 區塊 = OB 燭全範圍 [low, high]，由 i 畫到收盤穿過對面邊（睇多：close < low → 失效）。"""
    zones = []
    for i, lo, hi, d, k in cand[-max_zones:]:
        end = n - 1
        for j in range(k, n):
            if (d > 0 and c[j] < lo) or (d < 0 and c[j] > hi):
                end = j
                break
        zones.append((i, end, lo, hi, d))
    return zones


def _ob_common(ohlc, params, valid_only):
    o, h, l, c = ohlc['o'], ohlc['h'], ohlc['l'], ohlc['c']
    period = int(params['period'])
    strength = float(params['strength'])
    confirm = int(params['confirm'])
    max_zones = int(params['max_zones'])
    sweep = int(params.get('sweep', 0))
    n = c.shape[0]
    atr = compute_atr(ohlc, {'period': period})['atr']
    cand = _ob_candidates(o, h, l, c, atr, strength, confirm, sweep, valid_only)
    return _zones_to_arrays(n, _ob_zones(n, h, l, c, cand, max_zones))


def compute_ob(ohlc, params):
    """OB（Order Block）：結構突破前最後一根反向燭 = 區塊。見 `_ob_candidates`。"""
    return _ob_common(ohlc, params, valid_only=False)


def compute_vob(ohlc, params):
    """VOB（有效 OB）= OB + 有效性過濾（掃流動性 + 位移段有 FVG）。見 `_ob_valid`。"""
    return _ob_common(ohlc, params, valid_only=True)


# ───────────────────────────── Registry ─────────────────────────────

@dataclass(frozen=True)
class ParamSpec:
    key: str
    label_key: str          # i18n key（參數名要三語；指標名唔使）
    default: float
    lo: float
    hi: float
    is_int: bool = True


@dataclass(frozen=True)
class IndicatorDef:
    key: str
    label: str              # acronym（語言中立，直接做圖表/掣顯示名）
    positions: tuple        # 准入位置：'main'（疊價格軸）/ 'sub'（獨立 panel）
    params: tuple
    compute: object         # (ohlc dict, params dict) → dict[str, ndarray 全長度]
    warmup: int             # 有意義數值所需最少 bar 數（顯示參考）


INDICATOR_DEFS = {
    'boll': IndicatorDef(
        'boll', 'BOLL', ('main',),
        (ParamSpec('period', 'ind_p_period', 20, 2, 200),
         ParamSpec('dev', 'ind_p_dev', 2.0, 0.5, 5.0, is_int=False)),
        compute_boll, 20),
    'atr': IndicatorDef(
        'atr', 'ATR', ('sub',),
        (ParamSpec('period', 'ind_p_period', 14, 1, 200),),
        compute_atr, 14),
    'macd': IndicatorDef(
        'macd', 'MACD', ('sub',),
        (ParamSpec('fast', 'ind_p_fast', 12, 2, 200),
         ParamSpec('slow', 'ind_p_slow', 26, 3, 400),
         ParamSpec('signal', 'ind_p_signal', 9, 2, 200)),
        compute_macd, 35),
    'ob': IndicatorDef(
        'ob', 'OB', ('main',),
        (ParamSpec('period', 'ind_p_period', 14, 1, 200),
         ParamSpec('strength', 'ind_p_strength', 1.0, 0.0, 5.0, is_int=False),
         ParamSpec('confirm', 'ind_p_confirm', 3, 1, 20),
         ParamSpec('max_zones', 'ind_p_max_zones', 15, 1, 100)),
        compute_ob, 15),
    'fvg': IndicatorDef(
        'fvg', 'FVG', ('main',),
        (ParamSpec('period', 'ind_p_period', 14, 1, 200),
         ParamSpec('min_size', 'ind_p_min_size', 0.0, 0.0, 5.0, is_int=False),
         ParamSpec('max_zones', 'ind_p_max_zones', 15, 1, 100)),
        compute_fvg, 15),
    'vob': IndicatorDef(
        'vob', 'VOB', ('main',),
        (ParamSpec('period', 'ind_p_period', 14, 1, 200),
         ParamSpec('strength', 'ind_p_strength', 1.0, 0.0, 5.0, is_int=False),
         ParamSpec('confirm', 'ind_p_confirm', 3, 1, 20),
         ParamSpec('sweep', 'ind_p_sweep', 5, 1, 50),
         ParamSpec('max_zones', 'ind_p_max_zones', 15, 1, 100)),
        compute_vob, 20),
}


def _clamp_param(spec, value):
    """參數值清洗：唔係數 → default；clamp 到 [lo,hi]；is_int → int。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = float(spec.default)
    if math.isnan(v) or math.isinf(v):
        v = float(spec.default)
    v = max(spec.lo, min(spec.hi, v))
    return int(round(v)) if spec.is_int else v


def _params_summary(def_, params):
    """圖表 panel 標題 / 表格摘要：參數按 schema 順序用 '/' 連接（語言中立）。"""
    out = []
    for p in def_.params:
        v = params.get(p.key, p.default)
        out.append(str(int(v)) if p.is_int else f"{float(v):g}")
    return '/'.join(out)


# ───────────────────────────── IndicatorManager（配置 CRUD + 持久化 + listener） ─────────────────────────────

SECTION = 'indicators'
MAX_ITEMS = 6        # 執行個體上限（超過 → 'ind_limit' 如實回報）
MAX_SUB_ITEMS = 4    # 副圖 panel 上限（保圖表可讀）

# 首次運行 seed：主圖 BOLL + 副圖 ATR（用戶拍板嘅首批示範）
_SEED = [('boll', 'main'), ('atr', 'sub')]


class IndicatorManager:
    """指標執行個體配置 — 唯一事實來源。永遠唔收 rows、唔做計算。

    item = {'id','def','position','params','enabled'}（順序 = 繪製/panel 順序，穩定）。
    版本：config_version（參數/增刪 → 數值失效）/ layout_version（增刪/開關/位置 → panel 失效）— 俾 chart 分級失效。
    """

    def __init__(self):
        self._items = []
        self._next_id = 1
        self._listeners = []
        self.config_version = 0
        self.layout_version = 0
        self._load()

    # --- 載入 / 儲存 ----------------------------------------------------------
    def _load(self):
        st = state_store.load_section(SECTION, {})
        raw_items = st.get('items')
        if isinstance(raw_items, list):
            for raw in raw_items:
                item = self._sanitize(raw)
                if item is not None:
                    self._items.append(item)
            try:
                self._next_id = int(st.get('next_id', 1))
            except (TypeError, ValueError):
                self._next_id = 1
            # 防 id 撞：next_id 必須大過現有最大 id
            mx = max((int(e['id'].rsplit('-', 1)[-1]) for e in self._items
                      if str(e['id']).startswith('ind-')), default=0)
            self._next_id = max(self._next_id, mx + 1)
        else:
            # 首次（section 未寫過）→ seed 首批示範指標
            for def_key, pos in _SEED:
                self._items.append(self._new_item(def_key, pos, {}))
            self._save()

    def _sanitize(self, raw):
        """容忍 load：unknown def → drop；位置唔准入 → 退回首個准入值；參數缺/越界 → default/clamp。"""
        if not isinstance(raw, dict):
            return None
        d = INDICATOR_DEFS.get(str(raw.get('def', '')))
        if d is None:
            return None
        pos = raw.get('position')
        if pos not in d.positions:
            pos = d.positions[0]
        params = {p.key: _clamp_param(p, (raw.get('params') or {}).get(p.key)) for p in d.params}
        iid = str(raw.get('id') or '')
        if not iid:
            iid = f'ind-{self._next_id}'
            self._next_id += 1
        return {'id': iid, 'def': d.key, 'position': pos, 'params': params,
                'enabled': bool(raw.get('enabled', True))}

    def _new_item(self, def_key, position, params):
        """建立執行個體；id 由 self._next_id 派發並即時遞增（seed/add 都經呢度，唔會撞 id）。"""
        d = INDICATOR_DEFS[def_key]
        if position not in d.positions:
            position = d.positions[0]
        item = {'id': f'ind-{self._next_id}', 'def': def_key, 'position': position,
                'params': {p.key: _clamp_param(p, (params or {}).get(p.key)) for p in d.params},
                'enabled': True}
        self._next_id += 1
        return item

    def _save(self):
        state_store.save_section(SECTION, {'items': self._items, 'next_id': self._next_id})

    # --- 讀 -------------------------------------------------------------------
    def items(self):
        """→ list[dict] copy（保序）。"""
        return [{**e, 'params': dict(e['params'])} for e in self._items]

    def get(self, inst_id):
        for e in self._items:
            if e['id'] == inst_id:
                return e
        return None

    # --- 寫（全部 save + bump version + notify） --------------------------------
    def add(self, def_key, position, params, origin='manager'):
        if def_key not in INDICATOR_DEFS:
            return False, 'ind_bad_def', None
        if len(self._items) >= MAX_ITEMS or \
                (position == 'sub' and sum(1 for e in self._items if e['position'] == 'sub') >= MAX_SUB_ITEMS):
            return False, 'ind_limit', None
        item = self._new_item(def_key, position, params)
        self._items.append(item)
        self._save()
        self.config_version += 1
        self.layout_version += 1
        self._notify(origin, 'add')
        return True, 'ind_added', {**item, 'params': dict(item['params'])}

    def update(self, inst_id, position=None, params=None, origin='manager'):
        e = self.get(inst_id)
        if e is None:
            return False, 'ind_no_sel'
        d = INDICATOR_DEFS[e['def']]
        if position is not None and position in d.positions:
            e['position'] = position
        if params:
            for p in d.params:
                if p.key in params:
                    e['params'][p.key] = _clamp_param(p, params[p.key])
        self._save()
        self.config_version += 1
        self.layout_version += 1
        self._notify(origin, 'update')
        return True, 'ind_saved'

    def set_enabled(self, inst_id, enabled, origin='manager'):
        e = self.get(inst_id)
        if e is None:
            return False
        e['enabled'] = bool(enabled)
        self._save()
        self.layout_version += 1     # 🤖 唔 bump config_version：開關唔改變數值，cache 照用
        self._notify(origin, 'enable')
        return True

    def remove(self, inst_id, origin='manager'):
        e = self.get(inst_id)
        if e is None:
            return False, 'ind_no_sel'
        self._items = [x for x in self._items if x['id'] != inst_id]
        self._save()
        self.layout_version += 1
        self._notify(origin, 'remove')
        return True, 'ind_removed'

    # --- listener（照 theme.add_listener pattern；origin 俾 listener 過濾自己） ----
    def add_listener(self, fn):
        """註冊變更 callback（簽名 `fn(origin, kind)`，kind ∈ add/update/enable/remove）。"""
        if fn not in self._listeners:
            self._listeners.append(fn)

    def remove_listener(self, fn):
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _notify(self, origin, kind):
        for fn in list(self._listeners):   # copy — listener 入面 add/remove 唔會炸 iteration
            fn(origin, kind)


_MANAGER = None


def get_manager():
    """進程單例（照 symbol_search.get_directory 慣例）。"""
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = IndicatorManager()
    return _MANAGER


def reset_manager_for_test():
    """e2e：換 tmp state 檔後取新實例。"""
    global _MANAGER
    _MANAGER = None


# ───────────────────────────── 繪畫（draw-time 讀 gk.C_*，跟 theme） ─────────────────────────────

def _plot_boll(ax, x, sl):
    ax.fill_between(x, sl['upper'], sl['lower'], color=gk.C_ACCENT, alpha=0.06)
    ax.plot(x, sl['upper'], color=gk.C_MUTED, linewidth=0.8)
    ax.plot(x, sl['lower'], color=gk.C_MUTED, linewidth=0.8)
    ax.plot(x, sl['mid'], color=gk.C_ACCENT, linewidth=0.9)


def _plot_atr(ax, x, sl):
    ax.plot(x, sl['atr'], color=gk.C_ACCENT, linewidth=1.0)


def _plot_macd(ax, x, sl):
    h = sl['hist']
    m = ~np.isnan(h)
    ax.bar(x[m], h[m], width=0.7, color=np.where(h[m] >= 0, gk.C_UP, gk.C_DOWN))
    ax.axhline(0, color=gk.C_BORDER, linewidth=0.6)
    ax.plot(x, sl['dif'], color=gk.C_ACCENT, linewidth=0.9)
    ax.plot(x, sl['dea'], color=gk.C_MUTED, linewidth=0.9)


def _plot_zones(ax, x, sl):
    """ICT 區塊（OB/FVG/VOB）：睇多 = C_UP / 睇空 = C_DOWN 半透明填充 + 同色邊。
    🤖 NaN 位置先換 0 再交 `where` mask — fill_between 對 NaN 嘅處理唔一致，mask 先係可靠嘅斷段方式。"""
    for tk, bk, col in (('bull_top', 'bull_bottom', gk.C_UP),
                        ('bear_top', 'bear_bottom', gk.C_DOWN)):
        top, bot = sl[tk], sl[bk]
        m = ~(np.isnan(top) | np.isnan(bot))
        if not m.any():
            continue
        ax.fill_between(x, np.where(m, bot, 0.0), np.where(m, top, 0.0), where=m,
                        color=col, alpha=0.14, edgecolor=col, linewidth=0.6)


_PLOTTERS = {'boll': _plot_boll, 'atr': _plot_atr, 'macd': _plot_macd,
             'ob': _plot_zones, 'fvg': _plot_zones, 'vob': _plot_zones}


# ───────────────────────────── IndicatorKlineChart（K 線 + 指標疊加） ─────────────────────────────

class IndicatorKlineChart(gk.KlineChart):
    """KlineChart 子类 — 動態副圖 panel + 主圖指標疊加 + per-chart 計算 cache。

    ⚠️ coupling 契約：依賴 parent 嘅 `_redraw`（讀實例屬性 self.ax/self.axv → 重新綁定即自動畫到新 axes）、
    `self._s`（絕對→本地座標基準）、手勢 guard `(self.ax, self.axv)`。改 gui_kline.KlineChart 內部要同步檢查呢度。
    冇 `set_indicator_manager()`（例如行情頁 6 格 / standalone gui_kline）→ 行為同 parent 逐字相同。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ind_mgr = None
        self._ind_dirty = False
        self._axes_sig = ()          # 已砌 panel 嘅 (inst_id, ...) — 比對先重建
        self.ax_ind = []             # 副圖指標 axes（順序 = _axes_sig）
        self._ind_axis = {}          # inst_id → axes
        self._data_seq = 0           # set_bars 計數（cache key 之一）
        self._ind_cache = None       # {'data_seq','cfg_ver','full':{inst_id:{series:ndarray 全長度}}}

    # --- 注入 / 失效 -----------------------------------------------------------
    def set_indicator_manager(self, mgr):
        self._ind_mgr = mgr
        self._ind_dirty = True

    def mark_indicators_dirty(self):
        self._ind_dirty = True

    def set_bars(self, rows):
        self._data_seq += 1          # 🤖 必須喺 super 之前 — parent set_bars 尾會 call _redraw
        super().set_bars(rows)

    # --- axes 生命週期（全拆全砌；n_sub=0 時 gridspec 參數同 parent 逐字相同） ----
    def _visible_panels(self):
        if self._ind_mgr is None:
            return []
        return [(e['id'], e['def']) for e in self._ind_mgr.items()
                if e['enabled'] and e['position'] == 'sub' and e['def'] in INDICATOR_DEFS]

    def _build_axes(self):
        panels = self._visible_panels()
        fig = self.canvas.figure
        fig.clear()                  # Figure 物件身份保留（canvas / kline_page 都揸住佢）
        n_sub = len(panels)
        ratios = [3.0, 1.0] + [1.0] * n_sub     # 主圖 → volume → 指標 panel
        gs = fig.add_gridspec(len(ratios), 1, height_ratios=ratios,
                              hspace=0.06 if n_sub == 0 else 0.10,
                              left=0.055, right=0.985, top=0.97, bottom=0.09)
        self.ax = fig.add_subplot(gs[0])
        self.axv = fig.add_subplot(gs[1], sharex=self.ax)
        self.ax_ind = [fig.add_subplot(gs[i], sharex=self.ax) for i in range(2, len(ratios))]
        self._ind_axis = {p[0]: a for p, a in zip(panels, self.ax_ind)}
        self._axes_sig = tuple(p[0] for p in panels)
        self._ind_dirty = False
        # 🤖 唔准 reset _view/_drag — 用戶平移/縮放位置喺開關指標前後必須保持

    # --- 繪製 -----------------------------------------------------------------
    def _redraw(self):
        if self._ind_mgr is not None:
            sig = tuple(p[0] for p in self._visible_panels())
            if self._ind_dirty or sig != self._axes_sig:
                self._build_axes()
        super()._redraw()            # 蠟燭/成交量自動畫到（可能已重建嘅）self.ax/self.axv
        if self._ind_mgr is None:
            return
        self._draw_indicators()
        self.canvas.draw_idle()      # 同 parent 嘅 draw_idle coalesce，一次 repaint

    def _ensure_ind_cache(self):
        c = self._ind_cache
        if c is not None and c['data_seq'] == self._data_seq and \
                c['cfg_ver'] == self._ind_mgr.config_version:
            return
        self._recompute_indicators()

    def _recompute_indicators(self):
        """全表計算一次（所有執行個體，包括 disabled — 開關先至唔會 cache miss）。"""
        rows = self._rows
        full = {}
        if rows:
            o = np.array([r[1] for r in rows], dtype=float)
            h = np.array([r[2] for r in rows], dtype=float)
            l = np.array([r[3] for r in rows], dtype=float)
            c = np.array([r[4] for r in rows], dtype=float)
            ohlc = {'o': o, 'h': h, 'l': l, 'c': c}
            for e in self._ind_mgr.items():
                d = INDICATOR_DEFS.get(e['def'])
                if d is not None:
                    full[e['id']] = d.compute(ohlc, e['params'])
        self._ind_cache = {'data_seq': self._data_seq,
                           'cfg_ver': self._ind_mgr.config_version, 'full': full}

    def _draw_indicators(self):
        # 指標 panel 要自己 clear（parent._redraw 只 clear self.ax/self.axv）
        for a in self.ax_ind:
            a.clear()
            self._style_ax(a)
        n = len(self._rows)
        if n == 0:
            return
        # 可見 slice — 同 parent 同一套座標（本地 = 絕對 − self._s；parent 已經 set 好 _s + xlim）
        k = n - self._s
        xmin_l, xmax_l = self.ax.get_xlim()
        i0 = max(0, int(math.floor(xmin_l)))
        i1 = min(k, int(math.ceil(xmax_l)) + 1)
        if i1 <= i0:
            return
        x = np.arange(i0, i1, dtype=float)
        self._ensure_ind_cache()
        full = self._ind_cache['full']

        def _slice(inst_id):
            s_full = full.get(inst_id)
            if not s_full:
                return None
            return {name: arr[self._s + i0:self._s + i1] for name, arr in s_full.items()}

        # ── 主圖指標（疊價格軸）：Y fit 要合併 overlay 可見值（parent 淨係 fit h/l）──
        main_vals = []
        for e in self._ind_mgr.items():
            if not e['enabled'] or e['position'] != 'main':
                continue
            d = INDICATOR_DEFS.get(e['def'])
            sl = _slice(e['id'])
            if d is None or sl is None:
                continue
            _PLOTTERS[d.key](self.ax, x, sl)
            for arr in sl.values():
                fin = arr[~np.isnan(arr)]
                if fin.size:
                    main_vals.append(fin)
        if main_vals:
            lo, hi = self.ax.get_ylim()
            lo = min(lo, min(float(v.min()) for v in main_vals))
            hi = max(hi, max(float(v.max()) for v in main_vals))
            self.ax.set_ylim(lo, hi)

        # ── 副圖指標（獨立 panel，各自 y auto-fit 可見 slice）──
        for e in self._ind_mgr.items():
            if e['position'] != 'sub' or not e['enabled']:
                continue
            a = self._ind_axis.get(e['id'])
            d = INDICATOR_DEFS.get(e['def'])
            if a is None or d is None:
                continue
            title = f"{d.label} {_params_summary(d, e['params'])}"
            sl = _slice(e['id'])
            vals = np.concatenate(list(sl.values())) if sl else np.array([np.nan])
            fin = vals[~np.isnan(vals)]
            if fin.size == 0:
                a.set_ylim(-1.0, 1.0)
                a.set_title(title + '  —', fontsize=8, color=gk.C_MUTED, loc='left', pad=2)
                continue
            _PLOTTERS[d.key](a, x, sl)
            lo, hi = float(fin.min()), float(fin.max())
            if hi <= lo:
                lo, hi = lo - 1.0, hi + 1.0
            pad = (hi - lo) * 0.06
            a.set_ylim(lo - pad, hi + pad)
            a.set_title(title, fontsize=8, color=gk.C_MUTED, loc='left', pad=2)

        # ── X 軸時間標籤遷移到最底軸（parent 硬編碼設咗喺 axv）──
        if self.ax_ind:
            self.axv.tick_params(labelbottom=False)
            bottom = self.ax_ind[-1]
            tick_i = sorted(set(np.linspace(self._s + i0, self._s + i1 - 1,
                                           min(7, i1 - i0)).astype(int).tolist()))
            bottom.set_xticks([i - self._s for i in tick_i])
            bottom.set_xticklabels([gk._short_time(self._rows[i][0]) for i in tick_i], fontsize=8)

        # ── crosshair 補畫到指標 panel（parent 淨係畫 self.ax）──
        if self._hover_idx is not None and self._s <= self._hover_idx < n:
            lx = self._hover_idx - self._s
            if i0 <= lx < i1:
                for a in self.ax_ind:
                    a.axvline(lx, color=gk.C_MUTED, linewidth=0.8, linestyle='--', alpha=0.8)

    # --- 手勢（parent guard 硬編碼 (self.ax, self.axv) → 指標 panel 入面改寫 inaxes）──
    def _on_motion(self, ev):
        if ev.inaxes in self.ax_ind:
            ev.inaxes = self.ax
        super()._on_motion(ev)

    def _on_press(self, ev):
        if ev.inaxes in self.ax_ind:
            ev.inaxes = self.ax
        super()._on_press(ev)

    def _on_scroll(self, ev):
        if ev.inaxes in self.ax_ind:
            ev.inaxes = self.ax
        super()._on_scroll(ev)

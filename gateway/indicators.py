"""指標管理 — 指標領域單一事實來源：INDICATOR_DEFS（BOLL/ATR/MACD + ICT 全套 OB/FVG/VOB/BRK/BPR/BOS/CHoCH/LIQ/EQHL/PD/OTE，共 14 個）
+ 純計算 + IndicatorManager + IndicatorKlineChart（ticket #19 / #20 / #21）。

設計契約：
- **零改動 gui_kline.py**：指標疊加經 `IndicatorKlineChart(gk.KlineChart)` 子类實現。子类依賴 parent 內部
  （`_redraw` 讀實例屬性 `self.ax/self.axv` → 重新綁定即自動畫到新 axes；`self._s` 係絕對→本地座標基準；
  手勢 guard 硬編碼 `(self.ax, self.axv)` → 三個薄 override 改寫 `ev.inaxes`）。改 gui_kline.KlineChart 內部要同步檢查呢度。
- 計算係純函數：ndarray in → **全長度** ndarray out（warm-up 段 NaN）；繪製先 slice 可見窗 → 無左緣 warm-up 失真。
- ICT 輸出只有三種形態，全部用**價格值**（🤖 嚴禁 0/1 旗標 — 主圖 Y-fit 會 concat 晒所有陣列）：
  區塊 = `bull_top/bull_bottom/bear_top/bear_bottom`（區外 NaN，`_zones_to_arrays`）；
  水平位 = `*_top == *_bottom` == 價位 + `mark_bull/mark_bear`（標記嗰根嘅價位，`_levels_to_arrays`）；
  帶狀（PD）= `range_hi/equilibrium/range_lo`（`_plot_band`）。
- 計算 cache 住喺 chart（per-chart）— rows 屬每張圖（行情頁 6 格各自有 rows）；Manager 只管配置，永遠唔收 rows。
- 配置持久化：state_store section 'indicators'（照 favorites.py 模式）；變更經 add_listener(origin, kind) 通知，
  origin 參數俾 listener 過濾自己（防 re-entrancy）。
- 顏色全部 draw-time 讀 gk.C_*（theme 切換自動跟）；只用 8 色 palette + C_UP/C_DOWN 語義色，唔引入新 hex。
- 指標名（BOLL/ATR/MACD）係 acronym，語言中立，唔入 i18n；參數名先經 i18n key。
- 每個 def 自帶說明（ticket #21）：`desc_key`（一行描寫 → 管理頁列表欄 / 掣 tooltip）、`usage_key`（點樣用 → 詳情面板 / tooltip）、
  `ParamSpec.note_key`（每個參數一行 → 詳情面板可摺疊）。🤖 說明只喺 indicators.py 宣告 i18n key，文案一律入 i18n.py 三語。
"""
import math
from dataclasses import dataclass
from functools import partial

import numpy as np
from matplotlib.collections import PathCollection
from matplotlib.path import Path

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

def _supersede(zones):
    """同向區塊：較新者出現（start）即終止之前所有同向區塊（end = start − 1），終止得太短嘅剔除。
    🤖 用戶要求：「同一類 OB 出現了，之前嘅 OB 是不是應該消失」→ 預設係（參數 `supersede`）。
    順帶一個副作用好有用：同向區塊必然唔重疊 → `_zones_to_arrays` 嘅「較新者覆蓋」永遠唔會切短
    任何區塊，即係唔需要將陣列做多 slot 並存。"""
    zs = [list(z) for z in sorted(zones, key=lambda z: z[0])]
    for idx in range(1, len(zs)):
        st, d = zs[idx][0], zs[idx][4]
        for j in range(idx):
            if zs[j][4] == d and zs[j][1] >= st - 1:
                zs[j][1] = st - 1
    return [tuple(z) for z in zs if z[1] >= z[0]]


def _zones_to_arrays(n, zones):
    """zones = [(start, end, lo, hi, d)]（含 end，d=+1 睇多 / −1 睇空）→ 4 條全長度陣列。
    重疊區塊：後面（較新）嘅覆蓋前面 — 視覺上新區塊優先可見。
    🤖 OB 家族同 FVG 預設經 `_supersede` → 同向必然唔重疊，呢度唔會切短任何嘢；
       只有 `supersede=0`（或 BPR 呢類本身可以重疊嘅區塊）先會行到覆蓋呢條路。"""
    bt = np.full(n, np.nan)
    bb = np.full(n, np.nan)
    st = np.full(n, np.nan)
    sb = np.full(n, np.nan)
    for start, end, lo, hi, d in zones:
        t, b = (bt, bb) if d > 0 else (st, sb)
        t[start:end + 1] = hi
        b[start:end + 1] = lo
    return {'bull_top': bt, 'bull_bottom': bb, 'bear_top': st, 'bear_bottom': sb}


def _fvg_candidates(h, l, atr, min_size):
    """三根缺口候選 → list[(start, lo, hi, d)]（start = 確認根 i+2）。min_size = 0 → 唔使 ATR；
    > 0 而 ATR 仲喺 warm-up（NaN）→ 比較必然 False，即係自然過濾走。"""
    out = []
    for i in range(h.shape[0] - 2):
        ref = atr[i] if min_size > 0 else 0.0
        up = l[i + 2] - h[i]
        if up > 0 and up >= min_size * ref:
            out.append((i + 2, h[i], l[i + 2], 1))
        dn = l[i] - h[i + 2]
        if dn > 0 and dn >= min_size * ref:
            out.append((i, h[i + 2], l[i], -1))
    return out


def compute_fvg(ohlc, params):
    """FVG（三根缺口）：睇多 = low[i+2] > high[i] → 區塊 [high[i], low[i+2]]；睇空 = high[i+2] < low[i] → [high[i+2], low[i]]。
    區塊由缺口首根畫到價格**返身入缺口**（近邊被觸及：睇多 low < 區塊頂 / 睇空 high > 區塊底），之後 NaN。
    🤖 同向較新嘅缺口出現即終止舊區塊（`_supersede`，同 OB 家族一樣）：FVG 係可以重疊嘅區塊，
       但重疊落陣列會被「較新者覆蓋」逐 bar 切走 → 一個區塊砌成幾截「斷續」（用戶反映）。
    參數：period = 計 ATR 嘅週期（只俾 min_size 做尺）；min_size = 缺口最少幾多倍 ATR（0 = 全收）；max_zones = 只畫最近 N 個。"""
    h, l = ohlc['h'], ohlc['l']
    period = int(params['period'])
    min_size = float(params['min_size'])
    max_zones = int(params['max_zones'])
    n = h.shape[0]
    atr = compute_atr(ohlc, {'period': period})['atr']

    zones = []
    for start, lo, hi, d in _fvg_candidates(h, l, atr, min_size)[-max_zones:]:
        # 🤖 填平 = 價格返身入缺口（近邊），唔使完全穿過；掃描一律由確認根（i+2）先開始 —
        #    三根形態本身（尤其中間根）必然插喺缺口內，唔算填平。
        end = n - 1
        for j in range(start + (2 if d < 0 else 0), n):
            if (d > 0 and l[j] < hi) or (d < 0 and h[j] > lo):
                end = j
                break
        zones.append((start, end, lo, hi, d))
    return _zones_to_arrays(n, _supersede(zones))


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


def _ob_candidates(o, h, l, c, atr, strength, confirm, sweep, valid_only, max_size=0.0):
    """OB 候選：反向燭 i（睇多 OB = 陰燭）→ 之後 confirm 根內收盤突破 high[i]/low[i]（結構突破）+ 位移幅度 ≥ strength×ATR。
    valid_only（VOB）再過 `_ob_valid`。max_size > 0 → OB 燭本身高度 > max_size×ATR 即唔算（嗰啲係位移燭，唔係訂單塊）。
    → list[(i, lo, hi, d, k)]，k = 第一根確認燭。"""
    n = c.shape[0]
    out = []
    for i in range(n - 1):
        ref = atr[i]
        j1 = min(n - 1, i + confirm)
        if np.isnan(ref) or j1 <= i:
            continue
        if max_size > 0 and h[i] - l[i] > max_size * ref:
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


def _ob_zones(n, h, l, c, cand, max_zones, pen=1.0, supersede=True):
    """OB 區塊 = OB 燭全範圍 [low, high]，由 i 畫到「被消耗」為止。
    pen = 失效深度（0–1，以區塊高度計）：睇多 → 收盤 < high − pen×高度即死。
    pen=1.0 即舊行為（收盤完全穿過對面邊先死）；0.5 = 收盤穿過中點即死；0 = 一入區塊即死。
    supersede = 同向出現更新嘅 OB 即終止舊區塊（見 `_supersede`）。
    🤖 用收盤唔用影線：影線碰到區塊好常發生，會搞到區塊一出現就死。"""
    zones = []
    for i, lo, hi, d, k in cand[-max_zones:]:
        depth = pen * (hi - lo)
        end = n - 1
        for j in range(k, n):
            if (d > 0 and c[j] < hi - depth) or (d < 0 and c[j] > lo + depth):
                end = j
                break
        zones.append((i, end, lo, hi, d))
    return _supersede(zones) if supersede else zones


def _ob_common(ohlc, params, valid_only):
    o, h, l, c = ohlc['o'], ohlc['h'], ohlc['l'], ohlc['c']
    period = int(params['period'])
    strength = float(params['strength'])
    confirm = int(params['confirm'])
    max_zones = int(params['max_zones'])
    max_size = float(params.get('max_size', 0.0))
    pen = float(params.get('pen', 100)) / 100.0
    supersede = bool(int(params.get('supersede', 1)))
    sweep = int(params.get('sweep', 0))
    n = c.shape[0]
    atr = compute_atr(ohlc, {'period': period})['atr']
    cand = _ob_candidates(o, h, l, c, atr, strength, confirm, sweep, valid_only, max_size)
    return _zones_to_arrays(n, _ob_zones(n, h, l, c, cand, max_zones, pen, supersede))


def compute_ob(ohlc, params):
    """OB（Order Block）：結構突破前最後一根反向燭 = 區塊。見 `_ob_candidates`。"""
    return _ob_common(ohlc, params, valid_only=False)


def compute_vob(ohlc, params):
    """VOB（有效 OB）= OB + 有效性過濾（掃流動性 + 位移段有 FVG）。見 `_ob_valid`。"""
    return _ob_common(ohlc, params, valid_only=True)


# ─────── ICT 結構／流動性工具（BOS / CHoCH / LIQ / EQHL / PD / OTE / BRK / BPR）───────

def _swings(h, l, k):
    """fractal 拐點：h[i] 大過左右各 k 根嘅最高（嚴格大過左邊、大過等於右邊）= swing high；低同理。
    → (highs, lows) 兩個 index list。🤖 拐點要 k 根之後先確認到 → 最後 k 根永遠唔會係拐點
    （結構類指標因此天然滞后 k 根 —— ICT 本身都係事後確認，唔係預測）。"""
    n = h.shape[0]
    hs, ls = [], []
    for i in range(k, n - k):
        if h[i] > h[i - k:i].max() and h[i] >= h[i + 1:i + k + 1].max():
            hs.append(i)
        if l[i] < l[i - k:i].min() and l[i] <= l[i + 1:i + k + 1].min():
            ls.append(i)
    return hs, ls


def _structure_breaks(h, l, c, k):
    """跟住最近「已確認」嘅 swing high/low：收盤穿過 = 結構突破。
    → list[(swing_idx, level, break_idx, d, is_choch)]；d=+1 向上突破，
    is_choch = 方向與上一個突破相反（第一個突破永遠唔算 CHoCH）。"""
    hs, ls = _swings(h, l, k)
    out = []
    last_sh = last_sl = used_sh = used_sl = -1
    prev_d = 0
    hp = lp = 0
    for j in range(c.shape[0]):
        while hp < len(hs) and hs[hp] + k <= j:
            last_sh, hp = hs[hp], hp + 1
        while lp < len(ls) and ls[lp] + k <= j:
            last_sl, lp = ls[lp], lp + 1
        if last_sh >= 0 and last_sh != used_sh and c[j] > h[last_sh]:
            used_sh = last_sh
            out.append((last_sh, h[last_sh], j, 1, prev_d == -1))
            prev_d = 1
        if last_sl >= 0 and last_sl != used_sl and c[j] < l[last_sl]:
            used_sl = last_sl
            out.append((last_sl, l[last_sl], j, -1, prev_d == 1))
            prev_d = -1
    return out


def _levels_to_arrays(n, levels):
    """levels = [(start, end, mark, price, d)] → top==bottom==price（水平位）+ mark_bull/mark_bear（標記喺邊一根）。
    🤖 mark 陣列都係價格值（其他位置 NaN）→ 主圖 Y-fit 照樣啱。"""
    z = _zones_to_arrays(n, [(s, e, p, p, d) for s, e, _m, p, d in levels])
    z['mark_bull'] = np.full(n, np.nan)
    z['mark_bear'] = np.full(n, np.nan)
    for s, e, m, p, d in levels:
        z['mark_bull' if d > 0 else 'mark_bear'][m] = p
    return z


def _level_spans(n, h, l, c, breaks, max_levels):
    """突破位由 swing 點畫到第一次被回踩（睇多：low ≤ 位；睇空：high ≥ 位），之後 NaN。"""
    out = []
    for si, level, bj, d, _ch in breaks[-max_levels:]:
        end = n - 1
        for j in range(bj + 1, n):
            if (d > 0 and l[j] <= level) or (d < 0 and h[j] >= level):
                end = j
                break
        out.append((si, end, bj, level, d))
    return out


def compute_bos(ohlc, params):
    """BOS（Break of Structure）：收盤穿過最近已確認嘅 swing high/low = 趨勢延續（順住邊個方向着，就仲喺度）。"""
    h, l, c = ohlc['h'], ohlc['l'], ohlc['c']
    k, mx = int(params['swing']), int(params['max_levels'])
    br = [b for b in _structure_breaks(h, l, c, k) if not b[4]]
    return _levels_to_arrays(c.shape[0], _level_spans(c.shape[0], h, l, c, br, mx))


def compute_choch(ohlc, params):
    """CHoCH（Change of Character）：第一個逆住上一個結構突破方向嘅收盤突破 = 潛在轉勢嘅第一聲。"""
    h, l, c = ohlc['h'], ohlc['l'], ohlc['c']
    k, mx = int(params['swing']), int(params['max_levels'])
    br = [b for b in _structure_breaks(h, l, c, k) if b[4]]
    return _levels_to_arrays(c.shape[0], _level_spans(c.shape[0], h, l, c, br, mx))


def compute_liq(ohlc, params):
    """LIQ（流動性掃蕩 / Stop Hunt）：影線穿過最近已確認嘅 swing high/low，但收盤返返入面 = 止損被掃走、真係未破位。
    掃走買方流動性（上影線出界收返入）= 睇空標記；掃走賣方流動性 = 睇多標記。"""
    h, l, c = ohlc['h'], ohlc['l'], ohlc['c']
    k, mx = int(params['swing']), int(params['max_marks'])
    hs, ls = _swings(h, l, k)
    n = c.shape[0]
    out = []
    last_sh = last_sl = used_sh = used_sl = -1
    hp = lp = 0
    for j in range(n):
        while hp < len(hs) and hs[hp] + k <= j:
            last_sh, hp = hs[hp], hp + 1
        while lp < len(ls) and ls[lp] + k <= j:
            last_sl, lp = ls[lp], lp + 1
        if last_sh >= 0 and last_sh != used_sh and h[j] > h[last_sh]:
            used_sh = last_sh          # 🤖 收盤都出咗界 = 真破位（BOS 嘅事），呢個位即消費，唔再當掃蕩
            if c[j] < h[last_sh]:
                out.append((last_sh, j, j, h[last_sh], -1))
        if last_sl >= 0 and last_sl != used_sl and l[j] < l[last_sl]:
            used_sl = last_sl
            if c[j] > l[last_sl]:
                out.append((last_sl, j, j, l[last_sl], 1))
    return _levels_to_arrays(n, out[-mx:])


def compute_eqhl(ohlc, params):
    """EQHL（Equal Highs / Equal Lows）：兩個相距唔超過 tol×ATR 嘅同類拐點 = 流動性池（止損堆喺度，錢喺嗰度）。
    池畫到收盤穿過為止（着陸 = 流動性被取走）。等高位 = 上方流動性（傾向回落）；等低位 = 下方流動性（傾向反彈）。"""
    h, l, c = ohlc['h'], ohlc['l'], ohlc['c']
    k = int(params['swing'])
    tol = float(params['tol'])
    mx = int(params['max_zones'])
    n = c.shape[0]
    atr = compute_atr(ohlc, {'period': int(params['period'])})['atr']
    hs, ls = _swings(h, l, k)
    zones = []
    for idxs, side in ((hs, -1), (ls, 1)):     # 等高位標記做睇空、等低位做睇多（錢喺邊邊，價往對面去）
        for a in range(len(idxs) - 1):
            i1, i2 = idxs[a], idxs[a + 1]
            p1 = h[i1] if side < 0 else l[i1]
            p2 = h[i2] if side < 0 else l[i2]
            ref = atr[i2]
            if np.isnan(ref) or abs(p1 - p2) > tol * ref:
                continue
            lo, up = min(p1, p2), max(p1, p2)
            end = n - 1
            for j in range(i2 + 1, n):
                if (side < 0 and c[j] > up) or (side > 0 and c[j] < lo):
                    end = j
                    break
            zones.append((i1, end, lo, up, side))
    return _zones_to_arrays(n, zones[-mx:])


def compute_pd(ohlc, params):
    """PD（Premium / Discount Array）：回看 lookback 根嘅 dealing range + 中點均衡線。
    均衡以上 = 溢價區（只宜沽），以下 = 折讓區（只宜買）—— 呢個係 ICT 決定「邊邊先值得入場」嘅尺。"""
    h, l = ohlc['h'], ohlc['l']
    w = int(params['lookback'])
    n = h.shape[0]
    hi = np.full(n, np.nan)
    lo = np.full(n, np.nan)
    if n >= w:
        import pandas as pd
        hi = pd.Series(h).rolling(w).max().to_numpy()
        lo = pd.Series(l).rolling(w).min().to_numpy()
    return {'range_hi': hi, 'equilibrium': (hi + lo) / 2.0, 'range_lo': lo}


def compute_ote(ohlc, params):
    """OTE（Optimal Trade Entry）：對最近一段推進（swing 低→高 / 高→低）嘅 fib_lo–fib_hi 回調帶（預設 0.62–0.79）。
    帶由推進終點畫到收盤返返出推進終點（延續/回調失敗）為止 —— 即係「回調到呢個帶先入場」嘅位置。"""
    h, l, c = ohlc['h'], ohlc['l'], ohlc['c']
    k = int(params['swing'])
    flo, fhi = float(params['fib_lo']), float(params['fib_hi'])
    if fhi < flo:
        flo, fhi = fhi, flo
    mx = int(params['max_zones'])
    n = c.shape[0]
    hs, ls = _swings(h, l, k)
    pts = sorted([(i, -1) for i in hs] + [(i, 1) for i in ls])   # (index, 1=低拐點 / -1=高拐點)
    legs = []
    for a in range(len(pts) - 1):
        (i1, t1), (i2, t2) = pts[a], pts[a + 1]
        if t1 == t2:
            continue
        if t1 == 1:                                   # 向上推進：低 → 高
            lo_p, hi_p = l[i1], h[i2]
            if hi_p <= lo_p:
                continue
            rng = hi_p - lo_p
            legs.append((i2, hi_p - fhi * rng, hi_p - flo * rng, hi_p, lo_p, 1))
        else:                                         # 向下推進：高 → 低
            hi_p, lo_p = h[i1], l[i2]
            if hi_p <= lo_p:
                continue
            rng = hi_p - lo_p
            legs.append((i2, lo_p + flo * rng, lo_p + fhi * rng, hi_p, lo_p, -1))
    zones = []
    for start, bot, top, hi_p, lo_p, d in legs[-mx:]:
        end = n - 1
        for j in range(start + 1, n):
            if (d > 0 and (c[j] > hi_p or c[j] < lo_p)) or (d < 0 and (c[j] < lo_p or c[j] > hi_p)):
                end = j
                break
        zones.append((start, end, bot, top, d))
    return _zones_to_arrays(n, zones)


def compute_breaker(ohlc, params):
    """BRK（Breaker Block）：俾收盤着穿咗嘅 OB（失效 Order Block）反轉再用 —— 支援變阻力、阻力變支援。
    偵測條件同 OB 完全一樣（`_ob_candidates`），分別只喺：只畫「着穿之後」嗰段，方向亦反轉。"""
    o, h, l, c = ohlc['o'], ohlc['h'], ohlc['l'], ohlc['c']
    period = int(params['period'])
    strength = float(params['strength'])
    confirm = int(params['confirm'])
    mx = int(params['max_zones'])
    max_size = float(params.get('max_size', 0.0))
    pen = float(params.get('pen', 100)) / 100.0
    supersede = bool(int(params.get('supersede', 1)))
    n = c.shape[0]
    atr = compute_atr(ohlc, {'period': period})['atr']
    zones = []
    for i, lo, hi, d, k in _ob_candidates(o, h, l, c, atr, strength, confirm, 0, False,
                                          max_size)[-mx:]:
        depth = pen * (hi - lo)
        brk = None
        for j in range(k, n):                         # OB 被消耗到 pen 深度 = 失效 → 反轉做 Breaker
            if (d > 0 and c[j] < hi - depth) or (d < 0 and c[j] > lo + depth):
                brk = j
                break
        if brk is None:
            continue                                  # OB 仲未失效 → 唔係 Breaker
        end = n - 1
        for j in range(brk + 1, n):
            if (d > 0 and c[j] > hi) or (d < 0 and c[j] < lo):
                end = j
                break
        zones.append((brk, end, lo, hi, -d))
    return _zones_to_arrays(n, _supersede(zones) if supersede else zones)


def compute_bpr(ohlc, params):
    """BPR（Balanced Price Range）：兩個方向相反、價格重疊嘅 FVG = 雙向都未填平嘅不平衡帶（好強嘅支持/阻力）。
    重疊部分由第二個缺口出現起，畫到價格完全穿過呢個帶為止。"""
    h, l, c = ohlc['h'], ohlc['l'], ohlc['c']
    period = int(params['period'])
    min_size = float(params['min_size'])
    mx = int(params['max_zones'])
    n = c.shape[0]
    atr = compute_atr(ohlc, {'period': period})['atr']
    cand = _fvg_candidates(h, l, atr, min_size)
    zones = []
    for b in range(len(cand)):
        s2, lo2, hi2, d2 = cand[b]
        for a in range(max(0, b - 20), b):            # 只同對面方向、最近 20 個缺口配對（成本可控）
            s1, lo1, hi1, d1 = cand[a]
            if d1 == d2:
                continue
            lo, up = max(lo1, lo2), min(hi1, hi2)
            if up <= lo:
                continue
            end = n - 1
            for j in range(s2 + 1, n):
                if (c[j] <= lo) or (c[j] >= up):      # 完全穿過任一邊 = 帶已用
                    end = j
                    break
            zones.append((s2, end, lo, up, d2))
    return _zones_to_arrays(n, zones[-mx:])


# ───────────────────────────── Registry ─────────────────────────────

@dataclass(frozen=True)
class ParamSpec:
    key: str
    label_key: str          # i18n key（參數名要三語；指標名唔使）
    default: float
    lo: float
    hi: float
    is_int: bool = True
    note_key: str = ''      # 一行解釋（管理頁「參數說明」；唔填即冇解釋）


@dataclass(frozen=True)
class IndicatorDef:
    key: str
    label: str              # acronym（語言中立，直接做圖表/掣顯示名）
    positions: tuple        # 准入位置：'main'（疊價格軸）/ 'sub'（獨立 panel）
    params: tuple
    compute: object         # (ohlc dict, params dict) → dict[str, ndarray 全長度]
    warmup: int             # 有意義數值所需最少 bar 數（顯示參考）
    desc_key: str = ''      # 一行描寫（管理頁列表欄 / 掣 tooltip）
    usage_key: str = ''     # 點樣用（詳情面板 / tooltip）


# 每個 def：desc_key = 一行描寫（列表欄 / tooltip）、usage_key = 點樣用（詳情面板）；
# ParamSpec.note_key = 該參數一行解釋。三語全部喺 gateway/i18n.py。
INDICATOR_DEFS = {
    'boll': IndicatorDef(
        'boll', 'BOLL', ('main',),
        (ParamSpec('period', 'ind_p_period', 20, 2, 200, note_key='ind_n_boll_period'),
         ParamSpec('dev', 'ind_p_dev', 2.0, 0.5, 5.0, is_int=False, note_key='ind_n_boll_dev')),
        compute_boll, 20, 'ind_desc_boll', 'ind_use_boll'),
    'atr': IndicatorDef(
        'atr', 'ATR', ('sub',),
        (ParamSpec('period', 'ind_p_period', 14, 1, 200, note_key='ind_n_atr_period'),),
        compute_atr, 14, 'ind_desc_atr', 'ind_use_atr'),
    'macd': IndicatorDef(
        'macd', 'MACD', ('sub',),
        (ParamSpec('fast', 'ind_p_fast', 12, 2, 200, note_key='ind_n_macd_fast'),
         ParamSpec('slow', 'ind_p_slow', 26, 3, 400, note_key='ind_n_macd_slow'),
         ParamSpec('signal', 'ind_p_signal', 9, 2, 200, note_key='ind_n_macd_signal')),
        compute_macd, 35, 'ind_desc_macd', 'ind_use_macd'),
    # ── ICT：區塊（OB 家族 / 缺口 / 結構）──
    'ob': IndicatorDef(
        'ob', 'OB', ('main',),
        (ParamSpec('period', 'ind_p_period', 14, 1, 200, note_key='ind_n_atr_period'),
         ParamSpec('strength', 'ind_p_strength', 1.0, 0.0, 5.0, is_int=False, note_key='ind_n_strength'),
         ParamSpec('confirm', 'ind_p_confirm', 3, 1, 20, note_key='ind_n_confirm'),
         ParamSpec('max_size', 'ind_p_max_size', 3.0, 0.0, 10.0, is_int=False, note_key='ind_n_max_size'),
         ParamSpec('pen', 'ind_p_pen', 50, 0, 100, note_key='ind_n_pen'),
         ParamSpec('supersede', 'ind_p_supersede', 1, 0, 1, note_key='ind_n_supersede'),
         ParamSpec('max_zones', 'ind_p_max_zones', 15, 1, 100, note_key='ind_n_max_zones')),
        compute_ob, 15, 'ind_desc_ob', 'ind_use_ob'),
    'vob': IndicatorDef(
        'vob', 'VOB', ('main',),
        (ParamSpec('period', 'ind_p_period', 14, 1, 200, note_key='ind_n_atr_period'),
         ParamSpec('strength', 'ind_p_strength', 1.0, 0.0, 5.0, is_int=False, note_key='ind_n_strength'),
         ParamSpec('confirm', 'ind_p_confirm', 3, 1, 20, note_key='ind_n_confirm'),
         ParamSpec('sweep', 'ind_p_sweep', 5, 1, 50, note_key='ind_n_sweep'),
         ParamSpec('max_size', 'ind_p_max_size', 3.0, 0.0, 10.0, is_int=False, note_key='ind_n_max_size'),
         ParamSpec('pen', 'ind_p_pen', 50, 0, 100, note_key='ind_n_pen'),
         ParamSpec('supersede', 'ind_p_supersede', 1, 0, 1, note_key='ind_n_supersede'),
         ParamSpec('max_zones', 'ind_p_max_zones', 15, 1, 100, note_key='ind_n_max_zones')),
        compute_vob, 20, 'ind_desc_vob', 'ind_use_vob'),
    'brk': IndicatorDef(
        'brk', 'BRK', ('main',),
        (ParamSpec('period', 'ind_p_period', 14, 1, 200, note_key='ind_n_atr_period'),
         ParamSpec('strength', 'ind_p_strength', 1.0, 0.0, 5.0, is_int=False, note_key='ind_n_strength'),
         ParamSpec('confirm', 'ind_p_confirm', 3, 1, 20, note_key='ind_n_confirm'),
         ParamSpec('max_size', 'ind_p_max_size', 3.0, 0.0, 10.0, is_int=False, note_key='ind_n_max_size'),
         ParamSpec('pen', 'ind_p_pen', 50, 0, 100, note_key='ind_n_pen'),
         ParamSpec('supersede', 'ind_p_supersede', 1, 0, 1, note_key='ind_n_supersede'),
         ParamSpec('max_zones', 'ind_p_max_zones', 10, 1, 100, note_key='ind_n_max_zones')),
        compute_breaker, 15, 'ind_desc_brk', 'ind_use_brk'),
    'fvg': IndicatorDef(
        'fvg', 'FVG', ('main',),
        (ParamSpec('period', 'ind_p_period', 14, 1, 200, note_key='ind_n_atr_period'),
         ParamSpec('min_size', 'ind_p_min_size', 0.0, 0.0, 5.0, is_int=False, note_key='ind_n_min_size'),
         ParamSpec('max_zones', 'ind_p_max_zones', 15, 1, 100, note_key='ind_n_max_zones')),
        compute_fvg, 15, 'ind_desc_fvg', 'ind_use_fvg'),
    'bpr': IndicatorDef(
        'bpr', 'BPR', ('main',),
        (ParamSpec('period', 'ind_p_period', 14, 1, 200, note_key='ind_n_atr_period'),
         ParamSpec('min_size', 'ind_p_min_size', 0.0, 0.0, 5.0, is_int=False, note_key='ind_n_min_size'),
         ParamSpec('max_zones', 'ind_p_max_zones', 10, 1, 100, note_key='ind_n_max_zones')),
        compute_bpr, 15, 'ind_desc_bpr', 'ind_use_bpr'),
    # ── ICT：結構／流動性 ──
    'bos': IndicatorDef(
        'bos', 'BOS', ('main',),
        (ParamSpec('swing', 'ind_p_swing', 5, 2, 50, note_key='ind_n_swing'),
         ParamSpec('max_levels', 'ind_p_max_levels', 10, 1, 50, note_key='ind_n_max_levels')),
        compute_bos, 11, 'ind_desc_bos', 'ind_use_bos'),
    'choch': IndicatorDef(
        'choch', 'CHoCH', ('main',),
        (ParamSpec('swing', 'ind_p_swing', 5, 2, 50, note_key='ind_n_swing'),
         ParamSpec('max_levels', 'ind_p_max_levels', 10, 1, 50, note_key='ind_n_max_levels')),
        compute_choch, 11, 'ind_desc_choch', 'ind_use_choch'),
    'liq': IndicatorDef(
        'liq', 'LIQ', ('main',),
        (ParamSpec('swing', 'ind_p_swing', 5, 2, 50, note_key='ind_n_swing'),
         ParamSpec('max_marks', 'ind_p_max_marks', 10, 1, 50, note_key='ind_n_max_marks')),
        compute_liq, 11, 'ind_desc_liq', 'ind_use_liq'),
    'eqhl': IndicatorDef(
        'eqhl', 'EQHL', ('main',),
        (ParamSpec('period', 'ind_p_period', 14, 1, 200, note_key='ind_n_atr_period'),
         ParamSpec('swing', 'ind_p_swing', 5, 2, 50, note_key='ind_n_swing'),
         ParamSpec('tol', 'ind_p_tol', 0.15, 0.0, 1.0, is_int=False, note_key='ind_n_tol'),
         ParamSpec('max_zones', 'ind_p_max_zones', 10, 1, 100, note_key='ind_n_max_zones')),
        compute_eqhl, 14, 'ind_desc_eqhl', 'ind_use_eqhl'),
    # ── ICT：成交區間／入場位置 ──
    'pd': IndicatorDef(
        'pd', 'PD', ('main',),
        (ParamSpec('lookback', 'ind_p_lookback', 100, 10, 1000, note_key='ind_n_lookback'),),
        compute_pd, 100, 'ind_desc_pd', 'ind_use_pd'),
    'ote': IndicatorDef(
        'ote', 'OTE', ('main',),
        (ParamSpec('swing', 'ind_p_swing', 5, 2, 50, note_key='ind_n_swing'),
         ParamSpec('fib_lo', 'ind_p_fib_lo', 0.62, 0.3, 0.95, is_int=False, note_key='ind_n_fib_lo'),
         ParamSpec('fib_hi', 'ind_p_fib_hi', 0.79, 0.4, 0.999, is_int=False, note_key='ind_n_fib_hi'),
         ParamSpec('max_zones', 'ind_p_max_zones', 5, 1, 50, note_key='ind_n_max_zones')),
        compute_ote, 11, 'ind_desc_ote', 'ind_use_ote'),
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


def _zone_boxes(x, top, bot):
    """區塊陣列 → 一個個獨立方塊 [(x0, x1, lo, hi)]。
    🤖 必須喺 level 變化位切段：同向區塊重疊時陣列係「較新者覆蓋」（level 會喺中途跳），
    淨係按 NaN 斷段就會砌成一大片連續區、睇唔到個別方塊（用戶反映「連續、唔係獨立方塊」）。
    切段之後每段 top/bot 必然恆定 → 每個方塊就係一個矩形。"""
    m = ~(np.isnan(top) | np.isnan(bot))
    idx = np.flatnonzero(m)
    if idx.size == 0:
        return []
    prev, nxt = idx[:-1], idx[1:]
    cut = (nxt != prev + 1) | (top[nxt] != top[prev]) | (bot[nxt] != bot[prev])
    out = []
    for g in np.split(idx, np.flatnonzero(cut) + 1):
        out.append((x[g[0]] - 0.5, x[g[-1]] + 0.5, float(bot[g[0]]), float(top[g[0]])))
    return out


def _zone_rects(boxes):
    """[(x0,x1,lo,hi)] → **一個** compound Path（每塊仍然係自己一個閉合 sub-path）。
    🤖 方塊照樣獨立，但 artist 由 O(方塊) 變 1 個：4 個 ICT 疊加 = 47 個 collection → 1 個，
    實測一幀 −9 ms（#23：縮放/平移順唔順， bottleneck 係 artist 數量，唔係方塊數量）。"""
    if not boxes:
        return None
    verts = np.empty((len(boxes) * 5, 2))          # 🤖 5 點/塊（尾點 = 起點重複）：CLOSEPOLY 唔用自己嗰個頂點，4 點會變三角形
    for i, (x0, x1, lo, hi) in enumerate(boxes):
        verts[i * 5:(i + 1) * 5] = ((x0, lo), (x1, lo), (x1, hi), (x0, hi), (x0, lo))
    return Path(verts, np.tile([Path.MOVETO, Path.LINETO, Path.LINETO, Path.LINETO,
                                Path.CLOSEPOLY], len(boxes)))


def _plot_zones(ax, x, sl):
    """ICT 區塊（OB/FVG/VOB/BRK/BPR/EQHL/OTE）：睇多 = C_UP / 睇空 = C_DOWN 半透明填充 + 同色邊。
    每個方塊一個 sub-path（見 `_zone_boxes`）— 單根區塊都睇到，重疊區塊各自獨立；
    同一方向合併做一個 PathCollection（#23），繪畫契約冇變：方塊幾何逐個照樣計。"""
    for tk, bk, col in (('bull_top', 'bull_bottom', gk.C_UP),
                        ('bear_top', 'bear_bottom', gk.C_DOWN)):
        p = _zone_rects(_zone_boxes(x, sl[tk], sl[bk]))
        if p is not None:
            ax.add_collection(PathCollection([p], facecolors=col, edgecolors=col,
                                             linewidths=0.6, alpha=0.14))


def _plot_levels(ax, x, sl, tag=''):
    """水平位（BOS / CHoCH / LIQ）：逐根 hlines 砌成橫線（相鄰根自然連成一段）+ 標記三角喺 action 嗰根。
    段與段之間自然斷開（陣列喺位外係 NaN → mask 收唔到）。"""
    for pk, mk, up in (('bull_top', 'mark_bull', True), ('bear_top', 'mark_bear', False)):
        top, mk_arr = sl[pk], sl[mk]
        m = ~np.isnan(top)
        if not m.any():
            continue
        col = gk.C_UP if up else gk.C_DOWN
        ax.hlines(top[m], x[m] - 0.5, x[m] + 0.5, color=col, linewidth=1.0, alpha=0.85)
        mm = ~np.isnan(mk_arr)
        if mm.any():
            ax.plot(x[mm], mk_arr[mm], ls='none', marker='^' if up else 'v',
                    markersize=5, color=col)
            if tag:
                for xi, yi in zip(x[mm], mk_arr[mm]):
                    ax.text(xi, yi, tag, fontsize=7, color=col,
                            ha='left', va='bottom' if up else 'top')


def _plot_band(ax, x, sl):
    """PD：dealing range 上下界 + 50% 均衡線（區間淡色填充、均衡線虛線）。"""
    hi, mid, lo = sl['range_hi'], sl['equilibrium'], sl['range_lo']
    m = ~(np.isnan(hi) | np.isnan(lo))
    if not m.any():
        return
    ax.fill_between(x, np.where(m, lo, 0.0), np.where(m, hi, 0.0), where=m,
                    color=gk.C_MUTED, alpha=0.05)
    ax.plot(x, hi, color=gk.C_MUTED, linewidth=0.7, alpha=0.8)
    ax.plot(x, lo, color=gk.C_MUTED, linewidth=0.7, alpha=0.8)
    ax.plot(x, mid, color=gk.C_ACCENT, linewidth=0.9, linestyle='--')


_PLOTTERS = {'boll': _plot_boll, 'atr': _plot_atr, 'macd': _plot_macd,
             'ob': _plot_zones, 'fvg': _plot_zones, 'vob': _plot_zones,
             'brk': _plot_zones, 'bpr': _plot_zones, 'eqhl': _plot_zones, 'ote': _plot_zones,
             'bos': partial(_plot_levels, tag='BOS'), 'choch': partial(_plot_levels, tag='CHoCH'),
             'liq': _plot_levels, 'pd': _plot_band}

# 🤖 ICT 疊加畫嘅係「歷史價位」（區塊/水平位/帶狀）：一個幾百根之前形成、價格再冇返去過嘅 OB 依然有效，
# 但佢嘅價位可以離可見 K 線好遠。全部照樣參與主圖 Y-fit 就會撐大條 Y 軸 → 蠟燭縮晒 + 出現一大片空白
# （用戶：「有啲 VOB 獨立出嚟，同 K 線冇連接同關係」）。BOLL/ATR/MACD 係貼價線，照舊全量參與 fit。
FAR_OVERLAYS = frozenset(('ob', 'vob', 'brk', 'fvg', 'bpr', 'eqhl', 'ote',
                          'bos', 'choch', 'liq', 'pd'))
FIT_PAD = 0.25     # 遠距離疊加最多將主圖 Y 軸擴展「可見價格範圍」嘅 25%


def _fit_vals(arr, lo, hi, pad):
    """Y-fit 用嘅值：只攞有限值，而且只攞喺可見價格範圍 [lo−pad, hi+pad] 附近嘅。
    隔籬太遠嘅值唔參與 fit → 會被 axes 自動裁走（睇唔到），但唔會擠細條 K 線。"""
    fin = arr[~np.isnan(arr)]
    if fin.size == 0:
        return fin
    return fin[(fin >= lo - pad) & (fin <= hi + pad)]


# ───────────────────────────── IndicatorKlineChart（K 線 + 指標疊加） ─────────────────────────────

class IndicatorKlineChart(gk.KlineChart):
    """KlineChart 子类 — 動態副圖 panel + 主圖指標疊加 + per-chart 計算 cache。

    ⚠️ coupling 契約：依賴 parent 嘅 `_frame`（讀實例屬性 self.ax/self.axv → 重新綁定即自動畫到新 axes）、
    `self._s`（絕對→本地座標基準；#22 之後 parent 用絕對 index 繪畫 → 恒為 0，即本地 == 絕對）、
    手勢 guard `(self.ax, self.axv)`、以及 parent 唔再 `ax.clear()`（指標 artist 由本类自己追蹤清除）。
    改 gui_kline.KlineChart 內部要同步檢查呢度。
    冇 `set_indicator_manager()`（例如行情頁 6 格 / standalone gui_kline）→ 行為同 parent 逐字相同。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ind_mgr = None
        self._ind_dirty = False
        self._ind_artists = []       # #22：本幀砌咗嘅指標 artist（下一幀開始時逐個 remove）
        self._panel_cross = []       # #22 B：各 panel 預建嘅 crosshair（hover 只 set_xdata）
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
        self._ind_artists = []       # fig.clear() 已經清晒 → 唔好留住舊 ref
        self._static_dirty = True    # 🤖 新 axes 係白紙 → parent 下一幀必須重建靜態層（#22）
        # 🤖 唔准 reset _view/_drag — 用戶平移/縮放位置喺開關指標前後必須保持

    # --- 繪製 -----------------------------------------------------------------
    def _frame(self):
        """重寫 parent hook：砌 panel（需要時）→ 蠟燭/成交量自動畫到（可能已重建嘅）self.ax/self.axv → 疊指標。
        🤖 用 hook 而唔係重寫 `_redraw`：parent 嘅手勢節流（`_request_redraw`）先至會行到呢度。"""
        if self._ind_mgr is not None:
            sig = tuple(p[0] for p in self._visible_panels())
            if self._ind_dirty or sig != self._axes_sig:
                self._build_axes()
        super()._frame()
        if self._ind_mgr is not None:
            self._draw_indicators()

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

    @staticmethod
    def _ax_artists(ax):
        return list(ax.lines) + list(ax.collections) + list(ax.texts) + list(ax.patches)

    def _draw_indicators(self):
        # 指標 panel 要自己 clear（parent 淨係管自己嘅 artist）
        for a in self.ax_ind:
            a.clear()
            self._style_ax(a)
        self._panel_cross = []
        # 🤖 #22：parent 唔再每幀 `ax.clear()` → 主圖上面砌過嘅指標 artist 要自己逐個清，否則逐幀疊加
        for a in self._ind_artists:
            try:
                a.remove()
            except Exception:        # axes 已重建（_build_axes / fig.clear）→ 唔喺度
                pass
        self._ind_artists = []
        n = len(self._rows)
        if n == 0:
            return
        base = {id(a) for a in self._ax_artists(self.ax)}   # 靜態層（parent 砌嘅）唔算指標
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
        # 🤖 但 ICT 疊加只計「喺可見價格範圍附近」嘅值，否則歷史區塊會撐大條 Y 軸（見 FAR_OVERLAYS）
        lo0, hi0 = self.ax.get_ylim()
        pad = max(1e-9, (hi0 - lo0) * FIT_PAD)
        main_vals = []
        for e in self._ind_mgr.items():
            if not e['enabled'] or e['position'] != 'main':
                continue
            d = INDICATOR_DEFS.get(e['def'])
            sl = _slice(e['id'])
            if d is None or sl is None:
                continue
            _PLOTTERS[d.key](self.ax, x, sl)
            far = d.key in FAR_OVERLAYS
            for arr in sl.values():
                fin = _fit_vals(arr, lo0, hi0, pad) if far else arr[~np.isnan(arr)]
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
            tick_i = self._view_ticks(*self.ax.get_xlim(), len(self._rows))   # 🤖 同一個 clamp（出界刻度會撐開 limits）
            bottom.set_xticks(tick_i)
            bottom.set_xticklabels([gk._short_time(self._rows[i][0]) for i in tick_i], fontsize=8)

        # ── crosshair 補畫到指標 panel（parent 淨係畫 self.ax）：預建 + 之後只改 x（#22 B）──
        for a in self.ax_ind:
            self._panel_cross.append(
                a.axvline(0, color=gk.C_MUTED, linewidth=0.8, linestyle='--', alpha=0.8,
                          visible=False))
        self._update_hover()
        # 🤖 記低呢一幀喺主圖砌咗嘅指標 artist → 下一幀開始逐個 remove（parent 唔再 clear 主圖）
        self._ind_artists = [a for a in self._ax_artists(self.ax) if id(a) not in base]

    def _update_hover(self):
        """#22 B：郁滑鼠只改預建線嘅 x —— 主圖之外，指標 panel 嗰啲一齊跟（唔返嚟重砌 panel）。"""
        super()._update_hover()
        i = self._hover_idx
        on = i is not None and self._s <= i < len(self._rows)
        for ln in self._panel_cross:
            try:
                ln.set_visible(on)
                if on:
                    ln.set_xdata([i - self._s, i - self._s])
            except Exception:        # panel 已重建 → 呢條線作廢（下一幀會重建）
                pass

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

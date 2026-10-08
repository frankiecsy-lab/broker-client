"""E2E GUI test — 指標管理（ticket #19）：純計算 + Manager + IndicatorKlineChart + K線頁嵌入 + 管理頁。

Run: python .scratch/e2e_gui_indicators.py   (from project root; QT_QPA_PLATFORM=offscreen 自動設定。
     全 hermetic：tmp state 檔 + FakeDir（包住真 symbol index，is_stale=False 唔 auto-fetch）— 唔打 broker)
前置：modules/symbol_index.json 存在（K線頁嵌入部分，照 e2e_gui_p8）。

Flow:
1. 純計算：BOLL/ATR/MACD 長度==n、warm-up NaN 數、Wilder/ewm/rolling 對照、hist==HIST_SCALE×(dif−dea)
2. Manager：seed、CRUD、clamp/容忍 load、上限、版本、listener(origin)
3. IndicatorKlineChart：裸子类==parent（2 axes）；注入 BOLL+ATR → 3 axes、panel 有線/標題、x 標籤遷移
4. 開關：disable→axes 減、enable→返；用戶 _view（平移/縮放）保持
5. cache：hover 零重算 / set_bars +1 / 改參數 +1 且陣列唔同
6. KlinePage 嵌入：chart 實例已換、開關掣列、跨頁同步（add/disable/改參數）、theme QSS 帶 indtoggle
7. 管理頁：CRUD 經 widget、checkbox、跨頁同步、retranslate 三語
8. shell 註冊：'indicators' in PAGE_KEYS/NAV_DIRECT
9. ICT 區塊（ticket #20）：手砌 fixture 逐條斷言 OB/FVG/VOB 區塊起訖 + 過濾參數；隨機數據 → 主圖 fill_between
   真的砌出 PolyCollection；管理頁 def combo / 參數欄 / 三語

Exit code 0 = all pass; non-zero = at least one check failed.
"""
import os
import sys
import tempfile
import time
import types
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import gateway.state_store as state_store  # noqa: E402

_TMPDIR = tempfile.mkdtemp()
state_store.STATE_PATH = Path(_TMPDIR) / 'ui_state.json'   # 🤖 hermetic：tmp state 檔

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])  # noqa: E402

FAILURES = []


def check(name, ok):
    print(('  ✅ ' if ok else '  ❌ ') + name, flush=True)
    if not ok:
        FAILURES.append(name)


def pump(n=40):
    for _ in range(n):
        app.processEvents()


def make_rows(n=400, seed=7):
    """合成 K 線 rows（trend + noise；格式同 set_bars 契約）。"""
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(0, 1, n))
    rows = []
    for i in range(n):
        ci = float(c[i])
        o = ci + rng.normal(0, 0.3)
        hi = max(o, ci) + abs(rng.normal(0, 0.5))
        lo = min(o, ci) - abs(rng.normal(0, 0.5))
        rows.append((f"2026-01-01 {i // 60:02d}:{i % 60:02d}:00", o, hi, lo, ci, 1000 + i))
    return rows


# ══ Part 1：純計算（無 Qt 邏輯）══════════════════════════════════════════
print('── Part 1: 指標純計算 ──')
from gateway import indicators as ind  # noqa: E402

rows = make_rows()
n = len(rows)
ohlc = {'o': np.array([r[1] for r in rows]), 'h': np.array([r[2] for r in rows]),
        'l': np.array([r[3] for r in rows]), 'c': np.array([r[4] for r in rows])}

import pandas as pd  # noqa: E402

b = ind.compute_boll(ohlc, {'period': 20, 'dev': 2.0})
check('BOLL 長度==n / warm-up NaN==19 / rolling 對照',
      all(v.shape[0] == n for v in b.values()) and int(np.isnan(b['mid']).sum()) == 19
      and np.allclose(b['mid'][19:], pd.Series(ohlc['c']).rolling(20).mean().to_numpy()[19:]))
check('BOLL upper≥mid≥lower', np.all(b['upper'][19:] >= b['mid'][19:] - 1e-9)
      and np.all(b['lower'][19:] <= b['mid'][19:] + 1e-9))

a = ind.compute_atr(ohlc, {'period': 14})
tr = np.empty(n)
tr[0] = ohlc['h'][0] - ohlc['l'][0]
for i in range(1, n):
    tr[i] = max(ohlc['h'][i] - ohlc['l'][i], abs(ohlc['h'][i] - ohlc['c'][i - 1]),
                abs(ohlc['l'][i] - ohlc['c'][i - 1]))
ref = np.full(n, np.nan)
ref[13] = tr[:14].mean()
for i in range(14, n):
    ref[i] = ref[i - 1] + (tr[i] - ref[i - 1]) / 14
check('ATR 長度==n / warm-up NaN==13 / Wilder RMA 對照',
      a['atr'].shape[0] == n and int(np.isnan(a['atr']).sum()) == 13
      and np.allclose(a['atr'][13:], ref[13:]))

m = ind.compute_macd(ohlc, {'fast': 12, 'slow': 26, 'signal': 9})
dif_ref = (pd.Series(ohlc['c']).ewm(span=12, adjust=False).mean()
           - pd.Series(ohlc['c']).ewm(span=26, adjust=False).mean()).to_numpy()
check('MACD 全長度有值 / dif==ewm(adjust=False) 對照 / hist==HIST_SCALE×(dif−dea)',
      all(not np.isnan(v).any() for v in m.values())
      and np.allclose(m['dif'], dif_ref)
      and np.allclose(m['hist'], ind.HIST_SCALE * (m['dif'] - m['dea'])))

# ══ Part 2：IndicatorManager ══════════════════════════════════════════════
print('── Part 2: IndicatorManager ──')
ind.reset_manager_for_test()
mgr = ind.get_manager()
check('首次 seed = BOLL(main)+ATR(sub) 並寫入 state 檔',
      [(e['def'], e['position'], e['enabled']) for e in mgr.items()]
      == [('boll', 'main', True), ('atr', 'sub', True)] and '"items"' in state_store.STATE_PATH.read_text(encoding='utf-8'))
ok, _, item = mgr.add('macd', 'sub', {'slow': 500}, origin='t')
check('add + 參數 clamp（slow 500→400）', ok and item['params']['slow'] == 400)
ok2, _, item2 = mgr.add('boll', 'sub', {}, origin='t')
check('位置唔准入 → 退回首個准入值(main)', ok2 and item2['position'] == 'main')
for _ in range(6):
    mgr.add('atr', 'sub', {}, origin='t')
check('上限：總 ≤6 / sub ≤4，超過 → ind_limit',
      len(mgr.items()) <= ind.MAX_ITEMS
      and sum(1 for e in mgr.items() if e['position'] == 'sub') == ind.MAX_SUB_ITEMS
      and mgr.add('macd', 'sub', {}, origin='t') == (False, 'ind_limit', None))
cfg0 = mgr.config_version
mid_id = item['id']
ok, _ = mgr.update(mid_id, params={'fast': 8}, origin='t')
check('update params → config_version+1 + 生效',
      ok and mgr.config_version == cfg0 + 1 and mgr.get(mid_id)['params']['fast'] == 8)
mgr.set_enabled(mid_id, False, origin='t')
check('set_enabled → 生效且唔 bump config_version',
      not mgr.get(mid_id)['enabled'] and mgr.config_version == cfg0 + 1)
mgr.remove(mid_id, origin='t')
check('remove 生效', mgr.get(mid_id) is None)

import json  # noqa: E402
doc = json.loads(state_store.STATE_PATH.read_text(encoding='utf-8'))
doc['indicators']['items'] += [
    {'id': 'ind-99', 'def': 'nope', 'position': 'sub', 'params': {}, 'enabled': True},
    {'id': 'ind-98', 'def': 'atr', 'position': 'main', 'params': {'period': 9999}, 'enabled': True}]
state_store.STATE_PATH.write_text(json.dumps(doc), encoding='utf-8')
ind.reset_manager_for_test()
mgr2 = ind.get_manager()
e98 = mgr2.get('ind-98')
check('容忍 load：unknown def drop / 位置退回 / clamp / id 防撞',
      mgr2.get('ind-99') is None and e98['position'] == 'sub'
      and e98['params']['period'] == 200 and mgr2._next_id > 98)
seen = []
mgr2.add_listener(lambda o, k: seen.append((o, k)))
mgr2.set_enabled('ind-98', False, origin='me')
check('listener 收到 notify（帶 origin）', seen == [('me', 'enable')])

# ══ Part 3-5：IndicatorKlineChart（axes / 開關 / _view / cache）═══════════
print('── Part 3-5: IndicatorKlineChart ──')
# 全新 seed 狀態（乾淨斷言）
state_store.STATE_PATH = Path(_TMPDIR) / 'ui_state2.json'
ind.reset_manager_for_test()
mgr = ind.get_manager()
atr_id = next(e['id'] for e in mgr.items() if e['def'] == 'atr')

chart = ind.IndicatorKlineChart()
chart.set_bars(rows)
check('裸子类（冇 manager）axes==2 — 同 parent 逐字相同', len(chart.canvas.figure.axes) == 2)
chart.set_indicator_manager(mgr)
chart._redraw()
check('注入 BOLL+ATR → axes==3 + 主圖 BOLL 線 + ATR panel 有線/標題',
      len(chart.canvas.figure.axes) == 3 and len(chart.ax.get_lines()) >= 3
      and len(chart._ind_axis[atr_id].get_lines()) >= 1
      and 'ATR' in chart._ind_axis[atr_id].get_title(loc='left'))
check('x 時間標籤遷移到最底軸（volume 標籤隱藏）',
      any(lb.get_text() for lb in chart._ind_axis[atr_id].get_xticklabels())
      and not any(lb.get_text() and lb.get_visible() for lb in chart.axv.get_xticklabels()))

chart._view = (50.0, 250.0)
chart._redraw()
xl0 = chart.ax.get_xlim()
mgr.set_enabled(atr_id, False, origin='t')
chart._redraw()
check('disable → axes==2（BOLL 主圖仍喺）', len(chart.canvas.figure.axes) == 2
      and len(chart.ax.get_lines()) >= 3)
mgr.set_enabled(atr_id, True, origin='t')
chart._redraw()
xl1 = chart.ax.get_xlim()
check('enable → axes==3 + 用戶 _view 保持（xlim 冇跳返跟隨）',
      len(chart.canvas.figure.axes) == 3
      and abs(xl1[0] - xl0[0]) < 1e-6 and abs(xl1[1] - xl0[1]) < 1e-6)

calls = []
_orig = chart._recompute_indicators
chart._recompute_indicators = lambda: (calls.append(1), _orig())[1]
chart._hover_idx = 100
chart._redraw(); chart._redraw(); chart._redraw()
check('hover×3 → 重算 0 次（cache 命中）', len(calls) == 0)
chart.set_bars(rows + [rows[-1]])
check('set_bars → 重算 1 次', len(calls) == 1)
before = np.array(chart._ind_cache['full'][atr_id]['atr'])
mgr.update(atr_id, params={'period': 3}, origin='t')
chart._redraw()
after = np.array(chart._ind_cache['full'][atr_id]['atr'])
check('改 period → 重算 +1 且陣列唔同', len(calls) == 2 and not np.allclose(before[13:], after[13:], equal_nan=True))

chart._view = None
chart._redraw()
axp = chart._ind_axis[atr_id]
chart._on_scroll(types.SimpleNamespace(inaxes=axp, xdata=100.0, step=1, button=1, ydata=5.0))
check('指標 panel 上面 scroll/右鍵/hover 手勢生效', chart._view is not None)
chart._on_press(types.SimpleNamespace(inaxes=axp, xdata=100.0, button=3, ydata=5.0))
check('panel 上右鍵 → 復位跟隨', chart._view is None)

ok, _, macd_item = mgr.add('macd', 'sub', {}, origin='t')
chart._redraw()
axm = chart._ind_axis[macd_item['id']]
check('add MACD → axes==4 + 柱/零線/2 線 + 標題', ok and len(chart.canvas.figure.axes) == 4
      and len(axm.patches) > 0 and len(axm.get_lines()) >= 3
      and 'MACD' in axm.get_title(loc='left'))
for e in mgr.items():
    mgr.set_enabled(e['id'], False, origin='t')
chart._redraw()
check('全部 disable → axes==2', len(chart.canvas.figure.axes) == 2)

# ══ Part 6：KlinePage 嵌入（換 chart / 掣列 / 跨頁 / theme）═══════════════
print('── Part 6: KlinePage 嵌入 ──')
from gateway.pages import gui_kline as gk  # noqa: E402
from modules.symbol_search import CORE_TYPES, get_directory as real_get_directory  # noqa: E402


class FakeDir:
    """包住真 index：is_stale=False（hermetic — startup 唔會 auto-fetch），其餘 delegate（照 e2e_gui_p8）。"""

    def __init__(self, real):
        self._real = real
        self.entries = real.entries
        self.fetched_at = real.fetched_at

    @property
    def is_stale(self):
        return False

    def search(self, q, limit=20, types=CORE_TYPES):
        return self._real.search(q, limit, types)

    def display_name(self, code, lang='zh'):
        return self._real.display_name(code, lang)

    def has_code(self, c):
        return self._real.has_code(c)


gk.get_directory = lambda: FakeDir(real_get_directory())

state_store.STATE_PATH = Path(_TMPDIR) / 'ui_state3.json'
ind.reset_manager_for_test()

from gateway.pages.kline_page import KlinePage  # noqa: E402
import gateway.theme as theme_mod  # noqa: E402

page = KlinePage()
page.show()
pump()
chart2 = page._win.chart
check('KlinePage：chart 已換成 IndicatorKlineChart', isinstance(chart2, ind.IndicatorKlineChart))
ids = [e['id'] for e in page._mgr.items()]
check('開關掣列 = 2 個（objectName ind_toggle_* + property og=indtoggle）',
      sorted(page._ind_toggles) == sorted(ids)
      and all(b.objectName().startswith('ind_toggle_') and b.property('og') == 'indtoggle'
              and b.isCheckable() and b.isChecked() for b in page._ind_toggles.values()))
chart2.set_bars(rows)
pump()
check('餵數據 → axes==3', len(chart2.canvas.figure.axes) == 3)
btn_atr = page._ind_toggles[atr_id]
btn_atr.click()
pump()
off_ok = len(chart2.canvas.figure.axes) == 2
btn_atr.click()
pump()
check('撳掣 off→axes 2 / on→axes 3', off_ok and len(chart2.canvas.figure.axes) == 3)
ok, _, macd3 = page._mgr.add('macd', 'sub', {}, origin='ind_page')
pump()
check('管理頁 add → 掣列 3 個 + axes==4', ok and len(page._ind_toggles) == 3
      and len(chart2.canvas.figure.axes) == 4)
page._mgr.set_enabled(macd3['id'], False, origin='ind_page')
pump()
check('管理頁 disable → 掣 uncheck + axes==3',
      not page._ind_toggles[macd3['id']].isChecked() and len(chart2.canvas.figure.axes) == 3)
page._mgr.update(macd3['id'], params={'fast': 5}, origin='ind_page')
pump()
check('管理頁改參數 → 掣 text 帶新參數', '5/26/9' in page._ind_toggles[macd3['id']].text())
page.retranslate('en')
check('K線頁 retranslate EN → 掣列標籤英文', 'Indicators' in page._ind_bar_lbl.text())
page.retranslate('zh_hk')
theme_mod.apply_theme('light')
pump()
qss_ok = 'og="indtoggle"' in page.styleSheet() and '#D0D4D9' in page.styleSheet()
theme_mod.apply_theme('dark')
pump()
check('theme 切換 → 頁面 QSS 帶 indtoggle 規則跟 palette + chart 正常 redraw',
      qss_ok and len(chart2.canvas.figure.axes) == 3)
page._on_app_quit()
pump()

# ══ Part 7：管理頁 CRUD（經 widget）═══════════════════════════════════════
print('── Part 7: 指標管理頁 ──')
state_store.STATE_PATH = Path(_TMPDIR) / 'ui_state4.json'
ind.reset_manager_for_test()
from PySide6.QtCore import Qt  # noqa: E402
from gateway.pages.indicators_page import IndicatorsPage  # noqa: E402

ipage = IndicatorsPage()
ipage.show()
pump()
check('表格 = seed 2 行 + 三語表頭', ipage.model.rowCount() == 2
      and ipage.model.headerData(1, Qt.Horizontal) == '指標')
ipage.def_combo.setCurrentIndex(ipage._def_keys.index('macd'))
pump()
check('揀 MACD → 位置 combo 得准入位置(sub)', ipage.pos_combo.currentData() == 'sub')
ipage._param_spins['fast'].setValue(5)
ipage.add_btn.click()
pump()
check('新增 → 3 行 + status', ipage.model.rowCount() == 3 and '已新增' in ipage.status_lbl.text())
macd_id = next(e['id'] for e in ind.get_manager().items() if e['def'] == 'macd')
ipage.table.selectRow(2)
pump()
check('揀行 → 編輯模式填入', ipage._sel_id == macd_id and ipage._param_spins['fast'].value() == 5)
ipage._param_spins['fast'].setValue(8)
ipage.save_btn.click()
pump()
check('套用修改 → params 更新', ind.get_manager().get(macd_id)['params']['fast'] == 8)
ipage.model.setData(ipage.model.index(1, 0), Qt.Unchecked, Qt.CheckStateRole)
pump()
check('表格 checkbox → set_enabled 即時生效', not next(
    e['enabled'] for e in ind.get_manager().items() if e['def'] == 'atr'))
atr4 = next(e['id'] for e in ind.get_manager().items() if e['def'] == 'atr')
ind.get_manager().set_enabled(atr4, True, origin='kline_page')
pump()
check('K線頁端 enable → 管理頁表格即時打勾',
      ipage.model.data(ipage.model.index(1, 0), Qt.CheckStateRole) == Qt.Checked)
ipage.table.selectRow(2)
pump()
ipage.remove_btn.click()
pump()
check('移除所選 → 2 行', ipage.model.rowCount() == 2 and ind.get_manager().get(macd_id) is None)
ipage.retranslate('en')
pump()
check('管理頁 retranslate EN', 'Add indicator' in ipage.add_btn.text()
      and ipage.model.headerData(1, Qt.Horizontal) == 'Indicator')

# ══ Part 8：shell 註冊 ════════════════════════════════════════════════════
print('── Part 8: shell 註冊 ──')
from gateway.app import NAV_DIRECT, PAGE_KEYS  # noqa: E402
check("'indicators' in PAGE_KEYS + NAV_DIRECT（用戶指定頂層直按）",
      'indicators' in PAGE_KEYS and 'indicators' in NAV_DIRECT)

# ══ Part 9：ICT 區塊指標（OB / FVG / VOB，ticket #20）════════════════════
print('── Part 9: ICT 區塊指標（OB / FVG / VOB）──')
from gateway.i18n import t as _t  # noqa: E402

# 手砌 6 根：bar1/bar2 陰燭 → bar3 收盤突破；bar4 形成向上三根缺口；bar2 影線掃走前低
FIX = [(105.0, 105.5, 104.0, 104.5), (104.5, 105.0, 103.5, 104.0), (104.0, 104.2, 102.5, 103.0),
       (103.0, 105.5, 102.8, 105.2), (105.2, 107.0, 104.5, 106.8), (106.8, 108.0, 106.0, 107.5)]
fo = {'o': np.array([b[0] for b in FIX]), 'h': np.array([b[1] for b in FIX]),
      'l': np.array([b[2] for b in FIX]), 'c': np.array([b[3] for b in FIX])}


def span(arr):
    idx = np.where(~np.isnan(arr))[0]
    return None if idx.size == 0 else (int(idx[0]), int(idx[-1]))


fv = ind.compute_fvg(fo, {'period': 1, 'min_size': 0.0, 'max_zones': 15})
check('FVG：向上缺口 [104.2,104.5] 喺 bar4、bar5 由較新缺口覆蓋、bar0–3 冇值、冇向下缺口',
      fv['bull_bottom'][4] == 104.2 and fv['bull_top'][4] == 104.5
      and fv['bull_bottom'][5] == 105.5 and fv['bull_top'][5] == 106.0
      and np.isnan(fv['bull_bottom'][3]) and np.isnan(fv['bear_top']).all())
check('FVG：min_size=1×ATR 過濾細缺口 / max_zones=1 淨返最近一個',
      all(np.isnan(v).all() for v in ind.compute_fvg(fo, {'period': 1, 'min_size': 1.0, 'max_zones': 15}).values())
      and span(ind.compute_fvg(fo, {'period': 1, 'min_size': 0.0, 'max_zones': 1})['bull_bottom']) == (5, 5))
obp = {'period': 1, 'strength': 1.0, 'confirm': 3, 'max_zones': 15}
ob = ind.compute_ob(fo, obp)
check('OB：bar2 陰燭被收盤突破 → 區塊 [102.5,104.2] 由 bar2 畫到結尾（冇收盤穿過對面邊）',
      ob['bull_bottom'][2] == 102.5 and ob['bull_top'][2] == 104.2 and not np.isnan(ob['bull_bottom'][5]))
check('OB：strength=99×ATR → 冇位移 → 完全冇區塊',
      all(np.isnan(v).all() for v in ind.compute_ob(fo, dict(obp, strength=99.0)).values()))
vob = ind.compute_vob(fo, dict(obp, sweep=2))
check('VOB：bar1 冇掃流動性 → 剔除（淨返 bar2 起）',
      span(vob['bull_bottom']) == (2, 5) and np.isnan(vob['bull_bottom'][1]))
check('VOB：sweep=1 → bar1 都算（區塊由 bar1 開始）',
      span(ind.compute_vob(fo, dict(obp, sweep=1))['bull_bottom']) == (1, 5))

# 真畫出嚟：主圖 fill_between → PolyCollection（主圖疊加唔砌新 panel）
state_store.STATE_PATH = Path(_TMPDIR) / 'ui_state5.json'
ind.reset_manager_for_test()
mgr9 = ind.get_manager()
chart9 = ind.IndicatorKlineChart()
chart9.set_indicator_manager(mgr9)
chart9.set_bars(rows)
chart9._redraw()
col0 = len(chart9.ax.collections)
axes0 = len(chart9.canvas.figure.axes)
ok_add = [mgr9.add(k, 'main', {'strength': 0.3, 'confirm': 5, 'sweep': 3, 'max_zones': 50}, origin='t')[0]
          for k in ('ob', 'fvg', 'vob')]
chart9._redraw()
# 每個指標 bull / bear 各一個 fill_between（邊一邊冇區塊就唔畫）→ 期望數量按可見 slice 嘅 mask 計
k9 = len(rows) - chart9._s
xl0, xl1 = chart9.ax.get_xlim()
i0, i1 = max(0, int(np.floor(xl0))), min(k9, int(np.ceil(xl1)) + 1)
exp = sum(1 for e in mgr9.items() if e['def'] in ('ob', 'fvg', 'vob')
          for tk, bk in (('bull_top', 'bull_bottom'), ('bear_top', 'bear_bottom'))
          if (~np.isnan(chart9._ind_cache['full'][e['id']][tk][chart9._s + i0:chart9._s + i1])
              & ~np.isnan(chart9._ind_cache['full'][e['id']][bk][chart9._s + i0:chart9._s + i1])).any())
check(f'add ob/fvg/vob → 主圖多 {exp} 個 PolyCollection（每個指標 bull/bear 各一）+ axes 唔變（main 唔砌 panel）',
      all(ok_add) and exp >= 3 and len(chart9.ax.collections) == col0 + exp
      and len(chart9.canvas.figure.axes) == axes0)
paths = chart9.ax.collections[-1].get_paths()
check('區塊 path 非空（真有區塊，唔係空 collection）', len(paths) > 0 and len(paths[0].vertices) >= 3)
bad = []
for e in mgr9.items():
    if e['def'] in ('ob', 'fvg', 'vob'):
        s = chart9._ind_cache['full'][e['id']]
        if not all(a.shape[0] == len(rows) for a in s.values()):
            bad.append(e['def'] + ':長度')
        t_, b_ = s['bull_top'], s['bull_bottom']
        m = ~np.isnan(t_)
        if not m.any():
            bad.append(e['def'] + ':冇區塊')
        elif not (t_[m] >= b_[m]).all():
            bad.append(e['def'] + ':top<bottom')
if bad:
    print('     ⚠️ ' + ', '.join(bad))
check('隨機 400 根：三個 ICT 指標都有區塊 / 全長度陣列 / top≥bottom', not bad)

# 管理頁：def combo / 參數欄 / 三語
state_store.STATE_PATH = Path(_TMPDIR) / 'ui_state6.json'
ind.reset_manager_for_test()
ipage9 = IndicatorsPage()
ipage9.show()
pump()
labels = {ipage9.def_combo.itemText(i) for i in range(ipage9.def_combo.count())}
check('管理頁 def combo 見到 OB / FVG / VOB', {'OB', 'FVG', 'VOB'} <= labels)
ipage9.def_combo.setCurrentIndex(ipage9._def_keys.index('vob'))
pump()
check('揀 VOB → 位置只准 main + 參數欄砌到 5 個（含 sweep）',
      ipage9.pos_combo.count() == 1 and ipage9.pos_combo.currentData() == 'main'
      and set(ipage9._param_spins) == {'period', 'strength', 'confirm', 'sweep', 'max_zones'})
ipage9._param_spins['confirm'].setValue(5)
ipage9.add_btn.click()
pump()
check('新增 VOB → 表格 3 行 + 參數摘要 14/1/5/5/15',
      ipage9.model.rowCount() == 3
      and ipage9.model.data(ipage9.model.index(2, 1)) == 'VOB'
      and ipage9.model.data(ipage9.model.index(2, 3)) == '14/1/5/5/15')
ipage9.retranslate('en')
pump()
check('ICT 參數名三語（label + i18n）',
      _t('ind_p_confirm', 'en') == 'Confirm bars' and 'Confirm bars' in ipage9._param_lbls['confirm'].text())

print('\n' + ('全部通過 ✅' if not FAILURES else f'失敗 {len(FAILURES)} 項：{FAILURES}'))
sys.exit(0 if not FAILURES else 1)

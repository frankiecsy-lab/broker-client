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
   + `.ui` 骨架（kline_page.ui：embeddedSlot 填嵌入內容、ind_bar/indToggleSlot 按配置填、插位喺圖上方）
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

from PySide6.QtCore import QMargins, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox  # noqa: E402

app = QApplication.instance() or QApplication([])  # noqa: E402

FAILURES = []


def check(name, ok):
    print(('  ✅ ' if ok else '  ❌ ') + name, flush=True)
    if not ok:
        FAILURES.append(name)


def pump(n=40):
    for _ in range(n):
        app.processEvents()


def settle(ms=360):
    """等 KlineChart 嘅過渡動畫（#23 `_animate_to`，ANIM_MS=220ms）行完 —— QTimer 要真時鐘 + event loop。"""
    from PySide6.QtTest import QTest
    QTest.qWait(ms)
    pump()


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
# 🖱️ #23：scroll / 復位 而家一律經 `_animate_to`（ease-out）→ 手勢只「起過渡」，`_view` 要等插值行完先變
chart._on_scroll(types.SimpleNamespace(inaxes=axp, xdata=100.0, step=1, button=1, ydata=5.0))
check('指標 panel 上面 scroll 手勢生效（起咗過渡、target 有值）', chart._anim is not None)
settle()
check('過渡行完 → 縮放生效（_view 有值）', chart._view is not None)
chart._on_press(types.SimpleNamespace(inaxes=axp, xdata=100.0, button=3, ydata=5.0))
check('panel 上右鍵 → 起復位過渡（末參數 = 終止後返跟隨）', chart._anim is not None and chart._anim[-1] is True)
settle()
check('復位過渡行完 → 返跟隨（_view is None）', chart._view is None)

# 用戶要求：所有 K 線圖左鍵雙擊 = 還原縮放 + 返到最新 K 柱（parent 實作，子类自動繼承）
chart._on_scroll(types.SimpleNamespace(inaxes=chart.ax, xdata=100.0, step=1, button=1, ydata=5.0))
settle()
zoomed = chart._view
chart._on_press(types.SimpleNamespace(inaxes=chart.ax, xdata=100.0, button=1, ydata=5.0, dblclick=True))
check('主圖左鍵雙擊 → 先縮放過（_view 有值）再復位返跟隨最新',
      zoomed is not None and chart._anim is not None and chart._anim[-1] is True)
settle()
check('雙擊過渡行完 → 返跟隨最新', chart._view is None)
chart._on_scroll(types.SimpleNamespace(inaxes=axp, xdata=100.0, step=1, button=1, ydata=5.0))
settle()
zoomed2 = chart._view
chart._on_press(types.SimpleNamespace(inaxes=axp, xdata=100.0, button=1, ydata=5.0, dblclick=True))
check('指標 panel 上左鍵雙擊一樣有效（子类 inaxes 改寫覆蓋到雙擊）',
      zoomed2 is not None and chart._anim is not None)
settle()
check('panel 雙擊過渡行完 → 返跟隨', chart._view is None)
chart._on_press(types.SimpleNamespace(inaxes=chart.ax, xdata=100.0, button=1, ydata=5.0))
chart._on_press(types.SimpleNamespace(inaxes=chart.ax, xdata=100.0, button=1, ydata=5.0, dblclick=False))
check('單擊（dblclick=False）唔會復位 — 照樣可以拖動', chart._view is None and chart._drag is not None)
chart._on_release(types.SimpleNamespace(inaxes=chart.ax, xdata=100.0, button=1, ydata=5.0))

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
# ── `.ui` 骨架（排版喺 gateway/ui/kline_page.ui；gui_kline 零改動 → 內容填進 embeddedSlot）──
_central = chart2.parentWidget()
_play = page.layout()
check('`.ui` root objectName + Designer margin/spacing 照載入',
      page.objectName() == 'kline_page' and _play is not None
      and _play.contentsMargins() == QMargins(0, 0, 0, 0) and _play.spacing() == 0)
check('嵌入嘅 gui_kline central widget 填進 `.ui` 嘅 embeddedSlot',
      page.embeddedSlot.count() == 1 and page.embeddedSlot.itemAt(0).widget() is _central)
check('ind_bar / ind_bar_lbl / indToggleSlot 由 `.ui` 建出（含 Designer margin），並被插進 K 線圖正上方',
      page.ind_bar is not None and page.ind_bar_lbl is not None
      and page.ind_bar.layout().contentsMargins() == QMargins(6, 2, 6, 0)
      and _central.layout().indexOf(page.ind_bar) == _central.layout().indexOf(chart2) - 1)
ids = [e['id'] for e in page._mgr.items()]
check('開關掣按 IndicatorManager 配置填進 indToggleSlot（加指標唔使改 `.ui`）',
      page.indToggleSlot.count() == len(page._ind_toggles) == len(ids)
      and all(page.indToggleSlot.itemAt(i).widget() is b
              for i, b in enumerate(page._ind_toggles.values())))
check('WA_StyledBackground 由 _STAMP 補返 + 頁面級 QSS 以 objectName 為根',
      page.testAttribute(Qt.WA_StyledBackground) and 'QWidget#ind_bar' in page.styleSheet())
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
check('K線頁 retranslate EN → 掣列標籤英文', 'Indicators' in page.ind_bar_lbl.text())
page.retranslate('zh_hk')
theme_mod.apply_theme('light')
pump()
qss_ok = 'og="indtoggle"' in page.styleSheet() and '#D0D4D9' in page.styleSheet()
theme_mod.apply_theme('dark')
pump()
check('theme 切換 → 頁面 QSS 帶 indtoggle 規則跟 palette + chart 正常 redraw',
      qss_ok and len(chart2.canvas.figure.axes) == 3)

# ── 使用說明備注：呢頁嘅頁級 QSS 由 gui_kline 源碼重建（內含裸 `QLabel {}` 規則）
#    → role 樣式必須喺真頁面上實測生效，唔可以靠推斷 ──
from gateway.i18n import t as _t   # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402
page.retranslate('zh_hk')
pump()
check('K線頁使用說明三語齊全、無空白',
      all(_t('kline_page_note', lang).strip() for lang in ('zh_hk', 'zh_cn', 'en')))
check('K線頁使用說明已套用文案並帶 role=pagebody',
      page.kline_page_note.text() == _t('kline_page_note', 'zh_hk')
      and page.kline_page_note.property('role') == 'pagebody')
page.kline_page_note.ensurePolished()
got_muted = page.kline_page_note.palette().color(QPalette.WindowText).name().upper()
want_muted = theme_mod.THEMES[theme_mod.CURRENT]['muted'].upper()
check(f'頁級 QSS 有裸 QLabel 規則之下，role=pagebody 仍解析為淡色 {want_muted}（層疊實測，實測 {got_muted}）',
      got_muted == want_muted)
page.retranslate('en')
pump()
en_note = page.kline_page_note.text()
page.retranslate('zh_cn')
pump()
cn_note = page.kline_page_note.text()
check('切 EN / zh_cn：使用說明照跟語言（唔係寫死母語）',
      en_note == _t('kline_page_note', 'en') and cn_note == _t('kline_page_note', 'zh_cn'))
page.retranslate('zh_hk')
pump()
page._on_app_quit()
pump()

# ══ Part 7：管理頁 CRUD（經 widget）═══════════════════════════════════════
print('── Part 7: 指標管理頁 ──')
state_store.STATE_PATH = Path(_TMPDIR) / 'ui_state4.json'
ind.reset_manager_for_test()
from PySide6.QtCore import QMargins, Qt  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402
from gateway.pages import indicators_page as ipage_mod  # noqa: E402
from gateway.pages.indicators_page import IndicatorsPage  # noqa: E402

ipage = IndicatorsPage()
ipage.show()
pump()

# ── Part 7b：`.ui` 骨架（排版喺 gateway/ui/indicators_page.ui；控件數量屬資料 → 填進 slot）──
lay = ipage.layout()
check('`.ui` root objectName + Designer margin/spacing 照載入',
      ipage.objectName() == 'indicators_page' and lay is not None
      and lay.contentsMargins() == QMargins(10, 8, 10, 8) and lay.spacing() == 6)
check('靜態控件全部由 `.ui` 建出（objectName 即身份契約）',
      all(getattr(ipage, n, None) is not None for n in
          ('ind_def_combo', 'ind_pos_combo', 'ind_add_btn', 'ind_save_btn', 'ind_remove_btn',
           'ind_detail_toggle', 'ind_detail_panel', 'ind_detail_desc', 'ind_detail_usage',
           'ind_detail_head', 'ind_table', 'ind_counts', 'ind_status',
           'paramSlot', 'detailNoteSlot')))
check('og / WA_StyledBackground 由 _STAMP 補返（Designer 帶唔住 dynamic property）',
      ipage.ind_add_btn.property('og') == 'indbtn'
      and ipage.ind_detail_panel.testAttribute(Qt.WA_StyledBackground))
_d0 = ind.INDICATOR_DEFS[ipage._def_keys[ipage.ind_def_combo.currentIndex()]]
check('參數列按 INDICATOR_DEFS 生成並填進 paramSlot（加指標唔使改 `.ui`）',
      ipage.paramSlot.count() == 2 * len(_d0.params) + 1
      and all(ipage.findChild(type(ipage._param_spins[p.key]), 'ind_param_%s' % p.key) is not None
              for p in _d0.params))
check('詳情解釋逐條填進 detailNoteSlot（objectName ind_detail_note_<key>）',
      ipage.detailNoteSlot.count() == len(_d0.params)
      and all(ipage.findChild(QLabel, 'ind_detail_note_%s' % p.key) is not None for p in _d0.params))
check('頁面 QSS 有根（objectName → QSS cascade）', 'QWidget#indicators_page' in ipage.styleSheet())

check('表格 = seed 2 行 + 三語表頭', ipage.model.rowCount() == 2
      and ipage.model.headerData(1, Qt.Horizontal) == '指標')
ipage.ind_def_combo.setCurrentIndex(ipage._def_keys.index('macd'))
pump()
check('揀 MACD → 位置 combo 得准入位置(sub)', ipage.ind_pos_combo.currentData() == 'sub')
ipage._param_spins['fast'].setValue(5)
ipage.ind_add_btn.click()
pump()
check('新增 → 3 行 + status', ipage.model.rowCount() == 3 and '已新增' in ipage.ind_status.text())
macd_id = next(e['id'] for e in ind.get_manager().items() if e['def'] == 'macd')
ipage.ind_table.selectRow(2)
pump()
check('揀行 → 編輯模式填入', ipage._sel_id == macd_id and ipage._param_spins['fast'].value() == 5)
ipage._param_spins['fast'].setValue(8)
ipage.ind_save_btn.click()
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
ipage.ind_table.selectRow(2)
pump()
ipage.ind_remove_btn.click()
pump()
check('移除所選 → 2 行', ipage.model.rowCount() == 2 and ind.get_manager().get(macd_id) is None)
ipage.retranslate('en')
pump()
check('管理頁 retranslate EN', 'Add indicator' in ipage.ind_add_btn.text()
      and ipage.model.headerData(1, Qt.Horizontal) == 'Indicator')

# ── 使用說明備注：文案屬 i18n、樣式屬 theme（role）。缺 role → QSS 無聲失效，用戶睇唔到 ──
IND_NOTES = {'ind_page_note': ('ind_page_note', 'pagebody'),
             'ind_table_note': ('ind_table_note', 'usagehint')}
check(f'指標頁使用說明（{len(IND_NOTES)} 條）三語齊全、無空白',
      all(_t(k, lang).strip() for _w, (k, _r) in IND_NOTES.items()
          for lang in ('zh_hk', 'zh_cn', 'en')))
check('指標頁備注已套用文案並帶 role（無 role → 淡色提示睇唔到）',
      all(getattr(ipage, w).text() == _t(k, 'en')
          and getattr(ipage, w).property('role') == r
          for w, (k, r) in IND_NOTES.items()))
ipage.retranslate('zh_cn')
pump()
check('切 zh_cn：使用說明轉简体',
      all(getattr(ipage, w).text() == _t(k, 'zh_cn') for w, (k, _r) in IND_NOTES.items()))
ipage.retranslate('zh_hk')
pump()
check('切 zh_hk：使用說明跟返繁體',
      all(getattr(ipage, w).text() == _t(k, 'zh_hk') for w, (k, _r) in IND_NOTES.items()))

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
def exp_boxes(top, bot):
    """獨立實作（唔 reuse `_zone_boxes`）：mask 連續段內再按 level 變化切段 → 方塊數。
    呢個先係 `_plot_zones` 嘅契約：每個方塊一個 fill（重疊區塊各自獨立，唔係一大片）。"""
    cnt, i, n = 0, 0, top.shape[0]
    while i < n:
        if np.isnan(top[i]) or np.isnan(bot[i]):
            i += 1
            continue
        cnt += 1
        j = i + 1
        while j < n and not (np.isnan(top[j]) or np.isnan(bot[j])) \
                and top[j] == top[i] and bot[j] == bot[i]:
            j += 1
        i = j
    return cnt


k9 = len(rows) - chart9._s
xl0, xl1 = chart9.ax.get_xlim()
i0, i1 = max(0, int(np.floor(xl0))), min(k9, int(np.ceil(xl1)) + 1)
_per_dir = [exp_boxes(chart9._ind_cache['full'][e['id']][tk][chart9._s + i0:chart9._s + i1],
                      chart9._ind_cache['full'][e['id']][bk][chart9._s + i0:chart9._s + i1])
            for e in mgr9.items() if e['def'] in ('ob', 'fvg', 'vob')
            for tk, bk in (('bull_top', 'bull_bottom'), ('bear_top', 'bear_bottom'))]
exp, exp_dirs = sum(_per_dir), sum(1 for n in _per_dir if n)   # 🤖 #23：同方向合併成一個 PathCollection
from matplotlib.path import Path as _MPath  # noqa: E402  🤖 #23：方塊 = compound Path 入面嘅 sub-path（CLOSEPOLY 計）
def _n_rects(cols):
    return sum(p.codes.tolist().count(_MPath.CLOSEPOLY)
               for c in cols for p in c.get_paths())


def _quads(p):
    """compound Path → 每個 sub-path 嘅頂點清單。繪畫契約：5 點、尾點 == 起點 = **閉合四邊形**
    （CLOSEPOLY 唔會用自己嗰個頂點 → 4 點會變三角形，用戶抓到 OB 變楔形）。"""
    subs, cur = [], []
    for v, c in zip(p.vertices, p.codes):
        if c == _MPath.MOVETO:
            if cur:
                subs.append(cur)
            cur = [v]
        else:
            cur.append(v)
    if cur:
        subs.append(cur)
    return subs


def _all_quads(cols):
    q = [s for c in cols for p in c.get_paths() for s in _quads(p)]
    return q, all(len(s) == 5 and np.allclose(s[0], s[-1])
                  and len({tuple(v) for v in s[:-1]}) == 4 for s in q)
check(f'add ob/fvg/vob → 主圖多 {exp_dirs} 個 PathCollection（同方向合併）+ 方塊 sub-path 總數 == {exp}'
      f'（逐個獨立，繪畫契約冇變）+ axes 唔變（main 唔砌 panel）',
      all(ok_add) and exp >= 3 and len(chart9.ax.collections) == col0 + exp_dirs
      and _n_rects(chart9.ax.collections[col0:]) == exp
      and len(chart9.canvas.figure.axes) == axes0)
paths = chart9.ax.collections[-1].get_paths()
check('區塊 path 非空（真有區塊，唔係空 collection）', len(paths) > 0 and len(paths[0].vertices) >= 3)

# 用戶反映 VOB「連續、唔係獨立方塊」：同向重疊區塊喺陣列入面係較新者覆蓋（level 中途跳），
# 繪畫必須喺 level 變化位切段 → 兩個獨立方塊（每塊一個 fill_between，各自 ±0.5 覆蓋自己嗰啲根）
from matplotlib.figure import Figure  # noqa: E402
_t2 = np.array([10.0, 10.0, 10.0, 11.0, 11.0, np.nan])
_b2 = np.array([9.0, 9.0, 9.0, 10.2, 10.2, np.nan])
_axp = Figure().add_subplot(111)
ind._plot_zones(_axp, np.arange(6.0, dtype=float),
                {'bull_top': _t2, 'bull_bottom': _b2,
                 'bear_top': np.full(6, np.nan), 'bear_bottom': np.full(6, np.nan)})
_p2 = [p for c in _axp.collections for p in c.get_paths()]
_xs = sorted({float(v) for p in _p2 for v in p.vertices[:, 0]})
check('同向重疊區塊 → 切成 2 個獨立方塊（各自 ±0.5 覆蓋自己嗰啲根，唔係一大片）',
      _n_rects(_axp.collections) == 2 and all(len(p.vertices) >= 10 for p in _p2)
      and _xs == [-0.5, 2.5, 4.5])
_q2, _ok_q2 = _all_quads(_axp.collections)
check('方塊真係閉合四邊形（4 個唔同角 + 尾點返起點），唔係三角形', len(_q2) == 2 and _ok_q2)
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
labels = {ipage9.ind_def_combo.itemText(i) for i in range(ipage9.ind_def_combo.count())}
check('管理頁 def combo 見到 OB / FVG / VOB', {'OB', 'FVG', 'VOB'} <= labels)
ipage9.ind_def_combo.setCurrentIndex(ipage9._def_keys.index('vob'))
pump()
check('揀 VOB → 位置只准 main + 參數欄砌到 8 個（含 sweep / max_size / pen / supersede）',
      ipage9.ind_pos_combo.count() == 1 and ipage9.ind_pos_combo.currentData() == 'main'
      and set(ipage9._param_spins) == {'period', 'strength', 'confirm', 'sweep',
                                       'max_size', 'pen', 'supersede', 'max_zones'})
ipage9._param_spins['confirm'].setValue(5)
ipage9.ind_add_btn.click()
pump()
_CI = {c: i for i, c in enumerate(ipage_mod.COLUMNS)}   # 🤖 永遠按欄名搵欄，唔 hardcode index
check('新增 VOB → 表格 3 行 + 參數摘要 14/1/5/5/3/50/1/15（含 max_size/pen/supersede 預設）',
      ipage9.model.rowCount() == 3
      and ipage9.model.data(ipage9.model.index(2, _CI['name'])) == 'VOB'
      and ipage9.model.data(ipage9.model.index(2, _CI['params'])) == '14/1/5/5/3/50/1/15')
ipage9.retranslate('en')
pump()
check('ICT 參數名三語（label + i18n）',
      _t('ind_p_confirm', 'en') == 'Confirm bars' and 'Confirm bars' in ipage9._param_lbls['confirm'].text())

# #28：MA 逐條線 CHECKBOX（is_bool → 管理頁出 checkbox；摘要 skip bool）
ipage9.ind_def_combo.setCurrentIndex(ipage9._def_keys.index('ma'))
pump()
check('#28 揀 MA → 參數欄 p1..4 spin + show1..4 CHECKBOX（預設全剔）',
      set(ipage9._param_spins) == {'p1', 'p2', 'p3', 'p4', 'show1', 'show2', 'show3', 'show4'}
      and all(isinstance(ipage9._param_spins['show%d' % n], QCheckBox)
              and ipage9._param_spins['show%d' % n].isChecked() for n in (1, 2, 3, 4)))
ipage9._param_spins['show2'].setChecked(False)
ipage9.ind_add_btn.click()
pump()
ma9 = next(e for e in ipage9._mgr().items() if e['def'] == 'ma')
check('#28 新增 MA（MA2 唔剔）→ show2=0 入 manager、其他照 1',
      ipage9.model.rowCount() == 4 and ma9['params']['show2'] == 0 and ma9['params']['show1'] == 1)
check('#28 參數摘要 skip bool：照舊「5/10/20/60」',
      ipage9.model.data(ipage9.model.index(3, _CI['params'])) == '5/10/20/60')
ipage9.ind_table.selectRow(3)
pump()
check('#28 揀行 → CHECKBOX 反映返存嘅狀態（show2 冇剔）',
      ipage9._sel_id == ma9['id'] and not ipage9._param_spins['show2'].isChecked()
      and ipage9._param_spins['show1'].isChecked())
ipage9._param_spins['show2'].setChecked(True)
ipage9.ind_save_btn.click()
pump()
check('#28 剔返再套用 → params.show2=1', ipage9._mgr().get(ma9['id'])['params']['show2'] == 1)

# ══ Part 10：ICT 全套繪畫 + 一行描寫 / 可摺疊詳情（ticket #21）═════════════
print('\n── Part 10: ICT 全套繪畫 + 說明欄 / 可摺疊詳情 ──')
NEW = ('brk', 'bpr', 'bos', 'choch', 'liq', 'eqhl', 'pd', 'ote')


def exp_artists(def_key, full, inst_id, s, i0, i1):
    """按 _PLOTTERS 嘅契約算期望 artists 增量（collections, lines, texts）—
    用嚟斷言「真係砌出嘢」，唔係得個空 collection。"""
    sl = {k: v[s + i0:s + i1] for k, v in full[inst_id].items()}
    c = ln = tx = 0
    if def_key in ('ob', 'fvg', 'vob', 'brk', 'bpr', 'eqhl', 'ote'):
        for tk, bk in (('bull_top', 'bull_bottom'), ('bear_top', 'bear_bottom')):
            c += 1 if exp_boxes(sl[tk], sl[bk]) else 0   # 🤖 #23：同方向一個 PathCollection（方塊 = sub-path）
    elif def_key in ('bos', 'choch', 'liq'):
        for pk, mk in (('bull_top', 'mark_bull'), ('bear_top', 'mark_bear')):
            if (~np.isnan(sl[pk])).any():
                c += 1                                   # hlines → LineCollection
                mm = ~np.isnan(sl[mk])
                if mm.any():
                    ln += 1                              # 三角標記 → 一條 line
                    if def_key in ('bos', 'choch'):
                        tx += int(mm.sum())              # tag 文字
    elif def_key == 'pd':
        if (~np.isnan(sl['range_hi']) & ~np.isnan(sl['range_lo'])).any():
            c, ln = c + 1, ln + 3                        # 區間填充 + hi/lo/均衡 三條線
    return c, ln, tx


state_store.STATE_PATH = Path(_TMPDIR) / 'ui_state7.json'
ind.reset_manager_for_test()
mgr10 = ind.get_manager()
chart10 = ind.IndicatorKlineChart()
chart10.set_indicator_manager(mgr10)
chart10.set_bars(rows)
chart10._redraw()
for e in mgr10.items():                       # 🤖 MAX_ITEMS=6 → 逐個加逐個移除，基準永遠係清空後嘅狀態
    mgr10.remove(e['id'], origin='t')
chart10._redraw()
axes0 = len(chart10.canvas.figure.axes)       # 清晒 seed（連 sub panel 都冇）先取基準
base = (len(chart10.ax.collections), len(chart10.ax.lines), len(chart10.ax.texts))
bad10, drew = [], []
for k in NEW:
    ok_add, _msg, item = mgr10.add(k, 'main', {p.key: p.default for p in ind.INDICATOR_DEFS[k].params},
                                   origin='t')
    chart10._redraw()
    if not ok_add:
        bad10.append(k + ':add')
        continue
    got = (len(chart10.ax.collections), len(chart10.ax.lines), len(chart10.ax.texts))
    kk = len(rows) - chart10._s
    xl0, xl1 = chart10.ax.get_xlim()
    i0, i1 = max(0, int(np.floor(xl0))), min(kk, int(np.ceil(xl1)) + 1)
    exp = exp_artists(k, chart10._ind_cache['full'], item['id'], chart10._s, i0, i1)
    if got != tuple(b + e for b, e in zip(base, exp)):
        bad10.append('%s:畫咗 %s ≠ 期望 %s' % (k, got, exp))
    if sum(exp) == 0:
        bad10.append(k + ':冇嘢畫')
    drew.append('%s=%s' % (k, exp))
    mgr10.remove(item['id'], origin='t')
check('8 個新 ICT 逐個加入 → 主圖 artists 增量 == 按契約算出嘅期望（hlines/三角/tag/帶）', not bad10)
if bad10:
    print('     ⚠️ ' + '; '.join(bad10))
print('     ℹ️ ' + ' '.join(drew))
check('全部喺主圖疊加 → 唔砌新 panel（axes 數量唔變）', len(chart10.canvas.figure.axes) == axes0)
check('水平位真係畫到圖上（LineCollection path 非空）',
      any(len(c.get_paths()) > 0 for c in chart10.ax.collections if hasattr(c, 'get_paths')))
check('BOS/CHoCH tag 文字出現喺 ax.texts', len(chart10.ax.texts) > 0)

# 管理頁：說明欄 + 可摺疊詳情
state_store.STATE_PATH = Path(_TMPDIR) / 'ui_state8.json'
ind.reset_manager_for_test()
ip10 = IndicatorsPage()
ip10.show()
pump()
check('表格多咗「說明」欄（表頭三語 + 欄數 = COLUMNS）',
      ip10.model.columnCount() == len(ipage_mod.COLUMNS)
      and ip10.model.headerData(ipage_mod.COLUMNS.index('desc'), Qt.Horizontal) == '說明')
ip10.ind_def_combo.setCurrentIndex(ip10._def_keys.index('bos'))
pump()
ip10.ind_add_btn.click()
pump()
di = ipage_mod.COLUMNS.index('desc')
row_b = next(i for i in range(ip10.model.rowCount())
             if ip10.model.data(ip10.model.index(i, _CI['name'])) == 'BOS')   # seed 行喺前面 → 按名搵行
check('新增 BOS → 說明欄有一行描寫（跟語言）+ tooltip 帶完整用法',
      ip10.model.data(ip10.model.index(row_b, di)) == _t('ind_desc_bos', 'zh_hk')
      and _t('ind_use_bos', 'zh_hk') in (ip10.model.data(ip10.model.index(row_b, di), Qt.ToolTipRole) or ''))
check('詳情面板預設收起', not ip10.ind_detail_panel.isVisible() and '▸' in ip10.ind_detail_toggle.text())
ip10.ind_detail_toggle.click()
pump()
check('展開 → 顯示完整用法 + 每個參數一行解釋（ind_detail_note_<key>）',
      ip10.ind_detail_panel.isVisible()
      and _t('ind_use_bos', 'zh_hk') in ip10.ind_detail_usage.text()
      and set(ip10._detail_notes) == {'swing', 'max_levels'}
      and ip10._detail_notes['swing'].text() == '· %s：%s' % (_t('ind_p_swing', 'zh_hk'),
                                                              _t('ind_n_swing', 'zh_hk')))
ip10.ind_detail_toggle.click()
pump()
check('再撳 → 收起（只返返一行掣）', not ip10.ind_detail_panel.isVisible() and '▸' in ip10.ind_detail_toggle.text())
ip10.ind_def_combo.setCurrentIndex(ip10._def_keys.index('ote'))
pump()
check('轉類型 → 詳情即時跟（OTE 帶 fib_lo/fib_hi 解釋）',
      set(ip10._detail_notes) == {'swing', 'fib_lo', 'fib_hi', 'max_zones'}
      and _t('ind_desc_ote', 'zh_hk') in ip10.ind_detail_desc.text())
ip10.ind_detail_toggle.click()
pump()
ip10.retranslate('en')
pump()
check('三語：表頭/說明欄跟語言 + 詳情跟頂欄類型（而家 = OTE）嘅英文用法/參數解釋',
      ip10.model.headerData(di, Qt.Horizontal) == 'Description'
      and ip10.model.data(ip10.model.index(row_b, di)) == _t('ind_desc_bos', 'en')
      and _t('ind_use_ote', 'en') in ip10.ind_detail_usage.text()
      and _t('ind_n_swing', 'en') in ip10._detail_notes['swing'].text()
      # 詳情標題係純固定文案 → 由 `_TEXT` 表 cover（`_rebuild_detail` 已冇手寫 setText）
      and ip10.ind_detail_head.text() == _t('ind_detail_params', 'en'))
check('K線頁開關掣 tooltip 帶一行描寫（三語）',
      _t('ind_desc_ob', 'en') in KlinePage._ind_tooltip(ind.INDICATOR_DEFS['ob'], 'main', 'en')
      and _t('ind_pos_main', 'zh_cn') in KlinePage._ind_tooltip(ind.INDICATOR_DEFS['ob'], 'main', 'zh_cn'))

# ══ Part 11：主圖 Y-fit 距離閘（用戶：「有啲 VOB 獨立出嚟，同 K 線冇連接同關係」）════
print('\n── Part 11: 主圖 Y-fit 距離閘（離價好遠嘅區塊唔撐大 Y 軸）──')
kk11 = len(rows) - chart10._s
xl0, xl1 = chart10.ax.get_xlim()
j0, j1 = max(0, int(np.floor(xl0))), min(kk11, int(np.ceil(xl1)) + 1)
vis = rows[chart10._s + j0:chart10._s + j1]
cl_v, ch_v = min(r[3] for r in vis), max(r[2] for r in vis)
FAR_LVL = ch_v + 10.0 * (ch_v - cl_v)          # 遠到離譜：Y 軸唔應該為佢擴展
NEAR_LVL = ch_v + 0.1 * (ch_v - cl_v)          # 喺可見範圍附近（< FIT_PAD）：照樣要 fit 到


def _flat_zone(lvl):
    """临时 def：整條可見窗都有一個恒定價位嘅「區塊」（模擬一個永遠冇被消耗嘅歷史 OB）。"""
    def _c(ohlc, params):
        n = len(ohlc['c'])
        return {'bull_top': np.full(n, lvl), 'bull_bottom': np.full(n, lvl - 0.2),
                'bear_top': np.full(n, np.nan), 'bear_bottom': np.full(n, np.nan)}
    return _c


for _k, _lv in (('farzone', FAR_LVL), ('nearzone', NEAR_LVL)):
    ind.INDICATOR_DEFS[_k] = ind.IndicatorDef(_k, _k.upper(), ('main',), (), _flat_zone(_lv), 0)
    ind._PLOTTERS[_k] = ind._plot_zones
_old_far = ind.FAR_OVERLAYS
ind.FAR_OVERLAYS = frozenset(_old_far | {'farzone', 'nearzone'})   # 🤖 臨時 def 都要入閘，先至模擬到真 ICT 疊加
chart10._redraw()
base_y = tuple(chart10.ax.get_ylim())
_cols0 = len(chart10.ax.collections)   # 🤖 #22 之後蠟燭本身都係 collection → 必須用增量，唔准淨係睇 >0
ok_f, _m, it_f = mgr10.add('farzone', 'main', {}, origin='t')
chart10._redraw()
y_far = tuple(chart10.ax.get_ylim())
check('遠距離區塊（%0.1f，離可見 K 線 10 個範圍）照樣畫出方塊（唔係唔畫）' % FAR_LVL,
      ok_f and len(chart10.ax.collections) == _cols0 + 1)   # 恒定 level + 成窗連續 → 恰好 1 個方塊
check('…但主圖 Y 軸範圍逐個位唔變（唔撐大、唔擠細 K 線）', y_far == base_y)
ok_n, _m, it_n = mgr10.add('nearzone', 'main', {}, origin='t')
chart10._redraw()
check('近距離區塊（可見範圍 +10%，喺 FIT_PAD 內）仍然參與 Y-fit',
      ok_n and chart10.ax.get_ylim()[1] >= NEAR_LVL)
mgr10.remove(it_n['id'], origin='t')
ind.FAR_OVERLAYS = frozenset()                 # 對比：閘關咗 → 同一個 farzone 就撐大 Y 軸
chart10._redraw()
check('閘關咗（FAR_OVERLAYS 空）→ 同樣嘅 farzone 確實會撐大 Y 軸（證明係距離閘做功，唔係數據冇變化）',
      chart10.ax.get_ylim()[1] >= FAR_LVL)
ind.FAR_OVERLAYS = _old_far
mgr10.remove(it_f['id'], origin='t')
for _k in ('farzone', 'nearzone'):
    ind.INDICATOR_DEFS.pop(_k, None)
    ind._PLOTTERS.pop(_k, None)
chart10._redraw()
check('移除臨時 def 之後 Y 軸還原（冇殘留）', tuple(chart10.ax.get_ylim()) == base_y)

# ══ Part 12：兩層繪畫（#22 縮放/平移順暢）════════════════════════════════════
print('\n── Part 12: 兩層繪畫（pan·zoom 唔重建 artist · hover 唔 full redraw）──')
state_store.STATE_PATH = Path(_TMPDIR) / 'ui_state12.json'
ind.reset_manager_for_test()
mgr12 = ind.get_manager()
ch12 = ind.IndicatorKlineChart()
ch12.set_indicator_manager(mgr12)
ch12.set_bars(rows)
mgr12.add('ob', 'main', {}, origin='t')
ch12._redraw()
ids0 = [id(a) for a in ch12._arts]          # 靜態層（全部 bar = 3 個 collection + 現價線/tag/crosshair）
xl0, yl0 = ch12.ax.get_xlim(), ch12.ax.get_ylim()
ch12._on_scroll(types.SimpleNamespace(inaxes=ch12.ax, xdata=200.0, step=1, button=1, ydata=5.0))
ch12._on_press(types.SimpleNamespace(inaxes=ch12.ax, xdata=200.0, button=1, ydata=5.0))
ch12._on_motion(types.SimpleNamespace(inaxes=ch12.ax, xdata=150.0, button=1, ydata=5.0))
ch12._on_release(types.SimpleNamespace(inaxes=ch12.ax, xdata=150.0, button=1, ydata=5.0))
xl1, yl1 = ch12.ax.get_xlim(), ch12.ax.get_ylim()
_v0, _v1 = max(0, int(np.ceil(xl1[0]))), min(len(rows), int(np.floor(xl1[1])) + 1)
lo_v = min(r[3] for r in rows[_v0:_v1])
hi_v = max(r[2] for r in rows[_v0:_v1])
check('pan+zoom：xlim 郁咗 + Y fit 跟住可見 slice（包住可見高低、同之前唔同），但靜態層 artist 逐個同一個物件',
      xl1 != xl0 and yl1 != yl0 and yl1[0] <= lo_v and yl1[1] >= hi_v
      and [id(a) for a in ch12._arts] == ids0 and not ch12._static_dirty)
_xs = [float(v) for a in ch12._ind_artists if hasattr(a, 'get_paths')
       for p in a.get_paths() for v in p.vertices[:, 0]]
check('pan+zoom 都要重畫指標疊加：新嘅指標 artist 全部喺新可見窗內（可見 slice 跟住視窗）',
      len(_xs) > 0 and xl1[0] - 1.0 <= min(_xs) and max(_xs) <= xl1[1] + 1.0)
_stable = [len(ch12._ind_artists)]
for _ in range(5):
    ch12._redraw()
check('連續 6 幀：主圖指標 artist 數量恒定（parent 唔再 ax.clear() → 自己追蹤清除有效）',
      len(set(_stable + [len(ch12._ind_artists)])) == 1)

_nr = []
_orig12 = ch12._redraw
ch12._redraw = lambda: (_nr.append(1), _orig12())[1]
_ev = types.SimpleNamespace(inaxes=ch12.ax, xdata=150.0, button=1, ydata=5.0)
ch12._on_motion(_ev)
_rd0 = ch12.readout.text()
_ev.xdata = 160.0
ch12._on_motion(_ev)
check('hover：crosshair set_xdata 跟到 + readout 更新，但一次 _redraw 都冇叫（#22 B）',
      ch12._cross.get_visible() and list(ch12._cross.get_xdata()) == [160, 160]
      and _nr == [] and ch12.readout.text() != _rd0)
ok_m, _m12, it_m = mgr12.add('macd', 'sub', {}, origin='t')
ch12._redraw()
_ev.inaxes, _ev.xdata = ch12.ax, 170.0   # 🤖 砌 panel 會 fig.clear() 重建 axes → 舊 event 嘅 inaxes 已經係廢 object
ch12._on_motion(_ev)
check('指標 panel 嘅 crosshair 一齊跟住 hover（郁滑鼠唔返嚟重砌 panel）',
      ok_m and any(ln.get_visible() and list(ln.get_xdata()) == [170, 170]
                   for ln in ch12._panel_cross))
ch12._on_leave(types.SimpleNamespace())
check('離開畫布 → crosshair 收埋（主圖 + panel）',
      not ch12._cross.get_visible() and all(not ln.get_visible() for ln in ch12._panel_cross))
ch12._redraw = _orig12

_old_up = gk.C_UP
gk.C_UP = '#FF0000'                # theme recipe（kline_page / quotes_page 都係改 gk.C_* + _redraw）
ch12._redraw()
_ids_th = [id(a) for a in ch12._arts]
gk.C_UP = _old_up
ch12._redraw()
check('theme 換色 → 靜態層識得自己重建（配色砌進 collection，唔能靠改 limits）',
      _ids_th != ids0 and [id(a) for a in ch12._arts] != _ids_th)
ch12.set_bars(rows[:200])
_n_verts = sum(len(p.vertices) for c in ch12.ax.collections[:2] for p in c.get_paths())   # 影線 2 + 實體 5 = 7/根
_qb, _ok_qb = _all_quads(ch12.ax.collections[1:2])          # 實體 collection（影線係兩點段，唔入呢項）
check('set_bars 換數據 → 靜態層重砌，而且真係畫晒全部 200 根（compound path 頂點 = 7/根）',
      _n_verts == 7 * 200 and ch12._static_dirty is False)
check('蠟燭實體都係閉合四邊形（唔係三角形 — 同一個 CLOSEPOLY 陷阱）', len(_qb) == 200 and _ok_qb)

# ══ Part 13：過渡動畫（#23 eased 縮放/復位 + 松手慣性）═════════════════════════
print('\n── Part 13: 過渡動畫（eased 縮放/復位 + 松手慣性）──')
from PySide6.QtTest import QTest  # noqa: E402
ch13 = gk.KlineChart()
ch13.set_bars(rows)
ch13._redraw()
ids13 = [id(a) for a in ch13._arts]
ch13._on_scroll(types.SimpleNamespace(inaxes=ch13.ax, xdata=200.0, step=1, button=1, ydata=5.0))
start13, tgt = ch13._anim[1], ch13._anim[2]
# 🤖 插值用「假時鐘」直接 drive `_anim_tick`：呢個 e2e 進程好重（一次 pump() 實時 ~200 ms），
#    靠真 QTimer 取樣一定錯身以為已經行完 → 時序契約要控制到時鐘先至測得準。
w_s, w_t = start13[1] - start13[0], tgt[1] - tgt[0]
pr = []
for f in (0.25, 0.5, 0.75, 1.0):
    ch13._anim = (time.perf_counter() - f * ch13.ANIM_MS / 1000.0, start13, tgt, False)
    ch13._anim_running = True
    ch13._anim_tick()
    pr.append((w_s - (ch13._view[1] - ch13._view[0])) / (w_s - w_t))   # 歸一化進度
check('滾輪縮放 → ease-out 插值：進度嚴格單調、每段都比線性快（ease-out）、終點逐個位 == target、終止後 _anim 清空',
      all(pr[i] < pr[i + 1] for i in range(3)) and all(p > f for p, f in zip(pr, (0.25, 0.5, 0.75)))
      and pr[3] == 1.0 and ch13._view == tgt and ch13._anim is None)
settle()
fin = ch13.ax.get_xlim()
check('過渡終點真係上到屏：xlim == target 逐個位（#23 修復：出界 x 刻度會撐開 limits ±1 根）+ 期間靜態層冇重建',
      abs(fin[0] - tgt[0]) < 1e-9 and abs(fin[1] - tgt[1]) < 1e-9
      and [id(a) for a in ch13._arts] == ids13)
ch13._on_scroll(types.SimpleNamespace(inaxes=ch13.ax, xdata=150.0, step=-1, button=1, ydata=5.0))
QTest.qWait(60)
pump()
mid = ch13._view
ch13._on_press(types.SimpleNamespace(inaxes=ch13.ax, xdata=150.0, button=1, ydata=5.0))
check('郁手（press）即刻取消過渡 — 跟手優先，唔會同動畫搶',
      ch13._anim is None and ch13._view == mid and ch13._drag is not None)
ch13._on_release(types.SimpleNamespace(inaxes=ch13.ax, xdata=150.0, button=1, ydata=5.0))
# 由跟隨模式一掣縮到「全部數據」：起點 xmax > n-0.5（tag gutter）→ 以前 `_apply_view` 會逐幀還原返跟隨
start_full = ch13._view
ch13._on_scroll(types.SimpleNamespace(inaxes=ch13.ax, xdata=200.0, step=-5, button=1, ydata=5.0))
tgt_full = ch13._anim[2]
ch13._anim = (time.perf_counter() - 0.25 * ch13.ANIM_MS / 1000.0,) + ch13._anim[1:]   # 🤖 假時鐘：呢進程一次 event loop 可以 >220ms，靠真 QTimer 取樣會錯身
ch13._anim_running = True
ch13._anim_tick()
mid_full = ch13._view
settle()
full = ch13.ax.get_xlim()
check('由跟隨模式縮到「全部數據」→ 過渡真係郁（中途有中間態、終點 == 全部數據），唔會俾 out-of-range guard 打返落跟隨',
      start_full is not None and mid_full is not None
      and start_full[0] > mid_full[0] > tgt_full[0]
      # 🤖 位移喺 **xmin 軸**量（呢度距離 = 成幾百根）：xmax 嗰邊得幾個位，前面 press 取消會留低喺
      #    動畫任意一點 → 用 xmax 判閾值一定 flaky。
      and start_full[0] - mid_full[0] > 1.0
      and abs(full[0] - tgt_full[0]) < 1e-9 and abs(full[1] - tgt_full[1]) < 1e-9)
ch13._on_scroll(types.SimpleNamespace(inaxes=ch13.ax, xdata=300.0, step=2, button=1, ydata=5.0))
settle()                                   # 縮細 + 離開左緣 → 先至分辯到滑行方向（唔係一開波就 clamp）
ch13._on_press(types.SimpleNamespace(inaxes=ch13.ax, xdata=300.0, button=1, ydata=5.0))
for _xx in (270.0, 240.0, 210.0):          # 快速向左拖（樣本之間要短：既要有速度，又唔好超出 120ms 取樣窗；🤖 唔好 pump）
    ch13._on_motion(types.SimpleNamespace(inaxes=ch13.ax, xdata=_xx, button=1, ydata=5.0))
    QTest.qWait(5)
before = ch13._view
ch13._on_release(types.SimpleNamespace(inaxes=ch13.ax, xdata=210.0, button=1, ydata=5.0))
tgt2 = ch13._anim[2] if ch13._anim else None
settle()
after = ch13._view
check('拖動松手 → 慣性滑行：起過渡、目標 clamp 喺數據範圍內、終點逐個位 == 目標、方向跟住拖動',
      tgt2 is not None and after is not None and after[0] < before[0]
      and tgt2[0] >= -0.5 - 1e-9 and tgt2[1] <= len(rows) - 0.5 + 1e-9
      and abs(after[0] - tgt2[0]) < 1e-9 and abs(after[1] - tgt2[1]) < 1e-9)
slow = []
ch13._on_press(types.SimpleNamespace(inaxes=ch13.ax, xdata=100.0, button=1, ydata=5.0))
for _ in range(3):                       # 慢慢拖（每次隔 60ms）→ 速度 ≈ 0 → 唔應該滑
    ch13._on_motion(types.SimpleNamespace(inaxes=ch13.ax, xdata=99.0, button=1, ydata=5.0))
    QTest.qWait(60)
    pump()
ch13._on_release(types.SimpleNamespace(inaxes=ch13.ax, xdata=99.0, button=1, ydata=5.0))
check('慢慢拖再松手 → 唔會滑（速度自然接近 0，唔會亂郁）', ch13._anim is None)

# ══ Part 14：平滑平移（逐幀追近）+ 拖動凍結 Y 尺度（#24）═══════════════════════
print('\n── Part 14: 平滑平移 + 拖動凍結 Y 尺度 ──')
import math  # noqa: E402


def _yfit(ch, view):
    """`_apply_view` 應該 fit 出嘅 (價格 ylim, 成交量 ylim) — 照返同一個公式（可見 slice + 6% pad）。"""
    s = max(0, int(math.floor(view[0])))
    e = min(len(ch._rows), int(math.ceil(view[1])) + 1)
    l, h, v = (ch._ohlcv[k] for k in 'lhv')
    lo, hi = float(l[s:e].min()), float(h[s:e].max())
    pad = (hi - lo) * 0.06 or abs(hi) * 0.01 or 1.0
    return (lo - pad, hi + pad), (0.0, (float(v[s:e].max()) or 1.0) * 1.15)


def _eqy(ylim, want):
    return abs(ylim[0] - want[0]) < 1e-9 and abs(ylim[1] - want[1]) < 1e-9


def _ev14(x, step=0):
    return types.SimpleNamespace(inaxes=ch14.ax, xdata=x, button=1, ydata=5.0, step=step)


ch14 = gk.KlineChart()
ch14.set_bars(rows)
ch14._view = (100.0, 300.0)
ch14._redraw()
fit_a = _yfit(ch14, ch14._view)
cnt = {'apply': 0, 'frame': 0}
_real_apply14 = ch14._apply_view
ch14._apply_view = lambda *a, **k: (cnt.__setitem__('apply', cnt['apply'] + 1), _real_apply14(*a, **k))[1]
_real_frame14 = ch14._frame
ch14._frame = lambda: (cnt.__setitem__('frame', cnt['frame'] + 1), _real_frame14())[0]
check('基準：Y 軸自動 fit 可見窗口（唔係全表 min/max）',
      _eqy(ch14.ax.get_ylim(), fit_a[0]) and _eqy(ch14.axv.get_ylim(), fit_a[1]))

ch14._on_press(_ev14(200.0))
locked = (ch14.ax.get_ylim(), ch14.axv.get_ylim())
check('press → 凍結而家嘅 Y 尺度（#24 元兇：邊拖邊 refit = 橫移 + 同時垂直缩放，實測每 25 個 event 變 0.93–1.05×）',
      ch14._y_lock is not None and _eqy(locked[0], fit_a[0]) and _eqy(locked[1], fit_a[1]))
ys, frames = [], 0
for _xx in (210.0, 220.0, 230.0, 240.0, 250.0):
    ch14._on_motion(_ev14(_xx))
    ys.append(ch14.ax.get_ylim())
    if _xx in (220.0, 240.0):                 # 中途插幾幀，模擬真拖動（event 比幀密）
        ch14._pan_tick()
        frames += 1
        ys.append(ch14.ax.get_ylim())
check('拖動中（連同逐幀追近）Y 範圍逐個位恒定；而且每個 mouse event 唔再重算一次 limits/Y/ticks（實測 137→75，一半白做）',
      all(_eqy(y, fit_a[0]) for y in ys) and cnt['apply'] == frames)
QTest.qWait(200)                              # 樣本過期 → 唔觸發慣性（滑行喺 Part 13 已單獨測）
ch14._on_release(_ev14(250.0))
final14 = ch14._view
settle()
fit_b = _yfit(ch14, final14)
check('松手 → 一次過 refit 返新窗口（解除凍結、尺度跟到實際拖到嘅位置；兩段尺度真係唔同 → 呢項測到嘢）',
      ch14._y_lock is None and final14 == (150.0, 350.0)
      and _eqy(ch14.ax.get_ylim(), fit_b[0]) and not _eqy(locked[0], fit_b[0]))

ch14._view = (100.0, 300.0)
ch14._apply_view()
f_before, seq = cnt['frame'], []
ch14._pan_to((160.0, 360.0))                 # 🤖 target 要企喺數據範圍內：出界會俾 `_apply_view` clamp，量唔到收歛
for _ in range(20):
    ch14._pan_tick()                          # 🤖 手動 drive 幀：收歛契約要逐幀斷言，唔靠真 QTimer
    seq.append(ch14._view)
    if ch14._pan_target is None:
        break
steps = [seq[i + 1][0] - seq[i][0] for i in range(len(seq) - 1)]
check('平滑平移：逐幀單調追近、零 overshoot、≥3 幀先收歛（每幀位移細 = 滑動而唔係跳格）、最終誤差 0（貼實 target 逐個位）',
      len(seq) >= 3 and all(seq[i][0] < seq[i + 1][0] for i in range(len(seq) - 1))
      and all(v[0] <= 160.0 and v[1] <= 360.0 for v in seq)
      and max(steps) < 60.0 and seq[-1] == (160.0, 360.0) and ch14._pan_target is None)
check('每一幀都行 `_frame()`（唔能直接 `_apply_view()`：子类靠呢個 hook 重畫指標疊加 — #24 測試逼出嘅 bug）',
      cnt['frame'] - f_before == len(seq))

ch14._view = (100.0, 300.0)
ch14._apply_view()
locked2 = ch14.ax.get_ylim()
ch14._on_press(_ev14(200.0))
ch14._on_motion(_ev14(230.0))
ch14._on_scroll(_ev14(200.0, step=1))
check('拖動中途滾輪 → 即刻解除 Y 凍結（縮放 = 用戶明確要重縮放）', ch14._y_lock is None)
settle()
check('解除之後 Y 跟返新窗口（唔留低凍結咗嘅舊尺度）',
      _eqy(ch14.ax.get_ylim(), _yfit(ch14, ch14._view)[0]) and not _eqy(ch14.ax.get_ylim(), locked2))

ch14._on_press(_ev14(100.0))
locked3 = ch14._y_lock
ch14.set_bars(rows[:200])
check('set_bars 換數據 → 解除 Y 凍結（新數據必須重 fit）', ch14._y_lock is None and locked3 is not None)

ch14._view = (20.0, 120.0)                   # 🤖 而家得返 200 根：視窗要企喺範圍內，先至分辯到「落後」定「clamp」
ch14._apply_view()
ch14._on_press(_ev14(70.0))
ch14._on_motion(_ev14(120.0))
check('未追完嘅平移喺下一個手勢之前一定貼實（`_flush_pan`）→ 新 drag 由實際視窗開始，唔會由落後咗嘅位置計',
      ch14._pan_target == (70.0, 170.0) and ch14._view == (20.0, 120.0))
ch14._on_press(_ev14(70.0))
check('貼實之後 `_drag` 錨點 = 實際位置（跟手唔會返跳）',
      ch14._pan_target is None and ch14._view == (70.0, 170.0) and ch14._drag[1] == 70.0)

print('\n' + ('全部通過 ✅' if not FAILURES else f'失敗 {len(FAILURES)} 項：{FAILURES}'))
sys.exit(0 if not FAILURES else 1)

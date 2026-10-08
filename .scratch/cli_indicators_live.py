"""ticket #19 live smoke：真 OpenD — HK.HSImain K_1M 串流 → 指標 panel 有值、theme 跟色、開關唔郁 _view。

Run: python .scratch/cli_indicators_live.py（需要 OpenD 開緊；symbol index cache 存在）。
一次性 live 驗證（正式 hermetic 覆蓋喺 e2e_gui_indicators.py）。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile  # noqa: E402

import gateway.state_store as state_store  # noqa: E402

state_store.STATE_PATH = Path(tempfile.mkdtemp()) / 'ui_state.json'   # 唔污染真 ui_state

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])  # noqa: E402

FAILS = []


def check(name, ok):
    print(('  ✅ ' if ok else '  ❌ ') + name, flush=True)
    if not ok:
        FAILS.append(name)


def pump(n=20):
    for _ in range(n):
        app.processEvents()


# 真 index 包 FakeDir（is_stale=False → startup 唔 auto-fetch，smoke 快；串流照行真 broker）
from gateway.pages import gui_kline as gk  # noqa: E402
from modules.symbol_search import CORE_TYPES, get_directory as real_get_directory  # noqa: E402


class FakeDir:
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

from gateway import indicators as ind  # noqa: E402
import gateway.theme as theme_mod  # noqa: E402
from gateway.pages.kline_page import KlinePage  # noqa: E402

page = KlinePage()
page.show()
pump()
win = page._win
chart = win.chart

print('── live：HK.HSImain K_1M 串流 ──')
win.code_edit.setText('HK.HSImain')
win.test_btn.click()          # 預設 K_1M / 200 bars / futu
deadline = time.time() + 60
while time.time() < deadline and not chart._rows:
    pump(5)
    time.sleep(0.1)
check(f'真串流收到 K 線（rows={len(chart._rows)}）', len(chart._rows) > 0)

ids = {e['def']: e['id'] for e in page._mgr.items()}
deadline = time.time() + 10
while time.time() < deadline and (chart._ind_cache is None
                                  or ids['boll'] not in chart._ind_cache['full']):
    pump(5)
    time.sleep(0.1)
full = chart._ind_cache['full']
boll_mid, atr = full[ids['boll']]['mid'], full[ids['atr']]['atr']
check('BOLL mid 末端有值（真數據非 NaN）', not np.isnan(boll_mid[-1]))
check('ATR 末端有值', not np.isnan(atr[-1]))
axp = chart._ind_axis[ids['atr']]
check('ATR panel 有線 + 標題（live）', len(axp.get_lines()) >= 1 and 'ATR' in axp.get_title(loc='left'))

print('── theme 跟色 ──')
theme_mod.apply_theme('light')
pump(10)
acc = theme_mod.THEMES['light']['accent']
check('light theme → BOLL 中線 draw-time 食新 accent 色',
      any(l.get_color() == acc for l in chart.ax.get_lines()))
theme_mod.apply_theme('dark')
pump(10)

print('── 開關唔郁 view ──')
n = len(chart._rows)
view = (5.0, min(40.0, n - 1.0))          # 🤖 必須喺數據範圍內 — parent 對出界 _view 會主動返跟隨模式
chart._view = view
chart._redraw()
btn = page._ind_toggles[ids['atr']]
btn.click()
pump(5)
view_ok = chart._view == view and len(chart.canvas.figure.axes) == 2
btn.click()
pump(5)
check('disable→axes 減 / enable→返，用戶 _view 全程保持',
      view_ok and len(chart.canvas.figure.axes) == 3 and chart._view == view)

print('── ICT 區塊（OB / FVG / VOB）live ──')
for k in ('ob', 'fvg', 'vob'):
    page._mgr.add(k, 'main', {}, origin='smoke')
pump(5)
ids2 = {e['def']: e['id'] for e in page._mgr.items()}
n = len(chart._rows)
chart._view = (max(0.0, n - 120.0), float(n - 1))   # 睇最近 120 根（區塊先至睇得清）
chart._redraw()
deadline = time.time() + 10
while time.time() < deadline and not all(i in chart._ind_cache['full'] for i in ids2.values()):
    pump(5)
    time.sleep(0.1)
full = chart._ind_cache['full']


def runs(arr):
    m = ~np.isnan(arr)
    return int(np.count_nonzero(np.diff(m.astype(np.int8)) == 1))


for k in ('ob', 'fvg', 'vob'):
    s = full[ids2[k]]
    nz = runs(s['bull_top']) + runs(s['bear_top'])
    top, bot = s['bull_top'], s['bull_bottom']
    m = ~np.isnan(top)
    check(f'{k} live：真 HSI K 線有區塊（{nz} 段）+ top≥bottom',
          nz > 0 and (not m.any() or (top[m] >= bot[m]).all()))
check('K線頁開關掣列跟住變 5 個（ICT 三個都有掣，text 帶 acronym）',
      len(page._ind_toggles) == 5 and all(ids2[k] in page._ind_toggles for k in ('ob', 'fvg', 'vob'))
      and all(k.upper() in page._ind_toggles[ids2[k]].text() for k in ('ob', 'fvg', 'vob')))
shot = Path(__file__).with_name('ict_zones_live.png')
chart.canvas.figure.savefig(shot, dpi=110, facecolor=chart.canvas.figure.get_facecolor())
print(f'  📷 截圖：{shot}（OB 紅/綠區塊、FVG 缺口、VOB 有效 OB）')

win.test_btn.click()          # stop stream
pump(5)
page._on_app_quit()
pump()
print('\n' + ('全部通過 ✅' if not FAILS else f'失敗 {len(FAILS)} 項：{FAILS}'))
sys.exit(0 if not FAILS else 1)

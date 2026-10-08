"""診斷 + live 目視（ticket #21 跟進）：用戶反映 VOB 區塊「連續、唔係獨立方塊」→「有啲 VOB 獨立出嚟，
同 K 線冇連接同關係」。印出真 HSI 數據上嘅 VOB 區塊清單、合併落陣列後嘅連續段、以及每個區塊距可見
K 線幾遠；再開 VOB 截圖，對比「關閘（舊）」/「開閘（新）」兩張圖嘅 Y 軸高度。

Run: python .scratch/diag_vob_zones.py（需要 OpenD 開緊）
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile  # noqa: E402

import gateway.state_store as state_store  # noqa: E402

state_store.STATE_PATH = Path(tempfile.mkdtemp()) / 'ui_state.json'

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])  # noqa: E402


def pump(n=20):
    for _ in range(n):
        app.processEvents()


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
from gateway.pages.kline_page import KlinePage  # noqa: E402

page = KlinePage()
page.show()
pump()
win = page._win
chart = win.chart

win.code_edit.setText('HK.HSImain')
win.ktype_combo.setCurrentText('K_15M')      # 🤖 要夠多根先重現到「浮喺空處」嘅舊區塊
try:
    win.num_spin.setValue(300)
except Exception as exc:                    # noqa: BLE001
    print('num_spin → %s' % exc)
win.test_btn.click()
deadline = time.time() + 60
while time.time() < deadline and not chart._rows:
    pump(5)
    time.sleep(0.1)
n = len(chart._rows)
print('rows = %d' % n)
if n < 30:
    print('❌ 收唔到足夠 K 線（OpenD 未開？）')
    sys.exit(1)

o = np.array([r[1] for r in chart._rows], dtype=float)
h = np.array([r[2] for r in chart._rows], dtype=float)
l = np.array([r[3] for r in chart._rows], dtype=float)
c = np.array([r[4] for r in chart._rows], dtype=float)
ohlc = {'o': o, 'h': h, 'l': l, 'c': c}

# 用戶截圖嗰組參數 + 新增嘅 max_size / pen 預設（區塊高度過濾 + 被消耗幾深即失效）
P = {'period': 14, 'strength': 1.0, 'confirm': 3, 'sweep': 5, 'max_zones': 15,
     'max_size': 3.0, 'pen': 50, 'supersede': 1}
atr = ind.compute_atr(ohlc, {'period': P['period']})['atr']
cand = ind._ob_candidates(o, h, l, c, atr, P['strength'], P['confirm'], P['sweep'], True,
                          P['max_size'])
zones = ind._ob_zones(n, h, l, c, cand[-P['max_zones']:], P['max_zones'], P['pen'] / 100.0,
                      bool(P['supersede']))
print('VOB 候選 %d 個 → 區塊 %d 個（max_zones=%d）' % (len(cand), len(zones), P['max_zones']))
print('\n── 逐個區塊（start..end 含 end；h/ATR = 區塊高度 ÷ 當時 ATR）──')
for st, en, lo, hi, d in zones:
    ref = atr[st] if not np.isnan(atr[st]) else float('nan')
    print('  %s start=%-4d end=%-4d 長=%-4d 區間=[%.1f, %.1f] 高=%.1f 高/ATR=%.2f%s'
          % ('多' if d > 0 else '空', st, en, en - st + 1, lo, hi, hi - lo,
             (hi - lo) / ref if ref == ref else float('nan'),
             '   ← 未失效，畫到最後一根' if en == n - 1 else ''))

s = ind._zones_to_arrays(n, zones)
ov = [(a[0], b[0]) for ia, a in enumerate(zones) for b in zones[ia + 1:]
      if a[4] == b[4] and a[0] <= b[1] and b[0] <= a[1]]
print('\n── 同向重疊檢查（supersede=%d）：%s ──'
      % (P['supersede'], '冇重疊 ✓（每個方塊都有自己完整有效期，唔會被切短）' if not ov
         else '重疊 %d 對：%s ← 得 supersede=0 先會見到' % (len(ov), ov[:6])))
print('── 合併落陣列之後嘅連續段（🤖 相連 ≠ 重疊：一段內幾個 level = 幾個各自完整嘅方塊）──')
for tk, bk, nm in (('bull_top', 'bull_bottom', '多'), ('bear_top', 'bear_bottom', '空')):
    top, bot = s[tk], s[bk]
    m = ~(np.isnan(top) | np.isnan(bot))
    runs, i = [], 0
    while i < n:
        if m[i]:
            j = i
            lv = set()
            while j + 1 < n and m[j + 1]:
                j += 1
            for k in range(i, j + 1):
                lv.add((round(top[k], 4), round(bot[k], 4)))
            runs.append((i, j, len(lv)))
            i = j + 1
        else:
            i += 1
    print('  %s：連續段 %d 條' % (nm, len(runs)))
    for a, b, nlv in runs:
        print('     [%d..%d] 長 %d 根，內含 %d 個唔同level%s'
              % (a, b, b - a + 1, nlv,
                 '   ← 相連但各自完整 → 繪畫切成 %d 個方塊' % nlv if nlv > 1 else ''))

print('\n── 開 VOB，睇可見窗：區塊價位 vs 可見 K 線（「浮喺空處」嘅成因）──')
ok_add, msg, item = page._mgr.add('vob', 'main', P, origin='diag')
print('  add vob → %s %s' % (ok_add, msg))
# 🤖 縮到最後 25 根：早期形成、價格再冇返去嘅區塊就會變成「浮喺空處」——正正係用戶見到嘅情況
chart._view = (max(0.0, n - 25.0), float(n - 1))
chart._redraw()
pump(5)
k = n - chart._s
xl0, xl1 = chart.ax.get_xlim()
i0, i1 = max(0, int(xl0)), min(k, int(np.ceil(xl1)) + 1)
a0, a1 = chart._s + i0, chart._s + i1      # 🤖 區塊清單用絕對 index，一定要換返絕對先至對得埋
cl, ch = l[a0:a1].min(), h[a0:a1].max()
rng = ch - cl
gate = rng * ind.FIT_PAD
print('  可見窗（絕對）%d..%d K 線範圍 [%.1f, %.1f] 幅 %.1f（Y-fit 閘 = %.1f 點）'
      % (a0, a1, cl, ch, rng, gate))
nfar = 0
for st, en, lo, hi, d in zones:
    if en < a0 or st >= a1:
        continue
    gap = max(cl - hi, lo - ch, 0.0)
    if gap > gate:
        nfar += 1
    print('  %s [%d..%d] 區間=[%.1f, %.1f] → 距可見 K 線 %.1f 點 = %.0f%%%s'
          % ('多' if d > 0 else '空', max(st, a0), min(en, a1 - 1), lo, hi, gap, 100.0 * gap / rng,
             '   ← 離譜遠（同可見 K 線冇關係）→ 唔再撐 Y 軸' if gap > gate else ''))
print('  被距離閘隔走嘅區塊：%d 個' % nfar)

sl = chart._ind_cache['full'][item['id']]
xs = np.arange(i0, i1, dtype=float)
loc = {kk: v[chart._s + i0:chart._s + i1] for kk, v in sl.items()}
nb = sum(len(ind._zone_boxes(xs, loc[tk], loc[bk]))
         for tk, bk in (('bull_top', 'bull_bottom'), ('bear_top', 'bear_bottom')))
print('  可見窗 %d..%d → 方塊 %d 個；ax.collections = %d' % (i0, i1, nb, len(chart.ax.collections)))


def _shot(name):
    yl, xv = chart.ax.get_ylim(), chart.ax.get_xlim()
    print('  截圖而家嘅可見窗：local x [%.1f, %.1f] = 絕對 [%d, %d]（_s=%d, _view=%s）'
          % (xv[0], xv[1], chart._s + xv[0], chart._s + xv[1], chart._s, chart._view))
    print('  Y 軸 [%.1f, %.1f] 高 %.1f = 可見 K 線嘅 %.2f 倍'
          % (yl[0], yl[1], yl[1] - yl[0], (yl[1] - yl[0]) / rng))
    p = Path(__file__).with_name(name)
    chart.canvas.figure.savefig(p, dpi=110, facecolor=chart.canvas.figure.get_facecolor())
    print('  📷 截圖：%s' % p)


print('\n── 關閘（之前嘅行為）──')
_old = ind.FAR_OVERLAYS
ind.FAR_OVERLAYS = frozenset()
chart._redraw()
pump(3)
_shot('vob_far_off.png')

print('── 開閘（而家嘅行為）──')
ind.FAR_OVERLAYS = _old
chart._redraw()
pump(3)
_shot('vob_boxes_live.png')

win.test_btn.click()
pump(5)
page._on_app_quit()
pump()
sys.exit(0)

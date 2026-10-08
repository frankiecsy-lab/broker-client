"""E2E GUI test — 行情頁（Page 0）grid + 全週期 + 串流 + 本地記憶。

Run: python .scratch/e2e_gui_quotes.py   (from project root; QT_QPA_PLATFORM=offscreen 自動設定。
     全 hermetic：fake broker（注入 client_factory）+ fake symbol index + tmp state 檔 —
     唔打網絡、唔碰真 gateway/ui_state.json)

Flow:
1. shell 註冊：PAGE_KEYS[0] == 'home'（首頁最前）、quotes 第二位
2. 構造：6 cells / 默認 1x1（cell0 可見、其餘 hide）/ 11 週期按鈕齊全
3. 串流：默認 HK.00700 → baseline df → chart._rows + 價 label；tick → 價即時更新 +
   throttle 後 chart 更新；stale token tick 作廢
4. 換週期：K_5M 按鈕 exclusive（得一個 checked）→ 重新 stream（fake 收新 ktype）
5. 換標的：'hk.hsimain' → canonical 大細階 'HK.HSImain'（經 fake index get()）；
   模糊輸入 '腾讯' → completer model 連名；無效代碼 → ❌ label
6. layout 切換：2×2 → cells 0-3 可見 4-5 隱藏、**cells 實例不變**；cell1 設標的 → 新 stream
7. 本地記憶：改完 state → 新 page 實例 load 返（layout + 各格 symbol/period）
8. i18n retranslate 三語冇 KeyError；清理：request_shutdown → fake __aexit__ 被 call

Exit code 0 = all pass; non-zero = at least one check failed.
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402
import pandas as pd  # noqa: E402

import gateway.state_store as state_store  # noqa: E402
from gateway.app import PAGE_KEYS  # noqa: E402
from gateway.pages.quotes_page import QuotesPage, KTYPES, N_CELLS  # noqa: E402

FAILURES = []


def check(name, ok):
    print(('  ✅ ' if ok else '  ❌ ') + name)
    if not ok:
        FAILURES.append(name)


def wait_for(app, cond, what='?', timeout=10.0):
    t0 = time.time()
    while not cond():
        app.processEvents()
        if time.time() - t0 > timeout:
            check(f'TIMEOUT waiting: {what}', False)
            return False
    return True


def _df(closes):
    """KLINE_COLUMNS 形狀嘅 fake K 線 df（bar i 用 close[i]，o/h/l 圍住佢）。"""
    n = len(closes)
    return pd.DataFrame({
        'time_key': pd.date_range('2026-10-08 09:30', periods=n, freq='1min').astype(str),
        'open': [c - 0.5 for c in closes],
        'high': [c + 1.0 for c in closes],
        'low': [c - 1.0 for c in closes],
        'close': list(closes),
        'volume': [1000 + i for i in range(n)],
    })


class FakeSearchDir:
    """symbol index 契約子集：entries + search()/has_code()/get()/display_name()。"""

    def __init__(self):
        self._entries = [
            {'code': 'HK.00700', 'name': '騰訊控股', 'name_zh': '腾讯控股', 'name_en': 'Tencent'},
            {'code': 'HK.HSImain', 'name': '恒指期貨主連', 'name_zh': '恒指期货主连', 'name_en': 'HSI Main'},
            {'code': 'US.NVDA', 'name': '英偉達', 'name_zh': '英伟达', 'name_en': 'NVIDIA'},
        ]
        self._by_code = {e['code'].upper(): e for e in self._entries}

    def search(self, q, limit=20):
        q = str(q).strip().lower()
        out = [e for e in self._entries
               if q in e['code'].lower() or q in e['name'] or q in e['name_zh'] or q in e['name_en'].lower()]
        return out[:limit]

    def has_code(self, code):
        return str(code).strip().upper() in self._by_code

    def get(self, code):
        return self._by_code.get(str(code).strip().upper())

    def display_name(self, code, lang='zh'):
        e = self.get(code)
        if not e:
            return ''
        return e['name_en'] if lang == 'en' else (e['name_zh'] if lang == 'zh_cn' else e['name'])


class FakeKlineClient:
    """BrokerClient.stream_kline 契約替身：first yield = baseline，其後 = 一個 tick，之後吊住等 cancel。"""

    def __init__(self):
        self.calls = []          # (code, ktype) — keyword 調用都記錄
        self.entered = 0
        self.exited = 0
        self._hold = {}          # (code, ktype) → asyncio.Event（generator 吊住唔好結束）

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, *a):
        self.exited += 1
        return None

    async def stream_kline(self, code, ktype, broker=None, kline_num=None):
        self.calls.append((str(code), str(ktype)))
        ev = asyncio.Event()
        self._hold[(str(code), str(ktype))] = ev

        async def gen():
            yield _df([100.0, 101.0, 102.0])          # baseline
            await asyncio.sleep(0.05)
            yield _df([100.0, 101.0, 102.0, 105.0])   # live tick
            await ev.wait()                            # 吊住 — 等換標的/週期或 shutdown 時 cancel

        return True, gen(), ''


def main():
    app = QApplication.instance() or QApplication(sys.argv)

    # ── Part 1：shell 註冊（首頁最前，行情第二位）──
    print('── Part 1: shell registration ──')
    check("PAGE_KEYS[0] == 'home'（首頁最前）", len(PAGE_KEYS) > 0 and PAGE_KEYS[0] == 'home')
    check("'quotes' 已註冊（第二位）", 'quotes' in PAGE_KEYS and PAGE_KEYS[1] == 'quotes')

    # state 檔 redirect 去 tmp — 唔碰真 gateway/ui_state.json
    tmpdir = tempfile.mkdtemp(prefix='e2e_quotes_')
    state_store.STATE_PATH = Path(tmpdir) / 'ui_state.json'

    fake = FakeKlineClient()
    page = QuotesPage(client_factory=lambda: fake)
    # 注入 fake symbol index（completer 構造時已食真 directory → 換 _dir）
    fdir = FakeSearchDir()
    page._directory = fdir
    for c in page.cells:
        c.symbol_edit.completer()._dir = fdir
    page.show()   # offscreen 都要 show — 否則 isVisible() 永遠 False
    app.processEvents()
    check('page objectName = quotes_page', page.objectName() == 'quotes_page')
    check(f'{N_CELLS} cells constructed', len(page.cells) == N_CELLS)
    check('worker thread ready', wait_for(app, lambda: page._worker is not None, what='worker_ready'))

    # ── Part 2：默認 1x1 + 全週期按鈕 ──
    print('── Part 2: default layout + period buttons ──')
    check('默認 1×1：cell0 可見、其餘隱藏',
          page.cells[0].isVisible() and not any(c.isVisible() for c in page.cells[1:]))
    check('11 個週期按鈕齊全（K_1M…K_YEAR，文字全顯示冇 dropdown）',
          all(len(c._period_btns) == len(KTYPES) == 11 and
              all(b.text() == kt for kt, b in c._period_btns.items()) for c in page.cells))
    check('默認 period = K_1M checked', page.cells[0]._period == 'K_1M'
          and page.cells[0]._period_btns['K_1M'].isChecked())

    # ── Part 3：默認標的串流（baseline → 價 → tick → stale token）──
    print('── Part 3: baseline stream + live tick ──')
    check('默認 HK.00700 K_1M 自動開 stream',
          wait_for(app, lambda: ('HK.00700', 'K_1M') in fake.calls, what='default stream call'))
    cell0 = page.cells[0]
    check('baseline → chart._rows 上圖',
          wait_for(app, lambda: len(cell0.chart._rows) == 3, what='baseline bars'))
    check('baseline → 價 label = 102.00',
          wait_for(app, lambda: cell0.price_lbl.text() == '102.00', what='baseline price'))
    check('live tick → 價即時更新 105.00',
          wait_for(app, lambda: cell0.price_lbl.text() == '105.00', what='tick price'))
    check('throttle redraw → chart 4 bars',
          wait_for(app, lambda: len(cell0.chart._rows) == 4, what='throttled redraw'))
    # stale token（舊 run 遲到 tick）→ 作廢，價唔改
    old_price = cell0.price_lbl.text()
    page._on_cell_update(0, 999999, {'phase': 'tick', 'df': _df([1.0, 2.0, 999.0])})
    app.processEvents()
    check('stale token tick 被作廢（價唔改）', cell0.price_lbl.text() == old_price)

    # ── Part 4：換週期（exclusive 按鈕組 → 重新 stream）──
    print('── Part 4: period switch (exclusive buttons) ──')
    cell0._period_btns['K_5M'].click()
    app.processEvents()
    checked = [kt for kt, b in cell0._period_btns.items() if b.isChecked()]
    check('K_5M exclusive：得一個 checked', checked == ['K_5M'] and cell0._period == 'K_5M')
    check('換週期 → 重新 stream K_5M',
          wait_for(app, lambda: ('HK.00700', 'K_5M') in fake.calls, what='K_5M stream'))

    # ── Part 5：換標的（canonical 大細階）+ 模糊輸入 + 無效代碼 ──
    print('── Part 5: symbol change + fuzzy + invalid ──')
    cell0.symbol_edit.setText('hk.hsimain')
    cell0.symbol_edit.returnPressed.emit()
    # 🤖 模糊輸入經 gateway/symbol_input（debounce 200ms → 本地 index）→ 等 debounce 先讀 model
    def cands():
        return cell0.symbol_edit.completer().model().stringList()

    cell0.symbol_edit.setText('腾讯')
    wait_for(app, lambda: any('HK.00700' in s for s in cands()), 'fuzzy candidates')
    check('模糊輸入 → completer model 連名（「腾讯」hit 騰訊控股）',
          any('HK.00700' in s and '騰訊控股' in s for s in cands()))
    cell0.symbol_edit.setText('hk.hsimain')
    cell0.symbol_edit.returnPressed.emit()
    check('HK.HSImain canonical 大細階（唔變 HSIMAIN）',
          wait_for(app, lambda: ('HK.HSImain', 'K_5M') in fake.calls, what='canonical stream'))
    cell0.symbol_edit.setText('HELLO')
    cell0.symbol_edit.returnPressed.emit()
    app.processEvents()
    check('無效代碼 → ❌ label（唔開 stream）', '❌' in cell0.price_lbl.text())
    cell0.symbol_edit.setText('hk.hsimain')   # 還原 — 之後 Part 6/7 用呢個 state
    cell0.symbol_edit.returnPressed.emit()

    # ── Part 6：layout 切換（cells 實例永不銷毀）──
    print('── Part 6: layout switch ──')
    ids_before = [id(c) for c in page.cells]
    page._layout_btns['2x2'].click()
    app.processEvents()
    check('2×2：cells 0-3 可見、4-5 隱藏',
          all(page.cells[i].isVisible() for i in range(4))
          and not any(page.cells[i].isVisible() for i in range(4, 6)))
    check('cells 實例不變（切換只 hide/show + reposition）',
          [id(c) for c in page.cells] == ids_before)
    cell1 = page.cells[1]
    cell1.symbol_edit.setText('US.NVDA')
    cell1.symbol_edit.returnPressed.emit()
    check('可見格設標的 → 新 stream（US.NVDA K_1M）',
          wait_for(app, lambda: ('US.NVDA', 'K_1M') in fake.calls, what='cell1 stream'))
    check('cell1 baseline 上圖',
          wait_for(app, lambda: len(cell1.chart._rows) >= 3, what='cell1 baseline'))

    # 6.5 均分保證（用戶：不管彈出/放大縮小視窗，多圖高度都要均分 —
    #     _apply_layout 對可見 row/col 一律 stretch=1，唔靠 sizeHint 分配）
    def _pump(n=30):
        for _ in range(n):
            app.processEvents()
    def _equal_charts(ids):
        hs = [page.cells[i].chart.height() for i in ids]
        return hs and max(hs) - min(hs) <= 1   # ±1px 只容許奇偶捨入
    page.resize(700, 450)
    _pump()
    check('縮細 700×450：2×2 四格 chart 高度仍均分', _equal_charts(range(4)))
    page.resize(1500, 950)
    _pump()
    check('放大 1500×950：2×2 四格 chart 高度仍均分', _equal_charts(range(4)))

    # ── Part 7：本地記憶（統一 state_store 一個檔）──
    print('── Part 7: state persistence ──')
    saved = state_store.load_section('quotes', {})
    check('state 檔有 quotes section（layout + 6 cells）',
          saved.get('layout') == '2x2' and len(saved.get('cells', [])) == N_CELLS)
    fake2 = FakeKlineClient()
    page2 = QuotesPage(client_factory=lambda: fake2)
    page2._directory = fdir   # hermetic：canonical 都經 fake index
    for c in page2.cells:
        c.symbol_edit.completer()._dir = fdir
    check('新 page 實例 load 返記憶（layout 2×2 + cell0 HK.HSImain K_5M + cell1 US.NVDA）',
          page2._layout == '2x2'
          and page2.cells[0].state() == {'symbol': 'hk.hsimain', 'period': 'K_5M'}
          and page2.cells[1].state() == {'symbol': 'US.NVDA', 'period': 'K_1M'})
    check('恢復後可見格自動重新串流',
          wait_for(app, lambda: ('HK.HSImain', 'K_5M') in fake2.calls
                   and ('US.NVDA', 'K_1M') in fake2.calls, what='restored streams', timeout=15))
    page2._on_app_quit()
    check('page2 清理 → fake2 __aexit__', wait_for(app, lambda: fake2.exited == 1, what='fake2 exit'))

    # ── Part 8：i18n + 清理 ──
    print('── Part 8: i18n + cleanup ──')
    for lang in ('zh_hk', 'zh_cn', 'en'):
        page.retranslate(lang)
        app.processEvents()
    check('retranslate 三語冇 KeyError（t() fail-fast）', True)
    check('retranslate en → placeholder 跟語言', 'Symbol:' in page.cells[0].symbol_edit.placeholderText())
    page._on_app_quit()
    check('清理 → request_shutdown + fake __aexit__', wait_for(app, lambda: fake.exited == 1, what='fake exit'))

    print()
    if FAILURES:
        print(f'❌ E2E FAILED — {len(FAILURES)} check(s): ' + '; '.join(FAILURES))
        return 1
    print('✅ E2E PASSED — all checks green')
    return 0


if __name__ == '__main__':
    sys.exit(main())

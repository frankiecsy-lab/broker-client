"""E2E GUI test — 行情頁（Page 0）grid + 全週期 + 串流 + 本地記憶。

Run: python .scratch/e2e_gui_quotes.py   (from project root; QT_QPA_PLATFORM=offscreen 自動設定。
     全 hermetic：fake broker（注入 client_factory）+ fake symbol index + tmp state 檔 —
     唔打網絡、唔碰真 gateway/ui_state.json)

Flow:
1. shell 註冊：PAGE_KEYS[0] == 'home'（首頁最前）、quotes 第二位
1b. `.ui` 骨架（quotes_page.ui + chart_cell.ui）：Designer margin/spacing 照載入、頂欄控件齊、
    layout/週期掣按 LAYOUTS/KTYPES 填進 layoutSlot/periodSlot、promote 嘅 IndicatorKlineChart
    起返真 class、og/WA_StyledBackground 由 _STAMP 補返、QSS 兩層都有根
2. 構造：6 cells / 默認 1x1（cell0 可見、其餘 hide）/ 11 週期按鈕齊全
3. 串流：默認 HK.00700 → baseline df → chart._rows + 價 label；tick → 價即時更新 +
   throttle 後 chart 更新；stale token tick 作廢
4. 換週期：K_5M 按鈕 exclusive（得一個 checked）→ 重新 stream（fake 收新 ktype）
5. 換標的：'hk.hsimain' → canonical 大細階 'HK.HSImain'（經 fake index get()）；
   模糊輸入 '腾讯' → completer model 連名；無效代碼 → ❌ label
6. layout 切換：2×2 → cells 0-3 可見 4-5 隱藏、**cells 實例不變**；cell1 設標的 → 新 stream
6.6 #27：指標開關（注入 manager → panel/artist 出現、關咗返 parent 佈局）；策略下拉 → #30 全部格都套用
    畫 B/S（無 match 格冇）；串流 tick 新交叉 → **唔使重新揀策略** B/S 自動同步；指標關咗 B/S 照留
6.7 #28：「指標選項」menu — 逐實例 checkable = manager enabled（同 K線頁/管理頁同源）；MA sub-menu
    逐條線（show2=0 → cache 冇 ma2、少畫一條）
7. 本地記憶：改完 state → 新 page 實例 load 返（layout + 各格 symbol/period + 指標開關/策略 id）
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

from PySide6.QtCore import QMargins, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton, QToolButton  # noqa: E402
import pandas as pd  # noqa: E402

import gateway.indicators as indicators  # noqa: E402
import gateway.state_store as state_store  # noqa: E402
from gateway.app import PAGE_KEYS  # noqa: E402
from gateway.pages.quotes_page import QuotesPage, KTYPES, LAYOUTS, N_CELLS  # noqa: E402

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
        c.cell_symbol.completer()._dir = fdir
    page.show()   # offscreen 都要 show — 否則 isVisible() 永遠 False
    app.processEvents()
    check('page objectName = quotes_page', page.objectName() == 'quotes_page')
    check(f'{N_CELLS} cells constructed', len(page.cells) == N_CELLS)
    check('worker thread ready', wait_for(app, lambda: page._worker is not None, what='worker_ready'))

    # ── Part 1b：`.ui` 骨架（排版喺 gateway/ui/quotes_page.ui + chart_cell.ui；數量屬資料 → 填進 slot）──
    lay = page.layout()
    check('`.ui` root objectName + Designer margin/spacing 照載入',
          page.objectName() == 'quotes_page' and lay is not None
          and lay.contentsMargins() == QMargins(10, 8, 10, 10) and lay.spacing() == 6)
    check('頂欄靜態控件全部由 `.ui` 建出（objectName 即身份契約）',
          all(getattr(page, n, None) is not None for n in
              ('ind_toggle', 'ind_menu_btn', 'strat_label', 'strat_combo', 'layoutSlot', 'gridSlot'))
          and page.ind_toggle.isCheckable()
          and page.ind_menu_btn.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup)
    check('layout 按鈕按 LAYOUTS 生成並填進 layoutSlot（加 layout 唔使改 `.ui`）',
          page.layoutSlot.count() == len(LAYOUTS) and page.gridSlot.spacing() == 6
          and all(page.findChild(QPushButton, f'layout_{n}') is not None for n in LAYOUTS))
    c0 = page.cells[0]
    check('每格排版由 chart_cell.ui 建出（Designer margin/spacing 照載入）',
          c0.objectName() == 'quotes_cell' and c0.layout().contentsMargins() == QMargins(8, 6, 8, 6)
          and c0.layout().spacing() == 4
          and all(getattr(c0, n, None) is not None for n in ('cell_symbol', 'cell_price', 'periodSlot')))
    check('promote 嘅自繪 widget 起返真 class（QUiLoader factory）',
          isinstance(c0.chart, indicators.IndicatorKlineChart) and c0.chart.parent() is c0)
    check('週期掣按 KTYPES 生成並填進 periodSlot（加週期唔使改 `.ui`）',
          all(c.periodSlot.count() == len(KTYPES) for c in page.cells))
    check('og / WA_StyledBackground 由 _STAMP 補返（Designer 帶唔住 dynamic property）',
          page.ind_toggle.property('og') == 'indtoggle'
          and page.ind_menu_btn.property('og') == 'indmenu'
          and page._layout_btns['2x2'].property('og') == 'layoutbtn'
          and page.testAttribute(Qt.WA_StyledBackground) and c0.testAttribute(Qt.WA_StyledBackground))
    check('頁面 QSS 有根（objectName → QSS cascade，頁 + 格兩層都有）',
          'QWidget#quotes_page' in page.styleSheet() and 'QWidget#quotes_cell' in page.styleSheet())

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
          wait_for(app, lambda: cell0.cell_price.text() == '102.00', what='baseline price'))
    check('live tick → 價即時更新 105.00',
          wait_for(app, lambda: cell0.cell_price.text() == '105.00', what='tick price'))
    check('throttle redraw → chart 4 bars',
          wait_for(app, lambda: len(cell0.chart._rows) == 4, what='throttled redraw'))
    # stale token（舊 run 遲到 tick）→ 作廢，價唔改
    old_price = cell0.cell_price.text()
    page._on_cell_update(0, 999999, {'phase': 'tick', 'df': _df([1.0, 2.0, 999.0])})
    app.processEvents()
    check('stale token tick 被作廢（價唔改）', cell0.cell_price.text() == old_price)

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
    cell0.cell_symbol.setText('hk.hsimain')
    cell0.cell_symbol.returnPressed.emit()
    # 🤖 模糊輸入經 gateway/symbol_input（debounce 200ms → 本地 index）→ 等 debounce 先讀 model
    def cands():
        return cell0.cell_symbol.completer().model().stringList()

    cell0.cell_symbol.setText('腾讯')
    wait_for(app, lambda: any('HK.00700' in s for s in cands()), 'fuzzy candidates')
    check('模糊輸入 → completer model 連名（「腾讯」hit 騰訊控股）',
          any('HK.00700' in s and '騰訊控股' in s for s in cands()))
    cell0.cell_symbol.setText('hk.hsimain')
    cell0.cell_symbol.returnPressed.emit()
    check('HK.HSImain canonical 大細階（唔變 HSIMAIN）',
          wait_for(app, lambda: ('HK.HSImain', 'K_5M') in fake.calls, what='canonical stream'))
    cell0.cell_symbol.setText('HELLO')
    cell0.cell_symbol.returnPressed.emit()
    app.processEvents()
    check('無效代碼 → ❌ label（唔開 stream）', '❌' in cell0.cell_price.text())
    cell0.cell_symbol.setText('hk.hsimain')   # 還原 — 之後 Part 6/7 用呢個 state
    cell0.cell_symbol.returnPressed.emit()

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
    cell1.cell_symbol.setText('US.NVDA')
    cell1.cell_symbol.returnPressed.emit()
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

    # ── Part 6.6：#27 指標開關 + 策略 B/S 標記 + 串流同步 ──
    print('── Part 6.6: indicators toggle + strategy B/S + stream sync ──')
    from gateway import strategies as strat
    strat.reset_manager_for_test()   # singleton 由 tmp state 重 load（hermetic）
    ok_s, _msg, sentry = strat.get_manager().add(   # #32：add 冇 code/validity
        'E2E MA2x3',
        [{'type': 'ma_cross', 'side': 'above', 'params': {'fast': 2, 'slow': 3}, 'score': 100}],
        [{'type': 'ma_cross', 'side': 'below', 'params': {'fast': 2, 'slow': 3}, 'score': 100}],
        3)   # #31/#32 mark_buffer=3：@4 距 @2 兩條、@7 距 @4 三條 → 兩個都在 BUFFER 內轉純文字；@2 組內首個徽章
    check('策略建立成功（B/S 測試用）', ok_s and sentry)

    def bs_marks(chart):
        return sorted((int(tx.get_position()[0]), tx.get_text()) for tx in chart.ax.texts
                      if tx.get_text() in ('B', 'S'))

    page.ind_toggle.click()   # 開指標
    app.processEvents()
    check('開關撳上 → 所有格注入 manager + 副圖 panel 砌咗（seed BOLL/ATR）',
          all(c.chart._ind_mgr is not None for c in page.cells) and len(cell0.chart.ax_ind) >= 1)
    check('主圖指標疊加 artist 出現（BOLL 3 條線）', len(cell0.chart._ind_artists) >= 3)

    check('cell0 tick 到齊（4 bars）先至揀策略 — 免 race',
          wait_for(app, lambda: len(cell0.chart._rows) == 4, what='cell0 4 bars'))
    page._rebuild_strategy_combo()   # 等同入頁 showEvent refresh
    check('策略 dropdown item label = 純名稱（#32：冇「· 標的」）', page.strat_combo.findData(sentry['id']) >= 0
          and page.strat_combo.itemText(page.strat_combo.findData(sentry['id'])) == 'E2E MA2x3')
    page.strat_combo.setCurrentIndex(page.strat_combo.findData(sentry['id']))
    app.processEvents()
    check('cell0（HK.HSImain）→ B 標記（bar2 金叉 SMA2 101.5>SMA3 101）', bs_marks(cell0.chart) == [(2, 'B')])
    check('#30 全部格同時套用：cell1（US.NVDA 唔同標的）→ 自己數據都計出 B@2',
          bs_marks(cell1.chart) == [(2, 'B')])

    # 串流同步：直接餵 tick（行返 GUI throttle 鏈）— 新交叉必須自動出新標記
    newc = [100.0, 101.0, 102.0, 105.0, 90.0, 80.0, 70.0, 200.0]   # bar4 死叉（97.5<99）、bar7 再金叉（135>116.7）
    page._on_cell_update(0, page._cell_token[0], {'phase': 'tick', 'df': _df(newc)})
    check('串流 tick → chart 8 bars',
          wait_for(app, lambda: len(cell0.chart._rows) == 8, what='8 bars'))
    check('K 線刷新自動同步：唔使重新揀策略 → B@2 S@4 B@7 全出現',
          bs_marks(cell0.chart) == [(2, 'B'), (4, 'S'), (7, 'B')])

    # #29→#32：徽章 = 圓形同色底白字；BUFFER 內 = 背景透明純文字（B 紅字 / S 綠字，冇 bbox）
    from matplotlib.colors import to_rgba as _rgba
    from gateway.pages import gui_kline as _gk
    _bs = [(int(t.get_position()[0]), t.get_text(), t.get_bbox_patch() is not None, t.get_color(),
            t.get_bbox_patch().get_facecolor() if t.get_bbox_patch() is not None else None)
           for t in cell0.chart.ax.texts if t.get_text() in ('B', 'S')]
    check('#32 徽章 @2（組內首個）：圓形紅底（gk.C_UP）白字',
          [x for x, _s, bb, _c, _fc in _bs if bb] == [2]
          and all(t.get_color() == '#FFFFFF' for t in cell0.chart.ax.texts
                  if t.get_bbox_patch() is not None and t.get_text() in ('B', 'S'))
          and all(_fc is not None and _fc[:3] == _rgba(_gk.C_UP)[:3]
                  for _x, s, bb, _c, _fc in _bs if bb and s == 'B'))
    check('#32 BUFFER 內 @4/@7：冇 bbox 純文字 — S 綠字（C_DOWN）/ B 紅字（C_UP）',
          sorted(x for x, _s, bb, _c, _fc in _bs if not bb) == [4, 7]
          and all(_c == _gk.C_DOWN for _x, s, bb, _c, _fc in _bs if not bb and s == 'S')
          and all(_c == _gk.C_UP for _x, s, bb, _c, _fc in _bs if not bb and s == 'B'))

    # #30：StrategyManager listener → 策略頁（包括彈出窗）改 BUFFER，行情頁即時跟、唔使重新揀策略
    strat.get_manager().add_listener(page._on_strat_config)   # reset_manager_for_test 換咗 singleton → 重新掛
    ok_u, _um = strat.get_manager().update(sentry['id'], mark_buffer=0)
    app.processEvents()
    check('#30 listener：update mark_buffer=0 → 即時全部徽章（@7 都有圓形底色，冇重新揀）',
          ok_u and sorted((int(t.get_position()[0]), t.get_bbox_patch() is not None)
                          for t in cell0.chart.ax.texts if t.get_text() in ('B', 'S'))
          == [(2, True), (4, True), (7, True)])
    check('#30 listener 全格跟：cell1 嘅 B@2 都即時係徽章',
          all(t.get_bbox_patch() is not None for t in cell1.chart.ax.texts if t.get_text() in ('B', 'S')))
    strat.get_manager().update(sentry['id'], mark_buffer=3)   # 還原，後段語義不變
    app.processEvents()

    page.ind_toggle.click()   # 關指標 — B/S 必須照留
    app.processEvents()
    check('關指標 → panel 拆返（ax_ind 空、manager 移除）',
          all(c.chart._ind_mgr is None for c in page.cells) and cell0.chart.ax_ind == [])
    check('指標關咗 B/S 照畫（獨立開關）',
          bs_marks(cell0.chart) == [(2, 'B'), (4, 'S'), (7, 'B')])
    page.ind_toggle.click()   # 留低開住 — Part 7 驗證記憶
    app.processEvents()

    # ── Part 6.7：#28 指標選項 menu（逐實例 enabled + MA 逐條線 show）──
    print('── Part 6.7: #28 indicator options menu ──')
    from gateway import indicators as ind
    mgr = ind.get_manager()
    n0 = len(cell0.chart._ind_artists)   # baseline = BOLL 主圖線
    ok_m, _mm, mitem = mgr.add('ma', 'main', {'p1': 2, 'p2': 3, 'p3': 4, 'p4': 5})
    check('#28 加 MA 實例（show1..4 預設 1）',
          ok_m and all(mitem['params']['show%d' % n] == 1 for n in (1, 2, 3, 4)))
    app.processEvents()
    check('MA 開 → 四條線畫出（+4 artist）+ cache 有 ma1..ma4',
          len(cell0.chart._ind_artists) == n0 + 4
          and set(cell0.chart._ind_cache['full'][mitem['id']]) == {'ma1', 'ma2', 'ma3', 'ma4'})
    page._rebuild_ind_menu()
    check('menu 有逐實例 checkable item（checked = manager enabled）',
          mitem['id'] in page._ind_acts and page._ind_acts[mitem['id']].isChecked())
    page._ind_acts[mitem['id']].trigger()   # 揀走 MA
    app.processEvents()
    check('menu 揀走 MA → manager.enabled=False（同 K線頁/管理頁同源）',
          mgr.get(mitem['id'])['enabled'] is False)
    check('圖上 MA 線消失（BOLL 照留；disabled 照計 cache = 開關唔會 miss）',
          len(cell0.chart._ind_artists) == n0
          and set(cell0.chart._ind_cache['full'][mitem['id']]) == {'ma1', 'ma2', 'ma3', 'ma4'})
    page._rebuild_ind_menu()
    check('重建 menu → checked 反映 enabled=False', not page._ind_acts[mitem['id']].isChecked())
    page._ind_acts[mitem['id']].trigger()   # 開返
    app.processEvents()
    check('MA sub-menu 有四條線 item（label = MA + 現行週期）',
          all((mitem['id'], n) in page._ma_show_acts for n in (1, 2, 3, 4))
          and page._ma_show_acts[(mitem['id'], 2)].text() == 'MA 3')
    page._ma_show_acts[(mitem['id'], 2)].trigger()   # 收埋 MA2
    app.processEvents()
    check('收 MA2 → params.show2=0 + cache 冇 ma2（其他照留）+ 少畫一條',
          mgr.get(mitem['id'])['params']['show2'] == 0
          and set(cell0.chart._ind_cache['full'][mitem['id']]) == {'ma1', 'ma3', 'ma4'}
          and len(cell0.chart._ind_artists) == n0 + 3)
    page._ma_show_acts[(mitem['id'], 2)].trigger()   # 開返 — 留低全開比 Part 7
    app.processEvents()
    check('開返 MA2 → ma2 返 cache（配置變同串流同一條 cache 失效鏈）',
          set(cell0.chart._ind_cache['full'][mitem['id']]) == {'ma1', 'ma2', 'ma3', 'ma4'}
          and len(cell0.chart._ind_artists) == n0 + 4)

    # ── Part 7：本地記憶（統一 state_store 一個檔）──
    print('── Part 7: state persistence ──')
    saved = state_store.load_section('quotes', {})
    check('state 檔有 quotes section（layout + 6 cells）',
          saved.get('layout') == '2x2' and len(saved.get('cells', [])) == N_CELLS)
    check('#27 記憶（ind_shown=True + strategy id）',
          saved.get('ind_shown') is True and saved.get('strategy') == sentry['id'])
    fake2 = FakeKlineClient()
    page2 = QuotesPage(client_factory=lambda: fake2)
    page2._directory = fdir   # hermetic：canonical 都經 fake index
    for c in page2.cells:
        c.cell_symbol.completer()._dir = fdir
    check('新 page 實例 load 返記憶（layout 2×2 + cell0 HK.HSImain K_5M + cell1 US.NVDA）',
          page2._layout == '2x2'
          and page2.cells[0].state() == {'symbol': 'hk.hsimain', 'period': 'K_5M'}
          and page2.cells[1].state() == {'symbol': 'US.NVDA', 'period': 'K_1M'})
    check('#27 新實例還原指標開關 + 策略（combo 都返返個 item）',
          page2._ind_shown and page2.ind_toggle.isChecked()
          and page2._strategy_id == sentry['id']
          and page2.strat_combo.currentData() == sentry['id'])
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
    check('retranslate en → placeholder 跟語言', 'Symbol:' in page.cells[0].cell_symbol.placeholderText())
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

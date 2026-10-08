"""E2E GUI test — 標的列表頁（Page 5）表格 + FILTER + 模糊 + 一鍵更新 + 計數 + 衍生下鑽。

Run: python .scratch/e2e_gui_symbol_list.py   (from project root; QT_QPA_PLATFORM=offscreen 自動設定。
     全 hermetic：fake directory + fake option fetcher（注入 constructor）+ tmp state 檔 — 唔打網絡)

Flow:
1. shell 註冊：'symbol_list' in PAGE_KEYS
   + `.ui` 骨架：root objectName / Designer margin-spacing / 靜態控件 / filterSlot 按 registry 填 /
     `og`+`WA_StyledBackground` 由 _STAMP 補返 / QSS 有根
2. 構造：model rows = 全 entries；4 欄 header；market/type 顯示翻譯；
   🤖 name 欄繁中/簡中/英文自動切換（display_for 真路徑：zh_hk s2t 繁 / zh_cn 簡原樣 / en name_en fallback）
3. FILTER：市場 HK → 淨返 HK；種類 WARRANT → 入 w1（有窩輪標的 + 計數）；exclusive
4. 窩輪三級下鑽：w1（owner 分組）→ w2（認購/認沽/牛/熊）→ w3（窩輪列表 行使價/到期日）
   + US 無 owner 偽行直接落 w3 + 面包屑 + 逐級返回
5. 模糊輸入：flat search(types=None)；w1 內 substring 過濾
6. 期權二級下鑽：OPTION → o1（股票/ETF 候選）→ click → o2（fake 期權鏈 worker）
   + 失敗如實留 o1 + cache 唔重 fetch + 返回
7. 底部計數 / 一鍵更新 / 本地記憶（WARRANT 重載直接入 w1）/ i18n 三語

Exit code 0 = all pass; non-zero = at least one check failed.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QMargins, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

import gateway.state_store as state_store  # noqa: E402
import modules.symbol_search as ss  # noqa: E402  # FakeDir.display_name 委派真 display_for（三語契約）
from gateway.app import PAGE_KEYS  # noqa: E402
from gateway.pages.symbol_list_page import (MARKETS, TYPES,  # noqa: E402
                                            SymbolListPage)

FAILURES = []


def check(name, ok):
    print(('  ✅ ' if ok else '  ❌ ') + name)
    if not ok:
        FAILURES.append(name)


def pump(app, n=40):
    for _ in range(n):
        app.processEvents()


def wait_for(app, cond, what='?', timeout=10.0):
    t0 = time.time()
    while not cond():
        app.processEvents()
        if time.time() - t0 > timeout:
            check(f'TIMEOUT waiting: {what}', False)
            return False
    return True


ENTRIES = [
    {'code': 'HK.00700', 'name': '騰訊控股', 'name_zh': '腾讯控股', 'name_en': '', 'market': 'HK', 'type': 'STOCK'},
    {'code': 'HK.00005', 'name': '匯豐控股', 'name_zh': '汇丰控股', 'name_en': '', 'market': 'HK', 'type': 'STOCK'},
    {'code': 'HK.HSImain', 'name': '恒指期貨主連', 'name_zh': '恒指期货主连', 'name_en': '', 'market': 'HK', 'type': 'FUTURE'},
    {'code': 'HK.12345', 'name': '騰訊購', 'name_zh': '腾讯购', 'name_en': '', 'market': 'HK', 'type': 'WARRANT',
     'owner': 'HK.00700', 'wtype': 'CALL', 'expiry': '2026-12-31', 'strike': 500.0},
    {'code': 'HK.12346', 'name': '騰訊沽', 'name_zh': '腾讯沽', 'name_en': '', 'market': 'HK', 'type': 'WARRANT',
     'owner': 'HK.00700', 'wtype': 'PUT', 'expiry': '2026-12-31', 'strike': 300.0},
    {'code': 'HK.11111', 'name': '匯豐牛', 'name_zh': '汇丰牛', 'name_en': '', 'market': 'HK', 'type': 'WARRANT',
     'owner': 'HK.00005', 'wtype': 'BULL', 'expiry': '2027-06-30', 'strike': 99.0},
    {'code': 'US.WWWW', 'name': 'US Warrant', 'name_zh': '', 'name_en': 'US Warrant', 'market': 'US', 'type': 'WARRANT',
     'owner': '', 'wtype': '', 'expiry': '', 'strike': None},   # 🤖 實測：US 窩輪無 owner/類別
    {'code': 'US.NVDA', 'name': 'NVIDIA', 'name_zh': '', 'name_en': 'NVIDIA', 'market': 'US', 'type': 'STOCK'},
    {'code': 'US.QQQ', 'name': 'Invesco QQQ', 'name_zh': '', 'name_en': 'Invesco QQQ', 'market': 'US', 'type': 'ETF'},
    {'code': 'US.NQmain', 'name': 'NASDAQ 100 E-mini', 'name_zh': '', 'name_en': 'NASDAQ 100 E-mini', 'market': 'US', 'type': 'FUTURE'},
    {'code': 'HK.HSI', 'name': '恒生指數', 'name_zh': '恒生指数', 'name_en': '', 'market': 'HK', 'type': 'IDX'},
]

CHAIN_ROWS = [
    {'code': 'US.NVDA261007C115000', 'name': 'NVDA 261007 115.00C', 'otype': 'CALL',
     'strike': 115.0, 'expiry': '2026-10-07', 'lot_size': 100, 'owner': 'US.NVDA'},
    {'code': 'US.NVDA261007P115000', 'name': 'NVDA 261007 115.00P', 'otype': 'PUT',
     'strike': 115.0, 'expiry': '2026-10-07', 'lot_size': 100, 'owner': 'US.NVDA'},
]

CHAIN_CALLS = []


def fake_chain(code, progress_cb=None, max_dates=30):
    """symbol_search.fetch_option_chain 契約：(ok, rows, msg) + progress_cb(i,total,date)。"""
    CHAIN_CALLS.append(code)
    if progress_cb:
        progress_cb(1, 1, '2026-10-07')
    if code == 'US.NVDA':
        return True, [dict(r) for r in CHAIN_ROWS], '1 個到期日 / 2 條'
    return False, [], '呢個標的冇期權（模擬 SDK 錯誤）'


class FakeDir:
    """SymbolDirectory 契約子集：entries/fetched_at + search(types=) + fetch + get/display_name。"""

    def __init__(self):
        self.entries = [dict(e) for e in ENTRIES]
        self.fetched_at = '2026-10-08T00:00:00+08:00'
        self.fetch_calls = []
        self.search_calls = []

    def search(self, query, limit=20, types=None):
        self.search_calls.append((query, types))
        q = str(query).strip().lower()
        out = []
        for e in self.entries:
            if types is not None and e['type'] not in types:
                continue
            if (q in e['code'].lower() or q in e['name'].lower()
                    or q in e['name_zh'] or q in e['name_en'].lower()):
                out.append(e)
        return out[:limit]

    def get(self, code):
        for e in self.entries:
            if e['code'].upper() == str(code).upper():
                return e
        return None

    def display_name(self, code, lang='zh_hk'):
        # 🤖 委派真 display_for — 同 SymbolDirectory.display_name 同一契約（三語語言碼）
        e = self.get(code)
        return ss.display_for(e, lang) if e else ''

    def fetch(self, markets=('US', 'HK'), progress_cb=None):
        self.fetch_calls.append(tuple(markets))
        if progress_cb:
            progress_cb('HK/WARRANT', 14929)
        self.entries.append({'code': 'US.NEW', 'name': 'Fresh Listing', 'name_zh': '',
                             'name_en': 'Fresh Listing', 'market': 'US', 'type': 'STOCK'})
        self.fetched_at = '2026-10-08T09:00:00+08:00'
        return True, f'{len(self.entries)} symbols（HK, US 更新）'


def main():
    app = QApplication.instance() or QApplication(sys.argv)

    # ── Part 1：shell 註冊 ──
    print('── Part 1: shell registration ──')
    check("'symbol_list' in PAGE_KEYS", 'symbol_list' in PAGE_KEYS)

    tmpdir = tempfile.mkdtemp(prefix='e2e_symbollist_')
    state_store.STATE_PATH = Path(tmpdir) / 'ui_state.json'

    fdir = FakeDir()
    page = SymbolListPage(directory=fdir, option_fetcher=fake_chain)
    page.show()
    pump(app)
    check('objectName = symbol_list_page', page.objectName() == 'symbol_list_page')
    check(f'初始 model = 全部 {len(ENTRIES)} entries', page.model.rowCount() == len(ENTRIES))

    # ── Part 1b：`.ui` 骨架（排版喺 gateway/ui/symbol_list_page.ui；FILTER 掣數量屬資料 → 填進 slot）──
    lay = page.layout()
    check('`.ui` root objectName + Designer margin/spacing 照載入',
          page.objectName() == 'symbol_list_page' and lay is not None
          and lay.contentsMargins() == QMargins(10, 8, 10, 8) and lay.spacing() == 6)
    check('靜態控件全部由 `.ui` 建出（objectName 即身份契約）',
          all(getattr(page, n, None) is not None for n in
              ('sl_search', 'sl_update_btn', 'sl_back_btn', 'sl_crumb', 'sl_table',
               'sl_counts', 'sl_status')))
    check('FILTER 兩組按 MARKETS/TYPES 生成並填進 filterSlot（加市場／種類唔使改 `.ui`）',
          page.filterSlot.count() == len(MARKETS) + len(TYPES) + 1
          and all(page.findChild(QPushButton, f'sl_f_{k}') is not None
                  for k in tuple(MARKETS) + tuple(TYPES)))
    check('og / WA_StyledBackground 由 _STAMP 補返（Designer 帶唔住 dynamic property）',
          page.sl_update_btn.property('og') == 'slbtn'
          and page._mkt_btns['HK'].property('og') == 'filterbtn'
          and page.testAttribute(Qt.WA_StyledBackground))
    check('頁面 QSS 有根（objectName → QSS cascade）',
          'QWidget#symbol_list_page' in page.styleSheet())

    # ── Part 2：表格內容（翻譯 type / 語言 name）──
    print('── Part 2: table content ──')
    row_fut = next(r for r in range(page.model.rowCount())
                   if page.model.index(r, 0).data() == 'HK.HSImain')
    check('type 欄顯示翻譯（FUTURE → 「期貨」）',
          page.model.index(row_fut, 3).data() == '期貨')
    check('name 欄跟語言（zh_hk → 繁體 s2t「恒指期貨主連」）',
          page.model.index(row_fut, 1).data() == '恒指期貨主連')
    check('4 欄 header 三語翻译（zh_hk）',
          [page.model.headerData(c, Qt.Horizontal) for c in range(4)] == ['代碼', '名稱', '市場', '種類'])

    # 🤖 用戶要求：標的名稱繁中/簡中/英文自動切換（真 display_for 路徑，唔經 fake）
    row_nv = next(r for r in range(page.model.rowCount())
                  if page.model.index(r, 0).data() == 'US.NVDA')
    page.retranslate('zh_cn')
    check('name 欄 zh_cn → 簡體原樣唔轉（「恒指期货主连」）',
          page.model.index(row_fut, 1).data() == '恒指期货主连')
    page.retranslate('en')
    check('name 欄 en → name_en（NVIDIA）', page.model.index(row_nv, 1).data() == 'NVIDIA')
    check('name 欄 en → name_en 空如實 fallback 原生 name',
          page.model.index(row_fut, 1).data() == '恒指期貨主連')
    page.retranslate('zh_hk')
    check('name 欄返 zh_hk → s2t 繁體', page.model.index(row_fut, 1).data() == '恒指期貨主連')

    # ── Part 3：FILTER（市場 exclusive；WARRANT → 入 w1 下鑽）──
    print('── Part 3: filters ──')
    page._mkt_btns['HK'].click()
    pump(app)
    mkts = {page.model.index(r, 2).data() for r in range(page.model.rowCount())}
    check('市場=HK：淨返 HK entries', mkts == {'HK'} and page.model.rowCount() == 7)
    page._type_btns['WARRANT'].click()
    pump(app)
    check('種類=窩輪 → 入 w1（有窩輪標的列表，唔再平鋪）', page._mode == 'w1')
    check('w1 header（窩輪數/認購證/認沽證/牛證/熊證）',
          [page.model.headerData(c, Qt.Horizontal) for c in range(8)]
          == ['代碼', '名稱', '窩輪數', '認購證', '認沽證', '牛證', '熊證', '其他'])
    # HK：HK.00700（2 隻）排前、HK.00005（1 隻）排後（total desc）
    check('w1 按 owner 分組 + 計數（HK.00700 total 2 = CALL 1 + PUT 1）',
          page.model.rowCount() == 2
          and page.model.index(0, 0).data() == 'HK.00700'
          and page.model.index(0, 2).data() == '2'
          and page.model.index(0, 3).data() == '1'
          and page.model.index(0, 4).data() == '1'
          and page.model.index(1, 0).data() == 'HK.00005')
    check('w1 name 欄跟語言（entry → 繁體「騰訊控股」）',
          page.model.index(0, 1).data() == '騰訊控股')
    check('兩組 exclusive：各組得一個 checked',
          sum(1 for b in page._mkt_btns.values() if b.isChecked()) == 1
          and sum(1 for b in page._type_btns.values() if b.isChecked()) == 1)

    # ── Part 4：窩輪三級下鑽 ──
    print('── Part 4: warrant 3-level drill ──')
    page._on_cell_clicked(0, 0)   # HK.00700 → w2
    pump(app)
    check('L1→L2：類別行（認購證 1 / 認沽證 1）',
          page._mode == 'w2' and page.model.rowCount() == 2
          and page.model.index(0, 0).data() == '認購證' and page.model.index(0, 1).data() == '1'
          and page.model.index(1, 0).data() == '認沽證')
    crumb = page.sl_crumb.text()
    check('面包屑：窩輪 ▸ 標的名（繁簡任一種）',
          '窩輪' in crumb and ('腾讯' in crumb or '騰訊' in crumb))
    page._on_cell_clicked(0, 0)   # 認購證 → w3
    pump(app)
    check('L2→L3：窩輪列表（行使價/到期日）',
          page._mode == 'w3' and page.model.rowCount() == 1
          and page.model.index(0, 0).data() == 'HK.12345'
          and '500' in page.model.index(0, 3).data()
          and page.model.index(0, 4).data() == '2026-12-31')
    check('面包屑含類別', '認購證' in page.sl_crumb.text())
    page._back(); pump(app)
    check('返回 L3→L2', page._mode == 'w2')
    page._back(); pump(app)
    check('返回 L2→L1', page._mode == 'w1')
    page._back(); pump(app)
    check('返回 L1→flat（type reset ALL）', page._mode == 'flat' and page._type == 'ALL')

    # w1 模糊過濾（market 仍 HK）
    page._type_btns['WARRANT'].click(); pump(app)
    page.sl_search.setText('匯豐')
    wait_for(app, lambda: page.model.rowCount() == 1
             and page.model.index(0, 0).data() == 'HK.00005', what='w1 fuzzy')
    check('w1 模糊輸入：只返 match 嘅標的', True)
    page.sl_search.clear()
    wait_for(app, lambda: page.model.rowCount() == 2, what='w1 clear')

    # US 無 owner → 偽行直接落 w3
    page._mkt_btns['ALL'].click(); pump(app)
    check('ALL 市場 w1：2 個 HK 標的 + 1 個美股窩輪偽行',
          page.model.rowCount() == 3
          and '美股窩輪' in page.model.index(2, 1).data()
          and page.model.index(2, 2).data() == '1')
    page._on_cell_clicked(2, 0)   # 偽行 → 直接 w3
    pump(app)
    check('偽行點擊直接落 L3（US.WWWW 平鋪）',
          page._mode == 'w3' and page.model.rowCount() == 1
          and page.model.index(0, 0).data() == 'US.WWWW')
    page._back(); page._back(); pump(app)   # w3→w1→flat
    page._type_btns['ALL'].click(); pump(app)

    # ── Part 5：flat 模糊輸入（types=None 全量 — 窩輪都 match 到）──
    print('── Part 5: fuzzy input (all types) ──')
    page.sl_search.setText('腾讯')
    check('debounce 後 search(types=None) 且窩輪 match 到',
          wait_for(app, lambda: page.model.rowCount() == 3
                   and ('腾讯', None) in fdir.search_calls, what='fuzzy filter'))
    page.sl_search.setText('nvda')
    check('英文 code 模糊：NVDA hit',
          wait_for(app, lambda: page.model.rowCount() == 1
                   and page.model.index(0, 0).data() == 'US.NVDA', what='code fuzzy'))
    page.sl_search.clear()
    wait_for(app, lambda: page.model.rowCount() == len(ENTRIES), what='clear query')

    # ── Part 6：底部計數 ──
    print('── Part 6: counts bar ──')
    ct = page.sl_counts.text()
    st = page.sl_status.text()
    check('計數含市場×種類數字（HK 股票 2 / HK 窩輪 3 / US 期貨 1 …）',
          '窩輪 3' in ct and '期貨 1' in ct and '股票 2' in ct)
    check(f'計數含總數（共 {len(ENTRIES)}）+ 顯示筆數 + 更新時間',
          f'共 {len(ENTRIES)}' in ct and f'顯示 {len(ENTRIES)}' in st and '2026-10-08' in st)

    # ── Part 7：一鍵更新 ──
    print('── Part 7: refresh all ──')
    page.sl_update_btn.click()
    check('fake fetch 被 call（US+HK 全 plan）',
          wait_for(app, lambda: ('US', 'HK') in fdir.fetch_calls, what='fetch call'))
    check('更新完成 → 新 entry 入表格 + 計數更新',
          wait_for(app, lambda: page.model.rowCount() == len(ENTRIES) + 1
                   and f'共 {len(ENTRIES) + 1}' in page.sl_counts.text(), what='refreshed table'))
    check('更新後按鈕 re-enabled + status ✅',
          page.sl_update_btn.isEnabled() and '✅' in page.sl_status.text())

    # ── Part 8：期權二級下鑽（o1 候選 → o2 即時鏈）──
    print('── Part 8: option 2-level drill ──')
    page._type_btns['OPTION'].click()
    pump(app)
    cand = {e['code'] for e in fdir.entries if e['type'] in ('STOCK', 'ETF')}
    codes_shown = {page.model.index(r, 0).data() for r in range(page.model.rowCount())}
    check('OPTION → o1：只列股票/ETF 候選', page._mode == 'o1'
          and page.model.rowCount() == len(cand) and codes_shown == cand)
    page._on_cell_clicked(next(r for r in range(page.model.rowCount())
                               if page.model.index(r, 0).data() == 'US.NVDA'), 0)
    check('點擊 → QThread fake 鏈 worker 被 call',
          wait_for(app, lambda: 'US.NVDA' in CHAIN_CALLS, what='chain fetch'))
    check('o2：期權鏈表格（CALL/PUT 翻譯 + 行使價 + 到期日）',
          wait_for(app, lambda: page._mode == 'o2', what='o2 mode')
          and page.model.rowCount() == 2
          and page.model.index(0, 2).data() == '認購'
          and '115' in page.model.index(0, 3).data()
          and page.model.index(0, 4).data() == '2026-10-07')
    check('面包屑：期權 ▸ US.NVDA', 'US.NVDA' in page.sl_crumb.text())
    page._back(); pump(app)
    check('返回 o2→o1', page._mode == 'o1')
    n_calls = len(CHAIN_CALLS)
    page._on_cell_clicked(next(r for r in range(page.model.rowCount())
                               if page.model.index(r, 0).data() == 'US.NVDA'), 0)
    pump(app)
    check('cache：再次點擊唔重 fetch，直接入 o2',
          page._mode == 'o2' and len(CHAIN_CALLS) == n_calls)
    page._back(); pump(app)
    page._on_cell_clicked(next(r for r in range(page.model.rowCount())
                               if page.model.index(r, 0).data() == 'US.QQQ'), 0)
    check('鏈失敗 → 如實 ❌ 且留喺 o1',
          wait_for(app, lambda: 'US.QQQ' in CHAIN_CALLS, what='qqq chain')
          and wait_for(app, lambda: '❌' in page.sl_status.text(), what='❌ status')
          and page._mode == 'o1')
    page._back(); pump(app)
    check('返回 o1→flat', page._mode == 'flat' and page._type == 'ALL')

    # ── Part 9：本地記憶（filter 選擇；WARRANT 重載直接入 w1）──
    print('── Part 9: state persistence ──')
    page._mkt_btns['US'].click()
    page._type_btns['ETF'].click()
    pump(app)
    page2 = SymbolListPage(directory=fdir, option_fetcher=fake_chain)
    check('新 page 實例 load 返 filter（US × ETF）',
          page2._market == 'US' and page2._type == 'ETF'
          and page2.model.rowCount() == 1
          and page2.model.index(0, 0).data() == 'US.QQQ')
    page2._type_btns['WARRANT'].click()
    pump(app)
    page3 = SymbolListPage(directory=fdir, option_fetcher=fake_chain)
    check('重載 WARRANT → 直接入 w1（mode 跟 type 還原）',
          page3._type == 'WARRANT' and page3._mode == 'w1')

    # ── Part 10：i18n ──
    print('── Part 10: i18n ──')
    for lang in ('zh_hk', 'zh_cn', 'en'):
        page.retranslate(lang)
        pump(app)
    check('retranslate 三語冇 KeyError（t() fail-fast）', True)
    check('retranslate en → header/按鈕跟語言',
          page.model.headerData(0, Qt.Horizontal) == 'Code'
          and page._type_btns['WARRANT'].text() == 'Warrants')
    page._type_btns['WARRANT'].click()
    pump(app)
    check('en 入面 w1/w2 類別欄照譯',
          page.model.headerData(3, Qt.Horizontal) == 'Call'
          and page._wtype_label('BULL') == 'Bull')

    print()
    if FAILURES:
        print(f'❌ E2E FAILED — {len(FAILURES)} check(s): ' + '; '.join(FAILURES))
        return 1
    print('✅ E2E PASSED — all checks green')
    return 0


if __name__ == '__main__':
    sys.exit(main())

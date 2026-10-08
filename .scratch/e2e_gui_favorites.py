"""E2E GUI test — 標的收藏管理頁（Page 6）：本地 JSON + FILTER + 新增/刪除 + 記憶 + i18n。

Run: python .scratch/e2e_gui_favorites.py   (from project root; QT_QPA_PLATFORM=offscreen 自動設定。
     全 hermetic：fake directory（注入 constructor）+ tmp state 檔 — 唔打網絡)

Flow:
1. shell 註冊：'favorites' in PAGE_KEYS + NAV_DIRECT（主菜單直按）
2. 空頁：fav_empty status + counts 0
3. 新增：index 有 → snapshot market/type + canonical code（細階 input 還原）；
   index 冇但似 code → prefix 兜底 + UNKNOWN；重複 → ⚠️；唔似 code → ❌ 如實
4. 模糊輸入（gateway/symbol_input）：debounce → 本地 search；code 前綴 + 中文名都得；
   揀咗淨返 CODE 入欄；準確 code → 唔彈候選
5. FILTER：市場 × 種類 exclusive；UNKNOWN 只喺 ALL 出現
6. 刪除所選（多選）
7. 本地記憶：新 page 實例還原 items + filter；JSON 檔結構如實
8. i18n 三語（按鈕/欄頭/狀態跟語言）

Exit code 0 = all pass; non-zero = at least one check failed.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import gateway.favorites as favorites  # noqa: E402
import gateway.state_store as state_store  # noqa: E402
from gateway.app import NAV_DIRECT, PAGE_KEYS  # noqa: E402
from gateway.pages.favorites_page import FavoritesPage  # noqa: E402

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
    {'code': 'HK.HSImain', 'name': '恒指期貨主連', 'name_zh': '恒指期货主连', 'name_en': '', 'market': 'HK', 'type': 'FUTURE'},
    {'code': 'HK.HSI', 'name': '恒生指數', 'name_zh': '恒生指数', 'name_en': '', 'market': 'HK', 'type': 'IDX'},
    {'code': 'US.NVDA', 'name': 'NVIDIA', 'name_zh': '', 'name_en': 'NVIDIA', 'market': 'US', 'type': 'STOCK'},
    {'code': 'US.QQQ', 'name': 'Invesco QQQ', 'name_zh': '', 'name_en': 'Invesco QQQ', 'market': 'US', 'type': 'ETF'},
]


class FakeDir:
    """SymbolDirectory 契約子集：get / display_name / search / entries。"""

    def __init__(self, entries):
        self.entries = entries
        self.fetched_at = '2026-10-08T00:00:00'
        self._by = {e['code'].upper(): e for e in entries}

    def get(self, code):
        return self._by.get(str(code).strip().upper())

    def has_code(self, code):
        return str(code).strip().upper() in self._by

    def display_name(self, code, lang='zh'):
        e = self.get(code)
        if not e:
            return ''
        pref = e.get('name_en') if lang == 'en' else e.get('name_zh')
        return pref or e.get('name', '')

    def search(self, query, limit=20, types=None):
        q = str(query).lower()
        out = [e for e in self.entries
               if q in e['code'].lower() or q in str(e.get('name', '')).lower()
               or q in str(e.get('name_zh', '')).lower()]
        return out[:limit]


def cell(page, row, col):
    return page.model._page._cell(page.model.row_at(row),
                                  ('code', 'name', 'market', 'type', 'added')[col])


def codes_of(page):
    return [page.model.row_at(i)['code'] for i in range(page.model.rowCount())]


def main():
    app = QApplication(sys.argv)
    tmpdir = tempfile.mkdtemp(prefix='fav_state_')
    state_store.STATE_PATH = Path(tmpdir) / 'ui_state.json'

    # ── 1. shell 註冊 ──
    print('── Part 1: shell 註冊 ──')
    check("'favorites' in PAGE_KEYS", 'favorites' in PAGE_KEYS)
    check('NAV_DIRECT 含 favorites（主菜單直按）', 'favorites' in NAV_DIRECT)

    page = FavoritesPage(directory=FakeDir(ENTRIES))
    page.show()
    pump(app)

    # ── 2. 空頁 ──
    print('── Part 2: 空頁 ──')
    check('空表 + fav_empty status',
          page.model.rowCount() == 0 and '未有收藏' in page.status_lbl.text())
    check('counts = 收藏 0', '收藏 0' in page.counts_lbl.text())

    # ── 3. 新增 ──
    print('── Part 3: 新增 ──')
    page.add_edit.setText('hk.00700')   # 細階 input → canonical 還原
    page.add_btn.click()
    pump(app)
    check('index 有 → snapshot STOCK/HK + canonical code',
          codes_of(page) == ['HK.00700']
          and cell(page, 0, 3) == '股票' and cell(page, 0, 2) == 'HK')
    check('name 跟語言解析（zh → 名）', cell(page, 0, 1) == '腾讯控股')
    check('status ✅ fav_added', '已收藏' in page.status_lbl.text())
    check('輸入框清空', page.add_edit.text() == '')

    page.add_edit.setText('US.TEST99')   # index 冇但似 code → prefix 兜底
    page.add_btn.click()
    pump(app)
    check('index 冇但似 code → US prefix + UNKNOWN',
          'US.TEST99' in codes_of(page))
    idx = codes_of(page).index('US.TEST99')
    check('UNKNOWN 行 market=US + name 如實 —',
          cell(page, idx, 2) == 'US' and cell(page, idx, 1) == '—')

    page.add_edit.setText('HK.00700')   # 重複
    page.add_btn.click()
    pump(app)
    check('重複 → ⚠️ fav_dup 唔會加多一筆',
          codes_of(page).count('HK.00700') == 1 and '已經收藏過' in page.status_lbl.text())

    page.add_edit.setText('唔係code')   # 唔似 code 又搵唔到
    page.add_btn.click()
    pump(app)
    check('唔似 code → ❌ 如實，唔入表',
          '❌' in page.status_lbl.text() and page.model.rowCount() == 2)

    # ── 4. 模糊輸入（gateway/symbol_input：code 前綴 + 中文名都得，揀咗淨返 CODE 入欄）──
    print('── Part 4: 模糊輸入候選 ──')

    def cands():
        return page.completer.model().stringList()

    page.add_edit.setText('NV')
    wait_for(app, lambda: any(s.startswith('US.NVDA') for s in cands()), 'completer candidates')
    check('code 模糊（NV → US.NVDA）', any(s.startswith('US.NVDA') for s in cands()))

    page.add_edit.setText('腾讯')   # 🤖 用戶投訴位：打中文名必須有候選（舊版 model 淨返 code → 永遠空）
    wait_for(app, lambda: any(s.startswith('HK.00700') for s in cands()), 'name candidates')
    check('中文名模糊（腾讯 → HK.00700，item 連名稱）',
          any(len(s.split()) == 2 and s.startswith('HK.00700') for s in cands()))

    page.completer.activated.emit('HK.00700  騰訊控股')   # 模擬喺 dropdown 揀咗
    pump(app)
    check('揀咗候選 → 欄入面淨返乾淨 CODE', page.add_edit.text() == 'HK.00700')

    page.add_edit.setText('HK.00700')   # 已經係準確 code → 唔再彈候選阻眼
    wait_for(app, lambda: cands() == [], 'candidates cleared for exact code')
    check('準確 code → 清空候選（pass-through）', cands() == [])

    # ── 5. FILTER ──
    print('── Part 5: FILTER ──')
    page._mkt_btns['HK'].click()
    pump(app)
    check('市場 HK → 淨返 HK', codes_of(page) == ['HK.00700'])
    page._type_btns['STOCK'].click()
    pump(app)
    check('市場×種類交集 → 仍 1 筆股票', codes_of(page) == ['HK.00700'])
    page._mkt_btns['ALL'].click()
    pump(app)
    check('STOCK filter 下 US.TEST99（UNKNOWN）唔出現', codes_of(page) == ['HK.00700'])
    page._type_btns['ALL'].click()
    pump(app)
    check('ALL/ALL → UNKNOWN 出現（如實）', sorted(codes_of(page)) == ['HK.00700', 'US.TEST99'])
    check('filter 按鈕 exclusive（每組得一個 checked）',
          sum(b.isChecked() for b in page._mkt_btns.values()) == 1
          and sum(b.isChecked() for b in page._type_btns.values()) == 1)

    # ── 6. 刪除所選 ──
    print('── Part 6: 刪除 ──')
    page.table.selectRow(0)
    page.remove_btn.click()
    pump(app)
    check('刪除所選 → 剩低 1 筆 + 🗑 status',
          codes_of(page) == ['US.TEST99'] and '已刪除' in page.status_lbl.text())
    page.remove_btn.click()
    pump(app)
    check('冇選中 → ⚠️ fav_no_sel', '没有选中' in page.status_lbl.text()
          or '冇選中' in page.status_lbl.text())

    # ── 7. 本地記憶 + JSON 結構 ──
    print('── Part 7: 記憶 / JSON ──')
    page2 = FavoritesPage(directory=FakeDir(ENTRIES))
    pump(app)
    check('新實例還原 items + filter 記憶（ALL/ALL）',
          codes_of(page2) == ['US.TEST99']
          and page2._market == 'ALL' and page2._type == 'ALL')
    obj = json.loads(Path(state_store.STATE_PATH).read_text(encoding='utf-8'))
    fav = obj.get('favorites', {})
    check('JSON 檔：favorites section items 帶 code/market/type/added',
          isinstance(fav.get('items'), list) and len(fav['items']) == 1
          and set(fav['items'][0]) >= {'code', 'market', 'type', 'added'})

    # 加返兩筆再驗 filter 記憶
    page2.add_edit.setText('HK.HSI')
    page2.add_btn.click()
    page2.add_edit.setText('US.QQQ')
    page2.add_btn.click()
    pump(app)
    page2._mkt_btns['US'].click()
    page2._type_btns['ETF'].click()
    pump(app)
    page3 = FavoritesPage(directory=FakeDir(ENTRIES))
    pump(app)
    check('filter 記憶：重開直接入 US/ETF',
          page3._market == 'US' and page3._type == 'ETF'
          and codes_of(page3) == ['US.QQQ'])

    # ── 8. i18n 三語 ──
    print('── Part 8: i18n ──')
    page3.retranslate('en')
    pump(app)
    check('EN：按鈕/欄頭跟語言（filter 狀態不變）',
          page3.add_btn.text() == '+ Add favorite'
          and page3.model.headerData(4, Qt.Horizontal) == 'Added'
          and codes_of(page3) == ['US.QQQ'])
    check('EN type label', page3._type_label('ETF') == 'ETFs' and page3._type_label('STOCK') == 'Stocks')
    page3.retranslate('zh_cn')
    pump(app)
    check('zh_cn：简体按鈕 + 股票 label',
          page3.add_btn.text() == '＋ 新增收藏' and page3._type_label('STOCK') == '股票')

    # ── 9. canonical 大細階（🤖 live 抓出：store 唔准 upper — 期貨主連 HK.HSImain 要保留）──
    print('── Part 9: canonical 大細階 ──')
    page3._mkt_btns['ALL'].click()
    page3._type_btns['ALL'].click()
    page3.add_edit.setText('hk.hsimain')   # 細階 input → index canonical 還原
    page3.add_btn.click()
    pump(app)
    check('HK.HSImain canonical 保留（唔變 HSIMAIN）+ FUTURE snapshot',
          any(e['code'] == 'HK.HSImain' and e['type'] == 'FUTURE'
              for e in favorites.load_items()))

    print()
    if FAILURES:
        print(f'❌ E2E FAILED — {len(FAILURES)} checks: {FAILURES}')
        sys.exit(1)
    print('✅ E2E PASSED — all checks green')
    sys.exit(0)


if __name__ == '__main__':
    main()

"""E2E GUI test — FUTU trade page (Page 4) full flow.

Run: python .scratch/e2e_gui_futu_trade.py   (from project root; needs a display / offscreen Qt —
     QT_QPA_PLATFORM=offscreen is set automatically. 用直接執行而非 -m：stdlib `test` package 會 shadow 本地目錄)

Flow (all via FakeCtx / FakeQuoteCtx injected into the worker thread through _ctx_factory):
1. Page construction + i18n retranslate (3 languages)
1b. `.ui` 骨架（futu_trade_page.ui）：Designer margin/spacing 照載入、兩欄/四張 card/全部表單控件
    由 `.ui` 建出、欄位比例用 sizePolicy stretch、og/role/WA_StyledBackground 由 _STAMP 補返；
    表格欄數、combo item、QButtonGroup 呢啲「數量」屬資料 → 由 code 填進 `.ui` 嘅 slot
2. 自動連線（構造後 timer 自動 connect — 冇連線/斷開按鍵）→ account list —
   帳戶表只顯示 ACTIVE（DISABLED 被 filter 掉）；
   模擬/實盤 toggle 切換環境，所有資料跟住重新 query（唔混模擬同實盤）；
   市場過濾後為空 → 數據區清空 + 提示
3. place_order：買入/賣出兩鍵 — SIMULATE BUY success → today's orders auto-refresh;
   cancel selected order; positions + accinfo refresh
4. REAL path（解鎖狀態自動探測）：選 REAL 帳戶 → 即時 unlock_probe（modify_order 唔存在 id，
   無副作用）→ 狀態自動變「未解鎖」（唔使落單）；未解鎖落單 → 如實失敗唔落單；
   模擬 GUI 解鎖（flag 翻轉）→ 再探測 → 狀態自動「已解鎖」；落單成功；切返 SIM → 狀態欄隱藏
   （新版 OpenD 禁止 SDK unlock_trade；QMessageBox confirm 全程 monkeypatch auto-Yes）
5. code fuzzy input：debounce → stock_search（FakeSearchDir — 本地 symbol index 契約：entries +
   search()/get()，含期貨主連 HK.HSImain + 美股中文名欄 name_zh）→ QCompleter 候選（item =
   「CODE  名稱」連名，揀咗淨返 CODE 入欄）+ hint；期貨主連行情（canonical 細階 main —
   snapshot/subscribe fake 都大細階敏感，驗 _canonical_code）；
   繁簡通配（繁體「騰訊」query hit 簡體 canonical — t2s）；hint 中文跟 GUI 語言（繁/簡/英偉達→NVIDIA）；
   完整 code → subscribe：snapshot 預填 price=最新價 / qty=每手 + K_1M push 全流市價（KLINE_STREAM 方式）
   （fake handler _emit 注入 tick → price 自動更新；textEdited 後唔覆蓋）；
   中文 code 落單被 _CODE_RE 擋掉（唔送入 place_order）
5.5 nav 右鍵「彈出視窗」（OneGateWindow）：page 同一實例搬入彈出窗（reparent 後 show — 唔係空白窗）/
    左鍵永遠正常切換（彈出緊都搬返入 shell）/ 關窗搬返 stack 原位置
6. i18n 深度：表格欄名三語（col_*）、TIF 三語顯示（currentData 保持 enum）、語言切換唔影響選中
6. Live OpenD probe (optional): if config.json has futu.host/port and OpenD is running,
   actually connect once to verify the real SDK path (no orders placed — read-only queries only).
   `--no-live` skips it for fully hermetic runs (CI without OpenD).

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

import gateway.state_store as state_store  # noqa: E402
state_store.STATE_PATH = Path(tempfile.mkdtemp(prefix='e2e_futu_trade_')) / 'ui_state.json'  # 🤖 收工 stop 全部頁（有頁會 save state）→ 唔准污染真 ui_state

from PySide6.QtCore import Qt, QMargins  # noqa: E402
from PySide6.QtWidgets import QApplication, QGridLayout, QMessageBox, QSizePolicy  # noqa: E402
import pandas as pd  # noqa: E402

import gateway.pages.futu_trade_page as ftp_mod  # noqa: E402
import modules.symbol_search as ss  # noqa: E402 — FakeSearchDir 用佢嘅 _T2S（同真 index 同一正規化）
from futu import RET_OK  # noqa: E402
from gateway.app import NAV_DIRECT, OneGateWindow  # noqa: E402


class FakeCtx:
    """In-memory stand-in for OpenSecTradeContext — matches the REAL SDK call surface
    (ret_code + DataFrame tuples) so the worker code path is exercised unmodified."""

    def __init__(self, host='fake', port=0):
        self.host = host
        self.port = int(port)
        self.placed = []
        self.canceled = []
        self.closed = False
        self._trd_unlocked = False   # 模擬 OpenD GUI「解鎖交易」— 只影響 REAL 落單/撤單
        self._order_seq = 900000

    def close(self):
        self.closed = True

    def get_acc_list(self):
        df = pd.DataFrame([
            {'acc_id': '10000001', 'trd_env': 'SIMULATE', 'acc_type': 'CASH',
             'card_num': 'CARD-SECRET-SIM', 'trdmarket_auth': 'HK,US', 'acc_status': 'ACTIVE'},
            {'acc_id': '20000001', 'trd_env': 'REAL', 'acc_type': 'MARGIN',
             'card_num': 'CARD-SECRET-REAL', 'trdmarket_auth': 'HK,US,CN', 'acc_status': 'ACTIVE'},
            {'acc_id': '30000001', 'trd_env': 'REAL', 'acc_type': 'CASH',
             'card_num': 'CARD-SECRET-DISABLED', 'trdmarket_auth': 'HK', 'acc_status': 'DISABLED'},
        ])
        return RET_OK, df

    # 新版 OpenD 禁止 SDK unlock_trade — 未喺 GUI 解鎖時 REAL 落單/撤單回 unlock 類錯誤
    _UNLOCK_ERR = '未解锁交易'

    def place_order(self, price=0.0, qty=0, code='', trd_side=None, order_type=None,
                    trd_env=None, acc_id=None, time_in_force=None):
        if str(trd_env) == 'REAL' and not self._trd_unlocked:
            return -1, self._UNLOCK_ERR
        self._order_seq += 1
        oid = str(self._order_seq)
        self.placed.append(dict(trd_env=str(trd_env), code=code, trd_side=str(trd_side),
                                otype=str(order_type), price=float(price), qty=int(qty),
                                tif=str(time_in_force), acc_id=str(acc_id)))
        return RET_OK, pd.DataFrame({'order_id': [oid]})

    def order_list_query(self, trd_env=None, acc_id=None):
        rows = [{'order_id': f'{i + 1:06d}', 'code': p['code'], 'stock_name': '',
                 'trd_side': p['trd_side'], 'order_type': p['otype'], 'qty': p['qty'],
                 'dealt_qty': 0, 'price': p['price'], 'dealt_avg_price': 0.0,
                 'order_status': 'SUBMITTED', 'create_time': 'now'}
                for i, p in enumerate(self.placed)]
        return RET_OK, pd.DataFrame(rows)

    def cancel_order(self, trd_env=None, order_id=None):
        self.canceled.append((str(trd_env), str(order_id)))
        return RET_OK, pd.DataFrame()

    def modify_order(self, op, order_id, qty=0, price=0, trd_env=None, acc_id=None):
        # worker 用 modify_order(ModifyOrderOp.CANCEL, ...) 撤單；999999999 = 解鎖探測（唔存在 id）
        if str(trd_env) == 'REAL' and not self._trd_unlocked:
            return -1, self._UNLOCK_ERR
        if int(order_id) == 999999999:
            return -1, '订单不存在'
        self.canceled.append((str(trd_env), str(order_id)))
        return RET_OK, pd.DataFrame()

    def cancel_all_order(self, trd_env=None, acc_id=None):
        if str(trd_env) == 'REAL' and not self._trd_unlocked:
            return -1, self._UNLOCK_ERR
        return RET_OK, pd.DataFrame()

    def position_list_query(self, trd_env=None, acc_id=None):
        rows = [{'code': 'HK.00700', 'stock_name': 'TENCENT', 'position_market': 'HK',
                 'qty': 100, 'can_sell_qty': 100, 'cost_price': 350.0, 'market_val': 38000.0,
                 'pl_val': 3000.0, 'pl_ratio': 0.0857, 'currency': 'HKD'},
                {'code': 'US.AAPL', 'stock_name': 'APPLE', 'position_market': 'US',
                 'qty': 10, 'can_sell_qty': 10, 'cost_price': 200.0, 'market_val': 2100.0,
                 'pl_val': 100.0, 'pl_ratio': 0.05, 'currency': 'USD'}]
        return RET_OK, pd.DataFrame(rows)

    def accinfo_query(self, trd_env=None, acc_id=None):
        rows = [{'total_assets': 99999.99, 'cash': 12345.67, 'market_val': 87654.32,
                 'power': 150000.0, 'available_funds': 12000.0, 'avl_withdrawal_cash': 5000.0}]
        return RET_OK, pd.DataFrame(rows)


class FakeQuoteCtx:
    """In-memory stand-in for OpenQuoteContext — 而家只需要 get_market_snapshot（quote 預填）。
    （代碼搜尋已改行本地 symbol index — 見 FakeSearchDir，唔再打 basicinfo。）"""

    _SNAP = {'HK.00700': (380.0, 100), 'HK.00005': (70.0, 400),
             'US.AAPL': (334.6, 1), 'US.TSLA': (250.0, 1),
             'HK.HSImain': (23941.0, 50)}   # last_price, lot_size（真 OpenD 大細階敏感 — HSImain 細階 main）

    def __init__(self, host='fake', port=0):
        self.host = host
        self.port = int(port)
        self.closed = False
        self.snap_count = 0    # get_market_snapshot call count — 預填起步測試用
        self.subscribed = []   # subscribe_quote 記帳 — 全流市價測試用
        self.handler = None

    def set_handler(self, handler):
        self.handler = handler

    def subscribe(self, code_list, subtype_list, **kw):   # futu 10.x 統一 subscribe API
        self.subscribed = [str(c) for c in code_list]
        return RET_OK, ''

    def unsubscribe(self, code_list, subtype=None):
        gone = {str(c) for c in code_list}
        self.subscribed = [c for c in self.subscribed if c not in gone]
        return RET_OK, ''

    def get_market_snapshot(self, code_list):
        self.snap_count += 1
        # 大細階敏感（同真 OpenD 一致：'HK.HSIMAIN' → 冇行情）— 驗 worker _canonical_code 保 canonical
        rows = [{'code': str(c), 'last_price': p, 'lot_size': l}
                for c in code_list for (p, l) in [self._SNAP.get(str(c), (0.0, 0))]]
        return RET_OK, pd.DataFrame(rows)

    def close(self):
        self.closed = True


class FakeSearchDir:
    """In-memory stand-in for modules.symbol_search.SymbolDirectory — 搜尋改行本地 index
    （同 P8 共用），呢度複製佢嘅契約：entries + search(query, limit)，name 存簡體 canonical，
    CJK query 經 t2s 正規化先 match。含期貨主連（HK.HSImain）+ 美股中文名欄（用戶要求：
    打 HK → 恒指主連、打 NVDA → US.NVDA 英偉達）。"""

    _ENTRIES = [
        {'code': 'HK.00700', 'name': '腾讯控股', 'name_zh': '腾讯控股', 'name_en': '',
         'market': 'HK', 'type': 'STOCK'},
        {'code': 'HK.00005', 'name': '汇丰控股', 'name_zh': '汇丰控股', 'name_en': '',
         'market': 'HK', 'type': 'STOCK'},
        {'code': 'HK.HSImain', 'name': '恒指期货主连', 'name_zh': '恒指期货主连', 'name_en': '',
         'market': 'HK', 'type': 'FUTURE'},
        {'code': 'HK.800000', 'name': '恒生指数', 'name_zh': '恒生指数', 'name_en': '',
         'market': 'HK', 'type': 'IDX'},   # 指數唔可落單 — page 應該過濾咗
        {'code': 'US.NVDA', 'name': 'NVIDIA Corp', 'name_zh': '英伟达', 'name_en': 'NVIDIA Corp',
         'market': 'US', 'type': 'STOCK'},
        {'code': 'US.TSLA', 'name': 'Tesla Inc', 'name_zh': '特斯拉', 'name_en': 'Tesla Inc',
         'market': 'US', 'type': 'STOCK'},
    ]

    def __init__(self):
        self.entries = [dict(e) for e in self._ENTRIES]
        self.search_count = 0   # E2E 驗證搜尋經注入 dir（唔打網絡）

    def get(self, code):
        """契約同 SymbolDirectory.get — 大細階唔敏感 exact lookup → entry（canonical 大細階）。"""
        cu = str(code).strip().upper()
        for e in self.entries:
            if e['code'].upper() == cu:
                return dict(e)
        return None

    def search(self, query, limit=20):
        self.search_count += 1
        q = str(query).strip().upper()
        qn = ss._T2S.convert(q) if (ss._T2S and any('一' <= c <= '鿿' for c in q)) else q
        hits = [e for e in self.entries
                if q in e['code'].upper() or (qn and qn in e.get('name_zh', ''))
                or (q and q in e.get('name_en', '').upper())]
        return hits[:limit]


def _utf8_stdout():
    """Windows console 預設 GBK — ✅/❌ emoji 會 UnicodeEncodeError；強制 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def wait_for(app, cond, what='?', timeout=10.0):
    """Spin processEvents until cond() or timeout; return whether achieved."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


FAILS = 0


def check(name, ok):
    global FAILS
    print(f"  {'✅' if ok else '❌'} {name}")
    if not ok:
        FAILS += 1


def main():
    _utf8_stdout()
    app = QApplication.instance() or QApplication(sys.argv)
    win = OneGateWindow()
    win.show()
    page = win.pages['futu_trade']

    # 🤖 fake SDK factory 必須喺任何 event pumping 之前注入 — 頁面現在自動連線
    # （構造後 300ms timer），遲注入會俾真 SDK 食咗第一次 connect（破坏 hermetic）
    def _fake_factory(host, port):
        return FakeCtx(host=host, port=int(port))

    def _fake_qfactory(host, port):
        return FakeQuoteCtx(host=host, port=int(port))
    page._worker._ctx_factory = _fake_factory
    page._worker._qctx_factory = _fake_qfactory   # quote 行情 — fake quote ctx（hermetic）
    page._worker._search_dir = FakeSearchDir()    # 代碼模糊搜尋 — 注入 fake 本地 symbol index（hermetic）

    # 行情頁（預設頁）cell0 有默認 HK.00700 → 會自動開 stream；注入 fake broker 保持 hermetic
    class _FakeKlineClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def stream_kline(self, code, ktype, broker=None, kline_num=None):
            async def _empty():
                return
                yield   # pragma: no cover
            return True, _empty(), ''
    win.pages['quotes']._client_factory = lambda: _FakeKlineClient()

    # ── Part 1：One Gate registration + page construction + i18n retranslate (3 languages)──
    print('── Part 1: One Gate registration + construction + i18n ──')
    from gateway.app import PAGE_KEYS
    check("PAGE_KEYS has 'futu_trade'", 'futu_trade' in PAGE_KEYS)
    check('page constructed', page is not None and page.objectName() == 'futu_trade_page')
    check("nav button 'nav_futu_trade'",
          'futu_trade' in win.nav_btns and win.nav_btns['futu_trade'].objectName() == 'nav_futu_trade')
    for lang in ('zh_hk', 'zh_cn', 'en'):
        page.retranslate(lang)
        app.processEvents()
    check('retranslate zh_hk/zh_cn/en no KeyError (t() fail-fast)', True)
    page.retranslate('en')
    check('retranslate en buy btn', page.buy_btn.text() == 'BUY')
    check('retranslate en env sim btn', page.env_sim_btn.text() == 'SIMULATE')
    check('retranslate en tif display (data keeps enum)',
          page.tif_combo.currentText() == 'Day (DAY)' and page.tif_combo.currentData() == 'DAY')
    check('retranslate en table header', page.acc_table.horizontalHeaderItem(0).text() == 'Account ID')
    page.retranslate('zh_hk')
    check('retranslate zh_hk tif keeps selection + trilingual text',
          page.tif_combo.currentText() == '當日有效 (DAY)' and page.tif_combo.currentData() == 'DAY')
    check('retranslate zh_hk table header', page.acc_table.horizontalHeaderItem(0).text() == '帳戶 ID')

    # theme propagation: apply_theme → listener → 頁面級 QSS 跟住換（light/dark）
    from gateway.theme import apply_theme
    apply_theme('light')
    wait_for(app, lambda: '#E8EAED' in page.styleSheet(), what='theme light QSS', timeout=3)
    check('theme light → page QSS follows', True)
    apply_theme('dark')
    wait_for(app, lambda: '#1E1F22' in page.styleSheet(), what='theme dark QSS', timeout=3)
    check('theme dark → page QSS follows', True)

    # ── Part 1b：`.ui` 骨架（排版全部喺 gateway/ui/futu_trade_page.ui；「數量」屬資料 → code 填）──
    lay = page.layout()
    check('`.ui` root objectName + Designer margin/spacing 照載入',
          page.objectName() == 'futu_trade_page' and lay is not None
          and lay.contentsMargins() == QMargins(24, 24, 24, 24) and lay.spacing() == 6)
    check('兩欄主區 + 四張 card + 全部表單控件由 `.ui` 建出（objectName 即身份契約）',
          all(getattr(page, n, None) is not None for n in
              ('header_card', 'cols_wrap', 'left_col', 'right_col', 'orders_card', 'pos_card',
               'conn_card', 'order_card', 'orders_table', 'pos_table', 'acc_table', 'code_cell',
               'code_edit', 'price_edit', 'qty_edit', 'otype_combo', 'tif_combo', 'market_combo',
               'env_sim_btn', 'env_real_btn', 'buy_btn', 'sell_btn', 'unlock_status_lbl')))
    check('欄位/卡片比例喺 `.ui`（sizePolicy stretch = Designer Layout Stretch）',
          page.left_col.sizePolicy().horizontalStretch() == 3
          and page.right_col.sizePolicy().horizontalStretch() == 2
          and page.cols_wrap.sizePolicy().verticalStretch() == 1
          and page.orders_card.sizePolicy().verticalStretch() == 3
          and page.pos_card.sizePolicy().verticalStretch() == 2
          and page.header_card.sizePolicy().verticalPolicy() != QSizePolicy.Fixed)
    grids = {g.objectName(): g for g in page.findChildren(QGridLayout)}
    check('QGridLayout 間距屬性名必須 horizontalSpacing/verticalSpacing（hspacing 會被 uic 靜靜丟掉）',
          grids['orderGrid'].horizontalSpacing() == 10 and grids['orderGrid'].verticalSpacing() == 8)
    check('og / role / WA_StyledBackground 由 _STAMP 補返（Designer 帶唔住 dynamic property）',
          page.testAttribute(Qt.WA_StyledBackground)
          and page.orders_card.property('og') == 'pagecard'
          and page.order_card.property('og') == 'pagecard'
          and page.title_lbl.property('role') == 'pagetitle'
          and page.code_lbl.property('role') == 'formlabel'
          and page.buy_btn.property('og') == 'buybtn' and page.env_sim_btn.property('og') == 'envbtn')
    check('頁面 QSS 有根（objectName → QSS cascade）', 'QWidget#futu_trade_page' in page.styleSheet())
    check('表格欄數 / 高度上限：欄數由 code（ACC/ORDER/POS_COLS），位置同 maxH 由 `.ui`',
          page.acc_table.maximumHeight() == 160
          and page.acc_table.columnCount() == len(ftp_mod.ACC_COLS)
          and page.orders_table.columnCount() == len(ftp_mod.ORDER_COLS)
          and page.pos_table.columnCount() == len(ftp_mod.POS_COLS))
    check('combo item / 環境 exclusive toggle 由 code 填（加選項唔使改 `.ui`）',
          page.market_combo.count() == len(ftp_mod.MARKET_FILTERS)
          and page.otype_combo.count() == len(ftp_mod.ORDER_TYPES)
          and page.tif_combo.count() == len(ftp_mod.TIF_OPTIONS)
          and page._env_group.exclusive() and page.env_sim_btn.isChecked())

    # ── Part 2：自動連線（冇按鈕 — 構造後 timer 觸發）→ account list（ACTIVE filter + SIM/REAL toggle）──
    print('── Part 2: auto-connect + env/market filters ──')
    check('no connect/disconnect buttons (用戶要求：刪咗)',
          not hasattr(page, 'connect_btn') and not hasattr(page, 'disconnect_btn'))
    # 🤖 自動連線：構造後 300ms timer → Part 1 嘅 pumping 期間已經用 fake factory 連咗
    wait_for(app, lambda: page._connected, what='auto-connect (no button)')
    check('auto-connect ok（默認自動連線）', '✅' in page.conn_status_lbl.text())

    # default SIM env → only the ACTIVE SIMULATE account (DISABLED REAL filtered out)
    check('SIM env default → 1 row (ACTIVE filter)', page.acc_table.rowCount() == 1)
    check('row is SIMULATE+ACTIVE',
          all(r['trd_env'] == 'SIMULATE' and r['acc_status'] == 'ACTIVE' for r in page._acc_rows))

    # env toggle → REAL: only ACTIVE REAL shown (DISABLED filtered out), data follows along
    page.env_real_btn.click()
    wait_for(app, lambda: page.acc_table.rowCount() == 1, what='env toggle REAL')
    check('REAL env → 1 row (DISABLED filtered)',
          all(r['trd_env'] == 'REAL' and r['acc_status'] == 'ACTIVE' for r in page._acc_rows))
    wait_for(app, lambda: page._acc is not None and str(page._acc.get('trd_env')) == 'REAL',
             what='real auto select')
    check('auto-selected REAL account after toggle', True)

    # back to SIM → 1 row again
    page.env_sim_btn.click()
    wait_for(app, lambda: page.acc_table.rowCount() == 1, what='env toggle SIM')
    check('back to SIM → 1 row', all(r['trd_env'] == 'SIMULATE' for r in page._acc_rows))

    # market filter HKFUND → 0 rows (SIM account is HK,US only) → data views cleared + hint
    page.market_combo.setCurrentText('HKFUND')
    wait_for(app, lambda: page.acc_table.rowCount() == 0, what='market filter HKFUND → empty')
    check('SIM+CN → no ACTIVE account hint', 'ACTIVE' in page.orders_status_lbl.text())
    check('data cleared when no account', page._acc is None and page.pos_table.rowCount() == 0)

    # market filter All → row back, auto re-select + data reloads (follows along)
    page.market_combo.setCurrentText('All')
    wait_for(app, lambda: page.acc_table.rowCount() == 1, what='market filter All')
    wait_for(app, lambda: page._acc is not None and str(page._acc.get('trd_env')) == 'SIMULATE',
             what='auto re-select SIM')
    check('auto re-selected SIM account after filter back to All', True)

    # ── Part 3：place_order（買入/賣出兩鍵）──
    print('── Part 3: place order (BUY/SELL buttons) ──')
    # invalid form → ❌ (code empty), via BUY button handler, no worker op submitted
    page.qty_edit.setText('10'); page.price_edit.setText('200.5')
    page._on_place('BUY')
    wait_for(app, lambda: '❌' in page.order_result_lbl.text(), what='invalid form msg')
    check('empty code → invalid form blocked', True)

    # SIMULATE BUY success → today's orders auto-refresh
    page.code_edit.setText('US.AAPL')
    page.buy_btn.click()
    wait_for(app, lambda: 'order_id=' in page.order_result_lbl.text(), what='place_order')
    check('SIM buy order success (900001)', '900001' in page.order_result_lbl.text())
    fake_ctx = page._worker._ctx
    check('side=BUY recorded', fake_ctx.placed[-1]['trd_side'] == 'BUY')
    wait_for(app, lambda: page.orders_table.rowCount() >= 1, what='orders auto refresh')

    # cancel selected order (row 0) → ✅ + orders table update
    page.orders_table.selectRow(0)
    page.cancel_sel_btn.click()
    wait_for(app, lambda: '✅' in page.orders_status_lbl.text(), what='cancel_order')
    check('cancel ok', len(fake_ctx.canceled) == 1)

    # positions + accinfo refresh (auto-loaded on connect; explicit refresh still works)
    page._refresh('positions')
    wait_for(app, lambda: page.pos_table.rowCount() >= 2, what='positions')
    check('accinfo has cash', 'cash' in page.accinfo_lbl.text().lower())

    # ── Part 4：REAL path — 解鎖狀態自動探測（唔使落單）+ 落單流程 ──
    print('── Part 4: REAL unlock auto-probe + order flow ──')
    page.env_real_btn.click()
    wait_for(app, lambda: page._acc is not None and str(page._acc.get('trd_env')) == 'REAL',
             what='real auto select (2nd)')
    check('REAL account label uses accent color warning', 'color:' in page.acc_sel_lbl.styleSheet())
    # 用 isHidden()（唔係 isVisible()）— 頁面未必係 stacked layout 當前頁，parent hidden 會令 isVisible 永遠 False
    check('unlock status visible in REAL env', not page.unlock_status_lbl.isHidden())
    # 揀 REAL 帳戶 → 即時 unlock_probe（modify_order fake id，無副作用）→ 狀態自動更新，唔使等落單
    wait_for(app, lambda: page._unlock_state is False, what='auto unlock probe → locked')
    check('auto probe on account select → locked (no order placed)',
          'OpenD GUI' in page.unlock_status_lbl.text())

    # fill form (valid SELL)；REAL _on_place 全部經 confirm dialog → 先 monkeypatch auto-Yes
    page.code_edit.setText('HK.00700'); page.qty_edit.setText('100'); page.price_edit.setText('380')
    real_question = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    try:
        # 未喺 OpenD GUI 解鎖 → place 回 unlock 類錯誤：唔落單 + 如實回報
        n_placed_before = len(fake_ctx.placed)
        page.sell_btn.click()
        wait_for(app, lambda: '❌' in page.order_result_lbl.text(), what='locked place fails honestly')
        check('locked REAL place → no order placed', len(fake_ctx.placed) == n_placed_before)
        check('still locked after failed place',
              page._unlock_state is False and 'OpenD GUI' in page.unlock_status_lbl.text())
        check('order result hints OpenD GUI unlock', 'OpenD GUI' in page.order_result_lbl.text())
        check('buttons re-enabled after failed place', page.sell_btn.isEnabled() and page.buy_btn.isEnabled())

        # 模擬用戶喺 OpenD GUI 撳「解鎖交易」→ 即時探測 → 狀態自動變「已解鎖」（都係唔使落單）
        fake_ctx._trd_unlocked = True
        page._probe_unlock_now()
        wait_for(app, lambda: page._unlock_state is True, what='auto probe after GUI unlock → unlocked')
        check('GUI-unlock (simulated) → status auto-unlocked without placing an order',
              '已解鎖' in page.unlock_status_lbl.text())

        # 落單成功
        page.buy_btn.click()
        wait_for(app, lambda: '900002' in page.order_result_lbl.text(), what='place after unlock')
        check('REAL order placed (side=BUY)', fake_ctx.placed[-1]['trd_side'] == 'BUY')

        # 已解鎖 → 第二單直接行
        page.sell_btn.click()
        wait_for(app, lambda: '900003' in page.order_result_lbl.text(), what='2nd REAL place')
        check('2nd REAL order placed directly (side=SELL)', fake_ctx.placed[-1]['trd_side'] == 'SELL')
    finally:
        QMessageBox.question = real_question

    # 切返 SIM → 狀態欄隱藏
    page.env_sim_btn.click()
    wait_for(app, lambda: page.unlock_status_lbl.isHidden(), what='unlock status hide')
    check('unlock status hidden in SIM env', True)

    # ── Part 5：code fuzzy input — QCompleter + worker stock_search（FakeQuoteCtx）──
    print('── Part 5: code fuzzy input (QCompleter) ──')
    page.code_edit.clear()   # 清走 Part 4 嘅 HK.00700
    wait_for(app, lambda: not page._search_timer.isActive(), what='debounce settle', timeout=2.0)

    def _codes():   # dropdown item =「CODE  名稱」（用戶要求連名）— 斷言只比較 CODE 部分
        return [s.split(maxsplit=1)[0] for s in page.code_completer.model().stringList()]

    # '700' → debounce 300ms → worker stock_search（本地 symbol index — FakeSearchDir）→ 候選 HK.00700
    # 用戶實測「冇反應」根因：QCompleter popup 只喺 key event 彈 — 異步填 model 之後要主動 complete()
    page.code_edit.setFocus()
    wait_for(app, lambda: page.code_edit.hasFocus(), what='code_edit focus')
    page.code_edit.setText('700')
    wait_for(app, lambda: 'HK.00700' in _codes(), what='completer candidates for "700"')
    check('fuzzy search "700" → candidate HK.00700', True)
    check('async results force completer popup', page.code_completer.popup().isVisible())
    # 用戶要求：dropdown 要包括中英文名稱 — item =「CODE  名稱（跟語言）」
    check('dropdown items include display name (中英文名喺 dropdown 入面)',
          any(s.startswith('HK.00700') and '騰訊控股' in s
              for s in page.code_completer.model().stringList()))

    # 用戶要求：打「HK」→ 自動出現期貨主連 HK.HSImain（恒指主連 — 市場前綴 query 期貨/指數排前）
    page.code_edit.setText('HK')
    wait_for(app, lambda: _codes()[:1] == ['HK.HSImain'],
             what='prefix "HK" → futures main contract first')
    check('market prefix "HK" → HK.HSImain 排第一 (index scope: 期貨主連)', True)
    check('IDX entries excluded from trade page candidates (唔可落單)',
          'HK.800000' not in _codes())
    # 未完整 code：「HK.007」→ 收窄到 HK.00700（quote 側對唔完整 code 靜默失敗，唔污染 hint）
    page.code_edit.setText('HK.007')
    wait_for(app, lambda: _codes() == ['HK.00700'],
             what='incomplete "HK.007" narrows to HK.00700')
    check('incomplete code "HK.007" → candidate HK.00700', True)

    # 用戶要求：期貨主連都要有報價 — HK.HSImain（canonical 細階 main）subscribe/snapshot 保大細階
    # （真 OpenD 實測 'HK.HSIMAIN' → 未知股票；fake snapshot 都大細階敏感）
    page._on_code_activated('HK.HSImain 恒指期货主连')
    wait_for(app, lambda: page._worker._qctx.subscribed == ['HK.HSImain'],
             what='HSImain subscribe keeps canonical case')
    check('HK.HSImain 期貨主連 — subscribe 保 canonical 大細階（唔變 HSIMAIN）', True)
    wait_for(app, lambda: page.price_edit.text() == '23941' and page.qty_edit.text() == '50',
             what='HSImain snapshot prefill')
    check('HK.HSImain snapshot 預填 23941/50（期貨攞到價）', True)
    page._worker._price_handler._emit('HK.HSImain', 23950.0)
    wait_for(app, lambda: (page._quote or {}).get('last_price') == 23950.0, what='HSImain tick received')
    check('HK.HSImain price_tick 收得到（大細階唔敏感 guard）', page.price_edit.text() == '23950')
    page.code_edit.clear()   # 清走，唔阻後面「騰訊」段
    wait_for(app, lambda: not page._search_timer.isActive(), what='debounce settle', timeout=2.0)

    # 繁簡通配：先用無匹配 query 清走舊候選（避免舊 list 造成假陽性），再用繁體「騰訊」hit 簡體 canonical
    page.code_edit.setText('ZZZQQ')
    wait_for(app, lambda: page.code_completer.model().stringList() == [], what='no-match clears candidates')
    page.code_edit.setText('騰訊')
    wait_for(app, lambda: 'HK.00700' in _codes(), what='traditional CJK "騰訊"')
    check('traditional CJK "騰訊" → candidate HK.00700 (t2s normalize)', True)

    # 模擬用戶喺 completer 揀咗候選 → code_edit 填入 + hint 顯示名稱；完整 code → quote 預填
    page.price_edit.setText('111'); page.qty_edit.setText('50')   # 覆蓋 Part 4 殘值，先證預填真係覆蓋返
    page._on_code_activated('HK.00700 騰訊控股')   # dropdown item 文字（連名）→ 欄應該淨返 CODE
    wait_for(app, lambda: '騰訊控股' in page.code_hint_lbl.text(), what='code hint after activate')
    check('completer activation: item 連名，但 code_edit 淨返 CODE + hint 顯示名稱',
          page.code_edit.text() == 'HK.00700')

    # hint 中文跟 GUI 語言：zh_cn → 簡體「腾讯控股」、返 zh_hk → 繁體（retranslate 同步更新 hint）
    page.retranslate('zh_cn')
    check('hint follows GUI lang (zh_cn → simplified name)', '腾讯控股' in page.code_hint_lbl.text())
    page.retranslate('zh_hk')
    check('hint follows GUI lang (zh_hk → traditional name)', '騰訊控股' in page.code_hint_lbl.text())

    # quote 預填：debounce → get_market_snapshot（fake: 380 / 每手 100）→ price=最新價、qty=每手
    wait_for(app, lambda: page.price_edit.text() == '380' and page.qty_edit.text() == '100',
             what='quote prefill price/qty')
    check('price auto-prefilled with latest price, qty with lot_size', True)
    # 全流市價（kline stream 模式）：subscribe 之後 OpenD 主動推 tick → price 自動更新
    check('subscribe active for current code (全流市價)', page._worker._qctx.subscribed == ['HK.00700'])
    # 用戶實測「沒有串流」錯覺：夜市/價冇郁時睇唔到更新 → hint 如實顯示串流狀態
    check('hint shows stream indicator (串流中 ✅)', '串流中' in page.code_hint_lbl.text())
    page._worker._price_handler._emit('HK.00700', 381.5)
    wait_for(app, lambda: page.price_edit.text() == '381.5', what='push tick updates price')
    check('price push tick auto-updates price field', True)
    # 用戶手動改過 price（textEdited 先計）→ 其後 push 唔覆蓋手動值（但 hint 照更新）
    page.price_edit.setText('999'); page._price_manual = True
    page._worker._price_handler._emit('HK.00700', 382.0)
    wait_for(app, lambda: (page._quote or {}).get('last_price') == 382.0, what='tick received')
    check('manual price not overwritten by push', page.price_edit.text() == '999')
    page._price_manual = False; page.price_edit.setText('380')   # 復位，唔阻後面段

    # 用戶要求：打「NVDA」→ US.NVDA + 中文名（index name_zh 欄）；hint 跟語言：繁/簡/英
    page.code_edit.setText('NVDA')
    wait_for(app, lambda: 'US.NVDA' in _codes(), what='fuzzy "NVDA"')
    page._on_code_activated('US.NVDA 英偉達')
    wait_for(app, lambda: '英偉達' in page.code_hint_lbl.text(), what='NVDA hint (zh_hk s2t)')
    check('fuzzy "NVDA" → US.NVDA + 中文名 hint（zh_hk → 英偉達）', True)
    page.retranslate('zh_cn')
    check('NVDA hint zh_cn → 英伟达（name_zh canonical）', '英伟达' in page.code_hint_lbl.text())
    page.retranslate('en')
    check('NVDA hint en → name_en', 'NVIDIA Corp' in page.code_hint_lbl.text())
    page.retranslate('zh_hk')

    # 搜尋經注入嘅本地 index — 唔打網絡（qctx 只被 quote 用，snap_count 唔因搜尋增加）
    n_snap = page._worker._qctx.snap_count
    n_search = page._worker._search_dir.search_count
    page.code_edit.setText('TSLA')
    wait_for(app, lambda: 'US.TSLA' in _codes(), what='completer candidates for "TSLA"')
    check('fuzzy search "TSLA" → candidate US.TSLA', True)
    time.sleep(0.3)
    check('search served from local index (no network)',
          page._worker._search_dir.search_count > n_search
          and page._worker._qctx.snap_count == n_snap)

    # 用戶要求：中文唔可以送入 place_order — code 欄留緊中文 → 落單被 _CODE_RE 擋掉
    n_placed = len(page._worker._ctx.placed)
    page.code_edit.setText('恒指主連')
    page.buy_btn.click()
    wait_for(app, lambda: '❌' in page.order_result_lbl.text(), what='Chinese code rejected')
    check('Chinese name in code field → order rejected (no place_order sent)',
          '代碼無效' in page.order_result_lbl.text() and len(page._worker._ctx.placed) == n_placed)

    # ── Part 5.5：nav 右鍵「彈出視窗」（用戶要求）— 同一 page 實例搬出入窗，關窗搬返 ──
    print('── Part 5.5: nav right-click popup window ──')
    check('nav 按鈕 + 子選單按鈕都帶 context-menu policy (右鍵選單)',
          all(b.contextMenuPolicy() == Qt.CustomContextMenu
              for b in list(win.nav_btns.values()) + list(win.menu_btns.values())))
    # 用戶要求（MINOR CHANGE）：K線/全功能/標的列表 收埋喺「測試」子選單；連綫測試喺「設定」
    check('nav 分組：直接按鈕得 首頁/行情/FUTU 交易/標的收藏/指標/策略管理；測試/設定 為子選單',
          set(win.nav_btns) == set(NAV_DIRECT)
          and {'strategies'} <= set(win.nav_btns)   # ticket #21 加咗策略管理，斷言跟 registry
          and set(win.menu_btns) == {'test', 'settings'}
          # 用戶要求（ticket #20）：主菜單唔再要有「預留頁面」佔位
          and not hasattr(win, 'reserved_btn')
          and [a.objectName() for a in win.menu_btns['test'].menu().actions()]
              == ['nav_kline', 'nav_fulltest', 'nav_symbol_list']
          and [a.objectName() for a in win.menu_btns['settings'].menu().actions()]
              == ['nav_connection'])
    # 用戶要求（MINOR CHANGE）：語言 = 三個 exclusive 按鈕，唔准 dropdown
    check('語言三按鈕（exclusive，冇 lang_combo）',
          set(win.lang_btns) == {'zh_hk', 'zh_cn', 'en'} and not hasattr(win, 'lang_combo')
          and sum(1 for b in win.lang_btns.values() if b.isChecked()) == 1)
    win.lang_btns['en'].click()
    app.processEvents()
    check('lang btn → 全 shell 跟 EN + checked 遷移',
          win._lang == 'en' and win.menu_btns['test'].text() == 'Tests'
          and win.lang_btns['en'].isChecked() and not win.lang_btns['zh_hk'].isChecked())
    win.lang_btns['zh_hk'].click()
    app.processEvents()
    kline = win.pages['kline']
    win._popup_page('kline')
    check('popup window created', 'kline' in win._popups)
    check('page reparented out of stack (同一實例，唔開第二份)',
          win.stack.indexOf(kline) < 0 and kline.parent() is win._popups['kline'])
    app.processEvents()
    # 用戶實測「彈出窗空白」根因：Qt reparent 之後 widget 自動隱藏 — 要主動 show()
    check('popped-out page visible in popup window (唔係空白窗)', kline.isVisible())
    # 用戶要求：左鍵永遠正常切換 — 彈出緊都先搬返入 shell 再選中（子選單項 = 正常切換路徑）
    win.page_actions['kline'].trigger()
    app.processEvents()
    check('子選單項 → 搬返入 shell 正常切換（唔係彈窗）+ 分組高亮',
          'kline' not in win._popups and win.stack.currentWidget() is kline
          and win.page_actions['kline'].isChecked() and win.menu_btns['test'].isChecked())
    win._popup_page('kline')        # 再彈出 → 測試關窗搬返路徑
    win._popups['kline'].close()    # 關閉 → closeEvent 搬返入 stack 原位置
    app.processEvents()
    check('close popup → page returns to stack at original index',
          'kline' not in win._popups and win.stack.indexOf(kline) == PAGE_KEYS.index('kline'))
    win.page_actions['kline'].trigger()   # 搬返後 index mapping 正常 → 可以再揀返呢頁
    check('returned page selectable again (stack index mapping 修復)',
          win.stack.currentWidget() is kline and win.page_actions['kline'].isChecked())

    # ── Part 6：Live OpenD probe (optional, read-only)──
    print('── Part 6: live OpenD probe ──')
    try:
        with open(ftp_mod.CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f).get('futu', {})
        host, port = str(cfg.get('host', '')), int(cfg.get('port', 0))
    except Exception:
        host, port = '', 0
    if '--no-live' in sys.argv:
        print('  ⚠️ skipped — --no-live（hermetic mode，唔開真 SDK）')
    elif not (host and port):
        print('  ⚠️ skipped — no futu.host/port in config.json')
    else:
        # real SDK path: factory None → lazy import OpenSecTradeContext inside worker
        page._worker._ctx_factory = None
        page._worker._qctx_factory = None   # quote ctx 都返真 SDK — live probe 全程真路徑
        try:
            # 先斷 fake session — 避免 stale _connected / stale table data 誤判 live 結果
            page._submit('disconnect')
            wait_for(app, lambda: not page._connected, what='fake disconnect', timeout=5)
            check('fake disconnect → ctx.close()', bool(getattr(fake_ctx, 'closed', False)))
            page.host_edit.setText(host); page.port_edit.setText(str(port))
            page._on_connect()
            # _on_connect 同步 set busy text → 之後 label 只會變 ✅（成功）或 ❌（失敗）
            wait_for(app, lambda: '✅' in page.conn_status_lbl.text() or '❌' in page.conn_status_lbl.text(),
                     what='live connect', timeout=20)
            if '❌' in page.conn_status_lbl.text():
                print(f"  ⚠️ live OpenD unreachable ({host}:{port}) — skipped (not a failure)")
            else:
                check('live connect ok', True)
                # default SIM env; if no ACTIVE SIM account, switch to REAL
                if page.acc_table.rowCount() == 0:
                    page.env_real_btn.click()
                    wait_for(app, lambda: page.acc_table.rowCount() >= 1, what='live env REAL', timeout=5)
                n = page.acc_table.rowCount()
                print(f"  ℹ️ live accounts (ACTIVE + env filtered): {n}")
                if n >= 1:
                    page.acc_table.selectRow(0)
                    wait_for(app, lambda: page._acc is not None, what='live account select')
                    page._refresh('positions')
                    wait_for(app, lambda: page.pos_table.rowCount() > 0 or '❌' in page.accinfo_lbl.text(),
                             what='live positions', timeout=15)
                    print(f"  ℹ️ live positions rows: {page.pos_table.rowCount()}")
                # read-only — no orders placed against real OpenD
        finally:
            page._retry_timer.stop()   # 🤖 停自愈重試 — 唔想清理期間 timer 再 fire 過（重連會 restart worker）
            page._worker._ctx_factory = _fake_factory
            page._worker._qctx_factory = _fake_qfactory

    # E2E 唔 run app.exec() → aboutToQuit 唔會 fire → 要手停所有 background thread，否則 exit 時：
    # normal shutdown → C-level exit(127)；os._exit → 運行中嘅 C++ QThread 被 kill mid-call → 概率性 SIGSEGV。
    # 全部 thread 停咗之後 sys.exit(code) 就係乾淨 + 可靠（實測穩定）。
    page._worker.stop_and_wait(5000)
    time.sleep(0.5)   # futu SDK close() 之後內部 background thread 仲要 wind down
    for _p in list(win.pages.values()):   # 唔好 hardcode 頁名：新增頁（backtest/quant…）一律要停，否則又漏 thread
        if hasattr(_p, '_on_app_quit'):   # 長跑 LoopThread 嘅頁（C++ QThread，threading.enumerate 睇唔到）
            _p._on_app_quit()   # → 原 closeEvent 清理鏈：request_shutdown → thread.wait(3000)（同步，回傳時已停）
    win.close()
    app.processEvents()
    if FAILS:
        print(f'\n❌ E2E FAILED: {FAILS} check(s)')
        sys.exit(1)
    print('\n✅ E2E PASSED — all checks green')
    sys.exit(0)


if __name__ == '__main__':
    main()

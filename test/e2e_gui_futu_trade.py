"""E2E GUI test — FUTU trade page (Page 4) full flow.

Run: python test/e2e_gui_futu_trade.py   (from project root; needs a display / offscreen Qt —
     QT_QPA_PLATFORM=offscreen is set automatically. 用直接執行而非 -m：stdlib `test` package 會 shadow 本地目錄)

Flow (all via FakeCtx / FakeQuoteCtx injected into the worker thread through _ctx_factory):
1. Page construction + i18n retranslate (3 languages)
2. connect → account list — 帳戶表只顯示 ACTIVE（DISABLED 被 filter 掉）；
   模擬/實盤 toggle 切換環境，所有資料跟住重新 query（唔混模擬同實盤）；
   市場過濾後為空 → 數據區清空 + 提示
3. place_order：買入/賣出兩鍵 — SIMULATE BUY success → today's orders auto-refresh;
   cancel selected order; positions + accinfo refresh
4. REAL path（內置解鎖欄，無彈窗）：REAL env → 解鎖欄顯示；空密碼 → need_unlock 提示；
   錯密碼 → auto-unlock chain 失敗、唔落單；正確密碼 → unlock 成功後 pending order 自動落單；
   切返 SIM → 解鎖欄隱藏（QMessageBox confirm dialog 全程 monkeypatch auto-Yes）
5. code fuzzy input：code_edit debounce → worker stock_search（FakeQuoteCtx）→ QCompleter
   候選「MARKET.CODE」+ 名稱 hint + per-market cache（第二次搜尋唔再 fetch）
6. Live OpenD probe (optional): if config.json has futu.host/port and OpenD is running,
   actually connect once to verify the real SDK path (no orders placed — read-only queries only).
   `--no-live` skips it for fully hermetic runs (CI without OpenD).

Exit code 0 = all pass; non-zero = at least one check failed.
"""
import json
import os
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402
import pandas as pd  # noqa: E402

import gateway.pages.futu_trade_page as ftp_mod  # noqa: E402
from futu import RET_OK  # noqa: E402
from gateway.app import OneGateWindow  # noqa: E402


class FakeCtx:
    """In-memory stand-in for OpenSecTradeContext — matches the REAL SDK call surface
    (ret_code + DataFrame tuples) so the worker code path is exercised unmodified."""

    def __init__(self, host='fake', port=0):
        self.host = host
        self.port = int(port)
        self.placed = []
        self.canceled = []
        self.closed = False
        self._unlock_pwd = 'secret'
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

    def unlock_trade(self, password):
        # 真 SDK 契約：成功 (RET_OK, '')；失敗 (非0, 錯誤原因字符串) — fake 跟足
        if str(password) == self._unlock_pwd:
            return RET_OK, ''
        return -2, 'password error'

    def place_order(self, price=0.0, qty=0, code='', trd_side=None, order_type=None,
                    trd_env=None, acc_id=None, time_in_force=None):
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
        # worker 用 modify_order(ModifyOrderOp.CANCEL, ...) 撤單
        self.canceled.append((str(trd_env), str(order_id)))
        return RET_OK, pd.DataFrame()

    def cancel_all_order(self, trd_env=None, acc_id=None):
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
    """In-memory stand-in for OpenQuoteContext — get_stock_basicinfo 回固定小數據集
    （代碼模糊搜尋測試用；真 SDK 會回該 market/type 全部行）。"""

    _DATA = {
        'HK': [('00700', '騰訊控股'), ('00005', '匯豐控股'), ('09988', '阿里巴巴-W')],
        'US': [('AAPL', 'Apple Inc.'), ('TSLA', 'Tesla Inc.')],
    }

    def __init__(self, host='fake', port=0):
        self.host = host
        self.port = int(port)
        self.closed = False
        self.fetch_count = 0   # get_stock_basicinfo call count — E2E verifies per-market cache

    def get_stock_basicinfo(self, market, stock_type='STOCK', code_list=None):
        self.fetch_count += 1
        rows = self._DATA.get(str(market), [])   # SH/SZ 無 fake 數據 → 空（真 SDK 會回 A股）
        return RET_OK, pd.DataFrame(rows, columns=['code', 'name'])

    def close(self):
        self.closed = True


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
    page.retranslate('zh_hk')

    # theme propagation: apply_theme → listener → 頁面級 QSS 跟住換（light/dark）
    from gateway.theme import apply_theme
    apply_theme('light')
    wait_for(app, lambda: '#E8EAED' in page.styleSheet(), what='theme light QSS', timeout=3)
    check('theme light → page QSS follows', True)
    apply_theme('dark')
    wait_for(app, lambda: '#1E1F22' in page.styleSheet(), what='theme dark QSS', timeout=3)
    check('theme dark → page QSS follows', True)

    # inject fake SDK contexts (worker calls factories with host=/port= kwargs)
    def _fake_factory(host, port):
        return FakeCtx(host=host, port=int(port))

    def _fake_qfactory(host, port):
        return FakeQuoteCtx(host=host, port=int(port))
    page._worker._ctx_factory = _fake_factory
    page._worker._qctx_factory = _fake_qfactory   # 代碼模糊搜尋 — fake quote ctx（hermetic）

    # ── Part 2：connect → account list（ACTIVE filter + SIM/REAL toggle）──
    print('── Part 2: connect + env/market filters ──')
    page._on_connect()
    wait_for(app, lambda: page._connected, what='connect')
    check('connect ok', '✅' in page.conn_status_lbl.text())

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

    # ── Part 4：REAL path — 內置解鎖欄（無彈窗）+ auto-unlock chain ──
    print('── Part 4: REAL inline unlock + order flow ──')
    page.env_real_btn.click()
    wait_for(app, lambda: page._acc is not None and str(page._acc.get('trd_env')) == 'REAL',
             what='real auto select (2nd)')
    check('REAL account label uses accent color warning', 'color:' in page.acc_sel_lbl.styleSheet())
    # 用 isHidden()（唔係 isVisible()）— 頁面未必係 stacked layout 當前頁，parent hidden 會令 isVisible 永遠 False
    check('unlock row visible in REAL env', not page.unlock_pwd_edit.isHidden() and not page.unlock_btn.isHidden())

    # fill form (valid SELL)；REAL _on_place 全部經 confirm dialog → 先 monkeypatch auto-Yes
    page.code_edit.setText('HK.00700'); page.qty_edit.setText('100'); page.price_edit.setText('380')
    real_question = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    try:
        # 空密碼 → need_unlock 提示（confirm 已彈過；唔 submit 任何 worker op）
        n_placed_before = len(fake_ctx.placed)
        page._on_place('SELL')
        wait_for(app, lambda: '解鎖' in page.order_result_lbl.text(), what='need-unlock gate')
        check('empty pwd → need_unlock hint', True)

        # 錯密碼 → auto-unlock chain 失敗 → 唔落單、按鍵還原
        page.unlock_pwd_edit.setText('wrong')
        page.sell_btn.click()
        wait_for(app, lambda: '❌' in page.order_result_lbl.text(), what='unlock fail blocks order')
        check('wrong pwd → unlock fail shown', not page._unlocked)
        check('no order placed on unlock fail', len(fake_ctx.placed) == n_placed_before)
        check('buttons re-enabled after failed chain', page.sell_btn.isEnabled() and page.buy_btn.isEnabled())

        # 正確密碼 → auto-unlock 成功 → pending place_order 自動落單（confirm 只彈過一次）
        page.unlock_pwd_edit.setText('secret')
        page.buy_btn.click()
        wait_for(app, lambda: '900002' in page.order_result_lbl.text(), what='auto unlock + place')
        check('correct pwd → unlocked', page._unlocked)
        check('pending order auto-placed after unlock (side=BUY)', fake_ctx.placed[-1]['trd_side'] == 'BUY')

        # 已解鎖 → 直接落單（唔再經 unlock chain）
        page.sell_btn.click()
        wait_for(app, lambda: '900003' in page.order_result_lbl.text(), what='direct place after unlock')
        check('2nd REAL order placed directly (side=SELL)', fake_ctx.placed[-1]['trd_side'] == 'SELL')
    finally:
        QMessageBox.question = real_question

    # 切返 SIM → 解鎖欄隱藏
    page.env_sim_btn.click()
    wait_for(app, lambda: page.unlock_pwd_edit.isHidden(), what='unlock row hide')
    check('unlock row hidden in SIM env', True)

    # ── Part 5：code fuzzy input — QCompleter + worker stock_search（FakeQuoteCtx）──
    print('── Part 5: code fuzzy input (QCompleter) ──')
    page.code_edit.clear()   # 清走 Part 4 嘅 HK.00700
    wait_for(app, lambda: not page._search_timer.isActive(), what='debounce settle', timeout=2.0)

    # '700' → debounce 300ms → worker stock_search（market combo = All → HK/US/SH/SZ）→ 候選 HK.00700
    page.code_edit.setText('700')
    wait_for(app, lambda: 'HK.00700' in page.code_completer.model().stringList(),
             what='completer candidates for "700"')
    check('fuzzy search "700" → candidate HK.00700', True)

    # 模擬用戶喺 completer 揀咗候選 → code_edit 填入 + hint 顯示名稱
    page._on_code_activated('HK.00700')
    wait_for(app, lambda: '騰訊控股' in page.code_hint_lbl.text(), what='code hint after activate')
    check('completer activation fills code + name hint', page.code_edit.text() == 'HK.00700')

    # 第二次搜尋（TSLA）→ per-market cache hit — fetch_count 唔再增加（首次 All = HK/US/SH/SZ 4 次）
    wait_for(app, lambda: page._worker._qctx is not None, what='lazy qctx created')
    n_fetches = page._worker._qctx.fetch_count
    check('first search fetched all markets (cache primed)', n_fetches == 4)
    page.code_edit.setText('TSLA')
    wait_for(app, lambda: 'US.TSLA' in page.code_completer.model().stringList(),
             what='completer candidates for "TSLA"')
    check('fuzzy search "TSLA" → candidate US.TSLA', True)
    time.sleep(0.5)   # 俾 cache-hit 搜尋完成（唔會再 fetch）
    check('second search served from per-market cache (no re-fetch)',
          page._worker._qctx.fetch_count == n_fetches)

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
            page._worker._ctx_factory = _fake_factory
            page._worker._qctx_factory = _fake_qfactory

    # E2E 唔 run app.exec() → aboutToQuit 唔會 fire → 要手停所有 background thread，否則 exit 時：
    # normal shutdown → C-level exit(127)；os._exit → 運行中嘅 C++ QThread 被 kill mid-call → 概率性 SIGSEGV。
    # 全部 thread 停咗之後 sys.exit(code) 就係乾淨 + 可靠（實測穩定）。
    page._worker.stop_and_wait(5000)
    time.sleep(0.5)   # futu SDK close() 之後內部 background thread 仲要 wind down
    for _key in ('kline', 'fulltest'):   # 嵌入頁嘅 LoopThread（C++ QThread，threading.enumerate 睇唔到）
        _p = win.pages.get(_key)
        if _p is not None and hasattr(_p, '_on_app_quit'):
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

"""E2E — FUTU 交易頁（futu_trade_page）。

- **預設（offscreen hermetic）**：stub `sys.modules['futu']` 做 FakeCtx，驗完整 UI flow —
  One Gate 註冊 / connect→accounts / 市場過濾 / SIM 下單 / orders 表自動刷新 / 撤選定單 /
  positions+accinfo / REAL 解鎖+確認框 path（monkeypatch auto-Yes）/ disconnect ctx.close；
  加 i18n 三語 retranslate（t() fail-fast）+ theme 傳播。零網絡。
- **live read-only**（port 11111 開緊自動跑，或 `--live` 強制）：真 SDK 連線 + accounts +
  positions/accinfo query；**絕不落單 / 撤單 / unlock**。

用法：`python test/e2e_gui_futu_trade.py [--live]`（QT_QPA_PLATFORM=offscreen 自動設）。
"""
import os
import socket
import sys
import time
import types

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

# Windows GBK console 印唔到 emoji — 強制 UTF-8（Linux/Docker 上係 no-op）
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_PASS = 0
_FAIL = 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f'  ✅ {name}')
    else:
        _FAIL += 1
        print(f'  ❌ {name}')


def wait_for(app, cond, timeout=10.0, what=''):
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise TimeoutError(f'timeout waiting for {what}')
        app.processEvents()
        time.sleep(0.01)


# ── Fake futu module（offscreen hermetic 用；欄位名對住真 OpenD 回傳核實過）──

def _build_fake_module():
    import pandas as pd

    class _NS:
        pass

    OrderType = _NS()
    for k in ('NORMAL', 'MARKET', 'AUCTION_LIMIT', 'LIMIT_IF_TOUCHED'):
        setattr(OrderType, k, k)
    TimeInForce = _NS()
    for k in ('DAY', 'GTC', 'IOC'):
        setattr(TimeInForce, k, k)
    TrdSide = _NS()
    TrdSide.BUY, TrdSide.SELL = 'BUY', 'SELL'
    ModifyOrderOp = _NS()
    ModifyOrderOp.CANCEL = 'CANCEL'

    ACCS = [
        {'acc_id': 1001, 'trd_env': 'REAL', 'acc_type': 'MARGIN',
         'trdmarket_auth': '[HK, US]', 'acc_status': 'ACTIVE'},
        {'acc_id': 2002, 'trd_env': 'SIMULATE', 'acc_type': 'CASH',
         'trdmarket_auth': '[HK]', 'acc_status': 'ACTIVE'},
        {'acc_id': 3003, 'trd_env': 'REAL', 'acc_type': 'CASH',
         'trdmarket_auth': '[US]', 'acc_status': 'DISABLED'},
    ]

    class FakeCtx:
        def __init__(self, host='127.0.0.1', port=11111):
            self.host, self.port = host, int(port)
            self.closed = False
            self.placed = []
            self.cancelled = []

        def get_acc_list(self):
            return 0, pd.DataFrame(ACCS)

        def unlock_trade(self, password=None, **kw):
            if password == 'secret':
                return 0, ''
            return -1, 'wrong trade password'

        def place_order(self, price, qty, code, trd_side, order_type='NORMAL',
                        adjust_limit=0, trd_env='REAL', acc_id=0, time_in_force='DAY', **kw):
            oid = 900001 + len(self.placed)   # 先算 id 再 append（避免 off-by-one）
            self.placed.append({'price': price, 'qty': qty, 'code': code,
                                'side': str(trd_side), 'env': trd_env})
            return 0, pd.DataFrame({'order_id': [oid]})

        def order_list_query(self, trd_env='REAL', acc_id=0, **kw):
            rows = [{'code': p['code'], 'stock_name': 'FAKE', 'trd_side': p['side'],
                     'order_type': 'NORMAL', 'order_status': 'SUBMITTED',
                     'order_id': 900001 + i, 'qty': p['qty'], 'price': p['price'],
                     'dealt_qty': 0, 'dealt_avg_price': float('nan'),
                     'create_time': '2026-10-07 15:00:00'}
                    for i, p in enumerate(self.placed)]
            return 0, pd.DataFrame(rows)

        def position_list_query(self, trd_env='REAL', acc_id=0, **kw):
            rows = [
                {'code': 'HK.00700', 'stock_name': '騰訊控股', 'position_market': 'HK',
                 'qty': 100, 'can_sell_qty': 100, 'cost_price': 380.0, 'market_val': 42500.0,
                 'pl_val': 4500.0, 'pl_ratio': 0.1184, 'currency': 'HKD'},
                {'code': 'US.AAPL', 'stock_name': 'Apple Inc.', 'position_market': 'US',
                 'qty': 10, 'can_sell_qty': 10, 'cost_price': 200.0, 'market_val': 2350.0,
                 'pl_val': 350.0, 'pl_ratio': 0.175, 'currency': 'USD'},
            ]
            return 0, pd.DataFrame(rows)

        def accinfo_query(self, trd_env='REAL', acc_id=0, **kw):
            row = {'total_assets': 123456.78, 'cash': 98765.43, 'market_val': 24691.35,
                   'power': 200000.0, 'available_funds': 50000.0,
                   'avl_withdrawal_cash': float('nan')}
            return 0, pd.DataFrame([row])

        def modify_order(self, op, order_id, qty=0, price=0, **kw):
            self.cancelled.append(int(order_id))
            return 0, ''

        def cancel_all_order(self, trd_env='REAL', acc_id=0, **kw):
            return 0, ''

        def close(self):
            self.closed = True

    mod = types.ModuleType('futu')
    mod.RET_OK = 0
    mod.OpenSecTradeContext = FakeCtx
    mod.OrderType = OrderType
    mod.TimeInForce = TimeInForce
    mod.TrdSide = TrdSide
    mod.ModifyOrderOp = ModifyOrderOp
    return mod, FakeCtx


def _live_available():
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(('127.0.0.1', 11111))
        return True
    except Exception:
        return False
    finally:
        s.close()


def main():
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv)

    # ── Part 1：One Gate 註冊（gateway.app import 鏈會帶真 futu — 冇問題，之後先 stub）──
    print('== Part 1: One Gate registration ==')
    from gateway.app import PAGE_KEYS, OneGateWindow
    check("PAGE_KEYS 有 'futu_trade'", 'futu_trade' in PAGE_KEYS)
    win = OneGateWindow()
    page = win.pages.get('futu_trade')
    check('page instance 存在', page is not None and page.objectName() == 'futu_trade_page')
    check("nav 按鈕 'nav_futu_trade' 存在",
          'futu_trade' in win.nav_btns and win.nav_btns['futu_trade'].objectName() == 'nav_futu_trade')

    # ── Part 2：offscreen hermetic flow（fake futu module）──
    print('== Part 2: offscreen hermetic flow (FakeCtx) ==')
    fake_mod, FakeCtx = _build_fake_module()
    real_futu = sys.modules.get('futu')
    sys.modules['futu'] = fake_mod
    try:
        # connect → accounts
        page._on_connect()
        wait_for(app, lambda: page._connected, what='connect')
        check('connect ok + 3 accounts', page.acc_table.rowCount() == 3)
        check('conn status ✅', '✅' in page.conn_status_lbl.text())

        # 市場過濾（client-side，唔使重連）
        page.market_combo.setCurrentText('US')
        wait_for(app, lambda: page.acc_table.rowCount() == 2, what='market filter US')
        check("市場過濾 US → 2 rows", all('US' in str(r.get('trdmarket_auth', '')) for r in page._acc_rows))
        page.market_combo.setCurrentText('All')
        wait_for(app, lambda: page.acc_table.rowCount() == 3, what='market filter All')

        # 選 SIMULATE 帳戶 → 下單（SIM 唔彈確認框）
        sim_idx = next(i for i, a in enumerate(page._acc_rows) if a['trd_env'] == 'SIMULATE')
        page.acc_table.selectRow(sim_idx)
        wait_for(app, lambda: page._acc is not None and page._acc['trd_env'] == 'SIMULATE',
                 what='account select')
        check('選定 SIMULATE 帳戶', True)

        # invalid form → ❌（code 空）
        page.qty_edit.setText('10')
        page.price_edit.setText('200.5')
        page._on_place()
        wait_for(app, lambda: '❌' in page.order_result_lbl.text(), what='invalid form msg')
        check('invalid form 攔截（code 空）', True)

        # SIM 下單成功 → orders 表自動刷新
        page.code_edit.setText('US.AAPL')
        page._on_place()
        wait_for(app, lambda: 'order_id=' in page.order_result_lbl.text(), what='place_order')
        check('SIM 下單成功（order_id=900001）', '900001' in page.order_result_lbl.text())
        wait_for(app, lambda: page.orders_table.rowCount() >= 1, what='orders auto refresh')
        check('orders 表有落咗嘅單', any(str(r.get('order_id')) == '900001' for r in page._order_rows))

        # 撤選定單（SIM 直接行）— wait UI label（signal 處理完先斷言，唔好 race worker 內部狀態）
        page.orders_table.selectRow(0)
        page._on_cancel_selected()
        fake_ctx = page._worker._ctx
        wait_for(app, lambda: '✅' in page.orders_status_lbl.text()
                 or '❌' in page.orders_status_lbl.text(), what='cancel result')
        check('撤選定單 → modify_order(CANCEL)',
              '✅' in page.orders_status_lbl.text() and fake_ctx.cancelled == [900001])

        # positions + accinfo
        page._refresh('positions')
        wait_for(app, lambda: page.pos_table.rowCount() == 2, what='positions')
        check('positions 表 2 rows', True)
        wait_for(app, lambda: 'total_assets=' in page.accinfo_lbl.text(), what='accinfo')
        check('accinfo label 有 total_assets/cash/power',
              all(k in page.accinfo_lbl.text() for k in ('total_assets=', 'cash=', 'power=')))

        # REAL path：解鎖 + 確認框（monkeypatch auto-Yes）+ 下單
        import gateway.pages.futu_trade_page as ftp_mod
        real_question = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        try:
            real_idx = next(i for i, a in enumerate(page._acc_rows) if a['trd_env'] == 'REAL')
            page.acc_table.selectRow(real_idx)
            wait_for(app, lambda: page._acc is not None and page._acc['trd_env'] == 'REAL',
                     what='real account select')
            check('REAL 帳戶 label 用 accent 色提醒', 'color:' in page.acc_sel_lbl.styleSheet())

            # 未解鎖 → REAL 下單攔截
            page.code_edit.setText('HK.00700')
            page._on_place()
            wait_for(app, lambda: '❌' not in page.order_result_lbl.text()
                     and '解鎖' in page.order_result_lbl.text(), what='need-unlock gate')
            check('REAL 未解鎖 → 攔截', True)

            # 錯密碼 → ❌；啱密碼 → ✅
            page.unlock_pwd_edit.setText('wrong')
            page._on_unlock()
            wait_for(app, lambda: '❌' in page.order_result_lbl.text(), what='unlock fail')
            check('解鎖錯密碼 → ❌', True)
            page.unlock_pwd_edit.setText('secret')
            page._on_unlock()
            wait_for(app, lambda: '✅' in page.order_result_lbl.text(), what='unlock ok')
            check('解鎖成功 → ✅', True)

            # REAL 下單（確認框 auto-Yes）
            page.qty_edit.setText('100')
            page.price_edit.setText('380')
            page._on_place()
            wait_for(app, lambda: '900002' in page.order_result_lbl.text(), what='real place_order')
            check('REAL 下單成功（確認框 path）', True)
        finally:
            QMessageBox.question = real_question

        # disconnect → ctx.close
        page.disconnect_btn.click()
        wait_for(app, lambda: not page._worker.connected, what='disconnect')
        check('disconnect → ctx.closed', fake_ctx is not None and fake_ctx.closed)
    finally:
        if real_futu is not None:
            sys.modules['futu'] = real_futu

    # ── Part 3：i18n 三語 + theme 傳播 ──
    print('== Part 3: i18n + theme ==')
    for lang in ('zh_hk', 'zh_cn', 'en'):
        page.retranslate(lang)   # t() fail-fast — key 缺咗會即刻炸
    check('retranslate 三語無 KeyError', True)
    win._on_lang_changed(win.lang_combo.findData('en'))
    check("nav 按鈕跟隨 EN", 'Futu Trade' in win.nav_btns['futu_trade'].text())

    from gateway.theme import apply_theme
    apply_theme('light')
    wait_for(app, lambda: '#E8EAED' in page.styleSheet(), what='theme light QSS')
    check('theme light → 頁面 QSS 跟隨', True)
    apply_theme('dark')
    wait_for(app, lambda: '#1E1F22' in page.styleSheet(), what='theme dark QSS')
    check('theme dark → 頁面 QSS 跟隨', True)

    # ── Part 4：live read-only（port 開緊先跑；絕不落單/撤單/unlock）──
    if '--live' in sys.argv or _live_available():
        print('== Part 4: live read-only (OpenD 127.0.0.1:11111) ==')
        page._on_connect()
        wait_for(app, lambda: page._connected or '❌' in page.conn_status_lbl.text(),
                 what='live connect', timeout=30)
        if not page._connected:
            print(f'  ⚠️ live connect 失敗（{page.conn_status_lbl.text()}）— skip live checks')
        else:
            check('live accounts > 0', page.acc_table.rowCount() >= 1)
            idx = next((i for i, a in enumerate(page._acc_rows) if a['trd_env'] == 'SIMULATE'), 0)
            page.acc_table.selectRow(idx)
            wait_for(app, lambda: page._acc is not None, what='live account select')
            page._refresh('positions')
            wait_for(app, lambda: page.accinfo_lbl.text() != '' and '❌' not in page.accinfo_lbl.text(),
                     what='live positions/accinfo', timeout=30)
            check('live accinfo 有內容（read-only）', True)
            print(f"     live: {page.acc_table.rowCount()} accounts, "
                  f"{page.pos_table.rowCount()} positions")
            page.disconnect_btn.click()
            wait_for(app, lambda: not page._worker.connected, what='live disconnect', timeout=30)
            check('live disconnect ok', True)
    else:
        print('== Part 4: live read-only — SKIP（127.0.0.1:11111 唔通）==')

    # ── summary ──
    total = _PASS + _FAIL
    print(f'\n{"=" * 50}\nRESULT: {_PASS}/{total} PASS, {_FAIL} FAIL')
    return 1 if _FAIL else 0


if __name__ == '__main__':
    sys.exit(main())

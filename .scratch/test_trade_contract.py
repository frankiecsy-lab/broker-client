# -*- coding: utf-8 -*-
"""契約測試 — modules/trade_base.py + FutuClient/IBClient 交易能力 + BrokerClient 能力分派（ticket #34b）。

Run: python .scratch/test_trade_contract.py   （from project root）
無 OpenD、無 TWS、無網絡：futu 用假 ctx、IB 用假 ib object，專測**契約形狀**同**誠實度**：
  · clamp_env/clamp_side 別名 + 唔識就如實回退（唔亂當買、唔知就当冇真錢）
  · TRADE 旗標兩家都 True；無交易能力就如實講「呢家券商唔支援交易」，**唔靜默轉券商**
  · TradeBase 未實作 → NotImplementedError（唔係靜默回 None）
  · 帳戶/訂單/持倉/資金/回執欄位一律照契約投影（少欄留空、唔編數）
  · 落單回執 ≠ 成交（futu status 空字串、IB status = PendingSubmit 類原文）
  · 解鎖狀態三檔（未解鎖/已解鎖/未知 known=False）；IB 冇解鎖機制 → 唔阻全自動
"""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402
from futu import RET_OK  # noqa: E402

from modules.broker import BrokerClient  # noqa: E402
from modules.futu_client import FutuClient  # noqa: E402
from modules.ib_client import IBClient  # noqa: E402
from modules.trade_base import (  # noqa: E402
    ACC_COLS, ACCINFO_KEYS, ORDER_COLS, POS_COLS, RECEIPT_KEYS, TradeBase,
    clamp_env, clamp_side,
)

FAILURES = []
PASSED = 0
LOOP = asyncio.new_event_loop()   # 一條 loop 行晒全部 async 檢查（避免 Lock 綁咗另一條 loop）


def check(name, ok):
    global PASSED
    print(('  ✅ ' if ok else '  ❌ ') + name, flush=True)
    if ok:
        PASSED += 1
    else:
        FAILURES.append(name)


def run(coro):
    return LOOP.run_until_complete(coro)


# ══════════ 1. 輸入正規化（clamp_env / clamp_side）══════════
print('\n[1] 環境/方向正規化', flush=True)
check("clamp_env('paper') → SIMULATE", clamp_env('paper') == 'SIMULATE')
check("clamp_env('live') → REAL", clamp_env('live') == 'REAL')
check("clamp_env('REAL') 大細階唔影響", clamp_env('real') == 'REAL')
check('clamp_env(唔識) → SIMULATE（唔知就当冇真錢）', clamp_env('火星') == 'SIMULATE')
check('clamp_env(None) → SIMULATE', clamp_env(None) == 'SIMULATE')
check("clamp_side('b') → BUY", clamp_side('b') == 'BUY')
check("clamp_side('short') → SELL", clamp_side('short') == 'SELL')
check('clamp_side(唔識) → None（唔亂當買）', clamp_side('hold') is None)
check('clamp_side(None) → None', clamp_side(None) is None)


# ══════════ 2. 能力係問出嚟（TRADE 旗標 + TradeBase 未實作）══════════
print('\n[2] 能力旗標', flush=True)
check('FutuClient.TRADE = True', FutuClient.TRADE is True)
check('IBClient.TRADE = True', IBClient.TRADE is True)


class _Bare(TradeBase):
    NAME = 'bare'


bare = _Bare()
check('TradeBase 預設 TRADE = False（能力要主動聲明）', getattr(_Bare, 'TRADE', False) is False)
_unimpl = []
# required kw-only 參數照契約傳齊 — 先測到「未實作」本身，唔係測到 TypeError
_ARGS = {'place_order': {'code': 'HK.00700', 'side': 'BUY', 'qty': 1},
         'cancel_order': {'order_id': '1'}}
for _m in ('trade_accounts', 'place_order', 'cancel_order', 'open_orders',
           'positions', 'account_info', 'unlock_status'):
    try:
        run(getattr(bare, _m)(**_ARGS.get(_m, {})))
        _unimpl.append(_m)
    except NotImplementedError as e:
        if _Bare.NAME not in str(e):
            _unimpl.append(_m + '(訊息冇券商名)')
    except Exception as e:
        _unimpl.append(f'{_m}({type(e).__name__})')
check('未實作 7 個方法一律 NotImplementedError（唔係靜默回 None）' + (f' ← {_unimpl}' if _unimpl else ''),
      not _unimpl)


# ══════════ 3. BrokerClient 能力分派（唔靜默 fallback）══════════
print('\n[3] BrokerClient 分派', flush=True)
bc = BrokerClient()
check('config.source.trade 已設定並通過 fail-fast 驗證',
      str(bc.config.get('source', {}).get('trade', '')).lower() in ('futu', 'ib'))
ok_f, name_f, why_f = bc.trade_supported('futu')
ok_i, name_i, why_i = bc.trade_supported('ib')
check('trade_supported(futu) → (True, futu, 空)', (ok_f, name_f, why_f) == (True, 'futu', ''))
check('trade_supported(ib) → (True, ib, 空)', (ok_i, name_i, why_i) == (True, 'ib', ''))
check('唔傳 broker 就用 config.source.trade', bc.trade_supported()[0] is True)


class _NoTrade:
    NAME = 'futu'   # 頂替 futu slot：模擬「呢家券商冇交易能力」


bc._clients['futu'] = _NoTrade()
ok, name, why = bc.trade_supported()
check('無交易能力 → (False, 名, 如實原因)', ok is False and name == 'futu' and '唔支援交易' in why)
client, err = bc._resolve_trade_broker(None)
check('_resolve_trade_broker 唔支援 → (None, 原因)，唔轉去另一家', client is None and '唔支援交易' in err)
status, data, msg = run(bc.place_order(code='HK.00700', side='BUY', qty=100, price=10, account='1'))
check('落單遇到唔支援嘅券商 → (False, None, 原因) 而唔係炸',
      status is False and data is None and '唔支援交易' in msg)
bc = BrokerClient()   # 還原真 client


class _Spy:
    NAME = 'futu'
    TRADE = True

    def __init__(self):
        self.got = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def place_order(self, **kw):
        self.got = kw
        return True, {'order_id': 'X1'}, ''


spy = _Spy()
bc._clients['futu'] = spy
status, data, msg = run(bc.place_order(code='HK.00700', side='sell', qty=500, price=38.5,
                                       account='5129', env='real', order_type='NORMAL', tif='DAY'))
check('dispatch 將全部 kw 原樣交俾 client（唔喺中間改值）',
      status is True and data == {'order_id': 'X1'}
      and spy.got == {'code': 'HK.00700', 'side': 'sell', 'qty': 500, 'price': 38.5,
                      'account': '5129', 'env': 'real', 'order_type': 'NORMAL', 'tif': 'DAY'})
bc._clients['futu'] = FutuClient(bc.config.get('futu', {}))   # 還原

bc.config['source']['trade'] = '唔存在'
try:
    bc._validate_config_sources()
    check('config source.trade 打錯字 → 開機即炸（fail-fast）', False)
except ValueError:
    check('config source.trade 打錯字 → 開機即炸（fail-fast）', True)


# ══════════ 4. FUTU 實作（假 ctx）══════════
print('\n[4] FutuClient 交易能力', flush=True)


class FakeCtx:
    """假 OpenSecTradeContext — 只記 kwargs + 回預設 DataFrame。"""

    def __init__(self, ret=RET_OK, err='err'):
        self.ret, self.err = ret, err
        self.calls = []

    def _r(self, df):
        """成功 → DataFrame；失敗 → 券商原文（真 OpenD 就係咁：ret 唔係 RET_OK 時第二個位係錯字串）。"""
        return (self.ret, df) if self.ret == RET_OK else (self.ret, self.err)

    def get_acc_list(self):
        self.calls.append(('get_acc_list', {}))
        return self._r(pd.DataFrame([{'acc_id': 5129, 'trd_env': 'Simulate', 'acc_type': 'MARGIN',
                                      'trdmarket_auth': 'HK|US', 'acc_status': 'ACTIVE',
                                      'uni_card_num': '敏感唔入表'}]))

    def place_order(self, **kw):
        self.calls.append(('place_order', kw))
        return self._r(pd.DataFrame([{'order_id': '8001', 'order_status': 'SUBMITTED'}]))

    def modify_order(self, op, oid, **kw):
        self.calls.append(('modify_order', {'op': op, 'oid': oid, **kw}))
        return self.ret, self.err

    def order_list_query(self, **kw):
        self.calls.append(('order_list_query', kw))
        return self._r(pd.DataFrame([{'order_id': '8001', 'code': 'HK.00700', 'stock_name': '騰訊',
                                      'trd_side': 'BUY', 'order_type': 'NORMAL', 'qty': 100,
                                      'dealt_qty': 0, 'price': 38.5, 'dealt_avg_price': 0,
                                      'order_status': 'SUBMITTED', 'create_time': '2026-10-09',
                                      'card_num': '敏感唔入表'}]))

    def position_list_query(self, **kw):
        self.calls.append(('position_list_query', kw))
        return self._r(pd.DataFrame([{'code': 'HK.00700', 'stock_name': '騰訊', 'position_market': 'HK',
                                      'qty': 1000, 'can_sell_qty': 1000, 'cost_price': 30.0,
                                      'market_val': 38500.0, 'pl_val': 8500.0, 'pl_ratio': 28.3,
                                      'currency': 'HKD'}]))

    def accinfo_query(self, **kw):
        self.calls.append(('accinfo_query', kw))
        return self._r(pd.DataFrame([{'total_assets': 100000.0, 'cash': 50000.0,
                                      'market_val': 50000.0, 'power': 200000.0,
                                      'available_funds': 90000.0}]))   # 故意少 avl_withdrawal_cash

    def close(self):
        self.calls.append(('close', {}))


fc = FutuClient({'host': '127.0.0.1', 'port': 11111})
ctx = FakeCtx()
fc._trd_ctx = ctx   # 直接注入 → 唔使真 OpenD

st, data, msg = run(fc.trade_accounts())
check('trade_accounts → 只回 ACC_COLS（敏感欄位唔入表）',
      st is True and tuple(sorted(data[0])) == tuple(sorted(ACC_COLS)) and data[0]['acc_id'] == 5129)

st, data, msg = run(fc.place_order(code='HK.00700', side='buy', qty='100', price='38.5',
                                   account='5129', env='paper'))
kw = ctx.calls[-1][1]
check('place_order 回執照 RECEIPT_KEYS 形狀',
      st is True and tuple(sorted(data)) == tuple(sorted(RECEIPT_KEYS)) and data['order_id'] == '8001')
check('落單回執 ≠ 成交：futu status 留空字串', st is True and data['status'] == '')
check("env='paper' → trd_env=Simulate（別名入契約）", kw['trd_env'] == 'SIMULATE')
check('qty/price 字串 → 數字', kw['qty'] == 100 and kw['price'] == 38.5)
st, _, msg = run(fc.place_order(code='HK.00700', side='hold', qty=1, price=1, account='5129'))
check('方向唔識 → 如實拒（唔亂當買）', st is False and '買賣方向唔識' in msg)
st, _, msg = run(fc.place_order(code='HK.00700', side='BUY', qty=0, price=1, account='5129'))
check('qty<=0 → 如實拒', st is False and '數量必須大於 0' in msg)
st, _, msg = run(fc.place_order(code='HK.00700', side='BUY', qty=100, price=1))
check('冇帳戶 → 如實報（唔亂揀帳戶落真錢）', st is False and '未指定交易帳戶' in msg)

st, data, msg = run(fc.open_orders(account='5129', env='real'))
check('open_orders → 只回 ORDER_COLS', st is True and tuple(sorted(data[0])) == tuple(sorted(ORDER_COLS)))
st, data, msg = run(fc.positions(account='5129'))
check('positions → 只回 POS_COLS', st is True and tuple(sorted(data[0])) == tuple(sorted(POS_COLS)))

st, info, msg = run(fc.account_info(account='5129'))
check('account_info 回券商俾到嘅欄（少欄如實少）',
      st is True and info.get('total_assets') == 100000.0 and 'avl_withdrawal_cash' not in info)

st, info, msg = run(fc.unlock_status(account='5129', env='real'))
check('探測到「不存在」類 → unlocked=True, known=True',
      st is True and info == {'unlocked': True, 'known': True, 'hint': ''})
ctx.ret, ctx.err = 'ERR', '交易未解锁'
st, info, msg = run(fc.unlock_status(account='5129'))
check('探測到解鎖錯誤 → unlocked=False + 提示去 OpenD GUI',
      st is True and info['unlocked'] is False and info['known'] is True and 'OpenD GUI' in info['hint'])
ctx.ret, ctx.err = 'ERR', 'OpenD 內部錯誤 blabla'   # 非 RET_OK 又唔係解鎖類 → 如實未知
st, info, msg = run(fc.unlock_status(account='5129'))
check('其他錯誤 → known=False（如實未知，唔扮已解鎖）',
      st is True and info['unlocked'] is False and info['known'] is False)

ctx.ret, ctx.err = 'ERR', 'err'
st, data, msg = run(fc.trade_accounts())
check('get_acc_list 失敗 → 如實回原文 + 清 ctx 等下一手重連',
      st is False and msg == 'err' and fc._trd_ctx is None)

fc2 = FutuClient({})
fc2._open_trd_ctx = lambda: (_ for _ in ()).throw(RuntimeError('OpenD 未開'))
st, data, msg = run(fc2.trade_accounts())
check('開唔到 ctx → (False, None, 原因原文)', st is False and 'OpenD 交易連線開唔到' in msg)


# ══════════ 5. IB 實作（假 ib）══════════
print('\n[5] IBClient 交易能力', flush=True)


class FakeIB:
    """⚠️ `managedAccounts` 必須是 **method**：真 ib_async 2.1.0 就是 method（ib.py:497）。
       從前這裡是假 list，掩住了 `list(<bound method>)` 的 TypeError（#36b 真 bug）。"""

    def __init__(self):
        self._managed = ['DU12345', '5566778']
        self.summary = [
            SimpleNamespace(account='DU12345', tag='AccountType', value='PAPER',
                            currency='', modelCode=''),
            SimpleNamespace(account='5566778', tag='AccountType', value='INDIVIDUAL',
                            currency='', modelCode=''),
            SimpleNamespace(account='5566778', tag='NetLiquidation', value='1',
                            currency='USD', modelCode='')]
        self.summary_fail = ''        # 非空 → reqAccountSummaryAsync 擲（權限不足 / Error 321）
        self.summary_hangs = False    # True → future 永不 resolve（Error 321 的真實形狀）
        self.summary_calls = 0
        self.cancelled = []
        self.placed = []
        self.open = [SimpleNamespace(
            contract=SimpleNamespace(symbol='HSI', account='', exchange='HKTS', currency='HKD'),
            order=SimpleNamespace(orderId=7, action='SELL', orderType='LMT', totalQuantity=2,
                                  lmtPrice=19000.0, auxPrice=0.0, account='5566778'),
            orderStatus=SimpleNamespace(status='Submitted', filled=0.0, remaining=2.0, avgFillPrice=0.0))]
        self.pos = [SimpleNamespace(
            contract=SimpleNamespace(symbol='HSI', exchange='HKTS', currency='HKD'),
            account='5566778', position='-2', avgCost='18800.5')]

    def isConnected(self):
        return True

    def managedAccounts(self):
        return list(self._managed)

    async def reqAccountSummaryAsync(self):
        self.summary_calls += 1
        if self.summary_hangs:
            await asyncio.Event().wait()      # 永不 resolve：無超時就會永久卡住
        if self.summary_fail:
            raise RuntimeError(self.summary_fail)
        return None

    def accountSummary(self, account=''):
        return list(self.summary)

    def placeOrder(self, contract, order):
        self.placed.append((contract, order))
        return SimpleNamespace(order=order, orderStatus=SimpleNamespace(status='PendingSubmit'))

    def openOrders(self):
        return list(self.open)

    def cancelOrder(self, order):
        self.cancelled.append(order)

    async def reqOpenOrdersAsync(self):
        return list(self.open)

    async def reqPositionsAsync(self):
        return list(self.pos)

    async def reqAccountUpdatesAsync(self, acct):
        return None

    def accountValues(self, acct):
        # AccountValue 有 currency 欄（_fields: account/tag/value/currency/modelCode）→
        # 假物件必須帶，否則測不到「幣種一直在手但從前被丟棄」這一段
        return [SimpleNamespace(tag='NetLiquidation', value='123456.78', currency='USD'),
                SimpleNamespace(tag='CashBalance', value='50000', currency='USD'),
                SimpleNamespace(tag='', value='空 tag 唔收', currency='USD'),
                SimpleNamespace(tag='BuyingPower', value='200000', currency='')]


ic = IBClient({'host': '127.0.0.1', 'port': 4001})
fib = FakeIB()
ic.ib = fib


async def _fake_connect():   # 假裝已連（唔打真 TWS）
    return None


ic._ensure_connected = _fake_connect


async def _fake_contract(code):   # 合約解析屬 P7（已有測試），呢度唔再打 TWS
    return SimpleNamespace(symbol=str(code), exchange='HKTS', currency='HKD')


ic._make_contract = _fake_contract

st, data, msg = run(ic.trade_accounts())
check('trade_accounts → 只回 ACC_COLS', st is True and tuple(sorted(data[0])) == tuple(sorted(ACC_COLS)))
envs = {r['acc_id']: r['trd_env'] for r in data}
check('DU 前綴 → SIMULATE、其餘 → REAL（編號慣例，如實推）',
      envs == {'DU12345': 'SIMULATE', '5566778': 'REAL'})
check('假物件的 managedAccounts 是 method（真 bug：list(<bound method>) → TypeError）',
      callable(getattr(fib, 'managedAccounts', None)))
check('AccountType 帶回 acc_type（IB 帳戶列表 API 真沒有類型欄位）',
      {r['acc_id']: r['acc_type'] for r in data} ==
      {'DU12345': 'PAPER', '5566778': 'INDIVIDUAL'})
check('非 AccountType 的 summary 行不混入 acc_type',
      all(r['acc_type'] in ('PAPER', 'INDIVIDUAL') for r in data))

fib.summary_fail = 'Unable to obtain contractual account summary (Error 321)'
st, data, msg = run(ic.trade_accounts())
check('AccountType 失敗 → 帳戶照常返回、acc_type 留空（不連帶失敗、不假裝齊全）',
      st is True and len(data) == 2 and all(r['acc_type'] == '' for r in data) and msg == '')
fib.summary_fail = ''
fib.summary_hangs = True
ic._ACCOUNT_TYPE_TIMEOUT = 0.05          # 真 5 秒會拖慢測試；要測的是「有超時、不卡死」
st, data, msg = run(ic.trade_accounts())
check('summary 永不 resolve（權限不足時的真實形狀）→ 超時後照常返回，不卡死整個呼叫',
      st is True and all(r['acc_type'] == '' for r in data))
fib.summary_hangs = False
del ic._ACCOUNT_TYPE_TIMEOUT
fib.accountSummary = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('summary 讀取失敗'))
st, data, msg = run(ic.trade_accounts())
check('accountSummary 讀取失敗 → 照樣留空（第二條 except 同樣 best-effort）',
      st is True and len(data) == 2 and all(r['acc_type'] == '' for r in data))
del fib.accountSummary

st, data, msg = run(ic.place_order(code='HK.HSImain', side='short', qty=2, price=19000,
                                   account='5566778', order_type='NORMAL'))
o = fib.placed[-1][1]
check('place_order 回執照 RECEIPT_KEYS + status = IB 原文（唔係成交）',
      st is True and tuple(sorted(data)) == tuple(sorted(RECEIPT_KEYS)) and data['status'] == 'PendingSubmit')
check("order_type NORMAL → LMT、action 用 SELL（short）", o.orderType == 'LMT' and o.action == 'SELL')
check('lmtPrice / account 落咗落 Order', o.lmtPrice == 19000.0 and o.account == '5566778')
st, data, msg = run(ic.place_order(code='HK.HSImain', side='BUY', qty=1, account='5566778'))
check('LMT 冇價 → 如實拒', st is False and '必須有價格' in msg)
st, data, msg = run(ic.place_order(code='HK.HSImain', side='BUY', qty=1, account='5566778',
                                   order_type='MARKET'))
check('MARKET 冇價 → 接受（MKT 唔使價）', st is True and fib.placed[-1][1].orderType == 'MKT')

st, data, msg = run(ic.cancel_order(order_id='7'))
check('cancel_order 由 openOrders 搵返 Order object 先撤', st is True and len(fib.cancelled) == 1)
st, data, msg = run(ic.cancel_order(order_id='999'))
check('搵唔到訂單 → 如實講（唔扮成功）', st is False and '搵唔到訂單 999' in msg)

st, rows, msg = run(ic.open_orders())
check('open_orders → 只回 ORDER_COLS', st is True and tuple(sorted(rows[0])) == tuple(sorted(ORDER_COLS)))
check('IB 冇嘅欄（create_time/stock_name）留空、唔編數',
      rows[0]['create_time'] == '' and rows[0]['stock_name'] == '' and rows[0]['order_status'] == 'Submitted')
check('IB 數值欄 string → 數字', rows[0]['qty'] == 2)
st, rows, msg = run(ic.open_orders(account='DU12345'))
check('多帳戶 → 只留揀咗嗰個', st is True and rows == [])

st, rows, msg = run(ic.positions(account='5566778'))
check('positions → 只回 POS_COLS', st is True and tuple(sorted(rows[0])) == tuple(sorted(POS_COLS)))
check('IB reqPositions 真冇嘅欄留空（market_val/pl_val/pl_ratio/can_sell_qty）',
      rows[0]['qty'] == -2 and rows[0]['cost_price'] == 18800.5
      and rows[0]['market_val'] == '' and rows[0]['pl_val'] == ''
      and rows[0]['pl_ratio'] == '' and rows[0]['can_sell_qty'] == '')

st, info, msg = run(ic.account_info(account='5566778'))
check('account_info → ACCINFO_KEYS 全齊形（tag 映射）+ currency',
      st is True and tuple(sorted(k for k in info if k != 'currency')) == tuple(sorted(ACCINFO_KEYS)))
check('有值 → 數字；無值 → 留空（唔扮 0）',
      info['total_assets'] == 123456.78 and info['cash'] == 50000
      and info['market_val'] == '' and info['available_funds'] == '')
check('currency 帶回，以 NetLiquidation 那筆為準（IB 從不告訴呼叫端資金幣種）',
      info['currency'] == 'USD')
_av = fib.accountValues
fib.accountValues = lambda acct: [SimpleNamespace(tag='CashBalance', value='1', currency='HKD')]
st, info, msg = run(ic.account_info(account='5566778'))
check('無 NetLiquidation 幣種 → 退回任何一筆已知幣種；無值欄仍留空',
      st is True and info['currency'] == 'HKD' and info['total_assets'] == '')
fib.accountValues = lambda acct: [SimpleNamespace(tag='CashBalance', value='1', currency='')]
st, info, msg = run(ic.account_info(account='5566778'))
check('全部幣種為空 → currency 空字串（如實未知，不猜 USD）',
      st is True and info['currency'] == '')
fib.accountValues = _av

st, info, msg = run(ic.unlock_status())
check('IB 冇解鎖機制 → unlocked=True + known=True（唔阻全自動）',
      st is True and info['unlocked'] is True and info['known'] is True)

ic.ib = None
st, data, msg = run(ic.trade_accounts())
check('未連 TWS → 如實報連線原因（唔靜默）', st is False and 'IB/TWS' in msg)


print('\n' + ('❌ 有 %d 項失敗：%s' % (len(FAILURES), FAILURES) if FAILURES
              else '✅ 全部交易契約測試通過（%d 項）' % PASSED), flush=True)
sys.exit(1 if FAILURES else 0)

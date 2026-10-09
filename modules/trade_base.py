# -*- coding: utf-8 -*-
"""券商「交易能力」契約 — 落單／訂單／持倉／資金嘅**單一介面**。

## 點解唔放落 `BrokerBase`
`BrokerBase` 係**行情**契約（get_kline / stream_kline），每家券商都有；交易能力唔係。
所以呢度係獨立 mixin + `TRADE` 能力旗標：
* 有交易能力嘅 client → `class FutuClient(BrokerBase, TradeBase)` 再 `TRADE = True`
* 冇嘅 client → 唔使繼承都得，`BrokerClient` 分派時查旗標，如實回「呢家券商唔支援交易」
⚠️ 方法**唔係 `@abstractmethod`**：`BrokerBase` 嘅子類一多，abstract 會即刻炸晒現有實作。
   呢度用 `raise NotImplementedError` — 冇實作就喺**呼叫時**如實噴錯，唔係喺 import 時。

## 回傳形狀（同 `BrokerBase.get_kline` 完全一致）
一律 `(status, data, message)`：成功 `(True, data, '')`，失敗 `(False, None, 原因原文)`。
原因原文係**券商嘅字**（唔 i18n）— 由 gateway 邊界決定點樣顯示，呢層唔扮似懂。

## ⚠️ 落單回執 ≠ 成交
`place_order` 成功只代表**請求已送出俾券商**。兩家都唔喺回執入面講成交與否（futu 只回
order_id；IB 回 PendingSubmit/Submitted）。所以 `RECEIPT_KEYS.status` 係**券商原文**，
有可能係空字串 = 呢家券商根本唔喺回執帶狀態 → 呼叫方必須如實講「已送出（未確認成交）」，
唔好當成交。（成交與否要由 `open_orders` 先睇到，呢個係兩家 API 嘅事實，唔係實作偷工。）

## 欄位定義喺呢度（唔係喺頁）
帳戶／訂單／持倉／資金嘅欄位名係**資料形狀**知識，唔屬排版：兩頁（交易管理頁、量化交易頁）
同兩家券商都要對同一個 dict 形狀，所以一來源喺呢度，頁只揀邊幾欄顯示。
"""

from abc import ABC

# ── 交易環境（用戶：「實盤模擬都可以」）──
TRADE_ENVS = ('SIMULATE', 'REAL')
TRADE_ENV_DEFAULT = 'SIMULATE'
_ENV_ALIAS = {'sim': 'SIMULATE', 'simulation': 'SIMULATE', 'paper': 'SIMULATE',
              'demo': 'SIMULATE', 'virtual': 'SIMULATE',
              'real': 'REAL', 'live': 'REAL', 'prod': 'REAL'}


def clamp_env(v):
    """任何輸入 → 合法 trd_env（唔識就退回模擬 — 唔知就當冇真錢，安全邊界）。
       ⚠️ 查表一律 lowercase（alias 表全細階 key）：uppercase 入面會靜默 mismatch → 實盤變模擬，係會錯錢嘅錯。"""
    s = str(v or '').strip().lower()
    return _ENV_ALIAS.get(s, TRADE_ENV_DEFAULT)


# ── 買賣方向（兩家都食 BUY/SELL；可唔可以沽空由券商決定，唔支援就會如實被拒單）──
SIDES = ('BUY', 'SELL')
_SIDE_ALIAS = {'buy': 'BUY', 'b': 'BUY', 'long': 'BUY', 'sell': 'SELL', 's': 'SELL', 'short': 'SELL'}


def clamp_side(v):
    """任何輸入 → 'BUY'/'SELL'；唔識回 None（呼叫方如實報「方向唔知」，唔好亂當買）。"""
    return _SIDE_ALIAS.get(str(v or '').strip().lower())


# ── 帳戶／訂單／持倉／資金欄位（對住真 OpenD 回傳核實過；card_num 等敏感欄位刻意唔入表）──
ACC_COLS = ('acc_id', 'trd_env', 'acc_type', 'trdmarket_auth', 'acc_status')
ORDER_COLS = ('order_id', 'code', 'stock_name', 'trd_side', 'order_type', 'qty',
              'dealt_qty', 'price', 'dealt_avg_price', 'order_status', 'create_time')
POS_COLS = ('code', 'stock_name', 'position_market', 'qty', 'can_sell_qty', 'cost_price',
            'market_val', 'pl_val', 'pl_ratio', 'currency')
ACCINFO_KEYS = ('total_assets', 'cash', 'market_val', 'power', 'available_funds',
                'avl_withdrawal_cash')
# 落單回執（兩家 normalize 後嘅共同形狀；status = 券商原文，可空 = 唔帶狀態）
RECEIPT_KEYS = ('order_id', 'code', 'side', 'qty', 'price', 'status')

# 數值欄 → 表格右對齊（方便比較）；欄名顯示經 i18n col_* keys
NUMERIC_COLS = frozenset({'qty', 'dealt_qty', 'price', 'dealt_avg_price', 'can_sell_qty',
                          'cost_price', 'market_val', 'pl_val', 'pl_ratio'})


class TradeBase(ABC):
    """交易能力 mixin。`TRADE` 係能力旗標（`BrokerClient` 分派前一定查）。"""

    TRADE = False   # 有實作嘅 client 必須 override 為 True

    async def trade_accounts(self):
        """→ (True, list[dict:ACC_COLS], '')。兩家都要如實講邊個帳戶、係咪模擬。"""
        raise NotImplementedError(f'{self.NAME} 未實作 trade_accounts')

    async def place_order(self, *, code, side, qty, price=None, account=None,
                          env=None, order_type=None, tif=None):
        """→ (True, dict:RECEIPT_KEYS, '')。⚠️ 成功 = 已送出，唔係已成交（見模組 docstring）。"""
        raise NotImplementedError(f'{self.NAME} 未實作 place_order')

    async def cancel_order(self, *, order_id, account=None, env=None):
        """→ (True, 回執原文, '')。"""
        raise NotImplementedError(f'{self.NAME} 未實作 cancel_order')

    async def open_orders(self, *, account=None, env=None):
        """→ (True, list[dict:ORDER_COLS], '')。"""
        raise NotImplementedError(f'{self.NAME} 未實作 open_orders')

    async def positions(self, *, account=None, env=None):
        """→ (True, list[dict:POS_COLS], '')。"""
        raise NotImplementedError(f'{self.NAME} 未實作 positions')

    async def account_info(self, *, account=None, env=None):
        """→ (True, dict:ACCINFO_KEYS, '')。"""
        raise NotImplementedError(f'{self.NAME} 未實作 account_info')

    async def unlock_status(self, *, account=None, env=None):
        """→ (True, {'unlocked':bool,'known':bool,'hint':str}, '')。
           冇「解鎖」機制嘅券商（IB）→ unlocked=True + known=True（唔阻全自動落單）。"""
        raise NotImplementedError(f'{self.NAME} 未實作 unlock_status')

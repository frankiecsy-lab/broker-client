import json
import logging
import os
import re
import asyncio
from abc import ABC, abstractmethod
from datetime import date, timedelta
import pandas as pd
from futu import *
from .registry import BROKERS   # 🤖 P2：券商名單一來源（加新 client 只改 registry.py）

logger = logging.getLogger(__name__)   # 🤖 P5：print → logging（app-level config 喺 modules/__init__.py）

pd.set_option('display.width', None)
pd.set_option('display.max_colwidth', None)
#pd.set_option('display.max_rows', None)
class BrokerClient(ABC):
    def __init__(self):
        self.config = self._get_config()
        # 🤖 P2：config-driven instantiation — 每個 client 按自己嘅 NAME 讀 config.json 對應 section（ib/futu）；
        #    加新 client 只改 registry.py，呢度自動 instantiate
        self._clients = {name: cls(self.config.get(name, {})) for name, cls in BROKERS.items()}
        # 向後兼容 alias（舊代碼可能直接引用）
        self.ib_client = self._clients["ib"]
        self.futu_client = self._clients["futu"]
        self._validate_config_sources()

    async def __aenter__(self):
        #print("BrokerClient Started")
        return self

    # 2. 離開時：統一清理共享連線
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # 🤖 P1+P2：循環調用每個 client 嘅 disconnect()（BrokerBase 預設 no-op；IB 會真斷共享連線）—
        #    加新 client 唔使改呢度。Futu 每次獨立連線已喺各 stream 內自行 close
        for client in self._clients.values():
            try:
                await client.disconnect()
            except Exception:
                pass
        return False  # 若內部噴錯，讓異常正常拋出

    def _validate_config_sources(self):
        """🤖 P2：config.json 嘅 source.* 值開機時就驗證 — 打錯字即刻炸（fail-fast），唔使等到 runtime dispatch"""
        for key, src in (self.config.get("source", {}) or {}).items():
            if str(src).lower() not in BROKERS:
                raise ValueError(f"❌ config.json source.{key}=[{src}] 不支援，有效：{sorted(BROKERS)}")

    def _resolve_broker(self, broker, default_key):
        """🤖 P2：統一 dispatch — broker 參數 > config.source.<default_key>；名字驗證 fail-fast。"""
        source = self.config.get("source", {}).get(default_key, "ib")
        if broker is not None:
            source = str(broker).lower()
        if source not in BROKERS:
            raise ValueError(f"❌ 不支援的券商類型 [{source}]，有效：{sorted(BROKERS)}")
        return self._clients[source]

    # ══════════ 💳 交易能力 dispatch（#34b — 契約見 modules/trade_base.py）══════════
    # 🤖 能力係**問出嚟**嘅，唔係假設：`TRADE` 旗標冇就如實講「呢家券商唔支援交易」，
    #    絕對唔好靜默 fallback 去另一家（自動落單靜默轉券商 = 用錯帳戶落錯單，係會動真錢嘅錯）。
    def trade_supported(self, broker=None):
        """→ (支援與否, 券商名, 原因)。頁要如實顯示邊家可交易，唔使睇 exception。"""
        client = self._resolve_broker(broker, "trade")
        if getattr(client, "TRADE", False):
            return True, client.NAME, ""
        return False, client.NAME, f"呢家券商唔支援交易：{client.NAME}"

    def _resolve_trade_broker(self, broker):
        """交易 dispatch 唯一入口 — 唔支援就回 (None, 原因)，支援就回 (client, None)。"""
        ok, name, why = self.trade_supported(broker)
        if not ok:
            return None, why
        return self._clients[name], None

    def _get_config(self, file='config.json'):
        # 🤖 用本檔案所在目錄解析（唔係 CWD）：GUI 可以由任何工作目錄 import
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), file)
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def get_config(self):
        logger.info("config: %s", self.config)

    def get_client_status(self):
        pass

    # ✨ P2：dispatch 收斂成一條 — _resolve_broker（broker 參數 > config.source.*，fail-fast）+ registry 查 client
    async def get_kline(self,code,ktype,broker=None,kline_num=None):
        client = self._resolve_broker(broker, "get_kline")
        # 🤖 傳入 kline_num 可 override config；冇傳就用 config（預設 100）— 同 stream_kline 一致
        kline_num = int(kline_num) if kline_num is not None else self.config.get("kline_num", 100)
        async with client:
            return await client.get_kline(code=code, ktype=ktype, kline_num=kline_num)


    # ✨ 同 get_kline 一樣回傳 (status, data, message)；成功時 data 係 async generator（要 `async for df in data` 消費）
    async def stream_kline(self, code, ktype, broker=None,kline_num=None):
        client = self._resolve_broker(broker, "stream_kline")   # 🤖 傳入 broker 可 override config
        # 🤖 傳入 kline_num 可 override config；冇傳就用 config（預設 100）— 同 get_kline 一致
        kline_num = int(kline_num) if kline_num is not None else self.config.get("kline_num", 100)
        async with client:
            # 🤖 各 client 同一個 (status, data, message) 形狀；data = async generator（訂閱已喺 setup 階段完成）
            return await client.stream_kline(code=code, ktype=ktype, kline_num=kline_num)


    # ✨ 交易能力一律照 get_kline 一樣嘅 (status, data, message) 形狀；broker 參數 > config.source.trade
    async def trade_accounts(self, broker=None):
        client, err = self._resolve_trade_broker(broker)
        if client is None:
            return False, None, err
        async with client:
            return await client.trade_accounts()

    async def place_order(self, *, code, side, qty, price=None, account=None,
                          env=None, order_type=None, tif=None, broker=None):
        client, err = self._resolve_trade_broker(broker)
        if client is None:
            return False, None, err
        async with client:
            return await client.place_order(code=code, side=side, qty=qty, price=price,
                                            account=account, env=env, order_type=order_type, tif=tif)

    async def cancel_order(self, *, order_id, account=None, env=None, broker=None):
        client, err = self._resolve_trade_broker(broker)
        if client is None:
            return False, None, err
        async with client:
            return await client.cancel_order(order_id=order_id, account=account, env=env)

    async def open_orders(self, *, account=None, env=None, broker=None):
        client, err = self._resolve_trade_broker(broker)
        if client is None:
            return False, None, err
        async with client:
            return await client.open_orders(account=account, env=env)

    async def positions(self, *, account=None, env=None, broker=None):
        client, err = self._resolve_trade_broker(broker)
        if client is None:
            return False, None, err
        async with client:
            return await client.positions(account=account, env=env)

    async def account_info(self, *, account=None, env=None, broker=None):
        client, err = self._resolve_trade_broker(broker)
        if client is None:
            return False, None, err
        async with client:
            return await client.account_info(account=account, env=env)

    async def unlock_status(self, *, account=None, env=None, broker=None):
        client, err = self._resolve_trade_broker(broker)
        if client is None:
            return False, None, err
        async with client:
            return await client.unlock_status(account=account, env=env)

    # 💡 未來如果你要加 get_ticker，就用一樣的直覺邏輯寫：
    async def get_ticker(self):
        client = self._resolve_broker(None, "get_ticker")
        return await client.get_ticker()


async def main():
    code='HK.HSI'
    ktype='K_1M'
    async with BrokerClient() as client:
        #client.get_config()

        #get_kline
        #status, data, message=await client.get_kline(code=code, ktype=ktype,broker='futu')
        #print(data)
        #status, data, message=await client.get_kline(code=code, ktype=ktype,broker='ib')
        #print(data)

        #stream_kline（同 get_kline 形狀：status/data/message；成功時 data 係 async generator）
        '''status, gen, message = await client.stream_kline(code=code, ktype=ktype,broker='futu',kline_num=None)
        if status:
            async for kline in gen:
                print(kline)'''

# 🚀 --- 必須使用 asyncio.run() 作為整支非同步程式的啟動引擎 ---
if __name__ == "__main__":
    asyncio.run(main())

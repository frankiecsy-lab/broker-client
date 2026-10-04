import json
import re
import asyncio
from abc import ABC, abstractmethod
from datetime import date, timedelta
import pandas as pd
from futu import *
from futu_client import FutuClient
from ib_client import IBClient

pd.set_option('display.width', None)
pd.set_option('display.max_colwidth', None)

class BrokerClient(ABC):
    def __init__(self):
        self.config = self._get_config()
        self.ib_client = IBClient(config=self.config.get("ib", {}))
        self.futu_client = FutuClient(config=self.config.get("futu", {}))

    async def __aenter__(self):
        print("BrokerClient Started")
        return self

    # 2. 離開時：只 PRINT 結束
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        print("BrokerClient Exited  ")
        return False  # 若內部噴錯，讓異常正常拋出

    def _get_config(self, file='config.json'):
        # 💡 測試小幫手：如果找不到 config.json，自動幫你建立你先前提到的 JSON 結構
        with open(file, 'r', encoding='utf-8') as f:
            return json.load(f)

    def get_config(self):
        print(self.config)

    def get_client_status(self):
        pass

    # ✨ 核心修正區：直接讀取 config 做 if-else
    async def get_kline(self,code,ktype):
        source = self.config.get("source", {}).get("get_kline", "ib")
        if source == 'futu':
            async with self.futu_client as futu:
                return await futu.get_kline(code=code,ktype=ktype)
        else:
            async with self.ib_client as ib:
                return await ib.get_kline(code=code,ktype=ktype)

    async def stream_kline(self, code, ktype):
        source = self.config.get("source", {}).get("stream_kline", "ib")
        if source == 'futu':
            async with self.futu_client as futu:
                # 🤖 用 async for 接住子類別 yield 出來的水流，並再次 yield 吐給最外層 main
                async for data in futu.stream_kline(code=code, ktype=ktype):
                    yield data
        else:
            async with self.ib_client as ib:
                # 🤖 IB 端未來也套用一模一樣的 async for ... yield 邏輯
                async for data in ib.stream_kline(code=code, ktype=ktype):
                    yield data

    # 💡 未來如果你要加 get_ticker，就用一樣的直覺邏輯寫：
    async def get_ticker(self):
        source = self.config.get("source", {}).get("get_ticker", "ib")
        if source == 'futu':
            return await self.futu_client.get_ticker()
        else:
            return await self.ib_client.get_ticker()


async def main():
    code='HK.00700'
    ktype='K_60M'
    async with BrokerClient() as client:
        client.get_config()
        #get_kline
        print(await client.get_kline(code=code, ktype=ktype))
        print("-" * 50)

        #stream_kline
        async for json_result in client.stream_kline(code=code, ktype=ktype):
            print("\n📦 【策略層收到最新 JSON 數據】:")
            print(json.dumps(json_result, indent=2, ensure_ascii=False, default=str))

# 🚀 --- 必須使用 asyncio.run() 作為整支非同步程式的啟動引擎 ---
if __name__ == "__main__":
    asyncio.run(main())

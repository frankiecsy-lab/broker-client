import json
import os
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
#pd.set_option('display.max_rows', None)
class BrokerClient(ABC):
    def __init__(self):
        self.config = self._get_config()
        self.ib_client = IBClient(config=self.config.get("ib", {}))
        self.futu_client = FutuClient(config=self.config.get("futu", {}))

    async def __aenter__(self):
        #print("BrokerClient Started")
        return self

    # 2. 離開時：統一清理共享連線
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # 🤖 IB 係共享持久連線，要喺呢度斷；Futu 每次獨立連線已喺各 stream 內自行 close
        try:
            await self.ib_client.disconnect()
        except Exception:
            pass
        return False  # 若內部噴錯，讓異常正常拋出

    def _get_config(self, file='config.json'):
        # 🤖 用本檔案所在目錄解析（唔係 CWD）：GUI 可以由任何工作目錄 import
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), file)
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def get_config(self):
        print(self.config)

    def get_client_status(self):
        pass

    # ✨ 核心修正區：直接讀取 config 做 if-else
    async def get_kline(self,code,ktype,broker=None,kline_num=None):

        source = self.config.get("source", {}).get("get_kline", "ib")
        if broker is not None:
            source = broker.lower()
        # 🤖 傳入 kline_num 可 override config；冇傳就用 config（預設 100）— 同 stream_kline 一致
        kline_num = int(kline_num) if kline_num is not None else self.config.get("kline_num", 100)

        match source:
            case 'ib':
                async with self.ib_client as ib:
                    return await ib.get_kline(code=code,ktype=ktype,kline_num=kline_num)
            case 'futu':
                async with self.futu_client as futu:
                    return await futu.get_kline(code=code,ktype=ktype,kline_num=kline_num)
            case _:
                # 🛡️ 防禦性設計：萬一有人在 config.json 亂填名字，給予安全警告並報錯
                raise ValueError(f"❌ 錯誤：不支援的券商類型 [{source}]")


    async def stream_kline(self, code, ktype, broker=None,kline_num=None):
        source = self.config.get("source", {}).get("stream_kline", "ib")
        if broker is not None:
            source = broker.lower()  # 🤖 同 get_kline 一樣：傳入 broker 可 override config
        # 🤖 傳入 kline_num 可 override config；冇傳就用 config（預設 100）— 同 get_kline 一致
        kline_num = int(kline_num) if kline_num is not None else self.config.get("kline_num", 100)
        match source:
            case "ib":
                async with self.ib_client as ib:
                    # 🤖 IB 端未來也套用一模一樣的 async for ... yield 邏輯
                    async for data in ib.stream_kline(code=code, ktype=ktype, kline_num=kline_num):
                        yield data
            case 'futu':
                async with self.futu_client as futu:
                    # 🤖 用 async for 接住子類別 yield 出來的水流，並再次 yield 吐給最外層 main
                    async for data in futu.stream_kline(code=code, ktype=ktype, kline_num=kline_num):
                        yield data
            case _:
                # 🛡️ 防禦性設計：萬一有人在 config.json 亂填名字，給予安全警告並報錯
                raise ValueError(f"❌ 錯誤：不支援的券商類型 [{source}]")


    # 💡 未來如果你要加 get_ticker，就用一樣的直覺邏輯寫：
    async def get_ticker(self):
        source = self.config.get("source", {}).get("get_ticker", "ib")
        if source == 'futu':
            return await self.futu_client.get_ticker()
        else:
            return await self.ib_client.get_ticker()


async def main():
    code='HK.HSImain'
    ktype='K_1M'
    async with BrokerClient() as client:
        #client.get_config()

        #get_kline
        '''status, data, message=await client.get_kline(code=code, ktype=ktype,broker='futu')
        print(data)
        status, data, message=await client.get_kline(code=code, ktype=ktype,broker='ib')
        print(data)'''

        #stream_kline
        async for kline in client.stream_kline(code=code, ktype=ktype,broker='futu',kline_num=None):
            print(kline)

# 🚀 --- 必須使用 asyncio.run() 作為整支非同步程式的啟動引擎 ---
if __name__ == "__main__":
    asyncio.run(main())

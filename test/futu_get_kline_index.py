import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from modules import *
import asyncio


# print(client.get_config())
async def stream_kline(code,ktype,broker,kline_num=None):
    async with BrokerClient() as client:
        async for kline in client.stream_kline(code=code, ktype=ktype, broker=broker, kline_num=kline_num):
            print(kline)

async def get_kline(code,ktype,broker,kline_num=None):
    async with BrokerClient() as client:
        status, data, message = await client.get_kline(code=code, ktype=ktype, broker=broker, kline_num=kline_num)
        print(data)

if __name__ == "__main__":

    #asyncio.run(get_kline(code='HK.HSImain', ktype='K_1M', broker='futu', kline_num=None))
    #asyncio.run(stream_kline(code='HK.HSImain',ktype='K_1M',broker='futu',kline_num=None))

    #asyncio.run(get_kline(code='HK.00700', ktype='K_1M', broker='futu', kline_num=None))
    #asyncio.run(stream_kline(code='HK.00700', ktype='K_1M', broker='futu', kline_num=None))

    asyncio.run(get_kline(code='US.NQmain', ktype='K_1M', broker='futu', kline_num=None))
    #asyncio.run(stream_kline(code='US.NQmain', ktype='K_1M', broker='futu', kline_num=None))

    #asyncio.run(get_kline(code='US.NVDA', ktype='K_1M', broker='futu', kline_num=None))
    #asyncio.run(stream_kline(code='US.NVDA', ktype='K_1M', broker='futu', kline_num=None))


    #asyncio.run(get_kline(code='HK.HSImain', ktype='K_1M', broker='ib', kline_num=None))
    #asyncio.run(stream_kline(code='HK.HSImain',ktype='K_1M',broker='ib',kline_num=None))

    #asyncio.run(get_kline(code='HK.00700', ktype='K_1M', broker='ib', kline_num=None))
    #asyncio.run(stream_kline(code='HK.00700', ktype='K_1M', broker='ib', kline_num=None))

    #asyncio.run(get_kline(code='US.NQmain', ktype='K_1M', broker='ib', kline_num=None))
    #asyncio.run(stream_kline(code='US.NQmain', ktype='K_1M', broker='ib', kline_num=None))

    #asyncio.run(get_kline(code='US.NVDA', ktype='K_1M', broker='ib', kline_num=None))
    #asyncio.run(stream_kline(code='US.NVDA', ktype='K_1M', broker='ib', kline_num=None))
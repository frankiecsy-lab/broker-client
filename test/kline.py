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
        # 🤖 同 get_kline 形狀：(status, data, message)；成功時 data 係 async generator
        status, gen, message = await client.stream_kline(code=code, ktype=ktype, broker=broker, kline_num=kline_num)
        if not status or gen is None:
            print(f'❌ stream_kline 失敗：{message}')   # 🤖 失敗時印出真正原因（權限/代號解析/訂閱），唔會靜默結束
            return
        async for kline in gen:
            print(kline)

async def get_kline(code,ktype,broker,kline_num=None):
    async with BrokerClient() as client:
        status, data, message = await client.get_kline(code=code, ktype=ktype, broker=broker, kline_num=kline_num)
        if not status or data is None:
            print(f'❌ get_kline 失敗：{message}')   # 🤖 之前只 print(data) → 失敗時只見到 None，真正原因喺 message
        else:
            print(data)

if __name__ == "__main__":

    #asyncio.run(get_kline(code='HK.HSImain', ktype='K_1M', broker='futu', kline_num=None))
    #asyncio.run(stream_kline(code='HK.HSImain',ktype='K_1M',broker='futu',kline_num=None))

    #asyncio.run(get_kline(code='HK.00700', ktype='K_1M', broker='futu', kline_num=None))
    #asyncio.run(stream_kline(code='HK.00700', ktype='K_1M', broker='futu', kline_num=None))

    #asyncio.run(get_kline(code='US.NQmain', ktype='K_1M', broker='futu', kline_num=None))
    #asyncio.run(stream_kline(code='US.NQmain', ktype='K_1M', broker='futu', kline_num=None))

    #asyncio.run(get_kline(code='US.NVDA', ktype='K_1M', broker='futu', kline_num=None))
    asyncio.run(stream_kline(code='US.NVDA', ktype='K_1M', broker='futu', kline_num=None))


    #asyncio.run(get_kline(code='HK.HSImain', ktype='K_1M', broker='ib', kline_num=None))
    #asyncio.run(stream_kline(code='HK.HSImain',ktype='K_1M',broker='ib',kline_num=None))

    #asyncio.run(get_kline(code='HK.00700', ktype='K_1M', broker='ib', kline_num=None))
    #asyncio.run(stream_kline(code='HK.00700', ktype='K_1M', broker='ib', kline_num=None))

    #asyncio.run(get_kline(code='US.NQmain', ktype='K_1M', broker='ib', kline_num=None))
    #asyncio.run(stream_kline(code='US.NQmain', ktype='K_1M', broker='ib', kline_num=None))

    #asyncio.run(get_kline(code='US.NVDA', ktype='K_1M', broker='ib', kline_num=None))
    #asyncio.run(stream_kline(code='US.NVDA', ktype='K_1M', broker='ib', kline_num=None))
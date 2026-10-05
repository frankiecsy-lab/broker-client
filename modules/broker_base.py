from abc import ABC, abstractmethod


class BrokerBase(ABC):
    """🤖 P1：所有券商 client 的統一契約。

       - 子類必須聲明 NAME（registry key，見 registry.py）並實現 get_kline / stream_kline
       - 兩個方法一律回傳 (status, data, message)；stream_kline 成功時 data 係 async generator
         （第一次 yield = baseline 快照，之後每筆 push = live tick；停止 = cancel task / .aclose()）
       - connect/disconnect 預設 no-op；有持久連線嘅 client（IB 共享 clientId=99）override"""

    NAME = ""   # 🤖 子類必須 override：'ib' / 'futu' / ...（registry key，唔好再用 magic string）

    def __init__(self, config=None):
        self.config = config or {}

    async def connect(self):
        pass   # 預設 no-op（Futu 每次獨立開連線）；IB override → 建立共享持久連線

    async def disconnect(self):
        pass   # 預設 no-op；IB override → 斷開共享持久連線（由 BrokerClient.__aexit__ 統一呼叫）

    @abstractmethod
    async def get_kline(self, code, ktype, kline_num=None):
        """回傳 (status, data, message)"""

    @abstractmethod
    async def stream_kline(self, code, ktype, kline_num=None):
        """回傳 (status, data, message)；成功時 data 係 async generator"""

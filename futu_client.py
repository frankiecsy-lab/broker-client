import asyncio
from futu import *
import pandas as pd


class MyCurKlineHandler(CurKlineHandlerBase):
    def __init__(self):
        super(MyCurKlineHandler, self).__init__()
        # 📦 建立一個小緩存區，用來存放剛推過來的即時數據
        self.data_buffer = []

    def on_recv_rsp(self, rsp_pb):
        ret_code, content = super(MyCurKlineHandler, self).on_recv_rsp(rsp_pb)
        if ret_code == RET_OK:
            # 1. 轉成你要求的統一格式 ( status, data, message )
            #latest_bar = content.tail(1)

            json_result = {
                "status": True,
                "data": latest_bar.to_dict(orient='records'),
                "message": "Futu Stream Update"
            }
            # 2. 塞進緩存區，等待外層的 stream_kline 把它撈走
            self.data_buffer.append(json_result)

        # 這裡的 return 是富途 SDK 內部的規定，我們保持原樣即可
        return ret_code, content


class FutuClient():
    def __init__(self,config=None):
        print(f'Futu Config {config}')
        self.host = config.get("host", "127.0.0.1")
        self.port = config.get("port", 11111)
        pass

    async def __aenter__(self):
        self.quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.quote_ctx.close()
        return False

    async def get_kline(self,code,ktype):
        ret_sub, err_message = self.quote_ctx.subscribe([code], [ktype], subscribe_push=False,
                                                   session=Session.ALL)
        # 先订阅 K 线类型。订阅成功后 OpenD 将持续收到服务器的推送，False 代表暂时不需要推送给脚本
        if ret_sub == RET_OK:  # 订阅成功
            ret, data = self.quote_ctx.get_cur_kline(code, 2, ktype, AuType.QFQ)  # 获取美股AAPL最近2个 K 线数据
            if ret == RET_OK:
                return {
                    "status": True,
                    "data": data,
                    "message": None
                }
            else:
                return {
                    "status": False,
                    "data": None,
                    "message": data
                }
        else:
            return {
                "status": False,
                "data": None,
                "message": err_message
            }

    async def stream_kline(self, code, ktype):

        print(f"📡 [Futu PUSH] 正在啟動 {code} 的即時 K 線 PUSH 監聽...")

        # 1. 🔍 【歷史快照階段】：先一步到位，獲取當下的 K 線 DataFrame
        ret_sub, err_msg = self.quote_ctx.subscribe([code], [ktype], subscribe_push=True, session=Session.ALL)
        if ret_sub != RET_OK:
            yield {"status": False, "data": None, "message": err_msg}
            return

        handler = MyCurKlineHandler()
        self.quote_ctx.set_handler(handler)

        try:
            # 🔄 進入長線鎖定迴圈
            while True:
                # 🤖 關鍵檢查：如果發現富途 Handler 的緩存區有新的 PUSH 資料
                if handler.data_buffer:
                    # 把資料從緩存區拿出來
                    new_data = handler.data_buffer.pop(0)
                    # ✨ 核心魔法：用 yield 代替 return，把資料不斷吐向最外層，且絕對不斷線！
                    yield new_data

                # 依然維持每秒釋放控制權，不吃 CPU 資源
                await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            print("🛑 [Futu PUSH] 接收到終止指令，正在關閉監聽事件...")
        finally:
            self.quote_ctx.set_handler(None)

    async def get_ticker(self):
        print('Futu get_ticker (來自獨立的 Futu 引擎)')
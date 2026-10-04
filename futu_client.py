import asyncio
from futu import *
import pandas as pd
# 1. ✨ 核心修正：告訴富途官方大腦，不要在控制台打印任何連線狀態日誌！
SysConfig.enable_console_log(False)

# 2. ✨ 保險防護：將 Python 底層名為 'futu' 的管道音量調到最低，只准輸出錯誤
logging.getLogger('futu').setLevel(logging.ERROR)
class MyCurKlineHandler(CurKlineHandlerBase):
    def __init__(self):
        super(MyCurKlineHandler, self).__init__()
        # 📦 建立一個小緩存區，用來存放剛推過來的即時數據
        self.data_buffer = []

    def on_recv_rsp(self, rsp_pb):
        ret_code, content = super(MyCurKlineHandler, self).on_recv_rsp(rsp_pb)
        if ret_code == RET_OK and content is not None and not content.empty:
            # 取最新 bar 塞進緩存區，等待外層 stream_kline 撈走拼接入 K 線表
            self.data_buffer.append(content.tail(1))

        # 這裡的 return 是富途 SDK 內部的規定，我們保持原樣即可
        return ret_code, content


class FutuClient():
    def __init__(self,config=None):
        #print(f'Futu Config {config}')
        self.host = config.get("host", "127.0.0.1")
        self.port = config.get("port", 11111)
        pass

    async def __aenter__(self):
        self.quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.quote_ctx.close()
        return False

    def _normalize_kline(self,data,ktype):
        columns_to_drop = ['code', 'name', 'turnover', 'pe_ratio', 'turnover_rate', 'last_close']
        if ktype in ['K_DAY', 'K_WEEK', 'K_MON']:
            data['time_key'] = data['time_key'].astype(str).str.replace(' 00:00:00', '')
        data =  data.drop(columns=columns_to_drop, errors='ignore')
        data['volume'] = data['volume'].astype(int)
        for col in ['open', 'high', 'low', 'close']:
            if col in data.columns:
                data[col] = data[col].astype(float)
        standard_order = ['time_key', 'open', 'high', 'low', 'close', 'volume']
        data = data[standard_order]
        return data

    async def get_kline(self,code,ktype,kline_num=10):
        print('FUTU get_kline')
        status=False
        data, message=None,None
        ret_sub, err_message = self.quote_ctx.subscribe([code], [ktype], subscribe_push=False,
                                                   session=Session.ALL)
        # 先订阅 K 线类型。订阅成功后 OpenD 将持续收到服务器的推送，False 代表暂时不需要推送给脚本
        if ret_sub == RET_OK:  # 订阅成功
            ret, data = self.quote_ctx.get_cur_kline(code, kline_num, ktype, AuType.NONE)  # 获取美股AAPL最近2个 K 线数据
            if ret == RET_OK:
                status=True
            else:
                status=False
                message=data
        else:
            status=False
            message=err_message

        data = self._normalize_kline(data,ktype) if status else None

        return status, data, message

    async def stream_kline(self, code, ktype, kline_num=10):
        # 1️⃣ 先用 get_kline 取歷史 K 線做初始底表（HISTORY）
        status, data, _ = await self.get_kline(code, ktype, kline_num=kline_num)
        if not status or data is None:
            print("❌ FUTU stream_kline: 歷史 K 線取得失敗")
            return
        print("FUTU stream_kline...")

        # 2️⃣ 先 yield 一次完整歷史快照
        yield data

        # 3️⃣ 訂閱推送更新
        ret_sub, err_msg = self.quote_ctx.subscribe([code], [ktype], subscribe_push=True, session=Session.ALL)
        if ret_sub != RET_OK:
            print(f"❌ FUTU stream_kline: 訂閱失敗 {err_msg}")
            return

        handler = MyCurKlineHandler()
        self.quote_ctx.set_handler(handler)

        # 🤖 累積器：以 get_kline 嘅歷史做底，之後逐條拼接新 bar（同 IB 端同一份 contract）
        kline_df = data.copy()

        try:
            # 🔄 進入長線鎖定迴圈
            while True:
                # 🤖 關鍵檢查：如果發現富途 Handler 的緩存區有新的 PUSH 資料
                if handler.data_buffer:
                    latest_bar = handler.data_buffer.pop(0)
                    norm_row = self._normalize_kline(latest_bar.copy(), ktype)
                    tk = norm_row['time_key'].iloc[0]
                    if not kline_df.empty and kline_df['time_key'].iloc[-1] == tk:
                        # 同一根 bar（盤中更新）：原地覆蓋最後一行，唔會重複
                        kline_df.iloc[-1] = norm_row.iloc[0]
                    else:
                        # 新 bar：接到尾端
                        kline_df = pd.concat([kline_df, norm_row], ignore_index=True)
                    # ✨ yield 完整更新後嘅 K 線表到最外層，絕對不斷線！
                    yield kline_df.copy()

                # 依然維持每 0.1 秒釋放控制權，不吃 CPU 資源
                await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            print("🛑 [Futu PUSH] 接收到終止指令，正在關閉監聽事件...")
        finally:
            self.quote_ctx.set_handler(None)

    async def get_ticker(self):
        print('Futu get_ticker (來自獨立的 Futu 引擎)')
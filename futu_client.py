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
        # 🤖 共享 context 已廢：而家每條 stream / 每次 get_kline 用獨立連線（見 _new_ctx）
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    def _new_ctx(self):
        # 🤖 每條 stream / 每次 get_kline 開一條獨立 OpenQuoteContext
        # （OpenD 支援多條並發連線；每個 context 有自己 handler，8 條互不干擾）
        return OpenQuoteContext(host=self.host, port=self.port)

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
        quote_ctx = self._new_ctx()  # 🤖 獨立連線，用完即斷
        try:
            ret_sub, err_message = quote_ctx.subscribe([code], [ktype], subscribe_push=False,
                                                       session=Session.ALL)
            if ret_sub == RET_OK:  # 订阅成功
                ret, data = quote_ctx.get_cur_kline(code, kline_num, ktype, AuType.NONE)
                if ret == RET_OK:
                    status=True
                else:
                    status=False
                    message=data
            else:
                status=False
                message=err_message
        finally:
            quote_ctx.close()

        data = self._normalize_kline(data,ktype) if status else None

        return status, data, message

    async def stream_kline(self, code, ktype, kline_num=10):
        # 🤖 獨立 OpenQuoteContext：呢條 stream 專用，唔會同其他 stream 撞 handler / 連線
        quote_ctx = self._new_ctx()
        try:
            # 1️⃣ 先掛 handler 再訂閱 push — subscribe 之後每一筆推送都有人收，唔會漏 tick
            handler = MyCurKlineHandler()
            quote_ctx.set_handler(handler)

            ret_sub, err_msg = quote_ctx.subscribe([code], [ktype], subscribe_push=True, session=Session.ALL)
            if ret_sub != RET_OK:
                print(f"❌ FUTU stream_kline: 訂閱失敗 {err_msg}")
                return
            # 2️⃣ 取歷史 K 線做初始底表（HISTORY）
            ret, data = quote_ctx.get_cur_kline(code, kline_num, ktype, AuType.NONE)
            if ret != RET_OK:
                print("❌ FUTU stream_kline: 歷史 K 線取得失敗")
                return
            history_df = self._normalize_kline(data, ktype)

            print("FUTU stream_kline...")
            # 3️⃣ 先 yield 一次完整歷史快照
            yield history_df

            # 🤖 累積器：以歷史做底，之後逐條拼接新 bar（同 IB 端同一份 contract）
            kline_df = history_df.copy()

            # 🔄 進入長線鎖定迴圈
            while True:
                # 🤖 關鍵檢查：如果發現富途 Handler 的緩存區有新的 PUSH 資料
                if handler.data_buffer:
                    latest_bar = handler.data_buffer.pop(0)
                    norm_row = self._normalize_kline(latest_bar.copy(), ktype)
                    tk = norm_row['time_key'].iloc[0]
                    if not kline_df.empty:
                        last_tk = kline_df['time_key'].iloc[-1]
                        if tk < last_tk:
                            # 🤖 比最後一行舊：忽略，避免亂序拼接
                            continue
                        elif tk == last_tk:
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
            raise  # 🤖 保留取消語義：上層 task.cancel() 先可以正確結束
        finally:
            # 🤖 安全拔線：清 handler + 斷呢條 stream 專用連線
            try:
                quote_ctx.set_handler(None)
            except Exception:
                pass
            quote_ctx.close()

    async def get_ticker(self):
        print('Futu get_ticker (來自獨立的 Futu 引擎)')
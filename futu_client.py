import asyncio
import math
from datetime import date, timedelta
from futu import *
import pandas as pd
import queue
import queue
# 1. ✨ 核心修正：告訴富途官方大腦，不要在控制台打印任何連線狀態日誌！
SysConfig.enable_console_log(False)

# 2. ✨ 保險防護：將 Python 底層名為 'futu' 的管道音量調到最低，只准輸出錯誤
logging.getLogger('futu').setLevel(logging.ERROR)
class MyCurKlineHandler(CurKlineHandlerBase):
    def __init__(self, normalize=None, ktype=None):
        super(MyCurKlineHandler, self).__init__()
        # 📦 建立一個小緩存區，用來存放剛推過來的即時數據（已 normalize）
        # B3 fix: queue.Queue 本身 thread-safe — SDK callback thread put / asyncio loop get 互不干涉
        self.data_buffer = queue.Queue()
        self._normalize = normalize
        self.ktype = ktype

    def on_recv_rsp(self, rsp_pb):
        ret_code, content = super(MyCurKlineHandler, self).on_recv_rsp(rsp_pb)
        if ret_code == RET_OK and content is not None and not content.empty:
            # 取最新 bar，normalize 後塞進緩存區（tail(1) 本身係 copy，可以放心改）
            latest_bar = content.tail(1)
            if self._normalize is not None:
                latest_bar = self._normalize(latest_bar, self.ktype)
            # 等待外層 stream_kline 撈走拼接入 K 線表
            self.data_buffer.put(latest_bar)

        # 這裡的 return 是富途 SDK 內部的規定，我們保持原樣即可
        return ret_code, content


class FutuClient():
    def __init__(self,config=None):
        #print(f'Futu Config {config}')
        self.host = config.get("host", "127.0.0.1")
        self.port = config.get("port", 11111)
        self.kline_num = config.get("kline_num", 1000)
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
        # B4 fix: 先 copy 再改，唔會 mutate caller 傳入嘅 df
        data = data.copy()
        columns_to_drop = ['code', 'name', 'turnover', 'pe_ratio', 'turnover_rate', 'last_close']
        if ktype in ['K_DAY', 'K_WEEK', 'K_MON']:
            data['time_key'] = data['time_key'].astype(str).str.replace(' 00:00:00', '')
        data =  data.drop(columns=columns_to_drop, errors='ignore')
        # B5 fix: NaN volume（session 邊界情況）填 0，唔好喺 callback thread astype(int) 炸咗靜默丟 push
        data['volume'] = data['volume'].fillna(0).astype(int)
        for col in ['open', 'high', 'low', 'close']:
            if col in data.columns:
                data[col] = data[col].astype(float)
        standard_order = ['time_key', 'open', 'high', 'low', 'close', 'volume']
        data = data[standard_order]
        return data

    # 🤖 K 線取數 helper：≤1000 用 get_cur_kline；>1000 自動切 request_history_kline 分頁
    # （富途服務端硬限制：get_cur_kline num 最大 1000、request_history_kline 單次 max_count 最大 1000）
    async def _fetch_kline(self, quote_ctx, code, ktype, kline_num):
        if int(kline_num) <= 1000:
            return quote_ctx.get_cur_kline(code, int(kline_num), ktype, AuType.QFQ)

        # >1000：估算一個夠闊嘅日曆窗口（按每交易日最多 bar 數 + 週末假日緩衝），再逐頁撳
        bars_per_day = {
            'K_1M': 800, 'K_3M': 270, 'K_5M': 160, 'K_15M': 54,
            'K_30M': 27, 'K_60M': 14, 'K_DAY': 1, 'K_WEEK': 0.2, 'K_MON': 1 / 21,
        }.get(ktype, 800)
        trading_days = math.ceil(int(kline_num) / bars_per_day)
        calendar_days = int(trading_days * 7 / 5 * 1.3) + 10
        end_dt = date.today() + timedelta(days=1)   # +1 確保包到今日
        start_dt = end_dt - timedelta(days=calendar_days)

        frames, page_req_key = [], None
        while True:
            ret, data, page_req_key = quote_ctx.request_history_kline(
                code, str(start_dt), str(end_dt), ktype=ktype, autype=AuType.QFQ,
                max_count=1000, page_req_key=page_req_key, session=Session.ALL)
            if ret != RET_OK:
                return ret, data   # 失敗形狀同 get_cur_kline 一致：(ret, err_msg)
            frames.append(data)
            if page_req_key is None:
                break              # 冇更多數據，撳完
            await asyncio.sleep(0.1)  # 連續分頁間隔一下，避免觸發限頻

        full_df = pd.concat(frames, ignore_index=True)
        return RET_OK, full_df.tail(int(kline_num))   # 窗口可能多咗啲，截返最後 kline_num 條

    async def get_kline(self,code,ktype,kline_num=None):
        if kline_num is None:
            kline_num = self.kline_num
        print('FUTU get_kline')
        status=False
        data, message=None,None
        quote_ctx = self._new_ctx()  # 🤖 獨立連線，用完即斷
        try:
            ret_sub, err_message = quote_ctx.subscribe([code], [ktype], subscribe_push=False,
                                                       session=Session.ALL)
            if ret_sub == RET_OK:  # 订阅成功
                ret, data = await self._fetch_kline(quote_ctx, code, ktype, kline_num)
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

    async def stream_kline(self, code, ktype, kline_num=None):
        if kline_num is None:
            kline_num = self.kline_num
        # 🤖 獨立 OpenQuoteContext：呢條 stream 專用，唔會同其他 stream 撞 handler / 連線
        quote_ctx = self._new_ctx()
        try:
            print("FUTU stream_kline...")
            # 1️⃣ 先掛 handler 再訂閱 push — subscribe 之後每一筆推送都有人收，唔會漏 tick
            # 🤖 handler 收到 push 即刻 normalize，buffer 入面存嘅就係標準 6 欄 df
            handler = MyCurKlineHandler(normalize=self._normalize_kline, ktype=ktype)
            quote_ctx.set_handler(handler)

            # 2️⃣ 訂閱 push — 冇 subscribe，OpenD 唔會推任何數據落 handler，buffer 永遠係空
            ret_sub, err_msg = quote_ctx.subscribe([code], [ktype], subscribe_push=True, session=Session.ALL)
            if ret_sub != RET_OK:
                print(f"❌ FUTU stream_kline: 訂閱失敗 {err_msg}")
                return

            # 3️⃣ 取歷史 K 線做初始底表（HISTORY）— B2 fix: 直接用呢條 stream 自己嘅連線，唔多開一條；>1000 自動切分頁
            ret, data = await self._fetch_kline(quote_ctx, code, ktype, kline_num)
            if ret != RET_OK:
                print(f"❌ FUTU stream_kline: 歷史 K 線取得失敗 {data}")
                return
            history_df = self._normalize_kline(data, ktype)

            yield history_df
            kline_df = history_df.copy()

            # 🔄 進入長線鎖定迴圈
            while True:
                yielded = False
                # 🤖 關鍵檢查（B1 fix）：每圈 drain 晒 buffer 所有待處理 bar — 高頻 push 都唔會無界增長
                while True:
                    try:
                        latest_bar = handler.data_buffer.get_nowait()
                    except queue.Empty:
                        break
                    tk = latest_bar['time_key'].iloc[0]
                    if not kline_df.empty:
                        last_tk = kline_df['time_key'].iloc[-1]
                        if tk < last_tk:
                            # 🤖 比最後一行舊：忽略，避免亂序拼接（只跳過呢條 bar，唔會跳外層 sleep）
                            continue
                        elif tk == last_tk:
                            # 同一根 bar（盤中更新）：原地覆蓋最後一行，唔會重複
                            kline_df.iloc[-1] = latest_bar.iloc[0]
                        else:
                            # 🆕 新 bar（tk > last_tk）：接到尾端
                            kline_df = pd.concat([kline_df, latest_bar], ignore_index=True)
                    else:
                        # 新 bar：接到尾端
                        kline_df = pd.concat([kline_df, latest_bar], ignore_index=True)
                    yielded = True
                if yielded:
                    # ✨ yield 完整更新後嘅 K 線表到最外層（每圈最多一次快照），絕對不斷線！
                    yield kline_df.copy()
                # 依然維持每 0.1 秒釋放控制權，不吃 CPU 資源（固定執行，唔會俾 continue 跳過）
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
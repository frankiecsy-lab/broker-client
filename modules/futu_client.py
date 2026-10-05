import asyncio
import logging
import math
from datetime import date, timedelta
from futu import *
import pandas as pd
import queue
import queue

from .broker_base import BrokerBase   # 🤖 P1：統一契約（NAME / get_kline / stream_kline 形狀）
from .kline_schema import reorder_kline, validate_kline   # 🤖 P3：K 線 schema 一來源 + 驗證

SysConfig.enable_console_log(False)
logging.getLogger('futu').setLevel(logging.ERROR)

logger = logging.getLogger(__name__)   # 🤖 P5：print → logging（app-level config 喺 modules/__init__.py）

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


class FutuClient(BrokerBase):
    NAME = "futu"   # 🤖 P1：registry key（同 config.json 嘅 section 名）

    def __init__(self,config=None):
        super().__init__(config)   # 🤖 P1：BrokerBase.__init__ 存 self.config（允許唔傳 → {}）
        self.host = self.config.get("host", "127.0.0.1")
        self.port = self.config.get("port", 11111)
        self.kline_num = self.config.get("kline_num", 1000)

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
        # 🤖 P1 fix：time_key 統一 parse 成 datetime（同 IB）— futu 回傳 str（"2026-10-05 13:30:00" / "2026-10-05"），
        #    之前保持 str 令 schema 同 IB 唔一致；K_DAY/K_WEEK 舊嘅 str.replace(' 00:00:00') 分支一併廢（parse 後唔使再處理）
        tk = pd.to_datetime(data['time_key'], errors='coerce')
        if getattr(tk.dt, 'tz', None) is not None:
            tk = tk.dt.tz_localize(None)
        data['time_key'] = tk
        columns_to_drop = ['code', 'name', 'turnover', 'pe_ratio', 'turnover_rate', 'last_close']
        data =  data.drop(columns=columns_to_drop, errors='ignore')
        # B5 fix: NaN volume（session 邊界情況）填 0，唔好喺 callback thread astype(int) 炸咗靜默丟 push
        data['volume'] = data['volume'].fillna(0).astype(int)
        for col in ['open', 'high', 'low', 'close']:
            if col in data.columns:
                data[col] = data[col].astype(float)
        return reorder_kline(data)   # 🤖 P3：欄順序由 kline_schema 統一（唔再各自維護 standard_order）

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
        logger.info('FUTU get_kline')
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
        # 🤖 P3：成功前驗 schema — 唔符合契約就當失敗回報，唔會俾壞形狀流出到 caller
        if status:
            ok, why = validate_kline(data)
            if not ok:
                return False, None, f"FUTU get_kline: K 線 schema 驗證失敗 {why}"

        return status, data, message

    async def stream_kline(self, code, ktype, kline_num=None):
        """同 get_kline 一樣回傳 (status, data, message)：
           - status=True → data 係 async generator（第一次 yield = baseline，之後每筆 push = live tick）；
             consumer 用 `async for df in data` 消費，停止 = cancel task / .aclose()（finally 會斷呢條 stream 專用連線）
           - status=False → data=None、message 係失敗原因（訂閱權限 / 歷史取數 / OpenD 連線）"""
        if kline_num is None:
            kline_num = self.kline_num
        # 🤖 獨立 OpenQuoteContext：呢條 stream 專用，唔會同其他 stream 撞 handler / 連線
        quote_ctx = self._new_ctx()

        # Setup 階段（掛 handler + 訂閱 push + 取歷史）— 任何失敗都經 status/message 回報並即刻 close，唔留半訂閱狀態
        try:
            logger.info("FUTU stream_kline...")
            # 1️⃣ 先掛 handler 再訂閱 push — subscribe 之後每一筆推送都有人收，唔會漏 tick
            # 🤖 handler 收到 push 即刻 normalize，buffer 入面存嘅就係標準 6 欄 df
            handler = MyCurKlineHandler(normalize=self._normalize_kline, ktype=ktype)
            quote_ctx.set_handler(handler)

            # 2️⃣ 訂閱 push — 冇 subscribe，OpenD 唔會推任何數據落 handler，buffer 永遠係空
            ret_sub, err_msg = quote_ctx.subscribe([code], [ktype], subscribe_push=True, session=Session.ALL)
            if ret_sub != RET_OK:
                quote_ctx.close()   # 🤖 setup 失敗 → 即刻斷呢條專用連線，唔會漏
                return False, None, f"FUTU stream_kline: 訂閱失敗 {err_msg}"

            # 3️⃣ 取歷史 K 線做初始底表（HISTORY）— B2 fix: 直接用呢條 stream 自己嘅連線，唔多開一條；>1000 自動切分頁
            ret, data = await self._fetch_kline(quote_ctx, code, ktype, kline_num)
            if ret != RET_OK:
                quote_ctx.close()   # 🤖 setup 失敗 → 即刻斷呢條專用連線，唔會漏
                return False, None, f"FUTU stream_kline: 歷史 K 線取得失敗 {data}"
        except Exception as e:   # 🤖 OpenD 未開 / 連線被拒等 exception → 一樣經 status/message 回報
            try:
                quote_ctx.close()
            except Exception:
                pass
            return False, None, f"FUTU stream_kline: {type(e).__name__}: {e}"

        history_df = self._normalize_kline(data, ktype)
        # 🤖 P3：baseline 驗 schema — 唔符合就斷呢條專用連線並當失敗回報
        ok, why = validate_kline(history_df)
        if not ok:
            quote_ctx.close()
            return False, None, f"FUTU stream_kline: K 線 schema 驗證失敗 {why}"

        async def _stream():
            try:
                # 🤖 先 yield 一次完整歷史快照（baseline），再進入長線鎖定迴圈
                yield history_df
                kline_df = history_df.copy()

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
                logger.info("🛑 [Futu PUSH] 接收到終止指令，正在關閉監聽事件...")
                raise  # 🤖 保留取消語義：上層 task.cancel() 先可以正確結束
            finally:
                # 🤖 安全拔線：清 handler + 斷呢條 stream 專用連線
                try:
                    quote_ctx.set_handler(None)
                except Exception:
                    pass
                quote_ctx.close()

        return True, _stream(), None

    async def get_ticker(self):
        logger.info('Futu get_ticker (來自獨立的 Futu 引擎)')
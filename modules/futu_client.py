import asyncio
import logging
import math
import threading
from datetime import date, timedelta
from futu import *
import pandas as pd
import queue

from .broker_base import BrokerBase   # 🤖 P1：統一契約（NAME / get_kline / stream_kline 形狀）
from .kline_schema import reorder_kline, validate_kline   # 🤖 P3：K 線 schema 一來源 + 驗證
from .trade_base import (ACC_COLS, ACCINFO_KEYS, ORDER_COLS, POS_COLS,  # 🤖 #34b：交易能力契約
                         TradeBase, clamp_env, clamp_side)

SysConfig.enable_console_log(False)
logging.getLogger('futu').setLevel(logging.ERROR)

logger = logging.getLogger(__name__)   # 🤖 P5：print → logging（app-level config 喺 modules/__init__.py）

class KlineRouter(CurKlineHandlerBase):
    """🔀 一個 handler 掛喺**共享** ctx：按 push 自帶嘅 `(code, k_type)` 分派落各條 stream 自己嘅 queue。
       ⚠️ 分派必須喺 `_normalize_kline` **之前**做 — normalize 會 drop 咗 `code` 欄（見 FutuClient._normalize_kline）。
       同一對 (code,ktype) 可以有幾條 stream（行情頁兩格設同一標的同一週期）→ fan-out 落每個 queue，各自 drain 自己嗰份。
       B3 fix: queue.Queue 本身 thread-safe — SDK callback thread put / asyncio loop get 互不干涉。"""

    def __init__(self, normalize, sinks):
        super().__init__()
        self._normalize = normalize
        self._sinks = sinks   # (code, ktype) -> list[queue.Queue]（由 FutuClient 持有，邊界處只讀）

    def on_recv_rsp(self, rsp_pb):
        ret_code, content = super().on_recv_rsp(rsp_pb)
        if ret_code == RET_OK and content is not None and not content.empty:
            if 'code' not in content.columns or 'k_type' not in content.columns:
                # 🤖 呢個 SDK 版本嘅 push 冇咗路由欄 → 如實唔派（亂派會畫錯格），等上层靠 snapshot/tick 照樣更新
                logger.warning('FUTU push 缺少 code/k_type 欄，無法路由到 stream：%s', list(content.columns))
                return ret_code, content
            # 一次 push 可能跨標的 → 逐 (code,ktype) 組各取自己最新嗰根（tail(1) 本身係 copy，可以放心改）
            for (code, ktype), grp in content.groupby(['code', 'k_type'], sort=False):
                q_list = self._sinks.get((str(code), str(ktype)))
                if not q_list:
                    continue   # 已 unsubscribe / 而家冇人收 → 如實丟咗
                bar = self._normalize(grp.tail(1), str(ktype))
                for q in list(q_list):
                    q.put(bar)   # 等待外層 stream_kline 撈走拼接入 K 線表

        # 這裡的 return 是富途 SDK 內部的規定，我們保持原樣即可
        return ret_code, content


class FutuClient(BrokerBase, TradeBase):
    NAME = "futu"   # 🤖 P1：registry key（同 config.json 嘅 section 名）
    TRADE = True    # 🤖 #34b：有交易能力（見下方「💳 交易能力」段 + modules/trade_base.py）

    def __init__(self,config=None):
        super().__init__(config)   # 🤖 P1：BrokerBase.__init__ 存 self.config（允許唔傳 → {}）
        self.host = self.config.get("host", "127.0.0.1")
        self.port = self.config.get("port", 11111)
        self.kline_num = self.config.get("kline_num", 1000)
        # ⏳ 兩段式取數第一段根數（0 / 唔設 = 關兩段式，一次過取 kline_num）
        self.kline_num_first = int(self.config.get("kline_num_first", 0) or 0)
        # 🔌 共享連線狀態（見 _ensure_ctx）：_subs = 訂閱 refcount，_queues = 每條 stream 專屬 push queue
        self._lock = threading.RLock()
        self._shared_ctx = None
        self._shared_handler = None
        self._shared_dead = False
        self._subs = {}
        self._queues = {}
        # 💳 交易 ctx（#34b）：同行情 ctx **分開**一條（OpenD 支援並發；交易同行情係兩套 API）。
        #    lazy 開、全程序共用一條；`asyncio.Lock` 只為防兩條 coroutine 同時開（SDK 調用照樣 to_thread）
        self._trd_ctx = None
        self._trd_lock = asyncio.Lock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    def _open_ctx(self):
        """全檔唯一開 `OpenQuoteContext` 嘅位（一次 construction = 一次 OpenD handshake）。
           邊度會開：① `_ensure_ctx()` — 所有 stream 共用，全程序一次 ② `get_kline()` — 一次性取數，用完即斷。"""
        return OpenQuoteContext(host=self.host, port=self.port)

    # ══════════ 🔌 共享 OpenQuoteContext（P3）══════════
    # 🤖 舊設計：每條 stream / 每次 get_kline 各開一條連線（OpenD 支援並發、handler 各自獨立）。
    #    實測成本：行情頁 6 格 = 13 次 handshake，全部串行排喺同一個 event loop 前面 → 六格同時切要 5.8 秒。
    #    新契約：所有 stream 共用**一條** ctx + 一個 handler（按 (code,k_type) 路由），訂閱按 refcount 增減。
    def _ensure_ctx(self):
        """lazy 開一條共享 ctx。開唔到 / 被 OpenD 斷（exception）→ 標 `_shared_dead`，下次呢度重開。
           ⚠️ 會被幾個 to_thread worker 同時入 → 全程攞 `self._lock`（連 constructor 都喺 lock 內，先保證得返一條）。"""
        with self._lock:
            if self._shared_ctx is not None and not self._shared_dead:
                return self._shared_ctx
            if self._shared_ctx is not None:   # 死咗 → 清乾淨先重開
                self._drop_shared()
            self._shared_ctx = self._open_ctx()
            self._shared_handler = KlineRouter(normalize=self._normalize_kline, sinks=self._queues)
            self._shared_ctx.set_handler(self._shared_handler)
            self._shared_dead = False
            logger.info('FUTU: 開咗共享 OpenQuoteContext %s:%s', self.host, self.port)
            return self._shared_ctx

    def _drop_shared(self):
        """收返共享 ctx（调用者必須已經攞住 lock）。"""
        ctx, self._shared_ctx = self._shared_ctx, None
        self._shared_handler = None
        self._subs.clear()
        self._queues.clear()
        if ctx is not None:
            try:
                ctx.set_handler(None)
            except Exception:
                pass
            try:
                ctx.close()
            except Exception:
                pass

    def _acquire(self, code, ktype):
        """攞 (共享 ctx, 呢條 stream 專屬 push queue)。refcount 0→1 先真 `subscribe`。
           失敗 → raise，由 caller 經 `(False, None, msg)` 如實回報。
           ⚠️ 入面係同步 network 呼叫 → caller 必須 `await asyncio.to_thread(self._acquire, ...)`。"""
        key = (code, ktype)
        with self._lock:
            ctx = self._ensure_ctx()
            if self._subs.get(key, 0) == 0:
                try:
                    ret, msg = ctx.subscribe([code], [ktype], subscribe_push=True, session=Session.ALL)
                except Exception as e:
                    # 🤖 只有 transport 層異常先當連線死咗（權限 / 參數類 RET_ERROR 唔關連線事 —
                    #    亂標 dead 會連其他 live stream 一併拆走）
                    live = sum(self._subs.values())
                    self._shared_dead = True
                    self._drop_shared()
                    if live:
                        logger.warning('FUTU: 共享連線斷咗，%d 條 live stream 會收唔到 push（下次取數自動重開連線）', live)
                    raise ConnectionError(f'訂閱異常 {type(e).__name__}: {e}') from e
                if ret != RET_OK:
                    raise ConnectionError(f'訂閱失敗 {msg}')
                self._subs[key] = 1
            else:
                self._subs[key] += 1
            q = queue.Queue()
            self._queues.setdefault(key, []).append(q)
            return ctx, q

    def _release(self, code, ktype, q):
        """stream 結束（正常 / cancel / 錯誤）→ refcount 減一，歸零先 unsubscribe。
           ⚠️ **唔會 close 共享 ctx** — 其他 stream 仲用緊；真正收線由 `disconnect()` 負責。"""
        key = (code, ktype)
        with self._lock:
            sinks = self._queues.get(key)
            if sinks is not None and q in sinks:
                sinks.remove(q)
            n = self._subs.get(key, 1) - 1
            if n > 0:
                self._subs[key] = n
                return
            self._subs.pop(key, None)
            self._queues.pop(key, None)
            if self._shared_ctx is not None and not self._shared_dead:
                try:
                    self._shared_ctx.unsubscribe([code], [ktype])
                except Exception as e:
                    logger.warning('FUTU: unsubscribe %s %s 失敗 %s', code, ktype, e)

    async def disconnect(self):
        """🤖 收返共享連線 — `BrokerClient.__aexit__` 已經會循環調每個 client 嘅 `disconnect()`，上層零改動。"""
        with self._lock:
            self._drop_shared()
            self._shared_dead = False
        await self._drop_trd_ctx()   # 🤖 #34b：交易 ctx 一齊收（同一個清理點，唔另設開關）

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
            # 🧵 futu 係同步呼叫 → 落 worker thread：一格取數唔會阻塞住其他格嘅 push / baseline
            return await asyncio.to_thread(quote_ctx.get_cur_kline, code, int(kline_num), ktype, AuType.QFQ)

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
            # 🧵 同上：分頁都唔喺 event loop 上面行同步呼叫
            ret, data, page_req_key = await asyncio.to_thread(
                quote_ctx.request_history_kline, code, str(start_dt), str(end_dt),
                ktype=ktype, autype=AuType.QFQ, max_count=1000, page_req_key=page_req_key, session=Session.ALL)
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
        # 🤖 一次性取數：用獨立連線，唔使 push、唔同 stream 搶訂閱配額，用完即斷最簡單
        quote_ctx = self._open_ctx()
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
             consumer 用 `async for df in data` 消費，停止 = cancel task / .aclose()（finally 會 release 呢條 stream 嘅訂閱）
           - status=False → data=None、message 係失敗原因（訂閱權限 / 歷史取數 / OpenD 連線）
           - ⏳ 兩段式（config futu.kline_num_first > 0 且 < kline_num 先觸發）：baseline 只帶 kline_num_first 根，
             完整 kline_num 根會喺首幀之後以**多一次快照**送出 → 對 consumer 嚟講只係多一次 tick，契約唔變。
           - 🔌 連線契約（P3 改咗）：所有 stream **共用一條** `OpenQuoteContext` + 一個 `KlineRouter`，
             訂閱按 `(code,ktype)` refcount 增減。舊設計每條 stream 各開一條連線 → 行情頁 6 格 = 13 次
             handshake 排喺 event loop 前面（實測六格同時切 5.8 秒）；共用之後得返 1 次。
             收線由 `disconnect()` 負責，stream 結束唔 close 共享 ctx。"""
        if kline_num is None:
            kline_num = self.kline_num
        # ⏳ 兩段式：第一段只取 kline_num_first（夠即刻上圖），完整 kline_num 喺首幀之後背景補取。
        #    取數量同 `KlineChart.MAX_DRAW` 對齊 → 首圖唔使等 1000 根嘅 round-trip。
        first_num = min(int(kline_num), self.kline_num_first) if self.kline_num_first > 0 else int(kline_num)

        # Setup 階段（共享連線 + 訂閱 push + 取歷史）— 任何失敗都經 status/message 回報，唔留半訂閱狀態
        try:
            logger.info("FUTU stream_kline...")
            # 1️⃣ 攞共享 ctx + 呢條 stream 專屬 push queue（refcount 0→1 先真 subscribe）
            # 🧵 入面係同步 network round-trip → 落 worker thread（多格同時起 stream 先唔會互相排隊）
            quote_ctx, push_q = await asyncio.to_thread(self._acquire, code, ktype)
        except Exception as e:   # 🤖 OpenD 未開 / 連線被拒 / 訂閱失敗等 → 一樣經 status/message 回報
            return False, None, f"FUTU stream_kline: {e}"

        # 2️⃣ 取歷史 K 線做初始底表（HISTORY）— B2 fix: 直接用呢條 stream 所屬嘅連線，唔多開一條；>1000 自動切分頁
        #    ⏳ 兩段式：呢度只取 first_num（完整根數喺首幀上圖後背景補，見 _stream）
        try:
            ret, data = await self._fetch_kline(quote_ctx, code, ktype, first_num)
        except Exception as e:
            self._release(code, ktype, push_q)
            return False, None, f"FUTU stream_kline: {type(e).__name__}: {e}"
        if ret != RET_OK:
            self._release(code, ktype, push_q)   # 🤖 setup 失敗 → 即刻退返訂閱，唔會漏
            return False, None, f"FUTU stream_kline: 歷史 K 線取得失敗 {data}"

        history_df = self._normalize_kline(data, ktype)
        # 🤖 P3：baseline 驗 schema — 唔符合就退返訂閱並當失敗回報
        ok, why = validate_kline(history_df)
        if not ok:
            self._release(code, ktype, push_q)
            return False, None, f"FUTU stream_kline: K 線 schema 驗證失敗 {why}"

        async def _stream():
            try:
                # 🤖 先 yield 一次完整歷史快照（baseline），再進入長線鎖定迴圈
                yield history_df
                kline_df = history_df.copy()

                if first_num < int(kline_num):
                    # ⏳ 第二段：首幀已上圖，背景先補返完整 kline_num 根 → 再 yield 一次完整快照。
                    #    consumer 現有嘅 `n == 1 → baseline，其後 → tick` 自動當呢次係一次重繪 → GUI 零改動，
                    #    而 MAX_VIEW 嘅 zoom-out 深度唔減（呢個就係揀兩段式而唔係直接砍到 300 嘅原因）。
                    #    補取失敗唔折騰整條 stream（用戶已經有圖）→ 只 log，繼續收 push。
                    try:
                        ret2, data2 = await self._fetch_kline(quote_ctx, code, ktype, kline_num)
                        full = self._normalize_kline(data2, ktype) if ret2 == RET_OK else None
                        if full is not None and validate_kline(full)[0]:
                            kline_df = full   # 整份取代（唔 concat：兩段重疊會重複同一啲 bar）
                            yield kline_df.copy()
                        else:
                            logger.warning("FUTU stream_kline: 補取完整 %s 根失敗，維持 %s 根", kline_num, first_num)
                    except Exception as e:
                        logger.warning("FUTU stream_kline: 補取完整 K 線異常 %s: %s", type(e).__name__, e)

                while True:
                    yielded = False
                    # 🤖 關鍵檢查（B1 fix）：每圈 drain 晒 buffer 所有待處理 bar — 高頻 push 都唔會無界增長
                    while True:
                        try:
                            latest_bar = push_q.get_nowait()
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
                # 🤖 安全拔線：退返呢條 stream 嘅訂閱（refcount 歸零先 unsubscribe）。
                #    同步、唔 await — 被 cancel 嘅 generator 入面 await 有風險，而且其他 stream 仲用緊共享 ctx，
                #    真正收線由 `disconnect()` 負責。
                self._release(code, ktype, push_q)

        return True, _stream(), None

    async def get_ticker(self):
        logger.info('Futu get_ticker (來自獨立的 Futu 引擎)')

    # ══════════ 💳 交易能力（#34b — 契約見 modules/trade_base.py）══════════
    # ⚠️ 同行情段一樣：所有 SDK 調用一律 `asyncio.to_thread`（gRPC blocking，唔准 block event loop）。
    # ⚠️ 交易 ctx 同行情 ctx **分開**兩條（OpenD 支援並發，兩套 API 互不阻塞）。呢條俾「策略自動落單」用；
    #    `futu_trade_page` 嘅 `_FutuTradeWorker` 自己仲有一條俾「人落單」用 — 兩條并存係 OpenD 允許嘅。
    # ⚠️ 落單成功 = 已送出俾 OpenD，**唔係已成交**（回執只有 order_id）→ 呼叫方必須如實講。

    def _open_trd_ctx(self):
        from futu import OpenSecTradeContext   # lazy — import 要幾秒
        return OpenSecTradeContext(host=self.host, port=self.port)

    async def _ensure_trd_ctx(self):
        """lazy 開一條共享交易 ctx → (ctx, None)；開唔到 → (None, 原因原文)。
           ⚠️ 開 ctx 本身都係 blocking（OpenD handshake）→ 一併 to_thread，唔准喺 event loop 上做。"""
        if self._trd_ctx is not None:
            return self._trd_ctx, None
        async with self._trd_lock:
            if self._trd_ctx is None:
                try:
                    self._trd_ctx = await asyncio.to_thread(self._open_trd_ctx)
                    logger.info('FUTU 交易: 開咗共享 OpenSecTradeContext %s:%s', self.host, self.port)
                except Exception as e:
                    return None, f'OpenD 交易連線開唔到: {e}'
            return self._trd_ctx, None

    async def _drop_trd_ctx(self):
        """收返交易 ctx（失敗/斷開後重試就係靠呢度清掉，下一手重新 handshake）。"""
        ctx, self._trd_ctx = self._trd_ctx, None
        if ctx is not None:
            try:
                await asyncio.to_thread(ctx.close)
            except Exception:
                pass

    @staticmethod
    def _is_unlock_error(msg):
        """OpenD 錯誤字串屬「交易未解鎖」類？新版 OpenD 只容許喺 GUI 手動解鎖 → 呢個係唯一信號。"""
        s = str(msg).lower()
        return '解锁' in s or '解鎖' in s or 'unlock' in s

    @staticmethod
    def _acc_id(account):
        """futu 每個交易 API 都要 acc_id（int）。冇傳就如實報 — 亂揀一個帳戶落單係會動真錢嘅事。"""
        try:
            return int(account), None
        except (TypeError, ValueError):
            return None, '未指定交易帳戶（acc_id）— 先喺頁揀帳戶'

    @staticmethod
    def _rows(df, cols):
        """DataFrame → list[dict]（只取 cols 之中實際存在嘅欄；券商少俾欄唔會炸，如實少欄）。"""
        if not hasattr(df, 'to_dict'):
            return []
        return [{c: r[c] for c in cols if c in r} for r in df.to_dict(orient='records')]

    async def trade_accounts(self):
        from futu import RET_OK
        ctx, err = await self._ensure_trd_ctx()
        if err:
            return False, None, err
        ret, accs = await asyncio.to_thread(ctx.get_acc_list)
        if ret != RET_OK:
            await self._drop_trd_ctx()   # 攞唔到帳戶通常係 ctx 已死 → 清咗等下一手重連（同交易頁一樣自愈）
            return False, None, str(accs)
        return True, self._rows(accs, ACC_COLS), ''

    async def place_order(self, *, code, side, qty, price=None, account=None,
                          env=None, order_type=None, tif=None):
        from futu import OrderType, RET_OK, TimeInForce, TrdSide
        sd = clamp_side(side)
        if sd is None:
            return False, None, f'買賣方向唔識: {side!r}（只收 BUY/SELL）'
        acc, err = self._acc_id(account)
        if err:
            return False, None, err
        try:
            q = int(qty)
            p = float(price) if price not in (None, '') else 0.0
        except (TypeError, ValueError):
            return False, None, f'數量/價格唔係數字: qty={qty!r} price={price!r}'
        if q <= 0:
            return False, None, f'數量必須大於 0: {q}'
        ctx, err = await self._ensure_trd_ctx()
        if err:
            return False, None, err
        ret, data = await asyncio.to_thread(
            ctx.place_order, price=p, qty=q, code=str(code).strip(),
            trd_side=TrdSide.BUY if sd == 'BUY' else TrdSide.SELL,
            order_type=getattr(OrderType, str(order_type or 'NORMAL').upper(), None),
            trd_env=clamp_env(env), acc_id=acc,
            time_in_force=getattr(TimeInForce, str(tif or 'DAY').upper(), None))
        if ret != RET_OK:
            return False, None, str(data)
        oid = data['order_id'].iloc[0] if hasattr(data, 'columns') and not data.empty else ''
        # status 空字串 = OpenD 落單回執唔帶狀態 → 成交與否要由 open_orders 先睇到（契約已寫明）
        return True, {'order_id': str(oid), 'code': str(code).strip(), 'side': sd,
                      'qty': q, 'price': p, 'status': ''}, ''

    async def cancel_order(self, *, order_id, account=None, env=None):
        from futu import ModifyOrderOp, RET_OK
        acc, err = self._acc_id(account)
        if err:
            return False, None, err
        try:
            oid = int(order_id)
        except (TypeError, ValueError):
            return False, None, f'訂單號唔係整數: {order_id!r}'
        ctx, err = await self._ensure_trd_ctx()
        if err:
            return False, None, err
        ret, msg = await asyncio.to_thread(
            ctx.modify_order, ModifyOrderOp.CANCEL, oid,
            qty=0, price=0, trd_env=clamp_env(env), acc_id=acc)
        if ret != RET_OK:
            return False, None, str(msg)
        return True, str(msg), ''

    async def open_orders(self, *, account=None, env=None):
        from futu import RET_OK
        acc, err = self._acc_id(account)
        if err:
            return False, None, err
        ctx, err = await self._ensure_trd_ctx()
        if err:
            return False, None, err
        ret, df = await asyncio.to_thread(ctx.order_list_query, trd_env=clamp_env(env), acc_id=acc)
        if ret != RET_OK:
            return False, None, str(df)
        return True, self._rows(df, ORDER_COLS), ''

    async def positions(self, *, account=None, env=None):
        from futu import RET_OK
        acc, err = self._acc_id(account)
        if err:
            return False, None, err
        ctx, err = await self._ensure_trd_ctx()
        if err:
            return False, None, err
        ret, df = await asyncio.to_thread(ctx.position_list_query, trd_env=clamp_env(env), acc_id=acc)
        if ret != RET_OK:
            return False, None, str(df)
        return True, self._rows(df, POS_COLS), ''

    async def account_info(self, *, account=None, env=None):
        from futu import RET_OK
        acc, err = self._acc_id(account)
        if err:
            return False, None, err
        ctx, err = await self._ensure_trd_ctx()
        if err:
            return False, None, err
        ret, df = await asyncio.to_thread(ctx.accinfo_query, trd_env=clamp_env(env), acc_id=acc)
        if ret != RET_OK:
            return False, None, str(df)
        recs = df.to_dict(orient='records') if hasattr(df, 'to_dict') else []
        info = recs[0] if recs else {}
        missing = [k for k in ACCINFO_KEYS if k not in info]   # 如實：少欄要睇到，唔扮齊
        if missing:
            logger.warning('FUTU 交易: accinfo 缺少欄位 %s', missing)
        return True, info, ''

    async def unlock_status(self, *, account=None, env=None):
        """解鎖狀態探測 — 撤一個唔存在嘅 order_id（無副作用）。真 OpenD 實測：未解鎖 → unlock 錯誤
           優先於「訂單不存在」；已解鎖 → 訂單不存在類錯誤。其他錯誤 → known=False（如實未知）。"""
        from futu import ModifyOrderOp, RET_OK
        acc, err = self._acc_id(account)
        if err:
            return False, None, err
        ctx, err = await self._ensure_trd_ctx()
        if err:
            return False, None, err
        ret, msg = await asyncio.to_thread(
            ctx.modify_order, ModifyOrderOp.CANCEL, 999999999,
            qty=0, price=0, trd_env=clamp_env(env), acc_id=acc)
        if self._is_unlock_error(msg):
            return True, {'unlocked': False, 'known': True, 'hint': '去 OpenD GUI 手動「解鎖交易」'}, ''
        low = str(msg).lower()
        if ret == RET_OK or '不存在' in str(msg) or 'not exist' in low or 'not found' in low \
                or 'invalid' in low or 'order' in low:
            return True, {'unlocked': True, 'known': True, 'hint': ''}, ''
        return True, {'unlocked': False, 'known': False, 'hint': str(msg)}, ''
import asyncio
import pandas as pd
from ib_async import IB, Stock, util



class IBClient():
    def __init__(self,config=None):
        #print(f'IB Config {config}')
        self.host = config.get("host", "127.0.0.1")
        self.port = config.get("port", 4001)
        self.symbol_map={ 'US':'USD', 'HK':'HKD' }
        self.currency_map={ 'US':'USD', 'HK':'HKD' }
        self.exchange_map = {'US': 'SMART', 'HK': 'SEHK'}
        self.ktype_map = {
            # 🤖 只用作 bar size；duration 改由 _calc_duration() 按 kline_num 動態計算（get/stream 共用）
            'K_1M':   ('1 min',   None),
            'K_5M':   ('5 mins',  None),
            'K_15M':  ('15 mins', None),
            'K_60M':  ('1 hour',  None),
            'K_DAY':  ('1 day',   None),
            'K_WEEK': ('1 week',  None),
        }
        # 🤖 共享持久連線狀態：8 條 stream 共用同一條 IB 連線（IB 禁止同一 clientId 開多條）
        self.ib = None
        self._connected = False
        self._connect_lock = asyncio.Lock()

    async def _ensure_connected(self):
        """確保共享連線已建立；可並發呼叫（冪等），唔會重複 connect。"""
        async with self._connect_lock:
            if self._connected and self.ib is not None:
                return
            ib = IB()
            await ib.connectAsync(self.host, self.port, clientId=99)
            self.ib = ib
            self._connected = True

    async def disconnect(self):
        """斷開共享連線（由 BrokerClient.__aexit__ 統一呼叫）。"""
        async with self._connect_lock:
            if getattr(self, 'ib', None) is not None:
                try:
                    self.ib.disconnect()
                except Exception:
                    pass
            self.ib = None
            self._connected = False

    async def __aenter__(self):
        # 共享連線：只確保已連接；離開 context 唔會斷線（8 條 stream 共用同一條）
        await self._ensure_connected()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # 🤖 故意唔斷線：連線係共享嘅，由 BrokerClient.__aexit__ 統一清理
        return False

    def _normalize_kline(self,data,ktype):
        data = data.rename(columns={'date': 'time_key'})
        # 🤖 IB 回傳嘅 date 係 string（如 "2026-10-02"），pandas 3.x 會保持 str dtype，直接 .dt 會 crash — 先 parse 成 datetime；若帶 tz 再 strip
        tk = pd.to_datetime(data['time_key'], errors='coerce')
        if tk.dt.tz is not None:
            tk = tk.dt.tz_localize(None)
        data['time_key'] = tk
        columns_to_drop = ['average', 'name', 'turnover', 'barCount']
        data =  data.drop(columns=columns_to_drop, errors='ignore')
        data['volume'] = data['volume'].astype(int)
        for col in ['open', 'high', 'low', 'close']:
            if col in data.columns:
                data[col] = data[col].astype(float)
        standard_order = ['time_key', 'open', 'high', 'low', 'close', 'volume']
        data = data[standard_order]
        return data

    def _parse_code(self,code):
        if "." not in code:
            raise ValueError(f"Invalid code format (expected 'MARKET.SYMBOL'): {code}")
        market, symbol = code.split(".", 1)
        market = market.strip().upper()
        symbol = symbol.strip()
        currency = self.currency_map.get(market, "USD")
        exchange = self.exchange_map.get(market, "US")

        if market == "HK" and symbol.isdigit():
            symbol = str(int(symbol))

        return market, symbol,exchange,currency

    def _calc_duration(self, ktype, kline_num):
        # 🤖 根據 K 線週期 + 根數計算歷史窗口（get_kline / stream_kline 共用）
        # ⚠️ 窗口係「日曆日」但 bar 只喺交易日有：週末/假期食走 ~30%，港股 session 又比美股短 —
        #    margin 故意放大，確保回傳 > kline_num 根（多取嘅由 .tail(kline_num) 切走）；唔使精準，多過就得
        if ktype == 'K_1M':
            days = (kline_num // 390) * 2 + 5   # 一天約 390 根（港股基準，最保守）
            return f"{days} D"
        elif ktype == 'K_5M':
            days = (kline_num // 78) * 2 + 5    # 一天約 78 根
            return f"{days} D"
        elif ktype == 'K_15M':
            days = (kline_num // 26) * 2 + 5    # 一天約 26 根
            return f"{days} D"
        elif ktype == 'K_60M':
            days = int(kline_num / 5) * 2 + 5   # 港股一日只有 ~5-6 根（美股盤前盤後會多好多）
            return f"{days} D"
        elif ktype == 'K_DAY':
            months = (kline_num // 20) * 2 + 3  # 一個月約 20 個交易日，×2 留 margin
            return f"{months} M"
        elif ktype == 'K_WEEK':
            years = (kline_num // 52) * 2 + 2   # 一年 ~50-52 週，×2 留 margin（上市唔夠長就係全部）
            return f"{years} Y"
        else:
            return '2 D'

    async def stream_kline(self, code, ktype, kline_num=100):
        # 0️⃣ 確保共享連線已建立（冪等，8 條 stream 共用同一條）
        await self._ensure_connected()
        # 1️⃣ 先用 get_kline 取歷史 K 線做初始底表（HISTORY）
        status, data, _ = await self.get_kline(code, ktype, kline_num=kline_num)
        if not status or data is None:
            print("❌ IB stream_kline: 歷史 K 線取得失敗")
            return
        print("IB stream_kline...")

        # 2️⃣ 先 yield 一次完整歷史快照
        yield data

        market, symbol, exchange, currency = self._parse_code(code)
        contract = Stock(symbol, exchange, currency)

        # 🤖 同 get_kline 用同一個動態窗口，確保歷史底表同 live 訂閱範圍一致
        bar_size, _ = self.ktype_map.get(ktype, ('1 day', None))
        duration = self._calc_duration(ktype, kline_num)

        try:
            bars = await self.ib.reqHistoricalDataAsync(
                contract,
                endDateTime='',
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow='TRADES',
                useRTH=False,  # 🤖 同 get_kline 保持一致（含盤前盤後）
                keepUpToDate=True
            )
        except Exception as e:
            print(f"❌ IB stream_kline: keepUpToDate 訂閱失敗 {e}")
            return

        # 🤖 累積器：以 get_kline 嘅歷史做底，之後逐條拼接新 bar
        kline_df = data.copy()

        # 🤖 快取區：記錄有幾多筆新推送等待送出
        data_buffer = []

        # 🤖 定義回呼函式：IB 每 PUSH 一次就將最新 bar 拼入 K 線表，並標記待送
        def on_bar_update(bars_obj, has_new_bar):
            nonlocal kline_df
            latest_row = self._normalize_kline(util.df(bars_obj).tail(1), ktype)
            if latest_row.empty:
                return
            tk = latest_row['time_key'].iloc[0]
            if not kline_df.empty:
                last_tk = kline_df['time_key'].iloc[-1]
                if tk < last_tk:
                    # 🤖 比最後一行舊（例如初始批次補發）：忽略，避免亂序拼接
                    return
                elif tk == last_tk:
                    # 同一根 bar（盤中更新）：原地覆蓋最後一行，唔會重複
                    kline_df.iloc[-1] = latest_row.iloc[0]
            else:
                # 新 bar：接到尾端
                kline_df = pd.concat([kline_df, latest_row], ignore_index=True)
            data_buffer.append(True)

        # 掛載監聽事件
        bars.updateEvent += on_bar_update

        try:
            # 🔄 進入長線鎖定迴圈
            while True:
                # 🤖 有新推送就 yield 完整更新後嘅 K 線表到最外層，連線絕對不會斷！
                if data_buffer:
                    data_buffer.pop(0)
                    yield kline_df.copy()

                # 每 0.1 秒釋放一次控制權，維持背景監聽，不佔用 CPU 資源
                await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            print("🛑 [IB PUSH] 接收到終止指令，正在關閉監聽事件...")
            raise  # 🤖 保留取消語義：上層 task.cancel() 先可以正確結束
        finally:
            # 安全拔線
            bars.updateEvent -= on_bar_update

    async def get_kline(self,code,ktype,kline_num=100):
        await self._ensure_connected()  # 🤖 確保共享連線已建立（冪等）
        print('IB get_kline')
        status=False
        data, message=None,None

        market, symbol, exchange,currency = self._parse_code(code)
        # 定義商品：Apple 股票
        contract = Stock(symbol, exchange, currency)

        # 🤖 動態窗口（同 stream_kline 共用同一個 _calc_duration）
        bar_size, _ = self.ktype_map.get(ktype, ('1 day', None))
        duration = self._calc_duration(ktype, kline_num)

        try:
            # 尋找 K 線數據
            bars = await self.ib.reqHistoricalDataAsync(
                contract,
                endDateTime='',
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow='TRADES',
                useRTH=False  # 🤖 同 stream_kline 保持一致（含盤前盤後）
            )
            data = util.df(bars).tail(kline_num).reset_index(drop=True)
        except Exception as e:
            return False, None, str(e)

        status=True
        data = self._normalize_kline(data, ktype)
        return status, data, message


    async def get_ticker(self):
        print('Ib get_ticker (來自獨立的 IB 引擎)')
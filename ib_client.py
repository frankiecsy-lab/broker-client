import asyncio
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
            'K_1M':   ('1 min',   '1 D'),   # 1分鐘K：只保留過去 1 天（大約 390 根，最輕量）
            'K_5M':   ('5 mins',  '2 D'),   # 5分鐘K：保留過去 2 天（大約 156 根）
            'K_15M':  ('15 mins', '5 D'),   # 15分鐘K：保留過去 5 天
            'K_60M':  ('1 hour',  '1 M'),   # 1小時K：保留過去 1 個月
            'K_DAY':  ('1 day',   '3 M'),   # 日K：保留過去 3 個月（大約 60 根，方便計算均線）
            'K_WEEK': ('1 week',  '1 Y'),   # 週K：保留過去 1 年
        }

    async def __aenter__(self):
        self.ib=IB()
        await self.ib.connectAsync(self.host, self.port, clientId=99)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if getattr(self, 'ib', None) is not None:
            self.ib.disconnect()
        return False

    def _normalize_kline(self,data,ktype):
        data = data.rename(columns={'date': 'time_key'})
        data['time_key'] = data['time_key'].dt.tz_localize(None)
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

    async def stream_kline(self, code, ktype):

        print("IB stream_kline...")
        market, symbol, exchange, currency = self._parse_code(code)
        contract = Stock(symbol, exchange, currency)

        # 🤖 根據動態週期自動配對最安全的時間長度
        bar_size, duration = self.ktype_map.get(ktype, ('1 day', '2 D'))

        bars = await self.ib.reqHistoricalDataAsync(
            contract,
            endDateTime='',
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow='TRADES',
            useRTH=False,  # 週末除錯用 False
            keepUpToDate=True
        )

        '''history_df = util.df(bars)
        print("\n📜 === [IB 歷史快照] 剛啟動時載入的歷史 K 線數據 ===")
        print(history_df)
        print("==================================================\n")'''

        # 🤖 建立一個快取區（列表），用來存放剛推過來的 JSON 數據
        data_buffer = []

        # 🤖 定義回呼函式，每當 IB 有新價格 PUSH 來時，打包成統一格式塞入快取
        def on_bar_update(bars_obj, has_new_bar):
            latest_df = util.df(bars_obj)
            latest_row = latest_df.tail(1)

            # 打包成與富途完全一致的 統一格式
            json_result = {
                "status": True,
                "data": latest_row.to_dict(orient='records'),
                "message": "IB Stream Update"
            }
            # 塞入快取區
            data_buffer.append(json_result)

        # 掛載監聽事件
        bars.updateEvent += on_bar_update

        try:
            # 🔄 進入長線鎖定迴圈
            while True:
                # 🤖 如果快取區有資料，用 yield 不斷吐到最外層，且連線絕對不會斷！
                if data_buffer:
                    new_data = data_buffer.pop(0)
                    yield new_data

                # 每 0.1 秒釋放一次控制權，維持背景監聽，不佔用 CPU 資源
                await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            print("🛑 [IB PUSH] 接收到終止指令，正在關閉監聽事件...")
        finally:
            # 安全拔線
            bars.updateEvent -= on_bar_update

    async def get_kline(self,code,ktype,kline_num=100):
        print('IB get_kline')
        status=False
        data, message=None,None

        market, symbol, exchange,currency = self._parse_code(code)
        # 定義商品：Apple 股票
        contract = Stock(symbol, exchange, currency)

        # 🤖 根據動態週期自動配對最安全的時間長度（同 stream_kline 一樣）
        bar_size, duration = self.ktype_map.get(ktype, ('1 day', '2 D'))

        if ktype == 'K_1M':
            # 1分鐘K：一天 390 根。若大於 390 根需要 2天，否則 1天 就夠
            days = (kline_num // 390) + 1
            duration = f"{days} D"
        elif ktype == 'K_5M':
            # 5分鐘K：一天 78 根。
            days = (kline_num // 78) + 1
            duration = f"{days} D"
        elif ktype == 'K_15M':
            # 15分鐘K：一天 26 根。
            days = (kline_num // 26) + 1
            duration = f"{days} D"
        elif ktype == 'K_60M':
            # 60分鐘K（1小時）：一天 6.5 根。
            days = int(kline_num / 6.5) + 2
            duration = f"{days} D"
        elif ktype == 'K_DAY':
            # 日K：1 根就是 1 天。直接加上安全墊（考慮週末）乘以 1.5 倍天數，或者轉成月份
            # 200 根日 K 大約需要 10 個月 (10 M) 的歷史窗口
            months = (kline_num // 20) + 1  # 一個月大約 20 個交易日
            duration = f"{months} M"
        elif ktype == 'K_WEEK':
            # 週K：1 根是一週。200 根週 K 大約需要 4 年 (4 Y)
            years = (kline_num // 52) + 1
            duration = f"{years} Y"
        else:
            duration = '2 D'

        # 尋找 K 線數據
        bars = await self.ib.reqHistoricalDataAsync(
            contract,
            endDateTime='',
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow='TRADES',
            useRTH=True
        )

        data = util.df(bars).tail(kline_num).reset_index(drop=True)

        status=True
        data = self._normalize_kline(data, ktype)
        return status, data, message


    async def get_ticker(self):
        print('Ib get_ticker (來自獨立的 IB 引擎)')
import asyncio
from ib_async import *
import pandas as pd


class IBClient:
    """真正的 IB_ASYNC 統一入口類別"""

    def __init__(self, host='127.0.0.1', port=4001, client_id=200):
        self.host = host
        self.port = int(port)
        self.client_id = int(client_id)
        self.ib = None

    async def _ensure_connected(self):
        if self.ib is not None and self.ib.isConnected():
            return
        ib = IB()
        try:
            await ib.connectAsync(self.host, self.port, clientId=self.client_id, timeout=15)
        except Exception as e:
            raise RuntimeError(f'連線失敗 {self.host}:{self.port}: {e}') from e
        self.ib = ib

    async def close(self):
        if self.ib is not None:
            try:
                self.ib.disconnect()
                await asyncio.sleep(0.1)
            finally:
                self.ib = None

    async def __aenter__(self):
        await self._ensure_connected()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _get_contract(self, symbol, security_type):
        """內部輔助方法：統一合約獲取邏輯"""
        sec_type = security_type.upper()
        if sec_type == 'STK':
            contract = Stock(symbol, 'SMART', 'USD')
            qualified = await self.ib.qualifyContractsAsync(contract)
            if not qualified or contract.conId == 0:
                return None, f"❌ 找不到股票 {symbol}"
            return contract, None
        elif sec_type == 'FUT':
            search_contract = Future(symbol=symbol, exchange='CME', currency='USD')
            cds = await self.ib.reqContractDetailsAsync(search_contract)
            if not cds:
                return None, f"❌ 找不到任何 {symbol} 期貨合約"
            contracts = [cd.contract for cd in cds]
            contracts.sort(key=lambda c: c.lastTradeDateOrContractMonth)
            return contracts[0], None
        elif sec_type == 'CRYPTO':
            print(f"🔍 辨識為【加密貨幣】：正在建立 {symbol} 合約 (交易所: PAXOS)...")
            # IB 的加密貨幣固定在 PAXOS 交易所，以 USD 計價
            contract = Crypto(symbol, 'PAXOS', 'USD')
            qualified = await self.ib.qualifyContractsAsync(contract)
            if not qualified or contract.conId == 0:
                return None, f"❌ 找不到加密貨幣 {symbol}，請確認是否有開通 IB 加密貨幣交易權限"
            print(f"✅ 成功補全加密貨幣合約 conId: {contract.conId}")
            return contract, None
        elif sec_type == 'CASH':
            sym = symbol.upper()
            if len(sym) == 6:
                base_currency = sym[:3]  # 例如: EUR
                quote_currency = sym[3:]  # 例如: USD
            else:
                base_currency = sym
                quote_currency = 'USD'

            print(f"🔍 辨識為【外匯現貨】：正在建立 {base_currency}.{quote_currency} 合約 (交易所: IDEALPRO)...")
            contract = Forex(pair=f"{base_currency}{quote_currency}", exchange='IDEALPRO')

            qualified = await self.ib.qualifyContractsAsync(contract)
            if not qualified or contract.conId == 0:
                return None, f"❌ 找不到外匯組合 {base_currency}.{quote_currency}，請檢查代碼"
            print(f"✅ 成功補全外匯合約 conId: {contract.conId}")
            return contract, None

        return None, f"❌ 未知的類型: {security_type}，僅支援 'STK', 'FUT', 'CRYPTO' 或 'CASH'"

    def _get_what_to_show(self, security_type):
        """🚀 核心優化：根據安全類型自動決定 whatToShow 參數"""
        sec_type = security_type.upper()
        mapping = {
            'CRYPTO': 'AGGTRADES', # 加密貨幣硬性規定
            'CASH': 'MIDPOINT',    # 外匯現貨硬性規定
            'STK': 'TRADES',       # 股票預設成交價
            'FUT': 'TRADES'        # 期貨預設成交價
        }
        # 如果遇到沒寫進去的類型，預設返回 'TRADES' 防錯
        return mapping.get(sec_type, 'TRADES')

    async def get_kline(self, symbol, security_type='STK', durationStr='1 D', barSizeSetting='1 min'):

        try:
            contract, err = await self._get_contract(symbol, security_type)
            if err: return {'status': 'FAIL', 'data': None, 'message': err}

            wts = self._get_what_to_show(security_type)

            self.ib.reqMarketDataType(3)
            bars = await self.ib.reqHistoricalDataAsync(
                contract, endDateTime='', durationStr=durationStr,
                barSizeSetting=barSizeSetting, whatToShow=wts, useRTH=False, formatDate=1
            )

            if not bars:
                msg = f"❌ {symbol} 拉取 K 線失敗，IB 未返回任何數據（請檢查行情權限、開盤時間或時間參數設置）。"
                print(msg)
                return {'status': 'FAIL', 'data': None, 'message': msg}

            # 3. 統一處理 DataFrame 與平移至 +8 時區
            df = util.df(bars)
            df.set_index('date', inplace=True)

            success_msg = f"🎉 成功獲取 {symbol} 數據，共 {len(df)} 筆"
            print(success_msg)
            return {'status': 'OK', 'data': df, 'message': success_msg}

        except Exception as e:
            # 攔截任何未知的系統崩潰或網路中斷異常
            error_msg = f"💥 執行 get_kline_df 時發生非預期異常錯誤: {str(e)}"
            print(error_msg)
            return {'status': 'ERROR', 'data': None, 'message': error_msg}

    async def get_kline_live(self, symbol, security_type='STK', durationStr='1 D', barSizeSetting='1 min',
                                only_new_bar=True):
        """
        🔥 新增功能：先獲取歷史 K 線，再自動 keepUpToDate 實時更新

        :param durationStr: 初始載入的歷史長度 (例如 '1 D' 獲取大約幾百條 1 分鐘線)
        :param barSizeSetting: K 線週期 ('1 min', '5 mins', etc.)
        :param only_new_bar: True 表示只有在「一根 Bar 完結收盤」時才推送；
                             False 表示每次最新價格跳動（當前 Bar 還在變動）都推送
        """
        contract, err = await self._get_contract(symbol, security_type)
        if err:
            print(err)
            return

        print(f"📡 [KeepUpToDate] 正在初始化 {symbol} ({barSizeSetting}) 歷史數據並建立即時串流...")
        queue = asyncio.Queue()

        # 當有新價格跳動或新 Bar 生成時的事件回調
        def on_bar_update(bars, has_new_bar):
            if not bars:
                return

            # 如果設定 only_new_bar=True，則必須在真正形成新 Bar 時，才轉發「上一根已閉合」的 Bar
            if only_new_bar:
                if has_new_bar and len(bars) > 1:
                    # bars[-1] 是剛剛新開的 Bar，bars[-2] 是剛剛完美收盤閉合的 Bar
                    closed_bar = bars[-2]
                    df_bar = util.df([closed_bar])
                    asyncio.create_task(queue.put(df_bar))
            else:
                # 實時推送當前最新的 Bar（包含未收盤的即時跳動）
                last_bar = bars[-1]
                df_bar = util.df([last_bar])
                asyncio.create_task(queue.put(df_bar))

        wts = self._get_what_to_show(security_type)
        self.ib.reqMarketDataType(3)

        # 📌 關鍵：endDateTime 必須為空，且 keepUpToDate 設為 True
        bars = await self.ib.reqHistoricalDataAsync(
            contract, endDateTime='', durationStr=durationStr,
            barSizeSetting=barSizeSetting, whatToShow=wts, useRTH=False, formatDate=1,
            keepUpToDate=True
        )

        if not bars:
            print(f"❌ 無法建立 {symbol} 的實時更新流，歷史數據返回為空。")
            return

        # 1. 首先把初次取回的歷史數據轉成 DataFrame 先 yield 出去
        df_init = util.df(bars)
        df_init.set_index('date', inplace=True)
        print(f"✅ 歷史數據載入完成，共 {len(df_init)} 筆。開始監聽後續即時更新...")
        yield df_init

        # 2. 註冊監聽事件，捕捉後續的自動更新
        bars.updateEvent += on_bar_update

        try:
            # 不斷等待事件把最新 Bar 塞進 Queue，並即時 Yield 吐出
            while True:
                df_bar = await queue.get()
                df_bar.set_index('date', inplace=True)
                yield df_bar
                queue.task_done()
        except asyncio.CancelledError:
            # 🟢 智慧捕捉：完美攔截 K 線串流的中斷訊號，防止長串 Traceback 往外噴
            pass
        finally:
            # 外部中斷（如 break）時自動解除監聽
            print(f"🛑 停止 {symbol} 即時監聽，註銷事件...")
            bars.updateEvent -= on_bar_update
            self.ib.cancelHistoricalData(bars)

    async def get_ticks_live(self, symbol, security_type):
        """
        🔥 新增功能：實時逐筆成交串流 (Time & Sales / Tick-by-Tick)
        支援不同資產的底層規定（加密貨幣自動對應 AGGTRADES / 外匯現貨自動對應 BidAsk 或 MidPoint）
        """
        contract, err = await self._get_contract(symbol, security_type)
        if err:
            print(err)
            return

        sec_type = security_type.upper()
        # 決定 Tick 的訂閱類型：外匯通常看 BidAsk（買賣價），股票/期貨/加密貨幣看 Last（最新成交）
        tick_type = 'BidAsk' if sec_type == 'CASH' else 'Last'

        print(f"📡 [TickStream] 正在啟動 {symbol} 實時逐筆成交監聽 (類型: {tick_type})...")
        queue = asyncio.Queue()

        # 當 IB 伺服器推來全新 Tick 數據時的回調事件
        def on_tick_update(ticks):
            for tick in ticks:
                data_dict = {'time': pd.to_datetime(tick.time)}

                if tick_type == 'Last' and hasattr(tick, 'price') and tick.size > 0:
                    # 處理標準成交數據（股票/期貨/加密貨幣）
                    data_dict.update({'price': tick.price, 'size': tick.size, 'exchange': getattr(tick, 'exchange', '')})
                elif tick_type == 'BidAsk' and hasattr(tick, 'bidPrice'):
                    # 處理外匯買賣價跳動（因為外匯現貨沒有單一成交價，看 Midpoint 或是買賣報價）
                    mid_price = (tick.bidPrice + tick.askPrice) / 2 if tick.askPrice > 0 else tick.bidPrice
                    data_dict.update({'price': mid_price, 'size': tick.bidSize + tick.askSize, 'exchange': 'IDEALPRO'})
                else:
                    continue

                # 轉換為單行 DataFrame 並推入非同步佇列
                asyncio.create_task(queue.put(pd.DataFrame([data_dict])))

        self.ib.reqMarketDataType(3)  # 啟用延遲數據模式防錯

        # 呼叫 IB 原生 Tick-by-Tick 接口
        ticks_data = self.ib.reqTickByTickData(contract, tickType=tick_type, numberOfTicks=0, ignoreSize=False)
        ticks_data.updateEvent += on_tick_update

        try:
            while True:
                df_tick = await queue.get()
                yield df_tick
                queue.task_done()
        except asyncio.CancelledError:
            # 🟢 智慧捕捉：當使用者按下 Ctrl+C 時，在這裡溫柔地攔截，防止錯誤往外噴
            pass
        finally:
            print(f"🛑 停止 {symbol} 逐筆成交監聽，正在乾淨註銷事件與訂閱...")
            ticks_data.updateEvent -= on_tick_update
            self.ib.cancelTickByTickData(contract, tick_type)

    async def get_depth_live(self, symbol, security_type, num_rows=5):
        """
        🔥 新增功能：實時訂單流 / 市場深度串流 (Order Flow / Level 2 Market Depth) - 優化安全版
        :param num_rows: 想要監控的檔數（例如 5檔 或 10檔 掛單）
        """
        contract, err = await self._get_contract(symbol, security_type)
        if err:
            print(err)
            return

        # 📌 1. 智慧防禦：外匯現貨 (CASH) 不支援深度數據，直接阻斷，防止向 TWS 發送無效請求
        if security_type.upper() == 'CASH':
            print(f"❌ IB 官方不提供外匯現貨 ({symbol}) 的訂單流 L2 深度數據，請改用 K 線或 Tick 串流。")
            return

        print(f"📡 [OrderFlow] 正在啟動 {symbol} 實時市場深度監聽 (檔數: {num_rows})...")
        queue = asyncio.Queue()

        # 當買賣盤口有掛單變動時的回調事件
        def on_depth_update(depth, changes):
            bids = [{'bid_price': entry.price, 'bid_size': entry.size} for entry in depth.bids[:num_rows]]
            asks = [{'ask_price': entry.price, 'ask_size': entry.size} for entry in depth.asks[:num_rows]]

            # 打包成一個字典，並放入佇列
            asyncio.create_task(queue.put({
                'time': pd.Timestamp.now(),
                'bids': bids,
                'asks': asks
            }))

        self.ib.reqMarketDataType(3)  # 啟用延遲數據模式防錯

        # 請求訂單流深度數據
        depth_data = self.ib.reqMktDepth(contract, numRows=num_rows, isSmartDepth=True)
        depth_data.updateEvent += on_depth_update

        try:
            while True:
                data_dict = await queue.get()
                yield data_dict
                queue.task_done()
        except asyncio.CancelledError:
            # 📌 2. 智慧捕捉：當外面 Ctrl+C 被中斷時，在這裡溫柔地攔截，避免把錯誤往外拋導致崩潰
            pass
        finally:
            print(f"🛑 停止 {symbol} 訂單流監聽，正在乾淨註銷事件與訂閱...")
            if depth_data:
                depth_data.updateEvent -= on_depth_update

                # 📌 完美主義修復：只有當 reqId 真正存在（大於 0）時，才呼叫 API 取消，徹底根除 cancelMktDepth 小警告
                if getattr(depth_data, 'reqId', 0) > 0:
                    try:
                        self.ib.cancelMktDepth(depth_data, isSmartDepth=True)
                    except Exception:
                        pass
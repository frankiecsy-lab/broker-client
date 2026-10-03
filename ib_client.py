import asyncio
from ib_async import *
import pandas as pd


class IBClient:
    """真正的 IB_ASYNC 統一入口類別

    🔥 特性：
    - 同一條連線可並行訂閱多個串流（K線 / Tick / 盤口互不衝突）
    - 斷線自動重連（線性退避），成功後自動重建所有進行中的訂閱
    - 訂閱被 IB 拒絕時（無行情權限等），錯誤會以 {'error': ...} 送進串流，上層看得見
    - market_data_type 預設不強制降級：讓 IB 用帳號可用的最佳數據；需要純延遲模式可傳 3
    """

    RECONNECT_MAX_ATTEMPTS = 5      # 自動重連最大嘗試次數
    RECONNECT_BASE_DELAY = 2.0      # 基礎間隔（秒），線性退避：2s, 4s, 6s ...

    def __init__(self, host='127.0.0.1', port=4001, client_id=200, auto_reconnect=True):
        self.host = host
        self.port = int(port)
        self.client_id = int(client_id)
        self.auto_reconnect = auto_reconnect
        self.ib = None
        self._closed = False
        self._reconnecting = False
        self._subscriptions = []    # 進行中串流的「重建函數」登記處（重連成功後自動呼叫）

    # ================= 連線管理 / 自動重連 =================

    async def _ensure_connected(self):
        if self.ib is not None and self.ib.isConnected():
            return
        await self._connect()

    async def _connect(self):
        ib = IB()
        try:
            await ib.connectAsync(self.host, self.port, clientId=self.client_id, timeout=15)
        except Exception as e:
            raise RuntimeError(f'連線失敗 {self.host}:{self.port}: {e}') from e
        self.ib = ib
        if self.auto_reconnect:
            ib.disconnectedEvent += self._on_disconnected

    def _on_disconnected(self):
        """IB 底層斷線事件（Gateway 重啟 / 網路中斷）→ 啟動自動重連"""
        if self._closed or self._reconnecting:
            return
        print(f"🔌 [IBClient] 連線中斷！{self.host}:{self.port} 啟動自動重連...")
        self._reconnecting = True
        asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self):
        try:
            for attempt in range(1, self.RECONNECT_MAX_ATTEMPTS + 1):
                if self._closed:
                    return
                await asyncio.sleep(self.RECONNECT_BASE_DELAY * attempt)
                if self._closed:
                    return
                try:
                    print(f"🔁 [IBClient] 重連嘗試 {attempt}/{self.RECONNECT_MAX_ATTEMPTS} ...")
                    await self.ib.connectAsync(self.host, self.port, clientId=self.client_id, timeout=15)
                except Exception as e:
                    print(f"⚠️ [IBClient] 第 {attempt} 次重連失敗：{e}")
                    continue

                # 重連成功 → 逐一重建所有進行中的訂閱
                print("✅ [IBClient] 重連成功，正在重建進行中的訂閱...")
                for resub, _queues in list(self._subscriptions):
                    try:
                        await resub()
                    except Exception as e:
                        print(f"⚠️ [IBClient] 重建某筆訂閱失敗：{e}")
                return

            print(f"❌ [IBClient] 重連 {self.RECONNECT_MAX_ATTEMPTS} 次皆失敗，放棄。串流已停止。")
            for _resub, queues in self._subscriptions:
                for q in queues:
                    try:
                        q.put_nowait({'error': 'Error 0: 自動重連失敗（已達最大嘗試次數），串流已停止'})
                    except Exception:
                        pass
        finally:
            self._reconnecting = False

    async def close(self):
        self._closed = True
        if self.ib is not None:
            try:
                # 先卸除斷線監聽再斷線，避免手動關閉觸發自動重連
                if self.auto_reconnect:
                    self.ib.disconnectedEvent -= self._on_disconnected
                self.ib.disconnect()
            finally:
                self.ib = None

    async def __aenter__(self):
        await self._ensure_connected()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    # ================= 訂閱登記 / 錯誤轉發 =================

    def _register_subscription(self, resub_fn, error_queues=()):
        """登記一個串流的「重建函數」，斷線重連成功後會自動被呼叫。

        error_queues: 該串流的数据 queue；若最終重連失敗，會往裡面送錯誤標記讓上層優雅退出"""
        self._subscriptions.append((resub_fn, list(error_queues)))
        return len(self._subscriptions) - 1

    def _unregister_subscription(self, idx):
        if 0 <= idx < len(self._subscriptions):
            del self._subscriptions[idx]

    def _resolve_ticker_req_id(self, ticker, tick_type):
        """解析 ib_async 內部指派給這個 Ticker 訂閱的 reqId（私有 API，解析不到回傳 None）"""
        try:
            return self.ib.wrapper.ticker2ReqId[tick_type].get(ticker)
        except Exception:
            return None

    def _watch_request_errors(self, queue, req_id=None, contract=None):
        """把指定訂閱的 IB 錯誤送進串流 queue（例如無行情權限），讓上層看得見而不是靜默凍結。

        優先以 reqId 精確比對；解析不到時退回以 contract.conId 比對"""
        con_id = getattr(contract, 'conId', 0) or None

        def on_error(reqId, errorCode, errorString, err_contract):
            matched = (req_id is not None and reqId == req_id) or \
                      (req_id is None and con_id is not None and
                       getattr(err_contract, 'conId', 0) == con_id)
            if matched:
                try:
                    queue.put_nowait({'error': f'Error {errorCode}: {errorString}'})
                except Exception:
                    pass

        self.ib.errorEvent += on_error
        return on_error

    # ================= 合約解析 =================

    async def _get_contract(self, symbol, security_type, currency='USD'):
        """內部輔助方法：統一合約獲取邏輯"""
        sec_type = security_type.upper()
        if sec_type == 'STK':
            contract = Stock(symbol, 'SMART', currency)
            qualified = await self.ib.qualifyContractsAsync(contract)
            if not qualified or contract.conId == 0:
                return None, f"❌ 找不到股票 {symbol}"
            return contract, None
        elif sec_type == 'FUT':
            search_contract = Future(symbol=symbol, exchange='CME', currency=currency)
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

    def _apply_market_data_type(self, market_data_type):
        """預設不強制數據類型（讓 IB 用帳號可用的最佳數據）；需要純延遲模式才傳 3"""
        if market_data_type is not None:
            self.ib.reqMarketDataType(market_data_type)

    # ================= 歷史 K 線 =================

    async def get_kline(self, symbol, security_type='STK', durationStr='1 D', barSizeSetting='1 min',
                        currency='USD', market_data_type=None):

        try:
            contract, err = await self._get_contract(symbol, security_type, currency)
            if err: return {'status': 'FAIL', 'data': None, 'message': err}

            wts = self._get_what_to_show(security_type)
            self._apply_market_data_type(market_data_type)

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

    # ================= 即時 K 線串流 (keepUpToDate) =================

    async def get_kline_live(self, symbol, security_type='STK', durationStr='1 D', barSizeSetting='1 min',
                             only_new_bar=True, currency='USD', market_data_type=None, init_timeout=30):
        """
        🔥 功能：先獲取歷史 K 線，再自動 keepUpToDate 實時更新

        :param durationStr: 初始載入的歷史長度 (例如 '1 D' 獲取大約幾百條 1 分鐘線)
        :param barSizeSetting: K 線週期 ('1 min', '5 mins', etc.)
        :param only_new_bar: True 表示只有在「一根 Bar 完結收盤」時才推送；
                             False 表示每次最新價格跳動（當前 Bar 還在變動）都推送
        :param init_timeout: 初始歷史數據的超時秒數，防止無行情權限時永久卡死
        """
        contract, err = await self._get_contract(symbol, security_type, currency)
        if err:
            print(err)
            return

        print(f"📡 [KeepUpToDate] 正在初始化 {symbol} ({barSizeSetting}) 歷史數據並建立即時串流...")
        queue = asyncio.Queue()

        # 當有新價格跳動或新 Bar 生成時的事件回調（回調已在 event loop 內，put_nowait 零開銷）
        def on_bar_update(bars, has_new_bar):
            if not bars:
                return

            # 如果設定 only_new_bar=True，則必須在真正形成新 Bar 時，才轉發「上一根已閉合」的 Bar
            if only_new_bar:
                if has_new_bar and len(bars) > 1:
                    # bars[-1] 是剛剛新開的 Bar，bars[-2] 是剛剛完美收盤閉合的 Bar
                    closed_bar = bars[-2]
                    df_bar = util.df([closed_bar])
                    queue.put_nowait(df_bar)
            else:
                # 實時推送當前最新的 Bar（包含未收盤的即時跳動）
                last_bar = bars[-1]
                df_bar = util.df([last_bar])
                queue.put_nowait(df_bar)

        wts = self._get_what_to_show(security_type)
        state = {'bars': None}

        async def subscribe():
            # 🔥 加 timeout：無權限時 IB 對 keepUpToDate 請求可能永遠不回應（不給數據也不送錯誤），不能永久卡死
            bars = await asyncio.wait_for(
                self.ib.reqHistoricalDataAsync(
                    contract, endDateTime='', durationStr=durationStr,
                    barSizeSetting=barSizeSetting, whatToShow=wts, useRTH=False, formatDate=1,
                    keepUpToDate=True),
                timeout=init_timeout)
            if not bars:
                raise RuntimeError(f'{symbol} 歷史數據返回為空（請檢查行情權限）')
            self._apply_market_data_type(market_data_type)
            bars.updateEvent += on_bar_update
            state['bars'] = bars

        def detach():
            b = state['bars']
            if b is not None:
                try:
                    b.updateEvent -= on_bar_update
                except Exception:
                    pass

        async def resub():
            """斷線重連成功後的重建函數：重新訂閱，並補發一次初始歷史（上層會依 index 去重）"""
            detach()
            await subscribe()
            # 注意：這裡不 set_index —— 主循環會對 queue 裡的每個 item 統一做 set_index('date')
            queue.put_nowait(util.df(state['bars']))

        try:
            await subscribe()
        except (asyncio.TimeoutError, TimeoutError):
            print(f"❌ {symbol} 實時串流初始化超時（{init_timeout}s 內未收到歷史數據，通常是無行情權限）。")
            return
        except RuntimeError as e:
            print(f"❌ 無法建立 {symbol} 的實時更新流：{e}")
            return

        sub_idx = self._register_subscription(resub, error_queues=(queue,))

        # 1. 首先把初次取回的歷史數據轉成 DataFrame 先 yield 出去
        df_init = util.df(state['bars'])
        df_init.set_index('date', inplace=True)
        print(f"✅ 歷史數據載入完成，共 {len(df_init)} 筆。開始監聽後續即時更新...")
        yield df_init

        # 2. 註冊監聽事件，捕捉後續的自動更新
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
            self._unregister_subscription(sub_idx)
            detach()
            b = state['bars']
            if b is not None:
                try:
                    self.ib.cancelHistoricalData(b)
                except Exception:
                    pass
            print(f"🛑 停止 {symbol} 即時監聽，註銷事件...")

    # ================= 實時逐筆成交串流 (Time & Sales / Tick-by-Tick) =================

    async def get_ticks_live(self, symbol, security_type, currency='USD', market_data_type=None):
        """
        🔥 功能：實時逐筆成交串流 (Time & Sales / Tick-by-Tick)
        支援不同資產的底層規定（加密貨幣自動對應 AGGTRADES / 外匯現貨自動對應 BidAsk 或 MidPoint）

        ⚠️ 當 IB 拒絕訂閱（無行情權限等）時，會 yield 一個 {'error': 'Error ...'} dict，上層應檢查並停止。
        """
        contract, err = await self._get_contract(symbol, security_type, currency)
        if err:
            print(err)
            return

        sec_type = security_type.upper()
        # 決定 Tick 的訂閱類型：外匯通常看 BidAsk（買賣價），股票/期貨/加密貨幣看 Last（最新成交）
        tick_type = 'BidAsk' if sec_type == 'CASH' else 'Last'

        print(f"📡 [TickStream] 正在啟動 {symbol} 實時逐筆成交監聽 (類型: {tick_type})...")
        queue = asyncio.Queue()

        # 當 IB 伺服器推來全新 Tick 數據時的回調事件（回調已在 event loop 內，put_nowait 零開銷）
        def on_tick_update(ticks):
            for tick in ticks:
                data_dict = {'time': pd.to_datetime(tick.time)}

                if tick_type == 'Last' and hasattr(tick, 'price') and tick.size > 0:
                    # 處理標準成交數據（股票/期貨/加密貨幣）
                    data_dict.update({'price': tick.price, 'size': tick.size, 'exchange': getattr(tick, 'exchange', '')})
                elif tick_type == 'BidAsk' and hasattr(tick, 'bidPrice'):
                    # 外匯買賣價跳動（因為外匯現貨沒有單一成交價，看 Midpoint 或是買賣報價）
                    mid_price = (tick.bidPrice + tick.askPrice) / 2 if tick.askPrice > 0 else tick.bidPrice
                    data_dict.update({'price': mid_price, 'size': tick.bidSize + tick.askSize, 'exchange': 'IDEALPRO'})
                else:
                    continue

                # 轉換成單行 DataFrame 並推入非同步佇列
                queue.put_nowait(pd.DataFrame([data_dict]))

        state = {'ticks_data': None, 'error_handler': None}

        def attach(td):
            td.updateEvent += on_tick_update
            old = state['error_handler']
            if old is not None:
                try:
                    self.ib.errorEvent -= old
                except Exception:
                    pass
            # 🔥 把此訂閱的 IB 錯誤轉進串流（例如 Error 10189 無權限），上層看得見而不是靜默凍結
            req_id = self._resolve_ticker_req_id(td, tick_type)
            state['error_handler'] = self._watch_request_errors(queue, req_id=req_id, contract=contract)

        def detach():
            td = state['ticks_data']
            if td is not None:
                try:
                    td.updateEvent -= on_tick_update
                except Exception:
                    pass
            eh = state['error_handler']
            if eh is not None:
                try:
                    self.ib.errorEvent -= eh
                except Exception:
                    pass
                state['error_handler'] = None

        async def subscribe():
            # 呼叫 IB 原生 Tick-by-Tick 接口
            td = self.ib.reqTickByTickData(contract, tickType=tick_type, numberOfTicks=0, ignoreSize=False)
            state['ticks_data'] = td
            attach(td)

        async def resub():
            """斷線重連成功後的重建函數"""
            detach()
            await subscribe()

        try:
            self._apply_market_data_type(market_data_type)
            await subscribe()
        except Exception as e:
            print(f"💥 {symbol} 逐筆成交訂閱啟動失敗：{e}")
            return

        sub_idx = self._register_subscription(resub, error_queues=(queue,))

        try:
            while True:
                item = await queue.get()
                yield item   # DataFrame；或 IB 拒絕訂閱時的 {'error': ...}
                queue.task_done()
        except asyncio.CancelledError:
            # 🟢 智慧捕捉：當使用者按下 Ctrl+C 時，在這裡溫柔地攔截，防止錯誤往外噴導致崩潰
            pass
        finally:
            self._unregister_subscription(sub_idx)
            detach()
            td = state['ticks_data']
            if td is not None:
                try:
                    # cancelTickByTickData 收 (contract, tickType)，ib_async 內部自行查 reqId
                    self.ib.cancelTickByTickData(contract, tick_type)
                except Exception:
                    pass
            print(f"🛑 停止 {symbol} 逐筆成交監聽，正在乾淨註銷事件與訂閱...")

    # ================= 實時訂單流 / 市場深度串流 (Order Flow / L2) =================

    async def get_depth_live(self, symbol, security_type, num_rows=5, currency='USD', market_data_type=None):
        """
        🔥 功能：實時訂單流 / 市場深度串流 (Order Flow / Level 2 Market Depth) - 優化安全版
        :param num_rows: 想要監控的檔數（例如 5檔 或 10檔 掛單）

        ⚠️ 當 IB 拒絕訂閱（無行情權限等）時，會 yield 一個 {'error': 'Error ...'} dict，上層應檢查並停止。
        """
        contract, err = await self._get_contract(symbol, security_type, currency)
        if err:
            print(err)
            return

        # 📌 1. 智慧防禦：外匯現貨 (CASH) 不支援深度數據，直接阻斷，防止向 TWS 發送無效請求
        if security_type.upper() == 'CASH':
            print(f"❌ IB 官方不提供外匯現貨 ({symbol}) 的訂單流 L2 深度數據，請改用 K 線或 Tick 串流。")
            return

        print(f"📡 [OrderFlow] 正在啟動 {symbol} 實時市場深度監聽 (檔數: {num_rows})...")
        queue = asyncio.Queue()

        # 當買賣盤口有掛單變動時的回調事件（回調已在 event loop 內，put_nowait 零開銷）
        def on_depth_update(depth, changes):
            bids = [{'bid_price': entry.price, 'bid_size': entry.size} for entry in depth.bids[:num_rows]]
            asks = [{'ask_price': entry.price, 'ask_size': entry.size} for entry in depth.asks[:num_rows]]

            # 打包成一個字典，並放入佇列
            queue.put_nowait({
                'time': pd.Timestamp.now(),
                'bids': bids,
                'asks': asks
            })

        state = {'depth_data': None, 'error_handler': None}

        def attach(md):
            md.updateEvent += on_depth_update
            old = state['error_handler']
            if old is not None:
                try:
                    self.ib.errorEvent -= old
                except Exception:
                    pass
            # 🔥 把此訂閱的 IB 錯誤轉進串流（例如 Error 354/10189 無權限），上層看得見而不是靜默凍結
            req_id = self._resolve_ticker_req_id(md, 'mktDepth')
            state['error_handler'] = self._watch_request_errors(queue, req_id=req_id, contract=contract)

        def detach():
            md = state['depth_data']
            if md is not None:
                try:
                    md.updateEvent -= on_depth_update
                except Exception:
                    pass
            eh = state['error_handler']
            if eh is not None:
                try:
                    self.ib.errorEvent -= eh
                except Exception:
                    pass
                state['error_handler'] = None

        async def subscribe():
            # 請求訂單流深度數據
            md = self.ib.reqMktDepth(contract, numRows=num_rows, isSmartDepth=True)
            state['depth_data'] = md
            attach(md)

        async def resub():
            """斷線重連成功後的重建函數"""
            detach()
            await subscribe()

        try:
            self._apply_market_data_type(market_data_type)
            await subscribe()
        except Exception as e:
            print(f"💥 {symbol} 訂單流訂閱啟動失敗：{e}")
            return

        sub_idx = self._register_subscription(resub, error_queues=(queue,))

        try:
            while True:
                item = await queue.get()
                yield item   # {'time','bids','asks'}；或 IB 拒絕訂閱時的 {'error': ...}
                queue.task_done()
        except asyncio.CancelledError:
            # 📌 2. 智慧捕捉：當外面 Ctrl+C 中斷時，在這裡溫柔地攔截，防止錯誤往外噴導致崩潰
            pass
        finally:
            self._unregister_subscription(sub_idx)
            detach()
            md = state['depth_data']
            if md is not None:
                try:
                    # cancelMktDepth 收原始 contract（ib_async 內部由它查 reqId），傳 Ticker 會找不到
                    self.ib.cancelMktDepth(contract, isSmartDepth=True)
                except Exception:
                    pass
            print(f"🛑 停止 {symbol} 訂單流監聽，正在乾淨註銷事件與訂閱...")

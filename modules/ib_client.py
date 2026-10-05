import asyncio
import logging
import time
import pandas as pd
from ib_async import *

# 🤖 自動解析會 probe 一堆無效 contract，ib_async 每次都用 logging 印 "Error 200 No security definition" — 太嘈；本檔自己會印 ❌/✅
logging.getLogger('ib_async').setLevel(logging.CRITICAL)



class IBClient():
    def __init__(self,config=None):
        #print(f'IB Config {config}')
        config = config or {}   # 🤖 允許唔傳 config（用預設 host/port）
        self.host = config.get("host", "127.0.0.1")
        self.port = config.get("port", 4001)
        self.symbol_map={ 'US':'USD', 'HK':'HKD' }
        self.currency_map={ 'US':'USD', 'HK':'HKD' }
        self.exchange_map = {'US': 'SMART', 'HK': 'SEHK'}
        # 🤖 裸 symbol（無 MARKET. 前綴）唔使 map — _resolve_contract 按順序試 FUTURE(逐交易所) → STOCK(SMART) → INDEX(逐交易所)，
        #    由 TWS reqContractDetails 判斷；解析結果 cache 咗（同一 symbol+類型只查一次）。同 ib_futures_kline.py
        self._resolved = {}
        self.future_exchanges = ('CME', 'CBOT', 'NYMEX', 'COMEX', 'ICEUS')
        self.index_exchanges  = ('CBOE', 'NASDAQ', 'NYSE', 'ARCX')
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

    # 🤖 sec_type hint 接受短名（TWS wire format）或全名
    _SEC_TYPE_ALIASES = {'FUT': 'FUTURE', 'STK': 'STOCK', 'IND': 'INDEX'}

    async def _resolve_contract(self, code, sec_type=None):
        """唔使 map：按順序試 FUTURE(逐交易所) → STOCK(SMART) → INDEX(逐交易所)，由 TWS reqContractDetails 判斷。

        sec_type 指定（FUT/STK/IND）就只試嗰種類型；回傳解析到嘅 contract
        （exchange/currency 正確；期貨已帶 TWS 填好嘅 front month）。"""
        code = code.strip().upper()
        if sec_type:
            sec_type = self._SEC_TYPE_ALIASES.get(sec_type.upper(), sec_type.upper())
        cache_key = f"{code}:{sec_type or ''}"
        if cache_key in self._resolved:
            return self._resolved[cache_key]

        candidates = []
        if sec_type in (None, 'FUTURE'):
            candidates += [Future(symbol=code, lastTradeDateOrContractMonth='', exchange=ex, currency='USD')
                           for ex in self.future_exchanges]
        if sec_type in (None, 'STOCK'):
            candidates.append(Stock(code, 'SMART', 'USD'))
        if sec_type in (None, 'INDEX'):
            candidates += [Index(code, ex, 'USD') for ex in self.index_exchanges]

        for c in candidates:
            try:
                details = await self.ib.reqContractDetailsAsync(c)
            except Exception:
                continue   # TWS 對無效 contract 會報錯（如 Error 200），當「唔係呢種」繼續試下個
            if details:
                resolved = details[0].contract
                extra = f"，月份={resolved.lastTradeDateOrContractMonth}" \
                    if resolved.secType == 'FUT' and resolved.lastTradeDateOrContractMonth else ''
                print(f"[{code}] 自動解析到 {resolved.secType} @ {resolved.exchange}{extra}")
                self._resolved[cache_key] = resolved
                return resolved
        raise ValueError(f"無法解析 {code}（試過: {sec_type or 'FUTURE/STOCK/INDEX'}）")

    async def _make_contract(self, code):
        """由 code 建立 contract：
           - 'MARKET.SYMBOL'（US.AAPL / HK.00700）→ 先試股票；唔係股票（如 US.NQ）就 fallback 自動解析
           - 裸 symbol（ES / NQ / SPX）→ 自動解析 FUTURE/STOCK/INDEX；CODE:TYPE 可強制類型（ES:FUT）"""
        code = code.strip()
        sec_type = None
        if ':' in code:   # 🤖 CODE:TYPE 強制類型，同一代號多市場時用
            code, sec_type = code.split(':', 1)
        if '.' in code:   # 舊格式 MARKET.SYMBOL → 先試股票（US/HK market map）
            market, symbol, exchange, currency = self._parse_code(code)
            key = code.upper()
            if key in self._resolved:
                return self._resolved[key]
            contract = Stock(symbol, exchange, currency)
            try:
                details = await self.ib.reqContractDetailsAsync(contract)
            except Exception:
                details = []
            if details:
                resolved = details[0].contract   # 🤖 用 TWS 解析後嘅（currency/exchange 正確）
                self._resolved[key] = resolved
                return resolved
            print(f"[{code}] {exchange} 搵唔到股票，fallback 自動解析")
            resolved = await self._resolve_contract(symbol, sec_type)   # 🤖 例如 US.NQ → NQ 期貨
            self._resolved[key] = resolved   # 🤖 用完整 code cache — 否則每次呼叫都重新 probe Stock（stream_kline 會印第二次 fallback）
            return resolved
        return await self._resolve_contract(code, sec_type)

    def _calc_duration(self, ktype, kline_num, sec_type='STK'):
        # 🤖 根據 K 線週期 + 根數計算歷史窗口（get_kline / stream_kline 共用）
        # ⚠️ 窗口係「日曆日」但 bar 只喺交易日有：週末/假期食走 ~30%，港股 session 又比美股短 —
        #    margin 故意放大，確保回傳 > kline_num 根（多取嘅由 .tail(kline_num) 切走）；唔使精準，多過就得
        # ⚠️ bar 密度按資產類型：期貨 CME Globex 一日 ~23h 都有 bar；股票/指數只有 RTH 6.5h（+盤前盤後）
        if sec_type in ('FUT', 'FUTURE'):   # 🤖 TWS 回傳嘅 contract.secType 係短名 'FUT'
            per_day = {'K_1M': 1300, 'K_5M': 260, 'K_15M': 87, 'K_60M': 23}   # ~23h × 60
        else:
            per_day = {'K_1M': 390, 'K_5M': 78, 'K_15M': 26, 'K_60M': 5}      # 港股基準，最保守（同之前一樣）
        if ktype in per_day:
            days = (kline_num // per_day[ktype]) * 2 + 5
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
        """同 get_kline 一樣回傳 (status, data, message)：
           - status=True → data 係 async generator（第一次 yield = baseline，之後每筆 push = live tick）；
             consumer 用 `async for df in data` 消費，停止 = cancel task / .aclose()（finally 會拔線）
           - status=False → data=None、message 係失敗原因（代號解析 / 歷史取數 / keepUpToDate 訂閱）"""
        # 0️⃣ 確保共享連線已建立（冪等，8 條 stream 共用同一條）
        await self._ensure_connected()
        # 1️⃣ 先用 get_kline 取歷史 K 線做初始底表（HISTORY）— 失敗直接經 status/message 回報，唔會建 generator
        status, data, message = await self.get_kline(code, ktype, kline_num=kline_num)
        if not status or data is None:
            return False, None, f"IB stream_kline: 歷史 K 線取得失敗 {message}"

        # 2️⃣ keepUpToDate 訂閱（setup 階段 — 任何失敗都經 status/message 回報，唔會留半訂閱狀態）
        try:
            contract = await self._make_contract(code)   # 🤖 get_kline 已解析 + cache；呢度只係取 secType/duration

            # 🤖 同 get_kline 用同一個動態窗口，確保歷史底表同 live 訂閱範圍一致
            bar_size, _ = self.ktype_map.get(ktype, ('1 day', None))
            duration = self._calc_duration(ktype, kline_num, contract.secType)
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
            return False, None, f"IB stream_kline: keepUpToDate 訂閱失敗 {type(e).__name__}: {e}"
        if not bars:   # 🤖 TWS timeout/error 時 ib_async 靜默回傳空 list（唔係 exception，logging 又被食）— 唔 guard 會永遠 hang 喺下面迴圈
            return False, None, "IB stream_kline: keepUpToDate 無數據（TWS timeout/error）"

        print("IB stream_kline...")

        # 🤖 累積器：以 get_kline 嘅歷史做底，之後逐條拼接新 bar
        kline_df = data.copy()

        # 🤖 快取區：記錄有幾多筆新推送等待送出
        data_buffer = []
        update_count = 0   # 🤖 累計收到幾多次 push（用於偵測「訂閱咗但完全冇數據」）
        sub_start = time.monotonic()

        # 🤖 定義回呼函式：IB 每 PUSH 一次就將最新 bar 拼入 K 線表，並標記待送
        def on_bar_update(bars_obj, has_new_bar):
            nonlocal kline_df, update_count
            latest_row = self._normalize_kline(util.df(bars_obj).tail(1), ktype)
            if latest_row.empty:
                return
            update_count += 1
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
                    # 🆕 新 bar（tk > last_tk）：接到尾端 — 漏咗呢支分支，分鐘一過表就永遠凍喺舊 bar（「不更新」）
                    kline_df = pd.concat([kline_df, latest_row], ignore_index=True)
            else:
                # 新 bar：接到尾端
                kline_df = pd.concat([kline_df, latest_row], ignore_index=True)
            data_buffer.append(True)

        # 掛載監聽事件
        bars.updateEvent += on_bar_update

        async def _stream():
            try:
                # 🤖 先 yield 一次完整歷史快照（baseline），再進入長線鎖定迴圈
                yield kline_df.copy()
                while True:
                    # 🤖 有新推送就 yield 完整更新後嘅 K 線表到最外層，連線絕對不會斷！
                    if data_buffer:
                        # 🤖 B1 fix（同 futu）：一次 drain 晒所有待送標記、每圈最多 yield 一次快照 —
                        #    高頻 push（RTH NVDA 可以 >10 ticks/s）唔會令 buffer 無界增長
                        data_buffer.clear()
                        yield kline_df.copy()

                    # 🤖 訂閱後 120s 仍零 update：TWS 對無 market data permission 嘅 contract 會靜默唔 stream（Error 420）— 提示一次然後結束，唔好永遠 hang
                    if (update_count == 0 and time.monotonic() - sub_start > 120):
                        print(f"⚠️ IB stream_kline [{code}]: 訂閱後 120s 仍無任何 live update — "
                              f"大概率係 account 無呢個 contract 嘅 real-time market data（TWS Error 420），"
                              f"check IB Account Management → Market Data")
                        break   # 🤖 冇數據可 stream，繼續等只會永遠 hang — 交還控制權俾上層

                    # 每 0.1 秒釋放一次控制權，維持背景監聽，不佔用 CPU 資源
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                print("🛑 [IB PUSH] 接收到終止指令，正在關閉監聽事件...")
                raise  # 🤖 保留取消語義：上層 task.cancel() 先可以正確結束
            finally:
                # 安全拔線：移除本地監聽 + 通知 TWS 停止推送（否則 keepUpToDate 會喺 server 端繼續 stream，8 條 stream 共用連線會漏）
                bars.updateEvent -= on_bar_update
                try:
                    self.ib.cancelHistoricalData(bars)   # 🤖 取消 TWS 端嘅 keepUpToDate 訂閱
                except Exception:
                    pass   # 連線已斷就唔使理

        return True, _stream(), None

    async def get_kline(self,code,ktype,kline_num=100):
        await self._ensure_connected()  # 🤖 確保共享連線已建立（冪等）
        print('IB get_kline')
        status=False
        data, message=None,None

        try:
            contract = await self._make_contract(code)   # 🤖 MARKET.SYMBOL → 股票；裸 symbol → 自動解析 FUTURE/STOCK/INDEX
        except ValueError as e:
            return False, None, str(e)

        # 🤖 動態窗口（同 stream_kline 共用同一個 _calc_duration）；bar 密度按資產類型
        bar_size, _ = self.ktype_map.get(ktype, ('1 day', None))
        duration = self._calc_duration(ktype, kline_num, contract.secType)

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
        if not bars:   # 🤖 TWS timeout/error → ib_async 靜默回傳空 list（唔係 exception）— 同 ib_futures_kline.py 一樣要 guard
            return False, None, 'IB 無回傳數據（timeout 或 error，檢查代號/月份）'

        status=True
        data = self._normalize_kline(data, ktype)
        return status, data, message


    async def get_ticker(self):
        print('Ib get_ticker (來自獨立的 IB 引擎)')
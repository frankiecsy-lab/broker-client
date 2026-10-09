import asyncio
import logging
import time
import pandas as pd
from ib_async import *

# 🤖 自動解析會 probe 一堆無效 contract，ib_async 每次都用 logging 印 "Error 200 No security definition" — 太嘈；本檔自己會印 ❌/✅
logging.getLogger('ib_async').setLevel(logging.CRITICAL)

logger = logging.getLogger(__name__)   # 🤖 P5：print → logging（app-level config 喺 modules/__init__.py）

from .broker_base import BrokerBase   # 🤖 P1：統一契約（NAME / get_kline / stream_kline 形狀）
from .kline_schema import reorder_kline, validate_kline   # 🤖 P3：K 線 schema 一來源 + 驗證
from .trade_base import (ACC_COLS, ACCINFO_KEYS, ORDER_COLS, POS_COLS,  # 🤖 #34b：交易能力契約
                         TradeBase, clamp_side)

# 🤖 P6: IB error code → 人話 hint 映射表。由本 account 實測建立（findings 見 CHANGELOG.md P6 節；probe 腳本已清理），唔係憑記憶寫：
#    162/200/10314/2174 實測過；366/420 係標準 TWS code（呢個 account 觸發唔到 — 歷史數據權限 OK、stream 靜默無數據唔彈錯）。
#    表外嘅 code 由 _ib_error_text fallback 返「IB Error {code}: {TWS 原文}」— 永遠如實，唔會編造原因。
_IB_ERROR_HINTS = {
    162:   '該時段無歷史數據（HMDS returned no data）',
    200:   '標的唔存在，或本 account 無呢個 market data permission（TWS 對冇權限嘅 security 都回 200）',
    366:   '歷史數據請求 timeout（TWS busy 或代號/月份有問題）',
    420:   '無 real-time market data permission — check IB Account Management → Market Data 訂閱',
    10314: 'endDateTime format 錯誤（應為 yyyymmdd hh:mm:ss xx/xxxx）',
}


def _ib_error_text(code, msg):
    """🤖 P6: code + TWS 原文 → 人話 message；表外 code fallback 返 TWS 原文 — 永遠如實，唔會編造原因。"""
    hint = _IB_ERROR_HINTS.get(int(code))
    return f"{hint}（IB Error {code}）" if hint else f"IB Error {code}: {msg}"


class IBClient(BrokerBase, TradeBase):
    NAME = "ib"   # 🤖 P1：registry key（同 config.json 嘅 section 名）
    TRADE = True  # 🤖 #34b：有交易能力（見下方「💳 交易能力」段 + modules/trade_base.py）

    def __init__(self,config=None):
        super().__init__(config)   # 🤖 P1：BrokerBase.__init__ 存 self.config（允許唔傳 → {}）
        self.host = self.config.get("host", "127.0.0.1")
        self.port = config.get("port", 4001)
        self.symbol_map={ 'US':'USD', 'HK':'HKD' }
        self.currency_map={ 'US':'USD', 'HK':'HKD' }
        self.exchange_map = {'US': 'SMART', 'HK': 'SEHK'}
        # 🤖 裸 symbol（無 MARKET. 前綴）唔使 map — _resolve_contract 按順序試 FUTURE(逐交易所) → STOCK(SMART) → INDEX(逐交易所)，
        #    由 TWS reqContractDetails 判斷；解析結果 cache 咗（同一 symbol+類型只查一次）。同 ib_futures_kline.py
        self._resolved = {}
        # 🤖 P7 probe 實測（test/probe_ib_hsi.py）：(exchange, currency) 對 — SEHK 係 TWS 接受嘅 HK destination（HKEX/CFES/PKE 彈 'Invalid destination'）；
        #    本 account 無 HK derivatives permission → HSI@SEHK 回 Error 200 No security definition（honest fail），有 HK access 嘅 account 先解到
        self.future_exchanges = (('CME', 'USD'), ('CBOT', 'USD'), ('NYMEX', 'USD'), ('COMEX', 'USD'), ('ICEUS', 'USD'), ('SEHK', 'HKD'))
        # 🤖 P7：加 SEHK — HK 指數（如 HSI）喺 TWS 嘅 destination；本 account 無權限時一樣 honest fail
        self.index_exchanges  = ('CBOE', 'NASDAQ', 'NYSE', 'ARCX', 'SEHK')
        # 🤖 P7 L1: config.json ib.symbol_aliases — IB-only symbol / 用戶 shorthand → broker-native form（e.g. "MY.SPX": "SPX:IND"）；
        #    value 用 _make_contract 已支援嘅 format（裸 symbol / CODE:TYPE / MARKET.SYMBOL），唔好喺度 hardcode 合約月份
        self._symbol_aliases = {str(k).strip().upper(): str(v) for k, v in (self.config.get("symbol_aliases") or {}).items()}
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
        # 🤖 P6: 全局 errorEvent sink — [(monotonic_ts, reqId, code, msg)]；ib_async 會把 TWS error 食咗入 logging（上面已壓 CRITICAL），
        #    冇呢個 sink，空 list 回傳就永遠唔知真正原因。per-request 歸屬靠 bars.reqId（實測：await return 時 event 已 emit）
        self._error_log = []

    async def _ensure_connected(self):
        """確保共享連線已建立；可並發呼叫（冪等），唔會重複 connect。"""
        async with self._connect_lock:
            if self._connected and self.ib is not None:
                return
            ib = IB()
            await ib.connectAsync(self.host, self.port, clientId=99)
            # 🤖 P6: 掛全局 errorEvent sink — 實測 args=(reqId, code, message, contract)，見 test/probe_ib_errors.py
            def _on_ib_error(*args):
                self._error_log.append((time.monotonic(), args[0], args[1], args[2]))
                if len(self._error_log) > 500:
                    del self._error_log[:250]   # 🤖 共享連線長命 — 防無界增長
            ib.errorEvent += _on_ib_error
            self.ib = ib
            self._connected = True

    async def connect(self):
        """🤖 P1：BrokerBase 生命週期 hook — 建立共享持久連線（冪等）。"""
        await self._ensure_connected()

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
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # 🤖 故意唔斷線：連線係共享嘅，由 BrokerClient.__aexit__ 統一清理
        return False

    def _request_error(self, req_id):
        """🤖 P6: 搵屬於呢個 request（按 bars.reqId 歸屬）嘅 fatal error event — 回傳最後一條 (ts, reqId, code, msg) 或 None。
           Warning 唔會令 request 失敗：ib_async 將 2100-2200 分類為 warning（實測 2174 timezone deprecation），跳過。"""
        if req_id is None:
            return None
        fatal = [e for e in self._error_log
                 if e[1] == req_id and not (2100 <= int(e[2]) < 2200)]
        return fatal[-1] if fatal else None

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
        return reorder_kline(data)   # 🤖 P3：欄順序由 kline_schema 統一（唔再各自維護 standard_order）

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

    def resolve_symbol(self, code):
        """🤖 P7 L0+L1+L2: canonical Futu code → IB-native form（純函數，唔打網絡）。
           - L0 normalize（strip + upper）
           - L1 config alias（ib.symbol_aliases — IB-only symbol / 用戶 shorthand，e.g. "MY.SPX": "SPX:IND"）
           - L2 rule: 'X.YYmain'（Futu front-month futures convention）→ 'YY:FUT'（force FUTURE，_make_contract 解 dynamic front month）
             注意：只喺剩餘部分非空先 strip — US.MAIN（真股票 ticker）唔好誤當 main contract"""
        code = str(code).strip().upper()
        if code in self._symbol_aliases:      # L1
            return self._symbol_aliases[code]
        symbol = code.split('.', 1)[1] if '.' in code else code
        if symbol.endswith('MAIN') and len(symbol) > len('MAIN'):   # L2
            return f"{symbol[:-len('MAIN')]}:FUT"
        return code

    # 🤖 sec_type hint 接受短名（TWS wire format）或全名
    _SEC_TYPE_ALIASES = {'FUT': 'FUTURE', 'STK': 'STOCK', 'IND': 'INDEX'}

    async def _resolve_contract(self, code, sec_type=None, index_first=False):
        """唔使 map：按順序試 FUTURE(逐交易所) → STOCK(SMART) → INDEX(逐交易所)，由 TWS reqContractDetails 判斷。

        sec_type 指定（FUT/STK/IND）就只試嗰種類型；回傳解析到嘅 contract
        （exchange/currency 正確；期貨已帶 TWS 填好嘅 front month）。
        🤖 P7: index_first=True → INDEX 先於 FUTURE — canonical MARKET.SYMBOL（無 main）永遠唔係期貨語義
        （HK.HSI=指數，唔係 HSI 期貨）；US.NQ leniency 保留（Index('NQ') 全部 fail 後 FUTURE 照解到）。"""
        code = code.strip().upper()
        if sec_type:
            sec_type = self._SEC_TYPE_ALIASES.get(sec_type.upper(), sec_type.upper())
        cache_key = f"{code}:{sec_type or ''}{'-idx' if (index_first and not sec_type) else ''}"
        if cache_key in self._resolved:
            return self._resolved[cache_key]

        type_order = [sec_type] if sec_type else (('INDEX', 'FUTURE') if index_first else ('FUTURE', 'STOCK', 'INDEX'))
        candidates = []
        for t in type_order:
            if t == 'FUTURE':   # 🤖 P7 probe 實測（test/probe_ib_hsi.py）：(exchange, currency) 對 — SEHK/HKD 係 TWS 接受嘅 HK destination
                candidates += [Future(symbol=code, lastTradeDateOrContractMonth='', exchange=ex, currency=ccy)
                               for ex, ccy in self.future_exchanges]
            elif t == 'STOCK':
                candidates.append(Stock(code, 'SMART', 'USD'))
            else:   # INDEX
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
                logger.info("[%s] 自動解析到 %s @ %s%s", code, resolved.secType, resolved.exchange, extra)
                self._resolved[cache_key] = resolved
                return resolved
        # 🤖 P6 error honesty：全部 candidate 都 fail — 由 _error_log 搵最近嘅 fatal error，將真正原因帶入 message
        #    （如 Error 200 = 無 security definition / 無 permission），唔好只講「無法解析」令人以為係打錯 code
        last_err = None
        now = time.monotonic()
        for ts, _req_id, err_code, err_msg in reversed(self._error_log):
            if now - ts > 10:      # 🤖 只要最近 10s（呢次 resolve 期間產生）— 共享連線有其他 request 嘅舊 error
                break
            if not (2100 <= int(err_code) < 2200):   # warning band 唔算 fatal
                last_err = _ib_error_text(err_code, err_msg)
                break
        hint = f"；最後錯誤: {last_err}" if last_err else ""
        raise ValueError(f"無法解析 {code}（試過: {'/'.join(type_order)}{hint}）")

    async def _make_contract(self, code):
        """由 code 建立 contract：
           - 'MARKET.SYMBOL'（US.AAPL / HK.00700）→ 先試股票；唔係股票（如 US.NQ）就 fallback 自動解析
           - 裸 symbol（ES / NQ / SPX）→ 自動解析 FUTURE/STOCK/INDEX；CODE:TYPE 可強制類型（ES:FUT）
           🤖 P7：先經 resolve_symbol（L0 normalize / L1 config alias / L2 'YYmain' → 'YY:FUT'）"""
        code = self.resolve_symbol(code)
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
            logger.info("[%s] %s 搵唔到股票，fallback 自動解析", code, exchange)
            resolved = await self._resolve_contract(symbol, sec_type, index_first=True)   # 🤖 P7: INDEX 先於 FUTURE — canonical code（無 main）唔係期貨語義；US.NQ leniency 保留
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
           - status=False → data=None、message 係失敗原因（代號解析 / 歷史取數 / keepUpToDate 訂閱）
           🤖 P6 fail-fast：setup 後有 3s explicit-error grace window — TWS 彈到 fatal error（按 reqId 歸屬，
              warning 除外）即刻返 False；silent no-data account 等滿 3s 先入 stream，120s watchdog 仍係 backstop"""
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
        if not bars:   # 🤖 P6: TWS timeout/error → ib_async 靜默回傳空 list（唔係 exception）— 按 reqId 歸屬真正 error，唔再 generic message
            err = self._request_error(getattr(bars, 'reqId', None))
            why = _ib_error_text(err[2], err[3]) if err else '無回傳數據且無對應 TWS error event'
            return False, None, f"IB stream_kline: keepUpToDate 無數據 — {why}"

        logger.info("IB stream_kline...")

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

        # 🤖 P6: explicit-error grace window（3s）— TWS permission/timeout error 喺 ~1s 內到（實測）；
        #    呢個 account 係 silent no-data（無 RTUS 唔彈錯）所以會等滿 3s，第一筆 live bar 到就提前離開。
        #    8 條 stream 並發 setup → 總成本 ~3s，唔係 8×3s。
        deadline = time.monotonic() + 3.0
        err = None
        while time.monotonic() < deadline:
            err = self._request_error(getattr(bars, 'reqId', None))
            if err is not None or update_count > 0:
                break
            await asyncio.sleep(0.1)
        if err is not None:
            bars.updateEvent -= on_bar_update
            try:
                self.ib.cancelHistoricalData(bars)   # 🤖 拔走 TWS 端訂閱，唔留半訂閱狀態
            except Exception:
                pass
            return False, None, f"IB stream_kline: {_ib_error_text(err[2], err[3])}"

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
                        logger.warning("⚠️ IB stream_kline [%s]: 訂閱後 120s 仍無任何 live update — "
                                       "大概率係 account 無呢個 contract 嘅 real-time market data（TWS Error 420），"
                                       "check IB Account Management → Market Data", code)
                        break   # 🤖 冇數據可 stream，繼續等只會永遠 hang — 交還控制權俾上層

                    # 每 0.1 秒釋放一次控制權，維持背景監聽，不佔用 CPU 資源
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                logger.info("🛑 [IB PUSH] 接收到終止指令，正在關閉監聽事件...")
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
        logger.info('IB get_kline')
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
        except Exception as e:
            return False, None, str(e)

        if not bars:   # 🤖 P6: 空 list 唔再 generic message — 按 reqId 歸屬真正 TWS error（實測見 test/probe_ib_errors*.py）
            err = self._request_error(getattr(bars, 'reqId', None))
            why = _ib_error_text(err[2], err[3]) if err else '無回傳數據且無對應 TWS error event'
            return False, None, f"IB get_kline: {why}"

        try:
            data = util.df(bars).tail(kline_num).reset_index(drop=True)
        except Exception as e:
            return False, None, str(e)

        status=True
        data = self._normalize_kline(data, ktype)
        # 🤖 P3：成功前驗 schema — 唔符合契約就當失敗回報（stream_kline 嘅 baseline 用呢個 df，已一併覆蓋）
        ok, why = validate_kline(data)
        if not ok:
            return False, None, f"IB get_kline: K 線 schema 驗證失敗 {why}"
        return status, data, message


    async def get_ticker(self):
        logger.info('Ib get_ticker (來自獨立的 IB 引擎)')

    # ══════════ 💳 交易能力（#34b — 契約見 modules/trade_base.py）══════════
    # 🤖 全部复用行情嗰條**共享持久連線**（clientId=99）— IB 禁止同一 clientId 開多條，所以交易唔會另開連線。
    #    即係：量化頁自動落單同 K 線 stream 行同一條 TWS 通道，ib_async 內部自己排程。
    # ⚠️ IB **冇**「模擬/實盤」開關：係實盤定 paper 完全由你連邊個 TWS/Gateway 決定。帳戶 env 只按 IB
    #    官方帳戶編號慣例推（DU 前綴 = paper）— 呢個係**編號慣例唔係 API 欄位**，如實標明。
    # ⚠️ `placeOrder` 回傳 = Trade（status PendingSubmit/Submitted），**唔係成交**。成交要由 open_orders 先睇到。

    # futu 嘅訂單類型名 → IB 名。只有真正等價先映射；其餘原樣交俾 TWS（唔支援就由 TWS 如實拒單）
    _IB_ORDER_TYPES = {'NORMAL': 'LMT', 'MARKET': 'MKT'}
    # IB accountValues tag → 契約 ACCINFO_KEYS（冇對應嘅 key 留空，唔編數）
    _ACCINFO_TAGS = {'total_assets': 'NetLiquidation', 'cash': 'CashBalance',
                     'market_val': 'GrossPositionValue', 'power': 'BuyingPower',
                     'available_funds': 'AvailableFunds', 'avl_withdrawal_cash': 'MaxWithdrawalAmount'}

    async def _trd_conn(self):
        """確保共享連線喺 → (ib, None)；唔到 → (None, 原因原文)。"""
        try:
            await self._ensure_connected()
        except Exception as e:
            return None, f'IB/TWS 連線開唔到: {e}'
        if self.ib is None or not self.ib.isConnected():
            return None, 'IB/TWS 未連線（先 check TWS 或 IB Gateway 開咗無、port 啱唔啱）'
        return self.ib, None

    @staticmethod
    def _num(v):
        """IB 數值欄一律 string → float；唔係數就原樣回（如實，唔扮 0）。"""
        try:
            f = float(v)
        except (TypeError, ValueError):
            return v
        return int(f) if f == int(f) else f

    async def trade_accounts(self):
        ib, err = await self._trd_conn()
        if err:
            return False, None, err
        accts = list(getattr(ib, 'managedAccounts', None) or [])
        if not accts:
            return False, None, 'TWS 冇回報 managed account（連線權限不足？）'
        rows = [{c: {'acc_id': a,
                     'trd_env': 'SIMULATE' if str(a).upper().startswith('DU') else 'REAL',
                     'acc_status': 'ACTIVE'}.get(c, '') for c in ACC_COLS}
                for a in accts]   # 未提供嘅欄（acc_type/trdmarket_auth）留空 — 由契約決定形狀，唔扮齊
        return True, rows, ''

    async def place_order(self, *, code, side, qty, price=None, account=None,
                          env=None, order_type=None, tif=None):
        sd = clamp_side(side)
        if sd is None:
            return False, None, f'買賣方向唔識: {side!r}（只收 BUY/SELL）'
        try:
            q = int(qty)
            p = float(price) if price not in (None, '') else None
        except (TypeError, ValueError):
            return False, None, f'數量/價格唔係數字: qty={qty!r} price={price!r}'
        if q <= 0:
            return False, None, f'數量必須大於 0: {q}'
        otype = self._IB_ORDER_TYPES.get(str(order_type or 'NORMAL').upper(),
                                        str(order_type or 'LMT').upper())
        if otype == 'LMT' and p is None:
            return False, None, f'{otype} 訂單必須有價格'
        ib, err = await self._trd_conn()
        if err:
            return False, None, err
        try:
            contract = await self._make_contract(code)
        except Exception as e:
            return False, None, str(e)
        o = Order(action=sd, totalQuantity=q, orderType=otype)
        if p is not None and otype != 'MKT':
            o.lmtPrice = p
        if tif:
            o.tif = str(tif).upper()
        if account:
            o.account = str(account)
        try:
            trade = ib.placeOrder(contract, o)
        except Exception as e:
            return False, None, str(e)
        status = getattr(trade.orderStatus, 'status', '') or getattr(trade.order, 'status', '')
        return True, {'order_id': str(o.orderId), 'code': str(code).strip(), 'side': sd,
                      'qty': q, 'price': p if p is not None else '', 'status': str(status)}, ''

    async def cancel_order(self, *, order_id, account=None, env=None):
        ib, err = await self._trd_conn()
        if err:
            return False, None, err
        try:
            oid = int(order_id)
        except (TypeError, ValueError):
            return False, None, f'訂單號唔係整數: {order_id!r}'
        # IB 嘅 cancelOrder 食 Order object，唔食 order_id → 由本地 openOrders cache 搵返（呢個係 ib_async 正規用法）
        target = next((t.order for t in ib.openOrders() if t.order.orderId == oid), None)
        if target is None:
            return False, None, f'搵唔到訂單 {oid}（已成交/已撤/唔喺呢條連線？）'
        try:
            ib.cancelOrder(target)
        except Exception as e:
            return False, None, str(e)
        return True, f'已送出撤單請求 order_id={oid}', ''

    async def open_orders(self, *, account=None, env=None):
        ib, err = await self._trd_conn()
        if err:
            return False, None, err
        try:
            trades = await ib.reqOpenOrdersAsync()
        except Exception as e:
            return False, None, str(e)
        rows = []
        for t in trades:
            c, o, s = t.contract, t.order, t.orderStatus
            if account and str(getattr(o, 'account', '') or c.account or '') not in ('', str(account)):
                continue   # 多帳戶時如實只留揀咗嗰個
            raw = {'order_id': str(o.orderId), 'code': c.symbol,
                   'trd_side': o.action, 'order_type': o.orderType,
                   'qty': self._num(o.totalQuantity), 'dealt_qty': self._num(s.filled),
                   'price': self._num(o.lmtPrice or o.auxPrice or ''),
                   'dealt_avg_price': self._num(s.avgFillPrice),
                   'order_status': s.status}
            # create_time/stock_name：IB openOrders 唔帶 → 契約照留欄、值留空（如實，唔編數）
            rows.append({c2: raw.get(c2, '') for c2 in ORDER_COLS})
        return True, rows, ''

    async def positions(self, *, account=None, env=None):
        ib, err = await self._trd_conn()
        if err:
            return False, None, err
        try:
            poss = await ib.reqPositionsAsync()
        except Exception as e:
            return False, None, str(e)
        rows = []
        for p in poss:
            c = p.contract
            if account and str(p.account or '') != str(account):
                continue
            # ⚠️ IB reqPositions 得 position/avgCost — market_val/pl_val/pl_ratio/can_sell_qty 呢度真係冇，
            #    契約照留欄、值留空（如實，唔編數）
            raw = {'code': c.symbol, 'position_market': c.exchange,
                   'qty': self._num(p.position), 'cost_price': self._num(p.avgCost),
                   'currency': c.currency}
            rows.append({c2: raw.get(c2, '') for c2 in POS_COLS})
        return True, rows, ''

    async def account_info(self, *, account=None, env=None):
        ib, err = await self._trd_conn()
        if err:
            return False, None, err
        acct = str(account or '')
        try:
            await ib.reqAccountUpdatesAsync(acct)   # ib_async 收完第一批會自動 unsubscribe
            vals = ib.accountValues(acct)
        except Exception as e:
            return False, None, str(e)
        by_tag = {}
        for v in vals:
            if v.tag and v.value not in (None, ''):
                by_tag[v.tag] = v.value   # 最後一次為準（IB 會推多行更新）
        info = {k: self._num(by_tag.get(tag, '')) for k, tag in self._ACCINFO_TAGS.items()}
        missing = [k for k, v in info.items() if v == '']   # 如實：少欄要睇到，唔扮齊
        if missing:
            logger.warning('IB 交易: accountValues 缺少欄位 %s', missing)
        return True, info, ''

    async def unlock_status(self, *, account=None, env=None):
        """IB 冇「解鎖交易」機制 — 連咗 TWS/Gateway 就可以落單。如實回 unlocked=True（唔阻全自動）。"""
        _, err = await self._trd_conn()
        if err:
            return False, None, err
        return True, {'unlocked': True, 'known': True, 'hint': 'IB 冇解鎖機制 — 直接經 TWS/Gateway 落單'}, ''
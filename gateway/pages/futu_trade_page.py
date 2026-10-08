"""One Gate Page 4 — FUTU 交易管理（下單 / 今日訂單 / 持倉同帳戶資金）。

- **佈局**：兩欄 — 左 = 今日訂單 + 持倉/帳戶資金；右 = 連線帳戶 + 下單表單。
- **連線 + 帳戶**：`OpenSecTradeContext(host, port)`（host/port 預填 config.json futu section，
  可改）→ `get_acc_list()`。🤖 **自動連線**（用戶要求：冇連線/斷開按鍵）：構造後 300ms 自動 connect；
  失敗或斷開 → 15s 定時自動重試/重連（自愈），成功即停；狀態只喺 conn_status_lbl 如實反映。
  **模擬/實盤切換按鍵**（QButtonGroup exclusive，預設模擬）+
  市場過濾 combo — 全部係 **client-side filter**：帳戶表只顯示 **ACTIVE** 狀態、且符合
  當前環境 + 市場嘅帳戶；切換時所有資料（訂單/持倉/資金）跟住重新 query，**唔混模擬同實盤**。
- **解鎖狀態（實盤）**：只顯示解鎖狀態 label — **只有 REAL 環境先顯示**。新版 OpenD **禁止 SDK
  `unlock_trade` 解鎖**（官方 opend-skills：必須喺 OpenD GUI 手動點擊「解鎖交易」），而家亦冇查詢
  API → 狀態**自動探測**：`unlock_probe` op（modify_order 撤唔存在 order_id — 真 OpenD 實測未解鎖
  時 unlock 錯誤優先於訂單不存在，無副作用）喺選帳戶即時 + 10s 定時跑；落單/撤單成敗都更新。
  成功 → 已解鎖；unlock 類錯誤 → 未解鎖 + 提示去 OpenD GUI；其他 → 未知（如實標明）。
- **代碼模糊輸入**：code_edit = `gateway/symbol_input` 模糊輸入（completer 全域共用）— 輸入 debounce 300ms → worker `stock_search` 直接用
  **本地 symbol index**（`modules/symbol_search.get_directory()`，同 P8 共用 — 股票/ETF/指數/期貨主連
  HK.HSImain，中英 name 欄、繁簡 t2s 正規化 match）→ dropdown 候選 =「CODE  名稱（跟語言）」+ 名稱 hint
  （中文跟 GUI 語言：zh_hk 繁 / zh_cn 簡 / en name_en fallback 原生）；揀咗淨返 CODE 入欄。純本地，唔打網絡。
  code 大細階經 index canonical 還原（期貨主連 HK.HSImain 細階 main — 直接 upper 變未知股票）。
- **下單**：code / order type / price / qty / TIF（三語顯示，提交用 enum）+ **買入/賣出兩鍵**
  （方向由按鍵決定）→ `place_order`；REAL 帳戶先彈確認框；未解鎖 → 如實回報 + 狀態欄提示。
  code 落單前驗證 `MARKET.CODE` 格式（中文/未完整代碼唔會送入 place_order）。
  完整 code → `subscribe`：get_market_snapshot 預填 price=最新價 / qty=每手，再訂閱 **K_1M push**
  **全流市價**（KLINE_STREAM 方式 — 同 futu_client.stream_kline 一樣 subscribe_push=True，
  最新 bar close = 現價自動更新，取代 10s poll；換 code 自動改訂閱。實測呢個 OpenD QUOTE
  subtype push 永遠唔到、K 線 push 正常，所以市價行 K 線 push）；用戶手動改過（textEdited）就唔覆蓋。
- **今日訂單**：`order_list_query` 表格 + 撤選定單（`modify_order(CANCEL)`）/ 全數撤
  （`cancel_all_order`）；REAL 帳戶一樣先確認。
- **持倉同資金**：`position_list_query` 表格 + `accinfo_query` 關鍵數字。
- **線程模型**：所有 SDK 調用行單一專用 QThread（`_FutuTradeWorker` owns ctx，queue 串行化 —
  gRPC 調用係 blocking 唔好 block UI；ctx 非 thread-safe → 全部 op 同一條 thread）。
  futu SDK lazy import（import 要幾秒，唔拖慢 UI 啟動）— 同 connection_page probe 同一 pattern。

Theme 傳播：頁面級 QSS template（palette 由 gateway.theme.THEMES 注入，經 listener registry
跟隨外殼切換）— 同 connection_page 同一 pattern。

單獨運行：`python gateway/pages/futu_trade_page.py`（standalone window，帶語言/theme 控制）。
"""
import json
import os
import queue
import re
import sys
from pathlib import Path
from string import Template

# ── standalone bootstrap：直接跑呢個檔時將 project root 放落 sys.path（package mode 下 no-op）──
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt, QThread, QTimer, Signal  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QButtonGroup, QComboBox, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

import gateway.theme as theme_mod  # noqa: E402 — module 引用（唔係 from-import，避免 stale value binding）
from gateway.i18n import DEFAULT_LANG, t  # noqa: E402
from gateway.symbol_input import FuzzyCompleter, apply_item, display_name  # noqa: E402 — 全域模糊輸入

# config.json 路徑 — pathlib 跨平台（AGENTS.md：禁 hardcode 斜線）
CONFIG_PATH = Path(__file__).resolve().parents[2] / 'modules' / 'config.json'

# 落單 code 格式 — 必須完整 MARKET.CODE（中文/模糊輸入唔可以送入 place_order）
# 兼容股票（HK.00700 / US.NVDA）同期貨主連（HK.HSImain）
_CODE_RE = re.compile(r'^(?:HK|US|SH|SZ)\.[A-Z0-9][A-Z0-9.]*$', re.IGNORECASE)

# ── 表格顯示欄（對住真 OpenD 回傳核實過；card_num/uni_card_num 等敏感欄位刻意唔入表）──
ACC_COLS = ('acc_id', 'trd_env', 'acc_type', 'trdmarket_auth', 'acc_status')
ORDER_COLS = ('order_id', 'code', 'stock_name', 'trd_side', 'order_type', 'qty',
              'dealt_qty', 'price', 'dealt_avg_price', 'order_status', 'create_time')
POS_COLS = ('code', 'stock_name', 'position_market', 'qty', 'can_sell_qty', 'cost_price',
            'market_val', 'pl_val', 'pl_ratio', 'currency')
ACCINFO_KEYS = ('total_assets', 'cash', 'market_val', 'power', 'available_funds',
                'avl_withdrawal_cash')

# 數值欄 → 表格右對齊（方便比較）；欄名顯示經 i18n col_* keys
NUMERIC_COLS = frozenset({'qty', 'dealt_qty', 'price', 'dealt_avg_price', 'can_sell_qty',
                          'cost_price', 'market_val', 'pl_val', 'pl_ratio'})

# 下單表單選項（技術識別碼，三語同字 — 唔入 i18n）
MARKET_FILTERS = ('All', 'HK', 'US', 'HKFUND', 'USFUND')
ORDER_TYPES = ('NORMAL', 'MARKET', 'AUCTION_LIMIT', 'LIMIT_IF_TOUCHED')
TIF_OPTIONS = ('DAY', 'GTC', 'IOC')
# 🤖 用戶要求：OpenD 默認自動連線（冇連線/斷開按鈕）— 構造後短延遲自動連線，失敗定時重試
AUTO_CONNECT_DELAY_MS = 300
RETRY_MS = 15_000


def _fmt(v):
    """cell 值 → 顯示字串（None/NaN → 空；整數值 float 唔帶 .0）。"""
    if v is None:
        return ''
    if isinstance(v, float) and v != v:   # NaN
        return ''
    if isinstance(v, float):
        iv = int(v)
        if iv == v and abs(v) < 1e15:
            return str(iv)
        return f'{v:.6f}'.rstrip('0').rstrip('.')
    return str(v)


def _classify_error(e):
    """連線 exception → reason_code（refused/timeout/other）— page 端再映射 i18n，如實回報。"""
    import socket
    msg = str(e) or e.__class__.__name__
    low = msg.lower()
    if isinstance(e, (ConnectionRefusedError, ConnectionResetError)) or 'refuse' in low:
        return 'refused'
    if isinstance(e, (TimeoutError, socket.timeout)) or 'timeout' in low or 'timed out' in low:
        return 'timeout'
    return 'other'


class _FutuTradeWorker(QThread):
    """owns 一條 OpenSecTradeContext — 所有 SDK 調用經 queue 串行化喺呢條 thread。

    result dict 契約：`{'ok': True, ...payload}` / `{'ok': False, 'error': str[, 'reason_code']}`。
    `ctx_factory` 係測試注入位（E2E fake context）；None → lazy import futu SDK。
    """

    sig_result = Signal(str, object)   # (op_name, result_dict)

    def __init__(self, parent=None, ctx_factory=None, qctx_factory=None):
        super().__init__(parent)
        self._ctx = None
        self._qctx = None          # OpenQuoteContext（全流市價 push 用；lazy 建 — 獨立 gRPC 連線）
        self._sub_code = None      # 當前訂閱嘅 code（換 code → unsubscribe 舊）
        self._price_handler = None # QuoteHandlerBase 執行個體（lazy 掛 qctx）
        self._search_dir = None    # E2E inject fake SymbolDirectory；None → modules.symbol_search.get_directory()
        self._last_hostport = None # connect 時記低 — quote lazy 建 qctx 用
        self._q = queue.Queue()
        self._ctx_factory = ctx_factory      # E2E inject fake；None → real OpenSecTradeContext
        self._qctx_factory = qctx_factory    # E2E inject fake quote ctx；None → real OpenQuoteContext

    @property
    def connected(self):
        return self._ctx is not None

    def submit(self, op, **kw):
        self._q.put((op, kw))

    def stop_and_wait(self, timeout_ms=5000):
        """sentinel + 等 thread 退出（idempotent）；ctx 喺 run() finally 收尾。"""
        if not self.isRunning():
            return
        self._q.put(None)
        self.wait(timeout_ms)

    def run(self):
        try:
            while True:
                item = self._q.get()
                if item is None:   # sentinel → 收工
                    break
                op, kw = item
                result = self._exec(op, kw)
                self.sig_result.emit(op, result)
        finally:
            self._close_ctx()

    def _close_ctx(self):
        for attr in ('_ctx', '_qctx'):   # trade ctx + quote ctx 一齊收（重連/斷線；訂閱隨 qctx 一齊死）
            c = getattr(self, attr)
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        self._sub_code = None
        self._price_handler = None

    def _op_warmup(self, kw):
        """page 建立時預熱：import symbol_search（佢 top-level import futu — 要幾秒）+ load index。
        唔預熱嘅話第一次按鍵搜尋先付呢啲成本 → 用戶實測「冇反應」（第一次 search 等 10s+）。"""
        import modules.symbol_search as ss
        d = self._search_dir or ss.get_directory()
        return {'ok': True, 'entries': len(d.entries)}

    # ── ops（全部行喺呢條 thread）──────────────────────────────
    def _exec(self, op, kw):
        fn = getattr(self, f'_op_{op}', None)
        if fn is None:
            return {'ok': False, 'error': f'unknown op {op}'}
        try:
            return fn(kw)
        except Exception as e:   # 連線斷咗 / SDK 內部錯 → 如實回報，唔 fake success
            return {'ok': False, 'reason_code': _classify_error(e),
                    'error': str(e) or e.__class__.__name__}

    def _need_ctx(self):
        if self._ctx is None:
            return {'ok': False, 'error': 'not connected'}
        return None

    def _make_ctx(self, host, port):
        if self._ctx_factory is not None:
            return self._ctx_factory(host=host, port=int(port))
        from futu import OpenSecTradeContext   # lazy — import 要幾秒
        return OpenSecTradeContext(host=host, port=int(port))

    def _op_connect(self, kw):
        import time
        from futu import RET_OK
        self._close_ctx()   # 重連：先收舊 ctx
        t0 = time.monotonic()
        try:
            ctx = self._make_ctx(kw['host'], kw['port'])
        except Exception as e:
            return {'ok': False, 'reason_code': _classify_error(e),
                    'error': str(e) or e.__class__.__name__}
        ms = (time.monotonic() - t0) * 1000.0
        ret, accs = ctx.get_acc_list()
        if ret != RET_OK:
            self._close_ctx()
            return {'ok': False, 'error': str(accs)}
        self._ctx = ctx
        self._last_hostport = (kw['host'], int(kw['port']))   # stock_search lazy 建 qctx 用
        rows = [{c: r[c] for c in ACC_COLS if c in r} for r in accs.to_dict(orient='records')]
        return {'ok': True, 'connect_ms': ms, 'accounts': rows}

    def _op_disconnect(self, kw):
        self._close_ctx()
        return {'ok': True}

    @staticmethod
    def _is_unlock_error(msg):
        """SDK 錯誤字串屬「交易未解鎖」類？— 新版 OpenD 只容許 GUI 解鎖，呢個係唯一解鎖狀態信號。"""
        s = str(msg).lower()
        return '解锁' in s or '解鎖' in s or 'unlock' in s

    def _op_stock_search(self, kw):
        """代碼模糊搜尋 — 本地 symbol index（modules/symbol_search，同 P8 共用 singleton）：
        股票/ETF/指數/期貨主連（HK.HSImain）+ name_zh/name_en 兩欄，繁簡 t2s 正規化 match。
        純本地唔打網絡（index 由 P8 fetch 建立）；index 空 → 如實回報（page 端靜默）。"""
        import modules.symbol_search as ss   # lazy — top-level import 會拖慢 page 啟動（佢 import futu）
        d = self._search_dir or ss.get_directory()
        if not d.entries:
            return {'ok': False, 'reason_code': 'index_empty',
                    'error': 'symbol index 空 — 先 run: python -m modules.symbol_search fetch'}
        q = str(kw.get('query', '')).strip().upper()
        markets = set(kw.get('markets') or [])
        fund = kw.get('fund')
        hits = [e for e in d.search(q, limit=5000)           # 已排序（exact > prefix > contains）；
                # limit 要夠高 — 市場前綴會 match 幾千股票，唔然期貨主連截唔到（search 成本一樣，只係截斷）
                if (not markets or e.get('market') in markets)          # 市場 combo 過濾（All = 唔 filter）
                and (not fund or e.get('type') == 'ETF')               # HKFUND/USFUND → 只 ETF
                and e.get('type') != 'IDX']                            # 指數唔可落單 — 交易頁唔列 IDX
        if re.fullmatch(r'(?:HK|US|SH|SZ)\.?', q):
            # 純市場前綴（打「HK」）→ 期貨主連（頭 10）+ 股票（40）混合：唔然 169 隻期貨會淹沒股票 /
            # 50 cap 又截走 HSImain。搵特定主連打 HSI / 恒指 → 佢排第一（index prefix match）
            fut = [e for e in hits if e.get('type') == 'FUTURE'][:10]
            rest = [e for e in hits if e.get('type') != 'FUTURE'][:40]
            hits = fut + rest
        results = [{'code': e['code'], 'name': e.get('name', ''),
                    'name_zh': e.get('name_zh', ''), 'name_en': e.get('name_en', '')}
                   for e in hits[:50]]   # cap — completer 唔好一次塞幾百項
        return {'ok': True, 'results': results}

    def _ensure_qctx(self):
        """quote ctx lazy 建（stock_search / quote 共用）；失敗 → result dict。"""
        if self._qctx is not None:
            return None
        host, port = self._last_hostport or ('127.0.0.1', 11111)
        try:
            if self._qctx_factory is not None:
                self._qctx = self._qctx_factory(host=host, port=int(port))
            else:
                from futu import OpenQuoteContext   # lazy — import 要幾秒
                self._qctx = OpenQuoteContext(host=host, port=int(port))
        except Exception as e:
            return {'ok': False, 'reason_code': _classify_error(e),
                    'error': str(e) or e.__class__.__name__}
        return None

    def _snapshot(self, code):
        """單碼 snapshot → last_price / lot_size（預填起步；只讀，冇副作用）。"""
        from futu import RET_OK
        ret, df = self._qctx.get_market_snapshot([code])
        if ret != RET_OK or not hasattr(df, 'columns') or df.empty:
            return {'ok': False, 'error': str(df)}
        r = df.iloc[0]
        try:
            last_price = float(r.get('last_price') or 0.0)
            lot_size = int(r.get('lot_size') or 0)
        except (TypeError, ValueError):
            return {'ok': False, 'error': f'snapshot 數值欄異常: {dict(r)!r}'}
        if not last_price:   # 唔完整 code / 停牌 → 冇效價當失敗（靜默，唔污染 hint/預填）
            return {'ok': False, 'error': f'{code} 冇行情'}
        return {'ok': True, 'code': code, 'last_price': last_price, 'lot_size': lot_size}

    def _ensure_price_handler(self):
        """K_1M push handler（lazy，掛喺 qctx）— 收到 bar 更新即刻用最新 close emit 'price_tick'。
        同 futu_client.MyCurKlineHandler 同一 pattern：SDK callback thread → Qt signal（queued）。
        （實測呢個 OpenD：QUOTE subtype push 永遠唔到，K 線 push 正常流 — 所以市價行 K 線 push。）"""
        if self._price_handler is not None:
            return self._price_handler
        from futu import CurKlineHandlerBase, RET_OK   # K 線 push → CurKlineHandlerBase

        class _PricePushHandler(CurKlineHandlerBase):
            def __init__(self, worker):
                super().__init__()
                self._worker = worker

            def _emit(self, code, price):   # 分開出嚟 — e2e 可以直接 inject tick（唔使扮 protobuf）
                self._worker.sig_result.emit('price_tick', {'ok': True, 'code': str(code),
                                                            'last_price': float(price)})

            def on_recv_rsp(self, rsp_pb):
                ret, df = super().on_recv_rsp(rsp_pb)
                if ret == RET_OK and df is not None and not df.empty:
                    r = df.iloc[-1]   # 最新 bar 嘅 close = 現價
                    try:
                        self._emit(r['code'], r['close'])
                    except (KeyError, TypeError, ValueError):
                        pass
                return ret, df

        self._price_handler = _PricePushHandler(self)
        self._qctx.set_handler(self._price_handler)
        return self._price_handler

    def _canonical_code(self, raw):
        """MARKET.CODE → index canonical 大細階。期貨主連 HK.HSImain 嘅 'main' 係細階 —
        直接 upper() 變「未知股票 HSIMAIN」（真 OpenD 實測）；相反用戶打細階 hk.00700 都還原返大階。
        index 未 load / 撳唔到 → fallback upper()（舊行為，股票 code 唔會壞）。"""
        raw = str(raw).strip()
        try:
            import modules.symbol_search as ss
            e = (self._search_dir or ss.get_directory()).get(raw)
            if e:
                return e['code']
        except Exception:
            pass
        return raw.upper()

    def _op_subscribe(self, kw):
        """全流市價（KLINE_STREAM 方式：訂閱 K_1M push 一次，之後 OpenD 主動推，唔再 poll）：
        snapshot 起步（last_price + lot_size 預填）+ K 線 push 最新 close 更新現價。
        換 code → 先 cancel 舊訂閱（一次只跟一個 code）。"""
        err = self._need_ctx()
        if err:
            return err
        err = self._ensure_qctx()
        if err:
            return err
        from futu import RET_OK, KLType, Session
        code = self._canonical_code(kw['code'])   # 保 canonical 大細階（HK.HSImain 細階 main）
        snap = self._snapshot(code)   # snapshot 失敗都照訂閱 push — 有市冇市如實
        self._ensure_price_handler()
        if self._sub_code and self._sub_code != code:
            try:
                self._qctx.unsubscribe([self._sub_code], [KLType.K_1M])
            except Exception:
                pass
            self._sub_code = None
        if self._sub_code != code:
            # futu 10.x 統一 subscribe API（subscribe_quote 已廢）；session=Session.ALL 同 futu_client 一致
            ret, msg = self._qctx.subscribe([code], [KLType.K_1M], subscribe_push=True, session=Session.ALL)
            if ret != RET_OK:
                snap['subscribed'] = False
                if snap.get('ok'):
                    return snap   # 訂閱失敗但有 snapshot → 照預填（push 冇，如實標明冇串流）
                return {'ok': False, 'subscribed': False, 'error': str(msg)}
            self._sub_code = code
        snap['subscribed'] = True
        return snap

    def _op_unlock_probe(self, kw):
        """解鎖狀態探測 — modify_order 撤一個唔存在嘅 order_id（無副作用）。
        真 OpenD 實測：未解鎖 → 「沒有解鎖交易，請先解鎖交易」（unlock 錯誤優先於訂單不存在）；
        已解鎖 → 訂單不存在類錯誤。其他錯誤 → ok False（page 端唔改狀態，如實）。"""
        err = self._need_ctx()
        if err:
            return err
        from futu import ModifyOrderOp, RET_OK
        ret, msg = self._ctx.modify_order(
            ModifyOrderOp.CANCEL, 999999999, 0, 0,
            trd_env=kw['trd_env'], acc_id=int(kw['acc_id']))
        if self._is_unlock_error(msg):
            return {'ok': True, 'unlocked': False}
        low = str(msg).lower()
        if ret == RET_OK or '不存在' in str(msg) or 'not exist' in low or 'not found' in low \
                or 'invalid' in low or 'order' in low:
            return {'ok': True, 'unlocked': True}
        return {'ok': False, 'error': str(msg)}

    def _op_place_order(self, kw):
        err = self._need_ctx()
        if err:
            return err
        from futu import OrderType, RET_OK, TimeInForce, TrdSide
        otype = getattr(OrderType, kw['otype'], None)
        tif = getattr(TimeInForce, kw['tif'], None)
        side = TrdSide.BUY if kw['side'] == 'BUY' else TrdSide.SELL
        price = float(kw['price']) if kw.get('price') not in (None, '', 0) else 0.0
        ret, data = self._ctx.place_order(
            price=price, qty=int(kw['qty']), code=self._canonical_code(kw['code']), trd_side=side,
            order_type=otype, trd_env=kw['trd_env'], acc_id=int(kw['acc_id']),
            time_in_force=tif)
        if ret != RET_OK:
            if self._is_unlock_error(data):
                return {'ok': False, 'reason_code': 'unlock_needed', 'error': str(data)}
            return {'ok': False, 'error': str(data)}
        oid = data['order_id'].iloc[0] if hasattr(data, 'columns') and not data.empty else '?'
        return {'ok': True, 'order_id': _fmt(oid)}

    def _op_orders(self, kw):
        err = self._need_ctx()
        if err:
            return err
        from futu import RET_OK
        ret, df = self._ctx.order_list_query(trd_env=kw['trd_env'], acc_id=int(kw['acc_id']))
        if ret != RET_OK:
            return {'ok': False, 'error': str(df)}
        rows = [{c: r[c] for c in ORDER_COLS if c in r} for r in df.to_dict(orient='records')]
        return {'ok': True, 'rows': rows}

    def _op_positions(self, kw):
        err = self._need_ctx()
        if err:
            return err
        from futu import RET_OK
        ret, df = self._ctx.position_list_query(trd_env=kw['trd_env'], acc_id=int(kw['acc_id']))
        if ret != RET_OK:
            return {'ok': False, 'error': str(df)}
        rows = [{c: r[c] for c in POS_COLS if c in r} for r in df.to_dict(orient='records')]
        return {'ok': True, 'rows': rows}

    def _op_accinfo(self, kw):
        err = self._need_ctx()
        if err:
            return err
        from futu import RET_OK
        ret, df = self._ctx.accinfo_query(trd_env=kw['trd_env'], acc_id=int(kw['acc_id']))
        if ret != RET_OK:
            return {'ok': False, 'error': str(df)}
        recs = df.to_dict(orient='records')
        return {'ok': True, 'info': recs[0] if recs else {}}

    def _op_cancel_order(self, kw):
        err = self._need_ctx()
        if err:
            return err
        from futu import ModifyOrderOp, RET_OK
        ret, msg = self._ctx.modify_order(
            ModifyOrderOp.CANCEL, int(kw['order_id']), qty=0, price=0,
            trd_env=kw['trd_env'], acc_id=int(kw['acc_id']))
        if ret != RET_OK:
            if self._is_unlock_error(msg):
                return {'ok': False, 'reason_code': 'unlock_needed', 'error': str(msg)}
            return {'ok': False, 'error': str(msg)}
        return {'ok': True}

    def _op_cancel_all(self, kw):
        err = self._need_ctx()
        if err:
            return err
        from futu import RET_OK
        ret, msg = self._ctx.cancel_all_order(trd_env=kw['trd_env'], acc_id=int(kw['acc_id']))
        if ret != RET_OK:
            if self._is_unlock_error(msg):
                return {'ok': False, 'reason_code': 'unlock_needed', 'error': str(msg)}
            return {'ok': False, 'error': str(msg)}
        return {'ok': True}


# ── 頁面級 QSS template：只 style 本頁自己嘅 widget（pagetitle/pagebody 由 app-level [og="pagecard"] 規則處理）──
_QSS_TPL = Template('''
QWidget#futu_trade_page { background-color: $window; }

QLabel[role="sectitle"] { color: $text; font-size: 16px; font-weight: bold; }
QLabel[role="formlabel"] { color: $muted; font-size: 13px; }
QLabel[role="result"] { color: $text; font-size: 13px; }

QLineEdit {
    background-color: $surface; color: $text;
    border: 1px solid $border; border-radius: 5px; padding: 4px 8px; font-size: 13px;
}
QLineEdit:focus { border: 1px solid $accent; }
QLineEdit:disabled { background-color: $card; color: $muted; }

QComboBox {
    background-color: $card; color: $text;
    border: 1px solid $border; border-radius: 6px; padding: 5px 10px; font-size: 13px;
}
QComboBox QAbstractItemView {
    background-color: $surface; color: $text;
    selection-background-color: $accent; border: 1px solid $border;
}

QPushButton[og="actionbtn"] {
    background-color: $card; color: $text;
    border: 1px solid $border; border-radius: 6px; padding: 7px 14px; font-size: 13px;
}
QPushButton[og="actionbtn"]:hover { background-color: $border; }
QPushButton[og="actionbtn"]:disabled { background-color: $surface; color: $muted; }

QPushButton[og="primarybtn"] {
    background-color: $accent; color: #FFFFFF; font-weight: bold;
    border: none; border-radius: 6px; padding: 7px 18px; font-size: 13px;
}
QPushButton[og="primarybtn"]:hover { background-color: $accent_pressed; }
QPushButton[og="primarybtn"]:disabled { background-color: $border; color: $muted; }

/* 模擬/實盤環境切換（exclusive toggle）— checked = accent */
QPushButton[og="envbtn"] {
    background-color: $card; color: $muted;
    border: 1px solid $border; border-radius: 6px; padding: 6px 14px; font-size: 13px;
}
QPushButton[og="envbtn"]:hover { background-color: $border; }
QPushButton[og="envbtn"]:checked { background-color: $accent; color: #FFFFFF; border: none; font-weight: bold; }

/* 買入/賣出 — 固定語義色（港股慣例：紅買綠賣；唔入 palette，兩 theme 通用）*/
QPushButton[og="buybtn"] {
    background-color: #C93D26; color: #FFFFFF; font-weight: bold;
    border: none; border-radius: 6px; padding: 8px 14px; font-size: 13px;
}
QPushButton[og="buybtn"]:hover { background-color: #E04A33; }
QPushButton[og="buybtn"]:disabled { background-color: $border; color: $muted; }

QPushButton[og="sellbtn"] {
    background-color: #1E8449; color: #FFFFFF; font-weight: bold;
    border: none; border-radius: 6px; padding: 8px 14px; font-size: 13px;
}
QPushButton[og="sellbtn"]:hover { background-color: #27AE60; }
QPushButton[og="sellbtn"]:disabled { background-color: $border; color: $muted; }

QTableWidget {
    background-color: $surface; alternate-background-color: $card; color: $text;
    border: 1px solid $border; gridline-color: $border; font-size: 12px;
}
QTableWidget::item:selected { background-color: $accent; color: #FFFFFF; }
QHeaderView::section {
    background-color: $card; color: $muted; border: none;
    padding: 4px 6px; font-size: 12px; font-weight: bold;
}
''')


class FutuTradePage(QWidget):
    """FUTU 交易頁 — 兩欄佈局：左 = 今日訂單 + 持倉資金；右 = 連線帳戶（SIM/REAL 切換）+ 下單。

    - 所有 SDK 調用經 `_FutuTradeWorker`（單一 QThread + queue 串行）— UI 唔會 block。
    - 帳戶表只顯示 ACTIVE 狀態、且符合當前環境（模擬/實盤 toggle）+ 市場過濾嘅帳戶；
      切換時訂單/持倉/資金全部跟住重新 query — 唔混模擬同實盤。
    - REAL 帳戶嘅下單/撤單操作先彈 QMessageBox 確認；SIMULATE 直接行。
    - 解鎖狀態（只實盤顯示）：新版 OpenD 禁止 SDK unlock_trade — 用戶必須喺 OpenD GUI 手動
      「解鎖交易」。冇查詢 API → 狀態由落單/撤單成敗被動推斷（_set_unlock_state：
      成功=已解鎖 / unlock_needed 錯誤=未解鎖+提示 / 其他=未知，如實標明）。
    - code_edit 模糊輸入：debounce → worker stock_search（本地 symbol index — modules/symbol_search，
      含期貨主連/指數 + 中英名欄）→ QCompleter「MARKET.CODE」候選 + 名稱 hint（跟 GUI 語言）。
      落單前 code 經 _CODE_RE 驗證（中文輸入唔會送入 place_order）。
    """

    def __init__(self):
        super().__init__()
        self.setObjectName('futu_trade_page')
        self.setAttribute(Qt.WA_StyledBackground, True)   # bare QWidget 要呢個先會畫頁面級 QSS background
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 24)

        # ── Header card（標題 + 說明；pagetitle/pagebody 由 app-level [og="pagecard"] 規則 style）──
        header = self._make_card()
        hv = QVBoxLayout(header)
        hv.setContentsMargins(32, 24, 32, 24)
        self.title_lbl = QLabel()
        self.title_lbl.setProperty('role', 'pagetitle')
        self.body_lbl = QLabel()
        self.body_lbl.setProperty('role', 'pagebody')
        self.body_lbl.setWordWrap(True)
        hv.addWidget(self.title_lbl)
        hv.addSpacing(10)
        hv.addWidget(self.body_lbl)
        v.addWidget(header)

        # ── 兩欄主區：左 = 訂單 + 持倉 / 右 = 連線帳戶 + 下單（stretch 填剩餘高度）──
        cols = QHBoxLayout()
        cols.setSpacing(16)

        # ══ 左欄 — 今日訂單 card（stretch）══
        left_col = QVBoxLayout()
        left_col.setSpacing(16)

        orders_card = self._make_card()
        wv = QVBoxLayout(orders_card)
        wv.setContentsMargins(32, 24, 32, 24)
        self.orders_title_lbl = QLabel()
        self.orders_title_lbl.setProperty('role', 'sectitle')
        wv.addWidget(self.orders_title_lbl)
        wv.addSpacing(12)

        row3 = QHBoxLayout()
        self.orders_refresh_btn = QPushButton(t('trade_refresh', DEFAULT_LANG))
        self.orders_refresh_btn.setObjectName('trade_orders_refresh_btn')
        self.orders_refresh_btn.setProperty('og', 'actionbtn')
        self.cancel_sel_btn = QPushButton(t('trade_cancel_sel', DEFAULT_LANG))
        self.cancel_sel_btn.setObjectName('trade_cancel_sel_btn')
        self.cancel_sel_btn.setProperty('og', 'actionbtn')
        self.cancel_all_btn = QPushButton(t('trade_cancel_all', DEFAULT_LANG))
        self.cancel_all_btn.setObjectName('trade_cancel_all_btn')
        self.cancel_all_btn.setProperty('og', 'actionbtn')
        row3.addWidget(self.orders_refresh_btn)
        row3.addSpacing(8)
        row3.addWidget(self.cancel_sel_btn)
        row3.addSpacing(8)
        row3.addWidget(self.cancel_all_btn)
        row3.addStretch(1)
        wv.addLayout(row3)
        wv.addSpacing(8)

        self.orders_status_lbl = QLabel('')
        self.orders_status_lbl.setObjectName('trade_orders_status')
        self.orders_status_lbl.setProperty('role', 'result')
        self.orders_status_lbl.setWordWrap(True)
        wv.addWidget(self.orders_status_lbl)
        wv.addSpacing(8)

        self.orders_table = self._make_table('trade_orders_table', ORDER_COLS)
        wv.addWidget(self.orders_table, 1)
        left_col.addWidget(orders_card, 3)

        # ══ 左欄 — 持倉同帳戶資金 card（stretch）══
        pos_card = self._make_card()
        pv = QVBoxLayout(pos_card)
        pv.setContentsMargins(32, 24, 32, 24)
        self.pos_title_lbl = QLabel()
        self.pos_title_lbl.setProperty('role', 'sectitle')
        pv.addWidget(self.pos_title_lbl)
        pv.addSpacing(12)

        row4 = QHBoxLayout()
        self.pos_refresh_btn = QPushButton(t('trade_refresh', DEFAULT_LANG))
        self.pos_refresh_btn.setObjectName('trade_pos_refresh_btn')
        self.pos_refresh_btn.setProperty('og', 'actionbtn')
        row4.addWidget(self.pos_refresh_btn)
        row4.addStretch(1)
        pv.addLayout(row4)
        pv.addSpacing(8)

        self.accinfo_lbl = QLabel('')
        self.accinfo_lbl.setObjectName('trade_accinfo')
        self.accinfo_lbl.setProperty('role', 'result')
        self.accinfo_lbl.setWordWrap(True)
        pv.addWidget(self.accinfo_lbl)
        pv.addSpacing(8)

        self.pos_table = self._make_table('trade_pos_table', POS_COLS)
        pv.addWidget(self.pos_table, 1)
        left_col.addWidget(pos_card, 2)

        # ══ 右欄 — 連線同帳戶 card ══
        right_col = QVBoxLayout()
        right_col.setSpacing(16)

        conn_card = self._make_card()
        cv = QVBoxLayout(conn_card)
        cv.setContentsMargins(32, 24, 32, 24)
        self.conn_title_lbl = QLabel()
        self.conn_title_lbl.setProperty('role', 'sectitle')
        cv.addWidget(self.conn_title_lbl)
        cv.addSpacing(12)

        row1 = QHBoxLayout()
        self.host_lbl = self._form_label('conn_host')
        self.host_edit = QLineEdit()
        self.host_edit.setObjectName('trade_host')
        self.port_lbl = self._form_label('conn_port')
        self.port_edit = QLineEdit()
        self.port_edit.setObjectName('trade_port')
        row1.addWidget(self.host_lbl)
        row1.addWidget(self.host_edit, 2)
        row1.addSpacing(8)
        row1.addWidget(self.port_lbl)
        row1.addWidget(self.port_edit, 1)
        cv.addLayout(row1)
        cv.addSpacing(8)

        # 🤖 連線/斷開按鈕已刪（用戶要求：默認自動連線）— 連線狀態只喺 conn_status_lbl 如實反映
        cv.addSpacing(10)

        self.conn_status_lbl = QLabel('')
        self.conn_status_lbl.setObjectName('trade_conn_status')
        self.conn_status_lbl.setProperty('role', 'result')
        self.conn_status_lbl.setWordWrap(True)
        cv.addWidget(self.conn_status_lbl)
        cv.addSpacing(8)

        # 環境切換（SIM/REAL exclusive，預設模擬 — 安全邊）+ 市場過濾（帳戶表雙重 client-side filter）
        row_env = QHBoxLayout()
        self.env_sim_btn = QPushButton(t('trade_env_sim', DEFAULT_LANG))
        self.env_sim_btn.setObjectName('trade_env_sim_btn')
        self.env_sim_btn.setProperty('og', 'envbtn')
        self.env_sim_btn.setCheckable(True)
        self.env_real_btn = QPushButton(t('trade_env_real', DEFAULT_LANG))
        self.env_real_btn.setObjectName('trade_env_real_btn')
        self.env_real_btn.setProperty('og', 'envbtn')
        self.env_real_btn.setCheckable(True)
        self._env_group = QButtonGroup(self)
        self._env_group.setExclusive(True)
        self._env_group.addButton(self.env_sim_btn)
        self._env_group.addButton(self.env_real_btn)
        self.env_sim_btn.setChecked(True)   # 喺 connect signal 之前 set — 避免建檔時觸發 filter logic
        row_env.addWidget(self.env_sim_btn, 1)
        row_env.addSpacing(8)
        row_env.addWidget(self.env_real_btn, 1)
        row_env.addSpacing(12)
        self.market_lbl = self._form_label('trade_market')
        self.market_combo = QComboBox()
        self.market_combo.setObjectName('trade_market')
        self.market_combo.addItems(MARKET_FILTERS)
        row_env.addWidget(self.market_lbl)
        row_env.addWidget(self.market_combo, 1)
        cv.addLayout(row_env)
        cv.addSpacing(8)

        self.acc_table = self._make_table('trade_acc_table', ACC_COLS)
        self.acc_table.setMaximumHeight(160)
        cv.addWidget(self.acc_table, 1)
        cv.addSpacing(8)

        self.acc_sel_lbl = QLabel()
        self.acc_sel_lbl.setObjectName('trade_acc_selected')
        self.acc_sel_lbl.setProperty('role', 'result')
        cv.addWidget(self.acc_sel_lbl)
        cv.addSpacing(8)

        right_col.addWidget(conn_card)

        # ══ 右欄 — 下單 card（買入/賣出兩鍵；方向由按鍵決定）══
        order_card = self._make_card()
        ov = QVBoxLayout(order_card)
        ov.setContentsMargins(32, 24, 32, 24)
        self.order_title_lbl = QLabel()
        self.order_title_lbl.setProperty('role', 'sectitle')
        ov.addWidget(self.order_title_lbl)
        ov.addSpacing(12)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.code_lbl = self._form_label('trade_code')
        self.code_edit = QLineEdit()
        self.code_edit.setObjectName('trade_code')
        # 🤖 模糊輸入：completer 一律經 gateway/symbol_input；搜尋來源係 worker stock_search（異步 → set_hits）
        self.code_completer = FuzzyCompleter(parent=self)
        self.code_edit.setCompleter(self.code_completer)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)   # debounce — 打完先 query，唔係每按一次
        self.code_hint_lbl = QLabel('')
        self.code_hint_lbl.setObjectName('trade_code_hint')
        self.code_hint_lbl.setProperty('role', 'formlabel')
        code_cell = QWidget()
        ccv = QVBoxLayout(code_cell)
        ccv.setContentsMargins(0, 0, 0, 0)
        ccv.addWidget(self.code_edit)
        ccv.addWidget(self.code_hint_lbl)
        self.otype_lbl = self._form_label('trade_otype')
        self.otype_combo = QComboBox()
        self.otype_combo.setObjectName('trade_otype')
        self.otype_combo.addItems(ORDER_TYPES)
        self.price_lbl = self._form_label('trade_price')
        self.price_edit = QLineEdit()
        self.price_edit.setObjectName('trade_price')
        self.qty_lbl = self._form_label('trade_qty')
        self.qty_edit = QLineEdit()
        self.qty_edit.setObjectName('trade_qty')
        self.tif_lbl = self._form_label('trade_tif')
        self.tif_combo = QComboBox()
        self.tif_combo.setObjectName('trade_tif')
        for _tif in TIF_OPTIONS:   # 顯示三語（tif_* i18n）；userData = enum 名 — 提交用 currentData
            self.tif_combo.addItem(t(f'tif_{_tif}', DEFAULT_LANG), _tif)
        grid.addWidget(self.code_lbl, 0, 0)
        grid.addWidget(code_cell, 0, 1, 1, 3)
        grid.addWidget(self.otype_lbl, 1, 0)
        grid.addWidget(self.otype_combo, 1, 1)
        grid.addWidget(self.price_lbl, 1, 2)
        grid.addWidget(self.price_edit, 1, 3)
        grid.addWidget(self.qty_lbl, 2, 0)
        grid.addWidget(self.qty_edit, 2, 1)
        grid.addWidget(self.tif_lbl, 2, 2)
        grid.addWidget(self.tif_combo, 2, 3)
        ov.addLayout(grid)
        ov.addSpacing(6)

        # 解鎖狀態（只實盤顯示）— 新版 OpenD 只容許 GUI 解鎖；狀態由落單/撤單成敗被動推斷
        self.unlock_status_lbl = QLabel('')
        self.unlock_status_lbl.setObjectName('trade_unlock_status')
        self.unlock_status_lbl.setProperty('role', 'result')
        self.unlock_status_lbl.setWordWrap(True)
        ov.addWidget(self.unlock_status_lbl)
        ov.addSpacing(4)

        row2 = QHBoxLayout()
        self.buy_btn = QPushButton(t('trade_buy_btn', DEFAULT_LANG))
        self.buy_btn.setObjectName('trade_buy_btn')
        self.buy_btn.setProperty('og', 'buybtn')
        self.sell_btn = QPushButton(t('trade_sell_btn', DEFAULT_LANG))
        self.sell_btn.setObjectName('trade_sell_btn')
        self.sell_btn.setProperty('og', 'sellbtn')
        row2.addWidget(self.buy_btn, 1)
        row2.addSpacing(8)
        row2.addWidget(self.sell_btn, 1)
        ov.addLayout(row2)
        ov.addSpacing(10)

        self.order_result_lbl = QLabel('')
        self.order_result_lbl.setObjectName('trade_order_result')
        self.order_result_lbl.setProperty('role', 'result')
        self.order_result_lbl.setWordWrap(True)
        ov.addWidget(self.order_result_lbl)
        right_col.addWidget(order_card)
        right_col.addStretch(1)

        cols.addLayout(left_col, 3)
        cols.addLayout(right_col, 2)
        v.addLayout(cols, 1)

        # ── state ──
        self._lang = DEFAULT_LANG
        self._worker = _FutuTradeWorker(parent=self)
        self._worker.sig_result.connect(self._on_worker_result)
        self._ensure_worker()                    # worker 常開（warmup/搜尋唔使等 connect）
        self._worker.submit('warmup')            # 背景預熱 symbol index — 第一次搜尋即刻有反應
        self._connected = False
        self._unlock_state = None   # 解鎖狀態（被動推斷）：None=未知 / True=已解鎖 / False=未解鎖
        self._quote = None          # 當前 code 嘅 snapshot {last_price, lot_size} — price/qty 預填 + hint 顯示
        self._stream_on = False     # K_1M 全流市價訂閱生效 → hint 顯示「串流中 ✅」（如實，價冇郁都照示）
        self._price_manual = False  # 用戶改過 price/qty → 後嘅自動預填唔覆蓋手動值（textEdited 先計）
        self._qty_manual = False
        self._code_results = {}     # 'MARKET.CODE'（upper）→ name — completer hint 用
        self._acc = None            # 選定帳戶 dict（acc_id/trd_env/acc_type）
        self._accounts_all = []     # get_acc_list 全量（env/market filter 前）
        self._acc_rows = []         # 當前顯示嘅帳戶行（過濾後；selection index → 呢度）
        self._order_rows = []       # 訂單表行（cancel 時由 selection index 取 order_id）

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_app_quit)   # 退出收尾：斷 ctx（idempotent）
        theme_mod.add_listener(self._on_theme_changed)
        self._apply_embedded_theme(theme_mod.CURRENT)    # 初始 theme（讀 live module attr，避免 stale import binding）

        def _on_code_text(_t):   # 每按一次：hint 即時更新 + debounce 搜尋；換 code → 允許重新預填
            self._price_manual = False
            self._qty_manual = False
            self._quote = None
            self._stream_on = False   # 舊 code 嘅串流狀態唔串落新 code（新 subscribe 結果先更新）
            self._update_code_hint()
            self._search_timer.start()

        self.otype_combo.currentTextChanged.connect(self._on_otype_changed)
        self.market_combo.currentTextChanged.connect(lambda _t: self._on_account_filter_changed())
        self.env_sim_btn.toggled.connect(lambda _c: self._on_account_filter_changed())
        self.env_real_btn.toggled.connect(lambda _c: self._on_account_filter_changed())
        self.acc_table.itemSelectionChanged.connect(self._on_acc_selected)
        self.code_edit.textChanged.connect(_on_code_text)
        self._search_timer.timeout.connect(self._on_code_search)
        self.code_completer.activated.connect(self._on_code_activated)
        self.buy_btn.clicked.connect(lambda: self._on_place('BUY'))
        self.sell_btn.clicked.connect(lambda: self._on_place('SELL'))
        self.orders_refresh_btn.clicked.connect(lambda: self._refresh('orders'))
        self.cancel_sel_btn.clicked.connect(self._on_cancel_selected)
        self.cancel_all_btn.clicked.connect(self._on_cancel_all)
        self.pos_refresh_btn.clicked.connect(lambda: self._refresh('positions'))
        # textEdited 只喺用戶打字時 fire（setText 程序性更新唔計）→ 手動值唔會被自動預填覆蓋
        self.price_edit.textEdited.connect(lambda _t: setattr(self, '_price_manual', True))
        self.qty_edit.textEdited.connect(lambda _t: setattr(self, '_qty_manual', True))

        # 定時自動刷新（10s）：REAL+選定帳戶 → 解鎖狀態探測；有效 code → 最新價更新
        # handler 內部 check 條件 — 未連線/SIM 直接 return，唔使管 timer start/stop
        self._probe_timer = QTimer(self)
        self._probe_timer.setInterval(10000)
        self._probe_timer.timeout.connect(self._auto_probe)
        self._probe_timer.start()

        self._set_unlock_visible(self.env_real_btn.isChecked())   # 預設 SIM → 解鎖欄隱藏
        self._load_config_defaults()
        # 🤖 自動連線（用戶要求）：config 讀好先 start（port 要啱）；失敗 → RETRY_MS 後再試
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.setInterval(RETRY_MS)
        self._retry_timer.timeout.connect(self._on_connect)
        self._retry_timer.start(AUTO_CONNECT_DELAY_MS)
        self.retranslate(DEFAULT_LANG)

    # ── 建檔 helpers ─────────────────────────────────────────────
    @staticmethod
    def _make_card():
        card = QWidget()
        card.setProperty('og', 'pagecard')
        card.setAttribute(Qt.WA_StyledBackground, True)
        return card

    @staticmethod
    def _form_label(key):
        lbl = QLabel(t(key, DEFAULT_LANG))
        lbl.setProperty('role', 'formlabel')
        return lbl

    @staticmethod
    def _make_table(name, cols):
        tb = QTableWidget(0, len(cols))
        tb.setObjectName(name)
        tb.setHorizontalHeaderLabels(list(cols))
        tb.verticalHeader().setVisible(False)
        tb.setEditTriggers(QTableWidget.NoEditTriggers)
        tb.setSelectionBehavior(QTableWidget.SelectRows)
        tb.setSelectionMode(QTableWidget.SingleSelection)
        tb.setAlternatingRowColors(True)
        tb.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tb.verticalHeader().setDefaultSectionSize(26)   # 行高一致、唔擠
        return tb

    def _load_config_defaults(self):
        """host/port 預填 config.json futu section（唔寫返 — connection_page 負責 save）。"""
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f).get('futu', {})
            self.host_edit.setText(str(cfg.get('host', '127.0.0.1')))
            self.port_edit.setText(str(cfg.get('port', 11111)))
        except Exception:
            self.host_edit.setText('127.0.0.1')
            self.port_edit.setText('11111')

    # ── worker 橋接 ─────────────────────────────────────────────
    def _ensure_worker(self):
        if not self._worker.isRunning():
            self._worker.start()

    def _submit(self, op, **kw):
        """提交 op 入 worker queue（lazy start）；返回 False = 前置條件唔夠。"""
        if not self._connected and op != 'connect' and op != 'disconnect':
            return False
        self._ensure_worker()
        self._worker.submit(op, **kw)
        return True

    def _acc_kw(self):
        """選定帳戶 → worker kw（acc_id/trd_env）；冇選 → None。"""
        if not self._acc:
            return None
        return {'trd_env': str(self._acc.get('trd_env', '')), 'acc_id': self._acc['acc_id']}

    def _real_confirm(self, text):
        """REAL 帳戶操作先彈確認框；SIMULATE 直接放行。"""
        if not self._acc or str(self._acc.get('trd_env')) != 'REAL':
            return True
        ans = QMessageBox.question(
            self, t('trade_real_confirm_title', self._lang), text,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return ans == QMessageBox.Yes

    # ── 連線 / 帳戶 ─────────────────────────────────────────────
    def _on_connect(self):
        """自動連線唯一入口：構造後 timer 觸發 / 失敗重試觸發（冇按鈕）。"""
        host = self.host_edit.text().strip() or '127.0.0.1'
        port_text = self.port_edit.text().strip()
        try:
            int(port_text)
        except ValueError:
            self.conn_status_lbl.setText(t('conn_int_err', self._lang))
            return
        self.conn_status_lbl.setText(t('trade_busy', self._lang))
        self._ensure_worker()
        self._worker.submit('connect', host=host, port=int(port_text))

    def _apply_account_filter(self):
        """client-side 帳戶過濾 — ACTIVE only + 環境切換（SIM/REAL）+ 市場 combo；切換唔使重連。"""
        env = 'SIMULATE' if self.env_sim_btn.isChecked() else 'REAL'
        m = self.market_combo.currentText()
        rows = [a for a in self._accounts_all
                if str(a.get('acc_status')) == 'ACTIVE'
                and str(a.get('trd_env')) == env
                and (m == 'All' or m in str(a.get('trdmarket_auth', '')))]
        self._acc_rows = rows
        self._fill_table(self.acc_table, ACC_COLS, rows)

    def _on_account_filter_changed(self):
        """環境切換 / 市場過濾 → 重新 filter 帳戶表；選定帳戶唔喺新列表就自動選首行，
        並重 query 所有資料（訂單/持倉/資金跟隨環境 — 唔混模擬同實盤）。"""
        self._set_unlock_visible(self.env_real_btn.isChecked())   # 解鎖欄只實盤顯示
        self._apply_account_filter()
        if not self._connected:
            return
        cur = self._acc
        still_visible = bool(cur) and any(a.get('acc_id') == cur.get('acc_id') for a in self._acc_rows)
        if still_visible:
            return   # 選定冇變 — 資料已經正確（市場過濾唔影響 query 參數）
        if self._acc_rows:
            self.acc_table.selectRow(0)   # 視覺選中首行
            self._on_acc_selected()       # 直接同步 _acc + label（selectRow 對已選行唔會再發 signal）
            kw = self._acc_kw()
            if kw is not None:
                self._worker.submit('orders', **kw)
                self._worker.submit('positions', **kw)
                self._worker.submit('accinfo', **kw)
        else:
            self._clear_data_views(t('trade_no_active_acc', self._lang))

    def _clear_data_views(self, msg):
        """冇可選帳戶（環境/市場過濾後為空）→ 清空數據區 + 顯示提示。"""
        self._acc = None
        self.acc_sel_lbl.setStyleSheet('')
        self.acc_sel_lbl.setText(msg)
        self.orders_table.setRowCount(0)
        self.pos_table.setRowCount(0)
        self._order_rows = []
        self.orders_status_lbl.setText(msg)
        self.accinfo_lbl.setText(msg)

    def _on_acc_selected(self):
        r = self.acc_table.currentRow()
        if 0 <= r < len(self._acc_rows):
            self._acc = self._acc_rows[r]
            text = t('trade_acc_selected', self._lang).format(
                acc_id=_fmt(self._acc.get('acc_id')), env=self._acc.get('trd_env', '?'),
                type=self._acc.get('acc_type', '?'))
            # REAL 帳戶用 accent 色提醒（SIMULATE 保持普通 result 色）
            if str(self._acc.get('trd_env')) == 'REAL':
                pal = theme_mod.THEMES[theme_mod.CURRENT]
                self.acc_sel_lbl.setStyleSheet(f"color: {pal['accent']}; font-size: 13px;")
            else:
                self.acc_sel_lbl.setStyleSheet('')
            self.acc_sel_lbl.setText(text)
            self._probe_unlock_now()   # 選定 REAL 帳戶 → 即時探測解鎖狀態（唔使等落單）
        else:
            self._acc = None
            self.acc_sel_lbl.setStyleSheet('')
            self.acc_sel_lbl.setText(t('trade_no_account', self._lang))

    # ── 下單 / 解鎖 ─────────────────────────────────────────────
    def _on_otype_changed(self, otype):
        """MARKET 單唔使 price — disable 欄位防誤填。"""
        self.price_edit.setEnabled(otype != 'MARKET')

    def _set_unlock_visible(self, visible):
        """解鎖狀態欄只實盤（REAL env）先顯示 — 模擬盤唔需要解鎖。"""
        self.unlock_status_lbl.setVisible(visible)
        if visible:
            self._set_unlock_state(self._unlock_state)   # 顯示當前狀態（切換 env 都如實）
        else:
            self.unlock_status_lbl.setText('')

    def _set_unlock_state(self, state):
        """更新解鎖狀態欄 — 被動推斷（冇查詢 API）：None=未知 / True=已解鎖 / False=未解鎖。

        新版 OpenD 禁止 SDK unlock_trade — 解鎖只可以喺 OpenD GUI「解鎖交易」操作；
        信號來源：place/cancel 成功 → True；unlock_needed 錯誤 → False；其他情況 → None。
        """
        self._unlock_state = state
        key = {True: 'trade_unlock_state_unlocked',
               False: 'trade_unlock_state_locked'}.get(state, 'trade_unlock_state_unknown')
        self.unlock_status_lbl.setText(t('trade_unlock_status', self._lang) + t(key, self._lang))

    def _note_unlock_result(self, ok, result):
        """落單/撤單結果 → 更新解鎖狀態（成功=已解鎖；unlock_needed 錯誤=未解鎖；其他不動）。

        只 REAL 帳戶先有意義 — 模擬盤唔涉及解鎖，SIM 成敗唔好污染狀態。
        """
        if self._acc is None or str(self._acc.get('trd_env')) != 'REAL':
            return
        if ok:
            self._set_unlock_state(True)
        elif result.get('reason_code') == 'unlock_needed':
            self._set_unlock_state(False)

    def _on_code_search(self):
        """code_edit debounce timeout → worker stock_search（模糊候選，完整 code 都 match）；
        含「.」（完整/未完整 MARKET.CODE）→ 另外 quote（預填 price/qty；不完整 code 靜默失敗）。"""
        q = self.code_edit.text().strip()
        if len(q) < 1:
            self.code_completer.set_hits([])
            self._update_code_hint()
            return
        # 搜尋唔使連線 — 本地 symbol index 離線都得（落單/行情先要連線）；worker lazy start
        self._ensure_worker()
        if '.' in q and self._connected:   # MARKET.CODE（完整與否都試 — snapshot 預填 + 全流市價訂閱）
            self._worker.submit('subscribe', code=q)   # worker _canonical_code 還原 canonical 大細階
        m = self.market_combo.currentText()
        markets, fund = {'All': (['HK', 'US', 'SH', 'SZ'], False),
                         'HK': (['HK'], False), 'US': (['US'], False),
                         'HKFUND': (['HK'], True), 'USFUND': (['US'], True)}.get(m, (['HK'], False))
        self._worker.submit('stock_search', query=q.upper(), markets=markets, fund=fund)

    def _on_code_activated(self, text):
        """completer 揀咗候選（item =「CODE  名稱」）→ 欄入面淨返 CODE（apply_item：事件尾蓋返
        QCompleter 寫入嘅 item 文字）+ hint 顯示名稱。"""
        apply_item(self.code_edit, text)
        self._update_code_hint()

    def _update_code_hint(self):
        """code_edit → hint「CODE — name（跟語言）」；有 snapshot → 加「最新價 | 每手」。"""
        code = self.code_edit.text().strip()
        entry = self._code_results.get(code.upper())
        hint = f'{code} — {display_name(entry, self._lang)}' if entry else ''
        if self._quote and str(self._quote.get('code', '')).upper() == code.upper():
            hint = t('trade_code_quote', self._lang).format(
                hint=hint or code, price=_fmt(self._quote.get('last_price')),
                lot=_fmt(self._quote.get('lot_size')))
            if self._stream_on:   # 訂閱生效如實顯示 — 夜市價冇郁都唔會當冇串流
                hint += t('trade_stream_on', self._lang)
        self.code_hint_lbl.setText(hint)

    # ── 自動刷新（10s timer）：解鎖探測（最新價改由 QUOTE push 全流推，唔使 poll）──
    def _auto_probe(self):
        """定時：REAL+選定帳戶 → 解鎖狀態探測。條件喺 handler 內 check —
        timer 常開，未連線/SIM 直接 return。（價格更新見 'price_tick' 分支。）"""
        if not self._connected:
            return
        self._probe_unlock_now()

    def _probe_unlock_now(self):
        """即時解鎖探測（選帳戶 / 定時）— 只 REAL 帳戶有意義；結果喺 _on_worker_result 更新狀態欄。"""
        kw = self._acc_kw()
        if not self._connected or kw is None or str(self._acc.get('trd_env')) != 'REAL':
            return
        self._worker.submit('unlock_probe', **kw)

    def _apply_quote(self, result):
        """quote 結果 → 預填 price（最新價）/ qty（每手）— 用戶手動改過（textEdited）就唔覆蓋。"""
        code = self.code_edit.text().strip()
        if not code or str(result.get('code', '')).upper() != code.upper():   # stale — code 已改，丟掉（大細階唔敏感）
            return
        self._quote = {'code': code.upper(), 'last_price': result.get('last_price'),
                       'lot_size': result.get('lot_size')}
        if not self._price_manual and self._quote.get('last_price'):
            self.price_edit.setText(_fmt(self._quote['last_price']))
        if not self._qty_manual and self._quote.get('lot_size'):
            self.qty_edit.setText(_fmt(self._quote['lot_size']))
        self._update_code_hint()

    def _on_place(self, side):
        """side = 'BUY' / 'SELL'（由買入/賣出按鍵決定）→ place_order。"""
        if not self._connected or not self._acc:
            self.order_result_lbl.setText(t('trade_need_account', self._lang))
            return
        code = self.code_edit.text().strip()
        qty_text = self.qty_edit.text().strip()
        price_text = self.price_edit.text().strip()
        otype = self.otype_combo.currentText()
        try:
            qty = int(qty_text)
            if qty <= 0:
                raise ValueError
            price = float(price_text) if price_text else 0.0
            if otype != 'MARKET' and (not price_text or price <= 0):
                raise ValueError
        except ValueError:
            self.order_result_lbl.setText(t('trade_invalid_form', self._lang))
            return
        if not _CODE_RE.match(code):   # 必須完整 MARKET.CODE — 中文/未完整代碼唔送入 place_order
            self.order_result_lbl.setText(t('trade_invalid_code', self._lang))
            return
        kw = dict(code=code, side=side, otype=otype, price=price,
                  qty=qty, tif=self.tif_combo.currentData())   # enum 名（顯示三語喺 combo text）
        kw.update(self._acc_kw())
        confirm_text = t('trade_confirm_place', self._lang).format(
            code=code, side=kw['side'], qty=qty, price=_fmt(price), otype=otype,
            tif=self.tif_combo.currentText())
        if not self._real_confirm(confirm_text):
            return
        # 唔預檢解鎖（冇查詢 API）— 未解鎖時 place_order 會回 unlock_needed 類錯誤，
        # 如實回報 + 狀態欄提示去 OpenD GUI 解鎖（見 _on_worker_result）。
        self.buy_btn.setEnabled(False)   # 處理中兩鍵都 disable，防雙擊
        self.sell_btn.setEnabled(False)
        self.order_result_lbl.setText(t('trade_busy', self._lang))
        self._worker.submit('place_order', **kw)

    # ── 訂單 / 持倉 ─────────────────────────────────────────────
    def _refresh(self, op):
        """orders / positions（+accinfo）刷新 — 需要選定帳戶。"""
        kw = self._acc_kw()
        if not self._connected or kw is None:
            lbl = self.orders_status_lbl if op == 'orders' else self.accinfo_lbl
            lbl.setText(t('trade_need_account', self._lang))
            return
        self._worker.submit(op, **kw)
        if op == 'positions':
            self._worker.submit('accinfo', **kw)

    def _on_cancel_selected(self):
        kw = self._acc_kw()
        if not self._connected or kw is None:
            self.orders_status_lbl.setText(t('trade_need_account', self._lang))
            return
        r = self.orders_table.currentRow()
        if not (0 <= r < len(self._order_rows)):
            self.orders_status_lbl.setText(t('trade_no_selection', self._lang))
            return
        order_id = self._order_rows[r].get('order_id')
        confirm_text = t('trade_confirm_cancel', self._lang).format(id=_fmt(order_id))
        if not self._real_confirm(confirm_text):
            return
        self.cancel_sel_btn.setEnabled(False)
        self.orders_status_lbl.setText(t('trade_busy', self._lang))
        self._worker.submit('cancel_order', order_id=order_id, **kw)

    def _on_cancel_all(self):
        kw = self._acc_kw()
        if not self._connected or kw is None:
            self.orders_status_lbl.setText(t('trade_need_account', self._lang))
            return
        confirm_text = t('trade_confirm_cancel_all', self._lang).format(
            acc_id=_fmt(self._acc.get('acc_id')))
        if not self._real_confirm(confirm_text):
            return
        self.cancel_all_btn.setEnabled(False)
        self.orders_status_lbl.setText(t('trade_busy', self._lang))
        self._worker.submit('cancel_all', **kw)

    # ── worker 結果路由（QThread signal → main thread）──────────
    def _on_worker_result(self, op, result):
        if not isinstance(result, dict):
            return
        ok = bool(result.get('ok'))
        err_text = self._err_text(result)

        if op == 'connect':
            if ok:
                self._connected = True
                self._set_unlock_state(None)   # 新連線 → 解鎖狀態未知（如實）
                code = self.code_edit.text().strip()   # 離線期間已打咗完整 code → 連線即刻補預填+訂閱
                if '.' in code:
                    self._worker.submit('subscribe', code=code)
                self._accounts_all = result.get('accounts', [])
                self._apply_account_filter()
                # 預選第一行（方便即刻用；用戶可改）+ 自動載入該帳戶全部資料
                if self._acc_rows:
                    self.acc_table.selectRow(0)   # 視覺選中首行
                    self._on_acc_selected()       # 直接同步 _acc + label
                    kw = self._acc_kw()
                    if kw is not None:
                        self._worker.submit('orders', **kw)
                        self._worker.submit('positions', **kw)
                        self._worker.submit('accinfo', **kw)
                else:
                    self._clear_data_views(t('trade_no_active_acc', self._lang))
                self.conn_status_lbl.setText(t('trade_conn_ok', self._lang).format(
                    ms=f"{result.get('connect_ms', 0):.0f}", n=len(self._accounts_all)))
                self._retry_timer.stop()   # 🤖 成功 → 收返重試（保持連線）
            else:
                # 🤖 失敗如實顯示 + 定時自動重試（冇按鈕可以手動再連）
                self.conn_status_lbl.setText(t('trade_conn_fail', self._lang).format(err=err_text)
                                             + ' ' + t('trade_conn_retry', self._lang))
                self._retry_timer.start(RETRY_MS)

        elif op == 'disconnect':
            self._connected = False
            self._unlock_state = None
            self._acc = None
            self._accounts_all = []
            self._acc_rows = []
            self._order_rows = []
            self.acc_table.setRowCount(0)
            self.orders_table.setRowCount(0)
            self.pos_table.setRowCount(0)
            self.acc_sel_lbl.setText(t('trade_no_account', self._lang))
            self.accinfo_lbl.setText('')
            self.orders_status_lbl.setText('')
            self.unlock_status_lbl.setText('')
            self.conn_status_lbl.setText('')
            self._retry_timer.start(RETRY_MS)   # 🤖 斷開（例如 OpenD 熄咗）→ 自愈：定時自動重連
            self.code_completer.set_hits([])
            self._code_results = {}
            self._stream_on = False
            self.code_hint_lbl.setText('')

        elif op == 'stock_search':
            if ok:   # 搜尋失敗唔打斷輸入 — 候選保持原狀，下次輸入再試（如實：靜默）
                results = result.get('results', [])
                self._code_results = {r['code'].upper(): r for r in results}   # code → entry（hint 顯示名用）
                # 🤖 候選 item =「CODE  名稱（跟語言）」＋主動彈 popup 全部經 symbol_input.set_hits：
                # QCompleter 只喺 key event 嗰刻彈，而 model 係 debounce + worker 異步之後先填好
                #（用戶實測「冇反應」）→ set_hits 內部會主動 complete()
                self.code_completer.set_hits(results)
                self._update_code_hint()

        elif op == 'unlock_probe':
            if ok:   # 探測成功 → 如實更新；其他錯誤（ok False）唔改狀態（唔亂猜）
                self._set_unlock_state(bool(result.get('unlocked')))

        elif op == 'subscribe':
            if 'subscribed' in result:   # 串流狀態如實（snapshot 失敗但訂閱成功都照顯示）
                self._stream_on = bool(result.get('subscribed'))
            if ok:   # snapshot 起步失敗靜默 — 唔打斷輸入，hint/預填保持原狀（push 照收）
                self._apply_quote(result)

        elif op == 'price_tick':   # 全流市價 push — 只更新當前 code（stale guard 同 snapshot 一樣，大細階唔敏感）
            code = self.code_edit.text().strip().upper()
            if ok and self._quote and self._quote.get('code') == code \
                    and str(result.get('code', '')).upper() == code:
                self._quote['last_price'] = result.get('last_price')
                if not self._price_manual and result.get('last_price'):
                    self.price_edit.setText(_fmt(result['last_price']))
                self._update_code_hint()

        elif op == 'place_order':
            self.buy_btn.setEnabled(True)
            self.sell_btn.setEnabled(True)
            self._note_unlock_result(ok, result)   # 被動推斷解鎖狀態
            if ok:
                self.order_result_lbl.setText(t('trade_place_ok', self._lang).format(
                    id=result.get('order_id', '?')))
                kw = self._acc_kw()   # 落單成功 → 自動刷新今日訂單
                if kw is not None:
                    self._worker.submit('orders', **kw)
            else:
                self.order_result_lbl.setText(
                    t('trade_place_fail', self._lang).format(err=err_text))

        elif op == 'orders':
            if self._acc is None:      # stale result — 帳戶已 deselect（filter 清空 / disconnect）→ 保持空 view
                return
            if ok:
                self._order_rows = result.get('rows', [])
                self._fill_table(self.orders_table, ORDER_COLS, self._order_rows)
            else:
                self.orders_status_lbl.setText(
                    t('trade_op_fail', self._lang).format(err=err_text))

        elif op == 'positions':
            if self._acc is None:      # stale result — 同上
                return
            if ok:
                self._fill_table(self.pos_table, POS_COLS, result.get('rows', []))
            else:
                self.accinfo_lbl.setText(t('trade_op_fail', self._lang).format(err=err_text))

        elif op == 'accinfo':
            if self._acc is None:      # stale result — 同上
                return
            if ok:
                info = result.get('info', {})
                parts = [f'{k}={_fmt(info.get(k))}' for k in ACCINFO_KEYS
                         if _fmt(info.get(k)) not in ('', 'N/A')]
                self.accinfo_lbl.setText(t('trade_accinfo_lbl', self._lang).format(
                    kv=' | '.join(parts) if parts else t('trade_op_fail', self._lang).format(err='-')))
            else:
                self.accinfo_lbl.setText(t('trade_op_fail', self._lang).format(err=err_text))

        elif op == 'cancel_order':
            self.cancel_sel_btn.setEnabled(True)
            self._note_unlock_result(ok, result)   # 被動推斷解鎖狀態
            if ok:
                r = self.orders_table.currentRow()
                oid = _fmt(self._order_rows[r].get('order_id')) if 0 <= r < len(self._order_rows) else '?'
                self.orders_status_lbl.setText(t('trade_cancel_ok', self._lang).format(id=oid))
            else:
                self.orders_status_lbl.setText(
                    t('trade_op_fail', self._lang).format(err=err_text))

        elif op == 'cancel_all':
            self.cancel_all_btn.setEnabled(True)
            self._note_unlock_result(ok, result)   # 被動推斷解鎖狀態
            if ok:
                self.orders_status_lbl.setText(t('trade_cancel_all_ok', self._lang))
            else:
                self.orders_status_lbl.setText(
                    t('trade_op_fail', self._lang).format(err=err_text))

    def _err_text(self, result):
        """result dict → 人話錯誤字串（reason_code 映射 i18n，同 connection_page pattern）。"""
        rc = result.get('reason_code')
        if rc in ('refused', 'timeout'):
            return t(f'conn_reason_{rc}', self._lang)
        if rc == 'unlock_needed':
            return t('trade_unlock_needed', self._lang)
        return str(result.get('error', '?'))

    def _apply_headers(self, table, cols):
        """欄名三語（col_* i18n）— fill 時套用；retranslate 直接調呢個即時換欄名（唔使重填數據）。"""
        table.setHorizontalHeaderLabels([t(f'col_{c}', self._lang) for c in cols])

    def _fill_table(self, table, cols, rows):
        """rows（list of dict）→ QTableWidget（_fmt 格式化；缺欄位顯示空；欄名 i18n；數值欄右對齊）。"""
        self._apply_headers(table, cols)
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, col in enumerate(cols):
                item = QTableWidgetItem(_fmt(row.get(col)))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if col in NUMERIC_COLS:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(r, c, item)

    # ── theme 傳播 / i18n / 退出清理 ────────────────────────────
    def _apply_embedded_theme(self, name: str):
        """套用頁面級 QSS（palette 值由 THEMES[name] 注入）— cascade 入本頁子 widget。"""
        pal = theme_mod.THEMES[name]
        self.setStyleSheet(_QSS_TPL.substitute(pal))

    def _on_theme_changed(self, name: str):
        """外殼 / standalone window theme 切換（apply_theme listener）→ 本頁跟住換。"""
        self._apply_embedded_theme(name)

    def retranslate(self, lang: str):
        """外殼 / standalone window 語言切換時調用 — 全部文字跟隨（form 值/表格數據唔變）。"""
        self._lang = lang
        self.code_completer.lang = lang   # 🤖 dropdown 名稱跟語言（symbol_input / display_for 同一把尺）
        self.title_lbl.setText(t('page_futu_trade_title', lang))
        self.body_lbl.setText(t('page_futu_trade_body', lang))
        self.conn_title_lbl.setText(t('trade_conn_title', lang))
        # Host/Port 三語同字 — 建檔時已 set，唔使重設
        self.market_lbl.setText(t('trade_market', lang))
        self.env_sim_btn.setText(t('trade_env_sim', lang))
        self.env_real_btn.setText(t('trade_env_real', lang))
        self._set_unlock_state(self._unlock_state)   # 狀態欄按當前狀態重新翻譯
        self.order_title_lbl.setText(t('trade_order_title', lang))
        self.code_lbl.setText(t('trade_code', lang))
        self.otype_lbl.setText(t('trade_otype', lang))
        self.price_lbl.setText(t('trade_price', lang))
        self.qty_lbl.setText(t('trade_qty', lang))
        self.tif_lbl.setText(t('trade_tif', lang))
        cur_tif = self.tif_combo.currentData() or 'DAY'   # TIF 選項重建（三語顯示，保留選中 enum）
        self.tif_combo.blockSignals(True)
        self.tif_combo.clear()
        for _tif in TIF_OPTIONS:
            self.tif_combo.addItem(t(f'tif_{_tif}', lang), _tif)
        self.tif_combo.setCurrentIndex(max(0, self.tif_combo.findData(cur_tif)))
        self.tif_combo.blockSignals(False)
        self.buy_btn.setText(t('trade_buy_btn', lang))
        self.sell_btn.setText(t('trade_sell_btn', lang))
        self.orders_title_lbl.setText(t('trade_orders_title', lang))
        self.orders_refresh_btn.setText(t('trade_refresh', lang))
        self.cancel_sel_btn.setText(t('trade_cancel_sel', lang))
        self.cancel_all_btn.setText(t('trade_cancel_all', lang))
        self.pos_title_lbl.setText(t('trade_pos_title', lang))
        self.pos_refresh_btn.setText(t('trade_refresh', lang))
        # 動態 label（acc_sel / status）跟住重譯一次；表格欄名即時換（數據唔使重填）；hint 跟語言
        if self._acc:
            self._on_acc_selected()
        else:
            self.acc_sel_lbl.setText(t('trade_no_account', lang))
        for tb, cols in ((self.acc_table, ACC_COLS), (self.orders_table, ORDER_COLS),
                         (self.pos_table, POS_COLS)):
            self._apply_headers(tb, cols)
        self._update_code_hint()

    def _on_app_quit(self):
        """One Gate / standalone window 退出 → 斷 ctx（idempotent；worker finally 兜底）。"""
        if self._worker.isRunning():
            self._worker.stop_and_wait(5000)


if __name__ == '__main__':
    from gateway.pages.base_page import run_standalone
    run_standalone(FutuTradePage, 'page_futu_trade_title')

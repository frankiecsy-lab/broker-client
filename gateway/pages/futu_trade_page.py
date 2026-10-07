"""One Gate Page 4 — FUTU 交易管理（下單 / 今日訂單 / 持倉同帳戶資金）。

- **佈局**：兩欄 — 左 = 今日訂單 + 持倉/帳戶資金；右 = 連線帳戶 + 下單表單。
- **連線 + 帳戶**：`OpenSecTradeContext(host, port)`（host/port 預填 config.json futu section，
  可改）→ `get_acc_list()`。**模擬/實盤切換按鍵**（QButtonGroup exclusive，預設模擬）+
  市場過濾 combo — 全部係 **client-side filter**：帳戶表只顯示 **ACTIVE** 狀態、且符合
  當前環境 + 市場嘅帳戶；切換時所有資料（訂單/持倉/資金）跟住重新 query，**唔混模擬同實盤**。
- **解鎖（實盤）**：下單表單內置「交易解鎖密碼」欄 + 解鎖按鍵 — **只有 REAL 環境先顯示**（唔彈窗口）。
  正確方法係本條 gRPC 連線 `ctx.unlock_trade(password)`（連線級，非 per-account）— OpenD GUI 右上角
  解鎖只對佢自己嘅 session 有效，SDK 連線要各自 unlock_trade。REAL 下單未解鎖 → 自動用欄位密碼
  unlock、成功後先落單（pending chain）；unlock 失敗 → 唔落單 + 如實回報。密碼只存記憶體、永不持久化。
- **代碼模糊輸入**：code_edit 帶 QCompleter — 輸入 debounce 300ms → worker `stock_search`
  （OpenQuoteContext.get_stock_basicinfo，per-market cache；quote ctx 係獨立 gRPC 連線、lazy 建）→
  client-side contains match（code/name）→ 「MARKET.CODE」候選 + 名稱 hint label。
- **下單**：code / order type / price / qty / TIF + **買入/賣出兩鍵**（方向由按鍵決定）→
  `place_order`；REAL 帳戶先彈確認框、且要先解鎖。
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
import sys
from pathlib import Path
from string import Template

# ── standalone bootstrap：直接跑呢個檔時將 project root 放落 sys.path（package mode 下 no-op）──
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QStringListModel  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QButtonGroup, QComboBox, QCompleter, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

import gateway.theme as theme_mod  # noqa: E402 — module 引用（唔係 from-import，避免 stale value binding）
from gateway.i18n import DEFAULT_LANG, t  # noqa: E402

# config.json 路徑 — pathlib 跨平台（AGENTS.md：禁 hardcode 斜線）
CONFIG_PATH = Path(__file__).resolve().parents[2] / 'modules' / 'config.json'

# ── 表格顯示欄（對住真 OpenD 回傳核實過；card_num/uni_card_num 等敏感欄位刻意唔入表）──
ACC_COLS = ('acc_id', 'trd_env', 'acc_type', 'trdmarket_auth', 'acc_status')
ORDER_COLS = ('order_id', 'code', 'stock_name', 'trd_side', 'order_type', 'qty',
              'dealt_qty', 'price', 'dealt_avg_price', 'order_status', 'create_time')
POS_COLS = ('code', 'stock_name', 'position_market', 'qty', 'can_sell_qty', 'cost_price',
            'market_val', 'pl_val', 'pl_ratio', 'currency')
ACCINFO_KEYS = ('total_assets', 'cash', 'market_val', 'power', 'available_funds',
                'avl_withdrawal_cash')

# 下單表單選項（技術識別碼，三語同字 — 唔入 i18n）
MARKET_FILTERS = ('All', 'HK', 'US', 'HKFUND', 'USFUND')
ORDER_TYPES = ('NORMAL', 'MARKET', 'AUCTION_LIMIT', 'LIMIT_IF_TOUCHED')
TIF_OPTIONS = ('DAY', 'GTC', 'IOC')


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
        self._qctx = None          # OpenQuoteContext（代碼模糊搜尋用；lazy 建 — 獨立 gRPC 連線）
        self._stock_cache = {}     # (market, stock_type) → [(code, name)] — get_stock_basicinfo cache
        self._last_hostport = None # connect 時記低 — stock_search lazy 建 qctx 用
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
        for attr in ('_ctx', '_qctx'):   # trade ctx + quote ctx 一齊收（重連/斷線）
            c = getattr(self, attr)
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        self._stock_cache.clear()   # cache 跟連線走 — 重連後重新 fetch

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

    def _op_unlock(self, kw):
        err = self._need_ctx()
        if err:
            return err
        from futu import RET_OK
        ret, msg = self._ctx.unlock_trade(kw['password'])
        if ret != RET_OK:
            return {'ok': False, 'error': str(msg)}
        return {'ok': True}

    def _op_stock_search(self, kw):
        """代碼模糊搜尋 — OpenQuoteContext.get_stock_basicinfo（per-market cache）+ client-side contains。

        quote ctx 同 trade ctx 係兩條獨立 gRPC 連線（同一 OpenD host/port），lazy 建喺呢度；
        所有 op 都串行化喺同一條 worker thread → 唔會同 trade op 搶 SDK。
        """
        err = self._need_ctx()   # 要已連線先使到 qctx（host/port 由 connect 記低）
        if err:
            return err
        from futu import RET_OK, Market, SecurityType
        q = str(kw.get('query', '')).strip().upper()
        markets = kw.get('markets') or ['HK']
        stype = SecurityType.ETF if kw.get('fund') else SecurityType.STOCK
        results = []
        for m in markets:
            try:
                mkt_enum = getattr(Market, m)
            except AttributeError:
                continue
            cache_key = (m, str(stype))
            rows = self._stock_cache.get(cache_key)
            if rows is None:
                if self._qctx is None:
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
                ret, df = self._qctx.get_stock_basicinfo(mkt_enum, stype)
                if ret != RET_OK or not hasattr(df, 'columns'):
                    return {'ok': False, 'error': str(df)}
                rows = []
                for code, name in df[['code', 'name']].values.tolist():
                    rows.append((str(code), '' if name is None or name != name else str(name)))
                self._stock_cache[cache_key] = rows
            for code, name in rows:   # 模糊：contains（唔止 prefix）— code 或 name 命中都算
                if q and (q in code.upper() or q in name.upper()):
                    results.append({'code': f'{m}.{code}', 'name': name})
                    if len(results) >= 50:   # cap — completer 唔好一次塞幾百項
                        return {'ok': True, 'results': results}
        return {'ok': True, 'results': results}

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
            price=price, qty=int(kw['qty']), code=kw['code'], trd_side=side,
            order_type=otype, trd_env=kw['trd_env'], acc_id=int(kw['acc_id']),
            time_in_force=tif)
        if ret != RET_OK:
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
            return {'ok': False, 'error': str(msg)}
        return {'ok': True}

    def _op_cancel_all(self, kw):
        err = self._need_ctx()
        if err:
            return err
        from futu import RET_OK
        ret, msg = self._ctx.cancel_all_order(trd_env=kw['trd_env'], acc_id=int(kw['acc_id']))
        if ret != RET_OK:
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
    - REAL 帳戶嘅下單/撤單操作先彈 QMessageBox 確認 + 要先解鎖；SIMULATE 直接行。
    - 解鎖：下單表單內置密碼欄（只實盤顯示，唔彈窗口）→ 本連線 ctx.unlock_trade()
      （OpenD GUI 右上角解鎖對 SDK 連線無效 — 每條 gRPC 連線各自 unlock）；REAL 下單未解鎖
      會自動用欄位密碼 unlock、成功後先落單（pending chain）。密碼只存記憶體，永不寫入 config / 磁碟。
    - code_edit 模糊輸入：debounce → worker stock_search（OpenQuoteContext.get_stock_basicinfo
      per-market cache + client-side contains）→ QCompleter「MARKET.CODE」候選 + 名稱 hint。
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

        row1b = QHBoxLayout()
        self.connect_btn = QPushButton(t('trade_connect', DEFAULT_LANG))
        self.connect_btn.setObjectName('trade_connect_btn')
        self.connect_btn.setProperty('og', 'primarybtn')
        self.disconnect_btn = QPushButton(t('trade_disconnect', DEFAULT_LANG))
        self.disconnect_btn.setObjectName('trade_disconnect_btn')
        self.disconnect_btn.setProperty('og', 'actionbtn')
        self.disconnect_btn.setEnabled(False)
        row1b.addWidget(self.connect_btn, 1)
        row1b.addSpacing(8)
        row1b.addWidget(self.disconnect_btn, 1)
        cv.addLayout(row1b)
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
        # 模糊輸入：QCompleter（contains match）+ debounce timer → worker stock_search
        self._code_model = QStringListModel(self)
        self.code_completer = QCompleter(self._code_model, self)
        self.code_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.code_completer.setFilterMode(Qt.MatchContains)   # 模糊：contains（唔止 prefix）
        self.code_completer.setCompletionMode(QCompleter.PopupCompletion)
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
        self.tif_combo.addItems(TIF_OPTIONS)
        # 解鎖（只實盤顯示）— 內置欄位唔彈窗口；密碼經本連線 ctx.unlock_trade()
        self.unlock_lbl = self._form_label('trade_unlock_pwd')
        self.unlock_pwd_edit = QLineEdit()
        self.unlock_pwd_edit.setObjectName('trade_unlock_pwd')
        self.unlock_pwd_edit.setEchoMode(QLineEdit.Password)   # 密碼唔顯示明文
        self.unlock_btn = QPushButton(t('trade_unlock', DEFAULT_LANG))
        self.unlock_btn.setObjectName('trade_unlock_btn')
        self.unlock_btn.setProperty('og', 'actionbtn')
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
        grid.addWidget(self.unlock_lbl, 3, 0)
        grid.addWidget(self.unlock_pwd_edit, 3, 1, 1, 2)
        grid.addWidget(self.unlock_btn, 3, 3)
        ov.addLayout(grid)
        ov.addSpacing(6)

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
        self._connected = False
        self._unlocked = False
        self._pending_place = None  # REAL auto-unlock chain：unlock 成功 → 自動 submit 呢個 place_order kw
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

        def _on_code_text(_t):   # 每按一次：hint 即時更新 + debounce 搜尋
            self._update_code_hint()
            self._search_timer.start()

        self.otype_combo.currentTextChanged.connect(self._on_otype_changed)
        self.market_combo.currentTextChanged.connect(lambda _t: self._on_account_filter_changed())
        self.env_sim_btn.toggled.connect(lambda _c: self._on_account_filter_changed())
        self.env_real_btn.toggled.connect(lambda _c: self._on_account_filter_changed())
        self.acc_table.itemSelectionChanged.connect(self._on_acc_selected)
        self.connect_btn.clicked.connect(lambda: self._on_connect())
        self.disconnect_btn.clicked.connect(lambda: self._submit('disconnect'))
        self.unlock_btn.clicked.connect(self._on_unlock_click)
        self.code_edit.textChanged.connect(_on_code_text)
        self._search_timer.timeout.connect(self._on_code_search)
        self.code_completer.activated.connect(self._on_code_activated)
        self.buy_btn.clicked.connect(lambda: self._on_place('BUY'))
        self.sell_btn.clicked.connect(lambda: self._on_place('SELL'))
        self.orders_refresh_btn.clicked.connect(lambda: self._refresh('orders'))
        self.cancel_sel_btn.clicked.connect(self._on_cancel_selected)
        self.cancel_all_btn.clicked.connect(self._on_cancel_all)
        self.pos_refresh_btn.clicked.connect(lambda: self._refresh('positions'))

        self._set_unlock_visible(self.env_real_btn.isChecked())   # 預設 SIM → 解鎖欄隱藏
        self._load_config_defaults()
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
        host = self.host_edit.text().strip() or '127.0.0.1'
        port_text = self.port_edit.text().strip()
        try:
            int(port_text)
        except ValueError:
            self.conn_status_lbl.setText(t('conn_int_err', self._lang))
            return
        self.connect_btn.setEnabled(False)
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
        else:
            self._acc = None
            self.acc_sel_lbl.setStyleSheet('')
            self.acc_sel_lbl.setText(t('trade_no_account', self._lang))

    # ── 下單 / 解鎖 ─────────────────────────────────────────────
    def _on_otype_changed(self, otype):
        """MARKET 單唔使 price — disable 欄位防誤填。"""
        self.price_edit.setEnabled(otype != 'MARKET')

    def _set_unlock_visible(self, visible):
        """解鎖欄只實盤（REAL env）先顯示 — 模擬盤唔需要交易密碼。"""
        for w in (self.unlock_lbl, self.unlock_pwd_edit, self.unlock_btn, self.unlock_status_lbl):
            w.setVisible(visible)
        if not visible:
            self.unlock_status_lbl.setText('')

    def _on_unlock_click(self):
        """解鎖 — 本條 gRPC 連線級 ctx.unlock_trade()（密碼由內置欄位，唔彈窗口）。

        注意：OpenD GUI 右上角解鎖只對佢自己嘅 session 有效；SDK 每條連線要各自 unlock。
        """
        if not self._connected:
            self.unlock_status_lbl.setText(t('trade_need_account', self._lang))
            return
        pwd = self.unlock_pwd_edit.text()
        if not pwd:
            self.unlock_status_lbl.setText(t('trade_enter_pwd', self._lang))
            return
        self.unlock_btn.setEnabled(False)
        self.unlock_status_lbl.setText(t('trade_busy', self._lang))
        self._pending_place = None   # 手動解鎖 — 唔帶 pending order
        self._worker.submit('unlock', password=pwd)

    def _on_code_search(self):
        """code_edit debounce timeout → worker stock_search（模糊候選）。"""
        q = self.code_edit.text().strip()
        if not self._connected or len(q) < 1:
            self._code_model.setStringList([])
            self._update_code_hint()
            return
        if '.' in q:   # 已係完整 MARKET.CODE（completer 揀咗 / 用戶打晒）— 唔使再模糊搜尋，保留現有 hint
            return
        m = self.market_combo.currentText()
        markets, fund = {'All': (['HK', 'US', 'SH', 'SZ'], False),
                         'HK': (['HK'], False), 'US': (['US'], False),
                         'HKFUND': (['HK'], True), 'USFUND': (['US'], True)}.get(m, (['HK'], False))
        self._worker.submit('stock_search', query=q.upper(), markets=markets, fund=fund)

    def _on_code_activated(self, text):
        """completer 揀咗候選 → code_edit 填入 + hint 顯示名稱。"""
        self.code_edit.setText(text)
        self._update_code_hint()

    def _update_code_hint(self):
        """code_edit 內容命中搜尋結果 → hint label 顯示「CODE — name」。"""
        code = self.code_edit.text().strip()
        name = self._code_results.get(code.upper())
        self.code_hint_lbl.setText(f'{code} — {name}' if name else '')

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
        if not code:
            self.order_result_lbl.setText(t('trade_invalid_form', self._lang))
            return
        kw = dict(code=code, side=side, otype=otype, price=price,
                  qty=qty, tif=self.tif_combo.currentText())
        kw.update(self._acc_kw())
        confirm_text = t('trade_confirm_place', self._lang).format(
            code=code, side=kw['side'], qty=qty, price=_fmt(price), otype=otype, tif=kw['tif'])
        if not self._real_confirm(confirm_text):
            return
        # REAL 未解鎖 → 用內置欄位密碼自動 unlock，成功後先落單（pending chain）；
        # 正確方法係本連線 ctx.unlock_trade() — OpenD GUI 右上角解鎖對 SDK 連線無效。
        if str(self._acc.get('trd_env')) == 'REAL' and not self._unlocked:
            pwd = self.unlock_pwd_edit.text()
            if not pwd:
                self.order_result_lbl.setText(t('trade_need_unlock', self._lang))
                return
            self.buy_btn.setEnabled(False)   # 處理中兩鍵都 disable，防雙擊
            self.sell_btn.setEnabled(False)
            self.order_result_lbl.setText(t('trade_busy', self._lang))
            self._pending_place = kw         # unlock 成功 → _on_worker_result 自動落單
            self._worker.submit('unlock', password=pwd)
            return
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
            self.connect_btn.setEnabled(True)
            if ok:
                self._connected = True
                self._unlocked = False
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
                self.disconnect_btn.setEnabled(True)
            else:
                self.conn_status_lbl.setText(t('trade_conn_fail', self._lang).format(err=err_text))

        elif op == 'disconnect':
            self._connected = False
            self._unlocked = False
            self._pending_place = None
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
            self.disconnect_btn.setEnabled(False)
            self.unlock_pwd_edit.clear()   # 斷線清密碼（hygiene；本來就只存記憶體）
            self._code_model.setStringList([])
            self._code_results = {}
            self.code_hint_lbl.setText('')

        elif op == 'unlock':
            self.unlock_btn.setEnabled(True)
            pending = self._pending_place   # REAL 下單 auto-unlock chain 帶咗 place_order kw？
            self._pending_place = None
            if ok:
                self._unlocked = True
                self.unlock_status_lbl.setText(t('trade_unlock_ok', self._lang))
                if pending is not None:     # unlock 成功 → 自動落單（confirm 已喺 _on_place 彈過）
                    self._worker.submit('place_order', **pending)
            else:
                self.unlock_status_lbl.setText(
                    t('trade_unlock_fail', self._lang).format(err=err_text))
                if pending is not None:     # unlock 失敗 → 唔落單，還原按鍵 + 如實回報
                    self.buy_btn.setEnabled(True)
                    self.sell_btn.setEnabled(True)
                    self.order_result_lbl.setText(
                        t('trade_place_fail', self._lang).format(err=err_text))

        elif op == 'stock_search':
            if ok:   # 搜尋失敗唔打斷輸入 — 候選保持原狀，下次輸入再試（如實：靜默）
                results = result.get('results', [])
                self._code_results = {r['code'].upper(): r.get('name', '') for r in results}
                self._code_model.setStringList([r['code'] for r in results])
                self._update_code_hint()

        elif op == 'place_order':
            self.buy_btn.setEnabled(True)
            self.sell_btn.setEnabled(True)
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
            if ok:
                r = self.orders_table.currentRow()
                oid = _fmt(self._order_rows[r].get('order_id')) if 0 <= r < len(self._order_rows) else '?'
                self.orders_status_lbl.setText(t('trade_cancel_ok', self._lang).format(id=oid))
            else:
                self.orders_status_lbl.setText(
                    t('trade_op_fail', self._lang).format(err=err_text))

        elif op == 'cancel_all':
            self.cancel_all_btn.setEnabled(True)
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
        return str(result.get('error', '?'))

    def _fill_table(self, table, cols, rows):
        """rows（list of dict）→ QTableWidget（_fmt 格式化；缺欄位顯示空）。"""
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, col in enumerate(cols):
                item = QTableWidgetItem(_fmt(row.get(col)))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
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
        self.title_lbl.setText(t('page_futu_trade_title', lang))
        self.body_lbl.setText(t('page_futu_trade_body', lang))
        self.conn_title_lbl.setText(t('trade_conn_title', lang))
        # Host/Port 三語同字 — 建檔時已 set，唔使重設
        self.market_lbl.setText(t('trade_market', lang))
        self.connect_btn.setText(t('trade_connect', lang))
        self.disconnect_btn.setText(t('trade_disconnect', lang))
        self.env_sim_btn.setText(t('trade_env_sim', lang))
        self.env_real_btn.setText(t('trade_env_real', lang))
        self.unlock_lbl.setText(t('trade_unlock_pwd', lang))
        self.unlock_btn.setText(t('trade_unlock', lang))
        self.order_title_lbl.setText(t('trade_order_title', lang))
        self.code_lbl.setText(t('trade_code', lang))
        self.otype_lbl.setText(t('trade_otype', lang))
        self.price_lbl.setText(t('trade_price', lang))
        self.qty_lbl.setText(t('trade_qty', lang))
        self.tif_lbl.setText(t('trade_tif', lang))
        self.buy_btn.setText(t('trade_buy_btn', lang))
        self.sell_btn.setText(t('trade_sell_btn', lang))
        self.orders_title_lbl.setText(t('trade_orders_title', lang))
        self.orders_refresh_btn.setText(t('trade_refresh', lang))
        self.cancel_sel_btn.setText(t('trade_cancel_sel', lang))
        self.cancel_all_btn.setText(t('trade_cancel_all', lang))
        self.pos_title_lbl.setText(t('trade_pos_title', lang))
        self.pos_refresh_btn.setText(t('trade_refresh', lang))
        # 動態 label（acc_sel / status）跟住重譯一次
        if self._acc:
            self._on_acc_selected()
        else:
            self.acc_sel_lbl.setText(t('trade_no_account', lang))

    def _on_app_quit(self):
        """One Gate / standalone window 退出 → 斷 ctx（idempotent；worker finally 兜底）。"""
        if self._worker.isRunning():
            self._worker.stop_and_wait(5000)


if __name__ == '__main__':
    from gateway.pages.base_page import run_standalone
    run_standalone(FutuTradePage, 'page_futu_trade_title')

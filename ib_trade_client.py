# =====================================================================
# ib_trade_client.py — IB 交易客戶端：下單 / 撤單 / 持倉 / 訂單管理（NVDA 友善）
#
# 用法：
#   python ib_trade_client.py            # 開啟視窗（需 IB Gateway 跑在 127.0.0.1:4001）
#   python ib_trade_client.py --smoke    # 冒煙測試：自動開關視窗，不連線、不下單
#
# 設計說明：
# - 與 ib_gui_charts.py 同架構：背景 QThread 跑常駐 asyncio loop；GUI 執行緒用
#   call_soon_threadsafe / create_task 提交工作（下單/撤單/刷新），UI 不阻塞；
#   IB 事件（orderStatusEvent / openOrderEvent / positionEvent / accountSummaryEvent）
#   在 loop 執行緒觸發，經 Qt signal（排隊）送進 UI。
# - 連線用 ib_client.IBClient：斷線自動重連（線性退避）。ib 物件在重連時不變 →
#   事件只需掛一次；重連成功（connectedEvent）後自動重新拉取持倉/訂單快照。
# - 三個分頁：
#     * 持倉：reqPositionsAsync + positionEvent 即時更新（部位歸零的合約不顯示）。
#     * 訂單：reqAllOpenOrdersAsync（含 TWS / 其他 client 下的單）+ orderStatusEvent
#       / openOrderEvent 即時更新；選取列 → 「撤銷選取訂單」（有確認對話框）。
#     * 下單：代號/類型/方向/數量/訂單類型(MKT/LMT/STP/STPLIM)/TIF，送出前彈出
#       確認摘要（NVDA 會朗讀）；合約解析複用 IBClient._get_contract
#       （FUT 自動取最近到期月、CASH 走 IDEALPRO 外匯）。
# - ⚠️ 這是「真實下單」：確認後訂單真的送進 IB。clientId 每個 app 實例必須唯一
#   （與 ib_gui_charts.py 同時跑時請用不同 clientId，否則 Error 502）。
# - NVDA：所有控制項有 setAccessibleName；每頁上方有摘要列（StrongFocus，可被
#   焦點朗報）；訂單成交/撤銷、持倉變動會自動朗報（輸入框打字時不搶焦點）。
# =====================================================================

import asyncio
import contextlib
import os
import sys
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QThread, Signal, QTimer
from PySide6.QtGui import Qt, QAccessible
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QSpinBox, QComboBox, QPushButton, QCheckBox,
    QPlainTextEdit, QMessageBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView,
)

from ib_async import Order
from ib_async.util import UNSET_DOUBLE
from ib_client import IBClient

# ---- 選項清單 ----
SEC_TYPES = ['FUT', 'STK', 'CASH', 'CRYPTO']          # 預設 FUT（本帳號主要交易期貨）
ORDER_TYPES = [
    ('MKT', '市價 Market'),
    ('LMT', '限價 Limit'),
    ('STP', '停損 Stop'),
    ('STPLIM', '停損限價 Stop-Limit'),
]
TIFS = ['DAY', 'GTC', 'IOC']

# 帳號摘要列要顯示的 tag（accountSummaryAsync 會回傳更多，這裡只挑常用的）
ACCOUNT_TAGS = [
    ('NetLiquidation', '淨資產'),
    ('BuyingPower', '購買力'),
    ('ExcessLiquidity', '剩餘流動性'),
    ('AvailableFunds', '可用資金'),
]

# 訂單狀態 → 中文（NVDA 朗讀用；表格顯示中文，日誌保留原文）
STATUS_ZH = {
    'PendingSubmit': '待送出',
    'ApiPending': 'API待處理',
    'PreSubmitted': '預提交',
    'Submitted': '已送出',
    'ApiUpdate': 'API更新中',
    'ValidationError': '驗證錯誤',
    'Filled': '已全部成交',
    'Cancelled': '已撤銷',
    'ApiCancelled': 'API已撤銷',
    'PendingCancel': '待撤銷',
    'Inactive': '無效',
}


def _announce(text, target=None):
    """NVDA 即時朗讀。優先用 announceMessage（不搶焦點）；本機 PySide6 build
    未暴露該 API → 退回「聚焦摘要列」法，且使用者正在輸入框打字/選取時不搶焦點。"""
    try:
        QAccessible.announceMessage(text)
        return
    except Exception:
        pass
    if target is None:
        return
    fw = QApplication.focusWidget()
    if isinstance(fw, (QLineEdit, QComboBox)):   # 使用者正在輸入/選取 → 不搶焦點
        return
    target.setFocus()


def _f(v):
    """float 化 + UNSET_DOUBLE/NaN 護欄（ib_async 未設定的數值欄位是 DBL_MAX）"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    if v != v or v == UNSET_DOUBLE:      # NaN 或未設定 → 視為 0
        return 0.0
    return v


def _is_set(v):
    """ib_async 數值欄位是否「有設定」（未設定的預設值是 UNSET_DOUBLE=DBL_MAX）"""
    return v is not None and v != UNSET_DOUBLE


def _price_text(o):
    """訂單價格摘要：L=限價、S=停損（未設定不顯示）。📌 停損欄位是 auxPrice。"""
    parts = []
    if _is_set(o.lmtPrice):
        parts.append(f"L {float(o.lmtPrice):g}")
    if _is_set(o.auxPrice):
        parts.append(f"S {float(o.auxPrice):g}")
    return ' '.join(parts) if parts else '—'


def _fmt_expiry(s):
    """期貨到期月 '20261218' → '2026-12-18'"""
    s = str(s or '')
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


class _LogWriter:
    """把 print（IBClient / ib_async 的訊息）導進日誌 pane"""

    def __init__(self, engine):
        self._engine = engine

    def write(self, s):
        if s.strip():
            self._engine.sig_log.emit(s.rstrip('\n'))

    def flush(self):
        pass


class TradeEngine(QThread):
    """背景執行緒：常駐 asyncio loop + 單條 IBClient 連線（長駐，非一次性 session）"""

    sig_log = Signal(str)                 # 日誌文字
    sig_conn_state = Signal(str, str)     # (state, detail)；state: connecting/connected/disconnected
    sig_account = Signal(dict)            # {tag: (value_str, currency)}
    sig_positions = Signal(list)          # [dict] — 持倉快照（已過濾歸零）
    sig_orders = Signal(list)             # [dict] — 未平訂單快照
    sig_announce = Signal(str, str)       # (kind, text)；kind: 'order'/'position' → NVDA 朗報

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loop = None
        self._ready = threading.Event()
        self._client = None               # IBClient（連線中/已連線時非 None）
        self._connecting = False          # _session 進行中（防重複點擊產生雙連線）
        self._quiet = False               # 初始快照期間：IB 會重送全部訂單/持倉 → 不逐筆打日誌
        self._acct_cache = {}             # {tag: (value, currency)}
        self._initial_load_done = False   # 初始快照完成前不朗報（避免啟動時刷屏）

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in tasks:
                t.cancel()
            if tasks:
                try:
                    loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                except Exception:
                    pass
            loop.close()

    # ================= GUI 執行緒入口（都經 call_soon_threadsafe 進 loop）=================

    def connect(self, conn):
        """conn=(host, port, cid)。回傳 False = 已有連線（忽略重複點擊）。"""
        if not self._ready.wait(5) or self._loop is None:
            raise RuntimeError('async engine 尚未就緒')

        def _do():
            if self._client is not None or self._connecting:
                return   # 已在連線/已連線
            self.sig_conn_state.emit('connecting', '連線中...')
            self._loop.create_task(self._session(conn))

        self._loop.call_soon_threadsafe(_do)
        return True

    def disconnect(self):
        if not self._ready.wait(5) or self._loop is None:
            return

        def _do():
            self._loop.create_task(self._disconnect())

        self._loop.call_soon_threadsafe(_do)

    def place_order(self, p):
        """p = dict(symbol, sec_type, action, qty, order_type, limit_price, stop_price, tif, outside_rth)"""
        if not self._ready.wait(5) or self._loop is None:
            raise RuntimeError('async engine 尚未就緒')

        def _do():
            self._loop.create_task(self._place(p))

        self._loop.call_soon_threadsafe(_do)

    def cancel_order(self, client_id, order_id, perm_id):
        if not self._ready.wait(5) or self._loop is None:
            raise RuntimeError('async engine 尚未就緒')

        def _do():
            self._loop.create_task(self._cancel(client_id, order_id, perm_id))

        self._loop.call_soon_threadsafe(_do)

    # ================= loop 執行緒內的工作 =================

    async def _session(self, conn):
        """建立連線 → 掛事件 → 初始快照。失敗時 sig_conn_state('disconnected', err)。"""
        host, port, cid = conn
        writer = _LogWriter(self)
        self._connecting = True
        try:
            with contextlib.redirect_stdout(writer):
                print(f"\n{'=' * 64}\n▶️  連線 IB {host}:{port}（clientId={cid}）...")
                client = IBClient(host=host, port=port, client_id=cid)
                await client._ensure_connected()      # 建立 self.ib 並連線（自動重連已掛載）
                self._client = client
                ib = client.ib
                # 📌 ib 物件在 IBClient 自動重連時「不變」→ 事件只需掛一次。
                #    connectedEvent 在初始 connectAsync 結束時就發過（尚未掛上），
                #    之後每次「重連成功」才會觸發 _on_reconnected → 重新拉快照。
                ib.connectedEvent += self._on_reconnected
                ib.orderStatusEvent += self._on_order_status
                ib.openOrderEvent += self._on_open_order
                ib.positionEvent += self._on_position
                ib.accountSummaryEvent += self._on_account_value
                ib.errorEvent += self._on_error
                print(f"✅ 已連線（clientId={cid}）")
            self.sig_conn_state.emit('connected', f'已連線 clientId={cid}')
            await self._refresh_all()
        except Exception as e:
            # 已連上但後續步驟（掛事件/快照）出錯 → 主動斷開，避免留下孤兒連線
            if self._client is not None:
                try:
                    await self._client.close()
                except Exception:
                    pass
                self._client = None
            self.sig_log.emit(f"❌ 連線失敗：{e}")
            self.sig_conn_state.emit('disconnected', f'連線失敗：{e}')
        finally:
            self._connecting = False

    async def _disconnect(self):
        client = self._client
        if client is None:
            return
        writer = _LogWriter(self)
        try:
            with contextlib.redirect_stdout(writer):
                print("⏹ 正在斷開 IB 連線...")
                await client.close()
        except Exception as e:
            self.sig_log.emit(f"⚠️ 斷線時出錯：{e}")
        finally:
            self._client = None
            self._acct_cache = {}
            self._initial_load_done = False
            self.sig_account.emit({})
            self.sig_positions.emit([])
            self.sig_orders.emit([])
            self.sig_conn_state.emit('disconnected', '已斷線')

    async def _refresh_all(self):
        """拉取 訂單/持倉/帳號摘要 快照（初始載入與重連後都呼叫；冪等）"""
        client = self._client
        if client is None or not client.ib.isConnected():
            return
        ib = client.ib
        writer = _LogWriter(self)
        self._quiet = True      # 📌 快照期間 IB 會把既有訂單/持倉逐筆重送 → 事件回呼靜音，
                                #    避免啟動時日誌刷屏；最後統一 emit 完整快照
        try:
            with contextlib.redirect_stdout(writer):
                try:
                    await ib.reqAllOpenOrdersAsync()     # 含 TWS / 其他 client 的未平訂單
                except Exception as e:
                    self.sig_log.emit(f"⚠️ 拉取未平訂單失敗：{e}")
                try:
                    positions = await ib.reqPositionsAsync()
                    self._emit_positions(positions)
                except Exception as e:
                    self.sig_log.emit(f"⚠️ 拉取持倉失敗：{e}")
                try:
                    for v in await ib.accountSummaryAsync():
                        self._acct_cache[v.tag] = (v.value, v.currency)
                    self.sig_account.emit(dict(self._acct_cache))
                except Exception as e:
                    self.sig_log.emit(f"⚠️ 拉取帳號摘要失敗：{e}")
        finally:
            self._quiet = False
        self._initial_load_done = True
        self.sig_orders.emit(self._orders_snapshot())

    async def _place(self, p):
        """下單：解析合約 → 組 Order → placeOrder。全程日誌 + 結果朗報。"""
        client = self._client
        if client is None or not client.ib.isConnected():
            self.sig_log.emit("❌ 尚未連線，無法下單")
            return
        writer = _LogWriter(self)
        try:
            with contextlib.redirect_stdout(writer):
                contract, err = await client._get_contract(p['symbol'], p['sec_type'])
                if err:
                    self.sig_log.emit(err + f"（{p['symbol']} / {p['sec_type']}）")
                    return
                o = Order(
                    action=p['action'],
                    totalQuantity=float(p['qty']),
                    orderType=p['order_type'],
                    tif=p['tif'],
                )
                if p['order_type'] in ('LMT', 'STPLIM'):
                    o.lmtPrice = float(p['limit_price'])
                if p['order_type'] in ('STP', 'STPLIM'):
                    o.auxPrice = float(p['stop_price'])      # 📌 IB 停損價走 auxPrice
                if p.get('outside_rth'):
                    o.outsideRth = True
                trade = client.ib.placeOrder(contract, order=o)
                expiry = _fmt_expiry(getattr(contract, 'lastTradeDateOrContractMonth', ''))
                where = f"，到期 {expiry}" if contract.secType == 'FUT' else ''
                extra = ''
                if _is_set(o.lmtPrice):
                    extra += f" 限價={float(o.lmtPrice):g}"
                if _is_set(o.auxPrice):
                    extra += f" 停損={float(o.auxPrice):g}"
                msg = (f"✅ 已送出訂單 #{trade.order.orderId}：{p['action']} "
                       f"{float(p['qty']):g} {contract.symbol}（{contract.secType}, "
                       f"{contract.exchange or 'SMART'}{where}）類型={p['order_type']}{extra}")
                self.sig_log.emit(msg)
            self.sig_announce.emit('order', f"訂單已送出：{p['action']} {float(p['qty']):g} {contract.symbol}")
        except Exception as e:
            with contextlib.redirect_stdout(writer):
                traceback.print_exc()
            self.sig_log.emit(f"❌ 下單失敗：{e}")

    async def _cancel(self, client_id, order_id, perm_id):
        """撤單：由 (clientId, orderId, permId) 找到 Trade → cancelOrder"""
        client = self._client
        if client is None or not client.ib.isConnected():
            self.sig_log.emit("❌ 尚未連線，無法撤單")
            return
        ib = client.ib
        key = ib.wrapper.orderKey(client_id, order_id, perm_id)
        trade = ib.wrapper.trades.get(key)
        if trade is None:
            self.sig_log.emit(f"❌ 找不到訂單 #{order_id}（可能已成交/撤銷，或來自未同步的來源）")
            return
        try:
            t = ib.cancelOrder(trade.order)
            sym = trade.contract.symbol or '?'
            self.sig_log.emit(f"🗑️ 已請求撤單 #{order_id}（{sym}）→ 狀態 PendingCancel，等 IB 確認")
            if t is None:
                self.sig_log.emit("⚠️ cancelOrder 未找到對應 Trade（orderId 可能已被清除）")
        except Exception as e:
            self.sig_log.emit(f"❌ 撤單失敗：{e}")

    # ================= IB 事件回呼（都在 loop 執行緒觸發）=================

    def _on_reconnected(self):
        """IBClient 自動重連成功 → 重新拉快照（positions 不會被 IB 自動重送）"""
        self.sig_log.emit("🔁 已重新連線，正在重新拉取持倉/訂單快照...")
        asyncio.create_task(self._refresh_all())

    def _on_order_status(self, trade):
        if self._quiet or not self._initial_load_done:
            return      # 初始快照期間：由 _refresh_all 最後統一 emit
        st = trade.orderStatus.status
        sym = trade.contract.symbol or '?'
        oid = trade.order.orderId
        line = f"📋 訂單 #{oid}（{sym}）狀態 → {st}"
        if st == 'Filled':
            avgp = _f(trade.orderStatus.avgFillPrice)
            line += f"｜成交 {_f(trade.filled()):g}" + (f" @ {avgp:g}" if avgp else '')
        self.sig_log.emit(line)
        self.sig_orders.emit(self._orders_snapshot())
        # 重要狀態才朗報（初始載入期間已在上面擋掉，不會刷屏）
        if st in ('Filled', 'Cancelled', 'ApiCancelled'):
            self.sig_announce.emit('order', f"訂單 {oid}（{sym}）{STATUS_ZH.get(st, st)}")

    def _on_open_order(self, trade):
        """伺服器推來的未平訂單（例如在 TWS / 其他 client 下的單）"""
        if self._quiet or not self._initial_load_done:
            return
        o = trade.order
        self.sig_log.emit(
            f"📥 收到未平訂單 #{o.orderId}（{trade.contract.symbol or '?'}，"
            f"{o.action} {float(o.totalQuantity):g}，{o.orderType}，clientId={o.clientId}）")
        self.sig_orders.emit(self._orders_snapshot())

    def _on_position(self, pos):
        if self._quiet:
            return      # 初始快照由 reqPositionsAsync 回傳值處理
        sym = pos.contract.symbol or '?'
        if pos.position != 0:
            self.sig_log.emit(f"📈 持倉變動：{sym} → {float(pos.position):+g}"
                              f"（平均成本 {_f(pos.avgCost):g}）")
            if self._initial_load_done:
                self.sig_announce.emit('position', f"持倉變動：{sym} {float(pos.position):+g}")
        # 歸零也要刷新表格（把該列移除）
        client = self._client
        if client is not None and client.ib is not None:
            try:
                self._emit_positions(client.ib.positions())
            except Exception:
                pass

    def _on_account_value(self, v):
        self._acct_cache[v.tag] = (v.value, v.currency)
        if not self._quiet and self._initial_load_done:
            self.sig_account.emit(dict(self._acct_cache))

    def _on_error(self, req_id, code, msg, contract):
        # 交易相關錯誤（下單被拒 Error 102、撤單失敗等）reqId=orderId；全部顯示，交易者需要看到
        sym = getattr(contract, 'symbol', '') or ''
        self.sig_log.emit(f"⚠️ IB Error {code}" + (f"（{sym}）" if sym else '') + f": {msg}")

    # ================= 快照組裝 =================

    def _emit_positions(self, positions):
        rows = [self._pos_dict(p) for p in positions if float(p.position) != 0]
        rows.sort(key=lambda r: (r['secType'], r['symbol']))
        self.sig_positions.emit(rows)

    @staticmethod
    def _pos_dict(p):
        c = p.contract
        return {
            'symbol': c.symbol or '?',
            'secType': c.secType,
            'exchange': c.exchange or '',
            'expiry': _fmt_expiry(getattr(c, 'lastTradeDateOrContractMonth', '')),
            'position': float(p.position),
            'avgCost': _f(p.avgCost),
        }

    def _orders_snapshot(self):
        client = self._client
        if client is None:
            return []
        out = []
        for t in client.ib.openTrades():      # 只含「未平」訂單（DoneStates 已排除）
            o, c, st_ = t.order, t.contract, t.orderStatus
            out.append({
                'orderId': int(o.orderId),
                'permId': int(_f(st_.permId)),
                'clientId': int(o.clientId),
                'symbol': c.symbol or '?',
                'secType': c.secType,
                'action': o.action,
                'qty': _f(o.totalQuantity),
                'filled': _f(st_.filled),
                'remaining': _f(st_.remaining),
                'orderType': o.orderType,
                'price_text': _price_text(o),
                'tif': o.tif or '',
                'status': st_.status,
            })
        out.sort(key=lambda d: (d['clientId'], d['orderId']))
        return out

    # ================= 收尾 =================

    def shutdown(self):
        """關窗時呼叫：先關 IB 連線（若還在），再停 loop 並等執行緒結束"""
        if self._loop is not None:
            def _stop():
                client = self._client
                self._client = None

                async def _close_and_stop():
                    if client is not None:
                        try:
                            await client.close()
                        except Exception:
                            pass
                    self._loop.stop()

                self._loop.create_task(_close_and_stop())

            try:
                self._loop.call_soon_threadsafe(_stop)
            except RuntimeError:
                pass
        self.wait(3000)


# =====================================================================
# UI 元件
# =====================================================================

class PositionsPanel(QWidget):
    """持倉分頁：摘要列 + 表格"""

    HEADERS = ['代號', '類型', '交易所', '到期月', '部位', '平均成本']

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        self.summary = QLabel('持倉：尚無資料（先連線）')
        self.summary.setFocusPolicy(Qt.StrongFocus)      # NVDA 焦點朗報目標
        self.summary.setAccessibleName('持倉摘要')
        lay.addWidget(self.summary)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAccessibleName('持倉表格')
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i, w in enumerate((110, 70, 90, 100, 90, 110)):
            self.table.setColumnWidth(i, w)
        lay.addWidget(self.table)

    def set_rows(self, rows):
        n = len(rows)
        self.table.setRowCount(n)
        for i, r in enumerate(rows):
            vals = [r['symbol'], r['secType'], r['exchange'], r['expiry'],
                    f"{r['position']:+g}", (f"{r['avgCost']:g}" if r['avgCost'] else '—')]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if j >= 4:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, j, it)
        detail = ''
        if n:
            detail = '：' + '，'.join(f"{r['symbol']} {r['position']:+g}" for r in rows[:8])
            if n > 8:
                detail += f"…（共 {n} 筆）"
        self.summary.setText(f"持倉：共 {n} 筆{detail}")


class OrdersPanel(QWidget):
    """訂單分頁：摘要列 + 表格 + 撤單按鈕"""

    HEADERS = ['訂單#', 'PermID', 'Client', '代號', '方向', '數量', '已成交',
               '剩餘', '類型', '價格', 'TIF', '狀態']

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        self.summary = QLabel('訂單：尚無資料（先連線）')
        self.summary.setFocusPolicy(Qt.StrongFocus)
        self.summary.setAccessibleName('訂單摘要')
        lay.addWidget(self.summary)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAccessibleName('訂單表格')
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i, w in enumerate((70, 80, 60, 90, 70, 70, 70, 70, 70, 130, 50, 90)):
            self.table.setColumnWidth(i, w)
        lay.addWidget(self.table)

        row = QHBoxLayout()
        self.btn_cancel = QPushButton('🗑️ 撤銷選取訂單')
        self.btn_cancel.setAccessibleName('撤銷選取訂單按鈕')
        row.addWidget(self.btn_cancel)
        hint = QLabel('先點選表格中的一列，再按「撤銷選取訂單」（會彈出確認）。')
        hint.setAccessibleName('撤單說明')
        row.addWidget(hint, 1)
        lay.addLayout(row)

        self.rows = []          # 目前顯示的 [dict]（與表格列一一对應）

    def set_rows(self, rows):
        self.rows = list(rows)
        n = len(rows)
        self.table.setRowCount(n)
        for i, r in enumerate(rows):
            vals = [str(r['orderId']), str(r['permId']), str(r['clientId']), r['symbol'],
                    '買入' if r['action'] == 'BUY' else ('賣出' if r['action'] == 'SELL' else r['action']),
                    f"{r['qty']:g}", f"{r['filled']:g}", f"{r['remaining']:g}",
                    r['orderType'], r['price_text'], r['tif'], STATUS_ZH.get(r['status'], r['status'])]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if 5 <= j <= 7:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, j, it)
        detail = ''
        if n:
            detail = '：' + '，'.join(f"#{r['orderId']} {r['symbol']}" for r in rows[:8])
            if n > 8:
                detail += f"…（共 {n} 筆）"
        self.summary.setText(f"未平訂單：共 {n} 筆{detail}")

    def selected_order(self):
        """回傳選取列的 dict；沒選取回 None"""
        i = self.table.currentRow()
        if 0 <= i < len(self.rows):
            return self.rows[i]
        return None


class NewOrderForm(QWidget):
    """下單表單。validate() → (True, params_dict) 或 (False, 錯誤訊息)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        g = QGridLayout(self)

        self.sym_edit = QLineEdit('MNQ')
        self.sym_edit.setAccessibleName('下單代號')
        self.type_combo = QComboBox()
        self.type_combo.addItems(SEC_TYPES)
        self.type_combo.setCurrentText('FUT')
        self.type_combo.setAccessibleName('下單類型')

        self.side_combo = QComboBox()
        self.side_combo.addItem('買入 BUY', 'BUY')
        self.side_combo.addItem('賣出 SELL', 'SELL')
        self.side_combo.setAccessibleName('買賣方向')

        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 1_000_000_000)
        self.qty_spin.setValue(1)
        self.qty_spin.setAccessibleName('下單數量')

        self.ot_combo = QComboBox()
        for code, zh in ORDER_TYPES:
            self.ot_combo.addItem(f"{zh}（{code}）", code)
        self.ot_combo.setAccessibleName('訂單類型')

        self.tif_combo = QComboBox()
        self.tif_combo.addItems(TIFS)
        self.tif_combo.setCurrentText('DAY')
        self.tif_combo.setAccessibleName('有效期限 TIF')

        self.lmt_edit = QLineEdit('')
        self.lmt_edit.setPlaceholderText('限價（LMT / STPLIM 必填）')
        self.lmt_edit.setAccessibleName('限價價格')
        self.stp_edit = QLineEdit('')
        self.stp_edit.setPlaceholderText('停損價（STP / STPLIM 必填）')
        self.stp_edit.setAccessibleName('停損價格')

        self.rth_check = QCheckBox('允許盤外交易（Outside RTH，期貨夜盤用）')
        self.rth_check.setAccessibleName('盤外交易選項')

        g.addWidget(QLabel('代號：'), 0, 0)
        g.addWidget(self.sym_edit, 0, 1)
        g.addWidget(QLabel('類型：'), 0, 2)
        g.addWidget(self.type_combo, 0, 3)
        g.addWidget(QLabel('方向：'), 1, 0)
        g.addWidget(self.side_combo, 1, 1)
        g.addWidget(QLabel('數量：'), 1, 2)
        g.addWidget(self.qty_spin, 1, 3)
        g.addWidget(QLabel('訂單類型：'), 2, 0)
        g.addWidget(self.ot_combo, 2, 1)
        g.addWidget(QLabel('TIF：'), 2, 2)
        g.addWidget(self.tif_combo, 2, 3)
        g.addWidget(QLabel('限價：'), 3, 0)
        g.addWidget(self.lmt_edit, 3, 1)
        g.addWidget(QLabel('停損價：'), 3, 2)
        g.addWidget(self.stp_edit, 3, 3)
        g.addWidget(self.rth_check, 4, 0, 1, 4)

    def validate(self):
        sym = self.sym_edit.text().strip()
        if not sym:
            return False, '請輸入代號'
        ot = self.ot_combo.currentData()
        p = {
            'symbol': sym.upper(),
            'sec_type': self.type_combo.currentText(),
            'action': self.side_combo.currentData(),
            'qty': int(self.qty_spin.value()),
            'order_type': ot,
            'tif': self.tif_combo.currentText(),
            'outside_rth': bool(self.rth_check.isChecked()),
        }
        if ot in ('LMT', 'STPLIM'):
            try:
                p['limit_price'] = float(self.lmt_edit.text().strip())
                if p['limit_price'] <= 0:
                    return False, '限價必須大於 0'
            except ValueError:
                return False, f"限價不是數字：{self.lmt_edit.text()!r}"
        else:
            p['limit_price'] = 0.0
        if ot in ('STP', 'STPLIM'):
            try:
                p['stop_price'] = float(self.stp_edit.text().strip())
                if p['stop_price'] <= 0:
                    return False, '停損價必須大於 0'
            except ValueError:
                return False, f"停損價不是數字：{self.stp_edit.text()!r}"
        else:
            p['stop_price'] = 0.0
        return True, p

    def summary_text(self, p):
        ot_zh = dict(ORDER_TYPES).get(p['order_type'], p['order_type'])
        lines = [
            f"方向：{'買入' if p['action'] == 'BUY' else '賣出'}（{p['action']}）",
            f"代號：{p['symbol']}（{p['sec_type']}，期貨自動取最近到期月）",
            f"數量：{p['qty']:g}",
            f"類型：{ot_zh}（{p['order_type']}），TIF：{p['tif']}",
        ]
        if p['limit_price']:
            lines.append(f"限價：{p['limit_price']:g}")
        if p['stop_price']:
            lines.append(f"停損價：{p['stop_price']:g}")
        if p['outside_rth']:
            lines.append('盤外交易：是')
        return '\n'.join(lines)


class TradeWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('IB 交易客戶端 — 下單 / 撤單 / 持倉 / 訂單管理')
        self.resize(1080, 720)

        self.engine = TradeEngine(self)
        self._connected = False

        central = QWidget()
        self.setCentralWidget(central)      # 📌 必須：讓 win 擁有整個 UI 樹（否則 local var 出範圍 → C++ 被 GC）
        root = QVBoxLayout(central)

        # ---- 頂欄：連線控制 + 帳號摘要 ----
        top = QHBoxLayout()
        top.addWidget(QLabel('Host:'))
        self.host_edit = QLineEdit('127.0.0.1')
        self.host_edit.setFixedWidth(110)
        self.host_edit.setAccessibleName('主機位址')
        top.addWidget(self.host_edit)
        top.addWidget(QLabel('Port:'))
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(4001)
        self.port_spin.setAccessibleName('埠號')
        top.addWidget(self.port_spin)
        top.addWidget(QLabel('clientId:'))
        self.cid_spin = QSpinBox()
        self.cid_spin.setRange(1, 99999)
        self.cid_spin.setValue(200)
        self.cid_spin.setAccessibleName('客戶端編號')
        top.addWidget(self.cid_spin)

        self.btn_conn = QPushButton('🔌 連線')
        self.btn_conn.setAccessibleName('連線或斷線按鈕')
        self.btn_conn.clicked.connect(self._toggle_conn)
        top.addWidget(self.btn_conn)

        self.lbl_state = QLabel('未連線')
        self.lbl_state.setFocusPolicy(Qt.StrongFocus)
        self.lbl_state.setAccessibleName('連線狀態')
        top.addWidget(self.lbl_state)
        root.addLayout(top)

        acct_row = QHBoxLayout()
        self._acct_labels = {}
        for tag, zh in ACCOUNT_TAGS:
            lab = QLabel(f'{zh}：—')
            lab.setFocusPolicy(Qt.StrongFocus)
            lab.setAccessibleName(f'帳號{zh}')
            self._acct_labels[tag] = lab
            acct_row.addWidget(lab)
        acct_row.addStretch(1)
        root.addLayout(acct_row)

        # ---- 分頁：持倉 / 訂單 / 下單 ----
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName('主分頁')
        self.tab_pos = PositionsPanel()
        self.tab_ord = OrdersPanel()
        self.tab_new = NewOrderForm()
        self.tabs.addTab(self.tab_pos, '📈 持倉')
        self.tabs.addTab(self.tab_ord, '📋 訂單')
        self.tabs.addTab(self.tab_new, '✍️ 下單')
        root.addWidget(self.tabs, 1)

        # ---- 下單送出列（放在分頁下方，三個分頁共用）----
        submit_row = QHBoxLayout()
        self.btn_submit = QPushButton('📨 送出訂單（先確認）')
        self.btn_submit.setAccessibleName('送出訂單按鈕')
        self.btn_submit.setEnabled(False)
        self.btn_submit.clicked.connect(self._submit_order)
        submit_row.addWidget(self.btn_submit)
        warn = QLabel('⚠️ 真實下單：確認後訂單會送進 IB。clientId 每個 app 實例必須唯一。')
        warn.setAccessibleName('下單警告')
        submit_row.addWidget(warn, 1)
        root.addLayout(submit_row)

        # ---- 日誌 pane ----
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setAccessibleName('事件日誌')
        root.addWidget(self.log, 1)

        # ---- 引擎訊號接線（跨執行緒自動排隊）----
        self.engine.sig_log.connect(self._append_log)
        self.engine.sig_conn_state.connect(self._on_conn_state)
        self.engine.sig_account.connect(self._on_account)
        self.engine.sig_positions.connect(self.tab_pos.set_rows)
        self.engine.sig_orders.connect(self.tab_ord.set_rows)
        self.engine.sig_announce.connect(self._on_announce)
        self.tab_ord.btn_cancel.clicked.connect(self._cancel_selected)

        self.engine.start()

    # ================= 事件處理（GUI 執行緒）=================

    def _on_conn_state(self, state, detail):
        if state == 'connecting':
            self.lbl_state.setText('連線中...')
            self.btn_conn.setEnabled(False)
        elif state == 'connected':
            self._connected = True
            self.lbl_state.setText(detail)
            self.btn_conn.setText('⏹ 斷線')
            self.btn_conn.setEnabled(True)
            self.btn_submit.setEnabled(True)
            _announce(f'已連線：{detail}', target=self.lbl_state)
        else:   # disconnected
            self._connected = False
            self.lbl_state.setText(detail or '未連線')
            self.btn_conn.setText('🔌 連線')
            self.btn_conn.setEnabled(True)
            self.btn_submit.setEnabled(False)
            for tag, zh in ACCOUNT_TAGS:      # 清掉舊的帳號數字，避免斷線後誤看
                self._acct_labels[tag].setText(f'{zh}：—')

    def _on_account(self, d):
        for tag, zh in ACCOUNT_TAGS:
            if tag in d:
                val, cur = d[tag]
                self._acct_labels[tag].setText(f'{zh}：{val} {cur}'.strip())

    def _append_log(self, s):
        self.log.appendPlainText(s)

    def _on_announce(self, kind, text):
        """NVDA 朗報：訂單事件聚焦訂單摘要列、持倉事件聚焦持倉摘要列"""
        target = self.tab_ord.summary if kind == 'order' else self.tab_pos.summary
        _announce(text, target=target)

    # ================= 使用者動作 =================

    def _toggle_conn(self):
        if self._connected:
            r = QMessageBox.question(
                self, '斷線確認', '要斷開與 IB 的連線嗎？\n（未平訂單不會被撤銷，仍留在 IB）')
            if r == QMessageBox.StandardButton.Yes:
                self.engine.disconnect()
        else:
            conn = (self.host_edit.text().strip(), int(self.port_spin.value()),
                    int(self.cid_spin.value()))
            try:
                self.engine.connect(conn)
            except RuntimeError as e:
                QMessageBox.warning(self, '引擎未就緒', str(e))

    def _submit_order(self):
        ok, p = self.tab_new.validate()
        if not ok:
            QMessageBox.warning(self, '表單錯誤', str(p))
            return
        summary = self.tab_new.summary_text(p)
        r = QMessageBox.question(
            self, '確認下單（真實訂單）', f'請確認以下訂單內容：\n\n{summary}')
        if r == QMessageBox.StandardButton.Yes:
            try:
                self.engine.place_order(p)
            except RuntimeError as e:
                QMessageBox.warning(self, '引擎未就緒', str(e))

    def _cancel_selected(self):
        row = self.tab_ord.selected_order()
        if row is None:
            QMessageBox.information(self, '未選取', '請先在訂單表格中點選一列。')
            return
        r = QMessageBox.question(
            self, '撤單確認',
            f"要撤銷訂單 #{row['orderId']} 嗎？\n"
            f"{row['symbol']} {'買入' if row['action'] == 'BUY' else '賣出'} "
            f"{row['qty']:g}（{row['orderType']}，已成交 {row['filled']:g}）")
        if r == QMessageBox.StandardButton.Yes:
            try:
                self.engine.cancel_order(row['clientId'], row['orderId'], row['permId'])
            except RuntimeError as e:
                QMessageBox.warning(self, '引擎未就緒', str(e))

    def closeEvent(self, ev):
        self.engine.shutdown()
        super().closeEvent(ev)


def main():
    app = QApplication(sys.argv)
    win = TradeWindow()
    if '--smoke' in sys.argv:
        # 冒煙測試：不連線、不下單，只驗證 UI 能建立/關閉
        win.show()
        QTimer.singleShot(1000, app.quit)
        rc = app.exec()
        win.engine.shutdown()
        print(f"SMOKE OK (rc={rc})")
        sys.exit(rc if rc is not None else 0)
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()

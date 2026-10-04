# =====================================================================
# ib_gui_test.py — PySide6 GUI 測試控制台（測試 ib_test.py 的各函數）
#
# 用法：
#   python ib_gui_test.py            # 開啟視窗（需 IB Gateway 跑在 127.0.0.1:4001）
#   python ib_gui_test.py --smoke    # 冒煙測試：自動開關視窗，不需互動
#
# 設計說明：
# - 背景 QThread 內跑一個「常駐 asyncio event loop」；GUI 執行緒用
#   run_coroutine_threadsafe 提交協程 → UI 不會被阻塞。
# - ib_test.py 的 print 輸出（含 DataFrame）用 redirect_stdout 攔截，
#   經 Qt signal 送進日誌面板 → 不需要改動 ib_test.py 的列印邏輯。
# - 「停止」= cancel 目前 task：ib_client 各串流會自己接住 CancelledError、
#   清理訂閱並印 🛑，然後正常結束（不會殘留訂閱）。
# - 同一時間只允許跑一個測試（執行中其他按鍵停用），避免多條連線 clientId 衝突。
# =====================================================================

import asyncio
import contextlib
import os
import re
import sys
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QThread, Signal, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QLineEdit, QComboBox, QSpinBox, QPushButton,
    QPlainTextEdit, QMessageBox,
)

from ib_client import IBClient
from ib_test import (
    get_kline,
    get_kline_live,
    run_ticks_monitoring,
    run_order_flow_monitoring,
)

# ---- 參數選項（合法值清單見 ib_test.py 檔頭註解）----
SEC_TYPES = ['STK', 'FUT', 'CRYPTO', 'CASH']
DURATIONS = ['30 S', '1 D', '2 D', '5 D', '1 W', '1 M', '2 M', '3 M', '1 Y']
BARSIZES = [
    '1 sec', '5 secs', '15 secs', '30 secs',
    '1 min', '2 mins', '3 mins', '5 mins', '10 mins', '15 mins', '20 mins', '30 mins',
    '1 hour', '4 hours', '8 hours',
    '1 day', '1 week', '1 month',
]

# 多監控槽位可選的函數（ib_test.py 內的三個串流測試）
MONITOR_FNS = {
    'K線 Live': get_kline_live,
    '逐筆成交': run_ticks_monitoring,
    '訂單流 L2': run_order_flow_monitoring,
}

# ANSI 逸出序列（ib_test.py 的 \033[c 清屏指令在 GUI 裡沒意義，過濾掉）
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')


class _LogWriter:
    """redirect_stdout 目標：把 print 輸出轉發到 GUI 日誌面板"""

    def __init__(self, engine):
        self.engine = engine

    def write(self, s):
        if s:
            self.engine.sig_log.emit(s)

    def flush(self):
        pass


class AsyncEngine(QThread):
    """背景執行緒：常駐 asyncio event loop（整個 app 生命週期只有一個）"""

    sig_log = Signal(str)          # 日誌文字（可能含換行）
    sig_state = Signal(bool, str)  # (是否執行中, 測試名稱)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loop = None
        self._task = None
        self._stop_requested = False
        self._ready = threading.Event()

    # ---- QThread.run：把 loop 跑到底 ----
    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            # 收尾：取消所有殘留 task，讓 ib_client 的 finally 清理（🛑）有機會跑完
            tasks = asyncio.all_tasks(loop)
            for t in tasks:
                t.cancel()
            if tasks:
                try:
                    loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                except Exception:
                    pass
            loop.close()

    def submit(self, label, coro):
        """GUI 執行緒提交一個協程（coro 是已建立的 coroutine object）"""
        if not self._ready.wait(5) or self._loop is None:
            raise RuntimeError('async engine 尚未就緒')
        asyncio.run_coroutine_threadsafe(self._run(label, coro), self._loop)

    async def _run(self, label, coro):
        self._task = asyncio.current_task()
        self._stop_requested = False   # 每次新測試重置，避免上一次的 Stop 污染本次結果訊息
        writer = _LogWriter(self)
        try:
            with contextlib.redirect_stdout(writer):
                print(f"\n{'=' * 64}\n▶️  開始測試：{label}")
                await coro
                if self._stop_requested:
                    print("⏹ 已依使用者要求停止")
                else:
                    print("✅ 測試結束")
        except asyncio.CancelledError:
            with contextlib.redirect_stdout(writer):
                print("\n⏹ 已被使用者取消（Stop）")
            raise
        except Exception:
            with contextlib.redirect_stdout(writer):
                traceback.print_exc()
        finally:
            self._task = None
            self.sig_state.emit(False, '')

    def stop(self):
        """取消目前測試（GUI 執行緒呼叫安全）；沒有執行中的 task 時不做任何事"""
        if self._loop is not None and self._task is not None:
            self._stop_requested = True
            self._loop.call_soon_threadsafe(self._task.cancel)

    @property
    def running(self):
        return self._task is not None

    def shutdown(self):
        """關窗時呼叫：停 loop 並等執行緒結束"""
        if self._loop is not None:
            def _stop():
                t = self._task
                if t is not None:
                    t.cancel()
                self._loop.stop()
            try:
                self._loop.call_soon_threadsafe(_stop)
            except RuntimeError:
                pass
        self.wait(3000)


class _MonitorSlot(QWidget):
    """多監控的一個槽位：函數類型 + 類型 + 代號"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.fn_combo = QComboBox()
        self.fn_combo.addItems(list(MONITOR_FNS.keys()))
        self.sec_combo = QComboBox()
        self.sec_combo.addItems(SEC_TYPES)
        self.sym_edit = QLineEdit()
        self.sym_edit.setPlaceholderText('e.g. MNQ / 0700 / USDJPY')
        lay.addWidget(QLabel('函數'))
        lay.addWidget(self.fn_combo, 1)
        lay.addWidget(QLabel('類型'))
        lay.addWidget(self.sec_combo)
        lay.addWidget(QLabel('代號'))
        lay.addWidget(self.sym_edit, 1)

    def spec(self, p):
        """回傳 (函數, kwargs)；p 是全域參數 dict（幣別/Duration/BarSize/筆數/檔數）"""
        name = self.fn_combo.currentText()
        kw = {
            'security_type': self.sec_combo.currentText(),
            'symbol': self.sym_edit.text().strip(),
            'currency': p['currency'],
        }
        if name == 'K線 Live':
            kw['durationStr'] = p['durationStr']
            kw['barSizeSetting'] = p['barSizeSetting']
        elif name == '逐筆成交':
            kw['max_records'] = p['max_records']
        else:  # 訂單流 L2
            kw['rows'] = p['rows']
        return MONITOR_FNS[name], kw


class IBTestWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('IB 測試控制台 — ib_test.py')
        self.resize(1100, 760)

        # ---- async engine（背景執行緒 + 常駐 event loop）----
        self.engine = AsyncEngine(self)
        self.engine.sig_log.connect(self._append_log)
        self.engine.sig_state.connect(self._on_state)
        self.engine.start()
        self._busy = False   # GUI 端防雙擊護欄（sig_state 是跨執行緒排隊信號，有微小延遲）

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ---------- 連線設定 ----------
        conn_box = QGroupBox('連線設定')
        conn_lay = QHBoxLayout(conn_box)
        self.host_edit = QLineEdit('127.0.0.1')
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(4001)
        self.cid_spin = QSpinBox()
        self.cid_spin.setRange(1, 9999)
        self.cid_spin.setValue(200)   # 📌 與 ib_test.py 的 CLIENT_ID 一致；同時跑其他腳本請改
        conn_lay.addWidget(QLabel('Host'))
        conn_lay.addWidget(self.host_edit)
        conn_lay.addWidget(QLabel('Port'))
        conn_lay.addWidget(self.port_spin)
        conn_lay.addWidget(QLabel('clientId'))
        conn_lay.addWidget(self.cid_spin)
        conn_lay.addStretch(1)
        root.addWidget(conn_box)

        # ---------- 市場參數（單測與多監控共用）----------
        mkt_box = QGroupBox('市場參數')
        grid = QGridLayout(mkt_box)
        self.sec_combo = QComboBox()
        self.sec_combo.addItems(SEC_TYPES)
        self.symbol_edit = QLineEdit('MNQ')
        self.symbol_edit.setPlaceholderText('e.g. MNQ / NVDA / 0700 / USDJPY / BTC')
        self.currency_edit = QLineEdit('USD')
        self.currency_edit.setFixedWidth(80)
        self.dur_combo = QComboBox()
        self.dur_combo.setEditable(True)   # 可選可輸入（合法值見 ib_test.py 檔頭）
        self.dur_combo.addItems(DURATIONS)
        self.dur_combo.setCurrentText('1 D')
        self.bar_combo = QComboBox()
        self.bar_combo.setEditable(True)
        self.bar_combo.addItems(BARSIZES)
        self.bar_combo.setCurrentText('15 mins')
        self.maxrec_spin = QSpinBox()      # 逐筆成交：保留筆數
        self.maxrec_spin.setRange(2, 1000)
        self.maxrec_spin.setValue(50)
        self.rows_spin = QSpinBox()        # 訂單流：L2 檔數
        self.rows_spin.setRange(1, 40)
        self.rows_spin.setValue(5)

        grid.addWidget(QLabel('類型'), 0, 0)
        grid.addWidget(self.sec_combo, 0, 1)
        grid.addWidget(QLabel('代號'), 0, 2)
        grid.addWidget(self.symbol_edit, 0, 3, 1, 2)
        grid.addWidget(QLabel('幣別'), 0, 5)
        grid.addWidget(self.currency_edit, 0, 6)
        grid.addWidget(QLabel('Duration'), 1, 0)
        grid.addWidget(self.dur_combo, 1, 1)
        grid.addWidget(QLabel('BarSize'), 1, 2)
        grid.addWidget(self.bar_combo, 1, 3)
        grid.addWidget(QLabel('Tick筆數'), 1, 4)
        grid.addWidget(self.maxrec_spin, 1, 5)
        grid.addWidget(QLabel('L2檔數'), 1, 6)
        grid.addWidget(self.rows_spin, 1, 7)
        grid.setColumnStretch(3, 1)
        root.addWidget(mkt_box)

        # ---------- 測試按鍵（每個 ib_test.py 函數一個）----------
        btn_row = QHBoxLayout()
        self.btn_kline = QPushButton('拉取 K線')
        self.btn_kline_live = QPushButton('K線 Live 串流')
        self.btn_ticks = QPushButton('逐筆成交串流')
        self.btn_depth = QPushButton('訂單流 L2')
        self.stop_btn = QPushButton('⏹ 停止目前測試')
        self.stop_btn.setStyleSheet(
            'QPushButton { background-color: #c0392b; color: white; font-weight: bold; }'
            'QPushButton:hover { background-color: #a93226; }'
            'QPushButton:disabled { background-color: #d5dbdb; color: #7f8c8d; }')
        for b in (self.btn_kline, self.btn_kline_live, self.btn_ticks, self.btn_depth):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        btn_row.addWidget(self.stop_btn)
        root.addLayout(btn_row)

        # ---------- 多監控（單一連線並行）----------
        multi_box = QGroupBox('多監控（共用同一條連線、並行；K線用上方 Duration/BarSize，逐筆/Tick筆數、L2/檔數同理）')
        mlay = QVBoxLayout(multi_box)
        self.slot1 = _MonitorSlot()
        self.slot2 = _MonitorSlot()
        self.slot2.fn_combo.setCurrentText('訂單流 L2')
        mlay.addWidget(self.slot1)
        mlay.addWidget(self.slot2)
        mb_row = QHBoxLayout()
        self.btn_multi = QPushButton('啟動多監控')
        mb_row.addStretch(1)
        mb_row.addWidget(self.btn_multi)
        mlay.addLayout(mb_row)
        root.addWidget(multi_box)

        # ---------- 輸出日誌 ----------
        log_box = QGroupBox('輸出日誌')
        llay = QVBoxLayout(log_box)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(12000)   # 上限防記憶體無限成長
        f = QFont('Consolas', 9)
        f.setStyleHint(QFont.TypeWriter)
        self.log.setFont(f)
        clear_btn = QPushButton('清除日誌')
        clear_btn.clicked.connect(self.log.clear)
        top_row = QHBoxLayout()
        top_row.addStretch(1)
        top_row.addWidget(clear_btn)
        llay.addLayout(top_row)
        llay.addWidget(self.log, 1)
        root.addWidget(log_box, 3)

        self.setCentralWidget(central)
        self.statusBar().showMessage('待命')

        # ---- 接線 ----
        # 傳「工廠」（未呼叫）；_start_single 驗證參數通過後才呼叫它建立協程
        self.btn_kline.clicked.connect(lambda: self._start_single('拉取 K線', self._go_kline))
        self.btn_kline_live.clicked.connect(lambda: self._start_single('K線 Live 串流', self._go_kline_live))
        self.btn_ticks.clicked.connect(lambda: self._start_single('逐筆成交串流', self._go_ticks))
        self.btn_depth.clicked.connect(lambda: self._start_single('訂單流 L2', self._go_depth))
        self.stop_btn.clicked.connect(self.engine.stop)
        self.btn_multi.clicked.connect(self._start_multi)

    # ================= 參數收集 =================
    def _params(self):
        return {
            'security_type': self.sec_combo.currentText(),
            'symbol': self.symbol_edit.text().strip(),
            'currency': self.currency_edit.text().strip() or 'USD',
            'durationStr': self.dur_combo.currentText().strip() or '1 D',
            'barSizeSetting': self.bar_combo.currentText().strip() or '15 mins',
            'max_records': self.maxrec_spin.value(),
            'rows': self.rows_spin.value(),
        }

    def _make_client(self):
        return IBClient(
            host=self.host_edit.text().strip() or '127.0.0.1',
            port=int(self.port_spin.value()),
            client_id=int(self.cid_spin.value()),
        )

    # ================= 各測試的協程工廠（傳入 ib= 共用 GUI 建立的連線）=================
    def _go_kline(self):
        p = self._params()
        async def go():
            # IBClient 的連線發生在 __aenter__，所以要用 async with 包住
            async with self._make_client() as client:
                await get_kline(security_type=p['security_type'], symbol=p['symbol'],
                                durationStr=p['durationStr'], barSizeSetting=p['barSizeSetting'],
                                currency=p['currency'], ib=client)
        return go()   # 回傳協程（coroutine object）

    def _go_kline_live(self):
        p = self._params()
        async def go():
            async with self._make_client() as client:
                await get_kline_live(security_type=p['security_type'], symbol=p['symbol'],
                                     durationStr=p['durationStr'], barSizeSetting=p['barSizeSetting'],
                                     currency=p['currency'], ib=client)
        return go()   # 回傳協程（coroutine object）

    def _go_ticks(self):
        p = self._params()
        async def go():
            async with self._make_client() as client:
                await run_ticks_monitoring(security_type=p['security_type'], symbol=p['symbol'],
                                           max_records=p['max_records'], currency=p['currency'], ib=client)
        return go()   # 回傳協程（coroutine object）

    def _go_depth(self):
        p = self._params()
        async def go():
            async with self._make_client() as client:
                await run_order_flow_monitoring(security_type=p['security_type'], symbol=p['symbol'],
                                                rows=p['rows'], currency=p['currency'], ib=client)
        return go()   # 回傳協程（coroutine object）

    # ================= 啟動 / 狀態 =================
    def _start_single(self, label, coro_factory):
        p = self._params()
        if not p['symbol']:
            QMessageBox.warning(self, '缺少參數', '請先輸入代號（Symbol）')
            return
        if self._busy:
            return
        self._busy = True
        full_label = f"{label} — {p['security_type']}:{p['symbol']}"
        # 📌 立即啟用「停止」按鈕（GUI 執行緒直接做，不等跨執行緒信號）；
        #    engine 只在測試結束時發 sig_state(False)，若只靠它，Stop 會永遠是灰的
        self.stop_btn.setEnabled(True)
        self.statusBar().showMessage(f'執行中：{full_label}')
        try:
            self.engine.submit(full_label, coro_factory())   # 驗證通過才建立協程（避免 never-awaited）
        except RuntimeError as e:
            self._busy = False
            self.stop_btn.setEnabled(False)
            QMessageBox.critical(self, '引擎錯誤', str(e))

    def _start_multi(self):
        p = self._params()
        specs = []
        for i, slot in enumerate((self.slot1, self.slot2), 1):
            if not slot.sym_edit.text().strip():
                QMessageBox.warning(self, '缺少參數', f'監控 {i} 未輸入代號')
                return
            specs.append(slot.spec(p))
        if self._busy:
            return
        self._busy = True
        # 同 _start_single：立即啟用「停止」按鈕
        self.stop_btn.setEnabled(True)

        async def go():
            # 與 ib_test.run_multi_monitor 相同邏輯，但 host/port/clientId 可參數化
            async with self._make_client() as client:
                await asyncio.gather(*(fn(ib=client, **kw) for fn, kw in specs))

        names = ', '.join(f"{kw['security_type']}:{kw['symbol']}" for _fn, kw in specs)
        self.statusBar().showMessage(f'執行中：多監控（共用連線）— {names}')
        self.engine.submit(f'多監控（共用連線）— {names}', go())

    def _on_state(self, running, name):
        self._busy = running
        for b in (self.btn_kline, self.btn_kline_live, self.btn_ticks, self.btn_depth, self.btn_multi):
            b.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.statusBar().showMessage(f'執行中：{name}' if running else '待命')

    def _append_log(self, s):
        s = _ANSI_RE.sub('', s)   # 過濾 \033[c 等 ANSI 序列（GUI 裡清屏無意義）
        self.log.insertPlainText(s)
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def closeEvent(self, ev):
        self.engine.shutdown()
        ev.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    win = IBTestWindow()
    if '--smoke' in sys.argv:
        QTimer.singleShot(2500, app.quit)   # 冒煙測試：2.5 秒後自動關閉
    win.show()
    sys.exit(app.exec())

"""綜合測試 GUI（矩陣）：一行一個測試，三欄 [測試 | 參數 | 結果]，全部 broker × 標的類別 × 方式 × 市場。

- 無輸入欄位：參數拆多欄顯示（code / market / ktype / num / broker / method）
- Broker 分區：每個 broker 一段，段首有橫跨全欄嘅 header row（setSpan），futu / ib 一眼分清
- 右上角「隱藏 EXPECTED FAIL」toggle：只影響顯示（setRowHidden），Run All 仍然跑全部 rows
- 每行可獨立跑（▶ 或雙擊該行）；Run All = **全部同時並發**（futu 各開獨立 OpenD 連線；IB 共用一條 TWS session、多 reqId 並行，per-request error 歸屬靠 bars.reqId）
- Stop = cancel 所有運行中 + 清理所有結果（重新開始）
- 成功嘅行：「▸ 數據」按鈕拆開顯示返回嘅 K 線（mini table，最後 50 rows、按時間反向排序 — 最新 bar 喺第一行，可 scroll）
- 結果語義：✅ PASS / ⚠️ EXPECTED FAIL（預期嘅誠實失敗，例如無 permission）/ ❌ FAIL / ℹ️ 未驗證（只顯示實際結果）
- stream 行持續 live：第一次 yield = 歷史 baseline（收到 → 即刻出 verdict ✅ PASS），之後 live tick 持續更新結果格 + 數據表，到 Stop / 重跑為止（0 tick 唔算 fail — 可能冇成交時段 / IB 無 RTUS）
- worker thread 擁有一個 app-lifetime BrokerClient；closeEvent 釋放 IB clientId=99

Run: python gateway/pages/gui_fulltest.py   （需要 OpenD + TWS/IB Gateway 開緊）
"""
import asyncio
import logging
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 本檔喺 gateway/pages/
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHBoxLayout, QLabel, QMainWindow,
                               QHeaderView, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from modules import BrokerClient   # 🤖 registry：config 入面所有 broker（futu/ib）自動 instantiate，lazy connect

KLINE_NUM = 1000
TICK_UI_INTERVAL = 0.25   # 🤖 live tick 數據表 repaint throttle（秒）；label 文字每 tick 都更新

# ── 測試矩陣 ────────────────────────────────────────────────────────────────
# expect: True=應該成功 / False=預期誠實失敗（message 必須非空，可加 must_contain 斷言）/ None=未驗證，只顯示實際結果
TESTS = [
    # ── FUTU ──
    dict(broker='futu', method='get_kline', code='US.NVDA', ktype='K_1M',
         name='美股 · 歷史 K 線', intro='NVDA 1 分鐘 × 1000 bars；contract test 已驗證 PASS', expect=True),
    dict(broker='futu', method='stream_kline', code='US.NVDA', ktype='K_1M',
         name='美股 · 串流 K 線', intro='持續 live：第一次 yield = 歷史 baseline（收到即 PASS），之後 live tick 持續更新到 Stop', expect=True),
    dict(broker='futu', method='get_kline', code='HK.00700', ktype='K_1M',
         name='港股 · 歷史 K 線', intro='騰訊 1 分鐘 × 1000 bars；probe 實測 rows=100', expect=True),
    dict(broker='futu', method='stream_kline', code='HK.00700', ktype='K_1M',
         name='港股 · 串流 K 線', intro='持續 live（probe 實測 baseline + tick 有流）；baseline 收到即 PASS，live_updates 計到 Stop', expect=True),
    dict(broker='futu', method='get_kline', code='US.NQmain', ktype='K_1M',
         name='美股期貨主連 · 歷史 K 線', intro='NQmain → front month；本 account 無美股期貨行情權限（預期誠實失敗）', expect=False, must_contain='行情权限不足'),
    dict(broker='futu', method='stream_kline', code='US.NQmain', ktype='K_1M',
         name='美股期貨主連 · 串流 K 線', intro='訂閱應該即刻 fail-fast 報權限錯誤（預期誠實失敗）', expect=False, must_contain='行情权限不足'),
    dict(broker='futu', method='get_kline', code='HK.HSI', ktype='K_1M',
         name='港股指數 · 歷史 K 線', intro='HSI 係指數；Futu 歷史 K 線唔接受呢個 code（預期誠實失敗）', expect=False, must_contain='未知股票'),
    dict(broker='futu', method='stream_kline', code='HK.HSI', ktype='K_1M',
         name='港股指數 · 串流 K 線', intro='同上：訂閱回未知股票（預期誠實失敗）', expect=False, must_contain='未知股票'),
    dict(broker='futu', method='get_kline', code='HK.HSImain', ktype='K_1M',
         name='港股指數期貨主連 · 歷史 K 線', intro='HSI front month 期貨；canonical code，P7 已驗證 rows=1000', expect=True),
    dict(broker='futu', method='stream_kline', code='HK.HSImain', ktype='K_1M',
         name='港股指數期貨主連 · 串流 K 線', intro='持續 live（probe 實測 baseline=yes）；baseline 收到即 PASS，live_updates 計到 Stop（冇成交時段可以係 0）', expect=True),
    dict(broker='futu', method='get_kline', code='US.NONEXIST123', ktype='K_1M',
         name='唔存在代號 · 誠實失敗路徑', intro='應該返 status=False + 明確 message，唔好 hang 或 crash', expect=False),
    # ── IB ──
    dict(broker='ib', method='get_kline', code='US.NVDA', ktype='K_1M',
         name='美股 · 歷史 K 線 (IB)', intro='reqHistoricalData 1 分鐘 × 1000 bars；contract test 已驗證 PASS', expect=True),
    dict(broker='ib', method='stream_kline', code='US.NVDA', ktype='K_1M',
         name='美股 · 串流 K 線 (IB)', intro='持續 live；本 account 無 RTUS → live_updates 可以長期係 0，baseline 收到即 PASS', expect=True),
    dict(broker='ib', method='get_kline', code='HK.00700', ktype='K_1M',
         name='港股 · 歷史 K 線 (IB)', intro='SEHK 00700；probe 實測 rows=100', expect=True),
    dict(broker='ib', method='get_kline', code='US.NQmain', ktype='K_1M',
         name='美股期貨主連 · 歷史 K 線 (IB)', intro='NQ:FUT → reqContractDetails 解析 front month（probe: CME 20261218）先取 history；實測 rows=100', expect=True),
    dict(broker='ib', method='get_kline', code='HK.HSI', ktype='K_1M',
         name='港股指數 · 歷史 K 線 (IB)', intro='本 account 無 HSI permission：全 venue Error 200（P7 probe），預期誠實失敗', expect=False, must_contain='無法解析'),
    dict(broker='ib', method='get_kline', code='HK.HSImain', ktype='K_1M',
         name='港股指數期貨主連 · 歷史 K 線 (IB)', intro='無 HK derivatives permission → 無法解析 + 最後錯誤 hint；contract test case', expect=False, must_contain='無法解析'),
    dict(broker='ib', method='get_kline', code='US.NONEXIST123', ktype='K_1M',
         name='唔存在代號 · 誠實失敗路徑 (IB)', intro='Error 200 = 唔存在或無 permission；message 應該帶 TWS 原文 hint', expect=False, must_contain='無法解析'),
]

# ── Broker 分區：table 入面每個 broker 一段，段首有橫跨全欄嘅 header row（setSpan）──
BROKER_SECTIONS = [
    ('futu', 'FUTU · 富途 OpenD'),
    ('ib',   'IB · Interactive Brokers (TWS/Gateway)'),
]

# state → (顯示 label, 顏色)：expected_fail 係橙色（預期內嘅誠實失敗），fail 先係紅色
STATE_STYLE = {
    'pass':          ("✅ PASS", QColor(0, 130, 0)),
    'expected_fail': ("⚠️ EXPECTED FAIL", QColor(204, 120, 0)),
    'fail':          ("❌ FAIL", QColor(200, 0, 0)),
    'info_ok':       ("ℹ️ OK (未驗證)", QColor(0, 90, 170)),
    'info_fail':     ("ℹ️ FAIL (未驗證)", QColor(96, 96, 96)),
}


# ─────────────────────── asyncio ↔ Qt 橋（worker thread） ───────────────────────

class TestWorker(QObject):
    """住喺 worker thread；只發 signal，絕不碰 widget。

    Run All = 全部同時並發：每行一個 task（futu 各開獨立 OpenD 連線；IB 共用一條 TWS session、
    多 reqId 並行 — ib_client per-request error 歸屬靠 bars.reqId，_connect_lock 防雙重連線）。
    Stop = cancel 所有運行中 row/consume task（broker cleanup 經 finally 完成）+ 作廢所有結果。
    """

    row_started = Signal(int)
    row_done = Signal(int, object)   # (idx, {'state', 'detail'[, 'data'][, 'tick_token']})
    stream_tick = Signal(int, int, object)   # 🤖 (idx, tick_token, {'n', 'df'}) — live tick 持續更新（自動 queue 去 GUI thread）
    run_finished = Signal(object)    # summary dict（本 batch 全部行完成先 emit）
    stopped = Signal()               # Stop cleanup 完成（GUI 再清一次，catch 任何 late paint）

    def __init__(self, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self._loop = loop
        self._client = None            # BrokerClient（lazy，喺呢個 loop 上建立）
        self._client_lock = asyncio.Lock()   # 🤖 並發首調 ensure_client 序列化 — IB clientId=99 只可以有一條連線
        self._row_tasks: set[asyncio.Task] = set()      # 🤖 Run All = 每行一個 task 同時跑
        self._consume_tasks: set[asyncio.Task] = set()  # stream consume tasks（多條 stream 可並發）
        self._live_tasks: dict[int, asyncio.Task] = {}  # 🤖 idx → live stream consume task（持續到 Stop / 重跑該行）
        self._next_tick_token = 0    # 🤖 monotonic tick token：GUI 用嚟作廢舊 run 嘅 stale tick
        self._shutting_down = False
        self._pending = 0              # 未完成 row 數 → 歸零先 emit run_finished
        self._results: dict[int, dict] = {}

    async def ensure_client(self):
        async with self._client_lock:   # 🤖 並發首調喺呢度排隊，connect 完先放行（IB clientId=99 exclusivity）
            if self._client is None:
                self._client = BrokerClient()
                await self._client.__aenter__()
        return self._client

    # --- GUI-thread 入口 --------------------------------------------------------
    def enqueue(self, indices):
        try:
            asyncio.run_coroutine_threadsafe(self._enqueue(list(indices)), self._loop)
        except RuntimeError as e:  # loop 剛好停咗
            logging.warning("schedule rejected (loop not running): %s", e)

    def request_stop(self):
        try:
            asyncio.run_coroutine_threadsafe(self._do_stop(), self._loop)
        except RuntimeError as e:  # loop 剛好停咗
            logging.warning("stop rejected (loop not running): %s", e)

    async def _do_stop(self):
        await self._cancel_all()   # 🤖 cancel 所有運行中 row + consume task，等 broker cleanup 完成先算數
        self._pending = 0          # 🤖 cancelled row 唔會行 _run_row 嘅 decrement — 直接重置
        self._results.clear()      # 🤖 Stop = 作廢所有結果（GUI 同步清理 table）
        self.stopped.emit()

    async def _cancel_all(self):
        tasks = list(self._row_tasks | self._consume_tasks)
        for t in tasks:
            if not t.done():
                t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)   # 🤖 等 broker 端清理（finally）完成

    async def _enqueue(self, indices):
        for i in indices:
            self._pending += 1
            t = asyncio.create_task(self._run_row(i))   # 🤖 即刻開跑 — Run All 全部同時並發
            self._row_tasks.add(t)
            t.add_done_callback(self._row_tasks.discard)

    # --- per-row runner（每行一個 task，並行） ------------------------------------
    async def _run_row(self, idx):
        try:
            result = await self._run_one(idx)
        except asyncio.CancelledError:
            raise   # 🤖 Stop/shutdown cancel — 唔 emit row_done（GUI 已清理）
        except Exception as e:   # 🤖 雙重保險：_run_one 已經 catch，呢度只係防漏
            result = {'state': 'fail', 'detail': f"{type(e).__name__}: {e}"}
        self._results[idx] = result
        self.row_done.emit(idx, result)
        self._pending -= 1
        if self._pending == 0:   # 🤖 本 batch 全部行完成（並發完成順序任意，計數歸零先報 summary）
            self.run_finished.emit(self._summary())

    async def _run_one(self, idx):
        spec = TESTS[idx]
        self.row_started.emit(idx)
        data = None   # 🤖 成功返回嘅 K 線 df（GUI 拆疊顯示用）；失敗行保持 None
        tick_token = None   # 🤖 live stream row 嘅 token（GUI 驗證後續 tick 用）
        try:
            client = await self.ensure_client()
            if spec['method'] == 'get_kline':
                status, df, message = await client.get_kline(
                    code=spec['code'], ktype=spec['ktype'], broker=spec['broker'], kline_num=KLINE_NUM)
                if status and df is not None and len(df) > 0:
                    ok, detail = True, f"rows={len(df)}  last_tk={df['time_key'].iloc[-1]}"
                    data = df
                else:
                    ok, detail = False, str(message) if message else "status=False 但冇返錯誤 message"
            else:   # stream_kline
                status, gen, message = await client.stream_kline(
                    code=spec['code'], ktype=spec['ktype'], broker=spec['broker'], kline_num=KLINE_NUM)
                if not status or gen is None:
                    ok, detail = False, str(message) if message else "stream 啟動失敗但冇返錯誤 message"
                else:
                    tick_token, first_df = await self._start_live(idx, gen)   # 🤖 baseline 收到 → 即刻出 verdict；live tick 背景持續
                    ok, detail = True, "snapshots=1  live_updates=0（live 串流中…）"
                    data = first_df    # 🤖 baseline df 俾拆疊 table 顯示；後續 tick 經 stream_tick signal 更新
        except asyncio.CancelledError:
            raise   # 🤖 保留取消語義：shutdown / Stop 先可以正確結束
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"

        expect = spec['expect']
        mc = spec.get('must_contain')
        if expect is True:
            state = 'pass' if ok else 'fail'
        elif expect is False:
            if not ok and detail and (mc is None or mc in detail):
                state = 'expected_fail'   # 誠實失敗 + message 符合預期 → 橙色，唔係 bug
            elif not ok:
                state = 'fail'
                hint = f"  [期望不符：message 應該包含 {mc!r}]" if mc else "  [期望不符：應該有非空錯誤 message]"
                detail += hint
            else:
                state, detail = 'fail', "UNEXPECTED OK — 呢個測試預期誠實失敗（檢查 expectation 係咪過時）"
        else:   # None = 未驗證 → 只顯示實際結果，唔判斷對錯
            state = 'info_ok' if ok else 'info_fail'
        res = {'state': state, 'detail': detail}
        if data is not None:
            res['data'] = data
        if tick_token is not None:
            res['tick_token'] = tick_token   # 🤖 GUI 記錄做 live tick 驗證（作廢舊 run 嘅 stale tick）
        return res

    async def _start_live(self, idx: int, gen):
        """持續 live consume：第一次 yield = baseline（等佢到先 return），之後每 tick emit stream_tick。

        task 持續跑到 Stop / shutdown cancel 或重跑同一行；token 俾 GUI 作廢舊 run 嘅 stale tick。
        """
        old = self._live_tasks.get(idx)   # 🤖 重跑同一行：先 cancel 舊 live task 並等佢 broker cleanup 完成
        if old is not None and not old.done():
            old.cancel()
            await asyncio.gather(old, return_exceptions=True)   # cancelled task 嘅 CancelledError 被收集，唔會 propagate

        token = self._next_tick_token
        self._next_tick_token += 1

        first_df = None
        ready = asyncio.Event()
        n = 0

        async def consume():
            nonlocal first_df, n
            try:
                async for df in gen:
                    n += 1
                    if n == 1:
                        first_df = df     # 🤖 baseline — 喚醒等待者
                        ready.set()
                    else:
                        self.stream_tick.emit(idx, token, {'n': n, 'df': df})   # 🤖 live tick → GUI（自動 queue）
            except asyncio.CancelledError:
                raise

        task = asyncio.create_task(consume())
        self._consume_tasks.add(task)     # 🤖 Stop / shutdown 嘅 _cancel_all 一樣覆蓋到
        self._live_tasks[idx] = task

        def _cleanup(t, idx=idx):
            self._consume_tasks.discard(t)
            if self._live_tasks.get(idx) is t:   # 🤖 只移除仍然係當前嗰個（新 run 可能已取代）
                del self._live_tasks[idx]

        task.add_done_callback(_cleanup)
        try:
            await asyncio.wait_for(ready.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)   # 等 broker 端清理完成
            raise TimeoutError("setup 成功但 30s 內冇收到 baseline") from None
        except asyncio.CancelledError:   # 🤖 Stop 喺等 baseline 期間到 → 連新開嘅 consume task 都要 cancel（防 OpenD 連線漏）
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        return token, first_df

    def _summary(self):
        s = {'pass': 0, 'expected_fail': 0, 'fail': 0, 'info': 0}
        for r in self._results.values():
            st = r['state']
            if st == 'pass':
                s['pass'] += 1
            elif st == 'expected_fail':
                s['expected_fail'] += 1
            elif st == 'fail':
                s['fail'] += 1
            else:
                s['info'] += 1
        return s

    async def shutdown(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        await self._cancel_all()   # 🤖 同 Stop：cancel 所有 row + consume task，等 broker cleanup 完成
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)   # 釋放 IB clientId 99（如用過）
            except Exception as e:
                logging.warning("BrokerClient cleanup failed: %s", e)
            self._client = None
        if self._loop.is_running():
            self._loop.stop()


class LoopThread(QThread):
    """擁有 event loop + TestWorker；GUI thread 嘅 facade。（pattern 同 gui_kline.py）"""

    worker_ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: TestWorker | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_requested = False

    @property
    def worker(self) -> TestWorker | None:
        return self._worker

    # --- QThread body（跑喺 worker thread） ------------------------------------
    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        # Worker 喺呢度建立 → signal affinity == 呢個 thread → emit 自動 queue 去 GUI
        self._worker = TestWorker(loop)
        try:
            self.worker_ready.emit(self._worker)
            if self._stop_requested:
                loop.run_until_complete(self._worker.shutdown())
                return
            loop.run_forever()
        finally:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                try:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
            loop.close()

    # --- GUI-thread facade ------------------------------------------------------
    def request_shutdown(self):
        """GUI-thread 入口；pair with wait()。"""
        deadline = time.monotonic() + 2.0
        while True:
            w, loop = self._worker, self._loop
            if w is not None and loop is not None and loop.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(w.shutdown(), loop)
                except RuntimeError as e:
                    logging.warning("request_shutdown rejected: %s", e)
                return
            if not self.isRunning():
                return
            self._stop_requested = True  # run() 會喺入 run_forever 前檢查
            if time.monotonic() >= deadline:
                logging.warning("request_shutdown timed out waiting for the bridge")
                return
            time.sleep(0.01)


# ─────────────────────── GUI ───────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Broker 綜合測試矩陣（{len(TESTS)} tests）")
        self._worker: TestWorker | None = None
        self._inflight: set[int] = set()
        self._row_btns: list[QPushButton] = []
        self._res_labels: list[QLabel] = []      # 🤖 col 2 cell widget：結果文字
        self._data_btns: list[QPushButton] = []   # 「▸ 數據」拆疊按鈕（成功行先顯示）
        self._data_tables: list[QTableWidget] = []  # mini K 線 table（最後 50 rows）
        self._row_state: dict[int, str] = {}         # 🤖 每行最新 state（live tick repaint 用對應顏色）
        self._tick_gen: dict[int, int] = {}          # 🤖 idx → 當前 live stream token（作廢舊 run / Stop 後嘅 stale tick）
        self._last_tick_paint: dict[int, float] = {}  # 🤖 數據表 repaint throttle 時間戳（TICK_UI_INTERVAL）

        central = QWidget(self)
        v = QVBoxLayout(central)

        # ── toolbar：Run All / Stop + 進度 + summary（無任何輸入欄位）──
        bar = QHBoxLayout()
        self.run_all_btn = QPushButton("▶ Run All")
        self.run_all_btn.setObjectName('run_all')
        self.stop_btn = QPushButton("⏹ Stop")
        self.stop_btn.setObjectName('stop')
        self.progress_lbl = QLabel("等待 worker thread…")
        self.progress_lbl.setObjectName('progress')
        self.summary_lbl = QLabel("")
        self.summary_lbl.setObjectName('summary')
        for b in (self.run_all_btn, self.stop_btn):
            b.setEnabled(False)   # worker ready 先啟用（防 race）
        self.run_all_btn.clicked.connect(self.run_all)   # 🤖 漏咗 connect = 按鈕死（用戶實測 bug）
        self.stop_btn.clicked.connect(self.stop)
        bar.addWidget(self.run_all_btn)
        bar.addWidget(self.stop_btn)
        bar.addStretch(1)
        bar.addWidget(self.progress_lbl)
        bar.addWidget(self.summary_lbl)
        self.hide_ef_btn = QPushButton("隱藏 EXPECTED FAIL")
        self.hide_ef_btn.setObjectName('hide_expected_fail')
        self.hide_ef_btn.setCheckable(True)   # 🤖 display-only toggle：setRowHidden，唔影響 Run All 跑全部 rows
        self.hide_ef_btn.toggled.connect(self._on_toggle_hide_ef)
        bar.addWidget(self.hide_ef_btn)
        v.addLayout(bar)

        # ── 八欄 table + broker 分區 header rows：測試 | code | market | ktype | num | broker | method | 結果 ──
        self._test_to_row = [None] * len(TESTS)   # 🤖 test idx → table row（中間插咗 section headers）
        self._row_to_test = {}                    # 🤖 table row → test idx（header rows 唔喺入面）
        self.table = QTableWidget(len(TESTS) + len(BROKER_SECTIONS), 8)
        self.table.setHorizontalHeaderLabels(["測試", "code", "market", "ktype", "num", "broker", "method", "結果"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        for c in range(1, 7):   # 🤖 參數欄 auto-fit 內容（純短文字）
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(7, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 340)
        r = 0
        for broker_key, title in BROKER_SECTIONS:   # 🤖 每段：header row（setSpan 橫跨全欄）+ 該 broker 嘅 test rows
            self._build_section_header(r, broker_key, title)
            r += 1
            for i, spec in enumerate(TESTS):
                if spec['broker'] != broker_key:
                    continue
                self._test_to_row[i] = r
                self._row_to_test[r] = i
                self._build_test_row(r, i, spec)
                r += 1
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)   # 🤖 雙擊 test row = 獨立測試（header row ignore）
        v.addWidget(self.table)
        self.setCentralWidget(central)

        self.hide_ef_btn.setChecked(True)   # 🤖 用戶要求：默認隱藏 EXPECTED FAIL（toggled → 連文字反轉）

        self.thread = LoopThread(self)
        self.thread.worker_ready.connect(self._on_worker_ready)
        self.thread.start()

    def _build_section_header(self, r: int, broker_key: str, title: str):
        """Broker 分區 header row：橫跨全欄（setSpan），一眼分清 futu / ib 兩段。"""
        n = sum(1 for s in TESTS if s['broker'] == broker_key)
        it = QTableWidgetItem(f"▍{title} — {n} tests")
        f = it.font()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 2)
        it.setFont(f)
        bg = '#2f4858' if broker_key == 'futu' else '#5d4178'   # 🤖 深色系：淺/暗 table background 都清晰
        it.setBackground(QColor(bg))
        it.setForeground(QColor(255, 255, 255))
        self.table.setItem(r, 0, it)
        self.table.setSpan(r, 0, 1, 8)   # 🤖 橫跨全部 8 欄
        self.table.setRowHeight(r, 34)

    def _build_test_row(self, r: int, i: int, spec: dict):
        """r = table row（計入 section headers），i = test idx（TESTS 順序，signal 用）。"""
        # col 0：▶ 按鈕 + 名稱/簡介（cell widget）
        cell = QWidget()
        h = QHBoxLayout(cell)
        h.setContentsMargins(6, 3, 6, 3)
        h.setSpacing(8)
        btn = QPushButton("▶")
        btn.setFixedWidth(34)
        btn.setEnabled(False)   # 🤖 用戶要求：未開始（worker 未 ready）要灰 — _sync_run_controls 統一放行
        btn.clicked.connect(lambda checked=False, idx=i: self.run_row(idx))
        lbl = QLabel(f"<b>{spec['broker']} · {spec['name']}</b><br>"
                     f"<span style='color:#777'>{spec['intro']}</span>")
        lbl.setTextFormat(Qt.RichText)
        h.addWidget(btn)
        h.addWidget(lbl, 1)
        self.table.setCellWidget(r, 0, cell)
        self._row_btns.append(btn)

        # cols 1-6：參數拆多欄（純顯示文字；stream window 資訊喺 col 0 intro 已有）
        market = spec['code'].split('.')[0]
        for c, val in enumerate((spec['code'], market, spec['ktype'], str(KLINE_NUM),
                                 spec['broker'], spec['method']), start=1):
            it = QTableWidgetItem(val)
            self.table.setItem(r, c, it)

        # col 7：結果 cell widget = [結果文字] + [▸ 數據拆疊按鈕] + [mini K 線 table]
        rcell = QWidget()
        rv = QVBoxLayout(rcell)
        rv.setContentsMargins(6, 3, 6, 3)
        rv.setSpacing(4)
        res_lbl = QLabel("—")
        res_lbl.setTextFormat(Qt.PlainText)
        res_lbl.setWordWrap(True)
        data_btn = QPushButton("▸ 數據")
        data_btn.setVisible(False)   # 🤖 成功返回數據先顯示
        data_btn.clicked.connect(lambda checked=False, idx=i: self._toggle_data(idx))
        data_tbl = QTableWidget(0, 0)
        data_tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        data_tbl.setMaximumHeight(180)   # 🤖 mini table：內部 scroll，唔好無限撐高 row
        data_tbl.setVisible(False)
        rv.addWidget(res_lbl)
        rv.addWidget(data_btn, 0, Qt.AlignLeft)
        rv.addWidget(data_tbl)
        self.table.setCellWidget(r, 7, rcell)
        self._res_labels.append(res_lbl)
        self._data_btns.append(data_btn)
        self._data_tables.append(data_tbl)

    # --- worker bridge ----------------------------------------------------------
    def _sync_run_controls(self):
        """🤖 用戶要求：未開始要灰 — 按鈕狀態機（唯一出入口）：
        worker 未 ready = 全部灰；idle = Run All / 每行 ▶ 亮、Stop 灰；
        運行中 = Stop 亮、Run All 灰（防重入）、運行中嗰行 ▶ 灰。"""
        ready = self._worker is not None
        running = bool(self._inflight)
        self.run_all_btn.setEnabled(ready and not running)
        self.stop_btn.setEnabled(running)
        for i, b in enumerate(self._row_btns):
            b.setEnabled(ready and i not in self._inflight)

    def _on_worker_ready(self, worker: TestWorker):
        self._worker = worker
        self._sync_run_controls()
        self.progress_lbl.setText("Ready")
        worker.row_started.connect(self._on_row_started)
        worker.row_done.connect(self._on_row_done)
        worker.stream_tick.connect(self._on_stream_tick)   # 🤖 live tick → 即時更新結果格 + 數據表
        worker.run_finished.connect(self._on_run_finished)
        worker.stopped.connect(self._on_stopped)   # 🤖 Stop cleanup 完成 → 再清一次（catch late paint）

    # --- 觸發 -------------------------------------------------------------------
    def run_row(self, i: int):
        if self._worker is None or i in self._inflight:
            return
        self._inflight.add(i)
        self._sync_run_controls()
        self.progress_lbl.setText(f"運行中 {len(self._inflight)} 個測試…")
        self._worker.enqueue([i])

    def run_all(self):
        idxs = [i for i in range(len(TESTS)) if i not in self._inflight]
        if not idxs:
            return
        self._inflight.update(idxs)
        self._sync_run_controls()
        self.progress_lbl.setText(f"全測 {len(idxs)} 個測試…")
        self._worker.enqueue(idxs)

    def _on_cell_double_clicked(self, r: int, _c: int):
        i = self._row_to_test.get(r)   # 🤖 section header row 冇 mapping → ignore
        if i is not None:
            self.run_row(i)

    def _on_toggle_hide_ef(self, hide: bool):
        """右上角 toggle：隱藏/顯示 EXPECTED FAIL rows（display-only — Run All 仍然跑全部）。"""
        for i, spec in enumerate(TESTS):
            if spec['expect'] is False:
                self.table.setRowHidden(self._test_to_row[i], hide)
        self.hide_ef_btn.setText("顯示 EXPECTED FAIL" if hide else "隱藏 EXPECTED FAIL")

    def stop(self):
        if self._worker is not None:
            self._worker.request_stop()   # 🤖 cancel 所有運行中 row/consume task（worker 端）
            self.progress_lbl.setText("停止中…")
            self._clear_results()         # 🤖 GUI 即刻清理；stopped signal 完成時再清一次

    def _on_stopped(self):
        """Worker 確認 cleanup 完成 — 再清一次，catch 任何喺途中 paint 咗嘅 late row_done。"""
        self._clear_results()
        self.progress_lbl.setText("已停止（結果已清理）")

    def _clear_results(self):
        for i in range(len(TESTS)):
            lbl = self._res_labels[i]
            lbl.setText("—")
            lbl.setStyleSheet("")
            self._data_btns[i].setVisible(False)
            self._data_tables[i].setVisible(False)
            self._row_btns[i].setEnabled(True)
        self._inflight.clear()
        self._sync_run_controls()   # 🤖 Stop/清理 → 返 idle 態（Run All 亮、Stop 灰）
        self._tick_gen.clear()   # 🤖 Stop / 清理後任何 stale live tick 到都俾 token guard 丟（唔會 repaint 已清咗嘅 label）
        self.summary_lbl.setText("")

    # --- 結果繪製 -----------------------------------------------------------------
    def _on_row_started(self, i: int):
        self._row_btns[i].setEnabled(False)
        lbl = self._res_labels[i]
        lbl.setText("⏳ 運行中…")
        lbl.setStyleSheet("color: rgb(0, 100, 200);")
        self._data_btns[i].setVisible(False)   # 🤖 重跑時先收埋舊數據，完成後再決定顯示
        self._data_tables[i].setVisible(False)

    def _on_row_done(self, i: int, res: dict):
        self._inflight.discard(i)
        self._sync_run_controls()   # 🤖 全部完成 → Run All 返亮、Stop 返灰
        self._row_btns[i].setEnabled(True)
        if 'tick_token' in res:   # 🤖 live stream row：記錄 token 俾後續 tick 驗證（舊 run 嘅 tick 作廢）
            self._tick_gen[i] = res['tick_token']
        self._apply_state(i, res)
        remaining = len(self._inflight)
        self.progress_lbl.setText("Idle" if not remaining else f"運行中 {remaining} 個測試…")

    def _apply_state(self, i: int, res: dict):
        self._row_state[i] = res['state']   # 🤖 live tick repaint 讀呢度取對應 state 顏色
        label, color = STATE_STYLE[res['state']]
        detail = res['detail'] if len(res['detail']) <= 300 else res['detail'][:300] + "…"
        lbl = self._res_labels[i]
        lbl.setText(f"{label}\n{detail}")
        lbl.setStyleSheet(f"color: rgb({color.red()}, {color.green()}, {color.blue()});")
        df = res.get('data')
        btn, tbl = self._data_btns[i], self._data_tables[i]
        if df is not None and len(df) > 0:
            self._fill_data_table(i, df)
            btn.setText(f"▸ 數據（{len(df)} rows）")
            btn.setVisible(True)   # 🤖 成功返回數據 → 拆疊按鈕出現喺結果下方
        else:
            btn.setVisible(False)
            tbl.setVisible(False)
        self.table.resizeRowsToContents()

    def _fill_data_table(self, i: int, df):
        """mini table：最後 50 rows、按時間反向排序（最新 bar 喺第一行），內部 scroll。"""
        show = df.tail(50).iloc[::-1].reset_index(drop=True)   # 🤖 newest first
        tbl = self._data_tables[i]
        tbl.clear()
        cols = [str(c) for c in show.columns]
        tbl.setColumnCount(len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setRowCount(len(show))
        for r, (_, row) in enumerate(show.iterrows()):
            for c, colname in enumerate(show.columns):
                tbl.setItem(r, c, QTableWidgetItem(str(row[colname])))
        tbl.horizontalHeader().setStretchLastSection(True)

    def _toggle_data(self, i: int):
        """▸/▾ 拆疊：顯示 / 隱藏該行嘅 K 線 mini table。"""
        btn, tbl = self._data_btns[i], self._data_tables[i]
        if tbl.isVisible():
            tbl.setVisible(False)
            n = tbl.rowCount()
            btn.setText(f"▸ 數據（{n} rows）")
        else:
            tbl.setVisible(True)
            btn.setText("▾ 隱藏數據")
        self.table.resizeRowsToContents()

    def _on_stream_tick(self, i: int, token: int, payload: dict):
        """Live tick（worker emit → 自動 queue 去 GUI thread）：即時更新結果格 + 數據表。

        - token 驗證：只接受該行當前 run 嘅 tick（Stop / 重跑後嘅 stale tick 直接丟）
        - label 文字每 tick 都更新；數據表 repaint throttle 喺 TICK_UI_INTERVAL（4 Hz）
        """
        if self._tick_gen.get(i) != token:   # 🤖 stale tick → 直接丟
            return
        n = payload['n']
        label, color = STATE_STYLE[self._row_state.get(i, 'pass')]
        lbl = self._res_labels[i]
        lbl.setText(f"{label}\nsnapshots={n}  live_updates={max(0, n - 1)}（live 串流中…）")
        lbl.setStyleSheet(f"color: rgb({color.red()}, {color.green()}, {color.blue()});")
        now = time.monotonic()
        if now - self._last_tick_paint.get(i, 0.0) >= TICK_UI_INTERVAL:   # 🤖 throttle：數據表最多每秒 repaint 4 次
            self._last_tick_paint[i] = now
            df = payload['df']
            self._fill_data_table(i, df)
            if not self._data_tables[i].isVisible():   # 🤖 table 展開緊就唔好覆蓋「▾ 隱藏數據」文字
                self._data_btns[i].setText(f"▸ 數據（{len(df)} rows）")

    def _on_run_finished(self, summary: dict):
        s = summary
        self.summary_lbl.setText(
            f"✅ {s['pass']}   ⚠️ {s['expected_fail']}   ❌ {s['fail']}   ℹ️ {s['info']}")

    # --- 收檔 ---------------------------------------------------------------------
    def closeEvent(self, e):
        self.thread.request_shutdown()
        self.thread.wait(3000)   # 等 loop stop（shutdown 會先釋放 client）
        e.accept()


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.showMaximized()   # 🤖 全屏大小（用戶可以還原做視窗）
    sys.exit(app.exec())


if __name__ == '__main__':
    main()

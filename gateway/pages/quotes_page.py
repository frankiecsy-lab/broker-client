"""One Gate Page 0 — 行情：多格 K 圖 grid（1×1 / 1×2 左右 / 2×2 / 2×3）+ 全週期 + 串流 + 本地記憶。

- **Grid**：固定 6 個 ChartCell 實例（max 2×3）；右上 4 個 layout 按鈕只 hide/show + reposition —
  **cells 永不銷毀**，切換 layout 唔中斷 stream、唔丟狀態。
- **每格 ChartCell**：標的欄（模糊輸入 = `gateway/symbol_input` — 同 P8 共用本地 symbol index，連名）+
  **週期 = 11 個 checkable 按鈕（文字全顯示，唔准 dropdown）**：futu KLType 全集
  K_1M/K_3M/K_5M/K_15M/K_30M/K_60M/K_DAY/K_WEEK/K_MON/K_QUARTER/K_YEAR +
  KlineChart（重用 gui_kline 純 widget，set_bars 餵數據）+ 最新價 label（紅漲綠跌）。
- **串流**：單 QThread + asyncio loop，每格一條 `BrokerClient.stream_kline`（first yield = baseline
  → 圖 + 價；其後 = live tick，250ms throttle redraw）— 多路並發 + token 作廢 stale，
  照 gateway/pages/gui_fulltest.py TestWorker._start_live 已驗證 pattern。
- **本地記憶**：所有格嘅標的/週期 + layout → `gateway/state_store.py` 統一 JSON（邊改邊 save，啟動 load）。
  標的經 symbol index canonical 大細階還原（HK.HSImain 細階 main — 同交易頁同一把尺）。
- **Theme**：照 kline_page recipe（reassign gk.C_* + chart._redraw()）；頁面級 QSS template。

單獨運行：`python gateway/pages/quotes_page.py`。
"""
import asyncio
import logging
import os
import re
import string
import sys

# ── standalone bootstrap（同 kline_page 同一 convention；package mode 下 no-op）──
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QObject  # noqa: E402
from PySide6.QtWidgets import (QApplication, QButtonGroup, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)

from gateway.pages import gui_kline as gk  # noqa: E402 — 同目錄 app 組件：重用 KlineChart / _fmt_big
import gateway.theme as theme_mod  # noqa: E402
from gateway import state_store  # noqa: E402
from gateway.i18n import DEFAULT_LANG, t  # noqa: E402
from gateway.symbol_input import attach_symbol_input, make_search  # noqa: E402 — 全域模糊輸入

# ── 常量 ──
KTYPES = ('K_1M', 'K_3M', 'K_5M', 'K_15M', 'K_30M', 'K_60M',
          'K_DAY', 'K_WEEK', 'K_MON', 'K_QUARTER', 'K_YEAR')   # futu KLType 全集（用戶：週期要齊全）
LAYOUTS = {'1x1': (1, 1), '1x2': (1, 2), '2x2': (2, 2), '2x3': (2, 3)}   # rows, cols
LAYOUT_LABELS = {'1x1': '1×1', '1x2': '1×2', '2x2': '2×2', '2x3': '2×3'}
N_CELLS = 6
TICK_UI_INTERVAL = 0.25   # chart redraw throttle（同 fulltest TICK_UI_INTERVAL）
_CODE_RE = re.compile(r'^(?:HK|US|SH|SZ)\.[A-Z0-9][A-Z0-9.]*$', re.IGNORECASE)
DEFAULT_CELLS = [{'symbol': 'HK.00700', 'period': 'K_1M'}] + [{'symbol': '', 'period': 'K_1M'}] * 5


def _rows_from_df(df):
    """K 線 df（KLINE_COLUMNS 已 validate）→ KlineChart.set_bars 嘅 tuples（舊→新）。"""
    out = []
    for r in df[['time_key', 'open', 'high', 'low', 'close', 'volume']].to_dict('records'):
        out.append((str(r['time_key']), float(r['open']), float(r['high']),
                    float(r['low']), float(r['close']), float(r['volume'])))
    return out


# ─────────────────────────── 串流 worker（多路並發） ───────────────────────────

class GridWorker(QObject):
    """住喺 worker thread；只發 signal，唔碰 widget。多格 stream 並發（futu 每條自開連線）。
    多路 + token pattern 照 gui_fulltest.TestWorker（_live_tasks / _next_tick_token）。"""

    cell_update = Signal(int, int, object)   # (cell_id, token, {'phase': baseline|tick|error[, 'df'|'error']})

    def __init__(self, loop, client_factory=None):
        super().__init__()
        self._loop = loop
        self._client_factory = client_factory   # e2e 注入 fake（hermetic 唔打網絡）
        self._client = None
        self._client_lock = asyncio.Lock()      # 並發首調 ensure_client 序列化（IB clientId 安全）
        self._live_tasks = {}                   # cell_id → consume task（每格最多一條 live stream）
        self._consume_tasks = set()
        self._shutting_down = False

    async def ensure_client(self):
        async with self._client_lock:
            if self._client is None:
                client = self._client_factory() if self._client_factory is not None else None
                if client is None:   # late-bind factory 而家返 None → 真 BrokerClient
                    from modules.broker import BrokerClient   # lazy — import 拖慢 UI 啟動
                    client = BrokerClient()
                self._client = client
                await self._client.__aenter__()
        return self._client

    # --- GUI-thread facade ---
    # token 由 GUI 派（每次 start/stop 都 bump）：worker 只 echo 返 —
    # 換標的/換週期/停 stream 嘅瞬間 GUI 已改 token，queue 入面遲到嘅舊 update 一律作廢
    def start_cell(self, cell_id, token, code, ktype):
        try:
            asyncio.run_coroutine_threadsafe(self._start_cell(cell_id, token, code, ktype), self._loop)
        except RuntimeError as e:   # loop 剛好停咗
            logging.warning('start_cell rejected (loop not running): %s', e)

    def stop_cell(self, cell_id):
        try:
            asyncio.run_coroutine_threadsafe(self._cancel_live(cell_id), self._loop)
        except RuntimeError:
            pass

    # --- loop-thread 內部 ---
    async def _start_cell(self, cid, token, code, ktype):
        await self._cancel_live(cid)   # 換標的/週期：先 cancel 舊 stream（broker cleanup 完成先算）
        if not code:
            return
        try:
            client = await self.ensure_client()
            status, gen, message = await client.stream_kline(code=code, ktype=ktype)
        except Exception as e:
            self.cell_update.emit(cid, token, {'phase': 'error', 'error': f'{type(e).__name__}: {e}'})
            return
        if not status or gen is None:
            self.cell_update.emit(cid, token, {'phase': 'error', 'error': str(message) or 'stream 啟動失敗'})
            return

        n = 0

        async def consume():
            nonlocal n
            try:
                async for df in gen:
                    n += 1
                    # 第一次 yield = baseline → 即刻上圖；其後 = live tick（GUI 端 throttle redraw）
                    self.cell_update.emit(cid, token, {'phase': 'baseline' if n == 1 else 'tick', 'df': df})
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.cell_update.emit(cid, token, {'phase': 'error', 'error': f'{type(e).__name__}: {e}'})

        task = asyncio.create_task(consume())
        self._consume_tasks.add(task)
        self._live_tasks[cid] = task

        def _cleanup(t, cid=cid):
            self._consume_tasks.discard(t)
            if self._live_tasks.get(cid) is t:   # 只移除仍然係當前嗰個（新 run 可能已取代）
                del self._live_tasks[cid]

        task.add_done_callback(_cleanup)

    async def _cancel_live(self, cid):
        old = self._live_tasks.get(cid)
        if old is not None and not old.done():
            old.cancel()
            await asyncio.gather(old, return_exceptions=True)

    async def _cancel_all(self):
        tasks = list(self._consume_tasks)
        for t in tasks:
            if not t.done():
                t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def shutdown(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        await self._cancel_all()
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception as e:
                logging.warning('broker cleanup failed: %s', e)
            self._client = None
        if self._loop.is_running():
            self._loop.stop()


class _LoopThread(QThread):
    """擁有 event loop + GridWorker；GUI thread 嘅 facade（同 gui_kline / fulltest 同一 pattern）。"""

    worker_ready = Signal(object)

    def __init__(self, client_factory=None, parent=None):
        super().__init__(parent)
        self._client_factory = client_factory
        self._worker = None
        self._loop = None
        self._stop_requested = False

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._worker = GridWorker(loop, self._client_factory)   # 喺呢條 thread 建 → signal 自動 queue 去 GUI
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

    def request_shutdown(self):
        loop, w = self._loop, self._worker
        if loop is not None and w is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(w.shutdown(), loop)
        else:
            self._stop_requested = True


# ─────────────────────────── 單格 ChartCell ───────────────────────────

class ChartCell(QWidget):
    """一格：標的欄（模糊輸入）+ 11 週期按鈕 + 最新價 label + KlineChart。"""

    def __init__(self, page, cell_id):
        super().__init__()
        self._page = page
        self._id = cell_id
        self._period = 'K_1M'
        self.setObjectName('quotes_cell')
        self.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(4)

        top = QHBoxLayout()
        self.symbol_edit = QLineEdit()
        self.symbol_edit.setObjectName('cell_symbol')
        self.symbol_edit.setPlaceholderText(t('quotes_symbol_ph', page._lang))
        # 🤖 模糊輸入一律經 gateway/symbol_input（本地 index + pass-through；揀咗淨返 CODE 即刻重取數）
        self.completer = attach_symbol_input(self.symbol_edit, make_search(page._directory),
                                             lang=page._lang, on_activate=lambda _c: self._submit_symbol())
        self.symbol_edit.returnPressed.connect(self._submit_symbol)
        self.symbol_edit.editingFinished.connect(self._submit_symbol)
        top.addWidget(self.symbol_edit, 1)
        self.price_lbl = QLabel('—')
        self.price_lbl.setObjectName('cell_price')
        top.addWidget(self.price_lbl)
        v.addLayout(top)

        per = QHBoxLayout()
        per.setSpacing(2)
        self._period_btns = {}
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        for kt in KTYPES:
            b = QPushButton(kt)
            b.setObjectName('period_btn')
            b.setCheckable(True)
            b.setFixedHeight(20)
            b.setCursor(Qt.PointingHandCursor)
            grp.addButton(b)
            b.clicked.connect(lambda _c=False, k=kt: self._page._on_cell_period(self._id, k))
            per.addWidget(b)
            self._period_btns[kt] = b
        v.addLayout(per)

        self.chart = gk.KlineChart(self)
        v.addWidget(self.chart, 1)

    # --- 狀態 ---
    def set_state(self, symbol, period):
        self.symbol_edit.blockSignals(True)
        self.symbol_edit.setText(symbol)
        self.symbol_edit.blockSignals(False)
        self._set_period_checked(period)

    def state(self):
        return {'symbol': self.symbol_edit.text().strip(), 'period': self._period}

    def _set_period_checked(self, period):
        if period not in KTYPES:
            period = 'K_1M'
        self._period = period
        self._period_btns[period].setChecked(True)

    # --- 輸入 ---
    def _submit_symbol(self):   # 🤖 揀咗 completer 候選都會行呢度（attach on_activate）
        self._page._on_cell_symbol(self._id, self.symbol_edit.text().strip())

    # --- 輸出 ---
    def show_error(self, msg):
        self.price_lbl.setText(msg)
        self.price_lbl.setStyleSheet(f'color: {gk.C_ACCENT}; font-weight: bold; background: transparent;')

    def show_rows(self, rows):
        if not rows:
            return
        self.chart.set_bars(rows)
        last = rows[-1][4]
        prev = rows[-2][4] if len(rows) > 1 else last
        color = gk.C_UP if last > prev else (gk.C_DOWN if last < prev else gk.C_TEXT)
        self.price_lbl.setText(gk._fmt_big(last))
        self.price_lbl.setStyleSheet(f'color: {color}; font-weight: bold; background: transparent;')

    def clear_chart(self):
        self.chart.clear()
        self.price_lbl.setText('—')


# ─────────────────────────── 頁 ───────────────────────────

class QuotesPage(QWidget):
    """行情頁 — grid + layout 按鈕 + 多路串流 + 本地記憶。"""

    def __init__(self, client_factory=None):
        super().__init__()
        self._client_factory = client_factory   # e2e 注入 fake broker
        self.setObjectName('quotes_page')       # QSS QWidget#quotes_page 靠呢個名
        self._lang = DEFAULT_LANG
        self._directory = None                  # symbol index lazy（首次用到先 load）
        self._worker = None
        self._cell_token = {}                   # cell_id → 當前 token（GUI 派；start/stop 都 bump → 遲到 update 一律作廢）
        self._next_token = 0
        self._pending_df = {}                   # cell_id → 最新未畫嘅 tick df（throttle）
        self._paint_timers = {}                 # cell_id → singleShot QTimer（250ms）

        st = state_store.load_section('quotes', {})
        self._layout = st.get('layout') if st.get('layout') in LAYOUTS else '1x1'
        cells_st = st.get('cells')
        if not isinstance(cells_st, list) or len(cells_st) != N_CELLS:
            cells_st = [dict(d) for d in DEFAULT_CELLS]

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(6)

        # ── 頂欄：右上方 4 個 layout 按鈕（用戶要求）──
        bar = QHBoxLayout()
        bar.addStretch(1)
        self._layout_btns = {}
        for name in LAYOUTS:
            b = QPushButton(LAYOUT_LABELS[name])
            b.setObjectName(f'layout_{name}')
            b.setProperty('og', 'layoutbtn')
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, n=name: self._apply_layout(n))
            bar.addWidget(b)
            self._layout_btns[name] = b
        v.addLayout(bar)

        # ── grid ──
        # 模糊輸入喺 cell 構造時就食 directory → 一定要先 load index（同 P8 啟動同位置同成本；
        # get_directory() 只讀 cache，唔會自動開網絡）
        self._directory = self._get_directory()
        self.grid = QGridLayout()
        self.grid.setSpacing(6)
        self.cells = []
        for i in range(N_CELLS):
            cell = ChartCell(self, i)
            cs = cells_st[i] if isinstance(cells_st[i], dict) else {}
            cell.set_state(str(cs.get('symbol', '')), str(cs.get('period', 'K_1M')))
            self.cells.append(cell)
        v.addLayout(self.grid, 1)

        # ── worker（client_factory late-bind：構造 win 之後先注入 fake 都生效）──
        # 注意：GridWorker 會 CALL 呢個 factory 攞 client — 要 call 返注入嘅 factory，
        # 唔好直接返佢（雙重包裝 → client 變咗 function，__aenter__ 即刻炸）
        self._thread = _LoopThread(
            client_factory=lambda: self._client_factory() if self._client_factory else None)
        self._thread.worker_ready.connect(self._on_worker_ready)
        self._thread.start()

        self._apply_layout(self._layout, save=False)   # 排 grid + start 可見格嘅 stream

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_app_quit)
        theme_mod.add_listener(self._on_theme_changed)
        self._apply_theme_qss(theme_mod.CURRENT)

    # ── lazy symbol index（同 P8 / 交易頁共用 singleton）──
    def _get_directory(self):
        if self._directory is None:
            import modules.symbol_search as ss
            self._directory = ss.get_directory()
        return self._directory

    def _canonical_code(self, raw):
        """index canonical 大細階（HK.HSImain 細階 main）；撳唔到 → upper()（同交易頁同一把尺）。"""
        try:
            e = self._get_directory().get(raw)
            if e:
                return e['code']
        except Exception:
            pass
        return raw.upper()

    # ── worker wiring ──
    def _on_worker_ready(self, worker):
        self._worker = worker
        worker.cell_update.connect(self._on_cell_update)
        self._restart_visible_cells()

    def _bump_token(self, cid):
        """派一個新 token 俾呢格（冇 stream 會帶住舊 token → 全部遲到 update 作廢）。"""
        self._cell_token[cid] = self._next_token
        self._next_token += 1
        return self._cell_token[cid]

    def _start_cell_stream(self, cid):
        if self._worker is None:
            return
        stt = self.cells[cid].state()
        code = stt['symbol']
        if not code or not _CODE_RE.match(code):
            return
        self._worker.start_cell(cid, self._bump_token(cid), self._canonical_code(code), stt['period'])

    def _restart_visible_cells(self):
        for cid in self._visible_cell_ids():
            self._start_cell_stream(cid)

    # ── layout ──
    def _visible_cell_ids(self):
        rows, cols = LAYOUTS[self._layout]
        return list(range(rows * cols))

    def _apply_layout(self, name, save=True):
        if name not in LAYOUTS:
            return
        self._layout = name
        rows, cols = LAYOUTS[name]
        # 可見 row/col 一律 stretch=1、其餘清 0 — 保證任何窗口大細/彈出與否都**均分**。
        # （stretch 全 0 時 QGridLayout 按 sizeHint 分配，而 matplotlib canvas 的 sizeHint
        #  = figure 像素尺寸，各格 figure 歷史唔同 → row 高度異變；stretch 非 0 則純按 stretch）
        for r in range(max(rc[0] for rc in LAYOUTS.values())):
            self.grid.setRowStretch(r, 1 if r < rows else 0)
        for c in range(max(rc[1] for rc in LAYOUTS.values())):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)
        visible = set(range(rows * cols))
        for i, cell in enumerate(self.cells):
            if i in visible:
                r, c = divmod(i, cols)
                self.grid.addWidget(cell, r, c)   # 已喺 layout 內 → reposition
                cell.setVisible(True)
            else:
                cell.setVisible(False)
                self._bump_token(i)
                if self._worker is not None:
                    self._worker.stop_cell(i)
        for n, b in self._layout_btns.items():
            b.setChecked(n == name)
        self._restart_visible_cells()
        if save:
            self._save_state()

    # ── 用戶輸入 → 重取數 + 記憶 ──
    def _on_cell_symbol(self, cid, code):
        cell = self.cells[cid]
        self._bump_token(cid)   # 先作廢舊 stream 所有 in-flight update（哪怕随后先 stop）
        if not code:
            if self._worker is not None:
                self._worker.stop_cell(cid)
            cell.clear_chart()
            self._save_state()
            return
        if not _CODE_RE.match(code):
            if self._worker is not None:
                self._worker.stop_cell(cid)   # 舊標的嘅 stream 都停 — 唔好讓舊價蓋住錯誤 label
            cell.show_error(t('quotes_invalid_code', self._lang))
            self._save_state()
            return
        self._start_cell_stream(cid)
        self._save_state()

    def _on_cell_period(self, cid, ktype):
        self.cells[cid]._set_period_checked(ktype)
        self._start_cell_stream(cid)
        self._save_state()

    # ── worker → UI ──
    def _on_cell_update(self, cid, token, payload):
        if token != self._cell_token.get(cid):   # stale — 舊 run（換標的/週期/停之後）嘅遲到 update，作廢
            return
        cell = self.cells[cid]
        phase = payload.get('phase')
        if phase == 'error':
            cell.show_error(f'❌ {payload.get("error", "?")}')
            return
        rows = _rows_from_df(payload['df'])
        if phase == 'baseline':
            cell.show_rows(rows)   # baseline 即刻上圖
            return
        # live tick：價即時，redraw throttle（同 fulltest TICK_UI_INTERVAL）
        self._pending_df[cid] = rows
        timer = self._paint_timers.get(cid)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda cid=cid: self._paint_cell(cid))
            self._paint_timers[cid] = timer
        if not timer.isActive():
            timer.start(TICK_UI_INTERVAL * 1000)

    def _paint_cell(self, cid):
        rows = self._pending_df.pop(cid, None)
        if rows:
            self.cells[cid].show_rows(rows)

    # ── 本地記憶（統一 state_store，邊改邊 save）──
    def _save_state(self):
        state_store.save_section('quotes', {
            'layout': self._layout,
            'cells': [c.state() for c in self.cells],
        })

    # ── theme（照 kline_page recipe：reassign gk.C_* + redraw）──
    def _apply_theme_qss(self, name):
        pal = theme_mod.THEMES[name]
        gk.C_WINDOW, gk.C_SURFACE = pal['window'], pal['surface']
        gk.C_CARD, gk.C_BORDER = pal['card'], pal['border']
        gk.C_TEXT, gk.C_MUTED = pal['text'], pal['muted']
        gk.C_ACCENT, gk.C_ACCENT_PRESSED = pal['accent'], pal['accent_pressed']
        self.setStyleSheet(string.Template(_PAGE_QSS).substitute(
            window=pal['window'], surface=pal['surface'], card=pal['card'],
            border=pal['border'], text=pal['text'], muted=pal['muted'],
            accent=pal['accent'], accent_pressed=pal['accent_pressed']))
        for cell in getattr(self, 'cells', []):
            cell.chart.readout.setStyleSheet(f'color: {gk.C_MUTED}; font-size: 12px; background: transparent;')
            cell.chart._redraw()

    def _on_theme_changed(self, name):
        self._apply_theme_qss(name)

    # ── i18n ──
    def retranslate(self, lang):
        self._lang = lang
        for cell in self.cells:
            cell.symbol_edit.setPlaceholderText(t('quotes_symbol_ph', lang))
            cell.completer.lang = lang   # 🤖 display_for 直接食 GUI 語言碼（zh_cn 唔會再被轉做繁體）

    # ── 清理 ──
    def _on_app_quit(self):
        try:
            self._thread.request_shutdown()
            self._thread.wait(3000)
        except Exception:
            pass


_PAGE_QSS = """
QWidget#quotes_page { background-color: $window; }
QWidget#quotes_cell { background-color: $surface; border: 1px solid $border; border-radius: 6px; }
QLineEdit#cell_symbol { background-color: $card; color: $text; border: 1px solid $border;
    border-radius: 4px; padding: 4px 6px; font-size: 13px; }
QLabel#cell_price { color: $text; font-size: 16px; font-weight: bold; background: transparent; }
QPushButton#period_btn { color: $muted; background: transparent; border: 1px solid $border;
    border-radius: 3px; font-size: 10px; padding: 1px 3px; }
QPushButton#period_btn:hover { color: $text; border-color: $accent; }
QPushButton#period_btn:checked { color: #FFFFFF; background-color: $accent; border-color: $accent; font-weight: bold; }
QPushButton[og="layoutbtn"] { color: $muted; background: transparent; border: 1px solid $border;
    border-radius: 4px; padding: 4px 10px; font-size: 12px; }
QPushButton[og="layoutbtn"]:hover { color: $text; border-color: $accent; }
QPushButton[og="layoutbtn"]:checked { color: #FFFFFF; background-color: $accent; border-color: $accent; font-weight: bold; }
"""


if __name__ == '__main__':
    from gateway.pages.base_page import run_standalone
    run_standalone(QuotesPage, 'page_quotes_title')

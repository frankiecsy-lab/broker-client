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
- **#27 指標 / 策略標記**：頂欄「顯示指標」總開關（開 = 注入 `IndicatorManager`，所有格跟指標管理頁配置）+
  策略下拉（#30：揀咗 = 注入**全部格**，每格用自己數據計 B/S = 同一套規則同時套用到 1/2/4/6 全部 K 圖）。
  `IndicatorKlineChart` 嘅指標/marks cache 以 data_seq 為 key → **串流刷新自動同步**，唔需要额外重算觸發。
  #30：`StrategyManager.add_listener` → 策略頁（包括彈出窗）改規則/BUFFER → 下拉 + 全部格即時跟。
- **#28 指標選項 menu**：逐個指標 checkable（= manager `enabled`，同 K線頁/管理頁雙向同步）；MA 實例
  sub-menu 逐條線（params `show1..4`，0 → 唔畫、唔入 Y-fit）。總開關保留 = 頁面級 all-off。
- **本地記憶**：所有格嘅標的/週期 + layout + 指標開關/策略 id → `gateway/state_store.py` 統一 JSON（邊改邊 save，啟動 load）。
  標的經 symbol index canonical 大細階還原（HK.HSImain 細階 main — 同交易頁同一把尺）。
- **排版**：`gateway/ui/quotes_page.ui`（頂欄 + 空 `layoutSlot` / `gridSlot`）同 `gateway/ui/chart_cell.ui`
  （單格：標的欄 + 空 `periodSlot` + **promote** 咗嘅 `IndicatorKlineChart`，由 `loader.register_custom` 起返真 class）。
  🤖 控件**數量**一律屬資料 → layout 按鈕 = `LAYOUTS`、週期掣 = `KTYPES`、格數 = `N_CELLS`、
  策略 item = StrategyManager：全部由 code 填進 slot，加 layout／週期／格數唔使改 `.ui`。
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
from PySide6.QtWidgets import QApplication, QButtonGroup, QMenu, QPushButton, QWidget  # noqa: E402

from gateway.pages import gui_kline as gk  # noqa: E402 — 同目錄 app 組件：重用 KlineChart / _fmt_big
import gateway.indicators as indicators  # noqa: E402 — #27：IndicatorKlineChart（指標 + B/S 標記）
import gateway.theme as theme_mod  # noqa: E402
from gateway import state_store  # noqa: E402
from gateway.i18n import DEFAULT_LANG, t  # noqa: E402
from gateway.symbol_input import attach_symbol_input, make_search  # noqa: E402 — 全域模糊輸入
from gateway.ui.bind import apply_text, stamp  # noqa: E402
from gateway.ui.loader import apply_ui, register_custom  # noqa: E402

register_custom(indicators.IndicatorKlineChart)   # chart_cell.ui promote 呢個自繪 widget（見 loader._CUSTOM）

# ── 常量 ──
KTYPES = ('K_1M', 'K_3M', 'K_5M', 'K_15M', 'K_30M', 'K_60M',
          'K_DAY', 'K_WEEK', 'K_MON', 'K_QUARTER', 'K_YEAR')   # futu KLType 全集（用戶：週期要齊全）
LAYOUTS = {'1x1': (1, 1), '1x2': (1, 2), '2x2': (2, 2), '2x3': (2, 3)}   # rows, cols
LAYOUT_LABELS = {'1x1': '1×1', '1x2': '1×2', '2x2': '2×2', '2x3': '2×3'}
N_CELLS = 6
TICK_UI_INTERVAL = 0.25   # chart redraw throttle（同 fulltest TICK_UI_INTERVAL）
_CODE_RE = re.compile(r'^(?:HK|US|SH|SZ)\.[A-Z0-9][A-Z0-9.]*$', re.IGNORECASE)
DEFAULT_CELLS = [{'symbol': 'HK.00700', 'period': 'K_1M'}] + [{'symbol': '', 'period': 'K_1M'}] * 5

# `.ui` 內嘅靜態 widget：QSS property（Designer 帶唔住 dynamic property）+ 文字來源（見 gateway/ui/bind.py）
_STAMP = {'quotes_page': {},   # 純 QWidget root → 補 WA_StyledBackground，頁面級 QSS 先食到
          'ind_toggle': {'og': 'indtoggle'}, 'ind_menu_btn': {'og': 'indmenu'}}
_TEXT = {'ind_toggle': 'quotes_ind_show', 'ind_menu_btn': 'quotes_ind_menu',
         'strat_label': 'quotes_strategy_label'}
_STAMP_CELL = {'quotes_cell': {}}   # 每格 root 都係純 QWidget → 同上（QSS QWidget#quotes_cell）


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
    """一格：標的欄（模糊輸入）+ 11 週期按鈕 + 最新價 label + KlineChart。

    排版全部喺 `gateway/ui/chart_cell.ui`（`chart` 係 promote 咗嘅 `IndicatorKlineChart` —
    未注入 manager/策略時行為同 gk.KlineChart）；本檔只剩行為：接輸入、餵數據、砌週期掣。
    """

    def __init__(self, page, cell_id):
        super().__init__()
        self._page = page
        self._id = cell_id
        self._period = 'K_1M'
        apply_ui(self, 'chart_cell')   # root objectName = quotes_cell（QSS QWidget#quotes_cell 靠呢個名）
        stamp(self, _STAMP_CELL)       # WA_StyledBackground：Designer 帶唔住 dynamic property
        # placeholder 嘅來源係 i18n（`.ui` 內嗰句只係俾 Designer 睇）；逐格 set → 唔入 _TEXT 表
        self.cell_symbol.setPlaceholderText(t('quotes_symbol_ph', page._lang))
        # 🤖 模糊輸入一律經 gateway/symbol_input（本地 index + pass-through；揀咗淨返 CODE 即刻重取數）
        self.completer = attach_symbol_input(self.cell_symbol, make_search(page._directory),
                                             lang=page._lang, on_activate=lambda _c: self._submit_symbol())
        self.cell_symbol.returnPressed.connect(self._submit_symbol)
        self.cell_symbol.editingFinished.connect(self._submit_symbol)
        self._build_period_btns()

    def _build_period_btns(self):
        """週期掣 = futu KLType 全集（**數量屬資料**）→ 逐個填進 `.ui` 預留嘅空 periodSlot。"""
        self._period_btns = {}
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        for kt in KTYPES:
            b = QPushButton(kt)
            b.setObjectName('period_btn')   # 多格共用同一個名（QSS #period_btn），照舊
            b.setCheckable(True)
            b.setFixedHeight(20)
            b.setCursor(Qt.PointingHandCursor)
            grp.addButton(b)
            b.clicked.connect(lambda _c=False, k=kt: self._page._on_cell_period(self._id, k))
            self.periodSlot.addWidget(b)
            self._period_btns[kt] = b

    # --- 狀態 ---
    def set_state(self, symbol, period):
        self.cell_symbol.blockSignals(True)
        self.cell_symbol.setText(symbol)
        self.cell_symbol.blockSignals(False)
        self._set_period_checked(period)

    def state(self):
        return {'symbol': self.cell_symbol.text().strip(), 'period': self._period}

    def _set_period_checked(self, period):
        if period not in KTYPES:
            period = 'K_1M'
        self._period = period
        self._period_btns[period].setChecked(True)

    # --- 輸入 ---
    def _submit_symbol(self):   # 🤖 揀咗 completer 候選都會行呢度（attach on_activate）
        self._page._on_cell_symbol(self._id, self.cell_symbol.text().strip())

    # --- 輸出 ---
    def show_error(self, msg):
        self.cell_price.setText(msg)
        self.cell_price.setStyleSheet(f'color: {gk.C_ACCENT}; font-weight: bold; background: transparent;')

    def show_rows(self, rows):
        if not rows:
            return
        self.chart.set_bars(rows)
        last = rows[-1][4]
        prev = rows[-2][4] if len(rows) > 1 else last
        color = gk.C_UP if last > prev else (gk.C_DOWN if last < prev else gk.C_TEXT)
        self.cell_price.setText(gk._fmt_big(last))
        self.cell_price.setStyleSheet(f'color: {color}; font-weight: bold; background: transparent;')

    def clear_chart(self):
        self.chart.clear()
        self.cell_price.setText('—')


# ─────────────────────────── 頁 ───────────────────────────

class QuotesPage(QWidget):
    """行情頁 — grid + layout 按鈕 + 多路串流 + 本地記憶。

    排版全部喺 `gateway/ui/quotes_page.ui`（頂欄控件 + 空 `layoutSlot` / `gridSlot`）；
    本檔只剩行為：按 registry 砌控件填進 slot、接 signal、餵數據、排 grid。
    """

    def __init__(self, client_factory=None):
        super().__init__()
        self._client_factory = client_factory   # e2e 注入 fake broker
        apply_ui(self, 'quotes_page')           # root objectName = quotes_page（QSS 靠呢個名做根）
        stamp(self, _STAMP)                     # og / WA_StyledBackground：Designer 帶唔住
        self._lang = DEFAULT_LANG
        self._directory = None                  # symbol index lazy（首次用到先 load）
        self._worker = None
        self._cell_token = {}                   # cell_id → 當前 token（GUI 派；start/stop 都 bump → 遲到 update 一律作廢）
        self._next_token = 0
        self._pending_df = {}                   # cell_id → 最新未畫嘅 tick df（throttle）
        self._paint_timers = {}                 # cell_id → singleShot QTimer（250ms）

        st = state_store.load_section('quotes', {})
        self._layout = st.get('layout') if st.get('layout') in LAYOUTS else '1x1'
        self._ind_shown = bool(st.get('ind_shown', False))       # #27
        self._strategy_id = str(st.get('strategy', '') or '')     # #27
        cells_st = st.get('cells')
        if not isinstance(cells_st, list) or len(cells_st) != N_CELLS:
            cells_st = [dict(d) for d in DEFAULT_CELLS]

        # ── 頂欄控件（指標開關 / 指標選項 menu / 策略下拉）已由 `.ui` 建出 → 呢度只還原狀態 ──
        self.ind_toggle.setChecked(self._ind_shown)
        self.ind_menu_btn.setEnabled(self._ind_shown)
        self._ind_acts = {}          # iid → QAction（E2E hook）
        self._ma_show_acts = {}      # (iid, n) → QAction（E2E hook）
        self._build_layout_btns()

        # ── grid ──
        # 模糊輸入喺 cell 構造時就食 directory → 一定要先 load index（同 P8 啟動同位置同成本；
        # get_directory() 只讀 cache，唔會自動開網絡）
        self._directory = self._get_directory()
        self._build_cells(cells_st)
        self._connect_signals()
        self._retranslate_widgets()   # `.ui` 內嘅文字屬裝飾 → 一律跟語言覆寫

        # ── #27：還原指標開關 / 策略（cells 起齊先至 apply 到每格 chart）──
        self._rebuild_strategy_combo()
        self._apply_indicator_toggle()
        self._apply_strategy_to_cells()
        indicators.get_manager().add_listener(self._on_ind_config)   # 指標管理頁改配置 → 即時跟
        from gateway import strategies as strat
        strat.get_manager().add_listener(self._on_strat_config)      # #30：策略頁改規則/BUFFER → 即時跟

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

    # ── 按 registry 填 `.ui` 預留嘅空 slot（數量屬資料，排版屬 UI）──
    def _build_layout_btns(self):
        """layout 按鈕 = `LAYOUTS` → 逐個填進 layoutSlot；加 layout 唔使改 `.ui`。"""
        self._layout_btns = {}
        for name in LAYOUTS:
            b = QPushButton(LAYOUT_LABELS[name])
            b.setObjectName(f'layout_{name}')
            b.setProperty('og', 'layoutbtn')   # QSS [og="layoutbtn"]（code 起嘅控件自己 set）
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, n=name: self._apply_layout(n))
            self.layoutSlot.addWidget(b)
            self._layout_btns[name] = b

    def _build_cells(self, cells_st):
        """格數 = `N_CELLS` → 逐格 ChartCell（排版見 chart_cell.ui）填進 gridSlot。
        邊格可見、放邊個 row/col 屬行為 → 交落 `_apply_layout`；cells 永不銷毀（唔中斷 stream、唔丟狀態）。"""
        self.cells = []
        for i in range(N_CELLS):
            cell = ChartCell(self, i)
            cs = cells_st[i] if isinstance(cells_st[i], dict) else {}
            cell.set_state(str(cs.get('symbol', '')), str(cs.get('period', 'K_1M')))
            self.cells.append(cell)

    def _connect_signals(self):
        """signal 一律留喺 code（`.ui` 嘅 `<connections>` 留空）。"""
        self.ind_toggle.clicked.connect(lambda _c=False: self._on_ind_toggle())
        # #28：「指標選項」menu — 逐個指標 checkable（= manager enabled，同 K線頁/管理頁雙向同步）；
        # MA 實例另有 sub-menu 逐條線 show1..4。總開關 ind_toggle 保留（頁面級 all-off，唔碰全局）。
        self._ind_menu = QMenu(self.ind_menu_btn)
        self._ind_menu.aboutToShow.connect(self._rebuild_ind_menu)   # 每次開都食最新 manager 狀態
        self.ind_menu_btn.setMenu(self._ind_menu)
        self.strat_combo.currentIndexChanged.connect(self._on_strategy_choice)

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
            self.gridSlot.setRowStretch(r, 1 if r < rows else 0)
        for c in range(max(rc[1] for rc in LAYOUTS.values())):
            self.gridSlot.setColumnStretch(c, 1 if c < cols else 0)
        visible = set(range(rows * cols))
        for i, cell in enumerate(self.cells):
            if i in visible:
                r, c = divmod(i, cols)
                self.gridSlot.addWidget(cell, r, c)   # 已喺 layout 內 → reposition
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

    # ── #27：指標總開關 / 策略下拉（B/S 標記；同步靠 IndicatorKlineChart cache，呢度只管注入）──
    def _on_ind_toggle(self):
        self._ind_shown = self.ind_toggle.isChecked()
        self.ind_menu_btn.setEnabled(self._ind_shown)   # #28：關住指標 → 逐個揀冇意義，灰咗
        self._apply_indicator_toggle()
        self._save_state()

    def _apply_indicator_toggle(self):
        mgr = indicators.get_manager() if self._ind_shown else None
        for cell in self.cells:
            cell.chart.set_indicator_manager(mgr)
            cell.chart._redraw()

    def _on_ind_config(self, origin, kind):
        """指標配置變（任何 origin，包括本頁 menu）→ 開住即 redraw。"""
        if not self._ind_shown:
            return
        for cell in self.cells:
            cell.chart.mark_indicators_dirty()
            cell.chart._redraw()

    # ── #28：指標選項 menu — 逐個指標 checkable = manager enabled（同 K線頁掣列/管理頁天然同步）；
    #    MA 實例加 sub-menu 逐條線（params show1..4）。aboutToShow 每次重建 = 永遠食最新狀態。──
    def _rebuild_ind_menu(self):
        mgr = indicators.get_manager()
        self._ind_menu.clear()
        self._ind_acts = {}
        self._ma_show_acts = {}
        for e in mgr.items():
            d = indicators.INDICATOR_DEFS[e['def']]   # items() 嘅 'def' 係 def key 字串
            act = self._ind_menu.addAction(
                '%s · %s' % (d.label, indicators._params_summary(d, e['params'])))
            act.setCheckable(True)
            act.setChecked(bool(e['enabled']))
            act.triggered.connect(
                lambda _c, iid=e['id'], a=act: mgr.set_enabled(iid, a.isChecked(), origin='quotes_page'))
            self._ind_acts[e['id']] = act
            if d.key == 'ma':   # MA 逐條線 → showN=0 → compute 唔輸出 key = 唔畫、唔入 Y-fit
                sub = self._ind_menu.addMenu('MA')
                for n in (1, 2, 3, 4):
                    sa = sub.addAction('MA %s' % e['params'].get('p%d' % n, ''))
                    sa.setCheckable(True)
                    sa.setChecked(bool(int(e['params'].get('show%d' % n, 1))))
                    sa.triggered.connect(
                        lambda _c, iid=e['id'], k='show%d' % n, a=sa:
                        mgr.update(iid, params={k: 1 if a.isChecked() else 0}, origin='quotes_page'))
                    self._ma_show_acts[(e['id'], n)] = sa

    def _rebuild_strategy_combo(self):
        from gateway import strategies as strat   # lazy — 開行情頁先至碰策略 domain
        self.strat_combo.blockSignals(True)
        self.strat_combo.clear()
        self.strat_combo.addItem(t('quotes_strategy_none', self._lang), '')
        for e in strat.get_manager().items():
            self.strat_combo.addItem(e['name'], e['id'])   # #32：策略冇標的欄，label 只剩名稱
        self.strat_combo.setCurrentIndex(max(0, self.strat_combo.findData(self._strategy_id)))
        self._strategy_id = self.strat_combo.currentData() or ''
        self.strat_combo.blockSignals(False)

    def _on_strategy_choice(self):
        self._strategy_id = self.strat_combo.currentData() or ''
        self._apply_strategy_to_cells()
        self._save_state()

    def _apply_strategy_to_cells(self):
        """#30：揀咗策略 → 全部格都注入（用戶要求：1/2/4/6 全部 K 線圖同時套用）；
        B/S 每格用自己數據重算 = 同一套規則逐格計（冇數據嘅格自然空）。"""
        entry = None
        if self._strategy_id:
            from gateway import strategies as strat
            entry = strat.get_manager().get(self._strategy_id)
        for cell in self.cells:
            cell.chart.set_strategy(entry)
            cell.chart._redraw()

    def _on_strat_config(self, origin, kind):
        """策略變（任何 origin，包括策略頁/彈出策略頁）→ 下拉 + 全部格即時跟（#30）。"""
        self._rebuild_strategy_combo()
        self._apply_strategy_to_cells()

    # ── 用戶輸入 → 重取數 + 記憶 ──
    def _on_cell_symbol(self, cid, code):
        cell = self.cells[cid]
        self._bump_token(cid)   # 先作廢舊 stream 所有 in-flight update（哪怕随后先 stop）
        self._pending_df.pop(cid, None)   # 舊標的嘅 pending rows 一并丟 — 唔好 250ms 後蓋住新狀態（錯誤 label／空圖）
        # #30：策略注入晒全部格、唔再 match 標的 → 改標的唔使 re-apply
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
            'ind_shown': self._ind_shown,     # #27
            'strategy': self._strategy_id,    # #27
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
    def _retranslate_widgets(self):
        lang = self._lang
        apply_text(self, _TEXT, lang)   # 指標開關 / 指標選項 / 策略 label（objectName → i18n key）
        for cell in getattr(self, 'cells', []):
            cell.cell_symbol.setPlaceholderText(t('quotes_symbol_ph', lang))   # 逐格 placeholder
            cell.completer.lang = lang   # 🤖 display_for 直接食 GUI 語言碼（zh_cn 唔會再被轉做繁體）

    def retranslate(self, lang):
        self._lang = lang
        self._retranslate_widgets()
        self._rebuild_strategy_combo()   # 「無策略」item 跟語言

    # ── #27：每次入頁都 refresh 策略下拉（策略頁可能新增/刪除咗）──
    def showEvent(self, ev):
        super().showEvent(ev)
        self._rebuild_strategy_combo()
        self._apply_strategy_to_cells()

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
QPushButton[og="indtoggle"] { color: $muted; background: transparent; border: 1px solid $border;
    border-radius: 4px; padding: 4px 10px; font-size: 12px; }
QPushButton[og="indtoggle"]:hover { color: $text; border-color: $accent; }
QPushButton[og="indtoggle"]:checked { color: #FFFFFF; background-color: $accent; border-color: $accent; font-weight: bold; }
QToolButton[og="indmenu"] { color: $muted; background: transparent; border: 1px solid $border;
    border-radius: 4px; padding: 4px 10px; font-size: 12px; }
QToolButton[og="indmenu"]:hover { color: $text; border-color: $accent; }
QToolButton[og="indmenu"]:disabled { color: $border; }
QMenu { background-color: $card; color: $text; border: 1px solid $border; }
QMenu::item:selected { background-color: $accent; color: #FFFFFF; }
QLabel#strat_label { color: $muted; font-size: 12px; background: transparent; }
QComboBox#strat_combo { background-color: $card; color: $text; border: 1px solid $border;
    border-radius: 4px; padding: 3px 6px; font-size: 12px; min-width: 130px; }
"""


if __name__ == '__main__':
    from gateway.pages.base_page import run_standalone
    run_standalone(QuotesPage, 'page_quotes_title')

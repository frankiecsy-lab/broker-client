"""One Gate Page 5 — 標的列表：全 index 表格 + FILTER + 模糊輸入 + 一鍵更新 + 衍生下鑽。

- **表格**：QTableView + 自設 model（欄随 mode 變 — 見 SCHEMAS）；點擊欄頭排序。
  數據 = modules/symbol_search 本地 index（同 P8 / 交易頁同一 singleton）。
- **FILTER**：兩組 checkable 按鈕（市場 × 種類），各組 exclusive；選擇記憶 → state_store 'symbol_list'。
- **下鑽**（用戶要求：期權二級、窩輪三級）：
  - 窩輪（WARRANT）→ **L1** 有窩輪嘅標的（按 owner 分組 + 認購/認沽/牛/熊計數）→
    **L2** 該標嘅窩輪類別 → **L3** 該類別窩輪列表（行使價/到期日）。
    🤖 US 窩輪無 owner/類別（實測 'N/A.'）→ 一筆「美股窩輪」偽行，點擊直接落 L3 平鋪。
  - 期權（OPTION）→ **L1** 股票/ETF 候選（冇接口能枚舉「邊個有期權」— tooltip 如實講）→
    點擊 → **L2** 即時期權鏈（QThread 行 symbol_search.fetch_option_chain，逐到期日、progress、
    session cache、失敗如實 ❌）。
  - 面包屑 + 「◀ 返回」逐級退；點擊行落級（leaf 級唔響應）。
- **模糊輸入**：debounce 200ms。flat → directory.search(types=None)；o1 → search(types=STOCK/ETF)；
  其餘 mode → 對目前顯示行做 substring 過濾（純本地）。
- **一鍵更新**：QThread 行 directory.fetch(US+HK 全 plan) — progress 逐段更新 status。
- **底部**：市場 × 種類 計數（全 index）+ 顯示筆數 + 更新時間。
- **排版**：`gateway/ui/symbol_list_page.ui`（Designer 可調）— 搜尋欄、面包屑欄、表格、底部欄喺
  `.ui`；**FILTER 掣嘅數量**屬資料（= `MARKETS` / `TYPES` registry）→ 由 `_mk_group` 逐個填進
  `.ui` 預留嘅 `filterSlot`（加市場／種類唔使改 `.ui`）。
- **Theme/i18n**：照其他頁 recipe（`gateway/ui/bind.py` 嘅 `stamp` / `apply_text`）；retranslate 會一併 refresh（偽行名/面包屑跟語言）。

單獨運行：`python gateway/pages/symbol_list_page.py`。
"""
import logging
import os
import string
import sys
from collections import Counter

# ── standalone bootstrap（同 quotes_page 同一 convention；package mode 下 no-op）──
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt, QAbstractTableModel, QThread, QTimer, Signal  # noqa: E402
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QHeaderView,  # noqa: E402
                               QPushButton, QWidget)

from modules.symbol_search import display_for, to_simplified  # noqa: E402
import gateway.theme as theme_mod  # noqa: E402
from gateway import state_store  # noqa: E402
from gateway.i18n import DEFAULT_LANG, t  # noqa: E402
from gateway.ui.bind import apply_text, stamp  # noqa: E402
from gateway.ui.loader import apply_ui  # noqa: E402

# ── 常量（FILTER 按鈕順序 = 用戶列舉順序）──
MARKETS = ('ALL', 'HK', 'US')
MARKET_KEYS = {'ALL': 'sl_mkt_all', 'HK': 'sl_mkt_hk', 'US': 'sl_mkt_us'}
TYPES = ('ALL', 'STOCK', 'ETF', 'IDX', 'FUTURE', 'OPTION', 'WARRANT')
TYPE_KEYS = {'ALL': 'sl_type_all', 'STOCK': 'sl_type_stock', 'ETF': 'sl_type_etf',
             'IDX': 'sl_type_idx', 'FUTURE': 'sl_type_future', 'OPTION': 'sl_type_option',
             'WARRANT': 'sl_type_warrant'}
SEARCH_DEBOUNCE_MS = 200

# ── 下鑽 mode / schema（欄 key → i18n header key）──
MODES = ('flat', 'w1', 'w2', 'w3', 'o1', 'o2')
SCHEMAS = {
    'flat': ('code', 'name', 'market', 'type'),
    'w1':   ('code', 'name', 'total', 'CALL', 'PUT', 'BULL', 'BEAR', 'OTHER'),   # 有窩輪嘅標的
    'w2':   ('wtype', 'count'),                                                    # 窩輪類別
    'w3':   ('code', 'name', 'wtype', 'strike', 'expiry'),                        # 窩輪列表
    'o1':   ('code', 'name', 'market'),                                           # 期權候選（股票/ETF）
    'o2':   ('code', 'name', 'otype', 'strike', 'expiry', 'lot'),                 # 期權鏈
}
HEAD_KEYS = {
    'code': 'sl_head_code', 'name': 'sl_head_name', 'market': 'sl_head_market',
    'type': 'sl_head_type', 'total': 'sl_w_head_total', 'count': 'sl_w_head_count',
    'wtype': 'sl_w_head_wtype', 'strike': 'sl_w_head_strike', 'expiry': 'sl_w_head_expiry',
    'lot': 'sl_w_head_lot', 'otype': 'sl_w_head_otype',
    'CALL': 'sl_w_call', 'PUT': 'sl_w_put', 'BULL': 'sl_w_bull', 'BEAR': 'sl_w_bear',
    'OTHER': 'sl_w_other',
}
NUMERIC_COLS = {'total', 'count', 'strike', 'lot'}
WTYPE_KEYS = {'CALL': 'sl_w_call', 'PUT': 'sl_w_put', 'BULL': 'sl_w_bull',
              'BEAR': 'sl_w_bear', 'INLINE': 'sl_w_inline'}
WTYPE_ORDER = ('CALL', 'PUT', 'BULL', 'BEAR', 'INLINE', 'OTHER')   # w2 類別顯示順序
OTYPE_KEYS = {'CALL': 'sl_opt_call', 'PUT': 'sl_opt_put'}

# `.ui` 入面嘅靜態 widget：QSS property（Designer 帶唔住）+ 文字來源（見 gateway/ui/bind.py）
_STAMP = {'symbol_list_page': {},   # 純 QWidget root → 補 WA_StyledBackground，頁面級 QSS 先食到
          'sl_update_btn': {'og': 'slbtn'}, 'sl_back_btn': {'og': 'slbtn'}}
_TEXT = {'sl_update_btn': 'sl_update', 'sl_back_btn': 'sl_back'}
_PH = {'sl_search': 'sl_search_ph'}   # placeholder 唔屬 setText → 单独一行 loop


class _ListModel(QAbstractTableModel):
    """(cols, rows) → 表格；欄随 mode（set_view 一併換 schema）。排序喺呢度做。"""

    def __init__(self, page):
        super().__init__()
        self._page = page
        self._cols = SCHEMAS['flat']
        self._rows = []

    def set_view(self, cols, rows):
        self.beginResetModel()
        self._cols, self._rows = cols, rows
        self.endResetModel()

    def rowCount(self, parent=None):
        return len(self._rows)

    def columnCount(self, parent=None):
        return len(self._cols)

    def data(self, index, role=Qt.DisplayRole):
        if role != Qt.DisplayRole or not index.isValid():
            return None
        return self._page._cell(self._rows[index.row()], self._cols[index.column()])

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return t(HEAD_KEYS[self._cols[section]], self._page._lang)
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        col = self._cols[column]
        if col in NUMERIC_COLS:
            key = lambda r: _as_num(r.get(col))                                   # noqa: E731
        elif col == 'name':
            key = lambda r: self._page._cell(r, 'name').lower()                  # noqa: E731
        elif col in ('wtype', 'otype'):
            key = lambda r: self._page._cell(r, col)                              # noqa: E731
        else:
            key = lambda r: str(r.get(col, '')).upper()                           # noqa: E731
        self._rows.sort(key=key, reverse=(order == Qt.DescendingOrder))
        self.layoutChanged.emit()


def _as_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float('-inf')


class _FetchWorker(QThread):
    """一鍵更新：喺 QThread 行 blocking fetch（OpenD 枚舉 ~10 秒），唔 block UI。"""

    progress = Signal(str, int)     # ("HK/WARRANT", kept)
    done = Signal(bool, str)

    def __init__(self, directory, parent=None):
        super().__init__(parent)
        self._directory = directory

    def run(self):
        try:
            ok, msg = self._directory.fetch(
                markets=('US', 'HK'),
                progress_cb=lambda label, count: self.progress.emit(str(label), int(count)))
        except Exception as e:   # 連不上 OpenD 等 — 如實回報（Error honesty）
            logging.warning('symbol list fetch failed: %s', e)
            ok, msg = False, f'{type(e).__name__}: {e}'
        self.done.emit(ok, msg)


class _ChainWorker(QThread):
    """期權鏈：喺 QThread 行 symbol_search.fetch_option_chain（逐到期日，~秒級）唔 block UI。"""

    progress = Signal(int, int, str)   # (i, total, date)
    done = Signal(bool, str, list)

    def __init__(self, code, fetcher, parent=None):
        super().__init__(parent)
        self._code = code
        self._fetcher = fetcher

    def run(self):
        try:
            ok, rows, msg = self._fetcher(
                self._code,
                progress_cb=lambda i, total, d: self.progress.emit(int(i), int(total), str(d)))
        except Exception as e:
            logging.warning('option chain fetch failed: %s', e)
            ok, rows, msg = False, [], f'{type(e).__name__}: {e}'
        self.done.emit(ok, msg, rows)


class SymbolListPage(QWidget):
    """標的列表頁 — 表格 + FILTER + 模糊 + 一鍵更新 + 計數 + 期權/窩輪下鑽。"""

    def __init__(self, directory=None, option_fetcher=None):
        super().__init__()
        apply_ui(self, 'symbol_list_page')   # 排版（搜尋欄/面包屑欄/表格/底部欄）全部喺 `.ui`
        stamp(self, _STAMP)                  # og / WA_StyledBackground：Designer 帶唔住 dynamic property
        self._lang = DEFAULT_LANG
        self._directory = directory          # e2e 注入 fake；None → lazy get_directory()（只讀 cache）
        self._option_fetcher = option_fetcher  # e2e 注入 fake；None → lazy symbol_search.fetch_option_chain
        self._worker = None                  # _FetchWorker ref — 防 GC + 防重入
        self._chain_worker = None            # _ChainWorker ref — 防 GC + 防重入
        self._chain_cache = {}               # code → rows（session 內唔重 fetch）

        st = state_store.load_section('symbol_list', {})
        self._market = st.get('market') if st.get('market') in MARKETS else 'ALL'
        self._type = st.get('type') if st.get('type') in TYPES else 'ALL'
        self._mode = self._mode_for_type(self._type)   # 還原時直接返返對應下鑽起點
        self._drill = []                               # 下鑽上下文 stack（{'owner':…}/{'wtype':…}/{'code':…}）

        self._setup_table()
        self._build_filter_btns()   # 兩組 FILTER 掣按 MARKETS/TYPES 填進 `.ui` 嘅 filterSlot
        self._connect_signals()

        theme_mod.add_listener(self._on_theme_changed)
        self._apply_theme_qss(theme_mod.CURRENT)
        self._retranslate_widgets()   # `.ui` 內嘅文字屬裝飾 → 一律跟語言覆寫
        self._refresh()

    # ── 表格行為（控件本身喺 `.ui`）──
    def _setup_table(self):
        self.model = _ListModel(self)
        self.sl_table.setModel(self.model)
        self.sl_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sl_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sl_table.setSortingEnabled(True)
        self.sl_table.verticalHeader().setVisible(False)

    def _connect_signals(self):
        self._debounce = QTimer(self)   # 模糊輸入 debounce（行為，唔屬排版）
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(SEARCH_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._refresh)
        self.sl_search.textChanged.connect(lambda _q: self._debounce.start())
        self.sl_update_btn.clicked.connect(self._on_update_clicked)
        self.sl_back_btn.clicked.connect(self._back)
        # 🤖 QTableView 冇 QTableWidget.cellClicked — 用 QAbstractItemView.clicked（QModelIndex）
        self.sl_table.clicked.connect(lambda idx: self._on_cell_clicked(idx.row(), idx.column()))

    # ── lazy index（只讀 cache，唔會自動開網絡）──
    def _dir(self):
        if self._directory is None:
            import modules.symbol_search as ss
            self._directory = ss.get_directory()
        return self._directory

    def _opt_fetcher(self):
        if self._option_fetcher is None:
            from modules.symbol_search import fetch_option_chain
            self._option_fetcher = fetch_option_chain
        return self._option_fetcher

    def _build_filter_btns(self):
        """FILTER 兩組（exclusive 各組；再撳同一個 = 唔取消，永遠有一個 checked）。
        掣嘅**數量**屬資料（= MARKETS / TYPES registry）→ 逐個填進 `.ui` 預留嘅 `filterSlot`。"""
        self._mkt_btns, self._type_btns = {}, {}
        self._mk_group(self.filterSlot, MARKETS, MARKET_KEYS, self._mkt_btns,
                       lambda m: self._set_filter(market=m))
        self._mk_group(self.filterSlot, TYPES, TYPE_KEYS, self._type_btns,
                       lambda ty: self._set_filter(type_=ty))
        self.filterSlot.addStretch(1)

    def _mk_group(self, layout, keys, i18n_keys, store, on_pick):
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        for k in keys:
            b = QPushButton()   # 文字屬語言 → retranslate 先填
            b.setObjectName(f'sl_f_{k}')
            b.setProperty('og', 'filterbtn')
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, kk=k: on_pick(kk))
            if k == 'OPTION':   # 如實講：冇枚舉接口 → L1 係股票/ETF 候選
                b.setToolTip(t('sl_option_note', self._lang))
            elif k == 'WARRANT':
                b.setToolTip(t('sl_drill_hint', self._lang))
            grp.addButton(b)
            layout.addWidget(b)
            store[k] = b
        return grp

    # ── filter / mode 切換 ──
    @staticmethod
    def _mode_for_type(type_):
        return {'WARRANT': 'w1', 'OPTION': 'o1'}.get(type_, 'flat')

    def _set_filter(self, market=None, type_=None):
        if market is not None:
            self._market = market
        if type_ is not None:
            self._type = type_
            self._mode = self._mode_for_type(type_)   # 轉種類 = 重新入下鑽起點
            self._drill = []
        self._sync_checked()
        self._save_state()
        self._refresh()

    def _sync_checked(self):
        self._mkt_btns[self._market].setChecked(True)
        self._type_btns[self._type].setChecked(True)

    # ── 下鑽導航 ──
    def _on_cell_clicked(self, row, _col):
        if not self.model._rows:
            return
        r = self.model._rows[row]
        if self._mode == 'w1':
            if r.get('_pseudo'):
                self._drill.append({'owner': '', 'market': r['_pseudo']})   # 美股窩輪 → 直接 L3 平鋪
                self._mode = 'w3'
            else:
                self._drill.append({'owner': r['code']})
                self._mode = 'w2'
            self._refresh()
        elif self._mode == 'w2':
            self._drill.append({'wtype': r['wtype']})
            self._mode = 'w3'
            self._refresh()
        elif self._mode == 'o1':
            self._open_chain(r['code'])

    def _back(self):
        if self._mode == 'w3':
            self._drill.pop()
            self._mode = 'w2' if self._drill else 'w1'
        elif self._mode == 'w2':
            self._drill.pop()
            self._mode = 'w1'
        elif self._mode == 'o2':
            self._drill.pop()
            self._mode = 'o1'
        elif self._mode in ('w1', 'o1'):
            self._type = 'ALL'
            self._mode = 'flat'
            self._sync_checked()
            self._save_state()
        self._refresh()

    def _open_chain(self, code):
        if code in self._chain_cache:
            self._drill.append({'code': code})
            self._mode = 'o2'
            self._refresh()
            return
        if self._chain_worker is not None and self._chain_worker.isRunning():
            return   # 一條鏈未返 — 唔併發開第二條（防 OpenD 重入）
        self._chain_worker = _ChainWorker(code, self._opt_fetcher(), self)
        self._chain_worker.progress.connect(
            lambda i, total, d: self.sl_status.setText(
                f'⏳ {t("sl_chain_loading", self._lang)} {code} {i}/{total} {d}…'))
        self._chain_worker.done.connect(self._on_chain_done)
        self._chain_worker.start()

    def _on_chain_done(self, ok, msg, rows):
        code = self._chain_worker._code
        if ok:
            self._chain_cache[code] = rows
            self._drill.append({'code': code})
            self._mode = 'o2'
        self._refresh()   # 🤖 先 refresh — 唔然 status 會俾 _update_counts 蓋住（同 _on_update_done 同一教訓）
        if ok and not rows:
            self.sl_status.setText(f'⚠️ {t("sl_chain_none", self._lang)}：{code}')
        elif not ok:
            self.sl_status.setText(f'❌ {msg}')   # 留喺 o1 — 如實失敗，唔入空鏈頁

    # ── 各 mode 嘅顯示行 ──
    def _visible_rows(self):
        d = self._dir()
        q = self.sl_search.text().strip()
        rows = d.search(q, limit=10 ** 9, types=None) if q else list(d.entries)
        if self._market != 'ALL':
            rows = [e for e in rows if str(e.get('market', '')).upper() == self._market]
        if self._type != 'ALL':
            rows = [e for e in rows if e.get('type') == self._type]
        return rows

    def _warrants(self):
        d = self._dir()
        ws = [e for e in d.entries if e.get('type') == 'WARRANT']
        if self._market != 'ALL':
            ws = [e for e in ws if str(e.get('market', '')).upper() == self._market]
        return ws

    def _rows_w1(self):
        """有窩輪嘅標的（按 owner 分組 + 類別計數）；無 owner（US 實測）→ 偽行平鋪。"""
        d = self._dir()
        groups = {}
        for e in self._warrants():
            owner = str(e.get('owner', ''))
            g = groups.setdefault(owner, Counter())
            g[str(e.get('wtype', '')).upper() or 'OTHER'] += 1
        rows = []
        for owner, cnt in groups.items():
            if not owner:
                continue
            rows.append({'code': owner, 'entry': d.get(owner), 'total': sum(cnt.values()),
                         'CALL': cnt.get('CALL', 0), 'PUT': cnt.get('PUT', 0),
                         'BULL': cnt.get('BULL', 0), 'BEAR': cnt.get('BEAR', 0),
                         'OTHER': sum(v for k, v in cnt.items() if k not in WTYPE_ORDER[:4])})
        rows.sort(key=lambda r: -r['total'])
        no_owner = sum(sum(c.values()) for o, c in groups.items() if not o)
        if no_owner:   # 如實：呢個市場嘅窩輪冇所屬標的數據（US）→ 單筆偽行入 L3
            mkt = self._market if self._market != 'ALL' else 'US'
            rows.append({'code': '—', 'name': t('sl_us_warrant', self._lang),
                         '_pseudo': mkt, 'total': no_owner, 'OTHER': no_owner})
        return self._qfilter(rows, 'code name')

    def _rows_w2(self):
        owner = self._drill[-1]['owner']
        cnt = Counter(str(e.get('wtype', '')).upper() or 'OTHER'
                      for e in self._warrants() if str(e.get('owner', '')) == owner)
        rows = [{'wtype': wt, 'count': cnt[wt]} for wt in WTYPE_ORDER if cnt.get(wt)]
        return rows

    def _rows_w3(self):
        rows = [e for e in self._warrants() if str(e.get('owner', '')) == self._drill[0]['owner']]
        if len(self._drill) >= 2:   # drill = [owner, wtype]（w2 點擊先至有第二層）
            wt = self._drill[1]['wtype']
            rows = [e for e in rows
                    if (str(e.get('wtype', '')).upper() or 'OTHER') == wt]
        return self._qfilter(rows, 'code name')

    def _rows_o1(self):
        d = self._dir()
        q = self.sl_search.text().strip()
        rows = d.search(q, limit=10 ** 9, types=('STOCK', 'ETF')) if q else \
            [e for e in d.entries if e.get('type') in ('STOCK', 'ETF')]
        if self._market != 'ALL':
            rows = [e for e in rows if str(e.get('market', '')).upper() == self._market]
        return rows

    def _rows_o2(self):
        rows = self._chain_cache.get(self._drill[-1]['code'], [])
        return self._qfilter(rows, 'code name')

    def _qfilter(self, rows, fields):
        """mode 內本地 substring 過濾（模糊輸入喺下鑽頁照樣有用；唔再經 index 打分）。
           兩邊 t2s 正規化 — 顯示名可能係港式繁體（滙豐），用戶打匯豐都要 match 到。"""
        q = self.sl_search.text().strip()
        if not q:
            return rows
        ql = to_simplified(q).lower()
        keys = fields.split()
        return [r for r in rows
                if any(ql in to_simplified(str(self._cell(r, k))).lower() for k in keys)]

    def _refresh(self):
        self._sync_checked()
        rows = {'flat': self._visible_rows, 'w1': self._rows_w1, 'w2': self._rows_w2,
                'w3': self._rows_w3, 'o1': self._rows_o1, 'o2': self._rows_o2}[self._mode]()
        self.model.set_view(SCHEMAS[self._mode], rows)
        self._configure_columns()
        self._update_nav()
        self._update_counts()

    def _configure_columns(self):
        hh = self.sl_table.horizontalHeader()
        cols = SCHEMAS[self._mode]
        stretch = cols.index('name') if 'name' in cols else len(cols) - 1
        for i in range(len(cols)):
            hh.setSectionResizeMode(i, QHeaderView.Stretch if i == stretch
                                   else QHeaderView.ResizeToContents)

    def _update_nav(self):
        flat = self._mode == 'flat'
        self.sl_back_btn.setVisible(not flat)
        sep = ' ▸ '
        if flat:
            self.sl_crumb.setText('')
            return
        d = self._dir()
        if self._mode == 'w1':
            self.sl_crumb.setText(t('sl_type_warrant', self._lang) + sep + t('sl_drill_hint', self._lang))
        elif self._mode in ('w2', 'w3'):
            owner = self._drill[0]['owner']
            label = d.display_name(owner, self._lang) or owner
            seg = t('sl_type_warrant', self._lang) + sep + (label or t('sl_us_warrant', self._lang))
            if self._mode == 'w3' and len(self._drill) >= 2:
                seg += sep + self._wtype_label(self._drill[1]['wtype'])
            self.sl_crumb.setText(seg + sep + t('sl_drill_hint', self._lang) if self._mode == 'w2' else seg)
        elif self._mode == 'o1':
            self.sl_crumb.setText(t('sl_type_option', self._lang) + sep + t('sl_option_note', self._lang))
        elif self._mode == 'o2':
            code = self._drill[-1]['code']
            self.sl_crumb.setText(t('sl_type_option', self._lang) + sep + code)

    # ── 單元格格式化（model 回調）──
    def _cell(self, row, key):
        if key == 'name':
            lang = self._lang   # display_for 直接食 GUI 語言碼（繁/簡/英自動切換）
            if row.get('entry'):                       # w1 匯總行 → 所屬標的 entry
                return display_for(row['entry'], lang)
            if 'name_zh' in row or 'name_en' in row:  # 直接就係 index entry（flat/w3）
                return display_for(row, lang)
            return str(row.get('name', ''))            # 偽行 / 期權鏈 row（自帶 name）
        if key == 'type':
            return self._type_label(row.get('type', ''))
        if key == 'wtype':
            return self._wtype_label(row.get('wtype', ''))
        if key == 'otype':
            ot = str(row.get('otype', '')).upper()
            return t(OTYPE_KEYS[ot], self._lang) if ot in OTYPE_KEYS else ot
        if key in NUMERIC_COLS:
            v = row.get(key)
            return '' if v is None else (f'{int(v):,}' if key in ('total', 'count') else str(v))
        return str(row.get(key, ''))

    def _type_label(self, ty):
        return t(TYPE_KEYS[ty], self._lang) if ty in TYPE_KEYS else str(ty)

    def _wtype_label(self, wt):
        wt = str(wt).upper()
        if wt in WTYPE_KEYS:
            return t(WTYPE_KEYS[wt], self._lang)
        return t('sl_w_other', self._lang) if not wt else str(wt)

    def _update_counts(self):
        # 全 index 計數（唔跟 filter — 底部係「宇宙」概覽）+ 目前顯示筆數 + 更新時間
        by = {}
        for e in self._dir().entries:
            by.setdefault(str(e.get('market', '?')), Counter())[str(e.get('type', '?'))] += 1
        parts = []
        for mkt in sorted(by):
            seg = ' · '.join(f'{self._type_label(ty)} {n:,}' for ty, n in sorted(by[mkt].items()))
            parts.append(f'{mkt}: {seg}')
        total = sum(sum(c.values()) for c in by.values())
        self.sl_counts.setText(
            '　‖　'.join(parts) + f'　‖　{t("sl_total", self._lang)} {total:,}')
        fetched = self._dir().fetched_at or '—'
        self.sl_status.setText(
            f'{t("sl_showing", self._lang)} {self.model.rowCount():,}　·　'
            f'{t("sl_updated_at", self._lang)} {fetched}')

    # ── 一鍵更新 ──
    def _on_update_clicked(self):
        if self._worker is not None and self._worker.isRunning():
            return
        self.sl_update_btn.setEnabled(False)
        self.sl_status.setText(f'⏳ {t("sl_updating", self._lang)}')
        self._worker = _FetchWorker(self._dir(), self)
        self._worker.progress.connect(
            lambda label, count: self.sl_status.setText(f'⏳ {label} +{count:,}'))
        self._worker.done.connect(self._on_update_done)
        self._worker.start()

    def _on_update_done(self, ok, msg):
        self.sl_update_btn.setEnabled(True)
        if ok:
            self._chain_cache.clear()   # index 換咗 → 鏈 cache 一併作廢
            self._refresh()   # entries 已喺 fetch 內 merge + save → 表格/計數跟新
        # result 放最後先至睇到 — _refresh 會將 status 寫返「顯示 N · 更新於…」（下次 filter 就蓋返）
        self.sl_status.setText(f'{"✅" if ok else "❌"} {msg}')

    # ── 本地記憶（filter 選擇；下鑽路徑屬即時狀態，唔持久化）──
    def _save_state(self):
        state_store.save_section('symbol_list', {'market': self._market, 'type': self._type})

    # ── theme（照 quotes_page recipe）──
    def _apply_theme_qss(self, name):
        pal = theme_mod.THEMES[name]
        self.setStyleSheet(string.Template(_PAGE_QSS).substitute(
            window=pal['window'], surface=pal['surface'], card=pal['card'],
            border=pal['border'], text=pal['text'], muted=pal['muted'],
            accent=pal['accent'], accent_pressed=pal['accent_pressed']))

    def _on_theme_changed(self, name):
        self._apply_theme_qss(name)

    # ── i18n ──
    def _retranslate_widgets(self):
        lang = self._lang
        apply_text(self, _TEXT, lang)   # 一鍵更新 / 返回掣（objectName → i18n key）
        for name, key in _PH.items():   # placeholder 唔屬 setText
            getattr(self, name).setPlaceholderText(t(key, lang))
        for k, b in self._mkt_btns.items():   # FILTER 掣按 registry 動態生成 → 逐個 setText
            b.setText(t(MARKET_KEYS[k], lang))
        for k, b in self._type_btns.items():
            b.setText(t(TYPE_KEYS[k], lang))
            if k == 'OPTION':
                b.setToolTip(t('sl_option_note', lang))
            elif k == 'WARRANT':
                b.setToolTip(t('sl_drill_hint', lang))

    def retranslate(self, lang):
        self._lang = lang
        self._retranslate_widgets()
        self._refresh()   # rows（偽行名/面包屑/計數）+ headerData 全部跟語言重建


_PAGE_QSS = """
QWidget#symbol_list_page { background-color: $window; }
QLineEdit#sl_search { background-color: $card; color: $text; border: 1px solid $border;
    border-radius: 4px; padding: 5px 8px; font-size: 13px; }
QPushButton[og="slbtn"], QPushButton[og="filterbtn"] { color: $muted; background: transparent;
    border: 1px solid $border; border-radius: 4px; padding: 4px 10px; font-size: 12px; }
QPushButton[og="slbtn"]:hover, QPushButton[og="filterbtn"]:hover { color: $text; border-color: $accent; }
QPushButton[og="slbtn"]:pressed { background-color: $accent_pressed; color: #FFFFFF; }
QPushButton[og="filterbtn"]:checked { color: #FFFFFF; background-color: $accent; border-color: $accent; font-weight: bold; }
QTableView#sl_table { background-color: $surface; alternate-background-color: $card;
    color: $text; border: 1px solid $border; gridline-color: $border; font-size: 12px;
    selection-background-color: $accent; selection-color: #FFFFFF; }
QHeaderView::section { background-color: $card; color: $muted; border: 1px solid $border;
    padding: 4px; font-weight: bold; }
QLabel#sl_counts { color: $muted; font-size: 11px; }
QLabel#sl_status { color: $muted; font-size: 11px; }
QLabel#sl_crumb { color: $muted; font-size: 12px; }
"""


if __name__ == '__main__':
    from gateway.pages.base_page import run_standalone
    run_standalone(SymbolListPage, 'page_symbol_list_title')

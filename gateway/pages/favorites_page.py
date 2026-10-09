"""One Gate Page 6 — 標的收藏管理：本地 JSON 儲存 + 市場/種類 FILTER + 新增/刪除。

- **儲存**：gateway/favorites.py（state_store section 'favorites' — 統一 JSON 檔）。
  entry = {code, market, type, added}；name 唔存 — 顯示時跟語言由本地 index 即時解析
  （index 冇就如實 '—'，market/type 照 snapshot 顯示）。其他功能以後 load_items() 即用。
- **表格**：code / 名稱（跟語言）/ 市場 / 種類 / 加入日；欄頭排序；行可多選（刪除用）。
- **FILTER**：兩組 exclusive 按鈕（市場 × 種類），recipe 同標的列表頁（共用 MARKETS/TYPES 常量）；
  選擇記憶 → favorites section。type 'UNKNOWN'（index 冇嘅 code）只喺 ALL 出現 — 如實。
- **新增**：輸入框 = `gateway/symbol_input` 模糊輸入（debounce → directory.search(types=None) 純本地，
  候選 =「CODE  名稱」→ 打中文名都有得揀；收藏唔限種類）；
  新增時 index 有 entry → snapshot market/type；冇 → market 由 code prefix 兜底、type UNKNOWN；
  完全唔似 code 又搵唔到 → ❌ 如實。重複 → ⚠️。
- **刪除**：所選行 → 🗑 刪除所選（多選：Ctrl/Shift）。
- **底部**：收藏總數 / 顯示筆數 + status（set status 必須喺 refresh 之後 — #10/#11 教訓）。
- **排版**：`gateway/ui/favorites_page.ui`（Designer 可調）；FILTER 按鈕數量屬資料 → 由
  `MARKETS`/`TYPES` 生成後填進 `.ui` 預留嘅 `mktSlot`/`typeSlot`。
- **Theme/i18n**：照其他頁 recipe。

單獨運行：`python gateway/pages/favorites_page.py`。
"""
import os
import re
import string
import sys

# ── standalone bootstrap（同其他頁同一 convention；package mode 下 no-op）──
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt, QAbstractTableModel  # noqa: E402
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup,  # noqa: E402
                               QHeaderView, QPushButton, QWidget)

import gateway.favorites as favorites  # noqa: E402
import gateway.theme as theme_mod  # noqa: E402
from gateway.i18n import DEFAULT_LANG, t  # noqa: E402
from gateway.pages.symbol_list_page import (MARKETS, MARKET_KEYS, TYPES,  # noqa: E402
                                            TYPE_KEYS)  # 共用 FILTER 常量（單一事實來源）
from gateway.symbol_input import attach_symbol_input  # noqa: E402 — 全域模糊輸入
from gateway.ui.bind import apply_text, stamp  # noqa: E402
from gateway.ui.loader import apply_ui  # noqa: E402

COLUMNS = ('code', 'name', 'market', 'type', 'added')
HEAD_KEYS = {'code': 'sl_head_code', 'name': 'sl_head_name', 'market': 'sl_head_market',
             'type': 'sl_head_type', 'added': 'fav_head_added'}
_CODE_LIKE = re.compile(r'^(HK|US)\.[A-Z0-9]+$', re.I)   # prefix 兜底准入格式

# `.ui` 入面嘅靜態 widget：QSS property（Designer 帶唔住）+ 文字來源（見 gateway/ui/bind.py）
_STAMP = {'fav_add_btn': {'og': 'favbtn'}, 'fav_remove_btn': {'og': 'favbtn'},
          'fav_page_note': {'role': 'pagebody'}}
_TEXT = {'fav_add_btn': 'fav_add', 'fav_remove_btn': 'fav_remove',
         'fav_page_note': 'fav_page_note'}
_PH = {'fav_add_edit': 'fav_add_ph'}


class _FavModel(QAbstractTableModel):
    """(COLUMNS, rows) → 表格；排序喺呢度做（全部字串欄）。"""

    def __init__(self, page):
        super().__init__()
        self._page = page
        self._rows = []

    def set_rows(self, rows):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def row_at(self, i):
        return self._rows[i]

    def rowCount(self, parent=None):
        return len(self._rows)

    def columnCount(self, parent=None):
        return len(COLUMNS)

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and index.isValid():
            return self._page._cell(self._rows[index.row()], COLUMNS[index.column()])
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return t(HEAD_KEYS[COLUMNS[section]], self._page._lang)
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        key = COLUMNS[column]
        self._rows.sort(key=lambda r: str(self._page._cell(r, key)),
                        reverse=(order == Qt.DescendingOrder))
        self.layoutChanged.emit()


class FavoritesPage(QWidget):
    def __init__(self, directory=None):
        super().__init__()
        apply_ui(self, 'favorites_page')    # 排版喺 .ui（Designer 可調 margin / 行高 / 兩組 FILTER 位置）
        stamp(self, _STAMP)
        self._lang = DEFAULT_LANG
        self._directory = directory          # e2e 注入 fake；None → lazy get_directory()（只讀 cache）

        mkt, ty = favorites.load_filter()
        self._market = mkt if mkt in MARKETS else 'ALL'
        self._type = ty if ty in TYPES else 'ALL'

        # FILTER 兩組（exclusive 各組；recipe 同標的列表頁）— 按鈕數量屬資料，填進 `.ui` 嘅 slot
        self._mkt_btns, self._type_btns = {}, {}
        self._mk_group(self.mktSlot, MARKETS, MARKET_KEYS, self._mkt_btns,
                       lambda m: self._set_filter(market=m))
        self._mk_group(self.typeSlot, TYPES, TYPE_KEYS, self._type_btns,
                       lambda ty: self._set_filter(type_=ty))
        self._connect_signals()
        self._setup_table()

        self._sync_checked()
        theme_mod.add_listener(self._on_theme_changed)
        self._apply_theme_qss(theme_mod.CURRENT)
        self._refresh()

    def _connect_signals(self):
        # 🤖 模糊輸入一律經 gateway/symbol_input（debounce → 本地 index 候選）
        self.completer = attach_symbol_input(self.fav_add_edit, self._candidates,
                                             lang=self._lang)
        self.fav_add_edit.returnPressed.connect(self._on_add_clicked)
        self.fav_add_btn.clicked.connect(self._on_add_clicked)
        self.fav_remove_btn.clicked.connect(self._on_remove_clicked)

    def _setup_table(self):
        self.model = _FavModel(self)
        self.fav_table.setModel(self.model)
        self.fav_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.fav_table.setSelectionMode(QAbstractItemView.ExtendedSelection)   # 多選 → 刪除
        self.fav_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.fav_table.setSortingEnabled(True)
        self.fav_table.verticalHeader().setVisible(False)

    # ── lazy index（只讀 cache，唔會自動開網絡）──
    def _dir(self):
        if self._directory is None:
            import modules.symbol_search as ss
            self._directory = ss.get_directory()
        return self._directory

    def _mk_group(self, layout, keys, i18n_keys, store, on_pick):
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        for k in keys:
            b = QPushButton(t(i18n_keys[k], self._lang))
            b.setObjectName(f'fav_f_{k}')
            b.setProperty('og', 'filterbtn')
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, kk=k: on_pick(kk))
            grp.addButton(b)
            layout.addWidget(b)
            store[k] = b
        return grp

    # ── filter ──
    def _set_filter(self, market=None, type_=None):
        if market is not None:
            self._market = market
        if type_ is not None:
            self._type = type_
        self._sync_checked()
        favorites.save_filter(self._market, self._type)
        self._refresh()

    def _sync_checked(self):
        self._mkt_btns[self._market].setChecked(True)
        self._type_btns[self._type].setChecked(True)

    # ── 新增 / 刪除 ──
    def _candidates(self, q):
        """模糊輸入搜尋來源（attach_symbol_input call）：本地 index lazy load、只讀 cache；
           types=None = 全種類（收藏唔限指數/期貨/窩輪）；已係準確 code → 冇候選（唔彈窗）。"""
        d = self._dir()
        return [] if d.has_code(q) else d.search(q, types=None)

    def _on_add_clicked(self):
        raw = self.fav_add_edit.text().strip()
        if not raw:
            return
        entry = self._dir().get(raw)
        code = entry['code'] if entry else raw.upper()
        if entry is None and not _CODE_LIKE.match(code):
            self.fav_status.setText(t('fav_bad_code', self._lang))   # status 唔使等 refresh
            return
        ok, msg_key, item = favorites.add(code, entry)
        self._refresh()
        if ok:
            self.fav_add_edit.clear()
            self.fav_status.setText(f'✅ {item["code"]} — {t(msg_key, self._lang)}')
        else:
            self.fav_status.setText(f'⚠️ {code} — {t(msg_key, self._lang)}')

    def _on_remove_clicked(self):
        rows = {idx.row() for idx in self.fav_table.selectionModel().selectedRows()}
        if not rows:
            self.fav_status.setText(f'⚠️ {t("fav_no_sel", self._lang)}')
            return
        codes = [self.model.row_at(r)['code'] for r in rows]
        favorites.remove(codes)
        self._refresh()   # 🤖 set status 必須喺 refresh 之後（_update_counts 會蓋住）
        self.fav_status.setText(f'🗑 {t("fav_removed", self._lang)} {len(codes)}')

    # ── 顯示 ──
    def _visible_rows(self):
        rows = []
        for e in favorites.load_items():
            if self._market != 'ALL' and str(e.get('market', '')) != self._market:
                continue
            if self._type != 'ALL' and str(e.get('type', '')) != self._type:
                continue
            rows.append(e)
        return rows

    def _refresh(self):
        self.model.set_rows(self._visible_rows())
        self._configure_columns()
        self._update_counts()

    def _configure_columns(self):
        hdr = self.fav_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        widths = {0: 160, 1: 240, 2: 70, 3: 90, 4: 100}
        for col, w in widths.items():
            self.fav_table.setColumnWidth(col, w)

    def _cell(self, row, key):
        if key == 'name':
            name = self._dir().display_name(row.get('code', ''), self._lang)
            return name or '—'
        if key == 'type':
            return self._type_label(row.get('type', ''))
        if key == 'market':
            return str(row.get('market', '') or '—')
        return str(row.get(key, ''))

    def _type_label(self, ty):
        return t(TYPE_KEYS[ty], self._lang) if ty in TYPE_KEYS else str(ty)

    def _update_counts(self):
        total = len(favorites.load_items())
        self.fav_counts.setText(
            f'{t("fav_count", self._lang)} {total:,}　‖　'
            f'{t("fav_showing", self._lang)} {self.model.rowCount():,}')
        if total == 0:
            self.fav_status.setText(t('fav_empty', self._lang))

    # ── theme / i18n ──
    def _apply_theme_qss(self, name):
        pal = theme_mod.THEMES[name]
        self.setStyleSheet(string.Template(_PAGE_QSS).substitute(
            window=pal['window'], surface=pal['surface'], card=pal['card'],
            border=pal['border'], text=pal['text'], muted=pal['muted'],
            accent=pal['accent'], accent_pressed=pal['accent_pressed']))

    def _on_theme_changed(self, name):
        self._apply_theme_qss(name)

    def retranslate(self, lang):
        self._lang = lang
        self.completer.lang = lang   # 🤖 dropdown 名稱跟語言（display_for 直接食 GUI 語言碼）
        apply_text(self, _TEXT, lang)
        for obj, key in _PH.items():
            getattr(self, obj).setPlaceholderText(t(key, lang))
        for k, b in self._mkt_btns.items():
            b.setText(t(MARKET_KEYS[k], lang))
        for k, b in self._type_btns.items():
            b.setText(t(TYPE_KEYS[k], lang))
        self._refresh()   # headerData / type label / counts 全部跟語言重建


_PAGE_QSS = """
QWidget#favorites_page { background-color: $window; }
QLineEdit#fav_add_edit { background-color: $card; color: $text; border: 1px solid $border;
    border-radius: 4px; padding: 5px 8px; font-size: 13px; }
QPushButton[og="favbtn"], QPushButton[og="filterbtn"] { color: $muted; background: transparent;
    border: 1px solid $border; border-radius: 4px; padding: 4px 10px; font-size: 12px; }
QPushButton[og="favbtn"]:hover, QPushButton[og="filterbtn"]:hover { color: $text; border-color: $accent; }
QPushButton[og="favbtn"]:pressed { background-color: $accent_pressed; color: #FFFFFF; }
QPushButton[og="filterbtn"]:checked { color: #FFFFFF; background-color: $accent; border-color: $accent; font-weight: bold; }
QTableView#fav_table { background-color: $surface; alternate-background-color: $card;
    color: $text; border: 1px solid $border; gridline-color: $border; font-size: 12px;
    selection-background-color: $accent; selection-color: #FFFFFF; }
QHeaderView::section { background-color: $card; color: $muted; border: 1px solid $border;
    padding: 4px; font-weight: bold; }
QLabel#fav_counts, QLabel#fav_status { color: $muted; font-size: 11px; }
"""


if __name__ == '__main__':
    from gateway.pages.base_page import run_standalone
    run_standalone(FavoritesPage, 'page_favorites_title')

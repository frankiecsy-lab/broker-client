"""One Gate Page 8 — 策略管理：CRUD + 分數制條件編輯，本地 JSON 儲存，兼容日後 BACKTEST。

- **儲存**：gateway/strategies.py（state_store section 'strategies'）。entry = {id, name,
  buy/sell = [rule], entry_price:'market', qty:'min_lot', mark_buffer}。
  #32：刪 **標的／生效期**（連狀態／創建日）— 策略 = 純規則集，套用返晒全部格（#30）。
  #29/#31：表單有 **訊號BUFFER spinbox**（0–200，預設 10）— 對上一個訊號相隔 ≤N 條 bar
  嘅後續 B/S 轉純文字紅/綠。條件 rule = 純 JSON（type/side/params/score）→ backtest 直接食。
- **分數制**（用戶要求）：每條條件有分（1–100）；買／賣各自累加 ≥100 分即觸發。
  表單每邊 = 類型 combo（MA／BOLL／VOB）+ 方向 + 動態參數欄（範圍經 CondParamSpec，
  recipe 同指標頁）+ 分數 SpinBox + 「＋ 加條件」；已加條件逐條列出可 ✕ 移除。
- **表格**：名稱／買賣條件摘要（符號化語言中立）三欄；欄頭排序；行多選（刪除用）；
  揀行 → 載入表單編輯。
- **固定欄**（用戶要求 v1）：買入價＝市價、數量＝最低一手 → read-only label（存 mode enum，
  執行／backtest 時先 resolve 實數）。
- **排版**：`gateway/ui/strategies_page.ui`（Designer 可調）— 表、表單欄、買／賣兩個 GroupBox、
  按鈕行、底部行全部喺 `.ui`；**已加條件逐條**嘅排版另立 `gateway/ui/strategy_rule_row.ui`（模板，
  逐個 `load_ui`）。**數量**先屬資料：動態參數欄按 `CONDITION_DEFS`、條件條數按 `_draft` →
  由本檔填進 `.ui` 預留嘅 `str_<side>_paramSlot` / `str_<side>_rulesSlot`（加條件類型唔使改 `.ui`）。
  `_CondEditor` 因此係**行為控制器**，唔再自己砌控件。
- **Theme/i18n**：照其他頁 recipe（`gateway/ui/bind.py` 嘅 `stamp` / `apply_text`）。

單獨運行：`python gateway/pages/strategies_page.py`。
"""
import os
import string
import sys

# ── standalone bootstrap（同其他頁同一 convention；package mode 下 no-op）──
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt, QAbstractTableModel  # noqa: E402
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDoubleSpinBox,  # noqa: E402
                               QHeaderView, QLabel, QSpinBox, QWidget)

import gateway.strategies as strategies  # noqa: E402
import gateway.theme as theme_mod  # noqa: E402
from gateway.i18n import DEFAULT_LANG, t  # noqa: E402
from gateway.ui.bind import apply_text, stamp  # noqa: E402
from gateway.ui.loader import apply_ui, load_ui  # noqa: E402

COLUMNS = ('name', 'buy', 'sell')   # #32：刪 code/validity/status/created
HEAD_KEYS = {'name': 'str_head_name', 'buy': 'str_head_buy', 'sell': 'str_head_sell'}
_TYPE_KEYS = {'ma_cross': 'str_type_ma_cross', 'boll_cross': 'str_type_boll_cross',
              'vob_break': 'str_type_vob_break'}
_SIDE_KEYS = {'above': 'str_side_above', 'below': 'str_side_below'}
_LINE_KEYS = {'upper': 'str_line_upper', 'mid': 'str_line_mid', 'lower': 'str_line_lower'}

# `.ui` 入面嘅靜態 widget：QSS property（Designer 帶唔住）+ 文字來源（見 gateway/ui/bind.py）
# （str_<side>_scorelbl 原本就冇 og → 唔入表，跟預設 label 色）
_STAMP = {
    'strategies_page': {},   # bare QWidget 要 WA_StyledBackground 先食到頁面級背景 QSS
    'str_name_lbl': {'og': 'strlbl'}, 'str_buffer_lbl': {'og': 'strlbl'},
    'str_price_lbl': {'og': 'strfixed'}, 'str_qty_lbl': {'og': 'strfixed'},
    'str_buy_add_btn': {'og': 'strbtn'}, 'str_sell_add_btn': {'og': 'strbtn'},
    'str_add_btn': {'og': 'strbtn'}, 'str_save_btn': {'og': 'strbtn'},
    'str_remove_btn': {'og': 'strbtn'}, 'str_clear_btn': {'og': 'strbtn'},
    'str_page_note': {'role': 'pagebody'}, 'str_score_note': {'role': 'usagehint'},
}
_TEXT = {'str_name_lbl': 'str_name_lbl', 'str_buffer_lbl': 'str_buffer_lbl',
         'str_price_lbl': 'str_price_lbl', 'str_qty_lbl': 'str_qty_lbl',
         'str_add_btn': 'str_add_btn', 'str_save_btn': 'str_save_btn',
         'str_remove_btn': 'str_remove_btn', 'str_clear_btn': 'str_clear_btn',
         # 兩邊 GroupBox 嘅固定文案（objectName 已喺 `.ui` 固定 → 唔使喺 code 逐邊 set）
         'str_buy_group': 'str_buy_title', 'str_sell_group': 'str_sell_title',
         'str_buy_scorelbl': 'str_score_lbl', 'str_sell_scorelbl': 'str_score_lbl',
         'str_buy_add_btn': 'str_add_rule', 'str_sell_add_btn': 'str_add_rule',
         'str_page_note': 'str_page_note', 'str_score_note': 'str_score_note'}
_PH = {'str_name_edit': 'str_name_ph'}   # placeholder 唔屬 setText → 单独一行 loop
# `strategy_rule_row.ui` 嘅模板名（每條 draft 一份，load 後先改做 str_<side>_rule_{i}）
_ROW_STAMP = {'rule_lbl': {'og': 'strrule'}, 'rule_del_btn': {'og': 'strrule'}}


class _StrModel(QAbstractTableModel):
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


class _CondEditor:
    """一邊（buy/sell）條件編輯器嘅**行為**：控件全部由 `.ui` 建出（objectName `str_<side>_*`），
    呢度只管 combo 內容、動態參數欄、分數範圍同 draft 列表（數量屬資料 → 填進
    `str_<side>_paramSlot` / `str_<side>_rulesSlot`；排版屬 `.ui`）。"""

    def __init__(self, side, page):
        self._side = side
        self._page = page
        self._draft = []
        self._def_keys = list(strategies.CONDITION_DEFS)

        def w(name):   # objectName 即身份契約（QUiLoader 將控件掛做 page attribute）
            return getattr(page, f'str_{side}_{name}')

        self.type_combo = w('type')
        self.side_combo = w('side')
        self.param_slot = w('paramSlot')
        self.score_spin = w('score')
        self.add_btn = w('add_btn')
        self.rules_slot = w('rulesSlot')
        self._param_widgets = {}
        self._param_lbls = {}

        # combo 嘅「項目」屬資料（CONDITION_DEFS / SIDES）；文字屬語言 → retranslate 先填
        for k in self._def_keys:
            self.type_combo.addItem('', k)
        for s in strategies.SIDES:
            self.side_combo.addItem('', s)
        self.score_spin.setRange(strategies.SCORE_LO, strategies.SCORE_HI)
        self.score_spin.setValue(50)

        self.type_combo.currentIndexChanged.connect(self._rebuild_params)
        self.add_btn.clicked.connect(self._on_add_rule)
        self._rebuild_params()

    # ── 動態參數欄（range/choices 經 CondParamSpec；recipe 同指標頁）──
    def _cur_def(self):
        return strategies.CONDITION_DEFS[self.type_combo.currentData()]

    def _clear_layout(self, lay):
        while lay.count():
            item = lay.takeAt(0)   # 🤖 連 spacer/sub-layout 都要清，唔係淨係 setParent(None) widget
            w = item.widget()
            if w:
                w.setParent(None)
            elif item.layout():
                self._clear_layout(item.layout())

    def _rebuild_params(self, *_):
        lang = self._page._lang
        side = self._side
        self._clear_layout(self.param_slot)
        self._param_widgets, self._param_lbls = {}, {}
        for p in self._cur_def().params:
            lbl = QLabel(t(p.label_key, lang))
            lbl.setObjectName(f'str_{side}_paramlbl_{p.key}')
            lbl.setProperty('og', 'strparamlbl')
            self.param_slot.addWidget(lbl)
            if p.choices:
                w = QComboBox()
                for c in p.choices:
                    w.addItem(t(_LINE_KEYS[c], lang), c)
                if p.default in p.choices:
                    w.setCurrentIndex(list(p.choices).index(p.default))
            elif p.is_int:
                w = QSpinBox()
                w.setRange(int(p.lo), int(p.hi))
                w.setValue(int(p.default))
            else:
                w = QDoubleSpinBox()
                w.setRange(float(p.lo), float(p.hi))
                w.setSingleStep(0.1)
                w.setDecimals(1)
                w.setValue(float(p.default))
            w.setObjectName(f'str_{side}_param_{p.key}')   # E2E hook
            w.setProperty('og', 'strparam')
            self.param_slot.addWidget(w)
            self._param_widgets[p.key] = w
            self._param_lbls[p.key] = lbl
        self.param_slot.addStretch(1)

    def current_rule(self):
        params = {}
        for p in self._cur_def().params:
            w = self._param_widgets[p.key]
            params[p.key] = w.currentData() if p.choices else w.value()
        return {'type': self.type_combo.currentData(), 'side': self.side_combo.currentData(),
                'params': params, 'score': self.score_spin.value()}

    # ── draft 列表 ──
    def _on_add_rule(self):
        self._draft.append(self.current_rule())
        self._render_rules()

    def draft(self):
        return [dict(r) for r in self._draft]

    def set_draft(self, rules):
        self._draft = [dict(r) for r in rules or []]
        self._render_rules()

    def _render_rules(self):
        """逐條 draft = 一個 `strategy_rule_row.ui`（排版）→ 填進 rulesSlot（數量屬資料）。
        objectName 要 load 之後先改（逐條要獨有名俾 E2E / 刪除用），所以 `stamp` 必須排喺改名前。"""
        self._clear_layout(self.rules_slot)
        for i, r in enumerate(self._draft):
            row = load_ui('strategy_rule_row', QWidget)
            stamp(row, _ROW_STAMP)
            row.setObjectName(f'str_{self._side}_rule_row_{i}')
            row.rule_lbl.setObjectName(f'str_{self._side}_rule_{i}')
            row.rule_lbl.setText(strategies.rule_summary(r))
            row.rule_del_btn.setObjectName(f'str_{self._side}_rule_{i}_del')
            row.rule_del_btn.clicked.connect(lambda _c=False, idx=i: self._del_rule(idx))
            self.rules_slot.addWidget(row)

    def _del_rule(self, idx):
        if 0 <= idx < len(self._draft):
            self._draft.pop(idx)
            self._render_rules()

    # ── i18n（靜態文案一律喺 `_TEXT` 表；呢度只剩 combo 嘅「項目」文字 = 資料）──
    def retranslate(self):
        lang = self._page._lang
        for i, k in enumerate(self._def_keys):
            d = strategies.CONDITION_DEFS[k]
            self.type_combo.setItemText(i, t(_TYPE_KEYS[k], lang))
            self.type_combo.setItemData(i, t(d.desc_key, lang), Qt.ToolTipRole)
        for i, s in enumerate(strategies.SIDES):
            self.side_combo.setItemText(i, t(_SIDE_KEYS[s], lang))
        self._rebuild_params()   # 參數 label / choices combo 跟語言


class StrategiesPage(QWidget):
    def __init__(self):
        super().__init__()
        apply_ui(self, 'strategies_page')   # 排版（表/表單欄/兩個 GroupBox/按鈕行）全部喺 `.ui`
        stamp(self, _STAMP)                 # og / WA_StyledBackground：Designer 帶唔住 dynamic property
        self._lang = DEFAULT_LANG
        self._mgr_inst = None
        self._sel_id = None

        self._setup_table()
        # spin 範圍屬業務資料（MARK_BUFFER_*）→ 留喺 code，`.ui` 只留空 spin
        self.str_buffer_spin.setRange(strategies.MARK_BUFFER_LO, strategies.MARK_BUFFER_HI)
        self.str_buffer_spin.setValue(strategies.MARK_BUFFER_DEFAULT)
        self.buy_editor = _CondEditor('buy', self)    # 綁 `.ui` 控件（str_buy_*）
        self.sell_editor = _CondEditor('sell', self)
        self._connect_signals()

        self._retranslate_widgets()
        theme_mod.add_listener(self._on_theme_changed)
        self._apply_theme_qss(theme_mod.CURRENT)
        self._refresh()

    # ── lazy manager / index ──
    def _mgr(self):
        if self._mgr_inst is None:
            self._mgr_inst = strategies.get_manager()
        return self._mgr_inst

    # ── 表格行為（控件本身喺 `.ui`）──
    def _setup_table(self):
        self.model = _StrModel(self)
        self.str_table.setModel(self.model)
        self.str_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.str_table.setSelectionMode(QAbstractItemView.ExtendedSelection)   # 多選 → 刪除
        self.str_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.str_table.setSortingEnabled(True)
        self.str_table.verticalHeader().setVisible(False)

    def _connect_signals(self):
        self.str_table.selectionModel().selectionChanged.connect(self._on_sel_changed)
        self.str_add_btn.clicked.connect(self._on_add)
        self.str_save_btn.clicked.connect(self._on_save)
        self.str_remove_btn.clicked.connect(self._on_remove)
        self.str_clear_btn.clicked.connect(self._clear_form)

    # ── 表格 → 表單 ──
    def _on_sel_changed(self, *_):
        rows = sorted({idx.row() for idx in self.str_table.selectionModel().selectedRows()})
        if not rows:
            return
        e = self.model.row_at(rows[0])
        self._sel_id = e['id']
        self.str_name_edit.setText(e['name'])
        self.str_buffer_spin.setValue(strategies.clamp_mark_buffer(e.get('mark_buffer')))
        self.buy_editor.set_draft(e['buy'])
        self.sell_editor.set_draft(e['sell'])
        self.str_status.setText(f'{t("str_sel_edit", self._lang)}{e["name"]}')

    def _clear_form(self):
        self._sel_id = None
        self.str_name_edit.clear()
        self.str_buffer_spin.setValue(strategies.MARK_BUFFER_DEFAULT)
        self.buy_editor.set_draft([])
        self.sell_editor.set_draft([])

    # ── CRUD ──
    def _collect(self):
        return (self.str_name_edit.text().strip(),
                self.buy_editor.draft(), self.sell_editor.draft(),
                self.str_buffer_spin.value())

    def _on_add(self):
        name, buy, sell, mark_buffer = self._collect()
        if not name:
            self.str_status.setText(t('str_bad_name', self._lang))
            return
        ok, msg, item = self._mgr().add(name, buy, sell, mark_buffer)
        self._refresh()
        if ok:
            self._clear_form()
            self.str_status.setText(f'{t(msg, self._lang)} — {item["name"]}')
        else:
            self.str_status.setText(t(msg, self._lang))

    def _on_save(self):
        if not self._sel_id:
            self.str_status.setText(f'⚠️ {t("str_no_sel", self._lang)}')
            return
        name, buy, sell, mark_buffer = self._collect()
        ok, msg = self._mgr().update(self._sel_id, name, buy, sell, mark_buffer)
        self._refresh()
        self.str_status.setText(f'{t(msg, self._lang)}' if ok else t(msg, self._lang))

    def _on_remove(self):
        rows = {idx.row() for idx in self.str_table.selectionModel().selectedRows()}
        if not rows:
            self.str_status.setText(f'⚠️ {t("str_no_sel", self._lang)}')
            return
        ids = [self.model.row_at(r)['id'] for r in rows]
        self._mgr().remove(ids)
        if self._sel_id in ids:
            self._clear_form()
        self._refresh()   # 🤖 set status 必須喺 refresh 之後
        self.str_status.setText(f'{t("str_removed", self._lang)} {len(ids)}')

    # ── 顯示 ──
    def _refresh(self):
        self.model.set_rows(self._mgr().items())
        self._configure_columns()
        self._update_counts()

    def _configure_columns(self):
        hdr = self.str_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        widths = {0: 160, 1: 260, 2: 260}   # #32：得返名稱／買／賣三欄
        for col, w in widths.items():
            self.str_table.setColumnWidth(col, w)

    def _cell(self, row, key):
        if key == 'buy':
            return strategies.rules_summary(row.get('buy', []))
        if key == 'sell':
            return strategies.rules_summary(row.get('sell', []))
        return str(row.get(key, ''))

    def _update_counts(self):
        total = len(self._mgr().items())
        self.str_counts.setText(f'{t("str_count", self._lang)} {total:,}')
        if total == 0:
            self.str_status.setText(t('str_empty', self._lang))

    # ── theme / i18n ──
    def _apply_theme_qss(self, name):
        pal = theme_mod.THEMES[name]
        self.setStyleSheet(string.Template(_PAGE_QSS).substitute(
            window=pal['window'], surface=pal['surface'], card=pal['card'],
            border=pal['border'], text=pal['text'], muted=pal['muted'],
            accent=pal['accent'], accent_pressed=pal['accent_pressed']))

    def _on_theme_changed(self, name):
        self._apply_theme_qss(name)

    def _retranslate_widgets(self):
        lang = self._lang
        apply_text(self, _TEXT, lang)   # 全部靜態文案：表單 label、動作掣、兩邊 GroupBox 標題/分數 label/加條件掣
        for name, key in _PH.items():   # placeholder 唔屬 setText
            getattr(self, name).setPlaceholderText(t(key, lang))
        self.buy_editor.retranslate()   # 淨屬資料：combo 項目文字 + 動態參數 label
        self.sell_editor.retranslate()

    def retranslate(self, lang):
        self._lang = lang
        self._retranslate_widgets()
        self._refresh()   # headerData / counts 全部跟語言重建


_PAGE_QSS = """
QWidget#strategies_page { background-color: $window; }
QLineEdit#str_name_edit { background-color: $card; color: $text;
    border: 1px solid $border; border-radius: 4px; padding: 5px 8px; font-size: 13px; }
QComboBox, QSpinBox, QDoubleSpinBox { background-color: $card; color: $text;
    border: 1px solid $border; border-radius: 4px; padding: 3px 6px; font-size: 12px; }
QGroupBox { color: $muted; border: 1px solid $border; border-radius: 4px;
    margin-top: 8px; font-size: 11px; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; }
QPushButton[og="strbtn"] { color: $muted; background: transparent; border: 1px solid $border;
    border-radius: 4px; padding: 4px 10px; font-size: 12px; }
QPushButton[og="strbtn"]:hover { color: $text; border-color: $accent; }
QPushButton[og="strbtn"]:pressed { background-color: $accent_pressed; color: #FFFFFF; }
QLabel[og="strlbl"], QLabel[og="strrule"], QLabel[og="strparamlbl"] { color: $muted; font-size: 11px; }
QLabel[og="strfixed"] { color: $accent; font-size: 11px; font-weight: bold; }
QTableView#str_table { background-color: $surface; alternate-background-color: $card;
    color: $text; border: 1px solid $border; gridline-color: $border; font-size: 12px;
    selection-background-color: $accent; selection-color: #FFFFFF; }
QHeaderView::section { background-color: $card; color: $muted; border: 1px solid $border;
    padding: 4px; font-weight: bold; }
QLabel#str_counts, QLabel#str_status { color: $muted; font-size: 11px; }
"""


if __name__ == '__main__':
    from gateway.pages.base_page import run_standalone
    run_standalone(StrategiesPage, 'page_strategies_title')

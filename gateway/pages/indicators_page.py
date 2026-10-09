"""One Gate Page 8 — 指標管理：新增／修改／移除主圖及副圖指標（ticket #19 / #21）。

- **儲存**：gateway/indicators.py IndicatorManager（state_store section 'indicators'）—
  本頁係配置 CRUD UI，唔做計算；K 線頁（kline_page）經同一 manager listener 即時同步。
- **頂欄**：揀指標類型（INDICATOR_DEFS，名 = acronym 語言中立）→ 位置 combo（只列該類型准入位置）
  → 參數 SpinBox（range 由 ParamSpec）→ ＋ 新增；揀中表格行 → 頂欄填入（編輯模式）→ ✓ 套用修改 / 🗑 移除所選。
- **表格**：顯示（checkbox 即時 set_enabled）/ 指標 / **說明**（一行 desc，跟語言；完整用法喺 tooltip）/ 位置 / 參數摘要。
- **可摺疊詳情**（`ind_detail_toggle`，預設收起）：展開即顯示所選類型嘅完整用法 + **每個參數一行解釋**
  （`ind_detail_note_<key>`）。🤖 詳情永遠跟頂欄（= 編輯中／準備新增嘅類型），唔跟表格所選行 —
  因為新增時根本未有行；揀行會經 `_fill_editor` 轉到頂欄，所以兩者永遠一致。
- **跨頁同步**：manager listener（origin 過濾）— K 線頁撳開關掣 → 本頁表格即時打勾；本頁改 → K 線頁即時生效。
- **底部**：指標總數 / 顯示中 + status（set status 必須喺 refresh 之後 — #10/#11 教訓）。
- **排版**：`gateway/ui/indicators_page.ui`（Designer 可調）— 邊行、margin、間距、詳情面板排版屬 UI；
  類型 / 位置 / **參數列** / **每個參數一行解釋** 屬資料 → 由 `INDICATOR_DEFS` 生成後填進 `.ui`
  預留嘅 `paramSlot` / `detailNoteSlot`（加指標唔使改 `.ui`）。
- **Theme/i18n**：照其他頁 recipe（`gateway/ui/bind.py` 嘅 `stamp` / `apply_text`）。

單獨運行：`python gateway/pages/indicators_page.py`。
"""
import os
import string
import sys

# ── standalone bootstrap（同其他頁同一 convention；package mode 下 no-op）──
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt, QAbstractTableModel  # noqa: E402
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDoubleSpinBox,  # noqa: E402
                               QHeaderView, QLabel, QSpinBox, QWidget)

import gateway.theme as theme_mod  # noqa: E402
from gateway import indicators  # noqa: E402 — 指標單一事實來源
from gateway.i18n import DEFAULT_LANG, t  # noqa: E402
from gateway.ui.bind import apply_text, stamp  # noqa: E402
from gateway.ui.loader import apply_ui  # noqa: E402

COLUMNS = ('enabled', 'name', 'desc', 'position', 'params', 'id')
HEAD_KEYS = {'enabled': 'ind_head_enabled', 'name': 'ind_head_name', 'desc': 'ind_head_desc',
             'position': 'ind_head_position', 'params': 'ind_head_params', 'id': 'id'}
_POS_KEYS = {'main': 'ind_pos_main', 'sub': 'ind_pos_sub'}

# `.ui` 入面嘅靜態 widget：QSS property（Designer 帶唔住）+ 文字來源（見 gateway/ui/bind.py）
_STAMP = {
    'indicators_page': {}, 'ind_detail_panel': {},   # bare QWidget 要 WA_StyledBackground 先食到背景 QSS
    'ind_add_btn': {'og': 'indbtn'}, 'ind_save_btn': {'og': 'indbtn'},
    'ind_remove_btn': {'og': 'indbtn'}, 'ind_detail_toggle': {'og': 'indbtn'},
    'ind_detail_desc': {'og': 'inddesc'}, 'ind_detail_usage': {'og': 'indusage'},
    'ind_detail_head': {'og': 'indhead'},
    'ind_page_note': {'role': 'pagebody'}, 'ind_table_note': {'role': 'usagehint'},
}
_TEXT = {'ind_add_btn': 'ind_add', 'ind_save_btn': 'ind_save', 'ind_remove_btn': 'ind_remove',
         'ind_detail_head': 'ind_detail_params',
         'ind_page_note': 'ind_page_note', 'ind_table_note': 'ind_table_note'}
# 詳情掣嘅文字跟「展開定收起」→ 唔入 _TEXT，retranslate 入面單獨處理
# （ind_detail_desc / _usage 混咗所選類型嘅資料 → 照留喺 _rebuild_detail）


class _IndModel(QAbstractTableModel):
    """(COLUMNS, items) → 表格；enabled 欄 = checkbox（setData → mgr.set_enabled）。"""

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
        if not index.isValid():
            return None
        e = self._rows[index.row()]
        col = COLUMNS[index.column()]
        if col == 'enabled' and role == Qt.CheckStateRole:
            return Qt.Checked if e['enabled'] else Qt.Unchecked
        d = indicators.INDICATOR_DEFS[e['def']]
        if role == Qt.ToolTipRole and col in ('name', 'desc'):
            # 🤖 列表只顯示一行描寫 → 完整用法（點樣用）放 tooltip
            return '%s\n\n%s：%s' % (t(d.desc_key, self._page._lang),
                                     t('ind_detail_use', self._page._lang),
                                     t(d.usage_key, self._page._lang))
        if role == Qt.DisplayRole:
            if col == 'enabled':
                return None
            if col == 'name':
                return d.label
            if col == 'desc':
                return t(d.desc_key, self._page._lang)
            if col == 'position':
                return t(_POS_KEYS[e['position']], self._page._lang)
            if col == 'params':
                return indicators._params_summary(d, e['params'])
            return e['id']
        return None

    def flags(self, index):
        f = super().flags(index)
        if COLUMNS[index.column()] == 'enabled':
            f |= Qt.ItemIsUserCheckable
        return f

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid():
            return False
        col = COLUMNS[index.column()]
        if col == 'enabled' and role == Qt.CheckStateRole:
            # 🤖 經 manager（origin='ind_page'）— K 線頁 listener 收到即重建 panel
            self._page._mgr().set_enabled(self._rows[index.row()]['id'],
                                          value == Qt.Checked, origin='ind_page')
            return True
        return False

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            key = HEAD_KEYS[COLUMNS[section]]
            return 'id' if key == 'id' else t(key, self._page._lang)
        return None


class IndicatorsPage(QWidget):
    def __init__(self):
        super().__init__()
        apply_ui(self, 'indicators_page')   # 排版喺 .ui（Designer 可調 margin / 行高 / 詳情面板排版）
        stamp(self, _STAMP)
        self._lang = DEFAULT_LANG
        self._mgr_inst = None            # lazy get_manager()（e2e 可 reset 後重建本頁）
        self._sel_id = None
        self._def_keys = list(indicators.INDICATOR_DEFS)
        self._param_spins, self._param_lbls = {}, {}   # 參數控件：數量屬資料 → 填進 paramSlot
        self._detail_notes = {}                        # 詳情解釋：同上 → 填進 detailNoteSlot

        self._fill_def_combo()
        self._setup_table()              # 要先過 setModel（selectionModel 只有 model 之後先存在）
        self._connect_signals()
        self.ind_detail_panel.setVisible(False)   # 預設收起 = 狀態，唔屬排版

        self._rebuild_param_row()
        self._mgr().add_listener(self._on_mgr_changed)
        theme_mod.add_listener(self._on_theme_changed)
        self._apply_theme_qss(theme_mod.CURRENT)
        self._refresh()

    def _connect_signals(self):
        self.ind_add_btn.clicked.connect(self._on_add)
        self.ind_save_btn.clicked.connect(self._on_save)
        self.ind_remove_btn.clicked.connect(self._on_remove)
        self.ind_detail_toggle.clicked.connect(self._toggle_detail)
        self.ind_def_combo.currentIndexChanged.connect(self._rebuild_param_row)
        self.ind_table.selectionModel().selectionChanged.connect(self._on_sel_changed)

    def _fill_def_combo(self):
        """頂欄類型 combo：內容 = INDICATOR_DEFS（acronym 語言中立）；tooltip = 一行描寫（跟語言）。"""
        for i, k in enumerate(self._def_keys):
            d = indicators.INDICATOR_DEFS[k]
            self.ind_def_combo.addItem(d.label)
            self.ind_def_combo.setItemData(i, t(d.desc_key, self._lang), Qt.ToolTipRole)

    def _setup_table(self):
        self.model = _IndModel(self)
        self.ind_table.setModel(self.model)
        self.ind_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.ind_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.ind_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.ind_table.verticalHeader().setVisible(False)

    @staticmethod
    def _clear_slot(slot):
        """清走 slot 入面全部 item（連 `addStretch` 嘅 spacer 一齊清 — 否則每次轉類型都疊多一條）。"""
        while slot.count():
            it = slot.takeAt(0)
            if it is None:
                break
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    # ── lazy manager（app 內單例；e2e reset_manager_for_test 後本頁 lazy 取新的）──
    def _mgr(self):
        if self._mgr_inst is None:
            self._mgr_inst = indicators.get_manager()
        return self._mgr_inst

    # ── 頂欄參數編輯器（隨類型重建；range 由 ParamSpec）──
    def _cur_def(self):
        return indicators.INDICATOR_DEFS[self._def_keys[self.ind_def_combo.currentIndex()]]

    def _rebuild_param_row(self, *_):
        d = self._cur_def()
        self._clear_slot(self.paramSlot)   # 含上次嘅 stretch
        self._param_spins, self._param_lbls = {}, {}
        # 位置 combo 只列該類型准入位置
        self.ind_pos_combo.clear()
        for pos in d.positions:
            self.ind_pos_combo.addItem(t(_POS_KEYS[pos], self._lang), pos)
        for p in d.params:
            lbl = QLabel(t(p.label_key, self._lang))
            lbl.setObjectName(f'ind_paramlbl_{p.key}')
            lbl.setProperty('og', 'indparamlbl')
            self.paramSlot.addWidget(lbl)
            if p.is_bool:   # #28：開關型參數（MA 逐條線顯示）→ CHECKBOX
                sp = QCheckBox()
                sp.setChecked(bool(int(p.default)))
            elif p.is_int:
                sp = QSpinBox()
                sp.setRange(int(p.lo), int(p.hi))
                sp.setValue(int(p.default))
            else:
                sp = QDoubleSpinBox()
                sp.setRange(float(p.lo), float(p.hi))
                sp.setSingleStep(0.1)
                sp.setDecimals(1)
                sp.setValue(float(p.default))
            sp.setObjectName(f'ind_param_{p.key}')   # E2E hook
            sp.setProperty('og', 'indparam')
            self.paramSlot.addWidget(sp)
            self._param_spins[p.key] = sp
            self._param_lbls[p.key] = lbl
        self.paramSlot.addStretch(1)
        self._rebuild_detail(d)

    # ── 可摺疊詳情：一行描寫 + 完整用法 + 每個參數一行解釋（ticket #21）──
    def _toggle_detail(self):
        open_it = not self.ind_detail_panel.isVisible()
        self.ind_detail_panel.setVisible(open_it)
        self.ind_detail_toggle.setText(t('ind_detail_hide' if open_it else 'ind_detail_show', self._lang))

    def _rebuild_detail(self, d):
        """跟頂欄所選類型重建詳情（每個參數一行 → objectName ind_detail_note_<key>，填進 detailNoteSlot）。"""
        lang = self._lang
        self.ind_detail_desc.setText('%s %s：%s' % (d.label, t('ind_detail_desc', lang), t(d.desc_key, lang)))
        self.ind_detail_usage.setText('%s：%s' % (t('ind_detail_use', lang), t(d.usage_key, lang)))
        self._clear_slot(self.detailNoteSlot)
        self._detail_notes = {}
        for p in d.params:
            lbl = QLabel()   # E2E hook
            lbl.setObjectName('ind_detail_note_%s' % p.key)
            lbl.setProperty('role', 'usagehint')
            lbl.setWordWrap(True)
            lbl.setText('· %s：%s' % (t(p.label_key, lang), t(p.note_key, lang)))
            self.detailNoteSlot.addWidget(lbl)
            self._detail_notes[p.key] = lbl

    @staticmethod
    def _widget_val(sp):   # #28：spin → value()；checkbox（is_bool）→ 0/1
        return int(sp.isChecked()) if isinstance(sp, QCheckBox) else sp.value()

    def _editor_params(self):
        return {k: self._widget_val(sp) for k, sp in self._param_spins.items()}

    def _fill_editor(self, e):
        """編輯模式：表格所選行填入頂欄。"""
        i = self._def_keys.index(e['def'])
        self.ind_def_combo.blockSignals(True)
        self.ind_def_combo.setCurrentIndex(i)
        self.ind_def_combo.blockSignals(False)
        self._rebuild_param_row()
        pi = self.ind_pos_combo.findData(e['position'])
        self.ind_pos_combo.setCurrentIndex(pi if pi >= 0 else 0)
        for k, sp in self._param_spins.items():
            if isinstance(sp, QCheckBox):   # #28
                sp.setChecked(bool(int(e['params'].get(k, 1))))
            else:
                sp.setValue(e['params'].get(k, sp.value()))

    def _on_sel_changed(self, *_):
        rows = self.ind_table.selectionModel().selectedRows()
        if not rows:
            self._sel_id = None
            return
        e = self.model.row_at(rows[0].row())
        self._sel_id = e['id']
        self._fill_editor(e)

    # ── 新增 / 套用 / 移除 ──
    def _on_add(self):
        d = self._cur_def()
        pos = self.ind_pos_combo.currentData()
        ok, msg_key, _item = self._mgr().add(d.key, pos, self._editor_params(), origin='ind_page')
        self._refresh()   # 🤖 set status 必須喺 refresh 之後
        self.ind_status.setText(('✅ ' if ok else '') + t(msg_key, self._lang)
                                if ok else f'❌ {t(msg_key, self._lang)}')

    def _on_save(self):
        if not self._sel_id or self._mgr().get(self._sel_id) is None:
            self.ind_status.setText(f'⚠️ {t("ind_no_sel", self._lang)}')
            return
        pos = self.ind_pos_combo.currentData()
        ok, msg_key = self._mgr().update(self._sel_id, position=pos,
                                         params=self._editor_params(), origin='ind_page')
        self._refresh()
        self.ind_status.setText(('✅ ' if ok else '❌ ') + t(msg_key, self._lang))

    def _on_remove(self):
        if not self._sel_id or self._mgr().get(self._sel_id) is None:
            self.ind_status.setText(f'⚠️ {t("ind_no_sel", self._lang)}')
            return
        ok, msg_key = self._mgr().remove(self._sel_id, origin='ind_page')
        self._sel_id = None
        self._refresh()
        self.ind_status.setText(('🗑 ' if ok else '❌ ') + t(msg_key, self._lang))

    # ── 跨頁同步（K 線頁開關 → 本頁表格即時打勾）──
    def _on_mgr_changed(self, origin, kind):
        if origin != 'ind_page':
            self._refresh()

    # ── 顯示 ──
    def _refresh(self):
        self.model.set_rows(self._mgr().items())
        self._configure_columns()
        self._update_counts()

    def _configure_columns(self):
        hdr = self.ind_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(COLUMNS.index('desc'), QHeaderView.Stretch)   # 一行描寫用晒剩返嘅寬度
        widths = {0: 60, 1: 110, 3: 90, 4: 160, 5: 90}
        for col, w in widths.items():
            self.ind_table.setColumnWidth(col, w)

    def _update_counts(self):
        items = self._mgr().items()
        total = len(items)
        shown = sum(1 for e in items if e['enabled'])
        self.ind_counts.setText(
            f'{t("ind_count", self._lang)} {total}　‖　'
            f'{t("ind_showing", self._lang)} {shown}')
        if total == 0:
            self.ind_status.setText(t('ind_empty', self._lang))

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
        apply_text(self, _TEXT, lang)   # 三個動作掣（objectName → i18n key）
        self.ind_detail_toggle.setText(t('ind_detail_hide' if self.ind_detail_panel.isVisible()
                                         else 'ind_detail_show', lang))
        for i, k in enumerate(self._def_keys):   # 類型 combo tooltip（一行描寫）跟語言
            self.ind_def_combo.setItemData(i, t(indicators.INDICATOR_DEFS[k].desc_key, lang), Qt.ToolTipRole)
        self._rebuild_param_row()   # 位置/參數 label + 詳情面板 跟語言
        self._refresh()             # headerData / desc / position / counts 全部跟語言重建


_PAGE_QSS = """
QWidget#indicators_page { background-color: $window; }
QComboBox#ind_def_combo, QComboBox#ind_pos_combo {
    background-color: $card; color: $text; border: 1px solid $border;
    border-radius: 4px; padding: 4px 8px; font-size: 13px; }
QSpinBox[og="indparam"], QDoubleSpinBox[og="indparam"] {
    background-color: $card; color: $text; border: 1px solid $border;
    border-radius: 4px; padding: 3px 6px; font-size: 13px; }
QLabel[og="indparamlbl"] { color: $muted; font-size: 12px; }
QPushButton[og="indbtn"] { color: $muted; background: transparent;
    border: 1px solid $border; border-radius: 4px; padding: 4px 10px; font-size: 12px; }
QPushButton[og="indbtn"]:hover { color: $text; border-color: $accent; }
QPushButton[og="indbtn"]:pressed { background-color: $accent_pressed; color: #FFFFFF; }
QTableView#ind_table { background-color: $surface; alternate-background-color: $card;
    color: $text; border: 1px solid $border; gridline-color: $border; font-size: 12px;
    selection-background-color: $accent; selection-color: #FFFFFF; }
QHeaderView::section { background-color: $card; color: $muted; border: 1px solid $border;
    padding: 4px; font-weight: bold; }
QWidget#ind_detail_panel { background-color: $card; border: 1px solid $border; border-radius: 4px; }
QLabel[og="inddesc"], QLabel[og="indusage"] { color: $text; font-size: 12px; }
QLabel[og="indhead"] { color: $muted; font-size: 12px; font-weight: bold; }
QLabel#ind_counts, QLabel#ind_status { color: $muted; font-size: 11px; }
"""


if __name__ == '__main__':
    from gateway.pages.base_page import run_standalone
    run_standalone(IndicatorsPage, 'page_indicators_title')

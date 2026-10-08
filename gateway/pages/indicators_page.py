"""One Gate Page 8 — 指標管理：新增／修改／移除主圖及副圖指標（ticket #19）。

- **儲存**：gateway/indicators.py IndicatorManager（state_store section 'indicators'）—
  本頁係配置 CRUD UI，唔做計算；K 線頁（kline_page）經同一 manager listener 即時同步。
- **頂欄**：揀指標類型（INDICATOR_DEFS，名 = acronym 語言中立）→ 位置 combo（只列該類型准入位置）
  → 參數 SpinBox（range 由 ParamSpec）→ ＋ 新增；揀中表格行 → 頂欄填入（編輯模式）→ ✓ 套用修改 / 🗑 移除所選。
- **表格**：顯示（checkbox 即時 set_enabled）/ 指標 / 位置 / 參數摘要。
- **跨頁同步**：manager listener（origin 過濾）— K 線頁撳開關掣 → 本頁表格即時打勾；本頁改 → K 線頁即時生效。
- **底部**：指標總數 / 顯示中 + status（set status 必須喺 refresh 之後 — #10/#11 教訓）。
- **Theme/i18n**：照其他頁 recipe。

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
from PySide6.QtWidgets import (QAbstractItemView, QComboBox,  # noqa: E402
                               QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QSpinBox, QTableView, QVBoxLayout, QWidget)

import gateway.theme as theme_mod  # noqa: E402
from gateway import indicators  # noqa: E402 — 指標單一事實來源
from gateway.i18n import DEFAULT_LANG, t  # noqa: E402

COLUMNS = ('enabled', 'name', 'position', 'params', 'id')
HEAD_KEYS = {'enabled': 'ind_head_enabled', 'name': 'ind_head_name',
             'position': 'ind_head_position', 'params': 'ind_head_params', 'id': 'id'}
_POS_KEYS = {'main': 'ind_pos_main', 'sub': 'ind_pos_sub'}


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
        if role == Qt.DisplayRole:
            if col == 'enabled':
                return None
            if col == 'name':
                return indicators.INDICATOR_DEFS[e['def']].label
            if col == 'position':
                return t(_POS_KEYS[e['position']], self._page._lang)
            if col == 'params':
                return indicators._params_summary(indicators.INDICATOR_DEFS[e['def']], e['params'])
            return e['id']
        if col == 'enabled' and role == Qt.CheckStateRole:
            return Qt.Checked if e['enabled'] else Qt.Unchecked
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
        self.setObjectName('indicators_page')
        self._lang = DEFAULT_LANG
        self._mgr_inst = None            # lazy get_manager()（e2e 可 reset 後重建本頁）
        self._sel_id = None

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)

        # ── 頂欄：類型 / 位置 / 參數 / 新增 / 套用 / 移除 ──
        top = QHBoxLayout()
        self.def_combo = QComboBox()
        self.def_combo.setObjectName('ind_def_combo')
        self._def_keys = list(indicators.INDICATOR_DEFS)
        for k in self._def_keys:
            self.def_combo.addItem(indicators.INDICATOR_DEFS[k].label)
        top.addWidget(self.def_combo)
        self.pos_combo = QComboBox()
        self.pos_combo.setObjectName('ind_pos_combo')
        top.addWidget(self.pos_combo)
        v.addLayout(top)

        self.param_row = QHBoxLayout()   # 參數 label + spinbox（隨類型重建，objectName ind_param_<key>）
        self._param_spins = {}
        self._param_lbls = {}
        v.addLayout(self.param_row)

        btns = QHBoxLayout()
        self.add_btn = QPushButton(t('ind_add', self._lang))
        self.add_btn.setObjectName('ind_add_btn')
        self.add_btn.setProperty('og', 'indbtn')
        self.add_btn.clicked.connect(self._on_add)
        btns.addWidget(self.add_btn)
        self.save_btn = QPushButton(t('ind_save', self._lang))
        self.save_btn.setObjectName('ind_save_btn')
        self.save_btn.setProperty('og', 'indbtn')
        self.save_btn.clicked.connect(self._on_save)
        btns.addWidget(self.save_btn)
        self.remove_btn = QPushButton(t('ind_remove', self._lang))
        self.remove_btn.setObjectName('ind_remove_btn')
        self.remove_btn.setProperty('og', 'indbtn')
        self.remove_btn.clicked.connect(self._on_remove)
        btns.addWidget(self.remove_btn)
        btns.addStretch(1)
        v.addLayout(btns)

        # ── 表格（單選行 → 編輯模式；enabled 欄 checkbox 即時生效）──
        self.model = _IndModel(self)
        self.table = QTableView()
        self.table.setObjectName('ind_table')
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.selectionModel().selectionChanged.connect(self._on_sel_changed)
        v.addWidget(self.table, 1)

        # ── 底部：總數 / 顯示中 + status ──
        bottom = QHBoxLayout()
        self.counts_lbl = QLabel()
        self.counts_lbl.setObjectName('ind_counts')
        bottom.addWidget(self.counts_lbl, 1)
        self.status_lbl = QLabel()
        self.status_lbl.setObjectName('ind_status')
        bottom.addWidget(self.status_lbl)
        v.addLayout(bottom)

        self.def_combo.currentIndexChanged.connect(self._rebuild_param_row)
        self._rebuild_param_row()

        self._mgr().add_listener(self._on_mgr_changed)
        theme_mod.add_listener(self._on_theme_changed)
        self._apply_theme_qss(theme_mod.CURRENT)
        self._refresh()

    # ── lazy manager（app 內單例；e2e reset_manager_for_test 後本頁 lazy 取新的）──
    def _mgr(self):
        if self._mgr_inst is None:
            self._mgr_inst = indicators.get_manager()
        return self._mgr_inst

    # ── 頂欄參數編輯器（隨類型重建；range 由 ParamSpec）──
    def _cur_def(self):
        return indicators.INDICATOR_DEFS[self._def_keys[self.def_combo.currentIndex()]]

    def _rebuild_param_row(self, *_):
        d = self._cur_def()
        for w in list(self._param_spins.values()) + list(self._param_lbls.values()):
            w.setParent(None)
        self._param_spins, self._param_lbls = {}, {}
        # 位置 combo 只列該類型准入位置
        self.pos_combo.clear()
        for pos in d.positions:
            self.pos_combo.addItem(t(_POS_KEYS[pos], self._lang), pos)
        for p in d.params:
            lbl = QLabel(t(p.label_key, self._lang))
            lbl.setObjectName(f'ind_paramlbl_{p.key}')
            lbl.setProperty('og', 'indparamlbl')
            self.param_row.addWidget(lbl)
            if p.is_int:
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
            self.param_row.addWidget(sp)
            self._param_spins[p.key] = sp
            self._param_lbls[p.key] = lbl
        self.param_row.addStretch(1)

    def _editor_params(self):
        return {k: sp.value() for k, sp in self._param_spins.items()}

    def _fill_editor(self, e):
        """編輯模式：表格所選行填入頂欄。"""
        i = self._def_keys.index(e['def'])
        self.def_combo.blockSignals(True)
        self.def_combo.setCurrentIndex(i)
        self.def_combo.blockSignals(False)
        self._rebuild_param_row()
        pi = self.pos_combo.findData(e['position'])
        self.pos_combo.setCurrentIndex(pi if pi >= 0 else 0)
        for k, sp in self._param_spins.items():
            sp.setValue(e['params'].get(k, sp.value()))

    def _on_sel_changed(self, *_):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self._sel_id = None
            return
        e = self.model.row_at(rows[0].row())
        self._sel_id = e['id']
        self._fill_editor(e)

    # ── 新增 / 套用 / 移除 ──
    def _on_add(self):
        d = self._cur_def()
        pos = self.pos_combo.currentData()
        ok, msg_key, _item = self._mgr().add(d.key, pos, self._editor_params(), origin='ind_page')
        self._refresh()   # 🤖 set status 必須喺 refresh 之後
        self.status_lbl.setText(('✅ ' if ok else '') + t(msg_key, self._lang)
                                if ok else f'❌ {t(msg_key, self._lang)}')

    def _on_save(self):
        if not self._sel_id or self._mgr().get(self._sel_id) is None:
            self.status_lbl.setText(f'⚠️ {t("ind_no_sel", self._lang)}')
            return
        pos = self.pos_combo.currentData()
        ok, msg_key = self._mgr().update(self._sel_id, position=pos,
                                         params=self._editor_params(), origin='ind_page')
        self._refresh()
        self.status_lbl.setText(('✅ ' if ok else '❌ ') + t(msg_key, self._lang))

    def _on_remove(self):
        if not self._sel_id or self._mgr().get(self._sel_id) is None:
            self.status_lbl.setText(f'⚠️ {t("ind_no_sel", self._lang)}')
            return
        ok, msg_key = self._mgr().remove(self._sel_id, origin='ind_page')
        self._sel_id = None
        self._refresh()
        self.status_lbl.setText(('🗑 ' if ok else '❌ ') + t(msg_key, self._lang))

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
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        widths = {0: 60, 1: 110, 2: 90, 3: 160, 4: 90}
        for col, w in widths.items():
            self.table.setColumnWidth(col, w)

    def _update_counts(self):
        items = self._mgr().items()
        total = len(items)
        shown = sum(1 for e in items if e['enabled'])
        self.counts_lbl.setText(
            f'{t("ind_count", self._lang)} {total}　‖　'
            f'{t("ind_showing", self._lang)} {shown}')
        if total == 0:
            self.status_lbl.setText(t('ind_empty', self._lang))

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
        self.add_btn.setText(t('ind_add', lang))
        self.save_btn.setText(t('ind_save', lang))
        self.remove_btn.setText(t('ind_remove', lang))
        self._rebuild_param_row()   # 位置/參數 label 跟語言
        self._refresh()             # headerData / position / counts 全部跟語言重建


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
QLabel#ind_counts, QLabel#ind_status { color: $muted; font-size: 11px; }
"""


if __name__ == '__main__':
    from gateway.pages.base_page import run_standalone
    run_standalone(IndicatorsPage, 'page_indicators_title')

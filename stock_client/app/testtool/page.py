"""Test Tool page — parameter panel + progress header + collapsible result matrix.

Layout (top to bottom):
  title
  [card] codes editor | ktype/broker/function checkbox filters | numeric params
  [Test All] [Abort] [progress bar] [summary counts]
  scroll area of ResultRowWidget rows (rebuilt whenever a dimension filter changes)

The page owns no broker logic: it builds payloads from the current controls and
hands them to TestEngine, which drives the worker thread. Row state comes back
through engine signals.
"""

from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QProgressBar, QPushButton, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)

from .engine import TestEngine
from .model import BROKERS, DEFAULT_CODES, FUNCS, KTYPES, MatrixConfig, build_matrix
from .result_row import ResultRowWidget


class TestToolPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._theme = ctx.theme_mgr.current or "dark"
        self._bridge_ready = False
        self._tick_wired = False  # stream_tick connected once the worker exists (M4)
        self._rows: dict[str, ResultRowWidget] = {}
        self._last_summary: dict | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self.title_label = QLabel()
        self.title_label.setProperty("class", "sectionTitle")
        root.addWidget(self.title_label)

        # --- parameter panel -----------------------------------------------------
        params = QFrame()
        params.setProperty("class", "cardPanel")
        pl = QVBoxLayout(params)
        pl.setContentsMargins(12, 12, 12, 12)
        pl.setSpacing(10)

        # row 1: code list editor (add / edit by double-click / remove)
        codes_row = QHBoxLayout()
        self.codes_label = QLabel()
        self.code_list = QListWidget()
        self.code_list.setMaximumHeight(76)
        self.code_input = QLineEdit()
        self.btn_add_code = QPushButton()
        self.btn_remove_code = QPushButton()
        code_side = QVBoxLayout()
        code_side.setSpacing(4)
        code_side.addWidget(self.code_input)
        code_side.addWidget(self.btn_add_code)
        code_side.addWidget(self.btn_remove_code)
        codes_row.addWidget(self.codes_label)
        codes_row.addWidget(self.code_list, 1)
        codes_row.addLayout(code_side)
        pl.addLayout(codes_row)

        # row 2: dimension filters (checkbox columns; all checked by default)
        filt = QHBoxLayout()
        self._filter_labels: list[tuple[QLabel, str]] = []  # (label, i18n key) for retranslate

        def _check_column(i18n_key: str, items: list[str], store: dict):
            col = QVBoxLayout()
            lbl = QLabel()
            lbl.setProperty("class", "mutedLabel")
            self._filter_labels.append((lbl, i18n_key))
            col.addWidget(lbl)
            for name in items:
                cb = QCheckBox(name)
                cb.setChecked(True)
                store[name] = cb
                col.addWidget(cb)
            return col

        broker_display = {"ib": "IB", "futu": "Futu"}
        self._ktype_checks: dict[str, QCheckBox] = {}
        self._broker_checks: dict[str, QCheckBox] = {}
        self._func_checks: dict[str, QCheckBox] = {}
        filt.addLayout(_check_column("testtool.ktype", KTYPES, self._ktype_checks))
        filt.addSpacing(16)
        filt.addLayout(_check_column("testtool.broker", BROKERS, self._broker_checks))
        # brand names instead of raw keys on the broker column
        for b in BROKERS:
            self._broker_checks[b].setText(broker_display.get(b, b))
        filt.addSpacing(16)
        filt.addLayout(_check_column("testtool.function", FUNCS, self._func_checks))
        pl.addLayout(filt)

        # row 3: numeric params + strict/mock toggles (restored from settings, M5)
        s = ctx.settings
        num_row = QHBoxLayout()
        self.kline_num_label = QLabel()
        self.kline_spin = QSpinBox()
        self.kline_spin.setRange(10, 5000)
        self.kline_spin.setValue(int(s.tt_kline_num))
        self.dur_label = QLabel()
        self.dur_spin = QSpinBox()
        self.dur_spin.setSuffix(" s")
        self.dur_spin.setRange(5, 600)
        self.dur_spin.setValue(int(s.tt_duration))
        self.strict_chk = QCheckBox()
        self.strict_chk.setChecked(bool(s.tt_strict))
        self.mock_chk = QCheckBox()
        self.mock_chk.setChecked(bool(s.tt_mock))
        num_row.addWidget(self.kline_num_label)
        num_row.addWidget(self.kline_spin)
        num_row.addSpacing(16)
        num_row.addWidget(self.dur_label)
        num_row.addWidget(self.dur_spin)
        num_row.addStretch(1)
        num_row.addWidget(self.strict_chk)
        num_row.addSpacing(16)
        num_row.addWidget(self.mock_chk)
        pl.addLayout(num_row)

        root.addWidget(params)

        # --- progress / summary header ---------------------------------------------
        head = QHBoxLayout()
        self.btn_test_all = QPushButton()
        self.btn_test_all.setProperty("class", "primaryButton")
        self.btn_test_all.setEnabled(False)  # until the bridge reports ready
        self.btn_abort = QPushButton()
        self.btn_abort.setProperty("class", "dangerButton")
        self.btn_abort.setEnabled(False)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("%v/%m")
        self.summary_label = QLabel()
        self.summary_label.setProperty("class", "mutedLabel")
        head.addWidget(self.btn_test_all)
        head.addWidget(self.btn_abort)
        head.addWidget(self.progress, 1)
        head.addWidget(self.summary_label)
        root.addLayout(head)

        # --- result matrix (scroll area of collapsible rows) -------------------------
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.matrix_container = QWidget()
        self.matrix_layout = QVBoxLayout(self.matrix_container)
        self.matrix_layout.setContentsMargins(0, 0, 0, 0)
        self.matrix_layout.setSpacing(6)
        self.empty_label = QLabel()
        self.empty_label.setProperty("class", "mutedLabel")
        self.empty_label.hide()
        self.matrix_layout.addWidget(self.empty_label)
        self.scroll.setWidget(self.matrix_container)
        root.addWidget(self.scroll, 1)

        # --- engine + signal wiring ----------------------------------------------------
        self.engine = TestEngine(ctx.worker, parent=self)
        if ctx.worker is not None:
            ctx.worker.worker_ready.connect(self._on_worker_ready)
            if ctx.worker.worker is not None:  # thread already booted (fast start)
                self._on_worker_ready(ctx.worker.worker)

        self.engine.test_state_changed.connect(self._on_test_state_changed)
        self.engine.batch_progress.connect(self._on_batch_progress)
        self.engine.batch_finished.connect(self._on_batch_finished)
        ctx.theme_mgr.theme_changed.connect(self._on_theme_changed)

        # --- control wiring ---------------------------------------------------------------
        for code in ctx.settings.tt_codes or list(DEFAULT_CODES):  # persisted codes (M5)
            self.code_list.addItem(code)
        self.code_list.itemChanged.connect(self._rebuild_matrix)  # add/edit/remove all land here
        self.btn_add_code.clicked.connect(self._add_code)
        self.btn_remove_code.clicked.connect(self._remove_code)
        for store in (self._ktype_checks, self._broker_checks, self._func_checks):
            for cb in store.values():
                cb.stateChanged.connect(lambda _=None: self._rebuild_matrix())
        # persist last-used params as they change (M5) — the property setters coerce types
        st = self.ctx.settings
        self.kline_spin.valueChanged.connect(lambda v: setattr(st, "tt_kline_num", int(v)))
        self.dur_spin.valueChanged.connect(lambda v: setattr(st, "tt_duration", int(v)))
        self.engine.set_strict(self.strict_chk.isChecked())  # restore persisted value
        self.strict_chk.toggled.connect(self.engine.set_strict)
        self.strict_chk.toggled.connect(lambda v: setattr(st, "tt_strict", bool(v)))
        self.mock_chk.toggled.connect(self._on_mock_toggled)
        self.btn_test_all.clicked.connect(self._on_test_all)
        self.btn_abort.clicked.connect(self.engine.abort)

        self.retranslateUi()
        self._rebuild_matrix()  # initial 48-row matrix (zero broker contact)

    # --- bridge readiness -------------------------------------------------------------
    def _on_worker_ready(self, worker):
        self._bridge_ready = True
        self.engine.attach_worker(worker)
        if not self._tick_wired:  # live stream ticks -> rows (queued from the worker thread)
            self._tick_wired = True
            worker.stream_tick.connect(self._on_stream_tick)
        self.btn_test_all.setEnabled(True)
        for row in self._rows.values():
            row.set_run_enabled(True)
        self._apply_mock()  # restore persisted mock state now that the bridge exists

    def _apply_mock(self):
        """Push the mock toggle to the real bridge (no-op for fakes / pre-ready)."""
        lt = self.ctx.worker
        if lt is not None and hasattr(lt, "use_mock"):
            lt.use_mock(self.mock_chk.isChecked())

    def _on_mock_toggled(self, checked: bool):
        self.ctx.settings.tt_mock = bool(checked)  # persist (M5)
        self._apply_mock()

    # --- code list editor -----------------------------------------------------------------
    def _add_code(self):
        text = self.code_input.text().strip()
        if not text:
            return
        existing = [self.code_list.item(i).text() for i in range(self.code_list.count())]
        if text in existing:
            self.code_input.clear()
            return
        self.code_list.addItem(text)  # itemChanged -> _rebuild_matrix
        self.code_input.clear()

    def _remove_code(self):
        row = self.code_list.currentRow()
        if row >= 0:
            self.code_list.takeItem(row)  # itemChanged -> _rebuild_matrix

    # --- matrix rebuild (dimension filters changed) -------------------------------------------
    def _current_codes(self) -> list[str]:
        return [
            self.code_list.item(i).text().strip()
            for i in range(self.code_list.count())
            if self.code_list.item(i).text().strip()
        ]

    def _rebuild_matrix(self):
        self.ctx.settings.tt_codes = self._current_codes()  # persist last-used codes (M5)
        cfg = MatrixConfig(
            codes=self._current_codes(),
            ktypes=[k for k in KTYPES if self._ktype_checks[k].isChecked()],
            brokers=[b for b in BROKERS if self._broker_checks[b].isChecked()],
            funcs=[f for f in FUNCS if self._func_checks[f].isChecked()],
        )
        specs = build_matrix(cfg)
        self.engine.set_matrix(specs)

        for w in list(self._rows.values()):
            self.matrix_layout.removeWidget(w)
            w.deleteLater()
        self._rows.clear()

        for spec in specs:
            row = ResultRowWidget(spec, self.ctx.translator, self._theme)
            row.run_clicked.connect(self._on_run_clicked)
            st = self.engine.state_for(spec.test_id)  # keep results across filter toggles
            if st is not None:
                row.apply_state(st)
            row.set_run_enabled(self._bridge_ready)
            self.matrix_layout.addWidget(row)
            self._rows[spec.test_id] = row

        self.empty_label.setVisible(not specs)

    def _payload(self, spec) -> dict:
        return {
            "test_id": spec.test_id, "code": spec.code, "ktype": spec.ktype,
            "broker": spec.broker, "func": spec.func,
            "kline_num": self.kline_spin.value(), "duration_s": self.dur_spin.value(),
        }

    # --- run actions -----------------------------------------------------------------------------
    def _on_run_clicked(self, test_id: str):
        row = self._rows.get(test_id)
        if row is not None:
            self.engine.run_single(self._payload(row.spec))

    def _on_test_all(self):
        payloads = [self._payload(row.spec) for row in self._rows.values()]
        if not payloads:
            return
        self.progress.setRange(0, len(payloads))
        self.progress.setValue(0)
        self.engine.run_all(payloads)
        # run_all reset every state to pending — refresh the rows to match
        for tid, row in self._rows.items():
            st = self.engine.state_for(tid)
            if st is not None:
                row.apply_state(st)

    # --- live stream ticks (queued from the worker thread) ---------------------------------------------
    def _on_stream_tick(self, test_id: str, payload: dict):
        row = self._rows.get(test_id)
        if row is not None and row.spec.func == "stream_kline":
            row.update_live(payload)

    # --- engine signal handlers (GUI thread) ----------------------------------------------------------
    def _on_test_state_changed(self, test_id: str, state: dict):
        row = self._rows.get(test_id)
        if row is not None:
            row.apply_state(state)
        self.btn_abort.setEnabled(self.engine.is_busy())

    def _summary_text(self, s: dict) -> str:
        t = self.ctx.t
        parts = [f"{t('testtool.summary_pass')} {s['pass']}",
                 f"{t('testtool.summary_fail')} {s['fail']}"]
        if s["ok_no_ticks"]:
            parts.append(f"{t('testtool.summary_ok_no_ticks')} {s['ok_no_ticks']}")
        if s["aborted"]:
            parts.append(f"{t('testtool.summary_aborted')} {s['aborted']}")
        return " · ".join(parts)

    def _on_batch_progress(self, done: int, total: int):
        self.progress.setValue(done)
        self._last_summary = self.engine.summary()
        self.summary_label.setText(self._summary_text(self._last_summary))

    def _on_batch_finished(self, summary: dict):
        self._last_summary = summary
        self.summary_label.setText(self._summary_text(summary))
        self.progress.setValue(self.progress.maximum())
        self.btn_abort.setEnabled(False)

    # --- theme / language ------------------------------------------------------------------------------
    def _on_theme_changed(self, name: str):
        self._theme = name
        for row in self._rows.values():
            row.set_theme(name)

    def retranslateUi(self):
        t = self.ctx.t
        self.title_label.setText(t("testtool.title"))
        self.codes_label.setText(t("testtool.codes"))
        self.code_input.setPlaceholderText(t("testtool.code_placeholder"))
        self.btn_add_code.setText(t("testtool.add_code"))
        self.btn_remove_code.setText(t("testtool.remove_code"))
        for lbl, key in self._filter_labels:
            lbl.setText(t(key))
        self.kline_num_label.setText(t("testtool.kline_num"))
        self.dur_label.setText(t("testtool.duration"))
        self.strict_chk.setText(t("testtool.strict_pass"))
        self.mock_chk.setText(t("testtool.use_mock"))
        self.btn_test_all.setText(t("testtool.test_all"))
        self.btn_abort.setText(t("testtool.abort"))
        self.empty_label.setText(t("testtool.empty_matrix"))
        if self._last_summary is not None:
            self.summary_label.setText(self._summary_text(self._last_summary))
        for row in self._rows.values():
            row.retranslateUi()

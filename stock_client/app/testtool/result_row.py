"""ResultRowWidget — one collapsible row in the test matrix.

Header = dimension text + status pill + Run button + chevron; body is
collapsible and func-specific:
  get_kline -> table preview (<=20 rows) + elapsed/rows/last-bar meta line
  stream    -> stats grid (baseline / live updates / first+last ts / last bar)

The row is a pure display widget: the page pushes state via apply_state() and
receives run requests via run_clicked. It never talks to the engine or worker,
so it stays trivially testable offscreen.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ..theme.palette import current as palette_current
from ..widgets.sparkline import SparkLine


class ResultRowWidget(QFrame):
    run_clicked = Signal(str)  # test_id

    _PILL_COLOR_KEY = {
        "pending": "muted",
        "running": "accent",
        "pass": "success",
        "fail": "danger",
        "ok_no_ticks": "warning",
        "aborted": "muted",
    }

    def __init__(self, spec, translator, theme_name: str, parent=None):
        super().__init__(parent)
        self.spec = spec
        self._translator = translator
        self._theme = theme_name
        self._state: dict = {"status": "pending"}
        self._expanded = False

        self.setProperty("class", "cardPanel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- header row ---
        header = QWidget()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 8, 12, 8)
        hl.setSpacing(8)

        self.dim_label = QLabel()
        self.dim_label.setProperty("class", "monoText")
        self.status_pill = QLabel()
        self.run_btn = QPushButton()
        self.run_btn.setEnabled(False)  # enabled once the bridge reports ready
        self.chevron_btn = QPushButton()
        self.chevron_btn.setProperty("class", "chevronButton")
        self.chevron_btn.setFixedWidth(28)

        hl.addWidget(self.dim_label)
        hl.addStretch(1)
        hl.addWidget(self.status_pill)
        hl.addWidget(self.run_btn)
        hl.addWidget(self.chevron_btn)
        outer.addWidget(header)

        # --- collapsible body ---
        self.body = QWidget()
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(12, 0, 12, 12)
        bl.setSpacing(8)

        self.spark: SparkLine | None = None  # stream rows only (M4 live chart)
        if spec.func == "get_kline":
            self.table = QTableWidget(0, 6)
            self.table.verticalHeader().setVisible(False)
            self.table.setEditTriggers(QTableWidget.NoEditTriggers)
            self.table.setSelectionMode(QTableWidget.NoSelection)
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            self.table.setMaximumHeight(240)  # ~20 rows visible; scrolls beyond
            bl.addWidget(self.table)
            self.meta_label = QLabel()
            self.meta_label.setProperty("class", "mutedLabel")
            bl.addWidget(self.meta_label)
        else:
            grid_w = QWidget()
            g = QGridLayout(grid_w)
            g.setContentsMargins(0, 0, 0, 0)
            g.setHorizontalSpacing(24)
            self.stream_labels: dict[str, QLabel] = {}
            for key in ("baseline", "live_updates", "first_update", "last_update"):
                lbl = QLabel()
                lbl.setProperty("class", "mutedLabel")
                self.stream_labels[key] = lbl
            g.addWidget(self.stream_labels["baseline"], 0, 0)
            g.addWidget(self.stream_labels["live_updates"], 0, 1)
            g.addWidget(self.stream_labels["first_update"], 1, 0)
            g.addWidget(self.stream_labels["last_update"], 1, 1)
            bl.addWidget(grid_w)
            self.spark = SparkLine(self._theme)  # live close-price line, pushed per tick
            bl.addWidget(self.spark)
            self.last_bar_label = QLabel()
            self.last_bar_label.setProperty("class", "monoText")
            bl.addWidget(self.last_bar_label)

        # shared error line (both bodies)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        bl.addWidget(self.error_label)

        outer.addWidget(self.body)
        self.body.hide()

        self.run_btn.clicked.connect(lambda: self.run_clicked.emit(spec.test_id))
        self.chevron_btn.clicked.connect(self._toggle_expanded)

        self.retranslateUi()

    # --- state / expansion -------------------------------------------------------
    def apply_state(self, state: dict):
        """Push a full engine state dict (which is also the worker result)."""
        self._state = dict(state)
        if self._state.get("status") in ("fail", "ok_no_ticks") and not self._expanded:
            self.set_expanded(True)  # auto-expand so the error/note is visible
        self.retranslateUi()

    def set_run_enabled(self, enabled: bool):
        self.run_btn.setEnabled(enabled)

    def set_expanded(self, expanded: bool):
        self._expanded = expanded
        self.body.setVisible(expanded)
        self.chevron_btn.setText("▾" if expanded else "▸")

    def _toggle_expanded(self):
        self.set_expanded(not self._expanded)

    def update_live(self, payload: dict):
        """Live stream tick forwarded by the page (worker.stream_tick)."""
        if self._state.get("status") != "running":
            return  # late tick after finish/abort — don't clobber the final state
        t = lambda k: self._translator.t(f"testtool.{k}")
        self.stream_labels["live_updates"].setText(
            f"{t('live_updates')}: {payload.get('ticks', 0)}"
        )
        lb = payload.get("last_bar")
        if lb:
            self.last_bar_label.setText(
                f"O={lb.get('open')} H={lb.get('high')} L={lb.get('low')} "
                f"C={lb.get('close')} V={lb.get('volume')}"
            )
        if self.spark is not None:
            self.spark.set_data(payload.get("closes"))

    # --- theming / i18n -------------------------------------------------------------
    def set_theme(self, name: str):
        self._theme = name
        if self.spark is not None:
            self.spark.set_theme(name)
        self.retranslateUi()

    def retranslateUi(self):
        t = lambda k: self._translator.t(f"testtool.{k}")
        pal = palette_current(self._theme)
        status = self._state.get("status", "pending")

        self.dim_label.setText(
            f"{self.spec.code} · {self.spec.ktype} · {self.spec.broker} · {self.spec.func}"
        )
        self.run_btn.setText(t("run"))

        color_key = self._PILL_COLOR_KEY.get(status, "muted")
        c = pal[color_key]
        self.status_pill.setText(t(f"status_{status}"))
        # outline pill — readable on both themes without a filled background
        self.status_pill.setStyleSheet(
            f"color: {c}; border: 1px solid {c}; background-color: transparent;"
            f"border-radius: 9px; padding: 2px 10px; font-weight: 600;"
        )

        if self.spec.func == "get_kline":
            cols = [t("col_time"), t("col_open"), t("col_high"), t("col_low"),
                    t("col_close"), t("col_volume")]
            self.table.setHorizontalHeaderLabels(cols)
            preview = self._state.get("preview") or []
            keys = ["time_key", "open", "high", "low", "close", "volume"]
            self.table.setRowCount(len(preview))
            for r, row in enumerate(preview):
                for cidx, key in enumerate(keys):
                    item = QTableWidgetItem(str(row.get(key, "")))
                    item.setFlags(Qt.ItemIsEnabled)  # display-only preview
                    self.table.setItem(r, cidx, item)

            parts = []
            if "elapsed_ms" in self._state:
                parts.append(f"{t('elapsed_ms')}: {self._state['elapsed_ms']:.0f}")
            if self._state.get("rows") is not None:
                parts.append(f"{t('rows')}: {self._state['rows']}")
            lb = self._state.get("last_bar")
            if lb:
                parts.append(f"{t('last_bar')}: C={lb.get('close')} V={lb.get('volume')}")
            self.meta_label.setText("   ".join(parts))
        else:
            s = self._state
            base_txt = "✓" if s.get("baseline") else "✗"
            self.stream_labels["baseline"].setText(f"{t('baseline')}: {base_txt}")
            self.stream_labels["live_updates"].setText(
                f"{t('live_updates')}: {s.get('ticks', 0)}"
            )
            self.stream_labels["first_update"].setText(
                f"{t('first_update')}: {s.get('first_ts') or '—'}"
            )
            self.stream_labels["last_update"].setText(
                f"{t('last_update')}: {s.get('last_ts') or '—'}"
            )
            lb = s.get("last_bar")
            if lb:
                self.last_bar_label.setText(
                    f"O={lb.get('open')} H={lb.get('high')} L={lb.get('low')} "
                    f"C={lb.get('close')} V={lb.get('volume')}"
                )
            else:
                self.last_bar_label.setText(t("no_data"))

        # note / error line — translated key when the worker tagged one, raw text otherwise
        err = self._state.get("error")
        if not err and self._state.get("error_key"):
            err = t(self._state["error_key"])
        if status == "ok_no_ticks":
            # informational, not an error: baseline arrived but the window saw no ticks
            self.error_label.setStyleSheet(f"color: {pal['warning']}; font-weight: 600;")
            self.error_label.setText(t("ok_no_ticks_note"))
        elif err and status in ("fail", "aborted"):
            self.error_label.setStyleSheet(f"color: {pal['danger']}; font-weight: 600;")
            self.error_label.setText(f"{t('error')}: {err}")
        else:
            self.error_label.setStyleSheet("")
            self.error_label.setText("")

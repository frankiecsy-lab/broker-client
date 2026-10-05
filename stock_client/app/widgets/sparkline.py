"""SparkLine — dependency-free close-price polyline drawn with QPainter.

pyqtgraph is not installed, so live stream charts are hand-drawn: a fixed-height
widget that renders the pushed list of closes as an accent-colored line plus a
dot on the latest point. Theme-aware via the shared palette; data is pushed with
set_data() — the widget never polls or owns anything.
"""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..theme.palette import current as palette_current


class SparkLine(QWidget):
    MAX_POINTS = 300  # cap so paint cost stays flat for long streams

    def __init__(self, theme_name: str, parent=None):
        super().__init__(parent)
        self._theme = theme_name
        self._data: list[float] = []
        self.setFixedHeight(48)
        self.setMinimumWidth(120)

    # --- data / theming ---------------------------------------------------------
    def set_data(self, values):
        vals = [float(v) for v in (values or []) if v is not None and math.isfinite(float(v))]
        if vals != self._data:
            self._data = vals[-self.MAX_POINTS:]
            self.update()

    def set_theme(self, name: str):
        self._theme = name
        self.update()

    # --- painting -----------------------------------------------------------------
    def paintEvent(self, _event):
        p = QPainter(self)
        pal = palette_current(self._theme)
        rect = QRectF(self.rect()).adjusted(3, 5, -3, -5)
        data = self._data
        if len(data) < 2:
            return  # nothing to draw yet — the row's labels carry the state

        lo, hi = min(data), max(data)
        span = (hi - lo) or 1.0  # flat line -> avoid div-by-zero
        n = len(data)
        step_x = rect.width() / (n - 1)
        pts = [
            QPointF(rect.left() + i * step_x,
                    rect.bottom() - (v - lo) / span * rect.height())
            for i, v in enumerate(data)
        ]

        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(pal["accent"]), 1.6))
        p.drawPolyline(pts)
        # dot on the latest close so "live" is visible at a glance
        last = pts[-1]
        p.setBrush(QColor(pal["accent"]))
        p.setPen(Qt.NoPen)
        p.drawEllipse(last, 2.5, 2.5)

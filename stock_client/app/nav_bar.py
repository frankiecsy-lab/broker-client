"""TopNavBar — horizontal checkable button rail built from PAGE_REGISTRY."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget

from .page_registry import PAGE_REGISTRY


class TopNavBar(QWidget):
    page_changed = Signal(str)  # emits the page key

    def __init__(self, translator, parent=None):
        super().__init__(parent)
        self.setObjectName("NavBar")
        self._translator = translator
        self._buttons: dict[str, QPushButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(4)

        for key in PAGE_REGISTRY:
            btn = QPushButton()
            btn.setProperty("class", "navButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._group.addButton(btn)
            btn.clicked.connect(lambda checked=False, k=key: self.page_changed.emit(k))
            layout.addWidget(btn)
            self._buttons[key] = btn

        layout.addStretch(1)
        self.retranslateUi()

    def retranslateUi(self):
        for key, entry in PAGE_REGISTRY.items():
            self._buttons[key].setText(self._translator.t(entry["title_i18n"]))

    def set_active(self, key: str):
        btn = self._buttons.get(key)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)

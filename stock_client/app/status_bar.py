"""AppStatusBar — bottom strip: IB/Futu connection dots + transient message + language/theme readout."""

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from .theme.palette import current as palette_current

LANGUAGE_NAMES = {"zh_TW": "繁體中文", "zh_CN": "简体中文", "en_US": "English"}


class _DotLabel(QLabel):
    """Colored bullet + text; color is set per state so it follows the theme."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = "#9AA0A6"  # unknown gray until first render

    def set_state(self, color: str, text: str):
        self._color = color
        self.setText(f'<span style="color:{color};">&#9679;</span> {text}')


class AppStatusBar(QWidget):
    """Custom status strip (not QStatusBar) for a clean multi-indicator layout."""

    def __init__(self, translator, parent=None):
        super().__init__(parent)
        self._translator = translator
        self._theme_name = "dark"
        self._language = "zh_TW"
        # True / False / None(unknown) per broker — re-rendered on theme change
        self._broker_states = {"ib": None, "futu": None}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)

        self.ib_label = _DotLabel()
        self.futu_label = _DotLabel()
        self.message_label = QLabel()
        self.lang_label = QLabel()
        self.theme_label = QLabel()
        for w in (self.message_label, self.lang_label, self.theme_label):
            w.setProperty("class", "mutedLabel")

        layout.addWidget(self.ib_label)
        layout.addSpacing(16)
        layout.addWidget(self.futu_label)
        layout.addStretch(1)
        layout.addWidget(self.message_label)
        layout.addSpacing(16)
        layout.addWidget(self.lang_label)
        layout.addSpacing(12)
        layout.addWidget(self.theme_label)

    # --- state setters (safe to call from any slot; all GUI thread) ---
    def set_broker_state(self, broker: str, connected):
        if broker in self._broker_states:
            self._broker_states[broker] = connected
            self._render()

    def set_message(self, text: str):
        self.message_label.setText(text)

    def clear_message(self):
        self.message_label.setText("")

    def set_language(self, lang: str):
        self._language = lang
        self._render()

    def set_theme_name(self, name: str):
        self._theme_name = name
        self._render()

    # --- rendering ---
    def _render(self):
        pal = palette_current(self._theme_name)
        for broker, label in (("ib", self.ib_label), ("futu", self.futu_label)):
            state = self._broker_states[broker]
            if state is None:
                color, suffix = pal["muted"], "unknown"
            elif state:
                color, suffix = pal["success"], "connected"
            else:
                color, suffix = pal["danger"], "disconnected"
            label.set_state(color, self._translator.t(f"status.{broker}_{suffix}"))
        lang_name = LANGUAGE_NAMES.get(self._language, self._language)
        self.lang_label.setText(
            f"{self._translator.t('status.language')}: {lang_name}"
        )
        self.theme_label.setText(
            f"{self._translator.t('status.theme')}: {self._theme_name.capitalize()}"
        )

    def retranslateUi(self):
        self._render()

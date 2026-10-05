"""ThemeManager — applies dark/light QSS app-wide and tracks the active theme."""

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from .palette import THEMES

QSS_DIR = Path(__file__).resolve().parent / "qss"


class ThemeManager(QObject):
    theme_changed = Signal(str)

    def __init__(self, app: QApplication, settings, initial: str = "dark"):
        super().__init__()
        self._app = app
        self._settings = settings
        self.current_theme = None
        if initial not in THEMES:
            logging.warning("Unknown theme [%s], falling back to dark", initial)
            initial = "dark"
        self.apply(initial, persist=False)

    @property
    def current(self) -> str:
        return self.current_theme

    def apply(self, theme: str, persist: bool = True):
        """Load qss/<theme>.qss and set it on the whole app (instant re-polish)."""
        if theme not in THEMES:
            theme = "dark"
        with open(QSS_DIR / f"{theme}.qss", "r", encoding="utf-8") as f:
            self._app.setStyleSheet(f.read())
        changed = (self.current_theme != theme)
        self.current_theme = theme
        if persist:
            self._settings.theme = theme
        if changed:
            self.theme_changed.emit(theme)

    def toggle(self):
        self.apply("light" if self.current_theme == "dark" else "dark")

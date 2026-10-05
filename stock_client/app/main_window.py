"""MainWindow — menu bar + top nav rail + QStackedWidget pages + status strip.

Owns the central retranslate walk (language switch) and the shutdown order
that releases the shared IB connection cleanly (extended in M2).
"""

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from . import __version__
from .i18n import SUPPORTED_LANGUAGES
from .nav_bar import TopNavBar
from .page_registry import PAGE_REGISTRY, page_keys
from .status_bar import AppStatusBar, LANGUAGE_NAMES


class MainWindow(QMainWindow):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setObjectName("MainWindow")
        self.resize(1200, 800)

        # --- central: nav rail + stacked pages + status strip ---
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.nav_bar = TopNavBar(ctx.translator)
        self.stack = QStackedWidget()
        self.status_strip = AppStatusBar(ctx.translator)

        self._pages: dict[str, QWidget] = {}      # key -> built page (lazy cache)
        self._page_build_order: list[str] = []    # order pages were first opened

        root.addWidget(self.nav_bar)
        root.addWidget(self.stack, 1)
        root.addWidget(self.status_strip)
        self.setCentralWidget(central)

        # --- menu bar (texts filled in retranslateUi) ---
        mb = self.menuBar()

        self._file_menu = mb.addMenu("")
        self.act_check_conn = QAction("", self)
        self.act_check_conn.triggered.connect(self._on_check_connection)
        self.act_exit = QAction("", self)
        self.act_exit.triggered.connect(self.close)
        self._file_menu.addAction(self.act_check_conn)
        self._file_menu.addSeparator()
        self._file_menu.addAction(self.act_exit)

        self._view_menu = mb.addMenu("")
        self.act_theme_dark = QAction("", self, checkable=True)
        self.act_theme_light = QAction("", self, checkable=True)
        self.act_theme_dark.triggered.connect(lambda: ctx.theme_mgr.apply("dark"))
        self.act_theme_light.triggered.connect(lambda: ctx.theme_mgr.apply("light"))
        self._view_menu.addAction(self.act_theme_dark)
        self._view_menu.addAction(self.act_theme_light)

        self._lang_menu = mb.addMenu("")
        self._lang_actions: dict[str, QAction] = {}
        for lang in SUPPORTED_LANGUAGES:
            act = QAction(LANGUAGE_NAMES[lang], self, checkable=True)  # native name, not translated
            act.triggered.connect(lambda checked=False, l=lang: self._on_set_language(l))
            self._lang_menu.addAction(act)
            self._lang_actions[lang] = act

        self._help_menu = mb.addMenu("")
        self.act_about = QAction("", self)
        self.act_about.triggered.connect(self._on_about)
        self._help_menu.addAction(self.act_about)

        # --- wire signals ---
        ctx.translator.language_changed.connect(self._on_language_changed)
        ctx.theme_mgr.theme_changed.connect(self._on_theme_changed)
        self.nav_bar.page_changed.connect(self._on_page_changed)
        if ctx.worker is not None:  # async bridge (M2) — worker is born inside its thread
            ctx.worker.worker_ready.connect(self._on_worker_ready)

        # initial state
        self._sync_theme_actions()
        self._sync_lang_actions()
        if page_keys():
            self._on_page_changed(page_keys()[0])  # open the first registered page
        self.retranslateUi()

    # --- navigation ---
    def _on_page_changed(self, key: str):
        entry = PAGE_REGISTRY.get(key)
        if entry is None:
            return
        if key not in self._pages:
            page = entry["factory"](self.ctx)
            self._pages[key] = page
            self._page_build_order.append(key)
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(self._pages[key])

    # --- language ---
    def _on_set_language(self, lang: str):
        self.ctx.settings.language = lang
        self.ctx.translator.set_language(lang)  # emits language_changed

    def _on_language_changed(self, lang: str):
        for act in self._lang_actions.values():
            act.setChecked(False)
        if lang in self._lang_actions:
            self._lang_actions[lang].setChecked(True)
        self.status_strip.set_language(lang)
        self.retranslateUi()

    def _sync_lang_actions(self):
        cur = self.ctx.translator.language
        for lang, act in self._lang_actions.items():
            act.setChecked(lang == cur)

    # --- theme ---
    def _on_theme_changed(self, theme: str):
        self.status_strip.set_theme_name(theme)
        self._sync_theme_actions()

    def _sync_theme_actions(self):
        cur = self.ctx.theme_mgr.current
        if cur is not None:
            self.act_theme_dark.setChecked(cur == "dark")
            self.act_theme_light.setChecked(cur == "light")

    # --- async bridge (M2) ---
    def _on_worker_ready(self, worker):
        """AsyncWorker now exists on its own thread — route its signals to the strip."""
        worker.broker_status_changed.connect(self.status_strip.set_broker_state)
        worker.status_message.connect(self.status_strip.set_message)

    # --- actions ---
    def _on_check_connection(self):
        worker = self.ctx.worker
        if worker is None:  # bridge not started yet (pre-M2)
            return
        self.status_strip.set_message(self.ctx.t("status.checking_connection"))
        worker.probe_all()

    def _on_about(self):
        QMessageBox.about(
            self,
            self.ctx.t("menu.about"),
            f"Tomato Broker Server v{__version__}",
        )

    # --- central retranslate walk (deterministic: every widget exposes retranslateUi) ---
    def retranslateUi(self):
        t = self.ctx.t
        self.setWindowTitle(t("app.title"))
        self._file_menu.setTitle(t("menu.file"))
        self._view_menu.setTitle(t("menu.view"))
        self._lang_menu.setTitle(t("menu.language"))
        self._help_menu.setTitle(t("menu.help"))
        self.act_check_conn.setText(t("menu.check_connection"))
        self.act_exit.setText(t("menu.exit"))
        self.act_theme_dark.setText(t("menu.theme_dark"))
        self.act_theme_light.setText(t("menu.theme_light"))
        self.act_about.setText(t("menu.about"))
        self.nav_bar.retranslateUi()
        self.status_strip.retranslateUi()
        for key in self._page_build_order:
            page = self._pages[key]
            if hasattr(page, "retranslateUi"):
                page.retranslateUi()

    # --- shutdown (M2 extends: cancel tasks -> gather -> broker __aexit__ -> stop loop) ---
    def closeEvent(self, event):
        worker = self.ctx.worker
        if worker is not None:
            worker.request_shutdown()
            worker.wait(5000)  # block until the loop thread finishes cleanup
        super().closeEvent(event)

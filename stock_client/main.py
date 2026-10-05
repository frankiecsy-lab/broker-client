"""Tomato Broker Server — entry point.

High-DPI flags → QApplication → settings/translator/theme → AppContext
→ page registration → MainWindow. The async bridge (M2) is attached to the
context here once it exists; until then ctx.worker stays None and the UI
degrades gracefully.
"""

import sys
from pathlib import Path

# Make `app` importable no matter where python was launched from.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication  # noqa: E402


def main() -> int:
    # High-DPI scaling is on by default in Qt 6 (the AA_* attributes are deprecated no-ops).

    app = QApplication(sys.argv)
    app.setApplicationName("Tomato Broker Server")
    app.setOrganizationName("TomatoBrokerServer")

    from app.context import AppContext
    from app.i18n import Translator
    from app.settings import AppSettings
    from app.theme.manager import ThemeManager

    settings = AppSettings()
    translator = Translator(settings.language)
    translator.check_key_parity()  # warn once at startup if JSON key sets drift
    theme_mgr = ThemeManager(app, settings, initial=settings.theme)

    from app.async_bridge import LoopThread

    loop_thread = LoopThread(broker_dir=str(Path(settings.broker_dir)))

    ctx = AppContext(
        settings=settings,
        translator=translator,
        theme_mgr=theme_mgr,
        worker=loop_thread,  # GUI-thread facade; its AsyncWorker is born inside run()
        broker_dir=str(Path(settings.broker_dir)),
    )

    # --- page registration (future pages = one line each) ---
    from app.page_registry import register_page
    from app.testtool import TestToolPage

    register_page("test_tool", "nav.test_tool", lambda c: TestToolPage(c))

    loop_thread.start()  # worker thread boots in the background; ready within ms

    from app.main_window import MainWindow

    window = MainWindow(ctx)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

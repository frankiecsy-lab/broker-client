"""L0/L1 offscreen smoke test — app shell + i18n + theme + async bridge.

Run:  QT_QPA_PLATFORM=offscreen python _smoke_l0.py
Exits non-zero on any assertion failure; prints SMOKE_OK at the end.

Covers (works with brokers ON or OFF — probe results must match gateway reality):
  1. every i18n key resolves to a non-empty string in all 3 languages, parity clean
  2. live language switch updates window title + nav button + page title
  3. theme toggle re-polishes instantly and persists
  4. status bar renders broker dots (unknown state) without a worker
  5. async bridge: starts on its own thread, Check Connection -> dots match the
     gateway ports' real state (green when up / red when down, no crash either way)
  6. clean shutdown releases the worker thread (IB clientId 99 path)
"""

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def wait_for(cond, timeout_s: float = 15.0) -> bool:
    """Process events until cond() is true or the timeout fires (GUI thread)."""
    loop = QEventLoop()
    state = {"ok": False}

    def check():
        if cond():
            state["ok"] = True
            loop.quit()

    timer = QTimer()
    timer.setInterval(50)
    timer.timeout.connect(check)
    timer.start()
    QTimer.singleShot(int(timeout_s * 1000), loop.quit)
    check()
    loop.exec()
    return state["ok"]


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Tomato Broker Server")
    app.setOrganizationName("TomatoBrokerServer")

    from app.context import AppContext
    from app.i18n import SUPPORTED_LANGUAGES, Translator
    from app.i18n.translator import I18N_DIR
    from app.settings import AppSettings
    from app.theme.manager import ThemeManager

    settings = AppSettings()
    # pin the mock toggle off: section 5 asserts probes match gateway reality
    settings.tt_mock = False
    translator = Translator(settings.language)

    def load_lang(lang):
        with open(I18N_DIR / f"{lang}.json", "r", encoding="utf-8") as f:
            return json.load(f)

    for lang in SUPPORTED_LANGUAGES:
        data = load_lang(lang)
        assert isinstance(data, dict) and data, f"{lang} JSON empty"

    theme_mgr = ThemeManager(app, settings, initial=settings.theme)

    # --- bridge starts BEFORE MainWindow (same order as main.py) ---
    from app.async_bridge import LoopThread

    loop_thread = LoopThread(broker_dir=str(Path(settings.broker_dir)))
    ctx = AppContext(
        settings=settings,
        translator=translator,
        theme_mgr=theme_mgr,
        worker=loop_thread,
        broker_dir=str(Path(settings.broker_dir)),
    )

    from app.page_registry import register_page
    from app.testtool import TestToolPage

    register_page("test_tool", "nav.test_tool", lambda c: TestToolPage(c))

    from app.main_window import MainWindow

    window = MainWindow(ctx)
    loop_thread.start()  # worker thread boots in the background; ready within ms
    window.show()
    app.processEvents()

    # --- 1. every i18n key resolves to a non-empty string in all 3 languages ---
    def flatten(d, prefix=""):
        out = {}
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                out.update(flatten(v, key))
            else:
                out[key] = v
        return out

    base = flatten(load_lang("zh_TW"))
    for lang in SUPPORTED_LANGUAGES:
        data = flatten(load_lang(lang))
        missing = set(base) - set(data)
        extra = set(data) - set(base)
        assert not missing, f"{lang} missing keys: {sorted(missing)}"
        assert not extra, f"{lang} extra keys: {sorted(extra)}"
        for k, v in data.items():
            assert isinstance(v, str) and v.strip(), f"{lang}:{k} empty value"

    # --- 2. live language switch updates window title + nav button + page title ---
    seen = set()
    for lang in SUPPORTED_LANGUAGES:
        translator.set_language(lang)
        app.processEvents()
        title = window.windowTitle()
        assert title and not title.startswith("app."), f"untranslated title: {title!r}"
        nav_btn_text = window.nav_bar._buttons["test_tool"].text()
        assert nav_btn_text and "nav." not in nav_btn_text, f"untranslated nav: {nav_btn_text!r}"
        seen.add(nav_btn_text)
        page_title = window._pages["test_tool"].title_label.text()
        assert page_title and "testtool." not in page_title, (
            f"untranslated page title: {page_title!r}"
        )
    # app.title is the product name (identical in all 3 languages); nav text must differ
    assert len(seen) == 3, f"nav texts did not differ across languages: {seen}"

    # --- 3. theme toggle re-polishes instantly and persists ---
    before = app.styleSheet()
    theme_mgr.apply("light")
    app.processEvents()
    after_light = app.styleSheet()
    assert after_light != before, "applying light theme did not change stylesheet"
    theme_mgr.toggle()  # back to dark
    app.processEvents()
    assert app.styleSheet() == before, "toggle did not restore original theme"

    # --- 4. status bar renders broker dots without a worker (unknown state) ---
    window.status_strip.set_broker_state("ib", False)
    window.status_strip.set_broker_state("futu", True)
    app.processEvents()
    assert "color:" in window.status_strip.ib_label.text(), "dot html missing"

    # --- 5. async bridge (M2): ready -> Check Connection -> dots match reality ---
    import socket

    def port_open(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return True
        except OSError:
            return False

    expect_ib = port_open(4001)      # IB Gateway / TWS API port
    expect_futu = port_open(11111)   # Futu OpenD

    ok_ready = wait_for(lambda: loop_thread.worker is not None, timeout_s=10)
    assert ok_ready, "bridge did not become ready within 10s"
    worker = loop_thread.worker
    assert worker.thread() is not app.thread(), "worker must live on its own thread"

    # Spy on the actual probe emissions — section 4 left stale states in the strip,
    # so reading _broker_states alone can't tell "probe finished" from "old value".
    emitted: list[tuple[str, bool]] = []
    worker.broker_status_changed.connect(lambda b, c: emitted.append((b, c)))

    # the full menu path: File -> Check Connection
    window._on_check_connection()
    ok_states = wait_for(
        lambda: {b for b, _ in emitted} >= {"ib", "futu"},
        timeout_s=30,  # Futu SDK may take a few seconds to give up on OpenD
    )
    assert ok_states, f"probe did not complete within 30s (emitted: {emitted})"
    states = dict(emitted)  # last emission per broker wins
    print(f"gateway ports: ib={expect_ib} futu={expect_futu}; probe results: {states}")
    # probes must match reality in BOTH directions (green when up, red when down — no crash either way)
    assert states["ib"] is expect_ib, f"IB probe mismatch: expected {expect_ib}, got {states['ib']}"
    assert states["futu"] is expect_futu, f"Futu probe mismatch: expected {expect_futu}, got {states['futu']}"

    # --- 6. clean shutdown releases the thread (IB clientId 99 path) ---
    loop_thread.request_shutdown()
    ok_done = wait_for(lambda: not loop_thread.isRunning(), timeout_s=10)
    assert ok_done, "bridge thread did not stop after request_shutdown"

    print(f"nav texts per language: {sorted(seen)}")
    print(f"keys per language: {len(base)} (parity clean)")
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

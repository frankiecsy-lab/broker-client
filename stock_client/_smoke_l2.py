"""L2 offscreen smoke test — full pipeline through the REAL worker, offline.

Run:  QT_QPA_PLATFORM=offscreen python _smoke_l2.py
Exits non-zero on any assertion failure; prints SMOKE_L2_OK at the end.

Unlike M3 (fake bridge), this drives the real LoopThread + AsyncWorker with a
MockBrokerClient injected via the page's mock toggle, so every cross-thread
signal path is exercised without OpenD / IB Gateway:
  A. bridge boots on its own thread; broker SDKs stay unimported while mocked
  B. Test All over a 16-row matrix (2 codes x 2 ktypes x 2 brokers x 2 funcs):
     all pass, get_kline preview table + stream sparkline/tick counts correct
  C. ok_no_ticks path: mock with tick_count=0 -> amber note, auto-expanded row
  D. fail path: mock broker simulating a refused connection -> red error text
  E. clean shutdown releases the worker thread

The script pins (and restores) persisted settings so repeated runs are
deterministic and _smoke_l0's probe-reality assertions stay valid.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def wait_for(cond, timeout_s: float = 30.0) -> bool:
    """Process events until cond() is true or the timeout fires (GUI thread)."""
    loop = QEventLoop()
    state = {"ok": False}

    def check():
        if cond():
            state["ok"] = True
            loop.quit()

    timer = QTimer()
    timer.setInterval(25)
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

    from app.async_bridge import LoopThread
    from app.context import AppContext
    from app.i18n import Translator
    from app.settings import AppSettings
    from app.testtool.page import TestToolPage
    from app.theme.manager import ThemeManager

    settings = AppSettings()
    # pin persisted test-tool defaults so the matrix is deterministic run-to-run
    saved_mock = bool(settings.tt_mock)
    settings.tt_codes = ["US.AAPL", "HK.00700"]
    settings.tt_kline_num = 100
    settings.tt_duration = 30
    settings.tt_strict = True
    settings.tt_mock = False

    translator = Translator(settings.language)
    theme_mgr = ThemeManager(app, settings, initial=settings.theme)
    loop_thread = LoopThread(broker_dir=str(Path(settings.broker_dir)))
    ctx = AppContext(
        settings=settings, translator=translator, theme_mgr=theme_mgr,
        worker=loop_thread, broker_dir=str(Path(settings.broker_dir)),
    )

    page = TestToolPage(ctx)
    page.show()
    app.processEvents()
    loop_thread.start()  # real worker thread; ready within ms

    # --- A. bridge boots on its own thread -------------------------------------
    ok_ready = wait_for(lambda: page._bridge_ready, timeout_s=15)
    assert ok_ready, "bridge did not report ready"
    worker = loop_thread.worker
    assert worker is not None and worker.thread() is not app.thread(), (
        "worker must live on its own thread"
    )

    # shrink the matrix: keep K_1M + K_DAY only -> 2x2x2x2 = 16 rows
    for k in ("K_5M", "K_15M", "K_60M", "K_WEEK"):
        page._ktype_checks[k].setChecked(False)
    app.processEvents()
    assert len(page._rows) == 16, f"expected 16 rows after filter, got {len(page._rows)}"

    # enable the mock through the real UI toggle (persists + pushes to the bridge)
    page.mock_chk.setChecked(True)
    app.processEvents()
    assert settings.tt_mock is True, "mock toggle must persist"
    assert loop_thread.worker._mock_client is not None, "use_mock did not reach the worker"

    # --- B. Test All over 16 rows -------------------------------------------------
    summaries = []
    page.engine.batch_finished.connect(lambda s: summaries.append(s))
    page.btn_test_all.click()
    ok_done = wait_for(lambda: len(summaries) == 1, timeout_s=90)
    assert ok_done, "batch did not finish within 90s"
    summary = summaries[0]
    assert summary["pass"] == 16 and summary["fail"] == 0, (
        f"expected 16/16 pass with the mock, got {summary}"
    )

    # get_kline row: full preview table + meta line
    gk = next(r for r in page._rows.values() if r.spec.func == "get_kline")
    st_gk = page.engine.state_for(gk.spec.test_id)
    assert st_gk["status"] == "pass" and st_gk["rows"] == 100, st_gk
    gk.set_expanded(True)
    app.processEvents()
    assert gk.table.rowCount() == 20, f"preview should cap at 20 rows: {gk.table.rowCount()}"
    first_cell = gk.table.item(0, 0).text()
    assert "2026-10" in first_cell, f"time_key not normalized to a date string: {first_cell!r}"

    # stream row: baseline + 5 ticks drove the sparkline and counters
    sk = next(r for r in page._rows.values() if r.spec.func == "stream_kline")
    st_sk = page.engine.state_for(sk.spec.test_id)
    assert st_sk["status"] == "pass" and st_sk["baseline"] is True, st_sk
    assert st_sk["ticks"] == 5, f"mock tick_count=5 not honored: {st_sk['ticks']}"
    assert st_sk.get("first_ts") and st_sk.get("last_ts"), (
        f"stream timestamps missing: {st_sk}"
    )
    sk.set_expanded(True)
    app.processEvents()
    assert sk.spark is not None and len(sk.spark._data) >= 100, (
        "sparkline should hold the baseline closes (+ticks)"
    )

    # zero broker contact: the whole batch ran offline
    banned = [m for m in ("futu", "ib_async", "broker") if m in sys.modules]
    assert not banned, f"broker modules imported during mocked run: {banned}"

    # --- C. ok_no_ticks path (mock with 0 ticks) -----------------------------------
    loop_thread.use_mock(True, tick_count=0)
    sk2 = next(
        r for r in page._rows.values()
        if r.spec.func == "stream_kline" and r.spec.test_id != sk.spec.test_id
    )
    tid2 = sk2.spec.test_id
    sk2.run_btn.click()
    ok_c = wait_for(lambda: page.engine.state_for(tid2)["status"] in ("ok_no_ticks", "fail"),
                    timeout_s=30)
    assert ok_c, f"zero-tick stream did not finish: {page.engine.state_for(tid2)}"
    st_c = page.engine.state_for(tid2)
    assert st_c["status"] == "ok_no_ticks", (
        f"strict ON + 0 ticks must be ok_no_ticks, got {st_c}"
    )
    note = translator.t("testtool.ok_no_ticks_note")
    assert sk2.error_label.text() == note, (
        f"note missing on auto-expanded row: {sk2.error_label.text()!r}"
    )
    assert sk2.body.isVisible(), "ok_no_ticks rows must auto-expand to show the note"

    # --- D. fail path (mock simulates a refused connection) -------------------------
    loop_thread.use_mock(True, tick_count=5)
    worker._mock_client.fail_brokers.add("ib")  # smoke-only reach-in; UI has no such knob
    gk2 = next(
        r for r in page._rows.values()
        if r.spec.func == "get_kline" and r.spec.broker == "ib"
        and r.spec.test_id != gk.spec.test_id
    )
    tid3 = gk2.spec.test_id
    gk2.run_btn.click()
    ok_d = wait_for(lambda: page.engine.state_for(tid3)["status"] in ("fail", "pass"),
                    timeout_s=30)
    assert ok_d, f"refused-connection run did not finish: {page.engine.state_for(tid3)}"
    st_d = page.engine.state_for(tid3)
    assert st_d["status"] == "fail", f"ib get_kline must fail with a refused mock: {st_d}"
    assert "mock connection refused" in (st_d.get("error") or ""), st_d
    assert gk2.error_label.text(), "fail row must show the error text"

    # --- E. clean shutdown -----------------------------------------------------------
    loop_thread.request_shutdown()
    ok_stop = wait_for(lambda: not loop_thread.isRunning(), timeout_s=15)
    assert ok_stop, "bridge thread did not stop after request_shutdown"

    settings.tt_mock = saved_mock  # restore so other runs start from the user's choice
    print(f"summary: {summary}")
    print("SMOKE_L2_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
